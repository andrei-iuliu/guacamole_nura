"""Rootless tests for flows/rc4/local-storage/scripts/install_userdata.py.

A fake fastboot, synthetic boot images and a fake telnet debug shell; no
phone, sudo, network or real fastboot is used.
"""
import importlib.util
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

RC4 = Path(__file__).resolve().parents[1]
SCRIPT = RC4 / "local-storage/scripts/install_userdata.py"

spec = importlib.util.spec_from_file_location("install_userdata", SCRIPT)
iu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(iu)

FAKE_FASTBOOT = r"""#!/bin/sh
echo "$*" >> "$FAKE_LOG"
case "$*" in
  devices) i=0; while [ $i -lt "${FAKE_DEVICES:-1}" ]; do echo "SER$i	fastboot"; i=$((i+1)); done ;;
  *"getvar product") echo "product: ${FAKE_PRODUCT-guacamole}" >&2 ;;
  *"getvar unlocked") echo "unlocked: ${FAKE_UNLOCKED-yes}" >&2 ;;
  *"getvar current-slot") echo "current-slot: ${FAKE_SLOT-b}" >&2 ;;
  *"getvar partition-size:userdata") echo "partition-size:userdata: ${FAKE_USERDATA-0x3800000000}" >&2 ;;
  *) exit 1 ;;
esac
"""


def boot_image(path: Path, cmdline: bytes, kernel: bytes = b"KERNEL" * 100) -> Path:
    page = 4096
    hdr = bytearray(page)
    hdr[:8] = b"ANDROID!"
    struct.pack_into("<I", hdr, 8, len(kernel))
    struct.pack_into("<II", hdr, 36, page, 2)
    hdr[64:64 + len(cmdline)] = cmdline
    path.write_bytes(bytes(hdr) + kernel + b"\0" * (page - len(kernel) % page))
    return path


class FastbootGuard(unittest.TestCase):
    def test_allowed_shapes(self):
        for args in (["devices"], ["getvar", "product"], ["getvar", "partition-size:userdata"],
                     ["-S", "64M", "flash", "userdata", "/x.img"], ["boot", "/b.img"]):
            self.assertTrue(iu.Fastboot.allowed(args), args)

    def test_refused_shapes(self):
        for args in (["flash", "boot_b", "x"], ["flash", "boot_a", "x"], ["flash", "userdata", "x"],
                     ["-S", "64M", "flash", "boot_b", "x"], ["-S", "64M", "flash", "dtbo_b", "x"],
                     ["-S", "1G", "flash", "userdata", "x"], ["set_active", "a"], ["erase", "userdata"],
                     ["oem", "unlock"], ["flashing", "lock"], ["reboot"], ["getvar", "all; rm"],
                     ["--set-active=a"], ["flash", "vbmeta_b", "x"]):
            self.assertFalse(iu.Fastboot.allowed(args), args)
            with self.assertRaises(iu.InstallError):
                iu.Fastboot("/bin/false", dry_run=True).run(args)


class DeviceChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.fb_path = t / "fastboot"
        self.fb_path.write_text(FAKE_FASTBOOT)
        self.fb_path.chmod(0o755)
        self.log = t / "log"
        self.env = dict(os.environ)
        os.environ["FAKE_LOG"] = str(self.log)

    def tearDown(self):
        os.environ.clear(); os.environ.update(self.env)
        self.tmp.cleanup()

    def check(self, size=4 << 30, accept=None, **fake):
        for k in [k for k in os.environ if k.startswith("FAKE_") and k != "FAKE_LOG"]:
            del os.environ[k]
        for k, v in fake.items():
            os.environ[f"FAKE_{k.upper()}"] = str(v)
        return iu.check_device(iu.Fastboot(self.fb_path, dry_run=False), size, accept)

    def test_good_device(self):
        facts = self.check()
        self.assertEqual(facts["current-slot"], "b")
        self.assertIn("-s SER0 getvar product", self.log.read_text())

    def test_refusals(self):
        for fake, needle in (({"slot": "a"}, "slot"), ({"slot": "_a"}, "slot"),
                             ({"unlocked": "no"}, "unlocked"), ({"product": "hotdog"}, "product"),
                             ({"product": ""}, "product"), ({"devices": 0}, "exactly one"),
                             ({"devices": 2}, "exactly one"), ({"userdata": "0x1000"}, "not larger"),
                             ({"userdata": "garbage"}, "partition-size")):
            with self.subTest(fake=fake):
                with self.assertRaisesRegex(iu.InstallError, needle):
                    self.check(**fake)

    def test_slot_with_underscore_and_accept_product(self):
        self.check(slot="_b")
        with self.assertRaises(iu.InstallError):
            self.check(product="msmnile")
        self.check(product="msmnile", accept="msmnile")
        with self.assertRaises(iu.InstallError):
            self.check(product="msmnile", accept="msmnil")


class DebugImage(unittest.TestCase):
    def test_debug_image_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            normal = boot_image(t / "n.img", b"console=null")
            debug = boot_image(t / "d.img", b"console=null pmos.debug-shell")
            iu.check_debug_image(normal, debug)
            with self.assertRaisesRegex(iu.InstallError, "lacks"):
                iu.check_debug_image(normal, normal)
            with self.assertRaisesRegex(iu.InstallError, "carries"):
                iu.check_debug_image(debug, debug)
            other = boot_image(t / "o.img", b"pmos.debug-shell", kernel=b"OTHER" * 50)
            with self.assertRaisesRegex(iu.InstallError, "different kernels"):
                iu.check_debug_image(normal, other)
            with self.assertRaisesRegex(iu.InstallError, "missing debug image"):
                iu.check_debug_image(normal, t / "absent.img")


class DebugShell(unittest.TestCase):
    def serve(self, reply_rc):
        srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
        received = []

        def run():
            conn, _ = srv.accept()
            conn.sendall(b"\xff\xfb\x01\xff\xfb\x03~ # ")   # IAC WILL ECHO/SGA + prompt
            data = b""
            while b"NURA_FSCK_DONE" not in data:
                data += conn.recv(4096)
            received.append(data)
            conn.sendall(data)                               # pty echo: literal $rc
            conn.sendall(f"e2fsck output\nNURA_FSCK_DONE rc={reply_rc}\n".encode())
            conn.close(); srv.close()
        threading.Thread(target=run, daemon=True).start()
        return srv.getsockname()[1], received

    def test_rc_parsed_after_echo(self):
        for rc in (0, 4):
            port, received = self.serve(rc)
            got, transcript = iu.debug_shell("127.0.0.1", port, iu.FSCK_SCRIPT, iu.DONE, 20)
            self.assertEqual(got, rc)
            self.assertNotIn("\xff", transcript)
            self.assertIn(b"losetup --show -r", received[0])   # read-only loop
            self.assertNotIn(b"e2fsck -fy", received[0])

    def test_script_is_read_only(self):
        for forbidden in ("-fy", "-p ", "mkfs", "resize2fs", "dd ", "pmos_continue_boot", "mount "):
            self.assertNotIn(forbidden, iu.FSCK_SCRIPT)

    def test_strip_telnet(self):
        self.assertEqual(iu.strip_telnet(b"a\xff\xfb\x01b\xff\xfa\x18\x01\xff\xf0c\xff\xffd"), b"abc\xffd")


class DryRunEndToEnd(unittest.TestCase):
    def test_dry_run_never_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            fb = t / "fastboot"; fb.write_text(FAKE_FASTBOOT); fb.chmod(0o755)
            tool = t / "tool.py"; tool.write_text("print('PASS: fake manifest check')\n")
            image = t / "rootfs.img"; image.write_bytes(b"\0" * 4096)
            normal = boot_image(t / "boot.img", b"console=null")
            debug = boot_image(t / "debug.img", b"pmos.debug-shell")
            manifest = t / "DELIVERY.local.json"
            manifest.write_text(json.dumps({
                "rootfs_image": {"record": {"path": str(image), "size": 4096, "sha256": "0" * 64}},
                "boot_img": {"path": str(normal), "sha256": "1" * 64},
                "identity": {"kernel_release": "7.0.0-sm8150-guacamole-rc4"}}))
            log = t / "log"
            env = dict(os.environ, FAKE_LOG=str(log), NURA_LOCAL_STORAGE_TOOL=str(tool))
            r = subprocess.run([sys.executable, str(SCRIPT), "--dry-run", "--manifest", str(manifest),
                                "--debug-boot", str(debug), "--fastboot", str(fb),
                                "--backup-dir", str(t / "bk")],
                               capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("dry run, would run:", r.stdout)
            calls = log.read_text()
            self.assertNotIn("flash", calls)
            self.assertNotIn("boot ", calls)
            self.assertFalse((t / "bk").exists())

            # a failing manifest check stops before any fastboot call
            tool.write_text("import sys; sys.exit(1)\n")
            log.unlink()
            r = subprocess.run([sys.executable, str(SCRIPT), "--dry-run", "--manifest", str(manifest),
                                "--debug-boot", str(debug), "--fastboot", str(fb)],
                               capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(r.returncode, 1)
            self.assertIn("manifest check failed", r.stderr)
            self.assertFalse(log.exists())


if __name__ == "__main__":
    unittest.main()
