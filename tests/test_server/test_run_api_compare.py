"""Regression tests for run comparison API routing and behavior."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient

import dan.server.app as app_module


@dataclass
class _FakeRecord:
    payload: dict[str, Any]

    def snapshot(self) -> dict[str, Any]:
        return dict(self.payload)


class _FakeRunManager:
    def __init__(self, records: dict[str, _FakeRecord]) -> None:
        self._records = records

    def get_run(self, run_id: str) -> _FakeRecord | None:
        return self._records.get(run_id)


def _summary(
    run_id: str,
    *,
    status: str = "completed",
    total_tokens: int = 0,
    total_cost: float = 0.0,
    elapsed_seconds: float = 0.0,
    node_statuses: dict[str, str] | None = None,
    node_usage: dict[str, dict[str, int]] | None = None,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "graph_id": "wf-1",
        "status": status,
        "node_statuses": node_statuses or {},
        "started_at": 1000.0,
        "finished_at": 1005.0,
        "success": status == "completed",
        "errors": {},
        "outputs": {},
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "total_tokens": total_tokens,
        "total_cost": total_cost,
        "elapsed_seconds": elapsed_seconds,
        "node_usage": node_usage or {},
    }


def test_compare_endpoint_not_shadowed_by_get_run_route(monkeypatch):
    # If /api/runs/compare is accidentally routed to /api/runs/{run_id},
    # run_id="compare" would resolve and this request would not produce the
    # expected compare-specific validation error.
    rm = _FakeRunManager(
        {
            "compare": _FakeRecord(_summary("compare")),
        }
    )
    monkeypatch.setattr(app_module, "_run_manager", rm)

    client = TestClient(app_module.app)
    response = client.get(
        "/api/runs/compare",
        params={"run_a": "run-a-missing", "run_b": "run-b-missing"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Run 'run-a-missing' not found"


def test_compare_endpoint_returns_expected_deltas(monkeypatch):
    rm = _FakeRunManager(
        {
            "run-a": _FakeRecord(
                _summary(
                    "run-a",
                    total_tokens=100,
                    total_cost=0.001,
                    elapsed_seconds=3.0,
                    node_statuses={"n1": "node_completed", "n2": "node_completed"},
                    node_usage={
                        "n1": {
                            "prompt_tokens": 40,
                            "completion_tokens": 20,
                            "total_tokens": 60,
                        },
                        "n2": {
                            "prompt_tokens": 20,
                            "completion_tokens": 20,
                            "total_tokens": 40,
                        },
                    },
                )
            ),
            "run-b": _FakeRecord(
                _summary(
                    "run-b",
                    total_tokens=180,
                    total_cost=0.0035,
                    elapsed_seconds=5.2,
                    node_statuses={"n1": "node_completed", "n2": "node_failed"},
                    node_usage={
                        "n1": {
                            "prompt_tokens": 60,
                            "completion_tokens": 30,
                            "total_tokens": 90,
                        },
                        "n2": {
                            "prompt_tokens": 45,
                            "completion_tokens": 45,
                            "total_tokens": 90,
                        },
                    },
                )
            ),
        }
    )
    monkeypatch.setattr(app_module, "_run_manager", rm)

    client = TestClient(app_module.app)
    response = client.get("/api/runs/compare", params={"run_a": "run-a", "run_b": "run-b"})
    assert response.status_code == 200
    data = response.json()

    assert data["summary"]["token_delta"] == 80
    assert data["summary"]["elapsed_delta"] == 2.2
    assert data["summary"]["cost_delta"] == 0.0025

    node_diffs = {item["node_id"]: item for item in data["node_diffs"]}
    assert node_diffs["n1"]["token_delta"] == 30
    assert node_diffs["n2"]["status_changed"] is True
    assert node_diffs["n2"]["status_a"] == "node_completed"
    assert node_diffs["n2"]["status_b"] == "node_failed"
