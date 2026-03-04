"""Adapter framework — protocol, config, renderer bridge, and session management.

The ``MessagingAdapter`` protocol defines the contract every messaging backend
(email, Telegram, WhatsApp, …) must implement.  ``MessagingHumanRenderer``
bridges any adapter to the engine's ``HumanRenderer`` protocol so the DAN
engine is completely unaware of the underlying channel.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from dan.engine.executor import HumanRenderRequest, HumanRenderResponse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Session state machine
# ---------------------------------------------------------------------------

class SessionState(str, enum.Enum):
    IDLE = "idle"
    RUNNING = "running"
    AWAITING_HUMAN = "awaiting_human"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class AdapterSession:
    """Tracks the lifecycle of one workflow run inside a messaging conversation."""

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    external_id: str = ""
    run_id: str = ""
    state: SessionState = SessionState.IDLE
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    pending_request: HumanRenderRequest | None = None
    response_future: asyncio.Future[dict[str, Any]] | None = None

    def touch(self) -> None:
        self.last_active = time.time()


class AdapterSessionStore:
    """Maps external conversation IDs to ``AdapterSession`` instances.

    Thread-safe via an ``asyncio.Lock`` — all public methods are coroutines.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, AdapterSession] = {}
        self._by_external: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def create(self, external_id: str) -> AdapterSession:
        async with self._lock:
            session = AdapterSession(external_id=external_id)
            self._sessions[session.session_id] = session
            self._by_external[external_id] = session.session_id
            return session

    async def get_by_external(self, external_id: str) -> AdapterSession | None:
        async with self._lock:
            sid = self._by_external.get(external_id)
            return self._sessions.get(sid) if sid else None

    async def get(self, session_id: str) -> AdapterSession | None:
        async with self._lock:
            return self._sessions.get(session_id)

    async def update_state(self, session_id: str, state: SessionState) -> None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                session.state = state
                session.touch()

    async def remove(self, session_id: str) -> None:
        async with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is not None:
                self._by_external.pop(session.external_id, None)

    async def all_sessions(self) -> list[AdapterSession]:
        async with self._lock:
            return list(self._sessions.values())


# ---------------------------------------------------------------------------
# Adapter config
# ---------------------------------------------------------------------------

class AdapterConfig(BaseModel):
    """Base configuration shared by all messaging adapters."""

    workflow_path: str = ""
    api_keys: dict[str, str] = Field(default_factory=dict)
    timeout: float = 300.0
    welcome_message: str = "Welcome! Send a message to start a workflow."
    error_message: str = "Something went wrong. Please try again."
    trigger_mode: Literal["keyword", "always", "pattern"] = "always"
    trigger_pattern: str | None = None


# ---------------------------------------------------------------------------
# Messaging adapter protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class MessagingAdapter(Protocol):
    """Contract every messaging backend must fulfil."""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def send_prompt(
        self, session_id: str, prompt: str, schema: dict[str, Any] | None = None,
    ) -> None: ...

    async def wait_for_response(
        self, session_id: str, timeout: float,
    ) -> dict[str, Any]: ...

    async def send_result(self, session_id: str, result: dict[str, Any]) -> None: ...

    def set_message_callback(
        self, callback: Callable[[str, str], Any] | None,
    ) -> None:
        """Register a callback ``(external_id, text) -> ...`` for new messages."""
        ...


# ---------------------------------------------------------------------------
# Prompt formatting helpers
# ---------------------------------------------------------------------------

def format_prompt_for_messaging(request: HumanRenderRequest) -> str:
    """Convert a ``HumanRenderRequest`` into a plain-text message."""
    parts: list[str] = []
    if request.prompt:
        parts.append(request.prompt)
    if request.instructions:
        parts.append(f"\n{request.instructions}")

    if request.render_mode == "approval":
        parts.append("\nReply YES or NO.")
    elif request.render_mode == "selection" and request.options:
        parts.append("\nChoose one:")
        for i, opt in enumerate(request.options, 1):
            parts.append(f"  {i}. {opt}")
        parts.append("Reply with the number of your choice.")
    elif request.render_mode == "form" and request.output_schema:
        props = request.output_schema.get("properties", {})
        if props:
            parts.append("\nPlease provide:")
            for i, (key, spec) in enumerate(props.items(), 1):
                desc = spec.get("description", key)
                parts.append(f"  {i}. {desc}")
            parts.append("Reply with each value on a separate line.")

    return "\n".join(parts)


def parse_response_text(
    text: str, request: HumanRenderRequest,
) -> dict[str, Any]:
    """Parse raw message text into structured data based on render mode."""
    text = text.strip()

    if request.render_mode == "approval":
        normalised = text.lower()
        approved = normalised in ("yes", "y", "true", "1", "approve", "ok")
        return {"approved": approved, "response": text}

    if request.render_mode == "selection" and request.options:
        try:
            idx = int(text) - 1
            if 0 <= idx < len(request.options):
                return {"selected": request.options[idx], "index": idx, "response": text}
        except ValueError:
            for opt in request.options:
                if text.lower() == opt.lower():
                    return {"selected": opt, "index": request.options.index(opt), "response": text}
        return {"response": text}

    if request.render_mode == "form" and request.output_schema:
        props = request.output_schema.get("properties", {})
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        result: dict[str, Any] = {}
        for (key, _spec), value in zip(props.items(), lines):
            result[key] = value
        result["response"] = text
        return result

    return {"response": text}


# ---------------------------------------------------------------------------
# Trigger matching
# ---------------------------------------------------------------------------

def should_trigger(message: str, config: AdapterConfig) -> bool:
    """Return ``True`` if *message* should trigger a new workflow run."""
    if config.trigger_mode == "always":
        return True
    if config.trigger_mode == "keyword":
        pat = config.trigger_pattern or ""
        return message.strip().lower().startswith(pat.lower())
    if config.trigger_mode == "pattern":
        pat = config.trigger_pattern or ""
        return bool(re.search(pat, message))
    return False


# ---------------------------------------------------------------------------
# MessagingHumanRenderer — HumanRenderer bridge
# ---------------------------------------------------------------------------

class MessagingHumanRenderer:
    """Bridges a ``MessagingAdapter`` to the engine's ``HumanRenderer`` protocol.

    One renderer is created per adapter instance.  When the engine calls
    ``render(request)``, the renderer:

    1. Formats the prompt for the messaging channel.
    2. Sends it via ``adapter.send_prompt()``.
    3. Waits for a reply via ``adapter.wait_for_response()``.
    4. Parses the reply and returns a ``HumanRenderResponse``.
    """

    def __init__(
        self,
        adapter: MessagingAdapter,
        session_store: AdapterSessionStore,
    ) -> None:
        self._adapter = adapter
        self._sessions = session_store
        self._active_session_id: str | None = None

    @property
    def active_session_id(self) -> str | None:
        return self._active_session_id

    @active_session_id.setter
    def active_session_id(self, value: str | None) -> None:
        self._active_session_id = value

    async def render(self, request: HumanRenderRequest) -> HumanRenderResponse:
        sid = self._active_session_id
        if sid is None:
            raise RuntimeError("No active session — cannot render HumanNode prompt")

        session = await self._sessions.get(sid)
        if session is None:
            raise RuntimeError(f"Session '{sid}' not found in store")

        await self._sessions.update_state(sid, SessionState.AWAITING_HUMAN)
        session.pending_request = request

        prompt_text = format_prompt_for_messaging(request)
        schema = request.output_schema

        await self._adapter.send_prompt(sid, prompt_text, schema)

        timeout = request.timeout_seconds or 300.0
        try:
            raw = await self._adapter.wait_for_response(sid, timeout)
        except asyncio.TimeoutError:
            await self._sessions.update_state(sid, SessionState.RUNNING)
            if request.default_action is not None:
                return HumanRenderResponse(
                    request_id=request.request_id,
                    data={"response": request.default_action},
                    source="timeout",
                )
            raise

        await self._sessions.update_state(sid, SessionState.RUNNING)
        session.pending_request = None

        if isinstance(raw, dict) and raw:
            data = raw
        else:
            data = parse_response_text(str(raw.get("response", "")), request) if isinstance(raw, dict) else {"response": str(raw)}

        return HumanRenderResponse(
            request_id=request.request_id,
            data=data,
            source="human",
        )

    def as_callback(self):
        """Return an ``async callable(dict) -> dict`` for the legacy Engine API."""

        async def _callback(meta: dict[str, Any]) -> dict[str, Any]:
            request = HumanRenderRequest(
                request_id=meta.get("request_id", str(uuid.uuid4())),
                node_id=meta.get("node_id", ""),
                prompt=meta.get("prompt", ""),
            )
            response = await self.render(request)
            return response.data

        return _callback
