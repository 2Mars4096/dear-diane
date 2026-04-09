from __future__ import annotations

from types import SimpleNamespace

import pytest

from dan.server import runtime_config
from dan.server.routers import dependencies
from dan.server.routers.misc import health_check


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
