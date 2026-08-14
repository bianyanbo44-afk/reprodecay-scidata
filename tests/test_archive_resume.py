import sys
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from inspect_archives import (  # noqa: E402
    download,
    inspect_zip,
    interesting,
    resolve_download_url,
    root_relative_basenames,
    role_matches,
    safe_cache_name,
    stable_error_code,
    validate_download_url,
)
import zipfile


class ArchiveResumeTests(unittest.TestCase):
    @patch("inspect_archives.time.sleep")
    @patch("inspect_archives.urllib.request.build_opener")
    def test_download_url_resolution_retries_transport_failure(self, build_opener, _sleep):
        response = MagicMock()
        response.__enter__.return_value.geturl.return_value = (
            "https://zenodo.org/records/1/files/a.zip"
        )
        opener = build_opener.return_value
        opener.open.side_effect = [urllib.error.URLError(ConnectionRefusedError()), response]

        resolved = resolve_download_url(
            "https://zenodo.org/records/1/files/a.zip", attempts=2
        )

        self.assertEqual(resolved, "https://zenodo.org/records/1/files/a.zip")
        self.assertEqual(opener.open.call_count, 2)

    def test_complete_partial_is_promoted_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "archive.zip"
            partial = destination.with_suffix(".zip.part")
            partial.write_bytes(b"complete")
            download("https://zenodo.org/never-requested", destination, len(b"complete"))
            self.assertEqual(destination.read_bytes(), b"complete")
            self.assertFalse(partial.exists())

    def test_oversized_partial_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "archive.zip"
            destination.with_suffix(".zip.part").write_bytes(b"too-large")
            with self.assertRaisesRegex(ValueError, "partial_file_larger_than_expected"):
                download("https://zenodo.org/never-requested", destination, 3)

    def test_selected_inventory_filter_is_supported_by_field_contract(self):
        self.assertTrue(interesting("project/setup.py"))

    def test_census_role_is_supported_by_field_contract(self):
        self.assertTrue(role_matches({"selected_role": "census_provisional"}, "all"))
        self.assertTrue(role_matches({"selected_role": "census_provisional"}, "census_provisional"))
        self.assertFalse(role_matches({"selected_role": "census_provisional"}, "primary"))

    def test_cache_name_is_windows_safe(self):
        self.assertNotIn(":", safe_cache_name("12608834:v1", "project.zip"))

    def test_zip_text_budget_is_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "archive.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("project/setup.py", "print('x')")
            names, texts, suspicious, count = inspect_zip(path)
            self.assertEqual(count, 1)
            self.assertIn("project/setup.py", texts)
            self.assertFalse(suspicious)

    def test_macos_metadata_does_not_hide_project_root(self):
        roots = root_relative_basenames([
            "project/setup.py",
            "project/LICENSE",
            "__MACOSX/._setup.py",
        ])
        self.assertEqual(roots, {"setup.py", "license"})

    def test_root_noise_does_not_hide_unique_project_directory(self):
        roots = root_relative_basenames([
            "project/setup.py",
            "project/LICENSE",
            ".DS_Store",
        ])
        self.assertEqual(roots, {"setup.py", "license"})

    def test_real_root_file_prevents_incorrect_directory_stripping(self):
        roots = root_relative_basenames(["setup.py", "src/package.py"])
        self.assertEqual(roots, {"setup.py"})

    def test_download_rejects_non_https_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "unsafe_download_url"):
                download("http://zenodo.org/file.zip", Path(directory) / "x.zip", 1)

    def test_download_rejects_unlisted_host_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "download_host_not_allowed"):
                download("https://127.0.0.1/file.zip", Path(directory) / "x.zip", 1)

    def test_redirect_host_allowlist_is_narrow(self):
        validate_download_url("https://bucket.s3.amazonaws.com/archive.zip", redirect=True)
        validate_download_url("https://caltech2.osn.mghpcc.org/archive.zip", redirect=True)
        with self.assertRaisesRegex(ValueError, "download_host_not_allowed"):
            validate_download_url("https://example.com/archive.zip", redirect=True)

    def test_timeout_error_is_release_safe(self):
        exc = subprocess.TimeoutExpired(
            ["curl", "https://signed.example/?X-Amz-Credential=secret"],
            timeout=1,
        )
        self.assertEqual(stable_error_code(exc), "download_timeout")


if __name__ == "__main__":
    unittest.main()
