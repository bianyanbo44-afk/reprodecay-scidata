#!/usr/bin/env python3
"""Extract static, version-paired records from publication and endpoint archives.

Candidate code is read as bounded text and parsed as syntax. It is never
extracted to a runnable location, imported, installed, or executed.
"""

from __future__ import annotations

import argparse
import ast
import csv
import gzip
import hashlib
import io
import json
import posixpath
import re
import tarfile
import warnings
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from declarations import PARSED_STATUSES, extract_declarations


SCHEMA_VERSION = "0.3"
PARSER_VERSION = "static-pair-extractor-0.3.0"
MAX_MEMBERS = 100_000
MAX_TEXT_MEMBER_BYTES = 2 * 1024 * 1024
MAX_TOTAL_TEXT_BYTES = 128 * 1024 * 1024
MAX_LINK_HOPS = 8
TEXT_SUFFIXES = {".py", ".toml", ".cfg", ".ini", ".txt", ".yml", ".yaml", ".in"}
TEXT_BASENAMES = {
    "setup.py", "setup.cfg", "pyproject.toml", "tox.ini", "pytest.ini",
    "pipfile", "makefile", "license", "licence", "copying", "readme",
    "citation.cff", "codemeta.json", ".zenodo.json", "security.md",
    "contributing.md", "code_of_conduct.md", "changelog.md", "changes.md",
}
EXCLUDED_PARTS = {
    ".git", ".github", ".tox", ".venv", "venv", "build", "dist", "docs",
    "doc", "examples", "example", "benchmarks", "benchmark", "scripts",
    "tools", "vendor", "vendored", "tests", "test", "testing", "site-packages",
}
ARCHIVE_JUNK_PARTS = {"__macosx"}
ARCHIVE_JUNK_BASENAMES = {".ds_store", "thumbs.db"}
ROOT_MODULE_EXCLUDES = {"setup.py", "conftest.py", "conf.py", "manage.py", "noxfile.py"}
GOVERNANCE_PATTERNS = {
    "citation_cff": {"citation.cff"},
    "codemeta": {"codemeta.json"},
    "zenodo_metadata": {".zenodo.json"},
    "contributing": {"contributing", "contributing.md", "contributing.rst"},
    "code_of_conduct": {"code_of_conduct.md", "code_of_conduct.rst", "code-of-conduct.md", "code-of-conduct.rst", "conduct.md", "conduct.rst"},
    "security_policy": {"security.md", "security.rst"},
    "changelog": {"changelog", "changelog.md", "changes", "changes.md", "history.rst"},
}


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        if not rows:
            raise ValueError(f"cannot infer fields for empty table: {path}")
        fields = list(rows[0])
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_cache_name(record_id: str, file_key: str) -> str:
    suffix = "".join(Path(file_key).suffixes[-2:]) or ".bin"
    digest = hashlib.sha256(file_key.encode("utf-8")).hexdigest()[:12]
    safe_record_id = re.sub(r"[^A-Za-z0-9._-]+", "_", record_id)
    return f"zenodo_{safe_record_id}_{digest}{suffix}"


def safe_member_path(name: str) -> bool:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    return not path.is_absolute() and ".." not in path.parts and not bool(re.match(r"^[A-Za-z]:", normalized))


def strip_common_root(names: list[str]) -> dict[str, str]:
    paths = [PurePosixPath(name.replace("\\", "/")) for name in names if name and not name.endswith("/")]
    top = {path.parts[0] for path in paths if path.parts}
    strip_one = len(top) == 1 and all(len(path.parts) > 1 for path in paths)
    result: dict[str, str] = {}
    for original, path in zip([name for name in names if name and not name.endswith("/")], paths):
        parts = path.parts[1:] if strip_one else path.parts
        if parts:
            result[original] = PurePosixPath(*parts).as_posix()
    return result


def is_interesting_text(name: str) -> bool:
    path = PurePosixPath(name)
    lower = path.name.lower()
    return path.suffix.lower() in TEXT_SUFFIXES or lower in TEXT_BASENAMES or lower.startswith("requirements")


def is_archive_junk(name: str) -> bool:
    path = PurePosixPath(name.replace("\\", "/"))
    return (
        path.name.lower() in ARCHIVE_JUNK_BASENAMES
        or any(part.lower() in ARCHIVE_JUNK_PARTS for part in path.parts)
    )


def link_target(name: str, target: str, hard: bool = False) -> str:
    """Archive path a link member points to, or "" when it leaves the archive (spec Section 1).

    A symbolic link is relative to its own directory; a tar hard link names
    another member by its full path.
    """
    target = target.replace("\\", "/")
    if not target or target.startswith("/") or re.match(r"^[A-Za-z]:", target):
        return ""
    joined = posixpath.normpath(target if hard else posixpath.join(posixpath.dirname(name), target))
    return "" if joined == ".." or joined.startswith("../") else joined


def read_named_members(path: Path, archive_type: str, names: set[str]) -> dict[str, bytes]:
    """Read the named regular members in a second pass (targets of followed links)."""
    payloads: dict[str, bytes] = {}
    if archive_type == "zip":
        with zipfile.ZipFile(path) as archive:
            for name in names:
                with archive.open(name) as handle:
                    payloads[name] = handle.read(MAX_TEXT_MEMBER_BYTES)
    else:
        with tarfile.open(path, "r:*") as archive:
            for member in archive.getmembers():
                if member.name in names and member.isfile():
                    handle = archive.extractfile(member)
                    if handle is not None:
                        with handle:
                            payloads[member.name] = handle.read(MAX_TEXT_MEMBER_BYTES)
    return payloads


def stream_member(handle: Any, keep_text: bool, prefix: bytes = b"") -> tuple[int, str, bytes]:
    """Hash a member in chunks; keep its bytes only when it is a readable text member."""
    digest = hashlib.sha256(prefix)
    size = 0
    kept: list[bytes] = []
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
        size += len(chunk)
        if keep_text:
            kept.append(chunk)
    return size, digest.hexdigest(), b"".join(kept)


@dataclass
class ArchiveView:
    path: Path
    archive_type: str
    names: list[str]
    texts: dict[str, str]
    suspicious_member_count: int
    link_member_count: int
    duplicate_normalized_count: int
    skipped_large_text_count: int
    # normalized path -> (bytes, sha256); spec Section 1
    member_digests: dict[str, tuple[int, str]] = field(default_factory=dict)
    member_mtimes: list[int] = field(default_factory=list)
    # text members that are links: normalized path -> normalized target that was read,
    # or "" when the target is missing, outside the archive or not a regular file
    link_texts: dict[str, str] = field(default_factory=dict)

    def unread_reasons(self) -> dict[str, str]:
        """Why a text member listed in `names` has no text, when it is not the size limit."""
        return {name: "link_target_unresolved" for name, target in self.link_texts.items() if not target}

    @classmethod
    def read(cls, path: Path, digests: bool = True) -> "ArchiveView":
        raw_names: list[str] = []
        raw_texts: dict[str, bytes] = {}
        raw_digests: dict[str, tuple[int, str]] = {}
        raw_sizes: dict[str, int] = {}
        raw_links: dict[str, str] = {}
        mtimes: list[int] = []
        suspicious = 0
        links = 0
        skipped_large = 0
        total_text = 0
        archive_type = ""

        if zipfile.is_zipfile(path):
            archive_type = "zip"
            with zipfile.ZipFile(path) as archive:
                infos = archive.infolist()
                if len(infos) > MAX_MEMBERS:
                    raise ValueError("archive_member_limit_exceeded")
                for info in infos:
                    if info.is_dir():
                        continue
                    if is_archive_junk(info.filename):
                        continue
                    raw_names.append(info.filename)
                    if not safe_member_path(info.filename):
                        suspicious += 1
                    unix_mode = (info.external_attr >> 16) & 0o170000
                    if unix_mode == 0o120000:
                        # a zip symbolic link stores its target as the member content
                        links += 1
                        with archive.open(info) as handle:
                            size, digest, payload = stream_member(handle, True, b"link:")
                        raw_digests[info.filename] = (size, digest)
                        raw_links[info.filename] = link_target(info.filename, payload.decode("utf-8", errors="replace"))
                        continue
                    raw_sizes[info.filename] = info.file_size
                    keep_text = is_interesting_text(info.filename) and not (
                        info.file_size > MAX_TEXT_MEMBER_BYTES or total_text + info.file_size > MAX_TOTAL_TEXT_BYTES
                    )
                    if not digests and not keep_text:
                        size, digest, payload = info.file_size, "", b""
                    else:
                        with archive.open(info) as handle:
                            size, digest, payload = stream_member(handle, keep_text)
                    raw_digests[info.filename] = (size, digest)
                    if is_interesting_text(info.filename):
                        if not keep_text:
                            skipped_large += 1
                            continue
                        raw_texts[info.filename] = payload
                        total_text += info.file_size
        elif tarfile.is_tarfile(path):
            archive_type = "tar"
            with tarfile.open(path, "r:*") as archive:
                members = archive.getmembers()
                if len(members) > MAX_MEMBERS:
                    raise ValueError("archive_member_limit_exceeded")
                for member in members:
                    if member.isdir():
                        continue
                    if is_archive_junk(member.name):
                        continue
                    raw_names.append(member.name)
                    mtimes.append(int(member.mtime))
                    if not safe_member_path(member.name):
                        suspicious += 1
                    if member.issym() or member.islnk():
                        links += 1
                        target = member.linkname.encode("utf-8", errors="replace")
                        raw_digests[member.name] = (len(target), hashlib.sha256(b"link:" + target).hexdigest())
                        raw_links[member.name] = link_target(member.name, member.linkname, hard=member.islnk())
                        continue
                    if member.isfile():
                        raw_sizes[member.name] = member.size
                    interesting = member.isfile() and is_interesting_text(member.name)
                    keep_text = interesting and not (
                        member.size > MAX_TEXT_MEMBER_BYTES or total_text + member.size > MAX_TOTAL_TEXT_BYTES
                    )
                    handle = archive.extractfile(member) if member.isfile() and (digests or keep_text) else None
                    if handle is None:
                        size, digest, payload = 0, hashlib.sha256(b"").hexdigest(), b""
                    else:
                        with handle:
                            size, digest, payload = stream_member(handle, keep_text)
                    raw_digests[member.name] = (size, digest)
                    if interesting:
                        if not keep_text:
                            skipped_large += 1
                            continue
                        raw_texts[member.name] = payload[:MAX_TEXT_MEMBER_BYTES]
                        total_text += member.size
        else:
            raise ValueError("unsupported_archive_format")

        # Spec Section 1: a text member that is a link is read as the regular
        # member it points to inside the same archive.
        regular = {posixpath.normpath(name): name for name in raw_sizes}
        link_paths = {posixpath.normpath(name): target for name, target in raw_links.items()}
        link_sources: dict[str, str] = {}
        for name, target in raw_links.items():
            if not is_interesting_text(name):
                continue
            hops = 0
            while target in link_paths and hops < MAX_LINK_HOPS:
                target, hops = link_paths[target], hops + 1
            link_sources[name] = regular.get(target, "")
        pending = {
            source for source in link_sources.values()
            if source and source not in raw_texts and raw_sizes[source] <= MAX_TEXT_MEMBER_BYTES
        }
        extra_texts = read_named_members(path, archive_type, pending) if pending else {}
        size_limited: set[str] = set()
        for name, source in link_sources.items():
            if not source:
                continue
            payload = raw_texts.get(source, extra_texts.get(source))
            if payload is None or total_text + len(payload) > MAX_TOTAL_TEXT_BYTES:
                skipped_large += 1
                size_limited.add(name)
                continue
            raw_texts[name] = payload
            total_text += len(payload)

        normalized = strip_common_root(raw_names)
        names = sorted(normalized.values())
        texts: dict[str, str] = {}
        duplicates = 0
        for raw_name, payload in raw_texts.items():
            normal_name = normalized.get(raw_name)
            if not normal_name:
                continue
            if normal_name in texts:
                duplicates += 1
                continue
            texts[normal_name] = payload.decode("utf-8", errors="replace")
        digests: dict[str, tuple[int, str]] = {}
        for raw_name in raw_names:
            normal_name = normalized.get(raw_name)
            if normal_name and normal_name not in digests and raw_name in raw_digests:
                digests[normal_name] = raw_digests[raw_name]
        link_texts: dict[str, str] = {}
        for raw_name, source in link_sources.items():
            normal_name = normalized.get(raw_name)
            if normal_name and raw_name not in size_limited:
                link_texts[normal_name] = normalized.get(source, "") if source else ""
        return cls(path, archive_type, names, texts, suspicious, links, duplicates, skipped_large, digests, mtimes, link_texts)

    def content_tree_sha256(self) -> str:
        lines = "".join(f"{name}\t{size}\t{digest}\n" for name, (size, digest) in sorted(self.member_digests.items()))
        return hashlib.sha256(lines.encode("utf-8")).hexdigest()

    def member_mtime_mode(self) -> tuple[str, str]:
        """Most common tar member mtime (UTC) and whether all members share it.

        `git archive`, which GitHub uses for codeload tarballs, stamps every
        member with the committer time of the archived commit.
        """
        if not self.member_mtimes:
            return "", ""
        value, count = Counter(self.member_mtimes).most_common(1)[0]
        stamp = datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        return stamp, str(count == len(self.member_mtimes)).lower()


def project_id(doi: str) -> str:
    suffix = doi.strip().lower().removeprefix("https://doi.org/")
    return "doi_" + "".join(ch if ch.isalnum() else "_" for ch in suffix).strip("_")


def choose_publication_file(rows: list[dict[str, str]]) -> dict[str, str]:
    def priority(row: dict[str, str]) -> tuple[int, int, str]:
        name = row["file_key"].lower()
        if name.endswith((".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")):
            kind = 0
        elif name.endswith(".zip"):
            kind = 1
        elif name.endswith(".whl"):
            kind = 9
        else:
            kind = 5
        return kind, int(row["file_size_bytes"] or 0), name
    return sorted(rows, key=priority)[0]


def root_basenames(names: Iterable[str]) -> set[str]:
    return {PurePosixPath(name).name.lower() for name in names if len(PurePosixPath(name).parts) == 1}


def package_source_files(names: list[str]) -> list[str]:
    python_files = [PurePosixPath(name) for name in names if name.lower().endswith(".py")]
    roots: set[tuple[str, ...]] = set()
    for path in python_files:
        parts = path.parts
        if not parts:
            continue
        if parts[0].lower() in {"src", "python"} and len(parts) >= 3:
            roots.add((parts[0], parts[1]))
        elif len(parts) >= 2 and path.name == "__init__.py":
            roots.add((parts[0],))
    selected: list[str] = []
    for path in python_files:
        lower_parts = {part.lower() for part in path.parts}
        if lower_parts & EXCLUDED_PARTS:
            continue
        if len(path.parts) == 1 and path.name.lower() not in ROOT_MODULE_EXCLUDES:
            selected.append(path.as_posix())
            continue
        if any(path.parts[: len(root)] == root for root in roots):
            selected.append(path.as_posix())
    return sorted(set(selected))


def module_name(source_file: str) -> str:
    path = PurePosixPath(source_file)
    parts = list(path.parts)
    if parts and parts[0].lower() in {"src", "python"}:
        parts = parts[1:]
    if not parts:
        return ""
    parts[-1] = PurePosixPath(parts[-1]).stem
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def signature_text(arguments: ast.arguments) -> str:
    try:
        return ast.unparse(arguments)[:2000]
    except Exception:
        return ""


def decorator_text(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> str:
    values: list[str] = []
    for decorator in node.decorator_list:
        try:
            values.append(ast.unparse(decorator)[:300])
        except Exception:
            values.append(type(decorator).__name__)
    return json_text(sorted(values))


def literal_all(tree: ast.Module) -> set[str] | None:
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
                try:
                    value = ast.literal_eval(node.value)
                    if isinstance(value, (list, tuple, set)) and all(isinstance(item, str) for item in value):
                        return set(value)
                except Exception:
                    return None
    return None


def extract_api_symbols(
    view: ArchiveView, snapshot: dict[str, str], validation_rows: list[dict[str, str]]
) -> tuple[list[dict[str, str]], int, int, int]:
    raw_rows: list[dict[str, str]] = []
    parse_failures = 0
    unread = 0
    source_files = package_source_files(view.names)
    for source_file in source_files:
        text = view.texts.get(source_file)
        if text is None:
            parse_failures += 1
            unread += 1
            validation_rows.append(validation_row(snapshot, "api_source_read", "error", source_file, "source_text_unavailable"))
            continue
        try:
            with warnings.catch_warnings(record=True) as caught_warnings:
                warnings.simplefilter("always", SyntaxWarning)
                tree = ast.parse(text, filename=source_file, type_comments=True)
            for warning in caught_warnings:
                validation_rows.append(
                    validation_row(
                        snapshot,
                        "python_ast_warning",
                        "warning",
                        f"{source_file}:{getattr(warning, 'lineno', '')}",
                        f"{warning.category.__name__}:{warning.message}",
                    )
                )
        except (SyntaxError, ValueError, MemoryError, RecursionError) as exc:
            parse_failures += 1
            validation_rows.append(validation_row(snapshot, "python_ast_parse", "error", source_file, f"{type(exc).__name__}:{str(exc)[:300]}"))
            continue
        module = module_name(source_file)
        all_names = literal_all(tree)
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if node.name.startswith("_"):
                continue
            evidence = "literal_all" if all_names is not None and node.name in all_names else "lexical_nonprivate"
            if isinstance(node, ast.ClassDef):
                bases = []
                for base in node.bases:
                    try:
                        bases.append(ast.unparse(base)[:300])
                    except Exception:
                        bases.append(type(base).__name__)
                raw_rows.append(api_row(snapshot, source_file, module, "class", node.name, json_text(bases), node, evidence))
                for member in node.body:
                    if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if member.name.startswith("_") and member.name != "__init__":
                        continue
                    kind = "constructor" if member.name == "__init__" else "method"
                    raw_rows.append(
                        api_row(
                            snapshot, source_file, module, kind, f"{node.name}.{member.name}",
                            signature_text(member.args), member, "class_member",
                        )
                    )
            else:
                kind = "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"
                raw_rows.append(api_row(snapshot, source_file, module, kind, node.name, signature_text(node.args), node, evidence))

    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in raw_rows:
        grouped[(row["module"], row["symbol_kind"], row["qualified_name"])].append(row)
    result: list[dict[str, str]] = []
    for key in sorted(grouped):
        variants = grouped[key]
        first = sorted(variants, key=lambda row: (int(row["line_start"]), row["signature"]))[0]
        first["signature"] = " || ".join(sorted({row["signature"] for row in variants}))
        first["decorators"] = json_text(sorted({item for row in variants for item in json.loads(row["decorators"])}))
        first["variant_count"] = str(len(variants))
        result.append(first)
    return result, len(source_files), parse_failures, unread


def api_row(
    snapshot: dict[str, str], source_file: str, module: str, kind: str,
    qualified_name: str, signature: str, node: ast.AST, evidence: str,
) -> dict[str, str]:
    symbol_key = f"{module}|{kind}|{qualified_name}"
    return {
        "snapshot_id": snapshot["snapshot_id"],
        "project_id": snapshot["project_id"],
        "snapshot_role": snapshot["snapshot_role"],
        "symbol_key": symbol_key,
        "source_file": source_file,
        "module": module,
        "symbol_kind": kind,
        "qualified_name": qualified_name,
        "signature": signature,
        "decorators": decorator_text(node),
        "export_evidence": evidence,
        "line_start": str(getattr(node, "lineno", "")),
        "line_end": str(getattr(node, "end_lineno", "")),
        "variant_count": "1",
    }


def validation_row(snapshot: dict[str, str], check: str, status: str, evidence: str, detail: str) -> dict[str, str]:
    return {
        "snapshot_id": snapshot["snapshot_id"],
        "project_id": snapshot["project_id"],
        "snapshot_role": snapshot["snapshot_role"],
        "check_name": check,
        "status": status,
        "evidence": evidence,
        "detail": detail,
    }


@dataclass
class SnapshotExtraction:
    structure: dict[str, str]
    dependency_rows: list[dict[str, str]]
    api_rows: list[dict[str, str]]
    declaration_rows: list[dict[str, str]]
    identity: dict[str, str]
    member_digests: dict[str, tuple[int, str]]


def extract_snapshot(snapshot: dict[str, str], validation_rows: list[dict[str, str]]) -> SnapshotExtraction:
    archive_path = Path(snapshot["local_archive_path"])
    actual_sha = sha256_file(archive_path)
    if actual_sha != snapshot["archive_sha256"]:
        raise ValueError(f"archive_sha256_mismatch:{snapshot['snapshot_id']}")
    view = ArchiveView.read(archive_path)
    archive_evidence = f"snapshot:{snapshot['snapshot_id']}"
    if view.suspicious_member_count:
        validation_rows.append(validation_row(snapshot, "archive_paths", "error", archive_evidence, str(view.suspicious_member_count)))
    else:
        validation_rows.append(validation_row(snapshot, "archive_paths", "pass", archive_evidence, "0 suspicious paths"))
    validation_rows.append(validation_row(snapshot, "archive_sha256", "pass", archive_evidence, actual_sha))

    declarations = extract_declarations(snapshot, view.names, view.texts, view.unread_reasons())
    dependency_status, dependency_reasons = declarations.dependency_observation_status()
    for row in declarations.field_rows:
        if row["observation_status"] not in {"extracted", "confirmed_absent"}:
            validation_rows.append(
                validation_row(snapshot, "declaration_observation", "warning",
                               f"{row['source_file']}:{row['field']}", f"{row['observation_status']}:{row['detail']}")
            )
    pyproject_failed = any(
        row["source_kind"] == "pyproject" and row["observation_status"] == "parse_failure"
        for row in declarations.field_rows
    )

    api_rows, source_file_count, ast_parse_failures, unread_sources = extract_api_symbols(view, snapshot, validation_rows)
    source_lines = sum(len(view.texts[name].splitlines()) for name in package_source_files(view.names) if name in view.texts)
    lower_names = [name.lower() for name in view.names]
    root_names = root_basenames(view.names)
    governance = {
        key: any(
            PurePosixPath(name).name.lower() in patterns
            and (
                len(PurePosixPath(name).parts) == 1
                or PurePosixPath(name).parts[0].lower() == ".github"
            )
            for name in lower_names
        )
        for key, patterns in GOVERNANCE_PATTERNS.items()
    }
    ci_files = [
        name for name in view.names
        if name.lower().startswith(".github/workflows/") and name.lower().endswith((".yml", ".yaml"))
    ]
    ci_providers = []
    if ci_files:
        ci_providers.append("github_actions")
    for filename, provider in ((".travis.yml", "travis"), ("appveyor.yml", "appveyor"), ("azure-pipelines.yml", "azure_pipelines"), ("circle.yml", "circleci")):
        if filename in root_names:
            ci_providers.append(provider)
    test_files = [
        name for name in view.names
        if name.lower().endswith(".py")
        and (any(part.lower() in {"test", "tests", "testing"} for part in PurePosixPath(name).parts)
             or PurePosixPath(name).name.lower().startswith("test_"))
    ]
    structure = {
        "snapshot_id": snapshot["snapshot_id"],
        "project_id": snapshot["project_id"],
        "snapshot_role": snapshot["snapshot_role"],
        "archive_type": view.archive_type,
        "archive_member_count": str(len(view.names)),
        "archive_bytes": snapshot["archive_bytes"],
        "archive_sha256": actual_sha,
        "suspicious_member_count": str(view.suspicious_member_count),
        "link_member_count": str(view.link_member_count),
        "duplicate_normalized_member_count": str(view.duplicate_normalized_count),
        "skipped_large_text_count": str(view.skipped_large_text_count),
        "packaging_files": json_text(sorted(set(declarations.declaration_files))),
        "build_backends": json_text(sorted(set(declarations.build_backends))),
        "python_constraints": json_text(sorted(set(declarations.python_constraints))),
        "declared_dependency_record_count": str(len(declarations.dependency_rows)),
        "unparsed_dependency_record_count": str(sum(row["parse_status"] == "unparsed" for row in declarations.dependency_rows)),
        "source_python_file_count": str(source_file_count),
        "source_python_unread_file_count": str(unread_sources),
        "source_python_line_count": str(source_lines),
        "ast_parse_failure_count": str(ast_parse_failures),
        "syntactic_api_symbol_count": str(len(api_rows)),
        "test_python_file_count": str(len(test_files)),
        "ci_workflow_file_count": str(len(ci_files)),
        "ci_providers": json_text(sorted(set(ci_providers))),
        "citation_cff": str(governance["citation_cff"]).lower(),
        "codemeta": str(governance["codemeta"]).lower(),
        "zenodo_metadata": str(governance["zenodo_metadata"]).lower(),
        "contributing": str(governance["contributing"]).lower(),
        "code_of_conduct": str(governance["code_of_conduct"]).lower(),
        "security_policy": str(governance["security_policy"]).lower(),
        "changelog": str(governance["changelog"]).lower(),
        "archive_listing_status": "complete",
        "dependency_observation_status": dependency_status,
        "dependency_incomplete_reasons": json_text(dependency_reasons),
        "python_constraint_observation_status": declarations.python_constraint_observation_status(),
        "build_backend_observation_status": "incomplete" if pyproject_failed else "complete",
        "source_line_observation_status": "incomplete" if unread_sources else "complete",
        "api_observation_status": "incomplete" if ast_parse_failures else "complete",
        "parser_version": PARSER_VERSION,
        "third_party_code_executed": "false",
    }
    mtime_mode, mtime_uniform = view.member_mtime_mode()
    identity = {
        "content_tree_sha256": view.content_tree_sha256(),
        "content_member_count": str(len(view.member_digests)),
        "member_mtime_utc_mode": mtime_mode,
        "member_mtime_uniform": mtime_uniform,
    }
    return SnapshotExtraction(structure, declarations.dependency_rows, api_rows, declarations.field_rows,
                              identity, view.member_digests)


def aggregate_dependencies(rows: list[dict[str, str]]) -> dict[str, tuple[str, str]]:
    grouped: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"values": set(), "evidence": set()})
    for row in rows:
        if not row["normalized_name"] or row["parse_status"] not in PARSED_STATUSES:
            continue
        key = f"{row['dependency_group']}|{row['normalized_name']}"
        semantic = json_text({
            "specifier": row["specifier"], "marker": row["marker"], "extras": row["extras"],
            "attributes": json.loads(row["attributes"]) if row["attributes"] else {},
        })
        grouped[key]["values"].add(semantic)
        grouped[key]["evidence"].add(f"{row['source_file']}:{row['raw_declaration']}")
    return {
        key: (json_text(sorted(value["values"])), json_text(sorted(value["evidence"])))
        for key, value in grouped.items()
    }


def aggregate_api(rows: list[dict[str, str]]) -> dict[str, tuple[str, str]]:
    return {
        row["symbol_key"]: (
            json_text({"signature": row["signature"], "decorators": json.loads(row["decorators"])}),
            f"{row['source_file']}:{row['line_start']}",
        )
        for row in rows
    }


def set_features(structure: dict[str, str], field: str) -> dict[str, tuple[str, str]]:
    return {str(value): ("true", field) for value in json.loads(structure[field])}


def delta_rows_for_pair(
    pair_id: str, project: str, family: str, group: str,
    publication: dict[str, tuple[str, str]], endpoint: dict[str, tuple[str, str]],
    comparison_status: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for key in sorted(set(publication) | set(endpoint)):
        pub_value, pub_evidence = publication.get(key, ("", ""))
        end_value, end_evidence = endpoint.get(key, ("", ""))
        if key not in publication:
            delta = "added"
        elif key not in endpoint:
            delta = "removed"
        elif pub_value == end_value:
            delta = "unchanged"
        else:
            delta = "changed"
        rows.append(
            {
                "pair_id": pair_id,
                "project_id": project,
                "family": family,
                "feature_group": group,
                "feature_key": key,
                "publication_value": pub_value,
                "endpoint_value": end_value,
                "delta_type": delta,
                "comparison_status": comparison_status,
                "publication_evidence": pub_evidence,
                "endpoint_evidence": end_evidence,
            }
        )
    return rows


COMPLETE = "complete"
# Spec Section 6: the observation status that governs each compared feature.
STRUCTURE_LIST_STATUS = {
    "packaging_files": "archive_listing_status",
    "build_backends": "build_backend_observation_status",
    "python_constraints": "python_constraint_observation_status",
    "ci_providers": "archive_listing_status",
}
STRUCTURE_SCALAR_STATUS = {
    "source_python_file_count": "archive_listing_status",
    "source_python_line_count": "source_line_observation_status",
    "test_python_file_count": "archive_listing_status",
    "ci_workflow_file_count": "archive_listing_status",
    "citation_cff": "archive_listing_status",
    "codemeta": "archive_listing_status",
    "zenodo_metadata": "archive_listing_status",
    "contributing": "archive_listing_status",
    "code_of_conduct": "archive_listing_status",
    "security_policy": "archive_listing_status",
    "changelog": "archive_listing_status",
}


def comparison_status(publication: dict[str, str], endpoint: dict[str, str], status_field: str) -> str:
    def complete(row: dict[str, str]) -> bool:
        value = row[status_field]
        return value == COMPLETE or (status_field == "dependency_observation_status" and value == "no_declaration_file")
    return "comparable" if complete(publication) and complete(endpoint) else "incomplete_observation"


def parse_date(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def days_between(start: str, end: str) -> str:
    first, second = parse_date(start), parse_date(end)
    if first is None or second is None:
        return ""
    return str((second.date() - first.date()).days)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", default="data/candidate_frame_v0.1/pilot_candidates.csv")
    parser.add_argument("--publication-inspection", default="data/candidate_frame_v0.1/archive_static_inspection.csv")
    parser.add_argument("--publication-inventory", default="")
    parser.add_argument("--linkage-ledger", default="", help="archive_linkage_ledger.csv for archive publication dates")
    parser.add_argument("--publication-cache", default="data/raw/zenodo_archives")
    parser.add_argument("--endpoint-resolution", default="data/pilot_v0.2/endpoint_resolution.csv")
    parser.add_argument("--endpoint-cache", default="data/raw/endpoint_archives")
    parser.add_argument("--endpoint-as-of", default="2026-08-14", help="date the endpoint policy refers to")
    parser.add_argument("--output-dir", default="data/pilot_v0.2/tables")
    parser.add_argument("--member-manifest", default="", help="optional .csv.gz path for per-member content digests")
    parser.add_argument("--archive-paths-out", default="",
                        help="optional local-only CSV mapping snapshot_id to the archive path used")
    args = parser.parse_args()

    candidates = {row["doi"]: row for row in csv.DictReader(Path(args.candidates).open(encoding="utf-8-sig", newline=""))}
    publication_inventory: dict[tuple[str, str], dict[str, str]] = {}
    selected_publication_key: dict[str, str] = {}
    if args.publication_inventory:
        publication_inventory = {
            (row["scholarly_doi"], row["file_key"]): row
            for row in csv.DictReader(Path(args.publication_inventory).open(encoding="utf-8-sig", newline=""))
            if row.get("selected_source_archive") == "true"
        }
        selected_publication_key = {
            doi: file_key for doi, file_key in publication_inventory
        }
    linkage: dict[str, dict[str, str]] = {}
    if args.linkage_ledger:
        linkage = {
            row["scholarly_doi"]: row
            for row in csv.DictReader(Path(args.linkage_ledger).open(encoding="utf-8-sig", newline=""))
        }
    publication_groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in csv.DictReader(Path(args.publication_inspection).open(encoding="utf-8-sig", newline="")):
        if row["inspection_status"] == "inspected" and row["suspicious_member_path"] == "false":
            publication_groups[row["scholarly_doi"]].append(row)
    endpoints = [
        row for row in csv.DictReader(Path(args.endpoint_resolution).open(encoding="utf-8-sig", newline=""))
        if row["resolution_status"] == "resolved"
    ]
    output_dir = Path(args.output_dir)
    validation_rows: list[dict[str, str]] = []
    project_rows: list[dict[str, str]] = []
    snapshot_rows: list[dict[str, str]] = []
    structure_rows: list[dict[str, str]] = []
    dependency_rows: list[dict[str, str]] = []
    declaration_rows: list[dict[str, str]] = []
    api_rows: list[dict[str, str]] = []
    delta_rows: list[dict[str, str]] = []
    member_rows: list[tuple[str, str, int, str]] = []

    for endpoint in sorted(endpoints, key=lambda row: int(row["selection_rank"])):
        doi = endpoint["scholarly_doi"]
        if doi not in candidates or doi not in publication_groups:
            continue
        candidate = candidates[doi]
        if doi in selected_publication_key:
            publication = next(
                row for row in publication_groups[doi]
                if row["file_key"] == selected_publication_key[doi]
            )
        else:
            publication = choose_publication_file(publication_groups[doi])
        inventory_row = publication_inventory.get((doi, publication["file_key"]), {})
        archive_record_id = inventory_row.get("archive_record_id") or publication["archive_doi"].rsplit(".", 1)[-1]
        publication_path = Path(args.publication_cache) / safe_cache_name(archive_record_id, publication["file_key"])
        endpoint_path = Path(args.endpoint_cache) / f"{endpoint['project_id']}_{endpoint['endpoint_commit_sha']}.tar.gz"
        if not publication_path.exists() or not endpoint_path.exists():
            continue
        pid = project_id(doi)
        pub_sha = publication["local_sha256"]
        end_sha = endpoint["endpoint_archive_sha256"]
        pub_snapshot_id = f"{pid}__publication__{pub_sha[:16]}"
        end_snapshot_id = f"{pid}__endpoint__{end_sha[:16]}"
        pair_id = f"{pid}__{pub_sha[:12]}__{end_sha[:12]}"
        snapshots = [
            {
                "snapshot_id": pub_snapshot_id,
                "project_id": pid,
                "pair_id": pair_id,
                "snapshot_role": "publication",
                "source_provider": inventory_row.get("provider") or "zenodo",
                "source_identifier": publication["archive_doi"],
                "source_ref": publication["file_key"],
                "commit_sha": "",
                "source_url": f"https://doi.org/{publication['archive_doi']}",
                "local_archive_path": publication_path.as_posix(),
                "archive_bytes": publication["file_size_bytes"],
                "archive_sha256": pub_sha,
                "upstream_checksum": publication["repository_checksum"],
            },
            {
                "snapshot_id": end_snapshot_id,
                "project_id": pid,
                "pair_id": pair_id,
                "snapshot_role": "endpoint",
                "source_provider": "github",
                "source_identifier": endpoint["resolved_full_name"],
                "source_ref": endpoint["endpoint_ref_name"],
                "commit_sha": endpoint["endpoint_commit_sha"],
                "source_url": endpoint["endpoint_archive_url"],
                "local_archive_path": endpoint_path.as_posix(),
                "archive_bytes": endpoint["endpoint_archive_bytes"],
                "archive_sha256": end_sha,
                "upstream_checksum": "",
            },
        ]
        extracted: dict[str, SnapshotExtraction] = {}
        for snapshot in snapshots:
            result = extract_snapshot(snapshot, validation_rows)
            extracted[snapshot["snapshot_role"]] = result
            snapshot_rows.append({**snapshot, **result.identity, "parser_version": PARSER_VERSION, "third_party_code_executed": "false"})
            structure_rows.append(result.structure)
            dependency_rows.extend(result.dependency_rows)
            declaration_rows.extend(result.declaration_rows)
            api_rows.extend(result.api_rows)
            if args.member_manifest:
                member_rows.extend(
                    (snapshot["snapshot_id"], name, size, digest)
                    for name, (size, digest) in sorted(result.member_digests.items())
                )

        # Spec Section 1 and reviewer request: article, archive and endpoint dates with intervals.
        endpoint_identity = extracted["endpoint"].identity
        commit_time = endpoint_identity["member_mtime_utc_mode"] if endpoint_identity["member_mtime_uniform"] == "true" else ""
        ref_date = endpoint["endpoint_release_published_at"] if endpoint["endpoint_ref_type"] == "release" else commit_time
        archive_date = linkage.get(doi, {}).get("archive_publication_date", "")
        project_rows.append(
            {
                "project_id": pid,
                "pair_id": pair_id,
                "scholarly_doi": doi,
                "title": candidate["title"],
                "publication_date": candidate["publication_date"],
                "publication_year": candidate["publication_date"][:4],
                "joss_url": candidate["joss_url"],
                "input_repository_url": candidate["repository_url"],
                "resolved_repository_url": endpoint["resolved_repository_url"],
                "selection_rank": candidate["rank"],
                "selected_role": candidate["selected_role"],
                "publication_archive_doi": publication["archive_doi"],
                "publication_archive_date": archive_date,
                "publication_snapshot_id": pub_snapshot_id,
                "endpoint_snapshot_id": end_snapshot_id,
                "endpoint_policy": endpoint["endpoint_policy"],
                "endpoint_as_of_date": args.endpoint_as_of,
                "endpoint_resolution_mode": endpoint.get("resolution_mode") or "live",
                "endpoint_resolved_at_utc": endpoint["resolved_at_utc"],
                "endpoint_ref_type": endpoint["endpoint_ref_type"],
                "endpoint_ref_name": endpoint["endpoint_ref_name"],
                "endpoint_commit_sha": endpoint["endpoint_commit_sha"],
                "endpoint_release_published_at": endpoint["endpoint_release_published_at"],
                "endpoint_commit_time_utc": commit_time,
                "endpoint_ref_date": ref_date,
                "archive_to_article_days": days_between(archive_date, candidate["publication_date"]),
                "article_to_endpoint_ref_days": days_between(candidate["publication_date"], ref_date),
                "article_to_endpoint_as_of_days": days_between(candidate["publication_date"], args.endpoint_as_of),
                "repository_archived_at_endpoint": endpoint["repository_archived"],
                "repository_license_spdx_at_endpoint": endpoint["repository_license_spdx"],
            }
        )

        pub_structure = extracted["publication"].structure
        end_structure = extracted["endpoint"].structure
        delta_rows.extend(
            delta_rows_for_pair(
                pair_id, pid, "dependency", "declared_dependency",
                aggregate_dependencies(extracted["publication"].dependency_rows),
                aggregate_dependencies(extracted["endpoint"].dependency_rows),
                comparison_status(pub_structure, end_structure, "dependency_observation_status"),
            )
        )
        delta_rows.extend(
            delta_rows_for_pair(
                pair_id, pid, "api", "syntactic_symbol",
                aggregate_api(extracted["publication"].api_rows),
                aggregate_api(extracted["endpoint"].api_rows),
                comparison_status(pub_structure, end_structure, "api_observation_status"),
            )
        )
        for field_name, status_field in STRUCTURE_LIST_STATUS.items():
            delta_rows.extend(
                delta_rows_for_pair(
                    pair_id, pid, "structure", field_name,
                    set_features(pub_structure, field_name), set_features(end_structure, field_name),
                    comparison_status(pub_structure, end_structure, status_field),
                )
            )
        for field_name, status_field in STRUCTURE_SCALAR_STATUS.items():
            delta_rows.extend(
                delta_rows_for_pair(
                    pair_id, pid, "structure", "scalar",
                    {field_name: (pub_structure[field_name], field_name)},
                    {field_name: (end_structure[field_name], field_name)},
                    comparison_status(pub_structure, end_structure, status_field),
                )
            )

    project_rows.sort(key=lambda row: int(row["selection_rank"]))
    snapshot_rows.sort(key=lambda row: (row["project_id"], row["snapshot_role"]))
    structure_rows.sort(key=lambda row: (row["project_id"], row["snapshot_role"]))
    dependency_rows.sort(key=lambda row: (row["project_id"], row["snapshot_role"], row["dependency_group"], row["normalized_name"], row["source_file"], row["raw_declaration"]))
    declaration_rows.sort(key=lambda row: (row["project_id"], row["snapshot_role"], row["source_file"], row["field"]))
    api_rows.sort(key=lambda row: (row["project_id"], row["snapshot_role"], row["symbol_key"]))
    delta_rows.sort(key=lambda row: (row["project_id"], row["family"], row["feature_group"], row["feature_key"]))
    validation_rows.sort(key=lambda row: (row["project_id"], row["snapshot_role"], row["check_name"], row["evidence"]))

    write_csv(output_dir / "project_records.csv", project_rows)
    public_snapshot_rows = [
        {key: value for key, value in row.items() if key != "local_archive_path"}
        for row in snapshot_rows
    ]
    write_csv(output_dir / "snapshot_records.csv", public_snapshot_rows)
    write_csv(output_dir / "structure_records.csv", structure_rows)
    write_csv(output_dir / "dependency_records.csv", dependency_rows, [
        "snapshot_id", "project_id", "snapshot_role", "source_file", "source_kind",
        "dependency_group", "normalized_name", "raw_declaration", "specifier",
        "marker", "extras", "attributes", "parse_status", "parse_error",
    ])
    write_csv(output_dir / "declaration_source_records.csv", declaration_rows, [
        "snapshot_id", "project_id", "snapshot_role", "source_file", "source_kind", "field",
        "field_kind", "observation_status", "entry_count", "unparsed_entry_count", "detail",
    ])
    write_csv(output_dir / "api_symbol_records.csv", api_rows, [
        "snapshot_id", "project_id", "snapshot_role", "symbol_key", "source_file",
        "module", "symbol_kind", "qualified_name", "signature", "decorators",
        "export_evidence", "line_start", "line_end", "variant_count",
    ])
    write_csv(output_dir / "pair_delta_records.csv", delta_rows, [
        "pair_id", "project_id", "family", "feature_group", "feature_key",
        "publication_value", "endpoint_value", "delta_type", "comparison_status",
        "publication_evidence", "endpoint_evidence",
    ])
    write_csv(output_dir / "validation_records.csv", validation_rows, [
        "snapshot_id", "project_id", "snapshot_role", "check_name", "status", "evidence", "detail",
    ])
    if args.archive_paths_out:
        write_csv(Path(args.archive_paths_out),
                  [{"snapshot_id": row["snapshot_id"], "local_archive_path": row["local_archive_path"]} for row in snapshot_rows])
    if args.member_manifest:
        manifest_path = Path(args.member_manifest)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        # mtime=0 and an empty stored filename keep the gzip bytes reproducible.
        with manifest_path.open("wb") as target, \
                gzip.GzipFile(filename="", mode="wb", fileobj=target, mtime=0) as raw, \
                io.TextIOWrapper(raw, encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["snapshot_id", "member_path", "member_bytes", "member_sha256"])
            writer.writerows(sorted(member_rows))

    table_names = [
        "project_records.csv", "snapshot_records.csv", "structure_records.csv",
        "dependency_records.csv", "declaration_source_records.csv", "api_symbol_records.csv",
        "pair_delta_records.csv", "validation_records.csv",
    ]
    input_paths = {
        "candidates": Path(args.candidates),
        "publication_inspection": Path(args.publication_inspection),
        "endpoint_resolution": Path(args.endpoint_resolution),
    }
    if args.publication_inventory:
        input_paths["publication_inventory"] = Path(args.publication_inventory)
    if args.linkage_ledger:
        input_paths["linkage_ledger"] = Path(args.linkage_ledger)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "parser_version": PARSER_VERSION,
        "extraction_specification": "docs/extraction_specification_v0.3.md",
        "projects": len(project_rows),
        "snapshots": len(snapshot_rows),
        "structure_records": len(structure_rows),
        "dependency_records": len(dependency_rows),
        "declaration_source_records": len(declaration_rows),
        "api_symbol_records": len(api_rows),
        "pair_delta_records": len(delta_rows),
        "validation_records": len(validation_rows),
        "third_party_code_executed": False,
        "tables": table_names,
        "table_sha256": {name: sha256_file(output_dir / name) for name in table_names},
        "input_sha256": {name: sha256_file(path) for name, path in sorted(input_paths.items())},
    }
    (output_dir / "extraction_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
