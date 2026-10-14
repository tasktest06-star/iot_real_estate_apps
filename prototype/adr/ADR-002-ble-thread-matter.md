# ADR-002 — BLE + Thread/Matter instead of LoRaWAN

| | |
|---|---|
| **Status** | Proposed. Design only, no code on this branch. |
| **Date** | 2026-10-14 |
| **Branch** | `design/ble-thread-matter` |
| **Unlocks** | **DTC retail** — route (c) in [`03-business-models.md`](../../03-business-models.md) |
| **Decides** | Fork C in [`08-roadmap.md`](../../08-roadmap.md) §6, for the consumer channel |

## Context

`/workshop/idea_.txt` asks specifically about *"openthread + connectedhomeip (both apache 2
license) and is IPv6 mesh technology with google apple ecosystem"*. One correction first, verified
in `../../data/repos.csv`:

- **`project-chip/connectedhomeip` is Apache-2.0.** ✅
- **`openthread/openthread` is BSD-3-Clause, not Apache-2.0.** Permissive and perfectly safe for a
  closed product, but BSD-3 is merely *silent* on patents where Apache-2.0 §3 grants them
  explicitly. If the patent grant is why Apache-2.0 was preferred, note the difference.
- `espressif/esp-matter` and `espressif/esp-matter-tools` are Apache-2.0. ✅

The attraction is channel, not technology. Matter certification buys Apple Home, Google Home and
Alexa compatibility, which is what gets a box onto a retail shelf and into the 17% of US homeowners
who already own a water sensor. It buys an insurer nothing.

## Decision

**ESP32-C6-WROOM-1 (\$2.85–3.40 [V])** running **esp-matter** over **Thread**, with BLE for
commissioning. Fork `automatous-io/shelly-1-gen4-matter-thread` (**Apache-2.0**, ESP32-C6,
multi-fabric commissioning, Matter OTA, a working Thread border router) as the starting template
rather than beginning from `connectedhomeip`'s examples — which carry an explicit upstream notice
that they are *"not production-ready and are not intended to be shipped as-is in a commercial
product."*

Matter 1.2+ defines the device types this product needs: **Water Leak Detector**, Water Freeze
Detector, and the **Valve Configuration and Control** cluster. `connectedhomeip` ships
`examples/water-leak-detector-app` in-tree — **but Linux-only, with no embedded platform port**, so
porting it is real work, not a config change.

## The problem that makes this branch different

**Matter is a data model, not a transport, and its data model cannot carry this product's signal.**

The detector needs a 15-minute time series of `pipe_c` and `ambient_c` to learn a per-device
baseline and a noise floor ([`../detection/README.md`](../detection/README.md)). Standard Matter
clusters give you:

- `BooleanState.StateValue` — leak / no leak. A **verdict**, not a measurement.
- optionally a Temperature Measurement endpoint — one temperature, not a labelled pipe/ambient pair
  with install-verification flags.

So the 8-byte payload in contract §1 has no standard Matter equivalent. Three ways out, none free:

| Option | Consequence |
|---|---|
| **Run the detector on-device** and publish only `StateValue` | Contradicts [`08-roadmap.md`](../../08-roadmap.md) §4 ("no NN on the node before P2") and kills shadow mode — you cannot measure a nuisance rate you never see. Also forfeits the labelled corpus. |
| **Manufacturer-specific clusters** for the raw telemetry | Works, and is what the spec provides for, but ecosystem interop degrades: Apple/Google hubs ignore custom clusters, so you need your own app anyway, which was the thing Matter was supposed to avoid. |
| **Dual path** — Matter for local control and ecosystem badge, your own Wi-Fi/MQTT path for telemetry | Honest and probably correct. The device is on Wi-Fi already in this topology. Costs a second stack, and the device must hold two credentials. |

**Recommended: dual path.** Accept that Matter is the *shelf* and your own path is the *product*.

### And the strategic consequence

In the Matter-native topology, telemetry lands in **the homeowner's fabric first** — Apple, Google
or Samsung — and you see it only if they choose to forward it. That directly contradicts the
data-moat argument in [`05-open-source-stack.md`](../05-open-source-stack.md) §5.4.

That document already concludes the real moat is the carrier relationship, the installed base and
the claims-workflow integration rather than the dataset, so the contradiction is survivable. **But
it should be a decision, not a discovery.** This is the only one of the four branches where
somebody else owns the data path, and it is the single most important line in this ADR.

## Architecture delta

```
  main:     device ──LoRaWAN──► gateway ──► TTS ──webhook──► backend ──► you

  ADR-002:  device ──Thread──► border router ──► Apple/Google/SmartThings ──► ...them
              └────Wi-Fi/MQTT (the dual path) ─────────────────────────────► you
```

Note the border router is **mains-powered and on the customer's Wi-Fi**. So Thread solves none of
the problems that made LoRaWAN attractive — no infrastructure independence, no sub-GHz basement
penetration, no five-year battery. It is a channel decision wearing a technology costume.

## Migration plan

| Path | Change | Effort **[E]** |
|---|---|---|
| `contracts/INTERFACES.md` | **New §4c**: Matter attribute model plus the dual-path telemetry envelope. §1's 8 bytes survive only on the private path. | 3 days |
| `firmware/` | **Largest change of the four branches.** New ESP-IDF + esp-matter application; Zephyr app retained for LoRaWAN builds. The pure modules (`leak_detect`, `uplink`, `valve`, `install_probe`) port **unchanged** — they have no Zephyr includes — but all the glue is new. | 6–8 weeks |
| `firmware/boards/` | ESP32-C6-DevKitC-1 target | 2 days |
| Matter factory data | DAC provisioning via `esp-matter-tools`. **Mandatory for CSA certification** and routinely underestimated. | 1–2 weeks |
| `codec/` | **Unchanged** for the private path; irrelevant on the Matter path | — |
| `simulator/` | Emit §4c; physics unchanged | 1 week |
| `detection/` | **Unchanged.** 130 tests and 0.3383 carry over — *provided* the dual path exists. If the detector moves on-device, this becomes a rewrite. | — |
| `backend/` | Add an MQTT/HTTP ingest for the private path; optionally a Matter fabric controller for commissioning telemetry | 2 weeks |
| `dashboard/` | Unchanged | — |

**Total: 9–13 person-weeks [E]**, plus certification lead time.

## What breaks

1. **Battery life falls from 5–10 years to 1–3.** A Thread sleepy end device cannot match a LoRaWAN
   Class A node, and 2.4 GHz has worse penetration to a basement water main than sub-GHz. On a
   product whose sensor sits next to the foundation, that is the wrong direction.
2. **Shadow mode is at risk.** It survives only on the dual path. Standing rule 3 of the roadmap —
   no customer-visible alert before the nuisance rate is measured — is not negotiable, so the dual
   path is load-bearing rather than optional.
3. **New certification: CSA Matter, \$10,500 [V]** (Adopter \$7,500/yr + \$3,000/product), plus a
   **mandatory** authorised third-party test house — Bureau Veritas, Trident IoT, Novus Labs, UL,
   Teledyne LeCroy or Granite River Labs — whose fee is **unverified**. "Works with Apple Home" then
   comes free via Matter, or via MFi enrollment if you go HomeKit-native.
4. **You inherit ecosystem release cycles.** An Apple or Google hub update can change commissioning
   behaviour in the field, and multi-fabric edge cases are where Matter products actually fail.

## Cost

| Line | Delta vs `main` |
|---|---|
| Radio module | ESP32-C6-WROOM-1 **\$2.85–3.40 [V]** vs RAK3172 \$5.99 → **cheaper** |
| Gateway | **−\$99 to −\$382** — but the customer must own a border router |
| LoRa Alliance | **−\$4,200 [V]** |
| CSA Matter | **+\$10,500 [V]** + test house (unverified) |
| Recurring | \$0 |
| Engineering | **+9–13 person-weeks [E]** |

## When to pick this

At the **P0 buyer gate**, if the signed pilot is **retail or a consumer brand**, or if a
security-monitoring incumbent wants a Matter device for its own ecosystem (the Verisure-shaped
licensing deal in [`09-build-vs-whitelabel.md`](../../09-build-vs-whitelabel.md) §9.4).

Do **not** pick it for the insurer channel: an insurer does not care about Alexa, and it would hand
the telemetry to a third party. Do not pick it for multifamily: no operator wants to depend on
residents' hubs.

## Rejected within this branch

- **Matter over Wi-Fi instead of Thread.** Simpler, no border router, but worse battery still. If
  the product is mains-powered anyway, see [ADR-003](ADR-003-wifi-esp-idf.md).
- **HomeKit-native via MFi.** Narrower (Apple only) for comparable effort now that Matter exists.
- **Zigbee.** Larger installed base of hubs, but it is the dying ecosystem and `zigbee2mqtt` — the
  best device-quirk knowledge base — is **GPL-3.0**, so reference-only for closed firmware.
- **Running `connectedhomeip` examples directly.** Upstream explicitly says not to ship them.
