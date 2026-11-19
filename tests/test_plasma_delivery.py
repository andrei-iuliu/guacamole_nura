"""Focused host-only tests for the Plasma Mobile launch/delivery profile.

The fixtures are the same synthetic checkouts used by test_camera_serve.py and
test_camera_boot.py, promoted to the Plasma paths.  Nothing here starts a
server, runs sudo, touches the phone, or writes the working Phosh paths.
"""

import gzip
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

from test_camera_serve import (  # noqa: E402
    PLASMA_MARKERS,
    PLASMA_SYMLINKS,
    RELEASE,
    CheckoutTest,
    build_checkout,
    fixed_module_bytes,
    make_image,
    sha256,
)
from test_camera_boot import Checkout as BootCheckout  # noqa: E402


def _remap(path, root):
    """Rewrite both absolute and checkout-relative fixture paths."""
    path = str(path)
    return path.replace('build/rear-camera', 'build/plasma-mobile').replace(
        '.work/rear-camera', '.work/plasma-mobile')


def make_functional_initramfs(path):
    """A real release/port-10001 cpio archive, distinct from the fake boot one."""
    source = path.parent / (path.name + '.d')
    if source.exists():
        shutil.rmtree(source)
    module = source / 'lib/modules' / RELEASE
    module.mkdir(parents=True)
    (module / 'marker.txt').write_text(RELEASE + '\n')
    hook = source / 'usr/share/mkinitfs/hooks/netboot.sh'
    hook.parent.mkdir(parents=True)
    hook.write_text('#!/bin/sh\nNBD_PORT=10001\n')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as output:
        subprocess.run(
            'find . -print0 | cpio --null -o -H newc | gzip -9', shell=True,
            cwd=source, check=True, stdout=output, stderr=subprocess.DEVNULL)
    shutil.rmtree(source)
    return path


def promote_serve_fixture(test):
    """Copy the rear-camera fixture to isolated Plasma paths."""
    root = test.root
    pbuild = root / 'build/plasma-mobile'
    pstate = root / '.work/plasma-mobile'
    if pbuild.exists():
        shutil.rmtree(pbuild)
    if pstate.exists():
        shutil.rmtree(pstate)
    shutil.copytree(root / 'build/rear-camera', pbuild)
    shutil.copytree(root / '.work/rear-camera', pstate)

    source_image = pstate / 'pmbootstrap-work/chroot_native/home/pmos/rootfs/oneplus-guacamole.img'
    served_image = pstate / 'pmbootstrap-work/images_netboot/oneplus-guacamole.img'
    # The Plasma rootfs export initramfs is candidate bytes and may differ from
    # the fake known-good archive embedded in the copied boot image.
    candidate_initramfs = make_functional_initramfs(
        pstate / 'rootfs/export/initramfs')
    kernel = pbuild / 'kernel-package/boot'
    boot_payloads = {
        'vmlinuz': (kernel / 'vmlinuz').read_bytes(),
        'initramfs': candidate_initramfs.read_bytes(),
        'sm8150-oneplus-guacamole.dtb': (
            kernel / 'dtbs/qcom/sm8150-oneplus-guacamole.dtb').read_bytes(),
    }
    make_image(source_image, boot_payloads=boot_payloads,
               module_bytes=fixed_module_bytes(), port=10001, release=RELEASE,
               extra_files=PLASMA_MARKERS, extra_symlinks=PLASMA_SYMLINKS)
    shutil.copyfile(source_image, served_image)

    manifest_path = pbuild / 'DELIVERY.json'
    document = json.loads(manifest_path.read_text())
    for key in ('boot_img', 'image', 'kernel_package', 'dtb', 'initramfs',
                'rootfs_image', 'i2c_qcom_cci'):
        entry = document[key]
        entry['path'] = _remap(entry['path'], root)
        path = Path(entry['path'])
        entry['sha256'] = sha256(path)
        entry['size'] = path.stat().st_size
    document['rootfs_initramfs'] = {
        'path': str(candidate_initramfs),
        'sha256': sha256(candidate_initramfs),
        'size': candidate_initramfs.stat().st_size,
    }
    document['profile'] = 'plasma-mobile'
    document['ui'] = 'plasma-mobile'
    # The fixture models a restored rc2 candidate: rear1 kernel, rc2 label.
    document['release_candidate'] = 'nura-plasma-rc2'
    document['copied_from'] = 'build/rear-camera/boot.img'
    document['source_boot_sha256'] = sha256(root / 'build/rear-camera/boot.img')
    manifest_path.write_text(json.dumps(document, indent=2) + '\n')

    test.fixture['build'] = pbuild
    test.fixture['work'] = pstate
    test.fixture['source_image'] = source_image
    test.fixture['served_image'] = served_image
    return document


class PlasmaServeTests(CheckoutTest):
    plasma = True

    def setUp(self):
        super().setUp()
        self.document = promote_serve_fixture(self)

    def plasma_env(self, **extra):
        values = {'NURA_STATE_DIR': str(self.root / '.work/delivery-plasma-mobile')}
        values.update(extra)
        return values

    def test_manifest_and_state_are_isolated(self):
        result = self.run_script('plasma_serve.sh', '--check',
                                 environment=self.plasma_env())
        self.assertIn('PASS  profile plasma-mobile', result.stdout)
        self.assertIn('Plasma Mobile session/display-manager/no-greetd/lock markers', result.stdout)
        self.assertIn('.work/plasma-mobile', result.stdout)
        self.assertIn('.work/delivery-plasma-mobile', result.stderr)
        self.assertNotIn('build/rear-camera/DELIVERY.json', result.stdout)
        self.assertNotIn('profile rear-camera', result.stdout)
        forbidden = [line for line in self.calls()
                     if line.startswith(('sudo ', 'pmbootstrap '))]
        self.assertEqual(forbidden, [], 'check ran a privileged/server command')

    def test_check_does_not_write_phosh_state(self):
        self.run_script('plasma_serve.sh', '--check', environment=self.plasma_env())
        self.assertFalse((self.root / '.work/delivery').exists())
        self.assertFalse((self.root / '.work/rear-camera/usb-net.state').exists())

    def test_explicit_cleanup_check_runs_shared_shutdown_helper(self):
        helper = self.root / 'scripts/plasma_shutdown.sh'
        marker = self.root / 'cleanup-called'
        helper.write_text('#!/usr/bin/env bash\ntouch "$(dirname "$0")/../cleanup-called"\n')
        self.run_script('plasma_serve.sh', '--check', environment=self.plasma_env())
        self.assertFalse(marker.exists(), 'plain --check must not shut down chroots')
        self.run_script('plasma_serve.sh', '--cleanup', '--check', environment=self.plasma_env())
        self.assertTrue(marker.exists())

    def test_normal_serve_requires_successful_mount_cleanup_first(self):
        helper = self.root / 'scripts/plasma_shutdown.sh'
        helper.write_text('#!/usr/bin/env bash\necho "fixture shutdown failed" >&2\nexit 73\n')
        result = self.run_script('plasma_serve.sh', status=1, environment=self.plasma_env())
        self.assertIn('fixture shutdown failed', result.stderr)
        self.assertNotIn('rootfs image matches the manifest', result.stdout)
        self.assertFalse(any(line.startswith('sudo ') for line in self.calls()))

    def test_differing_valid_rootfs_initramfs_is_accepted(self):
        self.assertNotEqual(self.document['rootfs_initramfs']['sha256'],
                            self.document['initramfs']['sha256'])
        result = self.run_script('plasma_serve.sh', '--check',
                                 environment=self.plasma_env())
        self.assertIn('rootfs_initramfs', result.stdout)
        self.assertIn('release %s, NBD_PORT=10001' % RELEASE, result.stdout)

    def test_rc1_release_metadata_is_additive(self):
        document = self.document
        document['release_series'] = 'nura-plasma'
        document['release_candidate'] = 'nura-plasma-rc1'
        document['artifact_name'] = 'nura-plasma-rc1-oneplus-guacamole'
        (self.fixture['build'] / 'DELIVERY.json').write_text(json.dumps(document))
        result = self.run_script('plasma_serve.sh', '--check',
                                 environment=self.plasma_env())
        self.assertIn('PASS  profile plasma-mobile', result.stdout)
        self.assertIn('release %s' % RELEASE, result.stdout)

    def test_tampered_rootfs_initramfs_is_rejected(self):
        candidate = Path(self.document['rootfs_initramfs']['path'])
        candidate.write_bytes(candidate.read_bytes() + b'tampered')
        result = self.run_script('plasma_serve.sh', '--check', status=1,
                                 environment=self.plasma_env())
        self.assertIn('rootfs_initramfs', result.stderr)
        self.assertIn('manifest says', result.stderr)

    def test_wrong_profile_in_plasma_manifest_is_rejected(self):
        document = self.document
        document['profile'] = 'rear-camera'
        document['ui'] = 'rear-camera'
        (self.fixture['build'] / 'DELIVERY.json').write_text(json.dumps(document))
        result = self.run_script('plasma_serve.sh', '--check', status=1,
                                 environment=self.plasma_env())
        self.assertIn('profile', result.stderr)
        self.assertIn('rear-camera', result.stderr)

    def test_missing_profile_is_rejected(self):
        document = self.document
        document.pop('profile')
        document.pop('ui')
        (self.fixture['build'] / 'DELIVERY.json').write_text(json.dumps(document))
        result = self.run_script('plasma_serve.sh', '--check', status=1,
                                 environment=self.plasma_env())
        self.assertIn('does not record profile/ui', result.stderr)

    def test_live_server_is_refused_and_never_killed(self):
        body = ('echo "LISTEN 0 5 172.16.42.2:10001 0.0.0.0:* '
                'users:((\\"nbd-server\\",pid=1,fd=3))"\nexit 0')
        test = self
        fixture = build_checkout(test.root, cache='copy', plasma=True,
                                 tools={'ss': body, 'sudo': 'exit 0'})
        test.fixture = fixture
        promote_serve_fixture(test)
        result = self.run_script('plasma_serve.sh', '--yes',
                                 environment=self.plasma_env(), status=1)
        self.assertIn('never stops another server', result.stderr)
        killed = [line for line in self.calls() if line.startswith('sudo kill')]
        self.assertEqual(killed, [], 'Plasma serve must never kill a server')

    def test_plasma_usb_net_uses_isolated_state(self):
        result = self.run_script(
            'camera_usb_net.sh', 'up', '--iface', 'usb0', '--dry-run',
            '--profile', 'plasma-mobile', environment=self.plasma_env())
        self.assertIn('delivery-plasma-mobile/usb-net.state', result.stdout)
        self.assertNotIn('delivery/usb-net.state', result.stdout)

    def test_plasma_usb_net_status_reads_plasma_state(self):
        plasma_state = self.root / '.work/delivery-plasma-mobile'
        phosh_state = self.root / '.work/delivery'
        plasma_state.mkdir(parents=True)
        phosh_state.mkdir(parents=True)
        (plasma_state / 'usb-net.state').write_text(
            'IFACE=usb0\nADDR=172.16.42.2/24\nOWNED=yes\n')
        (phosh_state / 'usb-net.state').write_text(
            'IFACE=phosh0\nADDR=172.16.42.2/24\nOWNED=yes\n')
        result = self.run_script('camera_usb_net.sh', 'status', '--iface', 'usb0',
                                 '--profile', 'plasma-mobile',
                                 environment=self.plasma_env())
        self.assertIn('STATE_IFACE=usb0', result.stdout)
        self.assertNotIn('phosh0', result.stdout)

    def test_plasma_serve_refuses_a_profile_override(self):
        result = self.run_script('plasma_serve.sh', '--profile', 'rear-camera',
                                 '--check', status=2, environment=self.plasma_env())
        self.assertIn('profile is fixed to plasma-mobile', result.stderr)


class DefaultProfileTests(CheckoutTest):
    def test_rear_camera_default_paths_and_ui_are_unchanged(self):
        result = self.run_script('camera_serve.sh', '--check')
        self.assertIn('profile rear-camera', result.stderr)
        self.assertIn('.work/rear-camera', result.stdout)
        self.assertIn('.work/delivery', result.stderr)
        self.assertNotIn('profile plasma-mobile', result.stdout)
        self.assertNotIn('Plasma Mobile session markers', result.stdout)

    def test_rear_camera_refuses_a_plasma_manifest(self):
        document = self.manifest()
        document['profile'] = 'plasma-mobile'
        document['ui'] = 'plasma-mobile'
        (self.fixture['build'] / 'DELIVERY.json').write_text(json.dumps(document))
        result = self.run_script('camera_serve.sh', '--check', status=1)
        self.assertIn('not rear-camera', result.stderr)
        self.assertIn('plasma_serve.sh', result.stderr)


def promote_boot_fixture(checkout):
    root = checkout.root
    pbuild = root / 'build/plasma-mobile'
    pstate = root / '.work/plasma-mobile'
    shutil.copytree(root / 'build/rear-camera', pbuild)
    shutil.copytree(root / '.work/rear-camera', pstate)
    # Keep the boot-image initramfs entry (the fake known-good archive) and
    # record a separate candidate rootfs export, as the new schema requires.
    candidate_initramfs = make_functional_initramfs(
        pstate / 'rootfs/export/rootfs-initramfs')
    manifest_path = pbuild / 'DELIVERY.json'
    document = json.loads(manifest_path.read_text())
    for key in ('boot_img', 'image', 'kernel_package', 'dtb', 'initramfs',
                'rootfs_image', 'i2c_qcom_cci'):
        entry = document[key]
        entry['path'] = _remap(entry['path'], root)
    document['rootfs_initramfs'] = {
        'path': str(candidate_initramfs),
        'sha256': sha256(candidate_initramfs),
        'size': candidate_initramfs.stat().st_size,
    }
    document['profile'] = 'plasma-mobile'
    document['ui'] = 'plasma-mobile'
    # The fixture models the restored rc2 candidate: rear1 kernel, rc2 label.
    document['release_candidate'] = 'nura-plasma-rc2'
    document['copied_from'] = 'build/rear-camera/boot.img'
    document['source_boot_sha256'] = sha256(root / 'build/rear-camera/boot.img')
    manifest_path.write_text(json.dumps(document, indent=2) + '\n')
    return document


def promote_rc3_boot_fixture(checkout):
    """Rewrite the synthetic fixture as a built rc3 candidate."""
    root = checkout.root
    release = '7.0.0-sm8150-rc3'
    # setUp already promoted the rc2 fixture; rebuild it cleanly as rc3.
    for path in (root / 'build/plasma-mobile', root / '.work/plasma-mobile'):
        if path.exists():
            shutil.rmtree(path)
    document = promote_boot_fixture(checkout)
    pbuild = root / 'build/plasma-mobile'
    kernel = pbuild / 'kernel-package'
    legacy_modules = kernel / 'usr/lib/modules/7.0.0-sm8150-rear1'
    rc3_modules = kernel / 'usr/lib/modules' / release
    legacy_modules.rename(rc3_modules)
    (kernel / 'usr/share/kernel/postmarketos-qcom-sm8150/kernel.release').write_text(
        release + '\n')
    image = b'RC3-IMAGE-FIXTURE' + b'\x07' * 3000
    (pbuild / 'Image').write_bytes(image)
    (kernel / 'boot/vmlinuz').write_bytes(gzip.compress(image))
    pstate = root / '.work/plasma-mobile'
    initramfs_path = pstate / 'rootfs/export/initramfs'
    dtb = (kernel / 'boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb').read_bytes()
    def page(data):
        return data + b'\x00' * (-len(data) % 4096)

    boot = page(b'\x00' * 4096 + dtb) + initramfs_path.read_bytes()
    (pbuild / 'boot.img').write_bytes(boot)
    kernel_build = root / 'build/rc3-kernel'
    kernel_build.mkdir(parents=True, exist_ok=True)
    (kernel_build / 'boot.img').write_bytes(boot)
    (kernel_build / 'Image').write_bytes(image)

    def pin(path):
        return {'path': str(path), 'sha256': sha256(path), 'size': path.stat().st_size}

    module = rc3_modules / 'kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst'
    delivery = {
        'release': release,
        'worktree': str(root),
        'created_at': '2026-11-12T00:00:00Z',
        'git_head': '',
        'kernel_recipe': 'device/testing/rc3/linux-postmarketos-qcom-sm8150',
        'kernel_inputs_sha256': 'a' * 64,
        'boot_img': pin(kernel_build / 'boot.img'),
        'image': pin(kernel_build / 'Image'),
        'kernel_package': pin(kernel / 'boot/vmlinuz'),
        'dtb': pin(kernel / 'boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb'),
        'initramfs': pin(initramfs_path),
        'rootfs_image': pin(
            pstate / 'pmbootstrap-work/chroot_native/home/pmos/rootfs/oneplus-guacamole.img'),
        'i2c_qcom_cci': pin(module),
        'module_payload_sha256': 'b' * 64,
        'module_count': 1,
    }
    (kernel_build / 'DELIVERY.json').write_text(json.dumps(delivery, indent=2) + '\n')

    document.update({
        'release': release,
        'kernel_release': release,
        'release_candidate': 'nura-plasma-rc3',
        'artifact_name': 'nura-plasma-rc3-oneplus-guacamole',
        'kernel_build': str(kernel_build),
        'kernel_inputs_sha256': 'a' * 64,
        'kernel_recipe': 'device/testing/rc3/linux-postmarketos-qcom-sm8150',
        'boot_img': pin(pbuild / 'boot.img'),
        'image': pin(pbuild / 'Image'),
        'kernel_package': pin(kernel / 'boot/vmlinuz'),
        'dtb': pin(kernel / 'boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb'),
        'initramfs': pin(initramfs_path),
        'rootfs_initramfs': pin(initramfs_path),
        'i2c_qcom_cci': pin(module),
        'rootfs_image': pin(
            pstate / 'pmbootstrap-work/chroot_native/home/pmos/rootfs/oneplus-guacamole.img'),
        'copied_from': 'build/rc3-kernel/boot.img',
        'source_boot_sha256': sha256(kernel_build / 'boot.img'),
    })
    (pbuild / 'DELIVERY.json').write_text(json.dumps(document, indent=2) + '\n')
    return document


class PlasmaBootTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.checkout = BootCheckout(self.temporary.name)
        self.plasma_script = self.checkout.root / 'scripts/plasma_boot.sh'
        self.document = promote_boot_fixture(self.checkout)

    def run_plasma(self, *arguments, **overrides):
        return self.checkout.run(*arguments, script=str(self.plasma_script), **overrides)

    def test_plasma_status_uses_isolated_paths_and_known_good_boot(self):
        result = self.run_plasma('status', '--no-server-check')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r'profile\s+plasma-mobile')
        self.assertIn('known-good temporary boot', result.stdout)
        self.assertIn('build/plasma-mobile/DELIVERY.json', result.stdout)
        self.assertNotIn('build/rear-camera/DELIVERY.json', result.stdout)

    def test_rootfs_initramfs_differs_but_boot_initramfs_still_pinned(self):
        self.assertNotEqual(self.document['rootfs_initramfs']['sha256'],
                            self.document['initramfs']['sha256'])
        result = self.run_plasma('status', '--no-server-check')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('initramfs', result.stdout)
        self.assertIn('embedded_at', result.stdout)

    def test_tampered_boot_image_is_rejected(self):
        boot = self.checkout.root / 'build/plasma-mobile/boot.img'
        boot.write_bytes(boot.read_bytes() + b'tampered')
        document = self.document
        document['boot_img']['sha256'] = sha256(boot)
        document['boot_img']['size'] = boot.stat().st_size
        document.pop('copied_from', None)
        document.pop('source_boot_sha256', None)
        (self.checkout.root / 'build/plasma-mobile/DELIVERY.json').write_text(
            json.dumps(document))
        result = self.run_plasma('status', '--no-server-check')
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn('is not the known-good temporary boot', result.stderr)

    def test_missing_profile_is_rejected(self):
        document = self.document
        document.pop('profile')
        document.pop('ui')
        (self.checkout.root / 'build/plasma-mobile/DELIVERY.json').write_text(
            json.dumps(document))
        result = self.run_plasma('status', '--no-server-check')
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn('does not record profile/ui', result.stderr)

    def test_wrong_profile_is_rejected(self):
        document = self.document
        document['profile'] = 'rear-camera'
        document['ui'] = 'rear-camera'
        (self.checkout.root / 'build/plasma-mobile/DELIVERY.json').write_text(
            json.dumps(document))
        result = self.run_plasma('status', '--no-server-check')
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("!= 'plasma-mobile'", result.stderr)

    def test_dry_run_uses_plasma_state_and_never_flashes(self):
        result = self.run_plasma('boot', '--dry-run',
                                 ADB_DEVICES='', FASTBOOT_DEVICES='ABC123\tfastboot',
                                 SS_LISTEN='')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('plasma_serve.sh', result.stderr)
        self.assertIn('scripts/ssh_phone.sh', result.stdout)
        self.assertIn('build/plasma-mobile/boot.img', result.stdout)
        calls = self.checkout.calls()
        self.assertNotIn(' boot ', calls)
        self.assertNotIn('flash', calls)

    def test_successful_boot_writes_only_plasma_last_boot(self):
        result = self.run_plasma('boot', '--no-server-check',
                                 ADB_DEVICES='', FASTBOOT_DEVICES='ABC123\tfastboot')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.checkout.root / 'build/plasma-mobile/last-boot.json').is_file())
        self.assertFalse((self.checkout.root / 'build/rear-camera/last-boot.json').exists())
        record = json.loads(
            (self.checkout.root / 'build/plasma-mobile/last-boot.json').read_text())
        self.assertEqual(record['result'], 'success')
        self.assertIn('build/plasma-mobile/logs', record['log'])

    def test_rc3_boot_image_is_traced_to_the_rc3_kernel_build(self):
        document = promote_rc3_boot_fixture(self.checkout)
        result = self.run_plasma('status', '--no-server-check')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('kernel release 7.0.0-sm8150-rc3', result.stdout)
        self.assertIn('manifest release candidate nura-plasma-rc3', result.stdout)
        self.assertIn('rc3 kernel build', result.stdout)
        self.assertNotIn('known-good temporary boot', result.stdout)

    def test_rc3_boot_image_must_match_the_kernel_build_boot(self):
        promote_rc3_boot_fixture(self.checkout)
        kernel_manifest = self.checkout.root / 'build/rc3-kernel/DELIVERY.json'
        document = json.loads(kernel_manifest.read_text())
        document['boot_img']['sha256'] = '0' * 64
        kernel_manifest.write_text(json.dumps(document))
        result = self.run_plasma('status', '--no-server-check')
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn('not traceable to the rc3 kernel build', result.stderr)

    def test_rc3_manifest_must_record_the_kernel_build(self):
        promote_rc3_boot_fixture(self.checkout)
        manifest = self.checkout.root / 'build/plasma-mobile/DELIVERY.json'
        document = json.loads(manifest.read_text())
        document.pop('kernel_build')
        manifest.write_text(json.dumps(document))
        result = self.run_plasma('status', '--no-server-check')
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn('no usable kernel_build directory', result.stderr)

    def test_wrapper_refuses_a_profile_override(self):
        result = self.run_plasma('status', '--profile', 'rear-camera')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('profile is fixed to plasma-mobile', result.stderr)


if __name__ == '__main__':
    unittest.main()
