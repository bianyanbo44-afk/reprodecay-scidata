# Publication-archive eligibility policy v0.2

This rule is frozen before completion of the full archive inspection. It applies
to all 396 metadata-eligible JOSS records without project-specific exceptions.

A record is `eligible_archive` only when all of the following hold:

1. the publication-linked archive resolves to an immutable provider record whose
   publication date is not later than the JOSS publication date;
2. exactly one supported source archive is selected by the frozen v0.3.4 file
   rule and its provider checksum is present;
3. the complete selected archive passes checksum verification and static archive
   inspection, with no unsupported format or suspicious member path;
4. at least one root Python packaging artifact is present in the publication
   archive (`pyproject.toml`, `setup.py`, `setup.cfg`, `requirements.txt`,
   `Pipfile`, or a conda environment file); and
5. licence evidence exists either as provider metadata or as a root
   `LICENSE`/`LICENCE`/`COPYING` file.

Records that definitively fail items 2, 4, or 5 are `excluded_archive` with a
controlled reason. Records lacking a resolvable archive, a contemporaneous
archive version, a completed checksum/inspection, or an unambiguous safety
decision remain `unresolved_archive`; they are never silently promoted. Raw
access-state labels such as `open` are not licence evidence.

The ledger records every decision and evidence field. Third-party code is never
installed, imported, or executed.
