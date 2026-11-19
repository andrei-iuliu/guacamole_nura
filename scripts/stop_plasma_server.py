#!/usr/bin/env python3
"""USER RUN ONLY (via sudo): stop a verified Plasma NBD server and its restart loop."""
import os
from pathlib import Path
import signal
import select
import sys
import time


def command(proc, pid):
    return (proc / str(pid) / 'cmdline').read_bytes().rstrip(b'\0').decode().split('\0')


def owners(work, port, proc=Path('/proc')):
    work = Path(work)
    if work.resolve() != work or work.name != 'pmbootstrap-work' or work.parent.name != 'plasma-mobile':
        raise ValueError('work directory is not the canonical Plasma workspace')
    inodes = set()
    for name in ('tcp', 'tcp6'):
        table = proc / 'net' / name
        if not table.exists():
            continue
        for line in table.read_text().splitlines()[1:]:
            fields = line.split()
            if fields[3] == '0A' and int(fields[1].split(':')[1], 16) == port:
                inodes.add(fields[9])
    if not inodes:
        return []
    found = set()
    targets = set()
    pmb = str(work.parent / 'pmbootstrap/pmbootstrap.py')
    for path in proc.glob('[0-9]*'):
        try:
            held = {fd.readlink().as_posix() for fd in (path / 'fd').iterdir()}
        except FileNotFoundError:
            continue
        matched = {inode for inode in inodes if f'socket:[{inode}]' in held}
        if not matched:
            continue
        pid = int(path.name)
        args = command(proc, pid)
        expected = [f'172.16.42.2@{port}', '/mnt/pmbootstrap/netboot/oneplus-guacamole.img', '-d']
        if Path(args[0]).name != 'nbd-server' or args[1:] != expected:
            raise ValueError(f'port {port} belongs to an unrelated process (PID {pid})')
        if (path / 'root').resolve() != (work / 'chroot_native').resolve():
            raise ValueError(f'PID {pid} belongs to a different chroot')
        if (path / 'exe').resolve() != (work / 'chroot_native/usr/bin/nbd-server').resolve():
            raise ValueError(f'PID {pid} is not the expected nbd-server executable')
        parent = pid
        for _ in range(16):
            stat = (proc / str(parent) / 'stat').read_text().rsplit(')', 1)[1].split()
            parent = int(stat[1])
            if parent <= 1:
                break
            args = command(proc, parent)
            if len(args) > 1 and args[1] == pmb:
                if ('-w' not in args or args[args.index('-w') + 1] != str(work)
                        or 'netboot' not in args or args[args.index('netboot') + 1] != 'serve'
                        or '--port' not in args or args[args.index('--port') + 1] != str(port)):
                    raise ValueError('pmbootstrap ancestor does not match this Plasma server')
                targets.add(parent)
                break
        else:
            raise ValueError('cannot establish the server restart-loop owner')
        # A stopped pmbootstrap can leave sudo/nbd adopted by init or systemd.
        # Its verified chroot/executable/export still identify our orphan.
        targets.add(pid)
        found.update(matched)
    if found != inodes:
        raise ValueError('cannot identify every listener on the candidate port')
    return sorted(targets)


def wait_for_exit(handles, seconds):
    pending = set(handles)
    deadline = time.monotonic() + seconds
    while pending:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        ready, _, _ = select.select(list(pending), [], [], remaining)
        pending.difference_update(ready)
    return sorted(pending)


def stop(work, port):
    if port < 1024 or port > 65535 or port in (9999, 10000):
        raise ValueError('refusing a baseline or invalid port')
    targets = owners(work, port)
    # Hold stable process references, then revalidate ownership before signalling.
    handles = []
    try:
        for pid in targets:
            handles.append(os.pidfd_open(pid))
        if owners(work, port) != targets:
            raise ValueError('server ownership changed; retry cleanup')
        for fd in handles:
            try:
                signal.pidfd_send_signal(fd, signal.SIGTERM)
            except ProcessLookupError:
                pass
        pending = wait_for_exit(handles, 5)
        if pending:
            if not set(owners(work, port)).issubset(targets):
                raise ValueError('a new listener appeared; refusing SIGKILL')
            print('Verified stale processes ignored SIGTERM; sending SIGKILL to the same pidfds.')
            for fd in pending:
                try:
                    signal.pidfd_send_signal(fd, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if wait_for_exit(pending, 2):
                raise ValueError('verified stale processes did not exit after SIGKILL')
    finally:
        for fd in handles:
            os.close(fd)
    print(f'Verified Plasma server processes exited: {targets}')


if __name__ == '__main__':
    try:
        if len(sys.argv) != 3:
            raise ValueError('expected WORK PORT')
        stop(sys.argv[1], int(sys.argv[2]))
    except (OSError, ValueError, IndexError) as error:
        sys.exit(f'FAIL  stale Plasma server: {error}')
