#!/usr/bin/env python3
"""Generate the four manuscript figures from validated census tables.

All plotted values are read from the released census products or the
independent semantic-audit summary.  The script emits editable SVG/PDF,
600-dpi PNG/TIFF previews and one source-data CSV per figure.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import Counter
from pathlib import Path

import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle
import numpy as np

# Required before any figure is created: editable text in SVG/PDF.
mpl.rcParams["font.family"] = "sans-serif"
mpl.rcParams["font.sans-serif"] = ["Arial", "DejaVu Sans", "Liberation Sans"]
mpl.rcParams["svg.fonttype"] = "none"
mpl.rcParams["pdf.fonttype"] = 42
mpl.rcParams.update({
    "font.size": 7.4,
    "axes.spines.right": False,
    "axes.spines.top": False,
    "axes.linewidth": 0.8,
    "legend.frameon": False,
    "xtick.major.size": 2.5,
    "ytick.major.size": 2.5,
    "savefig.facecolor": "white",
})

COLORS = {
    "ink": "#263238",
    "muted": "#65727a",
    "blue": "#276FBF",
    "blue_soft": "#BFD7EA",
    "teal": "#2A9D8F",
    "teal_soft": "#BFE3DD",
    "ochre": "#D28B32",
    "ochre_soft": "#F1D5A8",
    "red": "#B94E48",
    "red_soft": "#E8B7B3",
    "green": "#5D8A66",
    "grey": "#D9DEE1",
    "grey_dark": "#8B969D",
}
FAMILY_ORDER = ["api", "dependency", "structure"]
FAMILY_LABELS = {"api": "API", "dependency": "Dependency", "structure": "Structure"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def num(value: str | int | float | None, default: float = np.nan) -> float:
    if value in (None, ""):
        return default
    return float(value)


def dump_source(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({field for row in rows for field in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def save(fig: plt.Figure, stem: Path, source_rows: list[dict[str, object]]) -> None:
    stem.parent.mkdir(parents=True, exist_ok=True)
    svg_path = stem.with_suffix(".svg")
    fig.savefig(svg_path, bbox_inches="tight")
    svg_lines = svg_path.read_text(encoding="utf-8").splitlines()
    svg_path.write_text(
        "\n".join(line.rstrip() for line in svg_lines) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    fig.savefig(f"{stem}.pdf", bbox_inches="tight")
    fig.savefig(f"{stem}.png", dpi=600, bbox_inches="tight")
    fig.savefig(f"{stem}.tiff", dpi=600, bbox_inches="tight")
    dump_source(stem.with_name(stem.name + "_source_data.csv"), source_rows)
    plt.close(fig)


def panel_label(ax: plt.Axes, label: str) -> None:
    ax.text(-0.08, 1.04, label, transform=ax.transAxes, ha="left", va="bottom", fontweight="bold", fontsize=9, color=COLORS["ink"])


def make_fig1(attrition: list[dict[str, str]], manifest: dict[str, object], endpoint_summary: list[dict[str, str]], out: Path) -> None:
    stages = [
        ("JOSS frame", "940", COLORS["blue"]),
        ("metadata\neligible", "396", COLORS["blue_soft"]),
        ("archive\neligible", "372", COLORS["teal_soft"]),
        ("endpoint\nresolved", "345", COLORS["ochre_soft"]),
        ("paired\nprojects", "345", COLORS["green"]),
    ]
    fig = plt.figure(figsize=(7.2, 5.1))
    grid = fig.add_gridspec(2, 2, height_ratios=[1.05, 1.0], width_ratios=[1.35, 1.0], hspace=0.58, wspace=0.38)
    ax_a = fig.add_subplot(grid[0, :])
    ax_b = fig.add_subplot(grid[1, 0])
    ax_c = fig.add_subplot(grid[1, 1])
    panel_label(ax_a, "a")
    ax_a.axis("off")
    xs = np.linspace(0.08, 0.92, len(stages))
    for i, ((label, value, color), x) in enumerate(zip(stages, xs)):
        box = FancyBboxPatch((x - 0.09, 0.35), 0.18, 0.30, boxstyle="round,pad=0.012,rounding_size=0.012", linewidth=1.0, edgecolor=COLORS["ink"], facecolor=color, transform=ax_a.transAxes)
        ax_a.add_patch(box)
        ax_a.text(x, 0.53, label, transform=ax_a.transAxes, ha="center", va="center", color=COLORS["ink"], fontsize=7.1)
        ax_a.text(x, 0.28, value, transform=ax_a.transAxes, ha="center", va="center", fontsize=10.5, fontweight="bold", color=COLORS["ink"])
        if i < len(stages) - 1:
            ax_a.add_patch(FancyArrowPatch((x + 0.095, 0.50), (xs[i + 1] - 0.095, 0.50), transform=ax_a.transAxes, arrowstyle="-|>", mutation_scale=10, linewidth=1.0, color=COLORS["muted"]))
    ax_a.text(0.08, 0.83, "Frozen frame to version-paired extraction cohort", transform=ax_a.transAxes, fontsize=8.2, fontweight="bold", color=COLORS["ink"])
    ax_a.text(0.08, 0.12, "Unresolved states remain in the ledgers: 7 metadata-stage, 4 archive-stage and 27 endpoint-stage records.", transform=ax_a.transAxes, fontsize=6.5, color=COLORS["muted"])

    panel_label(ax_b, "b")
    ax_b.axis("off")
    ax_b.text(0.02, 0.88, "Immutable pair", fontsize=8.3, fontweight="bold", color=COLORS["ink"])
    boxes = [(0.03, 0.48, 0.39, 0.25, "Publication archive", "341 Zenodo\n4 Figshare", COLORS["blue_soft"]), (0.58, 0.48, 0.39, 0.25, "Endpoint snapshot", "327 releases\n18 commit heads", COLORS["ochre_soft"])]
    for x, y, w, h, title, detail, color in boxes:
        ax_b.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.012,rounding_size=0.012", linewidth=0.9, edgecolor=COLORS["ink"], facecolor=color, transform=ax_b.transAxes))
        ax_b.text(x + w / 2, y + h * 0.66, title, transform=ax_b.transAxes, ha="center", va="center", fontsize=7.0, fontweight="bold")
        ax_b.text(x + w / 2, y + h * 0.30, detail, transform=ax_b.transAxes, ha="center", va="center", fontsize=6.7)
    ax_b.add_patch(FancyArrowPatch((0.43, 0.60), (0.57, 0.60), transform=ax_b.transAxes, arrowstyle="<->", mutation_scale=10, color=COLORS["muted"], linewidth=1.0))
    ax_b.text(0.50, 0.67, "SHA-256\n+ evidence", transform=ax_b.transAxes, ha="center", va="center", fontsize=6.2, color=COLORS["muted"])
    ax_b.text(0.50, 0.26, "2 snapshots per resolved project", transform=ax_b.transAxes, ha="center", va="center", fontsize=6.4, color=COLORS["muted"])

    panel_label(ax_c, "c")
    ax_c.axis("off")
    ax_c.text(0.02, 0.88, "Reusable record families", fontsize=8.3, fontweight="bold", color=COLORS["ink"])
    items = [("7 core CSV tables", "project, snapshot, structure, dependency, API, delta, validation", COLORS["blue"]), ("Evidence links", "relative source members and row-level comparison status", COLORS["teal"]), ("Static boundary", "no install, import or execution of candidate code", COLORS["red"])]
    for i, (title, detail, color) in enumerate(items):
        y = 0.68 - i * 0.22
        ax_c.add_patch(Rectangle((0.03, y - 0.045), 0.035, 0.09, transform=ax_c.transAxes, facecolor=color, edgecolor="none"))
        ax_c.text(0.09, y + 0.017, title, transform=ax_c.transAxes, fontsize=7.0, fontweight="bold", va="center")
        ax_c.text(0.09, y - 0.035, detail, transform=ax_c.transAxes, fontsize=6.1, color=COLORS["muted"], va="center")
    fig.text(0.01, 0.005, "Scope is a bounded JOSS/Python census; selection and parser missingness are data, not quality or prevalence estimates.", fontsize=6.2, color=COLORS["muted"])
    source = [{"panel": "a_attrition", **row} for row in attrition]
    source += [{"panel": "b_pair", "publication_archives": 345, "zenodo_archives": 341, "figshare_archives": 4, "endpoint_releases": 327, "endpoint_default_branch": 18, "resolved_projects": 345, "snapshots": 690}]
    source += [{"panel": "c_manifest", "field": key, "value": value} for key, value in manifest.items() if key in {"projects", "snapshots", "dependency_records", "api_symbol_records", "pair_delta_records", "validation_records"}]
    save(fig, out, source)


def make_fig2(family_rows: list[dict[str, str]], out: Path) -> None:
    values: dict[tuple[str, str], float] = {}
    status: dict[tuple[str, str], str] = {}
    for row in family_rows:
        key = (row["project_id"], row["family"])
        status[key] = row["paired_parse_complete"]
        if row["paired_parse_complete"] == "true" and num(row["interpretable_delta_denominator"], 0) > 0:
            values[key] = (num(row["added_count"], 0) + num(row["removed_count"], 0) + num(row["changed_count"], 0)) / num(row["interpretable_delta_denominator"], 1)
        else:
            values[key] = np.nan
    project_ids = sorted({row["project_id"] for row in family_rows})
    project_ids.sort(key=lambda pid: (-np.nansum([values.get((pid, family), np.nan) for family in FAMILY_ORDER]), pid))
    matrix = np.array([[values.get((pid, family), np.nan) for family in FAMILY_ORDER] for pid in project_ids])
    fig = plt.figure(figsize=(7.2, 5.0))
    grid = fig.add_gridspec(1, 2, width_ratios=[1.18, 0.82], wspace=0.42)
    ax_a = fig.add_subplot(grid[0, 0])
    ax_b = fig.add_subplot(grid[0, 1])
    panel_label(ax_a, "a")
    cmap = mpl.colormaps["YlOrRd"].copy()
    cmap.set_bad("white")
    im = ax_a.imshow(matrix, aspect="auto", interpolation="nearest", cmap=cmap, vmin=0, vmax=1)
    ax_a.set_xticks(np.arange(3), [FAMILY_LABELS[f] for f in FAMILY_ORDER])
    ax_a.set_yticks([])
    ax_a.set_xlabel("Family")
    ax_a.set_ylabel("Projects sorted by combined churn")
    ax_a.set_title("Project-level change fraction", loc="left", fontsize=8.2, fontweight="bold", pad=18)
    cbar = fig.colorbar(im, ax=ax_a, fraction=0.046, pad=0.03)
    cbar.set_label("(added + removed + changed) / comparable rows", fontsize=6.7)
    cbar.ax.tick_params(labelsize=6)
    changed_labels = []
    for family in FAMILY_ORDER:
        n_complete = sum(row["family"] == family and row["paired_parse_complete"] == "true" for row in family_rows)
        n_changed = sum(row["family"] == family and row["paired_parse_complete"] == "true" and row["observed_any_change"] == "true" for row in family_rows)
        changed_labels.append(f"{FAMILY_LABELS[family]} {n_changed}/{n_complete}")
    ax_a.text(0.5, 1.015, "Changed projects: " + " | ".join(changed_labels), transform=ax_a.transAxes, ha="center", va="bottom", fontsize=6.1, color=COLORS["muted"])
    panel_label(ax_b, "b")
    distributions = [[values[(pid, family)] for pid in project_ids if not np.isnan(values.get((pid, family), np.nan))] for family in FAMILY_ORDER]
    box = ax_b.boxplot(distributions, positions=np.arange(3), widths=0.48, patch_artist=True, showfliers=False, medianprops={"color": COLORS["ink"], "linewidth": 1.2}, whiskerprops={"color": COLORS["muted"]}, capprops={"color": COLORS["muted"]})
    for patch, color in zip(box["boxes"], [COLORS["blue_soft"], COLORS["teal_soft"], COLORS["ochre_soft"]]):
        patch.set_facecolor(color)
        patch.set_edgecolor(COLORS["ink"])
    ax_b.set_xticks(np.arange(3), [FAMILY_LABELS[f] for f in FAMILY_ORDER])
    ax_b.set_ylabel("Project-level change fraction")
    ax_b.set_ylim(-0.04, 1.04)
    ax_b.yaxis.set_major_formatter(mpl.ticker.PercentFormatter(1.0, decimals=0))
    ax_b.set_title("Distribution among complete comparisons", loc="left", fontsize=8.2, fontweight="bold", pad=7)
    ax_b.text(0.02, 0.98, "Complete pairs only; partial comparisons\nare omitted from the box summaries.", transform=ax_b.transAxes, fontsize=6.3, color=COLORS["muted"], va="top")
    fig.text(0.01, 0.005, "The landscape exposes churn that project-level net medians can conceal; fractions are descriptive, not compatibility or quality scores.", fontsize=6.2, color=COLORS["muted"])
    source = []
    for i, pid in enumerate(project_ids):
        for family in FAMILY_ORDER:
            row = next(r for r in family_rows if r["project_id"] == pid and r["family"] == family)
            source.append({"panel": "a_heatmap", "project_id": pid, "family": family, "sort_rank": i + 1, "paired_parse_complete": row["paired_parse_complete"], "change_fraction": values[(pid, family)], "added_count": row["added_count"], "removed_count": row["removed_count"], "changed_count": row["changed_count"], "interpretable_delta_denominator": row["interpretable_delta_denominator"]})
    save(fig, out, source)


def make_fig3(structures: list[dict[str, str]], out: Path) -> None:
    grouped: dict[str, dict[str, dict[str, bool]]] = {}
    specs = {
        "pyproject.toml": lambda row: "pyproject.toml" in json.loads(row["packaging_files"] or "[]"),
        "GitHub Actions": lambda row: "github_actions" in json.loads(row["ci_providers"] or "[]"),
        "Travis CI": lambda row: "travis" in json.loads(row["ci_providers"] or "[]"),
        "CITATION.cff": lambda row: row["citation_cff"] == "true",
    }
    for row in structures:
        grouped.setdefault(row["project_id"], {})[row["snapshot_role"]] = row
    transitions: list[dict[str, object]] = []
    counts: dict[str, Counter[str]] = {field: Counter() for field in specs}
    for pid, roles in grouped.items():
        for field, fn in specs.items():
            pub, end = fn(roles["publication"]), fn(roles["endpoint"])
            transition = "present_to_present" if pub and end else "absent_to_absent" if not pub and not end else "absent_to_present" if end else "present_to_absent"
            counts[field][transition] += 1
            transitions.append({"project_id": pid, "field": field, "publication_present": str(pub).lower(), "endpoint_present": str(end).lower(), "transition": transition})
    fig, axes = plt.subplots(2, 2, figsize=(7.2, 4.9), sharey=True)
    for ax, field in zip(axes.flat, specs):
        panel_label(ax, chr(ord("a") + list(specs).index(field)))
        c = counts[field]
        pub = (c["present_to_present"] + c["present_to_absent"]) / len(grouped)
        end = (c["present_to_present"] + c["absent_to_present"]) / len(grouped)
        ax.plot([0, 1], [pub, end], color=COLORS["muted"], linewidth=0.9, zorder=1)
        ax.scatter([0], [pub], s=30, color=COLORS["blue"], zorder=3)
        ax.scatter([1], [end], s=30, color=COLORS["teal"], zorder=3)
        ax.set_xticks([0, 1], ["Publication", "Endpoint"])
        ax.set_ylim(0, 1.08)
        ax.yaxis.set_major_formatter(mpl.ticker.PercentFormatter(1.0, decimals=0))
        ax.set_title(field, loc="left", fontsize=8.0, fontweight="bold", pad=6)
        ax.grid(axis="y", color=COLORS["grey"], linewidth=0.5)
        ax.set_axisbelow(True)
        direction = c["absent_to_present"] - c["present_to_absent"]
        sign = "+" if direction >= 0 else ""
        ax.text(0.98, 0.08, f"net {sign}{direction} projects", transform=ax.transAxes, ha="right", va="bottom", fontsize=6.4, color=COLORS["muted"])
        ax.text(0.03, 0.92, f"+{c['absent_to_present']} added\n-{c['present_to_absent']} removed", transform=ax.transAxes, fontsize=6.3, va="top", color=COLORS["ink"])
    axes[0, 0].set_ylabel("Project proportion")
    axes[1, 0].set_ylabel("Project proportion")
    fig.text(0.01, 0.005, "Transitions are binary presence observations; they do not assess configuration quality, workflow success or citation completeness.", fontsize=6.2, color=COLORS["muted"])
    save(fig, out, transitions)


def make_fig4(parsing: list[dict[str, str]], validation: list[dict[str, str]], audit: dict[str, object], sample: list[dict[str, str]], out: Path) -> None:
    fig = plt.figure(figsize=(7.2, 5.15))
    grid = fig.add_gridspec(2, 2, hspace=0.68, wspace=0.42)
    ax_a = fig.add_subplot(grid[0, 0])
    ax_b = fig.add_subplot(grid[0, 1])
    ax_c = fig.add_subplot(grid[1, 0])
    ax_d = fig.add_subplot(grid[1, 1])
    panel_label(ax_a, "a")
    completeness = {row["family"]: row for row in parsing if row["scope"] == "paired_snapshots"}
    x = np.arange(3)
    pub = [num(next(r for r in parsing if r["family"] == f and r["scope"] == "publication_snapshot")["complete_proportion"], 0) for f in FAMILY_ORDER]
    end = [num(next(r for r in parsing if r["family"] == f and r["scope"] == "endpoint_snapshot")["complete_proportion"], 0) for f in FAMILY_ORDER]
    pair = [num(completeness[f]["complete_proportion"], 0) for f in FAMILY_ORDER]
    width = 0.24
    ax_a.bar(x - width, pub, width, label="Publication", color=COLORS["blue_soft"])
    ax_a.bar(x, end, width, label="Endpoint", color=COLORS["ochre_soft"])
    ax_a.bar(x + width, pair, width, label="Paired", color=COLORS["teal"])
    ax_a.set_xticks(x, [FAMILY_LABELS[f] for f in FAMILY_ORDER])
    ax_a.set_ylim(0, 1.08)
    ax_a.yaxis.set_major_formatter(mpl.ticker.PercentFormatter(1.0, decimals=0))
    ax_a.set_ylabel("Complete parsing")
    ax_a.set_title("Parsing completeness", loc="left", fontsize=8.2, fontweight="bold", pad=7)
    ax_a.legend(fontsize=6, loc="lower left", ncol=1)
    ax_a.grid(axis="y", color=COLORS["grey"], linewidth=0.5)
    ax_a.set_axisbelow(True)

    panel_label(ax_b, "b")
    structure_expected = sum(v["expected"] for v in audit["structure"].values())
    structure_agreement = sum(v["agreement"] for v in audit["structure"].values())
    checks = [
        (
            "API symbol keys",
            audit["api"]["symbol_key"]["agreement_rate"],
            audit["api"]["symbol_key"]["agreement"],
            audit["api"]["symbol_key"]["expected"],
        ),
        (
            "Dependency records",
            audit["dependency"]["exact_record"]["agreement_rate"],
            audit["dependency"]["exact_record"]["agreement"],
            audit["dependency"]["exact_record"]["expected"],
        ),
        (
            "Structure values",
            structure_agreement / structure_expected,
            structure_agreement,
            structure_expected,
        ),
    ]
    y = np.arange(len(checks))
    ax_b.barh(y, [v for _, v, _, _ in checks], color=[COLORS["blue"], COLORS["teal"], COLORS["ochre"]], height=0.48)
    ax_b.set_yticks(y, [name for name, _, _, _ in checks])
    ax_b.set_xlim(0, 1.08)
    ax_b.xaxis.set_major_formatter(mpl.ticker.PercentFormatter(1.0, decimals=0))
    ax_b.set_xlabel("Exact agreement in 24-project audit")
    ax_b.set_title("Independent semantic audit", loc="left", fontsize=8.2, fontweight="bold", pad=7)
    for yi, (_, value, agreement, expected) in zip(y, checks):
        ax_b.text(value + 0.015, yi, f"{agreement:,}/{expected:,}", va="center", fontsize=6.5)
    ax_b.grid(axis="x", color=COLORS["grey"], linewidth=0.5)
    ax_b.set_axisbelow(True)

    panel_label(ax_c, "c")
    counts = Counter((row["check_name"], row["status"]) for row in validation)
    labels = ["AST warnings", "Metadata warnings", "AST parse errors", "API source errors"]
    keys = [("python_ast_warning", "warning"), ("metadata_parse", "warning"), ("python_ast_parse", "error"), ("api_source_read", "error")]
    vals = [counts[key] for key in keys]
    bars = ax_c.barh(np.arange(len(labels)), vals, color=[COLORS["blue_soft"], COLORS["teal_soft"], COLORS["red_soft"], COLORS["red"]], height=0.5)
    ax_c.set_yticks(np.arange(len(labels)), labels)
    ax_c.set_xscale("symlog", linthresh=10)
    ax_c.set_xlabel("Validation events (log-like scale)")
    ax_c.set_title("Retained parser events", loc="left", fontsize=8.2, fontweight="bold", pad=7)
    for bar, value in zip(bars, vals):
        ax_c.text(value * 1.08 if value else 1, bar.get_y() + bar.get_height() / 2, f"{value:,}", va="center", fontsize=6.5)
    ax_c.grid(axis="x", color=COLORS["grey"], linewidth=0.5)
    ax_c.set_axisbelow(True)

    panel_label(ax_d, "d")
    ax_d.axis("off")
    ax_d.text(0.02, 0.90, "Reconstruction checks", fontsize=8.2, fontweight="bold", color=COLORS["ink"])
    cards = [
        ("Byte-identical rerun", "canonical tables", COLORS["blue"]),
        ("Archive hashes", "690 / 690 pass", COLORS["teal"]),
        ("Independent archive audit", "48 / 48 located", COLORS["ochre"]),
        ("Evidence paths", "38 / 38 reconstructed", COLORS["green"]),
    ]
    for i, (title, detail, color) in enumerate(cards):
        y = 0.72 - i * 0.17
        ax_d.add_patch(Rectangle((0.03, y - 0.035), 0.035, 0.07, transform=ax_d.transAxes, facecolor=color, edgecolor="none"))
        ax_d.text(0.09, y + 0.01, title, transform=ax_d.transAxes, fontsize=6.9, fontweight="bold", va="center")
        ax_d.text(0.09, y - 0.034, detail, transform=ax_d.transAxes, fontsize=6.2, color=COLORS["muted"], va="center")
    ax_d.text(0.03, 0.05, "The independent audit is a stratified exact-agreement check, not a population accuracy estimate.", transform=ax_d.transAxes, fontsize=6.1, color=COLORS["muted"], va="bottom")
    fig.text(0.01, 0.005, "Completeness, parser events and reconstruction are reported as data-quality evidence; none implies installability or runtime behavior.", fontsize=6.2, color=COLORS["muted"])
    source = [{"panel": "a_completeness", **row} for row in parsing] + [{"panel": "c_validation", **row} for row in validation]
    source += [
        {
            "panel": "b_audit",
            "check": name,
            "agreement_rate": value,
            "agreement_count": agreement,
            "expected_count": expected,
        }
        for name, value, agreement, expected in checks
    ]
    source += [{"panel": "d_sample", **row} for row in sample]
    save(fig, out, source)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    data = root / "data/census_v0.2"
    summary_dir = data / "summary_tables"
    out = root / "figures/census_v0.2"
    final = root / "paper_rewriting_output/final_paper/figures"
    manifest = json.loads((data / "tables/extraction_manifest.json").read_text(encoding="utf-8"))
    attrition = read_csv(summary_dir / "selection_attrition.csv")
    family = read_csv(summary_dir / "project_family_delta_metrics.csv")
    parsing = read_csv(summary_dir / "parsing_completeness_summary.csv")
    structures = read_csv(data / "tables/structure_records.csv")
    validation = read_csv(data / "tables/validation_records.csv")
    endpoint_summary = read_csv(summary_dir / "endpoint_type_summary.csv")
    audit = json.loads((data / "independent_semantic_audit.json").read_text(encoding="utf-8"))
    sample = read_csv(data / "independent_semantic_audit_sample.csv")
    make_fig1(attrition, manifest, endpoint_summary, out / "fig1_pairing_workflow")
    make_fig2(family, out / "fig2_change_landscape")
    make_fig3(structures, out / "fig3_metadata_transitions")
    make_fig4(parsing, validation, audit, sample, out / "fig4_validation_audit")
    final.mkdir(parents=True, exist_ok=True)
    for stem in ("fig1_pairing_workflow", "fig2_change_landscape", "fig3_metadata_transitions", "fig4_validation_audit"):
        for suffix in (".svg", ".pdf", ".png", ".tiff", "_source_data.csv"):
            src = out / f"{stem}{suffix}"
            if src.exists():
                shutil.copy2(src, final / src.name)
    print(json.dumps({"output_dir": str(out), "figures": 4, "backend": "python/matplotlib"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
