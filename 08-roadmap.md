# 8. Product Technology Roadmap

Five phases, one hard exit gate each, fitted to **$25k–$100k of non-salary capex in the first 12
months** with **full in-house EE capability**.

---

## 0. How to read this roadmap

The budget is **non-salary**. Design labour is free because the team can do schematic, layout, RF
and enclosure in house. That inverts the usual startup cost structure: **what you spend money on is
test labs, listings, tooling, parts and lab time** — and that is what the phase budgets below count.

Three standing rules govern everything that follows. They are the three the team will want to break.

> **Rule 1 — No PCB layout in the first 90 days.**
> Free design labour is a trap, not an asset. It costs *calendar*, and calendar is the only thing in
> this plan that cannot be bought. Ten weeks of beautiful layout produces a board that cannot be
> validated, because there is no labelled data and no buyer to validate it against.
>
> **Rule 2 — No production run that is not pre-paid by a purchase order.**
> 1,000 units of parts is $15–40k. You cannot both certify and carry inventory on $100k.
>
> **Rule 3 — No customer-visible alert until the nuisance rate is measured.**
> Shadow mode first. An insurer's trust is spent once.

**The thesis of this document in one sentence:** certification is a $20–60k problem; the
company-ending problems are *no buyer will sign with a pre-seed vendor* and *a winter of labelled
data cannot be bought.* A roadmap organised entirely around certification optimises the $40k problem
and ignores the two fatal ones.

---

## 1. The two gates that set cost

Not three products — two binary questions about the bill of materials. Full detail in
[07-compliance-and-certification.md](07-compliance-and-certification.md).

| Option | Mains in enclosure | Wetted surface | Gates triggered | Cash **[E]** | Year 1? |
|---|---|---|---|---|---|
| **A. Clamp-on / puck leak sensor** (battery) | No | No | FCC Part 15, modular inheritance | **$31k–94k** | **Yes** |
| **B. Retrofit lever actuator** (no plumbing parts in box) | No | **No** | FCC; IAPMO scope *probably* N/A — get it in writing | $70k–231k | **Spike only** |
| **C. Gateway / bridge on a listed external adapter** | No | No | FCC 15B/15C | low | **Yes** |
| **D. Plug-in electrical fault detector** | **Yes** | No | FCC + UL 498 + UL 60730-1 + UL 94 | **$180k–560k** | No |
| **E. Inline motorised valve** | Yes | **Yes** | FCC + NRTL + IGC 115/349 + NSF 61/372 + cUPC | $112k–366k | No |

**A and C are your year-1 own-design hardware. B is a high-option-value spike, not a committed
product.** The shutoff *story* in the first pilots ships a third-party certified inline valve,
because that is what works and what insurers recognise.

---

## 2. The spine

| Phase | Window | Cash **[E]** | Entry condition | What gets built | **The single exit gate** |
|---|---|---|---|---|---|
| **P0 — Falsify the buyer** | wk 0–6 | **$6–14k** | — | Dev-kit nodes in real buildings; all four buyer routes worked in parallel; the 20-valve spike | A **countersigned** paid pilot or LOI from a named buyer for **≥50 units with a start date** |
| **P1 — Prove the alert** | wk 6–20 | **$12–28k** | P0 gate passed | Pilot on rebadged certified hardware + your own cloud; shadow-mode classifier; labelled corpus accumulating | **<0.5 nuisance alerts per device-year** over ≥90 days on ≥50 installed devices against a frozen eval set, **and ≥1 documented true event** |
| **P2 — Own the node** | mo 5–12 | **$25–55k** | P1 gate passed | Own-design clamp-on PCB on a pre-certified module; Zephyr + MCUboot OTA; FCC via modular inheritance | **FCC grant in hand**, and the first run shipped at **landed cost ≤60% of contracted unit price** |
| **P3 — Mains & water** | mo 12–24 | **$150–400k — requires a raise** | Funded | Plug-in detector and/or own valve; Matter for retail; multi-tenant carrier portal | A signed MSA with a carrier or program administrator **and ≥1 state rate/rule filing in progress** referencing the device |
| **P4 — Risk data product** | mo 24–36 | **requires a raise** | Funded | Portfolio risk scoring; claims triage; reinsurance-grade reporting | An **approved** filing, or a renewal at a measurable price uplift attributable to the device |

**P0–P2 total $43–97k — inside the envelope**, subject to Rule 2: it only closes if the production
run is customer-funded. **P3 is 2–4× the entire year-1 budget**, so "requires a raise" is a
structural fact, not a formality.

---

## 3. Phase detail

### P0 — Falsify the buyer · weeks 0–6 · $6–14k

**Objective:** find out whether anyone will sign, before spending a dollar on hardware you own.

**Build:** 20–30 dev-kit nodes (RAK19007 + RAK4631, $28/node) into **10–20 real buildings**. Ingest
pipeline skeleton with **raw retention from day one**. No enclosure, no PCB, no app — a 3D-printed
clip and a working uplink.

**Spend:** dev kits and gateways $2–4k · the **20-valve mechanical spike** $0.5–1k · **FTO opinion
$3–8k** · travel $0.5–1k.

**In parallel, and this is the actual work:** 25 buyer conversations across all four routes, with
one objective — a countersigned document. Send the **IAPMO scope-determination letter in week 1**.

**Do not:** lay out a PCB. Choose a radio. Build an app. Hire.

**Gate:** a countersigned paid pilot or LOI for ≥50 units with a start date.
**Continue** → P1. **Pivot** → switch buyer route (the fork in §6). **Stop** → if no route yields a
signature in 6 weeks, the product is not the problem and more engineering will not fix it.

### P1 — Prove the alert · weeks 6–20 · $12–28k

**Objective:** establish a defensible nuisance-alert rate on real housing stock, and start the
corpus.

**Build:** rebadge certified ODM hardware (Milesight/Dragino leak sensors, a third-party certified
valve for the shutoff story) under your own cloud. Classifier runs in **shadow mode** — logging
decisions, showing nobody. Install-verification telemetry is a **hard product requirement**, not a
nice-to-have, because placement error and sensor fault are indistinguishable in the data otherwise.

**Spend:** 50–100 rebadged units $3–8k · gateways $1–3k · cloud $0.6–1.5k · **GL + product liability
+ tech E&O binding $4–10k** · SOC 2 Type I readiness $3–6k.

**Do not:** ship an alert to a customer. Put a neural network on the node.

**Gate:** <0.5 nuisance alerts/device-year over ≥90 days on ≥50 devices against a **frozen**
evaluation set, plus ≥1 documented true event.
**Continue** → P2. **Pivot** → if nuisance rate is stuck, the sensing modality is wrong; try
acoustic or pressure before spending on your own board. **Stop** → if you cannot measure it at all,
your install verification failed and P1 repeats.

### P2 — Own the node · months 5–12 · $25–55k

**Objective:** own the hardware and the margin, at the cheapest possible certification burden.

**Build:** own-design clamp-on PCB on **RAK3172 ($5.99) or RAK11720 ($7.99)**, second-sourced to
**Seeed Wio-E5 ($6.49)**. Zephyr + MCUboot A/B OTA + mcumgr-over-BLE for field recovery. FCC via
modular inheritance — **read the module grant's antenna conditions before layout.**

**Spend:** FCC/ISED composite $1.5–5k · **LoRa Alliance $3k + certification $1.2k** · tooling
$8–25k · EVT/DVT and test fixtures $5–12k · pre-compliance scans $2–5k.

**Gate:** FCC grant in hand; first run at landed cost ≤60% of contracted unit price.

### P3 — Mains & water · months 12–24 · $150–400k · **requires a raise**

Plug-in electrical detector (UL 498 + UL 60730-1 + UL 94, **alert-only — never interrupt**) and/or
own inline valve (IGC 115/349 + NSF 61/372 + cUPC). Matter certification ($10.5k) for the retail
channel. Multi-tenant carrier portal. Actuarial evidence pack built on the surrogate metrics agreed
back in P0.

**Gate:** signed MSA plus ≥1 state filing in progress.

### P4 — Risk data product · months 24–36 · **requires a raise**

Portfolio risk scoring, claims triage, loss-avoided reporting, reinsurance interest. This is where a
hardware company earns a software multiple — and the only precedent is Adaptive + Whisker Labs
turning a sensor fleet into a parametric insurance trigger.

**Gate:** an approved filing, or a renewal at measurable price uplift.

---

## 4. Layer × phase

### Matrix A — what each layer looks like per phase

| Layer | P0 | P1 | P2 | P3 | P4 |
|---|---|---|---|---|---|
| **Sensing** | 2× TMP117 on dev kit | ODM sensors + probes | Own clamp-on PCB | + mains + wetted variants | Multi-peril fusion |
| **Edge compute / ML** | none — raw uplink | none — cloud shadow mode | CMSIS-DSP features | emlearn on-device | On-device anomaly |
| **Radio & WAN** | LoRaWAN bench | Radio chosen by buyer | One module family, 2nd-sourced | + Matter/Thread | + Sidewalk eval |
| **Device mgmt & OTA** | none | Golioth trial | **MCUboot + mcumgr/BLE** | Staged rollout + canary | Fleet SLA |
| **Cloud & data** | ingest + raw retention | TTS + VerneMQ + IoTDB | Magistrala + RBAC | Multi-tenant portal | Warehouse + feature store |
| **Analytics & risk model** | hand-labelled events | shadow classifier + frozen eval | tuned nuisance rejection | severity model | portfolio risk score |
| **Who signs the paper** | 25 conversations, 4 routes | **1 countersigned pilot** | aggregator contract | carrier MSA | carrier + reinsurer |
| **Compliance & cert** | **IAPMO scope letter** | E&O bound; SOC 2 Type I | **FCC grant** | NRTL + IGC + NSF | SOC 2 Type II |
| **Field ops & support** | you install, in person | install-verification telemetry | installer web app (mcumgr-web) | dispatch SLA | regional partners |

### Matrix B — build / use / partner, and the reasoning

`B` = build it · `U` = use something existing · `P` = partner · `—` = not yet

| Layer | P0 | P1 | P2 | P3 | Why |
|---|---|---|---|---|---|
| Sensing | U | U | **B** | B | Commodity until the clamp mechanics and ΔT calibration become yours |
| Edge compute / ML | — | — | U | **B** | **No NN on the node before P2.** You cannot compress a model you have not yet trained on data you do not yet have |
| Radio & WAN | U | U | U | U | **Never build a radio.** Modular inheritance is worth $10–25k per SKU |
| Device mgmt & OTA | — | **U** | **U** | **U** | **Never build this.** Golioth is Apache-2.0 with PKI rotation; hawkBit is the self-host escape hatch |
| Cloud & data | B | B | B | B | Cheap, and it is where the carrier-facing differentiation lives |
| Analytics & risk model | **B** | **B** | **B** | **B** | **The only layer that is actually the product** |
| Who signs the paper | B | **P** | **P** | P | Pre-seed vendors do not clear carrier vendor qualification; go via vipHomeLink / Asurion / HSB |
| Compliance & cert | P | P | P | P | Labs and NRTLs, always |
| Field ops & support | B | B | P | P | Install quality decides your false-positive rate, so own it until it is a playbook |

---

## 5. Critical path

**The critical path is not hardware.**

```
  buyer signature ──► installed base ──► ONE FREEZE/THAW CYCLE ──► defensible
                                          OF LABELLED DATA          nuisance rate
                                                                         │
                                          security / procurement ◄───────┘
                                          review ──► state filing

  ──────────────── parallel, must merely not be late ────────────────
  module choice ──► PCB ──► pre-compliance ──► FCC lab queue ──► grant ──► tooling
```

FCC and PCB sit on a **parallel** path. The non-compressible item is **calendar time in the field
across a winter** — cash cannot buy it and parallelism cannot shorten it. **Therefore the first 30
days exist to get devices into buildings**, and that is the entire justification for Rule 1.

A visual version of this, with the two non-compressible bars drawn behind everything else, is slide
21 of [slides/main.tex](slides/main.tex).

**Long-lead items** are tabulated in [07](07-compliance-and-certification.md) §7.7. The three that
most often surprise people: **a state rate/rule filing is 6–18 months per state per carrier**;
**SOC 2 Type I → Type II is 9–18 months end to end**; and **a module second-source is a new FCC
grant**, so a supply disruption costs a test campaign, not a part change.

**The seasonality trap:** miss October and you do not slip a quarter, you slip to the next winter.
Treat "devices in the field by October" as a calendar deadline, not a sprint goal.

---

## 6. Decision points where the roadmap forks

### Fork A — buyer route · decide at the P0 gate

| Option | Consequence |
|---|---|
| **Insurer / MGA** via aggregator | Hardware paid for, near-zero churn, but 18–30 months and a vendor bar (E&O, security review, often SOC 2) |
| **Property manager / multifamily** | Weeks-to-months cycle, upfront cash, **asset-attached churn**, insurability as the trigger |
| **DTC** | Fastest revenue, worst CAC, you fund the hardware, and you are selling risk transfer not sensors |
| **Builder / utility / OEM** | Lowest marketing spend, heavy price pressure, long design cycles; the Verisure-shaped licensing exit |

**How to decide:** whichever route produces a countersigned document first. Run all four in P0;
do not pre-commit.

### Fork B — the shutoff story · decide at the P0 gate

Resell a certified inline valve (**recommended for year 1**) · own-design retrofit lever actuator
(only if the 20-valve spike passes) · advisory-only with no actuator (cheapest, and often what
commercial buyers actually want).

### Fork C — radio · decide at P1, **and it is downstream of Fork A**

This is the load-bearing dependency most roadmaps get wrong:

- **Multifamily** → building gateway, **LoRaWAN wins** (one $382 gateway across dozens of $12 nodes)
- **DTC** → **BLE / Thread / Matter** (zero infrastructure, retail shelf appeal)
- **Carrier-mailed to a homeowner** → **LTE-M or BLE-plus-phone**, because **you cannot install a
  gateway in someone's house by mail**

So: lock the module *ecosystem* in week 1, lock the *radio* at week 6. See
[04-connectivity.md](04-connectivity.md) §4.4.

### Fork D — build model · decide at P2

Own-design · ODM rebadge · white-label · licence the analytics only. Costed in
[09-build-vs-whitelabel.md](09-build-vs-whitelabel.md).

---

## 7. Falsification plan — the four claims that kill the company

Each claim gets a test, a pass threshold, and what you do when it fails.

**Claim 1 — A pre-seed vendor can get a buyer signature within 6 weeks on *some* route.**
*Test:* 25 conversations across all four routes in P0.
*Pass:* one countersigned paid pilot or LOI for ≥50 units with a start date.
*On failure:* route via an MGA, program administrator, loss-control firm or reseller of record.
Property managers are the fallback that can actually transact without a DOI filing. If no route
signs, stop — this is the most likely single cause of death.

**Claim 2 — Nuisance alerts can be driven below 0.5 per device-year on real housing stock.**
*Test:* 90 days, ≥50 installed devices, frozen evaluation set, shadow mode.
*Pass:* <0.5/device-year, falling week over week.
*On failure:* the sensing modality is wrong. Try acoustic or pressure before spending on your own
board. Do not ship alerts to a customer to "get feedback".

**Claim 3 — A retrofit lever actuator can cycle real aged valves.**
*Test:* twenty salvaged main shutoff valves of mixed age, type and size.
*Pass:* **>70% cycle without stem weep**, and the multi-turn gate-valve fraction is tolerable.
*On failure:* kill option B permanently and resell a certified valve. Cost of the test: a few hundred
dollars.

**Claim 4 — The labelled corpus measurably improves nuisance rejection.**
*Test:* model performance per 1,000 additional device-months, held-out by region.
*Pass:* a measurable improvement curve that has not flattened.
*On failure:* **say so out loud and change the story.** The honest fallback is that the moat is the
carrier relationship, the installed base and the claims-workflow integration — which is a perfectly
good moat, and claiming a data moat you do not have is how you lose a carrier's actuarial team.

---

## 8. Risk register

Sorted by likelihood × impact. **Watch the leading indicator, not the risk.**

| # | Risk | L | I | **Leading indicator** | Mitigation |
|---|---|---|---|---|---|
| 1 | **No buyer will sign with a pre-seed vendor** | H | fatal | No countersigned doc by week 6 | Route via MGA / program administrator / loss-control firm / reseller of record. Run all four routes in P0. Property managers can transact without a filing |
| 2 | **False positives destroy insurer trust** | H | high | Nuisance alerts per device-year not falling week over week | Shadow mode; one-click "this was nothing"; install-verification telemetry so placement error is separable from sensor fault |
| 3 | **Loss reduction cannot be proven at this scale** | H | high | Pilot below ~2,000 house-years | **Agree a surrogate metric in writing in P0** — time-to-shutoff, mitigated-event case studies, severity model. Frequency studies need tens of thousands of house-years |
| 4 | **Install quality / the self-install failure tail** | H | medium | >10% of shipped units never reporting 7 days out | Install-verification telemetry as a hard requirement; **do not count unverified installs** |
| 5 | **LoRaWAN downlink latency makes remote shutoff unreliable** | H | high *if shutoff is promised* | Any architecture doc that has the cloud closing the valve | **Architectural, not operational:** LoRaWAN is uplink-only for alerts; the shutoff decision lives locally or in the valve. Class C burns the power budget |
| 6 | **Premium credit is unfilable or barred by anti-rebating rules** | M | fatal to the model | Carrier counsel will not commit to a filing path | **Sell loss control, not a discount** — NAIC Model #880 permits the giveaway with no filing. Treat premium credit as upside, never as the plan |
| 7 | **Battery life in a cold basement** | H | medium | Measured pulse behaviour at −10 to 0 °C | **Li-SOCl2 with a hybrid-layer capacitor, never coin cells** for the main SKU; derate datasheet capacity ~40%; **measure rather than model** |
| 8 | **Module EOL, and a second source invalidates the FCC grant** | M | high | Vendor PCN, or lead time >16 weeks | Pre-identify a footprint-compatible alternate from the **same** vendor (RAK3172 ↔ Wio-E5 is cross-vendor but same die); **budget the re-test as a known cost**; hold 12 months of stock after the PO |
| 9 | **Valve fails closed (dead sump, frozen pipes) or fails open (silent non-protection)** | M | high | Any unit arming above the breakaway-torque threshold | **Refuse to arm** above threshold; periodic exercise cycle; documented **fail-as-is** behaviour; mechanical override. Never sell shutoff as the primary benefit. Note the unlisted variant: **a false shutoff in a commercial building is a business-interruption claim against you** |
| 10 | **The data moat is not defensible** | M | high | Model performance flat per 1k device-months | Say plainly that the moat is the carrier relationship, installed base and claims workflow. **Make data rights explicit in every pilot contract** |
| 11 | **ODM dependency and landed-cost volatility** | M | medium | Landed cost moving >15% | Own the firmware and test fixtures even when the ODM builds; dual-quote a North American CM at P2; classify HTS early |
| 12 | **Seasonality makes schedule slip non-linear** | M | high | Devices not in the field by October | Treat October as a calendar deadline. **A 6-week slip costs 12 months.** Write down the founder time commitment now — if anyone takes contract work in month 7, you slip a winter |
| 13 | **US Cyber Trust Mark or state IoT security law becomes a procurement requirement** | L | medium | A carrier RFP mentioning it | Low cash to comply with NIST IR 8425 capabilities now; **do not budget for the Mark until its status is confirmed** |

---

## 9. KPI and telemetry contract per phase

What you measure, what you promise, and — the column that makes this credible — **what you refuse to
promise.**

| Phase | Measure | Promise to the buyer | **Explicitly refuse to promise** |
|---|---|---|---|
| **P0** | Uplink success rate; install time; event count | Nothing. It is a pilot | Any detection rate, any loss reduction |
| **P1** | Nuisance alerts/device-year; time-to-detection; % never-reporting | Shadow-mode reporting; mitigated-event case studies | Claim-frequency reduction; a specific detection sensitivity |
| **P2** | Alert precision/recall on frozen eval; battery life measured cold; OTA success rate | Detection SLA on defined leak classes; 5-year battery under stated duty cycle | Detection of leak classes not in the eval set; anything about the customer's plumbing you cannot see |
| **P3** | Severity avoided; time-to-shutoff; dispatch SLA | Loss-control service levels; data-governance commitments | **A filed premium discount** (that is the carrier's to file); frequency-reduction significance at pilot scale |
| **P4** | Portfolio risk-score lift; filing outcomes | Risk-scoring accuracy against held-out claims | Underwriting decisions — you are a vendor, not a producer |

---

## 10. What we will **not** build

- **No inline valve in year 1.** Triggers every gate at once; resell a certified one.
- **No mains-powered product in year 1.** NRTL is $15–40k plus annual factory inspections.
- **No arc-fault *interruption*, ever** without a deliberate decision — it puts you in UL 1699 at
  +$60–200k. Alert only, like Ting.
- **No own device-management or OTA backend.** Golioth is Apache-2.0 with PKI rotation; hawkBit is
  the self-host fallback. This is a solved problem and building it is pure dilution.
- **No neural network on the node before P2.** Use CMSIS-DSP features and emlearn; a 2 kB forest
  that runs five years beats a 200 kB CNN that runs eight months.
- **No FUOTA over LoRaWAN** until there is no alternative — unicast updates at DR0 blow the airtime
  budget, and `chirpstack-fuota-server` exists when you need it.
- **No own LoRaWAN network server before ~1,000 nodes.** TTS Open Source on a $18/mo VPS, or TTI
  Discovery free.
- **No Matter before a DTC buyer exists.** $10.5k and a mandatory test house buys retail shelf
  appeal and nothing an insurer cares about.
- **No AGPL or GPL anywhere in the product path.** ESPHome's device-side C++ is GPLv3; Tasmota,
  IotaWatt, Grafana, Meshtastic, zigbee2mqtt and LocalTuya are all copyleft. See
  [05](05-open-source-stack.md) §5.2.
- **No custom gateway hardware.** RAK sells them from $99 with certificates included.
- **No premium-share or MGA structure in year 1.** It needs producer licensing per state; it is the
  main upside lever and correctly a year-2+ decision.

---

## 11. The first 90 days

| Weeks | Do | "Done" means |
|---|---|---|
| **1** | Lock the **module ecosystem** (not the radio). Send the **IAPMO scope-determination letter**. Stand up ingest with raw retention | A written ecosystem decision with rationale; letter sent; first byte stored |
| **1–2** | Commission **FTO opinion** on the clamp-on thermal family | Counsel engaged, scope defined |
| **2–6** | **20–30 dev-kit nodes into 10–20 real buildings** | Nodes uplinking from real basements; install time measured |
| **2–12** | **25 buyer conversations across all four routes** | A countersigned pilot or LOI for ≥50 units |
| **6–12** | **The 20-valve torque-and-fit spike** on salvaged valves | A pass/fail number against the >70% threshold |
| **8–12** | Bind GL + product liability + tech E&O; start SOC 2 Type I readiness | Quotes in hand; readiness assessment booked |
| **Throughout** | **No PCB layout** | No Gerbers exist |

---

## 12. Budget roll-up

| Category | P0 | P1 | P2 | Total |
|---|---|---|---|---|
| Dev kits, gateways, samples | $2–4k | $1–3k | — | $3–7k |
| Rebadged pilot units | — | $3–8k | — | $3–8k |
| Mechanical spike (valves) | $0.5–1k | — | — | $0.5–1k |
| Legal: FTO opinion | $3–8k | — | — | $3–8k |
| Insurance: GL + PL + E&O | — | $4–10k | — | $4–10k |
| SOC 2 readiness | — | $3–6k | — | $3–6k |
| Certification (FCC/ISED, LoRa Alliance + cert) | — | — | $5.7–11.2k | $5.7–11.2k |
| Tooling | — | — | $8–25k | $8–25k |
| EVT/DVT, test fixtures, pre-compliance | — | — | $7–17k | $7–17k |
| Cloud | $0.2–0.5k | $0.6–1.5k | $1.2–2.5k | $2–4.5k |
| Travel | $0.5–1k | $0.5–1.5k | $0.5–1.5k | $1.5–4k |
| **Phase total** | **$6–14k** | **$12–28k** | **$25–55k** | **$43–97k** |

**The caveat that makes or breaks this.** The envelope only closes because **no production inventory
is in it.** 1,000 units of parts is $15–40k. Rule 2 exists because you cannot both certify and
inventory on $100k — the first run must be against a purchase order. If that is not achievable, the
honest plan is to stay on rebadged hardware through P2 and defer your own PCB, which costs margin
but not survival.

---

## 13. Where each number comes from

| This roadmap asserts | Sourced from |
|---|---|
| Certification costs and gate structure | [07-compliance-and-certification.md](07-compliance-and-certification.md), `data/cert-costs.csv` |
| Module prices and the dev-kit → production pairing | [06-hardware-and-devkits.md](06-hardware-and-devkits.md), `data/bom-estimates.csv` |
| $49–60/home/year pricing, CAC/LTV, churn, 18–30 month cycles | [03-business-models.md](03-business-models.md) — Ondo InsurTech's audited filings |
| NAIC Model #880 and the no-filing giveaway | [03-business-models.md](03-business-models.md) §3.0 |
| Aggregator route (vipHomeLink, Asurion, HSB) | [01](01-market-and-insurance-case.md) §1.4, [03](03-business-models.md) §3.5 |
| Nuisance-rate framing and the attribution limit | [03](03-business-models.md) §3.6 |
| Radio choice per buyer route; the downlink problem | [04-connectivity.md](04-connectivity.md) |
| Apache-2.0 stack and licence exclusions | [05-open-source-stack.md](05-open-source-stack.md), `data/repos.csv` |
| Application verdicts and the 20-valve kill criterion | [02-application-portfolio.md](02-application-portfolio.md), `data/application-scores.csv` |

---

## 14. Assumptions, and what would change this document

**Assumptions:** US-first with Canada deferred · no producer/MGA licence in year 1 · the team
genuinely has EE bandwidth without salary cost · founder time is committed through a full winter ·
dollar figures are as of **2026-10-14** and the certification ones are **[E]** estimates, not quotes.

**Findings that would force a rewrite:**

1. **The FTO opinion comes back adverse** on the clamp-on thermal family. Then the wedge changes to
   acoustic or pressure sensing, and P2 slips a quarter while the front end is re-derived.
2. **IAPMO's scope determination says a retrofit actuator *is* in scope.** Then option B's cost
   triples and it is dead rather than a spike.
3. **An aggregator will not take a pre-seed vendor either.** Then route (b), property managers,
   stops being the parallel cash business and becomes the only business, and the roadmap re-centres
   on buildings.
4. **Nuisance rate plateaus above 0.5/device-year** on real housing stock. Then this is a severity
   and triage product, not a prevention product, and the pitch becomes claims handling rather than
   loss control.
5. **A carrier mandates FM Approvals** for commercial. Then add an unbudgeted listing and 3–6 months,
   and talk to FM in P1 rather than P3.
6. **The US Cyber Trust Mark becomes mandatory in a carrier RFP.** Low cash impact, but confirm the
   program's live status before budgeting — it could not be verified for this pack.
