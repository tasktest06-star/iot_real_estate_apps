"""Alert lifecycle, shadow mode and nuisance feedback.

Shadow mode is standing rule 3 of 08-roadmap.md §0 and contract §8 calls it
mandatory and the default, so it gets the most direct tests in the suite: the
notification sink must be empty, `alerts.notified_ts` must be NULL, and the
API must say `notified: false`. Three independent assertions, because "we
didn't send it" is the claim the whole pilot rests on.

The decision-level tests call `alerts.record_decision` with a synthetic
Decision instead of driving the detector, so they hold whichever detector is
live (contract §5 fixes the verdict vocabulary, not the thresholds).
"""

from backend.app import alerts as alerts_mod
from backend.app import db, detector_bridge, ingest
from conftest import DEV_EUI, DEVICE_ID, envelope

LEAK = {"verdict": "leak_suspected", "confidence": 88,
        "reason": "delta -7.20 C held 1800 s below baseline -1.00 C",
        "delta_c": -7.2, "baseline_c": -1.0}
FREEZE = {"verdict": "freeze_risk", "confidence": 85, "reason": "pipe at 1.0 C",
          "delta_c": -1.0, "baseline_c": -1.0}
QUIET = {"verdict": "quiet", "confidence": 60, "reason": "delta near baseline",
         "delta_c": -1.0, "baseline_c": -1.0}


def _device(client, device_id=DEVICE_ID, dev_eui=DEV_EUI, f_cnt=1):
    client.post("/api/v1/uplink", json=envelope(f_cnt, device_id=device_id, dev_eui=dev_eui))
    with db.session() as conn:
        return db.row_to_dict(
            conn.execute("SELECT * FROM devices WHERE dev_eui = ?", (dev_eui,)).fetchone())


def _raise(device, decision=LEAK, ts=1760000000):
    with db.session() as conn:
        return alerts_mod.record_decision(conn, device, None, decision, ts, ts)


# ---------------------------------------------------------------- shadow ----
def test_shadow_alert_never_notifies(client):
    """The core of standing rule 3: logged, and shown to nobody."""
    device = _device(client)
    assert device["alerting_enabled"] == 0

    alerts_mod.clear_notifications()
    alert = _raise(device)

    assert alert["status"] == "shadow"
    assert alert["raised_in_shadow"] == 1
    assert alert["notified_ts"] is None
    assert alert["notify_channel"] is None
    assert alerts_mod.notifications() == []          # nothing was emitted

    listed = client.get("/api/v1/alerts", params={"status": "shadow"}).json()
    assert listed["count"] == 1
    assert listed["alerts"][0]["notified"] is False
    assert listed["alerts"][0]["raised_in_shadow"] is True
    assert client.get("/api/v1/notifications").json()["count"] == 0


def test_many_shadow_alerts_emit_nothing(client):
    """Repeat it across devices and verdicts: the sink stays empty."""
    alerts_mod.clear_notifications()
    for i in range(5):
        dev = _device(client, device_id="leak-%04d" % (100 + i),
                      dev_eui="70B3D57ED00001%02X" % i, f_cnt=1)
        _raise(dev, LEAK)
        _raise(dev, FREEZE)
    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 10
        assert conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE notified_ts IS NOT NULL").fetchone()[0] == 0
    assert alerts_mod.notifications() == []


def test_enabling_alerting_requires_a_written_reason(client):
    _device(client)
    r = client.post("/api/v1/devices/%s/alerting" % DEVICE_ID, json={"enabled": True})
    assert r.status_code == 422          # `reason` is required by the model
    r = client.post("/api/v1/devices/%s/alerting" % DEVICE_ID,
                    json={"enabled": True, "reason": "ok"})
    assert r.status_code == 422          # ...and must be more than two characters


def test_enabled_device_notifies_once(client):
    _device(client)
    r = client.post("/api/v1/devices/%s/alerting" % DEVICE_ID, json={
        "enabled": True, "reason": "nuisance rate measured at 0.2/device-year in shadow",
        "actor": "ops@example.test"})
    assert r.status_code == 200
    assert "standing rule 3" in r.json()["warning"].lower()
    assert r.json()["device"]["alerting_enabled"] is True

    with db.session() as conn:
        device = db.row_to_dict(
            conn.execute("SELECT * FROM devices WHERE dev_eui = ?", (DEV_EUI,)).fetchone())
    assert "ops@example.test" in device["alerting_change_reason"]

    alerts_mod.clear_notifications()
    alert = _raise(device)
    assert alert["status"] == "open"
    assert alert["raised_in_shadow"] == 0
    assert alert["notified_ts"] is not None
    sink = alerts_mod.notifications()
    assert len(sink) == 1
    assert sink[0]["alert_id"] == alert["id"]
    assert sink[0]["verdict"] == "leak_suspected"

    # a second uplink on the same live alert does not re-notify
    _raise(device, LEAK, ts=1760000900)
    assert len(alerts_mod.notifications()) == 1


def test_shadow_alerts_are_not_promoted_when_alerting_is_enabled(client):
    """Retroactively notifying an old shadow alert would be the worst of both."""
    device = _device(client)
    alert = _raise(device)
    alerts_mod.clear_notifications()
    client.post("/api/v1/devices/%s/alerting" % DEVICE_ID,
                json={"enabled": True, "reason": "gate passed, going live"})
    listed = client.get("/api/v1/alerts").json()["alerts"]
    assert [a["status"] for a in listed] == ["shadow"]
    assert alerts_mod.notifications() == []
    assert alert["id"] == listed[0]["id"]


# ------------------------------------------------------------ lifecycle -----
def test_one_live_alert_per_device_and_verdict(client):
    """A 45-minute leak on a 15-minute heartbeat is one alert, not three.

    Counting every uplink would inflate the P1 gate's nuisance rate (contract §7).
    """
    device = _device(client)
    first = _raise(device, LEAK, ts=1760000000)
    second = _raise(device, LEAK, ts=1760000900)
    third = _raise(device, LEAK, ts=1760001800)
    assert first["_created"] is True
    assert second["_created"] is False and third["_created"] is False
    assert first["id"] == second["id"] == third["id"]
    assert third["uplink_count"] == 3
    assert third["last_uplink_ts"] == 1760001800

    # a different verdict is a different alert
    freeze = _raise(device, FREEZE, ts=1760002700)
    assert freeze["id"] != first["id"]
    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 2


def test_non_alerting_verdicts_raise_nothing(client):
    """quiet/watch/sensor_fault/install_unverified are not customer perils.

    08-roadmap.md §8 risk 4: do not count unverified installs. They surface as
    fleet health in the portfolio report instead.
    """
    device = _device(client)
    for verdict in ("quiet", "watch", "sensor_fault", "install_unverified"):
        decision = dict(QUIET, verdict=verdict)
        assert _raise(device, decision) is None
    assert client.get("/api/v1/alerts").json()["count"] == 0
    assert set(detector_bridge.ALERTING_VERDICTS) == {"leak_suspected", "freeze_risk"}


def test_detection_latency_is_measured_from_the_first_anomalous_sample(client):
    device = _device(client)
    with db.session() as conn:
        alert = alerts_mod.record_decision(conn, device, None, LEAK, 1760001800, 1760000000)
    assert alert["detection_latency_s"] == 1800
    assert alert["first_anomalous_ts"] == 1760000000


# ------------------------------------------------------------- the ack ------
def test_ack_records_nuisance_feedback(client):
    """The one-click "this was nothing" of 08-roadmap.md §8 risk 2."""
    device = _device(client)
    alert = _raise(device)
    r = client.post("/api/v1/alerts/%d/ack" % alert["id"],
                    json={"was_nuisance": True, "note": "dishwasher", "actor": "claims-1"})
    assert r.status_code == 200
    body = r.json()
    assert body["recorded"] == "nuisance"
    assert body["alert"]["status"] == "nuisance"
    assert body["alert"]["was_nuisance"] is True
    assert body["alert"]["note"] == "dishwasher"
    assert body["alert"]["acked_by"] == "claims-1"

    with db.session() as conn:
        rows = conn.execute("SELECT * FROM nuisance_feedback").fetchall()
    assert len(rows) == 1
    assert rows[0]["was_nuisance"] == 1
    assert rows[0]["note"] == "dishwasher"
    assert rows[0]["alert_id"] == alert["id"]

    assert client.get("/api/v1/alerts", params={"status": "nuisance"}).json()["count"] == 1
    assert client.get("/api/v1/alerts", params={"status": "shadow"}).json()["count"] == 0


def test_ack_can_confirm_a_true_event(client):
    device = _device(client)
    alert = _raise(device)
    body = client.post("/api/v1/alerts/%d/ack" % alert["id"],
                       json={"was_nuisance": False, "note": "burst feed to the washer"}).json()
    assert body["recorded"] == "confirmed_true_event"
    assert body["alert"]["status"] == "acked"
    assert body["alert"]["was_nuisance"] is False
    with db.session() as conn:
        assert conn.execute(
            "SELECT was_nuisance FROM nuisance_feedback").fetchone()[0] == 0


def test_correcting_an_ack_appends_and_never_erases(client):
    device = _device(client)
    alert = _raise(device)
    client.post("/api/v1/alerts/%d/ack" % alert["id"], json={"was_nuisance": True})
    client.post("/api/v1/alerts/%d/ack" % alert["id"],
                json={"was_nuisance": False, "note": "actually a real leak"})
    with db.session() as conn:
        rows = conn.execute(
            "SELECT was_nuisance FROM nuisance_feedback ORDER BY id").fetchall()
    assert [r[0] for r in rows] == [1, 0]
    assert client.get("/api/v1/alerts").json()["alerts"][0]["status"] == "acked"


def test_ack_on_unknown_alert_is_404(client):
    r = client.post("/api/v1/alerts/4242/ack", json={"was_nuisance": True})
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "unknown_alert"


def test_alerts_status_filter_rejects_nonsense(client):
    r = client.get("/api/v1/alerts", params={"status": "exploded"})
    assert r.status_code == 400
    assert r.json()["detail"]["allowed"] == ["shadow", "open", "acked", "nuisance"]


# ------------------------------------------------- end-to-end through ingest -
def test_sustained_leak_through_the_real_ingest_path_stays_in_shadow(client):
    """Drive the detector over HTTP, not a synthetic decision.

    The leak signal is deliberately gross (pipe 9 C below a stable ambient, held
    for three hours) so any contract §5 detector should react. The assertion
    that holds for *every* detector is the shadow-mode invariant; the assertion
    that an alert exists at all is only made when the fallback is live, because
    the real detector's thresholds are its own component's business.
    """
    alerts_mod.clear_notifications()
    ingest.reset_detector_states()
    base = 1760000000
    f_cnt = 0
    # four quiet frames to establish a baseline, then a sustained cold pipe
    for i in range(4):
        f_cnt += 1
        client.post("/api/v1/uplink", json=envelope(
            f_cnt, pipe_c=18.0, ambient_c=19.0,
            received_at=db.iso(base + i * 900)))
    verdicts = []
    for i in range(12):
        f_cnt += 1
        r = client.post("/api/v1/uplink", json=envelope(
            f_cnt, pipe_c=10.0, ambient_c=19.0,
            received_at=db.iso(base + (4 + i) * 900))).json()
        verdicts.append(r["verdict"])
        assert r["shadow_mode"] is True
        if r["alert"]:
            assert r["alert"]["status"] == "shadow"
            assert r["alert"]["notified"] is False

    assert alerts_mod.notifications() == []
    with db.session() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE notified_ts IS NOT NULL").fetchone()[0] == 0
    if detector_bridge.using_fallback():
        assert "leak_suspected" in verdicts
        assert client.get("/api/v1/alerts",
                          params={"status": "shadow"}).json()["count"] == 1
