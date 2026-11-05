"""Dynamic routing sub-modes: router ospf / router rip / router bgp (rendered to FRR)."""

import ipaddress

from routercli.cli import modes as M
from routercli.cli.commands import registry as R
from routercli.config.model import check_text

OSPF, RIP, BGP = [M.OSPF], [M.RIP], [M.BGP]


def _toggle(lst, item, neg):
    if neg:
        if item in lst:
            lst.remove(item)
    elif item not in lst:
        lst.append(item)


def _classful(addr: str) -> str:
    first = int(addr.split(".")[0])
    plen = 8 if first < 128 else 16 if first < 192 else 24
    return str(ipaddress.IPv4Network(f"{addr}/{plen}", strict=False))


# ---- OSPF ---------------------------------------------------------------------

@R.command("router-id <rid:ipv4>", OSPF, help="Router-id for this OSPF process", no=True)
def ospf_rid(s, a, neg):
    s.cfg.ospf.router_id = None if neg else a["rid"]


@R.command("router-id", OSPF, no_only=True)
def ospf_no_rid(s, a, neg):
    s.cfg.ospf.router_id = None


@R.command("network <net:ipv4> <wild:wildcard> area <area:area>", OSPF, help="Enable routing on an IP network",
           no=True)
def ospf_network(s, a, neg):
    prefix = str(ipaddress.IPv4Network(f"{a['net']}/{a['wild']}", strict=False))
    entry = {"prefix": prefix, "area": a["area"]}
    nets = s.cfg.ospf.networks
    if neg:
        s.cfg.ospf.networks = [n for n in nets if n != entry]
    elif entry not in nets:
        if any(n["prefix"] == prefix for n in nets):
            s.error(f"% Network {prefix} is already configured in another area")
        nets.append(entry)


@R.command("passive-interface ( default | <ifname:iface> )", OSPF + RIP,
           help="Suppress routing updates on an interface", no=True)
def passive(s, a, neg):
    proto = s.cfg.ospf if s.mode == M.OSPF else s.cfg.rip
    if a.get("default"):
        if s.mode != M.OSPF:
            s.error("% 'passive-interface default' is only supported for OSPF")
        proto.passive_default = not neg
        return
    _toggle(proto.passive, a["ifname"], neg)


@R.command("redistribute {src:connected|static|rip|bgp} [subnets]", OSPF,
           help="Redistribute information from another routing protocol", no=True)
def ospf_redist(s, a, neg):
    _toggle(s.cfg.ospf.redistribute, a["src"], neg)


@R.command("default-information originate [always]", OSPF, help="Distribute a default route", no=True)
def ospf_default(s, a, neg):
    s.cfg.ospf.default_originate = not neg
    s.cfg.ospf.default_originate_always = bool(a.get("always")) and not neg


# ---- RIP ------------------------------------------------------------------------

@R.command("version {v:1|2}", RIP, help="Set routing protocol version")
def rip_version(s, a, neg):
    s.cfg.rip.version = int(a["v"])


@R.command("network <net:ipv4>", RIP, help="Enable routing on an IP network (classful)", no=True)
def rip_network(s, a, neg):
    _toggle(s.cfg.rip.networks, _classful(a["net"]), neg)


@R.command("redistribute {src:connected|static|ospf|bgp} [metric <m:int(1-16)>]", RIP,
           help="Redistribute information from another routing protocol", no=True)
def rip_redist(s, a, neg):
    _toggle(s.cfg.rip.redistribute, a["src"], neg)


@R.command("default-information originate", RIP, help="Distribute a default route", no=True)
def rip_default(s, a, neg):
    s.cfg.rip.default_originate = not neg


# ---- BGP ------------------------------------------------------------------------

@R.command("bgp router-id <rid:ipv4>", BGP, help="Override configured BGP router identifier", no=True)
def bgp_rid(s, a, neg):
    s.cfg.bgp.router_id = None if neg else a["rid"]


@R.command("neighbor <nb:ip> remote-as <asn:int(1-4294967295)>", BGP, help="Specify a BGP neighbor", no=True)
def bgp_neighbor(s, a, neg):
    nbs = s.cfg.bgp.neighbors
    if neg:
        nbs.pop(a["nb"], None)
    else:
        nbs.setdefault(a["nb"], {})["remote_as"] = a["asn"]


@R.command("neighbor <nb:ip>", BGP, no_only=True)
def bgp_no_neighbor(s, a, neg):
    s.cfg.bgp.neighbors.pop(a["nb"], None)


def _nb(s, ip):
    nb = s.cfg.bgp.neighbors.get(ip)
    if nb is None:
        s.error(f"% Specify remote-as or peer-group commands first ({ip})")
    return nb


@R.command("neighbor <nb:ip> description <text:line>", BGP, help="Neighbor specific description", no=True)
def bgp_nb_desc(s, a, neg):
    nb = _nb(s, a["nb"])
    if neg:
        nb.pop("description", None)
    else:
        nb["description"] = check_text(a["text"], "description")


@R.command("neighbor <nb:ip> shutdown", BGP, help="Administratively shut down this neighbor", no=True)
def bgp_nb_shut(s, a, neg):
    _nb(s, a["nb"])["shutdown"] = not neg


@R.command("network <net:ipv4> [mask <mask:mask>]", BGP, help="Specify a network to announce via BGP", no=True)
def bgp_network(s, a, neg):
    prefix = (str(ipaddress.IPv4Network(f"{a['net']}/{a['mask']}", strict=False)) if "mask" in a
              else _classful(a["net"]))
    _toggle(s.cfg.bgp.networks, prefix, neg)


@R.command("network <pfx:ipv6pfx>", BGP, help="Specify an IPv6 network to announce via BGP", no=True)
def bgp_network6(s, a, neg):
    _toggle(s.cfg.bgp.networks, str(ipaddress.IPv6Interface(a["pfx"]).network), neg)


@R.command("redistribute {src:connected|static|ospf|rip}", BGP,
           help="Redistribute information from another routing protocol", no=True)
def bgp_redist(s, a, neg):
    _toggle(s.cfg.bgp.redistribute, a["src"], neg)
