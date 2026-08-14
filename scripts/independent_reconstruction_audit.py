#!/usr/bin/env python3
"""Independent provenance and evidence-path reconstruction audit.

The audit deliberately uses only the standard library and does not import the
project extractor. It re-identifies a deterministic 20-project sample from
the released tables, locates cached archives by size and SHA-256, and checks
that sampled delta evidence paths occur in the corresponding archive members.
It is a provenance/evidence spot-check, not a population accuracy estimate.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import posixpath
import re
import tarfile
import zipfile
from pathlib import Path


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk_size):
            digest.update(block)
    return digest.hexdigest()


def archive_members(path: Path) -> set[str]:
    if path.name.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            names = [item.replace("\\", "/") for item in archive.namelist()]
    else:
        with tarfile.open(path, "r:*") as archive:
            names = [item.name.replace("\\", "/") for item in archive.getmembers()]
    names = [n for n in names if n and not n.endswith("/")]
    parts = [n.split("/", 1) for n in names if "/" in n]
    if parts and all(p[0] == parts[0][0] for p in parts):
        wrapper = parts[0][0]
        names = [n[len(wrapper) + 1:] for n in names if n.startswith(wrapper + "/")]
    return {posixpath.normpath(n).lstrip("./") for n in names}


def evidence_paths(value: str) -> list[str]:
    if not value:
        return []
    candidates: list[str] = []
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        decoded = value
    if isinstance(decoded, list):
        for item in decoded:
            candidates.extend(evidence_paths(str(item)))
    elif isinstance(decoded, dict):
        for item in decoded.values():
            candidates.extend(evidence_paths(str(item)))
    else:
        text = str(decoded).replace("\\", "/")
        match = re.match(r"^(.*?):[0-9]+$", text)
        candidates.append(match.group(1) if match else text)
    return [posixpath.normpath(item).lstrip("./") for item in candidates if item]


def locate_archives(root: Path, snapshots: list[dict[str, str]]) -> dict[str, Path]:
    files = [p for base in (root / "data/raw").glob("*") if base.is_dir() for p in base.rglob("*") if p.is_file()]
    by_size: dict[int, list[Path]] = {}
    for path in files:
        by_size.setdefault(path.stat().st_size, []).append(path)
    located: dict[str, Path] = {}
    for row in snapshots:
        expected_size = int(row["archive_bytes"])
        expected_hash = row["archive_sha256"]
        preferred = [p for p in by_size.get(expected_size, []) if row.get("commit_sha") and row["commit_sha"][:16] in p.name]
        candidates = preferred + [p for p in by_size.get(expected_size, []) if p not in preferred]
        for path in candidates:
            if sha256(path) == expected_hash:
                located[row["snapshot_id"]] = path
                break
    return located


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--sample-size", type=int, default=20)
    args = parser.parse_args()
    root = args.root.resolve()
    data_root = root / "data/census_v0.2"
    projects = sorted(read_csv(data_root / "tables/project_records.csv"), key=lambda row: row["project_id"])
    step = max(1, len(projects) // args.sample_size)
    sampled = projects[::step][: args.sample_size]
    sampled_ids = {row["project_id"] for row in sampled}
    snapshots = [row for row in read_csv(data_root / "tables/snapshot_records.csv") if row["project_id"] in sampled_ids]
    located = locate_archives(root, snapshots)
    snapshot_status: list[dict[str, str]] = []
    member_cache: dict[str, set[str]] = {}
    for row in snapshots:
        path = located.get(row["snapshot_id"])
        status = "pass" if path else "fail"
        snapshot_status.append({
            "project_id": row["project_id"],
            "snapshot_id": row["snapshot_id"],
            "snapshot_role": row["snapshot_role"],
            "check": "archive_sha256_reconstruction",
            "status": status,
            "detail": str(path.relative_to(root)) if path else "no cached archive matched size and SHA-256",
        })
        if path:
            member_cache[row["snapshot_id"]] = archive_members(path)

    deltas = [row for row in read_csv(data_root / "tables/pair_delta_records.csv") if row["project_id"] in sampled_ids]
    evidence_status: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in deltas:
        # Structure scalar values such as ``packaging_files`` are JSON fields,
        # not source-member locators; API/dependency evidence is path-addressed.
        if row["family"] == "structure":
            continue
        for role, field in (("publication", "publication_evidence"), ("endpoint", "endpoint_evidence")):
            key = (row["pair_id"], role)
            if key in seen or not row[field]:
                continue
            snapshot_id = next((s["snapshot_id"] for s in snapshots if s["pair_id"] == row["pair_id"] and s["snapshot_role"] == role), None)
            if not snapshot_id or snapshot_id not in member_cache:
                continue
            paths = evidence_paths(row[field])
            if not paths:
                continue
            present = all(any(item == member or item.endswith("/" + member) for member in member_cache[snapshot_id]) for item in paths)
            evidence_status.append({
                "project_id": row["project_id"],
                "snapshot_id": snapshot_id,
                "snapshot_role": role,
                "check": "delta_evidence_member_reconstruction",
                "status": "pass" if present else "fail",
                "detail": f"{row['family']}:{row['feature_group']}:{row['feature_key']} -> {paths[0]}",
            })
            seen.add(key)
            if len(evidence_status) >= args.sample_size * 2:
                break
        if len(evidence_status) >= args.sample_size * 2:
            break

    rows = snapshot_status + evidence_status
    out_csv = data_root / "independent_reconstruction_audit.csv"
    with out_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["project_id", "snapshot_id", "snapshot_role", "check", "status", "detail"])
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "status": "PASS" if rows and all(row["status"] == "pass" for row in rows) else "FAIL",
        "sampled_projects": len(sampled),
        "snapshot_checks": len(snapshot_status),
        "snapshot_passes": sum(row["status"] == "pass" for row in snapshot_status),
        "evidence_checks": len(evidence_status),
        "evidence_passes": sum(row["status"] == "pass" for row in evidence_status),
        "scope": "deterministic provenance and evidence-path spot-check; not a population accuracy estimate",
        "output_csv": str(out_csv.relative_to(root)),
    }
    (data_root / "independent_reconstruction_audit.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
