# scripts/

Start with [`../nura`](../nura): it runs the current flow and calls the
scripts below. This index says what each script is for and whether the
current (rc4 local-userdata) flow uses it. Superseded generations are in
[`../archive/scripts/`](../archive/README.md).

## Setup (called by `./nura`)

| Script | Purpose |
|---|---|
| `check_prereqs.sh` | Host packages per stage, with the install command for pacman/apt/dnf (`./nura prereqs`) |
| `fetch_inputs.sh` | Fetch and verify platform-tools, mkbootimg, avbtool, DTB tools, stock dtbo/boot (`./nura fetch`) |
| `build_host_tools.sh` | System `dtc` tools plus `ufdt_overlay_test_app` from AOSP libufdt, recorded in `local/TOOLS.json` |
| `bootstrap_baseline.sh` | Pinned pmaports/pmbootstrap baseline from public sources (`./nura baseline`) |
| `signing_key.sh` | One package-signing key across pmbootstrap work directories (`./nura signing-key`) |
| `clean_work.sh` | List/remove stale `.work` dirs, the per-UI kernel copy and (with `--rootfs`) pmbootstrap work dirs; read-only without `--apply` (`./nura clean`) |
| `extract_lineage_firmware.py` | Firmware bundle from a Lineage OTA (`./nura firmware`) |
| `prepare_sensor_bundle.py` | Sensor bundle from the OTA's vendor/dsp images (`./nura firmware`) |
| `prepare_venus_bundle.py` | Venus firmware bundle for the experimental `flows/rc4/optional/venus` set, from the phone's `modem_b` copy or an OTA modem image (`./nura firmware`) |

## Used by the rc4 flow (`flows/rc4/`)

| Script | Purpose |
|---|---|
| `build_rear_camera_kernel.sh` | Rootless kernel/DT/modules build (`./nura kernel` via `rc4.sh`) |
| `prepare_temporary_dtb.py` | ABL DTBO-quarantine wrapper DTB and its verification |
| `fastboot_boot.sh` | `fastboot boot` with the ABL stall recovery (used by the installer) |
| `check_boot_envelope.py`, `check_initramfs_release.py`, `check_rear_camera_package.py` | Boot image / initramfs / module checks used by the packers |
| `check_plasma_candidate.py`, `rootfs_prepare_plasma.sh`, `build_bootimg_rear_camera.sh` | Originals of the flow's adapted copies; hash-pinned in `flows/rc4/local-storage/ORIGIN-SHA256.json` |
| `plasma_boot.sh`, `plasma_serve.sh`, `plasma_shutdown.sh`, `stop_plasma_server.py` | Plasma netboot delivery; `rootfs_prepare_local.sh` still refers to them |
| `camera_boot.sh`, `camera_serve.sh`, `camera_usb_net.sh`, `camera_verify.sh`, `camera_build.sh` | Rear-camera netboot helpers behind the `plasma_*` wrappers |
| `rootfs_prepare_rear_camera.sh`, `serve_rear_camera.sh`, `build_rear_camera_libcamera.sh`, `collect_rear_camera.sh` | Rear-camera candidate tooling referenced by the camera helpers |
| `rc3_build.sh` | rc3 build phase; the rc4 checker still reads its layout |
| `patch_dtb.py` | 6.17-era flattened-DTS fix-ups; still referenced by rc4 topic material |

## Diagnostics and utilities (run by hand)

| Script | Purpose |
|---|---|
| `ramoops.sh` | Pull pstore/ramoops logs after a panic (from TWRP) |
| `collect_boot_diagnostics.sh` | Small startup snapshot; shipped in the device package |
| `nura-probe.sh`, `collect_profile.sh`, `analyze_profile.py` | Read-only phone snapshot and profiling bundle |
| `ssh_phone.sh` | USB SSH with host keys isolated per boot |
| `cellular_iccid_compare.py` | Compare ModemManager's ICCID views without printing the ICCID |
| `extract_dtb.sh` | Dump the live DTB from Android/TWRP over ADB |
| `extract_sensor_files.sh` | Collect SLPI sensor files from a running phone |
| `cam_rear.sh` | Rear-camera smoke test, run on the phone |
| `checkpoint_working_camera.py` | Read-only checkpoint of a working rear-camera build |
