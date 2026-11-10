#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Pin genuine rear1 initramfs/rootfs inputs before rootless boot-image packing."""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

from check_initramfs_release import (GZIP_MAGIC, XZ_MAGIC, ZSTD_MAGIC,
                                     _decompress, check_release, list_archive)

ROOT = Path(__file__).resolve().parents[1]
RELEASE = '7.0.0-sm8150-rear1'
MANIFEST = ROOT / 'build/rear-camera/packaging-inputs.json'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def check_initramfs(path):
    check_release(path, RELEASE)
    entries = [name for name in list_archive(path) if name.endswith('/netboot.sh')]
    if len(entries) != 1:
        raise ValueError('initramfs must contain exactly one netboot.sh hook')
    data = path.read_bytes()
    for magic, tool in [(GZIP_MAGIC, 'gzip'), (XZ_MAGIC, 'xz'), (ZSTD_MAGIC, 'zstd')]:
        if data.startswith(magic):
            data = _decompress(path, tool, ['-dc'])
            break
    hook = subprocess.run(['cpio', '-i', '--to-stdout', '--quiet', entries[0]],
                          input=data, capture_output=True, check=True).stdout
    if not re.search(rb'^NBD_PORT=10001$', hook, re.M):
        raise ValueError('initramfs netboot hook is not port 10001')


def check_inputs(initramfs, kernel_dir, rootfs=None):
    check_initramfs(initramfs)
    release = kernel_dir / 'usr/share/kernel/postmarketos-qcom-sm8150/kernel.release'
    if release.read_text().strip() != RELEASE:
        raise ValueError('staged kernel release mismatch')
    identity = {'release': RELEASE, 'nbd_port': 10001,
                'initramfs_sha256': digest(initramfs),
                'vmlinuz_sha256': digest(kernel_dir / 'boot/vmlinuz')}
    if rootfs is None:
        recorded = json.loads(MANIFEST.read_text())
        if any(recorded.get(k) != v for k, v in identity.items()):
            raise ValueError('inputs differ from the verified candidate rootfs')
        return identity
    if digest(rootfs / 'boot/vmlinuz') != identity['vmlinuz_sha256']:
        raise ValueError('rootfs kernel differs from staging')
    if (rootfs / 'usr/share/kernel/postmarketos-qcom-sm8150/kernel.release').read_text().strip() != RELEASE:
        raise ValueError('rootfs release mismatch')
    dtb = Path('boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb')
    if digest(rootfs / dtb) != digest(kernel_dir / dtb):
        raise ValueError('rootfs DTB differs from staging')
    modules = rootfs / 'usr/lib/modules'
    if sorted(p.name for p in modules.iterdir() if p.is_dir()) != [RELEASE]:
        raise ValueError('rootfs must contain only rear1 modules')
    for name in ['imx586', 'imx481', 's5k3m5', 'lc898217xc', 'ak7375', 'qcom-camss', 'i2c-qcom-cci']:
        matches = list(modules.rglob(name + '.ko*'))
        if len(matches) != 1:
            raise ValueError(f'rootfs needs exactly one {name} module')
        magic = subprocess.check_output(['modinfo', '-F', 'vermagic', str(matches[0])], text=True)
        if magic.split()[0] != RELEASE:
            raise ValueError(f'wrong vermagic: {matches[0]}')
    hook = rootfs / 'usr/share/mkinitfs/hooks/netboot.sh'
    if not re.search(r'^NBD_PORT=10001$', hook.read_text(), re.M):
        raise ValueError('rootfs netboot hook is not port 10001')
    identity['rootfs'] = str(rootfs.resolve())
    MANIFEST.write_text(json.dumps(identity, indent=2) + '\n')
    return identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--initramfs', type=Path, required=True)
    parser.add_argument('--kernel-dir', type=Path, default=ROOT / 'build/rear-camera/kernel-package')
    parser.add_argument('--record-rootfs', type=Path)
    args = parser.parse_args()
    print(json.dumps(check_inputs(args.initramfs, args.kernel_dir, args.record_rootfs), indent=2))


if __name__ == '__main__':
    main()
