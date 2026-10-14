# ADR-003 — Wi-Fi on ESP-IDF instead of LoRaWAN on Zephyr

| | |
|---|---|
| **Status** | Proposed. Design only, no code on this branch. |
| **Date** | 2026-10-14 |
| **Branch** | `design/wifi-esp-idf` |
| **Unlocks** | **Mains-powered products** — A12, A13, A14 in [`02-application-portfolio.md`](../../02-application-portfolio.md) |
| **Also tests** | Whether `main`'s portability claim is true |

## Context

Two independent reasons to want this branch.

**The product reason.** `main` is built around a coin-cell-class power budget, and that budget is
what forces sub-GHz, a 15-minute heartbeat and a 6-hour dwell. Three applications in the portfolio
are **mains-powered by definition** — the NEMA 5-15 metered plug (A12), the inline motorised valve
(A13), and the plug-in arc detector (A14). For those, the low-power argument evaporates and with it
the entire case for LoRaWAN: Wi-Fi costs nothing in power you have to spare, needs no gateway you
have to ship, and is what the proven electrical comparable (Ting) actually uses.

**The architecture reason, which is the more interesting one.** `main` justifies choosing upstream
Zephyr over Nordic's nRF Connect SDK on portability: *"your firmware survives a BOM change"*, and
the firmware was deliberately written so that `leak_detect.c`, `uplink.c`, `valve.c` and
`install_probe.c` contain **zero Zephyr includes**. That is a testable claim, and this branch is the
test. If those four modules port to a different RTOS unchanged, the claim holds. If they do not,
`main` is quietly coupled and the portability argument is marketing.

## Decision

**ESP32-C6-WROOM-1 (\$2.85–3.40 [V])** on **ESP-IDF** (Apache-2.0), Wi-Fi as the transport, BLE
retained for provisioning and field recovery.

ESP-IDF rather than Zephyr-on-ESP32, which is possible but pointless: on Espressif silicon,
ESP-IDF has the better drivers, the better Wi-Fi stack and — the thing that actually matters for a
product you will support for a decade — **published per-branch end-of-life dates**
(*"ESP-IDF v5.2 release branch reached end-of-life in August 2026"*). That lifecycle discipline is a
genuine asset, and it is the opposite of what happened to Mbed OS, which reached EOL in July 2026
with `os.mbed.com` now redirecting to GitHub. It is also **the easiest of these ecosystems to hire
for, by a wide margin**.

## Architecture delta

```
  main:      device ──LoRaWAN──► gateway ──► TTS ──webhook──► backend

  ADR-003:   device ──Wi-Fi/MQTT+TLS──────────────────────────► backend
                                       no gateway, no LNS
```

Same shape as [ADR-001](ADR-001-lte-m-nbiot.md)'s ingest change — direct device-to-backend — but
without the cellular module cost, the data plan or the carrier certification. The trade is that you
now depend on the customer's router.

## Migration plan

| Path | Change | Effort **[E]** |
|---|---|---|
| `firmware/src/leak_detect.{c,h}` | **Unchanged.** No Zephyr includes. | — |
| `firmware/src/uplink.{c,h}` | **Unchanged.** | — |
| `firmware/src/valve.{c,h}` | **Unchanged** — hardware reached only through the injected `valve_hw_t`. | — |
| `firmware/src/install_probe.{c,h}` | **Unchanged.** | — |
| `firmware/src/main.c` | **Full rewrite.** `k_work` → esp_timer/FreeRTOS tasks, `lorawan_send()` → `esp-mqtt`, `pm_device_action_run()` → esp_pm, `settings` → NVS. All glue, no logic. | 2–3 weeks |
| `firmware/boards/*.overlay` | **Deleted, not ported.** ESP-IDF has no devicetree; board wiring becomes C initialisation plus Kconfig. This is the one place the port is genuinely lossy — see below. | 1 week |
| `firmware/prj.conf`, `Kconfig` | → `sdkconfig.defaults`. The 22 `CONFIG_APP_*` tunables map across cleanly; `CONFIG_LORAWAN_*` and `CONFIG_TMP11X` do not exist. | 3 days |
| `firmware/tests/` | **Unchanged, and finally runnable** — these are plain-C host tests and ESP-IDF ships a host toolchain, so the four suites that have never executed can actually run. | — |
| TMP11x driver | **Rewrite.** Zephyr's `ti,tmp11x` driver has no ESP-IDF equivalent; you write a small I²C driver or take one from `esp-idf-lib`. | 3–4 days |
| `codec/` | **Unchanged** | — |
| `detection/` | **Unchanged.** 130 tests, 0.3383 nuisance rate carry over. | — |
| `backend/app/ingest.py` | Add an MQTT-over-TLS path; same `(dev_eui, seq)` idempotency question as ADR-001 | 1 week |
| `simulator/` | New envelope; physics unchanged | 4 days |

**Total: 5–7 person-weeks [E].**

**The portability verdict:** four of eight firmware source modules port **byte-for-byte unchanged**,
the tests port unchanged *and become executable*, and all of `detection/` and `codec/` are
untouched. `main`'s portability claim **holds**. What does not port is the devicetree, which is a
real asymmetry worth naming: Zephyr's overlays are declarative and reviewable, ESP-IDF's board
wiring is imperative C, so the migration loses a layer of expressiveness rather than gaining one.

## What breaks

1. **Battery products are out.** Not "worse" — out. Wi-Fi association and DHCP on every wake makes
   a five-year primary-cell product impossible. This branch is for mains-powered SKUs only, and
   trying to stretch it to the clamp-on sensor is the mistake it invites.
2. **You inherit the customer's router, and Ting's documented failure modes with it**: it needs the
   homeowner's 2.4 GHz credentials, fails behind a surge protector or UPS, and needs one device per
   electrical panel. Those are reviewer-reported facts about the market leader, not speculation.
3. **Provisioning becomes a real product surface.** SoftAP or BLE provisioning, a QR code, an app
   flow, and a support path for when the homeowner changes their Wi-Fi password. `esp-rainmaker`
   (Apache-2.0) covers this if you want it off the shelf. On LoRaWAN, provisioning was a factory
   step; here it is a customer step, and customer steps have a 10–30% failure tail (risk 4).
4. **Mains means NRTL.** Any product on this branch crosses the left-hand gate in
   [`07-compliance-and-certification.md`](../../07-compliance-and-certification.md): UL 498 + UL
   60730-1 + UL 94, **\$15,000–40,000 [E]** plus annual factory inspections. That is a property of
   being mains-powered, not of this branch, but this branch is the one that gets you there.

## Cost

| Line | Delta vs `main` |
|---|---|
| Radio module | **\$2.85–3.40 [V]** vs \$5.99 → **cheaper** |
| Gateway | **−\$99 to −\$382** |
| LoRa Alliance membership + cert | **−\$4,200 [V]** |
| NRTL (because mains) | **+\$15,000–40,000 [E]** + annual inspections |
| Recurring | \$0 |
| Engineering | **+5–7 person-weeks [E]** |

## When to pick this

When the product being built is **mains-powered anyway** — A12's metered plug (the verified North
American gap: Milesight's WS51x is EU/FR plug and CE-only, WS523 has a US variant but **neither is
UL/ETL listed**), A13's valve controller, or A14's arc detector. In those cases this is not a
compromise, it is the correct architecture, and `main`'s LoRaWAN stack is the wrong tool.

Do **not** pick it for the clamp-on sensor. That product's whole advantage is that it is battery
powered, mailable and needs nothing from the customer.

## Rejected within this branch

- **Zephyr on ESP32-C6.** Supported, and it would preserve the devicetree. But it gives up
  ESP-IDF's Wi-Fi maturity and published EOL dates on the one silicon family where Espressif's own
  SDK is clearly better, for a portability benefit this ADR has just shown you do not need.
- **ESP32-C3 instead of C6.** Cheaper, but no 802.15.4, so it closes the door on
  [ADR-002](ADR-002-ble-thread-matter.md) sharing the same silicon. The C6 keeps both options open
  for about a dollar.
- **Raspberry Pi RP2350 + RM2.** A \$4 radio module with a written production commitment to
  **January 2036 [V]** is unusually good supply-chain risk. But Wi-Fi 4 only, no 802.15.4, no
  sub-GHz, and far thinner Zephyr support. Viable for a bridge or a mains device; not for this.
- **ESPHome.** Tempting for speed, and disqualified: its device-side C++ is **GPLv3**, and only the
  Python generator is MIT. The GPL half is the half that ships.
