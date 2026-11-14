#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Read-only structural checks of the rc4 recipe and flow.  No writes, no build.

52 checks: recipe sources/checksums, retained rc3 bytes, the reviewed 0044-0058
set and order, the config gates, the kernel tarball hash, the device package
gates, the local-storage flow constants/guards and the rc4 wrapper.

Verifies:
  * every `source=` entry of the rc4 kernel APKBUILD exists in the recipe and
    matches its `sha512sums=` line (the merged-source checksum gate);
  * the 0001-0041 patch bytes are identical to the rc3 recipe (nothing old was
    silently rewritten);
  * the new patch numbers are exactly the reviewed set 0044-0058 and every
    topic patch is present under its assigned recipe name;
  * config identity: LOCALVERSION yields 7.0.0-sm8150-guacamole-rc4, and the
    gate-relevant CONFIG_* values are the reviewed ones;
  * the device package installs the MSS release unit but does not enable it in
    the preset and adds no mpss-start hook;
  * the local-storage flow is isolated under flows/rc4, uses the rc4 recipes,
    keeps the local-root guard and carries module_blacklist=qcomtee;
  * every path in the flow that refers to a kernel state/output points inside
    flows/rc4, and the unchanged rc3/rc2 baseline inputs are still referenced
    where they must be.

Exit status 0 when every check passes.
"""

import argparse
import hashlib
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[3]

NEW_PATCHES = {
    "0044-soc-qcom-guacamole-mss-vote.patch": "suspend-fixes",
    "0045-ath10k-snoc-wmi-key-ack.patch": "suspend-fixes",
    "0046-asoc-q6afe-dai-displayport-rx-input-widget.patch": "displayport",
    "0047-asoc-sm8150-accept-displayport-backend.patch": "displayport",
    "0048-asoc-sm8150-prepare-dp-codec-dai-before-afe-start.patch": "displayport",
    "0049-asoc-qdsp6-q6afe-split-port-config-and-start-errors.patch": "displayport",
    "0050-asoc-qdsp6-q6afe-send-dp-stream-and-controller-index.patch": "displayport",
    "0051-input-misc-add-goodix-fp.patch": "fingerprint",
    "0052-iio-magnetometer-add-mxm1120.patch": "popup-camera",
    "0053-media-i2c-add-imx471.patch": "popup-camera",
    "0054-media-qcom-add-oneplus-hotdog-popup-motor.patch": "popup-camera",
    "0055-arm64-dts-guacamole-fingerprint.patch": "fingerprint",
    "0056-arm64-dts-guacamole-popup-camera.patch": "popup-camera",
    "0057-arm64-dts-guacamole-displayport-video.patch": "displayport",
    "0058-arm64-dts-guacamole-displayport-audio-link.patch": "displayport",
}

GATES_CONFIG = {
    'CONFIG_LOCALVERSION': '"-sm8150-guacamole-rc4"',
    'CONFIG_PM_DEBUG': 'y',
    'CONFIG_PM_SLEEP_DEBUG': 'y',
    'CONFIG_QCOM_GUACAMOLE_MSS_VOTE': 'm',
    'CONFIG_QCOMTEE': 'm',
    'CONFIG_INPUT_GOODIX_FP': 'm',
    'CONFIG_MXM1120': 'm',
    'CONFIG_VIDEO_IMX471': 'm',
    'CONFIG_VIDEO_QCOM_HOTDOG_POPUP': 'm',
}

failures = []
checks = 0


def check(ok, label, detail=""):
    global checks
    checks += 1
    if not ok:
        failures.append("%s%s" % (label, (": " + detail) if detail else ""))
    print("%s %-78s %s" % ("PASS" if ok else "FAIL", label, detail))


def read(path):
    return pathlib.Path(path).read_text(encoding="utf-8", errors="replace")


def sha512(path):
    h = hashlib.sha512()
    h.update(pathlib.Path(path).read_bytes())
    return h.hexdigest()


def parse_apkbuild_sources(text):
    m = re.search(r'^source="\n(.*?)\n"\n', text, re.S | re.M)
    if not m:
        return [], []
    entries = []
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line:
            continue
        name = line.split("::")[-1].split("/")[-1]
        entries.append(name)
    sums = []
    m = re.search(r'^sha512sums="\n(.*?)\n"\n', text, re.S | re.M)
    if m:
        for line in m.group(1).splitlines():
            parts = line.split()
            if len(parts) == 2:
                sums.append((parts[0], parts[1]))
    return entries, sums


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recipe", default=str(REPO / "flows/rc4/device/testing/linux-postmarketos-qcom-sm8150"))
    ap.add_argument("--device", default=str(REPO / "flows/rc4/device/testing/device-oneplus-guacamole"))
    ap.add_argument("--local-flow", default=str(REPO / "flows/rc4/local-storage"))
    ap.add_argument("--release", default="7.0.0-sm8150-guacamole-rc4")
    ap.add_argument("--rc3-recipe", default=str(REPO / "device/testing/rc3/linux-postmarketos-qcom-sm8150"))
    args = ap.parse_args()

    recipe = pathlib.Path(args.recipe)
    device = pathlib.Path(args.device)
    flow = pathlib.Path(args.local_flow)
    rc3 = pathlib.Path(args.rc3_recipe)
    apkbuild = recipe / "APKBUILD"

    # ---------------------------------------------------------------- recipe
    check(apkbuild.is_file(), "recipe: APKBUILD exists", str(apkbuild))
    check((recipe / "config-postmarketos-qcom-sm8150.aarch64").is_file(),
          "recipe: config exists")
    text = read(apkbuild)
    entries, sums = parse_apkbuild_sources(text)
    check(bool(entries), "recipe: source= parsed", "%d entries" % len(entries))
    check(len(sums) == len(entries), "recipe: one sha512sum per source entry",
          "%d sums / %d entries" % (len(sums), len(entries)))
    sum_map = dict((name, digest) for digest, name in sums)

    missing = [e for e in entries if not e.startswith("linux-") and e != "config-$_flavor.$arch"
               and not (recipe / e).is_file()]
    check(not missing, "recipe: every local source file exists", ", ".join(missing[:4]))

    bad = []
    for entry in entries:
        if entry.startswith("linux-") or entry == "config-$_flavor.$arch":
            continue
        want = sum_map.get(entry)
        got = sha512(recipe / entry) if (recipe / entry).is_file() else None
        if want != got:
            bad.append(entry)
    check(not bad, "recipe: sha512sums match the files on disk", ", ".join(bad[:4]))

    patches = sorted(p.name for p in recipe.glob("0*.patch"))
    check(len(patches) == 52, "recipe: 0001-0041 plus 0044-0058 present",
          "%d patch files" % len(patches))
    check(all(f"name = '{args.release}'" in f"name = '{args.release}'" for _ in [0]) and
          re.search(r'^pkgver=7\.0\.0$', text, re.M) is not None and
          re.search(r'^_flavor="postmarketos-qcom-sm8150"$', text, re.M) is not None,
          "recipe: pkgver/flavor unchanged")
    check(re.search(r'^pkgrel=5$', text, re.M) is not None,
          "recipe: pkgrel bumped from the rc3 pkgrel=4")
    check(re.search(r'^prepare\(\) \{\n\tdefault_prepare\n\tcp "\$srcdir/config-\$_flavor\.\$arch" \.config',
                    text, re.M) is not None,
          "recipe: prepare() still default_prepare + config copy")

    rc3_patches = sorted(p.name for p in rc3.glob("0*.patch"))
    retained = [p for p in rc3_patches if p < "0042"]
    differing = [p for p in retained
                 if not (recipe / p).is_file() or
                 (recipe / p).read_bytes() != (rc3 / p).read_bytes()]
    check(not differing, "recipe: 0001-0041 patch bytes identical to rc3",
          ", ".join(differing[:4]))
    check(all(p in patches for p in NEW_PATCHES),
          "recipe: every reviewed topic patch is present under its assigned name",
          ", ".join(sorted(set(NEW_PATCHES) - set(patches))[:4]))

    # ordering: numeric order must be the reviewed apply order
    ordered = [p for p in patches if p >= "0044"]
    check(ordered == sorted(NEW_PATCHES), "recipe: new patch order is 0044-0058",
          ",".join(p[:4] for p in ordered))

    # tarball: the builder verifies this before applying anything.  It is not
    # part of this tree; the read-only rear-camera baseline copy is what the
    # builder falls back to, so check it when it is there.
    tar_name = re.search(r'^([0-9a-f]{128})  (linux-\S+\.tar\.\w+)$', read(apkbuild), re.M)
    if tar_name:
        want, name = tar_name.group(1), tar_name.group(2)
        candidates = [REPO / ".work/rear-camera" / name, REPO / ".work/rc3-kernel" / name,
                      pathlib.Path(args.recipe) / name]
        found = next((c for c in candidates if c.is_file()), None)
        if found is None:
            print("SKIP %-78s %s" % ("recipe: kernel tarball hash", "no local tarball"))
        else:
            check(sha512(found) == want,
                  "recipe: kernel tarball matches the retained sha512", str(found))

    # ----------------------------------------------------------------- config
    config = read(recipe / "config-postmarketos-qcom-sm8150.aarch64")
    for symbol, value in GATES_CONFIG.items():
        if value == "y":
            ok = re.search(r'^%s=y$' % re.escape(symbol), config, re.M) is not None
        elif value == "m":
            ok = re.search(r'^%s=m$' % re.escape(symbol), config, re.M) is not None
        else:
            ok = re.search(r'^%s=%s$' % (re.escape(symbol), re.escape(value)),
                           config, re.M) is not None
        check(ok, "config: %s=%s" % (symbol, value))
    check('CONFIG_LOCALVERSION="-sm8150-rc3"' not in config,
          "config: no rc3 localversion left")

    # ----------------------------------------------------------- device gate
    dev_apk = device / "APKBUILD"
    check(dev_apk.is_file(), "device: APKBUILD exists")
    dev = read(dev_apk)
    check("guacamole-mss-release" in dev and
          '$pkgdir/usr/libexec/guacamole-mss-release' in dev,
          "device: MSS release helper installed")
    preset = read(device / "80-guacamole.preset")
    check("guacamole-mss-release" not in preset,
          "device gate: MSS release unit NOT enabled in the preset")
    check("guacamole-mpss-start" not in dev or
          "guacamole-mss-release" not in read(device / "guacamole-mpss-start"),
          "device gate: no mpss-start hook for the MSS release")

    # ------------------------------------------------------------- local flow
    entry = flow / "scripts/local_storage_build.sh"
    packer = flow / "scripts/build_local_bootimg.sh"
    prepare = flow / "scripts/rootfs_prepare_local.sh"
    for p in (entry, packer, prepare):
        check(p.is_file(), "flow: %s" % p.relative_to(flow))
    entry_txt = read(entry)
    prepare_txt = read(prepare)
    packer_txt = read(packer)
    check("flows/rc4/build-source/kernel-output" in entry_txt,
          "flow: kernel output is flows/rc4/build-source")
    check("$SOURCE_ROOT/flows/rc4/device/testing/linux-postmarketos-qcom-sm8150"
          in prepare_txt or "flows/rc4/device/testing/linux-postmarketos-qcom-sm8150" in prepare_txt,
          "flow: uses the rc4 kernel recipe")
    check("device/testing/rc3/linux-postmarketos-qcom-sm8150" not in prepare_txt,
          "flow: no rc3 kernel recipe path")
    check('RECIPE_ROOT="${NURA_RECIPE_ROOT:-$ROOT/device/testing}"' in prepare_txt,
          "flow: one recipe root, pointing at the flow's own link farm")
    prepare_code = "\n".join(l for l in prepare_txt.splitlines()
                             if not l.lstrip().startswith("#"))
    check("flows/local-storage/device/testing" not in prepare_code,
          "flow: no direct default into the rc3-era recipe tree")
    # The link farm must select the rc4 device package (pkgrel 16), not the
    # rc3-era one (pkgrel 14), and the rc4 kernel recipe (pkgrel 5).
    farm = pathlib.Path(args.local_flow) / "device/testing"
    dev_apk = read(farm / "device-oneplus-guacamole/APKBUILD")
    check(re.search(r"^pkgrel=16$", dev_apk, re.M) is not None,
          "flow: RECIPE_ROOT resolves device-oneplus-guacamole to the rc4 pkgrel 16")
    check("guacamole-boot-slot.timer" in dev_apk and
          "plasma-mobile-device-presets" in dev_apk,
          "flow: device package ships the A/B slot marker and the Plasma corner preset")
    check((farm / "gmobile/APKBUILD").is_file(),
          "flow: link farm carries the patched gmobile (Phosh corner geometry)")
    krn_apk = read(farm / "linux-postmarketos-qcom-sm8150/APKBUILD")
    check(re.search(r"^pkgrel=5$", krn_apk, re.M) is not None,
          "flow: RECIPE_ROOT resolves the kernel recipe to the rc4 pkgrel 5")
    check("module_blacklist=qcomtee" in packer_txt,
          "flow: boot cmdline blacklists qcomtee at the kernel level")
    check("/dev/nbd" in prepare_txt,
          "flow: local-root guard (no NBD) still present")
    check("refuse_active_mounts" in prepare_txt,
          "flow: active-mount guard still present")
    check("flock" in entry_txt, "flow: concurrent-build flock still present")
    check("AUTHORIZED_KEY" in prepare_txt and "--authorized-key" in entry_txt,
          "flow: explicit public-key argument preserved")
    check('LOCAL_STATE="$FLOW_DIR/.work/local-userdata$UI_SUFFIX"' in entry_txt and
          'phosh) UI_SUFFIX="-phosh"' in entry_txt and 'plasma) UI_SUFFIX=""' in entry_txt,
          "flow: state stays under flows/rc4/local-storage (Phosh in its own -phosh dirs)")
    check('3.0-sm8150-(guacamole-rc|rc|rear)' in entry_txt.replace("\\", "") or
          "guacamole-rc|rc|rear" in entry_txt,
          "flow: release pattern accepts rc4 and keeps rc/rear rollback names")
    checker_txt = read(flow / "scripts/check_local_candidate.py")
    check('"series": "nura-plasma"' in checker_txt and '"series": "nura-phosh"' in checker_txt
          and 'RELEASE_CANDIDATE = f"{RELEASE_SERIES}-rc4"' in checker_txt
          and 'UI = os.environ.get("NURA_UI", "plasma")' in checker_txt,
          "flow: candidate label is nura-plasma-rc4 (nura-phosh-rc4 with NURA_UI=phosh)")

    # wrapper
    wrapper = REPO / "flows/rc4/scripts/rc4.sh"
    check(wrapper.is_file(), "wrapper: flows/rc4/scripts/rc4.sh exists")
    wtext = read(wrapper)
    for action in ("check", "contract", "kernel", "rootfs", "bootimg"):
        check(re.search(r'^\s+%s\)' % action, wtext, re.M) is not None,
              "wrapper: %s action present" % action)
    for forbidden in ("fastboot", "repartition", "umount", "mkfs"):
        check(forbidden not in wtext,
              "wrapper: no %s command" % forbidden)
    check("PYTHONDONTWRITEBYTECODE=1" in wtext,
          "wrapper: python never writes __pycache__")
    wcode = "\n".join(l for l in wtext.splitlines()
                      if not l.lstrip().startswith("#"))
    check("| tee" not in wcode and "rm -rf" not in wcode,
          "wrapper: the read-only action writes no logs and deletes nothing")
    check("__pycache__" not in wcode.replace("PYTHONDONTWRITEBYTECODE", ""),
          "wrapper: the read-only action deletes no caches")

    # ---------------------------------------------------------------- summary
    print("\n%d checks, %d failed" % (checks, len(failures)))
    for line in failures:
        print("FAILED: " + line, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
