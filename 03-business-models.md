# 3. Business Models — Who Writes the First Cheque

All four buyer routes, costed against the only public unit economics in this market, plus the
regulatory rule that makes a free-hardware giveaway legal.

---

## 3.0 The rule that makes this industry work

**NAIC Unfair Trade Practices Act Model Act #880, amended December 2020**, created a safe harbour
allowing insurers and producers to offer **"value-added products or services at no or reduced cost
when such products or services are not specified in the policy of insurance"**, provided the
offering:

1. relates to the insurance coverage;
2. serves a designated purpose **including "loss mitigation or loss control"**, reducing claim
   costs, or monitoring/assessing risk;
3. has a reasonable cost relative to premium;
4. provides customer contact information; and
5. is offered without unfair discrimination, based on **"documented objective evidence."**

([Locke Lord analysis, 11 Dec 2020](https://www.insurereinsure.com/2020/12/11/naic-adopts-amendments-to-the-unfair-trade-practices-act-model-pertaining-to-anti-rebating/);
[NAIC comment file](https://content.naic.org/sites/default/files/inline-files/_Combined_UTPA_Rebating_Comments090220_1.pdf))

**The operative consequence, and it shapes your entire go-to-market:**

> **Shipping a free device is a Model-880 value-added service and needs no rate filing.
> Giving a premium discount is a rate change and does.**

That is precisely why **Westfield** gives away the hardware *and* two years of free plumber repairs
while stating plainly that it *"does not currently offer a discount"*
([westfieldinsurance.com/leakbot](https://www.westfieldinsurance.com/leakbot)), whereas
**Nationwide** does both and publishes **filed, per-state** percentages — California 10% on water
and theft, 5% on fire; Washington 5% on all perils
([nationwide.com](https://www.nationwide.com/personal/insurance/homeowners/smart-home/)).

**Sell loss control, not a discount.** It is faster, it is legal without a filing, and it is what
40+ carriers already buy.

> **Unverified:** Model #880's de minimis non-cash gift dollar limit, and the number of states that
> have adopted the amendments. Both must be checked per state with counsel.

### Do you need an insurance licence?

**No — not to sell loss-control hardware and services to a carrier.** NAIC defines an insurance
producer as someone who **"sells, solicits, or negotiates insurance"**, and those three activities
are what trigger licensure ([NAIC producer licensing](https://content.naic.org/insurance-topics/producer-licensing)).
Selling devices, subscriptions and repair services *to a carrier* is a vendor relationship. None of
Ting Labs, Ondo, Flume or Phyn appears to take premium share.

**You need producer/MGA licensing — plus a fronting carrier or capacity provider — only if you want
to share in premium, bind risk, or underwrite.** Then you are into state producer licensing and MGA
statutes, in every state you operate. That is the main upside lever and it is correctly a year-2+
decision.

> **Unverified:** NAIC's Managing General Agents Act specifics. Get state-specific counsel before
> structuring anything that touches premium.

---

## 3.1 Route (a) — Insurer / MGA B2B2C, device free to the homeowner

**Who buys today.** 40+ US and Canadian carriers for Ting; 28 carriers for LeakBot; Amica for Flume;
Asurion-as-intermediary for Phyn; Chubb for StreamLabs (which it owns) and ProSentry.

**Contract shape.** A one-off **device fee** plus **recurring service**. Ondo's FY26 mix was
**£3.8m recurring vs £0.8m device fee** — about 82% recurring. Carriers buy prepaid service windows
of **2 years** (Westfield/LeakBot), **3 years** (Travelers, Chubb, Westfield/Ting, Kingstone) or
**5 years** (Nationwide/Ting).

**Price point.** **$49–60 per home per year [V]**. Ondo states $60/home/year produces positive
portfolio ROI; Ting's DTC equivalent is $49/year.

**Sales cycle: 18–30 months from pilot to multi-state rollout.** Selective ran a pilot from Feb 2024
and announced its full 15-state Personal Lines footprint in Mar 2026. State Farm went from a May 2021
announcement to 1M sensors in Feb 2023 to "2 million homes" in Apr 2024. Expect a one-state or
10,000-unit pilot first — Westfield's was 10,000 units in 2024.

### The unit economics, from audited filings — read this before committing

Ondo InsurTech is listed, so this is the only honest look at the model anyone outside it gets.

| Metric | FY26 (to 31 Mar 2026) |
|---|---|
| Revenue | £4.645m (USA £2.284m) |
| **Statutory gross margin** | **4.7%** |
| Gross margin **before** CAC | 54% (US 50%; **61% at higher density**) |
| Gross margin **after** CAC | **−15.5%** |
| Net loss | **£7.177m** |
| Cash | **£1.122m** |
| US CAC / LTV | **$54 / $180** (3:1) |
| Net profit per device | ~$148 over 6.5 years ≈ **$23/device/year** |
| Monthly churn | 1.27% (US 1.7%) ≈ **14–19% a year** |

**Three lessons.**

1. **Statutory gross margin was 4.7% because device cost *and* installed repair labour sit in
   COGS.** Ondo did **3,070 US in-home repairs** in FY26 with a **$250 per-part cap**. The repair
   network is the operationally hardest part and the main margin sink. **Launch monitoring-only and
   partner for repair** (Asurion-style) — or own repair deliberately, for defensibility, with
   capital behind it.
2. **Density is worth 11 margin points.** 50% → 61% purely from concentration in Indiana.
   Geographic focus is not a nicety; it is the difference between viable and not.
3. **At ~$23/device/year of contribution, this only works at hundreds of thousands of devices.** Do
   not plan to be profitable on 20,000 units.

**Churn asymmetry worth noting:** a DTC subscriber keeps paying when they switch insurer; an
**insurer-funded device dies with the policy.** Your churn is your carrier's churn.

### Procurement blockers

- **Rate filings**, if you want a discount rather than a giveaway (see §3.0).
- **State exclusions are routine.** Nationwide excludes AK, HI, LA, NY, OK; State Farm excludes
  AK, DE, WY.
- **Activation is contractually policed.** Nationwide **removes the discount if the device is not
  activated within 55 days**. Your onboarding funnel is a contract term.
- **Data-use anxiety.** Nationwide has to promise "data collected will not be used to deny claims"
  and that the customer keeps the device on exit. Expect to make the same commitments in writing.
- **Vendor qualification.** Bound E&O and product liability, a completed security review, often
  SOC 2. **This is the real reason pre-seed vendors do not get signed**, and it is why §3.5 matters
  more than this section.

---

## 3.2 Route (b) — Property managers, multifamily and HOA

**Who buys today.** Eddy Solutions (multifamily, office, hospitality, healthcare, education, data
centres, construction); Alert Labs / Watsco (15,000+ buildings); WINT (Empire State Building, HP,
Suffolk Construction); ProSentry (NYC co-ops and condos); SmartRent (600+ clients, 1.2m rental
homes); Resideo into managed-Wi-Fi communities.

**Price points.** ProSentry is the only published per-unit model: **~$70/sensor + $1/sensor/month**,
at ~4 sensors per unit → **~$280 capex and ~$48/year per unit**. Eddy and Alert Labs are quote-only
with an upfront hardware element plus monitoring.

**The lever is deductible and insurability, not premium.** Eddy publishes **up to 80% deductible
reduction** and **up to 25% premium reduction**. But the real driver is the Alexander story from
[01](01-market-and-insurance-case.md): a building facing **$150k → $850k** in premium after two
losses. For large portfolios the binding constraint is whether they can get covered at all — a
dramatically stronger buying trigger than a discount, and it needs no filing.

**Why this route is structurally better than (a) for a startup:**

| | Insurer-funded | Property manager |
|---|---|---|
| Who pays | Carrier | Owner/operator |
| Cash timing | Prepaid 2–5 yr service | **Upfront capex + monitoring** |
| Churn attaches to | **The policy** (14–19%/yr) | **The asset** (low) |
| Sales cycle | 18–30 months | **Weeks to months** |
| Vendor bar | E&O + security review + often SOC 2 | Insurance certificate and references |
| Radio economics | Per-home gateway hurts | **One gateway, dozens of nodes** |

**Procurement blockers.** Connectivity first: Contractor Magazine is blunt that Wi-Fi-dependent
devices need *"strong managed Wi-Fi infrastructure… for consistent device performance at scale"* —
which is exactly why Eddy chose LoRaWAN and Alert Labs chose **cellular with battery backup, "no
WiFi or wired networks required."** Then in-suite access and bylaws; then the **split incentive**
(owner pays capex, insurer captures the loss benefit), which is why broker-endorsed programs
matter — note that Eddy lists **Aon and HUB**, i.e. the brokers are the door, not the carriers.
Then capital budget cycles and plumber availability.

> **Unverified:** no named student-housing, senior-living or hotel deployment contracts were
> retrievable, though NMHC's Student Housing Conference exists as a channel.

---

## 3.3 Route (c) — Direct to consumer

The price ladder actually in market: **Ting $99 + $49/yr** · **Flume $269, or $149 + $49.99/yr** ·
**StreamLabs Scout $118–129 / Monitor $389 / Control $999+** · **Moen Flo ~$559 (unverified) +
FloProtect $5/mo.**

**The demand problem is education, not price.** Only **17%** of homeowners own a water sensor,
**68%** are unfamiliar with them and **61%** have never heard of automatic shutoff — while **53% of
$100k+ households would install if given a discount** (Hanover/Harris). So even for a retail SKU the
insurer is the natural demand-creation partner. There is also **no trust mark to lean on**: EPA
WaterSense has published only a *consumer guide* for leak detection and flow monitoring devices
([epa.gov/watersense](https://www.epa.gov/watersense/leak-detection-and-flow-monitoring-devices)),
not a label or certification.

**What the incumbents' DTC motion actually tells you:** Moen bundles **up to $5,000 of deductible
coverage** into a $5/month subscription and sells hardware on 36-month 0% financing. In DTC you are
selling **risk transfer**, not sensors. That is a financial product wrapped around hardware, and it
needs an insurance partner anyway.

**Verdict:** worst CAC, you fund the hardware, and you compete on shelf space with Fortune Brands
and Belkin. Treat DTC as a channel you add once a carrier has validated the product, not a route to
first revenue.

---

## 3.4 Route (d) — Builders, utilities and OEM white-label

**Licensing is a proven route at the top of this market — four live examples:**

- **Verisure ← Ting Labs** (Sept 2026): exclusive Europe, **co-development and licensing**. Ting
  supplies technology, firmware and cloud platform; **Verisure manufactures, sells, installs and
  supports.** Subscription-based, launching early 2027.
- **Asurion LeakProtect ← Phyn**: Asurion owns the brand, the plumber network, the monitoring and
  the carrier relationships; Phyn is the technology.
- **Phyn → GWA Group**: exclusive Australia/New Zealand distribution, Feb 2026.
- **Resideo → First Alert-branded devices** into USAA's PERKS channel at 20–29% off.

**What this means for you.** The OEM route is how a technology company with a real algorithm
monetises without building a sales force or an install network — and it is available *because* the
hard asset is the detection method and the cloud, not the plastic. If A1's analytics prove out, a
licensing deal with a security-monitoring incumbent (the Verisure shape) is a legitimate exit from
the capital treadmill.

**Price pressure and design cycles are the cost.** Builders standardise slowly and buy on unit
price.

> **Unverified:** no named US homebuilder standard-fit deal and no named US water-utility device
> giveaway or rebate program was retrievable. Both are plausible but unproven.

---

## 3.5 The finding that matters more than the route choice

**Carriers do not buy from pre-seed vendors. They buy from vendors with bound E&O, a completed
security review, often SOC 2, and a balance sheet their reinsurer will accept.**

The real route is almost always *through* someone:

| Aggregator | What it does | Evidence it works |
|---|---|---|
| **vipHomeLink** | Distributor platform to specialist and regional carriers | Delivered **Kingstone (Ting, NY)** and **Co-operative Insurance (LeakBot, VT/NH)** — it converted carriers for *both* market leaders |
| **Asurion** | End-to-end: device selection, professional install, monitoring, alert triage, warranty admin, **up to $500 plumbing discounts** | Partners with **100+** carriers, retailers and technology companies |
| **HSB (Munich Re)** | Sells loss-prevention programs *into* other carriers | Expanded its Ting partnership; launched smart-shutoff solutions in HSB Canada |
| **MGAs already in the channel** | SageSure, Bamboo are on Ting's carrier list | An MGA can transact faster than a carrier |

**One aggregator contract substitutes for ten carrier BD cycles.** Your roadmap needs a *"who will
sign the paper"* layer, which is why [08-roadmap.md](08-roadmap.md) tracks it as one of the nine
technology layers rather than burying it under "insurer integration".

---

## 3.6 The attribution problem — and what to promise instead

**You cannot prove loss reduction at pilot scale, and promising to is how you lose the relationship
you spent 18 months building.**

The arithmetic: escape-of-water claim frequency is **~1.5 per 100 house-years**. Detecting a 30–50%
reduction at statistical significance needs **tens of thousands of house-years plus a control
group**. A 500-home, 12-month pilot produces 500 house-years. It cannot get there.

Note how the incumbents handled this. Ting did not run a randomised trial — it ran a
**self-controlled study** on claims over time in homes with the device installed, using Triple-I
and Verisk data, and published it with a trade-body badge. Ondo quotes **"$25 million prevented
across 1,470 potential claim saves"** — a count of mitigated events, not a frequency study.

**So agree a surrogate metric in writing during P0.** Candidates, in descending order of how much a
carrier will respect them:

1. **Mitigated-event case studies** with dollar figures — what Ondo publishes.
2. **Time-to-detection and time-to-shutoff** distributions. Flume's carrier study leans on "83%
   would have gone undetected longer" and "77.5% identified within one day".
3. A **severity model**: events caught early × modelled severity avoided. Severity, not frequency,
   is where the statistics are reachable — and severity is also where the trend is worst (+63%).
4. A **self-controlled before/after** design on the carrier's own claims data, run with their
   actuarial team, which is the Ting playbook and the only route to a filing.

Put the surrogate, the data rights and the publication rights in the pilot contract. Data rights in
particular: if the carrier owns the telemetry, your corpus — and your §2.4 moat argument — evaporate.

---

## 3.7 Recommendation

**Run (b) for cash and (a) for scale, enter (a) through an aggregator, and keep (d) as the exit.**

| Route | Role | When |
|---|---|---|
| **(b) Property managers / multifamily** | **The cash business.** Upfront payment, asset-attached churn, insurability as the trigger, and the only place LoRaWAN economics win | **Start now** — weeks-to-months cycle |
| **(a) Insurers via an aggregator** | **The scale business.** Hardware paid for, near-zero marketing, but 18–30 months and a vendor bar you cannot clear directly yet | **Start conversations now, expect revenue in P2–P3** |
| **(c) DTC** | A channel to add once a carrier has validated the product | Year 2+ |
| **(d) OEM / licensing** | The capital-efficient endgame if the analytics prove out — the Verisure shape | Year 2+ |

And price to the published benchmark: **$49–60 per home per year** recurring, with a one-off device
fee, at roughly an 80/20 recurring-to-device mix.
