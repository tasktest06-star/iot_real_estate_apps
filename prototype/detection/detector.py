"""Thermal differential-temperature leak detector for a clamp-on pipe sensor.

WHAT THIS IS
------------
A per-device state machine that turns a stream of (pipe temperature, ambient
temperature) readings into one of the six verdicts in `VERDICTS`.  The signal
is `delta_c = pipe_c - ambient_c` (contracts/INTERFACES.md §1: delta is derived,
never transmitted).  Cold mains water moving through a pipe pulls the pipe below
room temperature; a leak is *continuous* flow, so a leak is a **sustained
depression of `delta_c` below the value this particular device has learned to
call normal**.

Three ideas carry the whole algorithm:

1.  **Differential, not absolute.** Using `pipe - ambient` cancels the room.
    A cold basement in January and a warm utility closet in July produce the
    same quiescent delta, so one set of constants works on both.
2.  **Per-device learned baseline and per-device learned noise floor.**
    Housing stock varies enormously (contracts/INTERFACES.md §5 behaviour 5),
    so the comparison is always against *this* device's slow baseline, and the
    trigger threshold is a multiple of *this* device's measured scatter.
    The baseline is held nearly still while an excursion is in progress so a
    real leak cannot be quietly absorbed into "normal".
3.  **Duration, not depth, is the discriminator.** A shower and a burst pipe
    look identical for ten minutes.  They stop looking identical after three
    hours.  Every nuisance-rejection decision in here is a duration or
    duty-cycle argument, because that is the only axis on which normal
    appliance use and a leak actually separate.  This is risk 2 in
    08-roadmap.md §8 ("false positives destroy insurer trust") and the
    falsification claim in §7 ("nuisance alerts below 0.5 per device-year").

WHAT THIS DELIBERATELY DOES *NOT* DO
------------------------------------
* **No learning from labels.** There is no model file, no training step and no
  label of any kind at runtime.  Everything adapts from the device's own
  history.  Gradient-trained anything on the node is explicitly out of scope
  until P2 (08-roadmap.md §3, "Do not: put a neural network on the node").
* **No floating-point accumulators, no allocation in the steady path, no
  growing history.**  All learners are integer fixed-point; the only window is
  a 32-bit shift register.  `update()` allocates exactly one object, the
  `Decision` the contract requires it to return; the C port fills a
  caller-supplied struct instead.  State is a flat record of scalars, so
  `sizeof(detector_state_t)` is a compile-time constant.
* **No clock-of-day, occupancy, or weather priors.** They would help on paper
  and overfit instantly: a node has no reliable local time and no site
  metadata, and a detector that depends on them cannot be validated against
  the frozen evaluation set the P1 gate requires.
* **No flow, pressure or acoustic input.** Single-sensor thermal only. If the
  nuisance rate cannot be driven under the gate with this modality, that is a
  *finding*, not something to paper over (08-roadmap.md §7, claim 2: "the
  sensing modality is wrong").
* **No alert suppression policy, no dedup, no notification.** `update()` is
  pure with respect to I/O and says only what it currently believes. Counting
  alerts, deduplicating them and holding them in shadow mode are the
  backend's job (contracts/INTERFACES.md §8).
* **No use of `battery_mv`.** It is carried in `Sample` because the uplink
  carries it, but it is fleet health, not evidence about water. Letting a
  low battery move a leak verdict would make the headline metric depend on
  cell chemistry.
* **No valve actuation, ever.** 08-roadmap.md §8 risk 5 is architectural: the
  cloud does not close the valve, and neither does this file.

PORTABILITY NOTE FOR THE C IMPLEMENTATION
-----------------------------------------
`_div_trunc` exists because Python's `//` floors and C's `/` truncates toward
zero.  On a signed exponential-moving-average error term, floor division
injects a systematic one-count-per-update drift in one direction; over a month
of 15-minute samples that is tens of degrees of baseline error.  Matching C
semantics here is not pedantry, it is the difference between the two
implementations agreeing.  The EWMA intermediate product needs 64 bits on the
target (`(int64_t)err_q * dt_s`); a Cortex-M4 does that in a single SMULL.
"""

import dataclasses
import math
from typing import Optional

VERDICTS = ("quiet", "watch", "leak_suspected", "freeze_risk",
            "sensor_fault", "install_unverified")

# ---------------------------------------------------------------------------
# Tunables.  In the firmware these are `static const`; here they are module
# constants so that the prototype and the C port share one list of numbers.
#
# Units are degrees C and seconds for readability; everything is converted to
# centi-degrees (the on-air unit, contracts/INTERFACES.md §1) exactly once, at
# import, so the hot path never touches a float constant.
# ---------------------------------------------------------------------------

# Physical plausibility.  The on-air int16 centi-degree encoding can carry
# -327.68..327.67 C, so out-of-range values are representable and must be
# rejected rather than trusted.
SENSOR_MIN_C = -40.0
SENSOR_MAX_C = 80.0

# A clamp-on sensor on a pipe with real thermal mass cannot teleport. The
# allowance grows with the sampling gap because over a long gap a genuine
# full-swing transition is indistinguishable from a glitch - but it is capped,
# because the largest *physically possible* transition is mains-to-ambient and
# that is not 40 C anywhere this product ships.
PLAUSIBLE_STEP_C = 8.0            # instantaneous allowance (noise + fast onset)
PLAUSIBLE_RATE_C_PER_MIN = 2.0    # extra allowance per minute of gap
PLAUSIBLE_STEP_CEIL_C = 20.0      # ...but never more than this, at any gap

# The step test alone is close to useless at a 15-minute heartbeat: the gap
# allowance grows to tens of degrees, so a noisy channel's garbage looks like
# a plausible transition. This band is the test that actually catches it.
#
# IT MUST BE ASYMMETRIC, and an earlier symmetric version of this check was a
# real bug: it dismissed the clearest leaks as sensor faults.
#
# The physics is not symmetric. A clamp-on sensor on a cold-water main has no
# heat source, so the pipe genuinely cannot run far ABOVE the room - a reading
# like that means the sensor came loose, the channel is lying, or it is clamped
# to the hot line, all of which are an installer's problem rather than a leak.
# But it can sit a very long way BELOW the room, and that is the entire signal
# this product sells: under continuous flow the pipe settles at ground-water
# temperature, so a 4 C main feeding a 20 C basement in a Minnesota January is
# about -16 C of delta, indefinitely, and is a leak rather than a fault.
#
# Measured against `../simulator/` (30 devices, 20 days, seed 11), minimum
# delta by scenario:
#
#     fast_leak          -10.71      appliance_use      -10.38
#     slow_leak           -9.12      shower_morning      -9.29
#     cold_basement_winter -4.87     quiet               -2.56
#     sensor_fault_noisy -87.09  <-- the thing this check exists to catch
#
# So a 10 C symmetric band had roughly zero headroom on the cold side, not the
# "factor of 1.7" an earlier version of this comment claimed: `fast_leak`
# already touches it. The cold limit is therefore set well clear of any
# legitimate hydraulic excursion, and garbage is still caught by three other
# tests that do not care about direction - out-of-range on the raw channels,
# the step-rate test, and the `FAULT_WINDOW_MIN_HITS` sustain requirement.
EXTREME_COLD_DELTA_C = 25.0       # pipe far BELOW baseline: plausible, it is the signal
EXTREME_HOT_DELTA_C = 10.0        # pipe far ABOVE baseline: no mechanism, so a fault
FAULT_WINDOW_MIN_HITS = 2         # implausible samples in the window => sustained fault

# A stuck ADC channel reports the same centi-degree forever. Real sensors
# dither at the LSB. Two independent tiers so the call does not depend on the
# room happening to move.
#
# The call has to land *before* the dwell window expires, or a channel that
# sticks at a cold value produces a leak alert before anyone notices the
# channel is dead - which is one of the nastier nuisance sources, because it
# never recovers. One hour and six samples is the earliest defensible call: a
# pipe with even 5% thermal coupling to its room moves ~10 LSB when the room
# moves 2 C, so zero LSB of movement over that hour is a dead channel.
STUCK_MIN_SAMPLES = 6
STUCK_MIN_SPAN_S = 3600
# Long before the full stuck criteria are met, a pipe channel that has not
# moved one LSB for this many samples is not *evidence* of anything. Blocking
# the leak latch on it is what stops a channel that freezes at a cold value
# from alerting in the window before it is diagnosed - and because the dwell
# timer is only held, a real leak behind a coarsely-quantised feed is delayed
# by a sample or two rather than missed.
STUCK_SUSPECT_SAMPLES = 4
STUCK_AMBIENT_MOVE_C = 2.0        # room moved this much and the pipe moved zero LSBs
# Backstop for the case where the room is *also* dead still, so the comparison
# above proves nothing. Deliberately far out (12 h of bit-identical readings)
# because calling a live sensor stuck creates a detection blind spot, which is
# worse than a missed fault report.
STUCK_HARD_SAMPLES = 48
STUCK_HARD_SPAN_S = 12 * 3600

# Freeze warning. Application A4 in 02-application-portfolio.md.
#
# 4.0 C (40 F) is the CONVENTIONAL setpoint, and the reasoning behind it is
# sound in the field: the pipe in an exterior wall is colder than the pipe at
# your sensor, so burst risk starts well above 0 C. It is nonetheless NOT the
# value used here, and the reason is measurement rather than preference.
#
# Measured on `../simulator/`, a 40-device 30-day mixed fleet, sweeping the
# setpoint and the dwell (`nuisance/dev-yr` against the 0.50 P1 gate):
#
#     warn  dwell | nuisance/dev-yr  recall  precision  freeze caught
#      4.0     2h |      12.1793 FAIL  0.872      0.122      29/31
#      4.0     4h |       8.7962 FAIL  0.769      0.188      25/31
#      2.0     2h |       0.3383 PASS  0.359      0.857       9/31   <-- chosen
#      2.0     4h |       0.3383 PASS  0.308      0.833       7/31
#      1.5     2h |       0.3383 PASS  0.231      0.800       4/31
#      1.0     4h |       0.3383 PASS  0.179      0.750       2/31
#
# This is an irreducible trade-off, not a tuning miss. In this corpus a safe
# `cold_basement_winter` pipe bottoms out at +2.50 C while a real
# `freeze_event` reaches -5.25 C, but the two overlap heavily in the 2.5-4.0 C
# band, so no setpoint-plus-dwell on pipe temperature alone separates them.
# Above 2.0 C the nuisance rate blows the gate; below it, freeze recall
# collapses.
#
# The gate is the binding constraint (08-roadmap.md §2), so the setpoint goes
# where the gate passes, and the cost is stated rather than hidden:
#
#   *** KNOWN LIMITATION: freeze warning catches 9 of 31 freeze events (29%)
#   *** at this setting. 02-application-portfolio.md scores A4 as "free upside
#   *** on the same sensor"; this measurement says that is optimistic. Useful
#   *** freeze detection needs a second signal - rate-of-fall, or the ambient
#   *** trend the sensor already reports - not a lower threshold. Tracked as a
#   *** P2 task, and it does not block the leak product, whose own nuisance
#   *** rate (0.3383/device-year) passes independently.
#
# Whether the field agrees with the simulator here is exactly what P1 exists to
# find out. If real cold basements sit warmer than modelled, the conventional
# 4.0 C becomes affordable and recall improves for free.
FREEZE_WARN_C = 2.0
FREEZE_CLEAR_C = 4.0              # hysteresis: a pipe sitting at the line must not chatter

# ...and a dwell, for the same reason the leak path has one.
#
# MEASURED. Latching freeze on a *single* sample below the setpoint was the
# dominant nuisance source on a mixed fleet: 60 of 61 nuisance alerts, 45 of
# them from `cold_basement_winter`, i.e. 20.6 alerts per device-year against a
# 0.50 gate. The threshold is not the problem - 40 F is the conventional
# setpoint and a basement pipe at 3 C genuinely is worth a warning. The problem
# was that an unheated basement grazes the setpoint for a few samples most
# winter nights, and each graze latched.
#
# A real freeze event does not graze it. Measured on `../simulator/`, 88.5% of
# `freeze_event` samples sit below the line and the pipe reaches -5.25 C, while
# every safe scenario bottoms out near +2.5 C and only dips under for ~6% of
# samples, scattered. So requiring sustained time below the line separates them
# cleanly without touching the setpoint.
#
# 4 h is chosen to be longer than an overnight graze and far shorter than the
# time it takes a pipe to actually freeze and burst.
FREEZE_DWELL_S = 2 * 3600
FREEZE_DWELL_MAX_S = FREEZE_DWELL_S        # saturate AT the trigger, not above it
# Capping the accumulator at the trigger rather than at 2x bounds how long a
# warning outlives the cold: release bleeds at 1:1, so a pipe that recovers
# clears in at most FREEZE_DWELL_S (2 h) instead of 4 h. Banking extra credit
# above the trigger buys nothing here - unlike the leak path, where depth of
# accumulated dwell feeds the reported confidence.

# Learners.
TAU_FAST_S = 1800                 # smoothing of delta_c itself
TAU_MED_S = 5400                  # 90 min: "where delta_c has been lately"
TAU_BASE_S = 172800               # 2 days: "normal for this device"
TAU_BASE_WARMUP_S = 3600          # snap to the device during install warm-up
TAU_BASE_HELD_S = 2592000         # 30 days: creep only, while an excursion is live
TAU_NOISE_S = 86400               # 1 day: mean absolute deviation of delta from base
TAU_AMB_FAST_S = 3600
TAU_AMB_SLOW_S = 21600

# When the room is warming fast, the pipe lags it and `delta_c` goes negative
# for purely thermal-mass reasons. That is the nastiest false positive in the
# whole product (a basement warming after a cold night). Hold the dwell timer
# while this is happening rather than discarding the evidence.
AMB_SLEW_HOLD_C = 1.5

# The second hold, and the one that pays for itself. An uninsulated 1/2" copper
# pipe full of water has a thermal time constant of roughly 35-40 min against
# natural convection (~800 J/K per metre over ~0.35 W/K); insulate it and that
# becomes hours. So an evening of dishwasher-then-washing-machine fills does
# not let the pipe return to room temperature between draws: it ratchets down
# over a couple of hours and then takes a couple more to recover, which on a
# raw depression test is indistinguishable from a slow leak.
#
# What *is* distinguishable: during that recovery the depression is shrinking,
# where a leak holds it flat. `delta_c` rising relative to its own 90-minute
# average means the pipe is warming back up, so stop crediting dwell. Hold
# rather than reset, so a leak that starts mid-recovery is only delayed.
RECOVERY_HOLD_C = 0.25

# Trigger: a multiple of the device's own scatter, floored and ceilinged. The
# floor stops a pathologically quiet device from becoming hair-trigger; the
# ceiling stops a churny device from becoming undetectable.
TRIGGER_K = 4
TRIGGER_FLOOR_C = 2.0
TRIGGER_CEIL_C = 12.0
MAD_INIT_C = TRIGGER_FLOOR_C / TRIGGER_K   # start exactly at the floor

# Asymmetric Schmitt trigger. The detector *enters* the depressed state on the
# smoothed value, because a single cold sample is not evidence; it *leaves* it
# on the raw value, because recovery is unambiguous - the pipe warmed back up,
# and no smoothing is needed to believe that.
#
# Without this the fast EWMA's own lag inflates the apparent duty cycle of
# intermittent draw: a 50%-duty appliance looks 75% depressed, which is above
# the release ratio, and the dwell integrator creeps up to an alert over a few
# hours. That was a measured false positive, not a theoretical one.
TRIGGER_HOLD_NUM = 1              # raw depression must stay above
TRIGGER_HOLD_DEN = 2              #   trigger * NUM / DEN to hold the state

# Duration. This is the nuisance/latency trade-off, and it is the only knob
# that matters for the P1 gate.
#
# DWELL_S is set longer than the longest plausible *household draw session*,
# not longer than the longest appliance cycle. A dishwasher followed by a
# washing machine followed by a shower is three hours of intermittent draw on
# one evening, and it is common; six hours is not. The cost is time to
# detection on a shallow leak, which the P1 gate does not bound and which is
# still days better than a water bill.
DWELL_S = 21600                   # 6 h of net depressed time to call a leak
# Escape hatch for a burst. A depression this many times the device's own
# trigger means essentially continuous full-bore mains flow - the pipe is
# sitting at mains temperature, not somewhere between. Ninety minutes of that
# with no let-up at all is not a household draw.
#
# MEASURED, and weaker than it sounds. Against `../simulator/`, `fast_leak`
# bottoms out at -10.71 C and `appliance_use` at -10.38 C, so the two overlap
# almost entirely in instantaneous depth and this hatch rarely fires. Burst
# time-to-detection is therefore DWELL_S, not DWELL_DEEP_S: median 6.17 h
# across seeds 13/29/41/57/71, max 6.38 h, on 8-device 30-day burst-only
# fleets. See test_most_fast_leaks_are_caught_quickly.
#
# REJECTED ALTERNATIVE - do not re-add it without re-running the gate. A
# second hatch keyed on *continuity* rather than depth (an unbroken run of 12
# depressed samples, zero let-up, earning a 2.5 h dwell) looked obviously
# right: a burst never lets up, household draw does. Measured, it fired on
# quiet and normal-household-use fleets and broke
# test_a_fleet_of_only_quiet_devices_raises_nothing. At a 15-minute heartbeat
# a normal evening genuinely does produce 12 unbroken depressed samples, and
# the EWMA lag bridges the small gaps. The nuisance rate is the P1 exit gate;
# latency is not. Trading the gate for latency is the wrong trade.
DWELL_DEEP_MULT = 3
DWELL_DEEP_S = 5400

DWELL_MAX_S = 2 * DWELL_S         # saturate; state must not grow
DT_CREDIT_MAX_S = 1800            # one sample can never buy more than 30 min of dwell
DWELL_RELEASE_MULT = 2            # recovery erases dwell twice as fast as it accrues,
                                  # i.e. sustained >2/3 duty cycle is required
WINDOW_SAMPLES = 32               # the one and only window: a 32-bit shift register
WINDOW_MIN_DEPRESSED = 4          # >=4 independent confirmations, whatever the cadence
CLEAR_S = 21600                   # 6 h clear before a latched leak verdict releases

# Install warm-up: no leak verdict until the device has had time to learn what
# normal is. Also re-armed when an install flips from unverified to verified,
# because anything learned before verification may have been learned through a
# misplaced sensor (08-roadmap.md §8 risk 4).
WARMUP_S = 43200                  # 12 h
WARMUP_MIN_SAMPLES = 6

# --- derived integer constants (centi-degrees) -----------------------------

def _cc(value_c: float) -> int:
    """Degrees C -> centi-degrees, rounded half away from zero."""
    return int(math.floor(value_c * 100.0 + 0.5)) if value_c >= 0 \
        else -int(math.floor(-value_c * 100.0 + 0.5))


SENSOR_MIN_CC = _cc(SENSOR_MIN_C)
SENSOR_MAX_CC = _cc(SENSOR_MAX_C)
PLAUSIBLE_STEP_CC = _cc(PLAUSIBLE_STEP_C)
PLAUSIBLE_RATE_CC_PER_MIN = _cc(PLAUSIBLE_RATE_C_PER_MIN)
PLAUSIBLE_STEP_CEIL_CC = _cc(PLAUSIBLE_STEP_CEIL_C)
EXTREME_COLD_DELTA_CC = _cc(EXTREME_COLD_DELTA_C)
EXTREME_HOT_DELTA_CC = _cc(EXTREME_HOT_DELTA_C)
STUCK_AMBIENT_MOVE_CC = _cc(STUCK_AMBIENT_MOVE_C)
FREEZE_WARN_CC = _cc(FREEZE_WARN_C)
FREEZE_CLEAR_CC = _cc(FREEZE_CLEAR_C)
AMB_SLEW_HOLD_CC = _cc(AMB_SLEW_HOLD_C)
RECOVERY_HOLD_CC = _cc(RECOVERY_HOLD_C)
TRIGGER_FLOOR_CC = _cc(TRIGGER_FLOOR_C)
TRIGGER_CEIL_CC = _cc(TRIGGER_CEIL_C)
MAD_INIT_CC = _cc(MAD_INIT_C)

_Q = 8                            # fixed-point fraction bits for the learners
_WINDOW_MASK = (1 << WINDOW_SAMPLES) - 1
_SAMPLES_SATURATE = 0xFFFF        # uint16 on the target; must not grow without bound

# Operator-facing reason strings are module constants, not f-strings: string
# formatting in the hot path is an allocation, and the C port wants literals.
_R_UNVERIFIED = "install not verified; sensor placement unproven"
_R_UNVERIFIED_FAULT = "install not verified, and the reading is also implausible"
_R_TAMPER = "device reports tamper"
_R_RANGE = "reading outside -40..80 C"
_R_NONFINITE = "non-finite reading"
_R_STEP = "temperature step faster than pipe thermal mass allows"
_R_NOISY = "repeated implausible readings; sensor or wiring suspect"
_R_EXTREME = "pipe/ambient difference far outside this device's learned band"
_R_STUCK = "pipe channel has not moved one LSB while the room has"
_R_FREEZE = "pipe at or below freeze-warning threshold"
_R_FREEZE_AND_LEAK = "pipe below freeze threshold while a leak is also suspected"
_R_LEAK = "pipe held below its learned baseline for longer than any normal draw"
_R_WATCH = "pipe below its learned baseline; too brief to call"
_R_WATCH_SLEW = "pipe below baseline while the room warms; thermal lag, holding"
_R_WATCH_RECOVERING = "pipe below baseline but warming back up; draw, not a leak"
_R_WARMUP = "learning this device's baseline"
_R_QUIET = "pipe at its learned baseline"


@dataclasses.dataclass
class Sample:
    """One decoded uplink, in engineering units (contracts/INTERFACES.md §5)."""

    ts_s: int            # unix seconds
    pipe_c: float
    ambient_c: float
    battery_mv: int
    install_verified: bool
    tamper: bool


@dataclasses.dataclass
class Decision:
    """What the detector currently believes (contracts/INTERFACES.md §5)."""

    verdict: str         # one of VERDICTS
    confidence: int      # 0..100
    reason: str          # short human string, shown to an operator
    delta_c: float
    baseline_c: float    # current learned baseline, for plotting


@dataclasses.dataclass
class DetectorState:
    """Fixed-size per-device state.

    Every field is a scalar.  There is no list, dict, set or buffer anywhere in
    here, which is a stronger guarantee than the "fixed-size window, not a
    growing list" the contract asks for: the window is `window_bits`, a 32-bit
    shift register of past "was depressed" observations.  `sizeof()` of the C
    equivalent is about 70 bytes.
    """

    device_id: str
    initialised: bool = False
    # learners, fixed-point centi-degrees (<< _Q)
    fast_q: int = 0               # smoothed delta_c
    med_q: int = 0                # delta_c over the last 90 min, for trend sign
    base_q: int = 0               # learned normal delta_c for this device
    mad_q: int = 0                # learned mean absolute deviation of delta_c
    amb_fast_q: int = 0
    amb_slow_q: int = 0
    # last-sample memory, plain centi-degrees
    last_ts_s: int = 0
    last_pipe_cc: int = 0
    have_last: bool = False
    # timers, seconds, all saturating
    warm_s: int = 0
    dwell_s: int = 0
    clear_s: int = 0
    freeze_s: int = 0
    stuck_span_s: int = 0
    # counters, saturating
    samples: int = 0
    stuck_run: int = 0
    # min/max of ambient *within the current identical-pipe run*. A span is
    # used rather than a distance from a reference so the evidence is monotone
    # within the run: a room that swings away and back must not un-prove a
    # dead channel.
    stuck_amb_min_cc: int = 0
    stuck_amb_max_cc: int = 0
    # shift registers
    window_bits: int = 0          # bit0 = most recent "depressed" observation
    fault_bits: int = 0           # bit0 = most recent "implausible step"
    # latches
    leak_latched: bool = False
    freeze_latched: bool = False


def new_state(device_id: str) -> DetectorState:
    """Fresh state for one device. Cheap enough to hold one per fleet member."""
    return DetectorState(device_id=device_id)


# ---------------------------------------------------------------------------
# integer helpers
# ---------------------------------------------------------------------------

def _div_trunc(num: int, den: int) -> int:
    """Integer division truncated toward zero, i.e. C's `/`.

    See the module docstring: Python's floor division biases a signed EWMA.
    """
    if num >= 0:
        return num // den
    return -((-num) // den)


def _ewma(cur_q: int, target_cc: int, dt_s: int, tau_s: int) -> int:
    """One step of a time-aware integer EWMA, in Q`_Q` centi-degrees.

    `dt_s` is clamped to `tau_s` so a long gap moves the estimate all the way
    to the target and never past it.
    """
    if dt_s <= 0:
        return cur_q
    if dt_s > tau_s:
        dt_s = tau_s
    return cur_q + _div_trunc(((target_cc << _Q) - cur_q) * dt_s, tau_s)


def _to_cc(value_q: int) -> int:
    """Q`_Q` -> centi-degrees, rounded."""
    return (value_q + (1 << (_Q - 1))) >> _Q


def _popcount32(v: int) -> int:
    """Population count of a 32-bit word.

    Written as the classic SWAR sequence rather than `bin(v).count('1')`
    because the latter allocates a string, and because `int.bit_count()` is
    Python 3.10+ while this prototype targets 3.9 (contracts/INTERFACES.md
    header).  The C port is `__builtin_popcount`.
    """
    v = v - ((v >> 1) & 0x55555555)
    v = (v & 0x33333333) + ((v >> 2) & 0x33333333)
    v = (v + (v >> 4)) & 0x0F0F0F0F
    return ((v * 0x01010101) >> 24) & 0x3F


def _clamp(value: int, low: int, high: int) -> int:
    if value < low:
        return low
    if value > high:
        return high
    return value


def _finite(x: float) -> bool:
    # bool is a subclass of int; a bool temperature is a decoder bug, not a
    # reading, so reject it rather than silently treating True as 1.0 C.
    if isinstance(x, bool):
        return False
    if not isinstance(x, (int, float)):
        return False
    return math.isfinite(x)


# ---------------------------------------------------------------------------
# the hot path
# ---------------------------------------------------------------------------

def update(state: DetectorState, s: Sample) -> Decision:
    """Fold one sample into `state` and return the current verdict.

    Mutates `state`; performs no I/O; allocates only the returned `Decision`.

    Verdict precedence, highest first, and why:

    1. ``install_unverified`` - an unverified install is indistinguishable
       from a sensor fault, so nothing it says may be counted
       (contracts/INTERFACES.md §5 behaviour 2, 08-roadmap.md §8 risk 4,
       "do not count unverified installs").  It outranks ``sensor_fault``
       because verification gates the trustworthiness of the whole channel,
       including its fault reports; when both apply the reason string says so.
    2. ``sensor_fault`` - tamper, out of range, implausible step, stuck
       channel.  Never a leak (§5 behaviour 3).
    3. ``freeze_risk`` - an absolute threshold needing no learning, and a more
       urgent dispatch than a slow leak (§5 behaviour 4).
    4. ``leak_suspected`` - the dwell test passed and has latched.
    5. ``watch`` / ``quiet``.

    ``watch`` is *not* an alert.  It exists so an operator looking at a device
    can see the detector working, and so the backend has something to put on a
    chart without paging anyone.
    """
    # --- 0. timebase -------------------------------------------------------
    dt_s = 0
    if state.have_last:
        dt_s = s.ts_s - state.last_ts_s
        if dt_s < 0:
            dt_s = 0          # out-of-order replay: no time credit, no rewind
    if s.ts_s > state.last_ts_s or not state.have_last:
        state.last_ts_s = s.ts_s
    state.have_last = True
    if state.samples < _SAMPLES_SATURATE:
        state.samples += 1

    # --- 1. plausibility of the raw reading -------------------------------
    pipe_ok = _finite(s.pipe_c)
    amb_ok = _finite(s.ambient_c)
    nonfinite = not (pipe_ok and amb_ok)

    pipe_cc = _cc(s.pipe_c) if pipe_ok else 0
    amb_cc = _cc(s.ambient_c) if amb_ok else 0

    out_of_range = (not nonfinite) and (
        pipe_cc < SENSOR_MIN_CC or pipe_cc > SENSOR_MAX_CC
        or amb_cc < SENSOR_MIN_CC or amb_cc > SENSOR_MAX_CC)

    step_bad = False
    extreme = False
    if not nonfinite and state.initialised:
        allow_cc = PLAUSIBLE_STEP_CC + (PLAUSIBLE_RATE_CC_PER_MIN * dt_s) // 60
        if allow_cc > PLAUSIBLE_STEP_CEIL_CC:
            allow_cc = PLAUSIBLE_STEP_CEIL_CC
        step_bad = abs(pipe_cc - state.last_pipe_cc) > allow_cc
        # Asymmetric on purpose - see EXTREME_*_DELTA_C. Positive excursion
        # = pipe hotter than its learned baseline, which has no physical
        # mechanism on a cold main; negative = colder, which is the signal.
        excursion_cc = (pipe_cc - amb_cc) - _to_cc(state.base_q)
        extreme = (excursion_cc >= EXTREME_HOT_DELTA_CC
                   or excursion_cc <= -EXTREME_COLD_DELTA_CC)
    state.fault_bits = (((state.fault_bits << 1) | (1 if (step_bad or extreme) else 0))
                        & _WINDOW_MASK)
    noisy = _popcount32(state.fault_bits) >= FAULT_WINDOW_MIN_HITS

    # Stuck channel: the pipe reading has not moved a single LSB for a long
    # time.  Tracked with counters, never a history buffer.
    stuck = False
    if not nonfinite:
        if state.initialised and pipe_cc == state.last_pipe_cc:
            if state.stuck_run < 0xFF:
                state.stuck_run += 1
            state.stuck_span_s = min(state.stuck_span_s + dt_s, STUCK_HARD_SPAN_S)
            if amb_cc < state.stuck_amb_min_cc:
                state.stuck_amb_min_cc = amb_cc
            if amb_cc > state.stuck_amb_max_cc:
                state.stuck_amb_max_cc = amb_cc
        else:
            state.stuck_run = 0
            state.stuck_span_s = 0
            state.stuck_amb_min_cc = amb_cc
            state.stuck_amb_max_cc = amb_cc
        if (state.stuck_run >= STUCK_MIN_SAMPLES
                and state.stuck_span_s >= STUCK_MIN_SPAN_S):
            room_moved = ((state.stuck_amb_max_cc - state.stuck_amb_min_cc)
                          >= STUCK_AMBIENT_MOVE_CC)
            stuck = room_moved or (state.stuck_run >= STUCK_HARD_SAMPLES
                                   and state.stuck_span_s >= STUCK_HARD_SPAN_S)
        state.last_pipe_cc = pipe_cc

    faulted = (bool(s.tamper) or nonfinite or out_of_range or step_bad or extreme
               or noisy or stuck)

    # --- 2. install verification gate -------------------------------------
    if not s.install_verified:
        # Drop every bit of accumulated leak evidence. The invariant
        # "unverified never yields a leak verdict" must survive the sample
        # *after* verification flips true as well, which is why the learner is
        # re-armed rather than merely paused.
        last_baseline_c = _to_cc(state.base_q) / 100.0 if state.initialised else 0.0
        state.dwell_s = 0
        state.clear_s = 0
        state.freeze_s = 0
        state.leak_latched = False
        state.window_bits = 0
        state.initialised = False
        state.warm_s = 0
        state.samples = 1
        delta_c = (pipe_cc - amb_cc) / 100.0 if not nonfinite else 0.0
        return Decision(
            verdict="install_unverified",
            confidence=100,              # a fact from the device, not an inference
            reason=_R_UNVERIFIED_FAULT if faulted else _R_UNVERIFIED,
            delta_c=delta_c,
            baseline_c=last_baseline_c,
        )

    # --- 3. learners ------------------------------------------------------
    delta_cc = pipe_cc - amb_cc if not nonfinite else 0

    if not state.initialised and not faulted:
        state.initialised = True
        state.fast_q = delta_cc << _Q
        state.med_q = delta_cc << _Q
        state.base_q = delta_cc << _Q
        state.mad_q = MAD_INIT_CC << _Q
        state.amb_fast_q = amb_cc << _Q
        state.amb_slow_q = amb_cc << _Q
        state.stuck_amb_min_cc = amb_cc
        state.stuck_amb_max_cc = amb_cc
        state.warm_s = 0

    trigger_cc = _clamp(TRIGGER_K * _to_cc(state.mad_q),
                        TRIGGER_FLOOR_CC, TRIGGER_CEIL_CC)
    baseline_cc = _to_cc(state.base_q)
    depression_cc = baseline_cc - _to_cc(state.fast_q)

    if not faulted and state.initialised:
        state.fast_q = _ewma(state.fast_q, delta_cc, dt_s, TAU_FAST_S)
        state.med_q = _ewma(state.med_q, delta_cc, dt_s, TAU_MED_S)
        state.amb_fast_q = _ewma(state.amb_fast_q, amb_cc, dt_s, TAU_AMB_FAST_S)
        state.amb_slow_q = _ewma(state.amb_slow_q, amb_cc, dt_s, TAU_AMB_SLOW_S)

        # Recompute against the freshly smoothed value: the verdict should
        # reflect this sample, not the one before it.
        depression_cc = baseline_cc - _to_cc(state.fast_q)
        quiescent = abs(depression_cc) < trigger_cc

        # The baseline tracks "normal" quickly during warm-up, slowly in
        # steady state, and only creeps while an excursion is live, so a real
        # leak cannot be absorbed into normality within the days it takes
        # someone to fix it.
        if state.warm_s < WARMUP_S:
            tau_base = TAU_BASE_WARMUP_S
        elif quiescent:
            tau_base = TAU_BASE_S
        else:
            tau_base = TAU_BASE_HELD_S
        state.base_q = _ewma(state.base_q, delta_cc, dt_s, tau_base)

        # The noise floor is learned from quiet time only. Learning it during
        # an excursion would let a long leak raise the device's own threshold
        # above the leak.
        if quiescent:
            state.mad_q = _ewma(state.mad_q, abs(delta_cc - baseline_cc),
                                dt_s, TAU_NOISE_S)

        if state.warm_s < WARMUP_S:
            state.warm_s = min(WARMUP_S, state.warm_s + dt_s)

    # --- 4. dwell integration --------------------------------------------
    # Enter on the smoothed depression, hold on the raw one. See the comment
    # on TRIGGER_HOLD_NUM: this is what separates a cycling appliance from a
    # leak, and it is the single most load-bearing line in the file.
    raw_depression_cc = baseline_cc - delta_cc
    depressed = (state.initialised and not faulted
                 and depression_cc >= trigger_cc
                 and raw_depression_cc >= (trigger_cc * TRIGGER_HOLD_NUM) // TRIGGER_HOLD_DEN)
    state.window_bits = ((state.window_bits << 1) | (1 if depressed else 0)) & _WINDOW_MASK

    # Two reasons to hold the dwell timer instead of crediting it. Both say
    # "this depression has a thermal explanation that is not water": the room
    # is warming and the pipe is lagging it, or the pipe is already warming
    # back up from an earlier draw. Hold rather than reset, so a leak that
    # starts during either is delayed and not missed.
    amb_slew_cc = _to_cc(state.amb_fast_q) - _to_cc(state.amb_slow_q)
    lag_hold = depressed and amb_slew_cc >= AMB_SLEW_HOLD_CC
    recovery_hold = depressed and (_to_cc(state.fast_q) - _to_cc(state.med_q)
                                   >= RECOVERY_HOLD_CC)

    if faulted or lag_hold or recovery_hold:
        pass                                   # freeze the timers
    elif depressed:
        state.dwell_s = min(state.dwell_s + min(dt_s, DT_CREDIT_MAX_S), DWELL_MAX_S)
        state.clear_s = 0
    else:
        state.dwell_s = max(0, state.dwell_s - min(dt_s, DT_CREDIT_MAX_S) * DWELL_RELEASE_MULT)
        state.clear_s = min(state.clear_s + dt_s, CLEAR_S)

    warmed = state.warm_s >= WARMUP_S and state.samples >= WARMUP_MIN_SAMPLES
    confirmations = _popcount32(state.window_bits)

    # A burst sits the pipe at mains temperature; a draw session only pulls it
    # part way. That depth difference is what buys the shorter dwell - and it
    # is a weaker separation than it sounds, which is why burst latency is
    # hours rather than minutes. See DWELL_DEEP_MULT for the measurement and
    # for the continuity alternative that was tried and rejected.
    dwell_required = DWELL_S
    if depression_cc >= DWELL_DEEP_MULT * trigger_cc:
        dwell_required = DWELL_DEEP_S

    if (warmed and not faulted and state.dwell_s >= dwell_required
            and confirmations >= WINDOW_MIN_DEPRESSED
            and state.stuck_run < STUCK_SUSPECT_SAMPLES):
        state.leak_latched = True
    elif state.leak_latched and state.clear_s >= CLEAR_S:
        # Released only after a long clear run, so one noisy sample cannot
        # drop and re-raise the alert and be counted twice.
        state.leak_latched = False

    # --- 5. freeze latch -------------------------------------------------
    # Gated on the *whole* fault set, not just range. A noisy channel throws
    # in-range garbage too, and a reading of 1 C from a sensor that is already
    # known to be lying is not a freeze warning - it is a dispatched van. This
    # was a measured false positive: four of the five nuisance alerts in the
    # first full-fleet run were freeze latches set by `sensor_fault_noisy`
    # samples and surfaced later, once the noise burst stopped being flagged.
    if not faulted:
        # Credit/release on time below the setpoint, mirroring the leak dwell.
        # Release is 1:1 rather than the leak path's 2:1 because a pipe that
        # climbs back over the line has genuinely stopped being at risk.
        if pipe_cc <= FREEZE_WARN_CC:
            state.freeze_s = min(state.freeze_s + min(dt_s, DT_CREDIT_MAX_S),
                                 FREEZE_DWELL_MAX_S)
        elif pipe_cc >= FREEZE_CLEAR_CC:
            state.freeze_s = max(0, state.freeze_s - min(dt_s, DT_CREDIT_MAX_S))

        if state.freeze_s >= FREEZE_DWELL_S:
            state.freeze_latched = True
        elif pipe_cc >= FREEZE_CLEAR_CC and state.freeze_s == 0:
            state.freeze_latched = False

    # --- 6. verdict ------------------------------------------------------
    delta_c = delta_cc / 100.0
    baseline_c = baseline_cc / 100.0

    if faulted:
        if s.tamper:
            reason, conf = _R_TAMPER, 95
        elif nonfinite:
            reason, conf = _R_NONFINITE, 95
        elif out_of_range:
            reason, conf = _R_RANGE, 95
        elif stuck:
            reason, conf = _R_STUCK, 90
        elif noisy:
            reason, conf = _R_NOISY, 85
        elif extreme:
            reason, conf = _R_EXTREME, 80
        else:
            reason, conf = _R_STEP, 60
        return Decision(verdict="sensor_fault", confidence=conf, reason=reason,
                        delta_c=delta_c, baseline_c=baseline_c)

    if state.freeze_latched:
        # How far below the warning line, scaled so 0 C reads as certain.
        margin = FREEZE_WARN_CC - pipe_cc
        conf = _clamp(60 + (margin * 40) // max(1, FREEZE_WARN_CC), 60, 100)
        return Decision(
            verdict="freeze_risk", confidence=conf,
            reason=_R_FREEZE_AND_LEAK if state.leak_latched else _R_FREEZE,
            delta_c=delta_c, baseline_c=baseline_c)

    if state.leak_latched:
        depth = _clamp((depression_cc * 50) // max(1, trigger_cc), 0, 100)
        held = _clamp((state.dwell_s * 50) // dwell_required, 0, 100)
        return Decision(verdict="leak_suspected",
                        confidence=_clamp(50 + (depth + held) // 4, 50, 100),
                        reason=_R_LEAK, delta_c=delta_c, baseline_c=baseline_c)

    if depressed:
        progress = _clamp((state.dwell_s * 40) // dwell_required, 0, 40)
        if lag_hold:
            reason = _R_WATCH_SLEW
        elif recovery_hold:
            reason = _R_WATCH_RECOVERING
        else:
            reason = _R_WATCH
        return Decision(verdict="watch", confidence=10 + progress, reason=reason,
                        delta_c=delta_c, baseline_c=baseline_c)

    return Decision(verdict="quiet",
                    confidence=100 if warmed else 50,
                    reason=_R_QUIET if warmed else _R_WARMUP,
                    delta_c=delta_c, baseline_c=baseline_c)
