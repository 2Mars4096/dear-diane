"""Paper search (Semantic Scholar) and citation verification tools."""

from __future__ import annotations

import asyncio
import re
import urllib.parse
from typing import Any

from ._shared import _http_get_json


async def _search_papers(
    query: str,
    num_results: int = 8,
    aspect: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    fields = "title,year,venue,authors,citationCount,externalIds,paperId,abstract"
    params = urllib.parse.urlencode(
        {"query": query, "limit": max(1, min(int(num_results), 20)), "fields": fields}
    )
    url = f"https://api.semanticscholar.org/graph/v1/paper/search?{params}"
    papers: list[dict[str, Any]] = []
    error_text = ""
    try:
        data = await asyncio.to_thread(_http_get_json, url)
        for row in data.get("data", []):
            if not isinstance(row, dict):
                continue
            ext = row.get("externalIds") or {}
            if not isinstance(ext, dict):
                ext = {}
            papers.append(
                {
                    "title": str(row.get("title", "")),
                    "year": row.get("year"),
                    "venue": str(row.get("venue", "")),
                    "abstract": str(row.get("abstract", "")),
                    "citation_count": int(row.get("citationCount", 0) or 0),
                    "paper_id": str(row.get("paperId", "")),
                    "doi": str(ext.get("DOI", "")),
                }
            )
    except Exception as exc:
        error_text = str(exc)

    return {
        "aspect": aspect,
        "query": query,
        "papers": papers,
        "paper_count": len(papers),
        "error": error_text,
    }


async def _citation_verifier(key_papers: list[Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    papers = key_papers if isinstance(key_papers, list) else []
    verified: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for row in papers:
        if not isinstance(row, dict):
            invalid.append({"paper": str(row), "reason": "Not an object"})
            continue
        title = str(row.get("title", "")).strip()
        if not title:
            invalid.append({"paper": row, "reason": "Missing title"})
            continue
        key = re.sub(r"\s+", " ", title.lower())
        if key in seen_titles:
            continue
        seen_titles.add(key)
        doi = str(row.get("doi", "")).strip()
        paper_id = str(row.get("paper_id", "")).strip() or str(row.get("paperId", "")).strip()
        if doi or paper_id:
            verified.append(
                {
                    "title": title,
                    "year": row.get("year"),
                    "venue": row.get("venue", ""),
                    "doi": doi,
                    "paper_id": paper_id,
                }
            )
        else:
            invalid.append({"paper": row, "reason": "Missing DOI/paper_id"})
    return {
        "verified_papers": verified,
        "invalid_citations": invalid,
        "verification_notes": f"Verified {len(verified)}; invalid {len(invalid)}.",
    }
