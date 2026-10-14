"""Evaluation harness: scores the detector against labelled JSONL and decides
the roadmap's P1 exit gate.

    python3 -m detection.evaluate --input fleet.jsonl

WHAT THIS IS
------------
The component whose arithmetic the whole P1 decision rests on
(08-roadmap.md §3 "P1 - Prove the alert", §7 claim 2).  It reads the
simulator's labelled JSONL (contracts/INTERFACES.md §6), decodes each 8-byte
uplink itself (§1), replays it through `detector.update()` in file order, and
reports the §7 metrics with an explicit PASS / FAIL against
`nuisance_alerts_per_device_year < 0.5`.

**A metrics bug that flatters the result is worse than having no metrics**, so
every definition below is written out, every normalisation is stated, and
where a choice could go either way the conservative one (the one that makes
the headline number *worse*) is taken and labelled.  `test_evaluate.py` tests
the arithmetic directly, including the boundary: exactly 0.5 must FAIL.

WHAT THIS DELIBERATELY DOES *NOT* DO
------------------------------------
* **It does not show the detector a label.**  `_label` is read only after
  `update()` has returned.  The detector's input is `Sample`, which has no
  label-shaped field in it, and nothing here tunes a threshold from `_label`.
* **It does not trust `decoded_payload`.**  `frm_payload` is decoded here,
  server-side, exactly as contracts/INTERFACES.md §4 requires of the backend.
  A `decoded_payload` in the envelope is used only if `frm_payload` is absent,
  and the fallback is reported in the output.
* **It does not model the alert-delivery policy.**  Shadow mode, acks and
  one-click "this was nothing" are the backend's (contracts/INTERFACES.md §8).
  This harness counts what the detector *would* have raised.
* **It does not weight, resample, bootstrap or compute confidence intervals.**
  With a few dozen simulated devices, an interval would be a statement about
  the simulator's random seed, not about housing stock.  The honest summary of
  uncertainty is "run it on another seed", which is why `--seed-note` exists
  and why the README reports held-out seeds.
* **It is not bounded-memory.**  The detector is; this is a host-side tool and
  it buffers the file so it can sort each device's samples into time order.

WHAT COUNTS AS AN ALERT
-----------------------
`update()` returns a belief every sample, not an alert.  An **alert** here is a
*rising edge* into a dispatchable verdict - one that would put a human in a
van:

    dispatchable = {"leak_suspected", "freeze_risk"}

`watch` is not an alert (it exists for charts), and neither are `sensor_fault`
or `install_unverified`: those are maintenance states, and 08-roadmap.md §8
risk 4 says explicitly "do not count unverified installs".

Edges are then deduplicated per device *and per verdict* with a refractory
window (default 24 h), because an operator paged twice in one day about the
same pipe experienced one nuisance, not two.  The un-deduplicated edge count
is printed alongside it so the deduplication can never hide anything.
"""

import argparse
import base64
import binascii
import collections
import dataclasses
import datetime
import json
import statistics
import struct
import sys
from typing import Dict, List, Optional, Sequence, Tuple

try:                                 # python3 -m detection.evaluate
    from .detector import Sample, new_state, update
except ImportError:                  # python3 detection/evaluate.py
    from detector import Sample, new_state, update

# ---------------------------------------------------------------------------
# definitions, all of which are arguable and none of which are hidden
# ---------------------------------------------------------------------------

#: The P1 exit gate (08-roadmap.md §3, contracts/INTERFACES.md §7). Strictly
#: less than: a measured 0.5 is a FAIL.
GATE_NUISANCE_PER_DEVICE_YEAR = 0.5

#: Verdicts that would dispatch a human. Only these can be an alert.
DISPATCHABLE = ("leak_suspected", "freeze_risk")

#: Julian year. 365 would overstate device-years by 0.07% and therefore
#: understate the nuisance rate; 365.25 is the convention actuaries use.
SECONDS_PER_YEAR = 365.25 * 24 * 3600

#: Two edges for the same verdict on the same device inside this window are
#: one nuisance, not two.
DEFAULT_REFRACTORY_S = 24 * 3600

#: A silent device is not an observed device. Any inter-sample gap longer than
#: this contributes only this much to device-years. Shrinking the denominator
#: *raises* the nuisance rate, so this cap is the conservative direction.
DEFAULT_MAX_GAP_S = 24 * 3600

#: "Devices with zero uplinks after day 7" (contracts/INTERFACES.md §7,
#: 08-roadmap.md §8 risk 4: ">10% of shipped units never reporting 7 days out").
NEVER_REPORTING_DAY = 7

#: Which verdict each labelled scenario *should* produce. Used only to score,
#: never to detect. Scenarios from contracts/INTERFACES.md §6.
#:
#: Only a fallback: the simulator in `../simulator/` puts an `expected_verdict`
#: on every `_label`, and that is preferred when present, because the component
#: that generated the ground truth is the authority on what it means. This
#: table exists so a simulator that emits the bare §6 label still scores.
SCENARIO_EXPECTED_VERDICT = {
    "quiet": None,
    "appliance_use": None,
    "shower_morning": None,
    "cold_basement_winter": None,
    "slow_leak": "leak_suspected",
    "fast_leak": "leak_suspected",
    "freeze_event": "freeze_risk",
    "sensor_fault_stuck": "sensor_fault",
    "sensor_fault_noisy": "sensor_fault",
    "install_unverified": "install_unverified",
}

_UPLINK_REASONS = ("heartbeat", "threshold", "boot", "install_probe")


# ---------------------------------------------------------------------------
# §1 / §4 decoding
# ---------------------------------------------------------------------------

class DecodeError(ValueError):
    """A line could not be turned into a Sample. Counted, never fatal."""


def decode_payload(raw: bytes) -> dict:
    """Decode the 8-byte FPort 1 uplink (contracts/INTERFACES.md §1).

    Mirrors the §3 codec output, including the rule that an unknown
    `uplink_reason` decodes to ``"reserved_<n>"`` rather than throwing - a
    decoder that throws takes the whole pipeline down with it.
    """
    if len(raw) != 8:
        raise DecodeError("payload is %d bytes, expected 8" % len(raw))
    pipe_cc, ambient_cc, battery_mv, flags, reason = struct.unpack(">hhHBB", raw)
    return {
        "pipe_c": pipe_cc / 100.0,
        "ambient_c": ambient_cc / 100.0,
        "delta_c": (pipe_cc - ambient_cc) / 100.0,
        "battery_mv": battery_mv,
        "flags": {
            "install_verified": bool(flags & 0x01),
            "tamper": bool(flags & 0x02),
            "cold_warning": bool(flags & 0x04),
        },
        "uplink_reason": (_UPLINK_REASONS[reason] if reason < len(_UPLINK_REASONS)
                          else "reserved_%d" % reason),
    }


def parse_received_at(text: str) -> int:
    """ISO-8601 -> unix seconds, tolerant of what a real LNS actually sends.

    The Things Stack timestamps carry **nanoseconds** and a trailing ``Z``.
    Python 3.9's `datetime.fromisoformat` rejects both, so this truncates the
    fraction to microseconds and normalises the zone. Getting this wrong
    silently shifts every timestamp and therefore every dwell window.
    """
    text = text.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    if "." in text:
        head, _, tail = text.partition(".")
        digits = ""
        for ch in tail:
            if ch.isdigit():
                digits += ch
            else:
                break
        rest = tail[len(digits):]
        text = head + "." + (digits[:6].ljust(6, "0")) + rest
    try:
        dt = datetime.datetime.fromisoformat(text)
    except ValueError as exc:
        raise DecodeError("unparseable received_at %r: %s" % (text, exc))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp())


@dataclasses.dataclass
class Record:
    """One labelled uplink, decoded."""

    device_id: str
    f_cnt: int
    sample: Sample
    scenario: str
    is_true_event: bool
    event_start_s: Optional[int]
    expected_verdict: Optional[str] = None
    used_decoded_payload: bool = False

    def episode_key(self) -> Tuple[str, Optional[int], bool]:
        """What makes two adjacent samples part of the same labelled episode.

        contracts/INTERFACES.md §6 shows `_label` on every line, and the
        simulator uses it that way: a device's label changes as the day goes
        on, so a 30-day record contains hundreds of labelled episodes rather
        than one scenario per device. Scoring per device would mean calling a
        whole device "a slow_leak device" and counting an alert raised three
        weeks before the leak as a true positive.
        """
        return (self.scenario, self.event_start_s, self.is_true_event)


def decode_record(obj: dict) -> Record:
    """Envelope (§4) + `_label` (§6) -> `Record`. Raises `DecodeError`."""
    if not isinstance(obj, dict):
        raise DecodeError("line is not a JSON object")
    ids = obj.get("end_device_ids") or {}
    label = obj.get("_label") or {}
    device_id = ids.get("device_id") or label.get("device_id") or ids.get("dev_eui")
    if not device_id:
        raise DecodeError("no device_id in end_device_ids or _label")

    up = obj.get("uplink_message") or {}
    # §4: only f_port 1 carries the sensor payload. Anything else (join
    # accepts, install-probe echoes on another port) is not a sample.
    f_port = up.get("f_port", 1)
    if f_port != 1:
        raise DecodeError("f_port %r is not the sensor port" % (f_port,))

    used_decoded = False
    frm = up.get("frm_payload")
    if frm:
        try:
            raw = base64.b64decode(frm, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise DecodeError("frm_payload is not base64: %s" % exc)
        fields = decode_payload(raw)
    else:
        # §4 says the backend must not trust a caller-supplied
        # decoded_payload. This harness is not the backend, and a simulator
        # that emits only the decoded form is still worth scoring - but the
        # substitution is counted and printed.
        dp = up.get("decoded_payload")
        if not isinstance(dp, dict):
            raise DecodeError("neither frm_payload nor decoded_payload present")
        fields = dp
        used_decoded = True

    flags = fields.get("flags") or {}
    received_at = obj.get("received_at")
    if not received_at:
        raise DecodeError("no received_at")

    try:
        sample = Sample(
            ts_s=parse_received_at(received_at),
            pipe_c=float(fields["pipe_c"]),
            ambient_c=float(fields["ambient_c"]),
            battery_mv=int(fields.get("battery_mv", 0)),
            install_verified=bool(flags.get("install_verified", False)),
            tamper=bool(flags.get("tamper", False)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DecodeError("bad payload fields: %s" % exc)

    event_start = label.get("event_start_s")
    scenario = str(label.get("scenario", "unlabelled"))
    expected = label.get("expected_verdict")
    if expected is None:
        expected = SCENARIO_EXPECTED_VERDICT.get(scenario, None)
    return Record(
        device_id=str(device_id),
        f_cnt=int(up.get("f_cnt", -1)),
        sample=sample,
        scenario=scenario,
        is_true_event=bool(label.get("is_true_event", False)),
        event_start_s=int(event_start) if event_start is not None else None,
        expected_verdict=str(expected) if expected else None,
        used_decoded_payload=used_decoded,
    )


# ---------------------------------------------------------------------------
# per-device replay result
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Alert:
    ts_s: int
    verdict: str
    confidence: int
    #: True if a labelled true event was already in progress at `ts_s` *and*
    #: this verdict is the one that event was supposed to produce.
    true_positive: bool = False
    #: The labelled scenario in progress when it fired, for the breakdown.
    scenario: str = "unlabelled"


@dataclasses.dataclass
class Episode:
    """One contiguous run of samples carrying the same `_label`.

    The unit of ground truth. A 30-day record of one device contains hundreds
    of these: quiet, then a shower, then quiet, then - on ten of the forty
    devices - a leak that runs to the end of the file.
    """

    scenario: str
    is_true_event: bool
    event_start_s: Optional[int]
    expected_verdict: Optional[str]
    first_ts_s: int
    last_ts_s: int
    samples: int = 0
    alerts: int = 0
    #: First sample *inside* this episode whose verdict was the expected one.
    detected_ts_s: Optional[int] = None
    #: The expected verdict was already being reported on the sample before
    #: this episode began, so `detected_ts_s` is a carry-over from an earlier
    #: episode and must not contribute to time-to-detection.
    carried_in: bool = False


@dataclasses.dataclass
class DeviceResult:
    """Everything the metric arithmetic needs about one device.

    Constructed by `replay()` from real data, and constructed directly by
    `test_evaluate.py` so the arithmetic can be tested without a detector in
    the loop.
    """

    device_id: str
    #: Device-level summary of the label, kept for the per-device report and
    #: as the fallback when a file carries one scenario per device rather than
    #: one per sample. `episodes` is the real ground truth when present.
    scenario: str = "unlabelled"
    is_true_event: bool = False
    event_start_s: Optional[int] = None
    episodes: List[Episode] = dataclasses.field(default_factory=list)
    samples: int = 0
    first_ts_s: Optional[int] = None
    last_ts_s: Optional[int] = None
    #: Seconds during which this device was both reporting and install-verified.
    #: See `_exposure_s` for the three deliberate choices baked into it.
    exposure_s: int = 0
    #: Seconds of inter-sample gap discarded by the max-gap cap, reported so
    #: the size of that correction is always visible.
    gap_excluded_s: int = 0
    #: Seconds discarded because the install was not verified.
    unverified_excluded_s: int = 0
    alerts: List[Alert] = dataclasses.field(default_factory=list)
    raw_edges: int = 0
    verdict_counts: Dict[str, int] = dataclasses.field(default_factory=dict)
    first_verdict_ts: Dict[str, int] = dataclasses.field(default_factory=dict)


def _exposure_s(records: Sequence[Record], max_gap_s: int) -> Tuple[int, int, int]:
    """Observed device-seconds, plus the two exclusions, for one device.

    Returns ``(exposure_s, gap_excluded_s, unverified_excluded_s)``.

    Three choices, all documented because each one moves the headline number:

    1. **Exposure is measured between the first and last uplink**, as a sum of
       inter-sample gaps.  A device with N samples at interval T therefore
       contributes ``(N-1)*T``, not ``N*T``: it *under*-counts the real
       exposure by one sampling interval, which makes the nuisance rate very
       slightly worse.  That is the direction an off-by-one is allowed to go.
       A device with a single uplink contributes zero, and cannot alert
       either, so it is self-consistent.
    2. **A gap longer than `max_gap_s` contributes only `max_gap_s`.**  A
       device that was silent for three weeks was not being observed, and
       crediting that silence as exposure would flatter the rate - exactly
       the mechanism risk 4 in 08-roadmap.md §8 warns about.
    3. **Time while `install_verified` is false does not count.**  The
       detector cannot alert then, so counting it as exposure would be free
       denominator.  08-roadmap.md §8 risk 4: "do not count unverified
       installs".  Each gap is attributed to the verification state at its
       *start*, which is the state that held for (almost) all of it.
    """
    exposure = 0
    gap_excluded = 0
    unverified_excluded = 0
    for prev, nxt in zip(records, records[1:]):
        gap = nxt.sample.ts_s - prev.sample.ts_s
        if gap <= 0:
            continue
        counted = min(gap, max_gap_s)
        gap_excluded += gap - counted
        if prev.sample.install_verified:
            exposure += counted
        else:
            unverified_excluded += counted
    return exposure, gap_excluded, unverified_excluded


def replay(records_by_device: Dict[str, List[Record]], refractory_s: int,
           max_gap_s: int) -> Dict[str, DeviceResult]:
    """Run the detector over every device and collect what the metrics need.

    The detector sees `Sample` only. `_label` is touched exclusively *after*
    `update()` has returned, to decide which labelled episode a verdict landed
    in. Nothing in this function can reach the detector.
    """
    results = {}
    for device_id, records in records_by_device.items():
        label = _device_label(records)
        result = DeviceResult(
            device_id=device_id,
            scenario=label[0], is_true_event=label[1], event_start_s=label[2],
            samples=len(records),
            first_ts_s=records[0].sample.ts_s if records else None,
            last_ts_s=records[-1].sample.ts_s if records else None,
        )
        result.exposure_s, result.gap_excluded_s, result.unverified_excluded_s = \
            _exposure_s(records, max_gap_s)

        state = new_state(device_id)
        previous = None                     # previous sample's verdict
        episode = None                      # the Episode currently open
        episode_key = None
        last_alert_ts: Dict[str, int] = {}
        for record in records:
            decision = update(state, record.sample)
            verdict = decision.verdict
            result.verdict_counts[verdict] = result.verdict_counts.get(verdict, 0) + 1
            result.first_verdict_ts.setdefault(verdict, record.sample.ts_s)

            key = record.episode_key()
            if key != episode_key:
                episode = Episode(
                    scenario=record.scenario,
                    is_true_event=record.is_true_event,
                    event_start_s=record.event_start_s,
                    expected_verdict=record.expected_verdict,
                    first_ts_s=record.sample.ts_s,
                    last_ts_s=record.sample.ts_s,
                    carried_in=(previous is not None
                                and previous == record.expected_verdict),
                )
                result.episodes.append(episode)
                episode_key = key
            episode.samples += 1
            episode.last_ts_s = record.sample.ts_s
            if (episode.detected_ts_s is None
                    and episode.expected_verdict is not None
                    and verdict == episode.expected_verdict):
                episode.detected_ts_s = record.sample.ts_s

            rising = verdict in DISPATCHABLE and verdict != previous
            if rising:
                result.raw_edges += 1
                previous_same = last_alert_ts.get(verdict)
                if previous_same is None or \
                        record.sample.ts_s - previous_same >= refractory_s:
                    last_alert_ts[verdict] = record.sample.ts_s
                    episode.alerts += 1
                    result.alerts.append(Alert(
                        ts_s=record.sample.ts_s, verdict=verdict,
                        confidence=decision.confidence,
                        scenario=record.scenario,
                        true_positive=_alert_is_true(episode, verdict,
                                                     record.sample.ts_s)))
            previous = verdict
        results[device_id] = result
    return results


def _alert_is_true(episode: Episode, verdict: str, ts_s: int) -> bool:
    """Was this alert the right alarm, during a labelled true event?

    Strict on purpose, in both directions:

    * The episode must be flagged `is_true_event`. An alert raised during an
      `appliance_use` episode is a nuisance even on a device that leaks later.
    * The verdict must be the one the label expected. A `leak_suspected`
      raised in the middle of a `sensor_fault_stuck` episode is a nuisance -
      paging somebody about water when the sensor is dead is exactly the
      failure risk 2 in 08-roadmap.md §8 is about - and a `freeze_risk` during
      a `slow_leak` is the right alarm for the wrong reason.
    * An alert before `_label.event_start_s` is a nuisance, not an early
      detection: whatever it fired on, it was not the event.

    When the label names no expected verdict but does flag a true event, any
    dispatchable alert counts, so the detector is not punished for a gap in
    the labelling it cannot see.
    """
    if not episode.is_true_event:
        return False
    if episode.event_start_s is not None and ts_s < episode.event_start_s:
        return False
    if episode.expected_verdict is None:
        return True
    return verdict == episode.expected_verdict


def _device_label(records: Sequence[Record]) -> Tuple[str, bool, Optional[int]]:
    """Collapse per-line labels to one per device.

    contracts/INTERFACES.md §6 puts `_label` on every line, so a device could
    in principle carry several. The scenario is taken to be the most specific
    one present (anything other than `quiet`), and the event start the earliest
    `event_start_s` on a line flagged `is_true_event`.
    """
    scenario = "unlabelled"
    for record in records:
        if record.scenario not in ("quiet", "unlabelled"):
            scenario = record.scenario
            break
    else:
        for record in records:
            if record.scenario == "quiet":
                scenario = "quiet"
                break

    is_true_event = any(r.is_true_event for r in records)
    starts = [r.event_start_s for r in records
              if r.is_true_event and r.event_start_s is not None]
    if not starts:
        # A scenario flagged as a true event with no start time is still a
        # true event; treat it as having run for the whole record rather than
        # silently dropping it from the recall denominator.
        starts = [r.sample.ts_s for r in records if r.is_true_event]
    return scenario, is_true_event, min(starts) if starts else None


def _episodes_of(result: DeviceResult) -> List[Episode]:
    """The device's labelled episodes, or one synthesised from its device-level
    label if the input carried a scenario per device rather than per sample."""
    if result.episodes:
        return result.episodes
    return [Episode(
        scenario=result.scenario, is_true_event=result.is_true_event,
        event_start_s=result.event_start_s,
        expected_verdict=SCENARIO_EXPECTED_VERDICT.get(result.scenario, None),
        first_ts_s=result.first_ts_s or 0, last_ts_s=result.last_ts_s or 0,
        samples=result.samples,
        detected_ts_s=_first_expected_verdict_ts(result),
        alerts=len(result.alerts))]


def _first_expected_verdict_ts(result: DeviceResult) -> Optional[int]:
    expected = SCENARIO_EXPECTED_VERDICT.get(result.scenario, None)
    if expected is None:
        for alert in result.alerts:
            if alert.true_positive:
                return alert.ts_s
        return None
    first = result.first_verdict_ts.get(expected)
    if first is None:
        for alert in result.alerts:
            if alert.true_positive and alert.verdict == expected:
                return alert.ts_s
        return None
    if result.event_start_s is not None and first < result.event_start_s:
        return None
    return first


# ---------------------------------------------------------------------------
# the metric arithmetic - pure, and tested directly
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Metrics:
    devices: int = 0
    samples: int = 0
    episodes: int = 0
    device_years: float = 0.0
    observed_span_days: float = 0.0

    alerts_total: int = 0
    raw_edges_total: int = 0
    true_alerts: int = 0
    nuisance_alerts: int = 0
    nuisance_alerts_by_verdict: Dict[str, int] = dataclasses.field(default_factory=dict)
    nuisance_alerts_by_scenario: Dict[str, int] = dataclasses.field(default_factory=dict)

    #: THE GATE.
    nuisance_alerts_per_device_year: float = 0.0
    #: Leak alerts only, for comparison with the headline above.
    nuisance_leak_alerts: int = 0
    nuisance_leak_alerts_per_device_year: float = 0.0
    #: Normalised by non-event exposure instead of total exposure.
    nuisance_alerts_per_quiet_device_year: float = 0.0
    quiet_device_years: float = 0.0

    true_events: int = 0
    true_events_detected: int = 0
    true_event_recall: Optional[float] = None
    #: The leak subset of the above, which is what the product is actually for.
    leak_events: int = 0
    leak_events_detected: int = 0
    leak_event_recall: Optional[float] = None
    precision: Optional[float] = None

    median_time_to_detection_s: Optional[float] = None
    time_to_detection_n: int = 0
    time_to_detection_dropped: int = 0
    time_to_detection_s: List[int] = dataclasses.field(default_factory=list)

    devices_after_day7: int = 0
    never_reporting: int = 0
    pct_never_reporting: Optional[float] = None

    gap_excluded_days: float = 0.0
    unverified_excluded_days: float = 0.0
    gate: float = GATE_NUISANCE_PER_DEVICE_YEAR
    passed: bool = False
    warnings: List[str] = dataclasses.field(default_factory=list)
    scenario_table: Dict[str, Dict[str, int]] = dataclasses.field(default_factory=dict)


def compute_metrics(results: Dict[str, DeviceResult],
                    gate: float = GATE_NUISANCE_PER_DEVICE_YEAR,
                    never_reporting_day: int = NEVER_REPORTING_DAY) -> Metrics:
    """Turn per-device replay results into the contracts/INTERFACES.md §7 set.

    Pure arithmetic on `DeviceResult` objects, deliberately separated from
    parsing and from the detector so `test_evaluate.py` can feed it
    hand-computed inputs and check the numbers that come out.

    `nuisance_alerts_per_device_year` - **the P1 exit gate** - is

        (alerts raised while no labelled true event was in progress)
        -------------------------------------------------------------
        (sum over devices of observed, install-verified seconds) / 365.25 d

    Numerator: rising edges into `leak_suspected` or `freeze_risk`, per device
    per verdict, deduplicated with the refractory window, on device-windows
    where `_label` says no true event had started.  `watch`, `sensor_fault`
    and `install_unverified` are never alerts.

    Denominator: `DeviceResult.exposure_s`, whose three conservative choices
    are spelled out in `_exposure_s`.  It is **total** observed device-years,
    not just quiet device-years, because that is what
    contracts/INTERFACES.md §7 says; the quiet-only variant is reported too
    because it is the stricter reading and the two should be close.

    The comparison is `rate < gate`, strictly.  A measured 0.5 is a FAIL.
    """
    m = Metrics(gate=gate)
    m.devices = len(results)
    if not results:
        m.warnings.append("no devices in input: nothing to score")
        return m

    exposure_total = 0
    quiet_exposure_total = 0
    episodes: List[Episode] = []
    for r in results.values():
        m.samples += r.samples
        exposure_total += r.exposure_s
        m.gap_excluded_days += r.gap_excluded_s / 86400.0
        m.unverified_excluded_days += r.unverified_excluded_s / 86400.0
        m.raw_edges_total += r.raw_edges
        device_episodes = _episodes_of(r)
        episodes.extend(device_episodes)

        # Quiet device-years: the exposure during episodes that were not true
        # events. Computed from the episodes rather than from the device, so a
        # device that leaks for the last three weeks contributes only its
        # first week as an opportunity to cry wolf.
        if r.episodes:
            for e in device_episodes:
                if not e.is_true_event:
                    quiet_exposure_total += max(0, e.last_ts_s - e.first_ts_s)
        elif not r.is_true_event:
            quiet_exposure_total += r.exposure_s
        elif r.event_start_s is not None and r.first_ts_s is not None:
            quiet_exposure_total += max(0, min(r.event_start_s, r.last_ts_s or 0)
                                        - r.first_ts_s)

        for alert in r.alerts:
            m.alerts_total += 1
            if alert.true_positive:
                m.true_alerts += 1
            else:
                m.nuisance_alerts += 1
                m.nuisance_alerts_by_verdict[alert.verdict] = \
                    m.nuisance_alerts_by_verdict.get(alert.verdict, 0) + 1
                m.nuisance_alerts_by_scenario[alert.scenario] = \
                    m.nuisance_alerts_by_scenario.get(alert.scenario, 0) + 1
                if alert.verdict == "leak_suspected":
                    m.nuisance_leak_alerts += 1

        # The scenario x verdict table is keyed on the *episode* scenario, so
        # a device that spends a week quiet and then leaks is counted in both
        # rows rather than being declared "a leak device".
        for e in device_episodes:
            bucket = m.scenario_table.setdefault(e.scenario, {})
            bucket["_episodes"] = bucket.get("_episodes", 0) + 1
            bucket["_samples"] = bucket.get("_samples", 0) + e.samples
            bucket["_alerts"] = bucket.get("_alerts", 0) + e.alerts
            if e.is_true_event:
                bucket["_true_events"] = bucket.get("_true_events", 0) + 1
                if e.detected_ts_s is not None:
                    bucket["_detected"] = bucket.get("_detected", 0) + 1

    m.episodes = len(episodes)
    m.device_years = exposure_total / SECONDS_PER_YEAR
    m.quiet_device_years = quiet_exposure_total / SECONDS_PER_YEAR

    if m.device_years > 0:
        m.nuisance_alerts_per_device_year = m.nuisance_alerts / m.device_years
        m.nuisance_leak_alerts_per_device_year = \
            m.nuisance_leak_alerts / m.device_years
    elif m.nuisance_alerts:
        m.nuisance_alerts_per_device_year = float("inf")
        m.nuisance_leak_alerts_per_device_year = float("inf")
        m.warnings.append(
            "zero device-years of exposure but %d nuisance alerts: the rate is "
            "undefined and is reported as infinite" % m.nuisance_alerts)
    else:
        m.warnings.append("zero device-years of exposure: the gate is vacuous")

    if m.quiet_device_years > 0:
        m.nuisance_alerts_per_quiet_device_year = \
            m.nuisance_alerts / m.quiet_device_years

    # --- recall / precision / time to detection --------------------------
    # One true event = one labelled episode flagged `is_true_event`, not one
    # device. Scoring per device would let a single leak that happened to be
    # caught stand in for all the episodes on that device.
    ttd: List[int] = []
    for e in episodes:
        if not e.is_true_event:
            continue
        m.true_events += 1
        if e.detected_ts_s is not None:
            m.true_events_detected += 1
        if e.expected_verdict == "leak_suspected":
            m.leak_events += 1
            if e.detected_ts_s is not None:
                m.leak_events_detected += 1

        # contracts/INTERFACES.md §7 / the task spec: measured from
        # `_label.event_start_s` to the first `leak_suspected`. Only leak
        # episodes contribute, only when detected, and not when the verdict
        # was carried in from a previous episode - that was not a detection of
        # *this* event and would drag the median toward zero.
        if (e.expected_verdict == "leak_suspected" and e.detected_ts_s is not None
                and not e.carried_in and e.event_start_s is not None):
            delay = e.detected_ts_s - e.event_start_s
            if delay >= 0:
                ttd.append(delay)
            else:
                m.time_to_detection_dropped += 1

    if m.true_events:
        m.true_event_recall = m.true_events_detected / m.true_events
        if m.leak_events:
            m.leak_event_recall = m.leak_events_detected / m.leak_events
    else:
        m.warnings.append(
            "no labelled true events in input: recall and time-to-detection "
            "are undefined, and the gate alone does not establish P1 "
            "(08-roadmap.md §3 also requires >=1 documented true event)")

    if m.alerts_total:
        m.precision = m.true_alerts / m.alerts_total

    if ttd:
        ttd.sort()
        m.time_to_detection_s = ttd
        m.time_to_detection_n = len(ttd)
        m.median_time_to_detection_s = float(statistics.median(ttd))

    # --- pct_never_reporting ---------------------------------------------
    firsts = [r.first_ts_s for r in results.values() if r.first_ts_s is not None]
    lasts = [r.last_ts_s for r in results.values() if r.last_ts_s is not None]
    if firsts and lasts:
        fleet_start, fleet_end = min(firsts), max(lasts)
        m.observed_span_days = (fleet_end - fleet_start) / 86400.0
        cutoff = fleet_start + never_reporting_day * 86400
        if fleet_end <= cutoff:
            m.warnings.append(
                "the record spans %.1f days, which is <= the %d-day "
                "never-reporting cutoff: pct_never_reporting is not "
                "measurable and is reported as None rather than 0%%"
                % (m.observed_span_days, never_reporting_day))
        else:
            # Only devices that existed before the cutoff can be judged; a
            # device first seen on day 20 has not had its 7 days yet.
            judged = [r for r in results.values()
                      if r.first_ts_s is not None and r.first_ts_s < cutoff]
            m.devices_after_day7 = len(judged)
            m.never_reporting = sum(
                1 for r in judged if (r.last_ts_s or 0) <= cutoff)
            if judged:
                m.pct_never_reporting = 100.0 * m.never_reporting / len(judged)

    m.passed = (m.device_years > 0
                and m.nuisance_alerts_per_device_year < gate)
    return m


# ---------------------------------------------------------------------------
# input
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class LoadStats:
    lines: int = 0
    decoded: int = 0
    skipped: int = 0
    duplicates: int = 0
    used_decoded_payload: int = 0
    reasons: Dict[str, int] = dataclasses.field(default_factory=dict)


def load(path: str) -> Tuple[Dict[str, List[Record]], LoadStats]:
    """Read labelled JSONL into per-device, time-sorted record lists.

    Deduplicates on `(device_id, f_cnt)` per contracts/INTERFACES.md §4
    ("duplicate `(dev_eui, f_cnt)` is idempotent"), keeping the first
    occurrence, and sorts each device's samples into time order because the
    detector is a causal state machine and the file need not be sorted.
    """
    by_device: Dict[str, List[Record]] = collections.defaultdict(list)
    seen = set()
    stats = LoadStats()
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            stats.lines += 1
            try:
                obj = json.loads(line)
            except ValueError as exc:
                stats.skipped += 1
                stats.reasons["bad json"] = stats.reasons.get("bad json", 0) + 1
                del exc
                continue
            try:
                record = decode_record(obj)
            except DecodeError as exc:
                stats.skipped += 1
                key = str(exc).split(":")[0]
                stats.reasons[key] = stats.reasons.get(key, 0) + 1
                continue
            if record.f_cnt >= 0:
                key2 = (record.device_id, record.f_cnt)
                if key2 in seen:
                    stats.duplicates += 1
                    continue
                seen.add(key2)
            if record.used_decoded_payload:
                stats.used_decoded_payload += 1
            stats.decoded += 1
            by_device[record.device_id].append(record)

    for records in by_device.values():
        records.sort(key=lambda r: (r.sample.ts_s, r.f_cnt))
    return dict(by_device), stats


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def _fmt_seconds(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return "%d s (%.1f h)" % (int(value), value / 3600.0)


def _fmt_opt(value: Optional[float], spec: str = "%.3f") -> str:
    return "n/a" if value is None else spec % value


def format_report(m: Metrics, stats: Optional[LoadStats] = None,
                  refractory_s: int = DEFAULT_REFRACTORY_S,
                  max_gap_s: int = DEFAULT_MAX_GAP_S,
                  source: str = "") -> str:
    out = []
    w = out.append
    w("=" * 74)
    w("P1 DETECTION EVALUATION" + ("  -  " + source if source else ""))
    w("=" * 74)
    if stats is not None:
        w("input     %d lines -> %d samples, %d duplicate (dev,f_cnt) dropped, "
          "%d undecodable" % (stats.lines, stats.decoded, stats.duplicates,
                              stats.skipped))
        if stats.reasons:
            for reason, count in sorted(stats.reasons.items(),
                                        key=lambda kv: -kv[1]):
                w("          skipped: %-48s %d" % (reason[:48], count))
        if stats.used_decoded_payload:
            w("          WARNING: %d uplinks had no frm_payload; fell back to "
              "decoded_payload" % stats.used_decoded_payload)
    w("fleet     %d devices, %d samples, %d labelled episodes, %.1f day span" %
      (m.devices, m.samples, m.episodes, m.observed_span_days))
    w("exposure  %.4f device-years observed" % m.device_years)
    w("          (= sum of install-verified inter-sample gaps, each capped at "
      "%.0f h;" % (max_gap_s / 3600.0))
    w("           %.2f device-days dropped by that cap, %.2f device-days "
      "dropped as unverified)" % (m.gap_excluded_days, m.unverified_excluded_days))
    w("alerting  rising edges into %s, deduplicated per device+verdict over "
      "%.0f h" % ("/".join(DISPATCHABLE), refractory_s / 3600.0))
    w("")
    w("-- the P1 exit gate -----------------------------------------------------")
    w("  nuisance_alerts_per_device_year   %10.4f      gate: < %.2f   [%s]" % (
        m.nuisance_alerts_per_device_year, m.gate,
        "PASS" if m.passed else "FAIL"))
    w("      %d nuisance alerts / %.4f device-years" %
      (m.nuisance_alerts, m.device_years))
    if m.nuisance_alerts_by_verdict:
        w("      by verdict:  " + ", ".join(
            "%s=%d" % kv for kv in sorted(m.nuisance_alerts_by_verdict.items())))
    if m.nuisance_alerts_by_scenario:
        w("      by scenario: " + ", ".join(
            "%s=%d" % kv for kv in sorted(m.nuisance_alerts_by_scenario.items())))
    w("      leak alerts only                 %10.4f  (%d alerts)" % (
        m.nuisance_leak_alerts_per_device_year, m.nuisance_leak_alerts))
    w("      per *quiet* device-year          %10.4f  (%.4f device-years)" % (
        m.nuisance_alerts_per_quiet_device_year, m.quiet_device_years))
    w("      undeduplicated rising edges      %10d" % m.raw_edges_total)
    w("")
    w("-- the rest of contracts/INTERFACES.md §7 -------------------------------")
    w("  true_event_recall                 %s   (%d of %d labelled true "
      "episodes)" % (_fmt_opt(m.true_event_recall).rjust(9),
                     m.true_events_detected, m.true_events))
    w("      leak episodes only            %s   (%d of %d)" % (
        _fmt_opt(m.leak_event_recall).rjust(9), m.leak_events_detected,
        m.leak_events))
    w("  precision                         %s   (%d true of %d alerts)" % (
        _fmt_opt(m.precision).rjust(9), m.true_alerts, m.alerts_total))
    w("  median_time_to_detection_s        %s   (n=%d leak episodes)" % (
        _fmt_seconds(m.median_time_to_detection_s).rjust(9),
        m.time_to_detection_n))
    if m.time_to_detection_s:
        w("      range %s .. %s" % (_fmt_seconds(m.time_to_detection_s[0]),
                                    _fmt_seconds(m.time_to_detection_s[-1])))
    w("  pct_never_reporting               %s   (%d of %d devices present "
      "before day %d)" % (
          (_fmt_opt(m.pct_never_reporting, "%.1f%%")).rjust(9),
          m.never_reporting, m.devices_after_day7, NEVER_REPORTING_DAY))
    w("")
    if m.scenario_table:
        w("-- by labelled episode (ground truth; the detector never sees it) -----")
        w("  %-22s %9s %9s %7s %9s %9s" % (
            "scenario", "episodes", "samples", "alerts", "true evts", "detected"))
        for scenario in sorted(m.scenario_table):
            b = m.scenario_table[scenario]
            w("  %-22s %9d %9d %7d %9d %9d" % (
                scenario[:22], b.get("_episodes", 0), b.get("_samples", 0),
                b.get("_alerts", 0), b.get("_true_events", 0),
                b.get("_detected", 0)))
        w("")
    for warning in m.warnings:
        w("WARNING: " + warning)
    if m.warnings:
        w("")
    w("=" * 74)
    w("P1 GATE: %s   (nuisance_alerts_per_device_year = %.4f, must be < %.2f)" % (
        "PASS" if m.passed else "FAIL", m.nuisance_alerts_per_device_year, m.gate))
    if m.passed and m.true_events and m.true_events_detected == 0:
        w("         ...but zero labelled true events were detected. The gate in")
        w("         08-roadmap.md §3 also requires >=1 documented true event, so")
        w("         this is not a P1 pass.")
    w("=" * 74)
    return "\n".join(out)


def metrics_to_dict(m: Metrics) -> dict:
    """JSON-serialisable view, for `make e2e` and for regression diffing."""
    d = dataclasses.asdict(m)
    for key in ("nuisance_alerts_per_device_year",
                "nuisance_leak_alerts_per_device_year"):
        if d[key] == float("inf"):
            d[key] = None
            d.setdefault("undefined", []).append(key)
    return d


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def run(path: str, refractory_s: int = DEFAULT_REFRACTORY_S,
        max_gap_s: int = DEFAULT_MAX_GAP_S,
        gate: float = GATE_NUISANCE_PER_DEVICE_YEAR,
        never_reporting_day: int = NEVER_REPORTING_DAY
        ) -> Tuple[Metrics, LoadStats, Dict[str, DeviceResult]]:
    """Load, replay, score. The importable entry point."""
    by_device, stats = load(path)
    results = replay(by_device, refractory_s=refractory_s, max_gap_s=max_gap_s)
    metrics = compute_metrics(results, gate=gate,
                              never_reporting_day=never_reporting_day)
    return metrics, stats, results


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python3 -m detection.evaluate",
        description="Score the leak detector against labelled simulator JSONL "
                    "and decide the P1 exit gate.")
    p.add_argument("--input", required=True,
                   help="labelled JSONL from simulator/ "
                        "(contracts/INTERFACES.md §6)")
    p.add_argument("--refractory-hours", type=float,
                   default=DEFAULT_REFRACTORY_S / 3600.0,
                   help="two alerts of the same verdict on one device inside "
                        "this window count once (default: %(default)s). Pass 0 "
                        "to count every rising edge.")
    p.add_argument("--max-gap-hours", type=float,
                   default=DEFAULT_MAX_GAP_S / 3600.0,
                   help="an inter-sample gap longer than this contributes only "
                        "this much to device-years (default: %(default)s)")
    p.add_argument("--gate", type=float, default=GATE_NUISANCE_PER_DEVICE_YEAR,
                   help="nuisance alerts per device-year that must be beaten, "
                        "strictly (default: %(default)s). Moving this does not "
                        "move the roadmap.")
    p.add_argument("--json", metavar="PATH",
                   help="also write the metrics as JSON ('-' for stdout)")
    p.add_argument("--strict", action="store_true",
                   help="exit 1 when the gate fails (default: exit 0 and say "
                        "so in the report, so `make e2e` still completes)")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    refractory_s = int(args.refractory_hours * 3600)
    max_gap_s = int(args.max_gap_hours * 3600)
    try:
        metrics, stats, _ = run(args.input, refractory_s=refractory_s,
                                max_gap_s=max_gap_s, gate=args.gate)
    except OSError as exc:
        print("cannot read --input: %s" % exc, file=sys.stderr)
        return 2

    report = format_report(metrics, stats, refractory_s=refractory_s,
                           max_gap_s=max_gap_s, source=args.input)
    if args.json != "-":
        print(report)
    if args.json:
        blob = json.dumps(metrics_to_dict(metrics), indent=2, sort_keys=True)
        if args.json == "-":
            print(blob)
        else:
            with open(args.json, "w", encoding="utf-8") as handle:
                handle.write(blob + "\n")
    return 1 if (args.strict and not metrics.passed) else 0


if __name__ == "__main__":
    sys.exit(main())
