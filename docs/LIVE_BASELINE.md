# Nura temporary boot — 2026-11-09

The user confirmed that Nura boots on their OnePlus 7 Pro GM1911 and that
Wi-Fi and Bluetooth work. They also saw a SIM-unlock prompt, but reported no
cellular service. These are user observations, not a complete hardware test.

## Boot identity

- Original Nura kernel tag `v6.17.0-sm8150`, source commit
  `379d8fe35c7ca685a650bd82fd023af0ea3f0de0`, all 24 patches.
- Kernel release `6.17.0-sm8150`, 828 matching modules, Phosh + systemd.
- Temporary image `build/boot.img`, SHA-256
  `a449a4dd8d8ed6bdc76e4417c66e5633d273afc58548ef963bdcc84c190c8860`.
- Verified DTBO wrapper; the stock DTBO partition was not erased or flashed.
- Rootfs served over NBD from the PC. Host socket inspection confirmed an
  established connection from `172.16.42.1` to `172.16.42.2:9999`.
- The user issued `fastboot boot`; sending and booting both returned `OKAY`.
  No Android boot/rootfs partition was flashed in this procedure.

## Host fixes needed for this session

The PC's USB interface `enp19s0u4u3` initially had no address. The user added
`172.16.42.2/24`. The phone then timed out reaching NBD while UFW was enabled;
the user added an inbound TCP rule limited to this interface, phone address
and port 9999. Boot succeeded after those changes. The UFW rule persists and
can be removed after netboot testing ends. Interface names can change.

## Acceptance boundary and next step

Wi-Fi and Bluetooth are accepted as working by the user for this boot.
Association, pairing, throughput and coexistence were not separately logged.
Audio, sensors, charging, suspend and long-term stability were not validated
in this session. A SIM-unlock prompt suggests a responding modem/SIM path;
registration, cellular data, SMS and calls remain unverified. The upstream
port also left SIM/cellular operation untested.

Keep the USB cable and NBD server connected while this session runs. The
kernel is temporary, and the root filesystem depends on that connection.
Preserve this baseline before changing modem configuration. Start cellular
diagnostics with read-only `mmcli -L` on the phone, then inspect the returned
modem's lock and registration state without disclosing SIM PINs or identifiers.

## Reset after the first session

The user subsequently reported that the phone returned to Android by itself,
possibly following a brief USB disconnect. Android was observed over ADB;
its reported boot reason was `bootloader` and battery was 100% at 4.384 V.
These readings do not establish why Linux reset.

The served image's root partition was preserved at
`local/postmortem-20261109/rootfs.ext4` with source offsets and SHA-256 in
`copy.json`. A normal read-only debugfs open reported a bitmap-checksum error;
checksum-disabled read-only inspection recovered the saved journals. This was
an unclean filesystem snapshot, not a completed filesystem-health check.
Kernel and PID1 extracts are saved beside the image. Records extend to about
560 seconds of kernel uptime; no saved panic or clean shutdown explains the
reset. Missing final records cannot distinguish USB loss from a kernel reset.

The user started a second temporary boot. Re-enumeration removed the host USB
IP address, which the user restored. A new NBD connection was observed, and
SSH subsequently responded with `SSH-2.0-OpenSSH_10.6`. No SSH authentication,
privileged remote command, repair or automatic reboot was performed by the
coordinator. Stability is not accepted; capture live logs on the next session.
