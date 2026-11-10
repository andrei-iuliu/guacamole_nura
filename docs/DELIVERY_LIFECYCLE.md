# Delivery lifecycle: stopped-cache refresh, per-build identity, post-boot checks

Status: host-only review patch for the `7.0.0-sm8150-rear1` candidate. No phone,
server, sudo, fastboot, pmbootstrap or capture action was performed while
writing this document or its tests. Hardware behaviour is whatever the user
already observed: three rear cameras work in the camera app, video records but
is choppy. This patch does not claim AF, FPS, suspend or video-quality
validation.

This document supersedes the serve/cache/identity advice in
`docs/CAMERA_DELIVERY.md` §3.4 and §5. The historical timeline and the CCI
fault analysis there are unchanged.

## 1. Canonical entrypoints

| Entrypoint | Status | Use |
| --- | --- | --- |
| `scripts/camera_build.sh` | canonical | `check`, `kernel`, `libcamera`, `bootimg`, `manifest`, `all`; writes one `DELIVERY.json` per build |
| `scripts/camera_serve.sh` | canonical | port 10001; validates the manifest, refreshes a **stopped** cache atomically, serves, records the pre-exposure identity |
| `scripts/camera_boot.sh` | canonical | identity-checked temporary `fastboot boot`; no flash path; `status` pre-boot and post-boot |
| `scripts/camera_verify.sh` | canonical | read-only post-boot check against the same `DELIVERY.json` |
| `scripts/camera_usb_net.sh` | canonical helper | `172.16.42.2/24`, finite wait, ownership record |
| `scripts/serve_rear_camera.sh` | superseded for verified delivery | simple port-10001 `netboot serve`; keeps working, but does not refresh or prove the cache |
| `scripts/boot_rear_camera.sh` | superseded for verified delivery | simple Android/fastboot `fastboot boot`; no identity gate |
| `scripts/build_rear_camera_kernel.sh`, `rootfs_prepare_rear_camera.sh` | helper | build/package helpers; the rootfs preparer is user-run, preserves old work/export and runs the final manifest/check |

Do not delete the superseded scripts in this patch. Use them only as a
fallback with the user's existing workflow; the four `camera_*` entrypoints are
the delivery interface that enforces identity.

## 2. What changed and why

### 2.1 A live cache is never replaced

`camera_serve.sh` used to copy the chroot rootfs image over
`images_netboot/oneplus-guacamole.img` before it checked port 10001. A running
`nbd-server` reads that file as its backing store, so `cp` could truncate the
image under a live phone. The order is now:

1. parse and validate `DELIVERY.json`;
2. hash the chroot image and inspect its GPT/ext4 contents;
3. compare the served cache;
4. if a refresh is needed, **refuse it while port 10001 is in use**;
5. copy to a sibling `*.refresh.<pid>` file;
6. hash and inspect **that staging file** (module tree, CCI module, DTB,
   initramfs, netboot hook port);
7. atomically `mv` the verified staging file over the cache;
8. configure USB/IP, free the port, start `nbd-server`;
9. after start, compare only `device:inode:size`; never re-hash a whole image
   after the phone can write to it.

If port 10001 is held by a candidate, stop that server and reboot the phone out
of the candidate before refreshing. The script refuses instead of killing it.

### 2.2 The CCI identity belongs to the build manifest

No script has a built-in `4cd35f05…`/8950 gate. The identity flow is:

- `camera_build.sh` proves **patch 0031 is in the kernel source**: the patch
  contains the all-master completion loop, `i2c-qcom-cci.c` has that loop
  before `for_each_available_child_of_node()`, and the pre-fix
  `init_completion(&master->irq_complete)` is gone. It also requires the staged
  module to be newer than that source and records the module hash/size it
  actually produced.
- `camera_build.sh manifest` writes
  `build/rear-camera/DELIVERY.json` with `{path,sha256,size}` for `boot_img`,
  `image`, `kernel_package`, `dtb`, `initramfs`, `rootfs_image` and
  `i2c_qcom_cci`.
- `camera_serve.sh`, `camera_boot.sh` and `camera_verify.sh` read the expected
  CCI hash/size from that manifest. A future rebuild with a different module
  hash is accepted as long as its own manifest records it.

The old hash stays useful as a historical observation in
`docs/CCI_PROBE_FAULT.md`; it is not a gate.

### 2.3 Post-boot images are mutable

The phone mounts the served rootfs read-write through NBD. Post-boot
`camera_boot.sh status` therefore does not hash the whole served image. It
reads `.work/delivery/served-image.json`, written by `camera_serve.sh` before
the server starts, and checks:

- the record belongs to the same served path;
- its `source_sha256` equals the manifest `rootfs_image` pin;
- the current `device:inode:size` still equals the verified generation.

The record says the image was verified **before exposure**. Content written by
the phone afterwards is expected. Pre-boot `camera_boot.sh boot` still hashes
the cache and checks the manifest module bytes, because the phone cannot write
before the server starts.

## 3. User-run sequence

Run from the candidate checkout. The commands are bash; the fish equivalents
for environment variables are shown separately.

```bash
cd /path/to/nura-rear-cameras-7-0
bash scripts/camera_build.sh check
bash scripts/camera_build.sh manifest      # after a build/package
bash scripts/camera_serve.sh --check       # no server started
bash scripts/camera_serve.sh               # refreshes stopped cache, serves 10001
# in a second terminal, with the phone in Android or fastboot:
bash scripts/camera_boot.sh boot --dry-run
bash scripts/camera_boot.sh boot
# after userspace:
bash scripts/camera_verify.sh
```

`camera_boot.sh` and `camera_verify.sh` are identity-checked but still expect
the user to run the phone commands. Nothing in this patch was run against the
phone.

### Fish

```fish
set -gx CAMERA_ROOT /path/to/nura-rear-cameras-7-0
cd $CAMERA_ROOT
bash scripts/camera_build.sh check
bash scripts/camera_serve.sh --check
bash scripts/camera_serve.sh
bash scripts/camera_boot.sh boot --dry-run
bash scripts/camera_boot.sh boot
bash scripts/camera_verify.sh
```

For a non-default USB interface:

```fish
set -gx NURA_USB_IFACE enp19s0u4u4
bash scripts/camera_serve.sh
```

For the simple fallback scripts, fish uses the same `env` form:

```fish
env NURA_USB_IFACE=enp19s0u4u4 bash scripts/serve_rear_camera.sh
```

## 4. What each refusal means

| Output | Meaning | Safe action |
| --- | --- | --- |
| `refusing to replace … while the candidate is in use` | port 10001 is listening, so the cache may be live | stop the candidate server, reboot the phone out of the candidate, rerun |
| `STALE MODULE … this build's manifest says …` | chroot image/module differs from `DELIVERY.json` | rebuild/package, then `camera_build.sh manifest` |
| `DELIVERY.json records CCI module …, staged module is …` | the package was rebuilt after the manifest | `camera_build.sh manifest` |
| `served cache file changed after exposure` | the cache was replaced while a phone could be using it | stop everything, refresh, reboot |
| `no pre-exposure identity record` | post-boot status has no serve record | `camera_serve.sh` on the next cycle; do not trust a post-boot hash |
| `CHECK FAILED: port 10001 is not available` | unrelated listener owns the candidate port | inspect `ss -ltnp`, stop only the candidate process |

Never stop or replace port 10000. Never `fastboot flash`; the boot path is
`fastboot boot` only.

## 5. Focused host tests

The tests use temporary fake checkouts, synthetic GPT/ext4 images, fake `ss`,
`ip`, `sudo`, `adb` and `fastboot`, and `CALL_LOG` assertions. They never run
the real tools or touch a phone.

```bash
python3 -m unittest \
  tests.test_camera_serve \
  tests.test_camera_boot \
  tests.test_camera_verify \
  tests.test_camera_build -v
```

The regressions specific to this patch:

- `LifecycleRegressionTests.test_refresh_is_refused_while_the_candidate_port_is_live`
- `LifecycleRegressionTests.test_future_build_cci_identity_comes_from_the_manifest`
- `LifecycleRegressionTests.test_check_is_independent_of_the_working_directory`
- `LifecycleRegressionTests.test_real_ip_up_configures_the_usb_host_address`
- `CameraBootTests.test_a_future_build_cci_hash_is_taken_from_the_manifest`
- `CameraBootTests.test_status_does_not_rehash_a_post_boot_mutable_image`
- `CameraBootTests.test_status_rejects_a_cache_replaced_after_exposure`
- `CameraBootTests.test_successful_boot_writes_last_boot_and_next_steps`
  (asserts `fastboot boot`, no `flash`/`erase`)
- `CheckTests.test_check_flags_a_module_that_differs_from_the_manifest`
- `ManifestTests.test_manifest_records_the_module_this_build_produced`
- `CameraVerifyTests.test_future_build_cci_identity_is_accepted_from_the_manifest`

## 6. Limits

- The live-cache guard uses the candidate port listener. A server bound to a
  different port, or a stale NBD client with no server, is outside this
  script's scope.
- The post-boot record proves file identity, not filesystem consistency after
  a crash. Use the existing pstore/ramoops procedure for that.
- This patch does not address choppy recording, AF, suspend or lens switching.
  Those remain separate diagnosis work.
