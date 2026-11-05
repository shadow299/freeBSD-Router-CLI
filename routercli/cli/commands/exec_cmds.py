"""User EXEC and privileged EXEC commands (show, ping, write, copy, commit, rollback, clear, reload)."""

import ipaddress
import json
import os
import platform
import re
import sys
import time

from routercli import __version__
from routercli.backend import paths
from routercli.backend.render_dnsmasq import pool_ranges
from routercli.cli import modes as M
from routercli.cli.commands import registry as R
from routercli.config import showrun

EX = list(M.EXEC_MODES)
PR = [M.PRIV]
COMMIT_MODES = [M.PRIV] + list(M.CONFIG_MODES)


# ---------------------------------------------------------------------------
# Session control
# ---------------------------------------------------------------------------

@R.command("enable", [M.EXEC], help="Turn on privileged commands")
def enable(s, a, neg):
    s.do_enable()


@R.command("disable", PR, help="Turn off privileged commands")
def disable(s, a, neg):
    s.to_mode(M.EXEC)


@R.command("( exit | logout | quit )", EX, help="Exit from the EXEC")
def logout(s, a, neg):
    s.quit = True


@R.command("configure [terminal]", PR, help="Enter configuration mode")
def configure(s, a, neg):
    s.refresh()
    s.candidate = s.running.copy()
    s.push(M.CONFIG)
    s.print("Enter configuration commands, one per line.  End with CNTL/Z.",
            "Changes are staged in a candidate configuration; use 'commit' to apply.")


@R.command("terminal length <lines:int(0-512)>", EX, help="Set number of lines on a screen")
def term_length(s, a, neg):
    s.term_length = a["lines"]


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

@R.command("ping <host:host> [repeat <count:int(1-100000)>] [size <size:int(36-18024)>] [source <src:ip>]",
           EX, help="Send echo messages")
def ping(s, a, neg):
    host = a["host"]
    v6 = ":" in host
    argv = ["ping"] + (["-6"] if v6 else []) + ["-c", str(a.get("count", 5))]
    if "size" in a:
        argv += ["-s", str(a["size"] - (48 if v6 else 28))]
    if "src" in a:
        argv += ["-S", a["src"]]
    argv.append(host)
    s.run_interactive(argv)


@R.command("traceroute <host:host>", EX, help="Trace route to destination")
def traceroute(s, a, neg):
    host = a["host"]
    s.run_interactive(["traceroute6" if ":" in host else "traceroute", "-w", "2", host])


# ---------------------------------------------------------------------------
# show (both EXEC levels)
# ---------------------------------------------------------------------------

@R.command("show version", EX, help="System hardware and software status")
def show_version(s, a, neg):
    uname = platform.uname()
    s.print(f"router-cli {__version__}, Cisco IOS-style shell for FreeBSD",
            f"Kernel: {uname.system} {uname.release} {uname.version}",
            f"Machine: {uname.machine}, hostname {s.running.hostname}",
            f"Python {platform.python_version()}",
            s.run_local(["uptime"]).strip())


@R.command("show clock", EX, help="Display the system clock")
def show_clock(s, a, neg):
    s.print(time.strftime("*%H:%M:%S.000 %Z %a %b %d %Y"))


@R.command("show privilege", EX, help="Show current privilege level")
def show_privilege(s, a, neg):
    s.print("Current privilege level is %d" % (1 if s.mode is M.EXEC else 15))


@R.command("show history", EX, help="Display the session command history")
def show_history(s, a, neg):
    s.print(*(f"  {h}" for h in s.history[-50:]))


@R.command("show users", EX, help="Display information about terminal lines")
def show_users(s, a, neg):
    s.print(s.run_local(["who"]))


def parse_ifconfig(text):
    out, cur = [], None
    for line in text.splitlines():
        if line and not line[0].isspace() and ":" in line:
            name = line.split(":", 1)[0]
            flags = re.search(r"<([^>]*)>", line)
            cur = {"name": name, "flags": (flags.group(1).split(",") if flags else []), "inet": [], "status": ""}
            out.append(cur)
        elif cur is not None:
            parts = line.split()
            if parts and parts[0] == "inet":
                cur["inet"].append(parts[1])
            elif parts and parts[0] == "status:":
                cur["status"] = " ".join(parts[1:])
    return out


@R.command("show interfaces [<ifname:iface>]", EX, help="Interface status and configuration")
def show_interfaces(s, a, neg):
    s.print(s.run_local(["ifconfig", "-v", a["ifname"]] if "ifname" in a else ["ifconfig", "-a"]))


@R.command("show ip interface brief", EX, help="Brief summary of IP status and configuration")
def show_ip_int_brief(s, a, neg):
    cfg = s.running
    s.print(f"{'Interface':<16}{'IP-Address':<18}{'OK?':<5}{'Method':<8}{'Status':<23}Protocol")
    text = s.run_local(["ifconfig", "-a"])
    entries = parse_ifconfig(text) if not text.startswith("%") else []
    if not entries:
        entries = [{"name": i.name, "flags": [] if i.shutdown else ["UP"], "inet": [a_.split("/")[0] for a_ in i.ipv4],
                    "status": "unknown"} for i in cfg.sorted_interfaces()]
    for e in entries:
        itf = cfg.interfaces.get(e["name"])
        method = "DHCP" if itf and itf.dhcp_client else ("manual" if itf and itf.ipv4 else "unset")
        status = "up" if "UP" in e["flags"] else "administratively down"
        proto = "up" if "RUNNING" in e["flags"] or e["status"] in ("active", "") and "UP" in e["flags"] else "down"
        s.print(f"{e['name']:<16}{(e['inet'][0] if e['inet'] else 'unassigned'):<18}{'YES':<5}{method:<8}"
                f"{status:<23}{proto}")


@R.command("show ip route", EX, help="IP routing table")
def show_ip_route(s, a, neg):
    if s.running.ospf or s.running.rip or s.running.bgp:
        s.helper("frr-route")
    else:
        s.print(s.run_local(["netstat", "-rn", "-f", "inet"]))


@R.command("show ipv6 route", EX, help="IPv6 routing table")
def show_ipv6_route(s, a, neg):
    s.print(s.run_local(["netstat", "-rn", "-f", "inet6"]))


@R.command("show ( arp | ip arp )", EX, help="ARP table")
def show_arp(s, a, neg):
    s.print(s.run_local(["arp", "-an"]))


@R.command("show ntp associations", EX, help="NTP associations")
def show_ntp(s, a, neg):
    s.print(s.run_local(["ntpq", "-pn"]))


@R.command("show hosts", EX, help="IP domain-name, lookup style, nameservers, and host table")
def show_hosts(s, a, neg):
    c = s.running
    s.print(f"Default domain is {c.domain_name or 'not set'}",
            f"Name servers are {', '.join(c.name_servers) or 'not set (system resolv.conf)'}",
            f"DNS server (dnsmasq) is {'enabled' if c.dns_server else 'disabled'}", "",
            f"{'Host':<32}Address")
    for n, ip in sorted(c.hosts.items()):
        s.print(f"{n:<32}{ip}")


@R.command("show vlans", EX, help="802.1Q VLAN subinterfaces")
def show_vlans(s, a, neg):
    s.print(f"{'VLAN':<6}{'Interface':<14}{'Parent':<10}Address")
    for itf in s.running.sorted_interfaces():
        if itf.is_vlan:
            s.print(f"{itf.vlan_id:<6}{itf.name:<14}{itf.parent:<10}{', '.join(itf.ipv4) or '-'}")


@R.command("show ip protocols", EX, help="IP routing protocol process parameters and statistics")
def show_ip_protocols(s, a, neg):
    c = s.running
    if not (c.ospf or c.rip or c.bgp):
        s.print("No dynamic routing protocols configured (static routing only).")
    if c.ospf:
        s.print(f'Routing Protocol is "ospf {c.ospf.process_id}"', f"  Router ID {c.ospf.router_id or 'auto'}",
                "  Routing for Networks:", *(f"    {n['prefix']} area {n['area']}" for n in c.ospf.networks),
                f"  Redistributing: {', '.join(c.ospf.redistribute) or 'none'}")
    if c.rip:
        s.print(f'Routing Protocol is "rip" version {c.rip.version}', "  Routing for Networks:",
                *(f"    {n}" for n in c.rip.networks))
    if c.bgp:
        s.print(f'Routing Protocol is "bgp {c.bgp.asn}"', "  Neighbors:",
                *(f"    {ip} remote-as {nb['remote_as']}" for ip, nb in c.bgp.neighbors.items()))


# ---------------------------------------------------------------------------
# Privileged show commands
# ---------------------------------------------------------------------------

@R.command("show running-config [interface <ifname:iface>]", PR, help="Current operating configuration")
def show_run(s, a, neg):
    s.refresh()
    if "ifname" in a:
        itf = s.running.interfaces.get(a["ifname"])
        if not itf:
            s.error(f"% Interface {a['ifname']} is not configured")
        s.print(*showrun.interface_text(itf), "end")
        return
    s.print(showrun.render(s.running, header=f"Current configuration : {time.strftime('%c')}"))


@R.command("write terminal", PR, help="Write to terminal")
def write_terminal(s, a, neg):
    show_run(s, {}, False)


@R.command("show startup-config", PR, help="Contents of startup configuration")
def show_start(s, a, neg):
    cfg = s.store.startup()
    if cfg is None:
        s.error("% startup-config is not present")
    s.print(showrun.render(cfg, header="Startup configuration"))


@R.command("show candidate-config", COMMIT_MODES, help="Uncommitted candidate configuration")
def show_candidate(s, a, neg):
    s.print(showrun.render(s.candidate_or_running(), header="Candidate configuration (uncommitted)"))


@R.command("show configuration changes", COMMIT_MODES, help="Diff between running and candidate configuration")
def show_changes(s, a, neg):
    s.print(s.diff_text(s.running, s.without_untouched(s.candidate_or_running())))


@R.command("show archive", PR, help="Committed configuration history")
def show_archive(s, a, neg):
    s.print(f"{'ID':<8}{'Time':<26}{'User':<12}Comment")
    for rec in reversed(s.store.archive_list()):
        s.print(f"{rec['id']:<8}{rec['time']:<26}{rec['user'] or '':<12}{rec['comment'] or ''}")
    p = s.store.pending()
    if p:
        s.print("", f"Commit-confirmed pending: rollback in {max(0, int(p['deadline'] - time.time()))}s")


@R.command("show archive config <id:int(1-999999)>", PR, help="Display an archived configuration")
def show_archive_cfg(s, a, neg):
    cfg = s.store.archive_get(a["id"])
    if not cfg:
        s.error(f"% Archive {a['id']} not found")
    s.print(showrun.render(cfg, header=f"Archived configuration {a['id']}"))


@R.command("show archive diff <id:int(1-999999)>", PR, help="Diff an archived configuration against running")
def show_archive_diff(s, a, neg):
    cfg = s.store.archive_get(a["id"])
    if not cfg:
        s.error(f"% Archive {a['id']} not found")
    s.print(s.diff_text(cfg, s.running, f"archive-{a['id']}", "running-config"))


@R.command("show access-lists [<acl:acl>]", PR, help="List access lists")
def show_acls(s, a, neg):
    acls = s.running.acls
    names = [a["acl"]] if "acl" in a else sorted(acls)
    for n in names:
        acl = acls.get(n)
        if not acl:
            s.error(f"% Access list {n} not found")
        s.print(f"{acl.kind.capitalize()} IP access list {acl.name}")
        for e in acl.entries:
            s.print(f"    {e.seq} {showrun.acl_entry_text(e, acl.kind)}")
    s.print("", "(Hit counters: 'show pf rules')")


@R.command("show pf {what:rules|nat|info|states}", PR, help="FreeBSD pf firewall state")
def show_pf(s, a, neg):
    s.helper({"rules": "pf-rules", "nat": "pf-nat", "info": "pf-info", "states": "nat-translations"}[a["what"]])


@R.command("show ip nat translations", PR, help="Translation entries")
def show_nat_trans(s, a, neg):
    s.helper("nat-translations")


@R.command("show ip nat statistics", PR, help="Translation statistics")
def show_nat_stats(s, a, neg):
    s.helper("pf-info")


@R.command("show ip dhcp binding", EX, help="DHCP address bindings")
def show_dhcp_binding(s, a, neg):
    s.print(f"{'IP address':<18}{'Hardware address':<20}{'Lease expiration':<26}Hostname")
    try:
        with open(paths.DNSMASQ_LEASES) as fh:
            for line in fh:
                p = line.split()
                if len(p) >= 4:
                    exp = "Infinite" if p[0] == "0" else time.strftime("%b %d %Y %I:%M %p", time.localtime(int(p[0])))
                    s.print(f"{p[2]:<18}{p[1]:<20}{exp:<26}{p[3] if p[3] != '*' else ''}")
    except FileNotFoundError:
        pass


@R.command("show ip dhcp pool [<pname:dhcppool>]", EX, help="DHCP pools information")
def show_dhcp_pool(s, a, neg):
    cfg = s.running
    for pool in sorted(cfg.dhcp_pools.values(), key=lambda p: p.name):
        if "pname" in a and pool.name != a["pname"]:
            continue
        s.print(f"Pool {pool.name} :", f" Network               : {pool.network or 'not set'}",
                f" Default router        : {', '.join(pool.default_routers) or '-'}",
                f" DNS servers           : {', '.join(pool.dns_servers) or ('this router' if cfg.dns_server else '-')}",
                f" Lease                 : {'infinite' if pool.lease_seconds == 0 else str(pool.lease_seconds) + 's'}")
        if pool.network:
            rngs = pool_ranges(cfg, pool)
            total = sum(int(ipaddress.IPv4Address(e)) - int(ipaddress.IPv4Address(b_)) + 1 for b_, e in rngs)
            s.print(f" Assignable addresses  : {total}")
            for b_, e in rngs:
                s.print(f"   {b_} - {e}")
        for b in pool.bindings:
            s.print(f" Static binding        : {b['mac']} -> {b['ip']} {b.get('hostname', '')}")
        s.print("")


@R.command("show ip ospf {what:neighbor|interface|database}", EX, help="OSPF information")
def show_ospf(s, a, neg):
    s.helper(f"ospf-{a['what']}")


@R.command("show ip bgp [summary]", EX, help="BGP information")
def show_bgp(s, a, neg):
    s.helper("bgp-summary" if a.get("summary") else "bgp-table")


@R.command("show ip rip [status]", EX, help="RIP information")
def show_rip(s, a, neg):
    s.helper("rip-status" if a.get("status") else "rip-routes")


@R.command("show logging", PR, help="Show the contents of the system log")
def show_logging(s, a, neg):
    s.helper("logging")


# ---------------------------------------------------------------------------
# Configuration lifecycle
# ---------------------------------------------------------------------------

@R.command("commit [confirmed [<minutes:int(1-60)>]] [comment <text:line>]", COMMIT_MODES,
           help="Validate and apply the candidate configuration")
def commit(s, a, neg):
    minutes = a.get("minutes", 10) if a.get("confirmed") else None
    s.commit(comment=a.get("text", ""), confirm_minutes=minutes)


@R.command("commit force", [M.PRIV], help="Re-render and re-apply the full running configuration")
def commit_force(s, a, neg):
    if s.confirm("Re-apply every setting (interfaces may flap)? [confirm]"):
        s.commit(comment="commit force", force=True)


@R.command("abort", list(M.CONFIG_MODES), help="Discard uncommitted changes and exit to EXEC")
def abort(s, a, neg):
    s.candidate = None
    s.to_mode(M.PRIV)


@R.command("( write [memory] | copy running-config startup-config )", PR, help="Save running config as startup")
def write_mem(s, a, neg):
    s.print("Building configuration...")
    res = s.store.call("save-startup")
    s.print(res.get("message") if res.get("ok") else f"% {res.get('error')}")


@R.command("write erase", PR, help="Erase startup-config")
def write_erase(s, a, neg):
    if s.confirm("Erasing the startup-config file system will remove all configuration files! "
                 "Continue? [confirm]"):
        res = s.store.call("erase-startup")
        s.print(res.get("message") if res.get("ok") else f"% {res.get('error')}")


@R.command("copy startup-config running-config", PR, help="Replace running config with startup-config")
def copy_start_run(s, a, neg):
    cfg = s.store.startup()
    if cfg is None:
        s.error("% startup-config is not present")
    s.print(s.diff_text(s.running, cfg, "running-config", "startup-config"))
    if s.confirm("Apply startup-config now? [confirm]"):
        s.candidate = cfg
        try:
            s.commit(comment="copy startup-config running-config")
        finally:
            s.candidate = None


@R.command("rollback [<id:int(1-999999)>]", PR, help="Roll back to an archived configuration (default: previous)")
def rollback(s, a, neg):
    ids = [r["id"] for r in s.store.archive_list()]
    target = a.get("id") or (ids[-2] if len(ids) >= 2 else None)
    cfg = s.store.archive_get(target) if target else None
    if cfg is None:
        s.error("% No such archived configuration (see 'show archive')")
    s.print(s.diff_text(s.running, cfg, "running-config", f"archive-{target}"))
    if s.confirm(f"Roll back to configuration {target}? [confirm]"):
        s.candidate = cfg
        try:
            s.commit(comment=f"rollback to {target}")
        finally:
            s.candidate = None


# ---------------------------------------------------------------------------
# clear / reload
# ---------------------------------------------------------------------------

@R.command("clear ip nat translation *", PR, help="Clear all dynamic NAT translations (pf states)")
def clear_nat(s, a, neg):
    s.helper("clear-nat")


@R.command("clear arp-cache", PR, help="Clear the entire ARP cache")
def clear_arp(s, a, neg):
    s.helper("clear-arp")


@R.command("clear ip ospf process", PR, help="Reset OSPF process")
def clear_ospf(s, a, neg):
    if s.confirm("Reset ALL OSPF processes? [no]: ", default=False):
        s.helper("clear-ospf")


@R.command("clear ip bgp *", PR, help="Reset all BGP sessions")
def clear_bgp(s, a, neg):
    s.helper("clear-bgp")


@R.command("clear counters", PR, help="Clear pf rule counters")
def clear_counters(s, a, neg):
    s.helper("clear-counters")


@R.command("reload", PR, help="Halt and perform a cold restart")
def reload(s, a, neg):
    startup = s.store.startup()
    if startup is None or startup.to_dict() != s.running.to_dict():
        ans = s.ask("System configuration has been modified. Save? [yes/no]: ")
        if ans in ("y", "yes"):
            write_mem(s, {}, False)
    if s.confirm("Proceed with reload? [confirm]"):
        s.helper("reload")


@R.command("show debug-state", PR, help="(internal) dump session state")
def show_debug(s, a, neg):
    s.print(json.dumps({"mode": s.mode, "stack": [m for m, _ in s.stack], "dirty": s.dirty(),
                        "sandbox": s.store.sandbox, "python": sys.version.split()[0],
                        "pid": os.getpid()}, indent=1))
