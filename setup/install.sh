#!/bin/sh
#
# install.sh - install router-cli on FreeBSD 14.x / 15.x (run as root from the project dir)
#
#   sh setup/install.sh            # install packages + router-cli
#   NO_PKG=1 sh setup/install.sh   # skip pkg(8) (offline / packages pre-installed)
#   PKG_REPO_DIR=/path sh setup/install.sh
#                                  # install from a local package repository (made with
#                                  #  'pkg repo'); no network needed - used by the installer image
#   NO_FRR=1 sh setup/install.sh   # skip FRR (no OSPF/RIP/BGP support)
#   PYVER=312 sh setup/install.sh  # python flavour (default: the ports default
#                                  #  python - 3.11 on older 14.x, 3.12 on 15.x)
#   NETISR_TUNING=no sh setup/install.sh
#                                  # do not set the netisr settings (deferred
#                                  #  dispatch, one bound thread per CPU core)
#
# Layout:
#   /usr/local/lib/router-cli/routercli   python package (root:wheel, read-only)
#   /usr/local/bin/router-cli             CLI launcher / login shell of 'router'
#   /usr/local/libexec/router-apply       privileged backend (doas target)
#   /usr/local/etc/doas.conf              'router' may run ONLY router-apply as root
#   /usr/local/etc/rc.d/routercli         boot hook (commit-confirmed safety)
#   /var/db/router-cli                    config store (root:router 0750)
#
set -eu

# bsdinstall's chroot may inherit a base-only PATH without ports executables.
PATH=/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/sbin:/usr/local/bin
export PATH

die() { echo "install.sh: $*" >&2; exit 1; }
INSTALL_STAGE="initial checks"
msg() { INSTALL_STAGE=$*; echo "==> $*"; }
trap 'status=$?; if [ "$status" -ne 0 ]; then
    printf "\ninstall.sh: FAILED during %s (exit status %s)\n" "$INSTALL_STAGE" "$status" >&2
fi' EXIT

[ "$(id -u)" -eq 0 ] || die "must be run as root"
[ "$(uname -s)" = "FreeBSD" ] || die "this installer targets FreeBSD"
major=$(freebsd-version -u | cut -d. -f1)
case "$major" in
    14|15) ;;
    *) echo "warning: FreeBSD $major detected - 14.x and 15.x are the supported targets" >&2 ;;
esac

SRC=$(cd "$(dirname "$0")/.." && pwd)
[ -d "$SRC/routercli" ] || die "cannot find routercli package in $SRC"

LIBDIR=/usr/local/lib/router-cli
STATEDIR=/var/db/router-cli
DOAS_CONF=/usr/local/etc/doas.conf
DOAS_RULE="permit nopass router as root cmd /usr/local/libexec/router-apply"

# --------------------------------------------------------------------------
# Python flavour. Package names are versioned (python311 / py311-prompt-toolkit
# on older 14.x, python312 / py312-prompt-toolkit on 15.x) and the repository
# only carries prompt-toolkit for the ports *default* python, so ask pkg which
# python the 'python3' meta-package points to.
detect_pyver() {
    if [ -z "${NO_PKG:-}" ]; then
        v=$(pkg rquery '%dn' python3 2>/dev/null | sed -n 's/^python\(3[0-9][0-9]*\)$/\1/p' | head -1)
        if [ -n "$v" ] && [ -n "$(pkg rquery '%n' "py${v}-prompt-toolkit" 2>/dev/null)" ]; then
            echo "$v"; return
        fi
        v=$(pkg search -q -x '^py3[0-9]+-prompt-toolkit-[0-9]' 2>/dev/null | \
            sed -n 's/^py\(3[0-9]*\)-.*/\1/p' | sort -n | tail -1)
        [ -n "$v" ] && { echo "$v"; return; }
    fi
    # offline: newest installed interpreter that has prompt_toolkit, else newest at all
    for p in $(ls -r /usr/local/bin/python3.[0-9]* 2>/dev/null | grep -E 'python3\.[0-9]+$'); do
        "$p" -c 'import prompt_toolkit' 2>/dev/null && { echo "$p" | sed 's/.*python3\./3/'; return; }
    done
    ls -r /usr/local/bin/python3.[0-9]* 2>/dev/null | grep -E 'python3\.[0-9]+$' | head -1 | sed 's/.*python3\./3/'
}

# --------------------------------------------------------------------------
# Offline mode: use ONLY the local repository in $PKG_REPO_DIR. pkg itself is
# bootstrapped from the pkg-*.pkg file in that repository.
if [ -n "${PKG_REPO_DIR:-}" ] && [ -z "${NO_PKG:-}" ]; then
    [ -d "$PKG_REPO_DIR" ] || die "PKG_REPO_DIR $PKG_REPO_DIR is not a directory"
    PKG_REPO_DIR=$(cd "$PKG_REPO_DIR" && pwd)
    REPOCONF=$(mktemp -d /tmp/router-repos.XXXXXX)
    printf 'ROUTER_OFFLINE: { url: "file://%s", signature_type: "none", enabled: yes }\n' \
        "$PKG_REPO_DIR" > "$REPOCONF/router-offline.conf"
    if ! command pkg -N >/dev/null 2>&1; then
        msg "Bootstrapping pkg from $PKG_REPO_DIR"
        pkgfile=$(find "$PKG_REPO_DIR" -name 'pkg-[0-9]*.pkg' | head -1)
        [ -n "$pkgfile" ] || die "no pkg-*.pkg in $PKG_REPO_DIR"
        bs=$(mktemp -d /tmp/pkg-bootstrap.XXXXXX)
        tar -xf "$pkgfile" -C "$bs" --include '*sbin/pkg-static'
        pkgstatic=$(find "$bs" -name pkg-static -type f | head -1)
        [ -n "$pkgstatic" ] || die "pkg-static not found in $pkgfile"
        "$pkgstatic" add "$pkgfile"
        rm -rf "$bs"
    fi
    pkg() { command pkg -o REPOS_DIR="$REPOCONF" -o IGNORE_OSVERSION=yes "$@"; }
fi

msg "Installing packages"
if [ -z "${NO_PKG:-}" ]; then
    pkg -N >/dev/null 2>&1 || env ASSUME_ALWAYS_YES=yes pkg bootstrap -y
    pkg update -q || true
fi
PYVER=${PYVER:-$(detect_pyver)}
[ -n "$PYVER" ] || die "cannot determine python version - set PYVER (e.g. PYVER=312)"
PY=/usr/local/bin/python$(echo "$PYVER" | sed 's/^3/3./')
echo "    python flavour: $PYVER ($PY)"
if [ -z "${NO_PKG:-}" ]; then
    pkg install -y "python${PYVER}" "py${PYVER}-prompt-toolkit" dnsmasq doas
    if [ -z "${NO_FRR:-}" ]; then
        # The FRR port is versioned (frr10, frr9, ...): take the newest available.
        frr=$(pkg search -q -x '^frr[0-9]+-[0-9]' | sed 's/-[0-9].*//' | sort -V | tail -1)
        if [ -n "$frr" ]; then
            pkg install -y "$frr"
        else
            echo "warning: no FRR package found - dynamic routing disabled" >&2
        fi
    fi
fi
[ -x "$PY" ] || die "$PY not found (install python${PYVER} or set PYVER)"
if [ -n "${REPOCONF:-}" ]; then
    pkg clean -a -y >/dev/null 2>&1 || true
fi

# --------------------------------------------------------------------------
# Realtek NICs.
#  * RTL8168/8111 family (incl. RTL8168H, 1GbE): built-in re(4) on 14.x and 15.x.
#  * RTL8125/8125B (2.5GbE, PCI 10ec:8125): NOT supported by re(4). Options:
#     - vendor : Realtek's driver from ports, net/realtek-re-kmod (14.x + 15.x).
#                Probes with higher priority than the built-in drivers, so it
#                drives every Realtek card: re0, re1, ...
#     - native : the new rge(4) driver (if_rge.ko; in stable/15 and 15.2+). Default
#                when present - nothing extra to install.
#                RTL8168H stays on re(4) as re0, the RTL8125B becomes rge0.
#   REALTEK=auto (default: only if a Realtek NIC is present) | yes | no
#   REALTEK_DRIVER=auto (default: native if this kernel has if_rge.ko, else vendor)
#                | vendor | native
#   REALTEK_JUMBO=1   keep the vendor driver's large RX buffers (jumbo frames)
# --------------------------------------------------------------------------
REALTEK=${REALTEK:-auto}
REALTEK_DRIVER=${REALTEK_DRIVER:-auto}
case "$REALTEK_DRIVER" in
    auto) if [ -f /boot/kernel/if_rge.ko ]; then REALTEK_DRIVER=native; else REALTEK_DRIVER=vendor; fi ;;
    vendor|native) ;;
    *) die "REALTEK_DRIVER must be auto|vendor|native";;
esac
realtek_nics=$(pciconf -l 2>/dev/null | grep -Ei 'vendor=0x10ec' | grep -Ei 'device=0x(8125|8126|8168|8161|8136)' || true)
LOADER=/boot/loader.conf

# sysrc accepts shell variable names, not dotted loader tunables.
loader_tunable() (
    key=$1
    value=$2
    touch "$LOADER" || die "cannot access $LOADER"
    tmp=$(mktemp "$LOADER.XXXXXX") || die "cannot create loader configuration temporary file"
    trap 'rm -f "$tmp"' EXIT
    cp -p "$LOADER" "$tmp" || die "cannot preserve loader configuration permissions"
    awk -v key="$key" '
        {
            lhs = $0
            sub(/=.*/, "", lhs)
            gsub(/[ \t]/, "", lhs)
            if (lhs != key) print
        }
    ' "$LOADER" > "$tmp" || die "cannot read loader configuration"
    if [ -n "$value" ]; then
        printf '%s="%s"\n' "$key" "$value" >> "$tmp" || die "cannot write loader tunable $key"
    fi
    mv -f "$tmp" "$LOADER" || die "cannot replace $LOADER"
)

realtek_vendor() {
    if [ ! -f /boot/modules/if_re.ko ]; then
        if [ -z "${NO_PKG:-}" ]; then
            pkg install -y realtek-re-kmod || true
        fi
    fi
    [ -f /boot/modules/if_re.ko ] || return 1
    sysrc -f $LOADER -x if_rge_load >/dev/null 2>&1 || true
    sysrc -f $LOADER if_re_load=YES if_re_name=/boot/modules/if_re.ko >/dev/null
    # Smaller RX buffers avoid driver hangs on fragmented memory (pkg-message);
    # 2048 still fits a full 1500-byte frame + VLAN tag. Jumbo frames need REALTEK_JUMBO=1.
    if [ -z "${REALTEK_JUMBO:-}" ]; then
        loader_tunable hw.re.max_rx_mbuf_sz 2048
    else
        loader_tunable hw.re.max_rx_mbuf_sz ""
    fi
    if kldstat -q -m pci/if_re 2>/dev/null; then      # vendor module registers pci/if_re (built-in: pci/re)
        echo "    vendor re driver already active"
    else
        echo "    vendor driver (realtek-re-kmod) set in $LOADER - REBOOT to activate it"
        echo "    afterwards both NICs are re0/re1 (check: ifconfig -l ; dmesg | grep '^re')"
    fi
}

realtek_native() {
    [ -f /boot/kernel/if_rge.ko ] || return 1
    sysrc -f $LOADER -x if_re_load if_re_name >/dev/null 2>&1 || true
    loader_tunable hw.re.max_rx_mbuf_sz ""
    sysrc -f $LOADER if_rge_load=YES >/dev/null
    kldload -n if_rge 2>/dev/null || true
    echo "    native rge(4) enabled: RTL8125B = rge0, RTL8168H = re0 (check: ifconfig -l)"
}

if [ "$REALTEK" = yes ] || { [ "$REALTEK" = auto ] && [ -n "$realtek_nics" ]; }; then
    msg "Realtek NIC support (driver: $REALTEK_DRIVER)"
    [ -n "$realtek_nics" ] && printf '%s\n' "$realtek_nics" | sed 's/^/    /'
    if [ "$REALTEK_DRIVER" = native ]; then
        realtek_native || { echo "    rge(4) not in this kernel (only in stable/15 / 15.2+) - using vendor driver";
                            realtek_vendor; } || \
            echo "warning: no RTL8125 driver available - install net/realtek-re-kmod (see docs/BUILDING_FREEBSD.md)" >&2
    else
        realtek_vendor || { echo "    realtek-re-kmod unavailable - trying native rge(4)"; realtek_native; } || \
            echo "warning: no RTL8125 driver available - install net/realtek-re-kmod (see docs/BUILDING_FREEBSD.md)" >&2
    fi
fi
"$PY" -c 'import prompt_toolkit' 2>/dev/null || \
    echo "warning: prompt_toolkit missing - CLI falls back to readline (reduced completion)" >&2

# --------------------------------------------------------------------------
msg "Creating group 'router'"
pw groupshow router >/dev/null 2>&1 || pw groupadd router

# --------------------------------------------------------------------------
msg "Installing python package to $LIBDIR"
rm -rf "$LIBDIR.new"
mkdir -p "$LIBDIR.new"
cp -R "$SRC/routercli" "$LIBDIR.new/"
find "$LIBDIR.new" -name '__pycache__' -type d -prune -exec rm -rf {} +
"$PY" -m compileall -q "$LIBDIR.new/routercli"
chown -R root:wheel "$LIBDIR.new"
chmod -R u=rwX,go=rX "$LIBDIR.new"
rm -rf "$LIBDIR.old"
[ -d "$LIBDIR" ] && mv "$LIBDIR" "$LIBDIR.old"
mv "$LIBDIR.new" "$LIBDIR"
rm -rf "$LIBDIR.old"

# Launchers: rewrite the shebang to the exact interpreter, isolated mode (-I).
msg "Installing launchers"
tmp=$(mktemp)
sed "1s|.*|#!$PY -I|" "$SRC/bin/router-cli" > "$tmp"
install -o root -g wheel -m 0755 "$tmp" /usr/local/bin/router-cli
mkdir -p /usr/local/libexec
sed "1s|.*|#!$PY -I|" "$SRC/bin/router-apply" > "$tmp"
install -o root -g wheel -m 0755 "$tmp" /usr/local/libexec/router-apply
rm -f "$tmp"

# --------------------------------------------------------------------------
msg "Creating state directory $STATEDIR"
install -d -o root -g router -m 0750 "$STATEDIR" "$STATEDIR/archive"
install -d -o root -g wheel  -m 0700 "$STATEDIR/backup"
mkdir -p /usr/local/etc/frr

# --------------------------------------------------------------------------
msg "Configuring doas ($DOAS_CONF)"
touch "$DOAS_CONF" || die "cannot create or access $DOAS_CONF"
if ! grep -qxF "$DOAS_RULE" "$DOAS_CONF"; then
    cp -p "$DOAS_CONF" "$DOAS_CONF.bak.$(date +%Y%m%d%H%M%S)" || \
        die "cannot back up $DOAS_CONF"
    {
        echo "# router-cli: the 'router' account may run ONLY the config backend as root."
        echo "# The environment is reset by doas (no keepenv), arguments are validated by the helper."
        echo "$DOAS_RULE"
    } >> "$DOAS_CONF" || die "cannot append the router backend rule to $DOAS_CONF"
fi
chown root:wheel "$DOAS_CONF" || die "cannot set root:wheel ownership on $DOAS_CONF"
chmod 0600 "$DOAS_CONF" || die "cannot set mode 0600 on $DOAS_CONF"
if doas -C "$DOAS_CONF"; then
    echo "    doas.conf syntax validated"
else
    status=$?
    die "doas -C $DOAS_CONF failed (exit status $status); configuration validation was not bypassed"
fi

# --------------------------------------------------------------------------
msg "Installing rc.d script"
install -o root -g wheel -m 0555 "$SRC/setup/rc.d/routercli" /usr/local/etc/rc.d/routercli
sysrc -q routercli_enable=YES >/dev/null

# Kernel modules used by the appliance (also loaded on demand by rc).
sysrc -q kld_list+="pf pflog if_vlan" >/dev/null 2>&1 || true

# --------------------------------------------------------------------------
# Packet processing on all CPU cores (netisr):
#   net.isr.dispatch=deferred  hand packets to netisr worker threads instead of
#                              processing them in the NIC interrupt context
#   net.isr.maxthreads=<ncpu>  one worker thread per CPU core
#   net.isr.bindthreads=1      pin each worker to its core (cache locality)
# maxthreads/bindthreads are boot-time tunables (read-only once the system runs),
# so they go into /boot/loader.conf; dispatch is a normal sysctl -> /etc/sysctl.conf.
NETISR_TUNING=${NETISR_TUNING:-yes}
SYSCTL_CONF=/etc/sysctl.conf
touch "$SYSCTL_CONF"
sed -i '' '/^net\.isr\.dispatch=/d' "$SYSCTL_CONF"
if [ "$NETISR_TUNING" = yes ]; then
    ncpu=$(sysctl -n hw.ncpu 2>/dev/null || echo 1)
    msg "netisr: deferred dispatch, $ncpu worker threads bound to the CPU cores"
    loader_tunable net.isr.maxthreads "$ncpu"
    loader_tunable net.isr.bindthreads 1
    echo "net.isr.dispatch=deferred" >> "$SYSCTL_CONF"
    sysctl net.isr.dispatch=deferred >/dev/null 2>&1 || true
    echo "    $LOADER: net.isr.maxthreads=$ncpu net.isr.bindthreads=1 (active after reboot)"
    echo "    $SYSCTL_CONF: net.isr.dispatch=deferred"
else
    loader_tunable net.isr.maxthreads ""
    loader_tunable net.isr.bindthreads ""
fi

# --------------------------------------------------------------------------
grep -qxF /usr/local/bin/router-cli /etc/shells || echo /usr/local/bin/router-cli >> /etc/shells

if [ -n "${REPOCONF:-}" ]; then rm -rf "$REPOCONF"; fi

msg "Done. Next steps:"
echo "    sh $SRC/setup/setup_accounts.sh     # create 'router' user, enable secret"
echo "    sh $SRC/setup/harden_sshd.sh        # sshd ForceCommand / root key-only"
