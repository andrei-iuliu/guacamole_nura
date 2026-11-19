"""Host-only regression tests for scripts/camera_boot.sh.

Every test builds a throw-away candidate checkout with fake artifacts and fake
adb/fastboot/ss tools, so no phone, cable, root privilege or real artifact is
touched.  The fake tools record their argv in $CALL_LOG, which is how the tests
prove that --dry-run builds the right command and that nothing flashes.
"""

import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/camera_boot.sh'
BASH = shutil.which('bash') or '/bin/bash'
RELEASE = '7.0.0-sm8150-rear1'
CCI_SHA256 = '4cd35f05ff873700c43baad265efd0bf73166352baac79f88d4aeca456fb27e4'
BASELINE_BOOT_SHA256 = 'a449a4dd8d8ed6bdc76e4417c66e5633d273afc58548ef963bdcc84c190c8860'

DTB = b'\xd0\x0d\xfe\xed' + b'DTB-FIXTURE' + b'\x01' * 2000
INITRAMFS = b'INITRAMFS-FIXTURE' + b'\x02' * 5000
IMAGE = b'IMAGE-FIXTURE' + b'\x03' * 4000
MODULE = b'CCI-FIXED-FIXTURE' + b'\x04' * 8000
ROOTFS = b'ROOTFS-FIXTURE' + b'\x05' * 9000 + MODULE + b'\x06' * 500


def digest(data):
    return hashlib.sha256(data).hexdigest()


def pad_page(data):
    return data + b'\x00' * (-len(data) % 4096)


def boot_image():
    """A fake boot.img with the DTB and initramfs at page-aligned offsets."""
    return pad_page(b'\x00' * 4096 + DTB) + INITRAMFS


class Checkout:
    """A minimal, self-contained candidate checkout used by every test."""

    def __init__(self, base, name='candidate checkout'):
        self.root = Path(base) / name
        self.log = self.root / 'calls.log'
        self.fakebin = self.root / 'fakebin'
        self.script = self.root / 'scripts' / 'camera_boot.sh'
        self.build()

    # -- construction ------------------------------------------------------
    def write(self, relative, data, mode=None):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            data = data.encode()
        path.write_bytes(data)
        if mode is not None:
            path.chmod(mode)
        return path

    def build(self):
        (self.root / 'scripts').mkdir(parents=True)
        shutil.copy2(SCRIPT, self.script)
        for name in ('plasma_boot.sh', 'plasma_serve.sh', 'camera_serve.sh'):
            if (ROOT / 'scripts' / name).is_file():
                shutil.copy2(ROOT / 'scripts' / name, self.root / 'scripts' / name)
        self.write('scripts/build_bootimg_rear_camera.sh', '#!/bin/sh\nexit 1\n', 0o755)
        (self.root / 'device/testing/rear-camera/linux-postmarketos-qcom-sm8150').mkdir(parents=True)

        self.write('build/rear-camera/boot.img', boot_image())
        self.write('build/rear-camera/Image', IMAGE)
        self.write('build/rear-camera/kernel-package/boot/vmlinuz', gzip.compress(IMAGE))
        self.write('build/rear-camera/kernel-package/boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb', DTB)
        self.write('build/rear-camera/kernel-package/usr/share/kernel/postmarketos-qcom-sm8150/kernel.release',
                   RELEASE + '\n')
        self.write('build/rear-camera/kernel-package/usr/lib/modules/%s/kernel/drivers/i2c/busses/'
                   'i2c-qcom-cci.ko.zst' % RELEASE, MODULE)
        self.write('.work/rear-camera/rootfs/export/initramfs', INITRAMFS)
        self.write('.work/rear-camera/pmbootstrap-work/chroot_native/home/pmos/rootfs/oneplus-guacamole.img',
                   ROOTFS)
        self.write('.work/rear-camera/pmbootstrap-work/images_netboot/oneplus-guacamole.img', ROOTFS)
        self.write('build/rear-camera/packaging-inputs.json', json.dumps({
            'release': RELEASE,
            'nbd_port': 10001,
            'initramfs_sha256': digest(INITRAMFS),
            'vmlinuz_sha256': digest((self.root / 'build/rear-camera/kernel-package/boot/vmlinuz').read_bytes()),
            'rootfs_image_sha256': digest(ROOTFS),
        }, indent=2) + '\n')
        self.write_manifest()
        self.write_tools()

    def entry(self, relative):
        path = self.root / relative
        return {'path': relative, 'sha256': digest(path.read_bytes()), 'size': path.stat().st_size}

    def write_manifest(self, **overrides):
        document = {
            'release': RELEASE,
            'worktree': os.path.realpath(str(self.root)),
            'created_at': '2026-11-10T00:00:00Z',
            'nbd_port': 10001,
            'boot_img': self.entry('build/rear-camera/boot.img'),
            'image': self.entry('build/rear-camera/Image'),
            'kernel_package': self.entry('build/rear-camera/kernel-package/boot/vmlinuz'),
            'dtb': self.entry('build/rear-camera/kernel-package/boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb'),
            'initramfs': self.entry('.work/rear-camera/rootfs/export/initramfs'),
            'rootfs_image': self.entry(
                '.work/rear-camera/pmbootstrap-work/chroot_native/home/pmos/rootfs/oneplus-guacamole.img'),
            'i2c_qcom_cci': self.entry(
                'build/rear-camera/kernel-package/usr/lib/modules/%s/kernel/drivers/i2c/busses/'
                'i2c-qcom-cci.ko.zst' % RELEASE),
        }
        for key, value in overrides.items():
            if value is None:
                document.pop(key, None)
            else:
                document[key] = value
        self.write('build/rear-camera/DELIVERY.json', json.dumps(document, indent=2) + '\n')
        return document

    def write_tools(self):
        self.write('fakebin/ss', '''#!/usr/bin/env python3
import os
import sys
with open(os.environ['CALL_LOG'], 'a') as log:
    log.write('ss ' + ' '.join(sys.argv[1:]) + '\\n')
print(os.environ.get('SS_LISTEN', ''))
''', 0o755)
        self.write('platform-tools/adb', '''#!/usr/bin/env python3
import os
import sys
args = sys.argv[1:]
with open(os.environ['CALL_LOG'], 'a') as log:
    log.write('adb ' + ' '.join(args) + '\\n')
def env(name):
    return os.environ.get(name, '')
if args[:1] == ['devices']:
    print(env('ADB_DEVICES'))
elif 'shell' in args and 'getprop' in args:
    prop = args[args.index('getprop') + 1]
    print(env('ADB_PROP_' + prop.replace('.', '_').upper()))
elif 'reboot' in args:
    sys.exit(int(env('ADB_REBOOT_STATUS') or '0'))
sys.exit(0)
''', 0o755)
        self.write('platform-tools/fastboot', '''#!/usr/bin/env python3
import os
import sys
args = sys.argv[1:]
with open(os.environ['CALL_LOG'], 'a') as log:
    log.write('fastboot ' + ' '.join(args) + '\\n')
if args[:1] == ['-s']:
    args = args[2:]
def env(name, default=''):
    return os.environ.get(name, default)
if args[:1] == ['devices']:
    print(env('FASTBOOT_DEVICES'))
elif args[:1] == ['getvar']:
    name = args[1]
    sys.stderr.write('%s: %s\\n' % (name, env('FASTBOOT_' + name.replace('-', '_').upper())))
elif args[:1] == ['boot']:
    sys.stdout.write(env('FASTBOOT_BOOT_OUTPUT', "Sending 'boot.img' (100 KB) OKAY\\nBooting OKAY\\n"))
    sys.exit(int(env('FASTBOOT_BOOT_STATUS') or '0'))
sys.exit(0)
''', 0o755)

    # -- execution ---------------------------------------------------------
    def env(self, **overrides):
        environment = dict(os.environ)
        environment.pop('CAMERA_BOOT_CCI_SHA256', None)
        environment.pop('CAMERA_BOOT_ATTEMPT_SECONDS', None)
        environment.update({
            'PATH': '%s:%s' % (self.fakebin, os.environ['PATH']),
            'CALL_LOG': str(self.log),
            'CAMERA_BOOT_CCI_SHA256': digest(MODULE),
            'ADB_DEVICES': 'ABC123\tdevice',
            'ADB_PROP_RO_PRODUCT_DEVICE': 'guacamole',
            'ADB_PROP_RO_PRODUCT_MODEL': 'GM1911',
            'ADB_PROP_RO_BOOT_SLOT_SUFFIX': '_b',
            'ADB_PROP_RO_BOOT_VERIFIEDBOOTSTATE': 'orange',
            'ADB_REBOOT_STATUS': '0',
            'FASTBOOT_DEVICES': '',
            'FASTBOOT_PRODUCT': 'guacamole',
            'FASTBOOT_CURRENT_SLOT': 'b',
            'FASTBOOT_BOOT_OUTPUT': "Sending 'boot.img' (100 KB) OKAY\nBooting OKAY\n",
            'FASTBOOT_BOOT_STATUS': '0',
            'SS_LISTEN': 'LISTEN 0 4096 0.0.0.0:10001 0.0.0.0:*',
        })
        environment.update({key: str(value) for key, value in overrides.items()})
        return environment

    def run(self, *arguments, script=None, cwd='/', **overrides):
        result = subprocess.run(
            [BASH, script or str(self.script), *arguments],
            cwd=cwd, env=self.env(**overrides), capture_output=True, text=True, timeout=120)
        return result

    def calls(self):
        return self.log.read_text() if self.log.exists() else ''

    def clear_calls(self):
        if self.log.exists():
            self.log.unlink()

    def last_boot(self):
        return json.loads((self.root / 'build/rear-camera/last-boot.json').read_text())


class CameraBootTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.checkout = Checkout(self.temporary.name)

    # -- help / usage ------------------------------------------------------
    def test_help_lists_subcommands_and_options(self):
        result = self.checkout.run('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Usage: camera_boot.sh', result.stdout)
        for word in ('boot', 'bootloader', 'android', 'status', '--dry-run',
                     '--serial', '--no-server-check', '--timeout', '--retries',
                     '--accept-current', 'flashes, erases, formats'):
            self.assertIn(word, result.stdout)

    def test_unknown_subcommand_is_rejected(self):
        result = self.checkout.run('frobnicate')
        self.assertEqual(result.returncode, 2)
        self.assertIn('unknown subcommand', result.stderr)

    def test_flash_and_erase_are_refused(self):
        for verb in ('flash', 'erase', 'format'):
            result = self.checkout.run(verb)
            self.assertEqual(result.returncode, 2, verb)
            self.assertIn('never flashes', result.stderr, verb)
        self.assertEqual(self.checkout.calls(), '', 'no tool may run for a refused verb')

    def test_retries_are_capped(self):
        result = self.checkout.run('boot', '--retries', '4')
        self.assertEqual(result.returncode, 2)
        self.assertIn('capped at 3', result.stderr)

    # -- manifest ----------------------------------------------------------
    def test_missing_manifest_refuses_with_build_command(self):
        (self.checkout.root / 'build/rear-camera/DELIVERY.json').unlink()
        result = self.checkout.run('status', '--no-server-check')
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn('no delivery manifest', result.stderr)
        self.assertIn('camera_build.sh', result.stderr)
        self.assertIn('--accept-current', result.stderr)

    def test_wrong_release_in_manifest_refuses(self):
        self.checkout.write_manifest(release='6.17.0-sm8150')
        result = self.checkout.run('status', '--no-server-check')
        self.assertEqual(result.returncode, 3)
        self.assertIn("manifest release '6.17.0-sm8150'", result.stderr)

    def test_manifest_from_another_worktree_refuses(self):
        self.checkout.write_manifest(worktree='guacamole_nura-workspace/somewhere-else')
        result = self.checkout.run('status', '--no-server-check')
        self.assertEqual(result.returncode, 3)
        self.assertIn('is not this checkout', result.stderr)

    def test_manifest_boot_image_path_must_be_the_candidate(self):
        self.checkout.write('build/7.0/boot.img', boot_image())
        document = self.checkout.write_manifest()
        document['boot_img'] = self.checkout.entry('build/7.0/boot.img')
        self.checkout.write('build/rear-camera/DELIVERY.json', json.dumps(document, indent=2) + '\n')
        result = self.checkout.run('status', '--no-server-check')
        self.assertEqual(result.returncode, 3)
        self.assertIn('is not the candidate path', result.stderr)

    def test_boot_image_hash_mismatch_refuses(self):
        document = self.checkout.write_manifest()
        document['boot_img']['sha256'] = '0' * 64
        self.checkout.write('build/rear-camera/DELIVERY.json', json.dumps(document, indent=2) + '\n')
        result = self.checkout.run('status', '--no-server-check')
        self.assertEqual(result.returncode, 3)
        self.assertIn('boot image', result.stderr)
        self.assertIn('pinned', result.stderr)

    def test_boot_image_size_mismatch_refuses(self):
        document = self.checkout.write_manifest()
        document['boot_img']['size'] = document['boot_img']['size'] + 1
        self.checkout.write('build/rear-camera/DELIVERY.json', json.dumps(document, indent=2) + '\n')
        result = self.checkout.run('status', '--no-server-check')
        self.assertEqual(result.returncode, 3)
        self.assertIn('size', result.stderr)

    def test_baseline_boot_hash_is_refused(self):
        document = self.checkout.write_manifest()
        document['boot_img']['sha256'] = BASELINE_BOOT_SHA256
        document['boot_img']['size'] = (self.checkout.root / 'build/rear-camera/boot.img').stat().st_size
        self.checkout.write('build/rear-camera/DELIVERY.json', json.dumps(document, indent=2) + '\n')
        result = self.checkout.run('status', '--no-server-check')
        self.assertEqual(result.returncode, 3)
        self.assertIn('manifest pins the known baseline', result.stderr)

    def test_baseline_worktree_name_is_refused(self):
        base = Path(self.temporary.name) / 'nura-sm8150-7-0'
        (base / 'scripts').mkdir(parents=True)
        shutil.copy2(SCRIPT, base / 'scripts/camera_boot.sh')
        result = subprocess.run([BASH, str(base / 'scripts/camera_boot.sh'), 'status'],
                                cwd='/', env=self.checkout.env(), capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 3)
        self.assertIn('baseline tree', result.stderr)

    def test_manifest_cci_hash_mismatch_refuses(self):
        document = self.checkout.write_manifest()
        document['i2c_qcom_cci']['sha256'] = '0' * 64
        self.checkout.write('build/rear-camera/DELIVERY.json',
                            json.dumps(document, indent=2) + '\n')
        result = self.checkout.run('status', '--no-server-check')
        self.assertEqual(result.returncode, 3)
        self.assertIn('CCI module', result.stderr)
        self.assertIn('pinned ' + '0' * 64, result.stderr)

    def test_manifest_dtb_must_match_kernel_package_dtb(self):
        other = self.checkout.write('build/rear-camera/other.dtb', DTB + b'tampered')
        document = self.checkout.write_manifest()
        document['dtb'] = {'path': 'build/rear-camera/other.dtb',
                           'sha256': digest(other.read_bytes()), 'size': other.stat().st_size}
        self.checkout.write('build/rear-camera/DELIVERY.json', json.dumps(document, indent=2) + '\n')
        result = self.checkout.run('status', '--no-server-check')
        self.assertEqual(result.returncode, 3)
        self.assertIn('kernel-package DTB', result.stderr)

    # -- tool discovery ----------------------------------------------------
    def test_missing_fastboot_refuses(self):
        (self.checkout.root / 'platform-tools/fastboot').unlink()
        result = self.checkout.run('boot', '--dry-run', '--no-server-check')
        self.assertEqual(result.returncode, 3)
        self.assertIn('fastboot not found', result.stderr)

    def test_missing_adb_refuses(self):
        (self.checkout.root / 'platform-tools/adb').unlink()
        result = self.checkout.run('boot', '--dry-run', '--no-server-check')
        self.assertEqual(result.returncode, 3)
        self.assertIn('adb not found', result.stderr)

    def test_tools_are_taken_from_platform_tools_before_path(self):
        self.checkout.write('fakebin/adb', "#!/bin/sh\necho path-adb >> \"$CALL_LOG\"\nexit 9\n", 0o755)
        self.checkout.write('fakebin/fastboot', "#!/bin/sh\necho path-fastboot >> \"$CALL_LOG\"\nexit 9\n", 0o755)
        result = self.checkout.run('boot', '--dry-run', '--no-server-check')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(str(self.checkout.root / 'platform-tools/fastboot'), result.stdout)
        self.assertNotIn('path-adb', self.checkout.calls())
        self.assertNotIn('path-fastboot', self.checkout.calls())

    # -- identity / device selection --------------------------------------
    def test_status_parses_android_identity_with_crlf(self):
        result = self.checkout.run('status', '--no-server-check',
                                   ADB_PROP_RO_PRODUCT_DEVICE='guacamole\r',
                                   ADB_PROP_RO_PRODUCT_MODEL='GM1911\r')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('product=guacamole (GM1911)', result.stdout)
        self.assertIn('slot=_b', result.stdout)
        self.assertIn('verdict=ok', result.stdout)

    def test_status_parses_fastboot_identity(self):
        result = self.checkout.run('status', '--no-server-check',
                                   ADB_DEVICES='', FASTBOOT_DEVICES='ABC123\tfastboot')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('fastboot ABC123 product=guacamole slot=b verdict=ok', result.stdout)

    def test_wrong_android_device_is_refused_without_yes(self):
        result = self.checkout.run('bootloader', '--dry-run', '--no-server-check',
                                   ADB_PROP_RO_PRODUCT_DEVICE='sunfish',
                                   ADB_PROP_RO_PRODUCT_MODEL='Pixel 3')
        self.assertEqual(result.returncode, 4)
        self.assertIn('does not identify guacamole', result.stderr)
        self.assertNotIn('adb -s', result.stdout)

    def test_yes_overrides_an_unexpected_device_with_a_warning(self):
        result = self.checkout.run('bootloader', '--dry-run', '--no-server-check', '--yes',
                                   ADB_PROP_RO_PRODUCT_DEVICE='sunfish',
                                   ADB_PROP_RO_PRODUCT_MODEL='Pixel 3')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('continuing because --yes was given', result.stderr)
        self.assertIn('adb -s ABC123 reboot bootloader', result.stdout)

    def test_unavailable_identity_requires_yes(self):
        result = self.checkout.run('boot', '--dry-run', '--no-server-check',
                                   ADB_PROP_RO_PRODUCT_DEVICE='',
                                   ADB_PROP_RO_PRODUCT_MODEL='')
        self.assertEqual(result.returncode, 4)
        self.assertIn('identity is unavailable', result.stderr)
        self.assertIn('--yes', result.stderr)

    def test_unavailable_identity_continues_with_yes(self):
        result = self.checkout.run('boot', '--dry-run', '--no-server-check', '--yes',
                                   ADB_PROP_RO_PRODUCT_DEVICE='',
                                   ADB_PROP_RO_PRODUCT_MODEL='')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('continuing because --yes was given', result.stderr)

    def test_unauthorized_device_stops_with_instructions(self):
        result = self.checkout.run('boot', '--dry-run', '--no-server-check',
                                   ADB_DEVICES='ABC123\tunauthorized')
        self.assertEqual(result.returncode, 4)
        self.assertIn('not bootable', result.stderr)
        self.assertIn('debugging prompt', result.stderr)

    def test_no_device_still_allows_a_dry_run(self):
        result = self.checkout.run('boot', '--dry-run', '--no-server-check',
                                   ADB_DEVICES='', FASTBOOT_DEVICES='')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('no adb/fastboot device attached', result.stderr)
        self.assertIn('<serial>', result.stdout)

    def test_multiple_devices_require_serial(self):
        result = self.checkout.run('boot', '--dry-run', '--no-server-check',
                                   ADB_DEVICES='ABC123\tdevice\nXYZ789\tdevice')
        self.assertEqual(result.returncode, 4)
        self.assertIn('several devices attached', result.stderr)
        self.assertIn('--serial', result.stderr)

    def test_serial_selects_one_of_several_devices(self):
        result = self.checkout.run('boot', '--dry-run', '--no-server-check', '--serial', 'XYZ789',
                                   ADB_DEVICES='ABC123\tdevice\nXYZ789\tdevice')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('adb -s XYZ789 reboot bootloader', result.stdout)

    def test_serial_must_be_attached(self):
        result = self.checkout.run('boot', '--dry-run', '--no-server-check', '--serial', 'NOPE')
        self.assertEqual(result.returncode, 4)
        self.assertIn('--serial NOPE is not attached', result.stderr)

    # -- dry-run command construction -------------------------------------
    def test_dry_run_from_android_reboots_then_boots_without_acting(self):
        result = self.checkout.run('boot', '--dry-run', '--no-server-check')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('adb -s ABC123 reboot bootloader', result.stdout)
        self.assertIn('fastboot -s ABC123 boot %s' % (self.checkout.root / 'build/rear-camera/boot.img'),
                      result.stdout)
        calls = self.checkout.calls()
        self.assertIn('adb devices', calls)
        self.assertNotIn('reboot bootloader', calls)
        self.assertNotIn(' boot ', calls)
        self.assertFalse((self.checkout.root / 'build/rear-camera/last-boot.json').exists())

    def test_dry_run_from_fastboot_skips_adb_entirely(self):
        result = self.checkout.run('boot', '--dry-run', '--no-server-check',
                                   ADB_DEVICES='', FASTBOOT_DEVICES='ABC123\tfastboot')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('fastboot -s ABC123 boot', result.stdout)
        self.assertNotIn('adb -s', result.stdout)
        self.assertNotIn('adb -s', self.checkout.calls())
        self.assertIn('adb devices', self.checkout.calls())

    def test_dry_run_bootloader_command(self):
        android = self.checkout.run('bootloader', '--dry-run', '--no-server-check')
        self.assertEqual(android.returncode, 0, android.stderr)
        self.assertIn('adb -s ABC123 reboot bootloader', android.stdout)
        self.checkout.clear_calls()
        fastboot = self.checkout.run('bootloader', '--dry-run', '--no-server-check',
                                     ADB_DEVICES='', FASTBOOT_DEVICES='ABC123\tfastboot')
        self.assertEqual(fastboot.returncode, 0, fastboot.stderr)
        self.assertIn('fastboot -s ABC123 reboot bootloader', fastboot.stdout)
        self.assertNotIn('reboot', self.checkout.calls())

    def test_dry_run_android_command(self):
        result = self.checkout.run('android', '--dry-run', '--no-server-check')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('adb -s ABC123 reboot', result.stdout)
        self.assertNotIn('reboot', self.checkout.calls())

    # -- server / served image --------------------------------------------
    def test_boot_tolerates_no_listener_before_the_usb_gadget_exists(self):
        # camera_serve.sh waits for the USB interface and starts nbd-server
        # only while the phone boots, so port 10001 is normally still closed
        # when camera_boot.sh runs.  That must not deadlock the documented order.
        result = self.checkout.run('boot', '--dry-run', SS_LISTEN='')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('not listening yet', result.stderr)
        self.assertIn('camera_serve.sh', result.stderr)
        self.assertIn('matches the pinned rootfs image', result.stdout)
        self.assertIn('fastboot -s ABC123 boot', result.stdout)

    def test_status_still_requires_the_listener(self):
        result = self.checkout.run('status', SS_LISTEN='')
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn('nothing is listening on port 10001', result.stderr)
        self.assertIn('camera_serve.sh', result.stderr)

    def test_server_check_rejects_a_stale_served_image(self):
        stale = self.checkout.write('.work/rear-camera/pmbootstrap-work/images_netboot/oneplus-guacamole.img',
                                    b'ROOTFS-FIXTURE' + b'\x05' * 9000)
        self.assertNotEqual(digest(stale.read_bytes()), digest(ROOTFS))
        result = self.checkout.run('boot', '--dry-run')
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn('served image', result.stderr)
        self.assertIn('camera_serve.sh', result.stderr)
        self.assertNotIn('adb -s', result.stdout)

    def test_server_check_rejects_an_image_without_the_fixed_cci_module(self):
        self.checkout.write('.work/rear-camera/pmbootstrap-work/images_netboot/oneplus-guacamole.img',
                            b'ROOTFS-FIXTURE' + b'\x05' * 9000)
        document = self.checkout.write_manifest()
        document['rootfs_image'] = self.checkout.entry(
            '.work/rear-camera/pmbootstrap-work/images_netboot/oneplus-guacamole.img')
        self.checkout.write('build/rear-camera/DELIVERY.json', json.dumps(document, indent=2) + '\n')
        result = self.checkout.run('boot', '--dry-run')
        self.assertEqual(result.returncode, 3)
        self.assertIn('does not contain the manifest CCI module', result.stderr)

    def test_server_check_passes_for_the_pinned_image(self):
        result = self.checkout.run('boot', '--dry-run')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('port 10001 is listening', result.stdout)
        self.assertIn('manifest CCI module bytes present', result.stdout)

    def test_no_server_check_skips_only_the_listener(self):
        result = self.checkout.run('boot', '--dry-run', '--no-server-check', SS_LISTEN='')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('--no-server-check: skipped the port-10001 listener check', result.stderr)
        self.assertIn('matches the pinned rootfs image', result.stdout)

    def test_no_server_check_still_refuses_a_stale_served_image(self):
        self.checkout.write('.work/rear-camera/pmbootstrap-work/images_netboot/oneplus-guacamole.img',
                            b'ROOTFS-FIXTURE' + b'\x05' * 9000)
        result = self.checkout.run('boot', '--dry-run', '--no-server-check')
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn('served image', result.stderr)
        self.assertIn('camera_serve.sh', result.stderr)

    # -- accept-current ----------------------------------------------------
    def test_accept_current_boots_without_a_manifest(self):
        (self.checkout.root / 'build/rear-camera/DELIVERY.json').unlink()
        result = self.checkout.run('boot', '--dry-run', '--no-server-check', '--accept-current')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('--accept-current', result.stderr)
        self.assertIn('no pinned manifest', result.stdout)
        self.assertIn('fastboot -s ABC123 boot', result.stdout)

    def test_accept_current_still_refuses_a_missing_boot_image(self):
        (self.checkout.root / 'build/rear-camera/DELIVERY.json').unlink()
        (self.checkout.root / 'build/rear-camera/boot.img').unlink()
        result = self.checkout.run('boot', '--dry-run', '--no-server-check', '--accept-current')
        self.assertEqual(result.returncode, 3)
        self.assertIn('boot image missing', result.stderr)

    # -- embedded artifact identity ---------------------------------------
    def test_boot_image_without_the_dtb_is_refused(self):
        self.checkout.write('build/rear-camera/boot.img', pad_page(b'\x00' * 4096) + INITRAMFS)
        self.checkout.write_manifest()
        result = self.checkout.run('status', '--no-server-check')
        self.assertEqual(result.returncode, 3)
        self.assertIn('not embedded', result.stderr)

    def test_boot_image_without_the_initramfs_is_refused(self):
        self.checkout.write('build/rear-camera/boot.img', pad_page(b'\x00' * 4096 + DTB))
        self.checkout.write_manifest()
        result = self.checkout.run('status', '--no-server-check')
        self.assertEqual(result.returncode, 3)
        self.assertIn('initramfs is not embedded', result.stderr)

    def test_vmlinuz_that_does_not_match_the_image_is_refused(self):
        self.checkout.write('build/rear-camera/kernel-package/boot/vmlinuz', gzip.compress(b'other image'))
        self.checkout.write_manifest()
        result = self.checkout.run('status', '--no-server-check',
                                   CAMERA_BOOT_CCI_SHA256=digest(MODULE))
        self.assertEqual(result.returncode, 3)
        self.assertIn('decompresses to', result.stderr)

    # -- real (fake-device) boot -------------------------------------------
    def test_successful_boot_writes_last_boot_and_next_steps(self):
        result = self.checkout.run('boot', '--no-server-check',
                                   ADB_DEVICES='', FASTBOOT_DEVICES='ABC123\tfastboot')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('handed to the phone', result.stdout)
        self.assertIn('camera_verify.sh', result.stdout)
        self.assertIn('photograph', result.stdout)
        record = self.checkout.last_boot()
        self.assertEqual(record['result'], 'success')
        self.assertEqual(record['attempts'], 1)
        self.assertEqual(record['image_sha256'], digest(self.checkout.root.joinpath(
            'build/rear-camera/boot.img').read_bytes()))
        self.assertEqual(record['device']['serial'], 'ABC123')
        self.assertEqual(record['device']['product'], 'guacamole')
        calls = self.checkout.calls()
        self.assertEqual(calls.count('fastboot -s ABC123 boot '), 1)
        self.assertNotIn('flash', calls)
        self.assertNotIn('erase', calls)
        logs = list((self.checkout.root / 'build/rear-camera/logs').glob('boot-*.log'))
        self.assertEqual(len(logs), 1, logs)

    def install_cci(self, data):
        """Replace the module in the kernel package and both rootfs copies."""
        module = self.checkout.write(
            'build/rear-camera/kernel-package/usr/lib/modules/%s/kernel/drivers/i2c/busses/'
            'i2c-qcom-cci.ko.zst' % RELEASE, data)
        rootfs = b'ROOTFS-FIXTURE' + b'\x05' * 9000 + data + b'\x06' * 500
        for relative in (
                '.work/rear-camera/pmbootstrap-work/chroot_native/home/pmos/rootfs/oneplus-guacamole.img',
                '.work/rear-camera/pmbootstrap-work/images_netboot/oneplus-guacamole.img'):
            self.checkout.write(relative, rootfs)
        return module, self.checkout.write_manifest()

    def test_a_future_build_cci_hash_is_taken_from_the_manifest(self):
        future = b'future-build-cci-module' * 500
        module, document = self.install_cci(future)
        self.assertEqual(document['i2c_qcom_cci']['sha256'], digest(future))
        result = self.checkout.run('boot', '--dry-run', CAMERA_BOOT_CCI_SHA256='')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(digest(future), result.stdout)
        self.assertNotIn(CCI_SHA256, result.stdout)

    def test_status_does_not_rehash_a_post_boot_mutable_image(self):
        document = self.checkout.write_manifest()
        served = self.checkout.root / \
            '.work/rear-camera/pmbootstrap-work/images_netboot/oneplus-guacamole.img'
        source_sha = document['rootfs_image']['sha256']
        generation = '%d:%d:%d' % (served.stat().st_dev, served.stat().st_ino,
                                   served.stat().st_size)
        self.checkout.write('.work/delivery/served-image.json', json.dumps({
            'path': str(served),
            'served_sha256': source_sha,
            'source_sha256': source_sha,
            'generation': generation,
            'release': RELEASE,
            'nbd_port': 10001,
        }, indent=2) + '\n')
        # The phone mounts this image read-write through NBD; those writes are
        # legitimate and must not make post-boot status fail. Overwrite in
        # place so the file identity (dev:inode:size) is unchanged, as NBD
        # block writes leave it.
        with served.open('r+b') as stream:
            stream.write(b'PHONE WROTE THIS AFTER BOOT')
        result = self.checkout.run('status', '--no-server-check')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('verified before exposure', result.stdout)
        self.assertIn('not rehashed', result.stdout)

    def test_status_rejects_a_cache_replaced_after_exposure(self):
        document = self.checkout.write_manifest()
        served = self.checkout.root / \
            '.work/rear-camera/pmbootstrap-work/images_netboot/oneplus-guacamole.img'
        source_sha = document['rootfs_image']['sha256']
        generation = '%d:%d:%d' % (served.stat().st_dev, served.stat().st_ino,
                                   served.stat().st_size)
        self.checkout.write('.work/delivery/served-image.json', json.dumps({
            'path': str(served),
            'served_sha256': source_sha,
            'source_sha256': source_sha,
            'generation': generation,
            'release': RELEASE,
            'nbd_port': 10001,
        }, indent=2) + '\n')
        replacement = served.with_name(served.name + '.new')
        replacement.write_bytes(served.read_bytes())
        os.replace(replacement, served)
        result = self.checkout.run('status', '--no-server-check')
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn('changed after exposure', result.stderr)

    def test_load_error_stops_after_one_attempt_and_logs_recovery(self):
        result = self.checkout.run('boot', '--no-server-check',
                                   ADB_DEVICES='', FASTBOOT_DEVICES='ABC123\tfastboot',
                                   FASTBOOT_BOOT_OUTPUT="Sending 'boot.img' (100 KB) FAILED\nLoad Error\n",
                                   FASTBOOT_BOOT_STATUS='1')
        self.assertEqual(result.returncode, 5, result.stdout + result.stderr)
        self.assertIn('Load Error', result.stdout)
        self.assertIn('NOT booted and nothing was flashed', result.stdout)
        self.assertIn('do not power-cycle', result.stdout)
        record = self.checkout.last_boot()
        self.assertEqual(record['result'], 'failed')
        self.assertEqual(record['attempts'], 1)
        self.assertIn('Load Error', record['detail'])
        self.assertEqual(self.checkout.calls().count('fastboot -s ABC123 boot '), 1)

    def test_adb_reboot_failure_stops_before_booting(self):
        result = self.checkout.run('boot', '--no-server-check', ADB_REBOOT_STATUS='42')
        self.assertEqual(result.returncode, 5, result.stdout + result.stderr)
        self.assertIn('adb reboot bootloader failed', result.stderr)
        self.assertNotIn('fastboot -s ABC123 boot', self.checkout.calls())
        self.assertEqual(self.checkout.last_boot()['result'], 'failed')

    def test_missing_fastboot_after_adb_reboot_stops(self):
        result = self.checkout.run('boot', '--no-server-check', '--timeout', '1',
                                   FASTBOOT_DEVICES='')
        self.assertEqual(result.returncode, 5, result.stdout + result.stderr)
        self.assertIn('no fastboot device', result.stderr)
        self.assertIn('adb -s ABC123 reboot bootloader', self.checkout.calls())
        self.assertNotIn('fastboot -s ABC123 boot', self.checkout.calls())

    # -- path resolution ---------------------------------------------------
    def test_root_comes_from_bash_source_not_cwd(self):
        result = self.checkout.run('status', '--no-server-check',
                                   script='scripts/camera_boot.sh', cwd=str(self.checkout.root))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(os.path.realpath(str(self.checkout.root)), result.stdout)

    def test_script_copy_outside_a_candidate_checkout_is_refused(self):
        base = Path(self.temporary.name) / 'not-candidate'
        (base / 'scripts').mkdir(parents=True)
        shutil.copy2(SCRIPT, base / 'scripts/camera_boot.sh')
        result = subprocess.run([BASH, str(base / 'scripts/camera_boot.sh'), 'status'],
                                cwd='/', env=self.checkout.env(), capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 3)
        self.assertIn('not a rear-camera candidate checkout', result.stderr)


if __name__ == '__main__':
    unittest.main()
