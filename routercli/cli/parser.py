"""Declarative Cisco-style command grammar.

Commands are registered with a compact syntax string, e.g.

    "ip address <addr:ipv4> <mask:mask> [secondary]"
    "ip access-group <acl:acl> {dir:in|out}"
    "ip route <net:ipv4> <mask:mask> ( <nh:ipv4> | <ifname:iface> | Null0 )"

  word          keyword (abbreviations accepted, e.g. 'sh run', 'conf t')
  <name:type>   typed parameter (see cli/types.py)
  {name:a|b}    keyword choice captured into args[name]
  [ ... ]       optional group (may contain '|' alternatives)
  ( a | b c )   alternatives

Each syntax is expanded into flat variants; the matcher walks all variants
token by token with IOS semantics (exact keyword > unique prefix > parameter,
'% Ambiguous command' when a prefix matches several keywords).
"""

import ipaddress
import itertools
import re
from collections import defaultdict
from dataclasses import dataclass, field

from routercli.cli.types import make_type

_TOKEN_RE = re.compile(r"\{[^}]*\}|<[^>]*>|[\[\]()|]|[^\s\[\]()|]+")

KW_HELP = {
    "show": "Show running system information", "configure": "Enter configuration mode",
    "terminal": "Configure from the terminal / set terminal parameters", "interface": "Select an interface to configure",
    "ip": "Global IP configuration subcommands", "ipv6": "IPv6 configuration subcommands",
    "no": "Negate a command or set its defaults", "do": "Run an exec command from config mode",
    "exit": "Exit from the current mode", "end": "Exit to privileged EXEC mode", "commit": "Apply the candidate configuration",
    "abort": "Discard uncommitted changes and exit to EXEC", "enable": "Turn on privileged commands",
    "disable": "Turn off privileged commands", "ping": "Send echo messages", "traceroute": "Trace route to destination",
    "write": "Write running configuration to memory or terminal", "copy": "Copy configuration",
    "reload": "Reboot the router", "clear": "Reset functions", "rollback": "Roll back to an archived configuration",
    "route": "Establish static routes", "nat": "NAT configuration commands", "dhcp": "Configure DHCP server",
    "access-list": "Add an access list entry", "access-group": "Apply an access list to the interface",
    "address": "Set the IP address of an interface", "description": "Interface specific description",
    "shutdown": "Shutdown the selected interface", "hostname": "Set system's network name",
    "router": "Enable a routing process", "network": "Specify a network", "running-config": "Current operating configuration",
    "startup-config": "Contents of startup configuration", "secondary": "Make this IP address a secondary address",
    "overload": "Overload an address translation (PAT)", "permit": "Specify packets to forward",
    "deny": "Specify packets to reject", "remark": "Access list entry comment", "any": "Any address",
    "host": "A single host address", "eq": "Match only packets on a given port number",
    "neq": "Match only packets not on a given port number", "lt": "Match only packets with a lower port number",
    "gt": "Match only packets with a greater port number", "range": "Match only packets in the range of port numbers",
    "established": "Match established connections", "log": "Log matches against this entry",
    "inside": "Inside interface for address translation", "outside": "Outside interface for address translation",
    "source": "Source address translation", "static": "Specify static local->global mapping",
    "list": "Specify access list describing local addresses", "pool": "Name pool of addresses",
    "Null0": "Null interface (discard)", "encapsulation": "Set encapsulation type for an interface",
    "dot1Q": "IEEE 802.1Q Virtual LAN", "mtu": "Set the interface Maximum Transmission Unit (MTU)",
    "redistribute": "Redistribute information from another routing protocol",
    "passive-interface": "Suppress routing updates on an interface", "neighbor": "Specify a neighbor router",
    "router-id": "Router-id for this routing process", "area": "OSPF area ID", "version": "Set routing protocol version",
    "default-router": "Default routers", "dns-server": "DNS servers", "domain-name": "Domain name",
    "lease": "Address lease time", "static-bind": "Static MAC to IP binding", "excluded-address": "Prevent DHCP from assigning certain addresses",
    "in": "Inbound packets", "out": "Outbound packets", "dhcp-client": "DHCP client",
    "dns": "Configure DNS server for a zone", "domain": "IP DNS Resolver",
}

SHOW_HELP = {
    "version": "System hardware and software status", "clock": "Display the system clock",
    "history": "Display the session command history", "users": "Display information about terminal lines",
    "interfaces": "Interface status and configuration", "ip": "IP information", "ipv6": "IPv6 information",
    "interface": "IP interface status and configuration", "brief": "Brief summary of IP status and configuration",
    "route": "IP routing table", "arp": "ARP table", "ntp": "Network time protocol",
    "hosts": "IP domain-name, lookup style, nameservers, and host table", "vlans": "VLAN subinterfaces",
    "protocols": "IP routing protocol process parameters and statistics", "running-config": "Current operating configuration",
    "startup-config": "Contents of startup configuration", "candidate-config": "Uncommitted candidate configuration",
    "configuration": "Configuration change information", "archive": "Archived configurations (commit history)",
    "access-lists": "List access lists", "pf": "Packet filter (pf) internals", "nat": "IP NAT information",
    "translations": "Translation entries", "statistics": "Translation statistics", "dhcp": "Show items in the DHCP database",
    "binding": "DHCP address bindings", "pool": "DHCP pools information", "ospf": "OSPF information",
    "bgp": "BGP information", "rip": "RIP information", "summary": "Summarized neighbor status",
    "logging": "Show the contents of logging buffers", "debug-state": "Internal session state",
    "privilege": "Show current privilege level",
}


class ParseError(Exception):
    def __init__(self, msg: str, pos: int = -1):
        super().__init__(msg)
        self.pos = pos


@dataclass(frozen=True)
class Kw:
    word: str


@dataclass(frozen=True)
class Choice:
    name: str
    options: tuple


@dataclass(frozen=True)
class Param:
    name: str
    ptype: object


@dataclass
class CommandDef:
    syntax: str
    handler: object
    help: str
    negatable: bool
    positive: bool
    variants: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Syntax compilation
# ---------------------------------------------------------------------------

def _parse_alts(toks, i, closer):
    alts, seq = [], []
    while i < len(toks):
        t = toks[i]
        if t == closer:
            alts.append(seq)
            return alts, i + 1
        if t == "|":
            alts.append(seq)
            seq = []
            i += 1
            continue
        if t in "[(":
            inner, i = _parse_alts(toks, i + 1, "]" if t == "[" else ")")
            if t == "[":
                inner = inner + [[]]
            seq.append(("alt", inner))
            continue
        seq.append(("el", _element(t)))
        i += 1
    if closer is not None:
        raise ValueError("unbalanced brackets in syntax")
    alts.append(seq)
    return alts, i


def _element(t):
    if t.startswith("<"):
        name, _, spec = t[1:-1].partition(":")
        return Param(name, make_type(spec or "word"))
    if t.startswith("{"):
        name, _, opts = t[1:-1].partition(":")
        return Choice(name, tuple(opts.split("|")))
    return Kw(t)


def _expand(seq):
    parts = []
    for kind, val in seq:
        if kind == "el":
            parts.append([[val]])
        else:
            parts.append([v for alt in val for v in _expand(alt)])
    return [list(itertools.chain.from_iterable(p)) for p in itertools.product(*parts)]


def compile_syntax(syntax: str) -> list:
    alts, _ = _parse_alts(_TOKEN_RE.findall(syntax), 0, None)
    variants = [v for alt in alts for v in _expand(alt)]
    for v in variants:
        for el in v[:-1]:
            if isinstance(el, Param) and el.ptype.greedy:
                raise ValueError(f"greedy parameter must be last: {syntax}")
    return variants


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class Registry:
    def __init__(self):
        self.by_mode = defaultdict(list)          # mode -> [(CommandDef, variant)]

    def command(self, syntax, modes, help="", no=False, no_only=False):
        def deco(fn):
            cd = CommandDef(syntax, fn, help, negatable=no or no_only, positive=not no_only)
            cd.variants = compile_syntax(syntax)
            for m in modes:
                self.by_mode[m].extend((cd, v) for v in cd.variants)
            return fn
        return deco

    def entries(self, mode, negated=False):
        return [(cd, v) for cd, v in self.by_mode.get(mode, [])
                if (cd.negatable if negated else cd.positive)]


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _convert(param: Param, tok: str, session):
    try:
        return True, param.ptype.convert(tok, session)
    except (ValueError, AssertionError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
        return False, None


def _step(states, tok, idx, session):
    """Advance all states by one token. Returns new states or raises ParseError."""
    kw_hits = defaultdict(list)
    param_hits = []
    greedy_hits = []
    low = tok.lower()
    for cd, var, args, greedy in states:
        if greedy:
            args = dict(args)
            args[greedy] += " " + tok
            greedy_hits.append((cd, var, args, greedy))
            continue
        if idx >= len(var):
            continue
        el = var[idx]
        if isinstance(el, Kw):
            if el.word.lower().startswith(low):
                kw_hits[el.word].append((cd, var, args if el.word in args else {**args, el.word: True}, None))
        elif isinstance(el, Choice):
            for opt in el.options:
                if opt.lower().startswith(low):
                    kw_hits[opt].append((cd, var, {**args, el.name: opt}, None))
        else:
            ok, val = _convert(el, tok, session)
            if ok:
                if el.ptype.greedy:
                    param_hits.append((cd, var, {**args, el.name: tok}, el.name))
                else:
                    param_hits.append((cd, var, {**args, el.name: val}, None))
    exact = [k for k in kw_hits if k.lower() == low]
    if exact:
        chosen = kw_hits[exact[0]]
    elif len(kw_hits) == 1 and not param_hits:
        chosen = next(iter(kw_hits.values()))
    elif len(kw_hits) == 1:
        chosen = next(iter(kw_hits.values())) + param_hits
    elif len(kw_hits) > 1:
        raise ParseError(f'% Ambiguous command:  "{tok}"', idx)
    else:
        chosen = param_hits
    chosen = chosen + greedy_hits
    if not chosen:
        raise ParseError("% Invalid input detected at '^' marker.", idx)
    return chosen


def match(entries, tokens, session):
    """Return (CommandDef, args) for the full token list or raise ParseError."""
    states = [(cd, v, {}, None) for cd, v in entries]
    for idx, tok in enumerate(tokens):
        states = _step(states, tok, idx, session)
    done = [(cd, args) for cd, var, args, greedy in states if greedy or len(var) == len(tokens)]
    if not done:
        raise ParseError("% Incomplete command.", len(tokens))
    return done[0]


def options(entries, tokens, session):
    """Possible next tokens: list of (word, help, is_keyword) and whether <cr> is acceptable."""
    states = [(cd, v, {}, None) for cd, v in entries]
    try:
        for idx, tok in enumerate(tokens):
            states = _step(states, tok, idx, session)
    except ParseError:
        return [], False
    pos = len(tokens)
    out, seen, cr = [], set(), False
    for cd, var, args, greedy in states:
        if greedy:
            cr = True
            if ("LINE", "") not in seen:
                seen.add(("LINE", ""))
                out.append(("LINE", "Text", False))
            continue
        if pos == len(var):
            cr = True
            continue
        el = var[pos]
        if isinstance(el, Kw):
            last_kw = not any(isinstance(e, Kw) for e in var[pos + 1:])
            if pos == 0 or (last_kw and el.word not in KW_HELP and el.word not in SHOW_HELP):
                h = cd.help if pos == 0 and el.word not in KW_HELP else KW_HELP.get(el.word, cd.help)
            elif isinstance(var[0], Kw) and var[0].word == "show":
                h = SHOW_HELP.get(el.word, cd.help if last_kw else "")
            else:
                h = KW_HELP.get(el.word, "")
            items = [(el.word, h, True)]
        elif isinstance(el, Choice):
            items = [(o, KW_HELP.get(o, ""), True) for o in el.options]
        else:
            items = [(el.ptype.placeholder, el.ptype.help or el.name.replace("_", " "), False)]
            items += [(c, "", True) for c in el.ptype.complete(session)]
        for w, h, k in items:
            if (w, k) not in seen:
                seen.add((w, k))
                out.append((w, h, k))
    return out, cr
