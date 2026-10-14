"""The detector bridge: contract §5 invariants, the loader, and the fallback.

Three groups, deliberately separated:

* invariants contract §5 states outright, asserted against whichever detector is
  live -- if `detection/detector.py` is importable and breaks one of these, the
  backend suite should say so;
* the loader itself: a missing or broken detector file must fall back rather
  than take the webhook down;
* the fallback's own thresholds, asserted with the fallback forced on, so these
  tests mean the same thing whether or not `detection/detector.py` exists. The
  *real* detector's thresholds are tested in `detection/test_detector.py`
  (contract §5 "each needs a test"); duplicating them here would mean two
  components owning one number.
"""

import pytest

from backend.app import detector_bridge


def _classify(state, ts, pipe_c, ambient_c, install_verified=True, tamper=False,
              battery_mv=3600):
    return detector_bridge.classify(state, ts, pipe_c, ambient_c, battery_mv,
                                    install_verified, tamper)


def test_bridge_reports_its_source():
    source = detector_bridge.source()
    assert source in ("detection/detector.py", "backend-fallback")
    if source == "backend-fallback":
        # the note must say why, so a green suite is never mistaken for a
        # working detector
        assert detector_bridge.note()
        assert "fallback" in detector_bridge.note()
    else:
        assert detector_bridge.note() is None


def test_verdict_vocabulary_matches_the_contract():
    assert set(detector_bridge.ALERTING_VERDICTS) <= set(detector_bridge.verdicts())
    for v in detector_bridge.ALERTING_VERDICTS:
        assert v in ("leak_suspected", "freeze_risk")


def test_every_decision_is_well_formed():
    state = detector_bridge.new_state("leak-0001")
    for i in range(10):
        d = _classify(state, 1760000000 + i * 900, 18.0, 19.0)
        assert d["verdict"] in detector_bridge.verdicts()
        assert 0 <= d["confidence"] <= 100
        assert isinstance(d["reason"], str) and d["reason"]
        assert d["delta_c"] is not None


def test_unverified_install_never_yields_a_leak_verdict():
    """Contract §5.2, verbatim: and never a leak verdict.

    08-roadmap.md §8 risk 4: an unverified install is indistinguishable from a
    sensor fault, and must not be counted.
    """
    state = detector_bridge.new_state("leak-unverified")
    verdicts = []
    for i in range(20):
        verdicts.append(_classify(state, 1760000000 + i * 900, 5.0, 19.0,
                                  install_verified=False)["verdict"])
    assert "leak_suspected" not in verdicts
    assert verdicts[-1] == "install_unverified"


def test_tamper_is_a_sensor_fault_not_a_leak():
    """Contract §5.3."""
    state = detector_bridge.new_state("leak-tampered")
    d = _classify(state, 1760000000, 5.0, 19.0, tamper=True)
    assert d["verdict"] == "sensor_fault"


def test_implausible_readings_are_a_sensor_fault():
    """Contract §5.3: outside -40..80 C."""
    state = detector_bridge.new_state("leak-implausible")
    assert _classify(state, 1760000000, -80.0, 19.0)["verdict"] == "sensor_fault"
    assert _classify(state, 1760000900, 19.0, 200.0)["verdict"] == "sensor_fault"


# ------------------------------------------------------------- the loader ---
@pytest.fixture()
def reloadable():
    """Let a test repoint the detector path and restore it afterwards."""
    saved = (detector_bridge._impl, detector_bridge._impl_source,
             detector_bridge._impl_note, detector_bridge._DETECTOR_PATH)
    yield detector_bridge
    (detector_bridge._impl, detector_bridge._impl_source,
     detector_bridge._impl_note, detector_bridge._DETECTOR_PATH) = saved


def test_missing_detector_file_falls_back(reloadable, tmp_path):
    reloadable._DETECTOR_PATH = tmp_path / "detector.py"
    reloadable._impl = None
    assert reloadable.source() == "backend-fallback"
    assert "does not exist yet" in reloadable.note()
    assert reloadable.using_fallback() is True
    # and it still classifies
    state = reloadable.new_state("leak-x")
    assert _classify(state, 1760000000, 18.0, 19.0)["verdict"] in reloadable.verdicts()


def test_broken_detector_file_falls_back_instead_of_crashing(reloadable, tmp_path):
    """A half-written detector from a parallel component must not 500 the webhook."""
    broken = tmp_path / "detector.py"
    broken.write_text("def new_state(device_id):\n    return  # unfinished\n")
    reloadable._DETECTOR_PATH = broken
    reloadable._impl = None
    assert reloadable.source() == "backend-fallback"
    assert "import of" in reloadable.note() and "failed" in reloadable.note()

    syntax_error = tmp_path / "detector2.py"
    syntax_error.write_text("def update(state, s:\n")
    reloadable._DETECTOR_PATH = syntax_error
    reloadable._impl = None
    assert reloadable.source() == "backend-fallback"
    assert "SyntaxError" in reloadable.note()


def test_detector_without_the_contract_verdicts_falls_back(reloadable, tmp_path):
    wrong = tmp_path / "detector.py"
    wrong.write_text(
        "import dataclasses\n"
        "VERDICTS = ('fine', 'broken')\n"
        "@dataclasses.dataclass\n"
        "class Sample:\n"
        "    ts_s: int\n    pipe_c: float\n    ambient_c: float\n"
        "    battery_mv: int\n    install_verified: bool\n    tamper: bool\n"
        "@dataclasses.dataclass\n"
        "class Decision:\n"
        "    verdict: str\n    confidence: int\n    reason: str\n"
        "    delta_c: float\n    baseline_c: float\n"
        "def new_state(device_id):\n    return {}\n"
        "def update(state, s):\n"
        "    return Decision('fine', 1, 'x', 0.0, 0.0)\n")
    reloadable._DETECTOR_PATH = wrong
    reloadable._impl = None
    assert reloadable.source() == "backend-fallback"
    assert "leak_suspected" in reloadable.note()


# ----------------------------------------------------- fallback behaviour ----
@pytest.fixture()
def fallback():
    """Force the marked fallback detector on, whatever detection/ contains.

    These tests describe the fallback, so they must not silently stop running
    when the real detector lands -- a skipped test proves nothing.
    """
    saved = (detector_bridge._impl, detector_bridge._impl_source,
             detector_bridge._impl_note)
    detector_bridge._impl = detector_bridge._FallbackDetector
    detector_bridge._impl_source = detector_bridge._FallbackDetector.source
    detector_bridge._impl_note = "forced on by the backend test suite"
    yield detector_bridge
    (detector_bridge._impl, detector_bridge._impl_source,
     detector_bridge._impl_note) = saved


def test_fallback_flags_a_sustained_negative_delta(fallback):
    """Contract §5.1."""
    state = detector_bridge.new_state("leak-slow")
    base = 1760000000
    for i in range(4):
        assert _classify(state, base + i * 900, 18.0, 19.0)["verdict"] == "quiet"
    verdicts = [_classify(state, base + (4 + i) * 900, 10.0, 19.0)["verdict"]
                for i in range(6)]
    assert verdicts[0] == "watch"
    assert "leak_suspected" in verdicts


def test_fallback_does_not_flag_a_shower(fallback):
    """Contract §5.1: brief excursions from appliance use must not trigger.

    This is the nuisance problem and the whole reason for shadow mode.
    """
    state = detector_bridge.new_state("leak-shower")
    base = 1760000000
    for i in range(6):
        _classify(state, base + i * 900, 18.0, 19.0)
    verdicts = []
    for hour in range(5):           # five mornings
        t = base + (6 + hour * 96) * 900
        verdicts.append(_classify(state, t, 9.0, 19.0)["verdict"])          # shower
        verdicts.append(_classify(state, t + 900, 17.5, 19.0)["verdict"])   # over
        verdicts.append(_classify(state, t + 1800, 18.0, 19.0)["verdict"])
    assert "leak_suspected" not in verdicts


def test_fallback_raises_freeze_risk(fallback):
    """Contract §5.4, application A4 on the same sensor."""
    state = detector_bridge.new_state("leak-cold")
    assert _classify(state, 1760000000, 1.0, 4.0)["verdict"] == "freeze_risk"


def test_fallback_baseline_adapts_per_device(fallback):
    """Contract §5.5: a fixed global threshold is wrong; housing stock varies."""
    warm = detector_bridge.new_state("warm-house")
    cold = detector_bridge.new_state("cold-basement")
    base = 1760000000
    for i in range(20):
        warm_d = _classify(warm, base + i * 900, 21.0, 22.0)
        cold_d = _classify(cold, base + i * 900, 7.0, 15.0)
    assert warm_d["verdict"] == "quiet" and cold_d["verdict"] == "quiet"
    assert cold_d["baseline_c"] < warm_d["baseline_c"] - 5
    # the cold basement's own -8 C delta is normal *for it*
    assert cold_d["delta_c"] < -7


def test_fallback_state_is_bounded(fallback):
    """Contract §5.6: a fixed-size window, not a growing list."""
    state = detector_bridge.new_state("leak-long")
    for i in range(5000):
        _classify(state, 1760000000 + i * 900, 18.0, 19.0)
    assert len(state.window) <= 32
    assert state.window.maxlen == 32


def test_fallback_rejects_a_physically_impossible_step(fallback):
    """Contract §5.3: faster than thermal mass allows."""
    state = detector_bridge.new_state("leak-step")
    _classify(state, 1760000000, 18.0, 19.0)
    assert _classify(state, 1760000010, 70.0, 19.0)["verdict"] == "sensor_fault"
