#!/usr/bin/env python3
"""Count declaration forms that the v0.2.x extractor handled lossily, across the cohort.

For every snapshot the root-level declaration files are read from the retained
archive and checked for the forms listed in FORMS. Detection is deliberately
simple and separate from scripts/declarations.py: it records whether a form is
present in the source, not how a parser treats it. Forms that occur in no
snapshot are reported with a zero count. Nothing is imported or executed.
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import re
import tomllib
from collections import Counter
from pathlib import Path, PurePosixPath

from extract_version_pairs import ArchiveView

# form -> (what v0.2.x did, what v0.3 does)
FORMS = {
    "setup_call_kwargs_unpacking": ("arguments passed through **mapping were dropped without an issue",
                                    "expanded when statically evaluable, otherwise unsupported_syntax"),
    "setup_python_requires_nonliteral": ("dropped without an issue", "unsupported_syntax"),
    "setup_requires_or_tests_require": ("not read", "read as build / test groups"),
    "pyproject_project_dynamic_dependencies": ("not flagged; snapshot could be marked complete", "dynamic_metadata"),
    "pyproject_setuptools_dynamic": ("not flagged", "dynamic_metadata"),
    "poetry_inline_table_entries": ("reduced to name and version", "all keys kept in attributes, marker and extras"),
    "poetry_inline_with_markers_extras_python_or_source": ("information lost", "kept"),
    "poetry_optional_entries": ("recorded as runtime", "assigned to optional:<extra> via tool.poetry.extras"),
    "poetry_multiple_constraint_entries": ("recorded as the package name only", "all constraints kept"),
    "poetry_exact_version_strings": ("name and version concatenated into a wrong package name",
                                     "name from the key, version as specifier"),
    "poetry_dev_dependencies_table": ("not read", "read as development"),
    "poetry_group_tables": ("read", "read"),
    "flit_metadata_table": ("not read", "read"),
    "pipfile": ("not read", "read"),
    "pep735_dependency_groups": ("not read", "read (added after the reference-based audit, finding F1)"),
    "setup_cfg_file_or_attr_reference": ("recorded as an unparsed requirement", "dynamic_metadata"),
    "requirements_editable_local_path": ("unparsed; snapshot marked incomplete", "directive"),
    "requirements_editable_or_url_with_egg": ("unparsed", "parsed_url"),
    "requirements_include_of_subdirectory_file": ("directive only; included declarations not read",
                                                  "included file read"),
    "requirements_hash_or_per_requirement_option": ("could be mis-parsed", "option removed before parsing"),
    "requirements_line_continuation": ("lines parsed separately", "lines joined"),
    "declaration_file_is_link": ("not read (tar) or read as the link target path (zip)",
                                 "read as the linked archive member (added after the reference-based audit, finding F2)"),
}


def setup_forms(text: str) -> set[str]:
    found: set[str] = set()
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return found
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
        if name != "setup":
            continue
        for keyword in node.keywords:
            if keyword.arg is None:
                found.add("setup_call_kwargs_unpacking")
            elif keyword.arg == "python_requires" and not isinstance(keyword.value, ast.Constant):
                found.add("setup_python_requires_nonliteral")
            elif keyword.arg in {"setup_requires", "tests_require"}:
                found.add("setup_requires_or_tests_require")
    return found


def pyproject_forms(text: str) -> tuple[set[str], Counter[str]]:
    found: set[str] = set()
    counts: Counter[str] = Counter()
    try:
        data = tomllib.loads(text)
    except Exception:
        return found, counts
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    if set(project.get("dynamic") or []) & {"dependencies", "optional-dependencies"}:
        found.add("pyproject_project_dynamic_dependencies")
    if isinstance(data.get("dependency-groups"), dict):
        found.add("pep735_dependency_groups")
    tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
    setuptools = tool.get("setuptools") if isinstance(tool.get("setuptools"), dict) else {}
    if isinstance(setuptools.get("dynamic"), dict) and set(setuptools["dynamic"]) & {"dependencies", "optional-dependencies"}:
        found.add("pyproject_setuptools_dynamic")
    if isinstance(tool.get("flit"), dict) and isinstance(tool["flit"].get("metadata"), dict):
        found.add("flit_metadata_table")
    poetry = tool.get("poetry") if isinstance(tool.get("poetry"), dict) else {}
    tables = []
    if isinstance(poetry.get("dependencies"), dict):
        tables.append(poetry["dependencies"])
    if isinstance(poetry.get("dev-dependencies"), dict):
        found.add("poetry_dev_dependencies_table")
        tables.append(poetry["dev-dependencies"])
    if isinstance(poetry.get("group"), dict):
        found.add("poetry_group_tables")
        tables.extend(group.get("dependencies") for group in poetry["group"].values()
                      if isinstance(group, dict) and isinstance(group.get("dependencies"), dict))
    for table in tables:
        for name, value in table.items():
            if str(name).lower() == "python":
                continue
            if isinstance(value, dict):
                counts["poetry_inline_table_entries"] += 1
                if set(value) & {"markers", "extras", "python", "git", "path", "url", "source", "branch", "tag", "rev"}:
                    counts["poetry_inline_with_markers_extras_python_or_source"] += 1
                if value.get("optional") is True:
                    counts["poetry_optional_entries"] += 1
            elif isinstance(value, list):
                counts["poetry_multiple_constraint_entries"] += 1
            elif isinstance(value, str) and re.match(r"^\d", value.strip()):
                counts["poetry_exact_version_strings"] += 1
    found.update(form for form, count in counts.items() if count)
    return found, counts


def requirements_forms(text: str, path: str) -> set[str]:
    found: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.endswith("\\"):
            found.add("requirements_line_continuation")
        if re.match(r"^(-e|--editable)[\s=]+\.", stripped):
            found.add("requirements_editable_local_path")
        elif "#egg=" in stripped:
            found.add("requirements_editable_or_url_with_egg")
        include = re.match(r"^(?:-r|--requirement)(?:\s+|=)(\S+)", stripped)
        if include and len(PurePosixPath(str(PurePosixPath(path).parent / include.group(1))).parts) > 1:
            found.add("requirements_include_of_subdirectory_file")
        # Options may follow the requirement or start a continuation line.
        if re.search(r"(^|\s)--(hash|global-option|install-option|config-settings)\b", stripped):
            found.add("requirements_hash_or_per_requirement_option")
    return found


def scan(view: ArchiveView) -> tuple[set[str], Counter[str]]:
    found: set[str] = set()
    counts: Counter[str] = Counter()
    for name, text in view.texts.items():
        if len(PurePosixPath(name).parts) != 1:
            continue
        base = name.lower()
        if base == "setup.py":
            found |= setup_forms(text)
        elif base == "pyproject.toml":
            forms, entry_counts = pyproject_forms(text)
            found |= forms
            counts.update(entry_counts)
        elif base == "setup.cfg":
            if re.search(r"^\s*(install_requires|tests_require|setup_requires|python_requires)\s*=\s*(file|attr):",
                         text, flags=re.MULTILINE):
                found.add("setup_cfg_file_or_attr_reference")
        elif base == "pipfile":
            found.add("pipfile")
        elif base.startswith("requirements") and base.endswith((".txt", ".in")):
            found |= requirements_forms(text, name)
    for name in view.link_texts:
        base = name.lower()
        if len(PurePosixPath(name).parts) == 1 and (
            base in {"setup.py", "setup.cfg", "pyproject.toml", "pipfile", "environment.yml", "environment.yaml"}
            or (base.startswith("requirements") and base.endswith((".txt", ".in")))
        ):
            found.add("declaration_file_is_link")
    return found, counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshots", required=True, help="snapshot_records.csv of the release to scan")
    parser.add_argument("--publication-cache", required=True)
    parser.add_argument("--endpoint-cache", required=True)
    parser.add_argument("--archive-paths", required=True,
                        help="CSV with snapshot_id,local_archive_path (written by the release build)")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    paths = {row["snapshot_id"]: row["local_archive_path"]
             for row in csv.DictReader(Path(args.archive_paths).open(encoding="utf-8-sig", newline=""))}
    snapshots = list(csv.DictReader(Path(args.snapshots).open(encoding="utf-8-sig", newline="")))
    rows: list[dict[str, str]] = []
    snapshot_forms: Counter[str] = Counter()
    project_forms: dict[str, set[str]] = {form: set() for form in FORMS}
    entry_totals: Counter[str] = Counter()
    for snapshot in snapshots:
        view = ArchiveView.read(Path(paths[snapshot["snapshot_id"]]), digests=False)
        found, counts = scan(view)
        entry_totals.update(counts)
        for form in found:
            snapshot_forms[form] += 1
            project_forms[form].add(snapshot["project_id"])
        rows.append({
            "snapshot_id": snapshot["snapshot_id"],
            "project_id": snapshot["project_id"],
            "snapshot_role": snapshot["snapshot_role"],
            "forms_present": json.dumps(sorted(found)),
            "poetry_entry_counts": json.dumps(dict(sorted(counts.items())), sort_keys=True),
        })

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "declaration_form_scan_by_snapshot.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = [
        {
            "form": form,
            "v0_2_x_handling": before,
            "v0_3_handling": after,
            "snapshots_with_form": snapshot_forms[form],
            "projects_with_form": len(project_forms[form]),
            "entries_with_form": entry_totals.get(form, ""),
            "snapshot_denominator": len(snapshots),
        }
        for form, (before, after) in FORMS.items()
    ]
    with (output / "declaration_form_scan_summary.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)
    print(json.dumps({row["form"]: row["snapshots_with_form"] for row in summary}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
