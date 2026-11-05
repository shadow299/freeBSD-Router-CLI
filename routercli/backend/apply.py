"""Transactional commit engine.

commit = validate model -> render files -> syntax-check rendered files with
the real tools (pfctl -n, dnsmasq --test, sh -n, vtysh -C) -> back up current
files -> atomically write -> apply live changes as a minimal diff (ifconfig /
route / pfctl / service) -> record running-config + archive.

If any live step fails, the previous files are restored and the inverse diff is
applied on a best-effort basis, so the box returns to the last good state.
"""

import fcntl
import ipaddress
import json
import os
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Optional

from routercli.backend import paths
from routercli.backend import render_dnsmasq, render_frr, render_misc, render_pf, render_rcconf
from routercli.backend.system import CommandError, Runner, read_file, write_atomic, write_temp
from routercli.config.model import Interface, RouterConfig


class ApplyError(RuntimeError):
    pass


@dataclass
class Step:
    desc: str
    argv: list
    tolerate: bool = False
    fallback: Optional[list] = None


@dataclass
class Report:
    warnings: list = field(default_factory=list)
    changed_files: list = field(default_factory=list)
    executed: list = field(default_factory=list)
    archive_id: Optional[int] = None


# ---------------------------------------------------------------------------
# State files
# ---------------------------------------------------------------------------

@contextmanager
def state_lock():
    os.makedirs(paths.STATE_DIR, exist_ok=True)
    fd = os.open(paths.LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def load_json(path: str) -> Optional[dict]:
    txt = read_file(path)
    return json.loads(txt) if txt else None


def load_config(path: str) -> Optional[RouterConfig]:
    d = load_json(path)
    return RouterConfig.from_dict(d) if d is not None else None


def save_config(path: str, cfg: RouterConfig) -> None:
    write_atomic(path, json.dumps(cfg.to_dict(), indent=2, sort_keys=True) + "\n", 0o640)


def archive(cfg: RouterConfig, user: str, comment: str) -> int:
    os.makedirs(paths.ARCHIVE_DIR, exist_ok=True)
    ids = list_archive_ids()
    new_id = (ids[-1] + 1) if ids else 1
    rec = {"id": new_id, "time": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "user": user,
           "comment": comment, "config": cfg.to_dict()}
    write_atomic(os.path.join(paths.ARCHIVE_DIR, f"{new_id:06d}.json"), json.dumps(rec, indent=1) + "\n", 0o640)
    for old in ids[:max(0, len(ids) + 1 - paths.ARCHIVE_MAX)]:
        os.unlink(os.path.join(paths.ARCHIVE_DIR, f"{old:06d}.json"))
    return new_id


def list_archive_ids() -> list:
    try:
        return sorted(int(f[:-5]) for f in os.listdir(paths.ARCHIVE_DIR) if f.endswith(".json") and f[:-5].isdigit())
    except FileNotFoundError:
        return []


# ---------------------------------------------------------------------------
# Rendering + validation
# ---------------------------------------------------------------------------

def render_all(cfg: RouterConfig) -> dict:
    files = {
        paths.RC_CONF: render_rcconf.render(cfg, read_file(paths.RC_CONF)),
        paths.PF_CONF: render_pf.render(cfg),
        paths.DNSMASQ_CONF: render_dnsmasq.render(cfg),
    }
    if render_frr.enabled(cfg):
        files[paths.FRR_CONF] = render_frr.render(cfg)
    if cfg.ntp_servers:
        files[paths.NTP_CONF] = render_misc.render_ntp(cfg)
    if cfg.name_servers:
        files[paths.RESOLV_CONF] = render_misc.render_resolv(cfg)
    return files


VALIDATORS = {
    paths.RC_CONF: lambda f: ["/bin/sh", "-n", f],
    paths.PF_CONF: lambda f: ["pfctl", "-n", "-f", f],
    paths.DNSMASQ_CONF: lambda f: ["dnsmasq", "--test", "-C", f],
    paths.FRR_CONF: lambda f: ["vtysh", "-C", "-f", f],
}


def validate_files(runner: Runner, files: dict) -> None:
    for path, content in files.items():
        mk = VALIDATORS.get(path)
        if not mk:
            continue
        tmp = write_temp(content, os.path.basename(path))
        try:
            res = runner.run(mk(tmp), mutating=False, check=False, timeout=60)
            if res.rc != 0:
                raise ApplyError(f"validation of {path} failed:\n{res.output.replace(tmp, path).strip()}")
        finally:
            os.unlink(tmp)


def _norm(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.startswith(("# Generated", "! Generated")))


def backup(files) -> str:
    dest = os.path.join(paths.BACKUP_DIR, time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(dest, exist_ok=True)
    manifest = {}
    for n, path in enumerate(files):
        if os.path.exists(path):
            shutil.copy2(path, os.path.join(dest, str(n)))
            manifest[path] = str(n)
        else:
            manifest[path] = None
    with open(os.path.join(dest, "manifest.json"), "w") as fh:
        json.dump(manifest, fh)
    backups = sorted(os.listdir(paths.BACKUP_DIR))
    for old in backups[:-10]:
        shutil.rmtree(os.path.join(paths.BACKUP_DIR, old), ignore_errors=True)
    return dest


def restore(backup_dir: str) -> None:
    with open(os.path.join(backup_dir, "manifest.json")) as fh:
        manifest = json.load(fh)
    for path, name in manifest.items():
        if name is None:
            if os.path.exists(path):
                os.unlink(path)
        else:
            write_atomic(path, read_file(os.path.join(backup_dir, name)))


# ---------------------------------------------------------------------------
# Live diff -> commands
# ---------------------------------------------------------------------------

def _route_argv(action: str, r) -> list:
    argv = ["route", "-n", action]
    if r.version == 6:
        argv.append("-inet6")
    if r.is_default:
        return argv + ["default"] + ([r.nexthop] if action != "delete" else [])
    argv += ["-net", r.prefix]
    if action == "delete":
        return argv
    if r.nexthop:
        return argv + [r.nexthop]
    if r.interface:
        return argv + ["-interface", r.interface]
    return argv + ["127.0.0.1" if r.version == 4 else "::1", "-blackhole"]


def _iface_steps(old: RouterConfig, new: RouterConfig) -> list:
    steps = []
    for name in sorted(set(old.interfaces) - set(new.interfaces)):
        o = old.interfaces[name]
        if o.is_vlan or o.is_cloned:
            steps.append(Step(f"destroy {name}", ["ifconfig", name, "destroy"], tolerate=True))
            continue
        if o.dhcp_client:
            steps.append(Step(f"stop dhclient on {name}", ["service", "dhclient", "stop", name], tolerate=True))
        for a in o.ipv4:
            steps.append(Step(f"remove {a} from {name}",
                              ["ifconfig", name, "inet", str(ipaddress.IPv4Interface(a).ip), "-alias"], tolerate=True))
        for a in o.ipv6:
            steps.append(Step(f"remove {a} from {name}",
                              ["ifconfig", name, "inet6", str(ipaddress.IPv6Interface(a).ip), "-alias"], tolerate=True))
        if o.description:
            steps.append(Step(f"clear description {name}", ["ifconfig", name, "-description"], tolerate=True))

    # Create new cloned/VLAN interfaces (parents first).
    for itf in new.sorted_interfaces():
        if itf.name in old.interfaces:
            continue
        if itf.is_vlan:
            steps.append(Step(f"bring up parent {itf.parent}", ["ifconfig", itf.parent, "up"]))
            steps.append(Step(f"create VLAN {itf.name}",
                              ["ifconfig", itf.name, "create", "vlan", str(itf.vlan_id), "vlandev", itf.parent]))
        elif itf.is_cloned:
            steps.append(Step(f"create {itf.name}", ["ifconfig", itf.name, "create"]))

    for itf in new.sorted_interfaces():
        o = old.interfaces.get(itf.name) or Interface(name=itf.name)
        n = itf.name
        if o.dhcp_client and not itf.dhcp_client:
            steps.append(Step(f"stop dhclient on {n}", ["service", "dhclient", "stop", n], tolerate=True))
        for a in o.ipv4:
            if a not in itf.ipv4:
                steps.append(Step(f"remove {a} from {n}",
                                  ["ifconfig", n, "inet", str(ipaddress.IPv4Interface(a).ip), "-alias"], tolerate=True))
        for a in o.ipv6:
            if a not in itf.ipv6:
                steps.append(Step(f"remove {a} from {n}",
                                  ["ifconfig", n, "inet6", str(ipaddress.IPv6Interface(a).ip), "-alias"], tolerate=True))
        if itf.offload != o.offload:
            flags = render_rcconf.OFFLOAD_ON if itf.offload else render_rcconf.OFFLOAD_OFF
            steps.append(Step(f"{'enable' if itf.offload else 'disable'} hardware offload on {n}",
                              ["ifconfig", n, *flags], tolerate=True))
        if itf.mtu and itf.mtu != o.mtu:
            steps.append(Step(f"set mtu {itf.mtu} on {n}", ["ifconfig", n, "mtu", str(itf.mtu)]))
        if itf.description != o.description:
            steps.append(Step(f"set description on {n}",
                              ["ifconfig", n, "description", itf.description] if itf.description
                              else ["ifconfig", n, "-description"], tolerate=True))
        for a in itf.ipv4:
            if a not in o.ipv4:
                steps.append(Step(f"add {a} to {n}", ["ifconfig", n, "inet", a, "alias"], tolerate=True))
        for a in itf.ipv6:
            if a not in o.ipv6:
                i6 = ipaddress.IPv6Interface(a)
                steps.append(Step(f"add {a} to {n}", ["ifconfig", n, "inet6", str(i6.ip), "prefixlen",
                                                      str(i6.network.prefixlen), "alias"], tolerate=True))
        if itf.shutdown != o.shutdown or itf.name not in old.interfaces:
            steps.append(Step(f"{'shutdown' if itf.shutdown else 'no shutdown'} {n}",
                              ["ifconfig", n, "down" if itf.shutdown else "up"]))
        if itf.dhcp_client and not o.dhcp_client:
            steps.append(Step(f"start dhclient on {n}", ["service", "dhclient", "start", n], tolerate=True))
    return steps


def live_steps(old: RouterConfig, new: RouterConfig, changed: set) -> list:
    steps = []
    old_host = f"{old.hostname}.{old.domain_name}" if old.domain_name else old.hostname
    new_host = f"{new.hostname}.{new.domain_name}" if new.domain_name else new.hostname
    if old_host != new_host:
        steps.append(Step("set hostname", ["hostname", new_host]))
    if old.ip_routing != new.ip_routing:
        steps.append(Step("ip routing", ["sysctl", f"net.inet.ip.forwarding={int(new.ip_routing)}"]))
    if old.ipv6_routing != new.ipv6_routing:
        steps.append(Step("ipv6 routing", ["sysctl", f"net.inet6.ip6.forwarding={int(new.ipv6_routing)}"]))

    steps += _iface_steps(old, new)

    old_routes = {r.key(): r for r in old.routes}
    new_routes = {r.key(): r for r in new.routes}
    for k in old_routes.keys() - new_routes.keys():
        steps.append(Step(f"delete route {old_routes[k].prefix}", _route_argv("delete", old_routes[k]), tolerate=True))
    for k in new_routes.keys() - old_routes.keys():
        r = new_routes[k]
        steps.append(Step(f"add route {r.prefix}", _route_argv("add", r), fallback=_route_argv("change", r)))

    if paths.PF_CONF in changed:
        steps.append(Step("load pf ruleset", ["pfctl", "-f", paths.PF_CONF]))
        steps.append(Step("enable pf", ["pfctl", "-e"], tolerate=True))
        steps.append(Step("start pflog", ["service", "pflog", "start"], tolerate=True))

    old_dm = old.dns_server or bool(old.dhcp_pools)
    new_dm = new.dns_server or bool(new.dhcp_pools)
    if new_dm and (paths.DNSMASQ_CONF in changed or not old_dm):
        steps.append(Step("restart dnsmasq", ["service", "dnsmasq", "restart"]))
    elif old_dm and not new_dm:
        steps.append(Step("stop dnsmasq", ["service", "dnsmasq", "onestop"], tolerate=True))

    if new.ntp_servers and paths.NTP_CONF in changed:
        steps.append(Step("restart ntpd", ["service", "ntpd", "restart"]))
    elif old.ntp_servers and not new.ntp_servers:
        steps.append(Step("stop ntpd", ["service", "ntpd", "onestop"], tolerate=True))

    if render_frr.enabled(new) and (paths.FRR_CONF in changed or
                                    render_rcconf.frr_daemons(old) != render_rcconf.frr_daemons(new)):
        steps.append(Step("restart FRR", ["service", "frr", "restart"]))
    elif render_frr.enabled(old) and not render_frr.enabled(new):
        steps.append(Step("stop FRR", ["service", "frr", "onestop"], tolerate=True))
    return steps


def run_steps(runner: Runner, steps: list, best_effort: bool = False) -> None:
    for st in steps:
        try:
            runner.run(st.argv)
        except CommandError as exc:
            if st.fallback:
                try:
                    runner.run(st.fallback)
                    continue
                except CommandError:
                    pass
            if st.tolerate or best_effort:
                continue
            raise ApplyError(f"step '{st.desc}' failed: {exc}")


# ---------------------------------------------------------------------------
# Public entry points (called by the privileged helper)
# ---------------------------------------------------------------------------

def commit(new: RouterConfig, user: str, comment: str = "", runner: Optional[Runner] = None,
           full: bool = False) -> Report:
    runner = runner or Runner()
    rep = Report()
    rep.warnings = new.validate()
    old = load_config(paths.RUNNING) or RouterConfig()
    if full:
        old = RouterConfig()

    files = render_all(new)
    validate_files(runner, files)
    changed = {p for p, c in files.items() if _norm(read_file(p)) != _norm(c)}
    rep.changed_files = sorted(changed)
    bdir = backup(files.keys())
    for path in sorted(changed):
        write_atomic(path, files[path])

    steps = live_steps(old, new, changed)
    try:
        run_steps(runner, steps)
    except ApplyError:
        restore(bdir)
        run_steps(runner, live_steps(new, old, set(files)), best_effort=True)
        raise
    save_config(paths.RUNNING, new)
    rep.archive_id = archive(new, user, comment)
    rep.executed = runner.log
    return rep
