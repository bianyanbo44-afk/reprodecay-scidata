# Reproduction Protocol

This repository contains the v0.2 version-paired census pipeline. The released
derived tables do not redistribute the 10+ GiB of third-party Zenodo, Figshare
and GitHub source archives. The archive ledgers retain the public URLs,
immutable commits, provider checksums and local SHA-256 values needed to
reacquire them under the original providers' terms.

## Reference environment

- Python 3.12.10 (Windows reference run)
- Exact reference tool versions: `environment_reference.json`
- Direct runtime dependencies: `requirements.txt`
- Development/test dependency: `requirements-dev.txt`
- Optional Word conversion dependency: `requirements-docx.txt`
- Fully resolved, SHA-256-pinned Windows/CPython 3.12 environment:
  `requirements-lock-win-py312.txt`

Create an isolated environment and install the requirements before running the
pipeline. Do not install or execute any indexed third-party software archive.

## Verify the derived release

From the repository root:

```text
python scripts/verify_release_candidate.py --root release_candidate_v0.2
```

This check is self-contained and verifies the release manifest, all included
file hashes, the strict census validation report, the static-only assertion and
the absence of raw source archives.

## Rebuild from reacquired archives

1. Reacquire the selected publication archives using
   `scripts/prefetch_selected_archives.py` and
   `data/census_frame_v0.2/archive_file_inventory.csv`.
2. Reacquire endpoint archives from the frozen ledger. This command never
   re-resolves current GitHub refs; it writes the exact filename expected by
   the validator and checks both byte size and SHA-256:

```text
python scripts/prefetch_endpoint_archives.py
```
3. Run the full archive-aware validator and deterministic rerun:

```text
python scripts/validate_census_dataset.py --strict --rerun
python scripts/independent_reconstruction_audit.py .
python scripts/summarize_census_dataset.py --summary-dir data/census_v0.2/summary_tables
python scripts/make_census_overview_figure.py
python -B -m pytest -q tests
```

The expected final counts are 345 projects, 690 snapshots, 9,074 dependency
records, 121,085 syntactic-interface records, 95,787 pair-delta records and
2,876 validation records. The expected endpoint accounting is 372 attempts,
327 stable releases, 18 default-branch endpoints and 27 unresolved endpoints.
The paired publication snapshots comprise 341 Zenodo records and 4 Figshare
records; all 345 endpoint snapshots use GitHub archives addressed by commit SHA.

## Reproducibility boundary

The pipeline is deterministic conditional on the frozen ledgers and immutable
archives. Reacquisition can fail because an upstream provider changes access;
that is why the release contains the original archive DOI/commit, provider
checksum, local SHA-256 and explicit unresolved states. A byte-identical rerun
is claimed only when the cached immutable inputs are available.

## Release metadata

`release_metadata_template.yml` must be completed with verified creators,
the formal data repository, persistent identifiers and licences before public
deposition. The verified GitHub repository and v0.2.1 release URL are already
filled. No DOI is inferred by the pipeline.

## Optional Word regeneration

With the bundled Pandoc binary (or an equivalent Pandoc 3.x installation), run
the following from `paper_rewriting_output/final_paper/` after compiling
`main.tex`:

```text
pandoc main.tex -o paper.docx --from latex --to docx --resource-path=. --extract-media=./media --number-sections --citeproc --bibliography=references.bib --csl=ieee.csl
python ../../scripts/prepare_word_output.py paper.docx paper.docx --width-inches 4.4
```

The preparation step preserves the figure, adds the package-wide PNG content
type required by strict OOXML validators and keeps the overview figure readable
with its caption in the Word layout. Run the Word guard and OOXML validator after
conversion.
