"""Tests for the unified PublishRuntime (GatewayRuntime + LocalRuntime)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dan.publish.runtime import (
    GatewayRuntime,
    LocalRuntime,
    PublishRuntime,
    _map_engine_event,
    create_publish_runtime,
)


# ---------------------------------------------------------------------------
# LocalRuntime
# ---------------------------------------------------------------------------

class TestLocalRuntime:
    def test_mode(self):
        rt = LocalRuntime()
        assert rt.mode == "local"

    def test_session_store_exposed(self):
        rt = LocalRuntime()
        assert rt.session_store is not None

    @pytest.mark.asyncio
    async def test_run_sync_returns_result(self):
        rt = LocalRuntime()
        mock_result = MagicMock(outputs={"text": "hello"}, success=True)

        with patch("dan.engine.Engine", autospec=False) as MockEngine:
            mock_engine = MockEngine.return_value
            mock_engine.run = AsyncMock(return_value=mock_result)

            result = await rt.run_sync("test_wf", MagicMock(), {"key": "val"})

        assert result["status"] == "completed"
        assert result["output"] == {"text": "hello"}
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_run_sync_captures_error(self):
        rt = LocalRuntime()

        with patch("dan.engine.Engine", autospec=False) as MockEngine:
            mock_engine = MockEngine.return_value
            mock_engine.run = AsyncMock(side_effect=RuntimeError("boom"))

            result = await rt.run_sync("test_wf", MagicMock(), {})

        assert result["status"] == "failed"
        assert "boom" in result["error"]

    @pytest.mark.asyncio
    async def test_run_async_returns_session_id(self):
        rt = LocalRuntime()

        with patch("dan.engine.Engine", autospec=False) as MockEngine:
            mock_engine = MockEngine.return_value
            mock_engine.run = AsyncMock(return_value=MagicMock(outputs={}, success=True))

            session_id = await rt.run_async("test_wf", MagicMock(), {})

        assert isinstance(session_id, str)
        assert len(session_id) > 0
        await asyncio.sleep(0.05)

    @pytest.mark.asyncio
    async def test_get_status_returns_none_for_unknown(self):
        rt = LocalRuntime()
        result = await rt.get_status("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_cancel_returns_false_for_unknown(self):
        rt = LocalRuntime()
        result = await rt.cancel("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_tracked_task(self):
        rt = LocalRuntime()

        async def slow_run(*a, **kw):
            await asyncio.sleep(100)
            return MagicMock(outputs={}, success=True)

        with patch("dan.engine.Engine", autospec=False) as MockEngine:
            mock_engine = MockEngine.return_value
            mock_engine.run = slow_run

            session_id = await rt.run_async("test_wf", MagicMock(), {})
            await asyncio.sleep(0.01)

            result = await rt.cancel(session_id)
            assert result is True

            status = await rt.get_status(session_id)
            assert status is not None
            assert "Cancelled" in (status.get("error", "") or "")

    @pytest.mark.asyncio
    async def test_close_cancels_tasks(self):
        rt = LocalRuntime()

        async def slow_run(*a, **kw):
            await asyncio.sleep(100)
            return MagicMock(outputs={}, success=True)

        with patch("dan.engine.Engine", autospec=False) as MockEngine:
            mock_engine = MockEngine.return_value
            mock_engine.run = slow_run

            await rt.run_async("test_wf", MagicMock(), {})
            await asyncio.sleep(0.01)

            assert len(rt._tasks) == 1
            await rt.close()
            assert len(rt._tasks) == 0


# ---------------------------------------------------------------------------
# GatewayRuntime
# ---------------------------------------------------------------------------

class TestGatewayRuntime:
    def test_mode(self):
        rt = GatewayRuntime()
        assert rt.mode == "gateway"

    @pytest.mark.asyncio
    async def test_run_sync_dispatches(self):
        rt = GatewayRuntime(server_url="http://localhost:8942")

        async def _events(run_id):
            yield {"event_type": "run_completed", "data": {"output": "ok"}}

        with patch("dan.client.DanClient") as MockClient:
            mock = MockClient.return_value
            mock.register_surface = AsyncMock()
            mock.dispatch = AsyncMock(return_value=MagicMock(run_id="r1"))
            mock.subscribe_run = _events

            result = await rt.run_sync("test_wf", MagicMock(), {"k": "v"})

        assert result["session_id"] == "r1"
        assert result["status"] == "completed"
        assert result["success"] is True
        assert "output" in result
        mock.dispatch.assert_called_once_with(
            workflow_id="test_wf",
            inputs={"k": "v"},
            surface_id=rt.surface_id,
        )

    @pytest.mark.asyncio
    async def test_run_async_returns_run_id(self):
        rt = GatewayRuntime(server_url="http://localhost:8942")

        with patch("dan.client.DanClient") as MockClient:
            mock = MockClient.return_value
            mock.register_surface = AsyncMock()
            mock.dispatch = AsyncMock(return_value=MagicMock(run_id="r2"))

            result = await rt.run_async("test_wf", MagicMock(), {})

        assert result == "r2"

    @pytest.mark.asyncio
    async def test_submit_input_resolves_request_id(self):
        rt = GatewayRuntime(server_url="http://localhost:8942")

        pending_item = MagicMock(
            run_id="r1",
            request_id="req-1",
            node_id="n1",
            prompt="Enter value",
        )

        with patch("dan.client.DanClient") as MockClient:
            mock = MockClient.return_value
            mock.register_surface = AsyncMock()
            mock.get_pending_inputs = AsyncMock(return_value=[pending_item])
            mock.submit_human_input = AsyncMock(return_value=True)

            result = await rt.submit_input("r1", {"answer": "yes"})

        assert result is not None
        assert result["status"] == "input_submitted"
        mock.submit_human_input.assert_called_once_with("r1", "req-1", {"answer": "yes"})

    @pytest.mark.asyncio
    async def test_submit_input_returns_none_when_no_pending(self):
        rt = GatewayRuntime(server_url="http://localhost:8942")

        with patch("dan.client.DanClient") as MockClient:
            mock = MockClient.return_value
            mock.register_surface = AsyncMock()
            mock.get_pending_inputs = AsyncMock(return_value=[])

            result = await rt.submit_input("r1", {"answer": "yes"})

        assert result is None

    @pytest.mark.asyncio
    async def test_cancel_delegates(self):
        rt = GatewayRuntime(server_url="http://localhost:8942")

        with patch("dan.client.DanClient") as MockClient:
            mock = MockClient.return_value
            mock.register_surface = AsyncMock()
            mock.cancel_run = AsyncMock(return_value=True)

            result = await rt.cancel("r1")

        assert result is True

    @pytest.mark.asyncio
    async def test_close(self):
        rt = GatewayRuntime(server_url="http://localhost:8942")

        with patch("dan.client.DanClient") as MockClient:
            mock = MockClient.return_value
            mock.register_surface = AsyncMock()
            mock.close = AsyncMock()
            mock.dispatch = AsyncMock(return_value=MagicMock(run_id="r1"))

            await rt.run_async("wf", MagicMock(), {})
            await rt.close()
            mock.close.assert_called_once()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

class TestCreatePublishRuntime:
    @pytest.mark.asyncio
    async def test_force_local_returns_local(self):
        rt = await create_publish_runtime(force_local=True)
        assert isinstance(rt, LocalRuntime)
        assert rt.mode == "local"

    @pytest.mark.asyncio
    async def test_server_unavailable_falls_back(self):
        with patch("dan.client.DanClient") as MockClient:
            mock = MockClient.return_value
            mock.is_server_available = AsyncMock(return_value=False)
            mock.close = AsyncMock()

            rt = await create_publish_runtime()

        assert isinstance(rt, LocalRuntime)

    @pytest.mark.asyncio
    async def test_server_available_returns_gateway(self):
        with patch("dan.client.DanClient") as MockClient:
            mock = MockClient.return_value
            mock.is_server_available = AsyncMock(return_value=True)
            mock.register_surface = AsyncMock()

            rt = await create_publish_runtime()

        assert isinstance(rt, GatewayRuntime)
        assert rt.mode == "gateway"


# ---------------------------------------------------------------------------
# Event mapping
# ---------------------------------------------------------------------------

class TestMapEngineEvent:
    def test_run_completed(self):
        result = _map_engine_event("run_completed", {"data": {"output": "ok"}, "timestamp": 1.0})
        assert result["type"] == "session_completed"

    def test_run_failed(self):
        result = _map_engine_event("run_failed", {"data": {"error": "crash"}, "timestamp": 1.0})
        assert result["type"] == "session_failed"
        assert result["error"] == "crash"

    def test_human_input_needed(self):
        result = _map_engine_event("human_input_needed", {"data": {}, "timestamp": 1.0})
        assert result["type"] == "status_changed"
        assert result["status"] == "awaiting_input"

    def test_node_started(self):
        result = _map_engine_event("node_started", {"data": {"node_id": "n1"}, "timestamp": 1.0})
        assert result["type"] == "progress"

    def test_node_skipped(self):
        result = _map_engine_event("node_skipped", {"data": {"node_id": "n2"}, "timestamp": 1.0})
        assert result["type"] == "progress"
        assert result["event_type"] == "node_skipped"

    def test_unknown_returns_none(self):
        result = _map_engine_event("some_other_event", {"data": {}, "timestamp": 1.0})
        assert result is None


# ---------------------------------------------------------------------------
# Additional coverage (I5 review items)
# ---------------------------------------------------------------------------

class TestLocalRuntimeSubscribeEvents:
    @pytest.mark.asyncio
    async def test_subscribe_emits_events(self):
        rt = LocalRuntime()

        async def _slow_engine_run(*a, **kw):
            await asyncio.sleep(0.02)
            return MagicMock(outputs={"done": True}, success=True)

        with patch("dan.engine.Engine", autospec=False) as MockEngine:
            mock_engine = MockEngine.return_value
            mock_engine.run = _slow_engine_run

            session_id = await rt.run_async("wf1", MagicMock(), {})
            events = []
            async for ev in rt.subscribe_events(session_id):
                events.append(ev)
                if ev.get("type") == "session_end":
                    break

        assert len(events) >= 1
        types = [e.get("type") for e in events]
        assert "session_end" in types


class TestGatewayRuntimeSubscribeEvents:
    @pytest.mark.asyncio
    async def test_subscribe_maps_engine_events(self):
        rt = GatewayRuntime(server_url="http://localhost:8942")

        async def _events(run_id):
            yield {"event_type": "run_started", "data": {}, "timestamp": 1.0}
            yield {"event_type": "node_started", "data": {"node_id": "n1"}, "timestamp": 2.0}
            yield {"event_type": "run_completed", "data": {"output": "ok"}, "timestamp": 3.0}

        with patch("dan.client.DanClient") as MockClient:
            mock = MockClient.return_value
            mock.register_surface = AsyncMock()
            mock.subscribe_run = _events

            events = []
            async for ev in rt.subscribe_events("r1"):
                events.append(ev)

        assert len(events) == 3
        assert events[0]["type"] == "status_changed"
        assert events[1]["type"] == "progress"
        assert events[2]["type"] == "session_completed"


class TestGatewayRuntimeGetStatus:
    @pytest.mark.asyncio
    async def test_get_status_with_pending(self):
        rt = GatewayRuntime(server_url="http://localhost:8942")

        pending_item = MagicMock(
            run_id="r1",
            request_id="req-1",
            node_id="n1",
            prompt="Enter value",
            input_schema={"type": "string"},
        )

        with patch("dan.client.DanClient") as MockClient:
            mock = MockClient.return_value
            mock.register_surface = AsyncMock()
            mock.get_run_status = AsyncMock(return_value={"status": "running"})
            mock.get_pending_inputs = AsyncMock(return_value=[pending_item])

            result = await rt.get_status("r1")

        assert result["session_id"] == "r1"
        assert result["status"] == "running"
        assert "pending_prompt" in result
        assert result["pending_prompt"]["request_id"] == "req-1"


class TestGatewayRuntimeStreamLost:
    @pytest.mark.asyncio
    async def test_run_sync_raises_when_stream_ends_early(self):
        rt = GatewayRuntime(server_url="http://localhost:8942")

        async def _events(run_id):
            yield {"event_type": "node_started", "data": {"node_id": "n1"}}
            # Stream ends without run_completed/run_failed/run_cancelled

        with patch("dan.client.DanClient") as MockClient:
            mock = MockClient.return_value
            mock.register_surface = AsyncMock()
            mock.dispatch = AsyncMock(return_value=MagicMock(run_id="r1"))
            mock.subscribe_run = _events

            with pytest.raises(RuntimeError, match="terminal event"):
                await rt.run_sync("wf", MagicMock(), {})


class TestGatewayRuntimeRunSyncErrors:
    @pytest.mark.asyncio
    async def test_run_sync_raises_on_failure(self):
        rt = GatewayRuntime(server_url="http://localhost:8942")

        async def _events(run_id):
            yield {"event_type": "run_failed", "data": {"error": "crash"}}

        with patch("dan.client.DanClient") as MockClient:
            mock = MockClient.return_value
            mock.register_surface = AsyncMock()
            mock.dispatch = AsyncMock(return_value=MagicMock(run_id="r1"))
            mock.subscribe_run = _events

            with pytest.raises(RuntimeError, match="crash"):
                await rt.run_sync("wf", MagicMock(), {})

    @pytest.mark.asyncio
    async def test_run_sync_raises_on_cancel(self):
        rt = GatewayRuntime(server_url="http://localhost:8942")

        async def _events(run_id):
            yield {"event_type": "run_cancelled", "data": {}}

        with patch("dan.client.DanClient") as MockClient:
            mock = MockClient.return_value
            mock.register_surface = AsyncMock()
            mock.dispatch = AsyncMock(return_value=MagicMock(run_id="r1"))
            mock.subscribe_run = _events

            with pytest.raises(RuntimeError, match="cancelled"):
                await rt.run_sync("wf", MagicMock(), {})
