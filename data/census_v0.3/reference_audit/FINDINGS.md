# Reference-based audit: findings log

Each finding records what the annotation from source files expected, what the draft extractor produced, the cause, and the resolution. The final comparison is run against the release after all resolutions.

## Specification clarifications (no change in behaviour)

- C1. A conda-style specifier is the text after the package name with the operator included (`numpy=1.13.0=py35_0` gives `=1.13.0=py35_0`). Specification Section 3.1 now states this.
- C2. A PEP 440 specifier is written in canonical form, sorted and without spaces (`flit_core >=2,<4` gives `<4,>=2`). Specification Section 3.1 now states this.
- C3. Field names in `declaration_source_records.csv` are listed in a new Specification Section 4.1. The earlier text named the `setup.py` fields by call syntax.
- C4. A top-level `name["key"] = value` update applies to any name bound to a dict, not only to a `**mapping`, and a non-evaluable `value` makes the whole name non-evaluable (joss.02809, where `optional_dependencies["all"]` is built by a comprehension, so `extras_require` is `unsupported_syntax`). Specification Section 2.1 now says so; the extractor already behaved this way.
- C5. The rules for package source files and interface symbols were stated only in the code and in one sentence of the manuscript. They are now Specification Section 7, written from the code, so that API symbols can be annotated from the specification. No behaviour changed.

## Annotation-format corrections

- **A1. Extras and markers in annotated entries** (found in joss.03414). The first annotations recorded only group, name and specifier. The comparison rule (Specification Section 6), however, compares the set of {specifier, marker, extras, attributes}. In joss.03414 the endpoint declares `black` twice, once with the extra `jupyter`. The released row is correctly `changed`, while the classification derived from the incomplete annotation was `unchanged`. Annotated entries now carry extras and markers wherever they occur, and the classification derived from the annotations uses them. Table attributes are not annotated, so any discrepancy that arises from attributes alone is reviewed by hand.

## Findings

### F1. PEP 735 `[dependency-groups]` not covered

- **Where found.** joss.04448, endpoint snapshot.
- **Source.** The `pyproject.toml` declares the groups `test`, `lint`, `docs`, `pre-commit` and `dev` (the last composed of include-group entries) in a PEP 735 `[dependency-groups]` table.
- **Draft extractor.** The table was not a declaration source, so none of these dependencies were extracted. The snapshot was nevertheless reported as completely observed. This is exactly the silent-omission pattern the reviewer described.
- **Resolution.** Specification Section 2 and the extractor now read `dependency-groups.<name>` fields, with group names mapped as for Poetry groups. An include-group entry is recorded as a directive, because the included group is a field of its own. A regression test was added, the independent re-implementation was extended, and the form scan counts the table.
- **Release.** The released v0.3.0 records include these dependencies.

### F2. Declaration file stored as a link

- **Where found.** joss.01610, endpoint snapshot.
- **Source.** The root `requirements.txt` is a symbolic link to `requirements/base.txt`, which declares `attrs` and `typer[all]`.
- **Draft extractor.** Link members were listed and hashed but never read. The file was therefore reported as `parse_failure` with the detail `text_not_read_size_limit`. The snapshot was correctly marked incomplete, but the reason was wrong and the two dependencies were missing. A second defect showed up on inspection: in a zip archive a symbolic link stores its target path as member content, and the draft read that path as the file's text.
- **Resolution.** Specification Section 1 now states that a text member that is a link is read as the regular member it points to inside the same archive (chains of up to 8 links). A link that cannot be followed gets the detail `link_target_unresolved`. Both the extractor and the independent re-implementation were changed, a regression test covers tar symbolic links, tar hard links, zip symbolic links and a link that leaves the archive, and the form scan counts linked declaration files.
- **Extent.** A scan of all 1,384 snapshot archives found 432 link members in 107 snapshots. Of these, 81 are text members (50 snapshots, 30 projects). One is a root-level declaration file (this one), and 26 are Python modules in 9 snapshots. The two implementations read identical text for all 50 snapshots.
- **Release.** The released v0.3.0 records follow links as specified.

## Verification

All 62 annotation files were written as an AI-assisted first pass from the exported source files and the specification. Yanbo Bian checked the 709 annotated items in `reference_audit_verification.xlsx` against the source files and confirmed on 2026-11-02 that all were correct; the `verified` marks were entered on that confirmation and `reference_audit.py sign` recorded the verifier in every annotation file. The final comparison against the released v0.3.0 tables found no discrepancy (3,321 items).

## Annotation tooling

- T1. The helper that expands a compact list of entries split on every comma, which cut multi-clause specifiers such as `<3.0.0,>=1.26.0` into pieces (joss.01414 and joss.02178). The helper now splits on a comma followed by a space. The two affected annotation files were regenerated before any comparison was reported.
