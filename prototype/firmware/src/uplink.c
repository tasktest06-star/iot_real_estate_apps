/*
 * uplink.c — byte-exact implementation of contracts/INTERFACES.md §1 and §2.
 *
 * Deliberately free of Zephyr includes; see the header for why.
 */

#include "uplink.h"

#define INT16_CC_MAX  32767
#define INT16_CC_MIN  (-32768)
#define UINT16_MV_MAX 65535

static int32_t clamp_i32(int32_t v, int32_t lo, int32_t hi)
{
	if (v < lo) {
		return lo;
	}
	if (v > hi) {
		return hi;
	}
	return v;
}

static void put_be16(uint8_t *p, uint16_t v)
{
	p[0] = (uint8_t)(v >> 8);
	p[1] = (uint8_t)(v & 0xFFU);
}

static uint16_t get_be16(const uint8_t *p)
{
	return (uint16_t)(((uint16_t)p[0] << 8) | (uint16_t)p[1]);
}

int32_t uplink_delta_cc(const struct uplink_sample *s)
{
	if (s == NULL) {
		return 0;
	}
	/* §1: delta_c = pipe_c - ambient_c. Computed on the *unclamped* values so
	 * the local detector sees the real magnitude even when the wire field
	 * would saturate. */
	return s->pipe_cc - s->ambient_cc;
}

int uplink_pack(const struct uplink_sample *s, uint8_t *out, size_t out_len)
{
	uint8_t flags = 0U;

	if ((s == NULL) || (out == NULL)) {
		return UPLINK_ERR_ARG;
	}
	if (out_len < UPLINK_PAYLOAD_LEN) {
		return UPLINK_ERR_LEN;
	}

	/* Offsets 0..1 and 2..3: int16 centi-degrees C, big-endian.
	 * The cast to uint16_t of a clamped int32 is the standard two's-complement
	 * reinterpretation; the Node codec sign-extends with `<<16>>16` and the
	 * Python backend uses struct '>h'. All three agree. */
	put_be16(&out[0], (uint16_t)(int16_t)clamp_i32(s->pipe_cc, INT16_CC_MIN, INT16_CC_MAX));
	put_be16(&out[2], (uint16_t)(int16_t)clamp_i32(s->ambient_cc, INT16_CC_MIN, INT16_CC_MAX));

	/* Offset 4..5: uint16 millivolts. Negative is impossible from a divider but
	 * clamp anyway — a sign-flipped ADC calibration must not read as 64 V. */
	put_be16(&out[4], (uint16_t)clamp_i32(s->battery_mv, 0, UINT16_MV_MAX));

	/* Offset 6: flags. Reserved bits 3..7 are left at zero, which is a contract
	 * requirement and not merely tidiness: the backend is entitled to use a
	 * nonzero reserved nibble as a protocol-version tripwire. */
	if (s->install_verified) {
		flags |= UPLINK_FLAG_INSTALL_VERIFIED;
	}
	if (s->tamper) {
		flags |= UPLINK_FLAG_TAMPER;
	}
	if (s->cold_warning) {
		flags |= UPLINK_FLAG_COLD_WARNING;
	}
	out[6] = (uint8_t)(flags & UPLINK_FLAG_DEFINED_MASK);

	/* Offset 7: uplink_reason. */
	out[7] = (uint8_t)s->reason;

	return (int)UPLINK_PAYLOAD_LEN;
}

int downlink_parse(const uint8_t *buf, size_t len, struct downlink_msg *out)
{
	uint8_t cmd;

	if ((buf == NULL) || (out == NULL)) {
		return UPLINK_ERR_ARG;
	}

	out->command = DL_CMD_NOP;
	out->arg = 0U;
	out->token = 0U;

	if (len != DOWNLINK_PAYLOAD_LEN) {
		/* A short or long frame on FPort 2 is not ours. Do not guess: a
		 * mis-length frame that we "fix up" could be a valve_close. */
		return UPLINK_ERR_LEN;
	}

	cmd = buf[0];
	out->arg = buf[1];
	out->token = get_be16(&buf[2]);

	switch (cmd) {
	case DL_CMD_NOP:
	case DL_CMD_VALVE_CLOSE:
	case DL_CMD_VALVE_OPEN:
	case DL_CMD_REQUEST_INSTALL_PROBE:
		/* §2: "Else 0" for arg. We accept a nonzero arg on these commands
		 * rather than rejecting the command, because rejecting a valve_open
		 * over a cosmetic field is the wrong failure direction. */
		out->command = (enum downlink_command)cmd;
		return UPLINK_OK;

	case DL_CMD_SET_HEARTBEAT:
		out->command = DL_CMD_SET_HEARTBEAT;
		if ((out->arg < DL_HEARTBEAT_MIN_MINUTES) ||
		    (out->arg > DL_HEARTBEAT_MAX_MINUTES)) {
			/* Out of §2's 1..240. Refuse rather than clamp: a 0-minute
			 * heartbeat from a corrupted frame would transmit continuously
			 * and flatten a Li-SOCl2 cell in days, and 240 is already the
			 * contract's ceiling. Note 240 is also uint8's practical
			 * ceiling, so only arg == 0 and 241..255 land here. */
			return UPLINK_ERR_RANGE;
		}
		return UPLINK_OK;

	default:
		/* Reserved/unknown command. Token is preserved (see header). */
		return UPLINK_ERR_CMD;
	}
}

/*
 * CONTRACT PROBLEM, reported rather than silently worked around
 * -------------------------------------------------------------
 * §2 says: "token — uint16 echo token; the device mirrors it in the next
 * uplink's uplink_reason=3 frame".
 *
 * The §1 uplink has no field capable of carrying a uint16 token. All eight
 * bytes are allocated (2+2+2+1+1) and bits 3..7 of the flags byte are five
 * reserved bits — not sixteen. So the stated echo is not implementable in
 * payload v1 without changing §1, which the contract forbids doing silently.
 *
 * What this firmware does instead, and why it is the closest honest reading:
 *   - the token is stored in device state (see app_state in main.c);
 *   - the response is identified by uplink_reason == 3, sent as the next frame
 *     after the command is processed;
 *   - correlation is therefore by ordering on (dev_eui, f_cnt), which is the
 *     same mechanism §1 already mandates for dedup and loss accounting and
 *     which the backend already indexes (§9, UNIQUE(dev_eui, f_cnt)).
 * A reason=3 frame is unambiguous as long as at most one command is outstanding
 * per device, which the backend's valve_commands table already enforces by
 * persisting every command with an outcome (§8).
 *
 * Fixing this properly needs one of: a 10-byte payload on a new FPort, a
 * separate FPort 3 ack frame, or shrinking the token to 5 bits in the reserved
 * flag nibble. All three are §1 changes. Flagged in the build report.
 */
