#!/usr/bin/env python3
"""Reacquire endpoint archives from the frozen endpoint-resolution ledger."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from resolve_endpoint_snapshots import download_archive, sha256_file


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ledger_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def endpoint_target(cache: Path, row: dict[str, str]) -> Path:
    project_id = row.get("project_id", "")
    commit = row.get("endpoint_commit_sha", "")
    if not project_id.startswith("doi_10_21105_joss_"):
        raise ValueError("invalid_project_id")
    if len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit):
        raise ValueError("invalid_endpoint_commit_sha")
    return cache / f"{project_id}_{commit}.tar.gz"


def validate_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname != "codeload.github.com":
        raise ValueError("invalid_endpoint_archive_url")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("invalid_endpoint_archive_url")


def verify_file(path: Path, expected_bytes: int, expected_sha256: str) -> bool:
    return (
        path.is_file()
        and path.stat().st_size == expected_bytes
        and sha256_file(path) == expected_sha256
    )


def fetch(row: dict[str, str], cache: Path, verify_only: bool) -> dict[str, str]:
    result = {
        "project_id": row.get("project_id", ""),
        "scholarly_doi": row.get("scholarly_doi", ""),
        "status": "",
        "error_code": "",
        "cache_name": "",
    }
    try:
        if row.get("resolution_status") != "resolved":
            raise ValueError("ledger_row_not_resolved")
        expected_bytes = int(row.get("endpoint_archive_bytes", ""))
        expected_sha256 = row.get("endpoint_archive_sha256", "")
        if expected_bytes <= 0:
            raise ValueError("invalid_endpoint_archive_bytes")
        if len(expected_sha256) != 64 or any(char not in "0123456789abcdef" for char in expected_sha256):
            raise ValueError("invalid_endpoint_archive_sha256")
        url = row.get("endpoint_archive_url", "")
        validate_url(url)
        target = endpoint_target(cache, row)
        result["cache_name"] = target.name
        if verify_file(target, expected_bytes, expected_sha256):
            result["status"] = "verified_cached"
            return result
        if verify_only:
            result["status"] = "missing_or_invalid"
            result["error_code"] = "archive_not_verified"
            return result
        target.unlink(missing_ok=True)
        download_archive(url, target)
        if not verify_file(target, expected_bytes, expected_sha256):
            target.unlink(missing_ok=True)
            raise ValueError("downloaded_archive_verification_failed")
        result["status"] = "downloaded_verified"
    except Exception as exc:
        result["status"] = "prefetch_error"
        message = str(exc)
        result["error_code"] = message if message else type(exc).__name__
    return result


def write_checkpoint(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, default=Path("data/census_v0.2/endpoint_resolution.csv"))
    parser.add_argument("--cache", type=Path, default=Path("data/raw/endpoint_archives_v0.2"))
    parser.add_argument("--checkpoint", type=Path, default=Path("data/census_v0.2/endpoint_prefetch_checkpoint.json"))
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=4)
    parser.add_argument("--max-files", type=int, default=0)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    rows = list(csv.DictReader(args.ledger.open(encoding="utf-8-sig", newline="")))
    resolved = [row for row in rows if row.get("resolution_status") == "resolved"]
    resolved.sort(key=lambda row: int(row["selection_rank"]))
    if args.max_files:
        resolved = resolved[: args.max_files]
    args.cache.mkdir(parents=True, exist_ok=True)

    state: dict[str, object] = {
        "schema_version": "0.2",
        "ledger": args.ledger.as_posix(),
        "ledger_sha256": ledger_sha256(args.ledger),
        "cache_filename_template": "{project_id}_{endpoint_commit_sha}.tar.gz",
        "verify_only": args.verify_only,
        "updated_at_utc": "",
        "results": {},
    }
    results = state["results"]
    assert isinstance(results, dict)

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(fetch, row, args.cache, args.verify_only): row for row in resolved}
        for completed, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            results[result["project_id"]] = result
            state["updated_at_utc"] = utc_now()
            write_checkpoint(args.checkpoint, state)
            if completed % 25 == 0:
                print(json.dumps({"event": "progress", "completed": completed}), flush=True)

    verified = sum(
        item.get("status") in {"verified_cached", "downloaded_verified"}
        for item in results.values()
        if isinstance(item, dict)
    )
    errors = len(resolved) - verified
    summary = {
        "ledger_rows": len(rows),
        "resolved_selected": len(resolved),
        "verified": verified,
        "errors": errors,
        "checkpoint": args.checkpoint.as_posix(),
    }
    print(json.dumps(summary, indent=2))
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
