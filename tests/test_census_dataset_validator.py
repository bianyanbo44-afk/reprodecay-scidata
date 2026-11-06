from __future__ import annotations

import csv
import hashlib
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from extract_version_pairs import PARSER_VERSION, SCHEMA_VERSION  # noqa: E402
from inspect_archives import safe_cache_name  # noqa: E402
from resolve_endpoint_snapshots import ENDPOINT_POLICY_VERSION  # noqa: E402
from validate_census_dataset import TABLE_NAMES, sha256_file, validate  # noqa: E402


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class CensusDatasetValidatorTests(unittest.TestCase):
    doi = "10.21105/joss.00001"
    pid = "doi_10_21105_joss_00001"
    pair = pid + "__pub__end"
    pub_sid = pid + "__publication__" + "a" * 16
    end_sid = pid + "__endpoint__" + "b" * 16

    def fixture(self, directory: str) -> dict[str, Path]:
        root = Path(directory)
        frame = root / "frame"
        census = root / "census"
        tables = census / "tables"
        pub_cache = root / "pub_cache"
        end_cache = root / "endpoint_cache"
        for path in (frame, tables, pub_cache, end_cache):
            path.mkdir(parents=True, exist_ok=True)

        publication_bytes = b"publication archive fixture"
        endpoint_bytes = b"endpoint archive fixture"
        pub_sha = hashlib.sha256(publication_bytes).hexdigest()
        end_sha = hashlib.sha256(endpoint_bytes).hexdigest()
        file_key = "source.zip"
        record_id = "123"
        publication_archive = pub_cache / safe_cache_name(record_id, file_key)
        endpoint_archive = end_cache / (self.pid + "_" + "c" * 40 + ".tar.gz")
        publication_archive.write_bytes(publication_bytes)
        endpoint_archive.write_bytes(endpoint_bytes)

        candidates = frame / "provisional_eligible.csv"
        write_csv(
            candidates,
            ["rank", "doi", "selected_role", "title", "publication_date", "joss_url", "repository_url"],
            [{
                "rank": "1", "doi": self.doi, "selected_role": "census_provisional", "title": "Fixture",
                "publication_date": "2020-01-01", "joss_url": "https://joss.theoj.org/papers/10.21105/joss.00001",
                "repository_url": "https://github.com/o/r",
            }],
        )
        eligibility = frame / "archive_eligibility_ledger.csv"
        write_csv(
            eligibility,
            ["rank", "selected_role", "scholarly_doi", "final_eligibility", "decision_basis", "third_party_code_executed"],
            [{"rank": "1", "selected_role": "census_provisional", "scholarly_doi": self.doi, "final_eligibility": "eligible_archive", "decision_basis": "archive-eligibility-policy-v0.2", "third_party_code_executed": "false"}],
        )
        inventory = frame / "archive_file_inventory.csv"
        write_csv(
            inventory,
            ["rank", "selected_role", "scholarly_doi", "provider", "archive_doi_resolved", "archive_record_id", "file_key", "file_size_bytes", "repository_checksum", "selected_source_archive"],
            [{
                "rank": "1", "selected_role": "census_provisional", "scholarly_doi": self.doi, "provider": "figshare",
                "archive_doi_resolved": "10.6084/m9.figshare.1", "archive_record_id": record_id, "file_key": file_key,
                "file_size_bytes": str(len(publication_bytes)), "repository_checksum": "md5:" + "1" * 32,
                "selected_source_archive": "true",
            }],
        )
        inspection = frame / "archive_static_inspection.csv"
        write_csv(
            inspection,
            ["selected_role", "rank", "scholarly_doi", "archive_doi", "file_key", "file_size_bytes", "repository_checksum", "local_sha256", "inspection_status", "suspicious_member_path", "third_party_code_executed"],
            [{
                "selected_role": "census_provisional", "rank": "1", "scholarly_doi": self.doi,
                "archive_doi": "10.6084/m9.figshare.1", "file_key": file_key, "file_size_bytes": str(len(publication_bytes)),
                "repository_checksum": "md5:" + "1" * 32, "local_sha256": pub_sha,
                "inspection_status": "inspected", "suspicious_member_path": "false", "third_party_code_executed": "false",
            }],
        )

        endpoint = census / "endpoint_resolution.csv"
        endpoint_fields = [
            "project_id", "scholarly_doi", "selection_rank", "selected_role", "resolved_repository_url",
            "resolved_full_name", "endpoint_policy", "endpoint_ref_type", "endpoint_ref_name", "endpoint_commit_sha",
            "endpoint_archive_url", "endpoint_archive_bytes", "endpoint_archive_sha256",
            "resolution_status", "resolution_error", "resolution_method", "endpoint_release_published_at",
            "resolution_mode", "endpoint_as_of_utc", "resolved_at_utc", "third_party_code_executed",
        ]
        write_csv(endpoint, endpoint_fields, [{
            "project_id": self.pid, "scholarly_doi": self.doi, "selection_rank": "1", "selected_role": "census_provisional",
            "resolved_repository_url": "https://github.com/o/r", "resolved_full_name": "o/r",
            "endpoint_policy": "latest_stable_release_else_default_branch_head", "endpoint_ref_type": "release",
            "endpoint_ref_name": "v1", "endpoint_commit_sha": "c" * 40,
            "endpoint_archive_url": "https://codeload.github.com/o/r/tar.gz/" + "c" * 40,
            "endpoint_archive_bytes": str(len(endpoint_bytes)), "endpoint_archive_sha256": end_sha,
            "resolution_status": "resolved", "resolution_error": "",
            "resolution_method": "github_rest_api", "endpoint_release_published_at": "2026-08-01T00:00:00Z",
            "resolution_mode": "live", "endpoint_as_of_utc": "", "resolved_at_utc": "2026-08-13T18:00:00+00:00",
            "third_party_code_executed": "false",
        }])
        endpoint_manifest = census / "endpoint_resolution_manifest.json"
        created_at = datetime.now(timezone.utc)
        endpoint_manifest.write_text(json.dumps({
            "schema_version": "0.2", "endpoint_policy_version": ENDPOINT_POLICY_VERSION,
            "created_at_utc": created_at.isoformat(), "endpoint_snapshot_date": created_at.date().isoformat(),
            "projects_attempted": 1, "projects_resolved": 1, "stable_release_endpoints": 1,
            "default_branch_endpoints": 0, "resolution_methods": {"github_rest_api": 1},
            "resolution_modes": {"live": 1},
            "third_party_code_executed": False, "selection_role": "census_provisional",
            "endpoint_resolution_sha256": sha256_file(endpoint),
            "input_sha256": {
                "candidates": sha256_file(candidates), "publication_inspection": sha256_file(inspection),
                "eligibility_ledger": sha256_file(eligibility),
            },
        }), encoding="utf-8")

        project_fields = [
            "project_id", "pair_id", "scholarly_doi", "selection_rank", "selected_role", "publication_snapshot_id",
            "endpoint_snapshot_id", "endpoint_ref_type", "endpoint_commit_sha", "publication_date", "publication_year",
            "publication_archive_date", "endpoint_as_of_date", "endpoint_resolution_mode", "endpoint_release_published_at",
            "endpoint_commit_time_utc", "endpoint_ref_date", "archive_to_article_days", "article_to_endpoint_ref_days",
            "article_to_endpoint_as_of_days",
        ]
        write_csv(tables / "project_records.csv", project_fields, [{
            "project_id": self.pid, "pair_id": self.pair, "scholarly_doi": self.doi, "selection_rank": "1",
            "selected_role": "census_provisional", "publication_snapshot_id": self.pub_sid,
            "endpoint_snapshot_id": self.end_sid, "endpoint_ref_type": "release", "endpoint_commit_sha": "c" * 40,
            "publication_date": "2020-01-01", "publication_year": "2020", "publication_archive_date": "2019-12-01",
            "endpoint_as_of_date": "2026-08-14", "endpoint_resolution_mode": "live",
            "endpoint_release_published_at": "2026-08-01T00:00:00Z", "endpoint_commit_time_utc": "",
            "endpoint_ref_date": "2026-08-01T00:00:00Z", "archive_to_article_days": "31",
            "article_to_endpoint_ref_days": str((date(2026, 8, 1) - date(2020, 1, 1)).days),
            "article_to_endpoint_as_of_days": str((date(2026, 8, 14) - date(2020, 1, 1)).days),
        }])
        snapshot_fields = [
            "snapshot_id", "project_id", "pair_id", "snapshot_role", "source_provider", "source_identifier",
            "source_ref", "commit_sha", "source_url", "archive_bytes", "archive_sha256", "upstream_checksum",
            "content_tree_sha256", "parser_version", "third_party_code_executed",
        ]
        write_csv(tables / "snapshot_records.csv", snapshot_fields, [
            {"snapshot_id": self.pub_sid, "project_id": self.pid, "pair_id": self.pair, "snapshot_role": "publication", "source_provider": "figshare", "source_identifier": "10.6084/m9.figshare.1", "source_ref": file_key, "commit_sha": "", "source_url": "https://doi.org/10.6084/m9.figshare.1", "archive_bytes": str(len(publication_bytes)), "archive_sha256": pub_sha, "upstream_checksum": "md5:" + "1" * 32, "content_tree_sha256": "a" * 64, "parser_version": PARSER_VERSION, "third_party_code_executed": "false"},
            {"snapshot_id": self.end_sid, "project_id": self.pid, "pair_id": self.pair, "snapshot_role": "endpoint", "source_provider": "github", "source_identifier": "o/r", "source_ref": "v1", "commit_sha": "c" * 40, "source_url": "https://codeload.github.com/o/r/tar.gz/" + "c" * 40, "archive_bytes": str(len(endpoint_bytes)), "archive_sha256": end_sha, "upstream_checksum": "", "content_tree_sha256": "b" * 64, "parser_version": PARSER_VERSION, "third_party_code_executed": "false"},
        ])
        statuses = {
            "packaging_files": "[]", "declared_dependency_record_count": "0",
            "archive_listing_status": "complete", "dependency_observation_status": "no_declaration_file",
            "python_constraint_observation_status": "complete", "build_backend_observation_status": "complete",
            "source_line_observation_status": "complete", "api_observation_status": "complete",
        }
        structure_fields = ["snapshot_id", "project_id", "snapshot_role", "archive_sha256", *statuses, "parser_version", "third_party_code_executed"]
        write_csv(tables / "structure_records.csv", structure_fields, [
            {"snapshot_id": self.pub_sid, "project_id": self.pid, "snapshot_role": "publication", "archive_sha256": pub_sha, **statuses, "parser_version": PARSER_VERSION, "third_party_code_executed": "false"},
            {"snapshot_id": self.end_sid, "project_id": self.pid, "snapshot_role": "endpoint", "archive_sha256": end_sha, **statuses, "parser_version": PARSER_VERSION, "third_party_code_executed": "false"},
        ])
        write_csv(tables / "declaration_source_records.csv", ["snapshot_id", "project_id", "snapshot_role", "source_file", "field", "field_kind", "observation_status", "entry_count"], [])
        write_csv(tables / "dependency_records.csv", ["snapshot_id", "project_id", "snapshot_role", "source_file", "parse_status"], [])
        write_csv(tables / "api_symbol_records.csv", ["snapshot_id", "project_id", "snapshot_role", "symbol_key", "source_file", "symbol_kind", "export_evidence"], [])
        write_csv(tables / "pair_delta_records.csv", ["pair_id", "project_id", "family", "feature_group", "feature_key", "delta_type", "comparison_status"], [])
        write_csv(tables / "validation_records.csv", ["snapshot_id", "project_id", "snapshot_role", "check_name", "status", "evidence", "detail"], [
            {"snapshot_id": self.pub_sid, "project_id": self.pid, "snapshot_role": "publication", "check_name": "archive_paths", "status": "pass", "evidence": "snapshot:" + self.pub_sid, "detail": "0 suspicious paths"},
            {"snapshot_id": self.pub_sid, "project_id": self.pid, "snapshot_role": "publication", "check_name": "archive_sha256", "status": "pass", "evidence": "snapshot:" + self.pub_sid, "detail": pub_sha},
            {"snapshot_id": self.end_sid, "project_id": self.pid, "snapshot_role": "endpoint", "check_name": "archive_paths", "status": "pass", "evidence": "snapshot:" + self.end_sid, "detail": "0 suspicious paths"},
            {"snapshot_id": self.end_sid, "project_id": self.pid, "snapshot_role": "endpoint", "check_name": "archive_sha256", "status": "pass", "evidence": "snapshot:" + self.end_sid, "detail": end_sha},
        ])
        table_hashes = {name: sha256_file(tables / name) for name in TABLE_NAMES}
        (tables / "extraction_manifest.json").write_text(json.dumps({
            "schema_version": SCHEMA_VERSION, "parser_version": PARSER_VERSION, "projects": 1, "snapshots": 2,
            "structure_records": 2,
            "dependency_records": 0, "declaration_source_records": 0, "api_symbol_records": 0, "pair_delta_records": 0,
            "validation_records": 4,
            "third_party_code_executed": False, "tables": list(TABLE_NAMES), "table_sha256": table_hashes,
            "input_sha256": {
                "candidates": sha256_file(candidates), "endpoint_resolution": sha256_file(endpoint),
                "publication_inspection": sha256_file(inspection), "publication_inventory": sha256_file(inventory),
            },
        }), encoding="utf-8")
        return {
            "candidates_path": candidates, "eligibility_path": eligibility, "inventory_path": inventory,
            "inspection_path": inspection, "publication_cache": pub_cache, "endpoint_cache": end_cache,
            "endpoint_path": endpoint,
            "endpoint_manifest_path": endpoint_manifest, "tables": tables,
        }

    def test_strict_complete_fixture_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = validate(**self.fixture(directory), strict=True, verify_archives=True)
            self.assertEqual(result["status"], "PASS", result["failures"])
            self.assertEqual(result["schema_version"], SCHEMA_VERSION)

    def test_endpoint_commit_and_archive_tampering_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self.fixture(directory)
            with paths["endpoint_path"].open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            rows[0]["endpoint_commit_sha"] = "short"
            write_csv(paths["endpoint_path"], list(rows[0]), rows)
            result = validate(**paths, strict=True, verify_archives=True)
            self.assertIn("invalid_endpoint_commit_sha:" + self.doi, result["failures"])
            self.assertIn("endpoint_manifest_resolution_hash_mismatch", result["failures"])

    def test_validation_error_and_local_path_leak_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self.fixture(directory)
            validation_path = paths["tables"] / "validation_records.csv"
            with validation_path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            rows[0]["status"] = "error"
            rows[0]["evidence"] = "C:" + "\\private\\archive.zip"
            write_csv(validation_path, list(rows[0]), rows)
            result = validate(**paths, strict=True, verify_archives=False)
            self.assertIn("validation_error_present", result["failures"])
            self.assertIn("absolute_local_path_leaked:validation_records.csv", result["failures"])
            self.assertIn("extraction_manifest_table_hash_mismatch", result["failures"])

    def test_unresolved_is_warning_and_project_attrition_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self.fixture(directory)
            with paths["endpoint_path"].open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            rows[0]["resolution_status"] = "unresolved"
            write_csv(paths["endpoint_path"], list(rows[0]), rows)
            non_strict = validate(**paths, strict=False, verify_archives=False)
            strict = validate(**paths, strict=True, verify_archives=False)
            self.assertIn("endpoint_unresolved:1", non_strict["warnings"])
            self.assertIn("endpoint_unresolved:1", strict["warnings"])
            self.assertIn("extraction_endpoint_doi_set_mismatch", strict["failures"])


if __name__ == "__main__":
    unittest.main()
