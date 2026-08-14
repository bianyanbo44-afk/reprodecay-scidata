#!/usr/bin/env python3
"""Build project-level descriptive summaries for the extracted census dataset.

The project, rather than an extracted dependency, symbol, or delta row, is the
sampling and resampling unit.  All analyses are descriptive.  The script does
not import or execute code from any indexed software archive.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable, Iterable, Sequence


SCHEMA_VERSION = "0.3"
BOOTSTRAP_SEED = 20260814
BOOTSTRAP_REPLICATES = 2000
FAMILIES = ("api", "dependency", "structure")
DELTA_TYPES = ("added", "removed", "changed", "unchanged")
SNAPSHOT_ROLES = ("publication", "endpoint")
PAIRED_METRICS = (
    "source_python_line_count",
    "source_python_file_count",
    "syntactic_api_symbol_count",
    "declared_dependency_record_count",
    "test_python_file_count",
    "ci_workflow_file_count",
)
CORE_SENSITIVITY_METRICS = (
    "source_python_line_count",
    "syntactic_api_symbol_count",
    "declared_dependency_record_count",
)
SENSITIVITY_RANKINGS = (
    "publication_archive_bytes",
    "publication_source_python_line_count",
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_optional_csv(path: Path) -> tuple[list[dict[str, str]], bool]:
    if not path.exists():
        return [], False
    return read_csv(path), True


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_csv(path: Path, rows: Sequence[dict[str, object]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: csv_value(row.get(key)) for key in fieldnames})
    temporary.replace(path)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def csv_value(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    return value


def normalized_doi(value: str) -> str:
    return value.strip().lower().removeprefix("https://doi.org/").removeprefix("http://doi.org/")


def unique_index(
    rows: Iterable[dict[str, str]], key: str | tuple[str, ...], label: str
) -> dict[object, dict[str, str]]:
    index: dict[object, dict[str, str]] = {}
    for row in rows:
        row_key: object
        if isinstance(key, tuple):
            row_key = tuple(row.get(field, "") for field in key)
        else:
            row_key = row.get(key, "")
        if not row_key or (isinstance(row_key, tuple) and any(not part for part in row_key)):
            raise ValueError(f"{label}_missing_key:{row_key}")
        if row_key in index:
            raise ValueError(f"{label}_duplicate_key:{row_key}")
        index[row_key] = row
    return index


def optional_number(value: str | None) -> int | float | None:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    try:
        number: int | float = int(text)
    except ValueError:
        try:
            number = float(text)
        except ValueError:
            return None
    return number if math.isfinite(float(number)) else None


def optional_boolean(value: str | None) -> bool | None:
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def assert_static_only_inputs(named_tables: dict[str, list[dict[str, str]]]) -> None:
    """Refuse to certify inputs that do not affirm the static-only protocol."""
    for table_name, rows in named_tables.items():
        for row_number, row in enumerate(rows, start=2):
            if "third_party_code_executed" not in row:
                continue
            if row["third_party_code_executed"] != "false":
                raise ValueError(
                    f"third_party_code_execution_not_false:{table_name}:row_{row_number}"
                )


def percentile(values: Sequence[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    return float(ordered[lower] * (upper - position) + ordered[upper] * (position - lower))


def median(values: Sequence[float]) -> float:
    result = percentile(values, 0.5)
    if result is None:
        raise ValueError("median_requires_values")
    return result


def mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("mean_requires_values")
    return sum(values) / len(values)


def rounded(value: float | None) -> float | None:
    return None if value is None else round(float(value), 6)


def bootstrap_interval(
    values: Sequence[float],
    statistic: Callable[[Sequence[float]], float],
    *,
    seed: int = BOOTSTRAP_SEED,
    replicates: int = BOOTSTRAP_REPLICATES,
) -> tuple[float | None, float | None]:
    """Return a deterministic project-resampling percentile interval."""
    if not values:
        return None, None
    generator = random.Random(seed)
    sample_size = len(values)
    estimates = [
        statistic([values[generator.randrange(sample_size)] for _ in range(sample_size)])
        for _ in range(replicates)
    ]
    return rounded(percentile(estimates, 0.025)), rounded(percentile(estimates, 0.975))


def safe_proportion(numerator: int | float, denominator: int) -> float | None:
    return rounded(float(numerator) / denominator) if denominator else None


def percentage_change(publication: float | None, endpoint: float | None) -> tuple[float | None, str]:
    if publication is None and endpoint is None:
        return None, "missing_both"
    if publication is None:
        return None, "missing_publication"
    if endpoint is None:
        return None, "missing_endpoint"
    if publication == 0:
        return None, "undefined_zero_publication_denominator"
    return rounded((endpoint - publication) / publication), "defined"


def parse_pair_status(
    family: str,
    publication: dict[str, str] | None,
    endpoint: dict[str, str] | None,
    delta_statuses: set[str],
    delta_count: int,
) -> tuple[bool | None, bool | None, bool | None, str]:
    if family == "structure":
        pub_complete = publication is not None
        end_complete = endpoint is not None
    else:
        field = f"{family}_parse_complete"
        pub_complete = optional_boolean(publication.get(field)) if publication else None
        end_complete = optional_boolean(endpoint.get(field)) if endpoint else None
    if pub_complete is None or end_complete is None:
        paired_complete: bool | None = None
    else:
        paired_complete = pub_complete and end_complete
    if "unavailable" in delta_statuses:
        comparison_status = "unavailable"
    elif "partial_parse" in delta_statuses or paired_complete is False:
        comparison_status = "partial_parse"
    elif paired_complete is None:
        comparison_status = "unavailable"
    elif delta_count == 0:
        comparison_status = "comparable_zero_features"
    else:
        comparison_status = "comparable"
    return pub_complete, end_complete, paired_complete, comparison_status


def build_project_products(
    projects: list[dict[str, str]],
    snapshots: list[dict[str, str]],
    structures: list[dict[str, str]],
    deltas: list[dict[str, str]],
    validations: list[dict[str, str]],
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
]:
    project_index = unique_index(projects, "project_id", "project_records")
    snapshot_index = unique_index(snapshots, ("project_id", "snapshot_role"), "snapshot_records")
    structure_index = unique_index(structures, ("project_id", "snapshot_role"), "structure_records")
    unique_index(
        deltas,
        ("pair_id", "family", "feature_group", "feature_key"),
        "pair_delta_records",
    )
    delta_groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in deltas:
        if row.get("project_id") not in project_index:
            raise ValueError(f"delta_unknown_project:{row.get('project_id', '')}")
        if row.get("family") not in FAMILIES:
            raise ValueError(f"delta_unknown_family:{row.get('family', '')}")
        delta_groups[(row["project_id"], row["family"])].append(row)
    validation_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in validations:
        validation_counts[row.get("project_id", "")][row.get("status", "missing") or "missing"] += 1

    project_rows: list[dict[str, object]] = []
    family_rows: list[dict[str, object]] = []
    parsing_rows: list[dict[str, object]] = []
    paired_rows: list[dict[str, object]] = []
    ordered_projects = sorted(
        projects,
        key=lambda row: (int(row["selection_rank"]) if row.get("selection_rank", "").isdigit() else math.inf, row["project_id"]),
    )
    for project in ordered_projects:
        project_id = project["project_id"]
        publication_snapshot = snapshot_index.get((project_id, "publication"))
        endpoint_snapshot = snapshot_index.get((project_id, "endpoint"))
        publication = structure_index.get((project_id, "publication"))
        endpoint = structure_index.get((project_id, "endpoint"))
        project_row: dict[str, object] = {
            "project_id": project_id,
            "pair_id": project.get("pair_id", ""),
            "scholarly_doi": normalized_doi(project.get("scholarly_doi", "")),
            "selection_rank": project.get("selection_rank", ""),
            "endpoint_ref_type": project.get("endpoint_ref_type", "") or "missing",
            "publication_archive_bytes": optional_number(
                publication_snapshot.get("archive_bytes") if publication_snapshot else None
            ),
            "endpoint_archive_bytes": optional_number(
                endpoint_snapshot.get("archive_bytes") if endpoint_snapshot else None
            ),
            "validation_pass_count": validation_counts[project_id]["pass"],
            "validation_warning_count": validation_counts[project_id]["warning"],
            "validation_error_count": validation_counts[project_id]["error"],
            "validation_other_status_count": sum(
                count
                for status, count in validation_counts[project_id].items()
                if status not in {"pass", "warning", "error"}
            ),
            "validation_record_count": sum(validation_counts[project_id].values()),
        }
        for metric in PAIRED_METRICS:
            pub_value = optional_number(publication.get(metric) if publication else None)
            end_value = optional_number(endpoint.get(metric) if endpoint else None)
            change = end_value - pub_value if pub_value is not None and end_value is not None else None
            percent_change, percent_status = percentage_change(pub_value, end_value)
            pair_status = "complete" if change is not None else (
                "missing_both" if pub_value is None and end_value is None
                else "missing_publication" if pub_value is None
                else "missing_endpoint"
            )
            project_row[f"publication_{metric}"] = pub_value
            project_row[f"endpoint_{metric}"] = end_value
            project_row[f"{metric}_absolute_change"] = change
            project_row[f"{metric}_proportional_change"] = percent_change
            project_row[f"{metric}_pair_status"] = pair_status
            paired_rows.append(
                {
                    "project_id": project_id,
                    "scholarly_doi": normalized_doi(project.get("scholarly_doi", "")),
                    "metric": metric,
                    "publication_value": pub_value,
                    "endpoint_value": end_value,
                    "absolute_change": change,
                    "proportional_change_from_publication": percent_change,
                    "proportional_change_denominator": pub_value,
                    "proportional_change_status": percent_status,
                    "pair_status": pair_status,
                }
            )

        for family in FAMILIES:
            rows = delta_groups[(project_id, family)]
            counts = Counter(row.get("delta_type", "unavailable") or "unavailable" for row in rows)
            statuses = {row.get("comparison_status", "unavailable") or "unavailable" for row in rows}
            pub_complete, end_complete, paired_complete, comparison_status = parse_pair_status(
                family, publication, endpoint, statuses, len(rows)
            )
            interpretable_denominator = sum(counts[kind] for kind in DELTA_TYPES)
            unknown_delta_count = len(rows) - interpretable_denominator
            observed_any_change = any(counts[kind] > 0 for kind in ("added", "removed", "changed"))
            if observed_any_change:
                any_change_status = "observed_change"
            elif paired_complete is True:
                any_change_status = "none_observed_complete_comparison"
            elif comparison_status == "partial_parse":
                any_change_status = "none_observed_partial_comparison"
            else:
                any_change_status = "unavailable"
            family_row: dict[str, object] = {
                "project_id": project_id,
                "scholarly_doi": normalized_doi(project.get("scholarly_doi", "")),
                "family": family,
                "comparison_status": comparison_status,
                "publication_parse_complete": pub_complete,
                "endpoint_parse_complete": end_complete,
                "paired_parse_complete": paired_complete,
                "observed_delta_record_count": len(rows),
                "interpretable_delta_denominator": interpretable_denominator,
                "unavailable_or_unknown_delta_count": unknown_delta_count,
                "observed_any_change": observed_any_change,
                "any_change_status": any_change_status,
            }
            for kind in DELTA_TYPES:
                family_row[f"{kind}_count"] = counts[kind]
                family_row[f"{kind}_proportion"] = safe_proportion(
                    counts[kind], interpretable_denominator
                )
            family_rows.append(family_row)
            parsing_rows.append(
                {
                    "project_id": project_id,
                    "scholarly_doi": normalized_doi(project.get("scholarly_doi", "")),
                    "family": family,
                    "publication_parse_complete": pub_complete,
                    "endpoint_parse_complete": end_complete,
                    "paired_parse_complete": paired_complete,
                    "comparison_status": comparison_status,
                    "observed_delta_record_count": len(rows),
                    "missingness_reason": (
                        ""
                        if paired_complete is not None
                        else "missing_structure_record_or_parse_flag"
                    ),
                }
            )
            project_row[f"{family}_comparison_status"] = comparison_status
            project_row[f"{family}_paired_parse_complete"] = paired_complete
            project_row[f"{family}_interpretable_delta_denominator"] = interpretable_denominator
            for kind in DELTA_TYPES:
                project_row[f"{family}_{kind}_count"] = counts[kind]
                project_row[f"{family}_{kind}_proportion"] = family_row[f"{kind}_proportion"]
        project_rows.append(project_row)
    return project_rows, family_rows, parsing_rows, paired_rows


def summarize_paired_changes(
    projects: list[dict[str, str]], paired_rows: list[dict[str, object]]
) -> list[dict[str, object]]:
    by_metric: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in paired_rows:
        by_metric[str(row["metric"])].append(row)
    output: list[dict[str, object]] = []
    for metric in PAIRED_METRICS:
        rows = by_metric[metric]
        complete_rows = [row for row in rows if row["absolute_change"] is not None]
        changes = [float(row["absolute_change"]) for row in complete_rows]
        proportional_changes = [
            float(row["proportional_change_from_publication"])
            for row in rows
            if row["proportional_change_from_publication"] is not None
        ]
        zero_denominators = sum(
            row["proportional_change_status"] == "undefined_zero_publication_denominator"
            for row in rows
        )
        pub_values = [float(row["publication_value"]) for row in rows if row["publication_value"] is not None]
        end_values = [float(row["endpoint_value"]) for row in rows if row["endpoint_value"] is not None]
        paired_pub_values = [float(row["publication_value"]) for row in complete_rows]
        paired_end_values = [float(row["endpoint_value"]) for row in complete_rows]
        lower, upper = bootstrap_interval(changes, median)
        proportional_lower, proportional_upper = bootstrap_interval(proportional_changes, median)
        output.append(
            {
                "metric": metric,
                "total_project_denominator": len(projects),
                "complete_pair_projects": len(changes),
                "missing_pair_projects": len(projects) - len(changes),
                "publication_value_projects": len(pub_values),
                "endpoint_value_projects": len(end_values),
                "publication_paired_median": rounded(percentile(paired_pub_values, 0.5)),
                "endpoint_paired_median": rounded(percentile(paired_end_values, 0.5)),
                "absolute_change_mean": rounded(mean(changes)) if changes else None,
                "absolute_change_median": rounded(percentile(changes, 0.5)),
                "absolute_change_q1": rounded(percentile(changes, 0.25)),
                "absolute_change_q3": rounded(percentile(changes, 0.75)),
                "absolute_change_min": rounded(min(changes)) if changes else None,
                "absolute_change_max": rounded(max(changes)) if changes else None,
                "bootstrap_median_95_lower": lower,
                "bootstrap_median_95_upper": upper,
                "proportional_change_defined_projects": len(proportional_changes),
                "proportional_change_zero_denominator_projects": zero_denominators,
                "proportional_change_missing_projects": (
                    len(projects) - len(proportional_changes) - zero_denominators
                ),
                "proportional_change_median": rounded(percentile(proportional_changes, 0.5)),
                "proportional_change_bootstrap_median_95_lower": proportional_lower,
                "proportional_change_bootstrap_median_95_upper": proportional_upper,
                "bootstrap_unit": "project",
                "bootstrap_seed": BOOTSTRAP_SEED,
                "bootstrap_replicates": BOOTSTRAP_REPLICATES,
            }
        )
    return output


def summarize_families(
    projects: list[dict[str, str]], family_rows: list[dict[str, object]]
) -> list[dict[str, object]]:
    grouped = {(str(row["project_id"]), str(row["family"])): row for row in family_rows}
    output: list[dict[str, object]] = []
    for family in FAMILIES:
        all_rows = [grouped[(project["project_id"], family)] for project in projects]
        complete_rows = [row for row in all_rows if row["paired_parse_complete"] is True]
        incomplete_count = len(projects) - len(complete_rows)
        for scope, scoped_rows in (
            ("all_extracted_projects_observed", all_rows),
            ("complete_family_comparisons", complete_rows),
        ):
            denominator = len(scoped_rows)
            for kind in DELTA_TYPES:
                with_any = sum(int(row[f"{kind}_count"]) > 0 for row in scoped_rows)
                output.append(
                    {
                        "family": family,
                        "delta_type": kind,
                        "scope": scope,
                        "projects_with_at_least_one": with_any,
                        "project_denominator": denominator,
                        "project_proportion": safe_proportion(with_any, denominator),
                        "projects_missing_or_incomplete": incomplete_count,
                        "denominator_definition": (
                            "all projects in project_records.csv"
                            if scope == "all_extracted_projects_observed"
                            else "projects with complete publication and endpoint parsing for the family"
                        ),
                    }
                )
    return output


def summarize_parsing(
    projects: list[dict[str, str]], parsing_rows: list[dict[str, object]]
) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for family in FAMILIES:
        rows = [row for row in parsing_rows if row["family"] == family]
        for scope, field in (
            ("publication_snapshot", "publication_parse_complete"),
            ("endpoint_snapshot", "endpoint_parse_complete"),
            ("paired_snapshots", "paired_parse_complete"),
        ):
            complete = sum(row[field] is True for row in rows)
            incomplete = sum(row[field] is False for row in rows)
            missing = sum(row[field] is None for row in rows)
            output.append(
                {
                    "family": family,
                    "scope": scope,
                    "complete_projects": complete,
                    "incomplete_projects": incomplete,
                    "missing_status_projects": missing,
                    "total_project_denominator": len(projects),
                    "complete_proportion": safe_proportion(complete, len(projects)),
                    "denominator_definition": "all projects in project_records.csv",
                }
            )
    return output


def rows_by_normalized_doi(
    rows: Iterable[dict[str, str]], field: str, label: str
) -> dict[str, dict[str, str]]:
    output: dict[str, dict[str, str]] = {}
    for row in rows:
        doi = normalized_doi(row.get(field, ""))
        if not doi:
            raise ValueError(f"{label}_missing_doi")
        if doi in output:
            raise ValueError(f"{label}_duplicate_doi:{doi}")
        output[doi] = row
    return output


def build_selection_products(
    projects: list[dict[str, str]],
    screening: list[dict[str, str]],
    archive_eligibility: list[dict[str, str]],
    endpoint_resolution: list[dict[str, str]],
    *,
    screening_available: bool,
    archive_available: bool,
    endpoint_available: bool,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    project_dois = {normalized_doi(row.get("scholarly_doi", "")) for row in projects}
    screen_index = rows_by_normalized_doi(screening, "doi", "screening_ledger") if screening else {}
    archive_index = (
        rows_by_normalized_doi(archive_eligibility, "scholarly_doi", "archive_eligibility_ledger")
        if archive_eligibility else {}
    )
    endpoint_index = (
        rows_by_normalized_doi(endpoint_resolution, "scholarly_doi", "endpoint_resolution")
        if endpoint_resolution else {}
    )
    all_screened = set(screen_index)
    metadata_eligible = {
        doi for doi, row in screen_index.items() if row.get("screen_status") == "eligible_metadata"
    }
    archive_assessed = metadata_eligible & set(archive_index)
    archive_eligible_ids = {
        doi for doi in archive_assessed
        if archive_index[doi].get("final_eligibility") == "eligible_archive"
    }
    endpoint_assessed = archive_eligible_ids & set(endpoint_index)
    endpoint_resolved_ids = {
        doi for doi in endpoint_assessed
        if endpoint_index[doi].get("resolution_status") == "resolved"
    }
    extracted_ids = project_dois
    extracted_in_prior_stage = (
        extracted_ids & endpoint_resolved_ids if endpoint_available else extracted_ids
    )
    extracted_outside_prior_stage = (
        extracted_ids - endpoint_resolved_ids if endpoint_available else set()
    )

    def stage_row(
        order: int,
        stage: str,
        retained: set[str],
        denominator_ids: set[str],
        assessed: set[str],
        excluded: set[str],
        unresolved: set[str],
        source_available: bool,
        denominator_stage: str,
    ) -> dict[str, object]:
        return {
            "stage_order": order,
            "stage": stage,
            "retained_projects": len(retained),
            "stage_denominator_projects": len(denominator_ids),
            "retention_proportion": safe_proportion(len(retained), len(denominator_ids)),
            "assessed_projects": len(assessed),
            "excluded_projects": len(excluded),
            "unresolved_projects": len(unresolved),
            "not_assessed_projects": len(denominator_ids - assessed),
            "denominator_stage": denominator_stage,
            "source_available": source_available,
        }

    screen_excluded = {doi for doi, row in screen_index.items() if row.get("screen_status") == "excluded"}
    screen_unresolved = {doi for doi, row in screen_index.items() if row.get("screen_status") == "unresolved"}
    archive_excluded = {
        doi for doi in archive_assessed
        if archive_index[doi].get("final_eligibility") == "excluded_archive"
    }
    archive_unresolved = {
        doi for doi in archive_assessed
        if archive_index[doi].get("final_eligibility") == "unresolved_archive"
    }
    endpoint_unresolved = {
        doi for doi in endpoint_assessed
        if endpoint_index[doi].get("resolution_status") != "resolved"
    }
    attrition_rows = [
        stage_row(
            1, "joss_records_screened", all_screened, all_screened, all_screened,
            set(), set(), screening_available, "self",
        ),
        stage_row(
            2, "metadata_eligible", metadata_eligible, all_screened, all_screened,
            screen_excluded, screen_unresolved, screening_available, "joss_records_screened",
        ),
        stage_row(
            3, "publication_archive_eligible", archive_eligible_ids, metadata_eligible,
            archive_assessed, archive_excluded, archive_unresolved, archive_available,
            "metadata_eligible",
        ),
        stage_row(
            4, "endpoint_resolved", endpoint_resolved_ids, archive_eligible_ids,
            endpoint_assessed, set(), endpoint_unresolved, endpoint_available,
            "publication_archive_eligible",
        ),
        stage_row(
            5, "paired_dataset_extracted", extracted_in_prior_stage,
            endpoint_resolved_ids if endpoint_available else extracted_ids,
            extracted_in_prior_stage, set(), set(), True, "endpoint_resolved",
        ),
    ]

    status_rows: list[dict[str, object]] = []

    def append_statuses(
        stage: str,
        denominator_ids: set[str],
        index: dict[str, dict[str, str]],
        status_field: str,
        reason_field: str,
    ) -> None:
        counter: Counter[tuple[str, str]] = Counter()
        for doi in denominator_ids:
            row = index.get(doi)
            if row is None:
                counter[("not_assessed", "missing_ledger_row")] += 1
            else:
                counter[(row.get(status_field, "") or "missing_status", row.get(reason_field, ""))] += 1
        for (status, reason), count in sorted(counter.items()):
            status_rows.append(
                {
                    "stage": stage,
                    "status": status,
                    "reason_code": reason,
                    "project_count": count,
                    "stage_denominator_projects": len(denominator_ids),
                    "project_proportion": safe_proportion(count, len(denominator_ids)),
                }
            )

    append_statuses("metadata_screening", all_screened, screen_index, "screen_status", "reason_code")
    append_statuses(
        "publication_archive_eligibility", metadata_eligible, archive_index,
        "final_eligibility", "reason_code",
    )
    append_statuses(
        "endpoint_resolution", archive_eligible_ids, endpoint_index,
        "resolution_status", "resolution_error",
    )
    extraction_denominator = endpoint_resolved_ids if endpoint_available else project_dois
    extraction_counter = Counter(
        "extracted" if doi in project_dois else "not_extracted" for doi in extraction_denominator
    )
    for status, count in sorted(extraction_counter.items()):
        status_rows.append(
            {
                "stage": "paired_dataset_extraction",
                "status": status,
                "reason_code": "" if status == "extracted" else "missing_project_record",
                "project_count": count,
                "stage_denominator_projects": len(extraction_denominator),
                "project_proportion": safe_proportion(count, len(extraction_denominator)),
            }
        )
    if extracted_outside_prior_stage:
        status_rows.append(
            {
                "stage": "paired_dataset_extraction",
                "status": "extracted_outside_endpoint_resolved_frame",
                "reason_code": "project_record_not_in_resolved_endpoint_ledger",
                "project_count": len(extracted_outside_prior_stage),
                "stage_denominator_projects": len(extraction_denominator),
                "project_proportion": None,
            }
        )
    return attrition_rows, status_rows


def endpoint_type_summary(
    projects: list[dict[str, str]],
    archive_eligibility: list[dict[str, str]],
    endpoint_resolution: list[dict[str, str]],
) -> list[dict[str, object]]:
    archive_eligible = {
        normalized_doi(row.get("scholarly_doi", ""))
        for row in archive_eligibility
        if row.get("final_eligibility") == "eligible_archive"
    }
    endpoint_by_doi = {
        normalized_doi(row.get("scholarly_doi", "")): row for row in endpoint_resolution
    }
    scopes: list[tuple[str, list[str]]] = []
    eligible_types: list[str] = []
    for doi in sorted(archive_eligible):
        endpoint = endpoint_by_doi.get(doi)
        if endpoint is None:
            eligible_types.append("not_assessed")
        elif endpoint.get("resolution_status") != "resolved":
            eligible_types.append("unresolved")
        else:
            eligible_types.append(endpoint.get("endpoint_ref_type", "") or "missing_ref_type")
    scopes.append(("archive_eligible_projects", eligible_types))
    resolved_types = [
        row.get("endpoint_ref_type", "") or "missing_ref_type"
        for doi, row in endpoint_by_doi.items()
        if doi in archive_eligible and row.get("resolution_status") == "resolved"
    ]
    scopes.append(("resolved_endpoint_projects", resolved_types))
    scopes.append(
        (
            "extracted_project_pairs",
            [row.get("endpoint_ref_type", "") or "missing_ref_type" for row in projects],
        )
    )
    output: list[dict[str, object]] = []
    for scope, values in scopes:
        counts = Counter(values)
        for ref_type, count in sorted(counts.items()):
            output.append(
                {
                    "scope": scope,
                    "endpoint_ref_type": ref_type,
                    "project_count": count,
                    "project_denominator": len(values),
                    "project_proportion": safe_proportion(count, len(values)),
                    "missing_or_unresolved_projects": sum(
                        counts[kind] for kind in ("missing_ref_type", "not_assessed", "unresolved")
                    ),
                }
            )
    return output


def sensitivity_project_sets(
    project_metrics: list[dict[str, object]], ranking_metric: str
) -> list[dict[str, object]]:
    ranked = [
        (str(row["project_id"]), float(row[ranking_metric]))
        for row in project_metrics
        if row.get(ranking_metric) is not None
    ]
    ranked.sort(key=lambda item: (-item[1], item[0]))
    rankable_ids = {project_id for project_id, _ in ranked}
    scenarios: list[dict[str, object]] = []
    for label, fraction in (("rank_complete_baseline", 0.0), ("exclude_top_1_percent", 0.01), ("exclude_top_5_percent", 0.05)):
        remove_count = math.ceil(fraction * len(ranked)) if ranked and fraction else 0
        excluded = ranked[:remove_count]
        excluded_ids = {project_id for project_id, _ in excluded}
        retained_ids = rankable_ids - excluded_ids
        scenarios.append(
            {
                "scenario": f"{label}_by_{ranking_metric}",
                "exclusion_fraction": fraction,
                "ranking_metric": ranking_metric,
                "rankable_projects": len(ranked),
                "missing_ranking_projects": len(project_metrics) - len(ranked),
                "excluded_projects": len(excluded_ids),
                "retained_project_ids": retained_ids,
                "excluded_project_ids": ";".join(project_id for project_id, _ in excluded),
                "smallest_excluded_value": min((value for _, value in excluded), default=None),
                "largest_retained_value": max(
                    (value for project_id, value in ranked if project_id in retained_ids), default=None
                ),
            }
        )
    return scenarios


def build_sensitivity_summary(
    project_metrics: list[dict[str, object]], family_rows: list[dict[str, object]]
) -> list[dict[str, object]]:
    metrics_by_project = {str(row["project_id"]): row for row in project_metrics}
    families_by_project = {
        (str(row["project_id"]), str(row["family"])): row for row in family_rows
    }
    output: list[dict[str, object]] = []
    for ranking_metric in SENSITIVITY_RANKINGS:
        for scenario in sensitivity_project_sets(project_metrics, ranking_metric):
            retained = set(scenario["retained_project_ids"])
            common = {
                key: value for key, value in scenario.items() if key != "retained_project_ids"
            }
            for metric in CORE_SENSITIVITY_METRICS:
                changes = [
                    float(metrics_by_project[project_id][f"{metric}_absolute_change"])
                    for project_id in sorted(retained)
                    if metrics_by_project[project_id].get(f"{metric}_absolute_change") is not None
                ]
                lower, upper = bootstrap_interval(changes, median)
                output.append(
                    {
                        **common,
                        "analysis_type": "paired_absolute_change",
                        "metric": metric,
                        "family": "",
                        "statistic": "median",
                        "estimate": rounded(percentile(changes, 0.5)),
                        "bootstrap_95_lower": lower,
                        "bootstrap_95_upper": upper,
                        "analysis_project_denominator": len(retained),
                        "available_projects": len(changes),
                        "missing_analysis_projects": len(retained) - len(changes),
                        "denominator_definition": "rank-complete retained projects with paired metric values",
                    }
                )
            for family in FAMILIES:
                complete_rows = [
                    families_by_project[(project_id, family)]
                    for project_id in sorted(retained)
                    if families_by_project[(project_id, family)]["paired_parse_complete"] is True
                ]
                indicators = [float(bool(row["observed_any_change"])) for row in complete_rows]
                lower, upper = bootstrap_interval(indicators, mean)
                output.append(
                    {
                        **common,
                        "analysis_type": "projects_with_any_observed_change",
                        "metric": "any_added_removed_or_changed_feature",
                        "family": family,
                        "statistic": "proportion",
                        "estimate": rounded(mean(indicators)) if indicators else None,
                        "bootstrap_95_lower": lower,
                        "bootstrap_95_upper": upper,
                        "analysis_project_denominator": len(retained),
                        "available_projects": len(indicators),
                        "missing_analysis_projects": len(retained) - len(indicators),
                        "denominator_definition": "rank-complete retained projects with complete family comparison",
                    }
                )
    return output


PROJECT_BASE_FIELDS = [
    "project_id", "pair_id", "scholarly_doi", "selection_rank", "endpoint_ref_type",
    "publication_archive_bytes", "endpoint_archive_bytes",
    "validation_pass_count", "validation_warning_count", "validation_error_count",
    "validation_other_status_count", "validation_record_count",
]
PROJECT_FIELDS = PROJECT_BASE_FIELDS + [
    field
    for metric in PAIRED_METRICS
    for field in (
        f"publication_{metric}", f"endpoint_{metric}", f"{metric}_absolute_change",
        f"{metric}_proportional_change", f"{metric}_pair_status",
    )
] + [
    field
    for family in FAMILIES
    for field in (
        f"{family}_comparison_status", f"{family}_paired_parse_complete",
        f"{family}_interpretable_delta_denominator",
        *(name for kind in DELTA_TYPES for name in (f"{family}_{kind}_count", f"{family}_{kind}_proportion")),
    )
]
FAMILY_FIELDS = [
    "project_id", "scholarly_doi", "family", "comparison_status",
    "publication_parse_complete", "endpoint_parse_complete", "paired_parse_complete",
    "observed_delta_record_count", "interpretable_delta_denominator",
    "unavailable_or_unknown_delta_count", "observed_any_change", "any_change_status",
    *(name for kind in DELTA_TYPES for name in (f"{kind}_count", f"{kind}_proportion")),
]
PARSING_FIELDS = [
    "project_id", "scholarly_doi", "family", "publication_parse_complete",
    "endpoint_parse_complete", "paired_parse_complete", "comparison_status",
    "observed_delta_record_count", "missingness_reason",
]
PAIRED_FIELDS = [
    "project_id", "scholarly_doi", "metric", "publication_value", "endpoint_value",
    "absolute_change", "proportional_change_from_publication",
    "proportional_change_denominator", "proportional_change_status", "pair_status",
]
PAIRED_SUMMARY_FIELDS = [
    "metric", "total_project_denominator", "complete_pair_projects", "missing_pair_projects",
    "publication_value_projects", "endpoint_value_projects", "publication_paired_median",
    "endpoint_paired_median",
    "absolute_change_mean", "absolute_change_median", "absolute_change_q1", "absolute_change_q3",
    "absolute_change_min", "absolute_change_max", "bootstrap_median_95_lower",
    "bootstrap_median_95_upper", "proportional_change_defined_projects",
    "proportional_change_zero_denominator_projects", "proportional_change_missing_projects",
    "proportional_change_median", "proportional_change_bootstrap_median_95_lower",
    "proportional_change_bootstrap_median_95_upper", "bootstrap_unit", "bootstrap_seed",
    "bootstrap_replicates",
]
FAMILY_SUMMARY_FIELDS = [
    "family", "delta_type", "scope", "projects_with_at_least_one", "project_denominator",
    "project_proportion", "projects_missing_or_incomplete", "denominator_definition",
]
PARSING_SUMMARY_FIELDS = [
    "family", "scope", "complete_projects", "incomplete_projects", "missing_status_projects",
    "total_project_denominator", "complete_proportion", "denominator_definition",
]
ATTRITION_FIELDS = [
    "stage_order", "stage", "retained_projects", "stage_denominator_projects",
    "retention_proportion", "assessed_projects", "excluded_projects", "unresolved_projects",
    "not_assessed_projects", "denominator_stage", "source_available",
]
STATUS_FIELDS = [
    "stage", "status", "reason_code", "project_count", "stage_denominator_projects",
    "project_proportion",
]
ENDPOINT_FIELDS = [
    "scope", "endpoint_ref_type", "project_count", "project_denominator",
    "project_proportion", "missing_or_unresolved_projects",
]
SENSITIVITY_FIELDS = [
    "ranking_metric", "scenario", "exclusion_fraction", "rankable_projects",
    "missing_ranking_projects", "excluded_projects", "excluded_project_ids",
    "smallest_excluded_value", "largest_retained_value", "analysis_type", "metric", "family",
    "statistic", "estimate", "bootstrap_95_lower", "bootstrap_95_upper",
    "analysis_project_denominator", "available_projects", "missing_analysis_projects",
    "denominator_definition",
]


def build_summary(
    tables: Path,
    output: Path,
    summary_dir: Path,
    screening_path: Path,
    archive_eligibility_path: Path,
    endpoint_resolution_path: Path,
) -> dict[str, object]:
    projects = read_csv(tables / "project_records.csv")
    snapshots = read_csv(tables / "snapshot_records.csv")
    structures = read_csv(tables / "structure_records.csv")
    dependencies = read_csv(tables / "dependency_records.csv")
    symbols = read_csv(tables / "api_symbol_records.csv")
    deltas = read_csv(tables / "pair_delta_records.csv")
    validations = read_csv(tables / "validation_records.csv")
    screening, screening_available = read_optional_csv(screening_path)
    archive_eligibility, archive_available = read_optional_csv(archive_eligibility_path)
    endpoint_resolution, endpoint_available = read_optional_csv(endpoint_resolution_path)

    assert_static_only_inputs(
        {
            "snapshot_records": snapshots,
            "structure_records": structures,
            "screening_ledger": screening,
            "archive_eligibility_ledger": archive_eligibility,
            "endpoint_resolution": endpoint_resolution,
        }
    )

    project_metrics, family_metrics, parsing_metrics, paired_changes = build_project_products(
        projects, snapshots, structures, deltas, validations
    )
    paired_summary = summarize_paired_changes(projects, paired_changes)
    family_summary = summarize_families(projects, family_metrics)
    parsing_summary = summarize_parsing(projects, parsing_metrics)
    attrition, selection_status = build_selection_products(
        projects,
        screening,
        archive_eligibility,
        endpoint_resolution,
        screening_available=screening_available,
        archive_available=archive_available,
        endpoint_available=endpoint_available,
    )
    endpoint_summary = endpoint_type_summary(projects, archive_eligibility, endpoint_resolution)
    sensitivity = build_sensitivity_summary(project_metrics, family_metrics)

    outputs = {
        "project_metrics": ("project_level_metrics.csv", project_metrics, PROJECT_FIELDS),
        "project_family_metrics": ("project_family_delta_metrics.csv", family_metrics, FAMILY_FIELDS),
        "project_parsing_completeness": ("project_parsing_completeness.csv", parsing_metrics, PARSING_FIELDS),
        "paired_changes": ("publication_endpoint_paired_changes.csv", paired_changes, PAIRED_FIELDS),
        "paired_change_summary": ("paired_change_summary.csv", paired_summary, PAIRED_SUMMARY_FIELDS),
        "family_project_summary": ("family_project_summary.csv", family_summary, FAMILY_SUMMARY_FIELDS),
        "parsing_completeness_summary": ("parsing_completeness_summary.csv", parsing_summary, PARSING_SUMMARY_FIELDS),
        "selection_attrition": ("selection_attrition.csv", attrition, ATTRITION_FIELDS),
        "selection_status_counts": ("selection_status_counts.csv", selection_status, STATUS_FIELDS),
        "endpoint_type_summary": ("endpoint_type_summary.csv", endpoint_summary, ENDPOINT_FIELDS),
        "large_project_sensitivity": ("large_project_sensitivity.csv", sensitivity, SENSITIVITY_FIELDS),
    }
    output_metadata: dict[str, dict[str, object]] = {}
    for name, (filename, rows, fields) in outputs.items():
        path = summary_dir / filename
        write_csv(path, rows, fields)
        output_metadata[name] = {
            "path": path.as_posix(),
            "rows": len(rows),
            "sha256": sha256_file(path),
        }

    paired_by_metric = {str(row["metric"]): row for row in paired_summary}
    endpoint_types_extracted = {
        str(row["endpoint_ref_type"]): int(row["project_count"])
        for row in endpoint_summary if row["scope"] == "extracted_project_pairs"
    }
    input_paths = {
        "project_records": tables / "project_records.csv",
        "snapshot_records": tables / "snapshot_records.csv",
        "structure_records": tables / "structure_records.csv",
        "dependency_records": tables / "dependency_records.csv",
        "api_symbol_records": tables / "api_symbol_records.csv",
        "pair_delta_records": tables / "pair_delta_records.csv",
        "validation_records": tables / "validation_records.csv",
        "screening_ledger": screening_path,
        "archive_eligibility_ledger": archive_eligibility_path,
        "endpoint_resolution": endpoint_resolution_path,
    }
    result: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "analysis_design": {
            "sampling_unit": "project",
            "aggregation_unit": "project",
            "bootstrap_unit": "project",
            "bootstrap_method": "nonparametric percentile interval",
            "bootstrap_seed": BOOTSTRAP_SEED,
            "bootstrap_replicates": BOOTSTRAP_REPLICATES,
            "analysis_scope": "descriptive summaries of the observed census dataset",
            "causal_inference_performed": False,
            "significance_tests_performed": False,
            "third_party_code_executed": False,
        },
        "projects": len(projects),
        "snapshots": len(snapshots),
        "endpoint_ref_types": endpoint_types_extracted,
        "records": {
            "dependencies": len(dependencies),
            "api_symbols": len(symbols),
            "pair_deltas": len(deltas),
            "validation": len(validations),
        },
        "selection_attrition": attrition,
        "parsing_completeness": parsing_summary,
        "family_project_summary": family_summary,
        "paired_change_summary": paired_summary,
        "median_change": {
            legacy_name: paired_by_metric[metric]["absolute_change_median"]
            for legacy_name, metric in (
                ("source_python_lines", "source_python_line_count"),
                ("syntactic_api_symbols", "syntactic_api_symbol_count"),
                ("declared_dependency_records", "declared_dependency_record_count"),
            )
        },
        "bootstrap_95_ci_median_change": {
            legacy_name: [
                paired_by_metric[metric]["bootstrap_median_95_lower"],
                paired_by_metric[metric]["bootstrap_median_95_upper"],
            ]
            for legacy_name, metric in (
                ("source_python_lines", "source_python_line_count"),
                ("syntactic_api_symbols", "syntactic_api_symbol_count"),
                ("declared_dependency_records", "declared_dependency_record_count"),
            )
        },
        "bootstrap_seed": BOOTSTRAP_SEED,
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "large_project_sensitivity": {
            "ranking_metrics": list(SENSITIVITY_RANKINGS),
            "exclusion_rule": "remove the largest ceil(fraction * n rank-complete projects), ties ordered by project_id",
            "fractions": [0.01, 0.05],
            "analysis_table": output_metadata["large_project_sensitivity"],
        },
        "missingness_policy": (
            "Empty or invalid source values remain missing; denominators and missing counts are emitted explicitly. "
            "Zero is never imputed for a missing value."
        ),
        "input_files": {
            name: {
                "path": path.as_posix(),
                "available": path.exists(),
                "sha256": sha256_file(path) if path.exists() else None,
            }
            for name, path in input_paths.items()
        },
        "output_tables": output_metadata,
        "third_party_code_executed": False,
    }
    write_json(output, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tables", type=Path, default=Path("data/census_v0.2/tables"))
    parser.add_argument("--output", type=Path, default=Path("data/census_v0.2/census_summary.json"))
    parser.add_argument("--summary-dir", type=Path, default=None)
    parser.add_argument(
        "--screening-ledger",
        type=Path,
        default=Path("data/census_frame_v0.2/screening_ledger.csv"),
    )
    parser.add_argument(
        "--archive-eligibility-ledger",
        type=Path,
        default=Path("data/census_frame_v0.2/archive_eligibility_ledger.csv"),
    )
    parser.add_argument(
        "--endpoint-resolution",
        type=Path,
        default=Path("data/census_v0.2/endpoint_resolution.csv"),
    )
    args = parser.parse_args()
    summary_dir = args.summary_dir or args.output.parent / "summary_tables"
    result = build_summary(
        args.tables,
        args.output,
        summary_dir,
        args.screening_ledger,
        args.archive_eligibility_ledger,
        args.endpoint_resolution,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
