#!/bin/sh
#
# metalog.sh ROOT SUBDIR - print mtree METALOG lines for ROOT/SUBDIR and
# everything below it (directories 0755, files 0644, *.sh and hooks 0755).
#
# The FreeBSD release tools build the ISO/USB image from a METALOG (list of
# files with owner and permissions) instead of the real file owners, so files
# added to the installer must be listed in it. Used by build-image.sh.
#
set -eu
ROOT=${1:?usage: metalog.sh ROOT SUBDIR}
SUB=${2:?usage: metalog.sh ROOT SUBDIR}
cd "$ROOT"
[ -d "$SUB" ] || { echo "metalog.sh: $ROOT/$SUB is not a directory" >&2; exit 1; }

bad=$(find "$SUB" -name '*[[:space:]#\\]*' | head -1)
[ -z "$bad" ] || { echo "metalog.sh: unsupported character in file name: $bad" >&2; exit 1; }

find "$SUB" -type d | LC_ALL=C sort | \
    sed 's|^\(.*\)$|./\1 type=dir uname=root gname=wheel mode=0755|'
find "$SUB" -type f | LC_ALL=C sort | while read -r f; do
    case "$f" in
        *.sh|*/bin/*|*/rc.d/*|*/local.post-configure) mode=0755 ;;
        *) mode=0644 ;;
    esac
    echo "./$f type=file uname=root gname=wheel mode=$mode"
done
find "$SUB" -type l | LC_ALL=C sort | while read -r f; do
    echo "./$f type=link uname=root gname=wheel mode=0755 link=$(readlink "$f")"
done
