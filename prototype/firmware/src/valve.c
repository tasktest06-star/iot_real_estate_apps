/*
 * valve.c — fail-as-is shutoff state machine. See valve.h for invariants I1-I4.
 * No Zephyr includes: hardware is reached only through valve_hw_t.
 */

#include "valve.h"

static const char *const state_names[] = {
	[V_UNKNOWN] = "V_UNKNOWN",
	[V_OPEN] = "V_OPEN",
	[V_CLOSED] = "V_CLOSED",
	[V_REFUSED] = "V_REFUSED",
	[V_FAULT] = "V_FAULT",
};

const char *valve_state_name(valve_state_t s)
{
	if ((unsigned int)s >= (sizeof(state_names) / sizeof(state_names[0]))) {
		return "V_UNKNOWN";
	}
	return state_names[s];
}

const char *valve_rc_name(enum valve_rc rc)
{
	switch (rc) {
	case VALVE_OK:
		return "ok";
	case VALVE_RC_NO_HW:
		return "no_valve_hardware";
	case VALVE_RC_NOT_ARMED:
		return "not_armed";
	case VALVE_RC_REFUSED:
		return "refused_at_install";
	case VALVE_RC_FAULTED:
		return "faulted";
	case VALVE_RC_LOW_BATTERY:
		return "battery_too_low_to_close";
	case VALVE_RC_ALREADY:
		return "already_in_position";
	case VALVE_RC_HW_FAULT:
		return "hardware_fault";
	case VALVE_RC_TIMEOUT:
		return "cycle_timeout";
	case VALVE_RC_ARG:
		return "bad_argument";
	default:
		return "unknown";
	}
}

void valve_cfg_defaults(struct valve_cfg *cfg)
{
	if (cfg == NULL) {
		return;
	}
	/* See the warning in valve.h: these are starting points, not measurements. */
	cfg->torque_refuse_mnm = 4000U;        /* 4 N.m */
	cfg->cycle_timeout_ms = 8000U;         /* 8 s full travel */
	cfg->min_actuate_mv = 3000U;           /* Li-SOCl2 + HLC under pulse load */
	cfg->exercise_interval_s = 2592000U;   /* 30 days */
}

static void cfg_or_defaults(const struct valve_cfg **cfg, struct valve_cfg *scratch)
{
	if (*cfg == NULL) {
		valve_cfg_defaults(scratch);
		*cfg = scratch;
	}
}

valve_state_t arm_or_refuse(const install_probe_t *p, const struct valve_cfg *cfg)
{
	struct valve_cfg scratch;

	cfg_or_defaults(&cfg, &scratch);

	if (p == NULL) {
		return V_FAULT;
	}

	/* A probe that did not run to completion is not evidence of fitness. */
	if (!p->completed) {
		return V_FAULT;
	}

	/* The three tests from 05-open-source-stack.md §5.3, in the document's
	 * order, with the document's semantics. */
	if (p->breakaway_torque_mnm > cfg->torque_refuse_mnm) {
		return V_REFUSED;
	}
	if (p->post_cycle_stem_leak) {
		return V_REFUSED;
	}
	if (p->cycle_time_ms > cfg->cycle_timeout_ms) {
		return V_FAULT;
	}

	/* Armed, and armed OPEN. Note that a successful arm leaves the water ON.
	 * There is no configuration in which commissioning ends with the water
	 * off: a device that shuts the water off while proving it can is a device
	 * that gets uninstalled (invariant I1). */
	return V_OPEN;
}

void valve_init(struct valve *v, const struct valve_cfg *cfg, const valve_hw_t *hw, void *ctx)
{
	if (v == NULL) {
		return;
	}

	/* Invariant I2: boot state is V_UNKNOWN. We do not read a persisted
	 * position and trust it, and we do not stroke the valve to find out. After
	 * a reset the stem position is genuinely unknown, and the cost of waiting
	 * for an installer or an exercise cycle is a lot lower than the cost of
	 * being wrong in the closed direction. */
	v->state = V_UNKNOWN;
	v->cfg = cfg;
	v->hw = hw;
	v->ctx = ctx;
	v->battery_mv = 0U;
	v->battery_known = false;
	v->last_exercise_s = 0U;
	v->exercised = false;
	v->cycles = 0U;
	v->last_rc = VALVE_OK;
	v->last_source = VALVE_SRC_INSTALLER_BLE;
	v->last_probe.breakaway_torque_mnm = 0U;
	v->last_probe.post_cycle_stem_leak = false;
	v->last_probe.cycle_time_ms = 0U;
	v->last_probe.completed = false;
}

static bool have_hw(const struct valve *v)
{
	return (v->hw != NULL) && (v->hw->pulse != NULL);
}

bool valve_is_armed(const struct valve *v)
{
	if (v == NULL) {
		return false;
	}
	return (v->state == V_OPEN) || (v->state == V_CLOSED);
}

enum valve_rc valve_arm(struct valve *v, const install_probe_t *p)
{
	valve_state_t next;

	if ((v == NULL) || (p == NULL)) {
		return VALVE_RC_ARG;
	}
	if (!have_hw(v)) {
		v->last_rc = VALVE_RC_NO_HW;
		return VALVE_RC_NO_HW;
	}

	/* Invariant I3: V_FAULT needs an explicit human clear first. Re-running a
	 * probe is not enough, because the probe uses the same actuator that just
	 * faulted. */
	if (v->state == V_FAULT) {
		v->last_rc = VALVE_RC_FAULTED;
		return VALVE_RC_FAULTED;
	}

	v->last_probe = *p;
	next = arm_or_refuse(p, v->cfg);
	v->state = next;

	switch (next) {
	case V_OPEN:
		v->last_rc = VALVE_OK;
		return VALVE_OK;
	case V_REFUSED:
		v->last_rc = VALVE_RC_REFUSED;
		return VALVE_RC_REFUSED;
	default:
		v->last_rc = VALVE_RC_HW_FAULT;
		return VALVE_RC_HW_FAULT;
	}
}

enum valve_rc valve_clear_fault(struct valve *v)
{
	if (v == NULL) {
		return VALVE_RC_ARG;
	}
	if (v->state != V_FAULT) {
		return VALVE_OK;
	}

	/* Back to V_UNKNOWN, not to V_OPEN. Clearing a fault does not establish
	 * where the stem is; only a completed probe does that, and the installer
	 * has to run one next (invariants I2, I3). */
	v->state = V_UNKNOWN;
	v->last_rc = VALVE_OK;
	return VALVE_OK;
}

void valve_set_battery_mv(struct valve *v, uint16_t mv)
{
	if (v == NULL) {
		return;
	}
	v->battery_mv = mv;
	v->battery_known = true;

	/*
	 * Deliberately empty of consequences. A low battery does NOT change the
	 * valve state here — this is the single most important non-action in the
	 * file. 05-open-source-stack.md §5.3: "a valve that closes on battery
	 * death ... causes the exact damage you were hired to prevent." The
	 * voltage is consulted at valve_request() time, where it can refuse a
	 * close; it never initiates one, and it never drives an open either, since
	 * an unnecessary stroke on a dying cell is how the cell dies.
	 */
}

static enum valve_rc gate_request(struct valve *v, bool close)
{
	struct valve_cfg scratch;
	const struct valve_cfg *cfg = v->cfg;

	cfg_or_defaults(&cfg, &scratch);

	if (!have_hw(v)) {
		return VALVE_RC_NO_HW;
	}

	switch (v->state) {
	case V_UNKNOWN:
		/* Invariant I2. §8: "does not try anyway". */
		return VALVE_RC_NOT_ARMED;
	case V_REFUSED:
		return VALVE_RC_REFUSED;
	case V_FAULT:
		return VALVE_RC_FAULTED;
	case V_OPEN:
		if (!close) {
			return VALVE_RC_ALREADY;
		}
		break;
	case V_CLOSED:
		if (close) {
			return VALVE_RC_ALREADY;
		}
		break;
	default:
		return VALVE_RC_NOT_ARMED;
	}

	/* The asymmetric battery gate. Closing needs headroom; opening does not
	 * get blocked, because refusing to open on a flat battery would convert a
	 * dead cell into a dry house. See valve.h. */
	if (close && v->battery_known && (v->battery_mv < cfg->min_actuate_mv)) {
		return VALVE_RC_LOW_BATTERY;
	}

	return VALVE_OK;
}

static enum valve_rc do_stroke(struct valve *v, bool close)
{
	struct valve_cfg scratch;
	const struct valve_cfg *cfg = v->cfg;
	uint32_t cycle_ms = 0U;
	int err;

	cfg_or_defaults(&cfg, &scratch);

	err = v->hw->pulse(v->ctx, close, &cycle_ms);

	if (err != 0) {
		/*
		 * Invariant I4: the stem is left exactly where the failed stroke left
		 * it. No retry, no corrective stroke in the other direction, no
		 * "drive closed to be safe". We do not know the mechanical condition
		 * and a second stroke into it is how a stem shears — and if the first
		 * stroke was a close that half-completed, driving it the rest of the
		 * way is the fail-closed behaviour this file exists to prevent.
		 */
		v->state = V_FAULT;
		return VALVE_RC_HW_FAULT;
	}

	if (cycle_ms > cfg->cycle_timeout_ms) {
		v->state = V_FAULT;
		return VALVE_RC_TIMEOUT;
	}

	v->state = close ? V_CLOSED : V_OPEN;
	v->cycles++;
	v->last_probe.cycle_time_ms = cycle_ms;
	return VALVE_OK;
}

enum valve_rc valve_request(struct valve *v, bool close, enum valve_source src)
{
	enum valve_rc rc;

	if (v == NULL) {
		return VALVE_RC_ARG;
	}

	v->last_source = src;

	rc = gate_request(v, close);
	if (rc != VALVE_OK) {
		v->last_rc = rc;
		return rc;
	}

	rc = do_stroke(v, close);
	v->last_rc = rc;
	return rc;
}

bool valve_needs_exercise(const struct valve *v, uint32_t now_s)
{
	struct valve_cfg scratch;
	const struct valve_cfg *cfg;

	if (v == NULL) {
		return false;
	}
	cfg = v->cfg;
	cfg_or_defaults(&cfg, &scratch);

	if (!valve_is_armed(v)) {
		/* Never exercise from V_UNKNOWN, V_REFUSED or V_FAULT: those are
		 * exactly the states where moving the valve is the dangerous act. */
		return false;
	}
	if (!v->exercised) {
		return true;
	}
	return (now_s - v->last_exercise_s) >= cfg->exercise_interval_s;
}

enum valve_rc valve_exercise(struct valve *v, uint32_t now_s)
{
	install_probe_t probe;
	enum valve_rc rc;
	uint32_t cycle_ms = 0U;
	uint32_t torque_mnm = 0U;
	bool leak = false;
	valve_state_t verdict;
	bool was_closed;

	if (v == NULL) {
		return VALVE_RC_ARG;
	}
	if (!have_hw(v)) {
		return VALVE_RC_NO_HW;
	}
	if (!valve_is_armed(v)) {
		return (v->state == V_FAULT) ? VALVE_RC_FAULTED
					     : ((v->state == V_REFUSED) ? VALVE_RC_REFUSED
									: VALVE_RC_NOT_ARMED);
	}

	/*
	 * A torque measurement is mandatory for an exercise cycle. Without it the
	 * stroke tells us nothing we did not already know, while still spending a
	 * high-current pulse out of a primary cell — and re-qualifying the valve is
	 * the entire point of the cycle (08-roadmap.md §8 risk 9, whose leading
	 * indicator is "any unit arming above the breakaway-torque threshold").
	 * An actuator that cannot report its current is not shippable as a shutoff
	 * SKU, so we refuse here rather than performing a cosmetic stroke.
	 */
	if (v->hw->measure_breakaway_mnm == NULL) {
		v->last_rc = VALVE_RC_NO_HW;
		return VALVE_RC_NO_HW;
	}

	was_closed = (v->state == V_CLOSED);

	/* Exercise re-measures the same three inputs arm_or_refuse() used at
	 * install, so that mechanical degradation is caught on a schedule rather
	 * than at the one moment the valve is actually needed. */
	if (v->hw->measure_breakaway_mnm(v->ctx, &torque_mnm) != 0) {
		v->state = V_FAULT;
		v->last_rc = VALVE_RC_HW_FAULT;
		return VALVE_RC_HW_FAULT;
	}

	rc = valve_request(v, !was_closed, VALVE_SRC_EXERCISE);
	if (rc != VALVE_OK) {
		return rc;
	}
	cycle_ms = v->last_probe.cycle_time_ms;

	if (v->hw->stem_leak != NULL) {
		if (v->hw->stem_leak(v->ctx, &leak) != 0) {
			leak = true; /* sensor answered badly: cannot prove it is dry */
		}
	}
	/*
	 * KNOWN GAP, stated rather than papered over: a unit with no stem-leak
	 * sensor leaves `leak` false, so arm_or_refuse()'s second test passes on
	 * absence of evidence rather than on evidence of absence. That is a
	 * faithful reading of the field's meaning ("a stem leak was detected") but
	 * it is not the same as having checked. Closing this gap is a hardware
	 * task — a moisture pad at the stem — and it belongs in the shutoff SKU
	 * before the shutoff SKU ships, not in this state machine.
	 */

	/* Return to the original position before judging, so that a refusal does
	 * not strand the house with the water off. If the return stroke itself is
	 * refused — most plausibly by the battery gate when the original position
	 * was closed — we stop with the water ON and report the rc. That is the
	 * safe direction, and it is fail-as-is: we never force a close to tidy up
	 * our own bookkeeping. */
	rc = valve_request(v, was_closed, VALVE_SRC_EXERCISE);
	if (rc != VALVE_OK) {
		return rc;
	}

	probe.breakaway_torque_mnm = torque_mnm;
	probe.post_cycle_stem_leak = leak;
	probe.cycle_time_ms = cycle_ms;
	probe.completed = true;

	v->last_probe = probe;
	v->last_exercise_s = now_s;
	v->exercised = true;

	verdict = arm_or_refuse(&probe, v->cfg);
	if (verdict != V_OPEN) {
		/* Degraded. Record the refusal but do NOT move the valve to do it: the
		 * state becomes V_REFUSED/V_FAULT in place, water in whatever
		 * condition the exercise left it, which is the position it started in. */
		v->state = verdict;
		v->last_rc = (verdict == V_REFUSED) ? VALVE_RC_REFUSED : VALVE_RC_HW_FAULT;
		return v->last_rc;
	}

	/* The return stroke above already left state at the original position; we
	 * do not re-assign it here, so that there is exactly one place in this file
	 * that sets a position state and it is do_stroke(). */
	v->last_rc = VALVE_OK;
	return VALVE_OK;
}
