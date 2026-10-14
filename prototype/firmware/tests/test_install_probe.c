/*
 * test_install_probe.c — install verification.
 *
 * The bit this code sets is §1 byte 6 bit 0, and it is the most consequential
 * bit in the payload: contracts/INTERFACES.md §5 behaviour 2 says an
 * unverified install can never produce a leak verdict, and 08-roadmap.md §8
 * risk 4 says not to count unverified installs at all. So a probe that is too
 * generous silently admits garbage into the evaluation corpus, and a probe that
 * is too strict silently removes working devices from the fleet. Both failures
 * are invisible in the data, which is why they get tests.
 *
 * NOT EXECUTED. No compiler on this machine. See tests/README.md.
 */

#include <stdio.h>

#include "../src/install_probe.h"
#include "minitest.h"

static struct install_probe_cfg cfg;
static struct install_probe_state acc;

static void reset(void)
{
	install_probe_cfg_defaults(&cfg);
	install_probe_begin(&acc);
}

/*
 * A good install: the installer runs the nearest fixture, the pipe follows the
 * water and the room does not move. Pipe ramps 21.00 -> 19.00 C over 40
 * samples; ambient sits at 20.00 C.
 */
static void feed_good_install(unsigned int samples)
{
	unsigned int i;

	for (i = 0; i < samples; i++) {
		int32_t pipe = 2100 - (int32_t)(i * 5U);

		install_probe_feed(&acc, &cfg, pipe, 2000);
	}
}

static void test_verified(void)
{
	MT_CASE("a seated clamp with a pipe-only thermal response verifies");
	reset();
	feed_good_install(40U);
	MT_EQI(install_probe_finish(&acc, &cfg), INSTALL_VERIFIED);
	MT_TRUE(install_result_is_verified(install_probe_finish(&acc, &cfg)));
	MT_EQS(install_result_name(INSTALL_VERIFIED), "verified");
}

static void test_no_thermal_response(void)
{
	unsigned int i;

	/*
	 * The commonest real failure: the clamp is on the pipe's insulation, or
	 * on the wrong pipe, or the installer never opened the tap. The pipe
	 * channel reads a plausible, stable temperature and nothing happens.
	 * Indistinguishable from a healthy quiet device unless it is checked here.
	 */
	MT_CASE("a pipe that does not respond to the fixture fails");
	reset();
	for (i = 0; i < 40U; i++) {
		install_probe_feed(&acc, &cfg, 2100, 2000);
	}
	MT_EQI(install_probe_finish(&acc, &cfg), INSTALL_FAIL_NO_THERMAL_RESPONSE);
	MT_TRUE(!install_result_is_verified(INSTALL_FAIL_NO_THERMAL_RESPONSE));

	MT_CASE("a response just under the threshold still fails");
	reset();
	for (i = 0; i < 40U; i++) {
		install_probe_feed(&acc, &cfg, 2100 - (int32_t)((i * 149U) / 39U), 2000);
	}
	MT_EQI(install_probe_finish(&acc, &cfg), INSTALL_FAIL_NO_THERMAL_RESPONSE);
}

static void test_ambient_moved(void)
{
	unsigned int i;

	/*
	 * Somebody opened the basement door, or the furnace cycled. The pipe moved
	 * and so did the room, so the pipe movement proves nothing about the
	 * clamp. Checked BEFORE the pipe-response test on purpose: a room-wide
	 * thermal event can satisfy the pipe test for entirely the wrong reason,
	 * and accepting it would certify a clamp dangling in free air.
	 */
	MT_CASE("a room-wide thermal event fails rather than verifying");
	reset();
	for (i = 0; i < 40U; i++) {
		install_probe_feed(&acc, &cfg, 2100 - (int32_t)(i * 5U),
				   2000 + (int32_t)(i * 5U));
	}
	MT_EQI(install_probe_finish(&acc, &cfg), INSTALL_FAIL_AMBIENT_MOVED);
}

static void test_sensors_identical(void)
{
	unsigned int i;

	/*
	 * Both sensors reading the same value to the LSB means they are in the
	 * same thermal place: the clamp is dangling next to the ambient sensor, or
	 * the installer fitted both to one spot. Reported first because the
	 * instruction it generates is unambiguous.
	 */
	MT_CASE("two channels that never separate fail");
	reset();
	for (i = 0; i < 40U; i++) {
		int32_t both = 2100 - (int32_t)(i * 5U);

		install_probe_feed(&acc, &cfg, both, both);
	}
	MT_EQI(install_probe_finish(&acc, &cfg), INSTALL_FAIL_SENSORS_IDENTICAL);

	MT_CASE("PRECEDENCE: identical channels beat ambient_moved");
	/* Both conditions hold in the run above — the channels are identical AND
	 * the 'ambient' swing is 200 cc. The identical verdict is the one an
	 * installer can act on. */
	MT_TRUE(acc.amb_max - acc.amb_min > cfg.max_ambient_response_cc);
}

static void test_incomplete(void)
{
	MT_CASE("too few samples is incomplete, never verified");
	reset();
	feed_good_install(5U);
	MT_EQI(install_probe_finish(&acc, &cfg), INSTALL_FAIL_INCOMPLETE);

	MT_CASE("exactly min_samples is enough");
	reset();
	/* Needs the full 150 cc swing inside min_samples, so step faster. */
	{
		unsigned int i;

		for (i = 0; i < cfg.min_samples; i++) {
			install_probe_feed(&acc, &cfg, 2100 - (int32_t)(i * 10U), 2000);
		}
	}
	MT_EQI(install_probe_finish(&acc, &cfg), INSTALL_VERIFIED);

	MT_CASE("an empty probe is incomplete, not verified");
	reset();
	MT_EQI(install_probe_finish(&acc, &cfg), INSTALL_FAIL_INCOMPLETE);
}

static void test_implausible_is_sticky(void)
{
	MT_CASE("one implausible reading fails the whole probe");
	reset();
	feed_good_install(40U);
	install_probe_feed(&acc, &cfg, 9000, 2000);
	MT_EQI(install_probe_finish(&acc, &cfg), INSTALL_FAIL_IMPLAUSIBLE);

	/*
	 * Sticky on purpose. Better a second attempt by an installer who is
	 * standing right there with the enclosure open than a device that reports
	 * for five years and is never believed.
	 */
	MT_CASE("and a good reading afterwards does not rescue it");
	install_probe_feed(&acc, &cfg, 2000, 2000);
	MT_EQI(install_probe_finish(&acc, &cfg), INSTALL_FAIL_IMPLAUSIBLE);

	MT_CASE("the implausible sample is excluded from the min/max window");
	MT_TRUE(acc.pipe_max <= 2100);
}

static void test_record(void)
{
	struct install_record rec;

	MT_CASE("a fresh record is unverified");
	install_record_init(&rec);
	MT_EQI(rec.thermal, INSTALL_UNKNOWN);
	MT_TRUE(!install_record_sensor_verified(&rec));
	MT_TRUE(!rec.valve_present);
	MT_TRUE(!rec.valve.completed);

	MT_CASE("§1 install_verified reflects the thermal probe only");
	rec.thermal = INSTALL_VERIFIED;
	MT_TRUE(install_record_sensor_verified(&rec));

	/*
	 * A refused valve must NOT un-verify the sensor. The sensor and the shutoff
	 * are two separate promises, and conflating them would silently stop
	 * counting a perfectly good sensor every time a valve turned out to be
	 * seized — which, per 08-roadmap.md §8 risk 9, is a thing that happens.
	 */
	MT_CASE("a refused valve does not un-verify the sensor");
	rec.valve_present = true;
	rec.valve.completed = true;
	rec.valve.breakaway_torque_mnm = 999999U;
	MT_TRUE(install_record_sensor_verified(&rec));

	MT_CASE("NULL safety");
	MT_TRUE(!install_record_sensor_verified(NULL));
	install_record_init(NULL);
	install_probe_cfg_defaults(NULL);
	install_probe_begin(NULL);
	install_probe_feed(NULL, &cfg, 0, 0);
	MT_EQI(install_probe_finish(NULL, &cfg), INSTALL_UNKNOWN);
}

static void test_cfg_null_uses_defaults(void)
{
	MT_CASE("NULL cfg falls back to the documented defaults");
	install_probe_begin(&acc);
	{
		unsigned int i;

		for (i = 0; i < 40U; i++) {
			install_probe_feed(&acc, NULL, 2100 - (int32_t)(i * 5U), 2000);
		}
	}
	MT_EQI(install_probe_finish(&acc, NULL), INSTALL_VERIFIED);
}

static void test_result_names(void)
{
	MT_CASE("every result has a name an installer can be shown");
	MT_EQS(install_result_name(INSTALL_UNKNOWN), "unknown");
	MT_EQS(install_result_name(INSTALL_VERIFIED), "verified");
	MT_EQS(install_result_name(INSTALL_FAIL_INCOMPLETE), "incomplete");
	MT_EQS(install_result_name(INSTALL_FAIL_SENSORS_IDENTICAL), "sensors_identical");
	MT_EQS(install_result_name(INSTALL_FAIL_NO_THERMAL_RESPONSE), "no_thermal_response");
	MT_EQS(install_result_name(INSTALL_FAIL_AMBIENT_MOVED), "ambient_moved");
	MT_EQS(install_result_name(INSTALL_FAIL_IMPLAUSIBLE), "implausible_reading");
	MT_TRUE(install_result_name((enum install_result)77) != NULL);
}

int main(void)
{
	reset();
	test_verified();
	test_no_thermal_response();
	test_ambient_moved();
	test_sensors_identical();
	test_incomplete();
	test_implausible_is_sticky();
	test_record();
	test_cfg_null_uses_defaults();
	test_result_names();
	return mt_report("test_install_probe");
}
