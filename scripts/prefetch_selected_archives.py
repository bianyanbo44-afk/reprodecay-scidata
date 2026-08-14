#!/usr/bin/env python3
"""Prefetch selected public archives with bounded concurrency and resume support."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from inspect_archives import download, md5_file, safe_cache_name, stable_error_code


def atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def fetch(row: dict[str, str], cache: Path) -> dict[str, str]:
    local = cache / safe_cache_name(row["archive_record_id"], row["file_key"])
    expected_size = int(row["file_size_bytes"])
    result = {
        "scholarly_doi": row["scholarly_doi"],
        "file_key": row["file_key"],
        "cache_name": local.name,
        "status": "",
        "error_code": "",
    }
    try:
        if local.exists() and local.stat().st_size != expected_size:
            local.unlink()
        if not local.exists():
            download(row["download_url"], local, expected_size)
        expected = row["repository_checksum"]
        if not expected.startswith("md5:"):
            raise ValueError("missing_or_unsupported_repository_checksum")
        if md5_file(local) != expected.split(":", 1)[1].lower():
            local.unlink(missing_ok=True)
            raise ValueError("repository_checksum_mismatch")
        result["status"] = "verified_cached"
    except Exception as exc:
        result["status"] = "prefetch_error"
        result["error_code"] = stable_error_code(exc)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", default="data/census_frame_v0.2/archive_file_inventory.csv")
    parser.add_argument("--cache", default="data/raw/zenodo_archives_v0.2")
    parser.add_argument("--checkpoint", default="data/census_frame_v0.2/archive_prefetch_checkpoint.json")
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=4)
    parser.add_argument("--max-files", type=int, default=0)
    args = parser.parse_args()

    inventory_path = Path(args.inventory)
    cache = Path(args.cache)
    checkpoint_path = Path(args.checkpoint)
    rows = [
        row for row in csv.DictReader(inventory_path.open(encoding="utf-8-sig", newline=""))
        if row["selected_source_archive"] == "true"
    ]
    rows.sort(key=lambda row: int(row["file_size_bytes"]))
    inventory_sha256 = hashlib.sha256(inventory_path.read_bytes()).hexdigest()
    state: dict[str, object] = {
        "schema_version": "0.2",
        "inventory_sha256": inventory_sha256,
        "workers": args.workers,
        "updated_at_utc": "",
        "results": {},
    }
    if checkpoint_path.exists():
        loaded = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if loaded.get("inventory_sha256") != inventory_sha256:
            raise ValueError("prefetch_checkpoint_inventory_mismatch")
        state = loaded
        state["workers"] = args.workers
    results = state["results"]
    assert isinstance(results, dict)
    pending = [row for row in rows if results.get(row["scholarly_doi"], {}).get("status") != "verified_cached"]
    if args.max_files:
        pending = pending[:args.max_files]

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(fetch, row, cache): row for row in pending}
        for completed, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            results[result["scholarly_doi"]] = result
            state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
            atomic_json(checkpoint_path, state)
            if completed % 10 == 0:
                print(json.dumps({"event": "progress", "completed_this_run": completed}), flush=True)

    verified = sum(item.get("status") == "verified_cached" for item in results.values())
    errors = sum(item.get("status") == "prefetch_error" for item in results.values())
    print(json.dumps({"selected": len(rows), "verified_cached": verified, "errors": errors}, indent=2))
    return 0 if verified == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
