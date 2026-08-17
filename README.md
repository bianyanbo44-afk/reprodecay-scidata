# Publication-anchored paired snapshots of Python research software

Version `v0.2.4` is the data, code, figure and manuscript package for a
Scientific Data Data Descriptor. It links the immutable software archive
associated with a Journal of Open Source Software (JOSS) article to a later
immutable GitHub endpoint for 345 Python projects published in 2018-2020.

## Released data object

Each resolved project has one publication snapshot and one endpoint snapshot.
The seven linked core tables contain:

- 345 project pairs and 690 immutable snapshots;
- 9,074 declared-dependency records;
- 121,085 syntactic-interface records;
- 95,787 evidence-linked pair-delta records; and
- 2,876 validation records.

Selection, publication-archive and endpoint-resolution ledgers retain excluded
and unresolved records with reason codes. Static extraction never installs,
imports or executes indexed third-party software. Syntactic differences are not
runtime compatibility results, dependency declarations are not solved
environments, and the bounded JOSS/Python cohort is not a prevalence or
software-quality estimate.

## What is included

- `data/census_v0.2/tables/`: seven canonical CSV tables and extraction manifest
- `data/census_v0.2/summary_tables/`: denominator-aware descriptive summaries
- `data/census_frame_v0.2/`: frozen screening, archive and endpoint ledgers
- `schema/`: the data-package descriptor and controlled vocabularies
- `figures/census_v0.2/`: four publication figures and source-data CSV files
- `scripts/`: acquisition, extraction, audit, validation and figure code
- `tests/`: packaged unit and contract tests
- `manuscript/`: the exact LaTeX source, bibliography, figures and PDF
- `REPRODUCE.md`: verification and archive-reacquisition instructions

Raw Zenodo, Figshare and GitHub source archives are not redistributed. The
released snapshot records retain the archive DOI or immutable commit, source
URL, provider checksum where available, local SHA-256 and byte size needed to
reacquire and verify those inputs under the original providers' terms.

## Verify this release

After downloading and extracting the release ZIP, enter the extracted package
directory and run:

```text
cd release_candidate_v0.2
python scripts/verify_release_candidate.py --root .
python -B -m pytest -q tests
```

From a Git clone, run:

```text
python scripts/verify_release_candidate.py --root .
python -B -m pytest -q tests
```

The expected self-check result is `PASS` for 138 managed files. The reference
environment is Python 3.12.10; exact versions and a hash-pinned Windows lockfile
are included.

## Validation evidence

- strict schema, key, vocabulary, archive-hash, path-safety and no-execution checks;
- a byte-identical rerun of the canonical tables from cached immutable inputs;
- an independent 24-project, 48-snapshot semantic audit reproducing 22,065 API
  symbol keys, 658 dependency records and all audited structure values; and
- a separate 20-project reconstruction matching 40 snapshot hashes and locating
  38 sampled evidence paths.

These are bounded reconstruction and exact-agreement checks. They are not a
population parser-accuracy estimate or a runtime test.

## Access, citation and licences

The public GitHub repository and tagged release provide anonymous first-round
review access. A formal data-repository record and archived code DOI will be
added before the second review round; no DOI is inferred in this package.

- Derived tabular data: CC BY 4.0 (`LICENSE-DATA`)
- Custom code: MIT (`LICENSE-CODE`)
- Third-party source archives: not redistributed and not relicensed

Use `CITATION.cff` for versioned citation metadata. The repository is
https://github.com/bianyanbo44-afk/reprodecay-scidata and the release page is
https://github.com/bianyanbo44-afk/reprodecay-scidata/releases/tag/v0.2.4.
