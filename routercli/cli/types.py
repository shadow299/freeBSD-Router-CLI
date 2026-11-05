"""Typed command parameters: validation, conversion, completion and '?' help."""

import ipaddress
import re

from routercli.config.model import HOSTNAME_RE, IFACE_RE, MAC_RE, NAME_RE
from routercli.config.showrun import PORT_NAMES


class ParamType:
    placeholder = "WORD"
    help = ""
    greedy = False                         # consumes the rest of the line

    def __init__(self, arg=None):
        self.arg = arg

    def convert(self, token: str, session):
        """Return converted value or raise ValueError."""
        return token

    def complete(self, session) -> list:
        return []


class Word(ParamType):
    placeholder = "WORD"

    def convert(self, token, session):
        if not NAME_RE.match(token):
            raise ValueError
        return token


class Line(ParamType):
    placeholder = "LINE"
    greedy = True


class Int(ParamType):
    def __init__(self, arg="0-4294967295"):
        super().__init__(arg)
        lo, hi = arg.split("-")
        self.lo, self.hi = int(lo), int(hi)
        self.placeholder = f"<{self.lo}-{self.hi}>"

    def convert(self, token, session):
        if not token.isdigit():
            raise ValueError
        v = int(token)
        if not self.lo <= v <= self.hi:
            raise ValueError
        return v


class IPv4(ParamType):
    placeholder = "A.B.C.D"

    def convert(self, token, session):
        if token.count(".") != 3:
            raise ValueError
        return str(ipaddress.IPv4Address(token))


class Mask(IPv4):
    placeholder = "A.B.C.D"
    help = "Subnet mask"

    def convert(self, token, session):
        ip = super().convert(token, session)
        return ipaddress.IPv4Network(f"0.0.0.0/{ip}").prefixlen


class Wildcard(IPv4):
    placeholder = "A.B.C.D"
    help = "Wildcard bits"

    def convert(self, token, session):
        ip = int(ipaddress.IPv4Address(super().convert(token, session)))
        inv = (~ip) & 0xFFFFFFFF
        plen = bin(inv).count("1")
        if inv != ((0xFFFFFFFF << (32 - plen)) & 0xFFFFFFFF):
            raise ValueError
        return plen


class IPv4Prefix(ParamType):
    placeholder = "A.B.C.D/nn"

    def convert(self, token, session):
        if "/" not in token:
            raise ValueError
        return str(ipaddress.IPv4Network(token, strict=True))


class IPv6(ParamType):
    placeholder = "X:X:X:X::X"

    def convert(self, token, session):
        if ":" not in token:
            raise ValueError
        return str(ipaddress.IPv6Address(token))


class IPv6Prefix(ParamType):
    placeholder = "X:X:X:X::X/<0-128>"

    def convert(self, token, session):
        if ":" not in token or "/" not in token:
            raise ValueError
        return str(ipaddress.IPv6Interface(token))


class IPAny(ParamType):
    placeholder = "A.B.C.D|X:X::X"

    def convert(self, token, session):
        return str(ipaddress.ip_address(token))


class Host(ParamType):
    placeholder = "WORD"
    help = "Hostname or IP address"

    def convert(self, token, session):
        try:
            return str(ipaddress.ip_address(token))
        except ValueError:
            if all(HOSTNAME_RE.match(p) for p in token.split(".")):
                return token
            raise


class Iface(ParamType):
    placeholder = "IFNAME"
    help = "Interface name (re0, em0, igb1, vtnet0, re0.10, lo1 ...)"

    def convert(self, token, session):
        if not IFACE_RE.match(token):
            raise ValueError
        return token

    def complete(self, session):
        return session.interface_names()


class Mac(ParamType):
    placeholder = "H.H.H|HH:HH:HH:HH:HH:HH"

    def convert(self, token, session):
        hexs = re.sub(r"[.:-]", "", token.lower())
        if not re.fullmatch(r"[0-9a-f]{12}", hexs):
            raise ValueError
        mac = ":".join(hexs[i:i + 2] for i in range(0, 12, 2))
        assert MAC_RE.match(mac)
        return mac


class Port(ParamType):
    placeholder = "<0-65535>"
    help = "Port number or name (www, ssh, domain, ...)"

    def convert(self, token, session):
        if token.isdigit() and 0 <= int(token) <= 65535:
            return int(token)
        if token in PORT_NAMES:
            return PORT_NAMES[token]
        raise ValueError

    def complete(self, session):
        return sorted(PORT_NAMES)


class AclName(Word):
    placeholder = "WORD|<1-2699>"
    help = "Access-list name or number"

    def complete(self, session):
        return sorted(session.candidate_or_running().acls)


class AclNumber(ParamType):
    RANGES = {"std": ((1, 99), (1300, 1999)), "ext": ((100, 199), (2000, 2699))}

    def __init__(self, arg="std"):
        super().__init__(arg)
        self.ranges = self.RANGES[arg]
        self.placeholder = " | ".join(f"<{lo}-{hi}>" for lo, hi in self.ranges)
        self.help = "IP standard access list" if arg == "std" else "IP extended access list"

    def convert(self, token, session):
        if token.isdigit() and any(lo <= int(token) <= hi for lo, hi in self.ranges):
            return str(int(token))
        raise ValueError


class DhcpPoolName(Word):
    def complete(self, session):
        return sorted(session.candidate_or_running().dhcp_pools)


class NatPoolName(Word):
    def complete(self, session):
        return sorted(session.candidate_or_running().nat_pools)


class Area(ParamType):
    placeholder = "<0-4294967295>|A.B.C.D"
    help = "OSPF area ID"

    def convert(self, token, session):
        if token.isdigit() and int(token) <= 4294967295:
            return token
        return str(ipaddress.IPv4Address(token))


TYPES = {
    "word": Word, "line": Line, "int": Int, "ipv4": IPv4, "mask": Mask, "wildcard": Wildcard,
    "ipv4pfx": IPv4Prefix, "ipv6": IPv6, "ipv6pfx": IPv6Prefix, "ip": IPAny, "host": Host,
    "iface": Iface, "mac": Mac, "port": Port, "acl": AclName, "aclnum": AclNumber, "dhcppool": DhcpPoolName,
    "natpool": NatPoolName, "area": Area,
}


def make_type(spec: str) -> ParamType:
    m = re.fullmatch(r"(\w+)(?:\(([^)]*)\))?", spec)
    if not m or m.group(1) not in TYPES:
        raise ValueError(f"unknown parameter type {spec!r}")
    cls = TYPES[m.group(1)]
    return cls(m.group(2)) if m.group(2) else cls()
