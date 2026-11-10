"""Checks for scripts/camera_serve.sh and scripts/camera_usb_net.sh.

Every test runs against a temporary fake checkout with tiny fake artifacts, a
synthetic GPT/ext4 rootfs image and a fixture sysfs tree. Nothing here starts a
server, runs sudo, touches 172.16.42.2, pmbootstrap, a phone or hardware.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
RELEASE = '7.0.0-sm8150-rear1'
FIXED_CCI_SHA = '4cd35f05ff873700c43baad265efd0bf73166352baac79f88d4aeca456fb27e4'
FIXED_CCI_SIZE = 8950
REAL_MODULE = (ROOT / 'build/rear-camera/kernel-package/usr/lib/modules' / RELEASE
               / 'kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst')
SECTOR = 4096
BOOT_LBA = 2048
BOOT_BYTES = 8 * 1024 * 1024
ROOT_LBA = 6144
ROOT_BYTES = 16 * 1024 * 1024
IMAGE_BYTES = ROOT_LBA * SECTOR + ROOT_BYTES
MODULE_RELATIVE = 'usr/lib/modules/%s/kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst' % RELEASE
HOOK_RELATIVE = 'usr/share/mkinitfs/hooks/netboot.sh'
# The pre-fix module the stale served cache carried: 8920 bytes, other hash.
PREFIX_MODULE = bytes(range(256)) * 34 + bytes(216)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fixed_module_bytes():
    if REAL_MODULE.is_file() and REAL_MODULE.stat().st_size == FIXED_CCI_SIZE:
        return REAL_MODULE.read_bytes()
    return b'x' * FIXED_CCI_SIZE


def make_ext4(directory, image, size_bytes):
    image.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['mke2fs', '-q', '-t', 'ext4', '-d', str(directory), str(image),
                    '%dM' % (size_bytes // (1024 * 1024))],
                   check=True, capture_output=True)


def write_gpt(image, partitions):
    """Write a (CRC-less) GPT with 4 KiB sectors; only the parser here reads it."""
    total = image.stat().st_size // SECTOR
    entries = bytearray(128 * 128)
    for index, (first, last) in enumerate(partitions):
        base = index * 128
        entries[base:base + 16] = bytes([0x28 if index == 0 else 0x45] + [0] * 15)
        struct.pack_into('<Q', entries, base + 32, first)
        struct.pack_into('<Q', entries, base + 40, last)
    header = bytearray(92)
    header[0:8] = b'EFI PART'
    struct.pack_into('<I', header, 12, 92)
    struct.pack_into('<Q', header, 24, 1)
    struct.pack_into('<Q', header, 32, total - 1)
    struct.pack_into('<Q', header, 40, 34)
    struct.pack_into('<Q', header, 48, total - 34)
    header[56:72] = b'\x11' * 16
    struct.pack_into('<Q', header, 72, 2)
    struct.pack_into('<I', header, 80, 128)
    struct.pack_into('<I', header, 84, 128)
    with image.open('r+b') as stream:
        stream.seek(SECTOR * 2)
        stream.write(entries)
        stream.seek(SECTOR)
        stream.write(header)


def make_image(destination, *, boot_payloads, module_bytes, port=10001, release=RELEASE):
    """Build <destination>: p1 = /vmlinuz,/initramfs,/dtb, p2 = the root tree."""
    destination = Path(destination)
    staging = destination.parent / (destination.name + '.d')
    if staging.exists():
        shutil.rmtree(staging)
    boot_tree = staging / 'boot'
    root_tree = staging / 'root'
    boot_tree.mkdir(parents=True)
    for name, payload in boot_payloads.items():
        (boot_tree / name).write_bytes(payload)
    module = root_tree / ('usr/lib/modules/%s/kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst'
                          % release)
    module.parent.mkdir(parents=True)
    module.write_bytes(module_bytes)
    hook = root_tree / HOOK_RELATIVE
    hook.parent.mkdir(parents=True)
    hook.write_text('#!/bin/sh\nNBD_PORT=%d\n' % port)
    boot_part = staging / 'p1.ext4'
    root_part = staging / 'p2.ext4'
    make_ext4(boot_tree, boot_part, BOOT_BYTES)
    make_ext4(root_tree, root_part, ROOT_BYTES)
    with destination.open('wb') as stream:
        stream.truncate(IMAGE_BYTES)
    with destination.open('r+b') as stream:
        stream.seek(BOOT_LBA * SECTOR)
        stream.write(boot_part.read_bytes())
        stream.seek(ROOT_LBA * SECTOR)
        stream.write(root_part.read_bytes())
    write_gpt(destination, [(BOOT_LBA, BOOT_LBA + BOOT_BYTES // SECTOR - 1),
                           (ROOT_LBA, ROOT_LBA + ROOT_BYTES // SECTOR - 1)])
    shutil.rmtree(staging)
    return destination


def write_sysfs(sysfs, interfaces):
    """interfaces: {name: {'driver': str, 'carrier': 0/1, 'ifindex': int}}."""
    for name, spec in interfaces.items():
        base = sysfs / 'class/net' / name
        (base / 'device').mkdir(parents=True)
        (base / 'ifindex').write_text('%d\n' % spec.get('ifindex', 1))
        carrier = spec.get('carrier', 0)
        (base / 'carrier').write_text('%d\n' % carrier)
        (base / 'operstate').write_text('up\n' if carrier else 'down\n')
        driver = spec.get('driver')
        if driver:
            driver_dir = sysfs / 'bus/usb/drivers' / driver
            driver_dir.mkdir(parents=True, exist_ok=True)
            (base / 'device/driver').symlink_to(driver_dir)
            (base / 'device/uevent').write_text('DRIVER=%s\nDEVTYPE=usb_interface\n' % driver)


def build_checkout(root, *, module_bytes=None, cache='copy', release=RELEASE,
                   port=10001, manifest=True, tools=None, interfaces=None):
    """Create (or recreate) a fake candidate checkout at <root>."""
    root = Path(root)
    if root.exists():
        shutil.rmtree(root)
    module_bytes = fixed_module_bytes() if module_bytes is None else module_bytes
    scripts = root / 'scripts'
    scripts.mkdir(parents=True)
    for name in ('camera_serve.sh', 'camera_usb_net.sh'):
        shutil.copy2(SCRIPTS / name, scripts / name)

    build = root / 'build/rear-camera'
    build.mkdir(parents=True)
    payloads = {
        'boot.img': b'fake boot image\n',
        'Image': b'fake kernel Image\n',
        'kernel-package/boot/vmlinuz': b'fake vmlinuz\n',
        'kernel-package/boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb': b'fake dtb\n',
        'initramfs': b'fake initramfs\n',
    }
    for relative, payload in payloads.items():
        path = build / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    staged_module = build / 'kernel-package' / MODULE_RELATIVE
    staged_module.parent.mkdir(parents=True)
    staged_module.write_bytes(module_bytes)

    work = root / '.work/rear-camera'
    (work / 'pmbootstrap').mkdir(parents=True)
    # Any accidental `pmbootstrap ... netboot serve` run is recorded and fails,
    # so --check/--dry-run tests can prove nothing was started.
    (work / 'pmbootstrap/pmbootstrap.py').write_text(
        'import os, sys\n'
        'with open(os.environ["CALL_LOG"], "a") as log:\n'
        '    log.write("pmbootstrap %s\\n" % " ".join(sys.argv[1:]))\n'
        'sys.exit(3)\n')
    (work / 'rootfs').mkdir()
    (work / 'rootfs/pmbootstrap.cfg').write_text(
        '[pmbootstrap]\ndevice = oneplus-guacamole\nwork = %s\n' % (work / 'pmbootstrap-work'))
    (work / 'pmaports').mkdir()
    chroot_rootfs = work / 'pmbootstrap-work/chroot_native/home/pmos/rootfs'
    chroot_rootfs.mkdir(parents=True)
    netboot = work / 'pmbootstrap-work/images_netboot'
    netboot.mkdir(parents=True)
    source_image = chroot_rootfs / 'oneplus-guacamole.img'
    served_image = netboot / 'oneplus-guacamole.img'
    boot_payloads = {
        'vmlinuz': payloads['kernel-package/boot/vmlinuz'],
        'initramfs': payloads['initramfs'],
        'sm8150-oneplus-guacamole.dtb': payloads['kernel-package/boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb'],
    }
    make_image(source_image, boot_payloads=boot_payloads, module_bytes=module_bytes,
               port=port, release=release)

    if manifest:
        entries = {}
        for key, relative in (
                ('boot_img', 'boot.img'),
                ('image', 'Image'),
                ('kernel_package', 'kernel-package/boot/vmlinuz'),
                ('dtb', 'kernel-package/boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb'),
                ('initramfs', 'initramfs')):
            path = build / relative
            entries[key] = {'path': str(path), 'sha256': sha256(path), 'size': path.stat().st_size}
        entries['rootfs_image'] = {'path': str(source_image), 'sha256': sha256(source_image),
                                   'size': source_image.stat().st_size}
        entries['i2c_qcom_cci'] = {'path': str(staged_module),
                                   'sha256': sha256(staged_module),
                                   'size': staged_module.stat().st_size}
        document = {'release': release, 'worktree': str(root), 'nbd_port': port,
                    'created_at': '2026-11-10T12:27:00Z', 'git_head': '0' * 40, **entries}
        (build / 'DELIVERY.json').write_text(json.dumps(document, indent=2) + '\n')

    if cache == 'copy':
        shutil.copyfile(source_image, served_image)
    elif cache == 'stale':
        make_image(served_image, boot_payloads=boot_payloads, module_bytes=PREFIX_MODULE,
                   port=port, release=release)

    sysfs = root / 'fake-sys'
    write_sysfs(sysfs, interfaces if interfaces is not None else {
        'usb0': {'driver': 'cdc_ncm', 'carrier': 1, 'ifindex': 12},
        'enp0s20f0u1': {'driver': 'rndis_host', 'carrier': 0, 'ifindex': 5},
        'lo': {'driver': None, 'carrier': 1, 'ifindex': 1},
        'docker0': {'driver': None, 'carrier': 1, 'ifindex': 100},
    })

    binaries = root / 'fake-bin'
    binaries.mkdir()
    bodies = {'ip': 'exit 0', 'ss': 'exit 0', 'nmcli': 'exit 1',
              'sudo': 'exit 1', 'ufw': 'exit 1'}
    bodies.update(tools or {})
    for name, body in bodies.items():
        path = binaries / name
        path.write_text('#!/usr/bin/env bash\nprintf "%s %s\\n" "%s" "$*" >> "$CALL_LOG"\n%s\n'
                        % (name, '', name, body))
        path.chmod(0o755)
    return {'root': root, 'build': build, 'work': work, 'source_image': source_image,
            'served_image': served_image, 'sysfs': sysfs, 'bin': binaries}


class CheckoutTest(unittest.TestCase):
    """Base: one fake checkout with a matching cache, fake tools, fixture sysfs."""

    cache = 'copy'

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / 'checkout with spaces'
        self.tmpdir = self.base / 'tmp'
        self.tmpdir.mkdir()
        self.log = self.base / 'calls.log'
        self.log.touch()
        self.fixture = build_checkout(self.root, cache=self.cache)

    def environment(self, **extra):
        values = {
            'PATH': '%s:%s' % (self.fixture['bin'], os.environ['PATH']),
            'TMPDIR': str(self.tmpdir),
            'CALL_LOG': str(self.log),
            'NURA_SYSFS_ROOT': str(self.fixture['sysfs']),
            'NURA_STATE_DIR': str(self.root / '.work/delivery'),
        }
        values.pop('NURA_USB_IFACE', None)
        values.update(extra)
        return {**os.environ, **values}

    def run_script(self, name, *arguments, status=0, environment=None, timeout=120, cwd='/'):
        result = subprocess.run(['bash', str(self.root / 'scripts' / name), *arguments],
                                env=self.environment(**(environment or {})), cwd=cwd,
                                capture_output=True, text=True, timeout=timeout)
        self.assertEqual(result.returncode, status,
                         'stdout:\n%s\nstderr:\n%s' % (result.stdout, result.stderr))
        return result

    def calls(self):
        return self.log.read_text().splitlines()

    def manifest(self):
        return json.loads((self.fixture['build'] / 'DELIVERY.json').read_text())

    def rebuild_source_image(self, *, module_bytes=None, port=10001, release=RELEASE):
        """Replace the chroot rootfs image and keep the manifest/cache consistent."""
        fixture = self.fixture
        kernel = fixture['build'] / 'kernel-package/boot'
        boot_payloads = {
            'vmlinuz': (kernel / 'vmlinuz').read_bytes(),
            'initramfs': (fixture['build'] / 'initramfs').read_bytes(),
            'sm8150-oneplus-guacamole.dtb': (kernel / 'dtbs/qcom/sm8150-oneplus-guacamole.dtb').read_bytes(),
        }
        make_image(fixture['source_image'], boot_payloads=boot_payloads,
                   module_bytes=fixed_module_bytes() if module_bytes is None else module_bytes,
                   port=port, release=release)
        document = self.manifest()
        document['rootfs_image']['sha256'] = sha256(fixture['source_image'])
        document['rootfs_image']['size'] = fixture['source_image'].stat().st_size
        (fixture['build'] / 'DELIVERY.json').write_text(json.dumps(document))
        shutil.copyfile(fixture['source_image'], fixture['served_image'])


class ScriptHygieneTests(CheckoutTest):
    def test_shell_syntax_is_valid(self):
        for name in ('camera_serve.sh', 'camera_usb_net.sh'):
            result = subprocess.run(['bash', '-n', str(SCRIPTS / name)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_shellcheck_when_installed(self):
        shellcheck = shutil.which('shellcheck')
        if shellcheck is None:
            self.skipTest('shellcheck is not installed')
        for name in ('camera_serve.sh', 'camera_usb_net.sh'):
            result = subprocess.run([shellcheck, '-x', str(SCRIPTS / name)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_help_does_not_touch_anything(self):
        for name in ('camera_serve.sh', 'camera_usb_net.sh'):
            result = self.run_script(name, '--help')
            self.assertIn('Usage:', result.stdout)
        self.assertEqual(self.calls(), [])


class ManifestTests(CheckoutTest):
    def test_missing_manifest_fails_closed(self):
        (self.fixture['build'] / 'DELIVERY.json').unlink()
        result = self.run_script('camera_serve.sh', '--check', status=1)
        self.assertIn('camera_build.sh', result.stderr)
        self.assertIn('DELIVERY.json', result.stderr)

    def test_wrong_release_fails_closed(self):
        document = self.manifest()
        document['release'] = '6.17.0-sm8150'
        (self.fixture['build'] / 'DELIVERY.json').write_text(json.dumps(document))
        result = self.run_script('camera_serve.sh', '--check', status=1)
        self.assertIn('release', result.stderr)

    def test_tampered_boot_image_fails_closed(self):
        # Same length, different bytes: only the sha256 check can notice.
        (self.fixture['build'] / 'boot.img').write_bytes(b'FAKE boot image\n')
        result = self.run_script('camera_serve.sh', '--check', status=1)
        self.assertIn('boot_img', result.stderr)
        self.assertIn('sha256', result.stderr)

    def test_wrong_boot_image_size_fails_closed(self):
        (self.fixture['build'] / 'boot.img').write_bytes(b'tampered boot image\n')
        result = self.run_script('camera_serve.sh', '--check', status=1)
        self.assertIn('manifest says', result.stderr)

    def test_manifest_from_another_worktree_fails_closed(self):
        document = self.manifest()
        document['worktree'] = str(self.base / 'someone-elses-checkout')
        (self.fixture['build'] / 'DELIVERY.json').write_text(json.dumps(document))
        result = self.run_script('camera_serve.sh', '--check', status=1)
        self.assertIn('is not this checkout', result.stderr)

    def test_manifest_rootfs_is_not_the_chroot_image(self):
        copy = self.root / 'build/rear-camera/other.img'
        shutil.copyfile(self.fixture['source_image'], copy)
        document = self.manifest()
        document['rootfs_image']['path'] = str(copy)
        (self.fixture['build'] / 'DELIVERY.json').write_text(json.dumps(document))
        result = self.run_script('camera_serve.sh', '--check', status=1)
        self.assertIn('is not the current chroot image', result.stderr)

    def test_correct_manifest_serves_checks(self):
        result = self.run_script('camera_serve.sh', '--check')
        self.assertIn('PASS  release %s' % RELEASE, result.stdout)
        self.assertIn("is this build's module", result.stdout)
        self.assertIn('CHECK OK', result.stdout)


class ImageVerificationTests(CheckoutTest):
    def test_fixed_module_is_verified_in_the_chroot_image(self):
        result = self.run_script('camera_serve.sh', '--check')
        self.assertIn('PASS  rootfs image: i2c-qcom-cci %s (%d bytes)'
                      % (FIXED_CCI_SHA, FIXED_CCI_SIZE), result.stdout)
        self.assertIn('PASS  rootfs image: exactly one module tree (%s)' % RELEASE, result.stdout)
        self.assertIn('PASS  rootfs image: netboot hook sets NBD_PORT=10001', result.stdout)

    def test_pre_fix_module_in_the_chroot_image_is_rejected(self):
        # The manifest still declares the fixed module in the kernel package;
        # only the image content is stale, so the image check has to catch it.
        self.rebuild_source_image(module_bytes=PREFIX_MODULE)
        result = self.run_script('camera_serve.sh', '--check', status=1)
        self.assertIn('STALE MODULE', result.stderr)
        self.assertIn('8920 bytes', result.stderr)

    def test_wrong_module_tree_release_is_rejected(self):
        self.rebuild_source_image(release='6.17.0-sm8150')
        result = self.run_script('camera_serve.sh', '--check', status=1)
        self.assertIn('expected exactly', result.stderr)

    def test_wrong_netboot_hook_port_is_rejected(self):
        self.rebuild_source_image(port=9999)
        result = self.run_script('camera_serve.sh', '--check', status=1)
        self.assertIn('NBD_PORT=10001', result.stderr)


class CacheTests(CheckoutTest):
    cache = 'stale'

    def test_stale_cache_is_detected_with_the_pre_fix_module(self):
        result = self.run_script('camera_serve.sh', '--check')
        self.assertIn('served cache', result.stderr)
        self.assertIn('pre-refresh', result.stderr)
        self.assertIn('is NOT this build', result.stderr)
        self.assertIn('REFRESH NEEDED', result.stderr)

    def test_no_replace_refuses_a_stale_cache(self):
        result = self.run_script('camera_serve.sh', '--check', '--no-replace', status=1)
        self.assertIn('--no-replace', result.stderr)

    def test_missing_cache_is_detected(self):
        self.fixture['served_image'].unlink()
        result = self.run_script('camera_serve.sh', '--check')
        self.assertIn('served cache is missing', result.stderr)
        self.assertIn('REFRESH NEEDED', result.stderr)

    def test_dry_run_plans_the_replace_and_executes_nothing(self):
        result = self.run_script('camera_serve.sh', '--dry-run')
        self.assertIn('DRY-RUN: python3', result.stdout)
        self.assertIn('DRY-RUN: sudo cp --reflink=auto', result.stdout)
        self.assertIn('netboot serve --port 10001', result.stdout)
        self.assertNotIn('netboot serve --port 10001 --replace', result.stdout)
        self.assertIn('camera_usb_net.sh wait', result.stdout)
        self.assertIn('DRY-RUN: sudo ip addr replace 172.16.42.2/24 dev usb0', result.stdout)
        executed = [line for line in self.calls()
                    if 'replace' in line or 'addr del' in line or 'link set' in line]
        self.assertEqual(executed, [], 'dry-run ran a mutating command: %s' % executed)
        self.assertFalse([line for line in self.calls() if line.startswith('sudo ')])

    def test_ready_block_is_not_printed_without_a_real_refresh(self):
        result = self.run_script('camera_serve.sh', '--dry-run')
        self.assertIn('READY', result.stdout)
        self.assertNotIn('PASS  served cache is byte-identical', result.stdout)


class NoServerTests(CheckoutTest):
    def assert_no_server(self):
        offenders = [line for line in self.calls() if line.startswith('pmbootstrap ')]
        self.assertEqual(offenders, [], 'check/dry-run started pmbootstrap: %s' % offenders)

    def test_check_never_starts_the_server(self):
        self.run_script('camera_serve.sh', '--check')
        self.assert_no_server()

    def test_dry_run_never_starts_the_server(self):
        self.run_script('camera_serve.sh', '--dry-run')
        self.assert_no_server()

    def test_dry_run_with_replace_never_starts_the_server(self):
        self.run_script('camera_serve.sh', '--dry-run', '--replace')
        self.assert_no_server()


class MatchingCacheTests(CheckoutTest):
    def test_matching_cache_does_not_pass_replace(self):
        result = self.run_script('camera_serve.sh', '--dry-run')
        self.assertIn('netboot serve --port 10001', result.stdout)
        self.assertNotIn('--replace', result.stdout)
        self.assertIn('PASS  served cache already equals the rootfs image', result.stdout)

    def test_replace_flag_forces_a_copy(self):
        result = self.run_script('camera_serve.sh', '--dry-run', '--replace')
        self.assertIn('--replace requested: the served cache is copied again', result.stdout)
        self.assertIn('DRY-RUN: sudo cp --reflink=auto', result.stdout)
        self.assertIn('netboot serve --port 10001', result.stdout)
        self.assertNotIn('netboot serve --port 10001 --replace', result.stdout)


class LifecycleRegressionTests(CheckoutTest):
    """The three delivery blockers: live cache, future builds, post-boot image."""

    cache = 'stale'

    def test_refresh_is_refused_while_the_candidate_port_is_live(self):
        # A live nbd-server reads the cache as its backing file: replacing it
        # truncates the image under the phone.
        body = ('if [[ "$*" == *10001* ]]; then\n'
                '  echo "LISTEN 0 5 172.16.42.2:10001 0.0.0.0:* '
                'users:((\\\"nbd-server\\\",pid=1,fd=3))"\n'
                'fi\nexit 0')
        build_checkout(self.root, cache='stale', tools={
            'ss': body,
            'cp': 'printf "cp %s\\n" "$*" >> "$CALL_LOG"; exit 99',
            'mv': 'printf "mv %s\\n" "$*" >> "$CALL_LOG"; exit 99',
        })
        before = sha256(self.fixture['served_image'])
        result = self.run_script('camera_serve.sh', '--yes', status=1)
        self.assertIn('refusing to replace', result.stderr)
        self.assertIn('in use', result.stderr)
        self.assertEqual(sha256(self.fixture['served_image']), before,
                         'a live cache must not be copied over')
        touched = [line for line in self.calls() if line.startswith(('cp ', 'mv '))]
        self.assertEqual(touched, [], 'refresh commands ran while the candidate was live')

    def test_future_build_cci_identity_comes_from_the_manifest(self):
        future = b'future-build-cci-module' * 500
        staged = self.fixture['build'] / ('kernel-package/usr/lib/modules/%s/kernel/drivers/'
                                          'i2c/busses/i2c-qcom-cci.ko.zst' % RELEASE)
        staged.write_bytes(future)
        self.rebuild_source_image(module_bytes=future)
        document = self.manifest()
        document['i2c_qcom_cci'] = {'path': str(staged), 'sha256': sha256(staged),
                                    'size': staged.stat().st_size}
        (self.fixture['build'] / 'DELIVERY.json').write_text(json.dumps(document))
        result = self.run_script('camera_serve.sh', '--check')
        self.assertIn('is this build', result.stdout)
        self.assertIn(sha256(staged), result.stdout)
        self.assertNotIn(FIXED_CCI_SHA, result.stdout,
                         'the script must not require one historical binary hash')

    def test_check_is_independent_of_the_working_directory(self):
        result = self.run_script('camera_serve.sh', '--check', cwd=str(self.tmpdir))
        self.assertIn('CHECK OK', result.stdout)
        self.assertIn('rootfs image matches the manifest', result.stdout)

    def test_real_ip_up_configures_the_usb_host_address(self):
        ip = ('if [[ "$*" == *"route get"* ]]; then\n'
              '  echo "172.16.42.1 dev usb0 src 172.16.42.2"\n'
              'fi\nexit 0')
        build_checkout(self.root, cache='copy', tools={'ip': ip, 'sudo': 'exec "$@"'})
        result = self.run_script('camera_usb_net.sh', 'up', '--iface', 'usb0')
        self.assertIn('+ sudo ip link set dev usb0 up', result.stderr)
        self.assertIn('+ sudo ip addr replace 172.16.42.2/24 dev usb0', result.stderr)
        self.assertIn('configured 172.16.42.2/24 on usb0', result.stderr)
        self.assertNotIn('dev lo', result.stderr)


class PortTests(CheckoutTest):
    def test_baseline_ports_are_refused(self):
        for port in ('9999', '10000'):
            result = self.run_script('camera_serve.sh', '--port', port, status=1)
            self.assertIn('refusing to serve on baseline port', result.stderr)
        self.assertEqual(self.calls(), [])

    def test_baseline_listener_is_reported_and_never_killed(self):
        body = ('if [[ "$*" == *10000* ]]; then\n'
                '  echo "LISTEN 0 5 172.16.42.2:10000 0.0.0.0:* users:((\\"nbd-server\\",pid=1,fd=3))"\n'
                'fi\nexit 0')
        build_checkout(self.root, cache='copy', tools={'ss': body})
        result = self.run_script('camera_serve.sh', '--check')
        self.assertIn('baseline port 10000 is listening (never touched by this script)',
                      result.stdout)
        self.assertFalse([line for line in self.calls() if line.startswith('sudo ')])

    def test_unrelated_process_on_the_candidate_port_blocks_the_check(self):
        body = ('echo "LISTEN 0 5 172.16.42.2:10001 0.0.0.0:* '
                'users:((\\"nginx\\",pid=1,fd=3))"\nexit 0')
        build_checkout(self.root, cache='copy', tools={'ss': body})
        result = self.run_script('camera_serve.sh', '--check', status=1)
        self.assertIn('unrelated process', result.stderr)
        self.assertIn('CHECK FAILED', result.stdout)
        self.assertFalse([line for line in self.calls() if line.startswith('sudo ')])

    def test_stale_candidate_server_is_reported_but_not_stopped_by_check(self):
        listener = self.base / 'nbd-server'
        shutil.copyfile(shutil.which('sleep'), listener)
        listener.chmod(0o755)
        process = subprocess.Popen([str(listener), '30'])
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        body = ('echo "LISTEN 0 5 172.16.42.2:10001 0.0.0.0:* '
                'users:((\\"nbd-server\\",pid=%d,fd=3))"\nexit 0' % process.pid)
        build_checkout(self.root, cache='copy', tools={'ss': body})
        result = self.run_script('camera_serve.sh', '--check')
        self.assertIn('would stop this candidate server after confirmation', result.stdout)
        self.assertFalse([line for line in self.calls() if line.startswith('sudo ')])
        self.assertIsNone(process.poll(), 'the candidate server must still be running')


class WorktreeGuardTests(unittest.TestCase):
    def test_baseline_checkout_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / 'nura-sm8150-7-0'
            fixture = build_checkout(root)
            environment = {**os.environ,
                           'PATH': '%s:%s' % (fixture['bin'], os.environ['PATH']),
                           'TMPDIR': str(base / 'tmp'),
                           'CALL_LOG': str(base / 'calls.log'),
                           'NURA_SYSFS_ROOT': str(fixture['sysfs']),
                           'NURA_STATE_DIR': str(root / '.work/delivery')}
            (base / 'tmp').mkdir()
            (base / 'calls.log').touch()
            result = subprocess.run(['bash', str(root / 'scripts/camera_serve.sh'), '--check'],
                                    env=environment, cwd='/', capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('refusing to serve from the baseline checkout', result.stderr)


class UsbNetDetectionTests(CheckoutTest):
    def detect(self, *arguments, sysfs=None, status=0):
        environment = {}
        if sysfs is not None:
            environment['NURA_SYSFS_ROOT'] = str(sysfs)
        result = self.run_script('camera_usb_net.sh', 'detect', *arguments,
                                 status=status, environment=environment or None)
        selected = [line.split('=', 1)[1] for line in result.stdout.splitlines()
                    if line.startswith('IFACE=')]
        self.assertEqual(len(selected), 1, result.stdout)
        return selected[0]

    def test_carrier_up_wins_over_newer_interface(self):
        self.assertEqual(self.detect(), 'usb0')

    def test_newest_interface_wins_without_carrier(self):
        sysfs = self.root / 'sys-no-carrier'
        write_sysfs(sysfs, {'enp0s20f0u2': {'driver': 'cdc_ncm', 'carrier': 0, 'ifindex': 7},
                            'enp0s20f0u9': {'driver': 'cdc_ncm', 'carrier': 0, 'ifindex': 40}})
        self.assertEqual(self.detect(sysfs=sysfs), 'enp0s20f0u9')

    def test_explicit_interface_wins(self):
        self.assertEqual(self.detect('--iface', 'enp0s20f0u1'), 'enp0s20f0u1')

    def test_non_usb_interfaces_are_ignored(self):
        sysfs = self.root / 'sys-non-usb'
        write_sysfs(sysfs, {'enp14s0': {'driver': None, 'carrier': 1, 'ifindex': 4},
                            'docker0': {'driver': None, 'carrier': 1, 'ifindex': 100}})
        (sysfs / 'class/net/enp14s0/device/uevent').write_text('DRIVER=e1000e\n')
        self.assertEqual(self.detect(sysfs=sysfs, status=1), '')

    def test_missing_explicit_interface_fails(self):
        result = self.run_script('camera_usb_net.sh', 'detect', '--iface', 'nope0', status=1)
        self.assertIn('no such interface: nope0', result.stderr)
        self.assertEqual(result.stdout, '')

    def test_wait_gives_up_with_the_configured_timeout(self):
        sysfs = self.root / 'sys-empty'
        (sysfs / 'class/net').mkdir(parents=True)
        result = self.run_script('camera_usb_net.sh', 'wait', '--timeout', '1',
                                 status=1, environment={'NURA_SYSFS_ROOT': str(sysfs)})
        self.assertIn('timed out after 1s', result.stderr)

    def test_wait_reports_the_interface_when_present(self):
        result = self.run_script('camera_usb_net.sh', 'wait', '--timeout', '5')
        self.assertIn('IFACE=usb0', result.stdout)

    def test_help_and_usage(self):
        self.assertIn('Usage:', self.run_script('camera_usb_net.sh', '--help').stdout)
        self.run_script('camera_usb_net.sh', status=1)


class UsbNetAddressTests(CheckoutTest):
    def state(self, iface, owned):
        directory = self.root / '.work/delivery'
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'usb-net.state').write_text(
            'IFACE=%s\nADDR=172.16.42.2/24\nOWNED=%s\n' % (iface, owned))

    def ip_with_address(self):
        body = ('if [[ "$*" == *"address show"* ]]; then\n'
                '  echo "3: usb0    inet 172.16.42.2/24 scope global usb0"\n'
                'fi\nexit 0')
        build_checkout(self.root, cache='copy', tools={'ip': body})

    def test_down_removes_an_address_this_tool_added(self):
        self.ip_with_address()
        self.state('usb0', 'yes')
        result = self.run_script('camera_usb_net.sh', 'down', '--dry-run')
        self.assertIn('DRY-RUN: sudo ip addr del 172.16.42.2/24 dev usb0', result.stdout)

    def test_down_keeps_an_address_it_did_not_add(self):
        self.ip_with_address()
        self.state('usb0', 'no')
        result = self.run_script('camera_usb_net.sh', 'down', '--dry-run')
        self.assertNotIn('ip addr del', result.stdout)
        self.assertIn('was not added by this tool', result.stderr)

    def test_up_reports_a_conflicting_address(self):
        body = ('if [[ "$*" == *"address show"* ]]; then\n'
                '  echo "4: enp14s0    inet 172.16.42.2/24 scope global enp14s0"\n'
                'fi\nexit 0')
        build_checkout(self.root, cache='copy', tools={'ip': body})
        result = self.run_script('camera_usb_net.sh', 'up', '--iface', 'usb0', '--dry-run',
                                 status=1)
        self.assertIn('already configured on: enp14s0', result.stderr)

    def test_up_dry_run_prints_the_privileged_commands(self):
        result = self.run_script('camera_usb_net.sh', 'up', '--iface', 'usb0', '--dry-run')
        self.assertIn('DRY-RUN: sudo ip link set dev usb0 up', result.stdout)
        self.assertIn('DRY-RUN: sudo ip addr replace 172.16.42.2/24 dev usb0', result.stdout)
        self.assertIn('DRY-RUN: write', result.stdout)
        self.assertEqual([line for line in self.calls()
                          if 'replace' in line or 'link set' in line], [])


if __name__ == '__main__':
    unittest.main()
