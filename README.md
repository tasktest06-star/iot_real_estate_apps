# Property IoT for Insurance Perils — North America

A briefing pack for a pre-seed hardware startup building real-estate IoT sold against two insurance
perils: **escape of water** (leak detection, emergency shutoff) and **electrical fire precursors**
(plug-in fault detection). Radios in scope: **LoRaWAN US915** and **BLE**, with Thread/Matter,
LTE-M and Wi-Fi as comparators.

Researched and written **2026-10-14**. Every licence claim is machine-verified; every dollar figure
is marked **[V]** vendor-published or **[E]** estimate; anything that could not be confirmed says
**unverified** rather than being asserted.

---

## 0. Assumptions this pack was written under

| Parameter | Value |
|---|---|
| Market | **North America** (US first; Canadian deltas noted) |
| Capex, first 12 months | **$25k–$100k**, non-salary |
| Team | **Full in-house EE**: schematic, PCB, RF, enclosure |
| Product wedge | All three evaluated, then sequenced |
| First buyer | All four routes evaluated, then sequenced |
| Build model | All four evaluated, then sequenced |
| Open-source constraint | **Apache-2.0 preferred** — product firmware stays closed |

Two defaults chosen where the brief was silent, both revisitable: **US-first** with Canada as a
phase-2 geography, and **no producer/MGA licence** in year one (see §3 of
[03-business-models.md](03-business-models.md) for why you do not need one, and what it would buy).

---

## 1. The recommendation, in one page

**Build the clamp-on thermal leak sensor. Resell someone else's valve. Defer the electrical plug.
Sell to buildings for cash and to carriers for scale — and reach carriers through an aggregator,
not directly.**

The reasoning, in the order it actually matters:

**Certification, not engineering, is your cash constraint.** With in-house EE, design labour is
nearly free — so what you spend money on is test labs, listings and tooling. Those costs differ by
an order of magnitude across the products you were considering, and they turn on two binary
questions: *is there mains voltage inside the enclosure?* (→ NRTL listing) and *is there a wetted
surface in the BOM?* (→ NSF/ANSI 61 + 372 and an IAPMO listing).

| Product | Mains | Wetted | Cost to a shippable certified product |
|---|---|---|---|
| Clamp-on thermal leak sensor | No | No | **$31k–94k** |
| Retrofit clamp-over-lever actuator | No | No | $70k–231k |
| Inline motorised valve | Yes | Yes | $112k–366k |
| Plug-in electrical fault detector | Yes | No | **$180k–560k+** |

Only the first fits your envelope. See [07-compliance-and-certification.md](07-compliance-and-certification.md).

**But certification is a $20–60k problem, and it is not what kills you.** The critical path is
`buyer signature → installed base → one freeze/thaw cycle of labelled data → a defensible
nuisance-alert rate → security review → filing`. FCC and PCB sit on a *parallel* path that merely
must not be late. One winter of field data cannot be bought or parallelised — **miss October and you
slip a year, not a quarter.** Hence the standing rule in the roadmap: *no PCB layout in the first
90 days.* It is counter-intuitive with an EE team sitting idle, and it is the single most important
sequencing decision in the plan.

**You will not get a carrier to sign as a pre-seed vendor.** Carriers need bound E&O, a completed
security review, often SOC 2. Both market leaders reached regional carriers *through* an
aggregator — [vipHomeLink](https://www.streetinsider.com/PRNewswire/vipHomeLink+Partners+with+Whisker+Labs+to+Offer+Ting+Fire+Prevention+Program+and+Power+Outage+Alerts/23684731.html)
delivered Kingstone for Ting and Co-operative Insurance for LeakBot;
[Asurion](https://www.prnewswire.com/news-releases/asurion-expands-leakprotect-to-bring-managed-water-loss-prevention-to-more-homes-302892021.html)
fronts 100+ carriers and wraps Phyn hardware as its own brand; HSB (Munich Re) resells loss
prevention into other carriers. Observed direct carrier cycles ran **18–30 months** from pilot to
rollout. One aggregator contract substitutes for ten carrier BD cycles.

**Run multifamily in parallel as the cash business.** It pays upfront, churns with the asset rather
than the policy, and the buying trigger is *insurability* rather than a discount — a far stronger
motivation. It is also the only configuration where LoRaWAN economics genuinely win: one $382
gateway amortised across dozens of $12 nodes.

**Do not fight Ting head-on.** It has 1.4M homes, 40+ carriers, State Farm Ventures backing, a
Triple-I-badged efficacy study and now an exclusive Verisure licensing deal for Europe. Attack its
structural exclusions instead: no-Wi-Fi homes, renters, multi-panel properties, no local alarm,
and Canada — where exactly one carrier offers it, in one province.

### What to do in week 1

1. Lock the **module ecosystem** (not the radio — radio is downstream of the buyer decision).
2. Send a **written scope-determination request to IAPMO R&T** on whether a clamp-over-lever
   actuator falls under IGC 115/349. Costs nothing, takes 2–6 weeks, highest information per dollar
   in the entire plan.
3. Stand up telemetry ingest with **raw retention from day one** — the corpus is the only asset
   here that cash cannot compress.
4. Start 25 buyer conversations across all four routes with one objective: a countersigned document.

---

## 2. How to read this pack

| Document | What it answers |
|---|---|
| [01-market-and-insurance-case.md](01-market-and-insurance-case.md) | Why insurers pay. Peril economics, every incumbent, every named insurer program and its terms, competitive white space. |
| [02-application-portfolio.md](02-application-portfolio.md) | **"What can we start with?"** 14 applications scored and given a start / spike / later / avoid verdict. |
| [03-business-models.md](03-business-models.md) | The four buyer routes costed, real unit economics from public filings, and the regulatory rule that makes free hardware legal. |
| [04-connectivity.md](04-connectivity.md) | LoRaWAN vs BLE vs Thread/Matter vs LTE-M vs Wi-Fi, the downlink-latency problem for shutoff, and gateway economics. |
| [05-open-source-stack.md](05-open-source-stack.md) | The verified Apache-2.0 stack, per-product architecture, annotated snippets, and the licence traps. |
| [06-hardware-and-devkits.md](06-hardware-and-devkits.md) | Dev kit → pre-certified production module pairs with prices, sensing front ends with part numbers, community health. |
| [07-compliance-and-certification.md](07-compliance-and-certification.md) | FCC/ISED, NRTL, IAPMO IGC, NSF, UL, LoRa Alliance, Matter — what each costs and how long it takes. |
| [08-roadmap.md](08-roadmap.md) | **The roadmap.** Five phases, one hard exit gate each, layer × phase matrix, critical path, risk register. |
| [09-build-vs-whitelabel.md](09-build-vs-whitelabel.md) | Own-design vs ODM rebadge vs white-label vs software-only, costed for an in-house-EE team. |
| [slides/main.tex](slides/main.tex) | The same roadmap as an internal **engineering** deck (not an investor pitch). `make slides`. |
| [prototype/](prototype/) | **A working end-to-end slice of the recommended wedge.** Zephyr firmware → LoRaWAN codec → simulated fleet → detection → insurer API → dashboard. 312 passing tests; `cd prototype && make e2e`. |
| [10-product-family-architecture.md](10-product-family-architecture.md) | **How this platform extends to the other 13 applications.** Per-product architecture, effort, certification gate and sequencing — plus adjacent perils. |
| [BRANCHES.md](BRANCHES.md) | **What each branch is, and why none of the four design branches are merged.** Per-branch detail, the comparison table, lifecycle and naming conventions. |

The prototype is where the pack stops asserting and starts measuring, and it has already corrected
two things in these documents: application **A4**'s "free upside" framing (§2.2 of
[02](02-application-portfolio.md)), and two Zephyr symbol names that upstream has since renamed.
Its headline number is the P1 exit gate from [08](08-roadmap.md): **0.3383 nuisance alerts per
device-year against a < 0.50 gate — PASS**, with freeze recall of 9/31 and leak recall of 1/4
reported as the honest cost. Details in [prototype/README.md](prototype/README.md).

Machine-readable: `data/repos.csv` (89 repos with verified licences), `data/application-scores.csv`,
`data/cert-costs.csv`, `data/bom-estimates.csv`.

```
make verify-licenses   # re-read every LICENSE file; fails if any licence drifted
make check-data        # CSVs parse, bounds hold, prose and CSV agree
make check-links       # every GitHub URL in the Markdown resolves
make slides            # build slides/main.pdf
```

---

## 3. Five things that are not what you would expect

**The standard named in the original brief is the wrong document.** ASSE 1087-2026 is *"Commercial
and Food Service Water Treatment Equipment Utilizing Drinking Water"* —
[asse-plumbing.org](https://asse-plumbing.org/standards/product-standards). The standards that
actually govern these products are **IAPMO IGC 115** (Automatic Water Leak Detection Devices),
**IGC 241** (Water Flow Shut-Off Devices), **IGC 261** (Excess-Flow Shutoff Devices) and
**IGC 349** (Electronic Plumbing Supply System Integrity Protection Devices) —
[iapmostandards.org](https://www.iapmostandards.org/iapmo-industry-standards/current-iapmo-igc-standards).
IGC 349 is the one that describes a Flo/Phyn-class product.

**Two of the most obvious backend choices are no longer open source.**
[ThingsBoard](https://github.com/thingsboard/thingsboard) moved to **BUSL-1.1**, capping free
commercial use at **100 devices and one server**; [EMQX](https://github.com/emqx/emqx) moved to
**BUSL-1.1** and now requires a paid licence file to run more than one node. Both still rank first
in search. Apache-2.0 replacements: [Magistrala](https://github.com/absmach/magistrala) and
[VerneMQ](https://github.com/vernemq/vernemq).

**Giving the device away free is explicitly legal; giving a discount is a rate filing.** NAIC's
Unfair Trade Practices Act Model #880, amended December 2020, created a safe harbour for
"value-added products or services" whose purpose includes **loss mitigation or loss control**. So
the hardware giveaway needs no filing, while a premium discount is a rate change that does. That is
why Westfield ships LeakBot free *with free repairs* and states plainly that it offers
[no discount](https://www.westfieldinsurance.com/leakbot).

**There is no Apache-2.0 LoRaWAN MAC, and its successor deliberately withholds the patent grant.**
Semtech's `LoRaMac-node` is Revised BSD and in maintenance mode; the replacement
[SWL2001](https://github.com/Lora-net/SWL2001) is **BSD-3-Clause-Clear**, which states that no
patent licences are granted. If Apache-2.0 §3's patent grant is *why* you wanted Apache-2.0, Clear
BSD is its deliberate opposite.

**Public LoRaWAN will not work inside US homes, and the reason is operational.** Senet no longer
exists independently (`senetco.com` now redirects to Netmore, which acquired Actility in January
2026), Everynet's site fails its TLS handshake, and Helium has pivoted cellular-first. But the
decisive problem is simpler: **no US operator gives you an API to pre-qualify a customer's address
before you ship them a box.** A hardware company shipping devices that might not connect has an
unmanageable returns cost. See [04-connectivity.md](04-connectivity.md).

---

## 4. Method, and what is weak

Research was done by direct retrieval from primary sources — insurer program pages, LSE regulatory
filings, standards bodies, patent databases, vendor stores and distributor price aggregators.
General web search was unavailable, so discovery ran through news RSS, PR wires and GitHub's own
server-rendered pages.

Licences were verified by reading each repository's actual `LICENSE` file at `HEAD` via
`raw.githubusercontent.com` (GitHub's unauthenticated REST API was rate-limit-exhausted). The
verifier classifies by **licence title**, not body text, because body matching is wrong in two ways
that both produced false positives during this research: a BUSL-1.1 file names Apache-2.0 as its
*Change License*, and GPLv3 §13 mentions the Affero licence. `scripts/verify-licenses.sh`
documents both traps and re-checks all 89 repos in about a minute.

**Known weak spots.** NFPA's electrical-distribution fire breakdown (site is JS-rendered; ESFI
returned 403) — USFA figures are used instead. Ting Labs funding *amounts*. Moen Flo hardware MSRP
(only a secondary $559 figure). NRTL and IAPMO listing costs — no NRTL publishes prices, so those
are flagged **[E]** and you should get three parallel quotes. NSF/ANSI 61 and 372 and CSA B125.3
details (nsf.org and csagroup.org both returned 403). US Cyber Trust Mark current status (every
fcc.gov URL returned 403) — **do not budget for it until you confirm it directly**. Motorised valve
current draw and stall current: unverified, and the answer costs one afternoon and a few hundred
dollars of sample valves, so measure rather than model. Per-document "unverified" labels mark the
rest.

**One structural caveat about the moat.** This pack argues in two directions and you should see
both. Open source covers nearly the whole stack, but there is **no open acoustic leak-signature
dataset and no open AC series-arc dataset** — GitHub's `afci` topic has one repository with zero
stars, and UL 1699 waveforms sit behind a paywall. That absence argues your captured corpus is the
asset. Against that: the discriminative signal for escape of water is mostly simple, the hard part
is nuisance rejection, and nuisance rejection generalises *poorly* across housing stock and climate
zone — while incumbents already hold 100k+ device-years. The honest reading is that the defensible
moat is the **carrier relationship, the installed base and the claims-workflow integration**, and
that the dataset is a necessary input rather than the moat itself. Both readings are argued in
[08-roadmap.md](08-roadmap.md) §7, where the claim is made falsifiable.
