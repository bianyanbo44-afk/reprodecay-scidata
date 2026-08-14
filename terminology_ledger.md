# Terminology Ledger

| Canonical term | Definition / use | Avoid |
|---|---|---|
| research software | Software produced for or used in scholarly research; operationalized here through the frozen JOSS frame | scientific code when the source population is broader than code snippets |
| publication-linked archive | Immutable Zenodo record referenced by the corresponding JOSS Crossref metadata | latest GitHub version |
| execution attempt | One revision under one fully specified environment condition | experiment when no scientific hypothesis test is meant |
| documented-current condition | Documented installation/test route with currently resolvable allowed dependencies | original environment |
| period-constrained condition | Dependency resolution limited by a preregistered historical cutoff where faithful artifacts exist | historical environment unless equivalence is demonstrated |
| bounded repair | Preregistered environment-only change followed by a fresh execution | fix, automatic fix, source-code repair |
| rebuildability | Ability to construct/install the software artifact under a specified environment | reproducibility when only installation is tested |
| testability | Ability to discover and execute the documented tests | correctness |
| execution outcome | Success, failure, timeout, policy-blocked or indeterminate result of an attempt | software quality |
| failure family | Evidence-grounded primary annotation from the frozen taxonomy | root cause unless causality is established |
| Technical Validation | Scientific Data section documenting dataset quality checks | Results |
| Data Records | Scientific Data section explaining deposited files, formats and fields | dataset results |

