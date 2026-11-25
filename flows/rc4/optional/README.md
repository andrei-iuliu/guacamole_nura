# Optional kernel sets

A set is a group of kernel patches (and, if needed, firmware) that is **not**
part of the default rc4 build. Nothing here is applied unless asked for:

```sh
./nura kernel --optional venus            # or: NURA_KERNEL_OPTIONAL="venus" ./nura kernel
```

Each set directory has:

| File | Purpose |
| --- | --- |
| `set.conf` | `name`, `release`, `status` (`experimental` or `stable`), `description`, optional `firmware` + `firmware_dest` |
| `SHA512SUMS` | pins every patch and the firmware tarball; the builder refuses a set that doesn't match |
| `patches/NNNN-*.patch` | applied in order after the recipe's `0001`-`0079`, with `--fuzz=0` |
| firmware tarball | gitignored local input, named in `set.conf` |

What the builder (`scripts/build_rear_camera_kernel.sh`) does with a set:

* verifies `SHA512SUMS`, then patches a fresh source tree. The source identity
  includes the sets, so a tree built with a set is never reused for a plain
  build or the other way round (it asks you to remove the tree instead);
* stages the firmware under `usr/lib/firmware/<firmware_dest>` and writes
  `usr/share/kernel/postmarketos-qcom-sm8150/optional-sets`
  (`name status sha256-of-SHA512SUMS`). The kernel APKBUILD installs both into
  the kernel APK, so on the phone
  `cat /usr/share/kernel/postmarketos-qcom-sm8150/optional-sets` shows what
  the running kernel package carries;
* `kernel-inputs.json` records the sets (`optional_sets`).

The kernel release string stays `7.0.0-sm8150-guacamole-rc4`; check the marker
file, not `uname`.

## Sets

| Set | Release | Status | What |
| --- | --- | --- | --- |
| `venus` | rc4t | **experimental** | SM8150 Venus (IRIS1) hardware video decode/encode: the AOSPA raphael series backported to 7.0, plus guacamole enablement and the board's firmware. See `../topics/venus/README.md`. |
