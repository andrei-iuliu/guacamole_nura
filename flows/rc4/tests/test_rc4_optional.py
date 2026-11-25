#!/usr/bin/env python3
"""Optional kernel sets (flows/rc4/optional): opt-in only, pinned, marked.

No kernel is built.  `rc4.sh kernel` runs with a recording stub builder that
stops after printing the environment it was given, and the recipe checker runs
on throwaway copies of a set.
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS = pathlib.Path(__file__).resolve().parent
RC4 = TESTS.parent
WRAPPER = RC4 / "scripts/rc4.sh"
OPTIONAL = RC4 / "optional"
VENUS = OPTIONAL / "venus"
RECIPE = RC4 / "device/testing/linux-postmarketos-qcom-sm8150"

sys.path.insert(0, str(RC4 / "scripts"))
import check_recipe  # noqa: E402

STUB = """#!/bin/bash
echo "STUB OPTIONAL=[$NURA_KERNEL_OPTIONAL] DIR=[$NURA_KERNEL_OPTIONAL_DIR] ARGS=[$*]"
exit 3
"""


def read_conf(path):
    conf = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            conf[key.strip()] = value.strip()
    return conf


class WrapperTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="rc4-optional-",
                                                 dir=str(RC4 / "build-source")
                                                 if (RC4 / "build-source").is_dir() else None))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        stub = self.tmp / "stub-builder.sh"
        stub.write_text(STUB)
        stub.chmod(0o755)
        self.env = dict(os.environ, RC4_KERNEL_ROOT=str(self.tmp / "root"),
                        NURA_KERNEL_BUILDER=str(stub), PYTHONDONTWRITEBYTECODE="1")
        self.env.pop("NURA_KERNEL_OPTIONAL", None)

    def run_kernel(self, *args, env=None):
        return subprocess.run(["bash", str(WRAPPER), "kernel", *args],
                              capture_output=True, text=True, env=env or self.env)

    def test_default_build_has_no_optional_set(self):
        result = self.run_kernel()
        self.assertIn("STUB OPTIONAL=[]", result.stdout)
        self.assertNotIn("EXPERIMENTAL".lower(), result.stdout.lower().replace("stub", ""))

    def test_flag_passes_the_set_and_warns_it_is_experimental(self):
        result = self.run_kernel("--optional", "venus", "--other-arg")
        self.assertIn("STUB OPTIONAL=[venus] DIR=[%s]" % OPTIONAL, result.stdout)
        self.assertIn("ARGS=[--other-arg]", result.stdout)
        self.assertIn("optional kernel set 'venus' is experimental", result.stdout)

    def test_environment_variable_works_too(self):
        env = dict(self.env, NURA_KERNEL_OPTIONAL="venus")
        result = self.run_kernel(env=env)
        self.assertIn("STUB OPTIONAL=[venus]", result.stdout)

    def test_unknown_set_is_refused_before_the_builder(self):
        result = self.run_kernel("--optional", "no-such-set")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown optional set 'no-such-set'", result.stderr)
        self.assertNotIn("STUB", result.stdout)


class VenusSetTest(unittest.TestCase):
    def test_venus_is_marked_experimental_as_rc4t(self):
        conf = read_conf(VENUS / "set.conf")
        self.assertEqual(conf["name"], "venus")
        self.assertEqual(conf["status"], "experimental")
        self.assertEqual(conf["release"], "rc4t")
        self.assertEqual(conf["firmware_dest"], "qcom/sm8150/oneplus7")

    def test_default_recipe_carries_none_of_the_set(self):
        names = {p.name[5:] for p in (VENUS / "patches").glob("*.patch")}
        self.assertEqual(len(names), 49)
        self.assertFalse(names & {p.name[5:] for p in RECIPE.glob("0*.patch")})
        apkbuild = (RECIPE / "APKBUILD").read_text()
        self.assertNotIn("venus", "\n".join(
            l for l in apkbuild.splitlines() if l.startswith("\t0") or l[:1].isalnum() and ".patch" in l))

    def test_board_patch_points_at_the_split_firmware(self):
        board = next((VENUS / "patches").glob("0049-*.patch")).read_text()
        self.assertIn('firmware-name = "qcom/sm8150/oneplus7/venus.mdt";', board)


class CheckerTest(unittest.TestCase):
    def copy_set(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="optional-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        dest = tmp / "venus"
        shutil.copytree(VENUS, dest, ignore=shutil.ignore_patterns("*.tar.gz"))
        return tmp, dest

    def run_checker(self, root):
        check_recipe.failures.clear()
        check_recipe.check_optional_sets(root, RECIPE)
        return list(check_recipe.failures)

    def test_real_set_passes(self):
        root, _ = self.copy_set()
        self.assertEqual(self.run_checker(root), [])

    def test_tampered_patch_fails(self):
        root, dest = self.copy_set()
        patch = next((dest / "patches").glob("0001-*.patch"))
        patch.write_text(patch.read_text() + "\n")
        self.assertTrue(any("patch hashes" in f for f in self.run_checker(root)))

    def test_unpinned_patch_fails(self):
        root, dest = self.copy_set()
        (dest / "patches/0050-extra.patch").write_text("x\n")
        failures = self.run_checker(root)
        self.assertTrue(any("pins exactly" in f for f in failures))

    def test_missing_status_fails(self):
        root, dest = self.copy_set()
        conf = dest / "set.conf"
        conf.write_text(conf.read_text().replace("status=experimental", "status=maybe"))
        self.assertTrue(any("status" in f for f in self.run_checker(root)))


if __name__ == "__main__":
    unittest.main()
