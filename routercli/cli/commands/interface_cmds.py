"""Interface configuration mode (config-if / config-subif)."""

import ipaddress

from routercli.cli import modes as M
from routercli.cli.commands import registry as R
from routercli.config.model import check_text

IF = [M.IF]


def _itf(s):
    return s.cfg.interface(s.context)


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
    itf = _itf(s)
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


@R.command("ip address dhcp", IF, help="IP address negotiated via DHCP", no=True)
def ip_address_dhcp(s, a, neg):
    itf = _itf(s)
    if neg:
        itf.dhcp_client = False
        return
    itf.ipv4 = []
    itf.dhcp_client = True


@R.command("ipv6 address <addr:ipv6pfx>", IF, help="Configure IPv6 address on interface", no=True)
def ipv6_address(s, a, neg):
    itf = _itf(s)
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
    _itf(s).mtu = None if neg else a["bytes"]


@R.command("hardware-offload", IF, no=True,
           help="NIC checksum/TSO/LRO offload (use 'no hardware-offload' for buggy NICs)")
def hardware_offload(s, a, neg):
    _itf(s).offload = not neg


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
