"""Tests for DanClient (Phase 13, Plan 23-2)."""

from __future__ import annotations

import json
import sys
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from dan.client.client import DanClient
from dan.client.errors import (
    ConnectionError,
    DispatchError,
    NotFoundError,
    RunLostError,
    ServerError,
)
from dan.client.models import DispatchResult


class TestDanClientInit:
    def test_default_url(self):
        client = DanClient()
        assert client._base_url == "http://localhost:8000"

    def test_custom_url(self):
        client = DanClient(base_url="http://myserver:9000")
        assert client._base_url == "http://myserver:9000"

    def test_env_url(self, monkeypatch):
        monkeypatch.setenv("DAN_SERVER_URL", "http://env-server:8080")
        client = DanClient()
        assert client._base_url == "http://env-server:8080"


class TestDanClientPing:
    @pytest.mark.asyncio
    async def test_ping_success(self):
        client = DanClient()
        mock_resp = httpx.Response(200, json={"status": "ok"})
        with patch.object(httpx.AsyncClient, "get", return_value=mock_resp):
            client._http = httpx.AsyncClient()
            assert await client.ping() is True

    @pytest.mark.asyncio
    async def test_ping_failure(self):
        client = DanClient()
        with patch.object(
            httpx.AsyncClient, "get", side_effect=httpx.ConnectError("refused")
        ):
            client._http = httpx.AsyncClient()
            assert await client.ping() is False


class TestDanClientDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_success(self):
        client = DanClient()
        mock_resp = httpx.Response(
            200,
            json={
                "run_id": "run-123",
                "workflow_name": "test",
                "status": "pending",
                "surface_id": "cli",
            },
        )
        with patch.object(httpx.AsyncClient, "post", return_value=mock_resp):
            client._http = httpx.AsyncClient()
            result = await client.dispatch(workflow_path="/test.json", surface_id="cli")
            assert result.run_id == "run-123"
            assert result.workflow_name == "test"

    @pytest.mark.asyncio
    async def test_dispatch_not_found(self):
        client = DanClient()
        mock_resp = httpx.Response(404, text="Workflow not found")
        with patch.object(httpx.AsyncClient, "post", return_value=mock_resp):
            client._http = httpx.AsyncClient()
            with pytest.raises(NotFoundError):
                await client.dispatch(workflow_id="nonexistent")

    @pytest.mark.asyncio
    async def test_dispatch_server_error(self):
        client = DanClient()
        mock_resp = httpx.Response(500, text="Internal error")
        with patch.object(httpx.AsyncClient, "post", return_value=mock_resp):
            client._http = httpx.AsyncClient()
            with pytest.raises(ServerError):
                await client.dispatch(workflow_path="/test.json")


class TestDanClientCancel:
    @pytest.mark.asyncio
    async def test_cancel_success(self):
        client = DanClient()
        mock_resp = httpx.Response(200, json={"run_id": "r1", "cancelled": True})
        with patch.object(httpx.AsyncClient, "post", return_value=mock_resp):
            client._http = httpx.AsyncClient()
            assert await client.cancel_run("r1") is True

    @pytest.mark.asyncio
    async def test_cancel_not_found(self):
        client = DanClient()
        mock_resp = httpx.Response(200, json={"run_id": "r1", "cancelled": False})
        with patch.object(httpx.AsyncClient, "post", return_value=mock_resp):
            client._http = httpx.AsyncClient()
            assert await client.cancel_run("r1") is False


class TestDanClientHumanInput:
    @pytest.mark.asyncio
    async def test_submit_success(self):
        client = DanClient()
        mock_resp = httpx.Response(
            200, json={"status": "resolved", "run_id": "r1", "request_id": "req-1"}
        )
        with patch.object(httpx.AsyncClient, "post", return_value=mock_resp):
            client._http = httpx.AsyncClient()
            ok = await client.submit_human_input("r1", "req-1", {"answer": "yes"})
            assert ok is True

    @pytest.mark.asyncio
    async def test_submit_not_found(self):
        client = DanClient()
        mock_resp = httpx.Response(404, text="Not found")
        with patch.object(httpx.AsyncClient, "post", return_value=mock_resp):
            client._http = httpx.AsyncClient()
            ok = await client.submit_human_input("r1", "req-1", {"answer": "yes"})
            assert ok is False


class TestDanClientListRuns:
    @pytest.mark.asyncio
    async def test_list_runs_unwraps(self):
        client = DanClient()
        mock_resp = httpx.Response(
            200,
            json={
                "runs": [
                    {"run_id": "r1", "graph_id": "g1", "status": "completed"},
                    {"run_id": "r2", "graph_id": "g2", "status": "running"},
                ],
                "total": 2,
            },
        )
        with patch.object(httpx.AsyncClient, "get", return_value=mock_resp):
            client._http = httpx.AsyncClient()
            runs = await client.list_runs()
            assert len(runs) == 2
            assert runs[0].run_id == "r1"


class TestDanClientConnectionError:
    @pytest.mark.asyncio
    async def test_connection_refused(self):
        client = DanClient(base_url="http://localhost:99999")
        with pytest.raises(ConnectionError):
            await client.dispatch(workflow_path="/test.json")


class TestDanClientSubscribeRun:
    @pytest.mark.asyncio
    async def test_clean_close_without_terminal_raises_run_lost(self):
        client = DanClient()

        class _FakeSocket:
            def __init__(self, msgs):
                self._msgs = msgs
                self._idx = 0

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._idx >= len(self._msgs):
                    raise StopAsyncIteration
                value = self._msgs[self._idx]
                self._idx += 1
                return value

        class _FakeConnectCtx:
            def __init__(self, msgs):
                self._msgs = msgs

            async def __aenter__(self):
                return _FakeSocket(self._msgs)

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class _FakeWebsockets:
            class exceptions:
                class ConnectionClosed(Exception):
                    pass

            @staticmethod
            def connect(url):  # noqa: ARG004
                return _FakeConnectCtx(
                    ['{"event_type":"node_started","run_id":"run-1"}']
                )

        with (
            patch.dict(sys.modules, {"websockets": _FakeWebsockets}),
            patch("dan.client.client._MAX_RECONNECT_RETRIES", 2),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            with pytest.raises(RunLostError):
                async for _ in client.subscribe_run("run-1"):
                    pass

    @pytest.mark.asyncio
    async def test_catchup_replays_pending_approval_events(self):
        client = DanClient()

        catchup = {
            "event_type": "_catchup",
            "run_id": "run-1",
            "snapshot": {"run_id": "run-1", "status": "pending"},
            "buffered_events": [
                {
                    "event_type": "human_input_needed",
                    "run_id": "run-1",
                    "data": {"request_id": "req-1", "render_mode": "approval"},
                },
                {
                    "event_type": "run_cancelled",
                    "run_id": "run-1",
                    "data": {"reason": "plan_rejected"},
                },
            ],
            "pending_human_inputs": [
                {
                    "event_type": "human_input_needed",
                    "run_id": "run-1",
                    "data": {"request_id": "req-1", "render_mode": "approval"},
                },
            ],
        }

        class _FakeSocket:
            def __init__(self, msgs):
                self._msgs = msgs
                self._idx = 0

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._idx >= len(self._msgs):
                    raise StopAsyncIteration
                value = self._msgs[self._idx]
                self._idx += 1
                return value

        class _FakeConnectCtx:
            def __init__(self, msgs):
                self._msgs = msgs

            async def __aenter__(self):
                return _FakeSocket(self._msgs)

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class _FakeWebsockets:
            class exceptions:
                class ConnectionClosed(Exception):
                    pass

            @staticmethod
            def connect(url):  # noqa: ARG004
                return _FakeConnectCtx([json.dumps(catchup)])

        with patch.dict(sys.modules, {"websockets": _FakeWebsockets}):
            events = []
            async for evt in client.subscribe_run("run-1"):
                events.append(evt)

            # Catch-up should replay human_input_needed exactly once
            assert events[0]["event_type"] == "human_input_needed"
            assert events[0]["data"]["request_id"] == "req-1"
            # Then terminal event should end the stream
            assert events[1]["event_type"] == "run_cancelled"
            assert len(events) == 2

    @pytest.mark.asyncio
    async def test_catchup_skips_non_pending_human_input_events(self):
        client = DanClient()

        catchup = {
            "event_type": "_catchup",
            "run_id": "run-1",
            "snapshot": {"run_id": "run-1", "status": "running"},
            "buffered_events": [
                {
                    "event_type": "human_input_needed",
                    "run_id": "run-1",
                    "data": {"request_id": "req-old", "render_mode": "approval"},
                },
                {
                    "event_type": "node_started",
                    "run_id": "run-1",
                    "node_id": "n1",
                    "data": {},
                },
                {
                    "event_type": "run_cancelled",
                    "run_id": "run-1",
                    "data": {"reason": "test"},
                },
            ],
            # No pending inputs -> req-old must not be replayed
            "pending_human_inputs": [],
        }

        class _FakeSocket:
            def __init__(self, msgs):
                self._msgs = msgs
                self._idx = 0

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._idx >= len(self._msgs):
                    raise StopAsyncIteration
                value = self._msgs[self._idx]
                self._idx += 1
                return value

        class _FakeConnectCtx:
            def __init__(self, msgs):
                self._msgs = msgs

            async def __aenter__(self):
                return _FakeSocket(self._msgs)

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class _FakeWebsockets:
            class exceptions:
                class ConnectionClosed(Exception):
                    pass

            @staticmethod
            def connect(url):  # noqa: ARG004
                return _FakeConnectCtx([json.dumps(catchup)])

        with patch.dict(sys.modules, {"websockets": _FakeWebsockets}):
            events = []
            async for evt in client.subscribe_run("run-1"):
                events.append(evt)

            assert all(
                not (
                    evt.get("event_type") == "human_input_needed"
                    and (evt.get("data") or {}).get("request_id") == "req-old"
                )
                for evt in events
            )
            assert events[0]["event_type"] == "node_started"

    @pytest.mark.asyncio
    async def test_subscribe_run_waits_for_automatic_recovery_completion(self):
        client = DanClient()

        class _FakeSocket:
            def __init__(self, msgs):
                self._msgs = msgs
                self._idx = 0

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._idx >= len(self._msgs):
                    raise StopAsyncIteration
                value = self._msgs[self._idx]
                self._idx += 1
                return value

        class _FakeConnectCtx:
            def __init__(self, msgs):
                self._msgs = msgs

            async def __aenter__(self):
                return _FakeSocket(self._msgs)

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class _FakeWebsockets:
            class exceptions:
                class ConnectionClosed(Exception):
                    pass

            @staticmethod
            def connect(url):  # noqa: ARG004
                return _FakeConnectCtx([
                    json.dumps({"event_type": "run_failed", "run_id": "run-1", "data": {"error": "boom"}}),
                    json.dumps({"event_type": "automatic_recovery_started", "run_id": "run-1", "data": {"selected_action": "rerun_from_checkpoint"}}),
                    json.dumps({"event_type": "automatic_recovery_completed", "run_id": "run-1", "data": {"status": "completed"}}),
                ])

        with patch.dict(sys.modules, {"websockets": _FakeWebsockets}):
            events = []
            async for evt in client.subscribe_run("run-1"):
                events.append(evt)

        assert [evt["event_type"] for evt in events] == [
            "run_failed",
            "automatic_recovery_started",
            "automatic_recovery_completed",
        ]

    @pytest.mark.asyncio
    async def test_subscribe_run_treats_final_failed_socket_close_as_terminal(self):
        client = DanClient()

        class _FakeSocket:
            def __init__(self, msgs):
                self._msgs = msgs
                self._idx = 0

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._idx >= len(self._msgs):
                    raise StopAsyncIteration
                value = self._msgs[self._idx]
                self._idx += 1
                return value

        class _FakeConnectCtx:
            def __init__(self, msgs):
                self._msgs = msgs

            async def __aenter__(self):
                return _FakeSocket(self._msgs)

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class _FakeWebsockets:
            class exceptions:
                class ConnectionClosed(Exception):
                    pass

            @staticmethod
            def connect(url):  # noqa: ARG004
                return _FakeConnectCtx([
                    json.dumps({"event_type": "run_failed", "run_id": "run-1", "data": {"error": "boom"}}),
                ])

        with patch.dict(sys.modules, {"websockets": _FakeWebsockets}):
            events = []
            async for evt in client.subscribe_run("run-1"):
                events.append(evt)

        assert [evt["event_type"] for evt in events] == ["run_failed"]
