# ADR-001 — LTE-M / NB-IoT instead of LoRaWAN

| | |
|---|---|
| **Status** | Proposed. Design only, no code on this branch. |
| **Date** | 2026-10-14 |
| **Branch** | `design/lte-m-nbiot` |
| **Unlocks** | **Insurer-mailed single-family** — route (a) in [`03-business-models.md`](../../03-business-models.md) |
| **Decides** | Fork C in [`08-roadmap.md`](../../08-roadmap.md) §6, for the carrier channel |

## Context

`main` ships LoRaWAN US915 and a gateway. For a building that is correct and cheap: one RAK7289V2
at **$382 [V]** amortises across dozens of **$12** nodes. For a device a carrier posts to a
policyholder it is unworkable, and the reason is not coverage — it is that **you cannot install a
gateway in someone's house by mail**, and no US public LoRaWAN operator exposes an API to
pre-qualify an address before you ship the box
([`04-connectivity.md`](../../04-connectivity.md) §4.1).

That matters because the insurer channel is the one with the proven commercial model: 40+ carriers
for Ting, 28 for LeakBot, **$49–60 per home per year [V]**, hardware paid for, near-zero marketing.
It is also the channel where `main`'s architecture cannot compete.

Ting solves this with Wi-Fi, and pays for it: it needs the homeowner's 2.4 GHz credentials, fails
behind a surge protector or UPS, and needs one sensor per electrical panel. LeakBot's clamp-on is
the closest analogue to our product and runs on cellular.

## Decision

Replace the LoRaWAN transport with **LTE-M (primary) and NB-IoT (fallback)** on the **Nordic
nRF9151 SiP**, and delete the gateway and the network server from the architecture entirely.

Why nRF9151 rather than an LTE module from a modem vendor: it is *"certified for global
operation"* across bands 1-5, 8, 12, 13, 17-20, 25, 26, 28, 65, 66, 85, it carries GNSS and
**NB-NTN** (satellite NB-IoT) for the rural tail, it stays inside the Nordic/Zephyr toolchain the
firmware already uses, and the dev kit is **$130.16 [V]** (Mouser). The cost is licence exposure,
below.

## Architecture delta

```
  main:    device ──LoRaWAN──► gateway ──► The Things Stack ──webhook──► backend
                                 ^              ^
                           you ship this    you host this

  ADR-001: device ──LTE-M──► carrier network ──CoAP/DTLS──► backend
                                                  ^
                                        no gateway, no LNS, no webhook
```

**Two whole layers disappear.** That is the headline: no gateway BOM, no gateway support burden, no
LNS to host, no Join Server key custody, no `chirpstack-*` tooling, no LoRa Alliance membership
(**$3,000/yr [V]**) and no LoRaWAN end-device certification (**$1,200 [V]**).

What replaces them is a direct device-to-backend path. The device speaks **CoAP over DTLS 1.2 with
a pre-shared key**, not MQTT: CoAP's header is ~4 bytes against MQTT's ~2 bytes of fixed header plus
topic string on every publish, and DTLS session resumption avoids a full handshake per wake. On a
device that wakes for a few seconds every 15 minutes for five years, header bytes and handshake
round-trips are the power budget.

## Migration plan

Concrete, file by file. "Unchanged" is as important as "rewrite".

| Path | Change | Effort **[E]** |
|---|---|---|
| `contracts/INTERFACES.md` §4 | **New ingest envelope.** The TTS webhook shape is gone; the device posts its own 8 bytes with no LNS metadata. Keep §4 for the LoRaWAN variant and add §4b for the cellular one, because the backend must accept both during any transition. | 1 day |
| `contracts/INTERFACES.md` §1 | **Unchanged.** The 8-byte payload is transport-agnostic and stays byte-for-byte identical. | — |
| `firmware/src/uplink.{c,h}` | **Unchanged.** `uplink_pack()` produces bytes; it does not care what carries them. This is the payoff for keeping the pure modules free of Zephyr includes. | — |
| `firmware/src/leak_detect.*`, `valve.*`, `install_probe.*` | **Unchanged.** Same reason. | — |
| `firmware/src/main.c` | **Rewrite the transport half.** `lorawan_send()` → `nrf_modem`/`sockets` + CoAP client; join/ABP → LTE attach, PSM and eDRX configuration. Sampling, scheduling and the decision site are untouched. | 2–3 weeks |
| `firmware/prj.conf` | `CONFIG_LORAWAN*` out; `CONFIG_NRF_MODEM_LIB`, `CONFIG_LTE_LINK_CONTROL`, `CONFIG_COAP`, `CONFIG_MODEM_KEY_MGMT`, PSM/eDRX timers in. | 2 days |
| `firmware/boards/` | New overlay for **nRF9151-DK**; the RAK4631 and nucleo_wl55jc overlays stay for the LoRaWAN build. | 3 days |
| `codec/` | **Unchanged**, and still useful — the same decode logic serves the backend and any future LNS. | — |
| `simulator/` | Emit the §4b envelope instead of the TTS one. The *physics* in `fleet.py` and `scenarios.py` is untouched; only the serialiser changes. Add a cellular packet-loss model (coverage holes, PSM wake failures) distinct from LoRa's. | 1 week |
| `detection/` | **Unchanged. All 130 tests and the measured 0.3383 nuisance rate carry over verbatim.** | — |
| `backend/app/ingest.py` | Add a `/api/v1/uplink/direct` path for §4b. Idempotency moves from `(dev_eui, f_cnt)` to `(dev_eui, device_seq)` — **see "What breaks"**. | 1 week |
| `backend/app/codec.py` | **Unchanged.** Already decodes server-side from raw bytes. | — |
| `docker-compose.yml` | Delete the commented TTS/Postgres/Redis services. Nothing replaces them. | 1 hour |
| CI | Add an nRF Connect SDK build job — still not buildable on the current box. | 2 days |

**Total: 5–7 person-weeks [E]**, dominated by the firmware transport rewrite.

## What breaks

**1. `f_cnt` disappears, and with it the idempotency key.** Contract §4 deliberately has no sequence
number in the payload because the LNS supplies `f_cnt`. Remove the LNS and nothing supplies it. All
eight payload bytes are allocated, so there is nowhere to put one — the same squeeze that produced
amendment A1. Options, in order of preference:

- Use the **DTLS epoch plus sequence number**, which the stack already maintains. Free, but opaque
  to the application layer and reset on session renegotiation.
- Spend **two of the five reserved flag bits** (§1 byte 6) on a 2-bit rolling counter. Cheap, enough
  to dedup a retry burst, not enough for long gaps.
- Add a **9th byte**. Trivial on cellular, where a 1-byte MTU difference is irrelevant — and this is
  the honest answer. The 8-byte limit exists to fit LoRaWAN DR0; cellular has no such constraint.
  **Recommended: go to 9 bytes with an explicit `seq`, and keep the 8-byte form for LoRaWAN builds.**

This is worth noting as a general lesson: `main`'s payload is tuned to a constraint that this branch
removes, and carrying the constraint anyway would be cargo-culting.

**2. The power model changes materially and is not a scaling of the old one.** LoRa SX1262 TX at
+22 dBm draws ~118 mA for tens of milliseconds. An LTE-M transmission at PC3 (23 dBm) draws
hundreds of milliamperes for substantially longer, and attach/TAU events are worse. The
Li-SOCl₂ + hybrid-layer-capacitor topology from [`06-hardware-and-devkits.md`](../../06-hardware-and-devkits.md)
still applies but the HLC must be sized up, and the ~40% cold derating now bites harder because
cellular current peaks are where a cold cell's internal resistance shows up. **Five-year battery
life on LTE-M at a 15-minute heartbeat is an assumption, not a result** — measure it before quoting
it to a carrier.

**3. New certification nobody budgeted.** [`07-compliance-and-certification.md`](../../07-compliance-and-certification.md)
costs FCC, NRTL, IAPMO and the radio alliances. It does **not** cost **PTCRB/GCF device certification
plus per-carrier approval** (Verizon, AT&T, T-Mobile each run their own), which a cellular product
needs before it can attach to a commercial network. Using a pre-certified SiP covers the modem, not
the finished device. This is a **real gap in the pack's cost model** and should be added to
`data/cert-costs.csv` when this branch is chosen. Figure **unverified** — get quotes.

**4. Licence exposure moves the wrong way.** `nrf_modem` is a **Nordic-5-Clause binary blob** and is
unavoidable on nRF91: *"must only be used with a Nordic Semiconductor ASA integrated circuit"* and
*"must not be reverse engineered, decompiled, modified and/or disassembled"*. Shipping a closed
commercial product is permitted — that is what the binary exception is for — but **the BOM is locked
to Nordic for the life of the firmware**, and you cannot patch the modem layer. `main`'s whole
portability argument (upstream Zephyr keeps STM32WL, nRF52840 and ESP32-C6 all on the table) does
not survive this branch. It is a deliberate trade, not an oversight.

## Cost

| Line | Delta vs `main` |
|---|---|
| Radio module | nRF9151 SiP vs RAK3172 $5.99 **[V]** — SiP price **unverified**, expect several × |
| Gateway | **−$99 to −$382 per home [V]** |
| LoRa Alliance membership + cert | **−$4,200 [V]** |
| PTCRB/GCF + carrier approval | **+unverified**, and not in the pack's model |
| Data | **+$1–3/device/year [E]** against a $49–60/home/year price point |
| Engineering | +5–7 person-weeks **[E]** |
| BOM, net | up on the module, **down overall once the gateway is counted** |

The gateway deletion is what makes this cheaper in a single-family deployment despite a dearer
module — exactly the arithmetic in [`04-connectivity.md`](../../04-connectivity.md) §4.3, where the
gateway is 79–92% of single-family hardware cost.

## When to pick this

At the **P0 buyer gate**, if the countersigned pilot comes from a **carrier, MGA or aggregator**
(vipHomeLink, Asurion, HSB) for devices mailed to policyholders. Signals that confirm it: the buyer
will not fund an in-home install visit; the target stock includes homes without reliable Wi-Fi; the
pilot spans multiple states with no site survey.

Do **not** pick it for multifamily. One building gateway and forty $12 nodes beats forty cellular
radios and forty data plans, and it is not close.

## Rejected within this branch

- **NB-IoT only.** Better building penetration, but higher latency and worse carrier support in
  North America than LTE-M. Keep it as fallback, not primary.
- **Wi-Fi (the Ting approach).** Zero recurring cost, but it needs the homeowner's credentials and
  inherits every failure mode in the PCWorld review. It defeats the purpose of mailing the device.
- **Amazon Sidewalk.** Genuinely interesting — the only 900 MHz network whose gateways (Echo, Ring)
  are already inside American homes, and nRF54L15 lists support. But payload limits are severe and
  **unverified**, and it makes Amazon a single point of failure for a product an insurer depends on.
  Worth a spike at P2, not a bet at P0.
- **Satellite NB-NTN as primary.** The nRF9151 supports it and it would solve the rural tail, but
  cost and latency make it a per-device option, not a default.
