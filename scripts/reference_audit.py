#!/usr/bin/env python3
"""Reference-based audit of declaration, structure, delta and interface records.

Expected outcomes come from reading the source files against
docs/extraction_specification_v0.3.md, not from any parser:

1. ``select``  chooses the audited projects with a documented, deterministic rule
   and exports the root-level declaration files (and one package module per
   API-audited snapshot) as plain text for annotation. Parser output is not
   exported.
2. Annotators write one JSON file per snapshot in ``annotations/`` (format in
   ``ANNOTATION_TEMPLATE``) from those sources alone.
3. ``compare`` checks the released records against the annotations: field
   statuses, dependency entries, Python constraints, build backends, snapshot
   observation status, interface symbols, and the delta type and comparison
   status that the specification's comparison rule implies for every
   annotated dependency key.

Every discrepancy is listed; nothing is averaged away.
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_version_pairs import ArchiveView, package_source_files  # noqa: E402  (archive reading only)

ANNOTATION_TEMPLATE = {
    "snapshot_id": "",
    "annotated_by": "",
    "verified_by": "",
    "fields": [
        {"source_file": "setup.py", "field": "setup.install_requires", "status": "extracted",
         "entries": [["runtime", "numpy", ">=1.16"]]},
    ],
    "python_constraints": ["setup.py:>=3.6"],
    "build_backends": [],
    "dependency_observation_status": "complete",
    "api_module": {"source_file": "", "symbols": [["function", "fit", ["x", "y"]]]},
    "notes": "",
}
DECLARATION_BASENAMES = {"pyproject.toml", "setup.cfg", "setup.py", "environment.yml", "environment.yaml", "pipfile"}
PARSED = {"parsed", "parsed_poetry", "parsed_pipfile", "parsed_non_pep508", "parsed_url"}


def read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["empty"])
        writer.writeheader()
        writer.writerows(rows)


def order_key(project_id: str) -> str:
    return hashlib.sha256(f"reference-audit-v0.3|{project_id}".encode()).hexdigest()


def is_declaration(name: str) -> bool:
    base = PurePosixPath(name).name.lower()
    return len(PurePosixPath(name).parts) == 1 and (
        base in DECLARATION_BASENAMES or (base.startswith("requirements") and base.endswith((".txt", ".in")))
    )


def select(args: argparse.Namespace) -> int:
    tables = Path(args.tables)
    projects = read(tables / "project_records.csv")
    structure = read(tables / "structure_records.csv")
    fields = read(tables / "declaration_source_records.csv")
    forms = read(Path(args.form_scan))
    paths = {row["snapshot_id"]: row["local_archive_path"] for row in read(Path(args.archive_paths))}
    snapshots = {(row["project_id"], row["snapshot_role"]): row["snapshot_id"] for row in structure}
    ordered = sorted((row["project_id"] for row in projects), key=order_key)

    chosen: dict[str, str] = {}

    def take(candidates: list[str], stratum: str, limit: int) -> None:
        for project in sorted(set(candidates), key=order_key):
            if limit <= 0:
                return
            if project not in chosen:
                chosen[project] = stratum
                limit -= 1

    # Stratum A: one project per lossy v0.2 declaration form present in the cohort.
    by_form: dict[str, list[str]] = defaultdict(list)
    for row in forms:
        for form in json.loads(row["forms_present"]):
            by_form[form].append(row["project_id"])
    for form in sorted(by_form):
        take(by_form[form], f"form:{form}", 1)
    # Stratum B: confirmed absence (a parsed setup.py or setup.cfg with no install_requires).
    absent = [row["project_id"] for row in fields
              if row["observation_status"] == "confirmed_absent" and row["field"] in {"setup.install_requires", "options.install_requires"}]
    take(absent, "confirmed_absence", args.per_stratum)
    # Stratum C: unobservable fields.
    for status in ("unsupported_syntax", "dynamic_metadata", "parse_failure", "extracted_partial"):
        take([row["project_id"] for row in fields if row["observation_status"] == status], f"unobservable:{status}", 1)
    # Stratum D: 2021-2022 extension projects and ordinary projects, in hash order.
    take([row["project_id"] for row in projects if row.get("publication_year", "") >= "2021"], "extension_2021_2022", args.per_stratum)
    take(ordered, "ordinary_hash_order", args.per_stratum)

    out = Path(args.output_dir)
    rows: list[dict[str, object]] = []
    for project, stratum in chosen.items():
        for role in ("publication", "endpoint"):
            snapshot_id = snapshots[(project, role)]
            view = ArchiveView.read(Path(paths[snapshot_id]), digests=False)
            target = out / "sources" / snapshot_id
            target.mkdir(parents=True, exist_ok=True)
            exported = []
            for name in sorted(view.names):
                if is_declaration(name) or (name in view.texts and name.startswith("requirements/")):
                    (target / name.replace("/", "__")).write_text(view.texts.get(name, "<<not read: size limit>>"), encoding="utf-8")
                    exported.append(name)
            api_file = ""
            if role == "endpoint" and stratum in {"ordinary_hash_order", "extension_2021_2022"}:
                sources = [name for name in package_source_files(view.names) if name in view.texts and 20 < len(view.texts[name].splitlines()) < 400]
                if sources:
                    api_file = sorted(sources, key=lambda name: hashlib.sha256(name.encode()).hexdigest())[0]
                    (target / ("API__" + api_file.replace("/", "__"))).write_text(view.texts[api_file], encoding="utf-8")
            (target / "_root_listing.txt").write_text(
                "\n".join(name for name in view.names if len(PurePosixPath(name).parts) == 1), encoding="utf-8")
            rows.append({"project_id": project, "stratum": stratum, "snapshot_id": snapshot_id, "snapshot_role": role,
                         "exported_declaration_files": json.dumps(exported), "api_module": api_file})
    write(out / "reference_audit_sample.csv", rows)
    (out / "annotation_template.json").write_text(json.dumps(ANNOTATION_TEMPLATE, indent=2), encoding="utf-8")
    print(json.dumps({"projects": len(chosen), "snapshots": len(rows), "strata": Counter(chosen.values())}, indent=2))
    return 0


def released_symbols(rows: list[dict[str, str]], source_file: str) -> set[tuple[str, str, tuple[str, ...]]]:
    out = set()
    for row in rows:
        if row["source_file"] != source_file:
            continue
        names: tuple[str, ...] = ()
        if row["symbol_kind"] != "class":
            try:
                tree = ast.parse(f"def f({row['signature'].split(' || ')[0]}): pass")
                arguments = tree.body[0].args  # type: ignore[attr-defined]
                names = tuple(a.arg for a in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs])
                names += tuple(f"*{arguments.vararg.arg}" for _ in [0] if arguments.vararg)
                names += tuple(f"**{arguments.kwarg.arg}" for _ in [0] if arguments.kwarg)
            except SyntaxError:
                names = ("<unparsable>",)
        out.add((row["symbol_kind"], row["qualified_name"], names))
    return out


def compare(args: argparse.Namespace) -> int:
    tables = Path(args.tables)
    sample = read(Path(args.audit_dir) / "reference_audit_sample.csv")
    annotations = {
        data["snapshot_id"]: data
        for path in sorted((Path(args.audit_dir) / "annotations").glob("*.json"))
        for data in [json.loads(path.read_text(encoding="utf-8"))]
    }
    sampled = {row["snapshot_id"] for row in sample}
    fields = defaultdict(dict)
    for row in read(tables / "declaration_source_records.csv"):
        if row["snapshot_id"] in sampled:
            fields[row["snapshot_id"]][(row["source_file"], row["field"])] = row["observation_status"]
    dependencies = defaultdict(list)
    for row in read(tables / "dependency_records.csv"):
        if row["snapshot_id"] in sampled:
            dependencies[row["snapshot_id"]].append(row)
    structure = {row["snapshot_id"]: row for row in read(tables / "structure_records.csv") if row["snapshot_id"] in sampled}
    symbols = defaultdict(list)
    for row in read(tables / "api_symbol_records.csv"):
        if row["snapshot_id"] in sampled:
            symbols[row["snapshot_id"]].append(row)
    deltas = defaultdict(dict)
    pairs = {row["project_id"] for row in sample}
    for row in read(tables / "pair_delta_records.csv"):
        if row["project_id"] in pairs and row["family"] == "dependency":
            deltas[row["project_id"]][row["feature_key"]] = (row["delta_type"], row["comparison_status"])

    results: list[dict[str, object]] = []

    def result(unit: str, snapshot: str, item: str, expected: object, released: object) -> None:
        results.append({"unit": unit, "snapshot_or_project": snapshot, "item": item,
                        "expected": json.dumps(expected, sort_keys=True), "released": json.dumps(released, sort_keys=True),
                        "agreement": str(expected == released).lower()})

    for row in sample:
        sid = row["snapshot_id"]
        note = annotations.get(sid)
        if note is None:
            result("annotation", sid, "annotation_file", "present", "missing")
            continue
        for field in note["fields"]:
            key = (field["source_file"], field["field"])
            result("field_status", sid, "|".join(key), field["status"], fields[sid].get(key, "<no row>"))
        # Dependency entries are compared per snapshot as multisets of (group, name, specifier); every
        # missing or extra entry is one discrepancy. Included requirements files make a per-field split ambiguous.
        expected_entries = Counter(tuple(entry[:3]) for field in note["fields"] for entry in field.get("entries", []))
        released_entries = Counter(
            (r["dependency_group"], r["normalized_name"], r["specifier"])
            for r in dependencies[sid] if r["parse_status"] in PARSED and r["normalized_name"]
        )
        for entry in sorted(expected_entries | released_entries):
            result("dependency_entry", sid, "|".join(entry), expected_entries[entry], released_entries[entry])
        unannotated = sorted(set(fields[sid]) - {(f["source_file"], f["field"]) for f in note["fields"]})
        result("field_coverage", sid, "released fields without annotation", [], ["|".join(k) for k in unannotated])
        result("python_constraints", sid, "python_constraints", sorted(note["python_constraints"]),
               json.loads(structure[sid]["python_constraints"]))
        result("build_backends", sid, "build_backends", sorted(note["build_backends"]), json.loads(structure[sid]["build_backends"]))
        result("observation_status", sid, "dependency_observation_status", note["dependency_observation_status"],
               structure[sid]["dependency_observation_status"])
        module = note.get("api_module") or {}
        if module.get("source_file"):
            expected = sorted((k, n, tuple(p)) for k, n, p in module["symbols"])
            result("api_symbols", sid, module["source_file"], [list(x) for x in expected],
                   [list(x) for x in sorted(released_symbols(symbols[sid], module["source_file"]))])

    # Delta classification implied by the annotations (specification Section 6).
    by_project = defaultdict(dict)
    for row in sample:
        if row["snapshot_id"] in annotations:
            by_project[row["project_id"]][row["snapshot_role"]] = annotations[row["snapshot_id"]]
    for project, roles in by_project.items():
        if set(roles) != {"publication", "endpoint"}:
            continue
        values = {}
        for role, note in roles.items():
            keyed = defaultdict(set)
            for field in note["fields"]:
                # Entries may carry extras and a marker after the specifier; table attributes are not annotated.
                for group, name, specifier, *rest in field.get("entries", []):
                    extras = rest[0] if len(rest) > 0 else ""
                    marker = rest[1] if len(rest) > 1 else ""
                    keyed[f"{group}|{name}"].add(json.dumps([specifier, extras, marker]))
            values[role] = keyed
        complete = all(note["dependency_observation_status"] in {"complete", "no_declaration_file"} for note in roles.values())
        status = "comparable" if complete else "incomplete_observation"
        for key in sorted(set(values["publication"]) | set(values["endpoint"])):
            pub, end = values["publication"].get(key), values["endpoint"].get(key)
            kind = "added" if pub is None else "removed" if end is None else "unchanged" if pub == end else "changed"
            result("delta_classification", project, key, [kind, status], list(deltas[project].get(key, ("<no row>", ""))))

    out = Path(args.audit_dir)
    write(out / "reference_audit_results.csv", results)
    summary = defaultdict(Counter)
    for row in results:
        summary[row["unit"]][row["agreement"]] += 1
    report = {
        "annotated_snapshots": sum(row["snapshot_id"] in annotations for row in sample),
        "sampled_snapshots": len(sample),
        "verified_snapshots": sum(bool(annotations.get(row["snapshot_id"], {}).get("verified_by")) for row in sample),
        "units": {unit: dict(counts) for unit, counts in summary.items()},
        "discrepancies": [row for row in results if row["agreement"] == "false"],
    }
    (out / "reference_audit_summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "discrepancies"}, indent=2))
    print(f"discrepancies: {len(report['discrepancies'])}")
    return 0


WORKSHEET_COLUMNS = ["item_id", "snapshot_id", "kind", "source_file", "field", "expected_status", "expected_entries",
                     "annotator_note", "verified", "reviewer_comment"]
YES = {"yes", "y", "ok", "true", "1", "是", "对"}


def worksheet(args: argparse.Namespace) -> int:
    """Export every annotated item to one sheet for item-by-item verification by an author."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    audit = Path(args.audit_dir)
    sample = read(audit / "reference_audit_sample.csv")
    rows: list[list[str]] = []
    for snapshot in sample:
        sid = snapshot["snapshot_id"]
        path = audit / "annotations" / f"{sid}.json"
        if not path.exists():
            continue
        note = json.loads(path.read_text(encoding="utf-8"))
        source_dir = f"sources/{sid}/"

        def add(kind: str, source: str, field: str, status: str, entries: str, remark: str = "") -> None:
            rows.append([f"{len(rows) + 1:04d}", sid, kind, source, field, status, entries, remark, "", ""])

        add("snapshot", source_dir + "_root_listing.txt", "dependency_observation_status",
            note["dependency_observation_status"], "", note.get("notes", ""))
        add("snapshot", source_dir, "python_constraints", "", "\n".join(note["python_constraints"]))
        add("snapshot", source_dir, "build_backends", "", "\n".join(note["build_backends"]))
        for field in note["fields"]:
            entries = "\n".join(" | ".join(str(part) for part in entry) for entry in field.get("entries", []))
            add("field", source_dir + field["source_file"].replace("/", "__"), field["field"], field["status"], entries)
        module = note.get("api_module") or {}
        if module.get("source_file"):
            api_file = source_dir + "API__" + module["source_file"].replace("/", "__")
            for kind, name, params in module["symbols"]:
                add("api_symbol", api_file, f"{kind} {name}", "", ", ".join(params))
    book = Workbook()
    readme = book.active
    readme.title = "how_to"
    for line in (
        "One row per annotated item. Open the file in source_file (relative to the reference_audit folder) and check the row",
        "against docs/extraction_specification_v0.3.md. Write yes in 'verified' when the expected status and entries are right,",
        "or no plus a comment when they are not. Entries are 'group | name | specifier [| extras | marker]'; extras are ';'-separated.",
        "API rows list the parameter names of one symbol in order (Specification Section 7.2).",
        "When done, run: python scripts/reference_audit.py sign --audit-dir <reference_audit folder> --worksheet <this file> --verifier \"<name>\"",
    ):
        readme.append([line])
    sheet = book.create_sheet("items")
    sheet.append(WORKSHEET_COLUMNS)
    for row in rows:
        sheet.append(row)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    widths = {"A": 7, "B": 30, "C": 11, "D": 42, "E": 34, "F": 18, "G": 52, "H": 52, "I": 9, "J": 30}
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.freeze_panes = "C2"
    sheet.auto_filter.ref = sheet.dimensions
    book.save(args.output)
    print(json.dumps({"items": len(rows), "snapshots": len({row[1] for row in rows}), "output": str(args.output)}))
    return 0


def sign(args: argparse.Namespace) -> int:
    """Record the verifier on every snapshot whose items are all marked verified in the worksheet."""
    from datetime import date
    from openpyxl import load_workbook

    sheet = load_workbook(args.worksheet, read_only=True)["items"]
    header = [cell.value for cell in next(sheet.iter_rows(max_row=1))]
    by_snapshot: dict[str, list[dict[str, str]]] = defaultdict(list)
    for values in sheet.iter_rows(min_row=2, values_only=True):
        row = {name: "" if value is None else str(value).strip() for name, value in zip(header, values)}
        by_snapshot[row["snapshot_id"]].append(row)
    signed, open_items = 0, []
    for sid, items in sorted(by_snapshot.items()):
        pending = [row for row in items if row["verified"].lower() not in YES]
        if pending:
            open_items.extend(pending)
            continue
        path = Path(args.audit_dir) / "annotations" / f"{sid}.json"
        note = json.loads(path.read_text(encoding="utf-8"))
        note["verified_by"] = f"{args.verifier}, {date.today().isoformat()}"
        path.write_text(json.dumps(note, indent=1, ensure_ascii=False), encoding="utf-8")
        signed += 1
    for row in open_items:
        print(f"open: {row['item_id']} {row['snapshot_id']} {row['field']}: {row['verified'] or '(blank)'} {row['reviewer_comment']}")
    print(json.dumps({"snapshots_signed": signed, "snapshots_open": len(by_snapshot) - signed, "open_items": len(open_items)}))
    return 0 if not open_items else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    w = sub.add_parser("worksheet")
    w.add_argument("--audit-dir", required=True)
    w.add_argument("--output", required=True)
    g = sub.add_parser("sign")
    g.add_argument("--audit-dir", required=True)
    g.add_argument("--worksheet", required=True)
    g.add_argument("--verifier", required=True)
    s = sub.add_parser("select")
    s.add_argument("--tables", required=True)
    s.add_argument("--form-scan", required=True, help="declaration_form_scan_by_snapshot.csv")
    s.add_argument("--archive-paths", required=True)
    s.add_argument("--output-dir", required=True)
    s.add_argument("--per-stratum", type=int, default=3)
    c = sub.add_parser("compare")
    c.add_argument("--tables", required=True)
    c.add_argument("--audit-dir", required=True)
    args = parser.parse_args()
    return {"select": select, "compare": compare, "worksheet": worksheet, "sign": sign}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
