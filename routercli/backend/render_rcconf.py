"""Render the router-cli managed block of /etc/rc.conf.

Only a delimited block at the *end* of rc.conf is owned by router-cli, so later
assignments override anything bsdinstall or the administrator wrote earlier,
while unrelated settings (sshd_enable, zfs_enable, ...) are preserved.
"""

import ipaddress
import re

from routercli.config.model import RouterConfig

BEGIN = "# >>> router-cli managed block >>> DO NOT EDIT - use router-cli (configure terminal / commit)"
END = "# <<< router-cli managed block <<<"
_UNSAFE = re.compile(r'["$`\\\n]')


def rcvar(ifname: str) -> str:
    return ifname.replace(".", "_")


def _q(value: str) -> str:
    if _UNSAFE.search(value):
        raise ValueError(f"refusing to render unsafe rc.conf value {value!r}")
    return f'"{value}"'


# 'no hardware-offload': work around NIC checksum/segmentation offload bugs
# (e.g. Realtek RTL8125 with realtek-re-kmod, see its pkg-message).
OFFLOAD_OFF = ["-rxcsum", "-txcsum", "-rxcsum6", "-txcsum6", "-tso", "-lro"]
OFFLOAD_ON = ["rxcsum", "txcsum", "rxcsum6", "txcsum6", "tso"]


def interface_vars(cfg: RouterConfig) -> dict:
    """Per-interface rc.conf variables (also used by the live-apply differ)."""
    out = {}
    parents = {}
    for itf in cfg.sorted_interfaces():
        if itf.is_vlan:
            parents.setdefault(itf.parent, []).append(itf.vlan_id)

    # dialerN interfaces are created and configured by ppp(8) (see render_ppp).
    names = [i.name for i in cfg.sorted_interfaces() if not i.is_dialer]
    for p in parents:
        if p not in cfg.interfaces:
            names.append(p)

    for name in names:
        itf = cfg.interfaces.get(name)
        v = {}
        rv = rcvar(name)
        parts = []
        if itf is None:                              # bare VLAN parent
            parts = ["up"]
        else:
            if itf.dhcp_client:
                parts.append("DHCP")
            elif itf.ipv4:
                parts.append(f"inet {itf.ipv4[0]}")
            if itf.mtu:
                parts.append(f"mtu {itf.mtu}")
            if not itf.offload:
                parts.extend(OFFLOAD_OFF)
            parts.append("down" if itf.shutdown else "up")
            aliases = [f"inet {a}" for a in itf.ipv4[1:]]
            if itf.ipv6:
                first = ipaddress.IPv6Interface(itf.ipv6[0])
                v[f"ifconfig_{rv}_ipv6"] = f"inet6 {first.ip} prefixlen {first.network.prefixlen}"
                for a in itf.ipv6[1:]:
                    a6 = ipaddress.IPv6Interface(a)
                    aliases.append(f"inet6 {a6.ip} prefixlen {a6.network.prefixlen}")
            for n, a in enumerate(aliases):
                v[f"ifconfig_{rv}_alias{n}"] = a
            if itf.description:
                v[f"ifconfig_{rv}_descr"] = itf.description
        v[f"ifconfig_{rv}"] = " ".join(parts)
        if name in parents:
            v[f"vlans_{rv}"] = " ".join(str(t) for t in sorted(parents[name]))
        out[name] = v
    return out


def render_vars(cfg: RouterConfig) -> list:
    vs = []
    host = f"{cfg.hostname}.{cfg.domain_name}" if cfg.domain_name else cfg.hostname
    vs.append(("hostname", host))
    vs.append(("gateway_enable", "YES" if cfg.ip_routing else "NO"))
    vs.append(("ipv6_gateway_enable", "YES" if cfg.ipv6_routing else "NO"))

    cloned = [i.name for i in cfg.sorted_interfaces() if i.is_cloned]
    if cloned:
        vs.append(("cloned_interfaces", " ".join(cloned)))
    for _name, ivars in interface_vars(cfg).items():
        vs.extend(sorted(ivars.items(), key=lambda kv: (not kv[0].startswith("vlans_"), kv[0])))

    v4, v6 = [], []
    for r in cfg.routes:
        if r.via_dialer:                 # installed by ppp(8): 'add! ... HISADDR'
            continue
        if r.is_default:
            vs.append(("defaultrouter" if r.version == 4 else "ipv6_defaultrouter", r.nexthop))
            continue
        if r.nexthop:
            spec = f"-net {r.prefix} {r.nexthop}"
        elif r.interface:
            spec = f"-net {r.prefix} -interface {r.interface}"
        else:
            spec = f"-net {r.prefix} {'127.0.0.1' if r.version == 4 else '::1'} -blackhole"
        (v4 if r.version == 4 else v6).append(spec)
    if v4:
        vs.append(("static_routes", " ".join(f"rcli{n}" for n in range(len(v4)))))
        vs.extend((f"route_rcli{n}", s) for n, s in enumerate(v4))
    if v6:
        vs.append(("ipv6_static_routes", " ".join(f"rcli6_{n}" for n in range(len(v6)))))
        vs.extend((f"ipv6_route_rcli6_{n}", s) for n, s in enumerate(v6))

    vs += ppp_vars(cfg)
    vs += [("pf_enable", "YES"), ("pf_rules", "/etc/pf.conf"), ("pflog_enable", "YES")]
    dnsmasq = cfg.dns_server or bool(cfg.dhcp_pools)
    vs += [("dnsmasq_enable", "YES" if dnsmasq else "NO"), ("dnsmasq_conf", "/usr/local/etc/dnsmasq.conf")]
    vs += [("ntpd_enable", "YES" if cfg.ntp_servers else "NO")]
    if cfg.ntp_servers:
        vs.append(("ntpd_sync_on_start", "YES"))
    daemons = frr_daemons(cfg)
    vs.append(("frr_enable", "YES" if daemons else "NO"))
    if daemons:
        vs += [("frr_daemons", " ".join(daemons)), ("frr_vtysh_boot", "YES")]
    vs.append(("routercli_enable", "YES"))
    return vs


def ppp_vars(cfg: RouterConfig) -> list:
    """rc.d/ppp: one -ddial profile per active dialer, tun unit = dialer number."""
    if not cfg.dialers():
        return []
    active = cfg.active_dialers()
    if not active:
        return [("ppp_enable", "NO")]
    vs = [("ppp_enable", "YES"), ("ppp_mode", "ddial"), ("ppp_nat", "NO"),
          ("ppp_profile", " ".join(d.name for d in active))]
    vs += [(f"ppp_{d.name}_unit", str(d.dialer_unit)) for d in active]
    return vs


def frr_daemons(cfg: RouterConfig) -> list:
    d = []
    if cfg.ospf:
        d.append("ospfd")
    if cfg.rip:
        d.append("ripd")
    if cfg.bgp:
        d.append("bgpd")
    return ["zebra", "staticd"] + d if d else []


def render_block(cfg: RouterConfig) -> str:
    lines = [BEGIN]
    lines += [f"{k}={_q(v)}" for k, v in render_vars(cfg)]
    lines.append(END)
    return "\n".join(lines) + "\n"


def strip_block(text: str) -> str:
    out, inside = [], False
    for line in text.splitlines():
        if line.startswith(BEGIN[:30]):
            inside = True
            continue
        if line.startswith(END[:30]):
            inside = False
            continue
        if not inside:
            out.append(line)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out) + ("\n" if out else "")


def render(cfg: RouterConfig, existing: str) -> str:
    base = strip_block(existing)
    return base + ("\n" if base else "") + render_block(cfg)
