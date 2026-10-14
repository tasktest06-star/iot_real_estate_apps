"""Behavioural tests for the leak detector.

One test (at least) per required behaviour in contracts/INTERFACES.md §5.
Every sequence here is hand-built from arithmetic, with no dependency on
`simulator/` at all, so these tests are the thing that still runs when the
simulator is broken, absent or being rewritten.

Style note: the helpers build *temperature profiles*, not magic numbers, so a
failure says which physical story stopped working rather than which constant
moved.
"""

import dataclasses
import sys

import pytest

from detection import detector
from detection.detector import Decision, Sample, VERDICTS, new_state, update

T0 = 1760000000          # a round unix timestamp, 2025-10-09
HEARTBEAT_S = 900        # 15 min, the P1 default duty cycle


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def mk(ts, pipe_c, ambient_c, install_verified=True, tamper=False,
       battery_mv=3600):
    return Sample(ts_s=ts, pipe_c=pipe_c, ambient_c=ambient_c,
                  battery_mv=battery_mv, install_verified=install_verified,
                  tamper=tamper)


def run_profile(state, profile, start_ts=T0, interval_s=HEARTBEAT_S,
                dither=True, **kw):
    """Feed `(pipe_c, ambient_c)` pairs at a fixed cadence; return decisions.

    `dither` adds a one-LSB (0.01 C) wobble to the pipe reading, because that
    is what a real 16-bit centi-degree channel does and a bit-identical stream
    is precisely what `sensor_fault_stuck` means. Tests that want a stuck
    channel pass `dither=False`.
    """
    out = []
    ts = start_ts
    for i, (pipe_c, ambient_c) in enumerate(profile):
        if dither:
            pipe_c = pipe_c + (i % 3 - 1) * 0.01
        out.append(update(state, mk(ts, pipe_c, ambient_c, **kw)))
        ts += interval_s
    return out


def settle(state, pipe_c=19.0, ambient_c=20.0, hours=30, start_ts=T0,
           interval_s=HEARTBEAT_S):
    """Get a device past warm-up with a stable baseline.

    30 h at a 15-minute heartbeat is 120 samples: comfortably past both the
    12 h warm-up and the 6-sample minimum, and long enough for the 2-day
    baseline EWMA to be sitting on the real value.
    """
    n = (hours * 3600) // interval_s
    decisions = run_profile(state, [(pipe_c, ambient_c)] * n,
                            start_ts=start_ts, interval_s=interval_s)
    assert decisions[-1].verdict == "quiet", decisions[-1]
    return start_ts + n * interval_s


def verdicts(decisions):
    return [d.verdict for d in decisions]


# ---------------------------------------------------------------------------
# contract shape
# ---------------------------------------------------------------------------

def test_contract_surface_matches_interfaces_md():
    assert VERDICTS == ("quiet", "watch", "leak_suspected", "freeze_risk",
                        "sensor_fault", "install_unverified")
    assert [f.name for f in dataclasses.fields(Sample)] == [
        "ts_s", "pipe_c", "ambient_c", "battery_mv", "install_verified", "tamper"]
    assert [f.name for f in dataclasses.fields(Decision)] == [
        "verdict", "confidence", "reason", "delta_c", "baseline_c"]


def test_every_decision_is_well_formed():
    """Whatever we throw at it, the Decision must be renderable by an operator UI."""
    state = new_state("leak-0001")
    profile = [(19.0, 20.0), (4.0, 20.0), (-99.0, 20.0), (19.0, 20.0),
               (float("nan"), 20.0), (19.0, 20.0), (3.0, 20.0)]
    decisions = run_profile(state, profile)
    decisions.append(update(state, mk(T0 + 99999, 19.0, 20.0, install_verified=False)))
    decisions.append(update(state, mk(T0 + 100899, 19.0, 20.0, tamper=True)))
    for d in decisions:
        assert d.verdict in VERDICTS
        assert isinstance(d.confidence, int) and 0 <= d.confidence <= 100
        assert isinstance(d.reason, str) and d.reason
        assert isinstance(d.delta_c, float)
        assert isinstance(d.baseline_c, float)


def test_new_state_is_per_device_and_independent():
    a, b = new_state("leak-0001"), new_state("leak-0002")
    settle(a)
    assert a.samples > 1
    assert b.samples == 0
    assert b.device_id == "leak-0002"


# ---------------------------------------------------------------------------
# behaviour 1 - sustained negative delta => leak_suspected
# ---------------------------------------------------------------------------

def test_sustained_cold_offset_raises_leak_suspected():
    """A pipe held 6 C below its learned baseline for hours is a leak."""
    state = new_state("leak-0001")
    ts = settle(state)
    # Continuous flow of 13 C mains water into a 20 C room.
    leak = run_profile(state, [(13.0, 20.0)] * 48, start_ts=ts)
    assert "leak_suspected" in verdicts(leak)
    first = verdicts(leak).index("leak_suspected")
    # Must pass through `watch` first: the detector should never jump straight
    # from quiet to an alert on one sample.
    assert set(verdicts(leak)[:first]) == {"watch"}
    # And the dwell must be honoured: at least DWELL_S of depressed time, to
    # within the one sample the detector cannot see inside.
    assert first * HEARTBEAT_S >= detector.DWELL_S - HEARTBEAT_S


def test_leak_verdict_latches_and_releases_only_after_a_long_clear_run():
    """One alert per episode, not one per sample, and no flapping."""
    state = new_state("leak-0001")
    ts = settle(state)
    leak = run_profile(state, [(13.0, 20.0)] * 40, start_ts=ts)
    assert leak[-1].verdict == "leak_suspected"
    ts += 40 * HEARTBEAT_S

    # Plumber arrives. The verdict must stay latched for a while rather than
    # dropping on the first clear sample.
    after = run_profile(state, [(19.0, 20.0)] * 40, start_ts=ts)
    assert after[0].verdict == "leak_suspected"
    assert "quiet" in verdicts(after)
    cleared = verdicts(after).index("quiet")
    assert cleared * HEARTBEAT_S >= detector.CLEAR_S - HEARTBEAT_S

    # A single cold sample mid-latch must not produce a second rising edge
    # once released - i.e. released state is genuinely clean.
    assert state.dwell_s == 0 and not state.leak_latched


def test_confidence_rises_with_both_depth_and_duration():
    shallow = new_state("leak-0001")
    ts = settle(shallow)
    shallow_d = [d for d in run_profile(shallow, [(16.5, 20.0)] * 60, start_ts=ts)
                 if d.verdict == "leak_suspected"]
    deep = new_state("leak-0002")
    ts = settle(deep)
    deep_d = [d for d in run_profile(deep, [(8.0, 20.0)] * 60, start_ts=ts)
              if d.verdict == "leak_suspected"]
    assert shallow_d and deep_d
    assert deep_d[0].confidence > shallow_d[0].confidence
    assert deep_d[-1].confidence >= deep_d[0].confidence


# ---------------------------------------------------------------------------
# behaviour 2 - brief excursions must NOT trigger. This is the product.
# ---------------------------------------------------------------------------

def test_a_morning_shower_does_not_trigger():
    """15 minutes of hot-water draw chilling the cold feed. Not an alert."""
    state = new_state("leak-0001")
    ts = settle(state)
    # Threshold-triggered uplinks during the event, 60 s apart, which is the
    # worst case for the dwell integrator: lots of depressed samples fast.
    shower = run_profile(state, [(12.0, 20.0)] * 15, start_ts=ts, interval_s=60)
    assert "leak_suspected" not in verdicts(shower)
    ts += 15 * 60
    recovery = run_profile(state, [(19.0, 20.0)] * 20, start_ts=ts)
    assert "leak_suspected" not in verdicts(recovery)
    assert recovery[-1].verdict == "quiet"


def test_a_week_of_daily_appliance_use_raises_no_alert():
    """Seven days of dishwasher + washer + two showers a day. Zero alerts.

    This is the nuisance test that matters: the gate in 08-roadmap.md §3 is
    <0.5 alerts per device-year, so a single alert in a week of normal use is
    about 50x over budget.
    """
    state = new_state("leak-0001")
    ts = settle(state)
    profile = []
    for _ in range(7):
        # A day of 15-minute heartbeats: 96 samples.
        day = [(19.0, 20.0)] * 96
        # Shower 07:00 (two samples), dishwasher 19:00 intermittent fill,
        # washing machine 20:30 with three fills.
        for idx in (28, 29):
            day[idx] = (12.0, 20.0)
        for idx in (76, 79, 82):                   # dishwasher: fill, pause, fill
            day[idx] = (13.5, 20.0)
        for idx in (82, 86, 90):                   # washer fills
            day[idx] = (13.0, 20.0)
        profile.extend(day)
    decisions = run_profile(state, profile, start_ts=ts)
    assert "leak_suspected" not in verdicts(decisions)


def test_an_intermittent_half_duty_draw_never_accumulates_dwell():
    """50% duty cycle for six hours: the release multiplier must win.

    A bath being topped up, or an appliance cycling, produces a long train of
    depressed samples with recovery between them. The asymmetric release is
    what makes that different from a leak.
    """
    state = new_state("leak-0001")
    ts = settle(state)
    profile = []
    for _ in range(12):
        profile.extend([(13.0, 20.0)] * 2 + [(19.0, 20.0)] * 2)
    decisions = run_profile(state, profile, start_ts=ts)
    assert "leak_suspected" not in verdicts(decisions)
    assert state.dwell_s < detector.DWELL_S


def test_one_enormous_sampling_gap_cannot_buy_a_leak_verdict():
    """A device on a 4-hour heartbeat must still need several confirmations.

    Without the per-sample dwell credit cap and the >=4-confirmation rule, a
    single depressed sample after a long silence would clear the whole dwell
    window in one step.
    """
    state = new_state("leak-0001")
    interval_s = 4 * 3600
    ts = settle(state, interval_s=interval_s, hours=200)
    decisions = run_profile(state, [(13.0, 20.0)] * 3, start_ts=ts,
                            interval_s=interval_s)
    assert verdicts(decisions) == ["watch", "watch", "watch"]
    assert state.dwell_s <= 3 * detector.DT_CREDIT_MAX_S
    # It does eventually fire - this is a delay, not a blind spot.
    more = run_profile(state, [(13.0, 20.0)] * 16,
                       start_ts=ts + 3 * interval_s, interval_s=interval_s)
    assert "leak_suspected" in verdicts(more)


# ---------------------------------------------------------------------------
# behaviour 3 - install_verified == False
# ---------------------------------------------------------------------------

def test_unverified_install_yields_install_unverified():
    state = new_state("leak-0001")
    decisions = run_profile(state, [(19.0, 20.0)] * 5, install_verified=False)
    assert verdicts(decisions) == ["install_unverified"] * 5
    assert all(d.confidence == 100 for d in decisions)


def test_unverified_install_never_yields_a_leak_verdict():
    """Even a textbook leak signature, for a week, must not alert."""
    state = new_state("leak-0001")
    decisions = run_profile(state, [(13.0, 20.0)] * 672,        # 7 days
                            install_verified=False)
    assert set(verdicts(decisions)) == {"install_unverified"}


def test_unverified_install_outranks_a_concurrent_fault():
    """Both apply; the verdict is install_unverified and the reason says so."""
    state = new_state("leak-0001")
    d = update(state, mk(T0, 19.0, 20.0, install_verified=False, tamper=True))
    assert d.verdict == "install_unverified"
    assert "implausible" in d.reason


def test_verification_rearms_the_learner_so_evidence_is_not_carried_over():
    """Anything learned before verification may have come from a misplaced
    sensor (08-roadmap.md §8 risk 4), so it must not count toward an alert."""
    state = new_state("leak-0001")
    # A full day of leak-shaped data while unverified.
    run_profile(state, [(13.0, 20.0)] * 96, install_verified=False)
    assert state.dwell_s == 0 and not state.leak_latched
    ts = T0 + 96 * HEARTBEAT_S
    # Verification arrives, and the cold signature continues. The detector
    # must re-learn before it may alert - so no alert within the warm-up.
    early = run_profile(state, [(13.0, 20.0)] * 8, start_ts=ts)
    assert "leak_suspected" not in verdicts(early)


# ---------------------------------------------------------------------------
# behaviour 4 - sensor faults, never a leak
# ---------------------------------------------------------------------------

def test_tamper_flag_yields_sensor_fault():
    state = new_state("leak-0001")
    ts = settle(state)
    d = update(state, mk(ts, 19.0, 20.0, tamper=True))
    assert d.verdict == "sensor_fault"
    assert "tamper" in d.reason


def test_tamper_during_a_live_leak_signature_never_reports_a_leak():
    state = new_state("leak-0001")
    ts = settle(state)
    decisions = run_profile(state, [(13.0, 20.0)] * 60, start_ts=ts, tamper=True)
    assert set(verdicts(decisions)) == {"sensor_fault"}


@pytest.mark.parametrize("pipe_c,ambient_c", [
    (-40.01, 20.0), (80.01, 20.0), (19.0, -41.0), (19.0, 120.0),
    (-327.68, 20.0), (327.67, 20.0),
])
def test_out_of_range_readings_yield_sensor_fault(pipe_c, ambient_c):
    """The int16 centi-degree wire format can carry -327.68..327.67 C, so
    out-of-range values are representable and must be rejected."""
    state = new_state("leak-0001")
    ts = settle(state)
    d = update(state, mk(ts, pipe_c, ambient_c))
    assert d.verdict == "sensor_fault"


@pytest.mark.parametrize("pipe_c", [-40.0, 80.0])
def test_range_limits_themselves_are_accepted(pipe_c):
    """-40 and +80 are in range; the test above proves -40.01 and 80.01 are not."""
    state = new_state("leak-0001")
    d = update(state, mk(T0, pipe_c, 20.0))
    assert d.verdict != "sensor_fault"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_readings_yield_sensor_fault(bad):
    state = new_state("leak-0001")
    ts = settle(state)
    assert update(state, mk(ts, bad, 20.0)).verdict == "sensor_fault"
    assert update(state, mk(ts + 900, 19.0, bad)).verdict == "sensor_fault"


# The next two tests isolate the step-RATE check, which means the fixture has to
# avoid tripping any of the other three fault tests on the way past.
#
# An earlier version stepped UP to 49 C to dodge being confounded with freeze
# risk, and walked into the opposite confound: +29 C against a -1 C baseline is
# a 30 C excursion, which the hot-side plausibility band rejects outright (a
# cold main has no heat source). The band fired first, so the tests were really
# asserting against the band while claiming to test the step rate.
#
# So: step DOWNWARD by 12 C, to pipe 7.0 C. That is
#   - over the 10.0 C allowance at a 60 s gap   -> a fault, via the step test
#   - under the 20.0 C allowance at a 900 s gap -> plausible
#   - a -12 C excursion, inside the 25 C cold band  -> not "extreme"
#   - still 3 C clear of FREEZE_WARN_C = 4.0        -> not freeze_risk
# which leaves exactly one check able to fire.
_STEP_PIPE_C = 7.0               # 12 C below settle()'s 19.0 C


def test_a_step_faster_than_thermal_mass_yields_sensor_fault():
    """12 C in 60 s on a clamped sensor is not physics, it is wiring."""
    state = new_state("leak-0001")
    last = settle(state) - HEARTBEAT_S       # timestamp of the final settled sample
    d = update(state, mk(last + 60, _STEP_PIPE_C, 20.0))
    assert d.verdict == "sensor_fault"
    assert "thermal mass" in d.reason


def test_the_step_allowance_widens_with_the_sampling_gap():
    """Over a 15-minute gap the same 12 C step is a plausible transition and
    must not be called a fault - at that spacing you genuinely cannot tell."""
    state = new_state("leak-0001")
    ts = settle(state)
    assert update(state, mk(ts, _STEP_PIPE_C, 20.0)).verdict != "sensor_fault"


def test_a_genuine_fast_onset_is_not_called_a_fault():
    """Flow starting does drop the pipe quickly; 7 C in a minute must pass."""
    state = new_state("leak-0001")
    last = settle(state) - HEARTBEAT_S
    d = update(state, mk(last + 60, 12.0, 20.0))
    assert d.verdict in ("watch", "quiet")


def test_a_noisy_sensor_yields_sensor_fault_and_never_a_leak():
    state = new_state("leak-0001")
    ts = settle(state)
    noisy = []
    for i in range(40):
        pipe = 60.0 if i % 2 else -20.0
        noisy.append((pipe, 20.0))
    decisions = run_profile(state, noisy, start_ts=ts, interval_s=60)
    assert "leak_suspected" not in verdicts(decisions)
    assert verdicts(decisions).count("sensor_fault") >= 38
    assert any("suspect" in d.reason for d in decisions)


def test_a_stuck_channel_yields_sensor_fault_and_never_a_leak():
    """The dangerous stuck case: frozen at a cold value, so the depression
    never clears and a naive dwell integrator would alert forever."""
    state = new_state("leak-0001")
    ts = settle(state)
    # Pipe channel dies at 13.00 C exactly while the room swings a few degrees.
    profile = [(13.0, 20.0 + (i % 4) * 1.0) for i in range(48)]
    decisions = run_profile(state, profile, start_ts=ts, dither=False)
    assert "sensor_fault" in verdicts(decisions)
    assert "leak_suspected" not in verdicts(decisions)
    assert any("LSB" in d.reason for d in decisions)
    # And it has to land before the dwell window would have expired, or the
    # alert goes out before anyone notices the channel is dead.
    assert verdicts(decisions).index("sensor_fault") < detector.DWELL_S // HEARTBEAT_S


def test_a_stuck_channel_is_caught_even_when_the_room_is_also_still():
    """Second tier: half a day of bit-identical readings is a dead channel
    whatever the room is doing. Deliberately slow, because calling a live
    sensor stuck opens a detection blind spot."""
    state = new_state("leak-0001")
    ts = settle(state)
    decisions = run_profile(state, [(13.0, 20.0)] * 60, start_ts=ts,
                            dither=False)
    assert "sensor_fault" in verdicts(decisions)
    stuck_at = verdicts(decisions).index("sensor_fault")
    assert stuck_at >= detector.STUCK_HARD_SAMPLES


def test_a_fault_freezes_the_learned_baseline():
    """Garbage must not be allowed to poison 'normal'."""
    state = new_state("leak-0001")
    ts = settle(state)
    before = state.base_q
    run_profile(state, [(500.0, 20.0)] * 20, start_ts=ts)
    assert state.base_q == before


# ---------------------------------------------------------------------------
# behaviour 5 - freeze risk
# ---------------------------------------------------------------------------

# Freeze needs FREEZE_DWELL_S (2 h) of time below FREEZE_WARN_C (2.0 C) before
# it latches, which at the 15-minute heartbeat is 8 samples. A single graze no
# longer latches, and that is deliberate: latching on one sample was measured as
# the dominant nuisance source on a mixed fleet (60 of 61 alerts). See the
# comment on FREEZE_WARN_C in detector.py for the sweep.
_FREEZE_DWELL_SAMPLES = detector.FREEZE_DWELL_S // HEARTBEAT_S      # 8


def test_pipe_below_freeze_threshold_yields_freeze_risk():
    state = new_state("leak-0001")
    ts = settle(state, pipe_c=9.0, ambient_c=10.0)
    cold = [(1.5, 10.0)] * (_FREEZE_DWELL_SAMPLES + 2)
    decisions = run_profile(state, cold, start_ts=ts)
    assert decisions[-1].verdict == "freeze_risk"
    # ...and not before the dwell is satisfied.
    assert decisions[0].verdict != "freeze_risk"


def test_a_brief_graze_below_the_freeze_line_does_not_latch():
    """An unheated basement grazes the setpoint most winter nights. That must
    not page anyone - it is the measured cause of a 20.6/device-year nuisance
    rate against a 0.50 gate."""
    state = new_state("leak-0001")
    ts = settle(state, pipe_c=9.0, ambient_c=10.0)
    graze = [(1.5, 10.0)] * (_FREEZE_DWELL_SAMPLES - 3) + [(5.0, 10.0)] * 4
    decisions = run_profile(state, graze, start_ts=ts)
    assert "freeze_risk" not in verdicts(decisions)
    assert not state.freeze_latched


def test_freeze_risk_needs_no_warm_up():
    """An absolute threshold needs no per-device learning, so a device installed
    in a cold snap must warn after its own dwell alone - it must NOT have to
    wait out the 12 h baseline warm-up the leak path needs."""
    state = new_state("leak-0001")
    cold = [(1.0, 8.0)] * (_FREEZE_DWELL_SAMPLES + 2)
    decisions = run_profile(state, cold, start_ts=T0)
    assert decisions[-1].verdict == "freeze_risk"
    assert decisions[-1].confidence >= 60
    # The whole run is far shorter than the leak path's warm-up requirement.
    assert len(cold) * HEARTBEAT_S < detector.WARMUP_S


def test_freeze_risk_has_hysteresis_and_does_not_chatter():
    state = new_state("leak-0001")
    ts = settle(state, pipe_c=9.0, ambient_c=10.0)
    # Latch it first, with a genuine sustained spell below the line.
    ts = ts + len(run_profile(state, [(1.0, 10.0)] * (_FREEZE_DWELL_SAMPLES + 2),
                              start_ts=ts)) * HEARTBEAT_S
    assert state.freeze_latched
    # Now hover across the line: it must stay latched, not toggle per sample.
    hover = [(1.9, 10.0), (2.2, 10.0), (1.8, 10.0), (2.5, 10.0), (3.5, 10.0)]
    decisions = run_profile(state, hover, start_ts=ts)
    assert set(verdicts(decisions)) == {"freeze_risk"}
    # Only a clear recovery above FREEZE_CLEAR_C, sustained long enough to bleed
    # the dwell back to zero, releases it.
    rel = run_profile(state, [(6.5, 10.0)] * (_FREEZE_DWELL_SAMPLES + 2),
                      start_ts=ts + len(hover) * HEARTBEAT_S)
    assert rel[-1].verdict != "freeze_risk"


def test_freeze_risk_outranks_a_concurrent_leak_suspicion():
    """A freezing pipe is the more urgent dispatch, and the reason must still
    tell the operator the leak suspicion exists."""
    state = new_state("leak-0001")
    ts = settle(state, pipe_c=9.0, ambient_c=10.0)
    decisions = run_profile(state, [(1.0, 10.0)] * 40, start_ts=ts)
    # The tail, once both the freeze dwell and the leak dwell are satisfied.
    assert set(verdicts(decisions[-8:])) == {"freeze_risk"}
    assert state.leak_latched
    assert decisions[-1].reason == detector._R_FREEZE_AND_LEAK


def test_a_tampered_freezing_pipe_is_a_sensor_fault_first():
    state = new_state("leak-0001")
    assert update(state, mk(T0, 1.0, 8.0, tamper=True)).verdict == "sensor_fault"


# ---------------------------------------------------------------------------
# behaviour 6 - the baseline adapts per device
# ---------------------------------------------------------------------------

def test_two_devices_in_different_housing_stock_learn_different_baselines():
    warm = new_state("leak-warm")
    cold = new_state("leak-cold")
    settle(warm, pipe_c=21.5, ambient_c=22.0)
    settle(cold, pipe_c=6.0, ambient_c=11.0)
    warm_base = detector._to_cc(warm.base_q)
    cold_base = detector._to_cc(cold.base_q)
    assert abs(warm_base - (-50)) < 25            # about -0.5 C
    assert abs(cold_base - (-500)) < 25           # about -5.0 C
    assert cold_base < warm_base - 300


def test_a_legitimately_cold_basement_pipe_in_winter_is_quiet():
    """The nastiest false positive in the product.

    6 C pipe, 11 C room, all winter. A fixed global threshold on `delta_c`
    would alert on this device forever; the per-device baseline must learn
    -5 C as normal and say nothing. Freeze risk is checked separately and the
    pipe is deliberately above the freeze line here so the two do not mix.
    """
    state = new_state("leak-0001")
    ts = settle(state, pipe_c=6.0, ambient_c=11.0)
    # Two more weeks of the same, with realistic diurnal wobble.
    profile = []
    for i in range(1344):
        wobble = ((i % 96) - 48) / 96.0           # +/- 0.5 C over the day
        profile.append((6.0 + wobble, 11.0 + wobble * 1.2))
    decisions = run_profile(state, profile, start_ts=ts)
    assert "leak_suspected" not in verdicts(decisions)
    assert verdicts(decisions).count("quiet") > 1300


def test_a_leak_on_the_cold_basement_device_is_still_detected():
    """Adapting the baseline must not cost sensitivity: the same device must
    still catch a leak that takes it 6 C below its own cold normal."""
    state = new_state("leak-0001")
    ts = settle(state, pipe_c=6.0, ambient_c=11.0)
    decisions = run_profile(state, [(0.5, 11.0)] * 48, start_ts=ts)
    # Below the freeze line, so the urgent verdict wins - but the leak
    # suspicion is latched underneath it, which is what the operator needs.
    assert state.leak_latched
    assert set(verdicts(decisions)) <= {"watch", "freeze_risk", "leak_suspected"}


def test_a_slow_seasonal_drift_is_absorbed_not_alerted():
    """Mains water cooling 8 C over six weeks must be learned, not alerted."""
    state = new_state("leak-0001")
    ts = settle(state, pipe_c=19.0, ambient_c=20.0)
    profile = []
    for i in range(4032):                          # 6 weeks of heartbeats
        drift = 8.0 * i / 4032.0
        profile.append((19.0 - drift, 20.0))
    decisions = run_profile(state, profile, start_ts=ts)
    assert "leak_suspected" not in verdicts(decisions)


def test_a_fast_warming_room_does_not_alert_on_pipe_thermal_lag():
    """A basement warming 10 C after a cold night, with the pipe lagging it.

    `delta_c` goes several degrees negative with no water moving at all, and
    stays there for hours. Nothing about the thermal signature distinguishes
    this from a slow leak - except that the room is moving, which is the one
    extra channel the sensor has.
    """
    state = new_state("leak-0001")
    ts = settle(state, pipe_c=9.0, ambient_c=10.0)
    profile = []
    pipe = 9.0
    for i in range(24):                            # 6 h of ramp, 10 C
        ambient = 10.0 + 10.0 * (i + 1) / 24.0
        pipe += (ambient - 1.0 - pipe) * 0.08      # one-pole lag, tau ~3 h
        profile.append((pipe, ambient))
    for i in range(48):                            # 12 h settling
        ambient = 20.0
        pipe += (ambient - 1.0 - pipe) * 0.08
        profile.append((pipe, ambient))
    decisions = run_profile(state, profile, start_ts=ts)
    assert "leak_suspected" not in verdicts(decisions)
    assert any(d.verdict == "watch" for d in decisions), \
        "the test is only meaningful if the depression was actually seen"
    assert any(d.reason == detector._R_WATCH_SLEW for d in decisions), \
        "the ambient-slew hold is the mechanism under test"


def test_the_baseline_does_not_absorb_a_live_leak():
    """If 'normal' chased the leak, the alert would silently self-cancel."""
    state = new_state("leak-0001")
    ts = settle(state)
    base_before = detector._to_cc(state.base_q)
    decisions = run_profile(state, [(13.0, 20.0)] * 384, start_ts=ts)  # 4 days
    assert decisions[-1].verdict == "leak_suspected"
    base_after = detector._to_cc(state.base_q)
    # Four days of leak may move the held baseline a little; it must not move
    # anywhere near the 600 cc it would take to cancel the alert.
    assert abs(base_after - base_before) < 150


def test_a_churny_device_gets_a_higher_trigger_than_a_quiet_one():
    """The trigger is a multiple of the device's own measured scatter."""
    quiet = new_state("leak-quiet")
    churny = new_state("leak-churny")
    settle(quiet, hours=60)
    profile = []
    for i in range(240):                           # 60 h, +/- 1.5 C scatter
        profile.append((19.0 + (1.5 if i % 2 else -1.5), 20.0))
    run_profile(churny, profile)
    assert detector._to_cc(churny.mad_q) > detector._to_cc(quiet.mad_q)


# ---------------------------------------------------------------------------
# behaviour 7 - bounded memory
# ---------------------------------------------------------------------------

def _deep_size(obj, seen=None):
    """Total bytes reachable from `obj`, counting each object once."""
    if seen is None:
        seen = set()
    if id(obj) in seen:
        return 0
    seen.add(id(obj))
    size = sys.getsizeof(obj)
    if isinstance(obj, dict):
        for k, v in obj.items():
            size += _deep_size(k, seen) + _deep_size(v, seen)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for item in obj:
            size += _deep_size(item, seen)
    if hasattr(obj, "__dict__"):
        size += _deep_size(vars(obj), seen)
    return size


def test_state_holds_no_container_at_all():
    """Stronger than 'fixed-size window, not a growing list': there is no
    container in the state, so the C struct has no pointers and no heap."""
    state = new_state("leak-0001")
    settle(state)
    for field in dataclasses.fields(state):
        value = getattr(state, field.name)
        assert isinstance(value, (int, float, bool, str)), (field.name, value)


def test_state_size_does_not_grow_with_sample_count():
    """Feed 100 samples, then 20,000 more, and assert the state is byte-for-byte
    the same size. The window is a 32-bit shift register, so it cannot grow."""
    state = new_state("leak-0001")
    ts = T0
    for i in range(100):
        update(state, mk(ts, 19.0 + (i % 5) * 0.2, 20.0))
        ts += HEARTBEAT_S
    fields_after_100 = len(dataclasses.fields(state))
    size_after_100 = _deep_size(state)

    for i in range(20000):
        update(state, mk(ts, 19.0 + (i % 5) * 0.2, 20.0))
        ts += HEARTBEAT_S
    assert len(dataclasses.fields(state)) == fields_after_100
    assert _deep_size(state) == size_after_100

    # And the two shift registers really are bounded to their declared width.
    assert 0 <= state.window_bits <= (1 << detector.WINDOW_SAMPLES) - 1
    assert 0 <= state.fault_bits <= (1 << detector.WINDOW_SAMPLES) - 1


def test_every_timer_and_counter_saturates():
    """Nothing in the state may grow without bound, including the counters -
    they are uint16/uint32 on the target."""
    state = new_state("leak-0001")
    ts = T0
    for i in range(80000):
        update(state, mk(ts, 19.0, 20.0))
        ts += HEARTBEAT_S
    assert state.samples == detector._SAMPLES_SATURATE
    assert state.clear_s <= detector.CLEAR_S
    assert state.dwell_s <= detector.DWELL_MAX_S
    assert state.warm_s <= detector.WARMUP_S
    assert state.stuck_run <= 0xFF


# ---------------------------------------------------------------------------
# integer arithmetic - the properties the C port has to match
# ---------------------------------------------------------------------------

def test_div_trunc_matches_c_integer_division():
    assert detector._div_trunc(7, 2) == 3
    assert detector._div_trunc(-7, 2) == -3        # not -4, which is what // gives
    assert detector._div_trunc(-1, 2) == 0
    assert detector._div_trunc(0, 2) == 0
    assert -7 // 2 == -4, "the point of _div_trunc is that // is wrong here"


def test_the_ewma_is_a_fixed_point_of_itself_at_its_target():
    """The floor-division bug this guards against drifts an EWMA by one count
    per update in one direction - tens of degrees over a month of 15-minute
    samples, which would manufacture a fleet-wide false-positive storm."""
    for cc in (-700, -100, 0, 100, 2500):
        q = cc << detector._Q
        for _ in range(5000):
            q = detector._ewma(q, cc, HEARTBEAT_S, detector.TAU_BASE_S)
        assert q == cc << detector._Q


def test_the_baseline_does_not_drift_on_a_flat_input():
    state = new_state("leak-0001")
    run_profile(state, [(19.0, 20.0)] * 5000)
    # delta is -1.00 C; one LSB of dither cannot move the learned baseline
    # more than one LSB away from it.
    assert abs(detector._to_cc(state.base_q) - (-100)) <= 1


def test_popcount32_matches_the_reference():
    for v in (0, 1, 2, 3, 0xFFFFFFFF, 0x80000000, 0x55555555, 0x0F0F0F0F, 12345678):
        assert detector._popcount32(v) == bin(v).count("1")


def test_cc_rounds_symmetrically():
    assert detector._cc(11.25) == 1125
    assert detector._cc(-7.15) == -715
    assert detector._cc(0.0) == 0
    assert detector._cc(0.005) == 1
    assert detector._cc(-0.005) == -1


def test_out_of_order_and_duplicate_timestamps_do_not_corrupt_state():
    state = new_state("leak-0001")
    ts = settle(state)
    d1 = update(state, mk(ts, 19.0, 20.0))
    d2 = update(state, mk(ts, 19.0, 20.0))                 # duplicate
    d3 = update(state, mk(ts - 100000, 19.0, 20.0))        # replayed from the past
    d4 = update(state, mk(ts + HEARTBEAT_S, 19.0, 20.0))
    assert [d.verdict for d in (d1, d2, d3, d4)] == ["quiet"] * 4
    assert state.last_ts_s == ts + HEARTBEAT_S
