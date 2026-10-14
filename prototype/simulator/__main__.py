"""CLI for the fleet simulator.

    python3 -m simulator --devices 40 --days 30 --out fleet.jsonl
    python3 -m simulator --devices 40 --days 30 --post http://127.0.0.1:8000/api/v1/uplink

WHAT THIS DELIBERATELY DOES NOT DO
    - No third-party HTTP client. `--post` uses `urllib.request` from the standard library
      (INTERFACES.md §10 keeps `simulator/` dependency-free), which means no connection
      pooling and no HTTP/2: posting a 30-day 40-device run one request at a time is slow.
      That is a deliberate trade, and `--out` plus a bulk loader is the fast path.
    - No replay timing. Frames are emitted as fast as they are generated, not in real time.
      `--rate` paces requests only so a dev-mode backend is not knocked over.
    - It does not retry forever. A failed POST is counted and reported; the run does not
      silently drop frames, because a silent drop would corrupt the §7 never-reporting metric
      that the whole exercise is trying to measure.
"""

import argparse
import base64
import collections
import datetime
import heapq
import itertools
import json
import random
import sys
import time
import urllib.error
import urllib.request
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from . import fleet, scenarios

DEFAULT_SEED = 20261014  # the date contracts/INTERFACES.md was authored
# Mid-January: the middle of the winter the whole plan turns on capturing
# (../../08-roadmap.md §8 risk 12, "devices not in the field by October" / "a 6-week slip
# costs 12 months"), and the part of the year where this measurement is strongest, because
# the gap between mains water and indoor air is widest.
#
# Worth knowing before choosing another window: "in the field by October" is a *shipping*
# deadline, not a data deadline. October mains water is still near 16 °C, so an autumn run
# produces a deliberately hard dataset in which even a fast leak moves `delta_c` by about a
# degree. `--start 2026-10-01` is a good adversarial case and a bad default.
DEFAULT_START = "2027-01-15T00:00:00Z"


def parse_start(text: str) -> int:
    """Accept `2026-11-01T00:00:00Z`, the same shape §4 uses for `received_at`."""
    cleaned = text.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    dt = datetime.datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp())


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python3 -m simulator",
        description="Generate a labelled synthetic fleet of leak-sensor uplinks "
        "(contracts/INTERFACES.md §4 envelope + §6 labels).",
    )
    p.add_argument("--devices", type=int, default=40, help="fleet size (default 40)")
    p.add_argument("--days", type=int, default=30, help="days to simulate (default 30)")
    p.add_argument("--out", default=None, help="write JSONL here; '-' for stdout")
    p.add_argument("--post", default=None, help="POST each envelope to this URL instead")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED, help="makes a run reproducible")
    p.add_argument("--start", default=DEFAULT_START, help="UTC start instant (default %s)" % DEFAULT_START)
    p.add_argument(
        "--heartbeat-min",
        type=int,
        default=15,
        help="scheduled uplink interval in minutes (default 15; the firmware escalates to 5 "
        "while a cold warning or a deep delta is latched)",
    )
    p.add_argument(
        "--event-rate",
        type=float,
        default=1.0,
        help="scales how many devices develop a leak or fault. 1.0 (default) puts about a "
        "third of the fleet in some abnormal state, which is far above real incidence but is "
        "what gives §7's recall and time-to-detection enough positives per class; 0 drops to "
        "the minimum that still covers all ten §6 scenarios. Precision is the one §7 metric "
        "this moves, so precision from a default run is optimistic",
    )
    p.add_argument(
        "--dup-rate",
        type=float,
        default=0.012,
        help="fraction of envelopes delivered twice, to exercise the §4 idempotency "
        "requirement on (dev_eui, f_cnt) (default 0.012)",
    )
    p.add_argument("--no-labels", action="store_true", help="omit the §6 _label block")
    p.add_argument("--limit", type=int, default=0, help="stop after N envelopes (0 = no limit)")
    p.add_argument("--rate", type=float, default=0.0, help="--post: max requests per second (0 = unpaced)")
    p.add_argument("--timeout", type=float, default=10.0, help="--post: per-request timeout in seconds")
    p.add_argument("--quiet", action="store_true", help="suppress the summary on stderr")
    return p


# ------------------------------------------------------------------------------ streaming


def stream_envelopes(
    spec: scenarios.FleetSpec, include_label: bool = True, dup_rate: float = 0.0
) -> Iterator[Dict[str, object]]:
    """Merge every device's frames into one chronological stream, the way a webhook sees them.

    `heapq.merge` over per-device generators rather than building a list: a 40-device 30-day
    run is well over a hundred thousand frames, and holding them all to sort them would be
    the one place this program needed real memory. Bounded memory is also the house style --
    §5 requirement 6 demands it of the detector, and it costs nothing here.
    """
    drng = random.Random(fleet.derive_seed("dupes", spec.seed))

    def keyed(plan: fleet.DevicePlan) -> Iterator[Tuple[int, str, fleet.DevicePlan, fleet.Frame]]:
        for frame in fleet.simulate_device(plan, spec.start_s, spec.days):
            # device_id breaks ties deterministically; two devices on a shared gateway land on
            # the same second often enough that an unstable order would break --seed.
            yield (frame.ts_s, plan.device.device_id, plan, frame)

    for _ts, _dev_id, plan, frame in heapq.merge(
        *[keyed(p) for p in spec.plans], key=lambda item: (item[0], item[1])
    ):
        env = fleet.envelope(plan.device, frame, include_label=include_label)
        yield env
        if dup_rate > 0.0 and drng.random() < dup_rate:
            # A real LNS re-delivers on webhook timeout, and two gateways hearing one frame
            # can surface as two messages. §4 requires (dev_eui, f_cnt) to be idempotent, so
            # the stream has to actually contain duplicates or that requirement is untested.
            yield env


# ---------------------------------------------------------------------------- output modes


def write_jsonl(envelopes: Iterable[Dict[str, object]], path: str, limit: int = 0) -> int:
    n = 0
    handle = sys.stdout if path == "-" else open(path, "w", encoding="utf-8")
    try:
        for env in envelopes:
            handle.write(json.dumps(env, separators=(",", ":"), sort_keys=False))
            handle.write("\n")
            n += 1
            if limit and n >= limit:
                break
    finally:
        if handle is not sys.stdout:
            handle.close()
    return n


def post_stream(
    url: str,
    envelopes: Iterable[Dict[str, object]],
    urlopen: Optional[Callable] = None,
    timeout: float = 10.0,
    rate: float = 0.0,
    limit: int = 0,
    sleep: Callable[[float], None] = time.sleep,
) -> Dict[str, object]:
    """POST each envelope to a TTS-webhook-compatible endpoint, with stdlib only.

    `urlopen` is injectable so the tests can exercise this path without a socket --
    INTERFACES.md §10 requires the suite to pass with no network access, and a test that
    binds a loopback port is a test that fails in somebody's sandbox.
    """
    opener = urlopen or urllib.request.urlopen
    status_counts: "collections.Counter[str]" = collections.Counter()
    sent = 0
    min_interval = 1.0 / rate if rate > 0 else 0.0
    last = 0.0
    for env in envelopes:
        if min_interval:
            wait = min_interval - (time.monotonic() - last)
            if wait > 0:
                sleep(wait)
            last = time.monotonic()
        body = json.dumps(env, separators=(",", ":")).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "leakguard-simulator/1.0"},
        )
        try:
            with opener(req, timeout=timeout) as resp:
                status_counts[str(getattr(resp, "status", getattr(resp, "code", 0)))] += 1
        except urllib.error.HTTPError as exc:
            # A 4xx/5xx is data, not a crash: the backend rejecting a frame is exactly the
            # kind of thing this mode exists to find.
            status_counts[str(exc.code)] += 1
        except urllib.error.URLError as exc:
            status_counts["unreachable"] += 1
            if status_counts["unreachable"] == 1:
                sys.stderr.write("simulator: POST failed: %s\n" % (exc.reason,))
        except OSError as exc:
            status_counts["error"] += 1
            if status_counts["error"] == 1:
                sys.stderr.write("simulator: POST error: %s\n" % (exc,))
        sent += 1
        if limit and sent >= limit:
            break
    return {"sent": sent, "status_counts": dict(status_counts)}


# -------------------------------------------------------------------------------- summary


def summarise(spec: scenarios.FleetSpec, envelopes: Iterable[Dict[str, object]]) -> Dict[str, object]:
    """Fleet-level facts worth printing, including the §7 inputs the dataset has to support."""
    by_scenario: "collections.Counter[str]" = collections.Counter()
    by_reason: "collections.Counter[str]" = collections.Counter()
    last_seen: Dict[str, int] = {}
    max_f_cnt: Dict[str, int] = {}
    seen_f_cnt: Dict[str, int] = {}
    battery_min: Dict[str, int] = {}
    total = 0
    day7_s = spec.start_s + 7 * fleet.SECONDS_PER_DAY

    for env in envelopes:
        total += 1
        ids = env["end_device_ids"]
        msg = env["uplink_message"]
        dev_id = ids["device_id"]
        raw = base64.b64decode(msg["frm_payload"])
        decoded = fleet.decode_uplink(raw)
        by_reason[decoded["uplink_reason"]] += 1
        label = env.get("_label")
        if label:
            by_scenario[label["scenario"]] += 1
        ts = int(datetime.datetime.strptime(env["received_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc
        ).timestamp())
        last_seen[dev_id] = max(last_seen.get(dev_id, 0), ts)
        max_f_cnt[dev_id] = max(max_f_cnt.get(dev_id, 0), int(msg["f_cnt"]))
        seen_f_cnt[dev_id] = seen_f_cnt.get(dev_id, 0) + 1
        mv = int(decoded["battery_mv"])
        battery_min[dev_id] = min(battery_min.get(dev_id, mv), mv)

    transmitted = sum(v + 1 for v in max_f_cnt.values())
    # §7's `pct_never_reporting` is "devices with zero uplinks after day 7", which is only a
    # statement about the install failure tail if the run is longer than seven days. On a
    # shorter window every device trivially qualifies, so report nothing rather than a number
    # that looks like a 100% failure rate.
    measurable = spec.days > 7
    never_after_day7 = [d for d, ts in last_seen.items() if ts < day7_s] if measurable else []
    return {
        "devices": len(spec.plans),
        "devices_with_uplinks": len(last_seen),
        "envelopes": total,
        "frames_transmitted_est": transmitted,
        "packet_loss_pct": round(100.0 * (1.0 - min(1.0, sum(seen_f_cnt.values()) / max(1, transmitted))), 2),
        "pct_never_reporting_after_day7": (
            round(100.0 * len(never_after_day7) / max(1, len(spec.plans)), 1) if measurable else None
        ),
        "never_reporting_devices": sorted(never_after_day7),
        "by_scenario": dict(by_scenario),
        "by_uplink_reason": dict(by_reason),
        "min_battery_mv": min(battery_min.values()) if battery_min else None,
        "missing_scenarios": sorted(set(fleet.SCENARIOS) - set(by_scenario)),
    }


def _print_summary(summary: Dict[str, object], stream=sys.stderr, truncated: bool = False) -> None:
    stream.write("simulator: %d envelopes from %d devices\n" % (summary["envelopes"], summary["devices"]))
    pct = summary["pct_never_reporting_after_day7"]
    stream.write(
        "simulator: packet loss %.2f%%, never-reporting after day 7 %s\n"
        % (
            summary["packet_loss_pct"],
            "n/a (run is 7 days or shorter)"
            if pct is None
            else "%.1f%% (%s)" % (pct, ", ".join(summary["never_reporting_devices"]) or "none"),
        )
    )
    stream.write("simulator: lowest battery %s mV\n" % summary["min_battery_mv"])
    stream.write("simulator: uplink reasons %s\n" % summary["by_uplink_reason"])
    width = max((len(s) for s in fleet.SCENARIOS), default=10)
    for name in fleet.SCENARIOS:
        n = summary["by_scenario"].get(name, 0)
        pct = 100.0 * n / max(1, summary["envelopes"])
        stream.write("simulator:   %-*s %7d  %5.2f%%\n" % (width, name, n, pct))
    if summary["missing_scenarios"]:
        stream.write(
            "simulator: WARNING missing scenarios: %s%s\n"
            % (
                ", ".join(summary["missing_scenarios"]),
                " (the stream was cut short by --limit, so this says nothing about the fleet)"
                if truncated
                else "",
            )
        )


# ----------------------------------------------------------------------------------- main


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.devices < 1:
        sys.stderr.write("simulator: --devices must be at least 1\n")
        return 2
    if args.days < 1:
        sys.stderr.write("simulator: --days must be at least 1\n")
        return 2
    if args.heartbeat_min < 1 or args.heartbeat_min > 240:
        # Matches the §2 set_heartbeat bound, because that is the range the device accepts.
        sys.stderr.write("simulator: --heartbeat-min must be 1..240 (contract §2)\n")
        return 2
    if args.post and args.out:
        sys.stderr.write("simulator: choose one of --out or --post\n")
        return 2

    try:
        start_s = parse_start(args.start)
    except ValueError as exc:
        sys.stderr.write("simulator: bad --start %r: %s\n" % (args.start, exc))
        return 2

    spec = scenarios.build_fleet(
        n_devices=args.devices,
        start_s=start_s,
        days=args.days,
        seed=args.seed,
        heartbeat_s=args.heartbeat_min * 60,
        event_rate=max(0.0, args.event_rate),
    )
    if not args.quiet:
        scenarios.warn_notes(spec)

    include_label = not args.no_labels

    if args.post:
        # Summarise from the same stream that is posted, so the numbers on stderr describe
        # what was actually sent rather than a second, differently-seeded run.
        collected: List[Dict[str, object]] = []

        def tee() -> Iterator[Dict[str, object]]:
            for env in stream_envelopes(spec, include_label, args.dup_rate):
                collected.append(env)
                yield env

        result = post_stream(
            args.post, tee(), timeout=args.timeout, rate=args.rate, limit=args.limit
        )
        if not args.quiet:
            _print_summary(summarise(spec, collected), truncated=bool(args.limit))
            sys.stderr.write("simulator: POST %s -> %s\n" % (args.post, result["status_counts"]))
        bad = sum(n for code, n in result["status_counts"].items() if not code.startswith("2"))
        return 1 if bad else 0

    out = args.out or "-"
    # Two passes over the generator: one to write, one to summarise. The second pass is only
    # run when a summary is wanted, and it regenerates rather than buffering, because the
    # whole point of the heapq.merge streaming above is not to hold the run in memory.
    n = write_jsonl(stream_envelopes(spec, include_label, args.dup_rate), out, limit=args.limit)
    if not args.quiet:
        if include_label:
            again: Iterable[Dict[str, object]] = stream_envelopes(spec, include_label, args.dup_rate)
            if args.limit:
                again = itertools.islice(again, args.limit)
            _print_summary(summarise(spec, again), truncated=bool(args.limit))
        else:
            sys.stderr.write("simulator: %d envelopes written (labels omitted)\n" % n)
        if out != "-":
            sys.stderr.write("simulator: wrote %s\n" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
