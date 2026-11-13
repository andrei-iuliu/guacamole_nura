# Recipe integration (read this before editing the shared recipe)

The parent owns the rc4 recipe.  This directory is a set of additions, not a
replacement for `device/testing/rc3/linux-postmarketos-qcom-sm8150/` or a
modified copy of `scripts/build_rear_camera_kernel.sh`.

## Merge order

1. Copy the reviewed rc3 recipe to the rc4 recipe path chosen by the parent.
   Keep the existing 0001-0041 bytes and their sha512 lines unchanged.
2. Add `0051-input-misc-add-goodix-fp.patch` and
   `0055-arm64-dts-guacamole-fingerprint.patch` to `source=` and
   `sha512sums=` exactly as in `APKBUILD.additions`.
3. Merge the config fragments into the recipe config:
   - `config/gate0.fragment` (QCOMTEE; TEE and SHMBRIDGE are already present)
   - `config/gate1.fragment` (INPUT_GOODIX_FP; depends on 0051)
   - `config/release-name.fragment` (7.0.0-sm8150-guacamole-rc4)
   Use the kernel's `scripts/kconfig/merge_config.sh` and then
   `make ... olddefconfig`; do not hand-append to the generated config.
4. Run `abuild checksum` (or the parent's checksum path) only after both
   patches and the config are in place.
5. Update the APKBUILD comment and `pkgrel`, and keep the release name
   `7.0.0-sm8150-guacamole-rc4`.  Never relabel or overwrite `rc3` images,
   modules, DTBs or boot images.

## Why gate1 is a separate fragment

`config/gate0.fragment` builds `qcomtee.ko` and keeps the existing SHM-bridge
allocator mode.  It is not passive by itself: `qcomtee` has a
`platform:qcomtee` modalias, qcom_scm registers that platform device at boot,
and udev/systemd can autoload the module, whose probe issues a QTEE
feature/version query.  For a passive gate-0 check, keep `qcomtee.ko` out of
the installed module tree or boot with `module_blacklist=qcomtee`, then load
it manually for the deliberate test.  `config/gate1.fragment` and patch 0055
must not be enabled as a working feature until gate 0 has been checked on the
phone.  The DT node is `status = "disabled"` and the driver
refuses power/reset without `goodix_fp.allow_power=1`, so even an accidental
merge cannot power the sensor.

## Verification helpers

- `check_recipe.sh <recipe-dir>`: read-only check that the two patch names and
  sha512 values are present and that the config symbols are correct.  It does
  not modify the recipe.  It is expected to fail until the parent has merged
  the additions.
- `../checks/` contains the apply/build evidence from this topic.  Those logs
  were produced from a reflinked rc3 source copy, not from the shared recipe.

## Self-check command

```sh
./recipe/check_recipe.sh ../device/testing/rc4/linux-postmarketos-qcom-sm8150
```

The script is intentionally conservative: it parses the literal `source=` and
`sha512sums=` text, so a recipe that computes sources dynamically will need
manual review.
