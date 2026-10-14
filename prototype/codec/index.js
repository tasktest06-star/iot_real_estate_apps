/**
 * LoRaWAN payload codec for the clamp-on differential-temperature leak sensor (A1).
 *
 * WHAT THIS IS
 *   An implementation of `contracts/INTERFACES.md` §1 (8-byte uplink, FPort 1), §2 (4-byte
 *   downlink, FPort 2) and §3 (decoded JSON shape), in the shape The Things Stack expects, so
 *   the same file can be submitted to TheThingsNetwork/lorawan-devices (Apache-2.0). Per
 *   `../../05-open-source-stack.md` §5.3 that submission is a go-to-market asset, not a
 *   nicety: it is how the sensor auto-provisions on TTN, TTS and partners' stacks.
 *
 * WHAT THIS DELIBERATELY DOES NOT DO
 *   - It does not decide anything. No thresholds, no leak logic, no state. `delta_c` is
 *     derived arithmetic, not a verdict; the classifier lives in `detection/` (and in P2, on
 *     the node -- see `../../08-roadmap.md` §4).
 *   - It never throws. Every malformed input comes back as `errors: [...]` with no `data`.
 *     A codec that throws takes the whole TTS webhook down for every device on the
 *     application, not just the one that sent the bad frame (INTERFACES.md §3).
 *   - It does not carry a sequence number. §1 is explicit: `f_cnt` comes from the network
 *     server and duplicating it on-air would waste one of eight scarce bytes.
 *   - No dependencies, no `require`, ES5-level syntax only in the three exported functions,
 *     because TTS evaluates codecs in a sandbox with no module system and no modern builtins.
 */

'use strict';

/* §1 byte 7. The contract gives the integers and their meanings; these are the string names
 * §3 requires on the wire out. Exported so the backend, detector and simulator spell them the
 * same way instead of each inventing a spelling. */
var UPLINK_REASONS = ['heartbeat', 'threshold', 'boot', 'install_probe'];

/* §2 byte 0. The contract already names these in snake_case, so they are used verbatim. */
var DOWNLINK_COMMANDS = ['nop', 'valve_close', 'valve_open', 'set_heartbeat', 'request_install_probe'];

var UPLINK_LENGTH = 8;
var DOWNLINK_LENGTH = 4;
var UPLINK_FPORT = 1;
var DOWNLINK_FPORT = 2;

/* bits 3-7 of §1 byte 6 are reserved and must be transmitted as 0. A frame with them set is
 * decodable -- we are not going to drop a leak alert over a reserved bit -- but it means the
 * firmware and this codec disagree about the layout, which is worth surfacing. */
var FLAGS_RESERVED_MASK = 0xf8;

/* ------------------------------------------------------------------ helpers */

/** Normalise TTS's `input.bytes` (array), a Buffer, or a Uint8Array into a plain array. */
function toByteArray(bytes) {
  if (bytes === null || bytes === undefined) return null;
  if (typeof bytes.length !== 'number') return null;
  var out = [];
  for (var i = 0; i < bytes.length; i++) {
    var b = bytes[i];
    if (typeof b !== 'number' || !isFinite(b) || Math.floor(b) !== b || b < 0 || b > 255) {
      return null;
    }
    out.push(b);
  }
  return out;
}

/** Big-endian int16 with sign extension. The `<< 16 >> 16` is the whole trick: JS bitwise ops
 * work on int32, so shifting a 16-bit value up to the sign bit and back arithmetic-shifts the
 * sign down through the high half. Without it 0xFD39 reads as 64825 instead of -711, which is
 * how a −7.11 °C pipe becomes a 648 °C pipe. */
function int16be(hi, lo) {
  return (((hi << 8) | lo) << 16) >> 16;
}

function uint16be(hi, lo) {
  return ((hi << 8) | lo) >>> 0;
}

function reasonName(n) {
  /* §3: unknown values decode to "reserved_<n>", they do not throw. New firmware in the field
   * will outlive this codec's deployment, so an unrecognised reason is an expected condition. */
  return n < UPLINK_REASONS.length ? UPLINK_REASONS[n] : 'reserved_' + n;
}

function commandName(n) {
  return n < DOWNLINK_COMMANDS.length ? DOWNLINK_COMMANDS[n] : 'reserved_' + n;
}

function commandCode(v) {
  if (typeof v === 'number') return Math.floor(v) === v && v >= 0 && v <= 255 ? v : -1;
  if (typeof v === 'string') {
    for (var i = 0; i < DOWNLINK_COMMANDS.length; i++) {
      if (DOWNLINK_COMMANDS[i] === v) return i;
    }
  }
  return -1;
}

function reasonCode(v) {
  if (typeof v === 'number') return Math.floor(v) === v && v >= 0 && v <= 255 ? v : -1;
  if (typeof v === 'string') {
    for (var i = 0; i < UPLINK_REASONS.length; i++) {
      if (UPLINK_REASONS[i] === v) return i;
    }
    /* Accept what we emit, so decode -> encode -> decode round-trips an unknown reason. */
    var m = /^reserved_(\d+)$/.exec(v);
    if (m) {
      var n = parseInt(m[1], 10);
      if (n >= 0 && n <= 255) return n;
    }
  }
  return -1;
}

/** Degrees C -> centi-degrees, half away from zero, range-checked against int16. */
function toCenti(c, field, errors) {
  if (typeof c !== 'number' || !isFinite(c)) {
    errors.push(field + ' must be a finite number');
    return 0;
  }
  var n = c < 0 ? -Math.round(-c * 100) : Math.round(c * 100);
  if (n < -32768 || n > 32767) {
    errors.push(field + ' out of int16 centi-degree range (-327.68..327.67): ' + c);
    return 0;
  }
  return n;
}

/* ------------------------------------------------- The Things Stack entry points */

/**
 * §1 + §3. `input` is `{ bytes: number[], fPort: number }`; TTS also passes `recvTime`, which
 * this codec does not need because the payload carries no timestamp.
 */
function decodeUplink(input) {
  var warnings = [];
  var bytes = toByteArray(input && input.bytes);

  if (bytes === null) {
    return { data: {}, warnings: warnings, errors: ['input.bytes must be an array of 0..255 integers'] };
  }
  if (bytes.length !== UPLINK_LENGTH) {
    /* Graceful, not fatal-to-the-webhook: a truncated frame is reported and dropped. Returning
     * a partially-decoded frame would be worse -- the backend would store a 0 °C pipe reading
     * and the detector would see a freeze that never happened. */
    return {
      data: {},
      warnings: warnings,
      errors: ['expected ' + UPLINK_LENGTH + ' bytes on FPort ' + UPLINK_FPORT + ', got ' + bytes.length],
    };
  }
  var fPort = input.fPort;
  if (fPort !== undefined && fPort !== null && fPort !== UPLINK_FPORT) {
    return {
      data: {},
      warnings: warnings,
      errors: ['unsupported FPort ' + fPort + ' (uplink data is FPort ' + UPLINK_FPORT + ')'],
    };
  }

  var pipeCenti = int16be(bytes[0], bytes[1]);
  var ambientCenti = int16be(bytes[2], bytes[3]);
  var flags = bytes[6];
  var reason = bytes[7];

  if (flags & FLAGS_RESERVED_MASK) {
    warnings.push('reserved flag bits set: 0x' + (flags & FLAGS_RESERVED_MASK).toString(16));
  }
  if (reason >= UPLINK_REASONS.length) {
    warnings.push('unknown uplink_reason ' + reason);
  }

  return {
    data: {
      pipe_c: pipeCenti / 100,
      ambient_c: ambientCenti / 100,
      /* Subtract in integer centi-degrees and divide once. Doing it the other way round --
       * `pipe_c - ambient_c` on the already-divided floats, as the snippet in
       * `../../05-open-source-stack.md` §5.3 does -- yields 11.25 - 18.4 =
       * -7.149999999999999 for the contract's own §3 worked example. Same physics, but the
       * JSON stops matching the contract byte for byte and anything comparing strings or
       * hashing the decoded payload disagrees. */
      delta_c: (pipeCenti - ambientCenti) / 100,
      battery_mv: uint16be(bytes[4], bytes[5]),
      flags: {
        install_verified: !!(flags & 0x01), // clamp seated; an unverified install is never
        tamper: !!(flags & 0x02), //           counted (08 §8 risk 4, INTERFACES.md §5.2)
        cold_warning: !!(flags & 0x04), //     freeze peril = A4, free upside on this sensor
      },
      uplink_reason: reasonName(reason),
    },
    warnings: warnings,
    errors: [],
  };
}

/**
 * §2. `input` is `{ data: { command, arg, token } }`; returns `{ bytes, fPort }`.
 * `command` may be the string name (preferred) or the raw integer.
 */
function encodeDownlink(input) {
  var errors = [];
  var warnings = [];
  var data = (input && input.data) || {};

  var cmd = commandCode(data.command);
  if (cmd < 0) {
    errors.push('unknown command: ' + JSON.stringify(data.command === undefined ? null : data.command));
  } else if (cmd >= DOWNLINK_COMMANDS.length) {
    warnings.push('command ' + cmd + ' is not defined in the contract; sending it anyway');
  }

  var arg = data.arg === undefined || data.arg === null ? 0 : data.arg;
  if (typeof arg !== 'number' || Math.floor(arg) !== arg || arg < 0 || arg > 255) {
    errors.push('arg must be an integer 0..255');
    arg = 0;
  } else if (cmd === 3) {
    /* §2: set_heartbeat takes minutes 1..240. The upper bound is a power budget, not an
     * arbitrary limit -- see `../../06-hardware-and-devkits.md` on the Li-SOCl2 energy
     * budget; the lower bound stops a downlink typo from flattening a four-year battery. */
    if (arg < 1 || arg > 240) errors.push('set_heartbeat arg must be 1..240 minutes, got ' + arg);
  } else if (arg !== 0) {
    errors.push('arg must be 0 for command ' + commandName(cmd));
  }

  var token = data.token === undefined || data.token === null ? 0 : data.token;
  if (typeof token !== 'number' || Math.floor(token) !== token || token < 0 || token > 65535) {
    errors.push('token must be a uint16 (0..65535)');
    token = 0;
  }

  if (errors.length) return { bytes: [], fPort: DOWNLINK_FPORT, warnings: warnings, errors: errors };

  return {
    bytes: [cmd & 0xff, arg & 0xff, (token >> 8) & 0xff, token & 0xff],
    fPort: DOWNLINK_FPORT,
    warnings: warnings,
    errors: [],
  };
}

/**
 * §2 in reverse. TTS calls this to show an operator what it just sent; the prototype also uses
 * it to prove `encodeDownlink` round-trips, and `backend/` uses it to render the audit trail of
 * valve commands required by §8 ("every command is persisted with its outcome").
 */
function decodeDownlink(input) {
  var warnings = [];
  var bytes = toByteArray(input && input.bytes);

  if (bytes === null) {
    return { data: {}, warnings: warnings, errors: ['input.bytes must be an array of 0..255 integers'] };
  }
  if (bytes.length !== DOWNLINK_LENGTH) {
    return {
      data: {},
      warnings: warnings,
      errors: ['expected ' + DOWNLINK_LENGTH + ' bytes on FPort ' + DOWNLINK_FPORT + ', got ' + bytes.length],
    };
  }
  var fPort = input.fPort;
  if (fPort !== undefined && fPort !== null && fPort !== DOWNLINK_FPORT) {
    return {
      data: {},
      warnings: warnings,
      errors: ['unsupported FPort ' + fPort + ' (downlink data is FPort ' + DOWNLINK_FPORT + ')'],
    };
  }
  if (bytes[0] >= DOWNLINK_COMMANDS.length) warnings.push('unknown command ' + bytes[0]);

  return {
    data: {
      command: commandName(bytes[0]),
      arg: bytes[1],
      token: uint16be(bytes[2], bytes[3]),
    },
    warnings: warnings,
    errors: [],
  };
}

/* ------------------------------------------------------------- local extension */

/**
 * NOT part of the TTS codec API -- TTS has no `encodeUplink`, because uplinks come from the
 * device. It exists so the test suite can round-trip §1 and so `firmware/` and `simulator/`
 * have one reference encoder to check their own byte packing against. Takes the §3 decoded
 * shape and returns the 8 bytes that decode back to it.
 */
function encodeUplink(input) {
  var errors = [];
  var warnings = [];
  var data = (input && input.data) || {};

  var pipeCenti = toCenti(data.pipe_c, 'pipe_c', errors);
  var ambientCenti = toCenti(data.ambient_c, 'ambient_c', errors);

  var mv = data.battery_mv;
  if (typeof mv !== 'number' || Math.floor(mv) !== mv || mv < 0 || mv > 65535) {
    errors.push('battery_mv must be a uint16 (0..65535)');
    mv = 0;
  }

  var f = data.flags || {};
  var flags = (f.install_verified ? 0x01 : 0) | (f.tamper ? 0x02 : 0) | (f.cold_warning ? 0x04 : 0);

  var reason = reasonCode(data.uplink_reason);
  if (reason < 0) {
    errors.push('unknown uplink_reason: ' + JSON.stringify(data.uplink_reason === undefined ? null : data.uplink_reason));
    reason = 0;
  }

  if (errors.length) return { bytes: [], fPort: UPLINK_FPORT, warnings: warnings, errors: errors };

  return {
    bytes: [
      (pipeCenti >> 8) & 0xff,
      pipeCenti & 0xff,
      (ambientCenti >> 8) & 0xff,
      ambientCenti & 0xff,
      (mv >> 8) & 0xff,
      mv & 0xff,
      flags,
      reason,
    ],
    fPort: UPLINK_FPORT,
    warnings: warnings,
    errors: [],
  };
}

/* The TTS sandbox has no module system, so this guard is what lets the identical file be both
 * a submittable `lorawan-devices` codec and a `require()`-able Node module. */
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    decodeUplink: decodeUplink,
    encodeDownlink: encodeDownlink,
    decodeDownlink: decodeDownlink,
    encodeUplink: encodeUplink,
    UPLINK_REASONS: UPLINK_REASONS,
    DOWNLINK_COMMANDS: DOWNLINK_COMMANDS,
    UPLINK_FPORT: UPLINK_FPORT,
    DOWNLINK_FPORT: DOWNLINK_FPORT,
  };
}
