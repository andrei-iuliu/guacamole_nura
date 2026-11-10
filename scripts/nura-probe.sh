#!/bin/sh
# nura-probe.sh - read-only snapshot for Nura 7.0 rear-camera/video triage.
#
# Run ON THE PHONE (or pipe through the host wrapper).  This script only reads
# /proc, /sys, package metadata and existing logs.  It does not start or stop a
# recording, service or stream; does not load/unload modules; does not write
# regulators, actuators, EEPROMs or sysfs; does not run i2cdetect; and does not
# call adb/fastboot/sudo.  Run it as the normal desktop user.
#
#   sh nura-probe.sh > snapshot.txt
#   sh nura-probe.sh --watch 60 > snapshot-with-watch.txt   # sample while the
#                                                           # user records
#
# --watch SECONDS samples CPU/GPU/thermal/charger state once a second.  It does
# NOT control the camera.  SECONDS=0 samples until Ctrl-C.
#
# The optional NURA_ROOT prefix exists only so the mocked test suite can point
# the same script at a fixture tree.  Do not set it on the phone.
set -u

R=${NURA_ROOT:-}
HOMEDIR=${R}${HOME:-/home/user}
PROBE_VERSION=1
WATCH=0
WATCH_SET=0

usage() {
    cat <<'EOF'
Usage: sh nura-probe.sh [--watch SECONDS]

Read-only Nura camera/video/regression snapshot. Run on the phone, as the
normal user. It never starts a recording, stream, service, module or write.

  --watch SECONDS  sample CPU/GPU/thermal/charger once a second for SECONDS
                   (0 = until Ctrl-C).  Start it, then record with the app.
  -h, --help       show this help

Output uses @@SECTION@@ markers so the host analyzer can parse it.  Missing
tools/files print [MISSING]; permission failures print [DENIED]; no PASS/FAIL
verdict is produced by this script.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --watch)
            [ $# -ge 2 ] || { printf 'nura-probe: --watch needs SECONDS\n' >&2; exit 2; }
            WATCH=$2
            WATCH_SET=1
            shift 2
            ;;
        --watch=*)
            WATCH=${1#*=}
            WATCH_SET=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'nura-probe: unknown argument: %s (try --help)\n' "$1" >&2
            exit 2
            ;;
    esac
done

case "$WATCH" in
    ''|*[!0-9]*) printf 'nura-probe: --watch must be a non-negative integer\n' >&2; exit 2 ;;
esac

have() { command -v "$1" >/dev/null 2>&1; }
sec() { printf '\n@@%s@@\n' "$1"; }
kv() { printf '%s=%s\n' "$1" "$2"; }
missing() { printf '[MISSING] %s\n' "$*"; }
denied() { printf '[DENIED] %s\n' "$*"; }

show_file() {
    _path=$1
    if [ ! -e "${R}${_path}" ]; then
        missing "$_path"
        return 0
    fi
    if [ ! -r "${R}${_path}" ]; then
        denied "$_path"
        return 0
    fi
    cat "${R}${_path}" 2>&1 || printf '[EXIT %s] %s\n' "$?" "$_path"
}

num_or_na() {
    case "${1:-}" in
        ''|*[!0-9.-]*) printf 'NA' ;;
        *) printf '%s' "$1" ;;
    esac
}

str_or_na() {
    if [ -n "${1:-}" ]; then printf '%s' "$1"; else printf 'NA'; fi
}

printf '# nura-probe %s - read-only; no streams, modules, services or writes\n' "$PROBE_VERSION"

# ---------------------------------------------------------------- identity
sec META
kv probe_version "$PROBE_VERSION"
_now=$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null) || _now='[MISSING]'
kv collected_utc "$_now"
_epoch=$(date +%s 2>/dev/null) || _epoch='[MISSING]'
kv collected_epoch "$_epoch"
if have uname; then
    _rel=$(uname -r 2>/dev/null) || _rel='[MISSING]'
    _ver=$(uname -v 2>/dev/null) || _ver='[MISSING]'
else
    _rel='[MISSING]'
    _ver='[MISSING]'
fi
kv kernel_release "$_rel"
kv kernel_version "$_ver"
if [ -r "${R}/etc/os-release" ]; then
    _pretty=$(sed -n 's/^PRETTY_NAME=//p' "${R}/etc/os-release" 2>/dev/null | head -n 1 | sed 's/^"//;s/"$//')
    [ -n "$_pretty" ] || _pretty='[MISSING]'
else
    _pretty='[MISSING]'
fi
kv os_pretty "$_pretty"
if [ -r "${R}/proc/cmdline" ]; then
    _cmdline=$(tr '\n' ' ' < "${R}/proc/cmdline" 2>/dev/null)
else
    _cmdline='[MISSING]'
fi
kv boot_cmdline "$_cmdline"

sec KERNEL
if have uname; then
    printf '$ uname -a\n'
    uname -a 2>&1 || printf '[EXIT %s]\n' "$?"
else
    missing uname
fi
show_file /proc/uptime
show_file /proc/version

# ---------------------------------------------------------------- packages
sec PACKAGES
if have apk; then
    printf '$ apk info -v | grep -E "libcamera|pipewire|gstreamer|ffmpeg|mesa|nura"\n'
    if ! apk info -v 2>&1 | grep -Ei '^(libcamera|pipewire|gstreamer|gst-|ffmpeg|mesa|vulkan|nura)'; then
        printf '[NO_MATCH]\n'
    fi
else
    missing apk
fi

# ---------------------------------------------------------------- modules
sec MODULES
if have lsmod; then
    printf '$ lsmod | grep -Ei camera/audio modules\n'
    if ! lsmod 2>&1 | grep -Ei 'camcc|camss|cci|imx586|imx481|s5k3m5|ak737|lc898|v4l2|videodev|venus|apr|q6|slim|wcd|tfa|rpmh'; then
        printf '[NO_MATCH]\n'
    fi
elif [ -r "${R}/proc/modules" ]; then
    if ! grep -Ei 'camcc|camss|cci|imx586|imx481|s5k3m5|ak737|lc898|v4l2|videodev|venus|apr|q6|slim|wcd|tfa|rpmh' "${R}/proc/modules"; then
        printf '[NO_MATCH]\n'
    fi
else
    missing lsmod
fi

# ---------------------------------------------------------------- cci module
sec CCI_MODULE
_cci=''
if have modinfo; then
    _cci=$(modinfo -n i2c_qcom_cci 2>/dev/null) || _cci=''
fi
if [ -z "$_cci" ] && [ -n "$_rel" ] && [ "$_rel" != '[MISSING]' ]; then
    for _ext in ko.zst ko.xz ko; do
        _cand="/usr/lib/modules/${_rel}/kernel/drivers/i2c/busses/i2c-qcom-cci.${_ext}"
        if [ -e "${R}${_cand}" ]; then
            _cci=$_cand
            break
        fi
    done
fi
if [ -n "$_cci" ]; then
    kv path "$_cci"
    if [ -e "${R}${_cci}" ]; then
        _size=$(wc -c < "${R}${_cci}" 2>/dev/null | tr -d ' ')
        kv size "${_size:-[MISSING]}"
        if have sha256sum; then
            _sha=$(sha256sum "${R}${_cci}" 2>/dev/null | awk '{print $1}')
            kv sha256 "${_sha:-[MISSING]}"
        else
            missing sha256sum
            kv sha256 '[MISSING]'
        fi
    else
        missing "$_cci"
    fi
else
    missing 'i2c_qcom_cci module path'
    kv path '[MISSING]'
fi

# ---------------------------------------------------------------- kernel log
sec DMESG
if have dmesg; then
    if dmesg >/dev/null 2>&1; then
        printf '$ dmesg | grep -Ei camera/audio/wifi/bt/charger/suspend/thermal\n'
        _out=$(dmesg 2>/dev/null | grep -Ei 'camss|cci|imx586|imx481|s5k3m5|ak737|lc898|camcc|rpmh|venus|v4l2|media|ath10k|wcn|bluetooth|hci|tfa|tas|wcd|soundwire|slim|q6|apr|charger|smb|battery|bq27|suspend|resume|thermal|throttl|oops|bug|watchdog|error|fail' | tail -400)
        if [ -n "$_out" ]; then
            printf '%s\n' "$_out"
        else
            printf '[NO_MATCH]\n'
        fi
    else
        denied 'dmesg (kernel.dmesg_restrict or permission)'
        printf 'hint: user-run: sudo dmesg > dmesg.txt\n'
    fi
else
    missing dmesg
fi

sec POWER_DOMAINS
show_file /sys/kernel/debug/pm_genpd/pm_genpd_summary

# ---------------------------------------------------------------- media graph
sec MEDIA
if have v4l2-ctl; then
    printf '$ v4l2-ctl --list-devices\n'
    v4l2-ctl --list-devices 2>&1 || printf '[EXIT %s]\n' "$?"
else
    missing v4l2-ctl
fi
if have media-ctl; then
    _found=0
    for _m in "${R}"/dev/media*; do
        [ -e "$_m" ] || continue
        _found=1
        printf '$ media-ctl -d %s -p\n' "${_m#"$R"}"
        media-ctl -d "$_m" -p 2>&1 | head -n 200 || printf '[EXIT %s]\n' "$?"
    done
    [ "$_found" -eq 1 ] || missing '/dev/media*'
else
    missing media-ctl
fi
for _v in "${R}"/sys/class/video4linux/*; do
    [ -d "$_v" ] || continue
    _n=${_v##*/}
    _name=$(cat "$_v/name" 2>/dev/null) || _name='[MISSING]'
    printf 'VIDEO4LINUX %s name=%s\n' "$_n" "$_name"
done

# ---------------------------------------------------------------- processes
sec CAMERA_PROCESSES
printf '# process snapshot (read-only)\n'
if have ps; then
    if ps -eo pid,ppid,pcpu,pmem,etime,comm >/dev/null 2>&1; then
        ps -eo pid,ppid,pcpu,pmem,etime,comm 2>/dev/null | head -n 40
    else
        ps 2>/dev/null | head -n 60
    fi
else
    missing ps
fi
printf '\n# camera device holders and camera-ish processes\n'
_seen=' '
for _p in "${R}"/proc/[0-9]*; do
    [ -d "$_p" ] || continue
    _pid=${_p##*/}
    [ "$_pid" = "$$" ] && continue
    _comm=$(cat "$_p/comm" 2>/dev/null) || _comm='?'
    _cmd=$(tr '\0' ' ' < "$_p/cmdline" 2>/dev/null) || _cmd=''
    _devs=''
    for _fd in "$_p"/fd/*; do
        [ -e "$_fd" ] || [ -L "$_fd" ] || continue
        _tgt=$(readlink "$_fd" 2>/dev/null) || continue
        case "$_tgt" in
            /dev/video*|/dev/media*|/dev/v4l-subdev*) _devs="$_devs,$_tgt" ;;
        esac
    done
    _devs=$(printf '%s\n' "$_devs" | tr ',' '\n' | sed '/^$/d' | sort -u | tr '\n' ',')
    _devs=${_devs%,}
    _interesting=0
    [ -n "$_devs" ] && _interesting=1
    case "$_comm $_cmd" in
        *camera*|*nura*|*pipewire*|*wireplumber*|*gst*|*cam-rear*|*snapshot*|*cheese*|*clapper*|*mpv*)
            _interesting=1
            ;;
    esac
    [ "$_interesting" -eq 1 ] || continue
    case "$_seen" in *" $_pid "*) continue ;; esac
    _seen="$_seen$_pid "
    _exe=$(readlink "$_p/exe" 2>/dev/null) || _exe='[UNKNOWN]'
    _exe=${_exe% (deleted)}
    printf 'PROCESS pid=%s comm=%s exe=%s devices=%s\n' \
        "$_pid" "$(printf '%s' "$_comm" | tr ' ' '_')" "$_exe" "${_devs:-none}"
    printf 'CMDLINE %s\n' "$_cmd"
    # Process mappings identify the stack without reading credential-bearing environments.
    if [ -r "$_p/maps" ]; then
        if ! awk 'NF >= 6 {print $6}' "$_p/maps" 2>/dev/null |
            grep -Ei 'libcamera|pipewire|spa-|gstreamer|gst|v4l2|libdrm|libEGL|libGLES|vulkan|mesa' |
            sed 's/ (deleted)$//' | sort -u | sed 's/^/MAP /'; then
            printf 'MAP [NO_MATCH]\n'
        fi
    else
        printf 'MAP [DENIED]\n'
    fi
done
[ "$_seen" = ' ' ] && printf '[NOT_OBSERVED] no camera device holder or camera-ish process matched\n'

# ---------------------------------------------------------------- libraries
sec LIBCAMERA_LIBS
_found=' '
for _p in "${R}"/proc/[0-9]*; do
    [ -r "$_p/maps" ] || continue
    for _lib in $(awk 'NF >= 6 {print $6}' "$_p/maps" 2>/dev/null |
        grep -Ei 'libcamera|spa-libcamera' | sed 's/ (deleted)$//' | sort -u); do
        case "$_found" in *" $_lib "*) continue ;; esac
        _found="$_found$_lib "
        printf 'LOADED %s\n' "$_lib"
        _real=$(readlink -f "${R}${_lib}" 2>/dev/null) || _real='[UNKNOWN]'
        printf '  realpath=%s\n' "$_real"
        if [ -f "${R}${_lib}" ]; then
            _sz=$(wc -c < "${R}${_lib}" 2>/dev/null | tr -d ' ')
            printf '  size=%s\n' "${_sz:-[MISSING]}"
            if have sha256sum; then
                _h=$(sha256sum "${R}${_lib}" 2>/dev/null | awk '{print $1}')
                printf '  sha256=%s\n' "${_h:-[MISSING]}"
            else
                printf '  sha256=[MISSING] (sha256sum)\n'
            fi
            if have apk; then
                _o=$(apk info -W "${R}${_lib}" 2>/dev/null | head -n 1)
                [ -n "$_o" ] && printf '  owner=%s\n' "$_o"
            fi
        else
            printf '  file=[MISSING]\n'
        fi
    done
done
[ "$_found" = ' ' ] && printf '[NOT_OBSERVED] no libcamera or spa-libcamera library mapped\n'
printf '\n# available (not necessarily loaded)\n'
for _lib in "${R}"/opt/nura-rear-camera/lib/libcamera.so* \
            "${R}"/usr/lib/libcamera.so* \
            "${R}"/usr/lib/spa-0.2/libcamera/libspa-libcamera.so*; do
    [ -e "$_lib" ] || continue
    _real=$(readlink -f "$_lib" 2>/dev/null) || _real="$_lib"
    _sz=$(wc -c < "$_lib" 2>/dev/null | tr -d ' ')
    printf 'AVAILABLE %s realpath=%s size=%s\n' \
        "${_lib#"$R"}" "${_real#"$R"}" "${_sz:-[MISSING]}"
done

# ---------------------------------------------------------------- pipewire
sec PIPEWIRE
if have ps && ps w 2>/dev/null | grep -q '[p]ipewire'; then
    if have wpctl; then
        wpctl status 2>&1 | head -n 80
    else
        missing wpctl
    fi
    if have pw-top; then
        if have timeout; then
            timeout 5 pw-top -b -n 1 2>&1 | head -n 60
        else
            pw-top -b -n 1 2>&1 | head -n 60
        fi
    else
        missing pw-top
    fi
else
    printf '[NOT_OBSERVED] no pipewire process\n'
fi

# ---------------------------------------------------------------- audio
sec AUDIO
show_file /proc/asound/cards
show_file /proc/asound/pcm
show_file /proc/asound/devices
if have aplay; then
    aplay -l 2>&1 | head -n 40
else
    missing aplay
fi
if have arecord; then
    arecord -l 2>&1 | head -n 40
else
    missing arecord
fi
if have amixer; then
    amixer scontrols 2>&1 | head -n 40
else
    missing amixer
fi
for _d in "${R}"/sys/bus/i2c/devices/*; do
    [ -e "$_d/name" ] || continue
    _n=$(cat "$_d/name" 2>/dev/null) || continue
    case "$_n" in
        *tfa*|*tas*|*wcd*|*aw88*|*cs35*)
            printf 'AUDIO_I2C %s name=%s\n' "${_d##*/}" "$_n"
            ;;
    esac
done

# ---------------------------------------------------------------- wifi
sec WIFI
if have iw; then
    iw dev 2>&1 | head -n 40
else
    missing iw
fi
show_file /proc/net/wireless
if have rfkill; then
    rfkill list 2>&1 | head -n 40
else
    missing rfkill
fi
for _d in "${R}"/sys/class/net/*; do
    [ -d "$_d" ] || continue
    _n=${_d##*/}
    [ "$_n" = lo ] && continue
    _op=$(cat "$_d/operstate" 2>/dev/null) || _op='[MISSING]'
    printf 'NET %s operstate=%s\n' "$_n" "$_op"
done
if have nmcli; then
    if have systemctl && systemctl is-active --quiet NetworkManager 2>/dev/null; then
        nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device status 2>&1 | head -n 30
    else
        printf '[SKIPPED] nmcli (NetworkManager not confirmed active; avoids D-Bus activation)\n'
    fi
else
    missing nmcli
fi

# ---------------------------------------------------------------- bluetooth
sec BLUETOOTH
if have rfkill; then
    rfkill list 2>&1 | head -n 40
else
    missing rfkill
fi
if have hciconfig; then
    hciconfig -a 2>&1 | grep -E '^(hci|BD Address|UP|DOWN|RX|TX|Link mode|Packet type)' | head -n 40
else
    missing hciconfig
fi
for _d in "${R}"/sys/class/bluetooth/*; do
    [ -d "$_d" ] || continue
    _n=${_d##*/}
    _addr=$(cat "$_d/address" 2>/dev/null) || _addr='[MISSING]'
    printf 'HCI %s address=%s\n' "$_n" "$_addr"
done
printf '# functional BT state requires user observation; no bluetoothctl/D-Bus call is made\n'

# ---------------------------------------------------------------- charging
sec CHARGING
for _ps in "${R}"/sys/class/power_supply/*; do
    [ -d "$_ps" ] || continue
    _n=${_ps##*/}
    printf 'SUPPLY %s\n' "$_n"
    for _a in type online status capacity voltage_now current_now temp health \
              charge_control_limit input_current_limit constant_charge_current; do
        _f="$_ps/$_a"
        [ -e "$_f" ] || continue
        if [ -r "$_f" ]; then
            printf '  %s=%s\n' "$_a" "$(cat "$_f" 2>/dev/null)"
        else
            printf '  %s=[DENIED]\n' "$_a"
        fi
    done
done
show_file /run/guacamole/charger
for _p in "${R}"/sys/class/typec/port*; do
    [ -d "$_p" ] || continue
    printf 'TYPEC %s\n' "${_p##*/}"
    for _a in data_role power_role port_type vconn_role; do
        _f="$_p/$_a"
        [ -e "$_f" ] || continue
        if [ -r "$_f" ]; then
            printf '  %s=%s\n' "$_a" "$(cat "$_f" 2>/dev/null)"
        else
            printf '  %s=[DENIED]\n' "$_a"
        fi
    done
done

# ---------------------------------------------------------------- suspend
sec SUSPEND
for _f in /sys/power/state /sys/power/mem_sleep /sys/power/wakeup_count /sys/power/autosleep; do
    show_file "$_f"
done
for _f in "${R}"/sys/power/suspend_stats/*; do
    [ -e "$_f" ] || continue
    if [ -r "$_f" ]; then
        printf '%s=%s\n' "${_f##*/}" "$(cat "$_f" 2>/dev/null)"
    else
        printf '%s=[DENIED]\n' "${_f##*/}"
    fi
done
if have journalctl; then
    if journalctl -n 1 --no-pager >/dev/null 2>&1; then
        _out=$(journalctl -k -b --no-pager -n 5000 2>/dev/null |
            grep -Ei 'PM: suspend|PM: resume|suspend entry|suspend exit|Freezing|abort' |
            tail -n 100)
        if [ -n "$_out" ]; then
            printf '%s\n' "$_out"
        else
            printf '[NO_MATCH]\n'
        fi
    else
        denied journalctl
    fi
else
    missing journalctl
fi

# ---------------------------------------------------------------- load/temps
sec THERMAL
show_file /proc/loadavg
show_file /proc/pressure/cpu
show_file /proc/pressure/io
for _p in "${R}"/sys/devices/system/cpu/cpufreq/policy*; do
    [ -d "$_p" ] || continue
    _n=${_p##*/}
    _cur=$(cat "$_p/scaling_cur_freq" 2>/dev/null) ||
        _cur=$(cat "$_p/cpuinfo_cur_freq" 2>/dev/null) || _cur='[MISSING]'
    _min=$(cat "$_p/scaling_min_freq" 2>/dev/null) || _min='[MISSING]'
    _max=$(cat "$_p/scaling_max_freq" 2>/dev/null) || _max='[MISSING]'
    _gov=$(cat "$_p/scaling_governor" 2>/dev/null) || _gov='[MISSING]'
    printf 'CPUFREQ %s cur=%s min=%s max=%s governor=%s\n' "$_n" "$_cur" "$_min" "$_max" "$_gov"
done
for _d in "${R}"/sys/class/devfreq/*; do
    [ -d "$_d" ] || continue
    _n=$(cat "$_d/name" 2>/dev/null) || _n=${_d##*/}
    _cur=$(cat "$_d/cur_freq" 2>/dev/null) || _cur='[MISSING]'
    _min=$(cat "$_d/min_freq" 2>/dev/null) || _min='[MISSING]'
    _max=$(cat "$_d/max_freq" 2>/dev/null) || _max='[MISSING]'
    _gov=$(cat "$_d/governor" 2>/dev/null) || _gov='[MISSING]'
    printf 'DEVFREQ %s cur=%s min=%s max=%s governor=%s\n' "$_n" "$_cur" "$_min" "$_max" "$_gov"
done
for _z in "${R}"/sys/class/thermal/thermal_zone*; do
    [ -d "$_z" ] || continue
    _n=${_z##*/}
    _t=$(cat "$_z/type" 2>/dev/null) || _t='[MISSING]'
    _v=$(cat "$_z/temp" 2>/dev/null) || _v='[MISSING]'
    printf 'THERMAL %s type=%s temp_mC=%s\n' "$_n" "$(printf '%s' "$_t" | tr ' ' '_')" "$_v"
done
if [ -d "${R}/sys/class/kgsl/kgsl-3d0" ]; then
    show_file /sys/class/kgsl/kgsl-3d0/gpuclk
    show_file /sys/class/kgsl/kgsl-3d0/gpu_busy_percentage
    show_file /sys/class/kgsl/kgsl-3d0/devfreq/cur_freq
fi

sec TOP
if have top; then
    if top -b -n 1 >/dev/null 2>&1; then
        top -b -n 1 2>&1 | head -n 35
    else
        printf '[UNAVAILABLE] top -b -n 1\n'
    fi
else
    missing top
fi

# ---------------------------------------------------------------- watch
if [ "$WATCH_SET" -eq 1 ]; then
    sec WATCH
    printf '# epoch load1 load5 psi_cpu_some psi_io_some cpu_max_khz cpu_gov gpu_hz gpu_gov thermal_max_c batt_status batt_capacity usb_online input_limit_ua\n'
    _start=$(date +%s 2>/dev/null) || _start=0
    _stop=0
    trap '_stop=1' INT TERM
    while [ "$_stop" -eq 0 ]; do
        _epoch=$(date +%s 2>/dev/null) || _epoch=NA
        _load=$(awk '{print $1, $2}' "${R}/proc/loadavg" 2>/dev/null)
        _l1=${_load%% *}
        _l5=${_load##* }
        _pc=$(sed -n 's/^some avg10=\([0-9.]*\).*/\1/p' "${R}/proc/pressure/cpu" 2>/dev/null | head -n 1)
        _pio=$(sed -n 's/^some avg10=\([0-9.]*\).*/\1/p' "${R}/proc/pressure/io" 2>/dev/null | head -n 1)
        _cpu=NA
        _cgov=NA
        for _p in "${R}"/sys/devices/system/cpu/cpufreq/policy*; do
            [ -d "$_p" ] || continue
            _v=$(cat "$_p/scaling_cur_freq" 2>/dev/null) ||
                _v=$(cat "$_p/cpuinfo_cur_freq" 2>/dev/null) || _v=''
            case "$_v" in
                ''|*[!0-9]*) ;;
                *)
                    if [ "$_cpu" = NA ] || [ "$_v" -gt "$_cpu" ]; then
                        _cpu=$_v
                    fi
                    ;;
            esac
            if [ "$_cgov" = NA ]; then
                _g=$(cat "$_p/scaling_governor" 2>/dev/null) || _g=''
                [ -n "$_g" ] && _cgov=$_g
            fi
        done
        _gpu=NA
        _ggov=NA
        for _d in "${R}"/sys/class/devfreq/*; do
            [ -d "$_d" ] || continue
            _n=$(cat "$_d/name" 2>/dev/null) || _n=''
            case "$_n" in
                *gpu*|*kgsl*)
                    _gpu=$(cat "$_d/cur_freq" 2>/dev/null) || _gpu=NA
                    _ggov=$(cat "$_d/governor" 2>/dev/null) || _ggov=NA
                    break
                    ;;
            esac
        done
        _tmax=NA
        for _z in "${R}"/sys/class/thermal/thermal_zone*; do
            [ -d "$_z" ] || continue
            _v=$(cat "$_z/temp" 2>/dev/null) || continue
            case "$_v" in
                ''|*[!0-9-]*) ;;
                *)
                    if [ "$_tmax" = NA ] || [ "$_v" -gt "$_tmax" ]; then
                        _tmax=$_v
                    fi
                    ;;
            esac
        done
        case "$_tmax" in
            ''|NA|*[!0-9-]*) _tmax=NA ;;
            *) _tmax=$((_tmax / 1000)) ;;
        esac
        _bs=NA
        _bc=NA
        _usb=NA
        _il=NA
        for _ps in "${R}"/sys/class/power_supply/*; do
            [ -d "$_ps" ] || continue
            _type=$(cat "$_ps/type" 2>/dev/null) || _type=''
            case "$_type" in
                Battery)
                    if [ "$_bs" = NA ]; then
                        _bs=$(cat "$_ps/status" 2>/dev/null | tr ' ' '_') || _bs=NA
                    fi
                    if [ "$_bc" = NA ]; then
                        _bc=$(cat "$_ps/capacity" 2>/dev/null) || _bc=NA
                    fi
                    ;;
                USB|Mains)
                    _on=$(cat "$_ps/online" 2>/dev/null) || _on=NA
                    [ "$_on" = 1 ] && _usb=1
                    [ "$_on" = 0 ] && [ "$_usb" = NA ] && _usb=0
                    ;;
            esac
            if [ "$_il" = NA ] && [ -r "$_ps/input_current_limit" ]; then
                _il=$(cat "$_ps/input_current_limit" 2>/dev/null) || _il=NA
            fi
        done
        printf '%s %s %s %s %s %s %s %s %s %s %s %s %s %s\n' \
            "$(num_or_na "$_epoch")" "$(num_or_na "$_l1")" "$(num_or_na "$_l5")" \
            "$(num_or_na "$_pc")" "$(num_or_na "$_pio")" "$(num_or_na "$_cpu")" \
            "$(str_or_na "$_cgov")" "$(num_or_na "$_gpu")" "$(str_or_na "$_ggov")" \
            "$(num_or_na "$_tmax")" "$(str_or_na "$_bs")" "$(num_or_na "$_bc")" \
            "$(num_or_na "$_usb")" "$(num_or_na "$_il")"
        _now=$(date +%s 2>/dev/null) || _now=$_start
        if [ "$WATCH" -gt 0 ] && [ $((_now - _start)) -ge "$WATCH" ]; then
            break
        fi
        sleep 1
    done
    trap - INT TERM
    printf '@@WATCH_END@@\n'
fi

# ---------------------------------------------------------------- recordings
sec RECORDINGS
for _d in "${HOMEDIR}/Videos" "${HOMEDIR}/Recordings" "${HOMEDIR}/Pictures"; do
    [ -d "$_d" ] || continue
    printf 'DIR %s\n' "${_d#"$R"}"
    ls -lt "$_d" 2>&1 | head -n 30
done
if have find; then
    printf '$ find %s -maxdepth 3 -type f (video extensions)\n' "${HOMEDIR#"$R"}"
    find "$HOMEDIR" -maxdepth 3 -type f \
        \( -iname '*.mp4' -o -iname '*.mov' -o -iname '*.mkv' -o -iname '*.webm' -o -iname '*.m4v' \) \
        2>/dev/null | head -n 80
else
    missing find
fi

# ---------------------------------------------------------------- journal
sec JOURNAL
if have journalctl; then
    if journalctl -n 1 --no-pager >/dev/null 2>&1; then
        _out=$(journalctl --since '15 min ago' --no-pager -n 3000 2>/dev/null |
            grep -Ei 'camera|libcamera|pipewire|spa|gst|v4l2|camss|cci|venus|thermal|throttl|encode|drop|underrun|overflow' |
            tail -n 300)
        if [ -n "$_out" ]; then
            printf '%s\n' "$_out"
        else
            printf '[NO_MATCH]\n'
        fi
    else
        denied journalctl
    fi
else
    missing journalctl
fi

printf '\n@@END@@\n'
