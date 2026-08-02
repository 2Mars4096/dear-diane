"""Tests for review-hardening fixes (plan 22-3, tasks 3-7)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


# ── Task 3: ChatClient.cancel_run uses gateway route ─────────────────────

class TestChatClientCancel:
    def test_cancel_uses_gateway_route(self):
        """ChatClient.cancel_run should POST to /api/gateway/cancel."""
        import inspect
        from dan.cli.chat import ChatClient

        source = inspect.getsource(ChatClient.cancel_run)
        assert "/api/gateway/cancel" in source
        assert "/api/runs/" not in source

    @pytest.mark.asyncio
    async def test_cancel_sends_run_id_in_body(self):
        """cancel_run should send run_id in the JSON body."""
        from dan.cli.chat import ChatClient

        client = ChatClient(base_url="http://test:8000")
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        mock_http.is_closed = False
        client._http = mock_http

        result = await client.cancel_run("run-123")

        mock_http.post.assert_called_once_with(
            "/api/gateway/cancel", json={"run_id": "run-123"}
        )
        assert result is True
        await client.close()


# ── Task 4: Human-input ownership validation ─────────────────────────────

class TestHumanInputOwnership:
    def _make_manager(self):
        from dan.server.run_manager import RunManager
        return RunManager()

    def test_ownership_dict_exists(self):
        rm = self._make_manager()
        assert hasattr(rm, "_human_input_request_ownership")
        assert isinstance(rm._human_input_request_ownership, dict)

    def test_submit_rejects_mismatched_run_id(self):
        rm = self._make_manager()
        evt = asyncio.Event()
        rm._pending_human_inputs["req-1"] = evt
        rm._human_input_request_ownership["req-1"] = "run-A"

        result = rm.submit_human_input("run-B", "req-1", {"response": "ok"})
        assert result is False
        assert "req-1" in rm._pending_human_inputs, "event should be restored"
        assert rm._human_input_request_ownership["req-1"] == "run-A"

    def test_submit_accepts_correct_run_id(self):
        rm = self._make_manager()
        evt = asyncio.Event()
        rm._pending_human_inputs["req-1"] = evt
        rm._human_input_request_ownership["req-1"] = "run-A"

        result = rm.submit_human_input("run-A", "req-1", {"response": "ok"})
        assert result is True
        assert evt.is_set()
        assert "req-1" not in rm._pending_human_inputs
        assert "req-1" not in rm._human_input_request_ownership

    def test_submit_accepts_when_no_ownership_recorded(self):
        """Backward compat: if no ownership was recorded, accept any run_id."""
        rm = self._make_manager()
        evt = asyncio.Event()
        rm._pending_human_inputs["req-1"] = evt

        result = rm.submit_human_input("any-run", "req-1", {"response": "ok"})
        assert result is True
        assert evt.is_set()

    def test_register_meta_approval_stores_ownership(self):
        rm = self._make_manager()
        rm.register_meta_approval("req-meta", run_id="run-X")
        assert rm._human_input_request_ownership.get("req-meta") == "run-X"

    def test_pop_meta_approval_cleans_ownership(self):
        rm = self._make_manager()
        evt = rm.register_meta_approval("req-meta", run_id="run-X")
        rm._human_input_responses["req-meta"] = {"approved": True}
        evt.set()
        rm.pop_meta_approval_response("req-meta")
        assert "req-meta" not in rm._human_input_request_ownership


# ── Task 5: Context edge not silently rewritten ──────────────────────────

class TestContextEdgeNotRewritten:
    def test_context_edge_preserved(self):
        from dan.server.chat_manager import _normalize_generated_mutation_ops

        ops = [
            {"op": "add_edge", "edge_type": "context",
             "source_id": "a", "source_port": "out",
             "target_id": "b", "target_port": "in"},
        ]
        result = _normalize_generated_mutation_ops(ops)
        assert len(result) == 1
        assert result[0]["edge_type"] == "context"

    def test_data_edge_unchanged(self):
        from dan.server.chat_manager import _normalize_generated_mutation_ops

        ops = [
            {"op": "add_edge", "edge_type": "data",
             "source_id": "a", "source_port": "out",
             "target_id": "b", "target_port": "in"},
        ]
        result = _normalize_generated_mutation_ops(ops)
        assert result[0]["edge_type"] == "data"


# ── Task 6: _coerce_strict_edges sets strict=True ───────────────────────

class TestCoerceStrictEdges:
    def test_sets_strict_on_add_edge(self):
        from dan.server.chat_manager import _coerce_strict_edges

        ops = [
            {"op": "add_edge", "source_id": "a", "source_port": "out",
             "target_id": "b", "target_port": "in"},
        ]
        result = _coerce_strict_edges(ops)
        assert result[0]["strict"] is True

    def test_preserves_explicit_strict_false(self):
        from dan.server.chat_manager import _coerce_strict_edges

        ops = [
            {"op": "add_edge", "strict": False,
             "source_id": "a", "source_port": "out",
             "target_id": "b", "target_port": "in"},
        ]
        result = _coerce_strict_edges(ops)
        assert result[0]["strict"] is False

    def test_non_edge_ops_untouched(self):
        from dan.server.chat_manager import _coerce_strict_edges

        ops = [
            {"op": "add_node", "node_type": "llm_operator", "name": "X"},
        ]
        result = _coerce_strict_edges(ops)
        assert "strict" not in result[0]

    def test_does_not_mutate_input(self):
        from dan.server.chat_manager import _coerce_strict_edges

        original = {"op": "add_edge", "source_id": "a", "source_port": "out",
                     "target_id": "b", "target_port": "in"}
        _coerce_strict_edges([original])
        assert "strict" not in original


# ── Task 7: Graph mutator alias collisions ───────────────────────────────

def _minimal_graph(nodes: list[dict] | None = None) -> dict[str, Any]:
    return {
        "metadata": {"name": "test", "description": "", "version": "1"},
        "nodes": nodes or [],
        "edges": [],
        "sub_graphs": {},
        "entry_points": [],
        "exit_points": [],
    }


class TestAddNodeRejectsExistingId:
    def test_explicit_id_collision_rejected(self):
        from dan.server.graph_mutator import GraphMutator, MutationPlan

        graph = _minimal_graph([
            {"id": "node_1", "node_type": "llm_operator", "name": "Existing",
             "input_ports": [{"name": "input", "schema": {}}],
             "output_ports": [{"name": "text", "schema": {}}]},
        ])
        plan = MutationPlan(
            operations=[
                {"op": "add_node", "id": "node_1",
                 "node_type": "llm_operator", "name": "Duplicate"},
            ],
        )
        result = GraphMutator().apply(graph, plan)
        assert not result.success
        assert any("already exists" in e.message for e in result.errors)

    def test_auto_id_no_collision(self):
        from dan.server.graph_mutator import GraphMutator, MutationPlan

        graph = _minimal_graph([
            {"id": "step-1", "node_type": "llm_operator", "name": "Existing",
             "input_ports": [{"name": "input", "schema": {}}],
             "output_ports": [{"name": "text", "schema": {}}]},
        ])
        plan = MutationPlan(
            operations=[
                {"op": "add_node", "node_type": "llm_operator", "name": "New Node"},
            ],
        )
        result = GraphMutator().apply(graph, plan)
        assert result.success
        ids = {n["id"] for n in result.new_graph["nodes"]}
        assert "new-node" in ids
        assert "step-1" in ids


class TestPlaceholderShadowing:
    def test_placeholder_does_not_shadow_existing_node(self):
        from dan.server.graph_mutator import GraphMutator, MutationPlan

        graph = _minimal_graph([
            {"id": "node_1", "node_type": "llm_operator", "name": "Real Node 1",
             "input_ports": [{"name": "input", "schema": {}}],
             "output_ports": [{"name": "text", "schema": {}}]},
        ])
        plan = MutationPlan(
            operations=[
                {"op": "add_node", "node_type": "llm_operator", "name": "Added"},
                {"op": "add_edge", "source_id": "node_1",
                 "source_port": "text", "target_id": "added",
                 "target_port": "input"},
            ],
        )
        result = GraphMutator().apply(graph, plan)
        assert result.success
        edge = result.new_graph["edges"][0]
        assert edge["source_node_id"] == "node_1", (
            "node_1 in add_edge should refer to the original node, "
            "not be aliased to the newly added node"
        )
