# 4. Connectivity — LoRaWAN vs BLE vs Thread/Matter vs LTE-M vs Wi-Fi

The brief named LoRaWAN or BLE. The honest answer is that **the radio is downstream of the buyer**,
and that for one of the two main buyer routes neither of those is the right choice.

---

## 4.0 The decision matrix

| Criterion | **LoRaWAN US915** | **BLE** | **Thread / Matter** | **LTE-M / NB-IoT** | **Wi-Fi** |
|---|---|---|---|---|---|
| Infrastructure you must supply | **A gateway** | A phone or hub | **A border router** | **None** | None (customer's router) |
| Works day one, no customer setup | No | No | No | **Yes** | No |
| Needs customer Wi-Fi credentials | No | No | Yes (for the BR) | **No** | **Yes** |
| Battery life, sensor duty cycle | **5–10 yr** | 2–5 yr | 1–3 yr | 2–5 yr | Months |
| Indoor basement/crawlspace reach | **Good (sub-GHz)** | Poor–moderate | Poor–moderate | **Good** | Poor |
| Per-device recurring cost | ~$0 (own gateway) | $0 | $0 | **$1–3/yr** | $0 |
| Downlink latency (for shutoff) | **Bad — see §4.2** | Good when connected | **Good** | Moderate | Good |
| Consumer ecosystem / retail appeal | None | Moderate | **Apple/Google/Amazon** | None | Moderate |
| Certification cost adder | LoRa Alliance **$3k + $1.2k** | — | CSA **$10.5k** | PTCRB/carrier | — |
| Open-source licence position | **No Apache-2.0 MAC** (BSD) | **Apache-2.0** (NimBLE/Zephyr) | **Apache-2.0** (connectedhomeip) | Vendor blobs | Vendor blobs |
| Best fit | **Multifamily / commercial** | Commissioning + local | **DTC retail** | **Insurer-mailed single-family** | Mains-powered only |

---

## 4.1 Public LoRaWAN cannot be relied on inside US homes

This is the finding that most changes the plan, so here is the evidence rather than the assertion.

**The networks, as of 2026-10-14:**

| Network | State | Usable for an indoor US home? |
|---|---|---|
| **The Things Network** | 249,612 members across 153 countries, but the model is explicit community reciprocity, and live forum activity is thin (**~43 posts in 7 days**). No gateway count and no coverage commitment published ([thethingsnetwork.org](https://www.thethingsnetwork.org/)) | **No** — hobbyist gateways, no SLA |
| **Helium IoT** | **358k+ active hotspots**, and genuinely cheap: **1 Data Credit = $0.00001**, billed per 24-byte increment of application payload, so a 24-byte uplink costs **$0.00001** with no annual network fee ([docs.helium.com](https://docs.helium.com/tokens/data-credit/)). But public messaging is now *"the world's largest LoRaWAN network and fastest growing cellular network"* — attention has moved | **No, not as a sole dependency** — incentive-driven, non-contractual coverage |
| **Senet** | **No longer exists independently.** `senetco.com` redirects to **Netmore**, which announced *"Netmore Acquires Actility"* on **26/01/2026** ([netmoregroup.com/news](https://netmoregroup.com/news/)) | **Maybe, contractually** — see below |
| **Netmore (ex-Senet + Actility)** | 50+ country networks, 18.4M+ devices contracted, 112,000 gateways, positioning as *"carrier-grade… for critical infrastructure"* with named verticals **utilities (water, gas, electricity) and properties** | **Your best carrier conversation** — but those are global figures; US gateway counts and pricing **unverified** |
| **Everynet US** | **Unreachable** — `everynet.com` fails with a TLS internal error | **No** (status unverified; treat as unavailable) |
| **MachineQ (Comcast)** | Dedicated enterprise deployments, no public network, no published pricing ([machineq.com](https://machineq.com/)) | **No for residential** — but a real channel for a multi-site commercial customer |
| **Amazon Sidewalk** | BLE short-range plus **LoRa and FSK at 900 MHz**; gateways are **Echo and Ring devices already inside American homes**. Qualification is reference-design qualification + end-product registration, and *"not every product must go through the testing process"* ([docs.sidewalk.amazon](https://docs.sidewalk.amazon/qualification/)) | **The interesting dark horse** — the only 900 MHz network plausibly *inside* the house. Severe payload limits (unverified) and total Amazon dependency |

**The decisive argument is operational, not coverage.** Every US public LoRaWAN footprint is either
community-contributed, incentive-contributed, or enterprise-dedicated. **None gives you an API you
can call before shipping a box to ask "is this address covered?"** A hardware company that ships
devices which might not connect has an unmanageable support and returns cost. And the
Netmore/Actility consolidation means the independent-operator market just got *smaller*.

Add that your sensor sits on a water main — in a basement, crawlspace or utility closet, behind
foundation concrete. That is the worst link budget in the building, and precisely where you need the
most margin.

---

## 4.2 The downlink problem — why LoRaWAN and remote shutoff do not mix

If you promise remote shutoff, this is the constraint that decides your architecture.

| LoRaWAN class | Downlink behaviour | Consequence for a shutoff command |
|---|---|---|
| **Class A** | Receive windows open **only** after the device uplinks | A shutoff command waits for the next scheduled uplink. At a 15-minute heartbeat your worst case is **15 minutes of flooding.** Unusable. |
| **Class B** | Scheduled ping slots synchronised to gateway beacons | Latency bounded to seconds, but beacon tracking costs power continuously, and it adds gateway timing requirements |
| **Class C** | Receive window open almost continuously | Latency is good, and **the power budget is gone** — Class C effectively means mains power |

**The resolution is architectural, not operational: do not put the shutoff decision on the WAN.**

- LoRaWAN carries **alerts uplink**, telemetry and configuration. It is an excellent uplink network.
- The **shutoff decision lives locally** — on the valve controller itself, or on a short local link
  (BLE, or a direct sub-GHz pairing) between the sensor and the valve. The cloud *confirms and
  reports*; it does not arbitrate the close.
- If a human must authorise the close, that is route A7 in
  [02-application-portfolio.md](02-application-portfolio.md) — advisory shutoff — and the latency
  budget becomes a dispatch SLA rather than a radio problem.

Write this into the product definition early. A "cloud closes the valve over LoRaWAN" architecture
will pass a demo and fail a real event.

---

## 4.3 Gateway economics — and the question it forces

Verified RAK pricing ([store.rakwireless.com](https://store.rakwireless.com)):

| Deployment | Gateway | Price **[V]** |
|---|---|---|
| Single home, customer's internet | RAK7266 WisGate Soho Lite | **$99** |
| Single home, better radio | RAK7268V2 WisGate Edge Lite 2 | **$154** |
| Home with unreliable or absent internet | **RAK7268CV2-V (Verizon LTE)** | **$299** |
| Multi-dwelling / small commercial | RAK7289V2 WisGate Edge Pro | **$382** |
| Campus / outdoor | RAK7285 Edge Ultra / RAK7393 | $799 / $439 |
| Bench | RAK7271/7371 USB concentrator | $99 |

Network server: **The Things Stack Open Source** is Apache-2.0 and self-hostable (~$18/mo VPS), or
TTI's hosted **Discovery free** (10 devices), **Standard €190/mo** including 1,000 devices
([thethingsindustries.com](https://www.thethingsindustries.com/stack/plans/)). Alternatively
ChirpStack (MIT) plus `chirpstack-pg-to-sqlite` runs the whole network server as a single Rust
binary on SQLite for ~$6/mo.

**The arithmetic that decides the architecture:**

| | Single-family | Multi-dwelling (40 units) |
|---|---|---|
| Gateway | $99–299 | $382 |
| Nodes | 1 × ~$27 | 40 × ~$12 = $480 |
| **Gateway as % of hardware cost** | **79–92%** | **44%**, and falling with every unit |

**In a single-family home the gateway costs three to ten times the sensor.** That kills a consumer
price point and it is dead weight in an insurer-funded BOM. In a building it amortises immediately.

**So the honest question: if you are shipping a gateway per home anyway, what is LoRa buying you?**
Nothing that BLE-plus-phone or LTE-M does not do more cheaply. The $299 Verizon gateway is the one
exception worth considering even for single-family — it removes the homeowner's router from your
support burden entirely, and support cost is frequently larger than the $145 delta.

---

## 4.4 The recommendation: segment the radio by buyer

**There is no single right radio here, and forcing one is the mistake.**

| Buyer route | Radio | Why |
|---|---|---|
| **Multifamily / commercial** (route b) | **LoRaWAN US915 + a building gateway** | One gateway across dozens of nodes; sub-GHz reaches risers and basements; 5–10 year battery; no reliance on anyone's managed Wi-Fi — which is exactly why Eddy chose LoRaWAN and Alert Labs chose cellular |
| **Insurer-mailed single-family** (route a) | **LTE-M / NB-IoT** | The only option that works on arrival with no gateway, no hub and no Wi-Fi credentials. You cannot install a gateway in someone's house by mail. ~$1–3/device/year of data against a $49–60/home/year price point |
| **DTC retail** (route c) | **BLE + Thread/Matter** | Zero infrastructure cost, and Matter gets you Apple/Google/Amazon shelf appeal. `connectedhomeip` is **Apache-2.0** and ships an in-tree `examples/water-leak-detector-app` |
| **Mains-powered products** (A12, A13, A14) | **Wi-Fi + Thread/Matter** | Mains power removes the low-power argument entirely; ESP32-C6-WROOM-1 at **$2.85–3.40 [V]** gives Wi-Fi 6, BLE 5.3 and 802.15.4 in one part |

**And BLE earns a place in every variant regardless of WAN choice** — for commissioning, install
verification, local diagnostics and field recovery. Zephyr's SMP/mcumgr over BLE is how a technician
re-flashes a bricked sensor in a basement with no WAN at all. That capability is worth more than it
looks the first time a thousand-unit batch ships with bad firmware.

### The practical sequencing consequence

**Lock the module *ecosystem* in week 1; defer the *radio* to the P0 buyer gate.** Choose a vendor
family that spans US915, BLE and ideally LTE-M on one SDK, so the firmware, OTA and certification
learning carries across whichever radio the buyer decision selects. See
[06-hardware-and-devkits.md](06-hardware-and-devkits.md) — this is why the RAK/Nordic/Espressif
comparison is framed around SDK breadth rather than per-part price.

---

## 4.5 On OpenThread and Matter specifically

The brief asked about these as Apache-2.0 IPv6 mesh in the Google/Apple ecosystem. Three
corrections and one opportunity:

**`openthread/openthread` is BSD-3-Clause, not Apache-2.0** — verified in `data/repos.csv`. It is
permissive and perfectly safe for a closed product, but if the reason you wanted Apache-2.0 was
§3's explicit patent grant, BSD-3 is merely *silent* on patents rather than granting them.
`project-chip/connectedhomeip` **is** Apache-2.0, as is `espressif/esp-matter`.

**Matter's water device types exist, and the leak detector example is in-tree** —
`examples/water-leak-detector-app` in connectedhomeip — but it currently has **only a Linux build,
no embedded platform port**, so you port it. And upstream states plainly that the examples are
*"not production-ready and are not intended to be shipped as-is in a commercial product."* That is
an engineering and CSA-certification warning, not a licence restriction. Use **`esp-matter` plus
`esp-matter-tools`**, which add the manufacturing and DAC-provisioning flow that certification
actually requires.

**Thread needs a border router**, which means mains power and the customer's Wi-Fi — so it solves
none of the problems that made you consider LoRaWAN. Its value is purely **channel**: Matter
certification (CSA Adopter **$7,500/yr + $3,000/product = $10,500 [V]**) buys Apple Home, Google
Home and Alexa compatibility, which matters for retail and matters not at all for an insurer.

**The opportunity:** `automatous-io/shelly-1-gen4-matter-thread` is **Apache-2.0**, runs on
**ESP32-C6 with esp-matter**, and implements a working **Matter-over-Thread relay controller** with
multi-fabric commissioning, OTA via Home Assistant and a Thread border router. For a valve
controller that is a working template rather than a blank page — the single most directly reusable
reference project found in this research.
