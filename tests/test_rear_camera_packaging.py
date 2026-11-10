"""Failure-path checks for the privileged preparer, using a fake pmbootstrap."""
import hashlib
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


@unittest.skipIf(os.geteuid() == 0, 'preparer refuses root')
class PreparerFailure(unittest.TestCase):
    def run_preparer(self, version=None):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)/'checkout with spaces';scripts=root/'scripts';scripts.mkdir(parents=True)
            shutil.copy2(ROOT/'scripts/rootfs_prepare_rear_camera.sh',scripts)
            state=root/'.work/rear-camera';(state/'linux-v7.0.0-sm8150').mkdir(parents=True)
            output=state/'output/include/config';output.mkdir(parents=True)
            (output/'kernel.release').write_text(package.RELEASE+'\n')
            for recipe,path in [('linux-postmarketos-qcom-sm8150','device/testing'),('postmarketos-mkinitfs-hook-netboot','main')]:
                d=root/'device/testing/rear-camera'/recipe;d.mkdir(parents=True)
                (d/'netboot.sh').write_text('NBD_PORT=10001\n')
                link=state/'pmaports'/path/recipe;link.parent.mkdir(parents=True,exist_ok=True);link.symlink_to(d)
            work=state/'pmbootstrap-work'
            packages=work/'packages/edge/aarch64'
            packages.mkdir(parents=True)
            old=packages/'linux-postmarketos-qcom-sm8150-7.0.0_p20261110114554-r0.apk'
            old.write_bytes(b'previous candidate')
            if version:
                (work/'version').write_text(version+'\n')
            else:
                (work/'version').write_text('8\n')
            binaries=root/'bin';binaries.mkdir();calls=root/'calls'
            python=binaries/'python3'
            python.write_text('#!/bin/sh\nif [ "$1" = - ]; then exec '+shlex.quote(sys.executable)+' "$@"; fi\nprintf "%s\\n" "$@" >> "$CALLS"\nexit 42\n')
            python.chmod(0o755)
            result=subprocess.run(['bash',str(scripts/'rootfs_prepare_rear_camera.sh')],cwd=t,
                                  env={**os.environ,'PATH':str(binaries)+':'+os.environ['PATH'],'CALLS':str(calls)},
                                  capture_output=True,text=True,timeout=20)
            if version == '7':
                self.assertNotEqual(result.returncode,0)
                self.assertFalse(calls.exists())
                self.assertEqual((work/'version').read_text(),'7\n')
            else:
                self.assertEqual(result.returncode,42,result.stdout+result.stderr)
                argv=calls.read_text().splitlines()
                self.assertEqual(argv[-3:],['build','--envkernel','linux-postmarketos-qcom-sm8150'])
                self.assertNotIn('install',argv)
                self.assertEqual((work/'version').read_text(),'8\n')
                self.assertTrue((state/'linux-v7.0.0-sm8150/.output').is_dir())
                self.assertFalse((state/'linux-v7.0.0-sm8150/.output').is_symlink())
                self.assertFalse(old.exists())
                self.assertEqual((state/'rootfs/previous-kernels'/old.name).read_bytes(), b'previous candidate')

    def test_packaging_failure_stops_before_install(self):self.run_preparer()
    def test_incompatible_work_directory_stops_before_pmbootstrap(self):self.run_preparer('7')


class InputIdentity(unittest.TestCase):
    def test_recorded_identity_and_changed_kernel(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);archive=rear.RearInitramfs().archive(root,10001)
            stage=root/'stage';release=stage/'usr/share/kernel/postmarketos-qcom-sm8150/kernel.release'
            release.parent.mkdir(parents=True);release.write_text(package.RELEASE)
            image=stage/'boot/vmlinuz';image.parent.mkdir();image.write_bytes(b'fixture kernel')
            manifest=root/'manifest.json'
            manifest.write_text(json.dumps({'release':package.RELEASE,'nbd_port':10001,
                'initramfs_sha256':package.digest(archive),'vmlinuz_sha256':package.digest(image)}))
            with mock.patch.object(package,'MANIFEST',manifest):
                package.check_inputs(archive,stage)
                image.write_bytes(b'different kernel')
                with self.assertRaisesRegex(ValueError,'inputs differ'):
                    package.check_inputs(archive,stage)


class KernelRecipeInstall(unittest.TestCase):
    def test_individual_dtbs_reach_apk_package(self):
        output=ROOT/'.work/rear-camera/output'
        if not (output/'arch/arm64/boot/Image.gz').exists():self.skipTest('kernel not built')
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);binaries=root/'bin';binaries.mkdir();stage=root/'package'
            make=binaries/'make'
            make.write_text('#!/bin/sh\nmkdir -p "$TEST_STAGE/usr/lib/modules/7.0.0-sm8150-rear1"\nln -s "$TEST_OUTPUT" "$TEST_STAGE/usr/lib/modules/7.0.0-sm8150-rear1/build"\n')
            make.chmod(0o755)
            recipe=ROOT/'device/testing/rear-camera/linux-postmarketos-qcom-sm8150/APKBUILD'
            subprocess.run(['sh','-c','. "$1"; builddir="$2"; pkgdir="$3"; package','sh',str(recipe),str(output),str(stage)],
                           env={**os.environ,'PATH':str(binaries)+':'+os.environ['PATH'],
                                'TEST_STAGE':str(stage),'TEST_OUTPUT':str(output)},check=True,capture_output=True)
            self.assertEqual(len(list((stage/'boot/dtbs/qcom').glob('*.dtb'))),4)
            for p in (stage/'boot/dtbs/qcom').glob('*.dtb'):
                self.assertEqual(p.read_bytes(),(output/'arch/arm64/boot/dts/qcom'/p.name).read_bytes())
            self.assertEqual(package.digest(stage/'boot/vmlinuz'),package.digest(output/'arch/arm64/boot/Image.gz'))
            self.assertEqual((stage/'usr/share/kernel/postmarketos-qcom-sm8150/kernel.release').read_text().strip(),package.RELEASE)
