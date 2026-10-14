/*
 * leak_detect.c — integer, allocation-free thermal leak / freeze detector.
 *
 * Mirrors detection/detector.py (contracts/INTERFACES.md §5). No Zephyr
 * includes, no libm, no malloc, no printf. See the header for the rationale.
 *
 * The signal, in one paragraph. Two TMP117s are clamped to the same pipe run:
 * one on the pipe, one reading the room. 06-hardware-and-devkits.md is explicit
 * that this is two TMP117s and not an NTC divider, because the thing being
 * measured is a small *persistent* delta against a noisy, drifting room. Water
 * moving through a pipe pulls the pipe toward supply temperature, so a draw-off
 * shows up as delta going negative. An appliance cycle does that for minutes
 * and then recovers; a leak does it and never recovers. Everything below is
 * machinery for telling those two apart without a labelled corpus, which is
 * what 08-roadmap.md P1 exists to go and collect.
 */

#include "leak_detect.h"

#define RING_MASK (LEAK_WINDOW_LEN - 1U)

/* The ring index is masked, not modulo'd. Enforce the precondition. */
_Static_assert((LEAK_WINDOW_LEN & RING_MASK) == 0U,
	       "LEAK_WINDOW_LEN must be a power of two");
_Static_assert(LEAK_WINDOW_LEN >= 8U, "window too short to characterise noise");

/*
 * Guard against anybody "improving" this by adding a growing buffer later
 * (§5 behaviour 6). If you legitimately grow the state, update the number and
 * say why in the commit message.
 */
_Static_assert(sizeof(struct leak_state) <= 256U,
	       "detector state must stay small enough to live on an M4 with "
	       "room for the LoRaWAN MAC; see INTERFACES.md §5");

static const char *const verdict_names[] = {
	[LEAK_VERDICT_QUIET] = "quiet",
	[LEAK_VERDICT_WATCH] = "watch",
	[LEAK_VERDICT_LEAK_SUSPECTED] = "leak_suspected",
	[LEAK_VERDICT_FREEZE_RISK] = "freeze_risk",
	[LEAK_VERDICT_SENSOR_FAULT] = "sensor_fault",
	[LEAK_VERDICT_INSTALL_UNVERIFIED] = "install_unverified",
};

const char *leak_verdict_name(enum leak_verdict v)
{
	if ((unsigned int)v >= (sizeof(verdict_names) / sizeof(verdict_names[0]))) {
		return "quiet"; /* never NULL: callers log this unconditionally */
	}
	return verdict_names[v];
}

void leak_cfg_defaults(struct leak_cfg *cfg)
{
	if (cfg == NULL) {
		return;
	}
	cfg->excursion_cc = 150;          /* 1.50 C below baseline */
	cfg->dwell_s = 1800U;             /* 30 min: longer than a shower */
	cfg->freeze_cc = 400;             /* 4.00 C */
	cfg->valid_min_cc = -4000;        /* -40 C, per §5 behaviour 3 */
	cfg->valid_max_cc = 8000;         /* +80 C */
	cfg->max_rate_cc_per_s = 100;     /* 1.00 C/s */
	cfg->noise_pp_cc = 800;           /* 8.00 C peak-to-peak in-window */
	cfg->noise_reversals_pct = 60U;
	cfg->baseline_shift = 6U;
	cfg->stuck_s = 3600U;             /* 1 h of bit-identical readings */
}

/* leak_cfg_defaults() is the only place default numbers live. Anything that
 * needs a config and was handed NULL gets this. */
static void cfg_or_defaults(const struct leak_cfg **cfg, struct leak_cfg *scratch)
{
	if (*cfg == NULL) {
		leak_cfg_defaults(scratch);
		*cfg = scratch;
	}
}

void leak_new_state(struct leak_state *st, const char *device_id)
{
	size_t i;

	if (st == NULL) {
		return;
	}

	for (i = 0U; i < sizeof(st->device_id); i++) {
		st->device_id[i] = '\0';
	}
	if (device_id != NULL) {
		for (i = 0U; (i < (sizeof(st->device_id) - 1U)) && (device_id[i] != '\0'); i++) {
			st->device_id[i] = device_id[i];
		}
	}

	for (i = 0U; i < LEAK_WINDOW_LEN; i++) {
		st->delta_ring[i] = 0;
		st->pipe_ring[i] = 0;
	}
	st->ring_head = 0U;
	st->ring_count = 0U;

	st->baseline_scaled = 0;
	st->baseline_cc = 0;
	st->baseline_primed = false;

	st->last_ts_s = 0U;
	st->last_pipe_cc = 0;
	st->have_last = false;

	st->identical_since_s = 0U;
	st->identical_valid = false;

	st->excursion_start_s = 0U;
	st->in_excursion = false;

	st->last_verdict = LEAK_VERDICT_QUIET;
}

static void ring_push(struct leak_state *st, int32_t delta_cc, int32_t pipe_cc)
{
	st->delta_ring[st->ring_head] = delta_cc;
	st->pipe_ring[st->ring_head] = pipe_cc;
	st->ring_head = (uint8_t)((st->ring_head + 1U) & RING_MASK);
	if (st->ring_count < LEAK_WINDOW_LEN) {
		st->ring_count++;
	}
}

/* Chronological access: 0 = oldest retained sample. */
static int32_t ring_at(const struct leak_state *st, const int32_t *ring, uint8_t idx)
{
	uint8_t slot = (uint8_t)((st->ring_head + LEAK_WINDOW_LEN - st->ring_count + idx)
				 & RING_MASK);
	return ring[slot];
}

/*
 * Sum of the n most recent deltas. Used instead of the instantaneous delta so
 * that one spike cannot open an excursion, and summed rather than averaged so
 * that the threshold comparison needs no divide at all — the threshold is
 * scaled by n on the other side instead.
 */
static int32_t recent_delta_sum(const struct leak_state *st, uint8_t n, uint8_t *used)
{
	int32_t sum = 0;
	uint8_t i;

	if (n > st->ring_count) {
		n = st->ring_count;
	}
	for (i = 0U; i < n; i++) {
		sum += ring_at(st, st->delta_ring, (uint8_t)(st->ring_count - 1U - i));
	}
	*used = n;
	return sum;
}

#define RECENT_N 4U

/*
 * Window shape of the pipe channel. Peak-to-peak plus the number of direction
 * reversals: a failing sensor rattles (many reversals, wide swing), whereas a
 * real hydraulic event ramps (few reversals, possibly wide swing). Comparing
 * only the swing would flag a genuine fast leak as a fault, which is the worst
 * possible confusion to make.
 */
static void pipe_window_shape(const struct leak_state *st, int32_t *pp, uint8_t *reversals)
{
	int32_t lo, hi, prev;
	int8_t last_sign = 0;
	uint8_t i;

	*pp = 0;
	*reversals = 0U;
	if (st->ring_count < 3U) {
		return;
	}

	lo = ring_at(st, st->pipe_ring, 0U);
	hi = lo;
	prev = lo;

	for (i = 1U; i < st->ring_count; i++) {
		int32_t v = ring_at(st, st->pipe_ring, i);
		int32_t d = v - prev;
		int8_t sign = (d > 0) ? 1 : ((d < 0) ? -1 : 0);

		if (v < lo) {
			lo = v;
		}
		if (v > hi) {
			hi = v;
		}
		if ((sign != 0) && (last_sign != 0) && (sign != last_sign)) {
			(*reversals)++;
		}
		if (sign != 0) {
			last_sign = sign;
		}
		prev = v;
	}

	*pp = hi - lo;
}

bool leak_cold_warning(const struct leak_cfg *cfg, int32_t pipe_cc)
{
	struct leak_cfg scratch;

	cfg_or_defaults(&cfg, &scratch);
	return pipe_cc <= cfg->freeze_cc;
}

static uint8_t clamp_conf(int32_t v)
{
	if (v < 0) {
		return 0U;
	}
	if (v > 100) {
		return 100U;
	}
	return (uint8_t)v;
}

struct leak_decision leak_update(struct leak_state *st, const struct leak_cfg *cfg,
				 const struct leak_sample *s)
{
	struct leak_cfg scratch;
	struct leak_decision dec;
	int64_t dt;
	uint32_t dt_s;
	bool temporal_ok;
	int32_t pp;
	uint8_t reversals;
	uint8_t used;
	int32_t sum;
	int32_t enter_threshold;
	int32_t exit_threshold;

	cfg_or_defaults(&cfg, &scratch);

	dec.verdict = LEAK_VERDICT_QUIET;
	dec.confidence = 0U;
	dec.reason = "no sample";
	dec.delta_cc = 0;
	dec.baseline_cc = (st != NULL) ? st->baseline_cc : 0;

	if ((st == NULL) || (s == NULL)) {
		return dec;
	}

	dec.delta_cc = s->pipe_cc - s->ambient_cc;   /* §1: the derived signal */
	dec.baseline_cc = st->baseline_cc;

	/*
	 * PRECEDENCE 1 — install verification (§5 behaviour 2).
	 *
	 * An unverified install is indistinguishable from a sensor fault, so it
	 * can never produce a leak verdict and must not be counted
	 * (08-roadmap.md §8 risk 4: "do not count unverified installs").
	 *
	 * We also refuse to *learn* from it. If the clamp is dangling in free air
	 * the delta is meaningless, and an EWMA that absorbed an hour of free-air
	 * readings would stay poisoned for an hour after the installer fixed the
	 * clamp — which turns one install error into a day of bad data.
	 */
	if (!s->install_verified) {
		st->in_excursion = false;
		st->last_verdict = LEAK_VERDICT_INSTALL_UNVERIFIED;
		dec.verdict = LEAK_VERDICT_INSTALL_UNVERIFIED;
		dec.confidence = 100U;
		dec.reason = "install not verified; clamp seating unconfirmed";
		return dec;
	}

	/* Timestamp sanity. A backwards clock is a reboot or an NVS restore, not a
	 * sensor fault: drop the temporal history and keep going, rather than
	 * emitting a fault the operator cannot action. */
	dt = (int64_t)s->ts_s - (int64_t)st->last_ts_s;
	temporal_ok = st->have_last && (dt > 0);
	if (st->have_last && (dt <= 0)) {
		st->have_last = false;
		st->in_excursion = false;
		st->identical_valid = false;
	}
	dt_s = temporal_ok ? (uint32_t)dt : 1U;

	/*
	 * PRECEDENCE 2 — tamper and physical plausibility (§5 behaviour 3).
	 * These produce sensor_fault, never leak. Faulted samples are NOT pushed
	 * into the window: one garbage reading must not contaminate the noise
	 * statistics for the next sixteen minutes.
	 */
	if (s->tamper) {
		st->in_excursion = false;
		st->last_verdict = LEAK_VERDICT_SENSOR_FAULT;
		dec.verdict = LEAK_VERDICT_SENSOR_FAULT;
		dec.confidence = 100U;
		dec.reason = "tamper asserted";
		return dec;
	}

	if ((s->pipe_cc < cfg->valid_min_cc) || (s->pipe_cc > cfg->valid_max_cc) ||
	    (s->ambient_cc < cfg->valid_min_cc) || (s->ambient_cc > cfg->valid_max_cc)) {
		st->in_excursion = false;
		st->last_verdict = LEAK_VERDICT_SENSOR_FAULT;
		dec.verdict = LEAK_VERDICT_SENSOR_FAULT;
		dec.confidence = 95U;
		dec.reason = "reading outside -40..80 C";
		return dec;
	}

	if (temporal_ok) {
		int32_t step = s->pipe_cc - st->last_pipe_cc;
		int64_t limit = (int64_t)cfg->max_rate_cc_per_s * (int64_t)dt_s;

		if (step < 0) {
			step = -step;
		}
		if ((int64_t)step > limit) {
			/* A clamped sensor on a pipe with real thermal mass cannot do
			 * this. Either the clamp came off mid-sample or the I2C read
			 * tore. Either way it is not hydraulics. */
			st->in_excursion = false;
			st->have_last = false;
			st->last_verdict = LEAK_VERDICT_SENSOR_FAULT;
			dec.verdict = LEAK_VERDICT_SENSOR_FAULT;
			dec.confidence = 85U;
			dec.reason = "step change faster than thermal mass allows";
			return dec;
		}
	}

	/* Stuck channel. Deliberately time-based rather than "all window samples
	 * equal": centi-degree quantisation of a TMP117 (7.8 mC resolution) means
	 * a genuinely still pipe can repeat a value for many minutes, so a short
	 * window would false-fault. An hour of bit-identical readings would not. */
	if (st->have_last && (s->pipe_cc == st->last_pipe_cc)) {
		if (!st->identical_valid) {
			st->identical_since_s = st->last_ts_s;
			st->identical_valid = true;
		}
	} else {
		st->identical_valid = false;
	}
	if (st->identical_valid && temporal_ok &&
	    ((s->ts_s - st->identical_since_s) >= cfg->stuck_s)) {
		st->in_excursion = false;
		st->last_verdict = LEAK_VERDICT_SENSOR_FAULT;
		dec.verdict = LEAK_VERDICT_SENSOR_FAULT;
		dec.confidence = 80U;
		dec.reason = "pipe channel stuck";
		return dec;
	}

	/* Sample is plausible: it may enter the window. */
	ring_push(st, dec.delta_cc, s->pipe_cc);
	st->last_ts_s = s->ts_s;
	st->last_pipe_cc = s->pipe_cc;
	st->have_last = true;

	pipe_window_shape(st, &pp, &reversals);
	if ((st->ring_count == LEAK_WINDOW_LEN) && (pp > cfg->noise_pp_cc) &&
	    (((uint32_t)reversals * 100U) >=
	     ((uint32_t)cfg->noise_reversals_pct * (uint32_t)(st->ring_count - 2U)))) {
		st->in_excursion = false;
		st->last_verdict = LEAK_VERDICT_SENSOR_FAULT;
		dec.verdict = LEAK_VERDICT_SENSOR_FAULT;
		dec.confidence = 75U;
		dec.reason = "pipe channel noisy beyond plausibility";
		return dec;
	}

	/*
	 * Baseline. Per-device, adaptive (§5 behaviour 5): housing stock varies so
	 * much that a single global delta threshold is wrong — a slab-on-grade
	 * house in Phoenix and a stone basement in Vermont do not share a
	 * quiescent delta. Seeded from the first good sample so it converges in
	 * one step instead of 64, then an EWMA by shift.
	 *
	 * Adaptation is FROZEN while an excursion is open. Without that freeze a
	 * slow leak teaches the detector that the leak is normal — the baseline
	 * walks down to meet it and the alert never fires. This is the single
	 * most important line in the function.
	 */
	if (!st->baseline_primed && (st->ring_count == 1U)) {
		st->baseline_scaled = dec.delta_cc * LEAK_BASELINE_SCALE;
		st->baseline_cc = dec.delta_cc;
	}
	if (st->ring_count >= LEAK_WINDOW_LEN) {
		st->baseline_primed = true;
	}

	enter_threshold = st->baseline_cc - cfg->excursion_cc;
	/* Asymmetric exit (hysteresis) so a delta sitting exactly on the threshold
	 * does not flap between watch and quiet once a minute, restarting the
	 * dwell timer each time and never reaching a verdict. */
	exit_threshold = st->baseline_cc - (cfg->excursion_cc / 2);

	sum = recent_delta_sum(st, RECENT_N, &used);

	if (st->in_excursion) {
		if (sum > ((int32_t)used * exit_threshold)) {
			st->in_excursion = false;
		}
	} else {
		if (st->baseline_primed && (sum < ((int32_t)used * enter_threshold))) {
			st->in_excursion = true;
			st->excursion_start_s = s->ts_s;
		}
	}

	if (!st->in_excursion) {
		int32_t target = dec.delta_cc * LEAK_BASELINE_SCALE;

		/* Arithmetic right shift on a negative value floors rather than
		 * truncates toward zero, which biases the baseline low by at most
		 * one 1/256 cc step per update. Harmless at this scale, and the
		 * alternative — a divide — buys nothing. */
		st->baseline_scaled += (target - st->baseline_scaled) >> cfg->baseline_shift;
		st->baseline_cc = st->baseline_scaled >> LEAK_BASELINE_SHIFT;
	}
	dec.baseline_cc = st->baseline_cc;

	/*
	 * PRECEDENCE 3 — freeze risk (§5 behaviour 4). Application A4, free upside
	 * on the same sensor (02-application-portfolio.md). Outranks the leak
	 * verdict because it is the more urgent and more actionable of the two, and
	 * because a pipe at 2 C that is also drawing cold water is a freeze story
	 * first. Excursion bookkeeping above still ran, so the moment the pipe
	 * warms past the threshold a qualifying leak reports immediately.
	 */
	if (s->pipe_cc <= cfg->freeze_cc) {
		st->last_verdict = LEAK_VERDICT_FREEZE_RISK;
		dec.verdict = LEAK_VERDICT_FREEZE_RISK;
		dec.confidence = clamp_conf(60 + ((cfg->freeze_cc - s->pipe_cc) / 10));
		dec.reason = "pipe at or below freeze threshold";
		return dec;
	}

	/*
	 * PRECEDENCE 4 — the leak itself (§5 behaviour 1). A persistent negative
	 * delta sustained beyond the dwell window. Brief excursions from appliance
	 * use must not trigger: that is the nuisance problem, it is risk 2 in
	 * 08-roadmap.md §8, and it is what the P1 gate measures. The dwell timer is
	 * in seconds against the sample's own timestamp rather than a sample count,
	 * so a missed wake-up or a changed heartbeat cannot shorten it.
	 */
	if (st->in_excursion) {
		uint32_t held_s = s->ts_s - st->excursion_start_s;

		if (held_s >= cfg->dwell_s) {
			int32_t depth = st->baseline_cc - (sum / (int32_t)used);
			int32_t over = (int32_t)((held_s - cfg->dwell_s) / 60U);

			st->last_verdict = LEAK_VERDICT_LEAK_SUSPECTED;
			dec.verdict = LEAK_VERDICT_LEAK_SUSPECTED;
			/* Confidence in the verdict: deeper and longer is more certain.
			 * One point per 0.5 C of depth beyond the excursion threshold,
			 * one per minute held beyond dwell, on a base of 60. */
			dec.confidence = clamp_conf(60 + ((depth - cfg->excursion_cc) / 50) + over);
			dec.reason = "negative delta sustained beyond dwell window";
			return dec;
		}

		st->last_verdict = LEAK_VERDICT_WATCH;
		dec.verdict = LEAK_VERDICT_WATCH;
		dec.confidence = clamp_conf(20 + (int32_t)((held_s * 30U) / cfg->dwell_s));
		dec.reason = "negative delta excursion open, dwell not met";
		return dec;
	}

	st->last_verdict = LEAK_VERDICT_QUIET;
	dec.verdict = LEAK_VERDICT_QUIET;
	if (!st->baseline_primed) {
		/* Honest about its own ignorance: the backend should not treat a
		 * freshly installed device's quiet as evidence of anything. */
		dec.confidence = 30U;
		dec.reason = "learning per-device baseline";
	} else {
		dec.confidence = 80U;
		dec.reason = "delta at baseline";
	}
	return dec;
}

/*
 * KNOWN LIMITATION, stated rather than hidden
 * -------------------------------------------
 * A delta-from-learned-baseline detector cannot see a leak that was already
 * running when the device was installed: the first good sample seeds the
 * baseline, so the leak becomes "normal" for that device. Catching that case
 * needs an absolute cross-fleet comparison, which lives in the backend where
 * the housing-stock context is, not on a node that has never seen another node.
 * install_probe.c records the absolute delta at commissioning time precisely so
 * that the backend has the datum it needs to make that call.
 */
