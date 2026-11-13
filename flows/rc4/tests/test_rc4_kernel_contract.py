#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Executable contract test: rc4 kernel build -> rootfs preflight.

This exists because static string assertions missed a real bug: `rc4.sh` pointed
`NURA_KERNEL_OUTPUT` at a sibling of the state, while the rootfs flow and the
snapshot guard read `kernel-state/output` and `kernel-output/{kernel-package,
Image,kernel-inputs.json}`.  A fake builder that consumes the *documented*
NURA_* contract is the only cheap way to catch that class of error.

What runs here, with no sudo, mount, pmbootstrap, phone or full build:

  1. `flows/rc4/scripts/rc4.sh kernel` with
     NURA_KERNEL_BUILDER=tests/fixtures/fake_build_rear_camera_kernel.sh and
     RC4_KERNEL_ROOT=<tmp inside the checkout>.  This exercises the real wrapper
     path: builder env mapping -> real `kernel-record` -> contract assertions.
  2. `local_storage_build.sh` (dry run) with the fake kernel: proves the
     entrypoint preflight (kernel-inputs.json, kernel-package, release, canonical
     snapshot incl. kernel-state/output) accepts a correctly built tree and
     writes nothing.
  3. `rootfs_prepare_local.sh --check-only` with the fake kernel: the real
     rootfs preflight and the `check_local_candidate.py source` handoff.
  4. Negative cases: a missing kernel-state/output, a missing kernel-package and
     a wrong release must each be refused with the documented message.

The fake tree lives in a temporary directory inside `flows/rc4/build-source`
(the checker requires build/state to be inside the checkout) and is removed at
the end of every test.
"""

import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

TESTS = pathlib.Path(__file__).resolve().parent
RC4 = TESTS.parent
REPO = RC4.parents[1]
FIXTURE = TESTS / "fixtures/fake_build_rear_camera_kernel.sh"
DTB_DIR = RC4 / "build-source/artifacts/dtb"
WRAPPER = RC4 / "scripts/rc4.sh"
ENTRYPOINT = RC4 / "local-storage/scripts/local_storage_build.sh"
PREPARE = RC4 / "local-storage/scripts/rootfs_prepare_local.sh"
RELEASE = "7.0.0-sm8150-guacamole-rc4"


def base_env(root):
    env = dict(os.environ)
    env.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "RC4_KERNEL_ROOT": str(root),
        "RC4_FAKE_DTB_DIR": str(DTB_DIR),
        "NURA_KERNEL_BUILDER": str(FIXTURE),
        "NURA_SOURCE_ROOT": str(REPO),
        # keep the flow from inheriting a user's build knobs
        "NURA_KERNEL_BUILD": "",
        "NURA_KERNEL_SOURCE_STATE": "",
    })
    return env


class KernelContractTest(unittest.TestCase):
    """One fake kernel per test class; each test may damage its own copy."""

    @classmethod
    def setUpClass(cls):
        if not DTB_DIR.is_dir() or not any(DTB_DIR.glob("*.dtb")):
            raise unittest.SkipTest("no reviewed DTBs in build-source/artifacts/dtb")
        cls._tmp = tempfile.mkdtemp(prefix="fake-kernel-", dir=str(RC4 / "build-source"))
        cls.root = pathlib.Path(cls._tmp)
        cls.env = base_env(cls.root)
        result = subprocess.run(["bash", str(WRAPPER), "kernel"],
                                capture_output=True, text=True, env=cls.env)
        cls.build = result
        cls.state = cls.root / "kernel-state"
        cls.out = cls.root / "kernel-output"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    # ------------------------------------------------------------- the build
    def test_01_fake_build_through_the_real_wrapper_succeeds(self):
        self.assertEqual(self.build.returncode, 0,
                         self.build.stdout[-4000:] + self.build.stderr[-4000:])
        self.assertIn("contract ok", self.build.stdout)
        self.assertIn("rc4 kernel build: PASS", self.build.stdout)

    def test_02_wrapper_maps_the_coherent_contract(self):
        # state/output is the kbuild output, stage and Image are in kernel-output
        self.assertTrue((self.state / "output/include/config/kernel.release").is_file())
        self.assertTrue((self.out / "kernel-package/boot/vmlinuz").is_file())
        self.assertTrue((self.out / "Image").is_file())
        self.assertTrue((self.out / "kernel-inputs.json").is_file())
        self.assertEqual((self.state / "output/include/config/kernel.release").read_text().strip(),
                         RELEASE)
        # the builder itself must not have written the manifest
        self.assertIn("no kernel-inputs.json / DELIVERY.json written", self.build.stdout)

    def test_03_kernel_record_identity_is_complete(self):
        import json
        data = json.loads((self.out / "kernel-inputs.json").read_text())
        self.assertEqual(data["release"], RELEASE)
        self.assertEqual(data["recipe"],
                         "flows/rc4/device/testing/linux-postmarketos-qcom-sm8150")
        self.assertEqual(pathlib.Path(data["image"]["path"]), self.out / "Image")
        self.assertEqual(pathlib.Path(data["kernel_build"]).resolve(), self.out.resolve())
        self.assertEqual(pathlib.Path(data["kernel_state"]).resolve(), self.state.resolve())
        self.assertGreaterEqual(data["module_count"], 1)
        self.assertEqual(len(data["module_payload_sha256"]), 64)

    def test_04_delivery_json_is_not_stolen_from_the_kernel_phase(self):
        # DELIVERY.json belongs to the rootfs phase; the kernel phase must not
        # invent one (that would hide a missing rootfs handoff).
        self.assertFalse((self.out / "DELIVERY.json").exists())

    # ------------------------------------------------- rootfs preflight (dry)
    def test_05_rootfs_dry_run_accepts_the_built_kernel(self):
        env = dict(self.env)
        env["NURA_KERNEL_BUILD"] = str(self.out)
        env["NURA_KERNEL_SOURCE_STATE"] = str(self.state)
        result = subprocess.run(["bash", str(ENTRYPOINT)],
                                capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0,
                         result.stdout[-4000:] + result.stderr[-4000:])
        self.assertIn("local-userdata build plan", result.stdout)
        self.assertIn(RELEASE, result.stdout)
        # a dry run must not create flow state
        self.assertFalse((RC4 / "local-storage/.work").exists())

    def test_06_rootfs_check_only_runs_the_checker_handoff(self):
        env = dict(self.env)
        env.update({
            "NURA_KERNEL_BUILD": str(self.out),
            "NURA_KERNEL_SOURCE_STATE": str(self.state),
            "NURA_KERNEL_STATE": str(self.state),
        })
        result = subprocess.run(["bash", str(PREPARE), "--check-only"],
                                capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0,
                         result.stdout[-6000:] + result.stderr[-6000:])
        self.assertIn("kernel recipe/staging and rc2 APK baseline pins: OK", result.stdout)
        self.assertIn("checks passed", result.stdout)

    def test_06b_kernel_copy_check_accepts_the_fake_state(self):
        env = dict(self.env)
        env.update({"NURA_KERNEL_SOURCE_STATE": str(self.state), "PYTHONDONTWRITEBYTECODE": "1"})
        result = subprocess.run(
            ["python3", str(RC4 / "local-storage/scripts/check_local_candidate.py"),
             "kernel-copy-check", "--state", str(self.state)],
            capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        import json
        data = json.loads(result.stdout)
        self.assertTrue(data["copyable"])
        self.assertEqual(data["release"], RELEASE)
        self.assertEqual(data["source_tree"], "linux-v7.0.0-sm8150")

    def test_06c_kernel_copy_check_accepts_the_real_kbuild_absolute_link(self):
        # A normal `make O=` state has output/source as an ABSOLUTE symlink to
        # the exact source tree inside the same state.  That is expected and
        # copy_kernel_state() rewrites it; the check must not refuse it.
        link = self.state / "output/source"
        self.assertTrue(link.is_symlink(), "fake builder must create output/source")
        self.assertTrue(os.path.isabs(os.readlink(link)),
                        "the fake state must mirror kbuild's absolute link")
        self.assertEqual(link.resolve(), (self.state / "linux-v7.0.0-sm8150").resolve())
        env = dict(self.env)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            ["python3", str(RC4 / "local-storage/scripts/check_local_candidate.py"),
             "kernel-copy-check", "--state", str(self.state)],
            capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"copyable": true', result.stdout)

    def _copy_check_on_mutated_state(self, mutate):
        root = pathlib.Path(tempfile.mkdtemp(prefix="fake-link-", dir=str(RC4 / "build-source")))
        state = root / "state-copy"
        shutil.copytree(self.state, state, symlinks=True)
        mutate(root, state)
        env = dict(self.env)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            ["python3", str(RC4 / "local-storage/scripts/check_local_candidate.py"),
             "kernel-copy-check", "--state", str(state)],
            capture_output=True, text=True, env=env)
        shutil.rmtree(root, ignore_errors=True)
        return result

    def test_06d_kernel_copy_check_refuses_a_source_link_outside_the_state(self):
        outside = pathlib.Path(tempfile.mkdtemp(prefix="outside-tree-", dir=str(RC4 / "build-source")))

        def mutate(root, state):
            link = state / "output/source"
            link.unlink()
            link.symlink_to(outside)

        try:
            result = self._copy_check_on_mutated_state(mutate)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("leaves the kernel state", result.stderr)
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_06e_kernel_copy_check_refuses_a_dangling_source_link(self):
        def mutate(root, state):
            link = state / "output/source"
            link.unlink()
            link.symlink_to(state / "linux-v7.0.0-sm8150-does-not-exist")

        result = self._copy_check_on_mutated_state(mutate)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dangling", result.stderr)

    def test_06f_kernel_copy_check_refuses_a_link_to_another_internal_tree(self):
        def mutate(root, state):
            other = state / "linux-v9.9.9-other"
            other.mkdir()
            (other / "Makefile").write_text("# decoy\n")
            link = state / "output/source"
            link.unlink()
            link.symlink_to(other)

        result = self._copy_check_on_mutated_state(mutate)
        self.assertNotEqual(result.returncode, 0)
        # two linux-* trees is itself refused before the link is inspected
        self.assertIn("expected exactly one linux-* source tree", result.stderr)

    def test_06g_kernel_copy_check_refuses_a_link_to_the_other_tree_when_unique(self):
        def mutate(root, state):
            # A single source tree, but output/source points at a sibling dir
            # inside the state that is not the validated tree.
            decoy = state / "not-a-source-tree"
            decoy.mkdir()
            link = state / "output/source"
            link.unlink()
            link.symlink_to(decoy)

        result = self._copy_check_on_mutated_state(mutate)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("another tree inside the state", result.stderr)

    # ---------------------------------------------------------- negative cases
    def _dry_run_with(self, env):
        return subprocess.run(["bash", str(ENTRYPOINT)],
                              capture_output=True, text=True, env=env)

    def test_07_missing_state_output_is_refused(self):
        root = pathlib.Path(tempfile.mkdtemp(prefix="fake-nooutput-", dir=str(RC4 / "build-source")))
        try:
            env = base_env(root)
            env["NURA_KERNEL_BUILD"] = str(self.out)
            env["NURA_KERNEL_SOURCE_STATE"] = str(self.state)
            # remove the state/output directory from a copy of the state
            shutil.copytree(self.state, root / "state-copy", symlinks=True)
            shutil.rmtree(root / "state-copy/output")
            env["NURA_KERNEL_SOURCE_STATE"] = str(root / "state-copy")
            result = self._dry_run_with(env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing read-only input", result.stderr)
            self.assertIn("kernel.release", result.stderr)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_08_missing_kernel_package_is_refused(self):
        root = pathlib.Path(tempfile.mkdtemp(prefix="fake-nostage-", dir=str(RC4 / "build-source")))
        try:
            env = base_env(root)
            out_copy = root / "out-copy"
            shutil.copytree(self.out, out_copy, symlinks=True)
            shutil.rmtree(out_copy / "kernel-package")
            env["NURA_KERNEL_BUILD"] = str(out_copy)
            env["NURA_KERNEL_SOURCE_STATE"] = str(self.state)
            result = self._dry_run_with(env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing canonical rc4 kernel package", result.stderr)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_09_wrong_release_is_refused(self):
        root = pathlib.Path(tempfile.mkdtemp(prefix="fake-wrongrel-", dir=str(RC4 / "build-source")))
        try:
            env = base_env(root)
            out_copy = root / "out-copy"
            shutil.copytree(self.out, out_copy, symlinks=True)
            (out_copy / "kernel-package/usr/share/kernel/postmarketos-qcom-sm8150/"
                       "kernel.release").write_text("7.0.0-sm8150-rc3\n")
            env["NURA_KERNEL_BUILD"] = str(out_copy)
            env["NURA_KERNEL_SOURCE_STATE"] = str(self.state)
            result = self._dry_run_with(env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("expects the rc4 kernel release", result.stderr)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_10_rootfs_check_only_refuses_a_missing_manifest(self):
        root = pathlib.Path(tempfile.mkdtemp(prefix="fake-nomanifest-", dir=str(RC4 / "build-source")))
        try:
            env = dict(self.env)
            out_copy = root / "out-copy"
            shutil.copytree(self.out, out_copy, symlinks=True)
            (out_copy / "kernel-inputs.json").unlink()
            env.update({
                "NURA_KERNEL_BUILD": str(out_copy),
                "NURA_KERNEL_SOURCE_STATE": str(self.state),
                "NURA_KERNEL_STATE": str(self.state),
            })
            result = subprocess.run(["bash", str(PREPARE), "--check-only"],
                                    capture_output=True, text=True, env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("kernel-inputs.json", result.stderr)
            self.assertIn("rc4.sh kernel", result.stderr)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
