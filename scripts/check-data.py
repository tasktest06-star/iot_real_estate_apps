#!/usr/bin/env python3
"""Assert the pack's CSVs parse and stay consistent with the Markdown.

Run with `make check-data`, or `python3 -I scripts/check-data.py`.

This exists because the CSVs are the machine-readable half of claims that are
also written out in prose. When someone edits a table in the Markdown and
forgets the CSV (or the reverse), this is what catches it.
"""

import csv
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROBLEMS: list[str] = []


def fail(msg: str) -> None:
    PROBLEMS.append(msg)


def load(name: str) -> list[dict[str, str]]:
    path = ROOT / "data" / name
    if not path.exists():
        fail(f"{name}: missing")
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        fail(f"{name}: parsed but empty")
    # A stray comma inside an unquoted field shows up as a None key.
    for i, row in enumerate(rows, start=2):
        if None in row or any(v is None for v in row.values()):
            fail(f"{name}: row {i} has the wrong number of fields "
                 f"(an unquoted comma inside a field?): {row.get('repo') or row.get('id') or row}")
    return rows


# ---------------------------------------------------------------- repos.csv
repos = load("repos.csv")
if repos:
    allowed_commercial = {"yes", "care", "no", "review"}
    seen: set[str] = set()
    for row in repos:
        repo = row["repo"]
        if repo in seen:
            fail(f"repos.csv: {repo} listed twice")
        seen.add(repo)
        if row["commercial_ok"] not in allowed_commercial:
            fail(f"repos.csv: {repo} has commercial_ok={row['commercial_ok']!r}, "
                 f"expected one of {sorted(allowed_commercial)}")
        if not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", repo):
            fail(f"repos.csv: {repo!r} is not an owner/name pair")
        if row["verified"] != "2026-10-14":
            fail(f"repos.csv: {repo} verified={row['verified']} — re-run "
                 f"scripts/verify-licenses.sh and update the date")

    # The headline claims the pack makes in prose. If any of these drift, the
    # recommendation in README.md and 05-open-source-stack.md is wrong.
    must_hold = {
        "thingsboard/thingsboard": ("BUSL-1.1", "no"),
        "emqx/emqx": ("BUSL-1.1", "no"),
        "grafana/grafana": ("AGPL-3.0", "no"),
        "openremote/openremote": ("AGPL-3.0", "no"),
        "esphome/esphome": ("Split-GPLv3-MIT", "no"),
        "chirpstack/chirpstack": ("MIT", "yes"),
        "openthread/openthread": ("BSD-3-Clause", "yes"),
        "Lora-net/SWL2001": ("BSD-3-Clear", "care"),
        "TheThingsNetwork/lorawan-stack": ("Apache-2.0", "yes"),
        "project-chip/connectedhomeip": ("Apache-2.0", "care"),
        "absmach/magistrala": ("Apache-2.0", "yes"),
        "vernemq/vernemq": ("Apache-2.0", "yes"),
    }
    by_repo = {r["repo"]: r for r in repos}
    for repo, (lic, commercial) in must_hold.items():
        row = by_repo.get(repo)
        if row is None:
            fail(f"repos.csv: {repo} is cited in the Markdown but missing from the CSV")
        elif (row["license"], row["commercial_ok"]) != (lic, commercial):
            fail(f"repos.csv: {repo} is {row['license']}/{row['commercial_ok']}, "
                 f"but the prose asserts {lic}/{commercial}")

    apache = sum(1 for r in repos if r["license"] == "Apache-2.0")
    print(f"repos.csv           {len(repos):>3} rows, {apache} Apache-2.0, "
          f"{sum(1 for r in repos if r['commercial_ok'] == 'no')} disqualified")

# --------------------------------------------------- application-scores.csv
apps = load("application-scores.csv")
if apps:
    allowed_verdicts = {"START", "SPIKE", "LATER", "AVOID-YEAR-1"}
    for row in apps:
        if row["verdict"] not in allowed_verdicts:
            fail(f"application-scores.csv: {row['id']} verdict={row['verdict']!r}")
        for col in ("cert_burden_1to5", "tech_risk_1to5", "defensibility_1to5"):
            try:
                val = int(row[col])
            except ValueError:
                fail(f"application-scores.csv: {row['id']} {col}={row[col]!r} is not an int")
                continue
            if not 0 <= val <= 5:
                fail(f"application-scores.csv: {row['id']} {col}={val} out of range 0-5")
    # The portfolio argument depends on there being a real spread of verdicts.
    verdicts = {v: sum(1 for r in apps if r["verdict"] == v) for v in allowed_verdicts}
    if verdicts["START"] < 2:
        fail("application-scores.csv: fewer than two START applications — "
             "the portfolio recommendation has nothing to stand on")
    print(f"application-scores  {len(apps):>3} rows, " +
          ", ".join(f"{k}={v}" for k, v in sorted(verdicts.items())))

# ----------------------------------------------------------- cert-costs.csv
certs = load("cert-costs.csv")
if certs:
    for row in certs:
        try:
            low, high = float(row["low_usd"]), float(row["high_usd"])
        except ValueError:
            fail(f"cert-costs.csv: {row['product']}/{row['line_item']} has non-numeric bounds")
            continue
        if low > high:
            fail(f"cert-costs.csv: {row['line_item']}: low {low} > high {high}")
        if row["evidence"] not in {"vendor-published", "estimate", "verified", "unverified"}:
            fail(f"cert-costs.csv: {row['line_item']} evidence={row['evidence']!r}")
    print(f"cert-costs.csv      {len(certs):>3} rows, "
          f"{sum(1 for r in certs if r['evidence'] == 'estimate')} estimates, "
          f"{sum(1 for r in certs if r['evidence'] == 'vendor-published')} vendor-published")

# --------------------------------------------------------- bom-estimates.csv
boms = load("bom-estimates.csv")
if boms:
    for row in boms:
        try:
            float(row["unit_usd"])
        except ValueError:
            fail(f"bom-estimates.csv: {row['item']} unit_usd={row['unit_usd']!r} is not numeric")
    print(f"bom-estimates.csv   {len(boms):>3} rows")

# ------------------------------------------- Markdown placeholder sweep
for md in sorted(ROOT.glob("*.md")):
    text = md.read_text(encoding="utf-8")
    for marker in ("TODO", "FIXME", "TBD", "XXX", "Lorem ipsum", "<placeholder"):
        if marker in text:
            fail(f"{md.name}: contains placeholder marker {marker!r}")

if PROBLEMS:
    print("\n".join(f"\nFAIL  {p}" for p in PROBLEMS))
    print(f"\n{len(PROBLEMS)} problem(s)")
    sys.exit(1)

print("\nAll data checks pass.")
