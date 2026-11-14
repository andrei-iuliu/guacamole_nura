# rc4 topic: iio-sensor-proxy spins one CPU core after Mobile Settings -> Sensors

Status: **root cause identified; the fix is wired into both images** as two
local recipes in `flows/rc4/device/testing/` (iio-sensor-proxy 3.9-r100,
libssc 0.4.4-r100) with the same plumbing the patched `phoc` has: recipe
links in the flow, version pins in the install list and `/etc/apk/world`,
a build-order rule and an installed-rootfs marker check.  **Not built and
not yet tested on a phone** (no build runs here).  The device package is
untouched - no pkgrel bump; the always-on watchdog mitigation from the first
draft was dropped after review, see "Why not a watchdog".

Reporter's symptom (phone, Phosh, 2026-11-14, device package 4-r19,
iio-sensor-proxy 3.9-r2, libssc 0.4.4-r0, hexagonrpcd 0.4.0-r3):

* the daemon's **main thread** sat at 100 % of one core (~7100 s CPU in ~2.4 h)
  since shortly after boot;
* `/proc/PID/task/PID/syscall` said `running` in 30/30 samples and
  `/proc/PID/io` syscr/syscw/rchar did not change over 5 s;
* accelerometer, light and proximity still reported on D-Bus;
* `systemctl restart iio-sensor-proxy` fixed it (new process ~2 % CPU);
* the spin started **when Phosh Mobile Settings was opened and its Sensors page
  claimed every sensor at once** (key new fact from the second report).

Reproduction and backtrace collected on the phone by the coordinator:
`evidence/iio-bt-20261114.txt` (five identical samples; debug symbols from
`iio-sensor-proxy-dbg` 3.9-r2 / `libssc-dbg` 0.4.4-r0):

```
main -> g_main_loop_run
  -> D-Bus handle_method_call (ClaimProximity, sender :1.336)   iio-sensor-proxy.c:594
     -> ssc_proximity_set_polling                              drv-ssc-proximity.c:97
        -> ssc_sensor_proximity_open_sync                      (libssc)
           -> ssc_common_wait_sync_context                    (libssc-common.c:56)
              -> g_main_context_iteration(default, FALSE)
                 -> D-Bus handle_method_call (ClaimLight, same sender)
                    -> ssc_light_set_polling               drv-ssc-light.c:94
                       -> ssc_sensor_light_open_sync       (libssc)
                          -> ssc_common_wait_sync_context  (libssc-common.c:56)
                             -> g_main_context_iteration(default, FALSE) -> ppoll
                             (here, forever)
```

## Root cause

Two defects stack up; neither is a "loop over sensor lists" or "NaN maths"
problem, and neither is in the SSC protocol decoder.

### 1. libssc's synchronous wait is a busy poll that iterates the *shared*
   default main context

`ssc_common_wait_sync_context()`, libssc 0.4.4 `src/libssc-common.c:51-60`:

```c
void
ssc_common_wait_sync_context (SyncContext *ctx) {
	g_mutex_lock (&ctx->mutex);
	while (!ctx->finished) {
		g_mutex_unlock (&ctx->mutex);
		g_main_context_iteration (g_main_context_default (), FALSE);
		g_mutex_lock (&ctx->mutex);
	}
	g_mutex_unlock (&ctx->mutex);
}
```

`may_block = FALSE` means `g_main_context_iteration()` polls with a 0 ms
timeout and returns immediately whenever the awaited async operation did not
complete inside the iteration.  The loop is therefore a busy wait: 100 % of one
core, and its only syscall is `ppoll(2)` with a zero timeout - which is why
`/proc/PID/io` syscr/syscw/rchar stay flat (ppoll is not a read or a write) and
why `/proc/PID/syscall` mostly reads `running` (the task is on-CPU, not
blocked in a syscall).  This is confirmed against the **shipped binary**, not
just the source: the helper in `libssc-0.4.4-r0.apk` (aarch64, Alpine edge,
the exact package on the phone) contains `mov w1, #0x0; bl
g_main_context_iteration` in the loop, and the backtrace's return address maps
onto the instruction after that call.  Full disassembly and the address
arithmetic: `evidence/libssc-0.4.4-r0-wait-loop.txt`.

The rework that introduced this is libssc commit `6e6d92d` "libssc-common:
rework synchronous handling" (PR #114, released in 0.4.0).  Before 0.4.0 each
sensor had its own `GMainContext` plus `g_main_loop_run()`, i.e. the wait
blocked.

### 2. iio-sensor-proxy runs those waits *inside* the D-Bus method handler,
   and libssc's wait dispatches the next method call re-entrantly

`handle_generic_method_call()` deliberately does not answer a `Claim*()`
before the first reading arrives (`src/iio-sensor-proxy.c:530-536`):

```c
/* Return the dbus invocation only after we receive the first
 * reading from the sensor, so that the client has a valid
 * measurement to work with right after calling Claim(). */
g_ptr_array_add (data->sensor_startup_dbus_invocations_delayed[driver_type],
                 invocation);
driver_set_polling (sensor_device, TRUE);
```

`driver_set_polling()` then calls `ssc_sensor_*_open_sync()` inline
(`src/drv-ssc-{light,proximity,accel,compass}.c`).  Because libssc's wait
iterates the **default** context, "the main loop" during that wait is the
libssc loop, and every pending source - including the GDBus idle that runs
the next `Claim*()` method call (`call_in_idle_cb`) - is dispatched *inside*
the first claim.  A client that claims several sensors at once (Phosh Mobile
Settings' Sensors page does) therefore nests one claim inside another: the
outer claim's `open_sync()` cannot return until the inner one returns, and the
inner one is the only thing that can dispatch anything.

libssc completes an enable only on a **sensor indication** (the
`SSC_MSG_RESPONSE_ENABLE_REPORT` configuration update, or the first
measurement; `sensor_open()` at `src/libssc-sensor.c:181-255`, completion at
`:457-500`).  The QMI acknowledgement of the enable request is *not* enough -
`sensor_open_ready()` (`:165-179`) only logs "Sensor enable request sent
successfully".  So an `open_sync()` that does not see that indication never
returns, and there is no timeout anywhere in 0.4.4.

That is the reported state: the inner (light) open never completed, the outer
(proximity) wait could not return because it was suspended inside the inner
call, and the busy loop turned the deadlock into 100 % CPU.  The sensors that
had already been opened kept working because the nested iteration is still the
same main context.

The same re-entrancy has already been reported upstream in a different
outcome: libssc issue #125 (open) shows `ClaimLight` dispatched inside a
`ReleaseLight -> ssc_sensor_light_close_sync -> g_main_context_iteration` wait,
and the resulting `g_task_return_boolean()` on the outer task segfaults
(backtrace frames `libssc-common.c:56`, `drv-ssc-light.c:94/104`).

### What is *not* proven

Whether, in the nested case, the SSC service never emits the awaited
indication for the inner sensor, or whether the indication was emitted and
lost inside the nested dispatch, cannot be decided from the five backtraces.
Both give the same observable (inner `open_sync` never returns).  Two
one-minute experiments on the phone discriminate, and neither changes the fix:

1. `sudo systemctl edit iio-sensor-proxy` with
   `[Service]` / `Environment=G_MESSAGES_DEBUG=all`, restart, open the
   Sensors page again, then read the journal: the last libssc line names the
   sensor and step ("Discovering sensor UID for data type 'rotv'",
   "Sensor enable request sent successfully", "Configuration updated for
   '<type>' sensor", "Measurement received for '<type>' sensor, assuming
   enabled").  If the enable is acked and no configuration/measurement line
   follows for that sensor, the DSP stayed quiet; if the line follows and the
   daemon still spins, the nested dispatch lost it.
2. Claim one sensor at a time with `busctl call net.hadess.SensorProxy
   /net/hadess/SensorProxy net.hadess.SensorProxy ClaimCompass` (then
   `ReleaseCompass`): non-nested claims are what Phosh does today and they
   complete, which is the basis for calling nesting the trigger.

### Unrelated observations from the report

* "Mount matrix provided by firmware is all 0, falling back to identity
  matrix!" twice: one warning per `ssc_sensor_*_new_sync()`
  (`src/libssc-sensor.c:400-421`), and the accelerometer's attributes are
  parsed twice at startup - once for the driver's `discover()`, once for its
  `open()`.  Two warnings therefore mean the accelerometer's discovery and
  open completed; they are not a symptom.
* `CompassHeading` staying at 0.0 means no compass reading arrived: Mobile
  Settings' `ClaimCompass()` is either queued behind the stuck claims or is
  itself another stalled open.
* The daemon had 3 eventfds and ~20 sockets; nothing in the trace suggests an
  fd storm.  The process is not blocked on a socket: it is spinning.

## Fix

Two local recipes, `pkgrel 100`, both against the exact versions that are on
the phone and now both in `flows/rc4/device/testing/`.  The first is the fix;
the second bounds the CPU cost of the synchronous calls that remain.  The
wiring is described in "How it is wired" below.

### `flows/rc4/device/testing/iio-sensor-proxy/` - the fix (3.9-r100)

`0001-drv-ssc-enable-and-disable-sensors-asynchronously.patch` replaces the
four `ssc_sensor_*_open_sync()/_close_sync()` calls in the drivers'
`set_polling()` with the async API plus a completion callback that only logs:

```c
static void
light_open_done (GObject *source, GAsyncResult *result, gpointer user_data)
{
	SSCSensorLight *sensor = SSC_SENSOR_LIGHT (source);
	g_autoptr(GError) error = NULL;

	if (!ssc_sensor_light_open_finish (sensor, result, &error))
		g_warning ("Opening SSC light sensor failed: %s",
		           error ? error->message : "UNKNOWN");
}

...
		/* Enable the sensor asynchronously.  The *_sync() helpers iterate
		 * the application's main context recursively while they wait, so
		 * calling one from this D-Bus method call handler re-enters the
		 * handler and dispatches the next pending method call (another
		 * Claim*() of the same client) inside the wait.  libssc's wait
		 * also polls without blocking, so every millisecond spent waiting
		 * burns a whole CPU core.  The first reading is what answers the
		 * delayed Claim() invocation, so nothing here needs to wait. */
		ssc_sensor_light_open (drv_data->sensor, NULL, light_open_done, NULL);
```

Why this is the right layer: `set_polling()` only used the return value for a
warning, the D-Bus reply already happens on the first reading, and with no
sync call in the handler there is no nested iteration at all - claims are
served one main-loop turn apart, in the order the GDBus idles run, and every
open completes the way it does for a single (Phosh-style) claim today.  It
also keeps working if libssc never answers: the daemon no longer blocks or
spins, it just logs nothing and the pending claim stays unanswered (the
pre-existing behaviour for an open that fails).

The completion callbacks also guard `error ? error->message : "UNKNOWN"`:
libssc 0.4.4 can return `FALSE` with a `NULL` `GError` (fixed upstream after
0.4.4, `f54187c`), and the old inline `g_warning(... error->message)` would
then dereference NULL - the crash in libssc issue #125.

### `flows/rc4/device/testing/libssc/` - the CPU bound (0.4.4-r100)

`0001-libssc-common-block-in-the-sync-wait.patch` changes the one
`g_main_context_iteration(..., FALSE)` in `ssc_common_wait_sync_context()` to
`TRUE`, with a comment.  Then a sync call that has to wait **blocks** instead
of burning a core; the sources of the default context are still dispatched
(so the SSC indications are processed, and `ssccli`/discovery keep working).

It does not by itself fix the stall - a blocking wait still dispatches the
next claim (a GDBus idle is always "ready"), so the deadlock would turn into a
silent hang instead of a hot loop.  Both recipes should go in together; the
daemon patch is what makes the claim path async, this one protects the
remaining synchronous users: the `*_new_sync()`/`*_close_sync()` discovery
pair in `*_discover()` (which runs at udev add and can also have a claim
dispatched inside it) and `ssccli`.

### Upstream state (see `evidence/upstream.md`)

No released version of either package fixes this.  libssc 0.4.4 is the newest
release; the only post-release fix in this area is `54dd13e` (a 30 s
wall-clock timeout while waiting for the SSC registry, closes issue #130
"Spins iio-sensor-proxy at 100%"), which does not apply to 0.4.4 with zero
fuzz and does not touch the wait loop.  iio-sensor-proxy 3.9 is the newest tag
and the SSC support is upstream since 3.9 (no pmaports patches any more);
nothing after 3.9 except a CI commit.

### Alternatives rejected

* **A private `GMainContext` pushed as thread-default in libssc's wait.**  The
  obvious "don't dispatch the application's sources" fix, but it needs the
  *whole* async machinery of the client to live in that context: libqrtr-glib
  and libqmi attach their sources to `g_main_context_get_thread_default()` at
  creation (`libqrtr-glib/qrtr-bus.c:415-420`, `libqmi-glib/qmi-device.c:1729`),
  and GTask completions go to the task's creation context.  Doing it right
  means a per-`SSCClient` context created and pushed for the whole client
  lifetime, plus passing it into every `ssc_common_*` helper and every
  `*_sync()` wrapper (9 files).  That is the more invasive change and it
  silently breaks the async API if a caller iterates the default context.
  Worth proposing upstream, not worth shipping from here untested.
* **Serialise claims in iio-sensor-proxy (queue + idle).**  Smaller, but not
  airtight: a claim that arrives while a queued `set_polling()` runs is still
  a pending GDBus idle and gets dispatched inside the wait.  The async patch
  removes the wait entirely, which is strictly better.
* **Backport `54dd13e` only.**  It bounds one of the two unbounded paths (the
  registry wait) but leaves both the claim-path deadlock and the busy loop.

### Why not a watchdog

An earlier draft shipped `nura-sensor-watchdog` (timer + CPU sampler +
`systemctl restart iio-sensor-proxy`).  Review dropped it: it hides the
symptom for two minutes per minute of a wedged daemon, restarts the daemon
into the same state when the trigger is reproducible, and adds an always-on
unit for a bug with an identified fix.  Until the recipes are wired, the
manual workaround is `sudo systemctl restart iio-sensor-proxy` (or do not open
the Sensors page with the current packages).

## Verification done here

All of it rootless, no phone, no pmbootstrap, no chroot, no build of an apk:

```
$ flows/rc4/topics/sensors/checks/verify.sh
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

* `sha512sum -c` runs against the tarballs `source=` names, in `source=`
  order: iio-sensor-proxy 3.9 `a89c842f...fda6` (identical to the aports
  3.9-r2 recipe) and libssc 0.4.4 `9240970f...c170` (identical to the aports
  0.4.4-r0 recipe).
* `patch -p1 -F0` (zero fuzz) on the *unpacked tarballs*, not on a git
  checkout.
* `cc -fsyntax-only -Wall -Wextra` on the four patched drivers against the
  real libssc 0.4.4 headers, and on the patched `libssc-common.c` against
  libqmi-glib/glib.  `-Wall -Wextra` is clean; this is a type/signature
  check, not a link or a full build (the host has no `meson` and no
  `libssc.pc`, and building the apk would need pmbootstrap).

Not verified, and why:

* the recipes were not built (`abuild`/pmbootstrap is out of scope for this
  task and no build may run here), so there is no `meson compile`/`meson
  test` evidence;
* the patched daemon was not run on the phone, so the claim path with real
  SSC hardware is untested - including the two experiments above.

The wiring below adds its own checks, which do run here:

```
$ PYTHONPATH=flows/rc4/local-storage/scripts python3 -m unittest flows.rc4.tests.test_rc4_sensor_ssc_fix
Ran 21 tests ... OK          # recipe content, patch hunks, links, pins, build order, marker check
$ bash flows/rc4/scripts/rc4.sh check
63 checks, 5 failed          # the 5 pre-existing kernel-recipe/label failures; both new checks PASS
```

## How it is wired

The two recipes live in `flows/rc4/device/testing/` and are wired exactly like
the patched `phoc` (Phosh-only) and `gmobile`:

1. **Recipes:** `flows/rc4/device/testing/iio-sensor-proxy/` (3.9-r100, the
   claim fix) and `flows/rc4/device/testing/libssc/` (0.4.4-r100, the blocking
   wait), each with its patch beside its APKBUILD.
2. **Recipe farm:** `flows/rc4/local-storage/device/testing/iio-sensor-proxy`
   and `.../libssc` are symlinks to those recipes (the iio-sensor-proxy link
   used to point at the rc3-era `flows/local-storage` tree).
3. **pmaports links** (`check_local_candidate.py`, `prepare_state()`):
   `links["main/libssc"]` and `links["main/iio-sensor-proxy"]`, libssc first
   because the daemon's makedepends include libssc-dev.  The pinned pmaports
   carries neither recipe and Alpine's `community/` builds are shadowed by
   these `main/` entries, so nothing else can win.
4. **Build order** (`rootfs_prepare_local.sh`): both are in the *shared*
   package loop (not in the Phosh-only `ui_builds`, because the device package
   depends on iio-sensor-proxy on Plasma too), with `libssc` immediately
   before `iio-sensor-proxy` so the daemon builds against the local library.
5. **Pins:** `install_add_string()` adds `libssc=$libssc_version`,
   `iio-sensor-proxy=$iio_sensor_proxy_version` and the two subpackages
   `iio-sensor-proxy-systemd` / `iio-sensor-proxy-udev` at the same r100 for
   both UIs, so apk cannot mix an r2 unit/udev rule with the r100 daemon; the
   two main packages are added to `WORLD_PINNED` and verified in
   `/etc/apk/world` like the kernel/device/firmware.
6. **Guards:** `run_install()` refuses any `iio-sensor-proxy` that is not
   `3.9-r100` and any `libssc` that is not `0.4.4-r100`, the same way it
   refuses a `phoc` that is not `0.58.0-r100`.
7. **Installed-rootfs proof:** `verify_sensor_drivers()` in
   `check_local_candidate.py` requires the daemon at
   `/usr/libexec/iio-sensor-proxy` and inspects its dynamic symbol names:
   no `ssc_sensor_*_open_sync` (the Alpine r2 claim path) and all
   `ssc_sensor_*_open_finish`/`_close_finish` (the patched async path).
   `*_close_sync` stays imported by `discover()` in both builds, so it is not
   a marker.  The apk-db check requires the daemon, its two subpackages and
   libssc at r100, and rejects any `iio-sensor-proxy-*`/`libssc-*` at another
   version.
8. **Tests:** `flows/rc4/tests/test_rc4_sensor_ssc_fix.py` pins the recipe
   contents, the patch hunks, the linking/pinning/build order and the
   `verify_sensor_drivers()` behaviour; `flows/rc4/scripts/check_recipe.py`
   (run by `rc4.sh check`) now also asserts that the link farm carries both
   r100 recipes.

On the phone: rebuild the image, open Mobile Settings -> Sensors, and watch
`top -p $(systemctl show -p MainPID --value iio-sensor-proxy)` stay at a few
percent (0-3 %), with `journalctl -u iio-sensor-proxy` showing the SSC
"Discovery"/"Configuration updated" lines.  Repeat with the page closed and
re-opened several times (claim/release cycles), then check the compass reports
(`CompassHeading` != 0) while the page is open.

## Files

```
README.md                                        this file
evidence/iio-bt-20261114.txt                     five gdb samples from the phone (coordinator)
evidence/libssc-0.4.4-r0-wait-loop.txt           shipped libssc wait loop: source, disassembly, address maths
evidence/upstream.md                             release/issue/commit state of libssc + iio-sensor-proxy
checks/verify.sh                                 sums + zero-fuzz apply + compile, reproducible
checks/RESULTS.md                                the commands and outputs above, with dates
```

The recipes themselves are wired into the image and live outside this topic:

```
flows/rc4/device/testing/iio-sensor-proxy/APKBUILD   3.9-r100 (the fix)
flows/rc4/device/testing/iio-sensor-proxy/0001-*.patch
flows/rc4/device/testing/libssc/APKBUILD             0.4.4-r100 (the CPU bound)
flows/rc4/device/testing/libssc/0001-*.patch
```
