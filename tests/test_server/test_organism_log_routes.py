from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from dan.server.routers.misc import (
    analyze_organism_log_file,
    list_organism_logs,
)
from dan.worker.organism_log import write_organism_log


def _write_code_log(path: Path) -> None:
    write_organism_log(
        path,
        [
            {
                "schema": "organism_log_v1",
                "stream": "bounded_run",
                "product": "dan_code",
                "session_id": "code-session-1",
                "turn_id": "turn-01",
                "task_id": "code-task-1",
                "trace_id": "trace:code-1",
                "timestamp": "2026-04-18T00:00:00Z",
                "event": "stage.started",
                "row_kind": "span_start",
                "span_kind": "control_stage",
                "span_id": "stage-1",
                "summary": "Plan",
                "parallel_lane": "planner",
            },
            {
                "schema": "organism_log_v1",
                "stream": "bounded_run",
                "product": "dan_code",
                "session_id": "code-session-1",
                "turn_id": "turn-01",
                "task_id": "code-task-1",
                "trace_id": "trace:code-1",
                "timestamp": "2026-04-18T00:00:03Z",
                "event": "stage.completed",
                "row_kind": "span_end",
                "span_kind": "control_stage",
                "span_id": "stage-1",
                "status": "completed",
                "summary": "Plan",
                "parallel_lane": "planner",
            },
        ],
    )


def _write_code_control_log(path: Path) -> None:
    write_organism_log(
        path,
        [
            {
                "schema": "organism_log_v1",
                "stream": "control_plane",
                "product": "dan_code",
                "session_id": "code-session-control",
                "trace_id": "trace:code-control-1",
                "timestamp": "2026-04-18T00:00:00Z",
                "event": "orchestrator.turn.decision.started",
                "row_kind": "span_start",
                "span_kind": "control_stage",
                "span_id": "decision-1",
                "summary": "Decide turn",
            },
            {
                "schema": "organism_log_v1",
                "stream": "control_plane",
                "product": "dan_code",
                "session_id": "code-session-control",
                "trace_id": "trace:code-control-1",
                "timestamp": "2026-04-18T00:00:02Z",
                "event": "orchestrator.turn.decision.completed",
                "row_kind": "span_end",
                "span_kind": "control_stage",
                "span_id": "decision-1",
                "status": "completed",
                "summary": "Decide turn",
            },
        ],
    )


def _write_research_control_log(path: Path) -> None:
    write_organism_log(
        path,
        [
            {
                "schema": "organism_log_v1",
                "stream": "control_plane",
                "product": "dan_research",
                "session_id": "research-session-1",
                "trace_id": "trace:research-control-1",
                "timestamp": "2026-04-18T00:01:00Z",
                "event": "provider.build.started",
                "row_kind": "span_start",
                "span_kind": "control_stage",
                "span_id": "provider-build-1",
                "summary": "Provider build",
            },
            {
                "schema": "organism_log_v1",
                "stream": "control_plane",
                "product": "dan_research",
                "session_id": "research-session-1",
                "trace_id": "trace:research-control-1",
                "timestamp": "2026-04-18T00:01:04Z",
                "event": "provider.build.completed",
                "row_kind": "span_end",
                "span_kind": "control_stage",
                "span_id": "provider-build-1",
                "status": "completed",
                "summary": "Provider build",
            },
        ],
    )


def _write_super_log(path: Path) -> None:
    write_organism_log(
        path,
        [
            {
                "schema": "organism_log_v1",
                "stream": "bounded_run",
                "product": "dan_super",
                "session_id": "super-session-1",
                "turn_id": "turn-01",
                "task_id": "super-task-1",
                "trace_id": "trace:super-1",
                "timestamp": "2026-04-22T00:00:00Z",
                "event": "run.log.started",
                "objective": "Build a product website.",
            },
            {
                "schema": "organism_log_v1",
                "stream": "bounded_run",
                "product": "dan_super",
                "session_id": "super-session-1",
                "turn_id": "turn-01",
                "task_id": "super-task-1",
                "trace_id": "trace:super-1",
                "timestamp": "2026-04-22T00:00:01Z",
                "event": "provider.build.started",
                "model": "gpt-test",
            },
            {
                "schema": "organism_log_v1",
                "stream": "bounded_run",
                "product": "dan_super",
                "session_id": "super-session-1",
                "turn_id": "turn-01",
                "task_id": "super-task-1",
                "trace_id": "trace:super-1",
                "timestamp": "2026-04-22T00:00:02Z",
                "event": "provider.build.completed",
                "model": "gpt-test",
                "status": "completed",
            },
            {
                "schema": "organism_log_v1",
                "stream": "bounded_run",
                "product": "dan_super",
                "session_id": "super-session-1",
                "turn_id": "turn-01",
                "task_id": "super-task-1",
                "trace_id": "trace:super-1",
                "timestamp": "2026-04-22T00:00:03Z",
                "event": "run.log.completed",
                "status": "completed",
            },
        ],
    )


def _write_mixed_research_control_log(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                '{"event":"cli.started","sequence":1,"timestamp":"2026-04-18T00:00:00Z"}',
                '{"event":"cli.completed","sequence":2,"timestamp":"2026-04-18T00:00:01Z"}',
                '{"schema":"organism_log_v1","stream":"control_plane","product":"dan_research","session_id":"research-session-1","trace_id":"trace:research-control-1","timestamp":"2026-04-18T00:01:00Z","event":"provider.build.started","row_kind":"span_start","span_kind":"control_stage","span_id":"provider-build-1","summary":"Provider build","sequence":3}',
                '{"schema":"organism_log_v1","stream":"control_plane","product":"dan_research","session_id":"research-session-1","trace_id":"trace:research-control-1","timestamp":"2026-04-18T00:01:04Z","event":"provider.build.completed","row_kind":"span_end","span_kind":"control_stage","span_id":"provider-build-1","status":"completed","summary":"Provider build","sequence":4}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_list_organism_logs_discovers_known_workspace_paths(tmp_path: Path) -> None:
    code_log = tmp_path / ".dan-code" / "runs" / "turn-01" / "events.jsonl"
    code_control_log = tmp_path / ".dan-code" / "control-plane-events.jsonl"
    super_log = tmp_path / ".dan-super" / "runs" / "turn-01" / "events.jsonl"
    research_control_log = tmp_path / ".dan-research" / "control-plane-events.jsonl"
    _write_code_log(code_log)
    _write_code_control_log(code_control_log)
    _write_super_log(super_log)
    _write_research_control_log(research_control_log)

    payload = await list_organism_logs(root_path=str(tmp_path), limit=10)

    assert payload["root_path"] == str(tmp_path.resolve())
    log_by_path = {item["path"]: item for item in payload["logs"]}
    assert str(code_log.resolve()) in log_by_path
    assert str(code_control_log.resolve()) in log_by_path
    assert str(super_log.resolve()) in log_by_path
    assert str(research_control_log.resolve()) in log_by_path

    code_summary = log_by_path[str(code_log.resolve())]
    assert code_summary["display_name"] == "Code turn-01"
    assert code_summary["product"] == "dan_code"
    assert code_summary["span_count"] == 1

    code_control_summary = log_by_path[str(code_control_log.resolve())]
    assert code_control_summary["display_name"] == "Code control plane"
    assert code_control_summary["product"] == "dan_code"
    assert code_control_summary["stream_kind"] == "control_plane"

    super_summary = log_by_path[str(super_log.resolve())]
    assert super_summary["display_name"] == "Super DAN turn-01"
    assert super_summary["product"] == "dan_super"

    research_summary = log_by_path[str(research_control_log.resolve())]
    assert research_summary["display_name"] == "Research control plane"
    assert research_summary["stream_kind"] == "control_plane"


@pytest.mark.asyncio
async def test_list_organism_logs_accepts_mixed_legacy_plus_v1_files(tmp_path: Path) -> None:
    research_control_log = tmp_path / ".dan-research" / "control-plane-events.jsonl"
    _write_mixed_research_control_log(research_control_log)

    payload = await list_organism_logs(root_path=str(tmp_path), limit=10)

    log_by_path = {item["path"]: item for item in payload["logs"]}
    summary = log_by_path[str(research_control_log.resolve())]
    assert summary["display_name"] == "Research control plane"
    assert summary["span_count"] == 1
    assert summary["event_count"] == 2


@pytest.mark.asyncio
async def test_analyze_organism_log_file_supports_relative_paths(tmp_path: Path) -> None:
    code_log = tmp_path / ".dan-code" / "runs" / "turn-01" / "events.jsonl"
    _write_code_log(code_log)

    payload = await analyze_organism_log_file(
        path=".dan-code/runs/turn-01/events.jsonl",
        root_path=str(tmp_path),
    )

    assert payload["path"] == str(code_log.resolve())
    assert payload["log"]["display_name"] == "Code turn-01"
    assert payload["analysis"]["span_count"] == 1
    assert payload["analysis"]["timeline"]["spans"][0]["label"] == "Plan"
    assert payload["analysis"]["graph"]["critical_path_span_ids"] == ["stage-1"]


@pytest.mark.asyncio
async def test_analyze_organism_log_file_uses_v1_tail_from_mixed_file(tmp_path: Path) -> None:
    research_control_log = tmp_path / ".dan-research" / "control-plane-events.jsonl"
    _write_mixed_research_control_log(research_control_log)

    payload = await analyze_organism_log_file(
        path=str(research_control_log),
        root_path=str(tmp_path),
    )

    assert payload["log"]["display_name"] == "Research control plane"
    assert payload["analysis"]["span_count"] == 1
    assert payload["analysis"]["timeline"]["spans"][0]["span_id"] == "provider-build-1"


@pytest.mark.asyncio
async def test_analyze_organism_log_file_rejects_non_organism_jsonl(tmp_path: Path) -> None:
    invalid = tmp_path / "events.jsonl"
    invalid.write_text('{"hello":"world"}\n', encoding="utf-8")

    with pytest.raises(HTTPException) as exc_info:
        await analyze_organism_log_file(path=str(invalid), root_path=str(tmp_path))

    assert exc_info.value.status_code == 400
