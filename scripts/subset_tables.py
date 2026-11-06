#!/usr/bin/env python3
"""Copy the core tables restricted to the projects of another release.

Used to summarize v0.3 on exactly the v0.2.4 cohort, so that the release
comparison isolates the effect of the extraction corrections from the frame
extension. The manifest is copied unchanged; the subset is an analysis
product, not a release.
"""

from __future__ import annotations

import argparse
import csv
import shutil
from pathlib import Path

TABLES = (
    "project_records.csv", "snapshot_records.csv", "structure_records.csv", "dependency_records.csv",
    "declaration_source_records.csv", "api_symbol_records.csv", "pair_delta_records.csv", "validation_records.csv",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tables", type=Path, required=True)
    parser.add_argument("--projects-from", type=Path, required=True, help="project_records.csv defining the subset")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.projects_from.open(encoding="utf-8-sig", newline="") as handle:
        keep = {row["project_id"] for row in csv.DictReader(handle)}
    args.output.mkdir(parents=True, exist_ok=True)
    for name in TABLES:
        with (args.tables / name).open(encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            fields = list(reader.fieldnames or [])
            rows = [row for row in reader if row["project_id"] in keep]
        with (args.output / name).open("w", encoding="utf-8-sig", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    shutil.copy(args.tables / "extraction_manifest.json", args.output / "extraction_manifest.json")
    print(f"kept {len(keep)} projects")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
