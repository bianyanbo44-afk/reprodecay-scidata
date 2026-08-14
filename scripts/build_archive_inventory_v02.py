#!/usr/bin/env python3
"""Build a restart-safe publication-archive inventory for census candidates.

Only public repository metadata are requested.  The script does not download
archive contents, import candidate packages, install dependencies, or execute
third-party code.  Provider responses are normalized into one project ledger
and one file inventory; an atomic JSON checkpoint is the recovery authority.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import http.client
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from build_census_frame_v02 import replace_with_retry


USER_AGENT = "ReproDecay-publication-archive-inventory/0.2"
RESOLUTION_POLICY_VERSION = "archive-resolution-v0.3.4"
DOI_PATTERN = re.compile(r"10\.\d{4,9}/[^\s<>\"']+", flags=re.IGNORECASE)
SOURCE_SUFFIX_PRIORITY = (
    (".tar.gz", 0),
    (".tgz", 0),
    (".tar.bz2", 1),
    (".tar.xz", 1),
    (".tar", 2),
    (".zip", 3),
)
NON_SOURCE_HINTS = ("paper", "manuscript", "documentation", "docs", "dataset", "results", "supplement")


@dataclass
class ArchiveFileRecord:
    rank: str
    selected_role: str
    scholarly_doi: str
    provider: str
    archive_doi_declared: str
    archive_doi_resolved: str
    archive_record_id: str
    file_key: str
    file_size_bytes: str
    repository_checksum: str
    download_url: str
    mimetype: str
    source_format_priority: str
    source_name_penalty: str
    selected_source_archive: str


@dataclass
class ArchiveLinkRecord:
    rank: str
    selected_role: str
    scholarly_doi: str
    joss_title: str
    joss_publication_date: str
    repository_url: str
    archive_relation_type: str = ""
    archive_doi_declared: str = ""
    archive_doi_normalized: str = ""
    archive_provider: str = ""
    archive_record_id: str = ""
    archive_concept_doi: str = ""
    archive_version: str = ""
    archive_publication_date: str = ""
    archive_title: str = ""
    archive_license: str = ""
    archive_access_right: str = ""
    archive_file_count: str = "0"
    source_archive_candidate_count: str = "0"
    total_archive_size_bytes: str = "0"
    selected_file_key: str = ""
    selected_file_size_bytes: str = ""
    selected_repository_checksum: str = ""
    selected_download_url: str = ""
    publication_date_delta_days: str = ""
    inventory_status: str = ""
    reason_code: str = ""
    inventoried_at_utc: str = ""
    third_party_code_executed: str = "false"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_doi(value: str) -> str:
    """Return a bare, lower-case DOI while removing common citation debris."""
    cleaned = urllib.parse.unquote(str(value or "")).strip()
    cleaned = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", cleaned, flags=re.IGNORECASE)
    match = DOI_PATTERN.search(cleaned)
    if not match:
        return ""
    return match.group(0).rstrip(".,;:)]}>\u201d\u2019'").lower()


def provider_for_doi(doi: str) -> str:
    value = normalize_doi(doi)
    if re.fullmatch(r"10\.5281/zenodo\.\d+", value):
        return "zenodo"
    if re.fullmatch(r"10\.6084/m9\.figshare\.\d+(?:\.v\d+)?", value):
        return "figshare"
    if re.fullmatch(r"10\.22002/d1\.\d+", value):
        return "caltechdata"
    return ""


def archive_reference(item: dict[str, Any]) -> tuple[str, str]:
    """Choose the publication-linked software archive DOI deterministically."""
    relation_priority = {
        "references": 0,
        "is-supplemented-by": 1,
        "has-part": 2,
        "is-part-of": 3,
    }
    candidates: list[tuple[int, str, str]] = []
    for relation, entries in (item.get("relation") or {}).items():
        for entry in entries or []:
            if str(entry.get("id-type", "")).lower() != "doi":
                continue
            declared = str(entry.get("id") or "")
            doi = normalize_doi(declared)
            if provider_for_doi(doi):
                candidates.append((relation_priority.get(str(relation).lower(), 50), str(relation), declared))
    if not candidates:
        return "", ""
    _, relation, declared = sorted(candidates, key=lambda value: (value[0], normalize_doi(value[2])))[0]
    return relation, declared


def request_json(url: str, attempts: int = 3) -> Any:
    """Request JSON with bounded retries for transient transport failures only."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt + 1 == attempts:
                raise
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionResetError,
            http.client.RemoteDisconnected,
            json.JSONDecodeError,
        ):
            if attempt + 1 == attempts:
                raise
        time.sleep(min(0.5 * (2**attempt), 4.0))
    raise RuntimeError("unreachable_request_state")


def source_priority(file_key: str) -> tuple[int, int] | None:
    lower = file_key.lower()
    if lower.endswith((".whl", ".exe", ".dmg", ".msi")):
        return None
    for suffix, priority in SOURCE_SUFFIX_PRIORITY:
        if lower.endswith(suffix):
            penalty = int(any(hint in Path(lower).name for hint in NON_SOURCE_HINTS))
            return priority, penalty
    return None


def version_tokens(value: str) -> set[str]:
    normalized = str(value or "").lower().strip()
    if not normalized:
        return set()
    tokens = {normalized, normalized.lstrip("v")}
    return {token for token in tokens if token}


def file_matches_version(file_key: str, archive_version: str) -> bool:
    name = Path(file_key).name.lower()
    return any(
        re.search(rf"(?:^|[-_.])v?{re.escape(token)}(?:[-_.]|$)", name)
        for token in version_tokens(archive_version)
    )


def select_source_file(files: list[ArchiveFileRecord], archive_version: str = "") -> ArchiveFileRecord | None:
    candidates = [record for record in files if record.source_format_priority]
    if not candidates:
        return None
    version_matched = [record for record in candidates if file_matches_version(record.file_key, archive_version)]
    if version_matched:
        candidates = version_matched
    return sorted(
        candidates,
        key=lambda record: (
            int(record.source_name_penalty),
            int(record.source_format_priority),
            record.file_key.lower(),
            int(record.file_size_bytes or 0),
        ),
    )[0]


def days_between(first: str, second: str) -> str:
    try:
        return str((date.fromisoformat(first[:10]) - date.fromisoformat(second[:10])).days)
    except (TypeError, ValueError):
        return ""


def license_text(value: Any) -> str:
    """Normalize provider-specific licence metadata without treating it as proof."""
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("id", "name", "title", "value"):
            if key in value:
                return license_text(value[key])
        for key in ("en", "default", "und"):
            if key in value:
                return license_text(value[key])
        return ""
    if isinstance(value, list):
        return ";".join(filter(None, (license_text(item) for item in value)))
    return str(value)


def normalized_file(
    link: ArchiveLinkRecord,
    *,
    key: str,
    size: Any,
    checksum: str,
    download_url: str,
    mimetype: str = "",
) -> ArchiveFileRecord:
    priority = source_priority(key)
    return ArchiveFileRecord(
        rank=link.rank,
        selected_role=link.selected_role,
        scholarly_doi=link.scholarly_doi,
        provider=link.archive_provider,
        archive_doi_declared=link.archive_doi_declared,
        archive_doi_resolved=link.archive_doi_normalized,
        archive_record_id=link.archive_record_id,
        file_key=key,
        file_size_bytes=str(size or 0),
        repository_checksum=checksum,
        download_url=download_url,
        mimetype=mimetype,
        source_format_priority=str(priority[0]) if priority else "",
        source_name_penalty=str(priority[1]) if priority else "",
        selected_source_archive="false",
    )


def zenodo_versions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    url = str((payload.get("links") or {}).get("versions") or "")
    if not url:
        url = f"https://zenodo.org/api/records/{payload.get('id', '')}/versions"
    versions: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    while url and url not in seen_urls:
        seen_urls.add(url)
        page = request_json(url)
        versions.extend(((page.get("hits") or {}).get("hits") or []))
        url = str((page.get("links") or {}).get("next") or "")
    return versions


def select_zenodo_version(
    payload: dict[str, Any], joss_date: str, requested_doi: str
) -> dict[str, Any]:
    """Select the latest immutable version not later than the JOSS date."""
    payload_doi = normalize_doi(str(payload.get("doi") or ""))
    concept_doi = normalize_doi(str(payload.get("conceptdoi") or ""))
    requested_doi = normalize_doi(requested_doi)
    if requested_doi == payload_doi and requested_doi != concept_doi:
        return payload
    if not concept_doi or requested_doi != concept_doi:
        raise LookupError("zenodo_declared_doi_did_not_match_record_or_concept")
    target = date.fromisoformat(joss_date[:10])
    family = zenodo_versions(payload)
    if not family:
        family = [payload]
    viable: list[tuple[date, int, dict[str, Any]]] = []
    for version in family:
        publication = str((version.get("metadata") or {}).get("publication_date") or "")[:10]
        try:
            published = date.fromisoformat(publication)
        except ValueError:
            continue
        if published <= target:
            viable.append((published, int(version.get("id") or 0), version))
    if not viable:
        raise LookupError("no_zenodo_version_on_or_before_joss_date")
    selected = sorted(viable, key=lambda value: (value[0], value[1]))[-1][2]
    selected_id = str(selected.get("id") or "")
    if selected_id and selected_id != str(payload.get("id") or ""):
        selected = request_json(f"https://zenodo.org/api/records/{selected_id}")
    return selected


def resolve_zenodo(link: ArchiveLinkRecord) -> list[ArchiveFileRecord]:
    match = re.fullmatch(r"10\.5281/zenodo\.(\d+)", link.archive_doi_normalized)
    if not match:
        raise ValueError("invalid_zenodo_doi")
    record_id = match.group(1)
    requested_doi = link.archive_doi_normalized
    payload = request_json(f"https://zenodo.org/api/records/{record_id}")
    payload = select_zenodo_version(payload, link.joss_publication_date, requested_doi)
    metadata = payload.get("metadata") or {}
    link.archive_record_id = str(payload.get("id") or record_id)
    link.archive_doi_normalized = normalize_doi(str(payload.get("doi") or link.archive_doi_normalized))
    link.archive_concept_doi = normalize_doi(str(payload.get("conceptdoi") or ""))
    link.archive_version = str(metadata.get("version") or "")
    link.archive_publication_date = str(metadata.get("publication_date") or "")
    link.archive_title = str(metadata.get("title") or "")
    link.archive_license = license_text(metadata.get("license") or "")
    link.archive_access_right = license_text(metadata.get("access_right") or "")
    result = []
    for item in payload.get("files") or []:
        result.append(
            normalized_file(
                link,
                key=str(item.get("key") or ""),
                size=item.get("size"),
                checksum=str(item.get("checksum") or ""),
                download_url=str((item.get("links") or {}).get("self") or ""),
            )
        )
    return result


def figshare_id_and_version(doi: str) -> tuple[str, str]:
    match = re.fullmatch(r"10\.6084/m9\.figshare\.(\d+)(?:\.v(\d+))?", normalize_doi(doi))
    if not match:
        raise ValueError("invalid_figshare_doi")
    return match.group(1), match.group(2) or ""


def choose_figshare_version(article_id: str, declared_version: str, joss_date: str) -> str:
    if declared_version:
        return declared_version
    versions = request_json(f"https://api.figshare.com/v2/articles/{article_id}/versions")
    viable: list[tuple[date, int]] = []
    for entry in versions or []:
        version = int(entry.get("version"))
        payload = request_json(f"https://api.figshare.com/v2/articles/{article_id}/versions/{version}")
        published = date.fromisoformat(str(payload.get("published_date"))[:10])
        if not joss_date or published <= date.fromisoformat(joss_date[:10]):
            viable.append((published, version))
    if not viable:
        raise LookupError("no_figshare_version_on_or_before_joss_date")
    return str(sorted(viable)[-1][1])


def resolve_figshare(link: ArchiveLinkRecord) -> list[ArchiveFileRecord]:
    article_id, declared_version = figshare_id_and_version(link.archive_doi_normalized)
    selected_version = choose_figshare_version(article_id, declared_version, link.joss_publication_date)
    payload = request_json(f"https://api.figshare.com/v2/articles/{article_id}/versions/{selected_version}")
    link.archive_record_id = f"{article_id}:v{selected_version}"
    link.archive_doi_normalized = normalize_doi(str(payload.get("doi") or link.archive_doi_normalized))
    link.archive_concept_doi = re.sub(r"\.v\d+$", "", link.archive_doi_normalized)
    link.archive_version = str(payload.get("version") or selected_version)
    link.archive_publication_date = str(payload.get("published_date") or "")[:10]
    link.archive_title = str(payload.get("title") or "")
    link.archive_license = license_text(payload.get("license") or "")
    link.archive_access_right = license_text(payload.get("access_right") or "")
    result = []
    for item in payload.get("files") or []:
        checksum = str(item.get("computed_md5") or item.get("supplied_md5") or "")
        if checksum and ":" not in checksum:
            checksum = f"md5:{checksum}"
        result.append(
            normalized_file(
                link,
                key=str(item.get("name") or ""),
                size=item.get("size"),
                checksum=checksum,
                download_url=str(item.get("download_url") or ""),
                mimetype=str(item.get("mimetype") or ""),
            )
        )
    return result


def resolve_caltechdata(link: ArchiveLinkRecord) -> list[ArchiveFileRecord]:
    query = urllib.parse.quote(f'pids.doi.identifier:"{link.archive_doi_normalized}"')
    search = request_json(f"https://data.caltech.edu/api/records?q={query}")
    hits = ((search.get("hits") or {}).get("hits") or [])
    exact = [
        hit for hit in hits
        if normalize_doi(str((((hit.get("pids") or {}).get("doi") or {}).get("identifier") or "")))
        == link.archive_doi_normalized
    ]
    if not exact:
        raise LookupError("caltechdata_record_not_found")
    payload = request_json(str((exact[0].get("links") or {}).get("self") or ""))
    metadata = payload.get("metadata") or {}
    link.archive_record_id = str(payload.get("id") or "")
    resolved_doi = str((((payload.get("pids") or {}).get("doi") or {}).get("identifier") or ""))
    link.archive_doi_normalized = normalize_doi(resolved_doi) or link.archive_doi_normalized
    link.archive_version = str(metadata.get("version") or "")
    link.archive_publication_date = str(metadata.get("publication_date") or "")
    link.archive_title = str(metadata.get("title") or "")
    link.archive_license = license_text(metadata.get("rights") or "")
    file_entries = payload.get("files") or {}
    if isinstance(file_entries, dict) and isinstance(file_entries.get("entries"), dict):
        values = list(file_entries["entries"].values())
    elif isinstance(file_entries, dict):
        values = [value for value in file_entries.values() if isinstance(value, dict)]
    else:
        values = [value for value in file_entries if isinstance(value, dict)]
    result = []
    for item in values:
        result.append(
            normalized_file(
                link,
                key=str(item.get("key") or ""),
                size=item.get("size"),
                checksum=str(item.get("checksum") or ""),
                download_url=str((item.get("links") or {}).get("content") or ""),
                mimetype=str(item.get("mimetype") or ""),
            )
        )
    return result


def resolve_candidate(candidate: dict[str, str], item: dict[str, Any], delay: float) -> dict[str, Any]:
    link = ArchiveLinkRecord(
        rank=candidate["rank"],
        selected_role=candidate.get("selected_role", ""),
        scholarly_doi=candidate["doi"].lower(),
        joss_title=candidate["title"],
        joss_publication_date=candidate["publication_date"],
        repository_url=candidate["repository_url"],
        inventoried_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    files: list[ArchiveFileRecord] = []
    relation, declared = archive_reference(item)
    link.archive_relation_type = relation
    link.archive_doi_declared = declared
    link.archive_doi_normalized = normalize_doi(declared)
    link.archive_provider = provider_for_doi(link.archive_doi_normalized)
    if not link.archive_doi_normalized:
        link.inventory_status = "no_external_archive"
        link.reason_code = "no_supported_publication_archive_doi"
        return {"linkage": asdict(link), "files": []}
    try:
        if link.archive_provider == "zenodo":
            files = resolve_zenodo(link)
        elif link.archive_provider == "figshare":
            files = resolve_figshare(link)
        elif link.archive_provider == "caltechdata":
            files = resolve_caltechdata(link)
        else:
            link.inventory_status = "unsupported_provider"
            link.reason_code = "archive_provider_not_supported"
            return {"linkage": asdict(link), "files": []}
        publication_delta = days_between(link.archive_publication_date, link.joss_publication_date)
        if publication_delta and int(publication_delta) > 0:
            raise LookupError("archive_publication_after_joss_date")
        selected = select_source_file(files, link.archive_version)
        if selected:
            selected.selected_source_archive = "true"
            link.selected_file_key = selected.file_key
            link.selected_file_size_bytes = selected.file_size_bytes
            link.selected_repository_checksum = selected.repository_checksum
            link.selected_download_url = selected.download_url
            link.inventory_status = "resolved_source_archive"
        else:
            link.inventory_status = "resolved_no_source_archive"
            link.reason_code = "no_supported_source_archive_file"
        link.archive_file_count = str(len(files))
        link.source_archive_candidate_count = str(sum(bool(file.source_format_priority) for file in files))
        link.total_archive_size_bytes = str(sum(int(file.file_size_bytes or 0) for file in files))
        link.publication_date_delta_days = publication_delta
    except urllib.error.HTTPError as exc:
        link.inventory_status = "unresolved"
        link.reason_code = f"provider_http_{exc.code}"
    except (
        urllib.error.URLError,
        TimeoutError,
        ConnectionResetError,
        http.client.RemoteDisconnected,
        json.JSONDecodeError,
    ) as exc:
        link.inventory_status = "unresolved"
        link.reason_code = f"provider_transport_{type(exc).__name__}"
    except Exception as exc:
        link.inventory_status = "unresolved"
        link.reason_code = f"provider_parse_{type(exc).__name__}:{str(exc)[:120]}"
    finally:
        time.sleep(max(delay, 0.0))
    return {"linkage": asdict(link), "files": [asdict(file) for file in files]}


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    replace_with_retry(temporary, path)


def atomic_write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    replace_with_retry(temporary, path)


def terminal(result: dict[str, Any]) -> bool:
    status = result.get("linkage", {}).get("inventory_status", "")
    return status in {
        "resolved_source_archive",
        "resolved_no_source_archive",
        "no_external_archive",
        "unsupported_provider",
    }


def build_manifest(
    *,
    output_dir: Path,
    candidates: list[dict[str, str]],
    state: dict[str, Any],
    input_sha: str,
    crossref_sha: str,
    workers: int,
    retry_unresolved: bool,
    complete: bool,
) -> dict[str, Any]:
    linkages, files = write_outputs(output_dir, state)
    status_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    for row in linkages:
        status_counts[row["inventory_status"]] = status_counts.get(row["inventory_status"], 0) + 1
        reason_counts[row["reason_code"] or "none"] = reason_counts.get(row["reason_code"] or "none", 0) + 1
    selected = [row for row in linkages if row["inventory_status"] == "resolved_source_archive"]
    return {
        "schema_version": "0.2",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_state": "complete" if complete else "in_progress",
        "candidate_records": len(candidates),
        "projects_inventoried": len(linkages),
        "projects_remaining": len(candidates) - len(linkages),
        "file_records": len(files),
        "selected_source_archives": len(selected),
        "selected_source_bytes": sum(int(row["selected_file_size_bytes"] or 0) for row in selected),
        "status_counts": status_counts,
        "reason_counts": reason_counts,
        "providers": sorted({row["archive_provider"] for row in linkages if row["archive_provider"]}),
        "candidate_sha256": input_sha,
        "crossref_sha256": crossref_sha,
        "resolution_policy_version": RESOLUTION_POLICY_VERSION,
        "linkage_ledger_sha256": sha256_file(output_dir / "archive_linkage_ledger.csv"),
        "file_inventory_sha256": sha256_file(output_dir / "archive_file_inventory.csv"),
        "restart_safe": True,
        "workers": workers,
        "retry_unresolved": retry_unresolved,
        "third_party_code_executed": False,
    }


def write_outputs(output_dir: Path, state: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    ordered = sorted(state["results"].values(), key=lambda result: int(result["linkage"]["rank"]))
    linkages = [result["linkage"] for result in ordered]
    files = [file for result in ordered for file in result["files"]]
    files.sort(key=lambda row: (int(row["rank"]), row["file_key"].lower()))
    atomic_write_csv(output_dir / "archive_linkage_ledger.csv", linkages, list(ArchiveLinkRecord.__dataclass_fields__))
    atomic_write_csv(output_dir / "archive_file_inventory.csv", files, list(ArchiveFileRecord.__dataclass_fields__))
    return linkages, files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", default="data/census_frame_v0.2/provisional_eligible.csv")
    parser.add_argument("--crossref", default="data/candidate_frame_v0.1/crossref_joss_2018_2020.json")
    parser.add_argument("--output-dir", default="data/census_frame_v0.2")
    parser.add_argument("--max-new", type=int, default=0, help="Maximum new/retried projects; 0 means all")
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=1)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--retry-unresolved", action="store_true")
    parser.add_argument("--progress-every", type=int, default=25)
    args = parser.parse_args()

    candidates_path = Path(args.candidates)
    crossref_path = Path(args.crossref)
    output_dir = Path(args.output_dir)
    checkpoint_path = output_dir / "archive_inventory_checkpoint.json"
    candidates = list(csv.DictReader(candidates_path.open(encoding="utf-8-sig", newline="")))
    candidates.sort(key=lambda row: int(row["rank"]))
    items = json.loads(crossref_path.read_text(encoding="utf-8"))
    crossref = {normalize_doi(str(item.get("DOI") or "")): item for item in items}
    input_sha = sha256_file(candidates_path)

    if checkpoint_path.exists():
        state = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if state.get("candidate_sha256") != input_sha:
            raise ValueError("archive_inventory_checkpoint_input_mismatch")
        if state.get("crossref_sha256") != sha256_file(crossref_path):
            raise ValueError("archive_inventory_checkpoint_crossref_mismatch")
        if state.get("resolution_policy_version") != RESOLUTION_POLICY_VERSION:
            raise ValueError("archive_inventory_checkpoint_policy_mismatch")
    else:
        state = {
            "schema_version": "0.2",
            "candidate_sha256": input_sha,
            "crossref_sha256": sha256_file(crossref_path),
            "resolution_policy_version": RESOLUTION_POLICY_VERSION,
            "results": {},
        }

    tasks: list[dict[str, str]] = []
    for candidate in candidates:
        existing = state["results"].get(candidate["doi"].lower())
        if existing and (terminal(existing) or not args.retry_unresolved):
            continue
        if args.max_new and len(tasks) >= args.max_new:
            break
        tasks.append(candidate)

    def commit(result: dict[str, Any], completed: int) -> None:
        doi = result["linkage"]["scholarly_doi"]
        state["results"][doi] = result
        atomic_write_json(checkpoint_path, state)
        manifest = build_manifest(
            output_dir=output_dir,
            candidates=candidates,
            state=state,
            input_sha=input_sha,
            crossref_sha=sha256_file(crossref_path),
            workers=args.workers,
            retry_unresolved=args.retry_unresolved,
            complete=False,
        )
        atomic_write_json(output_dir / "archive_inventory_manifest.json", manifest)
        if args.progress_every and completed % args.progress_every == 0:
            print(json.dumps({"event": "progress", "projects_inventoried": len(state["results"])}), flush=True)

    if args.workers == 1:
        for completed, candidate in enumerate(tasks, start=1):
            commit(resolve_candidate(candidate, crossref[candidate["doi"].lower()], args.delay), completed)
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(resolve_candidate, candidate, crossref[candidate["doi"].lower()], args.delay): candidate
                for candidate in tasks
            }
            for completed, future in enumerate(as_completed(futures), start=1):
                commit(future.result(), completed)

    manifest = build_manifest(
        output_dir=output_dir,
        candidates=candidates,
        state=state,
        input_sha=input_sha,
        crossref_sha=sha256_file(crossref_path),
        workers=args.workers,
        retry_unresolved=args.retry_unresolved,
        complete=len(state["results"]) == len(candidates),
    )
    atomic_write_json(output_dir / "archive_inventory_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
