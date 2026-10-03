"""Shared Super TUI-compatible contracts for Agent-facing surfaces."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from diane.notes import HUGO_NOTES_CAPABILITIES, enrich_notes_surface_context

SUPER_TUI_SURFACE_PROFILE = "super_tui"
SUPER_TUI_DEFAULT_BACKEND = "super_dan"
SUPER_TUI_MAX_PROMOTED_CONTINUATIONS = 16
SUPER_TUI_AGENT_CAPABILITIES: tuple[str, ...] = (
    "foreground_admission",
    "background_agent_runs",
    "task_board",
    "checkpoint_commands",
    *HUGO_NOTES_CAPABILITIES,
)


def normalize_super_tui_surface_profile(value: Any) -> str:
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if token in {"super_tui", "dan_super_tui", "gui_super_tui", "superdan_tui"}:
        return SUPER_TUI_SURFACE_PROFILE
    return token


def is_super_tui_surface_profile(value: Any) -> bool:
    return normalize_super_tui_surface_profile(value) == SUPER_TUI_SURFACE_PROFILE


def build_super_tui_profile_policy(
    *,
    model: str = "",
    base_url: str = "",
    artifact_dir: str = "",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    policy = dict(extra or {})
    if str(model or "").strip():
        policy["model"] = str(model)
    if str(base_url or "").strip():
        policy["base_url"] = str(base_url)
    if str(artifact_dir or "").strip():
        policy["artifact_dir"] = str(artifact_dir)
    return policy


def build_super_tui_tool_policy(
    *,
    max_tool_calls: int | str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    policy = dict(extra or {})
    try:
        tool_count = int(max_tool_calls or 0)
    except (TypeError, ValueError):
        tool_count = 0
    if tool_count > 0:
        policy["max_tool_calls"] = tool_count
    return policy


def build_super_tui_surface_context(
    *,
    workspace_root: str,
    workspace_source: str,
    conversation_recent_turns: Sequence[Mapping[str, Any]] = (),
    selected_skills: Sequence[str] = (),
    forced_new: bool = False,
    communication_policy: Mapping[str, Any] | None = None,
    execution_policy: Mapping[str, Any] | None = None,
    surface_policy: Mapping[str, Any] | None = None,
    attachments: Sequence[Mapping[str, Any]] = (),
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    context = dict(extra or {})
    inherited_capabilities = _string_sequence(context.get("capabilities"))
    context.update(
        {
            "workspace_root": str(workspace_root),
            "workspace_source": str(workspace_source or "super_tui"),
            "surface_profile": SUPER_TUI_SURFACE_PROFILE,
            "conversation": {"recent_turns": [dict(item) for item in conversation_recent_turns]},
            "selected_skills": list(selected_skills),
            "forced_new": bool(forced_new),
            "communication_policy": dict(communication_policy or {}),
            "execution_policy": dict(execution_policy or {}),
            "surface_policy": dict(surface_policy or {}),
            "appended_attachments": [dict(item) for item in attachments],
            "capabilities": list(
                dict.fromkeys([*inherited_capabilities, *SUPER_TUI_AGENT_CAPABILITIES])
            ),
        }
    )
    return enrich_notes_surface_context(context, workspace_root=workspace_root)


def build_super_tui_execute_overrides(
    *,
    profile_policy: Mapping[str, Any] | None = None,
    mutation_policy: Mapping[str, Any] | None = None,
    approval_policy: Mapping[str, Any] | None = None,
    tool_policy: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    surface: str = "super-tui",
    selected_skills: Sequence[str] = (),
    communication_policy: Mapping[str, Any] | None = None,
    execution_policy: Mapping[str, Any] | None = None,
    surface_policy: Mapping[str, Any] | None = None,
    attachments: Sequence[Mapping[str, Any]] = (),
    notify_completion: bool = False,
    plain: bool = False,
) -> dict[str, Any]:
    base_metadata = {
        "surface": str(surface or "super-tui"),
        "surface_profile": SUPER_TUI_SURFACE_PROFILE,
        "selected_skills": list(selected_skills),
        "communication_policy": dict(communication_policy or {}),
        "execution_policy": dict(execution_policy or {}),
        "surface_policy": dict(surface_policy or {}),
        "attachments": [dict(item) for item in attachments],
        "image_attachments": [dict(item) for item in attachments],
    }
    if notify_completion:
        base_metadata["tui_notify_completion"] = True
        base_metadata["tui_plain"] = bool(plain)
    base_metadata.update(dict(metadata or {}))

    return {
        "profile_policy": {
            "backend": SUPER_TUI_DEFAULT_BACKEND,
            **dict(profile_policy or {}),
        },
        "mutation_policy": {
            "mode": "workspace_mutation",
            "permission": "workspace_mutation",
            "external_side_effects": "deny",
            **dict(mutation_policy or {}),
        },
        "approval_policy": {
            "mode": "auto_within_workspace",
            **dict(approval_policy or {}),
        },
        "tool_policy": dict(tool_policy or {}),
        "metadata": base_metadata,
    }


def build_super_tui_agent_execute_payload(
    *,
    backend: str | None = None,
    background: bool = False,
    profile_policy: Mapping[str, Any] | None = None,
    mutation_policy: Mapping[str, Any] | None = None,
    approval_policy: Mapping[str, Any] | None = None,
    tool_policy: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    surface: str = "super-tui",
    selected_skills: Sequence[str] = (),
    communication_policy: Mapping[str, Any] | None = None,
    execution_policy: Mapping[str, Any] | None = None,
    surface_policy: Mapping[str, Any] | None = None,
    attachments: Sequence[Mapping[str, Any]] = (),
    notify_completion: bool = False,
    plain: bool = False,
    auto_execute_continuations: bool = True,
    max_promoted_continuations: int = SUPER_TUI_MAX_PROMOTED_CONTINUATIONS,
) -> dict[str, Any]:
    """Build an `/api/v2/agent-runs/{run_id}/execute` Super TUI payload."""

    backend_name = str(backend or SUPER_TUI_DEFAULT_BACKEND).strip() or SUPER_TUI_DEFAULT_BACKEND
    effective_profile = {
        "backend": backend_name,
        "surface_profile": SUPER_TUI_SURFACE_PROFILE,
        **dict(profile_policy or {}),
    }
    effective_metadata = {
        "backend": backend_name,
        "selected_backend": backend_name,
        "compatibility_profile": SUPER_TUI_SURFACE_PROFILE,
        **dict(metadata or {}),
    }
    overrides = build_super_tui_execute_overrides(
        profile_policy=effective_profile,
        mutation_policy=mutation_policy,
        approval_policy=approval_policy,
        tool_policy=tool_policy,
        metadata=effective_metadata,
        surface=surface,
        selected_skills=selected_skills,
        communication_policy=communication_policy,
        execution_policy=execution_policy,
        surface_policy=surface_policy,
        attachments=attachments,
        notify_completion=notify_completion,
        plain=plain,
    )
    return {
        "backend": backend_name,
        "surface_profile": SUPER_TUI_SURFACE_PROFILE,
        "background": bool(background),
        "auto_execute_continuations": bool(auto_execute_continuations),
        "max_promoted_continuations": int(max_promoted_continuations),
        **overrides,
    }


def apply_super_tui_execute_profile(
    payload: Mapping[str, Any],
    *,
    surface: str = "gui:super-tui",
) -> dict[str, Any]:
    """Apply Super TUI Agent defaults while preserving explicit caller overrides."""

    metadata = dict(payload.get("metadata") or {})
    selected_skills = _string_sequence(
        metadata.get("selected_skills")
        or payload.get("selected_skills")
        or ()
    )
    communication_policy = _mapping(
        metadata.get("communication_policy") or payload.get("communication_policy")
    )
    execution_policy = _mapping(
        metadata.get("execution_policy") or payload.get("execution_policy")
    )
    surface_policy = _mapping(
        metadata.get("surface_policy") or payload.get("surface_policy")
    )
    attachments = _mapping_sequence(
        metadata.get("attachments")
        or metadata.get("image_attachments")
        or payload.get("attachments")
        or ()
    )
    overrides = build_super_tui_execute_overrides(
        profile_policy=_mapping(payload.get("profile_policy")),
        mutation_policy=_mapping(payload.get("mutation_policy")),
        approval_policy=_mapping(payload.get("approval_policy")),
        tool_policy=_mapping(payload.get("tool_policy")),
        metadata=metadata,
        surface=str(metadata.get("surface") or surface),
        selected_skills=selected_skills,
        communication_policy=communication_policy,
        execution_policy=execution_policy,
        surface_policy=surface_policy,
        attachments=attachments,
        notify_completion=bool(metadata.get("tui_notify_completion", False)),
        plain=bool(metadata.get("tui_plain", False)),
    )
    overrides["metadata"]["gui_for"] = metadata.get("gui_for") or "dan super-tui"
    return overrides


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _mapping_sequence(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _string_sequence(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [str(item) for item in value if str(item).strip()]
