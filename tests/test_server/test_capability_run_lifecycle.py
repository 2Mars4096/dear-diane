"""Tests for run lifecycle capability handlers (25-4)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

import pytest

from dan.models.graph import Graph, GraphMetadata
from dan.models.nodes import LLMOperator
from dan.models.ports import InputPort, OutputPort
from dan.server.capability_registry import ALL_MODES, CapabilityContext, ChatCapabilityRegistry
from dan.server.capability_handlers import (
    resolve_run_reference,
    handle_start_run,
    handle_get_run_status,
    handle_list_active_runs,
    handle_cancel_run,
    handle_resume_run,
    handle_get_run_logs,
    handle_get_run_checkpoints,
    handle_rerun_from_checkpoint,
    handle_apply_pending_overlay,
    handle_submit_human_input,
    register_run_lifecycle_capabilities,
)


def _minimal_graph(graph_id: str = "test-wf") -> Graph:
    """Graph with one node so handle_start_run validation passes."""
    return Graph(
        metadata=GraphMetadata(name="Test", description=""),
        nodes=[
            LLMOperator(
                id="n1",
                name="Writer",
                model="gpt-4o",
                prompt_template="Write: {input}",
                input_ports=[InputPort(name="input")],
                output_ports=[OutputPort(name="text")],
            ),
        ],
        edges=[],
        entry_points=["n1"],
        exit_points=["n1"],
    )


def _graph_data(graph: Graph) -> dict:
    return json.loads(graph.model_dump_json())


# ── resolve_run_reference ───────────────────────────────────────────

class TestResolveRunReference:

    def test_concrete_run_id(self):
        rm = MagicMock()
        assert resolve_run_reference("run-123", rm) == "run-123"
        rm.list_runs.assert_not_called()

    def test_latest_empty(self):
        rm = MagicMock()
        rm.list_runs.return_value = []
        assert resolve_run_reference("latest", rm) is None

    def test_latest_with_runs(self):
        rm = MagicMock()
        rm.list_runs.return_value = [
            {"run_id": "r1", "started_at": 100},
            {"run_id": "r2", "started_at": 200},
        ]
        assert resolve_run_reference("latest", rm) == "r2"

    def test_latest_persisted_fallback(self):
        rm = MagicMock()
        rm.list_runs.return_value = []
        store = MagicMock()
        store.list_summaries.return_value = [{"run_id": "persisted-1"}]
        assert resolve_run_reference("latest", rm, store) == "persisted-1"

    def test_last_failed_empty(self):
        rm = MagicMock()
        rm.list_runs.return_value = [{"run_id": "r1", "status": "completed"}]
        assert resolve_run_reference("last_failed", rm) is None

    def test_last_failed_with_failed(self):
        rm = MagicMock()
        rm.list_runs.return_value = [
            {"run_id": "r1", "status": "failed", "started_at": 100},
            {"run_id": "r2", "status": "failed", "started_at": 200},
        ]
        assert resolve_run_reference("last_failed", rm) == "r2"

    def test_paused_empty(self):
        rm = MagicMock()
        rm.get_all_pending_human_inputs.return_value = []
        assert resolve_run_reference("paused", rm) is None

    def test_paused_with_pending(self):
        rm = MagicMock()
        rm.get_all_pending_human_inputs.return_value = [
            {"run_id": "r1", "event_type": "human_input_needed"},
        ]
        assert resolve_run_reference("paused", rm) == "r1"

    def test_paused_run_id_in_data(self):
        rm = MagicMock()
        rm.get_all_pending_human_inputs.return_value = [
            {"data": {"run_id": "r-from-data"}},
        ]
        assert resolve_run_reference("paused", rm) == "r-from-data"

    def test_paused_with_multiple_returns_none(self):
        """When multiple runs have pending input, resolve returns None for disambiguation."""
        rm = MagicMock()
        rm.get_all_pending_human_inputs.return_value = [
            {"run_id": "r1", "data": {"node_id": "n1", "prompt": "First"}},
            {"run_id": "r2", "data": {"node_id": "n2", "prompt": "Second"}},
        ]
        assert resolve_run_reference("paused", rm) is None


# ── get_pending_run_disambiguation ────────────────────────────────────

class TestGetPendingRunDisambiguation:

    def test_returns_none_when_empty(self):
        from dan.server.capability_handlers import get_pending_run_disambiguation
        rm = MagicMock()
        rm.get_all_pending_human_inputs.return_value = []
        assert get_pending_run_disambiguation(rm) is None

    def test_returns_none_when_single(self):
        from dan.server.capability_handlers import get_pending_run_disambiguation
        rm = MagicMock()
        rm.get_all_pending_human_inputs.return_value = [
            {"run_id": "r1", "data": {"node_id": "n1", "prompt": "One"}},
        ]
        assert get_pending_run_disambiguation(rm) is None

    def test_returns_message_when_multiple(self):
        from dan.server.capability_handlers import get_pending_run_disambiguation
        rm = MagicMock()
        rm.get_all_pending_human_inputs.return_value = [
            {"run_id": "r1", "data": {"node_id": "n1", "prompt": "First prompt"}},
            {"run_id": "r2", "data": {"node_id": "n2", "prompt": "Second prompt"}},
        ]
        msg = get_pending_run_disambiguation(rm)
        assert msg is not None
        assert "Multiple runs" in msg
        assert "r1" in msg
        assert "r2" in msg
        assert "n1" in msg
        assert "n2" in msg
        assert "First prompt" in msg


# ── start_run ───────────────────────────────────────────────────────

class TestStartRun:

    @pytest.mark.asyncio
    async def test_no_run_manager(self):
        ctx = CapabilityContext(workflow_id="wf1")
        result = await handle_start_run({}, ctx)
        assert result.success is False
        assert "Run manager" in result.message

    @pytest.mark.asyncio
    async def test_no_graph_store(self):
        ctx = CapabilityContext(workflow_id="wf1", run_manager=MagicMock())
        result = await handle_start_run({}, ctx)
        assert result.success is False
        assert "Graph store" in result.message

    @pytest.mark.asyncio
    async def test_workflow_id_required(self):
        ctx = CapabilityContext(workflow_id="", run_manager=MagicMock(), graph_store=MagicMock())
        result = await handle_start_run({}, ctx)
        assert result.success is False
        assert "workflow_id" in result.message

    @pytest.mark.asyncio
    async def test_graph_not_found(self):
        mock_store = MagicMock()
        mock_store.get_graph.return_value = None
        ctx = CapabilityContext(workflow_id="wf1", run_manager=MagicMock(), graph_store=mock_store)
        result = await handle_start_run({"workflow_id": "missing"}, ctx)
        assert result.success is False
        assert "not found" in result.message

    @pytest.mark.asyncio
    async def test_start_success(self):
        graph = _minimal_graph("wf1")
        mock_store = MagicMock()
        mock_store.list_graphs.return_value = [{"graph_id": "wf1", "name": "wf1"}]
        mock_store.get_graph.return_value = _graph_data(graph)
        mock_rm = MagicMock()
        mock_record = MagicMock()
        mock_record.run_id = "run-xyz"
        mock_record.status.value = "pending"
        mock_handle = MagicMock()
        mock_handle.record = mock_record
        mock_rm.launch_run = AsyncMock(return_value=mock_handle)
        ctx = CapabilityContext(workflow_id="wf1", run_manager=mock_rm, graph_store=mock_store)
        result = await handle_start_run(
            {
                "workflow_id": "wf1",
                "run_policy": {"profile": "long_running", "max_duration": 60.0},
            },
            ctx,
        )
        assert result.success is True
        assert "run-xyz" in result.message
        call = mock_rm.launch_run.await_args
        assert call.kwargs["graph_id"] == "wf1"
        assert call.kwargs["run_policy"] == {"profile": "long_running", "max_duration": 60.0}
        assert call.kwargs["inputs"] is None
        assert call.kwargs["run_id"] is None
        assert call.kwargs["session_id"] is None
        assert call.kwargs["bus"] is None
        assert call.args[0] == _graph_data(graph)


# ── get_run_status ─────────────────────────────────────────────────

class TestGetRunStatus:

    @pytest.mark.asyncio
    async def test_no_run_manager(self):
        ctx = CapabilityContext(workflow_id="wf1")
        result = await handle_get_run_status({"run_id": "r1"}, ctx)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_run_id_required(self):
        ctx = CapabilityContext(workflow_id="wf1", run_manager=MagicMock())
        result = await handle_get_run_status({}, ctx)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_resolve_fails(self):
        rm = MagicMock()
        rm.list_runs.return_value = []
        ctx = CapabilityContext(workflow_id="wf1", run_manager=rm)
        result = await handle_get_run_status({"run_id": "latest"}, ctx)
        assert result.success is False
        assert "Could not resolve" in result.message

    @pytest.mark.asyncio
    async def test_run_found(self):
        rm = MagicMock()
        rm.list_runs.return_value = [{"run_id": "r1", "started_at": 100}]
        mock_record = MagicMock()
        mock_record.snapshot.return_value = {
            "run_id": "r1",
            "graph_id": "wf1",
            "status": "running",
            "node_statuses": {},
            "started_at": 100,
        }
        rm.get_run.return_value = mock_record
        ctx = CapabilityContext(workflow_id="wf1", run_manager=rm)
        result = await handle_get_run_status({"run_id": "r1"}, ctx)
        assert result.success is True
        assert "r1" in result.message


# ── list_active_runs ───────────────────────────────────────────────

class TestListActiveRuns:

    @pytest.mark.asyncio
    async def test_no_activity_tracker(self):
        ctx = CapabilityContext(workflow_id="wf1")
        result = await handle_list_active_runs({}, ctx)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_with_activity(self):
        mock_tracker = MagicMock()
        mock_tracker.get_activity.return_value = {
            "active": [{"run_id": "r1", "graph_id": "wf1", "status": "running"}],
            "recent": [],
            "connected_surfaces": [],
        }
        ctx = CapabilityContext(workflow_id="wf1", activity_tracker=mock_tracker)
        result = await handle_list_active_runs({}, ctx)
        assert result.success is True
        assert "r1" in result.message


# ── cancel_run ──────────────────────────────────────────────────────

class TestCancelRun:

    @pytest.mark.asyncio
    async def test_cancel_success(self):
        rm = MagicMock()
        rm.list_runs.return_value = [{"run_id": "r1"}]
        rm.cancel_run.return_value = True
        ctx = CapabilityContext(workflow_id="wf1", run_manager=rm)
        result = await handle_cancel_run({"run_id": "r1"}, ctx)
        assert result.success is True
        assert "cancelled" in result.message.lower()


# ── resume_run ──────────────────────────────────────────────────────

class TestResumeRun:

    @pytest.mark.asyncio
    async def test_resume_success(self):
        graph = _minimal_graph("wf1")
        mock_store = MagicMock()
        mock_store.load_as_model.return_value = graph
        mock_rm = MagicMock()
        mock_record = MagicMock()
        mock_record.run_id = "r1"
        mock_record.status.value = "pending"
        mock_rm.resume_run = AsyncMock(return_value=mock_record)
        ctx = CapabilityContext(workflow_id="wf1", run_manager=mock_rm, graph_store=mock_store)
        result = await handle_resume_run(
            {
                "run_id": "r1",
                "workflow_id": "wf1",
                "run_policy": {"max_cost": 1.25},
            },
            ctx,
        )
        assert result.success is True
        assert "resumed" in result.message
        call = mock_rm.resume_run.await_args
        assert call.kwargs["graph_id"] == "wf1"
        assert call.kwargs["run_id"] == "r1"
        assert call.kwargs["session_id"] is None
        assert call.kwargs["run_policy"] == {"max_cost": 1.25}
        assert call.args[0] == graph


# ── get_run_logs ───────────────────────────────────────────────────

class TestGetRunLogs:

    @pytest.mark.asyncio
    async def test_logs_from_record(self):
        rm = MagicMock()
        rm.list_runs.return_value = [{"run_id": "r1"}]
        mock_record = MagicMock()
        mock_record.graph_id = "wf1"
        mock_record.events = [
            {"event_type": "run_started", "timestamp": 100},
            {"event_type": "node_completed", "node_id": "n1", "timestamp": 101},
        ]
        rm.get_run.return_value = mock_record
        ctx = CapabilityContext(workflow_id="wf1", run_manager=rm)
        result = await handle_get_run_logs({"run_id": "r1"}, ctx)
        assert result.success is True
        assert "run_started" in result.message or "node_completed" in result.message


# ── get_run_checkpoints ────────────────────────────────────────────

class TestGetRunCheckpoints:

    @pytest.mark.asyncio
    async def test_no_checkpoint(self):
        rm = MagicMock()
        rm.list_runs.return_value = [{"run_id": "r1"}]
        rm.get_checkpoint_info = AsyncMock(return_value=None)
        ctx = CapabilityContext(workflow_id="wf1", run_manager=rm)
        result = await handle_get_run_checkpoints({"run_id": "r1"}, ctx)
        assert result.success is True
        assert "No checkpoint" in result.message

    @pytest.mark.asyncio
    async def test_with_checkpoint(self):
        rm = MagicMock()
        rm.list_runs.return_value = [{"run_id": "r1"}]
        rm.get_checkpoint_info = AsyncMock(return_value={
            "run_id": "r1",
            "phase": "resumable",
            "stop_reason": "duration_limit",
            "completed_node_ids": ["n1", "n2"],
            "pending_node_ids": ["n3"],
            "remaining_node_ids": ["n3"],
            "node_output_keys": ["n1", "n2"],
        })
        ctx = CapabilityContext(workflow_id="wf1", run_manager=rm)
        result = await handle_get_run_checkpoints({"run_id": "r1"}, ctx)
        assert result.success is True
        assert "n1" in result.message or "n2" in result.message
        assert "resumable" in result.message
        assert "duration_limit" in result.message


# ── rerun_from_checkpoint ──────────────────────────────────────────

class TestRerunFromCheckpoint:

    @pytest.mark.asyncio
    async def test_rerun_value_error(self):
        graph = _minimal_graph("wf1")
        mock_store = MagicMock()
        mock_store.load_as_model.return_value = graph
        mock_rm = MagicMock()
        mock_rm.rerun_from_checkpoint = AsyncMock(side_effect=ValueError("No checkpoint"))
        ctx = CapabilityContext(workflow_id="wf1", run_manager=mock_rm, graph_store=mock_store)
        result = await handle_rerun_from_checkpoint(
            {
                "source_run_id": "r1",
                "workflow_id": "wf1",
                "scope_type": "downstream_of",
                "target_node_id": "n1",
                "run_policy": {"profile": "long_running"},
            },
            ctx,
        )
        assert result.success is False
        assert "Invalid" in result.message or "checkpoint" in result.message

    @pytest.mark.asyncio
    async def test_rerun_forwards_run_policy(self):
        graph = _minimal_graph("wf1")
        mock_store = MagicMock()
        mock_store.load_as_model.return_value = graph
        mock_rm = MagicMock()
        mock_record = MagicMock()
        mock_record.run_id = "rerun-1"
        mock_record.status.value = "pending"
        mock_rm.rerun_from_checkpoint = AsyncMock(return_value=mock_record)
        ctx = CapabilityContext(workflow_id="wf1", run_manager=mock_rm, graph_store=mock_store)

        result = await handle_rerun_from_checkpoint(
            {
                "source_run_id": "r1",
                "workflow_id": "wf1",
                "scope_type": "downstream_of",
                "target_node_id": "n1",
                "run_policy": {"max_duration": 30.0},
            },
            ctx,
        )

        assert result.success is True
        call = mock_rm.rerun_from_checkpoint.await_args
        assert call.kwargs["run_policy"] == {"max_duration": 30.0}


# ── submit_human_input ─────────────────────────────────────────────

class TestApplyPendingOverlay:

    @pytest.mark.asyncio
    async def test_apply_overlay_success(self):
        rm = MagicMock()
        rm.list_runs.return_value = [{"run_id": "r1", "started_at": 100}]
        rm.apply_pending_overlay = AsyncMock(return_value=True)
        ctx = CapabilityContext(workflow_id="wf1", run_manager=rm)

        result = await handle_apply_pending_overlay(
            {
                "run_id": "r1",
                "node_id": "n2",
                "patch": {"tool_id": "tool:fixed"},
                "source": "user",
                "reason": "repair config drift",
            },
            ctx,
        )

        assert result.success is True
        assert "overlay applied" in result.message.lower()
        rm.apply_pending_overlay.assert_awaited_once_with(
            "r1",
            "n2",
            {"tool_id": "tool:fixed"},
            source="user",
            reason="repair config drift",
        )

    @pytest.mark.asyncio
    async def test_apply_overlay_rejected_when_not_pending(self):
        rm = MagicMock()
        rm.list_runs.return_value = [{"run_id": "r1", "started_at": 100}]
        rm.apply_pending_overlay = AsyncMock(return_value=False)
        ctx = CapabilityContext(workflow_id="wf1", run_manager=rm)

        result = await handle_apply_pending_overlay(
            {
                "run_id": "r1",
                "node_id": "n2",
                "patch": {"tool_id": "tool:fixed"},
            },
            ctx,
        )

        assert result.success is False
        assert "not applied" in result.message.lower()


# ── submit_human_input ─────────────────────────────────────────────

class TestSubmitHumanInput:

    @pytest.mark.asyncio
    async def test_submit_success(self):
        rm = MagicMock()
        rm.submit_human_input.return_value = True
        ctx = CapabilityContext(workflow_id="wf1", run_manager=rm)
        result = await handle_submit_human_input(
            {"run_id": "r1", "request_id": "req1", "response": {"approved": True}},
            ctx,
        )
        assert result.success is True
        assert "submitted" in result.message.lower()

    @pytest.mark.asyncio
    async def test_submit_rejected(self):
        rm = MagicMock()
        rm.submit_human_input.return_value = False
        ctx = CapabilityContext(workflow_id="wf1", run_manager=rm)
        result = await handle_submit_human_input(
            {"run_id": "r1", "request_id": "req1", "response": {"approved": True}},
            ctx,
        )
        assert result.success is True
        assert "rejected" in result.message.lower()


# ── Registry ───────────────────────────────────────────────────────

class TestRunLifecycleRegistry:

    def test_all_tools_registered(self):
        reg = ChatCapabilityRegistry()
        register_run_lifecycle_capabilities(reg)
        names = reg.list_tool_names()
        expected = {
            "start_run",
            "get_run_status",
            "list_active_runs",
            "cancel_run",
            "resume_run",
            "get_run_logs",
            "get_run_checkpoints",
            "rerun_from_checkpoint",
            "apply_pending_overlay",
            "submit_human_input",
        }
        for name in expected:
            assert name in names, f"Missing tool: {name}"

    def test_write_tools_in_agent_mode(self):
        reg = ChatCapabilityRegistry()
        register_run_lifecycle_capabilities(reg)
        agent_tools = reg.list_tool_names("agent")
        assert "start_run" in agent_tools
        assert "cancel_run" in agent_tools
        assert "resume_run" in agent_tools
        assert "apply_pending_overlay" in agent_tools
        assert "submit_human_input" in agent_tools

    def test_read_tools_in_ask_mode(self):
        reg = ChatCapabilityRegistry()
        register_run_lifecycle_capabilities(reg)
        ask_tools = reg.list_tool_names("ask")
        assert "get_run_status" in ask_tools
        assert "list_active_runs" in ask_tools
        assert "get_run_logs" in ask_tools
        assert "get_run_checkpoints" in ask_tools
        assert "start_run" not in ask_tools
        assert "cancel_run" not in ask_tools
