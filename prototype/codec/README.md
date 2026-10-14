# `codec/` — LoRaWAN payload codec

The on-air format for the A1 clamp-on differential-temperature leak sensor, implementing
[`../contracts/INTERFACES.md`](../contracts/INTERFACES.md) §1 (8-byte uplink, FPort 1),
§2 (4-byte downlink, FPort 2) and §3 (decoded JSON).

Zero runtime dependencies. Tests use the built-in `node:test` runner.

```sh
node --test          # or: npm test
```

## Why this file is shaped like this

It is written to be dropped straight into
[`TheThingsNetwork/lorawan-devices`](https://github.com/TheThingsNetwork/lorawan-devices)
(Apache-2.0). Per `../../05-open-source-stack.md` §5.3 that is a go-to-market asset rather than
housekeeping: a published codec is how the sensor auto-provisions on TTN, TTS and partners'
stacks, so an integrator never has to be sent a PDF of a byte table.

Consequences of that goal, which otherwise look like odd style:

- **ES5-level syntax, no `require`, no modern builtins** inside the three exported functions.
  TTS evaluates codecs in a sandbox with no module system.
- `module.exports` is wrapped in `if (typeof module !== 'undefined' && module.exports)`. That
  guard is what lets the *identical* file be both a submittable TTS codec and a `require()`-able
  Node module, so there is no second copy to drift.
- The functions are named exactly `decodeUplink`, `encodeDownlink`, `decodeDownlink` and take
  `{ bytes, fPort }` / `{ data }`, returning `{ data | bytes, warnings, errors }`, because that
  is the TTS contract.

## API

| Function | In | Out |
|---|---|---|
| `decodeUplink({bytes, fPort})` | 8 bytes, FPort 1 | `{data: §3 object, warnings, errors}` |
| `encodeDownlink({data})` | `{command, arg, token}` | `{bytes: 4, fPort: 2, warnings, errors}` |
| `decodeDownlink({bytes, fPort})` | 4 bytes, FPort 2 | `{data: {command, arg, token}, warnings, errors}` |
| `encodeUplink({data})` | §3 object | `{bytes: 8, fPort: 1, warnings, errors}` |

`encodeUplink` is **not** part of the TTS codec API — TTS has no such hook, because uplinks come
from the device. It exists so the test suite can round-trip §1 and so `firmware/` and
`simulator/` have one reference encoder to check their own byte packing against. It is exported
from the module but is invisible to TTS, which only ever calls the three named entry points.

Also exported, so that the backend, the detector and the simulator all spell things the same way
instead of each inventing a spelling: `UPLINK_REASONS`, `DOWNLINK_COMMANDS`, `UPLINK_FPORT`,
`DOWNLINK_FPORT`.

### It never throws

Every malformed input — wrong length, non-integer bytes, a base64 string passed where a byte
array was expected, an `uplink_reason` from firmware that does not exist yet — comes back as
`errors: [...]` with `data: {}`. §3 spells out why: in TTS the codec runs inside the webhook
path, so an exception on one device's frame takes the webhook down for **every** device on the
application. One unit running next year's firmware must not be able to blind the fleet.

`warnings` is used for "decodable but someone should look": reserved flag bits set, an
`uplink_reason` outside the four defined values.

## Two decisions worth knowing about

**1. `delta_c` is computed in integer centi-degrees, then divided once.**

The snippet published in `../../05-open-source-stack.md` §5.3 computes
`i16(0,1) / 100 - i16(2,3) / 100`. For the contract's own §3 worked example that yields
`-7.149999999999999`, not `-7.15`, because neither `11.25` nor `18.4` lands on a binary
fraction that subtracts cleanly. This codec computes `(pipeCenti - ambientCenti) / 100`
instead. Identical physics, but the emitted JSON matches §3 character for character, which
matters because the backend stores this string and the dashboard prints it. There is a test that
sweeps every `pipe_c` value against a fixed ambient and asserts the serialised `delta_c` never
grows a tail of 9s.

**2. `uplink_reason` string names.** §1 gives the integers and their English meanings; §3
requires the *string* name on the way out but only shows `"heartbeat"`. The other three names
are therefore this codec's choice, in snake_case to match §2's command names:

| byte | §1 meaning | name emitted |
|---|---|---|
| 0 | heartbeat | `heartbeat` |
| 1 | threshold | `threshold` |
| 2 | boot | `boot` |
| 3 | install-probe result | `install_probe` |
| ≥4 | undefined | `reserved_<n>` |

If another component picked different spellings, this table is the one to change — it is a
single array at the top of `index.js`, also exported as `UPLINK_REASONS`.

## Test coverage

23 tests, all passing on Node 24. The substantive ones:

- **Golden vectors** — five base64 payloads with their exact §3 decodings. This table is
  duplicated verbatim in `../simulator/test_fleet.py`, so the JS and Python implementations of
  §1 are checked against the same expected bytes. That is the test that catches a byte-order or
  sign-extension disagreement between components, and it is worth more than the rest combined.
- **Sign extension** — `-7.15` survives; every negative raw value from −32768 up is swept;
  `0xFD35` decodes to −7.15 and not 64821. Reading the pair unsigned turns a −7.11 °C pipe into
  a +648 °C pipe, which reads as a sensor fault instead of the leak it is.
- **Exhaustive-ish byte sweep** — both sign bits, both int16 extremes, every flag byte including
  reserved bits, every possible `uplink_reason` byte: decode must not throw and must re-encode
  to the same bytes. A frame that decodes but does not re-encode is losing information.
- **All 256 `uplink_reason` values**, asserting `reserved_<n>` and that the rest of the frame is
  still usable afterwards.
- **All 8 combinations** of the three defined flag bits, plus all 31 reserved-bit patterns.
- **Lengths 0–20** except 8, plus structurally broken inputs.
- **Downlink** round-trip for all five §2 commands, the exact byte layout, and refusal of what
  §2 forbids (`set_heartbeat` outside 1–240, non-zero `arg` on other commands, out-of-range
  token).

## Limitations, stated plainly

- There is **no uplink encoder in TTS**, so `encodeUplink` cannot be exercised by the real
  stack; it is a prototype and firmware-verification tool only.
- This codec validates *structure*, never *plausibility*. A 300 °C pipe decodes fine. Range
  checks on physical values belong in `detection/` (§5 requirement 3), because what counts as
  implausible depends on the installation, and a codec that drops implausible frames destroys
  the evidence needed to diagnose a sensor fault.
- `f_cnt` is not in the payload and never will be (§1). Anything about duplicate detection or
  loss accounting is the network server's and the backend's business, not the codec's.
- Submitting to `lorawan-devices` also needs a `vendor/…/*.yaml` device definition and a
  LoRaWAN DeviceProfile. Those depend on a real DevEUI block and an assigned vendor ID, neither
  of which exists at prototype stage, so they are not in this directory.
