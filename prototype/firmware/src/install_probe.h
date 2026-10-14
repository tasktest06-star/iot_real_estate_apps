/*
 * install_probe.h — install verification.
 *
 * WHY THIS EXISTS AT ALL: install-verification telemetry is a hard product
 * requirement, not a feature. 08-roadmap.md P1 says it in those words, and
 * §8 risk 4 gives the reason — placement error and sensor fault are
 * indistinguishable in the data otherwise, and the leading indicator of the
 * self-install failure tail is ">10% of shipped units never reporting 7 days
 * out". contracts/INTERFACES.md §5 behaviour 2 then makes it load-bearing in
 * the algorithm: an unverified install can never produce a leak verdict.
 * So the single bit at §1 byte 6 bit 0 is the most consequential bit in the
 * payload, and this file is what is allowed to set it.
 *
 * TWO PROBES, ONE COMMISSIONING STEP:
 *   1. the thermal probe — is the clamp actually coupled to a water pipe?
 *   2. the valve mechanical probe — `install_probe_t`, the struct named in
 *      05-open-source-stack.md §5.3, whose measurements decide whether the
 *      shutoff is allowed to arm at all.
 *
 * WHAT THIS DELIBERATELY IS NOT: there is no heater in the BOM
 * (06-hardware-and-devkits.md §: two TMP117 and an NTC, nothing that can inject
 * heat), so the thermal probe does not self-stimulate. It requires the
 * installer to run the nearest fixture for about a minute, prompted over BLE.
 * That is a deliberate trade: a self-heating probe would add a resistor, a FET,
 * a power budget and a safety argument to save one sentence of installer
 * script.
 *
 * No Zephyr dependency: everything here is streaming integer arithmetic over
 * values the caller supplies, so tests/test_install_probe.c runs on a host.
 */

#ifndef APP_INSTALL_PROBE_H_
#define APP_INSTALL_PROBE_H_

#include <stdbool.h>
#include <stddef.h>   /* NULL — this file is built with no RTOS headers */
#include <stdint.h>

/*
 * The valve mechanical probe. Field names and semantics are taken verbatim from
 * 05-open-source-stack.md §5.3 so that the firmware and the document cannot
 * drift apart.
 *
 * `breakaway_torque_mnm` is the headline number: a quarter-turn ball valve that
 * has sat untouched in a crawlspace for fifteen years can be seized, and a
 * device that promises shutoff on a seized valve is selling non-protection.
 * Measure it at install time and decline to promise what the hardware cannot
 * deliver (08-roadmap.md §8 risk 9, leading indicator: "any unit arming above
 * the breakaway-torque threshold").
 *
 * HONEST NOTE ON THE MEASUREMENT: for a latching solenoid or a motorised
 * actuator there is no torque sensor on the BOM. The number is inferred from
 * actuator current and time-to-motion, which is a proxy that has to be
 * correlated against a bench torque wrench before the threshold in
 * valve_cfg_defaults() means anything. The inference lives in main.c's hardware
 * backend, not here, so that the correlation can be recalibrated per valve
 * model without touching the state machine.
 */
typedef struct {
	uint32_t breakaway_torque_mnm;  /* milli-newton-metres, inferred */
	bool post_cycle_stem_leak;      /* weep past the stem after cycling */
	uint32_t cycle_time_ms;         /* measured full-travel time */
	bool completed;                 /* false if the probe aborted part-way */
} install_probe_t;

/* Thermal probe outcome. The names are logged and sent up over BLE so an
 * installer gets a specific instruction, not "install failed". */
enum install_result {
	INSTALL_UNKNOWN = 0,
	INSTALL_VERIFIED,
	INSTALL_FAIL_INCOMPLETE,          /* too few samples: probe aborted */
	INSTALL_FAIL_SENSORS_IDENTICAL,   /* both channels agree to the LSB */
	INSTALL_FAIL_NO_THERMAL_RESPONSE, /* pipe did not move: clamp not coupled */
	INSTALL_FAIL_AMBIENT_MOVED,       /* room moved too: not a pipe signal */
	INSTALL_FAIL_IMPLAUSIBLE,         /* out of range during the probe */
};

const char *install_result_name(enum install_result r);
bool install_result_is_verified(enum install_result r);

struct install_probe_cfg {
	/* Pipe must move at least this much while the fixture runs. 1.50 C is
	 * comfortably above TMP117 noise and comfortably below what a minute of
	 * mains-temperature water does to a clamped copper pipe. */
	int32_t min_pipe_response_cc;

	/* The room must NOT move this much. This is the test that distinguishes
	 * "the clamp is on the pipe" from "somebody opened the basement door":
	 * a pipe-only signal is the whole point of having two sensors
	 * (06-hardware-and-devkits.md: use two TMP117, not an NTC divider). */
	int32_t max_ambient_response_cc;

	/* The two channels must separate by at least this much at some point. If
	 * they track to the LSB, the clamp is dangling next to the ambient sensor
	 * or the installer fitted both to the same spot. */
	int32_t min_separation_cc;

	int32_t valid_min_cc;
	int32_t valid_max_cc;

	uint8_t min_samples;
};

void install_probe_cfg_defaults(struct install_probe_cfg *cfg);

/*
 * Streaming accumulator — fixed size, and deliberately NOT a buffer of samples.
 * Min/max/first/last is all the verdict needs, so the probe costs ~32 bytes
 * regardless of how long the installer runs the tap.
 */
struct install_probe_state {
	uint8_t n;
	bool implausible;
	int32_t pipe_min, pipe_max;
	int32_t amb_min, amb_max;
	int32_t sep_max;
	int32_t first_pipe_cc, last_pipe_cc;
	int32_t first_amb_cc, last_amb_cc;
};

void install_probe_begin(struct install_probe_state *st);
void install_probe_feed(struct install_probe_state *st, const struct install_probe_cfg *cfg,
			int32_t pipe_cc, int32_t ambient_cc);
enum install_result install_probe_finish(const struct install_probe_state *st,
					 const struct install_probe_cfg *cfg);

/*
 * What gets persisted to NVS and what the uplink flag is derived from.
 * `delta_at_install_cc` is kept because the on-device detector is blind to a
 * leak that was already running at commissioning time (see the limitation note
 * at the bottom of leak_detect.c) — the absolute delta at install is the datum
 * the backend needs to catch that case by cross-fleet comparison.
 */
struct install_record {
	uint32_t ts_s;
	enum install_result thermal;
	int32_t delta_at_install_cc;
	bool valve_present;
	install_probe_t valve;
};

void install_record_init(struct install_record *rec);

/* The §1 byte 6 bit 0 `install_verified` flag. Sensor seating only: a device
 * with no valve, or with a valve that refused to arm, still reports verified
 * if the clamp is good, because the sensor and the shutoff are separate
 * promises and conflating them would silently stop counting good sensors. */
bool install_record_sensor_verified(const struct install_record *rec);

#endif /* APP_INSTALL_PROBE_H_ */
