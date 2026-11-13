#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Pin genuine kernel/initramfs/rootfs inputs before rootless boot-image packing.

``RELEASE`` defaults to the historical rear1 uname so the original rear-camera
flow is unchanged.  A future candidate with a different kernel release passes
``--release`` (or exports ``NURA_KERNEL_RELEASE``); every module-tree, vermagic
and initramfs comparison uses that one value.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from check_initramfs_release import (GZIP_MAGIC, XZ_MAGIC, ZSTD_MAGIC,
                                     _decompress, check_release, list_archive)

ROOT = Path(__file__).resolve().parents[1]
RELEASE = '7.0.0-sm8150-rear1'
MANIFEST = ROOT / 'build/rear-camera/packaging-inputs.json'


def release_name(value=None):
    """Return an explicit release or the historical rear1 default."""
    if value is None:
        value = os.environ.get('NURA_KERNEL_RELEASE', RELEASE)
    if not isinstance(value, str) or not re.fullmatch(r'[0-9A-Za-z._+-]+', value):
        raise ValueError(f'invalid kernel release name: {value!r}')
    return value

# Every module file in the APK staging tree must be present byte-for-byte in
# the installed rootfs.  Building both from the same release string is not
# enough: touch, FastRPC, SLIMbus and the camera drivers are modules, while the
# SMB5, SLIMbus core and display changes are built into vmlinuz.
MODULE_COMPRESSION = ((GZIP_MAGIC, 'gzip'), (XZ_MAGIC, 'xz'), (ZSTD_MAGIC, 'zstd'))
CAMERA_MODULES = ('imx586', 'imx481', 's5k3m5', 'lc898217xc', 'ak7375',
                  'qcom-camss', 'i2c-qcom-cci')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def check_initramfs(path, release=None):
    check_release(path, release_name(release))
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


def _module_key(relative):
    """Return the path up to and including .ko, ignoring a compression suffix."""
    relative = Path(relative).as_posix()
    for suffix in ('.gz', '.xz', '.zst'):
        if relative.endswith('.ko' + suffix):
            return relative[:-len(suffix)]
    if relative.endswith('.ko'):
        return relative
    return None


def _module_entries(modules_root):
    modules_root = Path(modules_root)
    if not modules_root.is_dir():
        raise ValueError(f'missing module tree: {modules_root}')
    entries = {}
    for path in sorted(modules_root.rglob('*')):
        if not path.is_file():
            continue
        key = _module_key(path.relative_to(modules_root))
        if key is None:
            continue
        if key in entries:
            raise ValueError(f'duplicate module payload for {key}')
        entries[key] = path
    if not entries:
        raise ValueError(f'no kernel modules under {modules_root}')
    return entries


def _module_data(path, modules_root):
    path = Path(path)
    if path.is_symlink():
        target = path.resolve(strict=True)
        root = Path(modules_root).resolve()
        if target != root and root not in target.parents:
            raise ValueError(f'module symlink escapes the module tree: {path} -> {target}')
        path = target
    return path.read_bytes()


def _module_digest(path, modules_root, decompress=False):
    data = _module_data(path, modules_root)
    if decompress:
        for magic, tool in MODULE_COMPRESSION:
            if data.startswith(magic):
                return hashlib.sha256(_decompress(Path(path), tool, ['-dc'])).hexdigest()
    return hashlib.sha256(data).hexdigest()


def module_payload(kernel_dir, release=None):
    """Identity of the complete staged module payload, not just selected names."""
    modules_root = Path(kernel_dir) / 'usr/lib/modules' / release_name(release)
    entries = _module_entries(modules_root)
    identity = hashlib.sha256()
    for key in sorted(entries):
        identity.update(key.encode() + b'\0')
        identity.update(_module_digest(entries[key], modules_root).encode() + b'\n')
    return {'sha256': identity.hexdigest(), 'count': len(entries)}


def _rootfs_modules(rootfs):
    for relative in ('usr/lib/modules', 'lib/modules'):
        candidate = Path(rootfs) / relative
        if candidate.is_dir():
            return candidate
    raise ValueError('rootfs has no module tree')


def check_module_payload(kernel_dir, rootfs, release=None):
    """Compare every staged module file with the installed rootfs module tree."""
    release = release_name(release)
    staged_root = Path(kernel_dir) / 'usr/lib/modules' / release
    staged = _module_entries(staged_root)
    modules_root = _rootfs_modules(rootfs)
    releases = sorted(path.name for path in modules_root.iterdir() if path.is_dir())
    if releases != [release]:
        raise ValueError(f'rootfs must contain only {release} modules, found {releases}')
    installed_root = modules_root / release
    installed = _module_entries(installed_root)
    missing = sorted(set(staged) - set(installed))
    extra = sorted(set(installed) - set(staged))
    if missing:
        raise ValueError(f'rootfs module payload is missing {missing[0]} '
                         f'({len(missing)} staged module(s) absent)')
    if extra:
        raise ValueError(f'rootfs module payload has unexpected {extra[0]} '
                         f'({len(extra)} module(s) not in the staged kernel)')
    for key in sorted(staged):
        staged_path = staged[key]
        installed_path = installed[key]
        if staged_path.suffix == installed_path.suffix:
            same = digest(staged_path) == digest(installed_path)
        else:
            # Compression is an intentional packaging choice; compare the
            # module bytes when the suffix differs.
            same = (_module_digest(staged_path, staged_root, decompress=True) ==
                    _module_digest(installed_path, installed_root, decompress=True))
        if not same:
            raise ValueError(
                f'rootfs module payload differs: {key} '
                f'(staged {staged_path.relative_to(staged_root)}, '
                f'rootfs {installed_path.relative_to(installed_root)})')
    return {'count': len(staged), 'sha256': module_payload(kernel_dir, release)['sha256']}


def check_inputs(initramfs, kernel_dir, rootfs=None, release=None, manifest=None,
                 check_recorded=True):
    release = release_name(release)
    check_initramfs(initramfs, release)
    release_file = kernel_dir / 'usr/share/kernel/postmarketos-qcom-sm8150/kernel.release'
    if release_file.read_text().strip() != release:
        raise ValueError('staged kernel release mismatch')
    payload = module_payload(kernel_dir, release)
    identity = {'release': release, 'nbd_port': 10001,
                'initramfs_sha256': digest(initramfs),
                'vmlinuz_sha256': digest(kernel_dir / 'boot/vmlinuz'),
                'module_payload_sha256': payload['sha256'],
                'module_count': payload['count']}
    if rootfs is None:
        if not check_recorded:
            return identity
        recorded = json.loads(Path(manifest or MANIFEST).read_text())
        changed = [key for key, value in identity.items() if recorded.get(key) != value]
        if changed:
            raise ValueError('inputs differ from the verified candidate rootfs: '
                             + ', '.join(changed))
        return identity
    if digest(rootfs / 'boot/vmlinuz') != identity['vmlinuz_sha256']:
        raise ValueError('rootfs kernel differs from staging')
    if (rootfs / 'usr/share/kernel/postmarketos-qcom-sm8150/kernel.release').read_text().strip() != release:
        raise ValueError('rootfs release mismatch')
    dtb = Path('boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb')
    if digest(rootfs / dtb) != digest(kernel_dir / dtb):
        raise ValueError('rootfs DTB differs from staging')
    check_module_payload(kernel_dir, rootfs, release)
    modules = _rootfs_modules(rootfs) / release
    for name in CAMERA_MODULES:
        matches = list(modules.rglob(name + '.ko*'))
        if len(matches) != 1:
            raise ValueError(f'rootfs needs exactly one {name} module')
        magic = subprocess.check_output(['modinfo', '-F', 'vermagic', str(matches[0])], text=True)
        if magic.split()[0] != release:
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
    parser.add_argument('--release', help='kernel uname; default 7.0.0-sm8150-rear1')
    parser.add_argument('--manifest', type=Path,
                        help='recorded packaging-inputs.json for the no-rootfs comparison')
    parser.add_argument('--no-recorded-inputs', action='store_true',
                        help='do not compare against a previous packaging-inputs.json')
    args = parser.parse_args()
    print(json.dumps(check_inputs(
        args.initramfs, args.kernel_dir, args.record_rootfs, args.release,
        args.manifest, not args.no_recorded_inputs), indent=2))


if __name__ == '__main__':
    main()
