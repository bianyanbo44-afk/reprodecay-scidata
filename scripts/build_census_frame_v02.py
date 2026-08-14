#!/usr/bin/env python3
"""Build a restart-safe provisional census ledger from the frozen JOSS frame.

The script reuses the already acquired Crossref JSON snapshot and screens only
public article/repository HTML. It never clones, imports, installs or executes
third-party software. Final eligibility remains provisional until archive-level
licence, packaging and publication-linkage checks are complete.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from build_candidate_frame import (
    RANK_SALT,
    ScreenRecord,
    fetch_github_web_metadata,
    github_parts,
    normalized_doi,
    publication_date,
    rank_key,
    repository_from_joss,
    safe_title,
)


def replace_with_retry(source: Path, destination: Path, attempts: int = 8) -> None:
    """Replace a checkpoint, retrying only transient Windows sharing locks."""
    for attempt in range(attempts):
        try:
            source.replace(destination)
            return
        except PermissionError:
            if attempt + 1 == attempts:
                raise
            time.sleep(min(0.05 * (2**attempt), 0.8))


def atomic_write_csv(path: Path, records: list[ScreenRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    fields = list(ScreenRecord.__dataclass_fields__)
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(asdict(record) for record in records)
    replace_with_retry(temporary, path)


def read_existing(path: Path) -> list[ScreenRecord]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [ScreenRecord(**row) for row in csv.DictReader(handle)]


def validate_unique(records: list[ScreenRecord], label: str) -> None:
    dois = [record.doi for record in records]
    ranks = [int(record.rank) for record in records]
    if len(dois) != len(set(dois)):
        raise ValueError(f"{label}_duplicate_doi")
    if len(ranks) != len(set(ranks)):
        raise ValueError(f"{label}_duplicate_rank")


def recover_temporary_checkpoint(path: Path) -> int:
    """Promote an intact temporary ledger only when it extends the formal one."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    if not temporary.exists():
        return 0
    formal = read_existing(path)
    candidate = read_existing(temporary)
    validate_unique(formal, "formal_checkpoint")
    validate_unique(candidate, "temporary_checkpoint")
    formal_by_doi = {record.doi: asdict(record) for record in formal}
    candidate_by_doi = {record.doi: asdict(record) for record in candidate}
    if len(candidate) <= len(formal):
        raise ValueError("temporary_checkpoint_not_newer")
    if any(candidate_by_doi.get(doi) != row for doi, row in formal_by_doi.items()):
        raise ValueError("temporary_checkpoint_diverges_from_formal")
    replace_with_retry(temporary, path)
    return len(candidate) - len(formal)


def screen(item: dict, rank: int) -> ScreenRecord:
    doi = normalized_doi(str(item.get("DOI", "")))
    record = ScreenRecord(
        rank=rank,
        selection_hash=rank_key(doi),
        doi=doi,
        title=safe_title(item),
        publication_date=publication_date(item),
        joss_url=f"https://joss.theoj.org/papers/{doi}",
        screened_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    try:
        repository_url, record.joss_url = repository_from_joss(doi)
        record.repository_url = repository_url
        if not repository_url:
            record.screen_status, record.reason_code = "excluded", "no_repository_link"
            return record
        parts = github_parts(repository_url)
        record.repository_host = repository_url.split("/", 3)[2].lower().removeprefix("www.")
        if not parts:
            record.screen_status, record.reason_code = "excluded", "non_github_host"
            return record
        record.repository_owner, record.repository_name = parts
        repo = fetch_github_web_metadata(repository_url)
        record.github_default_branch = str(repo.get("default_branch", ""))
        record.github_primary_language = str(repo.get("language") or "")
        record.github_python_packaging_evidence = str(repo.get("python_packaging_evidence") or "")
        record.github_license_spdx = str((repo.get("license") or {}).get("spdx_id") or "")
        record.github_archived = str(bool(repo.get("archived"))).lower()
        record.github_fork = str(bool(repo.get("fork"))).lower()
        record.github_created_at = str(repo.get("created_at") or "")
        if repo.get("fork"):
            record.screen_status, record.reason_code = "excluded", "repository_is_fork"
        elif not record.github_python_packaging_evidence:
            record.screen_status, record.reason_code = "excluded", "no_root_python_packaging_evidence"
        elif not record.github_license_spdx or record.github_license_spdx in {"NOASSERTION", "OTHER"}:
            record.screen_status, record.reason_code = "excluded", "license_not_machine_confirmed"
        elif not record.github_default_branch:
            record.screen_status, record.reason_code = "excluded", "no_default_branch"
        else:
            record.screen_status = "eligible_metadata"
            record.reason_code = ""
            record.selected_role = "census_provisional"
    except urllib.error.HTTPError as exc:
        record.screen_status = "unresolved"
        record.reason_code = (
            "repository_or_article_not_found" if exc.code == 404 else
            "rate_limit_or_access" if exc.code in {403, 429} else
            f"http_{exc.code}"
        )
    except Exception as exc:
        record.screen_status = "unresolved"
        record.reason_code = f"network_or_parse_{type(exc).__name__}"
    return record


def retryable(record: ScreenRecord) -> bool:
    return record.screen_status == "unresolved" and (
        record.reason_code == "rate_limit_or_access"
        or record.reason_code.startswith("network_or_parse_")
        or record.reason_code.startswith("http_5")
    )


def screen_with_delay(item: dict, rank: int, delay: float) -> ScreenRecord:
    result = screen(item, rank)
    time.sleep(max(delay, 0.0))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/candidate_frame_v0.1/crossref_joss_2018_2020.json")
    parser.add_argument("--output-dir", default="data/census_frame_v0.2")
    parser.add_argument("--max-new", type=int, default=0, help="Maximum new records; 0 means all remaining")
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=1)
    parser.add_argument("--retry-unresolved", action="store_true")
    parser.add_argument("--progress-every", type=int, default=25)
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    ledger_path = output_dir / "screening_ledger.csv"
    recovered_rows = recover_temporary_checkpoint(ledger_path)
    items = json.loads(input_path.read_text(encoding="utf-8"))
    ranked = sorted(items, key=lambda item: rank_key(str(item.get("DOI", ""))))
    existing = read_existing(ledger_path)
    by_doi = {record.doi: record for record in existing}
    started_count = len(by_doi)

    tasks: list[tuple[int, dict]] = []
    for rank, item in enumerate(ranked, start=1):
        doi = normalized_doi(str(item.get("DOI", "")))
        if doi in by_doi and not (args.retry_unresolved and retryable(by_doi[doi])):
            continue
        if args.max_new and len(tasks) >= args.max_new:
            break
        tasks.append((rank, item))

    if args.workers == 1:
        for completed_count, (rank, item) in enumerate(tasks, start=1):
            result = screen_with_delay(item, rank, args.delay)
            by_doi[result.doi] = result
            atomic_write_csv(ledger_path, sorted(by_doi.values(), key=lambda row: int(row.rank)))
            if args.progress_every and completed_count % args.progress_every == 0:
                print(json.dumps({"event": "progress", "records_screened": len(by_doi)}), flush=True)
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(screen_with_delay, item, rank, args.delay): rank
                for rank, item in tasks
            }
            for completed_count, future in enumerate(as_completed(futures), start=1):
                result = future.result()
                by_doi[result.doi] = result
                atomic_write_csv(ledger_path, sorted(by_doi.values(), key=lambda row: int(row.rank)))
                if args.progress_every and completed_count % args.progress_every == 0:
                    print(json.dumps({"event": "progress", "records_screened": len(by_doi)}), flush=True)

    records = sorted(by_doi.values(), key=lambda row: int(row.rank))
    atomic_write_csv(ledger_path, records)
    eligible = [row for row in records if row.screen_status == "eligible_metadata"]
    atomic_write_csv(output_dir / "provisional_eligible.csv", eligible)
    status_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    for row in records:
        status_counts[row.screen_status] = status_counts.get(row.screen_status, 0) + 1
        reason_counts[row.reason_code or "none"] = reason_counts.get(row.reason_code or "none", 0) + 1
    manifest = {
        "schema_version": "0.2",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_crossref_snapshot": str(input_path),
        "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "frame_records": len(ranked),
        "records_screened": len(records),
        "records_remaining": len(ranked) - len(records),
        "rank_method": "sha256(salt + '|' + normalized DOI)",
        "rank_salt": RANK_SALT,
        "screening_surface": "JOSS article HTML + public GitHub repository HTML",
        "eligibility_status": "provisional_metadata_only",
        "status_counts": status_counts,
        "reason_counts": reason_counts,
        "restart_safe": True,
        "workers": args.workers,
        "retry_unresolved": args.retry_unresolved,
        "recovered_temporary_rows": recovered_rows,
        "third_party_code_executed": False,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary = output_dir / "screening_manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    replace_with_retry(temporary, output_dir / "screening_manifest.json")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
