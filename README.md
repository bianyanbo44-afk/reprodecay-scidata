# Version-paired research-software evolution dataset v0.2

This release candidate contains the derived data, provenance ledgers, schemas,
validation outputs, source data for the overview figure, manuscript source and
reproduction code for the Scientific Data Data Descriptor:

**A version-paired dataset of dependency and interface evolution in Python
research software**

Repository: https://github.com/bianyanbo44-afk/reprodecay-scidata  
Versioned release: https://github.com/bianyanbo44-afk/reprodecay-scidata/releases/tag/v0.2.1

## Frozen data object

- 345 projects and 690 immutable source snapshots;
- 341 Zenodo and 4 Figshare publication snapshots;
- 345 GitHub endpoint snapshots: 327 stable releases and 18 default-branch
  commits;
- 9,074 dependency records, 121,085 syntactic-interface records, 95,787
  pair-delta records and 2,876 validation records;
- 27 endpoint-unresolved projects retained in the ledgers with reason codes.

All final project rows have `selected_role=census_provisional`. Extraction is
static: indexed third-party software was not installed, imported or executed.

## Verify this package

From the extracted package directory, run:

```text
python scripts/verify_release_candidate.py --root .
python -B -m pytest -q tests
```

`verify_release_candidate.py` is the authoritative self-check for this derived
release candidate. It verifies the package manifest and file hashes, required
files, frozen record counts, provider/role accounting, strict-validation status,
the no-execution assertion and the absence of raw source archives.

`scripts/build_release_audit.py` is a maintainer audit for the complete source
workspace and expects development and pilot artifacts that are intentionally not
included here. It is not the candidate-package self-check.

## Package layout

- `data/census_v0.2/tables/`: seven core CSV tables and extraction manifest;
- `data/census_v0.2/summary_tables/`: derived descriptive summaries;
- `data/census_frame_v0.2/`: screening and publication-archive ledgers;
- `figures/census_v0.2/`: overview figure PDF, SVG, high-resolution PNG and
  source-data CSV (the redundant workspace TIFF is not copied into this
  candidate);
- `schema/`: schemas and controlled vocabularies;
- `scripts/` and `tests/`: construction, validation and test code;
- `manuscript/`: LaTeX source, bibliography, figure and compiled PDF;
- `REPRODUCE.md`: archive-aware reconstruction protocol.
- `LICENSE-DATA`, `LICENSE-CODE` and `LICENSES.md`: explicit data, code and
  third-party rights boundaries;
- `requirements-lock-win-py312.txt` and `environment_reference.json`: the
  hash-pinned reference environment and external-tool versions.

## Archive and rights boundary

The package does not redistribute the approximately 10.9 GiB of third-party
Zenodo, Figshare or GitHub source archives. Released rows retain public archive
DOIs or immutable commits, provider checksums and locally computed SHA-256 values
for reacquisition under the original providers' terms.

This GitHub release is the versioned code and derived-data release candidate;
the formal dataset repository record and DOI are still pending. Data and code
licences are fixed as CC BY 4.0 and MIT, respectively. The two author names, Beijing Jiaotong
University affiliation, public institutional emails, no-external-funding
statement and no-competing-interests declaration are confirmed. Before public
dataset deposition, CRediT roles, any ORCIDs, the dataset DOI, a code-archive
DOI and the final anonymous-review access URL still require author confirmation. Yanbo Bian and
Zhihai Wang are designated as joint corresponding authors.
