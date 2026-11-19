"""Rear-only source, graph, build and packaging regressions; no hardware actions."""
import gzip
import hashlib
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import test_sm8150_7_0 as baseline
from test_initramfs_release import make_archive

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import check_rear_camera_package as package
from check_initramfs_release import InitramfsError

RECIPE = ROOT / 'device/testing/rear-camera'
SRC = ROOT / '.work/rear-camera/linux-v7.0.0-sm8150'
STAGE = ROOT / 'build/rear-camera/kernel-package'
DTBS = STAGE / 'boot/dtbs/qcom'
DTB = DTBS / 'sm8150-oneplus-guacamole.dtb'
ENV = {**os.environ, 'LD_LIBRARY_PATH': str(ROOT / 'local/lib')}


def tree(path):
    text = subprocess.check_output([str(ROOT/'local/bin/dtc'), '-I', 'dtb', '-O', 'dts', str(path)],
                                   env=ENV, stderr=subprocess.DEVNULL, text=True)
    nodes = {}; stack = []
    for line in text.splitlines():
        line = line.strip()
        if line.endswith(' {'):
            stack.append(line[:-2]); nodes['/'.join(stack).replace('//', '/')] = {}
        elif line == '};':
            stack.pop()
        elif stack and line.endswith(';'):
            key, _, value = line[:-1].partition(' = ')
            nodes['/'.join(stack).replace('//', '/')][key] = value
    return nodes


def cells(value):
    return [int(x, 16) for x in re.findall(r'0x[0-9a-f]+', value)]


class RearSources(unittest.TestCase):
    def test_recipe_maintainers_pass_abuild_format(self):
        for recipe in RECIPE.glob('*/APKBUILD'):
            maintainer = subprocess.check_output(
                ['sh', '-c', '. "$1"; printf "%s" "$maintainer"', 'sh', str(recipe)], text=True)
            self.assertRegex(maintainer, r'^.+ <[^ <>@]+@[^ <>@]+[.][^ <>@]+>$')

    def test_recipe_checksums_and_source_lists(self):
        for recipe in RECIPE.iterdir():
            if not recipe.is_dir(): continue
            result = subprocess.check_output(['sh', '-c', '. "$1"; printf "%s\\n" $source',
                                              'sh', str(recipe/'APKBUILD')], text=True)
            names = [x.split('::')[0].split('/')[-1] for x in result.splitlines()]
            sums = dict((name, h) for h, name in re.findall(r'^([a-f0-9]{128})  (\S+)$',
                                                          (recipe/'APKBUILD').read_text(), re.M))
            self.assertEqual(names, list(sums), "abuild checks sums in source order")
            for name in names:
                p = recipe/name if not name.endswith('.tar.gz') else ROOT/'.work/rear-camera'/name
                if not p.exists(): self.skipTest(f'archive not staged: {p}')
                self.assertEqual(hashlib.sha512(p.read_bytes()).hexdigest(), sums[name], name)

    def test_baseline_patch_bytes_preserved(self):
        old = ROOT/'device/testing/7.0/linux-postmarketos-qcom-sm8150'
        for p in old.glob('0*.patch'):
            self.assertEqual(p.read_bytes(), (RECIPE/'linux-postmarketos-qcom-sm8150'/p.name).read_bytes())

    def test_only_camera_config_changes(self):
        name = 'config-postmarketos-qcom-sm8150.aarch64'
        a = baseline.config_values(ROOT/'device/testing/7.0/linux-postmarketos-qcom-sm8150'/name)
        b = baseline.config_values(RECIPE/'linux-postmarketos-qcom-sm8150'/name)
        changed = {k for k in a.keys() | b.keys() if a.get(k) != b.get(k)}
        expected = {'CONFIG_LOCALVERSION', 'CONFIG_SM_CAMCC_8150', 'CONFIG_VIDEO_IMX481',
                    'CONFIG_VIDEO_IMX586', 'CONFIG_VIDEO_S5K3M5', 'CONFIG_VIDEO_LC898217XC',
                    'CONFIG_VIDEO_AK7375', 'CONFIG_V4L2_CCI', 'CONFIG_V4L2_CCI_I2C'}
        self.assertEqual(changed, expected)
        self.assertEqual(b['CONFIG_LOCALVERSION'], '"-sm8150-rear1"')
        self.assertEqual(b['CONFIG_SM_CAMCC_8150'], 'y')
        for k, v in baseline.ConfigTests.REQUIRED.items():
            if k != 'CONFIG_LOCALVERSION': self.assertEqual(b.get(k), v, k)

    def test_cci_reset_completion_initialized_without_master_zero_child(self):
        text=(SRC/'drivers/i2c/busses/i2c-qcom-cci.c').read_text()
        probe=text.split('static int cci_probe(')[1]
        before_children=probe.split('for_each_available_child_of_node')[0]
        self.assertIn('i < cci->data->num_masters', before_children)
        self.assertIn('init_completion(&cci->master[i].irq_complete)', before_children)

    def test_camss_bridge_clock_both_vfes_and_power_order(self):
        if not SRC.exists(): self.skipTest('patched source not staged')
        s=(SRC/'drivers/media/platform/qcom/camss/camss.c').read_text()
        block=s.split('vfe_res_8150[] = {')[1].split('\n};')[0]
        self.assertEqual(block.count('"gcc_camera_axi"'), 2)
        self.assertIn('DMA_BIT_MASK(31)', s)
        self.assertIn('cam_hf_0_camnoc', s)
        for sensor in ['imx586','imx481']:
            text=(SRC/f'drivers/media/i2c/{sensor}.c').read_text().split(f'static int {sensor}_power_on')[1].split('\n}\n')[0]
            self.assertLess(text.index('regulator_enable('+sensor+'->vana)'), text.index('regulator_enable('+sensor+'->vdig)'))
        text=(SRC/'drivers/media/i2c/s5k3m5.c').read_text()
        self.assertRegex(text, r'"afvdd".*\n.*"vdda".*\n.*"vddd".*\n.*"vddio"')
        self.assertIn('3300000, 3320000', text)
        self.assertIn('release_voltage:', text)

    def test_no_front_or_runtime_hold_in_candidate(self):
        for p in (RECIPE/'libcamera-rear').glob('*.patch'):
            self.assertNotIn('IMX471', p.name)
        text=(RECIPE/'linux-postmarketos-qcom-sm8150/0030-arm64-dts-guacamole-three-rear-cameras.patch').read_text()
        self.assertNotIn('ldo3 {', text)
        self.assertNotIn('imx471', text)
        self.assertNotIn('CAMERA_FRONT', text)
        self.assertNotIn('rpmhpd_hold', (RECIPE/'linux-postmarketos-qcom-sm8150/APKBUILD').read_text())
        apk=(RECIPE/'linux-postmarketos-qcom-sm8150/APKBUILD').read_text()
        self.assertIn('sm8150-oneplus-guacamole*.dtb',apk)
        self.assertNotIn('make modules_install dtbs_install',apk)
        lib=(RECIPE/'libcamera-rear/APKBUILD').read_text()
        self.assertIn('--prefix=/opt/nura-rear-camera', lib)
        for directory in ['libdir=lib', 'bindir=bin', 'libexecdir=libexec',
                          'includedir=include', 'datadir=share', 'sysconfdir=etc']:
            self.assertIn('--'+directory, lib)
        self.assertIn('depends="bash"', lib)
        self.assertIn('-Dsoftisp-gpu=enabled', lib)
        self.assertIn('!strip', lib)
        options = subprocess.check_output(
            ['sh', '-c', '. "$1"; printf "%s" "$options"', 'sh',
             str(RECIPE/'libcamera-rear/APKBUILD')], text=True).split()
        self.assertIn('!fhs', options, 'private /opt prefix requires abuild FHS exception')
        self.assertIn('ldpath="/opt/nura-rear-camera/lib"', lib)
        self.assertNotIn('conflicts=', lib)
        self.assertNotIn('provides=', lib)


@unittest.skipUnless(DTB.exists(), 'camera DTBs not built')
class RearGraph(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes=tree(DTB)
        cls.phandles={cells(p['phandle'])[0]: n for n,p in cls.nodes.items() if 'phandle' in p}

    def available(self, node):
        while node:
            self.assertIn(node, self.nodes)
            self.assertIn(self.nodes[node].get('status', '"okay"'), ('"okay"','"ok"'), node)
            node=node.rsplit('/',1)[0]

    def test_three_reciprocal_graphs_and_suppliers(self):
        self.available('/soc@0/clock-controller@ad00000')
        camss='/soc@0/camss@acb3000'
        self.available(camss)
        slots=[('imx586','ac4a000',1,'1a',1,1,3),('s5k3m5','ac4a000',0,'10',0,4,4),('imx481','ac4b000',1,'1a',3,4,4)]
        for sensor,cci,bus,addr,phy,bustype,lanes in slots:
            node=f'/soc@0/cci@{cci}/i2c-bus@{bus}/camera@{addr}'
            self.available(node)
            ep=node+'/port/endpoint'; remote=camss+f'/ports/port@{phy}/endpoint'
            self.assertEqual(self.phandles[cells(self.nodes[ep]['remote-endpoint'])[0]],remote)
            self.assertEqual(self.phandles[cells(self.nodes[remote]['remote-endpoint'])[0]],ep)
            self.assertEqual(cells(self.nodes[ep]['bus-type']),[bustype])
            self.assertEqual(len(cells(self.nodes[ep]['data-lanes'])),lanes)
            for prop,value in self.nodes[node].items():
                if prop.endswith('-supply'):
                    self.available(self.phandles[cells(value)[0]])
            if sensor in ['imx586','s5k3m5']:
                self.assertNotIn('lens-focus',self.nodes[node])
            else:
                self.available(self.phandles[cells(self.nodes[node]['lens-focus'])[0]])
        for lens in ['/soc@0/cci@ac4a000/i2c-bus@1/lens@72','/soc@0/cci@ac4a000/i2c-bus@0/lens@74']:
            self.assertEqual(self.nodes[lens]['status'],'"disabled"')
        self.assertNotIn(camss+'/ports/port@2',self.nodes)
        self.assertNotIn('/soc@0/cci@ac4b000/i2c-bus@0',self.nodes)
        self.assertFalse(any('imx471' in p.get('compatible','') for p in self.nodes.values()))

    def test_all_camera_providers_are_enabled(self):
        for node in ['/soc@0/cci@ac4a000', '/soc@0/cci@ac4b000', '/soc@0/camss@acb3000']:
            for prop, countprop in [('clocks', '#clock-cells'), ('power-domains', '#power-domain-cells'),
                                    ('interconnects', '#interconnect-cells'), ('iommus', '#iommu-cells')]:
                values=cells(self.nodes[node].get(prop,''))
                while values:
                    provider=self.phandles[values[0]]
                    self.available(provider)
                    self.assertIn(countprop,self.nodes[provider])
                    count=cells(self.nodes[provider][countprop])[0]
                    values=values[1+count:]

    def test_single_sensor_variants_have_one_graph(self):
        for slot,sensor in [('main','sony,imx586'),('tele','samsung,s5k3m5'),('wide','sony,imx481')]:
            nodes=tree(DTBS/f'sm8150-oneplus-guacamole-rear-{slot}.dtb')
            found=[p['compatible'].strip('"') for p in nodes.values() if p.get('compatible') in ['"sony,imx586"','"sony,imx481"','"samsung,s5k3m5"']]
            self.assertEqual(found,[sensor])
            self.assertEqual(len([n for n in nodes if n.startswith('/soc@0/camss@acb3000/ports/port@') and n.endswith('/endpoint')]),1)

    def test_speakers_and_supplies_have_enabled_ancestors(self):
        for addr in ['34','35']:
            self.available(f'/soc@0/geniqup@8c0000/i2c@890000/audio-codec@{addr}')


class RearPreservedBaselineDtb(baseline.DeviceTreeTests):
    def setUp(self):
        with mock.patch.object(baseline,'DTB',DTB): super().setUp()


class RearArtifacts(unittest.TestCase):
    def test_built_modules_all_match_distinct_release(self):
        modules=STAGE/'usr/lib/modules'/package.RELEASE
        if not modules.is_dir(): self.skipTest('modules not staged')
        self.assertEqual((STAGE/'usr/share/kernel/postmarketos-qcom-sm8150/kernel.release').read_text().strip(),package.RELEASE)
        for name in ['imx586','imx481','s5k3m5','lc898217xc','ak7375','qcom-camss','i2c-qcom-cci','v4l2-cci']:
            paths=list(modules.rglob(name+'.ko.zst'));self.assertEqual(len(paths),1,name)
            self.assertEqual(subprocess.check_output(['modinfo','-F','vermagic',str(paths[0])],text=True).split()[0],package.RELEASE)
        self.assertGreater(len(list(modules.rglob('*.ko.zst'))),800)

    def test_musl_libcamera_and_signed_ipa(self):
        prefix=ROOT/'build/rear-camera/libcamera-stage/opt/nura-rear-camera'
        if not prefix.exists(): self.skipTest('libcamera not staged')
        for p in [prefix/'bin/cam',prefix/'lib/libcamera.so.0.7.2',prefix/'lib/libcamera/ipa/ipa_soft_simple.so']:
            data=p.read_bytes();self.assertEqual(data[:4],b'\x7fELF');self.assertEqual(struct.unpack_from('<H',data,18)[0],183)
        dynamic=subprocess.check_output(['llvm-readelf','--program-headers',str(prefix/'bin/cam')],text=True)
        self.assertIn('/lib/ld-musl-aarch64.so.1',dynamic)
        p=prefix/'lib/libcamera/ipa/ipa_soft_simple.so'
        subprocess.run(['openssl','dgst','-sha256','-verify',str(ROOT/'build/rear-camera/ipa-public.pem'),'-signature',str(p)+'.sign',str(p)],check=True,capture_output=True)


class RearInitramfs(unittest.TestCase):
    def archive(self,directory,port,release=package.RELEASE):
        root=directory/'root';(root/'hooks').mkdir(parents=True)
        (root/'hooks/netboot.sh').write_text(f'NBD_PORT={port}\n')
        return make_archive(directory,[release])

    def test_matching_hook_and_release(self):
        with tempfile.TemporaryDirectory() as t:
            package.check_initramfs(self.archive(Path(t),10001))

    def test_baseline_hook_and_mismatched_release_rejected(self):
        for port,release in [(10000,package.RELEASE),(10001,'7.0.0-sm8150')]:
            with self.subTest(port=port,release=release),tempfile.TemporaryDirectory() as t:
                with self.assertRaises((ValueError,InitramfsError)):
                    package.check_initramfs(self.archive(Path(t),port,release))

    def test_packaging_scripts_syntax(self):
        for name in ['build_rear_camera_kernel','build_rear_camera_libcamera','rootfs_prepare_rear_camera','build_bootimg_rear_camera','serve_rear_camera','boot_rear_camera','collect_rear_camera','cam_rear']:
            path=ROOT/f'scripts/{name}.sh'
            if not path.exists():  # superseded launchers live in archive/scripts
                path=ROOT/f'archive/scripts/{name}.sh'
            subprocess.run(['bash','-n',str(path)],check=True)
        rootfs=(ROOT/'scripts/rootfs_prepare_rear_camera.sh').read_text()
        self.assertIn('7.0.0_p*-r0',rootfs)
        self.assertNotIn('.work/7.0',rootfs)
        serve=(ROOT/'scripts/serve_rear_camera.sh').read_text()
        self.assertIn('--port 10001',serve)
        self.assertIn('--replace',serve)
        boot=(ROOT/'scripts/build_bootimg_rear_camera.sh').read_text()
        self.assertIn('check_boot_envelope.py',boot)
        self.assertIn('check_rear_camera_package.py',boot)
