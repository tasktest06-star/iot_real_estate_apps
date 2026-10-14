"""Tests for the fleet simulator.

    python3 -m pytest simulator/ -q          # from the prototype root

Three things are being tested, in descending order of how much they are worth:

 1. **The §1 byte layout agrees with the Node codec.** `GOLDEN_VECTORS` below is copied
    verbatim from `../codec/index.test.js`, so two independent implementations of that table
    are checked against the same expected bytes. `test_node_codec_decodes_simulator_payloads`
    goes further and shells out to the real codec with real simulator output. A byte-level
    disagreement between components is the failure mode the contract warns about, and it is
    invisible to any test that only exercises one side.
 2. **Reproducibility**, because `--seed` is worthless otherwise and every downstream
    comparison of two detector versions depends on it.
 3. **The physics**, as falsifiable statements about the generated data: a slow leak must
    dwell, appliance use must not, a stuck sensor must actually stop changing. These are
    assertions about *this* model and not about real pipes; see README.md.

Deliberately not tested: anything requiring a network socket. INTERFACES.md §10 requires the
suite to pass offline, so the `--post` path is exercised with an injected `urlopen`.
"""

import base64
import collections
import io
import json
import os
import shutil
import struct
import subprocess

import pytest

from . import fleet, scenarios
from . import __main__ as cli

# ------------------------------------------------------------------- shared golden vectors

# Copied verbatim from ../codec/index.test.js. If you change a byte here, change it there.
GOLDEN_VECTORS = [
    (
        "contract §3 worked example",
        "BGUHMA1SAQA=",
        {
            "pipe_c": 11.25,
            "ambient_c": 18.4,
            "delta_c": -7.15,
            "battery_mv": 3410,
            "flags": {"install_verified": True, "tamper": False, "cold_warning": False},
            "uplink_reason": "heartbeat",
        },
    ),
    (
        "contract §4 example frm_payload",
        "BLEAAQ0yAQA=",
        {
            "pipe_c": 12.01,
            "ambient_c": 0.01,
            "delta_c": 12.0,
            "battery_mv": 3378,
            "flags": {"install_verified": True, "tamper": False, "cold_warning": False},
            "uplink_reason": "heartbeat",
        },
    ),
    (
        "freeze: negative pipe, cold_warning, threshold uplink",
        "/9YA0g0BBQE=",
        {
            "pipe_c": -0.42,
            "ambient_c": 2.1,
            "delta_c": -2.52,
            "battery_mv": 3329,
            "flags": {"install_verified": True, "tamper": False, "cold_warning": True},
            "uplink_reason": "threshold",
        },
    ),
    (
        "both channels negative, tampered, boot",
        "+y7+DA4UAwI=",
        {
            "pipe_c": -12.34,
            "ambient_c": -5.0,
            "delta_c": -7.34,
            "battery_mv": 3604,
            "flags": {"install_verified": True, "tamper": True, "cold_warning": False},
            "uplink_reason": "boot",
        },
    ),
    (
        "unverified install, unknown reason 7",
        "B9AH0Qy6AAc=",
        {
            "pipe_c": 20.0,
            "ambient_c": 20.01,
            "delta_c": -0.01,
            "battery_mv": 3258,
            "flags": {"install_verified": False, "tamper": False, "cold_warning": False},
            "uplink_reason": "reserved_7",
        },
    ),
]

START_S = cli.parse_start("2027-01-15T00:00:00Z")
SEED = 20261014

CODEC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "codec")


# --------------------------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def small_fleet():
    """The smallest fleet that still covers all ten §6 scenarios, over enough days for a
    freeze event to run its course."""
    return scenarios.build_fleet(n_devices=8, start_s=START_S, days=12, seed=SEED)


@pytest.fixture(scope="module")
def frames(small_fleet):
    """Every frame of `small_fleet`, grouped by device, so the physics tests share one run."""
    out = collections.OrderedDict()
    for plan in small_fleet.plans:
        out[plan.device.device_id] = (plan, list(fleet.simulate_device(plan, START_S, 12)))
    return out


def _decode(env):
    return fleet.decode_uplink(base64.b64decode(env["uplink_message"]["frm_payload"]))


# -------------------------------------------------------------------- §1 encoding agreement


def test_golden_vectors_encode_to_the_same_bytes_as_the_node_codec():
    for name, b64, data in GOLDEN_VECTORS:
        raw = fleet.encode_uplink(
            pipe_c=data["pipe_c"],
            ambient_c=data["ambient_c"],
            battery_mv=data["battery_mv"],
            install_verified=data["flags"]["install_verified"],
            tamper=data["flags"]["tamper"],
            cold_warning=data["flags"]["cold_warning"],
            uplink_reason=(
                fleet.UPLINK_REASONS.index(data["uplink_reason"])
                if data["uplink_reason"] in fleet.UPLINK_REASONS
                else int(data["uplink_reason"].split("_")[1])
            ),
        )
        assert len(raw) == 8, name
        assert base64.b64encode(raw).decode() == b64, name


def test_golden_vectors_decode_to_the_contract_shape():
    for name, b64, data in GOLDEN_VECTORS:
        assert fleet.decode_uplink(base64.b64decode(b64)) == data, name


def test_delta_is_exact_to_the_cent_including_the_contract_example():
    """§3's own example is the trap: 11.25 - 18.4 in floating point is -7.149999999999999.
    Subtracting in centi-degrees before dividing is what makes the JSON match the contract."""
    d = fleet.decode_uplink(fleet.encode_uplink(11.25, 18.40, 3410, True, False, False, 0))
    assert d["delta_c"] == -7.15
    assert repr(d["delta_c"]) == "-7.15"
    assert d["pipe_c"] - d["ambient_c"] != -7.15  # the naive form really is wrong
    for raw_pipe in range(-32768, 32768, 997):
        d = fleet.decode_uplink(struct.pack(">hhHBB", raw_pipe, 1840, 3410, 1, 0))
        assert abs(d["delta_c"] * 100 - (raw_pipe - 1840)) < 1e-6


def test_negative_temperatures_survive_int16_sign_extension():
    for c in (-0.01, -7.15, -12.34, -40.0, -55.5, -327.68):
        d = fleet.decode_uplink(fleet.encode_uplink(c, 0.0, 3400, True, False, True, 1))
        assert d["pipe_c"] == c, c
        assert d["delta_c"] == c, c
    # Independent channels: a pipe above freezing with ambient below it is February in a
    # crawlspace, not an exotic case.
    d = fleet.decode_uplink(fleet.encode_uplink(5.0, -5.0, 3400, True, False, False, 0))
    assert (d["pipe_c"], d["ambient_c"], d["delta_c"]) == (5.0, -5.0, 10.0)


def test_centi_rounds_half_away_from_zero_and_saturates():
    assert fleet.centi(0.005) == 1
    assert fleet.centi(-0.005) == -1
    assert fleet.centi(1.004) == 100
    assert fleet.centi(-1.004) == -100
    # Saturating, not wrapping: a wrapped value decodes to a plausible negative temperature.
    assert fleet.centi(400.0) == fleet.INT16_MAX
    assert fleet.centi(-400.0) == fleet.INT16_MIN


def test_flags_bitfield_round_trips_every_combination():
    for mask in range(8):
        iv, tp, cw = bool(mask & 1), bool(mask & 2), bool(mask & 4)
        raw = fleet.encode_uplink(10.0, 12.0, 3400, iv, tp, cw, 0)
        assert raw[6] == mask
        assert fleet.decode_uplink(raw)["flags"] == {
            "install_verified": iv,
            "tamper": tp,
            "cold_warning": cw,
        }


def test_every_uplink_reason_including_unknown():
    for n, name in enumerate(fleet.UPLINK_REASONS):
        assert fleet.decode_uplink(fleet.encode_uplink(0, 0, 0, False, False, False, n))["uplink_reason"] == name
    for n in (4, 5, 99, 255):
        d = fleet.decode_uplink(fleet.encode_uplink(0, 0, 0, False, False, False, n))
        assert d["uplink_reason"] == "reserved_%d" % n


def test_wrong_payload_length_is_rejected():
    for n in (0, 7, 9, 16):
        with pytest.raises(ValueError):
            fleet.decode_uplink(b"\x00" * n)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_node_codec_decodes_simulator_payloads(frames):
    """The cross-implementation check: run the real Node codec over real simulator output.

    This is the most valuable test in either suite. The golden vectors prove the two
    implementations agree on five hand-written frames; this proves they agree on a few
    hundred frames that the simulator actually produced, including the negative, the railed
    and the unknown-reason ones that the physics generates on its own.
    """
    payloads = []
    expected = []
    for _plan, dev_frames in frames.values():
        for frame in dev_frames[:: max(1, len(dev_frames) // 40)]:
            env = fleet.envelope(_plan.device, frame)
            payloads.append(env["uplink_message"]["frm_payload"])
            expected.append(fleet.decode_uplink(frame.raw))
    assert len(payloads) > 100

    script = (
        "const codec = require(%s);\n"
        "const out = JSON.parse(require('fs').readFileSync(0, 'utf8')).map((b64) => {\n"
        "  const r = codec.decodeUplink({ bytes: Array.from(Buffer.from(b64, 'base64')), fPort: 1 });\n"
        "  if (r.errors.length) throw new Error(b64 + ': ' + r.errors.join(','));\n"
        "  return r.data;\n"
        "});\n"
        "process.stdout.write(JSON.stringify(out));\n"
    ) % json.dumps(os.path.join(CODEC_DIR, "index.js"))

    proc = subprocess.run(
        ["node", "-e", script],
        input=json.dumps(payloads),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    decoded = json.loads(proc.stdout)
    assert decoded == expected, "Node codec and Python encoder disagree on §1"


# ------------------------------------------------------------------------- reproducibility


def _run_lines(devices=4, days=3, seed=SEED, **kw):
    spec = scenarios.build_fleet(n_devices=devices, start_s=START_S, days=days, seed=seed, **kw)
    return [json.dumps(e, sort_keys=True) for e in cli.stream_envelopes(spec, dup_rate=0.02)]


def test_same_seed_gives_byte_identical_output():
    assert _run_lines() == _run_lines()


def test_different_seed_gives_different_output():
    a, b = _run_lines(), _run_lines(seed=SEED + 1)
    assert a != b
    # Not merely reordered: the actual readings have to differ.
    assert set(a) != set(b)


def test_reproducible_across_processes(tmp_path):
    """`derive_seed` must not depend on PYTHONHASHSEED. `hash()` on a str is salted per
    process, so a simulator built on it would be reproducible only within one run -- and the
    failure would be invisible until someone compared two days' output."""
    assert fleet.derive_seed("device", 20261014, 3) == 3731934058546767616
    out = []
    for salt in ("0", "1", "random"):
        env = dict(os.environ, PYTHONHASHSEED=salt)
        path = str(tmp_path / ("fleet-%s.jsonl" % salt))
        proc = subprocess.run(
            ["python3", "-m", "simulator", "--devices", "3", "--days", "2", "--out", path, "--quiet"],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert proc.returncode == 0, proc.stderr
        with open(path, encoding="utf-8") as handle:
            out.append(handle.read())
    assert out[0] == out[1] == out[2]


def test_simulating_a_device_twice_gives_identical_frames():
    """Running a device forward must not consume anything. `simulate_device` used to deplete
    `Device.battery_mah_used` in place, so a second pass over the same fleet -- which is
    exactly what the CLI does to print its summary after writing the file -- reported
    different voltages from the ones it had just written. A simulator whose output depends on
    how many times you have looked at it is not reproducible, and `--seed` cannot fix it."""
    spec = scenarios.build_fleet(n_devices=3, start_s=START_S, days=4, seed=SEED)
    for plan in spec.plans:
        first = [(f.ts_s, f.f_cnt, f.raw, f.scenario) for f in fleet.simulate_device(plan, START_S, 4)]
        second = [(f.ts_s, f.f_cnt, f.raw, f.scenario) for f in fleet.simulate_device(plan, START_S, 4)]
        assert first == second, plan.device.device_id
        assert first


def test_summary_describes_the_file_that_was_written():
    """The CLI writes from one pass and summarises from a second. Both passes have to agree,
    or the numbers on stderr describe a run that does not exist."""
    spec = scenarios.build_fleet(n_devices=4, start_s=START_S, days=4, seed=SEED)
    first = [json.dumps(e, sort_keys=True) for e in cli.stream_envelopes(spec)]
    second = [json.dumps(e, sort_keys=True) for e in cli.stream_envelopes(spec)]
    assert first == second
    assert len(first) > 300


def test_fleet_composition_is_stable_under_a_different_run_length():
    """Device properties must depend on the seed and the index, not on how many days are
    being generated, so a 3-day debugging run describes the same fleet as a 30-day one."""
    short = scenarios.build_fleet(n_devices=6, start_s=START_S, days=3, seed=SEED)
    long = scenarios.build_fleet(n_devices=6, start_s=START_S, days=30, seed=SEED)
    for a, b in zip(short.plans, long.plans):
        assert a.device.archetype.name == b.device.archetype.name
        assert a.device.install_verified == b.device.install_verified
        assert a.device.occupants == b.device.occupants
        assert round(a.device.link_loss_p, 9) == round(b.device.link_loss_p, 9)


# ----------------------------------------------------------------------- §6 scenario cover


def test_all_ten_contract_scenarios_are_generated(small_fleet):
    counts = scenarios.scenario_counts(small_fleet)
    missing = [name for name, n in counts.items() if n == 0]
    assert not missing, "missing §6 scenarios: %s (got %s)" % (missing, counts)
    assert set(counts) == set(fleet.SCENARIOS)
    assert sorted(fleet.SCENARIOS) == sorted(
        [
            "quiet",
            "appliance_use",
            "shower_morning",
            "slow_leak",
            "fast_leak",
            "freeze_event",
            "sensor_fault_stuck",
            "sensor_fault_noisy",
            "install_unverified",
            "cold_basement_winter",
        ]
    )


def test_coverage_holds_at_the_documented_minimum_fleet():
    spec = scenarios.build_fleet(
        n_devices=scenarios.MIN_DEVICES_FOR_FULL_COVERAGE, start_s=START_S, days=4, seed=SEED
    )
    counts = scenarios.scenario_counts(spec)
    assert not [name for name, n in counts.items() if n == 0], counts
    assert not spec.notes


def test_a_fleet_too_small_for_coverage_says_so():
    spec = scenarios.build_fleet(n_devices=2, start_s=START_S, days=4, seed=SEED)
    assert any("below the" in note for note in spec.notes)


def test_labels_agree_with_the_contract_tables():
    for name in fleet.SCENARIOS:
        assert name in fleet.EXPECTED_VERDICT
        assert name in fleet.TRUE_EVENT
        # §5 VERDICTS is the closed set the detector may answer with.
        assert fleet.EXPECTED_VERDICT[name] in (
            "quiet",
            "watch",
            "leak_suspected",
            "freeze_risk",
            "sensor_fault",
            "install_unverified",
        )
    # §5 requirement 2 and risk 4: an unverified install is never a leak and never a true
    # event, however leak-like its readings look.
    assert fleet.TRUE_EVENT["install_unverified"] is False
    assert fleet.EXPECTED_VERDICT["install_unverified"] == "install_unverified"
    # The nastiest false positive must be labelled benign, or the whole exercise is circular.
    assert fleet.TRUE_EVENT["cold_basement_winter"] is False
    assert fleet.EXPECTED_VERDICT["cold_basement_winter"] == "quiet"
    assert fleet.TRUE_EVENT["slow_leak"] and fleet.TRUE_EVENT["fast_leak"]


# ---------------------------------------------------------------------------- §4 envelopes


def test_envelope_matches_the_contract_shape(frames):
    checked = 0
    for plan, dev_frames in frames.values():
        for frame in dev_frames[:80]:
            env = fleet.envelope(plan.device, frame)
            assert set(env) == {"end_device_ids", "received_at", "uplink_message", "_label"}
            ids = env["end_device_ids"]
            assert ids["device_id"] == plan.device.device_id
            assert len(ids["dev_eui"]) == 16 and int(ids["dev_eui"], 16) >= 0
            assert env["received_at"].endswith("Z") and len(env["received_at"]) == 20

            msg = env["uplink_message"]
            assert msg["f_port"] == 1
            assert isinstance(msg["f_cnt"], int) and msg["f_cnt"] >= 0
            raw = base64.b64decode(msg["frm_payload"])
            assert len(raw) == 8, "frm_payload must be base64 of the 8 raw §1 bytes"
            assert raw == frame.raw
            assert msg["rx_metadata"], "a received frame was heard by at least one gateway"
            for rx in msg["rx_metadata"]:
                assert rx["gateway_ids"]["gateway_id"].startswith("gw-")
                assert -140 <= rx["rssi"] <= -40
                assert -25.0 <= rx["snr"] <= 15.0
            lora = msg["settings"]["data_rate"]["lora"]
            # US915 uplinks at 125 kHz stop at SF10 (DR0); SF11/SF12 do not exist there.
            assert lora["spreading_factor"] in (7, 8, 9, 10)
            assert lora["bandwidth"] == 125000

            label = env["_label"]
            assert {"scenario", "is_true_event", "event_start_s", "device_id"} <= set(label)
            assert label["scenario"] in fleet.SCENARIOS
            assert label["device_id"] == plan.device.device_id
            assert label["is_true_event"] is fleet.TRUE_EVENT[label["scenario"]]
            if label["scenario"] != "quiet":
                assert isinstance(label["event_start_s"], int)
            checked += 1
    assert checked > 200


def test_label_can_be_omitted(small_fleet):
    env = next(iter(cli.stream_envelopes(small_fleet, include_label=False)))
    assert "_label" not in env


def test_payload_decodes_to_values_consistent_with_the_label(frames):
    for plan, dev_frames in frames.values():
        for frame in dev_frames[:120]:
            env = fleet.envelope(plan.device, frame)
            decoded = _decode(env)
            assert decoded["delta_c"] == env["_label"]["delta_c"]
            assert decoded["flags"]["install_verified"] == plan.device.install_verified
            assert 0 <= decoded["battery_mv"] <= 65535


def test_battery_is_plausible_and_colder_devices_read_lower(frames):
    """Risk 7: battery life in a cold basement. The point of modelling pulse sag is that the
    fleet view must not read a cold device as a dying one."""
    worst = {}
    for plan, dev_frames in frames.values():
        mvs = [fleet.decode_uplink(f.raw)["battery_mv"] for f in dev_frames]
        assert mvs
        assert all(2500 <= mv <= 3700 for mv in mvs), (plan.device.device_id, min(mvs), max(mvs))
        worst[plan.device.device_id] = min(mvs)
    assert min(worst.values()) < 3500, "no device in the fleet shows any battery wear"


# --------------------------------------------------------------- packet loss and f_cnt gaps


def test_f_cnt_is_strictly_increasing_and_has_gaps(frames):
    """§1 leaves sequencing to the network server, so loss shows up as `f_cnt` gaps rather
    than as missing time. §7's never-reporting metric has nothing to measure without them."""
    total_gaps = 0
    for plan, dev_frames in frames.values():
        counts = [f.f_cnt for f in dev_frames]
        assert counts == sorted(counts)
        assert len(set(counts)) == len(counts)
        assert counts[0] == 0, "the boot frame is forced through so the fleet is discoverable"
        total_gaps += (counts[-1] + 1) - len(counts)
    assert total_gaps > 0, "no packet loss was simulated at all"


def test_every_device_appears_at_least_once(small_fleet, frames):
    assert len(frames) == len(small_fleet.plans)
    for plan, dev_frames in frames.values():
        assert dev_frames, plan.device.device_id


def test_some_devices_stop_reporting_before_day_seven():
    """Risk 4's leading indicator is ">10% of shipped units never reporting 7 days out", so
    the dataset has to contain that tail or `pct_never_reporting` is untestable."""
    spec = scenarios.build_fleet(n_devices=20, start_s=START_S, days=10, seed=SEED)
    day7 = START_S + 7 * fleet.SECONDS_PER_DAY
    dark = 0
    for plan in spec.plans:
        last = None
        for frame in fleet.simulate_device(plan, START_S, 10):
            last = frame.ts_s
        if last is not None and last < day7:
            dark += 1
    assert dark >= 1
    assert dark <= len(spec.plans) // 2, "a majority-dark fleet is a bug, not a failure tail"


def test_duplicate_envelopes_are_emitted_for_the_idempotency_requirement(small_fleet):
    """§4: duplicate `(dev_eui, f_cnt)` must be idempotent at the backend. The stream has to
    contain duplicates or that requirement is never exercised."""
    seen = collections.Counter()
    for env in cli.stream_envelopes(small_fleet, dup_rate=0.05):
        seen[(env["end_device_ids"]["dev_eui"], env["uplink_message"]["f_cnt"])] += 1
    assert max(seen.values()) >= 2
    assert sum(1 for v in seen.values() if v > 1) < len(seen) // 4


def test_stream_is_chronological(small_fleet):
    last = ""
    for env in cli.stream_envelopes(small_fleet):
        assert env["received_at"] >= last
        last = env["received_at"]


# ------------------------------------------------------------------------------- physics


def _by_scenario(frames):
    out = collections.defaultdict(list)
    for _plan, dev_frames in frames.values():
        for frame in dev_frames:
            out[frame.scenario].append(frame)
    return out


def test_a_leak_is_a_persistent_negative_offset(frames):
    """The product's whole premise. A leak draws cold ground water continuously, so the
    offset is there in nearly every sample; the depth is not what distinguishes it."""
    groups = _by_scenario(frames)
    for kind in ("slow_leak", "fast_leak"):
        deltas = [f.delta_c for f in groups[kind]]
        assert len(deltas) > 50, kind
        negative = sum(1 for d in deltas if d < -0.5) / len(deltas)
        assert negative > 0.85, "%s is only negative in %.0f%% of samples" % (kind, 100 * negative)
    slow = sorted(f.delta_c for f in groups["slow_leak"])
    fast = sorted(f.delta_c for f in groups["fast_leak"])
    assert slow[len(slow) // 2] < -1.0
    assert fast[len(fast) // 2] < slow[len(slow) // 2], "a fast leak must be deeper than a slow one"


def test_appliance_use_is_deep_but_brief(frames):
    """The nuisance problem, as a falsifiable statement: appliance excursions reach the same
    depth as a leak -- so depth cannot be the discriminator -- but they do not dwell."""
    groups = _by_scenario(frames)
    appliance = sorted(f.delta_c for f in groups["appliance_use"])
    leak = sorted(f.delta_c for f in groups["slow_leak"])
    assert appliance[int(len(appliance) * 0.03)] < leak[len(leak) // 2], (
        "appliance use never gets as deep as a slow leak, which would make the nuisance "
        "problem artificially easy"
    )

    def longest_run(kind):
        """Longest unbroken stretch of samples sitting well below the device's *own* quiet
        baseline. Measured per device and relative to its baseline, because an absolute
        threshold is confounded by exactly the thing §5 requirement 5 is about: a cold
        basement already sits 1.5 K low, so every one of its samples clears a fixed -1 K
        bar whether or not anything is happening."""
        best = 0
        for plan, dev_frames in frames.values():
            quiet = sorted(
                f.delta_c for f in dev_frames if f.scenario in ("quiet", "cold_basement_winter")
            )
            if not quiet:
                continue
            floor = quiet[len(quiet) // 2] - 1.5
            run = 0
            for frame in dev_frames:
                if frame.scenario == kind and frame.delta_c < floor:
                    run += 1
                    best = max(best, run)
                else:
                    run = 0
        return best

    # At a 15-minute heartbeat a draw plus its thermal tail spans a handful of samples; a leak
    # spans the rest of the run. If these were comparable there would be nothing to detect,
    # and if the gap were enormous the problem would be trivial. It is neither.
    appliance_run = longest_run("appliance_use")
    leak_run = longest_run("slow_leak")
    assert 2 <= appliance_run <= 12, appliance_run
    assert leak_run > 3 * appliance_run, (leak_run, appliance_run)


def test_shower_morning_repeats_at_the_same_time_of_day(frames):
    """`shower_morning` is the hardest nuisance: long enough to saturate the pipe, and daily.
    A detector that ignores time of day will fire on it every weekday."""
    groups = _by_scenario(frames)
    showers = groups["shower_morning"]
    assert len(showers) > 20
    hours = collections.Counter((f.ts_s % 86400) // 3600 for f in showers)
    top = sum(n for _h, n in hours.most_common(4))
    assert top / len(showers) > 0.7, "showers are not concentrated in a few hours: %s" % hours
    deep = sorted(f.delta_c for f in showers)
    assert deep[len(deep) // 2] < -1.0, "a saturating draw should move the pipe a long way"


def test_a_freeze_event_is_caught_on_absolute_pipe_temperature(frames):
    """§5 requirement 4 and application A4. During a freeze both channels fall together, so
    `delta_c` stays near zero and only `pipe_c` carries the warning."""
    groups = _by_scenario(frames)
    freeze = groups["freeze_event"]
    assert len(freeze) > 10
    pipes = [fleet.decode_uplink(f.raw)["pipe_c"] for f in freeze]
    assert min(pipes) < fleet.FREEZE_LABEL_C
    assert min(pipes) < 2.0
    # The flag has to be set when the pipe is cold, because that is what §1 bit2 is for.
    for f in freeze:
        d = fleet.decode_uplink(f.raw)
        if d["pipe_c"] < fleet.FIRMWARE_COLD_WARNING_C:
            assert d["flags"]["cold_warning"], d
    deltas = sorted(f.delta_c for f in freeze)
    assert deltas[len(deltas) // 2] > -2.5, "a freeze is not a differential signal"


def test_a_stuck_sensor_stops_changing_bit_for_bit(frames):
    """A hung I2C bus or a one-shot conversion never re-armed: the driver returns the last
    good read forever. Ambient keeps moving, so a *fake* delta grows -- which is why this has
    to be classified as a fault and not as a slowly worsening leak."""
    groups = _by_scenario(frames)
    stuck = [f for f in groups["sensor_fault_stuck"]]
    assert len(stuck) > 30
    by_device = collections.defaultdict(list)
    for _plan, dev_frames in frames.values():
        for frame in dev_frames:
            if frame.scenario == "sensor_fault_stuck":
                by_device[_plan.device.device_id].append(frame)
    assert by_device
    for dev_id, dev_frames in by_device.items():
        pipes = {fleet.decode_uplink(f.raw)["pipe_c"] for f in dev_frames}
        assert len(pipes) == 1, "%s: a stuck sensor reported %d distinct values" % (dev_id, len(pipes))
        ambients = {fleet.decode_uplink(f.raw)["ambient_c"] for f in dev_frames}
        assert len(ambients) > 5, "%s: ambient should keep moving while pipe is stuck" % dev_id
        deltas = [f.delta_c for f in dev_frames]
        assert max(deltas) - min(deltas) > 1.0, "the fake delta should drift with ambient"


def test_a_noisy_sensor_steps_faster_than_thermal_mass_allows(frames):
    """§5 requirement 3: physically implausible readings are a sensor fault, not a leak. A
    pipe full of water cannot move 7 K in five minutes, and a railed sensor reports values
    outside the -40..80 °C band the contract calls implausible."""
    by_device = collections.defaultdict(list)
    for plan, dev_frames in frames.values():
        for frame in dev_frames:
            if frame.scenario == "sensor_fault_noisy":
                by_device[plan.device.device_id].append(frame)
    assert by_device
    saw_impossible_step = False
    saw_out_of_range = False
    for dev_frames in by_device.values():
        pipes = [fleet.decode_uplink(f.raw)["pipe_c"] for f in dev_frames]
        steps = [abs(b - a) for a, b in zip(pipes, pipes[1:])]
        if steps and max(steps) > 7.0:
            saw_impossible_step = True
        if any(p < -40.0 or p > 80.0 for p in pipes):
            saw_out_of_range = True
    assert saw_impossible_step
    assert saw_out_of_range
    # §1 bit1: a loose clamp is often what "noisy" means, and tamper must be reachable.
    assert any(
        fleet.decode_uplink(f.raw)["flags"]["tamper"]
        for dev_frames in by_device.values()
        for f in dev_frames
    )


def test_an_unverified_install_is_flagged_on_every_frame_and_never_a_leak(frames):
    """§5 requirement 2 and risk 4: do not count unverified installs. The readings are
    deliberately leak-like, so the only thing stopping a false alert is the flag."""
    found = False
    for plan, dev_frames in frames.values():
        if plan.device.install_verified:
            continue
        found = True
        for frame in dev_frames:
            assert fleet.decode_uplink(frame.raw)["flags"]["install_verified"] is False
            assert frame.scenario == "install_unverified"
            assert frame.is_true_event is False
            assert frame.expected_verdict == "install_unverified"
    assert found, "the fleet contains no unverified install"


def test_cold_basement_winter_is_a_persistent_negative_delta_with_no_leak(frames):
    """The nastiest false positive: a legitimately cold pipe, as deep as a slow leak, from
    day one, with nothing wrong. It is what §5 requirement 5 exists for."""
    groups = _by_scenario(frames)
    cold = groups["cold_basement_winter"]
    assert len(cold) > 50
    deltas = sorted(f.delta_c for f in cold)
    median = deltas[len(deltas) // 2]
    assert median <= fleet.COLD_PIPE_LABEL_DELTA_C
    # Overlapping the slow-leak distribution is the whole point; if it did not overlap, the
    # scenario would not be hard and the detector would be being flattered.
    leak = sorted(f.delta_c for f in groups["slow_leak"])
    assert median > leak[0], "cold basements should not be deeper than every leak sample"
    assert deltas[int(len(deltas) * 0.25)] < leak[len(leak) // 2] + 0.5
    for f in cold:
        d = fleet.decode_uplink(f.raw)
        assert d["flags"]["install_verified"] is True
        assert f.is_true_event is False
        assert d["pipe_c"] > 0.0, "a cold basement is cold, not freezing"


def test_quiet_devices_sit_near_zero_delta(frames):
    groups = _by_scenario(frames)
    quiet = sorted(f.delta_c for f in groups["quiet"])
    assert len(quiet) > 200
    median = quiet[len(quiet) // 2]
    assert -1.0 < median < 0.5, median
    leak = sorted(f.delta_c for f in groups["slow_leak"])
    assert median > leak[len(leak) // 2] + 0.8


def test_archetypes_differ_enough_to_need_a_per_device_baseline(frames):
    """§5 requirement 5: a fixed global threshold is wrong because housing stock varies. This
    is the measurement that says by how much."""
    quiet_median = {}
    for plan, dev_frames in frames.values():
        deltas = sorted(f.delta_c for f in dev_frames if f.scenario in ("quiet", "cold_basement_winter"))
        if len(deltas) > 30:
            quiet_median[plan.device.archetype.name] = deltas[len(deltas) // 2]
    assert len(quiet_median) >= 3, quiet_median
    spread = max(quiet_median.values()) - min(quiet_median.values())
    assert spread > 0.8, "baselines barely differ (%s), so a global threshold would work" % quiet_median


def test_the_leak_signal_is_seasonal():
    """A finding worth keeping as a test: the measurement is driven by
    `(ground_water - ambient)`, so the same leak is far less visible in autumn, when mains
    water is still warm, than in late winter. It decides when field data is worth collecting."""
    winter = fleet.ground_water_c(cli.parse_start("2027-02-20T00:00:00Z"))
    autumn = fleet.ground_water_c(cli.parse_start("2026-10-10T00:00:00Z"))
    assert 3.5 < winter < 7.0, winter
    assert 13.0 < autumn < 18.0, autumn
    assert autumn - winter > 7.0


def test_ground_water_never_freezes_and_the_year_is_continuous():
    prev = None
    for day in range(0, 740):
        ts = START_S + day * fleet.SECONDS_PER_DAY
        gw = fleet.ground_water_c(ts)
        assert gw >= 1.0
        assert 1.0 <= gw <= 20.0
        if prev is not None:
            assert abs(gw - prev) < 0.3, "ground water must not jump day to day"
        prev = gw


def test_thermal_response_is_asymmetric():
    """Fast with flow, slow without. During a draw the measurement is limited by the clamp's
    own mass; with no flow it is limited by the standing water column, which is ten times
    heavier. That asymmetry is why a two-minute draw is still visible three samples later,
    and therefore why the nuisance problem is hard rather than trivial."""
    for arch in fleet.ARCHETYPES.values():
        assert arch.tau_still_s > 8 * arch.tau_flow_s, arch.name
        assert 60.0 <= arch.tau_flow_s <= 180.0, arch.name
        assert 900.0 <= arch.tau_still_s <= 2700.0, arch.name


def test_flow_coupling_saturates():
    assert fleet.flow_coupling(0.0) == 0.0
    prev = 0.0
    for flow in (0.05, 0.1, 0.2, 0.4, 0.6, 0.8, 1.0):
        c = fleet.flow_coupling(flow)
        assert c > prev
        prev = c
    assert fleet.flow_coupling(1.0) < 1.0, "the pipe surface never reaches the water temperature"
    # Sub-linear: at a trickle the water part-warms before it reaches the sensor, which is
    # exactly why a slow leak is a small signal.
    assert fleet.flow_coupling(0.1) > 0.1 * fleet.flow_coupling(1.0)
    assert fleet.flow_coupling(0.1) < 0.4 * fleet.flow_coupling(1.0)


# ----------------------------------------------------------------------------------- CLI


def test_cli_writes_parseable_jsonl(tmp_path):
    path = str(tmp_path / "fleet.jsonl")
    assert cli.main(["--devices", "5", "--days", "3", "--out", path, "--quiet"]) == 0
    with open(path, encoding="utf-8") as handle:
        lines = [json.loads(line) for line in handle]
    assert len(lines) > 500
    for env in lines[:200]:
        assert len(base64.b64decode(env["uplink_message"]["frm_payload"])) == 8
    assert {env["_label"]["scenario"] for env in lines} == set(fleet.SCENARIOS)


def test_cli_rejects_nonsense():
    assert cli.main(["--devices", "0"]) == 2
    assert cli.main(["--days", "0"]) == 2
    assert cli.main(["--heartbeat-min", "999"]) == 2  # §2 bounds set_heartbeat at 1..240
    assert cli.main(["--start", "not-a-date"]) == 2
    assert cli.main(["--out", "x.jsonl", "--post", "http://127.0.0.1:1/"]) == 2


def test_parse_start_accepts_the_contract_timestamp_format():
    # The §4 example `received_at`. Note that §6's example `event_start_s` of 1760000000 is
    # 2025-10-09, a year before the contract's own authoring date -- see README.md.
    assert cli.parse_start("2026-10-14T03:00:00Z") == 1791946800
    assert cli.parse_start("2026-10-14T03:00:00+00:00") == 1791946800
    assert fleet.iso_utc(1791946800) == "2026-10-14T03:00:00Z"
    assert fleet.iso_utc(cli.parse_start(cli.DEFAULT_START)) == cli.DEFAULT_START


def test_summary_reports_the_section_7_inputs(small_fleet):
    summary = cli.summarise(small_fleet, cli.stream_envelopes(small_fleet))
    assert summary["devices"] == len(small_fleet.plans)
    assert summary["envelopes"] > 100
    assert summary["missing_scenarios"] == []
    assert 0.0 <= summary["packet_loss_pct"] <= 40.0
    assert summary["pct_never_reporting_after_day7"] is not None
    assert set(summary["by_uplink_reason"]) <= set(fleet.UPLINK_REASONS)
    assert "boot" in summary["by_uplink_reason"]
    # §2's install-probe result frame has to actually be sent by something.
    assert summary["by_uplink_reason"]["install_probe"] >= len(small_fleet.plans)


def test_never_reporting_is_not_reported_on_a_window_shorter_than_the_metric():
    spec = scenarios.build_fleet(n_devices=4, start_s=START_S, days=3, seed=SEED)
    summary = cli.summarise(spec, cli.stream_envelopes(spec))
    assert summary["pct_never_reporting_after_day7"] is None


def test_print_summary_mentions_every_scenario(small_fleet):
    buf = io.StringIO()
    cli._print_summary(cli.summarise(small_fleet, cli.stream_envelopes(small_fleet)), stream=buf)
    text = buf.getvalue()
    for name in fleet.SCENARIOS:
        assert name in text


# -------------------------------------------------------------------------- --post mode


class _FakeResponse:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b"{}"


def test_post_mode_sends_json_without_touching_the_network(small_fleet):
    """INTERFACES.md §10: tests pass offline. `urlopen` is injected rather than monkeypatched
    at module level so nothing can accidentally open a socket."""
    sent = []

    def fake_urlopen(req, timeout=None):
        sent.append(req)
        return _FakeResponse(200)

    envelopes = list(cli.stream_envelopes(small_fleet))[:50]
    result = cli.post_stream("http://127.0.0.1:8000/api/v1/uplink", envelopes, urlopen=fake_urlopen)
    assert result == {"sent": 50, "status_counts": {"200": 50}}
    assert len(sent) == 50
    for req in sent[:5]:
        assert req.get_method() == "POST"
        assert req.get_full_url() == "http://127.0.0.1:8000/api/v1/uplink"
        assert req.get_header("Content-type") == "application/json"
        body = json.loads(req.data.decode("utf-8"))
        assert body["uplink_message"]["f_port"] == 1
        assert len(base64.b64decode(body["uplink_message"]["frm_payload"])) == 8


def test_post_mode_counts_failures_instead_of_raising(small_fleet):
    import urllib.error

    codes = iter([200, 409, 500, 200])

    def fake_urlopen(req, timeout=None):
        code = next(codes)
        if code >= 400:
            raise urllib.error.HTTPError(req.get_full_url(), code, "no", {}, None)
        return _FakeResponse(code)

    envelopes = list(cli.stream_envelopes(small_fleet))[:4]
    result = cli.post_stream("http://127.0.0.1:8000/api/v1/uplink", envelopes, urlopen=fake_urlopen)
    assert result["sent"] == 4
    assert result["status_counts"] == {"200": 2, "409": 1, "500": 1}


def test_post_mode_survives_an_unreachable_backend(small_fleet):
    import urllib.error

    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    envelopes = list(cli.stream_envelopes(small_fleet))[:3]
    result = cli.post_stream("http://127.0.0.1:1/uplink", envelopes, urlopen=fake_urlopen)
    assert result == {"sent": 3, "status_counts": {"unreachable": 3}}


def test_post_mode_paces_requests_when_asked(small_fleet):
    waits = []

    def fake_urlopen(req, timeout=None):
        return _FakeResponse(200)

    envelopes = list(cli.stream_envelopes(small_fleet))[:5]
    cli.post_stream(
        "http://127.0.0.1:8000/api/v1/uplink",
        envelopes,
        urlopen=fake_urlopen,
        rate=1000.0,
        sleep=waits.append,
    )
    assert waits, "no pacing happened despite --rate"


def test_post_mode_honours_limit(small_fleet):
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        return _FakeResponse(202)

    result = cli.post_stream(
        "http://127.0.0.1:8000/api/v1/uplink",
        cli.stream_envelopes(small_fleet),
        urlopen=fake_urlopen,
        limit=7,
    )
    assert result["sent"] == 7
    assert len(calls) == 7


def test_cli_post_path_end_to_end(small_fleet, monkeypatch):
    posted = []

    def fake_urlopen(req, timeout=None):
        posted.append(req)
        return _FakeResponse(200)

    monkeypatch.setattr(cli.urllib.request, "urlopen", fake_urlopen)
    rc = cli.main(
        [
            "--devices",
            "3",
            "--days",
            "2",
            "--post",
            "http://127.0.0.1:8000/api/v1/uplink",
            "--limit",
            "25",
            "--quiet",
        ]
    )
    assert rc == 0
    assert len(posted) == 25


def test_cli_post_path_reports_failure(monkeypatch):
    import urllib.error

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.get_full_url(), 500, "boom", {}, None)

    monkeypatch.setattr(cli.urllib.request, "urlopen", fake_urlopen)
    rc = cli.main(
        ["--devices", "2", "--days", "2", "--post", "http://127.0.0.1:8000/api/v1/uplink", "--limit", "3", "--quiet"]
    )
    assert rc == 1


# ------------------------------------------------------------------------------ hygiene


# Everything `simulator/` is allowed to import. An explicit list rather than
# `sys.stdlib_module_names`, which only exists from Python 3.10 and the target runtime is 3.9.
_ALLOWED_IMPORTS = frozenset(
    {
        "argparse",
        "ast",
        "base64",
        "collections",
        "dataclasses",
        "datetime",
        "hashlib",
        "heapq",
        "io",
        "itertools",
        "json",
        "math",
        "os",
        "random",
        "shutil",
        "struct",
        "subprocess",
        "sys",
        "time",
        "typing",
        "urllib",
        # test-only
        "pytest",
    }
)

_MODULE_FILES = ("fleet.py", "scenarios.py", "__main__.py", "__init__.py", "test_fleet.py")


def test_simulator_imports_no_third_party_packages():
    """INTERFACES.md §10 and §5: `simulator/` is stdlib-only so it runs anywhere Python 3.9
    runs, and so the detector it feeds is written the way the firmware will have to be. The
    easiest way for that to rot is someone adding `import numpy` for one histogram."""
    import ast

    here = os.path.dirname(os.path.abspath(__file__))
    for name in _MODULE_FILES:
        with open(os.path.join(here, name), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                roots = [(node.module or "").split(".")[0]] if node.level == 0 else []
            else:
                continue
            for root in roots:
                assert root in _ALLOWED_IMPORTS, "%s imports %r" % (name, root)


def test_python_39_compatible_syntax():
    """The build box is Python 3.9.25 (INTERFACES.md header): no `X | Y` runtime unions and
    no `match`. `ast.parse` with `feature_version` rejects syntax newer than that, which
    catches the mistake here rather than on someone else's machine."""
    import ast

    here = os.path.dirname(os.path.abspath(__file__))
    for name in _MODULE_FILES:
        with open(os.path.join(here, name), encoding="utf-8") as handle:
            source = handle.read()
        ast.parse(source, filename=name, feature_version=(3, 9))
        # `feature_version` does not reject PEP 604 unions in annotations, which parse fine
        # and then fail at runtime on 3.9, so check the annotations themselves.
        for node in ast.walk(ast.parse(source)):
            annotation = getattr(node, "annotation", None) or getattr(node, "returns", None)
            if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
                raise AssertionError("%s uses a PEP 604 `X | Y` annotation" % name)


def test_every_module_has_a_docstring():
    """§10: every module says what it is and what it deliberately does not do."""
    for mod in (fleet, scenarios, cli):
        assert mod.__doc__ and len(mod.__doc__) > 200, mod.__name__
        assert "deliberately" in mod.__doc__.lower(), mod.__name__


def test_no_placeholder_code():
    """§10 forbids stubs, because a prototype with one in it cannot be distinguished from one
    that works. This file is excluded from the scan for the obvious reason that it has to name
    the banned strings in order to look for them."""
    here = os.path.dirname(os.path.abspath(__file__))
    banned = ("T" + "ODO", "FIX" + "ME", "implement me", "NotImplementedError", "pass  #")
    for name in os.listdir(here):
        if not name.endswith(".py") or name == os.path.basename(__file__):
            continue
        with open(os.path.join(here, name), encoding="utf-8") as handle:
            text = handle.read()
        for marker in banned:
            assert marker not in text, "%s contains %r" % (name, marker)
