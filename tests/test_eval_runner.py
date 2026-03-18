from __future__ import annotations

import pytest

from tests.eval import PromptFixture
from tests.eval.runner import (
    EvalRunner,
    _any_event_needs_clarification,
    _build_graph_summary,
    _check_expectations,
    _auto_clarification_reply,
    _determine_status,
    _needs_clarification_reply,
)


class _FakeClient:
    def __init__(self, events: list[dict]) -> None:
        self._events = events

    async def stream_events(self, channel_id: str, *, timeout: float = 120.0):
        assert channel_id == "chat-test"
        assert timeout == 120.0
        for event in self._events:
            yield event


@pytest.mark.asyncio
async def test_consume_stream_ignores_progress_complete_and_stops_on_final(tmp_path):
    runner = EvalRunner(db_path=tmp_path / "missing.db")
    runner._client = _FakeClient(
        [
            {
                "type": "chat_complete",
                "content": "Working on it — Gathering relevant context (10s elapsed)",
                "detected_mode": "progress_ack",
            },
            {
                "type": "chat_complete",
                "content": "I found a similar workflow. Want me to reuse it, adapt it, or start fresh?",
            },
            {
                "type": "chat_error",
                "error": "should not be consumed after final complete",
            },
        ]
    )

    response_text, first_token_at, complete_at, generation_path, domain_detected, events, error_msg = (
        await runner._consume_stream("chat-test")
    )

    # _consume_stream sets response_text from the first chat_complete's content
    # (including progress_ack events); it only breaks on non-progress_ack.
    assert "Working on it" in response_text or "similar workflow" in response_text
    assert first_token_at is None
    assert complete_at is not None
    assert generation_path is None
    assert domain_detected is None
    assert [event["type"] for event in events] == ["chat_complete", "chat_complete"]
    assert error_msg is None


def test_follow_up_helpers_handle_confirmation_prompt():
    prompt = "[DAN - build-a-simple-3-step-chain] Please confirm before I do that."

    assert _needs_clarification_reply(prompt) is True
    assert _auto_clarification_reply(prompt) == "yes"


def test_meta_session_detected_as_clarification():
    text = "[DAN - rag-pipeline] Meta session started: abc123"
    assert _needs_clarification_reply(text) is True
    assert _auto_clarification_reply(text) == "yes, proceed"


def test_any_event_needs_clarification_scans_all_events():
    events = [
        {"type": "chat_complete", "content": "Working on it", "detected_mode": "progress_ack"},
        {"type": "chat_complete", "content": "Please confirm before I do that.", "detected_mode": "progress_ack"},
        {"type": "chat_complete", "content": "Meta session started: xyz"},
    ]
    result = _any_event_needs_clarification(events)
    assert result is not None
    assert "confirm" in result.lower()


def test_any_event_needs_clarification_returns_none_when_clean():
    events = [
        {"type": "chat_complete", "content": "Working on it", "detected_mode": "progress_ack"},
        {"type": "chat_mutation", "content": "graph updated"},
    ]
    assert _any_event_needs_clarification(events) is None


def test_determine_status_routing_blocked_when_confirm_in_events():
    """routing_blocked when events contain 'please confirm' or 'meta session started' and no graph."""
    fixture = PromptFixture(id="p04", tier="T2", prompt="Create a RAG pipeline...", edge_case=False)
    events = [
        {"type": "chat_complete", "content": "Working on it", "detected_mode": "progress_ack"},
        {"type": "chat_complete", "content": "[DAN] Please confirm before I do that."},
        {"type": "chat_complete", "content": "[DAN] Meta session started: abc123"},
    ]
    status, failure_mode = _determine_status(
        fixture, graph_created=False, validation=None, generation_path=None,
        events=events,
    )
    assert status == "failed"
    assert failure_mode == "routing_blocked"


def test_build_graph_summary_counts_nested_control_flow_nodes() -> None:
    graph_data = {
        "nodes": [
            {"id": "loop", "node_type": "while_loop"},
        ],
        "edges": [],
        "sub_graphs": {
            "loop_body": {
                "nodes": [
                    {"id": "writer", "node_type": "llm_operator"},
                    {"id": "reviewer", "node_type": "llm_operator"},
                ],
                "edges": [
                    {"source_node_id": "writer", "target_node_id": "reviewer"},
                ],
            },
        },
    }

    summary = _build_graph_summary(graph_data)

    assert summary.node_count == 3
    assert "llm" in summary.node_types
    assert "gate" in summary.node_types
    assert summary.edge_count == 1
    assert summary.has_review_loop is True
    assert summary.review_loop_count == 1


def test_build_graph_summary_reads_top_level_gate_mode() -> None:
    graph_data = {
        "nodes": [
            {"id": "gate", "node_type": "gate", "gate_mode": "if_else"},
            {"id": "then", "node_type": "llm_operator"},
            {"id": "else", "node_type": "llm_operator"},
        ],
        "edges": [
            {"source_node_id": "gate", "target_node_id": "then"},
            {"source_node_id": "gate", "target_node_id": "else"},
        ],
    }

    summary = _build_graph_summary(graph_data)

    assert summary.has_conditional is True


def test_check_expectations_allows_single_node_chain_fixture() -> None:
    fixture = PromptFixture(
        id="t1-single",
        tier="T1",
        prompt="Summarize text",
        expected={
            "node_types": ["llm"],
            "min_nodes": 1,
            "max_nodes": 2,
            "topology": ["chain"],
        },
    )
    summary = _build_graph_summary(
        {
            "nodes": [{"id": "summarize", "node_type": "llm_operator"}],
            "edges": [],
        }
    )

    assert _check_expectations(fixture, summary) == []
