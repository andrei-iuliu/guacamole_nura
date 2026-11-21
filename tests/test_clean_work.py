"""scripts/clean_work.sh (./nura clean) on a fake checkout."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/clean_work.sh"


class CleanWorkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.repo = root / "repo"
        self.keys = root / "keys"
        r = self.repo
        for d in (".work/baseline/.work", ".work/stale-build/kernel-state",
                  ".work/in-use/kernel-state", "build/keep",
                  "flows/rc4/local-storage/build/local-userdata-phosh",
                  "flows/rc4/local-storage/.work/local-userdata-phosh/kernel-state/output",
                  "flows/rc4/local-storage/.work/local-userdata-phosh/rootfs-state/pmbootstrap-work/config_abuild"):
            (r / d).mkdir(parents=True)
        self.state = r / "flows/rc4/local-storage/.work/local-userdata-phosh"
        self.work = self.state / "rootfs-state/pmbootstrap-work"
        (self.work / "config_abuild/pmos@local-1.rsa.pub").write_text("KEY1\n")
        (self.state / "rootfs-state/rootfs").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def run_clean(self, *args):
        env = dict(os.environ, NURA_SOURCE_ROOT=str(self.repo), NURA_SUDO="",
                   NURA_SIGNING_KEY_DIR=str(self.keys),
                   NURA_KERNEL_SOURCE_STATE=str(self.repo / ".work/in-use/kernel-state"))
        for v in ("NURA_BASELINE_ROOT", "RC4_KERNEL_ROOT", "NURA_KERNEL_BUILD", "NURA_KERNEL_DIR"):
            env.pop(v, None)
        return subprocess.run(["bash", str(SCRIPT), *args], env=env, text=True,
                              capture_output=True, check=True).stdout

    def test_listing_is_read_only(self):
        out = self.run_clean()
        self.assertIn(".work/stale-build", out)
        self.assertIn("kernel-copy", out)
        self.assertNotIn(".work/in-use", out)
        self.assertNotIn(".work/baseline", out)
        self.assertIn("only copy of pmos@local-1", out)
        self.assertTrue((self.repo / ".work/stale-build").is_dir())
        self.assertTrue((self.state / "kernel-state").is_dir())

    def test_apply_keeps_configured_paths_and_outputs(self):
        self.run_clean("--apply")
        self.assertFalse((self.repo / ".work/stale-build").exists())
        self.assertFalse((self.state / "kernel-state").exists())
        for kept in (".work/in-use/kernel-state", ".work/baseline", "build/keep",
                     "flows/rc4/local-storage/build/local-userdata-phosh"):
            self.assertTrue((self.repo / kept).is_dir(), kept)
        self.assertTrue(self.work.is_dir(), "rootfs needs --rootfs")

    def test_rootfs_refused_until_key_adopted(self):
        out = self.run_clean("--apply", "--rootfs")
        self.assertIn("kept", out)
        self.assertTrue(self.work.is_dir())

    def test_rootfs_refused_with_a_different_project_key(self):
        self.keys.mkdir()
        (self.keys / "pmos@local-2.rsa.pub").write_text("KEY2\n")
        out = self.run_clean("--apply", "--rootfs")
        self.assertIn("not the project key", out)
        self.assertTrue(self.work.is_dir())

    def test_rootfs_removed_once_key_adopted(self):
        self.keys.mkdir()
        (self.keys / "pmos@local-1.rsa.pub").write_text("KEY1\n")
        self.run_clean("--apply", "--rootfs")
        self.assertFalse(self.work.exists())
        self.assertTrue((self.state / "rootfs-state/rootfs").is_dir())


if __name__ == "__main__":
    unittest.main()
