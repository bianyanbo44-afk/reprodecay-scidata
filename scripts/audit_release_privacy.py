#!/usr/bin/env python3
"""Scan release text for credential signatures and workstation path leaks."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterable


TEXT_SUFFIXES = {
    ".bib", ".cff", ".csv", ".ini", ".json", ".md", ".py", ".tex",
    ".txt", ".yml", ".yaml",
}
TEXT_BASENAMES = {"LICENSE-CODE", "LICENSE-DATA"}
SOURCE_LITERAL_TABLES = {
    "data/census_v0.2/tables/api_symbol_records.csv",
    "data/census_v0.2/tables/pair_delta_records.csv",
    "data/census_v0.3/tables/api_symbol_records.csv",
    "data/census_v0.3/tables/pair_delta_records.csv",
}
PATH_RULES = {"windows_user_path", "posix_user_path"}
RULES = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "credentialed_url": re.compile(r"https?://[^/\s:@]+:[^@\s/]+@"),
    "windows_user_path": re.compile(r"(?i)(?:^|[\s'\"])[A-Z]:[\\/](?:Users|Documents and Settings)[\\/][^\\/\s]+"),
    "posix_user_path": re.compile(r"(?:^|[\s'\"])/(?:home|Users)/[^/\s]+"),
}


def is_text_path(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_BASENAMES


def scan_paths(root: Path, relative_paths: Iterable[str]) -> list[dict[str, object]]:
    root = root.resolve()
    findings: list[dict[str, object]] = []
    for relative in sorted(set(relative_paths)):
        normalized_relative = relative.replace("\\", "/")
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file() or not is_text_path(path):
            continue
        try:
            with path.open(encoding="utf-8-sig", errors="strict") as handle:
                for line_number, line in enumerate(handle, start=1):
                    for rule, pattern in RULES.items():
                        # These two derived tables intentionally preserve
                        # source-code signatures. Home-directory literals in
                        # those signatures belong to the analysed public code,
                        # not to the workstation that built this release.
                        # Credential rules remain active for every table.
                        if normalized_relative in SOURCE_LITERAL_TABLES and rule in PATH_RULES:
                            continue
                        if pattern.search(line):
                            findings.append({"path": relative, "line": line_number, "rule": rule})
        except UnicodeDecodeError:
            findings.append({"path": relative, "line": 0, "rule": "declared_text_not_utf8"})
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path("release_candidate_v0.2"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    relative_paths = [path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()]
    findings = scan_paths(root, relative_paths)
    result = {
        "status": "PASS" if not findings else "FAIL",
        "files_considered": len(relative_paths),
        "findings": findings,
    }
    if args.output:
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
