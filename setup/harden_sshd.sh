#!/bin/sh
#
# harden_sshd.sh - apply setup/sshd_config.router-cli to /etc/ssh/sshd_config.
#
#   sh setup/harden_sshd.sh
#   (default) root may log in with its password or an SSH key
#   ROOT_LOGIN=prohibit-password sh setup/harden_sshd.sh  # root: SSH keys only
#   ROOT_LOGIN=no sh setup/harden_sshd.sh                 # root: console only
#   PASSWORD_AUTH=no sh setup/harden_sshd.sh              # keys only for every account
#   ALLOW_USERS="router root admin" sh setup/harden_sshd.sh
#
# Safe: the candidate file is validated with 'sshd -t' and the effective
# settings for 'router' are checked with 'sshd -T' before it is installed.
# The previous file is kept as sshd_config.bak.<timestamp>. Existing SSH
# sessions are not dropped by 'service sshd reload'.
#
set -eu

die() { echo "harden_sshd.sh: $*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || die "must be run as root"

SRC=$(cd "$(dirname "$0")" && pwd)
CONF=/etc/ssh/sshd_config
ROOT_LOGIN=${ROOT_LOGIN:-yes}
PASSWORD_AUTH=${PASSWORD_AUTH:-yes}
ALLOW_USERS=${ALLOW_USERS:-router root}
B_GLOBAL_BEGIN="# >>> router-cli global >>>"
B_GLOBAL_END="# <<< router-cli global <<<"
B_MATCH_BEGIN="# >>> router-cli match >>>"
B_MATCH_END="# <<< router-cli match <<<"

case "$ROOT_LOGIN" in yes|no|prohibit-password) ;; *) die "ROOT_LOGIN must be yes|no|prohibit-password";; esac
case "$PASSWORD_AUTH" in yes|no) ;; *) die "PASSWORD_AUTH must be yes|no";; esac
echo "$ALLOW_USERS" | grep -Eq '^[a-z_][a-z0-9_ -]*$' || die "invalid ALLOW_USERS"

if { [ "$ROOT_LOGIN" = prohibit-password ] || [ "$PASSWORD_AUTH" = no ]; } && [ ! -s /root/.ssh/authorized_keys ]; then
    echo "warning: root has no /root/.ssh/authorized_keys - root SSH login will be impossible" >&2
    echo "         (console access is unaffected). Set ROOT_PUBKEY in setup_accounts.sh." >&2
fi

if [ "$ROOT_LOGIN" = yes ] && [ "$PASSWORD_AUTH" = yes ]; then
    rootpw=$(pw usershow root 2>/dev/null | cut -d: -f2)
    case "$rootpw" in ""|"*"|"*LOCKED*"*) die "root has no password: run 'passwd root' first (or setup_accounts.sh)";; esac
fi

snippet=$(sed -e "s/@ROOT_LOGIN@/$ROOT_LOGIN/" -e "s/@PASSWORD_AUTH@/$PASSWORD_AUTH/g" \
              -e "s/@ALLOW_USERS@/$ALLOW_USERS/" "$SRC/sshd_config.router-cli")
global=$(printf '%s\n' "$snippet" | sed -n '/^# -* GLOBAL/,/^# -* MATCH/p' | sed '$d')
match=$(printf '%s\n' "$snippet" | sed -n '/^# -* MATCH/,$p')

new=$(mktemp /etc/ssh/sshd_config.XXXXXX)
trap 'rm -f "$new"' EXIT
{
    echo "$B_GLOBAL_BEGIN"
    printf '%s\n' "$global"
    echo "$B_GLOBAL_END"
    # previous content without our old blocks
    sed -e "/^$B_GLOBAL_BEGIN\$/,/^$B_GLOBAL_END\$/d" \
        -e "/^$B_MATCH_BEGIN\$/,/^$B_MATCH_END\$/d" "$CONF"
    echo "$B_MATCH_BEGIN"
    printf '%s\n' "$match"
    echo "$B_MATCH_END"
} > "$new"
chmod 0644 "$new"

sshd -t -f "$new" || die "new configuration failed 'sshd -t' - $CONF left unchanged"

# Verify the effective per-user settings before installing.
eff=$(sshd -T -f "$new" -C user=router,host=localhost,addr=127.0.0.1)
echo "$eff" | grep -qix 'forcecommand /usr/local/bin/router-cli' || die "ForceCommand not effective for router"
echo "$eff" | grep -qix 'allowtcpforwarding no' || die "AllowTcpForwarding not effective for router"
eff=$(sshd -T -f "$new" -C user=root,host=localhost,addr=127.0.0.1)
echo "$eff" | grep -qix "permitrootlogin $(echo "$ROOT_LOGIN" | sed 's/prohibit-password/without-password/')" || \
    echo "$eff" | grep -qix "permitrootlogin $ROOT_LOGIN" || die "PermitRootLogin not effective"
echo "$eff" | grep -qix "passwordauthentication $PASSWORD_AUTH" || die "PasswordAuthentication not effective for root"

cp -p "$CONF" "$CONF.bak.$(date +%Y%m%d%H%M%S)"
mv "$new" "$CONF"
trap - EXIT
sysrc -q sshd_enable=YES >/dev/null
if service sshd status >/dev/null 2>&1; then service sshd reload; else service sshd start; fi
echo "sshd hardened:"
echo "  root   -> PermitRootLogin $ROOT_LOGIN, PasswordAuthentication $PASSWORD_AUTH, normal shell (/bin/sh)"
echo "  router -> ForceCommand /usr/local/bin/router-cli (no forwarding, no sftp)"
echo "  AllowUsers $ALLOW_USERS"
echo "Keep this session open and test 'ssh router@<ip>' from another terminal."
