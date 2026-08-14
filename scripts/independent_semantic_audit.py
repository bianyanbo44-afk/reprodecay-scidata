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


class ArchiveView:
    def __init__(self, path: Path) -> None:
        self.path = path
        raw_names: list[str] = []
        raw_text: dict[str, bytes] = {}
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    if archive_junk(info.filename):
                        continue
                    raw_names.append(info.filename)
                    if interesting(info.filename) and info.file_size <= 2 * 1024 * 1024:
                        raw_text[info.filename] = archive.read(info)
            self.archive_type = "zip"
        elif tarfile.is_tarfile(path):
            with tarfile.open(path, "r:*") as archive:
                for member in archive.getmembers():
                    if member.isdir():
                        continue
                    if archive_junk(member.name):
                        continue
                    raw_names.append(member.name)
                    if member.isfile() and interesting(member.name) and member.size <= 2 * 1024 * 1024:
                        handle = archive.extractfile(member)
                        if handle is not None:
                            raw_text[member.name] = handle.read(2 * 1024 * 1024)
            self.archive_type = "tar"
        else:
            raise ValueError(f"unsupported_archive:{path}")
        normalized = strip_root(raw_names)
        self.names = sorted(set(normalized.values()))
        self.texts = {
            normalized[raw]: payload.decode("utf-8", errors="replace")
            for raw, payload in raw_text.items()
            if raw in normalized
        }


def locate_archives(root: Path, snapshots: list[dict[str, str]]) -> dict[str, Path]:
    bases = [root / "data/raw/endpoint_archives_v0.2", root / "data/raw/zenodo_archives_v0.2"]
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


def requirement_parts(raw: str, source_kind: str) -> tuple[str, str, str, str, str]:
    candidate = str(raw).strip()
    if not candidate or candidate.startswith("#"):
        return "", "", "", "", "skip"
    if candidate.startswith(("-r ", "--requirement ", "-c ", "--constraint ", "--index", "--extra-index")):
        return "", "", "", "", "directive"
    if " #" in candidate:
        candidate = candidate.split(" #", 1)[0].strip()
    if source_kind.startswith("poetry"):
        match = re.match(r"^([A-Za-z0-9_.-]+)(.*)$", candidate)
        if match and match.group(2).startswith(("^", "~")):
            return canonicalize_name(match.group(1)), match.group(2), "", "", "parsed_poetry"
    if Requirement is not None:
        try:
            req = Requirement(candidate)
            return canonicalize_name(req.name), str(req.specifier), str(req.marker or ""), ";".join(sorted(req.extras)), "parsed"
        except Exception:
            pass
    match = re.match(r"^([A-Za-z0-9_.-]+)\s*([<>=!~].*)?$", candidate)
    if match:
        return canonicalize_name(match.group(1)), match.group(2) or "", "", "", "parsed_non_pep508"
    return "", "", "", "", "unparsed"


def dep_row(source_file: str, source_kind: str, group: str, raw: Any) -> dict[str, str] | None:
    raw_text = str(raw).strip()
    name, specifier, marker, extras, status = requirement_parts(raw_text, source_kind)
    if status == "skip":
        return None
    return {
        "source_file": source_file,
        "source_kind": source_kind,
        "dependency_group": group,
        "normalized_name": name,
        "raw_declaration": raw_text,
        "specifier": specifier,
        "marker": marker,
        "extras": extras,
        "parse_status": status,
    }


def literal_value(node: ast.AST, env: dict[str, Any]) -> Any:
    if isinstance(node, ast.Name) and node.id in env:
        return env[node.id]
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def parse_setup_py(text: str, source_file: str) -> tuple[list[dict[str, str]], list[str]]:
    try:
        tree = ast.parse(text, filename=source_file)
    except SyntaxError:
        return [], ["syntax"]
    env: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and node.targets and isinstance(node.targets[0], ast.Name):
            value = literal_value(node.value, env)
            if value is not None:
                env[node.targets[0].id] = value
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and ((isinstance(node.func, ast.Name) and node.func.id == "setup") or (isinstance(node.func, ast.Attribute) and node.func.attr == "setup"))]
    if not calls:
        return [], ["setup_call_not_found"]
    values = {kw.arg: literal_value(kw.value, env) for kw in calls[-1].keywords if kw.arg}
    rows: list[dict[str, str]] = []
    issues: list[str] = []
    install = values.get("install_requires")
    if isinstance(install, str):
        install = [line.strip() for line in install.splitlines() if line.strip()]
    if isinstance(install, (list, tuple)):
        for value in install:
            row = dep_row(source_file, "setup_py", "runtime", value)
            if row:
                rows.append(row)
    elif "install_requires" in values:
        issues.append("install_requires_nonliteral")
    extras = values.get("extras_require")
    if isinstance(extras, dict):
        for group, values_ in extras.items():
            if isinstance(values_, str):
                values_ = [line.strip() for line in values_.splitlines() if line.strip()]
            if isinstance(values_, (list, tuple)):
                for value in values_:
                    row = dep_row(source_file, "setup_py", f"optional:{group}", value)
                    if row:
                        rows.append(row)
    elif "extras_require" in values:
        issues.append("extras_require_nonliteral")
    return rows, issues


def parse_pyproject(text: str, source_file: str) -> tuple[list[dict[str, str]], list[str]]:
    try:
        data = tomllib.loads(text)
    except Exception as exc:
        return [], [f"toml:{type(exc).__name__}"]
    rows: list[dict[str, str]] = []
    build = data.get("build-system") or {}
    if isinstance(build, dict):
        for value in build.get("requires") or []:
            row = dep_row(source_file, "pep517", "build", value)
            if row:
                rows.append(row)
    project = data.get("project") or {}
    if isinstance(project, dict):
        for value in project.get("dependencies") or []:
            row = dep_row(source_file, "pep621", "runtime", value)
            if row:
                rows.append(row)
        optional = project.get("optional-dependencies") or {}
        if isinstance(optional, dict):
            for group, values in optional.items():
                for value in values or []:
                    row = dep_row(source_file, "pep621", f"optional:{group}", value)
                    if row:
                        rows.append(row)
    tool = data.get("tool") or {}
    poetry = tool.get("poetry") if isinstance(tool, dict) else {}
    poetry = poetry if isinstance(poetry, dict) else {}
    dependencies = poetry.get("dependencies") or {}
    if isinstance(dependencies, dict):
        for name, value in dependencies.items():
            if str(name).lower() == "python":
                continue
            if isinstance(value, str):
                raw = f"{name}{value if value != '*' else ''}"
            elif isinstance(value, dict):
                version = value.get("version", "")
                raw = f"{name}{version if version != '*' else ''}"
            else:
                raw = str(name)
            row = dep_row(source_file, "poetry", "runtime", raw)
            if row:
                rows.append(row)
    groups = poetry.get("group") or {}
    if isinstance(groups, dict):
        for group_name, group_data in groups.items():
            values = group_data.get("dependencies") or {} if isinstance(group_data, dict) else {}
            for name, value in values.items():
                version = value if isinstance(value, str) else value.get("version", "") if isinstance(value, dict) else ""
                row = dep_row(source_file, "poetry_group", "development" if group_name == "dev" else f"optional:{group_name}", f"{name}{version if version != '*' else ''}")
                if row:
                    rows.append(row)
    return rows, []


def parse_setup_cfg(text: str, source_file: str) -> tuple[list[dict[str, str]], list[str]]:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except Exception as exc:
        return [], [f"cfg:{type(exc).__name__}"]
    rows: list[dict[str, str]] = []
    if parser.has_option("options", "install_requires"):
        for line in parser.get("options", "install_requires").splitlines():
            row = dep_row(source_file, "setup_cfg", "runtime", line)
            if row:
                rows.append(row)
    if parser.has_section("options.extras_require"):
        for group, raw_values in parser.items("options.extras_require"):
            for line in raw_values.splitlines():
                row = dep_row(source_file, "setup_cfg", f"optional:{group}", line)
                if row:
                    rows.append(row)
    return rows, []


def parse_environment(text: str, source_file: str) -> tuple[list[dict[str, str]], list[str]]:
    if yaml is None:
        return [], ["yaml_unavailable"]
    try:
        data = yaml.safe_load(text) or {}
    except Exception as exc:
        return [], [f"yaml:{type(exc).__name__}"]
    rows: list[dict[str, str]] = []
    values = data.get("dependencies") or [] if isinstance(data, dict) else []
    for value in values:
        if isinstance(value, str):
            if re.match(r"^python(?:\s|[<>=!~])", value, flags=re.IGNORECASE):
                continue
            row = dep_row(source_file, "conda_environment", "runtime", value)
            if row:
                rows.append(row)
        elif isinstance(value, dict):
            for group, items in value.items():
                for item in items or []:
                    row = dep_row(source_file, f"conda_{group}", "runtime", item)
                    if row:
                        rows.append(row)
    return rows, []


DEPENDENCY_FIELDS = (
    "source_file",
    "source_kind",
    "dependency_group",
    "normalized_name",
    "raw_declaration",
    "specifier",
    "marker",
    "extras",
    "parse_status",
)


def independent_dependencies(view: ArchiveView) -> tuple[list[tuple[str, ...]], list[str]]:
    rows: list[dict[str, str]] = []
    issues: list[str] = []
    for name, text in sorted(view.texts.items()):
        path = PurePosixPath(name)
        if len(path.parts) != 1:
            continue
        base = path.name.lower()
        parsed: list[dict[str, str]] = []
        if base == "pyproject.toml":
            parsed, found = parse_pyproject(text, name)
        elif base == "setup.cfg":
            parsed, found = parse_setup_cfg(text, name)
        elif base == "setup.py":
            parsed, found = parse_setup_py(text, name)
        elif base.startswith("requirements") and base.endswith((".txt", ".in")):
            group = "test" if "test" in base else "development" if any(token in base for token in ("dev", "lint", "docs", "doc")) else "runtime"
            for line in text.splitlines():
                row = dep_row(name, "requirements", group, line)
                if row:
                    parsed.append(row)
            found = []
        elif base in {"environment.yml", "environment.yaml"}:
            parsed, found = parse_environment(text, name)
        else:
            continue
        rows.extend(parsed)
        issues.extend(found)
    return [tuple(row[field] for field in DEPENDENCY_FIELDS) for row in rows], issues


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
    partial = [p["project_id"] for p in projects if any(row.get("paired_parse_complete") == "false" for row in by_family[p["project_id"]])]
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

    for pid in sorted(partial)[: max(1, sample_size // 4)]:
        add(pid, "parser_partial")
    for row in large[: max(1, sample_size // 4)]:
        add(row["project_id"], "large_source")
    for pid in ranked_change[: max(1, sample_size // 4)]:
        add(pid, "high_churn")
    for pid in sorted(branch)[: max(1, sample_size // 4)]:
        add(pid, "default_branch")
    for row in sorted(projects, key=lambda p: (complexities.get(p["project_id"], 0), int(p.get("selection_rank") or 0))):
        if all(row_.get("paired_parse_complete") == "true" for row_ in by_family[row["project_id"]]):
            add(row["project_id"], "complete_reference")
    for row in sorted(projects, key=lambda p: int(p.get("selection_rank") or 0)):
        add(row["project_id"], "deterministic_fill")
    return chosen[:sample_size]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--sample-size", type=int, default=24)
    args = parser.parse_args()
    root = args.root.resolve()
    tables = root / "data/census_v0.2/tables"
    summary = root / "data/census_v0.2/summary_tables"
    projects = read_csv(tables / "project_records.csv")
    structures = read_csv(tables / "structure_records.csv")
    families = read_csv(summary / "project_family_delta_metrics.csv")
    sample = choose_sample(projects, families, structures, args.sample_size)
    sample_ids = {row["project_id"] for row in sample}
    write_csv(tables.parent / "independent_semantic_audit_sample.csv", sample, ["project_id", "selection_rank", "stratum", "source_file_count_max", "change_score", "endpoint_ref_type"])
    snapshots = [row for row in read_csv(tables / "snapshot_records.csv") if row["project_id"] in sample_ids]
    located = locate_archives(root, snapshots)
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
        record(snapshot, "archive", "sha256_location", 1, 1, 1, str(path.relative_to(root)))
        view = ArchiveView(path)
        observed_api, observed_source_count, observed_parse_failures = independent_api(view)
        expected_api = expected_api_map(api_rows[sid])
        shared = sorted(set(expected_api) & set(observed_api))
        record(snapshot, "api", "symbol_key", len(expected_api), len(observed_api), len(shared), f"missing={len(set(expected_api)-set(observed_api))};extra={len(set(observed_api)-set(expected_api))}")
        for field in ("symbol_kind", "source_file", "line_start", "signature", "decorators", "export_evidence"):
            agreement = sum(api_field_equal(expected_api[key], observed_api[key], field) for key in shared)
            record(snapshot, "api", field, len(shared), len(shared), agreement, "exact field agreement among shared symbol keys")
        observed_dep, dep_issues = independent_dependencies(view)
        expected_dep = expected_dependency_records(dep_rows[sid])
        exact_agreement = counter_agreement(expected_dep, observed_dep)
        record(
            snapshot,
            "dependency",
            "exact_record",
            len(expected_dep),
            len(observed_dep),
            exact_agreement,
            f"missing={len(expected_dep)-exact_agreement};extra={len(observed_dep)-exact_agreement};issues={','.join(dep_issues)}",
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
            "packaging_files": json_compact(sorted(name for name in view.names if len(PurePosixPath(name).parts) == 1 and (PurePosixPath(name).name.lower() in {"pyproject.toml", "setup.py", "setup.cfg"} or PurePosixPath(name).name.lower().startswith("requirements") or PurePosixPath(name).name.lower() in {"environment.yml", "environment.yaml"}))),
            "source_python_file_count": str(observed_source_count),
            "source_python_line_count": str(sum(len(view.texts.get(name, "").splitlines()) for name in source_files(view.names))),
            "test_python_file_count": str(sum(1 for name in view.names if name.lower().endswith(".py") and (any(part.lower() in {"test", "tests", "testing"} for part in PurePosixPath(name).parts) or PurePosixPath(name).name.lower().startswith("test_")))),
            "ci_workflow_file_count": str(sum(1 for name in view.names if name.lower().startswith(".github/workflows/") and name.lower().endswith((".yml", ".yaml")))),
        }
        lower_names = [(name, PurePosixPath(name).name.lower()) for name in view.names]
        for key, patterns in GOVERNANCE.items():
            observed_struct[key] = str(any(base in patterns and (len(PurePosixPath(name).parts) == 1 or PurePosixPath(name).parts[0].lower() == ".github") for name, base in lower_names)).lower()
        for field in ("archive_type", "archive_member_count", "packaging_files", "source_python_file_count", "source_python_line_count", "test_python_file_count", "ci_workflow_file_count", *GOVERNANCE):
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
        "dependency": {check: aggregate("dependency", check) for check in ("exact_record", "source_file", "source_kind", "dependency_group", "normalized_name", "raw_declaration", "specifier", "marker", "extras", "parse_status")},
        "structure": {check: aggregate("structure", check) for check in ("archive_type", "archive_member_count", "packaging_files", "source_python_file_count", "source_python_line_count", "test_python_file_count", "ci_workflow_file_count", *GOVERNANCE)},
        "scope": "stratified exact-agreement audit of released fields; not a population accuracy estimate",
        "parser_independence": "does not import scripts/extract_version_pairs.py",
        "output_csv": "data/census_v0.2/independent_semantic_audit.csv",
    }
    (tables.parent / "independent_semantic_audit.json").write_text(json.dumps(summary_out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary_out, indent=2, ensure_ascii=False))
    return 0 if summary_out["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
