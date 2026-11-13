// SPDX-License-Identifier: GPL-2.0-only
/*
 * Bounded bring-up driver for the OnePlus 7T Pro pop-up camera motor.
 */

#include <linux/cleanup.h>
#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/iio/consumer.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/pinctrl/consumer.h>
#include <linux/platform_device.h>
#include <linux/pm_domain.h>
#include <linux/pwm.h>

#define HOTDOG_PREFLIGHT_MICROSTEPS	320
#define HOTDOG_CLOSED_DOWN_MIN		300
#define HOTDOG_CLOSED_UP_MAX		50
#define HOTDOG_RESTORE_DOWN_MIN		340
#define HOTDOG_RESTORE_DOWN_MAX		368
#define HOTDOG_SETTLED_DOWN_MIN		350
#define HOTDOG_RECOVER_MICROSTEPS	1920
#define HOTDOG_RECOVER_DOWN_MIN		320
#define HOTDOG_RECOVER_DOWN_MAX		360
#define HOTDOG_OPEN_DOWN_MIN		368
#define HOTDOG_FULL_COURSE_MICROSTEPS	(1380 * 32)
#define HOTDOG_APPROACH_MICROSTEPS	31680
#define HOTDOG_FINISH_OPEN_MICROSTEPS	(HOTDOG_FULL_COURSE_MICROSTEPS - \
					 HOTDOG_APPROACH_MICROSTEPS)
#define HOTDOG_FINISH_UP_MIN		130
#define HOTDOG_FINISH_UP_MAX		200
#define HOTDOG_FINISH_DOWN_MAX		50
#define HOTDOG_HALL_SAMPLE_MICROSTEPS	64
#define HOTDOG_PROGRESS_MIN_COUNTS	2
#define HOTDOG_OPEN_HALL_MIN		150
#define HOTDOG_HALL_ABS_MAX		450
#define HOTDOG_HALL_SUM_MIN		4
#define HOTDOG_FULL_OPEN_UP_MIN		300
/* Exact periods supported by PM8150L LPG near the downstream 104166/12000 ns. */
#define HOTDOG_SLOW_PERIOD_NS		105000
#define HOTDOG_FAST_PERIOD_NS		13125
#define HOTDOG_RAMP_MICROSTEPS		(30 * 32)
#define HOTDOG_PROGRESS_PROBE_MICROSTEPS	(100 * 32)
#define HOTDOG_HALL_POLL_US		15000

struct hotdog_popup {
	struct device *dev;
	struct gpio_desc *boost;
	struct gpio_desc *direction;
	struct gpio_desc *mode1;
	struct gpio_desc *sleep;
	struct gpio_desc *step;
	struct pwm_device *step_pwm;
	struct pinctrl *pinctrl;
	struct pinctrl_state *step_gpio_state;
	struct pinctrl_state *step_pwm_state;
	struct iio_channel *hall_up;
	struct iio_channel *hall_down;
	struct generic_pm_domain genpd;
	struct mutex lock;
	bool preflight_used;
	bool restore_used;
	bool open_used;
	bool finish_open_used;
	bool close_used;
	bool endpoint_reached;
	unsigned int last_steps;
	int before_up;
	int before_down;
	int after_up;
	int after_down;
	int last_error;
	unsigned int automatic_open_count;
	unsigned int automatic_close_count;
};

static bool hotdog_popup_is_closed(int up, int down)
{
	return abs(up) < HOTDOG_CLOSED_UP_MAX &&
	       abs(down) >= HOTDOG_RESTORE_DOWN_MIN;
}

static bool hotdog_popup_is_open(int up, int down)
{
	return abs(up) >= HOTDOG_FULL_OPEN_UP_MIN &&
	       abs(down) <= HOTDOG_FINISH_DOWN_MAX;
}

static void hotdog_popup_pwm_disable(struct hotdog_popup *popup)
{
	struct pwm_state state;

	if (popup->step_pwm) {
		pwm_get_state(popup->step_pwm, &state);
		state.enabled = false;
		pwm_apply_might_sleep(popup->step_pwm, &state);
	}

	if (popup->pinctrl && popup->step_gpio_state)
		pinctrl_select_state(popup->pinctrl, popup->step_gpio_state);

	if (popup->step)
		gpiod_set_value_cansleep(popup->step, 0);
}

static int hotdog_popup_pwm_enable(struct hotdog_popup *popup, u64 period)
{
	struct pwm_state state;
	int ret;

	ret = pinctrl_select_state(popup->pinctrl, popup->step_pwm_state);
	if (ret)
		return ret;

	pwm_init_state(popup->step_pwm, &state);
	state.period = period;
	state.duty_cycle = period / 2;
	state.polarity = PWM_POLARITY_NORMAL;
	/* LPG needs the new period committed while disabled before restarting. */
	state.enabled = false;
	ret = pwm_apply_might_sleep(popup->step_pwm, &state);
	if (ret)
		goto err_disable;

	state.enabled = true;
	ret = pwm_apply_might_sleep(popup->step_pwm, &state);
	if (ret)
		goto err_disable;

	return 0;

err_disable:
	hotdog_popup_pwm_disable(popup);
	return ret;
}

static bool hotdog_popup_halls_plausible(int up, int down)
{
	/* Both endpoint sensors are naturally weak around mid-travel. */
	return abs(up) <= HOTDOG_HALL_ABS_MAX &&
	       abs(down) <= HOTDOG_HALL_ABS_MAX &&
	       abs(up) + abs(down) >= HOTDOG_HALL_SUM_MIN;
}

static void hotdog_popup_stop(struct hotdog_popup *popup)
{
	hotdog_popup_pwm_disable(popup);
	gpiod_set_value_cansleep(popup->sleep, 0);
	gpiod_set_value_cansleep(popup->mode1, 0);
	gpiod_set_value_cansleep(popup->direction, 0);
}

static int hotdog_popup_read_halls(struct hotdog_popup *popup,
				   int *up, int *down)
{
	int ret;

	ret = iio_read_channel_raw(popup->hall_up, up);
	if (ret < 0)
		return ret;

	ret = iio_read_channel_raw(popup->hall_down, down);
	if (ret < 0)
		return ret;

	return 0;
}

static void hotdog_popup_power_on(struct hotdog_popup *popup, bool upward)
{
	/* FI5 direction is physically low for up and high for down. */
	gpiod_set_value_cansleep(popup->direction, !upward);
	gpiod_set_value_cansleep(popup->mode1, 1);
	gpiod_set_value_cansleep(popup->sleep, 1);
	usleep_range(1000, 1500);
}

static int hotdog_popup_drive_exact(struct hotdog_popup *popup, bool upward,
				    unsigned int microsteps)
{
	u64 duration_us;
	int ret;

	hotdog_popup_power_on(popup, upward);
	ret = hotdog_popup_pwm_enable(popup, HOTDOG_SLOW_PERIOD_NS);
	if (ret)
		goto out;

	duration_us = DIV_ROUND_UP_ULL((u64)microsteps *
					HOTDOG_SLOW_PERIOD_NS, NSEC_PER_USEC);
	usleep_range(duration_us, duration_us + 100);

out:
	hotdog_popup_stop(popup);
	return ret;
}

static int hotdog_popup_preflight_up(struct hotdog_popup *popup)
{
	int ret;

	ret = hotdog_popup_read_halls(popup, &popup->before_up,
				       &popup->before_down);
	if (ret)
		return ret;

	if (abs(popup->before_down) < HOTDOG_CLOSED_DOWN_MIN ||
	    abs(popup->before_up) >= HOTDOG_CLOSED_UP_MAX)
		return -ERANGE;

	/* DRV8834 1/32 mode: M0 is high-impedance in pinctrl, M1 is high. */
	ret = hotdog_popup_drive_exact(popup, true,
				       HOTDOG_PREFLIGHT_MICROSTEPS);
	if (ret)
		return ret;
	msleep(20);

	ret = hotdog_popup_read_halls(popup, &popup->after_up,
				       &popup->after_down);
	if (ret)
		return ret;

	dev_info(popup->dev,
		 "preflight up: %d microsteps, Hall up %d -> %d, down %d -> %d\n",
		 HOTDOG_PREFLIGHT_MICROSTEPS, popup->before_up, popup->after_up,
		 popup->before_down, popup->after_down);

	return 0;
}

static int hotdog_popup_restore_closed(struct hotdog_popup *popup,
				       unsigned int microsteps)
{
	int ret;

	ret = hotdog_popup_read_halls(popup, &popup->before_up,
				       &popup->before_down);
	if (ret)
		return ret;

	if (abs(popup->before_up) >= HOTDOG_CLOSED_UP_MAX)
		return -ERANGE;

	if (microsteps == HOTDOG_PREFLIGHT_MICROSTEPS &&
	    (abs(popup->before_down) < HOTDOG_RESTORE_DOWN_MIN ||
	     abs(popup->before_down) > HOTDOG_RESTORE_DOWN_MAX))
		return -ERANGE;

	if (microsteps == HOTDOG_RECOVER_MICROSTEPS &&
	    (abs(popup->before_down) < HOTDOG_RECOVER_DOWN_MIN ||
	     abs(popup->before_down) > HOTDOG_RECOVER_DOWN_MAX))
		return -ERANGE;

	ret = hotdog_popup_drive_exact(popup, false, microsteps);
	if (ret)
		return ret;
	msleep(20);

	ret = hotdog_popup_read_halls(popup, &popup->after_up,
				       &popup->after_down);
	if (ret)
		return ret;

	if (abs(popup->after_down) <= abs(popup->before_down))
		return -EPROTO;

	popup->last_steps = microsteps;
	dev_info(popup->dev,
		 "restore closed: %u microsteps, Hall up %d -> %d, down %d -> %d\n",
		 microsteps, popup->before_up, popup->after_up,
		 popup->before_down, popup->after_down);

	return 0;
}

static bool hotdog_popup_motion_endpoint(bool upward, int up, int down)
{
	return upward ? hotdog_popup_is_open(up, down) :
			 hotdog_popup_is_closed(up, down);
}

static int hotdog_popup_move_profile(struct hotdog_popup *popup, bool upward)
{
	int current_up;
	int current_down;
	bool progress;
	u64 remaining_ns;
	u64 sleep_us;
	int ret;

	ret = hotdog_popup_read_halls(popup, &popup->before_up,
				       &popup->before_down);
	if (ret)
		return ret;
	if (hotdog_popup_motion_endpoint(upward, popup->before_up,
					 popup->before_down)) {
		popup->after_up = popup->before_up;
		popup->after_down = popup->before_down;
		popup->endpoint_reached = true;
		popup->last_steps = 0;
		return 0;
	}

	if (upward && !hotdog_popup_is_closed(popup->before_up,
					      popup->before_down))
		return -ERANGE;
	if (!upward &&
	    !hotdog_popup_halls_plausible(popup->before_up, popup->before_down))
		return -ERANGE;

	popup->endpoint_reached = false;
	popup->last_steps = 0;
	hotdog_popup_power_on(popup, upward);

	ret = hotdog_popup_pwm_enable(popup, HOTDOG_SLOW_PERIOD_NS);
	if (ret)
		goto out_stop;

	/*
	 * The Hall sensors are flat close to either mechanical endpoint. Let the
	 * complete stock slow-start ramp run before deciding that the motor did
	 * not move; a 320-pulse check produces false stalls when closing a fully
	 * extended camera.
	 */
	sleep_us = DIV_ROUND_UP_ULL((u64)HOTDOG_RAMP_MICROSTEPS *
					HOTDOG_SLOW_PERIOD_NS, NSEC_PER_USEC);
	usleep_range(sleep_us, sleep_us + 100);
	popup->last_steps = HOTDOG_RAMP_MICROSTEPS;

	ret = hotdog_popup_read_halls(popup, &current_up, &current_down);
	if (ret)
		goto out_stop;
	popup->after_up = current_up;
	popup->after_down = current_down;
	progress = upward ?
		current_down - popup->before_down >= HOTDOG_PROGRESS_MIN_COUNTS :
		abs(current_down) - abs(popup->before_down) >=
			HOTDOG_PROGRESS_MIN_COUNTS ||
		abs(popup->before_up) - abs(current_up) >=
			HOTDOG_PROGRESS_MIN_COUNTS;

	ret = hotdog_popup_pwm_enable(popup, HOTDOG_FAST_PERIOD_NS);
	if (ret)
		goto out_stop;

	/*
	 * Both Hall sensors are flat at the mechanical endpoints.  The stock
	 * driver accelerates unconditionally after its slow ramp, so allow a
	 * short, bounded fast probe to clear endpoint backlash before declaring
	 * a stall.  A real obstruction is still stopped far short of a course.
	 */
	if (!progress) {
		sleep_us = DIV_ROUND_UP_ULL(
			(u64)HOTDOG_PROGRESS_PROBE_MICROSTEPS *
			HOTDOG_FAST_PERIOD_NS, NSEC_PER_USEC);
		usleep_range(sleep_us, sleep_us + 100);
		popup->last_steps += HOTDOG_PROGRESS_PROBE_MICROSTEPS;

		ret = hotdog_popup_read_halls(popup, &current_up, &current_down);
		if (ret)
			goto out_stop;
		popup->after_up = current_up;
		popup->after_down = current_down;
		progress = upward ?
			current_down - popup->before_down >=
				HOTDOG_PROGRESS_MIN_COUNTS :
			abs(current_down) - abs(popup->before_down) >=
				HOTDOG_PROGRESS_MIN_COUNTS ||
			abs(popup->before_up) - abs(current_up) >=
				HOTDOG_PROGRESS_MIN_COUNTS;
		if (!progress) {
			ret = -EPROTO;
			goto out_stop;
		}
	}

	remaining_ns = (u64)(HOTDOG_FULL_COURSE_MICROSTEPS -
				 popup->last_steps) * HOTDOG_FAST_PERIOD_NS;
	while (remaining_ns) {
		u64 interval_ns = min_t(u64, remaining_ns,
					 (u64)HOTDOG_HALL_POLL_US * NSEC_PER_USEC);

		sleep_us = DIV_ROUND_UP_ULL(interval_ns, NSEC_PER_USEC);
		usleep_range(sleep_us, sleep_us + 100);
		remaining_ns -= interval_ns;
		popup->last_steps = HOTDOG_FULL_COURSE_MICROSTEPS -
			DIV_ROUND_UP_ULL(remaining_ns, HOTDOG_FAST_PERIOD_NS);

		ret = hotdog_popup_read_halls(popup, &current_up, &current_down);
		if (ret)
			goto out_stop;
		popup->after_up = current_up;
		popup->after_down = current_down;
		if (!hotdog_popup_halls_plausible(current_up, current_down)) {
			ret = -ERANGE;
			goto out_stop;
		}
		if (hotdog_popup_motion_endpoint(upward, current_up,
						  current_down)) {
			popup->endpoint_reached = true;
			ret = 0;
			goto out_stop;
		}
	}

	ret = -ERANGE;

out_stop:
	hotdog_popup_stop(popup);
	msleep(20);
	if (!hotdog_popup_read_halls(popup, &current_up, &current_down)) {
		popup->after_up = current_up;
		popup->after_down = current_down;
	}

	dev_info(popup->dev,
		 "%s stopped: steps=%u endpoint=%u error=%d Hall up %d -> %d, down %d -> %d\n",
		 upward ? "open" : "close",
		 popup->last_steps, popup->endpoint_reached, ret,
		 popup->before_up, popup->after_up, popup->before_down,
		 popup->after_down);

	return ret;
}

static int hotdog_popup_open(struct hotdog_popup *popup)
{
	return hotdog_popup_move_profile(popup, true);
}

static int hotdog_popup_finish_open(struct hotdog_popup *popup)
{
	int ret;

	ret = hotdog_popup_read_halls(popup, &popup->before_up,
				       &popup->before_down);
	if (ret)
		return ret;

	if (abs(popup->before_up) < HOTDOG_FINISH_UP_MIN ||
	    abs(popup->before_up) > HOTDOG_FINISH_UP_MAX ||
	    abs(popup->before_down) > HOTDOG_FINISH_DOWN_MAX)
		return -ERANGE;

	ret = hotdog_popup_drive_exact(popup, true,
				       HOTDOG_FINISH_OPEN_MICROSTEPS);
	if (ret)
		return ret;
	msleep(20);

	ret = hotdog_popup_read_halls(popup, &popup->after_up,
				       &popup->after_down);
	if (ret)
		return ret;

	popup->last_steps = HOTDOG_FINISH_OPEN_MICROSTEPS;
	popup->endpoint_reached = abs(popup->after_up) >= HOTDOG_OPEN_HALL_MIN;
	if (!popup->endpoint_reached)
		return -ERANGE;

	dev_info(popup->dev,
		 "finish open: %u microsteps, Hall up %d -> %d, down %d -> %d\n",
		 HOTDOG_FINISH_OPEN_MICROSTEPS, popup->before_up,
		 popup->after_up, popup->before_down, popup->after_down);

	return 0;
}

static int hotdog_popup_close(struct hotdog_popup *popup)
{
	unsigned int close_steps;
	int ret;

	ret = hotdog_popup_move_profile(popup, false);
	if (ret || abs(popup->after_down) >= HOTDOG_SETTLED_DOWN_MIN)
		return ret;

	close_steps = popup->last_steps;
	ret = hotdog_popup_restore_closed(popup,
					 HOTDOG_PREFLIGHT_MICROSTEPS);
	if (!ret)
		popup->last_steps += close_steps;

	return ret;
}

static int hotdog_popup_close_with_retry(struct hotdog_popup *popup)
{
	int ret;

	ret = hotdog_popup_close(popup);
	if (ret != -EPROTO)
		return ret;

	/* The LPG/DRV8834 pair can ignore the first ramp after a fast stop. */
	dev_info(popup->dev, "close ramp did not move; retrying once\n");
	msleep(250);
	return hotdog_popup_close(popup);
}

static int hotdog_popup_domain_power_on(struct generic_pm_domain *domain)
{
	struct hotdog_popup *popup =
		container_of(domain, struct hotdog_popup, genpd);
	int recovery_ret;
	int ret;

	guard(mutex)(&popup->lock);
	popup->automatic_open_count++;
	ret = hotdog_popup_open(popup);
	if (ret) {
		/* Never leave a failed opening at an unknown intermediate point. */
		recovery_ret = hotdog_popup_close_with_retry(popup);
		if (recovery_ret)
			dev_err(popup->dev,
				"automatic open failed (%d), close recovery failed (%d)\n",
				ret, recovery_ret);
	}

	hotdog_popup_stop(popup);
	popup->last_error = ret;
	return ret;
}

static int hotdog_popup_domain_power_off(struct generic_pm_domain *domain)
{
	struct hotdog_popup *popup =
		container_of(domain, struct hotdog_popup, genpd);
	int ret;

	guard(mutex)(&popup->lock);
	popup->automatic_close_count++;
	ret = hotdog_popup_close_with_retry(popup);
	hotdog_popup_stop(popup);
	popup->last_error = ret;
	return ret;
}

static ssize_t status_show(struct device *dev,
			   struct device_attribute *attr, char *buf)
{
	struct hotdog_popup *popup = dev_get_drvdata(dev);
	int up;
	int down;
	int ret;

	guard(mutex)(&popup->lock);
	ret = hotdog_popup_read_halls(popup, &up, &down);
	if (ret)
		return ret;

	return sysfs_emit(buf,
		"preflight_used=%u restore_used=%u open_used=%u finish_open_used=%u close_used=%u automatic_open_count=%u automatic_close_count=%u endpoint=%u error=%d hall_up=%d hall_down=%d before_up=%d before_down=%d after_up=%d after_down=%d last_steps=%u preflight_microsteps=%u course_limit=%u\n",
		popup->preflight_used, popup->restore_used, popup->open_used,
		popup->finish_open_used, popup->close_used,
		popup->automatic_open_count, popup->automatic_close_count,
		popup->endpoint_reached, popup->last_error, up, down,
		popup->before_up, popup->before_down, popup->after_up,
		popup->after_down, popup->last_steps,
		HOTDOG_PREFLIGHT_MICROSTEPS, HOTDOG_FULL_COURSE_MICROSTEPS);
}
static DEVICE_ATTR_RO(status);

static ssize_t pulse_up_store(struct device *dev,
			      struct device_attribute *attr,
			      const char *buf, size_t count)
{
	struct hotdog_popup *popup = dev_get_drvdata(dev);
	unsigned int microsteps;
	int ret;

	ret = kstrtouint(buf, 0, &microsteps);
	if (ret)
		return ret;
	if (microsteps != HOTDOG_PREFLIGHT_MICROSTEPS)
		return -EINVAL;

	guard(mutex)(&popup->lock);
	if (popup->preflight_used)
		return -EALREADY;

	popup->preflight_used = true;
	ret = hotdog_popup_preflight_up(popup);
	hotdog_popup_stop(popup);
	popup->last_error = ret;
	if (ret)
		return ret;

	return count;
}
static DEVICE_ATTR_WO(pulse_up);

static ssize_t restore_closed_store(struct device *dev,
				    struct device_attribute *attr,
				    const char *buf, size_t count)
{
	struct hotdog_popup *popup = dev_get_drvdata(dev);
	unsigned int microsteps;
	int ret;

	ret = kstrtouint(buf, 0, &microsteps);
	if (ret)
		return ret;
	if (microsteps != HOTDOG_PREFLIGHT_MICROSTEPS &&
	    microsteps != HOTDOG_RECOVER_MICROSTEPS)
		return -EINVAL;

	guard(mutex)(&popup->lock);
	if (popup->restore_used)
		return -EALREADY;

	popup->restore_used = true;
	ret = hotdog_popup_restore_closed(popup, microsteps);
	hotdog_popup_stop(popup);
	popup->last_error = ret;
	if (ret)
		return ret;

	return count;
}
static DEVICE_ATTR_WO(restore_closed);

static ssize_t open_store(struct device *dev,
			  struct device_attribute *attr,
			  const char *buf, size_t count)
{
	struct hotdog_popup *popup = dev_get_drvdata(dev);
	unsigned int limit;
	int ret;

	ret = kstrtouint(buf, 0, &limit);
	if (ret)
		return ret;
	if (limit != HOTDOG_FULL_COURSE_MICROSTEPS)
		return -EINVAL;

	guard(mutex)(&popup->lock);
	if (popup->open_used)
		return -EALREADY;

	popup->open_used = true;
	ret = hotdog_popup_open(popup);
	hotdog_popup_stop(popup);
	popup->last_error = ret;
	if (ret)
		return ret;

	return count;
}
static DEVICE_ATTR_WO(open);

static ssize_t finish_open_store(struct device *dev,
				 struct device_attribute *attr,
				 const char *buf, size_t count)
{
	struct hotdog_popup *popup = dev_get_drvdata(dev);
	unsigned int microsteps;
	int ret;

	ret = kstrtouint(buf, 0, &microsteps);
	if (ret)
		return ret;
	if (microsteps != HOTDOG_FINISH_OPEN_MICROSTEPS)
		return -EINVAL;

	guard(mutex)(&popup->lock);
	if (popup->finish_open_used)
		return -EALREADY;

	popup->finish_open_used = true;
	ret = hotdog_popup_finish_open(popup);
	hotdog_popup_stop(popup);
	popup->last_error = ret;
	if (ret)
		return ret;

	return count;
}
static DEVICE_ATTR_WO(finish_open);

static ssize_t close_store(struct device *dev,
			   struct device_attribute *attr,
			   const char *buf, size_t count)
{
	struct hotdog_popup *popup = dev_get_drvdata(dev);
	unsigned int limit;
	int ret;

	ret = kstrtouint(buf, 0, &limit);
	if (ret)
		return ret;
	if (limit != HOTDOG_FULL_COURSE_MICROSTEPS)
		return -EINVAL;

	guard(mutex)(&popup->lock);
	if (popup->close_used)
		return -EALREADY;

	popup->close_used = true;
	ret = hotdog_popup_close(popup);
	hotdog_popup_stop(popup);
	popup->last_error = ret;
	if (ret)
		return ret;

	return count;
}
static DEVICE_ATTR_WO(close);

static struct attribute *hotdog_popup_attrs[] = {
	&dev_attr_status.attr,
	&dev_attr_pulse_up.attr,
	&dev_attr_restore_closed.attr,
	&dev_attr_open.attr,
	&dev_attr_finish_open.attr,
	&dev_attr_close.attr,
	NULL,
};
ATTRIBUTE_GROUPS(hotdog_popup);

static int hotdog_popup_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct hotdog_popup *popup;
	int ret;

	popup = devm_kzalloc(dev, sizeof(*popup), GFP_KERNEL);
	if (!popup)
		return -ENOMEM;

	popup->dev = dev;
	mutex_init(&popup->lock);
	platform_set_drvdata(pdev, popup);

	/* OxygenOS keeps the motor boost converter on across movements. */
	popup->boost = devm_gpiod_get(dev, "boost-enable", GPIOD_OUT_HIGH);
	if (IS_ERR(popup->boost))
		return dev_err_probe(dev, PTR_ERR(popup->boost),
				     "failed to get boost GPIO\n");
	popup->direction = devm_gpiod_get(dev, "direction", GPIOD_OUT_LOW);
	if (IS_ERR(popup->direction))
		return dev_err_probe(dev, PTR_ERR(popup->direction),
				     "failed to get direction GPIO\n");
	popup->mode1 = devm_gpiod_get(dev, "mode1", GPIOD_OUT_LOW);
	if (IS_ERR(popup->mode1))
		return dev_err_probe(dev, PTR_ERR(popup->mode1),
				     "failed to get mode1 GPIO\n");
	popup->sleep = devm_gpiod_get(dev, "sleep", GPIOD_OUT_LOW);
	if (IS_ERR(popup->sleep))
		return dev_err_probe(dev, PTR_ERR(popup->sleep),
				     "failed to get sleep GPIO\n");
	popup->step = devm_gpiod_get(dev, "step", GPIOD_OUT_LOW);
	if (IS_ERR(popup->step))
		return dev_err_probe(dev, PTR_ERR(popup->step),
				     "failed to get step GPIO\n");
	popup->step_pwm = devm_pwm_get(dev, "step");
	if (IS_ERR(popup->step_pwm))
		return dev_err_probe(dev, PTR_ERR(popup->step_pwm),
				     "failed to get step PWM\n");
	popup->pinctrl = devm_pinctrl_get(dev);
	if (IS_ERR(popup->pinctrl))
		return dev_err_probe(dev, PTR_ERR(popup->pinctrl),
				     "failed to get pinctrl\n");
	popup->step_gpio_state = pinctrl_lookup_state(popup->pinctrl, "default");
	if (IS_ERR(popup->step_gpio_state))
		return dev_err_probe(dev, PTR_ERR(popup->step_gpio_state),
				     "failed to get GPIO step state\n");
	popup->step_pwm_state = pinctrl_lookup_state(popup->pinctrl, "pwm");
	if (IS_ERR(popup->step_pwm_state))
		return dev_err_probe(dev, PTR_ERR(popup->step_pwm_state),
				     "failed to get PWM step state\n");

	popup->hall_up = devm_iio_channel_get(dev, "hall-up");
	if (IS_ERR(popup->hall_up))
		return dev_err_probe(dev, PTR_ERR(popup->hall_up),
				     "failed to get upper Hall channel\n");
	popup->hall_down = devm_iio_channel_get(dev, "hall-down");
	if (IS_ERR(popup->hall_down))
		return dev_err_probe(dev, PTR_ERR(popup->hall_down),
				     "failed to get lower Hall channel\n");

	hotdog_popup_stop(popup);
	popup->genpd.name = "hotdog-camera-popup";
	popup->genpd.power_on = hotdog_popup_domain_power_on;
	popup->genpd.power_off = hotdog_popup_domain_power_off;
	/*
	 * The sensor is probed while retracted. Start logically on so attaching
	 * the I2C device does not move the mechanism; its first runtime suspend
	 * establishes the closed/off state.
	 */
	ret = pm_genpd_init(&popup->genpd, NULL, false);
	if (ret)
		return dev_err_probe(dev, ret,
				     "failed to initialize power domain\n");

	ret = of_genpd_add_provider_simple(dev->of_node, &popup->genpd);
	if (ret) {
		pm_genpd_remove(&popup->genpd);
		return dev_err_probe(dev, ret,
				     "failed to register power domain\n");
	}

	ret = devm_device_add_group(dev, hotdog_popup_groups[0]);
	if (ret) {
		of_genpd_del_provider(dev->of_node);
		pm_genpd_remove(&popup->genpd);
		return ret;
	}

	dev_info(dev, "Hall-bounded pop-up power domain ready; motor remains off\n");
	return 0;
}

static void hotdog_popup_remove(struct platform_device *pdev)
{
	struct hotdog_popup *popup = platform_get_drvdata(pdev);

	of_genpd_del_provider(pdev->dev.of_node);
	pm_genpd_remove(&popup->genpd);
	hotdog_popup_stop(popup);
	gpiod_set_value_cansleep(popup->boost, 0);
}

static const struct of_device_id hotdog_popup_of_match[] = {
	{ .compatible = "oneplus,hotdog-popup-motor" },
	{ }
};
MODULE_DEVICE_TABLE(of, hotdog_popup_of_match);

static struct platform_driver hotdog_popup_driver = {
	.probe = hotdog_popup_probe,
	.remove = hotdog_popup_remove,
	.driver = {
		.name = "oneplus-hotdog-popup-motor",
		.of_match_table = hotdog_popup_of_match,
	},
};
module_platform_driver(hotdog_popup_driver);

MODULE_AUTHOR("Robin Snyders");
MODULE_DESCRIPTION("OnePlus hotdog pop-up camera motor bring-up driver");
MODULE_LICENSE("GPL");
