from __future__ import annotations

from dan.worker.organism_log import normalize_organism_log_rows
from dan.worker.organism_log_adapters import (
    OrganismLogImportConfig,
    detect_organism_log_adapter,
    normalize_import_rows,
    summarize_import_rows,
)


def test_generic_import_normalizes_model_and_tool_spans() -> None:
    raw_rows = [
        {
            "ts": "2026-04-18T00:00:00Z",
            "type": "llm_requested",
            "llm_call_id": "model-1",
            "run_id": "trace-1",
            "agent_id": "planner",
        },
        {
            "ts": "2026-04-18T00:00:02Z",
            "type": "llm_completed",
            "llm_call_id": "model-1",
            "run_id": "trace-1",
            "agent_id": "planner",
            "status": "completed",
        },
        {
            "ts": "2026-04-18T00:00:02Z",
            "type": "tool_started",
            "function_name": "web_search",
            "function_call_id": "tool-1",
            "parent_request_id": "model-1",
            "run_id": "trace-1",
            "agent_id": "researcher",
        },
        {
            "ts": "2026-04-18T00:00:05Z",
            "type": "tool_failed",
            "function_name": "web_search",
            "function_call_id": "tool-1",
            "parent_request_id": "model-1",
            "run_id": "trace-1",
            "agent_id": "researcher",
            "error": "429 rate limited",
        },
    ]

    assert detect_organism_log_adapter(raw_rows) == "generic_json"

    adapter, normalized_rows = normalize_import_rows(raw_rows)
    assert adapter == "generic_json"

    normalized = normalize_organism_log_rows(
        [{**row, "sequence": index + 1} for index, row in enumerate(normalized_rows)]
    )

    assert any(row.record_kind == "span" and row.event_family == "model" for row in normalized)
    assert any(row.record_kind == "span" and row.event_family == "tool" for row in normalized)
    assert any(row.parent_span_id == "model-1" for row in normalized if row.event_family == "tool")

    summary = summarize_import_rows(raw_rows)
    assert summary["adapter"] == "generic_json"
    assert summary["span_count"] >= 2
    assert any(
        str(item["status"]) == "failed" and str(item["event_family"]) == "tool"
        for item in summary["blocking_spans"]
    )


def test_generic_import_synthesizes_closed_span_from_single_row() -> None:
    raw_rows = [
        {
            "name": "validation",
            "call_id": "span-1",
            "started_at": "2026-04-18T00:00:00Z",
            "ended_at": "2026-04-18T00:00:03Z",
            "run_id": "trace-closed",
            "worker": "validator",
            "status": "completed",
            "summary": "Validate the patch",
        }
    ]

    _, normalized_rows = normalize_import_rows(raw_rows)
    assert len(normalized_rows) == 2

    normalized = normalize_organism_log_rows(
        [{**row, "sequence": index + 1} for index, row in enumerate(normalized_rows)]
    )
    spans = [row for row in normalized if row.record_kind == "span"]
    assert len(spans) == 1
    assert spans[0].duration_ms == 3000
    assert spans[0].summary == "Validate the patch"


def test_passthrough_import_preserves_existing_stream_kind() -> None:
    raw_rows = [
        {
            "schema": "organism_log_v1",
            "stream": "control_plane",
            "product": "external_product",
            "timestamp": "2026-04-18T00:00:00Z",
            "event": "provider.build.started",
            "row_kind": "span_start",
            "span_kind": "control_stage",
            "span_id": "provider-build-1",
        }
    ]

    adapter, normalized_rows = normalize_import_rows(
        raw_rows,
        config=OrganismLogImportConfig(),
    )

    assert adapter == "organism_log_v1"
    assert normalized_rows[0]["stream"] == "control_plane"
    assert normalized_rows[0]["product"] == "external_product"
