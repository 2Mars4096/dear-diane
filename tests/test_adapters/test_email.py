"""Tests for the email messaging adapter."""

from __future__ import annotations

import asyncio
import email as email_mod
from email.mime.text import MIMEText
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dan.adapters.email_adapter import (
    EmailAdapter,
    EmailAdapterConfig,
    _extract_body,
    _text_to_html,
)


@pytest.fixture
def email_config() -> EmailAdapterConfig:
    return EmailAdapterConfig(
        workflow_path="/tmp/wf.md",
        imap_host="imap.example.com",
        imap_port=993,
        imap_user="user@example.com",
        imap_password="secret",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="user@example.com",
        smtp_password="secret",
        target_email="human@example.com",
        poll_interval=0.1,
    )


@pytest.fixture
def email_adapter(email_config: EmailAdapterConfig) -> EmailAdapter:
    return EmailAdapter(email_config)


class TestEmailAdapterConfig:
    def test_defaults(self) -> None:
        cfg = EmailAdapterConfig()
        assert cfg.imap_port == 993
        assert cfg.smtp_port == 587
        assert cfg.imap_use_ssl is True
        assert cfg.smtp_use_tls is True
        assert cfg.poll_interval == 10.0

    def test_custom_values(self) -> None:
        cfg = EmailAdapterConfig(
            imap_host="imap.test.com",
            smtp_host="smtp.test.com",
            from_name="Test Bot",
        )
        assert cfg.imap_host == "imap.test.com"
        assert cfg.from_name == "Test Bot"


class TestEmailAdapterLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self, email_adapter: EmailAdapter) -> None:
        await email_adapter.start()
        assert email_adapter._running is True
        await email_adapter.stop()
        assert email_adapter._running is False


class TestEmailAdapterSend:
    @pytest.mark.asyncio
    async def test_send_prompt_calls_smtp(self, email_adapter: EmailAdapter) -> None:
        mock_send = AsyncMock()
        with patch("dan.adapters.email_adapter.aiosmtplib", create=True) as mock_smtp:
            mock_smtp.send = mock_send
            with patch.dict("sys.modules", {"aiosmtplib": mock_smtp}):
                import importlib
                import dan.adapters.email_adapter as mod
                importlib.reload(mod)

                adapter = mod.EmailAdapter(email_adapter.config)
                await adapter.start()
                await adapter._send_email(
                    to="test@example.com",
                    subject="Test",
                    html_body="<p>Hello</p>",
                )
                mock_send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_result_formats_html(self, email_adapter: EmailAdapter) -> None:
        called = False

        async def mock_send_email(**kwargs: Any) -> str:
            nonlocal called
            called = True
            return "<fake-id>"

        email_adapter._send_email = mock_send_email  # type: ignore[assignment]
        await email_adapter.send_result("sess-1", {"summary": "All good", "score": 42})
        assert called


class TestEmailAdapterReceive:
    @pytest.mark.asyncio
    async def test_wait_for_response_with_mock_imap(
        self, email_adapter: EmailAdapter,
    ) -> None:
        email_adapter._running = True

        def mock_imap_fetch(in_reply_to: str) -> str | None:
            return "I approve"

        email_adapter._imap_fetch_reply = mock_imap_fetch  # type: ignore[assignment]
        email_adapter._message_ids["sess-1:prompt"] = "<prompt-id>"

        result = await email_adapter.wait_for_response("sess-1", timeout=5.0)
        assert result["response"] == "I approve"

    @pytest.mark.asyncio
    async def test_wait_for_response_timeout(
        self, email_adapter: EmailAdapter,
    ) -> None:
        email_adapter._running = True

        def mock_imap_fetch(in_reply_to: str) -> str | None:
            return None

        email_adapter._imap_fetch_reply = mock_imap_fetch  # type: ignore[assignment]
        email_adapter._message_ids["sess-t:prompt"] = "<prompt-id>"

        with pytest.raises(asyncio.TimeoutError):
            await email_adapter.wait_for_response("sess-t", timeout=0.3)


class TestEmailHelpers:
    def test_text_to_html(self) -> None:
        result = _text_to_html("Hello <world>")
        assert "&lt;world&gt;" in result
        assert "font-family" in result

    def test_extract_body_plain(self) -> None:
        msg = MIMEText("Plain body text", "plain")
        body = _extract_body(msg)
        assert body == "Plain body text"

    def test_extract_body_multipart(self) -> None:
        from email.mime.multipart import MIMEMultipart

        outer = MIMEMultipart("alternative")
        outer.attach(MIMEText("Plain text", "plain"))
        outer.attach(MIMEText("<p>HTML</p>", "html"))
        body = _extract_body(outer)
        assert "Plain text" in body


class TestThreadTracking:
    @pytest.mark.asyncio
    async def test_message_id_stored_on_send(self, email_adapter: EmailAdapter) -> None:
        sent_msg_id = None

        async def mock_send_email(to: str, subject: str, html_body: str, in_reply_to: str | None = None) -> str:
            nonlocal sent_msg_id
            sent_msg_id = "<test-msg-id>"
            return sent_msg_id

        email_adapter._send_email = mock_send_email  # type: ignore[assignment]
        await email_adapter.send_prompt("sess-1", "What is your name?")
        assert email_adapter._message_ids.get("sess-1:prompt") == "<test-msg-id>"
