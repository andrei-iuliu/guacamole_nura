# Sensor fix: exact commands and outputs

Run 2026-11-14 (UTC). Host tools: `cc` (GCC), `llvm-objdump`, `patch`,
`sha512sum`, `curl`; headers glib 2.90.1 / gudev 238 / polkit 128 /
libqmi 1.38 / protobuf-c.  No phone, no root, no pmbootstrap, no chroot, no
mount, no apk build.

## Reproducible check (all four steps)

```
$ flows/rc4/topics/sensors/checks/verify.sh /tmp/verify-topic2
work: /tmp/verify-topic2
== iio-sensor-proxy: source and sums
iio-sensor-proxy-3.9.tar.gz: OK
0001-drv-ssc-enable-and-disable-sensors-asynchronously.patch: OK
== iio-sensor-proxy: apply with -F0 (zero fuzz)
patching file src/drv-ssc-accel.c
patching file src/drv-ssc-compass.c
patching file src/drv-ssc-light.c
patching file src/drv-ssc-proximity.c
== libssc: source and sums
libssc-0.4.4-1.tar.gz: OK
0001-libssc-common-block-in-the-sync-wait.patch: OK
== libssc: apply with -F0 (zero fuzz)
patching file src/libssc-common.c
== compile: patched iio-sensor-proxy SSC drivers
   drv-ssc-light          OK
   drv-ssc-proximity      OK
   drv-ssc-accel          OK
   drv-ssc-compass        OK
== compile: patched libssc-common.c
   libssc-common          OK
all checks passed
```

## Tarball hashes cross-checked against the Alpine recipes

The versions on the phone are exactly the ones the pinned edge index has, so
the recipes reuse the aports sources and sums:

```
$ curl -fsSL \
    https://raw.githubusercontent.com/alpinelinux/aports/master/community/iio-sensor-proxy/APKBUILD
...
sha512sums="
a89c842f8db2a1a36c1c7c990434bb57fd905224459170cba2ec56e79f1272acc4cac4221937704d5e9247f957d86fde716d25e2d1ccb80d88fa643b47edfda6  iio-sensor-proxy-3.9.tar.gz
b25df94e249ca46fe96926e5a866c20dc913fa52a2d9259fb7a3594861c6b2f246fc7fef8a21f5b021f3a71401f734ae27210137b0b1917a7daaffb5e3221f2e  iio-sensor-proxy.initd
"

$ curl -fsSL https://gitlab.freedesktop.org/hadess/iio-sensor-proxy/-/archive/3.9/iio-sensor-proxy-3.9.tar.gz -o /tmp/isp39.tar.gz
$ sha512sum /tmp/isp39.tar.gz
a89c842f8db2a1a36c1c7c990434bb57fd905224459170cba2ec56e79f1272acc4cac4221937704d5e9247f957d86fde716d25e2d1ccb80d88fa643b47edfda6  /tmp/isp39.tar.gz

$ curl -fsSL \
    https://raw.githubusercontent.com/alpinelinux/aports/master/community/libssc/APKBUILD
...
sha512sums="
9240970f6a70f4900ef2d8fad337dac815ea2602b3b395943326fb086f59d24dcf59b76bdbc6014ebd3c450eaad5393465ca038c165845ffe99bf8e282a5c170  libssc-0.4.4-1.tar.gz
"

$ curl -fsSL https://codeberg.org/DylanVanAssche/libssc/archive/v0.4.4.tar.gz -o /tmp/libssc-0.4.4-1.tar.gz
$ sha512sum /tmp/libssc-0.4.4-1.tar.gz
9240970f6a70f4900ef2d8fad337dac815ea2602b3b395943326fb086f59d24dcf59b76bdbc6014ebd3c450eaad5393465ca038c165845ffe99bf8e282a5c170  /tmp/libssc-0.4.4-1.tar.gz
```

## Local patch hashes (what the APKBUILDs list, source= order)

```
$ sha512sum flows/rc4/topics/sensors/recipe/*/*.patch
7fa5df4c2322b1273a5d968615905f445727414a8693f501978725b45969eeb1f875655bf8060b8239c00198b9dd12db5c4e931992b7e28db5d8999c7049ab66  flows/rc4/topics/sensors/recipe/iio-sensor-proxy/0001-drv-ssc-enable-and-disable-sensors-asynchronously.patch
ff6745564714413d72de76418060db2a9aad86aece8bbd6d03b68770afb093b2012acb3766d077685716a66d3a0ce263017599102e1ec66b48b9a10665646777  flows/rc4/topics/sensors/recipe/libssc/0001-libssc-common-block-in-the-sync-wait.patch
```

## Upstream 30 s-timeout commit does not apply to 0.4.4

```
$ cd /tmp/libssc && git checkout v0.4.4
$ git show 54dd13e > /tmp/upstream-54dd13e.patch
$ git apply --check /tmp/upstream-54dd13e.patch
error: patch failed: src/libssc-sensor.c:311
error: src/libssc-sensor.c: patch does not apply

$ patch -p1 --dry-run -F3 < /tmp/upstream-54dd13e.patch
checking file src/libssc-sensor.c
Hunk #5 succeeded at 318 with fuzz 3.
Hunk #6 succeeded at 359 with fuzz 3.
Hunk #7 succeeded at 448 with fuzz 3.
Hunk #8 succeeded at 476 with fuzz 3.
Hunk #9 succeeded at 495 with fuzz 3.
Hunk #10 succeeded at 607 (offset 5 lines).
Hunk #11 succeeded at 718 (offset 5 lines).
Hunk #12 succeeded at 824 (offset 5 lines).
```

## Shipped libssc disassembly (the busy wait)

```
$ cd /tmp/libssc-apk      # tar -xzf libssc-0.4.4-r0.apk from Alpine edge community/aarch64
$ llvm-objdump -d --no-show-raw-insn --start-address=0x47c8 --stop-address=0x4810 usr/lib/libssc.so.2
0000000000004260 <.text>:
    47c8:      	stp	x29, x30, [sp, #-0x20]!
    47cc:      	mov	x29, sp
    47d0:      	str	x19, [sp, #0x10]
    47d4:      	mov	x19, x0
    47d8:      	bl	0x40a0 <g_mutex_lock@plt>
    47dc:      	ldr	w0, [x19, #0x20]
    47e0:      	cbz	w0, 0x47f4 <.text+0x594>
    47e4:      	mov	x0, x19
    47e8:      	ldr	x19, [sp, #0x10]
    47ec:      	ldp	x29, x30, [sp], #0x20
    47f0:      	b	0x3d60 <g_mutex_unlock@plt>
    47f4:      	mov	x0, x19
    47f8:      	bl	0x3d60 <g_mutex_unlock@plt>
    47fc:      	bl	0x3e30 <g_main_context_default@plt>
    4800:      	mov	w1, #0x0                // =0
    4804:      	bl	0x3d20 <g_main_context_iteration@plt>
    4808:      	mov	x0, x19
    480c:      	b	0x47d8 <.text+0x578>
```

See `../evidence/libssc-0.4.4-r0-wait-loop.txt` for the full listing, the
source excerpt and the load-base arithmetic that puts the phone backtrace at
file offset 0x4808.

## Not run

* `abuild` / `meson` / `pmbootstrap` for either recipe (no build allowed in
  this task; no `meson` on this host),
* the patched daemon on the phone (the coordinator reproduces there),
* a shipped helper script: this topic ships no runtime script (the watchdog
  mitigation was dropped from scope).
