#!/usr/bin/env python3
"""USER RUN: install a HOST-VERIFIED local-userdata build onto the phone.

Replaces the Linux rootfs on `userdata` (everything under /home/user and every
hand-made change on the phone is lost; a backup is taken first when the phone
is reachable over SSH). Android on slot _a is not touched.

Steps:
  1. host: re-verify DELIVERY.local.json with local_storage_tool.py check;
     check the debug image (same kernel, pmos.debug-shell on its cmdline)
  2. backup /home/user and /etc/apk/world over SSH, if the phone runs Linux
  3. reach fastboot; require exactly one device, a guacamole product,
     unlocked=yes, current-slot=b, userdata larger than the image
  4. after a typed confirmation: fastboot -S 64M flash userdata <image>
  5. RAM-boot the debug image, run a read-only e2fsck of both
     subpartitions in the initramfs debug shell, then pmos_continue_boot
  6. wait for SSH and check the running kernel release
  7. PRINT (never run) the commands to RAM-boot and then flash boot_b

The only fastboot commands this script can issue are devices, getvar,
`-S 64M flash userdata` and `boot`; see Fastboot.ALLOWED. It never writes
boot_a/boot_b, dtbo, vbmeta, or changes the active slot.

  install_userdata.py --dry-run     # host checks + read-only getvars, no writes
  install_userdata.py               # the real install
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

FLOW = Path(__file__).resolve().parents[1]           # flows/rc4/local-storage
REPO = FLOW.parents[2]                             # the checkout
TOOL = Path(os.environ.get("NURA_LOCAL_STORAGE_TOOL", FLOW / "scripts/local_storage_tool.py"))
CONFIRM_PHRASE = "ERASE USERDATA"
GUACAMOLE_PRODUCT = re.compile(r"guacamole|oneplus 7 pro|gm191[0-9]", re.I)
DEBUG_TOKEN = b"pmos.debug-shell"


class InstallError(Exception):
    pass


def die(msg: str) -> None:
    raise InstallError(msg)


def say(msg: str) -> None:
    print(f"==> {msg}", flush=True)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------
# boot images

def boot_header(path: Path) -> dict:
    """Android boot image header v0-v2: kernel payload digest and cmdline."""
    data = path.read_bytes()
    if data[:8] != b"ANDROID!":
        die(f"{path} is not an Android boot image")
    kernel_size, = struct.unpack_from("<I", data, 8)
    page_size, header_version = struct.unpack_from("<II", data, 36)
    if header_version > 2:
        die(f"{path}: unexpected boot header v{header_version}")
    cmdline = data[64:64 + 512].split(b"\0")[0] + data[608:608 + 1024].split(b"\0")[0]
    kernel = data[page_size:page_size + kernel_size]
    return {"kernel_sha256": hashlib.sha256(kernel).hexdigest(), "cmdline": cmdline}


def check_debug_image(normal: Path, debug: Path) -> None:
    if not debug.is_file():
        die(f"missing debug image {debug}; build it with "
            "flows/rc4/local-storage/scripts/local_storage_debug_bootimg.sh")
    n, d = boot_header(normal), boot_header(debug)
    if DEBUG_TOKEN in n["cmdline"]:
        die(f"normal boot image {normal} carries {DEBUG_TOKEN.decode()}")
    if DEBUG_TOKEN not in d["cmdline"]:
        die(f"debug image {debug} lacks {DEBUG_TOKEN.decode()} on its cmdline")
    if n["kernel_sha256"] != d["kernel_sha256"]:
        die("debug and normal boot images carry different kernels")


# --------------------------------------------------------------------------
# fastboot, restricted

class Fastboot:
    """Only these command shapes can reach the bootloader."""

    def __init__(self, binary: Path, dry_run: bool):
        self.binary, self.dry_run, self.serial = str(binary), dry_run, None

    @staticmethod
    def allowed(args: list[str]) -> bool:
        if args in (["devices"],):
            return True
        if len(args) == 2 and args[0] == "getvar" and re.fullmatch(r"[a-z:_-]+", args[1]):
            return True
        if len(args) == 5 and args[:4] == ["-S", "64M", "flash", "userdata"]:
            return True
        if len(args) == 2 and args[0] == "boot":
            return True
        return False

    def run(self, args: list[str], timeout: int = 30, write: bool = False) -> subprocess.CompletedProcess:
        if not self.allowed(args):
            die(f"refusing fastboot command: {args}")
        argv = [self.binary] + (["-s", self.serial] if self.serial and args != ["devices"] else []) + args
        if write and self.dry_run:
            say(f"dry run, would run: {shlex.join(argv)}")
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)

    def devices(self) -> list[str]:
        out = self.run(["devices"]).stdout
        return [line.split()[0] for line in out.splitlines() if line.strip().endswith("fastboot")]

    def getvar(self, name: str) -> str:
        r = self.run(["getvar", name])
        for line in (r.stderr + r.stdout).replace("\r", "").splitlines():
            if line.startswith(f"{name}:"):
                return line[len(name) + 1:].strip()
        return ""


def check_device(fb: Fastboot, image_size: int, accept_product: str | None) -> dict:
    devices = fb.devices()
    if len(devices) != 1:
        die(f"need exactly one fastboot device, found {len(devices)}: {devices}")
    fb.serial = devices[0]
    facts = {name: fb.getvar(name) for name in
             ("product", "unlocked", "current-slot", "partition-size:userdata")}
    say("bootloader: " + ", ".join(f"{k}={v or '?'}" for k, v in facts.items()))
    product = facts["product"]
    if not (GUACAMOLE_PRODUCT.search(product) or (accept_product and product == accept_product)):
        die(f"product {product!r} is not recognised as guacamole; if this really is the "
            f"OnePlus 7 Pro, rerun with --accept-product {shlex.quote(product)}")
    if facts["unlocked"].lower() != "yes":
        die("bootloader is not unlocked")
    if facts["current-slot"].lstrip("_") != "b":
        die(f"current slot is {facts['current-slot']!r}; the Linux boot slot must be b")
    try:
        size = int(facts["partition-size:userdata"], 0)
    except ValueError:
        die("could not read partition-size:userdata")
    if size <= image_size:
        die(f"userdata ({size} B) is not larger than the image ({image_size} B)")
    return facts


def wait_for(predicate, timeout: float, interval: float = 2.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(interval)
    return False


# --------------------------------------------------------------------------
# SSH (phone running Linux)

def ssh_argv(args, *extra: str) -> list[str]:
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
            *extra, f"{args.ssh_user}@{args.phone_ip}"]


def ssh_ok(args) -> bool:
    return subprocess.run(ssh_argv(args) + ["true"], capture_output=True).returncode == 0


def backup(args, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    home = dest / "home-user.tar.gz"
    say(f"backing up /home/user to {home}")
    with home.open("wb") as out:
        tar = subprocess.Popen(ssh_argv(args) + ["tar -C /home -cpf - user"], stdout=subprocess.PIPE)
        gz = subprocess.run(["gzip"], stdin=tar.stdout, stdout=out)
        tar.stdout.close()
        if tar.wait() != 0 or gz.returncode != 0:
            die("home backup failed")
    if subprocess.run(["gzip", "-t", str(home)]).returncode != 0:
        die("home backup does not pass gzip -t")
    world = subprocess.run(ssh_argv(args) + ["cat /etc/apk/world"], capture_output=True, text=True)
    if world.returncode != 0:
        die("could not read /etc/apk/world")
    (dest / "apk-world.txt").write_text(world.stdout)
    say(f"backup done ({home.stat().st_size} B, {len(world.stdout.split())} world entries)")


# --------------------------------------------------------------------------
# initramfs debug shell (telnet on port 23)

FSCK_SCRIPT = r"""
dev=''; for u in /sys/class/block/*/uevent; do grep -qx 'PARTNAME=userdata' "$u" && dev=/dev/$(sed -n 's/^DEVNAME=//p' "$u"); done; echo "NURA_DEV=[$dev]"
rc=99; if [ -b "$dev" ]; then loop=$(losetup --show -r -Pf --sector-size 4096 "$dev"); echo "NURA_LOOP=[$loop]"; rc=98; if [ -b "${loop}p2" ]; then e2fsck -fn "${loop}p1"; r1=$?; e2fsck -fn "${loop}p2"; r2=$?; rc=$((r1 | r2)); fi; [ -n "$loop" ] && losetup -d "$loop"; fi; echo "NURA_FSCK_DONE rc=$rc"
"""
DONE = re.compile(rb"NURA_FSCK_DONE rc=(\d+)")


def strip_telnet(data: bytes) -> bytes:
    """Drop telnet IAC negotiation (IAC cmd opt, and IAC SB ... IAC SE)."""
    out, i = bytearray(), 0
    while i < len(data):
        b = data[i]
        if b != 0xFF:
            out.append(b); i += 1; continue
        cmd = data[i + 1] if i + 1 < len(data) else None
        if cmd == 0xFF:
            out.append(0xFF); i += 2
        elif cmd == 0xFA:
            end = data.find(b"\xff\xf0", i)
            i = len(data) if end < 0 else end + 2
        else:
            i += 3
    return bytes(out)


def debug_shell(host: str, port: int, script: str, done: re.Pattern, timeout: float) -> tuple[int, str]:
    with socket.create_connection((host, port), timeout=10) as s:
        s.settimeout(2)
        time.sleep(1)
        buf = b""
        for line in script.strip().splitlines():
            s.sendall(line.encode() + b"\n")
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                break
            buf += strip_telnet(chunk)
            m = done.search(buf)
            if m:
                return int(m.group(1)), buf.decode(errors="replace")
    die("debug shell closed or timed out before the filesystem check finished")


def continue_boot(host: str, port: int) -> None:
    with socket.create_connection((host, port), timeout=10) as s:
        s.sendall(b"pmos_continue_boot\n")
        time.sleep(2)


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


# --------------------------------------------------------------------------

def load_manifest(path: Path) -> dict:
    if not path.is_file():
        die(f"no manifest at {path}; build first with "
            "flows/rc4/local-storage/scripts/local_storage_build.sh --build (or pass --manifest)")
    say(f"verifying {path}")
    r = subprocess.run([sys.executable, str(TOOL), "check", "--manifest", str(path)],
                       capture_output=True, text=True)
    sys.stdout.write(r.stdout); sys.stderr.write(r.stderr)
    if r.returncode != 0:
        die("manifest check failed; rebuild or fix before installing")
    m = json.loads(path.read_text())
    return {"image": Path(m["rootfs_image"]["record"]["path"]),
            "image_size": m["rootfs_image"]["record"]["size"],
            "image_sha256": m["rootfs_image"]["record"]["sha256"],
            "boot": Path(m["boot_img"]["path"]),
            "boot_sha256": m["boot_img"]["sha256"],
            "release": m["identity"]["kernel_release"]}


def confirm(prompt: str, phrase: str) -> None:
    print(prompt)
    if input(f'Type "{phrase}" to continue: ').strip() != phrase:
        die("not confirmed; nothing was written")


BATTERY_LOW_MV = 3700


def battery_note(fb: Fastboot) -> tuple[str, str]:
    """(status, detail) from the ABL's battery getvars; unknown when unsupported."""
    soc_ok = fb.getvar("battery-soc-ok").lower()
    voltage = fb.getvar("battery-voltage")
    millivolts = int(voltage) if voltage.isdigit() else None
    detail = f"battery-voltage={voltage or '?'} battery-soc-ok={soc_ok or '?'}"
    if soc_ok == "no":
        return "FAIL", detail + " (charge the phone before flashing)"
    if millivolts is not None and millivolts < BATTERY_LOW_MV:
        return "warn", detail + f" (below {BATTERY_LOW_MV} mV; charge first)"
    if millivolts is None and not soc_ok:
        return "warn", "the bootloader does not report the battery; make sure it is charged"
    return "ok", detail


def preflight(args) -> int:
    """Read-only checklist of everything the install needs; reports all items."""
    rows: list[tuple[str, str, str]] = []

    def add(status: str, label: str, detail: str = "") -> None:
        rows.append((status, label, detail))

    prereq = subprocess.run(["bash", str(REPO / "scripts/check_prereqs.sh"), "--stage", "core",
                             "--stage", "phone", "--quiet"], capture_output=True, text=True)
    add("ok" if prereq.returncode == 0 else "FAIL", "host packages (core, phone)",
        prereq.stderr.strip().splitlines()[0] if prereq.returncode else "")

    manifest = None
    try:
        manifest = load_manifest(args.manifest)
        add("ok", "build manifest re-verified", f"{manifest['release']}, image {manifest['image_size']} B")
    except InstallError as e:
        add("FAIL", "build manifest", str(e))
    if manifest:
        try:
            check_debug_image(manifest["boot"], args.debug_boot)
            add("ok", "debug image matches the build", str(args.debug_boot))
        except InstallError as e:
            add("FAIL", "debug image", str(e))
    else:
        add("skip", "debug image", "needs the build manifest")

    key_dir = Path(os.environ.get("NURA_SIGNING_KEY_DIR", Path.home() / ".config/nura-guacamole/abuild"))
    keys = sorted(key_dir.glob("*.rsa")) if key_dir.is_dir() else []
    add("ok" if keys else "warn", "project signing key",
        keys[0].stem if keys else "none yet: later kernel APKs will need their key copied to the phone "
        "(scripts/signing_key.sh adopt)")

    if not args.fastboot.is_file():
        add("FAIL", "fastboot", f"missing {args.fastboot} (./nura fetch)")
        devices = []
    else:
        fb = Fastboot(args.fastboot, dry_run=True)
        devices = fb.devices()
    if not devices:
        if ssh_ok(args):
            add("ok", "phone reachable over SSH", "the install backs up /home/user, then reboots to fastboot")
        else:
            add("skip", "phone", "not in fastboot and not on SSH: connect it to check the bootloader")
    elif len(devices) > 1:
        add("FAIL", "fastboot devices", f"{len(devices)} connected; keep only the phone")
    else:
        fb.serial = devices[0]
        get = fb.getvar
        product = get("product")
        known = GUACAMOLE_PRODUCT.search(product) or (args.accept_product and product == args.accept_product)
        add("ok" if known else "FAIL", "bootloader product",
            product + ("" if known else f" (not recognised; --accept-product {product} if this is the OnePlus 7 Pro)"))
        unlocked = get("unlocked").lower()
        add("ok" if unlocked == "yes" else "FAIL", "bootloader unlocked", unlocked or "?")
        slot = get("current-slot").lstrip("_")
        add("ok" if slot == "b" else "FAIL", "current slot is b (Linux)", slot or "?")
        if get("slot-unbootable:b").lower() == "yes":
            add("warn", "slot b", "marked unbootable; the install RAM-boots, but check before flashing boot_b")
        a_unbootable, a_ok = get("slot-unbootable:a").lower(), get("slot-successful:a").lower()
        if a_unbootable == "yes":
            add("warn", "slot a (LineageOS)", "marked unbootable: the Android fallback is not usable")
        else:
            add("ok", "slot a (LineageOS)", f"unbootable={a_unbootable or '?'} successful={a_ok or '?'}")
        size = get("partition-size:userdata")
        try:
            size_bytes = int(size, 0)
            if manifest:
                fits = size_bytes > manifest["image_size"]
                add("ok" if fits else "FAIL", "userdata larger than the image",
                    f"{size_bytes} B vs {manifest['image_size']} B")
            else:
                add("ok", "userdata partition", f"{size_bytes} B")
        except ValueError:
            add("FAIL", "userdata partition size", f"unreadable: {size!r}")
        battery_status, battery_detail = battery_note(fb)
        add(battery_status, "battery", battery_detail)

    width = max(len(label) for _, label, _ in rows)
    print("\nPre-install checklist (read-only)")
    for status, label, detail in rows:
        print(f"  {status:<5} {label:<{width}}  {detail}")
    failed = [r for r in rows if r[0] == "FAIL"]
    print("\n" + ("not ready: fix the FAIL items above" if failed else
                  "ready to install (warn items are worth a look)"))
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ui_suffix = "" if os.environ.get("NURA_UI", "phosh") == "plasma" else "-phosh"
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--manifest", type=Path,
                   default=FLOW / f"build/local-userdata{ui_suffix}/DELIVERY.local.json")
    p.add_argument("--debug-boot", type=Path,
                   default=FLOW / f"build/local-debug{ui_suffix}/boot/boot.img")
    p.add_argument("--fastboot", type=Path, default=REPO / "platform-tools/fastboot")
    p.add_argument("--phone-ip", default="172.16.42.1")
    p.add_argument("--ssh-user", default="user")
    p.add_argument("--backup-dir", type=Path)
    p.add_argument("--skip-backup", action="store_true", help="do not back up even if SSH works")
    p.add_argument("--accept-product", help="exact fastboot product string to accept")
    p.add_argument("--no-continue", action="store_true",
                   help="stop in the debug shell after the filesystem check")
    p.add_argument("--dry-run", action="store_true",
                   help="host checks and read-only getvars only; no reboot, flash or boot")
    p.add_argument("--preflight", action="store_true",
                   help="read-only checklist of everything the install needs (reports all items)")
    args = p.parse_args(argv)
    if args.preflight:
        return preflight(args)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    logdir = args.backup_dir or REPO / f"backups/userdata-install-{stamp}"

    try:
        # 1. host
        m = load_manifest(args.manifest)
        check_debug_image(m["boot"], args.debug_boot)
        if not args.fastboot.is_file():
            die(f"missing fastboot: {args.fastboot}")
        fb = Fastboot(args.fastboot, args.dry_run)
        say(f"image {m['image']} ({m['image_size']} B), kernel {m['release']}")

        # 2. backup + 3. reach fastboot
        if not fb.devices():
            if ssh_ok(args):
                if not args.skip_backup:
                    if args.dry_run:
                        say(f"dry run, would back up /home/user and apk world to {logdir}")
                    else:
                        backup(args, logdir)
                if args.dry_run:
                    say("dry run, would reboot the phone into the bootloader; stopping here")
                    return 0
                confirm("The phone will reboot into the bootloader (sudo password asked on the phone side).",
                        "REBOOT")
                subprocess.run(ssh_argv(args, "-t") + ['sync; sudo sh -c "sync; reboot bootloader"'])
            else:
                say("no fastboot device and no SSH: put the phone into fastboot mode (Power + Volume Down)")
            if not wait_for(lambda: bool(fb.devices()), 180):
                die("no fastboot device appeared")
        elif not args.skip_backup:
            say("phone is already in fastboot: no backup possible (use --skip-backup to silence this)")
        facts = check_device(fb, m["image_size"], args.accept_product)

        # 4. flash userdata
        if not args.dry_run:
            confirm(f"\nThis REPLACES the Linux rootfs on userdata of {fb.serial} with\n  {m['image']}\n"
                    f"  sha256 {m['image_sha256']}\nAll data in /home/user on the phone is lost.",
                    CONFIRM_PHRASE)
        say("flashing userdata in 64 MiB sparse chunks")
        r = fb.run(["-S", "64M", "flash", "userdata", str(m["image"])], timeout=3600, write=True)
        if r.returncode != 0:
            die(f"flash failed:\n{r.stdout}{r.stderr}")
        if args.dry_run:
            say(f"dry run, would RAM-boot {args.debug_boot} and fsck in the debug shell; done")
            return 0

        # 5. debug boot + read-only fsck
        logdir.mkdir(parents=True, exist_ok=True)
        (logdir / "bootloader.json").write_text(json.dumps(facts, indent=2) + "\n")
        say(f"RAM-booting the debug image {args.debug_boot}")
        env = dict(os.environ, PATH=f"{args.fastboot.parent}:{os.environ['PATH']}")
        if subprocess.run(["bash", str(REPO / "scripts/fastboot_boot.sh"), str(args.debug_boot)],
                          env=env).returncode != 0:
            die("debug image did not boot; userdata is flashed, the phone is still in fastboot")
        say("waiting for the initramfs debug shell (telnet 23)")
        if not wait_for(lambda: port_open(args.phone_ip, 23), 240):
            die("debug shell did not appear; check the host USB network (172.16.42.2/24)")
        rc, transcript = debug_shell(args.phone_ip, 23, FSCK_SCRIPT, DONE, 900)
        (logdir / "debug-fsck.txt").write_text(transcript)
        if rc != 0:
            die(f"read-only e2fsck reported rc={rc}; the phone is left in the debug shell "
                f"(nc {args.phone_ip} 23). Transcript: {logdir / 'debug-fsck.txt'}")
        say("read-only e2fsck clean on both subpartitions")
        if args.no_continue:
            say(f"stopping as requested; continue by hand: nc {args.phone_ip} 23, pmos_continue_boot")
            return 0

        # 6. continue and check
        continue_boot(args.phone_ip, 23)
        say("continuing boot; first boot grows the filesystem, this can take several minutes")
        if not wait_for(lambda: ssh_ok(args), 900, 5):
            die("SSH did not come up within 15 minutes; check the screen")
        release = subprocess.run(ssh_argv(args) + ["uname -r"], capture_output=True, text=True).stdout.strip()
        if release != m["release"]:
            die(f"phone runs kernel {release!r}, expected {m['release']!r}")
        say(f"phone is up on {release}; logs in {logdir}")

        # 7. print-only next steps
        fbq = shlex.quote(str(args.fastboot))
        boot = shlex.quote(str(m["boot"]))
        print(f"""
Next steps (NOT run by this script):
  1. reboot to the bootloader:
       ssh -t {args.ssh_user}@{args.phone_ip} 'sync; sudo sh -c "sync; reboot bootloader"'
  2. RAM-boot the normal image and check the phone:
       PATH={shlex.quote(str(args.fastboot.parent))}:$PATH bash scripts/fastboot_boot.sh {boot}
  3. if it is good, from the bootloader again:
       sha256sum {boot}   # expect {m['boot_sha256']}
       {fbq} flash boot_b {boot}
       {fbq} reboot
Restore files from {logdir}/home-user.tar.gz selectively (not the whole home).""")
        return 0
    except InstallError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
