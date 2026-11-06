#!/usr/bin/env python3
"""Resolve and acquire immutable endpoint snapshots for pilot or census inputs.

The script uses repository metadata, a stable GitHub release when available,
and otherwise the default-branch head. It downloads a source archive addressed
by an immutable commit SHA. It never executes or imports candidate code.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


USER_AGENT = "ResearchSoftwareEvolutionDataset-endpoint-resolver/0.2"
MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
ENDPOINT_POLICY_VERSION = "endpoint-resolution-v0.3.1"
PUBLIC_ENDPOINT_FIELDS = (
    "project_id", "scholarly_doi", "selection_rank", "selected_role",
    "input_repository_url", "resolved_repository_url", "resolved_full_name",
    "repository_node_id", "repository_archived", "repository_disabled",
    "repository_fork", "repository_default_branch", "repository_created_at",
    "repository_updated_at", "repository_pushed_at", "repository_license_spdx",
    "endpoint_policy", "endpoint_ref_type", "endpoint_ref_name", "endpoint_commit_sha",
    "endpoint_release_id", "endpoint_release_published_at", "endpoint_archive_url",
    "endpoint_archive_bytes", "endpoint_archive_sha256", "resolution_status",
    "resolution_error", "resolution_method", "resolution_evidence_url",
    "github_rate_limit_remaining", "resolved_at_utc", "resolution_mode", "endpoint_as_of_utc",
    "third_party_code_executed",
)
TRANSIENT_RESOLUTION_ERRORS = frozenset(
    {
        "curl_download_failed:33",
        "github_http_301",
        "github_http_403",
        "github_http_429",
        "network_TimeoutError",
        "network_ConnectionResetError",
        "download_timeout",
        "RuntimeError_unspecified",
    }
)

GITHUB_WEB_HOSTS = {"github.com", "www.github.com"}


def run_curl_effective_url(url: str) -> tuple[str, int]:
    """Resolve a public GitHub Web URL and return its final URL and status."""
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise RuntimeError("curl_not_available")
    completed = subprocess.run(
        [
            curl,
            "-4",
            "--proto",
            "=https",
            "--silent",
            "--show-error",
            "--location",
            "--head",
            "--connect-timeout",
            "15",
            "--max-time",
            "45",
            "--user-agent",
            USER_AGENT,
            "--write-out",
            "\n%{url_effective}\n%{http_code}",
            url,
        ],
        capture_output=True,
        check=False,
        timeout=55,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"github_web_curl_failed:{completed.returncode}")
    lines = completed.stdout.decode("utf-8", errors="replace").splitlines()
    if len(lines) < 2 or not lines[-1].isdigit():
        raise RuntimeError("github_web_response_invalid")
    effective = lines[-2].strip()
    status = int(lines[-1])
    parsed = urllib.parse.urlparse(effective)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in GITHUB_WEB_HOSTS:
        raise RuntimeError("github_web_redirect_not_allowed")
    return effective, status


def github_coordinates_from_url(url: str) -> tuple[str, str, str]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in GITHUB_WEB_HOSTS:
        raise RuntimeError("github_web_redirect_not_allowed")
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        raise RuntimeError("repository_web_url_invalid")
    owner, name = parts[:2]
    if name.endswith(".git"):
        name = name[:-4]
    if not owner or not name:
        raise RuntimeError("repository_web_url_invalid")
    full_name = f"{owner}/{name}"
    return full_name, owner, name


def git_ls_remote(repository_url: str, *refs: str, symref: bool = False) -> str:
    git = shutil.which("git.exe") or shutil.which("git")
    if not git:
        raise RuntimeError("git_not_available")
    command = [git, "ls-remote"]
    if symref:
        command.append("--symref")
    command.append(repository_url)
    command.extend(refs)
    completed = subprocess.run(command, capture_output=True, check=False, timeout=90)
    if completed.returncode != 0:
        raise RuntimeError("git_ls_remote_failed")
    return completed.stdout.decode("utf-8", errors="replace")


def resolve_git_ref(repository_url: str, ref_name: str, *, release: bool) -> str:
    """Resolve a release tag or branch to a full commit SHA without cloning."""
    if release:
        base = f"refs/tags/{ref_name}"
        output = git_ls_remote(repository_url, base, f"{base}^{{}}")
        values = {}
        for line in output.splitlines():
            fields = line.split("\t", 1)
            if len(fields) == 2:
                values[fields[1]] = fields[0]
        sha = values.get(f"{base}^{{}}") or values.get(base, "")
    else:
        output = git_ls_remote(repository_url, f"refs/heads/{ref_name}")
        fields = output.splitlines()[0].split("\t", 1) if output.splitlines() else []
        sha = fields[0] if len(fields) == 2 else ""
    if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        raise RuntimeError("selected_ref_not_resolved_to_sha")
    return sha.lower()


def resolve_via_github_web(candidate: dict[str, str]) -> dict[str, str]:
    """Resolve canonical repo and latest stable ref from official GitHub Web/git surfaces."""
    canonical_url, status = run_curl_effective_url(candidate["repository_url"])
    if status != 200:
        raise RuntimeError("github_web_repository_unavailable")
    full_name, owner, name = github_coordinates_from_url(canonical_url)
    canonical_url = f"https://github.com/{owner}/{name}"
    latest_url, latest_status = run_curl_effective_url(f"{canonical_url}/releases/latest")
    prefix = f"/{owner}/{name}/releases/tag/"
    latest_path = urllib.parse.urlparse(latest_url).path
    if latest_status == 200 and latest_path.startswith(prefix):
        ref_type = "release"
        ref_name = urllib.parse.unquote(latest_path[len(prefix):])
        evidence_url = latest_url
    else:
        ref_type = "default_branch"
        symrefs = git_ls_remote(canonical_url + ".git", "HEAD", symref=True)
        match = re.search(r"^ref:\s+refs/heads/(.+?)\s+HEAD$", symrefs, flags=re.MULTILINE)
        if not match:
            raise RuntimeError("default_branch_not_resolved")
        ref_name = match.group(1)
        evidence_url = canonical_url
    commit_sha = resolve_git_ref(canonical_url + ".git", ref_name, release=ref_type == "release")
    return {
        "resolved_repository_url": canonical_url,
        "resolved_full_name": full_name,
        "repository_node_id": candidate.get("github_node_id", ""),
        "repository_archived": candidate.get("github_archived", ""),
        "repository_disabled": "",
        "repository_fork": candidate.get("github_fork", ""),
        "repository_default_branch": candidate.get("github_default_branch", ""),
        "repository_created_at": candidate.get("github_created_at", ""),
        "repository_updated_at": candidate.get("github_updated_at", ""),
        "repository_pushed_at": candidate.get("github_pushed_at", ""),
        "repository_license_spdx": candidate.get("github_license_spdx", ""),
        "endpoint_ref_type": ref_type,
        "endpoint_ref_name": ref_name,
        "endpoint_commit_sha": commit_sha,
        "endpoint_release_id": "",
        "endpoint_release_published_at": "",
        "resolution_method": "github_web_git_refs",
        "resolution_evidence_url": evidence_url,
    }


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def summarize_resolved_rows(rows: list[dict[str, str]]) -> dict[str, Any]:
    """Summarize endpoint type and method counts from resolved rows only.

    A row can retain partially resolved metadata when a later archive download
    fails. Such rows remain useful for diagnosing the failure, but must not
    contribute to endpoint or method counts in the manifest.
    """
    resolved_rows = [row for row in rows if row.get("resolution_status") == "resolved"]
    methods: dict[str, int] = {}
    for row in resolved_rows:
        method = row.get("resolution_method", "")
        if method:
            methods[method] = methods.get(method, 0) + 1
    return {
        "projects_resolved": len(resolved_rows),
        "stable_release_endpoints": sum(
            row.get("endpoint_ref_type") == "release" for row in resolved_rows
        ),
        "default_branch_endpoints": sum(
            row.get("endpoint_ref_type") == "default_branch" for row in resolved_rows
        ),
        "resolution_methods": dict(sorted(methods.items())),
    }


def project_id(doi: str) -> str:
    suffix = doi.strip().lower().removeprefix("https://doi.org/")
    return "doi_" + "".join(ch if ch.isalnum() else "_" for ch in suffix).strip("_")


def parse_curl_response(raw: bytes) -> tuple[str, bytes]:
    """Return the final response headers and body from curl --location output."""
    cursor = 0
    header_bytes = b""
    while raw[cursor:].startswith(b"HTTP/"):
        crlf_end = raw.find(b"\r\n\r\n", cursor)
        lf_end = raw.find(b"\n\n", cursor)
        candidates = [(crlf_end, 4), (lf_end, 2)]
        candidates = [(end, width) for end, width in candidates if end >= 0]
        if not candidates:
            return "", b""
        header_end, separator_width = min(candidates)
        header_bytes = raw[cursor:header_end]
        cursor = header_end + separator_width
    return header_bytes.decode("iso-8859-1", errors="replace"), raw[cursor:]


def canonical_repository_coordinates(repo: dict[str, Any]) -> tuple[str, str, str, str]:
    """Derive canonical repository identifiers from the final GitHub response."""
    full_name = str(repo["full_name"])
    owner, name = full_name.split("/", 1)
    if not owner or not name:
        raise RuntimeError("repository_full_name_invalid")
    slug = f"{urllib.parse.quote(owner)}/{urllib.parse.quote(name)}"
    return (
        full_name,
        str(repo["html_url"]),
        slug,
        f"https://api.github.com/repos/{slug}",
    )


def resolve_via_github_api(
    candidate: dict[str, str], token: str | None
) -> tuple[dict[str, str], str]:
    """Resolve repository metadata and the selected immutable ref through REST."""
    owner = candidate["repository_owner"]
    name = candidate["repository_name"]
    api_slug = f"{urllib.parse.quote(owner)}/{urllib.parse.quote(name)}"
    repo_api = f"https://api.github.com/repos/{api_slug}"
    repo, headers = request_json(repo_api, token)
    if not isinstance(repo, dict):
        raise RuntimeError("repository_response_invalid")
    full_name, repository_url, resolved_slug, canonical_repo_api = canonical_repository_coordinates(repo)
    default_branch = str(repo["default_branch"])
    licence = repo.get("license") or {}
    metadata = {
        "resolved_repository_url": repository_url,
        "resolved_full_name": full_name,
        "repository_node_id": str(repo.get("node_id") or ""),
        "repository_archived": str(bool(repo.get("archived"))).lower(),
        "repository_disabled": str(bool(repo.get("disabled"))).lower(),
        "repository_fork": str(bool(repo.get("fork"))).lower(),
        "repository_default_branch": default_branch,
        "repository_created_at": str(repo.get("created_at") or ""),
        "repository_updated_at": str(repo.get("updated_at") or ""),
        "repository_pushed_at": str(repo.get("pushed_at") or ""),
        "repository_license_spdx": str(licence.get("spdx_id") or ""),
        "github_rate_limit_remaining": headers.get("x-ratelimit-remaining", ""),
    }
    release, release_headers = request_json(
        f"https://api.github.com/repos/{resolved_slug}/releases/latest", token, allow_404=True
    )
    metadata["github_rate_limit_remaining"] = release_headers.get(
        "x-ratelimit-remaining", metadata["github_rate_limit_remaining"]
    )
    if isinstance(release, dict) and not bool(release.get("draft")) and not bool(release.get("prerelease")):
        ref_type = "release"
        ref_name = str(release["tag_name"])
        metadata["endpoint_release_id"] = str(release.get("id") or "")
        metadata["endpoint_release_published_at"] = str(release.get("published_at") or "")
    else:
        ref_type = "default_branch"
        ref_name = default_branch
    commit, commit_headers = request_json(
        f"https://api.github.com/repos/{resolved_slug}/commits/{urllib.parse.quote(ref_name, safe='')}",
        token,
    )
    metadata["github_rate_limit_remaining"] = commit_headers.get(
        "x-ratelimit-remaining", metadata["github_rate_limit_remaining"]
    )
    if not isinstance(commit, dict):
        raise RuntimeError("selected_ref_commit_response_invalid")
    commit_sha = str(commit.get("sha") or "")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", commit_sha):
        raise RuntimeError("selected_ref_not_resolved_to_sha")
    metadata.update(
        {
            "endpoint_ref_type": ref_type,
            "endpoint_ref_name": ref_name,
            "endpoint_commit_sha": commit_sha.lower(),
            "resolution_method": "github_rest_api",
            "resolution_evidence_url": canonical_repo_api,
        }
    )
    return metadata, canonical_repo_api


def resolve_via_github_api_as_of(candidate: dict[str, str], token: str | None, as_of: str) -> dict[str, str]:
    """Apply the endpoint policy retrospectively, to the repository state at ``as_of``.

    Mirrors GitHub's ``releases/latest`` rule (the non-draft, non-prerelease release
    with the newest ``created_at``) over releases already published at ``as_of``;
    otherwise selects the newest default-branch commit dated no later than ``as_of``.
    Releases deleted, or draft/prerelease flags changed, after ``as_of`` cannot be
    seen, and the current default branch name is used.
    """
    owner = candidate["repository_owner"]
    name = candidate["repository_name"]
    repo, headers = request_json(f"https://api.github.com/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(name)}", token)
    if not isinstance(repo, dict):
        raise RuntimeError("repository_response_invalid")
    full_name, repository_url, resolved_slug, canonical_repo_api = canonical_repository_coordinates(repo)
    default_branch = str(repo["default_branch"])
    licence = repo.get("license") or {}
    metadata = {
        "resolved_repository_url": repository_url,
        "resolved_full_name": full_name,
        "repository_node_id": str(repo.get("node_id") or ""),
        "repository_archived": str(bool(repo.get("archived"))).lower(),
        "repository_disabled": str(bool(repo.get("disabled"))).lower(),
        "repository_fork": str(bool(repo.get("fork"))).lower(),
        "repository_default_branch": default_branch,
        "repository_created_at": str(repo.get("created_at") or ""),
        "repository_updated_at": str(repo.get("updated_at") or ""),
        "repository_pushed_at": str(repo.get("pushed_at") or ""),
        "repository_license_spdx": str(licence.get("spdx_id") or ""),
    }
    releases: list[dict[str, Any]] = []
    for page in range(1, 21):
        batch, headers = request_json(
            f"https://api.github.com/repos/{resolved_slug}/releases?per_page=100&page={page}", token
        )
        if not isinstance(batch, list) or not batch:
            break
        releases.extend(item for item in batch if isinstance(item, dict))
        if len(batch) < 100:
            break
    eligible = [
        item for item in releases
        if not item.get("draft") and not item.get("prerelease")
        and item.get("published_at") and str(item["published_at"]) <= as_of
    ]
    if eligible:
        release = max(eligible, key=lambda item: (str(item.get("created_at") or ""), str(item["published_at"]), int(item.get("id") or 0)))
        ref_type, ref_name = "release", str(release["tag_name"])
        metadata["endpoint_release_id"] = str(release.get("id") or "")
        metadata["endpoint_release_published_at"] = str(release.get("published_at") or "")
        commit, headers = request_json(
            f"https://api.github.com/repos/{resolved_slug}/commits/{urllib.parse.quote(ref_name, safe='')}", token
        )
    else:
        ref_type, ref_name = "default_branch", default_branch
        commits, headers = request_json(
            f"https://api.github.com/repos/{resolved_slug}/commits?sha={urllib.parse.quote(default_branch, safe='')}"
            f"&until={urllib.parse.quote(as_of)}&per_page=1",
            token,
        )
        if not isinstance(commits, list) or not commits:
            raise RuntimeError("no_default_branch_commit_before_as_of")
        commit = commits[0]
    if not isinstance(commit, dict):
        raise RuntimeError("selected_ref_commit_response_invalid")
    commit_sha = str(commit.get("sha") or "")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", commit_sha):
        raise RuntimeError("selected_ref_not_resolved_to_sha")
    metadata.update(
        {
            "endpoint_ref_type": ref_type,
            "endpoint_ref_name": ref_name,
            "endpoint_commit_sha": commit_sha.lower(),
            "resolution_method": "github_rest_api_as_of",
            "resolution_evidence_url": canonical_repo_api,
            "github_rate_limit_remaining": headers.get("x-ratelimit-remaining", ""),
        }
    )
    return metadata


def resolve_endpoint_metadata(candidate: dict[str, str], token: str | None, as_of: str = "") -> dict[str, str]:
    """Use REST first and fall back to official Web/git refs only at API rate limits."""
    if as_of:
        return resolve_via_github_api_as_of(candidate, token, as_of)
    try:
        metadata, _ = resolve_via_github_api(candidate, token)
        return metadata
    except urllib.error.HTTPError as exc:
        remaining = str((exc.headers or {}).get("x-ratelimit-remaining", ""))
        if exc.code not in {403, 429} or remaining != "0":
            raise
    return resolve_via_github_web(candidate)


def request_json(
    url: str, token: str | None, allow_404: bool = False, attempts: int = 4
) -> tuple[Any | None, dict[str, str]]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if curl:
        for attempt in range(attempts):
            command = [
                curl, "-4", "--proto", "=https", "--fail-with-body", "--silent", "--show-error",
                "--location", "--connect-timeout", "15", "--max-time", "45", "--dump-header", "-",
                "--user-agent", USER_AGENT, "--header", "Accept: application/vnd.github+json",
                "--header", "X-GitHub-Api-Version: 2022-11-28",
            ]
            if token:
                command.extend(["--header", f"Authorization: Bearer {token}"])
            completed = subprocess.run(command + [url], capture_output=True, check=False, timeout=55)
            header_block, body = parse_curl_response(completed.stdout)
            response_headers = {
                key.strip().lower(): value.strip()
                for line in header_block.splitlines()
                if ":" in line
                for key, value in [line.split(":", 1)]
            }
            status_match = re.search(r"HTTP/\S+\s+(\d{3})", header_block)
            status = int(status_match.group(1)) if status_match else 0
            if completed.returncode == 0 and 200 <= status < 300:
                return json.loads(body.decode("utf-8")), response_headers
            if allow_404 and status == 404:
                return None, response_headers
            if status in {403, 429} and response_headers.get("x-ratelimit-remaining") == "0":
                raise urllib.error.HTTPError(url, status, "github_api_rate_limited", response_headers, None)
            if status not in {403, 429, 500, 502, 503, 504} or attempt + 1 == attempts:
                if status:
                    raise urllib.error.HTTPError(url, status, "github_api_error", response_headers, None)
                raise RuntimeError(f"github_curl_failed:{completed.returncode}")
            retry_after = response_headers.get("retry-after", "")
            wait = min(float(retry_after) if retry_after.isdigit() else 2.0 * (2**attempt), 30.0)
            time.sleep(wait)
        raise RuntimeError("request_retry_state_unreachable")

    request = urllib.request.Request(url, headers=headers)
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                response_headers = {key.lower(): value for key, value in response.headers.items()}
                return json.loads(response.read().decode("utf-8")), response_headers
        except urllib.error.HTTPError as exc:
            headers = {key.lower(): value for key, value in exc.headers.items()}
            if allow_404 and exc.code == 404:
                return None, headers
            if exc.code in {403, 429} and headers.get("x-ratelimit-remaining") == "0":
                raise
            if exc.code not in {403, 429, 500, 502, 503, 504} or attempt + 1 == attempts:
                raise
            retry_after = headers.get("retry-after", "")
            wait = min(float(retry_after) if retry_after.isdigit() else 2.0 * (2**attempt), 30.0)
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError, ConnectionResetError):
            if attempt + 1 == attempts:
                raise
            time.sleep(min(1.0 * (2**attempt), 8.0))
    raise RuntimeError("request_retry_state_unreachable")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


DOWNLOAD_MAX_SECONDS = 300


def download_archive(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise RuntimeError("curl_not_available")
    command = [
            curl,
            "--fail",
            "--location",
            "--silent",
            "--show-error",
            "--connect-timeout",
            "20",
            "--max-time",
            str(DOWNLOAD_MAX_SECONDS),
            "--retry",
            "3",
            "--retry-delay",
            "2",
            "--retry-all-errors",
            "-4",
            "--continue-at",
            "-",
            "--max-filesize",
            str(MAX_ARCHIVE_BYTES),
            "--user-agent",
            USER_AGENT,
            "--output",
            str(partial),
            url,
        ]
    completed = subprocess.run(
        command,
        check=False,
        timeout=DOWNLOAD_MAX_SECONDS + 30,
    )
    if completed.returncode == 33:
        partial.unlink(missing_ok=True)
        non_resuming_command = command.copy()
        continue_index = non_resuming_command.index("--continue-at")
        del non_resuming_command[continue_index : continue_index + 2]
        completed = subprocess.run(
            non_resuming_command,
            check=False,
            timeout=DOWNLOAD_MAX_SECONDS + 30,
        )
    if completed.returncode != 0:
        raise RuntimeError(f"curl_download_failed:{completed.returncode}")
    if partial.stat().st_size > MAX_ARCHIVE_BYTES:
        raise RuntimeError("archive_size_limit_exceeded")
    partial.replace(destination)


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for attempt in range(8):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(min(0.05 * (2**attempt), 0.8))


def stable_resolution_error(exc: Exception) -> str:
    if isinstance(exc, subprocess.TimeoutExpired):
        return "download_timeout"
    if isinstance(exc, urllib.error.HTTPError):
        return f"github_http_{exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return f"network_{type(exc.reason).__name__}"
    message = str(exc)
    allowed = {
        "archive_size_limit_exceeded",
        "curl_not_available",
        "default_branch_not_resolved",
        "git_ls_remote_failed",
        "git_not_available",
        "github_web_redirect_not_allowed",
        "github_web_repository_unavailable",
        "github_web_response_invalid",
        "override_commit_sha_not_full_length",
        "repository_response_invalid",
        "repository_web_url_invalid",
        "selected_ref_commit_response_invalid",
        "selected_ref_not_resolved_to_sha",
    }
    if (
        message in allowed
        or message.startswith("curl_download_failed:")
        or message.startswith("github_web_curl_failed:")
    ):
        return message
    return f"{type(exc).__name__}_unspecified"


def retryable_resolution_error(error: str) -> bool:
    return (
        error in TRANSIENT_RESOLUTION_ERRORS
        or error.startswith("github_web_curl_failed:")
        or error.startswith("network_")
        or error == "git_ls_remote_failed"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", default="data/candidate_frame_v0.1/pilot_candidates.csv")
    parser.add_argument("--publication-inspection", default="data/candidate_frame_v0.1/archive_static_inspection.csv")
    parser.add_argument("--eligibility-ledger", default="")
    parser.add_argument("--output-dir", default="data/pilot_v0.2")
    parser.add_argument("--cache", default="data/raw/endpoint_archives")
    parser.add_argument("--overrides", default="data/pilot_v0.2/endpoint_overrides.csv")
    parser.add_argument("--role", default="primary", choices=("primary", "replacement", "census_provisional", "all"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--as-of", default="",
                        help="resolve new projects retrospectively at this UTC time (ISO 8601 with Z)")
    parser.add_argument("--workers", type=int, default=1, help="worker threads for network resolution and download")
    parser.add_argument("--download-max-seconds", type=int, default=300, help="per-attempt time limit for one archive download")
    parser.add_argument("--retry-all-unresolved", action="store_true",
                        help="retry earlier unresolved rows whatever their error")
    args = parser.parse_args()

    global DOWNLOAD_MAX_SECONDS
    DOWNLOAD_MAX_SECONDS = args.download_max_seconds
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None
    candidate_rows = list(csv.DictReader(Path(args.candidates).open(encoding="utf-8-sig", newline="")))
    inspected_rows = list(csv.DictReader(Path(args.publication_inspection).open(encoding="utf-8-sig", newline="")))
    inspected_dois = {
        row["scholarly_doi"] for row in inspected_rows
        if row["inspection_status"] == "inspected" and row["suspicious_member_path"] == "false"
    }
    eligible_dois: set[str] | None = None
    if args.eligibility_ledger:
        eligible_dois = {
            row["scholarly_doi"]
            for row in csv.DictReader(Path(args.eligibility_ledger).open(encoding="utf-8-sig", newline=""))
            if row["final_eligibility"] == "eligible_archive"
        }
    selected = [
        row for row in candidate_rows
        if row["doi"] in inspected_dois and (args.role == "all" or row["selected_role"] == args.role)
        and (eligible_dois is None or row["doi"] in eligible_dois)
    ]
    selected.sort(key=lambda row: int(row["rank"]))
    if args.limit:
        selected = selected[: args.limit]

    output_dir = Path(args.output_dir)
    cache = Path(args.cache)
    overrides: dict[str, dict[str, str]] = {}
    override_path = Path(args.overrides)
    if override_path.exists():
        overrides = {
            override["scholarly_doi"]: override
            for override in csv.DictReader(override_path.open(encoding="utf-8-sig", newline=""))
        }
    existing_path = output_dir / "endpoint_resolution.csv"
    existing: dict[str, dict[str, str]] = {}
    if existing_path.exists():
        for prior in csv.DictReader(existing_path.open(encoding="utf-8-sig", newline="")):
            prior["resolution_mode"] = prior.get("resolution_mode") or "live"
            prior["endpoint_as_of_utc"] = prior.get("endpoint_as_of_utc") or ""
            existing[prior["scholarly_doi"]] = prior
    rows_by_doi: dict[str, dict[str, str]] = {}

    def ordered_rows() -> list[dict[str, str]]:
        return [
            {key: rows_by_doi[row["doi"]].get(key, "") for key in PUBLIC_ENDPOINT_FIELDS}
            for row in selected if row["doi"] in rows_by_doi
        ]

    def resolve_candidate(candidate: dict[str, str]) -> dict[str, str]:
        """Network part of endpoint resolution for one candidate; safe to run in a worker thread."""
        doi = candidate["doi"]
        row = {
            "project_id": project_id(doi),
            "scholarly_doi": doi,
            "selection_rank": candidate["rank"],
            "selected_role": candidate["selected_role"],
            "input_repository_url": candidate["repository_url"],
            "resolved_repository_url": "",
            "resolved_full_name": "",
            "repository_node_id": "",
            "repository_archived": "",
            "repository_disabled": "",
            "repository_fork": "",
            "repository_default_branch": "",
            "repository_created_at": "",
            "repository_updated_at": "",
            "repository_pushed_at": "",
            "repository_license_spdx": "",
            "endpoint_policy": "latest_stable_release_else_default_branch_head",
            "endpoint_ref_type": "",
            "endpoint_ref_name": "",
            "endpoint_commit_sha": "",
            "endpoint_release_id": "",
            "endpoint_release_published_at": "",
            "endpoint_archive_url": "",
            "endpoint_archive_bytes": "",
            "endpoint_archive_sha256": "",
            "resolution_status": "",
            "resolution_error": "",
            "resolution_method": "",
            "resolution_evidence_url": "",
            "github_rate_limit_remaining": "",
            "resolved_at_utc": now_utc(),
            "resolution_mode": "retrospective_as_of" if args.as_of else "live",
            "endpoint_as_of_utc": args.as_of,
            "third_party_code_executed": "false",
        }
        try:
            override = overrides.get(doi)
            if override:
                commit_sha = override["endpoint_commit_sha"].lower()
                if not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
                    raise ValueError("override_commit_sha_not_full_length")
                resolved_owner, resolved_name = override["resolved_full_name"].split("/", 1)
                archive_url = f"https://codeload.github.com/{resolved_owner}/{resolved_name}/tar.gz/{commit_sha}"
                local = cache / f"{row['project_id']}_{commit_sha}.tar.gz"
                if not local.exists():
                    download_archive(archive_url, local)
                row.update(
                    {
                        "resolved_repository_url": override["resolved_repository_url"],
                        "resolved_full_name": override["resolved_full_name"],
                        "repository_archived": override["repository_archived"],
                        "repository_disabled": "false",
                        "repository_fork": "false",
                        "repository_default_branch": override["repository_default_branch"],
                        "repository_license_spdx": override["repository_license_spdx"],
                        "endpoint_ref_type": override["endpoint_ref_type"],
                        "endpoint_ref_name": override["endpoint_ref_name"],
                        "endpoint_commit_sha": commit_sha,
                        "endpoint_release_published_at": override["endpoint_release_published_at"],
                        "endpoint_archive_url": archive_url,
                        "endpoint_archive_bytes": str(local.stat().st_size),
                        "endpoint_archive_sha256": sha256_file(local),
                        "resolution_status": "resolved",
                        "resolution_method": override["resolution_method"],
                        "resolution_evidence_url": override["resolution_evidence_url"],
                    }
                )
                return row
            metadata = resolve_endpoint_metadata(candidate, token, args.as_of)
            row.update(metadata)
            resolved_owner, resolved_name = row["resolved_full_name"].split("/", 1)
            commit_sha = row["endpoint_commit_sha"]
            archive_url = f"https://codeload.github.com/{resolved_owner}/{resolved_name}/tar.gz/{commit_sha}"
            local = cache / f"{row['project_id']}_{commit_sha}.tar.gz"
            if not local.exists():
                download_archive(archive_url, local)
            row.update(
                {
                    "endpoint_archive_url": archive_url,
                    "endpoint_archive_bytes": str(local.stat().st_size),
                    "endpoint_archive_sha256": sha256_file(local),
                    "resolution_status": "resolved",
                }
            )
        except Exception as exc:
            row["resolution_status"] = "unresolved"
            row["resolution_error"] = stable_resolution_error(exc)
        time.sleep(max(args.delay, 0.0))
        return row

    pending: list[dict[str, str]] = []
    for candidate in selected:
        doi = candidate["doi"]
        prior = existing.get(doi)
        if prior and prior.get("resolution_status") == "resolved":
            prior_path = Path(prior.get("endpoint_local_cache") or (
                cache / f"{prior['project_id']}_{prior['endpoint_commit_sha']}.tar.gz"
            ))
            if (
                prior_path.exists()
                and prior.get("endpoint_archive_sha256")
                and sha256_file(prior_path) == prior["endpoint_archive_sha256"]
            ):
                prior.setdefault("resolution_method", "github_rest_api")
                prior.setdefault("resolution_evidence_url", prior.get("endpoint_archive_url", ""))
                prior.pop("endpoint_local_cache", None)
                rows_by_doi[doi] = prior
                write_csv(output_dir / "endpoint_resolution.csv", ordered_rows())
                continue
        if (
            prior
            and prior.get("resolution_status") == "unresolved"
            and not args.retry_all_unresolved
            and not retryable_resolution_error(prior.get("resolution_error", ""))
        ):
            prior.pop("endpoint_local_cache", None)
            rows_by_doi[doi] = prior
            write_csv(output_dir / "endpoint_resolution.csv", ordered_rows())
            continue
        pending.append(candidate)

    # Candidates that need the network are resolved by worker threads; only this thread writes the ledger.
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(resolve_candidate, candidate): candidate["doi"] for candidate in pending}
        for completed, future in enumerate(as_completed(futures), start=1):
            rows_by_doi[futures[future]] = future.result()
            write_csv(output_dir / "endpoint_resolution.csv", ordered_rows())
            if completed % 25 == 0:
                print(json.dumps({"event": "progress", "resolved_this_run": completed, "pending": len(pending)}), flush=True)

    rows = ordered_rows()
    resolved_summary = summarize_resolved_rows(rows)
    summary = {
        "schema_version": "0.2",
        "endpoint_policy_version": ENDPOINT_POLICY_VERSION,
        "created_at_utc": now_utc(),
        "endpoint_snapshot_date": datetime.now(timezone.utc).date().isoformat(),
        "retrospective_as_of_utc": args.as_of,
        "resolution_modes": {
            mode: sum(row.get("resolution_mode") == mode for row in rows)
            for mode in sorted({row.get("resolution_mode", "") for row in rows})
        },
        "projects_attempted": len(rows),
        **resolved_summary,
        "third_party_code_executed": False,
        "github_token_present": bool(token),
        "max_archive_bytes": MAX_ARCHIVE_BYTES,
        "endpoint_resolution_sha256": sha256_file(output_dir / "endpoint_resolution.csv"),
        "input_sha256": {
            "candidates": sha256_file(Path(args.candidates)),
            "publication_inspection": sha256_file(Path(args.publication_inspection)),
            **(
                {"eligibility_ledger": sha256_file(Path(args.eligibility_ledger))}
                if args.eligibility_ledger else {}
            ),
        },
        "selection_role": args.role,
    }
    (output_dir / "endpoint_resolution_manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary["projects_resolved"] == summary["projects_attempted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
