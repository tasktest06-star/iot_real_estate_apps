"""Server-side decode of the 8-byte uplink payload (contracts/INTERFACES.md §1).

This is the backend's *own* implementation of the byte layout. It exists because
a webhook is untrusted input: contract §4 says the backend decodes `frm_payload`
itself and "must not trust a `decoded_payload` supplied by the caller". The Node
codec in `codec/` and the C encoder in `firmware/` are written independently
against the same table; agreement between them is a test, not an assumption.

What this module deliberately does NOT do:
  * no detection, no thresholds, no alerting -- it turns bytes into numbers and
    nothing else (the classifier lives in detection/, contract §5);
  * no exception on an unknown `uplink_reason`: contract §3 requires
    "reserved_<n>", because a decoder that throws takes the whole webhook down;
  * no tolerance of a wrong payload length -- that is a device/encoder bug and
    is surfaced as a decode error, not guessed at.
"""

import base64
import binascii
import struct
from typing import Any, Dict

# contract §1, FPort 1. Fixed at 8 bytes so a single uplink fits DR0 at the edge
# of US915 coverage (05-open-source-stack.md §5.3).
PAYLOAD_LEN = 8
UPLINK_FPORT = 1
DOWNLINK_FPORT = 2

# contract §1 byte 7
UPLINK_REASONS = {
    0: "heartbeat",
    1: "threshold",
    2: "boot",
    3: "install_probe_result",
}

# contract §2 byte 0
DOWNLINK_COMMANDS = {
    "nop": 0,
    "valve_close": 1,
    "valve_open": 2,
    "set_heartbeat": 3,
    "request_install_probe": 4,
}

_FLAG_INSTALL_VERIFIED = 0x01
_FLAG_TAMPER = 0x02
_FLAG_COLD_WARNING = 0x04
_FLAG_RESERVED_MASK = 0xF8  # bits 3-7, contract says reserved and 0


class PayloadError(ValueError):
    """`frm_payload` is not something contract §1 can describe."""


def decode_bytes(raw: bytes) -> Dict[str, Any]:
    """Decode 8 raw payload bytes into the contract §3 JSON shape.

    Big-endian. `pipe_c` and `ambient_c` are **int16** centi-degrees: the sign
    extension is the part that breaks in hand-rolled decoders, so it is done by
    `struct` with the signed format character rather than by shifting.
    """
    if not isinstance(raw, (bytes, bytearray)):
        raise PayloadError("payload must be bytes, got %s" % type(raw).__name__)
    if len(raw) != PAYLOAD_LEN:
        raise PayloadError(
            "expected %d payload bytes (contract §1), got %d" % (PAYLOAD_LEN, len(raw))
        )

    pipe_centi, ambient_centi, battery_mv, flags, reason = struct.unpack(">hhHBB", bytes(raw))

    pipe_c = pipe_centi / 100.0
    ambient_c = ambient_centi / 100.0
    # delta_c is derived, never transmitted (contract §1). Computed in
    # centi-degrees first: 1125/100 - 1840/100 is -7.149999999999999 in binary
    # floating point, while (1125 - 1840)/100 is exactly -7.15. The contract's
    # §3 example states -7.15, and a stored value of -7.149999999999999 shows up
    # in JSON and in the dashboard, so do the subtraction in integers.
    delta_c = (pipe_centi - ambient_centi) / 100.0

    return {
        "pipe_c": pipe_c,
        "ambient_c": ambient_c,
        "delta_c": delta_c,
        "battery_mv": battery_mv,
        "flags": {
            "install_verified": bool(flags & _FLAG_INSTALL_VERIFIED),
            "tamper": bool(flags & _FLAG_TAMPER),
            "cold_warning": bool(flags & _FLAG_COLD_WARNING),
        },
        # Reserved bits are reported, not rejected: a firmware that starts
        # setting bit 3 should show up in the data, not 400 the webhook.
        "reserved_flag_bits": (flags & _FLAG_RESERVED_MASK) >> 3,
        "uplink_reason": UPLINK_REASONS.get(reason, "reserved_%d" % reason),
        "uplink_reason_code": reason,
    }


def decode_frm_payload(frm_payload: str) -> Dict[str, Any]:
    """Base64 -> contract §3 dict. Raises PayloadError on anything malformed."""
    if not isinstance(frm_payload, str):
        raise PayloadError(
            "frm_payload must be a base64 string, got %s" % type(frm_payload).__name__
        )
    try:
        # validate=True so "not base64 at all" fails here rather than silently
        # decoding to a short buffer after non-alphabet characters are dropped.
        raw = base64.b64decode(frm_payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise PayloadError("frm_payload is not valid base64: %s" % exc)
    return decode_bytes(raw)


def encode_bytes(
    pipe_c: float,
    ambient_c: float,
    battery_mv: int,
    install_verified: bool = False,
    tamper: bool = False,
    cold_warning: bool = False,
    uplink_reason: int = 0,
) -> bytes:
    """Inverse of `decode_bytes`, for tests and for the curl examples in README.

    Not used in the request path. It lives here so the round-trip property is
    testable without importing the simulator (which is stdlib-only by contract
    §10 and is written by another component).
    """
    pipe_centi = int(round(pipe_c * 100))
    ambient_centi = int(round(ambient_c * 100))
    if not -32768 <= pipe_centi <= 32767 or not -32768 <= ambient_centi <= 32767:
        raise PayloadError("temperature out of int16 centi-degree range (contract §1)")
    if not 0 <= int(battery_mv) <= 65535:
        raise PayloadError("battery_mv out of uint16 range (contract §1)")
    if not 0 <= int(uplink_reason) <= 255:
        raise PayloadError("uplink_reason must fit one byte (contract §1)")
    flags = 0
    if install_verified:
        flags |= _FLAG_INSTALL_VERIFIED
    if tamper:
        flags |= _FLAG_TAMPER
    if cold_warning:
        flags |= _FLAG_COLD_WARNING
    return struct.pack(
        ">hhHBB", pipe_centi, ambient_centi, int(battery_mv), flags, int(uplink_reason)
    )


def encode_frm_payload(*args: Any, **kwargs: Any) -> str:
    """`encode_bytes` as base64, i.e. a ready-to-POST `frm_payload`."""
    return base64.b64encode(encode_bytes(*args, **kwargs)).decode("ascii")


def encode_downlink(command: str, arg: int = 0, token: int = 0) -> bytes:
    """Contract §2: 4 bytes on FPort 2 -- command, arg, uint16 token."""
    if command not in DOWNLINK_COMMANDS:
        raise PayloadError("unknown downlink command %r (contract §2)" % (command,))
    if command == "set_heartbeat" and not 1 <= int(arg) <= 240:
        raise PayloadError("set_heartbeat arg must be 1..240 minutes (contract §2)")
    if not 0 <= int(token) <= 0xFFFF:
        raise PayloadError("token must be uint16 (contract §2)")
    return struct.pack(">BBH", DOWNLINK_COMMANDS[command], int(arg) & 0xFF, int(token))
