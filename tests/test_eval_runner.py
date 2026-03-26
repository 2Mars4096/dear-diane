from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tests.eval import ExecutionResult, PromptFixture, ValidationResult
from tests.eval.metrics import EvalLogger
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


class _TrackingEvalClient:
    def __init__(self, channel_events: dict[str, list[dict]]) -> None:
        self._channel_events = channel_events
        self.send_calls: list[dict] = []

    async def create_graph(self, graph_id: str) -> dict:
        return {"graph_id": graph_id}

    async def send_message(self, workflow_id: str, message: str, **kwargs: object) -> dict:
        channel_id = f"chat-test-{len(self.send_calls) + 1}"
        self.send_calls.append(
            {
                "workflow_id": workflow_id,
                "message": message,
                **kwargs,
            }
        )
        return {"stream_channel_id": channel_id}

    async def stream_events(self, channel_id: str, *, timeout: float = 120.0):
        assert timeout == 120.0
        for event in self._channel_events[channel_id]:
            yield event


@pytest.mark.asyncio
async def test_run_single_preserves_workflow_contract_surface_context_on_clarification_reply(
    tmp_path,
):
    fixture = PromptFixture(id="clarify", tier="T1", prompt="Build a workflow")
    runner = EvalRunner(
        db_path=tmp_path / "missing.db",
        audit_dir=tmp_path / "audit",
        delay=0.0,
        workflow_contract="disabled",
    )
    runner._client = _TrackingEvalClient(
        {
            "chat-test-1": [
                {
                    "type": "chat_complete",
                    "content": "Please confirm before I do that.",
                },
            ],
            "chat-test-2": [
                {
                    "type": "chat_complete",
                    "content": "Done.",
                },
            ],
        }
    )
    runner._inspect_graph = AsyncMock(return_value=(False, None, None))
    runner._query_telemetry = lambda *_args, **_kwargs: (None, None, None)
    runner._query_audit = AsyncMock(
        return_value={
            "prompt_module_ids": ["workflow_generation_contract"],
            "workflow_guidance_injected": False,
            "workflow_guidance_surface": "",
        }
    )

    logger = EvalLogger(output_dir=tmp_path)
    record = await runner.run_single(
        fixture,
        "build",
        logger,
        workflow_contract_variant="disabled",
    )

    assert len(runner._client.send_calls) == 2
    assert runner._client.send_calls[0]["surface_context"] == {
        "workflow_generation_contract_enabled": False
    }
    assert runner._client.send_calls[1]["surface_context"] == {
        "workflow_generation_contract_enabled": False
    }
    assert runner._client.send_calls[1]["message"] == "yes"
    assert record.audit_found is True
    assert record.prompt_module_ids == ["workflow_generation_contract"]
    assert record.workflow_guidance_injected is False


@pytest.mark.asyncio
async def test_run_multi_turn_preserves_workflow_contract_surface_context_across_turns(
    tmp_path,
):
    fixture = PromptFixture(
        id="multi",
        tier="T2",
        prompt="Build a workflow",
        multi_turn_follow_ups=["Add a review step."],
    )
    runner = EvalRunner(
        db_path=tmp_path / "missing.db",
        audit_dir=tmp_path / "audit",
        delay=0.0,
        workflow_contract="enabled",
    )
    runner._client = _TrackingEvalClient(
        {
            "chat-test-1": [
                {
                    "type": "chat_complete",
                    "content": "Built the workflow.",
                },
            ],
            "chat-test-2": [
                {
                    "type": "chat_complete",
                    "content": "Added the review step.",
                },
            ],
        }
    )
    runner._inspect_graph = AsyncMock(return_value=(False, None, None))
    runner._query_telemetry = lambda *_args, **_kwargs: (None, None, None)
    runner._query_audit = AsyncMock(
        side_effect=[
            {
                "prompt_module_ids": ["workflow_generation_contract"],
                "workflow_guidance_injected": True,
                "workflow_guidance_surface": "build",
            },
            {
                "prompt_module_ids": ["workflow_generation_contract"],
                "workflow_guidance_injected": True,
                "workflow_guidance_surface": "repair",
            },
        ]
    )

    logger = EvalLogger(output_dir=tmp_path)
    records = await runner.run_multi_turn(
        fixture,
        "build",
        logger,
        workflow_contract_variant="enabled",
    )

    assert len(records) == 2
    assert [record.workflow_contract_variant for record in records] == [
        "enabled",
        "enabled",
    ]
    assert [record.workflow_guidance_surface for record in records] == [
        "build",
        "repair",
    ]
    assert [call["surface_context"] for call in runner._client.send_calls] == [
        {"workflow_generation_contract_enabled": True},
        {"workflow_generation_contract_enabled": True},
    ]


@pytest.mark.asyncio
async def test_consume_stream_ignores_progress_complete_and_stops_on_final(tmp_path):
    runner = EvalRunner(db_path=tmp_path / "missing.db", audit_dir=tmp_path / "audit")
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


@pytest.mark.parametrize(
    ("execution_status", "expected_failure_mode"),
    [
        ("failed", "execution_failed"),
        ("timeout", "execution_timeout"),
        ("error", "execution_error"),
    ],
)
def test_determine_status_fails_when_execution_does_not_complete(
    execution_status: str,
    expected_failure_mode: str,
):
    fixture = PromptFixture(id="exec-fail", tier="T1", prompt="Run this workflow")
    validation = ValidationResult(passed=True, run_ready=True)
    status, failure_mode = _determine_status(
        fixture,
        graph_created=True,
        validation=validation,
        generation_path="intent_compile",
        events=[],
        execution=ExecutionResult(status=execution_status, error="boom"),
    )
    assert status == "failed"
    assert failure_mode == expected_failure_mode


@pytest.mark.parametrize(
    ("run_readiness_failure_mode", "issues", "expected_failure_mode"),
    [
        ("unresolved_code", ["Code node 'compute' has empty code, so it is not run-ready."], "unresolved_code"),
        (
            "non_runnable_code",
            ["Code node 'compute' contains placeholder status payload code instead of runnable logic."],
            "non_runnable_code",
        ),
    ],
)
def test_determine_status_uses_code_specific_run_readiness_failure_modes(
    run_readiness_failure_mode: str,
    issues: list[str],
    expected_failure_mode: str,
) -> None:
    fixture = PromptFixture(id="code-fail", tier="T2", prompt="Compute metrics")
    validation = ValidationResult(
        passed=True,
        run_ready=False,
        run_readiness_issues=issues,
        run_readiness_failure_mode=run_readiness_failure_mode,
    )

    status, failure_mode = _determine_status(
        fixture,
        graph_created=True,
        validation=validation,
        generation_path="codegen",
        events=[],
    )

    assert status == "failed"
    assert failure_mode == expected_failure_mode


def test_determine_status_falls_back_to_generic_not_run_ready() -> None:
    fixture = PromptFixture(id="not-ready", tier="T1", prompt="Build a workflow")
    validation = ValidationResult(
        passed=True,
        run_ready=False,
        run_readiness_issues=["Workflow has no entry points, so it is not run-ready."],
    )

    status, failure_mode = _determine_status(
        fixture,
        graph_created=True,
        validation=validation,
        generation_path="intent_compile",
        events=[],
    )

    assert status == "failed"
    assert failure_mode == "not_run_ready"


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
