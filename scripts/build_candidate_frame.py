#!/usr/bin/env python3
"""Build a deterministic JOSS/Python pilot candidate frame.

This script performs metadata-only acquisition. It does not clone repositories,
install packages, or execute third-party code.

Sources:
  - Crossref journal works API for the frozen JOSS publication window.
  - JOSS article pages for the declared software-repository link.
  - GitHub repository API for primary language, licence, archival state and the
    immutable default-branch head observed at screening time.

The output records every screened DOI, including exclusions. Selection order is
defined by SHA-256 rather than Python's random module so it is stable across
Python versions.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


JOSS_ISSN = "2475-9066"
WINDOW_START = "2018-01-01"
WINDOW_END = "2020-12-31"
RANK_SALT = "reprodecay-pilot-v0.1-2026-08-13"
USER_AGENT = "ReproDecay-metadata-audit/0.1"
PRIMARY_TARGET = 20
REPLACEMENT_TARGET = 10


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                self.links.append(value)


@dataclass
class ScreenRecord:
    rank: int
    selection_hash: str
    doi: str
    title: str
    publication_date: str
    joss_url: str
    repository_url: str = ""
    repository_host: str = ""
    repository_owner: str = ""
    repository_name: str = ""
    github_node_id: str = ""
    github_default_branch: str = ""
    github_default_branch_sha: str = ""
    github_primary_language: str = ""
    github_python_packaging_evidence: str = ""
    github_license_spdx: str = ""
    github_archived: str = ""
    github_fork: str = ""
    github_created_at: str = ""
    github_updated_at: str = ""
    github_pushed_at: str = ""
    screen_status: str = ""
    reason_code: str = ""
    selected_role: str = ""
    screened_at_utc: str = ""


def request_bytes(url: str, token: str | None = None, retries: int = 2) -> tuple[bytes, dict[str, str]]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json, text/html;q=0.9, */*;q=0.8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=45) as response:
                response_headers = {k.lower(): v for k, v in response.headers.items()}
                return response.read(), response_headers
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    assert last_error is not None
    raise last_error


def request_json(url: str, token: str | None = None) -> tuple[Any, dict[str, str]]:
    raw, headers = request_bytes(url, token=token)
    return json.loads(raw.decode("utf-8")), headers


def normalized_doi(value: str) -> str:
    return value.strip().lower().removeprefix("https://doi.org/")


def publication_date(item: dict[str, Any]) -> str:
    parts = item.get("published", {}).get("date-parts", [[]])[0]
    if not parts:
        return ""
    year = int(parts[0])
    month = int(parts[1]) if len(parts) > 1 else 1
    day = int(parts[2]) if len(parts) > 2 else 1
    return f"{year:04d}-{month:02d}-{day:02d}"


def fetch_crossref_frame() -> tuple[list[dict[str, Any]], str]:
    params = {
        "filter": f"from-pub-date:{WINDOW_START},until-pub-date:{WINDOW_END}",
        "rows": "1000",
        "select": "DOI,title,published,URL,relation,resource,license",
    }
    url = f"https://api.crossref.org/journals/{JOSS_ISSN}/works?{urllib.parse.urlencode(params)}"
    payload, _ = request_json(url)
    items = payload["message"]["items"]
    return items, url


def rank_key(doi: str) -> str:
    return hashlib.sha256(f"{RANK_SALT}|{normalized_doi(doi)}".encode("utf-8")).hexdigest()


def repository_from_joss(doi: str) -> tuple[str, str]:
    joss_url = f"https://joss.theoj.org/papers/{urllib.parse.quote(doi, safe='/.')}"
    raw, _ = request_bytes(joss_url)
    parser = LinkParser()
    parser.feed(raw.decode("utf-8", errors="replace"))
    candidates: list[str] = []
    for href in parser.links:
        absolute = urllib.parse.urljoin(joss_url, href)
        parsed = urllib.parse.urlparse(absolute)
        host = parsed.netloc.lower().removeprefix("www.")
        path = parsed.path.strip("/")
        if host == "github.com":
            if path.startswith("openjournals/") or "/issues/" in parsed.path:
                continue
            if len(path.split("/")) == 2:
                candidates.append(f"https://github.com/{path}")
        elif host in {"gitlab.com", "bitbucket.org"}:
            if len(path.split("/")) >= 2:
                candidates.append(absolute.rstrip("/"))
    unique = list(dict.fromkeys(candidates))
    if not unique:
        return "", joss_url
    return unique[0], joss_url


def github_parts(repository_url: str) -> tuple[str, str] | None:
    parsed = urllib.parse.urlparse(repository_url)
    if parsed.netloc.lower().removeprefix("www.") != "github.com":
        return None
    parts = parsed.path.strip("/").removesuffix(".git").split("/")
    if len(parts) != 2:
        return None
    return parts[0], parts[1]


def fetch_github_repo(owner: str, name: str, token: str | None) -> tuple[dict[str, Any], dict[str, str]]:
    encoded = f"{urllib.parse.quote(owner)}/{urllib.parse.quote(name)}"
    return request_json(f"https://api.github.com/repos/{encoded}", token=token)


def fetch_github_web_metadata(repository_url: str) -> dict[str, Any]:
    """Read public repository metadata without executing repository code."""
    raw, _ = request_bytes(repository_url)
    page = raw.decode("utf-8", errors="replace")

    def first(pattern: str, default: str = "") -> str:
        match = re.search(pattern, page, flags=re.IGNORECASE)
        return match.group(1) if match else default

    packaging_files = []
    for filename in (
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "requirements.txt",
        "Pipfile",
        "environment.yml",
        "conda.yml",
    ):
        if re.search(rf'"(?:name|path)":"{re.escape(filename)}"', page, flags=re.IGNORECASE):
            packaging_files.append(filename)

    return {
        "node_id": "",
        "default_branch": first(r'"defaultBranch":"([^"]+)"'),
        "language": "Python-package-evidence" if packaging_files else "",
        "python_packaging_evidence": ";".join(packaging_files),
        "license": {"spdx_id": first(r'"license":\{"spdxId":"([^"]+)"')},
        "archived": first(r'"isArchived":(true|false)', "false").lower() == "true",
        "fork": first(r'"isFork":(true|false)', "false").lower() == "true",
        "created_at": first(r'"createdAt":"([^"]+)"'),
        "updated_at": "",
        "pushed_at": "",
    }


def fetch_default_branch_sha(owner: str, name: str, branch: str, token: str | None) -> str:
    encoded = f"{urllib.parse.quote(owner)}/{urllib.parse.quote(name)}/commits/{urllib.parse.quote(branch, safe='')}"
    payload, _ = request_json(f"https://api.github.com/repos/{encoded}", token=token)
    return str(payload.get("sha", ""))


def safe_title(item: dict[str, Any]) -> str:
    values = item.get("title") or []
    return str(values[0]) if values else ""


def write_csv(path: Path, records: list[ScreenRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(ScreenRecord.__dataclass_fields__)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow(asdict(record))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="data/candidate_frame")
    parser.add_argument("--primary", type=int, default=PRIMARY_TARGET)
    parser.add_argument("--replacements", type=int, default=REPLACEMENT_TARGET)
    parser.add_argument("--max-screen", type=int, default=120)
    parser.add_argument("--delay", type=float, default=0.25)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("GITHUB_TOKEN") or None
    started = datetime.now(timezone.utc).isoformat()

    items, crossref_url = fetch_crossref_frame()
    raw_path = output_dir / "crossref_joss_2018_2020.json"
    raw_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    ranked = sorted(items, key=lambda item: rank_key(str(item.get("DOI", ""))))
    records: list[ScreenRecord] = []
    selected_count = 0
    target_total = args.primary + args.replacements
    rate_limit_remaining = "unknown"

    for rank, item in enumerate(ranked, start=1):
        if len(records) >= args.max_screen or selected_count >= target_total:
            break
        doi = normalized_doi(str(item.get("DOI", "")))
        now = datetime.now(timezone.utc).isoformat()
        record = ScreenRecord(
            rank=rank,
            selection_hash=rank_key(doi),
            doi=doi,
            title=safe_title(item),
            publication_date=publication_date(item),
            joss_url=f"https://joss.theoj.org/papers/{doi}",
            screened_at_utc=now,
        )
        try:
            repository_url, joss_url = repository_from_joss(doi)
            record.repository_url = repository_url
            record.joss_url = joss_url
            if not repository_url:
                record.screen_status = "excluded"
                record.reason_code = "no_repository_link"
                records.append(record)
                continue
            parsed = urllib.parse.urlparse(repository_url)
            record.repository_host = parsed.netloc.lower().removeprefix("www.")
            parts = github_parts(repository_url)
            if not parts:
                record.screen_status = "excluded"
                record.reason_code = "pilot_non_github_host"
                records.append(record)
                continue
            owner, name = parts
            record.repository_owner = owner
            record.repository_name = name
            # Public HTML is the primary screening surface so unauthenticated
            # API limits do not change who enters the sample. API metadata may
            # be added later for the frozen eligible set.
            repo = fetch_github_web_metadata(repository_url)
            record.github_node_id = str(repo.get("node_id", ""))
            record.github_default_branch = str(repo.get("default_branch", ""))
            record.github_primary_language = str(repo.get("language") or "")
            record.github_python_packaging_evidence = str(repo.get("python_packaging_evidence") or "")
            license_obj = repo.get("license") or {}
            record.github_license_spdx = str(license_obj.get("spdx_id") or "")
            record.github_archived = str(bool(repo.get("archived"))).lower()
            record.github_fork = str(bool(repo.get("fork"))).lower()
            record.github_created_at = str(repo.get("created_at") or "")
            record.github_updated_at = str(repo.get("updated_at") or "")
            record.github_pushed_at = str(repo.get("pushed_at") or "")

            if repo.get("fork"):
                record.screen_status = "excluded"
                record.reason_code = "repository_is_fork"
            elif not record.github_python_packaging_evidence:
                record.screen_status = "excluded"
                record.reason_code = "no_root_python_packaging_evidence"
            elif not record.github_license_spdx or record.github_license_spdx in {"NOASSERTION", "OTHER"}:
                record.screen_status = "excluded"
                record.reason_code = "license_not_machine_confirmed"
            elif not record.github_default_branch:
                record.screen_status = "excluded"
                record.reason_code = "no_default_branch"
            else:
                # The exact revision used in execution is selected only after
                # manual publication-linkage review. Avoid spending a second
                # unauthenticated API request on a mutable branch head here.
                selected_count += 1
                record.screen_status = "eligible_metadata"
                record.reason_code = ""
                record.selected_role = "primary" if selected_count <= args.primary else "replacement"
        except urllib.error.HTTPError as exc:
            record.screen_status = "unresolved"
            if exc.code == 404:
                record.reason_code = "repository_or_article_not_found"
            elif exc.code in {403, 429}:
                record.reason_code = "api_rate_limit_or_access"
            else:
                record.reason_code = f"http_{exc.code}"
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            record.screen_status = "unresolved"
            record.reason_code = f"network_or_parse_{type(exc).__name__}"
        except Exception as exc:  # preserve the ledger, then expose the class
            record.screen_status = "unresolved"
            record.reason_code = f"unexpected_{type(exc).__name__}"
        records.append(record)
        # Checkpoint after every record so rate limits or interrupted network
        # calls cannot erase already completed screening work.
        write_csv(output_dir / "screening_ledger.csv", records)
        write_csv(
            output_dir / "pilot_candidates.csv",
            [candidate for candidate in records if candidate.screen_status == "eligible_metadata"],
        )
        time.sleep(max(args.delay, 0.0))

    write_csv(output_dir / "screening_ledger.csv", records)
    selected = [record for record in records if record.screen_status == "eligible_metadata"]
    write_csv(output_dir / "pilot_candidates.csv", selected)

    manifest = {
        "schema_version": "0.1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "started_at_utc": started,
        "metadata_only": True,
        "third_party_code_executed": False,
        "sampling_frame": {
            "journal": "Journal of Open Source Software",
            "issn": JOSS_ISSN,
            "publication_start": WINDOW_START,
            "publication_end": WINDOW_END,
            "crossref_query": crossref_url,
            "crossref_record_count": len(items),
            "rank_method": "sha256(salt + '|' + normalized DOI)",
            "rank_salt": RANK_SALT,
        },
        "screening": {
            "records_screened": len(records),
            "eligible_metadata": len(selected),
            "primary_target": args.primary,
            "replacement_target": args.replacements,
            "max_screen": args.max_screen,
            "github_token_present": bool(token),
            "github_screening_surface": "public_repository_html",
            "github_rate_limit_remaining_last_seen": rate_limit_remaining,
            "eligibility_is_provisional": True,
            "pending_manual_checks": [
                "licence text and scope",
                "installation instructions",
                "publication revision linkage",
                "non-interactive Linux feasibility",
                "safety and external-service requirements",
            ],
        },
        "files": {
            "raw_crossref": raw_path.name,
            "screening_ledger": "screening_ledger.csv",
            "pilot_candidates": "pilot_candidates.csv",
        },
    }
    (output_dir / "acquisition_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(json.dumps(manifest["screening"], indent=2))
    if len(selected) < target_total:
        print(
            f"Only {len(selected)} of {target_total} requested metadata-eligible records were found. "
            "Inspect the ledger; rerun with a GitHub token or after the rate-limit reset if needed.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
