# Data dictionary v0.2

All released CSV files use UTF-8 with a byte-order mark and a header row. Empty
strings mean that the upstream value was unavailable or not applicable; they do
not mean zero or false. JSON-valued cells are serialized with sorted keys and no
insignificant whitespace. Boolean text is lowercase `true` or `false`.

## Relationships

```text
project_records (project_id, pair_id)
  |-- snapshot_records (snapshot_id, project_id, pair_id)
  |     |-- structure_records (snapshot_id)
  |     |-- dependency_records (snapshot_id, dependency observation)
  |     |-- api_symbol_records (snapshot_id, symbol_key)
  |     `-- validation_records (snapshot_id, check_name, evidence)
  `-- pair_delta_records (pair_id, family, feature_group, feature_key)
```

## `project_records.csv`

Primary key: `project_id`. One row per scholarly software project and version
pair.

| Field | Type | Meaning |
|---|---|---|
| `project_id` | string | Stable local identifier derived from the normalized JOSS DOI. |
| `pair_id` | string | Identifier containing the project and shortened hashes of both snapshots. |
| `scholarly_doi` | DOI string | Normalized JOSS article DOI. |
| `title` | string | Article title supplied by Crossref. |
| `publication_date` | ISO date | JOSS publication date used in the candidate frame. |
| `joss_url` | URL | JOSS article page. |
| `input_repository_url` | URL | Repository URL observed during initial screening. |
| `resolved_repository_url` | URL | Canonical repository URL at endpoint resolution; may differ after a rename or move. |
| `selection_rank` | integer | Deterministic SHA-256 sampling rank; smaller values were screened first. |
| `selected_role` | enum | `primary` or `replacement` in the pilot frame; `census_provisional` in the frozen census frame. |
| `publication_archive_doi` | DOI string | DOI of the publication-linked software archive (Zenodo or Figshare in the final paired cohort). |
| `publication_snapshot_id` | string | Foreign key to the publication row in `snapshot_records`. |
| `endpoint_snapshot_id` | string | Foreign key to the endpoint row in `snapshot_records`. |
| `endpoint_policy` | enum | Frozen selection rule, currently `latest_stable_release_else_default_branch_head`. |
| `endpoint_ref_type` | enum | `release` or `default_branch`. |
| `endpoint_ref_name` | string | Release tag or default-branch name selected by the endpoint policy. |
| `endpoint_commit_sha` | 40-character hex | Immutable Git commit used for the endpoint archive. |
| `repository_archived_at_endpoint` | boolean text | GitHub archived flag observed during endpoint resolution. |
| `repository_license_spdx_at_endpoint` | SPDX string | GitHub-reported licence identifier at endpoint resolution. |

## `snapshot_records.csv`

Primary key: `snapshot_id`. Exactly two rows per resolved project pair.

| Field | Type | Meaning |
|---|---|---|
| `snapshot_id` | string | Project, role and shortened archive-hash identifier. |
| `project_id` | string | Foreign key to `project_records`. |
| `pair_id` | string | Foreign key to `project_records`. |
| `snapshot_role` | enum | `publication` or `endpoint`. |
| `source_provider` | enum | Archive provider. The controlled vocabulary is `zenodo`, `figshare`, `caltechdata` or `github`; the final paired cohort uses 341 Zenodo and 4 Figshare publication snapshots plus 345 GitHub endpoint snapshots. |
| `source_identifier` | string | Archive DOI for publication snapshots or canonical `owner/repository` for endpoints. |
| `source_ref` | string | Publication-archive file key or GitHub tag/branch selected for the snapshot. |
| `commit_sha` | 40-character hex or empty | Endpoint Git commit; empty when a publication archive does not expose a verified commit. |
| `source_url` | URL | Public source or archive URL. |
| `archive_bytes` | integer | Size of the acquired source archive. |
| `archive_sha256` | 64-character hex | Locally computed archive hash. |
| `upstream_checksum` | checksum or empty | Provider-reported checksum, currently MD5 for the selected Zenodo and Figshare publication archives. |
| `parser_version` | string | Version of the static extraction implementation. |
| `third_party_code_executed` | boolean text | Safety assertion; must be `false`. |

Local cache paths are deliberately excluded from the public table.

## `structure_records.csv`

Primary key: `snapshot_id`. One structural summary per snapshot.

| Field | Type | Meaning |
|---|---|---|
| `snapshot_id`, `project_id`, `snapshot_role` | string | Snapshot and project foreign keys plus role. |
| `archive_type` | enum | `zip` or `tar`. |
| `archive_member_count` | integer | Non-junk archive members after removal of a shared wrapper directory. |
| `archive_bytes`, `archive_sha256` | integer/string | Archive size and verified hash. |
| `suspicious_member_count` | integer | Member paths that are absolute, contain `..` or resemble drive paths. |
| `link_member_count` | integer | Archive symlink or hard-link entries; links are never followed. |
| `duplicate_normalized_member_count` | integer | Collisions after shared-root normalization. |
| `skipped_large_text_count` | integer | Interesting text members skipped by size limits. |
| `packaging_files` | JSON array | Root packaging and dependency files parsed or inspected. |
| `build_backends` | JSON array | PEP 517 build backends declared in `pyproject.toml`. |
| `python_constraints` | JSON array | Source-qualified declared Python-version constraints. |
| `declared_dependency_record_count` | integer | Child rows emitted to `dependency_records`. |
| `unparsed_dependency_record_count` | integer | Dependency rows retained with `unparsed` status. |
| `source_python_file_count` | integer | Python files selected by the documented source-root heuristic. |
| `source_python_line_count` | integer | Physical text lines in selected Python source files. |
| `ast_parse_failure_count` | integer | Selected Python files that could not be parsed by the configured AST parser. |
| `syntactic_api_symbol_count` | integer | Child rows emitted to `api_symbol_records`. |
| `test_python_file_count` | integer | Python test files identified by path/name rules. |
| `ci_workflow_file_count` | integer | GitHub Actions YAML files found in `.github/workflows`. |
| `ci_providers` | JSON array | Detected CI systems, including legacy root configuration files. |
| `citation_cff`, `codemeta`, `zenodo_metadata` | boolean text | Presence of common scholarly software metadata files. |
| `contributing`, `code_of_conduct`, `security_policy`, `changelog` | boolean text | Presence of governance or change-documentation files. |
| `dependency_parse_complete` | boolean text | `true` only when supported metadata was parsed without known non-literal gaps or unparsed rows. |
| `api_parse_complete` | boolean text | `true` only when all selected source files were readable within limits and AST parsing succeeded. |
| `parser_issues` | JSON array | Machine-readable limitations encountered during metadata parsing. |
| `parser_version` | string | Extraction implementation version. |
| `third_party_code_executed` | boolean text | Must be `false`. |

## `dependency_records.csv`

Observation key: `snapshot_id`, `source_file`, `dependency_group`,
`raw_declaration`. Duplicate declarations from distinct files are retained.

| Field | Type | Meaning |
|---|---|---|
| `snapshot_id`, `project_id`, `snapshot_role` | string | Snapshot/project keys and role. |
| `source_file` | relative path | File containing the declaration. |
| `source_kind` | controlled string | Parser/format, for example `pep621`, `poetry`, `setup_py`, `requirements` or `conda_environment`. |
| `dependency_group` | controlled string | `runtime`, `build`, `test`, `development` or `optional:<group>`. |
| `normalized_name` | string or empty | PEP 503-style normalized package name when parsed. |
| `raw_declaration` | string | Source declaration preserved for audit. |
| `specifier` | string | Version constraint in its source ecosystem syntax. |
| `marker` | string | PEP 508 environment marker when available. |
| `extras` | semicolon list | Sorted requested package extras. |
| `parse_status` | enum | `parsed`, `parsed_non_pep508`, `parsed_poetry`, `directive` or `unparsed`. |
| `parse_error` | string | Parser exception summary for unresolved declarations. |

These are declared direct dependencies. They are not proof that the package was
installed or resolved, and entries from multiple overlapping source files are
not silently deduplicated in the observation table.

## `api_symbol_records.csv`

Primary key: `snapshot_id`, `symbol_key`.

| Field | Type | Meaning |
|---|---|---|
| `snapshot_id`, `project_id`, `snapshot_role` | string | Snapshot/project keys and role. |
| `symbol_key` | string | Module, symbol-kind and qualified-name comparison key. |
| `source_file` | relative path | Parsed Python source file. |
| `module` | dotted string | Module name inferred from the source-root heuristic. |
| `symbol_kind` | enum | `class`, `constructor`, `method`, `function` or `async_function`. |
| `qualified_name` | string | Top-level name or `Class.member`. |
| `signature` | string | Normalized syntactic argument list for functions/methods, or a JSON base-class list for classes; conditional variants are joined by ` || `. |
| `decorators` | JSON array | Syntactic decorators on the definition. |
| `export_evidence` | enum | `literal_all`, `lexical_nonprivate` or `class_member`. |
| `line_start`, `line_end` | integer | One-based source evidence location. |
| `variant_count` | integer | Definitions collapsed into the comparison key. |

This table describes a conservative syntactic surface. It does not claim that a
symbol is importable, reachable at runtime or supported as a compatibility
contract.

## `pair_delta_records.csv`

Primary key: `pair_id`, `family`, `feature_group`, `feature_key`.

| Field | Type | Meaning |
|---|---|---|
| `pair_id`, `project_id` | string | Version-pair and project keys. |
| `family` | enum | `api`, `dependency` or `structure`. |
| `feature_group` | controlled string | Subfamily such as `syntactic_symbol`, `declared_dependency`, `packaging_files` or `scalar`. |
| `feature_key` | string | Normalized item compared across snapshots. |
| `publication_value`, `endpoint_value` | string/JSON | Canonical values on each side; empty when absent. |
| `delta_type` | enum | `added`, `removed`, `changed`, `unchanged` or `unavailable`. |
| `comparison_status` | enum | `comparable`, `partial_parse` or `unavailable`. |
| `publication_evidence`, `endpoint_evidence` | relative evidence | Source file/line or JSON list of declarations supporting each value. |

## `validation_records.csv`

One row per machine validation event.

| Field | Type | Meaning |
|---|---|---|
| `snapshot_id`, `project_id`, `snapshot_role` | string | Snapshot/project keys and role. |
| `check_name` | controlled string | Check such as `archive_sha256`, `archive_paths`, `metadata_parse` or `python_ast_warning`. |
| `status` | enum | `pass`, `warning` or `error`. |
| `evidence` | string | Checked archive, source file/line or other locator. |
| `detail` | string | Hash, count, warning or error detail. |

## Manifests and figure source data

- `extraction_manifest.json` records parser/schema versions and row counts.
- `endpoint_resolution_manifest.json` records the endpoint date, policy outcomes
  and resolution methods.
- `pilot_data_overview_source_data.csv` is a figure-specific derivative; it can
  be regenerated from the seven core tables and is not an independent dataset.
