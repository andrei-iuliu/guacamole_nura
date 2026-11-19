"""Exercise the manifest command dispatch without reading build artifacts."""
import contextlib
import io
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import check_plasma_candidate as checker


class CheckerCommandTests(unittest.TestCase):
    def test_kernel_manifest_dispatch_calls_the_function(self):
        args = ['kernel-manifest']
        for name in ('boot-img', 'initramfs', 'rootfs-image', 'rootfs-tree', 'apk-dir'):
            args += ['--' + name, '/fixture/' + name]
        with patch.object(checker, 'kernel_manifest', return_value={'verified': True}) as handler:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(checker.main(args), 0)
        handler.assert_called_once()
        self.assertIn('"verified": true', output.getvalue())

    def test_v2_payloads_match_real_mkbootimg_output(self):
        tool = Path(checker.ROOT) / 'local/tools/mkbootimg/mkbootimg.py'
        if not tool.is_file():
            self.skipTest('local mkbootimg unavailable')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {}
            for name, size in (('kernel', 4200), ('ramdisk', 2700), ('dtb', 5300)):
                files[name] = root / name
                files[name].write_bytes(name.encode() * size)
            boot = root / 'boot.img'
            command = [sys.executable, str(tool), '--header_version', '2', '--pagesize', '4096', '-o', str(boot)]
            for name, path in files.items():
                command += ['--' + name, str(path)]
            subprocess.run(command, check=True, capture_output=True)
            parts = checker.prove_boot_image(boot, files['kernel'], files['ramdisk'], files['dtb'], label='fixture')
            self.assertEqual(parts['header_size'], 1660)
            self.assertEqual(parts['dtb'], files['dtb'].read_bytes())
