# Dataset design v0.2

## Unit and keys

One project-level unit connects a JOSS paper to exactly two immutable source
snapshots where available.

- `project_id`: stable local identifier derived from the normalized JOSS DOI.
- `snapshot_id`: `project_id` plus `publication` or `endpoint` and source hash.
- `pair_id`: `project_id` plus both snapshot identifiers.
- child-record keys: `snapshot_id` plus a dependency, API symbol or evidence ID.

Unresolved endpoints are retained as project records with explicit reason codes
but do not produce fabricated snapshot or delta rows.

## Snapshot rules

### Publication snapshot

Use the immutable software-archive record linked from the JOSS
article/repository. The final paired cohort uses Zenodo and Figshare publication
records. Prefer a source archive (`.tar.gz`, `.zip`) over a built wheel when
multiple files encode the same release. Record the provider-reported checksum
and a locally computed SHA-256.

### Endpoint snapshot

Resolve in this order on the declared dataset snapshot date:

1. latest non-draft, non-prerelease GitHub release; or
2. immutable commit at the default branch head when no qualifying release exists.

Resolve the selected ref to a commit SHA and download a source archive addressed
by that SHA. Unreleased tags are deliberately ignored because Git tag recency is
not consistently defined by the public API. Record redirects, renamed
repositories, archived state and endpoint selection reason.

## Record families

### `project_records`

Scholarly DOI/title/date, repository identity, selection rank/role, eligibility
state, archive DOI and endpoint resolution state.

### `snapshot_records`

Source URL, ref/tag, commit when known, release date, archive checksums, archive
type, member count, source-file metrics and parser-version information.

### `dependency_records`

Source file, dependency group, normalized package name, raw declaration, parsed
specifier, marker/extras when supported, and parse status. Sources include PEP
621, Poetry, `setup.cfg`, statically readable `setup.py`, requirements files and
conda environment files. These are declared direct dependencies only.

### `api_symbol_records`

Python module path, symbol kind, qualified name, normalized syntactic signature,
decorator flags and parse status. Exclude tests, examples, vendored code,
generated/build directories and private names beginning with `_`. The record is
a reproducible syntactic approximation, not a runtime export guarantee.

### `structure_records`

Packaging/build files, declared Python constraint, CI providers/workflow counts,
test-file counts, source metrics, licence files, citation metadata, changelog,
security policy, contribution guide, code of conduct and archival metadata.

### `pair_delta_records`

For each normalized feature: publication value, endpoint value, delta type
(`added`, `removed`, `changed`, `unchanged`, `unavailable`) and evidence keys.
Aggregated counts are derived outputs, never hand-entered observations.

### `validation_records`

Schema checks, checksum checks, parser errors, duplicate and referential-integrity
checks, independent rerun comparison and manual audit decisions.

## Static safety boundary

Archives are read with bounded archive and text parsers. Files are not extracted
to runnable locations. Candidate modules are parsed with Python's AST without
importing them. `setup.py` is parsed as syntax and never executed. Symlinks,
absolute paths and path traversal members are flagged. Archive member and text
size limits are enforced.

## Pilot and scale-up

Pilot target: 20 primary projects plus ordered replacements as needed. The first
implementation checkpoint may use the subset whose publication archives have
already passed checksum and static archive inspection.

Scale-up target: all eligible Python projects in the 2018-2020 JOSS frame. The
frozen census contains 345 resolved project pairs. Their publication snapshots
comprise 341 Zenodo records and 4 Figshare records; all endpoint snapshots are
immutable GitHub archives addressed by commit SHA.

## Technical validation

- Verify repository-provided checksums and calculate SHA-256 for every archive.
- Run schema, uniqueness, referential-integrity and controlled-vocabulary checks.
- Rerun extraction from cached immutable archives and require byte-identical
  canonical tables after volatile timestamps are excluded.
- Parse a stratified sample with a second independent parser or manual audit.
- Double-check ambiguous package roots, generated code and namespace packages.
- Manually verify a stratified sample of dependency and API delta rows against
  source evidence; report denominators and uncertainty.
- Preserve all parser failures and missing values in machine-readable audit logs.

## Proposed release package

```text
dataset/
  README.md
  CITATION.cff
  LICENSE-data
  project_records.csv
  snapshot_records.csv
  dependency_records.csv
  api_symbol_records.csv
  structure_records.csv
  pair_delta_records.csv
  validation_records.csv
  schemas/
  provenance/
  checksums/
```

Raw third-party source archives will normally be referenced by persistent
identifiers and checksums rather than redistributed. The derived dataset will use
an open data licence after a field-level rights audit; extraction code will use a
separate software licence.
