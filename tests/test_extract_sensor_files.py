"""Exercise the legacy extraction helper without contacting a phone."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/extract_sensor_files.sh"


class SensorExtractionTest(unittest.TestCase):
    def test_sudo_failure_stops_without_creating_a_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ssh = root / "ssh"
            ssh.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "assert sys.argv[1] == 'user@phone'\n"
                "command = sys.argv[2]\n"
                "assert 's(){ sudo -n \"$@\"; }' in command\n"
                "assert 'sudo -S' not in command\n"
                "assert 'P=' not in command\n"
                "print('sudo: a password is required', file=sys.stderr)\n"
                "sys.exit(1)\n"
            )
            ssh.chmod(0o755)
            output = root / "output"
            result = subprocess.run(
                ["bash", str(SCRIPT), "user@phone", str(output)],
                env={**os.environ, "PATH": f"{root}:{os.environ['PATH']}"},
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("sudo: a password is required", result.stderr)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
