"""Shared fixtures. Every test gets a fresh SQLite file and a clean app state.

The HTTP tests drive the real FastAPI app over httpx: `fastapi.testclient.
TestClient` is an `httpx.Client` on an ASGI transport, so requests go through
the actual routing, validation and response serialisation -- no function is
called directly behind the app's back. No network is used (contract §10).
"""

import pathlib
import sys

import pytest

# prototype/ on sys.path, so `backend.app...` imports work however pytest is
# invoked (from the repo root or from backend/).
_PROTOTYPE_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_PROTOTYPE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROTOTYPE_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import alerts as alerts_mod  # noqa: E402
from backend.app import codec, db, ingest  # noqa: E402
from backend.app.main import app  # noqa: E402

DEV_EUI = "70B3D57ED0000001"
DEVICE_ID = "leak-0001"


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Point the app at a throwaway database and clear in-process state."""
    for name in db.DB_PATH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    db.set_db_path(tmp_path / "leakwatch.db")
    db.init_db()
    ingest.reset_detector_states()
    alerts_mod.clear_notifications()
    yield
    ingest.reset_detector_states()
    alerts_mod.clear_notifications()


@pytest.fixture()
def client(fresh_db):
    """The app under test, with its real lifespan (which applies the schema)."""
    with TestClient(app) as test_client:
        yield test_client


def envelope(
    f_cnt,
    pipe_c=18.0,
    ambient_c=19.0,
    battery_mv=3600,
    install_verified=True,
    tamper=False,
    cold_warning=False,
    uplink_reason=0,
    dev_eui=DEV_EUI,
    device_id=DEVICE_ID,
    received_at="2026-10-14T03:00:00Z",
    f_port=1,
    **extra
):
    """A contract §4 TTS v3 envelope with a real base64 `frm_payload`."""
    body = {
        "end_device_ids": {"device_id": device_id, "dev_eui": dev_eui},
        "received_at": received_at,
        "uplink_message": {
            "f_port": f_port,
            "f_cnt": f_cnt,
            "frm_payload": codec.encode_frm_payload(
                pipe_c, ambient_c, battery_mv, install_verified, tamper,
                cold_warning, uplink_reason),
            "rx_metadata": [{"gateway_ids": {"gateway_id": "gw-a"}, "rssi": -112, "snr": 2.5}],
            "settings": {"data_rate": {"lora": {"spreading_factor": 10, "bandwidth": 125000}}},
        },
    }
    body.update(extra)
    return body


def iso(ts):
    return db.iso(ts)
