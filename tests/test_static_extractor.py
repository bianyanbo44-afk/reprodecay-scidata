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
    extract_api_symbols,
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

    def test_content_tree_digest_ignores_container_format(self) -> None:
        # Spec Section 1: identical files at identical paths give the same digest,
        # whatever the archive bytes or format.
        import io
        import tarfile
        files = {"pkg/__init__.py": b"x = 1\n", "README.md": b"readme\n"}
        with tempfile.TemporaryDirectory() as temp_dir:
            zip_path = Path(temp_dir) / "a.zip"
            tar_path = Path(temp_dir) / "b.tar.gz"
            with zipfile.ZipFile(zip_path, "w") as archive:
                for name, payload in files.items():
                    archive.writestr(f"proj-1.0/{name}", payload)
            with tarfile.open(tar_path, "w:gz") as archive:
                for name, payload in files.items():
                    info = tarfile.TarInfo(f"other-root/{name}")
                    info.size = len(payload)
                    info.mtime = 1600000000
                    archive.addfile(info, io.BytesIO(payload))
            first, second = ArchiveView.read(zip_path), ArchiveView.read(tar_path)
            self.assertEqual(first.content_tree_sha256(), second.content_tree_sha256())
            self.assertEqual(second.member_mtime_mode(), ("2020-09-13T12:26:40Z", "true"))

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
            symbols, file_count, failures, unread = extract_api_symbols(view, snapshot(), validations)
            keys = {row["symbol_key"] for row in symbols}
            self.assertEqual(file_count, 1)
            self.assertEqual(failures, 0)
            self.assertEqual(unread, 0)
            self.assertIn("pkg|function|public", keys)
            self.assertIn("pkg|class|Model", keys)
            self.assertIn("pkg|constructor|Model.__init__", keys)
            self.assertIn("pkg|method|Model.fit", keys)
            self.assertFalse(any("_private" in key for key in keys))

    def test_text_links_are_read_as_their_target(self) -> None:
        # Spec Section 1: a link to a regular member is read as that member; a link
        # that leaves the archive is listed but not read, with its own reason.
        import hashlib
        import io
        import tarfile
        from declarations import extract_declarations
        base = b"attrs\ntyper[all]\n"
        with tempfile.TemporaryDirectory() as temp_dir:
            tar_path = Path(temp_dir) / "links.tar.gz"
            with tarfile.open(tar_path, "w:gz") as archive:
                info = tarfile.TarInfo("proj-1/requirements/base.txt")
                info.size = len(base)
                archive.addfile(info, io.BytesIO(base))
                for name, kind, target in (
                    ("proj-1/requirements.txt", tarfile.SYMTYPE, "requirements/base.txt"),
                    ("proj-1/requirements-dev.txt", tarfile.LNKTYPE, "proj-1/requirements/base.txt"),
                    ("proj-1/setup.py", tarfile.SYMTYPE, "../../elsewhere/setup.py"),
                ):
                    link = tarfile.TarInfo(name)
                    link.type, link.linkname = kind, target
                    archive.addfile(link)
            zip_path = Path(temp_dir) / "links.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("proj-1/requirements/base.txt", base)
                link = zipfile.ZipInfo("proj-1/requirements.txt")
                link.external_attr = 0o120777 << 16
                archive.writestr(link, "requirements/base.txt")

            view = ArchiveView.read(tar_path)
            self.assertEqual(view.texts["requirements.txt"], base.decode())
            self.assertEqual(view.texts["requirements-dev.txt"], base.decode())
            self.assertNotIn("setup.py", view.texts)
            self.assertEqual(view.unread_reasons(), {"setup.py": "link_target_unresolved"})
            expected = hashlib.sha256(b"link:requirements/base.txt").hexdigest()
            self.assertEqual(view.member_digests["requirements.txt"], (21, expected))

            zipped = ArchiveView.read(zip_path)
            self.assertEqual(zipped.texts["requirements.txt"], base.decode())
            self.assertEqual(zipped.member_digests["requirements.txt"], (21, expected))

            declarations = extract_declarations(snapshot(), view.names, view.texts, view.unread_reasons())
            names = {(row["normalized_name"], row["extras"]) for row in declarations.dependency_rows if row["source_file"] == "requirements.txt"}
            self.assertEqual(names, {("attrs", ""), ("typer", "all")})
            setup_rows = [row for row in declarations.field_rows if row["source_file"] == "setup.py"]
            self.assertEqual([(row["observation_status"], row["detail"]) for row in setup_rows], [("parse_failure", "link_target_unresolved")])


if __name__ == "__main__":
    unittest.main()
