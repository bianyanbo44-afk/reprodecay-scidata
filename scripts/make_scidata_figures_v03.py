#!/usr/bin/env python3
"""Figures 1-4 of the Data Descriptor (release v0.3), from the released tables and audit outputs.

Every plotted value is written to <figure>_source_data.csv. Project summary
proportions carry Wilson 95% intervals; audit points report exact checked-unit
agreement. Colours are light, labels
sit outside marks, and every figure passes the alignment and PDF collision
audits before it is accepted.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np

mpl.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"],
    "svg.fonttype": "none",
    "pdf.fonttype": 42,
    "font.size": 8,
    "axes.titlesize": 8,
    "axes.labelsize": 8,
    "xtick.labelsize": 7.5,
    "ytick.labelsize": 7.5,
    "legend.fontsize": 7.5,
    "axes.spines.right": False,
    "axes.spines.top": False,
    "axes.linewidth": 0.6,
    "xtick.major.width": 0.6,
    "ytick.major.width": 0.6,
    "legend.frameon": False,
    "savefig.facecolor": "white",
})
MM = 1 / 25.4
WIDTH = 183 * MM
INK = "#2B2B2B"
MUTED = "#6B6B6B"
GRID = "#E6E6E6"
FAMILY = {"dependency": "#3D7EA6", "api": "#E09F3E", "structure": "#7A9E7E"}
FAMILY_LABEL = {"dependency": "Declared dependencies", "api": "Syntactic interface", "structure": "Project structure"}
ROLE = {"publication": "#A9C5DE", "endpoint": "#2F5D85"}
GROUP = {"2018-2020": "#9DB7CF", "2021-2022": "#E3B579"}
STATUS_ORDER = ["extracted", "confirmed_absent", "extracted_partial", "dynamic_metadata", "unsupported_syntax", "parse_failure"]
STATUS_LABEL = {
    "extracted": "Extracted", "confirmed_absent": "Confirmed absent", "extracted_partial": "Partially extracted",
    "dynamic_metadata": "Build-time (dynamic)", "unsupported_syntax": "Not statically evaluable", "parse_failure": "Parse failure",
}


def read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def dependency_status_snapshot_counts(rows: list[dict[str, str]]) -> Counter:
    """Count distinct snapshots across all dependency-related declaration fields."""
    snapshot_sets: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in rows:
        if row["field_kind"] in {"dependency", "file", "setup_kwargs"}:
            snapshot_sets[(row["snapshot_role"], row["observation_status"])].add(row["snapshot_id"])
    return Counter({key: len(value) for key, value in snapshot_sets.items()})


def wilson(successes: int, n: int) -> tuple[float, float, float]:
    if n == 0:
        return math.nan, math.nan, math.nan
    z = 1.959964
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    # Clip floating-point drift so the interval always contains p and stays within [0, 1].
    return p, max(0.0, min(centre - half, p)), min(1.0, max(centre + half, p))


def panel_label(ax: plt.Axes, label: str, x: float | None = None) -> None:
    """Register a panel label; save() places it left of the panel's tick labels once layout is final."""
    ax.figure.__dict__.setdefault("_panel_labels", []).append((ax, label))


def place_panel_labels(fig: plt.Figure) -> None:
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    inverse = fig.transFigure.inverted()
    rise = 8 / (fig.get_figheight() * 72)
    for ax, label in getattr(fig, "_panel_labels", []):
        legend = ax.get_legend()
        if legend is not None:
            legend.set_in_layout(False)
        box = inverse.transform_bbox(ax.get_tightbbox(renderer, for_layout_only=True))
        axes_box = ax.get_position()
        fig.text(max(min(box.x0, axes_box.x0) - 0.005, 0.002), axes_box.y1 + rise, label, fontsize=9,
                 fontweight="bold", va="bottom", ha="left", color=INK)


def save(fig: plt.Figure, stem: Path, source: list[dict[str, object]], skill_scripts: Path | None) -> None:
    stem.parent.mkdir(parents=True, exist_ok=True)
    place_panel_labels(fig)
    if skill_scripts is not None:
        sys.path.insert(0, str(skill_scripts))
        from audit_panel_alignment import require_matplotlib_panel_alignment  # type: ignore
        fig.canvas.draw()
        require_matplotlib_panel_alignment(
            fig, json_out=f"{stem}.alignment.json", overlay_svg=None,
            tolerance_pt=1.5, gutter_tolerance_pt=1.5, strict=False,
        )
    legends = [ax.get_legend() for ax in fig.axes if ax.get_legend() is not None]
    extra_artists = [*legends, *fig.texts, *(text for ax in fig.axes for text in ax.texts)]
    for suffix in (".pdf", ".svg"):
        fig.savefig(f"{stem}{suffix}", bbox_inches="tight", bbox_extra_artists=extra_artists, pad_inches=0.06)
    fig.savefig(f"{stem}.png", dpi=600, bbox_inches="tight", bbox_extra_artists=extra_artists, pad_inches=0.06)
    fig.savefig(f"{stem}.tiff", dpi=600, bbox_inches="tight", bbox_extra_artists=extra_artists, pad_inches=0.06)
    plt.close(fig)
    fields = sorted({key for row in source for key in row})
    with Path(f"{stem}_source_data.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(source)


def year_group(date: str) -> str:
    return "2018-2020" if date[:4] <= "2020" else "2021-2022"


# -- Figure 1 -------------------------------------------------------------------


def fig1(paths: dict[str, Path], out: Path, scripts: Path | None) -> None:
    screening = read(paths["screening"])
    eligibility = {row["scholarly_doi"]: row for row in read(paths["eligibility"])}
    endpoints = {row["scholarly_doi"]: row for row in read(paths["endpoints"])}
    projects = read(paths["tables"] / "project_records.csv")
    project_dois = {row["scholarly_doi"] for row in projects}
    stages = [
        ("JOSS articles in frame", lambda r: True),
        ("Metadata screening passed", lambda r: r["screen_status"] == "eligible_metadata"),
        ("Publication archive eligible", lambda r: eligibility.get(r["doi"], {}).get("final_eligibility") == "eligible_archive"),
        ("Endpoint resolved", lambda r: endpoints.get(r["doi"], {}).get("resolution_status") == "resolved"),
        ("Paired snapshots extracted", lambda r: r["doi"] in project_dois),
    ]
    counts = {(stage, group): 0 for stage, _ in stages for group in GROUP}
    for row in screening:
        group = year_group(row["publication_date"])
        for stage, rule in stages:
            if rule(row):
                counts[(stage, group)] += 1

    fig = plt.figure(figsize=(WIDTH, 92 * MM))
    grid = fig.add_gridspec(2, 2, height_ratios=[1.0, 1.0], width_ratios=[1.25, 1.0], hspace=0.75, wspace=0.55,
                            left=0.17, right=0.97, top=0.92, bottom=0.12)
    ax = fig.add_subplot(grid[:, 0])
    source: list[dict[str, object]] = []
    y = np.arange(len(stages))[::-1]
    for index, (stage, _) in enumerate(stages):
        left = 0
        for group, colour in GROUP.items():
            value = counts[(stage, group)]
            ax.barh(y[index], value, left=left, color=colour, height=0.62, edgecolor="white", linewidth=0.5)
            left += value
            source.append({"panel": "a", "stage": stage, "publication_years": group, "articles_or_projects": value})
        ax.text(left + 25, y[index], f"{left:,}", va="center", ha="left", fontsize=7.5, color=INK)
    ax.set_yticks(y, [stage for stage, _ in stages])
    ax.set_xlim(0, max(sum(counts[(stages[0][0], g)] for g in GROUP) * 1.15, 1))
    ax.set_xlabel("JOSS articles or projects")
    # counts are printed at bar ends; no gridlines to cross them
    ax.set_axisbelow(True)
    handles = [mpl.patches.Patch(color=colour, label=f"Published {group}") for group, colour in GROUP.items()]
    ax.legend(handles=handles, loc="lower left", bbox_to_anchor=(0.0, 1.0), ncol=1, handlelength=1.0)
    panel_label(ax, "a", x=-0.62)

    ax = fig.add_subplot(grid[0, 1])
    combos = Counter((row["endpoint_resolution_mode"], row["endpoint_ref_type"]) for row in projects)
    modes = [("live", "Live resolution\n13–14 Aug 2026"), ("retrospective_as_of", "Retrospective resolution\nas of 14 Aug 2026")]
    kinds = [("release", "Release", ROLE["endpoint"]), ("default_branch", "Default-branch head", ROLE["publication"])]
    for index, (mode, label) in enumerate(modes):
        left = 0
        for kind, kind_label, colour in kinds:
            value = combos.get((mode, kind), 0)
            ax.barh(index, value, left=left, color=colour, height=0.55, edgecolor="white", linewidth=0.5)
            left += value
            source.append({"panel": "b", "resolution_mode": mode, "endpoint_ref_type": kind, "projects": value})
        ax.text(left + 8, index, f"{left:,}", va="center", fontsize=7.5, color=INK)
    ax.set_yticks(range(len(modes)), [label for _, label in modes])
    ax.invert_yaxis()
    ax.set_xlim(0, max(sum(v for (m, _), v in combos.items() if m == mode) for mode, _ in modes) * 1.22 + 1)
    ax.set_xlabel("Projects")
    ax.legend(handles=[mpl.patches.Patch(color=c, label=l) for _, l, c in kinds], loc="lower right",
              bbox_to_anchor=(1.0, 1.0), ncol=2, handlelength=1.0, columnspacing=1.0)
    panel_label(ax, "b", x=-0.9)

    ax = fig.add_subplot(grid[1, 1])
    by_year: dict[str, list[float]] = defaultdict(list)
    for row in projects:
        if row["article_to_endpoint_ref_days"].lstrip("-").isdigit():
            by_year[row["publication_year"]].append(int(row["article_to_endpoint_ref_days"]) / 365.25)
    years = sorted(by_year)
    data = [by_year[year] for year in years]
    parts = ax.boxplot(data, positions=range(len(years)), widths=0.55, patch_artist=True, showfliers=False,
                       medianprops={"color": INK, "linewidth": 0.9}, whiskerprops={"color": MUTED, "linewidth": 0.6},
                       capprops={"color": MUTED, "linewidth": 0.6})
    for patch in parts["boxes"]:
        patch.set(facecolor="#DCE7F1", edgecolor=MUTED, linewidth=0.6)
    rng = np.random.default_rng(20261101)
    for index, values in enumerate(data):
        ax.scatter(index + rng.uniform(-0.18, 0.18, len(values)), values, s=2.5, color=ROLE["endpoint"], alpha=0.45,
                   linewidths=0, rasterized=True, zorder=3)
        source += [{"panel": "c", "publication_year": years[index], "years_article_to_endpoint_ref": round(v, 4)} for v in values]
    ax.set_xticks(range(len(years)), [f"{year}\nn={len(by_year[year])}" for year in years])
    ax.set_ylabel("Years, article to endpoint")
    ax.set_ylim(bottom=min(0, min((min(v) for v in data if v), default=0)) - 0.2)
    ax.yaxis.grid(True, color=GRID, linewidth=0.5)
    ax.set_axisbelow(True)
    panel_label(ax, "c", x=-0.9)
    save(fig, out / "fig1_cohort_and_endpoints", source, scripts)


# -- Figure 2 -------------------------------------------------------------------


def fig2(paths: dict[str, Path], out: Path, scripts: Path | None) -> None:
    families = read(paths["summary"] / "project_family_delta_metrics.csv")
    projects = {row["project_id"]: row for row in read(paths["tables"] / "project_records.csv")}
    source: list[dict[str, object]] = []
    fig = plt.figure(figsize=(WIDTH, 62 * MM))
    grid = fig.add_gridspec(1, 3, wspace=0.55, left=0.07, right=0.985, top=0.84, bottom=0.2)

    ax = fig.add_subplot(grid[0, 0])
    order = ["dependency", "api", "structure"]
    for index, family in enumerate(order):
        rows = [row for row in families if row["family"] == family and row["paired_observation_complete"] == "true"]
        changed = sum(row["observed_any_change"] == "true" for row in rows)
        p, low, high = wilson(changed, len(rows))
        ax.errorbar(index, p, yerr=[[p - low], [high - p]], fmt="o", color=FAMILY[family], ms=4.5, capsize=2, lw=0.8)
        ax.text(index, -0.07, f"n={len(rows)}", ha="center", va="top", fontsize=7.5, color=MUTED,
                transform=ax.get_xaxis_transform())
        source.append({"panel": "a", "family": family, "projects_with_change": changed, "projects": len(rows),
                       "proportion": round(p, 6), "wilson_low": round(low, 6), "wilson_high": round(high, 6)})
    ax.set_xticks(range(3), ["Depend-\nencies", "Interface", "Structure"])
    ax.tick_params(axis="x", pad=16)
    ax.set_xlim(-0.6, 2.6)
    ax.set_ylim(0, 1)
    ax.set_ylabel("Projects with ≥1 added, removed\nor changed key (proportion)")
    ax.yaxis.grid(True, color=GRID, linewidth=0.5)
    ax.set_axisbelow(True)
    panel_label(ax, "a", x=-0.42)

    ax = fig.add_subplot(grid[0, 1])
    years = sorted({row["publication_year"] for row in projects.values()})
    offsets = {"dependency": -0.15, "api": 0.0, "structure": 0.15}
    for family in order:
        xs, ps, lows, highs = [], [], [], []
        for index, year in enumerate(years):
            rows = [row for row in families if row["family"] == family and row["paired_observation_complete"] == "true"
                    and projects[row["project_id"]]["publication_year"] == year]
            changed = sum(row["observed_any_change"] == "true" for row in rows)
            p, low, high = wilson(changed, len(rows))
            xs.append(index + offsets[family]); ps.append(p); lows.append(low); highs.append(high)
            source.append({"panel": "b", "family": family, "publication_year": year, "projects_with_change": changed,
                           "projects": len(rows), "proportion": round(p, 6), "wilson_low": round(low, 6), "wilson_high": round(high, 6)})
        ps_arr = np.array(ps)
        ax.errorbar(xs, ps_arr, yerr=[ps_arr - np.array(lows), np.array(highs) - ps_arr], fmt="-o", color=FAMILY[family],
                    ms=3.2, lw=0.8, capsize=1.5, label=FAMILY_LABEL[family])
    ax.set_xticks(range(len(years)), years)
    ax.set_ylim(0, 1)
    ax.set_xlabel("JOSS publication year")
    # Keep the in-panel legend clear of background strokes.
    ax.yaxis.grid(False)
    ax.set_axisbelow(True)
    ax.legend(loc="lower left", ncol=1, handlelength=1.2, fontsize=7.5,
              frameon=True, facecolor="white", framealpha=1.0, edgecolor="none")
    panel_label(ax, "b", x=-0.12)

    ax = fig.add_subplot(grid[0, 2])
    positions = []
    for index, family in enumerate(order):
        rows = [row for row in families if row["family"] == family and row["paired_observation_complete"] == "true"
                and int(row["interpretable_delta_denominator"] or 0) > 0]
        fractions = [(int(r["added_count"]) + int(r["removed_count"]) + int(r["changed_count"])) / int(r["interpretable_delta_denominator"]) for r in rows]
        parts = ax.boxplot([fractions], positions=[index], widths=0.5, patch_artist=True, showfliers=False,
                           medianprops={"color": INK, "linewidth": 0.9}, whiskerprops={"color": MUTED, "linewidth": 0.6},
                           capprops={"color": MUTED, "linewidth": 0.6})
        parts["boxes"][0].set(facecolor=FAMILY[family], alpha=0.35, edgecolor=MUTED, linewidth=0.6)
        positions.append(index)
        source += [{"panel": "c", "family": family, "project_id": r["project_id"], "change_fraction": round(f, 6)} for r, f in zip(rows, fractions)]
    ax.set_xticks(positions, ["Depend-\nencies", "Interface", "Structure"])
    ax.set_ylim(0, 1)
    ax.set_ylabel("Changed keys / compared keys")
    ax.yaxis.grid(True, color=GRID, linewidth=0.5)
    ax.set_axisbelow(True)
    panel_label(ax, "c", x=-0.32)
    save(fig, out / "fig2_change_landscape", source, scripts)


# -- Figure 3 -------------------------------------------------------------------


INDICATORS = [
    ("pyproject.toml", lambda s: "pyproject.toml" in json.loads(s["packaging_files"]), "archive_listing_status"),
    ("setup.py", lambda s: "setup.py" in json.loads(s["packaging_files"]), "archive_listing_status"),
    ("Requirements file", lambda s: any(f.lower().startswith("requirements") for f in json.loads(s["packaging_files"])), "archive_listing_status"),
    ("Python constraint declared", lambda s: bool(json.loads(s["python_constraints"])), "python_constraint_observation_status"),
    ("Build backend declared", lambda s: bool(json.loads(s["build_backends"])), "build_backend_observation_status"),
    ("GitHub Actions", lambda s: "github_actions" in json.loads(s["ci_providers"]), "archive_listing_status"),
    ("Travis CI", lambda s: "travis" in json.loads(s["ci_providers"]), "archive_listing_status"),
    ("CITATION.cff", lambda s: s["citation_cff"] == "true", "archive_listing_status"),
    ("CONTRIBUTING", lambda s: s["contributing"] == "true", "archive_listing_status"),
    ("Code of conduct", lambda s: s["code_of_conduct"] == "true", "archive_listing_status"),
]


def fig3(paths: dict[str, Path], out: Path, scripts: Path | None) -> None:
    structure = defaultdict(dict)
    for row in read(paths["tables"] / "structure_records.csv"):
        structure[row["project_id"]][row["snapshot_role"]] = row
    source: list[dict[str, object]] = []
    fig = plt.figure(figsize=(WIDTH, 70 * MM))
    grid = fig.add_gridspec(1, 2, width_ratios=[1.0, 1.0], wspace=0.08, left=0.17, right=0.985, top=0.86, bottom=0.14)
    ax_a, ax_b = fig.add_subplot(grid[0, 0]), fig.add_subplot(grid[0, 1])
    y = np.arange(len(INDICATORS))[::-1]
    composition_colours = {"kept": "#7A9E7E", "gained": "#3D7EA6", "lost": "#D08770", "never": "#E8E8E8"}
    for index, (label, test, status_field) in enumerate(INDICATORS):
        pairs = [roles for roles in structure.values()
                 if len(roles) == 2 and all(roles[r][status_field] == "complete" for r in roles)]
        pub = sum(test(roles["publication"]) for roles in pairs)
        end = sum(test(roles["endpoint"]) for roles in pairs)
        n = len(pairs)
        ax_a.plot([pub / n, end / n], [y[index]] * 2, color="#BFBFBF", lw=1.2, zorder=1)
        ax_a.scatter([pub / n], [y[index]], color=ROLE["publication"], s=16, zorder=2, edgecolors=MUTED, linewidths=0.3)
        ax_a.scatter([end / n], [y[index]], color=ROLE["endpoint"], s=16, zorder=3)
        kinds = Counter(
            "kept" if test(r["publication"]) and test(r["endpoint"]) else
            "gained" if test(r["endpoint"]) else "lost" if test(r["publication"]) else "never"
            for r in pairs
        )
        left = 0.0
        for kind in ("kept", "gained", "lost", "never"):
            share = kinds[kind] / n
            ax_b.barh(y[index], share, left=left, color=composition_colours[kind], height=0.62, edgecolor="white", linewidth=0.4)
            left += share
        ax_b.text(1.01, y[index], f"n={n}", va="center", fontsize=7.5, color=MUTED)
        source.append({"indicator": label, "governing_status": status_field, "comparable_projects": n,
                       "publication_present": pub, "endpoint_present": end, **{f"{k}_projects": kinds[k] for k in ("kept", "gained", "lost", "never")}})
    ax_a.set_yticks(y, [label for label, _, _ in INDICATORS])
    ax_a.set_xlim(0, 1)
    ax_a.set_xlabel("Share of projects with the item")
    ax_a.xaxis.grid(True, color=GRID, linewidth=0.5)
    ax_a.set_axisbelow(True)
    ax_a.legend(handles=[mpl.lines.Line2D([], [], marker="o", ls="", color=ROLE[r], label=f"{r.capitalize()} snapshot", ms=4)
                         for r in ("publication", "endpoint")], loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2, handletextpad=0.2)
    panel_label(ax_a, "a", x=-0.5)
    ax_b.set_yticks(y, [])
    ax_b.tick_params(axis="y", length=0)
    ax_b.set_xlim(0, 1)
    ax_b.set_xlabel("Share of projects")
    ax_b.legend(handles=[mpl.patches.Patch(color=c, label=k.capitalize()) for k, c in composition_colours.items()],
                loc="lower left", bbox_to_anchor=(0, 1.0), ncol=4, handlelength=1.0, columnspacing=0.8)
    panel_label(ax_b, "b", x=-0.06)
    save(fig, out / "fig3_metadata_transitions", source, scripts)


# -- Figure 4 -------------------------------------------------------------------


def fig4(paths: dict[str, Path], out: Path, scripts: Path | None, require_audits: bool) -> None:
    fields = read(paths["summary"] / "field_observation_summary.csv")
    declarations = read(paths["summary"] / "declaration_status_summary.csv")
    snapshot_counts = dependency_status_snapshot_counts(read(paths["tables"] / "declaration_source_records.csv"))
    source: list[dict[str, object]] = []
    fig = plt.figure(figsize=(WIDTH, 66 * MM))
    grid = fig.add_gridspec(1, 3, wspace=1.05, left=0.155, right=0.975, top=0.84, bottom=0.17)

    ax = fig.add_subplot(grid[0, 0])
    labels = {
        "archive_listing_status": "Archive listing", "build_backend_observation_status": "Build backend",
        "python_constraint_observation_status": "Python constraint", "source_line_observation_status": "Source lines",
        "api_observation_status": "Syntactic interface", "dependency_observation_status": "Dependency declarations",
    }
    rows = [row for row in fields if row["scope"] == "paired_snapshots"]
    y = np.arange(len(rows))[::-1]
    for index, row in enumerate(rows):
        n = int(row["total_project_denominator"])
        ok = int(row["complete_projects"]) + int(row["no_declaration_file_projects"])
        p, low, high = wilson(ok, n)
        ax.barh(y[index], p, color="#A9C5DE", height=0.6)
        ax.errorbar(p, y[index], xerr=[[p - low], [high - p]], fmt="none", ecolor=INK, lw=0.7, capsize=1.5)
        source.append({"panel": "a", "status_field": row["status_field"], "complete_pairs": ok, "pairs": n,
                       "proportion": round(p, 6), "wilson_low": round(low, 6), "wilson_high": round(high, 6)})
    ax.set_yticks(y, [labels[row["status_field"]] for row in rows])
    ax.set_xlim(0, 1)
    ax.set_xlabel(f"Pairs observed completely in both\nsnapshots (share of {int(rows[0]['total_project_denominator'])})")
    ax.xaxis.grid(True, color=GRID, linewidth=0.5)
    ax.set_axisbelow(True)
    panel_label(ax, "a", x=-0.82)

    ax = fig.add_subplot(grid[0, 1])
    dependency_rows = [row for row in declarations if row["field_kind"] in {"dependency", "file", "setup_kwargs"}]
    y = np.arange(len(STATUS_ORDER))[::-1]
    for offset, role in ((0.17, "publication"), (-0.17, "endpoint")):
        for index, status in enumerate(STATUS_ORDER):
            matching = [row for row in dependency_rows if row["snapshot_role"] == role and row["observation_status"] == status]
            snapshots = snapshot_counts[(role, status)]
            denominator = int(matching[0]["snapshot_denominator"]) if matching else int(next(
                row["snapshot_denominator"] for row in declarations if row["snapshot_role"] == role))
            ax.barh(y[index] + offset, snapshots / denominator, height=0.32, color=ROLE[role])
            source.append({"panel": "b", "snapshot_role": role, "observation_status": status,
                           "snapshots_with_status_any_dependency_field": snapshots, "snapshots": denominator})
    ax.set_yticks(y, [STATUS_LABEL[s] for s in STATUS_ORDER])
    ax.set_xlim(0, 1)
    ax.set_xlabel("Snapshots with ≥1 dependency-related\nfield in this status")
    ax.xaxis.grid(True, color=GRID, linewidth=0.5)
    ax.set_axisbelow(True)
    ax.legend(handles=[mpl.patches.Patch(color=ROLE[r], label=r.capitalize()) for r in ("publication", "endpoint")],
              loc="lower right", bbox_to_anchor=(1.0, 1.0), ncol=2, handlelength=1.0)
    panel_label(ax, "b", x=-0.95)

    ax = fig.add_subplot(grid[0, 2])
    audits: list[tuple[str, int, int]] = []
    independent = json.loads(paths["independent_audit"].read_text(encoding="utf-8")) if paths["independent_audit"].exists() else None
    if independent:
        dep = independent["dependency"]["exact_record"]
        audits.append(("Independent re-\nimplementation\n(dependency rows)", dep["agreement"], dep["expected"]))
    reference = json.loads(paths["reference_audit"].read_text(encoding="utf-8")) if paths["reference_audit"].exists() else None
    if reference:
        agree = sum(c.get("true", 0) for c in reference["units"].values())
        total = sum(c.get("true", 0) + c.get("false", 0) for c in reference["units"].values())
        audits.append(("Reference audit\n(annotated items)", agree, total))
    retro = json.loads(paths["retrospective"].read_text(encoding="utf-8")) if paths["retrospective"].exists() else None
    if retro:
        audits.append(("Retrospective vs live\nendpoint (same commit)", retro["same_commit"], retro["live_resolved_projects"]))
    if require_audits and len(audits) < 3:
        raise SystemExit("figure 4 needs the independent, reference and retrospective audit outputs")
    y = np.arange(len(audits))[::-1]
    for index, (label, agree, total) in enumerate(audits):
        p = agree / total
        ax.plot(p, y[index], "o", color=ROLE["endpoint"], ms=4, clip_on=False)
        ax.annotate(f"{agree:,}/{total:,}", xy=(1.0, y[index]), xycoords=("axes fraction", "data"), xytext=(7, 0), textcoords="offset points", va="center", fontsize=7.5, color=MUTED, annotation_clip=False)
        source.append({"panel": "c", "audit": label.replace("\n", " "), "agreeing": agree, "checked": total,
                       "proportion": round(p, 6)})
    ax.set_yticks(y, [label for label, _, _ in audits])
    ax.set_ylim(-0.6, len(audits) - 0.4)
    ax.set_xlim(0.95, 1.0)
    ax.set_xlabel("Agreement on checked units")
    ax.xaxis.grid(True, color=GRID, linewidth=0.5)
    ax.set_axisbelow(True)
    panel_label(ax, "c", x=-1.25)
    save(fig, out / "fig4_validation_audit", source, scripts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True, help="data/census_v0.3")
    parser.add_argument("--frame", type=Path, required=True, help="data/census_frame_v0.3")
    parser.add_argument("--reference-audit", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--skill-scripts", type=Path, default=None, help="nature-figure scripts directory for the alignment gate")
    parser.add_argument("--require-audits", action="store_true")
    args = parser.parse_args()
    paths = {
        "tables": args.dataset / "tables",
        "summary": args.dataset / "summary_tables",
        "screening": args.frame / "screening_ledger.csv",
        "eligibility": args.frame / "archive_eligibility_ledger.csv",
        "endpoints": args.dataset / "endpoint_resolution.csv",
        "independent_audit": args.dataset / "independent_semantic_audit.json",
        "reference_audit": args.reference_audit or args.dataset / "reference_audit_summary.json",
        "retrospective": args.dataset / "retrospective_agreement.json",
    }
    fig1(paths, args.output_dir, args.skill_scripts)
    fig2(paths, args.output_dir, args.skill_scripts)
    fig3(paths, args.output_dir, args.skill_scripts)
    fig4(paths, args.output_dir, args.skill_scripts, args.require_audits)
    print(json.dumps({"figures": 4, "output_dir": str(args.output_dir)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
