"""Typed configuration model.

The model is the single source of truth. The CLI edits a *candidate* instance;
the privileged backend receives it as JSON, re-validates it strictly with
``RouterConfig.from_dict(...).validate()`` and only then renders system files.
Every string that ends up in a shell-sourced file (rc.conf) is constrained by a
whitelist regex here, so nothing typed at the CLI can inject shell code.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import asdict, dataclass, field
from typing import Optional

IFACE_RE = re.compile(r"^[a-z]{1,10}[0-9]{1,3}(\.[0-9]{1,4})?$")
NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
TEXT_RE = re.compile(r"^[A-Za-z0-9 _.,:;/@#()+=!?*%&<>\[\]-]{0,200}$")
HOSTNAME_RE = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)((?!-)[A-Za-z0-9-]{1,63}(?<!-)\.)*(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")
MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
SECRET_RE = re.compile(r"^scrypt\$[0-9]+\$[0-9]+\$[0-9]+\$[0-9a-f]{32}\$[0-9a-f]{128}$")
DIALER_RE = re.compile(r"^dialer([0-9]{1,3})$")
# PPP credentials end up in /etc/ppp/ppp.conf, where render_ppp quotes and escapes them.
PPP_USER_RE = re.compile(r"^(?!!)[\x21-\x7e]{1,64}$")
# No leading '!': ppp's CHAP code runs "!..." as a program while PAP sends it
# literally, so no escaping is correct for both (chap.c / pap.c).
PPP_SECRET_RE = re.compile(r"^(?![ !])[\x20-\x7e]{0,63}[\x21-\x7e]$")
PPPOE_SERVICE_RE = re.compile(r"^[A-Za-z0-9_.@/+-]{1,64}$")
PPPOE_MAX_MTU = 1492

PROTOCOLS = ("ip", "tcp", "udp", "icmp", "gre", "esp", "ah", "ospf")
PORT_OPS = ("eq", "neq", "lt", "gt", "range")
REDIST = ("connected", "static", "ospf", "rip", "bgp")


class ConfigError(ValueError):
    pass


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise ConfigError(msg)


def check_iface(name: str) -> str:
    _check(isinstance(name, str) and bool(IFACE_RE.match(name)), f"invalid interface name {name!r}")
    return name


def is_dialer_name(name) -> bool:
    return isinstance(name, str) and bool(DIALER_RE.match(name))


def check_name(name: str, what: str = "name") -> str:
    _check(isinstance(name, str) and bool(NAME_RE.match(name)), f"invalid {what} {name!r}")
    return name


def check_text(text: str, what: str = "text") -> str:
    _check(isinstance(text, str) and bool(TEXT_RE.match(text)),
           f"invalid {what}: only letters, digits, spaces and basic punctuation allowed (no quotes, $, `, \\)")
    return text


def check_v4(addr: str) -> str:
    try:
        return str(ipaddress.IPv4Address(addr))
    except (ipaddress.AddressValueError, ValueError, TypeError):
        raise ConfigError(f"invalid IPv4 address {addr!r}")


def check_v6(addr: str) -> str:
    try:
        return str(ipaddress.IPv6Address(addr))
    except (ipaddress.AddressValueError, ValueError, TypeError):
        raise ConfigError(f"invalid IPv6 address {addr!r}")


def check_ip(addr: str) -> str:
    try:
        return str(ipaddress.ip_address(addr))
    except ValueError:
        raise ConfigError(f"invalid IP address {addr!r}")


def check_v4_iface_addr(value: str) -> str:
    try:
        return str(ipaddress.IPv4Interface(value))
    except (ValueError, TypeError):
        raise ConfigError(f"invalid IPv4 address/prefix {value!r}")


def check_v6_iface_addr(value: str) -> str:
    try:
        return str(ipaddress.IPv6Interface(value))
    except (ValueError, TypeError):
        raise ConfigError(f"invalid IPv6 address/prefix {value!r}")


def check_net(value: str, version: Optional[int] = None) -> str:
    try:
        net = ipaddress.ip_network(value, strict=True)
    except (ValueError, TypeError):
        raise ConfigError(f"invalid network {value!r} (host bits set or malformed)")
    _check(version is None or net.version == version, f"wrong address family for {value!r}")
    return str(net)


def check_addr_spec(value: str) -> str:
    """ACL address: 'any' or a v4 network in CIDR form."""
    if value == "any":
        return value
    return check_net(value, 4)


def check_port(value) -> int:
    _check(isinstance(value, int) and 0 <= value <= 65535, f"invalid port {value!r}")
    return value


# ---------------------------------------------------------------------------
# Interfaces
# ---------------------------------------------------------------------------

@dataclass
class Interface:
    name: str
    description: str = ""
    shutdown: bool = False
    dhcp_client: bool = False
    ipv4: list = field(default_factory=list)      # ["192.0.2.1/24", ...]; index 0 is primary
    ipv6: list = field(default_factory=list)      # ["2001:db8::1/64", ...]
    mtu: Optional[int] = None
    nat: Optional[str] = None                     # "inside" | "outside" | None
    acl_in: Optional[str] = None
    acl_out: Optional[str] = None
    offload: bool = True                          # False: disable NIC checksum/TSO/LRO offload
    # PPPoE client, logical side (interface dialerN -> ppp(8) profile "dialerN")
    negotiated: bool = False                      # ip address negotiated
    dialer_pool: Optional[int] = None             # dialer pool N
    ppp_username: str = ""                        # ppp chap hostname / pap sent-username
    ppp_password: str = ""                        # ppp chap password / pap ... password
    ppp_dns: bool = False                         # ppp ipcp dns request
    # PPPoE client, physical side (Ethernet port or VLAN that carries the session)
    pppoe_pool: Optional[int] = None              # pppoe-client dial-pool-number N
    pppoe_service: str = ""                       # ... service-name NAME

    @property
    def is_vlan(self) -> bool:
        return "." in self.name

    @property
    def parent(self) -> Optional[str]:
        return self.name.split(".", 1)[0] if self.is_vlan else None

    @property
    def vlan_id(self) -> Optional[int]:
        return int(self.name.split(".", 1)[1]) if self.is_vlan else None

    @property
    def is_cloned(self) -> bool:
        return self.name.startswith("lo") and self.name != "lo0"

    @property
    def is_dialer(self) -> bool:
        return is_dialer_name(self.name)

    @property
    def dialer_unit(self) -> Optional[int]:
        return int(DIALER_RE.match(self.name).group(1)) if self.is_dialer else None

    def _validate_ppp(self) -> None:
        n = self.name
        if self.is_dialer:
            _check(0 <= self.dialer_unit <= 255, f"{n}: dialer interface number must be 0-255")
            _check(not self.ipv4 and not self.dhcp_client,
                   f"{n}: dialer interfaces get their address from the ISP - use 'ip address negotiated'")
            _check(not self.ipv6, f"{n}: IPv6 addresses are not supported on dialer interfaces")
            _check(self.pppoe_pool is None, f"{n}: 'pppoe-client' belongs on the Ethernet port, not the dialer")
            if self.mtu is not None:
                _check(576 <= self.mtu <= PPPOE_MAX_MTU, f"{n}: PPPoE mtu must be 576-{PPPOE_MAX_MTU}")
            if self.dialer_pool is not None:
                _check(isinstance(self.dialer_pool, int) and 1 <= self.dialer_pool <= 255,
                       f"{n}: dialer pool must be 1-255")
            if self.ppp_username:
                _check(isinstance(self.ppp_username, str) and bool(PPP_USER_RE.match(self.ppp_username)),
                       f"{n}: invalid PPP username (1-64 printable characters, no spaces, not starting with '!')")
            if self.ppp_password:
                _check(isinstance(self.ppp_password, str) and bool(PPP_SECRET_RE.match(self.ppp_password)),
                       f"{n}: invalid PPP password (1-64 printable ASCII characters, "
                       "not starting with '!' or a space)")
        else:
            _check(not (self.negotiated or self.dialer_pool is not None or self.ppp_username or
                        self.ppp_password or self.ppp_dns),
                   f"{n}: PPP settings are only valid on dialer interfaces (interface dialer1)")
            if self.pppoe_pool is not None:
                _check(isinstance(self.pppoe_pool, int) and 1 <= self.pppoe_pool <= 255,
                       f"{n}: pppoe-client dial-pool-number must be 1-255")
                _check(not self.is_cloned, f"{n}: PPPoE needs an Ethernet port or VLAN subinterface")
            if self.pppoe_service:
                _check(self.pppoe_pool is not None, f"{n}: service-name needs pppoe-client dial-pool-number")
                _check(bool(PPPOE_SERVICE_RE.match(self.pppoe_service)), f"{n}: invalid PPPoE service-name")

    def validate(self) -> None:
        check_iface(self.name)
        _check(self.name != "lo0", "lo0 is managed by the system")
        self._validate_ppp()
        if self.is_vlan:
            _check(1 <= self.vlan_id <= 4094, f"{self.name}: VLAN id must be 1-4094")
        if self.description:
            check_text(self.description, "description")
        self.ipv4 = [check_v4_iface_addr(a) for a in self.ipv4]
        self.ipv6 = [check_v6_iface_addr(a) for a in self.ipv6]
        _check(len(set(self.ipv4)) == len(self.ipv4), f"{self.name}: duplicate IPv4 address")
        _check(not (self.dhcp_client and self.ipv4), f"{self.name}: cannot combine 'ip address dhcp' with static addresses")
        if self.mtu is not None:
            _check(isinstance(self.mtu, int) and 576 <= self.mtu <= 9216, f"{self.name}: mtu must be 576-9216")
        _check(self.nat in (None, "inside", "outside"), f"{self.name}: bad nat role")
        for acl in (self.acl_in, self.acl_out):
            if acl is not None:
                check_name(acl, "access-list name")

    @classmethod
    def from_dict(cls, d: dict) -> "Interface":
        return cls(
            name=d["name"], description=d.get("description", ""), shutdown=bool(d.get("shutdown", False)),
            dhcp_client=bool(d.get("dhcp_client", False)), ipv4=list(d.get("ipv4", [])),
            ipv6=list(d.get("ipv6", [])), mtu=d.get("mtu"), nat=d.get("nat"),
            acl_in=d.get("acl_in"), acl_out=d.get("acl_out"), offload=bool(d.get("offload", True)),
            negotiated=bool(d.get("negotiated", False)), dialer_pool=d.get("dialer_pool"),
            ppp_username=d.get("ppp_username", ""), ppp_password=d.get("ppp_password", ""),
            ppp_dns=bool(d.get("ppp_dns", False)), pppoe_pool=d.get("pppoe_pool"),
            pppoe_service=d.get("pppoe_service", ""))


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

@dataclass
class StaticRoute:
    prefix: str                         # "10.0.0.0/8" or "2001:db8::/32"
    nexthop: Optional[str] = None
    interface: Optional[str] = None
    blackhole: bool = False             # ip route ... Null0

    @property
    def version(self) -> int:
        return ipaddress.ip_network(self.prefix).version

    @property
    def is_default(self) -> bool:
        return ipaddress.ip_network(self.prefix).prefixlen == 0

    def key(self) -> tuple:
        return (self.prefix, self.nexthop, self.interface, self.blackhole)

    @property
    def via_dialer(self) -> bool:
        """Installed by ppp(8) ('add! ... HISADDR') instead of rc.conf/route(8)."""
        return is_dialer_name(self.interface)

    def validate(self) -> None:
        self.prefix = check_net(self.prefix)
        targets = sum(x is not None and x is not False for x in (self.nexthop, self.interface, self.blackhole or None))
        _check(targets == 1, f"route {self.prefix}: specify exactly one of next-hop, interface or Null0")
        if self.nexthop:
            self.nexthop = check_ip(self.nexthop)
            _check(ipaddress.ip_address(self.nexthop).version == self.version,
                   f"route {self.prefix}: next-hop address family mismatch")
        if self.interface:
            check_iface(self.interface)
        if self.via_dialer:
            _check(self.version == 4, f"route {self.prefix}: IPv6 routes over dialer interfaces are not supported")
        elif self.is_default:
            _check(self.nexthop is not None,
                   "default route requires a next-hop address (or a dialer interface, e.g. 'dialer1')")

    @classmethod
    def from_dict(cls, d: dict) -> "StaticRoute":
        return cls(prefix=d["prefix"], nexthop=d.get("nexthop"), interface=d.get("interface"),
                   blackhole=bool(d.get("blackhole", False)))


@dataclass
class Ospf:
    process_id: int = 1
    router_id: Optional[str] = None
    networks: list = field(default_factory=list)          # [{"prefix": "10.0.0.0/24", "area": "0"}]
    passive: list = field(default_factory=list)
    passive_default: bool = False
    redistribute: list = field(default_factory=list)
    default_originate: bool = False
    default_originate_always: bool = False

    def validate(self) -> None:
        _check(1 <= int(self.process_id) <= 65535, "OSPF process id must be 1-65535")
        if self.router_id:
            check_v4(self.router_id)
        for n in self.networks:
            n["prefix"] = check_net(n["prefix"], 4)
            area = str(n["area"])
            if not area.isdigit():
                check_v4(area)
            n["area"] = area
        for i in self.passive:
            check_iface(i)
        for r in self.redistribute:
            _check(r in REDIST and r != "ospf", f"ospf: cannot redistribute {r!r}")

    @classmethod
    def from_dict(cls, d: dict) -> "Ospf":
        return cls(**{k: d[k] for k in d if k in cls.__dataclass_fields__})


@dataclass
class Rip:
    version: int = 2
    networks: list = field(default_factory=list)          # ["10.0.0.0/8"]
    passive: list = field(default_factory=list)
    redistribute: list = field(default_factory=list)
    default_originate: bool = False

    def validate(self) -> None:
        _check(self.version in (1, 2), "RIP version must be 1 or 2")
        self.networks = [check_net(n, 4) for n in self.networks]
        for i in self.passive:
            check_iface(i)
        for r in self.redistribute:
            _check(r in REDIST and r != "rip", f"rip: cannot redistribute {r!r}")

    @classmethod
    def from_dict(cls, d: dict) -> "Rip":
        return cls(**{k: d[k] for k in d if k in cls.__dataclass_fields__})


@dataclass
class Bgp:
    asn: int
    router_id: Optional[str] = None
    neighbors: dict = field(default_factory=dict)         # ip -> {"remote_as": n, "description": "", "shutdown": False}
    networks: list = field(default_factory=list)
    redistribute: list = field(default_factory=list)

    def validate(self) -> None:
        _check(1 <= int(self.asn) <= 4294967295, "invalid BGP AS number")
        if self.router_id:
            check_v4(self.router_id)
        for ip, nb in self.neighbors.items():
            check_ip(ip)
            _check(1 <= int(nb.get("remote_as", 0)) <= 4294967295, f"neighbor {ip}: remote-as required")
            if nb.get("description"):
                check_text(nb["description"], "neighbor description")
        self.networks = [check_net(n) for n in self.networks]
        for r in self.redistribute:
            _check(r in REDIST and r != "bgp", f"bgp: cannot redistribute {r!r}")

    @classmethod
    def from_dict(cls, d: dict) -> "Bgp":
        return cls(**{k: d[k] for k in d if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Access lists / NAT
# ---------------------------------------------------------------------------

@dataclass
class AclEntry:
    seq: int
    action: str                         # permit | deny | remark
    protocol: str = "ip"
    src: str = "any"
    src_port: Optional[list] = None     # ["eq", 80] | ["range", 1000, 2000]
    dst: str = "any"
    dst_port: Optional[list] = None
    established: bool = False
    log: bool = False
    remark: str = ""

    def validate(self, kind: str) -> None:
        _check(isinstance(self.seq, int) and 1 <= self.seq <= 2147483647, "bad ACL sequence number")
        _check(self.action in ("permit", "deny", "remark"), "bad ACL action")
        if self.action == "remark":
            check_text(self.remark, "remark")
            return
        _check(self.protocol in PROTOCOLS, f"unsupported protocol {self.protocol!r}")
        self.src = check_addr_spec(self.src)
        self.dst = check_addr_spec(self.dst)
        if kind == "standard":
            _check(self.protocol == "ip" and self.dst == "any" and not self.src_port and not self.dst_port,
                   "standard ACLs match source address only")
        for port in (self.src_port, self.dst_port):
            if port:
                _check(self.protocol in ("tcp", "udp"), "port matching requires tcp or udp")
                _check(port[0] in PORT_OPS, "bad port operator")
                for p_ in port[1:]:
                    check_port(p_)
                _check(len(port) == (3 if port[0] == "range" else 2), "bad port specification")
        _check(not self.established or self.protocol == "tcp", "'established' requires tcp")

    @classmethod
    def from_dict(cls, d: dict) -> "AclEntry":
        return cls(**{k: d[k] for k in d if k in cls.__dataclass_fields__})


@dataclass
class Acl:
    name: str
    kind: str                           # standard | extended
    entries: list = field(default_factory=list)

    def next_seq(self) -> int:
        return (max((e.seq for e in self.entries), default=0) // 10 + 1) * 10

    def add(self, entry: AclEntry) -> None:
        self.entries = [e for e in self.entries if e.seq != entry.seq] + [entry]
        self.entries.sort(key=lambda e: e.seq)

    def validate(self) -> None:
        check_name(self.name, "access-list name")
        _check(self.kind in ("standard", "extended"), "bad ACL type")
        for e in self.entries:
            e.validate(self.kind)

    @classmethod
    def from_dict(cls, d: dict) -> "Acl":
        return cls(name=d["name"], kind=d["kind"], entries=[AclEntry.from_dict(e) for e in d.get("entries", [])])


@dataclass
class NatPool:
    name: str
    start: str
    end: str
    prefixlen: int

    def addresses(self) -> list:
        s, e = ipaddress.IPv4Address(self.start), ipaddress.IPv4Address(self.end)
        return [str(ipaddress.IPv4Address(i)) for i in range(int(s), int(e) + 1)]

    def validate(self) -> None:
        check_name(self.name, "NAT pool name")
        check_v4(self.start)
        check_v4(self.end)
        _check(0 < int(ipaddress.IPv4Address(self.end)) - int(ipaddress.IPv4Address(self.start)) + 1 <= 256,
               "NAT pool must contain 1-256 addresses (start <= end)")
        _check(0 < int(self.prefixlen) <= 32, "bad NAT pool netmask")

    @classmethod
    def from_dict(cls, d: dict) -> "NatPool":
        return cls(**{k: d[k] for k in d if k in cls.__dataclass_fields__})


@dataclass
class NatRule:
    kind: str                           # dynamic | static | static_port
    acl: Optional[str] = None           # dynamic
    interface: Optional[str] = None     # dynamic overload / static_port on interface address
    pool: Optional[str] = None          # dynamic
    overload: bool = False
    local: Optional[str] = None         # static / static_port inside local address
    global_ip: Optional[str] = None     # static / static_port inside global address
    protocol: Optional[str] = None      # static_port
    local_port: Optional[int] = None
    global_port: Optional[int] = None

    def key(self) -> tuple:
        if self.kind == "dynamic":
            return ("dynamic", self.acl)
        if self.kind == "static":
            return ("static", self.local)
        return ("static_port", self.protocol, self.global_ip or self.interface, self.global_port)

    def validate(self) -> None:
        _check(self.kind in ("dynamic", "static", "static_port"), "bad NAT rule type")
        if self.kind == "dynamic":
            check_name(self.acl, "access-list name")
            _check((self.interface is None) != (self.pool is None), "dynamic NAT needs exactly one of interface/pool")
            if self.interface:
                check_iface(self.interface)
            if self.pool:
                check_name(self.pool, "NAT pool name")
        else:
            check_v4(self.local)
            if self.kind == "static":
                check_v4(self.global_ip)
            else:
                _check(self.protocol in ("tcp", "udp"), "static port NAT protocol must be tcp or udp")
                check_port(self.local_port)
                check_port(self.global_port)
                _check((self.interface is None) != (self.global_ip is None),
                       "static port NAT needs exactly one of global address/interface")
                if self.interface:
                    check_iface(self.interface)
                else:
                    check_v4(self.global_ip)

    @classmethod
    def from_dict(cls, d: dict) -> "NatRule":
        return cls(**{k: d[k] for k in d if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# DHCP
# ---------------------------------------------------------------------------

@dataclass
class DhcpPool:
    name: str
    network: Optional[str] = None
    default_routers: list = field(default_factory=list)
    dns_servers: list = field(default_factory=list)
    domain_name: Optional[str] = None
    lease_seconds: int = 86400          # 0 = infinite
    bindings: list = field(default_factory=list)  # [{"mac": "..", "ip": "..", "hostname": ".."}]

    def validate(self) -> None:
        check_name(self.name, "DHCP pool name")
        if self.network:
            self.network = check_net(self.network, 4)
        self.default_routers = [check_v4(a) for a in self.default_routers]
        self.dns_servers = [check_v4(a) for a in self.dns_servers]
        if self.domain_name:
            _check(bool(DOMAIN_RE.match(self.domain_name)), "invalid domain-name")
        _check(isinstance(self.lease_seconds, int) and (self.lease_seconds == 0 or self.lease_seconds >= 120),
               "lease must be infinite or >= 2 minutes")
        for b in self.bindings:
            _check(bool(MAC_RE.match(b.get("mac", ""))), f"invalid MAC address {b.get('mac')!r}")
            check_v4(b["ip"])
            if b.get("hostname"):
                _check(bool(HOSTNAME_RE.match(b["hostname"])), "invalid binding hostname")
            if self.network:
                _check(ipaddress.IPv4Address(b["ip"]) in ipaddress.IPv4Network(self.network),
                       f"binding {b['ip']} is outside pool network {self.network}")

    @classmethod
    def from_dict(cls, d: dict) -> "DhcpPool":
        return cls(**{k: d[k] for k in d if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Top level
# ---------------------------------------------------------------------------

@dataclass
class RouterConfig:
    hostname: str = "router"
    domain_name: Optional[str] = None
    enable_secret: Optional[str] = None
    banner_motd: str = ""
    ip_routing: bool = True
    ipv6_routing: bool = False
    dns_server: bool = True
    name_servers: list = field(default_factory=list)
    hosts: dict = field(default_factory=dict)             # name -> ip
    ntp_servers: list = field(default_factory=list)
    interfaces: dict = field(default_factory=dict)        # name -> Interface
    routes: list = field(default_factory=list)            # [StaticRoute]
    acls: dict = field(default_factory=dict)              # name -> Acl
    nat_pools: dict = field(default_factory=dict)         # name -> NatPool
    nat_rules: list = field(default_factory=list)         # [NatRule]
    dhcp_pools: dict = field(default_factory=dict)        # name -> DhcpPool
    dhcp_excluded: list = field(default_factory=list)     # [[start, end]]
    ospf: Optional[Ospf] = None
    rip: Optional[Rip] = None
    bgp: Optional[Bgp] = None

    # -- helpers ----------------------------------------------------------
    def interface(self, name: str, create: bool = True) -> Optional[Interface]:
        if name not in self.interfaces and create:
            self.interfaces[name] = Interface(name=name)
        return self.interfaces.get(name)

    def sorted_interfaces(self) -> list:
        def key(i):
            m = re.match(r"^([a-z]+)([0-9]+)(?:\.([0-9]+))?$", i.name)
            return (m.group(1), int(m.group(2)), int(m.group(3) or -1)) if m else (i.name, 0, 0)
        return sorted(self.interfaces.values(), key=key)

    def outside_interfaces(self) -> list:
        return [i.name for i in self.sorted_interfaces() if i.nat == "outside"]

    def inside_interfaces(self) -> list:
        return [i.name for i in self.sorted_interfaces() if i.nat == "inside"]

    def dialers(self) -> list:
        return [i for i in self.sorted_interfaces() if i.is_dialer]

    def pppoe_ports(self, pool: Optional[int]) -> list:
        """Ethernet ports/VLANs with 'pppoe-client dial-pool-number <pool>'."""
        return [i for i in self.sorted_interfaces() if pool is not None and i.pppoe_pool == pool]

    def active_dialers(self) -> list:
        """Dialers that get a running ppp(8) process."""
        return [d for d in self.dialers() if not d.shutdown]

    # -- (de)serialisation -------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RouterConfig":
        if not isinstance(d, dict):
            raise ConfigError("configuration must be a JSON object")
        unknown = set(d) - set(cls.__dataclass_fields__)
        _check(not unknown, f"unknown configuration keys: {sorted(unknown)}")
        try:
            return cls(
                hostname=d.get("hostname", "router"),
                domain_name=d.get("domain_name"),
                enable_secret=d.get("enable_secret"),
                banner_motd=d.get("banner_motd", ""),
                ip_routing=bool(d.get("ip_routing", True)),
                ipv6_routing=bool(d.get("ipv6_routing", False)),
                dns_server=bool(d.get("dns_server", True)),
                name_servers=list(d.get("name_servers", [])),
                hosts=dict(d.get("hosts", {})),
                ntp_servers=list(d.get("ntp_servers", [])),
                interfaces={k: Interface.from_dict(v) for k, v in d.get("interfaces", {}).items()},
                routes=[StaticRoute.from_dict(r) for r in d.get("routes", [])],
                acls={k: Acl.from_dict(v) for k, v in d.get("acls", {}).items()},
                nat_pools={k: NatPool.from_dict(v) for k, v in d.get("nat_pools", {}).items()},
                nat_rules=[NatRule.from_dict(r) for r in d.get("nat_rules", [])],
                dhcp_pools={k: DhcpPool.from_dict(v) for k, v in d.get("dhcp_pools", {}).items()},
                dhcp_excluded=[list(x) for x in d.get("dhcp_excluded", [])],
                ospf=Ospf.from_dict(d["ospf"]) if d.get("ospf") else None,
                rip=Rip.from_dict(d["rip"]) if d.get("rip") else None,
                bgp=Bgp.from_dict(d["bgp"]) if d.get("bgp") else None,
            )
        except (KeyError, TypeError, AttributeError) as exc:
            raise ConfigError(f"malformed configuration: {exc}")

    def copy(self) -> "RouterConfig":
        return RouterConfig.from_dict(self.to_dict())

    # -- validation ----------------------------------------------------------
    def validate(self) -> list:
        """Validate everything. Raises ConfigError on the first hard error; returns warnings."""
        warnings = []
        _check(bool(HOSTNAME_RE.match(self.hostname)), f"invalid hostname {self.hostname!r}")
        if self.domain_name:
            _check(bool(DOMAIN_RE.match(self.domain_name)), "invalid domain-name")
        if self.enable_secret:
            _check(bool(SECRET_RE.match(self.enable_secret)), "malformed enable secret hash")
        if self.banner_motd:
            _check(len(self.banner_motd) < 4096 and "\x1b" not in self.banner_motd, "invalid banner")
        self.name_servers = [check_ip(a) for a in self.name_servers]
        for name, ip in self.hosts.items():
            _check(bool(DOMAIN_RE.match(name)), f"invalid host name {name!r}")
            self.hosts[name] = check_ip(ip)
        for s in self.ntp_servers:
            _check(bool(DOMAIN_RE.match(s)) or _is_ip(s), f"invalid NTP server {s!r}")

        for name, itf in self.interfaces.items():
            _check(name == itf.name, "interface key mismatch")
            itf.validate()
            for acl in (itf.acl_in, itf.acl_out):
                if acl and acl not in self.acls:
                    warnings.append(f"interface {name}: access-list {acl} is not defined (treated as permit any)")

        # Overlapping subnets across interfaces break routing.
        nets = []
        for itf in self.interfaces.values():
            for a in itf.ipv4:
                net = ipaddress.IPv4Interface(a).network
                for other_name, other in nets:
                    _check(not net.overlaps(other) or other_name == itf.name,
                           f"{a} on {itf.name} overlaps with {other} on {other_name}")
                nets.append((itf.name, net))

        seen = set()
        for r in self.routes:
            r.validate()
            _check(r.key() not in seen, f"duplicate route {r.prefix}")
            seen.add(r.key())
        _check(sum(1 for r in self.routes if r.is_default and r.version == 4) <= 1,
               "FreeBSD supports a single IPv4 default route (defaultrouter)")
        _check(sum(1 for r in self.routes if r.is_default and r.version == 6) <= 1,
               "FreeBSD supports a single IPv6 default route")

        for name, acl in self.acls.items():
            _check(name == acl.name, "ACL key mismatch")
            acl.validate()
        for name, pool in self.nat_pools.items():
            _check(name == pool.name, "NAT pool key mismatch")
            pool.validate()
        for rule in self.nat_rules:
            rule.validate()
            if rule.kind == "dynamic":
                _check(rule.acl in self.acls, f"NAT references undefined access-list {rule.acl}")
                if rule.pool:
                    _check(rule.pool in self.nat_pools, f"NAT references undefined pool {rule.pool}")
            if rule.interface:
                _check(rule.interface in self.interfaces, f"NAT references unknown interface {rule.interface}")
        if self.nat_rules and not self.outside_interfaces():
            warnings.append("NAT rules configured but no interface has 'ip nat outside'")

        warnings += self._validate_pppoe()

        for name, pool in self.dhcp_pools.items():
            _check(name == pool.name, "DHCP pool key mismatch")
            pool.validate()
            if pool.network and not any(
                    ipaddress.IPv4Interface(a).network.overlaps(ipaddress.IPv4Network(pool.network))
                    for i in self.interfaces.values() for a in i.ipv4):
                warnings.append(f"DHCP pool {name}: no interface has an address in {pool.network}")
        for rng in self.dhcp_excluded:
            _check(len(rng) == 2, "bad excluded-address range")
            check_v4(rng[0])
            check_v4(rng[1])
            _check(ipaddress.IPv4Address(rng[0]) <= ipaddress.IPv4Address(rng[1]), "excluded range start > end")

        for proto in (self.ospf, self.rip, self.bgp):
            if proto:
                proto.validate()
        return warnings


    def _validate_pppoe(self) -> list:
        warnings = []
        pools = {}
        for d in self.dialers():
            if d.dialer_pool is not None:
                _check(d.dialer_pool not in pools,
                       f"{d.name} and {pools.get(d.dialer_pool)} use the same dialer pool {d.dialer_pool}")
                pools[d.dialer_pool] = d.name
            if d.shutdown:
                continue
            missing = []
            if d.dialer_pool is None:
                missing.append("'dialer pool N'")
            elif not self.pppoe_ports(d.dialer_pool):
                missing.append(f"an Ethernet port with 'pppoe-client dial-pool-number {d.dialer_pool}'")
            if not d.negotiated:
                missing.append("'ip address negotiated'")
            if not d.ppp_username:
                missing.append("'ppp chap hostname USERNAME'")
            if not d.ppp_password:
                missing.append("'ppp chap password PASSWORD'")
            _check(not missing, f"{d.name} is incomplete, it needs " + ", ".join(missing)
                   + " (or 'shutdown' it)")
            for port in self.pppoe_ports(d.dialer_pool):
                _check(not port.shutdown, f"{d.name}: PPPoE port {port.name} is shut down")
            if d.ppp_dns and self.name_servers:
                warnings.append(f"{d.name}: 'ppp ipcp dns request' is ignored because 'ip name-server' is set")
            if not d.ppp_dns and not self.name_servers and self.dns_server:
                warnings.append(f"{d.name}: no upstream DNS - add 'ppp ipcp dns request' on {d.name} "
                                "or 'ip name-server ...'")
        for itf in self.interfaces.values():
            if itf.pppoe_pool is not None and itf.pppoe_pool not in pools:
                warnings.append(f"{itf.name}: no dialer interface has 'dialer pool {itf.pppoe_pool}'")
        for r in self.routes:
            if r.via_dialer:
                _check(r.interface in self.interfaces and self.interfaces[r.interface].is_dialer,
                       f"route {r.prefix}: interface {r.interface} is not configured")
        return warnings


def _is_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False
