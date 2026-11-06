# Reproduction protocol for v0.3.0

The release supports verification of the distributed files, regeneration from
retained archives, and reacquisition of equivalent source content. Each procedure
has a different input requirement.

## Reference environment

Use Python 3.12 and the direct dependencies in `requirements.txt` plus
`requirements-dev.txt` for tests. `environment_reference.json` and the hash-pinned
`requirements-lock-win-py312.txt` document the reference environment. No indexed
third-party software needs to be installed or executed.

## 1. Verify the distributed release

From the extracted release directory:

```text
python scripts/verify_release_v03.py --root .
python -B -m pytest -q tests
```

This tested procedure checks the package hashes, eight table schemas, row counts,
table hashes, canonical validation, independent audit and signed reference audit.
It requires neither network access nor source archives. The package manifest
may embed the archival DOI; the repository record is authoritative for the DOI.

## 2. Regenerate from retained source archives

The frozen source identities are in `snapshot_records.csv` and the acquisition
ledgers. Put retained publication and endpoint archives in separate cache
directories and pass their locations explicitly. For example, using relative
directories `data/raw/zenodo_archives_v0.3` and `data/raw/endpoint_archives_v0.3`:

```text
python scripts/validate_census_dataset.py --publication-cache data/raw/zenodo_archives_v0.3 --endpoint-cache data/raw/endpoint_archives_v0.3 --rerun --output regeneration_validation.json
python scripts/independent_semantic_audit.py . --sample-size 40
python scripts/summarize_census_dataset.py --tables data/census_v0.3/tables --output data/census_v0.3/census_summary.json --summary-dir data/census_v0.3/summary_tables
python scripts/make_scidata_figures_v03.py --dataset data/census_v0.3 --frame data/census_frame_v0.3 --output-dir figures/census_v0.3 --require-audits
```

The validator's v0.3 defaults select the frozen ledgers and tables. The full
archive-aware validator and deterministic regeneration were tested on the
retained inputs. Independent semantic auditing also needs the archive cache;
use `--archive-root` when the cache is outside the package. Consult each command's
`--help` for figure and summary destinations. The distributed tables and figures
already contain the canonical results.

The recorded endpoint accounting is 697 eligible publication archives and
692 resolved endpoints. Five endpoint archives exceed the 200 MiB size rule.
The core release comprises 692 pairs and 1,384 snapshots.

## 3. Reacquire equivalent source content

Publication archives can be downloaded from the frozen inventory:

```text
python scripts/prefetch_selected_archives.py --inventory data/census_frame_v0.3/archive_file_inventory.csv --cache data/raw/zenodo_archives_v0.3 --checkpoint publication_prefetch.json
python scripts/prefetch_endpoint_archives.py --ledger data/census_v0.3/endpoint_resolution.csv --cache data/raw/endpoint_archives_v0.3 --checkpoint endpoint_prefetch.json
```

The endpoint downloader uses recorded immutable commits; it does not resolve
today's tags or releases. Reacquisition depends on continued upstream access.
Provider-generated archive bytes can change while their source files remain
identical. Compare the original SHA-256 first. If it differs, verify source
content using `snapshot_member_manifest.csv.gz` and `content_tree_sha256`, as
defined in `docs/extraction_specification_v0.3.md`. A differing archive must not
be silently substituted into the byte-identical regeneration test.

## Figure and numeric provenance

The figure source-data CSV files are in `figures/census_v0.3/`. The summary tables
and validation outputs contain the numeric inputs used in the Data Descriptor.
`scripts/fill_manuscript_numbers.py` can populate the manuscript template supplied
with the separate submission source package. The data/code archive is independent
of the submission documents.
