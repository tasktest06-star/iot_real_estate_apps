"""Valve safety: the arming refusal and the unconfirmed outcome.

Contract §8 and 05-open-source-stack.md §5.3 -- fail-as-is, never fail-closed.
Two failure modes are being guarded, and both are in 08-roadmap.md §8 risk 9:
a valve that is operated when the install probe says it should not be, and a
command reported as success when nobody knows whether it moved.
"""

import base64

from backend.app import codec, db, valve
from conftest import DEV_EUI, DEVICE_ID, envelope

GOOD_PROBE = {"breakaway_torque_mnm": 900.0, "stem_leak": False, "cycle_time_ms": 4200}


def _seen(client, f_cnt=1, **kw):
    """An uplink timestamped now.

    The confirm window is relative to the command's issue time, and a frame
    that predates the command cannot confirm it, so these tests cannot use
    conftest's fixed historical timestamp.
    """
    kw.setdefault("received_at", db.iso(db.now_s()))
    return client.post("/api/v1/uplink", json=envelope(f_cnt, **kw)).json()


def _probe(client, **overrides):
    body = dict(GOOD_PROBE)
    body.update(overrides)
    return client.post("/api/v1/devices/%s/install_probe" % DEVICE_ID, json=body)


# ------------------------------------------------------------- refusals -----
def test_unprobed_device_refuses_with_409(client):
    """The default state of every device is "not armed"."""
    _seen(client)
    fleet = client.get("/api/v1/devices").json()["devices"][0]
    assert fleet["armed"] is False
    assert fleet["arming_reason_code"] == "no_install_probe"

    r = client.post("/api/v1/devices/%s/valve" % DEVICE_ID, json={"command": "close"})
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "device_not_armed"
    assert body["status"] == "refused"
    assert body["reason_code"] == "no_install_probe"
    assert body["action_taken"] == "none"
    assert body["fail_mode"].startswith("fail-as-is")
    assert "install probe" in body["reason"]
    assert body["command_id"]


def test_refusal_is_persisted_with_its_reason(client):
    """Risk 9's leading indicator cannot be watched if refusals are not recorded."""
    _seen(client)
    client.post("/api/v1/devices/%s/valve" % DEVICE_ID, json={"command": "close"})
    client.post("/api/v1/devices/%s/valve" % DEVICE_ID, json={"command": "open"})
    with db.session() as conn:
        rows = conn.execute("SELECT * FROM valve_commands ORDER BY id").fetchall()
    assert len(rows) == 2
    assert [r["status"] for r in rows] == ["refused", "refused"]
    assert [r["command"] for r in rows] == ["close", "open"]
    assert all(r["payload_b64"] is None for r in rows)   # nothing was built to send
    assert all("not armed" in r["reason"] for r in rows)
    # a machine-readable code, so risk 9's leading indicator can be grouped
    assert [r["reason_code"] for r in rows] == ["no_install_probe", "no_install_probe"]

    detail = client.get("/api/v1/devices/%s" % DEVICE_ID).json()
    assert [c["status"] for c in detail["valve_commands"]] == ["refused", "refused"]


def test_breakaway_torque_over_threshold_refuses(client):
    """05 §5.3 arm_or_refuse(): torque above the refuse threshold => V_REFUSED."""
    _seen(client)
    probe = _probe(client, breakaway_torque_mnm=valve.TORQUE_REFUSE_THRESHOLD_MNM + 1).json()
    assert probe["armed"] is False
    assert probe["breakaway_torque_ok"] is False
    assert probe["valve_state"] == "refused"

    r = client.post("/api/v1/devices/%s/valve" % DEVICE_ID, json={"command": "close"})
    assert r.status_code == 409
    assert r.json()["reason_code"] == "breakaway_torque_above_threshold"
    assert "stall mid-travel" in r.json()["reason"]


def test_stem_leak_refuses(client):
    _seen(client)
    assert _probe(client, stem_leak=True).json()["armed"] is False
    r = client.post("/api/v1/devices/%s/valve" % DEVICE_ID, json={"command": "close"})
    assert r.status_code == 409
    assert r.json()["reason_code"] == "stem_leak_detected"


def test_cycle_timeout_refuses_as_a_fault(client):
    _seen(client)
    probe = _probe(client, cycle_time_ms=valve.CYCLE_TIMEOUT_MS + 1).json()
    assert probe["valve_state"] == "fault"
    r = client.post("/api/v1/devices/%s/valve" % DEVICE_ID, json={"command": "close"})
    assert r.status_code == 409
    assert r.json()["reason_code"] == "cycle_time_above_timeout"


def test_a_passing_probe_arms_the_device(client):
    _seen(client)
    probe = _probe(client).json()
    assert probe["armed"] is True
    assert probe["valve_state"] == "open"
    assert probe["torque_refuse_threshold_mnm"] == valve.TORQUE_REFUSE_THRESHOLD_MNM
    fleet = client.get("/api/v1/devices").json()["devices"][0]
    assert fleet["armed"] is True and fleet["arming_reason_code"] is None


# ------------------------------------------------------------- outcomes -----
def test_armed_command_is_pending_not_success(client):
    """A queued downlink is `pending`. Nothing is ever reported as success."""
    _seen(client)
    _probe(client)
    r = client.post("/api/v1/devices/%s/valve" % DEVICE_ID,
                    json={"command": "close", "requested_by": "ops@example.test"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending"
    assert body["armed"] is True
    assert body["f_port"] == codec.DOWNLINK_FPORT == 2
    assert body["token_echo_verified"] is False
    assert "does not arbitrate" in body["arbitration"]

    raw = base64.b64decode(body["payload_b64"])
    assert len(raw) == 4
    assert raw[0] == codec.DOWNLINK_COMMANDS["valve_close"]
    assert (raw[2] << 8 | raw[3]) == body["token"] != 0

    statuses = [c["status"] for c in
                client.get("/api/v1/devices/%s" % DEVICE_ID).json()["valve_commands"]]
    assert statuses == ["pending"]


def test_command_with_no_confirming_uplink_becomes_unconfirmed(client):
    """Contract §8: never silently `success`.

    The deadline is moved into the past rather than sleeping; the expiry sweep
    then runs as part of an ordinary read, which is how it happens in service.
    """
    _seen(client)
    _probe(client)
    issued = client.post("/api/v1/devices/%s/valve" % DEVICE_ID,
                         json={"command": "close"}).json()
    with db.session() as conn:
        conn.execute("UPDATE valve_commands SET issued_ts = issued_ts - ?,"
                     " confirm_deadline_ts = confirm_deadline_ts - ? WHERE id = ?",
                     (valve.CONFIRM_WINDOW_S + 60, valve.CONFIRM_WINDOW_S + 60,
                      issued["command_id"]))

    detail = client.get("/api/v1/devices/%s" % DEVICE_ID).json()
    command = detail["valve_commands"][0]
    assert command["status"] == "unconfirmed"
    assert "no confirming uplink" in command["reason"]
    assert command["confirmed_at"] is None
    assert detail["valve_state"] == "open"       # as-is: unchanged, not assumed closed

    portfolio = client.get("/api/v1/insurer/portfolio").json()
    assert portfolio["mitigation"]["valve_commands_by_status"] == {"unconfirmed": 1}
    assert portfolio["mitigation"]["confirmed_closes"] == 0


def test_confirming_uplink_confirms_the_command(client):
    """Only an install_probe_result frame (uplink_reason 3) confirms."""
    _seen(client, f_cnt=1)
    _probe(client)
    issued = client.post("/api/v1/devices/%s/valve" % DEVICE_ID,
                         json={"command": "close"}).json()

    # a heartbeat proves the device is alive, not that the valve moved
    _seen(client, f_cnt=2, uplink_reason=0)
    assert client.get("/api/v1/devices/%s" % DEVICE_ID).json()[
        "valve_commands"][0]["status"] == "pending"

    r = _seen(client, f_cnt=3, uplink_reason=3)
    assert r["valve_command_confirmed"] == issued["command_id"]
    detail = client.get("/api/v1/devices/%s" % DEVICE_ID).json()
    command = detail["valve_commands"][0]
    assert command["status"] == "confirmed"
    assert command["confirmed_at"] is not None
    assert command["confirming_uplink_id"] is not None
    assert detail["valve_state"] == "closed"


def test_a_late_confirming_uplink_does_not_resurrect_an_expired_command(client):
    _seen(client, f_cnt=1)
    _probe(client)
    issued = client.post("/api/v1/devices/%s/valve" % DEVICE_ID,
                         json={"command": "close"}).json()
    with db.session() as conn:
        conn.execute("UPDATE valve_commands SET issued_ts = issued_ts - ?,"
                     " confirm_deadline_ts = confirm_deadline_ts - ? WHERE id = ?",
                     (valve.CONFIRM_WINDOW_S + 60, valve.CONFIRM_WINDOW_S + 60,
                      issued["command_id"]))
    client.get("/api/v1/devices/%s" % DEVICE_ID)        # triggers the sweep
    r = _seen(client, f_cnt=4, uplink_reason=3)
    assert r["valve_command_confirmed"] is None
    assert client.get("/api/v1/devices/%s" % DEVICE_ID).json()[
        "valve_commands"][0]["status"] == "unconfirmed"


def test_nothing_in_the_backend_can_auto_close_a_valve(client):
    """The shutoff decision is local (05 §5.3). A leak verdict issues nothing."""
    _seen(client, f_cnt=1)
    _probe(client)
    start = db.now_s() - 16 * 900
    for i in range(16):
        _seen(client, f_cnt=2 + i, pipe_c=9.0, ambient_c=19.0,
              received_at=db.iso(start + i * 900))
    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM valve_commands").fetchone()[0] == 0


# ------------------------------------------------------------ validation ----
def test_unknown_command_is_400(client):
    _seen(client)
    r = client.post("/api/v1/devices/%s/valve" % DEVICE_ID, json={"command": "wiggle"})
    assert r.status_code == 400
    assert r.json()["detail"]["allowed"] == ["close", "open"]


def test_valve_on_unknown_device_is_404(client):
    r = client.post("/api/v1/devices/leak-9999/valve", json={"command": "close"})
    assert r.status_code == 404


def test_probe_on_unknown_device_is_404(client):
    r = client.post("/api/v1/devices/leak-9999/install_probe", json=GOOD_PROBE)
    assert r.status_code == 404


def test_device_can_be_addressed_by_dev_eui(client):
    _seen(client)
    r = client.post("/api/v1/devices/%s/valve" % DEV_EUI, json={"command": "close"})
    assert r.status_code == 409
    assert r.json()["device_id"] == DEVICE_ID
