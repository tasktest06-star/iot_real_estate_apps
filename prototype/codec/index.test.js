/**
 * Tests for the §1/§2/§3 payload codec, using the built-in `node:test` runner and
 * `node:assert` so the package keeps zero dependencies (INTERFACES.md §10).
 *
 * Deliberately not tested here: anything about leak detection. This file asserts bytes and
 * JSON only. The one thing it does assert beyond the contract is the GOLDEN_VECTORS table,
 * which is duplicated verbatim in `../simulator/test_fleet.py` -- two independent
 * implementations of §1 (JS and Python) checked against the same expected bytes. That is the
 * test that actually catches a byte-order or sign-extension disagreement between components.
 *
 *   node --test
 */

'use strict';

const test = require('node:test');
const assert = require('node:assert');
const codec = require('./index.js');

const { decodeUplink, encodeUplink, encodeDownlink, decodeDownlink } = codec;

/** Convenience: decode and assert the frame was clean. */
function decodeOk(bytes, fPort) {
  const r = decodeUplink({ bytes, fPort: fPort === undefined ? 1 : fPort });
  assert.deepStrictEqual(r.errors, [], 'unexpected errors: ' + JSON.stringify(r.errors));
  return r.data;
}

function b64ToBytes(b64) {
  return Array.from(Buffer.from(b64, 'base64'));
}

/* ----------------------------------------------------------------- golden vectors */

/* base64 of the 8 raw §1 bytes -> the exact §3 decoding. Shared with the simulator's test
 * suite; if you change a byte here, change it there. */
const GOLDEN_VECTORS = [
  {
    name: 'contract §3 worked example',
    b64: 'BGUHMA1SAQA=',
    data: {
      pipe_c: 11.25,
      ambient_c: 18.4,
      delta_c: -7.15,
      battery_mv: 3410,
      flags: { install_verified: true, tamper: false, cold_warning: false },
      uplink_reason: 'heartbeat',
    },
  },
  {
    name: 'contract §4 example frm_payload',
    b64: 'BLEAAQ0yAQA=',
    data: {
      pipe_c: 12.01,
      ambient_c: 0.01,
      delta_c: 12.0,
      battery_mv: 3378,
      flags: { install_verified: true, tamper: false, cold_warning: false },
      uplink_reason: 'heartbeat',
    },
  },
  {
    name: 'freeze: negative pipe, cold_warning, threshold uplink',
    b64: '/9YA0g0BBQE=',
    data: {
      pipe_c: -0.42,
      ambient_c: 2.1,
      delta_c: -2.52,
      battery_mv: 3329,
      flags: { install_verified: true, tamper: false, cold_warning: true },
      uplink_reason: 'threshold',
    },
  },
  {
    name: 'both channels negative, tampered, boot',
    b64: '+y7+DA4UAwI=',
    data: {
      pipe_c: -12.34,
      ambient_c: -5.0,
      delta_c: -7.34,
      battery_mv: 3604,
      flags: { install_verified: true, tamper: true, cold_warning: false },
      uplink_reason: 'boot',
    },
  },
  {
    name: 'unverified install, unknown reason 7',
    b64: 'B9AH0Qy6AAc=',
    data: {
      pipe_c: 20.0,
      ambient_c: 20.01,
      delta_c: -0.01,
      battery_mv: 3258,
      flags: { install_verified: false, tamper: false, cold_warning: false },
      uplink_reason: 'reserved_7',
    },
  },
];

test('golden vectors decode exactly as the contract specifies', () => {
  for (const v of GOLDEN_VECTORS) {
    const bytes = b64ToBytes(v.b64);
    assert.strictEqual(bytes.length, 8, v.name + ': fixture must be 8 bytes');
    assert.deepStrictEqual(decodeOk(bytes), v.data, v.name);
  }
});

test('golden vectors re-encode to the identical bytes', () => {
  for (const v of GOLDEN_VECTORS) {
    const enc = encodeUplink({ data: v.data });
    assert.deepStrictEqual(enc.errors, [], v.name);
    assert.strictEqual(Buffer.from(enc.bytes).toString('base64'), v.b64, v.name);
    assert.strictEqual(enc.fPort, 1, v.name);
  }
});

/* ------------------------------------------------------------------- round-tripping */

test('encode -> decode round-trips across the representable range', () => {
  const cases = [
    { pipe_c: 0, ambient_c: 0, battery_mv: 0, uplink_reason: 'heartbeat' },
    { pipe_c: -327.68, ambient_c: 327.67, battery_mv: 65535, uplink_reason: 'install_probe' },
    { pipe_c: 327.67, ambient_c: -327.68, battery_mv: 1, uplink_reason: 'boot' },
    { pipe_c: 11.25, ambient_c: 18.4, battery_mv: 3410, uplink_reason: 'threshold' },
    { pipe_c: -0.01, ambient_c: 0.01, battery_mv: 3600, uplink_reason: 'heartbeat' },
    { pipe_c: 18.39, ambient_c: 20.45, battery_mv: 2998, uplink_reason: 'heartbeat' },
    { pipe_c: -42.99, ambient_c: 7.07, battery_mv: 3012, uplink_reason: 'boot' },
  ];
  for (const base of cases) {
    for (const flags of allFlagCombinations()) {
      const data = Object.assign({}, base, { flags });
      const enc = encodeUplink({ data });
      assert.deepStrictEqual(enc.errors, []);
      const dec = decodeOk(enc.bytes);
      assert.strictEqual(dec.pipe_c, base.pipe_c);
      assert.strictEqual(dec.ambient_c, base.ambient_c);
      assert.strictEqual(dec.battery_mv, base.battery_mv);
      assert.strictEqual(dec.uplink_reason, base.uplink_reason);
      assert.deepStrictEqual(dec.flags, flags);
      /* decode -> encode -> decode must be a fixed point, not merely close. */
      const again = encodeUplink({ data: dec });
      assert.deepStrictEqual(again.bytes, enc.bytes);
    }
  }
});

test('every byte pattern decodes without throwing and re-encodes to itself', () => {
  /* 2^64 patterns is not an option, so sweep the structurally interesting ones: both sign
   * bits, both temperature extremes, all flag bytes including reserved bits, and every
   * possible uplink_reason byte. A frame that decodes but does not re-encode to the same
   * bytes means the decoder is losing information. */
  const halves = [0x0000, 0x0001, 0x7fff, 0x8000, 0xffff, 0xfd35, 0x0465, 0x0730];
  for (const p of halves) {
    for (const a of halves) {
      for (const flags of [0x00, 0x07, 0x05, 0xff, 0xf8]) {
        for (const reason of [0, 1, 2, 3, 4, 127, 255]) {
          const bytes = [p >> 8, p & 0xff, a >> 8, a & 0xff, 0x0d, 0x52, flags, reason];
          const r = decodeUplink({ bytes, fPort: 1 });
          assert.deepStrictEqual(r.errors, []);
          const enc = encodeUplink({ data: r.data });
          assert.deepStrictEqual(enc.errors, []);
          /* Reserved flag bits are not represented in §3 and so cannot survive a re-encode;
           * mask them out of the comparison, and assert they were warned about instead. */
          const expected = bytes.slice();
          expected[6] = flags & 0x07;
          assert.deepStrictEqual(enc.bytes, expected, 'bytes ' + bytes.join(','));
          if (flags & 0xf8) {
            assert.ok(r.warnings.some((w) => /reserved flag bits/.test(w)));
          }
        }
      }
    }
  }
});

/* --------------------------------------------------------- negative temperatures */

test('int16 sign extension: -7.15 survives, and 0xFD35 is not 64821', () => {
  /* The classic bug: reading the two bytes as unsigned turns a -7.11 °C pipe into +648 °C,
   * which reads as a sensor fault instead of the leak it is. */
  const bytes = [0xfd, 0x35, 0x00, 0x00, 0x0d, 0x52, 0x01, 0x00];
  const d = decodeOk(bytes);
  assert.strictEqual(d.pipe_c, -7.15);
  assert.strictEqual(d.delta_c, -7.15);

  /* Every value with the sign bit set, swept byte by byte through the low half. */
  for (let raw = -32768; raw <= -1; raw += 7) {
    const u = raw & 0xffff;
    const d2 = decodeOk([u >> 8, u & 0xff, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]);
    assert.strictEqual(d2.pipe_c, raw / 100, 'raw ' + raw);
    assert.ok(d2.pipe_c < 0, 'raw ' + raw + ' decoded non-negative: ' + d2.pipe_c);
  }
  /* And the boundary either side of zero. */
  assert.strictEqual(decodeOk([0xff, 0xff, 0, 0, 0, 0, 0, 0]).pipe_c, -0.01);
  assert.strictEqual(decodeOk([0x00, 0x00, 0, 0, 0, 0, 0, 0]).pipe_c, 0);
  assert.strictEqual(decodeOk([0x80, 0x00, 0, 0, 0, 0, 0, 0]).pipe_c, -327.68);
  assert.strictEqual(decodeOk([0x7f, 0xff, 0, 0, 0, 0, 0, 0]).pipe_c, 327.67);
});

test('ambient_c is sign-extended independently of pipe_c', () => {
  /* A pipe above freezing and an ambient below it is the normal state of an uninsulated main
   * in an unheated crawlspace in February, so this is not an exotic case. */
  const d = decodeOk([0x01, 0xf4, 0xfe, 0x0c, 0x0d, 0x00, 0x01, 0x00]);
  assert.strictEqual(d.pipe_c, 5.0);
  assert.strictEqual(d.ambient_c, -5.0);
  assert.strictEqual(d.delta_c, 10.0);
});

/* --------------------------------------------------------------------- delta_c */

test('delta_c = pipe_c - ambient_c, computed in integer centi-degrees', () => {
  const cases = [
    [1125, 1840, -7.15], // the contract's own §3 example; float subtraction gives
    //                      -7.149999999999999 here, which is the whole reason for
    //                      doing the arithmetic before the divide
    [0, 0, 0],
    [-500, -1234, 7.34],
    [-1234, -500, -7.34],
    [2000, 2001, -0.01],
    [32767, -32768, 655.35], // widest possible delta; no int16 clamping on a derived field
    [-32768, 32767, -655.35],
    [349, 2041, -16.92],
  ];
  for (const [p, a, expected] of cases) {
    const pu = p & 0xffff;
    const au = a & 0xffff;
    const d = decodeOk([pu >> 8, pu & 0xff, au >> 8, au & 0xff, 0x0d, 0x52, 0x01, 0x00]);
    assert.strictEqual(d.delta_c, expected, p + ' - ' + a);
    /* Same number, to the cent, as the naive form -- just without the float dust. */
    assert.ok(Math.abs(d.delta_c - (d.pipe_c - d.ambient_c)) < 1e-9);
  }
});

test('delta_c is exact to the cent for every pipe value against a fixed ambient', () => {
  /* JSON.stringify of the decoded payload must not grow a tail of 9s, because the backend
   * stores and the dashboard prints this string. */
  for (let raw = -32768; raw <= 32767; raw += 13) {
    const u = raw & 0xffff;
    const d = decodeOk([u >> 8, u & 0xff, 0x07, 0x30, 0x0d, 0x52, 0x01, 0x00]);
    const s = JSON.stringify(d.delta_c);
    assert.ok(/^-?\d+(\.\d{1,2})?$/.test(s), 'raw ' + raw + ' gave delta_c ' + s);
  }
});

/* ----------------------------------------------------------------------- flags */

function allFlagCombinations() {
  const out = [];
  for (let m = 0; m < 8; m++) {
    out.push({
      install_verified: !!(m & 0x01),
      tamper: !!(m & 0x02),
      cold_warning: !!(m & 0x04),
    });
  }
  return out;
}

test('the full flags bitfield maps bit-for-bit', () => {
  for (let m = 0; m < 8; m++) {
    const d = decodeOk([0x04, 0x65, 0x07, 0x30, 0x0d, 0x52, m, 0x00]);
    assert.strictEqual(d.flags.install_verified, !!(m & 0x01), 'bit0 at flags=' + m);
    assert.strictEqual(d.flags.tamper, !!(m & 0x02), 'bit1 at flags=' + m);
    assert.strictEqual(d.flags.cold_warning, !!(m & 0x04), 'bit2 at flags=' + m);
    assert.deepStrictEqual(Object.keys(d.flags), ['install_verified', 'tamper', 'cold_warning']);
  }
});

test('reserved flag bits warn but never error, and never bleed into the named flags', () => {
  for (let hi = 1; hi < 32; hi++) {
    const flags = hi << 3; // bits 3-7 only
    const r = decodeUplink({ bytes: [0, 0, 0, 0, 0, 0, flags, 0], fPort: 1 });
    assert.deepStrictEqual(r.errors, []);
    assert.strictEqual(r.warnings.length, 1);
    assert.deepStrictEqual(r.data.flags, {
      install_verified: false,
      tamper: false,
      cold_warning: false,
    });
  }
});

/* --------------------------------------------------------------- uplink_reason */

test('every defined uplink_reason decodes to its contract name', () => {
  const expected = ['heartbeat', 'threshold', 'boot', 'install_probe'];
  expected.forEach((name, n) => {
    const r = decodeUplink({ bytes: [0, 0, 0, 0, 0, 0, 1, n], fPort: 1 });
    assert.strictEqual(r.data.uplink_reason, name, 'reason ' + n);
    assert.deepStrictEqual(r.warnings, [], 'reason ' + n + ' should not warn');
  });
  assert.deepStrictEqual(codec.UPLINK_REASONS, expected);
});

test('unknown uplink_reason decodes to reserved_<n> and does not throw', () => {
  /* §3 is explicit about this: a codec that throws takes the whole webhook down, so one
   * device running next year's firmware must not be able to blind the fleet. */
  for (let n = 4; n <= 255; n++) {
    let r;
    assert.doesNotThrow(() => {
      r = decodeUplink({ bytes: [0x04, 0x65, 0x07, 0x30, 0x0d, 0x52, 0x01, n], fPort: 1 });
    }, 'reason ' + n + ' threw');
    assert.deepStrictEqual(r.errors, [], 'reason ' + n + ' errored');
    assert.strictEqual(r.data.uplink_reason, 'reserved_' + n);
    /* The rest of the frame must still be usable -- that is the point of not throwing. */
    assert.strictEqual(r.data.delta_c, -7.15);
    assert.strictEqual(r.data.battery_mv, 3410);
    assert.ok(r.warnings.some((w) => /unknown uplink_reason/.test(w)));
  }
});

test('reserved_<n> re-encodes to the same reason byte', () => {
  const enc = encodeUplink({
    data: { pipe_c: 0, ambient_c: 0, battery_mv: 0, flags: {}, uplink_reason: 'reserved_200' },
  });
  assert.deepStrictEqual(enc.errors, []);
  assert.strictEqual(enc.bytes[7], 200);
});

/* ------------------------------------------------------ malformed input handling */

test('wrong payload length is an error, not an exception', () => {
  for (let n = 0; n <= 20; n++) {
    if (n === 8) continue;
    const bytes = new Array(n).fill(0x11);
    let r;
    assert.doesNotThrow(() => {
      r = decodeUplink({ bytes, fPort: 1 });
    }, 'length ' + n + ' threw');
    assert.strictEqual(r.errors.length, 1, 'length ' + n);
    assert.match(r.errors[0], /expected 8 bytes/);
    assert.deepStrictEqual(r.data, {}, 'length ' + n + ' must not half-decode');
  }
});

test('structurally broken input is an error, not an exception', () => {
  const bad = [
    undefined,
    null,
    {},
    { bytes: null },
    { bytes: 'BGUHMA1SAQA=' }, // base64 string instead of decoded bytes: a real mistake
    { bytes: [1, 2, 3, 4, 5, 6, 7, 300] },
    { bytes: [1, 2, 3, 4, 5, 6, 7, -1] },
    { bytes: [1, 2, 3, 4, 5, 6, 7, 1.5] },
    { bytes: [1, 2, 3, 4, 5, 6, 7, NaN] },
    { bytes: [1, 2, 3, 4, 5, 6, 7, null] },
  ];
  for (const input of bad) {
    let r;
    assert.doesNotThrow(() => {
      r = decodeUplink(input);
    }, JSON.stringify(input) + ' threw');
    assert.ok(r.errors.length > 0, JSON.stringify(input) + ' should have errored');
    assert.deepStrictEqual(r.data, {});
  }
  /* A 'BGUHMA1SAQA=' string has length 12, so it is caught as a length error only if the
   * element check runs first. Assert the message actually names the real problem. */
  assert.match(decodeUplink({ bytes: 'BGUHMA1SAQA=' }).errors[0], /array of 0\.\.255 integers/);
});

test('uplink on the wrong FPort errors; a missing FPort is tolerated', () => {
  const bytes = [0x04, 0x65, 0x07, 0x30, 0x0d, 0x52, 0x01, 0x00];
  assert.match(decodeUplink({ bytes, fPort: 2 }).errors[0], /unsupported FPort 2/);
  assert.deepStrictEqual(decodeUplink({ bytes }).errors, []);
  assert.deepStrictEqual(decodeUplink({ bytes, fPort: 1 }).errors, []);
});

test('out-of-range values are rejected by the encoder rather than silently wrapping', () => {
  const r = encodeUplink({ data: { pipe_c: 400, ambient_c: 0, battery_mv: 3000, uplink_reason: 'heartbeat' } });
  assert.strictEqual(r.errors.length, 1);
  assert.match(r.errors[0], /out of int16 centi-degree range/);
  assert.deepStrictEqual(r.bytes, []);

  for (const data of [
    { pipe_c: 0, ambient_c: 0, battery_mv: 70000, uplink_reason: 'heartbeat' },
    { pipe_c: 0, ambient_c: 0, battery_mv: -1, uplink_reason: 'heartbeat' },
    { pipe_c: NaN, ambient_c: 0, battery_mv: 0, uplink_reason: 'heartbeat' },
    { pipe_c: 0, ambient_c: 0, battery_mv: 0, uplink_reason: 'no_such_reason' },
    { pipe_c: 0, ambient_c: 0, battery_mv: 0 },
  ]) {
    const e = encodeUplink({ data });
    assert.ok(e.errors.length > 0, JSON.stringify(data));
    assert.deepStrictEqual(e.bytes, []);
  }
});

test('centi-degree rounding is half away from zero and symmetric', () => {
  const enc = (c) => encodeUplink({ data: { pipe_c: c, ambient_c: 0, battery_mv: 0, uplink_reason: 'heartbeat' } }).bytes;
  assert.strictEqual(((enc(0.005)[0] << 8) | enc(0.005)[1]) << 16 >> 16, 1);
  assert.strictEqual(((enc(-0.005)[0] << 8) | enc(-0.005)[1]) << 16 >> 16, -1);
  assert.strictEqual(((enc(1.004)[0] << 8) | enc(1.004)[1]) << 16 >> 16, 100);
  assert.strictEqual(((enc(-1.004)[0] << 8) | enc(-1.004)[1]) << 16 >> 16, -100);
});

/* -------------------------------------------------------------------- downlink */

test('downlink round-trips every command in §2', () => {
  const cases = [
    { command: 'nop', arg: 0, token: 0 },
    { command: 'valve_close', arg: 0, token: 1 },
    { command: 'valve_open', arg: 0, token: 65535 },
    { command: 'set_heartbeat', arg: 1, token: 4242 },
    { command: 'set_heartbeat', arg: 240, token: 0x0102 },
    { command: 'request_install_probe', arg: 0, token: 31337 },
  ];
  for (const data of cases) {
    const enc = encodeDownlink({ data });
    assert.deepStrictEqual(enc.errors, [], JSON.stringify(data));
    assert.strictEqual(enc.fPort, 2);
    assert.strictEqual(enc.bytes.length, 4);
    const dec = decodeDownlink({ bytes: enc.bytes, fPort: 2 });
    assert.deepStrictEqual(dec.errors, []);
    assert.deepStrictEqual(dec.data, data);
  }
});

test('downlink byte layout matches §2 exactly', () => {
  const enc = encodeDownlink({ data: { command: 'set_heartbeat', arg: 240, token: 0xbeef } });
  assert.deepStrictEqual(enc.bytes, [0x03, 0xf0, 0xbe, 0xef]);
  /* Integer command codes are accepted as well as names, because the backend stores the code. */
  assert.deepStrictEqual(encodeDownlink({ data: { command: 1, token: 7 } }).bytes, [0x01, 0x00, 0x00, 0x07]);
  assert.deepStrictEqual(decodeDownlink({ bytes: [0x02, 0x00, 0x00, 0x07] }).data, {
    command: 'valve_open',
    arg: 0,
    token: 7,
  });
});

test('downlink validation refuses what the contract forbids', () => {
  const bad = [
    { command: 'set_heartbeat', arg: 0, token: 1 }, // §2: minutes are 1..240
    { command: 'set_heartbeat', arg: 241, token: 1 },
    { command: 'valve_close', arg: 5, token: 1 }, // §2: arg must be 0 otherwise
    { command: 'valve_close', arg: 0, token: 65536 },
    { command: 'valve_close', arg: 0, token: -1 },
    { command: 'valve_close', arg: 0, token: 1.5 },
    { command: 'fire_the_missiles', arg: 0, token: 0 },
    { arg: 0, token: 0 },
  ];
  for (const data of bad) {
    let r;
    assert.doesNotThrow(() => {
      r = encodeDownlink({ data });
    });
    assert.ok(r.errors.length > 0, JSON.stringify(data));
    assert.deepStrictEqual(r.bytes, []);
  }
  /* Defaults: arg and token are optional and default to 0. */
  assert.deepStrictEqual(encodeDownlink({ data: { command: 'nop' } }).bytes, [0, 0, 0, 0]);
});

test('malformed downlink decodes gracefully', () => {
  for (const input of [undefined, {}, { bytes: [] }, { bytes: [1, 2, 3] }, { bytes: [1, 2, 3, 4, 5] }]) {
    let r;
    assert.doesNotThrow(() => {
      r = decodeDownlink(input);
    });
    assert.ok(r.errors.length > 0, JSON.stringify(input));
  }
  assert.match(decodeDownlink({ bytes: [0, 0, 0, 0], fPort: 1 }).errors[0], /unsupported FPort 1/);
  const unknown = decodeDownlink({ bytes: [9, 0, 0, 0] });
  assert.deepStrictEqual(unknown.errors, []);
  assert.strictEqual(unknown.data.command, 'reserved_9');
});

test('the install-probe echo token path described in §2 works end to end', () => {
  /* §2: the device mirrors the downlink token in the next uplink's uplink_reason=3 frame.
   * Nothing in the codec enforces that -- it is the firmware's job -- but the two halves have
   * to be expressible, and this is the test that says so. */
  const down = encodeDownlink({ data: { command: 'request_install_probe', token: 0x1234 } });
  assert.deepStrictEqual(down.bytes, [0x04, 0x00, 0x12, 0x34]);
  const up = encodeUplink({
    data: {
      pipe_c: 14.2,
      ambient_c: 19.0,
      battery_mv: 3590,
      flags: { install_verified: true, tamper: false, cold_warning: false },
      uplink_reason: 'install_probe',
    },
  });
  assert.strictEqual(decodeUplink({ bytes: up.bytes, fPort: 1 }).data.uplink_reason, 'install_probe');
});
