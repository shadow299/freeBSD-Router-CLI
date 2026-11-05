"""Global configuration mode commands: hostname, secrets, DNS, NTP, interfaces, static routes."""

import ipaddress

from routercli.auth import hash_secret
from routercli.cli import modes as M
from routercli.cli.commands import registry as R
from routercli.config.model import DOMAIN_RE, HOSTNAME_RE, SECRET_RE, StaticRoute, is_dialer_name

CF = [M.CONFIG]
ALL_CF = list(M.CONFIG_MODES)


@R.command("exit", ALL_CF, help="Exit from the current mode")
def exit_(s, a, neg):
    if s.mode == M.CONFIG:
        if s.leave_config():
            s.to_mode(M.PRIV)
    else:
        s.to_mode(M.CONFIG)


@R.command("end", ALL_CF, help="Exit to privileged EXEC mode")
def end(s, a, neg):
    if s.leave_config():
        s.to_mode(M.PRIV)


@R.command("hostname <name:word>", CF, help="Set system's network name")
def hostname(s, a, neg):
    if not HOSTNAME_RE.match(a["name"]):
        s.error("% Invalid hostname (letters, digits, '-' only; max 63 chars)")
    s.cfg.hostname = a["name"]


@R.command("( ip domain-name | ip domain name ) <domain:word>", CF, help="Define the default domain name", no=True)
def domain_name(s, a, neg):
    if neg:
        s.cfg.domain_name = None
        return
    if not DOMAIN_RE.match(a["domain"]):
        s.error("% Invalid domain name")
    s.cfg.domain_name = a["domain"]


@R.command("enable secret ( [0] <password:line> | 9 <hash:line> )", CF, help="Assign the privileged level secret",
           no=True)
def enable_secret(s, a, neg):
    if neg:
        s.error("% Refusing to remove the enable secret: it would make privileged mode unreachable.")
    if "hash" in a:
        if not SECRET_RE.match(a["hash"]):
            s.error("% Invalid type 9 (scrypt) secret")
        s.cfg.enable_secret = a["hash"]
        return
    if len(a["password"]) < 8:
        s.error("% Secret must be at least 8 characters")
    s.cfg.enable_secret = hash_secret(a["password"])


@R.command("banner motd <text:line>", CF, help="Set message of the day banner", no=True)
def banner(s, a, neg):
    if neg:
        s.cfg.banner_motd = ""
        return
    text = a["text"]
    if len(text) >= 2 and text[0] == text[-1] and not text[0].isalnum():
        text = text[1:-1]
    s.cfg.banner_motd = text.replace("\\n", "\n")


@R.command("banner motd", CF, no_only=True)
def no_banner(s, a, neg):
    s.cfg.banner_motd = ""


@R.command("ip routing", CF, help="Enable IP routing (packet forwarding)", no=True)
def ip_routing(s, a, neg):
    s.cfg.ip_routing = not neg


@R.command("ipv6 unicast-routing", CF, help="Enable IPv6 unicast routing", no=True)
def ipv6_routing(s, a, neg):
    s.cfg.ipv6_routing = not neg


@R.command("ip name-server <a:ip> [<b:ip> [<c:ip>]]", CF, help="Specify address of name server to use", no=True)
def name_server(s, a, neg):
    given = [a[k] for k in ("a", "b", "c") if k in a]
    if neg:
        s.cfg.name_servers = [x for x in s.cfg.name_servers if x not in given]
        return
    for x in given:
        if x not in s.cfg.name_servers:
            s.cfg.name_servers.append(x)
    if len(s.cfg.name_servers) > 6:
        s.error("% Maximum of 6 name servers")


@R.command("ip name-server", CF, no_only=True)
def no_name_server_all(s, a, neg):
    s.cfg.name_servers = []


@R.command("ip dns server", CF, help="Enable the DNS forwarder (dnsmasq)", no=True)
def dns_server(s, a, neg):
    s.cfg.dns_server = not neg


@R.command("ip host <name:word> <addr:ip>", CF, help="Add an entry to the ip hostname table", no=True)
def ip_host(s, a, neg):
    if neg:
        s.cfg.hosts.pop(a["name"], None)
        return
    if not DOMAIN_RE.match(a["name"]):
        s.error("% Invalid host name")
    s.cfg.hosts[a["name"]] = a["addr"]


@R.command("ip host <name:word>", CF, no_only=True)
def no_ip_host(s, a, neg):
    s.cfg.hosts.pop(a["name"], None)


@R.command("ntp server <server:host>", CF, help="Configure NTP server", no=True)
def ntp_server(s, a, neg):
    if neg:
        s.cfg.ntp_servers = [x for x in s.cfg.ntp_servers if x != a["server"]]
    elif a["server"] not in s.cfg.ntp_servers:
        s.cfg.ntp_servers.append(a["server"])


# ---------------------------------------------------------------------------
# Interfaces
# ---------------------------------------------------------------------------

@R.command("interface <ifname:iface>", CF, help="Select an interface to configure", no=True)
def interface(s, a, neg):
    name = a["ifname"]
    if name == "lo0":
        s.error("% lo0 is reserved by the system")
    if neg:
        if name not in s.cfg.interfaces:
            s.error(f"% Interface {name} is not configured")
        del s.cfg.interfaces[name]
        for i in list(s.cfg.interfaces):
            if i.startswith(name + "."):
                del s.cfg.interfaces[i]
        return
    base = name.split(".")[0]
    if is_dialer_name(base):
        if "." in name:
            s.error("% Dialer interfaces have no subinterfaces (for PPPoE over a VLAN put "
                    "'pppoe-client' on the VLAN, e.g. interface re1.35)")
        if int(base[6:]) > 255:
            s.error("% Dialer interface number must be 0-255")
        s.cfg.interface(name)
        s.push(M.IF, name)
        return
    known = s.interface_names()
    if not base.startswith("lo") and base not in known:
        s.print(f"% Warning: {base} does not exist on this system (configuration will still be accepted)")
    if "." in name:
        vid = int(name.split(".")[1])
        if not 1 <= vid <= 4094:
            s.error("% VLAN subinterface number must be 1-4094 (it is the 802.1Q tag)")
    s.cfg.interface(name)
    s.push(M.IF, name)


# ---------------------------------------------------------------------------
# Static routes
# ---------------------------------------------------------------------------

def _route_from_args(a, prefix):
    return StaticRoute(prefix=prefix, nexthop=a.get("nh"), interface=a.get("ifname"),
                       blackhole=bool(a.get("Null0")))


def _set_route(s, r, neg, any_target):
    routes = s.cfg.routes
    if neg:
        before = len(routes)
        s.cfg.routes = [x for x in routes if not (x.prefix == r.prefix and (any_target or x.key() == r.key()))]
        if len(s.cfg.routes) == before:
            s.error("% Route not found")
        return
    r.validate()
    if r.is_default:
        s.cfg.routes = [x for x in routes if not (x.is_default and x.version == r.version)]
    elif any(x.key() == r.key() for x in routes):
        return
    s.cfg.routes.append(r)


@R.command("ip route <net:ipv4> <mask:mask> ( <nh:ipv4> | <ifname:iface> | Null0 )", CF,
           help="Establish static routes", no=True)
def ip_route(s, a, neg):
    prefix = str(ipaddress.IPv4Network(f"{a['net']}/{a['mask']}", strict=False))
    _set_route(s, _route_from_args(a, prefix), neg, False)


@R.command("ip route <net:ipv4> <mask:mask>", CF, no_only=True)
def no_ip_route_any(s, a, neg):
    prefix = str(ipaddress.IPv4Network(f"{a['net']}/{a['mask']}", strict=False))
    _set_route(s, StaticRoute(prefix=prefix), True, True)


@R.command("ipv6 route <pfx:ipv6pfx> ( <nh:ipv6> | <ifname:iface> | Null0 )", CF,
           help="Establish static IPv6 routes", no=True)
def ipv6_route(s, a, neg):
    prefix = str(ipaddress.IPv6Interface(a["pfx"]).network)
    _set_route(s, _route_from_args(a, prefix), neg, False)


@R.command("ipv6 route <pfx:ipv6pfx>", CF, no_only=True)
def no_ipv6_route_any(s, a, neg):
    _set_route(s, StaticRoute(prefix=str(ipaddress.IPv6Interface(a["pfx"]).network)), True, True)


# ---------------------------------------------------------------------------
# Routing process entry
# ---------------------------------------------------------------------------

@R.command("router ospf <pid:int(1-65535)>", CF, help="Open Shortest Path First (OSPF)", no=True)
def router_ospf(s, a, neg):
    from routercli.config.model import Ospf
    if neg:
        s.cfg.ospf = None
        return
    if s.cfg.ospf is None:
        s.cfg.ospf = Ospf(process_id=a["pid"])
    elif s.cfg.ospf.process_id != a["pid"]:
        s.error(f"% Only one OSPF process is supported (ospf {s.cfg.ospf.process_id} exists)")
    s.push(M.OSPF, a["pid"])


@R.command("router rip", CF, help="Routing Information Protocol (RIP)", no=True)
def router_rip(s, a, neg):
    from routercli.config.model import Rip
    if neg:
        s.cfg.rip = None
        return
    if s.cfg.rip is None:
        s.cfg.rip = Rip()
    s.push(M.RIP)


@R.command("router bgp <asn:int(1-4294967295)>", CF, help="Border Gateway Protocol (BGP)", no=True)
def router_bgp(s, a, neg):
    from routercli.config.model import Bgp
    if neg:
        s.cfg.bgp = None
        return
    if s.cfg.bgp is None:
        s.cfg.bgp = Bgp(asn=a["asn"])
    elif s.cfg.bgp.asn != a["asn"]:
        s.error(f"% BGP is already running; AS is {s.cfg.bgp.asn}")
    s.push(M.BGP, a["asn"])
