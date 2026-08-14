#!/usr/bin/env python3
"""Validate the publication-archive census and its inspection ledger."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import urllib.parse
from pathlib import Path

from build_archive_inventory_v02 import RESOLUTION_POLICY_VERSION, select_source_file, ArchiveFileRecord
from inspect_archives import ALLOWED_DOWNLOAD_HOSTS, INSPECTION_POLICY_VERSION


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CENSUS = ROOT / "data" / "census_frame_v0.2"
TERMINAL_INVENTORY_STATUSES = {
    "resolved_source_archive",
    "resolved_no_source_archive",
    "no_external_archive",
}
TERMINAL_INSPECTION_STATUSES = {"inspected", "unsupported_archive_format", "unavailable"}
ALLOWED_UNAVAILABLE_ERRORS = {
    "download_timeout",
    "download_transport_TimeoutError",
    "download_transport_ConnectionResetError",
    "download_http_403",
    "download_http_404",
    "curl_download_failed_22",
    "curl_download_failed_28",
    "archive_text_budget_exceeded",
    "archive_member_limit_exceeded",
}
CONTROLLED_INSPECTION_ERRORS = {
    "archive_text_budget_exceeded",
    "archive_member_limit_exceeded",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def validate(census: Path, *, require_complete_inspection: bool = False) -> dict[str, object]:
    failures: list[str] = []
    warnings: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    links = read_csv(census / "archive_linkage_ledger.csv")
    files = read_csv(census / "archive_file_inventory.csv")
    inspections = read_csv(census / "archive_static_inspection.csv")
    manifest = json.loads((census / "archive_inventory_manifest.json").read_text(encoding="utf-8"))
    checkpoint = json.loads((census / "archive_inventory_checkpoint.json").read_text(encoding="utf-8"))
    candidates = read_csv(census / "provisional_eligible.csv")

    candidate_dois = {row["doi"] for row in candidates}
    link_dois = [row["scholarly_doi"] for row in links]
    file_keys = [(row["scholarly_doi"], row["file_key"]) for row in files]
    selected = [row for row in files if row["selected_source_archive"] == "true"]
    selected_keys = {(row["scholarly_doi"], row["file_key"]) for row in selected}
    inspection_keys = {(row["scholarly_doi"], row["file_key"]) for row in inspections}

    check(len(links) == len(candidates), f"linkage_count:{len(links)}:{len(candidates)}")
    check(len(link_dois) == len(set(link_dois)), "duplicate_linkage_doi")
    check(set(link_dois) == candidate_dois, "linkage_candidate_doi_set_mismatch")
    check(len(file_keys) == len(set(file_keys)), "duplicate_archive_file_key")
    check(all(row.get("selected_role") == "census_provisional" for row in links), "invalid_linkage_role")
    check(all(row.get("selected_role") == "census_provisional" for row in files), "invalid_file_role")
    check(all(row.get("third_party_code_executed") == "false" for row in links), "linkage_code_execution_flag")
    check(all(row["inventory_status"] in TERMINAL_INVENTORY_STATUSES or row["inventory_status"] == "unresolved" for row in links), "unknown_inventory_status")
    check(all(not row.get("publication_date_delta_days") or int(row["publication_date_delta_days"]) <= 0 for row in links), "future_archive_date")
    check(all(row.get("archive_license") not in {"open", "closed", "restricted"} for row in links), "access_state_in_license_field")
    check(all(re.fullmatch(r"md5:[0-9a-f]{32}", row.get("repository_checksum", "")) for row in selected), "selected_checksum_not_md5")
    check(all(int(row.get("file_size_bytes") or 0) > 0 for row in selected), "selected_size_not_positive")
    check(all(
        urllib.parse.urlparse(row.get("download_url", "")).scheme == "https"
        and (urllib.parse.urlparse(row.get("download_url", "")).hostname or "").lower() in ALLOWED_DOWNLOAD_HOSTS
        for row in selected
    ), "selected_download_not_safe_https")

    selected_counts: dict[str, int] = {}
    for row in selected:
        selected_counts[row["scholarly_doi"]] = selected_counts.get(row["scholarly_doi"], 0) + 1
    resolved_dois = {row["scholarly_doi"] for row in links if row["inventory_status"] == "resolved_source_archive"}
    check(set(selected_counts) == resolved_dois, "selected_resolved_doi_set_mismatch")
    check(all(count == 1 for count in selected_counts.values()), "selected_archive_not_unique_per_project")

    files_by_doi: dict[str, list[dict[str, str]]] = {}
    for row in files:
        files_by_doi.setdefault(row["scholarly_doi"], []).append(row)
    links_by_doi = {row["scholarly_doi"]: row for row in links}
    for doi in resolved_dois:
        link = links_by_doi[doi]
        file_rows = files_by_doi[doi]
        expected = select_source_file(
            [ArchiveFileRecord(**{key: row.get(key, "") for key in ArchiveFileRecord.__dataclass_fields__}) for row in file_rows],
            link["archive_version"],
        )
        actual = next(row for row in file_rows if row["selected_source_archive"] == "true")
        check(expected is not None and expected.file_key == actual["file_key"], f"selection_policy_mismatch:{doi}")
        check(link["selected_file_key"] == actual["file_key"], f"selected_key_link_mismatch:{doi}")
        check(link["selected_file_size_bytes"] == actual["file_size_bytes"], f"selected_size_link_mismatch:{doi}")
        check(link["selected_repository_checksum"] == actual["repository_checksum"], f"selected_checksum_link_mismatch:{doi}")
        check(link["selected_download_url"] == actual["download_url"], f"selected_url_link_mismatch:{doi}")
    for doi, file_rows in files_by_doi.items():
        link = links_by_doi[doi]
        check(int(link["archive_file_count"]) == len(file_rows), f"file_count_mismatch:{doi}")
        check(int(link["total_archive_size_bytes"]) == sum(int(row["file_size_bytes"]) for row in file_rows), f"file_bytes_mismatch:{doi}")
        check(int(link["source_archive_candidate_count"]) == sum(bool(row["source_format_priority"]) for row in file_rows), f"source_candidate_count_mismatch:{doi}")

    check(manifest.get("candidate_records") == len(candidates), "manifest_candidate_count_mismatch")
    check(manifest.get("projects_inventoried") == len(links), "manifest_linkage_count_mismatch")
    check(manifest.get("projects_remaining") == 0, "manifest_projects_remaining")
    check(manifest.get("run_state") == "complete", "manifest_not_complete")
    check(manifest.get("resolution_policy_version") == RESOLUTION_POLICY_VERSION, "manifest_policy_mismatch")
    check(checkpoint.get("resolution_policy_version") == RESOLUTION_POLICY_VERSION, "checkpoint_policy_mismatch")
    check(checkpoint.get("candidate_sha256") == manifest.get("candidate_sha256"), "checkpoint_candidate_hash_mismatch")
    check(checkpoint.get("crossref_sha256") == manifest.get("crossref_sha256"), "checkpoint_crossref_hash_mismatch")
    check(len(checkpoint.get("results", {})) == len(links), "checkpoint_result_count_mismatch")
    check(manifest.get("file_records") == len(files), "manifest_file_count_mismatch")
    check(manifest.get("selected_source_archives") == len(selected), "manifest_selected_count_mismatch")
    check(manifest.get("linkage_ledger_sha256") == sha256(census / "archive_linkage_ledger.csv"), "manifest_linkage_hash_mismatch")
    check(manifest.get("file_inventory_sha256") == sha256(census / "archive_file_inventory.csv"), "manifest_file_hash_mismatch")
    check(manifest.get("third_party_code_executed") is False, "manifest_code_execution_flag")
    check(not (census / "archive_inventory_checkpoint.json.tmp").exists(), "orphan_inventory_checkpoint_temporary")

    check(inspection_keys <= selected_keys, "inspection_not_selected_inventory_subset")
    check(len(inspections) == len(inspection_keys), "duplicate_inspection_key")
    check(all(row.get("third_party_code_executed") == "false" for row in inspections), "inspection_code_execution_flag")
    completed_inspection_keys = {
        (row["scholarly_doi"], row["file_key"])
        for row in inspections
        if row.get("inspection_status") in TERMINAL_INSPECTION_STATUSES
        or row.get("inspection_status") == "inspection_error"
        and row.get("inspection_error") in CONTROLLED_INSPECTION_ERRORS
    }
    if require_complete_inspection:
        check(completed_inspection_keys == selected_keys, "archive_inspection_incomplete")
        check(all(
            row.get("inspection_status") in {"inspected", "unavailable"}
            or row.get("inspection_status") == "inspection_error" and row.get("inspection_error") in CONTROLLED_INSPECTION_ERRORS
            for row in inspections
        ), "nonpassing_inspection_status")
        check(all(
            row.get("inspection_status") == "inspected" and not row.get("inspection_error")
            or row.get("inspection_status") == "unavailable" and row.get("inspection_error") in ALLOWED_UNAVAILABLE_ERRORS
            or row.get("inspection_status") == "inspection_error" and row.get("inspection_error") in CONTROLLED_INSPECTION_ERRORS
            for row in inspections
        ), "inspection_error_present")
        check(all(row.get("suspicious_member_path") == "false" for row in inspections), "suspicious_archive_member")
        check(all(
            row.get("inspection_status") in {"unavailable", "inspection_error"} or re.fullmatch(r"[0-9a-f]{64}", row.get("local_sha256", ""))
            for row in inspections
        ), "invalid_local_sha256")
        inspection_manifest_path = census / "archive_inspection_manifest.json"
        check(inspection_manifest_path.exists(), "inspection_manifest_missing")
        if inspection_manifest_path.exists():
            inspection_manifest = json.loads(inspection_manifest_path.read_text(encoding="utf-8"))
            check(inspection_manifest.get("inspection_policy_version") == INSPECTION_POLICY_VERSION, "inspection_policy_mismatch")
            check(inspection_manifest.get("complete") is True, "inspection_manifest_not_complete")
            check(inspection_manifest.get("inspection_sha256") == sha256(census / "archive_static_inspection.csv"), "inspection_manifest_hash_mismatch")
            check(inspection_manifest.get("inventory_sha256") == sha256(census / "archive_file_inventory.csv"), "inspection_inventory_hash_mismatch")
    elif completed_inspection_keys != selected_keys:
        warnings.append(f"archive_inspection_incomplete:{len(completed_inspection_keys)}/{len(selected_keys)}")

    unresolved = [row for row in links if row["inventory_status"] == "unresolved"]
    if unresolved:
        warnings.append(f"archive_inventory_unresolved:{len(unresolved)}")

    return {
        "schema_version": "0.3",
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "warnings": warnings,
        "counts": {
            "candidates": len(candidates),
            "linkages": len(links),
            "files": len(files),
            "selected_archives": len(selected),
            "completed_inspections": len(completed_inspection_keys),
            "unresolved": len(unresolved),
        },
        "hashes": {
            "linkage_ledger_sha256": sha256(census / "archive_linkage_ledger.csv"),
            "file_inventory_sha256": sha256(census / "archive_file_inventory.csv"),
            "inspection_sha256": sha256(census / "archive_static_inspection.csv"),
        },
        "input_sha256": {
            f"data/census_frame_v0.2/{name}": sha256(census / name)
            for name in (
                "provisional_eligible.csv",
                "archive_linkage_ledger.csv",
                "archive_file_inventory.csv",
                "archive_inventory_manifest.json",
                "archive_static_inspection.csv",
                "archive_eligibility_ledger.csv",
            )
            if (census / name).exists()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--census", type=Path, default=DEFAULT_CENSUS)
    parser.add_argument("--require-complete-inspection", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate(args.census, require_complete_inspection=args.require_complete_inspection)
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
