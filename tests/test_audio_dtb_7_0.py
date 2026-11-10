"""A speaker codec cannot probe beneath a disabled parent bus."""

import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
DTB = ROOT / '.work/7.0/build-7.0-clean/arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole.dtb'


class SpeakerBusTests(unittest.TestCase):
    def test_speaker_codecs_and_every_parent_are_available(self):
        if not DTB.is_file():
            self.skipTest('7.0 board DTB has not been built')
        for address in ('34', '35'):
            node = f'/soc@0/geniqup@8c0000/i2c@890000/audio-codec@{address}'
            while node != '/':
                with self.subTest(node=node):
                    result = subprocess.run(
                        [str(ROOT / 'local/bin/fdtget'), '-t', 's', str(DTB), node, 'status'],
                        capture_output=True, text=True,
                        env={**os.environ, 'LD_LIBRARY_PATH': str(ROOT / 'local/lib')},
                    )
                    if result.returncode:
                        self.assertIn('FDT_ERR_NOTFOUND', result.stderr)
                        # A missing status is enabled, but a missing node is an error.
                        exists = subprocess.run(
                            [str(ROOT / 'local/bin/fdtget'), '-p', str(DTB), node],
                            capture_output=True, env={**os.environ, 'LD_LIBRARY_PATH': str(ROOT / 'local/lib')},
                        )
                        self.assertEqual(exists.returncode, 0)
                    else:
                        self.assertIn(result.stdout.strip(), ('ok', 'okay'))
                node = node.rsplit('/', 1)[0] or '/'
