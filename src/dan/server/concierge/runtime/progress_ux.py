from __future__ import annotations

"""Runtime-facing progress and reassurance helpers."""

import logging
import time
import uuid
from typing import Any

from dan.chat_events import ChatCompleteEvent

from ..models import SurfaceMessage

logger = logging.getLogger(__name__)

_MESSAGING_SURFACES = {"telegram", "whatsapp", "whatsapp-web", "email"}
_ACTIVITY_LABELS = (
    "Gathering relevant context",
    "Researching your request",
    "Analyzing information",
    "Pulling things together",
)


def is_messaging_surface(msg: SurfaceMessage) -> bool:
    surface = str(getattr(msg, "surface", "") or "").split(":", 1)[0]
    return surface in _MESSAGING_SURFACES


def format_elapsed_seconds(elapsed_seconds: float) -> str:
    seconds = max(1, int(round(elapsed_seconds)))
    if seconds < 60:
        return f"{seconds}s elapsed"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        if seconds == 0:
            return f"{minutes}m elapsed"
        return f"{minutes}m {seconds}s elapsed"
    hours, minutes = divmod(minutes, 60)
    if minutes == 0 and seconds == 0:
        return f"{hours}h elapsed"
    if seconds == 0:
        return f"{hours}h {minutes}m elapsed"
    return f"{hours}h {minutes}m {seconds}s elapsed"


def format_progress_status(prefix: str, label: str, elapsed_seconds: float) -> str:
    clean_label = label.strip().rstrip(".!?")
    if elapsed_seconds > 0:
        return f"{prefix} — {clean_label} ({format_elapsed_seconds(elapsed_seconds)})"
    return f"{prefix} — {clean_label}"


def set_progress_phase(
    progress_sessions: dict[str, Any],
    external_id: str,
    phase_id: str,
    name: str,
    detail: str | None = None,
) -> bool:
    session = progress_sessions.get(external_id)
    if session is None:
        return False
    current = session.get_current_phase()
    created_phase = False
    if current is None or current.id != phase_id:
        if current is not None and current.status == "active":
            summary = current.summary or (
                current.sub_steps[-1] if current.sub_steps else current.name
            )
            session.complete_phase(current.id, summary)
        session.start_phase(phase_id, name)
        created_phase = True
    if detail:
        if created_phase:
            phase = session.get_current_phase()
            if phase is not None:
                phase.sub_steps.append(detail)
                return created_phase
        session.update_phase(phase_id, detail)
    return created_phase


def make_phase_event(
    progress_sessions: dict[str, Any],
    external_id: str,
    phase_id: str,
    name: str,
    detail: str | None = None,
    *,
    force: bool = False,
    min_phase_interval: float,
) -> ChatCompleteEvent | None:
    created_phase = set_progress_phase(
        progress_sessions,
        external_id,
        phase_id,
        name,
        detail,
    )
    session = progress_sessions.get(external_id)
    if session is None:
        return None
    if getattr(session, "verbosity", "minimal") == "minimal":
        return None
    now = time.monotonic()
    last_phase_event_time = float(
        getattr(session, "_last_phase_event_time", 0.0) or 0.0,
    )
    if (
        not force
        and not created_phase
        and now - last_phase_event_time < min_phase_interval
    ):
        return None
    session._last_phase_event_time = now
    label = detail or name
    elapsed_seconds = session.elapsed_total()
    return ChatCompleteEvent(
        message_id=uuid.uuid4().hex[:12],
        content=format_progress_status("Working on it", label, elapsed_seconds),
        token_usage={},
        context_window=0,
        graph_revision="",
        detected_mode="progress_ack",
        phase_label=label,
    )


def create_progress_renderer(surface: str) -> Any | None:
    try:
        from ..progress_ux import CLIProgressRenderer
    except ImportError:
        return None
    if surface == "cli":
        return CLIProgressRenderer()
    return None


def ensure_progress_session(progress_sessions: dict[str, Any], msg: SurfaceMessage) -> None:
    if msg.external_id in progress_sessions:
        return
    try:
        from ..progress_ux import (
            ProgressSession,
            get_user_verbosity_override,
            resolve_verbosity,
        )

        surface = msg.surface or "cli"
        verbosity = (
            get_user_verbosity_override(msg.external_id)
            or resolve_verbosity(surface)
        )
        progress_session = ProgressSession(surface=surface, verbosity=verbosity)
        renderer = create_progress_renderer(surface)
        if renderer is not None:
            progress_session.renderer = renderer
        progress_sessions[msg.external_id] = progress_session
        set_progress_phase(
            progress_sessions,
            msg.external_id,
            "intake",
            "Understanding your request",
        )
    except Exception:
        logger.debug("Progress session init failed", exc_info=True)


def build_reassurance_message(
    progress_sessions: dict[str, Any],
    msg: SurfaceMessage,
    reassurance_count: int,
) -> str:
    progress_session = progress_sessions.get(msg.external_id)
    elapsed_seconds = (
        progress_session.elapsed_total()
        if progress_session is not None
        else 0.0
    )
    prefix = "Working on it" if reassurance_count == 0 else "Still working"
    if progress_session is not None:
        phase = progress_session.get_current_phase()
        if phase is not None:
            latest = (
                phase.sub_steps[-1]
                if phase.sub_steps
                else (phase.summary or phase.name)
            ).strip().rstrip(".!?")
            if latest:
                return format_progress_status(prefix, latest, elapsed_seconds)

    idx = min(reassurance_count, len(_ACTIVITY_LABELS) - 1)
    return format_progress_status(prefix, _ACTIVITY_LABELS[idx], elapsed_seconds)
