"""TTS v3 webhook ingest: decode, device upsert, idempotency, detector run.

Contract §4. Three rules drive the whole module:

1. **The payload is decoded here, from `frm_payload`.** A webhook is untrusted
   input and anyone who can reach the URL can post one, so a `decoded_payload`
   in the body is ignored entirely -- trusting it would let a caller assert
   "pipe_c = -20" and manufacture an alert. `uplinks.raw_b64` keeps the on-air
   bytes so any decode can be re-run later (08-roadmap.md §3 P0: "raw retention
   from day one").
2. **Idempotent on `(dev_eui, f_cnt)`,** enforced by the UNIQUE constraint in
   schema.sql rather than by a read-then-write race. A duplicate returns the
   stored result and changes no counter, no baseline and no alert.
3. **Extra fields are ignored, not rejected** (contract §4). Only
   `end_device_ids.dev_eui`, `uplink_message.f_cnt` and `frm_payload` are
   required.

What this module deliberately does NOT do:
  * no `_label` handling. The simulator's ground-truth block (contract §6) is
    ignored on purpose: the backend must not be able to see the answer, or the
    evaluation in detection/evaluate.py is worthless;
  * no retry/queue. A 4xx is returned for a permanently malformed envelope so
    the LNS stops retrying it; anything the backend can store is stored and
    answered 200;
  * no detector state in the database -- see KNOWN LIMITATION below.

KNOWN LIMITATION -- f_cnt wrap and rejoin: contract §9 makes `(dev_eui, f_cnt)`
the dedup key. LoRaWAN frame counters are 16-bit on air and reset to 0 on a
rejoin, so after ~65k frames or any rejoin a *new* uplink can collide with an
old one and be swallowed as a duplicate. The contract is followed as written,
but a collision whose bytes differ from the stored frame is flagged
(`payload_mismatch`) and counted in the portfolio's data-quality block instead
of disappearing silently. A real deployment needs a session/join counter in the
key.
"""

import sqlite3
import threading
from typing import Any, Dict, List, Optional, Tuple

from . import alerts, codec, db, detector_bridge, valve

# How many stored uplinks are replayed to rebuild a device's detector state
# after a process restart. Bounded for the same reason contract §5.6 bounds the
# detector's own window: this has to stay cheap and predictable.
REPLAY_LIMIT = 200

_states_lock = threading.Lock()
_states = {}  # type: Dict[str, Any]


class EnvelopeError(ValueError):
    """The body is not a TTS v3 uplink envelope (contract §4)."""


def reset_detector_states() -> None:
    """Drop cached detector state. Used by tests and by the DB-path switch."""
    with _states_lock:
        _states.clear()


def _as_dict(value: Any, field: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise EnvelopeError("%s must be an object (contract §4)" % field)
    return value


def parse_envelope(body: Any) -> Dict[str, Any]:
    """Pull the required contract §4 fields out of the webhook body."""
    body = _as_dict(body, "body")
    ids = _as_dict(body.get("end_device_ids") or {}, "end_device_ids")
    uplink = _as_dict(body.get("uplink_message") or {}, "uplink_message")

    dev_eui = ids.get("dev_eui")
    if not isinstance(dev_eui, str) or not dev_eui.strip():
        raise EnvelopeError("end_device_ids.dev_eui is required (contract §4)")
    dev_eui = dev_eui.strip().upper()

    device_id = ids.get("device_id")
    if not isinstance(device_id, str) or not device_id.strip():
        # A DevEUI is identity; device_id is a label. Synthesise one rather than
        # dropping a frame that carries real sensor data.
        device_id = "eui-%s" % dev_eui.lower()
    device_id = device_id.strip()

    f_cnt = uplink.get("f_cnt")
    if isinstance(f_cnt, bool) or not isinstance(f_cnt, int):
        raise EnvelopeError("uplink_message.f_cnt is required and must be an integer")
    if f_cnt < 0:
        raise EnvelopeError("uplink_message.f_cnt must not be negative")

    frm_payload = uplink.get("frm_payload")
    if not isinstance(frm_payload, str):
        raise EnvelopeError("uplink_message.frm_payload is required (base64, contract §4)")

    f_port = uplink.get("f_port")
    if isinstance(f_port, bool) or not isinstance(f_port, int):
        f_port = None

    rssi = snr = gateway_id = None
    rx_meta = uplink.get("rx_metadata")
    if isinstance(rx_meta, list) and rx_meta:
        # Several gateways may receive one frame; keep the strongest, which is
        # the one that says most about coverage (risk 4's never-reporting tail).
        best = None
        for entry in rx_meta:
            if not isinstance(entry, dict):
                continue
            value = entry.get("rssi")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if best is None or value > best[0]:
                    best = (value, entry)
            elif best is None:
                best = (-999, entry)
        if best is not None:
            entry = best[1]
            raw_rssi = entry.get("rssi")
            rssi = int(raw_rssi) if isinstance(raw_rssi, (int, float)) else None
            raw_snr = entry.get("snr")
            snr = float(raw_snr) if isinstance(raw_snr, (int, float)) else None
            gw = entry.get("gateway_ids")
            if isinstance(gw, dict) and isinstance(gw.get("gateway_id"), str):
                gateway_id = gw["gateway_id"]

    sf = None
    settings = uplink.get("settings")
    if isinstance(settings, dict):
        lora = (settings.get("data_rate") or {})
        if isinstance(lora, dict):
            lora = lora.get("lora") or {}
            if isinstance(lora, dict) and isinstance(lora.get("spreading_factor"), int):
                sf = int(lora["spreading_factor"])

    return {
        "dev_eui": dev_eui,
        "device_id": device_id,
        "f_cnt": int(f_cnt),
        "f_port": f_port,
        "frm_payload": frm_payload,
        "received_at": uplink.get("received_at") or body.get("received_at"),
        "rssi": rssi,
        "snr": snr,
        "gateway_id": gateway_id,
        "spreading_factor": sf,
    }


def _get_state(conn: sqlite3.Connection, dev_eui: str, before_uplink_id: int) -> Any:
    """Detector state for a device, rebuilt from stored uplinks on a cache miss.

    Contract §5 states are opaque objects, so they are not serialisable into
    SQLite. After a restart the baseline is therefore re-learned by replaying
    the device's recent frames through `update()` and discarding the decisions.
    Replayed decisions never create alerts -- re-raising historical alerts on
    every restart would be a notification storm and would corrupt the nuisance
    rate.
    """
    with _states_lock:
        state = _states.get(dev_eui)
        if state is not None:
            return state

    state = detector_bridge.new_state(dev_eui)
    rows = conn.execute(
        "SELECT received_ts, pipe_c, ambient_c, battery_mv, install_verified, tamper"
        " FROM uplinks WHERE dev_eui = ? AND decode_ok = 1 AND id < ?"
        # Chronological, not arrival order: a detector that sees time run
        # backwards during a replay learns a nonsense baseline.
        " ORDER BY received_ts DESC, id DESC LIMIT ?",
        (dev_eui, int(before_uplink_id), REPLAY_LIMIT),
    ).fetchall()
    for row in reversed(rows):
        try:
            detector_bridge.classify(
                state, int(row["received_ts"]), float(row["pipe_c"]),
                float(row["ambient_c"]), int(row["battery_mv"] or 0),
                bool(row["install_verified"]), bool(row["tamper"]),
            )
        except Exception:
            # A replay failure must not block live ingest; the state is simply
            # less warmed up. The live call's error is reported on the uplink.
            continue
    with _states_lock:
        _states.setdefault(dev_eui, state)
        return _states[dev_eui]


def _upsert_device(
    conn: sqlite3.Connection,
    env: Dict[str, Any],
    ts: int,
) -> Tuple[Dict[str, Any], List[str]]:
    """Create or refresh the device row. New devices start in shadow mode."""
    warnings = []  # type: List[str]
    dev_eui, device_id = env["dev_eui"], env["device_id"]
    existing = conn.execute("SELECT * FROM devices WHERE dev_eui = ?", (dev_eui,)).fetchone()
    if existing is None:
        clash = conn.execute("SELECT dev_eui FROM devices WHERE device_id = ?",
                             (device_id,)).fetchone()
        if clash is not None:
            # Two DevEUIs claiming one device_id is a provisioning error. Keep
            # the data (the DevEUI is the real identity) and make it visible.
            device_id = "%s#%s" % (device_id, dev_eui[-4:])
            warnings.append(
                "device_id %r is already held by DevEUI %s; stored this device as %r"
                % (env["device_id"], clash["dev_eui"], device_id))
        conn.execute(
            "INSERT INTO devices (dev_eui, device_id, alerting_enabled, first_seen_ts,"
            " created_ts, updated_ts) VALUES (?,?,0,?,?,?)",
            (dev_eui, device_id, ts, ts, ts),
        )
    else:
        # A backfilled or out-of-order frame can be older than the first frame
        # already stored, and exposure in the portfolio report is measured from
        # first_seen_ts, so first_seen_ts only ever moves backwards.
        conn.execute(
            "UPDATE devices SET first_seen_ts = MIN(COALESCE(first_seen_ts, ?), ?)"
            " WHERE dev_eui = ?", (ts, ts, dev_eui))
        if existing["device_id"] != device_id:
            clash = conn.execute("SELECT dev_eui FROM devices WHERE device_id = ?",
                                 (device_id,)).fetchone()
            if clash is None:
                conn.execute(
                    "UPDATE devices SET device_id = ?, updated_ts = ? WHERE dev_eui = ?",
                    (device_id, ts, dev_eui))
            else:
                warnings.append(
                    "device_id %r reported by %s is already held by %s; keeping %r"
                    % (device_id, dev_eui, clash["dev_eui"], existing["device_id"]))
    row = conn.execute("SELECT * FROM devices WHERE dev_eui = ?", (dev_eui,)).fetchone()
    return db.row_to_dict(row), warnings


def handle_uplink(
    conn: sqlite3.Connection,
    body: Any,
    now_s: Optional[int] = None,
) -> Dict[str, Any]:
    """Ingest one TTS v3 uplink. Raises EnvelopeError for a malformed envelope."""
    env = parse_envelope(body)
    wall = db.now_s() if now_s is None else int(now_s)
    received_ts = db.parse_ts(env["received_at"], default=wall)
    warnings = []  # type: List[str]

    decoded = None  # type: Optional[Dict[str, Any]]
    decode_error = None  # type: Optional[str]
    if env["f_port"] is not None and env["f_port"] != codec.UPLINK_FPORT:
        decode_error = ("f_port %s is not the FPort %d uplink payload of contract §1; "
                        "stored raw, not decoded" % (env["f_port"], codec.UPLINK_FPORT))
        warnings.append(decode_error)
    else:
        try:
            decoded = codec.decode_frm_payload(env["frm_payload"])
        except codec.PayloadError as exc:
            decode_error = str(exc)
            warnings.append("payload did not decode: %s" % exc)

    # ---- idempotency gate: the UNIQUE constraint decides, atomically --------
    cur = conn.execute(
        "INSERT INTO uplinks (dev_eui, device_id, f_cnt, f_port, received_at, received_ts,"
        " raw_b64, decode_ok, decode_error, pipe_c, ambient_c, delta_c, battery_mv,"
        " install_verified, tamper, cold_warning, uplink_reason, rssi, snr, gateway_id,"
        " spreading_factor, created_ts)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT (dev_eui, f_cnt) DO NOTHING",
        (env["dev_eui"], env["device_id"], env["f_cnt"], env["f_port"], env["received_at"],
         received_ts, env["frm_payload"], 1 if decoded else 0, decode_error,
         decoded["pipe_c"] if decoded else None,
         decoded["ambient_c"] if decoded else None,
         decoded["delta_c"] if decoded else None,
         decoded["battery_mv"] if decoded else None,
         (1 if decoded["flags"]["install_verified"] else 0) if decoded else None,
         (1 if decoded["flags"]["tamper"] else 0) if decoded else None,
         (1 if decoded["flags"]["cold_warning"] else 0) if decoded else None,
         decoded["uplink_reason"] if decoded else None,
         env["rssi"], env["snr"], env["gateway_id"], env["spreading_factor"], wall),
    )
    if not cur.rowcount:
        return _duplicate_result(conn, env, warnings)

    uplink_id = int(cur.lastrowid)
    device, dev_warnings = _upsert_device(conn, env, received_ts)
    warnings.extend(dev_warnings)

    decision = None  # type: Optional[Dict[str, Any]]
    detector_error = None  # type: Optional[str]
    if decoded is not None:
        state = _get_state(conn, env["dev_eui"], uplink_id)
        try:
            decision = detector_bridge.classify(
                state, received_ts, decoded["pipe_c"], decoded["ambient_c"],
                decoded["battery_mv"], decoded["flags"]["install_verified"],
                decoded["flags"]["tamper"],
            )
        except Exception as exc:
            # The frame is kept and the failure is recorded. No verdict is
            # invented, and the webhook does not 500 into an LNS retry loop.
            detector_error = "%s: %s" % (type(exc).__name__, exc)
            warnings.append("detector raised %s; uplink stored without a verdict"
                            % type(exc).__name__)
            with _states_lock:
                _states.pop(env["dev_eui"], None)

    episode_start = device.get("anomaly_episode_start_ts")
    if decision is not None:
        if decision["verdict"] == "quiet":
            episode_start = None
        elif episode_start is None:
            episode_start = received_ts
        conn.execute(
            "UPDATE uplinks SET verdict = ?, confidence = ?, reason = ?, baseline_c = ?,"
            " detector_error = ? WHERE id = ?",
            (decision["verdict"], decision["confidence"], decision["reason"],
             decision["baseline_c"], detector_error, uplink_id),
        )
    elif detector_error is not None:
        conn.execute("UPDATE uplinks SET detector_error = ? WHERE id = ?",
                     (detector_error, uplink_id))

    # Out-of-order and backfilled frames are normal with a store-and-forward LNS.
    # They count towards uplink_count and exposure, but they must not overwrite
    # "last seen" or the denormalised latest sample -- a 3-day-old frame arriving
    # now would otherwise make a stale device look fresh and reset its verdict.
    stale_frame = (device.get("last_seen_ts") is not None
                   and received_ts < int(device["last_seen_ts"]))
    if stale_frame:
        warnings.append(
            "frame is older than this device's last seen uplink (%s): counted, but the "
            "device's latest-sample fields were left alone"
            % db.iso(device["last_seen_ts"]))
        conn.execute(
            "UPDATE devices SET uplink_count = uplink_count + 1, updated_ts = ?"
            " WHERE dev_eui = ?", (wall, env["dev_eui"]))
    else:
        conn.execute(
            "UPDATE devices SET last_seen_ts = ?, last_f_cnt = ?,"
            " uplink_count = uplink_count + 1,"
            " last_pipe_c = COALESCE(?, last_pipe_c),"
            " last_ambient_c = COALESCE(?, last_ambient_c),"
            " last_delta_c = COALESCE(?, last_delta_c),"
            " last_battery_mv = COALESCE(?, last_battery_mv),"
            " last_rssi = COALESCE(?, last_rssi), last_snr = COALESCE(?, last_snr),"
            " install_verified = COALESCE(?, install_verified),"
            " tamper = COALESCE(?, tamper), cold_warning = COALESCE(?, cold_warning),"
            " last_verdict = COALESCE(?, last_verdict),"
            " last_confidence = COALESCE(?, last_confidence),"
            " last_reason = COALESCE(?, last_reason),"
            " last_baseline_c = COALESCE(?, last_baseline_c),"
            " anomaly_episode_start_ts = ?, updated_ts = ? WHERE dev_eui = ?",
            (received_ts, env["f_cnt"],
             decoded["pipe_c"] if decoded else None,
             decoded["ambient_c"] if decoded else None,
             decoded["delta_c"] if decoded else None,
             decoded["battery_mv"] if decoded else None,
             env["rssi"], env["snr"],
             (1 if decoded["flags"]["install_verified"] else 0) if decoded else None,
             (1 if decoded["flags"]["tamper"] else 0) if decoded else None,
             (1 if decoded["flags"]["cold_warning"] else 0) if decoded else None,
             decision["verdict"] if decision else None,
             decision["confidence"] if decision else None,
             decision["reason"] if decision else None,
             decision["baseline_c"] if decision else None,
             episode_start, wall, env["dev_eui"]),
        )
    device = db.row_to_dict(
        conn.execute("SELECT * FROM devices WHERE dev_eui = ?", (env["dev_eui"],)).fetchone())

    alert = None
    if decision is not None:
        alert = alerts.record_decision(conn, device, uplink_id, decision, received_ts,
                                       episode_start)

    confirmed_command_id = valve.confirm_from_uplink(
        conn, env["dev_eui"], uplink_id,
        decoded["uplink_reason"] if decoded else None, received_ts)
    valve.expire_stale(conn, now_s=wall)

    return _result(env, uplink_id, decoded, decode_error, decision, detector_error,
                   device, alert, confirmed_command_id, duplicate=False, warnings=warnings)


def _duplicate_result(
    conn: sqlite3.Connection,
    env: Dict[str, Any],
    warnings: List[str],
) -> Dict[str, Any]:
    """Rebuild the stored result for a duplicate `(dev_eui, f_cnt)`."""
    row = conn.execute("SELECT * FROM uplinks WHERE dev_eui = ? AND f_cnt = ?",
                       (env["dev_eui"], env["f_cnt"])).fetchone()
    stored = db.row_to_dict(row)
    device = db.row_to_dict(
        conn.execute("SELECT * FROM devices WHERE dev_eui = ?", (env["dev_eui"],)).fetchone())
    if device is None:
        device = {"dev_eui": env["dev_eui"], "device_id": env["device_id"],
                  "alerting_enabled": 0}

    decoded = None
    if stored["decode_ok"]:
        try:
            decoded = codec.decode_frm_payload(stored["raw_b64"])
        except codec.PayloadError:
            decoded = None

    payload_mismatch = stored["raw_b64"] != env["frm_payload"]
    if payload_mismatch:
        # See the KNOWN LIMITATION note at the top of this module.
        warnings.append(
            "duplicate (dev_eui, f_cnt) carried different bytes (stored %s, received %s): "
            "accepted as a duplicate per contract §4, but this is what an f_cnt wrap or a "
            "rejoin looks like" % (stored["raw_b64"], env["frm_payload"]))

    decision = None
    if stored["verdict"]:
        decision = {"verdict": stored["verdict"], "confidence": stored["confidence"],
                    "reason": stored["reason"], "baseline_c": stored["baseline_c"],
                    "delta_c": stored["delta_c"]}
    alert_row = conn.execute(
        "SELECT * FROM alerts WHERE raised_uplink_id = ? ORDER BY id DESC LIMIT 1",
        (stored["id"],)).fetchone()
    alert = db.row_to_dict(alert_row)
    if alert is not None:
        alert["_created"] = False

    out = _result(env, int(stored["id"]), decoded, stored["decode_error"], decision,
                  stored["detector_error"], device, alert, None, duplicate=True,
                  warnings=warnings)
    out["payload_mismatch"] = payload_mismatch
    return out


def _result(
    env: Dict[str, Any],
    uplink_id: int,
    decoded: Optional[Dict[str, Any]],
    decode_error: Optional[str],
    decision: Optional[Dict[str, Any]],
    detector_error: Optional[str],
    device: Dict[str, Any],
    alert: Optional[Dict[str, Any]],
    confirmed_command_id: Optional[int],
    duplicate: bool,
    warnings: List[str],
) -> Dict[str, Any]:
    alerting_enabled = bool(device.get("alerting_enabled"))
    alert_out = None
    if alert is not None:
        alert_out = {
            "id": alert["id"],
            "status": alert["status"],
            "verdict": alert["verdict"],
            "created": bool(alert.get("_created")),
            "notified": alert.get("notified_ts") is not None,
            "raised_in_shadow": bool(alert.get("raised_in_shadow")),
        }
    return {
        # "accepted" for both the first and the duplicate POST: contract §4 says
        # a duplicate is accepted, not an error. `duplicate` is the only field
        # that differs, so a retrying LNS sees the same result either way.
        "status": "accepted",
        "duplicate": duplicate,
        "payload_mismatch": False,
        "device_id": device.get("device_id") or env["device_id"],
        "dev_eui": env["dev_eui"],
        "f_cnt": env["f_cnt"],
        "uplink_id": uplink_id,
        "decoded": decoded,
        "decode_error": decode_error,
        "verdict": decision["verdict"] if decision else None,
        "confidence": decision["confidence"] if decision else None,
        "reason": decision["reason"] if decision else None,
        "baseline_c": decision["baseline_c"] if decision else None,
        "detector": detector_bridge.source(),
        "detector_error": detector_error,
        "alerting_enabled": alerting_enabled,
        "shadow_mode": not alerting_enabled,
        "alert": alert_out,
        "valve_command_confirmed": confirmed_command_id,
        "warnings": warnings,
    }
