# Prototype — clamp-on leak sensor → LoRaWAN → detection → insurer API

A working end-to-end vertical slice of the wedge recommended in [the briefing pack](../README.md):
application **A1** (clamp-on thermal leak sensor), **A6** (valve control via a third-party certified
valve) and **A8** (the insurer-facing risk/loss-avoided product).

```bash
make venv          # .venv + pinned backend deps
make test          # 312 tests: 289 pytest + 23 node:test
make e2e           # boot the API, stream a simulated fleet through it, assert 7 invariants
make run           # serve the API + dashboard on http://127.0.0.1:8000
```

## What runs, end to end

```
 firmware/          simulator/           backend/                    dashboard/
 Zephyr C     ──►   labelled      ──►    FastAPI + SQLite      ──►   static HTML
 (not built         synthetic            ingest → detect →           fleet, alerts,
  here, see          fleet, TTS          alert lifecycle →           portfolio
  below)             webhook JSON        valve arming → report
                          │                      │
                     codec/  ◄── both decode ───►│
                     Node, 0 deps    §1 bytes    │
                                                 ▼
                                            detection/
                                       detector + eval harness
                                        (stdlib only, MCU-shaped)
```

Every component is built against one document — [`contracts/INTERFACES.md`](contracts/INTERFACES.md)
— which pins the 8-byte payload, the TTS webhook envelope, the detector signature, the REST surface
and the DB schema. Four components were built in parallel against it; the contract's §11 records
the six defects that surfaced during implementation and how each was resolved.

| Component | Tests | Notes |
|---|---|---|
| `firmware/` | 4 C suites, **never executed** | Zephyr app, nRF52840+SX1262 and STM32WLE5. **No compiler on this box** — see below |
| `codec/` | 23 (`node --test`) | LoRaWAN codec in TTS device-repository shape, zero dependencies |
| `simulator/` | 59 | 10 labelled scenarios, stdlib only, reproducible by seed |
| `detection/` | 130 | the detector + the harness that scores the P1 gate |
| `backend/` | 100 | FastAPI + stdlib `sqlite3`, idempotent ingest, shadow mode, valve arming |
| **total** | **312 passing** | |

## Measured result

40 devices × 30 days, seed 7 — 107,624 samples, 2.96 device-years of install-verified exposure:

| | |
|---|---|
| **`nuisance_alerts_per_device_year`** | **0.3383 — PASS** (P1 gate: < 0.50) |
| precision | 0.857 |
| leak recall | 0.250 (1 of 4 episodes) |
| freeze recall | 9 of 31 (29%) — **known limitation** |
| burst time-to-detection | 6.17 h median |
| `pct_never_reporting` | 5.0% |

[`detection/README.md`](detection/README.md) carries the trade-off sweep behind the freeze number,
the slow-leak weakness, and a continuity-based latency fix that was tried and **rejected because it
broke the gate**. The leak figures rest on n=4 episodes against a synthetic corpus whose realism is
an assumption, not a result — which is precisely why the roadmap puts P1 in real homes across a real
winter.

## The safety invariants, and where they are enforced

These are not comments; `make e2e` fails the build if any regress.

1. **Ingest is idempotent** on `(dev_eui, f_cnt)`. A replayed uplink returns the same status and is
   not double-counted. The payload is decoded **server-side** — a webhook is untrusted input, so a
   caller-supplied `decoded_payload` is ignored.
2. **Shadow mode is the default.** `devices.alerting_enabled` is false, alerts are written
   `status='shadow'`, and nothing notifies. No customer-visible alert until the nuisance rate is
   measured — standing rule 3 of [`08-roadmap.md`](../08-roadmap.md) §0.
3. **A valve command on an unarmed device returns 409** with a reason, and does not "try anyway".
   Arming requires a passed install probe: breakaway torque under threshold, no post-cycle stem
   leak, no cycle timeout.
4. **Fail-as-is, never fail-closed.** A valve that shuts on battery death or a firmware fault causes
   the freeze damage the product exists to prevent. Nothing actuates on fault, reset, brown-out or
   watchdog, and the firmware tests assert this against fake-actuator *stroke counters* rather than
   returned state — "never actuates" is a claim about current reaching a solenoid, not about an enum.
5. **The shutoff decision is local, never cloud-arbitrated.** LoRaWAN Class A opens a receive window
   only after an uplink, so a cloud-issued close waits for the next heartbeat — 15 minutes of
   flooding at a 15-minute interval ([`04-connectivity.md`](../04-connectivity.md) §4.2). The cloud
   confirms and reports; it does not decide.
6. **A command with no confirming uplink is `unconfirmed`, never silently `success`.**
7. **The portfolio report states what it does not claim.** Escape-of-water frequency is ~1.5 claims
   per 100 house-years, so proving a 30–50% reduction needs tens of thousands of house-years. The
   response carries surrogate metrics and an explicit disclaimer field instead of a fabricated
   "claims reduced by X%".

## What is NOT production-ready, precisely

Being blunt, because "production-ready prototype" is a contradiction worth naming:

- **The firmware has never been compiled.** There is no `gcc`, `arm-none-eabi-gcc`, `cmake`, `west`
  or Zephyr tree on this machine. The source is structurally correct and reviewable, the pure logic
  is deliberately free of Zephyr includes so it is host-testable once a toolchain exists, and
  [`firmware/README.md`](firmware/README.md) carries the `west` commands plus a ledger of what is
  verified versus assumed. Two Kconfig/devicetree names *were* resolved against upstream Zephyr
  (`CONFIG_LORAWAN_REGION_US915` is correct; the sensor is `ti,tmp11x` / `CONFIG_TMP11X`, not
  `tmp117`). The C tests have never run and no claim is made that they pass.
- **`Dockerfile` and `docker-compose.yml` have never been built** — no Docker here. Both say so in
  their headers. `make run` and `make e2e` are the paths actually exercised.
- **The corpus is synthetic.** No real pipe, no real basement, no real winter.
- **No auth, no TLS, no rate limiting, no multi-tenancy.** SQLite single-writer. Fine for a pilot
  on one VPS, not for a carrier integration — and the carrier security review is 6–18 months of
  long-lead work per [`07-compliance-and-certification.md`](../07-compliance-and-certification.md).
- **Nothing here is certified.** FCC Part 15, the NRTL question and the IAPMO scope determination
  are unchanged by any amount of code, and they are the real cash constraint.

## Layout

```
contracts/INTERFACES.md   the single source of truth (+ §11 amendments)
firmware/                 Zephyr app, 2 board targets, 4 unrun C test suites
codec/                    Node LoRaWAN codec, zero deps
simulator/                labelled synthetic fleet, 10 scenarios, stdlib
detection/                detector + evaluation harness, stdlib
backend/                  FastAPI + sqlite3
dashboard/                single static HTML file, no build step, no CDN
scripts/e2e.sh            the 7 acceptance assertions
Makefile                  venv / test / sim / eval / run / e2e / clean
```
