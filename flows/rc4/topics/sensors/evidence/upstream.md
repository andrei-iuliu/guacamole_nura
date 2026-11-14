Upstream state (checked 2026-11-14)
==================================

libssc (codeberg.org/DylanVanAssche/libssc, mirrored from
gitlab.com/postmarketOS/libssc)
---------------------------------------------------------------------------
* Releases: v0.4.4 (2026-06-27) is the newest; the phone runs 0.4.4-r0.
  After it, `main` has 10 commits (0.4.4-8-g54dd13e as of the clone).
* The busy wait was introduced by the 0.4.0 synchronous rework,
  `6e6d92d libssc-common: rework synchronous handling` (merged as PR #114,
  "Rework synchronous API handling", 2026-02-10, released in 0.4.0).  Before
  0.4.0 each sensor had its own GMainContext plus `g_main_loop_run()`, i.e. a
  *blocking* wait; the rework replaced that with
  `g_main_context_iteration(g_main_context_default(), FALSE)` on the shared
  default context.
* Issue #130 "Spins iio-sensor-proxy at 100%" (2026-09-03, Guido Günther)
  is the same symptom: backtrace inside `ssc_sensor_light_new_sync` ->
  libssc -> `g_main_context_iteration` -> `ppoll`, on a device with no
  hexagonrpcd, i.e. "discovery never completes".  It is open.
* The only fix on main is
  `54dd13e sensor: Give up when server never becomes available` (2026-09-04,
  Guido Günther), which adds a 30 s wall-clock timeout while waiting for the
  SSC "registry" sensor and closes #130.  It does **not** touch the wait
  loop, and it does **not** apply to v0.4.4 with zero fuzz: it is based on
  post-0.4.4 refactors (`g_clear_signal_handler`, `g_clear_handle_id`,
  attributes/measurement branch layout), so the 0.4.4 tree needs a backport,
  not a cherry-pick.  (`git apply --check` fails at src/libssc-sensor.c:311;
  `patch -F3` succeeds with fuzz 3 on 5 hunks.)
* Issue #125 "Crashes iio-sensor-proxy on config update" (2026-06-20, same
  author, open) is the same re-entrancy in a different outcome: the
  backtrace shows `ClaimLight` being dispatched *inside* a
  `ReleaseLight -> ssc_sensor_light_close_sync -> g_main_context_iteration`
  wait, and the resulting `g_task_return_boolean()` on the outer task
  segfaults.  Its frames are `libssc-common.c:56` (this loop) and
  `drv-ssc-light.c:94/104` -- the same two functions as the rc4j report.
* PR #104 / issue #67 (QMI client-ID exhaustion) is a separate, older
  problem: libssc did not release QMI clients reliably.  It is not the cause
  here (the rc4j daemon had been running for hours with its four clients).

iio-sensor-proxy (gitlab.freedesktop.org/hadess/iio-sensor-proxy)
---------------------------------------------------------------------------
* 3.9 (2025-…, the newest tag) is the first release with SSC support
  upstream, so the pmaports SSC patches are gone; Alpine's
  community/iio-sensor-proxy 3.9-r2 is the plain upstream tarball plus the
  OpenRC initd.  The phone runs exactly that.
* Nothing after 3.9 except a CI commit (`ca0d5b0`), so there is no upstream
  fix for the claim/handler re-entrancy.
* The SSC driver code is unchanged since 3.9 and still does:
  `handle_method_call(ClaimLight)` ->
  `ssc_light_set_polling(TRUE)` -> `ssc_sensor_light_open_sync()`
  on the main loop (src/drv-ssc-light.c:81-105,
  src/iio-sensor-proxy.c:490-600).

Conclusion: no released version of either package fixes this; both recipes
in ../recipe/ are local (pkgrel 100) proposals.
