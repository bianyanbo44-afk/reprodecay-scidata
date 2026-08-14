#!/usr/bin/env python3
"""Apply the frozen publication-archive eligibility rule to the census."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--census", type=Path, default=Path("data/census_frame_v0.2"))
    args = parser.parse_args()
    census = args.census
    candidates = read_csv(census / "provisional_eligible.csv")
    links = {row["scholarly_doi"]: row for row in read_csv(census / "archive_linkage_ledger.csv")}
    inspections = {
        (row["scholarly_doi"], row["file_key"]): row
        for row in read_csv(census / "archive_static_inspection.csv")
    }
    rows: list[dict[str, str]] = []
    now = datetime.now(timezone.utc).isoformat()
    for candidate in candidates:
        doi = candidate["doi"]
        link = links[doi]
        inspection = inspections.get((doi, link["selected_file_key"]))
        linkage_pass = link["inventory_status"] == "resolved_source_archive"
        date_pass = bool(link["publication_date_delta_days"]) and int(link["publication_date_delta_days"]) <= 0
        checksum_pass = bool(inspection and inspection["local_sha256"] and inspection["inspection_status"] == "inspected")
        path_pass = bool(inspection and inspection["suspicious_member_path"] == "false")
        packaging_pass = bool(inspection and inspection["root_packaging_files"])
        licence_evidence = ";".join(filter(None, (link["archive_license"], inspection["root_licence_files"] if inspection else "")))
        licence_pass = bool(licence_evidence)
        final = ""
        reason = ""
        if not linkage_pass:
            if link["inventory_status"] in {"resolved_no_source_archive"}:
                final, reason = "excluded_archive", "no_supported_source_archive_file"
            else:
                final, reason = "unresolved_archive", link["reason_code"] or "publication_archive_unresolved"
        elif not inspection or inspection["inspection_status"] == "inspection_error":
            final, reason = "unresolved_archive", "archive_inspection_incomplete"
        elif inspection["inspection_status"] == "unsupported_archive_format":
            final, reason = "excluded_archive", "unsupported_archive_format"
        elif not date_pass:
            final, reason = "unresolved_archive", "archive_date_not_confirmed_contemporaneous"
        elif not checksum_pass:
            final, reason = "unresolved_archive", "archive_checksum_not_verified"
        elif not path_pass:
            final, reason = "excluded_archive", "suspicious_archive_member_path"
        elif not packaging_pass:
            final, reason = "excluded_archive", "no_root_python_packaging_evidence_in_archive"
        elif not licence_pass:
            final, reason = "excluded_archive", "no_archive_licence_evidence"
        else:
            final = "eligible_archive"
        rows.append({
            "rank": candidate["rank"],
            "selected_role": candidate["selected_role"],
            "scholarly_doi": doi,
            "linkage_status": "pass" if linkage_pass else "fail",
            "date_status": "pass" if date_pass else "fail",
            "checksum_status": "pass" if checksum_pass else "pending",
            "path_safety_status": "pass" if path_pass else "pending" if not inspection else "fail",
            "packaging_status": "pass" if packaging_pass else "pending" if not inspection else "fail",
            "packaging_evidence": inspection["root_packaging_files"] if inspection else "",
            "licence_status": "pass" if licence_pass else "pending" if not inspection else "fail",
            "licence_evidence": licence_evidence,
            "final_eligibility": final,
            "reason_code": reason,
            "decision_basis": "archive-eligibility-policy-v0.2",
            "audited_at_utc": now,
            "third_party_code_executed": "false",
        })
    output = census / "archive_eligibility_ledger.csv"
    write_csv(output, rows)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["final_eligibility"]] = counts.get(row["final_eligibility"], 0) + 1
    print(json.dumps({"records": len(rows), "status_counts": counts}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
