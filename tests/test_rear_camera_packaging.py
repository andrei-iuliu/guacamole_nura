"""Failure-path checks for the privileged preparer, using a fake pmbootstrap."""
import gzip
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import test_rear_camera_7_0 as rear
ROOT = rear.ROOT
import check_rear_camera_package as package

RELEASE = package.RELEASE


class FakePreparer:
    """A complete on-disk checkout whose python3 never runs pmbootstrap."""

    def __init__(self, base, version=None, unversioned=False, findmnt=None, ss=None):
        self.root = Path(base) / 'checkout with spaces'
        scripts = self.root / 'scripts'
        scripts.mkdir(parents=True)
        shutil.copy2(ROOT / 'scripts/rootfs_prepare_rear_camera.sh', scripts)
        self.state = self.root / '.work/rear-camera'
        (self.state / 'linux-v7.0.0-sm8150').mkdir(parents=True)
        output = self.state / 'output/include/config'
        output.mkdir(parents=True)
        (output / 'kernel.release').write_text(RELEASE + '\n')
        (self.state / 'linux-v7.0.0-sm8150/Makefile').write_text('all:\n')
        (self.state / 'pmbootstrap/pmb/config').mkdir(parents=True)
        (self.state / 'pmbootstrap/pmb/config/__init__.py').write_text('work_version = 8\n')
        (self.state / 'pmbootstrap/pmbootstrap.py').write_text('# fake pmbootstrap\n')
        (self.state / 'rootfs').mkdir()
        (self.state / 'rootfs/pmbootstrap.cfg').write_text(
            '[pmbootstrap]\ndevice = oneplus-guacamole\n')
        (self.state / 'pmaports').mkdir()
        (self.state / 'pmaports/pmaports.cfg').write_text('[pmaports]\nversion=7\n')
        subprocess.run(['git', 'init', '-q', str(self.state / 'pmaports')], check=True,
                       capture_output=True)
        subprocess.run(['git', '-C', str(self.state / 'pmaports'), 'remote', 'add', 'origin',
                        'https://gitlab.postmarketos.org/postmarketOS/pmaports.git'], check=True,
                       capture_output=True)
        links = {
            'device/testing/device-oneplus-guacamole':
                self.root / 'device/testing/device-oneplus-guacamole',
            'device/testing/firmware-oneplus-guacamole':
                self.root / 'device/testing/firmware-oneplus-guacamole',
            'device/testing/linux-postmarketos-qcom-sm8150':
                self.root / 'device/testing/rear-camera/linux-postmarketos-qcom-sm8150',
            'main/hexagonrpcd': self.root / 'device/testing/hexagonrpcd',
            'main/iio-sensor-proxy': self.root / 'device/testing/iio-sensor-proxy',
            'main/postmarketos-mkinitfs-hook-netboot':
                self.root / 'device/testing/rear-camera/postmarketos-mkinitfs-hook-netboot',
            'temp/libcamera-rear': self.root / 'device/testing/rear-camera/libcamera-rear',
        }
        for relative, target in links.items():
            target.mkdir(parents=True, exist_ok=True)
            link = self.state / 'pmaports' / relative
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target, target_is_directory=True)
        for relative in ('device/testing/linux-postmarketos-qcom-sm8150',
                         'main/postmarketos-mkinitfs-hook-netboot'):
            (links[relative] / 'netboot.sh').write_text('NBD_PORT=10001\n')
        for archive in (
                'device/testing/firmware-oneplus-guacamole/'
                'firmware-oneplus-guacamole-24.0-20261011.tar.gz',
                'device/testing/device-oneplus-guacamole/'
                'guacamole-sensors-20261102.tar.gz'):
            path = self.root / archive
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'archive placeholder\n')
        kernel_package = self.root / 'build/rear-camera/kernel-package/boot'
        kernel_package.mkdir(parents=True)
        (kernel_package / 'vmlinuz').write_bytes(b'fake kernel\n')

        self.work = self.state / 'pmbootstrap-work'
        self.packages = self.work / 'packages/edge/aarch64'
        self.packages.mkdir(parents=True)
        self.old_apk = self.packages / 'linux-postmarketos-qcom-sm8150-7.0.0_p20261110114554-r0.apk'
        self.old_apk.write_bytes(b'previous candidate')
        if unversioned:
            (self.work / 'existing-data').write_text('preserve me')
        elif version is not None:
            (self.work / 'version').write_text(version + '\n')
        else:
            (self.work / 'version').write_text('8\n')

        self.build = self.root / 'build/rear-camera'
        (self.build / 'boot.img').write_bytes(b'old boot image\n')
        (self.build / 'DELIVERY.json').write_text('{"old": "delivery"}\n')
        (self.build / 'packaging-inputs.json').write_text('{"old": "inputs"}\n')
        self.export = self.state / 'rootfs/export'
        self.export.mkdir(parents=True)
        for name, target in (
                ('boot.img', '../../pmbootstrap-work/chroot_rootfs_oneplus-guacamole/boot/boot.img'),
                ('initramfs', '../../pmbootstrap-work/chroot_rootfs_oneplus-guacamole/boot/initramfs'),
                ('oneplus-guacamole.img',
                 '../../pmbootstrap-work/chroot_native/home/pmos/rootfs/oneplus-guacamole.img')):
            (self.export / name).symlink_to(target)

        binaries = self.root / 'bin'
        binaries.mkdir()
        self.calls = self.root / 'calls'
        python = binaries / 'python3'
        python.write_text('#!/bin/sh\nif [ "$1" = - ]; then exec ' +
                          shlex.quote(sys.executable) + ' "$@"; fi\n'
                          'printf "%s\\n" "$@" >> "$CALLS"\nexit 42\n')
        python.chmod(0o755)
        if findmnt == 'error':
            findmnt_body = 'exit 2'
        elif findmnt is not None:
            if findmnt == 'work':
                findmnt = str(self.work / 'chroot_native')
            findmnt_body = f'printf "%s\\n" "{findmnt}"'
        else:
            findmnt_body = 'exit 0'
        tool = binaries / 'findmnt'
        tool.write_text(f'#!/bin/sh\n{findmnt_body}\n')
        tool.chmod(0o755)
        ss_body = f'printf "%s\\n" "{ss}"' if ss is not None else 'exit 0'
        tool = binaries / 'ss'
        tool.write_text(f'#!/bin/sh\n{ss_body}\n')
        tool.chmod(0o755)

    def run(self):
        return subprocess.run(
            ['bash', str(self.root / 'scripts/rootfs_prepare_rear_camera.sh')],
            cwd=self.root.parent,
            env={**os.environ, 'PATH': str(self.root / 'bin') + ':' + os.environ['PATH'],
                 'CALLS': str(self.calls)},
            capture_output=True, text=True, timeout=30)


@unittest.skipIf(os.geteuid() == 0, 'preparer refuses root')
class PreparerFailure(unittest.TestCase):
    def make(self, **kwargs):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return FakePreparer(temporary.name, **kwargs)

    def assert_stopped_before_install(self, harness, result):
        self.assertEqual(result.returncode, 42, result.stdout + result.stderr)
        argv = harness.calls.read_text().splitlines()
        self.assertEqual(argv[-3:], ['build', '--envkernel', 'linux-postmarketos-qcom-sm8150'])
        self.assertNotIn('install', argv)
        self.assertEqual((harness.work / 'version').read_text(), '8\n')
        self.assertTrue((harness.state / 'linux-v7.0.0-sm8150/.output').is_dir())
        self.assertFalse((harness.state / 'linux-v7.0.0-sm8150/.output').is_symlink())
        rollback = list(harness.build.glob('rollback-*'))
        self.assertEqual(len(rollback), 1)
        self.assertEqual((rollback[0] / 'boot.img').read_bytes(), b'old boot image\n')
        self.assertEqual((rollback[0] / 'DELIVERY.json').read_text(), '{"old": "delivery"}\n')
        self.assertEqual((rollback[0] / 'packaging-inputs.json').read_text(), '{"old": "inputs"}\n')
        exports = list((harness.state / 'rootfs').glob('export.before-*'))
        self.assertEqual(len(exports), 1)
        self.assertFalse(harness.export.exists())
        return exports[0]

    def test_packaging_failure_stops_before_install(self):
        harness = self.make()
        result = harness.run()
        self.assert_stopped_before_install(harness, result)
        self.assertFalse(harness.old_apk.exists())
        self.assertEqual(
            (harness.state / 'rootfs/previous-kernels' / harness.old_apk.name).read_bytes(),
            b'previous candidate')

    def test_incompatible_work_directory_stops_before_pmbootstrap(self):
        harness = self.make(version='7')
        result = harness.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(harness.calls.exists())
        self.assertEqual((harness.work / 'version').read_text(), '7\n')

    def test_unversioned_partial_work_is_moved_not_deleted(self):
        harness = self.make(unversioned=True)
        result = harness.run()
        exports = self.assert_stopped_before_install(harness, result)
        backups = list(harness.state.glob('pmbootstrap-work.partial-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / 'existing-data').read_text(), 'preserve me')
        self.assertEqual((backups[0] / 'packages/edge/aarch64' / harness.old_apk.name).read_bytes(),
                         b'previous candidate')
        self.assertFalse((harness.work / 'existing-data').exists())
        self.assertEqual((exports / 'boot.img').readlink().as_posix(),
                         f'../../{backups[0].name}/chroot_rootfs_oneplus-guacamole/boot/boot.img')

    def test_active_mount_refuses_before_moving_the_old_tree(self):
        harness = self.make(unversioned=True,
                            findmnt='work')
        result = harness.run()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse(harness.calls.exists())
        self.assertEqual((harness.work / 'existing-data').read_text(), 'preserve me')
        self.assertFalse((harness.work / 'version').exists())
        self.assertFalse(list(harness.state.glob('pmbootstrap-work.partial-*')))

    def test_findmnt_failure_refuses_before_moving_the_old_tree(self):
        harness = self.make(unversioned=True,
                            findmnt='error')
        result = harness.run()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('findmnt failed', result.stdout + result.stderr)
        self.assertFalse(harness.calls.exists())
        self.assertEqual((harness.work / 'existing-data').read_text(), 'preserve me')
        self.assertFalse(list(harness.state.glob('pmbootstrap-work.partial-*')))
        self.assertTrue(harness.export.is_dir())

    def test_live_candidate_port_refuses_before_moving_the_old_tree(self):
        harness = self.make(unversioned=True,
                            ss='LISTEN 0 1 0.0.0.0:10001 0.0.0.0:*')
        result = harness.run()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse(harness.calls.exists())
        self.assertEqual((harness.work / 'existing-data').read_text(), 'preserve me')
        self.assertFalse(list(harness.state.glob('pmbootstrap-work.partial-*')))


class InputIdentity(unittest.TestCase):
    def test_recorded_identity_and_changed_kernel(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            archive = rear.RearInitramfs().archive(root, 10001)
            stage = root / 'stage'
            release = stage / 'usr/share/kernel/postmarketos-qcom-sm8150/kernel.release'
            release.parent.mkdir(parents=True)
            release.write_text(package.RELEASE)
            image = stage / 'boot/vmlinuz'
            image.parent.mkdir()
            image.write_bytes(b'fixture kernel')
            module = stage / 'usr/lib/modules' / package.RELEASE / 'kernel/drivers/test/s6sy761.ko'
            module.parent.mkdir(parents=True)
            module.write_bytes(b'touch module')
            payload = package.module_payload(stage)
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({
                'release': package.RELEASE, 'nbd_port': 10001,
                'initramfs_sha256': package.digest(archive),
                'vmlinuz_sha256': package.digest(image),
                'module_payload_sha256': payload['sha256'],
                'module_count': payload['count']}))
            with mock.patch.object(package, 'MANIFEST', manifest):
                package.check_inputs(archive, stage)
                module.write_bytes(b'tampered touch module')
                with self.assertRaisesRegex(ValueError, 'inputs differ'):
                    package.check_inputs(archive, stage)
                module.write_bytes(b'touch module')
                image.write_bytes(b'different kernel')
                with self.assertRaisesRegex(ValueError, 'inputs differ'):
                    package.check_inputs(archive, stage)


class ModulePayload(unittest.TestCase):
    MODULES = package.CAMERA_MODULES + ('s6sy761', 'fastrpc', 'slim-qcom-ngd-ctrl')

    def make_tree(self, root):
        stage = root / 'stage'
        release = stage / 'usr/share/kernel/postmarketos-qcom-sm8150/kernel.release'
        release.parent.mkdir(parents=True)
        release.write_text(RELEASE)
        (stage / 'boot/vmlinuz').parent.mkdir()
        (stage / 'boot/vmlinuz').write_bytes(b'kernel with built-in display and SLIMbus changes')
        dtb = stage / 'boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb'
        dtb.parent.mkdir(parents=True)
        dtb.write_bytes(b'dtb fixture')
        for name in self.MODULES:
            path = stage / 'usr/lib/modules' / RELEASE / 'kernel/drivers/test' / f'{name}.ko'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f'{name} payload'.encode())
        rootfs = root / 'rootfs'
        for relative in ('boot/vmlinuz',
                         'boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb',
                         'usr/share/kernel/postmarketos-qcom-sm8150/kernel.release'):
            source = stage / relative
            destination = rootfs / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
        for name in self.MODULES:
            relative = f'kernel/drivers/test/{name}.ko'
            destination = rootfs / 'usr/lib/modules' / RELEASE / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((stage / 'usr/lib/modules' / RELEASE / relative).read_bytes())
        hook = rootfs / 'usr/share/mkinitfs/hooks/netboot.sh'
        hook.parent.mkdir(parents=True)
        hook.write_text('NBD_PORT=10001\n')
        return stage, rootfs, rear.RearInitramfs().archive(root, 10001)

    def run_check(self, root, stage, rootfs, archive):
        manifest = root / 'packaging-inputs.json'
        with mock.patch.object(package, 'MANIFEST', manifest), \
             mock.patch.object(package.subprocess, 'check_output',
                               return_value=RELEASE + ' SMP preempt mod_unload'):
            return package.check_inputs(archive, stage, rootfs)

    def test_changed_module_bytes_are_compared_not_just_names(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            stage, rootfs, archive = self.make_tree(root)
            self.run_check(root, stage, rootfs, archive)
            changed = rootfs / 'usr/lib/modules' / RELEASE / 'kernel/drivers/test/s6sy761.ko'
            changed.write_bytes(b'different touch payload')
            with self.assertRaisesRegex(ValueError, 'module payload differs: .*s6sy761'):
                self.run_check(root, stage, rootfs, archive)

    def test_missing_and_extra_modules_fail(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            stage, rootfs, archive = self.make_tree(root)
            fastrpc = rootfs / 'usr/lib/modules' / RELEASE / 'kernel/drivers/test/fastrpc.ko'
            original = fastrpc.read_bytes()
            fastrpc.unlink()
            with self.assertRaisesRegex(ValueError, 'missing .*fastrpc'):
                self.run_check(root, stage, rootfs, archive)
            fastrpc.write_bytes(original)
            extra = rootfs / 'usr/lib/modules' / RELEASE / 'kernel/drivers/test/stale.ko'
            extra.write_bytes(b'stale')
            with self.assertRaisesRegex(ValueError, 'unexpected .*stale'):
                self.run_check(root, stage, rootfs, archive)

    def test_intentional_compression_change_is_accepted(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            stage, rootfs, archive = self.make_tree(root)
            module = rootfs / 'usr/lib/modules' / RELEASE / 'kernel/drivers/test/s6sy761.ko'
            module.write_bytes(gzip.compress(module.read_bytes()))
            module.rename(module.with_suffix('.ko.gz'))
            self.run_check(root, stage, rootfs, archive)

    def test_builtin_kernel_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            stage, rootfs, archive = self.make_tree(root)
            (rootfs / 'boot/vmlinuz').write_bytes(b'kernel without the built-in display fix')
            with self.assertRaisesRegex(ValueError, 'rootfs kernel differs from staging'):
                self.run_check(root, stage, rootfs, archive)


class KernelRecipeInstall(unittest.TestCase):
    def test_individual_dtbs_reach_apk_package(self):
        output = ROOT / '.work/rear-camera/output'
        if not (output / 'arch/arm64/boot/Image.gz').exists():
            self.skipTest('kernel not built')
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            binaries = root / 'bin'
            binaries.mkdir()
            stage = root / 'package'
            make = binaries / 'make'
            make.write_text('#!/bin/sh\nmkdir -p "$TEST_STAGE/usr/lib/modules/7.0.0-sm8150-rear1"\n'
                            'ln -s "$TEST_OUTPUT" "$TEST_STAGE/usr/lib/modules/'
                            '7.0.0-sm8150-rear1/build"\n')
            make.chmod(0o755)
            recipe = ROOT / 'device/testing/rear-camera/linux-postmarketos-qcom-sm8150/APKBUILD'
            subprocess.run(['sh', '-c', '. "$1"; builddir="$2"; pkgdir="$3"; package',
                            'sh', str(recipe), str(output), str(stage)],
                           env={**os.environ, 'PATH': str(binaries) + ':' + os.environ['PATH'],
                                'TEST_STAGE': str(stage), 'TEST_OUTPUT': str(output)},
                           check=True, capture_output=True)
            self.assertEqual(len(list((stage / 'boot/dtbs/qcom').glob('*.dtb'))), 4)
            for path in (stage / 'boot/dtbs/qcom').glob('*.dtb'):
                self.assertEqual(path.read_bytes(),
                                 (output / 'arch/arm64/boot/dts/qcom' / path.name).read_bytes())
            self.assertEqual(package.digest(stage / 'boot/vmlinuz'),
                             package.digest(output / 'arch/arm64/boot/Image.gz'))
            release = stage / 'usr/share/kernel/postmarketos-qcom-sm8150/kernel.release'
            self.assertEqual(release.read_text().strip(), package.RELEASE)
