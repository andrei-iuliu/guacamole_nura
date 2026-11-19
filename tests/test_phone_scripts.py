"""Exercise launch commands with mocked tools; never contact a phone or run sudo."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PhoneScriptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'checkout with spaces'
        (self.root / 'scripts').mkdir(parents=True)
        (self.root / 'platform-tools').mkdir()
        for name in ('boot_7_0.sh', 'boot_rear_camera.sh', 'serve_7_0.sh', 'serve_rear_camera.sh', 'ssh_phone.sh'):
            source = ROOT / 'scripts' / name
            if not source.exists():   # superseded launchers live in archive/scripts
                source = ROOT / 'archive/scripts' / name
            shutil.copy2(source, self.root / 'scripts' / name)
        image = self.root / 'build/7.0/boot.img'
        image.parent.mkdir(parents=True)
        image.touch()
        rear_image = self.root / 'build/rear-camera/boot.img'
        rear_image.parent.mkdir(parents=True)
        rear_image.touch()
        self.log = self.root / 'calls'
        self.environment = {**os.environ, 'PATH': f"{self.root / 'platform-tools'}:{os.environ['PATH']}",
                            'PHONE_TEST_LOG': str(self.log), 'NURA_USB_IFACE': 'test-usb0'}

    def tool(self, name, body=''):
        path = self.root / 'platform-tools' / name
        path.write_text('#!/usr/bin/env bash\n'
                        'printf "%s %s\\n" "${0##*/}" "$*" >> "$PHONE_TEST_LOG"\n' + body + '\n')
        path.chmod(0o755)

    def run_script(self, name, status=0):
        result = subprocess.run(['bash', str(self.root / 'scripts' / name)],
                                env=self.environment, cwd='/', capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, status, result.stdout + result.stderr)
        return self.log.read_text()

    def prepare_boot(self, in_fastboot=False, adb_status=0):
        self.tool('fastboot', 'echo "serial fastboot"' if in_fastboot else ':')
        self.tool('adb', f'exit {adb_status}')
        helper = self.root / 'scripts/fastboot_boot.sh'
        helper.write_text('printf "temporary-boot %s\\n" "$*" >> "$PHONE_TEST_LOG"\n')

    def test_android_reboots_then_boots_current_image(self):
        self.prepare_boot()
        log = self.run_script('boot_7_0.sh')
        self.assertIn('adb reboot bootloader\n', log)
        self.assertIn(f'temporary-boot {self.root}/build/7.0/boot.img\n', log)
        self.assertNotIn('flash', log)

    def test_fastboot_skips_adb(self):
        self.prepare_boot(in_fastboot=True)
        log = self.run_script('boot_7_0.sh')
        self.assertNotIn('adb ', log)
        self.assertIn('temporary-boot', log)

    def test_adb_failure_stops_before_boot(self):
        self.prepare_boot(adb_status=42)
        self.assertNotIn('temporary-boot', self.run_script('boot_7_0.sh', status=42))

    def prepare_server(self, has_address=False):
        self.tool('ip', 'if [[ "$*" == "-o -4 address show dev test-usb0" ]]; then\n'
                  + ('echo "1: test-usb0 inet 172.16.42.2/24 scope global test-usb0"\n' if has_address else ':\n')
                  + 'fi')
        self.tool('sudo')
        self.tool('python3')

    def test_server_adds_missing_address_and_uses_port_10000_without_replacing(self):
        self.prepare_server()
        log = self.run_script('serve_7_0.sh')
        self.assertIn('sudo ip address add 172.16.42.2/24 dev test-usb0\n', log)
        self.assertIn('netboot serve --port 10000\n', log)
        self.assertIn(f'-w {self.root}/.work/7.0/pmbootstrap-work', log)
        self.assertNotIn('--replace', log)

    def test_server_does_not_add_address_twice(self):
        self.prepare_server(has_address=True)
        self.assertNotIn('sudo ip address add', self.run_script('serve_7_0.sh'))

    def test_camera_server_adds_missing_address_and_serves_candidate(self):
        self.prepare_server()
        log = self.run_script('serve_rear_camera.sh')
        self.assertIn('sudo ip link set dev test-usb0 up\n', log)
        self.assertIn('sudo ip address add 172.16.42.2/24 dev test-usb0\n', log)
        self.assertIn('netboot serve --port 10001 --replace\n', log)
        self.assertIn(f'-w {self.root}/.work/rear-camera/pmbootstrap-work', log)
        self.assertIn('--replace', log)

    def test_camera_server_keeps_existing_address(self):
        self.prepare_server(has_address=True)
        self.assertNotIn('sudo ip address add', self.run_script('serve_rear_camera.sh'))

    def prepare_rear_boot(self, in_fastboot=False, adb_status=0):
        self.tool('fastboot', 'echo "serial fastboot"' if in_fastboot else ':')
        self.tool('adb', f'exit {adb_status}')

    def test_rear_boot_reboots_android_and_temporarily_boots_once(self):
        self.prepare_rear_boot()
        log = self.run_script('boot_rear_camera.sh')
        self.assertIn('adb reboot bootloader\n', log)
        self.assertIn(f'fastboot boot {self.root}/build/rear-camera/boot.img\n', log)
        self.assertEqual(log.count('fastboot boot '), 1)
        self.assertNotIn('flash', log)

    def test_rear_boot_skips_adb_if_already_in_fastboot(self):
        self.prepare_rear_boot(in_fastboot=True)
        log = self.run_script('boot_rear_camera.sh')
        self.assertNotIn('adb ', log)
        self.assertEqual(log.count('fastboot boot '), 1)

    def test_rear_boot_stops_if_adb_reboot_fails(self):
        self.prepare_rear_boot(adb_status=42)
        log = self.run_script('boot_rear_camera.sh', status=42)
        self.assertNotIn('fastboot boot ', log)

    def test_ssh_keeps_strict_host_key_checks_in_an_isolated_file(self):
        self.tool('ssh')
        log = self.run_script('ssh_phone.sh')
        command = log.strip()
        self.assertIn('ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=ask', command)
        self.assertIn('UserKnownHostsFile=', command)
        self.assertIn('.work/delivery-plasma-mobile/known_hosts', command)
        self.assertIn('HostKeyAlias=172.16.42.1', command)
        self.assertTrue(command.endswith('user@172.16.42.1'))

    def test_ssh_pins_one_alias_per_plasma_boot(self):
        self.tool('ssh')
        receipt = self.root / 'build/plasma-mobile/last-boot.json'
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(
            '{"result": "success", "worktree": "%s", '
            '"finished_at": "2026-11-12T00:00:00Z", "device": {"serial": "ABC123"}}\n'
            % self.root)
        command = self.run_script('ssh_phone.sh').strip()
        self.assertIn('HostKeyAlias=nura-plasma-', command)
        self.assertIn('user@172.16.42.1', command)
