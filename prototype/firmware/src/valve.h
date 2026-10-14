/*
 * valve.h — shutoff state machine. FAIL-AS-IS, never fail-closed.
 *
 * Read this comment before changing anything in valve.c.
 *
 * 05-open-source-stack.md §5.3 states the rule and the reason:
 *
 *     Fail-AS-IS, never fail-closed. A valve that closes on battery death or a
 *     firmware fault floods support with "no water" calls and, in a freeze
 *     event, causes the exact damage you were hired to prevent.
 *
 * 08-roadmap.md §8 risk 9 adds the commercial version of the same point: a
 * false shutoff in a commercial building is a business-interruption claim
 * against us. And §10 is why this file is compiled out by default — there is no
 * inline valve in year 1; the shipping SKU is sensor-only and the shutoff story
 * is a resold, certified third-party valve.
 *
 * The invariants, which the tests in tests/test_valve.c exist to pin down:
 *
 *   I1. There is no code path in this firmware that drives the valve closed as
 *       a consequence of a fault, a reset, a brown-out, a watchdog expiry, a
 *       join failure or a battery warning. Closing only ever happens as the
 *       result of an explicit valve_request(close) from an armed state.
 *   I2. Boot state is V_UNKNOWN, and V_UNKNOWN never actuates. After a reset we
 *       do not know where the stem is, and guessing is worse than waiting.
 *   I3. V_REFUSED and V_FAULT are sticky. They are cleared only by a human at
 *       the device — a fresh passing install probe for V_REFUSED, and
 *       additionally an explicit valve_clear_fault() for V_FAULT. No downlink
 *       can clear either (contracts/INTERFACES.md §8: POST /valve on an unarmed
 *       device returns 409 and "does not try anyway").
 *   I4. On any actuation error the physical valve is left exactly where it is.
 *       We do not attempt a corrective stroke, because a second stroke into an
 *       unknown mechanical condition is how a stem shears.
 *
 * No Zephyr dependency. Hardware is reached only through valve_hw_t, which
 * main.c fills in with GPIO/ADC and which tests fill in with a fake.
 */

#ifndef APP_VALVE_H_
#define APP_VALVE_H_

#include <stdbool.h>
#include <stddef.h>   /* NULL — this file is built with no RTOS headers */
#include <stdint.h>

#include "install_probe.h"

/* Exactly the enum from 05-open-source-stack.md §5.3. Do not reorder: these
 * values are logged, persisted to NVS and reported to the backend. */
typedef enum {
	V_UNKNOWN = 0,
	V_OPEN,
	V_CLOSED,
	V_REFUSED,
	V_FAULT,
} valve_state_t;

const char *valve_state_name(valve_state_t s);

struct valve_cfg {
	/* TORQUE_REFUSE_THRESHOLD in the document's snippet. Above this we refuse
	 * to arm rather than promise a shutoff the actuator cannot deliver.
	 *
	 * THE DEFAULT VALUE IS NOT A MEASURED RESULT. It is a starting point of
	 * the right order of magnitude for a 3/4" quarter-turn ball valve. It must
	 * be replaced with a number from a bench torque wrench on the specific
	 * valve model and the specific actuator, correlated against the current
	 * proxy described in install_probe.h. 08-roadmap.md is explicit about
	 * measuring rather than modelling. */
	uint32_t torque_refuse_mnm;

	/* CYCLE_TIMEOUT_MS in the document's snippet. A cycle that overruns is a
	 * mechanical problem, so it is V_FAULT rather than V_REFUSED. */
	uint32_t cycle_timeout_ms;

	/* Below this battery voltage we refuse to CLOSE but still permit OPEN.
	 * The asymmetry is deliberate and is the heart of fail-as-is:
	 *   - a failed open attempt leaves the valve where it already was, which
	 *     is no worse than not trying;
	 *   - a failed close attempt risks a partially driven stem — a brand-new
	 *     failure mode — and closed is never the safe default anyway.
	 * A latching solenoid needs one full pulse; a brown-out mid-pulse is the
	 * specific thing being avoided. */
	uint16_t min_actuate_mv;

	/* Periodic exercise cycle, 08-roadmap.md §8 risk 9's mitigation. A valve
	 * that is never moved seizes, and a seized valve that still reports
	 * V_OPEN is the "fails open (silent non-protection)" half of that risk. */
	uint32_t exercise_interval_s;
};

void valve_cfg_defaults(struct valve_cfg *cfg);

/*
 * Hardware backend. Every entry may be NULL on a sensor-only unit, in which
 * case the state machine answers VALVE_RC_NO_HW to everything and stays
 * V_UNKNOWN — which is the correct behaviour for the year-1 SKU.
 *
 * pulse(): drive one full stroke. Returns 0 on completion, negative on a
 * hardware error, and writes the measured stroke duration to *cycle_ms_out
 * even on error, so that a timeout is distinguishable from a dead driver.
 */
typedef struct {
	int (*pulse)(void *ctx, bool close, uint32_t *cycle_ms_out);
	int (*measure_breakaway_mnm)(void *ctx, uint32_t *mnm_out);
	int (*stem_leak)(void *ctx, bool *leak_out);
} valve_hw_t;

/* Why the last command was refused. Reported up so the backend can persist an
 * outcome for every command (§8) instead of guessing. */
enum valve_rc {
	VALVE_OK = 0,
	VALVE_RC_NO_HW = -1,        /* sensor-only unit */
	VALVE_RC_NOT_ARMED = -2,    /* V_UNKNOWN: never actuate from here (I2) */
	VALVE_RC_REFUSED = -3,      /* V_REFUSED: probe said the valve is unfit */
	VALVE_RC_FAULTED = -4,      /* V_FAULT: sticky, needs a human (I3) */
	VALVE_RC_LOW_BATTERY = -5,  /* close refused; open would be allowed */
	VALVE_RC_ALREADY = -6,      /* already in the requested position */
	VALVE_RC_HW_FAULT = -7,     /* driver reported an error -> V_FAULT */
	VALVE_RC_TIMEOUT = -8,      /* stroke overran cycle_timeout_ms -> V_FAULT */
	VALVE_RC_ARG = -9,
};

const char *valve_rc_name(enum valve_rc rc);

/* Who asked. Recorded and logged, because "the cloud asked" and "the local
 * detector decided" are different events with different audit requirements —
 * and because only one of them is on the safety path. See the big comment at
 * the decision site in main.c. */
enum valve_source {
	VALVE_SRC_LOCAL_DETECTOR = 0,
	VALVE_SRC_CLOUD_DOWNLINK,
	VALVE_SRC_INSTALLER_BLE,
	VALVE_SRC_EXERCISE,
};

struct valve {
	valve_state_t state;
	const struct valve_cfg *cfg;
	const valve_hw_t *hw;
	void *ctx;

	uint16_t battery_mv;
	bool battery_known;

	uint32_t last_exercise_s;
	bool exercised;

	uint32_t cycles;             /* lifetime stroke count, for wear tracking */
	install_probe_t last_probe;
	enum valve_rc last_rc;
	enum valve_source last_source;
};

/*
 * The arm/refuse decision from 05-open-source-stack.md §5.3.
 *
 * DOCUMENTED DEVIATION FROM THE SNIPPET: the document writes this as
 * `static valve_state_t arm_or_refuse(const install_probe_t *p)` with the
 * thresholds as compile-time macros. It takes a cfg argument here, and is not
 * static, for two reasons: thresholds differ per valve model, and a static
 * function cannot be unit-tested on a host. Passing cfg == NULL reproduces the
 * document's behaviour with the default thresholds exactly.
 *
 * An incomplete probe (p->completed == false) returns V_FAULT: a probe that did
 * not finish has told us nothing, and "nothing" must not read as "fit".
 */
valve_state_t arm_or_refuse(const install_probe_t *p, const struct valve_cfg *cfg);

void valve_init(struct valve *v, const struct valve_cfg *cfg, const valve_hw_t *hw, void *ctx);

/* Run the arm decision against a fresh probe. The only way out of V_REFUSED. */
enum valve_rc valve_arm(struct valve *v, const install_probe_t *p);

/* Explicit, installer-initiated fault clear. Called from the BLE commissioning
 * path only; the downlink handler must never call this (I3). */
enum valve_rc valve_clear_fault(struct valve *v);

void valve_set_battery_mv(struct valve *v, uint16_t mv);

bool valve_is_armed(const struct valve *v);

/* The actual command. close == true closes. */
enum valve_rc valve_request(struct valve *v, bool close, enum valve_source src);

bool valve_needs_exercise(const struct valve *v, uint32_t now_s);

/* close-then-open, re-measuring the probe inputs as it goes. A valve that has
 * degraded since install is sent to V_REFUSED here rather than at the moment it
 * is actually needed. */
enum valve_rc valve_exercise(struct valve *v, uint32_t now_s);

#endif /* APP_VALVE_H_ */
