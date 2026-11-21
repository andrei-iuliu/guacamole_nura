#!/usr/bin/env bash
# One package-signing key for every pmbootstrap work directory of this project.
#
# pmbootstrap generates a new abuild key (pmos@local-XXXXXXXX) for each fresh
# work directory, and only when $WORK/config_abuild/abuild.conf is missing. The
# installed phone trusts the key of the work directory that built its rootfs,
# so a kernel APK from a new work directory fails `apk add` until its key is
# copied over. Keeping one project key outside the repository avoids that.
#
#   scripts/signing_key.sh status [WORK]   show the project key (and WORK's key)
#   scripts/signing_key.sh adopt WORK      save WORK's existing key as the project key
#   scripts/signing_key.sh seed WORK       put the project key into a new WORK before
#                                          pmbootstrap would generate one
#
# Key directory: $NURA_SIGNING_KEY_DIR, default ~/.config/nura-guacamole/abuild
# (outside the checkout: survives cleanups and fresh clones). It holds the
# private key: keep it private and backed up. Files inside WORK belong to the
# chroot user (uid 12345), so adopt/seed use sudo (override with NURA_SUDO).
set -euo pipefail

KEY_DIR="${NURA_SIGNING_KEY_DIR:-$HOME/.config/nura-guacamole/abuild}"
SUDO="${NURA_SUDO-sudo}"
CHROOT_UID=12345

die() { echo "ERROR: $*" >&2; exit 1; }
note() { echo "==> $*"; }

# key_name DIR: the single pmos@local key name in DIR, or empty
key_name() {
    local dir=$1
    local -a keys=()
    [[ -d "$dir" ]] || return 0
    # Listing needs no privilege when the directory is readable (pmbootstrap
    # makes config_abuild 755); fall back to sudo only when it is not, and let
    # a sudo failure show instead of reading as "no key".
    local -a lister=(find)
    [[ -r "$dir" && -x "$dir" ]] || lister=($SUDO find)
    local listing
    listing="$("${lister[@]}" "$dir" -maxdepth 1 -name '*.rsa' -printf '%f\n')" ||
        die "cannot list $dir (sudo needs a terminal to ask for its password)"
    mapfile -t keys < <(printf '%s' "$listing" | sed '/^$/d' | sort)
    ((${#keys[@]} <= 1)) || die "more than one private key in $dir: ${keys[*]}"
    ((${#keys[@]})) && echo "${keys[0]%.rsa}"
    return 0
}

check_pair() {   # dir name: private and public halves must match
    local dir=$1 name=$2
    [[ "$($SUDO openssl rsa -in "$dir/$name.rsa" -pubout 2>/dev/null)" = "$($SUDO cat "$dir/$name.rsa.pub")" ]] ||
        die "$dir/$name.rsa does not match $name.rsa.pub"
}

cmd=${1:-status}; work=${2:-}
case "$cmd" in
    status)
        project="$(key_name "$KEY_DIR")"
        echo "project key: ${project:-none} ($KEY_DIR)"
        if [[ -n "$work" ]]; then
            echo "work key:    $(key_name "$work/config_abuild") ($work/config_abuild)"
        fi
        ;;
    adopt)
        [[ -n "$work" ]] || die "usage: signing_key.sh adopt WORK"
        name="$(key_name "$work/config_abuild")"
        [[ -n "$name" ]] || die "$work/config_abuild has no key to adopt"
        project="$(key_name "$KEY_DIR")"
        if [[ -n "$project" ]]; then
            [[ "$project" = "$name" ]] || die "a different project key ($project) already exists in $KEY_DIR"
            note "project key is already $name"; exit 0
        fi
        check_pair "$work/config_abuild" "$name"
        mkdir -p "$KEY_DIR"; chmod 700 "$KEY_DIR"
        $SUDO cat "$work/config_abuild/$name.rsa" > "$KEY_DIR/$name.rsa"
        chmod 600 "$KEY_DIR/$name.rsa"
        $SUDO cat "$work/config_abuild/$name.rsa.pub" > "$KEY_DIR/$name.rsa.pub"
        chmod 644 "$KEY_DIR/$name.rsa.pub"
        check_pair "$KEY_DIR" "$name"
        note "adopted $name as the project signing key ($KEY_DIR); back it up"
        ;;
    seed)
        [[ -n "$work" ]] || die "usage: signing_key.sh seed WORK"
        project="$(key_name "$KEY_DIR")"
        if [[ -z "$project" ]]; then
            note "no project key yet; pmbootstrap will generate one (adopt it afterwards)"
            exit 0
        fi
        check_pair "$KEY_DIR" "$project"
        existing="$(key_name "$work/config_abuild")"
        if [[ -n "$existing" ]]; then
            [[ "$existing" = "$project" ]] ||
                die "$work already signs with $existing, not the project key $project; packages from it
       will not install on a phone that trusts $project. Use a fresh work directory, or adopt
       $existing instead (move $KEY_DIR aside first)."
            note "work directory already uses the project key $project"
            exit 0
        fi
        own=(); [[ -n "$SUDO" ]] && own=(-o "$CHROOT_UID" -g "$CHROOT_UID")
        $SUDO install -d -m 755 "${own[@]}" "$work/config_abuild" "$work/config_apk_keys"
        $SUDO install -m 600 "${own[@]}" "$KEY_DIR/$project.rsa" "$work/config_abuild/$project.rsa"
        $SUDO install -m 644 "${own[@]}" "$KEY_DIR/$project.rsa.pub" "$work/config_abuild/$project.rsa.pub"
        $SUDO install -m 644 "${own[@]}" "$KEY_DIR/$project.rsa.pub" "$work/config_apk_keys/$project.rsa.pub"
        printf 'PACKAGER_PRIVKEY="/home/pmos/.abuild/%s.rsa"\n' "$project" |
            $SUDO install -m 644 "${own[@]}" /dev/stdin "$work/config_abuild/abuild.conf"
        note "seeded the project key $project into $work"
        ;;
    -h|--help) sed -n '2,19p' "$0" ;;
    *) die "unknown command: $cmd (status, adopt, seed)" ;;
esac
