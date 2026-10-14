# 2. Application Portfolio — What a Limited-Capex Startup Can Actually Start With

Fourteen candidate applications, scored and given a verdict. The machine-readable version is
`data/application-scores.csv`; this document explains the reasoning.

**Scoring convention.** `cert_burden` and `tech_risk` are 1–5 where **lower is better**.
`defensibility` is 1–5 where **higher is better**. Capex is the cash cost to a shippable certified
product, excluding salaries, consistent with `data/cert-costs.csv`.

---

## 2.0 The frame: two binary gates, not three products

The original brief framed this as a choice between three products. That framing hides the cost
driver. What actually determines capex is two yes/no questions about the bill of materials:

```
                       Is there MAINS VOLTAGE inside your enclosure?
                                  no  |  yes
                                      |
   Is there a WETTED        no    A1 A2 A3 A4 A5    |    D  (plug-in detector)
   SURFACE in the BOM?            A6 A7 A8 A9 C     |       NRTL: UL 498 + UL 60730-1 + UL 94
                                                    |
                            yes   A10 (if inline)   |    E  (inline motorised valve)
                                  NSF 61 + 372      |       NRTL + IGC 115/349 + NSF 61/372 + cUPC
```

Cross the left-hand boundary and you add **$15k–40k** of NRTL listing plus annual factory
inspections. Cross the bottom boundary and you add **$25k–80k** of plumbing listing and
wetted-material qualification. Stay in the top-left quadrant and your only gate is **FCC Part 15**,
which a pre-certified radio module largely pays for already.

**All eight START verdicts below avoid both gates.** That quadrant is the portfolio.

---

## 2.1 The portfolio

| ID | Application | Peril | BOM | Capex to ship | Mo. to revenue | Cert | Risk | Defens. | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| **A1** | Clamp-on thermal whole-home leak sensor | water | $22–35 | **$31k–94k** | 6–9 | 1 | 2 | 3 | **START** |
| **A2** | Point/puck leak sensors + in-home hub | water | $8–14 | $20k–50k | **4–6** | 1 | 1 | 1 | **START** |
| **A3** | Multifamily in-suite network on a building gateway | water | $10–18 | $25k–60k | 5–8 | 1 | 2 | **4** | **START** |
| **A4** | Freeze and pipe-temperature risk monitoring | water | $6–12 | **$0–15k** | **3–5** | 1 | 1 | 2 | **START** |
| **A5** | Sump pump and basement water monitoring | water | $14–22 | $20k–45k | 5–7 | 1 | 2 | 2 | **START** |
| **A6** | Resell/integrate a third-party certified valve | water | resold | **$0–10k** | **3–6** | **0** | 1 | 1 | **START** |
| **A7** | Advisory shutoff — monitored response, no actuator | water | $0 | $0–20k | 3–6 | **0** | 1 | 3 | **START** |
| **A8** | Insurer claims-triage and risk-scoring data product | both | $0 | $0–25k | 6–12 | **0** | 3 | **4** | **START** |
| **A9** | Retrofit clamp-over-lever shutoff actuator | water | $35–60 | $70k–231k | 12–18 | 2 | **5** | **4** | **SPIKE** |
| **A10** | Water sub-metering for condo/HOA cost allocation | water | $45–90 | $60k–150k | 9–15 | 4 | 3 | 3 | LATER |
| **A11** | Panel-level electrical monitoring with NILM | electrical | $45–80 | $60k–140k | 9–15 | 3 | 4 | 3 | LATER |
| **A12** | NEMA 5-15 metered LoRaWAN/cellular smart plug | electrical | $18–30 | $80k–180k | 12–18 | 4 | 3 | **4** | LATER |
| **A13** | Inline motorised shutoff valve (own design) | water | $70–130 | $112k–366k | 18–30 | **5** | 4 | 4 | LATER |
| **A14** | Plug-in electrical arc/fault detector | electrical | $25–45 | **$180k–560k** | 24–36 | **5** | **5** | **5** | **AVOID YR 1** |

---

## 2.2 The eight to start with

### A1 — Clamp-on thermal whole-home leak sensor · **the own-hardware wedge**

Two temperature sensors and a spring clip on the incoming main. A hidden leak continuously draws
cold ground water through the pipe, so the pipe sits at a persistent small ΔT below ambient that
normal intermittent use does not produce. This is LeakBot's Thermi-Q method, which claims
sensitivity to **5 ml/min**.

**Why it is the wedge:** no mains, no wetted surface, no plumber, mailable, and a ~$27 BOM
(`data/bom-estimates.csv`). Certification is FCC Part 15 only, and a pre-certified RAK3172 at
**$5.99 [V]** ships with downloadable FCC, ISED, CE and LoRa Alliance certificates. It also has a
proven insurer-funded channel — this is the product 28 carriers already buy from Ondo.

**What to watch:** it sits squarely on the Leakbot/Homeserve patent family (§1.6 of
[01](01-market-and-insurance-case.md)), so **an FTO opinion is a P0 line item, not an appendix**.
Use **two TMP117 (±0.1 °C class, $3.62 @1k [V])** rather than an NTC divider — self-heating and
tolerance stack will eat a small persistent ΔT. The acoustic variant (A1b in the BOM file) adds a
**TDK T5838 with 20 µA acoustic activity detect**, the one spec that makes battery-powered acoustic
sensing viable at all; treat it as a later SKU, not v1.

### A2 — Point/puck leak sensors + hub · **the fastest thing you can sell**

Pucks under the washer, dishwasher, water heater and sink, reporting to a hub. **$8–14 BOM**, 4–6
months to revenue, essentially no technical risk. Benchmark to beat: Milesight WS303 triggers at
**0.5 mm** of water on a CR2450 with ~5.7 year life.

**The honest problem is defensibility — scored 1.** This is a commodity anyone can buy from
Dragino or Milesight today. Its role is to get you installed, collecting data and holding a
contract while A1 certifies. Do not build the company on it.

### A3 — Multifamily in-suite network · **the best margin in the portfolio**

Dense cheap nodes in every unit, one building gateway. **This is the only configuration where
LoRaWAN economics genuinely beat the alternatives:** one RAK7289V2 at **$382 [V]** amortised across
dozens of **$12** nodes, versus a per-home gateway that destroys a consumer price point.

It also has the best commercial dynamics in the pack: it pays upfront, it churns with the *asset*
rather than the policy (unlike insurer-funded, which dies when the policyholder switches), and the
buying trigger is **insurability rather than a discount** — The Alexander's $150k → $850k premium
story sells itself and requires nobody to file anything. Brokers (Aon, HUB, USI) are the door.
Price to beat: ProSentry's **~$70/sensor + $1/sensor/month**.

### A4 — Freeze and pipe-temperature risk monitoring · **free upside**

You are already measuring pipe temperature for A1. Freeze risk is the same sensor, a different
threshold, and a *separately sellable* peril — Ting bundles low-temperature warning for exactly this
reason. **Capex $0–15k** because it is a firmware and cloud feature on hardware you already ship.
Strongly seasonal, which cuts both ways: it is the easiest thing to sell in October and the hardest
in June.

### A5 — Sump pump and basement water monitoring · **underserved**

Current sensing or vibration on the pump plus a water-presence probe, detecting a pump that has
failed *before* the basement floods. Genuinely underserved: no incumbent in §1.3 productises it.
Note the interaction with A9 — **a shutoff valve that fails closed kills a sump pump**, which is one
of the ways a leak-prevention device causes a loss.

### A6 — Resell or integrate a third-party certified valve · **how you tell the shutoff story in year 1**

Integrate over the API or radio of a valve someone else has already listed. **Zero certification
cash, zero wetted surface, zero plumbing liability on your BOM.** Thin hardware margin, but you
accumulate the sensing data and the carrier relationship, which are the actual assets. This is how
you answer "can you shut the water off?" without spending $112k–366k on A13.

### A7 — Advisory shutoff with no actuator · **the cheapest credible product**

Alert plus a human — homeowner app, property manager, or a monitoring centre — closes the valve. No
actuator, no liability, no hardware. For commercial and multifamily buyers a **monitored response**
is frequently what they actually want to buy, and it supports a premium-credit conversation.

The caveat is real and belongs in the risk register: **a 3 a.m. alert needs someone to act.**
Without a dispatch answer you are selling a notification, and notifications do not reduce claim
severity. That makes this an operations business more than a software feature.

### A8 — Insurer claims-triage and risk-scoring data product · **the software multiple**

Risk scores, first-notice-of-loss triage, portfolio analytics and loss-avoided reporting, built on
devices other people certified. Zero hardware risk. Carriers are demonstrably buying this —
LexisNexis launched Location Intelligence for Home in May 2026, and Adaptive + Whisker Labs turned a
sensor fleet into a parametric insurance trigger.

**The real gate is not technical, it is SOC 2 and a security review**, which is 3–6 months for
Type I and another 6–12 for Type II. Start it in P1, not when a carrier asks.

Adopt **openIDS Homeowners v1.0** as the outbound schema — Linux Foundation governed, royalty-free
patent commitments, and the Community Specification Licence explicitly exempts *implementations*
from its attribution terms, so closed-source implementation is permitted. There is **no open
real-time property-risk telemetry schema**, which is a cheap standard-setting opportunity: publishing
one (device → property → peril event → mitigation action → verified loss avoided) costs a week.

---

## 2.3 The one to spike, not commit

### A9 — Retrofit clamp-over-lever shutoff actuator

**Regulatory verdict: it dodges the plumbing standards.** NSF/ANSI 61 and 372 are scoped to
*wetted surfaces*; a bracket that bolts to the outside of an existing valve and turns its handle has
none. The host valve keeps its own listing. ASSE/IAPMO test regimes are built around a device
installed *in* the supply line — an external actuator has **no pressure boundary**, so most of the
matrix is physically inapplicable. Battery or low-voltage DC with an externally supplied UL-listed
wall adapter keeps you out of NRTL scope too. **So on paper this lands in the same cheap class as
A1.**

**One hard product-definition rule follows:** the moment the box contains a valve, adapter, union or
tailpiece, that part needs NSF 61/372 and the kit becomes a plumbing product. *Actuator only, no
plumbing parts in the box* — write it into the BOM policy, because the temptation to "just include
an adapter" arrives around month four.

**But the blocker is mechanical, and it is severe:**

- **Breakaway torque on an aged valve.** A brass ball valve untouched for twelve years does not
  behave like its datasheet. Seized PTFE seats, scale and stem corrosion push field breakaway well
  above nominal. Worse, **some valves will turn and then weep at the stem packing**, because the
  packing has taken a set. Your leak-prevention device then causes a leak, in the customer's
  basement, with your logo on it.
- **Multi-turn gate valves are common as mains shutoffs in older North American housing, and a
  quarter-turn lever actuator cannot drive them at all.** Combined with handle geometry, lever
  length and clearance to joists, the fraction of target homes where a clamp-on quarter-turn
  actuator physically fits and succeeds is **[E] well under half.** That is a product-market-fit
  ceiling, not a cost.
- **Liability does not care whose valve it is.** If you actuate a valve you did not certify and it
  fails, you are in the argument regardless.

**Therefore: a 20-valve spike in P0, not a product.** Buy twenty salvaged main shutoff valves of
mixed age, type and size, and measure breakaway torque, cycle success and post-cycle stem leakage.
A few hundred dollars and an afternoon answers whether A9 exists. **Kill criterion: fewer than 70%
of twenty real aged valves cycle without stem weep.**

The mitigation is also a genuine differentiator and is cheap: **test-cycle the valve at install
time, record it as telemetry, and refuse to arm** if breakaway torque exceeds a threshold or
post-cycle leakage is detected. Build that in P0.

---

## 2.4 The four to defer, and why

### A10 — Condo/HOA sub-metering · LATER
Real demand (boards are writing leak liability onto unit owners) and a genuine white space in
combining sub-metering with detection and an insurance credit. Deferred because inline metering
means **wetted surfaces plus weights-and-measures approval** — the expensive quadrant — and 9–15
months to revenue.

### A11 — Panel-level electrical monitoring with NILM · LATER
Technically the *right* front end for electrical work: **LEM ART Rogowski coils have 50/60 Hz to
700 kHz open-circuit bandwidth [V]** — the only part surveyed with bandwidth in the arc-relevant
range. And the open-source position is unusually strong: `nilmtk` + `nilmtk-contrib` are
**Apache-2.0** with 30+ modern models and a standard evaluation harness, and
`CircuitSetup/Expandable-6-Channel-ESP32-Energy-Meter` is **MIT for hardware *and* firmware** — use
it instead of IotaWatt (GPL-3.0) or emonTx (GPL firmware, **CC-BY-SA hardware**, so do not trace the
layout).

Deferred because it needs an electrician, sits adjacent to the panel for NRTL purposes, and — the
decisive point — **several NILM datasets (PLAID, WHITED, REDD, UK-DALE, REFIT) are CC-BY-NC or under
academic DUAs.** Training a shipped commercial model on a non-commercial dataset is a breach that no
amount of Apache-2.0 code hygiene fixes. Keep a per-model-artifact provenance register from the
first training run.

### A12 — NEMA 5-15 metered LoRaWAN/cellular smart plug · LATER
**The narrowest, most verifiable gap in the pack: this product does not exist in North America.**
Milesight's WS51x is Type F/E plug and CE-only despite supporting US915; the WS523 portable socket
has a US 15 A variant with FCC but **neither is UL/ETL listed**. Deferred only because it is mains —
UL 498 + UL 60730-1 + UL 94 — not because the opportunity is weak. Revisit the moment there is
revenue.

### A13 — Inline motorised valve (own design) · LATER
Triggers every gate at once: FCC + NRTL + IGC 115/349 + NSF 61/372 + cUPC, at **$112k–366k**, plus a
licensed plumber and probably an electrician (Moen's own Flo requires a nearby AC outlet and sells a
25-ft extension rather than going battery — strong evidence that a ½″–1″ motorised ball valve's
stall torque cannot be served from primary cells over a five-year life).

Two architectural notes for when you do build it. **If you want battery-only shutoff the actuator
must be a latching solenoid, not a motor** — a tens-of-millisecond pulse that holds state with zero
standing current is the only topology that gets years on cells; irrigation has solved this at
**$19–22 per valve body [V]**, though those bodies are not potable-rated and are diaphragm valves
needing minimum differential pressure. And **fail-safe must mean fail-*as-is*, not fail-closed**: a
valve that closes on battery death floods your support queue with "no water" calls and, in a freeze
event, causes the exact damage you were hired to prevent. Latching is inherently fail-as-is. Give
every unit a mechanical manual override.

### A14 — Plug-in electrical arc detector · **AVOID in year 1**

The most defensible product in the portfolio (scored 5) and the one you cannot afford.

**$180k–560k+**, dominated by the HV front end and the classifier. Ting's method is **wideband
voltage-waveform analysis at a single plug point** — HF transients riding on the 60 Hz cycle,
conducted across premises wiring, which is why one plug covers a house. Your front end is a
safety-rated divider from line into a wideband ADC sampled fast enough to resolve micro-arc bursts,
plus aggressive DSP. The hard parts are mains isolation done to a standard and the classifier.

Three things make it worse than it looks. **(1)** The training data does not exist in the open:
GitHub's `afci` topic has **one repository with zero stars**, and UL 1699 waveforms sit behind UL's
paywall and test-lab NDAs. **(2)** UL 1699 is as much about *not* tripping on vacuum cleaners,
dimmers and arc welders as about detecting arcs, and nuisance-trip data is equally proprietary.
**(3)** It sits on the Whisker Labs patent family, active in US/EP/CN/JP.

The one genuinely useful artefact is `MicrochipTech/ml-arc-fault-detection` — **Microchip
proprietary, so read it and reimplement clean-room, do not copy.** Its value is the feature
engineering and the budget it validates: **absolute mean, minimum, median frequency energy and
percent-time-over-zero on 160-sample windows at 16 kHz, in 4–6 kB flash and 1–2 kB RAM.** Those four
cheap features map almost directly onto CMSIS-DSP primitives. Note it is **DC** arc data; your
product is 120/240 V AC, which is materially harder because of zero-crossings and load masking.

**And the scoping rule that saves $60k–200k: do not interrupt the circuit.** The moment your device
opens the circuit on an arc signature you are an AFCI and you are in UL 1699. Ting deliberately only
alerts. Copy that.

---

## 2.5 The recommended combination

**A1 + A6 + A3, with A4/A7/A8 as attach.**

- **A1** is the own-design hardware — the only wedge that fits $25k–100k.
- **A6** tells the shutoff story in year 1 for near-zero cash, because "can you turn the water off?"
  is the first question every buyer asks.
- **A3** is the parallel cash business with the best margin and the lowest churn, and it is where
  your radio choice actually pays for itself.
- **A4** is free upside on hardware you already ship. **A7** needs no hardware at all. **A8** is the
  path to a software multiple and should start accumulating in P1.
- **A9** is a few-hundred-dollar spike with a published kill criterion.
- **A13** and **A14** are explicitly phase-3 products requiring a raise — and the pack says so in
  [08-roadmap.md](08-roadmap.md) rather than quietly assuming the money.
