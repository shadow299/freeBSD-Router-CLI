"""Filesystem locations.

Setting ROUTERCLI_SANDBOX=/some/dir redirects every managed path below that
directory and turns service/kernel commands into a logged dry run. This is used
for development and testing on non-FreeBSD hosts.
"""

import os

SANDBOX = os.environ.get("ROUTERCLI_SANDBOX", "").rstrip("/")


def p(path: str) -> str:
    return SANDBOX + path if SANDBOX else path


RC_CONF = p("/etc/rc.conf")
PF_CONF = p("/etc/pf.conf")
DNSMASQ_CONF = p("/usr/local/etc/dnsmasq.conf")
FRR_CONF = p("/usr/local/etc/frr/frr.conf")
NTP_CONF = p("/etc/ntp.conf")
RESOLV_CONF = p("/etc/resolv.conf")
DNSMASQ_LEASES = p("/var/db/dnsmasq.leases")

STATE_DIR = p("/var/db/router-cli")
RUNNING = os.path.join(STATE_DIR, "running-config.json")
STARTUP = os.path.join(STATE_DIR, "startup-config.json")
ARCHIVE_DIR = os.path.join(STATE_DIR, "archive")
BACKUP_DIR = os.path.join(STATE_DIR, "backup")
PENDING_CONFIRM = os.path.join(STATE_DIR, "pending-confirm.json")
LOCK_FILE = os.path.join(STATE_DIR, ".lock")
COMMAND_LOG = os.path.join(STATE_DIR, "sandbox-commands.log")

ARCHIVE_MAX = 30

HELPER = "/usr/local/libexec/router-apply"
DOAS = "/usr/local/bin/doas"
