#!/usr/bin/env python3
"""Create the final census data-overview figure from validated summary tables.

The figure is descriptive: it shows selection coverage, paired project-level
static metrics and family-level parsing/change coverage. No values are entered
manually; all plotted values are read from the generated census products.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np


METRIC_LABELS = {
    "source_python_line_count": "Python source lines",
    "source_python_file_count": "Python source files",
    "syntactic_api_symbol_count": "Syntactic API symbols",
    "declared_dependency_record_count": "Declared dependencies",
    "test_python_file_count": "Test Python files",
    "ci_workflow_file_count": "CI workflow files",
}
METRIC_ORDER = tuple(METRIC_LABELS)
FAMILY_LABELS = {"api": "API", "dependency": "Dependency", "structure": "Structure"}
FAMILY_ORDER = tuple(FAMILY_LABELS)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def number(value: str | int | float | None) -> float:
    if value in (None, ""):
        return float("nan")
    return float(value)


def configure() -> None:
    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"],
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "font.size": 7,
            "axes.spines.right": False,
            "axes.spines.top": False,
            "axes.linewidth": 0.8,
            "xtick.major.size": 2.5,
            "ytick.major.size": 2.5,
            "savefig.facecolor": "white",
        }
    )


def write_source_data(path: Path, rows: list[dict[str, object]]) -> None:
    fields = sorted({key for row in rows for key in row})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_figure(summary_path: Path, summary_dir: Path, output_stem: Path) -> None:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    attrition = read_csv(summary_dir / "selection_attrition.csv")
    paired = read_csv(summary_dir / "paired_change_summary.csv")
    parsing = read_csv(summary_dir / "parsing_completeness_summary.csv")
    family_metrics = read_csv(summary_dir / "project_family_delta_metrics.csv")

    # Keep the attrition chain in protocol order; not-assessed and unresolved
    # states stay visible instead of being folded into a single exclusion bar.
    attrition_order = [
        "joss_records_screened",
        "metadata_eligible",
        "publication_archive_eligible",
        "endpoint_resolved",
        "paired_dataset_extracted",
    ]
    attrition_by_stage = {row["stage"]: row for row in attrition}
    attrition_rows = [attrition_by_stage[stage] for stage in attrition_order if stage in attrition_by_stage]

    paired_by_metric = {row["metric"]: row for row in paired}
    family_complete = {
        row["family"]: row
        for row in parsing
        if row["scope"] == "paired_snapshots"
    }
    family_rows = {
        family: [row for row in family_metrics if row["family"] == family]
        for family in FAMILY_ORDER
    }
    family_changed = {
        family: {
            "projects_with_at_least_one": sum(row["observed_any_change"] == "true" for row in rows),
            "project_denominator": sum(row["paired_parse_complete"] == "true" for row in rows),
        }
        for family, rows in family_rows.items()
    }

    source_rows: list[dict[str, object]] = []
    for row in attrition_rows:
        source_rows.append(
            {
                "panel": "a_attrition",
                "stage": row["stage"],
                "stage_denominator_projects": row["stage_denominator_projects"],
                "assessed_projects": row["assessed_projects"],
                "retained_projects": row["retained_projects"],
                "excluded_projects": row["excluded_projects"],
                "unresolved_projects": row["unresolved_projects"],
                "not_assessed_projects": row["not_assessed_projects"],
                "retention_proportion": row["retention_proportion"],
            }
        )
    for metric in METRIC_ORDER:
        row = paired_by_metric.get(metric, {})
        source_rows.append(
            {
                "panel": "b_paired_metrics",
                "metric": metric,
                "publication_median": row.get("publication_paired_median", ""),
                "endpoint_median": row.get("endpoint_paired_median", ""),
                "absolute_change_median": row.get("absolute_change_median", ""),
                "bootstrap_median_95_lower": row.get("bootstrap_median_95_lower", ""),
                "bootstrap_median_95_upper": row.get("bootstrap_median_95_upper", ""),
                "complete_pair_projects": row.get("complete_pair_projects", ""),
                "bootstrap_unit": row.get("bootstrap_unit", ""),
            }
        )
    for family in FAMILY_ORDER:
        complete = family_complete.get(family, {})
        changed = family_changed.get(family, {})
        source_rows.append(
            {
                "panel": "c_family_coverage",
                "family": family,
                "complete_projects": complete.get("complete_projects", ""),
                "total_project_denominator": complete.get("total_project_denominator", ""),
                "complete_proportion": complete.get("complete_proportion", ""),
                "projects_with_any_delta_type": changed.get("projects_with_at_least_one", ""),
                "complete_family_denominator": changed.get("project_denominator", ""),
            }
        )

    configure()
    fig = plt.figure(figsize=(7.2, 4.55), constrained_layout=False)
    grid = fig.add_gridspec(2, 2, height_ratios=[1.0, 1.32], width_ratios=[1.05, 1.25], hspace=0.58, wspace=0.35)
    fig.subplots_adjust(bottom=0.16, top=0.96, left=0.10, right=0.98)
    ax_a = fig.add_subplot(grid[0, :])
    ax_b = fig.add_subplot(grid[1, 0])
    ax_c = fig.add_subplot(grid[1, 1])

    # Panel a: a compact attrition staircase.
    labels = [
        "JOSS frame",
        "Metadata\neligible",
        "Archive\neligible",
        "Endpoint\nresolved",
        "Paired\nextracted",
    ]
    retained = [int(float(row["retained_projects"])) for row in attrition_rows]
    bars = ax_a.bar(np.arange(len(retained)), retained, color="#2f627d", width=0.66, edgecolor="white", linewidth=0.8)
    ax_a.plot(np.arange(len(retained)), retained, color="#183b4d", linewidth=1.15, marker="o", markersize=3.5, zorder=3)
    for bar, value in zip(bars, retained):
        ax_a.text(bar.get_x() + bar.get_width() / 2, value + max(retained) * 0.035, f"{value:,}", ha="center", va="bottom", fontsize=7, color="#183b4d")
    ax_a.set_xticks(np.arange(len(labels)), labels)
    ax_a.set_ylabel("Projects retained")
    ax_a.set_title("a", loc="left", fontweight="bold", fontsize=9, pad=4)
    ax_a.set_ylim(0, max(retained) * 1.22 if retained else 1)
    ax_a.grid(axis="y", color="#d8dee2", linewidth=0.55, alpha=0.8)
    ax_a.set_axisbelow(True)
    ax_a.text(0.995, 0.98, f"Frame and selection ledger; n={summary.get('projects', '')} extracted pairs", transform=ax_a.transAxes, ha="right", va="top", fontsize=6.5, color="#52636b")

    # Panel b: paired medians with project-level descriptive percentile intervals.
    y = np.arange(len(METRIC_ORDER))
    pub = np.array([number(paired_by_metric.get(metric, {}).get("publication_paired_median")) for metric in METRIC_ORDER])
    end = np.array([number(paired_by_metric.get(metric, {}).get("endpoint_paired_median")) for metric in METRIC_ORDER])
    lower = np.array([number(paired_by_metric.get(metric, {}).get("bootstrap_median_95_lower")) for metric in METRIC_ORDER])
    upper = np.array([number(paired_by_metric.get(metric, {}).get("bootstrap_median_95_upper")) for metric in METRIC_ORDER])
    change = np.array([number(paired_by_metric.get(metric, {}).get("absolute_change_median")) for metric in METRIC_ORDER])
    x = np.arange(len(METRIC_ORDER))
    ax_b.axvline(0, color="#aeb9be", linewidth=0.8, zorder=0)
    ax_b.errorbar(change, x, xerr=np.vstack([change - lower, upper - change]), fmt="o", color="#b65d3b", ecolor="#d88f72", elinewidth=2.0, capsize=2.5, markersize=4.5, zorder=3)
    ax_b.set_yticks(x, [METRIC_LABELS[m] for m in METRIC_ORDER])
    ax_b.set_xlabel("Endpoint minus publication median")
    ax_b.set_title("b", loc="left", fontweight="bold", fontsize=9, pad=4)
    ax_b.grid(axis="x", color="#d8dee2", linewidth=0.55, alpha=0.8)
    ax_b.set_axisbelow(True)

    # Panel c: complete comparison and any observed delta by family.
    families_x = np.arange(len(FAMILY_ORDER))
    complete_props = np.array([number(family_complete.get(f, {}).get("complete_proportion")) for f in FAMILY_ORDER])
    changed_props = np.array([
        number(family_changed.get(f, {}).get("projects_with_at_least_one"))
        / number(family_changed.get(f, {}).get("project_denominator"))
        if number(family_changed.get(f, {}).get("project_denominator")) > 0 else np.nan
        for f in FAMILY_ORDER
    ])
    width = 0.35
    ax_c.bar(families_x - width / 2, complete_props, width, color="#6e8f7b", label="Complete comparison")
    ax_c.bar(families_x + width / 2, changed_props, width, color="#b65d3b", label="Any added/removed/changed")
    ax_c.set_xticks(families_x, [FAMILY_LABELS[f] for f in FAMILY_ORDER])
    ax_c.set_ylim(0, 1.08)
    ax_c.set_ylabel("Project proportion")
    ax_c.set_title("c", loc="left", fontweight="bold", fontsize=9, pad=4)
    ax_c.yaxis.set_major_formatter(mpl.ticker.PercentFormatter(1.0, decimals=0))
    ax_c.grid(axis="y", color="#d8dee2", linewidth=0.55, alpha=0.8)
    ax_c.set_axisbelow(True)
    ax_c.legend(loc="upper center", bbox_to_anchor=(0.55, 0.98), ncol=2, fontsize=6, handlelength=1.2, columnspacing=0.8)

    fig.text(0.01, 0.005, "Static, descriptive summaries only; missing and partial states are retained and no runtime execution is performed.", ha="left", va="bottom", fontsize=6, color="#52636b")
    output_stem.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(f"{output_stem}.svg", bbox_inches="tight")
    fig.savefig(f"{output_stem}.pdf", bbox_inches="tight")
    fig.savefig(f"{output_stem}.png", dpi=600, bbox_inches="tight")
    fig.savefig(f"{output_stem}.tiff", dpi=600, bbox_inches="tight")
    write_source_data(output_stem.with_name(output_stem.name + "_source_data.csv"), source_rows)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", type=Path, default=Path("data/census_v0.2/census_summary.json"))
    parser.add_argument("--summary-dir", type=Path, default=Path("data/census_v0.2/summary_tables"))
    parser.add_argument("--output", type=Path, default=Path("figures/census_v0.2/census_overview"))
    args = parser.parse_args()
    build_figure(args.summary, args.summary_dir, args.output)
    print(json.dumps({"output_stem": args.output.as_posix(), "projects": json.loads(args.summary.read_text(encoding="utf-8")).get("projects")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
