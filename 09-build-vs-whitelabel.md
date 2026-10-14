# 9. Build vs White-Label — Four Models, Costed for an In-House-EE Team

All four approaches evaluated, then sequenced. The team's full EE capability changes the answer from
what it would be otherwise.

---

## 9.0 Why in-house EE changes the calculus

The usual advice to a pre-seed hardware startup is "rebadge first, because hardware design is
expensive." That advice assumes you are paying an agency $80–150k for a design cycle. **You are
not** — schematic, layout, RF and enclosure are in house, so design labour costs *calendar* rather
than cash.

**What remains expensive is the stuff EE skill cannot discount:** test-lab slots, NRTL listings,
plumbing listings, injection-mould tooling, parts, and the certification re-test that a second-source
module triggers. Those costs are identical whether your layout is brilliant or mediocre.

So the honest framing is: **own-design hardware is reachable for you in year 1 — but only in the
regulatory quadrant that avoids NRTL and plumbing listings**, and only if you do not spend the
calendar you saved on a board nobody can yet validate. Hence Rule 1 in
[08-roadmap.md](08-roadmap.md): no PCB layout in the first 90 days.

---

## 9.1 The four models

| | **1. Own-design on a pre-certified module** | **2. ODM rebadge** | **3. White-label / licence out** | **4. Software and analytics only** |
|---|---|---|---|---|
| What you ship | Your PCB, your enclosure, your firmware | Someone else's certified device with your logo and cloud | Your technology inside a partner's product | Nothing physical |
| NRE **[E]** | **$31–94k** (option A) | **$2–10k** | $0 — the partner pays | $0 |
| Time to first revenue | 6–9 months | **3–5 months** | 12–24 months | **3–6 months** |
| Gross margin | **55–70%** | 25–40% | Royalty, 5–15% of partner revenue | **80%+** |
| BOM control | **Full** | None | None | n/a |
| FCC path | **Modular inheritance** | Inherited or transferred | Partner's | n/a |
| Differentiation | **High** | **Low** — competitors buy the same SKU | Medium — your algorithm is the product | High if the model is good |
| Biggest risk | Calendar, and the certification re-test on a second source | Vendor lock-in; landed-cost volatility | 12–24 month partner cycle with no revenue | You do not control the sensor you depend on |
| Fits $25–100k? | **Yes, for option A only** | **Yes, comfortably** | Yes (no capex, but no near revenue) | **Yes** |

---

## 9.2 Model 1 — Own-design on a pre-certified module · **the year-1 hardware answer**

**Use a module, never bare silicon.** The whole argument in one comparison: **STM32WLE5CCU6 is
$4.17 @10k [V]** but has **no modular approval**, so you own the entire FCC filing — **$6–15k and
6–12 weeks per SKU [E]**. **RAK3172 is $5.99 [V]** with downloadable FCC, ISED, CE and LoRa Alliance
certificates, reducing you to composite testing at **$1.5–5k [E]**. **The ~$1.80 per-unit saving
costs five figures and two months.** At 10,000 units the bare-silicon saving is $18k — roughly what
the extra certification costs, with all the risk on your side.

**The dual-source trick that actually works:** **RAK3172 ($5.99) and Seeed Wio-E5 ($6.49)** are the
**same STM32WLE5 die from different vendors.** That is a genuinely dual-sourced BOM, which matters
because risk 8 in [08](08-roadmap.md) §8 — module EOL triggering a new FCC grant — is the supply risk
most likely to hit you at the worst moment. Note it is not free: a different module is a different
grant, so budget the re-test as a known line item rather than a surprise.

**Read the module grant before you lay out the board.** Module grants carry antenna type and trace
conditions. Violate them and you forfeit modular approval and owe a full Part 15C campaign — the
most expensive unforced error available to a team that is otherwise doing everything right.

**Stay in the cheap quadrant.** Option A (clamp-on, battery, no wetted surface) is $31–94k. The
moment you add mains you add $15–40k of NRTL plus annual factory inspections; the moment you add a
wetted surface you add $25–80k of plumbing listing. See
[07-compliance-and-certification.md](07-compliance-and-certification.md).

---

## 9.3 Model 2 — ODM rebadge · **how you run the P1 pilot**

### The certification question that determines whether this is cheap

**FCC certification genuinely transfers on a rebadge, and this is the single biggest capex saving
available.** Per **47 CFR §2.933**:

> *"A new application for certification shall be filed whenever there is a change in the FCC
> Identifier for the equipment with or without a change in design, circuitry or construction.
> However, **a change in the model/type number or trade name performed in accordance with the
> provisions in §2.924 … is not considered to be a change in identification and does not require
> additional authorization.**"*
> ([Cornell LII, 47 CFR 2.933](https://www.law.cornell.edu/cfr/text/47/2.933))

Where there is no design change, a **streamlined Change-in-ID application** suffices, requiring the
original equipment identification, the grant date, how the rebranded equipment differs, whether
prior test results remain applicable, and exterior photographs showing the new identification label.

**So you can ship a rebadged radio under your own trade name** using either the original grantee's
FCC ID (the §2.924 route) or your own FCC ID via Change-in-ID **with the original grantee's written
permission and no retest.** No RF lab spend.

**Safety listing is the part that does not transfer cheaply.** A "multiple listing" mechanism is the
standard industry route for selling another manufacturer's certified product under your brand, but
**both UL pages describing it returned 404 and the program name, requirements and fees are
unverified — confirm directly with UL Solutions or Intertek.** This matters most for anything
mains-powered.

### What is actually available in North America

| Supplier | What you can rebadge | Certification posture | MOQ / NRE |
|---|---|---|---|
| **Tuya** | **OEM App** (no-code white-label app, 50+ templates, 2,400+ device categories, built-in e-commerce and subscription, multi-region data centres), TuyaOS firmware, IoT Core cloud, explicit "Create OEM Products" flow | Runs a **Product Certificate service** — *"a product needs a series of certifications to get a sales permit… Tuya provides multiple hardware certification support services"* — plus Works-With and Matter certification as paid add-ons | **Not published.** Quote-only |
| **Milesight** | WS303 mini leak sensor, EM300-SLD/ZLD leak sensors, **UC51x LoRaWAN solenoid valve controller**, UG63 gateways, WS523 portable metered socket | Leak sensors carry **CE/FCC/ISED/RCM/Telec/ICASA**. ⚠️ **WS51x wall socket is EU/FR plug and CE-only; WS523 has a US 15 A variant with FCC — but neither is UL/ETL listed** | Channel and Ecosystem Partner programs; **no MOQ/NRE published** |
| **Dragino** | LWL01/02/03A/04 leak sensors, LDS01/02 door sensors, water meter and flow sensors, **LT-22222-L I/O controller (2× relay 5 A @250 VAC)**, LPS8v2 gateway | OEM/ODM scope is **only**: customised enclosure logo, customised package design, module design assistance, manufacture/assembly. **No certification support, firmware customisation, MOQ, NRE or lead time published** | Quote-only, Shenzhen-based |
| **Chinese motorised ball valves** | Deep commoditised supply of DN15/20/25 2-way motorised ball valves with "OEM supported" flags — Hangzhou Frankever (which has both a **Matter** smart water valve and a **LoRaWAN IP67 shut-off controller**), TONHE, Ouming, Shenzhen Power-Tomorrow (DC 9–35 V with manual override), jlmeter. Some offer **NSF 61 stainless** options | **No FCC/UL/IAPMO listings advertised.** Assume you fund certification for anything mains-connected or plumbing-inline | **Not published** |
| **Shelly (Allterco)** | ⚠️ **No OEM/ODM or private-label program page exists** — only official resellers and verified installers. Product line is switches/dimmers/energy meters; **no water leak sensor or valve found** | — | **Unverified — do not assume an OEM program exists** |
| NetVox, Browan, Sonoff/ITEAD | **Unverified** — sonoff.tech/oem-odm 404s; no NetVox or Browan data retrieved | — | — |

### Real precedents in this exact market

**Verisure rebadging Ting for Europe** (licence plus own manufacturing), **Asurion wrapping Phyn
hardware as "LeakProtect"**, **Resideo supplying First-Alert-branded leak devices into USAA's
channel**, and **GWA Group taking Phyn exclusively for Australia and New Zealand.** The rebadge and
licence play is not a startup compromise in this industry — it is how the top of the market operates.

**Verdict:** this is how you run P1. It gets certified hardware into real buildings in 3–5 months
for $2–10k, which buys you the labelled corpus and a pilot contract while your own PCB is still a
schematic. It is a **means to the data and the contract**, not a long-term position — margin is
25–40% and a competitor can buy the identical SKU tomorrow.

---

## 9.4 Model 3 — White-label / licence out · **the capital-efficient endgame**

You supply technology, firmware and cloud; a partner manufactures, sells, installs and supports.
**This is exactly the Verisure ← Ting Labs deal** (September 2026): exclusive Europe, co-development
and licensing, subscription-based, launching early 2027. Ting built the algorithm and the cloud for
a decade and then let someone else own the channel in a whole continent.

**Why it is attractive for you:** zero capex, zero install network, zero support burden, and it
works *because* the hard asset is the detection method and the cloud rather than the plastic. If A1's
analytics prove out, a licensing deal with a security-monitoring incumbent is a legitimate exit from
the capital treadmill — and it is available to a small team in a way that national retail
distribution never is.

**Why it cannot be your year-1 plan:** a 12–24 month partner cycle with no revenue, and you need
something worth licensing first — which means you need P1's measured nuisance rate and P2's
shipping product before the conversation is real. **Keep it as the P3/P4 option, and do not sign
exclusivity early.**

---

## 9.5 Model 4 — Software and analytics only

Build the risk-scoring, claims-triage and insurer-reporting layer on devices other people make and
certify. **Zero hardware risk, 80%+ margin, 3–6 months to revenue.**

**The real gate is not technical.** It is **SOC 2 and a carrier security review** — 3–6 months for
Type I and another 6–12 for Type II. Start it in P1, not when a carrier asks.

**The structural weakness is real:** you do not control the sensor you depend on, so your detection
quality is capped by someone else's hardware, and your supplier can become your competitor. That is
precisely what Asurion did to the device vendors — it owns the brand, the plumber network, the
monitoring and the carrier relationships, and Phyn is a component.

**But this is not an either/or.** Application A8 in [02](02-application-portfolio.md) is this model
running *in parallel* with your hardware, and it is the route to a software multiple on a hardware
company. Adopt **openIDS Homeowners v1.0** as the outbound schema — LF-governed, royalty-free patent
commitments, and the Community Specification Licence explicitly exempts *implementations* from its
attribution terms, so closed-source implementation is fine. There is **no open real-time
property-risk telemetry schema**, which is a cheap standard-setting opportunity: publishing one
(device → property → peril event → mitigation action → verified loss avoided) costs a week and could
become the format your competitors implement.

---

## 9.6 Recommendation: sequence all four

**Not a choice — a sequence.** Each model is correct for a different phase.

| Phase | Model | Why |
|---|---|---|
| **P0** (wk 0–6) | Dev kits, no model yet | Prove a buyer exists before owning anything |
| **P1** (wk 6–20) | **2 — ODM rebadge** | Certified hardware in buildings for $2–10k; buys the corpus and the contract while your PCB is a schematic |
| **P1 in parallel** | **4 — software only** | Start the analytics and SOC 2 clock immediately; it is the layer that is actually the product |
| **P2** (mo 5–12) | **1 — own-design on a pre-certified module** | Margin goes 25–40% → 55–70%; option A only, staying out of NRTL and plumbing scope |
| **P3/P4** | **3 — licence out** | Once the analytics are proven, the Verisure-shaped deal is the capital-efficient way to reach a continent |

**The failure mode to avoid is picking model 1 in P0** — which is exactly what a team with idle EE
capability will want to do. Owning the hardware before you own a buyer or a dataset converts your
one unbuyable resource, calendar, into a board you cannot validate.

### The 90-day execution plan

Weeks 1–2: **lock the module ecosystem** (not the radio), send the **IAPMO scope letter**, commission
the **FTO opinion**, stand up ingest with raw retention.
Weeks 2–6: **20–30 dev-kit nodes into 10–20 real buildings**; quote two ODM suppliers for P1.
Weeks 2–12: **25 buyer conversations across all four routes**, targeting one countersigned document.
Weeks 6–12: the **20-valve torque-and-fit spike**; bind E&O; start SOC 2 readiness.
Throughout: **no PCB layout.**
