#!/usr/bin/env python3
"""Independent semantic audit of a stratified snapshot sample.

This script intentionally does not import the release extractor.  It reads the
released tables, locates the cached immutable archives by size and SHA-256, and
re-annotates Python symbols, dependency declarations and structure fields with
small independent parsers.  It is an audit of exact agreement on the
sample, not a population-level accuracy estimate.
"""

from __future__ import annotations

import argparse
import ast
import configparser
import csv
import hashlib
import json
import math
import posixpath
import re
import tarfile
import tomllib
import zipfile
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
except Exception:  # pragma: no cover - deterministic fallback for minimal runtimes
    Requirement = None

    def canonicalize_name(value: str) -> str:
        return re.sub(r"[-_.]+", "-", value).lower()

try:
    import yaml
except Exception:  # pragma: no cover - recorded as a parser limitation
    yaml = None


EXCLUDED_PARTS = {
    ".git", ".github", ".tox", ".venv", "venv", "build", "dist", "docs",
    "doc", "examples", "example", "benchmarks", "benchmark", "scripts",
    "tools", "vendor", "vendored", "tests", "test", "testing", "site-packages",
}
ROOT_MODULE_EXCLUDES = {"setup.py", "conftest.py", "conf.py", "manage.py", "noxfile.py"}
TEXT_SUFFIXES = {".py", ".toml", ".cfg", ".ini", ".txt", ".yml", ".yaml", ".in"}
TEXT_BASENAMES = {
    "setup.py", "setup.cfg", "pyproject.toml", "tox.ini", "pytest.ini", "pipfile",
    "makefile", "license", "licence", "copying", "readme", "citation.cff",
    "codemeta.json", ".zenodo.json", "security.md", "contributing.md",
    "code_of_conduct.md", "changelog.md", "changes.md",
}
GOVERNANCE = {
    "citation_cff": {"citation.cff"},
    "codemeta": {"codemeta.json"},
    "zenodo_metadata": {".zenodo.json"},
    "contributing": {"contributing", "contributing.md", "contributing.rst"},
    "code_of_conduct": {"code_of_conduct.md", "code_of_conduct.rst", "code-of-conduct.md", "conduct.md"},
    "security_policy": {"security.md", "security.rst"},
    "changelog": {"changelog", "changelog.md", "changes", "changes.md", "history.rst"},
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def strip_root(names: list[str]) -> dict[str, str]:
    clean = [n.replace("\\", "/") for n in names if n and not n.endswith("/")]
    parts = [PurePosixPath(n).parts for n in clean]
    tops = {p[0] for p in parts if p}
    remove = len(tops) == 1 and all(len(p) > 1 for p in parts)
    return {
        raw: PurePosixPath(*(PurePosixPath(raw).parts[1:] if remove else PurePosixPath(raw).parts)).as_posix()
        for raw in clean
    }


def interesting(name: str) -> bool:
    path = PurePosixPath(name)
    lower = path.name.lower()
    return path.suffix.lower() in TEXT_SUFFIXES or lower in TEXT_BASENAMES or lower.startswith("requirements")


def archive_junk(name: str) -> bool:
    path = PurePosixPath(name.replace("\\", "/"))
    return path.name.lower() in {".ds_store", "thumbs.db"} or any(part.lower() == "__macosx" for part in path.parts)


def resolve_link(links: dict[str, str], files: set[str], start: str) -> str | None:
    """Follow archive-internal links from `start` to a regular file; None if there is none."""
    seen: set[str] = set()
    current = start
    while current in links and current not in seen and len(seen) <= 8:
        seen.add(current)
        current = links[current]
    return current if current in files else None


class ArchiveView:
    def __init__(self, path: Path) -> None:
        self.path = path
        limit = 2 * 1024 * 1024
        raw_names: list[str] = []
        raw_text: dict[str, bytes] = {}
        files: dict[str, str] = {}  # posix-normalized path -> raw member name of a regular file
        links: dict[str, str] = {}  # posix-normalized link path -> posix-normalized target
        text_links: list[str] = []

        def link(raw: str, target: str, relative: bool) -> None:
            here = posixpath.normpath(raw)
            if relative:
                target = posixpath.join(posixpath.dirname(raw), target)
            links[here] = posixpath.normpath(target.replace("\\", "/"))
            if interesting(raw):
                text_links.append(raw)

        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    if archive_junk(info.filename):
                        continue
                    raw_names.append(info.filename)
                    if (info.external_attr >> 16) & 0o170000 == 0o120000:
                        link(info.filename, archive.read(info).decode("utf-8", errors="replace"), True)
                        continue
                    files[posixpath.normpath(info.filename)] = info.filename
                    if interesting(info.filename) and info.file_size <= limit:
                        raw_text[info.filename] = archive.read(info)
                wanted = {resolve_link(links, set(files), posixpath.normpath(raw)) for raw in text_links}
                for target in wanted - {None}:
                    if files[target] not in raw_text and archive.getinfo(files[target]).file_size <= limit:
                        raw_text[files[target]] = archive.read(files[target])
            self.archive_type = "zip"
        elif tarfile.is_tarfile(path):
            with tarfile.open(path, "r:*") as archive:
                for member in archive.getmembers():
                    if member.isdir():
                        continue
                    if archive_junk(member.name):
                        continue
                    raw_names.append(member.name)
                    if member.issym() or member.islnk():
                        link(member.name, member.linkname, member.issym())
                        continue
                    if member.isfile():
                        files[posixpath.normpath(member.name)] = member.name
                    if member.isfile() and interesting(member.name) and member.size <= limit:
                        handle = archive.extractfile(member)
                        if handle is not None:
                            raw_text[member.name] = handle.read(limit)
            wanted = {resolve_link(links, set(files), posixpath.normpath(raw)) for raw in text_links} - {None}
            missing = {files[target] for target in wanted if files[target] not in raw_text}
            if missing:
                with tarfile.open(path, "r:*") as archive:
                    for member in archive.getmembers():
                        if member.name in missing and member.isfile() and member.size <= limit:
                            handle = archive.extractfile(member)
                            if handle is not None:
                                raw_text[member.name] = handle.read(limit)
            self.archive_type = "tar"
        else:
            raise ValueError(f"unsupported_archive:{path}")
        for raw in text_links:
            target = resolve_link(links, set(files), posixpath.normpath(raw))
            if target is not None and files[target] in raw_text:
                raw_text[raw] = raw_text[files[target]]
        normalized = strip_root(raw_names)
        self.names = sorted(set(normalized.values()))
        self.texts = {
            normalized[raw]: payload.decode("utf-8", errors="replace")
            for raw, payload in raw_text.items()
            if raw in normalized
        }


def locate_archives(root: Path, snapshots: list[dict[str, str]]) -> dict[str, Path]:
    bases = [root / "data/raw/endpoint_archives_v0.2", root / "data/raw/zenodo_archives_v0.2",
             root / "data/raw/endpoint_archives_v0.3", root / "data/raw/zenodo_archives_v0.3"]
    files = [p for base in bases if base.is_dir() for p in base.rglob("*") if p.is_file()]
    by_size: dict[int, list[Path]] = defaultdict(list)
    for path in files:
        by_size[path.stat().st_size].append(path)
    located: dict[str, Path] = {}
    for row in snapshots:
        try:
            size = int(row["archive_bytes"])
        except ValueError:
            continue
        preferred = [p for p in by_size.get(size, []) if row.get("commit_sha") and row["commit_sha"][:16] in p.name]
        candidates = preferred + [p for p in by_size.get(size, []) if p not in preferred]
        for path in candidates:
            if sha256(path) == row["archive_sha256"]:
                located[row["snapshot_id"]] = path
                break
    return located


def source_files(names: list[str]) -> list[str]:
    py = [PurePosixPath(n) for n in names if n.lower().endswith(".py")]
    roots: set[tuple[str, ...]] = set()
    for path in py:
        parts = path.parts
        if parts and parts[0].lower() in {"src", "python"} and len(parts) >= 3:
            roots.add((parts[0], parts[1]))
        elif len(parts) >= 2 and path.name == "__init__.py":
            roots.add((parts[0],))
    selected: list[str] = []
    for path in py:
        lower_parts = {part.lower() for part in path.parts}
        if lower_parts & EXCLUDED_PARTS:
            continue
        if len(path.parts) == 1 and path.name.lower() not in ROOT_MODULE_EXCLUDES:
            selected.append(path.as_posix())
        elif any(path.parts[:len(root)] == root for root in roots):
            selected.append(path.as_posix())
    return sorted(set(selected))


def module_name(source_file: str) -> str:
    parts = list(PurePosixPath(source_file).parts)
    if parts and parts[0].lower() in {"src", "python"}:
        parts = parts[1:]
    if not parts:
        return ""
    parts[-1] = PurePosixPath(parts[-1]).stem
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def json_compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def literal_all(tree: ast.Module) -> set[str] | None:
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
                try:
                    value = ast.literal_eval(node.value)
                except Exception:
                    return None
                if isinstance(value, (list, tuple, set)) and all(isinstance(item, str) for item in value):
                    return set(value)
                return None
    return None


def signature(node: ast.AST) -> str:
    try:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return ast.unparse(node.args)[:2000]
        if isinstance(node, ast.ClassDef):
            return json_compact([ast.unparse(base)[:300] for base in node.bases])
    except Exception:
        return ""
    return ""


def decorators(node: ast.AST) -> str:
    values: list[str] = []
    for item in getattr(node, "decorator_list", []):
        try:
            values.append(ast.unparse(item)[:300])
        except Exception:
            values.append(type(item).__name__)
    return json_compact(sorted(values))


def independent_api(view: ArchiveView) -> tuple[dict[str, dict[str, str]], int, int]:
    rows: dict[str, dict[str, str]] = {}
    parse_failures = 0
    files = source_files(view.names)
    for source_file in files:
        text = view.texts.get(source_file)
        if text is None:
            parse_failures += 1
            continue
        try:
            tree = ast.parse(text, filename=source_file, type_comments=True)
        except (SyntaxError, ValueError, MemoryError, RecursionError):
            parse_failures += 1
            continue
        module = module_name(source_file)
        all_names = literal_all(tree)
        candidates: list[tuple[ast.AST, str, str, str]] = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                if not node.name.startswith("_"):
                    candidates.append((node, "class", node.name, "literal_all" if all_names is not None and node.name in all_names else "lexical_nonprivate"))
                    for member in node.body:
                        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and (not member.name.startswith("_") or member.name == "__init__"):
                            kind = "constructor" if member.name == "__init__" else "method"
                            candidates.append((member, kind, f"{node.name}.{member.name}", "class_member"))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                kind = "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"
                candidates.append((node, kind, node.name, "literal_all" if all_names is not None and node.name in all_names else "lexical_nonprivate"))
        for node, kind, name, evidence in candidates:
            key = f"{module}|{kind}|{name}"
            row = {
                "symbol_key": key,
                "source_file": source_file,
                "symbol_kind": kind,
                "qualified_name": name,
                "signature": signature(node),
                "decorators": decorators(node),
                "export_evidence": evidence,
                "line_start": str(getattr(node, "lineno", "")),
                "line_end": str(getattr(node, "end_lineno", "")),
            }
            if key in rows:
                signatures = set(rows[key]["signature"].split(" || ")) | {row["signature"]}
                decs = set(json.loads(rows[key]["decorators"])) | set(json.loads(row["decorators"]))
                rows[key]["signature"] = " || ".join(sorted(signatures))
                rows[key]["decorators"] = json_compact(sorted(decs))
                rows[key]["line_start"] = min(rows[key]["line_start"], row["line_start"], key=lambda x: int(x or 0))
            else:
                rows[key] = row
    return rows, len(files), parse_failures


DEPENDENCY_FIELDS = (
    "source_file",
    "source_kind",
    "dependency_group",
    "normalized_name",
    "raw_declaration",
    "specifier",
    "marker",
    "extras",
    "attributes",
    "parse_status",
)


DIRECTIVES = ("-r", "--requirement", "-c", "--constraint", "-i", "--index-url", "--extra-index-url", "-f",
              "--find-links", "--trusted-host", "--pre", "--no-binary", "--only-binary", "--prefer-binary",
              "--use-feature")
OK_STATUSES = {"extracted", "confirmed_absent"}


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def requirement_parts(raw: str) -> tuple[str, str, str, str, str]:
    """Spec v0.3 Section 3.1, written independently of scripts/declarations.py."""
    text = str(raw).strip()
    if not text or text.startswith("#"):
        return "", "", "", "", "skip"
    text = text.split(" #", 1)[0].strip()
    head = text.split()[0].split("=")[0].lower() if text.split() else ""
    if head in {"-e", "--editable"}:
        target = text[len(text.split()[0]):].strip() if " " in text or "\t" in text else text.split("=", 1)[-1]
        egg = re.search(r"#egg=([A-Za-z0-9_.-]+)", target)
        is_url = "://" in target or target.split("+")[0] in {"git", "hg", "svn", "bzr"}
        if is_url:
            return (canonicalize_name(egg.group(1)), "", "", "", "parsed_url") if egg else ("", "", "", "", "unparsed")
        return "", "", "", "", "directive"
    if head in DIRECTIVES:
        return "", "", "", "", "directive"
    if " --" in text:
        text = text.split(" --", 1)[0].strip()
    egg = re.search(r"#egg=([A-Za-z0-9_.-]+)", text)
    if egg and "://" in text.split("#", 1)[0] and " @ " not in text:
        return canonicalize_name(egg.group(1)), "", "", "", "parsed_url"
    if Requirement is not None:
        try:
            req = Requirement(text)
            return canonicalize_name(req.name), str(req.specifier), str(req.marker or ""), ";".join(sorted(req.extras)), "parsed"
        except Exception:
            pass
    conda = re.fullmatch(r"(?:[A-Za-z0-9_.-]+::)?([A-Za-z0-9_.-]+)(?:\s*([<>=!~]=?.*)|\s+(\S.*))?", text)
    if conda:
        return canonicalize_name(conda.group(1)), (conda.group(2) or conda.group(3) or "").strip(), "", "", "parsed_non_pep508"
    return "", "", "", "", "unparsed"


class Collector:
    def __init__(self) -> None:
        self.rows: list[dict[str, str]] = []
        self.fields: list[tuple[str, str, str]] = []  # (field_kind, field, status)
        self.python: list[str] = []
        self.backends: list[str] = []
        self.files: list[str] = []

    def row(self, source_file: str, kind: str, group: str, name: str, raw: str, spec: str, marker: str,
            extras: str, attributes: str, status: str) -> dict[str, str]:
        item = {"source_file": source_file, "source_kind": kind, "dependency_group": group, "normalized_name": name,
                "raw_declaration": raw, "specifier": spec, "marker": marker, "extras": extras,
                "attributes": attributes, "parse_status": status}
        self.rows.append(item)
        return item

    def req(self, source_file: str, kind: str, group: str, raw: Any) -> dict[str, str] | None:
        name, spec, marker, extras, status = requirement_parts(str(raw))
        if status == "skip":
            return None
        return self.row(source_file, kind, group, name, str(raw).strip(), spec, marker, extras, "", status)

    def table(self, source_file: str, kind: str, group: str, name: str, value: Any, status: str) -> dict[str, str]:
        raw = f"{name} = {compact(value)}"
        key = canonicalize_name(str(name))
        if isinstance(value, str):
            return self.row(source_file, kind, group, key, raw, "" if value.strip() == "*" else value.strip(), "", "", "", status)
        if isinstance(value, dict):
            version = str(value.get("version", "")).strip()
            extras = value.get("extras") or []
            rest = {k: v for k, v in value.items() if k != "version"}
            return self.row(source_file, kind, group, key, raw, "" if version == "*" else version,
                            str(value.get("markers", "")),
                            ";".join(sorted(map(str, extras))) if isinstance(extras, list) else str(extras),
                            compact(rest) if rest else "", status)
        if isinstance(value, list) and all(isinstance(x, dict) for x in value):
            return self.row(source_file, kind, group, key, raw, " || ".join(str(x.get("version", "")).strip() for x in value),
                            "", "", compact({"constraints": value}), status)
        return self.row(source_file, kind, group, key, raw, "", "", "", "", "unparsed")

    def mark(self, kind: str, field: str, status: str) -> None:
        self.fields.append((kind, field, status))

    def mark_rows(self, field: str, rows: list[dict[str, str] | None], unresolved: int = 0) -> None:
        bad = unresolved or any(r is not None and r["parse_status"] == "unparsed" for r in rows)
        self.mark("dependency", field, "extracted_partial" if bad else "extracted")

    def python_value(self, source_file: str, field: str, value: Any) -> None:
        if value is None:
            self.mark("python_constraint", field, "confirmed_absent")
        elif isinstance(value, str):
            self.python.append(f"{source_file}:{value.strip()}")
            self.mark("python_constraint", field, "extracted")
        else:
            self.mark("python_constraint", field, "unsupported_syntax")


def joined_lines(text: str) -> list[str]:
    out, carry = [], ""
    for line in text.splitlines():
        if line.rstrip().endswith("\\"):
            carry += line.rstrip()[:-1] + " "
        else:
            out.append(carry + line)
            carry = ""
    return out + ([carry] if carry else [])


def requirements_group(base: str) -> str:
    return "test" if "test" in base else "development" if any(t in base for t in ("dev", "lint", "docs", "doc")) else "runtime"


def parse_requirements_file(c: Collector, name: str, texts: dict[str, str]) -> None:
    group = requirements_group(PurePosixPath(name).name.lower())
    rows: list[dict[str, str] | None] = []
    missing = False
    queue = [(name, "requirements", frozenset({name}))]
    while queue:
        path, kind, seen = queue.pop(0)
        for line in joined_lines(texts.get(path, "")):
            item = c.req(path, kind, group, line)
            if item is None:
                continue
            rows.append(item)
            inc = re.match(r"^(?:-r|--requirement)(?:\s+|=)(\S+)", line.strip())
            if not inc:
                continue
            target = PurePosixPath(PurePosixPath(path).parent, inc.group(1)).as_posix().removeprefix("./")
            target = PurePosixPath(target).as_posix()
            base = PurePosixPath(target).name.lower()
            if len(PurePosixPath(target).parts) == 1 and base.startswith("requirements") and base.endswith((".txt", ".in")):
                continue
            if target in seen:
                continue
            if target not in texts:
                missing = True
                continue
            queue.append((target, "requirements_include", seen | {target}))
    if missing:
        c.mark("dependency", "requirements", "extracted_partial")
    elif rows:
        c.mark_rows("requirements", rows)
    else:
        c.mark("dependency", "requirements", "confirmed_absent")


def parse_pyproject(c: Collector, name: str, text: str) -> None:
    try:
        data = tomllib.loads(text)
    except Exception:
        c.mark("file", "(file)", "parse_failure")
        c.backend_failed = True  # type: ignore[attr-defined]
        return
    build = data.get("build-system")
    if isinstance(build, dict):
        if build.get("build-backend"):
            c.backends.append(str(build["build-backend"]))
        if "requires" in build:
            string_list(c, name, "pep517", "build-system.requires", "build", build["requires"])
    project = data.get("project")
    if isinstance(project, dict):
        dynamic = set(project.get("dynamic") or []) if isinstance(project.get("dynamic"), list) else set()
        if "dependencies" in dynamic:
            c.mark("dependency", "project.dependencies", "dynamic_metadata")
        elif "dependencies" in project:
            string_list(c, name, "pep621", "project.dependencies", "runtime", project["dependencies"])
        else:
            c.mark("dependency", "project.dependencies", "confirmed_absent")
        optional = project.get("optional-dependencies")
        if "optional-dependencies" in dynamic:
            c.mark("dependency", "project.optional-dependencies", "dynamic_metadata")
        elif isinstance(optional, dict):
            for extra, values in optional.items():
                string_list(c, name, "pep621", f"project.optional-dependencies.{extra}", f"optional:{extra}", values)
        elif optional is not None:
            c.mark("dependency", "project.optional-dependencies", "unsupported_syntax")
        if "requires-python" in dynamic:
            c.mark("python_constraint", "project.requires-python", "dynamic_metadata")
        else:
            c.python_value(name, "project.requires-python", project.get("requires-python"))
    dependency_groups = data.get("dependency-groups")
    if isinstance(dependency_groups, dict):
        for group, entries in dependency_groups.items():
            if not isinstance(entries, list):
                c.mark("dependency", f"dependency-groups.{group}", "unsupported_syntax")
                continue
            mapped = poetry_group_name(str(group))
            collected, bad = [], 0
            for entry in entries:
                if isinstance(entry, str):
                    collected.append(c.req(name, "pep735", mapped, entry))
                elif isinstance(entry, dict) and list(entry) == ["include-group"]:
                    collected.append(c.row(name, "pep735", mapped, "", compact(entry), "", "", "", "", "directive"))
                else:
                    bad += 1
            c.mark_rows(f"dependency-groups.{group}", collected, bad)
    elif dependency_groups is not None:
        c.mark("dependency", "dependency-groups", "unsupported_syntax")
    tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
    poetry = tool.get("poetry")
    if isinstance(poetry, dict):
        parse_poetry(c, name, poetry)
    flit = tool.get("flit", {}).get("metadata") if isinstance(tool.get("flit"), dict) else None
    if isinstance(flit, dict):
        if "requires" in flit:
            string_list(c, name, "flit", "tool.flit.metadata.requires", "runtime", flit["requires"])
        else:
            c.mark("dependency", "tool.flit.metadata.requires", "confirmed_absent")
        if isinstance(flit.get("requires-extra"), dict):
            for extra, values in flit["requires-extra"].items():
                string_list(c, name, "flit", f"tool.flit.metadata.requires-extra.{extra}", f"optional:{extra}", values)
        c.python_value(name, "tool.flit.metadata.requires-python", flit.get("requires-python"))
    st = tool.get("setuptools", {}).get("dynamic") if isinstance(tool.get("setuptools"), dict) else None
    if isinstance(st, dict):
        for key in ("dependencies", "optional-dependencies"):
            if key in st:
                c.mark("dependency", f"tool.setuptools.dynamic.{key}", "dynamic_metadata")


def string_list(c: Collector, name: str, kind: str, field: str, group: str, value: Any) -> None:
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        c.mark_rows(field, [c.req(name, kind, group, v) for v in value])
    else:
        c.mark("dependency", field, "unsupported_syntax")


def poetry_group_name(group: str) -> str:
    g = group.lower()
    return "development" if g in {"dev", "docs", "doc", "lint"} else "test" if g in {"test", "tests", "testing"} else f"group:{group}"


def parse_poetry(c: Collector, name: str, poetry: dict[str, Any]) -> None:
    extras: dict[str, list[str]] = {}
    for extra, members in (poetry.get("extras") or {}).items() if isinstance(poetry.get("extras"), dict) else []:
        for member in members if isinstance(members, list) else []:
            extras.setdefault(canonicalize_name(str(member)), []).append(str(extra))
    deps = poetry.get("dependencies")
    if isinstance(deps, dict):
        rows = []
        py = None
        for key, value in deps.items():
            if str(key).lower() == "python":
                py = value
                continue
            if isinstance(value, dict) and value.get("optional") is True:
                groups = [f"optional:{e}" for e in sorted(extras.get(canonicalize_name(str(key)), []))] or ["optional:unassigned"]
            else:
                groups = ["runtime"]
            rows += [c.table(name, "poetry", g, str(key), value, "parsed_poetry") for g in groups]
        c.mark_rows("tool.poetry.dependencies", rows)
        c.python_value(name, "tool.poetry.dependencies.python", py)
    elif "dependencies" in poetry:
        c.mark("dependency", "tool.poetry.dependencies", "unsupported_syntax")
        c.mark("python_constraint", "tool.poetry.dependencies.python", "unsupported_syntax")
    else:
        c.mark("dependency", "tool.poetry.dependencies", "confirmed_absent")
        c.mark("python_constraint", "tool.poetry.dependencies.python", "confirmed_absent")
    dev = poetry.get("dev-dependencies")
    if isinstance(dev, dict):
        c.mark_rows("tool.poetry.dev-dependencies",
                    [c.table(name, "poetry_dev", "development", str(k), v, "parsed_poetry") for k, v in dev.items()])
    elif dev is not None:
        c.mark("dependency", "tool.poetry.dev-dependencies", "unsupported_syntax")
    for group, data in (poetry.get("group") or {}).items() if isinstance(poetry.get("group"), dict) else []:
        values = data.get("dependencies") if isinstance(data, dict) else None
        field = f"tool.poetry.group.{group}.dependencies"
        if isinstance(values, dict):
            c.mark_rows(field, [c.table(name, "poetry_group", poetry_group_name(str(group)), str(k), v, "parsed_poetry")
                                for k, v in values.items()])
        else:
            c.mark("dependency", field, "unsupported_syntax")


def parse_pipfile(c: Collector, name: str, text: str) -> None:
    try:
        data = tomllib.loads(text)
    except Exception:
        c.mark("file", "(file)", "parse_failure")
        return
    for section, group in (("packages", "runtime"), ("dev-packages", "development")):
        values = data.get(section)
        if values is None:
            c.mark("dependency", section, "confirmed_absent")
        elif isinstance(values, dict):
            c.mark_rows(section, [c.table(name, "pipfile", group, str(k), v, "parsed_pipfile") for k, v in values.items()])
        else:
            c.mark("dependency", section, "unsupported_syntax")
    req = data.get("requires") if isinstance(data.get("requires"), dict) else {}
    c.python_value(name, "requires.python_version", req.get("python_full_version", req.get("python_version")))


def parse_setup_cfg(c: Collector, name: str, text: str) -> None:
    cfg = configparser.ConfigParser(interpolation=None)
    try:
        cfg.read_string(text)
    except Exception:
        c.mark("file", "(file)", "parse_failure")
        return
    for option, group in (("install_requires", "runtime"), ("setup_requires", "build"), ("tests_require", "test")):
        if not cfg.has_option("options", option):
            c.mark("dependency", f"options.{option}", "confirmed_absent")
            continue
        value = cfg.get("options", option)
        if value.strip().startswith(("file:", "attr:")):
            c.mark("dependency", f"options.{option}", "dynamic_metadata")
            continue
        c.mark_rows(f"options.{option}", [c.req(name, "setup_cfg", group, line) for line in value.splitlines()])
    if cfg.has_section("options.extras_require"):
        for extra, value in cfg.items("options.extras_require"):
            if value.strip().startswith(("file:", "attr:")):
                c.mark("dependency", f"options.extras_require.{extra}", "dynamic_metadata")
            else:
                c.mark_rows(f"options.extras_require.{extra}",
                            [c.req(name, "setup_cfg", f"optional:{extra}", line) for line in value.splitlines()])
    else:
        c.mark("dependency", "options.extras_require", "confirmed_absent")
    if cfg.has_option("options", "python_requires"):
        value = cfg.get("options", "python_requires")
        if value.strip().startswith(("file:", "attr:")):
            c.mark("python_constraint", "options.python_requires", "dynamic_metadata")
        else:
            c.python_value(name, "options.python_requires", value)
    else:
        c.python_value(name, "options.python_requires", None)


class NotStatic(Exception):
    pass


MUTATORS = {"append", "extend", "insert", "remove", "pop", "clear", "update", "setdefault", "__setitem__"}


def module_bindings(tree: ast.Module, stop: ast.stmt | None) -> tuple[dict[str, Any], set[str]]:
    values: dict[str, Any] = {}
    unsafe: set[str] = set()
    top = {id(n) for n in tree.body}

    def value_of(node: ast.AST) -> Any:
        return static_value(node, values, unsafe)

    for node in tree.body:
        if node is stop:
            break
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                values[node.targets[0].id] = value_of(node.value)
            except NotStatic:
                values.pop(node.targets[0].id, None)
                unsafe.add(node.targets[0].id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            try:
                values[node.target.id] = value_of(node.value)
            except NotStatic:
                values.pop(node.target.id, None)
                unsafe.add(node.target.id)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Subscript) \
                and isinstance(node.targets[0].value, ast.Name) and isinstance(values.get(node.targets[0].value.id), dict):
            try:
                values[node.targets[0].value.id][value_of(node.targets[0].slice)] = value_of(node.value)
            except (NotStatic, TypeError):
                unsafe.add(node.targets[0].value.id)
    globals_declared = {n for node in ast.walk(tree) if isinstance(node, ast.Global) for n in node.names}
    inside_scope = {id(child) for node in ast.walk(tree)
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef))
                    for child in ast.walk(node) if child is not node}
    for node in ast.walk(tree):
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            unsafe.add(node.target.id)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) \
                and node.func.attr in MUTATORS:
            unsafe.add(node.func.value.id)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and id(node) not in top:
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                for child in ast.walk(target):
                    if isinstance(child, ast.Name) and (id(node) not in inside_scope or child.id in globals_declared):
                        unsafe.add(child.id)
                    elif isinstance(child, ast.Subscript) and isinstance(child.value, ast.Name):
                        unsafe.add(child.value.id)
        elif isinstance(node, ast.For) and id(node) not in inside_scope:
            unsafe.update(child.id for child in ast.walk(node.target) if isinstance(child, ast.Name))
    return values, unsafe


def static_value(node: ast.AST, values: dict[str, Any], unsafe: set[str]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        return [static_value(x, values, unsafe) for x in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(static_value(x, values, unsafe) for x in node.elts)
    if isinstance(node, ast.Set):
        return {static_value(x, values, unsafe) for x in node.elts}
    if isinstance(node, ast.Dict):
        if None in node.keys:
            raise NotStatic("dict_unpack")
        return {static_value(k, values, unsafe): static_value(v, values, unsafe) for k, v in zip(node.keys, node.values)}
    if isinstance(node, ast.Name):
        if node.id in unsafe or node.id not in values:
            raise NotStatic(node.id)
        return values[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict" and not node.args \
            and all(k.arg for k in node.keywords):
        return {k.arg: static_value(k.value, values, unsafe) for k in node.keywords}
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = static_value(node.left, values, unsafe), static_value(node.right, values, unsafe)
        if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
            return list(a) + list(b)
        raise NotStatic("add")
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
        return -node.operand.value
    raise NotStatic(type(node).__name__)


def parse_setup_py(c: Collector, name: str, text: str) -> None:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        c.mark("file", "(file)", "parse_failure")
        return
    calls = sorted((n for n in ast.walk(tree) if isinstance(n, ast.Call)
                    and ((isinstance(n.func, ast.Name) and n.func.id == "setup")
                         or (isinstance(n.func, ast.Attribute) and n.func.attr == "setup"))),
                   key=lambda n: (n.lineno, n.col_offset))
    if not calls:
        c.mark("file", "(file)", "parse_failure")
        return
    call = calls[-1]
    stop = next((n for n in tree.body if n.lineno <= call.lineno <= (n.end_lineno or n.lineno)
                 and not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))), None)
    values, unsafe = module_bindings(tree, stop)
    given: dict[str, tuple[str, Any]] = {}
    hidden = False
    for kw in call.keywords:
        if kw.arg:
            given[kw.arg] = ("node", kw.value)
            continue
        try:
            mapping = static_value(kw.value, values, unsafe)
        except NotStatic:
            hidden = True
            continue
        if not isinstance(mapping, dict):
            hidden = True
            continue
        for key, value in mapping.items():
            given[str(key)] = ("value", value)
    if hidden:
        c.mark("setup_kwargs", "setup(**...)", "unsupported_syntax")

    def resolve(entry: tuple[str, Any]) -> Any:
        return entry[1] if entry[0] == "value" else static_value(entry[1], values, unsafe)

    def as_items(value: Any) -> list[Any] | None:
        if isinstance(value, str):
            return value.splitlines()
        return list(value) if isinstance(value, (list, tuple, set)) else None

    for field, group in (("install_requires", "runtime"), ("setup_requires", "build"), ("tests_require", "test")):
        if field not in given:
            if not hidden:
                c.mark("dependency", f"setup.{field}", "confirmed_absent")
            continue
        entry = given[field]
        try:
            items = as_items(resolve(entry))
            if items is None:
                c.mark("dependency", f"setup.{field}", "unsupported_syntax")
            else:
                c.mark_rows(f"setup.{field}", [c.req(name, "setup_py", group, v) for v in items])
        except NotStatic:
            if entry[0] == "node" and isinstance(entry[1], (ast.List, ast.Tuple)):
                rows, bad = [], 0
                for element in entry[1].elts:
                    try:
                        rows.append(c.req(name, "setup_py", group, static_value(element, values, unsafe)))
                    except NotStatic:
                        bad += 1
                c.mark_rows(f"setup.{field}", rows, bad)
            else:
                c.mark("dependency", f"setup.{field}", "unsupported_syntax")
    if "extras_require" not in given:
        if not hidden:
            c.mark("dependency", "setup.extras_require", "confirmed_absent")
    else:
        entry = given["extras_require"]
        try:
            extras = resolve(entry)
            if not isinstance(extras, dict):
                c.mark("dependency", "setup.extras_require", "unsupported_syntax")
            else:
                if not extras:
                    c.mark("dependency", "setup.extras_require", "extracted")
                for extra, value in extras.items():
                    items = as_items(value)
                    if items is None:
                        c.mark("dependency", f"setup.extras_require.{extra}", "unsupported_syntax")
                    else:
                        c.mark_rows(f"setup.extras_require.{extra}",
                                    [c.req(name, "setup_py", f"optional:{extra}", v) for v in items])
        except NotStatic:
            node = entry[1]
            if entry[0] == "node" and isinstance(node, ast.Dict) and None not in node.keys:
                for key_node, value_node in zip(node.keys, node.values):
                    try:
                        extra = str(static_value(key_node, values, unsafe))
                    except NotStatic:
                        c.mark("dependency", "setup.extras_require.<unresolved key>", "unsupported_syntax")
                        continue
                    try:
                        items = as_items(static_value(value_node, values, unsafe))
                        if items is None:
                            c.mark("dependency", f"setup.extras_require.{extra}", "unsupported_syntax")
                        else:
                            c.mark_rows(f"setup.extras_require.{extra}",
                                        [c.req(name, "setup_py", f"optional:{extra}", v) for v in items])
                    except NotStatic:
                        if isinstance(value_node, (ast.List, ast.Tuple)):
                            rows, bad = [], 0
                            for element in value_node.elts:
                                try:
                                    rows.append(c.req(name, "setup_py", f"optional:{extra}", static_value(element, values, unsafe)))
                                except NotStatic:
                                    bad += 1
                            c.mark_rows(f"setup.extras_require.{extra}", rows, bad)
                        else:
                            c.mark("dependency", f"setup.extras_require.{extra}", "unsupported_syntax")
            else:
                c.mark("dependency", "setup.extras_require", "unsupported_syntax")
    if "python_requires" not in given:
        if not hidden:
            c.mark("python_constraint", "setup.python_requires", "confirmed_absent")
    else:
        try:
            c.python_value(name, "setup.python_requires", resolve(given["python_requires"]))
        except NotStatic:
            c.mark("python_constraint", "setup.python_requires", "unsupported_syntax")


def parse_environment(c: Collector, name: str, text: str) -> None:
    if yaml is None:
        c.mark("file", "(file)", "parse_failure")
        return
    try:
        data = yaml.safe_load(text) or {}
    except Exception:
        c.mark("file", "(file)", "parse_failure")
        return
    deps = data.get("dependencies") if isinstance(data, dict) else None
    if deps is None:
        c.mark("dependency", "dependencies", "confirmed_absent")
        c.mark("python_constraint", "dependencies.python", "confirmed_absent")
        return
    if not isinstance(deps, list):
        c.mark("dependency", "dependencies", "unsupported_syntax")
        c.mark("python_constraint", "dependencies.python", "unsupported_syntax")
        return
    rows, bad, seen_python = [], 0, False
    for value in deps:
        if isinstance(value, str):
            if re.match(r"^(?:[A-Za-z0-9_.-]+::)?python(?:\s|[<>=!~]|$)", value.strip(), flags=re.IGNORECASE):
                c.python.append(f"{name}:{value.strip()}")
                seen_python = True
            else:
                rows.append(c.req(name, "conda_environment", "runtime", value))
        elif isinstance(value, dict):
            for sub, items in value.items():
                if isinstance(items, list):
                    rows += [c.req(name, f"conda_{sub}", "runtime", item) for item in items]
                else:
                    bad += 1
        else:
            bad += 1
    c.mark_rows("dependencies", rows, bad)
    c.mark("python_constraint", "dependencies.python", "extracted" if seen_python else "confirmed_absent")


def independent_dependencies(view: ArchiveView) -> tuple[list[tuple[str, ...]], dict[str, str]]:
    """All root-level declaration sources of one snapshot (spec v0.3 Sections 2-5)."""
    c = Collector()
    c.backend_failed = False  # type: ignore[attr-defined]
    for name in sorted(view.names):
        if len(PurePosixPath(name).parts) != 1:
            continue
        base = name.lower()
        is_requirements = base.startswith("requirements") and base.endswith((".txt", ".in"))
        if base not in {"pyproject.toml", "setup.cfg", "setup.py", "environment.yml", "environment.yaml", "pipfile"} \
                and not is_requirements:
            continue
        c.files.append(name)
        if name not in view.texts:
            c.mark("file", "(file)", "parse_failure")
            if base == "pyproject.toml":
                c.backend_failed = True  # type: ignore[attr-defined]
            continue
        text = view.texts[name]
        if base == "pyproject.toml":
            parse_pyproject(c, name, text)
        elif base == "setup.cfg":
            parse_setup_cfg(c, name, text)
        elif base == "setup.py":
            parse_setup_py(c, name, text)
        elif base == "pipfile":
            parse_pipfile(c, name, text)
        elif base in {"environment.yml", "environment.yaml"}:
            parse_environment(c, name, text)
        else:
            parse_requirements_file(c, name, view.texts)
    rows = [r for r in c.rows if r is not None]
    dependency_status = (
        "no_declaration_file" if not c.files else
        "complete" if all(s in OK_STATUSES for k, _, s in c.fields if k in {"dependency", "file", "setup_kwargs"})
        else "incomplete"
    )
    python_status = "complete" if all(
        s in OK_STATUSES for k, _, s in c.fields if k in {"python_constraint", "file", "setup_kwargs"}
    ) else "incomplete"
    observed = {
        "packaging_files": compact(sorted(set(c.files))),
        "build_backends": compact(sorted(set(c.backends))),
        "python_constraints": compact(sorted(set(c.python))),
        "dependency_observation_status": dependency_status,
        "python_constraint_observation_status": python_status,
        "build_backend_observation_status": "incomplete" if c.backend_failed else "complete",  # type: ignore[attr-defined]
    }
    return [tuple(r[f] for f in DEPENDENCY_FIELDS) for r in rows], observed


def expected_dependency_records(rows: list[dict[str, str]]) -> list[tuple[str, ...]]:
    return [tuple(row[field] for field in DEPENDENCY_FIELDS) for row in rows]


def counter_agreement(expected: list[tuple[str, ...]], observed: list[tuple[str, ...]]) -> int:
    return sum((Counter(expected) & Counter(observed)).values())


def dependency_field_agreement(
    expected: list[tuple[str, ...]], observed: list[tuple[str, ...]], field_idx: int
) -> int:
    """Match one field while holding every other dependency field fixed."""
    expected_by_context: dict[tuple[str, ...], Counter[str]] = defaultdict(Counter)
    observed_by_context: dict[tuple[str, ...], Counter[str]] = defaultdict(Counter)
    for row in expected:
        context = row[:field_idx] + row[field_idx + 1 :]
        expected_by_context[context][row[field_idx]] += 1
    for row in observed:
        context = row[:field_idx] + row[field_idx + 1 :]
        observed_by_context[context][row[field_idx]] += 1
    return sum(
        sum((values & observed_by_context[context]).values())
        for context, values in expected_by_context.items()
    )


def expected_api_map(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {row["symbol_key"]: row for row in rows}


def api_field_equal(expected: dict[str, str], observed: dict[str, str], field: str) -> bool:
    if field == "decorators":
        return sorted(json.loads(expected[field] or "[]")) == sorted(json.loads(observed[field] or "[]"))
    if field == "signature":
        return sorted((expected[field] or "").split(" || ")) == sorted((observed[field] or "").split(" || "))
    return expected.get(field, "") == observed.get(field, "")


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = (len(ordered) - 1) * fraction
    low = math.floor(index)
    high = math.ceil(index)
    return ordered[low] if low == high else ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def choose_sample(projects: list[dict[str, str]], family_rows: list[dict[str, str]], structure: list[dict[str, str]], sample_size: int) -> list[dict[str, str]]:
    by_project = defaultdict(list)
    for row in structure:
        by_project[row["project_id"]].append(row)
    by_family = defaultdict(list)
    for row in family_rows:
        by_family[row["project_id"]].append(row)
    complexities = {pid: max(int(row.get("source_python_file_count") or 0) for row in rows) for pid, rows in by_project.items()}
    threshold = percentile([float(v) for v in complexities.values()], 0.75)
    change_score: dict[str, float] = {}
    for pid, rows in by_family.items():
        score = 0.0
        for row in rows:
            if row["family"] in {"api", "dependency"}:
                score += sum(float(row.get(f"{kind}_proportion") or 0.0) for kind in ("added", "removed", "changed"))
        change_score[pid] = score
    ranked_change = sorted(change_score, key=lambda pid: (-change_score[pid], pid))
    partial = [p["project_id"] for p in projects if any(row.get("paired_observation_complete") == "false" for row in by_family[p["project_id"]])]
    extension = [p["project_id"] for p in projects if p.get("publication_year", "") >= "2021"]
    large = sorted(projects, key=lambda p: (-complexities.get(p["project_id"], 0), p["project_id"]))
    branch = [p["project_id"] for p in projects if p.get("endpoint_ref_type") == "default_branch"]
    chosen: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(pid: str, stratum: str) -> None:
        if pid in seen or len(chosen) >= sample_size:
            return
        row = next(p for p in projects if p["project_id"] == pid)
        chosen.append({"project_id": pid, "selection_rank": row.get("selection_rank", ""), "stratum": stratum, "source_file_count_max": str(complexities.get(pid, 0)), "change_score": f"{change_score.get(pid, 0.0):.6f}", "endpoint_ref_type": row.get("endpoint_ref_type", "")})
        seen.add(pid)

    for pid in sorted(partial)[: max(1, sample_size // 5)]:
        add(pid, "parser_partial")
    for row in large[: max(1, sample_size // 5)]:
        add(row["project_id"], "large_source")
    for pid in ranked_change[: max(1, sample_size // 5)]:
        add(pid, "high_churn")
    for pid in sorted(branch)[: max(1, sample_size // 5)]:
        add(pid, "default_branch")
    for pid in sorted(extension, key=lambda pid: next(int(p.get("selection_rank") or 0) for p in projects if p["project_id"] == pid))[: max(1, sample_size // 5)]:
        add(pid, "extension_2021_2022")
    for row in sorted(projects, key=lambda p: (complexities.get(p["project_id"], 0), int(p.get("selection_rank") or 0))):
        if all(row_.get("paired_observation_complete") == "true" for row_ in by_family[row["project_id"]]):
            add(row["project_id"], "complete_reference")
    for row in sorted(projects, key=lambda p: int(p.get("selection_rank") or 0)):
        add(row["project_id"], "deterministic_fill")
    return chosen[:sample_size]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--dataset-dir", default="data/census_v0.3")
    parser.add_argument("--archive-root", type=Path, default=None,
                        help="directory that contains data/raw/...; defaults to root")
    parser.add_argument("--sample-size", type=int, default=32)
    args = parser.parse_args()
    root = args.root.resolve()
    archive_root = (args.archive_root or args.root).resolve()
    tables = root / args.dataset_dir / "tables"
    summary = root / args.dataset_dir / "summary_tables"
    projects = read_csv(tables / "project_records.csv")
    structures = read_csv(tables / "structure_records.csv")
    families = read_csv(summary / "project_family_delta_metrics.csv")
    sample = choose_sample(projects, families, structures, args.sample_size)
    sample_ids = {row["project_id"] for row in sample}
    write_csv(tables.parent / "independent_semantic_audit_sample.csv", sample, ["project_id", "selection_rank", "stratum", "source_file_count_max", "change_score", "endpoint_ref_type"])
    snapshots = [row for row in read_csv(tables / "snapshot_records.csv") if row["project_id"] in sample_ids]
    located = locate_archives(archive_root, snapshots)
    api_rows = defaultdict(list)
    for row in read_csv(tables / "api_symbol_records.csv"):
        if row["snapshot_id"] in {s["snapshot_id"] for s in snapshots}:
            api_rows[row["snapshot_id"]].append(row)
    dep_rows = defaultdict(list)
    for row in read_csv(tables / "dependency_records.csv"):
        if row["snapshot_id"] in {s["snapshot_id"] for s in snapshots}:
            dep_rows[row["snapshot_id"]].append(row)
    struct_rows = {row["snapshot_id"]: row for row in structures if row["snapshot_id"] in {s["snapshot_id"] for s in snapshots}}
    sample_meta = {row["project_id"]: row for row in sample}
    audit_rows: list[dict[str, str]] = []
    counters: Counter[str] = Counter()
    field_totals: Counter[str] = Counter()
    field_passes: Counter[str] = Counter()
    missing_archives: list[str] = []

    def record(snapshot: dict[str, str], kind: str, check: str, expected_count: int, observed_count: int, agreement: int, detail: str) -> None:
        rate = agreement / expected_count if expected_count else (1.0 if observed_count == 0 else 0.0)
        status = "pass" if rate == 1.0 and expected_count == observed_count else "partial" if agreement else "fail"
        audit_rows.append({"project_id": snapshot["project_id"], "snapshot_id": snapshot["snapshot_id"], "snapshot_role": snapshot["snapshot_role"], "stratum": sample_meta[snapshot["project_id"]]["stratum"], "parser_kind": kind, "check": check, "expected_count": str(expected_count), "independent_count": str(observed_count), "agreement_count": str(agreement), "agreement_rate": f"{rate:.6f}", "status": status, "detail": detail})
        counters[f"{kind}:{check}:expected"] += expected_count
        counters[f"{kind}:{check}:observed"] += observed_count
        counters[f"{kind}:{check}:agreement"] += agreement

    for snapshot in snapshots:
        sid = snapshot["snapshot_id"]
        path = located.get(sid)
        if path is None:
            missing_archives.append(sid)
            record(snapshot, "archive", "sha256_location", 1, 0, 0, "cached archive not found by size and SHA-256")
            continue
        record(snapshot, "archive", "sha256_location", 1, 1, 1, path.name)
        view = ArchiveView(path)
        observed_api, observed_source_count, observed_parse_failures = independent_api(view)
        expected_api = expected_api_map(api_rows[sid])
        shared = sorted(set(expected_api) & set(observed_api))
        record(snapshot, "api", "symbol_key", len(expected_api), len(observed_api), len(shared), f"missing={len(set(expected_api)-set(observed_api))};extra={len(set(observed_api)-set(expected_api))}")
        for field in ("symbol_kind", "source_file", "line_start", "signature", "decorators", "export_evidence"):
            agreement = sum(api_field_equal(expected_api[key], observed_api[key], field) for key in shared)
            record(snapshot, "api", field, len(shared), len(shared), agreement, "exact field agreement among shared symbol keys")
        observed_dep, observed_meta = independent_dependencies(view)
        expected_dep = expected_dependency_records(dep_rows[sid])
        exact_agreement = counter_agreement(expected_dep, observed_dep)
        record(
            snapshot,
            "dependency",
            "exact_record",
            len(expected_dep),
            len(observed_dep),
            exact_agreement,
            f"missing={len(expected_dep)-exact_agreement};extra={len(observed_dep)-exact_agreement}",
        )
        for field_idx, field in enumerate(DEPENDENCY_FIELDS):
            agreement = dependency_field_agreement(expected_dep, observed_dep, field_idx)
            record(
                snapshot,
                "dependency",
                field,
                len(expected_dep),
                len(observed_dep),
                agreement,
                "field agreement with all other dependency fields held fixed; duplicate rows retained",
            )
        expected_struct = struct_rows[sid]
        observed_struct = {
            "archive_type": view.archive_type,
            "archive_member_count": str(len(view.names)),
            **observed_meta,
            "ci_providers": json_compact(sorted(
                (["github_actions"] if any(n.lower().startswith(".github/workflows/") and n.lower().endswith((".yml", ".yaml")) for n in view.names) else [])
                + [provider for filename, provider in ((".travis.yml", "travis"), ("appveyor.yml", "appveyor"), ("azure-pipelines.yml", "azure_pipelines"), ("circle.yml", "circleci"))
                   if any(len(PurePosixPath(n).parts) == 1 and PurePosixPath(n).name.lower() == filename for n in view.names)]
            )),
            "source_python_file_count": str(observed_source_count),
            "source_python_line_count": str(sum(len(view.texts.get(name, "").splitlines()) for name in source_files(view.names))),
            "test_python_file_count": str(sum(1 for name in view.names if name.lower().endswith(".py") and (any(part.lower() in {"test", "tests", "testing"} for part in PurePosixPath(name).parts) or PurePosixPath(name).name.lower().startswith("test_")))),
            "ci_workflow_file_count": str(sum(1 for name in view.names if name.lower().startswith(".github/workflows/") and name.lower().endswith((".yml", ".yaml")))),
        }
        lower_names = [(name, PurePosixPath(name).name.lower()) for name in view.names]
        for key, patterns in GOVERNANCE.items():
            observed_struct[key] = str(any(base in patterns and (len(PurePosixPath(name).parts) == 1 or PurePosixPath(name).parts[0].lower() == ".github") for name, base in lower_names)).lower()
        for field in ("archive_type", "archive_member_count", "packaging_files", "build_backends", "python_constraints", "ci_providers", "dependency_observation_status", "python_constraint_observation_status", "build_backend_observation_status", "source_python_file_count", "source_python_line_count", "test_python_file_count", "ci_workflow_file_count", *GOVERNANCE):
            agreement = int(expected_struct.get(field, "") == observed_struct.get(field, ""))
            record(snapshot, "structure", field, 1, 1, agreement, f"expected={expected_struct.get(field,'')};independent={observed_struct.get(field,'')}")

    fields = ["project_id", "snapshot_id", "snapshot_role", "stratum", "parser_kind", "check", "expected_count", "independent_count", "agreement_count", "agreement_rate", "status", "detail"]
    write_csv(tables.parent / "independent_semantic_audit.csv", audit_rows, fields)
    for key, total in counters.items():
        if key.endswith(":expected") or key.endswith(":observed") or key.endswith(":agreement"):
            continue
    def aggregate(kind: str, check: str) -> dict[str, Any]:
        base = f"{kind}:{check}"
        expected = counters[f"{base}:expected"]
        observed = counters[f"{base}:observed"]
        agreement = counters[f"{base}:agreement"]
        return {"expected": expected, "independent": observed, "agreement": agreement, "agreement_rate": round(agreement / expected, 6) if expected else 1.0}

    summary_out = {
        "status": "PASS" if not missing_archives and all(row["status"] == "pass" for row in audit_rows) else "FAIL",
        "sampled_projects": len(sample),
        "sampled_snapshots": len(snapshots),
        "sample_strata": dict(Counter(row["stratum"] for row in sample)),
        "located_archives": len(located),
        "missing_archives": missing_archives,
        "api": {check: aggregate("api", check) for check in ("symbol_key", "symbol_kind", "source_file", "line_start", "signature", "decorators", "export_evidence")},
        "dependency": {check: aggregate("dependency", check) for check in ("exact_record", *DEPENDENCY_FIELDS)},
        "structure": {check: aggregate("structure", check) for check in ("archive_type", "archive_member_count", "packaging_files", "build_backends", "python_constraints", "ci_providers", "dependency_observation_status", "python_constraint_observation_status", "build_backend_observation_status", "source_python_file_count", "source_python_line_count", "test_python_file_count", "ci_workflow_file_count", *GOVERNANCE)},
        "unaudited_released_fields": [
            "api_observation_status and source_line_observation_status (derived from the API pass, not re-derived)",
            "content_tree_sha256 and member manifests (recomputed by validate_census_dataset.py, not here)",
            "endpoint resolution fields (checked against the GitHub API only by check_retrospective_agreement.py)",
        ],
        "scope": "stratified exact-agreement audit of released fields; not a population accuracy estimate",
        "parser_independence": "does not import scripts/extract_version_pairs.py",
        "output_csv": f"{args.dataset_dir}/independent_semantic_audit.csv",
        "limitation": "written by the same authors from the same specification; shared misreadings of the specification are not detected here (see the reference-based audit)",
    }
    (tables.parent / "independent_semantic_audit.json").write_text(json.dumps(summary_out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary_out, indent=2, ensure_ascii=False))
    return 0 if summary_out["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
