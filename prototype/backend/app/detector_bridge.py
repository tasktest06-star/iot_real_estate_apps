"""Loads the real detector from `../../detection/detector.py` (contract §5).

The algorithm belongs to `detection/`, which is built in parallel by another
component and is stdlib-only because it has to fit on a Cortex-M4
(08-roadmap.md §4: no NN on the node before P2). The backend must not have a
second copy of it, so this module is a *bridge*: it imports the contract §5
surface (`Sample`, `Decision`, `VERDICTS`, `new_state`, `update`) by file path,
smoke-tests it once, and adapts its output to a plain dict.

If -- and only if -- that import or smoke test fails, the clearly-marked
`_FallbackDetector` below is used so the backend and its tests still run
standalone. The fallback is a stand-in, not a second implementation to be
maintained: it is the crudest thing that satisfies the six required behaviours
of contract §5, and `GET /api/v1/healthz` reports which one is live so a passing
test suite can never be mistaken for a passing *detector*.

What this module deliberately does NOT do:
  * no persistence of detector state in SQLite -- state is rebuilt by replaying
    recent uplinks (see `app/ingest.py`), because contract §5 states are opaque;
  * no tuning of the real detector's thresholds from here.
"""

import importlib.util
import pathlib
import sys
import threading
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

_PROTOTYPE_DIR = pathlib.Path(__file__).resolve().parents[2]
_DETECTOR_PATH = _PROTOTYPE_DIR / "detection" / "detector.py"

VERDICTS = (
    "quiet",
    "watch",
    "leak_suspected",
    "freeze_risk",
    "sensor_fault",
    "install_unverified",
)

# Verdicts that are a peril for the property, i.e. the ones an operator is meant
# to act on. `sensor_fault` and `install_unverified` are data-quality problems:
# 08-roadmap.md §8 risk 4 says do not count unverified installs, so they are
# reported as fleet health in app/insurer.py instead of raising an alert.
ALERTING_VERDICTS = ("leak_suspected", "freeze_risk")


# --------------------------------------------------------------------------- #
# FALLBACK DETECTOR -- USED ONLY WHEN detection/detector.py CANNOT BE IMPORTED
# --------------------------------------------------------------------------- #
# Crude on purpose. Thresholds are placeholders for the real learned behaviour;
# nothing here should be cited as the prototype's detection performance.
_FB_WINDOW = 32              # samples, fixed size (contract §5.6 bounded memory)
_FB_DWELL_S = 1800           # a leak must persist 30 min -- a shower must not
_FB_DWELL_SAMPLES = 3
_FB_LEAK_DROP_C = 2.0        # delta_c this far below the device's own baseline
_FB_FREEZE_C = 3.0           # pipe_c at or below this => freeze_risk (A4)
_FB_PLAUSIBLE_C = (-40.0, 80.0)
_FB_MAX_PIPE_RATE_C_PER_S = 0.5   # faster than any pipe's thermal mass allows
_FB_BASELINE_ALPHA = 0.1     # per-device adaptation (contract §5.5)


class _FallbackState(object):
    """Fixed-size state: a bounded deque plus a handful of scalars."""

    __slots__ = ("device_id", "window", "baseline_c", "last_ts", "last_pipe_c",
                 "excursion_start_ts", "excursion_samples")

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.window = deque(maxlen=_FB_WINDOW)  # type: deque
        self.baseline_c = None   # type: Optional[float]
        self.last_ts = None      # type: Optional[int]
        self.last_pipe_c = None  # type: Optional[float]
        self.excursion_start_ts = None  # type: Optional[int]
        self.excursion_samples = 0


class _FallbackDetector(object):
    source = "backend-fallback"

    @staticmethod
    def new_state(device_id: str) -> _FallbackState:
        return _FallbackState(device_id)

    @staticmethod
    def update(state: _FallbackState, sample: Dict[str, Any]) -> Dict[str, Any]:
        ts = int(sample["ts_s"])
        pipe_c = float(sample["pipe_c"])
        ambient_c = float(sample["ambient_c"])
        delta_c = round(pipe_c - ambient_c, 4)
        prev_ts, prev_pipe = state.last_ts, state.last_pipe_c
        state.window.append((ts, delta_c))
        state.last_ts, state.last_pipe_c = ts, pipe_c
        if state.baseline_c is None:
            state.baseline_c = delta_c
        baseline = state.baseline_c

        def out(verdict: str, confidence: int, reason: str) -> Dict[str, Any]:
            return {"verdict": verdict, "confidence": int(confidence), "reason": reason,
                    "delta_c": delta_c, "baseline_c": round(baseline, 3)}

        implausible = not (
            _FB_PLAUSIBLE_C[0] <= pipe_c <= _FB_PLAUSIBLE_C[1]
            and _FB_PLAUSIBLE_C[0] <= ambient_c <= _FB_PLAUSIBLE_C[1]
        )
        too_fast = False
        if prev_ts is not None and prev_pipe is not None and ts > prev_ts:
            too_fast = abs(pipe_c - prev_pipe) / float(ts - prev_ts) > _FB_MAX_PIPE_RATE_C_PER_S
        # Hardware integrity before placement: a tampered or implausible sensor
        # tells us nothing about the install (contract §5.3).
        if sample.get("tamper") or implausible or too_fast:
            state.excursion_start_ts, state.excursion_samples = None, 0
            why = "tamper flag" if sample.get("tamper") else (
                "reading outside -40..80 C" if implausible else "step change faster than thermal mass")
            return out("sensor_fault", 90, "sensor fault: %s" % why)
        # contract §5.2: an unverified install can never produce a leak verdict.
        if not sample.get("install_verified"):
            state.excursion_start_ts, state.excursion_samples = None, 0
            return out("install_unverified", 80, "install not verified; not counted")
        if pipe_c <= _FB_FREEZE_C:
            return out("freeze_risk", 85, "pipe at %.1f C, freeze threshold %.1f C"
                       % (pipe_c, _FB_FREEZE_C))

        anomalous = delta_c <= baseline - _FB_LEAK_DROP_C and delta_c < 0
        if not anomalous:
            state.excursion_start_ts, state.excursion_samples = None, 0
            state.baseline_c = (1 - _FB_BASELINE_ALPHA) * baseline + _FB_BASELINE_ALPHA * delta_c
            return out("quiet", 60, "delta %.2f C near baseline %.2f C" % (delta_c, baseline))
        if state.excursion_start_ts is None:
            state.excursion_start_ts = ts
            state.excursion_samples = 0
        state.excursion_samples += 1
        held_s = ts - state.excursion_start_ts
        if held_s >= _FB_DWELL_S and state.excursion_samples >= _FB_DWELL_SAMPLES:
            conf = min(95, 60 + int(held_s / 600) * 5)
            return out("leak_suspected", conf,
                       "delta %.2f C held %.0f s below baseline %.2f C" % (delta_c, held_s, baseline))
        return out("watch", 40, "delta %.2f C below baseline for %.0f s (dwell %d s)"
                   % (delta_c, held_s, _FB_DWELL_S))


# --------------------------------------------------------------------------- #
# Loader
# --------------------------------------------------------------------------- #
_load_lock = threading.Lock()
_impl = None            # type: Any
_impl_source = None     # type: Optional[str]
_impl_note = None       # type: Optional[str]


def _smoke_test(module: Any) -> None:
    """Run one sample through a candidate detector; raise if it is not usable."""
    for name in ("Sample", "Decision", "VERDICTS", "new_state", "update"):
        if not hasattr(module, name):
            raise AttributeError("detector is missing %s (contract §5)" % name)

    missing = [v for v in ALERTING_VERDICTS if v not in tuple(module.VERDICTS)]
    if missing:
        raise ValueError("detector VERDICTS lacks %s (contract §5)" % ", ".join(missing))
    state = module.new_state("smoke-test")
    sample = module.Sample(ts_s=1760000000, pipe_c=18.0, ambient_c=19.0,
                           battery_mv=3600, install_verified=True, tamper=False)
    decision = module.update(state, sample)
    verdict = getattr(decision, "verdict")
    if verdict not in tuple(module.VERDICTS):
        raise ValueError("detector returned verdict %r not in VERDICTS" % (verdict,))
    for field in ("confidence", "reason", "delta_c", "baseline_c"):
        getattr(decision, field)


def _load() -> Tuple[Any, str, Optional[str]]:
    """Import detection/detector.py, or fall back. Called once per process."""
    if not _DETECTOR_PATH.exists():
        return (_FallbackDetector, _FallbackDetector.source,
                "%s does not exist yet; using the backend's marked fallback detector"
                % _DETECTOR_PATH)
    try:
        spec = importlib.util.spec_from_file_location("leakwatch_detection_detector",
                                                      str(_DETECTOR_PATH))
        if spec is None or spec.loader is None:
            raise ImportError("cannot build an import spec for %s" % _DETECTOR_PATH)
        module = importlib.util.module_from_spec(spec)
        # Registered before exec so a dataclass defined in it pickles/reprs sanely.
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _smoke_test(module)
    except Exception as exc:  # a half-written detector must not take the app down
        sys.modules.pop("leakwatch_detection_detector", None)
        return (_FallbackDetector, _FallbackDetector.source,
                "import of %s failed (%s: %s); using the backend's marked fallback detector"
                % (_DETECTOR_PATH, type(exc).__name__, exc))
    return module, "detection/detector.py", None


def impl() -> Any:
    global _impl, _impl_source, _impl_note
    with _load_lock:
        if _impl is None:
            _impl, _impl_source, _impl_note = _load()
        return _impl


def source() -> str:
    impl()
    return _impl_source or "unknown"


def note() -> Optional[str]:
    impl()
    return _impl_note


def using_fallback() -> bool:
    return source() == _FallbackDetector.source


def verdicts() -> List[str]:
    return list(getattr(impl(), "VERDICTS", VERDICTS))


def new_state(device_id: str) -> Any:
    return impl().new_state(device_id)


def classify(
    state: Any,
    ts_s: int,
    pipe_c: float,
    ambient_c: float,
    battery_mv: int,
    install_verified: bool,
    tamper: bool,
) -> Dict[str, Any]:
    """Run one sample through the active detector; return a plain dict.

    Exceptions are the caller's to handle: `app/ingest.py` records a detector
    error on the uplink rather than losing the frame or returning a 500, because
    a webhook that 500s gets retried by the LNS and then retried again.
    """
    module = impl()
    kwargs = dict(ts_s=int(ts_s), pipe_c=float(pipe_c), ambient_c=float(ambient_c),
                  battery_mv=int(battery_mv), install_verified=bool(install_verified),
                  tamper=bool(tamper))
    if module is _FallbackDetector:
        decision = module.update(state, kwargs)  # dict in, dict out
        return dict(decision)
    decision = module.update(state, module.Sample(**kwargs))
    return {
        "verdict": getattr(decision, "verdict"),
        "confidence": int(getattr(decision, "confidence", 0) or 0),
        "reason": getattr(decision, "reason", "") or "",
        "delta_c": getattr(decision, "delta_c", None),
        "baseline_c": getattr(decision, "baseline_c", None),
    }
