/*
 * main.c — clamp-on thermal water-leak sensor, application A1.
 *
 * WHAT THIS IS: the Zephyr glue. Device bring-up, the sample loop, power
 * management, the uplink scheduler, the LoRaWAN join/send path, the BLE
 * commissioning window, and the one place in the firmware where a shutoff
 * decision is taken.
 *
 * WHAT THIS DELIBERATELY IS NOT: it contains no detection logic, no payload
 * layout and no valve state transitions. Those live in leak_detect.c, uplink.c
 * and valve.c, which have no Zephyr dependency at all so they can be unit
 * tested on a host (see tests/). If you find yourself adding a threshold
 * comparison to this file, it belongs in one of those three instead.
 *
 * HAS NOT BEEN COMPILED. There is no Zephyr tree, no CMake and no
 * arm-none-eabi-gcc on the machine this was written on. README.md says exactly
 * what that means and which API signatures carry residual uncertainty. Every
 * such spot is marked with an "API NOTE" comment here rather than only in the
 * README, so a reviewer with a tree in front of them can check them in order.
 */

#include <string.h>

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/adc.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/sensor.h>
#include <zephyr/logging/log.h>
#include <zephyr/lorawan/lorawan.h>
#include <zephyr/pm/device.h>
#include <zephyr/random/random.h>
#include <zephyr/settings/settings.h>
#include <zephyr/sys/reboot.h>
#include <zephyr/sys/util.h>

#if defined(CONFIG_BT)
#include <zephyr/bluetooth/bluetooth.h>
#endif

#if defined(CONFIG_TASK_WDT)
#include <zephyr/task_wdt/task_wdt.h>
#endif

#include "install_probe.h"
#include "leak_detect.h"
#include "uplink.h"
#include "valve.h"

LOG_MODULE_REGISTER(app, CONFIG_APP_LOG_LEVEL);

/* ------------------------------------------------------------------------- */
/* Devicetree handles                                                         */
/* ------------------------------------------------------------------------- */

#define PIPE_NODE    DT_NODELABEL(pipe_temp)
#define AMBIENT_NODE DT_NODELABEL(ambient_temp)
#define RADIO_NODE   DT_ALIAS(lora0)

/* API NOTE: DT_NODE_HAS_STATUS(node, okay) is the long-standing spelling and is
 * what Zephyr's own samples use. Recent trees add DT_NODE_HAS_STATUS_OKAY(node)
 * and deprecate the two-argument form; if the build warns, that is the
 * mechanical substitution. Unverified here — no Zephyr tree on this machine. */
BUILD_ASSERT(DT_NODE_HAS_STATUS(PIPE_NODE, okay),
	     "pipe_temp TMP117 missing: check boards/<board>.overlay");
BUILD_ASSERT(DT_NODE_HAS_STATUS(AMBIENT_NODE, okay),
	     "ambient_temp TMP117 missing: check boards/<board>.overlay");
BUILD_ASSERT(DT_NODE_HAS_STATUS(RADIO_NODE, okay),
	     "no lora0 alias: the SX1262 node is not enabled for this board");

static const struct device *const pipe_dev = DEVICE_DT_GET(PIPE_NODE);
static const struct device *const ambient_dev = DEVICE_DT_GET(AMBIENT_NODE);
static const struct device *const lora_dev = DEVICE_DT_GET(RADIO_NODE);

/*
 * Battery and (optionally) actuator-current sensing hang off the `zephyr,user`
 * node. That is the documented Zephyr mechanism for an application-specific
 * GPIO/ADC reference, and it is used here on purpose rather than inventing a
 * private devicetree binding for a divider whose resistor values differ between
 * the WisBlock base board and our own PCB.
 */
#define ZUSER DT_PATH(zephyr_user)

#if DT_NODE_HAS_PROP(ZUSER, io_channels)
static const struct adc_dt_spec batt_adc = ADC_DT_SPEC_GET_BY_IDX(ZUSER, 0);
#define HAVE_BATT_ADC 1
#endif

#if defined(CONFIG_BT) && DT_NODE_HAS_PROP(ZUSER, ble_button_gpios)
static const struct gpio_dt_spec ble_button = GPIO_DT_SPEC_GET(ZUSER, ble_button_gpios);
static struct gpio_callback ble_button_cb;
#define HAVE_BLE_BUTTON 1
#endif

#if defined(CONFIG_APP_VALVE_PRESENT)
#if !DT_NODE_HAS_PROP(ZUSER, valve_close_gpios) || !DT_NODE_HAS_PROP(ZUSER, valve_open_gpios)
#error "CONFIG_APP_VALVE_PRESENT=y requires valve-close-gpios and valve-open-gpios on zephyr,user"
#endif
static const struct gpio_dt_spec valve_close_gpio = GPIO_DT_SPEC_GET(ZUSER, valve_close_gpios);
static const struct gpio_dt_spec valve_open_gpio = GPIO_DT_SPEC_GET(ZUSER, valve_open_gpios);
#endif

/* ------------------------------------------------------------------------- */
/* Application state                                                          */
/* ------------------------------------------------------------------------- */

/*
 * Blocking LoRaWAN transmits get their own work queue. lorawan_send() blocks
 * for the whole air time plus both RX windows — well over a second at DR0 —
 * and the system work queue is shared with the settings subsystem and the BLE
 * host. Stalling it for seconds at a time is how mcumgr-over-BLE recovery stops
 * working at exactly the moment an installer needs it.
 */
#define APP_WQ_PRIO 5
K_THREAD_STACK_DEFINE(app_wq_stack, CONFIG_APP_WORKQ_STACK_SIZE);
static struct k_work_q app_wq;

static struct k_work_delayable sample_work;
static struct k_work_delayable uplink_work;
static struct k_work_delayable probe_work;
#if defined(CONFIG_BT)
static struct k_work_delayable ble_window_work;
static struct k_work ble_reopen_work;
#endif

static struct leak_state detector;
static struct leak_cfg leak_tunables;
static struct valve_cfg valve_tunables;
static struct valve shutoff;
static struct install_record installed;
static struct install_probe_cfg probe_tunables;
static struct install_probe_state probe_acc;

static struct {
	bool joined;
	uint8_t heartbeat_min;         /* §2 set_heartbeat, 1..240 */
	uint16_t pending_token;        /* §2 echo token; see note in uplink.c */
	bool probe_pending;
	uint16_t probe_samples_left;
	int32_t pipe_cc;
	int32_t ambient_cc;
	int32_t battery_mv;
	bool tamper;
	bool cold_warning;
	enum uplink_reason next_reason;
	int64_t last_uplink_uptime_ms;
	enum leak_verdict last_verdict;
	int wdt_channel;
	uint8_t dev_eui[8];
	uint8_t join_eui[8];
	uint8_t app_key[16];
	bool creds_present;
} app;

/* ------------------------------------------------------------------------- */
/* Persisted settings                                                         */
/* ------------------------------------------------------------------------- */

/*
 * CONFIG_LORAWAN_NVM_SETTINGS (see prj.conf) needs the settings subsystem up
 * before lorawan_start(), because that is where the MAC persists dev_nonce.
 * We reuse the same subsystem for the handful of things the application has to
 * survive a reboot with.
 *
 * NOTE WHAT IS *NOT* HERE: the valve position. It is deliberately not
 * persisted-and-trusted. See invariant I2 in valve.h — after a reset the stem
 * position is unknown and the firmware must say so rather than restore a
 * plausible guess.
 */
#define APP_SETTINGS_ROOT "app"

static int app_settings_set(const char *name, size_t len, settings_read_cb read_cb, void *cb_arg)
{
	const char *next;
	ssize_t rc;

	if (settings_name_steq(name, "hb", &next) && (next == NULL)) {
		uint8_t v;

		rc = read_cb(cb_arg, &v, sizeof(v));
		if (rc != (ssize_t)sizeof(v)) {
			return -EINVAL;
		}
		if ((v >= DL_HEARTBEAT_MIN_MINUTES) && (v <= DL_HEARTBEAT_MAX_MINUTES)) {
			app.heartbeat_min = v;
		}
		return 0;
	}

	if (settings_name_steq(name, "install", &next) && (next == NULL)) {
		struct install_record rec;

		rc = read_cb(cb_arg, &rec, sizeof(rec));
		if (rc != (ssize_t)sizeof(rec)) {
			return -EINVAL;
		}
		installed = rec;
		return 0;
	}

	if (settings_name_steq(name, "deveui", &next) && (next == NULL)) {
		rc = read_cb(cb_arg, app.dev_eui, sizeof(app.dev_eui));
		return (rc == (ssize_t)sizeof(app.dev_eui)) ? 0 : -EINVAL;
	}
	if (settings_name_steq(name, "joineui", &next) && (next == NULL)) {
		rc = read_cb(cb_arg, app.join_eui, sizeof(app.join_eui));
		return (rc == (ssize_t)sizeof(app.join_eui)) ? 0 : -EINVAL;
	}
	if (settings_name_steq(name, "appkey", &next) && (next == NULL)) {
		rc = read_cb(cb_arg, app.app_key, sizeof(app.app_key));
		if (rc != (ssize_t)sizeof(app.app_key)) {
			return -EINVAL;
		}
		app.creds_present = true;
		return 0;
	}

	return -ENOENT;
}

SETTINGS_STATIC_HANDLER_DEFINE(app_handler, APP_SETTINGS_ROOT, NULL, app_settings_set, NULL, NULL);

static void save_heartbeat(void)
{
	int err = settings_save_one(APP_SETTINGS_ROOT "/hb", &app.heartbeat_min,
				    sizeof(app.heartbeat_min));
	if (err) {
		LOG_WRN("heartbeat persist failed (%d)", err);
	}
}

static void save_install(void)
{
	/*
	 * Written as a raw struct, which is fine within one firmware build and is
	 * NOT fine across an OTA that changes the layout. Before the first signed
	 * A/B update goes out this needs either a version byte or per-field keys;
	 * an MCUboot swap that silently reinterprets the install record would
	 * un-verify a verified fleet, and §5 behaviour 2 then stops every one of
	 * those devices from ever reporting a leak.
	 */
	int err = settings_save_one(APP_SETTINGS_ROOT "/install", &installed, sizeof(installed));

	if (err) {
		LOG_WRN("install record persist failed (%d)", err);
	}
}

/* ------------------------------------------------------------------------- */
/* Sensors                                                                    */
/* ------------------------------------------------------------------------- */

/*
 * Suspend/resume around every read. 06-hardware-and-devkits.md and
 * 08-roadmap.md §8 risk 7 both insist the cold-basement battery number be
 * measured rather than modelled, so this code is written to make the duty cycle
 * easy to measure: exactly one conversion per sample period, device suspended
 * either side.
 *
 * API NOTE: whether the in-tree TMP11x driver implements PM actions at all is
 * unverified here. pm_device_action_run() returns -ENOSYS when a device has no
 * PM hooks, which is why -ENOSYS and -EALREADY are not treated as errors. If
 * the driver turns out to have no PM support, the real power win is its
 * one-shot conversion mode via SENSOR_ATTR_SAMPLING_FREQUENCY rather than
 * device PM, and that is the first thing to check against the measured number.
 */
static void sensor_power(const struct device *dev, bool on)
{
	int err = pm_device_action_run(dev, on ? PM_DEVICE_ACTION_RESUME
						: PM_DEVICE_ACTION_SUSPEND);

	if ((err != 0) && (err != -EALREADY) && (err != -ENOSYS) && (err != -ENOTSUP)) {
		LOG_WRN("%s PM %s failed (%d)", dev->name, on ? "resume" : "suspend", err);
	}
}

static int read_temp_cc(const struct device *dev, int32_t *out_cc)
{
	struct sensor_value val;
	int err;

	err = sensor_sample_fetch(dev);
	if (err) {
		return err;
	}

	/* The TMP11x driver reports on SENSOR_CHAN_AMBIENT_TEMP regardless of what
	 * the sensor is physically clamped to; "ambient" is the Zephyr channel
	 * name for temperature, not a statement about placement. */
	err = sensor_channel_get(dev, SENSOR_CHAN_AMBIENT_TEMP, &val);
	if (err) {
		return err;
	}

	/*
	 * struct sensor_value carries the sign in both members for negative
	 * readings (-1.50 C is val1 = -1, val2 = -500000), so this arithmetic is
	 * correct below zero — which is the half of the range that matters for a
	 * freeze product.
	 *
	 * val2 is micro-degrees, so one centi-degree is 10000 of them and half of
	 * one is 5000. We round to nearest rather than truncating, and round away
	 * from zero symmetrically, because the Node codec in codec/ rounds when it
	 * converts the other way — its tests assert that 0.005 C encodes to 1 and
	 * -0.005 C to -1. Truncating here would make the firmware disagree with
	 * the codec by one count on roughly half of all readings. One count is
	 * 0.01 C and changes no decision, but a known systematic difference
	 * between two implementations of the same contract is the kind of thing
	 * that costs an afternoon when someone eventually diffs them.
	 */
	if (val.val2 >= 0) {
		*out_cc = (val.val1 * 100) + ((val.val2 + 5000) / 10000);
	} else {
		*out_cc = (val.val1 * 100) + ((val.val2 - 5000) / 10000);
	}
	return 0;
}

static int read_battery_mv(int32_t *out_mv)
{
#if defined(HAVE_BATT_ADC)
	uint16_t raw = 0;
	int32_t mv;
	struct adc_sequence seq;
	int err;

	err = adc_sequence_init_dt(&batt_adc, &seq);
	if (err) {
		return err;
	}
	seq.buffer = &raw;
	seq.buffer_size = sizeof(raw);

	err = adc_read(batt_adc.dev, &seq);
	if (err) {
		return err;
	}

	mv = (int32_t)raw;
	err = adc_raw_to_millivolts_dt(&batt_adc, &mv);
	if (err) {
		return err;
	}

	/* Undo the external divider. The ratio is Kconfig rather than devicetree
	 * because it is a property of the carrier board's resistors, and because
	 * the "right" answer (the upstream `voltage-divider` binding) is not
	 * verified present in the tree this targets — see README.md. */
	*out_mv = (mv * CONFIG_APP_BATT_DIVIDER_NUM) / CONFIG_APP_BATT_DIVIDER_DEN;
	return 0;
#else
	ARG_UNUSED(out_mv);
	return -ENODEV;
#endif
}

/* ------------------------------------------------------------------------- */
/* Valve hardware backend                                                     */
/* ------------------------------------------------------------------------- */

#if defined(CONFIG_APP_VALVE_PRESENT)
/*
 * A latching solenoid: one polarity pulse latches closed, the other latches
 * open, and it holds position with no current. 05-open-source-stack.md §5.3 is
 * explicit that a latching solenoid is inherently fail-as-is and a motorised
 * ball valve is not, which is one of the reasons the motorised variant is
 * deferred.
 *
 * The torque figure is inferred from pulse current (see install_probe.h); this
 * backend is where that inference lives so it can be recalibrated per valve
 * model without touching the state machine.
 */
static int valve_hw_pulse(void *ctx, bool close, uint32_t *cycle_ms_out)
{
	const struct gpio_dt_spec *line = close ? &valve_close_gpio : &valve_open_gpio;
	int64_t start = k_uptime_get();
	int err;

	ARG_UNUSED(ctx);

	err = gpio_pin_set_dt(line, 1);
	if (err) {
		*cycle_ms_out = (uint32_t)(k_uptime_get() - start);
		return err;
	}

	k_msleep(CONFIG_APP_VALVE_PULSE_MS);

	err = gpio_pin_set_dt(line, 0);
	*cycle_ms_out = (uint32_t)(k_uptime_get() - start);
	return err;
}

static const valve_hw_t valve_hw = {
	.pulse = valve_hw_pulse,

	/*
	 * NULL rather than a stub, and the distinction matters.
	 *
	 * There is no current-sense shunt or ADC channel for the actuator on this
	 * carrier board, so breakaway torque cannot be measured here. Leaving the
	 * pointer NULL makes valve_exercise() answer VALVE_RC_NO_HW and leave the
	 * valve alone. A stub that returned an error instead would drive the state
	 * machine to V_FAULT — turning "this build cannot measure torque" into
	 * "this valve is broken", which is a false and sticky accusation about
	 * hardware that is working fine. A missing capability is not a fault.
	 *
	 * A stub that returned a plausible number would be worse still: it would
	 * let a seized valve arm, which is precisely the failure 08-roadmap.md §8
	 * risk 9 tracks ("any unit arming above the breakaway-torque threshold").
	 */
	.measure_breakaway_mnm = NULL,

	/* Likewise no stem-leak sensor. valve.c documents exactly what that costs:
	 * arm_or_refuse()'s second test then passes on absence of evidence rather
	 * than on evidence of absence. */
	.stem_leak = NULL,
};
#endif /* CONFIG_APP_VALVE_PRESENT */

/* ------------------------------------------------------------------------- */
/* Uplink                                                                     */
/* ------------------------------------------------------------------------- */

static void queue_uplink(enum uplink_reason reason, k_timeout_t delay)
{
	app.next_reason = reason;
	(void)k_work_reschedule_for_queue(&app_wq, &uplink_work, delay);
}

static void uplink_work_handler(struct k_work *work)
{
	struct uplink_sample s;
	uint8_t buf[UPLINK_PAYLOAD_LEN];
	int len;
	int err;
	enum lorawan_message_type type;

	ARG_UNUSED(work);

	if (!app.joined) {
		return;
	}

	s.pipe_cc = app.pipe_cc;
	s.ambient_cc = app.ambient_cc;
	s.battery_mv = app.battery_mv;
	s.install_verified = install_record_sensor_verified(&installed);
	s.tamper = app.tamper;
	s.cold_warning = app.cold_warning;
	s.reason = app.next_reason;

	len = uplink_pack(&s, buf, sizeof(buf));
	if (len != (int)UPLINK_PAYLOAD_LEN) {
		LOG_ERR("payload pack refused (%d) — not transmitting", len);
		return;
	}

	/*
	 * Heartbeats go unconfirmed; everything else is confirmed. A confirmed
	 * uplink costs a downlink slot and the extra receive window's worth of
	 * battery, which is not worth spending 96 times a day on a routine
	 * reading, but is worth spending on the frames an operator will be asked
	 * to act on. contracts/INTERFACES.md §1 does not mandate either, and the
	 * network server's f_cnt is what the backend dedups on regardless.
	 */
	type = (app.next_reason == UPLINK_REASON_HEARTBEAT) ? LORAWAN_MSG_UNCONFIRMED
							    : LORAWAN_MSG_CONFIRMED;

	err = lorawan_send(UPLINK_FPORT, buf, (uint8_t)len, type);
	if (err) {
		/* -EAGAIN here is normally the duty-cycle/backoff guard, not a
		 * failure. Do not retry in a tight loop: on US915 that turns into a
		 * battery fault rather than a radio fault. */
		LOG_WRN("lorawan_send(reason=%u) failed (%d)", (unsigned int)app.next_reason, err);
		return;
	}

	app.last_uplink_uptime_ms = k_uptime_get();
	LOG_INF("uplink reason=%u pipe=%d.%02d amb=%d.%02d batt=%dmV flags=0x%02x",
		(unsigned int)app.next_reason, app.pipe_cc / 100, (app.pipe_cc % 100 + 100) % 100,
		app.ambient_cc / 100, (app.ambient_cc % 100 + 100) % 100, app.battery_mv, buf[6]);
}

/* Minimum spacing between non-heartbeat frames, so a flapping verdict cannot
 * become a transmit storm. */
static bool uplink_rate_limit_ok(void)
{
	int64_t since = k_uptime_get() - app.last_uplink_uptime_ms;

	return since >= ((int64_t)CONFIG_APP_MIN_UPLINK_INTERVAL_S * 1000);
}

/* ------------------------------------------------------------------------- */
/* The sample loop, and the shutoff decision                                  */
/* ------------------------------------------------------------------------- */

static void sample_work_handler(struct k_work *work)
{
	struct leak_sample ls;
	struct leak_decision dec;
	int32_t pipe_cc = app.pipe_cc;
	int32_t ambient_cc = app.ambient_cc;
	int32_t batt_mv = app.battery_mv;
	bool read_ok = true;
	int err;

	ARG_UNUSED(work);

#if defined(CONFIG_TASK_WDT)
	if (app.wdt_channel >= 0) {
		(void)task_wdt_feed(app.wdt_channel);
	}
#endif

	/*
	 * An install probe is running at 1 Hz on this same work queue. Skip the
	 * normal sample while it does: feeding one-second-cadence readings taken
	 * during a deliberate thermal stimulus into the detector would both poison
	 * the baseline and trip the rate-of-change plausibility gate.
	 */
	if (app.probe_pending) {
		(void)k_work_reschedule_for_queue(&app_wq, &sample_work,
						  K_SECONDS(CONFIG_APP_SAMPLE_PERIOD_S));
		return;
	}

	sensor_power(pipe_dev, true);
	sensor_power(ambient_dev, true);

	err = read_temp_cc(pipe_dev, &pipe_cc);
	if (err) {
		LOG_WRN("pipe read failed (%d)", err);
		read_ok = false;
	}
	err = read_temp_cc(ambient_dev, &ambient_cc);
	if (err) {
		LOG_WRN("ambient read failed (%d)", err);
		read_ok = false;
	}

	sensor_power(pipe_dev, false);
	sensor_power(ambient_dev, false);

	err = read_battery_mv(&batt_mv);
	if (err == -ENODEV) {
		/* No divider wired on this carrier board. Report 0 mV rather than a
		 * made-up voltage: the backend's "% never reporting" and battery
		 * columns are a risk-register metric (08-roadmap.md §8 risk 4) and
		 * must not be fed a fiction. */
		batt_mv = 0;
	} else if (err) {
		LOG_WRN("battery read failed (%d)", err);
	}

	app.pipe_cc = pipe_cc;
	app.ambient_cc = ambient_cc;
	app.battery_mv = batt_mv;

	/*
	 * An I2C read failure is a tamper/fault signal, not a missing sample. A
	 * clamp that has been pulled off takes the sensor with it, and §1's tamper
	 * bit is how that reaches the operator.
	 */
	app.tamper = !read_ok;

	ls.ts_s = (uint32_t)(k_uptime_get() / 1000);
	ls.pipe_cc = pipe_cc;
	ls.ambient_cc = ambient_cc;
	ls.battery_mv = (uint16_t)((batt_mv < 0) ? 0 : ((batt_mv > 65535) ? 65535 : batt_mv));
	ls.install_verified = install_record_sensor_verified(&installed);
	ls.tamper = app.tamper;

	/*
	 * API/DESIGN NOTE on ls.ts_s: this is uptime, not wall-clock. The device
	 * has no RTC in the BOM and LoRaWAN DeviceTimeReq is optional on the
	 * network side, so the detector is written to need only *monotonic*
	 * timestamps (see leak_detect.c, which treats a backwards step as a reboot
	 * rather than a fault). The backend stamps `received_at` itself
	 * (contracts/INTERFACES.md §4), so absolute time is supplied where it is
	 * actually available.
	 */

	dec = leak_update(&detector, &leak_tunables, &ls);
	app.cold_warning = leak_cold_warning(&leak_tunables, pipe_cc);

	if (dec.verdict != app.last_verdict) {
		LOG_INF("verdict %s -> %s (conf=%u, delta=%d.%02d, base=%d.%02d): %s",
			leak_verdict_name(app.last_verdict), leak_verdict_name(dec.verdict),
			dec.confidence, dec.delta_cc / 100, (dec.delta_cc % 100 + 100) % 100,
			dec.baseline_cc / 100, (dec.baseline_cc % 100 + 100) % 100, dec.reason);
	}

	/* =====================================================================
	 * THE SHUTOFF DECISION. IT IS MADE HERE, LOCALLY, AND NOWHERE ELSE.
	 * =====================================================================
	 *
	 * 04-connectivity.md §4.2 is the reason, and it is not a preference:
	 * LoRaWAN Class A opens a receive window ONLY after an uplink. A
	 * cloud-issued close therefore cannot reach the device until the next
	 * scheduled uplink, so at a 15-minute heartbeat the worst case is
	 * 15 MINUTES OF FLOODING. Class B costs continuous beacon tracking and
	 * Class C costs the entire power budget, so neither is available to a
	 * five-year primary cell in a crawlspace.
	 *
	 * 08-roadmap.md §8 risk 5 names the failure mode and its leading
	 * indicator: "any architecture doc that has the cloud closing the valve".
	 *
	 * So: the detector verdict computed two lines above is the trigger. The
	 * cloud CONFIRMS and REPORTS; it does not arbitrate. The downlink handler
	 * further down can still carry an operator override, and that override is
	 * logged with VALVE_SRC_CLOUD_DOWNLINK precisely so the two paths are
	 * distinguishable in an audit — but no part of the safety argument depends
	 * on a downlink arriving.
	 *
	 * And it is off by default. CONFIG_APP_LOCAL_SHUTOFF_ENABLED defaults to n
	 * because of standing rule 3 in 08-roadmap.md §0: no customer-visible
	 * action until the nuisance rate is measured. Shadow mode first. An
	 * insurer's trust is spent once. The verdict is still computed, logged and
	 * uplinked while the actuation is disabled — that is what shadow mode
	 * means on the device side.
	 */
	if (IS_ENABLED(CONFIG_APP_LOCAL_SHUTOFF_ENABLED) &&
	    (dec.verdict == LEAK_VERDICT_LEAK_SUSPECTED)) {
		enum valve_rc rc;

		valve_set_battery_mv(&shutoff, ls.battery_mv);
		rc = valve_request(&shutoff, true, VALVE_SRC_LOCAL_DETECTOR);
		LOG_WRN("local shutoff requested: %s (state now %s)", valve_rc_name(rc),
			valve_state_name(shutoff.state));
	} else if (dec.verdict == LEAK_VERDICT_LEAK_SUSPECTED) {
		LOG_WRN("leak_suspected — shadow mode, no actuation "
			"(CONFIG_APP_LOCAL_SHUTOFF_ENABLED=n)");
	}

	/*
	 * Uplink policy. A verdict that an operator would act on earns an
	 * immediate threshold frame (§1 uplink_reason = 1); everything else waits
	 * for the heartbeat. Rate limited, because 96 heartbeats a day is the
	 * battery budget and a flapping verdict must not be allowed to exceed it.
	 */
	if ((dec.verdict != app.last_verdict) &&
	    ((dec.verdict == LEAK_VERDICT_LEAK_SUSPECTED) ||
	     (dec.verdict == LEAK_VERDICT_FREEZE_RISK) ||
	     (dec.verdict == LEAK_VERDICT_SENSOR_FAULT)) &&
	    uplink_rate_limit_ok()) {
		queue_uplink(UPLINK_REASON_THRESHOLD, K_NO_WAIT);
	}

	app.last_verdict = dec.verdict;

#if defined(CONFIG_APP_VALVE_PRESENT)
	if (valve_needs_exercise(&shutoff, (uint32_t)(k_uptime_get() / 1000))) {
		enum valve_rc rc = valve_exercise(&shutoff, (uint32_t)(k_uptime_get() / 1000));

		LOG_INF("exercise cycle: %s (state %s)", valve_rc_name(rc),
			valve_state_name(shutoff.state));
	}
#endif

	(void)k_work_reschedule_for_queue(&app_wq, &sample_work,
					  K_SECONDS(CONFIG_APP_SAMPLE_PERIOD_S));
}

/* ------------------------------------------------------------------------- */
/* Install probe                                                              */
/* ------------------------------------------------------------------------- */

static void probe_work_handler(struct k_work *work)
{
	int32_t pipe_cc = 0;
	int32_t ambient_cc = 0;
	int err_p;
	int err_a;

	ARG_UNUSED(work);

	sensor_power(pipe_dev, true);
	sensor_power(ambient_dev, true);
	err_p = read_temp_cc(pipe_dev, &pipe_cc);
	err_a = read_temp_cc(ambient_dev, &ambient_cc);
	sensor_power(pipe_dev, false);
	sensor_power(ambient_dev, false);

	if ((err_p == 0) && (err_a == 0)) {
		install_probe_feed(&probe_acc, &probe_tunables, pipe_cc, ambient_cc);
	}

	if (app.probe_samples_left > 0U) {
		app.probe_samples_left--;
	}

	if (app.probe_samples_left > 0U) {
		(void)k_work_reschedule_for_queue(&app_wq, &probe_work, K_SECONDS(1));
		return;
	}

	installed.thermal = install_probe_finish(&probe_acc, &probe_tunables);
	installed.ts_s = (uint32_t)(k_uptime_get() / 1000);
	installed.delta_at_install_cc = pipe_cc - ambient_cc;
	app.probe_pending = false;

	LOG_INF("install probe: %s (delta %d cc, token 0x%04x)",
		install_result_name(installed.thermal), installed.delta_at_install_cc,
		app.pending_token);

	/*
	 * A newly verified or newly failed install invalidates everything the
	 * detector learned from the old placement, so start the baseline again.
	 */
	leak_new_state(&detector, NULL);
	save_install();

	/* §1 uplink_reason = 3, "install-probe result". This is also the frame
	 * that §2 expects to carry the echo token — see the contract note at the
	 * bottom of uplink.c, because the 8-byte payload has no field for it. */
	queue_uplink(UPLINK_REASON_INSTALL_PROBE, K_NO_WAIT);
}

static void start_install_probe(uint16_t token)
{
	app.pending_token = token;
	app.probe_pending = true;
	app.probe_samples_left = CONFIG_APP_INSTALL_PROBE_SAMPLES;
	install_probe_begin(&probe_acc);

	/*
	 * The installer has to create the thermal stimulus by running the nearest
	 * fixture — there is no heater in the BOM (see install_probe.h). The BLE
	 * commissioning app is what tells them to; this log line is what tells a
	 * bench operator the same thing.
	 */
	LOG_INF("install probe started: run the nearest fixture for %u s",
		(unsigned int)CONFIG_APP_INSTALL_PROBE_SAMPLES);

	(void)k_work_reschedule_for_queue(&app_wq, &probe_work, K_NO_WAIT);
}

/* ------------------------------------------------------------------------- */
/* LoRaWAN                                                                    */
/* ------------------------------------------------------------------------- */

static void downlink_handler(uint8_t port, uint8_t flags, int16_t rssi, int8_t snr, uint8_t len,
			     const uint8_t *data)
{
	struct downlink_msg msg;
	int err;

	ARG_UNUSED(flags);

	LOG_INF("downlink port=%u len=%u rssi=%d snr=%d", port, len, rssi, snr);

	if (port != DOWNLINK_FPORT) {
		return;
	}

	err = downlink_parse(data, len, &msg);
	if (err == UPLINK_ERR_CMD) {
		LOG_WRN("unknown downlink command, token 0x%04x — ignored, not fatal", msg.token);
		return;
	}
	if (err != UPLINK_OK) {
		LOG_WRN("malformed downlink (%d) — ignored", err);
		return;
	}

	switch (msg.command) {
	case DL_CMD_NOP:
		break;

	case DL_CMD_SET_HEARTBEAT:
		app.heartbeat_min = msg.arg;
		save_heartbeat();
		LOG_INF("heartbeat set to %u min (token 0x%04x)", msg.arg, msg.token);
		break;

	case DL_CMD_REQUEST_INSTALL_PROBE:
		start_install_probe(msg.token);
		break;

	case DL_CMD_VALVE_CLOSE:
	case DL_CMD_VALVE_OPEN: {
		enum valve_rc rc;
		bool close = (msg.command == DL_CMD_VALVE_CLOSE);

		/*
		 * An operator override, NOT the safety path. See the big comment in
		 * sample_work_handler(): the local detector is what protects the
		 * house, because this message may be up to a full heartbeat late.
		 * It is honoured because a human with a phone at the property is a
		 * legitimate actor, it is tagged VALVE_SRC_CLOUD_DOWNLINK so the two
		 * paths stay distinguishable in an audit, and it is subject to
		 * exactly the same gates — a refused valve stays refused, and a
		 * faulted valve is NOT cleared from here (invariant I3 in valve.h).
		 */
		valve_set_battery_mv(&shutoff, (uint16_t)app.battery_mv);
		rc = valve_request(&shutoff, close, VALVE_SRC_CLOUD_DOWNLINK);
		LOG_INF("downlink valve %s (token 0x%04x): %s, state %s",
			close ? "close" : "open", msg.token, valve_rc_name(rc),
			valve_state_name(shutoff.state));

		/*
		 * Report the outcome on the next frame. §8: a command with no
		 * confirming uplink must become `unconfirmed`, never silently
		 * `success`, so we make sure there *is* a confirming frame.
		 *
		 * CONTRACT AMBIGUITY, reported: §2 says the echo token is mirrored
		 * "in the next uplink's uplink_reason=3 frame" for *every* command,
		 * but §1 names reason 3 "install-probe result". So a valve
		 * acknowledgement has to borrow reason 3, which is the only reading
		 * that satisfies §2. We follow §2 rather than the field's name, and
		 * flag it; the clean fix is a fifth reason value, "command_ack".
		 */
		app.pending_token = msg.token;
		queue_uplink(UPLINK_REASON_INSTALL_PROBE, K_NO_WAIT);
		break;
	}

	default:
		break;
	}
}

static struct lorawan_downlink_cb downlink_cb = {
	.port = LW_RECV_PORT_ANY,
	.cb = downlink_handler,
};

static void datarate_changed(enum lorawan_datarate dr)
{
	uint8_t max_next = 0;
	uint8_t max_abs = 0;

	/* API NOTE: lorawan_get_payload_sizes(uint8_t *max_next_payload_size,
	 * uint8_t *max_payload_size) returns void in Zephyr's header. The first
	 * output accounts for pending MAC commands and so can be smaller than the
	 * second. Unverified against a tree here. */
	lorawan_get_payload_sizes(&max_next, &max_abs);

	/*
	 * The reason §1 fixes the payload at eight bytes: at DR0 on US915 the
	 * application payload limit is 11 bytes, and DR0 is where a basement node
	 * at the edge of coverage ends up. If this ever logs a maximum below 8 the
	 * contract is unshippable on that link and the operator needs to know —
	 * which is exactly the margin the 8-byte budget was chosen to protect.
	 */
	LOG_INF("datarate now DR%u, max payload %u B (next frame %u B)", (unsigned int)dr, max_abs,
		max_next);
	if ((max_abs != 0U) && (max_abs < UPLINK_PAYLOAD_LEN)) {
		LOG_ERR("link cannot carry the %u-byte contract payload", UPLINK_PAYLOAD_LEN);
	}
}

static int load_credentials(void)
{
	/*
	 * Join credentials come from the settings partition, written per device at
	 * manufacture or over BLE at commissioning. The Kconfig fallbacks exist
	 * for bench bring-up only and default to empty strings: a device with no
	 * credentials REFUSES TO JOIN and says so, rather than shipping with a
	 * default key. A fleet sharing one AppKey is a fleet with one compromise.
	 */
	if (app.creds_present) {
		return 0;
	}

	if ((strlen(CONFIG_APP_DEV_EUI) != 16) || (strlen(CONFIG_APP_JOIN_EUI) != 16) ||
	    (strlen(CONFIG_APP_APP_KEY) != 32)) {
		return -ENOENT;
	}

	/* API NOTE: hex2bin() is Zephyr's own helper from <zephyr/sys/util.h>; it
	 * returns the number of bytes written, 0 on failure. */
	if (hex2bin(CONFIG_APP_DEV_EUI, 16, app.dev_eui, sizeof(app.dev_eui)) !=
	    sizeof(app.dev_eui)) {
		return -EINVAL;
	}
	if (hex2bin(CONFIG_APP_JOIN_EUI, 16, app.join_eui, sizeof(app.join_eui)) !=
	    sizeof(app.join_eui)) {
		return -EINVAL;
	}
	if (hex2bin(CONFIG_APP_APP_KEY, 32, app.app_key, sizeof(app.app_key)) !=
	    sizeof(app.app_key)) {
		return -EINVAL;
	}

	LOG_WRN("using Kconfig join credentials — bench only, never ship this");
	app.creds_present = true;
	return 0;
}

static int lorawan_bring_up(void)
{
	struct lorawan_join_config join = {0};
	int err;
	uint32_t backoff_s = CONFIG_APP_JOIN_BACKOFF_MIN_S;

	if (!device_is_ready(lora_dev)) {
		LOG_ERR("%s not ready", lora_dev->name);
		return -ENODEV;
	}

	/*
	 * API NOTE: lorawan_set_region() came in with multi-region support in
	 * subsys/lorawan. On a tree that predates it the region is a pure Kconfig
	 * choice and this call does not exist — delete it and rely on
	 * CONFIG_LORAWAN_REGION_US915 in prj.conf. Unverified here: there is no
	 * Zephyr tree on the machine this was written on.
	 */
	err = lorawan_set_region(LORAWAN_REGION_US915);
	if (err) {
		LOG_ERR("lorawan_set_region(US915) failed (%d)", err);
		return err;
	}

	err = lorawan_start();
	if (err) {
		LOG_ERR("lorawan_start failed (%d)", err);
		return err;
	}

	lorawan_register_downlink_callback(&downlink_cb);
	lorawan_register_dr_changed_callback(datarate_changed);

	/* ADR on: the node is static, so letting the network server walk it up
	 * from DR0 is free range and free battery. */
	lorawan_enable_adr(true);

	join.mode = LORAWAN_ACT_OTAA;
	join.dev_eui = app.dev_eui;
	join.otaa.join_eui = app.join_eui;
	join.otaa.app_key = app.app_key;
	join.otaa.nwk_key = app.app_key; /* LoRaWAN 1.0.x: one root key */
	/*
	 * dev_nonce is left at 0 ON PURPOSE. With CONFIG_LORAWAN_NVM_SETTINGS=y
	 * the MAC owns this counter, restores it from NVS and increments it per
	 * join attempt. Writing a value here would defeat the whole point of that
	 * Kconfig option — see the comment on it in prj.conf.
	 */

	while (true) {
		err = lorawan_join(&join);
		if (err == 0) {
			break;
		}

		/*
		 * Exponential backoff with jitter. US915 join on a gateway serving a
		 * single sub-band is routinely slow, because the device tries all 72
		 * channels and only 8 of them are listened to; a tight retry loop
		 * spends the battery before it finds one.
		 *
		 * API NOTE: sys_rand32_get() lives in <zephyr/random/random.h>. On
		 * trees older than ~v3.5 the header is <zephyr/random/rand32.h>.
		 */
		LOG_WRN("join failed (%d), retrying in ~%u s", err, backoff_s);
		k_sleep(K_SECONDS(backoff_s + (sys_rand32_get() % 30U)));
		backoff_s *= 2U;
		if (backoff_s > (uint32_t)CONFIG_APP_JOIN_BACKOFF_MAX_S) {
			backoff_s = CONFIG_APP_JOIN_BACKOFF_MAX_S;
		}
	}

	app.joined = true;
	LOG_INF("joined");
	return 0;
}

/* ------------------------------------------------------------------------- */
/* BLE commissioning and field recovery                                       */
/* ------------------------------------------------------------------------- */

#if defined(CONFIG_BT)
static const struct bt_data ble_ad[] = {
	BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
	BT_DATA(BT_DATA_NAME_COMPLETE, CONFIG_BT_DEVICE_NAME, sizeof(CONFIG_BT_DEVICE_NAME) - 1),
};

static void ble_window_close(struct k_work *work)
{
	ARG_UNUSED(work);
	(void)bt_le_adv_stop();
	LOG_INF("BLE commissioning window closed");
}

static void ble_window_open(void)
{
	int err;

	/*
	 * API NOTE: BT_LE_ADV_CONN is the long-standing macro for connectable
	 * advertising. Newer Zephyr deprecates it in favour of
	 * BT_LE_ADV_CONN_FAST_1 / BT_LE_ADV_CONN_ONE_TIME; if the build warns,
	 * that is the substitution. Unverified against a tree here.
	 */
	err = bt_le_adv_start(BT_LE_ADV_CONN, ble_ad, ARRAY_SIZE(ble_ad), NULL, 0);
	if (err) {
		LOG_ERR("bt_le_adv_start failed (%d)", err);
		return;
	}

	LOG_INF("BLE commissioning window open for %u s", (unsigned int)CONFIG_APP_BLE_WINDOW_S);
	(void)k_work_reschedule_for_queue(&app_wq, &ble_window_work,
					  K_SECONDS(CONFIG_APP_BLE_WINDOW_S));
}

static void ble_reopen_handler(struct k_work *work)
{
	ARG_UNUSED(work);
	ble_window_open();
}

#if defined(HAVE_BLE_BUTTON)
static void ble_button_pressed(const struct device *dev, struct gpio_callback *cb,
			       uint32_t pins)
{
	ARG_UNUSED(dev);
	ARG_UNUSED(cb);
	ARG_UNUSED(pins);

	/* ISR context: bt_le_adv_start() must not be called from here, so defer.
	 * This is the field-recovery path — an installer standing at a bricked or
	 * unresponsive node presses the magnet/reed switch and gets a window to
	 * attach mcumgr over BLE (08-roadmap.md §4: MCUboot + mcumgr/BLE at P2). */
	(void)k_work_submit_to_queue(&app_wq, &ble_reopen_work);
}

static void ble_button_bring_up(void)
{
	int err;

	if (!gpio_is_ready_dt(&ble_button)) {
		LOG_WRN("BLE recovery button not ready");
		return;
	}

	err = gpio_pin_configure_dt(&ble_button, GPIO_INPUT);
	if (err == 0) {
		err = gpio_pin_interrupt_configure_dt(&ble_button, GPIO_INT_EDGE_TO_ACTIVE);
	}
	if (err) {
		LOG_WRN("BLE recovery button setup failed (%d)", err);
		return;
	}

	gpio_init_callback(&ble_button_cb, ble_button_pressed, BIT(ble_button.pin));
	(void)gpio_add_callback(ble_button.port, &ble_button_cb);
}
#endif /* HAVE_BLE_BUTTON */

static int ble_bring_up(void)
{
	int err = bt_enable(NULL);

	if (err) {
		LOG_ERR("bt_enable failed (%d)", err);
		return err;
	}

	/*
	 * mcumgr's SMP-over-BLE transport registers itself when
	 * CONFIG_MCUMGR_TRANSPORT_BT=y, so there is no explicit registration call
	 * here. That transport is the field-recovery path: an installer with a
	 * phone can re-flash a node that a bad OTA bricked, with no WAN and no
	 * gateway (05-open-source-stack.md §5.1, "the difference between a bad OTA
	 * being an incident and being a mass-flooding liability event").
	 *
	 * Advertising is time-boxed rather than permanent. Continuous connectable
	 * advertising on a five-year primary cell is a measurable power line item,
	 * and it is also attack surface sitting in a basement for a decade.
	 */
#if defined(HAVE_BLE_BUTTON)
	ble_button_bring_up();
#endif
	ble_window_open();
	return 0;
}
#endif /* CONFIG_BT */

/* ------------------------------------------------------------------------- */
/* Watchdog                                                                   */
/* ------------------------------------------------------------------------- */

#if defined(CONFIG_TASK_WDT)
static void wdt_expired(int channel_id, void *user_data)
{
	ARG_UNUSED(channel_id);
	ARG_UNUSED(user_data);

	/*
	 * A watchdog reset takes the valve state machine back to V_UNKNOWN, and
	 * V_UNKNOWN never actuates (invariant I2 in valve.h). That is the whole
	 * fail-as-is story for a firmware hang: the water is left exactly as it
	 * was, and the device comes back saying it does not know where the stem
	 * is, rather than guessing. Nothing in this handler touches the valve.
	 */
	sys_reboot(SYS_REBOOT_COLD);
}

static void wdt_bring_up(void)
{
	const struct device *hw_wdt = DEVICE_DT_GET_OR_NULL(DT_ALIAS(watchdog0));

	app.wdt_channel = -1;

	if (task_wdt_init(hw_wdt) != 0) {
		LOG_WRN("task watchdog init failed; continuing without it");
		return;
	}

	app.wdt_channel = task_wdt_add(CONFIG_APP_WDT_TIMEOUT_MS, wdt_expired, NULL);
	if (app.wdt_channel < 0) {
		LOG_WRN("task_wdt_add failed (%d)", app.wdt_channel);
	}
}
#endif /* CONFIG_TASK_WDT */

/* ------------------------------------------------------------------------- */

static void app_state_init(void)
{
	app.joined = false;
	app.heartbeat_min = CONFIG_APP_HEARTBEAT_MIN;
	app.pending_token = 0U;
	app.probe_pending = false;
	app.probe_samples_left = 0U;
	app.pipe_cc = 0;
	app.ambient_cc = 0;
	app.battery_mv = 0;
	app.tamper = false;
	app.cold_warning = false;
	app.next_reason = UPLINK_REASON_BOOT;
	app.last_uplink_uptime_ms = INT64_MIN / 2;
	app.last_verdict = LEAK_VERDICT_QUIET;
	app.wdt_channel = -1;

	/* Credentials are explicitly zeroed rather than relying on .bss, so that
	 * "no key" is an invariant of this function and not a property of the
	 * linker. load_credentials() refuses to join on an empty key. */
	memset(app.dev_eui, 0, sizeof(app.dev_eui));
	memset(app.join_eui, 0, sizeof(app.join_eui));
	memset(app.app_key, 0, sizeof(app.app_key));
	app.creds_present = false;
}

int main(void)
{
	int err;

	app_state_init();

	leak_cfg_defaults(&leak_tunables);
	leak_tunables.dwell_s = CONFIG_APP_LEAK_DWELL_S;
	leak_tunables.excursion_cc = CONFIG_APP_LEAK_EXCURSION_CC;
	leak_tunables.freeze_cc = CONFIG_APP_FREEZE_CC;

	install_probe_cfg_defaults(&probe_tunables);
	valve_cfg_defaults(&valve_tunables);
	valve_tunables.torque_refuse_mnm = CONFIG_APP_VALVE_TORQUE_REFUSE_MNM;
	valve_tunables.cycle_timeout_ms = CONFIG_APP_VALVE_CYCLE_TIMEOUT_MS;
	valve_tunables.min_actuate_mv = CONFIG_APP_VALVE_MIN_ACTUATE_MV;

	install_record_init(&installed);
	leak_new_state(&detector, NULL);

	LOG_INF("clamp-on thermal leak sensor starting on %s", CONFIG_BOARD);

	/*
	 * Settings first. CONFIG_LORAWAN_NVM_SETTINGS stores dev_nonce through
	 * this subsystem, so it has to be up and loaded before lorawan_start().
	 * Get the order wrong and the join works exactly once — which is the
	 * failure mode prj.conf's comment warns about.
	 */
	err = settings_subsys_init();
	if (err) {
		LOG_ERR("settings_subsys_init failed (%d)", err);
	} else {
		err = settings_load();
		if (err) {
			LOG_ERR("settings_load failed (%d)", err);
		}
	}

	if (!device_is_ready(pipe_dev)) {
		LOG_ERR("pipe TMP117 not ready");
	}
	if (!device_is_ready(ambient_dev)) {
		LOG_ERR("ambient TMP117 not ready");
	}

#if defined(HAVE_BATT_ADC)
	if (!adc_is_ready_dt(&batt_adc)) {
		LOG_ERR("battery ADC not ready");
	} else {
		err = adc_channel_setup_dt(&batt_adc);
		if (err) {
			LOG_ERR("adc_channel_setup_dt failed (%d)", err);
		}
	}
#endif

#if defined(CONFIG_APP_VALVE_PRESENT)
	(void)gpio_pin_configure_dt(&valve_close_gpio, GPIO_OUTPUT_INACTIVE);
	(void)gpio_pin_configure_dt(&valve_open_gpio, GPIO_OUTPUT_INACTIVE);
	/* GPIO_OUTPUT_INACTIVE, not GPIO_OUTPUT_ACTIVE: the drive lines come up
	 * de-energised. A latching solenoid holds its position with no current, so
	 * booting with both lines inactive is literally fail-as-is in hardware. */
	valve_init(&shutoff, &valve_tunables, &valve_hw, NULL);
#else
	/* Year-1 SKU is sensor-only: 08-roadmap.md §10, "No inline valve in year
	 * 1. Triggers every gate at once; resell a certified one." The state
	 * machine is still instantiated, with no hardware, so that every call
	 * answers VALVE_RC_NO_HW instead of being compiled out and silently
	 * diverging from the tested build. */
	valve_init(&shutoff, &valve_tunables, NULL, NULL);
#endif

	k_work_queue_start(&app_wq, app_wq_stack, K_THREAD_STACK_SIZEOF(app_wq_stack), APP_WQ_PRIO,
			   NULL);
	k_work_init_delayable(&sample_work, sample_work_handler);
	k_work_init_delayable(&uplink_work, uplink_work_handler);
	k_work_init_delayable(&probe_work, probe_work_handler);
#if defined(CONFIG_BT)
	k_work_init_delayable(&ble_window_work, ble_window_close);
	k_work_init(&ble_reopen_work, ble_reopen_handler);
#endif

#if defined(CONFIG_TASK_WDT)
	wdt_bring_up();
#endif

#if defined(CONFIG_BT)
	(void)ble_bring_up();
#endif

	/* Start sampling before the radio is up. The detector and the local
	 * shutoff decision must not wait on a join: a device that cannot reach a
	 * gateway still has to protect the house. */
	(void)k_work_schedule_for_queue(&app_wq, &sample_work, K_NO_WAIT);

	/*
	 * The `return 0` paths below are not an abort. Returning from main() in
	 * Zephyr ends the main thread and leaves every other thread — the
	 * application work queue, the BLE host, mcumgr — running. That is the
	 * behaviour we want for an un-provisioned or un-joinable node: keep
	 * sampling, keep the local shutoff path alive, keep BLE recovery
	 * available, and do not spin a radio that cannot join.
	 */
	err = load_credentials();
	if (err) {
		LOG_ERR("no LoRaWAN join credentials provisioned (%d) — "
			"sampling locally, not joining. Provision over BLE.",
			err);
		return 0;
	}

	err = lorawan_bring_up();
	if (err) {
		LOG_ERR("LoRaWAN bring-up failed (%d) — sampling locally", err);
		return 0;
	}

	/* §1 uplink_reason = 2: boot. Sent once, immediately after join, so the
	 * fleet view can tell a reboot from a gap in coverage. */
	queue_uplink(UPLINK_REASON_BOOT, K_NO_WAIT);

	/*
	 * The heartbeat. 15 minutes by default; the backend can change it within
	 * §2's 1..240 and the new value is persisted. This loop deliberately does
	 * nothing but schedule: the system idles into the lowest power state
	 * CONFIG_PM allows between work items, which is where the five-year
	 * battery claim comes from — and which, per 08-roadmap.md §8 risk 7, is to
	 * be MEASURED at -10 to 0 C rather than modelled from a datasheet.
	 */
	while (true) {
		k_sleep(K_MINUTES(app.heartbeat_min));
		queue_uplink(UPLINK_REASON_HEARTBEAT, K_NO_WAIT);
	}

	return 0;
}
