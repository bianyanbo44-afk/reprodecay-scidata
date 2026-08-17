#!/usr/bin/env python3
"""Verify a derived release candidate without requiring third-party archives."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

import yaml

from audit_release_privacy import scan_paths


REQUIRED = (
    "README.md",
    "CITATION.cff",
    "LICENSE-CODE",
    "LICENSE-DATA",
    "LICENSES.md",
    "archive_eligibility_policy_v0.2.md",
    "data/candidate_frame_v0.1/crossref_joss_2018_2020.json",
    "data/census_frame_v0.2/archive_inspection_manifest.json",
    "data/census_frame_v0.2/archive_inventory_manifest.json",
    "data/census_v0.2/census_dataset_validation.json",
    "data/census_v0.2/census_summary.json",
    "data/census_v0.2/independent_semantic_audit.json",
    "data/census_v0.2/independent_semantic_audit.csv",
    "data/census_v0.2/independent_semantic_audit_sample.csv",
    "data/census_v0.2/tables/extraction_manifest.json",
    "data/census_v0.2/tables/project_records.csv",
    "data/census_v0.2/tables/snapshot_records.csv",
    "data/census_v0.2/tables/structure_records.csv",
    "data/census_v0.2/tables/dependency_records.csv",
    "data/census_v0.2/tables/api_symbol_records.csv",
    "data/census_v0.2/tables/pair_delta_records.csv",
    "data/census_v0.2/tables/validation_records.csv",
    "scripts/validate_census_dataset.py",
    "scripts/independent_semantic_audit.py",
    "scripts/make_scidata_figures.py",
    "scripts/prefetch_endpoint_archives.py",
    "schema/datapackage.json",
    "schema/v0.2_controlled_vocabularies.json",
    "manuscript/main.tex",
    "manuscript/references.bib",
    "manuscript/paper.pdf",
    "manuscript/figures/fig1_pairing_workflow.pdf",
    "manuscript/figures/fig2_change_landscape.pdf",
    "manuscript/figures/fig3_metadata_transitions.pdf",
    "manuscript/figures/fig4_validation_audit.pdf",
    "requirements.txt",
    "requirements-lock-win-py312.txt",
    "environment_reference.json",
    "pytest.ini",
    "REPRODUCE.md",
)

EXPECTED_TABLE_ROWS = {
    "project_records.csv": 345,
    "snapshot_records.csv": 690,
    "structure_records.csv": 690,
    "dependency_records.csv": 9_074,
    "api_symbol_records.csv": 121_085,
    "pair_delta_records.csv": 95_787,
    "validation_records.csv": 2_876,
}

EXPECTED_SNAPSHOT_PROVIDERS = {
    ("publication", "zenodo"): 341,
    ("publication", "figshare"): 4,
    ("endpoint", "github"): 345,
}

EXPECTED_CODE_REPOSITORY = "https://github.com/bianyanbo44-afk/reprodecay-scidata"
EXPECTED_RELEASE_VERSION = "0.2.3"
TRANSIENT_PATH_PARTS = {".git", "__pycache__", ".pytest_cache"}
TRANSIENT_SUFFIXES = {".pyc", ".pyo"}


def is_transient_artifact(relative: Path) -> bool:
    return bool(TRANSIENT_PATH_PARTS.intersection(relative.parts)) or relative.suffix in TRANSIENT_SUFFIXES


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def verify_data_package(root: Path, failures: list[str]) -> None:
    descriptor_path = root / "schema/datapackage.json"
    if not descriptor_path.is_file():
        return
    try:
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        failures.append(f"datapackage_invalid:{type(exc).__name__}")
        return
    resources = descriptor.get("resources")
    if not isinstance(resources, list) or len(resources) != 7:
        failures.append("datapackage_resource_count_mismatch")
        return
    for resource in resources:
        if not isinstance(resource, dict):
            failures.append("datapackage_resource_invalid")
            continue
        relative = resource.get("path")
        fields = resource.get("schema", {}).get("fields", [])
        if not isinstance(relative, str) or not isinstance(fields, list):
            failures.append(f"datapackage_resource_contract_invalid:{resource.get('name', '')}")
            continue
        table_path = (descriptor_path.parent / relative).resolve()
        if not table_path.is_relative_to(root) or not table_path.is_file():
            failures.append(f"datapackage_resource_missing:{resource.get('name', '')}")
            continue
        try:
            with table_path.open(encoding=resource.get("encoding", "utf-8-sig"), newline="") as handle:
                actual_fields = csv.DictReader(handle).fieldnames
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            failures.append(f"datapackage_resource_unreadable:{resource.get('name', '')}:{type(exc).__name__}")
            continue
        declared_fields = [field.get("name") for field in fields if isinstance(field, dict)]
        if actual_fields != declared_fields:
            failures.append(f"datapackage_header_mismatch:{resource.get('name', '')}")


def verify_citation_file(root: Path, failures: list[str]) -> None:
    path = root / "CITATION.cff"
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
        citation = yaml.safe_load(text)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        failures.append(f"citation_cff_invalid:{type(exc).__name__}")
        return
    if not isinstance(citation, dict) or citation.get("cff-version") != "1.2.0":
        failures.append("citation_cff_version_mismatch")
        return
    if "<" in text or ">" in text:
        failures.append("citation_cff_placeholder_present")
    if citation.get("repository-code") != EXPECTED_CODE_REPOSITORY:
        failures.append("citation_cff_repository_mismatch")
    if str(citation.get("version")) != EXPECTED_RELEASE_VERSION:
        failures.append("citation_cff_release_version_mismatch")


def verify(root: Path) -> dict[str, object]:
    failures: list[str] = []
    manifest_path = root / "package_manifest.json"
    if not manifest_path.is_file():
        return {"status": "FAIL", "failures": ["package_manifest_missing"]}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"status": "FAIL", "failures": [f"package_manifest_invalid:{type(exc).__name__}"]}
    if manifest.get("version") != "v0.2":
        failures.append("package_version_mismatch")
    if manifest.get("raw_archives_included") is not False:
        failures.append("raw_archive_boundary_not_false")
    if manifest.get("third_party_code_executed") is not False:
        failures.append("package_execution_assertion_failed")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        failures.append("package_file_manifest_missing")
        files = []
    expected = {str(item.get("path")): str(item.get("sha256")) for item in files if isinstance(item, dict)}
    if len(expected) != len(files):
        failures.append("package_manifest_duplicate_or_invalid_paths")
    for relative in REQUIRED:
        if relative not in expected:
            failures.append(f"required_not_manifested:{relative}")
        path = root / relative
        if not path.is_file():
            failures.append(f"required_file_missing:{relative}")
    for relative, expected_hash in expected.items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            failures.append(f"manifest_path_outside_root:{relative}")
            continue
        if not path.is_file():
            failures.append(f"manifest_file_missing:{relative}")
        elif sha256(path) != expected_hash:
            failures.append(f"hash_mismatch:{relative}")
        if relative.replace("\\", "/").startswith("data/raw/"):
            failures.append(f"raw_archive_path_present:{relative}")

    actual_payload = set()
    for path in root.rglob("*"):
        if not path.is_file() or path.name in {"package_manifest.json", "checksums.sha256"}:
            continue
        relative = path.relative_to(root)
        if not is_transient_artifact(relative):
            actual_payload.add(relative.as_posix())
    if actual_payload != set(expected):
        missing = sorted(set(expected) - actual_payload)
        extra = sorted(actual_payload - set(expected))
        failures.append(f"package_file_set_mismatch:missing={len(missing)}:extra={len(extra)}")

    checksums_path = root / "checksums.sha256"
    if not checksums_path.is_file():
        failures.append("checksums_file_missing")
    else:
        checksum_rows: dict[str, str] = {}
        for line in checksums_path.read_text(encoding="ascii").splitlines():
            digest, separator, relative = line.partition("  ")
            if not separator or not relative:
                failures.append("checksums_file_malformed")
                continue
            checksum_rows[relative] = digest
        expected_checksums = dict(expected)
        expected_checksums["package_manifest.json"] = sha256(manifest_path)
        if checksum_rows != expected_checksums:
            failures.append("checksums_file_content_mismatch")
    validation_path = root / "data/census_v0.2/census_dataset_validation.json"
    if validation_path.is_file():
        validation = json.loads(validation_path.read_text(encoding="utf-8"))
        if validation.get("status") != "PASS":
            failures.append("strict_census_validation_not_pass")
    extraction_path = root / "data/census_v0.2/tables/extraction_manifest.json"
    if extraction_path.is_file():
        extraction = json.loads(extraction_path.read_text(encoding="utf-8"))
        if extraction.get("third_party_code_executed") is not False:
            failures.append("extraction_execution_assertion_failed")

    verify_data_package(root, failures)
    verify_citation_file(root, failures)
    legacy_schemas = {
        "schema/project_record.schema.json",
        "schema/execution_record.schema.json",
        "schema/failure_annotation.schema.json",
    }
    if legacy_schemas & set(expected):
        failures.append("legacy_v01_schema_present")

    privacy_findings = scan_paths(root, expected)
    if privacy_findings:
        failures.append(f"privacy_or_secret_scan_failed:{len(privacy_findings)}")

    tables_root = root / "data/census_v0.2/tables"
    for name, expected_count in EXPECTED_TABLE_ROWS.items():
        path = tables_root / name
        if path.is_file():
            try:
                observed_count = len(read_csv_rows(path))
            except (OSError, UnicodeDecodeError, csv.Error) as exc:
                failures.append(f"table_unreadable:{name}:{type(exc).__name__}")
                continue
            if observed_count != expected_count:
                failures.append(f"table_row_count_mismatch:{name}:{observed_count}")

    projects_path = tables_root / "project_records.csv"
    if projects_path.is_file():
        project_roles = Counter(row.get("selected_role", "") for row in read_csv_rows(projects_path))
        if project_roles != Counter({"census_provisional": 345}):
            failures.append(f"project_role_accounting_mismatch:{dict(project_roles)}")

    snapshots_path = tables_root / "snapshot_records.csv"
    if snapshots_path.is_file():
        snapshot_providers = Counter(
            (row.get("snapshot_role", ""), row.get("source_provider", ""))
            for row in read_csv_rows(snapshots_path)
        )
        if snapshot_providers != Counter(EXPECTED_SNAPSHOT_PROVIDERS):
            failures.append(f"snapshot_provider_accounting_mismatch:{dict(snapshot_providers)}")
    raw_files = [p for p in (root / "data").rglob("*") if p.is_file() and "raw" in p.parts]
    if raw_files:
        failures.append(f"raw_files_present:{len(raw_files)}")
    return {
        "status": "PASS" if not failures else "FAIL",
        "version": manifest.get("version"),
        "files_checked": len(expected),
        "failures": failures,
        "raw_archives_included": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("release_candidate_v0.2"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify(args.root.resolve())
    if args.output:
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
