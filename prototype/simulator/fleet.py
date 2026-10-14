"""Device, physics and link model for the synthetic leak-sensor fleet.

WHAT THIS IS
    The forward model: given a device description and a schedule of water draws and fault
    episodes, produce the frames that device would actually transmit -- 8 raw bytes per
    contracts/INTERFACES.md §1, wrapped in the §4 The Things Stack envelope, with the §6
    ground-truth label attached. It exists so the whole system is runnable with no hardware,
    and so `detection/` has labelled data to be evaluated against before a single winter of
    real data exists (the bet of ../../08-roadmap.md §7).

    The measured quantity is a clamp-on differential temperature on the incoming water main:
    `pipe_c` from a sensor strapped to the pipe near where the service line enters the
    building, `ambient_c` from a second sensor a few centimetres away in air. §1 transmits
    both; `delta_c = pipe_c - ambient_c` is the signal.

    The physical claim the product rests on, and therefore the thing this model has to get
    right: a hidden leak draws ground water through the main *continuously*, so the pipe sits
    at a persistent small negative `delta_c`. Normal use also pulls the pipe negative, but
    only in bursts. The difference is dwell time, not depth -- a washing machine's fill is as
    deep as a fast leak. See README.md for the numbers.

WHAT THIS DELIBERATELY DOES NOT DO
    - No detection. Nothing here thresholds, scores or classifies; the only "decision" is the
      firmware's `cold_warning` flag and its uplink-rate escalation, which are §1 fields and
      have to be simulated to be transmitted.
    - No third-party packages. Stdlib only (INTERFACES.md §10), which rules out numpy. The
      cost is that the trace is an explicit minute-by-minute Euler loop; the benefit is that
      it runs anywhere Python 3.9 runs and reads like the firmware will.
    - No claim to be validated. This is a hand-built forward model with plausible constants,
      not a fit to measured data. It can rank detector designs and expose a detector that
      only works on toy data; it cannot predict the real nuisance rate. ../../08-roadmap.md
      §7 is explicit that only a real winter settles that.
"""

import base64
import dataclasses
import datetime
import hashlib
import math
import random
import struct
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------- §1 encoding

UPLINK_FPORT = 1
UPLINK_LENGTH = 8
INT16_MIN = -32768
INT16_MAX = 32767

# §1 byte 6
FLAG_INSTALL_VERIFIED = 0x01
FLAG_TAMPER = 0x02
FLAG_COLD_WARNING = 0x04

# §1 byte 7. Spelling matches codec/index.js UPLINK_REASONS so the two agree.
REASON_HEARTBEAT = 0
REASON_THRESHOLD = 1
REASON_BOOT = 2
REASON_INSTALL_PROBE = 3
UPLINK_REASONS = ("heartbeat", "threshold", "boot", "install_probe")


def centi(celsius: float) -> int:
    """Degrees C to centi-degrees, half away from zero, clamped to int16.

    Clamping rather than wrapping: a 16-bit field cannot represent 400 °C, and a wrapped
    value decodes to a plausible-looking negative temperature, which is exactly the kind of
    silent corruption this prototype exists to rule out. Real firmware would clamp too --
    saturating is the only honest thing a fixed-width field can do.
    """
    n = -int(-celsius * 100.0 + 0.5) if celsius < 0 else int(celsius * 100.0 + 0.5)
    return max(INT16_MIN, min(INT16_MAX, n))


def encode_uplink(
    pipe_c: float,
    ambient_c: float,
    battery_mv: int,
    install_verified: bool,
    tamper: bool,
    cold_warning: bool,
    uplink_reason: int,
) -> bytes:
    """The 8 bytes of §1, big-endian. The second independent implementation of that table.

    `codec/index.js` is the first. They are checked against the same golden vectors in
    `test_fleet.py` and `codec/index.test.js`, which is what makes agreement a tested fact
    rather than a hope.
    """
    flags = 0
    if install_verified:
        flags |= FLAG_INSTALL_VERIFIED
    if tamper:
        flags |= FLAG_TAMPER
    if cold_warning:
        flags |= FLAG_COLD_WARNING
    return struct.pack(
        ">hhHBB",
        centi(pipe_c),
        centi(ambient_c),
        max(0, min(65535, int(battery_mv))),
        flags,
        uplink_reason & 0xFF,
    )


def decode_uplink(raw: bytes) -> Dict[str, object]:
    """Inverse of `encode_uplink`, producing the §3 shape. Used by the tests and by `--post`
    dry runs; the backend has its own decoder and must not trust ours (§4)."""
    if len(raw) != UPLINK_LENGTH:
        raise ValueError("expected %d bytes, got %d" % (UPLINK_LENGTH, len(raw)))
    pipe, amb, mv, flags, reason = struct.unpack(">hhHBB", raw)
    return {
        "pipe_c": pipe / 100.0,
        "ambient_c": amb / 100.0,
        # Integer subtraction before the divide, matching codec/index.js: the float form
        # gives -7.149999999999999 for the contract's own §3 example.
        "delta_c": (pipe - amb) / 100.0,
        "battery_mv": mv,
        "flags": {
            "install_verified": bool(flags & FLAG_INSTALL_VERIFIED),
            "tamper": bool(flags & FLAG_TAMPER),
            "cold_warning": bool(flags & FLAG_COLD_WARNING),
        },
        "uplink_reason": (
            UPLINK_REASONS[reason] if reason < len(UPLINK_REASONS) else "reserved_%d" % reason
        ),
    }


def derive_seed(*parts: object) -> int:
    """A stable child seed. `hash()` on a str is salted per process (PYTHONHASHSEED), so using
    it here would make `--seed` reproducible only within one interpreter run."""
    digest = hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


# ------------------------------------------------------------------------------- climate

SECONDS_PER_DAY = 86400

# Outdoor air: a northern-tier US / southern-Canada climate, because that is where the
# freeze peril (A4) and the winter data-collection window of ../../08-roadmap.md §12 are.
_OUTDOOR_MEAN_C = 10.5
_OUTDOOR_AMPLITUDE_C = 13.0
_OUTDOOR_PEAK_DOY = 196.0  # mid-July
_OUTDOOR_DIURNAL_C = 5.5
_OUTDOOR_DIURNAL_PEAK_H = 15.0

# Shallow ground water, which is what a municipal main delivers. It lags air by about five
# weeks and swings at roughly half the amplitude, which puts the annual minimum in late
# February or March rather than January -- and it never freezes. These numbers are chosen to
# sit in the range the Burch-Christensen mains-temperature correlation gives for the northern
# tier: about 4.5 °C at the end of winter, 17.5 °C at the end of summer.
#
# The lag is load-bearing, not decoration. It is why the leak signal is *seasonal*: the
# measurement is driven by `(ground_water - ambient)`, so in October mains water is still
# near 16 °C against a 19 °C basement and even a fast leak barely moves `delta_c`, while in
# February the same leak has a twelve-degree gradient to work with. A detector tuned on
# autumn data is tuned on the weakest signal of the year.
_GW_MEAN_C = 11.0
_GW_AMPLITUDE_C = 6.5
_GW_LAG_DAYS = 38.0


def _day_of_year(ts_s: int) -> float:
    dt = datetime.datetime.fromtimestamp(ts_s, datetime.timezone.utc)
    return dt.timetuple().tm_yday + (ts_s % SECONDS_PER_DAY) / SECONDS_PER_DAY


def _hour_of_day(ts_s: int) -> float:
    return (ts_s % SECONDS_PER_DAY) / 3600.0


def outdoor_daily_mean_c(ts_s: int, weather_c: float = 0.0) -> float:
    """Seasonal outdoor air temperature, before the diurnal swing. `weather_c` is the
    synoptic anomaly (a warm spell, a cold snap), supplied by the caller's random walk."""
    doy = _day_of_year(ts_s)
    seasonal = _OUTDOOR_MEAN_C + _OUTDOOR_AMPLITUDE_C * math.cos(
        2.0 * math.pi * (doy - _OUTDOOR_PEAK_DOY) / 365.25
    )
    return seasonal + weather_c


def outdoor_c(ts_s: int, weather_c: float = 0.0) -> float:
    diurnal = _OUTDOOR_DIURNAL_C * math.cos(
        2.0 * math.pi * (_hour_of_day(ts_s) - _OUTDOOR_DIURNAL_PEAK_H) / 24.0
    )
    return outdoor_daily_mean_c(ts_s, weather_c) + diurnal


def ground_water_c(ts_s: int) -> float:
    """Incoming mains water temperature. Floored at 1 °C: a live main does not freeze."""
    doy = _day_of_year(ts_s)
    gw = _GW_MEAN_C + _GW_AMPLITUDE_C * math.cos(
        2.0 * math.pi * (doy - _OUTDOOR_PEAK_DOY - _GW_LAG_DAYS) / 365.25
    )
    return max(1.0, gw)


def foundation_c(ts_s: int, weather_c: float = 0.0) -> float:
    """Temperature of the foundation wall the first metre of pipe is clamped to.

    A blend of deep ground and outdoor air, because that is what a frost wall is: concrete
    with soil on one face and, above the frost line, outside air a few hundred millimetres
    away. Weighting it towards the air matters -- an all-ground model makes the wall *warmer*
    than an unheated basement's air through the whole of autumn (the ground lags by eight
    weeks), which would invert the sign of the cold-basement offset and delete the scenario.
    That is not a hypothetical: it is what this function did on its first draft.

    This term is the entire reason `cold_basement_winter` exists as a scenario. It puts a
    persistent *negative* `delta_c` on a device with no leak at all, at the same depth as a
    slow leak, which is the hardest false positive in the problem.
    """
    blended = 0.45 * ground_water_c(ts_s) + 0.55 * outdoor_daily_mean_c(ts_s, weather_c)
    # A frost wall does go below zero near the rim joist. The pipe inside it does not freeze
    # instantly, which the latent-heat term in `simulate_device` takes care of.
    return max(-3.0, blended)


# ---------------------------------------------------------------------------- archetypes


@dataclasses.dataclass(frozen=True)
class Archetype:
    """Where the sensor physically lives. §5 requirement 5 says the baseline must adapt per
    device because housing stock varies; these are the variations it has to cope with.

    ambient_base_c    air temperature the space is held at when the weather is mild
    outdoor_coupling  how much of the outdoor swing reaches that air (0 = conditioned)
    diurnal_amp_c     extra daily swing from occupancy and solar gain
    diurnal_peak_h    hour the space is warmest
    wall_k            conduction from the pipe to the foundation, no flow. The whole
                      cold-basement false positive lives in this number.
    tau_flow_s        pipe response while water is moving. Dominated by the clamp and sensor
                      mass, not the water: internal forced convection makes the pipe wall
                      track the water within seconds (h ~ 3 kW/m^2K over 0.055 m^2/m against
                      230 J/K/m of copper gives tau ~ 1.4 s), so what is actually measured is
                      how fast the strapped-on sensor follows the pipe.
    tau_still_s       re-equilibration to air with no flow. Now the standing water dominates:
                      ~1170 J/K per metre of water plus ~230 J/K of copper against natural
                      convection of ~0.5 W/K gives tens of minutes. This asymmetry is why a
                      two-minute draw is still visible three samples later, and it is the
                      reason the nuisance problem is hard rather than trivial.
    """

    name: str
    ambient_base_c: float
    outdoor_coupling: float
    diurnal_amp_c: float
    diurnal_peak_h: float
    wall_k: float
    tau_flow_s: float
    tau_still_s: float


#
# These are calibrated so that mid-winter air at the sensor lands between about 8 and 20 °C.
# That band is not cosmetic. A clamp goes on the incoming main within a metre or two of the
# meter, and any space that actually sat near freezing all winter would have burst its
# plumbing years ago, so such installs do not exist to be sampled. It also decides whether
# the product works at all: the leak signal is `(ground_water - ambient)`, so once a space
# gets as cold as the incoming water -- around 7 °C here in January -- a leak stops pulling
# the pipe down and starts pulling it *up*. An earlier draft of this table had a crawlspace
# at 3.8 °C in January, and in it a fast leak produced a *positive* `delta_c`. That is a true
# consequence of the physics, not a bug, and it is why `crawlspace` is still the marginal
# archetype here: the method degrades in cold spaces in deep winter, and the detector must
# not depend on the sign of `delta_c` alone.
ARCHETYPES: Dict[str, Archetype] = {
    # A finished basement with the furnace in it: warm, stable, almost no outdoor signal.
    "heated_basement": Archetype("heated_basement", 19.0, 0.08, 0.7, 19.0, 0.05, 95.0, 1500.0),
    # A mechanical closet on an upper floor. The easiest device in the fleet.
    "utility_closet": Archetype("utility_closet", 21.0, 0.05, 1.3, 20.0, 0.03, 85.0, 1350.0),
    # Unfinished, unheated, main entering through the frost wall. The hard one: its wall_k is
    # six times the heated basement's, so it carries a standing negative offset all winter.
    "cold_basement": Archetype("cold_basement", 15.0, 0.22, 0.5, 17.0, 0.30, 105.0, 1800.0),
    # Vented crawlspace with heat tape on the main. Tracks outdoors badly; the archetype on
    # which this whole measurement is weakest.
    "crawlspace": Archetype("crawlspace", 14.0, 0.32, 1.6, 16.0, 0.26, 110.0, 1700.0),
    # Attached garage, main run along the heated common wall.
    "garage": Archetype("garage", 17.5, 0.30, 2.6, 16.0, 0.14, 100.0, 1600.0),
}

# Flow -> how much of the pipe's temperature is set by the incoming water. Saturating, not
# linear: at a trickle the water has a long residence time in the service line and the first
# metres of pipe, so it arrives already part-warmed and the sensor barely sees it. That is
# precisely why a slow leak is a small signal rather than a large one.
_COUPLING_MAX = 0.92
_COUPLING_HALF_FLOW = 0.55


def flow_coupling(flow: float) -> float:
    if flow <= 0.0:
        return 0.0
    return _COUPLING_MAX * flow / (flow + _COUPLING_HALF_FLOW)


# -------------------------------------------------------------------------- descriptions


@dataclasses.dataclass
class Draw:
    """One burst of water use. `flow` is a fraction of the service line's capacity."""

    start_s: int
    duration_s: int
    flow: float
    kind: str  # 'tap' | 'toilet' | 'dishwasher' | 'washing_machine' | 'hose' | 'bath' | 'shower'
    scenario: str  # §6 label to attach while this draw dominates


@dataclasses.dataclass
class Episode:
    """A fault or leak that persists. None of these self-heal; a leak keeps leaking and a
    hung I2C bus stays hung until someone visits, which is the point."""

    kind: str  # §6 scenario name
    start_s: int
    end_s: int
    magnitude: float  # leak: flow fraction. freeze: ambient depression in K. faults: unused.


@dataclasses.dataclass
class Gateway:
    """A LoRaWAN gateway. Outages are correlated loss across every device that depends on it,
    which is what makes `f_cnt` gaps bursty instead of Bernoulli -- and bursty gaps are what
    the backend's never-reporting metric (§7) has to not be fooled by."""

    gateway_id: str
    outages: List[Tuple[int, int]] = dataclasses.field(default_factory=list)

    def down_at(self, ts_s: int) -> bool:
        for start_s, end_s in self.outages:
            if start_s <= ts_s < end_s:
                return True
        return False


@dataclasses.dataclass
class Device:
    """Everything that makes one unit different from another unit."""

    device_id: str
    dev_eui: str
    archetype: Archetype
    seed: int

    # Install quality (../../08-roadmap.md §8 risk 4: the self-install failure tail).
    install_verified: bool = True
    clamp_on_hot_line: bool = False  # unverified installs that found the wrong pipe
    dangle_offset_c: float = 0.0  # unverified installs where the clamp is in free air

    # Sensor imperfection. The per-channel calibration offsets are the reason a fixed global
    # delta threshold cannot work: they put a device-specific bias on the signal itself.
    cal_pipe_c: float = 0.0
    cal_ambient_c: float = 0.0
    noise_sigma_c: float = 0.18

    # Power. Li-SOCl2 AA, derated per ../../08-roadmap.md §8 risk 7.
    battery_mah_used: float = 0.0
    battery_mah_usable: float = 1400.0

    # Radio.
    heartbeat_s: int = 900
    escalated_heartbeat_s: int = 300
    link_margin_db: float = 0.0
    gateways: List[Gateway] = dataclasses.field(default_factory=list)
    link_loss_p: float = 0.02

    # Lifecycle.
    boot_offset_s: int = 0
    dark_at_s: Optional[int] = None  # install failure tail: stops transmitting, forever

    # Ambience.
    thermostat_offset_c: float = 0.0
    occupants: int = 3


@dataclasses.dataclass
class DevicePlan:
    """A device plus everything scheduled to happen to it. Built by `scenarios.build_fleet`."""

    device: Device
    draws: List[Draw]
    episodes: List[Episode]


@dataclasses.dataclass
class Frame:
    """One transmitted-and-received uplink, before it is wrapped in the §4 envelope."""

    ts_s: int
    f_cnt: int
    raw: bytes
    gateways: List[str]
    rssi: int
    snr: float
    spreading_factor: int
    scenario: str
    is_true_event: bool
    event_start_s: Optional[int]
    expected_verdict: str
    delta_c: float


# ------------------------------------------------------------------- scenario bookkeeping

# §6 names, plus what §5's VERDICTS should say about each. `watch` is an acceptable answer
# anywhere `quiet` is expected -- the detector is allowed to be curious, it is only not
# allowed to alert. The evaluation harness decides that; the simulator only states intent.
EXPECTED_VERDICT = {
    "quiet": "quiet",
    "appliance_use": "quiet",
    "shower_morning": "quiet",
    "slow_leak": "leak_suspected",
    "fast_leak": "leak_suspected",
    "freeze_event": "freeze_risk",
    "sensor_fault_stuck": "sensor_fault",
    "sensor_fault_noisy": "sensor_fault",
    "install_unverified": "install_unverified",
    "cold_basement_winter": "quiet",
}

# A "true event" is something an operator should be told about. Appliance use and a cold
# basement are real physical facts and completely uninteresting; an unverified install is
# interesting to the installer but, per §5 requirement 2 and risk 4, must never be counted as
# a leak, so it is not a true event either.
TRUE_EVENT = {
    "quiet": False,
    "appliance_use": False,
    "shower_morning": False,
    "slow_leak": True,
    "fast_leak": True,
    "freeze_event": True,
    "sensor_fault_stuck": True,
    "sensor_fault_noisy": True,
    "install_unverified": False,
    "cold_basement_winter": False,
}

SCENARIOS = tuple(EXPECTED_VERDICT.keys())

# Highest first. A leak that starts while the dishwasher is running is still a leak, and an
# unverified install is unverified no matter what else is going on, so it outranks everything.
_PRECEDENCE = (
    "install_unverified",
    "sensor_fault_stuck",
    "sensor_fault_noisy",
    "freeze_event",
    "fast_leak",
    "slow_leak",
    "shower_morning",
    "appliance_use",
    "cold_basement_winter",
    "quiet",
)


def _pick_scenario(active: Sequence[str]) -> str:
    for name in _PRECEDENCE:
        if name in active:
            return name
    return "quiet"


# ------------------------------------------------------------------------- firmware model

FIRMWARE_COLD_WARNING_C = 4.0  # §1 bit2. Sets the flag and escalates the uplink rate.
FIRMWARE_DELTA_ESCALATE_C = -6.0  # a delta this deep is worth spending battery on
FIRMWARE_ESCALATION_MAX_S = 7200  # how long an escalation may last before standing down
_SENSOR_RANGE_C = (-55.0, 125.0)  # TMP117 operating range; beyond this it reports nothing real

# A pipe this cold is at genuine risk of freezing, as distinct from the 4 °C at which the
# firmware raises `cold_warning`. One is a warning, the other is the peril.
FREEZE_LABEL_C = 2.5

# A no-flow structural offset at least this negative is confusable with a slow leak, and so
# is what makes `cold_basement_winter` a scenario rather than a curiosity. Calibrated against
# what the leak scenarios actually produce in this model: a slow leak shifts `delta_c` by
# roughly -2 K from the device's own quiet baseline, so a standing -1 K offset is squarely
# inside the confusion region and a heated basement's -0.8 K is not. Measured from the true
# physics rather than the reported value, so a device's calibration bias cannot change its
# own label.
COLD_PIPE_LABEL_DELTA_C = -1.0

STEP_S = 60  # Euler step for the thermal integration


def ambient_c(dev: Device, ts_s: int, weather_c: float = 0.0) -> float:
    """Air temperature at the sensor: the space's set point, pulled towards outdoors by
    however leaky the space is, plus its own daily swing."""
    a = dev.archetype
    outside = outdoor_c(ts_s, weather_c)
    base = a.ambient_base_c + dev.thermostat_offset_c
    diurnal = a.diurnal_amp_c * math.cos(2.0 * math.pi * (_hour_of_day(ts_s) - a.diurnal_peak_h) / 24.0)
    return base + a.outdoor_coupling * (outside - base) + diurnal


def _battery_mv(dev: Device, mah_used: float, air_c: float, jitter: float) -> int:
    """Loaded terminal voltage of a Li-SOCl2 AA during a transmit pulse.

    Two things matter and both are in the risk register. Depth of discharge gives the flat
    plateau and then the knee, so a fleet dashboard can see a device coming to end of life.
    Temperature gives the pulse sag: cell impedance climbs steeply below 0 °C, so the same
    cell reads 200 mV lower in an unheated crawlspace in February than in a utility closet.
    ../../08-roadmap.md §8 risk 7 says measure this rather than model it; this is the model
    standing in until there is something measured.

    `mah_used` is passed in rather than read off the device, so that running a device forward
    does not consume its battery permanently. It used to mutate `Device`, which made a second
    pass over the same fleet -- exactly what the CLI does to print its summary after writing
    the file -- produce slightly different voltages from the first. A simulator whose output
    depends on how many times you have looked at it is not reproducible.
    """
    dod = min(1.0, mah_used / dev.battery_mah_usable)
    ocv = 3.66 - 0.10 * dod - 0.55 * max(0.0, dod - 0.85) / 0.15
    sag = 0.08 + 0.004 * max(0.0, 15.0 - air_c) + 0.0012 * max(0.0, 5.0 - air_c) ** 2
    return max(0, min(65535, int(round((ocv - sag) * 1000.0 + jitter))))


# Energy per transmitted frame, mAh: ~371 ms of airtime at SF10/125 kHz and 20 dBm (~120 mA)
# plus two RX windows plus two TMP117 single-shot conversions. Sleep is charged separately.
_MAH_PER_FRAME = 0.014
_SLEEP_UA = 2.0


def _link_quality(dev: Device, rng: random.Random) -> Tuple[int, float, int]:
    """RSSI, SNR and spreading factor. US915 uplinks at 125 kHz only reach SF10 (DR0), so
    SF11/SF12 are not available however bad the link is -- a detail that matters because it
    caps what ADR can do for a basement device and therefore caps coverage."""
    rssi = int(round(-98.0 + dev.link_margin_db + rng.gauss(0.0, 3.5)))
    rssi = max(-137, min(-55, rssi))
    snr = round(max(-20.0, min(12.0, (rssi + 100.0) * 0.55 + rng.gauss(0.0, 1.8))), 1)
    if rssi > -95:
        sf = 7
    elif rssi > -105:
        sf = 8
    elif rssi > -113:
        sf = 9
    else:
        sf = 10
    return rssi, snr, sf


# ------------------------------------------------------------------------ the trace model


def simulate_device(plan: DevicePlan, start_s: int, days: int) -> Iterator[Frame]:
    """Run one device forward and yield the frames that were transmitted *and received*.

    Lost frames are not yielded but still consume an `f_cnt`, because that is what a real
    device does: the frame counter belongs to the device, so loss shows up at the backend as
    a gap in `f_cnt` rather than as missing time. §1 says loss accounting uses `f_cnt`; this
    is what gives it something to account for.
    """
    dev = plan.device
    rng = random.Random(dev.seed ^ 0x5EED_F00D)
    end_s = start_s + days * SECONDS_PER_DAY

    draws = sorted(plan.draws, key=lambda d: d.start_s)
    draw_idx = 0
    live_draws: List[Draw] = []

    # Synoptic weather: an AR(1) walk on the daily anomaly, so cold snaps last days rather
    # than minutes and `cold_basement_winter` has weather to be modulated by.
    weather_c = rng.gauss(0.0, 2.0)
    weather_day = -1

    f_cnt = 0
    boot_s = start_s + dev.boot_offset_s
    t = boot_s
    # Start the pipe at its no-flow equilibrium rather than at ambient, so the first samples
    # are not a startup transient the detector has to learn through.
    amb0 = ambient_c(dev, t, weather_c)
    pipe_c = amb0 + dev.archetype.wall_k * (foundation_c(t, weather_c) - amb0)
    stuck_value_c: Optional[float] = None
    next_emit_s = boot_s
    sent_boot = False
    sent_probe = False
    escalation_until_s = 0
    escalation_rearm_s = 0
    escalate_cond_prev = False
    cold_run_start_s: Optional[int] = None
    # Local, not a field on the device: see `_battery_mv`.
    mah_used = dev.battery_mah_used

    while t < end_s:
        day = (t - start_s) // SECONDS_PER_DAY
        if day != weather_day:
            weather_day = day
            weather_c = 0.82 * weather_c + rng.gauss(0.0, 1.9)

        # --- which episodes are running
        leak_flow = 0.0
        freeze_depression = 0.0
        episode_active: List[Tuple[str, int]] = []
        for ep in plan.episodes:
            if not (ep.start_s <= t < ep.end_s):
                continue
            episode_active.append((ep.kind, ep.start_s))
            if ep.kind in ("slow_leak", "fast_leak"):
                # Leaks do not switch on instantly: a pinhole opens and the flow establishes
                # over an hour or two as the surrounding soil saturates.
                ramp = min(1.0, (t - ep.start_s) / 5400.0)
                leak_flow += ep.magnitude * ramp
            elif ep.kind == "freeze_event":
                freeze_depression = _freeze_depression(ep, t)

        in_freeze = any(k == "freeze_event" for k, _ in episode_active)

        # --- water draws. Suppressed during a freeze event: the archetypal freeze claim is an
        # unoccupied property, and nobody is running a dishwasher in it.
        while draw_idx < len(draws) and draws[draw_idx].start_s <= t:
            if not in_freeze:
                live_draws.append(draws[draw_idx])
            draw_idx += 1
        live_draws = [d for d in live_draws if d.start_s + d.duration_s > t - dev.archetype.tau_still_s * 3]

        use_flow = 0.0
        draw_active: List[Tuple[str, int]] = []
        for d in live_draws:
            if d.start_s <= t < d.start_s + d.duration_s:
                use_flow += d.flow
                draw_active.append((d.scenario, d.start_s))
            elif d.start_s + d.duration_s <= t < d.start_s + d.duration_s + dev.archetype.tau_still_s * 2:
                # The decay tail still carries the draw's label: a sample taken 20 minutes
                # after a shower is a shower artefact, not a quiet sample, and calling it
                # quiet would be scoring the detector against a lie.
                draw_active.append((d.scenario, d.start_s))

        flow = min(1.0, use_flow + leak_flow)

        # --- thermal step
        ambient_true = ambient_c(dev, t, weather_c) - freeze_depression
        coupling = flow_coupling(flow)
        w_water = coupling
        w_wall = dev.archetype.wall_k * (1.0 - coupling)
        w_air = 1.0 - w_water - w_wall
        soil = foundation_c(t, weather_c) - freeze_depression * 0.35
        target = w_air * ambient_true + w_wall * soil + w_water * ground_water_c(t)

        if dev.install_verified:
            tau = dev.archetype.tau_flow_s if flow > 0.02 else dev.archetype.tau_still_s
        else:
            # The clamp is not on the pipe, so there is no pipe to have a time constant. It is
            # a lump of plastic in moving air, plus whatever it is actually touching.
            tau = 400.0
            if dev.clamp_on_hot_line:
                # Found the hot-water line instead. Reads high, and *rises* on a draw, which
                # is the inverse of the leak signature -- a detector keying on magnitude
                # rather than sign will happily call this a leak.
                target = ambient_true + dev.dangle_offset_c + 22.0 * coupling
            else:
                target = ambient_true + dev.dangle_offset_c

        step = 1.0 - math.exp(-STEP_S / tau)
        delta_to_target = target - pipe_c
        if pipe_c < 0.5 and delta_to_target < 0:
            # Latent heat of the standing water column. The pipe wall can go below zero but
            # it gets there slowly, and that plateau just above freezing is the signature a
            # freeze alert (A4) actually has to fire on.
            delta_to_target *= 0.12
        pipe_c += delta_to_target * step

        # --- emit?
        if t >= next_emit_s and (dev.dark_at_s is None or t < dev.dark_at_s):
            fault_active = [k for k, _ in episode_active if k.startswith("sensor_fault")]
            pipe_meas = pipe_c + dev.cal_pipe_c + rng.gauss(0.0, dev.noise_sigma_c)
            amb_meas = ambient_true + dev.cal_ambient_c + rng.gauss(0.0, dev.noise_sigma_c * 0.8)
            tamper = False

            if not dev.install_verified:
                # An unverified install is noisy as well as wrong: the clamp moves, the air
                # moves, nothing is in thermal contact with anything.
                pipe_meas += rng.gauss(0.0, 0.9)

            if "sensor_fault_stuck" in fault_active:
                # A hung I2C bus or a one-shot conversion that was never re-armed: the driver
                # keeps returning the last successful read, bit for bit, forever. Ambient
                # keeps moving, so a *fake* delta grows all by itself -- which is why this
                # has to be caught as a fault and not as a slowly worsening leak.
                if stuck_value_c is None:
                    stuck_value_c = pipe_meas
                pipe_meas = stuck_value_c
            elif "sensor_fault_noisy" in fault_active:
                roll = rng.random()
                if roll < 0.40:
                    # A step this big in 5 minutes is thermally impossible on a pipe with
                    # water in it -- §5 requirement 3's "faster than thermal mass allows".
                    pipe_meas += rng.choice((-1.0, 1.0)) * rng.uniform(7.0, 28.0)
                elif roll < 0.52:
                    # A dead or shorted sensor rails out of the TMP117's own range.
                    pipe_meas = rng.choice((_SENSOR_RANGE_C[0] - rng.uniform(0, 12), 97.0 + rng.uniform(0, 20)))
                if rng.random() < 0.18:
                    tamper = True  # the clamp is loose, which is often what "noisy" means

            cold_warning = pipe_meas < FIRMWARE_COLD_WARNING_C

            pipe_centi = centi(pipe_meas)
            amb_centi = centi(amb_meas)
            delta_c = (pipe_centi - amb_centi) / 100.0

            # Rate escalation is latched, time-boxed and rate-limited. A device in an
            # unheated basement sits below the cold-warning threshold for weeks, and
            # escalating for all of it would flatten the cell in a season and triple the
            # ingest volume for no new information. The re-arm guard matters just as much: a
            # noisy sensor crosses the threshold on half its samples, so without it the fault
            # pins the radio at the escalated rate indefinitely -- which is both a real
            # firmware bug class and, measured here, 23% of the whole dataset coming from one
            # broken device. Real firmware budgets escalation; so does this.
            escalate_cond = cold_warning or delta_c <= FIRMWARE_DELTA_ESCALATE_C
            if escalate_cond and not escalate_cond_prev and t >= escalation_rearm_s:
                escalation_until_s = t + FIRMWARE_ESCALATION_MAX_S
                escalation_rearm_s = t + 4 * FIRMWARE_ESCALATION_MAX_S
            escalate_cond_prev = escalate_cond
            escalated = escalate_cond and t < escalation_until_s

            if not sent_boot:
                reason = REASON_BOOT
            elif not sent_probe:
                reason = REASON_INSTALL_PROBE  # §2: the probe result frame
            elif escalated:
                reason = REASON_THRESHOLD
            else:
                reason = REASON_HEARTBEAT

            mah_used += _MAH_PER_FRAME + _SLEEP_UA * (dev.heartbeat_s / 3600.0) / 1000.0
            raw = encode_uplink(
                pipe_c=pipe_centi / 100.0,
                ambient_c=amb_centi / 100.0,
                battery_mv=_battery_mv(dev, mah_used, ambient_true, rng.gauss(0.0, 6.0)),
                install_verified=dev.install_verified,
                tamper=tamper,
                cold_warning=cold_warning,
                uplink_reason=reason,
            )

            # --- ground truth. Labelled from the physics that was simulated, never from the
            # intent behind it: if a device happens to sit at a near-freezing pipe
            # temperature without a scheduled freeze episode, that is still a freeze risk and
            # scoring a correct freeze alert against it as a false positive would be a lie.
            labelled: List[Tuple[str, int]] = list(episode_active) + list(draw_active)
            if not dev.install_verified:
                labelled.append(("install_unverified", boot_s))
            benign = not any(
                k in ("slow_leak", "fast_leak", "sensor_fault_stuck", "sensor_fault_noisy")
                for k, _ in labelled
            )
            if pipe_meas < FREEZE_LABEL_C and dev.install_verified and benign:
                if cold_run_start_s is None:
                    cold_run_start_s = t
                labelled.append(("freeze_event", cold_run_start_s))
            else:
                if pipe_meas >= FREEZE_LABEL_C + 1.0:
                    cold_run_start_s = None
            if (
                benign
                and dev.install_verified
                and not draw_active
                and _structural_delta_c(dev, t, weather_c) <= COLD_PIPE_LABEL_DELTA_C
            ):
                labelled.append(("cold_basement_winter", boot_s))

            scenario = _pick_scenario([k for k, _ in labelled])
            event_start_s = None
            for kind, s0 in labelled:
                if kind == scenario:
                    event_start_s = s0 if event_start_s is None else min(event_start_s, s0)

            force_delivery = not sent_boot
            got, rx_gateways = _receive(dev, t, rng)
            if force_delivery and not got:
                # The first uplink after a join is sent confirmed and retried, so in practice
                # it arrives or the device never joined at all. Forcing it also keeps the
                # JSONL self-describing: a unit whose clamp falls off on day two still appears
                # in the stream once, which is what gives §7's `pct_never_reporting` a
                # denominator without a separate provisioning manifest.
                got, rx_gateways = True, [dev.gateways[0].gateway_id]
            if got:
                rssi, snr, sf = _link_quality(dev, rng)
                yield Frame(
                    ts_s=t,
                    f_cnt=f_cnt,
                    raw=raw,
                    gateways=rx_gateways,
                    rssi=rssi,
                    snr=snr,
                    spreading_factor=sf,
                    scenario=scenario,
                    is_true_event=TRUE_EVENT[scenario],
                    event_start_s=event_start_s,
                    expected_verdict=EXPECTED_VERDICT[scenario],
                    delta_c=delta_c,
                )
            f_cnt += 1
            if reason == REASON_INSTALL_PROBE:
                sent_probe = True
            sent_boot = True
            next_emit_s = t + (dev.escalated_heartbeat_s if escalated else dev.heartbeat_s)

        t += STEP_S


def _freeze_depression(ep: Episode, t: int) -> float:
    """Ambient depression during a freeze event: six hours down, a plateau, twelve hours back.

    The shape matters. A freeze claim is a furnace failure or an open crawlspace vent during a
    cold snap, and the diagnostic signature is a *slow* descent of both channels together with
    `delta_c` staying near zero -- so the alert has to come from absolute `pipe_c`, not from
    the differential the rest of the system runs on.
    """
    ramp_s = 6 * 3600
    recover_s = 12 * 3600
    into = t - ep.start_s
    left = ep.end_s - t
    if into < ramp_s:
        return ep.magnitude * (into / ramp_s)
    if left < recover_s:
        return ep.magnitude * max(0.0, left / recover_s)
    return ep.magnitude


def _structural_delta_c(dev: Device, ts_s: int, weather_c: float) -> float:
    """The `delta_c` this device would show with no flow at all, from conduction to the
    foundation alone.

    This is the whole `cold_basement_winter` false positive in one line. An unfinished
    basement with the main entering through the frost wall has `wall_k` around 0.3 and, in
    February, a foundation five degrees colder than its own air -- so the pipe sits 1.5-2 K
    below ambient permanently, with no leak, which is the same depth as a slow leak. A
    finished basement at `wall_k` 0.05 shows a third of that and stays below the labelling
    threshold.

    It is also why §5 requirement 5 insists the baseline adapts per device: this offset is
    stable over weeks and arrives on day one, whereas a leak steps the baseline at a
    knowable moment. That difference is what makes the label learnable rather than unfair.
    """
    ambient = ambient_c(dev, ts_s, weather_c)
    return dev.archetype.wall_k * (foundation_c(ts_s, weather_c) - ambient)


def _receive(dev: Device, ts_s: int, rng: random.Random) -> Tuple[bool, List[str]]:
    """Was the frame heard, and by which gateways?

    A frame is received if any gateway hears it, so a device with two gateways in range is
    far more reliable than one with a single marginal link -- which is the whole economic
    argument for the gateway in ../../04-connectivity.md.
    """
    heard: List[str] = []
    for gw in dev.gateways:
        if gw.down_at(ts_s):
            continue
        if rng.random() >= dev.link_loss_p:
            heard.append(gw.gateway_id)
    return (len(heard) > 0), heard


# ----------------------------------------------------------------------------- envelopes


def iso_utc(ts_s: int) -> str:
    """§4 uses `2026-10-14T03:00:00Z`. Real TTS emits nanoseconds; the contract's format is
    what the backend was written to parse, so the contract wins."""
    return datetime.datetime.fromtimestamp(ts_s, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def envelope(dev: Device, frame: Frame, include_label: bool = True) -> Dict[str, object]:
    """The §4 The Things Stack v3 webhook object, plus the §6 `_label` block.

    `_label` carries the four keys §6 requires and four more that cost nothing and save the
    evaluation harness from re-deriving them. §4 says extra fields must be ignored rather
    than rejected, and §6 says the backend ignores `_label` entirely, so posting it at full
    size is also a live test that the backend really does ignore what it should.
    """
    msg: Dict[str, object] = {
        "f_port": UPLINK_FPORT,
        "f_cnt": frame.f_cnt,
        "frm_payload": base64.b64encode(frame.raw).decode("ascii"),
        "rx_metadata": [
            {
                "gateway_ids": {"gateway_id": gw},
                "rssi": frame.rssi - (0 if i == 0 else 4 + i * 3),
                "snr": round(frame.snr - (0.0 if i == 0 else 1.5 + i), 1),
            }
            for i, gw in enumerate(frame.gateways)
        ],
        "settings": {
            "data_rate": {"lora": {"spreading_factor": frame.spreading_factor, "bandwidth": 125000}}
        },
    }
    out: Dict[str, object] = {
        "end_device_ids": {"device_id": dev.device_id, "dev_eui": dev.dev_eui},
        "received_at": iso_utc(frame.ts_s),
        "uplink_message": msg,
    }
    if include_label:
        out["_label"] = {
            "scenario": frame.scenario,
            "is_true_event": frame.is_true_event,
            "event_start_s": frame.event_start_s,
            "device_id": dev.device_id,
            # Additive; ignored by the backend.
            "expected_verdict": frame.expected_verdict,
            "delta_c": frame.delta_c,
            "archetype": dev.archetype.name,
            "install_verified": dev.install_verified,
        }
    return out
