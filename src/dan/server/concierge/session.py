from __future__ import annotations

import logging
import os
import time
import uuid
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from .autonomy import AutonomyResolution

if TYPE_CHECKING:
    from dan.server.concierge.models import SurfaceMessage

logger = logging.getLogger(__name__)

_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})

_VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"running"}),
    "running": frozenset({"waiting", "completed", "failed", "cancelled"}),
    "waiting": frozenset({"running", "completed", "failed", "cancelled"}),
    "paused": frozenset({"running", "cancelled"}),
}


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SessionTier(int, Enum):
    INSTANT = 0
    SINGLE = 1
    MULTI = 2


class SessionState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class SessionResult(BaseModel):
    content: str = ""
    attachments: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    child_results: dict[str, SessionResult] = Field(default_factory=dict)
    error: str | None = None
    token_usage: dict[str, int] = Field(default_factory=dict)
    duration_ms: float = 0
    tools_used: list[str] = Field(default_factory=list)
    model_used: str | None = None


class SessionTrace(BaseModel):
    session_id: str
    parent_id: str | None
    root_id: str
    tier: SessionTier
    depth: int
    task: str
    state: SessionState
    token_usage: dict[str, int] = Field(default_factory=dict)
    estimated_cost: float = 0
    duration_ms: float = 0
    model_used: str | None = None
    tools_used: list[str] = Field(default_factory=list)
    error: str | None = None
    route_target: str | None = None
    action_hints: list[str] = Field(default_factory=list)
    route_source: str | None = None
    scenario_id: str | None = None
    scenario_confidence: float | None = None
    concierge_stage: str | None = None
    autonomy_level: str | None = None
    children: list[str] = Field(default_factory=list)
    created_at: float = 0
    started_at: float | None = None
    completed_at: float | None = None


class Session(BaseModel):
    id: str
    parent_id: str | None = None
    root_id: str
    tier: SessionTier
    depth: int = 0
    state: SessionState = SessionState.PENDING

    task: str
    task_context: dict[str, Any] = Field(default_factory=dict)

    msg: Any | None = None
    context: Any | None = None
    triage: Any | None = None
    autonomy_resolution: AutonomyResolution | None = None

    children: list[str] = Field(default_factory=list)
    child_execution: Literal["parallel", "serial", "mixed"] = "parallel"

    result: SessionResult | None = None

    created_at: float = Field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None

    max_depth: int = 4
    max_children: int = 8
    max_total_sessions: int = 32

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Session tree manager
# ---------------------------------------------------------------------------

class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._root_sessions: dict[str, str] = {}
        self._session_counts: dict[str, int] = {}

    # -- creation -----------------------------------------------------------

    def create_root(
        self,
        msg: SurfaceMessage,
        triage: Any,
        tier: SessionTier | int,
        autonomy_resolution: AutonomyResolution | None = None,
    ) -> Session:
        if isinstance(tier, int):
            tier = SessionTier(tier)
        sid = uuid.uuid4().hex[:16]
        max_depth = int(os.environ.get("DAN_SESSION_MAX_DEPTH", "4"))
        max_children = int(os.environ.get("DAN_SESSION_MAX_CHILDREN", "8"))
        max_total = int(os.environ.get("DAN_SESSION_MAX_TOTAL", "32"))

        session = Session(
            id=sid,
            root_id=sid,
            tier=tier,
            depth=0,
            task=msg.text,
            msg=msg,
            triage=triage,
            autonomy_resolution=autonomy_resolution,
            max_depth=max_depth,
            max_children=max_children,
            max_total_sessions=max_total,
        )
        self._sessions[sid] = session
        self._root_sessions[msg.external_id] = sid
        self._session_counts[sid] = 1
        logger.debug("created root session %s for %s (tier=%s)", sid, msg.external_id, tier.name)
        return session

    def create_child(
        self,
        parent_id: str,
        task: str,
        tier: SessionTier | int,
        task_context: dict[str, Any] | None = None,
        autonomy_resolution: AutonomyResolution | None = None,
    ) -> Session:
        if isinstance(tier, int):
            tier = SessionTier(tier)
        parent = self._sessions.get(parent_id)
        if parent is None:
            raise ValueError(f"parent session {parent_id} not found")
        if parent.tier == SessionTier.SINGLE:
            raise ValueError("SINGLE-tier sessions cannot have children")
        if not self.can_spawn_child(parent_id):
            raise ValueError(
                f"cannot spawn child for {parent_id}: "
                f"depth={parent.depth + 1}, children={len(parent.children)}, "
                f"total={self._session_counts.get(parent.root_id, 0)}"
            )

        root = self._sessions[parent.root_id]
        sid = uuid.uuid4().hex[:16]
        session = Session(
            id=sid,
            parent_id=parent_id,
            root_id=parent.root_id,
            tier=tier,
            depth=parent.depth + 1,
            task=task,
            task_context=task_context or {},
            autonomy_resolution=autonomy_resolution or parent.autonomy_resolution,
            max_depth=root.max_depth,
            max_children=root.max_children,
            max_total_sessions=root.max_total_sessions,
        )
        self._sessions[sid] = session
        parent.children.append(sid)
        self._session_counts[parent.root_id] = self._session_counts.get(parent.root_id, 0) + 1
        logger.debug("created child session %s under %s (depth=%d, tier=%s)", sid, parent_id, session.depth, tier.name)
        return session

    # -- lookup -------------------------------------------------------------

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def get_root(self, surface_id: str) -> Session | None:
        root_id = self._root_sessions.get(surface_id)
        if root_id is None:
            return None
        return self._sessions.get(root_id)

    def children_of(self, session_id: str) -> list[Session]:
        session = self._sessions.get(session_id)
        if session is None:
            return []
        return [self._sessions[cid] for cid in session.children if cid in self._sessions]

    # -- state management ---------------------------------------------------

    def update_state(self, session_id: str, new_state: SessionState | str) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"session {session_id} not found")

        if isinstance(new_state, str):
            new_state = SessionState(new_state)

        allowed = _VALID_TRANSITIONS.get(session.state.value, frozenset())
        if new_state.value not in allowed:
            raise ValueError(f"invalid transition {session.state.value} → {new_state.value} for session {session_id}")

        session.state = new_state
        now = time.time()
        if new_state == SessionState.RUNNING and session.started_at is None:
            session.started_at = now
        if new_state.value in _TERMINAL_STATES:
            session.completed_at = now

    def set_result(self, session_id: str, result: SessionResult) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"session {session_id} not found")
        session.result = result

    # -- budget enforcement -------------------------------------------------

    def can_spawn_child(self, parent_id: str) -> bool:
        parent = self._sessions.get(parent_id)
        if parent is None:
            return False
        root = self._sessions.get(parent.root_id)
        if root is None:
            return False
        if parent.depth + 1 > root.max_depth:
            return False
        if len(parent.children) >= root.max_children:
            return False
        if self._session_counts.get(parent.root_id, 0) >= root.max_total_sessions:
            return False
        return True

    # -- tree operations ----------------------------------------------------

    def cancel_tree(self, root_id: str) -> None:
        root = self._sessions.get(root_id)
        if root is None:
            return
        stack = [root_id]
        cancelled = 0
        while stack:
            sid = stack.pop()
            s = self._sessions.get(sid)
            if s is None:
                continue
            if s.state.value not in _TERMINAL_STATES:
                s.state = SessionState.CANCELLED
                s.completed_at = time.time()
                cancelled += 1
            stack.extend(s.children)
        logger.debug("cancelled %d sessions in tree %s", cancelled, root_id)

    def prune_completed(self, max_age_seconds: float = 300) -> int:
        now = time.time()
        to_remove: list[str] = []
        for rid, sid in list(self._root_sessions.items()):
            root = self._sessions.get(sid)
            if root is None:
                to_remove.append(rid)
                continue
            if root.state.value in _TERMINAL_STATES and root.completed_at is not None:
                if now - root.completed_at > max_age_seconds:
                    to_remove.append(rid)

        removed = 0
        for rid in to_remove:
            root_sid = self._root_sessions.pop(rid, None)
            if root_sid is not None:
                removed += self._remove_tree(root_sid)
        return removed

    def _remove_tree(self, session_id: str) -> int:
        count = 0
        stack = [session_id]
        while stack:
            sid = stack.pop()
            s = self._sessions.pop(sid, None)
            if s is None:
                continue
            count += 1
            stack.extend(s.children)
        self._session_counts.pop(session_id, None)
        return count

    # -- telemetry / export -------------------------------------------------

    @staticmethod
    def _session_metadata(session: Session) -> dict[str, Any]:
        msg = getattr(session, "msg", None)
        metadata = getattr(msg, "metadata", None)
        return metadata if isinstance(metadata, dict) else {}

    @staticmethod
    def _session_result_metadata(session: Session) -> dict[str, Any]:
        result = getattr(session, "result", None)
        metadata = getattr(result, "metadata", None) if result is not None else None
        return metadata if isinstance(metadata, dict) else {}

    @staticmethod
    def _handoff_metadata(session: Session) -> dict[str, Any]:
        task_context = getattr(session, "task_context", None)
        if not isinstance(task_context, dict):
            return {}
        handoff = task_context.get("handoff")
        return handoff if isinstance(handoff, dict) else {}

    @classmethod
    def _session_provenance(cls, session: Session) -> dict[str, Any]:
        msg_metadata = cls._session_metadata(session)
        result_metadata = cls._session_result_metadata(session)
        handoff = cls._handoff_metadata(session)
        triage = getattr(session, "triage", None)
        route = getattr(triage, "route", None)

        route_target = (
            str(getattr(route, "target", "") or "").strip()
            or str(handoff.get("route_target", "") or "").strip()
            or str(msg_metadata.get("route_target", "") or "").strip()
            or str(result_metadata.get("route_target", "") or "").strip()
            or None
        )

        raw_action_hints = (
            list(getattr(route, "action_hints", None) or [])
            or list(handoff.get("action_hints", []) or [])
            or list(msg_metadata.get("action_hints", []) or [])
            or list(result_metadata.get("action_hints", []) or [])
        )
        action_hints = [
            str(item).strip()
            for item in raw_action_hints
            if str(item).strip()
        ]

        scenario_confidence = getattr(triage, "scenario_confidence", None)
        if scenario_confidence is None:
            scenario_confidence = msg_metadata.get("scenario_confidence")
        if scenario_confidence is None:
            scenario_confidence = result_metadata.get("scenario_confidence")
        if scenario_confidence is not None:
            try:
                scenario_confidence = float(scenario_confidence)
            except (TypeError, ValueError):
                scenario_confidence = None

        autonomy_level = None
        autonomy_resolution = getattr(session, "autonomy_resolution", None)
        if autonomy_resolution is not None:
            autonomy_level = str(
                getattr(autonomy_resolution, "effective_level", "") or ""
            ).strip() or None
        if autonomy_level is None:
            raw_resolution = msg_metadata.get("autonomy_resolution")
            if isinstance(raw_resolution, dict):
                autonomy_level = str(
                    raw_resolution.get("effective_level", "") or ""
                ).strip() or None
        if autonomy_level is None:
            raw_resolution = result_metadata.get("autonomy_resolution")
            if isinstance(raw_resolution, dict):
                autonomy_level = str(
                    raw_resolution.get("effective_level", "") or ""
                ).strip() or None

        route_source = str(getattr(triage, "route_source", "") or "").strip() or None
        if route_source is None:
            route_source = str(msg_metadata.get("route_source", "") or "").strip() or None
        if route_source is None:
            route_source = str(result_metadata.get("route_source", "") or "").strip() or None

        scenario_id = getattr(triage, "scenario_id", None)
        if scenario_id is None:
            scenario_id = msg_metadata.get("scenario_id")
        if scenario_id is None:
            scenario_id = result_metadata.get("scenario_id")
        scenario_id = str(scenario_id).strip() or None if scenario_id is not None else None

        concierge_stage = (
            str(msg_metadata.get("concierge_stage", "") or "").strip()
            or str(result_metadata.get("concierge_stage", "") or "").strip()
            or None
        )

        return {
            "route_target": route_target,
            "action_hints": action_hints,
            "route_source": route_source,
            "scenario_id": scenario_id,
            "scenario_confidence": scenario_confidence,
            "concierge_stage": concierge_stage,
            "autonomy_level": autonomy_level,
        }

    def build_trace(self, root_id: str) -> list[SessionTrace]:
        traces: list[SessionTrace] = []
        stack = [root_id]
        while stack:
            sid = stack.pop()
            s = self._sessions.get(sid)
            if s is None:
                continue
            duration = 0.0
            if s.started_at is not None and s.completed_at is not None:
                duration = (s.completed_at - s.started_at) * 1000
            elif s.started_at is not None:
                duration = (time.time() - s.started_at) * 1000

            token_usage: dict[str, int] = {}
            tools_used: list[str] = []
            model_used: str | None = None
            error: str | None = None
            if s.result:
                token_usage = s.result.token_usage
                tools_used = s.result.tools_used
                model_used = s.result.model_used
                error = s.result.error
            provenance = self._session_provenance(s)

            traces.append(SessionTrace(
                session_id=s.id,
                parent_id=s.parent_id,
                root_id=s.root_id,
                tier=s.tier,
                depth=s.depth,
                task=s.task,
                state=s.state,
                token_usage=token_usage,
                duration_ms=duration,
                model_used=model_used,
                tools_used=tools_used,
                error=error,
                route_target=provenance["route_target"],
                action_hints=provenance["action_hints"],
                route_source=provenance["route_source"],
                scenario_id=provenance["scenario_id"],
                scenario_confidence=provenance["scenario_confidence"],
                concierge_stage=provenance["concierge_stage"],
                autonomy_level=provenance["autonomy_level"],
                children=list(s.children),
                created_at=s.created_at,
                started_at=s.started_at,
                completed_at=s.completed_at,
            ))
            stack.extend(s.children)
        return traces

    def export_tree(self, root_id: str) -> dict[str, Any]:
        def _export(sid: str) -> dict[str, Any] | None:
            s = self._sessions.get(sid)
            if s is None:
                return None
            provenance = self._session_provenance(s)
            node: dict[str, Any] = {
                "id": s.id,
                "parent_id": s.parent_id,
                "tier": s.tier.name,
                "depth": s.depth,
                "state": s.state.value,
                "task": s.task,
                "child_execution": s.child_execution,
                "route_target": provenance["route_target"],
                "action_hints": provenance["action_hints"],
                "route_source": provenance["route_source"],
                "scenario_id": provenance["scenario_id"],
                "scenario_confidence": provenance["scenario_confidence"],
                "concierge_stage": provenance["concierge_stage"],
                "autonomy_level": provenance["autonomy_level"],
                "created_at": s.created_at,
                "started_at": s.started_at,
                "completed_at": s.completed_at,
            }
            if s.result:
                node["result"] = {
                    "content": s.result.content,
                    "error": s.result.error,
                    "token_usage": s.result.token_usage,
                    "duration_ms": s.result.duration_ms,
                    "tools_used": s.result.tools_used,
                    "model_used": s.result.model_used,
                }
            children = []
            for cid in s.children:
                child_export = _export(cid)
                if child_export is not None:
                    children.append(child_export)
            if children:
                node["children"] = children
            return node

        return _export(root_id) or {}
