#!/usr/bin/env python3
"""Seed the restart-safe v0.3 census checkpoints with the v0.2 results.

The v0.3 frame adds the 2021-2022 JOSS articles. Records already screened,
inventoried, inspected and resolved for v0.2 are carried over unchanged, except
that their processing rank is re-derived from the hash order of the extended
frame (the rank only orders processing; it is not an eligibility input). The
restart-safe v0.2 scripts then process only the new records.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader), list(reader.fieldnames or [])


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v02-frame", type=Path, required=True, help="v0.2 census_frame directory with checkpoints")
    parser.add_argument("--v02-census", type=Path, required=True, help="v0.2 census directory (endpoint ledger)")
    parser.add_argument("--v03-frame", type=Path, required=True)
    parser.add_argument("--v03-census", type=Path, required=True)
    parser.add_argument("--crossref", type=Path, required=True, help="combined v0.3 Crossref frame")
    args = parser.parse_args()

    screening, _ = read_csv(args.v03_frame / "screening_ledger.csv")
    rank = {row["doi"]: row["rank"] for row in screening}
    candidates_path = args.v03_frame / "provisional_eligible.csv"
    report: dict[str, int] = {}

    # Publication-archive inventory checkpoint.
    state = json.loads((args.v02_frame / "archive_inventory_checkpoint.json").read_text(encoding="utf-8"))
    for doi, result in state["results"].items():
        result["linkage"]["rank"] = rank[doi]
        for row in result["files"]:
            row["rank"] = rank[doi]
    state["candidate_sha256"] = sha256_file(candidates_path)
    state["crossref_sha256"] = sha256_file(args.crossref)
    state["seeded_from_v0_2"] = len(state["results"])
    args.v03_frame.mkdir(parents=True, exist_ok=True)
    (args.v03_frame / "archive_inventory_checkpoint.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    report["inventory_results_seeded"] = len(state["results"])

    # Static inspection ledger.
    rows, fields = read_csv(args.v02_frame / "archive_static_inspection.csv")
    for row in rows:
        row["rank"] = rank[row["scholarly_doi"]]
    write_csv(args.v03_frame / "archive_static_inspection.csv", rows, fields)
    report["inspection_rows_seeded"] = len(rows)

    # Endpoint ledger: live v0.2 rows keep their values and are marked as live.
    rows, fields = read_csv(args.v02_census / "endpoint_resolution.csv")
    for extra in ("resolution_mode", "endpoint_as_of_utc"):
        if extra not in fields:
            fields.insert(fields.index("third_party_code_executed"), extra)
    for row in rows:
        row["selection_rank"] = rank[row["scholarly_doi"]]
        row["resolution_mode"] = row.get("resolution_mode") or "live"
        row["endpoint_as_of_utc"] = row.get("endpoint_as_of_utc") or ""
    write_csv(args.v03_census / "endpoint_resolution.csv", rows, fields)
    report["endpoint_rows_seeded"] = len(rows)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
