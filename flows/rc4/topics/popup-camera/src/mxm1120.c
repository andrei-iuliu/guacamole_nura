// SPDX-License-Identifier: GPL-2.0-only
/*
 * MagnaChip MXM1120 Hall effect magnetic position sensor driver
 */

#include <linux/bitops.h>
#include <linux/cleanup.h>
#include <linux/delay.h>
#include <linux/i2c.h>
#include <linux/iio/iio.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/property.h>
#include <linux/regmap.h>
#include <linux/regulator/consumer.h>

#define MXM1120_REG_PERSINT		0x00
#define MXM1120_REG_INTSRS		0x01
#define MXM1120_REG_SRST		0x07
#define MXM1120_REG_OPF			0x08
#define MXM1120_REG_DID			0x09
#define MXM1120_REG_ST1			0x10
#define MXM1120_REG_HSL			0x11
#define MXM1120_REG_HSH			0x12

#define MXM1120_SRST_RESET		0x01
#define MXM1120_DID_VALUE		0x9c
#define MXM1120_ST1_DRDY		BIT(0)
#define MXM1120_PERSINT_COUNT_4		0x40
#define MXM1120_INTSRS_POLL_0034_MT	0x01
#define MXM1120_OPF_80HZ_10BIT_ACTIVE	0x41

struct mxm1120_data {
	struct regmap *regmap;
	struct mutex lock; /* serialize status and sample register reads */
};

static const struct regmap_config mxm1120_regmap_config = {
	.reg_bits = 8,
	.val_bits = 8,
	.max_register = MXM1120_REG_HSH,
};

static const struct iio_chan_spec mxm1120_channels[] = {
	{
		.type = IIO_MAGN,
		.info_mask_separate = BIT(IIO_CHAN_INFO_RAW),
	},
};

static int mxm1120_read_measurement(struct mxm1120_data *data, int *value)
{
	unsigned int status;
	u8 sample[2];
	int ret;

	guard(mutex)(&data->lock);

	ret = regmap_read_poll_timeout(data->regmap, MXM1120_REG_ST1,
				       status, status & MXM1120_ST1_DRDY,
				       1000, 25000);
	if (ret)
		return ret;

	ret = regmap_bulk_read(data->regmap, MXM1120_REG_HSL,
			       sample, sizeof(sample));
	if (ret)
		return ret;

	*value = ((sample[1] & BIT(6)) << 2) | sample[0];
	if (sample[1] & BIT(7))
		*value -= 0x200;

	return 0;
}

static int mxm1120_read_raw(struct iio_dev *indio_dev,
			    const struct iio_chan_spec *chan, int *val,
			    int *val2, long mask)
{
	struct mxm1120_data *data = iio_priv(indio_dev);
	int ret;

	if (mask != IIO_CHAN_INFO_RAW)
		return -EINVAL;

	ret = mxm1120_read_measurement(data, val);
	if (ret)
		return ret;

	return IIO_VAL_INT;
}

static const struct iio_info mxm1120_info = {
	.read_raw = mxm1120_read_raw,
};

static int mxm1120_chip_init(struct device *dev, struct mxm1120_data *data)
{
	unsigned int id;
	int ret;

	ret = regmap_write(data->regmap, MXM1120_REG_SRST,
			   MXM1120_SRST_RESET);
	if (ret)
		return ret;

	usleep_range(5000, 6000);

	ret = regmap_read(data->regmap, MXM1120_REG_DID, &id);
	if (ret)
		return ret;

	if (id != MXM1120_DID_VALUE)
		return dev_err_probe(dev, -ENODEV,
				     "unexpected device ID 0x%02x\n", id);

	ret = regmap_write(data->regmap, MXM1120_REG_PERSINT,
			   MXM1120_PERSINT_COUNT_4);
	if (ret)
		return ret;

	/* Polling mode avoids enabling the position IRQ before calibration. */
	ret = regmap_write(data->regmap, MXM1120_REG_INTSRS,
			   MXM1120_INTSRS_POLL_0034_MT);
	if (ret)
		return ret;

	return regmap_write(data->regmap, MXM1120_REG_OPF,
			    MXM1120_OPF_80HZ_10BIT_ACTIVE);
}

static int mxm1120_probe(struct i2c_client *client)
{
	static const char * const supply_names[] = { "vdd", "vio" };
	struct device *dev = &client->dev;
	struct mxm1120_data *data;
	struct iio_dev *indio_dev;
	const char *label;
	int ret;

	indio_dev = devm_iio_device_alloc(dev, sizeof(*data));
	if (!indio_dev)
		return -ENOMEM;

	data = iio_priv(indio_dev);
	data->regmap = devm_regmap_init_i2c(client, &mxm1120_regmap_config);
	if (IS_ERR(data->regmap))
		return dev_err_probe(dev, PTR_ERR(data->regmap),
				     "failed to initialize regmap\n");

	ret = devm_mutex_init(dev, &data->lock);
	if (ret)
		return ret;

	ret = devm_regulator_bulk_get_enable(dev, ARRAY_SIZE(supply_names),
					     supply_names);
	if (ret)
		return dev_err_probe(dev, ret, "failed to enable supplies\n");

	usleep_range(5000, 6000);

	ret = mxm1120_chip_init(dev, data);
	if (ret)
		return dev_err_probe(dev, ret, "failed to initialize sensor\n");

	indio_dev->name = "mxm1120";
	indio_dev->modes = INDIO_DIRECT_MODE;
	indio_dev->info = &mxm1120_info;
	indio_dev->channels = mxm1120_channels;
	indio_dev->num_channels = ARRAY_SIZE(mxm1120_channels);
	if (!device_property_read_string(dev, "label", &label))
		indio_dev->label = label;

	return devm_iio_device_register(dev, indio_dev);
}

static const struct of_device_id mxm1120_of_match[] = {
	{ .compatible = "magnachip,mxm1120" },
	{ }
};
MODULE_DEVICE_TABLE(of, mxm1120_of_match);

static const struct i2c_device_id mxm1120_id[] = {
	{ "mxm1120" },
	{ }
};
MODULE_DEVICE_TABLE(i2c, mxm1120_id);

static struct i2c_driver mxm1120_driver = {
	.driver = {
		.name = "mxm1120",
		.of_match_table = mxm1120_of_match,
	},
	.probe = mxm1120_probe,
	.id_table = mxm1120_id,
};
module_i2c_driver(mxm1120_driver);

MODULE_AUTHOR("Robin Snyders <robin@snyders.xyz>");
MODULE_DESCRIPTION("MagnaChip MXM1120 Hall effect sensor driver");
MODULE_LICENSE("GPL");
