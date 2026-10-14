/*
 * install_probe.c — thermal install verification plus the valve probe record.
 * No Zephyr includes; see the header.
 */

#include "install_probe.h"

static const char *const result_names[] = {
	[INSTALL_UNKNOWN] = "unknown",
	[INSTALL_VERIFIED] = "verified",
	[INSTALL_FAIL_INCOMPLETE] = "incomplete",
	[INSTALL_FAIL_SENSORS_IDENTICAL] = "sensors_identical",
	[INSTALL_FAIL_NO_THERMAL_RESPONSE] = "no_thermal_response",
	[INSTALL_FAIL_AMBIENT_MOVED] = "ambient_moved",
	[INSTALL_FAIL_IMPLAUSIBLE] = "implausible_reading",
};

const char *install_result_name(enum install_result r)
{
	if ((unsigned int)r >= (sizeof(result_names) / sizeof(result_names[0]))) {
		return "unknown";
	}
	return result_names[r];
}

bool install_result_is_verified(enum install_result r)
{
	return r == INSTALL_VERIFIED;
}

void install_probe_cfg_defaults(struct install_probe_cfg *cfg)
{
	if (cfg == NULL) {
		return;
	}
	cfg->min_pipe_response_cc = 150;    /* 1.50 C */
	cfg->max_ambient_response_cc = 60;  /* 0.60 C */
	cfg->min_separation_cc = 20;        /* 0.20 C */
	cfg->valid_min_cc = -4000;
	cfg->valid_max_cc = 8000;
	cfg->min_samples = 20U;             /* ~20 s at 1 Hz */
}

static void cfg_or_defaults(const struct install_probe_cfg **cfg, struct install_probe_cfg *scratch)
{
	if (*cfg == NULL) {
		install_probe_cfg_defaults(scratch);
		*cfg = scratch;
	}
}

void install_probe_begin(struct install_probe_state *st)
{
	if (st == NULL) {
		return;
	}
	st->n = 0U;
	st->implausible = false;
	st->pipe_min = 0;
	st->pipe_max = 0;
	st->amb_min = 0;
	st->amb_max = 0;
	st->sep_max = 0;
	st->first_pipe_cc = 0;
	st->last_pipe_cc = 0;
	st->first_amb_cc = 0;
	st->last_amb_cc = 0;
}

static int32_t abs_i32(int32_t v)
{
	return (v < 0) ? -v : v;
}

void install_probe_feed(struct install_probe_state *st, const struct install_probe_cfg *cfg,
			int32_t pipe_cc, int32_t ambient_cc)
{
	struct install_probe_cfg scratch;
	int32_t sep;

	if (st == NULL) {
		return;
	}
	cfg_or_defaults(&cfg, &scratch);

	if ((pipe_cc < cfg->valid_min_cc) || (pipe_cc > cfg->valid_max_cc) ||
	    (ambient_cc < cfg->valid_min_cc) || (ambient_cc > cfg->valid_max_cc)) {
		/* Sticky: one implausible reading during commissioning is enough to
		 * fail the install. Better a second attempt by an installer who is
		 * standing right there than a device that reports for five years and
		 * is never believed. */
		st->implausible = true;
		return;
	}

	if (st->n == 0U) {
		st->pipe_min = pipe_cc;
		st->pipe_max = pipe_cc;
		st->amb_min = ambient_cc;
		st->amb_max = ambient_cc;
		st->first_pipe_cc = pipe_cc;
		st->first_amb_cc = ambient_cc;
		st->sep_max = 0;
	}

	if (pipe_cc < st->pipe_min) {
		st->pipe_min = pipe_cc;
	}
	if (pipe_cc > st->pipe_max) {
		st->pipe_max = pipe_cc;
	}
	if (ambient_cc < st->amb_min) {
		st->amb_min = ambient_cc;
	}
	if (ambient_cc > st->amb_max) {
		st->amb_max = ambient_cc;
	}

	sep = abs_i32(pipe_cc - ambient_cc);
	if (sep > st->sep_max) {
		st->sep_max = sep;
	}

	st->last_pipe_cc = pipe_cc;
	st->last_amb_cc = ambient_cc;

	if (st->n < 255U) {
		st->n++;
	}
}

enum install_result install_probe_finish(const struct install_probe_state *st,
					 const struct install_probe_cfg *cfg)
{
	struct install_probe_cfg scratch;
	int32_t pipe_swing;
	int32_t amb_swing;

	if (st == NULL) {
		return INSTALL_UNKNOWN;
	}
	cfg_or_defaults(&cfg, &scratch);

	if (st->implausible) {
		return INSTALL_FAIL_IMPLAUSIBLE;
	}
	if (st->n < cfg->min_samples) {
		return INSTALL_FAIL_INCOMPLETE;
	}

	pipe_swing = st->pipe_max - st->pipe_min;
	amb_swing = st->amb_max - st->amb_min;

	/*
	 * Order matters, and it is the order that gives the installer the most
	 * useful single instruction.
	 *
	 * Identical channels first: if the two sensors never separated, no other
	 * test means anything, and the fix ("the clamp is not on the pipe") is
	 * unambiguous.
	 */
	if (st->sep_max < cfg->min_separation_cc) {
		return INSTALL_FAIL_SENSORS_IDENTICAL;
	}

	/* Then: did the room move? Checked BEFORE the pipe response, because a
	 * room-wide thermal event can satisfy the pipe test for the wrong reason
	 * and we must not accept it. The failure instruction is "close the door /
	 * wait for the room to settle and retry". */
	if (amb_swing > cfg->max_ambient_response_cc) {
		return INSTALL_FAIL_AMBIENT_MOVED;
	}

	/* Finally: did the pipe respond to the fixture the installer ran? */
	if (pipe_swing < cfg->min_pipe_response_cc) {
		return INSTALL_FAIL_NO_THERMAL_RESPONSE;
	}

	return INSTALL_VERIFIED;
}

void install_record_init(struct install_record *rec)
{
	if (rec == NULL) {
		return;
	}
	rec->ts_s = 0U;
	rec->thermal = INSTALL_UNKNOWN;
	rec->delta_at_install_cc = 0;
	rec->valve_present = false;
	rec->valve.breakaway_torque_mnm = 0U;
	rec->valve.post_cycle_stem_leak = false;
	rec->valve.cycle_time_ms = 0U;
	rec->valve.completed = false;
}

bool install_record_sensor_verified(const struct install_record *rec)
{
	if (rec == NULL) {
		return false;
	}
	return install_result_is_verified(rec->thermal);
}
