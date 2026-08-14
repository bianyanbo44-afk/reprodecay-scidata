#!/usr/bin/env python3
"""Inventory publication-linked Zenodo archive files without executing them."""

from __future__ import annotations

import argparse
import csv
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


USER_AGENT = "ReproDecay-archive-inventory/0.1"


def request_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", default="data/candidate_frame_v0.1/static_eligibility_audit.csv")
    parser.add_argument("--output", default="data/candidate_frame_v0.1/archive_file_inventory.csv")
    args = parser.parse_args()

    candidates = list(csv.DictReader(Path(args.audit).open(encoding="utf-8-sig", newline="")))
    output_rows: list[dict[str, str]] = []

    for candidate in candidates:
        doi_match = re.search(r"10\.5281/zenodo\.(\d+)", candidate["archive_doi"], flags=re.IGNORECASE)
        if not doi_match:
            output_rows.append(
                {
                    "selected_role": candidate["selected_role"],
                    "rank": candidate["rank"],
                    "scholarly_doi": candidate["doi"],
                    "archive_doi": candidate["archive_doi"],
                    "archive_record_id": "",
                    "archive_concept_doi": "",
                    "archive_version": "",
                    "archive_publication_date": "",
                    "file_key": "",
                    "file_size_bytes": "",
                    "repository_checksum": "",
                    "download_url": "",
                    "inventory_status": "unresolved_archive_doi",
                    "inventoried_at_utc": datetime.now(timezone.utc).isoformat(),
                }
            )
            continue
        record_id = doi_match.group(1)
        payload = request_json(f"https://zenodo.org/api/records/{record_id}")
        metadata = payload.get("metadata") or {}
        files = payload.get("files") or []
        if not files:
            files = [{}]
        for file_record in files:
            links = file_record.get("links") or {}
            output_rows.append(
                {
                    "selected_role": candidate["selected_role"],
                    "rank": candidate["rank"],
                    "scholarly_doi": candidate["doi"],
                    "archive_doi": str(payload.get("doi") or candidate["archive_doi"]).lower(),
                    "archive_record_id": record_id,
                    "archive_concept_doi": str(payload.get("conceptdoi") or "").lower(),
                    "archive_version": str(metadata.get("version") or ""),
                    "archive_publication_date": str(metadata.get("publication_date") or ""),
                    "file_key": str(file_record.get("key") or ""),
                    "file_size_bytes": str(file_record.get("size") or ""),
                    "repository_checksum": str(file_record.get("checksum") or ""),
                    "download_url": str(links.get("self") or ""),
                    "inventory_status": "resolved" if file_record else "no_files",
                    "inventoried_at_utc": datetime.now(timezone.utc).isoformat(),
                }
            )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(output_rows[0]))
        writer.writeheader()
        writer.writerows(output_rows)

    total_size = sum(int(row["file_size_bytes"] or 0) for row in output_rows)
    print(
        json.dumps(
            {
                "candidate_records": len(candidates),
                "archive_file_records": len(output_rows),
                "total_archive_size_bytes": total_size,
                "third_party_code_executed": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
