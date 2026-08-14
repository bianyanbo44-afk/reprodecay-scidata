#!/usr/bin/env python3
"""Create the Python-only v0.2 pilot Data Overview figure."""

from __future__ import annotations

import argparse
import csv
import math
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["Arial", "DejaVu Sans", "Liberation Sans"]
plt.rcParams["svg.fonttype"] = "none"
plt.rcParams["pdf.fonttype"] = 42
plt.rcParams["font.size"] = 7
plt.rcParams["axes.linewidth"] = 0.8
plt.rcParams["axes.spines.right"] = False
plt.rcParams["axes.spines.top"] = False
plt.rcParams["legend.frameon"] = False


COLORS = {
    "navy": "#484878",
    "blue": "#7884B4",
    "pale_blue": "#DCE3F4",
    "rose": "#D9A6B8",
    "pale_rose": "#F1E0E7",
    "teal": "#42949E",
    "pale_teal": "#D9EEEE",
    "green": "#4C8B57",
    "pale_green": "#DDF3DE",
    "amber": "#C58A2C",
    "pale_amber": "#F5E6C8",
    "red": "#B64342",
    "grey": "#A8A8A8",
    "light_grey": "#E8E8E8",
    "dark": "#3E3E3E",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(path.open(encoding="utf-8-sig", newline="")))


def project_label(row: dict[str, str]) -> str:
    return row["resolved_repository_url"].rstrip("/").rsplit("/", 1)[-1]


def panel_label(ax: plt.Axes, label: str, x: float = -0.03, y: float = 1.03) -> None:
    ax.text(x, y, label, transform=ax.transAxes, fontsize=8, fontweight="bold", va="bottom")


def workflow_panel(ax: plt.Axes, n_pairs: int) -> None:
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    panel_label(ax, "a", x=-0.01, y=1.01)
    stages = [
        (0.02, 0.59, 0.14, 0.25, COLORS["pale_blue"], "JOSS record", "paper DOI"),
        (0.21, 0.59, 0.17, 0.25, COLORS["pale_rose"], "Publication\nsnapshot", "Zenodo DOI + SHA-256"),
        (0.43, 0.59, 0.17, 0.25, COLORS["pale_teal"], "Endpoint\nsnapshot", "release/commit + SHA-256"),
        (0.65, 0.59, 0.14, 0.25, COLORS["pale_green"], "Static parsers", "AST + metadata"),
        (0.84, 0.59, 0.14, 0.25, COLORS["pale_amber"], "Version-pair\nrecords", "delta + validation"),
    ]
    for index, (x, y, width, height, color, title, subtitle) in enumerate(stages):
        box = FancyBboxPatch(
            (x, y), width, height,
            boxstyle="round,pad=0.012,rounding_size=0.018",
            linewidth=0.8, edgecolor=COLORS["dark"], facecolor=color,
        )
        ax.add_patch(box)
        ax.text(x + width / 2, y + height * 0.62, title, ha="center", va="center", fontsize=7, fontweight="bold")
        ax.text(x + width / 2, y + height * 0.22, subtitle, ha="center", va="center", fontsize=5.6, color=COLORS["dark"])
        if index < len(stages) - 1:
            next_x = stages[index + 1][0]
            arrow = FancyArrowPatch(
                (x + width + 0.006, y + height / 2), (next_x - 0.006, y + height / 2),
                arrowstyle="-|>", mutation_scale=8, linewidth=0.8, color=COLORS["grey"],
            )
            ax.add_patch(arrow)
    ax.text(
        0.5, 0.37,
        "Candidate software is never installed, imported or executed",
        ha="center", va="center", fontsize=6.5, color=COLORS["dark"],
        bbox={"boxstyle": "round,pad=0.28", "facecolor": "white", "edgecolor": COLORS["grey"], "linewidth": 0.7},
    )
    ax.text(
        0.5, 0.15,
        f"Pilot evidence chain: {n_pairs} projects  |  {2 * n_pairs} immutable snapshots  |  hashes and parser status retained",
        ha="center", va="center", fontsize=6.5, fontweight="bold", color=COLORS["navy"],
    )


def records_panel(ax: plt.Axes, projects: list[dict[str, str]], deltas: list[dict[str, str]]) -> None:
    project_order = sorted(projects, key=lambda row: int(row["selection_rank"]))
    short_labels = [project_label(row) for row in project_order]
    project_ids = [row["project_id"] for row in project_order]
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in deltas:
        counts[row["project_id"]][row["family"]] += 1
    matrix = np.array(
        [[counts[pid][family] for family in ("api", "dependency", "structure")] for pid in project_ids],
        dtype=float,
    )
    cmap = mpl.colors.LinearSegmentedColormap.from_list("record_counts", ["#F3F3F8", COLORS["blue"], COLORS["navy"]])
    norm = mpl.colors.LogNorm(vmin=max(1, matrix.min()), vmax=matrix.max())
    image = ax.imshow(matrix, aspect="auto", interpolation="nearest", cmap=cmap, norm=norm)
    ax.set_yticks(range(len(project_ids)))
    ax.set_yticklabels(short_labels, fontsize=5.5)
    ax.set_xticks(range(3))
    ax.set_xticklabels(["Syntactic\nsymbol", "Declared\ndependency", "Structure"], fontsize=5.8)
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            value = int(matrix[i, j])
            color = "white" if norm(matrix[i, j]) > 0.58 else COLORS["dark"]
            ax.text(j, i, f"{value:,}", ha="center", va="center", fontsize=4.8, color=color)
    ax.set_title("Released delta records by pilot project", loc="left", fontsize=7.3, fontweight="bold", pad=5)
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    colorbar = ax.figure.colorbar(image, ax=ax, orientation="horizontal", fraction=0.045, pad=0.11, aspect=30)
    colorbar.set_label("Record count (log colour scale; cells show exact counts)", fontsize=5.3)
    colorbar.ax.tick_params(labelsize=4.8, length=2)
    panel_label(ax, "b", x=-0.17, y=1.03)


def quality_panel(ax: plt.Axes, projects: list[dict[str, str]], structures: list[dict[str, str]]) -> None:
    project_order = sorted(projects, key=lambda row: int(row["selection_rank"]))
    project_ids = [row["project_id"] for row in project_order]
    short_labels = [project_label(row) for row in project_order]
    by_project: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in structures:
        by_project[row["project_id"]].append(row)
    matrix = np.zeros((len(project_ids), 4), dtype=int)
    for i, pid in enumerate(project_ids):
        pair = by_project[pid]
        matrix[i, 0] = int(all(row["dependency_parse_complete"] == "true" for row in pair))
        matrix[i, 1] = int(all(row["api_parse_complete"] == "true" for row in pair))
        matrix[i, 2] = int(all(int(row["suspicious_member_count"]) == 0 for row in pair))
        matrix[i, 3] = int(all(len(row["archive_sha256"]) == 64 for row in pair))
    cmap = mpl.colors.ListedColormap([COLORS["pale_amber"], COLORS["pale_green"]])
    ax.imshow(matrix, aspect="auto", interpolation="nearest", cmap=cmap, vmin=0, vmax=1)
    ax.set_xticks(range(4))
    ax.set_xticklabels(["Dependency\nparse", "API\nparse", "Safe archive\npaths", "SHA-256\nverified"], fontsize=5.7)
    ax.set_yticks(range(len(project_ids)))
    ax.set_yticklabels(short_labels, fontsize=5.5)
    ax.tick_params(length=0)
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            ax.text(j, i, "pass" if matrix[i, j] else "partial", ha="center", va="center", fontsize=4.8, color=COLORS["dark"])
    ax.set_title("Project-level extraction and validation status", loc="left", fontsize=7.3, fontweight="bold", pad=5)
    for spine in ax.spines.values():
        spine.set_visible(False)
    panel_label(ax, "c", x=-0.17, y=1.03)
    ax.text(
        0.0, -0.115,
        "Partial: non-literal dependency expressions are retained, not imputed.",
        transform=ax.transAxes, fontsize=5.0, color=COLORS["dark"], va="top",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tables", default="data/pilot_v0.2/tables")
    parser.add_argument("--output-dir", default="figures/pilot_v0.2")
    args = parser.parse_args()
    tables = Path(args.tables)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)

    projects = read_csv(tables / "project_records.csv")
    structures = read_csv(tables / "structure_records.csv")
    deltas = read_csv(tables / "pair_delta_records.csv")
    if not projects or len(structures) != 2 * len(projects):
        raise ValueError("project/snapshot structure counts are inconsistent")

    delta_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in deltas:
        delta_counts[row["project_id"]][row["family"]] += 1
    structures_by_project: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in structures:
        structures_by_project[row["project_id"]].append(row)
    source_rows: list[dict[str, str]] = []
    for project in sorted(projects, key=lambda row: int(row["selection_rank"])):
        pid = project["project_id"]
        pair = structures_by_project[pid]
        source_rows.append(
            {
                "project_id": pid,
                "project_label": project_label(project),
                "selection_rank": project["selection_rank"],
                "api_delta_records": str(delta_counts[pid]["api"]),
                "dependency_delta_records": str(delta_counts[pid]["dependency"]),
                "structure_delta_records": str(delta_counts[pid]["structure"]),
                "dependency_parse_status": "pass" if all(row["dependency_parse_complete"] == "true" for row in pair) else "partial",
                "api_parse_status": "pass" if all(row["api_parse_complete"] == "true" for row in pair) else "partial",
                "safe_archive_paths_status": "pass" if all(int(row["suspicious_member_count"]) == 0 for row in pair) else "fail",
                "sha256_status": "pass" if all(len(row["archive_sha256"]) == 64 for row in pair) else "fail",
            }
        )
    source_path = output / "pilot_data_overview_source_data.csv"
    with source_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(source_rows[0]))
        writer.writeheader()
        writer.writerows(source_rows)

    width_in = 183 / 25.4
    height_in = 118 / 25.4
    fig = plt.figure(figsize=(width_in, height_in), facecolor="white")
    grid = fig.add_gridspec(2, 2, height_ratios=[0.72, 1.28], width_ratios=[1, 1], hspace=0.28, wspace=0.42)
    workflow_panel(fig.add_subplot(grid[0, :]), len(projects))
    records_panel(fig.add_subplot(grid[1, 0]), projects, deltas)
    quality_panel(fig.add_subplot(grid[1, 1]), projects, structures)
    fig.subplots_adjust(left=0.14, right=0.985, top=0.975, bottom=0.11)

    base = output / "pilot_data_overview"
    fig.savefig(base.with_suffix(".svg"))
    fig.savefig(base.with_suffix(".pdf"))
    fig.savefig(base.with_suffix(".png"), dpi=300)
    fig.savefig(base.with_suffix(".tiff"), dpi=600)
    plt.close(fig)
    print(str(base))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
