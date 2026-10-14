"""LeakWatch backend: ingest, detection glue, alerting, valve safety, reporting.

The REST surface is contracts/INTERFACES.md §8 and the storage is §9. Module map:

    codec.py            8-byte payload decode/encode (contract §1, §2)
    db.py               sqlite3 connections, idempotent schema application
    schema.sql          the five tables of contract §9
    ingest.py           TTS v3 webhook: decode, upsert, idempotency, detector run
    detector_bridge.py  loads ../../detection/detector.py (contract §5), or falls back
    alerts.py           alert lifecycle, shadow mode, nuisance feedback
    valve.py            arming check, command outcomes, fail-as-is
    insurer.py          portfolio / loss-avoided report, surrogates only
    main.py             FastAPI routes

What this package deliberately does NOT do: no auth, no multi-tenancy, no
outbound notification transport, and no claim-frequency arithmetic. The first
three are prototype scope; the fourth is a refusal (08-roadmap.md §8 risk 3).
"""

__all__ = ["main"]
