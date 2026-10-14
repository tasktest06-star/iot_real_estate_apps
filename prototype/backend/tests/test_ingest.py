"""Ingest tests -- contract §4: server-side decode, idempotency, device upsert."""

from backend.app import codec, db
from conftest import DEV_EUI, DEVICE_ID, envelope


def test_uplink_is_decoded_server_side(client):
    r = client.post("/api/v1/uplink", json=envelope(1, pipe_c=11.25, ambient_c=18.40,
                                                    battery_mv=3410))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "accepted"
    assert body["duplicate"] is False
    assert body["decoded"]["pipe_c"] == 11.25
    assert body["decoded"]["delta_c"] == -7.15
    assert body["decoded"]["battery_mv"] == 3410
    assert body["verdict"] is not None          # the detector ran
    assert body["decode_error"] is None


def test_caller_supplied_decoded_payload_is_ignored(client):
    """A webhook is untrusted input (contract §4).

    The lie here is extreme on purpose: a caller claiming pipe_c = -40 and a
    tamper flag would manufacture an alert if the backend trusted the body.
    """
    body = envelope(7, pipe_c=18.0, ambient_c=19.0)
    body["uplink_message"]["decoded_payload"] = {
        "pipe_c": -40.0, "ambient_c": 25.0, "delta_c": -65.0, "battery_mv": 1,
        "flags": {"install_verified": True, "tamper": True, "cold_warning": True},
        "uplink_reason": "threshold",
    }
    r = client.post("/api/v1/uplink", json=body)
    assert r.status_code == 200
    decoded = r.json()["decoded"]
    assert decoded["pipe_c"] == 18.0
    assert decoded["ambient_c"] == 19.0
    assert decoded["battery_mv"] == 3600
    assert decoded["flags"]["tamper"] is False


def test_duplicate_f_cnt_is_idempotent(client):
    """Contract §4: accept, do not double-count, return the same result."""
    body = envelope(42, pipe_c=11.25, ambient_c=18.40, battery_mv=3410)
    first = client.post("/api/v1/uplink", json=body).json()
    second = client.post("/api/v1/uplink", json=body).json()
    third = client.post("/api/v1/uplink", json=body).json()

    assert first["duplicate"] is False
    assert second["duplicate"] is True and third["duplicate"] is True
    for key in ("status", "device_id", "dev_eui", "f_cnt", "uplink_id", "decoded",
                "verdict", "confidence", "reason", "baseline_c", "decode_error"):
        assert second[key] == first[key], key
        assert third[key] == first[key], key

    # and nothing was double-counted
    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM uplinks").fetchone()[0] == 1
        assert conn.execute("SELECT uplink_count FROM devices").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == \
            (1 if first["alert"] else 0)

    device = client.get("/api/v1/devices/%s" % DEVICE_ID).json()
    assert device["uplink_count"] == 1
    assert len(device["recent_samples"]) == 1


def test_duplicate_with_different_bytes_is_flagged(client):
    """f_cnt wrap / rejoin: idempotent per contract, but not silent.

    See the KNOWN LIMITATION note in app/ingest.py -- (dev_eui, f_cnt) is only
    unique within a session, and this is what the collision looks like.
    """
    client.post("/api/v1/uplink", json=envelope(9, pipe_c=18.0, ambient_c=19.0))
    second = client.post("/api/v1/uplink",
                         json=envelope(9, pipe_c=4.0, ambient_c=19.0)).json()
    assert second["duplicate"] is True
    assert second["payload_mismatch"] is True
    assert any("rejoin" in w for w in second["warnings"])
    assert second["decoded"]["pipe_c"] == 18.0     # the stored frame wins


def test_new_device_defaults_to_shadow_mode(client):
    """Standing rule 3: a device seen on the network is not an alerting device."""
    r = client.post("/api/v1/uplink", json=envelope(1)).json()
    assert r["alerting_enabled"] is False
    assert r["shadow_mode"] is True
    fleet = client.get("/api/v1/devices").json()
    assert fleet["count"] == 1
    assert fleet["devices"][0]["device_id"] == DEVICE_ID
    assert fleet["devices"][0]["alerting_enabled"] is False
    assert fleet["devices"][0]["shadow_mode"] is True
    with db.session() as conn:
        assert conn.execute("SELECT alerting_enabled FROM devices").fetchone()[0] == 0


def test_device_upsert_tracks_last_seen_and_counts(client):
    for i, (f_cnt, ts) in enumerate([(1, "2026-10-14T03:00:00Z"),
                                     (2, "2026-10-14T03:15:00Z"),
                                     (3, "2026-10-14T03:30:00Z")]):
        client.post("/api/v1/uplink", json=envelope(f_cnt, received_at=ts))
    d = client.get("/api/v1/devices/%s" % DEVICE_ID).json()
    assert d["uplink_count"] == 3
    assert d["last_f_cnt"] == 3
    assert d["last_seen_at"] == "2026-10-14T03:30:00Z"
    assert d["first_seen_at"] == "2026-10-14T03:00:00Z"
    assert [s["f_cnt"] for s in d["recent_samples"]] == [1, 2, 3]   # oldest first


def test_backfilled_frame_does_not_make_a_stale_device_look_fresh(client):
    """Out-of-order delivery is normal with a store-and-forward LNS.

    A three-hour-old frame arriving now must still be counted, but it must not
    move `last_seen` backwards or reset the device's verdict -- both feed the
    portfolio report's exposure and freshness numbers.
    """
    fresh = "2026-10-14T06:00:00Z"
    stale = "2026-10-14T03:00:00Z"
    client.post("/api/v1/uplink", json=envelope(10, received_at=fresh, pipe_c=18.0))
    r = client.post("/api/v1/uplink",
                    json=envelope(11, received_at=stale, pipe_c=4.0)).json()
    assert any("older than this device's last seen" in w for w in r["warnings"])

    d = client.get("/api/v1/devices/%s" % DEVICE_ID).json()
    assert d["last_seen_at"] == fresh
    assert d["first_seen_at"] == stale        # exposure starts at the earliest frame
    assert d["uplink_count"] == 2             # both frames counted
    assert d["pipe_c"] == 18.0                # latest-sample fields untouched
    assert d["last_f_cnt"] == 10
    assert len(d["recent_samples"]) == 2      # and both are stored


def test_extra_fields_are_ignored_not_rejected(client):
    """Contract §4: only the listed fields are required; extras are ignored."""
    body = envelope(5, _label={"scenario": "slow_leak", "is_true_event": True},
                    correlation_ids=["a", "b"], simulated=True)
    body["uplink_message"]["consumed_airtime"] = "0.061696s"
    body["end_device_ids"]["application_ids"] = {"application_id": "leakwatch"}
    r = client.post("/api/v1/uplink", json=body)
    assert r.status_code == 200
    assert r.json()["decoded"] is not None


def test_simulator_ground_truth_label_is_not_stored(client):
    """Contract §6: the backend ignores `_label`. It must not leak into the DB."""
    client.post("/api/v1/uplink", json=envelope(6, _label={"scenario": "fast_leak",
                                                           "is_true_event": True}))
    with db.session() as conn:
        row = conn.execute("SELECT * FROM uplinks").fetchone()
        assert "slow_leak" not in str(dict(row))
        assert "fast_leak" not in str(dict(row))


def test_malformed_envelope_is_400_not_500(client):
    """400 so the LNS stops retrying; a 5xx would be retried forever."""
    for bad in [{}, {"end_device_ids": {}}, {"end_device_ids": {"dev_eui": "A"}},
                {"end_device_ids": {"dev_eui": "A"}, "uplink_message": {"f_cnt": 1}},
                {"end_device_ids": {"dev_eui": "A"},
                 "uplink_message": {"f_cnt": "x", "frm_payload": "AAAAAAAAAAA="}}]:
        r = client.post("/api/v1/uplink", json=bad)
        assert r.status_code == 400, (bad, r.status_code, r.text)
        assert r.json()["detail"]["error"] == "malformed_envelope"


def test_undecodable_payload_is_stored_not_dropped(client):
    """Raw retention from day one (08-roadmap.md §3 P0): keep the bytes, flag it."""
    body = envelope(3)
    body["uplink_message"]["frm_payload"] = "AAAA"      # 3 bytes, not 8
    r = client.post("/api/v1/uplink", json=body)
    assert r.status_code == 200
    out = r.json()
    assert out["decoded"] is None
    assert "8 payload bytes" in out["decode_error"]
    assert out["verdict"] is None
    with db.session() as conn:
        row = conn.execute("SELECT raw_b64, decode_ok FROM uplinks").fetchone()
        assert row["raw_b64"] == "AAAA" and row["decode_ok"] == 0


def test_other_fport_is_stored_without_decoding(client):
    body = envelope(4, f_port=2)
    r = client.post("/api/v1/uplink", json=body).json()
    assert r["decoded"] is None
    assert "f_port 2" in r["decode_error"]


def test_dev_eui_is_the_identity_and_is_normalised(client):
    client.post("/api/v1/uplink", json=envelope(1, dev_eui=DEV_EUI.lower()))
    with db.session() as conn:
        assert conn.execute("SELECT dev_eui FROM devices").fetchone()[0] == DEV_EUI
    assert client.get("/api/v1/devices/%s" % DEV_EUI).status_code == 200


def test_missing_device_id_is_synthesised_from_dev_eui(client):
    body = envelope(1)
    del body["end_device_ids"]["device_id"]
    r = client.post("/api/v1/uplink", json=body)
    assert r.status_code == 200
    assert r.json()["device_id"] == "eui-%s" % DEV_EUI.lower()


def test_nanosecond_timestamps_parse(client):
    """TTS emits 9 fractional digits; Python 3.9's fromisoformat cannot."""
    r = client.post("/api/v1/uplink",
                    json=envelope(1, received_at="2026-10-14T03:00:00.123456789Z"))
    assert r.status_code == 200
    d = client.get("/api/v1/devices/%s" % DEVICE_ID).json()
    assert d["last_seen_at"] == "2026-10-14T03:00:00Z"


def test_rx_metadata_keeps_the_strongest_gateway(client):
    body = envelope(1)
    body["uplink_message"]["rx_metadata"] = [
        {"gateway_ids": {"gateway_id": "gw-far"}, "rssi": -119, "snr": -3.0},
        {"gateway_ids": {"gateway_id": "gw-near"}, "rssi": -94, "snr": 9.5},
    ]
    client.post("/api/v1/uplink", json=body)
    d = client.get("/api/v1/devices/%s" % DEVICE_ID).json()
    assert d["rssi"] == -94 and d["snr"] == 9.5
    assert d["recent_samples"][0]["rssi"] == -94


def test_unknown_device_is_404(client):
    r = client.get("/api/v1/devices/leak-9999")
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "unknown_device"


def test_healthz_reports_the_detector_in_use(client):
    body = client.get("/api/v1/healthz").json()
    assert body["status"] == "ok"
    assert body["shadow_mode_default"] is True
    assert body["detector"] in ("detection/detector.py", "backend-fallback")
    assert isinstance(body["detector_is_fallback"], bool)


def test_root_serves_the_dashboard(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "<!doctype html" in r.text[:200].lower()
    # offline: no external script or stylesheet anywhere in the file
    assert "http://" not in r.text.replace("http://www.w3.org/2000/svg", "")
    assert "https://" not in r.text


def test_schema_application_is_idempotent(client):
    """Contract §9: applied idempotently at startup."""
    client.post("/api/v1/uplink", json=envelope(1))
    db.init_db()
    db.init_db()
    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM uplinks").fetchone()[0] == 1
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"devices", "uplinks", "alerts", "valve_commands", "nuisance_feedback"} <= names


def test_both_database_env_vars_are_honoured(tmp_path, monkeypatch):
    """scripts/e2e.sh, the Dockerfile and docker-compose.yml set LEAK_DB_PATH."""
    for name in db.DB_PATH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name in db.DB_PATH_ENV_VARS:
        target = tmp_path / ("%s.db" % name.lower())
        monkeypatch.setenv(name, str(target))
        db.set_db_path(target)                 # as the env var would resolve
        assert db.db_path() == target.resolve()
        db.init_db()
        assert target.exists()
        monkeypatch.delenv(name)
    # and the env var actually drives resolution when nothing is set explicitly
    monkeypatch.setenv("LEAK_DB_PATH", str(tmp_path / "from_env.db"))
    db._db_path = None                         # force re-resolution
    assert db.db_path() == (tmp_path / "from_env.db").resolve()


def test_stale_database_file_fails_loudly(tmp_path):
    """No migration tool, so a stale file must be a clear error, not a 500 later."""
    import sqlite3

    import pytest

    stale = tmp_path / "stale.db"
    conn = sqlite3.connect(str(stale))
    conn.executescript(
        "CREATE TABLE devices (dev_eui TEXT PRIMARY KEY, device_id TEXT);"
        "CREATE TABLE uplinks (id INTEGER PRIMARY KEY, dev_eui TEXT, f_cnt INTEGER);")
    conn.close()
    db.set_db_path(stale)
    try:
        with pytest.raises(RuntimeError) as exc:
            db.init_db()
        assert "predates backend/app/schema.sql" in str(exc.value)
        assert "alerting_enabled" in str(exc.value)
    finally:
        db.set_db_path(tmp_path / "fresh.db")
        db.init_db()


def test_unique_constraint_exists_on_dev_eui_f_cnt(client):
    """The idempotency guarantee is a constraint, not just application code."""
    with db.session() as conn:
        indexes = conn.execute("PRAGMA index_list('uplinks')").fetchall()
        unique_cols = []
        for idx in indexes:
            if idx["unique"]:
                cols = [r["name"] for r in
                        conn.execute("PRAGMA index_info('%s')" % idx["name"]).fetchall()]
                unique_cols.append(cols)
    assert ["dev_eui", "f_cnt"] in unique_cols


def test_codec_encode_helper_round_trips_over_http(client):
    r = client.get("/api/v1/codec/encode",
                   params={"pipe_c": -7.15, "ambient_c": 2.4, "battery_mv": 3200}).json()
    assert codec.decode_frm_payload(r["frm_payload"])["pipe_c"] == -7.15
    assert r["decoded"]["delta_c"] == -9.55
