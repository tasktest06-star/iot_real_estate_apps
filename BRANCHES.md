# Branches

Five branches. One carries the work; four carry **decisions not yet taken**.

Accurate as of **2026-10-15**. Regenerate the SHAs with `git branch -v` — they will drift, the
structure will not.

---

## `main` — the implementation

| | |
|---|---|
| **SHA** | `6058a78` |
| **Contents** | 89 files, 4 commits |
| **Status** | Active. Everything merges here. |

The briefing pack (`01`–`10`), the data files, the Beamer deck, and the working prototype.

| Commit | What landed |
|---|---|
| `9a313e1` | The North America briefing pack — 10 documents, 4 CSVs, 28-page deck, 89 machine-verified licences |
| `2c89a71` | The end-to-end prototype — 312 passing tests, P1 gate PASS at 0.3383 nuisance alerts/device-year |
| `13628dc` | The ADR index for alternative stacks |
| `6058a78` | The product-family architecture and implementation plan |

Verify it yourself:

```bash
python3 -I scripts/check-data.py     # CSV <-> prose consistency
make verify-licenses                 # re-read all 89 LICENSE files
cd prototype && make test && make e2e
```

---

## The four design branches

Each adds **exactly one ADR and nothing else** — no code, deliberately. They exist because
[`08-roadmap.md`](08-roadmap.md) §6 Fork C says the radio is downstream of the buyer, and the buyer
is not yet known. Committing code to a radio before the P0 gate would be spending the one resource
that cannot be bought back: calendar.

All four are rebased onto current `main`, so `git diff main..<branch>` shows only the ADR.

### `design/lte-m-nbiot` — the insurer channel

| | |
|---|---|
| **SHA** | `22db9e9` · **Adds** | `prototype/adr/ADR-001-lte-m-nbiot.md`, 163 lines |
| **Swaps** | LoRaWAN US915 → **LTE-M / NB-IoT** on nRF9151 |
| **Unlocks** | Insurer-mailed single-family — route (a), the one with the proven commercial model |
| **Effort** | 5–7 person-weeks **[E]** |
| **Status** | **Strongest candidate.** Pick at the P0 gate if the pilot comes from a carrier, MGA or aggregator. |

Deletes two whole layers: no gateway BOM, no network server, no Join Server key custody, no LoRa
Alliance membership or end-device certification (**−$4,200/yr [V]**). The device speaks CoAP over
DTLS straight to the backend.

Read it for four things it breaks: `f_cnt` disappears with the LNS and takes the idempotency key
with it (recommendation: go to 9 bytes — the 8-byte limit exists to fit LoRaWAN DR0, and carrying
that constraint onto cellular is cargo-culting); the power model is not a scaling of the LoRa one;
**PTCRB/GCF plus per-carrier approval is a cost [`07`](07-compliance-and-certification.md) does not
budget**, which is a real gap in the pack; and `nrf_modem` is a Nordic-5-Clause blob, so the BOM
locks to Nordic and `main`'s portability argument does not survive.

### `design/ble-thread-matter` — DTC retail, at a price

| | |
|---|---|
| **SHA** | `be557d8` · **Adds** | `prototype/adr/ADR-002-ble-thread-matter.md`, 146 lines |
| **Swaps** | LoRaWAN → **BLE + Thread/Matter** on ESP32-C6 |
| **Unlocks** | DTC retail — Apple/Google/Amazon shelf appeal |
| **Effort** | 9–13 person-weeks **[E]** — the largest of the four |
| **Status** | Hold. Correct only if the signed pilot is retail or a consumer brand. |

Answers the `idea_.txt` question directly, with one correction: **`connectedhomeip` is Apache-2.0,
but `openthread` is BSD-3-Clause, not Apache-2.0.**

The finding that sets this branch apart: **Matter is a data model, not a transport, and its standard
clusters cannot carry this product's signal.** `BooleanState.StateValue` is a verdict; the detector
needs a 15-minute pipe/ambient series to learn a baseline. And in the Matter-native topology,
telemetry lands in the homeowner's Apple/Google/SmartThings fabric *first* — which contradicts the
data-moat argument in [`05`](05-open-source-stack.md) §5.4. Survivable, because that document
already concludes the real moat is the carrier relationship. But it must be a decision, not a
discovery.

### `design/wifi-esp-idf` — mains products, and a proof

| | |
|---|---|
| **SHA** | `8e32181` · **Adds** | `prototype/adr/ADR-003-wifi-esp-idf.md`, 131 lines |
| **Swaps** | Zephyr → **ESP-IDF**, LoRaWAN → Wi-Fi |
| **Unlocks** | Mains-powered products — A12, A13, A14 |
| **Effort** | 5–7 person-weeks **[E]** |
| **Status** | **Already useful as a result**, independent of whether it is ever built. |

This branch did a job beyond its own proposal: it **tested a claim `main` rests on.** The firmware
was written with zero Zephyr includes in `leak_detect.c`, `uplink.c`, `valve.c` and
`install_probe.c`, justified as portability. Swapping the RTOS tests that.

**Verdict: the claim holds.** Four of eight firmware modules port byte-for-byte, all of
`detection/` and `codec/` are untouched, and the four C test suites not only port but become
**executable** — ESP-IDF ships a host toolchain, which this box lacks entirely. What does not port
is the devicetree; ESP-IDF has no equivalent, so board wiring becomes imperative C. Named rather
than glossed.

### `design/chirpstack-magistrala` — the portability test for the server

| | |
|---|---|
| **SHA** | `ad99e22` · **Adds** | `prototype/adr/ADR-004-chirpstack-magistrala.md`, 150 lines |
| **Swaps** | The Things Stack → **ChirpStack**; FastAPI → **Magistrala**. Same radio, same device. |
| **Unlocks** | No new buyer route |
| **Effort** | Part A: under 1 week. Part B: 3–5 weeks, **deferred** |
| **Status** | **Split decision — adopt half.** |

Part A (ChirpStack) is nearly free, and the reason is instructive: contract §4 already requires the
backend to decode `frm_payload` server-side and never trust a caller-supplied `decoded_payload`,
because a webhook is untrusted input. That rule was written for security; **portability is its side
effect.** Saves ~$12/month and brings `chirpstack-fuota-server` (multicast firmware update) and
`chirpstack-packet-multiplexer` (zero-downtime migration).

Part B (Magistrala) is **deferred with a named trigger** — adopt when a buyer's security review
first demands mTLS or RBAC. It replaces the plumbing and *none* of the product, and the valve safety
invariants in particular must stay in code we own and test.

---

## The comparison

Deltas against `main`. **[V]** vendor-published, **[E]** estimate.

| | `main` | ADR-001 | ADR-002 | ADR-003 | ADR-004 |
|---|---|---|---|---|---|
| Radio module **[V]** | RAK3172 $5.99 | nRF9151 SiP | ESP32-C6 $2.85–3.40 | ESP32-C6 $2.85–3.40 | unchanged |
| Infrastructure you ship | gateway $99–382 | **none** | customer's border router | **none** | gateway |
| Works on arrival | no | **yes** | no | no | no |
| Recurring per device | ~$0 | **$1–3/yr** | $0 | $0 | ~$0 |
| New certification | — | **PTCRB/carrier** | **CSA $10,500 [V]** | — | — |
| `detection/` changes | — | **none** | **none** | **none** | **none** |
| Who owns the data path | **you** | **you** | **Apple/Google/Amazon** | you | you |
| Licence posture | BSD-3 MAC | **Nordic-5-Clause blob** | Apache-2.0 | Apache-2.0 | MIT + Apache-2.0 |
| Effort **[E]** | — | 5–7 wk | 9–13 wk | 5–7 wk | <1 wk (Part A) |

**`detection/` is unchanged in all four, and that is the point.** 130 tests and a measured 0.3383
nuisance alerts/device-year carry across every radio and every stack, because the detector is
stdlib-only pure functions over a `Sample` and has no idea what delivered the bytes. **If a future
migration plan ever requires touching `detection/`, treat it as a defect in the new design rather
than a cost of the migration.**

---

## Why none of these are merged, and should not be

Merging all four would leave the repository asserting four incompatible radio architectures at once.
They are **mutually exclusive proposals**, not features.

The intended lifecycle:

1. The **P0 buyer gate** ([`08-roadmap.md`](08-roadmap.md) §2) produces a countersigned pilot.
2. That identifies the buyer, which determines the radio — Fork C is downstream of Fork A.
3. The winning branch's migration plan is executed on a **feature** branch, which does merge.
4. The losing ADRs stay exactly where they are, as the record of what was considered and why it
   lost. An ADR that loses is not waste; it is the thing that stops the same argument being had
   again in six months.

Nothing here is abandoned work. It is four priced options with one week of writing behind them
instead of four part-built stacks with four months behind them.

---

## Conventions

| Prefix | Meaning | Merges to `main`? |
|---|---|---|
| `main` | The implementation | — |
| `design/*` | An ADR plus a migration plan. **Docs only, zero code.** | **No** |
| `feat/*` | Executing a chosen migration plan | Yes |
| `fix/*` | Corrections to shipped code or verified claims | Yes |

Rules worth keeping:

- A `design/*` branch that acquires code is no longer a design branch. Rename it `feat/*` or drop
  the code.
- Keep `design/*` rebased onto `main` so `git diff main..<branch>` shows only the ADR. They were
  rebased on 2026-10-15 for exactly this reason: `main` had moved ahead, and the diffs were showing
  a spurious 308-line deletion of a document that only exists on `main`.
- Licence and cost claims cite [`data/repos.csv`](data/repos.csv) and
  [`data/cert-costs.csv`](data/cert-costs.csv), and `make verify-licenses` must still pass on any
  branch that touches them.

```bash
git branch -a                                    # all branches
git log --oneline --graph --all                  # the shape
git diff main..design/lte-m-nbiot                # one ADR, nothing else
git checkout design/lte-m-nbiot                  # read one
```
