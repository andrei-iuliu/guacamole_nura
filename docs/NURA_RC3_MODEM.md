# NURA RC3: ModemManager `1.25.95_git20261005-r1` (primary SIM slot)

Status: **source + recipe prepared; not built, not packaged, not installed, not
run.** No phone, SSH, adb, fastboot, sudo, pmbootstrap, chroot, mount, service
or package action was performed. This document defines what the delivery
worker must build and how it must be delivered; it does not claim the SIM loop
stops, and no binary or hardware result is asserted.

The RC2 baseline is `1.25.95_git20261005-r0`, built from Alpine aports commit
`50e299499516a04bde89c5566034ccfec6e1063a` (the installed packages carry that
`commit` value in `.PKGINFO`). The formula change is the single reviewed
selection patch; `pkgrel` 0 → 1.

## 1. Why this package

User-run `--slot-summary` on a locked RC2 boot measured:

| Observation | Value |
|---|---|
| Physical slot 1 | card present, slot ACTIVE, logical slot 1 |
| Physical slot 2 | card present, slot ACTIVE, logical slot 2 |
| ModemManager `primary-sim-slot` | **2** |
| Primary GW provisioning | slot 1 / application 1 |
| Secondary GW provisioning | slot 2 / application 1 |
| EF2FE2 read | exactly slot 1 |

`uim_get_slot_status_ready()` kept the **last** ACTIVE slot, so slot 2 became
`PrimarySimSlot`; the recheck reads EF2FE2 through the **Primary GW** session
(slot 1). The cached/current ICCIDs therefore differ every cycle, and
`modem_check_for_sim_swap()` reprobes the modem. ModemManager's own card-status
parser calls the Primary GW session "the SIM to use", so that session is the
authoritative mapping; "first ACTIVE", "last ACTIVE" and "physical slot 1" are
not.

The fix is `0002-primary-sim-slot-selection.patch` (reviewed; full behaviour
table and alternatives in `device/testing/modemmanager/README.md`): query UIM
Get Card Status first, decode the Primary GW physical slot as
`((index_gw_primary & 0xFF00) >> 8) + 1`, and select it only when it is in
range, ACTIVE and has a constructed SIM object; otherwise keep exactly the
previous last-ACTIVE behaviour. The obsolete write-only
`LoadSimSlotsContext.active_logical_id` is removed; the switch-slot path keeps
its own local variable.

The superseded identifier-from-EF2FE2 workaround is **not** used. No
configuration, udev rule or service unit changes.

## 2. Pins and identities

| Component | Identity |
|---|---|
| Alpine aports commit | `50e299499516a04bde89c5566034ccfec6e1063a` |
| Upstream package path at that commit | `community/modemmanager` — verified: the file exists there and is byte-identical to the snapshot; `main/modemmanager` returns 404 at the same commit |
| Upstream `APKBUILD` SHA-256 | `c952de6a4a9a7351904a575ae2ce8f4a683e841b572bb588e46e5b1159610516` |
| ModemManager upstream commit / pkgver | `7f0bf17ab518cd78925cf7e53cc0d5c42f88b41a` / `1.25.95_git20261005` |
| ModemManager tarball SHA-512 | `e049eecc104c989f516402ca7c21e3374c152e3840cf05c34ac91bc8e3305c83b53693bc0964f657664d4156baaac1a4e0fa35dd577733623dd669b67f1675f6` |
| Alpine `modemmanager.rules` SHA-512 | `6fab86c27502b29be0c6610d835a3249a4a81993eb986cff2c3ea9393fadd3f693ba4bb0532ae0e50a83d359559511859dd05f7ea2f0cb52839f535a7e49f8a0` |
| Alpine `modemmanager.initd` SHA-512 | `6e234cebcdec6d1afbca164ee5c13c96f7132ef31231d5c043cc5750ebe99e362a384d0463874b3c1c30799b433e0d3286add03af13c97fa406b4b5d2b78880c` |
| Alpine `0001-intel-XMM7360…patch` SHA-512 | `f06e9b89acab5b61c5965658d62551b0f168066384674f7c4dd56feb6f811c82c640470e634e32d8c4ef62290588cf974aa5f213e5c67e797a06d33e61fa24ec` |
| RC3 source patch SHA-256 / SHA-512 | `7f8e42293383a0a5db637a23df7c2b26d5bc69a2b482aa6c064cd675a98339f1` / `d58a53043bf5973a83fbe204bbbc29ec6852b9c573b95420fc4c299d2cda4e3cdccb14b960dde32af126ca5ce29e2620844124356dad3e7b1a3f322e75cb9b8c` |
| libqmi used by the build | Alpine `community/libqmi` `1.38.0_git20261005`, commit `1c972b8ab901565f26b9f95e1705269277bbfa1b`; ModemManager requires `qmi-glib >= 1.37.95` |

All five auxiliary/patch SHA-512 values are **recomputed from the files now in
`device/testing/rc3/modemmanager/`** and equal the pinned values; the tarball
SHA-512 was reproduced from a real download of the pinned archive. No checksum
was invented or copied unchecked.

## 3. Recipe

`device/testing/rc3/modemmanager/` is a complete aports package directory:

```
APKBUILD
APKBUILD.upstream
0001-intel-XMM7360-fix-big-endian-handling-of-ASN-integer.patch
0002-primary-sim-slot-selection.patch
modemmanager.rules
modemmanager.initd
README.md
```

The upstream delta contains `pkgrel=1`, the `0002…patch` source/checksum
entries, and removal of `$depends_dev` from `makedepends`. That last change
avoids requiring this recipe's own unbuilt `libmm-glib` r1 during bootstrap.
`checkdepends` also includes `dbus`: three upstream tests spawn `dbus-daemon`,
which the cached `dbus` APK supplies. The first full compile passed, but those
three tests failed without that executable; all 38 tests remain enabled.
The exact runtime and development-package version pins remain unchanged.
The earlier opt-in `apkbuild.patch` describes the source patch delta only.
The recipe is self-contained except for the upstream tarball, which is a normal
aports distfile. The downloaded pinned tarball was additionally checked against
the pinned git commit: its `src/mm-shared-qmi.c` is byte-identical (SHA-256
`c44d629dce85803a9fc92659b9bc0c54c114f7e8f88ada3415f0a92ba2ed25b2`) to
`git show 7f0bf17…:src/mm-shared-qmi.c`, and the reviewed patch applies to the
extracted archive with both `git apply --check -p1` and `patch -p1 --dry-run`.

The path correction matters when following the older opt-in instructions
against a pure Alpine aports checkout: at this commit the package is under
`community/`, not `main/`. In the candidate **pmaports** fork there is no
`community/` tree, so the package is staged at `main/modemmanager` there (the
fork/override convention). Do not keep two `modemmanager` recipes in one tree.

## 4. libqmi API verification (source-level, exact pins)

The new callback was checked against the pinned libqmi source. Method: in the
local read-only libqmi repository
(`bringup-workspace/sources/libqmi-current`),
`git archive 1c972b8ab901565f26b9f95e1705269277bbfa1b` was extracted to a
temporary directory and the pinned `build-aux/qmi-codegen/qmi-codegen` was run
on the pinned `data/qmi-service-uim.json` (SHA-256
`4808b1f724165d724410088e8ab9c70da394594ead221dbe886c212ea1a60dfe`) with
`data/qmi-common.json`. It generated:

```c
void qmi_client_uim_get_card_status (
    QmiClientUim *self,
    gpointer unused,
    guint timeout,
    GCancellable *cancellable,
    GAsyncReadyCallback callback,
    gpointer user_data);

QmiMessageUimGetCardStatusOutput *qmi_client_uim_get_card_status_finish (
    QmiClientUim *self,
    GAsyncResult *res,
    GError **error);

gboolean qmi_message_uim_get_card_status_output_get_card_status (
    QmiMessageUimGetCardStatusOutput *self,
    guint16 *value_card_status_index_gw_primary,
    guint16 *value_card_status_index_1x_primary,
    guint16 *value_card_status_index_gw_secondary,
    guint16 *value_card_status_index_1x_secondary,
    GArray **value_card_status_cards,
    GError **error);
```

The patch calls exactly these shapes: `(ctx->client_uim, NULL, 10, NULL,
callback, task)`, `_finish (client, res, &error)`, and
`get_card_status (output, &index_gw_primary, NULL, NULL, NULL, &cards, NULL)`.
The `0xFF00` high-byte / 0x00FF low-byte decoding is ModemManager's own:
`src/mm-modem-helpers-qmi.c` computes `gw_primary_slot_i = ((index_gw_primary &
0xFF00) >> 8)` and treats `0xFF` as unknown; the patch only adds `+ 1` for the
1-based slot array and validates bounds/ACTIVE/constructed-SIM before use.
`g_autoptr(QmiMessageUimGetCardStatusOutput)` is already used in
`src/mm-sim-qmi.c`, so the cleanup attribute is available for this type.

This is a source/API verification, **not a compile** of ModemManager. The full
build remains the required next evidence.

## 5. Build plan (user-run, aarch64)

Build only through the existing candidate pmbootstrap workdir, never a host
`abuild -r` (`arch="all"` means "supported on all architectures", but abuild
tags the package with the build architecture; a host build yields x86_64 APKs).

1. Stage the recipe into the candidate pmaports checkout at
   `main/modemmanager/` (see §3). The RC3 tooling may use a symlink to
   `device/testing/rc3/modemmanager`; the build then follows the recipe in place.
2. `pmbootstrap checksum modemmanager` — must reproduce the SHA-512 values in
   §2; downloads the tarball if missing.
3. `pmbootstrap build --arch aarch64 modemmanager` — builds every subpackage of
   the recipe in one abuild run, including `libmm-glib`, even when the caller
   uses `--no-depends --ignore-depends`.
4. Collect the five r1 APKs from
   `<candidate>/.work/pmbootstrap/packages/edge/aarch64/` (abuild hash suffixes
   are content-derived; match by name/version, never predict the suffix).

Do not run two pmbootstrap commands at once. `check()` runs ModemManager's own
meson tests inside the aarch64 environment; a check failure is a result to
report, not a reason to remove `check()`.

Dependencies are unchanged from the upstream recipe; the new build requires the
Alpine edge aarch64 `libqmi-dev >= 1.37.95` (pinned 1.38.0_git20261005) and
`libmbim-dev >= 1.32.0`. `libmm-glib` is rebuilt only because the main package's
`depends="libmm-glib=$pkgver-r$pkgrel"` requires the exact r1 pair. If the build
root does not already provide the makedepends (`meson`, `vala`,
`gobject-introspection-dev`, `libqmi-dev`, `libmbim-dev`, `polkit-dev`, …), they
must be installed/built there first; `--no-depends --ignore-depends` deliberately
does not build dependencies.

### Offline prerequisite (explicit)

The pinned tarball is **not** in the candidate workdir's `cache_distfiles/` (78
entries inspected; none is `ModemManager-7f0bf17…tar.gz`). The build host either
needs one read-only fetch of the pinned URL, or that exact file with SHA-512
`e049eecc…` must be seeded into `<candidate>/.work/pmbootstrap/cache_distfiles/`
before an offline build. The verified download used here was not installed into
any build cache; the task report records the staged copy left for the user to
seed into that directory.

## 6. Delivery contract (for the delivery worker)

Required artifacts, kept together:

| Package | arch | Constraint |
|---|---|---|
| `modemmanager` | aarch64 | daemon `/usr/sbin/ModemManager` carries the change |
| `libmm-glib` | aarch64 | exact `=1.25.95_git20261005-r1` pin from `modemmanager` |
| `modemmanager-systemd` | noarch | `install_if = systemd modemmanager=1.25.95_git20261005-r1` |
| `modemmanager-udev` | noarch | `install_if = udev modemmanager=1.25.95_git20261005-r1` |
| `modemmanager-lang` | noarch | `install_if = modemmanager=1.25.95_git20261005-r1 lang` |

All five share `origin=modemmanager` and version `1.25.95_git20261005-r1`; install
them as one set. The recipe also emits `-doc`, `-dbg`, `-dev`, `-openrc` and
`-bash-completion`; those are not part of the phone's required set and must not
be used to satisfy the version pin. Record `{path,sha256,size}` for each in the
build manifest (the same identity pattern the delivery lifecycle already uses),
plus the source pins and the recipe patch hash. Do not relabel an earlier r0
boot/build.

**Trust:** the r1 APKs are signed by the candidate workdir's abuild key
(`pmos@local-6af20cae.rsa.pub` in
`<candidate>/.work/pmbootstrap/config_apk_keys/`). The phone must trust that
public key in `/etc/apk/keys/`; verify the name on the phone before installing
and add it (user-run root action) only if missing. No `--allow-untrusted`.

**Rollback:** retain the exact Alpine-signed r0 files from
`<candidate>/.work/pmbootstrap/cache_apk_aarch64/` in the delivery artifact set,
with these SHA-256 values:

| r0 package | SHA-256 |
|---|---|
| `modemmanager-1.25.95_git20261005-r0.ab345f17.apk` | `93fc1bbdbd155d175a3a3896562f32bab64dde44801d332a57c90c54723dc730` |
| `libmm-glib-1.25.95_git20261005-r0.93b55423.apk` | `6f60c6befa5013ffd43198d8d54777ba55e7d0b4e48f8321c63b25d28ddb50b9` |
| `modemmanager-systemd-1.25.95_git20261005-r0.50382707.apk` | `89730ecce753383ba3d9ada528175a4466269baf76c3580d1f2045b4ea1a2131` |
| `modemmanager-udev-1.25.95_git20261005-r0.d3a91c50.apk` | `029bfb9eb3adc2d7936db691586ce79bf64a9d120fae533f339a810a940f8dc4` |
| `modemmanager-lang-1.25.95_git20261005-r0.7ac8401c.apk` | `d5ef6dc274bb05935dae25cc3b9be43b994e5859ee4602de27521dfca270e087` |

Rollback must not fetch anything (the phone has no uplink), and no rollback step
may use `--allow-untrusted`. The primary no-downgrade rollback is booting the
previous r0 image; an in-place downgrade must be validated by the delivery
worker on a user-run device (apk downgrade semantics were not exercised or
assumed here).

No configuration, udev rule or unit file is added by r1. A service restart after
install is the delivery worker's runtime step; nothing about it is claimed here.

## 7. Rootless validation

```sh
python3 -m unittest tests.test_rc3_modemmanager_recipe -v
python3 -m unittest tests.test_modemmanager_primary_sim_selection -v
```

The new suite checks the recipe layout, every recomputed checksum, the exact
upstream delta and circular-build regression, the reviewed patch identity, the absence of the
superseded workaround, version/subpackage/install constraints, that no default
build list references the recipe, and — when the pinned checkouts are present —
applies the patch to the pinned ModemManager blob and regenerates the pinned
libqmi UIM header to assert the three declarations above. The existing suite
covers the compiled `select_primary_sim_slot()` cases and the preserved swap /
slot-indication paths.

## 8. Limits and open items

1. Full ModemManager compile: **not done** (not authorized in this task).
2. aarch64 package, signing and artifact hashes: **not produced**.
3. Install, trust change and service restart: **not performed**.
4. The runtime claim "the SIM-change loop stops" is **not established**; the
   firmware was not observed after the change, and Primary GW stability at
   probe/enable is still unverified.
5. The earlier `msm-modem-uim-selection` observation and the dual-ACTIVE
   firmware mapping remain the same as in the RC2 report; nothing new was
   measured on a phone.

Source rollback: delete `device/testing/rc3/modemmanager/`, this document and
`tests/test_rc3_modemmanager_recipe.py`; no kernel, DTB, delivery, device or
shared script file is involved.
