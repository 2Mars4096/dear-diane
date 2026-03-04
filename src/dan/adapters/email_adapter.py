"""Email messaging adapter — IMAP receive + SMTP send.

Uses stdlib ``imaplib`` (via ``asyncio.to_thread``) for receiving and
``aiosmtplib`` for async sending.  Both libraries are available without
extra installs (``aiosmtplib`` is in the ``[messaging]`` optional dep group).
"""

from __future__ import annotations

import asyncio
import email
import email.utils
import html
import imaplib
import logging
import time
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from collections.abc import Callable
from typing import Any

from pydantic import Field

from dan.adapters.base import AdapterConfig

logger = logging.getLogger(__name__)


class EmailAdapterConfig(AdapterConfig):
    """Configuration for the email adapter."""

    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    imap_use_ssl: bool = True

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True

    poll_interval: float = 10.0
    target_email: str = ""
    subject_filter: str = ""
    from_name: str = "DAN Workflow"


MAX_REPLY_RETRIES = 3


class EmailAdapter:
    """Adapter that renders HumanNode I/O through email.

    **Receive path** — polls an IMAP inbox for new (unseen) messages that
    match the configured subject filter.  Incoming mail triggers a workflow
    run whose first input is the email body text.

    **Send path** — composes HTML emails and sends them via ``aiosmtplib``.

    Thread tracking uses the ``Message-ID`` / ``In-Reply-To`` headers so
    that prompt–reply pairs stay in the same email thread.
    """

    def __init__(self, config: EmailAdapterConfig) -> None:
        self.config = config
        self._running = False
        self._poll_task: asyncio.Task[None] | None = None
        self._message_ids: dict[str, str] = {}
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._on_new_message: Callable[[str, str], Any] | None = None
        self._pending_schemas: dict[str, dict[str, Any] | None] = {}
        self._pending_prompts: dict[str, str] = {}
        self._retry_counts: dict[str, int] = {}

    def set_message_callback(self, callback: Callable[[str, str], Any] | None) -> None:
        self._on_new_message = callback

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        self._running = True
        logger.info("Email adapter started (IMAP: %s, SMTP: %s)", self.config.imap_host, self.config.smtp_host)

    async def stop(self) -> None:
        self._running = False
        if self._poll_task is not None and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        logger.info("Email adapter stopped")

    # -- send ---------------------------------------------------------------

    async def send_prompt(
        self, session_id: str, prompt: str, schema: dict[str, Any] | None = None,
    ) -> None:
        self._pending_schemas[session_id] = schema
        self._pending_prompts[session_id] = prompt
        self._retry_counts.setdefault(session_id, 0)

        subject = f"Re: Workflow — action required"
        html_body = _text_to_html(prompt)
        in_reply_to = self._message_ids.get(session_id)
        msg_id = await self._send_email(
            to=self.config.target_email,
            subject=subject,
            html_body=html_body,
            in_reply_to=in_reply_to,
        )
        self._message_ids[f"{session_id}:prompt"] = msg_id

    async def send_result(self, session_id: str, result: dict[str, Any]) -> None:
        body_parts = ["<h3>Workflow Result</h3>", "<dl>"]
        for key, value in result.items():
            body_parts.append(f"<dt><b>{html.escape(str(key))}</b></dt>")
            body_parts.append(f"<dd>{html.escape(str(value))}</dd>")
        body_parts.append("</dl>")
        html_body = "\n".join(body_parts)
        in_reply_to = self._message_ids.get(session_id) or self._message_ids.get(f"{session_id}:prompt")
        await self._send_email(
            to=self.config.target_email,
            subject="Re: Workflow — result",
            html_body=html_body,
            in_reply_to=in_reply_to,
        )

    # -- receive ------------------------------------------------------------

    async def wait_for_response(
        self, session_id: str, timeout: float,
    ) -> dict[str, Any]:
        while True:
            loop = asyncio.get_running_loop()
            fut: asyncio.Future[dict[str, Any]] = loop.create_future()
            self._pending[session_id] = fut

            self._poll_task = asyncio.create_task(
                self._poll_replies(session_id, timeout),
            )

            try:
                result = await asyncio.wait_for(fut, timeout=timeout)
            finally:
                self._pending.pop(session_id, None)
                if self._poll_task is not None and not self._poll_task.done():
                    self._poll_task.cancel()
                    try:
                        await self._poll_task
                    except asyncio.CancelledError:
                        pass

            schema = self._pending_schemas.get(session_id)
            if schema and not self._validate_reply(result, schema):
                retries = self._retry_counts.get(session_id, 0)
                if retries < MAX_REPLY_RETRIES:
                    self._retry_counts[session_id] = retries + 1
                    original_prompt = self._pending_prompts.get(session_id, "")
                    retry_msg = (
                        "Your reply could not be parsed. "
                        f"Please try again ({MAX_REPLY_RETRIES - retries} attempts remaining).\n\n"
                        f"Original prompt:\n{original_prompt}"
                    )
                    await self.send_prompt(session_id, retry_msg, schema)
                    logger.info(
                        "Email reply parse failed for session %s, retry %d/%d",
                        session_id, retries + 1, MAX_REPLY_RETRIES,
                    )
                    continue
                else:
                    logger.warning(
                        "Email reply retries exhausted for session %s, returning raw",
                        session_id,
                    )

            self._retry_counts.pop(session_id, None)
            self._pending_schemas.pop(session_id, None)
            self._pending_prompts.pop(session_id, None)
            return result

    @staticmethod
    def _validate_reply(reply: dict[str, Any], schema: dict[str, Any]) -> bool:
        """Check if the reply has the required keys from the schema."""
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        if not properties:
            return True
        response_text = reply.get("response", "")
        if not response_text or not response_text.strip():
            return False
        if required:
            for key in required:
                if key not in reply or (key != "response" and not str(reply.get(key, "")).strip()):
                    return False
        return True

    async def _poll_replies(self, session_id: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        prompt_msg_id = self._message_ids.get(f"{session_id}:prompt", "")
        while self._running and time.monotonic() < deadline:
            try:
                reply = await asyncio.to_thread(
                    self._imap_fetch_reply, prompt_msg_id,
                )
                if reply is not None:
                    fut = self._pending.get(session_id)
                    if fut is not None and not fut.done():
                        fut.set_result({"response": reply})
                    return
            except Exception:
                logger.debug("IMAP poll error", exc_info=True)
            await asyncio.sleep(self.config.poll_interval)

    def _imap_fetch_reply(self, in_reply_to: str) -> str | None:
        """Synchronous IMAP fetch — called via ``asyncio.to_thread``."""
        try:
            if self.config.imap_use_ssl:
                conn = imaplib.IMAP4_SSL(self.config.imap_host, self.config.imap_port)
            else:
                conn = imaplib.IMAP4(self.config.imap_host, self.config.imap_port)
            conn.login(self.config.imap_user, self.config.imap_password)
            conn.select("INBOX")

            criteria = "(UNSEEN)"
            if in_reply_to:
                criteria = f'(UNSEEN HEADER In-Reply-To "{in_reply_to}")'

            _typ, data = conn.search(None, criteria)
            ids = data[0].split()
            if not ids:
                conn.logout()
                return None

            _typ, msg_data = conn.fetch(ids[-1], "(RFC822)")
            raw = msg_data[0][1] if msg_data and msg_data[0] else None
            conn.logout()

            if raw is None:
                return None

            msg = email.message_from_bytes(raw)
            body = _extract_body(msg)
            return body
        except Exception:
            logger.debug("IMAP fetch failed", exc_info=True)
            return None

    # -- internal send helper -----------------------------------------------

    async def _send_email(
        self,
        to: str,
        subject: str,
        html_body: str,
        in_reply_to: str | None = None,
    ) -> str:
        try:
            import aiosmtplib
        except ImportError as exc:
            raise RuntimeError(
                "aiosmtplib is required for the email adapter. "
                "Install with: pip install 'dan[messaging]'"
            ) from exc

        msg = MIMEMultipart("alternative")
        msg_id = f"<{uuid.uuid4()}@dan-adapter>"
        msg["Message-ID"] = msg_id
        msg["From"] = f"{self.config.from_name} <{self.config.smtp_user}>"
        msg["To"] = to
        msg["Subject"] = subject
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = in_reply_to
        msg.attach(MIMEText(html_body, "html"))

        await aiosmtplib.send(
            msg,
            hostname=self.config.smtp_host,
            port=self.config.smtp_port,
            username=self.config.smtp_user,
            password=self.config.smtp_password,
            use_tls=self.config.smtp_use_tls,
        )
        return msg_id


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _text_to_html(text: str) -> str:
    escaped = html.escape(text)
    return f"<div style='font-family:sans-serif;white-space:pre-wrap'>{escaped}</div>"


def _extract_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="replace")
    payload = msg.get_payload(decode=True)
    if payload:
        return payload.decode("utf-8", errors="replace")
    return ""
