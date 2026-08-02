from __future__ import annotations

from dan.worker.organism_log_analysis import analyze_organism_log_rows


def test_analyze_organism_log_rows_builds_timeline_and_critical_path() -> None:
    raw_rows = [
        {
            "schema": "organism_log_v1",
            "stream": "run",
            "product": "external",
            "timestamp": "2026-04-18T00:00:00Z",
            "event": "stage.started",
            "row_kind": "span_start",
            "span_kind": "control_stage",
            "span_id": "stage-1",
            "parallel_lane": "lane-a",
            "summary": "Plan",
        },
        {
            "schema": "organism_log_v1",
            "stream": "run",
            "product": "external",
            "timestamp": "2026-04-18T00:00:02Z",
            "event": "stage.completed",
            "row_kind": "span_end",
            "span_kind": "control_stage",
            "span_id": "stage-1",
            "parallel_lane": "lane-a",
            "status": "completed",
            "summary": "Plan",
        },
        {
            "schema": "organism_log_v1",
            "stream": "run",
            "product": "external",
            "timestamp": "2026-04-18T00:00:02.500Z",
            "event": "stage.started",
            "row_kind": "span_start",
            "span_kind": "control_stage",
            "span_id": "stage-2",
            "parallel_lane": "lane-a",
            "summary": "Build",
        },
        {
            "schema": "organism_log_v1",
            "stream": "run",
            "product": "external",
            "timestamp": "2026-04-18T00:00:03Z",
            "event": "tool.started",
            "row_kind": "span_start",
            "span_kind": "tool_call",
            "span_id": "tool-1",
            "parent_span_id": "stage-2",
            "parallel_lane": "lane-a",
            "tool_id": "file_read",
        },
        {
            "schema": "organism_log_v1",
            "stream": "run",
            "product": "external",
            "timestamp": "2026-04-18T00:00:05Z",
            "event": "tool.completed",
            "row_kind": "span_end",
            "span_kind": "tool_call",
            "span_id": "tool-1",
            "parent_span_id": "stage-2",
            "parallel_lane": "lane-a",
            "tool_id": "file_read",
            "status": "completed",
        },
        {
            "schema": "organism_log_v1",
            "stream": "run",
            "product": "external",
            "timestamp": "2026-04-18T00:00:07Z",
            "event": "stage.completed",
            "row_kind": "span_end",
            "span_kind": "control_stage",
            "span_id": "stage-2",
            "parallel_lane": "lane-a",
            "status": "completed",
            "summary": "Build",
        },
        {
            "schema": "organism_log_v1",
            "stream": "run",
            "product": "external",
            "timestamp": "2026-04-18T00:00:02Z",
            "event": "stage.started",
            "row_kind": "span_start",
            "span_kind": "control_stage",
            "span_id": "stage-b",
            "parallel_lane": "lane-b",
            "summary": "Parallel review",
        },
        {
            "schema": "organism_log_v1",
            "stream": "run",
            "product": "external",
            "timestamp": "2026-04-18T00:00:06Z",
            "event": "stage.completed",
            "row_kind": "span_end",
            "span_kind": "control_stage",
            "span_id": "stage-b",
            "parallel_lane": "lane-b",
            "status": "completed",
            "summary": "Parallel review",
        },
    ]

    analysis = analyze_organism_log_rows(raw_rows)
    assert analysis.span_count == 4
    assert len(analysis.timeline.lanes) == 2
    assert analysis.timeline.max_parallel_spans == 3

    span_index = {span.span_id: span for span in analysis.timeline.spans}
    assert span_index["stage-2"].exclusive_duration_ms == 2500
    assert span_index["stage-2"].waiting_duration_ms == 500
    assert span_index["stage-2"].direct_blocker_span_ids == ["stage-1"]
    assert span_index["stage-1"].dependent_span_ids == ["stage-2"]

    edge_relationships = {
        (edge.source_span_id, edge.target_span_id, edge.relationship)
        for edge in analysis.graph.edges
    }
    assert ("stage-1", "stage-2", "lane_sequence") in edge_relationships
    assert ("stage-2", "tool-1", "parent") in edge_relationships

    assert analysis.graph.critical_path_span_ids == ["stage-1", "stage-2"]
    assert analysis.graph.critical_path_duration_ms == 5000

    blocker_chain = next(
        item for item in analysis.graph.blocker_chains if item.target_span_id == "stage-2"
    )
    assert blocker_chain.direct_blocker_span_ids == ["stage-1"]
    assert blocker_chain.blocker_chain_span_ids == ["stage-1"]


def test_analyze_organism_log_rows_surfaces_mailbox_wait_blockers() -> None:
    raw_rows = [
        {
            "schema": "organism_log_v1",
            "stream": "control_plane",
            "product": "dan_research",
            "session_id": "research-session-1",
            "timestamp": "2026-04-18T00:00:00Z",
            "event": "agent.session.created",
            "agent_id": "dan-research.orchestrator",
            "agent_session_id": "agent-session-1",
            "worker_session_id": "worker-session-1",
            "sequence": 1,
        },
        {
            "schema": "organism_log_v1",
            "stream": "control_plane",
            "product": "dan_research",
            "session_id": "research-session-1",
            "timestamp": "2026-04-18T00:00:00Z",
            "event": "agent.mailbox.enqueued",
            "agent_id": "dan-research.orchestrator",
            "agent_session_id": "agent-session-1",
            "message_id": "agent-message-1",
            "task": "Plan",
            "sequence": 2,
        },
        {
            "schema": "organism_log_v1",
            "stream": "control_plane",
            "product": "dan_research",
            "session_id": "research-session-1",
            "timestamp": "2026-04-18T00:00:00Z",
            "event": "agent.mailbox.started",
            "agent_id": "dan-research.orchestrator",
            "agent_session_id": "agent-session-1",
            "message_id": "agent-message-1",
            "task": "Plan",
            "sequence": 3,
        },
        {
            "schema": "organism_log_v1",
            "stream": "control_plane",
            "product": "dan_research",
            "session_id": "research-session-1",
            "timestamp": "2026-04-18T00:00:01Z",
            "event": "agent.mailbox.enqueued",
            "agent_id": "dan-research.orchestrator",
            "agent_session_id": "agent-session-1",
            "message_id": "agent-message-2",
            "task": "Review",
            "sequence": 4,
        },
        {
            "schema": "organism_log_v1",
            "stream": "control_plane",
            "product": "dan_research",
            "session_id": "research-session-1",
            "timestamp": "2026-04-18T00:00:04Z",
            "event": "agent.mailbox.completed",
            "agent_id": "dan-research.orchestrator",
            "agent_session_id": "agent-session-1",
            "message_id": "agent-message-1",
            "status": "completed",
            "task": "Plan",
            "sequence": 5,
        },
        {
            "schema": "organism_log_v1",
            "stream": "control_plane",
            "product": "dan_research",
            "session_id": "research-session-1",
            "timestamp": "2026-04-18T00:00:04Z",
            "event": "agent.mailbox.started",
            "agent_id": "dan-research.orchestrator",
            "agent_session_id": "agent-session-1",
            "message_id": "agent-message-2",
            "task": "Review",
            "sequence": 6,
        },
        {
            "schema": "organism_log_v1",
            "stream": "control_plane",
            "product": "dan_research",
            "session_id": "research-session-1",
            "timestamp": "2026-04-18T00:00:06Z",
            "event": "agent.mailbox.completed",
            "agent_id": "dan-research.orchestrator",
            "agent_session_id": "agent-session-1",
            "message_id": "agent-message-2",
            "status": "completed",
            "task": "Review",
            "sequence": 7,
        },
        {
            "schema": "organism_log_v1",
            "stream": "control_plane",
            "product": "dan_research",
            "session_id": "research-session-1",
            "timestamp": "2026-04-18T00:00:06Z",
            "event": "agent.session.closed",
            "agent_id": "dan-research.orchestrator",
            "agent_session_id": "agent-session-1",
            "worker_session_id": "worker-session-1",
            "sequence": 8,
        },
    ]

    analysis = analyze_organism_log_rows(raw_rows)
    wait_span_id = "mailbox-wait:agent-session-1:agent-message-2"
    first_turn_span_id = "mailbox-turn:agent-session-1:agent-message-1"
    wait_span = next(span for span in analysis.timeline.spans if span.span_id == wait_span_id)

    assert wait_span.duration_ms == 3000
    assert wait_span.waiting_duration_ms == 3000
    assert wait_span.direct_blocker_span_ids == [first_turn_span_id]

    blocker_chain = next(
        item for item in analysis.graph.blocker_chains if item.target_span_id == wait_span_id
    )
    assert blocker_chain.direct_blocker_span_ids == [first_turn_span_id]
    assert first_turn_span_id in blocker_chain.blocker_chain_span_ids
