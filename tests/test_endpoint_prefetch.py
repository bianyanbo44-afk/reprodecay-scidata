from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from prefetch_endpoint_archives import endpoint_target, fetch, validate_url  # noqa: E402


class EndpointPrefetchTests(unittest.TestCase):
    def row(self, payload: bytes) -> dict[str, str]:
        return {
            "project_id": "doi_10_21105_joss_00001",
            "scholarly_doi": "10.21105/joss.00001",
            "selection_rank": "1",
            "endpoint_commit_sha": "a" * 40,
            "endpoint_archive_url": "https://codeload.github.com/o/r/tar.gz/" + "a" * 40,
            "endpoint_archive_bytes": str(len(payload)),
            "endpoint_archive_sha256": hashlib.sha256(payload).hexdigest(),
            "resolution_status": "resolved",
        }

    def test_expected_cache_filename(self) -> None:
        row = self.row(b"fixture")
        self.assertEqual(
            endpoint_target(Path("cache"), row).name,
            "doi_10_21105_joss_00001_" + "a" * 40 + ".tar.gz",
        )

    def test_existing_verified_archive_never_downloads(self) -> None:
        payload = b"endpoint fixture"
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            endpoint_target(cache, self.row(payload)).write_bytes(payload)
            with patch("prefetch_endpoint_archives.download_archive") as download:
                result = fetch(self.row(payload), cache, verify_only=False)
            self.assertEqual(result["status"], "verified_cached")
            download.assert_not_called()

    def test_download_is_verified_against_frozen_hash_and_size(self) -> None:
        payload = b"endpoint fixture"
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)

            def write_payload(_url: str, destination: Path) -> None:
                destination.write_bytes(payload)

            with patch("prefetch_endpoint_archives.download_archive", side_effect=write_payload):
                result = fetch(self.row(payload), cache, verify_only=False)
            self.assertEqual(result["status"], "downloaded_verified")

    def test_non_codeload_url_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid_endpoint_archive_url"):
            validate_url("https://example.org/archive.tar.gz")


if __name__ == "__main__":
    unittest.main()
