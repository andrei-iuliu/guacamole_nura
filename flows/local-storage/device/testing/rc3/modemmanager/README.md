# RC3 ModemManager r1: self-contained aports recipe

Status: **recipe only — not built, not installed, not booted.** The only
rootlessly verified facts are the source identities and checksums below, the
patch application to the pinned ModemManager source, the compiled
`select_primary_sim_slot()` behaviour, and the libqmi declarations generated
from the pinned libqmi commit. Builds are user-run on the existing candidate
pmbootstrap; no full ModemManager compile happened here.

This directory is a complete aports package directory. It is not a delta against
an aports checkout: copy it into the target pmaports/aports tree as the
`modemmanager` package directory, then build. It intentionally does **not**
replace or modify `device/testing/modemmanager/` (the earlier opt-in delta with
its 40 tests); that proposal stays as review evidence.

## Provenance

| Item | Value |
|---|---|
| Alpine aports commit | `50e299499516a04bde89c5566034ccfec6e1063a` |
| Upstream package path at that commit | `community/modemmanager` (verified byte-identical to `APKBUILD.upstream`; `main/modemmanager` returns 404 at the same commit) |
| Upstream `APKBUILD` SHA-256 | `c952de6a4a9a7351904a575ae2ce8f4a683e841b572bb588e46e5b1159610516` |
| ModemManager `pkgver` / `_commit` | `1.25.95_git20261005` / `7f0bf17ab518cd78925cf7e53cc0d5c42f88b41a` |
| Installed baseline | `1.25.95_git20261005-r0` (aarch64 `modemmanager` + `libmm-glib`; noarch `-systemd`, `-udev`, `-lang`) |
| This recipe | `1.25.95_git20261005-r1` (`pkgrel` 0 → 1) |
| libqmi required by the build | `>= 1.37.95`; Alpine `community/libqmi` `1.38.0_git20261005`, commit `1c972b8ab901565f26b9f95e1705269277bbfa1b` |

The upstream tarball is the only non-vendored source; it is the exact file the
pinned APKBUILD already pins:

```
ModemManager-7f0bf17ab518cd78925cf7e53cc0d5c42f88b41a.tar.gz
sha512 e049eecc104c989f516402ca7c21e3374c152e3840cf05c34ac91bc8e3305c83b53693bc0964f657664d4156baaac1a4e0fa35dd577733623dd669b67f1675f6
```

**Offline prerequisite:** the build workdir's `cache_distfiles/` must already
contain that file with that SHA-512, or the build host needs one fetch from
`https://gitlab.freedesktop.org/mobile-broadband/ModemManager/-/archive/$_commit/ModemManager-$_commit.tar.gz`.
The candidate workdir inspected for this task
(`.../guacamole_nura/.work/pmbootstrap/cache_distfiles/`, 78 entries) did not
contain it. No local copy was installed into a cache; the verified download used
here was temporary.

## Files

| File | SHA-512 | Role |
|---|---|---|
| `APKBUILD` | `5374bd40cf67adaf98069cc0d7d0122f3c531c7d3f2d21d731c171cb1dc4638388f3dcf4372ecfc148abe940e3c40ca7dfeb4fa79a119e6922e555df03fb27a9` | upstream source delta, circular-build fix and D-Bus test dependency |
| `APKBUILD.upstream` | `f90a1a9c50d91a4bb0f3b919971cb4f082c1bf86ead215311ca3e770a89e5b5a0d56cee59c18c997216504a61d82f9537de628f6d7dbcb8e899abe745357d114` | byte copy of the pinned aports file (SHA-256 `c952de6a…`), provenance only |
| `0001-intel-XMM7360-fix-big-endian-handling-of-ASN-integer.patch` | `f06e9b89acab5b61c5965658d62551b0f168066384674f7c4dd56feb6f811c82c640470e634e32d8c4ef62290588cf974aa5f213e5c67e797a06d33e61fa24ec` | byte copy of the Alpine patch, matches the pinned checksum |
| `0002-primary-sim-slot-selection.patch` | `d58a53043bf5973a83fbe204bbbc29ec6852b9c573b95420fc4c299d2cda4e3cdccb14b960dde32af126ca5ce29e2620844124356dad3e7b1a3f322e75cb9b8c` | the reviewed RC3 change (same bytes as `device/testing/modemmanager/0002-primary-sim-slot-selection.patch`, SHA-256 `7f8e4229…`) |
| `modemmanager.rules` | `6fab86c27502b29be0c6610d835a3249a4a81993eb986cff2c3ea9393fadd3f693ba4bb0532ae0e50a83d359559511859dd05f7ea2f0cb52839f535a7e49f8a0` | byte copy of the Alpine polkit rule, matches the pinned checksum |
| `modemmanager.initd` | `6e234cebcdec6d1afbca164ee5c13c96f7132ef31231d5c043cc5750ebe99e362a384d0463874b3c1c30799b433e0d3286add03af13c97fa406b4b5d2b78880c` | byte copy of the Alpine initd, matches the pinned checksum |

Every SHA-512 above is recomputed from the file on disk and also appears in the
recipe's `sha512sums`; no hash was copied from a summary without checking the
bytes. `diff -u APKBUILD.upstream APKBUILD` must show exactly: `pkgrel` 0 → 1,
one `source` line, one `sha512sums` line. The recipe has no other source change.

## Build (user-run, aarch64 only)

Stage the directory into the candidate pmaports checkout (the one pmbootstrap
uses, e.g. `<candidate>/.work/pmaports`) at the path that holds a forked Alpine
package in that tree — `main/modemmanager` in the current candidate tree, which
has no `community/` directory. The RC3 delivery tooling may stage it as a
symlink `main/modemmanager -> device/testing/rc3/modemmanager`; either way the
recipe itself stays at `device/testing/rc3/modemmanager` and there must be only
one `modemmanager` recipe in the tree.

```sh
# user-run, from the candidate workdir; never two pmbootstrap commands at once
pmbootstrap checksum modemmanager
pmbootstrap build --arch aarch64 modemmanager
```

- `--arch aarch64` is mandatory. `arch="all"` in the APKBUILD means "supported on
  all architectures"; abuild still tags the built packages with the build
  architecture. A host `abuild -r` would produce x86_64 packages that are not
  installable on the phone.
- `pmbootstrap checksum` re-derives `sha512sums`; it must reproduce the values
  above. It also downloads the tarball if the cache lacks it (see the
  prerequisite above).
- `pmbootstrap build --arch aarch64 modemmanager` builds all subpackages of the
  recipe in one abuild run, so `libmm-glib-…-r1` is produced even when the caller
  passes `--no-depends --ignore-depends` (the RC3 rootfs flow does).
- `check()` runs ModemManager's own meson test suite inside the aarch64 build
  environment. A failure is a build result to report; do not delete `check()` or
  bypass it to get an artifact.
- With `channel=edge`, built APKs land in
  `<candidate>/.work/pmbootstrap/packages/edge/aarch64/`.

Expected artifacts (abuild adds a content-hash suffix; do not predict it):

| Package | arch | Needed because |
|---|---|---|
| `modemmanager-1.25.95_git20261005-r1.*.apk` | aarch64 | contains `/usr/sbin/ModemManager`, where the patched selection code runs |
| `libmm-glib-1.25.95_git20261005-r1.*.apk` | aarch64 | `depends="libmm-glib=$pkgver-r$pkgrel"` pins the exact pair in one transaction |
| `modemmanager-systemd-…-r1.*.apk` | noarch | unit file; `install_if = systemd modemmanager=1.25.95_git20261005-r1` |
| `modemmanager-udev-…-r1.*.apk` | noarch | udev rules; `install_if = udev modemmanager=1.25.95_git20261005-r1` |
| `modemmanager-lang-…-r1.*.apk` | noarch | translations; `install_if = modemmanager=1.25.95_git20261005-r1 lang` |

The five packages share one `origin` and version. Install them as one set so the
exact-version `depends`/`install_if` constraints stay coherent. Payloads of
`-systemd`, `-udev` and `-lang` are expected to be unchanged from r0 (identical
sources and install rules); only their metadata version differs.

## Trust and rollback (requirements for the delivery worker)

- The r1 APKs will be signed by the candidate workdir's abuild key, whose public
  half is `pmos@local-6af20cae.rsa.pub` (present in
  `<candidate>/.work/pmbootstrap/config_apk_keys/`). The phone must trust that
  key in `/etc/apk/keys/` before a local install; verify the name on the phone
  first. If it is absent, installing the public key is a user-run root action,
  not part of this recipe.
- Never use `--allow-untrusted`. Never make rollback depend on a repository
  fetch: the phone has no uplink.
- Retained r0 rollback APKs (Alpine-signed by
  `alpine-devel@lists.alpinelinux.org-616ae350.rsa.pub`; verify that key is
  trusted on the phone before relying on rollback) are in
  `<candidate>/.work/pmbootstrap/cache_apk_aarch64/`:

```
93fc1bbdbd155d175a3a3896562f32bab64dde44801d332a57c90c54723dc730  modemmanager-1.25.95_git20261005-r0.ab345f17.apk
6f60c6befa5013ffd43198d8d54777ba55e7d0b4e48f8321c63b25d28ddb50b9  libmm-glib-1.25.95_git20261005-r0.93b55423.apk
89730ecce753383ba3d9ada528175a4466269baf76c3580d1f2045b4ea1a2131  modemmanager-systemd-1.25.95_git20261005-r0.50382707.apk
029bfb9eb3adc2d7936db691586ce79bf64a9d120fae533f339a810a940f8dc4  modemmanager-udev-1.25.95_git20261005-r0.d3a91c50.apk
d5ef6dc274bb05935dae25cc3b9be43b994e5859ee4602de27521dfca270e087  modemmanager-lang-1.25.95_git20261005-r0.7ac8401c.apk
```

Keep those exact files (copy them into the delivery artifact set, do not rely on
the cache surviving). A runtime downgrade was **not** exercised; the delivery
worker must validate the apk-tools downgrade invocation on a user-run device,
and should also treat "boot the previous r0 image" as the primary no-downgrade
rollback.

## What this recipe does not establish

No full ModemManager compile, no aarch64 package, no signature, no install, no
modem reprobe, and no claim that the SIM-change loop stops. See
`docs/NURA_RC3_MODEM.md` for the measured mapping, the API verification and the
exact delivery contract.
