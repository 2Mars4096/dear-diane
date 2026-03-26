"""WeChat Official Account messaging adapter.

This adapter targets the public WeChat Official Account channel used by
OpenClaw-style integrations. It provides the core protocol primitives needed by
the server callback surface: signature verification, plaintext XML parsing,
passive text replies, and customer-service API sends for async follow-up.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import inspect
import logging
import os
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree as ET

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from pydantic import Field

from dan.adapters.base import AdapterConfig

logger = logging.getLogger(__name__)
_WECHAT_API_BASE = "https://api.weixin.qq.com"
_WECHAT_CUSTOMER_SERVICE_TEXT_LIMIT = 1500
_WECHAT_CALLBACK_AES_BLOCK_SIZE = 32
_WECHAT_CALLBACK_RANDOM_PREFIX_SIZE = 16
_WECHAT_SUPPORTED_ENCRYPT_TYPES = {"aes"}


def _compute_signature(*parts: str) -> str:
    normalized = [str(part or "").strip() for part in parts]
    return hashlib.sha1("".join(sorted(normalized)).encode("utf-8")).hexdigest()


def verify_signature(token: str, signature: str, timestamp: str, nonce: str) -> bool:
    """Verify a standard WeChat callback signature."""
    token = str(token or "").strip()
    signature = str(signature or "").strip()
    timestamp = str(timestamp or "").strip()
    nonce = str(nonce or "").strip()
    if not token or not signature or not timestamp or not nonce:
        return False

    digest = _compute_signature(token, timestamp, nonce)
    return hmac.compare_digest(digest, signature)


def verify_message_signature(
    token: str,
    msg_signature: str,
    timestamp: str,
    nonce: str,
    encrypted_payload: str,
) -> bool:
    """Verify a WeChat encrypted callback signature."""
    token = str(token or "").strip()
    msg_signature = str(msg_signature or "").strip()
    timestamp = str(timestamp or "").strip()
    nonce = str(nonce or "").strip()
    encrypted_payload = str(encrypted_payload or "").strip()
    if not token or not msg_signature or not timestamp or not nonce or not encrypted_payload:
        return False

    digest = _compute_signature(token, timestamp, nonce, encrypted_payload)
    return hmac.compare_digest(digest, msg_signature)


def validate_callback_encrypt_type(
    encrypt_type: str,
    *,
    encrypted_callbacks_enabled: bool = False,
) -> None:
    """Validate the requested callback encryption mode."""
    normalized = str(encrypt_type or "").strip().lower()
    if not normalized or normalized in {"raw", "plain", "plaintext"}:
        return
    if normalized in _WECHAT_SUPPORTED_ENCRYPT_TYPES and encrypted_callbacks_enabled:
        return
    if normalized in _WECHAT_SUPPORTED_ENCRYPT_TYPES:
        raise ValueError("Encrypted WeChat callbacks are not enabled")
    raise ValueError(f"Unsupported WeChat encrypt_type: {normalized}")


def _xml_text(node: ET.Element, tag: str) -> str:
    value = node.findtext(tag)
    return str(value or "").strip()


def _cdata(text: str) -> str:
    return "<![CDATA[" + str(text or "").replace("]]>", "]]]]><![CDATA[>") + "]]>"


def _decode_encoding_aes_key(encoding_aes_key: str) -> bytes:
    encoded = str(encoding_aes_key or "").strip()
    if not encoded:
        raise ValueError("WeChat encoding_aes_key is required for encrypted callbacks")
    try:
        decoded = base64.b64decode(encoded + "=", validate=True)
    except Exception as exc:
        raise ValueError("Invalid WeChat encoding_aes_key") from exc
    if len(decoded) != 32:
        raise ValueError("Invalid WeChat encoding_aes_key")
    return decoded


def _require_callback_app_id(app_id: str) -> str:
    value = str(app_id or "").strip()
    if not value:
        raise ValueError("WeChat app_id is required for encrypted callbacks")
    return value


def _pkcs7_pad(payload: bytes, *, block_size: int = _WECHAT_CALLBACK_AES_BLOCK_SIZE) -> bytes:
    pad_len = block_size - (len(payload) % block_size)
    if pad_len <= 0:
        pad_len = block_size
    return payload + bytes([pad_len]) * pad_len


def _pkcs7_unpad(payload: bytes, *, block_size: int = _WECHAT_CALLBACK_AES_BLOCK_SIZE) -> bytes:
    if not payload:
        raise ValueError("Invalid encrypted WeChat payload")
    pad_len = payload[-1]
    if pad_len < 1 or pad_len > block_size:
        raise ValueError("Invalid encrypted WeChat payload")
    if payload[-pad_len:] != bytes([pad_len]) * pad_len:
        raise ValueError("Invalid encrypted WeChat payload")
    return payload[:-pad_len]


def encrypt_callback_payload(
    plaintext: str | bytes,
    *,
    encoding_aes_key: str,
    app_id: str,
    random_prefix: bytes | None = None,
) -> str:
    """Encrypt a callback payload using the Official Account AES envelope."""
    aes_key = _decode_encoding_aes_key(encoding_aes_key)
    receiver_id = _require_callback_app_id(app_id).encode("utf-8")
    message = plaintext if isinstance(plaintext, bytes) else str(plaintext).encode("utf-8")
    prefix = random_prefix or os.urandom(_WECHAT_CALLBACK_RANDOM_PREFIX_SIZE)
    if len(prefix) != _WECHAT_CALLBACK_RANDOM_PREFIX_SIZE:
        raise ValueError("WeChat encrypted callback random prefix must be 16 bytes")

    framed = prefix + struct.pack(">I", len(message)) + message + receiver_id
    padded = _pkcs7_pad(framed)
    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(aes_key[:16]))
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(encrypted).decode("utf-8")


def _decrypt_callback_payload_bytes(
    encrypted_payload: str,
    *,
    encoding_aes_key: str,
    app_id: str,
) -> bytes:
    aes_key = _decode_encoding_aes_key(encoding_aes_key)
    expected_app_id = _require_callback_app_id(app_id)
    encrypted_text = str(encrypted_payload or "").strip()
    if not encrypted_text:
        raise ValueError("Encrypted WeChat payload is empty")
    try:
        ciphertext = base64.b64decode(encrypted_text.encode("utf-8"), validate=True)
    except Exception as exc:
        raise ValueError("Invalid encrypted WeChat payload") from exc
    if not ciphertext or len(ciphertext) % 16 != 0:
        raise ValueError("Invalid encrypted WeChat payload")

    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(aes_key[:16]))
    decryptor = cipher.decryptor()
    unpadded = _pkcs7_unpad(decryptor.update(ciphertext) + decryptor.finalize())
    if len(unpadded) < _WECHAT_CALLBACK_RANDOM_PREFIX_SIZE + 4:
        raise ValueError("Invalid encrypted WeChat payload")

    message_length = struct.unpack(
        ">I",
        unpadded[_WECHAT_CALLBACK_RANDOM_PREFIX_SIZE : _WECHAT_CALLBACK_RANDOM_PREFIX_SIZE + 4],
    )[0]
    message_start = _WECHAT_CALLBACK_RANDOM_PREFIX_SIZE + 4
    message_end = message_start + message_length
    if len(unpadded) < message_end:
        raise ValueError("Invalid encrypted WeChat payload")
    message = unpadded[message_start:message_end]
    try:
        receiver_id = unpadded[message_end:].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Invalid encrypted WeChat payload") from exc
    if receiver_id != expected_app_id:
        raise ValueError("Encrypted WeChat payload app_id mismatch")
    return message


def _extract_encrypted_payload(xml_text: str | bytes) -> str:
    raw = xml_text.decode("utf-8") if isinstance(xml_text, bytes) else str(xml_text)
    raw = raw.strip()
    if not raw:
        raise ValueError("WeChat payload is empty")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError("Invalid WeChat XML payload") from exc
    encrypted = _xml_text(root, "Encrypt")
    if not encrypted:
        raise ValueError("Encrypted WeChat payload is missing Encrypt field")
    return encrypted


def decrypt_encrypted_callback_echostr(
    echostr: str,
    *,
    token: str,
    msg_signature: str,
    timestamp: str,
    nonce: str,
    encoding_aes_key: str,
    app_id: str,
) -> str:
    """Validate and decrypt an encrypted callback verification string."""
    encrypted = str(echostr or "").strip()
    if not verify_message_signature(token, msg_signature, timestamp, nonce, encrypted):
        raise PermissionError("Invalid WeChat message signature")
    try:
        return _decrypt_callback_payload_bytes(
            encrypted,
            encoding_aes_key=encoding_aes_key,
            app_id=app_id,
        ).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Invalid encrypted WeChat payload") from exc


def decrypt_encrypted_callback_xml(
    xml_text: str | bytes,
    *,
    token: str,
    msg_signature: str,
    timestamp: str,
    nonce: str,
    encoding_aes_key: str,
    app_id: str,
) -> str:
    """Validate and decrypt an encrypted callback body."""
    encrypted = _extract_encrypted_payload(xml_text)
    if not verify_message_signature(token, msg_signature, timestamp, nonce, encrypted):
        raise PermissionError("Invalid WeChat message signature")
    try:
        return _decrypt_callback_payload_bytes(
            encrypted,
            encoding_aes_key=encoding_aes_key,
            app_id=app_id,
        ).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Invalid encrypted WeChat payload") from exc


def parse_incoming_xml(xml_text: str | bytes) -> dict[str, Any]:
    """Parse a WeChat Official Account callback payload."""
    raw = xml_text.decode("utf-8") if isinstance(xml_text, bytes) else str(xml_text)
    raw = raw.strip()
    if not raw:
        raise ValueError("WeChat payload is empty")

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError("Invalid WeChat XML payload") from exc

    msg_type = _xml_text(root, "MsgType").lower()
    event = _xml_text(root, "Event").lower()
    parsed: dict[str, Any] = {
        "raw_xml": raw,
        "to_user_name": _xml_text(root, "ToUserName"),
        "from_user_name": _xml_text(root, "FromUserName"),
        "create_time": _xml_text(root, "CreateTime"),
        "msg_type": msg_type,
        "event": event,
        "content": _xml_text(root, "Content"),
        "event_key": _xml_text(root, "EventKey"),
        "ticket": _xml_text(root, "Ticket"),
        "msg_id": _xml_text(root, "MsgId"),
    }

    if msg_type == "event":
        if event in {"subscribe", "unsubscribe"}:
            normalized_text = event
        else:
            normalized_text = parsed["event_key"] or event or ""
        parsed["kind"] = event or "event"
        parsed["text"] = normalized_text
    else:
        parsed["kind"] = msg_type or "message"
        parsed["text"] = parsed["content"] or ""

    return parsed


def build_passive_text_reply(
    to_user_name: str,
    from_user_name: str,
    content: str,
    *,
    create_time: int | None = None,
) -> str:
    """Build a passive text reply XML payload."""
    created = int(time.time()) if create_time is None else int(create_time)
    return (
        "<xml>"
        f"<ToUserName>{_cdata(to_user_name)}</ToUserName>"
        f"<FromUserName>{_cdata(from_user_name)}</FromUserName>"
        f"<CreateTime>{created}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content>{_cdata(content)}</Content>"
        "</xml>"
    )


def build_encrypted_callback_reply(
    reply_xml: str,
    *,
    token: str,
    encoding_aes_key: str,
    app_id: str,
    nonce: str = "",
    timestamp: str = "",
) -> str:
    """Encrypt a passive reply body for `encrypt_type=aes` callbacks."""
    timestamp_text = str(timestamp or int(time.time()))
    nonce_text = str(nonce or os.urandom(8).hex())
    encrypted = encrypt_callback_payload(
        reply_xml,
        encoding_aes_key=encoding_aes_key,
        app_id=app_id,
    )
    msg_signature = _compute_signature(token, timestamp_text, nonce_text, encrypted)
    return (
        "<xml>"
        f"<Encrypt>{_cdata(encrypted)}</Encrypt>"
        f"<MsgSignature>{_cdata(msg_signature)}</MsgSignature>"
        f"<TimeStamp>{timestamp_text}</TimeStamp>"
        f"<Nonce>{_cdata(nonce_text)}</Nonce>"
        "</xml>"
    )


class WeChatOfficialAccountAdapterConfig(AdapterConfig):
    """Configuration for the WeChat Official Account adapter."""

    app_id: str = ""
    app_secret: str = ""
    token: str = ""
    encoding_aes_key: str = ""
    webhook_url: str = ""
    callback_path: str = "callback"
    account_name: str = ""
    app_name: str = ""
    support_encrypted_callbacks: bool = False
    passive_reply_budget_seconds: float = 4.0
    passive_reply_fallback_text: str = "Working on it..."
    api_base_url: str = _WECHAT_API_BASE
    access_token_refresh_margin_seconds: float = 300.0


@dataclass(slots=True)
class WeChatIncomingMessage:
    """Normalized inbound WeChat payload used by the adapter."""

    openid: str
    message_type: str
    text: str
    event: str = ""
    event_key: str = ""
    raw: dict[str, Any] | None = None


class WeChatOfficialAccountAdapter:
    """Chat-mode WeChat Official Account adapter.

    The first slice keeps the adapter local and protocol-compatible:
    lifecycle, snapshot reporting, session mapping, callback dispatch, and the
    WeChat XML/signature primitives used by webhook handlers.
    """

    def __init__(self, config: WeChatOfficialAccountAdapterConfig) -> None:
        self.config = config
        self._running = False
        self._connection_state = "disconnected"
        self._last_error: str | None = None
        self._on_new_message: Callable[[str, str], Any] | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._session_map: dict[str, str] = {}
        self._openid_map: dict[str, str] = {}
        self._last_prompt_by_session: dict[str, str] = {}
        self._last_result_by_session: dict[str, dict[str, Any]] = {}
        self._webhook_app: Any = None
        self._access_token: str | None = None
        self._access_token_expires_at: float = 0.0

    def set_message_callback(self, callback: Callable[[str, str], Any] | None) -> None:
        self._on_new_message = callback

    def register_session(self, session_id: str, openid: str) -> None:
        self._session_map[openid] = session_id
        self._openid_map[session_id] = openid

    def unregister_session(self, session_id: str) -> None:
        openid = self._openid_map.pop(session_id, None)
        if openid is not None:
            self._session_map.pop(openid, None)
        self._pending.pop(session_id, None)

    def _openid_from_session(self, session_id: str) -> str | None:
        return self._openid_map.get(session_id)

    def _session_from_openid(self, openid: str) -> str | None:
        return self._session_map.get(openid)

    def _dispatch_callback(self, openid: str, text: str) -> None:
        callback = self._on_new_message
        if callback is None:
            return

        try:
            result = callback(openid, text)
        except Exception:
            logger.exception("WeChat callback failed")
            return

        if inspect.isawaitable(result):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                loop.create_task(result)

    async def get_connection_snapshot(self) -> dict[str, Any]:
        if not self._running:
            return {"connection_state": "disconnected"}
        snapshot: dict[str, Any] = {
            "connection_state": self._connection_state,
            "last_error": self._last_error,
            "configured": bool(self.config.token or self.config.app_id or self.config.app_secret),
            "session_count": len(self._openid_map),
        }
        if self._access_token and self._access_token_expires_at > time.time():
            snapshot["access_token_ready"] = True
            snapshot["access_token_expires_at"] = self._access_token_expires_at
        if self.config.account_name:
            snapshot["account_name"] = self.config.account_name
        if self.config.app_name:
            snapshot["app_name"] = self.config.app_name
        return snapshot

    async def start(self) -> None:
        self._running = True
        self._connection_state = "connected"
        self._last_error = None
        try:
            self._webhook_app = self._build_webhook_app()
        except Exception:
            logger.debug("WeChat webhook app could not be initialized", exc_info=True)
            self._webhook_app = None

    async def stop(self) -> None:
        self._running = False
        self._connection_state = "disconnected"
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.cancel()
        self._pending.clear()

    async def send_prompt(
        self,
        session_id: str,
        prompt: str,
        schema: dict[str, Any] | None = None,
    ) -> None:
        openid = self._openid_from_session(session_id)
        if openid is None:
            logger.debug("WeChat send_prompt skipped for unknown session %s", session_id)
            return
        self._last_prompt_by_session[session_id] = prompt
        await self._send_customer_service_text(openid, prompt)

    async def send_result(self, session_id: str, result: dict[str, Any]) -> None:
        openid = self._openid_from_session(session_id)
        if openid is None:
            logger.debug("WeChat send_result skipped for unknown session %s", session_id)
            return
        self._last_result_by_session[session_id] = dict(result)
        lines = ["Workflow Result"]
        for key, value in result.items():
            lines.append(f"{key}: {value}")
        await self._send_customer_service_text(openid, "\n".join(lines))

    async def wait_for_response(self, session_id: str, timeout: float) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[session_id] = fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(session_id, None)

    def handle_incoming_xml(self, xml_text: str | bytes) -> dict[str, Any]:
        """Parse and dispatch a WeChat callback payload."""
        parsed = parse_incoming_xml(xml_text)
        message = self._normalize_incoming_message(parsed)
        session_id = self._session_from_openid(message.openid)
        if session_id is None and message.openid:
            # Reuse the external OpenID as the session key until the full
            # async customer-service reply path is implemented.
            self.register_session(message.openid, message.openid)
            session_id = message.openid

        if session_id is not None:
            pending = self._pending.get(session_id)
            if pending is not None and not pending.done() and message.message_type == "text":
                pending.set_result(
                    {
                        "response": message.text,
                        "message_type": message.message_type,
                        "from_user_name": message.openid,
                        "event": message.event or None,
                        "event_key": message.event_key or None,
                    },
                )
                return parsed

        self._dispatch_callback(message.openid, message.text)
        return parsed

    def _normalize_incoming_message(self, parsed: dict[str, Any]) -> WeChatIncomingMessage:
        message_type = str(parsed.get("msg_type") or "").strip().lower()
        event = str(parsed.get("event") or "").strip().lower()
        openid = str(parsed.get("from_user_name") or "").strip()
        if message_type == "event":
            text = str(parsed.get("text") or event or "").strip()
            event_key = str(parsed.get("event_key") or "").strip()
            return WeChatIncomingMessage(
                openid=openid,
                message_type=event or "event",
                text=text or event_key or event,
                event=event,
                event_key=event_key,
                raw=parsed,
            )
        text = str(parsed.get("content") or parsed.get("text") or "").strip()
        return WeChatIncomingMessage(
            openid=openid,
            message_type=message_type or "text",
            text=text,
            raw=parsed,
        )

    async def _get_access_token(self, *, force_refresh: bool = False) -> str:
        now = time.time()
        refresh_margin = max(
            float(self.config.access_token_refresh_margin_seconds or 300.0),
            0.0,
        )
        if (
            not force_refresh
            and self._access_token
            and now + refresh_margin < self._access_token_expires_at
        ):
            return self._access_token

        if not self.config.app_id or not self.config.app_secret:
            raise RuntimeError("WeChat app_id and app_secret are required for customer-service sends")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.config.api_base_url.rstrip('/')}/cgi-bin/token",
                params={
                    "grant_type": "client_credential",
                    "appid": self.config.app_id,
                    "secret": self.config.app_secret,
                },
            )
            response.raise_for_status()
            payload = response.json()

        access_token = str(payload.get("access_token") or "").strip()
        if not access_token:
            raise RuntimeError(
                f"WeChat access token unavailable: {payload.get('errmsg') or 'missing access_token'}",
            )

        expires_in = int(payload.get("expires_in") or 7200)
        self._access_token = access_token
        self._access_token_expires_at = now + max(expires_in, 60)
        return access_token

    @staticmethod
    def _split_customer_service_text(text: str) -> list[str]:
        content = str(text or "").strip()
        if not content:
            return []
        if len(content) <= _WECHAT_CUSTOMER_SERVICE_TEXT_LIMIT:
            return [content]

        parts: list[str] = []
        remaining = content
        while remaining:
            chunk = remaining[:_WECHAT_CUSTOMER_SERVICE_TEXT_LIMIT]
            split_at = chunk.rfind("\n")
            if split_at < 0:
                split_at = chunk.rfind(" ")
            if split_at <= 0:
                split_at = len(chunk)
            part = remaining[:split_at].strip()
            if not part:
                part = remaining[:_WECHAT_CUSTOMER_SERVICE_TEXT_LIMIT]
                split_at = len(part)
            parts.append(part)
            remaining = remaining[split_at:].strip()
        return parts

    async def _post_wechat_api(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        access_token = await self._get_access_token(force_refresh=force_refresh)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.config.api_base_url.rstrip('/')}{path}",
                params={"access_token": access_token},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        errcode = int(data.get("errcode") or 0)
        if errcode in {40001, 40014, 42001} and not force_refresh:
            self._access_token = None
            self._access_token_expires_at = 0.0
            return await self._post_wechat_api(
                path,
                payload,
                force_refresh=True,
            )
        if errcode not in {0}:
            raise RuntimeError(
                f"WeChat API error {errcode}: {data.get('errmsg') or 'unknown error'}",
            )
        return data

    async def _send_customer_service_text(self, openid: str, text: str) -> None:
        for chunk in self._split_customer_service_text(text):
            await self._post_wechat_api(
                "/cgi-bin/message/custom/send",
                {
                    "touser": openid,
                    "msgtype": "text",
                    "text": {"content": chunk},
                },
            )

    def _build_webhook_app(self) -> Any:
        try:
            from fastapi import FastAPI, HTTPException, Request
            from fastapi.responses import PlainTextResponse
        except Exception:  # pragma: no cover - fastapi is expected in tests
            return None

        app = FastAPI()

        @app.get("/wechat")
        async def verify(
            signature: str = "",
            timestamp: str = "",
            nonce: str = "",
            echostr: str = "",
            encrypt_type: str = "",
            msg_signature: str = "",
        ) -> PlainTextResponse:
            try:
                validate_callback_encrypt_type(
                    encrypt_type,
                    encrypted_callbacks_enabled=bool(self.config.support_encrypted_callbacks),
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if not verify_signature(self.config.token, signature, timestamp, nonce):
                raise HTTPException(status_code=403, detail="invalid signature")
            normalized = str(encrypt_type or "").strip().lower()
            if normalized == "aes":
                try:
                    echostr = decrypt_encrypted_callback_echostr(
                        echostr,
                        token=self.config.token,
                        msg_signature=msg_signature,
                        timestamp=timestamp,
                        nonce=nonce,
                        encoding_aes_key=self.config.encoding_aes_key,
                        app_id=self.config.app_id,
                    )
                except PermissionError as exc:
                    raise HTTPException(status_code=403, detail=str(exc)) from exc
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
            return PlainTextResponse(echostr)

        @app.post("/wechat")
        async def receive(
            request: Request,
            signature: str = "",
            timestamp: str = "",
            nonce: str = "",
            encrypt_type: str = "",
            msg_signature: str = "",
        ) -> PlainTextResponse:
            try:
                validate_callback_encrypt_type(
                    encrypt_type,
                    encrypted_callbacks_enabled=bool(self.config.support_encrypted_callbacks),
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if not verify_signature(self.config.token, signature, timestamp, nonce):
                raise HTTPException(status_code=403, detail="invalid signature")
            body = await request.body()
            if body:
                normalized = str(encrypt_type or "").strip().lower()
                if normalized == "aes":
                    try:
                        decrypted = decrypt_encrypted_callback_xml(
                            body,
                            token=self.config.token,
                            msg_signature=msg_signature,
                            timestamp=timestamp,
                            nonce=nonce,
                            encoding_aes_key=self.config.encoding_aes_key,
                            app_id=self.config.app_id,
                        )
                    except PermissionError as exc:
                        raise HTTPException(status_code=403, detail=str(exc)) from exc
                    except ValueError as exc:
                        raise HTTPException(status_code=400, detail=str(exc)) from exc
                    self.handle_incoming_xml(decrypted)
                else:
                    self.handle_incoming_xml(body)
            return PlainTextResponse("")

        return app
