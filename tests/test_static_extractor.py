from __future__ import annotations

import csv
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from extract_version_pairs import (  # noqa: E402
    ArchiveView,
    add_dependency,
    extract_api_symbols,
    extract_setup_py,
)


def snapshot() -> dict[str, str]:
    return {
        "snapshot_id": "snapshot_test",
        "project_id": "project_test",
        "snapshot_role": "publication",
    }


class StaticExtractorTests(unittest.TestCase):
    def test_validation_archive_evidence_does_not_publish_local_path(self) -> None:
        source = ROOT / "scripts" / "extract_version_pairs.py"
        text = source.read_text(encoding="utf-8")
        self.assertIn("archive_evidence = f\"snapshot:{snapshot['snapshot_id']}\"", text)
        self.assertNotIn('validation_row(snapshot, "archive_sha256", "pass", str(archive_path)', text)

    def test_archive_path_traversal_is_flagged_without_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive_path = root / "candidate.zip"
            outside = root.parent / "static_extractor_must_not_create.txt"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../static_extractor_must_not_create.txt", "unsafe")
                archive.writestr("project/pkg/__init__.py", "def visible(x):\n    return x\n")
            view = ArchiveView.read(archive_path)
            self.assertEqual(view.suspicious_member_count, 1)
            self.assertFalse(outside.exists())

    def test_setup_py_is_parsed_but_never_executed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            marker = Path(temp_dir) / "execution_marker.txt"
            source = (
                "from pathlib import Path\n"
                f"Path({marker.as_posix()!r}).write_text('executed')\n"
                "from setuptools import setup\n"
                "setup(install_requires=['numpy>=1.20'], python_requires='>=3.9')\n"
            )
            dependencies: list[dict[str, str]] = []
            constraints, issues = extract_setup_py(source, snapshot(), "setup.py", dependencies)
            self.assertFalse(marker.exists())
            self.assertEqual(issues, [])
            self.assertEqual(constraints, ["setup.py:>=3.9"])
            self.assertEqual(dependencies[0]["normalized_name"], "numpy")

    def test_dynamic_setup_expression_is_retained_as_partial_issue(self) -> None:
        source = "from setuptools import setup\nsetup(install_requires=get_requirements())\n"
        dependencies: list[dict[str, str]] = []
        _, issues = extract_setup_py(source, snapshot(), "setup.py", dependencies)
        self.assertIn("setup_py_install_requires_nonliteral", issues)
        self.assertEqual(dependencies, [])

    def test_poetry_constraint_is_preserved(self) -> None:
        rows: list[dict[str, str]] = []
        add_dependency(rows, snapshot(), "pyproject.toml", "poetry", "runtime", "numpy^1.26")
        self.assertEqual(rows[0]["normalized_name"], "numpy")
        self.assertEqual(rows[0]["specifier"], "^1.26")
        self.assertEqual(rows[0]["parse_status"], "parsed_poetry")

    def test_ast_surface_records_symbols_without_import(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = Path(temp_dir) / "api.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "project/pkg/__init__.py",
                    "__all__ = ['public']\n"
                    "def public(x: int = 1):\n    return x\n"
                    "def _private():\n    return None\n"
                    "class Model:\n"
                    "    def __init__(self, n=1):\n        self.n = n\n"
                    "    def fit(self, x):\n        return x\n",
                )
            view = ArchiveView.read(archive_path)
            validations: list[dict[str, str]] = []
            symbols, file_count, failures = extract_api_symbols(view, snapshot(), validations)
            keys = {row["symbol_key"] for row in symbols}
            self.assertEqual(file_count, 1)
            self.assertEqual(failures, 0)
            self.assertIn("pkg|function|public", keys)
            self.assertIn("pkg|class|Model", keys)
            self.assertIn("pkg|constructor|Model.__init__", keys)
            self.assertIn("pkg|method|Model.fit", keys)
            self.assertFalse(any("_private" in key for key in keys))


if __name__ == "__main__":
    unittest.main()
