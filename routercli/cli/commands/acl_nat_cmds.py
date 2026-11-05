"""Access lists (numbered + named standard/extended) and NAT (dynamic PAT, static NAT, port forwarding)."""

import ipaddress

from routercli.cli import modes as M
from routercli.cli.commands import registry as R
from routercli.config.model import Acl, AclEntry, NatPool, NatRule, check_text

CF = [M.CONFIG]
PROTO = "{proto:ip|tcp|udp|icmp|gre|esp|ah|ospf}"


def _addr(p):
    return f"( any | host <{p}host:ipv4> | <{p}net:ipv4> <{p}wild:wildcard> )"


def _port(p):
    return f"[ ( {{{p}op:eq|neq|lt|gt}} <{p}port:port> | range <{p}lo:port> <{p}hi:port> ) ]"


STD_SRC = "( any | host <shost:ipv4> | <snet:ipv4> [<swild:wildcard>] )"
STD = f"{{action:permit|deny}} {STD_SRC} [log]"
EXT = f"{{action:permit|deny}} {PROTO} {_addr('s')} {_port('s')} {_addr('d')} {_port('d')} [established] [log]"


def _spec(a, p):
    if f"{p}host" in a:
        return f"{a[p + 'host']}/32"
    if f"{p}net" in a:
        plen = a.get(f"{p}wild", 32)
        return str(ipaddress.IPv4Network(f"{a[p + 'net']}/{plen}", strict=False))
    return "any"


def _ports(a, p):
    if f"{p}op" in a:
        return [a[f"{p}op"], a[f"{p}port"]]
    if f"{p}lo" in a:
        if a[f"{p}lo"] > a[f"{p}hi"]:
            raise ValueError("port range start is greater than end")
        return ["range", a[f"{p}lo"], a[f"{p}hi"]]
    return None


def _entry(a, kind, seq) -> AclEntry:
    e = AclEntry(seq=seq, action=a["action"], log=bool(a.get("log")))
    e.src = _spec(a, "s")
    if kind == "extended":
        e.protocol = a["proto"]
        e.dst = _spec(a, "d")
        e.src_port = _ports(a, "s")
        e.dst_port = _ports(a, "d")
        e.established = bool(a.get("established"))
    e.validate(kind)
    return e


def _get_acl(s, name, kind) -> Acl:
    acl = s.cfg.acls.get(name)
    if acl is None:
        acl = s.cfg.acls[name] = Acl(name=name, kind=kind)
    elif acl.kind != kind:
        s.error(f"% Access-list {name} exists as a {acl.kind} list")
    return acl


# ---- numbered ACLs ----------------------------------------------------------------

@R.command(f"access-list <num:aclnum(std)> {STD}", CF, help="Add a standard access list entry")
def acl_std(s, a, neg):
    acl = _get_acl(s, a["num"], "standard")
    acl.add(_entry(a, "standard", acl.next_seq()))


@R.command(f"access-list <num:aclnum(ext)> {EXT}", CF, help="Add an extended access list entry")
def acl_ext(s, a, neg):
    acl = _get_acl(s, a["num"], "extended")
    try:
        acl.add(_entry(a, "extended", acl.next_seq()))
    except ValueError as exc:
        s.error(f"% {exc}")


@R.command("access-list ( <num:aclnum(std)> | <num:aclnum(ext)> ) remark <text:line>", CF,
           help="Access list entry comment")
def acl_remark(s, a, neg):
    kind = "standard" if int(a["num"]) < 100 or 1300 <= int(a["num"]) <= 1999 else "extended"
    acl = _get_acl(s, a["num"], kind)
    acl.add(AclEntry(seq=acl.next_seq(), action="remark", remark=check_text(a["text"], "remark")))


@R.command("access-list <num:acl>", CF, no_only=True)
def no_acl(s, a, neg):
    if s.cfg.acls.pop(a["num"], None) is None:
        s.error(f"% Access-list {a['num']} does not exist")


# ---- named ACLs ---------------------------------------------------------------------

@R.command("ip access-list {kind:standard|extended} <name:acl>", CF, help="Named access list", no=True)
def named_acl(s, a, neg):
    if neg:
        s.cfg.acls.pop(a["name"], None)
        return
    _get_acl(s, a["name"], a["kind"])
    s.push(M.ACL_STD if a["kind"] == "standard" else M.ACL_EXT, a["name"])


def _named_add(s, a, kind):
    acl = s.cfg.acls[s.context]
    seq = a.get("seq") or acl.next_seq()
    try:
        acl.add(_entry(a, kind, seq))
    except ValueError as exc:
        s.error(f"% {exc}")


@R.command(f"[<seq:int(1-2147483647)>] {STD}", [M.ACL_STD], help="Standard access list entry")
def named_std_entry(s, a, neg):
    _named_add(s, a, "standard")


@R.command(f"[<seq:int(1-2147483647)>] {EXT}", [M.ACL_EXT], help="Extended access list entry")
def named_ext_entry(s, a, neg):
    _named_add(s, a, "extended")


@R.command("[<seq:int(1-2147483647)>] remark <text:line>", [M.ACL_STD, M.ACL_EXT], help="Access list entry comment")
def named_remark(s, a, neg):
    acl = s.cfg.acls[s.context]
    acl.add(AclEntry(seq=a.get("seq") or acl.next_seq(), action="remark", remark=check_text(a["text"], "remark")))


@R.command("<seq:int(1-2147483647)>", [M.ACL_STD, M.ACL_EXT], no_only=True)
def named_no_seq(s, a, neg):
    acl = s.cfg.acls[s.context]
    before = len(acl.entries)
    acl.entries = [e for e in acl.entries if e.seq != a["seq"]]
    if len(acl.entries) == before:
        s.error(f"% Sequence {a['seq']} not found")


# ---- NAT ------------------------------------------------------------------------------

def _set_nat(s, rule: NatRule, neg):
    rule.validate()
    rules = [r for r in s.cfg.nat_rules if r.key() != rule.key()]
    if neg:
        if len(rules) == len(s.cfg.nat_rules):
            s.error("% Translation not found")
    else:
        if rule.kind == "dynamic" and rule.acl not in s.cfg.acls:
            s.print(f"% Warning: access-list {rule.acl} is not defined yet")
        rules.append(rule)
    s.cfg.nat_rules = rules


@R.command("ip nat inside source list <acl:acl> ( interface <ifname:iface> | pool <pool:natpool> ) [overload]", CF,
           help="Dynamic source translation (PAT with 'overload')", no=True)
def nat_dynamic(s, a, neg):
    _set_nat(s, NatRule(kind="dynamic", acl=a["acl"], interface=a.get("ifname"), pool=a.get("pool"),
                        overload=bool(a.get("overload"))), neg)


@R.command("ip nat inside source static <local:ipv4> <glob:ipv4>", CF, help="Static 1:1 NAT (pf binat)", no=True)
def nat_static(s, a, neg):
    _set_nat(s, NatRule(kind="static", local=a["local"], global_ip=a["glob"]), neg)


@R.command("ip nat inside source static {proto:tcp|udp} <local:ipv4> <lport:port> "
           "( <glob:ipv4> | interface <ifname:iface> ) <gport:port>", CF,
           help="Static port translation / port forward (pf rdr)", no=True)
def nat_static_port(s, a, neg):
    _set_nat(s, NatRule(kind="static_port", protocol=a["proto"], local=a["local"], local_port=a["lport"],
                        global_ip=a.get("glob"), interface=a.get("ifname"), global_port=a["gport"]), neg)


@R.command("ip nat pool <name:natpool> <start:ipv4> <end:ipv4> ( netmask <mask:mask> | prefix-length "
           "<plen:int(1-32)> )", CF, help="Define pool of addresses", no=True)
def nat_pool(s, a, neg):
    if neg:
        s.cfg.nat_pools.pop(a["name"], None)
        return
    pool = NatPool(name=a["name"], start=a["start"], end=a["end"], prefixlen=a.get("mask", a.get("plen")))
    pool.validate()
    s.cfg.nat_pools[a["name"]] = pool


@R.command("ip nat pool <name:natpool>", CF, no_only=True)
def no_nat_pool(s, a, neg):
    if any(r.pool == a["name"] for r in s.cfg.nat_rules):
        s.error(f"% Pool {a['name']} is in use by a NAT rule")
    s.cfg.nat_pools.pop(a["name"], None)
