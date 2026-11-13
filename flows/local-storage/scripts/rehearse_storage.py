#!/usr/bin/env python3
"""USER RUN: test 4096-byte-sector GPT/loop/ext4 growth on a disposable host file."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

FLOW = Path(__file__).resolve().parents[1]
TOOLS = ('losetup', 'blockdev', 'parted', 'partprobe', 'mkfs.ext2', 'mkfs.ext4',
         'e2fsck', 'resize2fs', 'dumpe2fs', 'blkid', 'udevadm')


def run(*args):
    return subprocess.run(list(args), text=True, capture_output=True, check=True).stdout.strip()


def owned_loop(loop, image):
    if not re.fullmatch(r'/dev/loop[0-9]+', loop):
        raise RuntimeError('refusing a non-loop device')
    attached = run('losetup', '--noheadings', '--output', 'NAME', '--associated', str(image)).splitlines()
    if attached != [loop]:
        raise RuntimeError('loop backing-file identity is not exclusive; stopping')


def filesystem_size(device):
    fields = {}
    for line in run('dumpe2fs', '-h', device).splitlines():
        if ':' in line:
            key, value = line.split(':', 1)
            fields[key] = value.strip()
    return int(fields['Block count']) * int(fields['Block size'])


def rehearse(directory):
    image = directory / 'disposable.img'
    if image.exists():
        raise RuntimeError('refusing to overwrite an existing rehearsal image')
    with image.open('xb') as stream:
        stream.truncate(128 * 1024**2)
    loop = None
    result = None
    try:
        loop = run('losetup', '--find', '--show', '--partscan', '--sector-size', '4096', str(image))
        owned_loop(loop, image)
        if run('blockdev', '--getss', loop) != '4096':
            raise RuntimeError('loop sector size is not 4096')
        run('parted', '-s', loop, 'mklabel', 'gpt')
        run('parted', '-s', loop, 'mkpart', 'primary', '8MiB', '32MiB')
        run('parted', '-s', loop, 'mkpart', 'primary', '32MiB', '120MiB')
        run('partprobe', loop)
        run('udevadm', 'settle', '--timeout=10')
        boot, root = loop + 'p1', loop + 'p2'
        run('mkfs.ext2', '-F', '-b', '4096', '-L', 'pmOS_boot', boot)
        run('mkfs.ext4', '-F', '-b', '4096', '-L', 'pmOS_root', root)
        before = filesystem_size(root)
        uuid = run('blkid', '-s', 'UUID', '-o', 'value', root)
        owned_loop(loop, image)
        run('losetup', '--detach', loop)
        loop = None
        with image.open('r+b') as stream:
            stream.truncate(256 * 1024**2)
        loop = run('losetup', '--find', '--show', '--partscan', '--sector-size', '4096', str(image))
        owned_loop(loop, image)
        if run('blockdev', '--getss', loop) != '4096':
            raise RuntimeError('reattached loop sector size is not 4096')
        root = loop + 'p2'
        # Same GPT growth operation used by the pinned initramfs.
        run('parted', '-f', '-s', loop, 'resizepart', '2', '100%')
        run('partprobe', loop)
        run('udevadm', 'settle', '--timeout=10')
        run('e2fsck', '-f', '-n', root)
        run('resize2fs', root)
        run('e2fsck', '-f', '-n', root)
        after = filesystem_size(root)
        if after <= before or run('blkid', '-s', 'UUID', '-o', 'value', root) != uuid:
            raise RuntimeError('ext4 did not grow or its identity changed')
        result = {'status': 'PASS', 'sector_size': 4096, 'filesystem_before': before,
                  'filesystem_after': after, 'image': str(image), 'phone_tested': False,
                  'limit': 'Host tools only; target BusyBox discovery and phone UFS remain untested.'}
    finally:
        if loop is not None:
            owned_loop(loop, image)
            run('losetup', '--detach', loop)
    (directory / 'RESULT.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='requires root; disposable host image only')
    args = parser.parse_args()
    missing = [tool for tool in TOOLS if shutil.which(tool) is None]
    if missing:
        parser.error('missing host tools: ' + ', '.join(missing))
    if not args.run:
        print('Ready for user-run rehearsal; no loop/image was created.')
        print('Run: sudo python3 ' + str(Path(__file__).resolve()) + ' --run')
        return
    if os.geteuid() != 0:
        parser.error('--run needs root; run the printed sudo command yourself')
    parent = FLOW / '.work/storage-rehearsal'
    if parent.resolve() != parent or parent.is_symlink():
        parser.error('rehearsal directory resolves through a symlink')
    parent.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='trial-', dir=parent))
    directory.chmod(0o755)  # Public synthetic test evidence, readable without sudo.
    try:
        print(json.dumps(rehearse(directory), indent=2))
    except Exception as error:
        raise SystemExit(f'Rehearsal failed: {error}. Evidence retained in {directory}')


if __name__ == '__main__':
    main()
