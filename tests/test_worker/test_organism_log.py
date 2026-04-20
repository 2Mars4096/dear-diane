from __future__ import annotations

from dan.worker.core.contracts import OutputContract
from dan.worker.organism_log import (
    ORGANISM_LOG_SCHEMA_VERSION,
    OrganismLogContext,
    OrganismLogWriter,
    organism_event_context,
    project_organism_spans,
    read_organism_log,
    read_organism_log_rows,
    readable_organism_log_v1_rows,
    stable_output_contract_id,
)


def test_stable_output_contract_id_ignores_instance_identity() -> None:
    contract_a = OutputContract(
        definition_of_done="Return one bounded answer.",
        expected_return_shape='{"result":"<required>"}',
        output_schema={"type": "object", "required": ["result"]},
    )
    contract_b = OutputContract(
        definition_of_done="Return one bounded answer.",
        expected_return_shape='{"result":"<required>"}',
        output_schema={"required": ["result"], "type": "object"},
    )

    assert stable_output_contract_id(contract_a) == stable_output_contract_id(contract_b)


def test_organism_log_writer_projects_model_and_tool_spans(tmp_path) -> None:
    log_path = tmp_path / "organism-events.jsonl"
    writer = OrganismLogWriter(
        path=log_path,
        context=OrganismLogContext(
            product="dan_code",
            stream_kind="bounded_run",
            session_id="session-1",
            turn_id="1",
            task_id="coding-organ-task:1",
            trace_id="trace:demo",
            organism_id="coding-organism",
            organ_id="coding-build",
        ),
    )
    contract = OutputContract(
        definition_of_done="Return the bounded coding candidate.",
        expected_return_shape='{"candidate_id":"<required>"}',
    )
    event_context = organism_event_context(
        metadata={
            "trace_id": "trace:demo",
            "handoff_packet_id": "packet:root",
            "recipient": {
                "cell_id": "coding-build.aggregation.lead",
                "organism_id": "coding-organism",
                "organ_id": "coding-build",
            },
            "organism_stage": "aggregation",
        },
        output_contract=contract,
        worker_id="aggregation.lead",
    )

    writer.emit(
        {
            "timestamp": "2026-04-18T00:00:00Z",
            "event": "model.requested",
            "model": "gpt-test",
            "model_call_id": "model-call:1",
            "round": 1,
            "tool_count": 1,
            **event_context,
        }
    )
    writer.emit(
        {
            "timestamp": "2026-04-18T00:00:01Z",
            "event": "tool.started",
            "tool_id": "file_read",
            "tool_call_id": "tool-call:1",
            "parent_model_call_id": "model-call:1",
            **event_context,
        }
    )
    writer.emit(
        {
            "timestamp": "2026-04-18T00:00:03Z",
            "event": "tool.completed",
            "tool_id": "file_read",
            "tool_call_id": "tool-call:1",
            "parent_model_call_id": "model-call:1",
            **event_context,
        }
    )
    writer.emit(
        {
            "timestamp": "2026-04-18T00:00:05Z",
            "event": "model.responded",
            "model": "gpt-test",
            "model_call_id": "model-call:1",
            "finish_reason": "tool_calls",
            **event_context,
        }
    )
    writer.emit_trace_rows(
        [
            {
                "kind": "handoff",
                "message_id": "packet:root",
                "trace_id": "trace:demo",
                "root_task_id": "coding-organ-task:1",
                "parent_packet_id": None,
                "sender_cell_id": "user.request",
                "recipient_cell_id": "coding-build.aggregation.lead",
                "task_id": "coding-organ-task:1:aggregate:1",
                "evidence_refs": [],
                "created_at": "2026-04-18T00:00:00Z",
            }
        ]
    )
    writer.close()

    rows = read_organism_log(log_path)
    assert rows
    assert all(row["schema"] == ORGANISM_LOG_SCHEMA_VERSION for row in rows)
    assert any(row["event"] == "trace.handoff" for row in rows)

    spans = project_organism_spans(rows)
    handoff_span = next(span for span in spans if span["span_id"] == "packet:root")
    model_span = next(span for span in spans if span["span_id"] == "model-call:1")
    tool_span = next(span for span in spans if span["span_id"] == "tool-call:1")

    assert handoff_span["duration_ms"] == 0
    assert handoff_span["span_kind"] == "cell_handoff"
    assert model_span["duration_ms"] == 5000
    assert model_span["parent_span_id"] == "packet:root"
    assert tool_span["duration_ms"] == 2000
    assert tool_span["parent_span_id"] == "model-call:1"
    assert tool_span["contract_id"].startswith("contract:")


def test_readable_organism_log_v1_rows_uses_clean_v1_suffix(tmp_path) -> None:
    log_path = tmp_path / "mixed-events.jsonl"
    log_path.write_text(
        "\n".join(
            [
                '{"event":"legacy.started","sequence":1,"timestamp":"2026-04-18T00:00:00Z"}',
                '{"event":"legacy.completed","sequence":2,"timestamp":"2026-04-18T00:00:01Z"}',
                '{"schema":"organism_log_v1","stream":"bounded_run","product":"dan_code","session_id":"code-session-1","turn_id":"turn-01","task_id":"code-task-1","trace_id":"trace:code-1","timestamp":"2026-04-18T00:00:02Z","event":"stage.started","row_kind":"span_start","span_kind":"control_stage","span_id":"stage-1","summary":"Plan","parallel_lane":"planner","sequence":3}',
                '{"schema":"organism_log_v1","stream":"bounded_run","product":"dan_code","session_id":"code-session-1","turn_id":"turn-01","task_id":"code-task-1","trace_id":"trace:code-1","timestamp":"2026-04-18T00:00:05Z","event":"stage.completed","row_kind":"span_end","span_kind":"control_stage","span_id":"stage-1","status":"completed","summary":"Plan","parallel_lane":"planner","sequence":4}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    raw_rows = read_organism_log(log_path)
    readable_rows = readable_organism_log_v1_rows(raw_rows)
    normalized = read_organism_log_rows(log_path)

    assert len(readable_rows) == 2
    assert all(row["schema"] == ORGANISM_LOG_SCHEMA_VERSION for row in readable_rows)
    assert normalized[0].event == "stage.started"
    assert any(row.record_kind == "span" and row.span_id == "stage-1" for row in normalized)


def test_organism_log_writer_derives_durable_mailbox_spans_and_worker_parentage(tmp_path) -> None:
    log_path = tmp_path / "durable-agent-events.jsonl"
    writer = OrganismLogWriter(
        path=log_path,
        context=OrganismLogContext(
            product="dan_research",
            stream_kind="control_plane",
            session_id="research-session-1",
            task_id="research-turn-1",
        ),
    )
    session_event = {
        "agent_id": "dan-research.orchestrator",
        "agent_session_id": "agent-session-1",
        "worker_session_id": "worker-session-1",
    }
    message_event = {
        **session_event,
        "message_id": "agent-message-1",
        "agent_message_id": "agent-message-1",
        "message_index": 1,
    }

    writer.emit(
        {
            "timestamp": "2026-04-18T00:00:00Z",
            "event": "agent.session.created",
            **session_event,
        }
    )
    writer.emit(
        {
            "timestamp": "2026-04-18T00:00:01Z",
            "event": "agent.mailbox.enqueued",
            "task": "Plan the next research step.",
            **message_event,
        }
    )
    writer.emit(
        {
            "timestamp": "2026-04-18T00:00:03Z",
            "event": "agent.mailbox.started",
            "task": "Plan the next research step.",
            **message_event,
        }
    )
    writer.emit(
        {
            "timestamp": "2026-04-18T00:00:03Z",
            "event": "worker.started",
            "worker_id": "dan-research.orchestrator",
            **message_event,
        }
    )
    writer.emit(
        {
            "timestamp": "2026-04-18T00:00:04Z",
            "event": "model.requested",
            "worker_id": "dan-research.orchestrator",
            "model_call_id": "model-call:mailbox-1",
            "model": "gpt-test",
            **message_event,
        }
    )
    writer.emit(
        {
            "timestamp": "2026-04-18T00:00:05Z",
            "event": "model.responded",
            "worker_id": "dan-research.orchestrator",
            "model_call_id": "model-call:mailbox-1",
            "model": "gpt-test",
            "finish_reason": "stop",
            **message_event,
        }
    )
    writer.emit(
        {
            "timestamp": "2026-04-18T00:00:06Z",
            "event": "worker.completed",
            "worker_id": "dan-research.orchestrator",
            "status": "completed",
            **message_event,
        }
    )
    writer.emit(
        {
            "timestamp": "2026-04-18T00:00:07Z",
            "event": "agent.mailbox.completed",
            "task": "Plan the next research step.",
            "status": "completed",
            "stop_reason": "completed",
            **message_event,
        }
    )
    writer.emit(
        {
            "timestamp": "2026-04-18T00:00:08Z",
            "event": "agent.session.closed",
            **session_event,
        }
    )
    writer.close()

    rows = read_organism_log_rows(log_path)
    session_span = next(
        row for row in rows if row.record_kind == "span" and row.span_kind == "durable_agent_session"
    )
    wait_span = next(
        row for row in rows if row.record_kind == "span" and row.span_kind == "durable_mailbox_wait"
    )
    turn_span = next(
        row for row in rows if row.record_kind == "span" and row.span_kind == "durable_mailbox_turn"
    )
    worker_span = next(
        row for row in rows if row.record_kind == "span" and row.span_kind == "worker_execution"
    )
    model_span = next(
        row for row in rows if row.record_kind == "span" and row.span_kind == "model_call"
    )

    assert session_span.duration_ms == 8000
    assert wait_span.duration_ms == 2000
    assert wait_span.parent_span_id == session_span.span_id
    assert turn_span.duration_ms == 4000
    assert turn_span.parent_span_id == session_span.span_id
    assert worker_span.parent_span_id == turn_span.span_id
    assert model_span.parent_span_id == turn_span.span_id


def test_organism_log_writer_projects_contract_validation_and_repair_spans(tmp_path) -> None:
    log_path = tmp_path / "contract-events.jsonl"
    writer = OrganismLogWriter(
        path=log_path,
        context=OrganismLogContext(
            product="dan_code",
            stream_kind="bounded_run",
            session_id="session-2",
            turn_id="2",
            task_id="coding-organ-task:2",
            trace_id="trace:contract",
            organism_id="coding-organism",
            organ_id="coding-build",
        ),
    )
    contract = OutputContract(
        definition_of_done="Return a structured coding decision.",
        expected_return_shape='{"action":"<required>","public_response":"<required>"}',
        output_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "public_response": {"type": "string"},
            },
            "required": ["action", "public_response"],
        },
    )
    event_context = organism_event_context(
        metadata={
            "trace_id": "trace:contract",
            "recipient": {
                "cell_id": "coding-build.aggregation.lead",
                "organism_id": "coding-organism",
                "organ_id": "coding-build",
            },
            "organism_stage": "aggregation",
        },
        output_contract=contract,
        worker_id="aggregation.lead",
    )

    writer.emit(
        {
            "timestamp": "2026-04-18T00:00:00Z",
            "event": "worker.started",
            **event_context,
        }
    )
    writer.emit(
        {
            "timestamp": "2026-04-18T00:00:01Z",
            "event": "contract.validation.started",
            "validation_phase": "initial",
            "normalization_mode": "jsonish_payload",
            **event_context,
        }
    )
    writer.emit(
        {
            "timestamp": "2026-04-18T00:00:02Z",
            "event": "contract.validation.completed",
            "validation_phase": "initial",
            "status": "invalid",
            "errors": ["$.public_response: 'public_response' is a required property"],
            "normalization_mode": "jsonish_payload",
            **event_context,
        }
    )
    writer.emit(
        {
            "timestamp": "2026-04-18T00:00:03Z",
            "event": "contract.repair.started",
            "validation_phase": "repair",
            "repair_round": 1,
            **event_context,
        }
    )
    writer.emit(
        {
            "timestamp": "2026-04-18T00:00:04Z",
            "event": "contract.validation.started",
            "validation_phase": "repair",
            "repair_round": 1,
            "normalization_mode": "jsonish_payload",
            **event_context,
        }
    )
    writer.emit(
        {
            "timestamp": "2026-04-18T00:00:05Z",
            "event": "contract.validation.completed",
            "validation_phase": "repair",
            "repair_round": 1,
            "status": "valid",
            "normalization_mode": "jsonish_payload",
            **event_context,
        }
    )
    writer.emit(
        {
            "timestamp": "2026-04-18T00:00:06Z",
            "event": "contract.repair.completed",
            "validation_phase": "repair",
            "repair_round": 1,
            "status": "repaired",
            **event_context,
        }
    )
    writer.emit(
        {
            "timestamp": "2026-04-18T00:00:07Z",
            "event": "worker.completed",
            "status": "completed",
            **event_context,
        }
    )
    writer.close()

    rows = read_organism_log_rows(log_path)
    validation_spans = [
        row for row in rows if row.record_kind == "span" and row.span_kind == "output_contract_validation"
    ]
    repair_span = next(
        row for row in rows if row.record_kind == "span" and row.span_kind == "output_contract_repair"
    )
    worker_span = next(
        row for row in rows if row.record_kind == "span" and row.span_kind == "worker_execution"
    )

    assert len(validation_spans) == 2
    initial_validation = next(row for row in validation_spans if row.status == "invalid")
    repaired_validation = next(row for row in validation_spans if row.status == "valid")

    assert initial_validation.duration_ms == 1000
    assert initial_validation.parent_span_id == worker_span.span_id
    assert repaired_validation.duration_ms == 1000
    assert repaired_validation.parent_span_id == repair_span.span_id
    assert repair_span.duration_ms == 3000
    assert repair_span.parent_span_id == worker_span.span_id
    assert repair_span.status == "repaired"
