import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_archive_inventory_v02 import (  # noqa: E402
    ArchiveFileRecord,
    ArchiveLinkRecord,
    archive_reference,
    atomic_write_json,
    choose_figshare_version,
    license_text,
    normalize_doi,
    normalized_file,
    provider_for_doi,
    select_zenodo_version,
    select_source_file,
)


def file_record(key, priority, penalty="0"):
    return ArchiveFileRecord(
        rank="1", selected_role="census_provisional", scholarly_doi="10.1/a", provider="zenodo",
        archive_doi_declared="10.5281/zenodo.1", archive_doi_resolved="10.5281/zenodo.1",
        archive_record_id="1", file_key=key, file_size_bytes="10", repository_checksum="",
        download_url="https://example.invalid", mimetype="", source_format_priority=priority,
        source_name_penalty=penalty, selected_source_archive="false",
    )


class ArchiveInventoryTests(unittest.TestCase):
    def test_doi_normalization_removes_url_and_curly_quote(self):
        self.assertEqual(
            normalize_doi("http://dx.doi.org/10.5281/Zenodo.3635630\u201d"),
            "10.5281/zenodo.3635630",
        )

    def test_provider_detection_is_strict(self):
        self.assertEqual(provider_for_doi("10.6084/m9.figshare.1.v2"), "figshare")
        self.assertEqual(provider_for_doi("10.21105/joss.1"), "")

    def test_archive_reference_ignores_non_archive_dois(self):
        item = {
            "relation": {
                "has-preprint": [{"id-type": "doi", "id": "10.1101/123"}],
                "references": [{"id-type": "doi", "id": "10.5281/zenodo.55"}],
            }
        }
        self.assertEqual(archive_reference(item), ("references", "10.5281/zenodo.55"))

    def test_source_selection_prefers_tar_over_zip_and_docs(self):
        records = [
            file_record("project.zip", "3"),
            file_record("documentation.tar.gz", "0", "1"),
            file_record("project.tar.gz", "0"),
        ]
        self.assertEqual(select_source_file(records).file_key, "project.tar.gz")

    def test_source_selection_prefers_archive_version_match(self):
        records = [file_record("project-1.0.0.tar.gz", "0"), file_record("project-1.2.0.zip", "3")]
        self.assertEqual(select_source_file(records, "1.2.0").file_key, "project-1.2.0.zip")

    def test_source_zip_beats_documentation_tar(self):
        records = [file_record("documentation.tar.gz", "0", "1"), file_record("project.zip", "3")]
        self.assertEqual(select_source_file(records).file_key, "project.zip")

    def test_zenodo_version_selection_rejects_future_only_family(self):
        payload = {
            "id": 2,
            "doi": "10.5281/zenodo.3",
            "conceptdoi": "10.5281/zenodo.2",
            "links": {"versions": "https://example.invalid/versions"},
            "metadata": {"publication_date": "2021-01-01"},
        }
        import build_archive_inventory_v02 as module
        original = module.zenodo_versions
        module.zenodo_versions = lambda _: [payload]
        try:
            with self.assertRaisesRegex(LookupError, "no_zenodo_version"):
                select_zenodo_version(payload, "2020-01-01", "10.5281/zenodo.2")
        finally:
            module.zenodo_versions = original

    def test_exact_zenodo_version_is_not_replaced_by_family_latest(self):
        payload = {
            "id": 2,
            "doi": "10.5281/zenodo.2",
            "conceptdoi": "10.5281/zenodo.1",
            "metadata": {"publication_date": "2020-01-02"},
        }
        self.assertIs(select_zenodo_version(payload, "2020-01-03", "10.5281/zenodo.2"), payload)

    def test_figshare_version_selection_rejects_future_only_family(self):
        import build_archive_inventory_v02 as module
        original = module.request_json

        def fake_request(url):
            if url.endswith("/versions"):
                return [{"version": 1}]
            return {"published_date": "2021-01-01"}

        module.request_json = fake_request
        try:
            with self.assertRaisesRegex(LookupError, "no_figshare_version"):
                choose_figshare_version("1", "", "2020-01-01")
        finally:
            module.request_json = original

    def test_license_text_handles_provider_objects_and_flags(self):
        self.assertEqual(license_text([{"title": {"en": "gpl-3.0"}}]), "gpl-3.0")
        self.assertEqual(license_text(True), "")

    def test_normalized_file_carries_census_role(self):
        link = ArchiveLinkRecord(
            rank="1",
            selected_role="census_provisional",
            scholarly_doi="10.21105/joss.1",
            joss_title="Example",
            joss_publication_date="2020-01-01",
            repository_url="https://github.com/example/project",
            archive_provider="zenodo",
            archive_doi_declared="10.5281/zenodo.1",
            archive_doi_normalized="10.5281/zenodo.1",
            archive_record_id="1",
        )
        record = normalized_file(
            link,
            key="project.zip",
            size=10,
            checksum="md5:00000000000000000000000000000000",
            download_url="https://zenodo.org/file.zip",
        )
        self.assertEqual(record.selected_role, "census_provisional")

    def test_atomic_json_replaces_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            atomic_write_json(path, {"a": 1})
            atomic_write_json(path, {"a": 2})
            self.assertEqual(path.read_text(encoding="utf-8").strip().splitlines()[-2].strip(), '"a": 2')
            self.assertFalse(path.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
