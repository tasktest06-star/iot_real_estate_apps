"""The portfolio report: surrogate metrics, and the refusal to claim more.

08-roadmap.md §8 risk 3 and §9's "explicitly refuse to promise" column are the
specification here. The tests therefore assert two kinds of thing: that the
surrogates are present and arithmetically what they say they are, and that the
response contains no frequency-reduction claim anywhere.
"""

import json

from backend.app import alerts as alerts_mod
from backend.app import db, insurer
from conftest import DEV_EUI, DEVICE_ID, envelope

LEAK = {"verdict": "leak_suspected", "confidence": 88, "reason": "sustained negative delta",
        "delta_c": -7.2, "baseline_c": -1.0}
# Inside the report's default 90-day window: the report is a windowed view, so a
# fixed historical timestamp would fall outside it and read as "no alerts".
BASE_TS = db.now_s() - 3600


def _device(client, device_id=DEVICE_ID, dev_eui=DEV_EUI, f_cnt=1, **kw):
    client.post("/api/v1/uplink",
                json=envelope(f_cnt, device_id=device_id, dev_eui=dev_eui, **kw))
    with db.session() as conn:
        return db.row_to_dict(
            conn.execute("SELECT * FROM devices WHERE dev_eui = ?", (dev_eui,)).fetchone())


def _raise(device, decision=LEAK, ts=BASE_TS):
    with db.session() as conn:
        return alerts_mod.record_decision(conn, device, None, decision, ts, ts)


def test_empty_portfolio_reports_zeroes_not_estimates(client):
    body = client.get("/api/v1/insurer/portfolio").json()
    assert body["portfolio"]["devices_known"] == 0
    assert body["detection"]["alerts_raised"] == 0
    assert body["loss_avoided_estimate"]["estimated_severity_avoided_usd_upper"] == 0.0
    assert body["detection"]["nuisance_alerts_per_device_year"] is None
    assert body["detection"]["p1_gate"]["status"] == "not_evaluable"


def test_no_claim_frequency_reduction_anywhere_in_the_response(client):
    """The field that must not exist. Checked against the serialised JSON.

    A "claims reduced by X%" string is unprovable at pilot scale (escape of
    water is ~1.5 claims per 100 house-years) and §9 P1 explicitly refuses to
    promise it.
    """
    _device(client)
    body = client.get("/api/v1/insurer/portfolio").json()
    blob = json.dumps(body).lower()
    for forbidden in ("claims_reduced", "claim_reduction", "frequency_reduction_pct",
                      "claims_avoided", "premium_discount", "premium_credit"):
        assert forbidden not in blob, forbidden
    # the refusal is in the payload, not just absent from it
    claims = [c["claim"] for c in body["not_claimed"]]
    assert "claim-frequency reduction (%)" in claims
    assert body["loss_avoided_estimate"]["statistical_power"][
        "frequency_claim_supportable"] is False
    assert all(c["status"] == "explicitly not claimed" for c in body["not_claimed"])


def test_nuisance_rate_is_exposed_after_an_ack(client):
    """Requirement: the portfolio report exposes the resulting nuisance rate."""
    device = _device(client)
    a1 = _raise(device, LEAK, ts=BASE_TS)
    client.post("/api/v1/alerts/%d/ack" % a1["id"],
                json={"was_nuisance": True, "note": "washing machine"})
    body = client.get("/api/v1/insurer/portfolio").json()["detection"]
    assert body["nuisance_alerts"] == 1
    assert body["reviewed_by_operator"] == 1
    assert body["nuisance_rate_of_reviewed"] == 1.0
    assert body["by_status"] == {"nuisance": 1}
    assert body["raised_in_shadow_mode"] == 1
    assert body["customer_notifications_sent"] == 0


def test_nuisance_per_device_year_uses_observed_exposure(client):
    """The P1 gate's metric, computed from real device-days -- not assumed."""
    # one device observed for 30 days, ending now: 30/365.25 house-years
    start = db.now_s() - 30 * 86400
    client.post("/api/v1/uplink", json=envelope(1, received_at=db.iso(start)))
    client.post("/api/v1/uplink", json=envelope(2, received_at=db.iso(db.now_s())))
    with db.session() as conn:
        device = db.row_to_dict(conn.execute("SELECT * FROM devices").fetchone())
    alert = _raise(device, LEAK, ts=start + 86400)
    client.post("/api/v1/alerts/%d/ack" % alert["id"], json={"was_nuisance": True})

    # a window that contains the whole observation period
    body = client.get("/api/v1/insurer/portfolio",
                      params={"window_days": 3650}).json()
    portfolio, detection = body["portfolio"], body["detection"]
    assert round(portfolio["observed_device_days"], 1) == 30.0
    expected = 1 / (30 * 86400 / insurer.SECONDS_PER_YEAR)
    assert abs(detection["nuisance_alerts_per_device_year"] - expected) < 0.01
    assert detection["nuisance_alerts_per_device_year"] > 0.5      # 12.2/device-year
    # ...and the gate still refuses to call that a failure of the product,
    # because one device for 30 days cannot evaluate it either way.
    assert detection["p1_gate"]["evaluable"] is False
    assert detection["p1_gate"]["status"] == "not_evaluable"
    assert any("gate needs >=50" in b for b in detection["p1_gate"]["blockers"])
    assert any("90" in b for b in detection["p1_gate"]["blockers"])


def test_unreviewed_alerts_block_the_gate(client):
    device = _device(client)
    _raise(device, LEAK)
    gate = client.get("/api/v1/insurer/portfolio").json()["detection"]["p1_gate"]
    assert any("not yet reviewed" in b for b in gate["blockers"])
    assert gate["definition"].startswith("08-roadmap.md §3 P1")


def test_severity_model_is_arithmetic_over_observed_events(client):
    """Every number traces to the DB or to a cited benchmark."""
    device = _device(client)
    a1 = _raise(device, LEAK, ts=BASE_TS)
    client.post("/api/v1/alerts/%d/ack" % a1["id"],
                json={"was_nuisance": False, "note": "confirmed burst"})
    est = client.get("/api/v1/insurer/portfolio").json()["loss_avoided_estimate"]
    severity = est["inputs"]["mean_water_claim_severity_usd"]["value"]
    reduction = est["inputs"]["external_severity_reduction_fraction"]["value"]
    assert severity == 15400 and reduction == 0.24
    assert est["events_upper_bound"] == 1
    assert est["events_lower_bound"] == 0          # no confirmed shutoff
    assert est["estimated_severity_avoided_usd_upper"] == round(15400 * 0.24, 2)
    assert est["estimated_severity_avoided_usd_lower"] == 0.0
    assert est["confidence"].startswith("illustrative")
    for key, value in est["inputs"].items():
        assert value["source"].startswith(("01-", "08-")), key
        assert value["provenance"]
    assert "NOT this device's measured effect" in \
        est["inputs"]["external_severity_reduction_fraction"]["note"]


def test_mitigated_event_requires_a_confirmed_shutoff_to_count_as_mitigated(client):
    """"Detected" is not "avoided", and the report keeps them apart."""
    _device(client, f_cnt=1)
    client.post("/api/v1/devices/%s/install_probe" % DEVICE_ID,
                json={"breakaway_torque_mnm": 800.0, "stem_leak": False})
    with db.session() as conn:
        device = db.row_to_dict(conn.execute("SELECT * FROM devices").fetchone())
    alert = _raise(device, LEAK, ts=db.now_s())
    client.post("/api/v1/alerts/%d/ack" % alert["id"], json={"was_nuisance": False})
    client.post("/api/v1/devices/%s/valve" % DEVICE_ID, json={"command": "close"})
    # the confirming frame must be timestamped after the command was issued
    client.post("/api/v1/uplink", json=envelope(2, uplink_reason=3,
                                                received_at=db.iso(db.now_s())))

    mitigation = client.get("/api/v1/insurer/portfolio").json()["mitigation"]
    assert mitigation["mitigated_events_with_confirmed_shutoff"] == 1
    event = mitigation["mitigated_events"][0]
    assert event["confirmed_shutoff"] is True
    assert event["time_to_shutoff_s"] is not None
    assert mitigation["confirmed_closes"] == 1
    assert mitigation["median_time_to_confirmed_shutoff_s"] is not None
    assert "cannot see" in mitigation["unobservable_mitigation_note"]


def test_refusals_are_grouped_by_machine_readable_reason(client):
    """08-roadmap.md §8 risk 9's leading indicator: units arming above threshold."""
    _device(client, f_cnt=1)
    client.post("/api/v1/devices/%s/valve" % DEVICE_ID, json={"command": "close"})
    client.post("/api/v1/devices/%s/install_probe" % DEVICE_ID,
                json={"breakaway_torque_mnm": 9999.0, "stem_leak": False})
    client.post("/api/v1/devices/%s/valve" % DEVICE_ID, json={"command": "close"})
    mitigation = client.get("/api/v1/insurer/portfolio").json()["mitigation"]
    assert mitigation["valve_refusals_by_reason"] == {
        "no_install_probe": 1, "breakaway_torque_above_threshold": 1}
    assert mitigation["valve_commands_by_status"] == {"refused": 2}


def test_never_reporting_says_why_it_cannot_be_computed(client):
    """Contract §7 asks for pct_never_reporting; this DB cannot know it."""
    _device(client)
    nr = client.get("/api/v1/insurer/portfolio").json()["portfolio"]["never_reporting"]
    assert nr["value"] is None
    assert "shipped-units roster" in nr["why_not_computed"]


def test_install_health_and_shadow_counts(client):
    _device(client, device_id="leak-0001", dev_eui=DEV_EUI, f_cnt=1, install_verified=True)
    _device(client, device_id="leak-0002", dev_eui="70B3D57ED0000002", f_cnt=1,
            install_verified=False)
    _device(client, device_id="leak-0003", dev_eui="70B3D57ED0000003", f_cnt=1,
            install_verified=True, tamper=True)
    p = client.get("/api/v1/insurer/portfolio").json()["portfolio"]
    assert p["devices_known"] == 3
    assert p["install_verified"] == 2
    assert p["install_unverified"] == 1
    assert p["tamper_flagged"] == 1
    assert p["in_shadow_mode"] == 3
    assert p["alerting_enabled"] == 0
    assert "risk 4" in p["install_verification_note"]


def test_report_declares_which_detector_produced_it(client):
    detection = client.get("/api/v1/insurer/portfolio").json()["detection"]
    assert detection["detector_implementation"] in ("detection/detector.py",
                                                    "backend-fallback")
    assert "NOT time-to-detection" in detection["detection_latency_definition"]


def test_openids_block_claims_no_conformance(client):
    schema = client.get("/api/v1/insurer/portfolio").json()["schema"]
    assert schema["name"].startswith("openIDS Homeowners v1.0")
    assert schema["conformance"].startswith("none claimed")
    assert "dormant" in schema["note"]


def test_surrogate_agreement_is_null_until_one_exists(client):
    block = client.get("/api/v1/insurer/portfolio").json()["surrogate_metric_agreement"]
    assert block["on_file"] is None
    assert len(block["surrogates_reported"]) >= 4
