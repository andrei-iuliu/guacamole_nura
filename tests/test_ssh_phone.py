"""User-run SSH helper checks with fake ssh and ssh-keygen; no phone access."""
import os
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SshPhoneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'checkout with spaces'
        (self.root / 'scripts').mkdir(parents=True)
        shutil.copy2(ROOT / 'scripts/ssh_phone.sh', self.root / 'scripts')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.log = self.root / 'calls'
        for command in ('ssh', 'ssh-keygen'):
            path = self.bin / command
            path.write_text('#!/bin/sh\nprintf "%s\\n" "${0##*/}" "$@" >> "$SSH_TEST_LOG"\n')
            path.chmod(0o755)
        self.known = self.root / '.work/delivery-plasma-mobile/known_hosts'

    def run_script(self, *args):
        return subprocess.run(
            ['bash', str(self.root / 'scripts/ssh_phone.sh'), *args],
            env={**os.environ, 'PATH': f'{self.bin}:{os.environ["PATH"]}',
                 'SSH_TEST_LOG': str(self.log)},
            capture_output=True, text=True, timeout=5)

    def test_normal_connection_checks_project_key_without_removing_it(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), [
            'ssh', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=ask',
            '-o', f'UserKnownHostsFile={self.known}', '-o', 'HostKeyAlias=172.16.42.1',
            'user@172.16.42.1'])

    def write_boot(self, stamp):
        receipt = self.root / 'build/plasma-mobile/last-boot.json'
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps({'result': 'success', 'worktree': str(self.root),
                                      'finished_at': stamp, 'device': {'serial': 'PHONE'}}))

    def test_boot_pin_is_stable_until_another_recorded_boot(self):
        self.write_boot('2026-11-11T17:02:56Z')
        self.assertEqual(self.run_script().returncode, 0)
        first = self.log.read_text().splitlines()
        alias = next(arg for arg in first if arg.startswith('HostKeyAlias='))
        self.assertTrue(alias.startswith('HostKeyAlias=nura-plasma-'))
        self.log.unlink()
        self.run_script()
        self.assertEqual(self.log.read_text().splitlines(), first)
        self.log.unlink()
        self.write_boot('2026-11-11T18:02:56Z')
        self.run_script()
        self.assertNotIn(alias, self.log.read_text().splitlines())

    def test_reset_only_removes_the_current_boot_alias(self):
        self.write_boot('2026-11-11T17:02:56Z')
        self.known.parent.mkdir(parents=True)
        self.known.write_text('fixture keys\n')
        self.assertEqual(self.run_script('--forget-host-key').returncode, 0)
        calls = self.log.read_text().splitlines()
        self.assertTrue(calls[2].startswith('nura-plasma-'))
        self.assertIn('HostKeyAlias=' + calls[2], calls)

    def test_explicit_reset_only_targets_project_phone_key(self):
        self.known.parent.mkdir(parents=True)
        self.known.write_text('fixture key\n')
        result = self.run_script('--forget-host-key')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.log.read_text().splitlines()
        self.assertEqual(calls[:5], ['ssh-keygen', '-R', '172.16.42.1', '-f', str(self.known)])
        self.assertIn('verify the new fingerprint', result.stderr)
        self.assertEqual(calls[5], 'ssh')

    def test_unrecognized_options_do_not_connect(self):
        self.assertEqual(self.run_script('--insecure').returncode, 2)
        self.assertFalse(self.log.exists())
