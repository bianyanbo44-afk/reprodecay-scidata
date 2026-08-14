#!/usr/bin/env python3
"""Create a static, non-executing eligibility audit for pilot candidates."""

from __future__ import annotations

import argparse
import csv
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


USER_AGENT = "ReproDecay-static-eligibility-audit/0.1"
INSTALL_PATTERNS = (
    r"pip install",
    r"conda install",
    r"python -m pip",
    r"poetry install",
    r"setup\.py install",
)
TEST_PATTERNS = (
    r"pytest",
    r"python -m unittest",
    r"tox",
    r"nox",
    r"make test",
)
RISK_PATTERNS = {
    "credentials": r"api[_ -]?key|access token|credentials|password",
    "external_service": r"external service|web service|server|database connection|remote api",
    "gpu": r"cuda|gpu|nvidia",
    "mpi_hpc": r"\bmpi\b|high[- ]performance computing|supercomputer",
    "proprietary": r"matlab|mathematica|proprietary|commercial licence|commercial license",
    "special_hardware": r"microscope|scanner|sensor|camera|instrument|hardware",
    "large_download": r"large dataset|download data|sample data|test data",
}


def request_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=45) as response:
        return response.read().decode("utf-8", errors="replace")


def request_json(url: str) -> Any:
    return json.loads(request_text(url))


def crossref_map(path: Path) -> dict[str, dict[str, Any]]:
    items = json.loads(path.read_text(encoding="utf-8"))
    return {str(item.get("DOI", "")).lower(): item for item in items}


def archive_dois(item: dict[str, Any]) -> list[str]:
    values: list[str] = []
    relations = item.get("relation") or {}
    for entries in relations.values():
        for entry in entries or []:
            if str(entry.get("id-type", "")).lower() == "doi":
                value = str(entry.get("id", "")).lower()
                value = value.removeprefix("https://doi.org/").removeprefix("http://dx.doi.org/")
                match = re.search(r"10\.5281/zenodo\.\d+", value, flags=re.IGNORECASE)
                if match:
                    values.append(match.group(0).lower())
    return list(dict.fromkeys(values))


def root_names(page: str) -> list[str]:
    names = re.findall(r'"(?:name|path)":"([^"/]+)"', page, flags=re.IGNORECASE)
    return sorted(set(names), key=str.lower)


def decode_embedded_readme(page: str) -> str:
    # GitHub embeds rendered README HTML in a JSON string. The extraction is a
    # heuristic signal only; URLs and the raw files remain the audit authority.
    match = re.search(r'"richText":"((?:\\.|[^"\\])*)"', page)
    if not match:
        return ""
    try:
        html = json.loads('"' + match.group(1) + '"')
    except json.JSONDecodeError:
        return ""
    html = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def evidence(patterns: tuple[str, ...], text: str) -> list[str]:
    return [pattern for pattern in patterns if re.search(pattern, text, flags=re.IGNORECASE)]


def risk_flags(text: str) -> list[str]:
    return [name for name, pattern in RISK_PATTERNS.items() if re.search(pattern, text, flags=re.IGNORECASE)]


def get_zenodo_record(doi: str) -> tuple[str, str, str, str]:
    match = re.search(r"10\.5281/zenodo\.(\d+)", doi, flags=re.IGNORECASE)
    if not match:
        return "", "", "", ""
    record_id = match.group(1)
    try:
        payload = request_json(f"https://zenodo.org/api/records/{record_id}")
    except (urllib.error.URLError, json.JSONDecodeError):
        return record_id, "", "", "unresolved"
    metadata = payload.get("metadata") or {}
    version = str(metadata.get("version") or "")
    publication_date = str(metadata.get("publication_date") or "")
    return record_id, version, publication_date, "resolved"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", default="data/candidate_frame_v0.1/pilot_candidates.csv")
    parser.add_argument("--crossref", default="data/candidate_frame_v0.1/crossref_joss_2018_2020.json")
    parser.add_argument("--output", default="data/candidate_frame_v0.1/static_eligibility_audit.csv")
    args = parser.parse_args()

    crossref = crossref_map(Path(args.crossref))
    candidates = list(csv.DictReader(Path(args.candidates).open(encoding="utf-8-sig", newline="")))
    rows: list[dict[str, str]] = []

    for candidate in candidates:
        doi = candidate["doi"].lower()
        repository_url = candidate["repository_url"].rstrip("/")
        branch = candidate["github_default_branch"]
        static_status = "review_required"
        static_reason = ""
        page = ""
        try:
            page = request_text(repository_url)
        except urllib.error.HTTPError as exc:
            static_status = "unresolved"
            static_reason = f"repository_http_{exc.code}"
        except urllib.error.URLError:
            static_status = "unresolved"
            static_reason = "repository_network_error"

        names = root_names(page) if page else []
        readme = decode_embedded_readme(page) if page else ""
        archives = archive_dois(crossref.get(doi, {}))
        archive_doi = archives[0] if archives else ""
        zenodo_id, archive_version, archive_date, archive_status = get_zenodo_record(archive_doi)
        install_hits = evidence(INSTALL_PATTERNS, readme)
        test_hits = evidence(TEST_PATTERNS, readme)
        risks = risk_flags(readme)

        licence_name = next((name for name in names if name.lower().startswith(("license", "licence", "copying"))), "")
        test_root = any(name.lower() in {"test", "tests", "testing", "tox.ini", "pytest.ini"} for name in names)
        ci_root = any(name.lower() in {".github", ".travis.yml", "appveyor.yml", "azure-pipelines.yml"} for name in names)

        if static_status != "unresolved":
            blocking = []
            if not archive_doi:
                blocking.append("no_archive_doi_in_crossref_relation")
            if not licence_name:
                blocking.append("licence_file_not_visible_at_root")
            if not install_hits:
                blocking.append("install_command_not_detected")
            if not branch:
                blocking.append("default_branch_missing")
            if blocking:
                static_status = "manual_block_check"
                static_reason = ";".join(blocking)
            else:
                static_status = "static_pass_manual_confirmation_required"

        rows.append(
            {
                "selected_role": candidate["selected_role"],
                "rank": candidate["rank"],
                "doi": doi,
                "title": candidate["title"],
                "repository_url": repository_url,
                "default_branch": branch,
                "licence_spdx_screen": candidate["github_license_spdx"],
                "licence_root_file": licence_name,
                "python_packaging_evidence": candidate["github_python_packaging_evidence"],
                "root_test_evidence": str(test_root).lower(),
                "root_ci_evidence": str(ci_root).lower(),
                "install_patterns": ";".join(install_hits),
                "test_patterns": ";".join(test_hits),
                "static_risk_flags": ";".join(risks),
                "archive_doi": archive_doi,
                "zenodo_record_id": zenodo_id,
                "archive_version": archive_version,
                "archive_publication_date": archive_date,
                "archive_resolution": archive_status,
                "static_status": static_status,
                "static_reason": static_reason,
                "manual_licence_confirmed": "",
                "manual_publication_revision_confirmed": "",
                "manual_install_route_confirmed": "",
                "manual_test_route_confirmed": "",
                "manual_safety_confirmed": "",
                "final_eligibility": "pending",
                "audited_at_utc": datetime.now(timezone.utc).isoformat(),
            }
        )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    summary: dict[str, int] = {}
    for row in rows:
        summary[row["static_status"]] = summary.get(row["static_status"], 0) + 1
    print(json.dumps({"records": len(rows), "static_status": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
