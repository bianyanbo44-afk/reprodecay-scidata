#!/usr/bin/env python3
"""Compare the v0.2.4 release with a v0.3 build on the v0.2.4 project cohort.

Reports, for the projects present in both releases: snapshot completeness
transitions, dependency-record and dependency-key differences, structure-field
differences, delta-row transitions, summary-value differences and the list of
affected projects. Results that do not change are reported as unchanged.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path


def read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else ["empty"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def dependency_values(rows: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, set[str]]]:
    """(snapshot_id, group|name) -> {specifier/marker/extras: values}, for parsed rows."""
    out: dict[tuple[str, str], dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for row in rows:
        if not row["normalized_name"] or not row["parse_status"].startswith("parsed"):
            continue
        key = (row["snapshot_id"], f"{row['dependency_group']}|{row['normalized_name']}")
        for field in ("specifier", "marker", "extras"):
            out[key][field].add(row[field])
    return out


def old_status(value: str) -> str:
    return {"partial_parse": "incomplete_observation"}.get(value, value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-tables", required=True)
    parser.add_argument("--old-summary", required=True)
    parser.add_argument("--new-tables", required=True)
    parser.add_argument("--new-summary", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    old_t, new_t = Path(args.old_tables), Path(args.new_tables)
    old_s, new_s = Path(args.old_summary), Path(args.new_summary)
    out = Path(args.output_dir)

    old_projects = {row["project_id"] for row in read(old_t / "project_records.csv")}
    new_projects = {row["project_id"] for row in read(new_t / "project_records.csv")}
    cohort = old_projects & new_projects
    report: dict[str, object] = {
        "cohort": "projects present in both v0.2.4 and the v0.3 build",
        "cohort_projects": len(cohort),
        "projects_only_in_v0_2_4": sorted(old_projects - new_projects),
        "projects_only_in_v0_3": len(new_projects - old_projects),
    }
    affected: dict[str, set[str]] = defaultdict(set)

    # 1. Snapshot completeness transitions.
    old_structure = {(r["project_id"], r["snapshot_role"]): r for r in read(old_t / "structure_records.csv") if r["project_id"] in cohort}
    new_structure = {(r["project_id"], r["snapshot_role"]): r for r in read(new_t / "structure_records.csv") if r["project_id"] in cohort}
    transitions: list[dict[str, object]] = []
    for family, old_field, new_field in (
        ("dependency", "dependency_parse_complete", "dependency_observation_status"),
        ("api", "api_parse_complete", "api_observation_status"),
        ("structure_python_constraints", None, "python_constraint_observation_status"),
        ("structure_build_backends", None, "build_backend_observation_status"),
        ("structure_source_line_count", None, "source_line_observation_status"),
    ):
        counts: Counter[tuple[str, str, str]] = Counter()
        for key, new_row in new_structure.items():
            old_row = old_structure[key]
            before = ("complete" if old_row[old_field] == "true" else "incomplete") if old_field else "complete (row exists)"
            after = new_row[new_field]
            counts[(key[1], before, after)] += 1
            if old_field and (before == "complete") != (after in {"complete", "no_declaration_file"}):
                affected[key[0]].add(f"{family}_completeness")
            if not old_field and after != "complete":
                affected[key[0]].add(f"{family}_completeness")
        for (role, before, after), count in sorted(counts.items()):
            transitions.append({"measure": family, "snapshot_role": role, "v0_2_4_status": before,
                                "v0_3_status": after, "snapshots": count})
    write(out / "completeness_transitions.csv", transitions)

    # Reasons behind new dependency incompleteness.
    reasons: Counter[str] = Counter()
    for row in new_structure.values():
        for reason in json.loads(row["dependency_incomplete_reasons"] or "[]"):
            reasons[reason.rsplit(":", 1)[-1]] += 1
    report["dependency_incomplete_reason_field_counts"] = dict(sorted(reasons.items()))

    # 2. Dependency records and keys.
    old_dependencies = [r for r in read(old_t / "dependency_records.csv") if r["project_id"] in cohort]
    new_dependencies = [r for r in read(new_t / "dependency_records.csv") if r["project_id"] in cohort]
    old_values, new_values = dependency_values(old_dependencies), dependency_values(new_dependencies)
    key_rows: list[dict[str, object]] = []
    key_counts: Counter[str] = Counter()
    for key in sorted(set(old_values) | set(new_values)):
        snapshot_id, feature = key
        project = snapshot_id.split("__", 1)[0]
        if key not in new_values:
            kind = "key_only_in_v0_2_4"
        elif key not in old_values:
            kind = "key_only_in_v0_3"
        elif any(old_values[key][field] != new_values[key][field] for field in ("specifier", "marker", "extras")):
            kind = "key_value_changed"
        else:
            kind = "unchanged"
        key_counts[kind] += 1
        if kind != "unchanged":
            affected[project].add("dependency_records")
            key_rows.append({
                "snapshot_id": snapshot_id, "feature_key": feature, "difference": kind,
                "v0_2_4_value": json.dumps({k: sorted(v) for k, v in old_values.get(key, {}).items()}, sort_keys=True),
                "v0_3_value": json.dumps({k: sorted(v) for k, v in new_values.get(key, {}).items()}, sort_keys=True),
            })
    write(out / "dependency_key_differences.csv", key_rows)
    old_count = Counter(r["snapshot_id"] for r in old_dependencies)
    new_count = Counter(r["snapshot_id"] for r in new_dependencies)
    report["dependency_records"] = {
        "v0_2_4_rows": len(old_dependencies),
        "v0_3_rows": len(new_dependencies),
        "snapshots_with_changed_row_count": sum(old_count[s] != new_count[s] for s in set(old_count) | set(new_count)),
        "parse_status_v0_2_4": dict(Counter(r["parse_status"] for r in old_dependencies)),
        "parse_status_v0_3": dict(Counter(r["parse_status"] for r in new_dependencies)),
        "key_comparison": dict(key_counts),
    }

    # 3. Structure list fields.
    structure_changes: Counter[str] = Counter()
    for key, new_row in new_structure.items():
        for field in ("packaging_files", "build_backends", "python_constraints", "ci_providers",
                      "source_python_line_count", "source_python_file_count", "syntactic_api_symbol_count"):
            if old_structure[key].get(field) != new_row.get(field):
                structure_changes[field] += 1
                affected[key[0]].add(f"structure_{field}")
    report["structure_field_changes_snapshots"] = {
        field: structure_changes.get(field, 0)
        for field in ("packaging_files", "build_backends", "python_constraints", "ci_providers",
                      "source_python_line_count", "source_python_file_count", "syntactic_api_symbol_count")
    }

    # 4. Delta rows.
    def delta_index(path: Path) -> dict[tuple[str, str, str, str], tuple[str, str]]:
        return {
            (r["project_id"], r["family"], r["feature_group"], r["feature_key"]): (r["delta_type"], old_status(r["comparison_status"]))
            for r in read(path) if r["project_id"] in cohort
        }
    old_delta, new_delta = delta_index(old_t / "pair_delta_records.csv"), delta_index(new_t / "pair_delta_records.csv")
    delta_rows: list[dict[str, object]] = []
    delta_counts: Counter[tuple[str, str]] = Counter()
    for key in set(old_delta) | set(new_delta):
        family = key[1] if key[1] != "structure" else f"structure:{key[2]}"
        if key not in new_delta:
            kind = "row_only_in_v0_2_4"
        elif key not in old_delta:
            kind = "row_only_in_v0_3"
        elif old_delta[key][0] != new_delta[key][0]:
            kind = "delta_type_changed"
        elif old_delta[key][1] != new_delta[key][1]:
            kind = f"status_{old_delta[key][1]}_to_{new_delta[key][1]}"
        else:
            kind = "unchanged"
        delta_counts[(family, kind)] += 1
        if kind != "unchanged":
            affected[key[0]].add(f"delta_{family}")
    for (family, kind), count in sorted(delta_counts.items()):
        delta_rows.append({"family_or_feature_group": family, "difference": kind, "delta_rows": count})
    write(out / "delta_row_transitions.csv", delta_rows)

    # 5. Summary values.
    summary_rows: list[dict[str, object]] = []
    old_parsing = {(r["family"], r["scope"]): r for r in read(old_s / "parsing_completeness_summary.csv")}
    new_parsing = {(r["family"], r["scope"]): r for r in read(new_s / "parsing_completeness_summary.csv")}
    for key in sorted(old_parsing):
        before, after = old_parsing[key]["complete_projects"], new_parsing.get(key, {}).get("complete_projects", "")
        summary_rows.append({"table": "parsing_completeness_summary", "item": f"{key[0]}:{key[1]}:complete_projects",
                             "v0_2_4": before, "v0_3": after,
                             "status": "unchanged" if before == after else "changed"})
    old_paired = {r["metric"]: r for r in read(old_s / "paired_change_summary.csv")}
    new_paired = {(r["metric"], r["summary_basis"]): r for r in read(new_s / "paired_change_summary.csv")}
    for metric, row in old_paired.items():
        for basis in ("all_available_values", "adequately_observed_pairs"):
            new_row = new_paired.get((metric, basis), {})
            for old_field, new_field in (("complete_pair_projects", "included_pair_projects"),
                                         ("absolute_change_median", "absolute_change_median"),
                                         ("bootstrap_median_95_lower", "bootstrap_median_95_lower"),
                                         ("bootstrap_median_95_upper", "bootstrap_median_95_upper")):
                before, after = row.get(old_field, ""), new_row.get(new_field, "")
                summary_rows.append({"table": "paired_change_summary", "item": f"{metric}:{basis}:{new_field}",
                                     "v0_2_4": before, "v0_3": after,
                                     "status": "unchanged" if before == after else "changed"})
    old_family = {(r["family"], r["delta_type"], r["scope"]): r for r in read(old_s / "family_project_summary.csv")}
    new_family = {(r["family"], r["delta_type"], r["scope"]): r for r in read(new_s / "family_project_summary.csv")}
    for key, row in sorted(old_family.items()):
        for field in ("projects_with_at_least_one", "project_denominator", "project_proportion"):
            before, after = row[field], new_family.get(key, {}).get(field, "")
            summary_rows.append({"table": "family_project_summary", "item": f"{':'.join(key)}:{field}",
                                 "v0_2_4": before, "v0_3": after,
                                 "status": "unchanged" if before == after else "changed"})
    write(out / "summary_value_comparison.csv", summary_rows)
    report["summary_values"] = dict(Counter(row["status"] for row in summary_rows))

    project_rows = [
        {"project_id": project, "affected_items": json.dumps(sorted(items))}
        for project, items in sorted(affected.items())
    ]
    write(out / "affected_projects.csv", project_rows)
    report["affected_projects"] = len(project_rows)
    report["unaffected_projects"] = len(cohort) - len(project_rows)
    report["affected_projects_by_item"] = dict(sorted(Counter(i for items in affected.values() for i in items).items()))
    (out / "release_comparison.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    (out / "release_comparison_tables.md").write_text(markdown(report, transitions, delta_rows, summary_rows), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def markdown(report: dict[str, object], transitions: list[dict[str, object]],
             delta_rows: list[dict[str, object]], summary_rows: list[dict[str, object]]) -> str:
    lines = [
        f"Cohort: {report['cohort_projects']} projects present in both releases; "
        f"{report['affected_projects']} affected, {report['unaffected_projects']} unaffected.",
        "",
        "## Snapshot observation status, v0.2.4 to v0.3",
        "",
        "| Measure | Snapshot | v0.2.4 | v0.3 | Snapshots |",
        "|---|---|---|---|---|",
        *(f"| {r['measure']} | {r['snapshot_role']} | {r['v0_2_4_status']} | {r['v0_3_status']} | {r['snapshots']} |" for r in transitions),
        "",
        "## Dependency records",
        "",
        "```json",
        json.dumps(report["dependency_records"], indent=2, sort_keys=True),
        "```",
        "",
        "## Delta rows",
        "",
        "| Family / feature group | Difference | Rows |",
        "|---|---|---|",
        *(f"| {r['family_or_feature_group']} | {r['difference']} | {r['delta_rows']} |" for r in delta_rows),
        "",
        "## Summary values",
        "",
        "| Table | Item | v0.2.4 | v0.3 | Status |",
        "|---|---|---|---|---|",
        *(f"| {r['table']} | {r['item']} | {r['v0_2_4']} | {r['v0_3']} | {r['status']} |" for r in summary_rows),
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
