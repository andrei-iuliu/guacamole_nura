# rc4 topic: iio-sensor-proxy spins one CPU core after Mobile Settings -> Sensors

Status: **root cause identified, fix prepared as two local recipes, not built,
not wired into the image, not tested on the phone.**  The only files this
topic changes outside `flows/rc4/topics/sensors/` are one section in
`docs/NURA_PHOSH.md` and `flows/rc4/tests/test_rc4_sensor_ssc_fix.py`; the
device package is untouched (no pkgrel bump - the always-on watchdog
mitigation from the first draft was dropped after review, see "Why not a
watchdog").

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
the phone.  The first is the fix; the second bounds the CPU cost of the
synchronous calls that remain.

### `recipe/iio-sensor-proxy/` - the fix (iio-sensor-proxy 3.9-r100)

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

### `recipe/libssc/` - the CPU bound (libssc 0.4.4-r100)

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
  SSC hardware is untested - including the two experiments above;
* nothing was wired into the image (see next section).

## How to wire it (not done here)

The coordinator asked for the fix to be wired only if it is small and clearly
correct.  The code change is small and compile-checked, but the only proof
that it fixes a DSP-side claim needs the phone, and a wrong daemon patch would
take the sensors out of a daily-use image, so this topic stops at a tested
recipe.  Wiring is mechanical; most of it already exists for
iio-sensor-proxy on this branch:

1. **iio-sensor-proxy (the fix):** copy `recipe/iio-sensor-proxy/` to
   `flows/rc4/device/testing/iio-sensor-proxy/` (APKBUILD + patch, the way
   `device/testing/gmobile` keeps its patch beside its recipe).  The flow
   already expects that directory: `check_local_candidate.py:944` maps
   `main/iio-sensor-proxy` to `device/testing/iio-sensor-proxy` and
   `rootfs_prepare_local.sh:1409` already builds `iio-sensor-proxy` for both
   UIs, so the recipe alone wires it.  (On this branch the older
   `flows/rc4/local-storage/device/testing/iio-sensor-proxy` link still points
   at `flows/local-storage/...`; repoint it at the new recipe like `gmobile`
   does, or drop it.)
2. **libssc (the CPU bound):** copy `recipe/libssc/` to
   `flows/rc4/device/testing/libssc/`, then add it to the flow:
   * `check_local_candidate.py`, `prepare_state()` next to
     `links["main/iio-sensor-proxy"]` (line 944):
     `links["main/libssc"] = "device/testing/libssc"`;
   * `rootfs_prepare_local.sh:1408-1411`: add `libssc` to the package loop
     **before** `iio-sensor-proxy` (it is a makedepend of the daemon).
3. **Keep the versions from drifting:** add
   `IIO_SENSOR_PROXY_VERSION = "3.9-r100"` and `LIBSSC_VERSION = "0.4.4-r100"`
   next to `GMOBILE_VERSION`/`PHOC_VERSION` (`check_local_candidate.py:130`),
   feed them into the installed-version check (line 1367) and the manifest
   (line 1458), extend the world pins in `rootfs_prepare_local.sh` (around
   line 1314) and add the same guards as the `phoc 0.58.0-r100` one (line
   1482), so apk cannot silently keep a repository build.
4. **Prove the right binary is installed:** add a
   `verify_sensor_drivers()` next to `verify_phoc_guard()`
   (`check_local_candidate.py:1268`) that fails unless
   `/usr/libexec/iio-sensor-proxy` imports `ssc_sensor_light_open` /
   `ssc_sensor_proximity_open` and **not** `ssc_sensor_light_open_sync` /
   `ssc_sensor_proximity_open_sync` (`objdump -T` on the installed binary; the
   sync symbols are exported by libssc, the async ones by the daemon, so the
   difference is unambiguous).
5. **Docs/tests:** `flows/rc4/tests/test_rc4_sensor_ssc_fix.py` already pins
   the recipe content; extend it (and `test_rc4_integration.py`) with the two
   link-farm entries and the version guards when the recipes move, and drop
   the "not wired" wording here.
6. **On the phone:** rebuild the image, open Mobile Settings -> Sensors, and
   watch `top -p $(systemctl show -p MainPID --value iio-sensor-proxy)` stay
   at a few percent, with `journalctl -u iio-sensor-proxy` showing the SSC
   "Discovery"/"Configuration updated" lines.  Repeat with the page closed
   and re-opened several times (claim/release cycles), then check the compass
   reports (`CompassHeading` != 0) while the page is open.

## Files

```
README.md                                        this file
evidence/iio-bt-20261114.txt                     five gdb samples from the phone (coordinator)
evidence/libssc-0.4.4-r0-wait-loop.txt           shipped libssc wait loop: source, disassembly, address maths
evidence/upstream.md                             release/issue/commit state of libssc + iio-sensor-proxy
recipe/iio-sensor-proxy/APKBUILD                  3.9-r100 (fix), source= + sha512sums in order
recipe/iio-sensor-proxy/0001-*.patch             async enable/disable in the four SSC drivers
recipe/libssc/APKBUILD                           0.4.4-r100 (CPU bound)
recipe/libssc/0001-*.patch                       may_block=TRUE in ssc_common_wait_sync_context()
checks/verify.sh                                 sums + zero-fuzz apply + compile, reproducible
checks/RESULTS.md                                the commands and outputs above, with dates
```
