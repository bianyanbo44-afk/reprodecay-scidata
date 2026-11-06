#!/usr/bin/env python3
"""Extend the frozen 2018-2020 JOSS frame with the 2021-2022 publication years.

The v0.1 Crossref snapshot is reused byte-for-byte for 2018-2020 so that the
original 940 records keep their screening history. Only the extension window is
fetched, using Crossref cursor paging. The combined frame and a manifest that
records both inputs, the request URLs and their hashes are written to the
output directory. Metadata-only: nothing is cloned, installed or executed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from build_candidate_frame import JOSS_ISSN, normalized_doi, publication_date, request_json

SELECT_FIELDS = "DOI,title,published,URL,relation,resource,license"


def fetch_window(start: str, end: str) -> tuple[list[dict[str, Any]], list[str]]:
    items: list[dict[str, Any]] = []
    urls: list[str] = []
    cursor = "*"
    while True:
        params = {
            "filter": f"from-pub-date:{start},until-pub-date:{end}",
            "rows": "1000",
            "select": SELECT_FIELDS,
            "cursor": cursor,
        }
        url = f"https://api.crossref.org/journals/{JOSS_ISSN}/works?{urllib.parse.urlencode(params)}"
        payload, _ = request_json(url)
        urls.append(url)
        page = payload["message"]["items"]
        items.extend(page)
        cursor = payload["message"].get("next-cursor") or ""
        if not page or not cursor or len(items) >= int(payload["message"].get("total-results", 0)):
            return items, urls


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen", default="data/candidate_frame_v0.1/crossref_joss_2018_2020.json")
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default="2022-12-31")
    parser.add_argument("--output-dir", default="data/candidate_frame_v0.3")
    args = parser.parse_args()

    frozen_path = Path(args.frozen)
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    extension, urls = fetch_window(args.start, args.end)

    frozen_dois = {normalized_doi(str(item.get("DOI", ""))) for item in frozen}
    extension_dois = [normalized_doi(str(item.get("DOI", ""))) for item in extension]
    if len(extension_dois) != len(set(extension_dois)):
        raise ValueError("extension_duplicate_doi")
    overlap = sorted(frozen_dois & set(extension_dois))
    if overlap:
        raise ValueError(f"extension_overlaps_frozen_frame:{overlap[:5]}")
    out_of_window = [
        doi for doi, item in zip(extension_dois, extension)
        if not (args.start <= publication_date(item) <= args.end)
    ]

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    extension_path = output_dir / f"crossref_joss_{args.start[:4]}_{args.end[:4]}.json"
    combined_path = output_dir / f"crossref_joss_2018_{args.end[:4]}.json"
    extension_path.write_text(json.dumps(extension, ensure_ascii=False, indent=2), encoding="utf-8")
    combined_path.write_text(json.dumps(frozen + extension, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest = {
        "schema_version": "0.3",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "frozen_input": str(frozen_path).replace("\\", "/"),
        "frozen_input_sha256": sha256_bytes(frozen_path.read_bytes()),
        "frozen_records": len(frozen),
        "extension_window": {"from_pub_date": args.start, "until_pub_date": args.end},
        "extension_request_urls": urls,
        "extension_output": str(extension_path).replace("\\", "/"),
        "extension_sha256": sha256_bytes(extension_path.read_bytes()),
        "extension_records": len(extension),
        "extension_records_outside_window_by_published_date": out_of_window,
        "combined_output": str(combined_path).replace("\\", "/"),
        "combined_sha256": sha256_bytes(combined_path.read_bytes()),
        "combined_records": len(frozen) + len(extension),
    }
    (output_dir / "frame_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("frozen_records", "extension_records", "combined_records")}))
    print(json.dumps({"outside_window": len(out_of_window)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
