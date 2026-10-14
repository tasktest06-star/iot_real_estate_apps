"""FastAPI application: the routes of contracts/INTERFACES.md §8.

    POST /api/v1/uplink                     TTS webhook, idempotent on (dev_eui, f_cnt)
    GET  /api/v1/devices                    fleet list
    GET  /api/v1/devices/{device_id}        detail + recent samples
    GET  /api/v1/alerts?status=             shadow | open | acked | nuisance
    POST /api/v1/alerts/{id}/ack            {"was_nuisance": bool, "note": str}
    POST /api/v1/devices/{device_id}/valve  {"command": "close"|"open"} -- 409 if not armed
    GET  /api/v1/insurer/portfolio          loss-avoided report
    GET  /api/v1/healthz                    liveness

Three endpoints exist that contract §8 does not list, because §8 as written
cannot be implemented without them. They are additions, not deviations -- no
listed endpoint changed shape. See backend/README.md:

    POST /api/v1/devices/{device_id}/install_probe   records the arming evidence
    POST /api/v1/devices/{device_id}/alerting        leaves/enters shadow mode
    GET  /                                           serves dashboard/index.html

What this module deliberately does NOT do: no authentication, no rate limiting,
no TLS. A real TTS webhook is authenticated with a shared secret header and the
dashboard needs a login before it holds a real insurer's data; both are out of
scope for a prototype that runs on localhost, and saying so is better than a
decorative token check. Nothing here should face the internet.
"""

import pathlib
import sqlite3
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import Body, FastAPI, HTTPException, Path, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from . import alerts as alerts_mod
from . import codec, db, detector_bridge, ingest, insurer, valve

API_PREFIX = "/api/v1"

# A stated parameter, not a state-of-charge model: 08-roadmap.md §8 risk 7 says
# measure rather than model, and Li-SOCl2 has a flat discharge curve until it
# collapses, so millivolts are a cliff detector and nothing more.
BATTERY_LOW_MV = 3000

# prototype/backend/app/main.py -> prototype/dashboard/index.html
_DASHBOARD = pathlib.Path(__file__).resolve().parents[2] / "dashboard" / "index.html"

RECENT_SAMPLE_LIMIT = 120


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()           # contract §9: schema applied idempotently at startup
    detector_bridge.impl() # resolve the detector once, so /healthz can report it
    yield


app = FastAPI(
    title="LeakWatch backend (prototype)",
    version="1",
    description="Property-IoT leak detection backend. Shadow mode by default; "
                "valve commands fail-as-is.",
    lifespan=lifespan,
)


# --------------------------------------------------------------------------- #
# request bodies
# --------------------------------------------------------------------------- #
class AckBody(BaseModel):
    was_nuisance: bool = Field(..., description="True = 'this was nothing' (risk 2)")
    note: Optional[str] = None
    actor: Optional[str] = None


class ValveBody(BaseModel):
    command: str = Field(..., description='"close" or "open"')
    requested_by: Optional[str] = None


class InstallProbeBody(BaseModel):
    breakaway_torque_mnm: float = Field(..., ge=0)
    stem_leak: bool = False
    cycle_time_ms: Optional[int] = Field(None, ge=0)
    note: Optional[str] = None


class AlertingBody(BaseModel):
    enabled: bool
    # Required on purpose. Leaving shadow mode breaks standing rule 3 of
    # 08-roadmap.md §0 unless the nuisance rate has been measured, so the
    # justification is recorded on the device row with the change.
    reason: str = Field(..., min_length=3)
    actor: Optional[str] = None


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _resolve(conn: sqlite3.Connection, device_id: str) -> Dict[str, Any]:
    """Look a device up by device_id, then by DevEUI. 404 if neither matches."""
    row = conn.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,)).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM devices WHERE dev_eui = ?",
                           (device_id.strip().upper(),)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={
            "error": "unknown_device",
            "device_id": device_id,
            "hint": "a device appears here after its first uplink or install probe",
        })
    return db.row_to_dict(row)


def _device_json(conn: sqlite3.Connection, d: Dict[str, Any], now: int) -> Dict[str, Any]:
    armed, reason_code, reason = valve.arming_check(d)
    live = conn.execute(
        "SELECT status, COUNT(*) FROM alerts WHERE dev_eui = ? AND status IN ('shadow','open')"
        " GROUP BY status", (d["dev_eui"],)).fetchall()
    live_counts = {r[0]: int(r[1]) for r in live}
    last_seen = d.get("last_seen_ts")
    return {
        "device_id": d["device_id"],
        "dev_eui": d["dev_eui"],
        "last_seen_at": db.iso(last_seen),
        "seconds_since_last_seen": (now - int(last_seen)) if last_seen else None,
        "first_seen_at": db.iso(d.get("first_seen_ts")),
        "uplink_count": d.get("uplink_count"),
        "last_f_cnt": d.get("last_f_cnt"),
        "battery_mv": d.get("last_battery_mv"),
        "battery_low": (d.get("last_battery_mv") is not None
                        and int(d["last_battery_mv"]) < BATTERY_LOW_MV),
        "battery_low_threshold_mv": BATTERY_LOW_MV,
        "battery_note": "millivolts only. Li-SOCl2 discharge is flat then cliffs, so this "
                        "is not a state of charge (08-roadmap.md §8 risk 7).",
        "pipe_c": d.get("last_pipe_c"),
        "ambient_c": d.get("last_ambient_c"),
        "delta_c": d.get("last_delta_c"),
        "baseline_c": d.get("last_baseline_c"),
        "rssi": d.get("last_rssi"),
        "snr": d.get("last_snr"),
        "verdict": d.get("last_verdict"),
        "confidence": d.get("last_confidence"),
        "verdict_reason": d.get("last_reason"),
        "install_verified": bool(d.get("install_verified")),
        "tamper": bool(d.get("tamper")),
        "cold_warning": bool(d.get("cold_warning")),
        "alerting_enabled": bool(d.get("alerting_enabled")),
        "shadow_mode": not bool(d.get("alerting_enabled")),
        "alerting_change_reason": d.get("alerting_change_reason"),
        "valve_state": d.get("valve_state"),
        "armed": armed,
        "arming_reason_code": reason_code,
        "arming_reason": reason,
        "install_probe_at": db.iso(d.get("install_probe_ts")),
        "breakaway_torque_mnm": d.get("breakaway_torque_mnm"),
        "stem_leak": None if d.get("stem_leak") is None else bool(d.get("stem_leak")),
        "open_alerts": live_counts.get("open", 0),
        "shadow_alerts": live_counts.get("shadow", 0),
    }


# --------------------------------------------------------------------------- #
# routes
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard() -> Any:
    """Serve the single-file dashboard. No build step, no CDN (it works offline)."""
    if not _DASHBOARD.exists():
        raise HTTPException(status_code=404,
                            detail={"error": "dashboard_missing", "path": str(_DASHBOARD)})
    return FileResponse(str(_DASHBOARD), media_type="text/html")


@app.get(API_PREFIX + "/healthz")
@app.get("/healthz", include_in_schema=False)
def healthz() -> Dict[str, Any]:
    """Liveness, plus which detector is live -- see app/detector_bridge.py."""
    with db.session() as conn:
        devices = conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
        uplinks = conn.execute("SELECT COUNT(*) FROM uplinks").fetchone()[0]
    return {
        "status": "ok",
        "db": str(db.db_path()),
        "devices": int(devices),
        "uplinks": int(uplinks),
        "detector": detector_bridge.source(),
        "detector_note": detector_bridge.note(),
        "detector_is_fallback": detector_bridge.using_fallback(),
        "shadow_mode_default": True,
    }


@app.post(API_PREFIX + "/uplink")
def post_uplink(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """TTS v3 webhook ingest (contract §4). Idempotent on `(dev_eui, f_cnt)`.

    `frm_payload` is decoded here; a `decoded_payload` in the body is ignored.
    A malformed envelope is 400 and not retried; anything storable is stored.
    """
    db.ensure_db()
    try:
        with db.session() as conn:
            return ingest.handle_uplink(conn, payload)
    except ingest.EnvelopeError as exc:
        # 400, not 500: the LNS should stop retrying a permanently broken body.
        raise HTTPException(status_code=400, detail={
            "error": "malformed_envelope", "reason": str(exc),
            "expected": "TTS v3 uplink envelope, contract §4",
        })


@app.get(API_PREFIX + "/devices")
def get_devices() -> Dict[str, Any]:
    db.ensure_db()
    now = db.now_s()
    with db.session() as conn:
        valve.expire_stale(conn, now_s=now)
        rows = conn.execute(
            "SELECT * FROM devices ORDER BY last_seen_ts DESC NULLS LAST, device_id"
        ).fetchall()
        devices = [_device_json(conn, db.row_to_dict(r), now) for r in rows]
    return {
        "generated_at": db.iso(now),
        "count": len(devices),
        "devices": devices,
        "note": "a device is in shadow mode until alerting is explicitly enabled "
                "(08-roadmap.md §0 standing rule 3).",
    }


@app.get(API_PREFIX + "/devices/{device_id}")
def get_device(
    device_id: str = Path(...),
    samples: int = Query(RECENT_SAMPLE_LIMIT, ge=1, le=1000),
) -> Dict[str, Any]:
    db.ensure_db()
    now = db.now_s()
    with db.session() as conn:
        valve.expire_stale(conn, now_s=now)
        device = _resolve(conn, device_id)
        rows = conn.execute(
            "SELECT * FROM uplinks WHERE dev_eui = ? ORDER BY received_ts DESC, id DESC"
            " LIMIT ?", (device["dev_eui"], int(samples))).fetchall()
        recent = [{
            "uplink_id": r["id"],
            "f_cnt": r["f_cnt"],
            "received_at": db.iso(r["received_ts"]),
            "pipe_c": r["pipe_c"],
            "ambient_c": r["ambient_c"],
            "delta_c": r["delta_c"],
            "baseline_c": r["baseline_c"],
            "battery_mv": r["battery_mv"],
            "uplink_reason": r["uplink_reason"],
            "install_verified": None if r["install_verified"] is None else bool(r["install_verified"]),
            "tamper": None if r["tamper"] is None else bool(r["tamper"]),
            "verdict": r["verdict"],
            "confidence": r["confidence"],
            "reason": r["reason"],
            "rssi": r["rssi"],
            "snr": r["snr"],
            "decode_ok": bool(r["decode_ok"]),
            "decode_error": r["decode_error"],
            "detector_error": r["detector_error"],
        } for r in reversed(rows)]   # oldest first, so the sparkline reads left to right
        out = _device_json(conn, device, now)
        out["recent_samples"] = recent
        out["alerts"] = alerts_mod.list_alerts(conn, device_id=device["device_id"], limit=50)
        out["valve_commands"] = valve.list_commands(conn, dev_eui=device["dev_eui"], limit=50)
    return out


@app.get(API_PREFIX + "/alerts")
def get_alerts(
    status: Optional[str] = Query(None, description="shadow | open | acked | nuisance"),
    device_id: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
) -> Dict[str, Any]:
    db.ensure_db()
    if status is not None and status not in alerts_mod.ALERT_STATUSES:
        raise HTTPException(status_code=400, detail={
            "error": "unknown_status", "status": status,
            "allowed": list(alerts_mod.ALERT_STATUSES),
        })
    with db.session() as conn:
        rows = alerts_mod.list_alerts(conn, status=status, device_id=device_id, limit=limit)
    return {
        "count": len(rows),
        "status_filter": status,
        "alerts": rows,
        "note": "status='shadow' means the alert was never shown to a customer: no "
                "notification was emitted (contract §8, standing rule 3).",
    }


@app.post(API_PREFIX + "/alerts/{alert_id}/ack")
def post_ack(alert_id: int = Path(..., ge=1), body: AckBody = Body(...)) -> Dict[str, Any]:
    """The one-click "this was nothing" of 08-roadmap.md §8 risk 2."""
    db.ensure_db()
    with db.session() as conn:
        updated = alerts_mod.ack(conn, alert_id, bool(body.was_nuisance), body.note, body.actor)
        if updated is None:
            raise HTTPException(status_code=404,
                                detail={"error": "unknown_alert", "alert_id": alert_id})
    return {
        "alert": updated,
        "recorded": "nuisance" if body.was_nuisance else "confirmed_true_event",
        "feeds": "nuisance_alerts_per_device_year in GET /api/v1/insurer/portfolio",
    }


@app.post(API_PREFIX + "/devices/{device_id}/valve")
def post_valve(
    device_id: str = Path(...),
    body: ValveBody = Body(...),
) -> Any:
    """Issue a valve command, or refuse with 409. Never "tries anyway"."""
    db.ensure_db()
    if body.command not in valve.VALID_COMMANDS:
        raise HTTPException(status_code=400, detail={
            "error": "unknown_command", "command": body.command,
            "allowed": list(valve.VALID_COMMANDS),
        })
    with db.session() as conn:
        device = _resolve(conn, device_id)
        result = valve.issue_command(conn, device, body.command, body.requested_by)
    if result["status"] == "refused":
        # 409 Conflict with the reason, per contract §8. The refusal is already
        # persisted in valve_commands, which is why this returns rather than
        # raising before the write.
        return JSONResponse(status_code=409, content={
            "error": "device_not_armed",
            "device_id": result["device_id"],
            "command": result["command"],
            "status": "refused",
            "reason_code": result["reason_code"],
            "reason": result["reason"],
            "action_taken": "none",
            "fail_mode": "fail-as-is, never fail-closed (05-open-source-stack.md §5.3)",
            "command_id": result["command_id"],
            "remedy": "run an install probe that records breakaway_torque_ok and no stem "
                      "leak: POST /api/v1/devices/{device_id}/install_probe",
        })
    return result


@app.post(API_PREFIX + "/devices/{device_id}/install_probe")
def post_install_probe(
    device_id: str = Path(...),
    body: InstallProbeBody = Body(...),
) -> Dict[str, Any]:
    """Record an install-probe result; this is what can arm a valve.

    An addition to contract §8: the arming rule §8 mandates needs probe data,
    the 8-byte uplink of §1 has no room for a torque reading, and §8 lists no
    endpoint to carry it. The probe is performed by the installer's BLE
    commissioning app (05-open-source-stack.md §5.3).
    """
    db.ensure_db()
    with db.session() as conn:
        device = _resolve(conn, device_id)
        return valve.record_install_probe(
            conn, device, body.breakaway_torque_mnm, body.stem_leak,
            body.cycle_time_ms, body.note)


@app.post(API_PREFIX + "/devices/{device_id}/alerting")
def post_alerting(
    device_id: str = Path(...),
    body: AlertingBody = Body(...),
) -> Dict[str, Any]:
    """Enable or disable customer-visible alerting for one device.

    An addition to contract §8, which mandates shadow mode but gives no way out
    of it. Deliberately per-device and deliberately requires a written reason:
    standing rule 3 of 08-roadmap.md §0 is a product rule, and a one-device
    exception with a recorded justification is auditable in a way that a global
    flag is not. Alerts already raised in shadow mode are not promoted -- they
    were never shown to anyone and retroactively notifying would be worse.
    """
    db.ensure_db()
    now = db.now_s()
    with db.session() as conn:
        device = _resolve(conn, device_id)
        conn.execute(
            "UPDATE devices SET alerting_enabled = ?, alerting_changed_ts = ?,"
            " alerting_change_reason = ?, updated_ts = ? WHERE dev_eui = ?",
            (1 if body.enabled else 0, now,
             "%s: %s" % (body.actor or "unattributed", body.reason), now, device["dev_eui"]))
        updated = _device_json(conn, _resolve(conn, device["device_id"]), now)
    return {
        "device": updated,
        "warning": ("customer-visible alerting is ON for this device. Standing rule 3 "
                    "(08-roadmap.md §0) says this is only defensible once the nuisance "
                    "rate is measured: check GET /api/v1/insurer/portfolio -> "
                    "detection.p1_gate first.") if body.enabled else
                   "device returned to shadow mode: no notifications will be emitted.",
        "existing_shadow_alerts": "left as shadow; not retroactively notified",
    }


@app.get(API_PREFIX + "/insurer/portfolio")
def get_portfolio(window_days: int = Query(90, ge=1, le=3650)) -> Dict[str, Any]:
    """Loss-avoided report. Surrogate metrics only -- see app/insurer.py."""
    db.ensure_db()
    with db.session() as conn:
        return insurer.portfolio_report(conn, window_days=window_days)


@app.get(API_PREFIX + "/notifications", include_in_schema=False)
def get_notifications() -> Dict[str, Any]:
    """The in-process notification sink, so shadow mode is inspectable by hand.

    Not part of contract §8. It exists because "no notification was emitted" is
    the kind of claim that should be checkable with curl, not just in a test.
    """
    sink = alerts_mod.notifications()
    return {
        "count": len(sink),
        "notifications": sink,
        "note": "shadow-mode alerts never appear here. In-process and non-durable: a "
                "restart empties it.",
    }


@app.get(API_PREFIX + "/codec/encode", include_in_schema=False)
def encode_helper(
    pipe_c: float = Query(...),
    ambient_c: float = Query(...),
    battery_mv: int = Query(3600, ge=0, le=65535),
    install_verified: bool = Query(False),
    tamper: bool = Query(False),
    cold_warning: bool = Query(False),
    uplink_reason: int = Query(0, ge=0, le=255),
) -> Dict[str, Any]:
    """Build a contract §1 `frm_payload` for hand-testing with curl.

    Not part of contract §8 and not an ingest path -- it is the encoder from
    app/codec.py exposed so the README's curl examples do not need a Python
    one-liner. It writes nothing.
    """
    try:
        b64 = codec.encode_frm_payload(pipe_c, ambient_c, battery_mv, install_verified,
                                       tamper, cold_warning, uplink_reason)
    except codec.PayloadError as exc:
        raise HTTPException(status_code=400, detail={"error": "unencodable", "reason": str(exc)})
    return {"frm_payload": b64, "decoded": codec.decode_frm_payload(b64)}
