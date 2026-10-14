# 5. The Open-Source Stack — Verified Apache-2.0, and the Traps

89 repositories with licences read from their actual `LICENSE` file at `HEAD` on **2026-10-14**.
Regenerate with `make verify-licenses`.

---

## 5.0 Read this before anything else: how licence checking goes wrong

The naive way to verify a licence is to grep the `LICENSE` body for `"Apache License"` and
`"Version 2.0"`. **This is wrong in two ways that both produced false positives while researching
this pack:**

1. **A Business Source License 1.1 file names Apache-2.0 as its *Change License*, in the header.**
   Body-matching therefore reports BUSL-licensed **ThingsBoard and EMQX as Apache-2.0** — the exact
   opposite of the truth, on the two packages most likely to end up in an IoT backend.
2. **GPLv3 §13 mentions the GNU Affero General Public License.** Body-matching therefore reports
   every GPLv3 project as AGPL, which over-restricts and sends you looking for replacements you do
   not need.

`scripts/verify-licenses.sh` classifies by **licence title** — the first twelve non-empty lines —
and checks the source-available licences *before* the permissive ones. It also records a
`commercial_ok` verdict per repo and a `role` explaining why it is there, so a reader can audit the
reasoning rather than trusting a table. **Four repos are marked `review`** rather than given a clean
verdict, because repo-level and file-level licensing disagree.

---

## 5.1 The reference stack

One recommendation per layer, with the runner-up. ✅ = the pick itself is Apache-2.0.

| Layer | Recommended | Apache-2.0? | Runner-up | Why |
|---|---|---|---|---|
| **Device RTOS** | [Zephyr](https://github.com/zephyrproject-rtos/zephyr) + `west` | ✅ | ESP-IDF ✅ | The only RTOS giving BLE + LoRaWAN + Matter from one Apache-2.0 tree, across STM32WL, nRF52840 and ESP32-C6 — so your firmware survives a BOM change. Use ESP-IDF for the *mains-powered* products where its dual-core and Wi-Fi are free. |
| **BLE stack** | Zephyr Bluetooth (host + open controller) | ✅ | [NimBLE](https://github.com/apache/mynewt-nimble) ✅ | In-tree, zero integration cost, and supports **coded PHY S2/S8** — long-range BLE for a basement. NimBLE is the only *standalone* Apache-2.0 host **and controller**, already inside ESP-IDF: your portability insurance. **Avoid Nordic's SoftDevice Controller blob.** |
| **LoRaWAN MAC** | Zephyr `subsys/lorawan` over [loramac-node](https://github.com/Lora-net/LoRaMac-node) | Glue ✅, MAC **BSD-3** | [SWL2001](https://github.com/Lora-net/SWL2001) **BSD-3-Clear** | **No Apache-2.0 LoRaWAN MAC exists — accept BSD and move on.** `CONFIG_LORAWAN_REGION_US915` is present. loramac-node is in maintenance mode, so plan a migration to LoRa Basics Modem — but note it is **Clear BSD, which explicitly grants no patent rights.** |
| **Secure boot / OTA** | [MCUboot](https://github.com/mcu-tools/mcuboot) | ✅ | Zephyr SMP/mcumgr ✅ (complementary) | Signed images, A/B swap, **rollback** — the difference between a bad OTA being an incident and being a mass-flooding liability event. Use both: MCUboot for the trust format, SMP/mcumgr for the **BLE** transport so an installer can recover a brick on site. |
| **Fleet management** | [Golioth SDK](https://github.com/golioth/golioth-firmware-sdk) | ✅ | [hawkBit](https://github.com/eclipse-hawkbit/hawkbit) (EPL-2.0, server-only) | OTA + RPC + **PKI certificate rotation**, CI-tested on Zephyr/ESP-IDF/NCS. The client is Apache-2.0; the cloud is paid SaaS — so pair with hawkBit as a self-host escape hatch. |
| **LoRaWAN network server** | [The Things Stack](https://github.com/TheThingsNetwork/lorawan-stack) | ✅ | [ChirpStack](https://github.com/chirpstack/chirpstack) (**MIT**) | The only Apache-2.0 LNS, and the **Join Server is in-tree — you hold your own root keys**, which is non-negotiable for a device that can flood a house. Take `chirpstack-fuota-server` and `chirpstack-packet-multiplexer` (both MIT) regardless of which LNS you run. |
| **Gateway ↔ LNS** | [LoRa Basics Station](https://github.com/lorabasics/basicstation) | BSD-3 | `sx1302_hal` UDP forwarder (BSD-3) | Needs **no inbound connections and no local time sync** — it works behind a homeowner's NAT and survives a router reboot — plus CUPS credential rotation. The UDP forwarder is fine on a bench and wrong in a fleet. |
| **MQTT broker** | [VerneMQ](https://github.com/vernemq/vernemq) | ✅ | [NanoMQ](https://github.com/nanomq/nanomq) (MIT) at the edge | The last credible **Apache-2.0 clustering** broker after EMQX went BSL. Run NanoMQ on the in-home hub so the house keeps working during a WAN outage. |
| **Time-series store** | [Apache IoTDB](https://github.com/apache/iotdb) | ✅ | [VictoriaMetrics](https://github.com/VictoriaMetrics/VictoriaMetrics) ✅ | IoTDB's device/sensor hierarchy maps natively onto property → device → channel. VictoriaMetrics is the safer ops bet but **downsampling is Enterprise-only**, which you will hit with 1 Hz flow data. **Avoid TimescaleDB:** compression and continuous aggregates live in `tsl/` under a proprietary licence. |
| **App platform** | [Magistrala](https://github.com/absmach/magistrala) | ✅ | [Node-RED](https://github.com/node-red/node-red) ✅ + your own service | **ThingsBoard is disqualified (BUSL-1.1, 100-device cap).** Magistrala has **mTLS, ABAC+RBAC, audit logs, multi-tenancy and OpenTelemetry** — precisely the list a carrier's security review asks for. Node-RED for per-carrier integrations, where every carrier wants a bespoke payload shape and you cannot afford a sprint each. |
| **On-device DSP/ML** | [CMSIS-DSP](https://github.com/ARM-software/CMSIS-DSP) + [CMSIS-NN](https://github.com/ARM-software/CMSIS-NN) + [tflite-micro](https://github.com/tensorflow/tflite-micro) | ✅ all three | [emlearn](https://github.com/emlearn/emlearn) (MIT) for the coin cell | **CMSIS-DSP is the real workhorse** — FFT/MFCC/DCT/filters/SVM serve acoustic leak signatures *and* arc spectral features *and* flow filtering. **LiteRT is NOT the MCU successor to TF Lite — it has no microcontroller support.** |
| **Dashboards** | [Apache Superset](https://github.com/apache/superset) | ✅ | [Perses](https://github.com/perses/perses) ✅ | **Grafana is AGPL-3.0-only** — embedding it in a carrier-facing portal triggers network copyleft. Superset's extensible RBAC is what lets you give a carrier a scoped portfolio view. Perses for internal fleet health. |
| **Water physics / labelled data** | [EPyT-Flow](https://github.com/WaterFutures/EPyT-Flow) (MIT) | — | [WNTR](https://github.com/USEPA/WNTR) (MIT) | Simulates leakages **and sensor faults** with uncertainty models — a labelled synthetic-data factory *including the false-alarm cases*, which is the part nobody else has. **Avoid the KIOS originals (LeakDB, BattLeDIM, EPyT): all EUPL-1.2 copyleft.** |
| **Electrical ML** | [nilmtk](https://github.com/nilmtk/nilmtk) + [nilmtk-contrib](https://github.com/nilmtk/nilmtk-contrib) + [nilm_metadata](https://github.com/nilmtk/nilm_metadata) | ✅ all three | [goruck/nilm](https://github.com/goruck/nilm) (MIT) | Rare: a whole academic ML ecosystem under Apache-2.0, still maintained, with 30+ modern models and a standard evaluation harness. **Steal `nilm_metadata`'s appliance schema** for your telemetry ontology. |
| **Cloud anomaly detection** | [darts](https://github.com/unit8co/darts) | ✅ | scikit-learn (BSD-3) | Forecasting plus anomaly scorers for slow leaks and circuit degradation — the long-horizon detection that cannot run on a coin cell. |
| **Insurance schema** | [openIDS](https://github.com/openidl-org/openidl-data-specification) | CommunitySpec-1.0 | Publish your own | Homeowners v1.0 is live and LF-governed with royalty-free patent commitments, and **implementations are exempt from the attribution terms**, so closed-source implementation is permitted. **`openidl-main` is dormant (1 star, last commit Dec 2023) — adopt the spec, not the platform.** |

### A realistic self-hosted backend, ~$95–115/month

Sized for **~10,000 devices at ~5 uplinks/device/day** — LoRaWAN duty cycles keep volumes tiny.

| Layer | Pick | Host | $/mo |
|---|---|---|---|
| LoRaWAN NS + Join Server | The Things Stack (Docker) | VPS A, 4 vCPU/8 GB | 18 |
| Postgres + Redis | — | VPS A | 0 |
| MQTT | VerneMQ | VPS B, 4 vCPU/8 GB | 18 |
| OTA | MCUboot on-device + hawkBit | VPS B | 0 |
| Time-series | Apache IoTDB | VPS C, 4 vCPU/16 GB | 35 |
| API + rules | Magistrala + Node-RED | VPS C | 0 |
| Dashboards | Superset + Perses | VPS D, 2 vCPU/4 GB | 9 |
| Object storage (raw waveforms, coredumps, OTA artefacts) | Backblaze B2 | — | 8–25 |
| Backups + monitoring | restic + Prometheus | — | 5 |

Deliberately omitted: Kubernetes (Docker Compose until ~50k devices), Kafka (MQTT + IoTDB handles
50k msg/day), ThingsBoard, Grafana. **The highest-leverage spend above $200/mo is not compute — it
is raw-waveform object storage**, because that corpus is the input to everything in §5.4. Spend the
slack on managed Postgres for the Join Server: losing your LoRaWAN root key material is an
extinction event.

---

## 5.2 Licence traps — the blunt version

**1. ThingsBoard is no longer open source, and the cap is 100 devices.** BUSL-1.1. The Additional
Use Grant permits free production use only if *"your use has a Commercial Purpose [and] Devices…
do not exceed 100, and… one server."* Last Apache-2.0 release was **v3.9.1**. Pinning it means no
security patches on a device attached to someone's plumbing. **Design it out now, not at device
#101.**

**2. EMQX went BSL 1.1 at v5.9.0 and clustering needs a paid licence file.** Your high-availability
story dies with a single node. Use **VerneMQ**.

**3. ESPHome's device-side code is GPLv3, not MIT — and most people have this backwards.** The
licence states: `.c, .cpp, .h, .hpp, .tcc, .ino` → **GPLv3**; the Python codebase and all other
parts → MIT. **The GPL half is the half that ships on your hardware.** Same category:
**Tasmota (GPL-3.0-only)**, **IotaWatt (GPL-3.0)**, **LocalTuya (GPL-3.0)**,
**Zigbee2MQTT (GPL-3.0)**, **Meshtastic (GPL-3.0)**. Prototype freely; ship nothing from them.
Permissive substitutes exist for every one: **`CircuitSetup/*` (MIT hardware *and* firmware)** for
energy metering, **`make-all/tuya-local` (MIT)** for Tuya.

**4. AGPL means your SaaS owes source to its users.** **Grafana (AGPL-3.0-only** since 2021**)**,
**OpenRemote (AGPL-3.0)**, **emoncms (AGPL-3.0)**, **EmonLib (AGPL-3.0)**. §13 triggers on *network
interaction*, not distribution — "we only run it on our servers" is the exact case it was written to
catch. Use Superset/Perses and Magistrala.

**5. EUPL-1.2 is copyleft, and it sits in the middle of your water-ML bucket.** **LeakDB,
BattLeDIM and EPyT are all EUPL-1.2.** Most people read "EU Public Licence" as permissive; it is
not, and it is explicitly compatible with GPL/AGPL. Use the MIT-licensed `WaterFutures/*` family
instead — same research lineage, same capability, non-viral.

**6. Clear BSD removes the patent grant deliberately.** Semtech's **LoRa Basics Modem (SWL2001)**
and all of Edge Impulse are **BSD-3-Clause-Clear**: *"NO EXPRESS OR IMPLIED LICENSES TO ANY PARTY'S
PATENT RIGHTS ARE GRANTED BY THIS LICENSE."* Since Apache-2.0 §3's patent grant is the usual reason
to prefer Apache-2.0, note the ranking: **Apache-2.0 > MIT/BSD-3 (silent on patents) > Clear BSD
(explicitly no grant).**

**7. "Chip-locked permissive" is not permissive.** **`nrfconnect/sdk-nrf` and `sdk-nrfxlib` are
`LicenseRef-Nordic-5-Clause`**: the software *"must only be used with a Nordic Semiconductor ASA
integrated circuit"* and binaries *"must not be reverse engineered, decompiled, modified and/or
disassembled."* You **can** ship a closed commercial product — that is the point of its binary
exception — but **your BOM is locked to Nordic for the life of that firmware**, you cannot patch the
radio or modem layer, and there is no patent grant. Same shape: **Microchip's cryptoauthlib**
(*"exclusively with Microchip products"*) and **ST's STM32CubeWL** (SLA0044, proprietary). For a
startup whose MCU supply and cost structure will change twice before Series A, that portability is
worth real money — which is the argument for **upstream Zephyr over nRF Connect SDK.**

**8. Dual licences must be elected in writing.** Mosquitto is **EPL-2.0 OR EDL-1.0** — elect EDL and
it is effectively BSD-3. Leshan is EPL-2.0 OR BSD-3. mbedTLS is Apache-2.0 OR GPL-2.0-or-later.
Record the election; it is a five-minute task that answers a diligence question years later.

**9. Memfault is not open source.** Excellent crash triage under a vendor-specific licence shipping
inside your binary. Fine as a vendor decision; fatal if it is sitting on a slide labelled "our
open-source stack" during diligence.

**10. Unlicensed is worse than copyleft.** `RAKWireless/rak_common_for_gateway`,
`dragino/openwrt_lede-18.06` and `chirpstack-gateway-os` state **no licence at all**, which legally
means all rights reserved — you have *less* right to use these than GPL code. Get written terms, and
assume any OpenWrt-derived gateway image carries **GPL-2.0** obligations: keep your proprietary
application in a separate userspace package, never linked into a GPL binary.

**11. Dataset licences will bite you, and code hygiene will not save you.** Several NILM datasets
(**PLAID, WHITED, BLUED, REDD, UK-DALE, REFIT, AMPds, BLOND**) are **CC-BY-NC or under academic
data-use agreements.** Training a *shipped commercial* model on a non-commercial dataset is a breach
no amount of Apache-2.0 code purity fixes. See §5.5.

**12. Matter examples carry an explicit no-ship notice.** `connectedhomeip`'s README states the
examples are *"not production-ready and are not intended to be shipped as-is in a commercial
product."* Not a licence restriction — an engineering and CSA-certification warning. Use
`esp-matter` + `esp-matter-tools` for the production and DAC-provisioning path.

---

## 5.3 Per-product architecture, with annotated snippets

### A1 — clamp-on thermal sensor (battery, LoRaWAN or LTE-M, BLE for commissioning)

```
  2× TMP117 (pipe + ambient) ──I²C──┐
  NTC (tamper/sanity) ─────────ADC──┤
                                    ├─ Zephyr app  ──┬── subsys/lorawan → SX1262 → gateway → TTS
  TDK T5838 mic (later SKU) ──PDM───┘   emlearn /    └── Zephyr BLE (commissioning + mcumgr)
                                        CMSIS-DSP
  MCUboot (A/B + rollback) under all of it
```

The Zephyr config. **The `NVM_SETTINGS` line is not optional** — without it the upstream sample
joins successfully exactly once, because `dev_nonce` is hardcoded, and you will lose a day to it:

```ini
# prj.conf — LoRaWAN Class A end device, US915
CONFIG_LORA=y
CONFIG_LORAWAN=y
CONFIG_LORAWAN_REGION_US915=y          # verified present in subsys/lorawan/Kconfig
CONFIG_LORAWAN_NVM_SETTINGS=y          # persists dev_nonce across reboots.
CONFIG_SETTINGS=y                      # Omit it and the device rejoins ONCE, then fails.
CONFIG_NVS=y
CONFIG_FLASH_MAP=y

CONFIG_BT=y                            # BLE for commissioning + field recovery
CONFIG_BT_PERIPHERAL=y
CONFIG_MCUMGR=y                        # re-flash a bricked node over BLE, no WAN needed
CONFIG_MCUMGR_TRANSPORT_BT=y
CONFIG_BOOTLOADER_MCUBOOT=y            # signed A/B images with rollback

CONFIG_PM_DEVICE=y                     # coin-cell/Li-SOCl2 duty cycling
CONFIG_SENSOR=y
CONFIG_TMP117=y
```

```dts
// boards/<board>.overlay — SX1262 on SPI, and the sensors.
// Pin names are placeholders: take the real ones from the module datasheet,
// and keep the antenna matching the module's FCC grant conditions or you
// forfeit modular approval and owe a full Part 15C campaign.
&spi1 {
    status = "okay";
    lora: lora@0 {
        compatible = "semtech,sx1262";
        reg = <0>;
        spi-max-frequency = <4000000>;
        reset-gpios  = <&gpio0 10 GPIO_ACTIVE_LOW>;
        busy-gpios   = <&gpio0 11 GPIO_ACTIVE_HIGH>;
        dio1-gpios   = <&gpio0 12 GPIO_ACTIVE_HIGH>;
        tx-enable-gpios = <&gpio0 13 GPIO_ACTIVE_HIGH>;  /* RF switch */
        rx-enable-gpios = <&gpio0 14 GPIO_ACTIVE_HIGH>;
        dio2-tx-enable;
        tcxo-power-startup-delay-ms = <5>;
    };
};

&i2c0 {
    status = "okay";
    pipe_temp: tmp117@48   { compatible = "ti,tmp117"; reg = <0x48>; };
    ambient_temp: tmp117@49 { compatible = "ti,tmp117"; reg = <0x49>; };
};
```

**The payload codec.** Submit this to
[`TheThingsNetwork/lorawan-devices`](https://github.com/TheThingsNetwork/lorawan-devices)
(Apache-2.0) — it is how your sensor auto-provisions on TTN, TTS and partners' stacks, and it is a
non-obvious go-to-market asset.

```javascript
// 8-byte uplink. Keep it under 11 bytes so DR0 works at the edge of coverage,
// and send deltas rather than decisions: the classifier lives in the cloud in
// P1 and only moves on-device once the nuisance rate is known (see 08 §4).
function decodeUplink(input) {
  const b = input.bytes;
  const i16 = (h, l) => (b[h] << 8 | b[l]) << 16 >> 16;   // sign-extend
  return {
    data: {
      pipe_c:    i16(0, 1) / 100,          // centi-°C
      ambient_c: i16(2, 3) / 100,
      delta_c:   i16(0, 1) / 100 - i16(2, 3) / 100,  // the actual signal
      battery_mv: b[4] << 8 | b[5],
      flags: {
        install_verified: !!(b[6] & 0x01),  // clamp seated: refuse to count an
                                            // unverified install (08 §8 risk 9)
        tamper:           !!(b[6] & 0x02),
        cold_warning:     !!(b[6] & 0x04),  // freeze peril = A4, same sensor
      },
      uplink_reason: b[7],                  // 0 heartbeat, 1 threshold, 2 boot
    },
  };
}
```

### A6/A9 — the shutoff state machine

The licence-clean template is **`automatous-io/shelly-1-gen4-matter-thread`** (Apache-2.0, ESP32-C6,
esp-matter, working relay control and multi-fabric commissioning). The behaviour that matters is
not the radio, it is the **failure semantics**:

```c
/* Fail-AS-IS, never fail-closed. A valve that closes on battery death or a
 * firmware fault floods support with "no water" calls and, in a freeze event,
 * causes the exact damage you were hired to prevent. A latching solenoid is
 * inherently fail-as-is; a motorised ball valve is not, which is one more
 * reason A13 is deferred (see 02 §2.4).
 *
 * The arm/refuse logic is also a real differentiator: measure the valve at
 * install time and decline to promise what the hardware cannot deliver. */
typedef enum { V_UNKNOWN, V_OPEN, V_CLOSED, V_REFUSED, V_FAULT } valve_state_t;

static valve_state_t arm_or_refuse(const install_probe_t *p)
{
    if (p->breakaway_torque_mNm > TORQUE_REFUSE_THRESHOLD) return V_REFUSED;
    if (p->post_cycle_stem_leak)                           return V_REFUSED;
    if (p->cycle_time_ms > CYCLE_TIMEOUT_MS)               return V_FAULT;
    return V_OPEN;
}

/* Shutoff decision is LOCAL. LoRaWAN Class A opens a receive window only after
 * an uplink, so a cloud-issued close waits for the next heartbeat -- 15 minutes
 * of flooding at a 15-minute interval. The cloud confirms and reports; it does
 * not arbitrate the close. See 04 §4.2. */
```

---

## 5.4 Where open source stops and your IP starts

Open source gives you essentially everything in §5.1. It does not give you the two datasets that
matter.

**Acoustic leak signatures: the cupboard is bare.** A GitHub search for `acoustic leak detection`
returns ~45 repositories, the most-starred of which has **four stars**. There is no open acoustic
leak dataset of consequence. EPyT-Flow gives you hydraulic-regime labels; it cannot give you
acoustic signatures.

**AC series-arc data: worse.** GitHub's `afci` topic has **one repository with zero stars**, and it
is an AutoCAD floor plan. `"series arc fault"` returns three repos, max three stars. **There is no
open UL 1699 waveform dataset** — those sit behind UL's paywall and test-lab NDAs. And UL 1699 is as
much about *not* tripping on vacuum cleaners, dimmers and arc welders as about detecting arcs, so the
nuisance-rejection data is equally proprietary.

The one useful artefact is `MicrochipTech/ml-arc-fault-detection` — **Microchip proprietary, so read
it for the method and reimplement clean-room.** Its value is the validated budget: **absolute mean,
minimum, median frequency energy and percent-time-over-zero, on 160-sample windows at 16 kHz, in
4–6 kB flash and 1–2 kB RAM.** Those four features map almost directly onto CMSIS-DSP primitives. It
is **DC** arc data; 120/240 V AC is materially harder.

**So budget an instrumented capture rig as a capital line item**, not a side project: pipe materials
× diameters × pressures × leak sizes × background noise for water; carbonised-path, cut-strand and
loose-connection fixtures plus the UL 1699 masking loads for electrical. **Store every raw
waveform** — hence the object-storage line in §5.1.

**But do not overclaim the moat.** The discriminative signal for escape of water is mostly simple —
flow continuity, pressure decay, temperature differential, water present at a known location. The
hard part is nuisance rejection, and **nuisance rejection generalises poorly across housing stock
and climate zone**, so a 10,000-home corpus transfers less well to a new region than you would hope,
while incumbents already hold 100k+ device-years. The honest position: **the defensible moat is the
carrier relationship, the installed base and the claims-workflow integration; the dataset is a
necessary input, not the moat.** [08-roadmap.md](08-roadmap.md) §7 makes this falsifiable.

---

## 5.5 Dataset provenance register — start this before the first training run

"Which dataset trained the model in firmware v1.4.2?" is a question a carrier's legal team will ask,
and it is **unanswerable retroactively.** Keep this table in version control and add a row before
any training run.

| Dataset | Typical licence | Commercial use | Notes |
|---|---|---|---|
| REDD | Academic DUA | **Check — likely no** | Request terms in writing |
| UK-DALE | CC-BY-4.0 variants | Check | Attribution required |
| REFIT | CC-BY-4.0 | Likely yes with attribution | Used by `goruck/nilm` |
| **PLAID** | Academic / CC-BY-NC variants | **Likely NO** | Plug-level signatures — the most relevant and the most restricted |
| **WHITED** | Academic | **Likely NO** | Plug-level appliance signatures |
| BLUED, BLOND, AMPds | Academic / CC-BY-NC | **Likely NO** | |
| LeakDB, BattLeDIM | **EUPL-1.2** | Copyleft — avoid | Use `WaterFutures/*` MIT instead |
| EPyT-Flow synthetic | **MIT** | **Yes** | Generate your own, unrestricted |
| VSB Power Line Fault | Kaggle competition terms | Check | The one sizable open HF electrical-waveform set |
| **Your own captured corpus** | Yours | **Yes** | The only unambiguous one. Make data rights explicit in every pilot contract. |

> All "likely" entries are **unverified** — these datasets are not GitHub-hosted and live on
> institutional pages, Figshare and Zenodo under individually varying terms. Check each one
> individually before a single training run, and record the answer here.

Also note the awesome-lists you will mine are themselves licensed: `ch-shin/awesome-nilm` is CC0;
`zhgqcn/awesome-NILM-with-code` is CC-BY-4.0 and requires attribution.
