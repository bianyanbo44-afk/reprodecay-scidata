## Material Passport

- Origin Skill: experiment-agent
- Origin Mode: run
- Origin Date: 2026-08-13T10:03:19Z
- Verification Status: VERIFIED
- Version Label: exp_result_v1
- Upstream Dependencies: static_pair_pilot_v0.2_n20

## Experiment Result

- **ID**: census_screen_v0.2_n940
- **Type**: ETL
- **Status**: completed after user-authorized recovery
- **Command**: `python scripts\build_census_frame_v02.py --max-new 0 --delay 0.1 --workers 3 --retry-unresolved --progress-every 25`
- **Working Directory**: project root
- **Exit Code**: 0
- **Third-party Code Executed**: false

### Output Summary

| Item | Count or status |
|---|---:|
| Frozen frame | 940 |
| Formal ledger rows | 940 |
| Formal ledger rank range | 1-940 |
| Provisional eligible | 396 |
| Excluded | 537 |
| Unresolved | 7 |
| Transient unresolved | 0 |
| Unique DOI keys | 940 |
| Unique rank keys | 940 |
| Manually audited unresolved | 7 |
| Zero-new-record rerun | CSV hashes unchanged |

### Anomaly

The initial process stopped with `PermissionError [WinError 5]` while replacing
`screening_ledger.csv`. After explicit user approval, the writer gained bounded
retry handling for transient Windows sharing locks. The 356-row temporary file
was promoted only after it was verified as a unique, non-divergent superset of
the 355-row formal ledger. Screening then resumed at rank 357 and completed all
940 records. All seven unresolved records were manually checked against official
JOSS, GitHub and Crossref surfaces and retained as unresolved rather than imputed.

## Validation Report

- **Source**: census_screen_v0.2_n940
- **Overall Confidence**: SOLID for deterministic metadata-level screening;
  archive-level final eligibility remains pending.
- **Reproducibility Method**: structural validator plus zero-new-record rerun
- **Reproducibility Verdict**: REPRODUCIBLE
- **Ledger SHA-256**: `e13a4227d04ecb532625834eea0a126cd3ab6d13c34b738b41424c214f79fad5`
- **Provisional eligible SHA-256**: `5e213ad33a505f5adb313fb82d51d3dc8d83c134b644680724788811be5e909a`
