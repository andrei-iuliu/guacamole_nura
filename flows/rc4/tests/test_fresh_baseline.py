"""Rootless tests for the fresh-baseline mode (NURA_FRESH_BASELINE=1) of the
rc4 local-storage flow and for scripts/bootstrap_baseline.sh --check.

No network, sudo, pmbootstrap or phone is used.
"""
import gzip
import hashlib
import importlib.util
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

RC4 = Path(__file__).resolve().parents[1]
REPO = RC4.parents[1]
SCRIPTS = RC4 / "local-storage/scripts"
CHECKER = SCRIPTS / "check_local_candidate.py"


def load_checker(**env):
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        spec = importlib.util.spec_from_file_location(f"clc_fresh_{len(env)}_{id(env)}", CHECKER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def tar_member(files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return gzip.compress(buf.getvalue())


def fake_apk(path: Path, name: str, version: str, files: dict) -> Path:
    pkginfo = f"pkgname = {name}\npkgver = {version}\n".encode()
    path.write_bytes(tar_member({".SIGN.RSA.test.rsa.pub": b"sig"})
                     + tar_member({".PKGINFO": pkginfo}) + tar_member(files))
    return path


class Recipes(unittest.TestCase):
    def setUp(self):
        self.clc = load_checker(NURA_FRESH_BASELINE="1", NURA_LOCAL_USERDATA="1")

    def apkbuild(self, text):
        d = tempfile.mkdtemp()
        p = Path(d) / "APKBUILD"
        p.write_text(text)
        return p

    def test_version_parsing(self):
        v = self.clc.apkbuild_version
        self.assertEqual(v(self.apkbuild("pkgname=x\n_pkgver=0.8.0\npkgver=9999$_pkgver\npkgrel=1\n")),
                         "99990.8.0-r1")
        self.assertEqual(v(self.apkbuild("pkgver=0.7.2\npkgrel=0\n")), "0.7.2-r0")
        for bad in ("pkgver=$(date)\npkgrel=0\n", "pkgver=1.0\npkgrel=x\n", "pkgrel=0\n"):
            with self.assertRaises(SystemExit):
                v(self.apkbuild(bad))

    def test_real_libcamera_rear_recipe(self):
        self.assertEqual(self.clc.apkbuild_version(self.clc.ROOT / self.clc.LIBCAMERA_REAR_RECIPE / "APKBUILD"),
                         self.clc.LIBRARY_REAR)

    def test_libcamera_rear_version_follows_the_baseline(self):
        # A fresh baseline builds the recipe (r1); rear1 baseline APKs are r0.
        self.assertEqual(self.clc.LIBRARY_REAR, "0.7.2-r1")
        self.assertEqual(load_checker(NURA_LOCAL_USERDATA="1").LIBRARY_REAR, "0.7.2-r0")

    def test_fresh_pins_need_matching_recipes(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            recipe = state / "pmaports/temp/libcamera"
            recipe.mkdir(parents=True)
            (recipe / "APKBUILD").write_text("_pkgver=0.8.0\npkgver=9999$_pkgver\npkgrel=1\n")
            pins = self.clc.baseline_pins(Path("/nonexistent"), state)
            self.assertTrue(pins["fresh"])
            self.assertIsNone(pins["rootfs"])
            self.assertEqual(pins["system_libcamera"]["libcamera-ipa"]["version"], self.clc.LIBCAMERA_ABI)
            self.assertEqual(pins["apks"]["libcamera-rear"]["version"], self.clc.LIBRARY_REAR)
            (recipe / "APKBUILD").write_text("_pkgver=0.9.0\npkgver=9999$_pkgver\npkgrel=1\n")
            with self.assertRaises(SystemExit):
                self.clc.baseline_pins(Path("/nonexistent"), state)

    def test_baseline_root_env_is_honoured(self):
        clc = load_checker(NURA_BASELINE_ROOT="/some/baseline")
        self.assertEqual(clc.BASELINE_STATE, Path("/some/baseline/.work/rear-camera"))
        self.assertEqual(clc.BASELINE_BUILD, Path("/some/baseline/build/rear-camera"))

    def test_netboot_consumers_refuse_fresh(self):
        import argparse
        with self.assertRaises(SystemExit):
            self.clc.manifest(argparse.Namespace())


class ApkFiles(unittest.TestCase):
    def test_apk_file_digest(self):
        clc = load_checker()
        with tempfile.TemporaryDirectory() as tmp:
            lib = b"\x7fELF fake libcamera"
            apk = fake_apk(Path(tmp) / "libcamera-99990.8.0-r1.apk", "libcamera", "99990.8.0-r1",
                           {"usr/lib/libcamera.so.0.8.0": lib})
            self.assertEqual(clc.apk_file_digest(apk, "usr/lib/libcamera.so.0.8.0"),
                             hashlib.sha256(lib).hexdigest())
            self.assertIsNone(clc.apk_file_digest(apk, "usr/lib/missing.so"))
            self.assertEqual(clc.parse_apk(apk, "libcamera"), "99990.8.0-r1")


class VerifyCli(unittest.TestCase):
    def run_verify(self, *extra):
        return subprocess.run(
            [sys.executable, str(CHECKER), "verify", "--kernel-dir", "/x", "--rootfs", "/x",
             "--initramfs", "/x", "--expected-vmlinuz-sha", "0", "--expected-dtb-sha", "0",
             "--expected-module-payload-sha", "0", "--expected-module-count", "1",
             "--ui-version", "1", *extra],
            capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))

    def test_exactly_one_libcamera_reference(self):
        self.assertIn("one of the arguments", self.run_verify().stderr)
        both = self.run_verify("--known-good-rootfs", "/a", "--built-apk-dir", "/b")
        self.assertIn("not allowed with", both.stderr)


class BootstrapCheck(unittest.TestCase):
    def test_check_mode_never_fetches(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run(["bash", str(REPO / "scripts/bootstrap_baseline.sh"), "--check", tmp],
                               capture_output=True, text=True, timeout=30)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("missing (run without --check", r.stderr)
            self.assertFalse((Path(tmp) / ".work/rear-camera/pmaports").exists())


if __name__ == "__main__":
    unittest.main()
