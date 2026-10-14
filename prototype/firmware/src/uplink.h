/*
 * uplink.h — LoRaWAN payload codec for the clamp-on thermal leak sensor.
 *
 * WHAT THIS IS: the byte-exact encoder for the 8-byte FPort 1 uplink defined in
 * contracts/INTERFACES.md §1, and the decoder for the 4-byte FPort 2 downlink
 * defined in §2. Plus the pure uplink-scheduling policy (§1's "uplink_reason").
 *
 * WHAT THIS DELIBERATELY IS NOT: it does not touch the radio. There is no
 * #include <zephyr/...> anywhere in uplink.c, on purpose — a Node codec
 * (codec/) and a Python backend (backend/) are being written against the same
 * table in contracts/INTERFACES.md §1, and a one-byte disagreement silently
 * corrupts every reading in the fleet. Keeping this file free of RTOS
 * dependencies is what lets tests/test_uplink.c compile and run on a host with
 * nothing but cc, so the three implementations can be diffed against the same
 * golden vectors.
 *
 * Endianness is big-endian throughout, per §1. Do not "optimise" this with a
 * memcpy of a struct.
 */

#ifndef APP_UPLINK_H_
#define APP_UPLINK_H_

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* contracts/INTERFACES.md §1 / §2 */
#define UPLINK_PAYLOAD_LEN   8U
#define UPLINK_FPORT         1U
#define DOWNLINK_PAYLOAD_LEN 4U
#define DOWNLINK_FPORT       2U

/*
 * §1 byte 7. Values are wire constants: never renumber them.
 * The codec maps these to strings (§3); the firmware only ever sends integers.
 */
enum uplink_reason {
	UPLINK_REASON_HEARTBEAT = 0,
	UPLINK_REASON_THRESHOLD = 1,
	UPLINK_REASON_BOOT = 2,
	UPLINK_REASON_INSTALL_PROBE = 3,
};

/* §1 byte 6. bits 3..7 are reserved and MUST be transmitted as zero. */
#define UPLINK_FLAG_INSTALL_VERIFIED 0x01U
#define UPLINK_FLAG_TAMPER           0x02U
#define UPLINK_FLAG_COLD_WARNING     0x04U
#define UPLINK_FLAG_DEFINED_MASK     0x07U

/*
 * Temperatures are carried as centi-degrees C in an int16 (§1), i.e. the
 * representable range is -327.68 .. +327.67 C. We accept int32 here and clamp
 * on the way out rather than taking an int16 argument, because a faulted I2C
 * read can produce a value far outside the TMP117's -55..+150 C range and
 * wrapping it would turn a hot-side fault into a plausible *cold* reading —
 * which is precisely the direction that fabricates a leak. Saturating is
 * detectable downstream; wrapping is not.
 */
struct uplink_sample {
	int32_t pipe_cc;      /* centi-degrees C */
	int32_t ambient_cc;   /* centi-degrees C */
	int32_t battery_mv;   /* millivolts; clamped to uint16 */
	bool install_verified;
	bool tamper;
	bool cold_warning;
	enum uplink_reason reason;
};

/* Return codes. Negative = refused; no errno dependency so this builds on a
 * bare host compiler as well as under Zephyr. */
#define UPLINK_OK           0
#define UPLINK_ERR_ARG    (-1)  /* NULL pointer */
#define UPLINK_ERR_LEN    (-2)  /* wrong payload length */
#define UPLINK_ERR_CMD    (-3)  /* unknown command byte */
#define UPLINK_ERR_RANGE  (-4)  /* in-range command, out-of-range argument */

/*
 * Packs *s into exactly UPLINK_PAYLOAD_LEN bytes, big-endian, per §1.
 * Returns UPLINK_PAYLOAD_LEN on success (so the caller can hand the result
 * straight to lorawan_send()'s length argument), or a negative UPLINK_ERR_*.
 *
 * Note there is no sequence number in the payload — §1 is explicit that the
 * network server's f_cnt serves that purpose and duplicating it on air wastes
 * one of eight scarce bytes.
 */
int uplink_pack(const struct uplink_sample *s, uint8_t *out, size_t out_len);

/* Derived signal, never transmitted (§1). Exposed because the detector and the
 * operator UI both want it and both must compute it identically. */
int32_t uplink_delta_cc(const struct uplink_sample *s);

/* §2 commands. Wire constants. */
enum downlink_command {
	DL_CMD_NOP = 0,
	DL_CMD_VALVE_CLOSE = 1,
	DL_CMD_VALVE_OPEN = 2,
	DL_CMD_SET_HEARTBEAT = 3,
	DL_CMD_REQUEST_INSTALL_PROBE = 4,
};

#define DL_HEARTBEAT_MIN_MINUTES 1U
#define DL_HEARTBEAT_MAX_MINUTES 240U

struct downlink_msg {
	enum downlink_command command;
	uint8_t arg;
	uint16_t token;
};

/*
 * Parses a 4-byte FPort 2 downlink per §2.
 *
 * Returns UPLINK_OK, or UPLINK_ERR_LEN / UPLINK_ERR_CMD / UPLINK_ERR_RANGE.
 * On UPLINK_ERR_CMD and UPLINK_ERR_RANGE the token IS still written to *out,
 * because the token is how an operator correlates "the device heard me but
 * refused" with the command row in the backend's valve_commands table
 * (§8: "a command with no confirming uplink becomes unconfirmed, never
 * silently success"). Silently dropping an unparseable downlink is how a fleet
 * ends up with commands that are neither applied nor accounted for.
 *
 * This mirrors §3's rule for the decoder — unknown values must not throw — in
 * the uplink direction: an unknown command byte is reported, not fatal.
 */
int downlink_parse(const uint8_t *buf, size_t len, struct downlink_msg *out);

#endif /* APP_UPLINK_H_ */
