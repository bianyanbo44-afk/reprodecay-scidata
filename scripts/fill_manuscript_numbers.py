#!/usr/bin/env python3
"""Fill <<key>> placeholders in the manuscript template from the released outputs.

Every number in the article is computed here from the v0.3.0 tables, ledgers,
summaries and audit outputs, so that the text cannot drift from the data. Prose
keys that summarize a pattern are read from a reviewed JSON file. The script
fails if any placeholder is left unfilled.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
import statistics
from collections import Counter
from datetime import date
from pathlib import Path


def read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def fmt(value: float | int, digits: int = 0) -> str:
    if isinstance(value, int) or digits == 0:
        return f"{int(round(value)):,}"
    return f"{value:,.{digits}f}"


def share(numerator: int, denominator: int) -> str:
    return f"{numerator:,}/{denominator:,} ({100 * numerator / denominator:.1f}\\%)"


def quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def compute(root: Path, dataset: Path, frame: Path, extra: dict[str, str]) -> dict[str, str]:
    tables = dataset / "tables"
    summary = dataset / "summary_tables"
    values: dict[str, str] = {}

    screening = read(frame / "screening_ledger.csv")
    values["N_frame"] = fmt(len(screening))
    values["N_frame_old"] = fmt(sum(row["publication_date"] < "2021" for row in screening))
    values["N_frame_new"] = fmt(sum(row["publication_date"] >= "2021" for row in screening))
    status = Counter(row["screen_status"] for row in screening)
    values["N_meta_eligible"] = fmt(status["eligible_metadata"])
    values["N_meta_excluded"] = fmt(status["excluded"])
    values["N_meta_unresolved"] = fmt(status["unresolved"])

    eligibility = Counter(row["final_eligibility"] for row in read(frame / "archive_eligibility_ledger.csv"))
    values["N_archive_eligible"] = fmt(eligibility["eligible_archive"])
    values["N_archive_excluded"] = fmt(eligibility["excluded_archive"])
    values["N_archive_unresolved"] = fmt(eligibility["unresolved_archive"])

    endpoints = read(dataset / "endpoint_resolution.csv")
    resolved = [row for row in endpoints if row["resolution_status"] == "resolved"]
    values["N_endpoint_resolved"] = fmt(len(resolved))
    values["N_endpoint_unresolved"] = fmt(len(endpoints) - len(resolved))
    # The manuscript attributes every unresolved endpoint to the archive size limit
    # (curl exit 63, --max-filesize); stop if any other error appears.
    errors = Counter(row["resolution_error"] for row in endpoints if row["resolution_status"] != "resolved")
    if set(errors) - {"curl_download_failed:63"}:
        raise SystemExit(f"unresolved endpoints with other errors: {dict(errors)}")
    backfill = json.loads((dataset / "endpoint_resolution_manifest.json").read_text(encoding="utf-8"))["release_date_backfill"]
    if set(backfill["statuses"]) != {"filled"}:
        raise SystemExit(f"release-date backfill left gaps: {backfill['statuses']}")
    values["N_release_date_backfill"] = fmt(backfill["statuses"]["filled"])
    values["N_release"] = fmt(sum(row["endpoint_ref_type"] == "release" for row in resolved))
    values["N_branch"] = fmt(sum(row["endpoint_ref_type"] == "default_branch" for row in resolved))
    values["N_live"] = fmt(sum(row["resolution_mode"] == "live" for row in resolved))
    values["N_retro"] = fmt(sum(row["resolution_mode"] == "retrospective_as_of" for row in resolved))

    projects = read(tables / "project_records.csv")
    values["N_projects"] = fmt(len(projects))
    values["N_projects_old"] = fmt(sum(row["publication_year"] <= "2020" for row in projects))
    values["N_projects_new"] = fmt(sum(row["publication_year"] >= "2021" for row in projects))
    manifest = json.loads((tables / "extraction_manifest.json").read_text(encoding="utf-8"))
    for key, field in (("N_snapshots", "snapshots"), ("N_dep_rows", "dependency_records"), ("N_api_rows", "api_symbol_records"),
                       ("N_delta_rows", "pair_delta_records"), ("N_val_rows", "validation_records"),
                       ("N_decl_rows", "declaration_source_records")):
        values[key] = fmt(manifest[field])

    retro = json.loads((dataset / "retrospective_agreement.json").read_text(encoding="utf-8"))
    values["retro_total"] = fmt(retro["live_resolved_projects"])
    values["retro_same"] = fmt(retro["same_commit"])
    values["retro_diff"] = fmt(retro["live_resolved_projects"] - retro["same_commit"])
    values["retro_same_pct"] = f"{100 * retro['same_commit'] / retro['live_resolved_projects']:.1f}"

    def one_decimal(value: float) -> str:
        return f"{value:.1f}".replace("-0.0", "0.0")

    dated = [row for row in projects if row["article_to_endpoint_ref_days"].lstrip("-").isdigit()]
    if len(dated) != len(projects):
        raise SystemExit(f"article-to-endpoint interval missing for {len(projects) - len(dated)} projects")
    years = [int(row["article_to_endpoint_ref_days"]) / 365.25 for row in dated]
    values["median_interval_years"] = one_decimal(statistics.median(years))
    values["iqr_interval_years"] = f"{one_decimal(quantile(years, 0.25))}--{one_decimal(quantile(years, 0.75))}"
    by_year: dict[str, list[float]] = {}
    for row, value in zip(dated, years):
        by_year.setdefault(row["publication_year"], []).append(value)
    medians = [f"{one_decimal(statistics.median(by_year[year]))} ({year})" for year in sorted(by_year)]
    values["interval_by_year"] = ", ".join(medians[:-1]) + " and " + medians[-1]
    first, last = min(by_year), max(by_year)
    values["max_interval_first_year"] = one_decimal(max(by_year[first]))
    values["max_interval_last_year"] = one_decimal(max(by_year[last]))
    values["first_year"], values["last_year"] = first, last
    negative = [row for row in dated if int(row["article_to_endpoint_ref_days"]) < 0]
    values["N_endpoint_before_article"] = fmt(len(negative))
    values["N_endpoint_between_archive_and_article"] = fmt(
        sum(row["endpoint_ref_date"][:10] >= row["publication_archive_date"] for row in negative))
    snapshots = {row["snapshot_id"]: row["content_tree_sha256"] for row in read(tables / "snapshot_records.csv")}
    identical = sum(snapshots[row["publication_snapshot_id"]] == snapshots[row["endpoint_snapshot_id"]] for row in projects)
    values["N_identical_pairs"] = fmt(identical)
    values["pct_identical_pairs"] = f"{100 * identical / len(projects):.0f}"
    values["min_interval_2022_years"] = f"{(date(2026, 8, 14) - date(2022, 12, 31)).days / 365.25:.1f}"

    families = read(summary / "project_family_delta_metrics.csv")
    for key, family in (("dep_any_change", "dependency"), ("api_any_change", "api"), ("struct_any_change", "structure")):
        rows = [row for row in families if row["family"] == family and row["paired_observation_complete"] == "true"]
        values[key] = share(sum(row["observed_any_change"] == "true" for row in rows), len(rows))

    paired = {(row["metric"], row["summary_basis"]): row for row in read(summary / "paired_change_summary.csv")}
    for key, metric in (("lines", "source_python_line_count"), ("api", "syntactic_api_symbol_count"),
                        ("dep", "declared_dependency_record_count")):
        for suffix, basis in (("", "adequately_observed_pairs"), ("_all", "all_available_values")):
            row = paired[(metric, basis)]
            values[f"{key}_median{suffix}"] = (
                f"{fmt(float(row['absolute_change_median']))} (95\\% interval {fmt(float(row['bootstrap_median_95_lower']))} to "
                f"{fmt(float(row['bootstrap_median_95_upper']))}; n={fmt(int(row['included_pair_projects']))})"
            )

    fields = {(row["status_field"], row["scope"]): row for row in read(summary / "field_observation_summary.csv")}
    dependency_pairs = fields[("dependency_observation_status", "paired_snapshots")]
    values["dep_obs_pairs"] = share(
        int(dependency_pairs["complete_projects"]) + int(dependency_pairs["no_declaration_file_projects"]),
        int(dependency_pairs["total_project_denominator"]),
    )
    declaration_counts = Counter(
        row["observation_status"] for row in read(tables / "declaration_source_records.csv")
        if row["field_kind"] in {"dependency", "file", "setup_kwargs"}
    )
    values["n_unsupported_fields"] = fmt(declaration_counts["unsupported_syntax"])
    values["n_partial_fields"] = fmt(declaration_counts["extracted_partial"])
    values["n_dynamic_fields"] = fmt(declaration_counts["dynamic_metadata"])
    values["n_parse_failure_fields"] = fmt(declaration_counts["parse_failure"])

    forms = {row["form"]: row for row in read(dataset / "declaration_form_scan_summary.csv")}
    values["form_kwargs_snapshots"] = fmt(int(forms["setup_call_kwargs_unpacking"]["snapshots_with_form"]))
    values["form_setup_requires_snapshots"] = fmt(int(forms["setup_requires_or_tests_require"]["snapshots_with_form"]))
    values["form_poetry_snapshots"] = fmt(int(forms["poetry_inline_table_entries"]["snapshots_with_form"]))
    values["form_pipfile_snapshots"] = fmt(int(forms["pipfile"]["snapshots_with_form"]))
    absent = sorted(name for name, row in forms.items() if int(row["snapshots_with_form"]) == 0)
    readable = {"setup_cfg_file_or_attr_reference": "a \\code{file:} or \\code{attr:} reference in \\code{setup.cfg}"}
    if not absent:
        values["absent_forms_sentence"] = "Every form covered by the specification occurs at least once"
    elif len(absent) == 1 and absent[0] in readable:
        values["absent_forms_sentence"] = f"The only form covered by the specification that occurs in no snapshot is {readable[absent[0]]}"
    else:
        values["absent_forms_sentence"] = f"Forms covered by the specification that occur in no snapshot: {len(absent)}"

    test_text = (root / "tests").glob("test_*.py")
    test_count = sum(len(re.findall(r"^\s+def test_", path.read_text(encoding="utf-8"), flags=re.MULTILINE)) for path in test_text)
    values["n_tests"] = fmt(test_count)
    values["n_regression_tests"] = fmt(len(re.findall(r"^\s+def test_", (root / "tests/test_declarations.py").read_text(encoding="utf-8"), flags=re.MULTILINE)))

    independent = json.loads((dataset / "independent_semantic_audit.json").read_text(encoding="utf-8"))
    values["ind_projects"] = fmt(independent["sampled_projects"])
    values["ind_snapshots"] = fmt(independent["sampled_snapshots"])
    api = independent["api"]["symbol_key"]
    dep = independent["dependency"]["exact_record"]
    values["ind_api_agree"] = f"{fmt(api['agreement'])} of {fmt(api['expected'])}"
    values["ind_dep_agree"] = f"{fmt(dep['agreement'])} of {fmt(dep['expected'])}"

    reference_path = dataset / "reference_audit_summary.json"
    if reference_path.exists():
        reference = json.loads(reference_path.read_text(encoding="utf-8"))
        agree = sum(c.get("true", 0) for c in reference["units"].values())
        total = sum(c.get("true", 0) + c.get("false", 0) for c in reference["units"].values())
        sample = read(dataset / "reference_audit_sample.csv")
        values["ref_projects"] = fmt(len({row["project_id"] for row in sample}))
        values["ref_snapshots"] = fmt(len(sample))
        values["ref_items"] = fmt(total)
        values["ref_agree"] = fmt(agree)

    comparison_path = dataset / "release_comparison" / "release_comparison.json"
    if comparison_path.exists():
        comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
        values["affected_projects"] = fmt(comparison["affected_projects"])
        values["unaffected_projects"] = fmt(comparison["unaffected_projects"])
        counts = comparison["summary_values"]
        values["v024_summary_unchanged"] = fmt(counts.get("unchanged", 0))
        values["v024_summary_values"] = fmt(sum(counts.values()))

    figures = root / "manuscript" / "figures"
    # Fig. 2b: the text says no year stands apart; check that before saying it.
    by_family: dict[str, list[dict[str, str]]] = {}
    for row in read(figures / "fig2_change_landscape_source_data.csv"):
        if row["panel"] == "b":
            by_family.setdefault(row["family"], []).append(row)
    ranges = []
    for family, label in (("dependency", "dependencies"), ("api", "interface"), ("structure", "structure")):
        rows = by_family[family]
        if max(float(row["wilson_low"]) for row in rows) > min(float(row["wilson_high"]) for row in rows):
            raise SystemExit(f"year intervals no longer all overlap for {family}; rewrite the year-trend sentence")
        shares = [float(row["proportion"]) for row in rows]
        ranges.append(f"from {min(shares):.2f} to {max(shares):.2f} for {label}")
    values["year_trend_sentence"] = (
        f"these proportions range {', '.join(ranges[:-1])} and {ranges[-1]}, with no consistent direction; "
        "within each family, the 95\\% intervals of all five years overlap.")
    # Fig. 3: transitions between the two snapshots.
    items = {row["indicator"]: row for row in read(figures / "fig3_metadata_transitions_source_data.csv")}

    def moved(indicator: str, kind: str) -> str:
        return fmt(int(items[indicator][f"{kind}_projects"]))

    values["transition_sentence"] = (
        f"Between the two snapshots, \\code{{pyproject.toml}} was added in {moved('pyproject.toml', 'gained')} projects "
        f"and removed in {moved('pyproject.toml', 'lost')}, and a build backend was newly declared in "
        f"{moved('Build backend declared', 'gained')}, whereas \\code{{setup.py}} was removed from "
        f"{moved('setup.py', 'lost')}. GitHub Actions was added in {moved('GitHub Actions', 'gained')} projects and "
        f"Travis CI removed from {moved('Travis CI', 'lost')}. \\code{{CITATION.cff}} was added in "
        f"{moved('CITATION.cff', 'gained')} projects and removed in {moved('CITATION.cff', 'lost')}.")
    values.update(extra)
    return values


def render(template: str, values: dict[str, str], pattern: str) -> tuple[str, list[str]]:
    missing: list[str] = []

    def substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            missing.append(key)
            return match.group(0)
        return values[key]

    return re.sub(pattern, substitute, template), sorted(set(missing))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--dataset", type=Path, default=Path("data/census_v0.3"))
    parser.add_argument("--frame", type=Path, default=Path("data/census_frame_v0.3"))
    parser.add_argument("--prose", type=Path, required=True, help="JSON with reviewed prose keys and DATASET_DOI")
    parser.add_argument("--template", type=Path, default=Path("manuscript/main.template.tex"))
    parser.add_argument("--bib-template", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=Path("manuscript/main.tex"))
    parser.add_argument("--values-out", type=Path, default=Path("manuscript/manuscript_numbers.json"))
    args = parser.parse_args()
    extra = json.loads(args.prose.read_text(encoding="utf-8"))
    values = compute(args.root, args.dataset, args.frame, extra)
    text, missing = render(args.template.read_text(encoding="utf-8"), values, r"<<([A-Za-z0-9_]+)>>")
    if missing:
        raise SystemExit("unfilled placeholders: " + ", ".join(missing))
    args.output.write_text(text, encoding="utf-8")
    if args.bib_template:
        bib, missing = render(args.bib_template.read_text(encoding="utf-8"), values, r"<<([A-Za-z0-9_]+)>>")
        if missing:
            raise SystemExit("unfilled bibliography placeholders: " + ", ".join(missing))
        args.bib_template.with_name("references.bib").write_text(bib, encoding="utf-8")
    args.values_out.write_text(json.dumps(values, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"filled": len(values), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
