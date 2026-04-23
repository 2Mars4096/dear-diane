from __future__ import annotations

import asyncio
import json

from dan.cli.code import CodeRunEventLogger
from dan.cli.research import ResearchEventLogger
from dan.cli.super_organism import SuperRunEventLogger
from dan.worker.core.contracts import ExecutionRequest
from dan.worker.core.executor import WorkerCoreExecutor
from dan.worker.core.interfaces import CallbackEventSink, CompletionRequest, CompletionResponse
from dan.worker.core.model import WorkerDefinition
from dan.worker.organism_log import read_organism_log_rows
from dan.worker.organism_log_analysis import analyze_organism_log


def test_code_run_event_logger_uses_shared_organism_log_schema(tmp_path) -> None:
    logger = CodeRunEventLogger(
        path=tmp_path / "code-events.jsonl",
        session_id="code-session-1",
        turn_id="2",
        task_id="code-task-2",
        organism_id="coding-organism",
        organ_id="coding-build",
    )

    logger.emit({"event": "run.log.started", "objective": "Fix the CLI bug."})
    logger.emit(
        {
            "event": "run.log.completed",
            "status": "completed",
            "trace_id": "trace-code-2",
        }
    )
    logger.close()

    rows = read_organism_log_rows(logger.path)

    assert rows[0].product == "dan_code"
    assert rows[0].stream_kind == "bounded_run"
    assert rows[0].session_id == "code-session-1"
    assert rows[-1].trace_id == "trace-code-2"
    assert any(row.record_kind == "span" and row.event_family == "run_log" for row in rows)


def test_code_run_event_logger_supports_shared_control_plane_stream(tmp_path) -> None:
    logger = CodeRunEventLogger(
        path=tmp_path / "code-control.jsonl",
        stream_kind="control_plane",
        session_id="code-session-control",
        organism_id="coding-organism",
        organ_id="dan-code.control-plane",
    )

    logger.emit({"event": "orchestrator.turn.decision.started"})
    logger.emit({"event": "orchestrator.turn.decision.completed", "action": "code"})
    logger.close()

    rows = read_organism_log_rows(logger.path)

    assert rows[0].product == "dan_code"
    assert rows[0].stream_kind == "control_plane"
    assert rows[0].session_id == "code-session-control"
    assert any(
        row.record_kind == "span" and row.event_family == "orchestrator"
        for row in rows
    )


def test_research_event_logger_supports_shared_control_plane_stream(tmp_path) -> None:
    logger = ResearchEventLogger(
        path=tmp_path / "research-control.jsonl",
        stream_kind="control_plane",
        session_id="research-session-1",
    )

    logger.emit({"event": "provider.build.started"})
    logger.emit({"event": "provider.build.completed", "model": "gpt-test"})
    logger.close()

    rows = read_organism_log_rows(logger.path)

    assert rows[0].product == "dan_research"
    assert rows[0].stream_kind == "control_plane"
    assert rows[0].session_id == "research-session-1"
    assert any(
        row.record_kind == "span" and row.event_family == "provider_build"
        for row in rows
    )


def test_super_run_event_logger_uses_shared_bounded_run_schema(tmp_path) -> None:
    logger = SuperRunEventLogger(
        path=tmp_path / "super-events.jsonl",
        session_id="super-session-1",
        turn_id="1",
        task_id="super-task-1",
        organism_id="super-dan-20",
        organ_id="super-dan.live",
        trace_id="trace:super-1",
    )

    logger.emit({"event": "run.log.started", "objective": "Build the website."})
    logger.emit({"event": "provider.build.started", "model": "gpt-test"})
    logger.emit({"event": "provider.build.completed", "model": "gpt-test"})
    logger.emit({"event": "run.log.completed", "status": "completed"})
    logger.close()

    rows = read_organism_log_rows(logger.path)

    assert rows[0].product == "dan_super"
    assert rows[0].stream_kind == "bounded_run"
    assert rows[0].session_id == "super-session-1"
    assert rows[0].trace_id == "trace:super-1"
    assert any(
        row.record_kind == "span" and row.event_family == "provider_build"
        for row in rows
    )


class _SequentialCompletionProvider:
    def __init__(self, *responses: str) -> None:
        self._responses = list(responses)

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        if not self._responses:
            raise AssertionError("No completion response queued")
        return CompletionResponse(
            text=self._responses.pop(0),
            raw={"remaining": len(self._responses)},
        )


def test_code_run_event_logger_captures_contract_spans_via_callback_sink(tmp_path) -> None:
    logger = CodeRunEventLogger(
        path=tmp_path / "code-contract-events.jsonl",
        session_id="code-session-contract",
        turn_id="3",
        task_id="code-task-3",
        organism_id="coding-organism",
        organ_id="coding-build",
        trace_id="trace:code-contract",
    )
    provider = _SequentialCompletionProvider(
        '{"action":"respond"}',
        '{"action":"respond","public_response":"patched"}',
    )
    worker = WorkerDefinition(id="structured-worker", model="stub-model")
    request = ExecutionRequest.from_harness(
        task="Return one structured coding response.",
        output_contract={
            "definition_of_done": "Return a structured coding decision.",
            "expected_return_shape": json.dumps(
                {
                    "action": "respond|clarify|code",
                    "public_response": "<required>",
                },
                sort_keys=True,
            ),
            "output_schema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["respond", "clarify", "code"]},
                    "public_response": {"type": "string"},
                },
                "required": ["action", "public_response"],
            },
        },
        metadata={
            "trace_id": "trace:code-contract",
            "organism_id": "coding-organism",
            "organ_id": "coding-build",
        },
    )

    asyncio.run(
        WorkerCoreExecutor(
            completion_provider=provider,
            event_sink=CallbackEventSink(logger.emit),
        ).execute(worker, request)
    )
    logger.close()

    rows = read_organism_log_rows(logger.path)
    validation_spans = [
        row for row in rows if row.record_kind == "span" and row.span_kind == "output_contract_validation"
    ]
    repair_spans = [
        row for row in rows if row.record_kind == "span" and row.span_kind == "output_contract_repair"
    ]

    assert len(validation_spans) == 2
    assert len(repair_spans) == 1
    assert any(row.status == "invalid" and row.parent_span_id.startswith("worker:") for row in validation_spans)
    assert any(
        row.status == "valid" and row.parent_span_id.startswith("contract-repair:")
        for row in validation_spans
    )
    assert repair_spans[0].status == "repaired"
    assert repair_spans[0].parent_span_id.startswith("worker:")


def test_code_run_event_log_analysis_answers_what_took_time(tmp_path) -> None:
    logger = CodeRunEventLogger(
        path=tmp_path / "code-analysis.jsonl",
        session_id="code-session-analysis",
        turn_id="1",
        task_id="code-task-1",
        organism_id="coding-organism",
        organ_id="coding-build",
        trace_id="trace:code-analysis",
    )
    logger.emit({"timestamp": "2026-04-18T00:00:00Z", "event": "run.log.started"})
    logger.emit(
        {
            "timestamp": "2026-04-18T00:00:00Z",
            "event": "worker.started",
            "worker_id": "coding-build.aggregation.lead",
            "handoff_packet_id": "packet:root",
        }
    )
    logger.emit(
        {
            "timestamp": "2026-04-18T00:00:01Z",
            "event": "model.requested",
            "worker_id": "coding-build.aggregation.lead",
            "handoff_packet_id": "packet:root",
            "model_call_id": "model-call:1",
            "model": "gpt-test",
        }
    )
    logger.emit(
        {
            "timestamp": "2026-04-18T00:00:02Z",
            "event": "tool.started",
            "worker_id": "coding-build.aggregation.lead",
            "handoff_packet_id": "packet:root",
            "tool_call_id": "tool-call:1",
            "tool_id": "file_read",
            "parent_model_call_id": "model-call:1",
        }
    )
    logger.emit(
        {
            "timestamp": "2026-04-18T00:00:04Z",
            "event": "tool.completed",
            "worker_id": "coding-build.aggregation.lead",
            "handoff_packet_id": "packet:root",
            "tool_call_id": "tool-call:1",
            "tool_id": "file_read",
            "parent_model_call_id": "model-call:1",
            "status": "completed",
        }
    )
    logger.emit(
        {
            "timestamp": "2026-04-18T00:00:05Z",
            "event": "model.responded",
            "worker_id": "coding-build.aggregation.lead",
            "handoff_packet_id": "packet:root",
            "model_call_id": "model-call:1",
            "model": "gpt-test",
            "finish_reason": "stop",
        }
    )
    logger.emit(
        {
            "timestamp": "2026-04-18T00:00:06Z",
            "event": "worker.completed",
            "worker_id": "coding-build.aggregation.lead",
            "handoff_packet_id": "packet:root",
            "status": "completed",
        }
    )
    logger.emit(
        {
            "timestamp": "2026-04-18T00:00:06Z",
            "event": "run.log.completed",
            "status": "completed",
        }
    )
    logger.close()

    analysis = analyze_organism_log(logger.path)
    span_index = {span.span_id: span for span in analysis.timeline.spans}

    assert span_index["model-call:1"].duration_ms == 4000
    assert span_index["tool-call:1"].duration_ms == 2000
    assert analysis.graph.critical_path_span_ids


def test_research_control_log_analysis_answers_what_blocked_the_next_step(tmp_path) -> None:
    logger = ResearchEventLogger(
        path=tmp_path / "research-control-analysis.jsonl",
        stream_kind="control_plane",
        session_id="research-session-1",
    )
    session_event = {
        "agent_id": "dan-research.orchestrator",
        "agent_session_id": "agent-session-1",
        "worker_session_id": "worker-session-1",
    }

    logger.emit({"timestamp": "2026-04-18T00:00:00Z", "event": "agent.session.created", **session_event})
    logger.emit(
        {
            "timestamp": "2026-04-18T00:00:00Z",
            "event": "agent.mailbox.enqueued",
            "message_id": "agent-message-1",
            "task": "Plan",
            **session_event,
        }
    )
    logger.emit(
        {
            "timestamp": "2026-04-18T00:00:00Z",
            "event": "agent.mailbox.started",
            "message_id": "agent-message-1",
            "task": "Plan",
            **session_event,
        }
    )
    logger.emit(
        {
            "timestamp": "2026-04-18T00:00:01Z",
            "event": "agent.mailbox.enqueued",
            "message_id": "agent-message-2",
            "task": "Review",
            **session_event,
        }
    )
    logger.emit(
        {
            "timestamp": "2026-04-18T00:00:04Z",
            "event": "agent.mailbox.completed",
            "message_id": "agent-message-1",
            "task": "Plan",
            "status": "completed",
            **session_event,
        }
    )
    logger.emit(
        {
            "timestamp": "2026-04-18T00:00:04Z",
            "event": "agent.mailbox.started",
            "message_id": "agent-message-2",
            "task": "Review",
            **session_event,
        }
    )
    logger.emit(
        {
            "timestamp": "2026-04-18T00:00:06Z",
            "event": "agent.mailbox.completed",
            "message_id": "agent-message-2",
            "task": "Review",
            "status": "completed",
            **session_event,
        }
    )
    logger.emit({"timestamp": "2026-04-18T00:00:06Z", "event": "agent.session.closed", **session_event})
    logger.close()

    analysis = analyze_organism_log(logger.path)
    wait_span_id = "mailbox-wait:agent-session-1:agent-message-2"
    first_turn_span_id = "mailbox-turn:agent-session-1:agent-message-1"
    wait_span = next(span for span in analysis.timeline.spans if span.span_id == wait_span_id)

    assert wait_span.waiting_duration_ms == 3000
    assert wait_span.direct_blocker_span_ids == [first_turn_span_id]
