/*
 * leak_detect.h — on-device thermal leak / freeze detector.
 *
 * WHAT THIS IS: the C twin of detection/detector.py. Same verdict names, same
 * required behaviours, same precedence (contracts/INTERFACES.md §5). The Python
 * version is the one that gets evaluated against the labelled simulator corpus
 * and the P1 gate (<0.5 nuisance alerts/device-year, 08-roadmap.md §9); this
 * version is the one that has to run in single-digit kB on a Cortex-M4 when the
 * classifier moves on-device at P2 (08-roadmap.md §4, "no NN on the node before
 * P2"). They must agree, so both are written to the same restrictions:
 *
 *   - no allocation at all, let alone in the hot path. The whole state is one
 *     caller-owned struct with fixed-size arrays;
 *   - no floating point. Everything is int32 centi-degrees, which is also the
 *     wire unit (§1), so there is no conversion step to get wrong;
 *   - a fixed-size ring window, never a growing buffer (§5 behaviour 6).
 *
 * DIVERGENCE FROM detection/detector.py, which landed after this file: the two
 * agree on all six verdict strings, on the precedence (including that
 * install_unverified outranks sensor_fault), on freezing the baseline during an
 * excursion, on fixed-point integer baselines, on the -40..80 C band, on the
 * 4.0 C freeze threshold and on the one-hour stuck span. They differ on the
 * dwell MECHANISM — this file uses a timer over a continuous excursion, the
 * Python uses a net credit/release integrator — and therefore on every dwell
 * constant, plus the Python's learned MAD noise floor, freeze hysteresis and
 * verdict latching. detector.py is authoritative, because it is the one
 * measured against the labelled corpus and the P1 gate. Do not copy its
 * constants across without porting its mechanism: "6 hours net" and "6 hours
 * uninterrupted" are not the same sensitivity. See README.md.
 *
 * WHAT THIS DELIBERATELY IS NOT:
 *   - it is not a classifier with learned weights. There is no labelled winter
 *     of data yet; that is the whole premise of 08-roadmap.md P1;
 *   - it does not decide to close the valve. It returns a verdict. The valve
 *     policy is in valve.c and the decision site is in main.c, commented;
 *   - it has no Zephyr dependency, so tests/test_leak_detect.c runs on a host.
 */

#ifndef APP_LEAK_DETECT_H_
#define APP_LEAK_DETECT_H_

#include <stdbool.h>
#include <stddef.h>   /* NULL, size_t — this file is built with no RTOS headers */
#include <stdint.h>

/*
 * Window length in samples. 16 at the default 60 s sample period is 16 minutes
 * of history, which is enough to characterise sensor noise and stuck-ness
 * without holding a leak-length record. Must be a power of two: the ring index
 * uses a mask, not a modulo, because the hot path should not divide.
 */
#define LEAK_WINDOW_LEN 16U

/*
 * Fixed-point scale for the adaptive baseline. A power of two so the
 * conversion is a shift; see the comment on baseline_scaled below for why the
 * extra precision is load-bearing rather than tidy.
 */
#define LEAK_BASELINE_SHIFT 8
#define LEAK_BASELINE_SCALE (1 << LEAK_BASELINE_SHIFT)

/* §5 VERDICTS, in the same order, with the same spellings. */
enum leak_verdict {
	LEAK_VERDICT_QUIET = 0,
	LEAK_VERDICT_WATCH,
	LEAK_VERDICT_LEAK_SUSPECTED,
	LEAK_VERDICT_FREEZE_RISK,
	LEAK_VERDICT_SENSOR_FAULT,
	LEAK_VERDICT_INSTALL_UNVERIFIED,
};

/* Returns the exact string in §5's VERDICTS tuple. Never NULL. */
const char *leak_verdict_name(enum leak_verdict v);

/* §5's Sample, in integer form. pipe_cc/ambient_cc are centi-degrees C. */
struct leak_sample {
	uint32_t ts_s;        /* unix seconds; monotonic within a device */
	int32_t pipe_cc;
	int32_t ambient_cc;
	uint16_t battery_mv;
	bool install_verified;
	bool tamper;
};

/* §5's Decision. `reason` points at a static string — never freed, never
 * formatted at runtime, so there is no snprintf in the hot path either. */
struct leak_decision {
	enum leak_verdict verdict;
	uint8_t confidence;     /* 0..100 */
	const char *reason;
	int32_t delta_cc;
	int32_t baseline_cc;
};

/*
 * Tunables. Passed in rather than read from Kconfig so that this file has no
 * build-system dependency and so tests can sweep them. main.c fills this from
 * CONFIG_APP_* (see Kconfig); leak_cfg_defaults() is the single source of the
 * default values and is what the host tests use.
 *
 * Every number here is a starting point to be calibrated against bench and
 * field data, not a measured result. 08-roadmap.md §8 risk 7 is explicit about
 * measuring rather than modelling, and the same applies to these thresholds.
 */
struct leak_cfg {
	/* How far below the learned baseline delta counts as an excursion. */
	int32_t excursion_cc;

	/* How long an excursion must persist before it is a leak rather than
	 * somebody having a shower. This is THE nuisance-rate knob (§5 behaviour 1,
	 * 08-roadmap.md §8 risk 2). Default 1800 s = 30 min, chosen to sit beyond
	 * the longest ordinary draw-off event in the simulator's scenario list
	 * (shower_morning, appliance_use) and well short of the hours that make a
	 * slow leak expensive. */
	uint32_t dwell_s;

	/* Pipe temperature at or below which we call freeze risk (§5 behaviour 4,
	 * application A4). Also drives the cold_warning flag in §1 byte 6, so that
	 * the flag and the verdict can never disagree. */
	int32_t freeze_cc;

	/* Physical plausibility gate (§5 behaviour 3). */
	int32_t valid_min_cc;         /* -40 C */
	int32_t valid_max_cc;         /* +80 C */
	int32_t max_rate_cc_per_s;    /* thermal-mass limit on |d(pipe)/dt| */

	/* Stuck/noisy sensor discrimination over the full window. */
	int32_t noise_pp_cc;          /* peak-to-peak above which it is noise */
	uint8_t noise_reversals_pct;  /* direction reversals, % of window */

	/* How long the pipe channel must return bit-identical readings before we
	 * call it stuck. Time-based rather than window-based on purpose: at
	 * centi-degree quantisation a genuinely still pipe repeats a value for
	 * minutes, so a 16-sample "all equal" test would false-fault. */
	uint32_t stuck_s;

	/* Baseline EWMA aggressiveness: baseline += (delta - baseline) >> shift.
	 * Shift 6 is a ~64-sample time constant, about an hour at 60 s sampling —
	 * fast enough to learn a cold basement (§5 behaviour 5, scenario
	 * cold_basement_winter), slow enough not to swallow a developing leak,
	 * and the adaptation is additionally frozen during an excursion (see .c). */
	uint8_t baseline_shift;
};

void leak_cfg_defaults(struct leak_cfg *cfg);

/*
 * Detector state. Fixed size, no pointers to heap, safe to put in .bss or to
 * persist verbatim. sizeof() is asserted in the .c file to keep anybody from
 * quietly adding a growing buffer later (§5 behaviour 6).
 */
struct leak_state {
	char device_id[20];            /* for logs only; never used in the maths */

	int32_t delta_ring[LEAK_WINDOW_LEN];
	int32_t pipe_ring[LEAK_WINDOW_LEN];
	uint8_t ring_head;
	uint8_t ring_count;

	/*
	 * The baseline is held at 1/256 cc internally and reported in whole cc.
	 *
	 * The extra precision is not cosmetic. A naive integer EWMA —
	 * `baseline += (delta - baseline) >> 6` — stalls permanently once
	 * |delta - baseline| drops below 64, because the shift truncates to zero.
	 * That leaves a standing offset of up to 63 cc, i.e. 0.63 C, against an
	 * excursion threshold of 1.50 C. A 40% error in the main detection
	 * threshold, produced entirely by rounding, and invisible unless you go
	 * looking: the baseline would simply never quite reach the truth.
	 * Scaling by 256 moves the stall point to 0.25 cc.
	 */
	int32_t baseline_scaled;    /* baseline_cc * LEAK_BASELINE_SCALE */
	int32_t baseline_cc;        /* reported value, derived */
	bool baseline_primed;

	uint32_t last_ts_s;
	int32_t last_pipe_cc;
	bool have_last;

	uint32_t identical_since_s;    /* stuck-channel timer */
	bool identical_valid;

	uint32_t excursion_start_s;
	bool in_excursion;

	enum leak_verdict last_verdict;
};

/* §5: new_state(device_id). device_id may be NULL. */
void leak_new_state(struct leak_state *st, const char *device_id);

/*
 * §5: update(state, s) -> Decision. Mutates *st, performs no I/O, allocates
 * nothing, and takes the same number of branches regardless of verdict.
 * cfg may be NULL, in which case defaults are used.
 */
struct leak_decision leak_update(struct leak_state *st, const struct leak_cfg *cfg,
				 const struct leak_sample *s);

/*
 * The §1 byte-6 cold_warning flag, derived from the same threshold as the
 * freeze_risk verdict so the wire flag and the local verdict cannot disagree.
 */
bool leak_cold_warning(const struct leak_cfg *cfg, int32_t pipe_cc);

#endif /* APP_LEAK_DETECT_H_ */
