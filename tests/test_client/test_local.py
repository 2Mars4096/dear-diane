"""Tests for DanClientOrLocal (Phase 13, Plan 23-2 task 6)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dan.client.local import DanClientOrLocal
from dan.client.models import DispatchResult
from dan.executors.tool import ToolExecutor
from dan.executors.tool import ToolRegistry
from dan.worker.adapters import WorkerBackedLegacyComputeExecutor
from dan.worker.executor import WorkerExecutor


class TestModeDetection:
    @pytest.mark.asyncio
    async def test_force_local(self):
        col = DanClientOrLocal(force_local=True)
        mode = await col.detect_mode()
        assert mode == "local"
        assert col.is_server_mode is False

    @pytest.mark.asyncio
    async def test_server_available(self):
        col = DanClientOrLocal()
        with patch.object(col._client, "ping", return_value=True):
            mode = await col.detect_mode()
        assert mode == "server"
        assert col.is_server_mode is True

    @pytest.mark.asyncio
    async def test_server_unavailable(self):
        col = DanClientOrLocal()
        with patch.object(col._client, "ping", return_value=False):
            mode = await col.detect_mode()
        assert mode == "local"
        assert col.is_server_mode is False

    @pytest.mark.asyncio
    async def test_server_error(self):
        col = DanClientOrLocal()
        with patch.object(col._client, "ping", side_effect=Exception("network")):
            mode = await col.detect_mode()
        assert mode == "local"

    @pytest.mark.asyncio
    async def test_server_unavailable_strict_raises(self):
        col = DanClientOrLocal(server_url="http://127.0.0.1:8000")
        with patch.object(col._client, "ping", return_value=False):
            with pytest.raises(RuntimeError, match="not reachable"):
                await col.detect_mode(strict_server=True)

    @pytest.mark.asyncio
    async def test_server_error_strict_raises(self):
        col = DanClientOrLocal(server_url="http://127.0.0.1:8000")
        with patch.object(col._client, "ping", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="health check failed"):
                await col.detect_mode(strict_server=True)

    def test_is_server_mode_before_detect_raises(self):
        col = DanClientOrLocal()
        with pytest.raises(RuntimeError, match="detect_mode"):
            _ = col.is_server_mode


class TestServerModeDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_delegates_to_client(self):
        col = DanClientOrLocal()
        col._is_server_mode = True
        mock_result = DispatchResult(
            run_id="r1", workflow_name="test", status="pending"
        )
        col._client.dispatch = AsyncMock(return_value=mock_result)
        result = await col.dispatch(workflow_path="/test.json", surface_id="cli")
        assert result.run_id == "r1"
        col._client.dispatch.assert_called_once()


class TestLocalModeDispatch:
    @pytest.mark.asyncio
    async def test_local_dispatch_creates_run(self, tmp_path):
        from dan.models.graph import Graph

        graph = Graph.model_validate({
            "version": "dan_graph_v1",
            "metadata": {"name": "test"},
            "nodes": [
                {
                    "id": "inp",
                    "name": "inputs",
                    "node_type": "input",
                    "variables": [
                        {"name": "topic", "type": "string", "description": "Topic"},
                    ],
                    "output_ports": [{"name": "result"}],
                },
            ],
            "edges": [],
            "entry_points": ["inp"],
            "exit_points": ["inp"],
        })
        wf = tmp_path / "test.json"
        wf.write_text(graph.model_dump_json(indent=2))

        mock_engine = MagicMock()
        mock_engine.run = AsyncMock(return_value={"output": "done"})

        col = DanClientOrLocal(force_local=True)
        await col.detect_mode()
        with patch("dan.utils.workflow_loader.load_graph", return_value=graph), \
             patch.object(col, "_make_engine", return_value=mock_engine) as make_engine:
            result = await col.dispatch(workflow_path=str(wf))
        assert result.run_id.startswith("local-")
        assert result.status == "pending"
        make_engine.assert_called_once()

    @pytest.mark.asyncio
    async def test_make_engine_threads_event_callback_and_renderer(self):
        mock_engine_cls = MagicMock(return_value=object())
        renderer = object()
        engine_config = object()
        col = DanClientOrLocal(
            force_local=True,
            engine_config=engine_config,
            human_renderer=renderer,
        )

        async def _event_cb(event):
            return None

        with patch("dan.engine.scheduler.Engine", mock_engine_cls):
            engine = col._make_engine(event_callback=_event_cb)

        assert engine is mock_engine_cls.return_value
        mock_engine_cls.assert_called_once_with(
            config=engine_config,
            executor_registry=mock_engine_cls.call_args.kwargs["executor_registry"],
            event_callback=_event_cb,
            human_renderer=renderer,
        )

        executor_registry = mock_engine_cls.call_args.kwargs["executor_registry"]
        tool_executor = executor_registry.get("tool_operator")
        worker_executor = executor_registry.get("worker")
        assert isinstance(tool_executor, WorkerBackedLegacyComputeExecutor)
        assert isinstance(worker_executor, WorkerExecutor)
        assert tool_executor.worker_executor is worker_executor
        assert tool_executor.registry is worker_executor._tool.registry
        assert worker_executor._tool.registry.has("pdf_read")

    @pytest.mark.asyncio
    async def test_make_engine_preserves_custom_tools_and_adds_builtins(self):
        mock_engine_cls = MagicMock(return_value=object())
        registry = ToolRegistry()

        async def _custom_tool(**kwargs):
            return kwargs

        registry.register("custom_tool", _custom_tool)
        col = DanClientOrLocal(force_local=True, tool_registry=registry)

        with patch("dan.engine.scheduler.Engine", mock_engine_cls):
            col._make_engine()

        executor_registry = mock_engine_cls.call_args.kwargs["executor_registry"]
        tool_executor = executor_registry.get("tool_operator")
        worker_executor = executor_registry.get("worker")
        assert isinstance(tool_executor, WorkerBackedLegacyComputeExecutor)
        assert isinstance(worker_executor, WorkerExecutor)
        assert tool_executor.worker_executor is worker_executor
        assert tool_executor.registry is worker_executor._tool.registry
        assert worker_executor._tool.registry.has("custom_tool")
        assert worker_executor._tool.registry.has("pdf_read")

    @pytest.mark.asyncio
    async def test_local_dispatch_no_path_raises(self):
        col = DanClientOrLocal(force_local=True)
        await col.detect_mode()
        with pytest.raises(ValueError, match="workflow_path"):
            await col.dispatch()


class TestCancelRun:
    @pytest.mark.asyncio
    async def test_cancel_server_mode(self):
        col = DanClientOrLocal()
        col._is_server_mode = True
        col._client.cancel_run = AsyncMock(return_value=True)
        assert await col.cancel_run("r1") is True

    @pytest.mark.asyncio
    async def test_cancel_local_nonexistent(self):
        col = DanClientOrLocal(force_local=True)
        await col.detect_mode()
        assert await col.cancel_run("nonexistent") is False


class TestClose:
    @pytest.mark.asyncio
    async def test_close_with_client(self):
        col = DanClientOrLocal()
        col._client.close = AsyncMock()
        await col.close()
        col._client.close.assert_called_once()
