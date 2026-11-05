"""DHCP server configuration (ip dhcp pool / dhcp-config mode), rendered to dnsmasq."""

import ipaddress

from routercli.cli import modes as M
from routercli.cli.commands import registry as R
from routercli.config.model import DOMAIN_RE, HOSTNAME_RE, DhcpPool

CF, DH = [M.CONFIG], [M.DHCP]


@R.command("ip dhcp pool <name:dhcppool>", CF, help="Configure DHCP address pools", no=True)
def dhcp_pool(s, a, neg):
    name = a["name"]
    if neg:
        if s.cfg.dhcp_pools.pop(name, None) is None:
            s.error(f"% Pool {name} does not exist")
        return
    s.cfg.dhcp_pools.setdefault(name, DhcpPool(name=name))
    s.push(M.DHCP, name)


@R.command("ip dhcp excluded-address <lo:ipv4> [<hi:ipv4>]", CF,
           help="Prevent DHCP from assigning certain addresses", no=True)
def excluded(s, a, neg):
    rng = [a["lo"], a.get("hi", a["lo"])]
    if ipaddress.IPv4Address(rng[0]) > ipaddress.IPv4Address(rng[1]):
        s.error("% Invalid range: low address is higher than high address")
    if neg:
        if rng not in s.cfg.dhcp_excluded:
            s.error("% Range not configured")
        s.cfg.dhcp_excluded.remove(rng)
    elif rng not in s.cfg.dhcp_excluded:
        s.cfg.dhcp_excluded.append(rng)


def _pool(s) -> DhcpPool:
    return s.cfg.dhcp_pools[s.context]


@R.command("network ( <pfx:ipv4pfx> | <net:ipv4> <mask:mask> )", DH, help="Network number and mask", no=True)
def network(s, a, neg):
    if neg:
        _pool(s).network = None
        return
    try:
        net = a["pfx"] if "pfx" in a else str(ipaddress.IPv4Network(f"{a['net']}/{a['mask']}", strict=True))
    except ValueError:
        s.error("% Network address has host bits set")
    _pool(s).network = net


@R.command("default-router <r1:ipv4> [<r2:ipv4> [<r3:ipv4>]]", DH, help="Default routers", no=True)
def default_router(s, a, neg):
    _pool(s).default_routers = [] if neg else [a[k] for k in ("r1", "r2", "r3") if k in a]


@R.command("default-router", DH, no_only=True)
def no_default_router(s, a, neg):
    _pool(s).default_routers = []


@R.command("dns-server <d1:ipv4> [<d2:ipv4> [<d3:ipv4>]]", DH, help="DNS servers", no=True)
def dns_server(s, a, neg):
    _pool(s).dns_servers = [] if neg else [a[k] for k in ("d1", "d2", "d3") if k in a]


@R.command("dns-server", DH, no_only=True)
def no_dns_server(s, a, neg):
    _pool(s).dns_servers = []


@R.command("domain-name <domain:word>", DH, help="Domain name", no=True)
def domain(s, a, neg):
    if neg:
        _pool(s).domain_name = None
        return
    if not DOMAIN_RE.match(a["domain"]):
        s.error("% Invalid domain name")
    _pool(s).domain_name = a["domain"]


@R.command("domain-name", DH, no_only=True)
def no_domain(s, a, neg):
    _pool(s).domain_name = None


@R.command("lease ( infinite | <days:int(0-365)> [<hours:int(0-23)> [<minutes:int(0-59)>]] )", DH,
           help="Address lease time", no=True)
def lease(s, a, neg):
    if neg:
        _pool(s).lease_seconds = 86400
    elif a.get("infinite"):
        _pool(s).lease_seconds = 0
    else:
        secs = a["days"] * 86400 + a.get("hours", 0) * 3600 + a.get("minutes", 0) * 60
        if secs < 120:
            s.error("% Lease must be at least 2 minutes")
        _pool(s).lease_seconds = secs


@R.command("lease", DH, no_only=True)
def no_lease(s, a, neg):
    _pool(s).lease_seconds = 86400


@R.command("static-bind <mac:mac> <addr:ipv4> [<hostname:word>]", DH, help="Static MAC to IP binding (dhcp-host)",
           no=True)
def static_bind(s, a, neg):
    pool = _pool(s)
    pool.bindings = [b for b in pool.bindings if b["mac"] != a["mac"]]
    if neg:
        return
    if "hostname" in a and not HOSTNAME_RE.match(a["hostname"]):
        s.error("% Invalid hostname")
    if any(b["ip"] == a["addr"] for b in pool.bindings):
        s.error(f"% {a['addr']} is already bound to another MAC address")
    b = {"mac": a["mac"], "ip": a["addr"]}
    if "hostname" in a:
        b["hostname"] = a["hostname"]
    pool.bindings.append(b)


@R.command("static-bind <mac:mac>", DH, no_only=True)
def no_static_bind(s, a, neg):
    pool = _pool(s)
    pool.bindings = [b for b in pool.bindings if b["mac"] != a["mac"]]
