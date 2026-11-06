#!/usr/bin/env python3
"""Validate census endpoint resolution and the eight-table static extraction (schema v0.3).

This gate reads and hashes local archives but never imports, installs, extracts,
or executes candidate software.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import urllib.parse
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from extract_version_pairs import PARSER_VERSION, SCHEMA_VERSION
from inspect_archives import safe_cache_name
from resolve_endpoint_snapshots import ENDPOINT_POLICY_VERSION


ROOT = Path(__file__).resolve().parents[1]
TABLE_NAMES = (
    "project_records.csv",
    "snapshot_records.csv",
    "structure_records.csv",
    "dependency_records.csv",
    "declaration_source_records.csv",
    "api_symbol_records.csv",
    "pair_delta_records.csv",
    "validation_records.csv",
)
PROVIDERS = {"zenodo", "figshare", "caltechdata", "github"}
ENDPOINT_REF_TYPES = {"release", "default_branch"}
RESOLUTION_METHODS = {
    "github_rest_api",
    "github_web_git_refs",
    "official_github_release_html_manual_verification",
    "github_rest_api_as_of",
}
RESOLUTION_MODES = {"live", "retrospective_as_of"}
DELTA_TYPES = {"added", "removed", "changed", "unchanged"}
COMPARISON_STATUSES = {"comparable", "incomplete_observation"}
DEPENDENCY_PARSE_STATUSES = {
    "parsed", "parsed_non_pep508", "parsed_poetry", "parsed_pipfile", "parsed_url", "directive", "unparsed",
}
FIELD_OBSERVATION_STATUSES = {
    "extracted", "extracted_partial", "confirmed_absent", "dynamic_metadata", "unsupported_syntax", "parse_failure",
}
SNAPSHOT_STATUS_VALUES = {
    "archive_listing_status": {"complete"},
    "dependency_observation_status": {"complete", "incomplete", "no_declaration_file"},
    "python_constraint_observation_status": {"complete", "incomplete"},
    "build_backend_observation_status": {"complete", "incomplete"},
    "source_line_observation_status": {"complete", "incomplete"},
    "api_observation_status": {"complete", "incomplete"},
}
# Specification v0.3 Section 6, restated here so that the released statuses are re-derived independently.
GOVERNING_STATUS = {
    ("dependency", "declared_dependency"): "dependency_observation_status",
    ("api", "syntactic_symbol"): "api_observation_status",
    ("structure", "packaging_files"): "archive_listing_status",
    ("structure", "build_backends"): "build_backend_observation_status",
    ("structure", "python_constraints"): "python_constraint_observation_status",
    ("structure", "ci_providers"): "archive_listing_status",
}
SCALAR_GOVERNING_STATUS = {"source_python_line_count": "source_line_observation_status"}
SYMBOL_KINDS = {"class", "constructor", "method", "function", "async_function"}
EXPORT_EVIDENCE = {"literal_all", "lexical_nonprivate", "class_member"}
ABSOLUTE_PATH = re.compile(r"(?:^|[\s'\"])(?:[A-Za-z]:[\\/]|/(?:home|Users)/[^/\s]+/)")
ELIGIBILITY_POLICY_VERSION = "archive-eligibility-policy-v0.2"
# These statuses are intentional observations from bounded static parsing. They
# remain visible in validation_records.csv but do not invalidate the dataset.
EXPECTED_STATIC_PARSE_ERROR_CHECKS = frozenset({"python_ast_parse", "api_source_read"})
CODE_LITERAL_FIELDS = {
    "api_symbol_records.csv": frozenset({"signature", "decorators"}),
    "pair_delta_records.csv": frozenset({"publication_value", "endpoint_value"}),
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_utc(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def safe_relative_evidence(value: str) -> bool:
    if not value or ABSOLUTE_PATH.search(value):
        return not value
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized.split(":", 1)[0])
    return not path.is_absolute() and ".." not in path.parts


def canonical_github_url(value: str, full_name: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == "github.com"
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
        and len(parts) == 2
        and "/".join(parts) == full_name
    )


def resolve_local(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def duplicates(rows: list[dict[str, str]], field: str) -> bool:
    values = [row.get(field, "") for row in rows]
    return bool(values) and (not all(values) or len(values) != len(set(values)))


def validate(
    *,
    candidates_path: Path,
    eligibility_path: Path,
    inventory_path: Path,
    inspection_path: Path,
    publication_cache: Path,
    endpoint_cache: Path,
    endpoint_path: Path,
    endpoint_manifest_path: Path,
    tables: Path,
    linkage_path: Path | None = None,
    strict: bool = False,
    verify_archives: bool = True,
) -> dict[str, Any]:
    def rooted(path: Path) -> Path:
        return path.resolve() if path.is_absolute() else (ROOT / path).resolve()

    candidates_path = rooted(candidates_path)
    eligibility_path = rooted(eligibility_path)
    inventory_path = rooted(inventory_path)
    inspection_path = rooted(inspection_path)
    publication_cache = rooted(publication_cache)
    endpoint_cache = rooted(endpoint_cache)
    endpoint_path = rooted(endpoint_path)
    endpoint_manifest_path = rooted(endpoint_manifest_path)
    tables = rooted(tables)
    linkage_path = rooted(linkage_path) if linkage_path else None
    failures: list[str] = []
    warnings: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    candidates = read_csv(candidates_path)
    eligibility = read_csv(eligibility_path)
    inventory = read_csv(inventory_path)
    inspections = read_csv(inspection_path)
    endpoints = read_csv(endpoint_path)
    endpoint_manifest = json.loads(endpoint_manifest_path.read_text(encoding="utf-8"))
    manifest_path = tables / "extraction_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    table_rows = {name: read_csv(tables / name) for name in TABLE_NAMES}

    eligible_rows = [row for row in eligibility if row.get("final_eligibility") == "eligible_archive"]
    eligible_dois = {row["scholarly_doi"] for row in eligible_rows}
    eligible_ranks = {row["rank"] for row in eligible_rows}
    candidate_by_doi = {row["doi"]: row for row in candidates}
    endpoints_by_doi = {row["scholarly_doi"]: row for row in endpoints}
    endpoint_dois = set(endpoints_by_doi)
    resolved = [row for row in endpoints if row.get("resolution_status") == "resolved"]
    resolved_dois = {row["scholarly_doi"] for row in resolved}
    unresolved = [row for row in endpoints if row.get("resolution_status") != "resolved"]

    check(not duplicates(candidates, "doi"), "duplicate_candidate_doi")
    check(not duplicates(candidates, "rank"), "duplicate_candidate_rank")
    check(not duplicates(eligibility, "scholarly_doi"), "duplicate_eligibility_doi")
    check(not duplicates(eligibility, "rank"), "duplicate_eligibility_rank")
    check(not duplicates(endpoints, "scholarly_doi"), "duplicate_endpoint_doi")
    check(not duplicates(endpoints, "selection_rank"), "duplicate_endpoint_rank")
    check(all("endpoint_local_cache" not in row for row in endpoints), "endpoint_local_cache_field_leaked")
    check(all(not ABSOLUTE_PATH.search(value or "") for row in endpoints for value in row.values()), "endpoint_absolute_local_path_leaked")
    check(endpoint_dois == eligible_dois, "endpoint_eligible_doi_set_mismatch")
    check(eligible_dois <= set(candidate_by_doi), "eligible_candidate_doi_set_mismatch")
    check(all(row.get("rank") == candidate_by_doi.get(row["scholarly_doi"], {}).get("rank") for row in eligible_rows), "eligible_candidate_rank_mismatch")
    check({row.get("selection_rank") for row in endpoints} == eligible_ranks, "endpoint_eligible_rank_set_mismatch")
    check(all(row.get("selected_role") == "census_provisional" for row in eligible_rows + endpoints), "invalid_census_selected_role")
    check(all(row.get("decision_basis") == ELIGIBILITY_POLICY_VERSION for row in eligibility), "eligibility_policy_mismatch")
    check(all(row.get("third_party_code_executed") == "false" for row in eligibility + endpoints), "endpoint_or_eligibility_execution_flag")
    check(all(row.get("third_party_code_executed") == "false" for row in inspections), "inspection_execution_flag")
    check(all(row.get("resolution_status") in {"resolved", "unresolved"} for row in endpoints), "invalid_resolution_status")
    check(all(not row.get("resolution_error") for row in resolved), "resolved_endpoint_has_error")
    check(all(bool(row.get("resolution_error")) for row in unresolved), "unresolved_endpoint_missing_error")
    if unresolved:
        warnings.append(f"endpoint_unresolved:{len(unresolved)}")
    check(resolved_dois <= eligible_dois, "resolved_endpoint_not_eligible")
    check(eligible_dois - resolved_dois == {row["scholarly_doi"] for row in unresolved}, "endpoint_unresolved_set_mismatch")

    for row in resolved:
        doi = row["scholarly_doi"]
        check(row.get("project_id") == project_id(doi), f"endpoint_project_id_mismatch:{doi}")
        check(row.get("selection_rank") == candidate_by_doi.get(doi, {}).get("rank"), f"endpoint_candidate_rank_mismatch:{doi}")
        check(canonical_github_url(row.get("resolved_repository_url", ""), row.get("resolved_full_name", "")), f"endpoint_repository_not_canonical_https:{doi}")
        check(bool(re.fullmatch(r"[0-9a-f]{40}", row.get("endpoint_commit_sha", ""))), f"invalid_endpoint_commit_sha:{doi}")
        check(row.get("endpoint_ref_type") in ENDPOINT_REF_TYPES, f"invalid_endpoint_ref_type:{doi}")
        check(row.get("resolution_method") in RESOLUTION_METHODS, f"invalid_resolution_method:{doi}")
        mode = row.get("resolution_mode", "")
        check(mode in RESOLUTION_MODES, f"invalid_resolution_mode:{doi}")
        if mode == "retrospective_as_of":
            as_of = parse_utc(row.get("endpoint_as_of_utc", ""))
            check(as_of is not None, f"retrospective_endpoint_missing_as_of:{doi}")
            check(row.get("resolution_method") == "github_rest_api_as_of", f"retrospective_endpoint_method_mismatch:{doi}")
            published = parse_utc(row.get("endpoint_release_published_at", ""))
            if row.get("endpoint_ref_type") == "release":
                check(published is not None and as_of is not None and published <= as_of, f"retrospective_release_after_as_of:{doi}")
        elif mode == "live":
            check(not row.get("endpoint_as_of_utc"), f"live_endpoint_has_as_of:{doi}")
            published = parse_utc(row.get("endpoint_release_published_at", ""))
            if published is not None:
                resolved_time = parse_utc(row.get("resolved_at_utc", ""))
                check(resolved_time is not None and published <= resolved_time, f"live_release_published_after_resolution:{doi}")
        check(row.get("endpoint_policy") == "latest_stable_release_else_default_branch_head", f"invalid_endpoint_policy:{doi}")
        check(bool(row.get("endpoint_ref_name")), f"missing_endpoint_ref_name:{doi}")
        check(row.get("endpoint_archive_url", "").startswith("https://codeload.github.com/"), f"invalid_endpoint_archive_url:{doi}")
        check(bool(re.fullmatch(r"[0-9a-f]{64}", row.get("endpoint_archive_sha256", ""))), f"invalid_endpoint_archive_sha256:{doi}")
        try:
            expected_size = int(row.get("endpoint_archive_bytes", ""))
        except ValueError:
            expected_size = -1
        check(expected_size > 0, f"invalid_endpoint_archive_size:{doi}")
        archive = endpoint_cache / f"{row['project_id']}_{row['endpoint_commit_sha']}.tar.gz"
        check(archive.is_file(), f"endpoint_archive_missing:{doi}")
        if archive.is_file():
            check(archive.stat().st_size == expected_size, f"endpoint_archive_size_mismatch:{doi}")
            if verify_archives:
                check(sha256_file(archive) == row.get("endpoint_archive_sha256"), f"endpoint_archive_hash_mismatch:{doi}")

    created = parse_utc(str(endpoint_manifest.get("created_at_utc", "")))
    try:
        snapshot_date = date.fromisoformat(str(endpoint_manifest.get("endpoint_snapshot_date", "")))
    except ValueError:
        snapshot_date = None
    check(endpoint_manifest.get("schema_version") == "0.2", "endpoint_manifest_schema_mismatch")
    check(endpoint_manifest.get("endpoint_policy_version") == ENDPOINT_POLICY_VERSION, "endpoint_manifest_policy_mismatch")
    check(created is not None and created <= datetime.now(timezone.utc), "endpoint_manifest_created_at_invalid")
    check(snapshot_date is not None and created is not None and snapshot_date == created.date(), "endpoint_manifest_snapshot_date_mismatch")
    check(endpoint_manifest.get("projects_attempted") == len(endpoints), "endpoint_manifest_attempted_count_mismatch")
    check(endpoint_manifest.get("projects_resolved") == len(resolved), "endpoint_manifest_resolved_count_mismatch")
    check(endpoint_manifest.get("stable_release_endpoints") == sum(row.get("endpoint_ref_type") == "release" for row in resolved), "endpoint_manifest_release_count_mismatch")
    check(endpoint_manifest.get("default_branch_endpoints") == sum(row.get("endpoint_ref_type") == "default_branch" for row in resolved), "endpoint_manifest_branch_count_mismatch")
    method_counts = dict(sorted(Counter(row.get("resolution_method") for row in resolved).items()))
    check(endpoint_manifest.get("resolution_methods") == method_counts, "endpoint_manifest_method_counts_mismatch")
    check(endpoint_manifest.get("third_party_code_executed") is False, "endpoint_manifest_execution_flag")
    check(endpoint_manifest.get("selection_role") == "census_provisional", "endpoint_manifest_selection_role_mismatch")
    check(endpoint_manifest.get("endpoint_resolution_sha256") == sha256_file(endpoint_path), "endpoint_manifest_resolution_hash_mismatch")
    mode_counts = dict(sorted(Counter(row.get("resolution_mode", "") for row in endpoints).items()))
    check(endpoint_manifest.get("resolution_modes") == mode_counts, "endpoint_manifest_mode_counts_mismatch")
    expected_endpoint_inputs = {
        "candidates": sha256_file(candidates_path),
        "publication_inspection": sha256_file(inspection_path),
        "eligibility_ledger": sha256_file(eligibility_path),
    }
    check(endpoint_manifest.get("input_sha256") == expected_endpoint_inputs, "endpoint_manifest_input_hash_mismatch")

    selected_inventory = {
        (row["scholarly_doi"], row["file_key"]): row
        for row in inventory if row.get("selected_source_archive") == "true" and row["scholarly_doi"] in eligible_dois
    }
    inspection_by_key = {(row["scholarly_doi"], row["file_key"]): row for row in inspections}
    for doi in sorted(eligible_dois):
        matching = [(key, row) for key, row in selected_inventory.items() if key[0] == doi]
        check(len(matching) == 1, f"eligible_publication_archive_not_unique:{doi}")
        if len(matching) != 1:
            continue
        key, inventory_row = matching[0]
        inspection = inspection_by_key.get(key)
        check(inspection is not None, f"eligible_publication_inspection_missing:{doi}")
        if inspection is None:
            continue
        check(inspection.get("file_size_bytes") == inventory_row.get("file_size_bytes"), f"publication_inventory_inspection_size_mismatch:{doi}")
        check(inspection.get("repository_checksum") == inventory_row.get("repository_checksum"), f"publication_inventory_inspection_checksum_mismatch:{doi}")
        check(inspection.get("inspection_status") == "inspected", f"eligible_publication_not_inspected:{doi}")
        check(inspection.get("suspicious_member_path") == "false", f"eligible_publication_path_unsafe:{doi}")
        archive = publication_cache / safe_cache_name(inventory_row["archive_record_id"], inventory_row["file_key"])
        try:
            expected_size = int(inventory_row.get("file_size_bytes", ""))
        except ValueError:
            expected_size = -1
        check(archive.is_file(), f"publication_archive_missing:{doi}")
        if archive.is_file():
            check(archive.stat().st_size == expected_size, f"publication_archive_size_mismatch:{doi}")
            if verify_archives:
                check(sha256_file(archive) == inspection.get("local_sha256"), f"publication_archive_hash_mismatch:{doi}")

    linkage = {row["scholarly_doi"]: row for row in read_csv(linkage_path)} if linkage_path else {}
    projects = table_rows["project_records.csv"]
    snapshots = table_rows["snapshot_records.csv"]
    structures = table_rows["structure_records.csv"]
    dependencies = table_rows["dependency_records.csv"]
    declarations = table_rows["declaration_source_records.csv"]
    symbols = table_rows["api_symbol_records.csv"]
    deltas = table_rows["pair_delta_records.csv"]
    validations = table_rows["validation_records.csv"]
    project_ids = {row["project_id"] for row in projects}
    project_dois = {row["scholarly_doi"] for row in projects}
    pair_ids = {row["pair_id"] for row in projects}
    snapshot_ids = {row["snapshot_id"] for row in snapshots}
    snapshot_by_id = {row["snapshot_id"]: row for row in snapshots}
    project_by_id = {row["project_id"]: row for row in projects}

    for field in ("project_id", "pair_id", "scholarly_doi", "selection_rank"):
        check(not duplicates(projects, field), f"duplicate_project_{field}")
    check(not duplicates(snapshots, "snapshot_id"), "duplicate_snapshot_id")
    check(project_dois == resolved_dois, "extraction_endpoint_doi_set_mismatch")
    if project_dois != eligible_dois:
        warnings.append(f"extraction_incomplete:{len(project_dois)}/{len(eligible_dois)}")
    check(all(row.get("selected_role") == "census_provisional" for row in projects), "invalid_project_selected_role")
    check(all(row.get("endpoint_ref_type") in ENDPOINT_REF_TYPES for row in projects), "invalid_project_endpoint_ref_type")
    check(all(bool(re.fullmatch(r"[0-9a-f]{40}", row.get("endpoint_commit_sha", ""))) for row in projects), "invalid_project_endpoint_sha")

    snapshots_by_project: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in snapshots:
        snapshots_by_project[row["project_id"]].append(row)
    check(set(snapshots_by_project) == project_ids, "snapshot_project_set_mismatch")
    check(all(len(rows) == 2 and {row["snapshot_role"] for row in rows} == {"publication", "endpoint"} for rows in snapshots_by_project.values()), "project_snapshot_pair_invalid")
    check(all(row.get("pair_id") in pair_ids and project_by_id.get(row.get("project_id"), {}).get("pair_id") == row.get("pair_id") for row in snapshots), "snapshot_pair_reference_invalid")
    check(all(row.get("source_provider") in PROVIDERS for row in snapshots), "invalid_snapshot_provider")
    check(all(row.get("source_provider") == "github" for row in snapshots if row.get("snapshot_role") == "endpoint"), "endpoint_snapshot_provider_invalid")
    check(all(row.get("source_provider") in PROVIDERS for row in snapshots if row.get("snapshot_role") == "publication"), "publication_snapshot_provider_invalid")
    check(all(row.get("third_party_code_executed") == "false" for row in snapshots + structures), "extraction_execution_flag")
    check(all(bool(re.fullmatch(r"[0-9a-f]{64}", row.get("archive_sha256", ""))) for row in snapshots + structures), "invalid_extraction_archive_sha256")
    check(all(row.get("parser_version") == PARSER_VERSION for row in snapshots + structures), "parser_version_mismatch")

    for project in projects:
        pid = project["project_id"]
        check(project.get("publication_snapshot_id") in snapshot_ids, f"missing_project_publication_snapshot:{pid}")
        check(project.get("endpoint_snapshot_id") in snapshot_ids, f"missing_project_endpoint_snapshot:{pid}")
        endpoint = endpoints_by_doi.get(project["scholarly_doi"], {})
        check(project.get("resolved_repository_url") in {None, "", endpoint.get("resolved_repository_url")}, f"project_repository_mismatch:{pid}")
        check(project.get("endpoint_ref_type") == endpoint.get("endpoint_ref_type"), f"project_endpoint_ref_type_mismatch:{pid}")
        check(project.get("endpoint_ref_name") in {None, "", endpoint.get("endpoint_ref_name")}, f"project_endpoint_ref_name_mismatch:{pid}")
        check(project.get("endpoint_commit_sha") == endpoint.get("endpoint_commit_sha"), f"project_endpoint_commit_mismatch:{pid}")
        endpoint_snapshot = snapshot_by_id.get(project.get("endpoint_snapshot_id", ""), {})
        check(endpoint_snapshot.get("archive_sha256") == endpoint.get("endpoint_archive_sha256"), f"endpoint_snapshot_hash_mismatch:{pid}")
        check(endpoint_snapshot.get("archive_bytes") == endpoint.get("endpoint_archive_bytes"), f"endpoint_snapshot_size_mismatch:{pid}")
        check(endpoint_snapshot.get("commit_sha") == endpoint.get("endpoint_commit_sha"), f"endpoint_snapshot_commit_mismatch:{pid}")
        check(endpoint_snapshot.get("source_identifier") == endpoint.get("resolved_full_name"), f"endpoint_snapshot_identifier_mismatch:{pid}")
        check(endpoint_snapshot.get("source_ref") == endpoint.get("endpoint_ref_name"), f"endpoint_snapshot_ref_mismatch:{pid}")
        check(endpoint_snapshot.get("source_url") == endpoint.get("endpoint_archive_url"), f"endpoint_snapshot_url_mismatch:{pid}")
        check(project.get("endpoint_resolution_mode") == endpoint.get("resolution_mode"), f"project_resolution_mode_mismatch:{pid}")
        check(project.get("endpoint_release_published_at") == endpoint.get("endpoint_release_published_at"), f"project_release_date_mismatch:{pid}")
        expected_ref_date = (
            project.get("endpoint_release_published_at") if project.get("endpoint_ref_type") == "release"
            else project.get("endpoint_commit_time_utc")
        )
        check(project.get("endpoint_ref_date") == expected_ref_date, f"project_endpoint_ref_date_rule:{pid}")
        check(project.get("publication_year") == project.get("publication_date", "")[:4], f"project_publication_year_mismatch:{pid}")
        for days_field, start_field, end_field in (
            ("archive_to_article_days", "publication_archive_date", "publication_date"),
            ("article_to_endpoint_ref_days", "publication_date", "endpoint_ref_date"),
            ("article_to_endpoint_as_of_days", "publication_date", "endpoint_as_of_date"),
        ):
            start, end = parse_day(project.get(start_field, "")), parse_day(project.get(end_field, ""))
            expected_days = str((end - start).days) if start and end else ""
            check(project.get(days_field) == expected_days, f"project_interval_mismatch:{days_field}:{pid}")
        if project.get("archive_to_article_days"):
            check(int(project["archive_to_article_days"]) >= 0, f"archive_later_than_article:{pid}")
        if linkage_path is not None:
            check(project.get("publication_archive_date") == linkage.get(project["scholarly_doi"], {}).get("archive_publication_date"),
                  f"project_archive_date_mismatch:{pid}")
        publication_snapshot = snapshot_by_id.get(project.get("publication_snapshot_id", ""), {})
        matching = [row for (doi, _), row in selected_inventory.items() if doi == project["scholarly_doi"]]
        if len(matching) == 1:
            inventory_row = matching[0]
            inspection = inspection_by_key.get((project["scholarly_doi"], inventory_row["file_key"]), {})
            check(publication_snapshot.get("source_provider") == inventory_row.get("provider"), f"publication_snapshot_provider_mismatch:{pid}")
            check(publication_snapshot.get("source_identifier") == inventory_row.get("archive_doi_resolved"), f"publication_snapshot_identifier_mismatch:{pid}")
            check(publication_snapshot.get("source_ref") == inventory_row.get("file_key"), f"publication_snapshot_ref_mismatch:{pid}")
            check(publication_snapshot.get("archive_sha256") == inspection.get("local_sha256"), f"publication_snapshot_hash_mismatch:{pid}")
            check(publication_snapshot.get("archive_bytes") == inventory_row.get("file_size_bytes"), f"publication_snapshot_size_mismatch:{pid}")

    check(len(structures) == len(snapshots) and {row["snapshot_id"] for row in structures} == snapshot_ids, "structure_snapshot_set_mismatch")
    check(all(row.get("archive_sha256") == snapshot_by_id.get(row.get("snapshot_id", ""), {}).get("archive_sha256") for row in structures), "structure_snapshot_hash_mismatch")
    check(all(not row.get("archive_bytes") or row.get("archive_bytes") == snapshot_by_id.get(row.get("snapshot_id", ""), {}).get("archive_bytes") for row in structures), "structure_snapshot_size_mismatch")
    for name, rows in table_rows.items():
        if name == "project_records.csv":
            continue
        check(all(row.get("project_id") in project_ids for row in rows), f"orphan_project_reference:{name}")
    for name in ("structure_records.csv", "dependency_records.csv", "declaration_source_records.csv", "api_symbol_records.csv", "validation_records.csv"):
        check(all(row.get("snapshot_id") in snapshot_ids for row in table_rows[name]), f"orphan_snapshot_reference:{name}")
        check(all(snapshot_by_id.get(row.get("snapshot_id", ""), {}).get("snapshot_role") == row.get("snapshot_role") for row in table_rows[name]), f"snapshot_role_reference_mismatch:{name}")
    check(all(row.get("pair_id") in pair_ids for row in deltas), "orphan_delta_pair_reference")
    check(all(project_by_id.get(row.get("project_id", ""), {}).get("pair_id") == row.get("pair_id") for row in deltas), "delta_project_pair_mismatch")
    check(len({(row["pair_id"], row["family"], row["feature_group"], row["feature_key"]) for row in deltas}) == len(deltas), "duplicate_delta_key")
    check(len({(row["snapshot_id"], row["symbol_key"]) for row in symbols}) == len(symbols), "duplicate_symbol_key")
    check(all(row.get("parse_status") in DEPENDENCY_PARSE_STATUSES for row in dependencies), "invalid_dependency_parse_status")
    check(all(row.get("symbol_kind") in SYMBOL_KINDS for row in symbols), "invalid_symbol_kind")
    check(all(row.get("export_evidence") in EXPORT_EVIDENCE for row in symbols), "invalid_export_evidence")
    check(all(row.get("family") in {"api", "dependency", "structure"} for row in deltas), "invalid_delta_family")
    check(all(row.get("delta_type") in DELTA_TYPES for row in deltas), "invalid_delta_type")
    check(all(row.get("comparison_status") in COMPARISON_STATUSES for row in deltas), "invalid_comparison_status")
    check(all(row.get("observation_status") in FIELD_OBSERVATION_STATUSES for row in declarations), "invalid_field_observation_status")
    check(all(row.get("field_kind") in {"dependency", "python_constraint", "file", "setup_kwargs"} for row in declarations), "invalid_field_kind")
    for field, allowed in SNAPSHOT_STATUS_VALUES.items():
        check(all(row.get(field) in allowed for row in structures), f"invalid_snapshot_status:{field}")
    check(all(bool(re.fullmatch(r"[0-9a-f]{64}", row.get("content_tree_sha256", ""))) for row in snapshots), "invalid_content_tree_sha256")
    # A snapshot's dependency status must follow from its field rows (specification Section 5).
    field_rows_by_snapshot: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in declarations:
        field_rows_by_snapshot[row["snapshot_id"]].append(row)
    for row in structures:
        rows_for_snapshot = field_rows_by_snapshot.get(row["snapshot_id"], [])
        packaging = json.loads(row.get("packaging_files") or "[]")
        if not packaging:
            expected_status = "no_declaration_file"
        elif any(item["field_kind"] in {"dependency", "file", "setup_kwargs"}
                 and item["observation_status"] not in {"extracted", "confirmed_absent"} for item in rows_for_snapshot):
            expected_status = "incomplete"
        else:
            expected_status = "complete"
        check(row.get("dependency_observation_status") == expected_status, f"dependency_status_not_derivable:{row['snapshot_id']}")
        check(int(row.get("declared_dependency_record_count") or -1) == sum(int(item["entry_count"]) for item in rows_for_snapshot),
              f"dependency_count_field_rows_mismatch:{row['snapshot_id']}")
    structure_by_pair_role = {
        (snapshot_by_id[row["snapshot_id"]]["pair_id"], row["snapshot_role"]): row
        for row in structures if row["snapshot_id"] in snapshot_by_id
    }
    status_mismatches = 0
    for row in deltas:
        if row["family"] == "structure" and row["feature_group"] == "scalar":
            governing = SCALAR_GOVERNING_STATUS.get(row["feature_key"], "archive_listing_status")
        else:
            governing = GOVERNING_STATUS.get((row["family"], row["feature_group"]), "")
        sides = [structure_by_pair_role.get((row["pair_id"], role), {}).get(governing, "") for role in ("publication", "endpoint")]
        expected = "comparable" if all(value in {"complete", "no_declaration_file"} for value in sides) else "incomplete_observation"
        status_mismatches += row["comparison_status"] != expected
    check(status_mismatches == 0, f"delta_comparison_status_not_derivable:{status_mismatches}")
    check(all(row.get("status") in {"pass", "warning", "error"} for row in validations), "invalid_validation_status")
    unexpected_validation_errors = [
        row for row in validations
        if row.get("status") == "error"
        and row.get("check_name") not in EXPECTED_STATIC_PARSE_ERROR_CHECKS
    ]
    check(not unexpected_validation_errors, "validation_error_present")
    if len(unexpected_validation_errors) < sum(row.get("status") == "error" for row in validations):
        warnings.append(
            "expected_static_parse_errors:"
            + str(sum(row.get("status") == "error" for row in validations) - len(unexpected_validation_errors))
        )
    validation_keys = {(row.get("snapshot_id"), row.get("check_name")) for row in validations}
    check(all((sid, "archive_paths") in validation_keys for sid in snapshot_ids), "archive_path_validation_missing")
    check(all((sid, "archive_sha256") in validation_keys for sid in snapshot_ids), "archive_hash_validation_missing")

    for name, rows in table_rows.items():
        check("local_archive_path" not in (rows[0] if rows else {}), f"local_path_field_leaked:{name}")
        allowed_literal_fields = CODE_LITERAL_FIELDS.get(name, frozenset())
        check(
            all(
                not ABSOLUTE_PATH.search(value or "")
                for row in rows
                for field, value in row.items()
                if field not in allowed_literal_fields
            ),
            f"absolute_local_path_leaked:{name}",
        )
    check(all(safe_relative_evidence(row.get("source_file", "")) for row in dependencies + symbols), "unsafe_source_evidence_path")

    expected_counts = {
        "projects": len(projects),
        "snapshots": len(snapshots),
        "structure_records": len(structures),
        "dependency_records": len(dependencies),
        "declaration_source_records": len(declarations),
        "api_symbol_records": len(symbols),
        "pair_delta_records": len(deltas),
        "validation_records": len(validations),
    }
    check(manifest.get("schema_version") == SCHEMA_VERSION, "extraction_manifest_schema_mismatch")
    check(manifest.get("parser_version") == PARSER_VERSION, "extraction_manifest_parser_mismatch")
    check(manifest.get("tables") == list(TABLE_NAMES), "extraction_manifest_tables_mismatch")
    check(all(manifest.get(key) == value for key, value in expected_counts.items()), "extraction_manifest_count_mismatch")
    check(manifest.get("third_party_code_executed") is False, "extraction_manifest_execution_flag")
    table_hashes = {name: sha256_file(tables / name) for name in TABLE_NAMES}
    check(manifest.get("table_sha256") == table_hashes, "extraction_manifest_table_hash_mismatch")
    expected_extraction_inputs = {
        "candidates": sha256_file(candidates_path),
        "endpoint_resolution": sha256_file(endpoint_path),
        "publication_inspection": sha256_file(inspection_path),
        "publication_inventory": sha256_file(inventory_path),
    }
    if linkage_path is not None:
        expected_extraction_inputs["linkage_ledger"] = sha256_file(linkage_path)
    check(manifest.get("input_sha256") == expected_extraction_inputs, "extraction_manifest_input_hash_mismatch")

    input_paths = {
        candidates_path,
        eligibility_path,
        inventory_path,
        inspection_path,
        endpoint_path,
        endpoint_manifest_path,
        manifest_path,
        *(tables / name for name in TABLE_NAMES),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS" if not failures else "FAIL",
        "strict": strict,
        "archive_bytes_verified": verify_archives,
        "failures": failures,
        "warnings": warnings,
        "counts": {
            "eligible_projects": len(eligible_dois),
            "endpoint_records": len(endpoints),
            "resolved_endpoints": len(resolved),
            **expected_counts,
        },
        "input_sha256": {
            path.relative_to(ROOT).as_posix(): sha256_file(path)
            for path in sorted(input_paths, key=lambda item: item.as_posix())
            if path.is_file() and path.is_relative_to(ROOT)
        },
    }


def parse_day(value: str) -> date | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date() if value else None
    except ValueError:
        return None


def project_id(doi: str) -> str:
    suffix = doi.strip().lower().removeprefix("https://doi.org/")
    return "doi_" + "".join(ch if ch.isalnum() else "_" for ch in suffix).strip("_")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, default=Path("data/census_frame_v0.3/provisional_eligible.csv"))
    parser.add_argument("--eligibility-ledger", type=Path, default=Path("data/census_frame_v0.3/archive_eligibility_ledger.csv"))
    parser.add_argument("--publication-inventory", type=Path, default=Path("data/census_frame_v0.3/archive_file_inventory.csv"))
    parser.add_argument("--publication-inspection", type=Path, default=Path("data/census_frame_v0.3/archive_static_inspection.csv"))
    parser.add_argument("--publication-cache", type=Path, default=Path("data/raw/zenodo_archives_v0.2"))
    parser.add_argument("--endpoint-resolution", type=Path, default=Path("data/census_v0.3/endpoint_resolution.csv"))
    parser.add_argument("--endpoint-cache", type=Path, default=Path("data/raw/endpoint_archives_v0.2"))
    parser.add_argument("--endpoint-manifest", type=Path, default=Path("data/census_v0.3/endpoint_resolution_manifest.json"))
    parser.add_argument("--tables", type=Path, default=Path("data/census_v0.3/tables"))
    parser.add_argument("--linkage-ledger", type=Path, default=Path("data/census_frame_v0.3/archive_linkage_ledger.csv"))
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--skip-archive-hash", action="store_true")
    parser.add_argument("--rerun", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    kwargs = {
        "candidates_path": args.candidates,
        "eligibility_path": args.eligibility_ledger,
        "inventory_path": args.publication_inventory,
        "inspection_path": args.publication_inspection,
        "publication_cache": args.publication_cache,
        "endpoint_cache": args.endpoint_cache,
        "endpoint_path": args.endpoint_resolution,
        "endpoint_manifest_path": args.endpoint_manifest,
        "tables": args.tables,
        "linkage_path": args.linkage_ledger,
        "strict": args.strict,
        "verify_archives": not args.skip_archive_hash,
    }
    result = validate(**kwargs)
    result["deterministic_rerun_byte_identical"] = None
    if args.rerun and result["status"] == "PASS":
        with tempfile.TemporaryDirectory(prefix="census_extraction_validation_") as directory:
            rerun = Path(directory) / "tables"
            command = [
                sys.executable,
                str(ROOT / "scripts" / "extract_version_pairs.py"),
                "--candidates", str(args.candidates),
                "--publication-inspection", str(args.publication_inspection),
                "--publication-inventory", str(args.publication_inventory),
                "--linkage-ledger", str(args.linkage_ledger),
                "--publication-cache", str(args.publication_cache),
                "--endpoint-resolution", str(args.endpoint_resolution),
                "--endpoint-cache", str(args.endpoint_cache),
                "--output-dir", str(rerun),
            ]
            completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=7200)
            if completed.returncode != 0:
                result["failures"].append(f"deterministic_rerun_failed:{completed.returncode}")
            else:
                names = (*TABLE_NAMES, "extraction_manifest.json")
                canonical_tables = args.tables.resolve() if args.tables.is_absolute() else (ROOT / args.tables).resolve()
                mismatches = [name for name in names if sha256_file(canonical_tables / name) != sha256_file(rerun / name)]
                result["deterministic_rerun_byte_identical"] = not mismatches
                if mismatches:
                    result["failures"].append("deterministic_rerun_mismatch:" + ",".join(mismatches))
            result["status"] = "PASS" if not result["failures"] else "FAIL"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
