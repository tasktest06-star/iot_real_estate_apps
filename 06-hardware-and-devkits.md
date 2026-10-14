# 6. Hardware — Dev Kits, Production Modules, Sensing Front Ends, Community

Prices verified **2026-10-14** from vendor stores and distributor aggregators. **[V]** =
vendor- or distributor-published; **[E]** = estimate; anything else says **unverified**.

---

## 6.0 The one decision that is actually irreversible

Everything else in year one is cheap to reverse — cloud is rentable, models retrain, enclosures
reprint, buyer routes switch. **The module and SDK choice is not.** It sets:

- **FCC modular inheritance**, worth roughly **$10–25k and 6–10 weeks** versus a full
  intentional-radiator campaign;
- the **RF layout rules you must obey to keep that inheritance** — module grants carry antenna type
  and trace conditions, and violating them forfeits modular approval and buys you a full Part 15C
  test;
- the RTOS, the OTA stack, the power budget that determines battery life, and your stocking and EOL
  exposure.

**So: lock the module *ecosystem* in week 1; defer the *radio* to the P0 buyer gate.** Choose a
vendor family spanning US915, BLE and ideally LTE-M on one SDK, so firmware, OTA and certification
learning carries across whichever radio the buyer decision selects.

---

## 6.1 Dev kit → production module pairs

| Ecosystem | Prototype on **[V]** | Production module **[V]** | Silicon | Radios | US915 | Cert pack |
|---|---|---|---|---|---|---|
| **RAK WisBlock** (LoRa + BLE) | RAK19007 base **$9.99** + RAK4631 core **$17.99** | RAK4631 is the module, **$17.99** | nRF52840 (M4F, 1 MB/256 KB) + SX1262 | LoRaWAN 1.0.3 A/B/C + BLE 5.0 | **Yes** | Downloadable CE/FCC/ISED/KC/RCM certificates; FCC ID string not on the page — *unverified* |
| **RAK WisDuo** (cheapest LoRa) | RAK3272S breakout **$15.00** | **RAK3172 $5.99**; -T (±2.5 ppm TCXO) $6.49; -TE (±0.5 ppm) $6.99. Axis **$6.47 @500+** | STM32WLE5CC single die, 256 KB flash ECC / 64 KB RAM | LoRaWAN A/B/C | **Yes** (EU868/US915/AU915/KR920/AS923/IN865/RU864) | **Best in class — direct downloads for [FCC](https://downloads.rakwireless.com/LoRa/RAK3172/Certification/RAK3172_FCC_Certification.zip), [ISED](https://downloads.rakwireless.com/LoRa/RAK3172/Certification/RAK3172_ISED_Certification.pdf), CE and [LoRa Alliance](https://downloads.rakwireless.com/LoRa/RAK3172/Certification/RAK3172_Lora_Alliance_Certification.pdf)** |
| **RAK LoRa+BLE on one die** | RAK11722 WisBlock core **$15.00** | **RAK11720 $7.99** | Ambiq **Apollo3 Blue** + SX1262 | **LoRa + BLE in one module** | datasheet 404'd — *unverified* | *unverified* |
| **Seeed / ST single-chip** | **Wio-E5 Dev Kit $26.99** ($22.99 @10+) | **Wio-E5 module $6.49** | STM32WLE5JC | LoRaWAN; +20.8 dBm; 2.1 µA sleep | **Yes** | Wiki lists CE-RED, FCC DSS/DTS, TELEC, IC — IDs not quoted, *unverified* |
| **Seeed BLE** | **XIAO nRF52840 $9.99** | Use a certified module (Raytac/Fanstel/u-blox) — XIAO is a board | nRF52840 | BLE 5.4 | n/a | *unverified* |
| **Seeed Thread/Matter** | **XIAO ESP32C6 $5.20** | ESP32-C6-WROOM-1 (below) | ESP32-C6 | Wi-Fi 6, BLE 5, Thread, Zigbee | n/a | inherits Espressif's grant |
| **Nordic BLE (current)** | **nRF54L15 DK $38.22** (Newark) | Raytac AN54L / AN54LQ / AN54LM-20A — pricing *unverified* | nRF54L15 | BLE, **Channel Sounding**, Matter, Thread, **Aliro**, **Amazon Sidewalk** | n/a | Raytac states FCC/IC/CE/UKCA/MIC/KC/SRRC/NCC/RCM per module |
| **Nordic BLE (mature)** | **nRF52840 DK $32.87** | **Raytac MDBT50Q-1MV2 $6.92–9.45** | nRF52840 | BLE 5, Mesh, NFC, Thread, 802.15.4, Matter | n/a | as above |
| **Nordic cellular** | **nRF9151 DK $130.16** (Mouser) | nRF9151 SiP — *"certified for global operation"*, LTE-M/NB-IoT/**NB-NTN**/DECT NR+ + GNSS. Bare SiP price *unverified* | nRF9151 (M33) | LTE-M, NB-IoT | n/a | [Nordic cert page](https://www.nordicsemi.com/Products/Wireless/Low-power-cellular-IoT/nRF91-Series-certifications); PTCRB detail *unverified* |
| **ST STM32WL** | **NUCLEO-WL55JC1 $53.24**. **The "1" suffix is the high band (868/915) — JC2 is 433** | **STM32WLE5CCU6 $10.00 @1 → $6.18 @500 → $4.17 @10k** | STM32WLE5 (M4 + SX126x on one die) | LoRa/(G)FSK/BPSK | **Yes** | **None — bare silicon, you own the whole FCC filing.** The ~$2 saving vs RAK3172 costs five figures |
| **Espressif** | ESP32-C6-DevKitC-1 **~$9**; ESP32-H2-DevKitM-1 ~$10 | **ESP32-C6-WROOM-1-N4/N8/N16 $2.85–3.40** sample; Mouser **$3.13 @500** | ESP32-C6 RISC-V @160 MHz | **Wi-Fi 6, BLE 5.3, 802.15.4 → Thread 1.3 / Zigbee 3.0 / Matter**; 20 dBm BLE | n/a | **Best-documented modular story — a [482-document certificate index](https://www.espressif.com/en/support/documents/certificates)** |
| **Silicon Labs** | xG24 Explorer **XG24-EK2703A $39.00** | **MGM240PB32VNA3 $6.44 @1000** | EFR32MG24 | BLE 5.4 + 802.15.4 | n/a | pre-certified; FCC ID *unverified* (silabs.com returned 403) |
| **Raspberry Pi** | Pico 2 "from $5" | **Radio Module 2 $4**, and **"in production until at least January 2036"** | RP2350 + RM2 | Wi-Fi 4 + BT 5.2 | n/a | compliance portal; FCC ID string not published |

### Verdict

**Prototype on RAK19007 + RAK4631 ($28 total)** — LoRa and BLE together, pre-certified, fastest to a
bench node. **Ship on RAK3172 ($5.99)** if you do not need BLE in the product, or **RAK11720
($7.99)** if you do. **Second-source with Seeed Wio-E5 ($6.49)** — same STM32WLE5 die, different
vendor, so a genuinely dual-sourced BOM is achievable, which matters because §6.4 risk 8 is real.
**Use ESP32-C6-WROOM-1 ($2.85–3.40) for any mains-powered product**, where mains power kills the
low-power argument and Matter/Thread buys retail shelf appeal.

**On Raspberry Pi RM2:** a $4 radio module with a **written production commitment to 2036** is
unusually good supply-chain risk for a startup, and RP2350 is cheap. But Wi-Fi 4 only, no 802.15.4,
no sub-GHz, and far thinner Zephyr support than nRF or ESP32. Use it for a **mains-powered** plug
device or a bridge; **not** for the battery leak sensor.

### Gateways **[V]**

RAK7266 Soho Lite **$99** · RAK7268V2 Edge Lite 2 **$154** · **RAK7268CV2-V (Verizon LTE) $299** ·
RAK7267 Soho Pro $254 · RAK7240V2 Edge Prime $337 · **RAK7289V2 Edge Pro $382** · RAK7393 Connect
Outdoor $439 · RAK7285 Edge Ultra $799 · RAK7271/7371 USB concentrator $99.
**Dragino LPS8v2** ships **LoRaWAN server + ChirpStack + Node-RED on the gateway itself** (US915
available) — the "works with no cloud" option, price not listed.

### Off-the-shelf comparables worth buying as teardowns

- **Milesight EM300-SLD** (1.5 m probe) / **EM300-ZLD** (3 m rope): triggers at **5 mm**, IP67,
  **>10 yr at SF7 US915**, CE/FCC/ISED/RCM/Telec.
- **Milesight WS303** mini: **0.5 mm** trigger, gold-plated probes, CR2450, **~5.7 yr**, IP67,
  63×63×14 mm. **This is the spec sheet to beat for A3.**
- **Dragino LWL02 / LDS02 / LT-22222-L** — the LT-22222-L is an I/O controller with **2× relay
  5 A @250 VAC**, 2 DI, 2 AI, STM32L072 + SX1276, US915 available. Useful for a valve-control
  prototype without designing a board.

> **Critical finding for the plug product:** **Milesight's WS51x wall socket is EU Type F / FR
> Type E only and CE-only — no FCC, no UL/ETL, no NEMA 5-15 variant** — despite supporting the
> US915 band. The **WS523** portable socket *does* have a US 15 A variant carrying CE/FCC/RCM/SAA/
> UKCA, but **neither is UL/ETL listed.** There is **no off-the-shelf North American NEMA 5-15
> metered LoRaWAN or cellular smart plug.** That is application A12 in
> [02-application-portfolio.md](02-application-portfolio.md).

---

## 6.2 Sensing front ends

### Non-invasive leak detection — the thermal method

LeakBot's **Thermi-Q**: clip onto the main, measure air *and* water temperature, and infer
continuous flow from a **persistent small ΔT** that normal intermittent use does not produce.
Claimed sensitivity **5 ml/min**. Physically: two temperature sensors and a spring clip.

| Function | Part | Spec | Price **[V]** |
|---|---|---|---|
| Pipe + ambient temperature | **TI TMP117** | ±0.1 °C class, I²C, 6-pin WSON | **$3.62–3.73 @1k** |
| Cheap alternative, long leads | **DS18B20** | 1-Wire, waterproof probe variants | $4.68 @1k |

**Use two TMP117, not an NTC divider.** You are detecting a small persistent ΔT against a noisy
ambient; NTC self-heating and tolerance stack will eat the signal. Keep one cheap NTC as a
sanity/tamper channel only.

### Acoustic and vibration

| Part | Spec | Price |
|---|---|---|
| **TDK InvenSense T5838** | PDM MEMS mic, SNR 68 dB, AOP 133 dB SPL, **Acoustic Activity Detect at ~20 µA** | *unverified* |
| Knowles SPH0645LM4H-B | I²S MEMS mic, the cheap standard | **$1.45 @1k [V]** |
| ST IIS3DWB | ultrawide-bandwidth 3-axis vibration | **$13.10–15.37 @1k [V]**; BW/noise *unverified* |

**The T5838's 20 µA acoustic activity detect is the single most important spec here** — it lets a
battery device sit in wake-on-sound and only burn MCU cycles when there is pipe noise. That is what
makes battery-powered acoustic leak detection viable at all. The IIS3DWB at $13–15 is **2–3× the
entire radio module**, so it belongs only in a commercial SKU. For a consumer sensor, a **mic** —
clamped to the pipe with a stethoscope-style coupler — is the cost-correct choice over an
accelerometer.

### Flow and pressure

| Part | Role | Spec | Price **[V]** |
|---|---|---|---|
| **TI TDC1000** | ultrasonic AFE | TX 31.25 kHz–4 MHz, RX jitter 50 ps RMS, **1.8 µA @2 SPS**, PT1000 interface | **$2.60 @1k** |
| **TI MSP430FR6047** | integrated ultrasonic MCU | **<25 ps differential ToF**, detects **<1 L/h**, **~3 µA at 1 meas/s**; exceeds ISO 4064 / OIML R49 / EN 1434 | **$9.25 @1k** |
| Sensirion SLF3S-1300F | thermal, no moving parts | **≤40 ml/min full scale**, 5% of reading, ≤6 mA | not published |
| ADI MAX35103 | ultrasonic TDC | analog.com timed out | *unverified* |

**Framing that matters:** MSP430FR6047 plus two transducers plus a machined flow body is a
**water-meter-class BOM**, not a leak-sensor BOM. It belongs in A13 where you are already paying for
a valve body. And the Sensirion part's **40 ml/min full scale is a medical/analytical range** — right
for microleak detection on a bypass loop, completely wrong for whole-house flow.

**On pressure-decay (Phyn's method):** "high-rate pressure sensing" is a **signal-processing**
claim, not an exotic sensor. US10352814B2 identifies leaks from pressure *alone*, from a *single*
sensor, via persistent turbulence in specific frequency bands lasting beyond ~45 minutes, monotonic
pressure slope interrupted by regulator resets, pressure drop during no-use periods, and abnormal
variation in stable pressure. You need an ordinary transducer sampled fast enough to see turbulence
spectra.

> **Unverified:** Honeywell MPR/ABP board-mount pressure sensor ranges, accuracy and supply current —
> both product URLs 404'd.

### Valve actuators — where the data is weakest

| Option | Verified | Missing |
|---|---|---|
| **U.S. Solid motorised ball valves** | **1,002 SKUs**; 1/8″–3″; DC 24 V (428 SKUs), AC 110/220 V (225), DC 12 V (191); stainless/brass/PVC; 2-wire auto-return (196) | **Current draw, stall current, time-to-close — unverified.** Product pages returned HTTP 429 repeatedly |
| **Orbit irrigation inline valves** | 57281 1″ FPT **$19.99**; 57280 ¾″ $18.89; 57020 1″ w/ flow control $19.59 | Solenoid type per model not stated |
| Tonhe, Belimo, Hunter | — | **unverified** (403/not reached) |
| **Retrofit clamp-over-lever** (Guardian, LeakSmart) | — | **unverified** — getguardian.com 429, leaksmart.com 403 |
| **Moen Flo** (the baseline) | Installs on the main; **requires a nearby AC outlet** (25-ft extension offered); measures flow, temperature, pressure; **daily microleak test** detecting "as small as a drop per minute" | **No third-party certification (IAPMO/UPC/NSF/ETL) stated anywhere on Moen's page** |

**Four conclusions you can bank without the missing amps:**

1. **Mains plus backup is effectively mandatory for an inline motorised ball valve.** Moen — the
   market leader — requires an AC outlet and sells an extension cable rather than going battery.
   That is strong evidence a ½″–1″ motorised ball valve's stall torque cannot be served from primary
   cells over a five-year life.
2. **Battery-only shutoff requires a latching solenoid, not a motor.** A tens-of-millisecond pulse
   holding state with zero standing current is the only topology that gets years on cells.
   Irrigation solved this at **$19–22 per valve body**, but those bodies are **not potable-rated**
   and are diaphragm valves needing minimum differential pressure, so they can fail to close at low
   ΔP.
3. **The retrofit clamp-over-lever approach is the only path that avoids plumbing-trade installation
   entirely** — no cut pipe, no wetted materials, no NSF 61, no UPC listing. Lowest regulatory
   friction; highest mechanical risk. See [02](02-application-portfolio.md) §2.3.
4. **Fail-safe must mean fail-*as-is*, not fail-closed.** Latching is inherently fail-as-is. Give
   every unit a mechanical manual override.

> **Do not design the battery around a datasheet you have not held.** Buy one U.S. Solid ¾″ DC-24 V
> valve and one retrofit kit and measure. One afternoon, a few hundred dollars.

### Plug-in electrical fault detection

Ting detects **micro-arcing** by **measuring voltage 30 million times per second** at a single
outlet and analysing the waveform. It carries an internal board temperature sensor and explicitly no
microphones or cameras. So the technique is **wideband voltage-waveform analysis**, not current
sensing and not per-circuit — which is why one plug covers a house.

Your front end would be a safety-rated capacitive/resistive divider from line into a wideband ADC
sampled fast enough to resolve micro-arc bursts (hundreds of kHz to MHz), plus aggressive DSP. The
hard parts are mains isolation done to a standard, and the classifier.

**The pragmatic alternative — metering ICs with harmonic capability.** Much lower risk, much lower
ceiling: you catch heating and loose-connection signatures and harmonic anomalies, not true
micro-arcs.

| Part | Role | Price **[V]** |
|---|---|---|
| **ST STPM34** | 3-phase AFE, 4× independent 24-bit ADC | **$1.59 @1k** — cheapest credible AFE by ~1.5× |
| **ADI ADE7953** | single-phase, two current channels | $6.08 @1 → **$2.46 @1k** |
| ADI ADE7816 | 1 voltage + 6 current channels | $7.56 @1k |
| **ADI ADE9000** | poly-phase with harmonics | $22.49 @1 → **$10.39 @1k** |
| **TI AMC130M03** | 3-ch 16-bit **64 kSPS with reinforced isolation (5000 V RMS)** and integrated isolated DC/DC | **NO DISTRIBUTOR STOCK FOUND** — do not design in without a TI supply commitment |
| **LEM ART Rogowski coils** | 10–10,000 A RMS, class 0.5, **50/60 Hz to 700 kHz open-circuit** | price *unverified* |
| TMP117 / DS18B20 | connector-heating channel — what Ting itself does | $3.62 / $4.68 @1k |

**Two notes.** AMC130M03 solves creepage and sampling in one part, but **64 kSPS means a 32 kHz
Nyquist — likely too slow for true micro-arc HF content.** And the **LEM ART's 700 kHz open-circuit
bandwidth is the only part surveyed with bandwidth in the arc-relevant range**, which is the
technical argument for going panel-level (A11) rather than plug-level if you pursue electrical at all.

---

## 6.3 Community health

| Ecosystem | Hard numbers | Verdict |
|---|---|---|
| **Zephyr** | **105,063 commits, 2,601 contributors, 159 repos**; >10M devices; **1,000+ boards**; Apache-2.0; neutral governance; **CNA with a PSIRT**; a **v3.7.x LTS** branch alongside v4.4.x | **Strongest long-term bet.** The LTS branch plus a PSIRT is exactly what you want under a ten-year property device |
| **Espressif / ESP-IDF** | Many parallel maintained branches with **published EOL dates** (*"ESP-IDF v5.2 reached end-of-life in August 2026"*) | **Best for the mains product.** Published branch EOLs are a genuine asset. **Easiest to hire for, by a wide margin** |
| **Nordic DevZone** | Live Q&A, monthly contributor leaderboard, DevAcademy courses — but **visible unanswered questions within 24 h** and no published SLA | **Best-supported BLE path**; nRF54L15 publicly lists Matter, Thread, **Aliro and Amazon Sidewalk** |
| **RAKwireless** | Discourse: **454 posts / 37 active users / 96 signups in 7 days** | **Healthy for its size**, and the real differentiator is **downloadable certificates** — worth more to you than forum volume |
| **Home Assistant** | **By far the liveliest measured: 14,999 posts / 23,918 active users / 3,590 signups in 30 days**; Apache-2.0; Nabu Casa commercial | **Your free distribution channel.** A first-class integration is the cheapest credibility in this market |
| **The Things Network / TTI** | 249,612 members, 153 countries; forum 100,203 posts all-time; device repo **1,133 devices from 151 manufacturers** — **but only ~43 posts in the last 7 days** | **Large registered base, modest live activity.** Commercially viable via TTI (Discovery free / **Standard €190/mo**); the community network is not a dependency to take |
| **ChirpStack** | Forum `/about` renders **all zeros** — stats not disclosed. Embedded in real products (Dragino LPS8v2) | ⚠️ **Key-person risk.** Great software, thin bus factor. Fine to self-host; risky as your whole platform without a fallback |
| **Seeed** | Extensive wiki; **Seeed Fusion PCBA** with instant online quoting, 24 h minimum build, no stated MOQ | **Second-source silicon and a credible low-volume CM** — not your software ecosystem |
| **Meshtastic** | 26 LoRa regions, 39 languages; device counters render as "0+" placeholders — size *unverified* | **Not a product platform.** A cheap field-test radio and a source of SX126x know-how |
| **Milesight / Dragino** | Deep catalogues with full band tables and certification lists; no community metrics | **Buy from them, do not build on them.** Best teardown and white-label candidates |

### Dead or dying — act on these

1. **Arm Mbed OS is EOL (July 2026).** Arm states Mbed OS and the Mbed Platform reach end of life,
   the website will be archived and online builds will stop. Confirmed structurally: **`os.mbed.com`
   now redirects to `github.com/ArmMbed`.** Reject any tutorial, BSP or contractor proposal built on
   Mbed.
2. **Senet no longer exists independently** — redirects to Netmore, which **acquired Actility in
   January 2026**. Two of the three names in the original brief are now one vendor.
3. **Everynet is unreachable** — TLS handshake failure. Not proof of shutdown, but not a carrier to
   plan a product around. *Unverified; treat as unavailable.*
4. **ESP-IDF v5.2 reached EOL August 2026** — pin to a branch with remaining life.
5. **TI AMC130M03 has no distributor stock** anywhere in the broker network.
6. **ST B-L072Z-LRWAN1** lifecycle *unverified* (st.com timed out). Treat as legacy; use STM32WL.

---

## 6.4 What could not be verified, and the cheapest way to close each gap

| Gap | Why | Next step |
|---|---|---|
| Exact FCC ID strings for RAK, Seeed, Espressif, Raytac, SiLabs modules | fccid.io, fcc.report, apps.fcc.gov, fcc.gov and archive.org all blocked | Download the vendor cert PDFs (RAK links are in §6.1) or query the FCC OET database from an unblocked network |
| **Valve current draw, stall current, time-to-close** | ussolid.com HTTP 429 on four attempts; Hunter/LeakSmart/Guardian 403/429 | **Buy one valve and one retrofit kit and measure.** One afternoon, a few hundred dollars — the highest-value unblock on this list |
| NSF/ANSI 61 and 372, CSA B125.3, FM Approvals water standards | nsf.org, csagroup.org, fmapprovals.com all 403 | Call IAPMO R&T and NSF directly — **both NSF International and IAPMO EGS are OSHA-recognised NRTLs**, so one body can scope both plumbing and electrical |
| US Cyber Trust Mark status | every fcc.gov URL 403 | **Confirm with the FCC or Lead Administrator before budgeting a cent** |
| NRTL and IAPMO listing costs | no NRTL publishes prices | Get three parallel quotes — Intertek ETL, QPS and UL differ by up to 2× on appliance work |
| Honeywell MPR/ABP, MAX35103, MCP39F521, IIS3DWB detail | vendor pages 404'd or timed out | Fetch the datasheet PDFs directly |
| Browan, NetVox, SenseCAP M2, Fanstel, Insight SiP, u-blox NORA-B1, EFR32FG23 | not reachable | — |
