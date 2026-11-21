"""The candidate pmbootstrap takes apk.static from the pinned 3.0.9 package.

apk-tools 3.0.10 fails to commit systemd files on fresh installs
(alpine/apk-tools#11231); check_local_candidate.pin_apk_static() patches the
candidate pmbootstrap so rootfs_prepare_local.sh can hand it a pinned package.
"""
import py_compile
import re
import tempfile
import unittest
from pathlib import Path

from test_fresh_baseline import RC4, load_checker

# The shape of pmb/helpers/apk_static.py at the pinned pmbootstrap commit.
UPSTREAM = '''import os
from pathlib import Path

import pmb.helpers.apk


def extract(version: str, apk_path: Path) -> None:
    pass


def init() -> None:
    """Download, verify, extract $WORK/apk.static."""
    # Get and parse the APKINDEX.
    version = "x"
'''


class ApkStaticPin(unittest.TestCase):
    def setUp(self):
        self.checker = load_checker()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.file = self.root / "pmb/helpers/apk_static.py"
        self.file.parent.mkdir(parents=True)
        self.file.write_text(UPSTREAM)

    def tearDown(self):
        self.tmp.cleanup()

    def test_pin_is_inserted_once_and_compiles(self):
        self.checker.pin_apk_static(self.root)
        once = self.file.read_text()
        self.checker.pin_apk_static(self.root)
        self.assertEqual(self.file.read_text(), once)
        self.assertEqual(once.count("NURA_APK_STATIC_APK"), 1)
        body = once[once.index("def init"):]
        self.assertLess(body.index("NURA_APK_STATIC_APK"), body.index("# Get and parse"))
        self.assertIn("extract(version, Path(pinned))", body)
        py_compile.compile(str(self.file), doraise=True)

    def test_changed_upstream_is_refused(self):
        self.file.write_text(UPSTREAM.replace("extract $WORK/apk.static", "fetch apk.static"))
        with self.assertRaises(SystemExit):
            self.checker.pin_apk_static(self.root)

    def test_stale_pin_is_refused(self):
        self.file.write_text(UPSTREAM.replace(
            '"""\n', '"""\n' + self.checker.APK_STATIC_PIN_MARK + " old\n", 1))
        with self.assertRaises(SystemExit):
            self.checker.pin_apk_static(self.root)

    def test_flow_and_fetch_agree_on_the_pin(self):
        flow = (RC4 / "local-storage/scripts/rootfs_prepare_local.sh").read_text()
        fetch = (RC4.parent.parent / "scripts/fetch_inputs.sh").read_text()
        version = re.search(r"^APK_STATIC_VERSION=(\S+)$", flow, re.M).group(1)
        sha = re.search(r"^APK_STATIC_SHA256=(\S+)$", flow, re.M).group(1)
        self.assertIn(f'APK_STATIC_FILE="apk-tools-static-{version}.apk"', fetch)
        self.assertIn(sha, fetch)


if __name__ == "__main__":
    unittest.main()
