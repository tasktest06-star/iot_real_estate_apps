"""The insurer portfolio / loss-avoided report (contract §8, openIDS-flavoured).

This is the module most likely to be read by someone deciding whether to trust
the vendor, so it is written to be falsifiable.

**What it reports: surrogates.** 08-roadmap.md §8 risk 3 -- "Loss reduction
cannot be proven at this scale" -- and §9's "explicitly refuse to promise"
column settle the question. Escape-of-water frequency is ~1.50 claims per 100
house-years (01-market-and-insurance-case.md §1.0), so a pilot of 50-100
devices over one winter observes single-digit house-years of exposure: nothing
with a confidence interval worth printing. The roadmap's committed mitigation is
to "agree a surrogate metric in writing in P0" -- time-to-shutoff,
mitigated-event case studies, a severity model -- and those are the numbers
below.

**What it refuses to report.** There is no "claims reduced by X%" field in this
response, and there never will be one at pilot scale. Every response carries a
`not_claimed` block that says so in the payload itself, so the refusal travels
with the data into whatever deck or spreadsheet copies it.

Three rules the code follows:
  * every number is either counted from this database or an external benchmark
    with a citation in `BENCHMARKS`. There is no third category;
  * an external benchmark is never presented as this device's performance. The
    only arithmetic done with one is the severity model, and its output is
    labelled `illustrative`;
  * where a metric cannot be computed here, the field says why instead of
    holding a zero that reads like a measurement (see `never_reporting`).

What this module deliberately does NOT do: no premium-credit calculation (06
risk 6: a filed discount is the carrier's to file, and anti-rebating rules make
it unfilable in places), no claims matching, no risk score.
"""

import sqlite3
import statistics
from typing import Any, Dict, List, Optional

from . import db, detector_bridge, valve

SECONDS_PER_YEAR = 31557600.0  # Julian year, so device-days -> house-years is exact
SECONDS_PER_DAY = 86400

# External benchmarks. Values quoted from the pack; nothing here is measured by
# this prototype and nothing here is a promise.
BENCHMARKS = {
    "mean_water_claim_severity_usd": {
        "value": 15400,
        "source": "01-market-and-insurance-case.md §1.0",
        "provenance": "iii.org on ISO/Verisk data, 2019-2023 average",
        "note": "US homeowners water-damage-and-freezing severity. Amica quotes >$21,000 "
                "for 2026; the lower figure is used so the severity model is conservative.",
    },
    "water_claims_per_100_house_years": {
        "value": 1.50,
        "source": "01-market-and-insurance-case.md §1.0",
        "provenance": "iii.org on ISO/Verisk data; IBHS independently confirms ~1 in 60",
        "note": "This is the number that makes a pilot-scale frequency claim impossible.",
    },
    "external_severity_reduction_fraction": {
        "value": 0.24,
        "source": "01-market-and-insurance-case.md §1.1",
        "provenance": "Flume carrier study cited by Amica (60% frequency / 24% severity)",
        "note": "A different vendor, a different sensing modality (whole-home flow, not "
                "clamp-on thermal). Used as a stated external benchmark for the severity "
                "model only. It is NOT this device's measured effect.",
    },
}

# 08-roadmap.md §3 P1 gate: <0.5 nuisance alerts/device-year over >=90 days on
# >=50 installed devices. All three conditions, or the gate is not evaluable.
P1_NUISANCE_GATE_PER_DEVICE_YEAR = 0.5
P1_MIN_DEVICES = 50
P1_MIN_DAYS = 90

# 08-roadmap.md §8 risk 3's leading indicator: "Pilot below ~2,000 house-years".
HOUSE_YEARS_FREQUENCY_FLOOR = 2000

# How long after an alert a confirmed valve close is still counted as the
# response to that alert.
SHUTOFF_ATTRIBUTION_WINDOW_S = 24 * 3600


def _median(values: List[float]) -> Optional[float]:
    return round(float(statistics.median(values)), 1) if values else None


def _count(conn: sqlite3.Connection, sql: str, args: Any = ()) -> int:
    row = conn.execute(sql, args).fetchone()
    return int(row[0] or 0)


def portfolio_report(
    conn: sqlite3.Connection,
    window_days: int = P1_MIN_DAYS,
    now_s: Optional[int] = None,
) -> Dict[str, Any]:
    now = db.now_s() if now_s is None else int(now_s)
    window_days = max(1, int(window_days))
    window_start = now - window_days * SECONDS_PER_DAY

    # A stale pending command must not be presented as if it were in flight.
    valve.expire_stale(conn, now_s=now)

    devices = [db.row_to_dict(r) for r in conn.execute("SELECT * FROM devices").fetchall()]
    n_devices = len(devices)

    # ---- exposure ---------------------------------------------------------
    # Observed exposure only: from each device's first uplink (or the window
    # start, whichever is later) to its last uplink. A device that stopped
    # reporting stops accruing exposure here -- counting it to "now" would
    # inflate house-years with time the device was not watching anything.
    exposure_s = 0
    for d in devices:
        first, last = d.get("first_seen_ts"), d.get("last_seen_ts")
        if first is None or last is None:
            continue
        start, end = max(int(first), window_start), min(int(last), now)
        if end > start:
            exposure_s += end - start
    house_years = exposure_s / SECONDS_PER_YEAR
    device_days = exposure_s / float(SECONDS_PER_DAY)

    reporting_7d = sum(1 for d in devices
                       if d.get("last_seen_ts") and now - int(d["last_seen_ts"]) <= 7 * SECONDS_PER_DAY)
    silent_7d = n_devices - reporting_7d
    install_verified = sum(1 for d in devices if d.get("install_verified"))
    alerting_enabled = sum(1 for d in devices if d.get("alerting_enabled"))
    tampered = sum(1 for d in devices if d.get("tamper"))

    # ---- alerts -----------------------------------------------------------
    alert_rows = [db.row_to_dict(r) for r in conn.execute(
        "SELECT * FROM alerts WHERE raised_ts >= ?", (window_start,)).fetchall()]
    by_status = {}  # type: Dict[str, int]
    by_verdict = {}  # type: Dict[str, int]
    for a in alert_rows:
        by_status[a["status"]] = by_status.get(a["status"], 0) + 1
        by_verdict[a["verdict"]] = by_verdict.get(a["verdict"], 0) + 1
    raised_in_shadow = sum(1 for a in alert_rows if a["raised_in_shadow"])
    notified = sum(1 for a in alert_rows if a["notified_ts"] is not None)
    acked = [a for a in alert_rows if a["acked_ts"] is not None]
    nuisance = [a for a in acked if a["was_nuisance"]]
    confirmed_true = [a for a in acked if a["was_nuisance"] == 0]
    unreviewed = [a for a in alert_rows if a["acked_ts"] is None]

    nuisance_rate = round(len(nuisance) / float(len(acked)), 3) if acked else None
    nuisance_per_device_year = (round(len(nuisance) / house_years, 3)
                                if house_years > 0 else None)

    gate_blockers = []  # type: List[str]
    if n_devices < P1_MIN_DEVICES:
        gate_blockers.append("%d devices, gate needs >=%d" % (n_devices, P1_MIN_DEVICES))
    mean_days_per_device = (device_days / n_devices) if n_devices else 0.0
    if mean_days_per_device < P1_MIN_DAYS:
        gate_blockers.append("mean observed %.1f days per device, gate needs >=%d"
                             % (mean_days_per_device, P1_MIN_DAYS))
    if unreviewed:
        gate_blockers.append("%d alert(s) not yet reviewed, so the nuisance count is a "
                             "lower bound" % len(unreviewed))
    if detector_bridge.using_fallback():
        gate_blockers.append("the backend's fallback detector is active, not detection/"
                             "detector.py: any rate measured here is not the product's")

    latencies = [int(a["detection_latency_s"]) for a in alert_rows
                 if a["detection_latency_s"] is not None]

    # ---- valve ------------------------------------------------------------
    cmd_rows = [db.row_to_dict(r) for r in conn.execute(
        "SELECT * FROM valve_commands WHERE issued_ts >= ?", (window_start,)).fetchall()]
    cmd_by_status = {}  # type: Dict[str, int]
    refusal_reasons = {}  # type: Dict[str, int]
    for c in cmd_rows:
        cmd_by_status[c["status"]] = cmd_by_status.get(c["status"], 0) + 1
        if c["status"] == "refused":
            key = c["reason_code"] or "unspecified"
            refusal_reasons[key] = refusal_reasons.get(key, 0) + 1
    confirmed_closes = [c for c in cmd_rows
                        if c["command"] == "close" and c["status"] == "confirmed"]
    shutoff_latencies = [int(c["confirmed_ts"]) - int(c["issued_ts"])
                         for c in confirmed_closes if c["confirmed_ts"] is not None]

    # Mitigation the backend can actually see: a confirmed true alert followed
    # by a confirmed valve close on the same device inside the attribution
    # window. Everything else -- a plumber dispatched, a homeowner closing a
    # stopcock -- is real mitigation that this backend cannot observe, so it is
    # listed as unobservable rather than assumed.
    mitigated = []  # type: List[Dict[str, Any]]
    for a in confirmed_true:
        match = None
        for c in confirmed_closes:
            if c["dev_eui"] != a["dev_eui"] or c["confirmed_ts"] is None:
                continue
            gap = int(c["confirmed_ts"]) - int(a["raised_ts"])
            if 0 <= gap <= SHUTOFF_ATTRIBUTION_WINDOW_S:
                match = (c, gap)
                break
        mitigated.append({
            "alert_id": a["id"],
            "device_id": a["device_id"],
            "verdict": a["verdict"],
            "raised_at": db.iso(a["raised_ts"]),
            "raised_in_shadow": bool(a["raised_in_shadow"]),
            "detection_latency_s": a["detection_latency_s"],
            "operator_note": a["note"],
            "confirmed_shutoff": match is not None,
            "shutoff_command_id": match[0]["id"] if match else None,
            "time_to_shutoff_s": match[1] if match else None,
        })
    with_shutoff = [m for m in mitigated if m["confirmed_shutoff"]]

    # ---- severity model ---------------------------------------------------
    severity = BENCHMARKS["mean_water_claim_severity_usd"]["value"]
    reduction = BENCHMARKS["external_severity_reduction_fraction"]["value"]
    lower_usd = round(len(with_shutoff) * severity * reduction, 2)
    upper_usd = round(len(mitigated) * severity * reduction, 2)

    report = {
        "generated_at": db.iso(now),
        "window_days": window_days,
        "window_start": db.iso(window_start),
        "schema": {
            "name": "openIDS Homeowners v1.0 (flavour)",
            "conformance": "none claimed -- peril grouping and severity vocabulary are "
                           "borrowed; this is not a conformance statement",
            "peril_group": "water damage and freezing",
            "note": "05-open-source-stack.md §5.1: adopt the openIDS spec, not the "
                    "openidl-main platform, which is dormant.",
        },
        "portfolio": {
            "devices_known": n_devices,
            "devices_reporting_last_7d": reporting_7d,
            "devices_silent_over_7d": silent_7d,
            "install_verified": install_verified,
            "install_unverified": n_devices - install_verified,
            "tamper_flagged": tampered,
            "alerting_enabled": alerting_enabled,
            "in_shadow_mode": n_devices - alerting_enabled,
            "observed_device_days": round(device_days, 2),
            "observed_house_years": round(house_years, 4),
            "install_verification_note":
                "08-roadmap.md §8 risk 4: unverified installs are not counted as "
                "protected exposure. An unverified install is indistinguishable from a "
                "sensor fault in the data.",
            "never_reporting": {
                "value": None,
                "why_not_computed":
                    "contract §7 wants pct_never_reporting (risk 4's leading indicator), "
                    "but a device row here is created by its first uplink, so a unit that "
                    "never reported is invisible to this database. The denominator is the "
                    "shipped-units roster, which lives in the provisioning system this "
                    "prototype does not have. `devices_silent_over_7d` is the closest "
                    "thing it can honestly compute.",
            },
        },
        "detection": {
            "detector_implementation": detector_bridge.source(),
            "detector_note": detector_bridge.note(),
            "alerts_raised": len(alert_rows),
            "by_status": by_status,
            "by_verdict": by_verdict,
            "raised_in_shadow_mode": raised_in_shadow,
            "customer_notifications_sent": notified,
            "reviewed_by_operator": len(acked),
            "awaiting_review": len(unreviewed),
            "confirmed_true_events": len(confirmed_true),
            "nuisance_alerts": len(nuisance),
            "nuisance_rate_of_reviewed": nuisance_rate,
            "nuisance_alerts_per_device_year": nuisance_per_device_year,
            "median_detection_latency_s": _median(latencies),
            "detection_latency_definition":
                "seconds from the first non-quiet sample of the episode to the alert being "
                "raised. This is NOT time-to-detection against physical event onset: that "
                "needs the simulator's ground-truth labels, which the backend deliberately "
                "ignores (contract §6) and detection/evaluate.py reports (contract §7).",
            "p1_gate": {
                "metric": "nuisance_alerts_per_device_year",
                "threshold": P1_NUISANCE_GATE_PER_DEVICE_YEAR,
                "observed": nuisance_per_device_year,
                "evaluable": not gate_blockers,
                "status": ("not_evaluable" if gate_blockers else
                           ("pass" if nuisance_per_device_year is not None
                            and nuisance_per_device_year < P1_NUISANCE_GATE_PER_DEVICE_YEAR
                            else "fail")),
                "blockers": gate_blockers,
                "definition": "08-roadmap.md §3 P1: <0.5 nuisance alerts per device-year "
                              "over >=90 days on >=50 installed devices against a frozen "
                              "evaluation set.",
            },
        },
        "mitigation": {
            "valve_commands_by_status": cmd_by_status,
            "valve_refusals_by_reason": refusal_reasons,
            "confirmed_closes": len(confirmed_closes),
            "median_time_to_confirmed_shutoff_s": _median(shutoff_latencies),
            "mitigated_events": mitigated,
            "mitigated_events_with_confirmed_shutoff": len(with_shutoff),
            "unobservable_mitigation_note":
                "A dispatched plumber or a homeowner closing a stopcock is mitigation this "
                "backend cannot see, so it is absent from these counts rather than "
                "estimated. Only a confirmed valve close within %d h of a confirmed true "
                "alert counts here." % (SHUTOFF_ATTRIBUTION_WINDOW_S // 3600),
            "fail_mode": "fail-as-is: a command on an unarmed device is refused (409) and "
                         "recorded, never attempted (05-open-source-stack.md §5.3).",
        },
        "loss_avoided_estimate": {
            "method": "severity model over observed mitigated events. A surrogate metric, "
                      "agreed in writing in P0 per 08-roadmap.md §8 risk 3 -- not a "
                      "measured loss outcome.",
            "arithmetic": "events x mean claim severity x external severity-reduction "
                          "fraction",
            "events_lower_bound": len(with_shutoff),
            "events_upper_bound": len(mitigated),
            "estimated_severity_avoided_usd_lower": lower_usd,
            "estimated_severity_avoided_usd_upper": upper_usd,
            "confidence": "illustrative [E]. Two of the three inputs are external "
                          "benchmarks measured on other vendors' devices; only the event "
                          "count comes from this pilot.",
            "inputs": BENCHMARKS,
            "statistical_power": {
                "observed_house_years": round(house_years, 4),
                "house_years_before_a_frequency_claim_is_discussable":
                    HOUSE_YEARS_FREQUENCY_FLOOR,
                "house_years_for_significance": "tens of thousands (08-roadmap.md §8 risk 3)",
                "frequency_claim_supportable": False,
                "why": "At %.2f claims per 100 house-years, %.4f observed house-years "
                       "expects %.4f claims. No reduction can be detected in that."
                       % (BENCHMARKS["water_claims_per_100_house_years"]["value"],
                          house_years,
                          house_years * BENCHMARKS["water_claims_per_100_house_years"]["value"] / 100.0),
            },
        },
        "not_claimed": [
            {"claim": "claim-frequency reduction (%)",
             "status": "explicitly not claimed",
             "why": "08-roadmap.md §9 P1 refuses to promise it and §8 risk 3 explains why: "
                    "escape-of-water frequency is ~1.5 per 100 house-years, so significance "
                    "needs tens of thousands of house-years. This pilot has "
                    "%.4f." % house_years},
            {"claim": "a specific detection sensitivity or recall figure",
             "status": "explicitly not claimed",
             "why": "08-roadmap.md §9 P1. Precision/recall against a frozen evaluation set "
                    "is a P2 deliverable (contract §7, detection/evaluate.py), and it is "
                    "not computed from live portfolio data because the backend has no "
                    "ground truth."},
            {"claim": "a filed premium discount or credit",
             "status": "explicitly not claimed",
             "why": "08-roadmap.md §9 P3 and §8 risk 6: a filing is the carrier's to make, "
                    "and anti-rebating rules bar the vendor from promising one. The product "
                    "sold here is loss control (NAIC Model #880), not a discount."},
            {"claim": "realised dollars saved",
             "status": "explicitly not claimed",
             "why": "`loss_avoided_estimate` is a model over observed events using external "
                    "severity benchmarks. Realised savings require matched claims data from "
                    "the carrier, which this system does not hold."},
            {"claim": "that a shadow-mode alert protected anyone",
             "status": "explicitly not claimed",
             "why": "%d of %d alerts in this window were raised in shadow mode and were "
                    "shown to no customer by design (standing rule 3). They measure the "
                    "nuisance rate; they do not mitigate anything."
                    % (raised_in_shadow, len(alert_rows))},
        ],
        "surrogate_metric_agreement": {
            "on_file": None,
            "note": "08-roadmap.md §8 risk 3 requires a surrogate metric agreed in writing "
                    "in P0. This backend holds no copy of that agreement, so the field "
                    "stays null rather than implying one exists.",
            "surrogates_reported": [
                "mitigated-event counts (with and without a confirmed shutoff)",
                "detection latency from first anomalous sample",
                "time to confirmed shutoff",
                "severity-model estimate, labelled illustrative",
                "nuisance alerts per device-year against the P1 gate",
            ],
        },
    }
    return report
