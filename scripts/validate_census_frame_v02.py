#!/usr/bin/env python3
"""Validate the restart-safe provisional census frame."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from build_candidate_frame import normalized_doi, rank_key


ROOT = Path(__file__).resolve().parents[1]
CENSUS = ROOT / "data" / "census_frame_v0.2"
FROZEN_INPUT = ROOT / "data" / "candidate_frame_v0.1" / "crossref_joss_2018_2020.json"
ALLOWED_EXCLUSION_REASONS = {
    "license_not_machine_confirmed",
    "no_root_python_packaging_evidence",
    "no_repository_link",
    "non_github_host",
    "repository_is_fork",
    "no_default_branch",
}
ALLOWED_UNRESOLVED_REASONS = {
    "repository_or_article_not_found",
    "rate_limit_or_access",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []
    ledger = read_csv(CENSUS / "screening_ledger.csv")
    eligible = read_csv(CENSUS / "provisional_eligible.csv")
    manifest = json.loads((CENSUS / "screening_manifest.json").read_text(encoding="utf-8"))
    unresolved_audit = read_csv(CENSUS / "unresolved_manual_audit.csv")
    frozen = json.loads(FROZEN_INPUT.read_text(encoding="utf-8"))

    def check(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    ranks = [int(row["rank"]) for row in ledger]
    dois = [row["doi"] for row in ledger]
    check(len(frozen) == 940, f"frozen_frame_count:{len(frozen)}")
    check(len(ledger) == 940, f"ledger_count:{len(ledger)}")
    check(ranks == list(range(1, 941)), "rank_sequence_not_1_through_940")
    check(len(set(dois)) == len(dois), "duplicate_doi")
    check(all(normalized_doi(doi) == doi for doi in dois), "non_normalized_doi")
    check(all(row["selection_hash"] == rank_key(row["doi"]) for row in ledger), "selection_hash_mismatch")
    check(all("2018-01-01" <= row["publication_date"] <= "2020-12-31" for row in ledger), "date_outside_frame")
    check(all(row["screened_at_utc"] for row in ledger), "missing_screen_timestamp")

    for row in ledger:
        status = row["screen_status"]
        reason = row["reason_code"]
        if status == "eligible_metadata":
            check(not reason, f"eligible_has_reason:{row['doi']}")
            check(row["selected_role"] == "census_provisional", f"eligible_role:{row['doi']}")
            check(bool(row["repository_url"]), f"eligible_missing_repository:{row['doi']}")
            check(bool(row["github_python_packaging_evidence"]), f"eligible_missing_packaging:{row['doi']}")
            check(bool(row["github_license_spdx"]), f"eligible_missing_license:{row['doi']}")
            check(bool(row["github_default_branch"]), f"eligible_missing_branch:{row['doi']}")
        elif status == "excluded":
            check(reason in ALLOWED_EXCLUSION_REASONS, f"unknown_exclusion_reason:{row['doi']}:{reason}")
            check(not row["selected_role"], f"excluded_has_role:{row['doi']}")
        elif status == "unresolved":
            check(
                reason in ALLOWED_UNRESOLVED_REASONS
                or reason.startswith("network_or_parse_")
                or reason.startswith("http_5"),
                f"unknown_unresolved_reason:{row['doi']}:{reason}",
            )
            check(not row["selected_role"], f"unresolved_has_role:{row['doi']}")
        else:
            failures.append(f"unknown_status:{row['doi']}:{status}")

    expected_eligible = [row for row in ledger if row["screen_status"] == "eligible_metadata"]
    check(eligible == expected_eligible, "provisional_eligible_not_exact_ledger_subset")
    input_sha256 = hashlib.sha256(FROZEN_INPUT.read_bytes()).hexdigest()
    check(manifest.get("input_sha256") == input_sha256, "input_sha256_mismatch")
    check(manifest.get("records_screened") == len(ledger), "manifest_screened_count_mismatch")
    check(manifest.get("records_remaining") == 0, "manifest_records_remaining_nonzero")
    check(manifest.get("third_party_code_executed") is False, "unexpected_code_execution_flag")
    check(not (CENSUS / "screening_ledger.csv.tmp").exists(), "orphan_ledger_temporary")
    check(not (CENSUS / "screening_manifest.json.tmp").exists(), "orphan_manifest_temporary")

    unresolved = [row for row in ledger if row["screen_status"] == "unresolved"]
    transient = [row for row in unresolved if row["reason_code"] != "repository_or_article_not_found"]
    unresolved_dois = {row["doi"] for row in unresolved}
    audited_dois = {row["doi"] for row in unresolved_audit}
    check(len(unresolved_audit) == len(audited_dois), "duplicate_unresolved_audit_doi")
    check(audited_dois == unresolved_dois, "unresolved_audit_doi_set_mismatch")
    check(
        all(row["manual_outcome"] in {"unresolved_declared_repository_404", "unresolved_joss_page_404"} for row in unresolved_audit),
        "unknown_manual_unresolved_outcome",
    )
    check(all(row["evidence_checked_at_utc"] for row in unresolved_audit), "missing_unresolved_audit_timestamp")
    if unresolved:
        warnings.append(f"manually_confirmed_unresolved_records:{len(unresolved)}")
    if transient:
        warnings.append(f"transient_unresolved_records:{len(transient)}")
    warnings.append("metadata_eligibility_is_provisional_pending_archive_level_audit")

    result = {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "warnings": warnings,
        "counts": {
            "frame": len(frozen),
            "screened": len(ledger),
            "eligible_metadata": len(expected_eligible),
            "excluded": sum(row["screen_status"] == "excluded" for row in ledger),
            "unresolved": len(unresolved),
            "manual_unresolved_audit": len(unresolved_audit),
        },
        "ledger_sha256": hashlib.sha256((CENSUS / "screening_ledger.csv").read_bytes()).hexdigest(),
        "eligible_sha256": hashlib.sha256((CENSUS / "provisional_eligible.csv").read_bytes()).hexdigest(),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
