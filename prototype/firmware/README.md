# Device firmware — clamp-on thermal water-leak sensor (application A1)

Zephyr RTOS application for the battery-powered clamp-on sensor: two TMP117
temperature probes on I²C (pipe + ambient), an SX1262 LoRaWAN radio, BLE for
commissioning and field recovery, MCUboot underneath for signed A/B updates.

It implements the device side of `../contracts/INTERFACES.md` — the 8-byte
FPort 1 uplink (§1), the 4-byte FPort 2 downlink (§2), and a C twin of the
detector in §5 — and the architecture in `../../05-open-source-stack.md` §5.3.

---

## This has not been built. Read this section before anything else.

**Nothing in this directory has been compiled, linked, flashed or run.** The
machine it was written on has no C compiler of any kind, no CMake, no `west`,
and no Zephyr tree:

```
$ which gcc cc clang arm-none-eabi-gcc cmake west
/usr/bin/which: no gcc in (...)
/usr/bin/which: no cc in (...)
/usr/bin/which: no clang in (...)
/usr/bin/which: no arm-none-eabi-gcc in (...)
/usr/bin/which: no cmake in (...)
/usr/bin/which: no west in (...)
```

So treat every statement about this firmware as a design claim, not a test
result. In particular:

- **No `prj.conf` symbol name has been validated by a build.** An undefined
  symbol in a Kconfig fragment is a *warning*, not an error, so a typo there is
  silent at the level of the build and loud at the level of the product — the
  `CONFIG_LORAWAN_REGION_US915` question below (now resolved)
  is the one that would put a US device in EU868. Read the Kconfig warnings.
- **No devicetree node, binding, pin or dt-binding header path has been through
  `dtc`.** The overlays mark the uncertain items `VERIFY (n)` in priority order.
- **No Zephyr API signature has been checked against a header.** The ones that
  carry real doubt are marked `API NOTE` in the source and listed in the ledger
  at the end of this file.
- **The unit tests in `tests/` have never run.** See `tests/README.md`.

What *was* verified is listed in the ledger. It is a short list and it is
honest about being short.

---

## Target Zephyr version

Written against the **Zephyr v3.7 LTS API surface**, which is also what
v4.x keeps for everything used here. That choice is deliberate and it is the
one `../../06-hardware-and-devkits.md` argues for: Zephyr maintains a **v3.7.x
LTS branch alongside mainline**, and it is a CNA with a PSIRT. Under a property
device with a ten-year service life, a long-term branch with a named security
response process is worth more than new features.

Two honesty notes on the version:

- `../../06-hardware-and-devkits.md` describes the project as having a
  "**v3.7.x LTS** branch alongside v4.4.x". The exact current mainline number
  could not be checked from this machine (no tree, no network), so build against
  whatever your `west manifest` actually resolves and treat the `API NOTE`
  comments as the checklist.
- The APIs used here (`lorawan_*`, `sensor_*`, `pm_device_action_run`,
  `settings_*`, `k_work_*`, `adc_*`, `task_wdt_*`) have been stable across
  v3.x and v4.x. The spots where they are *known* to have changed — the
  downlink callback's `flags` vs `data_pending` argument, `DT_NODE_HAS_STATUS`
  vs `DT_NODE_HAS_STATUS_OKAY`, `BT_LE_ADV_CONN` deprecation, the mcumgr group
  symbol rename, and the `random.h` / `rand32.h` header move — are each flagged
  in place with both spellings.

---

## Building

### One-time workspace setup

```sh
pip install --user west
west init -m https://github.com/zephyrproject-rtos/zephyr --mr v3.7-branch zephyrproject
cd zephyrproject
west update
west zephyr-export
pip install --user -r zephyr/scripts/requirements.txt

# Zephyr SDK (provides arm-none-eabi-*). Version per the Zephyr release notes.
west sdk install -t arm-zephyr-eabi
```

Then point `ZEPHYR_BASE` at the tree, or run the commands below from inside the
workspace.

### Primary target — RAK4631 (nRF52840 + SX1262)

```sh
west build -p always -b rak4631/nrf52840 \
    /workshop/plumbing_iot_insurance_applications/prototype/firmware

west flash                      # via the WisBlock's J-Link / DAPLink
```

On a Zephyr older than the hardware-model-v2 rename, the board target is
`rak4631_nrf52840` instead of `rak4631/nrf52840`. The overlay filename
`boards/rak4631_nrf52840.overlay` is correct for both, because Zephyr
normalises `/` to `_` when it looks for board overlays.

### Second target — STM32WLE5 / STM32WL55 (RAK3172 second source)

```sh
west build -p always -b nucleo_wl55jc \
    -- -DEXTRA_CONF_FILE=no_ble.conf \
    /workshop/plumbing_iot_insurance_applications/prototype/firmware
```

`no_ble.conf` is required on this target: the STM32WLE5 is a single die with a
sub-GHz radio and **no Bluetooth controller**, so `prj.conf`'s BLE block cannot
be satisfied there. What that costs is stated in `no_ble.conf` itself and it is
not trivial — mcumgr-over-BLE field recovery goes away with it.

### Building the MCUboot bootloader as well

```sh
west build -p always -b rak4631/nrf52840 --sysbuild \
    /workshop/plumbing_iot_insurance_applications/prototype/firmware
```

**Production blocker, stated so it cannot be missed:** MCUboot's in-tree signing
key is a *published development key*. Shipping with it means anybody can sign an
image this bootloader will accept, on a device that can shut off a building's
water. Generate a per-product key, set `CONFIG_MCUBOOT_SIGNATURE_KEY_FILE` in
the bootloader build, and keep the private half out of this repository.

### Host unit tests

```sh
cd tests && make run
```

See `tests/README.md`. These need a host compiler and nothing else — no Zephyr,
no cross toolchain.

---

## Layout

```
CMakeLists.txt                       Zephyr app build
prj.conf                             Kconfig fragment, heavily commented
Kconfig                              application tunables (CONFIG_APP_*)
no_ble.conf                          overlay conf for radios without BLE
boards/rak4631_nrf52840.overlay      nRF52840 + external SX1262
boards/nucleo_wl55jc.overlay         STM32WL on-die radio (SUBGHZSPI)
src/main.c                           Zephyr glue; the shutoff decision site
src/leak_detect.{c,h}                detector — C twin of detection/detector.py
src/uplink.{c,h}                     §1 encoder, §2 decoder
src/valve.{c,h}                      fail-as-is shutoff state machine
src/install_probe.{c,h}              install verification + install_probe_t
tests/                               plain-C host harness
```

### The four pure modules

`leak_detect.c`, `uplink.c`, `valve.c` and `install_probe.c` contain **no
`#include <zephyr/...>` at all** — only `<stdint.h>`, `<stdbool.h>` and
`<stddef.h>`. All hardware access in the valve path goes through an injected
`valve_hw_t` function-pointer struct, and all tunables arrive as `*_cfg` structs
rather than being read from Kconfig.

That is a property worth protecting, not an accident of style. It is what makes
the 8-byte encoder checkable against the Node codec and the Python backend on
any machine with a C compiler, and it is what lets the valve safety invariants be
asserted against a fake actuator rather than against a comment. If a Zephyr
include ever appears in one of those four files, `tests/` stops building and
both of those guarantees go with it.

`main.c` is the only file that knows Zephyr exists, and it contains no
thresholds, no payload layout and no valve state transitions.

---

## The 8-byte uplink, byte for byte

`contracts/INTERFACES.md` §1, FPort 1, **big-endian**, implemented in
`uplink_pack()`:

| Offset | Size | Field | Encoding |
|---|---|---|---|
| 0 | 2 | `pipe_c` | int16, centi-°C, two's complement, MSB first |
| 2 | 2 | `ambient_c` | int16, centi-°C |
| 4 | 2 | `battery_mv` | uint16, millivolts |
| 6 | 1 | `flags` | bit0 `install_verified`, bit1 `tamper`, bit2 `cold_warning`, bits 3-7 zero |
| 7 | 1 | `uplink_reason` | 0 heartbeat · 1 threshold · 2 boot · 3 install-probe result |

Three implementation decisions worth knowing about:

- **It saturates, it does not wrap.** `uplink_pack()` takes `int32_t`
  temperatures and clamps to `INT16_MIN`/`INT16_MAX`. A faulted I²C read can
  produce a value far outside the TMP117's −55…+150 °C range, and wrapping it
  would turn a hot-side fault into a plausible *cold* reading — exactly the
  direction that fabricates a leak. A saturated value is detectable downstream;
  a wrapped one is not.
- **Reserved bits 3-7 are always zero**, so the backend can use a nonzero
  reserved nibble as a protocol-version tripwire.
- **No sequence number**, per §1: the network server's `f_cnt` serves that
  purpose and duplicating it on air would spend one of eight scarce bytes.
  `delta_c` is likewise derived (`pipe_c − ambient_c`) and never transmitted,
  even though it is the actual signal.

Golden vectors, taken from the contract rather than from any implementation, in
`tests/test_uplink.c`:

| Source | Bytes | Decodes to |
|---|---|---|
| §4's `frm_payload` `"BLEAAQ0yAQA="` | `04 B1 00 01 0D 32 01 00` | pipe 12.01 °C, ambient 0.01 °C, 3378 mV, install_verified, heartbeat |
| §3's decoded-JSON example | `04 65 07 30 0D 52 01 00` | pipe 11.25 °C, ambient 18.40 °C, 3410 mV, install_verified, heartbeat |

Both were cross-checked numerically with `python3` against the contract text.

**Cross-component agreement, checked after the fact.** `codec/` and
`backend/app/codec.py` landed after this firmware and agree byte for byte:
the backend uses `struct.unpack(">hhHBB", ...)` for the uplink and
`struct.pack(">BBH", ...)` for the downlink — identical field widths, identical
big-endian order — with the same `0x01`/`0x02`/`0x04` flag bits, and
`codec/index.test.js` independently uses the byte string
`[0x04, 0x65, 0x07, 0x30, 0x0d, 0x52, 0x01, 0x00]`, which is exactly the
second golden vector above. Three implementations, one layout.

Two deliberate differences from those two, both in the firmware's favour for
its role:

- **The firmware saturates where the Python encoder raises.**
  `backend/app/codec.py` raises `PayloadError` on an out-of-int16 temperature;
  `uplink_pack()` clamps. Both are right for what they are: the Python encoder
  is a test helper, where an exception is a useful assertion failure, whereas
  the firmware is the only thing that encodes uplinks in production and must
  never refuse to transmit a reading. A device that declines to report is
  indistinguishable from a dead one, which is `08-roadmap.md` §8 risk 4.
- **The firmware rounds, matching the Node codec.** `codec/index.test.js`
  asserts that 0.005 °C encodes to 1 centi-degree and −0.005 °C to −1, so
  `read_temp_cc()` rounds to nearest away from zero when it converts Zephyr's
  micro-degree `sensor_value`. Truncating would have disagreed with the codec by
  one count on about half of all readings — harmless to any decision, but
  exactly the sort of systematic difference that costs an afternoon when someone
  finally diffs the two.

The 4-byte downlink decoder (§2) refuses a wrong-length frame rather than
guessing at it (a mis-length frame that gets "fixed up" could be a
`valve_close`), refuses a `set_heartbeat` outside 1…240 rather than clamping
(a 0-minute heartbeat from a corrupted frame transmits continuously and flattens
a primary cell in days), and reports an unknown command byte without treating it
as fatal — mirroring §3's rule that a decoder must not throw.

---

## The shutoff decision is local. That is the architecture.

The decision site is in `sample_work_handler()` in `src/main.c`, under a comment
that says why. In short:

**LoRaWAN Class A opens a receive window only after an uplink.** A cloud-issued
close therefore cannot reach the device until the next scheduled uplink, so at a
15-minute heartbeat the worst case is **fifteen minutes of flooding**.
`../../04-connectivity.md` §4.2 lays out the alternatives and both are closed:
Class B costs continuous beacon tracking, Class C costs the entire power budget
and effectively means mains power. Neither is available to a five-year primary
cell in a crawlspace.

`../../08-roadmap.md` §8 risk 5 gives the leading indicator in one line: *"any
architecture doc that has the cloud closing the valve."*

So the local detector verdict is the trigger. The cloud **confirms and reports;
it does not arbitrate.** A `valve_close` downlink is still honoured — a human
with a phone at the property is a legitimate actor — but it is tagged
`VALVE_SRC_CLOUD_DOWNLINK` so the two paths stay distinguishable in an audit,
it is subject to every one of the same gates, and **no part of the safety
argument depends on a downlink arriving.**

### Shadow mode is the default

`CONFIG_APP_LOCAL_SHUTOFF_ENABLED` defaults to **n**. Standing rule 3,
`../../08-roadmap.md` §0: no customer-visible action until the nuisance rate is
measured — an insurer's trust is spent once. With it off the detector still
runs, still produces verdicts, still logs them and still uplinks them, and
nothing actuates. That is the device-side mirror of the backend's
`alerting_enabled = false` / `status = "shadow"` behaviour in §8.

`CONFIG_APP_VALVE_PRESENT` also defaults to **n**, because the year-1 SKU is
sensor-only (`../../08-roadmap.md` §10: *"No inline valve in year 1. Triggers
every gate at once; resell a certified one."*). The state machine is still
compiled and still instantiated, with a NULL hardware backend, so every request
answers `VALVE_RC_NO_HW` — compiling it out would let the shipped build diverge
from the tested one.

---

## Valve safety: fail-as-is, never fail-closed

`05-open-source-stack.md` §5.3 states the rule and the reason: *"A valve that
closes on battery death or a firmware fault floods support with 'no water' calls
and, in a freeze event, causes the exact damage you were hired to prevent."*
`08-roadmap.md` §8 risk 9 adds the commercial version — a false shutoff in a
commercial building is a business-interruption claim against us.

States are exactly the document's: `V_UNKNOWN, V_OPEN, V_CLOSED, V_REFUSED,
V_FAULT`. Four invariants, each with tests in `tests/test_valve.c` that assert
on the **fake actuator's stroke counters** rather than on the returned state —
because "never actuates" is a claim about current reaching a solenoid, not about
the value of an enum:

| | Invariant |
|---|---|
| **I1** | No code path closes the valve as a consequence of a fault, reset, brown-out, watchdog expiry, join failure or battery warning. Closing happens only from an explicit request in an armed state. `valve_set_battery_mv()` is deliberately a pure setter with no consequences. |
| **I2** | Boot state is `V_UNKNOWN`, and `V_UNKNOWN` never actuates. After a reset the stem position is genuinely unknown; the valve position is deliberately **not** persisted-and-trusted. |
| **I3** | `V_REFUSED` and `V_FAULT` are sticky. `V_REFUSED` clears only on a fresh *passing* probe; `V_FAULT` additionally needs `valve_clear_fault()`, which only the BLE commissioning path calls. **No downlink can clear either.** |
| **I4** | On any actuation error the valve is left exactly where it is. No retry, no corrective stroke. A second stroke into an unknown mechanical condition is how a stem shears — and if a close half-completed, finishing it is precisely the fail-closed behaviour being avoided. |

`arm_or_refuse()` is the document's function, with the document's three tests in
the document's order: breakaway torque over threshold → `V_REFUSED`; post-cycle
stem leak → `V_REFUSED`; cycle time over timeout → `V_FAULT`. Two additions,
both documented in the source: an **incomplete probe is `V_FAULT`**, because no
evidence is not evidence of fitness; and it takes a `cfg` argument (the document
uses compile-time macros) so thresholds can be per-SKU and the function can be
unit-tested — passing `cfg == NULL` reproduces the document's behaviour exactly.

Two further design points:

- **The battery gate is asymmetric.** Below `CONFIG_APP_VALVE_MIN_ACTUATE_MV` a
  *close* is refused but an *open* is permitted. A failed open leaves the valve
  where it was, which is no worse than not trying; a failed close risks a
  partially driven stem, which is a brand-new failure mode, and closed is never
  the safe default.
- **A watchdog reset is the fail-as-is path for a firmware hang.** The reboot
  takes the state machine to `V_UNKNOWN`, which never actuates. The watchdog
  callback touches nothing but `sys_reboot()`.

---

## The detector

`src/leak_detect.{c,h}` is the C twin of `detection/detector.py`: same verdict
strings, same six required behaviours (`contracts/INTERFACES.md` §5), written to
the same restrictions because it has to fit in single-digit kB on a Cortex-M4
when the classifier moves on-device at P2 (`08-roadmap.md` §4: *no NN on the
node before P2*).

- **No allocation anywhere.** The whole state is one caller-owned struct;
  `_Static_assert` pins `sizeof(struct leak_state) <= 256` so nobody can quietly
  add a growing buffer later.
- **No floating point.** Everything is `int32_t` centi-degrees, which is also
  the wire unit, so there is no conversion step to get wrong.
- **Fixed-size ring window** (`LEAK_WINDOW_LEN = 16`, a power of two so the
  index is a mask and the hot path never divides), not a growing list.

Precedence, which `§5` does not specify and which the Python twin must match:

1. `install_unverified` — and it never learns a baseline from an unverified
   install either, so one install error does not poison a device for an hour
   after the installer fixes the clamp.
2. `sensor_fault` — tamper, out of −40…80 °C, a step faster than thermal mass
   allows, a stuck channel, or a noisy one.
3. `freeze_risk` — the more urgent and more actionable verdict; excursion
   bookkeeping still runs underneath, so a qualifying leak reports the moment
   the pipe warms past the threshold.
4. `leak_suspected` / `watch` / `quiet`.

Three choices worth calling out:

- **Baseline adaptation is frozen while an excursion is open.** Without that
  freeze, a slow leak teaches the detector that the leak is normal — the
  baseline walks down to meet it and the alert never fires. It is the single
  most important line in the function.
- **The baseline is held at 1/256 °C internally.** A naïve integer EWMA
  (`baseline += (delta - baseline) >> 6`) stalls permanently once the gap drops
  below 64, leaving a standing offset of up to 0.63 °C against a 1.50 °C
  excursion threshold — a 40% error in the main detection threshold produced
  entirely by rounding, and invisible unless you go looking for it.
- **Stuck-channel detection is time-based, not window-based.** At centi-degree
  quantisation a TMP117 on a still pipe can repeat a value for many minutes, so
  an "all 16 window samples equal" test would false-fault healthy devices. An
  hour of bit-identical readings would not.

### Divergence from `detection/detector.py` — must be reconciled before P2

`detection/detector.py` was written in parallel against the same §5 and landed
after this firmware. The good news first: **the two agree independently on
everything structural.** Same six verdict strings. Same precedence, including
the non-obvious choice that `install_unverified` outranks `sensor_fault`. Same
decision to hold the baseline nearly still during an excursion. Same
fixed-point integer baseline, for the same reason — the Python file's own
comment calls a naïve version "tens of degrees of baseline error". Same
−40…80 °C plausibility band, same 4.0 °C freeze threshold, same 1-hour
stuck-channel span.

Where they differ, **`detector.py` is authoritative**, because it is the
implementation that gets evaluated against the labelled simulator corpus and
the P1 exit gate. This firmware's defaults have no empirical backing at all.

| | This firmware | `detector.py` |
|---|---|---|
| Dwell mechanism | timer over a **continuous** excursion; recovery resets it | **net integrator** — depressed time accrues, recovery erases it at 2× |
| Dwell required | 1800 s | `DWELL_S` 21600 s, or `DWELL_DEEP_S` 5400 s for a deep depression |
| Trigger threshold | fixed 1.50 °C below baseline | **learned** per-device noise floor (MAD × k), floored at 2.0 °C, ceiled at 12.0 °C |
| Baseline time constant | ~1 h | 2 days normal, 1 h during warm-up, 30 days while held |
| Implausible step | rate-based, 1.0 °C/s | instantaneous, 8.0 °C |
| Freeze hysteresis | none — one threshold | warn 4.0 °C / clear 6.0 °C |
| Verdict latching | none | latches, with a 6 h clear |

**Do not simply copy the constants across.** `DWELL_S = 21600` means "6 hours
net" under an integrator and "6 hours uninterrupted" under this firmware's
timer, which are very different sensitivities — the firmware would become far
*less* sensitive, not more aligned. Reconciling them means porting the
mechanism, which is a real task:

1. Replace the continuous-excursion timer with the credit/release integrator
   (`state.dwell_s`, `DT_CREDIT_MAX_S`, `DWELL_RELEASE_MULT`). Bounded and
   integer, so it costs nothing this firmware cannot afford.
2. Add the learned MAD noise floor in place of the fixed `excursion_cc`.
3. Add the freeze hysteresis and the verdict latch.

Until that is done, the firmware detector should be treated as a **structural
reference implementation, not a tuned one** — which is fine, because
`CONFIG_APP_LOCAL_SHUTOFF_ENABLED` defaults to `n` and the cloud classifier is
the one making decisions through P1 anyway (`08-roadmap.md` §4: edge ML is
"none — cloud shadow mode" at P1). The port belongs at P2, when the node takes
over and the nuisance rate is known.

### Known limitation, stated rather than hidden

A delta-from-learned-baseline detector **cannot see a leak that was already
running when the device was installed**: the first good sample seeds the
baseline, so the leak becomes "normal" for that device. Catching it needs an
absolute cross-fleet comparison, which belongs in the backend where the
housing-stock context lives, not on a node that has never seen another node.
`install_probe.c` records `delta_at_install_cc` specifically so the backend has
the datum it needs.

---

## Install verification

The `install_verified` bit (§1 byte 6 bit 0) is the most consequential bit in
the payload: §5 behaviour 2 makes an unverified install incapable of producing a
leak verdict, and `08-roadmap.md` §8 risk 4 says not to count unverified
installs at all, with the leading indicator *">10% of shipped units never
reporting 7 days out"*.

There is no heater in the BOM, so the probe does not self-stimulate — **the
installer runs the nearest fixture for about a minute**, prompted over BLE. The
probe then verifies three things: that the two channels separate at all (if they
track to the LSB, the clamp is dangling next to the ambient sensor); that the
**room did not move** (checked before the pipe test, because a room-wide thermal
event can satisfy the pipe test for entirely the wrong reason); and that the
pipe *did* move. It is a streaming min/max accumulator — about 32 bytes
regardless of how long the tap runs, no sample buffer.

A refused valve does **not** un-verify the sensor. They are two separate
promises, and conflating them would silently stop counting good sensors every
time a valve turned out to be seized.

---

## Where the contract looks wrong or ambiguous

Reported rather than silently worked around, per the instruction at the top of
`contracts/INTERFACES.md`.

1. **§2's echo token is not implementable in payload v1.** §2 says the device
   *"mirrors it in the next uplink's `uplink_reason=3` frame"*, but the §1
   uplink has nowhere to put a `uint16`: all eight bytes are allocated and the
   flags byte has five reserved bits, not sixteen. **What this firmware does:**
   stores the token, sends the reason-3 frame, and relies on ordering over
   `(dev_eui, f_cnt)` for correlation — the same mechanism §1 already mandates
   for dedup and that §9 already indexes. **Clean fixes, all §1 changes:** a
   10-byte payload on a new FPort, a separate FPort 3 ack frame, or a 5-bit
   token in the reserved nibble. Flagged in a comment at the bottom of
   `src/uplink.c`.
2. **`uplink_reason = 3` is overloaded.** §1 names it *"install-probe result"*,
   but §2 requires it to be the response frame for *every* command, including
   `valve_close`. We follow §2 over the field's name. A fifth reason value,
   `command_ack`, would resolve it.
3. **§5 does not define verdict precedence.** Nothing says whether
   `install_unverified` or `sensor_fault` wins when both hold, or whether
   `freeze_risk` outranks `leak_suspected`. Both implementations must agree or
   the C and Python detectors will disagree on real data. The ordering chosen
   here is listed above and asserted in `tests/test_leak_detect.c`.
4. **§5 does not define what `confidence` is confidence *in*.** Read here as
   "confidence in the verdict", 0…100, monotonic in excursion depth and in time
   held beyond dwell — so `quiet` carries *high* confidence when the baseline is
   primed. A reading of "probability of a leak" would invert that. Same risk of
   silent divergence.
5. **§3 and §4's examples are different samples**, which is easy to misread as
   one worked example. §4's bytes also decode to a `delta_c` of +12.00 °C with
   the room at 0.01 °C, which no plumbing produces — they are clearly
   hand-built to exercise the layout. Harmless, but the simulator and backend
   are being written against the same two snippets.
6. **§1's `flags` has no "shutoff state" bit.** The backend is required to
   refuse `POST /valve` when the device is not armed (§8), but the uplink gives
   it no way to learn the valve state — so the backend's notion of "armed" can
   only ever come from the install-probe record, never from live telemetry. A
   device that has since gone to `V_FAULT` will still look armed. Two of the
   five reserved flag bits would fix this.

---

## Verification ledger

### Verified

- **The §1/§2 byte layout against the contract text**, including decoding §4's
  base64 `frm_payload` numerically with `python3` and confirming it unpacks to
  the field values §1 predicts. The golden vectors in `tests/test_uplink.c` are
  derived from the contract, not from this implementation.
- **Internal consistency, by script:** balanced braces and parentheses in all
  nine source files and all five test files; every function declared in a header
  has a definition in its `.c`; every `CONFIG_APP_*` referenced in `src/` is
  defined in `Kconfig` and vice versa; every field of the `app` state struct is
  initialised in `app_state_init()`.
- **That the four pure modules contain no Zephyr includes**, so the host test
  build is structurally possible — and that their *transitive* include closure
  actually covers every type they use. That check caught a real blocker: all
  four use `NULL`, and three of the four headers were missing `<stddef.h>`,
  which would have failed to compile the moment a compiler existed.
- **Detector arithmetic, by hand**, for each scenario asserted in
  `tests/test_leak_detect.c` — dwell timing, excursion open/close points,
  baseline convergence. This is how the integer-EWMA stall bug described above
  was found, before any compiler existed to find it.

### Not verified, and why

| Item | Why not |
|---|---|
| **It compiles** | No `gcc`, `cc`, `clang`, `arm-none-eabi-gcc`, `cmake` or `west` on this machine |
| **It links, fits in flash, or fits in 64 KB RAM on the WLE5** | Same |
| **`tests/` passes** | Same. Expect to fix warnings before seeing assertions |
| **Devicetree compiles** | No `dtc`, no Zephyr tree. See the `VERIFY (n)` blocks in both overlays |
| ~~`CONFIG_LORAWAN_REGION_US915` vs `CONFIG_LORAMAC_REGION_US915`~~ **RESOLVED 2026-10-14** | Verified against upstream `subsys/lorawan/Kconfig` on zephyr main: the choice symbols are `LORAWAN_REGION_{AS923,AU915,CN470,CN779,EU433,EU868,KR920,IN865,US915,RU864}`. **`CONFIG_LORAWAN_REGION_US915` is correct**; the `LORAMAC_REGION_*` alternative is not needed and has been removed from `prj.conf` |
| ~~`CONFIG_TMP117` / the `ti,tmp117` binding~~ **RESOLVED 2026-10-14** | Verified against upstream zephyr main: neither `ti,tmp117` nor `ti,tmp116` exists as a binding any more. Both were consolidated into `dts/bindings/sensor/ti,tmp11x.yaml` (compatible **`ti,tmp11x`**) with the driver at `drivers/sensor/ti/tmp11x` and symbol **`CONFIG_TMP11X`**, covering TMP116/117/119. Both overlays and `prj.conf` now use those names |
| **`lorawan_set_region()` exists** | Added with multi-region support. On an older tree the region is a pure Kconfig choice and the call does not exist |
| **The downlink callback signature** | Written as `(port, uint8_t flags, rssi, snr, len, data)`, which is the post-v3.5 form. Older trees use `bool data_pending` |
| **`lorawan_get_payload_sizes()` returns void** | Believed so; unchecked |
| **`DT_NODE_HAS_STATUS(node, okay)`** | Long-standing form; recent trees prefer `DT_NODE_HAS_STATUS_OKAY(node)` |
| **`BT_LE_ADV_CONN`** | Deprecated in newer Zephyr in favour of `BT_LE_ADV_CONN_FAST_1` / `BT_LE_ADV_CONN_ONE_TIME` |
| **mcumgr group symbol names** | `CONFIG_MCUMGR_GRP_IMG` / `_OS` are the post-3.1 names; older trees use `CONFIG_MCUMGR_CMD_IMG_MGMT` / `_OS_MGMT` |
| **dt-binding header paths** | `zephyr/dt-bindings/{adc/adc.h, adc/nrf-saadc.h, gpio/gpio.h, lora/sx126x.h}` are from memory. A wrong path fails at the preprocessor with the filename in the message — the cheapest item here to resolve |
| **Whether the TMP11x driver implements PM actions** | `pm_device_action_run()` returns `-ENOSYS` with no PM hooks, which the code tolerates. If it has none, the real power win is the sensor's one-shot conversion mode, not device PM |
| **SX1262 GPIO assignments on RAK4631** | Taken from the board support and module datasheet, not from a schematic in front of me. `05 §5.3` explicitly calls these placeholders |
| **I²C instance and STM32 pinctrl node labels** | The least certain items in either overlay. `&i2c1` on RAK4631 and `&i2c2` on PA11/PA12 for the Nucleo are stated as candidates with the exact `grep` to confirm them |
| **Battery divider ratio and SAADC input** | A property of the carrier board's resistors. `CONFIG_APP_BATT_DIVIDER_NUM/DEN` both default to 1 so a wrong ratio shows as an obviously wrong voltage rather than a plausible one |
| **Every threshold default** | `dwell_s = 1800`, `excursion_cc = 150`, `freeze_cc = 400`, `torque_refuse_mnm = 4000`, `cycle_timeout_ms = 8000`, `min_actuate_mv = 3000`. None is a measured result. The breakaway-torque figure in particular needs a bench torque wrench correlated against the current-sense proxy the firmware actually measures |
| **Battery life, cold-basement pulse behaviour, radio range, join time** | All require hardware and a gateway. `08-roadmap.md` §8 risk 7 is explicit that these are to be **measured** at −10 to 0 °C rather than modelled |

### Deliberately not implemented

- **No current-sense torque measurement.** The `valve_hw_t` hook is left `NULL`
  rather than stubbed, so `valve_exercise()` answers `VALVE_RC_NO_HW` and leaves
  the valve alone. The distinction matters: a stub returning an error would send
  the state machine to `V_FAULT` — turning "this build cannot measure torque"
  into a false, sticky accusation that working hardware is broken — and a stub
  returning a plausible number would let a seized valve arm, which is exactly
  the failure `08-roadmap.md` §8 risk 9 tracks. An actuator that cannot report
  its current cannot be re-qualified, and a cosmetic stroke spends a pulse out
  of a primary cell to prove nothing.
- **No stem-leak sensor** on this carrier board, so `arm_or_refuse()`'s second
  test passes on absence of evidence rather than evidence of absence. That is a
  faithful reading of the field's meaning but it is not the same as having
  checked. Closing it is a hardware task — a moisture pad at the stem — and it
  belongs in the shutoff SKU before the shutoff SKU ships.
- **No on-device ML.** `08-roadmap.md` §4: no NN on the node before P2. You
  cannot compress a model you have not trained on data you do not have.
- **No wall-clock time.** There is no RTC in the BOM and LoRaWAN
  `DeviceTimeReq` is optional network-side, so the detector needs only
  *monotonic* timestamps and treats a backwards step as a reboot rather than a
  fault. The backend stamps `received_at` itself (§4).
- **The install record is persisted as a raw struct**, which is fine within one
  build and **not** fine across an OTA that changes its layout. Before the first
  signed A/B update ships it needs a version byte or per-field keys: an MCUboot
  swap that silently reinterprets that record would un-verify a verified fleet,
  and §5 behaviour 2 would then stop every one of those devices from ever
  reporting a leak. Flagged in `save_install()`.
