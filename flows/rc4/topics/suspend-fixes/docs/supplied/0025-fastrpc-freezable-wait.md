# 0025: misc: fastrpc: let the freezer park a waiting invoke

- **Patch:** `kernel/0025-misc-fastrpc-freezable-invoke-wait.patch` (1 hunk, `drivers/misc/fastrpc.c`)
- **Fixes:** the sensor DSP (SLPI) crashing on every suspend.
- **Risk:** low. Only changes how a non-kernel FastRPC caller sleeps.

## Problem

`hexagonrpcd` (the sensor PD listener) is always blocked inside a FastRPC invoke, in `fastrpc_internal_invoke()` → `wait_for_completion_interruptible(&ctx->work)`. When system suspend freezes tasks, that wait is interrupted and returns `-ERESTARTSYS`. After thaw the syscall is restarted and sent as a **new message**. The DSP then sees two copies of its pending call and crashes 6 ms after "Restarting tasks":

```
EX:sensor_process:0x1:frpc_dsp:0x6c
```

Freezing tasks alone triggers it (`pm_test=freezer`).

**Why it matters for GN:** GN added SLPI sensors in commit `42e454f` (2026-10-14). GN's `CLAUDE.md` L64 says to never restart `hexagonrpcd` after it has attached, because the sensor PD is created on the first attach only and the sensors then stay dead until reboot. So one suspend kills auto-rotate and auto-brightness for the rest of the boot, and the SLPI crash/SSR runs in the middle of resume.

## Fix

```c
-		err = wait_for_completion_interruptible(&ctx->work);
+		err = wait_for_completion_state(&ctx->work,
+						TASK_INTERRUPTIBLE | TASK_FREEZABLE);
```

With `TASK_FREEZABLE`, the freezer freezes the sleeping caller in place instead of interrupting it, and a completion that arrives in the meantime is kept for the thaw. Real signals still interrupt the wait, because `TASK_INTERRUPTIBLE` is kept.

## Sources

- OM write-up: [`docs/sleep-20260927.md` "Why sleep crashed the sensor DSP" (L98–L113)](https://github.com/Cube-Oakley/omarchy-mobile/blob/f702f688288e59e3566f113d411ac7d18e4d0201/devices/oneplus7pro/docs/sleep-20260927.md#L98-L113)
- OM patch: [`kernel/sensors/fastrpc-freezable-wait.patch`](https://github.com/Cube-Oakley/omarchy-mobile/blob/f702f688288e59e3566f113d411ac7d18e4d0201/devices/oneplus7pro/kernel/sensors/fastrpc-freezable-wait.patch)
- GN sensors bring-up: commit [`42e454f`](https://github.com/emilyastranova/guacamole_nura/commit/42e454f), [`CLAUDE.md` L64](https://github.com/emilyastranova/guacamole_nura/blob/5b2241bb5c1bd0af2ce455a4efdabd7e8a5d9e24/CLAUDE.md#L64)

## Applies to

- 6.17 (`v6.17.0-sm8150`) after GN `0017`. The context is next to `0017`'s hunk, and `git am` is clean.
- 7.0 (`v7.0.0-sm8150`): clean. 7.0 still has the interruptible wait.
- Changes from OM: none to the code. A commit message and a `Source:` trailer were added.

## Verify

1. `echo freezer > /sys/power/pm_test; echo mem > /sys/power/state`
2. `cat /sys/class/remoteproc/*/state`: the SLPI remoteproc is still `running`, and dmesg has no `frpc_dsp` fatal.
3. `monitor-sensor` (iio-sensor-proxy) still reports the light sensor and accelerometer.

## Upstream

This looks upstreamable as is (`drivers/misc/fastrpc.c`, maintainers Srinivas Kandagatla et al.). It isn't specific to the device: any DSP listener blocked in an invoke would hit it.
