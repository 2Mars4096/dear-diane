from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from dan.server import runtime_config
from dan.server.routers import dependencies
from dan.server.routers.misc import health_check, list_organism_logs
from dan.worker.organism_log import write_organism_log


@pytest.mark.asyncio
async def test_health_check_prefers_request_app_state_over_globals(monkeypatch) -> None:
    state_run_manager = SimpleNamespace(
        list_runs=lambda: [
            {"status": "running"},
            {"status": "pending"},
            {"status": "completed"},
        ]
    )
    state_engine_config = object()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                dan=SimpleNamespace(
                    run_manager=state_run_manager,
                    startup_degradations=[{"category": "capabilities", "detail": "partial"}],
                    engine_config=state_engine_config,
                )
            )
        )
    )

    monkeypatch.setattr(runtime_config, "provider_readiness_summary", lambda cfg: {"config": cfg})

    result = await health_check(request)

    assert result["status"] == "ok"
    assert result["active_runs"] == 2
    assert result["startup"] == {
        "status": "degraded",
        "issues": [{"category": "capabilities", "detail": "partial"}],
    }
    assert result["providers"] == {"config": state_engine_config}


@pytest.mark.asyncio
async def test_health_check_without_request_uses_active_app_state(monkeypatch) -> None:
    state_run_manager = SimpleNamespace(
        list_runs=lambda: [
            {"status": "running"},
            {"status": "completed"},
        ]
    )
    state_engine_config = object()
    state = SimpleNamespace(
        run_manager=state_run_manager,
        startup_degradations=[{"category": "engine", "detail": "degraded"}],
        engine_config=state_engine_config,
    )

    monkeypatch.setattr(runtime_config, "provider_readiness_summary", lambda cfg: {"config": cfg})
    monkeypatch.setattr(dependencies, "_get_active_app_state", lambda: state)

    result = await health_check()

    assert result["status"] == "ok"
    assert result["active_runs"] == 1
    assert result["startup"] == {
        "status": "degraded",
        "issues": [{"category": "engine", "detail": "degraded"}],
    }
    assert result["providers"] == {"config": state_engine_config}


@pytest.mark.asyncio
async def test_list_organism_logs_discovers_dan_code_control_plane(
    tmp_path: Path,
) -> None:
    control_log = tmp_path / ".dan-code" / "control-plane-events.jsonl"
    write_organism_log(
        control_log,
        [
            {
                "schema": "organism_log_v1",
                "stream": "control_plane",
                "product": "dan_code",
                "session_id": "code-session-control",
                "trace_id": "trace:code-control-1",
                "timestamp": "2026-04-23T00:00:00Z",
                "event": "orchestrator.turn.decision.started",
                "row_kind": "span_start",
                "span_kind": "control_stage",
                "span_id": "decision-1",
            },
            {
                "schema": "organism_log_v1",
                "stream": "control_plane",
                "product": "dan_code",
                "session_id": "code-session-control",
                "trace_id": "trace:code-control-1",
                "timestamp": "2026-04-23T00:00:01Z",
                "event": "orchestrator.turn.decision.completed",
                "row_kind": "span_end",
                "span_kind": "control_stage",
                "span_id": "decision-1",
                "status": "completed",
            },
        ],
    )

    payload = await list_organism_logs(root_path=str(tmp_path), limit=10)
    log_by_path = {item["path"]: item for item in payload["logs"]}
    summary = log_by_path[str(control_log.resolve())]

    assert summary["display_name"] == "Code control plane"
    assert summary["product"] == "dan_code"
    assert summary["stream_kind"] == "control_plane"
