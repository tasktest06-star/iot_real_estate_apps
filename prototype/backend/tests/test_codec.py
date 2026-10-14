"""Payload codec tests -- contract §1 and §3.

The int16 sign extension is the test that matters: a Node codec and a C encoder
are written independently against the same byte table, and a decoder that drops
the sign turns a leak (pipe colder than ambient) into its opposite.
"""

import base64

import pytest

from backend.app import codec


def test_contract_section_3_example_decodes_exactly():
    """The §3 example: pipe 11.25, ambient 18.40, delta -7.15, battery 3410."""
    raw = bytes([0x04, 0x65, 0x07, 0x30, 0x0D, 0x52, 0x01, 0x00])
    out = codec.decode_bytes(raw)
    assert out["pipe_c"] == 11.25
    assert out["ambient_c"] == 18.40
    # Exactly -7.15, not -7.149999999999999: the subtraction is done in
    # centi-degrees before the divide (see app/codec.py).
    assert out["delta_c"] == -7.15
    assert out["battery_mv"] == 3410
    assert out["flags"] == {"install_verified": True, "tamper": False, "cold_warning": False}
    assert out["uplink_reason"] == "heartbeat"


def test_negative_temperatures_sign_extend():
    """A cold basement is negative: -7.15 C must survive, not become +~320 C."""
    b64 = codec.encode_frm_payload(-7.15, 2.40, 3200)
    out = codec.decode_frm_payload(b64)
    assert out["pipe_c"] == -7.15
    assert out["ambient_c"] == 2.40
    assert out["delta_c"] == -9.55
    # The failure mode being guarded: reading the bytes as unsigned.
    raw = base64.b64decode(b64)
    unsigned = (raw[0] << 8) | raw[1]
    assert unsigned == 64821 and out["pipe_c"] < 0


@pytest.mark.parametrize("pipe_c,ambient_c", [
    (-327.68, 327.67),   # the int16 rails
    (0.0, 0.0),
    (-0.01, 0.01),
    (80.0, -40.0),
])
def test_round_trip_at_the_rails(pipe_c, ambient_c):
    out = codec.decode_frm_payload(codec.encode_frm_payload(pipe_c, ambient_c, 65535))
    assert out["pipe_c"] == pipe_c
    assert out["ambient_c"] == ambient_c
    assert out["battery_mv"] == 65535


def test_flags_are_independent_bits():
    out = codec.decode_frm_payload(
        codec.encode_frm_payload(1.0, 2.0, 3000, install_verified=False, tamper=True,
                                 cold_warning=True))
    assert out["flags"] == {"install_verified": False, "tamper": True, "cold_warning": True}


def test_unknown_uplink_reason_does_not_throw():
    """Contract §3: unknown values decode to "reserved_<n>", never an exception.

    A decoder that throws takes the whole webhook down, which loses every
    device's frames and not just the odd one.
    """
    out = codec.decode_frm_payload(codec.encode_frm_payload(1.0, 2.0, 3000, uplink_reason=200))
    assert out["uplink_reason"] == "reserved_200"
    assert out["uplink_reason_code"] == 200


def test_all_four_defined_reasons():
    names = [codec.decode_frm_payload(
        codec.encode_frm_payload(1.0, 2.0, 3000, uplink_reason=n))["uplink_reason"]
        for n in range(4)]
    assert names == ["heartbeat", "threshold", "boot", "install_probe_result"]


def test_reserved_flag_bits_are_reported_not_rejected():
    raw = bytes([0x00, 0x64, 0x00, 0x00, 0x0C, 0x80, 0x08, 0x00])  # bit 3 set
    out = codec.decode_bytes(raw)
    assert out["reserved_flag_bits"] == 1
    assert out["flags"]["install_verified"] is False


@pytest.mark.parametrize("bad", [b"", b"\x01" * 7, b"\x01" * 9])
def test_wrong_length_is_a_payload_error(bad):
    with pytest.raises(codec.PayloadError):
        codec.decode_bytes(bad)


@pytest.mark.parametrize("bad", ["not base64 at all!", "QQ", None, 42])
def test_bad_base64_is_a_payload_error(bad):
    with pytest.raises(codec.PayloadError):
        codec.decode_frm_payload(bad)


def test_downlink_encoding_matches_contract_section_2():
    assert codec.encode_downlink("valve_close", 0, 0x1234) == bytes([0x01, 0x00, 0x12, 0x34])
    assert codec.encode_downlink("valve_open", 0, 1) == bytes([0x02, 0x00, 0x00, 0x01])
    assert codec.encode_downlink("set_heartbeat", 240, 0) == bytes([0x03, 0xF0, 0x00, 0x00])
    with pytest.raises(codec.PayloadError):
        codec.encode_downlink("set_heartbeat", 0, 0)      # arg must be 1..240
    with pytest.raises(codec.PayloadError):
        codec.encode_downlink("valve_close", 0, 0x10000)  # token is uint16


def test_contract_section_4_example_payload_is_internally_inconsistent():
    """Pinned so the mismatch is visible, not so it is blessed.

    `frm_payload: "BLEAAQ0yAQA="` in contract §4 decodes to 04b100010d320100 =
    pipe 12.01 C, ambient 0.01 C, battery 3378 mV -- it is NOT the §3 example
    (11.25 / 18.40 / 3410, which is "BGUHMA1SAQA="). Reported in the handback;
    the codec follows the byte table, which both examples agree on.
    """
    out = codec.decode_frm_payload("BLEAAQ0yAQA=")
    assert out["pipe_c"] == 12.01
    assert out["ambient_c"] == 0.01
    assert out["battery_mv"] == 3378
    assert codec.encode_frm_payload(11.25, 18.40, 3410, install_verified=True) == "BGUHMA1SAQA="
