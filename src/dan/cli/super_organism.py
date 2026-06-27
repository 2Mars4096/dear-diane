"""dan-super-organism — Super DAN organism showcase and live executor."""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import inspect
import json
import os
import re
import shutil
import sys
from collections.abc import Mapping as MappingABC
from collections.abc import Sequence as SequenceABC
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from dan.cli import load_env, normalize_workspace_root, resolve_config
from dan.cli import live_gateway
from dan.cli.dispatch import OrchestratorChoice, select_orchestrator
from dan.cli.super_hooks import (
    SuperHookRuntime,
    format_super_queue_status,
)
from dan.providers import LLMProvider
from dan.skills import invocation as skill_invocation
from dan.server.runtime_config import build_engine_config_from_env
from dan.worker.brief import RoleSpec, WorkerBrief, request_from_brief
from dan.worker.cell import build_cell
from dan.worker.contracts import snippets
from dan.worker.contracts.templates import coding_brief, review_brief, role_brief
from dan.worker.core.contracts import ExecutionRequest, OutputContract
from dan.worker.core.executor import WorkerCoreExecutor
from dan.worker.core.interfaces import CallbackEventSink
from dan.worker.core.model import WorkerDefinition
from dan.worker.organism_log import (
    ORGANISM_LOG_SCHEMA_VERSION,
    OrganismLogContext,
    OrganismLogWriter,
    new_trace_id,
)
from dan.worker.organisms.local_runtime import (
    LocalOrganismToolRuntime,
    ToolLoopCompletionProvider,
)
from dan.worker.organisms.super_organism import (
    DEFAULT_SUPER_ORGANISM_ACTIVE_CELL_CAP,
    DEFAULT_SUPER_ORGANISM_CELL_COUNT,
    DEFAULT_SUPER_ORGANISM_ID,
    DEFAULT_SUPER_ORGANISM_TARGET,
    SuperOrganismReport,
    run_super_organism_demo,
)
from dan.worker.structured_payload import parse_jsonish_payload


_GENERIC_SNAPSHOT_SKIP_DIR_NAMES = frozenset(
    {
        ".dan-super",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
    }
)
_SUPER_DAN_WORKER_MAX_TOKENS = 64_000
_SUPER_DAN_REPAIR_MAX_TOKENS = 64_000
_SUPER_DAN_VALIDATOR_MAX_TOKENS = 12_000
_SUPER_DAN_PLANNER_MAX_TOKENS = 16_000
_SUPER_DAN_PLAN_VALIDATOR_MAX_TOKENS = 8_000
_SUPER_DAN_GENERIC_BUILDER_RETRY_ATTEMPTS = 2
_SUPER_DAN_PLAN_CHECKBOX_RE = re.compile(r"^(\s*)-\s*\[([ xX])\]\s*(.+?)\s*$")
_GENERIC_ALIAS_TEXT_EXTENSIONS = frozenset(
    {
        ".css",
        ".csv",
        ".html",
        ".js",
        ".json",
        ".md",
        ".py",
        ".txt",
    }
)


def _live_execution_policy_payload(args: argparse.Namespace) -> dict[str, Any]:
    raw = getattr(args, "_tui_execution_policy", None)
    if isinstance(raw, Mapping):
        return dict(raw)
    return {}


def _live_execution_policy_int(
    args: argparse.Namespace,
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    payload = _live_execution_policy_payload(args)
    try:
        value = int(payload.get(key))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _live_max_auto_fix_rounds(args: argparse.Namespace, *, default: int = 1) -> int:
    payload = _live_execution_policy_payload(args)
    if payload and payload.get("allow_repair_cycles") is False:
        return 0
    return _live_execution_policy_int(
        args,
        "max_auto_fix_rounds",
        default=default,
        minimum=0,
        maximum=8,
    )


def _live_max_validation_cycles(args: argparse.Namespace, *, default: int = 2) -> int:
    return _live_execution_policy_int(
        args,
        "max_validation_cycles",
        default=default,
        minimum=1,
        maximum=12,
    )


def _live_repair_loop_limit(args: argparse.Namespace, *, default: int = 1) -> int:
    validation_room = max(0, _live_max_validation_cycles(args, default=default + 1) - 1)
    return min(_live_max_auto_fix_rounds(args, default=default), validation_room)


def _live_max_work_seconds(args: argparse.Namespace) -> float | None:
    payload = _live_execution_policy_payload(args)
    if not payload:
        return None
    raw = payload.get("max_work_seconds")
    if raw is None or raw == "":
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return max(0.01, min(3600.0, seconds))


def _live_max_builder_retry_attempts(args: argparse.Namespace) -> int:
    payload = _live_execution_policy_payload(args)
    if not payload:
        return _SUPER_DAN_GENERIC_BUILDER_RETRY_ATTEMPTS
    return _live_max_auto_fix_rounds(args, default=_SUPER_DAN_GENERIC_BUILDER_RETRY_ATTEMPTS)


def _live_answer_recovery_loop_limit(args: argparse.Namespace) -> int:
    return _live_max_auto_fix_rounds(args, default=2)


_SUPER_DAN_CAPABILITY_POINTS: tuple[str, ...] = (
    "You are one execution cell inside Super DAN, a private multi-agent organism; do not assume the model already knows this project.",
    "The organism can inspect workspace files, search when available, edit or write artifacts, validate results, repair failed candidates, and pass compact evidence between organs.",
    "Your job is to contribute a concrete, inspectable step toward the operator objective, not to behave like a generic standalone chatbot.",
)

_SUPER_DAN_TOOL_DESCRIPTIONS: dict[str, str] = {
    "list_directory": "discover workspace structure and candidate artifact paths",
    "file_read": "inspect exact file content before making grounded decisions",
    "file_edit": "make targeted edits to existing files",
    "file_write": "create new files or coherent full-file artifacts when appropriate",
    "workspace_check": "run deterministic existence, count, syntax, or structure checks",
    "shell_command": (
        "run real terminal commands in the workspace for operations that are naturally command-line work: "
        "tests/builds/scripts, project CLIs, command availability checks, faithful filesystem operations "
        "such as mkdir/cp/mv/rsync/find/du/wc/checksums/archive commands, and focused verification. "
        "Think of it as the local platform's command-line toolbox: when a task sounds terminal-native, "
        "actively choose the existing CLI, project script, Python one-liner, or POSIX utility that does it best. "
        "Prefer structured file tools for small precise reads/edits; prefer shell_command when the OS "
        "can copy, move, enumerate, verify, or execute more faithfully than reconstructing text through file tools"
    ),
    "git_status": "inspect changed workspace state without mutating it",
    "git_diff": "inspect concrete before/after workspace changes",
    "git_log": "inspect recent repository history when relevant",
    "web_search": "retrieve current external facts when the objective depends on freshness",
    "browser_tabs": "inspect current persistent browser tabs before taking browser actions",
    "browser_inspect": "inspect current browser URL/title/tabs, optional HTML, and structured interactive element metadata",
    "browser_open": "navigate a persistent browser session to a URL when browser state/auth matters",
    "browser_wait": "wait for browser page load, network idle, or a specific selector",
    "browser_extract": "read visible browser page text or a selected DOM element",
    "browser_screenshot": "capture the current browser page as local visual evidence",
    "browser_click": "click a browser DOM element by CSS selector after grounding the page state",
    "browser_fill": "replace text in a browser input or contenteditable element",
    "browser_type": "append typed text into a browser element",
    "browser_select": "select an option in a browser dropdown by CSS selector and value",
    "browser_download": "download authenticated browser artifacts through the current browser session",
    "desktop_observe": "capture local desktop screenshot/OCR/window evidence when desktop UI work is explicitly needed",
    "desktop_focus": "activate a desktop app/window before a desktop UI action",
    "desktop_click": "click grounded desktop coordinates after observing the target UI",
    "desktop_type": "type text into the focused desktop app when browser/workspace tools cannot reach the UI",
    "desktop_hotkey": "press a keyboard shortcut in the focused desktop app",
}

_SUPER_DAN_STAGE_RULE_INSTRUCTIONS: dict[str, str] = {
    "planner": (
        "Generate the planning checks from this operator request, the active intent policy, available tools, "
        "and workspace evidence already seen. Keep only checks that would change the next action; do not reuse "
        "a fixed stage checklist."
    ),
    "plan_validator": (
        "Generate validation checks for the emitted plan from its actual structure, identifiers, dependencies, "
        "and promised deliverables. Keep deterministic format constraints, but make the substantive review "
        "specific to this plan."
    ),
    "builder": (
        "Generate execution checks from the request-specific completion contract before acting. Decide whether "
        "the operator needs an in-session answer, an edit, a saved artifact, or an explicit blocker; do not turn "
        "answer-only review/summary work into file edits."
    ),
    "builder_retry": (
        "Generate retry checks from the previous failure evidence and the operator's actual target. The next "
        "attempt should repair the specific gap or report a blocker, not follow a generic retry recipe."
    ),
    "validator": (
        "Generate validation checks from the original request, the current frontier, and concrete evidence from "
        "the run. Judge only the work that was actually due now, and make remaining-work notes explicit."
    ),
    "repair": (
        "Generate repair checks from the validator's concrete findings and the original operator contract. Prefer "
        "the smallest honest repair, or report the blocker when repair is not currently justified."
    ),
}


@dataclass(frozen=True)
class OperatorIntentPolicy:
    """Structured operator constraints that must survive prompt/runtime boundaries."""

    active: bool = False
    work_mode: str = "workspace_change"
    mutation_policy: str = "required"
    evidence_policy: str = "reads_and_checks"
    allow_workspace_mutation: bool = True
    target_artifacts: tuple[str, ...] = ()
    allowed_read_paths: tuple[str, ...] = ()
    allowed_write_paths: tuple[str, ...] = ()
    forbid_other_workspace_inputs: bool = False
    allow_directory_listing: bool = True
    allow_git_context: bool = True
    allow_shell_command: bool = True
    allow_existing_artifact_reuse: bool = True
    source_scope: str = "workspace_allowed"
    constraints: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "active": bool(self.active),
            "work_mode": self.work_mode,
            "mutation_policy": self.mutation_policy,
            "evidence_policy": self.evidence_policy,
            "work_contract": {
                "work_mode": self.work_mode,
                "mutation_policy": self.mutation_policy,
                "evidence_policy": self.evidence_policy,
                "source_tracking": [
                    "files_read",
                    "links_opened",
                    "commands_run",
                    "checks_performed",
                ],
                "change_tracking": [
                    "files_created",
                    "files_edited",
                    "files_deleted",
                    "generated_artifacts",
                ],
                "final_response_policy": "human_readable_synthesis",
            },
            "allow_workspace_mutation": bool(self.allow_workspace_mutation),
            "target_artifacts": list(self.target_artifacts),
            "allowed_read_paths": list(self.allowed_read_paths),
            "allowed_write_paths": list(self.allowed_write_paths),
            "forbid_other_workspace_inputs": bool(self.forbid_other_workspace_inputs),
            "allow_directory_listing": bool(self.allow_directory_listing),
            "allow_git_context": bool(self.allow_git_context),
            "allow_shell_command": bool(self.allow_shell_command),
            "allow_existing_artifact_reuse": bool(self.allow_existing_artifact_reuse),
            "source_scope": self.source_scope,
            "constraints": list(self.constraints),
        }


def _live_pacing_policy(*, forbid_scratch_files: bool = False) -> dict[str, Any]:
    policy: dict[str, Any] = {
        "prefer_file_edit": True,
        "avoid_scratch_files": True,
        "cadence": (
            "land one coherent valid slice before attempting the next; use larger direct writes when they are "
            "the clearest way to create a new requested artifact, and downshift only after a real truncation or "
            "tool-shape failure"
        ),
    }
    if forbid_scratch_files:
        policy["avoid_scratch_files"] = True
    return policy


def _live_pacing_contract(policy: Mapping[str, Any] | None = None) -> str:
    return snippets.pacing_contract(policy or _live_pacing_policy())


def _bullet_section(title: str, items: Sequence[str]) -> str:
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    if not cleaned:
        return ""
    return title.rstrip(":") + ":\n" + "\n".join(f"- {item}" for item in cleaned)


def _super_dan_identity_snippet(extra_points: Sequence[str] | None = None) -> str:
    return _bullet_section(
        "Super DAN organism context",
        [*_SUPER_DAN_CAPABILITY_POINTS, *list(extra_points or [])],
    )


def _tool_guide_snippet(
    tool_ids: Sequence[str],
    *,
    extra_descriptions: Mapping[str, str] | None = None,
) -> str:
    descriptions = {**_SUPER_DAN_TOOL_DESCRIPTIONS, **dict(extra_descriptions or {})}
    seen: set[str] = set()
    lines: list[str] = []
    for raw_tool_id in tool_ids:
        tool_id = str(raw_tool_id).strip()
        if not tool_id or tool_id in seen:
            continue
        seen.add(tool_id)
        description = descriptions.get(tool_id, "use only when it directly supports the current stage")
        lines.append(f"`{tool_id}`: {description}.")
    return _bullet_section("Available tool guide", lines)


def _stage_questions_snippet(
    stage: str,
    *,
    extra_questions: Sequence[str] | None = None,
) -> str:
    instruction = _SUPER_DAN_STAGE_RULE_INSTRUCTIONS.get(
        stage,
        (
            "Generate request-specific checks from the operator request, active policy, available tools, "
            "and evidence already collected. Do not reuse a fixed stage checklist."
        ),
    )
    generated_rule = (
        "Write any needed who/what/where/how/quality/evidence gates in your own words for this request, "
        "omit gates that do not affect completion, and do not reuse a fixed stage checklist."
    )
    deterministic_gates = [
        f"Deterministic gate: {question}" for question in list(extra_questions or [])
    ]
    return _bullet_section(
        "Request-specific checks to generate",
        [instruction, generated_rule, *deterministic_gates],
    )


def _workspace_boundary_snippet() -> str:
    return (
        "Workspace boundary policy: default to the current workspace root. Read or write outside it only when "
        "the operator explicitly names an external path/target and the runtime policy permits it. Never use "
        "destructive git reset, checkout, or rm-style cleanup unless the operator specifically requests it."
    )


def _super_dan_stage_snippets(
    stage: str,
    *,
    tool_ids: Sequence[str] = (),
    extra_questions: Sequence[str] | None = None,
    extra_identity_points: Sequence[str] | None = None,
) -> list[str]:
    rendered = [
        _super_dan_identity_snippet(extra_identity_points),
        _workspace_boundary_snippet(),
        _tool_guide_snippet(tool_ids) if tool_ids else "",
        _stage_questions_snippet(stage, extra_questions=extra_questions),
    ]
    return [item for item in rendered if item.strip()]


_SUPER_DAN_SKILL_CONTENT_LIMIT = 3_500
_SUPER_DAN_EXPLICIT_SKILL_REFERENCE_LIMIT = 4_000
_SUPER_DAN_EXPLICIT_SKILL_REFERENCE_FILE_LIMIT = 4
_SUPER_DAN_MAX_AUTO_SKILLS = 3
_SUPER_DAN_SKILL_STOPWORDS = frozenset(
    {
        "about",
        "agent",
        "asks",
        "build",
        "create",
        "default",
        "file",
        "files",
        "from",
        "have",
        "into",
        "local",
        "make",
        "need",
        "project",
        "requested",
        "super",
        "task",
        "that",
        "this",
        "tool",
        "tools",
        "user",
        "when",
        "with",
        "workflow",
    }
)


def _skill_tokenize(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_.-]{2,}", str(text or "").lower())
        if token not in _SUPER_DAN_SKILL_STOPWORDS
    }


def _load_super_dan_skill_catalog(workspace_root: str) -> list[dict[str, Any]]:
    """Load DAN, Codex, Claude, and Cursor skills as advisory Super DAN packets."""

    return skill_invocation.load_skill_catalog(workspace_root)


def _super_dan_skill_token(value: Mapping[str, Any]) -> str:
    return skill_invocation.skill_token(value)


def _super_dan_skill_catalog_by_token(workspace_root: str) -> dict[str, dict[str, Any]]:
    return skill_invocation.catalog_by_token(_load_super_dan_skill_catalog(workspace_root))


def _parse_super_dan_skill_invocation_text(
    text: str,
    *,
    workspace_root: str,
) -> skill_invocation.SkillInvocationParse:
    """Parse leading ``$skill-name`` invocations for all Super DAN surfaces."""

    return skill_invocation.parse_skill_invocation_text(
        text,
        catalog=_load_super_dan_skill_catalog(workspace_root),
        workspace_root=workspace_root,
        browse_hint="Pick a full skill name",
    )


def _prepare_super_dan_skill_invocation_args(
    args: argparse.Namespace,
    *,
    workspace_root: Path | str | None = None,
    source: str = "cli",
) -> skill_invocation.SkillInvocationParse:
    root = str(workspace_root or normalize_workspace_root(str(getattr(args, "workspace", "."))))
    parsed = skill_invocation.prepare_skill_invocation_args(
        args,
        catalog=_load_super_dan_skill_catalog(root),
        workspace_root=root,
        source=source,
        browse_hint="Pick a full skill name",
    )
    if source == "tui" and parsed.selected_tokens:
        setattr(args, "_tui_selected_skill_mentions", list(parsed.selected_tokens))
    return parsed


def _super_dan_skill_bonus(skill_id: str, text: str) -> int:
    skill_id = skill_id.replace("-", "_")
    lowered = text.lower()
    bonuses = {
        "skill_creation": ["skill", "skills", "skill.md", "codex skill", "claude skill", "cursor skill"],
        "frontend_design": ["frontend", "website", "html", "css", "ui", "landing page", "web app"],
        "frontend_vibe": ["frontend", "website", "ui", "redesign", "dashboard", "app shell"],
        "web_artifacts_builder": ["react", "tailwind", "shadcn", "html artifact"],
        "theme_factory": ["theme", "styling", "colors", "visual style"],
        "brand_guidelines": ["brand", "branding", "anthropic"],
        "scientific_writer": ["paper", "manuscript", "literature review", "latex", "academic"],
        "paper_reader": ["paper", "pdf", "article", "literature review"],
        "paper_review": ["paper review", "referee", "review report", "manuscript"],
        "code_review": ["code review", "review diff", "pr review", "pull request"],
        "kaggle_scaffold": ["kaggle", "competition", "leaderboard", "oof"],
        "kaggle_project": ["kaggle", "competition", "leaderboard", "oof"],
        "beamer": ["beamer", "slides", "slide deck", "presentation"],
        "proposal": ["proposal", "research idea", "hypothesis"],
        "overleaf_agent": ["overleaf"],
    }
    return sum(3 for phrase in bonuses.get(skill_id, []) if phrase in lowered)


def _select_super_dan_skills(brief: WorkerBrief) -> list[dict[str, Any]]:
    payload = dict(brief.input_payload)
    metadata = dict(brief.metadata)
    workspace_root = str(payload.get("workspace_root") or metadata.get("workspace_root") or "")
    objective_text = " ".join(
        str(value or "")
        for value in [
            payload.get("objective"),
            payload.get("original_objective"),
            brief.task,
            brief.role.role_label,
            brief.role.responsibility,
        ]
    )
    query_tokens = _skill_tokenize(objective_text)
    if not objective_text.strip() or not query_tokens:
        return []

    scored: list[tuple[int, dict[str, Any]]] = []
    for skill in _load_super_dan_skill_catalog(workspace_root):
        name = str(skill.get("name") or "")
        skill_id = str(skill.get("id") or name).replace("-", "_")
        searchable = " ".join(
            [
                skill_id.replace("_", " "),
                name,
                str(skill.get("description") or ""),
                " ".join(str(tag) for tag in skill.get("tags") or []),
            ]
        )
        skill_tokens = _skill_tokenize(searchable)
        overlap = len(query_tokens & skill_tokens)
        exact = 4 if skill_id.replace("_", "-") in objective_text.lower() or skill_id in objective_text.lower() else 0
        score = overlap + exact + _super_dan_skill_bonus(skill_id, objective_text)
        if score >= 3:
            scored.append((score, skill))

    scored.sort(key=lambda item: (-item[0], str(item[1].get("id") or "")))
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for score, skill in scored:
        skill_id = str(skill.get("id") or skill.get("name") or "")
        if not skill_id or skill_id in seen:
            continue
        item = dict(skill)
        item["match_score"] = score
        selected.append(item)
        seen.add(skill_id)
        if len(selected) >= _SUPER_DAN_MAX_AUTO_SKILLS:
            break
    return selected


def _super_dan_skill_lookup_keys(value: Any) -> set[str]:
    return skill_invocation.skill_lookup_keys(value)


def _coerce_explicit_super_dan_skill_values(value: Any) -> list[str]:
    return skill_invocation.coerce_skill_values(value)


def _explicit_super_dan_skill_values(brief: WorkerBrief) -> list[str]:
    payload = dict(brief.input_payload)
    metadata = dict(brief.metadata)
    selected: list[str] = []
    seen: set[str] = set()
    for source in (metadata, payload):
        for key in (
            "explicit_skill_ids",
            "explicit_skills",
            "selected_skill_ids",
            "selected_skill_mentions",
            "tui_selected_skill_mentions",
            "_tui_selected_skill_mentions",
        ):
            for value in _coerce_explicit_super_dan_skill_values(source.get(key)):
                normalized = value.lower().lstrip("$")
                if normalized and normalized not in seen:
                    selected.append(normalized)
                    seen.add(normalized)
    return selected


def _select_explicit_super_dan_skills(brief: WorkerBrief) -> list[dict[str, Any]]:
    requested = _explicit_super_dan_skill_values(brief)
    if not requested:
        return []
    payload = dict(brief.input_payload)
    metadata = dict(brief.metadata)
    workspace_root = str(payload.get("workspace_root") or metadata.get("workspace_root") or "")
    return skill_invocation.select_explicit_skill_items(
        requested,
        _load_super_dan_skill_catalog(workspace_root),
        limit=_SUPER_DAN_MAX_AUTO_SKILLS,
    )


def _render_super_dan_skill_snippet(skill: Mapping[str, Any]) -> str:
    return skill_invocation.render_skill_packet(
        skill,
        content_limit=_SUPER_DAN_SKILL_CONTENT_LIMIT,
        reference_limit=_SUPER_DAN_EXPLICIT_SKILL_REFERENCE_LIMIT,
        reference_file_limit=_SUPER_DAN_EXPLICIT_SKILL_REFERENCE_FILE_LIMIT,
        sha256_prefix=lambda text: hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
    )


def _super_dan_skill_dir(skill: Mapping[str, Any]) -> Path | None:
    return skill_invocation.skill_dir(skill)


def _selected_super_dan_skill_catalog_items(
    workspace_root: Path,
    tokens: Sequence[str],
) -> list[dict[str, Any]]:
    return skill_invocation.selected_catalog_items(
        _load_super_dan_skill_catalog(str(workspace_root)),
        tokens,
    )


def _selected_super_dan_skill_script_candidates(skill: Mapping[str, Any]) -> list[str]:
    return skill_invocation.selected_skill_script_candidates(skill)


def _selected_super_dan_skill_preflight_script(skill: Mapping[str, Any]) -> Path | None:
    return skill_invocation.selected_skill_preflight_script(skill)


def _run_super_dan_skill_preflight(
    *,
    workspace_root: Path,
    skill: Mapping[str, Any],
    objective: str,
) -> skill_invocation.SkillPreflightResult:
    return skill_invocation.run_skill_preflight(
        workspace_root=workspace_root,
        skill=skill,
        objective=objective,
    )


def _run_selected_super_dan_skill_preflights(
    args: argparse.Namespace,
    *,
    workspace_root: Path,
) -> tuple[bool, list[str]]:
    existing = _selected_super_dan_skill_preflight_notes_from_args(args)
    if existing:
        return True, existing
    if bool(getattr(args, "plan_only", False)):
        return True, []
    if not bool(getattr(args, "live", False)):
        return True, []
    tokens = _selected_super_dan_skill_mentions_from_args(args)
    if not tokens:
        return True, []
    ok, notes = skill_invocation.run_skill_preflights_for_tokens(
        workspace_root=workspace_root,
        tokens=tokens,
        objective=str(getattr(args, "target", "") or ""),
        catalog=_load_super_dan_skill_catalog(str(workspace_root)),
    )
    if notes:
        setattr(args, "_selected_skill_preflight_notes", notes)
        setattr(args, "selected_skill_preflight", notes)
        if getattr(args, "_selected_skill_source", "") == "tui" or hasattr(args, "_tui_selected_skill_mentions"):
            setattr(args, "_tui_skill_preflight_notes", notes)
    return ok, notes


def _explicit_super_dan_skill_reference_excerpt(skill: Mapping[str, Any]) -> str:
    return skill_invocation.explicit_skill_reference_excerpt(
        skill,
        char_limit=_SUPER_DAN_EXPLICIT_SKILL_REFERENCE_LIMIT,
        file_limit=_SUPER_DAN_EXPLICIT_SKILL_REFERENCE_FILE_LIMIT,
    )


def _explicit_super_dan_skill_constraints(selected: Sequence[Mapping[str, Any]]) -> list[str]:
    return skill_invocation.explicit_skill_constraints(selected)


def _apply_auto_super_dan_skills(brief: WorkerBrief) -> WorkerBrief:
    if brief.metadata.get("active_skills"):
        return brief
    selected = _select_explicit_super_dan_skills(brief) or _select_super_dan_skills(brief)
    if not selected:
        return brief
    explicit_constraints = _explicit_super_dan_skill_constraints(selected)
    public_meta = [
        {
            "id": skill.get("id"),
            "name": skill.get("name"),
            "description": skill.get("description"),
            "source_path": skill.get("source_path"),
            "source_scope": skill.get("source_scope"),
            "match_score": skill.get("match_score"),
            "match_reason": skill.get("match_reason"),
            "content_sha256": hashlib.sha256(str(skill.get("content") or "").encode("utf-8")).hexdigest()[:16],
        }
        for skill in selected
    ]
    return brief.model_copy(
        update={
            "contract_snippets": [
                *list(brief.contract_snippets),
                *[_render_super_dan_skill_snippet(skill) for skill in selected],
            ],
            "hard_constraints": [
                *list(brief.hard_constraints),
                *explicit_constraints,
            ],
            "prompt_slots": {
                **dict(brief.prompt_slots),
                "active_skills": public_meta,
            },
            "metadata": {
                **dict(brief.metadata),
                "active_skills": public_meta,
                "active_skill_ids": [str(item.get("id") or "") for item in public_meta],
            },
        }
    )


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _super_plan_root(
    *,
    event_logger: "SuperRunEventLogger | None",
    workspace_root: Path,
) -> Path:
    if event_logger is not None:
        return (event_logger.path.parent / "plans").resolve(strict=False)
    return (workspace_root / ".dan-super" / "plans" / "latest").resolve(strict=False)


def _super_plan_root_relative(plan_root: Path, workspace_root: Path) -> str:
    relative = _relative_workspace_artifact_path(
        plan_root.resolve(strict=False),
        workspace_root=workspace_root,
    )
    return relative or str(plan_root)


def _super_plan_files(plan_root: Path, workspace_root: Path) -> list[str]:
    if not plan_root.exists():
        return []
    files: list[str] = []
    for path in sorted(plan_root.glob("*.md")):
        if not path.is_file():
            continue
        relative = _relative_workspace_artifact_path(
            path.resolve(strict=False),
            workspace_root=workspace_root,
        )
        files.append(relative or str(path))
    return files


def _super_plan_exclude_roots(plan_context: Mapping[str, Any] | None) -> tuple[Path, ...]:
    if not isinstance(plan_context, Mapping):
        return ()
    raw_root = str(plan_context.get("plan_root") or "").strip()
    if not raw_root:
        return ()
    return (Path(raw_root).expanduser().resolve(strict=False),)


def _super_plan_task_state(
    plan_root: Path,
    *,
    workspace_root: Path,
) -> list[dict[str, Any]]:
    if not plan_root.exists():
        return []
    tasks: list[dict[str, Any]] = []
    for path in sorted(plan_root.glob("*.md")):
        if not path.is_file():
            continue
        relative = _relative_workspace_artifact_path(
            path.resolve(strict=False),
            workspace_root=workspace_root,
        ) or str(path)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, start=1):
            match = _SUPER_DAN_PLAN_CHECKBOX_RE.match(line)
            if not match:
                continue
            indent, checked, text = match.groups()
            tasks.append(
                {
                    "file": relative,
                    "line": line_number,
                    "checked": checked.lower() == "x",
                    "indent": len(indent.replace("\t", "  ")),
                    "text": text.strip(),
                }
            )
    return tasks


def _super_plan_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _super_plan_task_graph(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        raw_items = value.get("tasks") or value.get("task_graph") or []
    else:
        raw_items = value
    if not isinstance(raw_items, list):
        return []
    tasks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_items:
        if not isinstance(item, Mapping):
            continue
        task_id = str(
            item.get("task_id")
            or item.get("id")
            or item.get("number")
            or ""
        ).strip()
        if not task_id or task_id in seen:
            continue
        seen.add(task_id)
        depends_on = _super_plan_string_list(
            item.get("depends_on") or item.get("dependencies") or []
        )
        owned_paths = _super_plan_string_list(
            item.get("owned_paths") or item.get("owner_paths") or item.get("paths") or []
        )
        deliverables = _super_plan_string_list(item.get("deliverables") or [])
        validation = _super_plan_string_list(
            item.get("validation") or item.get("checks") or item.get("acceptance") or []
        )
        status = str(item.get("status") or "planned").strip() or "planned"
        parallel_raw = item.get("parallel_safe")
        task: dict[str, Any] = {
            "task_id": task_id,
            "goal": str(item.get("goal") or item.get("summary") or "").strip(),
            "depends_on": depends_on,
            "owned_paths": owned_paths,
            "deliverables": deliverables,
            "validation": validation,
            "parallel_safe": bool(parallel_raw) if parallel_raw is not None else True,
            "status": status,
        }
        parent_id = str(item.get("parent_id") or item.get("parent") or "").strip()
        branch_id = str(item.get("branch_id") or item.get("branch") or "").strip()
        if parent_id:
            task["parent_id"] = parent_id
        if branch_id:
            task["branch_id"] = branch_id
        if item.get("risk") is not None:
            task["risk"] = str(item.get("risk") or "").strip()
        if item.get("confidence") is not None:
            task["confidence"] = _coerce_float(item.get("confidence"))
        tasks.append(task)
    return tasks


def _super_plan_ready_task_ids(
    task_graph: Sequence[Mapping[str, Any]],
    *,
    completed_task_ids: Sequence[str] = (),
) -> list[str]:
    completed = {str(item).strip() for item in completed_task_ids if str(item).strip()}
    ready: list[str] = []
    for task in task_graph:
        task_id = str(task.get("task_id") or "").strip()
        if not task_id:
            continue
        status = str(task.get("status") or "").strip().lower()
        if task_id in completed or status in {"done", "complete", "completed", "x"}:
            continue
        depends_on = _super_plan_string_list(task.get("depends_on") or [])
        if all(dep in completed for dep in depends_on):
            ready.append(task_id)
    return ready


def _super_plan_deferred_task_ids(
    task_graph: Sequence[Mapping[str, Any]],
    ready_task_ids: Sequence[str],
    *,
    completed_task_ids: Sequence[str] = (),
) -> list[str]:
    ready = {str(item).strip() for item in ready_task_ids if str(item).strip()}
    completed = {str(item).strip() for item in completed_task_ids if str(item).strip()}
    deferred: list[str] = []
    for task in task_graph:
        task_id = str(task.get("task_id") or "").strip()
        if not task_id or task_id in ready or task_id in completed:
            continue
        status = str(task.get("status") or "").strip().lower()
        if status in {"done", "complete", "completed", "x"}:
            continue
        deferred.append(task_id)
    return deferred


def _super_plan_deferred_match_terms(plan_context: Mapping[str, Any] | None) -> set[str]:
    if not isinstance(plan_context, Mapping):
        return set()
    deferred_ids = {
        str(item).strip()
        for item in (plan_context.get("deferred_task_ids") or [])
        if str(item).strip()
    }
    if not deferred_ids:
        return set()
    generic_terms = {
        "apps",
        "app",
        "src",
        "lib",
        "index",
        "html",
        "app.js",
        "styles",
        "styles.css",
        "readme",
        "readme.md",
        "page",
        "demo",
        "demos",
        "task",
        "tasks",
    }
    terms: set[str] = {task_id.lower() for task_id in deferred_ids}
    for task in _super_plan_task_graph(plan_context.get("task_graph") or []):
        task_id = str(task.get("task_id") or "").strip()
        if task_id not in deferred_ids:
            continue
        for path in [
            *_super_plan_string_list(task.get("owned_paths") or []),
            *_super_plan_string_list(task.get("deliverables") or []),
        ]:
            normalized = str(path or "").strip().lower()
            if normalized and normalized not in generic_terms:
                terms.add(normalized)
            for part in re.split(r"[/_.\s]+", normalized):
                part = part.strip("-")
                if len(part) >= 4 and part not in generic_terms:
                    terms.add(part)
        goal = str(task.get("goal") or "").strip().lower()
        for part in re.split(r"[^a-z0-9-]+", goal):
            part = part.strip("-")
            if len(part) >= 5 and part not in generic_terms:
                terms.add(part)
    return terms


def _super_plan_text_mentions_deferred(text: str, plan_context: Mapping[str, Any] | None) -> bool:
    lowered = str(text or "").lower()
    if not lowered:
        return False
    return any(term and term in lowered for term in _super_plan_deferred_match_terms(plan_context))


def _super_plan_task_graph_fragment(task_graph: Sequence[Mapping[str, Any]], *, limit: int = 8) -> str:
    fragments: list[str] = []
    for task in list(task_graph)[:limit]:
        task_id = str(task.get("task_id") or "").strip()
        if not task_id:
            continue
        depends = ",".join(_super_plan_string_list(task.get("depends_on") or [])) or "-"
        owned = ",".join(_super_plan_string_list(task.get("owned_paths") or [])[:3]) or "-"
        goal = str(task.get("goal") or "").strip()
        if len(goal) > 90:
            goal = goal[:87].rstrip() + "..."
        fragments.append(f"{task_id}(deps={depends}; owns={owned}; goal={goal or '-'})")
    if len(task_graph) > limit:
        fragments.append(f"+{len(task_graph) - limit} more")
    return "; ".join(fragments)


def _super_plan_task_map(plan_context: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(plan_context, Mapping):
        return {}
    return {
        str(task.get("task_id") or "").strip(): dict(task)
        for task in _super_plan_task_graph(plan_context.get("task_graph") or [])
        if str(task.get("task_id") or "").strip()
    }


def _super_plan_ready_tasks(plan_context: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(plan_context, Mapping):
        return []
    task_map = _super_plan_task_map(plan_context)
    ready_ids = _super_plan_string_list(
        plan_context.get("ready_task_ids") or plan_context.get("assigned_task_ids") or []
    )
    return [task_map[task_id] for task_id in ready_ids if task_id in task_map]


def _super_plan_task_owned_paths(task: Mapping[str, Any]) -> list[str]:
    paths = [
        *_super_plan_string_list(task.get("owned_paths") or []),
        *_super_plan_string_list(task.get("deliverables") or []),
    ]
    seen: set[str] = set()
    unique: list[str] = []
    for path in paths:
        normalized = path.strip().strip("/")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def _super_plan_graph_update_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    update = payload.get("task_graph_update")
    if not isinstance(update, Mapping):
        return {}
    return {
        "scope": str(update.get("scope") or "").strip(),
        "reason": str(update.get("reason") or "").strip(),
        "changed_task_ids": _super_plan_string_list(update.get("changed_task_ids") or []),
    }


def _super_plan_existing_completed_task_ids(plan_context: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(plan_context, Mapping):
        return []
    graph_state = plan_context.get("task_graph_state")
    if isinstance(graph_state, Mapping):
        completed = _super_plan_string_list(graph_state.get("completed_task_ids") or [])
        if completed:
            return completed
    return [
        str(task.get("task_id") or "").strip()
        for task in _super_plan_task_graph(plan_context.get("task_graph") or [])
        if str(task.get("task_id") or "").strip()
        and str(task.get("status") or "").strip().lower()
        in {"done", "complete", "completed", "x"}
    ]


def _super_plan_relative_mutation_path(path: str, *, workspace_root: Path) -> str:
    raw = str(path or "").strip()
    if not raw:
        return ""
    candidate = Path(raw).expanduser()
    resolved = candidate if candidate.is_absolute() else workspace_root / candidate
    try:
        return resolved.resolve(strict=False).relative_to(workspace_root.resolve(strict=False)).as_posix()
    except ValueError:
        return raw.replace("\\", "/").strip("/")


def _super_plan_task_ids_for_mutations(
    plan_context: Mapping[str, Any] | None,
    mutated_paths: Sequence[str],
    *,
    workspace_root: Path,
) -> list[str]:
    if not isinstance(plan_context, Mapping) or not mutated_paths:
        return []
    mutations = [
        _super_plan_relative_mutation_path(path, workspace_root=workspace_root)
        for path in mutated_paths
    ]
    mutations = [path for path in mutations if path]
    if not mutations:
        return []
    candidate_ids = set(
        _super_plan_string_list(plan_context.get("assigned_task_ids") or [])
        + _super_plan_string_list(plan_context.get("parallel_worktree_task_ids") or [])
        + _super_plan_string_list(plan_context.get("ready_task_ids") or [])
    )
    matched: list[str] = []
    for task in _super_plan_task_graph(plan_context.get("task_graph") or []):
        task_id = str(task.get("task_id") or "").strip()
        if not task_id or (candidate_ids and task_id not in candidate_ids):
            continue
        owned_paths = _super_plan_task_owned_paths(task)
        if not owned_paths:
            continue
        if any(
            _super_plan_rel_paths_overlap(mutation, owned_path)
            for mutation in mutations
            for owned_path in owned_paths
        ):
            matched.append(task_id)
    return list(dict.fromkeys(matched))


def _super_plan_rel_paths_overlap(left: str, right: str) -> bool:
    left_norm = str(left or "").strip().strip("/")
    right_norm = str(right or "").strip().strip("/")
    if not left_norm or not right_norm:
        return False
    return (
        left_norm == right_norm
        or left_norm.startswith(f"{right_norm}/")
        or right_norm.startswith(f"{left_norm}/")
    )


def _super_plan_tasks_conflict(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_paths = _super_plan_task_owned_paths(left)
    right_paths = _super_plan_task_owned_paths(right)
    if not left_paths or not right_paths:
        return True
    return any(
        _super_plan_rel_paths_overlap(left_path, right_path)
        for left_path in left_paths
        for right_path in right_paths
    )


def _super_plan_parallel_frontier(
    plan_context: Mapping[str, Any] | None,
    *,
    max_worktree_tasks: int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if max_worktree_tasks <= 0:
        return None, []
    ready_tasks = [
        task
        for task in _super_plan_ready_tasks(plan_context)
        if _super_plan_task_owned_paths(task)
    ]
    if len(ready_tasks) < 2:
        return (ready_tasks[0] if ready_tasks else None), []
    main_task = ready_tasks[0]
    selected: list[dict[str, Any]] = []
    for task in ready_tasks[1:]:
        if len(selected) >= max_worktree_tasks:
            break
        if not bool(task.get("parallel_safe", True)):
            continue
        if _super_plan_tasks_conflict(main_task, task):
            continue
        if any(_super_plan_tasks_conflict(existing, task) for existing in selected):
            continue
        selected.append(task)
    return main_task, selected


def _super_plan_context_for_frontier_main(
    plan_context: Mapping[str, Any] | None,
    main_task: Mapping[str, Any] | None,
    worktree_tasks: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    if not isinstance(plan_context, Mapping) or not main_task or not worktree_tasks:
        return dict(plan_context) if isinstance(plan_context, Mapping) else None
    main_id = str(main_task.get("task_id") or "").strip()
    worktree_ids = [
        str(task.get("task_id") or "").strip()
        for task in worktree_tasks
        if str(task.get("task_id") or "").strip()
    ]
    payload = dict(plan_context)
    payload["assigned_task_ids"] = [main_id]
    payload["ready_task_ids"] = [main_id]
    payload["parallel_worktree_task_ids"] = worktree_ids
    return payload


def _super_plan_branch_id(task: Mapping[str, Any]) -> str:
    explicit = str(task.get("branch_id") or "").strip()
    if explicit:
        return explicit
    task_id = str(task.get("task_id") or "").strip()
    if "-" in task_id:
        return task_id.split("-", 1)[0]
    return task_id or "root"


def _super_plan_parallel_groups(
    task_graph: Sequence[Mapping[str, Any]],
    ready_task_ids: Sequence[str],
) -> list[list[str]]:
    ready = {str(item).strip() for item in ready_task_ids if str(item).strip()}
    ready_tasks = [
        dict(task)
        for task in task_graph
        if str(task.get("task_id") or "").strip() in ready
        and bool(task.get("parallel_safe", True))
        and _super_plan_task_owned_paths(task)
    ]
    groups: list[list[dict[str, Any]]] = []
    for task in ready_tasks:
        placed = False
        for group in groups:
            if not any(_super_plan_tasks_conflict(existing, task) for existing in group):
                group.append(task)
                placed = True
                break
        if not placed:
            groups.append([task])
    return [
        [
            str(task.get("task_id") or "").strip()
            for task in group
            if str(task.get("task_id") or "").strip()
        ]
        for group in groups
        if len(group) > 1
    ]


def _super_plan_version_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-")
    return token or "root"


def _super_plan_existing_branch_refs(
    existing_state: Mapping[str, Any] | None,
) -> tuple[dict[str, str], dict[str, int]]:
    refs: dict[str, str] = {}
    versions: dict[str, int] = {}
    if not isinstance(existing_state, Mapping):
        return refs, versions
    raw_versions = existing_state.get("branch_versions")
    if isinstance(raw_versions, Mapping):
        for key, value in raw_versions.items():
            branch_id = str(key or "").strip()
            if not branch_id:
                continue
            try:
                versions[branch_id] = max(0, int(value))
            except (TypeError, ValueError):
                versions[branch_id] = 0
    raw_refs = existing_state.get("branch_refs")
    if isinstance(raw_refs, list):
        for item in raw_refs:
            if not isinstance(item, Mapping):
                continue
            branch_id = str(item.get("branch_id") or "").strip()
            if not branch_id:
                continue
            version_id = str(item.get("version_id") or "").strip()
            if version_id:
                refs[branch_id] = version_id
            try:
                versions[branch_id] = max(
                    versions.get(branch_id, 0),
                    int(item.get("local_revision") or 0),
                )
            except (TypeError, ValueError):
                versions.setdefault(branch_id, 0)
    return refs, versions


def _super_plan_version_lineage(
    *,
    existing_state: Mapping[str, Any] | None,
    branch_ids: Sequence[str],
    changed_branch_ids: Sequence[str],
    revision: int,
    update_scope: str,
) -> dict[str, Any]:
    existing = existing_state if isinstance(existing_state, Mapping) else {}
    existing_version_id = str(existing.get("version_id") or "").strip()
    existing_root_version_id = str(existing.get("root_version_id") or "").strip()
    existing_base_version_id = str(existing.get("base_version_id") or "").strip()
    existing_refs, existing_branch_versions = _super_plan_existing_branch_refs(existing)
    normalized_branches = sorted(
        {str(branch_id).strip() for branch_id in branch_ids if str(branch_id).strip()}
    )
    changed_branches = sorted(
        {str(branch_id).strip() for branch_id in changed_branch_ids if str(branch_id).strip()}
    )
    whole_graph = str(update_scope or "").strip() != "branch_local" or not existing_root_version_id
    if whole_graph:
        root_version_id = f"v{int(revision)}"
        branch_versions = {branch_id: 0 for branch_id in normalized_branches}
        branch_refs = []
        for branch_id in normalized_branches:
            branch_refs.append(
                {
                    "branch_id": branch_id,
                    "version_id": root_version_id,
                    "parent_version_id": existing_refs.get(branch_id) or existing_version_id,
                    "base_version_id": root_version_id,
                    "local_revision": 0,
                    "changed": True,
                }
            )
        return {
            "version_id": root_version_id,
            "root_version_id": root_version_id,
            "base_version_id": existing_version_id,
            "parent_version_ids": [existing_version_id] if existing_version_id else [],
            "changed_branch_ids": normalized_branches,
            "branch_versions": branch_versions,
            "branch_refs": branch_refs,
        }

    root_version_id = existing_root_version_id or existing_version_id or f"v{max(1, int(revision) - 1)}"
    base_version_id = existing_base_version_id or root_version_id
    branch_versions = {
        branch_id: existing_branch_versions.get(branch_id, 0)
        for branch_id in normalized_branches
    }
    parent_by_branch: dict[str, str] = {}
    branch_suffixes: dict[str, str] = {}
    for branch_id in changed_branches:
        branch_versions[branch_id] = branch_versions.get(branch_id, 0) + 1
        token = _super_plan_version_token(branch_id)
        branch_suffixes[branch_id] = f"b{token}.{branch_versions[branch_id]}"
        parent_by_branch[branch_id] = existing_refs.get(branch_id) or root_version_id
    branch_refs = []
    for branch_id in normalized_branches:
        token = _super_plan_version_token(branch_id)
        local_revision = branch_versions.get(branch_id, 0)
        changed = branch_id in set(changed_branches)
        version_id = (
            f"{root_version_id}.{branch_suffixes[branch_id]}"
            if changed
            else existing_refs.get(branch_id) or root_version_id
        )
        branch_refs.append(
            {
                "branch_id": branch_id,
                "version_id": version_id,
                "parent_version_id": parent_by_branch.get(branch_id) or existing_refs.get(branch_id) or root_version_id,
                "base_version_id": root_version_id,
                "local_revision": local_revision,
                "changed": changed,
                "ref_name": f"branch/{token}",
            }
        )
    changed_suffixes = [branch_suffixes[branch_id] for branch_id in changed_branches if branch_id in branch_suffixes]
    version_id = (
        f"{root_version_id}.{'+'.join(changed_suffixes)}"
        if changed_suffixes
        else existing_version_id or root_version_id
    )
    parent_version_ids = list(
        dict.fromkeys(parent_by_branch[branch_id] for branch_id in changed_branches if parent_by_branch.get(branch_id))
    )
    return {
        "version_id": version_id,
        "root_version_id": root_version_id,
        "base_version_id": base_version_id,
        "parent_version_ids": parent_version_ids,
        "changed_branch_ids": changed_branches,
        "branch_versions": branch_versions,
        "branch_refs": branch_refs,
    }


def _super_plan_task_graph_state(
    plan_context: Mapping[str, Any] | None,
    *,
    revision: int,
    source: str,
    update_reason: str,
    update_scope: str = "whole_graph",
    changed_task_ids: Sequence[str] = (),
    active_task_ids: Sequence[str] = (),
    completed_task_ids: Sequence[str] = (),
    ready_task_ids: Sequence[str] | None = None,
    deferred_task_ids: Sequence[str] | None = None,
    dependency_revisions: Sequence[Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(plan_context, Mapping):
        return None
    task_graph = _super_plan_task_graph(plan_context.get("task_graph") or [])
    if not task_graph:
        return None
    existing_state = plan_context.get("task_graph_state")
    previous_task_states = {
        str(item.get("task_id") or "").strip(): str(item.get("state") or "").strip()
        for item in (
            existing_state.get("tasks")
            if isinstance(existing_state, Mapping) and isinstance(existing_state.get("tasks"), list)
            else []
        )
        if isinstance(item, Mapping) and str(item.get("task_id") or "").strip()
    }
    existing_completed = (
        existing_state.get("completed_task_ids")
        if isinstance(existing_state, Mapping)
        else []
    )
    changed = {
        str(item).strip()
        for item in changed_task_ids
        if str(item).strip()
    }
    completed = {
        str(item).strip()
        for item in [*list(existing_completed or []), *list(completed_task_ids)]
        if str(item).strip()
    }
    active = {
        str(item).strip()
        for item in active_task_ids
        if str(item).strip()
    }
    ready = {
        str(item).strip()
        for item in (
            ready_task_ids
            if ready_task_ids is not None
            else plan_context.get("ready_task_ids") or plan_context.get("assigned_task_ids") or []
        )
        if str(item).strip()
    }
    if not ready and task_graph:
        ready = set(_super_plan_ready_task_ids(task_graph, completed_task_ids=tuple(completed)))
    deferred = {
        str(item).strip()
        for item in (
            deferred_task_ids
            if deferred_task_ids is not None
            else plan_context.get("deferred_task_ids") or []
        )
        if str(item).strip()
    }
    if not deferred and task_graph:
        deferred = set(
            _super_plan_deferred_task_ids(
                task_graph,
                sorted(ready),
                completed_task_ids=tuple(completed),
            )
        )
    branch_counts: dict[str, dict[str, Any]] = {}
    state_tasks: list[dict[str, Any]] = []
    for task in task_graph:
        task_id = str(task.get("task_id") or "").strip()
        branch_id = _super_plan_branch_id(task)
        status = str(task.get("status") or "").strip().lower()
        if task_id in completed or status in {"done", "complete", "completed", "x"}:
            state = "done"
        elif task_id in active:
            state = "active"
        elif task_id in ready:
            state = "ready"
        elif task_id in deferred:
            state = "deferred"
        else:
            state = "planned"
        enriched = dict(task)
        enriched["branch_id"] = branch_id
        enriched["state"] = state
        state_tasks.append(enriched)
        if previous_task_states.get(task_id) != state:
            changed.add(task_id)
        branch = branch_counts.setdefault(
            branch_id,
            {
                "branch_id": branch_id,
                "task_ids": [],
                "ready_task_ids": [],
                "active_task_ids": [],
                "completed_task_ids": [],
                "deferred_task_ids": [],
            },
        )
        branch["task_ids"].append(task_id)
        if state == "ready":
            branch["ready_task_ids"].append(task_id)
        elif state == "active":
            branch["active_task_ids"].append(task_id)
        elif state == "done":
            branch["completed_task_ids"].append(task_id)
        elif state == "deferred":
            branch["deferred_task_ids"].append(task_id)
    task_branch_ids = {
        str(task.get("task_id") or "").strip(): _super_plan_branch_id(task)
        for task in task_graph
        if str(task.get("task_id") or "").strip()
    }
    changed_branch_ids = sorted(
        {
            task_branch_ids[task_id]
            for task_id in changed
            if task_id in task_branch_ids and task_branch_ids[task_id]
        }
    )
    lineage = _super_plan_version_lineage(
        existing_state=existing_state if isinstance(existing_state, Mapping) else None,
        branch_ids=sorted(branch_counts.keys()),
        changed_branch_ids=changed_branch_ids,
        revision=revision,
        update_scope=str(update_scope or "whole_graph"),
    )
    return {
        "schema": "super_dan_task_graph_v1",
        "graph_id": str(plan_context.get("graph_id") or "run-local-task-graph"),
        "revision": int(revision),
        **lineage,
        "source": str(source or "unknown"),
        "update_reason": str(update_reason or "").strip(),
        "update_scope": str(update_scope or "whole_graph"),
        "changed_task_ids": sorted(changed),
        "tasks": state_tasks,
        "ready_task_ids": sorted(ready),
        "deferred_task_ids": sorted(deferred),
        "active_task_ids": sorted(active),
        "completed_task_ids": sorted(completed),
        "parallel_groups": _super_plan_parallel_groups(task_graph, sorted(ready)),
        "branches": list(branch_counts.values()),
        "dependency_revisions": [
            dict(item) if isinstance(item, Mapping) else str(item)
            for item in (dependency_revisions if dependency_revisions is not None else plan_context.get("dependency_revisions") or [])
        ],
    }


def _super_plan_context_with_graph_state(
    plan_context: Mapping[str, Any] | None,
    *,
    revision: int,
    source: str,
    update_reason: str,
    update_scope: str = "whole_graph",
    changed_task_ids: Sequence[str] = (),
    active_task_ids: Sequence[str] = (),
    completed_task_ids: Sequence[str] = (),
    ready_task_ids: Sequence[str] | None = None,
    deferred_task_ids: Sequence[str] | None = None,
    dependency_revisions: Sequence[Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(plan_context, Mapping):
        return None
    payload = dict(plan_context)
    graph_state = _super_plan_task_graph_state(
        payload,
        revision=revision,
        source=source,
        update_reason=update_reason,
        update_scope=update_scope,
        changed_task_ids=changed_task_ids,
        active_task_ids=active_task_ids,
        completed_task_ids=completed_task_ids,
        ready_task_ids=ready_task_ids,
        deferred_task_ids=deferred_task_ids,
        dependency_revisions=dependency_revisions,
    )
    if graph_state is not None:
        payload["task_graph_state"] = graph_state
        payload["task_graph_revision"] = revision
    return payload


def _super_plan_context_payload(
    plan_context: Mapping[str, Any] | None,
    *,
    plan_root: Path | None = None,
    workspace_root: Path | None = None,
    include_task_state_key: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(plan_context, Mapping):
        return None
    payload = {
        "persistence": str(plan_context.get("persistence") or "run_temp"),
        "plan_root": str(plan_context.get("plan_root") or ""),
        "plan_root_relative": str(plan_context.get("plan_root_relative") or ""),
        "plan_files": list(plan_context.get("plan_files") or []),
        "assigned_task_ids": list(plan_context.get("assigned_task_ids") or []),
        "ready_task_ids": list(plan_context.get("ready_task_ids") or []),
        "deferred_task_ids": list(plan_context.get("deferred_task_ids") or []),
        "task_graph": _super_plan_task_graph(plan_context.get("task_graph") or []),
        "dependency_revisions": list(plan_context.get("dependency_revisions") or []),
        "execution_mode": str(plan_context.get("execution_mode") or "dependency_frontier"),
        "parallel_worktree_task_ids": list(plan_context.get("parallel_worktree_task_ids") or []),
        "validation": dict(plan_context.get("validation") or {}),
        "request_understanding": dict(plan_context.get("request_understanding") or {}),
    }
    if isinstance(plan_context.get("task_graph_state"), Mapping):
        payload["task_graph_state"] = dict(plan_context.get("task_graph_state") or {})
        payload["task_graph_revision"] = plan_context.get("task_graph_revision")
    if include_task_state_key and plan_root is not None and workspace_root is not None:
        payload[include_task_state_key] = _super_plan_task_state(
            plan_root,
            workspace_root=workspace_root,
        )
    return payload


def _super_plan_file_contract(plan_root_relative: str) -> str:
    return (
        "Run-local plan file contract:\n"
        f"- Write temporary plan markdown only under `{plan_root_relative}/`; do not update permanent `docs/plans/` "
        "or `docs/todo.md` from the planner stage.\n"
        "- Use all-digit numeric identifiers only. Top-level phase files are `1-short-name.md`, `2-short-name.md`; "
        "sub-plan files are `1-1-short-slice.md`, `1-2-short-slice.md`.\n"
        "- Do not create third-level plan files such as `1-1-1-*`. Put deeper breakdowns inside the nearest plan file.\n"
        "- A top-level phase must be one internally coherent large feature chunk. If two chunks are unrelated, use "
        "`1-*` and `2-*` rather than forcing them under `1-1` and `1-2`.\n"
        "- Sub-plans must be coherent slices of their parent phase. Keep simple tasks to one top-level plan file.\n"
        "- Keep checklist nesting to at most two levels and make each checkbox evidence-checkable by a validator.\n"
        "- For broad tasks, predict a compact dependency task graph: each executable task should have a stable digit "
        "task id, optional `parent_id`, optional `branch_id`, `depends_on`, `owned_paths`, deliverables, validation "
        "checks, and whether it is parallel-safe.\n"
        "- The ready frontier is the set of tasks whose dependencies are already satisfied and whose owned paths do not "
        "conflict. Downstream tasks remain queued until their dependencies are complete.\n"
        "- Use sections: Status, Goal, Tasks, Decisions, Notes. Sub-plan files should include a Parent link.\n"
        "- Leave tasks unchecked until an executor actually completes them; executors may tick completed tasks later."
    )


def _super_plan_executor_contract(plan_context: Mapping[str, Any] | None) -> str:
    payload = _super_plan_context_payload(plan_context)
    if not payload:
        return ""
    ready_ids = list(payload.get("ready_task_ids") or payload.get("assigned_task_ids") or [])
    ready = ", ".join(str(item) for item in ready_ids) or "the highest-value ready frontier"
    parallel_worktree = ", ".join(str(item) for item in (payload.get("parallel_worktree_task_ids") or [])[:8])
    parallel_note = (
        f" Parallel worktree tasks already admitted elsewhere, do not duplicate them in this lane: {parallel_worktree}."
        if parallel_worktree
        else ""
    )
    deferred = ", ".join(str(item) for item in (payload.get("deferred_task_ids") or [])[:8])
    deferred_note = f" Deferred or blocked tasks, not for this worker unless dependencies change: {deferred}." if deferred else ""
    plan_files = ", ".join(str(item) for item in (payload.get("plan_files") or [])[:6]) or "run-local plan files"
    graph = _super_plan_task_graph_fragment(payload.get("task_graph") or [])
    graph_note = f" Dependency graph: {graph}." if graph else ""
    return (
        "Plan execution contract / Dependency-frontier execution contract: a run-local plan has already been validated for this broad objective. "
        f"Plan files: {plan_files}. Execute the current ready frontier: {ready}.{parallel_note}{deferred_note}{graph_note} "
        "You may complete one or more ready tasks when their owned paths are compatible, but do not expand into blocked "
        "downstream tasks merely because the full objective mentions them. If the dependency prediction is wrong, update "
        "the plan notes or dependency revisions with evidence and stop at the smallest coherent correction. "
        "You may update plan checkboxes only for work you actually complete with concrete workspace evidence. "
        "Leave partial or blocked work unchecked and add a short note instead. "
        "Plan-file edits alone do not count as the deliverable mutation; create or edit the actual requested artifact too."
    )


def _super_plan_validation_contract(plan_context: Mapping[str, Any] | None) -> str:
    payload = _super_plan_context_payload(plan_context)
    if not payload:
        return ""
    ready = ", ".join(str(item) for item in (payload.get("ready_task_ids") or payload.get("assigned_task_ids") or []))
    deferred = ", ".join(str(item) for item in (payload.get("deferred_task_ids") or [])[:8])
    frontier_note = (
        f"Current ready frontier: {ready or 'highest-value ready tasks'}. "
        f"Deferred or blocked tasks: {deferred or 'none recorded'}. "
    )
    return (
        "Plan-progress audit contract / Dependency-frontier validation contract: validate the current ready frontier, not the entire future DAG at once. "
        f"{frontier_note}"
        "Compare plan checkbox state with changed deliverable files and available evidence. "
        "Any task newly marked `[x]` must be supported by actual implementation, dataset/report changes, or verification. "
        "Fail or list `blocking_current_task_failures` only when the current frontier is incomplete, broken, or unsupported. "
        "Put future downstream gaps under `deferred_task_gaps` / `remaining_work`; do not turn queued DAG tasks into a "
        "repair brief for the current worker. If the current frontier is materially complete and only deferred tasks remain, "
        "return `passed=true` with `completion_scope=\"current_frontier\"`."
    )


def _super_should_run_planner(
    objective: str,
    *,
    operator_intent_policy: OperatorIntentPolicy,
    prompt_only_creation_target: str | None,
    tool_ids: Sequence[str],
) -> bool:
    if prompt_only_creation_target:
        return False
    if not operator_intent_policy.allow_workspace_mutation:
        return False
    if operator_intent_policy.forbid_other_workspace_inputs:
        return False
    if "file_write" not in set(tool_ids):
        return False
    text = " ".join(str(objective or "").lower().split())
    if not text:
        return False
    if _super_mentions_source_file_path(text):
        return False
    if re.search(r"\bsmall\s+(?:grounded\s+)?edits?\b", text):
        return False
    word_count = len(text.split())
    narrow_terms = ("small ", "single ", "one ", "quick ", "bounded ", " note", " file")
    if word_count <= 14 and any(term in f" {text} " for term in narrow_terms):
        return False
    if _super_is_interactive_source_implementation_objective(text):
        return False
    if _super_is_targeted_source_repair_objective(text):
        return False
    if _super_explicitly_requests_run_local_planning(text):
        return True
    broad_terms = (
        "project",
        "repo",
        "codebase",
        "dataset",
        "pipeline",
        "system",
        "agent",
        "organism",
        "architecture",
        "refactor",
        "migration",
        "end to end",
        "self evolve",
    )
    action_terms = (
        "build",
        "improve",
        "clean",
        "implement",
        "create",
        "fix",
        "migrate",
        "rewrite",
    )
    if any(term in text for term in broad_terms) and any(term in text for term in action_terms):
        return True
    return False


_SUPER_SOURCE_FILE_EXTENSIONS = (
    "c",
    "cc",
    "cpp",
    "cs",
    "css",
    "go",
    "gd",
    "gds",
    "h",
    "hpp",
    "html",
    "java",
    "js",
    "json",
    "jsx",
    "kt",
    "lua",
    "md",
    "php",
    "py",
    "rb",
    "rs",
    "scala",
    "scss",
    "sh",
    "svelte",
    "swift",
    "ts",
    "tsx",
    "txt",
    "vue",
    "xml",
    "yaml",
    "yml",
)


def _super_mentions_source_file_path(text: str) -> bool:
    normalized = " ".join(str(text or "").lower().split())
    if not normalized:
        return False
    extensions = "|".join(re.escape(ext) for ext in _SUPER_SOURCE_FILE_EXTENSIONS)
    return bool(re.search(rf"\b[\w./-]+\.({extensions})\b", normalized))


def _super_explicitly_requests_run_local_planning(text: str) -> bool:
    normalized = " ".join(str(text or "").lower().split())
    if not normalized:
        return False
    planning_patterns = (
        r"\b(?:break\s+down|breakdown|roadmap|todo|milestone)\b",
        r"\b(?:create|draft|make|outline|prepare|produce|write)\s+(?:a\s+|the\s+)?(?:run-local\s+)?plan\b",
        r"\b(?:help\s+me\s+|please\s+)?plan\s+(?:the|this|out|for|how)\b",
        r"^\s*(?:please\s+)?plan\b",
        r"\bplanning\s+only\b",
        r"\bno\s+execution\b.*\bplan\b",
    )
    return any(re.search(pattern, normalized) for pattern in planning_patterns)


def _super_is_interactive_source_implementation_objective(text: str) -> bool:
    normalized = " ".join(str(text or "").lower().split())
    if not normalized:
        return False
    action_terms = (
        "build",
        "create",
        "develop",
        "extend",
        "fix",
        "improve",
        "implement",
        "make",
        "polish",
        "repair",
        "ship",
        "upgrade",
    )
    interaction_terms = (
        "animation",
        "animations",
        "button",
        "click",
        "controls",
        "dashboard",
        "demo",
        "drag",
        "editor",
        "flow",
        "form",
        "game",
        "gameplay",
        "input",
        "interactive",
        "live",
        "loop",
        "operate",
        "operable",
        "playable",
        "player",
        "prototype",
        "realtime",
        "round",
        "rounds",
        "simulation",
        "stateful",
        "tool",
        "turn",
        "turns",
        "usable",
        "visualization",
        "wave",
        "waves",
        "workflow",
    )
    implementation_context_terms = (
        "assets",
        "code",
        "component",
        "components",
        "compile",
        "edit",
        "edits",
        "file",
        "files",
        "function",
        "functions",
        "implementation",
        "module",
        "modules",
        "project",
        "run",
        "scene",
        "scenes",
        "script",
        "scripts",
        "source",
        "test",
        "validator",
        "workspace",
    )
    has_action = any(re.search(rf"\b{re.escape(term)}\b", normalized) for term in action_terms)
    has_interaction = any(
        re.search(rf"\b{re.escape(term)}\b", normalized) for term in interaction_terms
    )
    has_implementation_context = any(
        re.search(rf"\b{re.escape(term)}\b", normalized) for term in implementation_context_terms
    ) or _super_mentions_source_file_path(normalized)
    return has_action and has_interaction and has_implementation_context


def _super_is_targeted_source_repair_objective(text: str) -> bool:
    normalized = " ".join(str(text or "").lower().split())
    if not normalized:
        return False
    action_terms = (
        "address",
        "debug",
        "fix",
        "patch",
        "repair",
        "resolve",
        "unblock",
    )
    failure_terms = (
        "bug",
        "compile",
        "compiler",
        "crash",
        "error",
        "exception",
        "fail",
        "failing",
        "failure",
        "lint",
        "regression",
        "runtime",
        "test",
        "tests",
        "traceback",
        "typecheck",
        "validation",
    )
    scope_terms = (
        "current",
        "exact",
        "focused",
        "function",
        "line",
        "method",
        "minimal",
        "narrow",
        "one",
        "small",
        "source",
        "targeted",
    )
    has_action = any(re.search(rf"\b{re.escape(term)}\b", normalized) for term in action_terms)
    has_failure = any(re.search(rf"\b{re.escape(term)}\b", normalized) for term in failure_terms)
    has_scope = any(re.search(rf"\b{re.escape(term)}\b", normalized) for term in scope_terms)
    mentions_source = _super_mentions_source_file_path(normalized)
    if not (has_action and has_failure and (has_scope or mentions_source or len(normalized.split()) <= 28)):
        return False
    broad_without_path = any(
        term in normalized
        for term in (
            "architecture",
            "codebase",
            "end to end",
            "migration",
            "project",
            "refactor",
            "repo",
            "system",
        )
    ) and not mentions_source
    return not broad_without_path


class SuperRunEventLogger:
    """Persist timestamped Super DAN live events to one JSONL file."""

    def __init__(
        self,
        *,
        path: Path,
        session_id: str = "",
        turn_id: str = "",
        task_id: str = "",
        organism_id: str = "",
        organ_id: str = "",
        trace_id: str = "",
        progress_callback=None,
        hook_runtime: SuperHookRuntime | None = None,
    ) -> None:
        self._writer = OrganismLogWriter(
            path=path,
            context=OrganismLogContext(
                product="dan_super",
                stream_kind="bounded_run",
                session_id=session_id,
                turn_id=turn_id,
                task_id=task_id,
                trace_id=trace_id,
                organism_id=organism_id,
                organ_id=organ_id,
            ),
        )
        self._progress_callback = progress_callback
        self._hook_runtime = hook_runtime
        self.path = self._writer.path

    def emit(self, event: dict[str, Any]) -> None:
        payload = dict(event)
        row = self._writer.emit(payload)
        if self._progress_callback is not None:
            self._progress_callback(dict(row))
        self._emit_hook_rows(row)

    def _emit_hook_rows(self, row: dict[str, Any]) -> None:
        if self._hook_runtime is None:
            return
        for hook_event in self._hook_runtime.process_event(dict(row)):
            hook_row = self._writer.emit(dict(hook_event))
            if self._progress_callback is not None:
                self._progress_callback(dict(hook_row))

    def emit_trace_rows(self, trace_rows: Sequence[dict[str, Any]]) -> None:
        rows = [dict(row) for row in trace_rows]
        self._writer.emit_trace_rows(rows)
        if self._progress_callback is not None:
            for row in rows:
                self._progress_callback(dict(row))

    def update_context(self, **updates: Any) -> None:
        self._writer.update_context(**updates)

    def hook_state_snapshot(self) -> dict[str, Any] | None:
        if self._hook_runtime is None:
            return None
        return self._hook_runtime.snapshot()

    def plan_worktree_task(
        self,
        packet_id: str,
        *,
        owner_scope: str,
        reason: str,
    ) -> Any | None:
        if self._hook_runtime is None:
            return None
        task, events = self._hook_runtime.plan_worktree_task(
            packet_id,
            owner_scope=owner_scope,
            reason=reason,
        )
        for event in events:
            row = self._writer.emit(dict(event))
            if self._progress_callback is not None:
                self._progress_callback(dict(row))
        return task

    def admit_worktree_diff(self, diff_packet: Mapping[str, Any]) -> list[dict[str, Any]]:
        if self._hook_runtime is None:
            return []
        emitted: list[dict[str, Any]] = []
        for event in self._hook_runtime.admit_worktree_diff(diff_packet):
            row = self._writer.emit(dict(event))
            emitted.append(dict(row))
            if self._progress_callback is not None:
                self._progress_callback(dict(row))
        return emitted

    def close(self) -> None:
        self._writer.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dan-super-organism",
        description=(
            "Run Super DAN from the command line. With an objective and configured "
            "model, the CLI runs the native live execution lane; use --plan-only for "
            "the coordination contract/showcase."
        ),
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help=(
            "Operator objective. Omit in a terminal to start an interactive Super DAN session; "
            "non-interactive live work requires an explicit objective. Explicit report flags can "
            f"still render the default universal-agent showcase ({DEFAULT_SUPER_ORGANISM_TARGET!r})."
        ),
    )
    parser.add_argument(
        "--organism-id",
        default=DEFAULT_SUPER_ORGANISM_ID,
        help="Organism identifier stamped into the report.",
    )
    parser.add_argument(
        "--cell-count",
        type=int,
        default=DEFAULT_SUPER_ORGANISM_CELL_COUNT,
        help="Logical cell count. Defaults to the cheaper 20-cell operator mode; use 100 for the showcase.",
    )
    parser.add_argument(
        "--active-cell-cap",
        type=int,
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Only print the organism contract; do not materialize deterministic artifacts.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Force Super DAN's native live execution lane with local tools. "
            "Objectives auto-enter this lane when a live model is configured."
        ),
    )
    parser.add_argument(
        "--model",
        help="Model for --live. Defaults to DAN_MODEL or DAN_LLM_MODEL.",
    )
    parser.add_argument(
        "--api-key",
        help="Optional API key override for --live.",
    )
    parser.add_argument(
        "--base-url",
        help="Optional OpenAI-compatible base URL override for --live.",
    )
    parser.add_argument(
        "--max-tool-rounds",
        type=int,
        default=10,
        help="Maximum model/tool rounds for --live. Defaults to 10.",
    )
    parser.add_argument(
        "--max-tool-calls",
        type=int,
        default=128,
        help="Maximum local tool calls for --live. Defaults to 128.",
    )
    parser.add_argument(
        "--validation-command",
        action="append",
        default=[],
        help=(
            "Run this shell command from the workspace after live validation. "
            "May be supplied multiple times; nonzero exits or runtime/compiler error output fail the run."
        ),
    )
    parser.add_argument(
        "--validation-timeout",
        type=int,
        default=120,
        help="Timeout in seconds for each --validation-command. Defaults to 120.",
    )
    parser.add_argument(
        "--validation-continue-on-failure",
        action="store_true",
        help=(
            "Run all --validation-command entries even after one fails. "
            "By default validation stops at the first failed command to avoid expensive checks after a mandatory gate fails."
        ),
    )
    parser.add_argument(
        "--workspace",
        default=".",
        help="Workspace root for Super DAN artifacts. Defaults to the current directory.",
    )
    parser.add_argument(
        "--artifact-dir",
        default="website",
        help=(
            "Directory, relative to --workspace unless absolute, for generated website artifacts. "
            "In code-like live mode, a website-named workspace is treated as the artifact root when this is omitted."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON output. Live mode includes both the report and live build result.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print the full organism text report instead of the compact CLI summary.",
    )
    parser.add_argument(
        "--quiet-progress",
        action="store_true",
        help="Disable live progress lines for non-JSON runs.",
    )
    parser.add_argument(
        "--reactivity",
        choices=("immediate", "balanced", "batch"),
        default="balanced",
        help=(
            "Super DAN hook/inbox reactivity profile for live runs. "
            "Immediate wakes organs quickly, balanced coalesces short bursts, batch waits longer."
        ),
    )
    parser.add_argument(
        "--queue-status",
        action="store_true",
        help="Print Super DAN hook/inbox queue state. With no objective, only prints status.",
    )
    parser.add_argument(
        "--worktree-parallelism",
        type=int,
        default=0,
        help=(
            "Maximum isolated worktree workers admitted from the validated dependency-ready frontier. "
            "Diffs are copied back only after hook admission."
        ),
    )
    parser.add_argument(
        "--output",
        help="Optional path to write the JSON report.",
    )
    return parser


def _resolve_internal_active_cell_cap(args: argparse.Namespace) -> int:
    explicit = getattr(args, "active_cell_cap", None)
    if explicit is not None:
        return int(explicit)
    cell_count = int(getattr(args, "cell_count", DEFAULT_SUPER_ORGANISM_CELL_COUNT))
    if cell_count <= DEFAULT_SUPER_ORGANISM_CELL_COUNT:
        return DEFAULT_SUPER_ORGANISM_ACTIVE_CELL_CAP
    return min(20, max(DEFAULT_SUPER_ORGANISM_ACTIVE_CELL_CAP, (cell_count + 4) // 5))


def _print_text_report(report: SuperOrganismReport) -> None:
    lines = _text_report_lines(report)
    print("\n".join(lines), end="\n")


def _print_compact_report(
    report: SuperOrganismReport,
    *,
    artifact_paths: Sequence[Path] | None = None,
) -> None:
    paths = list(artifact_paths or [])
    if paths:
        lines = [
            "Status: completed",
            "Build: completed",
            f"Website: {paths[0]}",
            "Files:",
            *[f"- {path}" for path in paths],
            "",
            f"Organism: {_display_text(report.organism_id)} ({report.cell_count} logical cells)",
            f"Target: {_display_text(report.target)}",
            "Full trace: rerun with --verbose or --json.",
        ]
        print("\n".join(lines), end="\n\n")
        return

    lines = [
        f"Status: {_display_text(report.status)}",
        f"Organism: {_display_text(report.organism_id)} ({report.cell_count} logical cells)",
        f"Target: {_display_text(report.target)}",
        f"Verdict: {_display_text(report.final_verdict)}",
        f"{_display_text(report.score_label)}: {report.credibility_score:.2f}",
        "Organs: " + ", ".join(f"{key}={value}" for key, value in sorted(report.organ_counts.items())),
        "Coordination: "
        + " -> ".join(
            [
                "objective",
                "context",
                "decompose",
                "memory",
                "immune",
                "reallocate",
                "probe",
                "synthesize",
            ]
        ),
        "",
        "Full trace: rerun with --verbose or --json.",
    ]
    print("\n".join(lines), end="\n")


def _print_live_report(
    report: SuperOrganismReport,
    live_result: dict[str, Any],
    *,
    verbose: bool = False,
) -> None:
    files = [str(path) for path in live_result.get("files") or []]
    missing = [str(path) for path in live_result.get("missing_files") or []]
    status = _display_text(live_result.get("status") or "failed")
    summary_label = _display_text(live_result.get("summary_label") or "Live Run")
    lines = [
        "Status: completed" if status == "completed" else "Status: failed",
        f"{summary_label}: {status}",
    ]
    website = str(live_result.get("website") or "").strip()
    if website:
        lines.append(f"Website: {website}")
    if files and website:
        lines.append("Files:")
        lines.extend(f"- {path}" for path in files)
    elif files:
        lines.append("Mutated Paths:")
        lines.extend(f"- {path}" for path in files)
    if missing:
        lines.append("Missing Files:")
        lines.extend(f"- {path}" for path in missing)
    error = str(live_result.get("error") or "").strip()
    if error:
        lines.append(f"Error: {_display_text(error)}")
    failed_step = str(live_result.get("failed_step") or "").strip()
    if failed_step and status != "completed":
        lines.append(f"Failed Step: {_display_text(failed_step)}")
    validation = dict(live_result.get("validation") or {})
    if validation:
        verdict = "passed" if validation.get("passed") else "failed"
        score = _coerce_float(validation.get("overall_score"))
        lines.append(f"Validation: {verdict} ({score:.2f})")
    event_log_path = str(live_result.get("event_log_path") or "").strip()
    if event_log_path:
        lines.append(f"Event Log: {event_log_path}")
    hook_state = live_result.get("hook_state")
    if bool(live_result.get("show_queue_status")) and isinstance(hook_state, dict):
        inboxes = hook_state.get("inboxes") if isinstance(hook_state.get("inboxes"), dict) else {}
        lines.append("Hook Queues:")
        for inbox_id in sorted(inboxes):
            inbox = inboxes.get(inbox_id) if isinstance(inboxes.get(inbox_id), dict) else {}
            metrics = inbox.get("metrics") if isinstance(inbox.get("metrics"), dict) else {}
            pending = len(list(inbox.get("pending_packet_ids") or []))
            active = len(list(inbox.get("active_lease_ids") or []))
            lines.append(
                f"- {inbox_id}: pending={pending} active={active} "
                f"enqueued={int(metrics.get('enqueued') or 0)} "
                f"leased={int(metrics.get('leased') or 0)} "
                f"coalesced={int(metrics.get('coalesced') or 0)}"
            )
    lines.extend(
        [
            f"Model: {_display_text(live_result.get('model') or '')}",
            f"Tool Calls: {int(live_result.get('tool_calls') or 0)}",
            f"Token Usage: {_format_token_usage(live_result.get('token_usage'))}",
            "",
            f"Organism: {_display_text(report.organism_id)} ({report.cell_count} logical cells)",
            f"Target: {_display_text(report.target)}",
            "Full trace shown below." if verbose else "Full trace: rerun with --verbose or --json.",
        ]
    )
    print("\n".join(lines), end="\n")
    if verbose:
        print()
        _print_text_report(report)


def _text_report_lines(report: SuperOrganismReport) -> list[str]:
    lines = [
        f"Status: {_display_text(report.status)}",
        f"Mode: {_display_text(report.mode)}",
        f"Organism Contract: {_display_text(report.scenario.value)}",
        f"Organism: {_display_text(report.organism_id)}",
        f"Target: {_display_text(report.target)}",
        f"Cells: {report.cell_count} logical",
        f"Verdict: {_display_text(report.final_verdict)}",
        f"{_display_text(report.score_label)}: {report.credibility_score:.2f}",
        f"Stages: {_display_text(' -> '.join(report.stage_sequence))}",
        "Organs: " + ", ".join(f"{key}={value}" for key, value in sorted(report.organ_counts.items())),
        "Signals: " + ", ".join(f"{key}={value}" for key, value in sorted(report.signal_counts.items())),
    ]
    if report.claim_graph:
        lines.extend(["", "Claim Graph:"])
        for claim in report.claim_graph:
            lines.append(
                f"- {claim.claim_id} [{claim.status.value}, {claim.confidence:.2f}] "
                f"{_display_text(claim.text)}"
            )
    if report.delivery_plan:
        lines.extend(["", "Delivery Plan:"])
        for node in report.delivery_plan:
            lines.append(
                f"- {node.node_id} | {node.status} | {node.assigned_cell_count} cells | "
                f"{_display_text(node.title)}"
            )
    if report.shared_board is not None:
        lines.extend(["", "Board:"])
        lines.append(
            "- completed="
            + str(len(report.shared_board.completed_ticket_ids))
            + ", waiting="
            + str(len(report.shared_board.waiting_ticket_ids))
            + ", blocked="
            + str(len(report.shared_board.blocked_ticket_ids))
            + ", reserve="
            + str(len(report.shared_board.reserve_cell_ids))
        )
        lines.append(
            "- packets: published="
            + str(len(report.shared_board.published_packet_ids))
            + ", pending="
            + str(len(report.shared_board.pending_packet_ids))
        )
    if report.coordination_tickets:
        lines.extend(["", "Tickets:"])
        for ticket in report.coordination_tickets:
            lines.append(
                f"- {ticket.ticket_id} | {ticket.status} | owner {ticket.owner_cell_id} | "
                f"{_display_text(ticket.title)}"
            )
    if report.handoff_packets:
        lines.extend(["", "Handoffs:"])
        for packet in report.handoff_packets:
            lines.append(
                f"- {packet.packet_id} | {packet.status} | {packet.from_ticket_id}->{packet.to_ticket_id} | "
                f"{packet.packet_type}"
            )
    lines.extend(["", "Reallocations:"])
    for decision in report.reallocation_decisions:
        lines.append(
            f"- {decision.decision_id}: {decision.cell_count} "
            f"{decision.from_organ.value}->{decision.to_organ.value}; {_display_text(decision.reason)}"
        )
    if report.final_audit is not None:
        lines.extend(
            [
                "",
                "Final Audit:",
                f"- {report.final_audit.status} | satisfied={str(report.final_audit.satisfied).lower()} | blockers: "
                f"{_display_text(', '.join(report.final_audit.blocker_ticket_ids) or 'none')}",
                _display_text(report.final_audit.summary),
            ]
        )
    lines.extend(["", "Final Memo:", _display_text(report.final_memo)])
    lines.extend(["", f"Caveat: {_display_text(report.caveat)}"])
    return lines


def _display_text(value: object) -> str:
    return " ".join(str(value).split())


def _truncate_text(value: Any, *, limit: int = 160) -> str:
    text = _display_text(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _path_basename(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return Path(text).name or text
    except Exception:
        return text


def _tool_request_summary(tool_id: str, arguments: Mapping[str, Any]) -> str:
    args = dict(arguments or {})
    if tool_id == "file_edit":
        mode = str(args.get("mode") or "replace")
        path = str(args.get("path") or args.get("file_path") or "(missing path)")
        start_line = args.get("start_line")
        end_line = args.get("end_line", start_line)
        if start_line is not None:
            return f"{mode} {_path_basename(path)}:{start_line}-{end_line}"
        old_string = str(args.get("old_string") or "")
        if old_string:
            return f"{mode} {_path_basename(path)} ({len(old_string)} chars anchor)"
        edits = args.get("edits")
        if isinstance(edits, list):
            return f"{mode} {_path_basename(path)} ({len(edits)} edits)"
        return f"{mode} {_path_basename(path)}"
    if tool_id == "file_write":
        mode = str(args.get("mode") or "overwrite")
        path = str(args.get("path") or "(missing path)")
        content = str(args.get("content") or "")
        return f"{mode} {_path_basename(path)} ({len(content.encode('utf-8'))} bytes)"
    if tool_id == "file_read":
        path = str(args.get("path") or "(missing path)")
        start_line = args.get("start_line")
        end_line = args.get("end_line")
        if start_line is not None or end_line is not None:
            return f"{_path_basename(path)}:{start_line or 1}-{end_line or 'end'}"
        return _path_basename(path)
    if tool_id == "list_directory":
        return str(args.get("path") or ".")
    if tool_id == "web_search":
        return _truncate_text(args.get("query") or args.get("url") or "(missing query)", limit=160)
    if tool_id == "shell_command":
        return _truncate_text(args.get("command") or "(missing command)", limit=160)
    if tool_id in {"git_status", "git_diff", "git_log"}:
        return f"path={args.get('path') or '.'}"
    if "path" in args:
        return _truncate_text(args.get("path") or "", limit=160)
    try:
        return _truncate_text(
            json.dumps(args, ensure_ascii=False, sort_keys=True, default=str),
            limit=160,
        )
    except Exception:
        return _truncate_text(str(args), limit=160)


def _tool_result_summary(tool_id: str, payload: Mapping[str, Any]) -> str:
    result = payload.get("result")
    if str(payload.get("status") or "").strip() in {"failed", "denied"}:
        return str(payload.get("error") or "tool failed")
    if isinstance(result, dict):
        if tool_id == "list_directory":
            count = result.get("count")
            total_count = result.get("total_count")
            remaining = result.get("remaining_count")
            summary = f"entries={count if count is not None else len(result.get('entries') or [])}"
            if total_count not in {None, count}:
                summary += f"/{total_count}"
            if result.get("truncated") and remaining is not None:
                summary += f" remaining={remaining}"
            return summary
        if tool_id == "file_read":
            parts: list[str] = []
            returned = result.get("returned_line_count", result.get("line_count"))
            total = result.get("total_line_count")
            if returned is not None and total is not None and total != returned:
                parts.append(f"lines={returned}/{total}")
            elif returned is not None:
                parts.append(f"lines={returned}")
            if result.get("size") is not None:
                file_size = result.get("file_size")
                if file_size is not None and file_size != result.get("size"):
                    parts.append(f"bytes={result.get('size')}/{file_size}")
                else:
                    parts.append(f"bytes={result.get('size')}")
            path = result.get("path")
            if path:
                parts.append(f"path={_path_basename(path)}")
            return " ".join(parts) or "read file"
        if tool_id == "file_write":
            path = result.get("path")
            bytes_written = result.get("bytes_written")
            if bytes_written is not None:
                return f"wrote {bytes_written} bytes to {_path_basename(path)}"
            return f"wrote {_path_basename(path)}"
        if tool_id == "file_edit":
            path = result.get("path")
            mode = result.get("mode") or "edit"
            start_line = result.get("start_line")
            end_line = result.get("end_line")
            changed = result.get("changed")
            suffix = " changed=no" if changed is False else ""
            if start_line is not None:
                return f"{mode} {_path_basename(path)}:{start_line}-{end_line}{suffix}"
            return f"{mode} {_path_basename(path)}{suffix}"
        if tool_id == "shell_command":
            stdout = str(result.get("stdout") or "")
            stderr = str(result.get("stderr") or "")
            return (
                f"exit={result.get('exit_code', '?')} "
                f"stdout={len(stdout)} chars stderr={len(stderr)} chars"
            )
        if tool_id == "web_search":
            results = result.get("results")
            if isinstance(results, list):
                return f"results={len(results)}"
            if result.get("url"):
                return _truncate_text(result.get("url"), limit=160)
            return "search completed"
        if tool_id == "git_diff":
            return (
                f"files={result.get('files_changed', 0)} "
                f"+{result.get('additions', 0)} -{result.get('deletions', 0)}"
            )
        if tool_id == "git_status":
            return (
                f"modified={len(result.get('modified', []) or [])} "
                f"untracked={len(result.get('untracked', []) or [])}"
            )
        if "path" in result:
            return _truncate_text(result.get("path") or "", limit=160)
    if payload.get("error"):
        return _truncate_text(payload.get("error"), limit=160)
    try:
        return _truncate_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, default=str),
            limit=160,
        )
    except Exception:
        return _truncate_text(str(result), limit=160)


class SuperProgressRenderer:
    """Render concise live Super DAN progress in the terminal."""

    def __init__(self, *, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self._seen: set[tuple[str, str, str]] = set()

    @staticmethod
    def _timestamp(event: Mapping[str, Any]) -> str:
        raw = str(event.get("timestamp") or "").strip()
        if raw:
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone().strftime("%H:%M:%S")
            except ValueError:
                pass
        return datetime.now().astimezone().strftime("%H:%M:%S")

    @staticmethod
    def _scope(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if text.startswith("super-dan.live."):
            text = text[len("super-dan.live.") :]
        if text.endswith("-builder"):
            return "builder"
        if text.endswith("-validator"):
            return "validator"
        if "." in text:
            text = text.split(".")[-1]
        return text

    def _emit(self, event: Mapping[str, Any], message: str) -> None:
        print(f"[{self._timestamp(event)}] {message}", flush=True)

    def _print_once(self, key: tuple[str, str, str], event: Mapping[str, Any], message: str) -> None:
        if key in self._seen:
            return
        self._seen.add(key)
        self._emit(event, message)

    def __call__(self, event: dict[str, Any]) -> None:
        if not self._enabled:
            return
        name = str(event.get("event") or "").strip()
        if not name:
            return
        if name == "run.log.started":
            objective = _truncate_text(event.get("objective") or "", limit=180)
            self._emit(event, f"[run] started: {objective}")
            return
        if name == "super.hook.runtime.started":
            profile = str(event.get("reactivity_profile") or "balanced")
            inboxes = ",".join(str(item) for item in list(event.get("inboxes") or [])[:6])
            suffix = f" inboxes={inboxes}" if inboxes else ""
            self._emit(event, f"[hooks] runtime started: reactivity={profile}{suffix}")
            return
        if name == "super.worktree.policy.configured":
            parallelism = int(event.get("worktree_parallelism") or 0)
            root = _truncate_text(event.get("worktree_root") or "", limit=120)
            self._emit(event, f"[hooks] worktree lane configured: parallelism={parallelism} root={root}")
            return
        if name == "super.hook.packet_enqueued":
            inbox_id = str(event.get("inbox_id") or "inbox")
            source = str(event.get("source_event") or "event")
            packet_type = str(event.get("packet_type") or "packet")
            depth = int(event.get("queue_depth") or 0)
            self._emit(event, f"[hooks] {inbox_id} <= {source} ({packet_type}, depth={depth})")
            return
        if name.startswith("super.inbox."):
            inbox_id = str(event.get("inbox_id") or "inbox")
            reason = _truncate_text(event.get("reason") or event.get("queue_full_action") or "", limit=120)
            action = name.rsplit(".", 1)[-1].replace("packet_", "")
            suffix = f": {reason}" if reason else ""
            self._emit(event, f"[hooks] {inbox_id} {action}{suffix}")
            return
        if name == "live.objective.normalized":
            reason = str(event.get("reason") or "objective").replace("_", " ")
            self._emit(event, f"[run] normalized {reason}")
            hint = _truncate_text(event.get("previous_failure_hint") or "", limit=180)
            if hint:
                self._emit(event, f"[run] previous validation feedback: {hint}")
            return
        if name == "provider.build.started":
            model = str(event.get("model") or event.get("requested_model") or "").strip()
            if model:
                self._emit(event, f"[model] preparing provider: {model}")
            return
        if name == "live.website_build.started":
            root = _truncate_text(event.get("artifact_root") or "", limit=120)
            self._emit(event, f"[build] website lane started: {root}")
            return
        if name in {"live.generic_execution.started", "live.generic_build.started"}:
            root = _truncate_text(event.get("workspace_root") or "", limit=120)
            self._emit(event, f"[build] generic lane started: {root}")
            return
        if name == "live.planning.started":
            root = _truncate_text(event.get("plan_root") or "", limit=120)
            self._emit(event, f"[planning] started: {root}")
            return
        if name == "live.planning.completed":
            status = str(event.get("status") or "completed")
            files = int(event.get("plan_file_count") or 0)
            self._emit(event, f"[planning] {status} files={files}")
            return
        if name == "live.plan_validation.started":
            self._emit(event, "[planning] validation started")
            return
        if name == "live.plan_validation.completed":
            verdict = "passed" if event.get("passed") else "failed"
            score = _coerce_float(event.get("overall_score"))
            self._emit(event, f"[planning] validation {verdict} {score:.2f}")
            return
        if name == "live.worktree_frontier.started":
            task_ids = ",".join(str(item) for item in (event.get("task_ids") or [])[:8])
            suffix = f": {task_ids}" if task_ids else ""
            self._emit(event, f"[worktree] frontier started{suffix}")
            return
        if name == "super.worktree.task_planned":
            task_id = _truncate_text(event.get("owner_scope") or event.get("task_id") or "", limit=120)
            self._emit(event, f"[worktree] planned {task_id}")
            return
        if name == "super.worktree.diff_admitted":
            changed = [
                _path_basename(path)
                for path in (event.get("changed_files") or [])
                if str(path).strip()
            ]
            suffix = f" changed={','.join(changed)}" if changed else ""
            self._emit(event, f"[worktree] diff admitted{suffix}")
            return
        if name == "live.worktree.diff_applied":
            task_id = str(event.get("plan_task_id") or event.get("task_id") or "task")
            changed = [
                _path_basename(path)
                for path in (event.get("changed_files") or [])
                if str(path).strip()
            ]
            suffix = f" changed={','.join(changed)}" if changed else " changed=none"
            self._emit(event, f"[worktree] applied {task_id}{suffix}")
            return
        if name == "live.worktree_task.completed":
            task_id = str(event.get("plan_task_id") or "task")
            status = str(event.get("status") or "completed")
            self._emit(event, f"[worktree] {task_id} {status}")
            return
        if name == "model.requested":
            span_id = str(event.get("span_id") or event.get("model_call_id") or "")
            round_id = str(event.get("round") or "?")
            scope = self._scope(event.get("worker_id"))
            prefix = f"[{scope}][model]" if scope else "[model]"
            model = str(event.get("model") or "(unknown)")
            tools = int(event.get("tool_count") or 0)
            self._print_once(
                ("model.requested", span_id, round_id),
                event,
                f"{prefix} request round={round_id} model={model} tools={tools}",
            )
            return
        if name == "model.responded":
            span_id = str(event.get("span_id") or event.get("model_call_id") or "")
            round_id = str(event.get("round") or "?")
            scope = self._scope(event.get("worker_id"))
            prefix = f"[{scope}][model]" if scope else "[model]"
            finish = str(event.get("finish_reason") or "").strip()
            tool_calls = [
                str(item).strip()
                for item in (event.get("tool_calls") or [])
                if str(item).strip()
            ]
            details = [f"round={round_id}"]
            if finish:
                details.append(f"finish={finish}")
            if tool_calls:
                details.append("tools=" + ",".join(tool_calls[:4]))
            self._print_once(
                ("model.responded", span_id, round_id),
                event,
                f"{prefix} response {' '.join(details)}",
            )
            return
        if name == "tool.started":
            span_id = str(event.get("span_id") or event.get("tool_call_id") or "")
            tool_id = str(event.get("tool_id") or "tool")
            scope = self._scope(event.get("worker_id"))
            prefix = f"[{scope}][tool]" if scope else "[tool]"
            summary = _tool_request_summary(tool_id, dict(event.get("arguments") or {}))
            self._print_once(
                ("tool.started", span_id, tool_id),
                event,
                f"{prefix} {tool_id}: {summary}",
            )
            return
        if name == "tool.completed":
            span_id = str(event.get("span_id") or event.get("tool_call_id") or "")
            tool_id = str(event.get("tool_id") or "tool")
            status = str(event.get("status") or "completed")
            scope = self._scope(event.get("worker_id"))
            prefix = f"[{scope}][tool]" if scope else "[tool]"
            summary = _tool_result_summary(tool_id, event)
            label = "ok" if status == "completed" else "failed"
            self._print_once(
                ("tool.completed", span_id, tool_id),
                event,
                f"{prefix} {label} {tool_id}: {summary}",
            )
            return
        if name in {"tool.failed", "tool.denied"}:
            span_id = str(event.get("span_id") or event.get("tool_call_id") or "")
            tool_id = str(event.get("tool_id") or "tool")
            scope = self._scope(event.get("worker_id"))
            prefix = f"[{scope}][tool]" if scope else "[tool]"
            message = _truncate_text(event.get("error") or name, limit=180)
            self._print_once(
                (name, span_id, tool_id),
                event,
                f"{prefix} {name.split('.')[-1]} {tool_id}: {message}",
            )
            return
        if name == "toolloop.soft_budget_nudged":
            action = str(event.get("action") or "nudge")
            phase = str(event.get("phase") or "phase")
            self._emit(event, f"[status] soft budget: {action} ({phase})")
            return
        if name == "live.validation.started":
            self._emit(event, "[validation] started")
            return
        if name == "live.validation.model_completed":
            verdict = "passed" if event.get("passed") else "failed"
            self._emit(event, f"[validation] model {verdict}")
            return
        if name == "live.validation.shell_check.started":
            command = _truncate_text(event.get("command") or "validation command", limit=140)
            self._emit(event, f"[validation] command started: {command}")
            return
        if name == "live.validation.shell_check.completed":
            verdict = "passed" if event.get("passed") else "failed"
            command = _truncate_text(event.get("command") or "validation command", limit=100)
            exit_code = event.get("exit_code")
            suffix = f" exit={exit_code}" if exit_code is not None else ""
            self._emit(event, f"[validation] command {verdict}{suffix}: {command}")
            failure = str(event.get("failure") or "").strip()
            if failure:
                self._emit(event, f"[validation] gap: {_truncate_text(failure, limit=180)}")
            return
        if name == "live.validation.completed":
            verdict = "passed" if event.get("passed") else "failed"
            score = _coerce_float(event.get("overall_score"))
            self._emit(event, f"[validation] {verdict} {score:.2f}")
            failures = [
                _truncate_text(item, limit=180)
                for item in (event.get("deterministic_failures") or [])
                if str(item).strip()
            ]
            for failure in failures[:2]:
                self._emit(event, f"[validation] gap: {failure}")
            return
        if name == "live.builder_retry.started":
            attempt = int(event.get("attempt") or 1)
            reason = _truncate_text(event.get("reason") or "no required files changed", limit=160)
            self._emit(event, f"[builder] retry {attempt} started: {reason}")
            return
        if name == "live.builder_retry.completed":
            attempt = int(event.get("attempt") or 1)
            status = str(event.get("status") or "completed")
            changed = [
                _path_basename(path)
                for path in (event.get("changed_required_files") or [])
                if str(path).strip()
            ]
            suffix = f" changed={','.join(changed)}" if changed else " changed=none"
            self._emit(event, f"[builder] retry {attempt} {status}{suffix}")
            return
        if name == "live.answer_recovery.started":
            attempt = int(event.get("attempt") or 1)
            reason = _truncate_text(event.get("reason") or "final answer missing", limit=160)
            self._emit(event, f"[answer] recovery {attempt} started: {reason}")
            return
        if name == "live.answer_recovery.completed":
            attempt = int(event.get("attempt") or 1)
            verdict = "satisfied" if event.get("answer_satisfactory") else "still missing"
            self._emit(event, f"[answer] recovery {attempt} {verdict}")
            return
        if name in {"live.website_repair.started", "live.generic_repair.started"}:
            attempt = int(event.get("attempt") or 1)
            reason = _truncate_text(event.get("reason") or "validation failed", limit=160)
            self._emit(event, f"[repair] attempt {attempt} started: {reason}")
            return
        if name in {"live.website_repair.completed", "live.generic_repair.completed"}:
            attempt = int(event.get("attempt") or 1)
            status = str(event.get("status") or "completed")
            changed = [
                _path_basename(path)
                for path in (event.get("changed_required_files") or [])
                if str(path).strip()
            ]
            suffix = f" changed={','.join(changed)}" if changed else ""
            self._emit(event, f"[repair] attempt {attempt} {status}{suffix}")
            return
        if name in {
            "live.website_build.completed",
            "live.generic_execution.completed",
            "live.generic_build.completed",
        }:
            status = str(event.get("status") or "completed")
            self._emit(event, f"[build] {status}")
            return
        if name == "run.log.completed":
            status = str(event.get("status") or "completed")
            self._emit(event, f"[done] {status}")
            return
        if name == "run.log.failed":
            message = _truncate_text(event.get("error") or "failed", limit=180)
            self._emit(event, f"[done] failed: {message}")
            return
        if name == "super.heartbeat":
            phase = str(event.get("phase") or "running")
            detail = _truncate_text(event.get("detail") or "", limit=160)
            elapsed = int(event.get("elapsed_seconds") or 0)
            suffix = f": {detail}" if detail else ""
            self._emit(event, f"[status] still running {phase} ({elapsed}s idle){suffix}")


class SuperHeartbeatMonitor:
    """Emit sparse terminal/log heartbeats while live model calls are quiet."""

    def __init__(
        self,
        *,
        event_callback,
        enabled: bool,
        idle_seconds: float = 10.0,
        repeat_seconds: float = 10.0,
        poll_seconds: float = 2.0,
    ) -> None:
        self._event_callback = event_callback
        self._enabled = bool(enabled) and event_callback is not None
        self._idle_seconds = max(0.0, float(idle_seconds))
        self._repeat_seconds = max(self._idle_seconds, float(repeat_seconds), 0.0)
        self._poll_seconds = max(0.001, float(poll_seconds))
        self._last_activity = 0.0
        self._last_heartbeat = 0.0
        self._phase = "starting"
        self._worker_id = ""
        self._detail = ""
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None

    def observe(self, event: dict[str, Any]) -> None:
        if not self._enabled:
            return
        name = str(event.get("event") or "")
        if not name or name == "super.heartbeat":
            return
        loop = asyncio.get_running_loop()
        self._last_activity = loop.time()
        worker_id = str(event.get("worker_id") or "").strip()
        if worker_id:
            self._worker_id = worker_id
        if name == "model.requested":
            self._phase = "model"
            self._detail = (
                f"round={event.get('round', '?')} "
                f"model={event.get('model') or '(unknown)'} "
                f"tools={event.get('tool_count', 0)}"
            )
            return
        if name == "model.responded":
            self._phase = "model-response"
            self._detail = f"finish={event.get('finish_reason') or 'stop'}"
            return
        if name == "tool.started":
            self._phase = "tool"
            self._detail = _tool_request_summary(
                str(event.get("tool_id") or ""),
                dict(event.get("arguments") or {}),
            )
            return
        if name == "tool.completed":
            self._phase = "post-tool"
            self._detail = str(event.get("tool_id") or "tool")
            return
        if name in {"live.planning.started", "live.plan_validation.started"}:
            self._phase = "planning"
            self._detail = str(event.get("plan_root") or "run-local plan")
            return
        if name == "live.validation.started":
            self._phase = "validation"
            self._detail = "read-only validator"
            return
        if name == "completion.completed":
            self._phase = "completion"
            self._detail = str(event.get("stop_reason") or "completed")
            return

    async def start(self) -> None:
        if not self._enabled or self._task is not None:
            return
        loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._last_activity = loop.time()
        self._last_heartbeat = 0.0
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        if self._stop_event is not None:
            self._stop_event.set()
        try:
            await self._task
        finally:
            self._task = None
            self._stop_event = None

    async def _run(self) -> None:
        stop_event = self._stop_event
        if stop_event is None:
            return
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._poll_seconds)
                break
            except asyncio.TimeoutError:
                pass
            loop = asyncio.get_running_loop()
            now = loop.time()
            idle = now - self._last_activity
            since_last = now - self._last_heartbeat if self._last_heartbeat else float("inf")
            if idle < self._idle_seconds or since_last < self._repeat_seconds:
                continue
            self._last_heartbeat = now
            self._event_callback(
                {
                    "event": "super.heartbeat",
                    "phase": self._phase,
                    "detail": self._detail,
                    "worker_id": self._worker_id,
                    "elapsed_seconds": int(idle),
                }
            )


def _is_website_objective(
    report: SuperOrganismReport,
    args: argparse.Namespace | None = None,
) -> bool:
    return False


def _super_live_choice(
    report: SuperOrganismReport,
    args: argparse.Namespace | None = None,
) -> OrchestratorChoice:
    context: dict[str, Any] = {
        "command": "super-organism",
    }
    surface_context = _surface_context_from_args(args)
    if surface_context:
        context["surface_context"] = surface_context
    surface_policy = getattr(args, "_tui_surface_policy", None) if args is not None else None
    if isinstance(surface_policy, MappingABC):
        context["surface_policy"] = dict(surface_policy)
    capability_packs = getattr(args, "_capability_packs", None) if args is not None else None
    if capability_packs:
        context["capability_packs"] = capability_packs
    code_like_live = args is not None and bool(getattr(args, "_code_like_live", False))
    workspace_root = normalize_workspace_root(str(args.workspace)) if args is not None else Path(".")
    operator_policy = _operator_intent_policy_from_objective(
        str(report.target or ""),
        workspace_root=workspace_root,
    )
    skip_workspace_context = bool(operator_policy.forbid_other_workspace_inputs)
    existing_website_workspace = (
        code_like_live
        and not skip_workspace_context
        and _existing_website_workspace_context(args)
    )
    single_file_html_workspace = (
        code_like_live
        and not skip_workspace_context
        and not existing_website_workspace
        and _single_file_html_workspace_context(args)
    )
    if operator_policy.active:
        context["operator_intent_policy"] = operator_policy.to_payload()
    if existing_website_workspace:
        context["existing_website_workspace"] = True
        context["workspace_kind"] = "website"
    elif single_file_html_workspace:
        context["single_file_html_workspace"] = True
        context["workspace_kind"] = "single_file_html"
        if _live_context_allows_mutation_signal(report):
            context["intent_signal"] = {
                "operation": "mutate",
                "artifact_target": "workspace",
                "mutation_permission": True,
                "confidence": 0.9,
                "source": "super-dan-live-context",
                "rationale": (
                    "explicit live or interactive Super DAN context targets an existing single-file HTML artifact"
                ),
                "evidence": [
                    "live_context:mutation_permission",
                    "existing_artifact:single_file_html",
                ],
            }
    base_choice = select_orchestrator(
        str(report.target or ""),
        context,
    )
    if (
        base_choice.orchestrator_id == "super-dan-live-general"
        or not code_like_live
        or not _live_context_allows_mutation_signal(report)
    ):
        return base_choice
    if code_like_live and _live_context_allows_mutation_signal(report):
        artifact_target = "workspace"
        evidence = ["live_context:mutation_permission"]
        if existing_website_workspace:
            evidence.append("existing_artifact:website")
        elif single_file_html_workspace:
            evidence.append("existing_artifact:single_file_html")
        context["intent_signal"] = {
            "operation": "mutate",
            "artifact_target": artifact_target,
            "mutation_permission": True,
            "confidence": 0.9,
            "source": "super-dan-live-context",
            "rationale": (
                "explicit live or interactive Super DAN context grants workspace mutation permission"
            ),
            "evidence": evidence,
        }
    return select_orchestrator(
        str(report.target or ""),
        context,
    )


def _live_context_allows_mutation_signal(report: SuperOrganismReport) -> bool:
    return True


def _supports_live_execution(
    report: SuperOrganismReport,
    args: argparse.Namespace | None = None,
) -> bool:
    choice = _super_live_choice(report, args)
    return choice.orchestrator_id == "super-dan-live-general"


def _live_choice_tool_ids(choice: OrchestratorChoice) -> list[str]:
    return [str(tool_id) for tool_id in choice.tool_policy.get("allowed_tool_ids") or []]


def _live_choice_preferred_tool_ids(choice: OrchestratorChoice, fallback: Sequence[str]) -> list[str]:
    preferred = choice.tool_policy.get("preferred_tool_ids")
    if isinstance(preferred, (list, tuple)):
        return [str(tool_id) for tool_id in preferred]
    return list(fallback)


def _live_choice_read_only_tool_ids(choice: OrchestratorChoice) -> list[str]:
    read_only = {
        "list_directory",
        "file_read",
        "workspace_check",
        "web_search",
        "current_datetime",
        "browser_tabs",
        "browser_inspect",
        "browser_wait",
        "browser_extract",
        "browser_screenshot",
        "desktop_observe",
        "git_status",
        "git_diff",
        "git_log",
    }
    return [tool_id for tool_id in _live_choice_tool_ids(choice) if tool_id in read_only]


def _live_choice_required_files(choice: OrchestratorChoice) -> list[str]:
    files = choice.artifact_policy.get("required_files")
    if isinstance(files, (list, tuple)) and files:
        return [str(filename) for filename in files]
    return ["index.html", "styles.css", "app.js", "README.md"]


def _live_choice_existing_preferred_coordinated_files(choice: OrchestratorChoice) -> int:
    value = choice.artifact_policy.get(
        "existing_website_preferred_coordinated_files",
        choice.artifact_policy.get("existing_website_min_changed_files", 2),
    )
    try:
        return max(int(value), 1)
    except (TypeError, ValueError):
        return 2


def _live_choice_template_phrases(choice: OrchestratorChoice) -> list[str]:
    phrases = choice.acceptance_policy.get("template_phrases")
    if isinstance(phrases, (list, tuple)):
        return [str(phrase) for phrase in phrases]
    return []


def _single_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _has_any_word(text: str, words: Sequence[str]) -> bool:
    padded = f" {text} "
    return any(f" {word} " in padded for word in words)


def _vague_website_continuation_applies(
    objective: str,
    *,
    existing_website: bool,
) -> bool:
    text = _single_line(objective).lower()
    if not text:
        return False
    site_referents = ("website", "site", "page", "frontend", "landing page")
    has_site_referent = any(phrase in text for phrase in site_referents)
    if not has_site_referent and not existing_website:
        return False
    continuation_phrases = (
        "keep patching",
        "continue patching",
        "keep improving",
        "continue improving",
        "keep working",
        "keep going",
        "patch this",
        "patch it",
        "make it better",
        "improve it",
        "continue",
    )
    if not any(phrase in text for phrase in continuation_phrases):
        return False
    specific_words = (
        "add",
        "remove",
        "fix",
        "redesign",
        "rewrite",
        "animation",
        "responsive",
        "mobile",
        "copy",
        "color",
        "layout",
        "section",
        "component",
        "changelog",
        "readme",
        "docs",
        "button",
        "form",
        "pricing",
        "hero",
    )
    return not _has_any_word(text, specific_words)


def _latest_super_dan_failure_hint(
    workspace_root: Path,
    *,
    max_chars: int = 420,
) -> str:
    run_root = _super_run_root(workspace_root)
    if not run_root.exists():
        return ""
    event_logs = sorted(
        run_root.glob("turn-*/events.jsonl"),
        key=lambda path: path.parent.name,
        reverse=True,
    )
    for event_log in event_logs:
        try:
            lines = event_log.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            event = str(row.get("event") or "")
            failed = row.get("passed") is False or str(row.get("status") or "") == "failed"
            if event not in {"live.validation.completed", "run.log.completed"} or not failed:
                continue
            parts: list[str] = []
            deterministic_failures = row.get("deterministic_failures")
            if isinstance(deterministic_failures, list):
                parts.extend(str(item).strip() for item in deterministic_failures if str(item).strip())
            for key in ("error", "repair_brief", "comparison_note"):
                value = str(row.get(key) or "").strip()
                if value:
                    parts.append(value)
            hint = _single_line(" ".join(parts))
            if hint:
                return hint[:max_chars]
    return ""


def _live_website_objective_context(
    report: SuperOrganismReport,
    *,
    workspace_root: Path,
    required_paths: Sequence[Path],
) -> dict[str, Any]:
    original = _single_line(report.target)
    existing_required_files = [
        path.name for path in required_paths if path.exists()
    ]
    existing_website = "index.html" in existing_required_files or len(existing_required_files) >= 2
    previous_failure_hint = (
        _latest_super_dan_failure_hint(workspace_root) if existing_website else ""
    )
    if not _vague_website_continuation_applies(
        original,
        existing_website=existing_website,
    ):
        return {
            "normalized": False,
            "reason": "",
            "original_objective": original,
            "effective_objective": original,
            "existing_required_files": existing_required_files,
            "previous_failure_hint": previous_failure_hint,
        }
    default_brief = (
        "Continue patching this existing website with a concrete maintainability pass. "
        "Prefer coordinated, inspectable changes across index.html, styles.css, app.js, and README.md. "
        "Preserve the current site subject unless the existing files make it clearly obsolete. "
        "Add or improve modular sections, editable content blocks or a component map, a changelog or patch-notes area, "
        "and explicit patch/extension guidance so future edits are easy. "
        "Keep the patch bounded, static, and dependency-free."
    )
    if previous_failure_hint:
        default_brief = (
            f"{default_brief} Address this previous validation feedback: {previous_failure_hint}"
        )
    return {
        "normalized": True,
        "reason": "vague_website_continuation",
        "original_objective": original,
        "effective_objective": default_brief,
        "existing_required_files": existing_required_files,
        "previous_failure_hint": previous_failure_hint,
    }


def _argv_has_option(argv: Sequence[str], option: str) -> bool:
    return any(token == option or token.startswith(f"{option}=") for token in argv)


def _live_model_configured(requested_model: str | None) -> bool:
    if str(requested_model or "").strip():
        return True
    config = resolve_config()
    if str(config.get("model") or "").strip():
        return True
    engine_config = build_engine_config_from_env()
    fallback = str(engine_config.llm_default_model or "").strip()
    return bool(fallback and fallback != "stub-model")


def _should_implicit_live(report: SuperOrganismReport, args: argparse.Namespace) -> bool:
    live_probe_args = copy.copy(args)
    setattr(live_probe_args, "_code_like_live", True)
    if (
        bool(getattr(args, "live", False))
        or bool(getattr(args, "plan_only", False))
        or not str(getattr(args, "target", "") or "").strip()
        or not _supports_live_execution(report, live_probe_args)
    ):
        return False
    if not (
        bool(getattr(args, "_stdin_is_tty", False))
        or bool(getattr(args, "_model_explicit", False))
    ):
        return False
    return _live_model_configured(getattr(args, "model", None))


def _should_materialize_website(report: SuperOrganismReport, args: argparse.Namespace) -> bool:
    return False


def _artifact_root(args: argparse.Namespace) -> Path:
    workspace = Path(str(args.workspace)).expanduser()
    artifact_dir = Path(str(args.artifact_dir)).expanduser()
    if artifact_dir.is_absolute():
        return artifact_dir
    return workspace / artifact_dir


def _materialize_website_artifact(report: SuperOrganismReport, args: argparse.Namespace) -> list[Path]:
    root = _artifact_root(args)
    root.mkdir(parents=True, exist_ok=True)
    files = {
        root / "index.html": _render_website_html(report),
        root / "styles.css": _render_website_css(),
        root / "app.js": _render_website_js(report),
        root / "README.md": _render_website_readme(report),
    }
    for path, content in files.items():
        path.write_text(content, encoding="utf-8")
    return list(files)


def _resolve_live_model(requested_model: str | None) -> str:
    text = str(requested_model or "").strip()
    if text:
        return text
    config = resolve_config()
    env_model = str(config.get("model") or "").strip()
    if env_model:
        return env_model
    engine_config = build_engine_config_from_env()
    fallback = str(engine_config.llm_default_model or "").strip()
    if fallback and fallback != "stub-model":
        return fallback
    raise ValueError(
        "Super DAN live mode requires --model or a configured DAN_MODEL/DAN_LLM_MODEL"
    )


def _build_live_provider(
    model: str,
    *,
    api_key: str | None,
    base_url: str | None,
) -> LLMProvider:
    return live_gateway.build_gateway_backed_live_provider(
        model,
        api_key=api_key,
        base_url=base_url,
    )


def _existing_website_workspace_context(args: argparse.Namespace) -> bool:
    workspace_root = normalize_workspace_root(str(args.workspace))
    if workspace_root.name.lower() in {"website", "site", "web", "public", "dist"}:
        return True
    artifact_dir = Path(str(getattr(args, "artifact_dir", "website") or "website")).expanduser()
    candidate_roots = [workspace_root]
    if artifact_dir.is_absolute():
        candidate_roots.append(artifact_dir.resolve(strict=False))
    else:
        candidate_roots.append((workspace_root / artifact_dir).resolve(strict=False))
    for root in candidate_roots:
        if not (root / "index.html").exists():
            continue
        companion_count = sum(
            1
            for filename in ("styles.css", "app.js", "README.md")
            if (root / filename).exists()
        )
        if companion_count >= 1:
            return True
    return False


def _single_file_html_workspace_context(args: argparse.Namespace) -> bool:
    workspace_root = normalize_workspace_root(str(args.workspace))
    artifact_dir = Path(str(getattr(args, "artifact_dir", "website") or "website")).expanduser()
    candidate_roots = [workspace_root]
    if artifact_dir.is_absolute():
        candidate_roots.append(artifact_dir.resolve(strict=False))
    else:
        candidate_roots.append((workspace_root / artifact_dir).resolve(strict=False))
    for root in candidate_roots:
        if root.name.lower() in {"website", "site", "web", "public", "dist"}:
            continue
        if not (root / "index.html").exists():
            continue
        companion_count = sum(
            1
            for filename in ("styles.css", "app.js", "README.md")
            if (root / filename).exists()
        )
        if companion_count == 0:
            return True
    return False


def _workspace_should_be_website_artifact_root(
    args: argparse.Namespace,
    choice: OrchestratorChoice,
) -> bool:
    if bool(getattr(args, "_artifact_dir_explicit", False)):
        return False
    if not bool(getattr(args, "_code_like_live", False)):
        return False
    artifact_dir = Path(str(args.artifact_dir or "")).expanduser()
    if artifact_dir.is_absolute() or artifact_dir.as_posix().strip("/") not in {
        "website",
        "",
    }:
        return False
    workspace_root = normalize_workspace_root(str(args.workspace))
    if workspace_root.name.lower() in {"website", "site", "web", "public", "dist"}:
        return True
    required_files = _live_choice_required_files(choice)
    existing = [
        filename for filename in required_files if (workspace_root / filename).exists()
    ]
    return "index.html" in existing or len(existing) >= 3


def _live_artifact_layout(
    args: argparse.Namespace,
    choice: OrchestratorChoice,
) -> tuple[Path, Path, list[str], list[Path]]:
    artifact_dir = Path(str(args.artifact_dir)).expanduser()
    required_files = _live_choice_required_files(choice)
    if _workspace_should_be_website_artifact_root(args, choice):
        workspace_root = normalize_workspace_root(str(args.workspace))
        artifact_root = workspace_root
        relative_files = list(required_files)
    elif artifact_dir.is_absolute():
        workspace_root = artifact_dir.resolve(strict=False)
        artifact_root = workspace_root
        relative_files = list(required_files)
    else:
        workspace_root = normalize_workspace_root(str(args.workspace))
        artifact_root = (workspace_root / artifact_dir).resolve(strict=False)
        relative_files = [(artifact_dir / filename).as_posix() for filename in required_files]
    required_paths = [(workspace_root / relative_path).resolve(strict=False) for relative_path in relative_files]
    return workspace_root, artifact_root, relative_files, required_paths


def _live_expected_return_shape() -> str:
    return json.dumps(
        {
            "candidate_id": "super-dan-live-general-001",
            "request_understanding": {
                "request_kind": "software | research | document | general",
                "aspect_reviews": [
                    {
                        "aspect": "audience_or_user_need",
                        "question": "what must be understood before doing the work?",
                        "request_comment": "model-authored rule tailored to this request",
                        "confidence": 0.8,
                    }
                ],
                "confidence_scoped_acceptance": [
                    {
                        "criterion": "request-specific completion rule",
                        "confidence": 0.8,
                        "action": "do_or_explain",
                    }
                ],
                "stop_rule": "request-specific condition for when it is honest to stop",
            },
            "task_graph_update": {
                "scope": "whole_graph | branch_local",
                "changed_task_ids": ["1-1"],
                "reason": "model-authored graph revision or sequencing update",
            },
            "answer": "short user-facing summary of what was found or changed",
            "change_summary": ["short summary of concrete files written"],
            "target_files": ["path/to/changed-file.md"],
            "test_plan": ["inspect the changed artifact or run a focused verification command"],
            "risks": ["remaining limitations or assumptions"],
            "files_created": ["path/to/changed-file.md"],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _live_answer_return_shape() -> str:
    return json.dumps(
        {
            "candidate_id": "super-dan-live-review-001",
            "request_understanding": {
                "request_kind": "software | research | document | general",
                "aspect_reviews": [
                    {
                        "aspect": "answer_scope",
                        "question": "what does the operator need answered?",
                        "request_comment": "model-authored rule tailored to this request",
                        "confidence": 0.8,
                    }
                ],
                "confidence_scoped_acceptance": [
                    {
                        "criterion": "request-specific answer rule",
                        "confidence": 0.8,
                        "action": "do_or_explain",
                    }
                ],
                "stop_rule": "request-specific condition for when it is honest to stop",
            },
            "task_graph_update": {
                "scope": "whole_graph | branch_local",
                "changed_task_ids": ["1-1"],
                "reason": "model-authored graph revision or sequencing update",
            },
            "answer": "substantive in-session answer shaped by the operator's request; include only sections and details that help the operator understand the result",
            "summary": ["key finding or status point"],
            "risks": ["remaining limitations or assumptions"],
            "validation": ["inspection evidence used for the answer"],
            "remaining_work": ["follow-up work that would require explicit permission or a separate request"],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _live_validation_return_shape() -> str:
    return json.dumps(
        {
            "passed": True,
            "overall_score": 0.9,
            "dimension_scores": {
                "objective_alignment": 0.9,
                "artifact_specificity": 0.9,
                "execution_quality": 0.9,
            },
            "repair_brief": "",
            "missing_requirements": [],
            "blocking_current_task_failures": [],
            "deferred_task_gaps": [],
            "remaining_work": [],
            "ready_next_task_ids": [],
            "dependency_revisions": [],
            "task_graph_update": {
                "scope": "branch_local",
                "changed_task_ids": ["1-1"],
                "reason": "validator sequencing update for the current branch only",
            },
            "aspect_coverage": [
                {
                    "aspect": "what",
                    "status": "satisfied | deferred | blocked",
                    "evidence": ["changed file, check, or inspected finding"],
                    "gap": "",
                }
            ],
            "completion_scope": "full_objective | current_frontier",
            "comparison_note": "The result materially satisfies the operator objective and is not just a generic demo shell.",
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _live_plan_return_shape() -> str:
    return json.dumps(
        {
            "request_understanding": {
                "request_kind": "software | research | document | general",
                "aspect_reviews": [
                    {
                        "aspect": "target_scope",
                        "question": "what must be understood before planning?",
                        "request_comment": "model-authored rule tailored to this request",
                        "confidence": 0.8,
                    }
                ],
                "confidence_scoped_acceptance": [
                    {
                        "criterion": "request-specific planning/execution rule",
                        "confidence": 0.8,
                        "action": "do_or_explain",
                    }
                ],
                "stop_rule": "request-specific condition for when it is honest to stop",
            },
            "task_graph_update": {
                "scope": "whole_graph",
                "changed_task_ids": ["1-1", "1-2"],
                "reason": "initial model-authored task graph",
            },
            "plan_files": [".dan-super/runs/turn-01/plans/1-coherent-phase.md"],
            "phase_summary": ["1: coherent large feature chunk"],
            "task_graph": [
                {
                    "task_id": "1-1",
                    "parent_id": "1",
                    "branch_id": "1",
                    "goal": "first executable task",
                    "depends_on": [],
                    "owned_paths": ["relative/path"],
                    "deliverables": ["relative/path"],
                    "validation": ["focused check"],
                    "parallel_safe": True,
                    "risk": "low",
                    "confidence": 0.8,
                }
            ],
            "ready_task_ids": ["1-1"],
            "deferred_task_ids": ["1-2"],
            "first_build_slice": ["1-1"],
            "notes": ["why the ready frontier is the right work to execute now"],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _live_plan_validation_return_shape() -> str:
    return json.dumps(
        {
            "passed": True,
            "overall_score": 0.9,
            "task_graph": [
                {
                    "task_id": "1-1",
                    "parent_id": "1",
                    "branch_id": "1",
                    "goal": "first executable task",
                    "depends_on": [],
                    "owned_paths": ["relative/path"],
                    "deliverables": ["relative/path"],
                    "validation": ["focused check"],
                    "parallel_safe": True,
                }
            ],
            "ready_task_ids": ["1-1"],
            "deferred_task_ids": ["1-2"],
            "first_build_slice": ["1-1"],
            "blocking_issues": [],
            "suggested_fixes": [],
            "dependency_revisions": [],
            "task_graph_update": {
                "scope": "branch_local",
                "changed_task_ids": ["1-1"],
                "reason": "plan-validator graph correction scoped to affected branch",
            },
            "comparison_note": "The numeric plan is coherent and ready for execution.",
        },
        ensure_ascii=False,
        sort_keys=True,
    )


_REQUEST_UNDERSTANDING_ASPECTS: tuple[tuple[str, str], ...] = (
    ("who", "Who is affected or served by the request?"),
    ("what", "What concrete deliverable or change is being requested?"),
    ("where", "Where should the work happen or be saved?"),
    ("how", "How should DAN proceed, including constraints and allowed tools?"),
    ("quality", "What hidden quality criteria matter beyond artifact shape?"),
    ("evidence", "What evidence should prove the work is actually done?"),
)


_REQUEST_UNDERSTANDING_STAGE_GUIDANCE: dict[str, tuple[str, ...]] = {
    "planner": (
        "Turn the aspect comments into an acceptance contract with atomic criteria, likely failure modes, and evidence gates.",
        "Decompose around ways the artifact could fail, not only around visible sections or files.",
        "For broad work, name cheap alternatives, falsification checks, or decision points before committing to a design.",
    ),
    "plan_validator": (
        "Act as an independent acceptance gate for the plan; compare each aspect comment against concrete plan coverage.",
        "Reject plans that can produce artifact shape without quality gates, evidence tasks, or a credible ready frontier.",
        "Require a requirement-to-evidence path for the current frontier and clearly deferred downstream work.",
    ),
    "builder": (
        "Before finalizing, attempt every high-confidence criterion that can be satisfied with the enabled tools.",
        "Build substantive behavior, evidence, or content; do not substitute plausible artifact shape for hidden quality.",
        "When a criterion cannot be verified or completed now, name the blocker or remaining work explicitly.",
    ),
    "worktree": (
        "Complete the assigned slice while preserving the whole-request quality contract.",
        "Keep sibling and deferred tasks out of scope, but make the owned slice evidence-backed and integration-safe.",
        "Return evidence for the owned paths rather than a prose claim that the slice is probably done.",
    ),
    "builder_retry": (
        "Treat the retry as recovery from missing durable progress; make the smallest meaningful target update that advances the objective.",
        "Use the aspect comments to choose a write that repairs a real requirement gap, not just a placeholder artifact.",
        "If the enabled tools or policy prevent the needed write, report the precise blocker instead of inventing completion.",
    ),
    "validator": (
        "Act as the independent inspector: compare changed artifacts against the aspect comments and acceptance criteria.",
        "Fail artifact-shape-only results, happy-path-only demos, unsupported claims, or missing evidence for confidently doable criteria.",
        "Populate aspect_coverage for who/what/where/how/quality/evidence with status, evidence, and any remaining gap.",
    ),
    "repair": (
        "Repair the highest-impact failed criterion first, localized to the affected files or artifact sections.",
        "Fix evidence and quality gaps directly; do not cover them with more persuasive prose.",
        "Preserve validated substance and rerun or describe the focused checks that prove the repair addressed the failure.",
    ),
}


def _request_understanding_kind(objective: str) -> str:
    text = _single_line(objective).lower()
    if not text:
        return "general"
    if any(phrase in text for phrase in ("research", "paper", "literature", "experiment", "citation")):
        return "research"
    if any(phrase in text for phrase in ("website", "landing page", "web app", "frontend", "page")):
        return "website"
    if any(phrase in text for phrase in ("app", "tool", "cli", "api", "dashboard", "feature", "bug", "test")):
        return "software"
    if any(phrase in text for phrase in ("report", "memo", "markdown", "document", "brief")):
        return "document"
    return "general"


def _request_understanding_payload(
    objective: str,
    *,
    workspace_root: Path,
    operator_intent_policy: OperatorIntentPolicy | None = None,
) -> dict[str, Any]:
    """Build a compact rule-generation brief for live execution.

    The brief is intentionally deterministic, but it is only meta-guidance. Live
    planner/builder stages must generate the concrete request-specific aspect
    reviews, acceptance criteria, and stop rule instead of inheriting a fixed
    fallback checklist.
    """

    policy = operator_intent_policy or OperatorIntentPolicy()
    clean_objective = _single_line(objective)
    kind = _request_understanding_kind(clean_objective)
    target_paths = _explicit_objective_artifact_paths(
        clean_objective,
        workspace_root=workspace_root,
    )
    if not target_paths:
        target_paths = list(policy.target_artifacts)
    rule_generation_brief = [
        "Generate the actual aspect reviews, acceptance criteria, and stop rule from the operator's exact wording and the active intent policy.",
        "Choose the response shape before planning work: in-session answer, inspection findings, workspace edit, saved artifact, or clarification.",
        "Default explain, review, summarize, check, status, and 'what is this' style requests to an in-session answer unless the operator explicitly asks to save, export, edit, or create a named artifact.",
        "Do not turn a casual explanation request into document creation; file-existence checks are valid acceptance criteria only when persistence is part of the request.",
        "Generated criteria must test semantic delivery and evidence, not only tool success, artifact existence, or a completed status event.",
        "When intent is uncertain, generate criteria that answer or explain the blocker instead of inventing extra workspace work.",
    ]
    if target_paths:
        rule_generation_brief.append(
            f"Use explicit target path hints as context, but still decide whether the operator asked for a file change: {', '.join(target_paths[:6])}."
        )
    else:
        rule_generation_brief.append(
            f"Use the active workspace root only as context unless the generated rules justify workspace work: {workspace_root}."
        )
    if policy.forbid_other_workspace_inputs:
        rule_generation_brief.append(
            "The generated rules must honor the operator's source boundary and avoid relying on other workspace inputs."
        )
    if not policy.allow_existing_artifact_reuse:
        rule_generation_brief.append(
            "The generated rules must not depend on existing artifacts as source material."
        )
    if not policy.allow_shell_command:
        rule_generation_brief.append("The generated rules must not require shell commands.")
    if not policy.allow_workspace_mutation:
        rule_generation_brief.append(
            "The generated rules must treat the deliverable as an in-session answer or inspection result, not a workspace mutation."
        )

    return {
        "schema": "super_dan_request_understanding_v1",
        "source": "rule_generation_brief",
        "request_kind": kind,
        "original_request": clean_objective,
        "workspace_root": str(workspace_root),
        "target_paths": list(target_paths),
        "rule_generation_brief": rule_generation_brief,
        "aspect_reviews": [],
        "confidence_scoped_acceptance": [],
        "stop_rule": "",
    }


def _request_understanding_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    scalar = _single_line(value)
    return [scalar] if scalar else []


def _normalize_model_request_understanding_payload(
    value: Any,
    *,
    fallback: Mapping[str, Any],
) -> dict[str, Any] | None:
    parsed = parse_jsonish_payload(value)
    if not isinstance(parsed, Mapping):
        return None
    nested = parsed.get("request_understanding")
    if isinstance(nested, Mapping):
        parsed = nested

    aspect_items: list[dict[str, Any]] = []
    raw_aspects = (
        parsed.get("aspect_reviews")
        or parsed.get("understanding_rules")
        or parsed.get("rules")
        or []
    )
    for index, item in enumerate(_request_understanding_list(raw_aspects), start=1):
        if isinstance(item, Mapping):
            aspect = _single_line(
                item.get("aspect")
                or item.get("name")
                or item.get("dimension")
                or f"rule_{index}"
            )
            question = _single_line(
                item.get("question")
                or item.get("prompt")
                or "What must be understood for this request?"
            )
            comment = _single_line(
                item.get("request_comment")
                or item.get("comment")
                or item.get("rule")
                or item.get("summary")
                or item.get("criterion")
            )
            confidence = _coerce_float(item.get("confidence") if item.get("confidence") is not None else 0.8)
        else:
            aspect = f"rule_{index}"
            question = "What must be understood for this request?"
            comment = _single_line(item)
            confidence = 0.8
        if not aspect or not comment:
            continue
        aspect_items.append(
            {
                "aspect": aspect[:80],
                "question": question[:180],
                "request_comment": comment[:500],
                "confidence": max(0.0, min(1.0, confidence)),
            }
        )

    criteria_items: list[dict[str, Any]] = []
    raw_criteria = (
        parsed.get("confidence_scoped_acceptance")
        or parsed.get("acceptance_criteria")
        or parsed.get("criteria")
        or []
    )
    for item in _request_understanding_list(raw_criteria):
        if isinstance(item, Mapping):
            criterion = _single_line(
                item.get("criterion")
                or item.get("rule")
                or item.get("summary")
                or item.get("request_comment")
            )
            confidence = _coerce_float(item.get("confidence") if item.get("confidence") is not None else 0.8)
            action = _single_line(item.get("action") or "do_or_explain") or "do_or_explain"
        else:
            criterion = _single_line(item)
            confidence = 0.8
            action = "do_or_explain"
        if not criterion:
            continue
        criteria_items.append(
            {
                "criterion": criterion[:500],
                "confidence": max(0.0, min(1.0, confidence)),
                "action": action[:80],
            }
        )

    stop_rule = _single_line(parsed.get("stop_rule") or parsed.get("completion_rule") or "")
    if not aspect_items and not criteria_items and not stop_rule:
        return None

    return {
        "schema": "super_dan_request_understanding_v1",
        "source": "model_authored",
        "request_kind": _single_line(parsed.get("request_kind") or fallback.get("request_kind") or "general"),
        "original_request": _single_line(parsed.get("original_request") or fallback.get("original_request") or ""),
        "workspace_root": _single_line(parsed.get("workspace_root") or fallback.get("workspace_root") or ""),
        "target_paths": _super_plan_string_list(parsed.get("target_paths") or fallback.get("target_paths") or []),
        "aspect_reviews": aspect_items or list(fallback.get("aspect_reviews") or []),
        "confidence_scoped_acceptance": criteria_items or list(fallback.get("confidence_scoped_acceptance") or []),
        "stop_rule": stop_rule or _single_line(fallback.get("stop_rule") or ""),
    }


def _extract_request_understanding_from_outputs(
    outputs: Any,
    *,
    fallback: Mapping[str, Any],
) -> dict[str, Any] | None:
    parsed = parse_jsonish_payload(outputs)
    if isinstance(parsed, Mapping):
        normalized = _normalize_model_request_understanding_payload(parsed, fallback=fallback)
        if normalized is not None:
            return normalized
        for key in ("result", "text", "answer", "final_response"):
            if key in parsed:
                nested = _normalize_model_request_understanding_payload(parsed.get(key), fallback=fallback)
                if nested is not None:
                    return nested
    return None


def _request_understanding_contract(
    payload: Mapping[str, Any] | None,
    *,
    stage: str | None = None,
) -> str:
    if not isinstance(payload, Mapping) or not payload:
        return ""
    stage_key = str(stage or "").strip()
    aspect_lines: list[str] = []
    for item in payload.get("aspect_reviews") or []:
        if not isinstance(item, Mapping):
            continue
        aspect = str(item.get("aspect") or "").strip()
        comment = _single_line(item.get("request_comment") or "")
        if aspect and comment:
            aspect_lines.append(f"- {aspect}: {comment}")
    criteria_lines: list[str] = []
    for item in payload.get("confidence_scoped_acceptance") or []:
        if not isinstance(item, Mapping):
            continue
        criterion = _single_line(item.get("criterion") or "")
        if criterion:
            criteria_lines.append(f"- {criterion}")
    brief_lines = [
        _single_line(item)
        for item in _request_understanding_list(payload.get("rule_generation_brief") or [])
        if _single_line(item)
    ]
    if not aspect_lines and not criteria_lines and not brief_lines:
        return ""
    lines = [
        "Request rule-generation brief: keep end-to-end responsibility, but execute stage-by-stage with explicit gates. "
        "The fixed text below is meta-guidance only; generate the concrete request-specific aspect reviews, "
        "acceptance criteria, and stop rule before deciding what counts as complete.",
    ]
    if stage_key:
        lines.append(f"Current stage: {stage_key}.")
    stage_guidance = _REQUEST_UNDERSTANDING_STAGE_GUIDANCE.get(stage_key, ())
    if stage_guidance:
        lines.append("Stage-specific request handling:")
        lines.extend(f"- {item}" for item in stage_guidance)
    if brief_lines:
        lines.append("Rules brief for the model to turn into request-specific criteria:")
        lines.extend(f"- {item}" for item in brief_lines[:12])
    if aspect_lines:
        lines.append("Generated aspect review comments:")
        lines.extend(aspect_lines[:8])
    if criteria_lines:
        lines.append("Generated confidence-scoped acceptance criteria:")
        lines.extend(criteria_lines[:10])
    stop_rule = _single_line(payload.get("stop_rule") or "")
    if stop_rule:
        lines.append(f"Stop rule: {stop_rule}")
    lines.append(
        "When your stage returns structured JSON, include `request_understanding` with model-authored "
        "`aspect_reviews`, `confidence_scoped_acceptance`, and `stop_rule`; keep only details useful to the operator."
    )
    return "\n".join(lines)


def _super_report_evidence_blocks(report: SuperOrganismReport) -> list[dict[str, Any]]:
    return [
        {
            "label": f"Super DAN {report.cell_count}-cell contract",
            "content": report.final_memo,
            "source": "super_organism_report",
            "trust_label": "advisory",
        },
        {
            "label": "Delivery nodes",
            "content": json.dumps(
                [node.model_dump(mode="json") for node in report.delivery_plan],
                ensure_ascii=False,
                sort_keys=True,
            ),
            "source": "super_organism_report",
            "trust_label": "advisory",
        },
        {
            "label": "Coordination tickets",
            "content": json.dumps(
                [ticket.model_dump(mode="json") for ticket in report.coordination_tickets],
                ensure_ascii=False,
                sort_keys=True,
            ),
            "source": "super_organism_report",
            "trust_label": "advisory",
        },
        {
            "label": "Handoff packets",
            "content": json.dumps(
                [packet.model_dump(mode="json") for packet in report.handoff_packets],
                ensure_ascii=False,
                sort_keys=True,
            ),
            "source": "super_organism_report",
            "trust_label": "advisory",
        },
        {
            "label": "Final audit gate",
            "content": json.dumps(
                (report.final_audit.model_dump(mode="json") if report.final_audit is not None else {}),
                ensure_ascii=False,
                sort_keys=True,
            ),
            "source": "super_organism_report",
            "trust_label": "advisory",
        },
    ]


def _selected_super_dan_skill_mentions_from_args(args: argparse.Namespace | None) -> list[str]:
    return skill_invocation.selected_skill_mentions_from_args(args)


def _selected_super_dan_skill_preflight_notes_from_args(args: argparse.Namespace | None) -> list[str]:
    return skill_invocation.selected_skill_preflight_notes_from_args(args)


def _brief_with_explicit_super_dan_skills(
    brief: WorkerBrief,
    *,
    args: argparse.Namespace | None = None,
) -> WorkerBrief:
    selected = _selected_super_dan_skill_mentions_from_args(args)
    if not selected:
        return brief
    preflight_notes = _selected_super_dan_skill_preflight_notes_from_args(args)
    existing = _explicit_super_dan_skill_values(brief)
    source = str(getattr(args, "_selected_skill_source", "") or "operator")
    combined: list[str] = []
    seen: set[str] = set()
    for value in [*existing, *selected]:
        normalized = value.lower().lstrip("$")
        if normalized and normalized not in seen:
            combined.append(normalized)
            seen.add(normalized)
    return brief.model_copy(
        update={
            "input_payload": {
                **dict(brief.input_payload),
                "explicit_skill_ids": combined,
                "selected_skill_mentions": combined,
                "selected_skill_source": source,
                **({"selected_skill_preflight": preflight_notes} if preflight_notes else {}),
            },
            "metadata": {
                **dict(brief.metadata),
                "explicit_skill_ids": combined,
                "selected_skill_mentions": combined,
                "selected_skill_source": source,
                **({"selected_skill_preflight": preflight_notes} if preflight_notes else {}),
            },
        }
    )


def _surface_attachments_from_args(args: argparse.Namespace | None) -> list[dict[str, Any]]:
    if args is None:
        return []
    raw = getattr(args, "_surface_attachments", None)
    if not isinstance(raw, SequenceABC) or isinstance(raw, (str, bytes)):
        return []
    return [dict(item) for item in raw if isinstance(item, MappingABC)]


def _surface_image_attachments_from_args(args: argparse.Namespace | None) -> list[dict[str, Any]]:
    if args is None:
        return []
    raw = getattr(args, "_surface_image_attachments", None)
    if not isinstance(raw, SequenceABC) or isinstance(raw, (str, bytes)):
        raw = getattr(args, "_surface_attachments", None)
    if not isinstance(raw, SequenceABC) or isinstance(raw, (str, bytes)):
        return []
    images: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, MappingABC):
            continue
        kind = str(item.get("kind") or "").strip().lower()
        if kind and kind not in {"image", "figure"}:
            continue
        images.append(dict(item))
    return images


def _surface_history_from_args(args: argparse.Namespace | None) -> list[dict[str, str]]:
    if args is None:
        return []
    raw = getattr(args, "_surface_history", None)
    if not isinstance(raw, SequenceABC) or isinstance(raw, (str, bytes)):
        return []
    history: list[dict[str, str]] = []
    for item in raw[-12:]:
        if not isinstance(item, MappingABC):
            continue
        role = str(item.get("role") or "").strip()
        content = " ".join(str(item.get("content") or "").split())
        if role in {"user", "assistant"} and content:
            history.append({"role": role, "content": content[:1200]})
    return history


def _surface_context_from_args(args: argparse.Namespace | None) -> dict[str, Any]:
    if args is None:
        return {}
    raw = getattr(args, "_surface_context", None)
    return dict(raw) if isinstance(raw, MappingABC) else {}


_SURFACE_ALREADY_KNOWN_INSTRUCTION = (
    "Here is what we already know from the recent surface conversation. Treat clearly stated prior assistant "
    "findings as starting facts, not hypotheses to rediscover, unless current file fingerprints or validation "
    "output contradict them. Start by acting on these facts; do not repeat broad discovery before the first "
    "material workspace edit."
)


def _surface_already_known_context(history: Sequence[Mapping[str, str]]) -> str:
    for item in reversed(history):
        if not isinstance(item, MappingABC):
            continue
        if str(item.get("role") or "").strip() != "assistant":
            continue
        content = " ".join(str(item.get("content") or "").split())
        if content:
            return content[:1800]
    return ""


def _brief_with_surface_conversation(
    brief: WorkerBrief,
    *,
    args: argparse.Namespace | None = None,
) -> WorkerBrief:
    history = _surface_history_from_args(args)
    context = _surface_context_from_args(args)
    if not history and not context:
        return brief
    payload_update = {
        **dict(brief.input_payload),
        "surface_history": history,
        "surface_context": context,
    }
    already_known = _surface_already_known_context(history)
    if already_known:
        payload_update["surface_already_known"] = {
            "instruction": _SURFACE_ALREADY_KNOWN_INSTRUCTION,
            "content": already_known,
        }
    metadata_update = {
        **dict(brief.metadata),
        "surface_history": history,
        "surface_context": context,
    }
    contract_snippets = list(brief.contract_snippets)
    if already_known and _SURFACE_ALREADY_KNOWN_INSTRUCTION not in contract_snippets:
        contract_snippets.append(_SURFACE_ALREADY_KNOWN_INSTRUCTION)
    return brief.model_copy(
        update={
            "input_payload": payload_update,
            "metadata": metadata_update,
            "contract_snippets": contract_snippets,
        }
    )


def _brief_with_surface_attachments(
    brief: WorkerBrief,
    *,
    args: argparse.Namespace | None = None,
) -> WorkerBrief:
    attachments = _surface_attachments_from_args(args)
    image_attachments = _surface_image_attachments_from_args(args)
    if not attachments and not image_attachments:
        return brief
    payload_update = {
        **dict(brief.input_payload),
        "surface_attachments": attachments,
        "image_attachments": image_attachments,
    }
    metadata_update = {
        **dict(brief.metadata),
        "surface_attachments": attachments,
        "image_attachments": image_attachments,
    }
    return brief.model_copy(update={"input_payload": payload_update, "metadata": metadata_update})


def _request_from_live_brief(
    brief: WorkerBrief,
    *,
    args: argparse.Namespace | None = None,
) -> ExecutionRequest:
    explicit_brief = _brief_with_explicit_super_dan_skills(brief, args=args)
    explicit_brief = _brief_with_surface_conversation(explicit_brief, args=args)
    explicit_brief = _brief_with_surface_attachments(explicit_brief, args=args)
    if args is not None:
        execution_policy = _live_execution_policy_payload(args)
        max_work_seconds = _live_max_work_seconds(args)
        if execution_policy or max_work_seconds is not None:
            metadata_update = dict(explicit_brief.metadata)
            if execution_policy:
                metadata_update["execution_policy"] = execution_policy
            if max_work_seconds is not None:
                metadata_update["completion_timeout_seconds"] = max_work_seconds
            explicit_brief = explicit_brief.model_copy(update={"metadata": metadata_update})
    return request_from_brief(_apply_auto_super_dan_skills(explicit_brief))


def _live_cell_from_brief(
    *,
    model: str,
    brief: WorkerBrief,
    worker_id: str,
    organism_stage: str,
) -> WorkerDefinition:
    worker = build_cell(model, brief.sampling_policy, brief.role.role_label)
    metadata = {
        **dict(worker.metadata),
        "worker_id": worker_id,
        "organism_stage": organism_stage,
        "brief_driven": True,
    }
    return worker.model_copy(update={"id": worker_id, "metadata": metadata})


def _live_website_task(
    report: SuperOrganismReport,
    *,
    artifact_root: Path,
    relative_files: Sequence[str],
    objective_context: Mapping[str, Any] | None = None,
) -> str:
    files = ", ".join(relative_files)
    context = dict(objective_context or {})
    effective_objective = str(context.get("effective_objective") or report.target).strip()
    original_objective = str(context.get("original_objective") or report.target).strip()
    normalized_note = ""
    if context.get("normalized"):
        normalized_note = (
            f"Original operator wording: {original_objective}. "
            "The request was vague continuation language, so apply the expanded default patch brief above. "
        )
    return (
        "Build the requested product website now. "
        f"Operator objective: {effective_objective}. "
        f"{normalized_note}"
        f"Artifact root: {artifact_root}. "
        f"Required relative files: {files}. "
        "Honor the supplied ticket ownership and handoff packets instead of freeforming a generic demo shell. "
        f"{_live_pacing_contract()} "
        "If the website files already exist, improve them incrementally instead of rewriting the whole site in one response. "
        "For an existing website patch, prefer coordinated edits when the objective spans structure, style, behavior, or docs; "
        "a focused single-file patch is acceptable when it fully satisfies the objective. "
        "Do not create extra scratch files outside the required artifact set. "
        "Make the website's actual product or subject clear. Preserve the existing site subject when patching an existing site. "
        "Do not pivot to a generic Super DAN execution-contract/demo site unless the operator explicitly asks for Super DAN. "
        "Actually create the files, then return the requested compact JSON-like completion summary."
    )


def _live_website_validation_task(
    report: SuperOrganismReport,
    *,
    artifact_root: Path,
    relative_files: Sequence[str],
    objective_context: Mapping[str, Any] | None = None,
) -> str:
    files = ", ".join(relative_files)
    context = dict(objective_context or {})
    effective_objective = str(context.get("effective_objective") or report.target).strip()
    original_objective = str(context.get("original_objective") or report.target).strip()
    normalized_note = ""
    if context.get("normalized"):
        normalized_note = (
            f"Original operator wording: {original_objective}. "
            "Validate against the expanded patch brief, not only the terse continuation wording. "
        )
    return (
        "Validate the materialized website now in read-only mode. "
        f"Operator objective: {effective_objective}. "
        f"{normalized_note}"
        f"Artifact root: {artifact_root}. "
        f"Required relative files: {files}. "
        "Inspect the generated files and decide whether the result is a real product website aligned with the objective, "
        "not just a generic Super DAN execution-contract demo shell. "
        "Allow a focused single-file patch when it materially satisfies the objective; require broader coverage only when "
        "the request or evidence truly spans multiple files."
    )


def _validation_repair_brief(
    validation: Mapping[str, Any],
    deterministic_failures: Sequence[str],
) -> str:
    parts: list[str] = []
    blocking = validation.get("blocking_current_task_failures")
    if isinstance(blocking, list):
        parts.extend(_display_text(item) for item in blocking if str(item).strip())
    for key in ("repair_brief", "comparison_note", "error"):
        value = _display_text(validation.get(key) or "")
        if value:
            parts.append(value)
    missing = validation.get("missing_requirements")
    if isinstance(missing, list):
        parts.extend(_display_text(item) for item in missing if str(item).strip())
    parts.extend(_display_text(item) for item in deterministic_failures if str(item).strip())
    seen: set[str] = set()
    compact: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.add(part)
            compact.append(part)
    return " ".join(compact)


def _super_plan_scoped_validation_payload(
    validation: Mapping[str, Any],
    plan_context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    normalized = _normalize_validation_payload(dict(validation))
    for key in ("status", "tool_calls", "event_count", "error", "token_usage"):
        if key in validation:
            normalized[key] = validation.get(key)
    if not isinstance(plan_context, Mapping):
        return normalized
    deferred_gaps = _super_plan_string_list(normalized.get("deferred_task_gaps") or [])
    remaining_work = _super_plan_string_list(normalized.get("remaining_work") or [])
    blocking = _super_plan_string_list(normalized.get("blocking_current_task_failures") or [])
    if deferred_gaps and remaining_work:
        normalized["deferred_task_gaps"] = list(dict.fromkeys([*deferred_gaps, *remaining_work]))
    elif remaining_work:
        normalized["deferred_task_gaps"] = remaining_work
    deferred_gaps = _super_plan_string_list(normalized.get("deferred_task_gaps") or [])
    missing = _super_plan_string_list(normalized.get("missing_requirements") or [])
    if not deferred_gaps and missing:
        deferred_like = [
            item for item in missing if _super_plan_text_mentions_deferred(item, plan_context)
        ]
        if deferred_like:
            deferred_gaps = deferred_like
            normalized["deferred_task_gaps"] = deferred_like
            normalized["missing_requirements"] = [
                item for item in missing if item not in set(deferred_like)
            ]
            missing = _super_plan_string_list(normalized.get("missing_requirements") or [])
    if blocking:
        normalized["passed"] = False
        normalized["missing_requirements"] = blocking
        if not str(normalized.get("repair_brief") or "").strip():
            normalized["repair_brief"] = blocking[0]
        return normalized
    if deferred_gaps:
        deferred_set = set(deferred_gaps)
        only_deferred_missing = not missing or all(item in deferred_set for item in missing)
        scope = str(normalized.get("completion_scope") or "").strip()
        if only_deferred_missing or scope == "current_frontier":
            normalized["passed"] = True
            normalized["completion_scope"] = "current_frontier"
            normalized["missing_requirements"] = []
            normalized["repair_brief"] = ""
            comparison = str(normalized.get("comparison_note") or "").strip()
            if "deferred" not in comparison.lower():
                suffix = "Deferred DAG tasks remain queued for later ready-frontier waves."
                normalized["comparison_note"] = f"{comparison} {suffix}".strip()
    return normalized


def _looks_like_git_baseline_rejection(text: str) -> bool:
    lowered = str(text or "").lower()
    git_terms = ("git history", "git log", "git diff", "commit", "head~", "untracked")
    baseline_terms = ("baseline", "greenfield", "created fresh", "no prior version")
    return any(term in lowered for term in git_terms) and any(
        term in lowered for term in baseline_terms
    )


def _text_requests_additive_update(text: str) -> bool:
    lowered = text.lower()
    additive_terms = (
        "add",
        "expand",
        "enrich",
        "include",
        "missing",
        "lacks",
        "insufficient",
        "deepen",
        "more substantive",
        "not enough",
    )
    removal_terms = ("remove", "delete", "trim", "shorten", "compress")
    return any(term in lowered for term in additive_terms) and not (
        any(term in lowered for term in removal_terms)
        and not any(term in lowered for term in ("add", "expand", "include", "missing"))
    )


def _generic_validation_requests_additive_repair(validation: Mapping[str, Any]) -> bool:
    return _text_requests_additive_update(_validation_repair_brief(validation, []))


def _generic_validation_repair_brief(
    validation: Mapping[str, Any],
    *,
    plan_context: Mapping[str, Any] | None = None,
) -> str:
    scoped_validation = _super_plan_scoped_validation_payload(validation, plan_context)
    raw = _validation_repair_brief(scoped_validation, [])
    if not _looks_like_git_baseline_rejection(raw):
        return raw
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", raw)
        if sentence.strip()
    ]
    filtered_sentences = []
    for sentence in sentences:
        lowered_sentence = sentence.lower()
        sentence_is_git_baseline = (
            ("git" in lowered_sentence or "commit" in lowered_sentence or "head~" in lowered_sentence)
            and any(term in lowered_sentence for term in ("baseline", "diff", "history", "greenfield"))
        ) or (
            "baseline" in lowered_sentence
            and any(term in lowered_sentence for term in ("locate", "create", "prior version", "fresh"))
        )
        if not sentence_is_git_baseline:
            filtered_sentences.append(sentence)
    substantive = " ".join(filtered_sentences).strip()
    if not substantive:
        substantive = raw
    return (
        "Do not create, overwrite, or commit a baseline artifact to manufacture before/after evidence. "
        "This workspace may contain untracked operator artifacts; use the run's pre-run file-state metadata and "
        "the current artifact content as the baseline. Repair by improving the existing candidate directly. "
        f"Validator feedback, interpreted as substantive content gaps rather than a git-history requirement: {substantive}"
    )


def _generic_repair_validation_payload(
    validation: Mapping[str, Any],
    *,
    repair_brief: str,
    plan_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _super_plan_scoped_validation_payload(validation, plan_context)
    payload["repair_brief"] = repair_brief
    if _looks_like_git_baseline_rejection(_validation_repair_brief(validation, [])):
        payload["comparison_note"] = (
            "Git history or commit evidence is not required for this generic workspace repair; "
            "compare against the pre-run file-state metadata and current artifact substance."
        )
        payload["git_baseline_rejection_corrected"] = True
    return payload


def _live_website_repair_task(
    report: SuperOrganismReport,
    *,
    artifact_root: Path,
    relative_files: Sequence[str],
    validation: Mapping[str, Any],
    deterministic_failures: Sequence[str],
    changed_required_paths: Sequence[str],
    objective_context: Mapping[str, Any] | None = None,
) -> str:
    files = ", ".join(relative_files)
    changed = ", ".join(_path_basename(path) for path in changed_required_paths) or "none"
    repair_brief = _validation_repair_brief(validation, deterministic_failures)
    context = dict(objective_context or {})
    effective_objective = str(context.get("effective_objective") or report.target).strip()
    return (
        "Repair the previous website patch now; do not stop with another summary-only response. "
        f"Operator objective: {effective_objective}. "
        f"Artifact root: {artifact_root}. "
        f"Required relative files: {files}. "
        f"Files already changed this run: {changed}. "
        f"Previous validation failure: {repair_brief or 'validator rejected the previous patch'}. "
        "Make concrete edits in the required files. If the feedback asks for broader coordination, update one or more "
        "required files that were not changed yet. For a maintainability patch, update the visible HTML, CSS guidance/tokens, "
        "JS module or patch notes behavior, and README patch instructions as needed. "
        "If the feedback lists Template phrase hits, remove or rename those exact hits in index.html; static validation "
        "passes only when fewer than 2 exact template hits remain. "
        "Keep the repair bounded, static, dependency-free, and inside the required artifact set. "
        "Actually write the repair with file_write or file_edit, then return the compact completion summary."
    )


def _live_artifact_builder_retry_task(
    report: SuperOrganismReport,
    *,
    artifact_root: Path,
    relative_files: Sequence[str],
    validation: Mapping[str, Any],
    deterministic_failures: Sequence[str],
    objective_context: Mapping[str, Any] | None = None,
) -> str:
    files = ", ".join(relative_files)
    recovery_brief = _validation_repair_brief(validation, deterministic_failures)
    context = dict(objective_context or {})
    effective_objective = str(context.get("effective_objective") or report.target).strip()
    return (
        "Run a focused Super DAN builder retry now. The prior builder returned without changing any required "
        "artifact file, so this is not a validation repair. "
        f"Operator objective: {effective_objective}. "
        f"Artifact root: {artifact_root}. "
        f"Required relative files: {files}. "
        f"Failure reason: {recovery_brief or 'no required artifact files changed'}. "
        "Do not summarize. Make at least one concrete required-file edit with file_write or file_edit before finalizing. "
        "For an existing artifact, prefer a small coordinated patch that improves the visible/result-bearing files. "
        "Keep the retry bounded, static, dependency-free, and inside the required artifact set."
    )


def _live_generic_planner_task(
    report: SuperOrganismReport,
    *,
    workspace_root: Path,
    plan_root: Path,
    plan_root_relative: str,
    operator_intent_policy: OperatorIntentPolicy | None = None,
    request_understanding: Mapping[str, Any] | None = None,
) -> str:
    policy_note = _operator_intent_policy_prompt(operator_intent_policy or OperatorIntentPolicy())
    understanding_note = _request_understanding_contract(request_understanding, stage="planner")
    if understanding_note:
        understanding_note += " "
    return (
        "Create a run-local Super DAN execution plan for this broad objective, then stop. "
        f"Operator objective: {report.target}. "
        f"Workspace root: {workspace_root}. "
        f"Temporary plan root: {plan_root} (`{plan_root_relative}`). "
        f"{policy_note} "
        f"{understanding_note}"
        "Do not implement the deliverable in this stage. Inspect only the context needed to make the plan coherent. "
        "Break the work into at most two file levels: top-level numeric phase files and optional numeric sub-plan files. "
        "Predict a compact dependency task graph even if the dependencies are imperfect: each task should name its "
        "parent/branch when useful, prerequisites, owned paths, deliverables, validation checks, and whether it can run "
        "in parallel with other ready tasks. Keep graph rules brief; do not write a backup plan in prose. "
        "Choose the current ready frontier: tasks whose dependencies are satisfied now and whose owned paths do not conflict. "
        "Downstream tasks should be marked deferred/blocked instead of becoming immediate repair work."
    )


def _live_generic_plan_validation_task(
    report: SuperOrganismReport,
    *,
    workspace_root: Path,
    plan_root: Path,
    plan_files: Sequence[str],
    operator_intent_policy: OperatorIntentPolicy | None = None,
    request_understanding: Mapping[str, Any] | None = None,
) -> str:
    policy_note = _operator_intent_policy_prompt(operator_intent_policy or OperatorIntentPolicy())
    understanding_note = _request_understanding_contract(request_understanding, stage="plan_validator")
    if understanding_note:
        understanding_note += " "
    rendered_files = ", ".join(str(path) for path in plan_files) or "none"
    return (
        "Validate the run-local Super DAN plan before execution. "
        f"Operator objective: {report.target}. "
        f"Workspace root: {workspace_root}. "
        f"Plan root: {plan_root}. "
        f"Plan files: {rendered_files}. "
        f"{policy_note} "
        f"{understanding_note}"
        "Read the plan files, then decide whether they are coherent, numeric, non-contradictory, and ready for a builder. "
        "Reject plans that use alphabet placeholders, create third-level plan files, mix unrelated sub-plans under one "
        "phase, or fail to identify a dependency-ready frontier. Audit the predicted DAG: dependencies may be imperfect, "
        "but ready tasks must have satisfied prerequisites and non-conflicting owned paths. Return corrected `task_graph`, "
        "`ready_task_ids`, `deferred_task_ids`, and brief `dependency_revisions` / `task_graph_update` entries when the "
        "plan is mostly usable. Scope sequencing edits to the affected branch unless the whole graph is invalid."
    )


def _live_generic_task(
    report: SuperOrganismReport,
    *,
    workspace_root: Path,
    operator_intent_policy: OperatorIntentPolicy | None = None,
    prompt_only_creation_target: str | None = None,
    plan_context: Mapping[str, Any] | None = None,
    request_understanding: Mapping[str, Any] | None = None,
) -> str:
    policy = operator_intent_policy or OperatorIntentPolicy()
    policy_note = _operator_intent_policy_prompt(policy)
    plan_note = _super_plan_executor_contract(plan_context)
    if plan_note:
        plan_note += " "
    understanding_note = _request_understanding_contract(request_understanding, stage="builder")
    if understanding_note:
        understanding_note += " "
    constrained_creation_note = ""
    if prompt_only_creation_target:
        constrained_creation_note = (
            "Constrained creation condition: the operator forbids other workspace inputs and the explicit target "
            f"`{prompt_only_creation_target}` is missing. Create the target from the objective and binding policy alone. "
            "Do not spend a tool call on directory inventory, git context, existing-artifact reuse, or target-existence checks before the first write. "
        )
    workspace_context_sentence = (
        "Create from the operator objective and allowed target path; do not inspect existing workspace context before the first write. "
        if prompt_only_creation_target
        else "Inspect the existing project or workspace as needed. "
    )
    workspace_scope_note = (
        "When the request refers to existing artifacts indirectly, infer the target set from explicit paths, recent surface conversation, "
        "and user-facing workspace files before widening the search. Treat runtime/history/state directories such as `.dan-*`, "
        "`.agent-subsessions`, memory stores, plan traces, and test logs as non-deliverable context unless the operator explicitly names them. "
    )
    coordination_sentence = (
        "Use the constrained creation packet as the execution context. "
        if prompt_only_creation_target
        else "Honor the supplied ticket ownership and handoff packets instead of freeforming a generic build summary. "
    )
    interactive_source_note = ""
    if _super_is_interactive_source_implementation_objective(str(report.target or "")):
        interactive_source_note = (
            "Interactive source implementation condition: this objective asks for a usable application, "
            "prototype, tool, visualization, or demo that a person can operate. Treat product source, scene/state files, scripts, UI, "
            "assets, and validation/tests as the deliverable surface. Do not use `.dan-super` plan files or notes "
            "as the first durable output. Inspect only enough structure to identify the right source files, then "
            "make concrete source or validation edits that create user actions, visible feedback, state transitions, "
            "and a repeatable short interaction loop. "
        )
    if not policy.allow_workspace_mutation:
        return (
            "Answer the operator objective from inspection in the current workspace now, using only the enabled tools. "
            f"Operator objective: {report.target}. "
            f"Workspace root: {workspace_root}. "
            f"{policy_note} "
            f"{coordination_sentence}"
            f"{understanding_note}"
            "Inspect the existing project or workspace as needed, but do not create or edit workspace files. "
            f"{workspace_scope_note}"
            "If the objective asks for current external facts, use web_search when available instead of guessing. "
            "The deliverable is the in-session answer, not a saved summary file, unless the operator explicitly asks for a file or artifact. "
            "Finish with a substantive direct answer for the operator: include a Summary, evidence-backed findings, "
            "important limitations, and any follow-up work that would require explicit permission or a separate request. "
            "For project-summary or 'what is this project about' requests, explain what the project is, main components, current state, important files, and blockers or next steps. "
            "Return the requested compact JSON-like answer summary with an `answer` field containing the actual response the operator should read."
        )
    return (
        "Execute the operator objective in the current workspace now, using the enabled tools to produce the requested deliverable. "
        f"Operator objective: {report.target}. "
        f"Workspace root: {workspace_root}. "
        f"{policy_note} "
        f"{constrained_creation_note}"
        f"{coordination_sentence}"
        f"{plan_note}"
        f"{understanding_note}"
        f"{interactive_source_note}"
        f"{workspace_context_sentence}"
        f"{workspace_scope_note}"
        "If the objective asks for current external facts, use web_search "
        "instead of guessing. If it asks to save, export, return, or eventually produce a file, create or update the appropriate "
        "workspace artifact; markdown/report requests should be materialized as a markdown file with source notes or links when "
        "available. If it asks for software, make the bounded implementation and run focused verification when useful. "
        "Actually mutate workspace files before finalizing, then return the requested compact JSON-like completion summary."
    )


def _live_generic_answer_recovery_task(
    report: SuperOrganismReport,
    *,
    workspace_root: Path,
    failure_reason: str,
    attempt: int,
    previous_output: Any,
    operator_intent_policy: OperatorIntentPolicy | None = None,
    request_understanding: Mapping[str, Any] | None = None,
) -> str:
    policy_note = _operator_intent_policy_prompt(operator_intent_policy or OperatorIntentPolicy())
    understanding_note = _request_understanding_contract(request_understanding, stage="final_response_recovery")
    if understanding_note:
        understanding_note += " "
    try:
        previous_rendered = json.dumps(previous_output, ensure_ascii=False, sort_keys=True)
    except TypeError:
        previous_rendered = str(previous_output)
    previous_rendered = _truncate_text(previous_rendered, limit=1600)
    return (
        "Recover the missing final answer for this Super DAN run now. "
        f"Operator objective: {report.target}. "
        f"Workspace root: {workspace_root}. "
        f"Recovery attempt: {attempt}. "
        f"Why the previous final response was unsatisfactory: {failure_reason}. "
        f"Previous output: {previous_rendered}. "
        f"{policy_note} "
        f"{understanding_note}"
        "This is an answer-only recovery pass: do not create, edit, delete, or otherwise mutate workspace files. "
        "Use the enabled read-only tools only if the answer needs more evidence. "
        "Move closer to the operator's goal by producing the actual in-session answer, not another status receipt. "
        "Return compact JSON with an `answer` field containing the human-readable answer the operator should read. "
        "If the request truly cannot be answered from available evidence, put the explicit blocker and the narrow next step in `answer`; "
        "do not return only `completed`, `Run finished`, candidate ids, file lists, or change receipts."
    )


def _live_generic_worktree_task(
    report: SuperOrganismReport,
    *,
    main_workspace_root: Path,
    worktree_root: Path,
    task: Mapping[str, Any],
    plan_context: Mapping[str, Any] | None = None,
    operator_intent_policy: OperatorIntentPolicy | None = None,
    request_understanding: Mapping[str, Any] | None = None,
) -> str:
    policy_note = _operator_intent_policy_prompt(operator_intent_policy or OperatorIntentPolicy())
    understanding_note = _request_understanding_contract(request_understanding, stage="worktree")
    if understanding_note:
        understanding_note += " "
    task_id = str(task.get("task_id") or "").strip()
    goal = str(task.get("goal") or "").strip()
    owned = ", ".join(_super_plan_task_owned_paths(task)) or "the task-owned files"
    checks = ", ".join(_super_plan_string_list(task.get("validation") or [])) or "focused local inspection"
    plan_note = _super_plan_executor_contract(plan_context)
    if plan_note:
        plan_note += " "
    return (
        "Execute one Super DAN dependency-frontier task inside this isolated worktree. "
        f"Operator objective: {report.target}. "
        f"Task id: {task_id}. "
        f"Task goal: {goal or 'complete the assigned ready task'}. "
        f"Authoritative workspace root: {main_workspace_root}. "
        f"Isolated worktree root for this worker: {worktree_root}. "
        f"Owned paths for this worker: {owned}. "
        f"Expected validation evidence: {checks}. "
        f"{policy_note} "
        f"{understanding_note}"
        f"{plan_note}"
        "Only edit files under the owned paths for this task. Do not implement sibling ready tasks or deferred downstream tasks. "
        "Do not edit `.dan-super` state or plan files from a worktree worker. "
        "Make concrete file_write or file_edit calls in the isolated worktree, then return the compact completion summary."
    )


def _live_generic_builder_retry_task(
    report: SuperOrganismReport,
    *,
    workspace_root: Path,
    failure_reason: str,
    attempt: int,
    recommended_write_paths: Sequence[str] | None = None,
    additive_recovery_required: bool = False,
    operator_intent_policy: OperatorIntentPolicy | None = None,
    prompt_only_creation_target: str | None = None,
    plan_context: Mapping[str, Any] | None = None,
    request_understanding: Mapping[str, Any] | None = None,
) -> str:
    policy_note = _operator_intent_policy_prompt(operator_intent_policy or OperatorIntentPolicy())
    plan_note = _super_plan_executor_contract(plan_context)
    if plan_note:
        plan_note += " "
    understanding_note = _request_understanding_contract(request_understanding, stage="builder_retry")
    if understanding_note:
        understanding_note += " "
    preservation_note = ""
    if additive_recovery_required:
        preservation_note = (
            "This is an additive/enrichment objective against an existing workspace artifact. "
            "Preserve the existing artifact substance, tables, headings, and quantitative details. "
            "Do not replace an existing report/document with a shorter generic scaffold. "
            "For existing files, prefer targeted file_edit or append-style file_write; use whole-file overwrite only "
            "for a complete expanded replacement that preserves all prior substance. "
        )
    target_paths = [
        str(path).strip()
        for path in (recommended_write_paths or [])
        if str(path).strip()
    ]
    target_note = ""
    if target_paths:
        rendered_targets = ", ".join(f"`{path}`" for path in target_paths[:6])
        first_target = target_paths[0]
        target_note = (
            f"Recommended builder retry targets, in priority order: {rendered_targets}. "
            f"Make the first durable write to `{first_target}` unless that path is clearly incompatible. "
            "If the chosen path already exists, use a targeted file_edit or append-style file_write; "
            "if it does not exist, create it with file_write. "
        )
    constrained_creation_note = ""
    if prompt_only_creation_target:
        constrained_creation_note = (
            "Constrained creation condition: other workspace inputs are forbidden and the explicit target "
            f"`{prompt_only_creation_target}` is missing. Create that target from the objective and policy alone. "
            "Do not take an inspection/checking call before the first write. "
        )
    return (
        "Run another Super DAN builder attempt now, narrowed by the previous no-mutation result. "
        f"Attempt: {attempt}. "
        f"Operator objective: {report.target}. "
        f"Workspace root: {workspace_root}. "
        f"Previous failure reason: {failure_reason or 'no workspace files were changed'}. "
        f"{policy_note} "
        f"{preservation_note}"
        f"{target_note}"
        f"{constrained_creation_note}"
        f"{plan_note}"
        f"{understanding_note}"
        "The previous builder returned or timed out without a durable workspace mutation. Use any useful evidence it produced, "
        "then decide whether a concrete write is now justified. If it is, make at least one concrete file_write or file_edit "
        "call before finalizing. If the objective asks for a report or markdown deliverable, create or update the report "
        "artifact directly. If an existing relevant artifact is present, prefer appending or targeted edits over replacing it. "
        "then return the requested compact JSON-like completion summary."
    )


def _live_generic_validation_task(
    report: SuperOrganismReport,
    *,
    workspace_root: Path,
    pre_run_file_state: Mapping[str, Mapping[str, Any]] | None = None,
    post_run_file_state: Mapping[str, Mapping[str, Any]] | None = None,
    operator_intent_policy: OperatorIntentPolicy | None = None,
    plan_context: Mapping[str, Any] | None = None,
    request_understanding: Mapping[str, Any] | None = None,
) -> str:
    policy_note = _operator_intent_policy_prompt(operator_intent_policy or OperatorIntentPolicy())
    plan_note = _super_plan_validation_contract(plan_context)
    if plan_note:
        plan_note += " "
    understanding_note = _request_understanding_contract(request_understanding, stage="validator")
    if understanding_note:
        understanding_note += " "
    state_note = ""
    if pre_run_file_state:
        state_note = (
            "Pre-run file-state metadata for mutated paths is available in the input payload. "
            "Use it as the before/after baseline for untracked workspaces; do not require git commits, git log, "
            "or git diff evidence when a file existed before the run and was changed by write tools. "
        )
    if post_run_file_state:
        state_note += (
            "Post-run file-state metadata for mutated paths is also available; use it to notice destructive shrinkage "
            "or missing artifacts. "
        )
    frontier_note = ""
    if plan_note:
        frontier_note = (
            "This run may be one wave of a broader dependency DAG. Judge whether the current ready frontier is complete, "
            "safe, and supported by changed-file evidence. Do not fail solely because deferred downstream DAG tasks remain; "
            "record those under deferred_task_gaps or remaining_work. "
        )
    return (
        "Validate the live workspace deliverable now in read-only mode. "
        f"Operator objective: {report.target}. "
        f"Workspace root: {workspace_root}. "
        f"{policy_note} "
        f"{state_note}"
        f"{plan_note}"
        f"{understanding_note}"
        f"{frontier_note}"
        "Inspect the mutated files and relevant read-only evidence, then decide whether the result materially advances the "
        "objective. For report or markdown objectives, verify that a report-like artifact was actually written and is not just "
        "a generic planning memo. For software objectives, inspect the implementation and verification evidence."
    )


def _live_generic_repair_task(
    report: SuperOrganismReport,
    *,
    workspace_root: Path,
    validation: Mapping[str, Any],
    mutated_paths: Sequence[str],
    repair_brief: str | None = None,
    pre_run_file_state: Mapping[str, Mapping[str, Any]] | None = None,
    current_file_state: Mapping[str, Mapping[str, Any]] | None = None,
    operator_intent_policy: OperatorIntentPolicy | None = None,
    plan_context: Mapping[str, Any] | None = None,
    request_understanding: Mapping[str, Any] | None = None,
) -> str:
    policy_note = _operator_intent_policy_prompt(operator_intent_policy or OperatorIntentPolicy())
    plan_note = _super_plan_executor_contract(plan_context)
    if plan_note:
        plan_note += " "
    understanding_note = _request_understanding_contract(request_understanding, stage="repair")
    if understanding_note:
        understanding_note += " "
    effective_repair_brief = repair_brief or _generic_validation_repair_brief(validation)
    changed = ", ".join(str(path) for path in mutated_paths) or "none recorded"
    state_note = ""
    if pre_run_file_state or current_file_state:
        state_note = (
            "Use the supplied file-state metadata as before/after context. Do not treat missing git commits, empty git log, "
            "or an untracked workspace as proof that a new baseline must be created. "
        )
    frontier_note = ""
    if plan_note:
        frontier_note = (
            "Repair only blockers for the current ready frontier. Do not implement deferred downstream DAG tasks as part of "
            "this repair unless the validator explicitly revised the dependency graph and marked them ready. "
        )
    return (
        "Repair the previous Super DAN live deliverable now. "
        f"Operator objective: {report.target}. "
        f"Workspace root: {workspace_root}. "
        f"Current mutated files: {changed}. "
        f"{policy_note} "
        f"{plan_note}"
        f"{understanding_note}"
        f"{state_note}"
        f"{frontier_note}"
        f"Validation feedback: {effective_repair_brief or 'validator rejected the previous deliverable'}. "
        "When validation feedback lists exact files, missing source markers, compiler diagnostics, or shell guard "
        "FAIL lines, treat those as the repair targets. Refresh at most the focused range needed for the edit, then "
        "mutate the named source or validation files directly before any broad re-audit. Editing a secondary "
        "validator/helper file alone is not sufficient when the feedback also names broken product source. "
        "Make concrete workspace edits that address that feedback; do not return a summary-only response. If the deliverable is "
        "a report or markdown artifact, edit that artifact directly and improve grounding or coverage as needed. "
        "Do not overwrite a substantive existing artifact with a shorter baseline, scaffold, or outline to manufacture "
        "before/after evidence; preserve existing substance and expand or target-edit it unless the validator explicitly asks "
        "for removal. "
        "Keep the repair bounded, preserve the existing artifact shape unless the feedback requires otherwise, "
        "and finish with the requested compact JSON-like completion summary."
    )


def _super_run_root(workspace_root: Path) -> Path:
    return workspace_root / ".dan-super" / "runs"


def _next_super_run_number(run_root: Path) -> int:
    highest = 0
    if run_root.exists():
        for child in run_root.iterdir():
            if not child.is_dir():
                continue
            name = child.name
            if not name.startswith("turn-"):
                continue
            try:
                highest = max(highest, int(name.split("-", 1)[1]))
            except (TypeError, ValueError):
                continue
    return highest + 1


def _build_super_run_workdir(workspace_root: Path) -> tuple[Path, int]:
    run_root = _super_run_root(workspace_root)
    run_root.mkdir(parents=True, exist_ok=True)
    turn_number = _next_super_run_number(run_root)
    workdir = run_root / f"turn-{turn_number:02d}"
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir, turn_number


def _super_session_id(workspace_root: Path) -> str:
    digest = hashlib.sha1(
        str(workspace_root.resolve(strict=False)).encode("utf-8")
    ).hexdigest()[:12]
    return f"super-dan:{digest}"


def _log_live_event(
    logger: SuperRunEventLogger | None,
    event: str,
    **payload: Any,
) -> None:
    if logger is None:
        return
    logger.emit({"event": event, **payload})


def _safe_workspace_artifact_path(
    raw_path: str,
    *,
    workspace_root: Path,
) -> Path | None:
    raw = str(raw_path or "").strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = workspace_root / candidate
    relative = _relative_workspace_artifact_path(candidate, workspace_root=workspace_root)
    if not relative:
        return None
    return (workspace_root / relative).resolve(strict=False)


def _objective_forbids_other_workspace_inputs(objective: str) -> bool:
    lowered = " ".join(str(objective or "").lower().split())
    if not lowered:
        return False
    patterns = (
        r"\bdo\s+not\s+(?:read|inspect|open|look\s+at|use)\s+(?:any\s+)?other\s+files?\b",
        r"\bdon['’]?t\s+(?:read|inspect|open|look\s+at|use)\s+(?:any\s+)?other\s+files?\b",
        r"\bdont\s+(?:read|inspect|open|look\s+at|use)\s+(?:any\s+)?other\s+files?\b",
        r"\bwithout\s+(?:reading|inspecting|opening|using)\s+(?:any\s+)?other\s+files?\b",
        r"\bdo\s+not\s+(?:read|inspect|open|look\s+at|use)\s+(?:this\s+)?(?:folder|directory|workspace)\b",
        r"\bdon['’]?t\s+(?:read|inspect|open|look\s+at|use)\s+(?:this\s+)?(?:folder|directory|workspace)\b",
    )
    return any(re.search(pattern, lowered) for pattern in patterns)


def _objective_forbids_existing_artifact_reuse(objective: str) -> bool:
    lowered = " ".join(str(objective or "").lower().split())
    if not lowered:
        return False
    patterns = (
        r"\bfrom\s+scratch\b",
        r"\bdo\s+not\s+use\s+(?:any\s+)?existing\b",
        r"\bdon['’]?t\s+use\s+(?:any\s+)?existing\b",
        r"\bdont\s+use\s+(?:any\s+)?existing\b",
        r"\bwithout\s+using\s+(?:any\s+)?existing\b",
    )
    return any(re.search(pattern, lowered) for pattern in patterns)


def _objective_forbids_workspace_mutation(objective: str) -> bool:
    lowered = " ".join(str(objective or "").lower().split())
    if not lowered:
        return False
    patterns = (
        r"\bread[-\s]?only\b",
        r"\bdo\s+not\s+(?:edit|modify|change|write|create|delete|touch|mutate)\s+(?:any\s+)?(?:workspace\s+)?files?\b",
        r"\bdon['’]?t\s+(?:edit|modify|change|write|create|delete|touch|mutate)\s+(?:any\s+)?(?:workspace\s+)?files?\b",
        r"\bdont\s+(?:edit|modify|change|write|create|delete|touch|mutate)\s+(?:any\s+)?(?:workspace\s+)?files?\b",
        r"\bwithout\s+(?:editing|modifying|changing|writing|creating|deleting|touching|mutating)\s+(?:any\s+)?(?:workspace\s+)?files?\b",
        r"\bno\s+(?:file\s+)?(?:edits?|writes?|changes?|modifications?|mutations?)\b",
    )
    return any(re.search(pattern, lowered) for pattern in patterns)


def _objective_requests_workspace_mutation(objective: str) -> bool:
    lowered = " ".join(str(objective or "").lower().split())
    if not lowered:
        return False
    patterns = (
        r"\b(?:edit|modify|change|write|create|delete|touch|mutate|fix|repair|implement|build|add|update|save|export|materialize)\b",
        r"\bmake\s+(?:a\s+)?(?:change|changes|edit|edits|fix|fixes|patch|patches|improvement|improvements)\b",
        r"\b(?:produce|generate)\s+(?:a\s+)?(?:file|artifact|document|markdown|report|memo|patch|diff)\b",
    )
    return any(re.search(pattern, lowered) for pattern in patterns)


def _objective_is_project_answer_request(objective: str) -> bool:
    lowered = " ".join(str(objective or "").lower().split())
    if not lowered:
        return False
    project_target = r"(?:project|repo|repository|codebase|workspace)"
    patterns = (
        rf"\b(?:what\s+(?:is|does|are)|what['’]s)\s+(?:this|the)\s+{project_target}(?:\s+(?:about|do|for))?\b",
        rf"\btell\s+me\s+about\s+(?:this|the)\s+{project_target}\b",
        rf"\b{project_target}\s+(?:summary|overview)\b",
        rf"\b(?:summarize|summarise|summary)\s+(?:of\s+)?(?:this|the)?\s*{project_target}\b",
        rf"\b(?:give|write|create)\s+(?:me\s+)?(?:a\s+)?(?:brief\s+)?(?:summary|overview)\s+of\s+(?:this|the)\s+{project_target}\b",
        rf"\bhelp\s+me\s+(?:understand|summarize|summarise|summary)\s+(?:what\s+)?(?:this|the)?\s*{project_target}\s*(?:is\s+)?(?:about)?\b",
    )
    return any(re.search(pattern, lowered) for pattern in patterns)


def _objective_explicitly_requests_saved_answer_artifact(objective: str) -> bool:
    lowered = " ".join(str(objective or "").lower().split())
    if not lowered:
        return False
    if re.search(r"(?<!\w)[\w./~-]+\.(?:md|txt|json|html|py|ts|tsx|jsx|csv|yaml|yml|toml)\b", lowered):
        return True
    if re.search(r"\b(?:readme|file|artifact|document|markdown|md)\b", lowered):
        return True
    if re.search(r"\b(?:edit|modify|change|update|fix|repair|implement|build|add|delete|touch|mutate)\b", lowered):
        return True
    if re.search(r"\b(?:save|export|materialize)\b", lowered):
        return True
    return False


def _objective_is_assessment_only_request(objective: str) -> bool:
    lowered = " ".join(str(objective or "").lower().split())
    if not lowered:
        return False
    project_answer = _objective_is_project_answer_request(lowered)
    if _objective_requests_workspace_mutation(lowered):
        if not project_answer or _objective_explicitly_requests_saved_answer_artifact(lowered):
            return False
    assessment_patterns = (
        r"\b(?:review|audit|assess|evaluate|inspect|check|look\s+over|analyze|analyse|summarize|summarise|explain)\b",
        r"\bwhat\s+(?:do\s+you\s+think|is\s+going\s+on|is\s+the\s+status)\b",
        r"\bhelp\s+me\s+(?:understand|review|audit|assess|evaluate|inspect|check|analyze|analyse|summarize|summarise|summary)\b",
    )
    return project_answer or any(re.search(pattern, lowered) for pattern in assessment_patterns)


def _objective_forbids_shell_command(objective: str) -> bool:
    lowered = " ".join(str(objective or "").lower().split())
    if not lowered:
        return False
    patterns = (
        r"\bdo\s+not\s+run\s+(?:any\s+)?(?:external\s+)?(?:shell\s+|terminal\s+)?commands?\b",
        r"\bdon['’]?t\s+run\s+(?:any\s+)?(?:external\s+)?(?:shell\s+|terminal\s+)?commands?\b",
        r"\bdont\s+run\s+(?:any\s+)?(?:external\s+)?(?:shell\s+|terminal\s+)?commands?\b",
        r"\bwithout\s+running\s+(?:any\s+)?(?:external\s+)?(?:shell\s+|terminal\s+)?commands?\b",
        r"\bno\s+(?:external\s+)?(?:shell\s+|terminal\s+)?commands?\b",
    )
    return any(re.search(pattern, lowered) for pattern in patterns)


def _operator_intent_policy_from_objective(
    objective: str,
    *,
    workspace_root: Path,
) -> OperatorIntentPolicy:
    target_artifacts = tuple(
        _explicit_objective_artifact_paths(
            objective,
            workspace_root=workspace_root,
        )
    )
    forbid_other_inputs = _objective_forbids_other_workspace_inputs(objective)
    forbid_existing_reuse = _objective_forbids_existing_artifact_reuse(objective)
    forbid_workspace_mutation = _objective_forbids_workspace_mutation(objective)
    assessment_only = _objective_is_assessment_only_request(objective)
    forbid_shell_command = forbid_workspace_mutation or assessment_only or _objective_forbids_shell_command(objective)
    constraints: list[str] = []
    if forbid_other_inputs:
        constraints.append("Do not read, inspect, list, or otherwise use other workspace files.")
    if forbid_existing_reuse:
        constraints.append("Do not reuse existing workspace artifacts as source material.")
    if assessment_only:
        constraints.append("This is an assessment request; inspect the workspace and answer with findings.")
        constraints.append("Do not create or edit review documents unless the operator explicitly asks for a saved artifact or file changes.")
    if forbid_workspace_mutation:
        constraints.append("Do not write, edit, create, delete, or otherwise mutate workspace files.")
        constraints.append("Return a direct answer or read-only findings only; no workspace mutation is required.")
    if forbid_shell_command:
        constraints.append("Do not run shell, terminal, or external commands.")
    if not constraints:
        return OperatorIntentPolicy(target_artifacts=target_artifacts)

    allowed_targets = target_artifacts
    return OperatorIntentPolicy(
        active=True,
        allow_workspace_mutation=not (forbid_workspace_mutation or assessment_only),
        target_artifacts=target_artifacts,
        allowed_read_paths=allowed_targets if forbid_other_inputs else (),
        allowed_write_paths=() if (forbid_workspace_mutation or assessment_only) else allowed_targets,
        forbid_other_workspace_inputs=forbid_other_inputs,
        allow_directory_listing=not forbid_other_inputs,
        allow_git_context=not forbid_other_inputs,
        allow_shell_command=not (forbid_other_inputs or forbid_shell_command),
        allow_existing_artifact_reuse=not (forbid_other_inputs or forbid_existing_reuse),
        source_scope=(
            "operator_prompt_read_only"
            if forbid_workspace_mutation
            else (
                "operator_prompt_assessment_answer_only"
                if assessment_only
                else (
                "operator_prompt_and_target_artifacts_only"
                if forbid_other_inputs
                else "workspace_allowed_without_existing_artifact_reuse"
                )
            )
        ),
        constraints=tuple(constraints),
    )


def _operator_intent_policy_from_request(request: ExecutionRequest) -> OperatorIntentPolicy:
    raw = getattr(request, "metadata", {}).get("operator_intent_policy")
    if not isinstance(raw, Mapping):
        return OperatorIntentPolicy()
    return OperatorIntentPolicy(
        active=bool(raw.get("active")),
        allow_workspace_mutation=bool(raw.get("allow_workspace_mutation", True)),
        target_artifacts=tuple(str(path) for path in raw.get("target_artifacts") or ()),
        allowed_read_paths=tuple(str(path) for path in raw.get("allowed_read_paths") or ()),
        allowed_write_paths=tuple(str(path) for path in raw.get("allowed_write_paths") or ()),
        forbid_other_workspace_inputs=bool(raw.get("forbid_other_workspace_inputs")),
        allow_directory_listing=bool(raw.get("allow_directory_listing", True)),
        allow_git_context=bool(raw.get("allow_git_context", True)),
        allow_shell_command=bool(raw.get("allow_shell_command", True)),
        allow_existing_artifact_reuse=bool(raw.get("allow_existing_artifact_reuse", True)),
        source_scope=str(raw.get("source_scope") or "workspace_allowed"),
        constraints=tuple(str(item) for item in raw.get("constraints") or ()),
    )


def _operator_intent_policy_prompt(policy: OperatorIntentPolicy) -> str:
    if not policy.active:
        return ""
    lines = [
        "Binding operator intent policy:",
        f"- Source scope: {policy.source_scope}.",
    ]
    if policy.target_artifacts:
        lines.append(f"- Target artifacts: {', '.join(policy.target_artifacts)}.")
    if policy.allowed_read_paths:
        lines.append(f"- Allowed workspace reads: {', '.join(policy.allowed_read_paths)}.")
    elif policy.forbid_other_workspace_inputs:
        lines.append("- Allowed workspace reads: none before the target artifact exists.")
    if policy.allowed_write_paths:
        lines.append(f"- Allowed workspace writes: {', '.join(policy.allowed_write_paths)}.")
    elif not policy.allow_workspace_mutation:
        lines.append("- Allowed workspace writes: none.")
    for constraint in policy.constraints:
        lines.append(f"- {constraint}")
    if not policy.allow_directory_listing:
        lines.append("- Do not list or inventory the workspace directory.")
    if not policy.allow_git_context:
        lines.append("- Do not use git status, git diff, or git log as workspace context.")
    if not policy.allow_existing_artifact_reuse:
        lines.append("- Do not materialize the target by copying or adapting another workspace artifact.")
    return " ".join(lines)


def _filter_tool_ids_for_operator_intent(
    tool_ids: Sequence[str],
    policy: OperatorIntentPolicy,
) -> list[str]:
    if not policy.active:
        return list(tool_ids)
    blocked: set[str] = set()
    if not policy.allow_workspace_mutation:
        blocked.update({"file_write", "file_edit"})
    if not policy.allow_directory_listing:
        blocked.add("list_directory")
    if not policy.allow_git_context:
        blocked.update({"git_status", "git_diff", "git_log"})
    if not policy.allow_shell_command:
        blocked.add("shell_command")
    if policy.forbid_other_workspace_inputs and not policy.allowed_read_paths:
        blocked.add("file_read")
    return [str(tool_id) for tool_id in tool_ids if str(tool_id) not in blocked]


def _operator_policy_relative_path(raw_path: str, *, workspace_root: Path) -> str | None:
    raw = str(raw_path or "").strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = workspace_root / candidate
    resolved = candidate.resolve(strict=False)
    root = workspace_root.resolve(strict=False)
    if resolved == root:
        return "."
    return _relative_workspace_artifact_path(resolved, workspace_root=workspace_root)


def _operator_policy_tool_decision(
    policy: OperatorIntentPolicy,
    *,
    tool_id: str,
    arguments: Mapping[str, Any],
    workspace_root: Path,
) -> tuple[bool, str]:
    if not policy.active:
        return True, ""
    tool = str(tool_id)
    args = dict(arguments or {})
    allowed_reads = set(policy.allowed_read_paths)
    allowed_writes = set(policy.allowed_write_paths)
    allowed_targets = set(policy.target_artifacts) | allowed_reads | allowed_writes

    if tool == "list_directory" and not policy.allow_directory_listing:
        return False, "operator_intent_blocks_directory_listing"
    if tool in {"git_status", "git_diff", "git_log"} and not policy.allow_git_context:
        return False, "operator_intent_blocks_git_context"
    if tool == "shell_command" and not policy.allow_shell_command:
        return False, "operator_intent_blocks_shell_context"
    if tool in {"file_write", "file_edit"} and not policy.allow_workspace_mutation:
        return False, "operator_intent_blocks_workspace_mutation"
    if tool == "file_read":
        relative = _operator_policy_relative_path(str(args.get("path") or ""), workspace_root=workspace_root)
        if not relative or relative not in allowed_reads:
            return False, f"operator_intent_blocks_file_read:{relative or '(unknown)'}"
    if tool in {"file_write", "file_edit"} and allowed_writes:
        relative = _operator_policy_relative_path(str(args.get("path") or ""), workspace_root=workspace_root)
        if not relative or relative not in allowed_writes:
            return False, f"operator_intent_blocks_file_write:{relative or '(unknown)'}"
    if tool == "workspace_check" and policy.forbid_other_workspace_inputs:
        raw_paths: list[str] = []
        if str(args.get("path") or "").strip():
            raw_paths.append(str(args.get("path")))
        if isinstance(args.get("paths"), (list, tuple)):
            raw_paths.extend(str(path) for path in args.get("paths") or [])
        check = str(args.get("check") or "").strip()
        for raw_path in raw_paths:
            relative = _operator_policy_relative_path(raw_path, workspace_root=workspace_root)
            if relative == "." and check == "exists":
                continue
            if relative not in allowed_targets:
                return False, f"operator_intent_blocks_workspace_check:{relative or '(unknown)'}"
        if check and check != "exists":
            checked_path = _operator_policy_relative_path(
                str(args.get("path") or ""),
                workspace_root=workspace_root,
            )
            if checked_path not in allowed_targets:
                return False, f"operator_intent_blocks_workspace_check:{checked_path or '(unknown)'}"
    return True, ""


def _operator_intent_approval_callback(
    policy: OperatorIntentPolicy,
    *,
    workspace_root: Path,
    event_callback,
):
    if not policy.active:
        return None

    def approve(tool_id: str, arguments: dict[str, Any], metadata: dict[str, Any]) -> bool:
        del metadata
        allowed, reason = _operator_policy_tool_decision(
            policy,
            tool_id=tool_id,
            arguments=arguments,
            workspace_root=workspace_root,
        )
        if not allowed:
            event_callback(
                {
                    "event": "tool.policy_denied",
                    "tool_id": tool_id,
                    "arguments": dict(arguments),
                    "reason": reason,
                    "operator_intent_policy": policy.to_payload(),
                }
            )
        return allowed

    return approve


def _snapshot_workspace_file_metadata_for_intent(
    workspace_root: Path,
    policy: OperatorIntentPolicy,
) -> dict[str, dict[str, Any]]:
    if not policy.forbid_other_workspace_inputs:
        return _snapshot_workspace_file_metadata(workspace_root)
    paths = _dedupe_preserving_order(
        [
            *policy.target_artifacts,
            *policy.allowed_read_paths,
            *policy.allowed_write_paths,
        ]
    )
    return {
        str((workspace_root / path).resolve(strict=False)): _file_metadata(workspace_root / path)
        for path in paths
    }


def _operator_prompt_only_creation_target(
    policy: OperatorIntentPolicy,
    *,
    workspace_root: Path,
    pre_run_workspace_state: Mapping[str, Mapping[str, Any]] | None = None,
) -> str | None:
    if not policy.active or not policy.forbid_other_workspace_inputs:
        return None
    if not policy.target_artifacts:
        return None
    target = str(policy.target_artifacts[0] or "").strip()
    if not target:
        return None
    if policy.allowed_write_paths and target not in set(policy.allowed_write_paths):
        return None
    target_path = _safe_workspace_artifact_path(target, workspace_root=workspace_root)
    if target_path is None:
        return None
    if pre_run_workspace_state is not None:
        state = pre_run_workspace_state.get(str(target_path.resolve(strict=False)))
        if isinstance(state, Mapping) and bool(state.get("exists")):
            return None
    elif target_path.exists():
        return None
    return target


def _prompt_only_creation_tool_ids(tool_ids: Sequence[str]) -> list[str]:
    available = {str(tool_id) for tool_id in tool_ids}
    if "file_write" in available:
        return ["file_write"]
    if "file_edit" in available:
        return ["file_edit"]
    return list(tool_ids)


def _live_provider_request_overrides(
    request: ExecutionRequest,
    *,
    tool_ids: Sequence[str],
    workspace_root: Path,
) -> dict[str, Any]:
    metadata = getattr(request, "metadata", None)
    if not isinstance(metadata, Mapping):
        return {}
    if metadata.get("builder_retry") is not True:
        return {}

    overrides: dict[str, Any] = {"thinking": {"type": "disabled"}}
    primary_target = str(metadata.get("exclusive_write_owner_path") or "").strip()
    target_path = _safe_workspace_artifact_path(
        primary_target,
        workspace_root=workspace_root,
    )
    if (
        target_path is not None
        and not target_path.exists()
        and "file_write" in {str(tool_id) for tool_id in tool_ids}
    ):
        overrides["tool_choice"] = {
            "type": "function",
            "function": {"name": "file_write"},
        }
    return overrides


async def _execute_live_request(
    *,
    worker: WorkerDefinition,
    request: ExecutionRequest,
    tool_ids: Sequence[str],
    workspace_root: Path,
    args: argparse.Namespace,
    model: str,
    provider: LLMProvider,
    event_logger: SuperRunEventLogger | None = None,
) -> tuple[Any, list[dict[str, Any]], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    heartbeat_monitor: SuperHeartbeatMonitor | None = None

    def record_event(event: dict[str, Any]) -> None:
        payload = dict(event)
        events.append(payload)
        if event_logger is None:
            if heartbeat_monitor is not None:
                heartbeat_monitor.observe(payload)
            return
        trace_row = payload.get("trace_row")
        if (
            str(payload.get("event") or "").strip() == "trace.row"
            and isinstance(trace_row, dict)
        ):
            event_logger.emit_trace_rows([trace_row])
            if heartbeat_monitor is not None:
                heartbeat_monitor.observe(dict(trace_row))
            return
        event_logger.emit(payload)
        if heartbeat_monitor is not None:
            heartbeat_monitor.observe(payload)

    tool_runtime = LocalOrganismToolRuntime(
        tool_ids=list(tool_ids),
        workspace_root=workspace_root,
        approval_callback=_operator_intent_approval_callback(
            _operator_intent_policy_from_request(request),
            workspace_root=workspace_root,
            event_callback=record_event,
        ),
        event_callback=record_event,
    )
    completion_provider = ToolLoopCompletionProvider(
        provider=provider,
        tool_runtime=tool_runtime,
        default_model=model,
        max_rounds=int(args.max_tool_rounds),
        max_tool_calls=int(args.max_tool_calls),
        provider_request_overrides=_live_provider_request_overrides(
            request,
            tool_ids=tool_ids,
            workspace_root=workspace_root,
        ),
        event_callback=record_event,
    )
    executor = WorkerCoreExecutor(
        completion_provider=completion_provider,
        event_sink=CallbackEventSink(record_event),
    )
    heartbeat_monitor = SuperHeartbeatMonitor(
        event_callback=record_event,
        enabled=event_logger is not None,
    )
    await heartbeat_monitor.start()
    try:
        result = await executor.execute(worker, request)
    finally:
        await heartbeat_monitor.stop()
    raw_response = result.metadata.get("raw_response")
    executed_tools = (
        list(raw_response.get("executed_tools") or [])
        if isinstance(raw_response, dict)
        else []
    )
    return result, executed_tools, events


def _file_digest(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    except Exception:
        return "__unreadable__"
    return hashlib.sha256(data).hexdigest()


def _snapshot_file_state(paths: Sequence[Path]) -> dict[str, str | None]:
    return {
        str(path.resolve(strict=False)): _file_digest(path)
        for path in paths
    }


def _file_metadata(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {"exists": False, "size": None, "mtime_ns": None}
    except Exception as exc:
        return {
            "exists": False,
            "size": None,
            "mtime_ns": None,
            "error": type(exc).__name__,
        }
    return {
        "exists": True,
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _snapshot_workspace_file_metadata(workspace_root: Path) -> dict[str, dict[str, Any]]:
    root = workspace_root.resolve(strict=False)
    snapshot: dict[str, dict[str, Any]] = {}
    if not root.exists():
        return snapshot
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if dirname not in _GENERIC_SNAPSHOT_SKIP_DIR_NAMES
        ]
        for filename in filenames:
            path = Path(dirpath) / filename
            try:
                if path.is_symlink() or not path.is_file():
                    continue
            except OSError:
                continue
            snapshot[str(path.resolve(strict=False))] = _file_metadata(path)
    return snapshot


_GENERIC_FIRST_WRITE_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "app",
        "application",
        "build",
        "can",
        "complex",
        "create",
        "creates",
        "detail",
        "detailed",
        "do",
        "effects",
        "eventually",
        "file",
        "for",
        "from",
        "good",
        "help",
        "high",
        "html",
        "i",
        "in",
        "include",
        "includes",
        "javascript",
        "js",
        "make",
        "me",
        "need",
        "of",
        "overall",
        "please",
        "production",
        "quality",
        "return",
        "rich",
        "should",
        "special",
        "that",
        "the",
        "this",
        "to",
        "use",
        "with",
        "write",
        "you",
    }
)
_GENERIC_FIRST_WRITE_EXPLICIT_PATH_RE = re.compile(
    r"(?<![\w./-])([\w./-]+\.(?:html|htm|md|markdown|txt|json|csv|py|js|css))(?![\w./-])",
    re.IGNORECASE,
)


def _dedupe_preserving_order(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        rendered = str(item).strip()
        if not rendered or rendered in seen:
            continue
        seen.add(rendered)
        result.append(rendered)
    return result


def _relative_workspace_artifact_path(path: Path, *, workspace_root: Path) -> str | None:
    try:
        relative = path.resolve(strict=False).relative_to(workspace_root.resolve(strict=False))
    except (OSError, ValueError):
        return None
    if any(part in _GENERIC_SNAPSHOT_SKIP_DIR_NAMES for part in relative.parts):
        return None
    if any(part.startswith(".") for part in relative.parts):
        return None
    rendered = relative.as_posix()
    if not rendered or rendered.startswith("../") or rendered == ".":
        return None
    return rendered


def _existing_workspace_artifact_paths(
    snapshot: Mapping[str, Mapping[str, Any]],
    *,
    workspace_root: Path,
) -> list[str]:
    paths: list[str] = []
    for raw_path, state in sorted(snapshot.items()):
        if isinstance(state, Mapping) and not bool(state.get("exists")):
            continue
        relative = _relative_workspace_artifact_path(
            Path(str(raw_path)),
            workspace_root=workspace_root,
        )
        if relative:
            paths.append(relative)
    return _dedupe_preserving_order(paths)


def _generic_artifact_extension(objective: str) -> str:
    lowered = objective.lower()
    if any(token in lowered for token in ("html", "javascript", "canvas", "animation", "web app", "website", "page")):
        return ".html"
    if any(token in lowered for token in ("markdown", "report", "research", "memo", "document", "note")):
        return ".md"
    if "python" in lowered or re.search(r"\bscript\b|\bcli\b", lowered):
        return ".py"
    if "json" in lowered:
        return ".json"
    if "csv" in lowered or "spreadsheet" in lowered:
        return ".csv"
    return ".md"


def _slugify_artifact_stem(objective: str, *, fallback: str) -> str:
    words: list[str] = []
    for word in re.findall(r"[a-z0-9]+", objective.lower()):
        if word in _GENERIC_FIRST_WRITE_STOPWORDS:
            continue
        if len(word) <= 1:
            continue
        words.append(word)
        if len(words) >= 5:
            break
    return "-".join(words) or fallback


def _first_available_artifact_path(
    stem: str,
    extension: str,
    *,
    existing_paths: Sequence[str],
) -> str:
    existing = {str(path).strip() for path in existing_paths if str(path).strip()}
    candidate = f"{stem}{extension}"
    if candidate not in existing:
        return candidate
    for index in range(2, 100):
        candidate = f"{stem}-{index}{extension}"
        if candidate not in existing:
            return candidate
    return f"{stem}-{hashlib.sha1(stem.encode('utf-8')).hexdigest()[:8]}{extension}"


def _explicit_objective_artifact_paths(
    objective: str,
    *,
    workspace_root: Path,
) -> list[str]:
    paths: list[str] = []
    for match in _GENERIC_FIRST_WRITE_EXPLICIT_PATH_RE.finditer(objective):
        raw = match.group(1).strip().lstrip("./")
        if not raw or raw.startswith("../") or "/.dan-super/" in f"/{raw}/":
            continue
        path = Path(raw)
        if path.is_absolute():
            relative = _relative_workspace_artifact_path(path, workspace_root=workspace_root)
            if relative:
                paths.append(relative)
            continue
        if any(part in _GENERIC_SNAPSHOT_SKIP_DIR_NAMES or part.startswith(".") for part in path.parts):
            continue
        paths.append(path.as_posix())
    return _dedupe_preserving_order(paths)


def _generic_builder_retry_targets(
    objective: str,
    *,
    workspace_root: Path,
    pre_run_workspace_state: Mapping[str, Mapping[str, Any]],
    additive_recovery_required: bool,
    operator_intent_policy: OperatorIntentPolicy | None = None,
) -> list[str]:
    policy = operator_intent_policy or OperatorIntentPolicy()
    existing_paths = (
        []
        if policy.forbid_other_workspace_inputs or not policy.allow_existing_artifact_reuse
        else _existing_workspace_artifact_paths(
            pre_run_workspace_state,
            workspace_root=workspace_root,
        )
    )
    explicit_paths = _explicit_objective_artifact_paths(
        objective,
        workspace_root=workspace_root,
    )
    extension = _generic_artifact_extension(objective)
    existing_by_extension = [
        path for path in existing_paths if Path(path).suffix.lower() == extension
    ]
    fallback_stem = {
        ".html": "index",
        ".md": "artifact",
        ".py": "script",
        ".json": "artifact",
        ".csv": "data",
    }.get(extension, "artifact")
    generated = _first_available_artifact_path(
        _slugify_artifact_stem(objective, fallback=fallback_stem),
        extension,
        existing_paths=[*existing_paths, *explicit_paths],
    )
    if additive_recovery_required:
        return _dedupe_preserving_order(
            [*explicit_paths, *existing_by_extension, *existing_paths, generated]
        )[:6]
    create_like = bool(
        re.search(r"\b(build|create|generate|write|produce|implement)\b", objective.lower())
    )
    if create_like and generated:
        return _dedupe_preserving_order(
            [*explicit_paths, generated, *existing_by_extension, *existing_paths]
        )[:6]
    return _dedupe_preserving_order(
        [*explicit_paths, *existing_by_extension, *existing_paths, generated]
    )[:6]


def _target_missing_in_snapshot(
    relative_path: str,
    *,
    workspace_root: Path,
    snapshot: Mapping[str, Mapping[str, Any]],
) -> bool:
    target_path = _safe_workspace_artifact_path(
        relative_path,
        workspace_root=workspace_root,
    )
    if target_path is None:
        return False
    state = snapshot.get(str(target_path.resolve(strict=False)))
    if isinstance(state, Mapping):
        return not bool(state.get("exists"))
    return not target_path.exists()


def _materialize_missing_explicit_target_from_existing_artifact(
    objective: str,
    *,
    workspace_root: Path,
    recommended_write_paths: Sequence[str],
) -> dict[str, Any] | None:
    explicit_paths = set(
        _explicit_objective_artifact_paths(
            objective,
            workspace_root=workspace_root,
        )
    )
    if not explicit_paths or not recommended_write_paths:
        return None

    target_relative = str(recommended_write_paths[0] or "").strip()
    if target_relative not in explicit_paths:
        return None
    target_path = _safe_workspace_artifact_path(
        target_relative,
        workspace_root=workspace_root,
    )
    if target_path is None or target_path.exists():
        return None
    suffix = target_path.suffix.lower()
    if suffix not in _GENERIC_ALIAS_TEXT_EXTENSIONS:
        return None

    target_stem = target_path.stem.lower()
    for raw_source in list(recommended_write_paths)[1:]:
        source_relative = str(raw_source or "").strip()
        if not source_relative or source_relative == target_relative:
            continue
        source_path = _safe_workspace_artifact_path(
            source_relative,
            workspace_root=workspace_root,
        )
        if source_path is None or not source_path.is_file():
            continue
        if source_path.suffix.lower() != suffix:
            continue
        source_stem = source_path.stem.lower()
        if target_stem not in source_stem and source_stem not in target_stem:
            continue
        try:
            content = source_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(content, encoding="utf-8")
        return {
            "ok": True,
            "tool_id": "file_write",
            "arguments": {
                "path": target_relative,
                "content": content,
            },
            "result": {
                "path": str(target_path),
                "bytes": len(content.encode("utf-8")),
                "created": True,
                "source_path": source_relative,
                "fallback": "existing_artifact_alias",
            },
            "synthetic": True,
        }
    return None


def _file_metadata_for_paths(
    snapshot: Mapping[str, Mapping[str, Any]],
    paths: Sequence[str],
) -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    for raw_path in paths:
        path = Path(str(raw_path)).expanduser()
        rendered = str(path.resolve(strict=False))
        state = snapshot.get(rendered)
        states[rendered] = (
            dict(state)
            if isinstance(state, Mapping)
            else {"exists": False, "size": None, "mtime_ns": None}
        )
    return states


def _super_copy_workspace_to_worktree(
    workspace_root: Path,
    worktree_root: Path,
) -> None:
    source_root = workspace_root.resolve(strict=False)
    target_root = worktree_root.resolve(strict=False)
    allowed_parent = (source_root / ".dan-super" / "worktrees").resolve(strict=False)
    if not _path_is_under(target_root, allowed_parent):
        raise ValueError(f"refusing to prepare worktree outside {allowed_parent}: {target_root}")
    if target_root.exists():
        shutil.rmtree(target_root)
    target_root.mkdir(parents=True, exist_ok=True)
    skip_names = set(_GENERIC_SNAPSHOT_SKIP_DIR_NAMES)
    skip_names.add(".dan-super")
    for child in source_root.iterdir() if source_root.exists() else []:
        if child.name in skip_names:
            continue
        if child.is_symlink():
            continue
        destination = target_root / child.name
        if child.is_dir():
            shutil.copytree(
                child,
                destination,
                ignore=shutil.ignore_patterns(*sorted(skip_names)),
                symlinks=False,
            )
        elif child.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, destination)


def _super_worktree_owner_scope(task: Mapping[str, Any]) -> str:
    task_id = str(task.get("task_id") or "").strip() or "ready-task"
    owned = ",".join(_super_plan_task_owned_paths(task)) or task_id
    return f"plan-task:{task_id}:{owned}"


def _super_worktree_packet_id(task: Mapping[str, Any], run_task_id: str) -> str:
    task_id = str(task.get("task_id") or "").strip() or "ready-task"
    return f"super-frontier:{hashlib.sha256(f'{run_task_id}:{task_id}'.encode('utf-8')).hexdigest()[:16]}"


def _super_worktree_relative_mutation_paths(
    worktree_paths: Sequence[str],
    *,
    worktree_root: Path,
    task: Mapping[str, Any],
) -> list[str]:
    allowed = _super_plan_task_owned_paths(task)
    relative_paths: list[str] = []
    seen: set[str] = set()
    for raw_path in worktree_paths:
        path = Path(str(raw_path)).expanduser().resolve(strict=False)
        try:
            relative = path.relative_to(worktree_root.resolve(strict=False)).as_posix()
        except ValueError:
            continue
        if allowed and not any(_super_plan_rel_paths_overlap(relative, owner) for owner in allowed):
            continue
        if relative in seen:
            continue
        seen.add(relative)
        relative_paths.append(relative)
    return relative_paths


def _super_apply_worktree_files(
    *,
    worktree_root: Path,
    workspace_root: Path,
    relative_paths: Sequence[str],
) -> list[dict[str, Any]]:
    synthetic_tools: list[dict[str, Any]] = []
    for relative in relative_paths:
        source = (worktree_root / relative).resolve(strict=False)
        target = (workspace_root / relative).resolve(strict=False)
        if not _path_is_under(target, workspace_root):
            continue
        if not source.is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        synthetic_tools.append(
            {
                "ok": True,
                "tool_id": "file_write",
                "arguments": {"path": relative},
                "result": {
                    "path": str(target),
                    "bytes": int(target.stat().st_size),
                    "created": True,
                    "source_path": str(source),
                    "fallback": "worktree_admitted_diff_apply",
                },
                "synthetic": True,
            }
        )
    return synthetic_tools


def _current_file_metadata_for_paths(paths: Sequence[str]) -> dict[str, dict[str, Any]]:
    return {
        str(Path(str(raw_path)).expanduser().resolve(strict=False)): _file_metadata(
            Path(str(raw_path)).expanduser()
        )
        for raw_path in paths
    }


def _changed_paths_from_snapshot(
    snapshot: dict[str, str | None],
    paths: Sequence[Path],
) -> list[str]:
    changed: list[str] = []
    seen: set[str] = set()
    for path in paths:
        rendered = str(path.resolve(strict=False))
        if rendered in seen:
            continue
        seen.add(rendered)
        if snapshot.get(rendered) != _file_digest(path):
            changed.append(rendered)
    return changed


def _coerce_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _normalize_token_usage(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, int] = {}
    for key, value in raw.items():
        try:
            normalized[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    prompt = int(raw.get("prompt_tokens", raw.get("prompt", 0)) or 0)
    completion = int(raw.get("completion_tokens", raw.get("completion", 0)) or 0)
    total = int(raw.get("total_tokens", prompt + completion) or (prompt + completion))
    normalized["prompt_tokens"] = prompt
    normalized["completion_tokens"] = completion
    normalized["total_tokens"] = total
    return normalized


def _merge_token_usage(*usage_maps: Any) -> dict[str, int]:
    merged: dict[str, int] = {}
    for usage_map in usage_maps:
        for key, value in _normalize_token_usage(usage_map).items():
            merged[key] = int(merged.get(key, 0) or 0) + int(value)
    return merged


def _format_token_usage(raw: Any) -> str:
    usage = _normalize_token_usage(raw)
    if not usage:
        return "unavailable"
    parts = [
        f"prompt={usage.get('prompt_tokens', 0)}",
        f"completion={usage.get('completion_tokens', 0)}",
        f"total={usage.get('total_tokens', 0)}",
    ]
    if usage.get("cached_input_tokens"):
        parts.append(f"cached_input={usage['cached_input_tokens']}")
    if usage.get("cache_write_tokens"):
        parts.append(f"cache_write={usage['cache_write_tokens']}")
    return ", ".join(parts)


def _extract_completion_usage_from_raw(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    usage_totals = raw.get("usage_totals")
    if usage_totals is not None:
        return _normalize_token_usage(usage_totals)
    provider_result = raw.get("provider_result")
    if isinstance(provider_result, dict):
        return _normalize_token_usage(provider_result.get("usage"))
    return {}


def _extract_execution_usage(result: Any) -> dict[str, int]:
    metadata = dict(getattr(result, "metadata", {}) or {})
    return _merge_token_usage(
        _extract_completion_usage_from_raw(metadata.get("raw_response")),
        _extract_completion_usage_from_raw(metadata.get("initial_raw_response")),
        _extract_completion_usage_from_raw(metadata.get("repair_raw_response")),
    )


def _failed_validation_payload(
    *,
    reason: str,
    missing_requirements: Sequence[str] | None = None,
) -> dict[str, Any]:
    items = [str(item).strip() for item in (missing_requirements or []) if str(item).strip()]
    if not items:
        items = [reason]
    return {
        "passed": False,
        "overall_score": 0.0,
        "dimension_scores": {},
        "repair_brief": reason,
        "missing_requirements": items,
        "comparison_note": reason,
    }


def _normalize_validation_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return _failed_validation_payload(
            reason="validator did not return a structured validation payload"
        )
    dimension_scores = payload.get("dimension_scores")
    missing_requirements = payload.get("missing_requirements")
    blocking_current = _super_plan_string_list(payload.get("blocking_current_task_failures") or [])
    deferred_gaps = _super_plan_string_list(payload.get("deferred_task_gaps") or [])
    remaining_work = _super_plan_string_list(payload.get("remaining_work") or [])
    ready_next = _super_plan_string_list(payload.get("ready_next_task_ids") or [])
    dependency_revisions = [
        dict(item)
        for item in (payload.get("dependency_revisions") or [])
        if isinstance(item, Mapping)
    ] if isinstance(payload.get("dependency_revisions"), list) else []
    task_graph_update = _super_plan_graph_update_payload(payload)
    return {
        "passed": bool(payload.get("passed")),
        "overall_score": _coerce_float(payload.get("overall_score")),
        "dimension_scores": dict(dimension_scores) if isinstance(dimension_scores, dict) else {},
        "repair_brief": str(payload.get("repair_brief") or "").strip(),
        "missing_requirements": (
            [str(item).strip() for item in missing_requirements if str(item).strip()]
            if isinstance(missing_requirements, list)
            else []
        ),
        "blocking_current_task_failures": blocking_current,
        "deferred_task_gaps": deferred_gaps,
        "remaining_work": remaining_work,
        "ready_next_task_ids": ready_next,
        "dependency_revisions": dependency_revisions,
        "task_graph_update": task_graph_update,
        "aspect_coverage": [
            dict(item)
            for item in (payload.get("aspect_coverage") or [])
            if isinstance(item, Mapping)
        ] if isinstance(payload.get("aspect_coverage"), list) else [],
        "completion_scope": str(payload.get("completion_scope") or "").strip(),
        "comparison_note": str(payload.get("comparison_note") or "").strip(),
    }


def _extract_validation_payload(outputs: Any) -> Any:
    if not isinstance(outputs, dict):
        return outputs
    if any(key in outputs for key in ("passed", "overall_score", "missing_requirements")):
        return outputs
    for key in ("result", "text"):
        raw = outputs.get(key)
        if not isinstance(raw, str):
            continue
        text = raw.strip()
        if not text:
            continue
        try:
            decoded = json.loads(text)
        except Exception:
            continue
        if isinstance(decoded, dict):
            return decoded
    return outputs


_OPERATIONAL_FINAL_ANSWER_RECEIPTS = frozenset(
    {
        "completed",
        "complete",
        "done",
        "finished",
        "run finished",
        "run completed",
        "run complete",
        "run log completed",
        "super dan completed",
        "super dan complete",
        "super dan finished",
        "dan completed",
        "dan finished",
    }
)


def _answer_text_from_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        answer = _answer_text_from_outputs(value)
        return answer
    if isinstance(value, (list, tuple)):
        parts = [_single_line(item) for item in value if _single_line(item)]
        return "; ".join(parts)
    text = str(value or "").strip()
    if not text:
        return ""
    if text[:1] in {"{", "["}:
        parsed = parse_jsonish_payload(text)
        if isinstance(parsed, (Mapping, list, tuple)):
            answer = _answer_text_from_value(parsed)
            if answer:
                return answer
    return _single_line(text)


def _answer_text_from_outputs(outputs: Any) -> str:
    parsed = parse_jsonish_payload(outputs)
    if isinstance(parsed, Mapping):
        for key in ("answer", "final_answer", "final_response", "response"):
            answer = _answer_text_from_value(parsed.get(key))
            if answer:
                return answer
        for key in ("result", "text"):
            raw = parsed.get(key)
            if isinstance(raw, str) and raw.strip()[:1] in {"{", "["}:
                answer = _answer_text_from_value(raw)
                if answer:
                    return answer
            answer = _answer_text_from_value(raw)
            if answer:
                return answer
        if not any(key in parsed for key in ("change_summary", "target_files", "files_created", "files_changed")):
            answer = _answer_text_from_value(parsed.get("summary"))
            if answer:
                return answer
        return ""
    if isinstance(parsed, (list, tuple)):
        return _answer_text_from_value(parsed)
    return _answer_text_from_value(outputs)


def _normalized_final_answer_receipt(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _single_line(text).lower()).strip()


def _answer_text_satisfaction(
    text: str,
    *,
    objective: str,
) -> tuple[bool, str]:
    answer = _single_line(text)
    if not answer:
        return False, "No final answer text was returned."
    normalized = _normalized_final_answer_receipt(answer)
    if normalized in _OPERATIONAL_FINAL_ANSWER_RECEIPTS:
        return False, "The final response was only an operational completion receipt."
    if re.fullmatch(r"(?:run\s+log\s+)?(?:completed|complete|done|finished)", normalized):
        return False, "The final response was only an operational completion receipt."
    word_count = len(re.findall(r"[A-Za-z0-9]+", answer))
    exact_reply_requested = bool(
        re.search(r"\b(?:reply|respond|say)\s+with\s+exactly\b", str(objective or "").lower())
    )
    if word_count < 3 and not exact_reply_requested:
        return False, "The final answer was too thin to satisfy the request."
    return True, ""


def _live_answer_quality(outputs: Any, *, objective: str) -> dict[str, Any]:
    answer = _answer_text_from_outputs(outputs)
    passed, reason = _answer_text_satisfaction(answer, objective=objective)
    return {
        "passed": bool(passed),
        "answer": answer,
        "reason": reason,
        "missing_requirements": [] if passed else [reason],
    }


def _answer_validation_payload(answer_quality: Mapping[str, Any]) -> dict[str, Any]:
    if bool(answer_quality.get("passed")):
        return {
            "passed": True,
            "overall_score": 1.0,
            "dimension_scores": {"answer_delivery": 1.0},
            "repair_brief": "",
            "missing_requirements": [],
            "comparison_note": "A substantive in-session answer was delivered.",
        }
    reason = str(answer_quality.get("reason") or "The final answer is missing.").strip()
    return _failed_validation_payload(
        reason=reason,
        missing_requirements=answer_quality.get("missing_requirements") or [reason],
    )


def _normalize_plan_validation_payload(
    payload: Any,
    *,
    plan_files: Sequence[str],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "passed": False,
            "overall_score": 0.0,
            "first_build_slice": [],
            "blocking_issues": ["plan validator did not return a structured payload"],
            "suggested_fixes": [],
            "comparison_note": "Plan validation returned an unstructured response.",
            "plan_files": list(plan_files),
            "task_graph": [],
            "ready_task_ids": [],
            "deferred_task_ids": [],
            "dependency_revisions": [],
        }
    blocking = payload.get("blocking_issues")
    suggested = payload.get("suggested_fixes")
    task_graph = _super_plan_task_graph(payload.get("task_graph") or [])
    first_slice = payload.get("first_build_slice") or payload.get("assigned_task_ids")
    if not isinstance(first_slice, list):
        first_slice = []
    ready_ids = _super_plan_string_list(payload.get("ready_task_ids") or [])
    if not ready_ids:
        ready_ids = [str(item).strip() for item in first_slice if str(item).strip()]
    if not ready_ids and task_graph:
        ready_ids = _super_plan_ready_task_ids(task_graph)
    deferred_ids = _super_plan_string_list(payload.get("deferred_task_ids") or [])
    if not deferred_ids and task_graph:
        deferred_ids = _super_plan_deferred_task_ids(task_graph, ready_ids)
    dependency_revisions = [
        dict(item)
        for item in (payload.get("dependency_revisions") or [])
        if isinstance(item, Mapping)
    ] if isinstance(payload.get("dependency_revisions"), list) else []
    task_graph_update = _super_plan_graph_update_payload(payload)
    return {
        "passed": bool(payload.get("passed")),
        "overall_score": _coerce_float(payload.get("overall_score")),
        "first_build_slice": [str(item).strip() for item in first_slice if str(item).strip()] or list(ready_ids),
        "ready_task_ids": list(ready_ids),
        "deferred_task_ids": list(deferred_ids),
        "task_graph": task_graph,
        "dependency_revisions": dependency_revisions,
        "task_graph_update": task_graph_update,
        "blocking_issues": (
            [str(item).strip() for item in blocking if str(item).strip()]
            if isinstance(blocking, list)
            else []
        ),
        "suggested_fixes": (
            [str(item).strip() for item in suggested if str(item).strip()]
            if isinstance(suggested, list)
            else []
        ),
        "comparison_note": str(payload.get("comparison_note") or "").strip(),
        "plan_files": list(plan_files),
    }


def _merge_validation_failures(
    validation: dict[str, Any],
    failures: Sequence[str],
) -> dict[str, Any]:
    issues = [str(item).strip() for item in failures if str(item).strip()]
    if not issues:
        return dict(validation)
    merged = _normalize_validation_payload(validation)
    existing = list(merged.get("missing_requirements") or [])
    for issue in issues:
        if issue not in existing:
            existing.append(issue)
    merged["passed"] = False
    merged["overall_score"] = min(_coerce_float(merged.get("overall_score")), 0.49)
    merged["missing_requirements"] = existing
    if not str(merged.get("repair_brief") or "").strip():
        merged["repair_brief"] = issues[0]
    if not str(merged.get("comparison_note") or "").strip():
        merged["comparison_note"] = issues[0]
    return merged


_VALIDATION_ERROR_LINE_RE = re.compile(
    r"(?im)^\s*(?:"
    r"ERROR\b|ERROR:|SCRIPT ERROR\b|FATAL\b|CRITICAL\b|PANIC\b|"
    r"FAIL\b|FAIL:|FAILED\b|"
    r"(?:\[[^\n\]]+\]\s*)?Status:\s*FAIL\b|"
    r"Traceback \(most recent call last\)|Parser Error\b|Parse Error\b|"
    r"Unhandled exception\b|AssertionError\b|Segmentation fault\b"
    r")"
)


def _validation_commands_from_args(args: argparse.Namespace) -> list[str]:
    commands: list[str] = []
    seen: set[str] = set()
    for raw in getattr(args, "validation_command", []) or []:
        command = str(raw or "").strip()
        if not command or command in seen:
            continue
        seen.add(command)
        commands.append(command)
    return commands


def _validation_output_preview(text: str, *, limit: int = 4000) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 20)] + "\n...[truncated]..."


def _validation_error_lines(stdout: str, stderr: str, *, limit: int = 3) -> list[str]:
    combined = "\n".join(part for part in (stderr, stdout) if str(part or "").strip())
    lines: list[str] = []
    for match in _VALIDATION_ERROR_LINE_RE.finditer(combined):
        line_start = combined.rfind("\n", 0, match.start()) + 1
        line_end = combined.find("\n", match.end())
        if line_end < 0:
            line_end = len(combined)
        line = _display_text(combined[line_start:line_end])
        if line and line not in lines:
            lines.append(line)
        if len(lines) >= limit:
            break
    return lines


def _validation_shell_failure(
    *,
    command: str,
    exit_code: int,
    stdout: str,
    stderr: str,
) -> str | None:
    error_lines = _validation_error_lines(stdout, stderr)
    if exit_code != 0:
        detail = error_lines[0] if error_lines else _display_text(stderr or stdout)
        suffix = f": {_truncate_text(detail, limit=220)}" if detail else ""
        return f"Validation command failed: `{command}` exited {exit_code}{suffix}"
    if error_lines:
        return (
            "Validation command reported runtime/compiler errors or validation failure output despite exit 0: "
            f"`{command}`: {_truncate_text(error_lines[0], limit=220)}"
        )
    return None


async def _run_validation_shell_commands(
    *,
    args: argparse.Namespace,
    workspace_root: Path,
    model: str,
    worker_id: str,
    event_logger: SuperRunEventLogger | None,
) -> list[str]:
    commands = _validation_commands_from_args(args)
    if not commands:
        return []
    timeout = max(1, int(getattr(args, "validation_timeout", 120) or 120))
    continue_on_failure = bool(getattr(args, "validation_continue_on_failure", False))
    failures: list[str] = []
    for index, command in enumerate(commands, start=1):
        _log_live_event(
            event_logger,
            "live.validation.shell_check.started",
            worker_id=worker_id,
            model=model,
            command=command,
            command_index=index,
            timeout_seconds=timeout,
            workspace_root=str(workspace_root),
        )
        env = dict(os.environ)
        env["DAN_WORKSPACE_ROOT"] = str(workspace_root)
        env["PWD"] = str(workspace_root)
        exit_code = -1
        stdout = ""
        stderr = ""
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workspace_root),
                env=env,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
            exit_code = int(proc.returncode or 0)
            stdout = (stdout_bytes or b"").decode("utf-8", errors="replace")
            stderr = (stderr_bytes or b"").decode("utf-8", errors="replace")
        except asyncio.TimeoutError:
            try:
                proc.kill()  # type: ignore[name-defined]
                await proc.wait()  # type: ignore[name-defined]
            except Exception:
                pass
            stderr = f"Command timed out after {timeout} seconds."
        except Exception as exc:
            stderr = f"{type(exc).__name__}: {exc}"
        failure = _validation_shell_failure(
            command=command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )
        error_lines = _validation_error_lines(stdout, stderr)
        if failure:
            failures.append(failure)
        _log_live_event(
            event_logger,
            "live.validation.shell_check.completed",
            worker_id=worker_id,
            model=model,
            command=command,
            command_index=index,
            exit_code=exit_code,
            passed=not bool(failure),
            failure=failure,
            error_lines=list(error_lines),
            stdout_preview=_validation_output_preview(stdout),
            stderr_preview=_validation_output_preview(stderr),
        )
        if failure and not continue_on_failure:
            remaining = len(commands) - index
            if remaining > 0:
                _log_live_event(
                    event_logger,
                    "live.validation.shell_check.short_circuited",
                    worker_id=worker_id,
                    model=model,
                    failed_command=command,
                    failed_command_index=index,
                    skipped_count=remaining,
                    reason="previous validation command failed",
                )
            break
    return failures


def _existing_required_files_from_snapshot(
    snapshot: dict[str, str | None],
    required_paths: Sequence[Path],
) -> list[str]:
    existing: list[str] = []
    for path in required_paths:
        rendered = str(path.resolve(strict=False))
        if snapshot.get(rendered) is not None:
            existing.append(rendered)
    return existing


def _website_static_validation_failures(
    report: SuperOrganismReport,
    *,
    required_paths: Sequence[Path],
    changed_required_paths: Sequence[str],
    template_phrases: Sequence[str],
) -> list[str]:
    failures: list[str] = []
    if not changed_required_paths:
        failures.append("The live run did not change any required website files.")
    if not required_paths:
        return failures
    index_path = Path(required_paths[0])
    if not index_path.exists():
        return failures
    try:
        html = " ".join(index_path.read_text(encoding="utf-8", errors="ignore").lower().split())
    except Exception as exc:
        failures.append(f"Failed to inspect {index_path}: {type(exc).__name__}: {exc}")
        return failures
    target = " ".join(str(report.target or "").lower().split())
    if target and len(target) >= 24 and target in html:
        failures.append("The generated website still echoes the raw operator prompt as page copy.")
    template_hits = _website_template_phrase_hits(html, template_phrases)
    if len(template_hits) >= 2:
        failures.append(
            "The generated website still looks like the generic Super DAN contract/demo template. "
            f"Template phrase hits in index.html: {_display_template_phrase_hits(template_hits)}. "
            "Remove or rename enough exact hits so fewer than 2 remain."
        )
    return failures


def _website_template_phrase_hits(
    normalized_html: str,
    template_phrases: Sequence[str],
) -> list[str]:
    hits: list[str] = []
    seen: set[str] = set()
    for phrase in template_phrases:
        display = " ".join(str(phrase or "").split())
        normalized = display.lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        if normalized in normalized_html:
            hits.append(display)
    return hits


def _display_template_phrase_hits(hits: Sequence[str], *, limit: int = 8) -> str:
    visible = [f"'{hit}'" for hit in hits[:limit]]
    if len(hits) > limit:
        visible.append(f"+{len(hits) - limit} more")
    return ", ".join(visible)


def _log_final_validation_event(
    event_logger: SuperRunEventLogger | None,
    *,
    worker_id: str,
    model: str,
    validation: dict[str, Any],
    deterministic_failures: Sequence[str] | None = None,
    changed_required_files: Sequence[str] | None = None,
    builder_retry_attempted: bool = False,
    repair_attempted: bool = False,
    repair_exhausted: bool = False,
    task_graph_state: Mapping[str, Any] | None = None,
) -> None:
    retry_attempted = bool(builder_retry_attempted)
    _log_live_event(
        event_logger,
        "live.validation.completed",
        worker_id=worker_id,
        model=model,
        status=validation.get("status"),
        passed=validation.get("passed"),
        overall_score=validation.get("overall_score"),
        completion_scope=validation.get("completion_scope") or None,
        blocking_current_task_failures=list(validation.get("blocking_current_task_failures") or []) or None,
        deferred_task_gaps=list(validation.get("deferred_task_gaps") or []) or None,
        remaining_work=list(validation.get("remaining_work") or []) or None,
        ready_next_task_ids=list(validation.get("ready_next_task_ids") or []) or None,
        dependency_revisions=list(validation.get("dependency_revisions") or []) or None,
        task_graph_update=(
            dict(validation.get("task_graph_update") or {})
            if isinstance(validation.get("task_graph_update"), Mapping)
            else None
        ),
        task_graph_state=dict(task_graph_state or {}) or None,
        aspect_coverage=list(validation.get("aspect_coverage") or []) or None,
        deterministic_failures=list(deterministic_failures or []) or None,
        changed_required_files=list(changed_required_files or []),
        builder_retry_attempted=retry_attempted,
        repair_attempted=bool(repair_attempted),
        repair_exhausted=bool(repair_exhausted),
        tool_calls=int(validation.get("tool_calls") or 0),
        event_count=int(validation.get("event_count") or 0),
    )


async def _run_live_validation(
    *,
    worker: WorkerDefinition,
    request: ExecutionRequest,
    tool_ids: Sequence[str],
    workspace_root: Path,
    args: argparse.Namespace,
    model: str,
    provider: LLMProvider,
    event_logger: SuperRunEventLogger | None = None,
) -> dict[str, Any]:
    _log_live_event(
        event_logger,
        "live.validation.started",
        worker_id=worker.id,
        model=model,
        tool_ids=list(tool_ids),
    )
    shell_validation_ran = bool(_validation_commands_from_args(args))
    deterministic_failures: list[str] = []
    if shell_validation_ran:
        pre_model_shell_failures = await _run_validation_shell_commands(
            args=args,
            workspace_root=workspace_root,
            model=model,
            worker_id=worker.id,
            event_logger=event_logger,
        )
        if pre_model_shell_failures:
            deterministic_failures.extend(pre_model_shell_failures)
            validation = _merge_validation_failures(
                _normalize_validation_payload(
                    {
                        "passed": False,
                        "overall_score": 0.0,
                        "repair_brief": pre_model_shell_failures[0],
                        "missing_requirements": list(pre_model_shell_failures),
                        "comparison_note": pre_model_shell_failures[0],
                    }
                ),
                pre_model_shell_failures,
            )
            validation["status"] = "failed"
            validation["tool_calls"] = 0
            validation["event_count"] = 0
            validation["error"] = pre_model_shell_failures[0]
            validation["token_usage"] = {}
            validation["deterministic_failures"] = list(deterministic_failures)
            _log_live_event(
                event_logger,
                "live.validation.model_skipped",
                worker_id=worker.id,
                model=model,
                reason="validation command failed before model validation",
                passed=False,
            )
            return validation

    result, executed_tools, events = await _execute_live_request(
        worker=worker,
        request=request,
        tool_ids=tool_ids,
        workspace_root=workspace_root,
        args=args,
        model=model,
        provider=provider,
        event_logger=event_logger,
    )
    token_usage = _extract_execution_usage(result)
    validation = _normalize_validation_payload(
        _extract_validation_payload(dict(result.outputs))
    )
    if result.status != "completed":
        validation = _merge_validation_failures(
            validation,
            [result.error or "validator did not complete successfully"],
        )
    validation["status"] = result.status
    validation["tool_calls"] = len(executed_tools)
    validation["event_count"] = len(events)
    validation["error"] = result.error
    validation["token_usage"] = token_usage
    _log_live_event(
        event_logger,
        "live.validation.model_completed",
        worker_id=worker.id,
        model=model,
        status=validation.get("status"),
        passed=validation.get("passed"),
        tool_calls=len(executed_tools),
        event_count=len(events),
    )
    deterministic_failures.extend(
        str(item).strip()
        for item in (validation.get("deterministic_failures") or [])
        if str(item).strip()
    )
    shell_failures = []
    if not shell_validation_ran:
        shell_failures = await _run_validation_shell_commands(
            args=args,
            workspace_root=workspace_root,
            model=model,
            worker_id=worker.id,
            event_logger=event_logger,
        )
    if shell_failures:
        validation = _merge_validation_failures(validation, shell_failures)
        for failure in shell_failures:
            if failure not in deterministic_failures:
                deterministic_failures.append(failure)
    validation["deterministic_failures"] = list(deterministic_failures)
    return validation


async def _run_live_website_build(
    report: SuperOrganismReport,
    args: argparse.Namespace,
    *,
    model: str,
    provider: LLMProvider,
    run_trace_id: str,
    run_task_id: str,
    event_logger: SuperRunEventLogger | None = None,
) -> dict[str, Any]:
    choice = _super_live_choice(report, args)
    website_tool_ids = _live_choice_tool_ids(choice)
    website_preferred_tool_ids = _live_choice_preferred_tool_ids(
        choice,
        ["file_write", "file_edit", "file_read", "list_directory"],
    )
    website_read_only_tool_ids = _live_choice_read_only_tool_ids(choice)
    existing_preferred_coordinated_files = _live_choice_existing_preferred_coordinated_files(choice)
    template_phrases = _live_choice_template_phrases(choice)
    pacing_policy = _live_pacing_policy(forbid_scratch_files=True)
    worker_id = "super-dan.live.website-builder"
    workspace_root, artifact_root, relative_files, required_paths = _live_artifact_layout(args, choice)
    workspace_root.mkdir(parents=True, exist_ok=True)
    file_snapshot = _snapshot_file_state(required_paths)
    objective_context = _live_website_objective_context(
        report,
        workspace_root=workspace_root,
        required_paths=required_paths,
    )
    if objective_context.get("normalized"):
        _log_live_event(
            event_logger,
            "live.objective.normalized",
            reason=objective_context.get("reason"),
            original_objective=objective_context.get("original_objective"),
            effective_objective=objective_context.get("effective_objective"),
            previous_failure_hint=objective_context.get("previous_failure_hint") or None,
            existing_required_files=list(objective_context.get("existing_required_files") or []),
        )
    worker_brief = coding_brief(
            role=RoleSpec(
                role_label="workspace_worker",
                responsibility="Build the requested Super DAN static website artifact.",
                success_criteria=[
                    "All required website files exist.",
                    "Existing websites receive material required-file changes; multi-file coordination is preferred for broad patches.",
                    "The result is inspectable by opening index.html directly.",
                ],
                artifact_targets=list(relative_files),
                trace_role="super-dan.live.website-builder",
            ),
            task=_live_website_task(
                report,
                artifact_root=artifact_root,
                relative_files=relative_files,
                objective_context=objective_context,
            ),
            scope=f"workspace={workspace_root}; artifact_root={artifact_root}; native Super DAN live website build",
            hard_constraints=[
                "Actually create or update all required website files with file_write or file_edit.",
                "Keep writes inside the requested workspace/artifact paths.",
                "Do not install dependencies or require a build step.",
                "Do not create scratch or throwaway files outside the required website artifact set.",
                "For existing websites, materially change at least one required file; prefer broader coordinated edits when the requested patch naturally spans files.",
            ],
            soft_constraints=[
                "Build a polished static product website with distinctive layout, motion, and concise copy.",
                "Treat the supplied coordination tickets as the working backlog and satisfy the final audit gate.",
                "If the required files already exist, improve them incrementally instead of replacing everything at once.",
                "If this is an existing website redesign, update HTML structure plus CSS visual language and/or JS motion.",
                "After one failed or truncated large write, immediately switch to a smaller section-level strategy.",
                "Avoid rereading the same file unless the next edit truly needs exact line grounding.",
                "Use local file tools for every required file before finalizing.",
                "Keep dependencies zero; no package install, no external CDN requirement.",
            ],
            pacing_policy=pacing_policy,
            tool_policy={
                "allowed_tool_ids": list(website_tool_ids),
                "preferred_tool_ids": list(website_preferred_tool_ids),
                "max_tool_calls": int(args.max_tool_calls),
            },
            contract_snippets=[
                *_super_dan_stage_snippets("builder", tool_ids=website_tool_ids),
            ],
            output_contract=OutputContract(
                definition_of_done=(
                    "All required files exist on disk and the final response names the files created, "
                    "a concise validation plan, and any remaining risks."
                ),
                expected_return_shape=_live_expected_return_shape(),
            ),
            sampling_policy={
                "profile": choice.sampling_policy,
                "temperature": 0.35,
                "max_tokens": _SUPER_DAN_WORKER_MAX_TOKENS,
            },
            evidence=_super_report_evidence_blocks(report),
            input_payload={
                "objective": objective_context.get("effective_objective") or report.target,
                "original_objective": report.target,
                "objective_normalization": dict(objective_context),
                "workspace_root": str(workspace_root),
                "artifact_root": str(artifact_root),
                "artifact_files": list(relative_files),
                "organism_id": report.organism_id,
                "cell_count": report.cell_count,
                "active_cell_cap": report.active_cell_cap,
                "organ_counts": dict(report.organ_counts),
                "delivery_plan": [node.model_dump(mode="json") for node in report.delivery_plan],
                "write_pacing": dict(pacing_policy),
                "existing_required_files": _existing_required_files_from_snapshot(
                    file_snapshot,
                    required_paths,
                ),
                "existing_website_change_policy": {
                    "minimum_required_changed_files": 1,
                    "preferred_coordinated_required_files": existing_preferred_coordinated_files,
                    "prefer_coordinated_when_objective_spans_files": True,
                },
                "shared_board": (
                    report.shared_board.model_dump(mode="json") if report.shared_board is not None else None
                ),
                "coordination_tickets": [
                    ticket.model_dump(mode="json") for ticket in report.coordination_tickets
                ],
                "handoff_packets": [packet.model_dump(mode="json") for packet in report.handoff_packets],
                "final_audit": (
                    report.final_audit.model_dump(mode="json") if report.final_audit is not None else None
                ),
            },
            metadata={
                "surface": "super_organism",
                "mode": "live",
                "tool_budget_profile": "super_dan_live",
                "trace_id": run_trace_id,
                "root_task_id": run_task_id,
                "organism_id": report.organism_id,
                "organ_id": "super-dan.live.website",
                "organism_stage": "execution",
                "worker_id": worker_id,
            },
        )
    worker = _live_cell_from_brief(
        model=model,
        brief=worker_brief,
        worker_id=worker_id,
        organism_stage="execution",
    )
    request = _request_from_live_brief(worker_brief, args=args)
    _log_live_event(
        event_logger,
        "live.website_build.started",
        model=model,
        workspace_root=str(workspace_root),
        artifact_root=str(artifact_root),
        tool_ids=list(website_tool_ids),
    )
    result, executed_tools, events = await _execute_live_request(
        worker=worker,
        request=request,
        tool_ids=website_tool_ids,
        workspace_root=workspace_root,
        args=args,
        model=model,
        provider=provider,
        event_logger=event_logger,
    )
    _log_live_event(
        event_logger,
        "live.website_build.completed",
        model=model,
        status=result.status,
        tool_calls=len(executed_tools),
        event_count=len(events),
    )
    build_token_usage = _extract_execution_usage(result)
    existing_paths = [path for path in required_paths if path.exists()]
    missing_paths = [path for path in required_paths if not path.exists()]
    changed_required_paths = _changed_paths_from_snapshot(file_snapshot, required_paths)
    mutated_paths = _mutation_paths_from_tools(executed_tools, workspace_root=workspace_root)
    error = result.error
    if missing_paths and not error:
        error = (
            "live build finished without creating required files: "
            + ", ".join(str(path) for path in missing_paths)
        )
    validation = _failed_validation_payload(
        reason=error or "live website build did not meet the exit contract",
        missing_requirements=[str(path) for path in missing_paths],
    )

    async def run_website_validator(
        current_changed_required_paths: Sequence[str],
    ) -> dict[str, Any]:
        validator_worker_id = "super-dan.live.website.validator"
        validator_brief = review_brief(
            role=RoleSpec(
                role_label="validator_website",
                responsibility="Validate the Super DAN website artifact in read-only mode.",
                success_criteria=[
                    "Required files were read directly.",
                    "The artifact materially satisfies the operator objective.",
                    "The result is not a generic Super DAN execution-contract demo.",
                ],
                artifact_targets=list(relative_files),
                trace_role="super-dan.live.website.validator",
            ),
            task=_live_website_validation_task(
                report,
                artifact_root=artifact_root,
                relative_files=relative_files,
                objective_context=objective_context,
            ),
            scope=f"workspace={workspace_root}; artifact_root={artifact_root}; native Super DAN website validation",
            hard_constraints=[
                "Read-only validation only; do not write or edit files.",
                "Inspect the required website files directly before deciding.",
                "Fail if the result is still a generic execution-contract/demo template.",
                "Fail if the page mostly echoes the raw operator prompt.",
            ],
            soft_constraints=[
                "Prefer concrete missing requirements over vague criticism.",
                "Judge objective alignment, not just file existence.",
            ],
            allowed_tool_ids=website_read_only_tool_ids,
            failure_phrases=template_phrases,
            tool_policy={
                "allowed_tool_ids": list(website_read_only_tool_ids),
                "preferred_tool_ids": ["file_read", "list_directory"],
                "max_tool_calls": max(4, min(int(args.max_tool_calls), 24)),
            },
            contract_snippets=[
                *_super_dan_stage_snippets(
                    "validator",
                    tool_ids=website_read_only_tool_ids,
                ),
            ],
            sampling_policy={
                "profile": "deterministic",
                "temperature": 0.0,
                "max_tokens": _SUPER_DAN_VALIDATOR_MAX_TOKENS,
            },
            output_contract=OutputContract(
                definition_of_done="Return the validation report only.",
                expected_return_shape=_live_validation_return_shape(),
            ),
            input_payload={
                "objective": objective_context.get("effective_objective") or report.target,
                "original_objective": report.target,
                "objective_normalization": dict(objective_context),
                "workspace_root": str(workspace_root),
                "artifact_root": str(artifact_root),
                "artifact_files": list(relative_files),
                "required_files": [str(path) for path in required_paths],
                "changed_required_files": list(current_changed_required_paths),
            },
            metadata={
                "surface": "super_organism",
                "mode": "live",
                "tool_budget_profile": "super_dan_live",
                "trace_id": run_trace_id,
                "root_task_id": run_task_id,
                "organism_id": report.organism_id,
                "organ_id": "super-dan.live.website",
                "worker_id": validator_worker_id,
                "organism_stage": "validation",
            },
        )
        validator_worker = _live_cell_from_brief(
            model=model,
            brief=validator_brief,
            worker_id=validator_worker_id,
            organism_stage="validation",
        )
        validator_request = _request_from_live_brief(validator_brief, args=args)
        return await _run_live_validation(
            worker=validator_worker,
            request=validator_request,
            tool_ids=website_read_only_tool_ids,
            workspace_root=workspace_root,
            args=args,
            model=model,
            provider=provider,
            event_logger=event_logger,
        )

    if not missing_paths and changed_required_paths:
        validation = await run_website_validator(changed_required_paths)
    static_validation_failures = _website_static_validation_failures(
        report,
        required_paths=required_paths,
        changed_required_paths=changed_required_paths,
        template_phrases=template_phrases,
    )
    validation = _merge_validation_failures(
        validation,
        static_validation_failures,
    )
    validation["deterministic_failures"] = list(
        dict.fromkeys(
            [
                *[
                    str(item).strip()
                    for item in (validation.get("deterministic_failures") or [])
                    if str(item).strip()
                ],
                *list(static_validation_failures),
            ]
        )
    )
    validation_tool_calls_total = int(validation.get("tool_calls") or 0)
    validation_event_count_total = int(validation.get("event_count") or 0)
    validation_token_usage = _merge_token_usage(validation.get("token_usage"))
    builder_retry_attempts = 0
    builder_retry_limit = _live_max_builder_retry_attempts(args)
    while (
        result.status == "completed"
        and not changed_required_paths
        and builder_retry_attempts < builder_retry_limit
    ):
        builder_retry_attempts += 1
        recovery_reason = _validation_repair_brief(validation, static_validation_failures)
        _log_live_event(
            event_logger,
            "live.builder_retry.started",
            attempt=builder_retry_attempts,
            model=model,
            reason=recovery_reason or "no required website files changed",
            changed_required_files=list(changed_required_paths),
            artifact_kind="website",
        )
        recovery_worker_id = "super-dan.live.builder-retry"
        recovery_brief = coding_brief(
            role=RoleSpec(
                role_label="coding_worker",
                responsibility="Run a focused Super DAN builder retry after a no-mutation attempt.",
                success_criteria=[
                    "At least one required artifact file is concretely edited.",
                    "The retry directly advances the operator objective.",
                    "The result remains static and dependency-free.",
                ],
                artifact_targets=list(relative_files),
                trace_role="super-dan.live.builder-retry",
            ),
            task=_live_artifact_builder_retry_task(
                report,
                artifact_root=artifact_root,
                relative_files=relative_files,
                validation=validation,
                deterministic_failures=static_validation_failures,
                objective_context=objective_context,
            ),
            scope=f"workspace={workspace_root}; artifact_root={artifact_root}; Super DAN artifact builder retry",
            hard_constraints=[
                "Actually edit at least one required artifact file; do not return a summary-only response.",
                "Keep writes inside the requested workspace/artifact paths.",
                "Do not install dependencies or require a build step.",
                "Do not create scratch or throwaway files outside the required artifact set.",
            ],
            soft_constraints=[
                "Prefer a small, visible edit over another broad analysis pass.",
                "Use file_edit for existing files when exact grounding is available; use bounded file_write when creating a missing required file.",
                "Avoid rereading unchanged context unless exact edit grounding is needed.",
            ],
            pacing_policy=pacing_policy,
            tool_policy={
                "allowed_tool_ids": list(website_tool_ids),
                "preferred_tool_ids": list(website_preferred_tool_ids),
                "max_tool_calls": int(args.max_tool_calls),
            },
            contract_snippets=[
                *_super_dan_stage_snippets(
                    "builder_retry",
                    tool_ids=website_tool_ids,
                ),
            ],
            output_contract=OutputContract(
                definition_of_done=(
                    "A concrete required-file edit has been made and the final response names changed files, "
                    "validation plan, and remaining risks."
                ),
                expected_return_shape=_live_expected_return_shape(),
            ),
            sampling_policy={
                "profile": choice.sampling_policy,
                "temperature": 0.2,
                "max_tokens": _SUPER_DAN_REPAIR_MAX_TOKENS,
            },
            evidence=_super_report_evidence_blocks(report),
            input_payload={
                "objective": objective_context.get("effective_objective") or report.target,
                "original_objective": report.target,
                "objective_normalization": dict(objective_context),
                "workspace_root": str(workspace_root),
                "artifact_root": str(artifact_root),
                "artifact_files": list(relative_files),
                "required_files": [str(path) for path in required_paths],
                "changed_required_files": list(changed_required_paths),
                "validation": dict(validation),
                "deterministic_failures": list(static_validation_failures),
                "write_pacing": dict(pacing_policy),
                "existing_required_files": _existing_required_files_from_snapshot(
                    file_snapshot,
                    required_paths,
                ),
                "existing_website_change_policy": {
                    "minimum_required_changed_files": 1,
                    "preferred_coordinated_required_files": existing_preferred_coordinated_files,
                    "prefer_coordinated_when_objective_spans_files": True,
                },
            },
            metadata={
                "surface": "super_organism",
                "mode": "live",
                "tool_budget_profile": "super_dan_live",
                "trace_id": run_trace_id,
                "root_task_id": run_task_id,
                "organism_id": report.organism_id,
                "organ_id": "super-dan.live.website",
                "organism_stage": "execution",
                "worker_id": recovery_worker_id,
                "builder_retry": True,
                "exclusive_write_owner_path": relative_files[0] if relative_files else "",
                "recommended_write_paths": list(relative_files),
            },
        )
        recovery_worker = _live_cell_from_brief(
            model=model,
            brief=recovery_brief,
            worker_id=recovery_worker_id,
            organism_stage="execution",
        )
        recovery_result, recovery_tools, recovery_events = await _execute_live_request(
            worker=recovery_worker,
            request=_request_from_live_brief(recovery_brief, args=args),
            tool_ids=website_tool_ids,
            workspace_root=workspace_root,
            args=args,
            model=model,
            provider=provider,
            event_logger=event_logger,
        )
        result = recovery_result
        executed_tools.extend(recovery_tools)
        events.extend(recovery_events)
        build_token_usage = _merge_token_usage(
            build_token_usage,
            _extract_execution_usage(recovery_result),
        )
        error = recovery_result.error
        existing_paths = [path for path in required_paths if path.exists()]
        missing_paths = [path for path in required_paths if not path.exists()]
        changed_required_paths = _changed_paths_from_snapshot(file_snapshot, required_paths)
        mutated_paths = _mutation_paths_from_tools(executed_tools, workspace_root=workspace_root)
        _log_live_event(
            event_logger,
            "live.builder_retry.completed",
            attempt=builder_retry_attempts,
            model=model,
            status=recovery_result.status,
            tool_calls=len(recovery_tools),
            event_count=len(recovery_events),
            changed_required_files=list(changed_required_paths),
            artifact_kind="website",
        )
        validation = _failed_validation_payload(
            reason=error or "live builder retry did not meet the exit contract",
            missing_requirements=[str(path) for path in missing_paths],
        )
        if not missing_paths and changed_required_paths:
            validation = await run_website_validator(changed_required_paths)
        static_validation_failures = _website_static_validation_failures(
            report,
            required_paths=required_paths,
            changed_required_paths=changed_required_paths,
            template_phrases=template_phrases,
        )
        validation = _merge_validation_failures(
            validation,
            static_validation_failures,
        )
        validation["deterministic_failures"] = list(
            dict.fromkeys(
                [
                    *[
                        str(item).strip()
                        for item in (validation.get("deterministic_failures") or [])
                        if str(item).strip()
                    ],
                    *list(static_validation_failures),
                ]
            )
        )
        validation_tool_calls_total += int(validation.get("tool_calls") or 0)
        validation_event_count_total += int(validation.get("event_count") or 0)
        validation_token_usage = _merge_token_usage(
            validation_token_usage,
            validation.get("token_usage"),
        )
    repair_attempts = 0
    repair_limit = _live_repair_loop_limit(args, default=1)
    while (
        result.status == "completed"
        and not missing_paths
        and bool(changed_required_paths)
        and not bool(validation.get("passed"))
        and repair_attempts < repair_limit
    ):
        repair_attempts += 1
        repair_reason = _validation_repair_brief(validation, static_validation_failures)
        _log_live_event(
            event_logger,
            "live.website_repair.started",
            attempt=repair_attempts,
            model=model,
            reason=repair_reason or "validation failed",
            changed_required_files=list(changed_required_paths),
        )
        repair_worker_id = "super-dan.live.website-repair"
        repair_brief = coding_brief(
            role=RoleSpec(
                role_label="coding_worker",
                responsibility="Repair the Super DAN website artifact after validation failure.",
                success_criteria=[
                    "Validation feedback is addressed with concrete file edits.",
                    "The repair changes required website files that directly address the failed validation.",
                    "The result remains static and dependency-free.",
                ],
                artifact_targets=list(relative_files),
                trace_role="super-dan.live.website-repair",
            ),
            task=_live_website_repair_task(
                report,
                artifact_root=artifact_root,
                relative_files=relative_files,
                validation=validation,
                deterministic_failures=static_validation_failures,
                changed_required_paths=changed_required_paths,
                objective_context=objective_context,
            ),
            scope=f"workspace={workspace_root}; artifact_root={artifact_root}; Super DAN website validation repair",
            hard_constraints=[
                "Actually edit required website files; do not return a summary-only response.",
                "Keep writes inside the requested workspace/artifact paths.",
                "Do not install dependencies or require a build step.",
                "Do not create scratch or throwaway files outside the required website artifact set.",
                "Change additional required files only when the validation feedback or objective calls for broader coverage.",
            ],
            soft_constraints=[
                "Prefer small targeted edits over rewriting the whole site.",
                "Prioritize required files that have not changed yet.",
                "Address validator feedback directly before polishing unrelated details.",
                "Avoid rereading unchanged context unless exact edit grounding is needed.",
            ],
            pacing_policy=pacing_policy,
            tool_policy={
                "allowed_tool_ids": list(website_tool_ids),
                "preferred_tool_ids": list(website_preferred_tool_ids),
                "max_tool_calls": int(args.max_tool_calls),
            },
            contract_snippets=[
                *_super_dan_stage_snippets("repair", tool_ids=website_tool_ids),
            ],
            output_contract=OutputContract(
                definition_of_done=(
                    "Validation feedback is addressed with concrete required-file edits and the final response names "
                    "changed files, validation plan, and remaining risks."
                ),
                expected_return_shape=_live_expected_return_shape(),
            ),
            sampling_policy={
                "profile": choice.sampling_policy,
                "temperature": 0.25,
                "max_tokens": _SUPER_DAN_REPAIR_MAX_TOKENS,
            },
            evidence=_super_report_evidence_blocks(report),
            input_payload={
                "objective": objective_context.get("effective_objective") or report.target,
                "original_objective": report.target,
                "objective_normalization": dict(objective_context),
                "workspace_root": str(workspace_root),
                "artifact_root": str(artifact_root),
                "artifact_files": list(relative_files),
                "required_files": [str(path) for path in required_paths],
                "changed_required_files": list(changed_required_paths),
                "validation": dict(validation),
                "deterministic_failures": list(static_validation_failures),
                "write_pacing": dict(pacing_policy),
                "existing_required_files": _existing_required_files_from_snapshot(
                    file_snapshot,
                    required_paths,
                ),
                "existing_website_change_policy": {
                    "minimum_required_changed_files": 1,
                    "preferred_coordinated_required_files": existing_preferred_coordinated_files,
                    "prefer_coordinated_when_objective_spans_files": True,
                },
            },
            metadata={
                "surface": "super_organism",
                "mode": "live",
                "tool_budget_profile": "super_dan_live",
                "trace_id": run_trace_id,
                "root_task_id": run_task_id,
                "organism_id": report.organism_id,
                "organ_id": "super-dan.live.website",
                "organism_stage": "execution",
                "worker_id": repair_worker_id,
            },
        )
        repair_worker = _live_cell_from_brief(
            model=model,
            brief=repair_brief,
            worker_id=repair_worker_id,
            organism_stage="execution",
        )
        repair_result, repair_tools, repair_events = await _execute_live_request(
            worker=repair_worker,
            request=_request_from_live_brief(repair_brief, args=args),
            tool_ids=website_tool_ids,
            workspace_root=workspace_root,
            args=args,
            model=model,
            provider=provider,
            event_logger=event_logger,
        )
        result = repair_result
        executed_tools.extend(repair_tools)
        events.extend(repair_events)
        build_token_usage = _merge_token_usage(
            build_token_usage,
            _extract_execution_usage(repair_result),
        )
        if repair_result.error:
            error = repair_result.error
        existing_paths = [path for path in required_paths if path.exists()]
        missing_paths = [path for path in required_paths if not path.exists()]
        changed_required_paths = _changed_paths_from_snapshot(file_snapshot, required_paths)
        mutated_paths = _mutation_paths_from_tools(executed_tools, workspace_root=workspace_root)
        _log_live_event(
            event_logger,
            "live.website_repair.completed",
            attempt=repair_attempts,
            model=model,
            status=repair_result.status,
            tool_calls=len(repair_tools),
            event_count=len(repair_events),
            changed_required_files=list(changed_required_paths),
        )
        validation = _failed_validation_payload(
            reason=error or "live website repair did not meet the exit contract",
            missing_requirements=[str(path) for path in missing_paths],
        )
        if not missing_paths and changed_required_paths:
            validation = await run_website_validator(changed_required_paths)
        static_validation_failures = _website_static_validation_failures(
            report,
            required_paths=required_paths,
            changed_required_paths=changed_required_paths,
            template_phrases=template_phrases,
        )
        validation = _merge_validation_failures(
            validation,
            static_validation_failures,
        )
        validation["deterministic_failures"] = list(
            dict.fromkeys(
                [
                    *[
                        str(item).strip()
                        for item in (validation.get("deterministic_failures") or [])
                        if str(item).strip()
                    ],
                    *list(static_validation_failures),
                ]
            )
        )
        validation_tool_calls_total += int(validation.get("tool_calls") or 0)
        validation_event_count_total += int(validation.get("event_count") or 0)
        validation_token_usage = _merge_token_usage(
            validation_token_usage,
            validation.get("token_usage"),
        )
    _log_final_validation_event(
        event_logger,
        worker_id="super-dan.live.website.validator",
        model=model,
        validation=validation,
        deterministic_failures=list(validation.get("deterministic_failures") or []),
        changed_required_files=changed_required_paths,
        builder_retry_attempted=bool(builder_retry_attempts),
        repair_attempted=bool(repair_attempts),
        repair_exhausted=bool(repair_attempts and not validation.get("passed")),
    )
    if not changed_required_paths and not error:
        error = "live build finished without changing any required website files"
    if not validation.get("passed") and not error:
        error = (
            str(validation.get("repair_brief") or "").strip()
            or "live build failed validation"
        )
    status = (
        "completed"
        if result.status == "completed"
        and not missing_paths
        and bool(changed_required_paths)
        and bool(validation.get("passed"))
        else "failed"
    )
    token_usage = _merge_token_usage(
        build_token_usage,
        validation_token_usage,
    )
    return {
        "status": status,
        "mode": "live",
        "model": model,
        "workspace_root": str(workspace_root),
        "artifact_root": str(artifact_root),
        "website": str(required_paths[0]),
        "files": [str(path) for path in existing_paths],
        "required_files": [str(path) for path in required_paths],
        "missing_files": [str(path) for path in missing_paths],
        "changed_required_files": list(changed_required_paths),
        "tool_calls": len(executed_tools) + validation_tool_calls_total,
        "mutated_paths": mutated_paths,
        "event_count": len(events) + validation_event_count_total,
        "summary": result.outputs.get("result") or result.outputs.get("text") or "",
        "error": error,
        "summary_label": "Live Build",
        "objective_kind": "website",
        "repair_attempts": repair_attempts,
        "builder_retry_attempts": builder_retry_attempts,
        "failed_step": "validation" if not bool(validation.get("passed")) else "",
        "token_usage": token_usage,
        "validation": validation,
        "plan_context": _super_plan_context_payload(
            plan_context,
            plan_root=plan_root,
            workspace_root=workspace_root,
            include_task_state_key="task_state_final",
        ),
    }


async def _run_live_generic_execution(
    report: SuperOrganismReport,
    args: argparse.Namespace,
    *,
    model: str,
    provider: LLMProvider,
    run_trace_id: str,
    run_task_id: str,
    event_logger: SuperRunEventLogger | None = None,
) -> dict[str, Any]:
    workspace_root = normalize_workspace_root(str(args.workspace))
    operator_intent_policy = _operator_intent_policy_from_objective(
        str(report.target or ""),
        workspace_root=workspace_root,
    )
    operator_intent_payload = operator_intent_policy.to_payload()
    choice = _super_live_choice(report, args)
    generic_tool_ids = _filter_tool_ids_for_operator_intent(
        _live_choice_tool_ids(choice),
        operator_intent_policy,
    )
    generic_preferred_tool_ids = _filter_tool_ids_for_operator_intent(
        _live_choice_preferred_tool_ids(
            choice,
            [
                "list_directory",
                "web_search",
                "file_read",
                "file_edit",
                "file_write",
                "git_diff",
                "shell_command",
            ],
        ),
        operator_intent_policy,
    )
    generic_read_only_tool_ids = _filter_tool_ids_for_operator_intent(
        _live_choice_read_only_tool_ids(choice),
        operator_intent_policy,
    )
    pacing_policy = _live_pacing_policy()
    worker_id = "super-dan.live.general-builder"
    workspace_root.mkdir(parents=True, exist_ok=True)
    pre_run_workspace_state = _snapshot_workspace_file_metadata_for_intent(
        workspace_root,
        operator_intent_policy,
    )
    prompt_only_creation_target = _operator_prompt_only_creation_target(
        operator_intent_policy,
        workspace_root=workspace_root,
        pre_run_workspace_state=pre_run_workspace_state,
    )
    if prompt_only_creation_target:
        generic_tool_ids = _prompt_only_creation_tool_ids(generic_tool_ids)
        generic_preferred_tool_ids = _prompt_only_creation_tool_ids(generic_preferred_tool_ids)
    generic_evidence = [] if prompt_only_creation_target else _super_report_evidence_blocks(report)
    request_understanding = _request_understanding_payload(
        str(report.target or ""),
        workspace_root=workspace_root,
        operator_intent_policy=operator_intent_policy,
    )
    _log_live_event(
        event_logger,
        "live.request_understanding.briefed",
        request_understanding_schema=request_understanding.get("schema"),
        request_kind=request_understanding.get("request_kind"),
        original_request=request_understanding.get("original_request"),
        workspace_root=request_understanding.get("workspace_root"),
        target_paths=list(request_understanding.get("target_paths") or []),
        source=request_understanding.get("source"),
        rule_generation_brief=list(request_understanding.get("rule_generation_brief") or []),
        aspect_reviews=list(request_understanding.get("aspect_reviews") or []),
        confidence_scoped_acceptance=list(request_understanding.get("confidence_scoped_acceptance") or []),
        stop_rule=request_understanding.get("stop_rule"),
    )
    generic_input_payload: dict[str, Any] = {
        "objective": report.target,
        "workspace_root": str(workspace_root),
        "write_pacing": dict(pacing_policy),
        "operator_intent_policy": operator_intent_payload,
        "request_understanding": dict(request_understanding),
    }
    if prompt_only_creation_target:
        generic_input_payload.update(
            {
                "execution_condition": "operator_prompt_only_creation",
                "recommended_write_paths": [prompt_only_creation_target],
                "omitted_context_reason": (
                    "Operator policy forbids other workspace inputs; prompt replay omits organism board/evidence context "
                    "that cannot be used before the first write."
                ),
            }
        )
    else:
        generic_input_payload.update(
            {
                "organism_id": report.organism_id,
                "cell_count": report.cell_count,
                "active_cell_cap": report.active_cell_cap,
                "organ_counts": dict(report.organ_counts),
                "delivery_plan": [node.model_dump(mode="json") for node in report.delivery_plan],
                "shared_board": (
                    report.shared_board.model_dump(mode="json") if report.shared_board is not None else None
                ),
                "coordination_tickets": [
                    ticket.model_dump(mode="json") for ticket in report.coordination_tickets
                ],
                "handoff_packets": [packet.model_dump(mode="json") for packet in report.handoff_packets],
                "final_audit": (
                    report.final_audit.model_dump(mode="json") if report.final_audit is not None else None
                ),
            }
        )
    plan_context: dict[str, Any] | None = None
    planning_token_usage: dict[str, int] | None = None
    planning_tool_calls_total = 0
    planning_event_count_total = 0
    task_graph_revision = 0
    plan_root = _super_plan_root(event_logger=event_logger, workspace_root=workspace_root)
    plan_root_relative = _super_plan_root_relative(plan_root, workspace_root)

    async def run_optional_planner() -> tuple[dict[str, Any] | None, dict[str, int] | None]:
        nonlocal planning_tool_calls_total, planning_event_count_total, request_understanding, task_graph_revision
        planner_tool_ids = [
            tool_id
            for tool_id in ("list_directory", "file_read", "file_write", "file_edit")
            if tool_id in set(generic_tool_ids)
        ]
        plan_validator_tool_ids = [
            tool_id
            for tool_id in ("list_directory", "file_read")
            if tool_id in set(generic_read_only_tool_ids)
        ]
        if not planner_tool_ids or not plan_validator_tool_ids:
            return None, None
        if not _super_should_run_planner(
            str(report.target or ""),
            operator_intent_policy=operator_intent_policy,
            prompt_only_creation_target=prompt_only_creation_target,
            tool_ids=planner_tool_ids,
        ):
            return None, None
        plan_root.mkdir(parents=True, exist_ok=True)
        _log_live_event(
            event_logger,
            "live.planning.started",
            model=model,
            plan_root=str(plan_root),
            plan_root_relative=plan_root_relative,
            tool_ids=list(planner_tool_ids),
        )
        planner_worker_id = "super-dan.live.general.planner"
        planner_brief = role_brief(
            role=RoleSpec(
                role_label="workspace_planner",
                responsibility="Create a coherent run-local execution plan for broad Super DAN workspace work.",
                success_criteria=[
                    "Temporary numeric plan files are written under the run-local plan root.",
                    "Top-level phases are coherent large feature chunks, not unrelated buckets.",
                    "The output predicts a dependency task graph and identifies the current ready frontier.",
                ],
                artifact_targets=[plan_root_relative],
                trace_role="super-dan.live.general.planner",
            ),
            task=_live_generic_planner_task(
                report,
                workspace_root=workspace_root,
                plan_root=plan_root,
                plan_root_relative=plan_root_relative,
                operator_intent_policy=operator_intent_policy,
                request_understanding=request_understanding,
            ),
            scope=f"workspace={workspace_root}; Super DAN run-local temporary planning",
            hard_constraints=[
                "Do not implement the final deliverable during the planner stage.",
                "Write plan markdown only under the supplied run-local plan root.",
                "Use all-digit numeric plan identifiers only; do not use alphabetic placeholders or `N-M` notation.",
                "One top-level phase must be one internally coherent large feature chunk.",
                "Do not update permanent project tracking docs from this stage.",
                *list(operator_intent_policy.constraints),
            ],
            soft_constraints=[
                "Default to a small plan; a simple broad task may need one top-level phase and no sub-plan files.",
                "Create sub-plans only when they are coherent slices of the same parent phase.",
                "Make each checkbox specific enough that a later validator can audit it against changed files.",
                "Prefer several disjoint ready tasks over one giant task when their owned paths can be executed independently.",
            ],
            tool_policy={
                "allowed_tool_ids": list(planner_tool_ids),
                "preferred_tool_ids": [
                    tool_id
                    for tool_id in ("list_directory", "file_read", "file_write", "file_edit")
                    if tool_id in set(planner_tool_ids)
                ],
                "max_tool_calls": max(4, min(int(args.max_tool_calls), 24)),
            },
            contract_snippets=[
                *_super_dan_stage_snippets("planner", tool_ids=planner_tool_ids),
                _super_plan_file_contract(plan_root_relative),
                snippets.no_scratch_files_contract(),
            ],
            output_contract=OutputContract(
                definition_of_done=(
                    "Run-local numeric plan files have been written and the first executable build slice is identified."
                ),
                expected_return_shape=_live_plan_return_shape(),
            ),
            sampling_policy={
                "profile": choice.sampling_policy,
                "temperature": 0.20,
                "max_tokens": _SUPER_DAN_PLANNER_MAX_TOKENS,
            },
            evidence=[] if prompt_only_creation_target else _super_report_evidence_blocks(report),
            input_payload={
                "objective": report.target,
                "workspace_root": str(workspace_root),
                "plan_root": str(plan_root),
                "plan_root_relative": plan_root_relative,
                "operator_intent_policy": operator_intent_payload,
                "request_understanding": dict(request_understanding),
            },
            metadata={
                "surface": "super_organism",
                "mode": "live",
                "tool_budget_profile": "super_dan_live",
                "trace_id": run_trace_id,
                "root_task_id": run_task_id,
                "organism_id": report.organism_id,
                "organ_id": "super-dan.live.general",
                "organism_stage": "planning",
                "worker_id": planner_worker_id,
                "operator_intent_policy": operator_intent_payload,
            },
        )
        planner_worker = _live_cell_from_brief(
            model=model,
            brief=planner_brief,
            worker_id=planner_worker_id,
            organism_stage="planning",
        )
        planner_result, planner_tools, planner_events = await _execute_live_request(
            worker=planner_worker,
            request=_request_from_live_brief(planner_brief, args=args),
            tool_ids=planner_tool_ids,
            workspace_root=workspace_root,
            args=args,
            model=model,
            provider=provider,
            event_logger=event_logger,
        )
        usage = _extract_execution_usage(planner_result)
        planning_tool_calls_total += len(planner_tools)
        planning_event_count_total += len(planner_events)
        plan_files = _super_plan_files(plan_root, workspace_root)
        planner_payload = _extract_validation_payload(dict(planner_result.outputs))
        updated_understanding = _extract_request_understanding_from_outputs(
            planner_payload,
            fallback=request_understanding,
        )
        if updated_understanding is not None:
            request_understanding = updated_understanding
            _log_live_event(
                event_logger,
                "live.request_understanding.updated",
                source="planner",
                request_understanding=dict(request_understanding),
                request_understanding_schema=request_understanding.get("schema"),
                request_kind=request_understanding.get("request_kind"),
                original_request=request_understanding.get("original_request"),
                workspace_root=request_understanding.get("workspace_root"),
                target_paths=list(request_understanding.get("target_paths") or []),
                aspect_reviews=list(request_understanding.get("aspect_reviews") or []),
                confidence_scoped_acceptance=list(request_understanding.get("confidence_scoped_acceptance") or []),
                stop_rule=request_understanding.get("stop_rule"),
            )
        initial_slice: list[str] = []
        initial_task_graph: list[dict[str, Any]] = []
        initial_ready_task_ids: list[str] = []
        initial_deferred_task_ids: list[str] = []
        if isinstance(planner_payload, Mapping):
            raw_slice = planner_payload.get("first_build_slice")
            if isinstance(raw_slice, list):
                initial_slice = [str(item).strip() for item in raw_slice if str(item).strip()]
            initial_task_graph = _super_plan_task_graph(planner_payload.get("task_graph") or [])
            initial_ready_task_ids = _super_plan_string_list(planner_payload.get("ready_task_ids") or [])
            if not initial_ready_task_ids:
                initial_ready_task_ids = list(initial_slice)
            if not initial_ready_task_ids and initial_task_graph:
                initial_ready_task_ids = _super_plan_ready_task_ids(initial_task_graph)
            initial_deferred_task_ids = _super_plan_string_list(planner_payload.get("deferred_task_ids") or [])
            if not initial_deferred_task_ids and initial_task_graph:
                initial_deferred_task_ids = _super_plan_deferred_task_ids(
                    initial_task_graph,
                    initial_ready_task_ids,
                )
        initial_plan_context = {
            "enabled": True,
            "usable": False,
            "persistence": "run_temp",
            "plan_root": str(plan_root),
            "plan_root_relative": plan_root_relative,
            "plan_files": list(plan_files),
            "assigned_task_ids": list(initial_ready_task_ids),
            "ready_task_ids": list(initial_ready_task_ids),
            "deferred_task_ids": list(initial_deferred_task_ids),
            "task_graph": list(initial_task_graph),
            "dependency_revisions": [],
            "execution_mode": "dependency_frontier",
            "request_understanding": dict(request_understanding),
        }
        if initial_task_graph:
            initial_graph_update = _super_plan_graph_update_payload(planner_payload)
            task_graph_revision += 1
            initial_plan_context = _super_plan_context_with_graph_state(
                initial_plan_context,
                revision=task_graph_revision,
                source="planner",
                update_reason=initial_graph_update.get("reason") or "Initial model-authored task graph.",
                update_scope="whole_graph",
                changed_task_ids=list(initial_graph_update.get("changed_task_ids") or []),
                ready_task_ids=initial_ready_task_ids,
                deferred_task_ids=initial_deferred_task_ids,
            ) or initial_plan_context
            _log_live_event(
                event_logger,
                "live.task_graph.updated",
                source="planner",
                plan_context=_super_plan_context_payload(
                    initial_plan_context,
                    plan_root=plan_root,
                    workspace_root=workspace_root,
                ),
                task_graph_state=dict(initial_plan_context.get("task_graph_state") or {}),
            )
        _log_live_event(
            event_logger,
            "live.planning.completed",
            model=model,
            status=planner_result.status,
            tool_calls=len(planner_tools),
            event_count=len(planner_events),
            plan_file_count=len(plan_files),
            plan_files=list(plan_files),
            task_graph=list(initial_task_graph),
            ready_task_ids=list(initial_ready_task_ids),
            deferred_task_ids=list(initial_deferred_task_ids),
            first_build_slice=list(initial_slice),
            task_graph_state=dict(initial_plan_context.get("task_graph_state") or {}),
            request_understanding=dict(request_understanding),
            error=planner_result.error,
        )
        if planner_result.status != "completed" or planner_result.error or not plan_files:
            return None, usage

        _log_live_event(
            event_logger,
            "live.plan_validation.started",
            model=model,
            plan_root=str(plan_root),
            plan_files=list(plan_files),
            tool_ids=list(plan_validator_tool_ids),
        )
        plan_validator_worker_id = "super-dan.live.general.plan-validator"
        plan_validator_brief = review_brief(
            role=RoleSpec(
                role_label="plan_validator",
                responsibility="Audit the run-local execution plan before a builder follows it.",
                success_criteria=[
                    "Plan files are inspected in read-only mode.",
                    "Numeric naming, phase coherence, dependency graph, and ready-frontier clarity are verified.",
                    "Contradictions or incoherent phase grouping are rejected.",
                ],
                artifact_targets=list(plan_files),
                trace_role="super-dan.live.general.plan-validator",
            ),
            task=_live_generic_plan_validation_task(
                report,
                workspace_root=workspace_root,
                plan_root=plan_root,
                plan_files=plan_files,
                operator_intent_policy=operator_intent_policy,
                request_understanding=request_understanding,
            ),
            scope=f"workspace={workspace_root}; Super DAN run-local plan validation",
            hard_constraints=[
                "Read-only validation only; do not write or edit files.",
                "Reject alphabetic plan ids, `N-M` placeholders, and third-level plan file names.",
                "Reject unrelated sub-plans grouped under one parent phase.",
                "Reject missing or incoherent dependency-frontier metadata for broad multi-task objectives.",
                "Return the structured plan validation payload only.",
                *list(operator_intent_policy.constraints),
            ],
            soft_constraints=[
                "Prefer a small actionable ready frontier over over-planning.",
                "Name blocking issues precisely enough for a planner retry or human review.",
            ],
            allowed_tool_ids=plan_validator_tool_ids,
            tool_policy={
                "allowed_tool_ids": list(plan_validator_tool_ids),
                "preferred_tool_ids": [
                    tool_id
                    for tool_id in ("file_read", "list_directory")
                    if tool_id in set(plan_validator_tool_ids)
                ],
                "max_tool_calls": max(4, min(int(args.max_tool_calls), 16)),
            },
            contract_snippets=[
                *_super_dan_stage_snippets("plan_validator", tool_ids=plan_validator_tool_ids),
                _super_plan_file_contract(plan_root_relative),
            ],
            sampling_policy={
                "profile": "deterministic",
                "temperature": 0.0,
                "max_tokens": _SUPER_DAN_PLAN_VALIDATOR_MAX_TOKENS,
            },
            output_contract=OutputContract(
                definition_of_done="Return the plan validation report only.",
                expected_return_shape=_live_plan_validation_return_shape(),
            ),
            input_payload={
                "objective": report.target,
                "workspace_root": str(workspace_root),
                "plan_root": str(plan_root),
                "plan_root_relative": plan_root_relative,
                "plan_files": list(plan_files),
                "plan_task_state": _super_plan_task_state(plan_root, workspace_root=workspace_root),
                "operator_intent_policy": operator_intent_payload,
                "request_understanding": dict(request_understanding),
            },
            metadata={
                "surface": "super_organism",
                "mode": "live",
                "tool_budget_profile": "super_dan_live",
                "trace_id": run_trace_id,
                "root_task_id": run_task_id,
                "organism_id": report.organism_id,
                "organ_id": "super-dan.live.general",
                "worker_id": plan_validator_worker_id,
                "organism_stage": "plan_validation",
                "operator_intent_policy": operator_intent_payload,
            },
        )
        plan_validator_worker = _live_cell_from_brief(
            model=model,
            brief=plan_validator_brief,
            worker_id=plan_validator_worker_id,
            organism_stage="plan_validation",
        )
        plan_validation_result, plan_validation_tools, plan_validation_events = await _execute_live_request(
            worker=plan_validator_worker,
            request=_request_from_live_brief(plan_validator_brief, args=args),
            tool_ids=plan_validator_tool_ids,
            workspace_root=workspace_root,
            args=args,
            model=model,
            provider=provider,
            event_logger=event_logger,
        )
        usage = _merge_token_usage(usage, _extract_execution_usage(plan_validation_result))
        planning_tool_calls_total += len(plan_validation_tools)
        planning_event_count_total += len(plan_validation_events)
        raw_plan_validation_payload = _extract_validation_payload(dict(plan_validation_result.outputs))
        plan_validation = _normalize_plan_validation_payload(
            raw_plan_validation_payload,
            plan_files=plan_files,
        )
        if plan_validation_result.status != "completed":
            plan_validation["passed"] = False
            plan_validation["blocking_issues"] = [
                *list(plan_validation.get("blocking_issues") or []),
                plan_validation_result.error or "plan validator did not complete successfully",
            ]
        if not plan_validation.get("first_build_slice") and initial_slice:
            plan_validation["first_build_slice"] = list(initial_slice)
        if not plan_validation.get("task_graph") and initial_task_graph:
            plan_validation["task_graph"] = list(initial_task_graph)
        if not plan_validation.get("ready_task_ids") and initial_ready_task_ids:
            plan_validation["ready_task_ids"] = list(initial_ready_task_ids)
        if not plan_validation.get("deferred_task_ids") and initial_deferred_task_ids:
            plan_validation["deferred_task_ids"] = list(initial_deferred_task_ids)
        if not plan_validation.get("first_build_slice") and plan_validation.get("ready_task_ids"):
            plan_validation["first_build_slice"] = list(plan_validation.get("ready_task_ids") or [])
        task_graph_update = _super_plan_graph_update_payload(planner_payload)
        validation_graph_update = _super_plan_graph_update_payload(raw_plan_validation_payload)
        update_scope = str(
            validation_graph_update.get("scope")
            or task_graph_update.get("scope")
            or ("branch_local" if plan_validation.get("dependency_revisions") else "whole_graph")
        ).strip()
        update_reason = str(
            validation_graph_update.get("reason")
            or task_graph_update.get("reason")
            or "Plan validation normalized the task graph and ready frontier."
        ).strip()
        validation_plan_context = {
            "enabled": True,
            "usable": True,
            "persistence": "run_temp",
            "plan_root": str(plan_root),
            "plan_root_relative": plan_root_relative,
            "plan_files": list(plan_files),
            "assigned_task_ids": list(
                plan_validation.get("ready_task_ids")
                or plan_validation.get("first_build_slice")
                or []
            ),
            "ready_task_ids": list(plan_validation.get("ready_task_ids") or []),
            "deferred_task_ids": list(plan_validation.get("deferred_task_ids") or []),
            "task_graph": list(plan_validation.get("task_graph") or []),
            "dependency_revisions": list(plan_validation.get("dependency_revisions") or []),
            "execution_mode": "dependency_frontier",
            "validation": plan_validation,
            "request_understanding": dict(request_understanding),
        }
        if plan_validation.get("task_graph"):
            task_graph_revision += 1
            validation_plan_context = _super_plan_context_with_graph_state(
                validation_plan_context,
                revision=task_graph_revision,
                source="plan_validator",
                update_reason=update_reason,
                update_scope=update_scope or "whole_graph",
                changed_task_ids=list(validation_graph_update.get("changed_task_ids") or []),
                ready_task_ids=list(plan_validation.get("ready_task_ids") or []),
                deferred_task_ids=list(plan_validation.get("deferred_task_ids") or []),
                dependency_revisions=list(plan_validation.get("dependency_revisions") or []),
            ) or validation_plan_context
            _log_live_event(
                event_logger,
                "live.task_graph.updated",
                source="plan_validator",
                plan_context=_super_plan_context_payload(
                    validation_plan_context,
                    plan_root=plan_root,
                    workspace_root=workspace_root,
                ),
                task_graph_state=dict(validation_plan_context.get("task_graph_state") or {}),
            )
        _log_live_event(
            event_logger,
            "live.plan_validation.completed",
            model=model,
            status=plan_validation_result.status,
            passed=plan_validation.get("passed"),
            overall_score=plan_validation.get("overall_score"),
            tool_calls=len(plan_validation_tools),
            event_count=len(plan_validation_events),
            plan_files=list(plan_files),
            ready_task_ids=list(plan_validation.get("ready_task_ids") or []),
            deferred_task_ids=list(plan_validation.get("deferred_task_ids") or []),
            first_build_slice=list(plan_validation.get("first_build_slice") or []),
            task_graph=list(plan_validation.get("task_graph") or []),
            dependency_revisions=list(plan_validation.get("dependency_revisions") or []),
            task_graph_state=dict(validation_plan_context.get("task_graph_state") or {}),
            blocking_issues=list(plan_validation.get("blocking_issues") or []),
        )
        if not plan_validation.get("passed"):
            return None, usage
        return (validation_plan_context, usage)

    plan_context, planning_token_usage = await run_optional_planner()
    full_plan_context = dict(plan_context) if isinstance(plan_context, Mapping) else None
    worktree_prepared_tasks: list[dict[str, Any]] = []
    main_frontier_task: dict[str, Any] | None = None
    worktree_frontier_tasks: list[dict[str, Any]] = []
    if full_plan_context:
        main_frontier_task, worktree_frontier_tasks = _super_plan_parallel_frontier(
            full_plan_context,
            max_worktree_tasks=max(0, int(getattr(args, "worktree_parallelism", 0) or 0)),
        )
    if worktree_frontier_tasks and event_logger is not None:
        for frontier_task in worktree_frontier_tasks:
            packet_id = _super_worktree_packet_id(frontier_task, run_task_id)
            owner_scope = _super_worktree_owner_scope(frontier_task)
            runtime_task = event_logger.plan_worktree_task(
                packet_id,
                owner_scope=owner_scope,
                reason="ready_frontier_parallel",
            )
            plan_task_id = str(frontier_task.get("task_id") or "").strip()
            if runtime_task is None:
                _log_live_event(
                    event_logger,
                    "live.worktree_task.skipped",
                    plan_task_id=plan_task_id,
                    packet_id=packet_id,
                    owner_scope=owner_scope,
                    reason="worktree task was not admitted by hook runtime",
                )
                continue
            worktree_root = Path(str(runtime_task.worktree_path)).expanduser()
            try:
                _super_copy_workspace_to_worktree(workspace_root, worktree_root)
            except Exception as exc:
                _log_live_event(
                    event_logger,
                    "live.worktree_task.skipped",
                    plan_task_id=plan_task_id,
                    task_id=runtime_task.task_id,
                    packet_id=runtime_task.packet_id,
                    owner_scope=runtime_task.owner_scope,
                    worktree_root=str(worktree_root),
                    reason=f"worktree preparation failed: {type(exc).__name__}: {exc}",
                )
                continue
            worktree_prepared_tasks.append(
                {
                    "plan_task": dict(frontier_task),
                    "runtime_task": runtime_task,
                    "worktree_root": worktree_root,
                }
            )
        if worktree_prepared_tasks:
            plan_context = _super_plan_context_for_frontier_main(
                full_plan_context,
                main_frontier_task,
                [dict(item["plan_task"]) for item in worktree_prepared_tasks],
            )
            _log_live_event(
                event_logger,
                "live.worktree_frontier.started",
                model=model,
                task_ids=[
                    str(item["plan_task"].get("task_id") or "").strip()
                    for item in worktree_prepared_tasks
                ],
                worktree_roots=[str(item["worktree_root"]) for item in worktree_prepared_tasks],
                main_task_id=str((main_frontier_task or {}).get("task_id") or ""),
            )

    async def run_prepared_worktree_frontier() -> dict[str, Any]:
        if not worktree_prepared_tasks:
            return {
                "synthetic_tools": [],
                "tool_calls": 0,
                "event_count": 0,
                "token_usage": None,
                "applied_task_ids": [],
                "events": [],
            }
        total_tool_calls = 0
        total_event_count = 0
        token_usage: dict[str, int] | None = None
        synthetic_tools: list[dict[str, Any]] = []
        collected_events: list[dict[str, Any]] = []
        applied_task_ids: list[str] = []

        async def run_one(prepared: Mapping[str, Any]) -> dict[str, Any]:
            frontier_task = dict(prepared.get("plan_task") or {})
            runtime_task = prepared.get("runtime_task")
            worktree_root = Path(str(prepared.get("worktree_root") or "")).expanduser()
            plan_task_id = str(frontier_task.get("task_id") or "").strip()
            task_plan_context = dict(full_plan_context or {})
            task_plan_context["assigned_task_ids"] = [plan_task_id] if plan_task_id else []
            task_plan_context["ready_task_ids"] = [plan_task_id] if plan_task_id else []
            task_plan_context["parallel_worktree_task_ids"] = []
            worker_id_for_task = (
                "super-dan.live.worktree."
                + re.sub(r"[^A-Za-z0-9_.-]+", "-", plan_task_id or "ready-task").strip("-")
            )
            owned_paths = _super_plan_task_owned_paths(frontier_task)
            worktree_brief = role_brief(
                role=RoleSpec(
                    role_label="workspace_worker",
                    responsibility="Execute one dependency-ready Super DAN task in an isolated worktree.",
                    success_criteria=[
                        "Only task-owned files are created or edited.",
                        "The isolated patch materially completes the assigned ready task.",
                        "The final response reports changed files and validation evidence.",
                    ],
                    artifact_targets=list(owned_paths),
                    trace_role=worker_id_for_task,
                ),
                task=_live_generic_worktree_task(
                    report,
                    main_workspace_root=workspace_root,
                    worktree_root=worktree_root,
                    task=frontier_task,
                    plan_context=task_plan_context,
                    operator_intent_policy=operator_intent_policy,
                    request_understanding=request_understanding,
                ),
                scope=f"workspace={worktree_root}; isolated Super DAN ready-frontier task {plan_task_id}",
                hard_constraints=[
                    "Only create or edit files under the task-owned paths.",
                    "Do not edit the authoritative main workspace directly from this worker.",
                    "Do not update `.dan-super` state, run logs, or plan files from this worktree worker.",
                    "Do not implement sibling ready tasks or deferred downstream tasks.",
                    *list(operator_intent_policy.constraints),
                ],
                soft_constraints=[
                    "Keep the patch small enough that admission can copy it back cleanly.",
                    "Prefer targeted file_edit when a task-owned file already exists.",
                    "Run focused verification only when useful for the assigned task.",
                ],
                tool_policy={
                    "allowed_tool_ids": list(generic_tool_ids),
                    "preferred_tool_ids": list(generic_preferred_tool_ids),
                    "max_tool_calls": max(2, min(int(args.max_tool_calls), 32)),
                },
                contract_snippets=[
                    *_super_dan_stage_snippets("builder", tool_ids=generic_tool_ids),
                    _super_plan_executor_contract(task_plan_context),
                    _live_pacing_contract(pacing_policy),
                    snippets.incremental_edit_contract(),
                    snippets.no_scratch_files_contract(),
                ],
                output_contract=OutputContract(
                    definition_of_done=(
                        "The assigned dependency-ready task is implemented in the isolated worktree and changed files are named."
                    ),
                    expected_return_shape=_live_expected_return_shape(),
                ),
                sampling_policy={
                    "profile": choice.sampling_policy,
                    "temperature": 0.25,
                    "max_tokens": _SUPER_DAN_WORKER_MAX_TOKENS,
                },
                evidence=generic_evidence,
                input_payload={
                    "objective": report.target,
                    "main_workspace_root": str(workspace_root),
                    "workspace_root": str(worktree_root),
                    "plan_task": frontier_task,
                    "owned_paths": list(owned_paths),
                    "operator_intent_policy": operator_intent_payload,
                    "request_understanding": dict(request_understanding),
                    "plan_context": _super_plan_context_payload(
                        task_plan_context,
                        plan_root=plan_root,
                        workspace_root=workspace_root,
                    ),
                },
                metadata={
                    "surface": "super_organism",
                    "mode": "live",
                    "tool_budget_profile": "super_dan_live",
                    "trace_id": run_trace_id,
                    "root_task_id": run_task_id,
                    "organism_id": report.organism_id,
                    "organ_id": "super-dan.live.general",
                    "organism_stage": "execution",
                    "worker_id": worker_id_for_task,
                    "operator_intent_policy": operator_intent_payload,
                    "super_dan_worktree": True,
                    "plan_task_id": plan_task_id,
                    "owned_paths": list(owned_paths),
                },
            )
            worker_for_task = _live_cell_from_brief(
                model=model,
                brief=worktree_brief,
                worker_id=worker_id_for_task,
                organism_stage="execution",
            )
            _log_live_event(
                event_logger,
                "live.worktree_task.started",
                model=model,
                plan_task_id=plan_task_id,
                task_id=getattr(runtime_task, "task_id", ""),
                packet_id=getattr(runtime_task, "packet_id", ""),
                owner_scope=getattr(runtime_task, "owner_scope", ""),
                worktree_root=str(worktree_root),
                owned_paths=list(owned_paths),
            )
            worktree_result, worktree_tools, worktree_events = await _execute_live_request(
                worker=worker_for_task,
                request=_request_from_live_brief(worktree_brief, args=args),
                tool_ids=generic_tool_ids,
                workspace_root=worktree_root,
                args=args,
                model=model,
                provider=provider,
                event_logger=event_logger,
            )
            worktree_mutated_paths = _mutation_paths_from_tools(
                worktree_tools,
                workspace_root=worktree_root,
                exclude_roots=(),
            )
            relative_mutations = _super_worktree_relative_mutation_paths(
                worktree_mutated_paths,
                worktree_root=worktree_root,
                task=frontier_task,
            )
            summary_text = str(
                worktree_result.outputs.get("result")
                or worktree_result.outputs.get("text")
                or ""
            ).strip()
            admission_events = []
            if event_logger is not None:
                admission_events = event_logger.admit_worktree_diff(
                    {
                        "task_id": getattr(runtime_task, "task_id", ""),
                        "packet_id": getattr(runtime_task, "packet_id", ""),
                        "owner_scope": getattr(runtime_task, "owner_scope", ""),
                        "changed_files": list(relative_mutations),
                        "summary": summary_text or f"worktree task {plan_task_id} completed",
                        "validation_evidence": _super_plan_string_list(
                            frontier_task.get("validation") or []
                        ),
                        "candidate_score": 0.75 if worktree_result.status == "completed" else 0.25,
                        "merge_risk": "bounded_owned_path_copy",
                        "diff_ref": str(worktree_root),
                    }
                )
            admitted = any(
                row.get("event") == "super.worktree.diff_admitted"
                for row in admission_events
            )
            applied_tools: list[dict[str, Any]] = []
            if admitted:
                applied_tools = _super_apply_worktree_files(
                    worktree_root=worktree_root,
                    workspace_root=workspace_root,
                    relative_paths=relative_mutations,
                )
                _log_live_event(
                    event_logger,
                    "live.worktree.diff_applied",
                    plan_task_id=plan_task_id,
                    task_id=getattr(runtime_task, "task_id", ""),
                    packet_id=getattr(runtime_task, "packet_id", ""),
                    owner_scope=getattr(runtime_task, "owner_scope", ""),
                    changed_files=list(relative_mutations),
                    applied_files=[
                        str(
                            (
                                tool.get("result")
                                if isinstance(tool.get("result"), Mapping)
                                else {}
                            ).get("path")
                            or ""
                        )
                        for tool in applied_tools
                    ],
                )
            else:
                _log_live_event(
                    event_logger,
                    "live.worktree.diff_skipped",
                    plan_task_id=plan_task_id,
                    task_id=getattr(runtime_task, "task_id", ""),
                    packet_id=getattr(runtime_task, "packet_id", ""),
                    owner_scope=getattr(runtime_task, "owner_scope", ""),
                    changed_files=list(relative_mutations),
                    reason="diff was not admitted",
                )
            _log_live_event(
                event_logger,
                "live.worktree_task.completed",
                model=model,
                plan_task_id=plan_task_id,
                task_id=getattr(runtime_task, "task_id", ""),
                packet_id=getattr(runtime_task, "packet_id", ""),
                owner_scope=getattr(runtime_task, "owner_scope", ""),
                status=worktree_result.status,
                tool_calls=len(worktree_tools),
                event_count=len(worktree_events),
                changed_files=list(relative_mutations),
                applied=bool(applied_tools),
                error=worktree_result.error,
            )
            return {
                "plan_task_id": plan_task_id,
                "status": worktree_result.status,
                "tool_calls": len(worktree_tools),
                "event_count": len(worktree_events),
                "token_usage": _extract_execution_usage(worktree_result),
                "synthetic_tools": applied_tools,
                "events": list(worktree_events),
                "applied": bool(applied_tools),
            }

        worktree_results = await asyncio.gather(
            *(run_one(prepared) for prepared in worktree_prepared_tasks),
            return_exceptions=True,
        )
        for item in worktree_results:
            if isinstance(item, Exception):
                _log_live_event(
                    event_logger,
                    "live.worktree_task.failed",
                    error_type=type(item).__name__,
                    error=str(item),
                )
                continue
            total_tool_calls += int(item.get("tool_calls") or 0)
            total_event_count += int(item.get("event_count") or 0)
            token_usage = _merge_token_usage(token_usage, item.get("token_usage"))
            synthetic_tools.extend(list(item.get("synthetic_tools") or []))
            collected_events.extend(list(item.get("events") or []))
            if item.get("applied"):
                applied_task_ids.append(str(item.get("plan_task_id") or ""))
        return {
            "synthetic_tools": synthetic_tools,
            "tool_calls": total_tool_calls,
            "event_count": total_event_count,
            "token_usage": token_usage,
            "applied_task_ids": [task_id for task_id in applied_task_ids if task_id],
            "events": collected_events,
        }

    if isinstance(plan_context, Mapping) and plan_context.get("task_graph"):
        active_frontier_ids = [
            *list(plan_context.get("assigned_task_ids") or plan_context.get("ready_task_ids") or []),
            *list(plan_context.get("parallel_worktree_task_ids") or []),
        ]
        task_graph_revision += 1
        plan_context = _super_plan_context_with_graph_state(
            plan_context,
            revision=task_graph_revision,
            source="execution_frontier",
            update_reason="Execution admitted the current ready frontier; non-overlapping branches may run in parallel.",
            update_scope="branch_local",
            changed_task_ids=active_frontier_ids,
            active_task_ids=active_frontier_ids,
        ) or dict(plan_context)
        _log_live_event(
            event_logger,
            "live.task_graph.updated",
            source="execution_frontier",
            plan_context=_super_plan_context_payload(
                plan_context,
                plan_root=plan_root,
                workspace_root=workspace_root,
            ),
            task_graph_state=dict(plan_context.get("task_graph_state") or {}),
        )

    if plan_context:
        generic_input_payload["plan_context"] = _super_plan_context_payload(
            plan_context,
            plan_root=plan_root,
            workspace_root=workspace_root,
            include_task_state_key="task_state_before_execution",
        )
    interactive_source_implementation = _super_is_interactive_source_implementation_objective(
        str(report.target or "")
    )
    targeted_source_repair = _super_is_targeted_source_repair_objective(str(report.target or ""))
    if interactive_source_implementation:
        generic_input_payload["execution_condition"] = "interactive_source_implementation"
        generic_input_payload["first_write_expectation"] = (
            "After minimal source/scene inspection, the live worker should make product source, scene/state, "
            "UI, asset, or validation/test edits rather than continuing reconnaissance."
        )
    elif targeted_source_repair:
        generic_input_payload["execution_condition"] = "targeted_source_repair"
        generic_input_payload["first_write_expectation"] = (
            "After one focused inspection of the failing source/test/error context, the live worker should make "
            "a material source or test edit, or report the exact blocker that prevents the edit."
        )
    mutation_required = operator_intent_policy.allow_workspace_mutation
    worker_success_criteria = (
        [
            "The final answer is a substantive in-session answer to the requested review, assessment, or project-summary question.",
            "Findings are grounded in available workspace evidence.",
            "Any limitations or follow-up work are explicit.",
        ]
        if not mutation_required
        else [
            "At least one workspace file is created or edited.",
            "The change materially advances the operator objective.",
            "The final answer names changed files, validation plan, and remaining risks.",
        ]
    )
    definition_of_done = (
        "The final response answers the operator in-session with a substantive summary, evidence-backed findings, "
        "limitations, and any follow-up work that would require explicit permission or a separate request; a file-change receipt alone is not done."
        if not mutation_required
        else (
            "At least one workspace file was created or edited and the final response names the changed files, "
            "a concise validation plan, and remaining risks."
        )
    )
    worker_brief = role_brief(
            role=RoleSpec(
                role_label="workspace_worker",
                responsibility="Execute the requested Super DAN deliverable directly in the workspace.",
                success_criteria=worker_success_criteria,
                trace_role="super-dan.live.general-builder",
            ),
            task=_live_generic_task(
                report,
                workspace_root=workspace_root,
                operator_intent_policy=operator_intent_policy,
                prompt_only_creation_target=prompt_only_creation_target,
                plan_context=plan_context,
                request_understanding=request_understanding,
            ),
            scope=f"workspace={workspace_root}; native Super DAN live general workspace execution",
            hard_constraints=[
                *(
                    ["Do not create, edit, delete, or otherwise mutate workspace files; answer from inspection."]
                    if not mutation_required
                    else ["Actually mutate workspace files before finalizing."]
                ),
                *(
                    [
                        "For interactive source implementation objectives, after minimal inspection the first durable output must be product source, scene/state, UI, asset, or validation/test edits, not `.dan-super` plans or analysis notes.",
                    ]
                    if interactive_source_implementation
                    else []
                ),
                *(
                    [
                        "For targeted source repair objectives, after at most one focused inspection of the relevant file, test, or error context, make a concrete file_edit/file_write mutation to the responsible source/test material or report a precise blocker.",
                        "Do not create `.dan-super` plan files or run validation as a substitute for the required repair edit unless the precise blocker is that no responsible workspace file can be identified.",
                    ]
                    if targeted_source_repair
                    else []
                ),
                "Default to the current workspace root; only use explicit external paths when the operator asks and runtime policy allows.",
                *(
                    [
                        "When the requested deliverable is a saved report, markdown file, data note, or other document artifact, write that artifact to the workspace.",
                    ]
                    if mutation_required
                    else []
                ),
                *(
                    [
                        "Follow the supplied run-local plan context, but treat the actual deliverable edit as mandatory; plan-file edits alone do not satisfy the objective.",
                        "Tick plan checkboxes only for work completed with evidence.",
                    ]
                    if plan_context and mutation_required
                    else []
                ),
                "Use web_search for current external facts when the enabled tool is available.",
                *list(operator_intent_policy.constraints),
            ],
            soft_constraints=[
                *(
                    ["Prefer a bounded concrete deliverable over broad speculative analysis."]
                    if mutation_required
                    else ["Prefer a concise evidence-backed answer over creating a workspace artifact."]
                ),
                "For broad maps, cover the highest-value chain first and mark lower-confidence gaps clearly.",
                "Prefer primary/company/regulatory/source-grounded evidence over unsourced memory when current facts matter.",
                "Keep the final summary concise and inspectable.",
                *(
                    [
                        "Use shell_command only when it materially verifies or inspects the workspace.",
                        "Inspect first, then make a bounded coherent implementation.",
                        "Prefer `file_edit` over whole-file `file_write` when the target file already exists.",
                        "After one failed or truncated large write, immediately switch to a smaller patch strategy.",
                    ]
                    if mutation_required
                    else []
                ),
                (
                    "Avoid rereading the same files unless the next edit truly needs exact grounding."
                    if mutation_required
                    else "Avoid rereading the same files unless the next finding truly needs exact grounding."
                ),
                *(
                    [
                        "For targeted repairs, prefer the smallest responsible path/function/edit range over broad repository discovery.",
                    ]
                    if targeted_source_repair
                    else []
                ),
            ],
            tool_policy={
                "allowed_tool_ids": list(generic_tool_ids),
                "preferred_tool_ids": list(generic_preferred_tool_ids),
                "max_tool_calls": int(args.max_tool_calls),
            },
            contract_snippets=[
                *_super_dan_stage_snippets("builder", tool_ids=generic_tool_ids),
                _super_plan_executor_contract(plan_context),
                _live_pacing_contract(pacing_policy),
                snippets.incremental_edit_contract(),
                snippets.no_scratch_files_contract(),
            ],
            output_contract=OutputContract(
                definition_of_done=definition_of_done,
                expected_return_shape=(
                    _live_answer_return_shape()
                    if not mutation_required
                    else _live_expected_return_shape()
                ),
            ),
            sampling_policy={
                "profile": choice.sampling_policy,
                "temperature": 0.30,
                "max_tokens": _SUPER_DAN_WORKER_MAX_TOKENS,
            },
            evidence=generic_evidence,
            input_payload=generic_input_payload,
            metadata={
                "surface": "super_organism",
                "mode": "live",
                "tool_budget_profile": "super_dan_live",
                "trace_id": run_trace_id,
                "root_task_id": run_task_id,
                "organism_id": report.organism_id,
                "organ_id": "super-dan.live.general",
                "organism_stage": "execution",
                "worker_id": worker_id,
                "operator_intent_policy": operator_intent_payload,
                "exclusive_write_owner_path": prompt_only_creation_target or "",
                "recommended_write_paths": (
                    [prompt_only_creation_target] if prompt_only_creation_target else []
                ),
                "operator_prompt_only_creation": bool(prompt_only_creation_target),
                "interactive_source_implementation": interactive_source_implementation,
                "targeted_source_repair": targeted_source_repair,
            },
        )
    worker = _live_cell_from_brief(
        model=model,
        brief=worker_brief,
        worker_id=worker_id,
        organism_stage="execution",
    )
    request = _request_from_live_brief(worker_brief, args=args)

    async def run_main_builder() -> tuple[Any, list[dict[str, Any]], list[dict[str, Any]]]:
        _log_live_event(
            event_logger,
            "live.generic_build.started",
            model=model,
            workspace_root=str(workspace_root),
            tool_ids=list(generic_tool_ids),
            operator_intent_policy=operator_intent_payload if operator_intent_policy.active else None,
            main_task_id=str((main_frontier_task or {}).get("task_id") or ""),
            parallel_worktree_task_ids=[
                str(item["plan_task"].get("task_id") or "").strip()
                for item in worktree_prepared_tasks
            ],
        )
        main_result, main_tools, main_events = await _execute_live_request(
            worker=worker,
            request=request,
            tool_ids=generic_tool_ids,
            workspace_root=workspace_root,
            args=args,
            model=model,
            provider=provider,
            event_logger=event_logger,
        )
        _log_live_event(
            event_logger,
            "live.generic_build.completed",
            model=model,
            status=main_result.status,
            tool_calls=len(main_tools),
            event_count=len(main_events),
        )
        return main_result, main_tools, main_events

    if worktree_prepared_tasks:
        (result, executed_tools, events), worktree_summary = await asyncio.gather(
            run_main_builder(),
            run_prepared_worktree_frontier(),
        )
    else:
        result, executed_tools, events = await run_main_builder()
        worktree_summary = {
            "synthetic_tools": [],
            "tool_calls": 0,
            "event_count": 0,
            "token_usage": None,
            "applied_task_ids": [],
            "events": [],
        }
    executed_tools.extend(list(worktree_summary.get("synthetic_tools") or []))
    events.extend(list(worktree_summary.get("events") or []))
    if worktree_prepared_tasks and full_plan_context:
        restored_plan_context = dict(full_plan_context)
        if isinstance(plan_context, Mapping) and isinstance(plan_context.get("task_graph_state"), Mapping):
            restored_plan_context["task_graph_state"] = dict(plan_context.get("task_graph_state") or {})
            restored_plan_context["task_graph_revision"] = plan_context.get("task_graph_revision")
        plan_context = restored_plan_context
    builder_payload_for_graph = _extract_validation_payload(
        dict(result.outputs) if isinstance(result.outputs, Mapping) else result.outputs,
    )
    updated_understanding = _extract_request_understanding_from_outputs(
        builder_payload_for_graph,
        fallback=request_understanding,
    )
    if updated_understanding is not None:
        request_understanding = updated_understanding
        _log_live_event(
            event_logger,
            "live.request_understanding.updated",
            source="builder",
            request_understanding=dict(request_understanding),
            request_understanding_schema=request_understanding.get("schema"),
            request_kind=request_understanding.get("request_kind"),
            original_request=request_understanding.get("original_request"),
            workspace_root=request_understanding.get("workspace_root"),
            target_paths=list(request_understanding.get("target_paths") or []),
            aspect_reviews=list(request_understanding.get("aspect_reviews") or []),
            confidence_scoped_acceptance=list(request_understanding.get("confidence_scoped_acceptance") or []),
            stop_rule=request_understanding.get("stop_rule"),
        )
    build_token_usage = _merge_token_usage(
        planning_token_usage,
        _merge_token_usage(
            _extract_execution_usage(result),
            worktree_summary.get("token_usage"),
        ),
    )
    mutated_paths = _mutation_paths_from_tools(
        executed_tools,
        workspace_root=workspace_root,
        exclude_roots=_super_plan_exclude_roots(plan_context),
    )
    if isinstance(plan_context, Mapping) and plan_context.get("task_graph"):
        builder_graph_update = _super_plan_graph_update_payload(builder_payload_for_graph)
        graph_changed_task_ids = _super_plan_string_list(
            builder_graph_update.get("changed_task_ids") or []
        )
        completed_from_mutations = _super_plan_task_ids_for_mutations(
            plan_context,
            mutated_paths,
            workspace_root=workspace_root,
        )
        completed_from_worktrees = _super_plan_string_list(
            worktree_summary.get("applied_task_ids") or []
        )
        execution_completed_task_ids = list(
            dict.fromkeys(
                [
                    *completed_from_mutations,
                    *completed_from_worktrees,
                ]
            )
        )
        active_frontier_ids = _super_plan_string_list(
            [
                *list(plan_context.get("assigned_task_ids") or plan_context.get("ready_task_ids") or []),
                *list(plan_context.get("parallel_worktree_task_ids") or []),
            ]
        )
        remaining_active_task_ids = [
            task_id
            for task_id in active_frontier_ids
            if task_id not in set(execution_completed_task_ids)
        ]
        if execution_completed_task_ids or builder_graph_update:
            task_graph_revision += 1
            plan_context = _super_plan_context_with_graph_state(
                plan_context,
                revision=task_graph_revision,
                source="execution_result",
                update_reason=(
                    builder_graph_update.get("reason")
                    or "Execution produced branch-local task progress from changed owned paths."
                ),
                update_scope=builder_graph_update.get("scope") or "branch_local",
                changed_task_ids=list(
                    dict.fromkeys([*graph_changed_task_ids, *execution_completed_task_ids])
                ),
                active_task_ids=remaining_active_task_ids,
                completed_task_ids=execution_completed_task_ids,
            ) or dict(plan_context)
            _log_live_event(
                event_logger,
                "live.task_graph.updated",
                source="execution_result",
                plan_context=_super_plan_context_payload(
                    plan_context,
                    plan_root=plan_root,
                    workspace_root=workspace_root,
                ),
                task_graph_state=dict(plan_context.get("task_graph_state") or {}),
            )
    mutation_required = operator_intent_policy.allow_workspace_mutation
    error = result.error
    if mutation_required and not mutated_paths and not error:
        error = "live execution finished without any workspace file mutations"
    answer_recovery_attempts = 0
    answer_recovery_limit = _live_answer_recovery_loop_limit(args)
    answer_quality: dict[str, Any] = {}
    validation_tool_calls_total = 0
    validation_event_count_total = 0
    validation_token_usage: dict[str, int] | None = None

    def record_validation_usage(current_validation: Mapping[str, Any]) -> None:
        nonlocal validation_tool_calls_total, validation_event_count_total, validation_token_usage
        validation_tool_calls_total += int(current_validation.get("tool_calls") or 0)
        validation_event_count_total += int(current_validation.get("event_count") or 0)
        validation_token_usage = _merge_token_usage(
            validation_token_usage,
            current_validation.get("token_usage"),
        )

    async def run_generic_answer_recovery() -> dict[str, Any]:
        nonlocal answer_recovery_attempts, result, executed_tools, events, build_token_usage, error

        current_quality = _live_answer_quality(result.outputs, objective=report.target)
        while (
            result.status == "completed"
            and not error
            and not bool(current_quality.get("passed"))
            and answer_recovery_attempts < answer_recovery_limit
        ):
            answer_recovery_attempts += 1
            recovery_reason = str(current_quality.get("reason") or "The final answer is missing.").strip()
            _log_live_event(
                event_logger,
                "live.answer_recovery.started",
                attempt=answer_recovery_attempts,
                model=model,
                reason=recovery_reason,
            )
            recovery_worker_id = "super-dan.live.answer-recovery"
            recovery_brief = role_brief(
                role=RoleSpec(
                    role_label="workspace_worker",
                    responsibility="Recover a missing or inadequate final in-session answer.",
                    success_criteria=[
                        "The response contains the actual answer the operator requested.",
                        "The answer is grounded in available workspace evidence or names a precise blocker.",
                        "The pass does not create or edit workspace files.",
                    ],
                    trace_role="super-dan.live.answer-recovery",
                ),
                task=_live_generic_answer_recovery_task(
                    report,
                    workspace_root=workspace_root,
                    failure_reason=recovery_reason,
                    attempt=answer_recovery_attempts,
                    previous_output=dict(result.outputs) if isinstance(result.outputs, Mapping) else result.outputs,
                    operator_intent_policy=operator_intent_policy,
                    request_understanding=request_understanding,
                ),
                scope=f"workspace={workspace_root}; native Super DAN final-answer recovery",
                hard_constraints=[
                    "Do not create, edit, delete, or otherwise mutate workspace files.",
                    "Do not return another operational receipt such as completed, finished, or Run finished.",
                    "Return the actual in-session answer in the `answer` field, or a precise blocker in that same field.",
                    *list(operator_intent_policy.constraints),
                ],
                soft_constraints=[
                    "Prefer synthesizing from already gathered evidence before doing additional reads.",
                    "If more context is necessary, use the smallest read-only inspection that can answer the operator.",
                    "Keep the recovered answer concise but substantive.",
                ],
                tool_policy={
                    "allowed_tool_ids": list(generic_read_only_tool_ids),
                    "preferred_tool_ids": _filter_tool_ids_for_operator_intent(
                        ["file_read", "list_directory", "git_diff", "git_status", "workspace_check", "web_search"],
                        operator_intent_policy,
                    ),
                    "max_tool_calls": max(4, min(int(args.max_tool_calls), 16)),
                },
                contract_snippets=[
                    *_super_dan_stage_snippets("final_response_recovery", tool_ids=generic_read_only_tool_ids),
                ],
                output_contract=OutputContract(
                    definition_of_done=(
                        "The final response contains a substantive in-session answer or an explicit blocker; "
                        "status receipts and file/change receipts are not enough."
                    ),
                    expected_return_shape=_live_answer_return_shape(),
                ),
                sampling_policy={
                    "profile": choice.sampling_policy,
                    "temperature": 0.2,
                    "max_tokens": _SUPER_DAN_REPAIR_MAX_TOKENS,
                },
                evidence=_super_report_evidence_blocks(report),
                input_payload={
                    "objective": report.target,
                    "workspace_root": str(workspace_root),
                    "failure_reason": recovery_reason,
                    "attempt": answer_recovery_attempts,
                    "previous_output": dict(result.outputs) if isinstance(result.outputs, Mapping) else result.outputs,
                    "operator_intent_policy": operator_intent_payload,
                    "request_understanding": dict(request_understanding),
                    "answer_recovery_rule": (
                        "Each recovery attempt must either produce a substantive answer or a clearer blocker; "
                        "do not repeat the same completion receipt."
                    ),
                },
                metadata={
                    "surface": "super_organism",
                    "mode": "live",
                    "tool_budget_profile": "super_dan_live",
                    "trace_id": run_trace_id,
                    "root_task_id": run_task_id,
                    "organism_id": report.organism_id,
                    "organ_id": "super-dan.live.general",
                    "organism_stage": "final_response_recovery",
                    "worker_id": recovery_worker_id,
                    "operator_intent_policy": operator_intent_payload,
                    "answer_recovery": True,
                },
            )
            recovery_worker = _live_cell_from_brief(
                model=model,
                brief=recovery_brief,
                worker_id=recovery_worker_id,
                organism_stage="final_response_recovery",
            )
            recovery_result, recovery_tools, recovery_events = await _execute_live_request(
                worker=recovery_worker,
                request=_request_from_live_brief(recovery_brief, args=args),
                tool_ids=generic_read_only_tool_ids,
                workspace_root=workspace_root,
                args=args,
                model=model,
                provider=provider,
                event_logger=event_logger,
            )
            result = recovery_result
            executed_tools.extend(recovery_tools)
            events.extend(recovery_events)
            build_token_usage = _merge_token_usage(
                build_token_usage,
                _extract_execution_usage(recovery_result),
            )
            if recovery_result.error:
                error = recovery_result.error
            current_quality = _live_answer_quality(result.outputs, objective=report.target)
            _log_live_event(
                event_logger,
                "live.answer_recovery.completed",
                attempt=answer_recovery_attempts,
                model=model,
                status=recovery_result.status,
                tool_calls=len(recovery_tools),
                event_count=len(recovery_events),
                answer_satisfactory=bool(current_quality.get("passed")),
                reason=str(current_quality.get("reason") or ""),
                answer_preview=_truncate_text(current_quality.get("answer") or "", limit=240),
            )
        return current_quality

    async def run_generic_validator(paths: Sequence[str]) -> dict[str, Any]:
        validator_worker_id = "super-dan.live.general.validator"
        pre_run_file_state = _file_metadata_for_paths(pre_run_workspace_state, paths)
        post_run_file_state = _current_file_metadata_for_paths(paths)
        validator_plan_context = _super_plan_context_payload(
            plan_context,
            plan_root=plan_root,
            workspace_root=workspace_root,
            include_task_state_key="task_state_after_execution",
        )
        validator_brief = review_brief(
                role=RoleSpec(
                    role_label="validator_workspace",
                    responsibility="Validate the Super DAN workspace deliverable in read-only mode.",
                    success_criteria=[
                        "Mutated files and relevant git evidence were inspected.",
                        "The implementation materially advances the operator objective.",
                        "Placeholder-style or non-responsive changes are rejected.",
                    ],
                    artifact_targets=list(paths),
                    trace_role="super-dan.live.general.validator",
                ),
                task=_live_generic_validation_task(
                    report,
                    workspace_root=workspace_root,
                    pre_run_file_state=pre_run_file_state,
                    post_run_file_state=post_run_file_state,
                    operator_intent_policy=operator_intent_policy,
                    plan_context=validator_plan_context,
                    request_understanding=request_understanding,
                ),
                scope=f"workspace={workspace_root}; native Super DAN general workspace validation",
                hard_constraints=[
                    "Read-only validation only; do not write or edit files.",
                    "Inspect the mutated files and relevant read-only git evidence before deciding.",
                    "Use pre-run file-state metadata as the material-change baseline for untracked workspaces; do not require git commits or git history.",
                    *(
                        [
                            "Audit plan checkbox updates against changed deliverable files and evidence; do not pass a run only because it ticked plan tasks.",
                        ]
                        if validator_plan_context
                        else []
                    ),
                    "Fail if the run made only placeholder-style or otherwise non-responsive changes.",
                    "For document/report objectives, inspect the written artifact and fail if no report-like file was produced.",
                    *list(operator_intent_policy.constraints),
                ],
                soft_constraints=[
                    "Prefer concrete missing requirements over vague criticism.",
                    "Judge material advancement against the operator objective.",
                ],
                allowed_tool_ids=generic_read_only_tool_ids,
                tool_policy={
                    "allowed_tool_ids": list(generic_read_only_tool_ids),
                    "preferred_tool_ids": _filter_tool_ids_for_operator_intent(
                        ["git_diff", "file_read", "web_search", "git_status", "list_directory", "workspace_check"],
                        operator_intent_policy,
                    ),
                    "max_tool_calls": max(4, min(int(args.max_tool_calls), 24)),
                },
                contract_snippets=[
                    *_super_dan_stage_snippets(
                        "validator",
                        tool_ids=generic_read_only_tool_ids,
                        extra_questions=(
                            [
                                "Were any plan tasks ticked, and do the changed files prove those tasks are actually complete?"
                            ]
                            if validator_plan_context
                            else None
                        ),
                    ),
                    _super_plan_validation_contract(validator_plan_context),
                ],
                sampling_policy={
                    "profile": "deterministic",
                    "temperature": 0.0,
                    "max_tokens": _SUPER_DAN_VALIDATOR_MAX_TOKENS,
                },
                output_contract=OutputContract(
                    definition_of_done="Return the validation report only.",
                    expected_return_shape=_live_validation_return_shape(),
                ),
                input_payload={
                    "objective": report.target,
                    "workspace_root": str(workspace_root),
                    "mutated_paths": list(paths),
                    "pre_run_file_state": pre_run_file_state,
                    "post_run_file_state": post_run_file_state,
                    "operator_intent_policy": operator_intent_payload,
                    "request_understanding": dict(request_understanding),
                    "plan_context": validator_plan_context,
                },
                metadata={
                    "surface": "super_organism",
                    "mode": "live",
                    "tool_budget_profile": "super_dan_live",
                    "trace_id": run_trace_id,
                    "root_task_id": run_task_id,
                    "organism_id": report.organism_id,
                    "organ_id": "super-dan.live.general",
                    "worker_id": validator_worker_id,
                    "organism_stage": "validation",
                    "operator_intent_policy": operator_intent_payload,
                },
            )
        validator_worker = _live_cell_from_brief(
            model=model,
            brief=validator_brief,
            worker_id=validator_worker_id,
            organism_stage="validation",
        )
        validator_request = _request_from_live_brief(validator_brief, args=args)
        validation_payload = await _run_live_validation(
            worker=validator_worker,
            request=validator_request,
            tool_ids=generic_read_only_tool_ids,
            workspace_root=workspace_root,
            args=args,
            model=model,
            provider=provider,
            event_logger=event_logger,
        )
        return _super_plan_scoped_validation_payload(
            validation_payload,
            validator_plan_context,
        )

    builder_retry_attempts = 0

    if not mutation_required:
        answer_quality = await run_generic_answer_recovery()
        validation = _answer_validation_payload(answer_quality)
        if not bool(validation.get("passed")) and not error:
            error = str(validation.get("repair_brief") or "").strip() or "final answer missing"
    else:
        validation = _failed_validation_payload(
            reason=error or "live execution did not meet the exit contract",
            missing_requirements=(
                ["No workspace file mutations were observed."]
                if mutation_required and not mutated_paths
                else None
            ),
        )

    async def run_generic_builder_retry() -> None:
        nonlocal builder_retry_attempts
        nonlocal result, executed_tools, events, build_token_usage, mutated_paths, error, validation

        additive_recovery_required = _text_requests_additive_update(report.target)
        explicit_write_paths = _explicit_objective_artifact_paths(
            report.target,
            workspace_root=workspace_root,
        )
        if (
            additive_recovery_required
            and explicit_write_paths
            and _target_missing_in_snapshot(
                explicit_write_paths[0],
                workspace_root=workspace_root,
                snapshot=pre_run_workspace_state,
            )
        ):
            additive_recovery_required = False
        pre_existing_workspace_paths = [
            path
            for path, state in sorted(pre_run_workspace_state.items())
            if isinstance(state, Mapping) and bool(state.get("exists"))
        ]
        recommended_write_paths = _generic_builder_retry_targets(
            report.target,
            workspace_root=workspace_root,
            pre_run_workspace_state=pre_run_workspace_state,
            additive_recovery_required=additive_recovery_required,
            operator_intent_policy=operator_intent_policy,
        )
        recovery_tool_ids = [
            tool_id
            for tool_id in ("file_read", "file_write", "file_edit")
            if tool_id in set(generic_tool_ids)
        ] or list(generic_tool_ids)
        if prompt_only_creation_target:
            recovery_tool_ids = _prompt_only_creation_tool_ids(recovery_tool_ids)
        recovery_preferred_tool_ids = [
            tool_id
            for tool_id in ("file_write", "file_edit", "file_read")
            if tool_id in set(recovery_tool_ids)
        ]
        builder_retry_limit = _live_max_builder_retry_attempts(args)
        while (
            not mutated_paths
            and builder_retry_attempts < builder_retry_limit
        ):
            builder_retry_attempts += 1
            recovery_reason = (
                error
                or _validation_repair_brief(validation, [])
                or "live execution finished without any workspace file mutations"
            )
            _log_live_event(
                event_logger,
                "live.builder_retry.started",
                attempt=builder_retry_attempts,
                model=model,
                reason=recovery_reason,
                additive_recovery_required=bool(additive_recovery_required),
                recommended_write_paths=list(recommended_write_paths),
                artifact_kind="generic",
            )
            recovery_worker_id = "super-dan.live.builder-retry"
            recovery_brief = role_brief(
                role=RoleSpec(
                    role_label="workspace_worker",
                    responsibility="Run a focused Super DAN builder retry after a no-mutation attempt.",
                    success_criteria=[
                        "At least one workspace file is created or edited.",
                        "The retry materially advances the original operator objective.",
                        "The final answer names changed files, validation plan, and remaining risks.",
                    ],
                    trace_role="super-dan.live.builder-retry",
                ),
                task=_live_generic_builder_retry_task(
                    report,
                    workspace_root=workspace_root,
                    failure_reason=recovery_reason,
                    attempt=builder_retry_attempts,
                    recommended_write_paths=recommended_write_paths,
                    additive_recovery_required=additive_recovery_required,
                    operator_intent_policy=operator_intent_policy,
                    prompt_only_creation_target=prompt_only_creation_target,
                    plan_context=plan_context,
                    request_understanding=request_understanding,
                ),
                scope=f"workspace={workspace_root}; native Super DAN generic builder retry",
                hard_constraints=[
                    "Actually create or edit at least one workspace file with file_write or file_edit before finalizing.",
                    "Default to the current workspace root; only use explicit external paths when the operator asks and runtime policy allows.",
                    "If the requested deliverable is a saved report, markdown file, data note, or other document artifact, write that artifact to the workspace.",
                    "For additive/enrichment objectives on existing artifacts, preserve existing content and do not replace the artifact with a shorter scaffold.",
                    *(
                        [
                            "Use the supplied run-local plan context if present, but do not count plan-file edits as the required deliverable mutation.",
                            "Tick plan checkboxes only for tasks actually completed in this retry.",
                        ]
                        if plan_context
                        else []
                    ),
                    *list(operator_intent_policy.constraints),
                ],
                soft_constraints=[
                    "Prefer the smallest coherent durable artifact edit that materially advances the objective.",
                    "If a relevant existing artifact is present, prefer file_edit or append-style file_write over replacing it.",
                    "When asked to enrich a section, read that section and insert or replace only the necessary local range while preserving existing tables and quantitative content.",
                    "Avoid additional broad discovery unless the next write depends on one exact path or fact.",
                    "For report objectives, write substantive section content rather than another outline or plan.",
                ],
                tool_policy={
                    "allowed_tool_ids": list(recovery_tool_ids),
                    "preferred_tool_ids": list(recovery_preferred_tool_ids),
                    "max_tool_calls": int(args.max_tool_calls),
                },
                contract_snippets=[
                    *_super_dan_stage_snippets(
                        "builder_retry",
                        tool_ids=recovery_tool_ids,
                    ),
                    _super_plan_executor_contract(plan_context),
                    _live_pacing_contract(pacing_policy),
                    snippets.incremental_edit_contract(),
                    snippets.no_scratch_files_contract(),
                ],
                output_contract=OutputContract(
                    definition_of_done=(
                        "The no-mutation failure is recovered by a concrete workspace file creation or edit, and the final "
                        "response names changed files, validation plan, and remaining risks."
                    ),
                    expected_return_shape=_live_expected_return_shape(),
                ),
                sampling_policy={
                    "profile": choice.sampling_policy,
                    "temperature": 0.25,
                    "max_tokens": _SUPER_DAN_REPAIR_MAX_TOKENS,
                },
                evidence=[] if prompt_only_creation_target else _super_report_evidence_blocks(report),
                input_payload={
                    "objective": report.target,
                    "workspace_root": str(workspace_root),
                    "failure_reason": recovery_reason,
                    "attempt": builder_retry_attempts,
                    "write_pacing": dict(pacing_policy),
                    "additive_recovery_required": bool(additive_recovery_required),
                    "recommended_write_paths": list(recommended_write_paths),
                    "pre_run_workspace_file_state": dict(pre_run_workspace_state),
                    "pre_existing_workspace_paths": list(pre_existing_workspace_paths),
                    "operator_intent_policy": operator_intent_payload,
                    "request_understanding": dict(request_understanding),
                    "plan_context": _super_plan_context_payload(
                        plan_context,
                        plan_root=plan_root,
                        workspace_root=workspace_root,
                        include_task_state_key="task_state_before_retry",
                    ),
                    **(
                        {
                            "execution_condition": "operator_prompt_only_creation",
                            "omitted_context_reason": (
                                "Operator policy forbids other workspace inputs; recovery prompt omits broad organism "
                                "evidence and context that cannot be used before the first write."
                            ),
                        }
                        if prompt_only_creation_target
                        else {}
                    ),
                },
                metadata={
                    "surface": "super_organism",
                    "mode": "live",
                    "tool_budget_profile": "super_dan_live",
                    "trace_id": run_trace_id,
                    "root_task_id": run_task_id,
                    "organism_id": report.organism_id,
                    "organ_id": "super-dan.live.general",
                    "organism_stage": "execution",
                    "worker_id": recovery_worker_id,
                    "exclusive_write_owner_path": (
                        recommended_write_paths[0] if recommended_write_paths else ""
                    ),
                    "recommended_write_paths": list(recommended_write_paths),
                    "builder_retry": True,
                    "operator_intent_policy": operator_intent_payload,
                    "repair_policy": {
                        "forbid_shrinking_existing_artifacts": bool(additive_recovery_required),
                        "target_paths": list(pre_existing_workspace_paths),
                    },
                },
            )
            recovery_worker = _live_cell_from_brief(
                model=model,
                brief=recovery_brief,
                worker_id=recovery_worker_id,
                organism_stage="execution",
            )
            recovery_result, recovery_tools, recovery_events = await _execute_live_request(
                worker=recovery_worker,
                request=_request_from_live_brief(recovery_brief, args=args),
                tool_ids=recovery_tool_ids,
                workspace_root=workspace_root,
                args=args,
                model=model,
                provider=provider,
                event_logger=event_logger,
            )
            result = recovery_result
            executed_tools.extend(recovery_tools)
            events.extend(recovery_events)
            build_token_usage = _merge_token_usage(
                build_token_usage,
                _extract_execution_usage(recovery_result),
            )
            if recovery_result.error:
                error = recovery_result.error
            mutated_paths = _mutation_paths_from_tools(
                executed_tools,
                workspace_root=workspace_root,
                exclude_roots=_super_plan_exclude_roots(plan_context),
            )
            if not mutated_paths and operator_intent_policy.allow_existing_artifact_reuse:
                alias_tool = _materialize_missing_explicit_target_from_existing_artifact(
                    report.target,
                    workspace_root=workspace_root,
                    recommended_write_paths=recommended_write_paths,
                )
                if alias_tool is not None:
                    executed_tools.append(alias_tool)
                    alias_result = (
                        alias_tool.get("result")
                        if isinstance(alias_tool.get("result"), Mapping)
                        else {}
                    )
                    alias_event = {
                        "event": "live.builder_retry.alias_materialized",
                        "attempt": builder_retry_attempts,
                        "target_path": str(alias_result.get("path") or ""),
                        "source_path": str(alias_result.get("source_path") or ""),
                        "reason": "explicit_missing_target_alias",
                    }
                    events.append(alias_event)
                    _log_live_event(event_logger, **alias_event)
                    mutated_paths = _mutation_paths_from_tools(
                        executed_tools,
                        workspace_root=workspace_root,
                        exclude_roots=_super_plan_exclude_roots(plan_context),
                    )
            _log_live_event(
                event_logger,
                "live.builder_retry.completed",
                attempt=builder_retry_attempts,
                model=model,
                status=recovery_result.status,
                tool_calls=len(recovery_tools),
                event_count=len(recovery_events),
                changed_required_files=list(mutated_paths),
                artifact_kind="generic",
            )
            if mutated_paths:
                if not recovery_result.error:
                    error = None
                validation = await run_generic_validator(mutated_paths)
                record_validation_usage(validation)
                return

            error = recovery_result.error or "live execution finished without any workspace file mutations"
            validation = _failed_validation_payload(
                reason=error,
                missing_requirements=["No workspace file mutations were observed."],
            )

    if mutated_paths:
        validation = await run_generic_validator(mutated_paths)
        record_validation_usage(validation)
    elif mutation_required and result.status == "completed":
        await run_generic_builder_retry()

    repair_attempts = 0
    repair_limit = _live_repair_loop_limit(args, default=1)
    while (
        result.status == "completed"
        and mutated_paths
        and not bool(validation.get("passed"))
        and repair_attempts < repair_limit
    ):
        repair_attempts += 1
        repair_reason = _generic_validation_repair_brief(validation, plan_context=plan_context)
        repair_validation = _generic_repair_validation_payload(
            validation,
            repair_brief=repair_reason,
            plan_context=plan_context,
        )
        repair_pre_run_file_state = _file_metadata_for_paths(pre_run_workspace_state, mutated_paths)
        repair_current_file_state = _current_file_metadata_for_paths(mutated_paths)
        additive_repair_required = _generic_validation_requests_additive_repair(validation)
        _log_live_event(
            event_logger,
            "live.generic_repair.started",
            attempt=repair_attempts,
            model=model,
            reason=repair_reason or "validation failed",
            changed_required_files=list(mutated_paths),
            additive_repair_required=bool(additive_repair_required),
        )
        repair_worker_id = "super-dan.live.general-repair"
        repair_brief = role_brief(
            role=RoleSpec(
                role_label="workspace_worker",
                responsibility="Repair the Super DAN workspace deliverable after validation failure.",
                success_criteria=[
                    "Validation feedback is addressed with concrete workspace edits.",
                    "The repair materially advances the original operator objective.",
                    "The final answer names changed files, validation plan, and remaining risks.",
                ],
                artifact_targets=list(mutated_paths),
                trace_role="super-dan.live.general-repair",
            ),
            task=_live_generic_repair_task(
                report,
                workspace_root=workspace_root,
                validation=repair_validation,
                mutated_paths=mutated_paths,
                repair_brief=repair_reason,
                pre_run_file_state=repair_pre_run_file_state,
                current_file_state=repair_current_file_state,
                operator_intent_policy=operator_intent_policy,
                plan_context=plan_context,
                request_understanding=request_understanding,
            ),
            scope=f"workspace={workspace_root}; native Super DAN general workspace validation repair",
            hard_constraints=[
                "Actually edit workspace files; do not return a summary-only response.",
                "Treat validator-named files and missing source markers as required repair targets; do not stop after changing only an adjacent helper or validator file.",
                "After one focused read of a named repair target, make a concrete file_edit/file_write/shell_command mutation or report a precise blocker.",
                "Default to the current workspace root; only use explicit external paths when the operator asks and runtime policy allows.",
                "Do not create, commit, or overwrite a baseline artifact just to satisfy git-history or before/after evidence.",
                *(
                    [
                        "Use the supplied run-local plan context if present, but do not count plan-file edits as the repair deliverable.",
                        "Only tick plan checkboxes that the repair actually completes.",
                    ]
                    if plan_context
                    else []
                ),
                *list(operator_intent_policy.constraints),
            ],
            soft_constraints=[
                "Prefer targeted edits over rewriting the whole artifact.",
                "Address validator feedback directly before polishing unrelated details.",
                "Preserve the existing artifact shape unless the feedback requires broader restructuring.",
                "Prefer `file_edit` over whole-file `file_write` when the target file already exists.",
                "For additive repair feedback, preserve existing substance and expand or target-edit rather than replacing it with a shorter scaffold.",
            ],
            tool_policy={
                "allowed_tool_ids": list(generic_tool_ids),
                "preferred_tool_ids": list(generic_preferred_tool_ids),
                "max_tool_calls": int(args.max_tool_calls),
            },
            contract_snippets=[
                *_super_dan_stage_snippets("repair", tool_ids=generic_tool_ids),
                _super_plan_executor_contract(plan_context),
                _live_pacing_contract(pacing_policy),
                snippets.incremental_edit_contract(),
                snippets.no_scratch_files_contract(),
            ],
            output_contract=OutputContract(
                definition_of_done=(
                    "Validation feedback is addressed with concrete workspace edits and the final response names "
                    "changed files, validation plan, and remaining risks."
                ),
                expected_return_shape=_live_expected_return_shape(),
            ),
            sampling_policy={
                "profile": choice.sampling_policy,
                "temperature": 0.25,
                "max_tokens": _SUPER_DAN_REPAIR_MAX_TOKENS,
            },
            evidence=_super_report_evidence_blocks(report),
            input_payload={
                "objective": report.target,
                "workspace_root": str(workspace_root),
                "mutated_paths": list(mutated_paths),
                "validation": repair_validation,
                "pre_run_file_state": repair_pre_run_file_state,
                "current_file_state": repair_current_file_state,
                "write_pacing": dict(pacing_policy),
                "operator_intent_policy": operator_intent_payload,
                "request_understanding": dict(request_understanding),
                "plan_context": _super_plan_context_payload(
                    plan_context,
                    plan_root=plan_root,
                    workspace_root=workspace_root,
                    include_task_state_key="task_state_before_repair",
                ),
            },
            metadata={
                "surface": "super_organism",
                "mode": "live",
                "tool_budget_profile": "super_dan_live",
                "trace_id": run_trace_id,
                "root_task_id": run_task_id,
                "organism_id": report.organism_id,
                "organ_id": "super-dan.live.general",
                "organism_stage": "execution",
                "worker_id": repair_worker_id,
                "operator_intent_policy": operator_intent_payload,
                "validation_repair": True,
                "recommended_write_paths": list(mutated_paths),
                "required_repair_paths": list(mutated_paths),
                "repair_policy": {
                    "forbid_shrinking_existing_artifacts": bool(additive_repair_required),
                    "target_paths": list(mutated_paths),
                },
            },
        )
        repair_worker = _live_cell_from_brief(
            model=model,
            brief=repair_brief,
            worker_id=repair_worker_id,
            organism_stage="execution",
        )
        repair_result, repair_tools, repair_events = await _execute_live_request(
            worker=repair_worker,
            request=_request_from_live_brief(repair_brief, args=args),
            tool_ids=generic_tool_ids,
            workspace_root=workspace_root,
            args=args,
            model=model,
            provider=provider,
            event_logger=event_logger,
        )
        result = repair_result
        executed_tools.extend(repair_tools)
        events.extend(repair_events)
        build_token_usage = _merge_token_usage(
            build_token_usage,
            _extract_execution_usage(repair_result),
        )
        if repair_result.error:
            error = repair_result.error
        mutated_paths = _mutation_paths_from_tools(
            executed_tools,
            workspace_root=workspace_root,
            exclude_roots=_super_plan_exclude_roots(plan_context),
        )
        _log_live_event(
            event_logger,
            "live.generic_repair.completed",
            attempt=repair_attempts,
            model=model,
            status=repair_result.status,
            tool_calls=len(repair_tools),
            event_count=len(repair_events),
            changed_required_files=list(mutated_paths),
        )
        validation = _failed_validation_payload(
            reason=error or "live workspace repair did not meet the exit contract",
            missing_requirements=(
                ["No workspace file mutations were observed."]
                if not mutated_paths
                else None
            ),
        )
        if mutated_paths:
            validation = await run_generic_validator(mutated_paths)
            record_validation_usage(validation)
    final_task_graph_state: dict[str, Any] | None = None
    if isinstance(plan_context, Mapping) and plan_context.get("task_graph"):
        validation_graph_update = _super_plan_graph_update_payload(validation)
        validation_changed_task_ids = _super_plan_string_list(
            validation_graph_update.get("changed_task_ids") or []
        )
        inferred_completed_task_ids = _super_plan_task_ids_for_mutations(
            plan_context,
            mutated_paths,
            workspace_root=workspace_root,
        )
        completed_task_ids = list(
            dict.fromkeys(
                [
                    *_super_plan_existing_completed_task_ids(plan_context),
                    *inferred_completed_task_ids,
                ]
            )
        )
        ready_next_task_ids = _super_plan_string_list(validation.get("ready_next_task_ids") or [])
        if ready_next_task_ids:
            ready_next_set = set(ready_next_task_ids)
            completed_task_ids = [
                task_id for task_id in completed_task_ids if task_id not in ready_next_set
            ]
        task_graph = _super_plan_task_graph(plan_context.get("task_graph") or [])
        if bool(validation.get("passed")):
            active_after_validation: list[str] = []
            ready_after_validation: list[str] | None = (
                ready_next_task_ids
                or _super_plan_ready_task_ids(
                    task_graph,
                    completed_task_ids=completed_task_ids,
                )
            )
        else:
            active_after_validation = [
                task_id
                for task_id in _super_plan_string_list(
                    [
                        *list(
                            plan_context.get("assigned_task_ids")
                            or plan_context.get("ready_task_ids")
                            or []
                        ),
                        *list(plan_context.get("parallel_worktree_task_ids") or []),
                    ]
                )
                if task_id not in set(completed_task_ids)
            ]
            ready_after_validation = ready_next_task_ids or _super_plan_string_list(
                plan_context.get("ready_task_ids") or []
            )
        if ready_next_task_ids or bool(validation.get("passed")):
            deferred_after_validation = _super_plan_deferred_task_ids(
                task_graph,
                ready_after_validation or [],
                completed_task_ids=completed_task_ids,
            )
        else:
            deferred_after_validation = _super_plan_string_list(
                plan_context.get("deferred_task_ids") or []
            )
        task_graph_revision += 1
        plan_context = _super_plan_context_with_graph_state(
            plan_context,
            revision=task_graph_revision,
            source="validator",
            update_reason=(
                validation_graph_update.get("reason")
                or (
                    "Validator advanced the ready frontier after the current branch update."
                    if bool(validation.get("passed"))
                    else "Validator kept the graph scoped to the current branch after finding gaps."
                )
            ),
            update_scope=validation_graph_update.get("scope") or "branch_local",
            changed_task_ids=list(
                dict.fromkeys([*validation_changed_task_ids, *completed_task_ids])
            ),
            active_task_ids=active_after_validation,
            completed_task_ids=completed_task_ids,
            ready_task_ids=ready_after_validation,
            deferred_task_ids=deferred_after_validation,
            dependency_revisions=list(validation.get("dependency_revisions") or []),
        ) or dict(plan_context)
        final_task_graph_state = dict(plan_context.get("task_graph_state") or {})
        _log_live_event(
            event_logger,
            "live.task_graph.updated",
            source="validator",
            plan_context=_super_plan_context_payload(
                plan_context,
                plan_root=plan_root,
                workspace_root=workspace_root,
            ),
            task_graph_state=final_task_graph_state,
        )
    _log_final_validation_event(
        event_logger,
        worker_id="super-dan.live.general.validator",
        model=model,
        validation=validation,
        deterministic_failures=list(validation.get("missing_requirements") or []),
        changed_required_files=mutated_paths,
        builder_retry_attempted=bool(builder_retry_attempts),
        repair_attempted=bool(repair_attempts),
        repair_exhausted=bool(repair_attempts and not validation.get("passed")),
        task_graph_state=final_task_graph_state,
    )
    if not validation.get("passed") and not error:
        error = (
            _generic_validation_repair_brief(validation, plan_context=plan_context)
            or "live execution failed validation"
        )
    status = (
        "completed"
        if (
            result.status == "completed"
            and bool(validation.get("passed"))
            and (bool(mutated_paths) or not mutation_required)
        )
        else "failed"
    )
    token_usage = _merge_token_usage(
        build_token_usage,
        validation_token_usage,
    )
    raw_summary = (
        (result.outputs.get("result") or result.outputs.get("text") or "")
        if isinstance(result.outputs, Mapping)
        else str(result.outputs or "")
    )
    final_summary = (
        str(answer_quality.get("answer") or "").strip()
        if not mutation_required
        else ""
    ) or str(raw_summary or "")
    return {
        "status": status,
        "mode": "live",
        "model": model,
        "workspace_root": str(workspace_root),
        "files": list(mutated_paths),
        "required_files": [],
        "missing_files": [],
        "tool_calls": (
            len(executed_tools)
            + validation_tool_calls_total
            + planning_tool_calls_total
            + int(worktree_summary.get("tool_calls") or 0)
        ),
        "mutated_paths": list(mutated_paths),
        "event_count": len(events) + validation_event_count_total + planning_event_count_total,
        "summary": final_summary,
        "error": error,
        "summary_label": "Live Run",
        "objective_kind": "general",
        "answer_recovery_attempts": int(answer_recovery_attempts),
        "token_usage": token_usage,
        "validation": validation,
        "request_understanding": dict(request_understanding),
        "plan_context": _super_plan_context_payload(
            plan_context,
            plan_root=plan_root,
            workspace_root=workspace_root,
            include_task_state_key="task_state_final",
        ),
    }


async def _close_live_provider(
    provider: LLMProvider,
    *,
    event_logger: SuperRunEventLogger | None = None,
) -> None:
    for attr_name in ("close", "aclose"):
        closer = getattr(provider, attr_name, None)
        if not callable(closer):
            continue
        try:
            result = closer()
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            _log_live_event(
                event_logger,
                "provider.close.failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )
        return


async def _run_live_execution_with_provider_cleanup(
    report: SuperOrganismReport,
    args: argparse.Namespace,
    *,
    model: str,
    provider: LLMProvider,
    run_trace_id: str,
    run_task_id: str,
    event_logger: SuperRunEventLogger | None,
    objective_kind: str,
) -> dict[str, Any]:
    try:
        return await _run_live_generic_execution(
            report,
            args,
            model=model,
            provider=provider,
            run_trace_id=run_trace_id,
            run_task_id=run_task_id,
            event_logger=event_logger,
        )
    finally:
        await _close_live_provider(provider, event_logger=event_logger)


def _mutation_paths_from_tools(
    executed_tools: Sequence[dict[str, Any]],
    *,
    workspace_root: Path,
    exclude_roots: Sequence[Path] = (),
) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    excluded = [root.resolve(strict=False) for root in exclude_roots]
    for tool in executed_tools:
        tool_id = str(tool.get("tool_id") or "")
        if not tool.get("ok") or tool_id not in {"file_write", "file_edit", "shell_command"}:
            continue
        arguments = tool.get("arguments") if isinstance(tool.get("arguments"), dict) else {}
        result = tool.get("result") if isinstance(tool.get("result"), dict) else {}
        if tool_id == "shell_command":
            try:
                exit_code = int(result.get("exit_code", 0))
            except (TypeError, ValueError):
                exit_code = 0
            changes = result.get("workspace_changes") if exit_code == 0 else {}
            raw_paths = changes.get("changed_paths") if isinstance(changes, dict) else []
            if not isinstance(raw_paths, SequenceABC) or isinstance(raw_paths, (str, bytes)):
                continue
            for raw_path in raw_paths:
                text = str(raw_path or "").strip()
                if not text:
                    continue
                candidate = Path(text).expanduser()
                path = candidate if candidate.is_absolute() else workspace_root / candidate
                resolved = path.resolve(strict=False)
                if any(_path_is_under(resolved, root) for root in excluded):
                    continue
                rendered = str(resolved)
                if rendered in seen:
                    continue
                seen.add(rendered)
                paths.append(rendered)
            continue
        if (
            tool_id == "file_edit"
            and (result.get("changed") is False or result.get("no_op") is True)
        ):
            continue
        raw_path = str(result.get("path") or arguments.get("path") or arguments.get("file_path") or "").strip()
        if not raw_path:
            continue
        candidate = Path(raw_path).expanduser()
        path = candidate if candidate.is_absolute() else workspace_root / candidate
        resolved = path.resolve(strict=False)
        if any(_path_is_under(resolved, root) for root in excluded):
            continue
        rendered = str(resolved)
        if rendered in seen:
            continue
        seen.add(rendered)
        paths.append(rendered)
    return paths


def _render_website_html(report: SuperOrganismReport) -> str:
    objective = _html_escape(report.target)
    cell_count = int(report.cell_count)
    nodes = "\n".join(
        f"""          <article class="node-card" data-status="{_html_escape(node.status)}">
            <span>{_html_escape(node.node_id)}</span>
            <h3>{_html_escape(node.title)}</h3>
            <p>{_html_escape('; '.join(node.notes[:1]) or 'A coordinated cell contract.')}</p>
          </article>"""
        for node in report.delivery_plan
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Super DAN Organism</title>
  <link rel="stylesheet" href="./styles.css" />
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div class="hero-copy">
        <p class="eyebrow">{cell_count}-cell universal agent organism</p>
        <h1>Super DAN turns one objective into coordinated execution.</h1>
        <p class="lede">{objective}</p>
        <div class="metrics">
          <span>{cell_count} logical cells</span>
          <span>scheduler-managed waves</span>
          <span>{_html_escape(report.score_label)} {report.credibility_score:.2f}</span>
        </div>
      </div>
      <div class="organism-stage" aria-label="Animated {cell_count}-cell organism">
        <div class="cell-field"></div>
        <div class="core">DAN</div>
      </div>
    </section>

    <section class="organs">
      <p class="eyebrow">Organ contracts</p>
      <h2>Cells specialize, then synchronize.</h2>
      <div class="organ-grid">
        <div>Brain<br><strong>objective + authority</strong></div>
        <div>Scout<br><strong>context acquisition</strong></div>
        <div>Claim<br><strong>work graph atoms</strong></div>
        <div>Immune<br><strong>risk pressure</strong></div>
        <div>Memory<br><strong>shared board</strong></div>
        <div>Experiment<br><strong>execution probe</strong></div>
        <div>Synthesis<br><strong>done / continue / clarify</strong></div>
      </div>
    </section>

    <section class="contract">
      <p class="eyebrow">Execution contract</p>
      <h2>{_html_escape(report.final_verdict)}</h2>
      <div class="node-grid">
{nodes}
      </div>
    </section>
  </main>
  <script src="./app.js"></script>
</body>
</html>
"""


def _render_website_css() -> str:
    return """:root {
  color-scheme: dark;
  --bg: #10130f;
  --panel: #182017;
  --ink: #f2ead8;
  --muted: #b7ad96;
  --signal: #f2b84b;
  --immune: #ff6b4a;
  --memory: #7ed7b5;
  --brain: #a7c7ff;
}

* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  background:
    radial-gradient(circle at 20% 10%, rgba(242, 184, 75, .22), transparent 28rem),
    radial-gradient(circle at 90% 20%, rgba(126, 215, 181, .14), transparent 24rem),
    linear-gradient(135deg, #10130f, #070806 70%);
  color: var(--ink);
  font: 16px/1.5 "Iowan Old Style", "Palatino Linotype", Palatino, serif;
}
.shell { width: min(1180px, calc(100vw - 40px)); margin: 0 auto; padding: 56px 0; }
.hero { display: grid; grid-template-columns: 1fr 520px; gap: 48px; align-items: center; min-height: 78vh; }
.eyebrow { color: var(--signal); letter-spacing: .18em; text-transform: uppercase; font: 700 12px/1.2 "Avenir Next", "Gill Sans", sans-serif; }
h1, h2 { line-height: .94; margin: 0; letter-spacing: -.05em; }
h1 { font-size: clamp(48px, 8vw, 104px); max-width: 820px; }
h2 { font-size: clamp(36px, 6vw, 72px); }
.lede { color: var(--muted); font-size: clamp(18px, 2vw, 24px); max-width: 680px; }
.metrics { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 28px; }
.metrics span, .node-card, .organ-grid div {
  border: 1px solid rgba(242, 234, 216, .16);
  background: rgba(24, 32, 23, .74);
  box-shadow: 0 20px 80px rgba(0,0,0,.24);
}
.metrics span { padding: 10px 14px; border-radius: 999px; color: var(--ink); }
.organism-stage { position: relative; aspect-ratio: 1; border-radius: 48px; overflow: hidden; background: rgba(24, 32, 23, .64); border: 1px solid rgba(242, 234, 216, .14); }
.cell-field { position: absolute; inset: 0; }
.cell { position: absolute; width: 10px; height: 10px; border-radius: 999px; background: var(--signal); transform: translate(-50%, -50%); animation: pulse 2.8s ease-in-out infinite; box-shadow: 0 0 22px currentColor; }
.cell:nth-child(3n) { color: var(--memory); background: var(--memory); }
.cell:nth-child(5n) { color: var(--immune); background: var(--immune); }
.cell:nth-child(7n) { color: var(--brain); background: var(--brain); }
.core { position: absolute; inset: 50% auto auto 50%; transform: translate(-50%, -50%); width: 112px; height: 112px; border-radius: 50%; display: grid; place-items: center; background: var(--ink); color: #111; font: 900 26px/1 "Avenir Next", "Gill Sans", sans-serif; }
.organs, .contract { padding: 84px 0 0; }
.organ-grid, .node-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-top: 28px; }
.organ-grid div, .node-card { border-radius: 24px; padding: 22px; min-height: 132px; }
.node-card span { color: var(--signal); font: 800 12px/1 "Avenir Next", "Gill Sans", sans-serif; }
.node-card h3 { margin: 12px 0 8px; font-size: 22px; line-height: 1.05; }
.node-card p { margin: 0; color: var(--muted); }

@keyframes pulse {
  0%, 100% { transform: translate(-50%, -50%) scale(.74); opacity: .45; }
  45% { transform: translate(-50%, -50%) scale(1.4); opacity: 1; }
}
@media (max-width: 900px) {
  .hero { grid-template-columns: 1fr; }
  .organ-grid, .node-grid { grid-template-columns: 1fr; }
}
@media (prefers-reduced-motion: reduce) {
  .cell { animation: none; }
}
"""


def _render_website_js(report: SuperOrganismReport) -> str:
    total = max(1, int(report.cell_count))
    return f"""const field = document.querySelector('.cell-field');
if (field) {{
  const total = {total};
  const rings = Math.max(1, Math.ceil(total / 20));
  const perRing = Math.max(1, Math.ceil(total / rings));
  for (let index = 0; index < total; index += 1) {{
    const cell = document.createElement('i');
    cell.className = 'cell';
    const ring = Math.floor(index / perRing) + 1;
    const angle = (index % perRing) / perRing * Math.PI * 2 + ring * 0.22;
    const radius = 8 + (ring / (rings + 1)) * 34;
    const x = 50 + Math.cos(angle) * radius;
    const y = 50 + Math.sin(angle) * radius;
    cell.style.left = `${{x}}%`;
    cell.style.top = `${{y}}%`;
    cell.style.animationDelay = `${{(index % 20) * 70}}ms`;
    field.appendChild(cell);
  }}
}}
"""


def _render_website_readme(report: SuperOrganismReport) -> str:
    return f"""# Super DAN Website Artifact

Generated by `dan super-organism` from this objective:

```text
{report.target}
```

Open `index.html` in a browser, or serve this folder with any static server.

The artifact is deterministic, self-contained, and dependency-free.
"""


def _html_escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _run_super_turn(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if bool(getattr(args, "live", False)) and bool(getattr(args, "plan_only", False)):
        parser.error("--live cannot be combined with --plan-only")

    try:
        report = run_super_organism_demo(
            args.target,
            organism_id=str(args.organism_id),
            cell_count=int(args.cell_count),
            active_cell_cap=_resolve_internal_active_cell_cap(args),
        )
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    implicit_live = _should_implicit_live(report, args)
    if implicit_live:
        setattr(args, "live", True)
        setattr(args, "_implicit_live", True)
        setattr(args, "_code_like_live", True)
    elif not hasattr(args, "_implicit_live"):
        setattr(args, "_implicit_live", False)
    if bool(getattr(args, "live", False)):
        setattr(args, "_code_like_live", True)

    payload = report.model_dump(mode="json")
    live_result: dict[str, Any] | None = None
    if bool(getattr(args, "live", False)):
        if not _supports_live_execution(report, args):
            parser.error(
                "--live could not resolve a generic Super DAN workspace-deliverable lane"
            )
        live_workspace_root = normalize_workspace_root(str(args.workspace))
        live_workdir, live_turn_number = _build_super_run_workdir(live_workspace_root)
        live_trace_id = new_trace_id()
        live_task_id = f"super-dan-live:{live_turn_number}"
        objective_kind = "general"
        progress_renderer_factory = getattr(args, "_progress_renderer_factory", None)
        progress_enabled = (
            not bool(getattr(args, "json", False))
            and not bool(getattr(args, "quiet_progress", False))
        )
        if callable(progress_renderer_factory):
            progress_renderer = progress_renderer_factory(enabled=progress_enabled, args=args)
        else:
            progress_renderer = SuperProgressRenderer(enabled=progress_enabled)
        hook_runtime = SuperHookRuntime(
            state_root=live_workspace_root / ".dan-super" / "state",
            run_id=live_task_id,
            turn_id=str(live_turn_number),
            task_id=live_task_id,
            trace_id=live_trace_id,
            reactivity_profile=str(getattr(args, "reactivity", "balanced") or "balanced"),
            worktree_parallelism=max(0, int(getattr(args, "worktree_parallelism", 0) or 0)),
        )
        event_logger = SuperRunEventLogger(
            path=live_workdir / "events.jsonl",
            session_id=_super_session_id(live_workspace_root),
            turn_id=str(live_turn_number),
            task_id=live_task_id,
            organism_id=report.organism_id,
            organ_id="super-dan.live",
            trace_id=live_trace_id,
            progress_callback=progress_renderer,
            hook_runtime=hook_runtime,
        )
        try:
            selected_skill_mentions = _selected_super_dan_skill_mentions_from_args(args)
            selected_skill_preflight_notes = _selected_super_dan_skill_preflight_notes_from_args(args)
            _log_live_event(
                event_logger,
                "run.log.started",
                trace_id=live_trace_id,
                task_id=live_task_id,
                turn_number=live_turn_number,
                objective=report.target,
                objective_kind=objective_kind,
                workspace_root=str(live_workspace_root),
                workdir=str(live_workdir),
                requested_model=str(args.model or "").strip() or None,
                selected_skill_mentions=selected_skill_mentions,
                selected_skill_preflight=selected_skill_preflight_notes,
            )
            preflight_ok, preflight_notes = _run_selected_super_dan_skill_preflights(
                args,
                workspace_root=live_workspace_root,
            )
            for note in preflight_notes:
                _log_live_event(
                    event_logger,
                    "skill.preflight.failed" if not preflight_ok and "failed" in note else "skill.preflight.completed",
                    selected_skill_mentions=selected_skill_mentions,
                    note=note,
                )
            if not preflight_ok:
                message = "; ".join(preflight_notes) or "selected skill preflight failed"
                _log_live_event(
                    event_logger,
                    "run.log.failed",
                    trace_id=live_trace_id,
                    task_id=live_task_id,
                    status="failed",
                    error_type="SkillPreflightError",
                    error=message,
                    event_log_path=str(event_logger.path),
                    event_log_schema=ORGANISM_LOG_SCHEMA_VERSION,
                )
                if not bool(getattr(args, "_suppress_live_failed_stderr", False)):
                    print(message, file=sys.stderr)
                return 2
            try:
                model = _resolve_live_model(args.model)
                _log_live_event(
                    event_logger,
                    "provider.build.started",
                    requested_model=str(args.model or "").strip() or None,
                    model=model,
                    base_url=str(args.base_url or "").strip() or None,
                )
                provider = _build_live_provider(
                    model,
                    api_key=args.api_key,
                    base_url=args.base_url,
                )
                _log_live_event(
                    event_logger,
                    "provider.build.completed",
                    model=model,
                    base_url=str(args.base_url or "").strip() or None,
                )
            except Exception as exc:
                _log_live_event(
                    event_logger,
                    "provider.build.failed",
                    error_type=type(exc).__name__,
                    error=str(exc),
                    requested_model=str(args.model or "").strip() or None,
                    base_url=str(args.base_url or "").strip() or None,
                )
                _log_live_event(
                    event_logger,
                    "run.log.failed",
                    trace_id=live_trace_id,
                    task_id=live_task_id,
                    status="failed",
                    error_type=type(exc).__name__,
                    error=str(exc),
                    event_log_path=str(event_logger.path),
                    event_log_schema=ORGANISM_LOG_SCHEMA_VERSION,
                )
                if bool(getattr(args, "_suppress_live_failed_stderr", False)):
                    return 2
                parser.error(str(exc))
                return 2
            try:
                live_result = asyncio.run(
                    _run_live_execution_with_provider_cleanup(
                        report,
                        args,
                        model=model,
                        provider=provider,
                        run_trace_id=live_trace_id,
                        run_task_id=live_task_id,
                        event_logger=event_logger,
                        objective_kind=objective_kind,
                    )
                )
            except Exception as exc:
                _log_live_event(
                    event_logger,
                    "run.log.failed",
                    trace_id=live_trace_id,
                    task_id=live_task_id,
                    status="failed",
                    error_type=type(exc).__name__,
                    error=str(exc),
                    event_log_path=str(event_logger.path),
                    event_log_schema=ORGANISM_LOG_SCHEMA_VERSION,
                )
                if not bool(getattr(args, "_suppress_live_failed_stderr", False)):
                    print(
                        f"Live Super DAN failed: {type(exc).__name__}: {exc}",
                        file=sys.stderr,
                    )
                return 1
            live_result = {
                **dict(live_result or {}),
                "event_log_path": str(event_logger.path),
                "event_log_schema": ORGANISM_LOG_SCHEMA_VERSION,
                "hook_state": event_logger.hook_state_snapshot(),
                "show_queue_status": bool(getattr(args, "queue_status", False)),
            }
            _log_live_event(
                event_logger,
                "run.log.completed",
                trace_id=live_trace_id,
                task_id=live_task_id,
                status=live_result.get("status"),
                objective_kind=objective_kind,
                validation_passed=bool(
                    dict(live_result.get("validation") or {}).get("passed")
                ),
                tool_calls=int(live_result.get("tool_calls") or 0),
                event_count=int(live_result.get("event_count") or 0),
                event_log_path=str(event_logger.path),
                event_log_schema=ORGANISM_LOG_SCHEMA_VERSION,
            )
        finally:
            event_logger.close()
        payload = {"report": payload, "live_build": live_result}

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
        if live_result is not None and live_result.get("status") != "completed":
            return 1
    elif live_result is not None:
        live_report_printer = getattr(args, "_live_report_printer", None)
        if not callable(live_report_printer):
            live_report_printer = _print_live_report
        live_report_printer(
            report,
            live_result,
            verbose=bool(getattr(args, "verbose", False)),
        )
        if live_result.get("status") != "completed":
            return 1
    else:
        paths = (
            _materialize_website_artifact(report, args)
            if _should_materialize_website(report, args)
            else []
        )
        if bool(getattr(args, "verbose", False)):
            _print_text_report(report)
            if paths:
                artifact_lines = ["", "Materialized Artifacts:", *[f"- {path}" for path in paths], ""]
                print("\n".join(artifact_lines), end="\n")
        else:
            _print_compact_report(report, artifact_paths=paths)
    return 0


def _timestamped_backup_path(path: Path) -> Path:
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.name}.backup-{stamp}")
    if not candidate.exists():
        return candidate
    for index in range(2, 1000):
        numbered = path.with_name(f"{path.name}.backup-{stamp}-{index}")
        if not numbered.exists():
            return numbered
    raise RuntimeError(f"could not allocate backup path for {path}")


def _archive_reset_path(path: Path, label: str) -> str:
    if not path.exists():
        return f"No Super DAN {label} exists at {path}"
    try:
        backup_path = _timestamped_backup_path(path)
        path.rename(backup_path)
    except (OSError, RuntimeError) as exc:
        return f"Reset failed for {path}: {exc}"
    return f"Archived Super DAN {label}: {path} -> {backup_path}"


def _reset_super_context(workspace_root: Path, scope: str = "") -> str:
    normalized_scope = scope.strip().lower()
    if normalized_scope in {"", "all", "context"}:
        return _archive_reset_path(workspace_root / ".dan-super", "context")
    if normalized_scope in {"state", "queues", "queue"}:
        return _archive_reset_path(workspace_root / ".dan-super" / "state", "state")
    return "Usage: /reset [all|state]"


def _interactive_loop(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    workspace_root = normalize_workspace_root(str(args.workspace))
    print("Super DAN interactive")
    print(f"workspace: {workspace_root}")
    print(
        "Type an objective, /plan <objective> for a dry contract, "
        "/status for queues, /reset [all|state], or /exit."
    )
    while True:
        try:
            text = input("super-dan> ")
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            return 130
        objective = text.strip()
        if not objective:
            continue
        lowered = objective.lower()
        if lowered in {"/exit", "/quit", "exit", "quit"}:
            return 0
        if lowered in {"/status", "/queues", "status"}:
            print(format_super_queue_status(workspace_root))
            continue
        if lowered in {"/reset", "reset", "/clear", "clear"} or lowered.startswith(
            ("/reset ", "reset ", "/clear ", "clear ")
        ):
            reset_scope = ""
            if " " in objective:
                reset_scope = objective.split(maxsplit=1)[1]
            print(_reset_super_context(workspace_root, reset_scope))
            continue
        plan_only = False
        if lowered.startswith("/plan "):
            objective = objective[6:].strip()
            plan_only = True
        if not objective:
            continue
        turn_args = copy.copy(args)
        turn_args.target = objective
        parsed = _prepare_super_dan_skill_invocation_args(
            turn_args,
            workspace_root=workspace_root,
            source="cli",
        )
        if parsed.message:
            print(parsed.message)
        if not parsed.should_run:
            continue
        if not str(turn_args.target or "").strip():
            continue
        turn_args.plan_only = plan_only
        turn_args.live = bool(getattr(args, "live", False)) or not plan_only
        turn_args.json = False
        turn_args.output = None
        turn_args._code_like_live = not plan_only
        try:
            exit_code = _run_super_turn(turn_args, parser)
        except SystemExit as exc:
            exit_code = int(exc.code or 0) if isinstance(exc.code, int) else 2
        if exit_code not in {0, 1}:
            return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    load_env()
    parser = build_parser()
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(raw_argv)
    setattr(args, "_live_explicit", _argv_has_option(raw_argv, "--live"))
    setattr(args, "_model_explicit", _argv_has_option(raw_argv, "--model"))
    setattr(args, "_artifact_dir_explicit", _argv_has_option(raw_argv, "--artifact-dir"))
    setattr(args, "_implicit_live", False)
    setattr(args, "_code_like_live", False)
    setattr(args, "_stdin_is_tty", sys.stdin.isatty())

    no_objective = not str(getattr(args, "target", "") or "").strip()
    stdin_is_tty = bool(getattr(args, "_stdin_is_tty", False))
    if no_objective and bool(getattr(args, "queue_status", False)):
        workspace_root = normalize_workspace_root(str(args.workspace))
        print(format_super_queue_status(workspace_root))
        return 0
    report_mode_requested = any(
        bool(getattr(args, field, False))
        for field in ("plan_only", "json", "verbose")
    ) or bool(getattr(args, "output", None))
    if no_objective and not report_mode_requested and stdin_is_tty:
        return _interactive_loop(args, parser)
    if no_objective and bool(getattr(args, "live", False)):
        parser.error(
            "objective required for --live; pass an objective or run in a terminal for interactive mode"
        )
    if no_objective and not report_mode_requested:
        parser.error(
            "objective required in non-interactive mode; pass an objective, run in a terminal "
            "for interactive mode, or use --queue-status"
        )

    if not no_objective:
        parsed = _prepare_super_dan_skill_invocation_args(args, source="cli")
        if parsed.message and not bool(getattr(args, "json", False)):
            print(parsed.message)
        if not parsed.should_run:
            return 0

    return _run_super_turn(args, parser)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
