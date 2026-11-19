#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Guard/order tests for the copied rc4 local-storage flow.

The full rootfs pipeline needs a kernel package and a pmbootstrap chroot, so
these tests only exercise the parts that must refuse *before* any copy, mount
or build:

  * the entrypoint refuses a missing checkout before writing anything;
  * it refuses a missing rc4 kernel build and names the flows/rc4 path;
  * the release gate accepts 7.0.0-sm8150-guacamole-rc4 and the legacy
    rc/rear rollback names, and rejects anything else;
  * the public-key validator refuses private key material;
  * the candidate label and the disabled-suspend policy are the rc4 ones.

They run in a temporary directory with the real scripts read-only; nothing
under the repository is written.
"""

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

TESTS = pathlib.Path(__file__).resolve().parent
RC4 = TESTS.parent
REPO = RC4.parents[1]
FLOW = RC4 / "local-storage"
ENTRY = FLOW / "scripts/local_storage_build.sh"
CHECKER = FLOW / "scripts/check_local_candidate.py"
KERNEL_OUT = RC4 / "build-source/kernel-output"


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


class TestEntrypointGuards(unittest.TestCase):
    def test_missing_checkout_is_refused_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = pathlib.Path(tmp) / "no-checkout"

            def snapshot():
                # flow state from earlier real builds may exist; it must not change
                return sorted(str(p.relative_to(FLOW)) for top in (FLOW / ".work", FLOW / "build")
                              if top.exists() for p in [top, *top.glob("*"), *top.glob("*/*")])
            before = snapshot()
            result = run(["bash", str(ENTRY)], env={
                "PATH": "/usr/bin:/bin", "NURA_SOURCE_ROOT": str(missing)})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing source checkout", result.stderr)
            self.assertEqual(snapshot(), before, "a refused run must not create flow state")

    def test_missing_rc4_kernel_build_is_named(self):
        # The real checkout has no built kernel yet in a fresh tree; the
        # entrypoint must name the flows/rc4 path, never build/rc3-kernel.
        if (KERNEL_OUT / "kernel-inputs.json").is_file():
            self.skipTest("an rc4 kernel build already exists")
        result = run(["bash", str(ENTRY)], env={"PATH": "/usr/bin:/bin"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing canonical rc4 kernel inputs", result.stderr)
        self.assertIn("flows/rc4/build-source/kernel-output", result.stderr)
        self.assertNotIn("build/rc3-kernel", result.stderr)

    def test_no_phone_actions_in_usage(self):
        result = run(["bash", str(ENTRY), "--help"])
        self.assertEqual(result.returncode, 0)
        text = result.stdout
        for forbidden in ("fastboot", "adb", "ssh", "reboot", "flash"):
            self.assertNotIn(forbidden, text)


class TestReleaseGate(unittest.TestCase):
    def release_ok(self, value):
        """The same regex the entrypoint and the pipeline use."""
        import re
        return re.search(r"^7\.0\.0-sm8150-(guacamole-rc|rc|rear)[0-9]+$", value) is not None

    def test_accepts_rc4_and_rollback_names(self):
        for value in ("7.0.0-sm8150-guacamole-rc4", "7.0.0-sm8150-rc3",
                      "7.0.0-sm8150-rear1"):
            self.assertTrue(self.release_ok(value), value)

    def test_rejects_anything_else(self):
        for value in ("7.0.0-sm8150", "7.0.0-sm8150-guacamole",
                      "7.0.0-sm8150-guacamole-rcX", "6.17.0-sm8150-rc4",
                      "7.0.0-sm8150-guacamole-rc4-extra"):
            self.assertFalse(self.release_ok(value), value)

    def test_entrypoint_pins_the_rc4_release(self):
        text = ENTRY.read_text()
        self.assertIn('"7.0.0-sm8150-guacamole-rc4"', text)
        self.assertIn("expects the rc4 kernel release", text)


class TestCheckerUnits(unittest.TestCase):
    def test_private_key_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = pathlib.Path(tmp) / "id_ed25519"
            key.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n"
                           "-----END OPENSSH PRIVATE KEY-----\n")
            out = pathlib.Path(tmp) / "authorized_keys"
            result = run([sys.executable, str(CHECKER), "authorized-key",
                          "--public-key", str(key), "--output", str(out)])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("private key", (result.stderr + result.stdout).lower())
            self.assertFalse(out.exists())

    def test_disabled_suspend_policy_constant(self):
        text = CHECKER.read_text()
        self.assertIn("HandleSuspendKey=ignore", text)
        self.assertIn("AutoSuspendAction=0", text)

    def test_candidate_label_is_rc4(self):
        # Phosh is the default UI since 2026-11-14
        result = run([sys.executable, str(CHECKER), "release-candidate"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "nura-phosh-rc4")


class TestKernelBuildIdentity(unittest.TestCase):
    def test_checker_expects_the_rc4_release_and_recipe(self):
        text = CHECKER.read_text()
        self.assertIn('RELEASE = "7.0.0-sm8150-guacamole-rc4"', text)
        self.assertIn('KERNEL_RECIPE = "flows/rc4/device/testing/'
                      'linux-postmarketos-qcom-sm8150"', text)
        self.assertIn('flows/rc4/build-source/kernel-output', text)
        self.assertNotIn("build/rc3-kernel", text)
        self.assertNotIn(".work/rc3-kernel", text)

    def test_origin_manifest_matches_the_copies(self):
        manifest = json.loads((FLOW / "ORIGIN-SHA256.json").read_text())
        import hashlib
        for original, entry in manifest.items():
            copy = FLOW / entry["copy"]
            self.assertTrue(copy.is_file(), copy)
            digest = hashlib.sha256(copy.read_bytes()).hexdigest()
            self.assertNotEqual(digest, entry["sha256"],
                                "%s still equals its pre-adaptation copy hash; "
                                "the origin manifest must record the input, not "
                                "the adapted output" % entry["copy"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
