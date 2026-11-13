#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Generate flows/rc4/INVENTORY.json from the supplied input trees.

Every file under bringup-notes/Patches is listed
once, with its sha256, the topic it came from, the rc4 disposition
(integrated / already present / gated / blocked), the rc4 artifact that carries
it and a documentation link.  The table below is the authority; re-running this
script only refreshes hashes and the tree listing, so the inventory cannot
drift from the files.

Usage: flows/rc4/scripts/make_inventory.py [--out PATH]
"""

import argparse
import hashlib
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[3]
RC4 = REPO / "flows/rc4"
INPUT = pathlib.Path("bringup-notes/Patches")

DOC = "docs/NURA_RC4.md"
TOPIC_DOC = "flows/rc4/topics/{topic}/README.md"

# relative-to-topic path -> (disposition, rc4 artifact, documentation anchor)
TABLE = {
    # ------------------------------------------------------------ suspend-fixes
    "README.md": ("integrated", "flows/rc4/topics/suspend-fixes/README.md",
                  DOC + "#suspend-fixes"),
    "docs/00-diagnose.md": ("integrated", "flows/rc4/topics/suspend-fixes/docs/supplied/00-diagnose.md",
                            DOC + "#suspend-fixes"),
    "docs/0025-fastrpc-freezable-wait.md": ("already present", "rc3 0034-fastrpc-freezable-wait.patch",
                                            DOC + "#suspend-fixes"),
    "docs/0026-s6sy761-resume-irq.md": ("already present", "rc3 0035-s6sy761-resume-irq.patch",
                                        DOC + "#suspend-fixes"),
    "docs/0027-guacamole-mss-vote.md": ("integrated", "recipe 0044", DOC + "#suspend-fixes"),
    "docs/0028-ath10k-snoc-wmi-key-ack.md": ("integrated", "recipe 0045", DOC + "#suspend-fixes"),
    "kernel/0025-misc-fastrpc-freezable-invoke-wait.patch":
        ("already present", "rc3 0034-fastrpc-freezable-wait.patch", DOC + "#suspend-fixes"),
    "kernel/0026-input-s6sy761-enable-irq-after-resume-restart.patch":
        ("already present", "rc3 0035-s6sy761-resume-irq.patch (superset)", DOC + "#suspend-fixes"),
    "kernel/0027-soc-qcom-guacamole-mss-vote.patch":
        ("integrated", "recipe 0044-soc-qcom-guacamole-mss-vote.patch", DOC + "#suspend-fixes"),
    "kernel/0028-ath10k-snoc-wmi-key-ack.patch":
        ("integrated", "recipe 0045-ath10k-snoc-wmi-key-ack.patch", DOC + "#suspend-fixes"),
    "kernel/APKBUILD.diff": ("blocked", "targets 6.17/pkgrel 25-26; replaced by the rc4 recipe",
                             DOC + "#rejected-inputs"),
    "kernel/config.diff": ("blocked", "stale 6.17 fragment; replaced by flows/rc4/integration/config.fragment",
                           DOC + "#rejected-inputs"),
    "device/guacamole-mss-release":
        ("integrated", "flows/rc4/device/testing/device-oneplus-guacamole/guacamole-mss-release",
         DOC + "#suspend-fixes"),
    "device/guacamole-mss-release.service":
        ("gated", "installed in the rc4 device package, NOT enabled in 80-guacamole.preset",
         DOC + "#gates"),
    "device/device-package.diff": ("blocked", "targets the 6.17 pkgrel-11/12 OpenRC-only package",
                                   DOC + "#rejected-inputs"),
    "device/90-guacamole-modem-recovery.rules":
        ("gated", "not installed; diagnostic-only udev rule, see the topic INSTALL.md",
         DOC + "#suspend-fixes"),

    # -------------------------------------------------------------- displayport
    "README.md": ("integrated", "flows/rc4/topics/displayport/docs/reference/original-README.md",
                  DOC + "#displayport"),
    "docs/01-video.md": ("integrated", "flows/rc4/topics/displayport/docs/reference/01-video.md",
                         DOC + "#displayport"),
    "docs/02-audio.md": ("integrated", "flows/rc4/topics/displayport/docs/reference/02-audio.md",
                         DOC + "#displayport"),
    "dts/0001-dt-displayport-video.diff":
        ("integrated", "recipe 0057-arm64-dts-guacamole-displayport-video.patch (rewritten as a 7.0 dtsi)",
         DOC + "#displayport"),
    "dts/0002-dt-displayport-audio-link.diff":
        ("gated", "recipe 0058-...-displayport-audio-link.patch: dp-dai-link ships status=\"disabled\"",
         DOC + "#gates"),
    "kernel/0029-asoc-q6afe-dai-displayport-rx-playback-widget.patch":
        ("integrated", "recipe 0046-asoc-q6afe-dai-displayport-rx-input-widget.patch",
         DOC + "#displayport"),
    "kernel/0030-asoc-sm8150-accept-displayport-backend.patch":
        ("integrated", "recipe 0047-asoc-sm8150-accept-displayport-backend.patch", DOC + "#displayport"),
    "kernel/0031-asoc-sm8150-enable-dp-audio-before-afe-start.patch":
        ("gated", "recipe 0048-asoc-sm8150-prepare-dp-codec-dai-before-afe-start.patch (inert while the link is disabled)",
         DOC + "#displayport"),
    "kernel/0032-asoc-q6afe-split-config-and-start-errors.patch":
        ("integrated", "recipe 0049-asoc-qdsp6-q6afe-split-port-config-and-start-errors.patch",
         DOC + "#displayport"),
    "kernel/0033-asoc-q6afe-send-dp-stream-and-controller-index.patch":
        ("gated", "recipe 0050-asoc-qdsp6-q6afe-send-dp-stream-and-controller-index.patch (experiment)",
         DOC + "#displayport"),

    # --------------------------------------------------------------- fingerprint
    "README.md": ("integrated", "flows/rc4/topics/fingerprint/original/README.md",
                  DOC + "#fingerprint"),
    "docs/findings.md": ("integrated", "flows/rc4/topics/fingerprint/original/docs/findings.md",
                         DOC + "#fingerprint"),
    "dts/goodix-fp.dtsi": ("gated", "recipe 0055-arm64-dts-guacamole-fingerprint.patch: node status=\"disabled\"",
                           DOC + "#gates"),
    "gate0/config.fragment": ("integrated", "flows/rc4/integration/config.fragment (CONFIG_QCOMTEE=m)",
                              DOC + "#fingerprint"),
    "gate0/gate0-check.sh": ("gated", "flows/rc4/topics/fingerprint/scripts/gate0-check.sh (not shipped in the rootfs)",
                             DOC + "#fingerprint"),
    "kernel/0034-input-misc-add-goodix-fp.patch":
        ("integrated", "recipe 0051-input-misc-add-goodix-fp.patch (repaired, allow_power defaults to false)",
         DOC + "#fingerprint"),

    # -------------------------------------------------------------- popup-camera
    "README.md": ("integrated", "flows/rc4/topics/popup-camera/README.md", DOC + "#popup-camera"),
    "docs/motor-procedure.md": ("integrated", "flows/rc4/topics/popup-camera/docs/motor-procedure.md",
                                DOC + "#popup-camera"),
    "dts/0001-guacamole-camera-add-popup-front.diff":
        ("gated", "recipe 0056-arm64-dts-guacamole-popup-camera.patch: motor disabled, sensor+CAMSS endpoint absent by default",
         DOC + "#gates"),
    "kernel/0035-iio-magnetometer-add-mxm1120.patch":
        ("integrated", "recipe 0052-iio-magnetometer-add-mxm1120.patch", DOC + "#popup-camera"),
    "kernel/0036-media-i2c-add-imx471.patch":
        ("integrated", "recipe 0053-media-i2c-add-imx471.patch", DOC + "#popup-camera"),
    "kernel/0037-media-qcom-add-oneplus-popup-motor.patch":
        ("integrated", "recipe 0054-media-qcom-add-oneplus-hotdog-popup-motor.patch (driver built, DT node disabled)",
         DOC + "#popup-camera"),
}


def sha256(path):
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(RC4 / "INVENTORY.json"))
    args = ap.parse_args()

    supplied = []
    seen = set()
    for topic_dir in sorted(p for p in INPUT.iterdir() if p.is_dir()):
        topic = topic_dir.name
        for f in sorted(p for p in topic_dir.rglob("*") if p.is_file()):
            rel = str(f.relative_to(topic_dir))
            key = (topic, rel)
            if key in seen:
                continue
            seen.add(key)
            disposition, artifact, doc = TABLE[rel]
            supplied.append({
                "topic": topic,
                "path": "Patches/%s/%s" % (topic, rel),
                "sha256": sha256(f),
                "bytes": f.stat().st_size,
                "disposition": disposition,
                "rc4_artifact": artifact,
                "docs": [doc, TOPIC_DOC.format(topic=topic)],
            })

    listed = {(e["topic"], e["path"].split("/", 2)[2]) for e in supplied}
    expected = {(t.name, str(f.relative_to(t))) for t in INPUT.iterdir() if t.is_dir()
                for f in t.rglob("*") if f.is_file()}
    missing = expected - listed
    extra = listed - expected
    if missing or extra:
        print("inventory coverage mismatch: missing=%s extra=%s"
              % (sorted(missing)[:5], sorted(extra)[:5]), file=sys.stderr)
        return 1

    patches = sorted(p.name for p in (RC4 / "device/testing/linux-postmarketos-qcom-sm8150").glob("0*.patch"))
    inventory = {
        "topic": "rc4 integration",
        "release": "7.0.0-sm8150-guacamole-rc4",
        "generated_by": "flows/rc4/scripts/make_inventory.py",
        "input_root": str(INPUT),
        "supplied_root": str(INPUT),
        "summary": {
            "supplied_files": len(supplied),
            "by_topic": {t: sum(1 for e in supplied if e["topic"] == t)
                         for t in sorted({e["topic"] for e in supplied})},
            "by_disposition": {d: sum(1 for e in supplied if e["disposition"] == d)
                               for d in sorted({e["disposition"] for e in supplied})},
        },
        "recipe": {
            "path": "flows/rc4/device/testing/linux-postmarketos-qcom-sm8150",
            "patches": patches,
            "recipe_patch_count": len(patches),
            "new_patch_numbers": "0044-0058",
            "rc3_retained": "0001-0041 byte-identical",
        },
        "gates": [
            "MSS release helper installed, unit not enabled in 80-guacamole.preset, no mpss-start hook",
            "system suspend policy disabled (logind HandleSuspendKey=ignore, PowerDevil AutoSuspendAction=0)",
            "DP video enabled, DP audio DAI link status=\"disabled\"",
            "front sensor and CAMSS port@2 absent together unless CAMERA_POPUP_SENSOR; motor node disabled",
            "fingerprint DT node status=\"disabled\", goodix_fp.allow_power defaults to false",
            "QCOMTEE=m but module_blacklist=qcomtee on the rc4 boot cmdline",
        ],
        "supplied_files": supplied,
        "documentation": {
            "release": DOC,
            "integration": "flows/rc4/README.md",
            "review": "flows/rc4/REVIEW.md",
            "local_storage_flow": "flows/rc4/local-storage/README.md",
        },
    }
    pathlib.Path(args.out).write_text(json.dumps(inventory, indent=2) + "\n")
    print("wrote %s: %d supplied files %s" % (args.out, len(supplied), inventory["summary"]["by_disposition"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
