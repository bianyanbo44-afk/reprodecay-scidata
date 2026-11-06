# Publication-anchored paired snapshots of Python research software

Version `v0.3.0` links the source archive associated with a Journal of Open Source
Software (JOSS) article to the repository endpoint selected as of 14 August 2026.
The frame covers 1,671 articles published in 2018–2022 and yields 692 paired
Python research-software projects. Source evidence and observation status make
the field-level records reusable for studies of dependencies, syntactic
interfaces, packaging, CI and citation metadata after publication.

## Data object

The eight core CSV tables contain 692 projects, 1,384 snapshots, 27,764 dependency
records, 279,728 syntactic interface records, 11,167 declaration-source records,
214,440 pair-delta records and 5,550 validation records. Every comparison retains
its two values, source evidence, delta type and comparison status. Field-level
observation states distinguish confirmed absence from incomplete extraction.

## Package contents

- `data/census_v0.3/tables/` — eight core tables and their extraction manifest.
- `data/census_v0.3/summary_tables/` — summaries with explicit denominators,
  observation rules and publication-year strata.
- `data/census_v0.3/dictionary/` — human-readable field dictionary and
  Supplementary Table 1, including definitions, types, allowed values and examples.
- `data/census_v0.3/reference_audit/` — signed annotations, item-level comparisons
  and the findings log; third-party source exports are excluded.
- `data/census_v0.3/release_comparison/` — machine-readable comparison with v0.2.4.
- `data/census_frame_v0.3/` — screening, archive linkage, inventory, inspection
  and eligibility ledgers, retaining exclusions and unresolved cases.
- `data/candidate_frame_v0.3/` — frozen Crossref frame and acquisition metadata.
- `schema/` — machine-readable schemas; `docs/` — the extraction specification.
- `scripts/` and `tests/` — construction, audit, verification and regression code.
- `figures/census_v0.3/` — the four figures and their source-data exports.
- `CHANGES_v0.2.4_to_v0.3.0.md` — corrections on the original cohort, followed by
  the separate extension of the frame.

## Verification

From an extracted v0.3.0 release package, run:

```text
python scripts/verify_release_v03.py --root .
python -B -m pytest -q tests
```

The verifier checks every managed file hash, core-table counts and hashes,
schema coverage, validation results and the completed source-based audit. It
requires no source archive or network access. See `REPRODUCE.md` for regeneration
from retained archives and reacquisition under the original providers' terms.

## Validation and interpretation

The canonical validation passed and regeneration from retained input archives
reproduced the core tables byte for byte. Independent reconstruction covered
40 projects and 80 snapshots. The source-based audit covered 31 projects,
62 snapshots and 3,321 items, with all final items agreeing after correction.
The released audit documents the defects detected and the corrections made.

The static pipeline never installs, imports or executes third-party software.
Interface records describe syntax; dependency records describe declarations.
Observation status governs whether a recorded absence or zero is interpretable.
Dates, interval fields and year strata support comparisons across publication
cohorts with different lengths of exposure.

## Citation, access and licenses

The versioned data and code are preserved through the GitHub–Zenodo integration.
The archival record supplies the version DOI and anonymous download. The release
page links to that record and provides the complete derived-data archive:
https://github.com/bianyanbo44-afk/reprodecay-scidata/releases/tag/v0.3.0.
`CITATION.cff` supplies the author, title and version metadata. The public package
contains data, construction code, validation evidence and figures. Manuscript
and referee-response submission documents are maintained separately.

- Derived data — CC BY 4.0 (`LICENSE-DATA`).
- Custom code — MIT (`LICENSE-CODE`).
- Third-party source archives — reacquired from their providers; not redistributed.
