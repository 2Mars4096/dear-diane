from __future__ import annotations

from types import SimpleNamespace

from dan.search import BeaconCorpusStore, backfill_beacon_corpus_from_tool_calls


def test_backfill_beacon_corpus_from_tool_calls(tmp_path) -> None:
    store = BeaconCorpusStore(tmp_path / "beacon.db", ttl_seconds=3600)
    tool_calls = [
        {
            "tool_name": "web_search",
            "result_data": {
                "query": "quarterly revenue",
                "search_result_set": {
                    "query": "quarterly revenue",
                    "results": [
                        {
                            "title": "Revenue report",
                            "url": "https://example.com/report",
                            "fetched_content": "Revenue grew 24% in 2025.",
                            "provider": "brave",
                        }
                    ],
                },
            },
        },
        {
            "tool_name": "web_fetch",
            "cap_result": SimpleNamespace(
                data={
                    "url": "https://example.com/page",
                    "content": "Beacon fetch backfill stores prior pages.",
                    "content_type": "text/html",
                }
            ),
        },
    ]

    summary = backfill_beacon_corpus_from_tool_calls(tool_calls, corpus_store=store)

    assert summary["saved_documents"] == 2
    assert store.stats()["documents"] == 2
    assert store.search("revenue 2025", limit=1)[0].url == "https://example.com/report"
