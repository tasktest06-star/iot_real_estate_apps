# Host tests for the pure firmware logic

## Read this first: these tests have never been run

**There is no C compiler on the machine this code was written on.** No `gcc`,
no `cc`, no `clang`, no `cmake`, no `west`, no Zephyr tree. The commands below
are what you type; they are not a transcript of something that happened.

```
$ which gcc cc clang cmake west
/usr/bin/which: no gcc in (...)
/usr/bin/which: no cc in (...)
/usr/bin/which: no clang in (...)
/usr/bin/which: no cmake in (...)
/usr/bin/which: no west in (...)
```

So: **no claim is made that these tests pass.** They have not compiled. The
first person to run them should expect to fix compiler diagnostics — most
likely `-Wsign-compare` and `-Wconversion` complaints in files that do a lot of
`int32_t`/`uint8_t` mixing — before seeing a single assertion result. That is
why `-Werror` is opt-in (`STRICT=1`) rather than the default: the wrong order
of work is "fix every warning before you can run anything".

What *was* done instead, and it is the only verification claim in this
directory: the two golden payload vectors in `test_uplink.c` were cross-checked
numerically against `contracts/INTERFACES.md` itself using `python3`, which this
machine does have. The contract's own base64 example in §4, `"BLEAAQ0yAQA="`,
decodes to the eight bytes `04 B1 00 01 0D 32 01 00`, and those bytes unpack
under `struct.unpack('>hhHBB', ...)` to the field values the §1 table predicts.
So the *expected* side of the codec tests is verified against the contract. The
C that produces the *actual* side is not.

## Running them

```sh
cd firmware/tests
make run                # build and run all four suites
make STRICT=1 run       # same, with -Werror -Wconversion -Wsign-conversion
make SAN=1 run          # same, with ASan + UBSan
make clean
```

`make run` exits non-zero if any suite reports a failure, so it drops into CI
unchanged.

## Why a plain-C harness and not ztest

`ztest` is the right answer for anything that touches Zephyr. It is the wrong
answer for these four files, because it would require a Zephyr tree and either
`native_sim` or real hardware to run them — and the entire point of keeping
`uplink.c`, `leak_detect.c`, `valve.c` and `install_probe.c` free of
`#include <zephyr/...>` is that the logic can be checked anywhere.

That matters most for the payload codec. Three independent implementations of
`contracts/INTERFACES.md` §1 are being written in parallel — this C encoder, a
Node decoder in `codec/`, and a Python decoder in `backend/`. A one-byte
disagreement between them silently corrupts every reading in the fleet and
would not show up as an error anywhere. The golden vectors in `test_uplink.c`
are lifted from the contract document rather than from any implementation,
precisely so that all three can be checked against the same fixture instead of
against each other.

`minitest.h` is forty lines of macros. That is the right size for a harness
whose job is "are these two integers equal".

## What each suite covers

| Suite | Covers | Contract reference |
|---|---|---|
| `test_uplink` | 8-byte encoder, 4-byte decoder, big-endian order, two's-complement negatives, saturation instead of wrapping, reserved bits zero, malformed-downlink refusals | §1, §2, §3 |
| `test_leak_detect` | all six required detector behaviours, plus the `§6` scenarios `slow_leak`, `fast_leak`, `appliance_use`, `shower_morning`, `freeze_event`, `sensor_fault_stuck`, `sensor_fault_noisy`, `install_unverified`, `cold_basement_winter` | §5, §6 |
| `test_valve` | fail-as-is invariants I1-I4, the `arm_or_refuse` truth table, sticky `V_REFUSED`/`V_FAULT`, the asymmetric battery gate, the exercise cycle | §8; `05-open-source-stack.md` §5.3 |
| `test_install_probe` | thermal install verification and its failure taxonomy | §5 behaviour 2; `08-roadmap.md` §8 risk 4 |

`test_valve` is the one worth reading if you only read one. Every assertion in
it checks the **fake hardware's stroke counters**, not the returned state. The
claim being made to a customer is that this device will not shut their water off
by accident; "`V_UNKNOWN` never actuates" is a statement about electricity
reaching a solenoid, not about the value of an enum, so that is what gets
asserted.

## Things these tests deliberately do not cover

- **Anything in `src/main.c`.** It is Zephyr glue — devices, work queues,
  `lorawan_send`, PM, BLE — and testing it needs `native_sim` at minimum and
  really needs hardware. It is also where every unverified API signature lives;
  those are marked `API NOTE` in the source and listed in `../README.md`.
- **Timing and power.** The five-year battery claim and the cold-basement pulse
  behaviour have to be *measured* on hardware at −10 to 0 °C
  (`08-roadmap.md` §8 risk 7 is explicit that modelling it is not acceptable).
  No host test can speak to it.
- **Radio behaviour.** Join, ADR, duty cycle, US915 sub-band channel masks. All
  of it needs a gateway and a network server.
- **The absolute correctness of any threshold.** The tests pin down *behaviour
  at* a threshold, not that the threshold is the right number. Every default in
  `leak_cfg_defaults()`, `valve_cfg_defaults()` and `install_probe_cfg_defaults()`
  is a starting point to be calibrated against bench and field data. The P1
  field trial is what sets them, and the nuisance-alert rate is the only thing
  that can say whether `dwell_s = 1800` is right.
