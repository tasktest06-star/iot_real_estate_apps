# Backend — ingest, alerting, valve safety, insurer reporting

FastAPI + stdlib `sqlite3`. Implements `../contracts/INTERFACES.md` §1 (payload decode),
§4 (TTS webhook), §8 (REST API) and §9 (database), and serves the single-file dashboard in
`../dashboard/` at `/`.

Three behaviours are not negotiable and are tested directly, because the pilot's credibility rests
on them rather than on any feature:

| Behaviour | Where | Why |
|---|---|---|
| **Shadow mode by default** — `devices.alerting_enabled` defaults to 0; while 0, alerts are written `status='shadow'` and **no notification is emitted** | `app/alerts.py` | `08-roadmap.md` §0 standing rule 3: no customer-visible alert until the nuisance rate is measured. An insurer's trust is spent once |
| **Fail-as-is valve** — a command on an unarmed device is **409 + reason**, persisted, never attempted; a command with no confirming uplink becomes `unconfirmed`, never `success` | `app/valve.py` | `05-open-source-stack.md` §5.3 `arm_or_refuse()`; `08-roadmap.md` §8 risk 9. A false shutoff in a commercial building is a business-interruption claim against you |
| **No frequency claim** — the portfolio report carries a `not_claimed` block and has no "claims reduced by X%" field | `app/insurer.py` | `08-roadmap.md` §8 risk 3 and §9's "explicitly refuse to promise" column. Escape of water is ~1.5 claims per 100 house-years, so significance needs tens of thousands of house-years |

## Run it

```bash
cd prototype
./.venv/bin/python -m pytest backend/ -q                       # 99 tests
./.venv/bin/python -m uvicorn backend.app.main:app --port 8000
# dashboard:  http://127.0.0.1:8000/
# OpenAPI:    http://127.0.0.1:8000/docs
```

The database is `backend/leakwatch.db` (ignored by `backend/.gitignore`). Override with
`LEAKWATCH_DB=/path/to.db` or `LEAK_DB_PATH=/path/to.db` — both are honoured, because
`scripts/e2e.sh`, the `Dockerfile` and `docker-compose.yml` set the second name and the contract
names neither. The schema is applied idempotently at startup; there is no migration
tool on purpose, so if `schema.sql` changes, startup refuses a stale file with a message telling
you to delete it.

### Module map

| File | What it is | What it deliberately does *not* do |
|---|---|---|
| `app/codec.py` | contract §1/§2 payload codec | no detection, no thresholds; never throws on an unknown `uplink_reason` |
| `app/db.py` | connections, schema application, timestamp parsing | no pool, no ORM, no migrations |
| `app/schema.sql` | the five tables of contract §9 | no retention policy — `uplinks.raw_b64` is kept forever (P0 raw retention) |
| `app/ingest.py` | webhook: decode, upsert, idempotency, detector | ignores the simulator's `_label` and any caller-supplied `decoded_payload` |
| `app/detector_bridge.py` | loads `../detection/detector.py` (contract §5) | no second copy of the algorithm; no tuning of its thresholds |
| `app/alerts.py` | alert lifecycle, shadow mode, nuisance feedback | no SMS/email/webhook transport; no auto-resolution; no escalation ladder |
| `app/valve.py` | arming check, command outcomes | does not talk to an LNS, and cannot auto-close a valve |
| `app/insurer.py` | portfolio / loss-avoided report | no premium-credit maths, no claims matching, no risk score |
| `app/main.py` | routes | no auth, no rate limiting, no TLS |

## The API as built

All under `/api/v1`. `/healthz` is also served at the root for probes that expect it there.

| Method | Path | Notes |
|---|---|---|
| `POST` | `/uplink` | TTS v3 envelope. Idempotent on `(dev_eui, f_cnt)`. `200` with `duplicate: true` on a repeat; `400` on a malformed envelope (so the LNS stops retrying) |
| `GET` | `/devices` | fleet: last-seen, battery mV, verdict, Δ°C, install state, arming state, shadow/alerting, live alert counts |
| `GET` | `/devices/{device_id}` | detail + `recent_samples` (oldest first, `?samples=` 1–1000) + alerts + valve commands. Resolves by `device_id` **or** DevEUI |
| `GET` | `/alerts?status=` | `shadow` \| `open` \| `acked` \| `nuisance`; also `?device_id=`, `?limit=` |
| `POST` | `/alerts/{id}/ack` | `{"was_nuisance": bool, "note": str, "actor": str}` → status `nuisance` or `acked`, plus a row in `nuisance_feedback` |
| `POST` | `/devices/{device_id}/valve` | `{"command": "close"\|"open"}` → `200` `pending` if armed, **`409`** `refused` with `reason_code` if not |
| `GET` | `/insurer/portfolio` | `?window_days=` (default 90). Surrogate metrics + `not_claimed` |
| `GET` | `/healthz` | liveness, and **which detector is live** |

### Additions to contract §8

Three endpoints §8 does not list. No listed endpoint changed shape; these exist because §8 as
written cannot be implemented without them. Flagged here because the contract says deviations must
be reported, not made silently.

| Method | Path | Why it has to exist |
|---|---|---|
| `POST` | `/devices/{device_id}/install_probe` | §8 says a device is armed only if "an install probe recorded `breakaway_torque_ok` and no stem leak", but the §1 uplink is 8 bytes with no torque field and §8 lists no endpoint to carry one. The probe comes from the installer's BLE commissioning app (05 §5.3). Body: `{"breakaway_torque_mnm": float, "stem_leak": bool, "cycle_time_ms": int?, "note": str?}` |
| `POST` | `/devices/{device_id}/alerting` | §8 mandates shadow mode but gives no way out of it. Per-device, and `reason` is **required** so leaving shadow mode is recorded on the device row |
| `GET` | `/` | serves `../dashboard/index.html` |

Two debug routes are excluded from the OpenAPI schema and are not part of the contract:
`GET /api/v1/notifications` (the in-process notification sink, so "no notification was emitted" is
checkable with curl) and `GET /api/v1/codec/encode` (builds a `frm_payload` for hand-testing; it
writes nothing).

## Walk it with curl

```bash
# 1. build the 8 bytes yourself: pipe 11.25 C, ambient 18.40 C, 3410 mV, install_verified, heartbeat
python3 -c "import base64,struct;print(base64.b64encode(struct.pack('>hhHBB',1125,1840,3410,1,0)).decode())"
# BGUHMA1SAQA=

# 2. post it (note the lie in decoded_payload: the backend ignores it and decodes the bytes)
curl -s -X POST localhost:8000/api/v1/uplink -H 'content-type: application/json' -d '{
  "end_device_ids": {"device_id":"leak-0001","dev_eui":"70B3D57ED0000001"},
  "uplink_message": {"f_port":1,"f_cnt":42,"frm_payload":"BGUHMA1SAQA=",
                     "decoded_payload":{"pipe_c":-40.0,"flags":{"tamper":true}},
                     "received_at":"2026-10-14T04:07:29Z"}}' | jq '{duplicate, decoded, verdict}'
# -> {"duplicate": false, "decoded": {"pipe_c": 11.25, "ambient_c": 18.4, "delta_c": -7.15, ...,
#     "flags": {"install_verified": true, "tamper": false, ...}}, "verdict": "quiet"}

# 3. post it again -> same uplink_id, same decoded payload, nothing double-counted
#    -> {"status":"accepted","duplicate":true,"uplink_id":1, ...}

# 4. refuse a valve command on a device that has never been probed
curl -s -X POST localhost:8000/api/v1/devices/leak-0001/valve \
  -H 'content-type: application/json' -d '{"command":"close"}' -o /dev/null -w '%{http_code}\n'
# -> 409   (body: reason_code "no_install_probe", action_taken "none", fail_mode "fail-as-is")

# 5. arm it, then command it
curl -s -X POST localhost:8000/api/v1/devices/leak-0001/install_probe \
  -H 'content-type: application/json' \
  -d '{"breakaway_torque_mnm":870,"stem_leak":false,"cycle_time_ms":4300}' | jq .armed   # true
curl -s -X POST localhost:8000/api/v1/devices/leak-0001/valve \
  -H 'content-type: application/json' -d '{"command":"close"}' | jq '{status, f_port, payload_b64}'
# -> {"status":"pending","f_port":2,"payload_b64":"AQDa5A=="}   never "success"

curl -s localhost:8000/api/v1/notifications | jq .count     # 0 while everything is in shadow mode
curl -s localhost:8000/api/v1/insurer/portfolio | jq .not_claimed
```

## How shadow mode works

`alerting_enabled` is a **per-device** column defaulting to 0. On a decision whose verdict is
`leak_suspected` or `freeze_risk`, `app/alerts.py` writes an alert and:

* **shadow (default):** `status='shadow'`, `raised_in_shadow=1`, `notified_ts` stays NULL, and
  `_notify()` is never called. There is no code path from a shadow alert to the notification sink.
* **enabled:** `status='open'`, one notification, `notified_ts` set. A second uplink on the same
  live alert does not re-notify.

`raised_in_shadow` is stored separately from `status` so that acking a shadow alert (which is how
the nuisance rate gets measured) does not erase the fact that nobody was told. Alerts already
raised in shadow are **not** promoted when alerting is later enabled — retroactively notifying
about a three-week-old reading is worse than both options.

One live alert per `(device, verdict)`: a 45-minute leak on a 15-minute heartbeat is one alert, not
three, because counting every uplink would inflate the P1 gate's own metric.

`sensor_fault` and `install_unverified` never raise an alert. They are data-quality states, and
`08-roadmap.md` §8 risk 4 says not to count unverified installs; they surface as fleet health in
`GET /devices` and in the portfolio report instead.

## How valve refusal works

`valve.arming_check()` is a direct transcription of `arm_or_refuse()` in 05 §5.3:

| Condition | `reason_code` | HTTP |
|---|---|---|
| no install probe on record | `no_install_probe` | 409 |
| `breakaway_torque_mnm` > `LEAKWATCH_TORQUE_REFUSE_MNM` (default 1500) | `breakaway_torque_above_threshold` | 409 |
| stem leak after cycling | `stem_leak_detected` | 409 |
| `cycle_time_ms` > `LEAKWATCH_CYCLE_TIMEOUT_MS` (default 10000) | `cycle_time_above_timeout` | 409 |
| probe passed | — | 200, `status: "pending"` |

Every outcome, including every refusal, is a row in `valve_commands` with its `reason_code` — risk
9's leading indicator is "any unit arming above the breakaway-torque threshold", and you cannot
watch that if refusals are only HTTP responses.

A `pending` command whose `confirm_deadline_ts` (issue + `LEAKWATCH_CONFIRM_WINDOW_S`, default
1800 s = two heartbeats) has passed becomes **`unconfirmed`**. The sweep runs on ingest and on every
read of device or portfolio state, so a stale command can never be displayed as in-flight. The
device's `valve_state` is left as it was: fail-as-is.

The thresholds are **parameters, not measurements**. The real refuse threshold comes from the
20-valve mechanical spike that `08-roadmap.md` §3 funds in P0.

## Limitations, stated rather than hidden

1. **No authentication, no TLS, no rate limiting.** A real TTS webhook is authenticated with a
   shared-secret header and the dashboard needs a login before it holds an insurer's data. A
   decorative token check would be worse than this sentence. Do not expose this to a network.
2. **No LNS downlink.** `POST /valve` builds and persists the contract §2 downlink bytes but does
   not enqueue them: that needs a real network server and an API key. The command sits at `pending`
   until confirmed or expired — which is exactly the honest state.
3. **The echo token cannot be verified.** Contract §2 says the device mirrors a uint16 token in the
   next `uplink_reason=3` frame, but the §1 uplink payload has no token field. Confirmation uses
   the next `install_probe_result` frame and reports `token_echo_verified: false`.
4. **`(dev_eui, f_cnt)` is only unique within a LoRaWAN session.** A rejoin resets `f_cnt`, so a
   genuinely new frame can collide with an old one and be swallowed as a duplicate. Implemented as
   the contract specifies; a collision whose bytes differ is flagged `payload_mismatch` in the
   response rather than disappearing.
5. **`pct_never_reporting` cannot be computed here.** A device row is created by its first uplink,
   so a unit that never reported is invisible. The denominator is the shipped-units roster, which
   lives in a provisioning system this prototype does not have. The report says so in the payload
   and offers `devices_silent_over_7d` instead.
6. **Detector state is in memory.** Contract §5 states are opaque objects, so after a restart a
   device's baseline is re-learned by replaying its last 200 stored frames; replayed decisions
   never raise alerts.
7. **Detection latency is not time-to-detection.** The report measures from the first non-quiet
   sample to the alert. True latency against physical event onset needs the simulator's ground
   truth, which the backend deliberately ignores (contract §6); `detection/evaluate.py` owns that.
8. **No Docker, no `sqlite3` CLI on this box.** Inspect the database with
   `./.venv/bin/python -c "import sqlite3; ..."`, or use `GET /api/v1/devices/{id}`.

## Tests

`./.venv/bin/python -m pytest backend/ -q` — 99 tests, offline, each with its own temporary
database. They drive the real app through `fastapi.testclient.TestClient`, which is an
`httpx.Client` over an ASGI transport, so routing, validation and serialisation are all exercised.

| File | Covers |
|---|---|
| `tests/test_codec.py` | contract §1/§3 vectors, int16 sign extension (−7.15 survives), the int16 rails, `reserved_<n>`, malformed payloads, §2 downlinks |
| `tests/test_ingest.py` | server-side decode, a caller-supplied `decoded_payload` being ignored, idempotency and non-double-counting, backfilled frames, nanosecond timestamps, 400 on a malformed envelope, raw retention, the UNIQUE constraint, stale-database refusal |
| `tests/test_alerts.py` | shadow alerts emit nothing (three independent assertions), enabling requires a written reason, one notification per alert, no retroactive promotion, coalescing, ack/nuisance feedback, append-only corrections |
| `tests/test_valve.py` | all four refusal reasons, refusals persisted with codes, `pending` not `success`, the `unconfirmed` sweep, confirmation only by an `install_probe_result` frame, no auto-close |
| `tests/test_insurer.py` | no frequency claim anywhere in the serialised JSON, the `not_claimed` block, nuisance rate and per-device-year maths, gate not evaluable at pilot scale, severity-model arithmetic, refusal grouping |
| `tests/test_detector_bridge.py` | contract §5 invariants against whichever detector is live, the loader falling back on a missing/broken/non-conforming detector, and the fallback's own six behaviours with the fallback forced on |
