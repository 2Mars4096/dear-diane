"""Deterministic CLI-to-orchestrator dispatch choices."""

from __future__ import annotations

import re
from typing import Any, Mapping

from pydantic import BaseModel, Field

from dan.cli.main import _SUBCOMMANDS


WEBSITE_INTENT_CUES = (
    "website",
    "landing page",
    "web page",
    "homepage",
    "static site",
    "html",
    "css",
)
BUILD_INTENT_CUES = (
    "build",
    "implement",
    "code",
    "feature",
    "fix",
    "ship",
)
CODE_EXECUTION_FAMILIES = ("code", "code_plus_research")
SUPER_DAN_WEBSITE_TOOL_IDS = (
    "list_directory",
    "file_read",
    "file_write",
    "file_edit",
)
SUPER_DAN_GENERIC_TOOL_IDS = (
    "list_directory",
    "file_read",
    "file_write",
    "file_edit",
    "shell_command",
    "git_status",
    "git_diff",
    "git_log",
)
SUPER_DAN_WEBSITE_FILES = ("index.html", "styles.css", "app.js", "README.md")
SUPER_DAN_WEBSITE_TEMPLATE_PHRASES = (
    "execution contract",
    "objective contract",
    "capability and authority contract",
    "native execution lane",
    "acceptance synthesis",
    "super dan turns one objective into coordinated execution",
)
SUPER_DAN_EXISTING_WEBSITE_MIN_CHANGED_FILES = 2


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
) -> OrchestratorChoice:
    return OrchestratorChoice(
        orchestrator_id=orchestrator_id,
        brief_composer=brief_composer,
        sampling_policy=sampling_policy,
        tool_policy=dict(tool_policy or {}),
        runtime_policy=dict(runtime_policy or {}),
        organism_plan_template=plan_template,
        artifact_policy=dict(artifact_policy or {}),
        acceptance_policy=dict(acceptance_policy or {}),
        matched_cues=list(matched_cues or []),
        rationale=rationale,
    )


def select_orchestrator(intent: str, context: Mapping[str, Any] | None = None) -> OrchestratorChoice:
    """Return the deterministic brief composer and plan template for a CLI intent."""

    command = _command_from_context(context)
    text = _normalize(" ".join([intent, str((context or {}).get("intent", ""))]))
    if command in ("read", "reader"):
        return _choice(
            orchestrator_id="dan-reader",
            brief_composer="templates.research_brief",
            plan_template="document-reader",
            rationale="reader command maps to document research/review brief",
            tool_policy={"mode": "read-only"},
        )
    if command == "research":
        return _choice(
            orchestrator_id="dan-research",
            brief_composer="templates.research_brief",
            plan_template="deep-research",
            rationale="research command maps to evidence-grounded research organism",
            tool_policy={"mode": "read-only-with-retrieval"},
        )
    if command == "code":
        return _choice(
            orchestrator_id="dan-code",
            brief_composer="templates.coding_brief",
            plan_template="coding",
            rationale="code command maps to coding brief composer",
            sampling_policy="creative",
            tool_policy={"mode": "workspace-mutation"},
        )
    if command == "organism":
        return _choice(
            orchestrator_id="dan-reference-organism",
            brief_composer="templates.role_brief",
            plan_template="reference-project-execution",
            rationale="organism command maps to the reference project-execution brief graph",
        )
    if command == "super-organism":
        website_cues = _matched_cues(text, WEBSITE_INTENT_CUES)
        build_cues = _matched_cues(text, BUILD_INTENT_CUES)
        execution_family = _execution_family(context)
        code_family = not execution_family or execution_family in CODE_EXECUTION_FAMILIES
        if execution_family and not code_family:
            return _choice(
                orchestrator_id="super-dan-showcase",
                brief_composer="templates.role_brief",
                plan_template="super-dan-showcase",
                rationale="super-organism objective family is not currently live-executable",
                matched_cues=website_cues + build_cues,
                tool_policy={"mode": "read-only"},
                acceptance_policy={"requires_live_artifact": False},
            )
        if website_cues and code_family:
            return _choice(
                orchestrator_id="super-dan-live-website",
                brief_composer="templates.coding_brief",
                plan_template="super-dan-website",
                rationale="super-organism website/build cues select website artifact policy",
                matched_cues=website_cues + build_cues,
                sampling_policy="creative",
                tool_policy={
                    "mode": "workspace-mutation",
                    "profile": "website",
                    "allowed_tool_ids": list(SUPER_DAN_WEBSITE_TOOL_IDS),
                    "preferred_tool_ids": ["file_write", "file_edit", "file_read", "list_directory"],
                },
                artifact_policy={
                    "required_files": list(SUPER_DAN_WEBSITE_FILES),
                    "existing_website_min_changed_files": SUPER_DAN_EXISTING_WEBSITE_MIN_CHANGED_FILES,
                },
                acceptance_policy={
                    "requires_live_artifact": True,
                    "template_phrases": list(SUPER_DAN_WEBSITE_TEMPLATE_PHRASES),
                },
            )
        return _choice(
            orchestrator_id="super-dan-live-coding",
            brief_composer="templates.coding_brief",
            plan_template="super-dan-coding",
            rationale="super-organism generic build intent maps to coding artifact policy",
            matched_cues=build_cues,
            sampling_policy="creative",
            tool_policy={
                "mode": "workspace-mutation",
                "profile": "generic",
                "allowed_tool_ids": list(SUPER_DAN_GENERIC_TOOL_IDS),
                "preferred_tool_ids": [
                    "list_directory",
                    "file_read",
                    "file_edit",
                    "file_write",
                    "git_diff",
                    "shell_command",
                ],
            },
            acceptance_policy={"requires_live_artifact": True},
        )
    if command in _SUBCOMMANDS:
        return _choice(
            orchestrator_id=f"dan-{re.sub(r'[^a-z0-9-]+', '-', command)}",
            brief_composer="templates.role_brief",
            plan_template=command,
            rationale="known CLI command has no specialized Plan 56 brief composer yet",
        )
    website_cues = _matched_cues(text, WEBSITE_INTENT_CUES)
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
                "allowed_tool_ids": list(SUPER_DAN_WEBSITE_TOOL_IDS),
                "preferred_tool_ids": ["file_write", "file_edit", "file_read", "list_directory"],
            },
            artifact_policy={
                "required_files": list(SUPER_DAN_WEBSITE_FILES),
                "existing_website_min_changed_files": SUPER_DAN_EXISTING_WEBSITE_MIN_CHANGED_FILES,
            },
            acceptance_policy={
                "requires_live_artifact": True,
                "template_phrases": list(SUPER_DAN_WEBSITE_TEMPLATE_PHRASES),
            },
        )
    return _choice(
        orchestrator_id="dan-universal",
        brief_composer="templates.role_brief",
        plan_template="generic",
        rationale="fallback deterministic universal-organism dispatch",
    )


__all__ = [
    "BUILD_INTENT_CUES",
    "CODE_EXECUTION_FAMILIES",
    "OrchestratorChoice",
    "SUPER_DAN_EXISTING_WEBSITE_MIN_CHANGED_FILES",
    "SUPER_DAN_GENERIC_TOOL_IDS",
    "SUPER_DAN_WEBSITE_FILES",
    "SUPER_DAN_WEBSITE_TEMPLATE_PHRASES",
    "SUPER_DAN_WEBSITE_TOOL_IDS",
    "WEBSITE_INTENT_CUES",
    "select_orchestrator",
]
