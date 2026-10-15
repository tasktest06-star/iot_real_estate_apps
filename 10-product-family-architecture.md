# 10. Product Family — Architecture and Implementation Plan

How the platform built in [`prototype/`](prototype/) for application **A1** (clamp-on thermal leak
sensor) extends to the other thirteen applications in
[`02-application-portfolio.md`](02-application-portfolio.md), and to adjacent perils beyond them.

Written 2026-10-15. **[V]** vendor-published, **[E]** estimate, **unverified** says so.

---

## 10.0 What this document is for

`prototype/` is one product. The commercial case in
[`03-business-models.md`](03-business-models.md) is a *portfolio*: at ~$23/device/year of
contribution (Ondo's audited figure), a single SKU only works at hundreds of thousands of units,
and the realistic path to that volume is several products sharing one platform and one buyer
relationship.

So the question this answers is not "what else could we build" — `02` already ranks that. It is:
**given the code that now exists, what does each additional product actually cost, and in what
order do they unlock each other?**

---

## 10.1 The platform boundary, measured not asserted

Four branches ([`prototype/adr/`](prototype/adr/README.md)) swapped the radio and the server stack.
In all four, **`detection/` was unchanged** — 130 tests and a measured 0.3383 nuisance
alerts/device-year carried across every variant. [ADR-003](prototype/adr/ADR-003-wifi-esp-idf.md)
swapped the RTOS and four of eight firmware modules ported byte-for-byte.

That gives a real, tested platform boundary rather than an aspirational one:

| Layer | Reusable across products? | Why |
|---|---|---|
| `detection/detector.py` | **Yes, with a generalisation** — see §10.2 | Pure stdlib functions over a `Sample`; no I/O, no transport knowledge |
| `detection/evaluate.py` | **Yes, as-is** | Scores any labelled corpus against the P1 gate |
| `simulator/` physics | **Per-product** | Each peril has its own physics; the *harness* is reusable |
| `backend/app/{db,ingest,alerts}.py` | **Yes, as-is** | Device registry, idempotent ingest, shadow mode, ack/nuisance feedback are peril-agnostic |
| `backend/app/insurer.py` | **Yes, extended** | Portfolio reporting is per-peril rows in one report |
| `backend/app/valve.py` | **Yes for any actuator** | The arming/refusal/unconfirmed pattern generalises |
| `codec/`, `contracts/` §1 | **Per-product payload, same discipline** | 8 bytes of *something*; the int16/flags/reason skeleton is the reusable part |
| `firmware/src/{uplink,valve,install_probe}.c` | **Yes** | Zero RTOS includes, proven portable |
| `firmware/src/leak_detect.c` | **Yes, with the same generalisation** | C twin of the Python detector |
| `firmware/src/main.c` | **Per-radio** | Glue only |
| **Sensing front end** | **No — this is the per-product work** | §10.3 |
| **Enclosure and mechanics** | **No** | Often the dominant cost (see A9) |
| **Certification** | **No, and it dominates** | §10.4 |

**The one-line version: software is ~80% reusable, hardware front ends and certification are ~0%.**
Which means product sequencing should be driven by the certification gate, not by engineering
effort — the opposite of most hardware roadmaps.

---

## 10.2 The detector generalises, because it is not really a thermal detector

`detector.py` is described as a thermal differential-temperature detector. Structurally it is
something more general: **a persistence-against-adaptive-baseline detector with a nuisance-rejection
dwell.** Its mechanism is

1. learn this device's own quiescent baseline and noise floor (MAD),
2. derive a trigger from that noise floor rather than a global constant,
3. integrate **net** time the signal stays depressed past the trigger, with release faster than
   accrual (`DWELL_RELEASE_MULT = 2`), so intermittent excursions decay,
4. require a minimum count of independent confirmations in a bounded 32-bit window,
5. latch, with a long clear period so one noisy sample cannot re-raise an alert.

Nothing in that is about temperature. The same machine works on any signal where **"a sustained
departure from this installation's own normal" means a fault**:

| Product | Signal fed to the same machine | Direction |
|---|---|---|
| A1 clamp-on thermal | `pipe_c − ambient_c` | depressed |
| A2 point/puck leak | water-present duty cycle | elevated |
| A4 freeze | absolute pipe temperature | depressed *(with its own dwell — see §10.5)* |
| A5 sump pump | pump run-time per hour, or run frequency | elevated |
| A10 sub-metering | continuous minimum night flow | elevated |
| A11/A12 electrical | per-circuit current, harmonic distortion, connector temperature | elevated |

**Recommended refactor, and it is the highest-leverage software work in this document:** extract
`detector.py`'s machine into `detection/persistence.py` taking a `SignalConfig`
(trigger multiple, dwell, release ratio, direction, plausibility band, freeze-style secondary
latch), and make the thermal detector a thin configuration of it. Cost **[E] 1–2 weeks** including
porting the 130 tests to parameterised form. It is what turns "we have a leak sensor" into "we have
a platform", and every product below gets cheaper for it.

**The caveat that must survive the refactor:** the measured 0.3383 nuisance rate belongs to the
*thermal* configuration on *that* corpus. A new signal gets its own labelled corpus and its own
gate run. Generalising the code does not generalise the evidence.

---

## 10.3 Per-product architecture and implementation plan

Effort is **[E]**, incremental over the existing platform, assuming the §10.2 refactor is done.
"Cert" is the gate crossed per [`07-compliance-and-certification.md`](07-compliance-and-certification.md).

### Tier 1 — same gate, same `Sample`, weeks not months

These stay in the cheap quadrant (no mains, no wetted surface) and reuse the existing payload
skeleton almost unchanged.

#### A4 — Freeze and pipe-temperature risk · **already built, needs a second signal**
Shipped in `prototype/` and measured at **9 of 31 freeze events caught (29%)** — see
[`prototype/detection/README.md`](prototype/detection/README.md). The pack's "free upside" framing
was corrected to `tech_risk = 3` because of it.
- **Architecture change:** add rate-of-fall and the ambient trend as inputs; the sensor already
  reports both. A pipe falling 2 °C/hour toward 2 °C is a different object from a pipe that has sat
  at 2.5 °C all winter, and absolute threshold alone cannot tell them apart.
- **Changes:** `detection/` only — new `SignalConfig` plus a derivative input. Firmware, codec,
  backend, certification: **none**.
- **Effort:** 1–2 weeks. **Cert:** none. **Verdict: do this first.** It is the cheapest unshipped
  value in the portfolio and it improves a product already in the field.

#### A2 — Point/puck leak sensors + hub · **4–6 weeks**
- **Sensing:** gold-plated contact pair, conductivity. Benchmark to beat: Milesight WS303 triggers
  at **0.5 mm** of water on a CR2450 with ~5.7-year life **[V]**.
- **Payload:** new 8-byte layout — water-present flag, duty cycle, battery, temperature. Same int16
  + flags + reason skeleton.
- **Changes:** sensing front end and enclosure (new); `detection/` a new `SignalConfig`; firmware
  pure modules unchanged; `main.c` sampling loop modified; codec new; backend **unchanged** (it
  already stores arbitrary decoded payloads per device type).
- **Cert:** FCC Part 15 only, module grant inherited → **$1,500–5,000 [E]**.
- **Honest note:** `defensibility = 1`. Anyone can buy this from Dragino or Milesight tomorrow. Its
  role is to get installed and accumulate corpus while A1 certifies — not to be the company.

#### A5 — Sump pump and basement water monitoring · **6–8 weeks**
- **Sensing:** current transformer on the pump lead (non-invasive, no mains in *your* enclosure if
  the CT is passive and the device is battery) plus a water-present probe. Optionally vibration.
- **The signal:** pump run-time per hour and cycle frequency. A pump cycling more often is a rising
  water table; a pump that stops cycling during rain has **failed**, which is the valuable alert and
  the one no incumbent in [`01`](01-market-and-insurance-case.md) §1.3 productises.
- **Changes:** new front end; `detection/` config with an *elevated*-direction trigger plus an
  absence detector; backend unchanged.
- **Cert:** FCC only **if** the CT keeps mains outside your enclosure. **Check this carefully** — a
  CT clamped round a mains conductor with a passive secondary is normally fine, but it is exactly
  the kind of question an NRTL will have an opinion about. **Unverified.**
- **Interaction worth noting:** a shutoff valve that fails closed **kills a sump pump**. A5 and A13
  must share state, which is an argument for building them on one platform rather than separately.

### Tier 2 — new gate, months, needs the §10.2 refactor to be worth it

#### A9 — Retrofit clamp-over-lever actuator · **spike first, then 12–18 weeks**
- **Regulatory:** dodges NSF 61/372 and IAPMO entirely — no wetted surface, no pressure boundary.
  Battery or an externally supplied listed adapter keeps it out of NRTL. **Same cheap quadrant as
  A1.**
- **The blocker is mechanical, not regulatory:** breakaway torque on aged valves, stem weep after
  years of set packing, and multi-turn gate valves that a quarter-turn actuator cannot drive at all.
  **[E] well under half** of target homes are compatible.
- **Reuse is unusually high:** `firmware/src/valve.c` and `install_probe.c` already implement
  arm-or-refuse, the fail-as-is state machine and the install probe — written for A13 and directly
  applicable. `backend/app/valve.py`'s 409-on-unarmed path is unchanged.
- **Plan:** the **20-valve spike** from [`08-roadmap.md`](08-roadmap.md) §7 claim 3 — twenty
  salvaged main shutoff valves, measure breakaway torque, cycle success, post-cycle stem leakage.
  **Kill criterion: fewer than 70% cycle without weep.** Cost a few hundred dollars and an
  afternoon. Do not write firmware before this passes.
- **If it passes:** mechanical development is **$60,000–200,000 [E]** and dominates everything else.

#### A10 — Condo/HOA sub-metering · **4–6 months**
- **Sensing:** ultrasonic transit-time. **TI MSP430FR6047** gives `<25 ps` differential ToF,
  detects `<1 L/h`, ~3 µA at 1 measurement/s, and *"exceeds ISO 4064, OIML R49, EN 1434"* at
  **$9.25 @1k [V]** — or **TDC1000** at **$2.60 @1k [V]** with a separate MCU.
- **The signal:** minimum night flow. A unit with a non-zero floor at 3 a.m. has a leak; this is the
  utility industry's standard method and it maps onto the §10.2 machine directly.
- **Cert, and why this is Tier 2:** inline means **wetted surfaces** → NSF/ANSI 61 + 372, IAPMO IGC
  115/349, plus **weights-and-measures approval** if the reading is used for billing between
  parties. That last one is a different regulatory world and is **unverified** in this pack.
- **Reuse:** detection and backend yes; front end is a water-meter-class BOM, which is why `02`
  prices it at $45–90.

#### A12 — NEMA 5-15 metered plug · **4–6 months**
- **The gap is verified and narrow:** Milesight's WS51x is **EU Type F / FR Type E and CE-only**
  despite supporting US915; the WS523 portable socket has a US 15 A variant with FCC — but
  **neither is UL/ETL listed**. There is no off-the-shelf North American metered LoRaWAN or
  cellular smart plug.
- **Sensing:** **ST STPM34 at $1.59 @1k [V]** is the cheapest credible metering AFE by ~1.5×;
  **ADI ADE7953** at **$2.46 @1k [V]** for single-phase with two current channels.
- **Architecture:** mains power kills the low-power argument, so this is
  [ADR-003](prototype/adr/ADR-003-wifi-esp-idf.md) territory — ESP32-C6 on ESP-IDF, Wi-Fi, no
  gateway.
- **Cert:** crosses the mains gate. UL 498 + UL 60730-1 + UL 94 → **$15,000–40,000 [E]** plus
  **annual factory inspections**, which first-year budgets routinely omit.

#### A11 — Panel-level electrical monitoring with NILM · **4–6 months**
- **Sensing:** **LEM ART Rogowski coils**, 10–10,000 A RMS, class 0.5, and — the spec that matters —
  **50/60 Hz to 700 kHz open-circuit bandwidth [V]**, the only part surveyed with bandwidth in the
  arc-relevant range. Split-core CTs for the cheap variant.
- **Software reuse is exceptional here and it is all Apache-2.0:** `nilmtk` + `nilmtk-contrib`
  (30+ modern disaggregation models, standard evaluation harness) + `nilm_metadata` — **steal its
  appliance schema for the telemetry ontology instead of designing one twice.** Reference hardware:
  `CircuitSetup/Expandable-6-Channel-ESP32-Energy-Meter` is **MIT for hardware *and* firmware**, so
  the schematic is liftable. Use it instead of IotaWatt (GPL-3.0) or emonTx (GPL firmware,
  **CC-BY-SA hardware** — do not trace the layout).
- **The blocker is dataset licensing, not engineering.** PLAID, WHITED, BLUED, REDD, UK-DALE, REFIT
  and AMPds are variously **CC-BY-NC or under academic DUAs**. Training a shipped commercial model
  on a non-commercial dataset is a breach no code hygiene fixes. The provenance register in
  [`prototype/`](05-open-source-stack.md) §5.5 must be populated **before the first training run**.
- **Cert:** electrician install, panel-adjacent NRTL scope.

### Tier 3 — defer, and the reasons are not engineering

#### A13 — Inline motorised valve · **requires a raise**
Triggers every gate simultaneously: FCC + NRTL + IGC 115/349 + NSF 61/372 + cUPC →
**$112,000–366,000 [E]**, plus a licensed plumber and probably an electrician (Moen's own Flo
requires a nearby AC outlet and sells a 25-ft extension rather than going battery — strong evidence
a ½″–1″ motorised ball valve's stall torque cannot be served from primary cells over five years).

Two architectural notes already encoded in `prototype/firmware/src/valve.c`: battery-only shutoff
requires a **latching solenoid, not a motor**; and **fail-safe must mean fail-*as-is***, never
fail-closed. Until then, **resell a certified valve** (A6) — zero certification cash, and you still
accumulate the sensing data and the carrier relationship, which are the assets.

#### A14 — Plug-in arc detector · **requires a raise, and a dataset that does not exist**
**$180,000–560,000 [E]**, dominated by the HV front end and the classifier. Three compounding
problems: the training data is unobtainable in the open (GitHub's `afci` topic has **one repository
with zero stars**; UL 1699 waveforms sit behind UL's paywall and test-lab NDAs); UL 1699 is as much
about *not* tripping on vacuum cleaners, dimmers and arc welders, and nuisance-trip data is equally
proprietary; and it sits on Whisker Labs' patent family, active in US/EP/CN/JP.

**The one scoping rule that saves $60,000–200,000: never interrupt the circuit.** Ting deliberately
only alerts, which keeps it out of UL 1699. Copy that, and write "alert-only" somewhere nobody can
quietly change it.

---

## 10.4 Sequencing, driven by the certification gate

Because software is ~80% reusable and certification is ~0%, the correct order is **all of the cheap
quadrant first**, regardless of how interesting the expensive products are.

```
  P1  ├─ A4 freeze second signal        detection/ only, no cert        1-2 wk
      └─ §10.2 persistence refactor     detection/ only, no cert        1-2 wk
  P2  ├─ A2 puck sensors                FCC only                       4-6 wk
      ├─ A5 sump pump                   FCC only (verify CT scope)     6-8 wk
      └─ A9 20-valve SPIKE              a few hundred dollars          1 afternoon
  P3  ├─ A12 metered plug               + NRTL $15-40k   } requires
      ├─ A11 panel NILM                 + NRTL, datasets } a raise
      └─ A10 sub-metering               + NSF/IGC/W&M    }
  P4  ├─ A13 inline valve               $112-366k
      └─ A14 arc detector               $180-560k + a dataset that does not exist
```

Everything in P1 and P2 fits the **$25k–100k** envelope alongside A1, because none of it crosses a
gate. That is the whole argument for the platform.

---

## 10.5 Shared platform work that unlocks several products at once

Ranked by products unlocked per week of effort.

1. **The §10.2 persistence refactor** — 1–2 weeks, unlocks A2, A4, A5, A10, A11. Do it first.
2. **A per-peril `SignalConfig` registry in the backend**, so one device type can report several
   signals and the portfolio report rolls them up per peril. ~1 week. The schema already stores
   decoded payloads per device, so this is mostly reporting.
3. **A generic actuator abstraction** lifted from `valve.py` — arm-or-refuse, fail-as-is,
   unconfirmed-never-success. ~1 week, unlocks A9 and A13 and any future relay product. The safety
   invariants must stay in code you own and test, per
   [ADR-004](prototype/adr/ADR-004-chirpstack-magistrala.md).
4. **A second dwell discipline for secondary latches.** The freeze bug is instructive: the leak path
   had a 6-hour dwell and the freeze path had none, which produced 60 of 61 nuisance alerts. Any new
   signal added without a dwell will repeat it. Make the dwell mandatory in `SignalConfig` — not
   optional with a default.
5. **The labelled-corpus harness.** `simulator/` physics is per-peril, but the scenario/label/eval
   scaffolding is not. Every new product needs a corpus before it can claim a gate.

---

## 10.6 Adjacent perils, beyond this portfolio

The same platform and the same buyer relationship extend past water and fire. Listed for
completeness; none is researched to the depth of A1–A14 and all are **unverified**.

| Peril | Signal | Why it fits | Why it might not |
|---|---|---|---|
| **Mould / humidity** | RH + dew-point margin against surface temperature | Same sensor pair, literally zero new hardware | Slow-onset, hard to attribute a claim to |
| **Boiler / HVAC failure** | Supply-return ΔT, cycle frequency | Same thermal front end; freeze prevention overlaps | Equipment-warranty channel, not insurance |
| **Roof / gutter ice damming** | Attic temperature and RH | Big winter peril in the northern US | Placement is awkward; install quality dominates |
| **Appliance supply-line failure** | Point sensor behind the washer | The highest-frequency single source of interior water claims | This is A2 with better placement, not a new product |
| **Vacant/seasonal property** | All of the above, plus occupancy inference | Insurers price vacancy explicitly; monitoring is directly underwritable | Needs the carrier relationship first |

The honest filter: a peril is worth adding when **an insurer already prices it separately**. Mould
and vacancy pass that test. Gutter ice probably does not.

---

## 10.7 What would make this plan fail

1. **The persistence refactor leaks product-specific logic.** If `SignalConfig` accumulates
   per-product special cases, you have a worse single detector rather than a platform. Test: adding
   a product must not modify `persistence.py`.
2. **Generalising the code is mistaken for generalising the evidence.** 0.3383 nuisance
   alerts/device-year is the *thermal* configuration on *one* synthetic corpus. Every new signal
   needs its own corpus and its own gate run, and none of them inherit that number.
3. **Portfolio breadth substitutes for buyer depth.** Six products and no countersigned pilot is
   worse than one product and a carrier contract. Risk 1 in [`08`](08-roadmap.md) §8 is unchanged by
   anything in this document, and building more SKUs is the most attractive way to avoid confronting
   it.
4. **A cheap-quadrant product quietly crosses a gate.** One mains adapter inside the enclosure, or
   one adapter fitting in the box, and the certification cost moves by an order of magnitude. The
   BOM policy from [`02`](02-application-portfolio.md) §2.3 — *actuator only, no plumbing parts in
   the box* — generalises: **nothing enters a BOM without re-checking both gates.**
5. **The A5/A13 interaction is missed.** A shutoff valve that fails closed kills a sump pump and
   floods the basement it was protecting. Products on one platform must share state, which is an
   argument for this document existing at all.
