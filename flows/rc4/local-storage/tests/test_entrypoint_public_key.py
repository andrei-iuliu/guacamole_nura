"""Public key is forwarded as one argument; no build/admin operation runs."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

FLOW = Path(__file__).resolve().parents[1]


class PublicKeyForwardingTests(unittest.TestCase):
    def test_rootfs_receives_optional_public_key_with_spaces(self):
        source = (FLOW / 'scripts/local_storage_build.sh').read_text()
        function = source.split('run_rootfs() {', 1)[1].split('\nrun_boot_image()', 1)[0]
        for use_key in (False, True):
            with self.subTest(use_key=use_key), tempfile.TemporaryDirectory() as directory:
                fake = Path(directory) / 'fake rootfs.sh'
                fake.write_text('''#!/bin/bash
[[ "$1" == --build ]] || exit 7
if [[ -n "$AUTHORIZED_KEY" ]]; then
    [[ $# == 3 && "$2" == --authorized-key && "$3" == "$AUTHORIZED_KEY" ]] || exit 8
else
    [[ $# == 1 ]] || exit 9
fi
''')
                env = dict(os.environ, ROOTFS_SCRIPT=str(fake), AUTHORIZED_KEY=(directory+'/key with spaces.pub' if use_key else ''))
                names = ('NURA_UI SOURCE_ROOT ROOTFS_STATE LOCAL_OUT KERNEL_STATE KERNEL_BUILD HOOK_RECIPE HOOK_VERSION TOOL HELPER_SCRIPTS')
                shell = 'set -euo pipefail\n'
                shell += '\n'.join(name+'=fixture' for name in names.split())+'\n'
                shell += 'run_rootfs() {'+function+'\nrun_rootfs\n'
                result = subprocess.run(['bash', '-c', shell], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
