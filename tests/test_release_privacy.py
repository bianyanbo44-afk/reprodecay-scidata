from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from audit_release_privacy import scan_paths  # noqa: E402
from verify_release_candidate import is_transient_artifact  # noqa: E402


class ReleasePrivacyTests(unittest.TestCase):
    def test_clean_release_text_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("Public DOI: 10.21105/joss.00001\n", encoding="utf-8")
            self.assertEqual(scan_paths(root, ["README.md"]), [])

    def test_secret_and_local_path_are_reported_without_echoing_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "table.csv").write_text(
                "value\n"
                + "C:" + "\\Users\\person\\private.txt\n"
                + "ghp" + "_abcdefghijklmnopqrstuvwxyz1234567890\n",
                encoding="utf-8",
            )
            findings = scan_paths(root, ["table.csv"])
            self.assertEqual({finding["rule"] for finding in findings}, {"windows_user_path", "github_token"})
            self.assertTrue(all(set(finding) == {"path", "line", "rule"} for finding in findings))

    def test_binary_files_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "paper.pdf").write_bytes(b"C:" + b"\\Users\\person\\private")
            self.assertEqual(scan_paths(root, ["paper.pdf"]), [])

    def test_source_literal_tables_allow_paths_but_still_flag_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            table = root / "data/census_v0.2/tables/api_symbol_records.csv"
            table.parent.mkdir(parents=True)
            table.write_text(
                "signature\n"
                + "logfolder='" + "/home" + "/pi/pirecorder/'\n"
                + "ghp" + "_abcdefghijklmnopqrstuvwxyz1234567890\n",
                encoding="utf-8",
            )
            findings = scan_paths(root, [table.relative_to(root).as_posix()])
            self.assertEqual(findings, [{"path": "data/census_v0.2/tables/api_symbol_records.csv", "line": 3, "rule": "github_token"}])

    def test_release_verifier_ignores_runtime_caches(self) -> None:
        self.assertTrue(is_transient_artifact(Path("scripts/__pycache__/audit_release_privacy.pyc")))
        self.assertTrue(is_transient_artifact(Path(".pytest_cache/v/cache/nodeids")))
        self.assertTrue(is_transient_artifact(Path(".git/objects/ab/cdef")))
        self.assertFalse(is_transient_artifact(Path("scripts/verify_release_candidate.py")))


if __name__ == "__main__":
    unittest.main()
