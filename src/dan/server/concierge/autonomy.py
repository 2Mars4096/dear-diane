from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel

_VALID_PREFERENCES = {"auto", "careful", "balanced", "aggressive"}
_RISKY_KEYWORDS = (
    "delete",
    "remove",
    "destroy",
    "unpublish",
    "cancel",
    "drop",
    "wipe",
    "send",
    "email",
    "message",
    "post",
    "publish",
)
_DIRECTIVE_KEYWORDS = (
    "implement",
    "fix",
    "patch",
    "update",
    "edit",
    "add",
    "create",
    "refactor",
    "wire",
    "run",
    "test",
)
_MESSAGING_SURFACES = {"telegram", "whatsapp", "whatsapp-web", "email"}
_QUESTION_PREFIX_RE = re.compile(
    r"^\s*(?:what|how|why|where|when|which|who|can you|could you|would you|is|are|do|does|did|should)\b",
    re.IGNORECASE,
)


class AutonomyPreference(str, Enum):
    AUTO = "auto"
    CAREFUL = "careful"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


class AutonomySource(str, Enum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    FALLBACK = "fallback"


class AutonomyResolution(BaseModel):
    preferred_level: str = AutonomyPreference.AUTO.value
    effective_level: str = AutonomyPreference.BALANCED.value
    source: str = AutonomySource.FALLBACK.value
    reason: str = ""
    announce_change: bool = False


def normalize_autonomy_preference(
    value: str | None,
    *,
    default: str = AutonomyPreference.AUTO.value,
) -> str:
    raw = str(value or "").strip().lower()
    if raw in _VALID_PREFERENCES:
        return raw
    return default


def normalize_legacy_autonomy_level(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    return {
        "interactive": AutonomyPreference.CAREFUL.value,
        "supervised": AutonomyPreference.BALANCED.value,
        "autonomous": AutonomyPreference.AGGRESSIVE.value,
    }.get(raw, normalize_autonomy_preference(raw))


def autonomy_max_tool_turns(level: str | None) -> int:
    normalized = normalize_autonomy_preference(level, default=AutonomyPreference.BALANCED.value)
    return {
        AutonomyPreference.CAREFUL.value: 12,
        AutonomyPreference.BALANCED.value: 24,
        AutonomyPreference.AGGRESSIVE.value: 36,
    }[normalized]


def autonomy_cost_confirm_threshold(base_threshold: float, level: str | None) -> float:
    normalized = normalize_autonomy_preference(level, default=AutonomyPreference.BALANCED.value)
    if normalized == AutonomyPreference.CAREFUL.value:
        return min(base_threshold, 0.25)
    if normalized == AutonomyPreference.AGGRESSIVE.value:
        return max(base_threshold, 2.0)
    return base_threshold


def build_autonomy_announcement(resolution: AutonomyResolution | None) -> str:
    if resolution is None or not resolution.announce_change:
        return ""
    level = resolution.effective_level
    reason = resolution.reason.strip()
    if reason:
        return f"Autonomy: switched to `{level}` ({reason})."
    return f"Autonomy: switched to `{level}`."


def infer_autonomy_level(
    *,
    text: str,
    triage: Any = None,
    surface: str = "",
    action_hints: list[str] | None = None,
) -> tuple[str, str, str]:
    lower = (text or "").strip().lower()
    hints = {str(h or "").strip().lower() for h in action_hints or [] if str(h or "").strip()}
    surface_kind = str(surface or "").split(":", 1)[0].lower()
    intent = str(getattr(triage, "intent", "") or "").lower()
    route = getattr(triage, "route", None)
    route_target = str(getattr(route, "target", "") or "").lower()

    if any(keyword in lower for keyword in _RISKY_KEYWORDS):
        return AutonomyPreference.CAREFUL.value, AutonomySource.INFERRED.value, "risky or irreversible wording"
    if "publish" in hints:
        return AutonomyPreference.CAREFUL.value, AutonomySource.INFERRED.value, "publish-like action"
    if surface_kind in _MESSAGING_SURFACES and intent == "plan":
        return AutonomyPreference.CAREFUL.value, AutonomySource.INFERRED.value, "messaging-surface mutation risk"
    if "?" in lower and any(h in hints for h in {"write_file", "workflow_edit"}):
        return AutonomyPreference.CAREFUL.value, AutonomySource.INFERRED.value, "ambiguous write request"
    if _QUESTION_PREFIX_RE.search(lower) and intent == "ask":
        return AutonomyPreference.BALANCED.value, AutonomySource.FALLBACK.value, "read-only question"

    directive_score = sum(1 for keyword in _DIRECTIVE_KEYWORDS if keyword in lower)
    if directive_score >= 2 and route_target not in {"workflow"} and "publish" not in hints:
        return AutonomyPreference.AGGRESSIVE.value, AutonomySource.INFERRED.value, "clear low-risk directive"
    if intent == "agent" and route_target in {"file", "general", ""} and directive_score >= 1:
        return AutonomyPreference.AGGRESSIVE.value, AutonomySource.INFERRED.value, "action-oriented request"

    return AutonomyPreference.BALANCED.value, AutonomySource.FALLBACK.value, "weak or mixed signals"


def resolve_autonomy(
    *,
    text: str,
    triage: Any = None,
    surface: str = "",
    turn_preference: str | None = None,
    session_preference: str | None = None,
    project_preference: str | None = None,
    env_preference: str | None = None,
    last_effective_level: str | None = None,
    action_hints: list[str] | None = None,
) -> AutonomyResolution:
    turn_pref = normalize_autonomy_preference(turn_preference, default="")
    session_pref = normalize_autonomy_preference(session_preference, default="")
    project_pref = normalize_autonomy_preference(project_preference, default="")
    env_pref = normalize_autonomy_preference(env_preference, default=AutonomyPreference.AUTO.value)

    preferred = (
        turn_pref
        or session_pref
        or project_pref
        or env_pref
        or AutonomyPreference.AUTO.value
    )

    if preferred != AutonomyPreference.AUTO.value:
        effective = preferred
        source = AutonomySource.EXPLICIT.value
        if turn_pref:
            reason = "turn override"
        elif session_pref:
            reason = "session preference"
        elif project_pref:
            reason = "project preference"
        else:
            reason = "default preference"
    else:
        effective, source, reason = infer_autonomy_level(
            text=text,
            triage=triage,
            surface=surface,
            action_hints=action_hints,
        )

    last_effective = normalize_autonomy_preference(
        last_effective_level,
        default="",
    )
    return AutonomyResolution(
        preferred_level=preferred,
        effective_level=effective,
        source=source,
        reason=reason,
        announce_change=bool(last_effective and last_effective != effective),
    )
