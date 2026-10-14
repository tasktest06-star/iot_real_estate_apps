# Interface contract — v1

**This file is the single source of truth.** Four components are built in parallel against it:
firmware, codec, simulator, detection, backend. If you think the contract is wrong, say so in your
report — **do not silently deviate**, because something else is already built against it.

Dates/versions: authored 2026-10-14. Target runtimes on the build box:
**Python 3.9.25** (so: no `X | Y` unions at runtime, no `match`; `list[str]` annotations are fine),
**Node 24**, no gcc, no Docker, no sqlite3 CLI (Python's `sqlite3` module is present).

---

## 1. Uplink payload — 8 bytes, LoRaWAN FPort 1

Byte layout, big-endian. This matches the codec published in `../../05-open-source-stack.md`, so
**do not change it without updating that document too.**

| Offset | Size | Field | Encoding |
|---|---|---|---|
| 0 | 2 | `pipe_c` | int16, centi-degrees C (−327.68 … 327.67) |
| 2 | 2 | `ambient_c` | int16, centi-degrees C |
| 4 | 2 | `battery_mv` | uint16, millivolts |
| 6 | 1 | `flags` | bit0 `install_verified`, bit1 `tamper`, bit2 `cold_warning`, bits3-7 reserved (0) |
| 7 | 1 | `uplink_reason` | 0 heartbeat, 1 threshold, 2 boot, 3 downlink-command response |

`uplink_reason = 3` means **"this frame answers a downlink command"** — which covers both an
install-probe result and the acknowledgement of a valve command, because an install probe *is*
requested by downlink command 4. One value, one meaning. (Amendment A2.)

**Why 8 bytes:** keeps a single uplink inside the DR0 payload limit at the edge of US915 coverage.
**There is deliberately no sequence number in the payload** — the LoRaWAN network server supplies
`f_cnt`, and duplicating it on-air wastes a scarce byte. Dedup and loss accounting use `f_cnt`.

`delta_c` is **derived**, never transmitted: `delta_c = pipe_c - ambient_c`. It is the actual signal.

## 2. Downlink payload — 4 bytes, FPort 2

| Offset | Size | Field | Encoding |
|---|---|---|---|
| 0 | 1 | `command` | 0 nop, 1 valve_close, 2 valve_open, 3 set_heartbeat, 4 request_install_probe |
| 1 | 1 | `arg` | for `set_heartbeat`: minutes (1–240). Else 0 |
| 2 | 2 | `token` | uint16 echo token; the device mirrors it in the next uplink's `uplink_reason=3` frame |

## 3. Decoded payload JSON (codec output)

```json
{ "pipe_c": 11.25, "ambient_c": 18.40, "delta_c": -7.15,
  "battery_mv": 3410,
  "flags": { "install_verified": true, "tamper": false, "cold_warning": false },
  "uplink_reason": "heartbeat" }
```

`uplink_reason` is the **string** name, not the integer. Unknown values decode to
`"reserved_<n>"` rather than throwing — a decoder that throws takes the whole webhook down.

## 4. Ingest envelope — The Things Stack v3 webhook shape

The simulator emits this and the backend accepts it, because this is what a real LNS posts. Only
the fields below are required; extra fields must be ignored, not rejected.

```json
{
  "end_device_ids": { "device_id": "leak-0001", "dev_eui": "70B3D57ED0000001" },
  "received_at": "2026-10-14T03:00:00Z",
  "uplink_message": {
    "f_port": 1,
    "f_cnt": 42,
    "frm_payload": "BGUHMA1SAQA=",
    "rx_metadata": [ { "gateway_ids": { "gateway_id": "gw-a" }, "rssi": -112, "snr": 2.5 } ],
    "settings": { "data_rate": { "lora": { "spreading_factor": 10, "bandwidth": 125000 } } }
  }
}
```

- `frm_payload` is **base64 of the 8 raw bytes**. The backend decodes it server-side; it must not
  trust a `decoded_payload` supplied by the caller.
- That example is a real worked vector, consistent with §1 and §3:
  `BGUHMA1SAQA=` $\rightarrow$ `04 65 07 30 0D 52 01 00` $\rightarrow$ pipe 11.25 °C, ambient
  18.40 °C, **delta −7.15 °C**, 3410 mV, `install_verified`, heartbeat. Use it as a test fixture.
- Duplicate `(dev_eui, f_cnt)` is **idempotent** — accept, do not double-count, return the same
  result.

## 5. Detector interface — pure functions, stdlib only

`detection/detector.py`. **No numpy, no scipy, no sklearn.** Reason: this algorithm has to fit in
single-digit kB on a Cortex-M4 (see `../../08-roadmap.md` §4 "no NN on the node before P2"), so the
prototype is written the way the firmware will be — integer-friendly, bounded memory, no
allocation in the hot path.

```python
@dataclasses.dataclass
class Sample:
    ts_s: int            # unix seconds
    pipe_c: float
    ambient_c: float
    battery_mv: int
    install_verified: bool
    tamper: bool

@dataclasses.dataclass
class Decision:
    verdict: str         # see below
    confidence: int      # 0..100
    reason: str          # short human string, shown to an operator
    delta_c: float
    baseline_c: float    # current learned baseline, for plotting

VERDICTS = ("quiet", "watch", "leak_suspected", "freeze_risk",
            "sensor_fault", "install_unverified")

def new_state(device_id: str) -> DetectorState: ...
def update(state: DetectorState, s: Sample) -> Decision: ...   # pure w.r.t. I/O; mutates state
```

**Required behaviours** (each needs a test):
1. A persistent negative `delta_c` sustained beyond a dwell window $\Rightarrow$ `leak_suspected`.
   Brief excursions from normal appliance use must **not** trigger — this is the nuisance problem.
2. `install_verified == False` $\Rightarrow$ `install_unverified`, and **never** a leak verdict.
   An unverified install is indistinguishable from a sensor fault and must not be counted.
3. `tamper` or physically implausible readings (pipe/ambient out of −40…80 °C, or a step change
   faster than thermal mass allows) $\Rightarrow$ `sensor_fault`, not a leak.
4. `pipe_c` below a freeze threshold $\Rightarrow$ `freeze_risk` (this is application A4, free upside
   on the same sensor).
5. The baseline adapts per device — housing stock varies, so a fixed global threshold is wrong.
6. Bounded memory: the state must hold a fixed-size window, not a growing list.

## 6. Simulator output — labelled JSONL

`simulator/` emits one JSON object per line: the §4 envelope plus a `_label` block that the backend
**ignores** and the evaluation harness uses as ground truth.

```json
{ "...envelope...": "...", "_label": { "scenario": "slow_leak", "is_true_event": true,
                                       "event_start_s": 1760000000, "device_id": "leak-0001" } }
```

Required scenarios (ground truth for the P1 gate): `quiet`, `appliance_use`, `shower_morning`,
`slow_leak`, `fast_leak`, `freeze_event`, `sensor_fault_stuck`, `sensor_fault_noisy`,
`install_unverified`, `cold_basement_winter`.

## 7. Evaluation metrics — must mirror the roadmap's P1 gate

`detection/evaluate.py` reports, and `make e2e` prints:

- **`nuisance_alerts_per_device_year`** — the P1 exit gate is **< 0.5**
- `true_event_recall`, `precision`
- `median_time_to_detection_s` on true events
- `pct_never_reporting` (devices with zero uplinks after day 7 — risk 4 in the register)

## 8. Backend REST API — `/api/v1`

| Method | Path | Purpose |
|---|---|---|
| POST | `/uplink` | TTS webhook ingest. Idempotent on `(dev_eui, f_cnt)` |
| GET | `/devices` | fleet list with last-seen, battery, verdict |
| GET | `/devices/{device_id}` | detail + recent samples |
| GET | `/alerts?status=` | `shadow` \| `open` \| `acked` \| `nuisance` |
| POST | `/alerts/{id}/ack` | body `{"was_nuisance": bool, "note": str}` — the one-click "this was nothing" from risk 2 |
| POST | `/devices/{device_id}/valve` | body `{"command":"close"\|"open"}`. **Must refuse when the device is not armed** |
| GET | `/insurer/portfolio` | loss-avoided report, openIDS-flavoured |
| GET | `/healthz` | liveness |

**Shadow mode is mandatory and default.** A device has `alerting_enabled` (default **false**).
While false, alerts are written with `status="shadow"` and no notification is emitted. This is
standing rule 3 from `../../08-roadmap.md` §0 and it is not optional.

**Valve safety, from `../../05-open-source-stack.md` §5.3 — fail-as-is, never fail-closed:**
- A device is armed only if an install probe recorded `breakaway_torque_ok` and no stem leak.
- `POST /valve` on an unarmed device returns **409** with a reason. It does not "try anyway".
- Every command is persisted with its outcome; a command with no confirming uplink becomes
  `unconfirmed`, never silently `success`.

## 9. Database — SQLite via stdlib `sqlite3`

Tables: `devices`, `uplinks`, `alerts`, `valve_commands`, `nuisance_feedback`.
Schema lives in `backend/app/schema.sql` and is applied idempotently at startup.
`uplinks` has `UNIQUE(dev_eui, f_cnt)` — that is what makes ingest idempotent.

## 10. Conventions every component must follow

- **Python 3.9.** Run `python3 -m pytest` from the repo root; tests live beside their code.
- **No network access at test time.** Tests must pass offline.
- **Stdlib only** for `detection/` and `simulator/`. `backend/` may use FastAPI/uvicorn/httpx.
- **Node codec has zero runtime dependencies** and tests with the built-in `node:test`.
- Every module gets a docstring saying what it is and what it deliberately does *not* do.
- Comment the *why*, not the *what*. The pack's documents are the rationale — cite them by filename
  and section when a decision traces back to one.
- No placeholder code, no `TODO`, no `pass  # implement me`. If something cannot work here (gcc is
  absent, Docker is absent), say so in that component's README and make the limitation explicit.

---

## 11. Amendments — v1.0 → v1.1 (2026-10-14)

Raised by the firmware implementation and resolved here. The ones marked **doc-only** change no
code; the one deferred item is recorded rather than quietly fixed, because four components are
already built against v1.0 and a silent payload change would desynchronise them.

**A1 — §2's echo token cannot be echoed. Doc-only.**
All eight §1 bytes are allocated and the flags byte has five reserved bits, not sixteen, so there is
nowhere to mirror a uint16 token. **Resolution:** the device stores the token and the correlation is
done on `(dev_eui, f_cnt)` ordering — the mechanism §1 already mandates (no sequence number on air,
because the network server supplies `f_cnt`) and §9 already indexes with a `UNIQUE` constraint. The
token stays in the downlink for the device's own idempotency, and is not expected back on the uplink.

**A2 — `uplink_reason = 3` was overloaded. Doc-only, §1 reworded above.**
§1 called it "install-probe result" while §2 required it as the ack for every command. These are the
same thing: an install probe is requested by downlink command 4. Renamed to "downlink-command
response". No new enum value, so no decoder changes.

**A3 — §5 defined no verdict precedence. Doc-only; settled.**
The firmware and the Python detector independently chose the same order, which is good evidence it
is the natural one. It is now normative:

> `install_unverified` > `sensor_fault` > `freeze_risk` > `leak_suspected` > `watch` > `quiet`

The safety reasoning runs downward: an unverified install is indistinguishable from a sensor fault,
so it must mask everything; a faulted sensor must never produce a leak verdict; and freeze outranks
leak because the mitigation differs (freeze wants heat and an open valve, a leak wants it shut).

**A4 — `confidence` was undefined. Doc-only.**
It is **confidence in the stated verdict**, 0–100 — so a confidently quiet device reports a *high*
number. It is explicitly **not** a probability of a leak; reading it that way inverts the scale on
every non-leak verdict.

**A5 — §3 and §4's examples were different samples, and §4's was physically impossible. Fixed.**
The old `frm_payload` decoded to ambient 0.01 °C with a **+12.00 °C** delta — no room is at freezing,
and no plumbing produces a pipe 12 degrees warmer than its surroundings. §4 now carries
`BGUHMA1SAQA=`, which decodes to precisely §3's worked example.

**A6 — §1 carries no valve state. DEFERRED to payload v2, with a documented consequence.**
§8 requires the backend to refuse `POST /valve` on an unarmed device, but an uplink gives it no way
to observe that the valve has since gone `V_FAULT` or `V_REFUSED`, so a stale device can still look
armed. Two of the five reserved flag bits (bits 3–4: 0 unknown, 1 open, 2 closed, 3 fault/refused)
would fix it, and that is the v2 change.

**Why this is tolerable in v1 rather than urgent:** the failure is in the safe direction. The backend
issues the close, the device refuses it locally — the shutoff decision is local by design (§8, and
`04-connectivity.md` §4.2) — and §8 already requires a command with no confirming uplink to be
recorded `unconfirmed`, **never silently `success`**. So the degraded behaviour is "the operator is
told the command was not confirmed", which is correct, not "the operator is told the water is off
when it is not". Do not fix this by making the backend optimistic.
