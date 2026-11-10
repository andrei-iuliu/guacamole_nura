"""Exercise scripts/camera_build.sh inside a temporary fake checkout.

The script resolves its repository root from BASH_SOURCE, so every test copies
it into a throw-away tree.  No test runs sudo, pmbootstrap, a phone command or
a real build; the manifest tests reuse the already-built candidate CCI module
and base DTB only when they exist in this checkout.
"""
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/camera_build.sh'
RELEASE = '7.0.0-sm8150-rear1'
CCI_SHA256 = '4cd35f05ff873700c43baad265efd0bf73166352baac79f88d4aeca456fb27e4'
REAL_CCI = ROOT / 'build/rear-camera/kernel-package/usr/lib/modules' / RELEASE / \
    'kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst'
REAL_DTB = ROOT / 'build/rear-camera/kernel-package/boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb'
requires_built_artifacts = unittest.skipUnless(
    REAL_CCI.is_file() and REAL_DTB.is_file(), 'candidate CCI module/base DTB are not built here')

ARTIFACT_KEYS = ['boot_img', 'image', 'kernel_package', 'dtb', 'initramfs', 'rootfs_image',
                 'i2c_qcom_cci']


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def boot_image(dtb):
    """Minimal Android v2 boot image that carries only a DTB blob."""
    page = 4096
    header = bytearray(page)
    header[0:8] = b'ANDROID!'
    struct.pack_into('<I', header, 8, 0)        # kernel_size
    struct.pack_into('<I', header, 16, 0)       # ramdisk_size
    struct.pack_into('<I', header, 24, 0)       # second_size
    struct.pack_into('<I', header, 36, page)    # page_size
    struct.pack_into('<I', header, 40, 2)       # header_version
    struct.pack_into('<I', header, 1632, 0)     # recovery_dtbo_size
    struct.pack_into('<I', header, 1644, 1660)  # header_size
    struct.pack_into('<I', header, 1648, len(dtb))
    struct.pack_into('<Q', header, 1652, 0x03000000)
    return bytes(header) + dtb


class FakeCheckout:
    def __init__(self, base):
        self.root = Path(base) / 'candidate checkout'
        (self.root / 'scripts').mkdir(parents=True)
        shutil.copy2(SCRIPT, self.root / 'scripts/camera_build.sh')
        # The helpers are only printed by --dry-run, never executed here.
        for helper in ('build_rear_camera_kernel.sh', 'build_rear_camera_libcamera.sh',
                       'build_bootimg_rear_camera.sh'):
            shutil.copy2(ROOT / 'scripts' / helper, self.root / 'scripts' / helper)
        self.recipe = self.root / 'device/testing/rear-camera/linux-postmarketos-qcom-sm8150'
        self.recipe.mkdir(parents=True)
        (self.recipe / 'APKBUILD').write_text('# candidate recipe marker\n')
        (self.recipe / 'config-postmarketos-qcom-sm8150.aarch64').write_text(
            'CONFIG_LOCALVERSION="-sm8150-rear1"\n')
        self.build = self.root / 'build/rear-camera'
        self.build.mkdir(parents=True)
        self.kernel_package = self.build / 'kernel-package'
        self.release_file = self.kernel_package / \
            'usr/share/kernel/postmarketos-qcom-sm8150/kernel.release'
        self.release_file.parent.mkdir(parents=True)
        self.release_file.write_text(RELEASE + '\n')
        self.delivery = self.build / 'DELIVERY.json'
        self.image = self.build / 'Image'
        self.vmlinuz = self.kernel_package / 'boot/vmlinuz'
        self.base_dtb = self.kernel_package / 'boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb'
        self.module = self.kernel_package / 'usr/lib/modules' / RELEASE / \
            'kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst'
        self.initramfs = self.root / '.work/rear-camera/rootfs/export/initramfs'
        self.rootfs_image = self.root / '.work/rear-camera/pmbootstrap-work/chroot_native' / \
            'home/pmos/rootfs/oneplus-guacamole.img'
        self.bootimg = self.build / 'boot.img'

    def write_artifacts(self, cci=b'not the fixed module', dtb=b'\xd0\x0d\xfe\xed' + b'\0' * 60):
        image = b'fake arm64 image payload\n'
        self.image.write_bytes(image)
        self.vmlinuz.parent.mkdir(parents=True, exist_ok=True)
        self.vmlinuz.write_bytes(gzip.compress(image))
        self.base_dtb.parent.mkdir(parents=True, exist_ok=True)
        self.base_dtb.write_bytes(dtb)
        self.initramfs.parent.mkdir(parents=True, exist_ok=True)
        self.initramfs.write_bytes(b'fake initramfs\n')
        self.rootfs_image.parent.mkdir(parents=True, exist_ok=True)
        self.rootfs_image.write_bytes(b'fake rootfs image\n')
        self.module.parent.mkdir(parents=True, exist_ok=True)
        self.module.write_bytes(cci)
        self.bootimg.write_bytes(boot_image(dtb))

    def write_patch(self):
        (self.recipe / '0031-i2c-qcom-cci-initialize-all-master-completions.patch').write_text(
            '+\tfor (i = 0; i < cci->data->num_masters; i++)\n'
            '+\t\tinit_completion(&cci->master[i].irq_complete);\n')

    def write_source(self, fixed=True):
        source = (self.root / '.work/rear-camera/linux-v7.0.0-sm8150/drivers/i2c/busses/'
                  'i2c-qcom-cci.c')
        source.parent.mkdir(parents=True, exist_ok=True)
        if fixed:
            source.write_text(
                'for (i = 0; i < cci->data->num_masters; i++)\n'
                '\tinit_completion(&cci->master[i].irq_complete);\n'
                'for_each_available_child_of_node(dev->of_node, child) {\n')
        else:
            source.write_text('for_each_available_child_of_node(dev->of_node, child) {\n')
        older = time.time() - 120
        os.utime(source, (older, older))
        return source

    def paths(self):
        return {
            'boot_img': self.bootimg,
            'image': self.image,
            'kernel_package': self.vmlinuz,
            'dtb': self.base_dtb,
            'initramfs': self.initramfs,
            'rootfs_image': self.rootfs_image,
            'i2c_qcom_cci': self.module,
        }

    def run(self, *arguments, env=None, expect=0, timeout=120, cwd='/'):
        environment = {key: value for key, value in os.environ.items()
                       if key not in {'INITRAMFS', 'OUTPUT_DIR', 'KERNEL_DIR', 'BASE_DTB',
                                      'TEMP_DTB_DIR', 'JOBS', 'DTB_MODE', 'DTB_OFFSET'}}
        environment.update(env or {})
        result = subprocess.run(
            ['bash', str(self.root / 'scripts/camera_build.sh'), *arguments],
            cwd=cwd, env=environment, capture_output=True, text=True, timeout=timeout)
        output = result.stdout + result.stderr
        if result.returncode != expect:
            raise AssertionError(
                f'expected exit {expect}, got {result.returncode}\n'
                f'--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}')
        return output


class TemporaryCheckoutTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.checkout = FakeCheckout(self.temporary.name)


class HelpTests(TemporaryCheckoutTest):
    def test_help_lists_the_delivery_subcommands(self):
        output = self.checkout.run('--help')
        for subcommand in ('check', 'kernel', 'libcamera', 'bootimg', 'manifest', 'all', 'package'):
            self.assertIn(subcommand, output)
        self.assertIn('--dry-run', output)
        self.assertIn('--jobs', output)
        self.assertIn('DELIVERY.json', output)


class CheckoutRefusalTests(TemporaryCheckoutTest):
    def test_refuses_a_directory_that_is_not_the_candidate_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'some other checkout'
            (root / 'scripts').mkdir(parents=True)
            shutil.copy2(SCRIPT, root / 'scripts/camera_build.sh')
            result = subprocess.run(['bash', str(root / 'scripts/camera_build.sh'), 'check'],
                                    cwd='/', capture_output=True, text=True, timeout=60)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('not the rear-camera candidate checkout', result.stderr)
            self.assertFalse((root / 'build').exists())

    def test_refuses_a_staged_release_from_another_kernel(self):
        self.checkout.release_file.write_text('7.0.0-sm8150\n')
        output = self.checkout.run('check', expect=1)
        self.assertIn('refusing to mix kernels', output)

    def test_refuses_artifact_overrides_outside_the_checkout(self):
        with tempfile.NamedTemporaryFile(suffix='.img') as outside:
            output = self.checkout.run('check', env={'INITRAMFS': outside.name}, expect=1)
        self.assertIn('resolves outside this checkout', output)


class CheckTests(TemporaryCheckoutTest):
    def test_check_fails_closed_on_missing_artifacts(self):
        output = self.checkout.run('check', expect=1)
        self.assertIn('FAIL', output)
        self.assertIn('CCI module missing', output)
        self.assertIn('boot.img missing', output)
        self.assertIn('packaging inputs missing', output)
        self.assertIn('DELIVERY.json missing', output)
        self.assertFalse(self.checkout.delivery.exists())
        self.assertFalse((self.checkout.build / '.camera_build.lock').exists())

    def test_check_dry_run_only_prints_the_plan(self):
        output = self.checkout.run('check', '--dry-run')
        self.assertIn('WOULD CHECK', output)
        self.assertIn('dry-run: nothing was read, hashed or written', output)
        self.assertNotIn('PASS', output)
        self.assertFalse(self.checkout.delivery.exists())
        self.assertFalse((self.checkout.build / '.camera_build.lock').exists())
        self.assertFalse((self.checkout.root / '.work').exists())

    def test_check_flags_a_module_that_differs_from_the_manifest(self):
        self.checkout.write_patch()
        self.checkout.write_source()
        self.checkout.write_artifacts(cci=b'x' * 8920)
        document = {'release': RELEASE, 'worktree': str(self.checkout.root)}
        for key, path in self.checkout.paths().items():
            document[key] = {'path': str(path), 'sha256': digest(path),
                             'size': path.stat().st_size}
        document['i2c_qcom_cci']['sha256'] = '0' * 64
        self.checkout.delivery.write_text(json.dumps(document))
        output = self.checkout.run('check', expect=1)
        self.assertIn('DELIVERY.json records CCI module', output)
        self.assertIn('staged module is', output)

    def test_check_reports_a_delivery_manifest_hash_mismatch(self):
        self.checkout.write_artifacts()
        document = {
            'release': RELEASE,
            'worktree': str(self.checkout.root),
            'created_at': '2026-11-10T00:00:00Z',
            'git_head': 'unknown',
        }
        for key, path in self.checkout.paths().items():
            document[key] = {'path': str(path), 'sha256': '00' * 32, 'size': 1}
        self.checkout.delivery.write_text(json.dumps(document))
        output = self.checkout.run('check', expect=1)
        self.assertIn('DELIVERY.json boot_img hash/size mismatch', output)
        self.assertIn('DELIVERY.json i2c_qcom_cci hash/size mismatch', output)
        self.assertIn('DELIVERY.json release: 7.0.0-sm8150-rear1', output)


class BuildPhaseTests(TemporaryCheckoutTest):
    def test_kernel_dry_run_prints_the_helper_without_running_it(self):
        output = self.checkout.run('kernel', '--dry-run')
        self.assertIn('DRY-RUN', output)
        self.assertIn('build_rear_camera_kernel.sh', output)
        self.assertIn(str(self.checkout.root), output)
        self.assertFalse((self.checkout.root / '.work').exists())
        self.assertFalse((self.checkout.build / '.camera_build.lock').exists())

    def test_libcamera_dry_run_prints_the_helper_without_running_it(self):
        output = self.checkout.run('libcamera', '--dry-run')
        self.assertIn('build_rear_camera_libcamera.sh', output)
        self.assertFalse((self.checkout.build / 'libcamera-stage').exists())

    def test_bootimg_requires_an_explicit_initramfs(self):
        output = self.checkout.run('bootimg', expect=1)
        self.assertIn('INITRAMFS is not set', output)

    def test_bootimg_dry_run_prints_the_packer_command(self):
        self.checkout.write_artifacts()
        output = self.checkout.run('bootimg', '--dry-run',
                                   env={'INITRAMFS': str(self.checkout.initramfs)})
        self.assertIn('build_bootimg_rear_camera.sh', output)
        self.assertIn('changed boot.img hash', output)

    def test_all_dry_run_prints_the_phase_order(self):
        self.checkout.write_artifacts()
        output = self.checkout.run('all', '--dry-run',
                                   env={'INITRAMFS': str(self.checkout.initramfs)})
        for phase in ('check (prebuild)', 'build_rear_camera_kernel.sh',
                      'build_rear_camera_libcamera.sh', 'unittest discover',
                      'build_bootimg_rear_camera.sh', 'DELIVERY.json'):
            self.assertIn(phase, output)
        self.assertFalse(self.checkout.delivery.exists())


class AllPhaseTests(TemporaryCheckoutTest):
    def stub_kernel(self, exit_code):
        log = Path(self.temporary.name) / 'kernel-calls'
        helper = self.checkout.root / 'scripts/build_rear_camera_kernel.sh'
        helper.write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "{log}"\nexit {exit_code}\n')
        helper.chmod(0o755)
        return log

    def write_patch(self):
        (self.checkout.recipe / '0031-i2c-qcom-cci-initialize-all-master-completions.patch').write_text(
            '+\tfor (i = 0; i < cci->data->num_masters; i++)\n'
            '+\t\tinit_completion(&cci->master[i].irq_complete);\n')

    def test_all_stops_before_building_when_a_source_prerequisite_is_missing(self):
        log = self.stub_kernel(0)
        output = self.checkout.run('all', expect=1)
        self.assertIn('phase 1/6: check', output)
        self.assertIn('source prerequisites failed', output)
        self.assertNotIn('phase 2/6: kernel', output)
        self.assertFalse(log.exists())

    def test_all_reaches_the_kernel_phase_when_only_artifacts_are_missing(self):
        self.write_patch()
        log = self.stub_kernel(42)
        output = self.checkout.run('all', expect=42)
        self.assertIn('WARN  CCI module missing', output)
        self.assertNotIn('FAIL', output)
        self.assertIn('phase 2/6: kernel', output)
        self.assertTrue(log.exists(), 'the kernel helper should have been invoked')


class ManifestTests(TemporaryCheckoutTest):
    def test_manifest_records_the_module_this_build_produced(self):
        self.checkout.write_patch()
        self.checkout.write_source()
        future = b'future-build-module' * 500
        self.checkout.write_artifacts(cci=future)
        output = self.checkout.run('manifest')
        document = json.loads(self.checkout.delivery.read_text())
        self.assertEqual(document['i2c_qcom_cci']['sha256'], hashlib.sha256(future).hexdigest())
        self.assertEqual(document['i2c_qcom_cci']['size'], len(future))
        self.assertIn('CCI module is newer than the patched source', output)

    def test_manifest_refuses_when_the_source_fix_is_missing(self):
        self.checkout.write_artifacts(cci=b'pre-fix module stand-in')
        output = self.checkout.run('manifest', expect=1)
        self.assertIn('patch 0031 missing', output)
        self.assertFalse(self.checkout.delivery.exists())

    def test_manifest_refuses_a_module_older_than_the_patched_source(self):
        self.checkout.write_patch()
        source = self.checkout.write_source()
        self.checkout.write_artifacts(cci=b'x' * 8920)
        newer = time.time() + 120
        os.utime(source, (newer, newer))
        output = self.checkout.run('manifest', expect=1)
        self.assertIn('is not newer than', output)
        self.assertFalse(self.checkout.delivery.exists())

    def test_manifest_dry_run_writes_nothing(self):
        output = self.checkout.run('manifest', '--dry-run')
        self.assertIn('DRY-RUN', output)
        self.assertIn('DELIVERY.json', output)
        self.assertFalse(self.checkout.delivery.exists())

    @requires_built_artifacts
    def test_manifest_records_the_verified_artifacts(self):
        self.checkout.write_patch()
        self.checkout.write_source()
        self.checkout.write_artifacts(cci=REAL_CCI.read_bytes(), dtb=REAL_DTB.read_bytes())
        newer = time.time() + 60
        os.utime(self.checkout.bootimg, (newer, newer))
        self.checkout.run('manifest')
        document = json.loads(self.checkout.delivery.read_text())
        self.assertEqual(document['release'], RELEASE)
        self.assertEqual(document['worktree'], str(self.checkout.root))
        self.assertEqual(document['git_head'], 'unknown')
        self.assertIn('created_at', document)
        for key, path in self.checkout.paths().items():
            self.assertEqual(set(document[key]), {'path', 'sha256', 'size'}, key)
            self.assertEqual(document[key]['sha256'], digest(path), key)
            self.assertEqual(document[key]['size'], path.stat().st_size, key)
            self.assertEqual(document[key]['path'], str(path), key)
        self.assertEqual(document['i2c_qcom_cci']['sha256'], digest(REAL_CCI))
        self.assertEqual(document['i2c_qcom_cci']['size'], REAL_CCI.stat().st_size)

    @requires_built_artifacts
    def test_manifest_refuses_an_input_newer_than_the_boot_image(self):
        self.checkout.write_patch()
        self.checkout.write_source()
        self.checkout.write_artifacts(cci=REAL_CCI.read_bytes(), dtb=REAL_DTB.read_bytes())
        newer = time.time() + 120
        os.utime(self.checkout.initramfs, (newer, newer))
        output = self.checkout.run('manifest', expect=1)
        self.assertIn('is newer than', output)
        self.assertIn('repack the boot image', output)
        self.assertFalse(self.checkout.delivery.exists())

    @requires_built_artifacts
    def test_manifest_refuses_a_boot_image_from_another_dtb(self):
        self.checkout.write_patch()
        self.checkout.write_source()
        self.checkout.write_artifacts(cci=REAL_CCI.read_bytes(), dtb=REAL_DTB.read_bytes())
        foreign = REAL_DTB.read_bytes() + b'\0' * 8
        self.checkout.bootimg.write_bytes(boot_image(foreign))
        newer = time.time() + 60
        os.utime(self.checkout.bootimg, (newer, newer))
        output = self.checkout.run('manifest', expect=1)
        self.assertIn('matches neither', output)
        self.assertFalse(self.checkout.delivery.exists())


class PackageTests(TemporaryCheckoutTest):
    def test_package_prints_the_privileged_command_and_runs_nothing(self):
        binaries = Path(self.temporary.name) / 'bin'
        binaries.mkdir()
        log = Path(self.temporary.name) / 'calls'
        for name in ('pmbootstrap', 'sudo'):
            tool = binaries / name
            tool.write_text(f'#!/bin/sh\nprintf "%s %s\\n" "${{0##*/}}" "$*" >> "{log}"\n')
            tool.chmod(0o755)
        output = self.checkout.run('package', env={'PATH': f'{binaries}:{os.environ["PATH"]}'})
        lines = [line for line in output.splitlines() if line.strip()]
        self.assertEqual(lines[0],
                         f'bash "{self.checkout.root}/scripts/rootfs_prepare_rear_camera.sh"')
        self.assertIn('privileged', output)
        self.assertIn('camera_serve.sh', output)
        self.assertFalse(log.exists(), log.read_text() if log.exists() else '')
        self.assertFalse(self.checkout.delivery.exists())


class ScriptSyntaxTests(unittest.TestCase):
    def test_script_parses_with_bash_n(self):
        subprocess.run(['bash', '-n', str(SCRIPT)], check=True)


if __name__ == '__main__':
    unittest.main()
