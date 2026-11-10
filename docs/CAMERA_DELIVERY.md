# Rear-camera candidate delivery runbook

Candidate release: **`7.0.0-sm8150-rear1`**. This is the delivery runbook and
audit of the current prepare/serve/IP/boot path. Documentation only: no phone,
server, sudo, pmbootstrap or network action was performed.

Use the **candidate** checkout, never `nura-sm8150-7-0`, the 6.17 tree or an
arbitrary `build/rear-camera` copy.

`camera_build.sh`, `camera_serve.sh`, `camera_boot.sh`, `camera_verify.sh` and
`camera_usb_net.sh` are the new interface in this checkout (briefs G–J). They
are additive: `serve_rear_camera.sh`, `boot_rear_camera.sh` and the other Luna
launch scripts are untouched. `NEW` blocks use the new scripts; `CURRENT`
blocks remain the proven fallback. The crash analysis behind this runbook is in
`docs/CCI_PROBE_FAULT.md`.

## 1. Pipeline map and staleness edges

```
device/testing/rear-camera/linux-postmarketos-qcom-sm8150 (+ patches 0001..0031)
  │ E1 bash scripts/build_rear_camera_kernel.sh           (host, rootless)
  ▼
.work/rear-camera/output + build/rear-camera/{Image,kernel-package}
  │ E2 bash scripts/rootfs_prepare_rear_camera.sh        (user, pmbootstrap)
  ▼
envkernel APK -> E3 pmbootstrap install -> rootfs image   (user, pmbootstrap)
  │                                          │ E4 pmbootstrap export
  │ E5 INITRAMFS=... build_bootimg...         ▼
  ▼                                 .work/rear-camera/rootfs/export/
build/rear-camera/boot.img             {initramfs,oneplus-guacamole.img}
  │ E6 fastboot boot (never flash)             │ E7 netboot serve
  ▼                                            ▼
phone kernel + initramfs            images_netboot/oneplus-guacamole.img
  │ E9 nbd-client :10001                      │ E8 nbd-server 172.16.42.2:10001
  └─────────── phone rootfs (NBD p2) ◄────────┘
                   │
                   └─ E10 ssh user@172.16.42.1
```

| Edge | Command | Artifact | Validation | Stale when |
| --- | --- | --- | --- | --- |
| E1 | `build_rear_camera_kernel.sh` | `output/`, `Image`, `kernel-package/` | recipe SHA-512s; `.rear-source.sha256`; release; envelope | recipe/patches changed after the source marker |
| E2 | `rootfs_prepare_rear_camera.sh` | `linux-...-7.0.0_pYYYYMMDDHHMMSS-r0.apk` | exactly one APK; vermagic; `previous-kernels/` | envkernel not rerun; multiple candidate APKs |
| E3 | `pmbootstrap install --add <exact>` | `chroot_native/home/pmos/rootfs/oneplus-guacamole.img` | one module tree; manifest CCI module; hook 10001 | install not rerun after E2 |
| E4 | `pmbootstrap export` | `export/{initramfs,oneplus-guacamole.img}` | release/port checker; image hash | export symlinks from an older run |
| E5 | `INITRAMFS=... build_bootimg_rear_camera.sh` | `build/rear-camera/boot.img` | package/envelope checks; unpack header; CCI nodes | boot.img older than its inputs; isolation image packed over combined |
| E6 | `boot_rear_camera.sh` / `camera_boot.sh boot` | phone kernel/initramfs | hash/release/device; boot log | any `boot.img`, including a stale one |
| E7 | `camera_serve.sh` refresh (stopped cache only) | `images_netboot/oneplus-guacamole.img` | manifest CCI/rootfs; staged copy inspected before atomic rename; refused while 10001 is in use | cache from an older install, or a live server owning the cache |
| E8 | `nbd-server 172.16.42.2@10001 <cache> -d` | TCP 10001 | `ss`; cmdline; pre-exposure record | a previous server/port collision |
| E9 | initramfs `hooks/netboot.sh` | phone rootfs | `uname`, module hash, dmesg, `media-ctl` | stale cache, wrong port, missing IP/UFW rule |
| E10 | `ssh user@172.16.42.1` | session | `-4`, timeout, host-key policy | host key changed; address flushed |

## 2. Current failure audit, ranked by delivery risk

**P0 — the served cache is the pre-fix image.**
`scripts/rootfs_prepare_rear_camera.sh:72` exports the rootfs and `:84` packs
`boot.img`, but never refreshes `images_netboot`.
`pmbootstrap/pmb/commands/netboot.py:25-36` copies only if the destination is
missing or `--replace`, so the 12:29 serve reused the 12:08 cache:

```
12:01:47  APK ..._p20261110120144 (pre-fix CCI 8920 / 6f61b608...)
12:11:12  log.txt:31693  cp /home/pmos/rootfs/... -> images_netboot  (only copy)
12:23:49  APK ..._p20261110122346 (fixed CCI 8950 / 4cd35f05...)
12:26:40  log.txt:39597  install of the fixed APK
12:27:07  chroot rootfs image  4e464ab7...  (8,049,917,952 B)
12:29:14  log.txt:40077  serve --port 10001, no "Copied device image" line
```

Now: source `4e464ab7...`, served `3707af89...`, same size. The cache mtime
(12:29) is not its copy time; the log is the evidence. The pre-fix module is in
every archived kernel APK from 11:45 to 12:01.

**P0 — `boot_rear_camera.sh` boots any image.** `:5` hardcodes the path;
`:10-12` checks only existence; `:14-18` has no device/serial/product check;
`:20` is one un-timed `exec fastboot boot` with no server check. It cannot
detect a stale image or the 12:08 cache.

**P1 — `serve_rear_camera.sh` cannot prove what it serves.** `:2` claims "no
image replacement"; `:9` hardcodes `enp19s0u4u4`; `:11` waits forever with no
timeout/candidate list; `:13-15` does not check foreign `172.16.42.2/24` or
`ip route get`; `:16-18` has no `--replace`, no image verification, no READY,
and `exec`s away cleanup.

**P1 — no fixed-module check.** `check_rear_camera_package.py:60-69` checks the
installed rootfs module release/vermagic, not the CCI hash;
`check_initramfs_release.py` is listing-only. The initramfs has
`hooks/netboot.sh` and only its small module subset — **no camera/CAMSS/CCI
modules**; the CCI module is in the rootfs image. A boot.img-only check cannot
catch the bug.

**P2 — `ssh_phone.sh:4` is a bare `ssh user@172.16.42.1`.** No `-4`,
`ConnectTimeout`, retry or project-local known-hosts policy.

**P2 — path leakage.** `packaging-inputs.json` records live-worktree absolute
`rootfs`/APK paths; `.work/rear-camera/rootfs/pmbootstrap.cfg:13-14` names the
live `work`/`apts`. `serve_rear_camera.sh` overrides both with `-w`/`-p`, but
manual pmbootstrap calls without them use the live worktree.
`export/dtbs/...` is an absolute symlink to the live worktree; the image and
initramfs symlinks are relative. **P3 — doc contradictions:** see §6.

## 3. Safe runbook

```bash
export CAMERA_ROOT=guacamole_nura-workspace/nura-rear-cameras-7-0
cd "$CAMERA_ROOT"
```

Fish: `set -gx CAMERA_ROOT /home/user/.../nura-rear-cameras-7-0`.

### 3.1 Preflight (host, rootless)

`NEW`: `bash scripts/camera_build.sh check` prints PASS/FAIL; `DELIVERY.json`
is written only by `camera_build.sh manifest`. Inspect its identity with
`jq '{release,worktree,boot_img,rootfs_image,i2c_qcom_cci}' build/rear-camera/DELIVERY.json`
(or `python3 -m json.tool`).

`CURRENT`:

```bash
cd "$CAMERA_ROOT"
test -f device/testing/rear-camera/linux-postmarketos-qcom-sm8150/APKBUILD
cat build/rear-camera/kernel-package/usr/share/kernel/postmarketos-qcom-sm8150/kernel.release
python3 scripts/check_rear_camera_package.py \
  --initramfs .work/rear-camera/rootfs/export/initramfs
python3 scripts/check_boot_envelope.py --image build/rear-camera/Image \
  --kernel-offset 0x8000 --dtb-offset 0x03000000 --ramdisk-offset 0x01000000 \
  --ramdisk-size "$(stat -c %s .work/rear-camera/rootfs/export/initramfs)"
python3 -m unittest discover -s tests -p 'test_rear_camera*.py' -v
sha256sum .work/rear-camera/pmbootstrap-work/chroot_rootfs_oneplus-guacamole/usr/lib/modules/7.0.0-sm8150-rear1/kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst
```

Expected: release `7.0.0-sm8150-rear1`; hook 10001; envelope margin `0x508000`;
27 focused tests (1 skip without `libcamera-v0.7.2.tar.gz`); CCI `8950` /
`4cd35f05...`. The last hash is the installed rootfs tree the image was exported
from; tie it to the image with `packaging-inputs.json`/`DELIVERY.json` (the new
`camera_serve.sh --check` verifies the image itself). Current identity (12:27;
refresh from `DELIVERY.json` after any rebuild):

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `build/rear-camera/boot.img` | 57393152 | `0e19bbb5524b8b6b7ae7cc024327375a14678796298c1e8110a4f28cffe0cec9` |
| `Image` | 44411392 | `4e798093d18fceb8d4708565e85203866ed80db06b2eef9ec8f00cf12c9883d4` |
| kernel-package DTB | 114058 | `f9e1ae14950c88858487feefb1425775c7d4bb4fecd276af466770460427a74d` |
| `export/initramfs` | 12771769 | `476066665394df2626122c2e4398819f2bcba0abd9f836602d9c628377c99cad` |
| rootfs image | 8049917952 | `4e464ab75a0ef1d33a8824643f3e0f3da942fbfb5b2c5bb2869092018836b935` |
| fixed CCI module (historical observation) | 8950 | `4cd35f05ff873700c43baad265efd0bf73166352baac79f88d4aeca456fb27e4` |
| **stale served cache** | 8049917952 | `3707af89e2b41d27984cb18deb79fbabcafa9a3e4cd9282e48fdcfb7b866a39a` |

`boot.img` unpacks to kernel `4e798093...`, ramdisk `47606666...`, DTB
`3992b3c6...`, header v2/page 4096, DTB address `0x03000000`, cmdline
`pmos.root=/dev/nbd0p2 pmos.boot=/dev/nbd0p1`. The combined DTB has
`cci@ac4a000`, `cci@ac4b000`; CCI1 has only `i2c-bus@1` (master 1) with
`sony,imx481` and `asahi-kasei,ak7374`.

### 3.2 Build (host-only, rootless)

`NEW`: `bash scripts/camera_build.sh all --jobs N` runs
check→kernel→libcamera→tests→bootimg→manifest; `bootimg` requires
`INITRAMFS=...` and refuses a stale image.

`CURRENT`: `bash scripts/build_rear_camera_kernel.sh`, then
`bash scripts/build_rear_camera_libcamera.sh`, then the unit tests above. Any
rebuild changes hashes: rerun packaging and `manifest` before serve/boot.

### 3.3 Package the rootfs (USER-RUN, privileged)

```bash
bash scripts/rootfs_prepare_rear_camera.sh
```

This invokes pmbootstrap (`sudo`, chroots, image build), installs the exact
APKs, exports the initramfs/rootfs image and packs `boot.img`; logs are under
`.work/rear-camera/rootfs/package-*.log`. **It does not refresh
`images_netboot`** — §3.4 is mandatory. Do not package while the candidate
rootfs is being served.

### 3.4 Serve on port 10001 (HOST, USER-RUN; sudo for cache/iface)

`NEW`:

```bash
bash scripts/camera_serve.sh --check
bash scripts/camera_serve.sh --dry-run
bash scripts/camera_serve.sh     # auto-refresh, verify, print READY
```

READY must show worktree, release, boot.img/rootfs/CCI hashes, interface, host
IP, port 10001 and the SSH command. It must never touch port 10000. The script
refuses to refresh while port 10001 is in use, copies to a staging file,
verifies that file, and only then renames it over the stopped cache. It writes
`.work/delivery/served-image.json` before starting the server; post-boot checks
use that record instead of re-hashing the phone-mutable image. See
`docs/DELIVERY_LIFECYCLE.md` §2.

`CURRENT` — fallback only. This path has no refresh or identity gate and is
superseded for verified delivery; use it deliberately, not as the default
(`docs/DELIVERY_LIFECYCLE.md` §1).

```bash
# terminal 1: stop any old candidate server (Ctrl-C) first
cd "$CAMERA_ROOT"
sudo rm -f .work/rear-camera/pmbootstrap-work/images_netboot/oneplus-guacamole.img
bash scripts/serve_rear_camera.sh
```

Different USB port: `env NURA_USB_IFACE=<iface> bash scripts/serve_rear_camera.sh`
(fish uses the same `env NAME=value command`). The script waits forever: if
only the wait line appears, Ctrl-C, run `ip -br link`, retry. No interface is
normal while the phone is in Android/fastboot; the gadget appears at initramfs
time. Verify before booting (terminal 2; wait for `NOTE: Copied device image
...` and for `nbd-server` to start):

```bash
SRC="$CAMERA_ROOT/.work/rear-camera/pmbootstrap-work/chroot_native/home/pmos/rootfs/oneplus-guacamole.img"
SERVED="$CAMERA_ROOT/.work/rear-camera/pmbootstrap-work/images_netboot/oneplus-guacamole.img"
sha256sum "$SRC" "$SERVED"    # must be identical: 4e464ab7...
ss -ltn '( sport = :10001 )'  # nbd-server on 172.16.42.2:10001
```

Mismatch in the fallback path: stop, remove the cache again, repeat. The
canonical `camera_serve.sh` does this safely and refuses a live cache; do not
use a manual `rm -f` while port 10001 is listening. `netboot serve --replace`
is an alternative, but it prompts; `-y` disables **all** prompts, so prefer the
explicit stopped-cache refresh in `camera_serve.sh`. The live cache is
root-owned; the canonical script handles that with sudo.
Also check `ip -4 address show dev <iface>` (`172.16.42.2/24`), `ip route get
172.16.42.1` (must use the USB iface, not docker/VPN) and `sudo ufw status
numbered` (inbound 10001 from `172.16.42.1` only). Do not add broad rules, do
not remove the 10000 rule, and do not `nmcli device set` without deciding
first (NetworkManager may flush the address).

### 3.5 Boot (host-only, one attempt)

`NEW`:

```bash
bash scripts/camera_boot.sh status
bash scripts/camera_boot.sh boot --dry-run   # identity + fastboot command
bash scripts/camera_boot.sh boot             # checks port 10001 + served hash
```

`CURRENT`:

```bash
"$CAMERA_ROOT/platform-tools/adb" devices       # Android: authorized
"$CAMERA_ROOT/platform-tools/fastboot" devices  # fastboot: exactly one
timeout 180 bash scripts/boot_rear_camera.sh
```

If Linux is already running: `ssh -t user@172.16.42.1 'sync; sudo reboot bootloader'`.
On `Load Error`/stall at `Sending 'boot.img'`, use the proven helper once (no
power-cycle loop):

```bash
export PATH="$CAMERA_ROOT/platform-tools:$PATH"
bash scripts/fastboot_boot.sh "$CAMERA_ROOT/build/rear-camera/boot.img"
```

It resets USB device `18d1:d00d` and restarts the bootloader; never flash. Do
not use `fastboot flash boot`.

### 3.6 Verify after boot (HOST, then read-only PHONE commands)

`NEW`: `bash scripts/camera_verify.sh --host 172.16.42.1 --user user` prints
PASS/FAIL, the fixed/stale verdict, dmesg classification and graph, and saves
`build/rear-camera/diagnostics/phone-<UTC>/`.

`CURRENT`:

```bash
mkdir -p "$CAMERA_ROOT/.work/delivery"
ssh -4 -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new \
  -o UserKnownHostsFile="$CAMERA_ROOT/.work/delivery/known_hosts" user@172.16.42.1
```

On the phone (read-only; first line is the primary pass/fail):

```sh
uname -r
sha256sum /usr/lib/modules/7.0.0-sm8150-rear1/kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst
lsmod | grep -E 'i2c_qcom_cci|qcom_camss|imx586|imx481|s5k3m5|ak7375|lc898217xc'
dmesg | grep -iE 'cci|camss|imx586|imx481|s5k3m5|ak737|lc898|cci reset timeout|Oops'
for m in /dev/media*; do media-ctl -d "$m" -p; done
cam-rear -l
```

Expected: release `7.0.0-sm8150-rear1`; the CCI hash/size recorded in this
build's `DELIVERY.json` (the historical observed build was `4cd35f05...`/8950);
no reset timeout/oops; three sensor links; `cam-rear -l` lists the rear cameras.
`8920`/`6f61b608...` means **STALE MODULE** — stop, refresh (§3.4), reboot.
`scripts/collect_rear_camera.sh` collects the same read-only evidence. Do not
load modules, stream, write I2C or touch regulators.

### 3.7 Rollback and crash capture (HOST/USER)

Ctrl-C the candidate server, then:

```bash
"$CAMERA_ROOT/platform-tools/fastboot" reboot    # fastboot -> Android
BASE=guacamole_nura-workspace/nura-sm8150-7-0
"$BASE/platform-tools/fastboot" boot "$BASE/build/7.0/boot.img"
bash "$BASE/scripts/serve_7_0.sh"                # only if 10000 is not running
```

On an oops/panic, **photograph the screen before rebooting**. Boot TWRP and run
`bash scripts/ramoops.sh`; pstore lands in `logs/ramoops_<timestamp>/`. If SSH
is alive, run `scripts/collect_rear_camera.sh` first. Do not repeatedly retry a
power cut (PMIC UVLO history).

## 4. Troubleshooting

| Symptom | Check | Fix | Evidence |
| --- | --- | --- | --- |
| Serve hangs before a server line | `ip -br link`; `/sys/class/net` | Ctrl-C; set `NURA_USB_IFACE`; use a known USB port | `serve_rear_camera.sh:9-11` |
| No carrier/IP | `ip -4 addr`; `ip route get 172.16.42.1` | `sudo ip addr replace 172.16.42.2/24 dev <if>`; bring link up; remove foreign route | `LIVE_BASELINE.md` |
| Address vanishes after replug/reboot | `ip -4 addr`; NetworkManager log | re-add address; `nmcli device set <if> managed no` only with consent | live baseline |
| NBD timeout/refused | `ss -ltn :10001`; `sudo ufw status`; phone console | allow inbound TCP 10001 from `172.16.42.1` on the USB iface; confirm hook port | `LIVE_BASELINE.md:22-25` |
| Port 10001 busy | `ss -ltnp '( sport = :10001 )'`; `ps -fp <pid>` | stop only candidate `nbd-server`/pmbootstrap; never kill 10000 | `netboot.py:46-72` |
| Phone CCI is `6f61b608`/8920 | phone `sha256sum`; cache vs source hash | stop, `sudo rm -f images_netboot/...`, serve, confirm, reboot | §2 timeline |
| boot.img wrong/old | `sha256sum`; `unpack_bootimg.py` | rebuild/repack; compare `DELIVERY.json`; never boot isolation as combined | §3.1 |
| `Sending` stall / `Load Error` | `fastboot devices`; `lsusb` | one `fastboot_boot.sh` recovery; no flashing | `fastboot_boot.sh:15-50` |
| `adb` unauthorized/no fastboot | `adb devices`; `fastboot devices` | authorize USB debugging; reconnect; do not proceed | `boot_rear_camera.sh:14-18` |
| SSH host-key/timeout | `ssh -4 -v`; `ping -c1 172.16.42.1`; `nc -vz 172.16.42.1 22` | `accept-new` + project known_hosts; `ssh-keygen -R` only after reflash | `ssh_phone.sh` |
| Oops before userspace/SSH | screen; pstore | photograph, TWRP, `scripts/ramoops.sh` | `CLAUDE.md` |
| NBD quits after phone reboot | serve terminal; `ss` | re-add host IP; wait for the loop to reconnect | `netboot.py:73-76` |
| CCI reset timeout, module fixed | `dmesg \| grep -i cci` | stop; capture dmesg/photo; do not repeatedly power | patch 0031 |

## 5. Never-load-the-wrong-image checklist (copy-pasteable)

> **Updated by `docs/DELIVERY_LIFECYCLE.md`.** Do not treat the old
> `EXPECT_CCI` constant or a post-boot whole-image hash as identity. The
> manifest now supplies the CCI/boot/rootfs pins for each build, and post-boot
> cache bytes are legitimately mutable. The checklist below is the pre-boot
> form only.

```bash
cd "${CAMERA_ROOT:?set CAMERA_ROOT to the candidate checkout}"
REL=7.0.0-sm8150-rear1
BOOT=build/rear-camera/boot.img
SRC=.work/rear-camera/pmbootstrap-work/chroot_native/home/pmos/rootfs/oneplus-guacamole.img
MANIFEST=build/rear-camera/DELIVERY.json
case "$PWD" in */nura-sm8150-7-0*) echo "WRONG WORKTREE"; exit 1;; esac
test -f device/testing/rear-camera/linux-postmarketos-qcom-sm8150/APKBUILD || exit 1
test "$(cat build/rear-camera/kernel-package/usr/share/kernel/postmarketos-qcom-sm8150/kernel.release)" = "$REL" || exit 1
python3 - "$MANIFEST" "$BOOT" "$SRC" <<'PY'
import hashlib, json, sys
manifest, boot, source = sys.argv[1:4]
doc = json.load(open(manifest))
def sha(path):
    return hashlib.sha256(open(path, 'rb').read()).hexdigest()
assert sha(boot) == doc['boot_img']['sha256'], 'BOOT HASH'
assert sha(source) == doc['rootfs_image']['sha256'], 'ROOTFS HASH'
print('manifest identity OK:', doc['i2c_qcom_cci']['sha256'], doc['i2c_qcom_cci']['size'])
PY
bash scripts/camera_serve.sh --check || exit 1
bash scripts/camera_boot.sh boot --dry-run || exit 1
bash scripts/camera_boot.sh boot        # one fastboot boot; never flash
```

Do not compare the served cache to the chroot image after userspace has booted:
that image is mounted read-write and has changed by design. Let
`camera_serve.sh` verify it before exposure and `camera_boot.sh status` use the
record. On Android, `adb devices` must show `device` first; on fastboot,
`getvar product` must contain `guacamole`. **One attempt**; on failure collect,
do not loop.

## 6. Doc reconciliation (do not edit here)

| Location | Contradiction / outdated statement | Future update |
| --- | --- | --- |
| `docs/REAR_CAMERA_7_0_BRINGUP.md:365` | "No image replacement is used" | Replace with §3.4 refresh + READY/verify |
| `docs/REAR_CAMERA_7_0_BRINGUP.md:377` | USB-reset recovery "separate" from launcher | Superseded by `camera_boot.sh` |
| `docs/REAR_CAMERA_7_0_BRINGUP.md:309-316` | Artifact table lacks boot.img/initramfs/rootfs/CCI | Add hashes; mark table time-stamped |
| `docs/REAR_CAMERA_7_0_BRINGUP.md:511-514` | "rootfs and boot image still contain the old module" | False after 12:27: rootfs has the fix; boot.img has no CCI module |
| `README.md:21-24` | Candidate "still needs user-run packaging" | Packaging done; hardware validation + script hardening remain |
| `README.md:59` | Camera "Not working / Not started" | Candidate rear stack exists; hardware untested |
| `docs/LIVE_BASELINE.md:16,25` | Port 9999, iface `enp19s0u4u3` | Historical; 10000 baseline / 10001 candidate, iface per port |
| `docs/NURA_ROOTFS_BUILD.md:217` | "binds 172.16.42.2:9999" | 7.0 baseline 10000; candidate 10001 |
| `docs/SM8150_7_0_BRINGUP.md:365,454` | "No real boot.img"; "No netboot server" | Historical; 7.0 image/server exist |
| `docs/TEMPORARY_BOOT.md:5` | "No boot image was produced" | 6.17-era historical |
| `build/rear-camera/packaging-inputs.json` | Live-worktree absolute `rootfs`/APK paths | New `DELIVERY.json` must be current-ROOT authoritative |

## 7. Residual risks

- The `0x03000000` DTB relocation and the three-rear graph are **not
  hardware-tested**; structural checks prove only internal consistency.
- The fixed CCI module is only in the rootfs image. Any serve path that reuses
  `images_netboot` silently undoes patch 0031; until `camera_serve.sh` lands,
  the `sudo rm -f` + hash check is mandatory.
- The live cache is root-owned; refresh needs sudo/`--replace`, and
  `pmbootstrap -y` disables all prompts.
- Absolute live-worktree paths in `packaging-inputs.json` and
  `pmbootstrap.cfg` can silently resolve to live artifacts unless every
  pmbootstrap call passes `-w`/`-p` and the new scripts verify `ROOT`.
- NetworkManager can flush `172.16.42.2/24`; UFW may block 10001; port 10000
  must stay untouched.
- ABL stall/Load Error, fastboot state and `dtbo` assumptions remain hardware
  risks; never flash and never retry a failing power cycle.
- `DELIVERY.json` is generated by `scripts/camera_build.sh manifest`; it must
  be regenerated after any rebuild. It records this checkout's absolute paths,
  so a manifest copied from another worktree is rejected by design.
