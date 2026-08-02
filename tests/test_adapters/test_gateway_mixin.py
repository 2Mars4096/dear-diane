"""Tests for GatewayAdapterMixin (Phase 13, Plan 23-4)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dan.adapters.gateway_mixin import GatewayAdapterMixin
from dan.adapters.base import AdapterConfig


class MockAdapter(GatewayAdapterMixin):
    """Minimal adapter for testing the mixin."""

    def __init__(self, config: AdapterConfig):
        self._config = config
        self.surface_id = "test-adapter"
        self.surface_type = "test"
        self.sent_messages: list[tuple[str, str]] = []
        self._pending_requests: dict = {}
        self._relay_tasks: set = set()

    @property
    def adapter_config(self) -> AdapterConfig:
        return self._config

    async def send_message(self, session_id: str, text: str) -> None:
        self.sent_messages.append((session_id, text))


class TestGatewayInit:
    @pytest.mark.asyncio
    async def test_local_mode(self):
        config = AdapterConfig(workflow_path="test.json", local_mode=True)
        adapter = MockAdapter(config)
        mode = await adapter.init_gateway_client()
        assert mode == "local"

    @pytest.mark.asyncio
    async def test_server_mode_detection(self):
        config = AdapterConfig(workflow_path="test.json")
        adapter = MockAdapter(config)
        with patch("dan.client.local.DanClientOrLocal") as MockCOL:
            mock_instance = MockCOL.return_value
            mock_instance.detect_mode = AsyncMock(return_value="server")
            mock_instance.register_surface = AsyncMock()
            mode = await adapter.init_gateway_client()
        assert mode == "server"


class TestDispatchViaGateway:
    @pytest.mark.asyncio
    async def test_no_client_returns_none(self):
        config = AdapterConfig(workflow_path="test.json", local_mode=True)
        adapter = MockAdapter(config)
        result = await adapter.dispatch_via_gateway(
            "session-1", workflow_path="/test.json"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_dispatch_with_server(self):
        config = AdapterConfig(workflow_path="test.json")
        adapter = MockAdapter(config)

        from dan.client.models import DispatchResult

        mock_client = MagicMock()
        mock_client.is_server_mode = True
        mock_client.dispatch = AsyncMock(
            return_value=DispatchResult(
                run_id="r1", workflow_name="test", status="pending"
            )
        )

        async def empty_stream(run_id):
            return
            yield  # make it an async generator

        mock_client.subscribe_run = empty_stream
        adapter._dan_client = mock_client

        run_id = await adapter.dispatch_via_gateway(
            "session-1", workflow_path="/test.json"
        )
        assert run_id == "r1"


class TestHandleGatewayResponse:
    @pytest.mark.asyncio
    async def test_no_pending(self):
        config = AdapterConfig(workflow_path="test.json")
        adapter = MockAdapter(config)
        result = await adapter.handle_gateway_response("session-1", "hello")
        assert result is False

    @pytest.mark.asyncio
    async def test_with_pending(self):
        config = AdapterConfig(workflow_path="test.json")
        adapter = MockAdapter(config)
        mock_client = MagicMock()
        mock_client.is_server_mode = True
        mock_client.submit_human_input = AsyncMock(return_value=True)
        adapter._dan_client = mock_client

        adapter._pending_requests["session-1"] = {
            "run_id": "r1",
            "request_id": "req-1",
        }
        result = await adapter.handle_gateway_response("session-1", "yes")
        assert result is True


class TestCancelGatewayRun:
    @pytest.mark.asyncio
    async def test_cancel_no_client(self):
        config = AdapterConfig(workflow_path="test.json")
        adapter = MockAdapter(config)
        assert await adapter.cancel_gateway_run("s1", "r1") is False

    @pytest.mark.asyncio
    async def test_cancel_with_client(self):
        config = AdapterConfig(workflow_path="test.json")
        adapter = MockAdapter(config)
        mock_client = MagicMock()
        mock_client.is_server_mode = True
        mock_client.cancel_run = AsyncMock(return_value=True)
        adapter._dan_client = mock_client
        assert await adapter.cancel_gateway_run("s1", "r1") is True
