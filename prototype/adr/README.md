# Architecture decision records — alternative stacks

`main` implements **one** point in a large design space: Zephyr + LoRaWAN US915 + The Things Stack +
FastAPI/SQLite. That was the right first choice for the multifamily buyer route, and it is the
wrong choice for three of the four routes in [`03-business-models.md`](../../03-business-models.md).

Each alternative lives on its own **design branch**: an ADR plus a concrete migration plan against
`main`, with costs taken from [`07-compliance-and-certification.md`](../../07-compliance-and-certification.md)
and part prices from [`06-hardware-and-devkits.md`](../../06-hardware-and-devkits.md). **No code** —
the point is to choose before building, because [`08-roadmap.md`](../../08-roadmap.md) §6 Fork C says
the radio is downstream of the buyer and the buyer is not yet known.

| Branch | ADR | Swaps | Buyer route it unlocks |
|---|---|---|---|
| `design/lte-m-nbiot` | [ADR-001](ADR-001-lte-m-nbiot.md) | LoRaWAN → **LTE-M / NB-IoT** (nRF9151) | **Insurer-mailed single-family** — the only radio that survives being posted to a policyholder |
| `design/ble-thread-matter` | [ADR-002](ADR-002-ble-thread-matter.md) | LoRaWAN → **BLE + Thread/Matter** (ESP32-C6) | **DTC retail** — Apple/Google/Amazon shelf appeal |
| `design/wifi-esp-idf` | [ADR-003](ADR-003-wifi-esp-idf.md) | Zephyr → **ESP-IDF**, LoRaWAN → Wi-Fi | **Mains-powered products** (A12/A13/A14) |
| `design/chirpstack-magistrala` | [ADR-004](ADR-004-chirpstack-magistrala.md) | TTS → **ChirpStack**, FastAPI → **Magistrala**. Same radio | None — it tests whether the architecture is portable or quietly coupled |

## The comparison that matters

Costs are deltas against `main`, not absolutes. **[V]** vendor-published, **[E]** estimate.

| | `main` (LoRaWAN) | ADR-001 LTE-M | ADR-002 BLE/Matter | ADR-003 Wi-Fi/ESP-IDF | ADR-004 ChirpStack |
|---|---|---|---|---|---|
| Radio module **[V]** | RAK3172 $5.99 | nRF9151 SiP | ESP32-C6-WROOM-1 $2.85–3.40 | ESP32-C6-WROOM-1 $2.85–3.40 | unchanged |
| Infrastructure you ship | gateway $99–382 | **none** | border router (customer's) | **none** | gateway, unchanged |
| Works on arrival, no setup | no | **yes** | no | no | no |
| Recurring per device | ~$0 | **$1–3/yr** | $0 | $0 | ~$0 |
| New certification **[E]** | — | **PTCRB/carrier** | **CSA $10,500 [V]** | — | — |
| `detection/` changes | — | **none** | **none** | **none** | **none** |
| Who owns the data path | **you** | **you** | **Apple/Google/Amazon** | you | you |
| Licence exposure | BSD-3 MAC | **Nordic-5-Clause blob** | Apache-2.0 | Apache-2.0 | MIT + Apache-2.0 |

Two results fall out of that table and are worth stating before reading any individual ADR.

**`detection/` is unchanged in all four.** That is not luck, it is the one architectural property
worth having: the detector is stdlib-only pure functions over a `Sample` struct, so it does not know
what radio delivered the bytes. The 130 detection tests and the measured 0.3383 nuisance rate carry
across every branch. If a migration plan ever *does* require touching `detection/`, that is the
signal something has been coupled that should not have been.

**ADR-002 is strategically different from the other three.** BLE/Matter is the only option where
the telemetry lands in someone else's cloud first. That directly contradicts the data-moat argument
in [`05-open-source-stack.md`](../05-open-source-stack.md) §5.4 — and since that document already
concludes the real moat is the carrier relationship rather than the dataset, the contradiction is
survivable. But it should be a decision, not a discovery.

## Working with these branches

```bash
git branch -a                                  # see them
git checkout design/lte-m-nbiot                # read one
git diff main..design/lte-m-nbiot --stat       # what it adds (docs only)
```

They are deliberately **not** merged into `main` and should not be. Merging all four would leave the
repo claiming four incompatible radio architectures at once. The intended lifecycle: the P0 buyer
gate in [`08-roadmap.md`](../../08-roadmap.md) picks one, that branch's migration plan gets executed
on a feature branch, and the losing ADRs stay as records of what was considered and why it lost.
