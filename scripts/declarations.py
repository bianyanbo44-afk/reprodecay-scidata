"""Static extraction of dependency declarations (specification v0.3, Sections 2-5).

Every declaration field that is read, or that is a core field of a parsed
``setup.py``/``setup.cfg``, yields one field row with an observation status, so
that confirmed absence, build-time (dynamic) metadata, unsupported syntax,
partial extraction and parse failures stay distinguishable. Nothing is
imported, installed or executed.
"""

from __future__ import annotations

import ast
import configparser
import json
import re
import tomllib
from pathlib import PurePosixPath
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - recorded as a parser limitation
    yaml = None

try:
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
except ImportError:  # pragma: no cover - local fallback is deterministic
    Requirement = None

    def canonicalize_name(value: str) -> str:
        return re.sub(r"[-_.]+", "-", value).lower()


PARSED_STATUSES = {"parsed", "parsed_poetry", "parsed_pipfile", "parsed_non_pep508", "parsed_url"}
COMPLETE_FIELD_STATUSES = {"extracted", "confirmed_absent"}
DIRECTIVE_PREFIXES = (
    "-r", "--requirement", "-c", "--constraint", "-i", "--index-url", "--extra-index-url",
    "-f", "--find-links", "--trusted-host", "--pre", "--no-binary", "--only-binary",
    "--prefer-binary", "--use-feature",
)
SETUP_FIELDS = {
    "install_requires": "runtime",
    "extras_require": "optional",
    "setup_requires": "build",
    "tests_require": "test",
    "python_requires": "",
}
LIST_MUTATORS = {"append", "extend", "insert", "remove", "pop", "clear", "update", "setdefault", "__setitem__"}
CONDA_PATTERN = re.compile(r"^(?:[A-Za-z0-9_.-]+::)?([A-Za-z0-9_.-]+)(?:\s*([<>=!~]=?.*)|\s+(\S.*))?$")
EGG_PATTERN = re.compile(r"#egg=([A-Za-z0-9_.-]+)")


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def poetry_group(name: str) -> str:
    lower = name.lower()
    if lower in {"dev", "docs", "doc", "lint"}:
        return "development"
    if lower in {"test", "tests", "testing"}:
        return "test"
    return f"group:{name}"


def requirements_group(filename: str) -> str:
    stem = PurePosixPath(filename).name.lower()
    if "test" in stem:
        return "test"
    if any(token in stem for token in ("dev", "lint", "docs", "doc")):
        return "development"
    return "runtime"


class Unresolved(Exception):
    """A setup.py value outside the statically evaluable subset (Section 2.1)."""


class Declarations:
    """Collects dependency rows, field rows and Python/backend observations for one snapshot."""

    def __init__(self, snapshot: dict[str, str]) -> None:
        self.snapshot = snapshot
        self.dependency_rows: list[dict[str, str]] = []
        self.field_rows: list[dict[str, str]] = []
        self.python_constraints: list[str] = []
        self.build_backends: list[str] = []
        self.declaration_files: list[str] = []

    # -- rows -----------------------------------------------------------------

    def _dependency(self, source_file: str, source_kind: str, group: str, name: str, raw: str,
                    specifier: str, marker: str, extras: str, attributes: str, status: str,
                    error: str = "") -> dict[str, str]:
        row = {
            "snapshot_id": self.snapshot["snapshot_id"],
            "project_id": self.snapshot["project_id"],
            "snapshot_role": self.snapshot["snapshot_role"],
            "source_file": source_file,
            "source_kind": source_kind,
            "dependency_group": group,
            "normalized_name": name,
            "raw_declaration": raw,
            "specifier": specifier,
            "marker": marker,
            "extras": extras,
            "attributes": attributes,
            "parse_status": status,
            "parse_error": error,
        }
        self.dependency_rows.append(row)
        return row

    def field(self, source_file: str, source_kind: str, field: str, field_kind: str, status: str,
              rows: list[dict[str, str]] | None = None, detail: str = "") -> None:
        rows = rows or []
        self.field_rows.append(
            {
                "snapshot_id": self.snapshot["snapshot_id"],
                "project_id": self.snapshot["project_id"],
                "snapshot_role": self.snapshot["snapshot_role"],
                "source_file": source_file,
                "source_kind": source_kind,
                "field": field,
                "field_kind": field_kind,
                "observation_status": status,
                "entry_count": str(len(rows)),
                "unparsed_entry_count": str(sum(row["parse_status"] == "unparsed" for row in rows)),
                "detail": detail[:300],
            }
        )

    def extracted_field(self, source_file: str, source_kind: str, field: str,
                        rows: list[dict[str, str]], unresolved_entries: int = 0) -> None:
        partial = unresolved_entries or any(row["parse_status"] == "unparsed" for row in rows)
        detail = f"{unresolved_entries} entries not statically evaluable" if unresolved_entries else ""
        self.field(source_file, source_kind, field, "dependency",
                   "extracted_partial" if partial else "extracted", rows, detail)

    def python_field(self, source_file: str, source_kind: str, field: str, value: Any) -> None:
        if value is None:
            self.field(source_file, source_kind, field, "python_constraint", "confirmed_absent")
        elif isinstance(value, str):
            self.python_constraints.append(f"{source_file}:{value.strip()}")
            self.field(source_file, source_kind, field, "python_constraint", "extracted")
        else:
            self.field(source_file, source_kind, field, "python_constraint", "unsupported_syntax",
                       detail=f"value type {type(value).__name__}")

    # -- entries --------------------------------------------------------------

    def requirement(self, source_file: str, source_kind: str, group: str, raw: Any) -> dict[str, str] | None:
        """Section 3.1. Returns None for blank and comment lines."""
        raw_text = str(raw).strip()
        if not raw_text or raw_text.startswith("#"):
            return None
        candidate = raw_text.split(" #", 1)[0].strip()
        lowered = candidate.lower()
        if lowered.startswith(("-e ", "-e\t", "--editable ", "--editable=")):
            target = re.split(r"[\s=]", candidate, maxsplit=1)[1].strip() if re.search(r"[\s=]", candidate) else ""
            egg = EGG_PATTERN.search(target)
            if "://" in target or target.startswith(("git+", "hg+", "svn+", "bzr+")):
                if egg:
                    return self._dependency(source_file, source_kind, group, canonicalize_name(egg.group(1)),
                                            raw_text, "", "", "", "", "parsed_url")
                return self._dependency(source_file, source_kind, group, "", raw_text, "", "", "", "", "unparsed",
                                        "editable_url_without_egg_name")
            return self._dependency(source_file, source_kind, group, "", raw_text, "", "", "", "", "directive")
        if any(lowered == prefix or lowered.startswith((prefix + " ", prefix + "=")) for prefix in DIRECTIVE_PREFIXES):
            return self._dependency(source_file, source_kind, group, "", raw_text, "", "", "", "", "directive")
        if " --" in candidate:
            candidate = candidate.split(" --", 1)[0].strip()
        egg = EGG_PATTERN.search(candidate)
        if egg and ("://" in candidate.split("#", 1)[0]) and " @ " not in candidate:
            return self._dependency(source_file, source_kind, group, canonicalize_name(egg.group(1)),
                                    raw_text, "", "", "", "", "parsed_url")
        try:
            if Requirement is None:
                raise ValueError("packaging_library_unavailable")
            requirement = Requirement(candidate)
            return self._dependency(
                source_file, source_kind, group, canonicalize_name(requirement.name), raw_text,
                str(requirement.specifier), str(requirement.marker or ""),
                ";".join(sorted(requirement.extras)), "", "parsed",
            )
        except Exception as exc:
            conda = CONDA_PATTERN.match(candidate)
            if conda:
                specifier = conda.group(2) or conda.group(3) or ""
                return self._dependency(source_file, source_kind, group, canonicalize_name(conda.group(1)),
                                        raw_text, specifier.strip(), "", "", "", "parsed_non_pep508")
            return self._dependency(source_file, source_kind, group, "", raw_text, "", "", "", "", "unparsed",
                                    f"{type(exc).__name__}:{str(exc)[:200]}")

    def table_entry(self, source_file: str, source_kind: str, group: str, name: str, value: Any,
                    status: str) -> dict[str, str]:
        """Section 3.2: Poetry and Pipfile table entries."""
        raw = f"{name} = {json_text(value)}"
        normalized = canonicalize_name(str(name))
        if isinstance(value, str):
            return self._dependency(source_file, source_kind, group, normalized, raw,
                                    "" if value.strip() == "*" else value.strip(), "", "", "", status)
        if isinstance(value, dict):
            version = str(value.get("version", "")).strip()
            attributes = {key: item for key, item in value.items() if key != "version"}
            extras = value.get("extras") or []
            return self._dependency(
                source_file, source_kind, group, normalized, raw,
                "" if version == "*" else version,
                str(value.get("markers", "")),
                ";".join(sorted(str(item) for item in extras)) if isinstance(extras, list) else str(extras),
                json_text(attributes) if attributes else "", status,
            )
        if isinstance(value, list) and all(isinstance(item, dict) for item in value):
            versions = [str(item.get("version", "")).strip() for item in value]
            return self._dependency(source_file, source_kind, group, normalized, raw,
                                    " || ".join(versions), "", "", json_text({"constraints": value}), status)
        return self._dependency(source_file, source_kind, group, normalized, raw, "", "", "", "", "unparsed",
                                f"unexpected_value_type:{type(value).__name__}")

    # -- statuses (Section 5) -------------------------------------------------

    def dependency_observation_status(self) -> tuple[str, list[str]]:
        if not self.declaration_files:
            return "no_declaration_file", []
        reasons = sorted({
            f"{row['source_file']}:{row['field']}:{row['observation_status']}"
            for row in self.field_rows
            if row["field_kind"] in {"dependency", "file", "setup_kwargs"}
            and row["observation_status"] not in COMPLETE_FIELD_STATUSES
        })
        return ("incomplete" if reasons else "complete"), reasons

    def python_constraint_observation_status(self) -> str:
        for row in self.field_rows:
            if row["field_kind"] in {"python_constraint", "file", "setup_kwargs"} and \
                    row["observation_status"] not in COMPLETE_FIELD_STATUSES:
                return "incomplete"
        return "complete"


# -- requirements files and conda environments -------------------------------


def logical_lines(text: str) -> list[str]:
    lines: list[str] = []
    buffer = ""
    for line in text.splitlines():
        if line.rstrip().endswith("\\"):
            buffer += line.rstrip()[:-1] + " "
            continue
        lines.append(buffer + line)
        buffer = ""
    if buffer:
        lines.append(buffer)
    return lines


INCLUDE_PATTERN = re.compile(r"^(?:-r|--requirement)(?:\s+|=)(\S+)")


def extract_requirements(text: str, declarations: Declarations, source_file: str,
                         texts: dict[str, str] | None = None) -> None:
    """Section 3.1. `-r` includes of non-root files inside the archive are followed."""
    group = requirements_group(source_file)
    rows: list[dict[str, str]] = []
    missing: list[str] = []

    def read(path: str, kind: str, seen: frozenset[str]) -> None:
        for line in logical_lines((texts or {}).get(path, "") if path != source_file else text):
            row = declarations.requirement(path, kind, group, line)
            if row is None:
                continue
            rows.append(row)
            include = INCLUDE_PATTERN.match(line.strip())
            if not include:
                continue
            target = str(PurePosixPath(path).parent / include.group(1)).replace("\\", "/").removeprefix("./")
            target = PurePosixPath(target).as_posix()
            if len(PurePosixPath(target).parts) == 1 and declaration_kind(target) == "requirements":
                continue  # a root-level requirements file is read on its own
            if target in seen:
                continue
            if texts is None or target not in texts:
                missing.append(target)
                continue
            read(target, "requirements_include", seen | {target})

    read(source_file, "requirements", frozenset({source_file}))
    if missing:
        declarations.field(source_file, "requirements", "requirements", "dependency", "extracted_partial", rows,
                           detail="include not found in archive: " + ";".join(sorted(set(missing))))
    elif rows:
        declarations.extracted_field(source_file, "requirements", "requirements", rows)
    else:
        declarations.field(source_file, "requirements", "requirements", "dependency", "confirmed_absent")


def extract_environment_yml(text: str, declarations: Declarations, source_file: str) -> None:
    kind = "conda_environment"
    if yaml is None:
        declarations.field(source_file, kind, "(file)", "file", "parse_failure", detail="pyyaml_unavailable")
        return
    try:
        data = yaml.safe_load(text) or {}
    except Exception as exc:
        declarations.field(source_file, kind, "(file)", "file", "parse_failure",
                           detail=f"{type(exc).__name__}:{str(exc)[:200]}")
        return
    dependencies = data.get("dependencies") if isinstance(data, dict) else None
    if dependencies is None:
        declarations.field(source_file, kind, "dependencies", "dependency", "confirmed_absent")
        declarations.field(source_file, kind, "dependencies.python", "python_constraint", "confirmed_absent")
        return
    if not isinstance(dependencies, list):
        declarations.field(source_file, kind, "dependencies", "dependency", "unsupported_syntax",
                           detail=f"value type {type(dependencies).__name__}")
        declarations.field(source_file, kind, "dependencies.python", "python_constraint", "unsupported_syntax")
        return
    rows: list[dict[str, str]] = []
    unresolved = 0
    python_seen = False
    for value in dependencies:
        if isinstance(value, str):
            if re.match(r"^(?:[A-Za-z0-9_.-]+::)?python(?:\s|[<>=!~]|$)", value.strip(), flags=re.IGNORECASE):
                declarations.python_constraints.append(f"{source_file}:{value.strip()}")
                python_seen = True
            elif (row := declarations.requirement(source_file, kind, "runtime", value)) is not None:
                rows.append(row)
        elif isinstance(value, dict):
            for subgroup, items in value.items():
                if isinstance(items, list):
                    for item in items:
                        if (row := declarations.requirement(source_file, f"conda_{subgroup}", "runtime", item)) is not None:
                            rows.append(row)
                else:
                    unresolved += 1
        else:
            unresolved += 1
    declarations.extracted_field(source_file, kind, "dependencies", rows, unresolved)
    declarations.field(source_file, kind, "dependencies.python", "python_constraint",
                       "extracted" if python_seen else "confirmed_absent")


# -- pyproject.toml and Pipfile -------------------------------------------------


def _string_list(declarations: Declarations, source_file: str, kind: str, field: str, group: str,
                 value: Any) -> None:
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        rows = [row for item in value if (row := declarations.requirement(source_file, kind, group, item)) is not None]
        declarations.extracted_field(source_file, kind, field, rows)
    else:
        declarations.field(source_file, kind, field, "dependency", "unsupported_syntax",
                           detail=f"value type {type(value).__name__}")


def extract_pyproject(text: str, declarations: Declarations, source_file: str) -> None:
    try:
        data = tomllib.loads(text)
    except Exception as exc:
        declarations.field(source_file, "pyproject", "(file)", "file", "parse_failure",
                           detail=f"{type(exc).__name__}:{str(exc)[:200]}")
        return
    build = data.get("build-system")
    if isinstance(build, dict):
        if build.get("build-backend"):
            declarations.build_backends.append(str(build["build-backend"]))
        if "requires" in build:
            _string_list(declarations, source_file, "pep517", "build-system.requires", "build", build["requires"])

    project = data.get("project")
    if isinstance(project, dict):
        dynamic = project.get("dynamic") or []
        dynamic = set(dynamic) if isinstance(dynamic, list) else set()
        if "dependencies" in dynamic:
            declarations.field(source_file, "pep621", "project.dependencies", "dependency", "dynamic_metadata",
                               detail="listed in project.dynamic")
        elif "dependencies" in project:
            _string_list(declarations, source_file, "pep621", "project.dependencies", "runtime", project["dependencies"])
        else:
            declarations.field(source_file, "pep621", "project.dependencies", "dependency", "confirmed_absent")
        if "optional-dependencies" in dynamic:
            declarations.field(source_file, "pep621", "project.optional-dependencies", "dependency",
                               "dynamic_metadata", detail="listed in project.dynamic")
        elif isinstance(project.get("optional-dependencies"), dict):
            for extra, values in project["optional-dependencies"].items():
                _string_list(declarations, source_file, "pep621", f"project.optional-dependencies.{extra}",
                             f"optional:{extra}", values)
        elif "optional-dependencies" in project:
            declarations.field(source_file, "pep621", "project.optional-dependencies", "dependency",
                               "unsupported_syntax")
        if "requires-python" in dynamic:
            declarations.field(source_file, "pep621", "project.requires-python", "python_constraint",
                               "dynamic_metadata", detail="listed in project.dynamic")
        else:
            declarations.python_field(source_file, "pep621", "project.requires-python", project.get("requires-python"))

    groups = data.get("dependency-groups")
    if isinstance(groups, dict):
        # PEP 735. An include-group entry is a directive: the included group is extracted as its own field.
        for name, items in groups.items():
            field = f"dependency-groups.{name}"
            if not isinstance(items, list):
                declarations.field(source_file, "pep735", field, "dependency", "unsupported_syntax")
                continue
            rows: list[dict[str, str]] = []
            unresolved = 0
            for item in items:
                if isinstance(item, str):
                    if (row := declarations.requirement(source_file, "pep735", poetry_group(str(name)), item)) is not None:
                        rows.append(row)
                elif isinstance(item, dict) and set(item) == {"include-group"}:
                    rows.append(declarations._dependency(source_file, "pep735", poetry_group(str(name)), "",
                                                         json_text(item), "", "", "", "", "directive"))
                else:
                    unresolved += 1
            declarations.extracted_field(source_file, "pep735", field, rows, unresolved)
    elif "dependency-groups" in data:
        declarations.field(source_file, "pep735", "dependency-groups", "dependency", "unsupported_syntax")

    tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
    poetry = tool.get("poetry")
    if isinstance(poetry, dict):
        extract_poetry(poetry, declarations, source_file)
    flit = (tool.get("flit") or {}).get("metadata") if isinstance(tool.get("flit"), dict) else None
    if isinstance(flit, dict):
        if "requires" in flit:
            _string_list(declarations, source_file, "flit", "tool.flit.metadata.requires", "runtime", flit["requires"])
        else:
            declarations.field(source_file, "flit", "tool.flit.metadata.requires", "dependency", "confirmed_absent")
        extras = flit.get("requires-extra")
        if isinstance(extras, dict):
            for extra, values in extras.items():
                _string_list(declarations, source_file, "flit", f"tool.flit.metadata.requires-extra.{extra}",
                             f"optional:{extra}", values)
        declarations.python_field(source_file, "flit", "tool.flit.metadata.requires-python", flit.get("requires-python"))
    setuptools_dynamic = (tool.get("setuptools") or {}).get("dynamic") if isinstance(tool.get("setuptools"), dict) else None
    if isinstance(setuptools_dynamic, dict):
        for key in ("dependencies", "optional-dependencies"):
            if key in setuptools_dynamic:
                declarations.field(source_file, "setuptools_dynamic", f"tool.setuptools.dynamic.{key}", "dependency",
                                   "dynamic_metadata", detail=json_text(setuptools_dynamic[key])[:300])


def extract_poetry(poetry: dict[str, Any], declarations: Declarations, source_file: str) -> None:
    extras_map = poetry.get("extras") if isinstance(poetry.get("extras"), dict) else {}
    extras_by_package: dict[str, list[str]] = {}
    for extra, names in extras_map.items():
        for name in names if isinstance(names, list) else []:
            extras_by_package.setdefault(canonicalize_name(str(name)), []).append(str(extra))

    dependencies = poetry.get("dependencies")
    if isinstance(dependencies, dict):
        rows: list[dict[str, str]] = []
        python_value = None
        for name, value in dependencies.items():
            if str(name).lower() == "python":
                python_value = value
                continue
            optional = isinstance(value, dict) and value.get("optional") is True
            if optional:
                groups = [f"optional:{extra}" for extra in sorted(extras_by_package.get(canonicalize_name(str(name)), []))]
                groups = groups or ["optional:unassigned"]
            else:
                groups = ["runtime"]
            for group in groups:
                rows.append(declarations.table_entry(source_file, "poetry", group, str(name), value, "parsed_poetry"))
        declarations.extracted_field(source_file, "poetry", "tool.poetry.dependencies", rows)
        declarations.python_field(source_file, "poetry", "tool.poetry.dependencies.python", python_value)
    elif "dependencies" in poetry:
        declarations.field(source_file, "poetry", "tool.poetry.dependencies", "dependency", "unsupported_syntax")
        declarations.field(source_file, "poetry", "tool.poetry.dependencies.python", "python_constraint",
                           "unsupported_syntax")
    else:
        declarations.field(source_file, "poetry", "tool.poetry.dependencies", "dependency", "confirmed_absent")
        declarations.field(source_file, "poetry", "tool.poetry.dependencies.python", "python_constraint",
                           "confirmed_absent")

    dev = poetry.get("dev-dependencies")
    if isinstance(dev, dict):
        rows = [declarations.table_entry(source_file, "poetry_dev", "development", str(name), value, "parsed_poetry")
                for name, value in dev.items()]
        declarations.extracted_field(source_file, "poetry_dev", "tool.poetry.dev-dependencies", rows)
    elif dev is not None:
        declarations.field(source_file, "poetry_dev", "tool.poetry.dev-dependencies", "dependency", "unsupported_syntax")

    groups = poetry.get("group")
    if isinstance(groups, dict):
        for group_name, group_data in groups.items():
            values = group_data.get("dependencies") if isinstance(group_data, dict) else None
            field = f"tool.poetry.group.{group_name}.dependencies"
            if isinstance(values, dict):
                rows = [declarations.table_entry(source_file, "poetry_group", poetry_group(str(group_name)),
                                                 str(name), value, "parsed_poetry")
                        for name, value in values.items()]
                declarations.extracted_field(source_file, "poetry_group", field, rows)
            else:
                declarations.field(source_file, "poetry_group", field, "dependency", "unsupported_syntax")


def extract_pipfile(text: str, declarations: Declarations, source_file: str) -> None:
    try:
        data = tomllib.loads(text)
    except Exception as exc:
        declarations.field(source_file, "pipfile", "(file)", "file", "parse_failure",
                           detail=f"{type(exc).__name__}:{str(exc)[:200]}")
        return
    for section, group in (("packages", "runtime"), ("dev-packages", "development")):
        values = data.get(section)
        if values is None:
            declarations.field(source_file, "pipfile", section, "dependency", "confirmed_absent")
        elif isinstance(values, dict):
            rows = [declarations.table_entry(source_file, "pipfile", group, str(name), value, "parsed_pipfile")
                    for name, value in values.items()]
            declarations.extracted_field(source_file, "pipfile", section, rows)
        else:
            declarations.field(source_file, "pipfile", section, "dependency", "unsupported_syntax")
    requires = data.get("requires") if isinstance(data.get("requires"), dict) else {}
    value = requires.get("python_full_version", requires.get("python_version"))
    declarations.python_field(source_file, "pipfile", "requires.python_version", value)


# -- setup.cfg --------------------------------------------------------------------


def extract_setup_cfg(text: str, declarations: Declarations, source_file: str) -> None:
    kind = "setup_cfg"
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except Exception as exc:
        declarations.field(source_file, kind, "(file)", "file", "parse_failure",
                           detail=f"{type(exc).__name__}:{str(exc)[:200]}")
        return

    def delegated(value: str) -> bool:
        return value.strip().startswith(("file:", "attr:"))

    for option, group in (("install_requires", "runtime"), ("setup_requires", "build"), ("tests_require", "test")):
        field = f"options.{option}"
        if not parser.has_option("options", option):
            declarations.field(source_file, kind, field, "dependency", "confirmed_absent")
            continue
        value = parser.get("options", option)
        if delegated(value):
            declarations.field(source_file, kind, field, "dependency", "dynamic_metadata", detail=value.strip())
            continue
        rows = [row for line in value.splitlines()
                if (row := declarations.requirement(source_file, kind, group, line)) is not None]
        declarations.extracted_field(source_file, kind, field, rows)

    if parser.has_section("options.extras_require"):
        for extra, raw_values in parser.items("options.extras_require"):
            field = f"options.extras_require.{extra}"
            if delegated(raw_values):
                declarations.field(source_file, kind, field, "dependency", "dynamic_metadata", detail=raw_values.strip())
                continue
            rows = [row for line in raw_values.splitlines()
                    if (row := declarations.requirement(source_file, kind, f"optional:{extra}", line)) is not None]
            declarations.extracted_field(source_file, kind, field, rows)
    else:
        declarations.field(source_file, kind, "options.extras_require", "dependency", "confirmed_absent")

    if parser.has_option("options", "python_requires"):
        value = parser.get("options", "python_requires")
        if delegated(value):
            declarations.field(source_file, kind, "options.python_requires", "python_constraint", "dynamic_metadata",
                               detail=value.strip())
        else:
            declarations.python_field(source_file, kind, "options.python_requires", value)
    else:
        declarations.python_field(source_file, kind, "options.python_requires", None)


# -- setup.py ---------------------------------------------------------------------


class StaticEnvironment:
    """Module-level bindings that Section 2.1 allows a setup() argument to reference."""

    def __init__(self, tree: ast.Module, stop_at: ast.stmt | None = None) -> None:
        self.values: dict[str, Any] = {}
        self.tainted: set[str] = set()
        top_level = {id(node) for node in tree.body}
        for node in tree.body:
            if node is stop_at:
                break
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name):
                    try:
                        self.values[target.id] = self.evaluate(node.value)
                    except Unresolved:
                        self.values.pop(target.id, None)
                        self.tainted.add(target.id)
                elif isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) \
                        and isinstance(self.values.get(target.value.id), dict):
                    try:
                        self.values[target.value.id][self.evaluate(target.slice)] = self.evaluate(node.value)
                    except (Unresolved, TypeError):
                        self.tainted.add(target.value.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                try:
                    self.values[node.target.id] = self.evaluate(node.value)
                except Unresolved:
                    self.values.pop(node.target.id, None)
                    self.tainted.add(node.target.id)
        self._taint_mutations(tree, top_level)

    def _taint_mutations(self, tree: ast.Module, top_level: set[int]) -> None:
        global_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Global):
                global_names.update(node.names)
        function_nodes: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                function_nodes.update(id(child) for child in ast.walk(node) if child is not node)
        for node in ast.walk(tree):
            if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
                self.tainted.add(node.target.id)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and isinstance(node.func.value, ast.Name) and node.func.attr in LIST_MUTATORS:
                self.tainted.add(node.func.value.id)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and id(node) not in top_level:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    for child in ast.walk(target):
                        if isinstance(child, ast.Name) and (id(node) not in function_nodes or child.id in global_names):
                            self.tainted.add(child.id)
                        elif isinstance(child, ast.Subscript) and isinstance(child.value, ast.Name):
                            self.tainted.add(child.value.id)
            elif isinstance(node, (ast.For, ast.With)) and id(node) not in function_nodes:
                target = node.target if isinstance(node, ast.For) else None
                for child in ast.walk(target) if target is not None else []:
                    if isinstance(child, ast.Name):
                        self.tainted.add(child.id)

    def evaluate(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            items = [self.evaluate(item) for item in node.elts]
            return items if isinstance(node, ast.List) else tuple(items) if isinstance(node, ast.Tuple) else set(items)
        if isinstance(node, ast.Dict):
            if any(key is None for key in node.keys):
                raise Unresolved("dict_unpacking")
            return {self.evaluate(key): self.evaluate(value) for key, value in zip(node.keys, node.values)}
        if isinstance(node, ast.Name):
            if node.id in self.tainted or node.id not in self.values:
                raise Unresolved(f"name:{node.id}")
            return self.values[node.id]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict" \
                and not node.args and all(keyword.arg for keyword in node.keywords):
            return {keyword.arg: self.evaluate(keyword.value) for keyword in node.keywords}
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = self.evaluate(node.left), self.evaluate(node.right)
            if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
                return list(left) + list(right)
            raise Unresolved("add_non_sequence")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
            return -node.operand.value
        raise Unresolved(type(node).__name__)


def _setup_values(call: ast.Call, environment: StaticEnvironment) -> tuple[dict[str, ast.AST | Any], list[str]]:
    """Resolve setup() keywords in call order; explicit and ** values are both honoured."""
    values: dict[str, ast.AST | Any] = {}
    unresolved_mappings: list[str] = []
    for keyword in call.keywords:
        if keyword.arg:
            values[keyword.arg] = keyword.value
            continue
        try:
            mapping = environment.evaluate(keyword.value)
        except Unresolved as exc:
            unresolved_mappings.append(f"{ast.unparse(keyword.value)[:80]}:{exc}")
            continue
        if not isinstance(mapping, dict):
            unresolved_mappings.append(f"{ast.unparse(keyword.value)[:80]}:not_a_dict")
            continue
        for key, value in mapping.items():
            values[str(key)] = ("__resolved__", value)
    return values, unresolved_mappings


def _resolve(value: ast.AST | tuple[str, Any], environment: StaticEnvironment) -> Any:
    if isinstance(value, tuple) and len(value) == 2 and value[0] == "__resolved__":
        return value[1]
    return environment.evaluate(value)  # type: ignore[arg-type]


def _requirement_items(value: Any) -> list[Any] | None:
    if isinstance(value, str):
        return [line for line in value.splitlines()]
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return None


def extract_setup_py(text: str, declarations: Declarations, source_file: str) -> None:
    kind = "setup_py"
    try:
        tree = ast.parse(text, filename=source_file)
    except (SyntaxError, ValueError) as exc:
        declarations.field(source_file, kind, "(file)", "file", "parse_failure",
                           detail=f"setup_py_syntax:{getattr(exc, 'lineno', '')}:{getattr(exc, 'msg', exc)}")
        return
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and ((isinstance(node.func, ast.Name) and node.func.id == "setup")
             or (isinstance(node.func, ast.Attribute) and node.func.attr == "setup"))
    ]
    if not calls:
        declarations.field(source_file, kind, "(file)", "file", "parse_failure", detail="setup_call_not_found")
        return
    call = sorted(calls, key=lambda node: (node.lineno, node.col_offset))[-1]
    # Bindings after the top-level statement that runs setup() do not reach it.
    # A setup() inside a function may run after any module-level binding.
    enclosing = next(
        (node for node in tree.body
         if node.lineno <= call.lineno <= (node.end_lineno or node.lineno)
         and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))),
        None,
    )
    environment = StaticEnvironment(tree, stop_at=enclosing)
    values, unresolved_mappings = _setup_values(call, environment)
    if unresolved_mappings:
        declarations.field(source_file, kind, "setup(**...)", "setup_kwargs", "unsupported_syntax",
                           detail=";".join(unresolved_mappings))

    for name, group in SETUP_FIELDS.items():
        field = f"setup.{name}"
        field_kind = "python_constraint" if name == "python_requires" else "dependency"
        if name not in values:
            if not unresolved_mappings:
                declarations.field(source_file, kind, field, field_kind, "confirmed_absent")
            continue
        node = values[name]
        if name == "python_requires":
            try:
                declarations.python_field(source_file, kind, field, _resolve(node, environment))
            except Unresolved as exc:
                declarations.field(source_file, kind, field, field_kind, "unsupported_syntax", detail=str(exc))
            continue
        if name == "extras_require":
            _setup_extras(node, environment, declarations, source_file)
            continue
        rows, unresolved = _setup_requirement_list(node, environment, declarations, source_file, group)
        if rows is None:
            declarations.field(source_file, kind, field, field_kind, "unsupported_syntax", detail=unresolved)
        else:
            declarations.extracted_field(source_file, kind, field, rows, int(unresolved or 0))


def _setup_requirement_list(node: Any, environment: StaticEnvironment, declarations: Declarations,
                            source_file: str, group: str) -> tuple[list[dict[str, str]] | None, str]:
    try:
        items = _requirement_items(_resolve(node, environment))
        if items is None:
            return None, "value is not a string or sequence"
        rows = [row for item in items if (row := declarations.requirement(source_file, "setup_py", group, item)) is not None]
        return rows, ""
    except Unresolved as exc:
        # A list literal with some non-literal elements is partially extractable.
        if isinstance(node, (ast.List, ast.Tuple)):
            rows: list[dict[str, str]] = []
            unresolved = 0
            for element in node.elts:
                try:
                    value = environment.evaluate(element)
                except Unresolved:
                    unresolved += 1
                    continue
                if (row := declarations.requirement(source_file, "setup_py", group, value)) is not None:
                    rows.append(row)
            return rows, str(unresolved)
        return None, str(exc)


def _setup_extras(node: Any, environment: StaticEnvironment, declarations: Declarations, source_file: str) -> None:
    kind = "setup_py"
    try:
        extras = _resolve(node, environment)
    except Unresolved as exc:
        if isinstance(node, ast.Dict) and all(key is not None for key in node.keys):
            for key_node, value_node in zip(node.keys, node.values):
                try:
                    extra = str(environment.evaluate(key_node))
                except Unresolved:
                    declarations.field(source_file, kind, "setup.extras_require.<unresolved key>", "dependency",
                                       "unsupported_syntax")
                    continue
                rows, unresolved = _setup_requirement_list(value_node, environment, declarations, source_file,
                                                           f"optional:{extra}")
                field = f"setup.extras_require.{extra}"
                if rows is None:
                    declarations.field(source_file, kind, field, "dependency", "unsupported_syntax", detail=unresolved)
                else:
                    declarations.extracted_field(source_file, kind, field, rows, int(unresolved or 0))
            return
        declarations.field(source_file, kind, "setup.extras_require", "dependency", "unsupported_syntax", detail=str(exc))
        return
    if not isinstance(extras, dict):
        declarations.field(source_file, kind, "setup.extras_require", "dependency", "unsupported_syntax",
                           detail="value is not a dict")
        return
    if not extras:
        declarations.field(source_file, kind, "setup.extras_require", "dependency", "extracted")
    for extra, values in extras.items():
        items = _requirement_items(values)
        field = f"setup.extras_require.{extra}"
        if items is None:
            declarations.field(source_file, kind, field, "dependency", "unsupported_syntax", detail="value is not a sequence")
            continue
        rows = [row for item in items
                if (row := declarations.requirement(source_file, kind, f"optional:{extra}", item)) is not None]
        declarations.extracted_field(source_file, kind, field, rows)


# -- dispatcher ---------------------------------------------------------------------


def declaration_kind(name: str) -> str:
    base = PurePosixPath(name).name.lower()
    if base == "pyproject.toml":
        return "pyproject"
    if base == "setup.cfg":
        return "setup_cfg"
    if base == "setup.py":
        return "setup_py"
    if base.startswith("requirements") and base.endswith((".txt", ".in")):
        return "requirements"
    if base in {"environment.yml", "environment.yaml"}:
        return "environment"
    if base == "pipfile":
        return "pipfile"
    return ""


EXTRACTORS = {
    "pyproject": extract_pyproject,
    "setup_cfg": extract_setup_cfg,
    "setup_py": extract_setup_py,
    "requirements": extract_requirements,
    "environment": extract_environment_yml,
    "pipfile": extract_pipfile,
}


def extract_declarations(
    snapshot: dict[str, str], names: list[str], texts: dict[str, str], unread_reasons: dict[str, str] | None = None
) -> Declarations:
    """Run every supported root-level declaration file of one snapshot (spec Section 2).

    `unread_reasons` gives the reason a listed file has no text when it is not
    the size limit (a link whose target is not a regular member of the archive).
    """
    declarations = Declarations(snapshot)
    for name in sorted(names):
        if len(PurePosixPath(name).parts) != 1:
            continue
        kind = declaration_kind(name)
        if not kind:
            continue
        declarations.declaration_files.append(name)
        if name not in texts:
            detail = (unread_reasons or {}).get(name, "text_not_read_size_limit")
            declarations.field(name, kind, "(file)", "file", "parse_failure", detail=detail)
            continue
        if kind == "requirements":
            extract_requirements(texts[name], declarations, name, texts)
        else:
            EXTRACTORS[kind](texts[name], declarations, name)
    return declarations
