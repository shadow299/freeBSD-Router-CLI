"""Render the model as Cisco IOS-style text (show running-config)."""

import ipaddress

from routercli.auth import type7_encode
from routercli.config.model import AclEntry, RouterConfig

PORT_NAMES = {"www": 80, "http": 80, "https": 443, "ssh": 22, "telnet": 23, "ftp": 21, "ftp-data": 20,
              "smtp": 25, "domain": 53, "dns": 53, "bootps": 67, "bootpc": 68, "tftp": 69, "ntp": 123,
              "snmp": 161, "bgp": 179, "ldap": 389, "syslog": 514, "pop3": 110, "imap": 143, "rdp": 3389,
              "isakmp": 500, "non500-isakmp": 4500}


def wildcard(prefixlen: int) -> str:
    return str(ipaddress.IPv4Address((1 << (32 - prefixlen)) - 1))


def net_text(prefix: str, mask_style="mask") -> str:
    n = ipaddress.IPv4Network(prefix)
    return f"{n.network_address} {n.netmask if mask_style == 'mask' else wildcard(n.prefixlen)}"


def addr_text(spec: str) -> str:
    if spec == "any":
        return "any"
    n = ipaddress.IPv4Network(spec)
    if n.prefixlen == 32:
        return f"host {n.network_address}"
    return f"{n.network_address} {wildcard(n.prefixlen)}"


def port_text(spec) -> str:
    if not spec:
        return ""
    return " " + " ".join(str(x) for x in spec)


def acl_entry_text(e: AclEntry, kind: str) -> str:
    if e.action == "remark":
        return f"remark {e.remark}"
    if kind == "standard":
        return f"{e.action} {addr_text(e.src)}" + (" log" if e.log else "")
    s = f"{e.action} {e.protocol} {addr_text(e.src)}{port_text(e.src_port)} {addr_text(e.dst)}{port_text(e.dst_port)}"
    if e.established:
        s += " established"
    if e.log:
        s += " log"
    return s


def is_numbered(name: str) -> bool:
    return name.isdigit()


def _lease_text(seconds: int) -> str:
    if seconds == 0:
        return "lease infinite"
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return f"lease {d} {h} {m}" if m else (f"lease {d} {h}" if h else f"lease {d}")


def interface_text(itf) -> list:
    L = [f"interface {itf.name}"]
    if itf.description:
        L.append(f" description {itf.description}")
    if itf.is_vlan:
        L.append(f" encapsulation dot1Q {itf.vlan_id}")
    if itf.mtu:
        L.append(f" mtu {itf.mtu}")
    if not itf.offload:
        L.append(" no hardware-offload")
    if itf.dhcp_client:
        L.append(" ip address dhcp")
    elif itf.negotiated:
        L.append(" ip address negotiated")
    elif not itf.ipv4:
        L.append(" no ip address")
    for n, a in enumerate(itf.ipv4):
        i = ipaddress.IPv4Interface(a)
        L.append(f" ip address {i.ip} {i.netmask}" + (" secondary" if n else ""))
    for a in itf.ipv6:
        L.append(f" ipv6 address {a}")
    if itf.nat:
        L.append(f" ip nat {itf.nat}")
    if itf.acl_in:
        L.append(f" ip access-group {itf.acl_in} in")
    if itf.acl_out:
        L.append(f" ip access-group {itf.acl_out} out")
    if itf.pppoe_pool is not None:
        L.append(" pppoe enable group global")
        L.append(f" pppoe-client dial-pool-number {itf.pppoe_pool}"
                 + (f" service-name {itf.pppoe_service}" if itf.pppoe_service else ""))
    if itf.is_dialer:
        L.append(" encapsulation ppp")
        if itf.dialer_pool is not None:
            L.append(f" dialer pool {itf.dialer_pool}")
        if itf.ppp_username:
            L.append(f" ppp chap hostname {itf.ppp_username}")
        if itf.ppp_password:
            L.append(f" ppp chap password 7 {type7_encode(itf.ppp_password)}")
        if itf.ppp_username and itf.ppp_password:
            L.append(f" ppp pap sent-username {itf.ppp_username} password 7 {type7_encode(itf.ppp_password)}")
        if itf.ppp_dns:
            L.append(" ppp ipcp dns request")
    L.append(" shutdown" if itf.shutdown else " no shutdown")
    return L


def render(cfg: RouterConfig, header: str = "") -> str:
    L = ["Building configuration...", ""] if header else []
    if header:
        L += [header, "!"]
    L += ["version router-cli/FreeBSD", "!", f"hostname {cfg.hostname}", "!"]
    if cfg.enable_secret:
        L.append(f"enable secret 9 {cfg.enable_secret}")
    if cfg.banner_motd:
        L.append(f"banner motd ^{cfg.banner_motd}^")
    L.append("!")
    if not cfg.ip_routing:
        L.append("no ip routing")
    if cfg.ipv6_routing:
        L.append("ipv6 unicast-routing")
    if cfg.domain_name:
        L.append(f"ip domain-name {cfg.domain_name}")
    if cfg.name_servers:
        L.append("ip name-server " + " ".join(cfg.name_servers))
    L.append("ip dns server" if cfg.dns_server else "no ip dns server")
    for name, ip in sorted(cfg.hosts.items()):
        L.append(f"ip host {name} {ip}")
    for s in cfg.ntp_servers:
        L.append(f"ntp server {s}")
    L.append("!")
    for a, b in cfg.dhcp_excluded:
        L.append(f"ip dhcp excluded-address {a}" + (f" {b}" if b != a else ""))
    for pool in sorted(cfg.dhcp_pools.values(), key=lambda p: p.name):
        L.append("!")
        L.append(f"ip dhcp pool {pool.name}")
        if pool.network:
            L.append(f" network {net_text(pool.network)}")
        if pool.default_routers:
            L.append(" default-router " + " ".join(pool.default_routers))
        if pool.dns_servers:
            L.append(" dns-server " + " ".join(pool.dns_servers))
        if pool.domain_name:
            L.append(f" domain-name {pool.domain_name}")
        if pool.lease_seconds != 86400:
            L.append(" " + _lease_text(pool.lease_seconds))
        for b in pool.bindings:
            L.append(f" static-bind {b['mac']} {b['ip']}" + (f" {b['hostname']}" if b.get("hostname") else ""))
    L.append("!")
    for itf in sorted(cfg.sorted_interfaces(), key=lambda i: i.is_dialer):    # dialers last, like IOS
        L += interface_text(itf)
        L.append("!")
    if cfg.ospf:
        o = cfg.ospf
        L.append(f"router ospf {o.process_id}")
        if o.router_id:
            L.append(f" router-id {o.router_id}")
        if o.passive_default:
            L.append(" passive-interface default")
        for i in o.passive:
            L.append(f" passive-interface {i}")
        for n in o.networks:
            L.append(f" network {net_text(n['prefix'], 'wild')} area {n['area']}")
        for r in o.redistribute:
            L.append(f" redistribute {r} subnets")
        if o.default_originate:
            L.append(" default-information originate" + (" always" if o.default_originate_always else ""))
        L.append("!")
    if cfg.rip:
        r = cfg.rip
        L += ["router rip", f" version {r.version}"]
        for n in r.networks:
            L.append(f" network {ipaddress.IPv4Network(n).network_address}")
        for i in r.passive:
            L.append(f" passive-interface {i}")
        for x in r.redistribute:
            L.append(f" redistribute {x}")
        if r.default_originate:
            L.append(" default-information originate")
        L.append("!")
    if cfg.bgp:
        b = cfg.bgp
        L.append(f"router bgp {b.asn}")
        if b.router_id:
            L.append(f" bgp router-id {b.router_id}")
        for ip, nb in sorted(b.neighbors.items()):
            L.append(f" neighbor {ip} remote-as {nb['remote_as']}")
            if nb.get("description"):
                L.append(f" neighbor {ip} description {nb['description']}")
            if nb.get("shutdown"):
                L.append(f" neighbor {ip} shutdown")
        for n in b.networks:
            net = ipaddress.ip_network(n)
            L.append(f" network {net.network_address} mask {net.netmask}" if net.version == 4 else f" network {n}")
        for x in b.redistribute:
            L.append(f" redistribute {x}")
        L.append("!")
    for pool in sorted(cfg.nat_pools.values(), key=lambda p: p.name):
        mask = ipaddress.IPv4Network(f"0.0.0.0/{pool.prefixlen}").netmask
        L.append(f"ip nat pool {pool.name} {pool.start} {pool.end} netmask {mask}")
    for r in cfg.nat_rules:
        if r.kind == "dynamic":
            tgt = f"interface {r.interface}" if r.interface else f"pool {r.pool}"
            L.append(f"ip nat inside source list {r.acl} {tgt}" + (" overload" if r.overload else ""))
        elif r.kind == "static":
            L.append(f"ip nat inside source static {r.local} {r.global_ip}")
        else:
            g = f"interface {r.interface}" if r.interface else r.global_ip
            L.append(f"ip nat inside source static {r.protocol} {r.local} {r.local_port} {g} {r.global_port}")
    for r in cfg.routes:
        tgt = r.nexthop or r.interface or "Null0"
        if r.version == 4:
            L.append(f"ip route {net_text(r.prefix)} {tgt}")
        else:
            L.append(f"ipv6 route {r.prefix} {tgt}")
    L.append("!")
    for acl in sorted(cfg.acls.values(), key=lambda a: (not is_numbered(a.name), a.name.zfill(10))):
        if is_numbered(acl.name):
            for e in acl.entries:
                L.append(f"access-list {acl.name} {acl_entry_text(e, acl.kind)}")
        else:
            L.append(f"ip access-list {acl.kind} {acl.name}")
            for e in acl.entries:
                L.append(f" {e.seq} {acl_entry_text(e, acl.kind)}")
        L.append("!")
    L.append("end")
    return "\n".join(L) + "\n"
