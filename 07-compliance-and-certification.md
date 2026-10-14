# 7. Compliance and Certification — North America

The binding cash constraint for a team with in-house EE. Researched 2026-10-14.
**[V]** = vendor- or body-published; **[E]** = estimate, not a quote.

---

## 7.0 Correction: the standard in the original brief is the wrong document

**ASSE 1087-2026 is *"Commercial and Food Service Water Treatment Equipment Utilizing Drinking
Water"*** ([asse-plumbing.org](https://asse-plumbing.org/standards/product-standards)). It has
nothing to do with leak detection or shutoff. ASSE does publish over 50 plumbing product standards,
but 1087 is not the one.

**The standards that actually govern these products are IAPMO IGC**
([iapmostandards.org](https://www.iapmostandards.org/iapmo-industry-standards/current-iapmo-igc-standards)):

| Standard | Title | Scope |
|---|---|---|
| **IGC 115-2013e1** | **Automatic Water Leak Detection Devices** | NPS-1/4 to NPS-10 |
| **IGC 241-2019** | **Water Flow Shut-Off Devices** | Mechanical automatic supply shutoff for water heaters, bathtubs, showerheads, lavatories, sinks, water closets |
| **IGC 261-2015** | **Excess-Flow Shutoff Devices** | Water supply systems |
| **IGC 349-2018e1** | **Electronic Plumbing Supply System Integrity Protection Devices** | Electronic detection devices protecting plumbing supply integrity — **the one that best describes a Flo/Phyn-class product** |
| IGC 200-2017e2 / IGC 128-2019 | Overflow pans; water-heater shutoff valves with thermal expansion control | Adjacent; possibly relevant to SKU variants |

Certifying to the wrong document is an expensive mistake made early. If you take one action from
this document, make it **send a written scope-determination request to IAPMO R&T in week 1** (see
§7.6).

---

## 7.1 The two gates that set your cost

```
   Is there MAINS VOLTAGE inside your enclosure? ──► NRTL listing
                                                     UL 498 + UL 60730-1 + UL 94
                                                     $15k–40k [E] + annual factory inspections

   Is there a WETTED SURFACE in your BOM? ─────────► NSF/ANSI 61 + NSF/ANSI 372
                                                     + IAPMO IGC 115/349 + UPC listing
                                                     $25k–80k [E] + a licensed plumber to install
```

| Product | Mains | Wetted | Plumbing listing | Licensed plumber | Total **[E]** |
|---|---|---|---|---|---|
| **Clamp-on thermal sensor** | No | **No** | **None** | **No** | **$31k–94k** |
| **Retrofit clamp-over-lever actuator** | No | **No** | Probably none — *get it in writing* | **No** | $70k–231k |
| **Gateway / bridge on a listed external adapter** | No | No | None | No | low |
| **Plug-in electrical detector** | **Yes** | No | None | No | **$180k–560k+** |
| **Inline motorised valve** | **Yes** | **Yes** | **IGC 115/349 + NSF 61 + NSF 372 + UPC/cUPC** | **Effectively yes** | $112k–366k |

**The retrofit actuator is the interesting case, and the analysis is worth following.** NSF/ANSI 61
and 372 are scoped to *wetted surfaces* — 61 to what leaches into drinking water, 372 to the ≤0.25%
weighted-average lead content of surfaces contacting potable water. A bracket that bolts to the
outside of an existing valve and turns its handle **has no wetted surface at all**; the host valve
keeps its own listing. The IAPMO test regimes are built around a device installed *in* the supply
line — hydrostatic and burst pressure, flow and pressure loss, leakage, endurance cycling under line
pressure — and an external actuator has **no pressure boundary**, so most of that matrix is
physically inapplicable. Battery or low-voltage DC with an externally supplied UL-listed wall
adapter keeps you out of NRTL scope too, because the adapter's certification is the adapter vendor's
problem.

**So on paper the retrofit actuator lands in the same cheap regulatory class as the clamp-on
sensor** — a stronger conclusion than the original three-product framing allowed. Two caveats:
"inapplicable tests" is not the same as "out of scope", and a certifier could reasonably argue the
*assembly performing automatic shutoff* is the device under test. Hence §7.6. And the blocker turns
out to be mechanical rather than regulatory — see [02](02-application-portfolio.md) §2.3.

**One hard product-definition rule:** the moment the SKU contains a valve, adapter, union or
tailpiece, that part needs NSF 61/372 and the kit becomes a plumbing product. **Actuator only, no
plumbing parts in the box.** Write it into the BOM policy.

---

## 7.2 Radio — FCC Part 15 Subpart C and ISED

**Modular approval is the whole reason to buy a pre-certified module** rather than bare silicon.
Vendor evidence is concrete: RAK publishes per-module **FCC and ISED certificates as direct
downloads**; Espressif maintains a **482-document certificate index**; Raytac states FCC/IC/CE/UKCA/
MIC/KC/SRRC/NCC/RCM per module.

**Published lab pricing is rare — most labs quote only.** Sunfire Testing publishes
**EMC from $999** and **wireless testing from $2,499** covering *"FCC Part 15C, CE, Industry Canada
and any other regulatory markets"* for Wi-Fi/BT/BLE/Zigbee/**LoRa**/cellular, as *"all-inclusive
project pricing, with unlimited re-testing"* including reports, labelling guidance and
**certification filing fees** ([sunfiretesting.com](https://www.sunfiretesting.com/)) **[V]**.

| Scenario | Cost **[E]** | Timeline **[E]** |
|---|---|---|
| **Inheriting a module's grant** (unchanged module, vendor antenna, correct labelling and OEM integration instructions) — you still owe composite/unintentional-radiator testing of the finished product | **$1,500–5,000 per SKU** | 2–4 weeks |
| **Full intentional-radiator certification of your own radio design** (bare STM32WLE5 + your antenna) | **$6,000–15,000 per SKU** incl. ISED | 6–12 weeks |
| **Class II permissive change** (e.g. you change the antenna) | low four figures | — |

**Three traps:**

1. **Module grants carry antenna type and trace conditions. Violate them and you forfeit modular
   approval** and owe a full Part 15C campaign. Read the grant before the layout, not after.
2. **Three radios × three products = nine filings.** A LoRa sensor, a LoRa+BLE valve and a
   Wi-Fi/Thread plug are independent filings. **Standardising on one module family across products
   is worth five figures of avoided cost** — and is the §6.0 argument restated in dollars.
3. **A different module is a different FCC grant.** So a supply disruption does not cost you a part
   change, it costs you a test campaign and 4–10 weeks of lab queue, at exactly the moment you have
   no cash. Pre-identify a footprint-compatible alternate from the **same vendor**, and budget the
   re-test as a known line item.

> **Unverified:** FCC application and permissive-change fees (every fcc.gov URL returned 403), and
> ISED procedure specifics including the Canadian-representative requirement and CN number (the
> ISED pages reachable were a list of *rescinded* SPR documents).

---

## 7.3 Safety listing for mains-powered products

**You are not locked to UL. OSHA currently recognises 21 NRTLs**
([osha.gov](https://www.osha.gov/nationally-recognized-testing-laboratory-program/current-list-of-nrtls)):
UL, **Intertek (ETL)**, CSA Group, TÜV Rheinland NA, TÜV SÜD America, **FM Approvals**, **IAPMO
EGS**, **NSF International**, Bureau Veritas, DEKRA, Element, Eurofins, Nemko, QAI, **QPS**, SGS,
BACL, JET, LabTest, SolarPTL, SwRI.

Note that **NSF and IAPMO EGS are themselves NRTLs** — so one body can often scope both your
plumbing listing and your electrical listing. That is a real procurement simplification.

Applicable standards, with verified titles and document prices **[V]**
([shopulstandards.com](https://www.shopulstandards.com/)):

| Standard | Title | Document price |
|---|---|---|
| **UL 498**, Ed. 16 (2024-06-20) | **Attachment Plugs and Receptacles** | $798–1,990 |
| **UL 60730-1**, Ed. 6 (2024-10-31) | **Automatic Electrical Controls — Part 1: General Requirements** — explicitly covers **smart-enabled devices** and both inherent and functional safety | $763–1,906 |
| **UL 94**, Ed. 7 (2023, rev. 2026-07-02) | **Tests for Flammability of Plastic Materials** | $402–998 |
| **UL 1699**, Ed. 4 (2025-11-17) | **Arc-Fault Circuit-Interrupters** — AFCIs for dwelling units up to 20 A on 120 V 60 Hz circuits; Ed. 4 adds **expanded EMC requirements** | $798–1,990 |

**Listing cost: $15,000–40,000 and 3–6 months [E]** for a first plug-in mains SKU, including
construction review and dielectric/temperature-rise/abnormal testing — **plus annual follow-up
factory inspections**, a recurring cost routinely forgotten in first-year budgets. Get three
parallel quotes; Intertek ETL and QPS routinely undercut UL on straightforward appliance work.

> **Unverified:** NRTL pricing and timelines — UL, Intertek and Keystone all publish no prices
> (ul.com service URLs 404'd; intertek.com and keystonecompliance.com returned 403). The figures
> above are estimates.

Note for enclosure selection: Milesight's own WS51x spec calls out **"Polycarbonate (UL94 V-0)"**.
V-0 material is table stakes for a mains product, not an upgrade.

### The $60k–200k scoping decision

**Do not claim arc-fault *interruption*.** The moment your device opens the circuit on an arc
signature, you are an AFCI and you are in **UL 1699** — a brutal and expensive standard, and one
that is as much about *not* tripping on vacuum cleaners, dimmers and arc welders as about detecting
arcs.

**Ting deliberately only alerts and cannot trip a circuit.** That is what keeps it in plain UL 498
and UL 60730 territory. Copy that choice, and write "alert-only" into the product definition where
nobody can quietly change it.

> **Unverified:** IEC 62606 (the European AFDD route) — iec.ch search 404'd. Only relevant on export
> anyway.

---

## 7.4 Water-contact and plumbing standards

| Standard | Status |
|---|---|
| **IAPMO IGC 115 / 241 / 261 / 349** | **Verified** — titles and scopes in §7.0 |
| **NSF/ANSI 61** (what leaches into drinking water) and **NSF/ANSI 372** (≤0.25% weighted-average lead) | **Unverified** — nsf.org returned 403. Scope is well established; **cost unverified** |
| **IAPMO UPC listing / IAPMO R&T** | Directory exists at [pld.iapmo.org](https://pld.iapmo.org/) but rendered without results — **specific listings unverified** |
| **CSA B125.3** (Canada) | **Unverified** — csagroup.org returned 403 |

**Local plumbing codes** vary by authority having jurisdiction, and whether a *listed* device and a
*licensed* installer are required is **unverified at a national level**. The commercially safe
assumption: **inline means a listed device plus a licensed plumber in most jurisdictions; clamp-on
means neither.** Build the clamp-on products first precisely because they sidestep this entire tree.

---

## 7.5 Insurer-facing recognition, cybersecurity, and ecosystem programs

| Program | Status | Cost **[V]** unless noted |
|---|---|---|
| **FM Approvals** | **Is an OSHA-recognised NRTL.** Whether FM publishes an approval standard for water leak detection / automatic shutoff is **unverified** (fmapprovals.com 403). **Worth a direct call — FM Approval is the single most valuable mark for commercial-property insurer channels** | — |
| **IBHS** | Runs **FORTIFIED Home™** with a public designation database and lists "Interior Water Damage" as a research area — but **no IBHS program for leak-detection devices or smart-home sensors appears on the site.** Do not plan a go-to-market around a listing that does not exist | — |
| **US Cyber Trust Mark** | **Status unverified.** Every fcc.gov URL returned 403; UL program URLs 404'd. **Do not budget for this until you confirm it directly with the FCC or the Lead Administrator.** Note that carriers and retail channels *can* start requiring it, and state IoT security laws already impose default-credential rules | — |
| **NIST IR 8425** | Real, published **September 2022** under EO 14028; identifies cybersecurity capabilities for consumer IoT. Companion IR 8425A exists ([csrc.nist.gov](https://csrc.nist.gov/pubs/ir/8425/final)) | **free** |
| **LoRaWAN certification** | End device at an Authorised Test Lab **$1,200**; end device self-test **$1,700**; module at ATL **$2,000**; module self-test **$2,800**; child-device variants **$200–750**; extra LCTT licence $1,000. Certificates valid **5 years** ([lora-alliance.org](https://lora-alliance.org/lorawan-certification/)) | as listed |
| **LoRa Alliance membership** | **Mandatory** for certification. **Adopter $3,000–6,000/yr — and the $3,000 rate applies to startups under $1M total revenue**, which is you. One free LCTT licence per member per year ([lora-alliance.org](https://lora-alliance.org/become-a-member/)) | **$3,000/yr** |
| **Matter / CSA** | Membership **Adopter $7,500/yr** (Participant $21,500, Promoter $112,500, Associate $0). Certification **$2,000/product** for Participants, **$3,000/product** for Adopters; derivatives $1,500–2,500; Associate white-label $2,500 + $500/yr per product ([csa-iot.org](https://csa-iot.org/become-member/)) | **$10,500 realistic entry** |
| **Matter test houses** | An authorised third-party test house is **mandatory**. North America: Bureau Veritas, Trident IoT, Novus Labs, UL Solutions, Teledyne LeCroy, Granite River Labs ([csa-iot.org](https://csa-iot.org/certification/testing-providers/)) | lab fee on top — **unverified** |
| **Works with Apple Home** | Two paths: HomeKit via **MFi Program** enrollment, or **Matter via CSA** and then you may use the badge. Apple supports Matter from iOS 16.1 | **no Apple fees published** |
| **Amazon Sidewalk** | Two-stage: reference-design qualification then end-product registration. Notably *"not every product must go through the testing process"* — tiered pathways exist ([docs.sidewalk.amazon](https://docs.sidewalk.amazon/qualification/)) | test houses and fees **unverified** |
| **EU CRA / RED** | Out of scope now. Note only that CE/RED testing is bundled cheaply by labs already doing your FCC work (Sunfire lists **CE from $999**) | later |

---

## 7.6 Cost to a shippable certified product

Full line items in `data/cert-costs.csv`.

### A — Clamp-on thermal leak sensor (battery, LoRaWAN US915 + BLE)

| Line | Low | High | Evidence |
|---|---|---|---|
| Pre-certified radio module (RAK3172 $5.99 / RAK11720 $7.99) | — | — | **[V]** |
| FCC/ISED composite + unintentional testing, module grant inherited | $1,500 | $5,000 | **[E]**, floor at Sunfire's published $999 |
| LoRa Alliance membership (startup rate) | $3,000 | $6,000 | **[V]** |
| LoRaWAN certification, end device at ATL | $1,200 | $1,700 | **[V]** |
| Plumbing listing | **$0** | **$0** | no wetted path |
| NRTL safety listing | **$0** | **$0** | no mains |
| UL 94 standard document (if claiming V-0) | $0 | $998 | **[V]** |
| Tooling, EVT/DVT, **and an FTO opinion** | $25,000 | $80,000 | **[E]** |
| **Total** | **~$31k** | **~$94k** | |

### B — Retrofit clamp-over-lever actuator

| Line | Low | High |
|---|---|---|
| Radio certification (shared module; Class II or new composite) | $1,500 | $5,000 |
| LoRaWAN certification (child device on the same stack) | $200 | $1,200 |
| Plumbing listing | $0 | $0 |
| NRTL (only if a mains variant ships) | $8,000 | $25,000 |
| **Mechanical development — gearbox, torque limiting, universal lever grip** | **$60,000** | **$200,000** |
| **Total** | **~$70k** | **~$231k** |

The mechanical line dominates, which is why this is a spike with a kill gate rather than a product.

### C — Plug-in electrical fault detector (120 V, alert-only)

| Line | Low | High |
|---|---|---|
| Radio (ESP32-C6-WROOM-1, grant inherited) | — | — |
| FCC/ISED composite testing | $1,500 | $5,000 |
| **NRTL listing to UL 498 + UL 60730-1 + UL 94** | $15,000 | $40,000 |
| NRTL annual follow-up / factory inspections | $2,000 | $8,000 /yr |
| Matter certification (CSA Adopter $7,500 + $3,000/product) | $10,500 | $10,500 |
| **If it interrupts the circuit → UL 1699 AFCI** | **+$60,000** | **+$200,000** |
| HV front end, isolation design, arc classifier | $150,000 | $500,000+ |
| **Total (alert-only)** | **~$180k** | **~$560k** |

### D — Inline motorised valve (added on top of B's radio lines)

| Line | Low | High |
|---|---|---|
| IAPMO IGC 115 and/or IGC 349 listing + R&T | $15,000 | $45,000 |
| NSF/ANSI 61 + 372 wetted-material qualification | $10,000 | $35,000 |
| cUPC / CSA B125.3 for Canada | $5,000 | $20,000 |
| UL 60730 functional safety for the control | $12,000 | $35,000 |
| **Total** | **~$112k** | **~$366k** |

**Headline: product A is a $31–94k problem. Product C is a $180–560k problem dominated by the
classifier and NRTL, and it sits on Whisker Labs' patent family. Product D doubles to triples the
moment you go inline instead of clamp-on. Sequence A → B spike → D / C.**

---

## 7.7 The week-1 action, and the long-lead items

**Send a written scope-determination request to IAPMO R&T** asking whether an external
clamp-over-lever actuator with no wetted surface and no pressure boundary falls within IGC 115 or
IGC 349. It is cheap, takes **2–6 weeks [E]**, and it is the single highest-information-per-dollar
action in the entire plan — it either unlocks own-design shutoff hardware in year 1 or closes the
question permanently.

Then ask the more useful commercial question: **who is actually requiring the listing?** It is
rarely a regulatory requirement for a retrofit accessory. Real demand comes from plumbing-code
listing obligations when a device is installed *in* the potable system, from commercial-property
insurers' own device-approval processes, and from procurement checklists. For commercial buyers the
mark that tends to matter is **FM Approvals**, not an ASSE or IAPMO number. **So the binding
constraint may be a carrier's approval process rather than any published standard — which is a
procurement problem, not a lab problem, and it is slower.**

### Long-lead items, ranked by how little cash can compress them

| Item | Lead time **[E]** | Must start by | Cash-compressible? |
|---|---|---|---|
| **Labelled field data across a freeze/thaw cycle** | **12 months** | P0 wk 2 | **No** |
| Insurer / aggregator procurement + security review | 6–18 mo | P0 wk 1 | No |
| State rate/rule filing (per state, per carrier) | 6–18 mo | P2 | No |
| SOC 2 Type I → Type II | 3–6 mo → +6–12 mo | P1 | Partly |
| **IAPMO written scope determination** | **2–6 wk** | **P0 wk 1** | No |
| FCC test-lab slot (queue + test) | 4–10 wk queue + 1–3 wk | P2 start − 10 wk | Partly (expedite fees) |
| Pre-certified module MOQ / allocation | 8–20 wk | P1 mid | No |
| Injection-mould tooling + 2–3 iterations | 5–10 wk each | P2 mid | Partly |
| GL + product liability + tech E&O binding | 3–8 wk | P1 start − 8 wk | No |

**The item that cannot be bought is calendar time in the field across a winter.** Everything else on
this list can be expedited, parallelised or paid away. That is why
[08-roadmap.md](08-roadmap.md) puts devices in buildings in the first 30 days and forbids PCB layout
for 90.
