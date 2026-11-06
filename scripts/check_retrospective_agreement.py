#!/usr/bin/env python3
"""Measure how often retrospective (as-of) endpoint resolution reproduces live resolution.

For every project resolved live in the v0.2 endpoint ledger, the endpoint policy
is re-applied retrospectively at ``--as-of`` and the selected commit is compared
with the live selection. Metadata only: nothing is downloaded or executed.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
from collections import Counter
from pathlib import Path

from resolve_endpoint_snapshots import resolve_via_github_api_as_of, stable_resolution_error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", default="data/census_v0.2/endpoint_resolution.csv")
    parser.add_argument("--candidates", default="data/census_frame_v0.2/provisional_eligible.csv")
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--delay", type=float, default=0.1)
    parser.add_argument("--attempts", type=int, default=3, help="tries per project before an error is recorded")
    parser.add_argument("--previous", default="", help="earlier output; only its error rows are re-queried")
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None
    candidates = {row["doi"]: row for row in csv.DictReader(open(args.candidates, encoding="utf-8-sig", newline=""))}
    live = [row for row in csv.DictReader(open(args.ledger, encoding="utf-8-sig", newline=""))
            if row["resolution_status"] == "resolved"]
    previous = {}
    if args.previous:
        previous = {row["project_id"]: row for row in csv.DictReader(open(args.previous, encoding="utf-8-sig", newline=""))}
    rows: list[dict[str, str]] = []
    for item in live:
        earlier = previous.get(item["project_id"])
        if earlier and not earlier["error"]:
            rows.append(earlier)
            continue
        candidate = dict(candidates[item["scholarly_doi"]])
        # Query the repository under the name it resolved to live, as a retrospective run would.
        candidate["repository_owner"], candidate["repository_name"] = item["resolved_full_name"].split("/", 1)
        for attempt in range(args.attempts):
            try:
                meta = resolve_via_github_api_as_of(candidate, token, args.as_of)
                sha, ref_type, ref_name, error = meta["endpoint_commit_sha"], meta["endpoint_ref_type"], meta["endpoint_ref_name"], ""
                break
            except Exception as exc:  # recorded, not raised: agreement is reported over all rows
                sha, ref_type, ref_name = "", "", ""
                error = f"{stable_resolution_error(exc)}:{type(exc).__name__}:{str(exc)[:120]}"
                time.sleep(5 * (attempt + 1))
        rows.append({
            "project_id": item["project_id"],
            "live_ref_type": item["endpoint_ref_type"],
            "live_ref_name": item["endpoint_ref_name"],
            "live_commit_sha": item["endpoint_commit_sha"],
            "retrospective_ref_type": ref_type,
            "retrospective_ref_name": ref_name,
            "retrospective_commit_sha": sha,
            "same_commit": str(sha == item["endpoint_commit_sha"]).lower(),
            "error": error,
        })
        time.sleep(args.delay)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "as_of": args.as_of,
        "live_resolved_projects": len(rows),
        "same_commit": sum(row["same_commit"] == "true" for row in rows),
        "different_commit": sum(row["same_commit"] == "false" and not row["error"] for row in rows),
        "retrospective_errors": dict(Counter(row["error"] for row in rows if row["error"])),
        "by_live_ref_type": {
            ref_type: {
                "projects": sum(row["live_ref_type"] == ref_type for row in rows),
                "same_commit": sum(row["live_ref_type"] == ref_type and row["same_commit"] == "true" for row in rows),
            }
            for ref_type in sorted({row["live_ref_type"] for row in rows})
        },
    }
    output.with_suffix(".json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
