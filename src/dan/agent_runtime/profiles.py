"""Explicit agent-profile resolution for surface adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from dan.agent_runtime.types import AgentProfile

_MODE_ALIASES: dict[str, str] = {
    "build": "agent",
    "mutate": "agent",
}


@dataclass(frozen=True)
class ResolvedAgentProfile:
    """Normalized profile information for one agent turn."""

    requested_mode: str
    normalized_mode: str
    profile: AgentProfile

    @property
    def is_build(self) -> bool:
        return self.profile is AgentProfile.BUILD

    @property
    def is_debug(self) -> bool:
        return self.profile is AgentProfile.DEBUG

    @property
    def is_planning(self) -> bool:
        return self.profile is AgentProfile.PLANNING


def normalize_agent_mode(mode: str) -> str:
    """Normalize legacy mode aliases without importing server helpers."""
    raw_mode = str(mode or "").strip().lower()
    if not raw_mode:
        return "agent"
    if raw_mode == "auto":
        return "auto"
    return _MODE_ALIASES.get(raw_mode, raw_mode)


def resolve_agent_profile(
    *,
    mode: str,
    allow_mutation_tool: bool = False,
    graph_is_empty: bool = False,
    required_action_hints: Sequence[str] | None = None,
) -> ResolvedAgentProfile:
    """Resolve the explicit runtime profile for a chat/agent turn."""
    requested_mode = str(mode or "").strip().lower() or "agent"
    normalized_mode = normalize_agent_mode(requested_mode)
    if normalized_mode == "auto":
        normalized_mode = "agent"

    action_hints = {str(item).strip().lower() for item in (required_action_hints or ()) if str(item).strip()}
    build_requested = requested_mode in {"build", "mutate"}
    planning_requested = requested_mode in {"plan", "planning"}
    review_requested = requested_mode in {"review"}
    debug_requested = normalized_mode == "debug" or requested_mode == "debug"
    build_hint = allow_mutation_tool or graph_is_empty or "workflow_edit" in action_hints

    if debug_requested:
        profile = AgentProfile.DEBUG
    elif planning_requested:
        profile = AgentProfile.PLANNING
    elif build_requested or build_hint:
        profile = AgentProfile.BUILD
    elif review_requested:
        profile = AgentProfile.REVIEW
    else:
        profile = AgentProfile.DIRECT_TASK

    return ResolvedAgentProfile(
        requested_mode=requested_mode,
        normalized_mode=normalized_mode,
        profile=profile,
    )


__all__ = [
    "ResolvedAgentProfile",
    "normalize_agent_mode",
    "resolve_agent_profile",
]
