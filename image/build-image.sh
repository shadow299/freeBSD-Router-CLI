#!/bin/sh
#
# build-image.sh - build the router installer (ISO + USB-stick image) from the
# FreeBSD stable/15 source code, with router-cli and all its packages inside.
#
# Run as root on a FreeBSD 15 machine (real or virtual):
#
#   sh image/build-image.sh                # PC / x64 (amd64) installer
#   sh image/build-image.sh -a arm64       # ARM 64-bit (UEFI) installer, cross-built
#
# Options:
#   -a ARCH     amd64 (default) | arm64
#   -b BRANCH   FreeBSD git branch (default: stable/15)
#   -s DIR      FreeBSD source directory (default: /usr/src)
#   -o DIR      output directory (default: /usr/obj/router-image)
#   (set MAKEOBJDIRPREFIX=/bigdisk/obj to build somewhere else than /usr/obj;
#    set GIT_URL=https://github.com/freebsd/freebsd-src.git to download the
#    source from the GitHub mirror if git.FreeBSD.org is slow or unreachable)
#   -j N        parallel build jobs (default: number of CPUs)
#   -k NAME     kernel configuration (default: GENERIC)
#   -n          no FRR (no OSPF/RIP/BGP; saves ~30 MB)
#   -u          do not update the source code (build what is in -s DIR)
#   -f          force a full rebuild of world + kernel
#   -h          help
#
# The result is in <output>/images/:
#   freebsd-router-<version>-<arch>-<date>.img   write this to a USB stick
#   freebsd-router-<version>-<arch>-<date>.iso   for virtual machines / CD
#   CHECKSUMS.SHA256
#
# Steps (each logs to <output>/logs/<arch>-<step>.log):
#   1 checks   2 source   3 world   4 kernel   5 release   6 packages
#   7 router files   8 images
# A re-run skips world + kernel if the source, src.conf and kernel are
# unchanged, so a failed later step costs minutes, not hours.
#
set -eu

PROJ=$(cd "$(dirname "$0")/.." && pwd)
HERE=$PROJ/image
ARCH=amd64
BRANCH=stable/15
SRC=/usr/src
OBJROOT=${MAKEOBJDIRPREFIX:-/usr/obj}
OUT=$OBJROOT/router-image
JOBS=$(sysctl -n hw.ncpu 2>/dev/null || echo 2)
KERNCONF=GENERIC
NO_FRR=""
NO_UPDATE=""
FORCE=""
GIT_URL=${GIT_URL:-https://git.FreeBSD.org/src.git}

usage() { sed -n '2,/^set -eu/p' "$0" | sed -e '/^set -eu/d' -e 's/^#//' -e 's/^ //'; exit "${1:-0}"; }
while getopts a:b:s:o:j:k:nufh opt; do
    case "$opt" in
        a) ARCH=$OPTARG ;;   b) BRANCH=$OPTARG ;;  s) SRC=$OPTARG ;;
        o) OUT=$OPTARG ;;    j) JOBS=$OPTARG ;;    k) KERNCONF=$OPTARG ;;
        n) NO_FRR=1 ;;       u) NO_UPDATE=1 ;;     f) FORCE=1 ;;
        h) usage 0 ;;        *) usage 1 ;;
    esac
done

case "$ARCH" in
    amd64)   TARGET=amd64; TARGET_ARCH=amd64 ;;
    arm64)   TARGET=arm64; TARGET_ARCH=aarch64 ;;
    riscv64) echo "riscv64: not supported by this script yet (see docs/BUILDING_FREEBSD.md)" >&2; exit 1 ;;
    *)       echo "unknown -a $ARCH (use amd64 or arm64)" >&2; exit 1 ;;
esac

LOGDIR=$OUT/logs
STEP=0
step() { STEP=$((STEP + 1)); printf '\n[%s/8] %s\n' "$STEP" "$*"; }
info() { printf '      %s\n' "$*"; }
die()  { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

# run NAME CMD... : run a long command with its output in a log file
run() {
    name=$1; shift
    log=$LOGDIR/$ARCH-$name.log
    info "log: $log   (watch it live: tail -f $log)"
    start=$(date +%s)
    if ! "$@" > "$log" 2>&1; then
        echo "------ last 40 lines of $log ------" >&2
        tail -n 40 "$log" >&2
        die "step '$name' failed - see $log (docs/BUILDING_FREEBSD.md: 'When something goes wrong')"
    fi
    info "done in $(( ($(date +%s) - start) / 60 )) min"
}

# Everything the FreeBSD build needs, passed to every make call. The build
# machine's /etc/src.conf and /etc/make.conf are NOT used, so the image does not
# depend on how the build machine is set up (e.g. a CPUTYPE in make.conf).
SRCCONF=$HERE/src.conf
__MAKE_CONF=/dev/null
export SRCCONF __MAKE_CONF KERNCONF
MK="make -C $SRC -j$JOBS TARGET=$TARGET TARGET_ARCH=$TARGET_ARCH SRCCONF=$SRCCONF __MAKE_CONF=/dev/null KERNCONF=$KERNCONF"
REL="make -C $SRC/release TARGET=$TARGET TARGET_ARCH=$TARGET_ARCH SRCCONF=$SRCCONF __MAKE_CONF=/dev/null KERNCONF=$KERNCONF NOPKGBASE=1 NOSRC=1 NOPORTS=1 NOPKG=1"

# ---------------------------------------------------------------------------
step "Checking the build machine"
[ "$(uname -s)" = FreeBSD ] || die "this must run on FreeBSD (a FreeBSD 15 virtual machine is fine)"
[ "$(id -u)" -eq 0 ] || die "run as root (type 'su -' first)"
hostmajor=$(freebsd-version -u | cut -d. -f1)
[ "$hostmajor" -ge 15 ] || die "the build machine runs FreeBSD $(freebsd-version -u); FreeBSD 15 or newer is needed to build stable/15"
command -v git >/dev/null 2>&1 || die "git is missing - install it with:  pkg install -y git-lite"
mkdir -p "$OUT" "$LOGDIR" "$OBJROOT"
free_gb=$(df -k "$OBJROOT" | awk 'NR==2 {print int($4 / 1048576)}')
[ "$free_gb" -ge 30 ] || die "only ${free_gb} GB free on $OBJROOT - at least 30 GB are needed"
mem_gb=$(( $(sysctl -n hw.physmem) / 1073741824 ))
[ "$mem_gb" -ge 4 ] || info "WARNING: only ${mem_gb} GB RAM - the build may be slow; use -j 2 if it fails"
pkg -N >/dev/null 2>&1 || env ASSUME_ALWAYS_YES=yes pkg bootstrap -y >/dev/null
info "FreeBSD $(freebsd-version -u), $JOBS CPU jobs, ${mem_gb} GB RAM, ${free_gb} GB free"
info "building: $BRANCH for $ARCH ($TARGET/$TARGET_ARCH), kernel $KERNCONF"

# ---------------------------------------------------------------------------
step "Getting the FreeBSD source code ($BRANCH) into $SRC"
if [ -d "$SRC/.git" ]; then
    if [ -z "$NO_UPDATE" ]; then
        info "updating the existing checkout"
        run git-update sh -c "git -C '$SRC' fetch --depth 1 origin '$BRANCH' && git -C '$SRC' checkout -q -B '$BRANCH' FETCH_HEAD"
    fi
else
    # /usr/src is often a mount point that cannot be removed ("Device busy"): the
    # installer's ZFS layout makes it a dataset (zroot/usr/src). So download into
    # the existing directory instead of letting git create it. Only filesystem
    # housekeeping entries (UFS .snap, lost+found) may be in it.
    mkdir -p "$SRC"
    extra=""
    for f in "$SRC"/* "$SRC"/.[!.]* "$SRC"/..?*; do
        [ -e "$f" ] || continue
        case "${f##*/}" in lost+found|.snap) continue ;; esac
        extra="$extra${f##*/} "
    done
    if [ -n "$extra" ]; then
        die "$SRC is not empty and is not a FreeBSD git checkout (it contains: $extra...).
       Empty it first (it may be a mount point, so remove its contents, not the directory):
         mkdir -p /root/src.old && mv $SRC/* $SRC/.[!.]* /root/src.old/
       or build from another directory:  sh image/build-image.sh -s /home/src"
    fi
    info "downloading from $GIT_URL (about 1 GB, 5-20 minutes)"
    run git-clone sh -c "git -C '$SRC' init -q && git -C '$SRC' remote add origin '$GIT_URL' && \
        git -C '$SRC' fetch --depth 1 origin '$BRANCH' && git -C '$SRC' checkout -q -B '$BRANCH' FETCH_HEAD"
fi
[ -f "$SRC/sys/conf/newvers.sh" ] || die "$SRC does not look like FreeBSD source code"
REVISION=$(sed -n 's/^REVISION="\(.*\)"/\1/p' "$SRC/sys/conf/newvers.sh")
BRANCHNAME=$(sed -n 's/^BRANCH="\(.*\)"/\1/p' "$SRC/sys/conf/newvers.sh")
OSVERSION=$(awk '/^#define[[:space:]]+__FreeBSD_version/ {print $3}' "$SRC/sys/sys/param.h")
MAJOR=${REVISION%%.*}
GITREV=$(git -C "$SRC" rev-parse --short HEAD 2>/dev/null || echo unknown)
VERSION="$REVISION-$BRANCHNAME"
info "source: FreeBSD $VERSION ($GITREV), __FreeBSD_version $OSVERSION"
[ -f "$SRC/sys/$TARGET/conf/$KERNCONF" ] || die "kernel config $SRC/sys/$TARGET/conf/$KERNCONF not found"

STAMP=$OUT/.built-$ARCH
want="$GITREV $(sha256 -q "$SRCCONF") $KERNCONF $(sha256 -q "$SRC/sys/$TARGET/conf/$KERNCONF")"
if [ -z "$FORCE" ] && [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$want" ]; then
    SKIP_BUILD=1
else
    SKIP_BUILD=""
    rm -f "$STAMP"
fi

# ---------------------------------------------------------------------------
step "Building the FreeBSD base system (buildworld)"
if [ -n "$SKIP_BUILD" ]; then
    info "already built from this source - skipping (use -f to force)"
else
    info "this is the long part: about 1-3 hours on a 4-8 core PC (more for arm64)"
    run buildworld $MK buildworld
fi

# ---------------------------------------------------------------------------
step "Building the kernel ($KERNCONF)"
if [ -n "$SKIP_BUILD" ]; then
    info "already built - skipping"
else
    info "about 10-30 minutes"
    run buildkernel $MK buildkernel
    echo "$want" > "$STAMP"
fi

# ---------------------------------------------------------------------------
step "Preparing the installer (FreeBSD release tools)"
info "packing base.txz + kernel.txz and the installer system, about 10-20 minutes"
run release-clean sh -c "$REL clean && $REL obj"
RELOBJ=$($REL -V .OBJDIR)
[ -d "$RELOBJ" ] || die "release object directory $RELOBJ missing"
run release-disc1 $REL disc1
DISC=$RELOBJ/disc1
[ -f "$DISC/usr/freebsd-dist/base.txz" ] && [ -f "$DISC/METALOG" ] || die "release staging in $DISC is incomplete"

# ---------------------------------------------------------------------------
step "Downloading the router packages for FreeBSD:$MAJOR:$TARGET_ARCH"
PKGW=$OUT/pkg-$ARCH
rm -rf "$PKGW"
mkdir -p "$PKGW/db" "$PKGW/cache" "$PKGW/repo"
# A private package database + cache for the TARGET's ABI; the build
# machine's own packages are not touched.
tpkg() {
    pkg -o ABI="FreeBSD:$MAJOR:$TARGET_ARCH" -o OSVERSION="$OSVERSION" -o IGNORE_OSVERSION=yes \
        -o PKG_DBDIR="$PKGW/db" -o PKG_CACHEDIR="$PKGW/cache" -o ASSUME_ALWAYS_YES=yes "$@"
}
run pkg-update tpkg update -f
PYVER=$(tpkg rquery '%dn' python3 | sed -n 's/^python\(3[0-9]*\)$/\1/p' | head -1)
[ -n "$PYVER" ] || die "could not find python3 in the package repository"
PKGS="pkg python3 python$PYVER py$PYVER-prompt-toolkit dnsmasq doas"
if [ -z "$NO_FRR" ]; then
    FRR=$(tpkg search -q -x '^frr[0-9]+-[0-9]' | sed 's/-[0-9].*//' | sort -V | tail -1)
    if [ -n "$FRR" ]; then PKGS="$PKGS $FRR"
    else info "WARNING: no FRR package found - dynamic routing will be unavailable"; fi
fi
info "packages: $PKGS (+ dependencies)"
run pkg-fetch tpkg fetch -d -o "$PKGW/repo" $PKGS
run pkg-repo pkg repo "$PKGW/repo"
info "$(find "$PKGW/repo" -name '*.pkg' | wc -l | tr -d ' ') package files, $(du -sh "$PKGW/repo" | cut -f1)"

# ---------------------------------------------------------------------------
step "Adding router-cli to the installer"
PAY=$DISC/usr/freebsd-router
rm -rf "$PAY"
mkdir -p "$PAY/router-cli"
tar -cf - -C "$PROJ" --exclude .git --exclude __pycache__ --exclude '*.pyc' --exclude tests --exclude out . | \
    tar -xf - -C "$PAY/router-cli"
cp -R "$PKGW/repo" "$PAY/packages"
cat > "$PAY/build.env" <<EOF
PYVER=$PYVER
NO_FRR=$NO_FRR
IMAGE_VERSION="FreeBSD $VERSION ($GITREV) $ARCH, built $(date +%Y-%m-%d)"
EOF
# hook run by the FreeBSD installer after it has installed the base system
install -m 0555 "$HERE/installer/local.post-configure" "$DISC/usr/libexec/bsdinstall/local.post-configure"
# skip installer screens that the router setup takes care of
rclocal=$DISC/etc/rc.local
{
    head -n 1 "$rclocal"
    echo "# freebsd-router: these installer screens are handled by the router setup"
    echo "BSDINSTALL_SKIP_FIRMWARE=yes; BSDINSTALL_SKIP_USERS=yes; BSDINSTALL_SKIP_SERVICES=yes"
    echo "export BSDINSTALL_SKIP_FIRMWARE BSDINSTALL_SKIP_USERS BSDINSTALL_SKIP_SERVICES"
    tail -n +2 "$rclocal"
} > "$rclocal.new"
mv "$rclocal.new" "$rclocal"
# The images are made from the METALOG (file list with owners and modes), so
# every added file must be listed there too.
sh "$HERE/metalog.sh" "$DISC" usr/freebsd-router >> "$DISC/METALOG"
echo "./usr/libexec/bsdinstall/local.post-configure type=file uname=root gname=wheel mode=0555" >> "$DISC/METALOG"
info "added $(du -sh "$PAY" | cut -f1) (router-cli + packages)"

# ---------------------------------------------------------------------------
step "Creating the ISO and USB images"
run images $REL disc1.iso memstick.img
IMGDIR=$OUT/images
mkdir -p "$IMGDIR"
NAME=freebsd-router-$VERSION-$ARCH-$(date +%Y%m%d)
mv -f "$RELOBJ/disc1.iso" "$IMGDIR/$NAME.iso"
mv -f "$RELOBJ/memstick.img" "$IMGDIR/$NAME.img"
(cd "$IMGDIR" && { grep -v "($NAME\." CHECKSUMS.SHA256 2>/dev/null || true; sha256 "$NAME.iso" "$NAME.img"; } > CHECKSUMS.new \
    && mv CHECKSUMS.new CHECKSUMS.SHA256)

printf '\nFinished. Your installer images:\n'
ls -lh "$IMGDIR/$NAME.img" "$IMGDIR/$NAME.iso" | awk '{print "   " $5 "  " $NF}'
cat <<EOF

Next: write the .img file to a USB stick (all data on the stick is erased!), e.g.
   dd if=$IMGDIR/$NAME.img of=/dev/da0 bs=1m conv=sync status=progress
(on Windows use Rufus or balenaEtcher) and boot the router from it.
See docs/BUILDING_FREEBSD.md, "Part 3".
EOF
