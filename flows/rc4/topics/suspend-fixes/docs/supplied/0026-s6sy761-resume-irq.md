# 0026: Input: s6sy761 - finish controller restart before enabling IRQ

- **Patch:** `kernel/0026-input-s6sy761-enable-irq-after-resume-restart.patch` (`drivers/input/touchscreen/s6sy761.c`)
- **Depends on:** GN `0005-input-s6sy761-add-optional-reset-gpio.patch`
- **Fixes:** the touchscreen being dead after wake (resume returns `-ENODEV`). This looks like a crash or hang to the user.
- **Risk:** low.

## Problem

GN's `0005` is the same reset-GPIO and bounded-poll patch OM uses (originally from Robin Snyders / hotdog), but OM needed a follow-up that GN doesn't have. In GN, after `0005`:

```c
static int s6sy761_resume(struct device *dev)
{
	struct s6sy761_data *sdata = dev_get_drvdata(dev);

	enable_irq(sdata->client->irq);      /* IRQ on first ...            */
	return s6sy761_power_on(sdata);      /* ... then restart + poll FIFO */
}
```

`s6sy761_power_on()` resets the controller and polls the event FIFO for `S6SY761_INFO_BOOT_COMPLETE`. The IRQ handler reads the same FIFO, so with the IRQ already enabled it can consume the boot-complete event. `power_on()` then times out and resume returns `-ENODEV`, which leaves touch dead. Suspend (`s6sy761_power_off()`) already calls `disable_irq()`, so the IRQ is off on entry to resume.

OM's first real suspend trial on guacamole: "the touchscreen resume callback returned -ENODEV". After this patch: "no new failed resume count", and the user confirmed "I woke it and touch still works."

## Fix

Run `s6sy761_power_on()` first. If the input device is open, re-send `S6SY761_SENSE_ON` (the open callback normally does this, but it does not run on system resume). Only then call `enable_irq()`.

## Sources

- OM write-up: [`docs/suspend-20260917.md` "RTC and first two suspend trials" (L51–L92)](https://github.com/Cube-Oakley/omarchy-mobile/blob/f702f688288e59e3566f113d411ac7d18e4d0201/devices/oneplus7pro/docs/suspend-20260917.md#L51-L92) and "Physical power-button wake passed"
- OM patch: [`kernel/touch/s6sy761-resume-irq.patch`](https://github.com/Cube-Oakley/omarchy-mobile/blob/f702f688288e59e3566f113d411ac7d18e4d0201/devices/oneplus7pro/kernel/touch/s6sy761-resume-irq.patch)
- GN base: [`0005-input-s6sy761-add-optional-reset-gpio.patch`](https://github.com/emilyastranova/guacamole_nura/blob/5b2241bb5c1bd0af2ce455a4efdabd7e8a5d9e24/device/testing/linux-postmarketos-qcom-sm8150/0005-input-s6sy761-add-optional-reset-gpio.patch). It is identical to OM [`kernel/touch/s6sy761-reset.patch`](https://github.com/Cube-Oakley/omarchy-mobile/blob/f702f688288e59e3566f113d411ac7d18e4d0201/devices/oneplus7pro/kernel/touch/s6sy761-reset.patch) apart from the author line.

## Applies to

- 6.17 after GN `0005`: clean. The OM patch applied with an offset, and this export is rebased so it has none.
- 7.0 after GN `0005`: clean.
- Not taken from OM: `kernel/touch/s6sy761-guacamole-probe.patch`. OM labels it a diagnostic for live overlay probing, and it is not needed for resume.

## Verify

Suspend and wake with the power key, then:
- `cat /sys/power/suspend_stats/failed_resume` does not go up.
- dmesg has no `s6sy761 ... -19` / `PM: failed to resume`.
- Touch works in Phosh without restarting anything. If the kernel resume is clean but the compositor lost the device, see the userspace note in `00-diagnose.md` section 5.
