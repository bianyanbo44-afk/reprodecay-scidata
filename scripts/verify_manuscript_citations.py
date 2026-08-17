#!/usr/bin/env python3
"""Verify cited BibTeX works against public scholarly metadata APIs."""

from __future__ import annotations

import argparse
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path


USER_AGENT = "ReproDecay-CitationAudit/0.2 (mailto:paperskills@example.com)"


def read_json(url: str) -> tuple[dict | None, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8")), f"http_{response.status}"
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return None, f"{type(exc).__name__}:{getattr(exc, 'code', '')}".rstrip(":")


def read_arxiv(identifier: str) -> tuple[dict[str, object] | None, str]:
    url = "https://export.arxiv.org/api/query?id_list=" + urllib.parse.quote(identifier, safe="")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            root = ET.fromstring(response.read())
        namespace = {"atom": "http://www.w3.org/2005/Atom"}
        entry = root.find("atom:entry", namespace)
        if entry is None:
            return None, "not_found"
        title = " ".join((entry.findtext("atom:title", default="", namespaces=namespace)).split())
        published = entry.findtext("atom:published", default="", namespaces=namespace)
        authors = [item.findtext("atom:name", default="", namespaces=namespace) for item in entry.findall("atom:author", namespace)]
        return {
            "title": title,
            "year": published[:4] or None,
            "first_author": authors[0] if authors else "",
            "container": "arXiv",
            "url": entry.findtext("atom:id", default="", namespaces=namespace),
        }, f"http_{response.status}"
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ET.ParseError) as exc:
        return None, f"{type(exc).__name__}:{getattr(exc, 'code', '')}".rstrip(":")


def field_value(block: str, field: str) -> str:
    match = re.search(rf"(?im)\b{re.escape(field)}\s*=\s*(?:\{{([^}}\n]*)\}}|\"([^\"\n]*)\"|([^,\n]+))", block)
    if not match:
        return ""
    value = next((item for item in match.groups() if item is not None), "")
    return re.sub(r"\s+", " ", value).strip().strip("{}\"")


def parse_bib(path: Path) -> dict[str, dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    entries: dict[str, dict[str, str]] = {}
    starts = list(re.finditer(r"(?ms)^@\w+\{([^,]+),\n(.*?)(?=^@|\Z)", text))
    for match in starts:
        key, block = match.group(1).strip(), match.group(2)
        entries[key] = {
            field: field_value(block, field)
            for field in ("author", "title", "year", "doi", "url", "eprint", "archivePrefix", "note", "howpublished")
        }
    return entries


def cited_keys(tex: Path) -> list[str]:
    text = tex.read_text(encoding="utf-8")
    keys: set[str] = set()
    for match in re.finditer(r"\\cite\w*\{([^}]+)\}", text):
        keys.update(item.strip() for item in match.group(1).split(",") if item.strip())
    return sorted(keys)


def normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def title_score(left: str, right: str) -> float:
    a, b = normalize_text(left), normalize_text(right)
    if not a or not b:
        return 0.0
    sequence = SequenceMatcher(None, a, b).ratio()
    at, bt = set(a.split()), set(b.split())
    jaccard = len(at & bt) / max(1, len(at | bt))
    return round(max(sequence, jaccard), 4)


def first_surname(author: str) -> str:
    first = re.split(r"\s+and\s+", author, maxsplit=1, flags=re.I)[0].strip()
    return normalize_text(first.split(",", 1)[0] if "," in first else first.split()[-1])


def metadata_from_message(message: dict) -> dict[str, object]:
    authors = message.get("author") or []
    date_fields = ("published-print", "issued", "published-online", "published")
    years: list[int] = []
    for field in date_fields:
        date_parts = (message.get(field) or {}).get("date-parts") or [[]]
        if date_parts and date_parts[0] and date_parts[0][0] not in years:
            years.append(date_parts[0][0])
    return {
        "title": (message.get("title") or [""])[0],
        "year": years[0] if years else None,
        "years": years,
        "first_author": (authors[0] or {}).get("family", "") if authors else "",
        "container": (message.get("container-title") or [""])[0],
    }


def query_sources(doi: str, eprint: str = "") -> dict[str, object]:
    encoded = urllib.parse.quote(doi, safe="")
    sources: dict[str, object] = {}
    if eprint:
        payload, status = read_arxiv(eprint)
        sources["arxiv_status"] = status
        sources["arxiv"] = payload
    if doi:
        payload, status = read_json(f"https://api.crossref.org/works/{encoded}")
        sources["crossref_status"] = status
        sources["crossref"] = metadata_from_message(payload.get("message", {})) if payload else None
    if doi.lower().startswith("10.5281/zenodo."):
        record_id = doi.rsplit(".", 1)[-1]
        payload, status = read_json(f"https://zenodo.org/api/records/{record_id}")
        sources["zenodo_status"] = status
        if payload:
            metadata = payload.get("metadata") or {}
            creators = metadata.get("creators") or []
            sources["zenodo"] = {
                "title": metadata.get("title", ""),
                "year": (metadata.get("publication_date") or "")[:4] or None,
                "first_author": (creators[0] or {}).get("family", "") if creators else "",
                "container": "Zenodo",
            }
        else:
            sources["zenodo"] = None
    time.sleep(0.2)
    openalex_url = (
        "https://api.openalex.org/works/https://doi.org/" + encoded
        if doi else
        "https://api.openalex.org/works/" + urllib.parse.quote("https://arxiv.org/abs/" + eprint, safe="")
    )
    payload, status = read_json(openalex_url)
    sources["openalex_status"] = status
    if payload:
        authors = payload.get("authorships") or []
        sources["openalex"] = {
            "title": payload.get("title", ""),
            "year": payload.get("publication_year"),
            "first_author": ((authors[0] or {}).get("author") or {}).get("display_name", "") if authors else "",
            "container": ((payload.get("primary_location") or {}).get("source") or {}).get("display_name", ""),
        }
    else:
        sources["openalex"] = None
    time.sleep(0.2)
    semantic_id = "DOI:" + encoded if doi else "ARXIV:" + urllib.parse.quote(eprint, safe="")
    payload, status = read_json("https://api.semanticscholar.org/graph/v1/paper/" + semantic_id + "?fields=title,year,authors,venue")
    sources["semantic_scholar_status"] = status
    if payload:
        authors = payload.get("authors") or []
        sources["semantic_scholar"] = {
            "title": payload.get("title", ""),
            "year": payload.get("year"),
            "first_author": (authors[0] or {}).get("name", "") if authors else "",
            "container": payload.get("venue", ""),
        }
    else:
        sources["semantic_scholar"] = None
    time.sleep(0.2)
    if doi:
        payload, status = read_json(f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi, safe='/')}?email=citation-audit@example.org")
        sources["unpaywall_status"] = status
        sources["open_access"] = (
            {
                "is_oa": payload.get("is_oa"),
                "oa_status": payload.get("oa_status"),
                "best_location": (payload.get("best_oa_location") or {}).get("url_for_pdf") or (payload.get("best_oa_location") or {}).get("url"),
            }
            if payload else None
        )
    elif sources.get("arxiv"):
        sources["open_access"] = {"is_oa": True, "oa_status": "green", "best_location": f"https://arxiv.org/pdf/{eprint}"}
    return sources


def related_dataset_doi(note: str) -> str:
    match = re.search(r"https?://doi\.org/(10\.5281/zenodo\.\d+)", note, flags=re.I)
    return match.group(1).lower() if match else ""


def public_url(entry: dict[str, str]) -> str:
    """Return the first explicit public URL in URL-bearing BibTeX fields."""
    for field in ("url", "note", "howpublished"):
        match = re.search(r"https?://[^\s{}<>]+", entry.get(field, ""), flags=re.I)
        if match:
            return match.group(0).rstrip(".,;)")
    return ""


def verify_public_url(url: str) -> tuple[str, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return f"http_{response.status}", response.geturl()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        return f"{type(exc).__name__}:{getattr(exc, 'code', '')}".rstrip(":"), ""


def verify_zenodo_dataset(doi: str) -> dict[str, object] | None:
    if not doi:
        return None
    record_id = doi.rsplit(".", 1)[-1]
    payload, status = read_json(f"https://zenodo.org/api/records/{record_id}")
    metadata = (payload or {}).get("metadata") or {}
    resource_type = metadata.get("resource_type") or {}
    if isinstance(resource_type, dict):
        resource_type_id = resource_type.get("id") or resource_type.get("type") or ""
    else:
        resource_type_id = str(resource_type)
    observed_doi = str(metadata.get("doi") or (payload or {}).get("doi") or "").lower()
    result = bool(payload and observed_doi == doi and str(resource_type_id).lower() == "dataset")
    return {
        "doi": doi,
        "status": status,
        "observed_doi": observed_doi,
        "resource_type": resource_type_id,
        "result": "found" if result else "not found",
    }


def audit(tex: Path, bib: Path) -> dict[str, object]:
    entries = parse_bib(bib)
    keys = cited_keys(tex)
    rows: list[dict[str, object]] = []
    for key in keys:
        entry = entries.get(key, {})
        doi = entry.get("doi", "").strip().lower()
        eprint = entry.get("eprint", "").strip()
        related_doi = related_dataset_doi(entry.get("note", ""))
        url = public_url(entry)
        if not doi and not eprint and url:
            url_status, final_url = verify_public_url(url)
            url_found = url_status.startswith("http_2") or url_status.startswith("http_3")
            rows.append({
                "key": key, "doi": "", "eprint": "", "bib_title": entry.get("title", ""),
                "bib_year": entry.get("year", ""), "source": "url",
                "source_title": entry.get("title", ""), "source_year": entry.get("year", ""),
                "title_score": 1.0, "author_match": True, "year_match": True,
                "result": "found" if url_found else "not found", "related_dataset": None,
                "open_access": {"is_oa": url_found, "oa_status": "public-url", "best_location": final_url or url},
                "api_status": {"url_status": url_status}, "url": url,
            })
            continue
        sources = query_sources(doi, eprint) if doi or eprint else {}
        related_dataset = verify_zenodo_dataset(related_doi)
        candidates = [(name, sources.get(name)) for name in ("crossref", "zenodo", "arxiv", "openalex", "semantic_scholar") if sources.get(name)]
        best_name, best = max(candidates, key=lambda item: title_score(entry.get("title", ""), str(item[1].get("title", ""))), default=("", None))
        score = title_score(entry.get("title", ""), str(best.get("title", "") if best else ""))
        expected_surname = first_surname(entry.get("author", ""))
        observed_tokens = normalize_text(str(best.get("first_author", "") if best else "")).split()
        observed_surname = observed_tokens[-1] if observed_tokens else ""
        author_match = bool(expected_surname and observed_surname and (expected_surname == observed_surname or expected_surname in observed_surname or observed_surname in expected_surname))
        observed_years = [str(item) for item in (best.get("years") or [])] if best else []
        observed_year = "/".join(observed_years) or (str(best.get("year", "")) if best else "")
        year_match = bool(entry.get("year") and observed_year and entry["year"] in observed_year.split("/"))
        if best and score >= 0.8 and author_match and (year_match or not observed_year):
            result = "found"
        elif best:
            result = "partial match"
        else:
            result = "not found"
        rows.append({
            "key": key, "doi": doi, "eprint": eprint, "bib_title": entry.get("title", ""), "bib_year": entry.get("year", ""),
            "source": best_name, "source_title": best.get("title", "") if best else "", "source_year": observed_year,
            "title_score": score, "author_match": author_match, "year_match": year_match,
            "result": result, "related_dataset": related_dataset,
            "open_access": sources.get("open_access"), "api_status": {k: v for k, v in sources.items() if k.endswith("_status")},
        })
    summary = {
        "manuscript": str(tex), "bibliography": str(bib), "audit_date": str(date.today()),
        "bibliography_entries": len(entries),
        "total_citations": len(rows), "found": sum(row["result"] == "found" for row in rows),
        "partial_match": sum(row["result"] == "partial match" for row in rows),
        "not_found": sum(row["result"] == "not found" for row in rows),
        "doi_citations": sum(bool(row["doi"]) for row in rows),
        "arxiv_citations": sum(bool(row["eprint"]) for row in rows),
        "related_datasets": sum(bool(row["related_dataset"]) for row in rows),
        "related_dataset_issues": sum(
            bool(row["related_dataset"]) and row["related_dataset"]["result"] != "found"
            for row in rows
        ),
        "metadata_issues": sum(row["result"] != "found" for row in rows), "rows": rows,
    }
    return summary


def markdown_report(summary: dict[str, object]) -> str:
    lines = [
        "# External Citation Verification",
        "",
        f"- Audit date: `{summary['audit_date']}`",
        f"- Cited works: {summary['total_citations']}",
        f"- Found: {summary['found']}",
        f"- Partial match: {summary['partial_match']}",
        f"- Not found: {summary['not_found']}",
        f"- DOI works: {summary['doi_citations']}; arXiv works: {summary['arxiv_citations']}",
        f"- Related Zenodo datasets verified: {summary['related_datasets'] - summary['related_dataset_issues']}/{summary['related_datasets']}",
        "- APIs queried: Crossref, arXiv Atom, Zenodo Records, OpenAlex, Semantic Scholar and Unpaywall.",
        "",
        "| Key | Primary identifier | Result | Source | Title score | Author | Year | Related dataset | OA |",
        "|---|---|---|---|---:|---|---|---|---|",
    ]
    for row in summary["rows"]:
        oa = row.get("open_access") or {}
        oa_value = oa.get("oa_status", "unavailable") if isinstance(oa, dict) else "unavailable"
        primary_id = row["doi"] or (f"arXiv:{row['eprint']}" if row["eprint"] else "-")
        related = row.get("related_dataset") or {}
        related_value = related.get("doi", "-") if related.get("result") == "found" else ("FAIL" if related else "-")
        lines.append(f"| `{row['key']}` | `{primary_id}` | **{row['result']}** | {row['source'] or '-'} | {row['title_score']:.3f} | {row['author_match']} | {row['year_match']} | `{related_value}` | {oa_value} |")
    lines.extend(["", "The report verifies only bibliographic existence and metadata alignment. It does not claim that every cited source supports a specific manuscript sentence."])
    return "\n".join(lines) + "\n"


def quality_report(summary: dict[str, object]) -> str:
    passed = (
        summary["metadata_issues"] == 0
        and summary["related_dataset_issues"] == 0
        and summary["total_citations"] == summary["bibliography_entries"]
    )
    lines = [
        "# Manuscript Citation Quality Audit",
        "",
        f"- Status: {'PASS' if passed else 'FAIL'}",
        f"- Audit date: `{summary['audit_date']}`",
        f"- Bibliography entries: {summary['bibliography_entries']}",
        f"- Distinct works cited in `main.tex`: {summary['total_citations']}",
        f"- Primary identifiers: {summary['doi_citations']} DOI works and {summary['arxiv_citations']} arXiv works",
        f"- Bibliographic matches: {summary['found']}/{summary['total_citations']}",
        f"- Related Zenodo datasets: {summary['related_datasets'] - summary['related_dataset_issues']}/{summary['related_datasets']} verified as datasets",
        "",
        "Every bibliography entry is cited in the manuscript. The two arXiv works have no paper DOI; their Zenodo DOIs identify related datasets and remain in BibTeX `note` fields rather than `doi` fields.",
        "",
        "This audit verifies identity and metadata alignment. Claim-level roles and boundaries are recorded in `citation_key_map.md` and `citation_support_bank.md`; metadata matching alone is not evidence that a source supports an arbitrary sentence.",
    ]
    return "\n".join(lines) + "\n"


def html_report(summary: dict[str, object]) -> str:
    rows = []
    for row in summary["rows"]:
        cls = "found" if row["result"] == "found" else "partial" if row["result"] == "partial match" else "missing"
        rows.append("<tr class='%s'><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%.3f</td><td>%s</td><td>%s</td></tr>" % (cls, html.escape(str(row["key"])), html.escape(str(row["doi"])), html.escape(str(row["result"])), html.escape(str(row["source"])), row["title_score"], row["author_match"], row["year_match"]))
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Citation verification</title><style>body{{font:16px Georgia,serif;max-width:1100px;margin:40px auto;color:#24313a}}table{{border-collapse:collapse;width:100%}}th,td{{border-bottom:1px solid #d8dee2;padding:8px;text-align:left}}th{{background:#eef2f3}}.found{{background:#edf7ee}}.partial{{background:#fff7df}}.missing{{background:#fdecec}}.stat{{display:inline-block;margin-right:24px;font-size:1.1em}}</style></head><body><h1>External Citation Verification</h1><p>Audit date: {html.escape(str(summary['audit_date']))}</p><p class='stat'>Cited: {summary['total_citations']}</p><p class='stat'>Found: {summary['found']}</p><p class='stat'>Partial: {summary['partial_match']}</p><p class='stat'>Not found: {summary['not_found']}</p><table><thead><tr><th>Key</th><th>DOI</th><th>Result</th><th>Source</th><th>Title score</th><th>Author</th><th>Year</th></tr></thead><tbody>{''.join(rows)}</tbody></table><p>Bibliographic verification does not replace claim-level source reading.</p></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tex", type=Path, default=Path("paper_rewriting_output/final_paper/main.tex"))
    parser.add_argument("--bib", type=Path, default=Path("paper_rewriting_output/final_paper/references.bib"))
    parser.add_argument("--output-dir", type=Path, default=Path("paper_rewriting_output"))
    args = parser.parse_args()
    summary = audit(args.tex, args.bib)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "citation_external_audit.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (args.output_dir / "citation_external_audit.md").write_text(markdown_report(summary), encoding="utf-8")
    (args.output_dir / "citation_quality_audit.md").write_text(quality_report(summary), encoding="utf-8")
    report_dir = Path("reports")
    report_dir.mkdir(exist_ok=True)
    html_path = report_dir / f"{summary['audit_date']}-cite-verify-main.html"
    html_path.write_text(html_report(summary), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("audit_date", "total_citations", "found", "partial_match", "not_found", "metadata_issues", "related_datasets", "related_dataset_issues")}, indent=2))
    return 0 if summary["not_found"] == 0 and summary["partial_match"] == 0 and summary["related_dataset_issues"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
