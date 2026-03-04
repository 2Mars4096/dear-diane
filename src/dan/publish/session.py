"""Stateful session management for published workflows.

``PublishSession`` tracks each active execution.  ``PublishedHumanRenderer``
implements the engine's ``HumanRenderer`` protocol: when a HumanNode fires,
it parks the prompt in the session and waits on an ``asyncio.Event`` until
the consumer submits input via the MCP or HTTP channel.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from dan.engine.executor import HumanRenderRequest, HumanRenderResponse

logger = logging.getLogger(__name__)

DEFAULT_HUMAN_TIMEOUT = 300.0  # 5 minutes


class SessionStatus(str, enum.Enum):
    RUNNING = "running"
    AWAITING_INPUT = "awaiting_input"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class PublishSession:
    """Tracks one published workflow execution."""

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    workflow_id: str = ""
    status: SessionStatus = SessionStatus.RUNNING
    pending_request: HumanRenderRequest | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    _input_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _input_data: dict[str, Any] = field(default_factory=dict, repr=False)

    def touch(self) -> None:
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "session_id": self.session_id,
            "workflow_id": self.workflow_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.pending_request is not None:
            d["pending_prompt"] = {
                "request_id": self.pending_request.request_id,
                "node_id": self.pending_request.node_id,
                "node_name": self.pending_request.node_name,
                "render_mode": self.pending_request.render_mode,
                "prompt": self.pending_request.prompt,
                "instructions": self.pending_request.instructions,
                "input_schema": self.pending_request.input_schema,
                "output_schema": self.pending_request.output_schema,
                "options": self.pending_request.options,
                "default_action": self.pending_request.default_action,
            }
        if self.result is not None:
            d["result"] = self.result
        if self.error is not None:
            d["error"] = self.error
        return d


class PublishSessionStore:
    """In-memory store for active publish sessions.  Thread-safe via asyncio.Lock."""

    def __init__(self) -> None:
        self._sessions: dict[str, PublishSession] = {}
        self._lock = asyncio.Lock()
        self._event_queues: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}

    async def create(self, workflow_id: str) -> PublishSession:
        async with self._lock:
            session = PublishSession(workflow_id=workflow_id)
            self._sessions[session.session_id] = session
            return session

    async def get(self, session_id: str) -> PublishSession | None:
        async with self._lock:
            return self._sessions.get(session_id)

    async def update_status(self, session_id: str, status: SessionStatus) -> None:
        async with self._lock:
            s = self._sessions.get(session_id)
            if s is not None:
                s.status = status
                s.touch()
        self.emit_event(session_id, {
            "type": "status_changed", "status": status.value,
        })

    async def set_result(self, session_id: str, result: dict[str, Any]) -> None:
        async with self._lock:
            s = self._sessions.get(session_id)
            if s is not None:
                s.result = result
                s.status = SessionStatus.COMPLETED
                s.touch()
        self.emit_event(session_id, {
            "type": "session_completed", "result": result,
        })

    async def set_error(self, session_id: str, error: str) -> None:
        async with self._lock:
            s = self._sessions.get(session_id)
            if s is not None:
                s.error = error
                s.status = SessionStatus.FAILED
                s.touch()
        self.emit_event(session_id, {
            "type": "session_failed", "error": error,
        })

    async def remove(self, session_id: str) -> None:
        async with self._lock:
            self._sessions.pop(session_id, None)

    async def list_sessions(self, workflow_id: str | None = None) -> list[PublishSession]:
        async with self._lock:
            sessions = list(self._sessions.values())
        if workflow_id:
            sessions = [s for s in sessions if s.workflow_id == workflow_id]
        return sessions

    def emit_event(self, session_id: str, event: dict[str, Any]) -> None:
        """Push an event to all subscribers of a session (non-blocking)."""
        for q in self._event_queues.get(session_id, []):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def subscribe_events(self, session_id: str):  # -> AsyncGenerator[dict]
        """Return an async generator that yields events for *session_id*.

        Terminates when the session reaches a terminal state (completed/failed)
        or is removed from the store.
        """
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=5000)
        self._event_queues.setdefault(session_id, []).append(queue)
        try:
            while True:
                session = await self.get(session_id)
                if session is None:
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield event
                except asyncio.TimeoutError:
                    pass
                if session.status in (SessionStatus.COMPLETED, SessionStatus.FAILED):
                    while not queue.empty():
                        yield queue.get_nowait()
                    yield {"type": "session_end", "status": session.status.value}
                    break
        finally:
            queues = self._event_queues.get(session_id, [])
            if queue in queues:
                queues.remove(queue)


class PublishedHumanRenderer:
    """``HumanRenderer`` for published workflows.

    When the engine hits a HumanNode, this renderer:
    1. Stores the prompt in the session.
    2. Transitions to ``awaiting_input``.
    3. Waits on ``session._input_event`` until the consumer submits input
       (via MCP ``submit_input`` tool or HTTP endpoint).
    4. Returns the submitted data as ``HumanRenderResponse``.

    Timeout returns ``default_action`` if available, else raises.
    """

    def __init__(
        self,
        session_store: PublishSessionStore,
        *,
        timeout: float = DEFAULT_HUMAN_TIMEOUT,
    ) -> None:
        self._store = session_store
        self._timeout = timeout
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
            raise RuntimeError("PublishedHumanRenderer: no active session")

        session = await self._store.get(sid)
        if session is None:
            raise RuntimeError(f"PublishedHumanRenderer: session '{sid}' not found")

        session.pending_request = request
        session.status = SessionStatus.AWAITING_INPUT
        session._input_event.clear()
        session.touch()

        timeout = request.timeout_seconds or self._timeout
        try:
            await asyncio.wait_for(session._input_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            session.pending_request = None
            session.status = SessionStatus.RUNNING
            session.touch()
            if request.default_action is not None:
                logger.info("HumanNode timeout — using default_action for session %s", sid)
                return HumanRenderResponse(
                    request_id=request.request_id,
                    data={"response": request.default_action},
                    source="timeout",
                )
            raise asyncio.TimeoutError(
                f"HumanNode timed out after {timeout}s with no default_action"
            )

        data = dict(session._input_data)
        session.pending_request = None
        session._input_data.clear()
        session.status = SessionStatus.RUNNING
        session.touch()

        return HumanRenderResponse(
            request_id=request.request_id,
            data=data,
            source="human",
        )


async def submit_human_input(
    store: PublishSessionStore,
    session_id: str,
    data: dict[str, Any],
) -> PublishSession | None:
    """Submit human input to an awaiting session.

    Returns the updated session, or ``None`` if the session doesn't exist
    or isn't in ``awaiting_input`` state.
    """
    session = await store.get(session_id)
    if session is None or session.status != SessionStatus.AWAITING_INPUT:
        return None
    session._input_data = data
    session._input_event.set()
    return session
