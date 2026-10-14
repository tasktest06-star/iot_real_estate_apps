"""Scenario generators: what happens to each device, and when.

WHAT THIS IS
    The composition layer. `fleet.py` knows how a pipe responds to water and weather;
    this module decides which devices exist, where they are installed, how badly, how often
    somebody runs the washing machine, and which of them develop the ten conditions
    contracts/INTERFACES.md §6 requires as ground truth for the P1 gate.

    The ten §6 scenarios fall into three kinds, and keeping them separate is what makes the
    labels honest:

      background  `quiet`, `appliance_use`, `shower_morning` -- present on every healthy
                  device all the time. Nobody schedules these; they are the nuisance floor
                  the alert has to clear, and ../../08-roadmap.md §8 risk 2 says that floor
                  is what destroys insurer trust.
      episodes    `slow_leak`, `fast_leak`, `freeze_event`, `sensor_fault_stuck`,
                  `sensor_fault_noisy` -- things that start at a moment and then persist.
      properties  `install_unverified`, `cold_basement_winter` -- true of the installation
                  from day one, not events at all. Both are false positives by construction:
                  §5 requirement 2 and risk 4 say an unverified install must never be counted
                  as a leak, and a cold basement is a cold basement.

WHAT THIS DELIBERATELY DOES NOT DO
    - It does not choose the hard cases to be winnable. `slow_leak` is generated at the same
      depth as `cold_basement_winter`'s structural offset because that is what the physics
      gives; if a detector cannot separate them on dwell time, that is a real finding, not a
      simulator bug to be tuned away.
    - It does not model repairs, alert responses or operator behaviour. A leak here runs until
      the end of the window. The one exception is a device that has several stacked episodes,
      where the earlier one ends when the next begins, because two conditions at once would
      make the single `scenario` label of §6 a lie.
    - It does not model flow rate in litres. `flow` is a dimensionless fraction of the service
      line's capacity, which is all the thermal model needs and all that could be calibrated
      without a flow meter on a real install.
"""

import dataclasses
import random
import sys
from typing import Dict, List, Sequence, Tuple

from . import fleet
from .fleet import Device, DevicePlan, Draw, Episode, Gateway, SECONDS_PER_DAY

# ------------------------------------------------------------------------------- fixtures


@dataclasses.dataclass(frozen=True)
class Fixture:
    """One kind of water draw. `bursts` is the fill cycle count: a dishwasher takes water two
    or three times with several minutes of washing in between, which is why it reads as a
    cluster of excursions rather than one, and why it is harder to dismiss than a tap."""

    kind: str
    weight: float
    bursts: Tuple[int, int]
    duration_s: Tuple[int, int]
    flow: Tuple[float, float]
    gap_s: Tuple[int, int]
    summer_only: bool = False


FIXTURES: Tuple[Fixture, ...] = (
    Fixture("tap", 0.40, (1, 1), (12, 55), (0.20, 0.50), (0, 0)),
    Fixture("toilet", 0.26, (1, 1), (45, 80), (0.35, 0.58), (0, 0)),
    Fixture("dishwasher", 0.08, (2, 3), (55, 95), (0.48, 0.70), (420, 900)),
    Fixture("washing_machine", 0.08, (3, 4), (100, 185), (0.70, 0.95), (300, 720)),
    Fixture("bath", 0.05, (1, 1), (240, 430), (0.72, 0.90), (0, 0)),
    Fixture("shower_offpeak", 0.09, (1, 1), (300, 620), (0.65, 0.88), (0, 0)),
    # Outdoor tap: the long summer nuisance. Twenty minutes of garden hose is a deeper and
    # far longer excursion than anything indoors and is the single most leak-like benign
    # event in the dataset.
    Fixture("hose", 0.04, (1, 1), (420, 1500), (0.55, 0.90), (0, 0), summer_only=True),
)

# Relative water-use intensity by hour, local time. Two peaks, a deep trough overnight. The
# overnight trough is what makes a leak detectable at all: it is the only window in which a
# healthy house is reliably quiet, and a detector that uses it is using real structure.
HOUR_WEIGHTS: Tuple[float, ...] = (
    0.18, 0.08, 0.06, 0.06, 0.15, 0.55, 1.60, 2.20,
    1.50, 1.00, 0.90, 0.95, 1.05, 0.85, 0.80, 0.95,
    1.35, 1.90, 2.10, 1.85, 1.45, 1.00, 0.60, 0.30,
)

# §6 episode scenarios, every one of which must appear in any run (the P1 ground truth).
REQUIRED_EPISODES: Tuple[str, ...] = (
    "slow_leak",
    "fast_leak",
    "freeze_event",
    "sensor_fault_stuck",
    "sensor_fault_noisy",
)

# Ordering when several episodes are stacked on one device: a time-boxed freeze first, then a
# leak, then a sensor fault. That reads as a plausible service history -- a cold snap, later a
# leak, and a clamp knocked loose by whoever came to fix it.
_EPISODE_ORDER = {
    "freeze_event": 0,
    "slow_leak": 1,
    "fast_leak": 1,
    "sensor_fault_stuck": 2,
    "sensor_fault_noisy": 3,
}

# Prevalence. This is the one place the dataset is knowingly unrealistic, so it is worth
# being blunt about it.
#
# `_REQUIRED_SHARE` of the eligible devices carry each of the five episode kinds, and
# `_EXTRA_EPISODE_P` of the rest develop something at random. At the default fleet size that
# puts roughly a third of devices in some abnormal state over a month -- three orders of
# magnitude above a real portfolio, where a monitored property has a water-damage claim every
# few hundred house-years (../../01-market-and-insurance-case.md).
#
# The reason to do it anyway: §7's `true_event_recall` and `median_time_to_detection_s` need
# positives per class, and two events is not a median. The reason it is safe: both of those
# metrics, and `nuisance_alerts_per_device_year` -- the actual P1 exit gate -- are rates over
# their own denominators and do not move with prevalence. `precision` does. **Precision
# measured on a default run is therefore optimistic and must not be quoted as a forecast.**
# `--event-rate 0` drops to the forced coverage minimum if a realistic mix is wanted instead.
_REQUIRED_SHARE = 0.06
_EXTRA_EPISODE_P = 0.08
_EXTRA_EPISODE_WEIGHTS = (
    ("slow_leak", 0.34),
    ("fast_leak", 0.14),
    ("freeze_event", 0.18),
    ("sensor_fault_stuck", 0.17),
    ("sensor_fault_noisy", 0.17),
)

_ARCHETYPE_WEIGHTS = (
    ("heated_basement", 0.34),
    ("cold_basement", 0.22),
    ("utility_closet", 0.20),
    ("crawlspace", 0.14),
    ("garage", 0.10),
)

# US household size distribution, roughly. It only matters because it sets how many draws a
# day the nuisance floor is made of.
_OCCUPANT_WEIGHTS = (("1", 0.18), ("2", 0.30), ("3", 0.24), ("4", 0.18), ("5", 0.10))

# Minimum fleet for which all ten §6 scenarios are guaranteed: one device carries the
# unverified install, one is left episode-free so the cold-basement false positive is
# observable, and the remaining three stack the five episodes between them.
MIN_DEVICES_FOR_FULL_COVERAGE = 5
MIN_DAYS_FOR_FULL_COVERAGE = 2


@dataclasses.dataclass
class FleetSpec:
    """Everything needed to run the fleet forward, plus the run parameters it was built for."""

    plans: List[DevicePlan]
    gateways: List[Gateway]
    start_s: int
    days: int
    seed: int
    notes: List[str] = dataclasses.field(default_factory=list)


# ------------------------------------------------------------------------------ utilities


def _weighted_choice(rng: random.Random, pairs: Sequence[Tuple[str, float]]) -> str:
    total = sum(w for _, w in pairs)
    r = rng.random() * total
    for name, w in pairs:
        r -= w
        if r <= 0:
            return name
    return pairs[-1][0]


def _weighted_hour(rng: random.Random) -> int:
    total = sum(HOUR_WEIGHTS)
    r = rng.random() * total
    for h, w in enumerate(HOUR_WEIGHTS):
        r -= w
        if r <= 0:
            return h
    return 23


def _is_summer(ts_s: int) -> bool:
    return fleet.outdoor_daily_mean_c(ts_s) > 16.0


# --------------------------------------------------------------------------------- draws


def household_draws(rng: random.Random, dev: Device, start_s: int, days: int) -> List[Draw]:
    """Ordinary indoor water use: the `appliance_use` scenario.

    These are the events that must not alarm. Their depth is not the discriminator -- a
    washing machine's fill saturates the pipe just as thoroughly as a burst supply line -- so
    anything that separates them from a leak has to be built on how long they last and how
    often they repeat. That is the nuisance problem of risk 2, stated as data.
    """
    draws: List[Draw] = []
    per_day = 2.5 + 2.6 * dev.occupants
    for day in range(days):
        day0 = start_s + day * SECONDS_PER_DAY
        n = int(round(per_day * rng.uniform(0.7, 1.3)))
        for _ in range(n):
            summer = _is_summer(day0)
            choices = [(f.kind, f.weight) for f in FIXTURES if summer or not f.summer_only]
            kind = _weighted_choice(rng, choices)
            fx = next(f for f in FIXTURES if f.kind == kind)
            hour = _weighted_hour(rng)
            t0 = day0 + hour * 3600 + rng.randrange(0, 3600)
            flow = rng.uniform(*fx.flow)
            n_bursts = rng.randint(*fx.bursts)
            cursor = t0
            for _b in range(n_bursts):
                duration = rng.randint(*fx.duration_s)
                draws.append(
                    Draw(
                        start_s=cursor,
                        duration_s=duration,
                        flow=flow,
                        kind=fx.kind,
                        scenario="appliance_use",
                    )
                )
                if fx.gap_s[1] > 0:
                    cursor += duration + rng.randint(*fx.gap_s)
    return draws


def morning_showers(rng: random.Random, dev: Device, start_s: int, days: int) -> List[Draw]:
    """The `shower_morning` scenario: a long draw, at the same time most days.

    This is the nuisance that looks most like a leak and is the easiest to dismiss, because
    it is both long enough to saturate the pipe *and* repeating on a daily period. A detector
    that learns a time-of-day profile gets this one for free; a detector that only thresholds
    on depth and dwell will fire on it every weekday morning, which at 0.5 nuisance alerts
    per device-year (§7) is roughly 500 times the entire annual budget.
    """
    base_hour = rng.uniform(6.0, 8.4)
    second_p = 0.45 if dev.occupants >= 2 else 0.0
    draws: List[Draw] = []
    for day in range(days):
        if rng.random() > 0.82:  # away, or did not shower
            continue
        day0 = start_s + day * SECONDS_PER_DAY
        t0 = day0 + int((base_hour + rng.gauss(0.0, 0.42)) * 3600)
        for i in range(2 if rng.random() < second_p else 1):
            start = t0 + (0 if i == 0 else rng.randint(1200, 3000))
            draws.append(
                Draw(
                    start_s=start,
                    duration_s=rng.randint(420, 780),
                    flow=rng.uniform(0.66, 0.90),
                    kind="shower",
                    scenario="shower_morning",
                )
            )
    return draws


# ------------------------------------------------------------------------------- episodes


def _coldest_start(start_s: int, earliest_s: int, latest_s: int) -> int:
    """Pick the coldest day in a window, for scheduling a freeze event where one could
    actually happen. A freeze alert generated in July would be a simulator artefact, and a
    detector tuned against artefacts is worse than no detector."""
    best_t = earliest_s
    best_c = None
    t = earliest_s
    while t < max(earliest_s + 1, latest_s):
        c = fleet.outdoor_daily_mean_c(t)
        if best_c is None or c < best_c:
            best_c, best_t = c, t
        t += SECONDS_PER_DAY
    return best_t


def make_episode(
    rng: random.Random, kind: str, window_start_s: int, window_end_s: int, run_start_s: int, dev: Device
) -> Episode:
    """One episode, confined to `[window_start_s, window_end_s)`.

    Leaks and sensor faults run to the end of their window because neither self-heals. A
    freeze event is time-boxed because the weather is.
    """
    span = max(SECONDS_PER_DAY // 4, window_end_s - window_start_s)
    if kind == "freeze_event":
        # A freeze claim is a furnace failure, a tripped breaker or an open crawlspace vent
        # during a cold snap, so the depression is measured against *this device's own* air,
        # not against outdoors: a heated basement has to fall nearly twenty degrees to put
        # its pipe near zero, an unheated crawlspace barely five. Scheduling it on the
        # coldest day available keeps it a plausible event rather than an artefact.
        latest = max(window_start_s, window_end_s - 20 * 3600)
        start = _coldest_start(run_start_s, window_start_s, latest)
        duration = min(int(span * 0.8), rng.randint(18 * 3600, 40 * 3600))
        magnitude = min(34.0, max(6.0, fleet.ambient_c(dev, start) + 1.0 + rng.uniform(0.0, 4.0)))
        return Episode(kind, start, start + max(6 * 3600, duration), magnitude)

    start = window_start_s + rng.randrange(0, max(1, int(span * 0.25)))
    if kind == "slow_leak":
        # A weeping joint or a slab pinhole: continuous, and small enough that the pipe only
        # moves one or two degrees. In winter it is about -2 K, in summer about -1 K, because
        # the signal is proportional to how far the mains water is below room temperature --
        # so the same leak is roughly half as visible in August.
        return Episode(kind, start, window_end_s, rng.uniform(0.07, 0.16))
    if kind == "fast_leak":
        # A failed supply line or a split pipe behind a wall, running unseen.
        return Episode(kind, start, window_end_s, rng.uniform(0.35, 0.60))
    if kind in ("sensor_fault_stuck", "sensor_fault_noisy"):
        return Episode(kind, start, window_end_s, 0.0)
    raise ValueError("unknown episode kind: %r" % (kind,))


def _assign_episodes(
    rng: random.Random, kinds: Sequence[str], start_s: int, days: int, dev: Device
) -> List[Episode]:
    """Lay several episodes out back to back on one device, in a plausible order.

    Non-overlapping on purpose: §6 gives each sample exactly one `scenario`, so two live
    conditions at once would mean the label hides one of them, and a detector would be
    penalised for correctly spotting the hidden one.
    """
    if not kinds:
        return []
    ordered = sorted(kinds, key=lambda k: (_EPISODE_ORDER[k], k))
    run_end_s = start_s + days * SECONDS_PER_DAY
    # Leave the first chunk of the window event-free: the detector needs a stretch of normal
    # behaviour to learn a baseline from before anything happens to it (§5 requirement 5),
    # and time-to-detection (§7) is meaningless without a clean "before".
    lead_s = int(days * SECONDS_PER_DAY * 0.18)
    first_s = start_s + max(6 * 3600, lead_s)
    span = max(6 * 3600, (run_end_s - first_s) // len(ordered))
    episodes: List[Episode] = []
    for i, kind in enumerate(ordered):
        w0 = first_s + i * span
        w1 = run_end_s if i == len(ordered) - 1 else first_s + (i + 1) * span
        if w0 >= run_end_s:
            break
        episodes.append(make_episode(rng, kind, w0, min(w1, run_end_s), start_s, dev))
    return episodes


# ---------------------------------------------------------------------------- fleet build


def _build_gateways(rng: random.Random, n_devices: int, start_s: int, days: int) -> List[Gateway]:
    n_gw = max(2, min(5, 1 + n_devices // 12))
    gateways: List[Gateway] = []
    for i in range(n_gw):
        gw = Gateway("gw-%s" % chr(ord("a") + i))
        # Backhaul drops and power cuts. These make `f_cnt` gaps bursty and simultaneous
        # across devices, which is the shape that fools a naive never-reporting metric (§7).
        for _ in range(max(0, days // 9)):
            o0 = start_s + rng.randrange(0, max(1, days * SECONDS_PER_DAY))
            gw.outages.append((o0, o0 + rng.randint(90 * 60, 7 * 3600)))
        gw.outages.sort()
        gateways.append(gw)
    return gateways


def _install_quality(rng: random.Random, dev: Device, hot_line_p: float = 0.35) -> None:
    """Make an install bad in one of the two ways a self-install actually goes wrong.

    Both come straight from ../../08-roadmap.md §8 risk 4 and §5 requirement 2. The clamp in
    free air reads a plausible-looking temperature that has nothing to do with the pipe. The
    clamp on the hot-water line reads high and moves the *wrong way* on a draw, which is
    worse, because a detector keying on the magnitude of the excursion rather than its sign
    will call it a leak.
    """
    dev.install_verified = False
    dev.clamp_on_hot_line = rng.random() < hot_line_p
    dev.dangle_offset_c = rng.uniform(10.0, 20.0) if dev.clamp_on_hot_line else rng.uniform(-3.5, 2.5)
    dev.noise_sigma_c = max(dev.noise_sigma_c, 0.45)


def build_fleet(
    n_devices: int,
    start_s: int,
    days: int,
    seed: int,
    heartbeat_s: int = 900,
    ensure_coverage: bool = True,
    event_rate: float = 1.0,
) -> FleetSpec:
    """Build a labelled fleet. Deterministic in `(n_devices, start_s, days, seed)`.

    Every random draw comes from a `random.Random` seeded through `fleet.derive_seed`, never
    from the module-level `random` state, so the result does not depend on import order, on
    PYTHONHASHSEED, or on anything else having used the RNG first.

    Each *purpose* gets its own stream -- `install`, `draws`, `showers`, `episodes`, and one
    per device for the trace itself. That is not fastidiousness. On the first draft all of
    them shared one stream per device, and because `household_draws` consumes a
    device-dependent number of values, every later decision was conditioned on how many draws
    the household happened to make: the 78%-of-devices-have-a-morning-shower gate came out at
    63%, visibly clustered. Independent streams also mean adding a `random()` call in one
    generator no longer silently reshuffles every other device's properties, which is what
    makes `--seed` reproducible across code changes rather than only across runs.
    """
    notes: List[str] = []
    if ensure_coverage and n_devices < MIN_DEVICES_FOR_FULL_COVERAGE:
        notes.append(
            "fleet of %d is below the %d needed to guarantee all ten §6 scenarios; "
            "some will be missing" % (n_devices, MIN_DEVICES_FOR_FULL_COVERAGE)
        )
    if ensure_coverage and days < MIN_DAYS_FOR_FULL_COVERAGE:
        notes.append("a %d-day run is too short for a freeze event to complete" % days)

    frng = random.Random(fleet.derive_seed("fleet", seed, n_devices, start_s, days))
    gateways = _build_gateways(frng, n_devices, start_s, days)

    plans: List[DevicePlan] = []
    # Which devices carry which required episodes. Index 0 is the unverified install and
    # index 1 is left episode-free so `cold_basement_winter` is observable on it; the rest are
    # candidates. With a fleet of five that is three carriers for five episodes, so two of
    # them get a stacked pair.
    carriers: List[int] = []
    unverified_idx = 0 if (ensure_coverage and n_devices >= 2) else -1
    cold_idx = 1 if (ensure_coverage and n_devices >= 3) else -1

    for i in range(n_devices):
        if ensure_coverage and i in (unverified_idx, cold_idx):
            continue
        carriers.append(i)

    # Each episode kind goes to several carriers in a large fleet and to one in a small one.
    # Round-robin rather than random, so coverage is a property of the code instead of a
    # property of the seed: a run that silently omitted `fast_leak` would quietly report
    # perfect recall on it.
    per_kind = max(1, int(round(len(carriers) * _REQUIRED_SHARE * event_rate))) if event_rate > 0 else 1
    required: Dict[int, List[str]] = {}
    if ensure_coverage and carriers:
        for j, kind in enumerate(list(REQUIRED_EPISODES) * per_kind):
            idx = carriers[j % len(carriers)]
            required.setdefault(idx, []).append(kind)

    for i in range(n_devices):
        drng = random.Random(fleet.derive_seed("device", seed, i))
        device_id = "leak-%04d" % (i + 1)
        archetype_name = _weighted_choice(drng, _ARCHETYPE_WEIGHTS)
        if i == cold_idx:
            archetype_name = "cold_basement"
        dev = Device(
            device_id=device_id,
            dev_eui="70B3D57ED0%06X" % (i + 1),
            archetype=fleet.ARCHETYPES[archetype_name],
            seed=fleet.derive_seed("sim", seed, i),
            cal_pipe_c=max(-0.6, min(0.6, drng.gauss(0.0, 0.22))),
            cal_ambient_c=max(-0.6, min(0.6, drng.gauss(0.0, 0.22))),
            noise_sigma_c=drng.uniform(0.10, 0.30),
            occupants=int(_weighted_choice(drng, _OCCUPANT_WEIGHTS)),
            thermostat_offset_c=drng.gauss(0.0, 1.6),
            heartbeat_s=heartbeat_s,
        )

        # Battery age. A real portfolio is a mix of units installed over three years, with a
        # tail already at the knee -- which is the only way a fleet view shows anything useful.
        dod = drng.uniform(0.80, 0.97) if drng.random() < 0.10 else drng.uniform(0.02, 0.55)
        dev.battery_mah_used = dev.battery_mah_usable * dod

        # Link budget. Loss is long-tailed: most devices are fine, a tenth are at the edge of
        # coverage, and those are the ones the never-reporting metric keeps tripping over.
        roll = drng.random()
        if roll < 0.68:
            dev.link_loss_p = drng.uniform(0.005, 0.040)
        elif roll < 0.90:
            dev.link_loss_p = drng.uniform(0.050, 0.160)
        else:
            dev.link_loss_p = drng.uniform(0.180, 0.380)
        dev.link_margin_db = 14.0 - 110.0 * dev.link_loss_p + drng.gauss(0.0, 2.0)
        dev.gateways = [gateways[i % len(gateways)]]
        if len(gateways) > 1 and drng.random() < 0.55:
            dev.gateways.append(gateways[(i + 1 + drng.randrange(len(gateways) - 1)) % len(gateways)])
        dev.boot_offset_s = drng.randrange(0, dev.heartbeat_s)

        irng = random.Random(fleet.derive_seed("install", seed, i))
        is_unverified = i == unverified_idx or (i not in required and irng.random() < 0.05)
        if is_unverified:
            # The guaranteed unverified device is pinned to the free-air failure, which is
            # both the commoner mistake and the leak-like one: a clamp dangling in air drifts
            # around a plausible temperature with no relation to the pipe. The hot-line
            # variant is left to chance for the rest.
            _install_quality(irng, dev, hot_line_p=0.0 if i == unverified_idx else 0.35)

        # The install failure tail: a unit that reports once and is never heard from again.
        # Far likelier when the install was never verified, which is exactly why risk 4's
        # mitigation is install-verification telemetry rather than better instructions. The
        # fleet-wide rate lands near 10%, which is the leading indicator that risk names.
        dark_p = 0.40 if (is_unverified and i != unverified_idx) else (0.0 if i in required else 0.02)
        if ensure_coverage and n_devices >= 6 and i == n_devices - 1:
            # One guaranteed dark unit, so §7's `pct_never_reporting` always has something to
            # measure rather than depending on a lucky seed.
            _install_quality(irng, dev)
            dark_p = 1.0
        if irng.random() < dark_p:
            dev.dark_at_s = start_s + dev.boot_offset_s + int(irng.uniform(1.2, 5.6) * SECONDS_PER_DAY)

        # Draws. An unverified clamp is not on the pipe, so water use is invisible to it; the
        # hot-line variant sees the mirror image, which `fleet.simulate_device` handles.
        draws: List[Draw] = []
        if dev.dark_at_s is None or dev.dark_at_s > start_s + SECONDS_PER_DAY:
            draws.extend(household_draws(random.Random(fleet.derive_seed("draws", seed, i)), dev, start_s, days))
            srng = random.Random(fleet.derive_seed("showers", seed, i))
            if srng.random() < 0.78:
                draws.extend(morning_showers(srng, dev, start_s, days))

        erng = random.Random(fleet.derive_seed("episodes", seed, i))
        kinds = list(required.get(i, []))
        if not kinds and not is_unverified and i != cold_idx and dev.dark_at_s is None:
            if erng.random() < _EXTRA_EPISODE_P * event_rate:
                kinds.append(_weighted_choice(erng, _EXTRA_EPISODE_WEIGHTS))
        episodes = _assign_episodes(erng, kinds, start_s, days, dev)

        plans.append(DevicePlan(device=dev, draws=draws, episodes=episodes))

    if ensure_coverage:
        coldest = fleet.outdoor_daily_mean_c(_coldest_start(start_s, start_s, start_s + days * SECONDS_PER_DAY))
        if coldest > 8.0:
            notes.append(
                "the warmest the simulated window gets is a %.1f °C daily mean, so the "
                "cold_basement_winter structural offset will be small and that scenario may "
                "not appear; run with --start in November-March to exercise it" % coldest
            )
    return FleetSpec(plans=plans, gateways=gateways, start_s=start_s, days=days, seed=seed, notes=notes)


def scenario_counts(spec: FleetSpec) -> Dict[str, int]:
    """Count the frames each §6 scenario produces. Used by the CLI summary and the tests;
    this runs the full simulation, so it is not cheap."""
    counts: Dict[str, int] = dict((name, 0) for name in fleet.SCENARIOS)
    for plan in spec.plans:
        for frame in fleet.simulate_device(plan, spec.start_s, spec.days):
            counts[frame.scenario] += 1
    return counts


def warn_notes(spec: FleetSpec, stream=sys.stderr) -> None:
    for note in spec.notes:
        stream.write("simulator: note: %s\n" % note)
