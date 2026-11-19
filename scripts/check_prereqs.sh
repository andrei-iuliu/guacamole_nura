#!/usr/bin/env bash
# Check host prerequisites and print what to install for anything missing.
#
#   scripts/check_prereqs.sh [--stage core|boot|kernel|rootfs|firmware|phone]... [--quiet]
#
# With no --stage, every stage is checked. Exit status: 0 when everything
# checked is present, 1 otherwise (other scripts call it to warn early).
set -uo pipefail

STAGES=()
QUIET=0
while (($#)); do
    case "$1" in
        --stage) STAGES+=("${2:?--stage needs a name}"); shift ;;
        --stage=*) STAGES+=("${1#*=}") ;;
        --quiet) QUIET=1 ;;
        -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done
((${#STAGES[@]})) || STAGES=(core boot kernel rootfs firmware phone)

# stage: what it is for | items ("cmd:NAME" or "file:PATH" or "lib:SONAME"), each with a package key
declare -A WHAT=(
    [core]="any step"
    [boot]="boot image and DTB wrapper (scripts/build_host_tools.sh, prepare_temporary_dtb.py)"
    [kernel]="kernel build (flows/rc4/scripts/rc4.sh kernel)"
    [rootfs]="rootfs build (local_storage_build.sh --build; pmbootstrap)"
    [firmware]="firmware/sensor extraction from a Lineage OTA"
    [phone]="installing and talking to the phone"
)
declare -A ITEMS=(
    [core]="cmd:git=git cmd:curl=curl cmd:python3=python cmd:sha256sum=coreutils cmd:tar=tar cmd:gzip=gzip"
    [boot]="cmd:dtc=dtc cmd:fdtget=dtc cmd:fdtput=dtc cmd:fdtoverlay=dtc lib:libfdt.so.1=dtc file:/usr/include/libfdt.h=libfdt-dev cmd:cc=gcc cmd:cpio=cpio cmd:zstd=zstd"
    [kernel]="cmd:clang=clang cmd:ld.lld=lld cmd:llvm-objcopy=llvm cmd:pahole=pahole cmd:make=make cmd:flex=flex cmd:bison=bison cmd:perl=perl cmd:rsync=rsync"
    [rootfs]="cmd:sudo=sudo cmd:kpartx=kpartx cmd:losetup=util-linux cmd:flock=util-linux cmd:openssl=openssl cmd:debugfs=e2fsprogs cmd:chroot=coreutils cmd:ps=procps cmd:sha512sum=coreutils cmd:rsync=rsync"
    [firmware]="cmd:mcopy=mtools cmd:debugfs=e2fsprogs"
    [phone]="cmd:ssh=openssh"
)

# package key -> name per package manager
pkg_name() {
    local key=$1 pm=$2
    case "$pm:$key" in
        pacman:libfdt-dev) echo dtc ;;
        pacman:kpartx) echo multipath-tools ;;
        pacman:llvm) echo llvm ;;
        pacman:procps) echo procps-ng ;;
        pacman:openssh) echo openssh ;;
        apt:dtc) echo device-tree-compiler ;;
        apt:libfdt-dev) echo libfdt-dev ;;
        apt:gcc) echo build-essential ;;
        apt:python) echo python3 ;;
        apt:pahole) echo dwarves ;;
        apt:llvm) echo llvm ;;
        apt:openssh) echo openssh-client ;;
        dnf:libfdt-dev) echo libfdt-devel ;;
        dnf:dtc) echo dtc ;;
        dnf:python) echo python3 ;;
        dnf:pahole) echo dwarves ;;
        dnf:procps) echo procps-ng ;;
        dnf:kpartx) echo kpartx ;;
        dnf:openssh) echo openssh-clients ;;
        *) echo "$key" ;;
    esac
}

if command -v pacman >/dev/null; then PM=pacman; INSTALL="sudo pacman -S --needed"
elif command -v apt-get >/dev/null; then PM=apt; INSTALL="sudo apt install"
elif command -v dnf >/dev/null; then PM=dnf; INSTALL="sudo dnf install"
else PM=unknown; INSTALL="install:"
fi

have() {
    local kind=${1%%:*} name=${1#*:}
    case "$kind" in
        cmd) command -v "$name" >/dev/null 2>&1 ;;
        file) [[ -e "$name" ]] ;;
        lib) ldconfig -p 2>/dev/null | grep -q "[[:space:]]$name " ||
             compgen -G "/usr/lib*/$name" >/dev/null || compgen -G "/usr/lib/*/$name" >/dev/null ;;
    esac
}

status=0
declare -A ALL_PKGS=()
for stage in "${STAGES[@]}"; do
    [[ -n "${ITEMS[$stage]:-}" ]] || { echo "unknown stage: $stage" >&2; exit 2; }
    missing=() pkgs=()
    for item in ${ITEMS[$stage]}; do
        spec=${item%%=*} key=${item#*=}
        have "$spec" && continue
        missing+=("${spec#*:}")
        pkg="$(pkg_name "$key" "$PM")"
        [[ " ${pkgs[*]} " == *" $pkg "* ]] || pkgs+=("$pkg")
        ALL_PKGS[$pkg]=1
    done
    if ((${#missing[@]})); then
        status=1
        echo "WARNING [$stage] missing for ${WHAT[$stage]}: ${missing[*]}" >&2
        echo "         $INSTALL ${pkgs[*]}" >&2
    elif ((!QUIET)); then
        echo "ok      [$stage] ${WHAT[$stage]}"
    fi
done

# Version checks for tools whose output the pins depend on.
if have cmd:dtc && [[ " ${STAGES[*]} " == *" boot "* ]]; then
    v="$(dtc --version 2>/dev/null | sed -n 's/.*DTC v\{0,1\}\([0-9][0-9.]*\).*/\1/p')"
    if ! printf '1.7\n%s\n' "${v:-0}" | sort -V -C; then
        echo "WARNING [boot] dtc is ${v:-unknown}; 1.7 or newer is needed" >&2; status=1
    fi
fi
if have cmd:clang && [[ " ${STAGES[*]} " == *" kernel "* ]]; then
    v="$(clang --version | sed -n 's/.*clang version \([0-9]*\).*/\1/p' | head -1)"
    [[ "$v" == 23 ]] || echo "note    [kernel] clang $v; the reviewed kernel builds used clang 23 (see BUILD_ENVIRONMENT.md)" >&2
fi

if ((status)) && ((${#ALL_PKGS[@]})); then
    echo >&2
    echo "To install everything missing: $INSTALL ${!ALL_PKGS[*]}" >&2
fi
exit $status
