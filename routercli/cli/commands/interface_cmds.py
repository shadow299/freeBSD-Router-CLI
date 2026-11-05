"""Interface configuration mode (config-if / config-subif)."""

import getpass
import ipaddress

from routercli.auth import type7_decode
from routercli.cli import modes as M
from routercli.cli.commands import registry as R
from routercli.config.model import PPP_SECRET_RE, PPPOE_MAX_MTU, check_text

IF = [M.IF]


def _itf(s):
    return s.cfg.interface(s.context)


def _dialer(s):
    itf = _itf(s)
    if not itf.is_dialer:
        s.error("% Only valid on dialer interfaces (PPPoE): 'interface dialer1'")
    return itf


def _not_dialer(s, hint="% Not supported on dialer interfaces"):
    itf = _itf(s)
    if itf.is_dialer:
        s.error(hint)
    return itf


NEGOTIATED_HINT = "% A dialer gets its address from the ISP: use 'ip address negotiated'"


@R.command("description <text:line>", IF, help="Interface specific description", no=True)
def description(s, a, neg):
    if neg:
        _itf(s).description = ""
        return
    check_text(a["text"], "description")
    _itf(s).description = a["text"]


@R.command("description", IF, no_only=True)
def no_description(s, a, neg):
    _itf(s).description = ""


@R.command("ip address <addr:ipv4> <mask:mask> [secondary]", IF, help="Set the IP address of an interface", no=True)
def ip_address(s, a, neg):
    itf = _not_dialer(s, NEGOTIATED_HINT)
    cidr = str(ipaddress.IPv4Interface(f"{a['addr']}/{a['mask']}"))
    iface = ipaddress.IPv4Interface(cidr)
    if not neg and a["mask"] < 31 and iface.ip in (iface.network.network_address, iface.network.broadcast_address):
        s.error(f"% Bad mask /{a['mask']} for address {a['addr']}")
    if neg:
        if cidr not in itf.ipv4:
            s.error("% Address not configured on this interface")
        if not a.get("secondary") and itf.ipv4 and itf.ipv4[0] == cidr and len(itf.ipv4) > 1:
            s.error("% Must delete secondary before deleting primary")
        itf.ipv4.remove(cidr)
        return
    itf.dhcp_client = False
    for other in s.cfg.interfaces.values():
        for x in other.ipv4:
            if other.name != itf.name and ipaddress.IPv4Interface(x).network.overlaps(iface.network):
                s.error(f"% {iface.network} overlaps with {other.name}")
    if a.get("secondary"):
        if not itf.ipv4:
            s.error("% Primary address must be configured first")
        if cidr not in itf.ipv4:
            itf.ipv4.append(cidr)
    else:
        itf.ipv4 = [cidr] + [x for x in itf.ipv4[1:] if x != cidr]


@R.command("ip address", IF, no_only=True)
def no_ip_address(s, a, neg):
    itf = _itf(s)
    itf.ipv4 = []
    itf.dhcp_client = False
    itf.negotiated = False


@R.command("ip address dhcp", IF, help="IP address negotiated via DHCP", no=True)
def ip_address_dhcp(s, a, neg):
    itf = _not_dialer(s, NEGOTIATED_HINT)
    if neg:
        itf.dhcp_client = False
        return
    itf.ipv4 = []
    itf.dhcp_client = True


@R.command("ipv6 address <addr:ipv6pfx>", IF, help="Configure IPv6 address on interface", no=True)
def ipv6_address(s, a, neg):
    itf = _not_dialer(s, "% IPv6 addresses are not supported on dialer interfaces")
    if neg:
        if a["addr"] not in itf.ipv6:
            s.error("% Address not configured on this interface")
        itf.ipv6.remove(a["addr"])
    elif a["addr"] not in itf.ipv6:
        itf.ipv6.append(a["addr"])


@R.command("ipv6 address", IF, no_only=True)
def no_ipv6_address(s, a, neg):
    _itf(s).ipv6 = []


@R.command("shutdown", IF, help="Shutdown the selected interface", no=True)
def shutdown(s, a, neg):
    _itf(s).shutdown = not neg


@R.command("mtu <bytes:int(576-9216)>", IF, help="Set the interface Maximum Transmission Unit (MTU)", no=True)
def mtu(s, a, neg):
    itf = _itf(s)
    if not neg and itf.is_dialer and a["bytes"] > PPPOE_MAX_MTU:
        s.error(f"% PPPoE MTU is at most {PPPOE_MAX_MTU} (1500 minus 8 bytes PPPoE header)")
    itf.mtu = None if neg else a["bytes"]


@R.command("hardware-offload", IF, no=True,
           help="NIC checksum/TSO/LRO offload (use 'no hardware-offload' for buggy NICs)")
def hardware_offload(s, a, neg):
    _not_dialer(s).offload = not neg


@R.command("mtu", IF, no_only=True)
def no_mtu(s, a, neg):
    _itf(s).mtu = None


@R.command("encapsulation dot1Q <vid:int(1-4094)>", IF, help="IEEE 802.1Q Virtual LAN")
def encapsulation(s, a, neg):
    itf = _itf(s)
    if not itf.is_vlan:
        s.error("% Encapsulation is only supported on subinterfaces (e.g. interface em0.10)")
    if itf.vlan_id != a["vid"]:
        s.error(f"% FreeBSD names VLAN interfaces after their tag: use 'interface {itf.parent}.{a['vid']}'")


@R.command("ip nat {role:inside|outside}", IF, help="NAT interface commands", no=True)
def ip_nat(s, a, neg):
    itf = _itf(s)
    if neg:
        if itf.nat == a["role"]:
            itf.nat = None
    else:
        itf.nat = a["role"]


@R.command("ip access-group <acl:acl> {dir:in|out}", IF, help="Specify access control for packets", no=True)
def access_group(s, a, neg):
    itf = _itf(s)
    attr = "acl_in" if a["dir"] == "in" else "acl_out"
    if neg:
        setattr(itf, attr, None)
        return
    if a["acl"] not in s.cfg.acls:
        s.print(f"% Warning: access-list {a['acl']} is not defined yet (it permits everything until defined)")
    setattr(itf, attr, a["acl"])


@R.command("ip access-group {dir:in|out}", IF, no_only=True)
def no_access_group(s, a, neg):
    setattr(_itf(s), "acl_in" if a["dir"] == "in" else "acl_out", None)


# ---------------------------------------------------------------------------
# PPPoE client (FreeBSD ppp(8)), IOS style:
#   interface re1                      interface dialer1
#    pppoe enable group global          ip address negotiated
#    pppoe-client dial-pool-number 1    encapsulation ppp
#                                       dialer pool 1
#                                       ppp chap hostname USER
#                                       ppp chap password PASSWORD
# ---------------------------------------------------------------------------

@R.command("pppoe enable [group global]", IF, help="Enable PPPoE on this Ethernet port", no=True)
def pppoe_enable(s, a, neg):
    itf = _not_dialer(s, "% 'pppoe enable' belongs on the Ethernet port (e.g. interface re1)")
    if neg:
        itf.pppoe_pool, itf.pppoe_service = None, ""


@R.command("pppoe-client dial-pool-number <pool:int(1-255)> [service-name <svc:pppoesvc>]", IF,
           help="Run a PPPoE client session on this port for the dialer with this pool", no=True)
def pppoe_client(s, a, neg):
    itf = _not_dialer(s, "% 'pppoe-client' belongs on the Ethernet port (e.g. interface re1)")
    if neg:
        itf.pppoe_pool, itf.pppoe_service = None, ""
        return
    if itf.is_cloned:
        s.error("% PPPoE needs an Ethernet port or a VLAN subinterface")
    itf.pppoe_pool = a["pool"]
    itf.pppoe_service = a.get("svc", "")


@R.command("pppoe-client", IF, no_only=True)
def no_pppoe_client(s, a, neg):
    itf = _itf(s)
    itf.pppoe_pool, itf.pppoe_service = None, ""


@R.command("ip address negotiated", IF, help="IP address negotiated over PPP (PPPoE)", no=True)
def ip_address_negotiated(s, a, neg):
    _dialer(s).negotiated = not neg


@R.command("encapsulation ppp", IF, help="Point-to-Point protocol (dialer interfaces)")
def encapsulation_ppp(s, a, neg):
    _dialer(s)


@R.command("dialer pool <pool:int(1-255)>", IF, help="Use the PPPoE port(s) of this dial pool", no=True)
def dialer_pool(s, a, neg):
    _dialer(s).dialer_pool = None if neg else a["pool"]


@R.command("dialer pool", IF, no_only=True)
def no_dialer_pool(s, a, neg):
    _dialer(s).dialer_pool = None


def _password(s, a):
    if "enc" in a:
        try:
            pw = type7_decode(a["enc"])
        except ValueError:
            s.error("% Invalid encrypted (type 7) password")
    else:
        pw = a["pw"]
    if not PPP_SECRET_RE.match(pw):
        s.error("% Invalid password: 1-64 printable ASCII characters, not starting with '!' or a space"
                " (FreeBSD ppp would treat a leading '!' as a program to run)")
    return pw


@R.command("ppp chap hostname <user:pppuser>", IF, help="PPP user name (login) from your ISP", no=True)
def ppp_chap_hostname(s, a, neg):
    _dialer(s).ppp_username = "" if neg else a["user"]


@R.command("ppp chap hostname", IF, no_only=True)
def no_ppp_chap_hostname(s, a, neg):
    _dialer(s).ppp_username = ""


@R.command("ppp chap password ( [0] <pw:line> | 7 <enc:word> )", IF,
           help="PPP password from your ISP (type 'ppp chap password' alone to enter it hidden)", no=True)
def ppp_chap_password(s, a, neg):
    itf = _dialer(s)
    itf.ppp_password = "" if neg else _password(s, a)


@R.command("ppp chap password", IF, help="Enter the PPP password without showing it", no=True)
def ppp_chap_password_prompt(s, a, neg):
    itf = _dialer(s)
    if neg:
        itf.ppp_password = ""
        return
    if not s.interactive:
        s.error("% Give the password: ppp chap password PASSWORD")
    s.flush()
    try:
        pw = getpass.getpass("PPP password: ")
        again = getpass.getpass("Confirm PPP password: ")
    except EOFError:
        s.error("% Aborted")
    if pw != again:
        s.error("% Passwords do not match")
    itf.ppp_password = _password(s, {"pw": pw})


@R.command("ppp pap sent-username <user:pppuser> password ( [0] <pw:line> | 7 <enc:word> )", IF,
           help="PPP user name and password (PAP); same account as 'ppp chap'", no=True)
def ppp_pap(s, a, neg):
    itf = _dialer(s)
    if neg:
        itf.ppp_username, itf.ppp_password = "", ""
        return
    itf.ppp_password = _password(s, a)
    itf.ppp_username = a["user"]


@R.command("ppp pap sent-username", IF, no_only=True)
def no_ppp_pap(s, a, neg):
    itf = _dialer(s)
    itf.ppp_username, itf.ppp_password = "", ""


@R.command("ppp ipcp dns request", IF, help="Use the DNS servers the ISP offers", no=True)
def ppp_ipcp_dns(s, a, neg):
    _dialer(s).ppp_dns = not neg
