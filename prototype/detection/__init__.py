"""Leak detection and its evaluation harness.

`detector` is the algorithm (contracts/INTERFACES.md §5); `evaluate` is the
offline harness that scores it against the simulator's labelled JSONL and
decides the roadmap's P1 exit gate (contracts/INTERFACES.md §7,
08-roadmap.md §3 "P1 — Prove the alert").

This package deliberately depends on **nothing but the standard library**, in
either module: the detector has to be portable to a Cortex-M4 and the harness
has to be runnable by anyone auditing the gate number without installing a
scientific Python stack.
"""

__all__ = ["detector", "evaluate"]
