/*
 * test_leak_detect.c — one test per required behaviour in
 * contracts/INTERFACES.md §5, plus the scenarios §6 names as P1 ground truth.
 *
 * detection/detector.py is being written against the same six behaviours in
 * Python. These tests are the firmware half of that agreement: same verdict
 * strings, same precedence, same dwell semantics. Where §5 leaves something
 * unstated, this file says which choice was made and flags it — see the
 * PRECEDENCE and CONFIDENCE notes below, both of which are places the Python
 * and C implementations could silently disagree.
 *
 * NOT EXECUTED. No compiler on this machine. See tests/README.md.
 */

#include <stdio.h>

#include "../src/leak_detect.h"
#include "minitest.h"

#define SAMPLE_DT 60U
#define QUIET_PIPE 2000
#define QUIET_AMB 2000

static struct leak_state st;
static struct leak_cfg cfg;

static void reset(void)
{
	leak_cfg_defaults(&cfg);
	leak_new_state(&st, "leak-0001");
}

static struct leak_decision feed(uint32_t ts, int32_t pipe, int32_t amb, bool verified,
				 bool tamper)
{
	struct leak_sample s;

	s.ts_s = ts;
	s.pipe_cc = pipe;
	s.ambient_cc = amb;
	s.battery_mv = 3600;
	s.install_verified = verified;
	s.tamper = tamper;
	return leak_update(&st, &cfg, &s);
}

/* Fill the window so the baseline is primed. Returns the next timestamp. */
static uint32_t prime(int32_t pipe, int32_t amb)
{
	uint32_t ts;
	unsigned int i;

	for (i = 1; i <= 20; i++) {
		ts = i * SAMPLE_DT;
		(void)feed(ts, pipe, amb, true, false);
	}
	return 21U * SAMPLE_DT;
}

/* ------------------------------------------------------------------------- */

static void test_verdict_names_match_contract(void)
{
	/* §5's VERDICTS tuple, spelled exactly. The codec, the backend, the
	 * evaluation harness and the operator UI all key off these strings, so a
	 * typo here is a silently-dropped alert class. */
	MT_CASE("§5 VERDICTS spellings");
	MT_EQS(leak_verdict_name(LEAK_VERDICT_QUIET), "quiet");
	MT_EQS(leak_verdict_name(LEAK_VERDICT_WATCH), "watch");
	MT_EQS(leak_verdict_name(LEAK_VERDICT_LEAK_SUSPECTED), "leak_suspected");
	MT_EQS(leak_verdict_name(LEAK_VERDICT_FREEZE_RISK), "freeze_risk");
	MT_EQS(leak_verdict_name(LEAK_VERDICT_SENSOR_FAULT), "sensor_fault");
	MT_EQS(leak_verdict_name(LEAK_VERDICT_INSTALL_UNVERIFIED), "install_unverified");

	MT_CASE("verdict name is never NULL, even out of range");
	MT_TRUE(leak_verdict_name((enum leak_verdict)99) != NULL);
}

/* §5 behaviour 2 — and the stronger half of it: NEVER a leak verdict. */
static void test_install_unverified(void)
{
	uint32_t ts;
	unsigned int i;
	struct leak_decision d;

	MT_CASE("§5.2 unverified install reports install_unverified");
	reset();
	d = feed(SAMPLE_DT, QUIET_PIPE, QUIET_AMB, false, false);
	MT_EQI(d.verdict, LEAK_VERDICT_INSTALL_UNVERIFIED);
	MT_EQI(d.confidence, 100);

	/*
	 * The scenario §6 calls install_unverified: a device with an unseated
	 * clamp sitting in what looks exactly like a severe leak. It must never
	 * produce a leak verdict, because an unverified install is
	 * indistinguishable from a sensor fault and 08-roadmap.md §8 risk 4 says
	 * not to count it at all.
	 */
	MT_CASE("§5.2 unverified install NEVER yields a leak verdict");
	reset();
	for (i = 1; i <= 300; i++) {
		d = feed(i * SAMPLE_DT, QUIET_PIPE - 1000, QUIET_AMB, false, false);
		MT_TRUE(d.verdict == LEAK_VERDICT_INSTALL_UNVERIFIED);
	}

	/* Losing verification mid-flight must take effect on the same sample, and
	 * must abandon any excursion in progress — the excursion was measured
	 * against a placement we no longer trust. */
	MT_CASE("§5.2 losing verification mid-excursion drops the excursion");
	reset();
	ts = prime(QUIET_PIPE, QUIET_AMB);
	for (i = 0; i < 10; i++) {
		(void)feed(ts + (i * SAMPLE_DT), QUIET_PIPE - 300, QUIET_AMB, true, false);
	}
	d = feed(ts + (10 * SAMPLE_DT), QUIET_PIPE - 300, QUIET_AMB, false, false);
	MT_EQI(d.verdict, LEAK_VERDICT_INSTALL_UNVERIFIED);
}

/* §5 behaviour 3 — tamper and implausible readings are sensor_fault, not leak. */
static void test_sensor_fault(void)
{
	struct leak_decision d;
	uint32_t ts;

	MT_CASE("§5.3 tamper yields sensor_fault, not leak");
	reset();
	ts = prime(QUIET_PIPE, QUIET_AMB);
	d = feed(ts, QUIET_PIPE - 2000, QUIET_AMB, true, true);
	MT_EQI(d.verdict, LEAK_VERDICT_SENSOR_FAULT);

	MT_CASE("§5.3 reading above +80 C is sensor_fault");
	reset();
	d = feed(SAMPLE_DT, 9000, QUIET_AMB, true, false);
	MT_EQI(d.verdict, LEAK_VERDICT_SENSOR_FAULT);

	MT_CASE("§5.3 reading below -40 C is sensor_fault");
	reset();
	d = feed(SAMPLE_DT, -5000, QUIET_AMB, true, false);
	MT_EQI(d.verdict, LEAK_VERDICT_SENSOR_FAULT);

	MT_CASE("§5.3 ambient channel is range-checked too");
	reset();
	d = feed(SAMPLE_DT, QUIET_PIPE, 9000, true, false);
	MT_EQI(d.verdict, LEAK_VERDICT_SENSOR_FAULT);

	/* "a step change faster than thermal mass allows". 200 cc in one second is
	 * 2 C/s on a clamped pipe, which is not hydraulics. */
	MT_CASE("§5.3 step faster than thermal mass is sensor_fault");
	reset();
	(void)feed(100U, QUIET_PIPE, QUIET_AMB, true, false);
	d = feed(101U, QUIET_PIPE + 200, QUIET_AMB, true, false);
	MT_EQI(d.verdict, LEAK_VERDICT_SENSOR_FAULT);

	/* The same magnitude spread over a minute is a plausible fast draw-off and
	 * must NOT be called a fault — this is the discrimination that matters,
	 * because calling a real fast leak a sensor fault loses the event. */
	MT_CASE("the same step over a minute is NOT a fault");
	reset();
	(void)feed(100U, QUIET_PIPE, QUIET_AMB, true, false);
	d = feed(160U, QUIET_PIPE + 200, QUIET_AMB, true, false);
	MT_TRUE(d.verdict != LEAK_VERDICT_SENSOR_FAULT);
}

/* §6 scenario sensor_fault_stuck. */
static void test_stuck_channel(void)
{
	struct leak_decision d;
	unsigned int i;

	MT_CASE("§6 sensor_fault_stuck: an hour of identical readings");
	reset();
	/* Deliberately NOT flagged early. At centi-degree quantisation a TMP117
	 * on a still pipe can repeat a value for many minutes, so a short
	 * window-equality test would false-fault a healthy device. */
	for (i = 1; i <= 60; i++) {
		d = feed(i * SAMPLE_DT, QUIET_PIPE, QUIET_AMB, true, false);
		MT_TRUE(d.verdict != LEAK_VERDICT_SENSOR_FAULT);
	}
	d = feed(61U * SAMPLE_DT, QUIET_PIPE, QUIET_AMB, true, false);
	MT_EQI(d.verdict, LEAK_VERDICT_SENSOR_FAULT);
}

/* §6 scenario sensor_fault_noisy. */
static void test_noisy_channel(void)
{
	struct leak_decision d;
	unsigned int i;

	MT_CASE("§6 sensor_fault_noisy: wide swing plus many reversals");
	reset();
	for (i = 1; i < LEAK_WINDOW_LEN; i++) {
		d = feed(i * SAMPLE_DT, ((i % 2U) == 0U) ? 2900 : 2000, QUIET_AMB, true, false);
		MT_TRUE(d.verdict != LEAK_VERDICT_SENSOR_FAULT);
	}
	d = feed(LEAK_WINDOW_LEN * SAMPLE_DT, 2900, QUIET_AMB, true, false);
	MT_EQI(d.verdict, LEAK_VERDICT_SENSOR_FAULT);

	/*
	 * And the discrimination that earns the reversal count its place: a
	 * monotonic ramp of the SAME peak-to-peak magnitude is a real hydraulic
	 * event, not noise. Counting only the swing would classify every fast leak
	 * as a broken sensor.
	 */
	MT_CASE("a monotonic ramp of equal magnitude is NOT noise");
	reset();
	for (i = 1; i <= LEAK_WINDOW_LEN; i++) {
		d = feed(i * SAMPLE_DT, 2900 - (int32_t)(i * 60U), QUIET_AMB, true, false);
		MT_TRUE(d.verdict != LEAK_VERDICT_SENSOR_FAULT);
	}
}

/* §5 behaviour 4 — freeze threshold, application A4. */
static void test_freeze_risk(void)
{
	struct leak_decision d;
	uint32_t ts;

	MT_CASE("§5.4 pipe below the freeze threshold yields freeze_risk");
	reset();
	ts = prime(QUIET_PIPE, QUIET_AMB);
	d = feed(ts, 300, QUIET_AMB, true, false);
	MT_EQI(d.verdict, LEAK_VERDICT_FREEZE_RISK);

	/* freeze_risk outranks the leak verdict. A pipe at 3 C drawing cold water
	 * is a freeze story first — it is the more urgent and more actionable of
	 * the two. §5 does not state a precedence between them; this is the choice
	 * made here and detector.py must make the same one. */
	MT_CASE("PRECEDENCE: freeze_risk outranks leak_suspected");
	MT_TRUE(d.delta_cc < -cfg.excursion_cc);

	MT_CASE("the cold_warning flag shares the freeze threshold exactly");
	MT_TRUE(leak_cold_warning(&cfg, cfg.freeze_cc));
	MT_TRUE(leak_cold_warning(&cfg, cfg.freeze_cc - 1));
	MT_TRUE(!leak_cold_warning(&cfg, cfg.freeze_cc + 1));

	MT_CASE("PRECEDENCE: sensor_fault outranks freeze_risk");
	reset();
	(void)prime(QUIET_PIPE, QUIET_AMB);
	d = feed(21U * SAMPLE_DT, 300, QUIET_AMB, true, true);
	MT_EQI(d.verdict, LEAK_VERDICT_SENSOR_FAULT);
}

/* §5 behaviour 1 — sustained negative delta beyond the dwell window. */
static void test_slow_leak(void)
{
	struct leak_decision d;
	uint32_t ts;
	uint32_t t;
	uint32_t first_leak_ts = 0U;
	uint32_t leak_began_ts;
	bool saw_watch = false;

	MT_CASE("§6 slow_leak: leak_suspected only after the dwell window");
	reset();
	ts = prime(QUIET_PIPE, QUIET_AMB);
	leak_began_ts = ts;

	for (t = ts; t <= (ts + 4800U); t += SAMPLE_DT) {
		d = feed(t, QUIET_PIPE - 300, QUIET_AMB, true, false);
		if (d.verdict == LEAK_VERDICT_WATCH) {
			saw_watch = true;
		}
		if ((d.verdict == LEAK_VERDICT_LEAK_SUSPECTED) && (first_leak_ts == 0U)) {
			first_leak_ts = t;
		}
	}

	MT_TRUE(first_leak_ts != 0U);
	MT_TRUE(saw_watch);

	/* Not before the dwell window has actually elapsed since the leak began. */
	MT_TRUE(first_leak_ts >= (leak_began_ts + cfg.dwell_s));
	/* And not unreasonably late: the excursion takes a few samples to open
	 * because the test is on a boxcar of the last four deltas, not a single
	 * sample. Ten minutes of slack covers that. */
	MT_TRUE(first_leak_ts <= (leak_began_ts + cfg.dwell_s + 600U));

	MT_CASE("§5 leak_suspected reports confidence and a per-device baseline");
	MT_TRUE(d.verdict == LEAK_VERDICT_LEAK_SUSPECTED);
	MT_TRUE(d.confidence >= 60U);
	MT_TRUE(d.confidence <= 100U);
	MT_EQI(d.delta_cc, -300);
	MT_TRUE(d.reason != NULL);

	/*
	 * The single most important property in the file: the baseline must NOT
	 * have walked down to meet the leak. If adaptation were allowed to
	 * continue during an excursion, a slow leak would teach the detector that
	 * the leak is normal and the alert would never fire at all.
	 */
	MT_CASE("baseline adaptation is frozen during an excursion");
	MT_TRUE(d.baseline_cc > -100);
}

/* §5 behaviour 1, the other half: "brief excursions from normal appliance use
 * must not trigger — this is the nuisance problem." */
static void test_nuisance_rejection(void)
{
	struct leak_decision d;
	uint32_t ts;
	unsigned int i;
	bool saw_leak = false;

	MT_CASE("§6 appliance_use: a 10-minute draw-off never triggers");
	reset();
	ts = prime(QUIET_PIPE, QUIET_AMB);
	for (i = 0; i < 10; i++) {
		d = feed(ts + (i * SAMPLE_DT), QUIET_PIPE - 300, QUIET_AMB, true, false);
		saw_leak |= (d.verdict == LEAK_VERDICT_LEAK_SUSPECTED);
	}
	for (i = 10; i < 40; i++) {
		d = feed(ts + (i * SAMPLE_DT), QUIET_PIPE, QUIET_AMB, true, false);
		saw_leak |= (d.verdict == LEAK_VERDICT_LEAK_SUSPECTED);
	}
	MT_TRUE(!saw_leak);
	MT_EQI(d.verdict, LEAK_VERDICT_QUIET);

	/* A 25-minute shower is still inside a 30-minute dwell window. This is the
	 * test that the P1 exit gate (<0.5 nuisance alerts/device-year) lives or
	 * dies on, and the reason dwell_s is a Kconfig knob rather than a
	 * constant. */
	MT_CASE("§6 shower_morning: 25 minutes of draw-off never triggers");
	reset();
	ts = prime(QUIET_PIPE, QUIET_AMB);
	saw_leak = false;
	for (i = 0; i < 25; i++) {
		d = feed(ts + (i * SAMPLE_DT), QUIET_PIPE - 400, QUIET_AMB, true, false);
		saw_leak |= (d.verdict == LEAK_VERDICT_LEAK_SUSPECTED);
	}
	for (i = 25; i < 60; i++) {
		d = feed(ts + (i * SAMPLE_DT), QUIET_PIPE, QUIET_AMB, true, false);
		saw_leak |= (d.verdict == LEAK_VERDICT_LEAK_SUSPECTED);
	}
	MT_TRUE(!saw_leak);

	/* Repeated short events must not accumulate into an alert either: the
	 * dwell timer restarts, it does not integrate. Three showers in a morning
	 * is a Tuesday, not a claim. */
	MT_CASE("repeated brief events do not accumulate");
	reset();
	ts = prime(QUIET_PIPE, QUIET_AMB);
	saw_leak = false;
	for (i = 0; i < 180; i++) {
		int32_t pipe = ((i % 30U) < 10U) ? (QUIET_PIPE - 350) : QUIET_PIPE;

		d = feed(ts + (i * SAMPLE_DT), pipe, QUIET_AMB, true, false);
		saw_leak |= (d.verdict == LEAK_VERDICT_LEAK_SUSPECTED);
	}
	MT_TRUE(!saw_leak);
}

/*
 * §5 behaviour 5 — "The baseline adapts per device — housing stock varies, so a
 * fixed global threshold is wrong."
 *
 * §6 scenario cold_basement_winter. This is the test that proves the baseline
 * is doing real work: in a cold basement the quiescent delta is strongly
 * POSITIVE (the pipe is warmer than the room), and the leak signature is a
 * positive delta getting smaller. A global "negative delta" rule detects
 * nothing here, however it is tuned.
 */
static void test_baseline_adapts_per_device(void)
{
	struct leak_decision d;
	unsigned int i;
	uint32_t ts;
	uint32_t t;
	bool saw_leak = false;

	MT_CASE("§6 cold_basement_winter: baseline learns a positive delta");
	reset();
	(void)prime(QUIET_PIPE, QUIET_AMB);

	/* Pipe 8.00 C in a 5.00 C basement: quiescent delta +300 cc. */
	for (i = 0; i < 400; i++) {
		d = feed((21U + i) * SAMPLE_DT, 800, 500, true, false);
		saw_leak |= (d.verdict == LEAK_VERDICT_LEAK_SUSPECTED);
	}
	MT_TRUE(!saw_leak);
	MT_EQI(d.verdict, LEAK_VERDICT_QUIET);
	MT_TRUE(d.baseline_cc > 280);
	MT_TRUE(d.baseline_cc <= 300);

	/*
	 * Now a leak on THIS device: pipe pulled to 5.50 C, delta +50. Still a
	 * positive delta — so a global negative-delta threshold reports nothing —
	 * but 250 cc below what this device has learned is normal.
	 */
	MT_CASE("§5.5 a leak is relative to the learned baseline, not an absolute");
	ts = 421U * SAMPLE_DT;
	saw_leak = false;
	for (t = ts; t <= (ts + 4800U); t += SAMPLE_DT) {
		d = feed(t, 550, 500, true, false);
		if (d.verdict == LEAK_VERDICT_LEAK_SUSPECTED) {
			saw_leak = true;
		}
	}
	MT_TRUE(saw_leak);
	MT_TRUE(d.delta_cc > 0); /* positive delta, and still a leak */
}

/* §6 scenario fast_leak: a deep excursion still honours the dwell window, but
 * reports higher confidence than a shallow one. */
static void test_fast_leak_confidence(void)
{
	struct leak_decision shallow;
	struct leak_decision deep;
	uint32_t ts;
	uint32_t t;

	MT_CASE("§6 fast_leak: deeper excursion reports higher confidence");
	reset();
	ts = prime(QUIET_PIPE, QUIET_AMB);
	shallow.confidence = 0U;
	for (t = ts; t <= (ts + 3600U); t += SAMPLE_DT) {
		shallow = feed(t, QUIET_PIPE - 200, QUIET_AMB, true, false);
	}

	reset();
	ts = prime(QUIET_PIPE, QUIET_AMB);
	deep.confidence = 0U;
	for (t = ts; t <= (ts + 3600U); t += SAMPLE_DT) {
		deep = feed(t, QUIET_PIPE - 1200, QUIET_AMB, true, false);
	}

	MT_EQI(shallow.verdict, LEAK_VERDICT_LEAK_SUSPECTED);
	MT_EQI(deep.verdict, LEAK_VERDICT_LEAK_SUSPECTED);
	MT_TRUE(deep.confidence > shallow.confidence);

	/*
	 * CONFIDENCE NOTE, flagged because §5 does not define it: `confidence` is
	 * read here as "confidence in the verdict", 0..100, monotonic in excursion
	 * depth and in time held beyond dwell. §5 gives the field and its range
	 * but not its meaning, so detector.py could reasonably have read it as
	 * "probability of a leak" and produced a low number for `quiet`. The two
	 * implementations must agree, and this is the reading.
	 */
	MT_CASE("quiet carries low confidence while the baseline is still learning");
	reset();
	MT_TRUE(feed(SAMPLE_DT, QUIET_PIPE, QUIET_AMB, true, false).confidence < 50U);
}

/* §5 behaviour 6 — bounded memory, fixed-size window, no growing list. */
static void test_bounded_memory(void)
{
	unsigned int i;
	struct leak_decision d;

	MT_CASE("§5.6 state is a fixed-size struct with no heap");
	MT_TRUE(sizeof(struct leak_state) <= 256U);

	/* Ten thousand samples through the same state object. The only thing a
	 * host test can actually assert about allocation is that nothing crashes
	 * and the state does not change size, but it does prove the window is a
	 * ring rather than an append. */
	MT_CASE("§5.6 ten thousand samples do not grow the state");
	reset();
	for (i = 1; i <= 10000U; i++) {
		d = feed(i * SAMPLE_DT, QUIET_PIPE + (int32_t)(i % 7U), QUIET_AMB, true, false);
	}
	MT_TRUE(d.reason != NULL);
	MT_TRUE(sizeof(st.delta_ring) == (LEAK_WINDOW_LEN * sizeof(int32_t)));
	MT_TRUE(st.ring_count == LEAK_WINDOW_LEN);

	MT_CASE("NULL arguments do not crash");
	MT_TRUE(leak_update(NULL, &cfg, NULL).verdict == LEAK_VERDICT_QUIET);
	leak_new_state(NULL, NULL);
	leak_cfg_defaults(NULL);
}

/* A reboot resets the uptime clock. The detector must treat a backwards
 * timestamp as a discontinuity, not as a fault an operator has to action —
 * there is no RTC in the BOM, so this will happen in the field. */
static void test_clock_discontinuity(void)
{
	struct leak_decision d;

	MT_CASE("backwards timestamp is a discontinuity, not a sensor_fault");
	reset();
	(void)prime(QUIET_PIPE, QUIET_AMB);
	d = feed(10U, QUIET_PIPE, QUIET_AMB, true, false);
	MT_TRUE(d.verdict != LEAK_VERDICT_SENSOR_FAULT);
}

int main(void)
{
	test_verdict_names_match_contract();
	test_install_unverified();
	test_sensor_fault();
	test_stuck_channel();
	test_noisy_channel();
	test_freeze_risk();
	test_slow_leak();
	test_nuisance_rejection();
	test_baseline_adapts_per_device();
	test_fast_leak_confidence();
	test_bounded_memory();
	test_clock_discontinuity();
	return mt_report("test_leak_detect");
}
