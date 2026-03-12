"""WhatsApp Web adapter — personal WhatsApp via QR code pairing.

Uses ``neonize`` (a Python WhatsApp Web library backed by whatsmeow/Go)
to connect to WhatsApp as a linked device.  No Business API, no Meta
verification, no webhook — just scan the QR code and start chatting.

Requires the ``neonize`` optional dependency::

    pip install neonize

Setup:

1. Run ``dan-adapter whatsapp-web``
2. Scan the QR code in your terminal with WhatsApp > Linked Devices > Link a Device
3. Send a message to yourself (or your linked number) — DAN responds.

Session data is stored in ``~/.dan/whatsapp-web/`` so you only pair once.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import Field

from dan.adapters.base import AdapterConfig

logger = logging.getLogger(__name__)

_MAX_MESSAGE_LENGTH = 4096
_PROGRESS_THROTTLE_SECONDS = 5.0
_DEFAULT_DB_DIR = Path.home() / ".dan" / "whatsapp-web"
_DEFAULT_MEDIA_DIR = Path.home() / ".dan" / "whatsapp-web" / "media"
_MEDIA_CLEANUP_SECONDS = 3600.0
_OUTBOUND_ECHO_WINDOW_SECONDS = 30.0


class WhatsAppWebAdapterConfig(AdapterConfig):
    """Configuration for the WhatsApp Web adapter."""

    db_path: str = ""
    allowed_jids: list[str] = Field(default_factory=list)
    progress_throttle: float = _PROGRESS_THROTTLE_SECONDS
    max_inbound_media_mb: float = 50.0


class WhatsAppWebAdapter:
    """Renders HumanNode I/O through personal WhatsApp via QR pairing.

    Features
    --------
    * QR code pairing — scan once, stays linked.
    * Incoming messages trigger workflow runs or resolve HumanNode prompts.
    * Messages longer than 4096 characters are automatically split.
    * Approval / selection / form render modes via plain text.
    * Progress updates throttled to avoid spam.
    * Session DB persisted in ``~/.dan/whatsapp-web/`` for reconnection.
    """

    def __init__(self, config: WhatsAppWebAdapterConfig) -> None:
        self.config = config
        self._client: Any = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._last_progress: dict[str, float] = {}
        self._on_new_message: Callable[[str, str], Any] | None = None
        self._running = False
        self._session_map: dict[str, str] = {}  # jid -> session_id
        self._jid_map: dict[str, str] = {}  # session_id -> jid
        self._recent_outbound: dict[str, list[tuple[float, str]]] = {}
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._connect_task: asyncio.Task | None = None

    def set_message_callback(self, callback: Callable[[str, str], Any] | None) -> None:
        self._on_new_message = callback

    async def _sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        try:
            from neonize.client import NewClient
            from neonize.events import MessageEv, ConnectedEv, PairStatusEv
        except ImportError as exc:
            raise RuntimeError(
                "neonize is required for the WhatsApp Web adapter. "
                "Install with: pip install neonize"
            ) from exc

        db_path = self.config.db_path
        if not db_path:
            _DEFAULT_DB_DIR.mkdir(parents=True, exist_ok=True)
            db_path = str(_DEFAULT_DB_DIR / "session.sqlite3")

        self._client = NewClient(db_path)

        @self._client.event(ConnectedEv)
        def on_connected(_client: Any, _event: Any) -> None:
            logger.info("WhatsApp Web adapter connected")
            print("\n  WhatsApp Web connected! You can now send messages.\n", file=sys.stderr)

        @self._client.event(PairStatusEv)
        def on_pair_status(_client: Any, event: Any) -> None:
            logger.info("Pair status: %s", event)

        @self._client.event(MessageEv)
        def on_message(_client: Any, event: Any) -> None:
            if self._event_loop is None or self._event_loop.is_closed():
                return
            asyncio.run_coroutine_threadsafe(
                self._handle_incoming(event), self._event_loop,
            )

        self._event_loop = asyncio.get_running_loop()
        self._running = True
        self._connect_task = asyncio.create_task(self._connect_with_retry())
        logger.info("WhatsApp Web adapter starting (db: %s)", db_path)
        print(
            "\n  Scan the QR code above with WhatsApp > Linked Devices > Link a Device\n"
            "  (If already paired, connection will resume automatically.)\n",
            file=sys.stderr,
        )

    async def _connect_with_retry(self) -> None:
        """Connect to WhatsApp with exponential backoff on disconnect."""
        max_retries = 10
        base_delay = 2.0
        max_delay = 300.0

        consecutive_failures = 0
        while self._running:
            if not self._running:
                return
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._client.connect)
                if not self._running:
                    return
                consecutive_failures = 0
                logger.info("WhatsApp connection ended, will reconnect")
            except Exception as exc:
                if not self._running:
                    return
                consecutive_failures += 1
                logger.warning(
                    "WhatsApp connection failed (attempt %d): %s",
                    consecutive_failures,
                    exc,
                )
                if consecutive_failures >= max_retries:
                    logger.error(
                        "WhatsApp reconnection failed after %d consecutive attempts",
                        max_retries,
                    )
                    return

            delay = min(base_delay * (2 ** max(consecutive_failures - 1, 0)), max_delay)
            logger.info("Reconnecting in %.0fs...", delay)
            await self._sleep(delay)

    async def stop(self) -> None:
        self._running = False
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:
                pass
        if self._connect_task is not None and not self._connect_task.done():
            self._connect_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(self._connect_task), timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        logger.info("WhatsApp Web adapter stopped")

    # -- send ---------------------------------------------------------------

    async def send_prompt(
        self, session_id: str, prompt: str, schema: dict[str, Any] | None = None,
    ) -> None:
        jid = self._jid_from_session(session_id)
        if jid is None:
            logger.warning("send_prompt: unknown session %s", session_id)
            return
        await self._send_text(jid, prompt)

    async def send_result(self, session_id: str, result: dict[str, Any]) -> None:
        jid = self._jid_from_session(session_id)
        if jid is None:
            return
        lines = ["*Workflow Result*\n"]
        for key, value in result.items():
            lines.append(f"*{key}:* {value}")
        await self._send_text(jid, "\n".join(lines))

    async def send_progress(self, session_id: str, message: str) -> None:
        now = time.monotonic()
        last = self._last_progress.get(session_id, 0.0)
        if now - last < self.config.progress_throttle:
            return
        self._last_progress[session_id] = now
        jid = self._jid_from_session(session_id)
        if jid is not None:
            await self._send_text(jid, f"⏳ {message}")

    # -- file sending -------------------------------------------------------

    _IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    _VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".3gp"}
    _AUDIO_EXTS = {".mp3", ".ogg", ".opus", ".m4a", ".wav", ".aac"}

    _WA_SIZE_LIMITS: dict[str, int] = {
        "image": 16 * 1024 * 1024,       # 16 MB
        "video": 64 * 1024 * 1024,        # 64 MB (WhatsApp compresses further)
        "audio": 16 * 1024 * 1024,        # 16 MB
        "document": 100 * 1024 * 1024,    # 100 MB
    }

    def _classify_file(self, path: Path) -> str:
        ext = path.suffix.lower()
        if ext in self._IMAGE_EXTS:
            return "image"
        if ext in self._VIDEO_EXTS:
            return "video"
        if ext in self._AUDIO_EXTS:
            return "audio"
        return "document"

    def _check_file_size(self, path: Path, file_type: str) -> tuple[bool, str]:
        """Return (ok, warning_message). ok=True if within limits."""
        size = path.stat().st_size
        limit = self._WA_SIZE_LIMITS.get(file_type, self._WA_SIZE_LIMITS["document"])
        size_mb = size / (1024 * 1024)
        limit_mb = limit / (1024 * 1024)
        if size > limit:
            return False, (
                f"File '{path.name}' is {size_mb:.1f} MB, "
                f"exceeds WhatsApp {file_type} limit of {limit_mb:.0f} MB."
            )
        if size > limit * 0.8:
            return True, (
                f"File '{path.name}' is {size_mb:.1f} MB "
                f"(limit: {limit_mb:.0f} MB for {file_type})."
            )
        return True, ""

    def _guess_mimetype(self, path: Path) -> str | None:
        mime, _encoding = mimetypes.guess_type(str(path))
        return mime

    async def send_file(
        self,
        jid: str,
        file_path: str,
        caption: str | None = None,
    ) -> dict[str, Any]:
        """Send a file to a WhatsApp chat. Returns status dict.

        Auto-detects type from extension. Checks size against WhatsApp limits.
        """
        path = Path(file_path)
        if not path.exists():
            return {"ok": False, "error": f"File not found: {file_path}"}

        file_type = self._classify_file(path)
        ok, warning = self._check_file_size(path, file_type)
        if not ok:
            return {"ok": False, "error": warning, "needs_confirmation": False}

        result: dict[str, Any] = {"ok": True, "file": path.name, "type": file_type, "size_mb": round(path.stat().st_size / (1024 * 1024), 2)}
        if warning:
            result["warning"] = warning

        if self._client is None:
            return {"ok": False, "error": "WhatsApp client not connected"}

        try:
            from neonize.utils import build_jid
        except ImportError:
            return {"ok": False, "error": "neonize not available"}

        try:
            recipient = self._resolve_recipient(jid)
            file_bytes = path.read_bytes()

            if file_type == "image":
                self._client.send_image(recipient, file_bytes, caption=caption)
            elif file_type == "video":
                self._client.send_video(recipient, file_bytes, caption=caption)
            elif file_type == "audio":
                self._client.send_audio(recipient, file_bytes)
            else:
                mimetype = self._guess_mimetype(path)
                self._client.send_document(
                    recipient, file_bytes,
                    caption=caption,
                    filename=path.name,
                    mimetype=mimetype,
                )

            self._record_outbound_message(jid, f"[file: {path.name}]")
            result["sent"] = True
            return result
        except Exception as exc:
            logger.exception("Failed to send file %s to %s", path.name, jid)
            return {"ok": False, "error": str(exc)}

    async def send_files(
        self,
        jid: str,
        file_paths: list[str],
        caption: str | None = None,
        *,
        confirm_threshold: int = 5,
        size_warn_mb: float = 50.0,
    ) -> dict[str, Any]:
        """Send multiple files with safety checks.

        Returns a dict with ``needs_confirmation=True`` when file count exceeds
        *confirm_threshold* or total size exceeds *size_warn_mb*.  The caller
        should present the warning and re-call with confirmed paths only.
        """
        paths = [Path(p) for p in file_paths]
        missing = [p for p in paths if not p.exists()]
        if missing:
            return {"ok": False, "error": f"Files not found: {[str(m) for m in missing]}"}

        total_size = sum(p.stat().st_size for p in paths)
        total_mb = total_size / (1024 * 1024)

        if len(paths) > confirm_threshold or total_mb > size_warn_mb:
            file_list = "\n".join(
                f"  {p.name} ({p.stat().st_size / (1024*1024):.1f} MB)" for p in paths
            )
            return {
                "ok": True,
                "needs_confirmation": True,
                "message": (
                    f"About to send {len(paths)} files ({total_mb:.1f} MB total):\n"
                    f"{file_list}\n\n"
                    f"Reply 'yes' to confirm or 'no' to cancel."
                ),
                "files": [str(p) for p in paths],
            }

        results = []
        for p in paths:
            r = await self.send_file(jid, str(p), caption=caption)
            results.append(r)

        sent = sum(1 for r in results if r.get("sent"))
        failed = [r for r in results if not r.get("ok")]
        return {
            "ok": len(failed) == 0,
            "sent": sent,
            "total": len(paths),
            "total_mb": round(total_mb, 2),
            "failed": failed if failed else None,
        }

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

    # -- message handling ---------------------------------------------------

    @staticmethod
    def _jid_to_str(jid: Any) -> str:
        """Convert a neonize protobuf JID to a clean ``user@server`` string."""
        user = getattr(jid, "User", "") or ""
        server = getattr(jid, "Server", "") or "s.whatsapp.net"
        return f"{user}@{server}" if user else str(jid)

    async def _download_media(self, msg: Any) -> tuple[bytes, str, str] | None:
        """Download media from a message. Returns (data, filename, mime_type) or None."""
        if self._client is None:
            return None
        loop = asyncio.get_running_loop()
        try:
            data = await loop.run_in_executor(None, self._client.download_any, msg)
        except Exception:
            logger.debug("Media download failed", exc_info=True)
            return None
        if not data:
            return None
        max_bytes = int(self.config.max_inbound_media_mb * 1024 * 1024)
        if len(data) > max_bytes:
            logger.warning("Downloaded media exceeds size limit: %d bytes", len(data))
            return None

        if hasattr(msg, "documentMessage") and msg.documentMessage:
            filename = getattr(msg.documentMessage, "fileName", "") or ""
            mime = getattr(msg.documentMessage, "mimetype", "") or "application/octet-stream"
        elif hasattr(msg, "imageMessage") and msg.imageMessage:
            filename = ""
            mime = getattr(msg.imageMessage, "mimetype", "") or "image/jpeg"
        elif hasattr(msg, "videoMessage") and msg.videoMessage:
            filename = ""
            mime = getattr(msg.videoMessage, "mimetype", "") or "video/mp4"
        elif hasattr(msg, "audioMessage") and msg.audioMessage:
            filename = ""
            mime = getattr(msg.audioMessage, "mimetype", "") or "audio/ogg"
        elif hasattr(msg, "stickerMessage") and msg.stickerMessage:
            filename = ""
            mime = getattr(msg.stickerMessage, "mimetype", "") or "image/webp"
        else:
            filename = ""
            mime = "application/octet-stream"

        if not filename:
            ext = mimetypes.guess_extension(mime) or ""
            filename = f"media_{id(msg) % 100000:05d}{ext}"

        return data, filename, mime

    def _save_media_temp(self, data: bytes, filename: str) -> Path:
        """Save media to temp directory and schedule cleanup."""
        _DEFAULT_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = f"{int(time.time())}_{filename}"
        path = _DEFAULT_MEDIA_DIR / safe_name
        path.write_bytes(data)
        self._schedule_media_cleanup(path)
        return path

    def _schedule_media_cleanup(self, path: Path) -> None:
        """Delete a temp media file after _MEDIA_CLEANUP_SECONDS."""
        def _cleanup() -> None:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
        timer = threading.Timer(_MEDIA_CLEANUP_SECONDS, _cleanup)
        timer.daemon = True
        timer.start()

    async def _handle_incoming(self, event: Any) -> None:
        try:
            info = event.Info
            source = info.MessageSource
            sender_jid = self._jid_to_str(source.Sender)
            chat_jid = self._jid_to_str(source.Chat)

            msg = event.Message
            text = ""
            attachment_path: str | None = None
            is_voice_note = False

            if hasattr(msg, "conversation") and msg.conversation:
                text = msg.conversation
            elif hasattr(msg, "extendedTextMessage") and msg.extendedTextMessage:
                text = msg.extendedTextMessage.text or ""

            elif hasattr(msg, "imageMessage") and msg.imageMessage:
                caption = getattr(msg.imageMessage, "caption", "") or ""
                file_len = getattr(msg.imageMessage, "fileLength", 0) or 0
                if file_len > self.config.max_inbound_media_mb * 1024 * 1024:
                    await self._send_text(
                        chat_jid,
                        f"That image is too large ({file_len / (1024*1024):.1f} MB). "
                        f"Max is {self.config.max_inbound_media_mb:.0f} MB.",
                    )
                    return
                result = await self._download_media(msg)
                if result:
                    data, filename, _mime = result
                    path = self._save_media_temp(data, filename)
                    attachment_path = str(path)
                text = f"[User sent image]\n{caption}".strip()

            elif hasattr(msg, "documentMessage") and msg.documentMessage:
                doc = msg.documentMessage
                caption = getattr(doc, "caption", "") or ""
                doc_filename = getattr(doc, "fileName", "") or "document"
                file_len = getattr(doc, "fileLength", 0) or 0
                if file_len > self.config.max_inbound_media_mb * 1024 * 1024:
                    await self._send_text(
                        chat_jid,
                        f"That file is too large ({file_len / (1024*1024):.1f} MB). "
                        f"Max is {self.config.max_inbound_media_mb:.0f} MB.",
                    )
                    return
                result = await self._download_media(msg)
                if result:
                    data, dl_filename, _mime = result
                    path = self._save_media_temp(data, dl_filename)
                    attachment_path = str(path)
                text = f"[User sent file: {doc_filename}]\n{caption}".strip()

            elif hasattr(msg, "audioMessage") and msg.audioMessage:
                file_len = getattr(msg.audioMessage, "fileLength", 0) or 0
                if file_len > self.config.max_inbound_media_mb * 1024 * 1024:
                    await self._send_text(
                        chat_jid,
                        f"That audio is too large ({file_len / (1024*1024):.1f} MB). "
                        f"Max is {self.config.max_inbound_media_mb:.0f} MB.",
                    )
                    return
                is_voice_note = bool(getattr(msg.audioMessage, "ptt", False))
                result = await self._download_media(msg)
                if result:
                    data, filename, _mime = result
                    path = self._save_media_temp(data, filename)
                    attachment_path = str(path)
                if is_voice_note and attachment_path:
                    text = f"[Voice note: {attachment_path}]"
                elif attachment_path:
                    text = "[User sent audio]"

            elif hasattr(msg, "videoMessage") and msg.videoMessage:
                caption = getattr(msg.videoMessage, "caption", "") or ""
                file_len = getattr(msg.videoMessage, "fileLength", 0) or 0
                if file_len > self.config.max_inbound_media_mb * 1024 * 1024:
                    await self._send_text(
                        chat_jid,
                        f"That video is too large ({file_len / (1024*1024):.1f} MB). "
                        f"Max is {self.config.max_inbound_media_mb:.0f} MB.",
                    )
                    return
                result = await self._download_media(msg)
                if result:
                    data, filename, _mime = result
                    path = self._save_media_temp(data, filename)
                    attachment_path = str(path)
                text = f"[User sent video]\n{caption}".strip()

            elif hasattr(msg, "stickerMessage") and msg.stickerMessage:
                text = "[User sent a sticker]"

            elif hasattr(msg, "contactMessage") and msg.contactMessage:
                name = getattr(msg.contactMessage, "displayName", "") or "Unknown"
                text = f"[User shared contact: {name}]"

            elif hasattr(msg, "locationMessage") and msg.locationMessage:
                loc = msg.locationMessage
                lat = getattr(loc, "degreesLatitude", 0.0)
                lon = getattr(loc, "degreesLongitude", 0.0)
                text = f"[User shared location: {lat}, {lon}]"

            if not text and not attachment_path:
                return

            if attachment_path and not is_voice_note:
                text = f"[Attachment: {attachment_path}]\n{text}"

            # Neonize marks messages sent by our account as IsFromMe, which includes
            # commands typed from the user's phone to their own chat. Allow those
            # through unless they are just echoes of text we sent moments ago.
            if getattr(source, "IsFromMe", getattr(info, "IsFromMe", False)):
                if self._is_recent_outbound_echo(chat_jid, text):
                    return

            if not self._is_allowed(sender_jid):
                return

            external_id = chat_jid

            sid = self._session_map.get(external_id)
            if sid and sid in self._pending:
                fut = self._pending.get(sid)
                if fut and not fut.done():
                    fut.set_result({"response": text})
                    return

            if self._on_new_message is not None:
                await self._on_new_message(external_id, text)

        except Exception:
            logger.exception("Error handling incoming WhatsApp message")

    # -- internal helpers ---------------------------------------------------

    def _is_allowed(self, jid: str) -> bool:
        if not self.config.allowed_jids:
            return True
        return any(allowed in jid for allowed in self.config.allowed_jids)

    def register_session(self, session_id: str, jid: str) -> None:
        self._session_map[jid] = session_id
        self._jid_map[session_id] = jid

    def unregister_session(self, session_id: str) -> None:
        jid = self._jid_map.pop(session_id, None)
        if jid is not None:
            self._session_map.pop(jid, None)

    def _session_id_from_jid(self, jid: str) -> str | None:
        return self._session_map.get(jid)

    def _jid_from_session(self, session_id: str) -> str | None:
        return self._jid_map.get(session_id)

    def _record_outbound_message(self, jid: str, text: str) -> None:
        now = time.monotonic()
        entries = self._recent_outbound.setdefault(jid, [])
        entries.append((now, text))
        cutoff = now - _OUTBOUND_ECHO_WINDOW_SECONDS
        self._recent_outbound[jid] = [
            (ts, msg) for ts, msg in entries if ts >= cutoff
        ]

    def _is_recent_outbound_echo(self, jid: str, text: str) -> bool:
        now = time.monotonic()
        entries = self._recent_outbound.get(jid, [])
        if not entries:
            return False
        cutoff = now - _OUTBOUND_ECHO_WINDOW_SECONDS
        kept: list[tuple[float, str]] = []
        matched = False
        for ts, msg in entries:
            if ts < cutoff:
                continue
            kept.append((ts, msg))
            if msg == text:
                matched = True
        self._recent_outbound[jid] = kept
        return matched

    @staticmethod
    def _reply_prefix() -> str:
        from dan.server.concierge.identity import format_bare_prefix
        return f"{format_bare_prefix()} "

    def _resolve_recipient(self, jid: str) -> Any:
        """Resolve a JID string to a neonize JID suitable for sending.

        LID JIDs (``user@lid``) are converted to phone-number JIDs via
        ``client.get_pn_from_lid()``.  Regular JIDs use ``build_jid()``.
        """
        from neonize.utils import build_jid

        parts = jid.split("@")
        user = parts[0]
        server = parts[1] if len(parts) > 1 else "s.whatsapp.net"

        if server == "lid" and self._client is not None:
            try:
                lid_jid = build_jid(user)
                lid_jid.Server = "lid"
                pn_jid = self._client.get_pn_from_lid(lid_jid)
                pn_user = getattr(pn_jid, "User", "") or ""
                if pn_user:
                    logger.info("Resolved LID %s -> phone %s", jid, pn_user)
                    return pn_jid
            except Exception as exc:
                logger.warning("LID resolution failed for %s: %s", jid, exc)

        return build_jid(user)

    def _send_text_fallback(self, jid: str, text: str) -> None:
        """Best-effort fallback after retry exhaustion.

        This uses the same client, but strips prefixes/chunking and sends a short
        plain-text apology. Even when the original message fails, a smaller
        fallback can still succeed for content-specific send errors.
        """
        if self._client is None:
            return
        try:
            recipient = self._resolve_recipient(jid)
            self._record_outbound_message(jid, text)
            self._client.send_message(recipient, text)
        except Exception:
            logger.exception("WhatsApp fallback send also failed for %s", jid)

    async def _send_text(self, jid: str, text: str) -> None:
        if self._client is None:
            logger.error("_send_text called but client is None")
            return
        try:
            from neonize.utils import build_jid  # noqa: F401 — ensure available
        except ImportError:
            logger.error("neonize not available for sending")
            return

        recipient = self._resolve_recipient(jid)
        prefixed = f"{self._reply_prefix()}{text}"
        for chunk in _split_message(prefixed):
            self._record_outbound_message(jid, chunk)
            logger.info("Sending WhatsApp message to %s (%d chars)", jid, len(chunk))
            for attempt in range(2):
                try:
                    resp = self._client.send_message(recipient, chunk)
                    logger.info("send_message response ID: %s", getattr(resp, "ID", "?"))
                    break
                except Exception:
                    if attempt == 0:
                        logger.warning("WhatsApp send failed, retrying in 2s...")
                        await self._sleep(2)
                    else:
                        logger.exception("Failed to send WhatsApp message to %s after retry", jid)
                        self._send_text_fallback(
                            jid,
                            "Sorry, I couldn't deliver that response. Please try again.",
                        )
                        return


def _split_message(text: str, max_len: int = _MAX_MESSAGE_LENGTH) -> list[str]:
    """Split text into chunks that fit WhatsApp's message length limit."""
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
        elif split_at == 0:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n ")
    return chunks


async def transcribe_audio(audio_path: str) -> str | None:
    """Transcribe audio using an OpenAI-compatible Whisper API.

    Provider resolution:
    1. DAN_WHISPER_API_KEY + DAN_WHISPER_BASE_URL + DAN_WHISPER_MODEL
    2. DAN_OPENAI_API_KEY + https://api.openai.com/v1 + whisper-1
    3. DAN_LLM_API_KEY + DAN_LLM_BASE_URL + whisper-1
    4. None → return None
    """
    import httpx

    if os.environ.get("DAN_WHISPER_API_KEY"):
        api_key = os.environ["DAN_WHISPER_API_KEY"]
        base_url = os.environ.get("DAN_WHISPER_BASE_URL", "https://api.openai.com/v1")
        model = os.environ.get("DAN_WHISPER_MODEL", "whisper-1")
    elif os.environ.get("DAN_OPENAI_API_KEY"):
        api_key = os.environ["DAN_OPENAI_API_KEY"]
        base_url = "https://api.openai.com/v1"
        model = "whisper-1"
    elif os.environ.get("DAN_LLM_API_KEY") or os.environ.get("LLM_API_KEY"):
        api_key = os.environ.get("DAN_LLM_API_KEY") or os.environ["LLM_API_KEY"]
        base_url = os.environ.get("DAN_LLM_BASE_URL", "https://api.openai.com/v1")
        model = "whisper-1"
    else:
        return None

    base_url = base_url.rstrip("/")

    path = Path(audio_path)
    if not path.exists():
        logger.warning("Audio file not found: %s", audio_path)
        return None

    mime = mimetypes.guess_type(str(path))[0] or "audio/ogg"
    url = f"{base_url}/audio/transcriptions"

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (path.name, path.read_bytes(), mime)},
                data={"model": model},
            )
        if response.status_code != 200:
            logger.warning(
                "Whisper API error %d: %s", response.status_code, response.text[:200],
            )
            return None
        result = response.json()
        return result.get("text", "").strip() or None
    except Exception:
        logger.debug("Audio transcription failed", exc_info=True)
        return None
