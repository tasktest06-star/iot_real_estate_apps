"""Alert lifecycle, shadow mode, and nuisance feedback.

Shadow mode is standing rule 3 of 08-roadmap.md §0 -- "No customer-visible
alert until the nuisance rate is measured" -- and contract §8 repeats that it is
mandatory and the default. It is implemented in exactly one place, here:

    status = "open"   and a notification is emitted   iff devices.alerting_enabled
    status = "shadow" and nothing whatsoever is sent  otherwise

`devices.alerting_enabled` defaults to 0 in schema.sql, so a device that was
merely seen on the network is in shadow mode; leaving shadow mode is an explicit
POST with a written reason (see app/main.py) that is recorded on the device row.

What this module deliberately does NOT do:
  * no SMS/email/webhook/push integration. `notify()` appends to an in-process
    sink. A real deployment swaps the sink for a transport; the point of the
    seam is that the shadow-mode test asserts on an empty sink;
  * no auto-resolution of alerts. An alert is closed by a human acking it,
    because the ack *is* the measurement (risk 2's leading indicator);
  * no re-notification or escalation ladder -- one notification per alert.
"""

import sqlite3
from collections import deque
from typing import Any, Dict, List, Optional

from . import db, detector_bridge

# In-process notification sink. Bounded so a long-running prototype cannot grow
# without limit. Tests assert that a shadow alert leaves this untouched.
NOTIFICATION_SINK_MAX = 500
NOTIFICATIONS = deque(maxlen=NOTIFICATION_SINK_MAX)  # type: deque

ALERT_STATUSES = ("shadow", "open", "acked", "nuisance")
LIVE_STATUSES = ("shadow", "open")


def clear_notifications() -> None:
    NOTIFICATIONS.clear()


def notifications() -> List[Dict[str, Any]]:
    return list(NOTIFICATIONS)


def _notify(alert_id: int, device_id: str, verdict: str, reason: str, ts: int) -> str:
    """Emit the one customer-visible notification for an alert.

    Called only for status="open". There is no code path from a shadow alert to
    this function: the caller checks `alerting_enabled` and this function is not
    reached otherwise.
    """
    channel = "in-process-sink"
    NOTIFICATIONS.append({
        "alert_id": alert_id,
        "device_id": device_id,
        "verdict": verdict,
        "reason": reason,
        "channel": channel,
        "at": db.iso(ts),
    })
    return channel


def is_alerting_verdict(verdict: Optional[str]) -> bool:
    return verdict in detector_bridge.ALERTING_VERDICTS


def record_decision(
    conn: sqlite3.Connection,
    device: Dict[str, Any],
    uplink_id: Optional[int],
    decision: Dict[str, Any],
    received_ts: int,
    first_anomalous_ts: Optional[int],
) -> Optional[Dict[str, Any]]:
    """Create or extend an alert for a detector decision.

    Returns the alert row as a dict, or None if the verdict is not alertable.
    One live alert per (device, verdict): a 30-minute leak on a 15-minute
    heartbeat is one alert, not three. Counting every uplink as an alert would
    inflate the nuisance rate that the P1 gate is measured on (contract §7).
    """
    verdict = decision.get("verdict")
    if not is_alerting_verdict(verdict):
        return None

    dev_eui = device["dev_eui"]
    existing = conn.execute(
        "SELECT * FROM alerts WHERE dev_eui = ? AND verdict = ? AND status IN (?, ?)"
        " ORDER BY id DESC LIMIT 1",
        (dev_eui, verdict) + LIVE_STATUSES,
    ).fetchone()

    if existing is not None:
        conn.execute(
            "UPDATE alerts SET last_uplink_ts = ?, uplink_count = uplink_count + 1,"
            " confidence = ?, reason = ?, delta_c = ?, baseline_c = ? WHERE id = ?",
            (received_ts, decision.get("confidence"), decision.get("reason"),
             decision.get("delta_c"), decision.get("baseline_c"), existing["id"]),
        )
        row = conn.execute("SELECT * FROM alerts WHERE id = ?", (existing["id"],)).fetchone()
        out = db.row_to_dict(row)
        out["_created"] = False
        return out

    alerting_enabled = bool(device.get("alerting_enabled"))
    status = "open" if alerting_enabled else "shadow"
    latency = None
    if first_anomalous_ts is not None:
        latency = max(0, int(received_ts) - int(first_anomalous_ts))

    cur = conn.execute(
        "INSERT INTO alerts (dev_eui, device_id, verdict, status, raised_in_shadow,"
        " confidence, reason, delta_c, baseline_c, raised_ts, raised_uplink_id,"
        " first_anomalous_ts, detection_latency_s, last_uplink_ts, uplink_count, created_ts)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)",
        (dev_eui, device.get("device_id"), verdict, status, 0 if alerting_enabled else 1,
         decision.get("confidence"), decision.get("reason"), decision.get("delta_c"),
         decision.get("baseline_c"), received_ts, uplink_id, first_anomalous_ts, latency,
         received_ts, received_ts),
    )
    alert_id = int(cur.lastrowid)

    if alerting_enabled:
        channel = _notify(alert_id, device.get("device_id") or dev_eui, verdict,
                          decision.get("reason") or "", received_ts)
        conn.execute("UPDATE alerts SET notified_ts = ?, notify_channel = ? WHERE id = ?",
                     (received_ts, channel, alert_id))

    row = conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    out = db.row_to_dict(row)
    out["_created"] = True
    return out


def list_alerts(
    conn: sqlite3.Connection,
    status: Optional[str] = None,
    device_id: Optional[str] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM alerts"
    where = []  # type: List[str]
    args = []   # type: List[Any]
    if status:
        where.append("status = ?")
        args.append(status)
    if device_id:
        where.append("(device_id = ? OR dev_eui = ?)")
        args.extend([device_id, device_id])
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY raised_ts DESC, id DESC LIMIT ?"
    args.append(int(limit))
    return [to_json(db.row_to_dict(r)) for r in conn.execute(sql, args).fetchall()]


def get_alert(conn: sqlite3.Connection, alert_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM alerts WHERE id = ?", (int(alert_id),)).fetchone()
    return db.row_to_dict(row)


def ack(
    conn: sqlite3.Connection,
    alert_id: int,
    was_nuisance: bool,
    note: Optional[str] = None,
    actor: Optional[str] = None,
    now_s: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Record the one-click "this was nothing" (08-roadmap.md §8 risk 2).

    `was_nuisance=False` is feedback too -- it is a confirmed true event, the
    numerator of the P1 gate's "at least one documented true event". Both
    answers append a `nuisance_feedback` row, so a later correction never
    erases the operator's first answer.

    Shadow alerts can be acked. That is the whole point of shadow mode: the
    nuisance rate is measured on decisions that were never shown to a customer.
    """
    row = conn.execute("SELECT * FROM alerts WHERE id = ?", (int(alert_id),)).fetchone()
    if row is None:
        return None
    ts = db.now_s() if now_s is None else int(now_s)
    status = "nuisance" if was_nuisance else "acked"
    conn.execute(
        "UPDATE alerts SET status = ?, acked_ts = ?, acked_by = ?, was_nuisance = ?,"
        " note = ? WHERE id = ?",
        (status, ts, actor, 1 if was_nuisance else 0, note, int(alert_id)),
    )
    conn.execute(
        "INSERT INTO nuisance_feedback (alert_id, dev_eui, device_id, was_nuisance, note,"
        " actor, created_ts) VALUES (?,?,?,?,?,?,?)",
        (int(alert_id), row["dev_eui"], row["device_id"], 1 if was_nuisance else 0,
         note, actor, ts),
    )
    updated = conn.execute("SELECT * FROM alerts WHERE id = ?", (int(alert_id),)).fetchone()
    return to_json(db.row_to_dict(updated))


def to_json(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Alert row -> API shape. `notified` is explicit so shadow is visible."""
    if row is None:
        return None
    return {
        "id": row["id"],
        "device_id": row["device_id"],
        "dev_eui": row["dev_eui"],
        "verdict": row["verdict"],
        "status": row["status"],
        "raised_in_shadow": bool(row["raised_in_shadow"]),
        "confidence": row["confidence"],
        "reason": row["reason"],
        "delta_c": row["delta_c"],
        "baseline_c": row["baseline_c"],
        "raised_at": db.iso(row["raised_ts"]),
        "last_uplink_at": db.iso(row["last_uplink_ts"]),
        "uplink_count": row["uplink_count"],
        "detection_latency_s": row["detection_latency_s"],
        "notified": row["notified_ts"] is not None,
        "notified_at": db.iso(row["notified_ts"]),
        "notify_channel": row["notify_channel"],
        "acked_at": db.iso(row["acked_ts"]),
        "acked_by": row["acked_by"],
        "was_nuisance": None if row["was_nuisance"] is None else bool(row["was_nuisance"]),
        "note": row["note"],
    }
