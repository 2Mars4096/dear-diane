"""Tests for PublishGatewayClient (Phase 13, Plan 23-5)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dan.publish.gateway_mode import PublishGatewayClient


class TestPublishGatewayInit:
    @pytest.mark.asyncio
    async def test_force_local(self):
        client = PublishGatewayClient(force_local=True)
        mode = await client.init()
        assert mode == "local"
        assert client.is_server_mode is False

    @pytest.mark.asyncio
    async def test_init_returns_mode(self):
        client = PublishGatewayClient(force_local=True)
        mode = await client.init()
        assert mode in ("server", "local")

    @pytest.mark.asyncio
    async def test_server_mode_with_mock(self):
        client = PublishGatewayClient()
        with patch("dan.client.local.DanClientOrLocal") as MockCOL:
            mock_instance = MockCOL.return_value
            mock_instance.detect_mode = AsyncMock(return_value="server")
            mock_instance.register_surface = AsyncMock()
            mode = await client.init()
        assert mode == "server"
        assert client.is_server_mode is True


class TestPublishDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_and_wait_not_server_raises(self):
        client = PublishGatewayClient(force_local=True)
        await client.init()
        with pytest.raises(RuntimeError, match="Not in server mode"):
            await client.dispatch_and_wait(workflow_id="test")

    @pytest.mark.asyncio
    async def test_dispatch_async_not_server_raises(self):
        client = PublishGatewayClient(force_local=True)
        await client.init()
        with pytest.raises(RuntimeError, match="Not in server mode"):
            await client.dispatch_async(workflow_id="test")

    @pytest.mark.asyncio
    async def test_dispatch_uses_registered_surface_id(self):
        client = PublishGatewayClient()

        async def _events(run_id: str):  # noqa: ARG001
            yield {"event_type": "run_completed", "data": {}}

        with patch("dan.client.local.DanClientOrLocal") as MockCOL:
            mock_instance = MockCOL.return_value
            mock_instance.detect_mode = AsyncMock(return_value="server")
            mock_instance.register_surface = AsyncMock()
            mock_instance.dispatch = AsyncMock(return_value=MagicMock(run_id="r1"))
            mock_instance.subscribe_run = _events

            await client.init()
            await client.dispatch_and_wait(workflow_id="test")

            registered_id = mock_instance.register_surface.call_args.kwargs["surface_id"]
            dispatched_id = mock_instance.dispatch.call_args.kwargs["surface_id"]
            assert dispatched_id == registered_id


class TestPublishCancel:
    @pytest.mark.asyncio
    async def test_cancel_not_server(self):
        client = PublishGatewayClient(force_local=True)
        await client.init()
        result = await client.cancel("r1")
        assert result is False


class TestPublishSubmitInput:
    @pytest.mark.asyncio
    async def test_submit_not_server(self):
        client = PublishGatewayClient(force_local=True)
        await client.init()
        result = await client.submit_input("r1", "req-1", {"answer": "yes"})
        assert result is False


class TestPublishClose:
    @pytest.mark.asyncio
    async def test_close_safe(self):
        client = PublishGatewayClient(force_local=True)
        await client.init()
        await client.close()
