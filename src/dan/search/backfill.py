"""Backfill helpers that seed Beacon corpus records from prior DAN tool output."""

from __future__ import annotations

from typing import Any, Iterable

from .corpus import BeaconCorpusStore, get_default_beacon_corpus_store


def _iter_capability_payloads(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("result_data"), dict):
            payloads.append(dict(item["result_data"]))
            continue
        cap_result = item.get("cap_result")
        data = getattr(cap_result, "data", None)
        if isinstance(data, dict):
            payloads.append(dict(data))
            continue
        if isinstance(item.get("data"), dict):
            payloads.append(dict(item["data"]))
    return payloads


def backfill_beacon_corpus_from_payloads(
    payloads: Iterable[dict[str, Any]],
    *,
    corpus_store: BeaconCorpusStore | None = None,
) -> dict[str, int]:
    store = corpus_store or get_default_beacon_corpus_store()
    saved_documents = 0
    saved_chunks = 0

    for payload in payloads:
        search_result_set = payload.get("search_result_set")
        if isinstance(search_result_set, dict):
            query = str(search_result_set.get("query", "") or "").strip()
            for result in search_result_set.get("results") or []:
                if not isinstance(result, dict):
                    continue
                content = str(result.get("fetched_content", "") or "").strip()
                url = str(result.get("url", "") or "").strip()
                if not url or not content:
                    continue
                record = store.upsert_document(
                    url=url,
                    title=str(result.get("title", "") or "").strip(),
                    content=content,
                    query=query,
                    source_type="chat_search_backfill",
                    metadata={
                        "provider": str(result.get("provider", "") or "").strip(),
                        "source_query": str(result.get("source_query", "") or "").strip() or query,
                    },
                )
                if record is not None:
                    saved_documents += 1
                    saved_chunks += record.chunk_count

        for fetched in payload.get("fetched_results") or []:
            if not isinstance(fetched, dict) or not fetched.get("success"):
                continue
            content = str(
                fetched.get("content", "")
                or fetched.get("fetched_content", "")
                or ""
            ).strip()
            url = str(fetched.get("url", "") or "").strip()
            if not url or not content:
                continue
            record = store.upsert_document(
                url=url,
                title=str(fetched.get("title", "") or "").strip(),
                content=content,
                query=str(payload.get("query", "") or "").strip(),
                source_type="chat_fetch_backfill",
                metadata={"fetch_index": fetched.get("index")},
            )
            if record is not None:
                saved_documents += 1
                saved_chunks += record.chunk_count

        if "url" in payload and "content" in payload:
            content = str(payload.get("content", "") or "").strip()
            url = str(payload.get("url", "") or "").strip()
            if url and content:
                record = store.upsert_document(
                    url=url,
                    title=str(payload.get("title", "") or "").strip(),
                    content=content,
                    query=url,
                    source_type="web_fetch_backfill",
                    metadata={
                        "fetch_via": str(payload.get("fetch_via", "") or "").strip(),
                        "content_type": str(payload.get("content_type", "") or "").strip(),
                    },
                )
                if record is not None:
                    saved_documents += 1
                    saved_chunks += record.chunk_count

    return {"saved_documents": saved_documents, "saved_chunks": saved_chunks}


def backfill_beacon_corpus_from_tool_calls(
    tool_calls: Iterable[dict[str, Any]],
    *,
    corpus_store: BeaconCorpusStore | None = None,
) -> dict[str, int]:
    return backfill_beacon_corpus_from_payloads(
        _iter_capability_payloads(tool_calls),
        corpus_store=corpus_store,
    )
