#!/usr/bin/env python3
"""Validate v0.2 pilot tables and test deterministic reruns."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_csv(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(path.open(encoding="utf-8-sig", newline="")))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tables", default="data/pilot_v0.2/tables")
    parser.add_argument("--rerun", action="store_true")
    args = parser.parse_args()

    tables = ROOT / args.tables
    failures: list[str] = []
    warnings: list[str] = []
    projects = read_csv(tables / "project_records.csv")
    snapshots = read_csv(tables / "snapshot_records.csv")
    structures = read_csv(tables / "structure_records.csv")
    dependencies = read_csv(tables / "dependency_records.csv")
    symbols = read_csv(tables / "api_symbol_records.csv")
    deltas = read_csv(tables / "pair_delta_records.csv")
    validations = read_csv(tables / "validation_records.csv")

    project_ids = {row["project_id"] for row in projects}
    snapshot_ids = {row["snapshot_id"] for row in snapshots}
    check(len(projects) == len(project_ids), "duplicate_project_id", failures)
    check(len(snapshots) == len(snapshot_ids), "duplicate_snapshot_id", failures)
    check(len(snapshots) == 2 * len(projects), "snapshot_count_not_two_per_project", failures)
    check(all(sum(row["project_id"] == pid for row in snapshots) == 2 for pid in project_ids), "project_without_two_snapshots", failures)
    check(all({row["snapshot_role"] for row in snapshots if row["project_id"] == pid} == {"publication", "endpoint"} for pid in project_ids), "snapshot_role_pair_invalid", failures)
    check(all(row["project_id"] in project_ids for row in structures + dependencies + symbols + deltas + validations), "orphan_project_reference", failures)
    check(all(row["snapshot_id"] in snapshot_ids for row in structures + dependencies + symbols + validations), "orphan_snapshot_reference", failures)
    check(all(row["third_party_code_executed"] == "false" for row in snapshots + structures), "third_party_execution_flag", failures)
    check(all(row["status"] != "error" for row in validations), "validation_error_present", failures)
    check(all(row["delta_type"] in {"added", "removed", "changed", "unchanged", "unavailable"} for row in deltas), "invalid_delta_type", failures)
    check(all(row["comparison_status"] in {"comparable", "partial_parse", "unavailable"} for row in deltas), "invalid_comparison_status", failures)
    check(len({(row["pair_id"], row["family"], row["feature_group"], row["feature_key"]) for row in deltas}) == len(deltas), "duplicate_delta_key", failures)
    check(len({(row["snapshot_id"], row["symbol_key"]) for row in symbols}) == len(symbols), "duplicate_symbol_key", failures)
    check(all(row["archive_sha256"] and len(row["archive_sha256"]) == 64 for row in snapshots), "invalid_archive_sha256", failures)
    check(all(row["snapshot_role"] in {"publication", "endpoint"} for row in snapshots), "invalid_snapshot_role", failures)
    check(all(row["source_provider"] in {"zenodo", "github"} for row in snapshots), "invalid_source_provider", failures)
    check(all(row["selected_role"] in {"primary", "replacement"} for row in projects), "invalid_selected_role", failures)
    check(all(row["endpoint_ref_type"] in {"release", "default_branch"} for row in projects), "invalid_endpoint_ref_type", failures)
    check(all(row["parse_status"] in {"parsed", "parsed_non_pep508", "parsed_poetry", "directive", "unparsed"} for row in dependencies), "invalid_dependency_parse_status", failures)
    check(all(row["symbol_kind"] in {"class", "constructor", "method", "function", "async_function"} for row in symbols), "invalid_symbol_kind", failures)
    check(all(row["export_evidence"] in {"literal_all", "lexical_nonprivate", "class_member"} for row in symbols), "invalid_export_evidence", failures)
    check(all(row["family"] in {"api", "dependency", "structure"} for row in deltas), "invalid_delta_family", failures)
    check(all(row["status"] in {"pass", "warning", "error"} for row in validations), "invalid_validation_status", failures)
    check(all(row["comparison_status"] == "partial_parse" or row["comparison_status"] == "unavailable" or row["comparison_status"] == "comparable" for row in deltas), "invalid_comparison_status", failures)
    check(all("local_archive_path" not in row for row in snapshots), "local_path_leaked", failures)
    check(all(Path(row["source_file"]).is_absolute() is False for row in dependencies + symbols), "absolute_source_evidence_path", failures)
    partial_pairs = {row["pair_id"] for row in deltas if row["comparison_status"] == "partial_parse"}
    if partial_pairs:
        warnings.append(f"partial_parse_pairs:{len(partial_pairs)}")
    if len(projects) < 15:
        warnings.append(f"pilot_gate_not_yet_reached:{len(projects)}/15")

    rerun_hash_match = None
    if args.rerun and not failures:
        with tempfile.TemporaryDirectory(prefix="version_pair_validation_") as temp_dir:
            rerun_dir = Path(temp_dir) / "tables"
            command = [
                sys.executable,
                str(ROOT / "scripts" / "extract_version_pairs.py"),
                "--output-dir",
                str(rerun_dir),
            ]
            completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=300)
            check(completed.returncode == 0, f"rerun_failed:{completed.stderr[-500:]}", failures)
            if completed.returncode == 0:
                canonical = [
                    "project_records.csv", "snapshot_records.csv", "structure_records.csv",
                    "dependency_records.csv", "api_symbol_records.csv", "pair_delta_records.csv",
                    "validation_records.csv", "extraction_manifest.json",
                ]
                mismatches = [name for name in canonical if sha256(tables / name) != sha256(rerun_dir / name)]
                rerun_hash_match = not mismatches
                check(not mismatches, f"non_deterministic_tables:{','.join(mismatches)}", failures)

    result = {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "warnings": warnings,
        "counts": {
            "projects": len(projects),
            "snapshots": len(snapshots),
            "dependency_records": len(dependencies),
            "api_symbol_records": len(symbols),
            "pair_delta_records": len(deltas),
            "validation_records": len(validations),
        },
        "deterministic_rerun_byte_identical": rerun_hash_match,
    }
    print(json.dumps(result, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
