/*
 * test_valve.c — the fail-as-is invariants, pinned down.
 *
 * These are the tests that matter most in this repository. The claim being
 * made to an insurer is that this device will not shut a customer's water off
 * by accident, and 08-roadmap.md §8 risk 9 names the unlisted version of the
 * downside: "a false shutoff in a commercial building is a business-
 * interruption claim against you."
 *
 * Every test here is structured the same way: drive the state machine, then
 * assert on the FAKE HARDWARE's stroke counters. Asserting on the returned
 * state would only prove the bookkeeping is self-consistent; asserting on the
 * stroke counters proves no current reached the solenoid. The invariant
 * "V_UNKNOWN never actuates" is a statement about electricity, not about an
 * enum, so that is what gets checked.
 *
 * NOT EXECUTED. No compiler on this machine. See tests/README.md.
 */

#include <stdio.h>

#include "../src/install_probe.h"
#include "../src/valve.h"
#include "minitest.h"

/* ------------------------------------------------------------------------- */
/* Fake valve hardware                                                        */
/* ------------------------------------------------------------------------- */

struct fake_hw {
	int closes;          /* strokes toward closed — the dangerous direction */
	int opens;
	int torque_reads;
	uint32_t cycle_ms;   /* what pulse() will report */
	uint32_t torque_mnm; /* what measure_breakaway_mnm() will report */
	bool stem_leak;
	int pulse_err;       /* non-zero to simulate a driver failure */
	int torque_err;
};

static struct fake_hw hw_state;

static int fake_pulse(void *ctx, bool close, uint32_t *cycle_ms_out)
{
	struct fake_hw *h = (struct fake_hw *)ctx;

	if (close) {
		h->closes++;
	} else {
		h->opens++;
	}
	*cycle_ms_out = h->cycle_ms;
	return h->pulse_err;
}

static int fake_torque(void *ctx, uint32_t *mnm_out)
{
	struct fake_hw *h = (struct fake_hw *)ctx;

	h->torque_reads++;
	*mnm_out = h->torque_mnm;
	return h->torque_err;
}

static int fake_stem_leak(void *ctx, bool *leak_out)
{
	struct fake_hw *h = (struct fake_hw *)ctx;

	*leak_out = h->stem_leak;
	return 0;
}

static valve_hw_t fake_ops;
static struct valve_cfg cfg;
static struct valve v;

static void reset_hw(void)
{
	hw_state.closes = 0;
	hw_state.opens = 0;
	hw_state.torque_reads = 0;
	hw_state.cycle_ms = 1200U;
	hw_state.torque_mnm = 1500U;
	hw_state.stem_leak = false;
	hw_state.pulse_err = 0;
	hw_state.torque_err = 0;

	fake_ops.pulse = fake_pulse;
	fake_ops.measure_breakaway_mnm = fake_torque;
	fake_ops.stem_leak = fake_stem_leak;

	valve_cfg_defaults(&cfg);
	valve_init(&v, &cfg, &fake_ops, &hw_state);
}

static install_probe_t good_probe(void)
{
	install_probe_t p;

	p.breakaway_torque_mnm = 1500U;  /* well under the 4000 default */
	p.post_cycle_stem_leak = false;
	p.cycle_time_ms = 1200U;         /* well under the 8000 default */
	p.completed = true;
	return p;
}

/* ------------------------------------------------------------------------- */

static void test_arm_or_refuse_truth_table(void)
{
	install_probe_t p;

	valve_cfg_defaults(&cfg);

	MT_CASE("05 §5.3 a good probe arms OPEN");
	p = good_probe();
	MT_EQI(arm_or_refuse(&p, &cfg), V_OPEN);

	MT_CASE("05 §5.3 breakaway torque over threshold refuses");
	p = good_probe();
	p.breakaway_torque_mnm = cfg.torque_refuse_mnm + 1U;
	MT_EQI(arm_or_refuse(&p, &cfg), V_REFUSED);

	MT_CASE("torque exactly at the threshold still arms (strict >)");
	p = good_probe();
	p.breakaway_torque_mnm = cfg.torque_refuse_mnm;
	MT_EQI(arm_or_refuse(&p, &cfg), V_OPEN);

	MT_CASE("05 §5.3 post-cycle stem leak refuses");
	p = good_probe();
	p.post_cycle_stem_leak = true;
	MT_EQI(arm_or_refuse(&p, &cfg), V_REFUSED);

	MT_CASE("05 §5.3 cycle timeout is a FAULT, not a refusal");
	p = good_probe();
	p.cycle_time_ms = cfg.cycle_timeout_ms + 1U;
	MT_EQI(arm_or_refuse(&p, &cfg), V_FAULT);

	MT_CASE("an incomplete probe is V_FAULT: no evidence is not fitness");
	p = good_probe();
	p.completed = false;
	MT_EQI(arm_or_refuse(&p, &cfg), V_FAULT);

	MT_CASE("a NULL probe is V_FAULT");
	MT_EQI(arm_or_refuse(NULL, &cfg), V_FAULT);

	/* The document checks torque first, then stem leak, then cycle time. Order
	 * is observable when more than one test fails, so pin it. */
	MT_CASE("torque is checked before cycle time (document order)");
	p = good_probe();
	p.breakaway_torque_mnm = cfg.torque_refuse_mnm + 1U;
	p.cycle_time_ms = cfg.cycle_timeout_ms + 1U;
	MT_EQI(arm_or_refuse(&p, &cfg), V_REFUSED);

	MT_CASE("cfg == NULL reproduces the document's default thresholds");
	p = good_probe();
	MT_EQI(arm_or_refuse(&p, NULL), V_OPEN);
	p.breakaway_torque_mnm = 1000000U;
	MT_EQI(arm_or_refuse(&p, NULL), V_REFUSED);
}

/* Invariant I2: boot state is V_UNKNOWN, and V_UNKNOWN never actuates. */
static void test_unknown_never_actuates(void)
{
	install_probe_t p;

	MT_CASE("I2 boot state is V_UNKNOWN");
	reset_hw();
	MT_EQI(v.state, V_UNKNOWN);
	MT_TRUE(!valve_is_armed(&v));

	/* §8: "POST /valve on an unarmed device returns 409 with a reason. It does
	 * not 'try anyway'." The firmware half of that promise is this: no stroke. */
	MT_CASE("I2 close from V_UNKNOWN is refused with NO stroke");
	MT_EQI(valve_request(&v, true, VALVE_SRC_LOCAL_DETECTOR), VALVE_RC_NOT_ARMED);
	MT_EQI(hw_state.closes, 0);
	MT_EQI(hw_state.opens, 0);
	MT_EQI(v.state, V_UNKNOWN);

	MT_CASE("I2 open from V_UNKNOWN is also refused with no stroke");
	MT_EQI(valve_request(&v, false, VALVE_SRC_INSTALLER_BLE), VALVE_RC_NOT_ARMED);
	MT_EQI(hw_state.opens, 0);

	MT_CASE("I2 a cloud downlink cannot actuate an unarmed valve either");
	MT_EQI(valve_request(&v, true, VALVE_SRC_CLOUD_DOWNLINK), VALVE_RC_NOT_ARMED);
	MT_EQI(hw_state.closes, 0);

	MT_CASE("arming leaves the water ON, never off");
	p = good_probe();
	MT_EQI(valve_arm(&v, &p), VALVE_OK);
	MT_EQI(v.state, V_OPEN);
	MT_EQI(hw_state.closes, 0);
}

static void test_normal_operation(void)
{
	install_probe_t p = good_probe();

	reset_hw();
	MT_EQI(valve_arm(&v, &p), VALVE_OK);

	MT_CASE("close from armed-open strokes once and reports V_CLOSED");
	MT_EQI(valve_request(&v, true, VALVE_SRC_LOCAL_DETECTOR), VALVE_OK);
	MT_EQI(v.state, V_CLOSED);
	MT_EQI(hw_state.closes, 1);

	MT_CASE("a redundant close does not stroke again");
	MT_EQI(valve_request(&v, true, VALVE_SRC_CLOUD_DOWNLINK), VALVE_RC_ALREADY);
	MT_EQI(hw_state.closes, 1);

	MT_CASE("open returns to V_OPEN");
	MT_EQI(valve_request(&v, false, VALVE_SRC_INSTALLER_BLE), VALVE_OK);
	MT_EQI(v.state, V_OPEN);
	MT_EQI(hw_state.opens, 1);

	MT_CASE("a redundant open does not stroke again");
	MT_EQI(valve_request(&v, false, VALVE_SRC_INSTALLER_BLE), VALVE_RC_ALREADY);
	MT_EQI(hw_state.opens, 1);

	MT_CASE("lifetime stroke count is tracked for wear");
	MT_EQI(v.cycles, 2);
}

/* Invariant I3: V_REFUSED and V_FAULT are sticky, and no downlink clears them. */
static void test_refused_is_sticky(void)
{
	install_probe_t p;

	reset_hw();
	p = good_probe();
	p.breakaway_torque_mnm = cfg.torque_refuse_mnm + 500U;

	MT_CASE("a refusing probe lands in V_REFUSED without stroking");
	MT_EQI(valve_arm(&v, &p), VALVE_RC_REFUSED);
	MT_EQI(v.state, V_REFUSED);
	MT_EQI(hw_state.closes, 0);
	MT_EQI(hw_state.opens, 0);

	MT_CASE("I3 V_REFUSED refuses every command source, with no stroke");
	MT_EQI(valve_request(&v, true, VALVE_SRC_LOCAL_DETECTOR), VALVE_RC_REFUSED);
	MT_EQI(valve_request(&v, true, VALVE_SRC_CLOUD_DOWNLINK), VALVE_RC_REFUSED);
	MT_EQI(valve_request(&v, false, VALVE_SRC_CLOUD_DOWNLINK), VALVE_RC_REFUSED);
	MT_EQI(hw_state.closes, 0);
	MT_EQI(hw_state.opens, 0);
	MT_EQI(v.state, V_REFUSED);

	MT_CASE("V_REFUSED is cleared only by a fresh PASSING probe");
	p = good_probe();
	MT_EQI(valve_arm(&v, &p), VALVE_OK);
	MT_EQI(v.state, V_OPEN);
}

static void test_fault_is_sticky_and_needs_a_human(void)
{
	install_probe_t p = good_probe();

	reset_hw();
	MT_EQI(valve_arm(&v, &p), VALVE_OK);

	MT_CASE("I4 a driver error faults in place and does NOT retry");
	hw_state.pulse_err = -5;
	MT_EQI(valve_request(&v, true, VALVE_SRC_LOCAL_DETECTOR), VALVE_RC_HW_FAULT);
	MT_EQI(v.state, V_FAULT);
	MT_EQI(hw_state.closes, 1); /* the one attempt, and no corrective stroke */
	MT_EQI(hw_state.opens, 0);

	/*
	 * This is the heart of fail-as-is. The close half-failed, so the stem may
	 * be anywhere. We do NOT drive it the rest of the way closed to "finish
	 * the job", and we do NOT drive it open to "be safe" — either would be an
	 * unknown-condition stroke, and one of them is the fail-closed behaviour
	 * this whole file exists to prevent. The valve stays exactly where it is.
	 */
	MT_CASE("I3 V_FAULT refuses everything, still with no stroke");
	hw_state.pulse_err = 0;
	MT_EQI(valve_request(&v, true, VALVE_SRC_LOCAL_DETECTOR), VALVE_RC_FAULTED);
	MT_EQI(valve_request(&v, false, VALVE_SRC_CLOUD_DOWNLINK), VALVE_RC_FAULTED);
	MT_EQI(hw_state.closes, 1);
	MT_EQI(hw_state.opens, 0);

	MT_CASE("I3 a fresh passing probe does NOT clear V_FAULT");
	MT_EQI(valve_arm(&v, &p), VALVE_RC_FAULTED);
	MT_EQI(v.state, V_FAULT);

	MT_CASE("I3 valve_clear_fault returns to V_UNKNOWN, not to V_OPEN");
	MT_EQI(valve_clear_fault(&v), VALVE_OK);
	MT_EQI(v.state, V_UNKNOWN);

	MT_CASE("and V_UNKNOWN still does not actuate until re-armed");
	MT_EQI(valve_request(&v, true, VALVE_SRC_LOCAL_DETECTOR), VALVE_RC_NOT_ARMED);
	MT_EQI(hw_state.closes, 1);
	MT_EQI(valve_arm(&v, &p), VALVE_OK);
	MT_EQI(v.state, V_OPEN);
}

static void test_cycle_timeout_faults(void)
{
	install_probe_t p = good_probe();

	reset_hw();
	MT_EQI(valve_arm(&v, &p), VALVE_OK);

	MT_CASE("a stroke that overruns the timeout is V_FAULT");
	hw_state.cycle_ms = cfg.cycle_timeout_ms + 1U;
	MT_EQI(valve_request(&v, true, VALVE_SRC_LOCAL_DETECTOR), VALVE_RC_TIMEOUT);
	MT_EQI(v.state, V_FAULT);
	MT_EQI(hw_state.closes, 1);
}

/*
 * The asymmetric battery gate. 05-open-source-stack.md §5.3: "A valve that
 * closes on battery death ... causes the exact damage you were hired to
 * prevent."
 */
static void test_battery_gate_is_asymmetric(void)
{
	install_probe_t p = good_probe();

	reset_hw();
	MT_EQI(valve_arm(&v, &p), VALVE_OK);

	MT_CASE("a low battery blocks CLOSE with no stroke");
	valve_set_battery_mv(&v, (uint16_t)(cfg.min_actuate_mv - 500U));
	MT_EQI(valve_request(&v, true, VALVE_SRC_LOCAL_DETECTOR), VALVE_RC_LOW_BATTERY);
	MT_EQI(hw_state.closes, 0);
	MT_EQI(v.state, V_OPEN);

	MT_CASE("reporting a low battery never itself changes the valve state");
	valve_set_battery_mv(&v, 1800U);
	MT_EQI(v.state, V_OPEN);
	MT_EQI(hw_state.closes, 0);
	MT_EQI(hw_state.opens, 0);

	MT_CASE("but a low battery does NOT block OPEN");
	/* Get to V_CLOSED on a healthy battery first. */
	valve_set_battery_mv(&v, 3600U);
	MT_EQI(valve_request(&v, true, VALVE_SRC_LOCAL_DETECTOR), VALVE_OK);
	MT_EQI(v.state, V_CLOSED);
	valve_set_battery_mv(&v, (uint16_t)(cfg.min_actuate_mv - 500U));
	MT_EQI(valve_request(&v, false, VALVE_SRC_LOCAL_DETECTOR), VALVE_OK);
	MT_EQI(v.state, V_OPEN);
	MT_EQI(hw_state.opens, 1);

	MT_CASE("exactly at the threshold a close is permitted (strict <)");
	valve_set_battery_mv(&v, (uint16_t)cfg.min_actuate_mv);
	MT_EQI(valve_request(&v, true, VALVE_SRC_LOCAL_DETECTOR), VALVE_OK);
}

/* The year-1 SKU: sensor only. 08-roadmap.md §10. */
static void test_no_hardware_is_a_clean_refusal(void)
{
	install_probe_t p = good_probe();

	valve_cfg_defaults(&cfg);
	valve_init(&v, &cfg, NULL, NULL);

	MT_CASE("a sensor-only unit answers NO_HW and stays V_UNKNOWN");
	MT_EQI(v.state, V_UNKNOWN);
	MT_EQI(valve_arm(&v, &p), VALVE_RC_NO_HW);
	MT_EQI(valve_request(&v, true, VALVE_SRC_LOCAL_DETECTOR), VALVE_RC_NO_HW);
	MT_EQI(valve_request(&v, false, VALVE_SRC_CLOUD_DOWNLINK), VALVE_RC_NO_HW);
	MT_EQI(valve_exercise(&v, 0U), VALVE_RC_NO_HW);
	MT_EQI(v.state, V_UNKNOWN);
	MT_TRUE(!valve_needs_exercise(&v, 99999999U));
}

/* 08-roadmap.md §8 risk 9's mitigation: a periodic exercise cycle, because a
 * valve that is never moved seizes and then reports V_OPEN while protecting
 * nothing. */
static void test_exercise_cycle(void)
{
	install_probe_t p = good_probe();

	reset_hw();

	MT_CASE("never exercise from an unarmed state");
	MT_TRUE(!valve_needs_exercise(&v, 99999999U));

	MT_EQI(valve_arm(&v, &p), VALVE_OK);

	MT_CASE("a freshly armed valve is due an exercise cycle");
	MT_TRUE(valve_needs_exercise(&v, 1000U));

	MT_CASE("a healthy exercise cycle returns to the original position");
	MT_EQI(valve_exercise(&v, 1000U), VALVE_OK);
	MT_EQI(v.state, V_OPEN);
	MT_EQI(hw_state.closes, 1);
	MT_EQI(hw_state.opens, 1);
	MT_TRUE(hw_state.torque_reads > 0);
	MT_TRUE(!valve_needs_exercise(&v, 1001U));

	MT_CASE("and is due again after the interval");
	MT_TRUE(valve_needs_exercise(&v, 1000U + cfg.exercise_interval_s));

	MT_CASE("a degraded valve is REFUSED during exercise, not when needed");
	hw_state.torque_mnm = cfg.torque_refuse_mnm + 1000U;
	MT_EQI(valve_exercise(&v, 1000U + cfg.exercise_interval_s), VALVE_RC_REFUSED);
	MT_EQI(v.state, V_REFUSED);

	MT_CASE("a stem leak found during exercise also refuses");
	reset_hw();
	MT_EQI(valve_arm(&v, &p), VALVE_OK);
	hw_state.stem_leak = true;
	MT_EQI(valve_exercise(&v, 1000U), VALVE_RC_REFUSED);
	MT_EQI(v.state, V_REFUSED);

	/*
	 * An actuator with no current sense cannot be re-qualified, so the cycle
	 * refuses rather than performing a cosmetic stroke that spends a pulse out
	 * of a primary cell and proves nothing.
	 */
	MT_CASE("no torque measurement means no exercise cycle at all");
	reset_hw();
	fake_ops.measure_breakaway_mnm = NULL;
	valve_init(&v, &cfg, &fake_ops, &hw_state);
	MT_EQI(valve_arm(&v, &p), VALVE_OK);
	MT_EQI(valve_exercise(&v, 1000U), VALVE_RC_NO_HW);
	MT_EQI(hw_state.closes, 0);
	MT_EQI(hw_state.opens, 0);
}

static void test_names_and_null_safety(void)
{
	MT_CASE("state names match 05 §5.3's enum exactly");
	MT_EQS(valve_state_name(V_UNKNOWN), "V_UNKNOWN");
	MT_EQS(valve_state_name(V_OPEN), "V_OPEN");
	MT_EQS(valve_state_name(V_CLOSED), "V_CLOSED");
	MT_EQS(valve_state_name(V_REFUSED), "V_REFUSED");
	MT_EQS(valve_state_name(V_FAULT), "V_FAULT");
	MT_TRUE(valve_state_name((valve_state_t)42) != NULL);

	MT_CASE("every refusal has a reportable reason string");
	MT_EQS(valve_rc_name(VALVE_OK), "ok");
	MT_TRUE(valve_rc_name(VALVE_RC_NOT_ARMED) != NULL);
	MT_TRUE(valve_rc_name(VALVE_RC_LOW_BATTERY) != NULL);
	MT_TRUE(valve_rc_name((enum valve_rc)-99) != NULL);

	MT_CASE("NULL valve pointers are refused, not dereferenced");
	MT_EQI(valve_request(NULL, true, VALVE_SRC_LOCAL_DETECTOR), VALVE_RC_ARG);
	MT_EQI(valve_arm(NULL, NULL), VALVE_RC_ARG);
	MT_EQI(valve_exercise(NULL, 0U), VALVE_RC_ARG);
	MT_EQI(valve_clear_fault(NULL), VALVE_RC_ARG);
	MT_TRUE(!valve_is_armed(NULL));
	MT_TRUE(!valve_needs_exercise(NULL, 0U));
	valve_set_battery_mv(NULL, 3600U);
	valve_init(NULL, NULL, NULL, NULL);
	valve_cfg_defaults(NULL);
}

int main(void)
{
	test_arm_or_refuse_truth_table();
	test_unknown_never_actuates();
	test_normal_operation();
	test_refused_is_sticky();
	test_fault_is_sticky_and_needs_a_human();
	test_cycle_timeout_faults();
	test_battery_gate_is_asymmetric();
	test_no_hardware_is_a_clean_refusal();
	test_exercise_cycle();
	test_names_and_null_safety();
	return mt_report("test_valve");
}
