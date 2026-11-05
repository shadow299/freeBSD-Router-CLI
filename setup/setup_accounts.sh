#!/bin/sh
#
# setup_accounts.sh - system account separation for the router appliance.
#
#   root   : full FreeBSD shell (/bin/sh); console and SSH login with
#            password (and/or SSH public key if ROOT_PUBKEY is given).
#   router : management account; login shell = /usr/local/bin/router-cli.
#            Never sees a sh/csh prompt (console, SSH or su). Can only reach
#            root privileges through 'doas router-apply' (config backend).
#
# Usage (as root, after install.sh):
#   sh setup/setup_accounts.sh
#   ROUTER_PUBKEY=/path/id_ed25519.pub ROOT_PUBKEY=/path/admin.pub sh setup/setup_accounts.sh
#   FORCE_INIT=1 sh setup/setup_accounts.sh     # re-initialise the router config store
#
set -eu

die() { echo "setup_accounts.sh: $*" >&2; exit 1; }
msg() { echo "==> $*"; }

[ "$(id -u)" -eq 0 ] || die "must be run as root"
CLI=/usr/local/bin/router-cli
HELPER=/usr/local/libexec/router-apply
[ -x "$CLI" ] && [ -x "$HELPER" ] || die "run setup/install.sh first"
PY=$(head -1 "$CLI" | sed 's/^#!//; s/ .*//')

read_secret() {     # read_secret PROMPT VAR   (no echo, asked twice)
    while :; do
        stty -echo; printf '%s: ' "$1"; read -r _s1; printf '\n'
        printf 'Confirm %s: ' "$1"; read -r _s2; printf '\n'; stty echo
        if [ -z "$_s1" ]; then echo "  empty, try again"; continue; fi
        if [ "$_s1" != "$_s2" ]; then echo "  mismatch, try again"; continue; fi
        if [ "${#_s1}" -lt 8 ]; then echo "  must be at least 8 characters"; continue; fi
        eval "$2=\$_s1"; unset _s1 _s2; return 0
    done
}
trap 'stty echo 2>/dev/null || true' EXIT INT TERM

install_key() {     # install_key USER HOMEDIR KEYFILE
    [ -f "$3" ] || die "key file $3 not found"
    ssh-keygen -l -f "$3" >/dev/null 2>&1 || die "$3 is not a valid public key"
    install -d -o root -g "$(id -gn "$1")" -m 0750 "$2/.ssh"
    install -o root -g "$(id -gn "$1")" -m 0640 "$3" "$2/.ssh/authorized_keys"
}

# --------------------------------------------------------------------------
# 1. root: standard shell, never the CLI
# --------------------------------------------------------------------------
msg "Configuring root account"
rootshell=$(pw usershow root | cut -d: -f10)
case "$rootshell" in
    /bin/sh|/bin/csh|/bin/tcsh) ;;
    *) pw usermod root -s /bin/sh; echo "  root shell reset to /bin/sh" ;;
esac
if [ -n "${ROOT_PUBKEY:-}" ]; then
    install -d -o root -g wheel -m 0700 /root/.ssh
    install -o root -g wheel -m 0600 "$ROOT_PUBKEY" /root/.ssh/authorized_keys
    echo "  installed root SSH key from $ROOT_PUBKEY"
fi
# root logs in with its password (console and SSH) and/or an SSH key.
root_has_password() {
    case "$(pw usershow root | cut -d: -f2)" in ""|"*"|"*LOCKED*"*) return 1;; esac
}
if ! root_has_password || [ "${SET_ROOT_PW:-}" = 1 ]; then
    echo "  set the root password (console and SSH login):"
    until passwd root && root_has_password; do echo "  try again"; done
fi
pw unlock root >/dev/null 2>&1 || true

# --------------------------------------------------------------------------
# 2. router: restricted management account
# --------------------------------------------------------------------------
msg "Configuring 'router' account"
grep -qxF "$CLI" /etc/shells || echo "$CLI" >> /etc/shells
pw groupshow router >/dev/null 2>&1 || pw groupadd router
if pw usershow router >/dev/null 2>&1; then
    pw usermod router -g router -G '' -s "$CLI" -d /home/router -c "Router management CLI"
else
    pw useradd router -g router -s "$CLI" -d /home/router -c "Router management CLI"
fi
# Home is owned by root so the account cannot plant dotfiles / alter its own keys
# (it has no shell anyway). Only the CLI history file is writable.
install -d -o root -g router -m 0750 /home/router
touch /home/router/.hushlogin /home/router/.router_cli_history
chown root:router /home/router/.hushlogin
chown router:router /home/router/.router_cli_history
chmod 0600 /home/router/.router_cli_history
# 'router' must never be in wheel (FreeBSD su(1) requires wheel)
pw groupmod wheel -d router >/dev/null 2>&1 || true

if [ -n "${ROUTER_PUBKEY:-}" ]; then
    install_key router /home/router "$ROUTER_PUBKEY"
    echo "  installed router SSH key from $ROUTER_PUBKEY"
fi
if [ "$(pw usershow router | cut -d: -f2)" = "*" ] || [ "${SET_ROUTER_PW:-}" = 1 ]; then
    echo "  set the 'router' login password (SSH/console login to the CLI):"
    passwd router
fi

# --------------------------------------------------------------------------
# 3. enable secret + initial configuration store
# --------------------------------------------------------------------------
if [ -f /var/db/router-cli/running-config.json ] && [ -z "${FORCE_INIT:-}" ]; then
    msg "Configuration store already initialised (FORCE_INIT=1 to reset) - skipping enable secret"
else
    msg "Setting the privileged-EXEC 'enable secret'"
    read_secret "enable secret" ENABLE_SECRET
    host=$(hostname -s 2>/dev/null || echo router)
    # Secret goes via stdin (never argv / environment); hashed with scrypt.
    printf '%s' "$ENABLE_SECRET" | "$PY" -I -c '
import json, sys
sys.path.insert(0, "/usr/local/lib/router-cli")
from routercli.auth import hash_secret
from routercli.config.model import HOSTNAME_RE
host = sys.argv[1] if HOSTNAME_RE.match(sys.argv[1]) else "router"
print(json.dumps({"force": bool(sys.argv[2]),
                  "config": {"hostname": host, "enable_secret": hash_secret(sys.stdin.read())}}))
' "$host" "${FORCE_INIT:-}" | "$HELPER" init
    unset ENABLE_SECRET
fi

msg "Done."
echo "    root   : $(pw usershow root | cut -d: -f10)"
echo "    router : $(pw usershow router | cut -d: -f10)"
echo "  Test locally with:  su - router     (or log in on the console as 'router')"
echo "  Then run:           sh $(dirname "$0")/harden_sshd.sh"
