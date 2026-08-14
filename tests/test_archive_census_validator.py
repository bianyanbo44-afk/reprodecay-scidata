import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from validate_archive_census import validate  # noqa: E402


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class ArchiveCensusValidatorTests(unittest.TestCase):
    def fixture(self, directory, *, archive_license="MIT", inspection_status="inspected"):
        root = Path(directory)
        candidates = [{"doi": "10.21105/joss.1"}]
        links = [{
            "selected_role": "census_provisional", "scholarly_doi": "10.21105/joss.1",
            "archive_license": archive_license, "archive_access_right": "open",
            "archive_version": "1.0", "archive_file_count": "1",
            "source_archive_candidate_count": "1", "total_archive_size_bytes": "10",
            "selected_file_key": "project.zip", "selected_file_size_bytes": "10",
            "selected_repository_checksum": "md5:" + "0" * 32,
            "selected_download_url": "https://zenodo.org/project.zip",
            "publication_date_delta_days": "-1", "inventory_status": "resolved_source_archive",
            "third_party_code_executed": "false",
        }]
        files = [{
            "rank": "1", "selected_role": "census_provisional", "scholarly_doi": "10.21105/joss.1",
            "provider": "zenodo", "archive_doi_declared": "10.5281/zenodo.1",
            "archive_doi_resolved": "10.5281/zenodo.1", "archive_record_id": "1",
            "file_key": "project.zip", "file_size_bytes": "10", "repository_checksum": "md5:" + "0" * 32,
            "download_url": "https://zenodo.org/project.zip", "mimetype": "application/zip",
            "source_format_priority": "3", "source_name_penalty": "0", "selected_source_archive": "true",
        }]
        inspections = [{
            "scholarly_doi": "10.21105/joss.1", "file_key": "project.zip",
            "local_sha256": "0" * 64, "suspicious_member_path": "false",
            "inspection_error": "" if inspection_status == "inspected" else "download_timeout",
            "inspection_status": inspection_status, "third_party_code_executed": "false",
        }]
        for name, rows in (
            ("provisional_eligible.csv", candidates),
            ("archive_linkage_ledger.csv", links),
            ("archive_file_inventory.csv", files),
            ("archive_static_inspection.csv", inspections),
        ):
            write_csv(root / name, rows)
        manifest = {
            "run_state": "complete", "resolution_policy_version": "archive-resolution-v0.3.4",
            "candidate_sha256": "candidate", "crossref_sha256": "crossref",
            "candidate_records": 1, "projects_inventoried": 1, "projects_remaining": 0,
            "file_records": 1, "selected_source_archives": 1,
            "linkage_ledger_sha256": hashlib.sha256((root / "archive_linkage_ledger.csv").read_bytes()).hexdigest(),
            "file_inventory_sha256": hashlib.sha256((root / "archive_file_inventory.csv").read_bytes()).hexdigest(),
            "third_party_code_executed": False,
        }
        (root / "archive_inventory_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        checkpoint = {
            "resolution_policy_version": "archive-resolution-v0.3.4",
            "candidate_sha256": "candidate", "crossref_sha256": "crossref",
            "results": {"10.21105/joss.1": {}},
        }
        (root / "archive_inventory_checkpoint.json").write_text(json.dumps(checkpoint), encoding="utf-8")
        inspection_manifest = {
            "inspection_policy_version": "archive-static-inspection-v0.2.1",
            "complete": True,
            "inspection_sha256": hashlib.sha256((root / "archive_static_inspection.csv").read_bytes()).hexdigest(),
            "inventory_sha256": hashlib.sha256((root / "archive_file_inventory.csv").read_bytes()).hexdigest(),
        }
        (root / "archive_inspection_manifest.json").write_text(json.dumps(inspection_manifest), encoding="utf-8")
        return root

    def test_valid_archive_census_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            result = validate(self.fixture(directory), require_complete_inspection=True)
            self.assertEqual(result["status"], "PASS")

    def test_access_state_cannot_masquerade_as_license(self):
        with tempfile.TemporaryDirectory() as directory:
            result = validate(self.fixture(directory, archive_license="open"))
            self.assertIn("access_state_in_license_field", result["failures"])

    def test_incomplete_inspection_is_warning_until_freeze_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory, inspection_status="inspection_error")
            advisory = validate(root)
            blocking = validate(root, require_complete_inspection=True)
            self.assertEqual(advisory["status"], "PASS")
            self.assertEqual(blocking["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
