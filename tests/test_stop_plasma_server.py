"""Listener ownership checks against synthetic /proc; no real signals or sudo."""
import importlib.util
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'stop_plasma_server', Path(__file__).resolve().parents[1] / 'scripts/stop_plasma_server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class StaleServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.proc = root / 'proc'
        (self.proc / 'net').mkdir(parents=True)
        self.work = root / '.work/plasma-mobile/pmbootstrap-work'
        self.native = self.work / 'chroot_native'
        (self.native / 'usr/bin').mkdir(parents=True)
        (self.native / 'usr/bin/nbd-server').touch()
        (self.proc / 'net/tcp').write_text(
            'header\n0: 02002AAC:2711 00000000:0000 0A 0:0 00:0 0 0 0 77\n')
        for pid in (90, 100):
            (self.proc / str(pid) / 'fd').mkdir(parents=True)
        self.args = ['nbd-server', '172.16.42.2@10001',
                     '/mnt/pmbootstrap/netboot/oneplus-guacamole.img', '-d']
        self.write_command(100, self.args)
        self.write_command(90, ['python3', str(self.work.parent / 'pmbootstrap/pmbootstrap.py'),
                                '-w', str(self.work), 'netboot', 'serve', '--port', '10001'])
        (self.proc / '100/stat').write_text('100 (nbd-server) S 90 0\n')
        (self.proc / '100/root').symlink_to(self.native)
        (self.proc / '100/exe').symlink_to(self.native / 'usr/bin/nbd-server')
        (self.proc / '100/fd/4').symlink_to('socket:[77]')

    def write_command(self, pid, args):
        (self.proc / str(pid) / 'cmdline').write_bytes(('\0'.join(args) + '\0').encode())

    def owners(self):
        return server.owners(self.work, 10001, self.proc)

    def test_finds_only_server_and_exact_restart_loop(self):
        self.assertEqual(self.owners(), [90, 100])

    def test_orphan_adopted_by_systemd_has_no_restart_loop_to_stop(self):
        self.write_command(90, ['/usr/lib/systemd/systemd', '--user'])
        (self.proc / '90/stat').write_text('90 (systemd) S 1 0\n')
        self.assertEqual(self.owners(), [100])

    def test_orphan_still_requires_exact_chroot(self):
        self.write_command(90, ['/usr/lib/systemd/systemd', '--user'])
        (self.proc / '90/stat').write_text('90 (systemd) S 1 0\n')
        path = self.proc / '100/root'
        path.unlink()
        path.symlink_to(self.work.parent)
        with self.assertRaisesRegex(ValueError, 'different chroot'):
            self.owners()

    def test_another_chroot_is_refused(self):
        path = self.proc / '100/root'
        path.unlink()
        path.symlink_to(self.work.parent)
        with self.assertRaisesRegex(ValueError, 'different chroot'):
            self.owners()

    def test_another_export_or_executable_is_refused(self):
        for args in (['other-server', *self.args[1:]],
                     [*self.args[:2], '/mnt/other.img', '-d']):
            self.write_command(100, args)
            with self.assertRaisesRegex(ValueError, 'unrelated process'):
                self.owners()

    def test_foreign_restart_loop_is_refused(self):
        self.write_command(90, ['python3', str(self.work.parent / 'pmbootstrap/pmbootstrap.py'),
                                '-w', '/other/work', 'netboot', 'serve', '--port', '10001'])
        with self.assertRaisesRegex(ValueError, 'ancestor does not match'):
            self.owners()

    def test_unidentified_listener_is_refused(self):
        (self.proc / '100/fd/4').unlink()
        with self.assertRaisesRegex(ValueError, 'cannot identify every listener'):
            self.owners()

    def test_only_validated_pidfds_receive_sigterm(self):
        with patch.object(server, 'owners', return_value=[90, 100]), \
                patch.object(server.os, 'pidfd_open', side_effect=[10, 11]) as opened, \
                patch.object(server, 'wait_for_exit', return_value=[]), \
                patch.object(server.signal, 'pidfd_send_signal') as sent, \
                patch.object(server.os, 'close') as closed:
            server.stop(self.work, 10001)
            self.assertEqual([c.args for c in opened.call_args_list], [(90,), (100,)])
            self.assertEqual([c.args for c in sent.call_args_list],
                             [(10, signal.SIGTERM), (11, signal.SIGTERM)])
            self.assertEqual(closed.call_count, 2)

    def test_blocked_sigterm_only_escalates_the_same_live_pidfd(self):
        with patch.object(server, 'owners', return_value=[90, 100]), \
                patch.object(server.os, 'pidfd_open', side_effect=[10, 11]), \
                patch.object(server, 'wait_for_exit', side_effect=[[11], []]), \
                patch.object(server.signal, 'pidfd_send_signal') as sent, \
                patch.object(server.os, 'close'):
            server.stop(self.work, 10001)
            self.assertEqual([c.args for c in sent.call_args_list], [
                (10, signal.SIGTERM), (11, signal.SIGTERM), (11, signal.SIGKILL)])

    def test_new_listener_prevents_escalation(self):
        with patch.object(server, 'owners', side_effect=[[90, 100], [90, 100], [999]]), \
                patch.object(server.os, 'pidfd_open', side_effect=[10, 11]), \
                patch.object(server, 'wait_for_exit', return_value=[11]), \
                patch.object(server.signal, 'pidfd_send_signal') as sent, \
                patch.object(server.os, 'close'):
            with self.assertRaisesRegex(ValueError, 'new listener'):
                server.stop(self.work, 10001)
            self.assertTrue(all(c.args[1] == signal.SIGTERM for c in sent.call_args_list))

    def test_exit_wait_is_bounded_and_detects_readiness(self):
        read, write = os.pipe()
        try:
            self.assertEqual(server.wait_for_exit([read], 0.01), [read])
            os.write(write, b'x')
            self.assertEqual(server.wait_for_exit([read], 0.01), [])
        finally:
            os.close(read)
            os.close(write)

    def test_changed_ownership_sends_no_signal(self):
        with patch.object(server, 'owners', side_effect=[[90, 100], [91, 100]]), \
                patch.object(server.os, 'pidfd_open', side_effect=[10, 11]), \
                patch.object(server.signal, 'pidfd_send_signal') as sent, \
                patch.object(server.os, 'close'):
            with self.assertRaisesRegex(ValueError, 'ownership changed'):
                server.stop(self.work, 10001)
            sent.assert_not_called()

    def test_baseline_port_is_never_inspected_or_signalled(self):
        with patch.object(server, 'owners') as inspect:
            with self.assertRaisesRegex(ValueError, 'baseline'):
                server.stop(self.work, 10000)
            inspect.assert_not_called()
