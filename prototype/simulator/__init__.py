"""Synthetic labelled device fleet for the property-IoT leak-detection prototype.

WHAT THIS IS
    A standard-library-only generator of contracts/INTERFACES.md §4 The Things Stack uplink
    envelopes with §6 ground-truth labels attached, so that `backend/`, `detection/` and
    `dashboard/` are runnable and measurable with no hardware, no gateway and no winter.
    `frm_payload` is real base64 of the real 8 bytes of §1, encoded by `fleet.encode_uplink`,
    which is a second independent implementation of that byte table checked against the Node
    codec's golden vectors.

    Entry points:
        python3 -m simulator --devices 40 --days 30 --out fleet.jsonl
        python3 -m simulator --devices 40 --days 30 --post http://127.0.0.1:8000/api/v1/uplink

WHAT THIS DELIBERATELY DOES NOT DO
    It is not validated against measured data and cannot predict the real nuisance rate; see
    `fleet.py`'s docstring and README.md. It exists to rank detector designs and to expose a
    detector that only works on toy data, which is a much lower bar than predicting the field
    and the only one reachable before the winter that ../../08-roadmap.md §7 says settles it.
"""

__all__ = ["fleet", "scenarios"]
