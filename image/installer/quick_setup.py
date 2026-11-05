"""Build the installer's quick-setup script for router-cli (used by postinstall.sh).

    QS_TYPE=dhcp|pppoe QS_HOST=gw1 QS_WAN=rge0 QS_LAN=re0 QS_LANIP=192.168.1.1 \
    [QS_VLAN=35 QS_USER=name@isp QS_PASS=secret] python3 -I quick_setup.py [check]

Values come from the environment so that nothing (ISP passwords!) goes through
shell quoting or sed. 'check' only validates QS_USER / QS_PASS / QS_VLAN and
prints a message on failure. The PPP password is written as a Cisco type 7 string
(hex digits only), so no character of it can upset the CLI's line parser.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))      # project root -> routercli

from routercli.auth import type7_encode                          # noqa: E402
from routercli.config.model import PPP_SECRET_RE, PPP_USER_RE    # noqa: E402


def _read(name: str) -> str:
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return f.read()


def check(user: str, password: str, vlan: str = "") -> str:
    """Return an error message, or '' if the values are usable."""
    if not PPP_USER_RE.match(user) or "?" in user:
        return "the user name must be 1-64 characters without spaces, '?' or a leading '!'"
    if not PPP_SECRET_RE.match(password):
        return "the password must be 1-64 characters, not starting with '!' or a space"
    if vlan and not (vlan.isdigit() and 1 <= int(vlan) <= 4094):
        return "the VLAN ID must be a number from 1 to 4094 (or empty)"
    return ""


def build(wan_type: str, host: str, wan: str, lan: str, lanip: str,
          vlan: str = "", user: str = "", password: str = "") -> str:
    values = {"HOST": host, "WAN": wan, "LAN": lan, "LANIP": lanip, "NET": lanip.rsplit(".", 1)[0]}
    if wan_type == "pppoe":
        err = check(user, password, vlan)
        if err:
            raise ValueError(err)
        block = _read("quick-setup-wan-pppoe.cli")
        values.update(PORT=f"{wan}.{vlan}" if vlan else wan, USER=user, PASS7=type7_encode(password),
                      NATIF="dialer1")
    elif wan_type == "dhcp":
        block = _read("quick-setup-wan-dhcp.cli")
        values["NATIF"] = wan
    else:
        raise ValueError(f"unknown WAN type {wan_type!r}")
    text = _read("quick-setup.cli").replace("!@WAN_CONFIG@\n", block)
    # One pass, so a value containing '@NAME@' is never substituted again.
    return re.sub(r"@([A-Z0-9]+)@", lambda m: values.get(m.group(1), m.group(0)), text)


def main(argv) -> int:
    env = os.environ
    if argv[1:] == ["check"]:
        err = check(env.get("QS_USER", ""), env.get("QS_PASS", ""), env.get("QS_VLAN", ""))
        if err:
            print(f"  {err}")
        return 1 if err else 0
    try:
        sys.stdout.write(build(env.get("QS_TYPE", "dhcp"), env["QS_HOST"], env["QS_WAN"], env["QS_LAN"],
                               env["QS_LANIP"], env.get("QS_VLAN", ""), env.get("QS_USER", ""),
                               env.get("QS_PASS", "")))
    except (KeyError, ValueError) as exc:
        print(f"quick_setup.py: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
