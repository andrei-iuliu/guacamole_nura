"""Shutdown ordering with fake mount/process tools; never invokes real pmbootstrap."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import json
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PlasmaShutdownTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'checkout with spaces'
        (self.root / 'scripts').mkdir(parents=True)
        shutil.copy2(ROOT / 'scripts/plasma_shutdown.sh', self.root / 'scripts')
        self.state = self.root / '.work/plasma-mobile'
        for directory in ('pmbootstrap', 'rootfs', 'pmaports', 'pmbootstrap-work'):
            (self.state / directory).mkdir(parents=True)
        (self.state / 'pmbootstrap/pmbootstrap.py').touch()
        (self.state / 'rootfs/pmbootstrap.cfg').touch()
        self.marker = self.root / 'mounted'
        self.log = self.root / 'calls'
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.stopped = self.root / 'stopped'
        self.stop_log = self.root / 'stop-calls'
        self.tool('ss', '[[ ! -f "$TEST_STOPPED" ]] || exit 0\nprintf "%s" "${TEST_LISTENER:-}"\n')
        self.tool('findmnt', '''
[[ ${TEST_FINDMNT_FAIL:-0} == 0 ]] || exit 2
if [[ -f "$TEST_MOUNT_MARKER" ]]; then printf '%s\\n' "$TEST_MOUNT_TARGET"; fi
''')
        self.tool('python3', '''
if [[ ${1:-} == - ]]; then exec "$TEST_REAL_PYTHON" "$@"; fi
printf '%s\\n' "$@" > "$TEST_CALLS"
[[ ${TEST_SHUTDOWN_FAIL:-0} == 0 ]] || exit 2
if [[ ${TEST_REMAIN:-0} == 0 ]]; then rm -- "$TEST_MOUNT_MARKER"; fi
''')
        tools = self.root / 'platform-tools'
        tools.mkdir()
        self.tool('sudo', 'printf "%s\\n" "$@" > "$TEST_STOP_LOG"\ntouch "$TEST_STOPPED"\n')
        for name, body in (
            ('adb', '''
[[ ${TEST_ANDROID:-0} == 1 && ${2:-} == PHONE123 ]] || exit 1
case ${3:-} in get-state) echo device ;; shell) echo 30 ;; *) exit 1 ;; esac
'''),
            ('fastboot', 'printf "%s\\n" "${TEST_FASTBOOT:-}"\n'),
        ):
            target = tools / name
            target.write_text('#!/usr/bin/env bash\nset -e\n' + body)
            target.chmod(0o755)

    def tool(self, name, body):
        path = self.bin / name
        path.write_text('#!/usr/bin/env bash\nset -e\n' + body)
        path.chmod(0o755)

    def run_shutdown(self, **extra):
        return subprocess.run(
            ['bash', str(self.root / 'scripts/plasma_shutdown.sh')],
            env={**os.environ, 'PATH': f'{self.bin}:{os.environ["PATH"]}',
                 'TEST_MOUNT_MARKER': str(self.marker),
                 'TEST_MOUNT_TARGET': str(self.state / 'pmbootstrap-work/chroot_native/proc'),
                 'TEST_CALLS': str(self.log), 'TEST_STOPPED': str(self.stopped),
                 'TEST_STOP_LOG': str(self.stop_log), 'TEST_REAL_PYTHON': sys.executable,
                 **extra},
            text=True, capture_output=True, timeout=10)

    def test_no_mounts_skips_shutdown(self):
        self.assertEqual(self.run_shutdown().returncode, 0)
        self.assertFalse(self.log.exists())

    def test_shutdown_pins_plasma_state_then_rechecks_mounts(self):
        self.marker.touch()
        target = str(self.state / 'pmbootstrap-work/chroot_native/proc').replace(' ', r'\x20')
        result = self.run_shutdown(TEST_MOUNT_TARGET=target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), [
            str(self.state / 'pmbootstrap/pmbootstrap.py'),
            '-c', str(self.state / 'rootfs/pmbootstrap.cfg'),
            '-p', str(self.state / 'pmaports'),
            '-w', str(self.state / 'pmbootstrap-work'), 'shutdown'])
        self.assertFalse(self.marker.exists())

    def test_other_profile_mounts_are_untouched(self):
        self.marker.touch()
        result = self.run_shutdown(TEST_MOUNT_TARGET=str(self.root / '.work/rear-camera/chroot_native'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.marker.exists())
        self.assertFalse(self.log.exists())

    def test_live_server_prevents_shutdown(self):
        self.marker.touch()
        result = self.run_shutdown(TEST_LISTENER='LISTEN 0 1 172.16.42.2:10001')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('never stops another server', result.stderr)
        self.assertFalse(self.log.exists())

    def remember_phone(self):
        receipt = self.root / 'build/plasma-mobile/last-boot.json'
        receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps({'device': {'serial': 'PHONE123'}}))

    def test_android_proof_allows_owned_server_cleanup_then_unmount(self):
        self.remember_phone()
        self.marker.touch()
        result = self.run_shutdown(TEST_LISTENER='LISTEN :10001', TEST_ANDROID='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Phone PHONE123 is in Android', result.stdout)
        self.assertEqual(self.stop_log.read_text().splitlines(), [
            'python3', str(self.root / 'scripts/stop_plasma_server.py'),
            str(self.state / 'pmbootstrap-work'), '10001'])
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.log.read_text().splitlines()[-1], 'shutdown')

    def test_matching_fastboot_phone_allows_cleanup(self):
        self.remember_phone()
        result = self.run_shutdown(TEST_LISTENER='LISTEN :10001',
                                   TEST_FASTBOOT='PHONE123\tfastboot')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.stop_log.exists())

    def test_disconnect_or_different_phone_never_stops_server(self):
        self.remember_phone()
        for fastboot in ('', 'OTHERPHONE\tfastboot'):
            with self.subTest(fastboot=fastboot):
                result = self.run_shutdown(TEST_LISTENER='LISTEN :10001', TEST_FASTBOOT=fastboot)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('disconnect alone is insufficient', result.stderr)
                self.assertFalse(self.stop_log.exists())

    def test_shutdown_failure_stops_without_retry(self):
        self.marker.touch()
        result = self.run_shutdown(TEST_SHUTDOWN_FAIL='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('shutdown failed', result.stderr)
        self.assertTrue(self.marker.exists())
        self.assertEqual(self.log.read_text().splitlines().count('shutdown'), 1)

    def test_leftover_mounts_fail_after_shutdown(self):
        self.marker.touch()
        result = self.run_shutdown(TEST_REMAIN='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('mounts remain', result.stderr)

    def test_unverifiable_mounts_fail_closed(self):
        result = self.run_shutdown(TEST_FINDMNT_FAIL='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('cannot inspect mounts', result.stderr)
        self.assertFalse(self.log.exists())

    def test_symlinked_work_is_refused(self):
        work = self.state / 'pmbootstrap-work'
        work.rmdir()
        work.symlink_to(self.root)
        result = self.run_shutdown()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('resolves outside', result.stderr)
        self.assertFalse(self.log.exists())
