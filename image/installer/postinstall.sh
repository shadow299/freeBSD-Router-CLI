#!/bin/sh
#
# postinstall.sh - runs INSIDE the newly installed system (chroot), started by
# the installer hook local.post-configure. Usage: postinstall.sh <workdir>
#
#   <workdir>/router-cli/   this project
#   <workdir>/packages/     offline pkg repository (python, prompt_toolkit, dnsmasq, doas, frr)
#   <workdir>/build.env     settings recorded by build-image.sh (PYVER, NO_FRR, IMAGE_VERSION)
#
# 1. installs the packages + router-cli without network access
# 2. creates the 'router' account (password) and the CLI 'enable secret'
# 3. locks down sshd (router -> CLI only, root -> normal shell, password allowed)
# 4. optional quick setup: WAN (DHCP or PPPoE) + LAN (192.168.1.1/24, DHCP, DNS, NAT)
#
set -eu

WORK=${1:?usage: postinstall.sh <workdir>}
SHARE=/usr/local/share/router-cli
PYVER=""; NO_FRR=""; IMAGE_VERSION=""
[ -f "$WORK/build.env" ] && . "$WORK/build.env"

msg() { printf '\n==> %s\n' "$*"; }
ask() {     # ask "Question" default  -> $REPLY
    printf '%s [%s]: ' "$1" "$2"
    read -r REPLY || REPLY=""
    [ -n "$REPLY" ] || REPLY=$2
}
read_secret() {     # read_secret "Prompt" -> the typed text on stdout (not echoed)
    printf '%s: ' "$1" >&2
    stty -echo 2>/dev/null || true
    IFS= read -r _s || _s=""
    stty echo 2>/dev/null || true
    printf '\n' >&2
    printf '%s' "$_s"
}
trap 'stty echo 2>/dev/null || true' EXIT

# ---------------------------------------------------------------------------
msg "Copying router-cli to $SHARE"
rm -rf "$SHARE"
mkdir -p "$SHARE"
cp -R "$WORK/router-cli/." "$SHARE/"
find "$SHARE" -name '__pycache__' -type d -prune -exec rm -rf {} +

msg "Installing packages from the installer (no network needed)"
env PKG_REPO_DIR="$WORK/packages" PYVER="$PYVER" NO_FRR="$NO_FRR" \
    REALTEK=auto REALTEK_DRIVER=auto sh "$SHARE/setup/install.sh"

msg "Creating the 'router' account"
cat <<'EOF'
  Two accounts exist on this router:
    root   - full FreeBSD shell (you set its password a moment ago)
    router - goes straight into the Cisco-style CLI (SSH or console)
  You now choose:
    1) the login password of 'router'
    2) the 'enable secret' - the password for the CLI command 'enable'
  Use at least 8 characters. Nothing is shown while you type.
EOF
host=$(sysrc -n hostname 2>/dev/null || echo router)
env SET_ROUTER_PW=1 ROUTER_HOSTNAME="${host%%.*}" sh "$SHARE/setup/setup_accounts.sh"

msg "Configuring SSH"
env SSHD_RESTART=no sh "$SHARE/setup/harden_sshd.sh"

# ---------------------------------------------------------------------------
# Optional quick setup
# ---------------------------------------------------------------------------
PY=$(head -1 /usr/local/bin/router-cli | sed 's/^#!//; s/ .*//')
ifaces=$(ifconfig -l | tr ' ' '\n' | \
    grep -Ev '^(lo|pflog|pfsync|enc|gif|stf|bridge|tun|tap|vlan|wg|lagg|carp|ipfw)[0-9]' | tr '\n' ' ')
set -- $ifaces
nif=$#
if1=${1:-}; if2=${2:-}

quick_setup() {
    echo
    echo "Network ports found in this machine:"
    for i in $ifaces; do
        mac=$(ifconfig "$i" 2>/dev/null | awk '/ether/{print $2; exit}')
        st=$(ifconfig "$i" 2>/dev/null | awk '/status:/{print $2; exit}')
        drv=$(echo "$i" | sed 's/[0-9]*$//')
        printf '   %-8s driver %-6s MAC %-17s cable %s\n' "$i" "$drv" "${mac:--}" "${st:-unknown}"
    done
    echo "   (tip: plug a cable into one port at a time and re-run to see which is which:"
    echo "    'cable active' = something is connected)"
    echo
    case " $ifaces " in
        *" rge0 "*) dwan=rge0 ;;
        *) if [ "$nif" -ge 2 ]; then dwan=$if2; else dwan=$if1; fi ;;
    esac
    dlan=$(for i in $ifaces; do [ "$i" != "$dwan" ] && { echo "$i"; break; }; done)

    while :; do
        ask "WAN port (to the internet / ISP modem)" "$dwan"; wan=$REPLY
        ask "LAN port (to your switch / PCs)" "${dlan:-}"; lan=$REPLY
        case " $ifaces " in *" $wan "*) ;; *) echo "  '$wan' is not a port of this machine"; continue ;; esac
        case " $ifaces " in *" $lan "*) ;; *) echo "  '$lan' is not a port of this machine"; continue ;; esac
        [ "$wan" != "$lan" ] || { echo "  WAN and LAN must be different ports"; continue; }
        break
    done
    while :; do
        ask "Router address on the LAN (network is /24 = 255.255.255.0)" "192.168.1.1"; lanip=$REPLY
        if echo "$lanip" | grep -Eq '^(25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])(\.(25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])){3}$'; then
            last=${lanip##*.}
            [ "$last" -ge 1 ] && [ "$last" -le 254 ] && break
        fi
        echo "  please type an address like 192.168.1.1"
    done
    net=${lanip%.*}
    hn=$(echo "${host%%.*}" | tr -cd 'A-Za-z0-9-'); [ -n "$hn" ] || hn=router

    echo
    echo "  How does $wan connect to the internet?"
    echo "    dhcp  - the ISP's modem/ONT hands out an address automatically (cable, most fibre/LTE boxes)"
    echo "    pppoe - your ISP gave you a PPPoE user name and password (DSL, many fibre ISPs)"
    while :; do
        ask "Connection type (dhcp/pppoe)" "dhcp"
        case "$REPLY" in [Dd]*) wantype=dhcp; break ;; [Pp]*) wantype=pppoe; break ;; esac
    done
    qs_user=""; qs_pass=""; qs_vlan=""
    if [ "$wantype" = pppoe ]; then
        while :; do
            ask "PPPoE user name (from your ISP)" ""; qs_user=$REPLY
            qs_pass=$(read_secret "PPPoE password (not shown)")
            again=$(read_secret "Retype the PPPoE password")
            [ "$qs_pass" = "$again" ] || { echo "  the passwords do not match - again please"; continue; }
            ask "ISP VLAN ID (only if your ISP gave you one, e.g. 35; empty = none)" ""; qs_vlan=$REPLY
            QS_USER=$qs_user QS_PASS=$qs_pass QS_VLAN=$qs_vlan \
                "$PY" -I "$SHARE/image/installer/quick_setup.py" check && break
        done
        again=""
    fi

    sb=$(mktemp -d /tmp/router-quick.XXXXXX)
    mkdir -p "$sb/etc" "$sb/var/db/router-cli/archive" "$sb/usr/local/etc"
    cp /etc/rc.conf "$sb/etc/rc.conf"
    cp /var/db/router-cli/running-config.json "$sb/var/db/router-cli/"
    # Build the CLI script (quick_setup.py fills the templates; values go through
    # the environment, never through the shell or sed), run it through the real CLI
    # in a sandbox (validates everything, changes nothing), then install the
    # result with 'router-apply init'.
    if ! env QS_TYPE="$wantype" QS_HOST="$hn" QS_WAN="$wan" QS_LAN="$lan" QS_LANIP="$lanip" \
            QS_VLAN="$qs_vlan" QS_USER="$qs_user" QS_PASS="$qs_pass" \
            "$PY" -I "$SHARE/image/installer/quick_setup.py" > "$sb/quick.cli"; then
        rm -rf "$sb"
        echo "  quick setup failed - the router starts unconfigured (see docs/EXAMPLES.md section 7)"
        return 0
    fi
    qs_pass=""
    out=$(env ROUTERCLI_SANDBOX="$sb" ROUTERCLI_IFACES="$ifaces" \
              "$PY" -I /usr/local/bin/router-cli --privileged < "$sb/quick.cli" 2>&1) || true
    if printf '%s\n' "$out" | grep -q '^ *%'; then
        printf '%s\n' "$out"
        rm -rf "$sb"
        echo "  quick setup failed - the router starts unconfigured (see docs/EXAMPLES.md section 7)"
        return 0
    fi
    "$PY" -I -c '
import json, sys
print(json.dumps({"force": True, "write_files": True, "config": json.load(open(sys.argv[1]))}))
' "$sb/var/db/router-cli/running-config.json" | /usr/local/libexec/router-apply init
    rm -rf "$sb"
    echo
    if [ "$wantype" = pppoe ]; then
        echo "  WAN $wan: PPPoE${qs_vlan:+ on VLAN $qs_vlan} as '$qs_user' (interface dialer1), firewalled, NAT"
        echo "            check it after the reboot with: show pppoe session"
    else
        echo "  WAN $wan: address from your ISP (DHCP), firewalled, NAT"
    fi
    echo "  LAN $lan: $lanip/24, DHCP for PCs $net.50 - $net.254, DNS on the router"
    QUICK_LAN=$lan; QUICK_IP=$lanip
}

QUICK_LAN=""; QUICK_IP=""
msg "Quick network setup"
if [ "$nif" -eq 0 ]; then
    echo "  no network ports detected - skipping (configure later with router-cli)"
else
    echo "  This makes the box a working home/office router right away:"
    echo "    WAN port  -> internet (DHCP, or PPPoE with your ISP login), firewall + NAT"
    echo "    LAN port  -> 192.168.1.1, gives addresses to your PCs (DHCP) and DNS"
    echo "  Everything can be changed later in the CLI ('configure terminal')."
    ask "Do the quick setup now? (yes/no)" "yes"
    case "$REPLY" in [Yy]*) quick_setup ;; *) echo "  skipped" ;; esac
fi

# ---------------------------------------------------------------------------
msg "Router software installed${IMAGE_VERSION:+ ($IMAGE_VERSION)}"
echo "  After the reboot:"
echo "   - on this screen, log in as 'router' for the CLI or 'root' for the FreeBSD shell"
if [ -n "$QUICK_IP" ]; then
    echo "   - or plug a PC into $QUICK_LAN and run:   ssh router@$QUICK_IP"
fi
echo "   - in the CLI: 'enable' (enable secret), then 'show running-config'"
echo "   - beginner guide: $SHARE/docs/EXAMPLES.md"
printf '\nPress Enter to continue... '
read -r _ || true
exit 0
