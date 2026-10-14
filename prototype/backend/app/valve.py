"""Valve commands, the arming check, and command outcomes.

Transcribed from `arm_or_refuse()` in 05-open-source-stack.md §5.3 and restated
by contract §8: **fail-as-is, never fail-closed.**

    A device is armed only if an install probe recorded breakaway_torque_ok and
    no stem leak. POST /valve on an unarmed device returns 409 with a reason.
    It does not "try anyway".

Three things follow, and all three are enforced here rather than at the HTTP
layer so they cannot be bypassed:

1. **No probe means not armed.** A device that has merely been seen on the
   network has `install_probe_ts IS NULL`, and every valve command for it is
   refused. Arming is opt-in evidence, not a default.
2. **A refusal is a persisted outcome, not an error page.** The refused command
   is written to `valve_commands` with its reason, because 08-roadmap.md §8
   risk 9's leading indicator is "any unit arming above the breakaway-torque
   threshold" -- you cannot watch that indicator if refusals are not recorded.
3. **A command with no confirming uplink becomes `unconfirmed`, never
   `success`.** LoRaWAN Class A only opens a receive window after an uplink
   (04 §4.2), so a cloud-issued close waits for the next heartbeat. The cloud
   confirms and reports; it does not arbitrate the close.

What this module deliberately does NOT do:
  * it does not talk to a LoRaWAN network server. The downlink bytes are built
    per contract §2 and persisted; actually enqueueing them is a TTS Downlink
    API call that needs a real NS and an API key, and faking it would be
    exactly the "silently success" failure the contract forbids. `status` is
    `pending` from the moment the command is accepted;
  * it does not close a valve on a detector verdict. The shutoff decision is
    local to the valve (05 §5.3); nothing in this backend can auto-close;
  * it cannot verify the echo token -- see CONFIRMATION below.

CONFIRMATION, and a contract defect: contract §2 says the device mirrors the
uint16 token "in the next uplink's `uplink_reason=3` frame", but the §1 uplink
payload is 8 bytes of pipe/ambient/battery/flags/reason with **no token field**,
so the echo cannot be read. This implementation therefore treats the next
`uplink_reason == install_probe_result` frame inside the confirm window as the
confirmation and reports `token_echo_verified: false` on every command. Fixing
it needs a payload change (a 10-byte uplink, or a second FPort for acks), which
is a contract decision, not a backend one.
"""

import base64
import os
import random
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from . import codec, db

VALID_COMMANDS = ("close", "open")
_COMMAND_TO_DOWNLINK = {"close": "valve_close", "open": "valve_open"}

# Parameters, not measurements. 08-roadmap.md §3 P0 funds a "20-valve mechanical
# spike" at $0.5-1k; that spike is what sets the real refuse threshold. Until it
# is run these are conservative defaults, overridable by environment variable so
# the pilot can change them without a code edit.
TORQUE_REFUSE_THRESHOLD_MNM = float(os.environ.get("LEAKWATCH_TORQUE_REFUSE_MNM", "1500"))
CYCLE_TIMEOUT_MS = int(os.environ.get("LEAKWATCH_CYCLE_TIMEOUT_MS", "10000"))

# Two 15-minute heartbeats. A Class A device cannot be reached until it uplinks,
# so a shorter window would mark healthy devices unconfirmed.
CONFIRM_WINDOW_S = int(os.environ.get("LEAKWATCH_CONFIRM_WINDOW_S", "1800"))

_token_rng = random.SystemRandom()


def _new_token() -> int:
    """Non-zero uint16, so a zero echo is distinguishable from "no echo"."""
    return _token_rng.randrange(1, 0x10000)


def arming_check(device: Dict[str, Any]) -> Tuple[bool, Optional[str], str]:
    """(armed, reason_code, human_reason). Mirrors arm_or_refuse() in 05 §5.3."""
    if device.get("install_probe_ts") is None:
        return (False, "no_install_probe",
                "no install probe on record: an unprobed valve is not armed, and the "
                "backend will not try a shutoff it cannot promise (05 §5.3)")
    if not device.get("breakaway_torque_ok"):
        return (False, "breakaway_torque_above_threshold",
                "install probe measured breakaway torque %s mNm, above the %.0f mNm refuse "
                "threshold: the actuator may stall mid-travel (08 §8 risk 9)"
                % (device.get("breakaway_torque_mnm"), TORQUE_REFUSE_THRESHOLD_MNM))
    if device.get("stem_leak"):
        return (False, "stem_leak_detected",
                "install probe detected a stem leak after cycling: operating the valve "
                "would make the leak it is meant to stop")
    cycle_ms = device.get("cycle_time_ms")
    if cycle_ms is not None and int(cycle_ms) > CYCLE_TIMEOUT_MS:
        return (False, "cycle_time_above_timeout",
                "install probe cycle time %d ms exceeds the %d ms timeout: valve fault"
                % (int(cycle_ms), CYCLE_TIMEOUT_MS))
    return (True, None, "install probe passed: breakaway torque %s mNm under %.0f mNm, no "
                        "stem leak" % (device.get("breakaway_torque_mnm"),
                                       TORQUE_REFUSE_THRESHOLD_MNM))


def record_install_probe(
    conn: sqlite3.Connection,
    device: Dict[str, Any],
    breakaway_torque_mnm: float,
    stem_leak: bool,
    cycle_time_ms: Optional[int] = None,
    note: Optional[str] = None,
    now_s: Optional[int] = None,
) -> Dict[str, Any]:
    """Store an install-probe result and recompute the arming state.

    The probe arrives out of band (the installer's BLE commissioning app --
    05 §5.3 "measure the valve at install time"), because the 8-byte uplink of
    contract §1 has no room for a torque reading. Contract §8 has no endpoint
    for this, which is a gap: without it the arming rule it mandates can never
    be satisfied. See backend/README.md "Extensions beyond contract §8".
    """
    ts = db.now_s() if now_s is None else int(now_s)
    torque = float(breakaway_torque_mnm)
    torque_ok = torque <= TORQUE_REFUSE_THRESHOLD_MNM
    cycle = None if cycle_time_ms is None else int(cycle_time_ms)
    if not torque_ok or stem_leak:
        valve_state = "refused"
    elif cycle is not None and cycle > CYCLE_TIMEOUT_MS:
        valve_state = "fault"
    else:
        valve_state = "open"  # arm_or_refuse() returns V_OPEN: as-found, not actuated
    conn.execute(
        "UPDATE devices SET install_probe_ts = ?, breakaway_torque_mnm = ?,"
        " breakaway_torque_ok = ?, stem_leak = ?, cycle_time_ms = ?, probe_note = ?,"
        " valve_state = ?, updated_ts = ? WHERE dev_eui = ?",
        (ts, torque, 1 if torque_ok else 0, 1 if stem_leak else 0, cycle, note,
         valve_state, ts, device["dev_eui"]),
    )
    row = conn.execute("SELECT * FROM devices WHERE dev_eui = ?",
                       (device["dev_eui"],)).fetchone()
    updated = db.row_to_dict(row)
    armed, reason_code, reason = arming_check(updated)
    return {
        "device_id": updated["device_id"],
        "dev_eui": updated["dev_eui"],
        "probe_at": db.iso(ts),
        "breakaway_torque_mnm": torque,
        "breakaway_torque_ok": torque_ok,
        "stem_leak": bool(stem_leak),
        "cycle_time_ms": cycle,
        "torque_refuse_threshold_mnm": TORQUE_REFUSE_THRESHOLD_MNM,
        "valve_state": valve_state,
        "armed": armed,
        "arming_reason_code": reason_code,
        "arming_reason": reason,
    }


def issue_command(
    conn: sqlite3.Connection,
    device: Dict[str, Any],
    command: str,
    requested_by: Optional[str] = None,
    now_s: Optional[int] = None,
) -> Dict[str, Any]:
    """Refuse or queue a valve command. Always persists a `valve_commands` row.

    Returns a dict whose "status" is "refused" or "pending"; the HTTP layer
    turns "refused" into 409. Nothing is ever returned as "success".
    """
    if command not in VALID_COMMANDS:
        raise ValueError("command must be one of %s" % (VALID_COMMANDS,))
    ts = db.now_s() if now_s is None else int(now_s)
    armed, reason_code, reason = arming_check(device)

    if not armed:
        cur = conn.execute(
            "INSERT INTO valve_commands (dev_eui, device_id, command, status, reason_code,"
            " reason, requested_by, issued_ts) VALUES (?,?,?,'refused',?,?,?,?)",
            (device["dev_eui"], device.get("device_id"), command, reason_code, reason,
             requested_by, ts),
        )
        return {
            "command_id": int(cur.lastrowid),
            "device_id": device.get("device_id"),
            "command": command,
            "status": "refused",
            "reason_code": reason_code,
            "reason": reason,
            "action_taken": "none",
            "fail_mode": "fail-as-is",
            "issued_at": db.iso(ts),
            "armed": False,
        }

    token = _new_token()
    payload = codec.encode_downlink(_COMMAND_TO_DOWNLINK[command], 0, token)
    payload_b64 = base64.b64encode(payload).decode("ascii")
    deadline = ts + CONFIRM_WINDOW_S
    cur = conn.execute(
        "INSERT INTO valve_commands (dev_eui, device_id, command, status, reason, token,"
        " f_port, payload_b64, requested_by, issued_ts, confirm_deadline_ts)"
        " VALUES (?,?,?,'pending',?,?,?,?,?,?,?)",
        (device["dev_eui"], device.get("device_id"), command,
         "queued for the next Class A receive window; awaiting a confirming uplink",
         token, codec.DOWNLINK_FPORT, payload_b64, requested_by, ts, deadline),
    )
    return {
        "command_id": int(cur.lastrowid),
        "device_id": device.get("device_id"),
        "command": command,
        "status": "pending",
        "reason_code": None,
        "reason": "queued for the next Class A receive window; awaiting a confirming uplink",
        "armed": True,
        "arming_reason": reason,
        "f_port": codec.DOWNLINK_FPORT,
        "payload_b64": payload_b64,
        "token": token,
        "token_echo_verified": False,
        "token_echo_note": "contract §1 has no token field in the uplink payload, so the "
                           "§2 echo cannot be verified; confirmation uses the next "
                           "install_probe_result frame instead",
        "issued_at": db.iso(ts),
        "confirm_deadline_at": db.iso(deadline),
        "confirm_window_s": CONFIRM_WINDOW_S,
        "arbitration": "the cloud confirms and reports; it does not arbitrate the close "
                       "(05 §5.3)",
    }


def expire_stale(conn: sqlite3.Connection, now_s: Optional[int] = None) -> int:
    """Pending commands past their deadline become `unconfirmed`. Never `success`.

    Called on ingest and on every read of device/portfolio state, so a stale
    pending command cannot be presented as if it were still in flight.
    """
    ts = db.now_s() if now_s is None else int(now_s)
    cur = conn.execute(
        "UPDATE valve_commands SET status = 'unconfirmed', reason = ? "
        "WHERE status = 'pending' AND confirm_deadline_ts IS NOT NULL"
        " AND confirm_deadline_ts < ?",
        ("no confirming uplink within %d s: outcome unknown, reported as unconfirmed "
         "rather than success" % CONFIRM_WINDOW_S, ts),
    )
    return int(cur.rowcount or 0)


def confirm_from_uplink(
    conn: sqlite3.Connection,
    dev_eui: str,
    uplink_id: Optional[int],
    uplink_reason: Optional[str],
    received_ts: int,
) -> Optional[int]:
    """Confirm the oldest pending command for a device from a confirming uplink.

    Only an `install_probe_result` frame (contract §1 uplink_reason 3) counts.
    A heartbeat proves the device is alive, not that the valve moved -- treating
    it as confirmation would be the "silently success" the contract forbids.
    """
    if uplink_reason != "install_probe_result":
        return None
    row = conn.execute(
        "SELECT * FROM valve_commands WHERE dev_eui = ? AND status = 'pending'"
        " AND issued_ts <= ? ORDER BY issued_ts ASC, id ASC LIMIT 1",
        (dev_eui, int(received_ts)),
    ).fetchone()
    if row is None:
        return None
    if row["confirm_deadline_ts"] is not None and int(received_ts) > int(row["confirm_deadline_ts"]):
        return None
    conn.execute(
        "UPDATE valve_commands SET status = 'confirmed', confirmed_ts = ?,"
        " confirming_uplink_id = ?, reason = ? WHERE id = ?",
        (int(received_ts), uplink_id,
         "confirmed by an install_probe_result uplink (echo token unverifiable, see "
         "app/valve.py)", row["id"]),
    )
    new_state = "closed" if row["command"] == "close" else "open"
    conn.execute("UPDATE devices SET valve_state = ?, updated_ts = ? WHERE dev_eui = ?",
                 (new_state, int(received_ts), dev_eui))
    return int(row["id"])


def list_commands(
    conn: sqlite3.Connection,
    dev_eui: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM valve_commands"
    args = []  # type: List[Any]
    if dev_eui:
        sql += " WHERE dev_eui = ?"
        args.append(dev_eui)
    sql += " ORDER BY issued_ts DESC, id DESC LIMIT ?"
    args.append(int(limit))
    return [to_json(db.row_to_dict(r)) for r in conn.execute(sql, args).fetchall()]


def to_json(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "command_id": row["id"],
        "device_id": row["device_id"],
        "dev_eui": row["dev_eui"],
        "command": row["command"],
        "status": row["status"],
        "reason_code": row["reason_code"],
        "reason": row["reason"],
        "token": row["token"],
        "f_port": row["f_port"],
        "payload_b64": row["payload_b64"],
        "requested_by": row["requested_by"],
        "issued_at": db.iso(row["issued_ts"]),
        "confirm_deadline_at": db.iso(row["confirm_deadline_ts"]),
        "confirmed_at": db.iso(row["confirmed_ts"]),
        "confirming_uplink_id": row["confirming_uplink_id"],
    }
