/*
 * test_uplink.c — contract conformance for the 8-byte uplink (§1) and the
 * 4-byte downlink (§2).
 *
 * THESE ARE THE CROSS-COMPONENT GOLDEN VECTORS. The codec (Node) and the
 * backend (Python) are being written against the same table in
 * contracts/INTERFACES.md. The first two vectors below are lifted directly out
 * of the contract itself rather than invented here, which is what makes them
 * useful as a shared conformance fixture:
 *
 *   - GOLDEN_SEC4 is the base64 `frm_payload` in §4's webhook example,
 *     "BLEAAQ0yAQA=", decoded to its eight raw bytes.
 *   - GOLDEN_SEC3 is §3's decoded-JSON example re-encoded to bytes.
 *
 * If a Node or Python implementation disagrees with either, one of the three is
 * wrong and the contract is the tie-breaker.
 *
 * NOT EXECUTED. No compiler on this machine. See tests/README.md.
 */

#include <stdio.h>

#include "../src/uplink.h"
#include "minitest.h"

/*
 * §4's example payload: base64 "BLEAAQ0yAQA=" -> 04 B1 00 01 0D 32 01 00.
 *   pipe_c    = 0x04B1 =  1201 ->  12.01 C
 *   ambient_c = 0x0001 =     1 ->   0.01 C
 *   battery   = 0x0D32 =  3378 mV
 *   flags     = 0x01   -> install_verified
 *   reason    = 0x00   -> heartbeat
 *
 * OBSERVATION ON THE CONTRACT, not a defect: this payload is not the same
 * sample as §3's decoded-JSON example, and a reader can easily assume it is.
 * Its delta is +12.00 C with the room at 0.01 C, which no plumbing produces;
 * the bytes were clearly hand-built to exercise the layout rather than to
 * describe a scenario. Both facts are worth saying out loud because the
 * simulator and the backend are being written against the same two examples.
 */
static const unsigned char GOLDEN_SEC4[UPLINK_PAYLOAD_LEN] = {
	0x04, 0xB1, 0x00, 0x01, 0x0D, 0x32, 0x01, 0x00,
};

/*
 * §3's decoded payload: pipe 11.25, ambient 18.40, battery 3410,
 * install_verified true, heartbeat. delta_c = -7.15, which is a plausible
 * draw-off signal. -> 04 65 07 30 0D 52 01 00
 */
static const unsigned char GOLDEN_SEC3[UPLINK_PAYLOAD_LEN] = {
	0x04, 0x65, 0x07, 0x30, 0x0D, 0x52, 0x01, 0x00,
};

static void test_golden_vectors(void)
{
	struct uplink_sample s;
	unsigned char out[UPLINK_PAYLOAD_LEN];

	MT_CASE("§1 golden vector from §4's base64 frm_payload");
	s.pipe_cc = 1201;
	s.ambient_cc = 1;
	s.battery_mv = 3378;
	s.install_verified = true;
	s.tamper = false;
	s.cold_warning = false;
	s.reason = UPLINK_REASON_HEARTBEAT;
	MT_EQI(uplink_pack(&s, out, sizeof(out)), UPLINK_PAYLOAD_LEN);
	MT_EQBYTES(out, GOLDEN_SEC4, UPLINK_PAYLOAD_LEN);

	MT_CASE("§1 golden vector from §3's decoded JSON example");
	s.pipe_cc = 1125;
	s.ambient_cc = 1840;
	s.battery_mv = 3410;
	s.install_verified = true;
	s.tamper = false;
	s.cold_warning = false;
	s.reason = UPLINK_REASON_HEARTBEAT;
	MT_EQI(uplink_pack(&s, out, sizeof(out)), UPLINK_PAYLOAD_LEN);
	MT_EQBYTES(out, GOLDEN_SEC3, UPLINK_PAYLOAD_LEN);

	MT_CASE("§1 delta_c is derived, never transmitted");
	MT_EQI(uplink_delta_cc(&s), -715);
}

static void test_endianness_and_sign(void)
{
	struct uplink_sample s;
	unsigned char out[UPLINK_PAYLOAD_LEN];

	s.battery_mv = 3600;
	s.install_verified = false;
	s.tamper = false;
	s.cold_warning = false;
	s.reason = UPLINK_REASON_HEARTBEAT;

	MT_CASE("§1 big-endian byte order, high byte first");
	s.pipe_cc = 0x1234;
	s.ambient_cc = 0x5678;
	MT_EQI(uplink_pack(&s, out, sizeof(out)), UPLINK_PAYLOAD_LEN);
	MT_EQI(out[0], 0x12);
	MT_EQI(out[1], 0x34);
	MT_EQI(out[2], 0x56);
	MT_EQI(out[3], 0x78);

	MT_CASE("§1 negative temperatures are two's complement int16");
	/* -5.50 C = -550 cc = 0xFDDA. The freeze product spends half its life
	 * below zero, so this is the half of the range that matters. */
	s.pipe_cc = -550;
	s.ambient_cc = -1;
	MT_EQI(uplink_pack(&s, out, sizeof(out)), UPLINK_PAYLOAD_LEN);
	MT_EQI(out[0], 0xFD);
	MT_EQI(out[1], 0xDA);
	MT_EQI(out[2], 0xFF);
	MT_EQI(out[3], 0xFF);

	MT_CASE("§1 int16 extremes encode exactly");
	s.pipe_cc = 32767;
	s.ambient_cc = -32768;
	MT_EQI(uplink_pack(&s, out, sizeof(out)), UPLINK_PAYLOAD_LEN);
	MT_EQI(out[0], 0x7F);
	MT_EQI(out[1], 0xFF);
	MT_EQI(out[2], 0x80);
	MT_EQI(out[3], 0x00);
}

static void test_saturation(void)
{
	struct uplink_sample s;
	unsigned char out[UPLINK_PAYLOAD_LEN];

	s.install_verified = false;
	s.tamper = false;
	s.cold_warning = false;
	s.reason = UPLINK_REASON_HEARTBEAT;
	s.battery_mv = 3600;

	/*
	 * The behaviour this pins down is the reason uplink_pack takes int32 and
	 * clamps rather than taking int16 and wrapping. A faulted I2C read can
	 * produce a wild value; wrapping it turns a hot-side fault into a
	 * plausible COLD reading, which is the exact direction that fabricates a
	 * leak. Saturation is detectable downstream. Wrapping is not.
	 */
	MT_CASE("out-of-range temperature saturates, never wraps");
	s.pipe_cc = 999999;
	s.ambient_cc = -999999;
	MT_EQI(uplink_pack(&s, out, sizeof(out)), UPLINK_PAYLOAD_LEN);
	MT_EQI(out[0], 0x7F);
	MT_EQI(out[1], 0xFF);
	MT_EQI(out[2], 0x80);
	MT_EQI(out[3], 0x00);

	MT_CASE("battery clamps into uint16 and never goes negative");
	s.pipe_cc = 0;
	s.ambient_cc = 0;
	s.battery_mv = 70000;
	MT_EQI(uplink_pack(&s, out, sizeof(out)), UPLINK_PAYLOAD_LEN);
	MT_EQI(out[4], 0xFF);
	MT_EQI(out[5], 0xFF);

	s.battery_mv = -5;
	MT_EQI(uplink_pack(&s, out, sizeof(out)), UPLINK_PAYLOAD_LEN);
	MT_EQI(out[4], 0x00);
	MT_EQI(out[5], 0x00);
}

static void test_flags_and_reason(void)
{
	struct uplink_sample s;
	unsigned char out[UPLINK_PAYLOAD_LEN];
	int r;

	s.pipe_cc = 0;
	s.ambient_cc = 0;
	s.battery_mv = 3600;
	s.reason = UPLINK_REASON_HEARTBEAT;

	MT_CASE("§1 byte 6 bit assignments");
	s.install_verified = true;
	s.tamper = false;
	s.cold_warning = false;
	(void)uplink_pack(&s, out, sizeof(out));
	MT_EQI(out[6], 0x01);

	s.install_verified = false;
	s.tamper = true;
	(void)uplink_pack(&s, out, sizeof(out));
	MT_EQI(out[6], 0x02);

	s.tamper = false;
	s.cold_warning = true;
	(void)uplink_pack(&s, out, sizeof(out));
	MT_EQI(out[6], 0x04);

	s.install_verified = true;
	s.tamper = true;
	s.cold_warning = true;
	(void)uplink_pack(&s, out, sizeof(out));
	MT_EQI(out[6], 0x07);

	/* §1: bits 3-7 reserved (0). Not cosmetic — the backend is entitled to
	 * treat a nonzero reserved nibble as a protocol-version tripwire. */
	MT_CASE("§1 reserved flag bits 3-7 are always zero");
	MT_EQI(out[6] & 0xF8, 0x00);

	MT_CASE("§1 byte 7 uplink_reason values");
	s.install_verified = false;
	s.tamper = false;
	s.cold_warning = false;
	for (r = 0; r <= 3; r++) {
		s.reason = (enum uplink_reason)r;
		(void)uplink_pack(&s, out, sizeof(out));
		MT_EQI(out[7], r);
	}
}

static void test_pack_refusals(void)
{
	struct uplink_sample s;
	unsigned char out[UPLINK_PAYLOAD_LEN];

	s.pipe_cc = 0;
	s.ambient_cc = 0;
	s.battery_mv = 3600;
	s.install_verified = false;
	s.tamper = false;
	s.cold_warning = false;
	s.reason = UPLINK_REASON_BOOT;

	MT_CASE("pack refuses rather than overruns a short buffer");
	MT_EQI(uplink_pack(&s, out, UPLINK_PAYLOAD_LEN - 1), UPLINK_ERR_LEN);

	MT_CASE("pack refuses NULL arguments");
	MT_EQI(uplink_pack(NULL, out, sizeof(out)), UPLINK_ERR_ARG);
	MT_EQI(uplink_pack(&s, NULL, sizeof(out)), UPLINK_ERR_ARG);
}

static void test_downlink(void)
{
	struct downlink_msg m;
	unsigned char buf[DOWNLINK_PAYLOAD_LEN];

	MT_CASE("§2 valve_close with big-endian token");
	buf[0] = 1;
	buf[1] = 0;
	buf[2] = 0xBE;
	buf[3] = 0xEF;
	MT_EQI(downlink_parse(buf, sizeof(buf), &m), UPLINK_OK);
	MT_EQI(m.command, DL_CMD_VALVE_CLOSE);
	MT_EQI(m.arg, 0);
	MT_EQI(m.token, 0xBEEF);

	MT_CASE("§2 nop, valve_open, request_install_probe");
	buf[0] = 0;
	MT_EQI(downlink_parse(buf, sizeof(buf), &m), UPLINK_OK);
	MT_EQI(m.command, DL_CMD_NOP);
	buf[0] = 2;
	MT_EQI(downlink_parse(buf, sizeof(buf), &m), UPLINK_OK);
	MT_EQI(m.command, DL_CMD_VALVE_OPEN);
	buf[0] = 4;
	MT_EQI(downlink_parse(buf, sizeof(buf), &m), UPLINK_OK);
	MT_EQI(m.command, DL_CMD_REQUEST_INSTALL_PROBE);

	MT_CASE("§2 set_heartbeat accepts 1..240");
	buf[0] = 3;
	buf[1] = 15;
	buf[2] = 0x00;
	buf[3] = 0x2A;
	MT_EQI(downlink_parse(buf, sizeof(buf), &m), UPLINK_OK);
	MT_EQI(m.command, DL_CMD_SET_HEARTBEAT);
	MT_EQI(m.arg, 15);
	MT_EQI(m.token, 0x002A);

	buf[1] = 1;
	MT_EQI(downlink_parse(buf, sizeof(buf), &m), UPLINK_OK);
	buf[1] = 240;
	MT_EQI(downlink_parse(buf, sizeof(buf), &m), UPLINK_OK);

	/* A 0-minute heartbeat from a corrupted frame would transmit continuously
	 * and flatten a primary cell in days. Refuse, do not clamp. */
	MT_CASE("§2 set_heartbeat refuses out-of-range minutes");
	buf[1] = 0;
	MT_EQI(downlink_parse(buf, sizeof(buf), &m), UPLINK_ERR_RANGE);
	MT_EQI(m.token, 0x002A); /* token still reported, for the audit trail */
	buf[1] = 241;
	MT_EQI(downlink_parse(buf, sizeof(buf), &m), UPLINK_ERR_RANGE);

	MT_CASE("unknown command is reported, not fatal, and keeps its token");
	buf[0] = 99;
	buf[1] = 0;
	buf[2] = 0x12;
	buf[3] = 0x34;
	MT_EQI(downlink_parse(buf, sizeof(buf), &m), UPLINK_ERR_CMD);
	MT_EQI(m.command, DL_CMD_NOP);
	MT_EQI(m.token, 0x1234);

	MT_CASE("wrong length is refused, never guessed at");
	buf[0] = 1;
	MT_EQI(downlink_parse(buf, 3, &m), UPLINK_ERR_LEN);
	MT_EQI(downlink_parse(buf, 5, &m), UPLINK_ERR_LEN);
	MT_EQI(downlink_parse(buf, 0, &m), UPLINK_ERR_LEN);
	/* And the command must not leak through from a rejected frame. */
	MT_EQI(m.command, DL_CMD_NOP);

	MT_CASE("downlink refuses NULL arguments");
	MT_EQI(downlink_parse(NULL, sizeof(buf), &m), UPLINK_ERR_ARG);
	MT_EQI(downlink_parse(buf, sizeof(buf), NULL), UPLINK_ERR_ARG);
}

int main(void)
{
	test_golden_vectors();
	test_endianness_and_sign();
	test_saturation();
	test_flags_and_reason();
	test_pack_refusals();
	test_downlink();
	return mt_report("test_uplink");
}
