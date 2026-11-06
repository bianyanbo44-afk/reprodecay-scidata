# Changes from v0.2.4 to v0.3.0

v0.3.0 makes two independent changes, and this document reports them separately.

1. **Corrections.** It corrects how declarations are extracted and how observation completeness is recorded. The effect of the corrections is measured on exactly the 345 projects released in v0.2.4.
2. **Extension.** It extends the frame from JOSS 2018–2020 to 2018–2022. The extension adds projects; it does not change any v0.2.4 project.

The machine-readable comparison is in `data/census_v0.3/release_comparison/`. It contains `release_comparison.json`, `completeness_transitions.csv`, `dependency_key_differences.csv`, `delta_row_transitions.csv`, `summary_value_comparison.csv` and `affected_projects.csv`. It is produced by `scripts/compare_releases.py`. For the comparison, the v0.3.0 tables were first restricted to the v0.2.4 projects with `scripts/subset_tables.py` and then summarized.

## 1. Corrections

The extraction rules are written down in `docs/extraction_specification_v0.3.md`.

| Area | v0.2.4 | v0.3.0 |
|---|---|---|
| `setup(**mapping)` | Arguments passed through a mapping were ignored, and the snapshot could still be marked complete. | Expanded when the mapping can be evaluated statically; otherwise the field is recorded as `unsupported_syntax`. |
| `project.dynamic`, setuptools `file:`/`attr:`, `tool.setuptools.dynamic` | Not flagged. | Recorded as `dynamic_metadata`. |
| Poetry inline tables | Reduced to the package name and version. | All keys kept: markers and extras in their own columns, everything else in the new `attributes` column. Optional entries are assigned to their extras. |
| Poetry exact versions (`numpy = "1.2.3"`) | Name and version were concatenated into a spurious package name. | Name taken from the key; the version is the specifier. |
| `setup_requires`, `tests_require`, `tool.poetry.dev-dependencies`, flit metadata, Pipfile | Not read. | Read. |
| PEP 735 `[dependency-groups]` | Not read, and the snapshot could still be marked complete. | Read; group names map as for Poetry groups. Found by the reference-based audit (finding F1). |
| Declaration file or module stored as a link | Not read (tar); in a zip, the link target path was read as the file's text. | Read as the archive member the link points to; a link that leaves the archive is reported as `link_target_unresolved`. Found by the reference-based audit (finding F2). |
| `-e .` in requirements files | Recorded as an unparsed requirement, so the snapshot was marked incomplete. | Recorded as a directive. |
| `-r` include of a file in a subdirectory | Recorded as a directive; the included declarations were not read. | The included file is read. |
| Non-literal `python_requires`; a name mutated in `setup.py` | Silently dropped, or the value before mutation was used. | Recorded as `unsupported_syntax`. |
| Completeness of a declaration field | Not recorded. | New table `declaration_source_records.csv`, with one status per field: `extracted`, `extracted_partial`, `confirmed_absent`, `dynamic_metadata`, `unsupported_syntax` or `parse_failure`. |
| Completeness of structural fields | Inferred from the existence of the structure row. | Listing-derived fields are separated from content-dependent fields. The content-dependent fields carry their own status: Python constraints, build backends and source lines. |
| Interface completeness | Incomplete whenever any text member was skipped for size, including non-source files. | Incomplete only when a selected package source file is unread or fails to parse. |
| `comparison_status` of structure deltas | Always `comparable`. | Derived from the status that governs the field. `partial_parse` is renamed `incomplete_observation`. |
| Paired summaries | "Complete" meant that both numbers existed. | Two named bases are reported, each with its denominator and exclusion reasons. `all_available_values` describes extracted record volume; `adequately_observed_pairs` keeps only pairs whose governing status is complete in both snapshots. |
| Content identity | Archive SHA-256 only. | `content_tree_sha256` and a per-member manifest in addition to the archive SHA-256. |
| Dates | JOSS publication date only. | Archive date, endpoint reference date, endpoint resolution time and the intervals between them. The 248 releases resolved live through the GitHub web interface had no release record; their release identifier and publication time were retrieved by tag name on 2026-11-01, each lookup is kept in `endpoint_release_date_backfill.csv`, and no selected commit changed. |

### Effect on the 345 projects of v0.2.4

**Affected projects.** 154 projects are affected and 191 are unchanged. `affected_projects.csv` lists, for each affected project, the families and fields that changed.

**Dependency records.** The number of rows rose from 9,074 to 9,886, and 175 snapshots changed their row count. Compared by key (dependency group and normalized name):

| Key comparison | Keys |
|---|---|
| Unchanged | 7,571 |
| New in v0.3.0 | 803 |
| Removed (spurious) | 70 |
| Value changed | 34 |

Of the 803 new keys, 323 come from `setup.py` (`setup_requires`, `tests_require` and expanded mappings), 249 from PEP 735 dependency groups, 71 from files included with `-r`, 66 from Poetry development and group tables, 33 from Pipfiles and 26 from `setup.cfg`; the remaining 35 come from other Poetry and flit tables, conda environment files and requirements files. The 70 removed keys include the spurious Poetry names.

**Completeness, publication / endpoint / paired.**

| Measure | v0.2.4 | v0.3.0 |
|---|---|---|
| Dependency observation complete | 250 / 265 / 238 | 228 / 253 / 217 |
| Interface observation complete | 322 / 320 / 317 | 332 / 334 / 330 |
| Structure observation complete | 345 / 345 / 345 (row existence) | 311 / 329 / 311 |

The structure decrease comes from 33 pairs with an incomplete Python-constraint observation and 1 pair with an incomplete source-line count. Across both roles, the dependency fields that prevent completeness are:

| Field status | Fields |
|---|---|
| `unsupported_syntax` (mostly `setup.py` values computed at run time) | 230 |
| `extracted_partial` | 45 |
| `dynamic_metadata` | 5 |
| `parse_failure` | 3 |

**Summary values.** Of 129 summary values reported in v0.2.4, 74 are unchanged and 55 changed. The full list, with both values side by side, is in `summary_value_comparison.csv`.

Unchanged:

- The medians of paired change on the `all_available_values` basis. Source lines rose by 193 (95% interval 87 to 370), interface symbols by 3 (1 to 7), and the median change in dependency records, source files, test files and CI workflow files is 0.
- All `all_extracted_projects_observed` rows of the family summary.
- The binary transitions shown in Figure 3: `pyproject.toml` added in 108 projects and removed in 0; GitHub Actions added in 141 and removed in 1; Travis CI added in 3 and removed in 123; `CITATION.cff` added in 51 and removed in 0.

Changed:

- Every completeness count.
- The numbers of adequately observed pairs.
- The median change in source lines among adequately observed pairs: 203.5 (90.0 to 370.0) in 344 pairs. v0.2.4 reported 193 for all 345 projects as if all were completely observed.
- The share of projects with at least one change among completely observed pairs:

| Family | v0.2.4 | v0.3.0 |
|---|---|---|
| Interface | 211/317 (66.6%) | 219/330 (66.4%) |
| Dependencies | 133/238 (55.9%) | 122/217 (56.2%) |
| Structure | 243/345 (70.4%) | 215/311 (69.1%) |

### Conclusions and figure descriptions that change

- The v0.2.4 statement that "structure records are complete for all 345 projects at both snapshots" was wrong for the content-dependent fields. It is replaced by field-level completeness (Figure 4a).
- "Complete parsing" for dependencies and the interface now refers to the corrected flags. The numbers of complete pairs changed as shown above.
- The change landscape of Figure 2 is now restricted to adequately observed pairs, with denominators. The dark heatmap of all projects was removed.
- The descriptive conclusions are unchanged. Change is common in all three families. Net medians are small. Packaging and CI moved towards `pyproject.toml` and GitHub Actions. The proportions with at least one change differ from v0.2.4 by at most 1.3 percentage points.

## 2. Extension of the frame

- **Frame.** 731 JOSS articles published in 2021–2022 were added, so the frame now has 1,671 articles.
- **2018–2020 records.** They are carried over unchanged, except for re-derived processing ranks and retries of earlier transient failures:
  - one publication archive that had returned an HTTP error in August 2026 (joss.00596) was acquired;
  - of the 27 projects whose live endpoint resolution had failed (26 because of network errors, 1 download timeout), 24 were resolved retrospectively (see below); for the other 3 the archive at the selected commit exceeds the 200 MiB download limit.
- **Endpoints of the added projects.** They were resolved retrospectively as of 2026-08-14T05:21:07Z. Applied to the 345 projects that had been resolved live, the retrospective rule selected the same commit in 342 cases (99.1%), as recorded in `retrospective_agreement.csv`.
- **Release size.** The release now contains 692 projects: 697 projects have an eligible publication archive, and the endpoint archive of 5 of them (3 from 2018–2020, 2 from 2021–2022) exceeds the 200 MiB download limit.
