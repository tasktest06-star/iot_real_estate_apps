"""Tests for the evaluation harness, and for the metric arithmetic above all.

A metrics bug that flatters the result is worse than having no metrics: it
turns a failed P1 gate into a green dashboard and sends the company into P2 on
a number that was never true.  So the bulk of this file does not test the
detector at all - it hands `compute_metrics()` hand-computed `DeviceResult`
objects and checks the numbers that come back, including the boundary case
where the gate must fail.

`synthesise_fleet()` at the bottom is a small self-contained generator of
contracts/INTERFACES.md §6 JSONL.  It exists so these tests pass with no
dependency on `simulator/`, which is built in parallel by someone else.  It is
**not** a substitute for the real simulator and the README says so: passing a
gate on data you generated yourself is marking your own homework.
"""

import base64
import io
import json
import math
import os
import random
import struct
import tempfile

import pytest

from detection import evaluate
from detection.evaluate import (Alert, DecodeError, DeviceResult, Metrics,
                                SECONDS_PER_YEAR, compute_metrics,
                                decode_payload, decode_record, format_report,
                                parse_received_at, run)

T0 = 1760000000
DAY = 86400
YEAR_S = int(SECONDS_PER_YEAR)


# ---------------------------------------------------------------------------
# helpers for building DeviceResult objects with exactly known arithmetic
# ---------------------------------------------------------------------------

def dev(device_id, exposure_s, alerts=(), scenario="quiet", is_true_event=False,
        event_start_s=None, first_ts_s=T0, last_ts_s=None, samples=2,
        **kw):
    return DeviceResult(
        device_id=device_id, scenario=scenario, is_true_event=is_true_event,
        event_start_s=event_start_s, samples=samples,
        first_ts_s=first_ts_s,
        last_ts_s=first_ts_s + exposure_s if last_ts_s is None else last_ts_s,
        exposure_s=exposure_s, alerts=list(alerts), **kw)


def leak_alert(ts_s, true_positive=False):
    return Alert(ts_s=ts_s, verdict="leak_suspected", confidence=80,
                 true_positive=true_positive)


def freeze_alert(ts_s, true_positive=False):
    return Alert(ts_s=ts_s, verdict="freeze_risk", confidence=80,
                 true_positive=true_positive)


# ===========================================================================
# THE GATE ARITHMETIC
# ===========================================================================

def test_device_years_is_exactly_exposure_over_a_julian_year():
    results = {"a": dev("a", YEAR_S), "b": dev("b", YEAR_S)}
    m = compute_metrics(results)
    assert m.device_years == pytest.approx(2.0, abs=1e-6)


def test_one_nuisance_alert_in_two_device_years_is_exactly_half():
    results = {"a": dev("a", YEAR_S, [leak_alert(T0 + 100)]),
               "b": dev("b", YEAR_S)}
    m = compute_metrics(results)
    assert m.nuisance_alerts == 1
    assert m.nuisance_alerts_per_device_year == pytest.approx(0.5, abs=1e-9)


def test_exactly_the_gate_value_is_a_fail():
    """`< 0.5`, strictly. If this ever passes, the gate has been moved."""
    results = {"a": dev("a", YEAR_S, [leak_alert(T0 + 100)]),
               "b": dev("b", YEAR_S)}
    m = compute_metrics(results)
    assert m.nuisance_alerts_per_device_year == pytest.approx(0.5, abs=1e-9)
    assert m.passed is False
    assert "FAIL" in format_report(m)


def test_just_under_the_gate_passes_and_just_over_fails():
    # 1 alert over 2.01 device-years -> 0.4975
    under = compute_metrics({"a": dev("a", int(2.01 * YEAR_S),
                                      [leak_alert(T0 + 100)])})
    assert under.nuisance_alerts_per_device_year < 0.5 and under.passed
    # 1 alert over 1.99 device-years -> 0.5025
    over = compute_metrics({"a": dev("a", int(1.99 * YEAR_S),
                                     [leak_alert(T0 + 100)])})
    assert over.nuisance_alerts_per_device_year > 0.5 and not over.passed


def test_zero_exposure_with_alerts_is_infinite_not_zero():
    """The off-by-one that would most flatter the headline: dividing by a
    denominator that rounded to zero and calling the result a pass."""
    m = compute_metrics({"a": dev("a", 0, [leak_alert(T0)], samples=1,
                                  last_ts_s=T0)})
    assert m.device_years == 0.0
    assert m.nuisance_alerts_per_device_year == float("inf")
    assert m.passed is False
    assert any("undefined" in w for w in m.warnings)


def test_zero_exposure_without_alerts_is_a_fail_not_a_vacuous_pass():
    m = compute_metrics({"a": dev("a", 0, samples=1, last_ts_s=T0)})
    assert m.passed is False
    assert any("vacuous" in w for w in m.warnings)


def test_no_devices_at_all_is_a_fail():
    m = compute_metrics({})
    assert m.passed is False
    assert m.devices == 0


def test_a_true_positive_alert_is_not_a_nuisance():
    results = {"a": dev("a", YEAR_S, [leak_alert(T0 + 100, true_positive=True)],
                        scenario="slow_leak", is_true_event=True,
                        event_start_s=T0)}
    m = compute_metrics(results)
    assert m.nuisance_alerts == 0
    assert m.true_alerts == 1
    assert m.nuisance_alerts_per_device_year == 0.0
    assert m.passed


def test_freeze_nuisance_counts_in_the_headline_and_is_broken_out():
    """The headline number counts every alert that would dispatch a human, not
    just leak alerts, because an insurer does not care which kind was wrong.
    Both figures are reported so the mix is never hidden."""
    results = {"a": dev("a", YEAR_S, [freeze_alert(T0 + 100)]),
               "b": dev("b", YEAR_S, [leak_alert(T0 + 100)]),
               "c": dev("c", 2 * YEAR_S)}
    m = compute_metrics(results)
    assert m.device_years == pytest.approx(4.0, abs=1e-6)
    assert m.nuisance_alerts == 2
    assert m.nuisance_alerts_per_device_year == pytest.approx(0.5, abs=1e-9)
    assert m.nuisance_leak_alerts == 1
    assert m.nuisance_leak_alerts_per_device_year == pytest.approx(0.25, abs=1e-9)
    assert m.nuisance_alerts_by_verdict == {"freeze_risk": 1, "leak_suspected": 1}


def test_quiet_device_years_excludes_time_after_an_event_started():
    """The stricter normalisation: a device that spent half the record leaking
    only contributes the other half as an opportunity to cry wolf."""
    results = {"a": dev("a", 2 * YEAR_S, scenario="slow_leak",
                        is_true_event=True, event_start_s=T0 + YEAR_S,
                        first_ts_s=T0, last_ts_s=T0 + 2 * YEAR_S,
                        alerts=[leak_alert(T0 + 10)])}
    m = compute_metrics(results)
    assert m.device_years == pytest.approx(2.0, abs=1e-6)
    assert m.quiet_device_years == pytest.approx(1.0, abs=1e-6)
    # The alert fired a year before the event started, so it is a nuisance.
    assert m.nuisance_alerts == 1
    assert m.nuisance_alerts_per_device_year == pytest.approx(0.5, abs=1e-9)
    assert m.nuisance_alerts_per_quiet_device_year == pytest.approx(1.0, abs=1e-6)


def test_gate_threshold_is_a_parameter_but_defaults_to_the_roadmap_value():
    assert evaluate.GATE_NUISANCE_PER_DEVICE_YEAR == 0.5
    results = {"a": dev("a", YEAR_S, [leak_alert(T0)])}
    assert compute_metrics(results).passed is False
    assert compute_metrics(results, gate=2.0).passed is True
    assert compute_metrics(results).gate == 0.5


# ===========================================================================
# EXPOSURE: the three documented choices in _exposure_s
# ===========================================================================

def _records(device_id, count, interval_s, install_verified=True, start=T0):
    out = []
    for i in range(count):
        out.append(evaluate.Record(
            device_id=device_id, f_cnt=i,
            sample=evaluate.Sample(ts_s=start + i * interval_s, pipe_c=19.0,
                                   ambient_c=20.0, battery_mv=3600,
                                   install_verified=install_verified,
                                   tamper=False),
            scenario="quiet", is_true_event=False, event_start_s=None))
    return out


def test_exposure_is_n_minus_one_intervals_and_that_is_deliberate():
    """96 samples 15 minutes apart span 95 intervals, not 96. Under-counting
    exposure makes the nuisance rate *worse*, which is the direction an
    off-by-one is allowed to go."""
    exposure, gaps, unverified = evaluate._exposure_s(
        _records("a", 96, 900), max_gap_s=DAY)
    assert exposure == 95 * 900
    assert gaps == 0 and unverified == 0


def test_a_single_uplink_contributes_zero_exposure():
    exposure, _, _ = evaluate._exposure_s(_records("a", 1, 900), max_gap_s=DAY)
    assert exposure == 0


def test_a_long_silence_is_capped_and_the_discard_is_reported():
    """A device silent for three weeks was not being observed. Crediting that
    silence as exposure is the mechanism that would hide risk 4."""
    records = _records("a", 2, 21 * DAY)
    exposure, gaps, _ = evaluate._exposure_s(records, max_gap_s=DAY)
    assert exposure == DAY
    assert gaps == 20 * DAY


def test_unverified_time_is_excluded_from_the_denominator():
    """Otherwise an unverified device is free denominator: it cannot alert, so
    its device-years would dilute the rate for nothing."""
    verified = _records("a", 10, 900, install_verified=True)
    unverified = _records("a", 10, 900, install_verified=False)
    exposure, _, excluded = evaluate._exposure_s(unverified, max_gap_s=DAY)
    assert exposure == 0
    assert excluded == 9 * 900
    exposure, _, excluded = evaluate._exposure_s(verified, max_gap_s=DAY)
    assert exposure == 9 * 900 and excluded == 0


def test_exposure_attributes_each_gap_to_the_state_at_its_start():
    records = _records("a", 4, 900, install_verified=False)
    for r in records[2:]:
        r.sample.install_verified = True
    # gaps: [0->1] unverified, [1->2] unverified, [2->3] verified
    exposure, _, excluded = evaluate._exposure_s(records, max_gap_s=DAY)
    assert exposure == 900 and excluded == 2 * 900


def test_out_of_order_samples_contribute_no_negative_exposure():
    records = _records("a", 3, 900)
    records[1].sample.ts_s = T0 - 10 * DAY
    exposure, _, _ = evaluate._exposure_s(records, max_gap_s=DAY)
    assert exposure >= 0


# ===========================================================================
# ALERT COUNTING: edges, latching and the refractory window
# ===========================================================================

def test_refractory_window_collapses_a_repeat_page_on_the_same_day():
    by_device = {"a": _verdict_records("a", ["quiet"] * 2
                                       + ["leak_suspected"] * 2
                                       + ["quiet"] * 2
                                       + ["leak_suspected"] * 2)}
    # Replay is not used here; drive the counting logic through a fake stream.
    edges = _count_edges([0, 0, 1, 1, 0, 0, 1, 1], interval_s=3600,
                         refractory_s=24 * 3600)
    assert edges == 1
    far_apart = _count_edges([1] + [0] * 48 + [1], interval_s=3600,
                             refractory_s=24 * 3600)
    assert far_apart == 2
    del by_device


def test_zero_refractory_counts_every_rising_edge():
    assert _count_edges([1, 0, 1, 0, 1], interval_s=3600, refractory_s=0) == 3


def _verdict_records(device_id, verdicts):
    return verdicts


def _count_edges(depressed_flags, interval_s, refractory_s):
    """Re-implementation of the counting rule, used to pin it down.

    Deliberately an independent implementation: if this and `replay()` ever
    disagree, one of them is wrong and the test says so.
    """
    count = 0
    previous = None
    last = None
    for i, flag in enumerate(depressed_flags):
        verdict = "leak_suspected" if flag else "quiet"
        ts = T0 + i * interval_s
        if verdict == "leak_suspected" and verdict != previous:
            if last is None or ts - last >= refractory_s:
                count += 1
                last = ts
        previous = verdict
    return count


def test_replay_counts_one_alert_per_leak_episode_not_one_per_sample():
    """The end-to-end version of the rule above, through the real detector."""
    records = _records("a", 120, 900)                     # 30 h of warm-up
    ts = T0 + 120 * 900
    for i in range(120):                                  # 30 h of leak
        records.append(evaluate.Record(
            device_id="a", f_cnt=1000 + i,
            sample=evaluate.Sample(ts_s=ts + i * 900, pipe_c=13.0 + (i % 3) * 0.01,
                                   ambient_c=20.0, battery_mv=3600,
                                   install_verified=True, tamper=False),
            scenario="slow_leak", is_true_event=True, event_start_s=ts))
    results = evaluate.replay({"a": records}, refractory_s=24 * 3600,
                              max_gap_s=DAY)
    r = results["a"]
    assert r.verdict_counts.get("leak_suspected", 0) > 50, "the leak must latch"
    assert len(r.alerts) == 1
    assert r.alerts[0].verdict == "leak_suspected"
    assert r.alerts[0].true_positive is True
    assert r.raw_edges == 1


def test_watch_and_sensor_fault_and_unverified_are_never_alerts():
    assert set(evaluate.DISPATCHABLE) == {"leak_suspected", "freeze_risk"}
    for verdict in ("quiet", "watch", "sensor_fault", "install_unverified"):
        assert verdict not in evaluate.DISPATCHABLE


def test_an_unverified_device_contributes_no_alerts_and_no_exposure():
    """Both halves matter: if its exposure counted it would dilute the rate."""
    records = _records("a", 480, 900, install_verified=False)
    for r in records:
        r.sample.pipe_c = 13.0                             # a textbook leak
        r.scenario = "install_unverified"
    results = evaluate.replay({"a": records}, refractory_s=DAY, max_gap_s=DAY)
    m = compute_metrics(results)
    assert results["a"].alerts == []
    assert results["a"].exposure_s == 0
    assert m.device_years == 0.0
    assert m.nuisance_alerts == 0


# ===========================================================================
# PRECISION / RECALL / TIME TO DETECTION
# ===========================================================================

def test_precision_is_true_alerts_over_all_alerts():
    results = {
        "a": dev("a", YEAR_S, [leak_alert(T0 + 10, True)], scenario="slow_leak",
                 is_true_event=True, event_start_s=T0),
        "b": dev("b", YEAR_S, [leak_alert(T0 + 10, True)], scenario="fast_leak",
                 is_true_event=True, event_start_s=T0),
        "c": dev("c", YEAR_S, [leak_alert(T0 + 10)]),
        "d": dev("d", YEAR_S, [leak_alert(T0 + 10)]),
    }
    m = compute_metrics(results)
    assert m.alerts_total == 4 and m.true_alerts == 2
    assert m.precision == pytest.approx(0.5)


def test_precision_is_none_not_one_when_nothing_alerted():
    """A detector that never alerts has undefined precision, not perfect
    precision. Reporting 1.0 here would be the most flattering bug available."""
    m = compute_metrics({"a": dev("a", YEAR_S)})
    assert m.precision is None
    assert "n/a" in format_report(m)


def test_recall_counts_devices_with_the_expected_verdict_for_their_scenario():
    results = {
        "leak-ok": dev("leak-ok", YEAR_S, [leak_alert(T0 + 10, True)],
                       scenario="slow_leak", is_true_event=True,
                       event_start_s=T0),
        "leak-missed": dev("leak-missed", YEAR_S, scenario="slow_leak",
                           is_true_event=True, event_start_s=T0),
        "freeze-ok": dev("freeze-ok", YEAR_S, [freeze_alert(T0 + 10, True)],
                         scenario="freeze_event", is_true_event=True,
                         event_start_s=T0),
        "quiet": dev("quiet", YEAR_S),
    }
    m = compute_metrics(results)
    assert m.true_events == 3
    assert m.true_events_detected == 2
    assert m.true_event_recall == pytest.approx(2 / 3)


def test_a_leak_scenario_is_not_recalled_by_a_freeze_alert():
    """Scenario-aware matching: the right alarm for the wrong reason is not a
    detection, and conflating them would inflate recall."""
    results = {"a": dev("a", YEAR_S, [freeze_alert(T0 + 10, True)],
                        scenario="slow_leak", is_true_event=True,
                        event_start_s=T0)}
    m = compute_metrics(results)
    assert m.true_events == 1 and m.true_events_detected == 0


def test_a_non_dispatchable_expected_verdict_is_scored_from_the_verdict_stream():
    """If a simulator flags `sensor_fault_stuck` as a true event, the right
    answer is `sensor_fault`, which is not an alert - so recall has to look at
    the verdicts, not the alert list."""
    seen = dev("a", YEAR_S, scenario="sensor_fault_stuck", is_true_event=True,
               event_start_s=T0)
    seen.first_verdict_ts = {"sensor_fault": T0 + 100}
    missed = dev("b", YEAR_S, scenario="sensor_fault_stuck", is_true_event=True,
                 event_start_s=T0)
    missed.first_verdict_ts = {"quiet": T0}
    m = compute_metrics({"a": seen, "b": missed})
    assert m.true_events == 2 and m.true_events_detected == 1
    # ...and neither produced an alert, so neither is a nuisance.
    assert m.nuisance_alerts == 0


def test_recall_is_none_when_there_are_no_true_events_and_says_so():
    m = compute_metrics({"a": dev("a", YEAR_S)})
    assert m.true_event_recall is None
    assert any("no labelled true events" in w for w in m.warnings)


def test_a_pass_with_zero_detected_true_events_is_called_out():
    """08-roadmap.md §3 requires <0.5 nuisance *and* >=1 documented true
    event. A detector that returns `quiet` forever passes the first half."""
    results = {"a": dev("a", YEAR_S, scenario="slow_leak", is_true_event=True,
                        event_start_s=T0)}
    m = compute_metrics(results)
    assert m.passed is True, "the gate itself is about nuisance only"
    assert "not a P1 pass" in format_report(m)


def test_median_time_to_detection_odd_count():
    results = {}
    for i, delay in enumerate((3600, 7200, 10800)):
        d = dev("d%d" % i, YEAR_S, [leak_alert(T0 + delay, True)],
                scenario="slow_leak", is_true_event=True, event_start_s=T0)
        d.first_verdict_ts = {"leak_suspected": T0 + delay}
        results[d.device_id] = d
    m = compute_metrics(results)
    assert m.median_time_to_detection_s == 7200.0
    assert m.time_to_detection_n == 3


def test_median_time_to_detection_even_count_averages_the_middle_pair():
    results = {}
    for i, delay in enumerate((3600, 7200, 10800, 14400)):
        d = dev("d%d" % i, YEAR_S, [leak_alert(T0 + delay, True)],
                scenario="fast_leak", is_true_event=True, event_start_s=T0)
        d.first_verdict_ts = {"leak_suspected": T0 + delay}
        results[d.device_id] = d
    m = compute_metrics(results)
    assert m.median_time_to_detection_s == 9000.0


def test_time_to_detection_ignores_a_leak_verdict_that_predates_the_event():
    """Otherwise a device that was alerting *before* the leak started would
    contribute a negative or near-zero detection time and drag the median
    down - a flattering bug."""
    d = dev("a", YEAR_S, [leak_alert(T0 - 3600)], scenario="slow_leak",
            is_true_event=True, event_start_s=T0, first_ts_s=T0 - 10 * DAY)
    d.first_verdict_ts = {"leak_suspected": T0 - 3600}
    m = compute_metrics({"a": d})
    assert m.median_time_to_detection_s is None
    assert m.time_to_detection_n == 0
    assert m.nuisance_alerts == 1


def test_time_to_detection_only_uses_leak_scenarios():
    """contracts/INTERFACES.md §7 measures it to the first `leak_suspected`."""
    d = dev("a", YEAR_S, [freeze_alert(T0 + 500, True)], scenario="freeze_event",
            is_true_event=True, event_start_s=T0)
    d.first_verdict_ts = {"freeze_risk": T0 + 500}
    m = compute_metrics({"a": d})
    assert m.true_events_detected == 1
    assert m.median_time_to_detection_s is None


# ===========================================================================
# PCT_NEVER_REPORTING  (risk 4)
# ===========================================================================

def test_pct_never_reporting_counts_devices_silent_after_day_seven():
    results = {
        "alive": dev("alive", 30 * DAY, first_ts_s=T0, last_ts_s=T0 + 30 * DAY),
        "dead": dev("dead", 3 * DAY, first_ts_s=T0, last_ts_s=T0 + 3 * DAY),
    }
    m = compute_metrics(results)
    assert m.devices_after_day7 == 2
    assert m.never_reporting == 1
    assert m.pct_never_reporting == pytest.approx(50.0)


def test_pct_never_reporting_is_none_when_the_record_is_too_short():
    """A 5-day run cannot answer a 7-day question. Reporting 0% there would be
    the quietest possible way to hide a 20% install failure rate."""
    results = {"a": dev("a", 5 * DAY, first_ts_s=T0, last_ts_s=T0 + 5 * DAY)}
    m = compute_metrics(results)
    assert m.pct_never_reporting is None
    assert any("not\nmeasurable" in w.replace(" ", "\n") or "measurable" in w
               for w in m.warnings)
    assert "n/a" in format_report(m)


def test_a_device_installed_after_the_cutoff_is_not_judged():
    """It has not had its seven days yet; counting it either way is wrong."""
    results = {
        "early": dev("early", 30 * DAY, first_ts_s=T0, last_ts_s=T0 + 30 * DAY),
        "late": dev("late", DAY, first_ts_s=T0 + 20 * DAY,
                    last_ts_s=T0 + 21 * DAY),
    }
    m = compute_metrics(results)
    assert m.devices_after_day7 == 1
    assert m.pct_never_reporting == pytest.approx(0.0)


def test_a_device_whose_last_uplink_is_exactly_on_the_cutoff_is_never_reporting():
    """'Zero uplinks *after* day 7': an uplink at exactly day 7 is not after
    it. Stating the inequality is the whole point of this test."""
    results = {"a": dev("a", 7 * DAY, first_ts_s=T0, last_ts_s=T0 + 7 * DAY),
               "b": dev("b", 30 * DAY, first_ts_s=T0, last_ts_s=T0 + 30 * DAY)}
    m = compute_metrics(results)
    assert m.never_reporting == 1


# ===========================================================================
# §1 / §4 / §6 DECODING
# ===========================================================================

def encode_payload(pipe_c, ambient_c, battery_mv=3600, install_verified=True,
                   tamper=False, cold_warning=False, reason=0):
    """The §1 encoder, written independently of `decode_payload`."""
    flags = ((1 if install_verified else 0) | (2 if tamper else 0)
             | (4 if cold_warning else 0))
    return struct.pack(">hhHBB", int(round(pipe_c * 100)),
                       int(round(ambient_c * 100)), battery_mv, flags, reason)


def envelope(device_id, ts_s, payload, f_cnt=1, label=None, f_port=1):
    iso = (__import__("datetime").datetime
           .fromtimestamp(ts_s, __import__("datetime").timezone.utc)
           .strftime("%Y-%m-%dT%H:%M:%S.%f000Z"))
    obj = {
        "end_device_ids": {"device_id": device_id,
                           "dev_eui": "70B3D57ED000%04X" % (hash(device_id) & 0xFFFF)},
        "received_at": iso,
        "uplink_message": {
            "f_port": f_port, "f_cnt": f_cnt,
            "frm_payload": base64.b64encode(payload).decode("ascii"),
            "rx_metadata": [{"gateway_ids": {"gateway_id": "gw-a"},
                             "rssi": -112, "snr": 2.5}],
            "settings": {"data_rate": {"lora": {"spreading_factor": 10,
                                                "bandwidth": 125000}}},
        },
    }
    if label is not None:
        obj["_label"] = label
    return obj


def test_payload_decode_matches_the_contract_example():
    """contracts/INTERFACES.md §3 worked example, round-tripped."""
    fields = decode_payload(encode_payload(11.25, 18.40, 3410))
    assert fields["pipe_c"] == pytest.approx(11.25)
    assert fields["ambient_c"] == pytest.approx(18.40)
    assert fields["delta_c"] == pytest.approx(-7.15)
    assert fields["battery_mv"] == 3410
    assert fields["flags"] == {"install_verified": True, "tamper": False,
                               "cold_warning": False}
    assert fields["uplink_reason"] == "heartbeat"


def test_negative_temperatures_are_signed_int16():
    fields = decode_payload(encode_payload(-12.5, -30.0))
    assert fields["pipe_c"] == pytest.approx(-12.5)
    assert fields["ambient_c"] == pytest.approx(-30.0)


def test_all_three_flag_bits_decode_independently():
    for verified in (False, True):
        for tamper in (False, True):
            for cold in (False, True):
                f = decode_payload(encode_payload(
                    10.0, 20.0, install_verified=verified, tamper=tamper,
                    cold_warning=cold))["flags"]
                assert f == {"install_verified": verified, "tamper": tamper,
                             "cold_warning": cold}


@pytest.mark.parametrize("reason,expected", [
    (0, "heartbeat"), (1, "threshold"), (2, "boot"), (3, "install_probe"),
    (4, "reserved_4"), (255, "reserved_255"),
])
def test_unknown_uplink_reason_decodes_rather_than_throwing(reason, expected):
    """§3: 'a decoder that throws takes the whole webhook down'."""
    assert decode_payload(encode_payload(10.0, 20.0,
                                         reason=reason))["uplink_reason"] == expected


def test_a_wrong_length_payload_is_a_decode_error_not_a_crash():
    with pytest.raises(DecodeError):
        decode_payload(b"\x00" * 7)
    with pytest.raises(DecodeError):
        decode_payload(b"\x00" * 9)


@pytest.mark.parametrize("text,expected", [
    ("2026-10-14T03:00:00Z", 1791946800),
    ("2026-10-14T03:00:00.000000000Z", 1791946800),
    ("2026-10-14T03:00:00.123456789Z", 1791946800),
    ("2026-10-14T03:00:00+00:00", 1791946800),
    ("2026-10-14T04:00:00+01:00", 1791946800),
])
def test_received_at_parses_what_a_real_lns_sends(text, expected):
    """The Things Stack emits nanoseconds and a trailing Z; Python 3.9's
    `fromisoformat` rejects both. Getting this wrong shifts every dwell window
    in the evaluation."""
    assert parse_received_at(text) == expected


def test_a_naive_timestamp_is_read_as_utc_not_local():
    assert parse_received_at("2026-10-14T03:00:00") == 1791946800


def test_an_unparseable_timestamp_is_a_decode_error():
    with pytest.raises(DecodeError):
        parse_received_at("yesterday afternoon")


def test_decode_record_builds_the_sample_the_detector_expects():
    obj = envelope("leak-0001", T0, encode_payload(11.0, 20.0, 3400,
                                                   install_verified=True),
                   label={"scenario": "slow_leak", "is_true_event": True,
                          "event_start_s": T0, "device_id": "leak-0001"})
    record = decode_record(obj)
    assert record.device_id == "leak-0001"
    assert record.sample.pipe_c == pytest.approx(11.0)
    assert record.sample.install_verified is True
    assert record.scenario == "slow_leak"
    assert record.is_true_event is True
    assert record.event_start_s == T0
    assert record.used_decoded_payload is False


def test_extra_envelope_fields_are_ignored_not_rejected():
    """§4: 'extra fields must be ignored, not rejected'."""
    obj = envelope("leak-0001", T0, encode_payload(11.0, 20.0))
    obj["correlation_ids"] = ["as:up:01"]
    obj["uplink_message"]["consumed_airtime"] = "0.061696s"
    obj["uplink_message"]["decoded_payload"] = {"pipe_c": 999.0}
    record = decode_record(obj)
    # And frm_payload wins over a caller-supplied decoded_payload (§4).
    assert record.sample.pipe_c == pytest.approx(11.0)
    assert record.used_decoded_payload is False


def test_a_non_sensor_fport_is_skipped():
    obj = envelope("leak-0001", T0, encode_payload(11.0, 20.0), f_port=2)
    with pytest.raises(DecodeError):
        decode_record(obj)


def test_an_unlabelled_line_is_still_scored_as_a_non_event():
    obj = envelope("leak-0001", T0, encode_payload(11.0, 20.0))
    record = decode_record(obj)
    assert record.scenario == "unlabelled" and record.is_true_event is False


def test_decoded_payload_fallback_is_used_but_flagged():
    obj = envelope("leak-0001", T0, encode_payload(11.0, 20.0))
    del obj["uplink_message"]["frm_payload"]
    obj["uplink_message"]["decoded_payload"] = {
        "pipe_c": 7.5, "ambient_c": 20.0, "battery_mv": 3300,
        "flags": {"install_verified": True, "tamper": False}}
    record = decode_record(obj)
    assert record.sample.pipe_c == pytest.approx(7.5)
    assert record.used_decoded_payload is True


def test_garbage_base64_is_a_decode_error():
    obj = envelope("leak-0001", T0, encode_payload(11.0, 20.0))
    obj["uplink_message"]["frm_payload"] = "not base64 !!!"
    with pytest.raises(DecodeError):
        decode_record(obj)


def test_load_skips_bad_lines_dedupes_f_cnt_and_sorts_by_time(tmp_path):
    path = tmp_path / "mixed.jsonl"
    good = envelope("leak-0001", T0 + 900, encode_payload(11.0, 20.0), f_cnt=2)
    earlier = envelope("leak-0001", T0, encode_payload(12.0, 20.0), f_cnt=1)
    lines = [
        json.dumps(good),
        json.dumps(good),                       # exact duplicate (dev, f_cnt)
        json.dumps(earlier),                    # out of order in the file
        "{not json",
        json.dumps({"end_device_ids": {"device_id": "x"}}),   # no uplink_message
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    by_device, stats = evaluate.load(str(path))
    assert stats.decoded == 2
    assert stats.duplicates == 1
    assert stats.skipped == 2
    assert [r.sample.ts_s for r in by_device["leak-0001"]] == [T0, T0 + 900]


def test_device_label_prefers_the_specific_scenario_over_quiet():
    records = [
        evaluate.Record("a", 0, evaluate.Sample(T0, 19.0, 20.0, 3600, True, False),
                        "quiet", False, None),
        evaluate.Record("a", 1, evaluate.Sample(T0 + 1, 19.0, 20.0, 3600, True, False),
                        "slow_leak", True, T0 + 500),
    ]
    assert evaluate._device_label(records) == ("slow_leak", True, T0 + 500)


def test_a_true_event_with_no_start_time_is_still_counted():
    """Dropping it would silently shrink the recall denominator."""
    records = [evaluate.Record(
        "a", 0, evaluate.Sample(T0, 19.0, 20.0, 3600, True, False),
        "slow_leak", True, None)]
    scenario, is_true, start = evaluate._device_label(records)
    assert (scenario, is_true, start) == ("slow_leak", True, T0)


# ===========================================================================
# REPORT AND CLI
# ===========================================================================

def test_report_states_the_pass_fail_verdict_explicitly():
    m = compute_metrics({"a": dev("a", 10 * YEAR_S, [leak_alert(T0)])})
    text = format_report(m)
    assert "P1 GATE: PASS" in text
    assert "nuisance_alerts_per_device_year" in text
    m2 = compute_metrics({"a": dev("a", YEAR_S, [leak_alert(T0)])})
    assert "P1 GATE: FAIL" in format_report(m2)


def test_report_shows_the_exposure_normalisation_it_used():
    m = compute_metrics({"a": dev("a", YEAR_S, gap_excluded_s=3 * DAY,
                                  unverified_excluded_s=DAY)})
    text = format_report(m, max_gap_s=DAY)
    assert "device-years observed" in text
    assert "3.00 device-days dropped by that cap" in text
    assert "1.00 device-days dropped as unverified" in text


def test_metrics_to_dict_is_json_serialisable_even_when_infinite():
    m = compute_metrics({"a": dev("a", 0, [leak_alert(T0)], samples=1,
                                  last_ts_s=T0)})
    blob = json.dumps(evaluate.metrics_to_dict(m))
    assert json.loads(blob)["nuisance_alerts_per_device_year"] is None


def test_cli_runs_end_to_end_and_writes_json(tmp_path, capsys):
    path = tmp_path / "fleet.jsonl"
    synthesise_fleet(str(path), devices=6, days=12, seed=3)
    out_json = tmp_path / "metrics.json"
    rc = evaluate.main(["--input", str(path), "--json", str(out_json)])
    assert rc == 0
    printed = capsys.readouterr().out
    assert "P1 GATE:" in printed
    blob = json.loads(out_json.read_text(encoding="utf-8"))
    assert blob["devices"] == 6
    assert "nuisance_alerts_per_device_year" in blob


def test_cli_strict_mode_exits_nonzero_on_a_failing_gate(tmp_path, capsys):
    path = tmp_path / "fleet.jsonl"
    synthesise_fleet(str(path), devices=4, days=12, seed=4)
    # An impossible gate, to prove --strict is wired to the verdict.
    rc = evaluate.main(["--input", str(path), "--gate", "0.0", "--strict"])
    capsys.readouterr()
    assert rc == 1


def test_cli_reports_a_missing_input_without_a_traceback(capsys):
    rc = evaluate.main(["--input", "/nonexistent/fleet.jsonl"])
    assert rc == 2
    assert "cannot read" in capsys.readouterr().err


# ===========================================================================
# END TO END on synthetic data
# ===========================================================================

def test_end_to_end_on_a_synthetic_fleet(tmp_path):
    """Smoke test of the whole pipe: JSONL -> decode -> detect -> metrics.

    The thresholds here are loose on purpose. This file's job is to prove the
    arithmetic; the *numbers* are measured against `simulator/` and written
    down in README.md, because a gate passed on data this module generated
    itself would be marking its own homework.
    """
    path = tmp_path / "fleet.jsonl"
    scenarios = synthesise_fleet(str(path), devices=20, days=21, seed=7)
    metrics, stats, results = run(str(path))
    assert stats.decoded > 2000 and stats.skipped == 0
    assert metrics.devices == 20
    assert metrics.device_years > 0.5
    assert metrics.true_events == sum(
        1 for s in scenarios.values() if s in ("slow_leak", "fast_leak",
                                               "freeze_event"))
    assert metrics.true_event_recall is not None and metrics.true_event_recall > 0.5
    assert metrics.nuisance_alerts_per_device_year < 2.0
    # Every unverified device must be silent, on every seed.
    for device_id, scenario in scenarios.items():
        if scenario == "install_unverified":
            assert results[device_id].alerts == []


def test_a_fleet_of_only_quiet_devices_raises_nothing(tmp_path):
    """The purest nuisance test available: no water moves anywhere."""
    path = tmp_path / "quiet.jsonl"
    synthesise_fleet(str(path), devices=10, days=30, seed=11,
                     only=("quiet", "cold_basement_winter"))
    metrics, _, _ = run(str(path))
    assert metrics.nuisance_alerts == 0
    assert metrics.passed


def test_a_fleet_of_only_normal_household_use_raises_nothing(tmp_path):
    path = tmp_path / "normal.jsonl"
    synthesise_fleet(str(path), devices=10, days=30, seed=12,
                     only=("appliance_use", "shower_morning"))
    metrics, _, _ = run(str(path))
    assert metrics.nuisance_alerts == 0, \
        "normal appliance use must not page anyone"
    assert metrics.passed


def test_most_fast_leaks_are_caught_quickly(tmp_path):
    """Bursts are the easy case and must be caught in hours, not days.

    Not *all* of them, and the shortfall is physical rather than a tuning
    miss. `delta_c = pipe_c - ambient_c` shrinks when the room cools toward
    mains temperature, so a burst in an unheated garage on a cooling evening
    becomes less visible the longer it runs. See the README: a device in a
    wide-swinging space also earns a wider trigger, and a burst whose depth is
    comparable to that swing can be missed.

    A device that went silent before its event also stays in the recall
    denominator: it missed a real leak, and 08-roadmap.md §8 risk 4 is
    precisely about not quietly dropping those.
    """
    path = tmp_path / "bursts.jsonl"
    synthesise_fleet(str(path), devices=8, days=30, seed=13, only=("fast_leak",))
    metrics, _, results = run(str(path))
    alive = [r for r in results.values()
             if r.event_start_s is not None and r.last_ts_s > r.event_start_s]
    assert len(alive) >= 6
    caught = [r for r in alive
              if any(a.verdict == "leak_suspected" and a.true_positive
                     for a in r.alerts)]
    assert len(caught) >= len(alive) - 1
    assert metrics.median_time_to_detection_s is not None
    # 7 h, not the 4 h an earlier version of this test asserted. 4 h was
    # aspiration, not measurement: burst latency is DWELL_S, because the
    # depth-based escape hatch (DWELL_DEEP_MULT) almost never fires - bursts
    # and appliance use overlap in instantaneous depth in the simulator, which
    # is realistic. Measured 6.17 h median across seeds 13/29/41/57/71 with a
    # 6.38 h worst case, so this bounds the real behaviour with headroom
    # instead of encoding a target the architecture deliberately trades away.
    #
    # An attempt to actually fix the latency, via a continuity-based hatch, is
    # documented as rejected in detector.py: it broke the quiet-fleet nuisance
    # tests. Nuisance is the P1 gate; latency is not.
    assert metrics.median_time_to_detection_s <= 7 * 3600
    assert metrics.nuisance_alerts == 0


def test_slow_leak_sensitivity_tracks_the_room_to_mains_temperature_gap(tmp_path):
    """A documented limitation of the modality, asserted so it stays visible.

    The signal a thermal detector has to work with is proportional to the gap
    between room temperature and mains temperature. A weeping joint in a 21 C
    utility closet fed by 14 C mains moves the pipe several degrees; the same
    weep in an 11 C basement fed by 7 C mains moves it under two, which is
    inside the noise floor of a real clamp-on install. So slow-leak recall is
    high in heated space and poor in cold space, and no amount of threshold
    tuning fixes that - it is why 08-roadmap.md §7 claim 2 says the fallback
    is acoustic or pressure sensing rather than a better classifier.
    """
    path = tmp_path / "leaks.jsonl"
    synthesise_fleet(str(path), devices=10, days=30, seed=13,
                     only=("slow_leak",))
    metrics, _, results = run(str(path))
    assert metrics.true_events == 10
    assert 0 < metrics.true_events_detected < 10, (
        "if this ever reads 10, the fixture's cold-basement archetype has "
        "stopped being cold and the test has lost its point")
    # Whatever it misses, it must not cry wolf while missing it.
    assert metrics.nuisance_alerts_per_device_year < 0.5
    del results


# ===========================================================================
# the fixture generator
# ===========================================================================

#: Scenario mix. Weighted to look like housing stock rather than a test
#: harness: most devices are boring, which is exactly why the nuisance rate is
#: hard to get under 0.5.
_SCENARIO_MIX = (
    ("quiet", 4), ("appliance_use", 5), ("shower_morning", 4),
    ("cold_basement_winter", 3), ("slow_leak", 2), ("fast_leak", 1),
    ("freeze_event", 1), ("sensor_fault_stuck", 1), ("sensor_fault_noisy", 1),
    ("install_unverified", 1),
)

_TRUE_EVENT_SCENARIOS = ("slow_leak", "fast_leak", "freeze_event")

#: Where the sensor lives: (ambient mean, diurnal amplitude, mains temp).
_ARCHETYPES = (
    (21.0, 1.2, 14.0),      # heated utility closet
    (11.0, 0.8, 7.0),       # unheated basement in winter
    (15.0, 3.5, 10.0),      # attached garage
)

_STEP_S = 300               # 5-minute internal step
_HEARTBEAT_S = 900          # uplink cadence when nothing is happening


def synthesise_fleet(path, devices=20, days=21, seed=7, only=None):
    """Write contracts/INTERFACES.md §6 labelled JSONL to `path`.

    A deliberately small stand-in for `simulator/`, so this test module has no
    dependency on a component built in parallel. One-pole thermal lag, diurnal
    ambient, flow events that pull the pipe toward mains temperature, and the
    ten §6 scenarios. Returns ``{device_id: scenario}``.
    """
    rng = random.Random(seed)
    pool = [s for s, n in _SCENARIO_MIX for _ in range(n)]
    if only:
        pool = [s for s in pool if s in only] or list(only)

    scenarios = {}
    steps = (days * 86400) // _STEP_S
    with io.open(path, "w", encoding="utf-8") as handle:
        for index in range(devices):
            device_id = "leak-%04d" % (index + 1)
            scenario = pool[index % len(pool)]
            scenarios[device_id] = scenario
            _write_device(handle, rng, device_id, scenario, steps)
    return scenarios


def _write_device(handle, rng, device_id, scenario, steps):
    amb_mean, amb_amp, mains = _ARCHETYPES[
        1 if scenario == "cold_basement_winter" else rng.randrange(len(_ARCHETYPES))]
    standoff = rng.uniform(0.3, 1.2)
    # Thermal time constant of the pipe against its room. ~2300 s for a bare
    # 1/2" water-filled copper pipe under natural convection; several times
    # that once it is lagged. The slow end is the hard end: the pipe does not
    # recover between appliance fills.
    tau_idle = rng.uniform(2000.0, 8000.0)
    tau_flow = 420.0                                # and the water fast
    event_start_step = int(steps * rng.uniform(0.35, 0.6))
    # Risk 4: a slice of the fleet simply stops reporting in the first week.
    dies_at = int(steps * rng.uniform(0.07, 0.20)) if rng.random() < 0.08 else None

    pipe = amb_mean - standoff
    stuck_value = None
    f_cnt = 0
    last_uplink = -10 ** 9
    install_verified = scenario != "install_unverified"
    label = {"scenario": scenario,
             "is_true_event": scenario in _TRUE_EVENT_SCENARIOS,
             "device_id": device_id}

    for step in range(steps):
        t = T0 + step * _STEP_S
        hour = (step * _STEP_S / 3600.0) % 24.0
        ambient = (amb_mean + amb_amp * math.sin(2 * math.pi * (hour - 15) / 24.0)
                   + rng.gauss(0, 0.08))
        if scenario == "freeze_event" and step >= event_start_step:
            # A cold snap drags the whole space below freezing.
            ambient -= min(18.0, 0.012 * (step - event_start_step) * _STEP_S / 60.0)

        flow, target = _flow_target(scenario, step, event_start_step, hour,
                                    ambient, mains, standoff, rng)
        tau = tau_flow if flow else tau_idle
        pipe += (target - pipe) * (1.0 - math.exp(-_STEP_S / tau))

        reported = pipe
        tamper = False
        if scenario == "sensor_fault_stuck" and step >= event_start_step:
            if stuck_value is None:
                stuck_value = round(pipe, 2)
            reported = stuck_value
        elif scenario == "sensor_fault_noisy" and step >= event_start_step:
            if rng.random() < 0.55:
                reported = rng.choice([-55.0, 92.0, rng.uniform(-40.0, 80.0)])
            tamper = rng.random() < 0.02

        if dies_at is not None and step >= dies_at:
            break

        # Heartbeat, plus threshold-triggered uplinks while something is
        # happening - which is the harder case for the dwell integrator.
        due = (t - last_uplink) >= _HEARTBEAT_S
        urgent = flow and (t - last_uplink) >= _STEP_S
        if not (due or urgent):
            continue
        last_uplink = t
        reason = 0 if due and not urgent else 1
        payload = encode_payload(
            max(-327.0, min(327.0, reported + rng.gauss(0, 0.02))),
            ambient, battery_mv=3600 - step // 40,
            install_verified=install_verified, tamper=tamper,
            cold_warning=reported < 4.0, reason=reason)
        line = dict(label)
        line["event_start_s"] = (T0 + event_start_step * _STEP_S
                                 if label["is_true_event"] else None)
        handle.write(json.dumps(envelope(device_id, t, payload, f_cnt=f_cnt,
                                         label=line)) + "\n")
        f_cnt += 1


def _flow_target(scenario, step, event_start_step, hour, ambient, mains,
                 standoff, rng):
    """`(is_flowing, pipe_target_c)` for this step."""
    idle = ambient - standoff
    minute_of_day = (step * _STEP_S // 60) % 1440

    if scenario in ("slow_leak", "fast_leak") and step >= event_start_step:
        if scenario == "fast_leak":
            return True, mains
        # A weeping joint: continuous but partial flow, so the pipe sits
        # between mains and room. This is the one that has to be caught.
        return True, idle - (idle - mains) * 0.55

    # Normal household draw. Every scenario except `quiet` gets some.
    if scenario in ("appliance_use", "slow_leak", "fast_leak",
                    "cold_basement_winter", "sensor_fault_stuck",
                    "sensor_fault_noisy", "install_unverified"):
        # Dishwasher ~19:00 and washing machine ~20:30, both intermittent
        # fills rather than continuous flow.
        for start in (1140, 1230):
            offset = minute_of_day - start
            if 0 <= offset < 90 and (offset // 5) % 4 == 0:
                return True, mains
    if scenario in ("shower_morning", "appliance_use", "slow_leak",
                    "fast_leak", "cold_basement_winter"):
        for start, length in ((420, 14), (1320, 11)):
            if 0 <= minute_of_day - start < length:
                return True, mains + 1.0
    del hour, rng
    return False, idle


if __name__ == "__main__":        # a dev convenience, not part of the tests
    import argparse

    p = argparse.ArgumentParser(
        description="Write the test fixture fleet to a file, for eyeballing "
                    "the harness when simulator/ is unavailable.")
    p.add_argument("--out", default=os.path.join(tempfile.gettempdir(),
                                                 "fixture_fleet.jsonl"))
    p.add_argument("--devices", type=int, default=40)
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--seed", type=int, default=7)
    a = p.parse_args()
    mix = synthesise_fleet(a.out, devices=a.devices, days=a.days, seed=a.seed)
    print("%s: %d devices, %d days, seed %d" % (a.out, a.devices, a.days, a.seed))
    for name in sorted(set(mix.values())):
        print("  %-22s %d" % (name, sum(1 for v in mix.values() if v == name)))
