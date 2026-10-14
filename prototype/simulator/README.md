# `simulator/` — labelled synthetic device fleet

Generates [`../contracts/INTERFACES.md`](../contracts/INTERFACES.md) §4 The Things Stack uplink
envelopes with §6 ground-truth labels, so `backend/`, `detection/` and `dashboard/` are runnable
and measurable with no hardware, no gateway and no winter.

`frm_payload` is real base64 of the real 8 bytes of §1, packed by `fleet.encode_uplink`. That is
a second, independent implementation of the §1 byte table; it and the Node codec are checked
against the same golden vectors, and `test_fleet.py` also shells out to the actual codec and
decodes a few hundred generated frames with it.

**Standard library only.** No numpy, no pandas, no third-party packages at all, per §10 — the
same constraint `detection/` is under, for the same reason: this code has to read the way
firmware on a Cortex-M4 will have to.

```sh
python3 -m simulator --devices 40 --days 30 --out fleet.jsonl
python3 -m simulator --devices 40 --days 30 --post http://127.0.0.1:8000/api/v1/uplink
python3 -m pytest simulator/ -q          # from the prototype root
```

| flag | default | notes |
|---|---|---|
| `--devices` | 40 | 5 is the minimum that still covers all ten §6 scenarios |
| `--days` | 30 | a 40×30 run is ~110k envelopes / ~63 MB / ~40 s |
| `--out` | — | path, or `-` for stdout |
| `--post` | — | POSTs each envelope with `urllib.request`; mutually exclusive with `--out` |
| `--seed` | 20261014 | identical output for identical arguments |
| `--start` | `2027-01-15T00:00:00Z` | mid-winter, where this measurement is strongest |
| `--heartbeat-min` | 15 | the firmware escalates to 5 min while a warning is latched |
| `--event-rate` | 1.0 | scales how many devices get sick; `0` gives realistic prevalence |
| `--dup-rate` | 0.012 | duplicate deliveries, to exercise §4 idempotency |
| `--no-labels` | off | drop `_label` |
| `--limit`, `--rate`, `--timeout`, `--quiet` | | |

## The signal, and why it is hard

A clamp-on sensor reads the pipe surface near where the service line enters the building; a
second sensor a few centimetres away reads the air. §1 transmits both and `delta_c` is derived.
The pipe's temperature is modelled as a first-order lag towards a flow-weighted mix of three
reservoirs — room air, the foundation wall, and the incoming mains water:

```
coupling = 0.92 * flow / (flow + 0.55)          # saturating
w_water  = coupling
w_wall   = wall_k * (1 - coupling)
target   = (1 - w_water - w_wall)*ambient + w_wall*foundation + w_water*ground_water
```

Two constants carry nearly all the behaviour.

**The time constants are asymmetric, and that is the whole problem.** With water moving, the
pipe wall tracks the water within seconds (internal forced convection, h ≈ 3 kW/m²K over
0.055 m²/m against 230 J/K/m of copper, τ ≈ 1.4 s), so what the sensor's response time actually
measures is the clamp's own mass: **τ ≈ 85–110 s**. With no flow the standing water column
dominates — ~1170 J/K per metre of water plus ~230 J/K of copper against roughly 0.5 W/K of
natural convection — so **τ ≈ 1350–1800 s**. A two-minute toilet fill therefore reaches most of
its depth quickly and then takes *forty minutes* to decay. At a 15-minute heartbeat that is
three or four samples of leak-shaped data from a toilet.

**`wall_k` is the cold-basement false positive.** An unfinished basement with the main entering
through the frost wall conducts to a foundation several degrees colder than its own air, so its
pipe sits persistently below ambient with nothing wrong at all.

Measured on a 40-device, 30-day January run (`delta_c`, °C):

| scenario | n | p05 | median | p95 | median `pipe_c` |
|---|---|---|---|---|---|
| `fast_leak` | 4178 | −6.81 | **−5.30** | −2.54 | 9.99 |
| `shower_morning` | 2629 | −6.92 | **−3.38** | −1.04 | 8.90 |
| `sensor_fault_noisy` | 4621 | −70.59 | −2.90 | 87.56 | 8.88 |
| `slow_leak` | 8524 | −4.15 | **−2.88** | −1.58 | 14.67 |
| `appliance_use` | 19961 | −6.02 | −2.61 | −0.68 | 12.05 |
| `cold_basement_winter` | 25824 | −4.06 | **−2.37** | −0.70 | 8.56 |
| `sensor_fault_stuck` | 6356 | −8.30 | −2.24 | −0.06 | 17.08 |
| `freeze_event` | 382 | −3.24 | −0.81 | 1.54 | 2.22 |
| `quiet` | 27674 | −1.54 | −0.77 | 0.05 | 16.89 |
| `install_unverified` | 9347 | −3.17 | −0.73 | 16.59 | 16.84 |

Read the four bold rows together. A slow leak sits at −2.9. A **morning shower is −3.4**, deeper
than the leak it must not be mistaken for. A washing machine's fill reaches −6.0 at its 5th
percentile, as deep as a *fast* leak. And a **cold basement with nothing whatever wrong with it
sits at −2.4 all winter**, inside the slow-leak distribution.

**Depth does not separate these classes.** What separates them is dwell time and baseline
structure, which is why §5's dwell window and §5 requirement 5's per-device adaptive baseline are
load-bearing rather than refinements. A detector that thresholds `delta_c` will alert on every
shower in the fleet, every morning, and on every unheated basement for the whole of February.

## The ten §6 scenarios

Three kinds, and keeping them apart is what makes the labels honest.

**Background — present on every healthy device, all the time.**

- **`quiet`** — no draw, no fault. Sits a few tenths below zero from the mild structural offset
  and per-channel calibration bias.
- **`appliance_use`** — taps (12–55 s), toilet fills (45–80 s), dishwashers (2–3 fill cycles of
  55–95 s with several minutes between), washing machines (3–4 cycles of 100–185 s at near-full
  flow), baths, off-peak showers, and in summer a garden hose for up to 25 minutes. Draws per day
  scale with household size and follow a two-peaked hourly profile with a deep overnight trough.
  That trough is the only window in which a healthy house is reliably quiet, and it is real
  structure a detector may use.
- **`shower_morning`** — 7–13 minutes at high flow, at a per-device hour between 06:00 and 08:30
  ± 25 minutes, on ~82% of days, on ~78% of devices. **The hardest nuisance**: long enough to
  saturate the pipe completely, and daily.

**Episodes — start at a moment, then persist.**

- **`slow_leak`** — a weeping joint or slab pinhole, flow 0.07–0.16, ramping in over 90 minutes
  and never stopping. The hard case: about −2.9 in January, roughly half that in August.
- **`fast_leak`** — a split supply line running unseen, flow 0.35–0.60, about −5.3.
- **`freeze_event`** — furnace failure or an open vent during a cold snap. Scheduled on the
  coldest day in the window; ambient falls over six hours by whatever it takes to put *this
  device's* pipe near zero (nearly 20 K for a heated basement, 6 K for a crawlspace), holds
  18–40 h, recovers over twelve. Draws are suppressed, because the archetypal freeze claim is an
  unoccupied property. Both channels fall together so **`delta_c` stays near zero and only
  `pipe_c` carries the warning** — the latent heat of the standing water puts a plateau just
  above freezing, which is the signature A4 has to fire on.
- **`sensor_fault_stuck`** — a hung I2C bus or a one-shot conversion never re-armed: the driver
  returns the last good read, bit for bit, forever. Ambient keeps moving, so a *fake* `delta_c`
  grows on its own. This is the nastiest fault, because untreated it looks like a slowly
  worsening leak.
- **`sensor_fault_noisy`** — 40% of samples take a 7–28 K step, which a pipe full of water cannot
  do in five minutes; 12% rail outside the TMP117's own range, past the −40…80 °C band §5
  calls implausible. `tamper` is set on 18% of samples, because a loose clamp is usually what
  "noisy" means.

**Properties — true of the installation from day one, and benign by construction.**

- **`install_unverified`** — `install_verified=false` on every frame. Two failure modes: the clamp
  dangling in free air (reads a plausible temperature with no relation to the pipe, and does not
  respond to draws at all), or **clamped to the hot-water line** — reads 10–20 K high and moves
  the *wrong way* on a draw, so a detector keying on excursion magnitude rather than sign calls
  it a leak. §5 requirement 2 and risk 4: never counted as a leak, never a true event.
- **`cold_basement_winter`** — a legitimately cold pipe. Labelled when the device's no-flow
  structural offset is at or beyond −1.0 K, measured from the true physics so a device's own
  calibration bias cannot change its label. **Overlaps the slow-leak distribution on purpose.**
  It is separable, but only because it is stable over weeks and present from day one, whereas a
  leak *steps* the baseline at a knowable moment — which is exactly the structure §5
  requirement 5 asks the detector to learn.

### How a sample gets its label

Labels come from the physics that was simulated, never from the intent behind it. If a device
happens to sit at a near-freezing pipe temperature with no scheduled freeze episode, it is
labelled `freeze_event` anyway, because it genuinely is at risk and scoring a correct freeze
alert as a false positive would be a lie. Precedence, highest first:

```
install_unverified > sensor_fault_stuck > sensor_fault_noisy > freeze_event
    > fast_leak > slow_leak > shower_morning > appliance_use > cold_basement_winter > quiet
```

A draw keeps its label through its thermal decay tail (two still-air time constants, about an
hour): a sample 20 minutes after a shower is a shower artefact, and calling it `quiet` would be
scoring the detector against a lie in the other direction. Stacked episodes on one device are
laid out sequentially and never overlap, because §6 gives each sample exactly one `scenario`.

## Everything else the fleet models

- **Five archetypes** — `heated_basement`, `utility_closet`, `cold_basement`, `crawlspace`,
  `garage` — differing in set point, how much of the outdoor swing reaches them, daily swing, and
  `wall_k`. Mid-winter air at the sensor lands between about 8 and 20 °C across the fleet, which
  is why §5 requirement 5's per-device baseline is necessary and a global threshold is not
  merely suboptimal but wrong.
- **Climate** — seasonal outdoor air for the northern tier (−2.5 °C January daily mean, 23.5 °C
  July) with a diurnal swing and an AR(1) synoptic walk, so cold snaps last days.
- **Ground water** — lags air by five weeks at roughly half the amplitude: 4.5 °C at the end of
  winter, 17.5 °C at the end of summer, floored at 1 °C because a live main does not freeze.
- **Sensor imperfection** — per-channel calibration offsets (σ 0.22 K, clipped at ±0.6) and
  measurement noise (σ 0.10–0.30 K, dominated by thermal contact and air movement rather than by
  the TMP117, which is good to ±0.1 K). The calibration offsets are a *differential* bias on the
  signal itself, which is the second reason a global threshold cannot work.
- **Battery** — Li-SOCl2 AA, 1400 mAh usable after the ~40% derate of risk 7, charged
  0.014 mAh per frame (371 ms of SF10 airtime at 20 dBm, two RX windows, two TMP117 conversions)
  plus 2 µA sleep. Reported voltage is the *loaded* pulse voltage, so it carries both depth of
  discharge (flat plateau, then a knee past 85%) and **cold sag** — the same cell reads ~200 mV
  lower at −5 °C than at 20 °C. A fleet view must not mistake a cold device for a dying one.
  Devices start with 0–55% depth of discharge, and a tenth of the fleet starts past the knee.
- **Radio** — long-tailed per-device loss (68% of devices at 0.5–4%, 10% at 18–38%), gateway
  outages of 1.5–7 h that make gaps bursty and simultaneous across devices, two gateways in
  range for ~55% of devices. RSSI/SNR correlate with the link budget, and the spreading factor
  stays in 7–10 because **US915 uplinks at 125 kHz stop at SF10 (DR0)** — SF11/SF12 do not exist
  there, which caps what ADR can do for a basement device.
- **Packet loss and `f_cnt`** — a lost frame is not emitted but still consumes an `f_cnt`,
  because the counter belongs to the device. Loss therefore appears at the backend as a gap in
  `f_cnt`, which is what §1 says loss accounting uses.
- **The install failure tail** — risk 4's leading indicator is ">10% of shipped units never
  reporting 7 days out". ~5–10% of the fleet transmits its boot frame and is never heard from
  again, concentrated among unverified installs, which is what gives §7's `pct_never_reporting`
  something to measure.
- **Uplink reasons** — all four of §1 appear: `boot` on the first frame, `install_probe` on the
  second (the §2 probe-result frame), `threshold` while a cold warning or a deep `delta_c` is
  latched, `heartbeat` otherwise.
- **Duplicates** — ~1.2% of envelopes are delivered twice with the same `(dev_eui, f_cnt)`, so
  §4's idempotency requirement is actually exercised rather than merely asserted.

## Three findings that came out of building this

**1. The leak signal is seasonal, and October is the worst month to collect data.** The signal is
driven by `(ground_water − ambient)`. In October mains water is still near 16 °C against a 19 °C
basement, so even a fast leak moves `delta_c` by about a degree; in February the same leak has a
twelve-degree gradient to work with. Risk 12's "devices in the field by October" is a *shipping*
deadline, and it is right — but the data does not start arriving until December. `--start
2026-10-01` generates that adversarial autumn dataset deliberately.

**2. In a space as cold as the incoming water, a leak raises the pipe above ambient.** An early
draft of the archetype table had a crawlspace at 3.8 °C in January, and in it a fast leak
produced a *positive* `delta_c`. That is a true consequence of the physics, not a modelling bug.
The archetypes are now calibrated to spaces where a live main plausibly exists (nothing that sat
near freezing all winter still has intact plumbing), but `crawlspace` is deliberately left as the
marginal case: **the method degrades in cold spaces in deep winter, and a detector must not
depend on the sign of `delta_c` alone.**

**3. A noisy sensor will flatten the battery and flood the ingest path** unless rate escalation
is budgeted. A sensor faulting across the threshold on half its samples re-triggers escalation
indefinitely; before the re-arm guard was added, one broken device out of forty produced 23% of
the entire dataset. The firmware model now allows at most one 2-hour escalation per 8 hours. It
is a real firmware bug class, and it showed up here before it could show up in the field.

## Limitations, stated plainly

- **This is not validated against measured data.** It is a hand-built forward model with
  plausible constants. It can rank detector designs and expose a detector that only works on toy
  data. It **cannot predict the real nuisance rate**, and nothing here should be quoted as
  evidence for the P1 gate. `../../08-roadmap.md` §7 is explicit that only a real winter settles
  that, and the whole point of shadow mode (standing rule 3) is that the simulator is not
  trusted to.
- **Prevalence is deliberately unrealistic.** At `--event-rate 1.0` roughly a third of the fleet
  is in some abnormal state over a month, three orders of magnitude above a real portfolio. That
  is needed because §7's recall and time-to-detection need positives per class, and two events is
  not a median. `nuisance_alerts_per_device_year` — the actual P1 exit gate —
  `true_event_recall` and `median_time_to_detection_s` are rates over their own denominators and
  do not move with prevalence. **`precision` does, so precision measured on a default run is
  optimistic and must not be quoted as a forecast.** `--event-rate 0` drops to the forced
  coverage minimum.
- **No repairs, no alert responses, no operator behaviour.** A leak here runs to the end of the
  window. Anything about how quickly a human acts is out of scope.
- **`flow` is dimensionless**, a fraction of the service line's capacity, not litres per minute.
  That is all the thermal model needs and all that could be calibrated without a flow meter on a
  real install.
- **No join procedure, no ADR, no MAC commands, no downlinks.** The simulator produces uplink
  envelopes only. §2 downlinks are implemented in `../codec/`, and valve command semantics are
  the backend's.
- **The device list is only discoverable from the stream.** A unit that boots and dies would
  otherwise be invisible in the JSONL, so the boot frame bypasses the loss model — in a real
  deployment DevEUIs are provisioned into the LNS before shipment, which is what gives
  `pct_never_reporting` a denominator. This keeps the file self-describing without a separate
  manifest.
- **One process, one core.** A 40×30 run takes about 40 seconds; 400 devices takes about seven
  minutes. There is no parallelism, because `heapq.merge` over per-device generators keeps memory
  bounded and that mattered more.

## Contract points that were wrong, ambiguous, or worth flagging

Raised rather than silently worked around, per the instruction at the top of `INTERFACES.md`.

1. **§4's example `frm_payload` does not decode to §3's example.** `"BLEAAQ0yAQA="` is
   `04 b1 00 01 0d 32 01 00` → pipe 12.01 °C, ambient **0.01 °C**, battery 3378 mV. §3's worked
   example is 11.25 / 18.40 / 3410. Both are legal frames and the byte layout is unambiguous, but
   an ambient of 0.01 °C is not a plausible illustration and the two examples read as though they
   should match. The real §3 example encodes to `"BGUHMA1SAQA="`. Both are in the golden-vector
   tables on either side so neither can drift.
2. **§6's example `event_start_s` of 1760000000 is 2025-10-09**, a year before the contract's
   own authoring date of 2026-10-14 (which is 1791946800 at 03:00Z). Harmless, but it means the
   example cannot be used as a sanity check on a timestamp.
3. **§3 requires `uplink_reason` as a string but only names `"heartbeat"`.** The other three
   spellings are the codec's choice; see `../codec/README.md` for the table. This module imports
   the same list so the two cannot diverge.
4. **§7's `pct_never_reporting` is undefined for runs of seven days or less.** "Devices with zero
   uplinks after day 7" makes every device qualify on a 3-day window, which would read as a 100%
   failure rate. Reported as `None` below the threshold rather than as a number.
5. **§1's `delta_c = pipe_c - ambient_c` is ambiguous about where the division happens.**
   Subtracting the centi-degree integers and dividing once gives §3's `-7.15`; dividing first and
   then subtracting gives `-7.149999999999999`. Both codec and simulator do it the first way.
6. **§6 does not say what `event_start_s` should be when nothing is happening.** `null` here.
7. **Nothing in the contract says `is_true_event` for the two sensor-fault scenarios or for
   `install_unverified`.** The choice made here: faults are true events (a real condition an
   operator must be told about, verdict `sensor_fault`), while `install_unverified` and
   `cold_basement_winter` are **not** — §5 requirement 2 and risk 4 are explicit that an
   unverified install must never be counted. `_label` also carries an additive
   `expected_verdict`, `delta_c`, `archetype` and `install_verified`, which §4's
   ignore-extra-fields rule makes safe.
