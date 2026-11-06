#!/usr/bin/env python3
"""Build the field-level data dictionary (Supplementary Table 1) from the released tables.

Definitions, types, allowed values and the governing observation status are
written here by hand. A representative example and the observed value set of
each enumerated field are taken from the released tables, so that the
dictionary cannot drift from the data. Outputs: CSV, XLSX and Markdown.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

ENUM = "enum"
STATUS = "complete | incomplete"
DEP_STATUS = "complete | incomplete | no_declaration_file"
KEYS = "snapshot_id, project_id, snapshot_role: snapshot and project keys; snapshot_role is publication or endpoint"

# table -> [(field, type, definition, allowed values, governing status)]
DICTIONARY: dict[str, list[tuple[str, str, str, str, str]]] = {
    "project_records.csv": [
        ("project_id", "string", "Stable project identifier derived from the normalized JOSS DOI.", "doi_10_21105_joss_<number>", ""),
        ("pair_id", "string", "Identifier of the publication/endpoint pair: project_id plus 12-character prefixes of both archive SHA-256 values.", "", ""),
        ("scholarly_doi", "DOI", "Normalized (lower-case) JOSS article DOI.", "10.21105/joss.<number>", ""),
        ("title", "string", "Article title as supplied by Crossref.", "", ""),
        ("publication_date", "ISO date", "JOSS article publication date from Crossref.", "2018-01-01 to 2022-12-31", ""),
        ("publication_year", "integer", "Year of publication_date; the stratum used in publication-year summaries.", "2018-2022", ""),
        ("joss_url", "URL", "JOSS article page.", "", ""),
        ("input_repository_url", "URL", "Software repository URL declared on the JOSS article page.", "", ""),
        ("resolved_repository_url", "URL", "Canonical GitHub repository URL at endpoint resolution; differs from input_repository_url after a rename or transfer.", "", ""),
        ("selection_rank", "integer", "Position of the article in SHA-256 hash order of the frame. It fixed processing order only and is not a sampling weight or eligibility input.", "1-1671", ""),
        ("selected_role", ENUM, "Frame role of the project.", "census_provisional", ""),
        ("publication_archive_doi", "DOI", "DOI of the archive record linked to the JOSS article (the publication snapshot).", "Zenodo or Figshare DOI", ""),
        ("publication_archive_date", "ISO date", "Publication date of the archive record. The eligibility rule requires it to be no later than publication_date.", "", ""),
        ("publication_snapshot_id", "string", "Foreign key to the publication row of snapshot_records.", "", ""),
        ("endpoint_snapshot_id", "string", "Foreign key to the endpoint row of snapshot_records.", "", ""),
        ("endpoint_policy", ENUM, "Rule that selected the endpoint: the latest non-draft, non-prerelease GitHub release, otherwise the default-branch head.", "latest_stable_release_else_default_branch_head", ""),
        ("endpoint_as_of_date", "ISO date", "Date to which the endpoint policy refers.", "2026-08-14", ""),
        ("endpoint_resolution_mode", ENUM, "live: the policy was applied to the repository state on the endpoint date. retrospective_as_of: the policy was applied later to the repository history, restricted to releases and commits that existed at endpoint_as_of_date.", "live | retrospective_as_of", ""),
        ("endpoint_resolved_at_utc", "ISO datetime", "Time at which the endpoint was resolved (not the date of the selected release or commit).", "", ""),
        ("endpoint_ref_type", ENUM, "Kind of reference selected by the endpoint policy.", "release | default_branch", ""),
        ("endpoint_ref_name", "string", "Release tag or default-branch name.", "", ""),
        ("endpoint_commit_sha", "hex(40)", "Git commit of the endpoint archive.", "", ""),
        ("endpoint_release_published_at", "ISO datetime", "GitHub publication time of the selected release; empty for default-branch endpoints.", "", ""),
        ("endpoint_commit_time_utc", "ISO datetime", "Committer time of the endpoint commit, read from the uniform member timestamps that git archive writes into GitHub tarballs; empty when timestamps are not uniform.", "", ""),
        ("endpoint_ref_date", "ISO datetime", "Date of the endpoint state: endpoint_release_published_at for releases, endpoint_commit_time_utc for default-branch endpoints.", "", ""),
        ("archive_to_article_days", "integer", "Days from publication_archive_date to publication_date (non-negative by the eligibility rule).", ">= 0", ""),
        ("article_to_endpoint_ref_days", "integer", "Days from publication_date to endpoint_ref_date: the observed post-publication interval.", "", ""),
        ("article_to_endpoint_as_of_days", "integer", "Days from publication_date to endpoint_as_of_date: the maximum possible interval.", "", ""),
        ("repository_archived_at_endpoint", "boolean", "GitHub archived flag at endpoint resolution.", "true | false", ""),
        ("repository_license_spdx_at_endpoint", "SPDX id", "GitHub-reported license of the repository at endpoint resolution.", "", ""),
    ],
    "snapshot_records.csv": [
        ("snapshot_id", "string", "Snapshot identifier: project_id, role and the first 16 hex characters of archive_sha256.", "", ""),
        ("project_id", "string", "Foreign key to project_records.", "", ""),
        ("pair_id", "string", "Foreign key to project_records.", "", ""),
        ("snapshot_role", ENUM, "Role of the snapshot in the pair.", "publication | endpoint", ""),
        ("source_provider", ENUM, "Provider of the archive.", "zenodo | figshare | caltechdata | github", ""),
        ("source_identifier", "string", "Archive DOI (publication) or owner/repository (endpoint).", "", ""),
        ("source_ref", "string", "Archive file key (publication) or release tag or branch (endpoint).", "", ""),
        ("commit_sha", "hex(40) or empty", "Endpoint commit; empty for publication archives.", "", ""),
        ("source_url", "URL", "Public URL from which the archive can be reacquired.", "", ""),
        ("archive_bytes", "integer", "Size of the archive in bytes.", "", ""),
        ("archive_sha256", "hex(64)", "SHA-256 of the archive bytes.", "", ""),
        ("upstream_checksum", "string", "Provider-reported checksum (md5:<hex>) for publication archives; empty for endpoints.", "", ""),
        ("content_tree_sha256", "hex(64)", "SHA-256 over the sorted list of (member path, size, member SHA-256). Equal values mean the same files at the same paths even when the archive bytes differ, for example after a hosting service re-creates a tarball.", "", ""),
        ("content_member_count", "integer", "Number of member files covered by content_tree_sha256.", "", ""),
        ("member_mtime_utc_mode", "ISO datetime", "Most common member modification time in tar archives; empty for ZIP archives.", "", ""),
        ("member_mtime_uniform", "boolean or empty", "Whether all tar members share member_mtime_utc_mode.", "true | false | empty", ""),
        ("parser_version", "string", "Version of the static extractor.", "static-pair-extractor-0.3.0", ""),
        ("third_party_code_executed", "boolean", "Safety assertion.", "false", ""),
    ],
    "structure_records.csv": [
        ("snapshot_id, project_id, snapshot_role", "string", KEYS, "", ""),
        ("archive_type", ENUM, "Container format.", "zip | tar", "archive_listing_status"),
        ("archive_member_count", "integer", "Member files after removing a single wrapper directory and archive junk.", "", "archive_listing_status"),
        ("archive_bytes", "integer", "Archive size in bytes.", "", ""),
        ("archive_sha256", "hex(64)", "Verified archive hash.", "", ""),
        ("suspicious_member_count", "integer", "Members with absolute paths, '..' segments or drive prefixes; never extracted.", "", "archive_listing_status"),
        ("link_member_count", "integer", "Symbolic or hard links. A text member that is a link is read as the member it points to inside the same archive (specification Section 1); nothing outside the archive is read.", "", "archive_listing_status"),
        ("duplicate_normalized_member_count", "integer", "Path collisions after wrapper removal.", "", "archive_listing_status"),
        ("skipped_large_text_count", "integer", "Text members not read because of the 2 MiB per-file or 128 MiB total limit.", "", ""),
        ("packaging_files", "JSON array", "Root-level declaration files present (specification Section 2).", "", "archive_listing_status"),
        ("build_backends", "JSON array", "build-system.build-backend values in pyproject.toml.", "", "build_backend_observation_status"),
        ("python_constraints", "JSON array", "Declared Python-version constraints, each prefixed by its source file.", "", "python_constraint_observation_status"),
        ("declared_dependency_record_count", "integer", "Rows of dependency_records for the snapshot (including directives and unparsed rows). A measurement of extracted record volume.", "", "dependency_observation_status"),
        ("unparsed_dependency_record_count", "integer", "Dependency rows with parse_status unparsed.", "", "dependency_observation_status"),
        ("source_python_file_count", "integer", "Python files selected as package source by the source-root rule.", "", "archive_listing_status"),
        ("source_python_unread_file_count", "integer", "Selected package source files that could not be read within the size limits.", "", ""),
        ("source_python_line_count", "integer", "Physical lines in the selected package source files that were read.", "", "source_line_observation_status"),
        ("ast_parse_failure_count", "integer", "Selected source files that were unread or failed AST parsing.", "", ""),
        ("syntactic_api_symbol_count", "integer", "Rows of api_symbol_records for the snapshot.", "", "api_observation_status"),
        ("test_python_file_count", "integer", "Python files in test directories or named test_*.", "", "archive_listing_status"),
        ("ci_workflow_file_count", "integer", "YAML files under .github/workflows.", "", "archive_listing_status"),
        ("ci_providers", "JSON array", "CI systems detected from configuration file names.", "github_actions | travis | appveyor | azure_pipelines | circleci", "archive_listing_status"),
        ("citation_cff, codemeta, zenodo_metadata", "boolean", "Presence of CITATION.cff, codemeta.json or .zenodo.json at the root or under .github.", "true | false", "archive_listing_status"),
        ("contributing, code_of_conduct, security_policy, changelog", "boolean", "Presence of the corresponding governance or change-log file at the root or under .github.", "true | false", "archive_listing_status"),
        ("archive_listing_status", ENUM, "Whether the member listing is complete (always complete for extracted snapshots).", "complete", ""),
        ("dependency_observation_status", ENUM, "complete: every declaration field was extracted or confirmed absent; incomplete: at least one field is partial, dynamic, unsupported or failed; no_declaration_file: no supported declaration file at the root.", DEP_STATUS, ""),
        ("dependency_incomplete_reasons", "JSON array", "source_file:field:status for each field that makes the dependency observation incomplete.", "", ""),
        ("python_constraint_observation_status", ENUM, "complete when every field that can carry a Python constraint was extracted or confirmed absent and no declaration file failed to parse.", STATUS, ""),
        ("build_backend_observation_status", ENUM, "complete when there is no root pyproject.toml or it was parsed.", STATUS, ""),
        ("source_line_observation_status", ENUM, "complete when every selected package source file was read.", STATUS, ""),
        ("api_observation_status", ENUM, "complete when every selected package source file was read and parsed.", STATUS, ""),
        ("parser_version", "string", "Extractor version.", "static-pair-extractor-0.3.0", ""),
        ("third_party_code_executed", "boolean", "Safety assertion.", "false", ""),
    ],
    "declaration_source_records.csv": [
        ("snapshot_id, project_id, snapshot_role", "string", KEYS, "", ""),
        ("source_file", "path", "Root-level declaration file (or '(file)' rows refer to the whole file).", "", ""),
        ("source_kind", ENUM, "Declaration format.", "pyproject | pep517 | pep621 | pep735 | poetry | poetry_dev | poetry_group | flit | setuptools_dynamic | setup_cfg | setup_py | requirements | conda_environment | pipfile", ""),
        ("field", "string", "Declaration field, named by its path in the file (specification Section 2); '(file)' for file-level failures; 'setup(**...)' for an unresolvable keyword mapping.", "", ""),
        ("field_kind", ENUM, "What the field carries.", "dependency | python_constraint | file | setup_kwargs", ""),
        ("observation_status", ENUM, "Observation status of the field (specification Section 4).", "extracted | extracted_partial | confirmed_absent | dynamic_metadata | unsupported_syntax | parse_failure", ""),
        ("entry_count", "integer", "Dependency rows produced by the field.", "", ""),
        ("unparsed_entry_count", "integer", "Of entry_count, rows with parse_status unparsed.", "", ""),
        ("detail", "string", "Reason or source text for non-extracted statuses (for example the unresolved name, the file: reference, text_not_read_size_limit or link_target_unresolved).", "", ""),
    ],
    "dependency_records.csv": [
        ("snapshot_id, project_id, snapshot_role", "string", KEYS, "", ""),
        ("source_file", "path", "File containing the declaration (an included requirements file for source_kind requirements_include).", "", ""),
        ("source_kind", ENUM, "Declaration format.", "pep517 | pep621 | pep735 | poetry | poetry_dev | poetry_group | flit | setup_cfg | setup_py | requirements | requirements_include | conda_environment | conda_pip | pipfile", ""),
        ("dependency_group", "string", "Declared role of the dependency.", "runtime | build | test | development | optional:<extra> | group:<name>", ""),
        ("normalized_name", "string", "PEP 503 canonical package name; empty for directives and unparsed rows.", "", ""),
        ("raw_declaration", "string", "Declaration as written in the source; table entries are rendered as '<name> = <value as JSON>'.", "", ""),
        ("specifier", "string", "Version constraint in the source syntax (PEP 440, Poetry or conda); empty when unconstrained.", "", ""),
        ("marker", "string", "PEP 508 environment marker, or the Poetry markers value.", "", ""),
        ("extras", "string", "Requested extras, sorted and joined by ';'.", "", ""),
        ("attributes", "JSON object", "Other keys of a Poetry or Pipfile table entry (for example optional, python, git, path, source, or a list of alternative constraints).", "", ""),
        ("parse_status", ENUM, "How the declaration was parsed (specification Section 3).", "parsed | parsed_poetry | parsed_pipfile | parsed_non_pep508 | parsed_url | directive | unparsed", ""),
        ("parse_error", "string", "Parser error for unparsed rows.", "", ""),
    ],
    "api_symbol_records.csv": [
        ("snapshot_id, project_id, snapshot_role", "string", KEYS, "", ""),
        ("symbol_key", "string", "Comparison key <module>|<symbol_kind>|<qualified_name>.", "", ""),
        ("source_file", "path", "Python source file.", "", ""),
        ("module", "string", "Dotted module name derived from the path.", "", ""),
        ("symbol_kind", ENUM, "Kind of definition.", "class | constructor | method | function | async_function", ""),
        ("qualified_name", "string", "Top-level name or Class.member.", "", ""),
        ("signature", "string", "Argument list as written (classes: JSON list of base classes); variants joined by ' || '.", "", ""),
        ("decorators", "JSON array", "Decorators as written.", "", ""),
        ("export_evidence", ENUM, "Why the symbol is considered public.", "literal_all | lexical_nonprivate | class_member", ""),
        ("line_start, line_end", "integer", "Source location of the first variant.", "", ""),
        ("variant_count", "integer", "Definitions with the same key (for example conditional definitions).", "", ""),
    ],
    "pair_delta_records.csv": [
        ("pair_id, project_id", "string", "Pair and project keys.", "", ""),
        ("family", ENUM, "Compared family.", "dependency | api | structure", ""),
        ("feature_group", "string", "Sub-family.", "declared_dependency | syntactic_symbol | packaging_files | build_backends | python_constraints | ci_providers | scalar", ""),
        ("feature_key", "string", "Compared item: <dependency_group>|<normalized_name>, the API symbol_key, a list element or a structure field name.", "", ""),
        ("publication_value, endpoint_value", "string or JSON", "Value on each side (dependencies: sorted set of {specifier, marker, extras, attributes}); empty when the key is absent on that side.", "", ""),
        ("delta_type", ENUM, "added: absent in the publication snapshot; removed: absent at the endpoint; changed: present on both sides with different values; unchanged: equal values.", "added | removed | changed | unchanged", ""),
        ("comparison_status", ENUM, "comparable when the governing observation status is complete in both snapshots; incomplete_observation otherwise, in which case added, removed or changed may reflect a change in what could be observed.", "comparable | incomplete_observation", ""),
        ("publication_evidence, endpoint_evidence", "string or JSON", "Source evidence for each side (file:line or the declarations behind the value).", "", ""),
    ],
    "validation_records.csv": [
        ("snapshot_id, project_id, snapshot_role", "string", KEYS, "", ""),
        ("check_name", "string", "Validation check.", "archive_sha256 | archive_paths | declaration_observation | api_source_read | python_ast_parse | python_ast_warning", ""),
        ("status", ENUM, "Outcome.", "pass | warning | error", ""),
        ("evidence", "string", "Checked locator (snapshot, file or file:line).", "", ""),
        ("detail", "string", "Hash, count or message.", "", ""),
    ],
}


def read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tables", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    tables = Path(args.tables)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    missing: list[str] = []
    for table, entries in DICTIONARY.items():
        data = read(tables / table)
        columns = list(data[0]) if data else []
        documented = {name.strip() for field, *_ in entries for name in field.split(",")}
        missing += [f"{table}:{column}" for column in columns if column not in documented]
        for field, kind, definition, allowed, governing in entries:
            first = field.split(",")[0].strip()
            values = [row.get(first, "") for row in data]
            example = next((value for value in values if value not in {"", "[]", "{}"}), "")
            observed = ""
            if kind == ENUM:
                counts = Counter(values)
                observed = json.dumps(dict(sorted(counts.items())), ensure_ascii=False)
            rows.append({
                "table": table,
                "field": field,
                "type": kind,
                "definition": definition,
                "allowed_values": allowed,
                "observed_value_counts": observed,
                "example": example[:160],
                "missing_value_meaning": "empty string: not available or not applicable; never an imputed zero or false",
                "governing_observation_status": governing,
            })
    if missing:
        raise SystemExit("undocumented columns: " + ", ".join(missing))
    with (out / "data_dictionary_v0.3.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font
        book = Workbook()
        sheet = book.active
        sheet.title = "Supplementary Table 1"
        sheet.append(list(rows[0]))
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for row in rows:
            sheet.append(list(row.values()))
        for column, width in zip("ABCDEFGHI", (24, 30, 13, 70, 40, 40, 40, 30, 30)):
            sheet.column_dimensions[column].width = width
        for line in sheet.iter_rows(min_row=2):
            for cell in line:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
        sheet.freeze_panes = "A2"
        book.save(out / "Supplementary_Table_1_data_dictionary.xlsx")
    except ImportError:
        print("openpyxl unavailable; XLSX not written")
    lines = ["# Data dictionary v0.3", "",
             "Generated by scripts/build_data_dictionary.py from the released tables. Empty strings mean not available "
             "or not applicable. A count, list or boolean governed by an observation status is a measurement only when "
             "that status is complete.", ""]
    for table in DICTIONARY:
        lines += [f"## `{table}`", "", "| Field | Type | Definition | Allowed values | Example | Governing status |", "|---|---|---|---|---|---|"]
        for row in rows:
            if row["table"] == table:
                cells = [row[k].replace("|", "\\|").replace("\n", " ") for k in ("field", "type", "definition", "allowed_values", "example", "governing_observation_status")]
                lines.append("| " + " | ".join(f"`{cells[0]}`" if i == 0 else c for i, c in enumerate(cells)) + " |")
        lines.append("")
    (out / "data_dictionary_v0.3.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"fields": len(rows), "tables": len(DICTIONARY)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
