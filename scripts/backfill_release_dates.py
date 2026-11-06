#!/usr/bin/env python3
"""Backfill release identifiers and publication times for live-resolved release endpoints.

Endpoints resolved live through the GitHub web interface (resolution_method
github_web_git_refs) record the release tag and commit but not the release's
REST identifier or publication time, so their endpoint reference date is empty.
This script looks each release up by tag through the GitHub REST API, writes
every lookup to a provenance ledger, and fills the two columns in the endpoint
resolution ledger only when the release is published, is neither a draft nor
a prerelease, and was published no later than the recorded resolution time.
The selected commit and archive are never changed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from resolve_endpoint_snapshots import request_json  # noqa: E402

LEDGER_FIELDS = [
    "project_id", "resolved_full_name", "endpoint_ref_name", "resolved_at_utc", "release_id",
    "release_published_at", "release_created_at", "release_draft", "release_prerelease", "backfill_status",
    "fetched_at_utc",
]


def parse_utc(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def lookup(row: dict[str, str], token: str | None) -> dict[str, str]:
    tag = urllib.parse.quote(row["endpoint_ref_name"], safe="")
    release, _ = request_json(f"https://api.github.com/repos/{row['resolved_full_name']}/releases/tags/{tag}", token,
                              allow_404=True)
    entry = {"project_id": row["project_id"], "resolved_full_name": row["resolved_full_name"],
             "endpoint_ref_name": row["endpoint_ref_name"], "resolved_at_utc": row["resolved_at_utc"],
             "fetched_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
    if not isinstance(release, dict):
        return {**entry, "backfill_status": "release_not_found"}
    published = str(release.get("published_at") or "")
    entry.update({"release_id": str(release.get("id") or ""), "release_published_at": published,
                  "release_created_at": str(release.get("created_at") or ""),
                  "release_draft": str(bool(release.get("draft"))).lower(),
                  "release_prerelease": str(bool(release.get("prerelease"))).lower()})
    published_time, resolved_time = parse_utc(published), parse_utc(row["resolved_at_utc"])
    if release.get("draft") or release.get("prerelease") or published_time is None:
        status = "not_a_published_stable_release"
    elif resolved_time is None or published_time > resolved_time:
        status = "published_after_resolution"
    else:
        status = "filled"
    return {**entry, "backfill_status": status}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint-resolution", type=Path, default=Path("data/census_v0.3/endpoint_resolution.csv"))
    parser.add_argument("--ledger", type=Path, default=Path("data/census_v0.3/endpoint_release_date_backfill.csv"))
    parser.add_argument("--manifest", type=Path, default=Path("data/census_v0.3/endpoint_resolution_manifest.json"))
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None

    with args.endpoint_resolution.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    previous = {row["project_id"]: row for row in read_csv(args.ledger)} if args.ledger.exists() else {}
    targets = [
        row for row in rows
        if row["resolution_status"] == "resolved" and row["resolution_mode"] == "live"
        and row["endpoint_ref_type"] == "release" and not row["endpoint_release_published_at"]
    ]
    ledger = dict(previous)
    for index, row in enumerate(targets, 1):
        if row["project_id"] not in previous:
            ledger[row["project_id"]] = lookup(row, token)
        if index % 50 == 0:
            print(f"{index}/{len(targets)}", file=sys.stderr, flush=True)
    for row in rows:
        entry = ledger.get(row["project_id"])
        if entry and entry["backfill_status"] == "filled" and not row["endpoint_release_published_at"]:
            row["endpoint_release_id"] = entry["release_id"]
            row["endpoint_release_published_at"] = entry["release_published_at"]
    write_csv(args.ledger, [ledger[key] for key in sorted(ledger)], LEDGER_FIELDS)
    with args.endpoint_resolution.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    statuses: dict[str, int] = {}
    for entry in ledger.values():
        statuses[entry["backfill_status"]] = statuses.get(entry["backfill_status"], 0) + 1
    if args.manifest.exists():
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        manifest["endpoint_resolution_sha256"] = sha256_file(args.endpoint_resolution)
        fetched = sorted(entry["fetched_at_utc"] for entry in ledger.values())
        manifest["release_date_backfill"] = {
            "ledger": args.ledger.name, "ledger_sha256": sha256_file(args.ledger), "statuses": statuses,
            "fetched_from_utc": fetched[0] if fetched else "", "fetched_to_utc": fetched[-1] if fetched else "",
            "changed_columns": ["endpoint_release_id", "endpoint_release_published_at"],
        }
        args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print({"targets": len(targets), "statuses": statuses})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
