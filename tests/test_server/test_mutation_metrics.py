"""Tests for the mutation metrics collector and /api/metrics/mutations endpoint."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, AsyncGenerator, AsyncIterator
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("DAN_GRAPHS_DIR", tempfile.mkdtemp())
os.environ.setdefault("DAN_CHECKPOINT_DIR", tempfile.mkdtemp())

from dan.providers import CompletionResult, StreamChunk  # noqa: E402
from dan.providers.registry import ProviderRegistry  # noqa: E402
from dan.server.app import app, lifespan  # noqa: E402
from dan.server.mutation_metrics import MutationMetrics  # noqa: E402


# ---------------------------------------------------------------------------
# Unit tests for MutationMetrics dataclass
# ---------------------------------------------------------------------------


class TestMutationMetrics:
    def test_initial_state_is_zero(self):
        m = MutationMetrics()
        s = m.summary()
        assert s["total_mutations"] == 0
        assert s["successful_applies"] == 0
        assert s["failed_applies"] == 0
        assert s["apply_success_rate"] == 0.0
        assert s["retry_count"] == 0
        assert s["stale_plan_count"] == 0

    def test_record_apply_success(self):
        m = MutationMetrics()
        m.record_apply(True)
        m.record_apply(True)
        m.record_apply(False)
        s = m.summary()
        assert s["total_mutations"] == 3
        assert s["successful_applies"] == 2
        assert s["failed_applies"] == 1
        assert s["apply_success_rate"] == pytest.approx(2 / 3, abs=0.01)

    def test_record_validation(self):
        m = MutationMetrics()
        m.record_validation(True)
        m.record_validation(True)
        m.record_validation(False)
        s = m.summary()
        assert s["validation_attempts"] == 3
        assert s["validation_passes"] == 2
        assert s["post_validate_pass_rate"] == pytest.approx(2 / 3, abs=0.01)

    def test_record_retry(self):
        m = MutationMetrics()
        m.record_retry()
        m.record_retry()
        assert m.summary()["retry_count"] == 2

    def test_record_stale_plan(self):
        m = MutationMetrics()
        m.record_stale_plan()
        assert m.summary()["stale_plan_count"] == 1

    def test_record_turns_to_success(self):
        m = MutationMetrics()
        m.record_turns_to_success(1)
        m.record_turns_to_success(3)
        m.record_turns_to_success(2)
        s = m.summary()
        assert s["turn_samples"] == 3
        assert s["avg_user_turns_to_success"] == 2.0

    def test_reset_clears_all(self):
        m = MutationMetrics()
        m.record_apply(True)
        m.record_apply(False)
        m.record_validation(True)
        m.record_retry()
        m.record_stale_plan()
        m.record_turns_to_success(2)

        report = m.reset()
        assert report["total_mutations"] == 2
        assert report["retry_count"] == 1
        assert report["stale_plan_count"] == 1

        s = m.summary()
        assert s["total_mutations"] == 0
        assert s["successful_applies"] == 0
        assert s["failed_applies"] == 0
        assert s["retry_count"] == 0
        assert s["stale_plan_count"] == 0
        assert s["turn_samples"] == 0

    def test_success_rate_with_no_attempts(self):
        m = MutationMetrics()
        assert m.apply_success_rate == 0.0
        assert m.post_validate_pass_rate == 0.0
        assert m.avg_user_turns_to_success == 0.0

    def test_elapsed_seconds_is_positive(self):
        m = MutationMetrics()
        s = m.summary()
        assert s["elapsed_seconds"] >= 0


# ---------------------------------------------------------------------------
# Integration: metrics endpoint via FastAPI
# ---------------------------------------------------------------------------


class _MockProvider:
    async def complete(self, **kwargs: Any) -> CompletionResult:
        return CompletionResult(
            text="Mock reply", tool_calls=[], usage={"prompt_tokens": 10, "completion_tokens": 5},
        )

    async def stream(self, **kwargs: Any) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(delta="Mock", accumulated="Mock", done=True,
                          usage={"prompt_tokens": 10, "completion_tokens": 5})


def _mock_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register("default", _MockProvider())  # type: ignore[arg-type]
    return registry


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    with patch(
        "dan.server.app._build_chat_provider_registry",
        return_value=_mock_registry(),
    ):
        async with lifespan(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                yield c


_MINIMAL_GRAPH = {
    "version": "dan_graph_v1",
    "metadata": {"name": "metrics-test-wf"},
    "nodes": [
        {
            "id": "n1", "node_type": "llm_operator", "name": "Writer",
            "input_ports": [{"name": "input", "schema": {}}],
            "output_ports": [{"name": "text", "schema": {}}],
            "position": {"x": 0, "y": 0},
            "ui": {}, "metadata": {},
            "model": "mock-model", "prompt_template": "Write: {input}",
        },
    ],
    "edges": [],
    "sub_graphs": {},
    "entry_points": ["n1"],
    "exit_points": ["n1"],
    "shared_context": [],
    "artifact_refs": [],
}


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_data(client: AsyncClient):
    """GET /api/metrics/mutations returns expected metrics structure."""
    resp = await client.get("/api/metrics/mutations")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_mutations" in data
    assert "successful_applies" in data
    assert "failed_applies" in data
    assert "apply_success_rate" in data
    assert "retry_count" in data
    assert "stale_plan_count" in data
    assert "elapsed_seconds" in data


@pytest.mark.asyncio
async def test_metrics_reset_endpoint(client: AsyncClient):
    """POST /api/metrics/mutations/reset returns report and clears metrics."""
    resp = await client.post("/api/metrics/mutations/reset")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_mutations" in data

    resp2 = await client.get("/api/metrics/mutations")
    assert resp2.json()["total_mutations"] == 0


async def _get_stored_revision(client: AsyncClient, gid: str) -> str:
    """Fetch the graph from the store and compute its revision."""
    from dan.server.chat_manager import compute_graph_revision
    resp = await client.get(f"/api/graphs/{gid}")
    return compute_graph_revision(resp.json()["data"])


@pytest.mark.asyncio
async def test_metrics_updated_after_successful_mutation(client: AsyncClient):
    """Applying a valid mutation updates the metrics counters."""
    await client.post("/api/metrics/mutations/reset")

    gid = "metrics-mut-ok"
    await client.post("/api/graphs", json={"graph_id": gid})
    await client.put(f"/api/graphs/{gid}", json=_MINIMAL_GRAPH)

    revision = await _get_stored_revision(client, gid)

    mutation_plan = {
        "operations": [
            {"op": "add_node", "node_type": "llm_operator", "name": "Reviewer"},
        ],
        "description": "Add reviewer",
        "base_graph_revision": revision,
    }
    apply_resp = await client.post(
        f"/api/graphs/{gid}/apply-mutation",
        json={"mutation_plan": mutation_plan},
    )
    assert apply_resp.status_code == 200
    result = apply_resp.json()
    assert result["success"] is True, f"Apply failed: {result.get('errors')}"

    metrics_resp = await client.get("/api/metrics/mutations")
    data = metrics_resp.json()
    assert data["total_mutations"] >= 1
    assert data["successful_applies"] >= 1


@pytest.mark.asyncio
async def test_metrics_updated_after_failed_mutation(client: AsyncClient):
    """Applying an invalid mutation increments the failure counter."""
    await client.post("/api/metrics/mutations/reset")

    gid = "metrics-mut-fail"
    await client.post("/api/graphs", json={"graph_id": gid})
    await client.put(f"/api/graphs/{gid}", json=_MINIMAL_GRAPH)

    revision = await _get_stored_revision(client, gid)

    mutation_plan = {
        "operations": [
            {"op": "remove_node", "node_id": "nonexistent-node"},
        ],
        "description": "Remove non-existent node",
        "base_graph_revision": revision,
    }
    apply_resp = await client.post(
        f"/api/graphs/{gid}/apply-mutation",
        json={"mutation_plan": mutation_plan},
    )
    assert apply_resp.status_code == 200
    result = apply_resp.json()
    assert result["success"] is False

    metrics_resp = await client.get("/api/metrics/mutations")
    data = metrics_resp.json()
    assert data["failed_applies"] >= 1
