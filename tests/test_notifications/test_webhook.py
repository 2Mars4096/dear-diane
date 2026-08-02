"""Tests for dan.notifications.webhook — WebhookNotifier."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dan.notifications.webhook import WebhookNotifier


def _make_config(
    url: str = "https://hooks.example.com/dan",
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
    event_types: list[str] | None = None,
) -> MagicMock:
    cfg = MagicMock()
    cfg.url = url
    cfg.headers = headers or {}
    cfg.timeout = timeout
    cfg.event_types = event_types if event_types is not None else [
        "run_completed", "run_failed", "human_input_needed"
    ]
    return cfg


def _make_event(event_type: str, **kwargs: Any) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "run_id": "r1",
        "workflow_name": "my-workflow",
        "data": {},
        "timestamp": 1700000000.0,
        "surface_id": "cli-1",
        **kwargs,
    }


# ── Payload construction ──────────────────────────────────────────────────


class TestPayload:
    @pytest.mark.asyncio
    async def test_run_completed_payload(self):
        notifier = WebhookNotifier(_make_config())
        notifier._post = AsyncMock()
        await notifier.notify(_make_event("run_completed"))
        payload = notifier._post.call_args[0][0]
        assert payload["event_type"] == "run_completed"
        assert payload["run_id"] == "r1"
        assert payload["workflow_name"] == "my-workflow"
        assert payload["message"] == "Run completed successfully"
        assert payload["surface_id"] == "cli-1"

    @pytest.mark.asyncio
    async def test_run_failed_payload(self):
        notifier = WebhookNotifier(_make_config())
        notifier._post = AsyncMock()
        event = _make_event("run_failed", data={"error": "OOM"})
        await notifier.notify(event)
        payload = notifier._post.call_args[0][0]
        assert "OOM" in payload["message"]

    @pytest.mark.asyncio
    async def test_human_input_payload(self):
        notifier = WebhookNotifier(_make_config())
        notifier._post = AsyncMock()
        event = _make_event("human_input_needed", data={"prompt": "Approve?"})
        await notifier.notify(event)
        payload = notifier._post.call_args[0][0]
        assert "Approve?" in payload["message"]

    @pytest.mark.asyncio
    async def test_workflow_id_fallback(self):
        notifier = WebhookNotifier(_make_config())
        notifier._post = AsyncMock()
        event = {"event_type": "run_completed", "run_id": "r1", "workflow_id": "wf-id", "data": {}, "timestamp": 0, "surface_id": None}
        await notifier.notify(event)
        payload = notifier._post.call_args[0][0]
        assert payload["workflow_name"] == "wf-id"


# ── Filtering ─────────────────────────────────────────────────────────────


class TestFiltering:
    @pytest.mark.asyncio
    async def test_skips_when_no_url(self):
        notifier = WebhookNotifier(_make_config(url=""))
        notifier._post = AsyncMock()
        await notifier.notify(_make_event("run_completed"))
        notifier._post.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_filtered_event_types(self):
        cfg = _make_config(event_types=["run_failed"])
        notifier = WebhookNotifier(cfg)
        notifier._post = AsyncMock()
        await notifier.notify(_make_event("run_completed"))
        notifier._post.assert_not_called()


# ── HTTP POST ─────────────────────────────────────────────────────────────


def _mock_httpx_client(post_side_effect=None, post_return=None):
    """Build a mock httpx.AsyncClient context manager."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    if post_side_effect is not None:
        mock_client.post = AsyncMock(side_effect=post_side_effect)
    elif post_return is not None:
        mock_client.post = AsyncMock(return_value=post_return)
    else:
        resp = MagicMock()
        resp.status_code = 200
        mock_client.post = AsyncMock(return_value=resp)
    return mock_client


class TestPost:
    @pytest.mark.asyncio
    async def test_successful_post(self):
        notifier = WebhookNotifier(_make_config())
        mock_client = _mock_httpx_client()

        with patch("httpx.AsyncClient", return_value=mock_client):
            await notifier._post({"event_type": "run_completed"})
        mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_retries_on_server_error(self):
        notifier = WebhookNotifier(_make_config())
        error_resp = MagicMock(status_code=500)
        ok_resp = MagicMock(status_code=200)
        mock_client = _mock_httpx_client(post_side_effect=[error_resp, ok_resp])

        with patch("httpx.AsyncClient", return_value=mock_client):
            await notifier._post({"event_type": "run_failed"})
        assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_exception_retries_then_logs(self):
        notifier = WebhookNotifier(_make_config())
        mock_client = _mock_httpx_client(post_side_effect=ConnectionError("down"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            await notifier._post({"event_type": "run_completed"})
        assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_custom_headers_sent(self):
        cfg = _make_config(headers={"X-Token": "secret"})
        notifier = WebhookNotifier(cfg)
        mock_client = _mock_httpx_client()

        with patch("httpx.AsyncClient", return_value=mock_client):
            await notifier._post({"event_type": "run_completed"})
        call_kwargs = mock_client.post.call_args
        assert call_kwargs.kwargs.get("headers") == {"X-Token": "secret"}

    @pytest.mark.asyncio
    async def test_timeout_passed_to_client(self):
        cfg = _make_config(timeout=3.0)
        notifier = WebhookNotifier(cfg)
        mock_client = _mock_httpx_client()

        with patch("httpx.AsyncClient", return_value=mock_client) as mock_cls:
            await notifier._post({"event_type": "run_completed"})
        mock_cls.assert_called_with(timeout=3.0)
