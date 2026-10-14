# detection — the leak detector and its evaluation harness

The component that is actually the product. Everything else in this prototype moves bytes around;
this decides whether to wake someone up at 3 a.m.

- `detector.py` — the thermal differential-temperature detector (contract §5)
- `evaluate.py` — scores a labelled corpus against the P1 exit gate (contract §7)
- `test_detector.py`, `test_evaluate.py` — 130 tests, stdlib `pytest` only

```bash
../.venv/bin/python -m pytest detection -q            # 130 tests
../.venv/bin/python -m simulator --devices 40 --days 30 --seed 7 --out /tmp/f.jsonl
../.venv/bin/python -m detection.evaluate --input /tmp/f.jsonl
```

## Why this is stdlib-only and integer-flavoured

No numpy, no scipy, no sklearn. That is not asceticism: this algorithm has to fit in single-digit
kilobytes on a Cortex-M4 and run five years on primary cells, so the prototype is written the way
the firmware must be — fixed-size scalar state, no allocation in the hot path, no growing buffers,
fixed-point accumulators. `DetectorState` contains no list, dict, set or buffer; the only window is
`window_bits`, a 32-bit shift register. The C twin is `../firmware/src/leak_detect.c`.

## What it does

A hidden leak draws cold ground water through the main continuously, so the pipe sits at a
*persistent* small negative `delta_c` (pipe − ambient) that intermittent household draw does not
produce. The detector learns each device's own quiescent baseline and noise floor (housing stock
varies far too much for a global threshold), then integrates **net depressed time** against an
adaptive trigger.

The single most load-bearing line is the `TRIGGER_HOLD` condition — raw depression must *stay*
above a fraction of the trigger to keep crediting dwell. Without it a 50%-duty appliance looks 75%
depressed through EWMA lag and creeps to an alert over a few hours. That was a measured false
positive, not a theoretical one.

Verdict precedence, normative per contract amendment A3:

```
install_unverified > sensor_fault > freeze_risk > leak_suspected > watch > quiet
```

An unverified install is indistinguishable from a sensor fault, so it masks everything. A faulted
sensor never yields a leak. Freeze outranks leak because the mitigation is opposite — freeze wants
heat and an *open* valve.

## Measured results

40 devices × 30 days, seed 7, 107,624 samples, 2.96 device-years of install-verified exposure:

| Metric | Value | Gate |
|---|---|---|
| **`nuisance_alerts_per_device_year`** | **0.3383** | **< 0.50 — PASS** |
| precision | 0.857 (6 true of 7 alerts) | — |
| nuisance alerts on *quiet* devices | 0.4522 / device-year | — |
| `pct_never_reporting` | 5.0% (2 of 40) | — |
| `true_event_recall` (all perils) | 0.359 (14 of 39) | — |
| &nbsp;&nbsp;↳ leak episodes only | **0.250 (1 of 4)** | — |
| &nbsp;&nbsp;↳ freeze episodes only | **9 of 31 (29%)** | — |
| median time to detection, bursts | 6.17 h across seeds (max 6.38 h) | not gated |

Nuisance alerts by scenario: `appliance_use` 1, everything else **0** — including
`cold_basement_winter` (2,049 episodes, 22,936 samples) and `quiet` (35,646 samples).

## Three limitations, stated plainly

**1. Freeze detection catches 29% of freeze events.** This is the honest cost of passing the gate,
and it is an irreducible trade-off in this corpus rather than a tuning miss. A safe
`cold_basement_winter` pipe bottoms out at +2.50 °C while a real `freeze_event` reaches −5.25 °C,
but they overlap heavily in the 2.5–4.0 °C band. The measured sweep:

| warn | dwell | nuisance/device-yr | freeze caught |
|---|---|---|---|
| 4.0 °C (the conventional 40 °F setpoint) | 2 h | 12.18 **FAIL** | 29/31 |
| 4.0 °C | 4 h | 8.80 **FAIL** | 25/31 |
| **2.0 °C** | **2 h** | **0.338 PASS** | **9/31** |
| 1.0 °C | 4 h | 0.338 PASS | 2/31 |

Above 2.0 °C the nuisance rate blows the gate; below it, recall collapses. The gate is the binding
constraint (`../../08-roadmap.md` §2), so the setpoint goes where the gate passes.
`../../02-application-portfolio.md` scores application A4 (freeze monitoring) as "free upside on the
same sensor" — **this measurement says that is optimistic.** Useful freeze detection needs a second
signal (rate-of-fall, or the ambient trend the sensor already reports), not a lower threshold.

**2. Slow leaks are the weak case: 0 of 2 `slow_leak` episodes detected.** `delta_c` shrinks as the
room cools toward mains temperature, so a device in a wide-swinging space earns a wider trigger and
a shallow leak can sit inside it. Asserted as a test
(`test_slow_leak_sensitivity_tracks_the_room_to_mains_temperature_gap`) so it stays visible rather
than being rediscovered later.

**3. The leak numbers rest on n=4 episodes.** Leak recall of 0.250 and a single-sample
time-to-detection are not a basis for any claim to a carrier. The corpus is synthetic and its
realism is an assumption, not a result — which is exactly why `../../08-roadmap.md` makes P1 about
real homes across a real freeze/thaw cycle, and why `pct_never_reporting` is measured at all.

## Burst latency, and a rejected fix

Burst time-to-detection is `DWELL_S` (6 h), not `DWELL_DEEP_S` (1.5 h), because the depth-based
escape hatch almost never fires: `fast_leak` bottoms at −10.71 °C and `appliance_use` at −10.38 °C,
so they overlap in instantaneous depth. Measured 6.17 h median, 6.38 h worst case.

A second hatch keyed on **continuity** rather than depth — an unbroken run of 12 depressed samples
earning a 2.5 h dwell — looked obviously right, since a burst never lets up and household draw
does. **Measured, it fired on quiet and normal-use fleets** and broke the quiet-fleet nuisance
tests: at a 15-minute heartbeat a normal evening genuinely produces 12 unbroken depressed samples,
and EWMA lag bridges the gaps. It is documented as rejected in `detector.py` with that reasoning.
Do not re-add it without re-running the gate.

Latency is not gated by P1; the nuisance rate is. Trading the gate for latency is the wrong trade.

## Evaluation definitions that matter

`nuisance_alerts_per_device_year` normalises by **install-verified** exposure: the sum of
inter-sample gaps, each capped at 24 h, over devices whose install probe passed. Unverified devices
are excluded from the denominator *and* can never raise a leak alert, because an unverified install
is not a measurement. On the seed-7 fleet that drops 69.12 device-days as unverified and 0 to the
24 h cap. Alerts are **rising edges** into `leak_suspected`/`freeze_risk`, deduplicated per
device+verdict over 24 h — an operator paged twice for one event has been paged once too often, and
counting every sample would flatter a latching detector enormously.

`test_evaluate.py` tests the metric arithmetic itself, because a metrics bug that flatters the
result is worse than having no metrics.
