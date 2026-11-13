"""Loop-device trust boundary and cleanup, without any admin commands."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/rehearse_storage.py'
spec = importlib.util.spec_from_file_location('rehearsal', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RehearsalTests(unittest.TestCase):
    def test_non_loop_device_is_rejected_without_a_command(self):
        with patch.object(module, 'run') as command:
            with self.assertRaises(RuntimeError):
                module.owned_loop('/dev/sda', Path('/fixture'))
            command.assert_not_called()

    def test_wrong_backing_file_is_rejected(self):
        with patch.object(module, 'run', return_value='/dev/loop8'):
            with self.assertRaises(RuntimeError):
                module.owned_loop('/dev/loop7', Path('/fixture'))

    def test_failure_detaches_only_the_owned_loop(self):
        calls = []
        def command(*args):
            calls.append(args)
            if args[:2] == ('losetup', '--find'):
                return '/dev/loop7'
            if args[:2] == ('losetup', '--noheadings'):
                return '/dev/loop7'
            if args[:2] == ('blockdev', '--getss'):
                return '512'  # force failure before partition creation
            return ''
        with tempfile.TemporaryDirectory() as directory, patch.object(module, 'run', side_effect=command):
            with self.assertRaises(RuntimeError):
                module.rehearse(Path(directory))
        self.assertIn(('losetup', '--detach', '/dev/loop7'), calls)
        self.assertFalse(any(c[0] in ('parted', 'mkfs.ext4') for c in calls))
