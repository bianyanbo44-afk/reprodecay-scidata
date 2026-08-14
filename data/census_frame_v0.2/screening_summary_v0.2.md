# Provisional census screening summary v0.2

## Boundary

This checkpoint covers metadata-level screening of the frozen 940-record JOSS
frame. It does not yet establish final archive-level eligibility. The evidence
surface was limited to the frozen Crossref snapshot, current JOSS article HTML
and current public GitHub repository HTML. Third-party software was never
installed, imported or executed.

## Results

| Status | Records | Share of frame |
|---|---:|---:|
| `eligible_metadata` | 396 | 42.13% |
| `excluded` | 537 | 57.13% |
| `unresolved` | 7 | 0.74% |
| Total | 940 | 100.00% |

The 537 metadata exclusions comprised 412 records without detected root-level
Python packaging evidence, 56 without a machine-confirmed repository licence,
37 with a non-GitHub software repository, 19 without a repository link and 13
whose current repository was identified as a fork. These are screening outcomes
under the frozen rule, not statements about software quality.

All seven unresolved records were manually checked against official JOSS,
GitHub and Crossref surfaces. Six retained JOSS paper pages whose declared
repositories returned HTTP 404 without redirect. One retained Crossref work
record whose DOI target and JOSS paper page returned HTTP 404. The records stay
`unresolved` rather than being imputed or silently excluded.

## Reproducibility evidence

- Frozen Crossref input SHA-256:
  `343eb098decc383477c33f8e7c5b9fa97e0b23c81e6410f85e7284a89cdb4ca9`
- Screening ledger SHA-256:
  `e13a4227d04ecb532625834eea0a126cd3ab6d13c34b738b41424c214f79fad5`
- Provisional eligible table SHA-256:
  `5e213ad33a505f5adb313fb82d51d3dc8d83c134b644680724788811be5e909a`
- Rank coverage: exactly 1 through 940, with 940 unique DOI and rank keys.
- A zero-new-record rerun left both CSV hashes unchanged.
- The validator and release audit passed.

## Next gate

The 396 metadata-eligible records require publication-archive resolution,
archive-level packaging and licence audit, immutable endpoint resolution and
static parsing before a final released census can be frozen.
