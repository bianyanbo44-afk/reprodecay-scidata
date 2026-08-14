#!/usr/bin/env python3
"""Download, verify, and statically inspect publication-linked archives.

Security boundary: archives are never extracted and third-party code is never
executed. Only member names and bounded text from selected configuration/readme
files are read through Python's archive readers.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable


USER_AGENT = "ReproDecay-static-archive-inspector/0.1"
INSPECTION_POLICY_VERSION = "archive-static-inspection-v0.2.1"
MAX_TEXT_MEMBER_BYTES = 2 * 1024 * 1024
MAX_TOTAL_TEXT_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 100_000
ALLOWED_DOWNLOAD_HOSTS = {
    "zenodo.org",
    "www.zenodo.org",
    "ndownloader.figshare.com",
    "figshare.com",
    "www.figshare.com",
    "data.caltech.edu",
}
ALLOWED_REDIRECT_HOST_SUFFIXES = (
    ".amazonaws.com",
    ".cloudfront.net",
    ".figshare.com",
    ".zenodo.org",
    ".osn.mghpcc.org",
)
INTERESTING_BASENAMES = {
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements_test.txt",
    "requirements-test.txt",
    "tox.ini",
    "pytest.ini",
    "environment.yml",
    "environment.yaml",
    "pipfile",
    "makefile",
    "license",
    "license.txt",
    "license.md",
    "licence",
    "licence.txt",
    "copying",
    "readme",
    "readme.md",
    "readme.rst",
}
INSTALL_PATTERNS = (r"pip install", r"conda install", r"python -m pip", r"poetry install", r"setup\.py install")
TEST_PATTERNS = (r"pytest", r"python -m unittest", r"tox(?:\s|$)", r"nox(?:\s|$)", r"make test")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def md5_file(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_cache_name(record_id: str, file_key: str) -> str:
    suffix = "".join(Path(file_key).suffixes[-2:]) or ".bin"
    digest = hashlib.sha256(file_key.encode("utf-8")).hexdigest()[:12]
    safe_record_id = re.sub(r"[^A-Za-z0-9._-]+", "_", record_id)
    return f"zenodo_{safe_record_id}_{digest}{suffix}"


def validate_download_url(url: str, *, redirect: bool = False) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise ValueError("unsafe_download_url")
    host = (parsed.hostname or "").lower()
    allowed = host in ALLOWED_DOWNLOAD_HOSTS
    if redirect:
        allowed = allowed or any(host.endswith(suffix) for suffix in ALLOWED_REDIRECT_HOST_SUFFIXES)
    if not allowed:
        raise ValueError("download_host_not_allowed")


class RestrictedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        validate_download_url(newurl, redirect=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def resolve_download_url(url: str, *, attempts: int = 5) -> str:
    """Resolve and validate redirects, retrying transient transport failures."""
    validate_download_url(url)
    opener = urllib.request.build_opener(RestrictedRedirectHandler())
    for attempt in range(attempts):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with opener.open(request, timeout=45) as response:
                resolved = response.geturl()
            validate_download_url(resolved, redirect=True)
            return resolved
        except urllib.error.HTTPError as exc:
            if exc.code not in {408, 429, 500, 502, 503, 504} or attempt + 1 == attempts:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionResetError):
            if attempt + 1 == attempts:
                raise
        time.sleep(min(2.0 * (2**attempt), 30.0))
    raise RuntimeError("download_url_retry_state_unreachable")


def download(url: str, destination: Path, expected_size: int) -> None:
    validate_download_url(url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    if partial.exists() and partial.stat().st_size == expected_size:
        partial.replace(destination)
        return
    if partial.exists() and partial.stat().st_size > expected_size:
        raise ValueError("partial_file_larger_than_expected")
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if curl:
        resolved_url = resolve_download_url(url)
        command = [
            curl,
            "--fail",
            "--proto",
            "=https",
            "--silent",
            "--show-error",
            "--connect-timeout",
            "15",
            "--max-time",
            "300",
            "--retry",
            "3",
            "--retry-delay",
            "2",
            "--retry-all-errors",
            "-4",
            "--continue-at",
            "-",
            "--user-agent",
            USER_AGENT,
            "--output",
            str(partial),
        ]
        completed = subprocess.run(command + [resolved_url], check=False, timeout=330)
        if completed.returncode != 0 and "ndownloader.figshare.com/files/" in url:
            # Figshare's signed S3 redirect can expire before curl follows it.
            # A fresh request usually produces a new short-lived signature.
            time.sleep(2)
            resolved_url = resolve_download_url(url)
            completed = subprocess.run(command + [resolved_url], check=False, timeout=330)
        if completed.returncode != 0:
            raise RuntimeError(f"curl_download_failed_{completed.returncode}")
    else:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        opener = urllib.request.build_opener(RestrictedRedirectHandler())
        with opener.open(request, timeout=120) as response, partial.open("wb") as handle:
            validate_download_url(response.geturl(), redirect=True)
            shutil.copyfileobj(response, handle, length=1024 * 1024)
    if expected_size and partial.stat().st_size != expected_size:
        raise ValueError(f"download_size_mismatch:{partial.stat().st_size}:{expected_size}")
    partial.replace(destination)


def public_zenodo_download_url(record_id: str, file_key: str) -> str:
    quoted = urllib.parse.quote(file_key, safe="/")
    return f"https://zenodo.org/records/{record_id}/files/{quoted}?download=1"


def source_value(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def role_matches(row: dict[str, str], requested_role: str) -> bool:
    if requested_role == "all":
        return True
    return source_value(row, "selected_role", "selection_role") == requested_role


def suspicious_path(name: str) -> bool:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    return path.is_absolute() or ".." in path.parts or bool(re.match(r"^[a-zA-Z]:", normalized))


def interesting(name: str) -> bool:
    path = PurePosixPath(name.replace("\\", "/"))
    basename = path.name.lower()
    parts = [part.lower() for part in path.parts]
    return (
        basename in INTERESTING_BASENAMES
        or basename.startswith("requirements") and basename.endswith((".txt", ".in"))
        or "tests" in parts
        or "test" in parts
        or ".github" in parts
    )


def decode_bounded(handle: BinaryIO, size: int) -> str:
    if size < 0 or size > MAX_TEXT_MEMBER_BYTES:
        return ""
    return handle.read(MAX_TEXT_MEMBER_BYTES + 1)[:MAX_TEXT_MEMBER_BYTES].decode("utf-8", errors="replace")


def inspect_zip(path: Path) -> tuple[list[str], dict[str, str], bool, int]:
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_MEMBERS:
            raise ValueError("archive_member_limit_exceeded")
        names = [info.filename for info in infos]
        texts: dict[str, str] = {}
        selected_text_bytes = 0
        for info in infos:
            if info.is_dir() or not interesting(info.filename) or info.file_size > MAX_TEXT_MEMBER_BYTES:
                continue
            if selected_text_bytes + info.file_size > MAX_TOTAL_TEXT_BYTES:
                raise ValueError("archive_text_budget_exceeded")
            with archive.open(info, "r") as handle:
                texts[info.filename] = decode_bounded(handle, info.file_size)
            selected_text_bytes += info.file_size
        return names, texts, any(suspicious_path(name) for name in names), len(infos)


def inspect_tar(path: Path) -> tuple[list[str], dict[str, str], bool, int]:
    with tarfile.open(path, mode="r:*") as archive:
        members = archive.getmembers()
        if len(members) > MAX_MEMBERS:
            raise ValueError("archive_member_limit_exceeded")
        names = [member.name for member in members]
        texts: dict[str, str] = {}
        selected_text_bytes = 0
        for member in members:
            if not member.isfile() or not interesting(member.name) or member.size > MAX_TEXT_MEMBER_BYTES:
                continue
            if selected_text_bytes + member.size > MAX_TOTAL_TEXT_BYTES:
                raise ValueError("archive_text_budget_exceeded")
            handle = archive.extractfile(member)
            if handle is not None:
                with handle:
                    texts[member.name] = decode_bounded(handle, member.size)
                selected_text_bytes += member.size
        return names, texts, any(suspicious_path(name) for name in names), len(members)


def inspect_archive(path: Path) -> tuple[str, list[str], dict[str, str], bool, int]:
    if zipfile.is_zipfile(path):
        names, texts, suspicious, count = inspect_zip(path)
        return "zip", names, texts, suspicious, count
    if tarfile.is_tarfile(path):
        names, texts, suspicious, count = inspect_tar(path)
        return "tar", names, texts, suspicious, count
    return "unsupported", [], {}, False, 0


def root_relative_basenames(names: Iterable[str]) -> set[str]:
    paths = []
    for name in names:
        if not name or name.endswith("/"):
            continue
        path = PurePosixPath(name.replace("\\", "/"))
        if not path.parts or path.parts[0].lower() in {"__macosx", ".ds_store"}:
            continue
        paths.append(path)
    if not paths:
        return set()
    root_files = {path.parts[0].lower() for path in paths if len(path.parts) == 1}
    top_directories = {path.parts[0] for path in paths if len(path.parts) > 1}
    strip_root = ""
    if not root_files and len(top_directories) == 1:
        strip_root = next(iter(top_directories))
    elif len(top_directories) == 1:
        candidate = next(iter(top_directories))
        if not any(name in INTERESTING_BASENAMES for name in root_files):
            strip_root = candidate
    result: set[str] = set()
    for path in paths:
        parts = path.parts[1:] if strip_root and path.parts[0] == strip_root else path.parts
        if len(parts) == 1:
            result.add(parts[0].lower())
    return result


def find_patterns(patterns: tuple[str, ...], text: str) -> list[str]:
    return [pattern for pattern in patterns if re.search(pattern, text, flags=re.IGNORECASE)]


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
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


def stable_error_code(exc: Exception) -> str:
    """Return a release-safe error code without commands, URLs, or local paths."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return "download_timeout"
    if isinstance(exc, urllib.error.HTTPError):
        return f"download_http_{exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return f"download_transport_{type(exc.reason).__name__}"
    if isinstance(exc, (TimeoutError, ConnectionResetError)):
        return f"download_transport_{type(exc).__name__}"
    message = str(exc)
    allowed_messages = (
        "archive_member_limit_exceeded",
        "archive_text_budget_exceeded",
        "download_host_not_allowed",
        "missing_or_unsupported_repository_checksum",
        "partial_file_larger_than_expected",
        "repository_checksum_mismatch",
        "unsafe_download_url",
    )
    if message.startswith("curl_download_failed_"):
        return message
    if message.startswith("download_size_mismatch:"):
        return "download_size_mismatch"
    if message in allowed_messages:
        return message
    return f"{type(exc).__name__}_unspecified"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", default="data/candidate_frame_v0.1/archive_file_inventory.csv")
    parser.add_argument("--cache", default="data/raw/zenodo_archives")
    parser.add_argument("--output", default="data/candidate_frame_v0.1/archive_static_inspection.csv")
    parser.add_argument(
        "--role",
        choices=("all", "primary", "replacement", "census_provisional"),
        default="all",
    )
    parser.add_argument(
        "--selected-only",
        action="store_true",
        help="When present, inspect only rows marked selected_source_archive=true.",
    )
    parser.add_argument("--max-files", type=int, default=0, help="Maximum new or retried files; 0 means unlimited")
    parser.add_argument("--manifest", default="", help="Optional inspection manifest JSON path")
    args = parser.parse_args()

    inventory = list(csv.DictReader(Path(args.inventory).open(encoding="utf-8-sig", newline="")))
    if args.selected_only and inventory and "selected_source_archive" in inventory[0]:
        inventory = [row for row in inventory if row.get("selected_source_archive") == "true"]
    inventory.sort(key=lambda row: int(row["file_size_bytes"] or 0))
    cache = Path(args.cache)
    output = Path(args.output)
    existing_rows = list(csv.DictReader(output.open(encoding="utf-8-sig", newline=""))) if output.exists() else []
    rows_by_key = {(row["scholarly_doi"], row["file_key"]): row for row in existing_rows}
    attempted = 0

    def ordered_rows() -> list[dict[str, str]]:
        keys = [(row["scholarly_doi"], row["file_key"]) for row in inventory]
        return [rows_by_key[key] for key in keys if key in rows_by_key]

    for source in inventory:
        key = (source["scholarly_doi"], source["file_key"])
        existing = rows_by_key.get(key)
        existing_matches_source = bool(
            existing
            and existing.get("archive_doi") == source_value(source, "archive_doi_resolved", "archive_doi")
            and existing.get("file_size_bytes") == source.get("file_size_bytes")
            and existing.get("repository_checksum") == source.get("repository_checksum")
        )
        if existing and not existing_matches_source:
            rows_by_key.pop(key, None)
            existing = None
        if not role_matches(source, args.role):
            continue
        if existing and existing["inspection_status"] in {"inspected", "unsupported_archive_format"}:
            expected_role = source_value(source, "selected_role", "selection_role")
            if expected_role and existing.get("selected_role") != expected_role:
                existing["selected_role"] = expected_role
                write_csv(output, ordered_rows())
            continue
        if args.max_files and attempted >= args.max_files:
            break
        attempted += 1
        local = cache / safe_cache_name(source["archive_record_id"], source["file_key"])
        status = ""
        error = ""
        archive_type = ""
        names: list[str] = []
        texts: dict[str, str] = {}
        suspicious = False
        member_count = 0
        try:
            expected_size = int(source["file_size_bytes"] or 0)
            if local.exists() and expected_size and local.stat().st_size != expected_size:
                local.unlink()
            if not local.exists():
                download(
                    source.get("download_url") or public_zenodo_download_url(source["archive_record_id"], source["file_key"]),
                    local,
                    expected_size,
                )
            expected = source["repository_checksum"]
            if not expected.startswith("md5:"):
                raise ValueError("missing_or_unsupported_repository_checksum")
            if md5_file(local) != expected.split(":", 1)[1].lower():
                local.unlink(missing_ok=True)
                raise ValueError("repository_checksum_mismatch")
            archive_type, names, texts, suspicious, member_count = inspect_archive(local)
            status = "inspected" if archive_type != "unsupported" else "unsupported_archive_format"
        except Exception as exc:
            error = stable_error_code(exc)
            # Keep a publication-linked object in the census when the provider
            # is reachable but the immutable file cannot be acquired. Such a
            # row is auditable and remains unresolved by archive eligibility.
            status = "unavailable" if error in {
                "download_timeout",
                "download_transport_TimeoutError",
                "download_transport_ConnectionResetError",
                "download_http_403",
                "download_http_404",
                "curl_download_failed_22",
                "curl_download_failed_28",
                "archive_text_budget_exceeded",
                "archive_member_limit_exceeded",
            } else "inspection_error"

        roots = root_relative_basenames(names)
        all_text = "\n".join(texts.values())
        packaging = sorted(
            name for name in roots if name in {
                "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "pipfile", "environment.yml", "environment.yaml"
            }
        )
        licence = sorted(name for name in roots if name.startswith(("license", "licence", "copying")))
        test_evidence = sorted(
            name for name in names
            if re.search(r"(^|/)(tests?|tox\.ini|pytest\.ini)(/|$)", name.replace("\\", "/"), flags=re.IGNORECASE)
        )
        rows_by_key[key] = {
                "selected_role": source_value(source, "selected_role", "selection_role"),
                "rank": source["rank"],
                "scholarly_doi": source["scholarly_doi"],
                "archive_doi": source_value(source, "archive_doi_resolved", "archive_doi"),
                "file_key": source["file_key"],
                "file_size_bytes": source["file_size_bytes"],
                "repository_checksum": source["repository_checksum"],
                "local_sha256": sha256_file(local) if local.exists() else "",
                "archive_type": archive_type,
                "member_count": str(member_count),
                "suspicious_member_path": str(suspicious).lower(),
                "root_packaging_files": ";".join(packaging),
                "root_licence_files": ";".join(licence),
                "test_member_count": str(len(test_evidence)),
                "install_patterns": ";".join(find_patterns(INSTALL_PATTERNS, all_text)),
                "test_patterns": ";".join(find_patterns(TEST_PATTERNS, all_text)),
                "inspection_status": status,
                "inspection_error": error,
                "inspected_at_utc": datetime.now(timezone.utc).isoformat(),
                "third_party_code_executed": "false",
            }
        write_csv(output, ordered_rows())

    output_rows = ordered_rows()
    summary = {
                "files": len(output_rows),
                "projects": len({row["scholarly_doi"] for row in output_rows}),
                "downloaded_bytes": sum(int(row["file_size_bytes"] or 0) for row in output_rows),
                "inspection_errors": sum(row["inspection_status"] == "inspection_error" for row in output_rows),
                "unsupported_formats": sum(row["inspection_status"] == "unsupported_archive_format" for row in output_rows),
                "suspicious_member_paths": sum(row["suspicious_member_path"] == "true" for row in output_rows),
                "third_party_code_executed": False,
            }
    if args.manifest:
        manifest_path = Path(args.manifest)
        manifest = {
            "schema_version": "0.2",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "inspection_policy_version": INSPECTION_POLICY_VERSION,
            "inventory_sha256": sha256_file(Path(args.inventory)),
            "inspection_sha256": sha256_file(output),
            "selected_only": args.selected_only,
            "requested_role": args.role,
            "inventory_rows_considered": len(inventory),
            "complete": len(output_rows) == len(inventory),
            **summary,
        }
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(manifest_path)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
