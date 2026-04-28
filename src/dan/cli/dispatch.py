"""Deterministic CLI-to-orchestrator dispatch choices."""

from __future__ import annotations

import re
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, Field

from dan.cli.main import _SUBCOMMANDS


_WEBSITE_INTENT_CUES = (
    "website",
    "landing page",
    "web page",
    "homepage",
    "static site",
    "html",
    "css",
)
_BUILD_INTENT_CUES = (
    "build",
    "implement",
    "code",
    "feature",
    "fix",
    "ship",
)
CODE_EXECUTION_FAMILIES = ("code", "code_plus_research")
AMBIGUOUS_OPERATOR_EXECUTION_FAMILIES = ("", "general_operator", "general operator")
_SUPER_DAN_WEBSITE_TOOL_IDS = (
    "list_directory",
    "file_read",
    "file_write",
    "file_edit",
)
_SUPER_DAN_GENERIC_TOOL_IDS = (
    "list_directory",
    "file_read",
    "workspace_check",
    "web_search",
    "file_write",
    "file_edit",
    "shell_command",
    "git_status",
    "git_diff",
    "git_log",
)
_SUPER_DAN_WEBSITE_FILES = ("index.html", "styles.css", "app.js", "README.md")
_SUPER_DAN_WEBSITE_TEMPLATE_PHRASES = (
    "execution contract",
    "objective contract",
    "capability and authority contract",
    "native execution lane",
    "acceptance synthesis",
    "super dan turns one objective into coordinated execution",
)
_SUPER_DAN_EXISTING_WEBSITE_PREFERRED_COORDINATED_FILES = 2


class IntentSignal(BaseModel):
    """Explainable operation/artifact signal consumed by deterministic dispatch."""

    operation: Literal["mutate", "read_only", "unknown"] = "unknown"
    artifact_target: str = "unspecified"
    mutation_permission: bool = False
    confidence: float = 0.0
    source: str = "deterministic"
    rationale: str = ""
    evidence: list[str] = Field(default_factory=list)


class OrchestratorChoice(BaseModel):
    """Typed deterministic dispatch decision."""

    orchestrator_id: str
    brief_composer: str
    sampling_policy: str = "deterministic"
    tool_policy: dict[str, Any] = Field(default_factory=dict)
    runtime_policy: dict[str, Any] = Field(default_factory=dict)
    organism_plan_template: str = ""
    artifact_policy: dict[str, Any] = Field(default_factory=dict)
    acceptance_policy: dict[str, Any] = Field(default_factory=dict)
    intent_signal: IntentSignal = Field(default_factory=IntentSignal)
    matched_cues: list[str] = Field(default_factory=list)
    rationale: str = ""


def _normalize(text: Any) -> str:
    return " ".join(str(text or "").lower().split())


def _command_from_context(context: Mapping[str, Any] | None) -> str:
    if not context:
        return ""
    for key in ("command", "subcommand", "cli_command", "surface"):
        value = _normalize(context.get(key))
        if value:
            return value.removeprefix("dan ").removeprefix("dan-")
    argv = context.get("argv")
    if isinstance(argv, (list, tuple)) and argv:
        for item in argv:
            value = _normalize(item)
            if value in _SUBCOMMANDS:
                return value
    return ""


def _matched_cues(text: str, cues: tuple[str, ...]) -> list[str]:
    normalized = _normalize(text)
    return [cue for cue in cues if cue in normalized]


def _execution_family(context: Mapping[str, Any] | None) -> str:
    if not context:
        return ""
    return _normalize(context.get("execution_family"))


def _existing_website_context(context: Mapping[str, Any] | None) -> bool:
    if not context:
        return False
    return bool(
        context.get("existing_website_workspace")
        or context.get("existing_website")
        or _normalize(context.get("workspace_kind")) == "website"
    )


def _explicit_intent_signal(context: Mapping[str, Any] | None) -> IntentSignal | None:
    if not context:
        return None
    raw_signal = context.get("intent_signal")
    if isinstance(raw_signal, IntentSignal):
        return raw_signal
    if isinstance(raw_signal, Mapping):
        return IntentSignal.model_validate(dict(raw_signal))
    operation = _normalize(context.get("operation") or context.get("intent_operation"))
    artifact_target = _normalize(
        context.get("artifact_target")
        or context.get("artifact_kind")
        or context.get("artifact_type")
    )
    mutation_permission = context.get("mutation_permission")
    if not operation and mutation_permission is None:
        return None
    normalized_operation = operation if operation in {"mutate", "read_only", "unknown"} else "unknown"
    resolved_permission = (
        bool(mutation_permission)
        if mutation_permission is not None
        else normalized_operation == "mutate"
    )
    return IntentSignal(
        operation=normalized_operation,
        artifact_target=artifact_target or "unspecified",
        mutation_permission=resolved_permission,
        confidence=float(context.get("intent_confidence") or 0.9),
        source=str(context.get("intent_source") or "context").strip() or "context",
        rationale=str(context.get("intent_rationale") or "explicit intent signal supplied by caller").strip(),
        evidence=[str(item) for item in list(context.get("intent_evidence") or [])],
    )


def _artifact_target(context: Mapping[str, Any] | None, website_cues: Sequence[str]) -> tuple[str, list[str]]:
    evidence: list[str] = []
    if context:
        explicit = _normalize(
            context.get("artifact_target")
            or context.get("artifact_kind")
            or context.get("artifact_type")
            or context.get("workspace_kind")
        )
        if explicit:
            evidence.append(f"artifact_context:{explicit}")
            if explicit in {"website", "site", "web", "public", "dist"}:
                return "website", evidence
            return explicit, evidence
    if _existing_website_context(context):
        evidence.append("existing_artifact:website")
        return "website", evidence
    if website_cues:
        evidence.extend(f"artifact_cue:{cue}" for cue in website_cues)
        return "website", evidence
    return "unspecified", evidence


def resolve_intent_signal(
    intent: str,
    context: Mapping[str, Any] | None = None,
) -> IntentSignal:
    """Resolve an explainable operation/artifact signal before lane selection."""

    explicit = _explicit_intent_signal(context)
    if explicit is not None:
        return explicit

    command = _command_from_context(context)
    text = _normalize(" ".join([intent, str((context or {}).get("intent", ""))]))
    website_cues = _matched_cues(text, _WEBSITE_INTENT_CUES)
    build_cues = _matched_cues(text, _BUILD_INTENT_CUES)
    execution_family = _execution_family(context)
    artifact_target, artifact_evidence = _artifact_target(context, website_cues)
    evidence: list[str] = []
    if command:
        evidence.append(f"command:{command}")
    if execution_family:
        evidence.append(f"execution_family:{execution_family}")
    evidence.extend(f"operation_cue:{cue}" for cue in build_cues)
    evidence.extend(artifact_evidence)

    if command in {"research", "read", "reader"}:
        return IntentSignal(
            operation="read_only",
            artifact_target=artifact_target,
            mutation_permission=False,
            confidence=0.95,
            rationale="explicit read-only CLI command",
            evidence=evidence,
        )
    if command == "code":
        return IntentSignal(
            operation="mutate",
            artifact_target="workspace",
            mutation_permission=True,
            confidence=0.95,
            rationale="explicit coding CLI command",
            evidence=evidence,
        )

    if execution_family in CODE_EXECUTION_FAMILIES:
        return IntentSignal(
            operation="mutate",
            artifact_target=artifact_target if artifact_target != "unspecified" else "workspace",
            mutation_permission=True,
            confidence=0.86,
            rationale="code execution family permits workspace mutation",
            evidence=evidence,
        )
    if execution_family and execution_family not in AMBIGUOUS_OPERATOR_EXECUTION_FAMILIES:
        return IntentSignal(
            operation="read_only",
            artifact_target=artifact_target,
            mutation_permission=False,
            confidence=0.82,
            rationale="non-code execution family is kept read-only by policy",
            evidence=evidence,
        )
    if build_cues:
        return IntentSignal(
            operation="mutate",
            artifact_target=artifact_target if artifact_target != "unspecified" else "workspace",
            mutation_permission=True,
            confidence=0.72,
            rationale="deterministic mutation cue found in operator objective",
            evidence=evidence,
        )
    if artifact_target != "unspecified":
        return IntentSignal(
            operation="mutate",
            artifact_target=artifact_target,
            mutation_permission=True,
            confidence=0.64,
            rationale="existing artifact context permits an ambiguous operator turn to patch that artifact",
            evidence=evidence,
        )
    return IntentSignal(
        operation="unknown",
        artifact_target=artifact_target,
        mutation_permission=False,
        confidence=0.35,
        rationale="no mutation or artifact signal resolved",
        evidence=evidence,
    )


def _choice(
    *,
    orchestrator_id: str,
    brief_composer: str,
    plan_template: str,
    rationale: str,
    matched_cues: list[str] | None = None,
    sampling_policy: str = "deterministic",
    tool_policy: Mapping[str, Any] | None = None,
    runtime_policy: Mapping[str, Any] | None = None,
    artifact_policy: Mapping[str, Any] | None = None,
    acceptance_policy: Mapping[str, Any] | None = None,
    intent_signal: IntentSignal | Mapping[str, Any] | None = None,
) -> OrchestratorChoice:
    if intent_signal is None:
        resolved_intent_signal = IntentSignal()
    elif isinstance(intent_signal, IntentSignal):
        resolved_intent_signal = intent_signal
    else:
        resolved_intent_signal = IntentSignal.model_validate(dict(intent_signal))
    return OrchestratorChoice(
        orchestrator_id=orchestrator_id,
        brief_composer=brief_composer,
        sampling_policy=sampling_policy,
        tool_policy=dict(tool_policy or {}),
        runtime_policy=dict(runtime_policy or {}),
        organism_plan_template=plan_template,
        artifact_policy=dict(artifact_policy or {}),
        acceptance_policy=dict(acceptance_policy or {}),
        intent_signal=resolved_intent_signal,
        matched_cues=list(matched_cues or []),
        rationale=rationale,
    )


def select_orchestrator(intent: str, context: Mapping[str, Any] | None = None) -> OrchestratorChoice:
    """Return the deterministic brief composer and plan template for a CLI intent."""

    command = _command_from_context(context)
    text = _normalize(" ".join([intent, str((context or {}).get("intent", ""))]))
    signal_context = context
    if command == "super-organism":
        signal_context = dict(context or {})
        signal_context.pop("execution_family", None)
    intent_signal = resolve_intent_signal(intent, signal_context)
    if command in ("read", "reader"):
        return _choice(
            orchestrator_id="dan-reader",
            brief_composer="templates.research_brief",
            plan_template="document-reader",
            rationale="reader command maps to document research/review brief",
            tool_policy={"mode": "read-only"},
            intent_signal=intent_signal,
        )
    if command == "research":
        return _choice(
            orchestrator_id="dan-research",
            brief_composer="templates.research_brief",
            plan_template="deep-research",
            rationale="research command maps to evidence-grounded research organism",
            tool_policy={"mode": "read-only-with-retrieval"},
            intent_signal=intent_signal,
        )
    if command == "code":
        return _choice(
            orchestrator_id="dan-code",
            brief_composer="templates.coding_brief",
            plan_template="coding",
            rationale="code command maps to coding brief composer",
            sampling_policy="creative",
            tool_policy={"mode": "workspace-mutation"},
            intent_signal=intent_signal,
        )
    if command == "organism":
        return _choice(
            orchestrator_id="dan-reference-organism",
            brief_composer="templates.role_brief",
            plan_template="reference-project-execution",
            rationale="organism command maps to the reference project-execution brief graph",
            intent_signal=intent_signal,
        )
    if command == "super-organism":
        website_cues = _matched_cues(text, _WEBSITE_INTENT_CUES)
        build_cues = _matched_cues(text, _BUILD_INTENT_CUES)
        if intent_signal.operation != "mutate" or not intent_signal.mutation_permission:
            return _choice(
                orchestrator_id="super-dan-showcase",
                brief_composer="templates.role_brief",
                plan_template="super-dan-showcase",
                rationale="super-organism intent signal is not currently live-executable",
                matched_cues=website_cues + build_cues,
                tool_policy={"mode": "read-only"},
                acceptance_policy={"requires_live_artifact": False},
                intent_signal=intent_signal,
            )
        return _choice(
            orchestrator_id="super-dan-live-general",
            brief_composer="templates.role_brief",
            plan_template="super-dan-general-workspace",
            rationale="super-organism intent signal maps to generic workspace deliverable policy",
            matched_cues=website_cues + build_cues,
            sampling_policy="creative",
            tool_policy={
                "mode": "workspace-mutation",
                "profile": "generic",
                "allowed_tool_ids": list(_SUPER_DAN_GENERIC_TOOL_IDS),
                "preferred_tool_ids": [
                    "list_directory",
                    "web_search",
                    "file_read",
                    "file_edit",
                    "file_write",
                    "git_diff",
                    "shell_command",
                ],
            },
            acceptance_policy={"requires_live_artifact": True},
            intent_signal=intent_signal,
        )
    if command in _SUBCOMMANDS:
        return _choice(
            orchestrator_id=f"dan-{re.sub(r'[^a-z0-9-]+', '-', command)}",
            brief_composer="templates.role_brief",
            plan_template=command,
            rationale="known CLI command has no specialized Plan 56 brief composer yet",
            intent_signal=intent_signal,
        )
    website_cues = _matched_cues(text, _WEBSITE_INTENT_CUES)
    if website_cues:
        return _choice(
            orchestrator_id="super-dan-live-website",
            brief_composer="templates.coding_brief",
            plan_template="super-dan-website",
            rationale="website cues select the website brief composer without an LLM classifier",
            matched_cues=website_cues,
            sampling_policy="creative",
            tool_policy={
                "mode": "workspace-mutation",
                "profile": "website",
                "allowed_tool_ids": list(_SUPER_DAN_WEBSITE_TOOL_IDS),
                "preferred_tool_ids": ["file_write", "file_edit", "file_read", "list_directory"],
            },
            artifact_policy={
                "required_files": list(_SUPER_DAN_WEBSITE_FILES),
                "existing_website_preferred_coordinated_files": _SUPER_DAN_EXISTING_WEBSITE_PREFERRED_COORDINATED_FILES,
            },
            acceptance_policy={
                "requires_live_artifact": True,
                "template_phrases": list(_SUPER_DAN_WEBSITE_TEMPLATE_PHRASES),
            },
            intent_signal=intent_signal,
        )
    return _choice(
        orchestrator_id="dan-universal",
        brief_composer="templates.role_brief",
        plan_template="generic",
        rationale="fallback deterministic universal-organism dispatch",
        intent_signal=intent_signal,
    )


__all__ = [
    "IntentSignal",
    "OrchestratorChoice",
    "resolve_intent_signal",
    "select_orchestrator",
]
