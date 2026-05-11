"""Terminal UI for Super DAN live runs.

This module is intentionally a sibling surface to ``dan super-organism``.
It reuses the Super DAN runner and event log, but owns terminal rendering.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from dan.cli import _try_import_rich, load_env, normalize_workspace_root
from dan.cli.super_hooks import format_super_queue_status
from dan.cli import super_organism as super_cli
from dan.skills import invocation as skill_invocation


_TUI_COMMANDS: tuple[tuple[str, str], ...] = (
    ("/plan", "show the deterministic contract for an objective"),
    ("/status", "show Super DAN hook/inbox queue state"),
    ("/queues", "alias for /status"),
    ("/skills", "list skill mentions available for $ autocomplete"),
    ("/reset", "archive .dan-super context or state"),
    ("/help", "show shell commands"),
    ("/exit", "leave the TUI"),
    ("/append", "planned active-run checkpoint append"),
    ("/continue", "planned continue-after-current command"),
    ("/pause", "planned active-run pause"),
    ("/cancel", "planned active-run cancel"),
)
_PROMPT_TOOLKIT_FALLBACK_WARNED = False
_RICH_TOOL_TERMS = (
    "file_read",
    "file_write",
    "file_edit",
    "list_directory",
    "shell_command",
    "workspace_check",
    "web_search",
    "http_request",
    "pdf_read",
    "git_status",
)
_RICH_STATUS_STYLES = (
    (r"\b(?:failed|failure|blocked|blocker|denied|error|missing|rejected|invalid)\b", "bold red"),
    (r"\b(?:passed|completed|complete|changed|created|updated|done|ok|activated)\b", "bold green"),
    (r"\b(?:running|started|starting|planning|model|validation|builder|workspace)\b", "bold cyan"),
    (r"\b(?:waiting|queued|queue|repair|retry|pending|fallback|still)\b", "bold yellow"),
)
_RICH_PATH_RE = re.compile(
    r"(?<![\w$])(?:"
    r"~|/|\./|\.\./|\.dan-super/|docs/|src/|tests/|apps/|website/|data/|raw/|figures/|tables/|logs/|beamer/"
    r")[^\s,;:)]+"
    r"|(?<![\w$])[\w.-]+\.(?:py|md|jsonl?|html|css|js|ts|tsx|txt|csv|parquet|tex|pdf|png|jpg|jpeg|svg)(?![\w])"
)


def _clip(value: Any, *, limit: int = 96) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _append_unique(values: list[str], value: Any, *, limit: int = 10) -> None:
    text = str(value or "").strip()
    if not text or text in values:
        return
    values.append(text)
    del values[:-limit]


def _tool_request_summary(tool_id: str, arguments: Mapping[str, Any]) -> str:
    try:
        return super_cli._tool_request_summary(tool_id, arguments)
    except Exception:
        if "path" in arguments:
            return _clip(arguments.get("path"), limit=120)
        return _clip(json.dumps(dict(arguments), sort_keys=True, default=str), limit=120)


def _tool_result_summary(tool_id: str, event: Mapping[str, Any]) -> str:
    try:
        return super_cli._tool_result_summary(tool_id, event)
    except Exception:
        result = event.get("result") if isinstance(event.get("result"), dict) else {}
        if isinstance(result, dict) and result.get("path"):
            return _clip(result.get("path"), limit=120)
        return _clip(result or event.get("error") or "completed", limit=120)


def _worker_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.replace("super-dan.live.", "")


def _rich_semantic_text(line: str) -> Any:
    from rich.text import Text

    text = Text(str(line or ""), style="grey62")
    plain = text.plain
    label = re.match(r"^(\s*(?:-\s*)?)([A-Z][A-Za-z /_-]*)(:)", plain)
    if label:
        start, end = label.span(2)
        label_style = {
            "Current": "bold cyan",
            "Context": "bold blue",
            "Progress": "bold white",
            "Results": "bold white",
            "Trace": "bold cyan",
            "Validation": "bold green",
            "Changed": "bold green",
            "Artifact": "bold cyan",
            "Blocker": "bold red",
            "Tool started": "bold blue",
            "Tool completed": "bold green",
            "Tool failed": "bold red",
            "Tool denied": "bold red",
            "Model round": "bold cyan",
            "Repair started": "bold yellow",
            "Run": "bold cyan",
            "Selected skills": "bold magenta",
        }.get(label.group(2), "bold white")
        text.stylize(label_style, start, end)

    for match in _RICH_PATH_RE.finditer(plain):
        text.stylize("bold cyan", match.start(), match.end())

    for match in re.finditer(r"(?<!\w)\$[A-Za-z][A-Za-z0-9_-]*", plain):
        text.stylize("bold magenta", match.start(), match.end())

    for term in _RICH_TOOL_TERMS:
        for match in re.finditer(rf"\b{re.escape(term)}\b", plain):
            text.stylize("bold blue", match.start(), match.end())

    for match in re.finditer(r"\b(?:gpt|claude|kimi|gemini|o\d)[A-Za-z0-9_.:-]*", plain, flags=re.IGNORECASE):
        text.stylize("bold cyan", match.start(), match.end())

    for pattern, style in _RICH_STATUS_STYLES:
        for match in re.finditer(pattern, plain, flags=re.IGNORECASE):
            text.stylize(style, match.start(), match.end())

    return text


@dataclass(frozen=True)
class TuiSkillSuggestion:
    token: str
    name: str
    description: str = ""
    source_scope: str = ""


@dataclass(frozen=True)
class TuiCompletionCandidate:
    value: str
    meta: str
    start_position: int


@dataclass(frozen=True)
class TuiSkillMentionParse:
    objective: str
    selected: tuple[TuiSkillSuggestion, ...] = ()
    unknown_tokens: tuple[str, ...] = ()
    should_run: bool = True
    message: str = ""


@dataclass(frozen=True)
class TuiRunCommand:
    command: str
    payload: str = ""
    message: str = ""


@dataclass(frozen=True)
class TuiSkillPreflightResult:
    token: str
    ok: bool
    notes: tuple[str, ...] = ()
    ran_hook: bool = False


def _skill_token(value: Mapping[str, Any]) -> str:
    return skill_invocation.skill_token(value)


def _load_tui_skill_suggestions(workspace_root: Path, *, limit: int = 80) -> list[TuiSkillSuggestion]:
    suggestions: list[TuiSkillSuggestion] = []
    seen: set[str] = set()
    for item in skill_invocation.load_skill_catalog(str(workspace_root)):
        token = _skill_token(item)
        if not token or token in seen:
            continue
        seen.add(token)
        suggestions.append(
            TuiSkillSuggestion(
                token=token,
                name=str(item.get("name") or token),
                description=str(item.get("description") or ""),
                source_scope=str(item.get("source_scope") or ""),
            )
        )
        if len(suggestions) >= limit:
            break
    return suggestions


def _completion_candidates(
    text_before_cursor: str,
    *,
    commands: Sequence[tuple[str, str]] = _TUI_COMMANDS,
    skills: Sequence[TuiSkillSuggestion] = (),
    limit: int = 12,
) -> list[TuiCompletionCandidate]:
    text = str(text_before_cursor or "")
    if text.startswith("/") and not re.search(r"\s", text):
        prefix = text.lower()
        rows = [
            TuiCompletionCandidate(command, meta, -len(text))
            for command, meta in commands
            if command.startswith(prefix)
        ]
        return rows[:limit]

    match = re.search(r"(^|\s)(\$|\$[A-Za-z][A-Za-z0-9_-]*)$", text)
    if not match:
        return []
    raw = match.group(2)
    prefix = raw[1:].lower()
    rows: list[TuiCompletionCandidate] = []
    for skill in skills:
        token = skill.token
        if prefix and not token.startswith(prefix):
            continue
        meta = skill.description or skill.name
        if skill.source_scope:
            meta = f"{skill.source_scope}: {meta}"
        rows.append(TuiCompletionCandidate(f"${token}", _clip(meta, limit=90), -len(raw)))
        if len(rows) >= limit:
            break
    return rows


def _parse_tui_run_command(text: str) -> TuiRunCommand | None:
    stripped = str(text or "").strip()
    lowered = stripped.lower()
    for command in ("/append", "/continue", "/pause", "/cancel"):
        if lowered == command or lowered.startswith(command + " "):
            payload = stripped[len(command) :].strip()
            if command == "/append":
                message = (
                    "Append is only available for server-backed V2 active runs. "
                    "This direct local TUI run cannot accept active-run steering yet."
                )
            elif command == "/continue":
                message = (
                    "Continue-after-current is only available for server-backed V2 active runs. "
                    "This direct local TUI run cannot queue continuation work yet."
                )
            elif command == "/pause":
                message = "Pause is not available yet; resumable checkpoint state is still a Plan 57-10 task."
            else:
                message = (
                    "Cancel is not wired into direct local Super DAN runs yet. "
                    "Use Ctrl-C to interrupt this shell, or run through V2 once checkpoint cancellation lands."
                )
            return TuiRunCommand(command=command[1:], payload=payload, message=message)
    return None


def _build_prompt_toolkit_completer(
    *,
    commands: Sequence[tuple[str, str]],
    skills: Sequence[TuiSkillSuggestion],
) -> Any:
    from prompt_toolkit.completion import Completer, Completion

    class SuperTuiCompleter(Completer):
        def get_completions(self, document: Any, complete_event: Any) -> Any:
            del complete_event
            for item in _completion_candidates(
                document.text_before_cursor,
                commands=commands,
                skills=skills,
            ):
                yield Completion(
                    item.value,
                    start_position=item.start_position,
                    display=item.value,
                    display_meta=item.meta,
                )

    return SuperTuiCompleter()


def _build_prompt_toolkit_key_bindings() -> Any:
    from prompt_toolkit.key_binding import KeyBindings

    bindings = KeyBindings()

    @bindings.add("/")
    def _slash(event: Any) -> None:
        event.current_buffer.insert_text("/")
        event.current_buffer.start_completion(select_first=False)

    @bindings.add("$")
    def _dollar(event: Any) -> None:
        event.current_buffer.insert_text("$")
        event.current_buffer.start_completion(select_first=False)

    return bindings


def _build_prompt_toolkit_style() -> Any:
    from prompt_toolkit.styles import Style

    return Style.from_dict(
        {
            "completion-menu.completion": "bg:#252a33 #cbd5e1",
            "completion-menu.completion.current": "bg:#00d1d1 #001014 bold",
            "completion-menu.meta.completion": "bg:#252a33 #9ca3af",
            "completion-menu.meta.completion.current": "bg:#00d1d1 #001014",
            "scrollbar.background": "bg:#252a33",
            "scrollbar.button": "bg:#00d1d1",
        }
    )


def _build_prompt_toolkit_session(
    *,
    commands: Sequence[tuple[str, str]],
    skills: Sequence[TuiSkillSuggestion],
) -> Any:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.shortcuts import CompleteStyle

    return PromptSession(
        completer=_build_prompt_toolkit_completer(commands=commands, skills=skills),
        complete_while_typing=True,
        complete_style=CompleteStyle.COLUMN,
        key_bindings=_build_prompt_toolkit_key_bindings(),
        reserve_space_for_menu=8,
        style=_build_prompt_toolkit_style(),
    )


def _readline_input(
    prompt: str,
    *,
    commands: Sequence[tuple[str, str]],
    skills: Sequence[TuiSkillSuggestion],
) -> str:
    try:
        import readline
    except ImportError:
        return input(prompt)

    old_completer = readline.get_completer()
    old_delims = readline.get_completer_delims()
    matches: list[str] = []

    def complete(text: str, state: int) -> str | None:
        nonlocal matches
        if state == 0:
            line = readline.get_line_buffer()[: readline.get_endidx()]
            candidates = _completion_candidates(line, commands=commands, skills=skills)
            matches = [candidate.value for candidate in candidates]
        try:
            return matches[state]
        except IndexError:
            return None

    readline.set_completer(complete)
    readline.set_completer_delims(" \t\n")
    try:
        readline.parse_and_bind("tab: complete")
        return input(prompt)
    finally:
        readline.set_completer(old_completer)
        readline.set_completer_delims(old_delims)


def _read_interactive_line(
    prompt: str,
    *,
    commands: Sequence[tuple[str, str]],
    skills: Sequence[TuiSkillSuggestion],
) -> str:
    global _PROMPT_TOOLKIT_FALLBACK_WARNED
    if sys.stdin.isatty():
        try:
            session = _build_prompt_toolkit_session(commands=commands, skills=skills)
            return session.prompt(prompt)
        except Exception:
            if not _PROMPT_TOOLKIT_FALLBACK_WARNED:
                print(
                    "Dropdown suggestions unavailable; falling back to Tab completion.",
                    file=sys.stderr,
                )
                _PROMPT_TOOLKIT_FALLBACK_WARNED = True
            return _readline_input(prompt, commands=commands, skills=skills)
    return input(prompt)


def _format_skill_suggestions(skills: Sequence[TuiSkillSuggestion], query: str = "") -> str:
    normalized = query.strip().lower().lstrip("$")
    matches = [
        skill
        for skill in skills
        if not normalized
        or skill.token.startswith(normalized)
        or normalized in skill.name.lower()
        or normalized in skill.description.lower()
    ]
    if not matches:
        return "No matching skills."
    lines = ["Available skill mentions:"]
    for skill in matches[:20]:
        description = f" — {_clip(skill.description, limit=90)}" if skill.description else ""
        lines.append(f"- ${skill.token}{description}")
    if len(matches) > 20:
        lines.append(f"... {len(matches) - 20} more")
    return "\n".join(lines)


def _parse_tui_skill_mentions(text: str, skills: Sequence[TuiSkillSuggestion]) -> TuiSkillMentionParse:
    """Parse leading TUI skill mentions without letting them become the objective."""

    by_token = {skill.token.lower(): skill for skill in skills}
    catalog = [
        {
            "id": skill.token.replace("-", "_"),
            "name": skill.token,
            "description": skill.description,
            "source_scope": skill.source_scope,
            "content": skill.description or skill.name,
        }
        for skill in skills
    ]
    parsed = skill_invocation.parse_skill_invocation_text(
        text,
        catalog=catalog,
        browse_hint="Use /skills to pick an available skill",
    )
    selected = tuple(
        by_token[token]
        for token in parsed.selected_tokens
        if token in by_token
    )
    return TuiSkillMentionParse(
        objective=parsed.objective,
        selected=selected,
        unknown_tokens=parsed.unknown_tokens,
        should_run=parsed.should_run,
        message=parsed.message,
    )


@dataclass
class SuperTuiState:
    """Small projection of Super DAN events for terminal rendering."""

    objective: str = ""
    workspace: str = ""
    task_id: str = ""
    status: str = "idle"
    phase: str = "waiting"
    model: str = ""
    event_log_path: str = ""
    validation: str = ""
    validation_score: str = ""
    active_tool: str = ""
    current_step: str = ""
    queue_status: str = ""
    changed_files: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    progress: list[str] = field(default_factory=list)
    results: list[str] = field(default_factory=list)
    recent: list[str] = field(default_factory=list)

    def _record_progress(self, line: Any, *, limit: int = 12) -> None:
        _append_unique(self.progress, line, limit=limit)

    def _record_result(self, line: Any, *, limit: int = 10) -> None:
        _append_unique(self.results, line, limit=limit)

    def observe(self, event: Mapping[str, Any]) -> str | None:
        name = str(event.get("event") or "").strip()
        if not name:
            return None
        line: str | None = None
        if name == "run.log.started":
            self.status = "running"
            self.phase = "starting"
            self.objective = str(event.get("objective") or self.objective)
            self.workspace = str(event.get("workspace_root") or self.workspace)
            self.task_id = str(event.get("task_id") or self.task_id)
            self.current_step = "Starting Super DAN run"
            run_ref = f" {self.task_id}" if self.task_id else ""
            line = f"Started run{run_ref}: {_clip(self.objective, limit=120)}"
            self._record_progress(line)
            selected = [
                str(token).strip().lstrip("$")
                for token in (event.get("selected_skill_mentions") or [])
                if str(token).strip()
            ]
            if selected:
                self._record_progress("Selected skills: " + ", ".join(f"${token}" for token in selected))
        elif name in {"skill.preflight.completed", "skill.preflight.failed"}:
            failed = name.endswith("failed")
            self.phase = "skill setup" if not failed else "skill setup failed"
            note = _clip(event.get("note") or name, limit=160)
            self.current_step = note
            line = note
            self._record_progress(line)
            if failed:
                self._record_result(line)
        elif name in {"provider.build.started", "provider.build.completed"}:
            self.phase = "model setup"
            self.model = str(event.get("model") or event.get("requested_model") or self.model)
            self.current_step = "Preparing model provider"
            line = f"Configured model provider: {self.model or 'default model'}"
            self._record_progress(line)
        elif name in {"live.generic_execution.started", "live.generic_build.started"}:
            self.phase = "building"
            self.workspace = str(event.get("workspace_root") or self.workspace)
            self.current_step = "Builder is editing the workspace"
            line = f"Build lane started in {_clip(self.workspace or 'workspace', limit=120)}"
            self._record_progress(line)
        elif name == "live.planning.started":
            self.phase = "planning"
            self.current_step = "Planning the run"
            line = f"Planning started: {_clip(event.get('plan_root') or 'run-local plan')}"
            self._record_progress(line)
        elif name == "live.planning.completed":
            self.phase = "planning complete"
            count = event.get("plan_file_count", 0)
            self.current_step = "Planning complete"
            line = f"Planning complete: {count} plan file(s)"
            self._record_progress(line)
            if count:
                self._record_result(f"Plan files created/updated: {count}")
        elif name == "model.requested":
            self.phase = "model"
            self.model = str(event.get("model") or self.model)
            worker = _worker_label(event.get("worker_id"))
            scope = f" ({worker})" if worker else ""
            tools = event.get("tool_count", 0)
            self.current_step = f"Waiting for model round {event.get('round', '?')}{scope}"
            line = f"Model round {event.get('round', '?')}{scope}: {self.model or 'unknown'} with {tools} tool(s)"
            self._record_progress(line)
        elif name == "model.responded":
            self.phase = "model response"
            tool_calls = [
                str(item).strip()
                for item in (event.get("tool_calls") or [])
                if str(item).strip()
            ]
            if tool_calls:
                self.current_step = "Model requested tools: " + ", ".join(tool_calls[:4])
                detail = "tool calls -> " + ", ".join(tool_calls[:4])
            else:
                self.current_step = "Model returned a response"
                detail = f"finish={event.get('finish_reason') or 'stop'}"
            line = f"Model round {event.get('round', '?')} responded: {detail}"
            self._record_progress(line)
        elif name == "tool.started":
            self.phase = "tool"
            tool_id = str(event.get("tool_id") or "tool")
            self.active_tool = tool_id
            summary = _tool_request_summary(tool_id, dict(event.get("arguments") or {}))
            self.current_step = f"Running {tool_id}: {summary}"
            line = f"Tool started: {tool_id} - {summary}"
            self._record_progress(line)
        elif name == "tool.completed":
            tool_id = str(event.get("tool_id") or "tool")
            self.active_tool = ""
            result = event.get("result") if isinstance(event.get("result"), dict) else {}
            path = result.get("path") if isinstance(result, dict) else None
            if tool_id in {"file_write", "file_edit"} and path:
                _append_unique(self.changed_files, str(path))
                self._record_result(f"Changed file: {path}")
            summary = _tool_result_summary(tool_id, event)
            self.current_step = f"Completed {tool_id}: {summary}"
            line = f"Tool completed: {tool_id} - {summary}"
            self._record_progress(line)
        elif name in {"tool.failed", "tool.denied"}:
            self.phase = "tool blocked"
            tool_id = str(event.get("tool_id") or "tool")
            message = _clip(event.get("error") or name, limit=160)
            self.current_step = f"Blocked on {tool_id}"
            line = f"Tool {name.split('.')[-1]}: {tool_id} - {message}"
            self._record_progress(line)
            self._record_result(line)
        elif name == "live.validation.started":
            self.phase = "validation"
            self.validation = "running"
            self.current_step = "Validator is checking the result"
            line = "Validation started: checking workspace changes against the objective"
            self._record_progress(line)
        elif name in {"live.validation.model_completed", "live.validation.completed"}:
            passed = bool(event.get("passed"))
            self.phase = "validation"
            self.validation = "passed" if passed else "failed"
            try:
                self.validation_score = f"{float(event.get('overall_score')):.2f}"
            except (TypeError, ValueError):
                self.validation_score = ""
            for item in event.get("deterministic_failures") or []:
                _append_unique(self.blockers, str(item), limit=5)
            suffix = f" {self.validation_score}" if self.validation_score else ""
            self.current_step = f"Validation {self.validation}{suffix}".strip()
            line = f"Validation {self.validation}{suffix}".strip()
            self._record_progress(line)
            self._record_result(line)
            for item in event.get("deterministic_failures") or []:
                self._record_result(f"Validation gap: {_clip(item, limit=140)}")
        elif name in {"live.builder_retry.started", "live.website_repair.started", "live.generic_repair.started"}:
            self.phase = "repair"
            reason = _clip(event.get("reason") or name, limit=140)
            self.current_step = f"Repairing: {reason}"
            line = f"Repair started: {reason}"
            self._record_progress(line)
        elif name in {"live.builder_retry.completed", "live.website_repair.completed", "live.generic_repair.completed"}:
            self.phase = "repair complete"
            changed = [
                str(path)
                for path in (event.get("changed_required_files") or [])
                if str(path).strip()
            ]
            suffix = f" changed={', '.join(changed[:3])}" if changed else ""
            self.current_step = "Repair complete"
            line = f"Repair {event.get('status') or 'completed'}{suffix}"
            self._record_progress(line)
            if changed:
                self._record_result(line)
        elif name == "super.hook.packet_enqueued":
            inbox = str(event.get("inbox_id") or "inbox")
            depth = event.get("queue_depth", "?")
            self.queue_status = f"{inbox} depth={depth}"
            self.current_step = f"Queued follow-up work for {inbox}"
            line = f"Queue update: {inbox} has depth {depth}"
            self._record_progress(line)
        elif name in {"run.log.completed", "run.log.failed"}:
            self.status = str(event.get("status") or ("failed" if name.endswith("failed") else "completed"))
            self.phase = "done" if self.status == "completed" else "failed"
            self.event_log_path = str(event.get("event_log_path") or self.event_log_path)
            self.current_step = f"Run {self.status}"
            line = f"Run {self.status}"
            self._record_progress(line)
            self._record_result(line)
        elif name == "super.heartbeat":
            self.phase = str(event.get("phase") or self.phase or "running")
            detail = _clip(event.get("detail") or "", limit=120)
            suffix = f" - {detail}" if detail else ""
            self.current_step = f"Still running: {self.phase}{suffix}"
            line = self.current_step
            self._record_progress(line)
        if line:
            _append_unique(self.recent, line, limit=8)
        return line

    def apply_live_result(self, live_result: Mapping[str, Any]) -> None:
        self.status = str(live_result.get("status") or self.status or "unknown")
        self.phase = "done" if self.status == "completed" else "failed"
        self.event_log_path = str(live_result.get("event_log_path") or self.event_log_path)
        for path in live_result.get("files") or []:
            _append_unique(self.changed_files, str(path))
            _append_unique(self.artifacts, str(path))
        website = str(live_result.get("website") or "").strip()
        if website:
            _append_unique(self.artifacts, website)
        validation = live_result.get("validation") if isinstance(live_result.get("validation"), dict) else {}
        if validation:
            self.validation = "passed" if validation.get("passed") else "failed"
            try:
                self.validation_score = f"{float(validation.get('overall_score')):.2f}"
            except (TypeError, ValueError):
                self.validation_score = ""
        score = f" ({self.validation_score})" if self.validation_score else ""
        self.current_step = f"Final result: {self.status}"
        self._record_result(f"Final result: {self.status}{score}", limit=10)
        _append_unique(self.recent, f"Final result: {self.status}", limit=8)

    def plain_snapshot(self) -> str:
        lines = [
            "Super DAN TUI",
            f"Status: {self.status}",
            f"Phase: {self.phase}",
        ]
        if self.task_id:
            lines.append(f"Run: {self.task_id}")
        if self.objective:
            lines.append(f"Objective: {_clip(self.objective, limit=140)}")
        if self.workspace:
            lines.append(f"Workspace: {self.workspace}")
        if self.model:
            lines.append(f"Model: {self.model}")
        if self.validation:
            score = f" ({self.validation_score})" if self.validation_score else ""
            lines.append(f"Validation: {self.validation}{score}")
        if self.queue_status:
            lines.append(f"Queue: {self.queue_status}")
        if self.changed_files:
            lines.append("Changed:")
            lines.extend(f"- {path}" for path in self.changed_files[-6:])
        if self.artifacts:
            lines.append("Artifacts:")
            lines.extend(f"- {path}" for path in self.artifacts[-6:])
        if self.blockers:
            lines.append("Blockers:")
            lines.extend(f"- {_clip(item, limit=120)}" for item in self.blockers[-4:])
        if self.event_log_path:
            lines.append(f"Event Log: {self.event_log_path}")
        return "\n".join(lines)

    def recent_event_lines(self) -> list[str]:
        lines: list[str] = []
        if self.current_step:
            lines.append(f"Current: {self.current_step}")
        if self.objective:
            lines.append(f"Context: objective={_clip(self.objective, limit=120)}")
        if self.task_id:
            lines.append(f"Context: run={self.task_id}")
        if self.workspace:
            lines.append(f"Context: workspace={self.workspace}")
        if self.model:
            lines.append(f"Context: model={self.model}")
        if self.queue_status:
            lines.append(f"Context: queue={self.queue_status}")
        if self.progress:
            lines.append("Progress:")
            lines.extend(f"  - {item}" for item in self.progress[-9:])
        result_lines: list[str] = []
        if self.validation:
            score = f" ({self.validation_score})" if self.validation_score else ""
            result_lines.append(f"Validation: {self.validation}{score}")
        for path in self.changed_files[-4:]:
            result_lines.append(f"Changed: {path}")
        for path in self.artifacts[-4:]:
            result_lines.append(f"Artifact: {path}")
        for item in self.blockers[-4:]:
            result_lines.append(f"Blocker: {_clip(item, limit=120)}")
        result_lines.extend(self.results[-6:])
        if result_lines:
            lines.append("Results:")
            for item in result_lines[-9:]:
                lines.append(f"  - {item}")
        if self.event_log_path:
            lines.append(f"Trace: {self.event_log_path}")
        return lines[-28:] or ["Waiting for events."]

    def rich_renderable(self) -> Any:
        from rich.console import Group
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        status_style = {
            "completed": "bold green",
            "failed": "bold red",
            "blocked": "bold red",
            "running": "bold cyan",
            "idle": "dim",
        }.get(self.status, "bold cyan")
        header = Table.grid(expand=True)
        header.add_column(ratio=1)
        header.add_column(justify="right")
        title = Text("Super DAN TUI", style="bold cyan")
        status = Text(f"{self.status} / {self.phase}", style=status_style)
        header.add_row(title, status)

        recent = Table.grid(expand=True)
        recent.add_column()
        for line in self.recent_event_lines():
            recent.add_row(_rich_semantic_text(line))

        return Group(
            Panel(header, border_style="cyan"),
            Panel(recent, title="Recent Events", border_style="magenta"),
        )


class SuperTuiProgressRenderer:
    """Render Super DAN events as a terminal UI."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        objective: str = "",
        workspace: str = "",
        plain: bool = False,
        force_rich: bool = False,
    ) -> None:
        self.enabled = bool(enabled)
        self.state = SuperTuiState(objective=objective, workspace=workspace)
        self._plain = bool(plain)
        self._force_rich = bool(force_rich)
        self._console = None
        self._live = None
        self._live_cls = None
        self._rich_enabled = False
        self._last_line = ""

    def __enter__(self) -> "SuperTuiProgressRenderer":
        if not self.enabled:
            return self
        if not self._plain:
            Console, _ = _try_import_rich()
            if Console is not None:
                try:
                    from rich.live import Live

                    self._console = Console()
                    self._live_cls = Live
                    self._rich_enabled = self._force_rich or bool(getattr(self._console, "is_terminal", False))
                except ImportError:
                    self._rich_enabled = False
        if self._rich_enabled and self._live_cls is not None and self._console is not None:
            self._live = self._live_cls(
                self.state.rich_renderable(),
                console=self._console,
                refresh_per_second=8,
                transient=False,
            )
            self._live.start()
        else:
            print("Super DAN TUI", flush=True)
            if self.state.objective:
                print(f"objective: {_clip(self.state.objective, limit=140)}", flush=True)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None

    def __call__(self, event: dict[str, Any]) -> None:
        if not self.enabled:
            return
        line = self.state.observe(event)
        if self._rich_enabled and self._live is not None:
            self._live.update(self.state.rich_renderable())
            return
        if line and line != self._last_line:
            self._last_line = line
            print(f"[tui] {line}", flush=True)

    def note(self, line: str) -> None:
        text = str(line or "").strip()
        if not self.enabled or not text:
            return
        _append_unique(self.state.recent, text, limit=8)
        if self._rich_enabled and self._live is not None:
            self._live.update(self.state.rich_renderable())
            return
        if text != self._last_line:
            self._last_line = text
            print(f"[tui] {text}", flush=True)

    def print_live_report(self, report: Any, live_result: dict[str, Any], *, verbose: bool = False) -> None:
        del report, verbose
        self.state.apply_live_result(live_result)
        if self._rich_enabled and self._live is not None:
            self._live.update(self.state.rich_renderable())
            return
        print(self.state.plain_snapshot(), flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = super_cli.build_parser()
    parser.prog = "dan-super-tui"
    parser.description = (
        "Run Super DAN through a terminal UI. This is a sibling surface to "
        "dan-super-organism and reuses the same live runner and event logs."
    )
    parser.epilog = (
        "Interactive commands: /plan, /status, /skills, /reset [all|state], /help, /exit. "
        "Typing / opens command suggestions and typing $ opens skill suggestions when prompt_toolkit is available. "
        "Planned active-run commands /append, /continue, /pause, and /cancel "
        "are shown in the TUI roadmap but require Plan 57-10 before they steer a live run."
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="Use deterministic plain terminal output instead of Rich panels.",
    )
    parser.add_argument(
        "--status",
        dest="queue_status",
        action="store_true",
        help="Alias for --queue-status.",
    )
    parser.add_argument(
        "--event-log",
        help="Render a static TUI projection from an existing Super DAN events.jsonl file.",
    )
    return parser


def _argv_has_option(argv: Sequence[str], option: str) -> bool:
    return option in set(argv)


def _prepare_args(args: argparse.Namespace, raw_argv: Sequence[str]) -> None:
    setattr(args, "_live_explicit", _argv_has_option(raw_argv, "--live"))
    setattr(args, "_model_explicit", _argv_has_option(raw_argv, "--model"))
    setattr(args, "_artifact_dir_explicit", _argv_has_option(raw_argv, "--artifact-dir"))
    setattr(args, "_implicit_live", False)
    setattr(args, "_code_like_live", False)
    setattr(args, "_stdin_is_tty", sys.stdin.isatty())


def _run_tui_turn(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    *,
    force_live: bool = False,
) -> int:
    if force_live and not bool(getattr(args, "plan_only", False)):
        args.live = True
        args._code_like_live = True
    workspace = str(normalize_workspace_root(str(args.workspace)))
    renderer = SuperTuiProgressRenderer(
        enabled=not bool(getattr(args, "json", False)) and not bool(getattr(args, "quiet_progress", False)),
        objective=str(getattr(args, "target", "") or ""),
        workspace=workspace,
        plain=bool(getattr(args, "plain", False)),
    )
    for token in getattr(args, "_tui_selected_skill_mentions", []) or []:
        _append_unique(renderer.state.recent, f"skill selected: ${token}", limit=8)

    def _renderer_factory(*, enabled: bool, args: argparse.Namespace) -> SuperTuiProgressRenderer:
        del enabled, args
        return renderer

    args._progress_renderer_factory = _renderer_factory
    args._live_report_printer = renderer.print_live_report
    with renderer:
        return super_cli._run_super_turn(args, parser)


def _prepare_target_skill_mentions(
    args: argparse.Namespace,
    *,
    skills: Sequence[TuiSkillSuggestion] | None = None,
) -> TuiSkillMentionParse:
    workspace_root = normalize_workspace_root(str(args.workspace))
    available = list(skills) if skills is not None else _load_tui_skill_suggestions(workspace_root)
    parsed = _parse_tui_skill_mentions(str(getattr(args, "target", "") or ""), available)
    if parsed.selected:
        selected = [skill.token for skill in parsed.selected]
        setattr(args, "_selected_skill_mentions", selected)
        setattr(args, "selected_skill_mentions", selected)
        setattr(args, "_selected_skill_source", "tui")
        setattr(args, "_tui_selected_skill_mentions", selected)
    if parsed.should_run:
        args.target = parsed.objective
    return parsed


def _selected_skill_catalog_items(
    workspace_root: Path,
    tokens: Sequence[str],
) -> list[dict[str, Any]]:
    return skill_invocation.selected_catalog_items(
        skill_invocation.load_skill_catalog(str(workspace_root)),
        tokens,
    )


def _selected_skill_script_candidates(skill: Mapping[str, Any]) -> list[str]:
    return skill_invocation.selected_skill_script_candidates(skill)


def _selected_skill_preflight_script(skill: Mapping[str, Any]) -> Path | None:
    return skill_invocation.selected_skill_preflight_script(skill)


def _run_skill_preflight(
    *,
    workspace_root: Path,
    skill: Mapping[str, Any],
    objective: str,
) -> TuiSkillPreflightResult:
    result = skill_invocation.run_skill_preflight(
        workspace_root=workspace_root,
        skill=skill,
        objective=objective,
    )
    return TuiSkillPreflightResult(
        token=result.token,
        ok=result.ok,
        notes=result.notes,
        ran_hook=result.ran_hook,
    )


def _run_selected_skill_preflights(
    args: argparse.Namespace,
    *,
    workspace_root: Path,
) -> tuple[bool, list[str]]:
    if bool(getattr(args, "plan_only", False)):
        return True, []
    if not bool(getattr(args, "live", False)):
        return True, []
    tokens = list(getattr(args, "_tui_selected_skill_mentions", []) or [])
    if not tokens:
        return True, []
    ok, notes = skill_invocation.run_skill_preflights_for_tokens(
        workspace_root=workspace_root,
        tokens=tokens,
        objective=str(getattr(args, "target", "") or ""),
        catalog=skill_invocation.load_skill_catalog(str(workspace_root)),
    )
    if notes:
        setattr(args, "_tui_skill_preflight_notes", notes)
        setattr(args, "_selected_skill_preflight_notes", notes)
        setattr(args, "selected_skill_preflight", notes)
    return ok, notes


def _read_event_log(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _render_static_state(state: SuperTuiState, *, plain: bool) -> None:
    if not plain:
        Console, _ = _try_import_rich()
        if Console is not None:
            try:
                console = Console()
                console.print(state.rich_renderable())
                return
            except Exception:
                pass
    print(state.plain_snapshot())


def _render_composer_hint(workspace_root: Path, *, plain: bool, skill_count: int) -> None:
    if not plain:
        Console, _ = _try_import_rich()
        if Console is not None:
            try:
                from rich.panel import Panel
                from rich.text import Text

                body = Text()
                body.append("Type your objective in the prompt below.\n", style="bold")
                body.append(f"Workspace: {workspace_root}\n", style="dim")
                body.append("Commands: /plan, /status, /skills, /reset, /help, /exit\n", style="cyan")
                body.append(f"Skill suggestions: {skill_count} loaded", style="green" if skill_count else "dim")
                console = Console()
                console.print(Panel(body, title="Message", border_style="cyan", padding=(1, 2)))
                console.print(
                    "  Use / for commands and $ for skills. Enter submits.",
                    style="dim on #20242c",
                )
                return
            except Exception:
                pass
    print("Message", flush=True)
    print("  Type your objective in the prompt below.", flush=True)
    print(f"  Workspace: {workspace_root}", flush=True)
    print("  Commands: /plan, /status, /skills, /reset, /help, /exit", flush=True)
    print(f"  Skill suggestions: {skill_count} loaded", flush=True)
    print("  Use / for commands and $ for skills. Enter submits.", flush=True)


def _render_event_log(args: argparse.Namespace) -> int:
    path = Path(str(args.event_log)).expanduser()
    if not path.exists():
        print(f"event log not found: {path}", file=sys.stderr)
        return 2
    state = SuperTuiState(
        workspace=str(normalize_workspace_root(str(args.workspace))),
    )
    try:
        for row in _read_event_log(path):
            state.observe(row)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"could not read event log: {exc}", file=sys.stderr)
        return 2
    if not state.event_log_path:
        state.event_log_path = str(path)
    _render_static_state(state, plain=bool(getattr(args, "plain", False)))
    return 0


def _interactive_loop(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    workspace_root = normalize_workspace_root(str(args.workspace))
    commands = _TUI_COMMANDS
    skills = _load_tui_skill_suggestions(workspace_root)
    _render_composer_hint(
        workspace_root,
        plain=bool(getattr(args, "plain", False)),
        skill_count=len(skills),
    )
    while True:
        try:
            text = _read_interactive_line(
                "super-tui> ",
                commands=commands,
                skills=skills,
            )
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
            print(format_super_queue_status(workspace_root), flush=True)
            continue
        if lowered in {"/help", "help"}:
            print(
                "Commands: /plan <objective>, /status, /skills [filter], /reset [all|state], /exit. "
                "Type / for command suggestions and $ for skill suggestions when prompt_toolkit is available. "
                "/append, /continue, /pause, and /cancel are planned for active-run steering.",
                flush=True,
            )
            continue
        if lowered == "/skills" or lowered.startswith("/skills "):
            query = objective.split(maxsplit=1)[1] if " " in objective else ""
            print(_format_skill_suggestions(skills, query), flush=True)
            continue
        command = _parse_tui_run_command(objective)
        if command is not None:
            print(command.message, flush=True)
            continue
        if lowered in {"/reset", "reset", "/clear", "clear"} or lowered.startswith(
            ("/reset ", "reset ", "/clear ", "clear ")
        ):
            reset_scope = objective.split(maxsplit=1)[1] if " " in objective else ""
            print(super_cli._reset_super_context(workspace_root, reset_scope), flush=True)
            continue
        plan_only = False
        if lowered.startswith("/plan "):
            objective = objective[6:].strip()
            plan_only = True
        if not objective:
            continue
        turn_args = copy.copy(args)
        turn_args.target = objective
        parsed = _prepare_target_skill_mentions(turn_args, skills=skills)
        if parsed.message:
            print(parsed.message, flush=True)
        if not parsed.should_run:
            continue
        if not str(turn_args.target or "").strip():
            continue
        turn_args.plan_only = plan_only
        turn_args.json = False
        turn_args.output = None
        try:
            exit_code = _run_tui_turn(turn_args, parser, force_live=not plan_only)
        except SystemExit as exc:
            exit_code = int(exc.code or 0) if isinstance(exc.code, int) else 2
        if exit_code not in {0, 1}:
            return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    load_env()
    parser = build_parser()
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(raw_argv)
    _prepare_args(args, raw_argv)

    if str(getattr(args, "event_log", "") or "").strip():
        return _render_event_log(args)

    no_objective = not str(getattr(args, "target", "") or "").strip()
    if no_objective and bool(getattr(args, "queue_status", False)):
        workspace_root = normalize_workspace_root(str(args.workspace))
        print(format_super_queue_status(workspace_root))
        return 0
    report_mode_requested = any(
        bool(getattr(args, field, False))
        for field in ("plan_only", "json", "verbose")
    ) or bool(getattr(args, "output", None))
    if no_objective and not report_mode_requested and sys.stdin.isatty():
        return _interactive_loop(args, parser)
    if no_objective and not report_mode_requested:
        parser.error("objective required in non-interactive TUI mode")
    parsed = _prepare_target_skill_mentions(args)
    if parsed.message and not bool(getattr(args, "json", False)):
        print(parsed.message)
    if not parsed.should_run:
        return 0
    return _run_tui_turn(args, parser)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
