// SPDX-License-Identifier: GPL-2.0-only
/*
 * Goodix optical under-display fingerprint sensor, OnePlus 7 Pro (guacamole).
 *
 * Power, reset and interrupt only. The sensor's data path belongs to a
 * Goodix app in Qualcomm TrustZone; Linux never sees an image.
 *
 * This keeps the userspace ABI of the downstream OnePlus driver so the
 * stock fingerprint HAL (libgf_ud_hal_18821_G2_4_oplus.so) can drive it:
 *  - /dev/goodix_fp with the GF_IOC_* ioctls;
 *  - netlink protocol 25: the HAL registers its port by sending any
 *    message, the driver unicasts one-byte GF_NET_EVENT_* messages to it;
 *  - /proc/fp_id, /proc/lcd_type, /proc/fp_kernel_event, /proc/tee_bind_core.
 * Reference: LineageOS android_kernel_oneplus_sm8150, lineage-23.2,
 * drivers/input/oplus_fp_drivers/{goodix_optical_fp,oplus_fp_common,include}.
 *
 * Downstream, the touchscreen driver reports finger down/up over the sensor
 * and the display driver reports when the spot is lit (UI ready). Mainline
 * has neither hook, so a userspace helper writes them to the sysfs
 * attributes "touch", "ui_ready" and "screen" on this device.
 *
 * This is gate-1 groundwork, not authentication. Power-on and reset are
 * refused unless the module is loaded with goodix_fp.allow_power=1, and the
 * DT node ships disabled. Do not enable either until gate 0 (QCOMTEE/QTEE)
 * has been verified on the phone.
 */
#include <linux/delay.h>
#include <linux/device.h>
#include <linux/err.h>
#include <linux/gpio/consumer.h>
#include <linux/interrupt.h>
#include <linux/kernel.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/netlink.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/pm_wakeup.h>
#include <linux/proc_fs.h>
#include <linux/rcupdate.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/uaccess.h>
#include <net/netlink.h>
#include <net/sock.h>

#define GF_IOC_MAGIC			'g'
#define GF_IOC_INIT			_IOR(GF_IOC_MAGIC, 0, u8)
#define GF_IOC_EXIT			_IO(GF_IOC_MAGIC, 1)
#define GF_IOC_RESET			_IO(GF_IOC_MAGIC, 2)
#define GF_IOC_ENABLE_IRQ		_IO(GF_IOC_MAGIC, 3)
#define GF_IOC_DISABLE_IRQ		_IO(GF_IOC_MAGIC, 4)
#define GF_IOC_ENABLE_SPI_CLK		_IOW(GF_IOC_MAGIC, 5, u32)
#define GF_IOC_DISABLE_SPI_CLK		_IO(GF_IOC_MAGIC, 6)
#define GF_IOC_ENABLE_POWER		_IO(GF_IOC_MAGIC, 7)
#define GF_IOC_DISABLE_POWER		_IO(GF_IOC_MAGIC, 8)
#define GF_IOC_INPUT_KEY_EVENT		_IOW(GF_IOC_MAGIC, 9, struct gf_key)
#define GF_IOC_ENTER_SLEEP_MODE		_IO(GF_IOC_MAGIC, 10)
#define GF_IOC_GET_FW_INFO		_IOR(GF_IOC_MAGIC, 11, u8)
#define GF_IOC_REMOVE			_IO(GF_IOC_MAGIC, 12)
#define GF_IOC_CHIP_INFO		_IOW(GF_IOC_MAGIC, 13, struct gf_ioc_chip_info)
#define GF_IOC_NAV_EVENT		_IOW(GF_IOC_MAGIC, 14, u32)
#define GF_IOC_POWER_RESET		_IO(GF_IOC_MAGIC, 17)
#define GF_IOC_WAKELOCK_TIMEOUT_ENABLE	_IO(GF_IOC_MAGIC, 18)
#define GF_IOC_WAKELOCK_TIMEOUT_DISABLE	_IO(GF_IOC_MAGIC, 19)
#define GF_IOC_CLEAN_TOUCH_FLAG		_IO(GF_IOC_MAGIC, 20)
#define GF_IOC_AUTO_SEND_TOUCHDOWN	_IO(GF_IOC_MAGIC, 21)
#define GF_IOC_AUTO_SEND_TOUCHUP	_IO(GF_IOC_MAGIC, 22)
#define GF_IOC_STOP_WAIT_INTERRUPT_EVENT _IO(GF_IOC_MAGIC, 23)

/* Same layout as downstream: an int-sized enum and a u32. */
struct gf_key {
	u32 key;
	u32 value;
};

struct gf_ioc_chip_info {
	u8 vendor_id;
	u8 mode;
	u8 operation;
	u8 reserved[5];
};

#define GF_NETLINK_PROTO	25	/* downstream NETLINK_TEST */
#define GF_NETLINK_MSGSIZE	32	/* downstream MAX_MSGSIZE */

enum gf_net_event {
	GF_NET_EVENT_TEST = 0,
	GF_NET_EVENT_IRQ,
	GF_NET_EVENT_SCR_OFF,
	GF_NET_EVENT_SCR_ON,
	GF_NET_EVENT_TP_TOUCHDOWN,
	GF_NET_EVENT_TP_TOUCHUP,
	GF_NET_EVENT_UI_READY,
	GF_NET_EVENT_UI_DISAPPEAR,
};

#define FP_DRIVER_NETLINK	1	/* /proc/fp_kernel_event: non-GKI build */

/*
 * Gate 1 opt-in. The DT node is disabled by default and this parameter is
 * false by default, so a stray module load cannot power or reset the sensor.
 * Load with goodix_fp.allow_power=1 only after gate 0 passes.
 */
static bool allow_power;
module_param(allow_power, bool, 0444);
MODULE_PARM_DESC(allow_power,
		 "Allow userspace to power/reset the sensor (gate 1 opt-in)");

struct gf_dev {
	struct device *dev;
	struct miscdevice misc;
	struct gpio_desc *reset;	/* DT active low: 1 = held in reset */
	struct gpio_desc *power;
	int irq;
	bool irq_enabled;
	bool wake_enabled;
	bool powered;
	bool removed;
	bool remove_done;
	bool misc_registered;
	unsigned int users;
	int last_touch;
	/*
	 * Lock order: ops_lock -> lock.  The threaded IRQ handler takes only
	 * lock.  Never acquire ops_lock while holding lock, or a disable/sync
	 * path can wait for a handler that needs lock.
	 */
	struct mutex lock;		/* software state; also taken by gf_irq() */
	struct mutex ops_lock;		/* open/ioctl/release/remove serialization */

	struct sock *nl_sk;
	u32 nl_pid;			/* 0 until the HAL registers */

	const char *chip_name;
	const char *lcd_type;
	struct proc_dir_entry *proc_fp_id, *proc_lcd_type;
	struct proc_dir_entry *proc_event, *proc_tee_bind;
};

/* Netlink and /proc are global in the downstream ABI: one sensor only. */
static struct gf_dev __rcu *gf;

/*
 * Caller holds g->lock.  remove() sets g->removed under g->lock, which
 * stops new senders, and only then releases nl_sk, so an in-flight sender
 * cannot race the socket teardown.
 */
static void gf_send(struct gf_dev *g, u8 event)
{
	struct sk_buff *skb;
	struct nlmsghdr *nlh;
	u32 pid = READ_ONCE(g->nl_pid);

	if (g->removed || !g->nl_sk || !pid)
		return;

	skb = nlmsg_new(GF_NETLINK_MSGSIZE, GFP_KERNEL);
	if (!skb)
		return;
	nlh = nlmsg_put(skb, 0, 0, 0, GF_NETLINK_MSGSIZE, 0);
	if (!nlh) {
		kfree_skb(skb);
		return;
	}
	memset(nlmsg_data(nlh), 0, GF_NETLINK_MSGSIZE);
	*(u8 *)nlmsg_data(nlh) = event;
	nlmsg_unicast(g->nl_sk, skb, pid);
}

static void gf_nl_input(struct sk_buff *skb)
{
	struct nlmsghdr *nlh = nlmsg_hdr(skb);
	struct gf_dev *g;
	u32 pid;

	if (skb->len < NLMSG_HDRLEN || nlh->nlmsg_len < NLMSG_HDRLEN)
		return;

	rcu_read_lock();
	g = rcu_dereference(gf);
	if (g) {
		/*
		 * Downstream stores nlh->nlmsg_pid. The kernel sets
		 * NETLINK_CB(skb).portid to the sender's bound port id and
		 * leaves nlmsg_pid to userspace, so prefer portid and keep
		 * nlmsg_pid as the downstream-compatible fallback.
		 */
		pid = NETLINK_CB(skb).portid;
		if (!pid)
			pid = nlh->nlmsg_pid;
		if (pid)
			WRITE_ONCE(g->nl_pid, pid);
	}
	rcu_read_unlock();
}

static irqreturn_t gf_irq(int irq, void *data)
{
	struct gf_dev *g = data;

	mutex_lock(&g->lock);
	if (!g->removed && g->powered) {
		pm_wakeup_event(g->dev, 500);
		gf_send(g, GF_NET_EVENT_IRQ);
	}
	mutex_unlock(&g->lock);
	return IRQ_HANDLED;
}

/* Caller holds g->lock. */
static void gf_power_set(struct gf_dev *g, bool on)
{
	if (g->powered == on)
		return;
	gpiod_set_value_cansleep(g->power, on);
	if (on)
		msleep(30);		/* downstream gf_power_on() */
	g->powered = on;
}

/*
 * IRQ-line operations must not wait for the threaded handler while holding
 * g->lock: gf_irq() takes g->lock.  Disable with the nosync variant under
 * g->lock, then synchronize outside it.  All callers hold ops_lock so the
 * line cannot be enabled or freed underneath the sync.
 */
static bool gf_irq_disable_nosync_locked(struct gf_dev *g)
{
	if (g->irq < 0 || !g->irq_enabled)
		return false;
	disable_irq_nosync(g->irq);
	g->irq_enabled = false;
	return true;
}

static void gf_irq_sync(struct gf_dev *g)
{
	if (g->irq >= 0)
		synchronize_irq(g->irq);
}

/* Caller holds g->lock. */
static void gf_wake_disable_locked(struct gf_dev *g)
{
	if (!g->wake_enabled)
		return;
	disable_irq_wake(g->irq);
	g->wake_enabled = false;
}

/*
 * Last-close/unbind hardware release.  Caller holds ops_lock and must not
 * hold g->lock.  The mutex is dropped before synchronize_irq(), so an
 * in-flight threaded handler can acquire g->lock and finish.
 */
static void gf_release_hw(struct gf_dev *g)
{
	bool sync_irq;

	mutex_lock(&g->lock);
	gf_wake_disable_locked(g);
	sync_irq = gf_irq_disable_nosync_locked(g);
	gf_power_set(g, false);
	g->last_touch = 0;
	mutex_unlock(&g->lock);

	if (sync_irq)
		gf_irq_sync(g);
}

/* Caller holds g->lock. */
static void gf_hw_reset(struct gf_dev *g)
{
	gpiod_set_value_cansleep(g->reset, 1);
	usleep_range(5000, 6000);
	gpiod_set_value_cansleep(g->reset, 0);
	usleep_range(10000, 11000);
}

/* Caller holds g->lock. */
static void gf_touch(struct gf_dev *g, int state)
{
	if (state == g->last_touch)
		return;
	g->last_touch = state;
	pm_wakeup_event(g->dev, 500);
	gf_send(g, state ? GF_NET_EVENT_TP_TOUCHDOWN : GF_NET_EVENT_TP_TOUCHUP);
}

static long gf_ioctl(struct file *filp, unsigned int cmd, unsigned long arg)
{
	struct gf_dev *g = filp->private_data;
	struct gf_ioc_chip_info info;
	u8 route = GF_NETLINK_PROTO;
	bool sync_irq = false;
	long ret = 0;

	if (!g)
		return -ENODEV;
	if (_IOC_TYPE(cmd) != GF_IOC_MAGIC)
		return -ENODEV;

	/* Downstream validates the user range before looking at the command. */
	if ((_IOC_DIR(cmd) & _IOC_READ) &&
	    !access_ok((void __user *)arg, _IOC_SIZE(cmd)))
		return -EFAULT;
	if ((_IOC_DIR(cmd) & _IOC_WRITE) &&
	    !access_ok((void __user *)arg, _IOC_SIZE(cmd)))
		return -EFAULT;

	mutex_lock(&g->ops_lock);
	mutex_lock(&g->lock);

	if (g->removed) {
		ret = -ENODEV;
		goto out;
	}

	/* Downstream refuses everything but power commands while off. */
	if (!g->powered && cmd != GF_IOC_ENABLE_POWER &&
	    cmd != GF_IOC_DISABLE_POWER && cmd != GF_IOC_POWER_RESET) {
		ret = -ENODEV;
		goto out;
	}

	switch (cmd) {
	case GF_IOC_INIT:
		if (copy_to_user((void __user *)arg, &route, sizeof(route)))
			ret = -EFAULT;
		break;
	case GF_IOC_RESET:
		if (!allow_power) {
			ret = -EPERM;
			break;
		}
		gf_hw_reset(g);
		break;
	case GF_IOC_POWER_RESET:
		if (!allow_power) {
			ret = -EPERM;
			break;
		}
		gpiod_set_value_cansleep(g->reset, 1);
		gf_power_set(g, false);
		msleep(50);
		gf_power_set(g, true);
		gpiod_set_value_cansleep(g->reset, 0);
		usleep_range(3000, 4000);
		break;
	case GF_IOC_ENABLE_IRQ:
		if (g->irq >= 0 && !g->irq_enabled) {
			enable_irq(g->irq);
			g->irq_enabled = true;
		}
		break;
	case GF_IOC_DISABLE_IRQ:
		sync_irq = gf_irq_disable_nosync_locked(g);
		break;
	case GF_IOC_ENABLE_POWER:
		if (!allow_power) {
			ret = -EPERM;
			break;
		}
		gf_power_set(g, true);
		break;
	case GF_IOC_DISABLE_POWER:
		gf_power_set(g, false);
		break;
	case GF_IOC_REMOVE:
		gf_wake_disable_locked(g);
		sync_irq = gf_irq_disable_nosync_locked(g);
		gf_power_set(g, false);
		g->last_touch = 0;
		break;
	case GF_IOC_CHIP_INFO:
		if (copy_from_user(&info, (void __user *)arg, sizeof(info))) {
			ret = -EFAULT;
			break;
		}
		dev_info(g->dev, "chip vendor 0x%x mode 0x%x operation 0x%x\n",
			 info.vendor_id, info.mode, info.operation);
		break;
	case GF_IOC_WAKELOCK_TIMEOUT_ENABLE:
		pm_wakeup_event(g->dev, 1000);
		break;
	case GF_IOC_WAKELOCK_TIMEOUT_DISABLE:
		pm_relax(g->dev);
		break;
	case GF_IOC_CLEAN_TOUCH_FLAG:
		g->last_touch = 0;
		break;
	case GF_IOC_AUTO_SEND_TOUCHDOWN:
		gf_touch(g, 1);
		break;
	case GF_IOC_AUTO_SEND_TOUCHUP:
		gf_touch(g, 0);
		break;
	case GF_IOC_EXIT:
	case GF_IOC_ENABLE_SPI_CLK:	/* the bus is not ours */
	case GF_IOC_DISABLE_SPI_CLK:
	case GF_IOC_INPUT_KEY_EVENT:	/* navigation keys: not used by an optical sensor */
	case GF_IOC_NAV_EVENT:
	case GF_IOC_ENTER_SLEEP_MODE:
	case GF_IOC_GET_FW_INFO:
	case GF_IOC_STOP_WAIT_INTERRUPT_EVENT: /* read() event path, not netlink */
		break;
	default:
		dev_warn(g->dev, "unsupported ioctl 0x%x\n", cmd);
		break;
	}
out:
	mutex_unlock(&g->lock);
	if (sync_irq)
		gf_irq_sync(g);
	mutex_unlock(&g->ops_lock);
	return ret;
}

static int gf_open(struct inode *inode, struct file *filp)
{
	struct miscdevice *misc = filp->private_data;
	struct gf_dev *g = container_of(misc, struct gf_dev, misc);
	bool first;
	int ret = 0;

	mutex_lock(&g->ops_lock);
	mutex_lock(&g->lock);
	if (g->removed) {
		ret = -ENODEV;
		goto out_unlock;
	}

	first = !g->users++;
	if (first && g->irq >= 0 && !g->irq_enabled) {
		enable_irq(g->irq);	/* downstream irq_setup() */
		g->irq_enabled = true;
	}
	mutex_unlock(&g->lock);

	if (first && g->irq >= 0) {
		ret = enable_irq_wake(g->irq);
		if (ret) {
			dev_warn(g->dev, "enable_irq_wake failed: %d\n", ret);
			ret = 0;	/* wake is a best-effort optimisation */
		} else {
			mutex_lock(&g->lock);
			g->wake_enabled = true;
			mutex_unlock(&g->lock);
		}
	}

	filp->private_data = g;
	mutex_unlock(&g->ops_lock);
	return 0;

out_unlock:
	mutex_unlock(&g->lock);
	mutex_unlock(&g->ops_lock);
	return ret;
}

static int gf_release(struct inode *inode, struct file *filp)
{
	struct gf_dev *g = filp->private_data;
	bool last, removed, free_it = false;

	if (!g)
		return 0;

	mutex_lock(&g->ops_lock);
	mutex_lock(&g->lock);
	if (!g->users) {
		mutex_unlock(&g->lock);
		mutex_unlock(&g->ops_lock);
		return 0;
	}

	g->users--;
	last = !g->users;
	removed = g->removed;
	mutex_unlock(&g->lock);

	/* A HAL that dies mid-capture must not leave the sensor on. */
	if (last && !removed)
		gf_release_hw(g);

	mutex_lock(&g->lock);
	/* remove() owns the free until it has finished using g. */
	free_it = g->removed && !g->users && g->remove_done;
	mutex_unlock(&g->lock);

	filp->private_data = NULL;
	mutex_unlock(&g->ops_lock);
	if (free_it)
		kfree(g);
	return 0;
}

static const struct file_operations gf_fops = {
	.owner = THIS_MODULE,
	.open = gf_open,
	.release = gf_release,
	.unlocked_ioctl = gf_ioctl,
	.compat_ioctl = compat_ptr_ioctl,
};

/* sysfs: what the downstream touch and display drivers reported */

static ssize_t touch_store(struct device *dev, struct device_attribute *attr,
			   const char *buf, size_t count)
{
	struct gf_dev *g = dev_get_drvdata(dev);
	bool on;
	int ret;

	if (!g)
		return -ENODEV;
	ret = kstrtobool(buf, &on);
	if (ret)
		return ret;
	mutex_lock(&g->lock);
	if (!g->removed)
		gf_touch(g, on);
	mutex_unlock(&g->lock);
	return count;
}
static DEVICE_ATTR_WO(touch);

static ssize_t ui_ready_store(struct device *dev, struct device_attribute *attr,
			      const char *buf, size_t count)
{
	struct gf_dev *g = dev_get_drvdata(dev);
	bool on;
	int ret;

	if (!g)
		return -ENODEV;
	ret = kstrtobool(buf, &on);
	if (ret)
		return ret;
	mutex_lock(&g->lock);
	if (!g->removed)
		gf_send(g, on ? GF_NET_EVENT_UI_READY : GF_NET_EVENT_UI_DISAPPEAR);
	mutex_unlock(&g->lock);
	return count;
}
static DEVICE_ATTR_WO(ui_ready);

static ssize_t screen_store(struct device *dev, struct device_attribute *attr,
			    const char *buf, size_t count)
{
	struct gf_dev *g = dev_get_drvdata(dev);
	bool on;
	int ret;

	if (!g)
		return -ENODEV;
	ret = kstrtobool(buf, &on);
	if (ret)
		return ret;
	mutex_lock(&g->lock);
	if (!g->removed)
		gf_send(g, on ? GF_NET_EVENT_SCR_ON : GF_NET_EVENT_SCR_OFF);
	mutex_unlock(&g->lock);
	return count;
}
static DEVICE_ATTR_WO(screen);

static struct attribute *gf_attrs[] = {
	&dev_attr_touch.attr,
	&dev_attr_ui_ready.attr,
	&dev_attr_screen.attr,
	NULL
};
ATTRIBUTE_GROUPS(gf);

/* /proc nodes the HAL and init.oplus.fingerprints.sh read */

static ssize_t gf_proc_str(char __user *buf, size_t count, loff_t *pos,
			   const char *s)
{
	return simple_read_from_buffer(buf, count, pos, s, strlen(s));
}

static ssize_t fp_id_read(struct file *f, char __user *buf, size_t count, loff_t *pos)
{
	struct gf_dev *g = pde_data(file_inode(f));

	if (!g)
		return -ENODEV;
	return gf_proc_str(buf, count, pos, g->chip_name);
}

static ssize_t lcd_type_read(struct file *f, char __user *buf, size_t count, loff_t *pos)
{
	struct gf_dev *g = pde_data(file_inode(f));

	if (!g)
		return -ENODEV;
	return gf_proc_str(buf, count, pos, g->lcd_type);
}

static ssize_t fp_event_read(struct file *f, char __user *buf, size_t count, loff_t *pos)
{
	int type = FP_DRIVER_NETLINK;

	/* Downstream answers only a read of exactly one int. */
	if (count != sizeof(type))
		return -EPERM;
	if (copy_to_user(buf, &type, sizeof(type)))
		return -EFAULT;
	return count;
}

static ssize_t tee_bind_write(struct file *f, const char __user *buf, size_t count, loff_t *pos)
{
	return count;	/* downstream pins the TEE to a CPU core; not needed */
}

static const struct proc_ops fp_id_ops = { .proc_read = fp_id_read };
static const struct proc_ops lcd_type_ops = { .proc_read = lcd_type_read };
static const struct proc_ops fp_event_ops = { .proc_read = fp_event_read };
static const struct proc_ops tee_bind_ops = { .proc_write = tee_bind_write };

static void gf_proc_remove(struct gf_dev *g)
{
	proc_remove(g->proc_fp_id);
	proc_remove(g->proc_lcd_type);
	proc_remove(g->proc_event);
	proc_remove(g->proc_tee_bind);
}

static int gf_probe(struct platform_device *pdev)
{
	struct netlink_kernel_cfg nl_cfg = { .input = gf_nl_input };
	struct device *dev = &pdev->dev;
	struct gf_dev *g;
	int ret;

	if (rcu_access_pointer(gf))
		return -EBUSY;

	g = kzalloc_obj(*g);
	if (!g)
		return -ENOMEM;
	g->dev = dev;
	g->irq = -1;
	mutex_init(&g->lock);
	mutex_init(&g->ops_lock);

	/* Held in reset and unpowered, as downstream leaves it at probe. */
	g->reset = devm_gpiod_get(dev, "reset", GPIOD_OUT_HIGH);
	if (IS_ERR(g->reset)) {
		ret = dev_err_probe(dev, PTR_ERR(g->reset), "reset gpio\n");
		goto err;
	}
	g->power = devm_gpiod_get(dev, "power", GPIOD_OUT_LOW);
	if (IS_ERR(g->power)) {
		ret = dev_err_probe(dev, PTR_ERR(g->power), "power gpio\n");
		goto err;
	}

	g->irq = platform_get_irq(pdev, 0);
	if (g->irq < 0) {
		ret = g->irq;
		goto err;
	}
	ret = devm_request_threaded_irq(dev, g->irq, NULL, gf_irq,
					IRQF_ONESHOT | IRQF_NO_AUTOEN,
					"goodix_fp", g);
	if (ret) {
		ret = dev_err_probe(dev, ret, "irq\n");
		goto err;
	}
	device_init_wakeup(dev, true);

	if (of_property_read_string(dev->of_node, "oplus,chip-name", &g->chip_name))
		g->chip_name = "unknown";
	if (of_property_read_string(dev->of_node, "oplus,lcd-type", &g->lcd_type))
		g->lcd_type = "unknown";

	g->nl_sk = netlink_kernel_create(&init_net, GF_NETLINK_PROTO, &nl_cfg);
	if (!g->nl_sk) {
		ret = -ENOMEM;
		dev_err_probe(dev, ret, "netlink protocol %d\n", GF_NETLINK_PROTO);
		goto err;
	}

	platform_set_drvdata(pdev, g);
	rcu_assign_pointer(gf, g);

	/*
	 * Downstream exposes fp_id as 0666 and lets userspace rewrite the
	 * chip name.  0051 keeps it read-only: the DT name is the source of
	 * truth for gate 1, and no gate-1/2 consumer needs the write.  Add a
	 * write handler only if the gate-4 HAL or init script is observed to
	 * write it.
	 */
	g->proc_fp_id = proc_create_data("fp_id", 0444, NULL, &fp_id_ops, g);
	g->proc_lcd_type = proc_create_data("lcd_type", 0444, NULL,
					    &lcd_type_ops, g);
	g->proc_event = proc_create_data("fp_kernel_event", 0444, NULL,
					 &fp_event_ops, g);
	/*
	 * Downstream exports this as 0222.  The handler is a no-op CPU-pinning
	 * hint, so keep it root-only instead of world-writable.  Relax to 0222
	 * only if the gate-4 HAL turns out to run as a non-root user and needs
	 * the write to succeed.
	 */
	g->proc_tee_bind = proc_create_data("tee_bind_core", 0200, NULL,
					    &tee_bind_ops, g);
	if (!g->proc_fp_id || !g->proc_lcd_type || !g->proc_event ||
	    !g->proc_tee_bind) {
		ret = -ENOMEM;
		goto err;
	}

	g->misc.minor = MISC_DYNAMIC_MINOR;
	g->misc.name = "goodix_fp";
	g->misc.fops = &gf_fops;
	g->misc.parent = dev;
	ret = misc_register(&g->misc);
	if (ret)
		goto err;
	g->misc_registered = true;

	dev_info(dev, "%s, netlink %d, power gate %s\n", g->chip_name,
		 GF_NETLINK_PROTO, allow_power ? "open" : "closed");
	return 0;

err:
	/* devm_request_threaded_irq() above is released by devres on failure. */
	gf_proc_remove(g);
	if (rcu_access_pointer(gf) == g) {
		rcu_assign_pointer(gf, NULL);
		synchronize_rcu();
	}
	if (g->nl_sk)
		netlink_kernel_release(g->nl_sk);
	if (g->misc_registered)
		misc_deregister(&g->misc);
	platform_set_drvdata(pdev, NULL);
	device_init_wakeup(dev, false);
	kfree(g);
	return ret;
}

static void gf_remove(struct platform_device *pdev)
{
	struct gf_dev *g = platform_get_drvdata(pdev);
	bool free_now;

	if (!g)
		return;

	/*
	 * Deregister before taking ops_lock: misc_open() holds misc_mtx while
	 * calling gf_open(), which takes ops_lock.  Taking ops_lock first and
	 * then calling misc_deregister() would be an ABBA deadlock.  An
	 * in-flight open keeps a module reference, so this path (module unload)
	 * cannot run until it finishes; suppress_bind_attrs blocks sysfs unbind.
	 */
	if (g->misc_registered)
		misc_deregister(&g->misc);

	mutex_lock(&g->ops_lock);

	mutex_lock(&g->lock);
	g->removed = true;
	mutex_unlock(&g->lock);

	/*
	 * Nosync under g->lock, synchronize outside it: the threaded handler
	 * takes g->lock, so a synchronous disable_irq() here would deadlock.
	 */
	gf_release_hw(g);

	/* All file operations are serialized, so no one can re-enable it. */
	if (g->irq >= 0) {
		devm_free_irq(&pdev->dev, g->irq, g);
		g->irq = -1;
	}

	gf_proc_remove(g);

	rcu_assign_pointer(gf, NULL);
	synchronize_rcu();
	if (g->nl_sk) {
		netlink_kernel_release(g->nl_sk);
		g->nl_sk = NULL;
	}

	mutex_lock(&g->lock);
	g->remove_done = true;
	free_now = !g->users;
	mutex_unlock(&g->lock);

	device_init_wakeup(&pdev->dev, false);
	platform_set_drvdata(pdev, NULL);
	mutex_unlock(&g->ops_lock);

	if (free_now)
		kfree(g);
}

static const struct of_device_id gf_of_match[] = {
	{ .compatible = "goodix,goodix_fp" },	/* downstream compatible */
	{ }
};
MODULE_DEVICE_TABLE(of, gf_of_match);

static struct platform_driver gf_driver = {
	.probe = gf_probe,
	.remove = gf_remove,
	.driver = {
		.name = "goodix_fp",
		.of_match_table = gf_of_match,
		.dev_groups = gf_groups,
		/* Keep remove on module unload; misc fops.owner blocks it while open. */
		.suppress_bind_attrs = true,
	},
};
module_platform_driver(gf_driver);

MODULE_DESCRIPTION("Goodix optical fingerprint sensor power/IRQ (OnePlus downstream ABI)");
MODULE_LICENSE("GPL");
