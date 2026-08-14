#!/usr/bin/env python3
"""Validate scoping artifacts without treating uncollected data as complete."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRAME = ROOT / "data" / "candidate_frame_v0.1"


def read_csv(name: str) -> list[dict[str, str]]:
    return list(csv.DictReader((FRAME / name).open(encoding="utf-8-sig", newline="")))


def check(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []

    for path in ROOT.joinpath("schema").glob("*.json"):
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            failures.append(f"invalid_json_schema:{path.name}:{exc}")

    manifest = json.loads((FRAME / "acquisition_manifest.json").read_text(encoding="utf-8"))
    candidates = read_csv("pilot_candidates.csv")
    ledger = read_csv("screening_ledger.csv")
    audit = read_csv("static_eligibility_audit.csv")
    inventory = read_csv("archive_file_inventory.csv")
    inspection_path = FRAME / "archive_static_inspection.csv"
    inspections = read_csv("archive_static_inspection.csv") if inspection_path.exists() else []

    check(manifest["sampling_frame"]["crossref_record_count"] == 940, "crossref_count_not_940", failures)
    check(len(candidates) == 30, f"candidate_count:{len(candidates)}", failures)
    check(sum(row["selected_role"] == "primary" for row in candidates) == 20, "primary_count_not_20", failures)
    check(sum(row["selected_role"] == "replacement" for row in candidates) == 10, "replacement_count_not_10", failures)
    check(len(ledger) == 90, f"ledger_count:{len(ledger)}", failures)
    check(len(audit) == 30, f"static_audit_count:{len(audit)}", failures)
    check(len({row["doi"] for row in audit}) == 30, "duplicate_static_audit_doi", failures)
    check(len({row["scholarly_doi"] for row in inventory}) == 30, "archive_inventory_project_count", failures)
    check(all(re.fullmatch(r"10\.5281/zenodo\.\d+", row["archive_doi"]) for row in audit), "noncanonical_archive_doi", failures)
    check(all(row["inventory_status"] == "resolved" for row in inventory), "unresolved_archive_inventory", failures)
    check(all(row["repository_checksum"].startswith("md5:") for row in inventory), "missing_repository_md5", failures)

    if inspections:
        primary_inventory = [row for row in inventory if row["selected_role"] == "primary"]
        primary_keys = {(row["scholarly_doi"], row["file_key"]) for row in primary_inventory}
        primary_inspections = [
            row for row in inspections if (row["scholarly_doi"], row["file_key"]) in primary_keys
        ]
        check(
            len(primary_inspections) == len(primary_inventory),
            f"primary_archive_inspection_incomplete:{len(primary_inspections)}/{len(primary_inventory)}",
            failures,
        )
        if len(inspections) < len(inventory):
            warnings.append(f"replacement_archive_inspection_deferred:{len(inspections)}/{len(inventory)}_all_files")
        check(all(row["third_party_code_executed"] == "false" for row in inspections), "unexpected_code_execution_flag", failures)
        check(all(row["local_sha256"] for row in inspections), "missing_local_sha256", failures)
        check(all(row["suspicious_member_path"] == "false" for row in inspections), "suspicious_archive_member", failures)
        check(all(row["inspection_status"] == "inspected" for row in inspections), "archive_inspection_error", failures)
    else:
        warnings.append("archive_inspection_not_started")

    manuscript = (ROOT / "manuscript_scaffold.md").read_text(encoding="utf-8")
    title = manuscript.splitlines()[0].removeprefix("# ")
    check(len(title) <= 110, f"title_too_long:{len(title)}", failures)
    check("ReproDecay" not in title, "brand_name_in_title", failures)
    forbidden = ["first-ever", "unprecedented", "revolutionary", "AI-ready"]
    for word in forbidden:
        check(word.lower() not in manuscript.lower(), f"forbidden_marketing_word:{word}", failures)
    warnings.append("manuscript_contains_intentional_placeholders")

    result = {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "warnings": warnings,
        "counts": {
            "candidate_frame": manifest["sampling_frame"]["crossref_record_count"],
            "screened": len(ledger),
            "pilot_candidates": len(candidates),
            "archive_files": len(inventory),
            "archive_files_inspected": len(inspections),
        },
    }
    print(json.dumps(result, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
