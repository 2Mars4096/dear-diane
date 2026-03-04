"""WhatsApp Business API messaging adapter.

Uses ``httpx`` (a core DAN dependency) to call the WhatsApp Cloud API.
Webhook reception is provided via a lightweight FastAPI sub-application
that can be mounted in an existing server or run standalone.

.. important::

   The WhatsApp Business API requires:

   1. A **Meta Business** account verified through the Meta Business Suite.
   2. A registered **WhatsApp Business phone number** with an approved
      display name.
   3. A permanent **System User access token** with the
      ``whatsapp_business_messaging`` permission.
   4. For proactive (business-initiated) messages you need **pre-approved
      message templates** registered in the WhatsApp Manager.  Only
      template messages can initiate a conversation outside the 24-hour
      customer-service window.

   See https://developers.facebook.com/docs/whatsapp/cloud-api/get-started
   for the full setup guide.  This is *not* a "pip install and go"
   situation — plan for a 1–2 day onboarding process.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
from collections.abc import Callable
from typing import Any

from pydantic import Field

from dan.adapters.base import AdapterConfig

logger = logging.getLogger(__name__)

_WHATSAPP_API_BASE = "https://graph.facebook.com/v21.0"
_MAX_MESSAGE_LENGTH = 4096


class WhatsAppAdapterConfig(AdapterConfig):
    """Configuration for the WhatsApp Cloud API adapter."""

    access_token: str = ""
    phone_number_id: str = ""
    webhook_url: str = ""
    verify_token: str = "dan-verify"
    app_secret: str = ""


class WhatsAppAdapter:
    """Renders HumanNode I/O through WhatsApp Business Cloud API.

    Features
    --------
    * Interactive list messages for ``selection`` render mode.
    * Quick-reply buttons for ``approval`` render mode.
    * Webhook receiver (FastAPI sub-app) for incoming messages.
    * Session per sender phone number.
    * Automatic message splitting at word boundaries.
    """

    def __init__(self, config: WhatsAppAdapterConfig) -> None:
        self.config = config
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._on_new_message: Callable[[str, str], Any] | None = None
        self._running = False
        self._webhook_app: Any = None

        self._session_map: dict[str, str] = {}
        self._phone_map: dict[str, str] = {}

    def set_message_callback(self, callback: Callable[[str, str], Any] | None) -> None:
        self._on_new_message = callback

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        self._running = True
        self._webhook_app = self._build_webhook_app()
        logger.info("WhatsApp adapter started (phone_number_id: %s)", self.config.phone_number_id)

    async def stop(self) -> None:
        self._running = False
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        logger.info("WhatsApp adapter stopped")

    # -- send ---------------------------------------------------------------

    async def send_prompt(
        self, session_id: str, prompt: str, schema: dict[str, Any] | None = None,
    ) -> None:
        phone = self._phone_from_session(session_id)
        if phone is None:
            logger.warning("send_prompt: unknown session %s", session_id)
            return
        await self._send_text_message(phone, prompt)

    async def send_interactive_buttons(
        self,
        session_id: str,
        body_text: str,
        buttons: list[dict[str, str]],
    ) -> None:
        """Send a quick-reply button message (approval mode)."""
        phone = self._phone_from_session(session_id)
        if phone is None:
            return

        btn_list = []
        for btn in buttons[:3]:
            btn_list.append({
                "type": "reply",
                "reply": {"id": btn.get("id", "btn"), "title": btn.get("title", "")[:20]},
            })

        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body_text[:1024]},
                "action": {"buttons": btn_list},
            },
        }
        await self._api_post("messages", payload)

    async def send_interactive_list(
        self,
        session_id: str,
        body_text: str,
        button_text: str,
        sections: list[dict[str, Any]],
    ) -> None:
        """Send a list (selection) message."""
        phone = self._phone_from_session(session_id)
        if phone is None:
            return

        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": body_text[:1024]},
                "action": {
                    "button": button_text[:20],
                    "sections": sections,
                },
            },
        }
        await self._api_post("messages", payload)

    async def send_result(self, session_id: str, result: dict[str, Any]) -> None:
        phone = self._phone_from_session(session_id)
        if phone is None:
            return
        lines = ["*Workflow Result*\n"]
        for key, value in result.items():
            lines.append(f"*{key}:* {value}")
        await self._send_text_message(phone, "\n".join(lines))

    # -- receive ------------------------------------------------------------

    async def wait_for_response(
        self, session_id: str, timeout: float,
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[session_id] = fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(session_id, None)

    def handle_incoming(self, phone: str, text: str) -> None:
        """Called by the webhook handler when a user message arrives."""
        sid = self._session_map.get(phone)
        if sid and sid in self._pending:
            fut = self._pending.get(sid)
            if fut and not fut.done():
                fut.set_result({"response": text})
                return

        if self._on_new_message is not None:
            asyncio.get_event_loop().create_task(
                self._on_new_message(phone, text),
            )

    def handle_button_reply(self, phone: str, button_id: str, title: str) -> None:
        """Called when a user taps a quick-reply button or list row."""
        sid = self._session_map.get(phone)
        if sid and sid in self._pending:
            fut = self._pending.get(sid)
            if fut and not fut.done():
                approved = button_id in ("approve_yes", "yes")
                fut.set_result({
                    "button_id": button_id,
                    "title": title,
                    "approved": approved,
                    "response": title,
                })

    # -- session mapping ----------------------------------------------------

    def register_session(self, session_id: str, phone: str) -> None:
        self._session_map[phone] = session_id
        self._phone_map[session_id] = phone

    def unregister_session(self, session_id: str) -> None:
        phone = self._phone_map.pop(session_id, None)
        if phone is not None:
            self._session_map.pop(phone, None)

    def _phone_from_session(self, session_id: str) -> str | None:
        return self._phone_map.get(session_id)

    # -- API helpers --------------------------------------------------------

    async def _api_post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        import httpx

        url = f"{_WHATSAPP_API_BASE}/{self.config.phone_number_id}/{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.config.access_token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()

    async def _send_text_message(self, phone: str, text: str) -> None:
        for chunk in _split_message(text):
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": phone,
                "type": "text",
                "text": {"preview_url": False, "body": chunk},
            }
            await self._api_post("messages", payload)

    # -- webhook FastAPI app ------------------------------------------------

    def _build_webhook_app(self) -> Any:
        """Build a FastAPI sub-application for the WhatsApp webhook."""
        try:
            from fastapi import FastAPI, Request, Response
        except ImportError:
            logger.warning("FastAPI not available — webhook app not created")
            return None

        app = FastAPI(title="DAN WhatsApp Webhook")

        @app.get("/webhook")
        async def verify(request: Request) -> Response:
            mode = request.query_params.get("hub.mode")
            token = request.query_params.get("hub.verify_token")
            challenge = request.query_params.get("hub.challenge", "")
            if mode == "subscribe" and token == self.config.verify_token:
                return Response(content=challenge, media_type="text/plain")
            return Response(status_code=403)

        @app.post("/webhook")
        async def receive(request: Request) -> dict[str, str]:
            body = await request.json()
            if self.config.app_secret:
                raw_body = await request.body()
                sig = request.headers.get("x-hub-signature-256", "")
                if not _verify_signature(raw_body, self.config.app_secret, sig):
                    return {"status": "invalid_signature"}
            _process_webhook_payload(self, body)
            return {"status": "ok"}

        return app

    @property
    def webhook_app(self) -> Any:
        """The FastAPI sub-app to mount for webhook reception."""
        if self._webhook_app is None:
            self._webhook_app = self._build_webhook_app()
        return self._webhook_app


def _process_webhook_payload(adapter: WhatsAppAdapter, body: dict[str, Any]) -> None:
    """Extract messages from a Cloud API webhook payload."""
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                phone = msg.get("from", "")
                msg_type = msg.get("type", "")

                if msg_type == "text":
                    text = msg.get("text", {}).get("body", "")
                    adapter.handle_incoming(phone, text)
                elif msg_type == "interactive":
                    interactive = msg.get("interactive", {})
                    itype = interactive.get("type", "")
                    if itype == "button_reply":
                        reply = interactive.get("button_reply", {})
                        adapter.handle_button_reply(
                            phone, reply.get("id", ""), reply.get("title", ""),
                        )
                    elif itype == "list_reply":
                        reply = interactive.get("list_reply", {})
                        adapter.handle_button_reply(
                            phone, reply.get("id", ""), reply.get("title", ""),
                        )


def _verify_signature(payload: bytes, secret: str, signature_header: str) -> bool:
    """Verify the ``x-hub-signature-256`` header."""
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        secret.encode(), payload, hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature_header)


def _split_message(text: str, max_len: int = _MAX_MESSAGE_LENGTH) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = text.rfind(" ", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks
