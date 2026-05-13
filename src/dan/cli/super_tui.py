"""Terminal UI for Super DAN live runs.

This module is intentionally a sibling surface to ``dan super-organism``.
It reuses the Super DAN runner and event log, but owns terminal rendering.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import re
import shlex
import shutil
import sys
import textwrap
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from dan.agent_runtime.progress_narrator import (
    NARRATOR_READ_ONLY,
    NarratorRequest,
    NarratorReport,
    RunNarratorSnapshot,
    TranscriptRef,
    classify_agent_turn_intent,
    deterministic_narrator_report,
    deterministic_narrator_response,
    generate_narrator_response,
    route_agent_turn_intent_with_model,
    start_narrator_job,
    tokenize_intent_text,
)
from dan.cli import _try_import_rich, load_env, normalize_workspace_root
from dan.cli.super_hooks import format_super_queue_status
from dan.cli import super_organism as super_cli
from dan.skills import invocation as skill_invocation
from dan.worker.core.interfaces import CompletionRequest
from dan.worker.organisms.local_runtime import (
    LocalOrganismToolRuntime,
    ToolLoopCompletionProvider,
    available_local_organism_tools,
)


_TUI_COMMANDS: tuple[tuple[str, str], ...] = (
    ("/plan", "show the deterministic contract for an objective"),
    ("/progress", "narrate current or recent run progress"),
    ("/last", "narrate the last visible run state"),
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
    (r"\b(?:running|started|starting|planning|model|validation|builder|workspace|read-only|write)\b", "bold cyan"),
    (r"\b(?:waiting|queued|queue|repair|retry|pending|fallback|still)\b", "bold yellow"),
)
_TUI_STREAM_PREFIX = "dan:"
_TUI_STREAM_PREFIX_RE = re.compile(r"^(?:\[tui\]|dan:)")
_ANSWER_LINE_LIMIT = 4000
_ANSWER_LINE_COUNT_LIMIT = 80
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


def _tui_list_item(value: Any, *, indent: str = "") -> str:
    text = str(value or "").rstrip()
    if text.startswith("- "):
        return f"{indent}  {text}"
    return f"{indent}- {text}"


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


def _workspace_context_result_summary(tool_id: str, event: Mapping[str, Any]) -> str:
    result = event.get("result") if isinstance(event.get("result"), dict) else {}
    arguments = event.get("arguments") if isinstance(event.get("arguments"), dict) else {}
    if tool_id == "workspace_check" and isinstance(result, dict):
        check = str(result.get("check") or arguments.get("check") or "check").strip()
        passed = result.get("passed")
        status = "passed" if passed is True else "failed" if passed is False else "completed"
        result_items = result.get("results")
        path = ""
        if isinstance(result_items, list):
            for item in result_items:
                if isinstance(item, dict) and item.get("path"):
                    path = str(item.get("path") or "").strip()
                    break
        if not path:
            path = str(result.get("path") or arguments.get("path") or "").strip()
        if path:
            return f"{check} {status}: {path}"
        return f"{check} {status}"
    path = ""
    if isinstance(result, dict):
        path = str(result.get("path") or "").strip()
    if not path and isinstance(arguments, dict):
        path = str(arguments.get("path") or "").strip()
    details: list[str] = []
    if isinstance(result, dict):
        for key, label in (
            ("line_count", "lines"),
            ("lines", "lines"),
            ("entry_count", "entries"),
            ("entries", "entries"),
            ("byte_count", "bytes"),
            ("bytes", "bytes"),
        ):
            value = result.get(key)
            if value not in (None, ""):
                details.append(f"{label}={value}")
                break
    if path and details:
        return f"{path} ({', '.join(details)})"
    if path:
        return path
    return _tool_result_summary(tool_id, event)


def _worker_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.replace("super-dan.live.", "")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _format_elapsed_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _shell_command_intent(command: Any) -> str:
    text = str(command or "").strip()
    lowered = f" {text.lower()} "
    if re.search(r"(^|[\s;&|])cp\s", text) or " rsync " in lowered:
        return "Copying files with shell"
    if re.search(r"(^|[\s;&|])mkdir\s", text):
        return "Preparing folders with shell"
    if re.search(r"(^|[\s;&|])(ls|find|du|wc|shasum|sha256sum)\s", text):
        return "Verifying files with shell"
    if re.search(r"(^|[\s;&|])(pytest|npm|pnpm|yarn|uv|python|python3|cargo|go)\s", text):
        return "Running a project command"
    return "Running a shell command"


def _human_progress_phase(phase: Any) -> str:
    text = str(phase or "").strip().lower()
    if not text:
        return "the task"
    if "model" in text:
        return "the next step"
    if "tool" in text:
        return "the current action"
    if "valid" in text:
        return "validation"
    if "repair" in text or "retry" in text:
        return "the repair pass"
    if "plan" in text:
        return "the plan"
    if "build" in text or "workspace" in text:
        return "the workspace changes"
    return text.replace("_", " ")


def _narrator_trigger_for_event(event_name: str, event: Mapping[str, Any]) -> str:
    name = str(event_name or "").strip()
    if not name or name.startswith("narrator."):
        return ""
    if name == "run.log.started":
        return "opening"
    if name in {"model.requested", "tool.started"}:
        return "progress"
    if name == "tool.completed":
        return "checkpoint" if str(event.get("tool_id") or "") in {"file_write", "file_edit"} else "progress"
    if name in {
        "live.validation.started",
        "live.validation.completed",
        "super.hook.packet_enqueued",
    }:
        return "checkpoint"
    if name in {"tool.failed", "tool.denied"}:
        return "blocker"
    if name in {"live.builder_retry.started", "live.website_repair.started", "live.generic_repair.started"}:
        return "checkpoint"
    if name == "super.heartbeat":
        return "heartbeat"
    if name in {"run.log.completed", "run.log.failed"}:
        return "final"
    return ""


def _is_relative_to_path(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _raw_event_line(event: Mapping[str, Any]) -> str:
    name = str(event.get("event") or "").strip()
    if not name:
        return ""
    bits = [name]
    for key in ("tool_id", "status", "phase", "round", "worker_id", "task_id"):
        value = event.get(key)
        if value not in (None, ""):
            bits.append(f"{key}={_clip(value, limit=80)}")
    arguments = event.get("arguments") if isinstance(event.get("arguments"), dict) else {}
    result = event.get("result") if isinstance(event.get("result"), dict) else {}
    path = arguments.get("path") or result.get("path") if isinstance(arguments, dict) else None
    if path:
        bits.append(f"path={_clip(path, limit=100)}")
    return " | ".join(bits)


def _tui_transcript_path(workspace_root: Path) -> Path:
    return workspace_root / ".dan-super" / "tui" / "transcript.jsonl"


@dataclass(frozen=True)
class TuiTranscriptEntry:
    role: str
    text: str
    created_at: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


def _append_tui_transcript_entry(
    workspace_root: Path,
    *,
    role: str,
    text: str,
    metadata: Mapping[str, Any] | None = None,
) -> Path | None:
    clean_text = str(text or "").strip()
    clean_role = str(role or "").strip() or "system_notice"
    if not clean_text:
        return None
    path = _tui_transcript_path(workspace_root)
    row = {
        "created_at": _now_iso(),
        "role": clean_role,
        "text": clean_text,
        "metadata": dict(metadata or {}),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n")
    except OSError:
        return None
    return path


def _read_tui_transcript(workspace_root: Path, *, limit: int = 24) -> list[TuiTranscriptEntry]:
    path = _tui_transcript_path(workspace_root)
    if not path.exists():
        return []
    rows: list[TuiTranscriptEntry] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines[-max(1, limit) :]:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        rows.append(
            TuiTranscriptEntry(
                role=str(value.get("role") or "system_notice"),
                text=str(value.get("text") or ""),
                created_at=str(value.get("created_at") or ""),
                metadata=value.get("metadata") if isinstance(value.get("metadata"), dict) else {},
            )
        )
    return rows


def _format_transcript_line(entry: TuiTranscriptEntry) -> str:
    role = entry.role
    label = {
        "user": "user",
        "assistant_progress": "assistant",
        "assistant_final": "assistant",
        "assistant_narrator": "assistant",
        "system_notice": "system",
        "debug_ref": "trace",
    }.get(role, role or "system")
    raw_text = str(entry.text or "").strip()
    if role in {"assistant_final", "assistant_narrator"}:
        text = raw_text
    elif role == "user":
        text = _clip(raw_text, limit=600)
    else:
        text = _clip(raw_text, limit=240)
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if not lines:
        return f"{label}:"
    if len(lines) == 1:
        return f"{label}: {lines[0]}"
    return "\n".join([f"{label}: {lines[0]}"] + [f"  {line}" for line in lines[1:]])


def _stream_line_without_prefix(line: str) -> str:
    text = str(line or "").strip()
    if text.startswith(_TUI_STREAM_PREFIX):
        return text[len(_TUI_STREAM_PREFIX) :].strip()
    if text.startswith("[tui]"):
        return text[len("[tui]") :].strip()
    return text


def _tui_line_base_style(line: str) -> str:
    text = _stream_line_without_prefix(line)
    lowered = text.lower()
    if not text:
        return "grey50"
    if text.startswith(("- ", "  - ")):
        return "white"
    important_prefixes = (
        "Answer:",
        "Result:",
        "Results:",
        "Changed:",
        "Artifact:",
        "Validation:",
        "Blocker:",
        "Source:",
    )
    if text.startswith(important_prefixes):
        return "white"
    progress_prefixes = (
        "Activity:",
        "Context:",
        "Current:",
        "Mode:",
        "Narrator:",
        "Progress:",
        "Queue:",
        "Run:",
        "Selected skills:",
        "Tool started:",
        "Model round:",
        "Repair started:",
        "Working:",
        "Elapsed:",
    )
    if text.startswith(progress_prefixes):
        return "grey50"
    progress_starts = (
        "checking ",
        "read-only review",
        "review started",
        "thinking through",
        "relevant context",
        "still working",
        "the executor",
        "the run is",
    )
    if lowered.startswith(progress_starts):
        return "grey50"
    return "grey62"


def _dim_rich_style(style: str) -> str:
    if "red" in style:
        return style
    if "green" in style:
        return "green"
    if "yellow" in style:
        return "dark_orange"
    if "cyan" in style:
        return "dark_cyan"
    if "blue" in style:
        return "blue"
    if "magenta" in style:
        return "magenta"
    if "white" in style:
        return "grey70"
    return style


def _rich_semantic_text(line: str, *, base_style: str | None = None) -> Any:
    from rich.text import Text

    resolved_base_style = base_style or _tui_line_base_style(str(line or ""))
    is_low_importance = resolved_base_style in {"grey50", "dim", "dim white"}
    text = Text(str(line or ""), style=resolved_base_style)
    plain = text.plain
    tui_prefix = _TUI_STREAM_PREFIX_RE.match(plain)
    if tui_prefix:
        text.stylize("cyan" if is_low_importance else "bold cyan", tui_prefix.start(), tui_prefix.end())
    label = re.match(
        r"^(\s*(?:(?:\[tui\]|dan:)\s*)?(?:-\s*)?)([A-Z][A-Za-z /_-]*)(:)",
        plain,
    )
    if label:
        start, end = label.span(2)
        label_style = {
            "Current": "bold cyan",
            "Context": "bold blue",
            "Mode": "bold magenta",
            "Activity": "bold white",
            "Result": "bold white",
            "Answer": "bold white",
            "Narrator": "bold magenta",
            "Source": "bold cyan",
            "Progress": "bold white",
            "Results": "bold white",
            "Trace": "bold cyan",
            "You asked": "bold cyan",
            "Working": "bold yellow",
            "Elapsed": "bold green",
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
        if is_low_importance and label.group(2) not in {
            "Answer",
            "Result",
            "Results",
            "Changed",
            "Artifact",
            "Validation",
            "Blocker",
            "Source",
        }:
            label_style = _dim_rich_style(label_style)
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
            text.stylize(_dim_rich_style(style) if is_low_importance else style, match.start(), match.end())

    return text


def _print_tui_stream_line(text: str, *, plain: bool = False) -> None:
    rendered = f"{_TUI_STREAM_PREFIX} {str(text or '').strip()}"
    if plain:
        print(rendered, flush=True)
        return
    Console, _ = _try_import_rich()
    if Console is None:
        print(rendered, flush=True)
        return
    try:
        console = Console(highlight=False)
        console.print(_rich_semantic_text(rendered), soft_wrap=True)
    except Exception:
        print(rendered, flush=True)


def _wrap_tui_stream_lines(lines: Sequence[str], *, width: int | None = None) -> list[str]:
    columns = int(width or shutil.get_terminal_size((120, 24)).columns or 120)
    wrap_width = max(48, columns - 8)
    wrapped: list[str] = []
    for raw_line in lines:
        line = str(raw_line or "").rstrip()
        if not line:
            continue
        if len(line) <= wrap_width:
            wrapped.append(line)
            continue
        prefix_match = re.match(r"^(\s*(?:[-*]\s+|\d+[.)]\s+)?)", line)
        prefix = prefix_match.group(1) if prefix_match else ""
        wrapped.extend(
            textwrap.wrap(
                line,
                width=wrap_width,
                subsequent_indent=" " * len(prefix),
                break_long_words=False,
                break_on_hyphens=False,
            )
            or [line]
        )
    return wrapped


def _print_tui_stream_block(title: str, lines: Sequence[str], *, plain: bool = False) -> None:
    title_text = str(title or "").strip() or "Answer"
    visible_lines = _wrap_tui_stream_lines([str(line or "").rstrip() for line in lines if str(line or "").strip()])
    if not visible_lines:
        return
    if plain:
        print(f"{_TUI_STREAM_PREFIX} {title_text}:", flush=True)
        for line in visible_lines:
            print(f"  {line}", flush=True)
        return
    Console, _ = _try_import_rich()
    if Console is None:
        print(f"{_TUI_STREAM_PREFIX} {title_text}:", flush=True)
        for line in visible_lines:
            print(f"  {line}", flush=True)
        return
    try:
        from rich.panel import Panel

        body = _rich_semantic_text("\n".join(visible_lines), base_style="white")
        console = Console(highlight=False)
        console.print(
            Panel(
                body,
                title=title_text,
                border_style="cyan",
                padding=(0, 1),
            )
        )
    except Exception:
        print(f"{_TUI_STREAM_PREFIX} {title_text}:", flush=True)
        for line in visible_lines:
            print(f"  {line}", flush=True)


def _write_tui_clock_line(footer: str) -> bool:
    text = str(footer or "").strip()
    if not text:
        return False
    sys.stdout.write("\r\x1b[2K" + f"{_TUI_STREAM_PREFIX} {text}")
    sys.stdout.flush()
    return True


def _clear_tui_clock_line(active: bool) -> None:
    if not active:
        return
    sys.stdout.write("\r\x1b[2K")
    sys.stdout.flush()


def _run_with_tui_working_clock(args: argparse.Namespace, callback: Any) -> Any:
    """Run a blocking callback while refreshing one visible Working line."""

    if bool(getattr(args, "json", False)) or bool(getattr(args, "quiet_progress", False)):
        return callback()

    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def _worker() -> None:
        try:
            result["value"] = callback()
        except BaseException as exc:  # pragma: no cover - re-raised in caller thread
            error["value"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    started = time.monotonic()
    last_footer = ""
    clock_active = False
    thread.start()
    while thread.is_alive():
        elapsed_seconds = time.monotonic() - started
        if elapsed_seconds >= 1.0:
            footer = f"Working: {_format_elapsed_duration(elapsed_seconds)}"
            if footer != last_footer:
                clock_active = _write_tui_clock_line(footer) or clock_active
                last_footer = footer
        time.sleep(0.25)
    thread.join()
    _clear_tui_clock_line(clock_active)
    if "value" in error:
        raise error["value"]
    return result.get("value")


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


@dataclass(frozen=True)
class TuiIntentDecision:
    permission: str
    complexity: str
    confidence: float
    rationale: str
    clarification: str = ""

    @property
    def lane(self) -> str:
        if self.needs_clarification:
            return "clarification"
        return f"{self.complexity} {self.permission}"

    @property
    def needs_clarification(self) -> bool:
        return bool(self.clarification)

    @property
    def mode_line(self) -> str:
        if self.needs_clarification:
            return "clarification needed"
        return f"{self.lane} - {self.rationale}"


@dataclass(frozen=True)
class TuiSimpleWritePlan:
    operation: str
    source: str = ""
    destination: str = ""


_READ_ONLY_SOURCE_STOPWORDS = {
    "about",
    "again",
    "check",
    "display",
    "explain",
    "file",
    "files",
    "find",
    "list",
    "open",
    "please",
    "read",
    "review",
    "show",
    "summarize",
    "summary",
    "tell",
    "the",
    "this",
    "view",
    "what",
    "where",
    "which",
    "workspace",
}
_READ_ONLY_FILE_SUFFIXES = {
    ".md",
    ".txt",
    ".rst",
    ".json",
    ".jsonl",
    ".toml",
    ".yaml",
    ".yml",
    ".csv",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".html",
    ".css",
}
_CODE_REVIEW_FILE_SUFFIXES = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".html",
    ".css",
    ".sh",
    ".bash",
    ".zsh",
    ".sql",
    ".r",
    ".jl",
}
_READ_ONLY_SKIP_PARTS = {
    ".git",
    ".dan-super",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
}
_TUI_READ_ONLY_MODEL_TOOL_IDS = (
    "list_directory",
    "file_read",
    "workspace_check",
    "git_status",
    "git_diff",
)


def _classify_tui_intent(
    text: str,
    *,
    selected_skills: Sequence[str] = (),
) -> TuiIntentDecision:
    del selected_skills
    stripped = str(text or "").strip()
    if not stripped:
        return TuiIntentDecision(
            permission="",
            complexity="",
            confidence=0.0,
            rationale="empty input",
            clarification="What should Super DAN do?",
        )
    return TuiIntentDecision(
        permission="",
        complexity="",
        confidence=0.35,
        rationale="free-text routing requires model-assisted intent classification",
        clarification=(
            "I need the model router to decide whether this is progress, read-only inspection, "
            "or workspace-changing work."
        ),
    )


def _tui_decision_from_core_decision(decision: Any) -> TuiIntentDecision:
    lane = str(getattr(decision, "lane", "") or "").strip()
    confidence = float(getattr(decision, "confidence", 0.0) or 0.0)
    rationale = str(getattr(decision, "rationale", "") or "").strip() or "model-assisted route"
    effort = str(getattr(decision, "executor_effort", "") or "complex").strip().lower()
    if effort not in {"simple", "complex"}:
        effort = "complex"
    if lane == NARRATOR_READ_ONLY:
        return TuiIntentDecision(
            permission="read-only",
            complexity="narrator",
            confidence=confidence,
            rationale=rationale,
        )
    if lane == "executor read-only":
        return TuiIntentDecision(
            permission="read-only",
            complexity=effort,
            confidence=confidence,
            rationale=rationale,
        )
    if lane == "executor write":
        return TuiIntentDecision(
            permission="write",
            complexity=effort,
            confidence=confidence,
            rationale=rationale,
        )
    return TuiIntentDecision(
        permission="",
        complexity="",
        confidence=confidence,
        rationale=rationale,
        clarification=str(getattr(decision, "clarification", "") or "").strip()
        or "Should this be progress/status, read-only inspection, or workspace-changing work?",
    )


def _extract_read_only_path_mentions(text: str) -> list[str]:
    mentions: list[str] = []
    for match in _RICH_PATH_RE.finditer(str(text or "")):
        token = match.group(0).strip().rstrip(".,;:")
        if token and token not in mentions:
            mentions.append(token)
    return mentions


def _parse_simple_write_request(text: str) -> TuiSimpleWritePlan | None:
    try:
        tokens = shlex.split(str(text or ""))
    except ValueError:
        return None
    if not tokens:
        return None
    lowered = [token.lower() for token in tokens]
    for index, token in enumerate(lowered):
        if token in {"copy", "cp", "move", "mv", "rename"}:
            if index + 1 >= len(tokens):
                return None
            source = tokens[index + 1]
            destination = ""
            for marker_index in range(index + 2, len(tokens)):
                if lowered[marker_index] in {"to", "as"} and marker_index + 1 < len(tokens):
                    destination = tokens[marker_index + 1]
                    break
            if not destination:
                return None
            operation = "copy" if token in {"copy", "cp"} else "move"
            return TuiSimpleWritePlan(operation=operation, source=source, destination=destination)
        if token == "touch":
            if index + 1 < len(tokens):
                return TuiSimpleWritePlan(operation="touch", destination=tokens[index + 1])
            return None
    return None


def _read_only_keywords(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", str(text or "").lower())
    result: list[str] = []
    for word in words:
        if word in _READ_ONLY_SOURCE_STOPWORDS:
            continue
        if word not in result:
            result.append(word)
    return result[:8]


def _candidate_workspace_sources(
    workspace_root: Path,
    *,
    limit: int = 240,
    include_dirs: bool = False,
) -> list[Path]:
    sources: list[Path] = []
    try:
        iterator = workspace_root.rglob("*")
        for path in iterator:
            if len(sources) >= limit:
                break
            rel_parts = path.relative_to(workspace_root).parts
            if any(part in _READ_ONLY_SKIP_PARTS for part in rel_parts):
                continue
            if path.is_dir():
                if include_dirs:
                    sources.append(path)
                continue
            if path.suffix.lower() not in _READ_ONLY_FILE_SUFFIXES:
                continue
            sources.append(path)
    except OSError:
        return []
    return sorted(sources, key=lambda item: str(item.relative_to(workspace_root)).lower())


def _candidate_sources_under(path: Path, *, limit: int = 8) -> list[Path]:
    if path.is_file():
        return [path]
    sources: list[Path] = []
    try:
        for candidate in path.rglob("*"):
            if len(sources) >= limit:
                break
            rel_parts = candidate.relative_to(path).parts
            if any(part in _READ_ONLY_SKIP_PARTS for part in rel_parts):
                continue
            if candidate.is_file() and candidate.suffix.lower() in _READ_ONLY_FILE_SUFFIXES:
                sources.append(candidate)
    except OSError:
        return []
    return sorted(sources, key=lambda item: str(item).lower())


def _extract_read_only_search_terms(text: str) -> list[str]:
    quoted = [match.group(1).strip() for match in re.finditer(r'"([^"]{2,80})"', str(text or ""))]
    if quoted:
        return quoted[:4]
    keywords = _read_only_keywords(text)
    filtered = [
        keyword
        for keyword in keywords
        if keyword not in {"find", "search", "grep", "inside", "project", "docs", "files"}
        and "." not in keyword
        and "/" not in keyword
    ]
    return filtered[:4]


def _search_sources(
    workspace_root: Path,
    objective: str,
    *,
    limit: int = 12,
) -> tuple[list[str], list[str]]:
    terms = _extract_read_only_search_terms(objective)
    if not terms:
        return ["Search skipped: no query terms identified."], ["Add a quoted phrase or keyword to search."]
    explicit_sources = _resolve_read_only_sources(workspace_root, objective, limit=12)
    sources: list[Path] = []
    if explicit_sources:
        for source in explicit_sources:
            sources.extend(_candidate_sources_under(source, limit=24) if source.is_dir() else [source])
    else:
        sources = _candidate_workspace_sources(workspace_root, limit=400)
    matches: list[str] = []
    lowered_terms = [term.lower() for term in terms]
    for source in sources:
        rel = str(source.relative_to(workspace_root)) if _is_relative_to_path(source, workspace_root) else str(source)
        text = _read_text_preview(source, max_bytes=64_000)
        for line_number, line in enumerate(text.splitlines(), start=1):
            lowered = line.lower()
            if any(term in lowered for term in lowered_terms):
                matches.append(f"{rel}:{line_number}: {_clip(line.strip(), limit=150)}")
                if len(matches) >= limit:
                    activity = [f"Search completed: {len(matches)} match(es) for {', '.join(terms)}."]
                    return activity, matches
    if matches:
        activity = [f"Search completed: {len(matches)} match(es) for {', '.join(terms)}."]
        return activity, matches
    activity = [f"Search completed: no matches for {', '.join(terms)}."]
    return activity, ["No matching lines found in bounded workspace sources."]


def _resolve_read_only_sources(workspace_root: Path, objective: str, *, limit: int = 4) -> list[Path]:
    root = normalize_workspace_root(str(workspace_root))
    resolved: list[Path] = []
    for raw in _extract_read_only_path_mentions(objective):
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = root / path
        try:
            candidate = path.resolve()
            root_resolved = root.resolve()
        except OSError:
            continue
        if _is_relative_to_path(candidate, root_resolved) and candidate.exists():
            resolved.append(candidate)
    if resolved:
        return resolved[:limit]

    keywords = _read_only_keywords(objective)
    if not keywords:
        return []
    scored: list[tuple[int, str, Path]] = []
    for path in _candidate_workspace_sources(root):
        rel = str(path.relative_to(root)).lower()
        stem = path.stem.lower()
        name = path.name.lower()
        score = 0
        for keyword in keywords:
            if keyword in name:
                score += 5
            elif keyword in stem:
                score += 4
            elif keyword in rel:
                score += 2
        if score:
            scored.append((score, rel, path))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [path for _, _, path in scored[:limit]]


def _workspace_overview_lines(workspace_root: Path, *, limit: int = 18) -> list[str]:
    try:
        entries = sorted(workspace_root.iterdir(), key=lambda item: item.name.lower())
    except OSError as exc:
        return [f"Workspace could not be listed: {exc}"]
    lines = [f"Workspace: {workspace_root}"]
    visible = [
        item
        for item in entries
        if item.name not in _READ_ONLY_SKIP_PARTS and not item.name.startswith(".")
    ][:limit]
    if not visible:
        lines.append("No visible top-level files or directories.")
        return lines
    lines.append("Top-level entries:")
    for item in visible:
        suffix = "/" if item.is_dir() else ""
        lines.append(f"- {item.name}{suffix}")
    if len(entries) > len(visible):
        lines.append(f"... {len(entries) - len(visible)} more")
    return lines


def _read_text_preview(path: Path, *, max_bytes: int = 32_000) -> str:
    try:
        data = path.read_bytes()[:max_bytes]
    except OSError as exc:
        return f"Could not read {path}: {exc}"
    return data.decode("utf-8", errors="replace")


def _summarize_source(path: Path, text: str, workspace_root: Path) -> list[str]:
    rel = str(path.relative_to(workspace_root)) if _is_relative_to_path(path, workspace_root) else str(path)
    lines = text.splitlines()
    headings = [line.strip() for line in lines if line.lstrip().startswith("#")][:8]
    open_tasks = [line.strip() for line in lines if re.search(r"- \[ \]", line)][:8]
    done_count = sum(1 for line in lines if re.search(r"- \[[xX]\]", line))
    open_count = sum(1 for line in lines if re.search(r"- \[ \]", line))
    result = [f"Source: {rel} ({len(lines)} lines)."]
    if path.suffix.lower() in _CODE_REVIEW_FILE_SUFFIXES:
        imports = sum(1 for line in lines if re.match(r"\s*(?:from\s+\S+\s+import|import\s+\S+)", line))
        classes = [match.group(1) for line in lines if (match := re.match(r"\s*class\s+([A-Za-z_][A-Za-z0-9_]*)", line))]
        functions = [match.group(1) for line in lines if (match := re.match(r"\s*def\s+([A-Za-z_][A-Za-z0-9_]*)", line))]
        doc_hint = ""
        joined = "\n".join(lines[:12])
        doc_match = re.search(r'"""(.*?)"""|\'\'\'(.*?)\'\'\'', joined, flags=re.DOTALL)
        if doc_match:
            doc_hint = " ".join((doc_match.group(1) or doc_match.group(2) or "").split())
        result.append(
            f"Code summary: {imports} import(s), {len(classes)} class(es), {len(functions)} function(s)."
        )
        if classes:
            result.append("Classes: " + ", ".join(classes[:6]))
        if functions:
            result.append("Functions: " + ", ".join(functions[:8]))
        if doc_hint:
            result.append("Purpose hint: " + _clip(doc_hint, limit=160))
        if len(result) == 2:
            result.append("No top-level definitions detected in the inspected preview.")
        return result
    if open_count or done_count:
        result.append(f"Tasks: {open_count} open, {done_count} completed.")
    if headings:
        result.append("Headings: " + "; ".join(_clip(item, limit=60) for item in headings[:6]))
    if open_tasks:
        result.append("Open items:")
        result.extend(f"- {_clip(item, limit=140)}" for item in open_tasks[:6])
    if len(result) == 1:
        preview = " ".join(line.strip() for line in lines[:8] if line.strip())
        result.append(_clip(preview or "File is empty.", limit=220))
    return result


def _excerpt_source(path: Path, text: str, workspace_root: Path, *, max_lines: int = 28) -> list[str]:
    rel = str(path.relative_to(workspace_root)) if _is_relative_to_path(path, workspace_root) else str(path)
    lines = text.splitlines()
    result = [f"Source: {rel} ({len(lines)} lines)."]
    excerpt = lines[:max_lines]
    if not excerpt:
        result.append("(empty file)")
        return result
    result.extend(_clip(line, limit=180) for line in excerpt)
    if len(lines) > max_lines:
        result.append(f"... {len(lines) - max_lines} more lines")
    return result


def _build_read_only_answer(
    workspace_root: Path,
    objective: str,
    decision: TuiIntentDecision,
) -> tuple[list[str], list[str]]:
    sources = _resolve_read_only_sources(workspace_root, objective)
    activity: list[str] = []
    answer: list[str] = []
    token_set = set(tokenize_intent_text(objective))
    if token_set & {"find", "grep", "search"}:
        return _search_sources(workspace_root, objective)
    wants_summary = bool(token_set & {"analyse", "analyze", "explain", "review", "summarise", "summarize", "summary"})

    if sources:
        for source in sources:
            if source.is_dir():
                rel_dir = str(source.relative_to(workspace_root)) if _is_relative_to_path(source, workspace_root) else str(source)
                activity.append(f"Directory listed: {rel_dir}")
                if wants_summary or decision.complexity == "complex":
                    nested_sources = _candidate_sources_under(source, limit=6)
                    if nested_sources:
                        activity.append(f"Sources inspected under {rel_dir}: {len(nested_sources)}")
                        for nested in nested_sources[:6]:
                            answer.extend(_summarize_source(nested, _read_text_preview(nested), workspace_root))
                    else:
                        answer.extend(_workspace_overview_lines(source))
                else:
                    answer.extend(_workspace_overview_lines(source))
                continue
            rel = str(source.relative_to(workspace_root)) if _is_relative_to_path(source, workspace_root) else str(source)
            text = _read_text_preview(source)
            activity.append(f"Source inspected: {rel}")
            if wants_summary or decision.complexity == "complex":
                answer.extend(_summarize_source(source, text, workspace_root))
            else:
                answer.extend(_excerpt_source(source, text, workspace_root))
        return activity, answer

    if token_set & {"list", "project", "show", "status", "what", "where", "workspace"}:
        activity.append("Workspace listed.")
        answer.extend(_workspace_overview_lines(workspace_root))
        return activity, answer

    activity.append("No matching workspace source found.")
    answer.append(
        "No matching workspace file was found. Add a path like docs/todo.md or use /status for queue state."
    )
    return activity, answer


def _resolve_simple_write_path(workspace_root: Path, raw_path: str) -> Path:
    root = normalize_workspace_root(str(workspace_root)).resolve()
    path = Path(str(raw_path or "")).expanduser()
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve(strict=False)
    if not _is_relative_to_path(resolved, root):
        raise ValueError(f"path is outside workspace: {raw_path}")
    try:
        relative_parts = resolved.relative_to(root).parts
    except ValueError:
        relative_parts = ()
    if ".dan-super" in relative_parts:
        raise ValueError(f"path is reserved for Super DAN state: {raw_path}")
    return resolved


def _run_simple_write_plan(
    workspace_root: Path,
    plan: TuiSimpleWritePlan,
) -> tuple[list[str], list[str], list[str]]:
    activity: list[str] = []
    results: list[str] = []
    changed: list[str] = []
    root = normalize_workspace_root(str(workspace_root)).resolve()

    if plan.operation == "touch":
        destination = _resolve_simple_write_path(root, plan.destination)
        if destination.exists():
            raise ValueError(f"destination already exists: {destination.relative_to(root)}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("", encoding="utf-8")
        rel = str(destination.relative_to(root))
        activity.append(f"File created: {rel}")
        results.append(f"Created: {rel}")
        changed.append(rel)
        return activity, results, changed

    source = _resolve_simple_write_path(root, plan.source)
    destination = _resolve_simple_write_path(root, plan.destination)
    if not source.exists():
        raise ValueError(f"source does not exist: {source.relative_to(root)}")
    if destination.exists():
        raise ValueError(f"destination already exists: {destination.relative_to(root)}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if plan.operation == "copy":
        if source.is_dir():
            raise ValueError("directory copy is not a simple-write operation yet")
        shutil.copy2(source, destination)
        action = "Copied"
    elif plan.operation == "move":
        source.rename(destination)
        action = "Moved"
    else:
        raise ValueError(f"unsupported simple-write operation: {plan.operation}")
    src_rel = str(source.relative_to(root))
    dst_rel = str(destination.relative_to(root))
    activity.append(f"{action}: {src_rel} -> {dst_rel}")
    results.append(f"{action}: {dst_rel}")
    changed.append(dst_rel)
    return activity, results, changed


def _tui_read_only_tool_ids(workspace_root: Path) -> list[str]:
    available = available_local_organism_tools()
    tool_ids = [
        tool_id
        for tool_id in _TUI_READ_ONLY_MODEL_TOOL_IDS
        if tool_id in available
    ]
    if not _workspace_is_git_repo(workspace_root):
        tool_ids = [tool_id for tool_id in tool_ids if not tool_id.startswith("git_")]
    return tool_ids


def _workspace_is_git_repo(workspace_root: Path) -> bool:
    current = workspace_root.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return True
    return False


def _tui_tool_schemas(tool_ids: Sequence[str]) -> list[dict[str, Any]]:
    available = available_local_organism_tools()
    schemas: list[dict[str, Any]] = []
    for tool_id in tool_ids:
        metadata = available.get(tool_id)
        if not metadata:
            continue
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": tool_id,
                    "description": str(metadata.get("description") or tool_id),
                    "parameters": metadata.get("parameters")
                    or {"type": "object", "properties": {}},
                },
            }
        )
    return schemas


def _build_tui_read_only_live_provider(args: argparse.Namespace, model: str) -> Any:
    return super_cli._build_live_provider(
        model,
        api_key=getattr(args, "api_key", None),
        base_url=getattr(args, "base_url", None),
    )


def _tui_read_only_system_prompt(tool_ids: Sequence[str]) -> str:
    return (
        "You are the read-only answer lane for Super DAN TUI. "
        "Answer the user's question using only the provided workspace and read-only tools. "
        "Do not mutate files, durable state, queues, git state, dependencies, networked services, or .dan-super. "
        "Allowed tools: "
        + ", ".join(tool_ids)
        + ". Use concise tool calls only when they materially improve the answer. "
        "Return a direct answer with cited workspace paths or path:line references when useful. "
        "For project or code review, use a short opening paragraph followed by compact bullets when useful. "
        "Do not paste raw source code, imports, docstrings, markdown tables, or long file excerpts unless the user explicitly asks for source text."
    )


def _tui_read_only_user_prompt(workspace_root: Path, objective: str) -> str:
    return (
        f"Workspace root: {workspace_root}\n"
        f"User request: {objective}\n\n"
        "Inspect only what is necessary. If the request is too broad, summarize the most relevant sources and name any limits."
    )


def _split_answer_lines(text: str, *, limit: int = _ANSWER_LINE_COUNT_LIMIT) -> list[str]:
    lines = []
    in_code_block = False
    for raw_line in str(text or "").splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        line = line.strip()
        if line.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
        line = re.sub(r"`([^`]+)`", r"\1", line)
        lines.append(line.strip())
    if not lines and str(text or "").strip():
        lines = [str(text).strip()]
    return [_clip(line, limit=_ANSWER_LINE_LIMIT) for line in lines[:limit]]


def _narrator_visible_answer_lines(values: Sequence[str], *, limit: int = _ANSWER_LINE_COUNT_LIMIT) -> list[str]:
    visible: list[str] = []
    for value in values:
        line = str(value or "").strip()
        if not line:
            continue
        if re.match(r"^(?:Trace|Event Log|Source|Context|Activity|Raw|Tool)\s*:", line, flags=re.IGNORECASE):
            continue
        visible.append(_clip(line, limit=_ANSWER_LINE_LIMIT))
        if len(visible) >= limit:
            break
    return visible


def _looks_like_markdown_table_line(line: str) -> bool:
    text = str(line or "").strip()
    return text.count("|") >= 2 or bool(re.fullmatch(r"[:|\-\s]+", text))


def _looks_like_raw_code_line(line: str) -> bool:
    text = str(line or "").strip()
    if not text:
        return False
    if re.match(
        r"^(?:from\s+\S+\s+import|import\s+\S+|def\s+\w+|class\s+\w+|return\b|if\s+__name__|"
        r"elif\b|else:|try:|except\b|finally:|with\s+|for\s+|while\s+)",
        text,
    ):
        return True
    if '"""' in text or "'''" in text:
        return True
    code_markers = sum(text.count(marker) for marker in ("{", "}", "=>", "==", "!=", "&&", "||", "::", ";"))
    return len(text) > 140 and code_markers >= 2


def _answer_lines_have_useful_prose(lines: Sequence[str]) -> bool:
    for line in lines:
        text = str(line or "").strip()
        if not text:
            continue
        if re.match(r"^(?:Source|Path|File|Trace|Event Log)\s*:", text, flags=re.IGNORECASE):
            continue
        if _looks_like_markdown_table_line(text) or _looks_like_raw_code_line(text):
            continue
        if len(re.findall(r"[A-Za-z][A-Za-z]{2,}", text)) >= 4:
            return True
    return False


def _read_only_tool_evidence_text(events: Sequence[Mapping[str, Any]], *, limit: int = 6000) -> str:
    chunks: list[str] = []
    for event in events:
        if str(event.get("event") or "") != "tool.completed":
            continue
        tool_id = str(event.get("tool_id") or "").strip()
        result = event.get("result") if isinstance(event.get("result"), dict) else {}
        arguments = event.get("arguments") if isinstance(event.get("arguments"), dict) else {}
        path = str(result.get("path") or arguments.get("path") or "").strip()
        if tool_id == "file_read":
            content = str(result.get("content") or "").strip()
            line_count = result.get("line_count") or result.get("lines") or ""
            header = f"file_read {path}".strip()
            if line_count:
                header += f" ({line_count} lines)"
            chunks.append(header)
            if content:
                chunks.append(_clip(content, limit=2000))
        elif tool_id == "list_directory":
            entries = result.get("entries")
            if isinstance(entries, list):
                names = []
                for item in entries[:40]:
                    if isinstance(item, dict):
                        names.append(str(item.get("path") or item.get("name") or "").strip())
                    else:
                        names.append(str(item).strip())
                chunks.append(f"list_directory {path}: " + ", ".join(name for name in names if name))
            else:
                chunks.append(f"list_directory {path}: {_clip(result, limit=1200)}")
        elif tool_id == "workspace_check":
            chunks.append(f"workspace_check: {_clip(json.dumps(result, sort_keys=True, default=str), limit=1600)}")
        elif tool_id in {"git_status", "git_diff"}:
            chunks.append(f"{tool_id}: {_clip(json.dumps(result, sort_keys=True, default=str), limit=1600)}")
    text = "\n\n".join(chunk for chunk in chunks if chunk.strip())
    return _clip(text, limit=limit)


def _tui_read_only_followup_messages(
    *,
    workspace_root: Path,
    objective: str,
    evidence: str,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are the read-only answer lane for Super DAN TUI. "
                "Write the final answer from the provided tool evidence only. "
                "Do not claim to read more files or call tools. Do not paste raw source code, imports, docstrings, markdown tables, or long excerpts. "
                "For project/code review, give concise findings with cited paths and concrete next steps."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Workspace root: {workspace_root}\n"
                f"User request: {objective}\n\n"
                "Tool evidence already gathered:\n"
                f"{evidence or '(no tool evidence was captured)'}\n\n"
                "Now provide the user-facing answer in 3-8 concise lines."
                " Use a short paragraph plus bullets when that is easier to read."
            ),
        },
    ]


def _tui_stream_output(args: argparse.Namespace) -> bool:
    return not (
        bool(getattr(args, "plain", False))
        or bool(getattr(args, "json", False))
        or bool(getattr(args, "quiet_progress", False))
        or bool(getattr(args, "raw_events", False))
    )


def _emit_tui_stream_line(args: argparse.Namespace, line: Any) -> None:
    if not _tui_stream_output(args):
        return
    text = str(line or "").strip()
    if not text:
        return
    if text == str(getattr(args, "_tui_last_stream_line", "") or ""):
        return
    setattr(args, "_tui_last_stream_line", text)
    _print_tui_stream_line(text, plain=bool(getattr(args, "plain", False)))


def _emit_tui_stream_answer(args: argparse.Namespace, state: "SuperTuiState") -> None:
    lines = [str(line or "").strip() for line in state.answer_lines if str(line or "").strip()]
    if not lines:
        return
    block_text = "\n".join(lines)
    if block_text == str(getattr(args, "_tui_last_stream_answer_block", "") or ""):
        return
    setattr(args, "_tui_last_stream_answer_block", block_text)
    _print_tui_stream_block("Answer", lines, plain=bool(getattr(args, "plain", False)))


def _run_tui_read_only_model_answer(
    args: argparse.Namespace,
    decision: TuiIntentDecision,
    state: SuperTuiState,
) -> bool:
    workspace_root = normalize_workspace_root(str(args.workspace))
    objective = str(getattr(args, "target", "") or "").strip()
    tool_ids = _tui_read_only_tool_ids(workspace_root)
    if not tool_ids:
        state._record_progress("Read-only model loop skipped: no read-only tools available.")
        return False
    try:
        model = super_cli._resolve_live_model(str(getattr(args, "model", "") or ""))
    except ValueError as exc:
        state._record_progress(f"Read-only model loop skipped: {_clip(exc, limit=160)}")
        return False

    async def _run() -> tuple[str, list[dict[str, Any]], bool]:
        provider = _build_tui_read_only_live_provider(args, model)
        tool_events: list[dict[str, Any]] = []

        def record_event(event: dict[str, Any]) -> None:
            row = dict(event)
            tool_events.append(row)
            state.observe(row)

        runtime = LocalOrganismToolRuntime(
            tool_ids=tool_ids,
            workspace_root=workspace_root,
            event_callback=record_event,
        )
        completion_provider = ToolLoopCompletionProvider(
            provider=provider,
            tool_runtime=runtime,
            default_model=model,
            max_rounds=4,
            max_tool_calls=12,
            event_callback=record_event,
        )
        try:
            response = await completion_provider.complete(
                CompletionRequest(
                    model=model,
                    system_prompt=_tui_read_only_system_prompt(tool_ids),
                    user_prompt=_tui_read_only_user_prompt(workspace_root, objective),
                    temperature=0.2,
                    max_tokens=1600,
                    tools=_tui_tool_schemas(tool_ids),
                    metadata={
                        "worker_id": "super-dan.tui.read-only",
                        "tui_lane": decision.lane,
                    },
                )
            )
            text = response.text
            answer_lines = _split_answer_lines(text)
            used_followup = False
            if not _answer_lines_have_useful_prose(answer_lines):
                evidence = _read_only_tool_evidence_text(tool_events)
                if evidence:
                    followup = await provider.complete(
                        messages=_tui_read_only_followup_messages(
                            workspace_root=workspace_root,
                            objective=objective,
                            evidence=evidence,
                        ),
                        model=model,
                        temperature=0.2,
                        max_tokens=1200,
                    )
                    text = str(followup.text or "").strip()
                    used_followup = True
            return text, tool_events, used_followup
        finally:
            await super_cli._close_live_provider(provider)

    state.model = model
    state._record_progress("Read-only model loop started.")
    try:
        text, events, used_followup = asyncio.run(_run())
    except Exception as exc:
        state._record_result(f"Read-only model loop failed: {_clip(exc, limit=180)}")
        return False

    answer_lines = _split_answer_lines(text)
    for line in answer_lines:
        state._record_answer(line)
    state._record_progress(
        f"Read-only model loop completed: {sum(1 for event in events if str(event.get('event')) == 'tool.completed')} tool result(s)."
    )
    if used_followup:
        state._record_progress("Model wrote the final answer from gathered read-only evidence.")
    if not state.answer_lines or not _answer_lines_have_useful_prose(state.answer_lines):
        state._record_result("Read-only model loop did not return a usable textual answer.")
        return False
    return True


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
    limit: int = 16,
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
    mode_line: str = ""
    mode_rationale: str = ""
    debug_events: bool = False
    changed_files: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    activity_lines: list[str] = field(default_factory=list)
    answer_lines: list[str] = field(default_factory=list)
    narrator_lines: list[str] = field(default_factory=list)
    narrator_reports: list[dict[str, Any]] = field(default_factory=list)
    trace_lines: list[str] = field(default_factory=list)
    progress: list[str] = field(default_factory=list)
    results: list[str] = field(default_factory=list)
    recent: list[str] = field(default_factory=list)
    timeline: list[str] = field(default_factory=list)
    raw_events: list[str] = field(default_factory=list)
    tool_counts: dict[str, int] = field(default_factory=dict)
    _coalesce_indexes: dict[str, int] = field(default_factory=dict)
    started_at_monotonic: float = 0.0
    ended_at_monotonic: float = 0.0
    _last_narrator_report_key: str = ""

    def _record_timeline(self, line: Any, *, coalesce_key: str = "", limit: int = 120) -> None:
        text = str(line or "").strip()
        if not text:
            return
        if coalesce_key and coalesce_key in self._coalesce_indexes:
            index = self._coalesce_indexes[coalesce_key]
            if 0 <= index < len(self.timeline):
                self.timeline[index] = text
                return
        self.timeline.append(text)
        if coalesce_key:
            self._coalesce_indexes[coalesce_key] = len(self.timeline) - 1
        if len(self.timeline) > limit:
            overflow = len(self.timeline) - limit
            del self.timeline[:overflow]
            self._coalesce_indexes = {
                key: value - overflow
                for key, value in self._coalesce_indexes.items()
                if value - overflow >= 0
            }

    def _record_progress(self, line: Any, *, limit: int = 12) -> None:
        _append_unique(self.progress, line, limit=limit)
        _append_unique(self.activity_lines, line, limit=limit)
        self._record_timeline(line)

    def _record_result(self, line: Any, *, limit: int = 10) -> None:
        _append_unique(self.results, line, limit=limit)

    def _record_answer(self, line: Any, *, limit: int = _ANSWER_LINE_COUNT_LIMIT) -> None:
        _append_unique(self.answer_lines, line, limit=limit)

    def _narrator_snapshot(self) -> RunNarratorSnapshot:
        elapsed = 0.0
        if self.started_at_monotonic:
            end = self.ended_at_monotonic or time.monotonic()
            elapsed = max(0.0, end - self.started_at_monotonic)
        trace_refs = [self.event_log_path] if self.event_log_path else []
        trace_refs.extend(self.trace_lines[-3:])
        snapshot_id = ":".join(
            part
            for part in (
                self.task_id or "tui",
                str(len(self.timeline) + len(self.results) + len(self.narrator_lines)),
                self.status,
                self.phase,
            )
            if part
        )
        return RunNarratorSnapshot(
            snapshot_id=snapshot_id,
            run_id=self.task_id,
            task_id=self.task_id,
            objective=self.objective,
            workspace=self.workspace,
            status=self.status,
            phase=self.phase,
            current_step=self.current_step,
            elapsed_seconds=elapsed,
            model=self.model,
            mode_line=self.mode_line,
            recent_events=tuple(self.recent[-8:]),
            activity=tuple(self.activity_lines[-10:] or self.timeline[-10:]),
            results=tuple(self._result_event_lines(include_answers=False)[-10:]),
            changed_files=tuple(self.changed_files[-8:]),
            artifacts=tuple(self.artifacts[-8:]),
            validation=self.validation,
            validation_score=self.validation_score,
            blockers=tuple(self.blockers[-6:]),
            queued_work=self.queue_status,
            trace_refs=tuple(path for path in trace_refs if path),
            source_event_count=len(self.raw_events),
        )

    def _record_narrator_report(self, report: NarratorReport, *, limit: int = 10) -> None:
        text = str(report.text or "").strip()
        if not text:
            return
        if report.kind == "opening":
            return
        if report.kind == "final":
            previous_final_texts = {
                str(payload.get("text") or "").strip()
                for payload in self.narrator_reports
                if isinstance(payload, dict) and payload.get("kind") == "final"
            }
            if previous_final_texts:
                self.narrator_lines = [
                    line for line in self.narrator_lines if line not in previous_final_texts
                ]
                self.narrator_reports = [
                    payload
                    for payload in self.narrator_reports
                    if not (isinstance(payload, dict) and payload.get("kind") == "final")
                ]
                self.answer_lines = [
                    line for line in self.answer_lines if line not in previous_final_texts
                ]
        key = f"{report.kind}:{text}"
        if key == self._last_narrator_report_key:
            return
        self._last_narrator_report_key = key
        _append_unique(self.narrator_lines, text, limit=limit)
        self.narrator_reports.append(report.to_payload())
        if report.kind == "final":
            _append_unique(self.answer_lines, text, limit=_ANSWER_LINE_COUNT_LIMIT)
        if len(self.narrator_reports) > limit:
            del self.narrator_reports[: len(self.narrator_reports) - limit]

    def _maybe_record_narrator_report(self, event_name: str, event: Mapping[str, Any]) -> None:
        if self.debug_events:
            return
        trigger = _narrator_trigger_for_event(event_name, event)
        if not trigger:
            return
        report = deterministic_narrator_report(
            self._narrator_snapshot(),
            trigger=trigger,
            event_name=event_name,
            event_payload=event,
        )
        if report is not None:
            self._record_narrator_report(report)

    def set_intent_decision(self, decision: TuiIntentDecision | None) -> None:
        if decision is None:
            return
        self.mode_line = decision.mode_line
        self.mode_rationale = decision.rationale

    def elapsed_footer(self) -> str:
        if not self.started_at_monotonic:
            return ""
        finished = self.ended_at_monotonic or self.status in {"completed", "failed", "blocked"}
        end = self.ended_at_monotonic if self.ended_at_monotonic else time.monotonic()
        elapsed = _format_elapsed_duration(end - self.started_at_monotonic)
        return f"{'Elapsed' if finished else 'Working'}: {elapsed}"

    def observe(self, event: Mapping[str, Any]) -> str | None:
        name = str(event.get("event") or "").strip()
        if not name:
            return None
        raw_line = _raw_event_line(event)
        if raw_line:
            _append_unique(self.raw_events, raw_line, limit=80)
        line: str | None = None
        if name == "run.log.started":
            if not self.started_at_monotonic:
                self.started_at_monotonic = time.monotonic()
            self.ended_at_monotonic = 0.0
            self.status = "running"
            self.phase = "starting"
            self.objective = str(event.get("objective") or self.objective)
            self.workspace = str(event.get("workspace_root") or self.workspace)
            self.task_id = str(event.get("task_id") or self.task_id)
            self.current_step = "Starting Super DAN run"
            objective = _clip(self.objective or "the requested task", limit=120)
            line = f"You asked: {objective}"
            self._record_progress(line)
            if self.workspace:
                self._record_timeline(f"Workspace: {self.workspace}", coalesce_key="context:workspace")
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
            line = f"Skill setup {'failed' if failed else 'finished'}: {note}"
            self._record_progress(line)
            if failed:
                self._record_result(line)
        elif name in {"provider.build.started", "provider.build.completed"}:
            self.phase = "model setup"
            self.model = str(event.get("model") or event.get("requested_model") or self.model)
            self.current_step = "Getting ready"
            line = "Model provider ready."
            _append_unique(self.progress, line, limit=12)
            _append_unique(self.activity_lines, line, limit=12)
            self._record_timeline(line, coalesce_key="setup:model")
        elif name in {"live.generic_execution.started", "live.generic_build.started"}:
            self.phase = "building"
            self.workspace = str(event.get("workspace_root") or self.workspace)
            self.current_step = "Working in the workspace"
            line = f"Workspace work started: {_clip(self.workspace or 'the workspace', limit=120)}"
            _append_unique(self.progress, line, limit=12)
            _append_unique(self.activity_lines, line, limit=12)
            self._record_timeline(line, coalesce_key="context:workspace")
        elif name == "live.planning.started":
            self.phase = "planning"
            self.current_step = "Planning the run"
            line = "Planning started."
            self._record_progress(line)
        elif name == "live.planning.completed":
            self.phase = "planning complete"
            count = event.get("plan_file_count", 0)
            self.current_step = "Planning complete"
            line = f"Planning is complete: {count} plan file(s)."
            self._record_progress(line)
            if count:
                self._record_result(f"Plan files created/updated: {count}")
        elif name == "model.requested":
            self.phase = "model"
            self.model = str(event.get("model") or self.model)
            worker = _worker_label(event.get("worker_id"))
            scope = f" ({worker})" if worker else ""
            self.current_step = f"Thinking through the next step{scope}"
            line = None
        elif name == "model.responded":
            self.phase = "model response"
            tool_calls = [
                str(item).strip()
                for item in (event.get("tool_calls") or [])
                if str(item).strip()
            ]
            if tool_calls:
                self.current_step = "Choosing the next action"
            else:
                self.current_step = "Preparing the response"
            line = None
        elif name == "tool.started":
            self.phase = "tool"
            tool_id = str(event.get("tool_id") or "tool")
            self.active_tool = tool_id
            arguments = dict(event.get("arguments") or {})
            summary = _tool_request_summary(tool_id, arguments)
            self.current_step = f"Running {tool_id}: {summary}"
            if tool_id == "shell_command":
                intent = _shell_command_intent(arguments.get("command")).replace(" with shell", "")
                line = f"Terminal command started: {intent.lower()}."
            elif tool_id in {"file_read", "list_directory", "workspace_check"}:
                line = "Reading workspace context."
            elif tool_id in {"file_write", "file_edit"}:
                line = f"Workspace change prepared: {_clip(summary, limit=120)}"
            else:
                line = "Project tool started."
            _append_unique(self.progress, line, limit=12)
            coalesce = (
                "active:shell"
                if tool_id == "shell_command"
                else "active:read"
                if tool_id in {"file_read", "list_directory", "workspace_check"}
                else "active:write"
                if tool_id in {"file_write", "file_edit"}
                else f"active:{tool_id}"
            )
            self._record_timeline(line, coalesce_key=coalesce)
            _append_unique(self.activity_lines, line, limit=12)
        elif name == "tool.completed":
            tool_id = str(event.get("tool_id") or "tool")
            self.active_tool = ""
            self.tool_counts[tool_id] = self.tool_counts.get(tool_id, 0) + 1
            result = event.get("result") if isinstance(event.get("result"), dict) else {}
            path = result.get("path") if isinstance(result, dict) else None
            if tool_id in {"file_write", "file_edit"} and path:
                _append_unique(self.changed_files, str(path))
                self._record_result(f"Changed file: {path}")
            summary = _tool_result_summary(tool_id, event)
            count = self.tool_counts[tool_id]
            if tool_id in {"file_read", "list_directory", "workspace_check"}:
                noun = "item" if count == 1 else "items"
                context_summary = _workspace_context_result_summary(tool_id, event)
                self.current_step = f"Workspace context checked: {context_summary}"
                line = f"Workspace context checked ({count} {noun}); latest: {_clip(context_summary, limit=120)}"
                _append_unique(self.progress, line, limit=12)
                _append_unique(self.activity_lines, line, limit=12)
                self._record_timeline(line, coalesce_key="tool:read")
            elif tool_id in {"file_write", "file_edit"}:
                self.current_step = f"Workspace change completed: {summary}"
                if path:
                    line = f"File changed: {path}"
                else:
                    noun = "change" if count == 1 else "changes"
                    line = f"Workspace files updated ({count} {noun}); latest: {_clip(summary, limit=120)}"
                _append_unique(self.progress, line, limit=12)
                _append_unique(self.activity_lines, line, limit=12)
                self._record_timeline(line, coalesce_key="tool:write")
            elif tool_id == "shell_command":
                self.current_step = f"Terminal command completed: {summary}"
                exit_code = None
                if isinstance(result, dict) and "exit_code" in result:
                    exit_code = result.get("exit_code")
                status = f"exit {exit_code}" if exit_code is not None else "completed"
                line = f"Terminal command finished ({status}): {_clip(summary, limit=140)}"
                _append_unique(self.progress, line, limit=12)
                _append_unique(self.activity_lines, line, limit=12)
                self._record_timeline(line, coalesce_key=f"tool:{tool_id}")
            else:
                self.current_step = f"Project tool completed: {summary}"
                line = f"Project tool finished: {_clip(summary, limit=140)}"
                self._record_progress(line)
        elif name in {"tool.failed", "tool.denied"}:
            self.phase = "tool blocked"
            tool_id = str(event.get("tool_id") or "tool")
            message = _clip(event.get("error") or name, limit=160)
            self.current_step = f"Blocked on {tool_id}"
            line = f"{tool_id} {name.split('.')[-1]}: {message}"
            self._record_progress(line)
            self._record_result(line)
        elif name == "live.validation.started":
            self.phase = "validation"
            self.validation = "running"
            self.current_step = "Validator is checking the result"
            line = "Validation running."
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
            line = f"Validation {self.validation}{suffix}.".strip()
            self._record_progress(line)
            self._record_result(line)
            for item in event.get("deterministic_failures") or []:
                self._record_result(f"Validation gap: {_clip(item, limit=140)}")
        elif name in {"live.builder_retry.started", "live.website_repair.started", "live.generic_repair.started"}:
            self.phase = "repair"
            reason = _clip(event.get("reason") or name, limit=140)
            self.current_step = f"Repairing: {reason}"
            line = f"Repair pass started: {reason}"
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
            line = f"Repair {event.get('status') or 'completed'}{suffix}."
            self._record_progress(line)
            if changed:
                self._record_result(line)
        elif name == "super.hook.packet_enqueued":
            inbox = str(event.get("inbox_id") or "inbox")
            depth = event.get("queue_depth", "?")
            self.queue_status = f"{inbox} depth={depth}"
            self.current_step = f"Queued follow-up work for {inbox}"
            line = f"Follow-up queued for {inbox} (depth {depth})."
            self._record_progress(line)
        elif name in {"run.log.completed", "run.log.failed"}:
            self.status = str(event.get("status") or ("failed" if name.endswith("failed") else "completed"))
            self.phase = "done" if self.status == "completed" else "failed"
            self.event_log_path = str(event.get("event_log_path") or self.event_log_path)
            if self.started_at_monotonic and not self.ended_at_monotonic:
                self.ended_at_monotonic = time.monotonic()
            self.current_step = f"Run {self.status}"
            if self.status == "completed":
                line = "Done."
            else:
                line = f"The run {self.status}."
            self._record_progress(line)
            self._record_result(line)
        elif name == "super.heartbeat":
            self.phase = str(event.get("phase") or self.phase or "running")
            detail = _clip(event.get("detail") or "", limit=120)
            if re.search(r"\b(?:round|model|tools?)=", detail, flags=re.IGNORECASE):
                detail = ""
            human_phase = _human_progress_phase(self.phase)
            suffix = f" - {detail}" if detail else ""
            self.current_step = f"Still running: {self.phase}{suffix}"
            line = f"Still working on {human_phase}{suffix}."
            _append_unique(self.progress, line, limit=12)
            _append_unique(self.activity_lines, line, limit=12)
            self._record_timeline(line, coalesce_key="heartbeat")
        elif name == "narrator.requested":
            self.phase = "narrator"
            self.current_step = "Progress summary requested"
            line = "Progress summary requested from the current run snapshot."
            self._record_progress(line)
        elif name == "narrator.started":
            self.phase = "narrator"
            self.current_step = "Narrator is preparing an answer"
            line = "Narrator model call started from the current run snapshot."
            self._record_progress(line)
        elif name == "narrator.completed":
            self.phase = "narrator complete"
            self.current_step = "Narrator answer completed"
            line = "Narrator answer completed."
            self._record_progress(line)
        elif name == "narrator.stale":
            self.phase = "narrator stale"
            self.current_step = "Narrator answer is stale"
            line = "Narrator answer is based on an older snapshot."
            self._record_progress(line)
            self._record_result(line)
        elif name == "narrator.failed":
            self.phase = "narrator failed"
            message = _clip(event.get("error") or "failed", limit=160)
            self.current_step = "Narrator failed"
            line = f"Narrator failed: {message}"
            self._record_progress(line)
            self._record_result(line)
        self._maybe_record_narrator_report(name, event)
        if line:
            _append_unique(self.recent, line, limit=8)
        return line

    def apply_live_result(self, live_result: Mapping[str, Any]) -> None:
        self.status = str(live_result.get("status") or self.status or "unknown")
        self.phase = "done" if self.status == "completed" else "failed"
        self.event_log_path = str(live_result.get("event_log_path") or self.event_log_path)
        if self.started_at_monotonic and not self.ended_at_monotonic:
            self.ended_at_monotonic = time.monotonic()
        for path in live_result.get("files") or []:
            _append_unique(self.changed_files, str(path))
            _append_unique(self.artifacts, str(path))
            self._record_result(f"Changed: {path}", limit=10)
        website = str(live_result.get("website") or "").strip()
        if website:
            _append_unique(self.artifacts, website)
            self._record_result(f"Artifact: {website}", limit=10)
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
        final_line = f"Final result: {self.status}{score}."
        self._record_timeline(final_line)
        _append_unique(self.activity_lines, final_line, limit=12)
        _append_unique(self.recent, f"Final result: {self.status}", limit=8)
        report = deterministic_narrator_report(
            self._narrator_snapshot(),
            trigger="final",
            event_name="run.log.completed" if self.status == "completed" else "run.log.failed",
            event_payload=dict(live_result),
        )
        if report is not None:
            self._record_narrator_report(report)

    def transcript_summary(self, *, exit_code: int | None = None) -> str:
        if self.answer_lines and self.status in {"completed", "failed", "blocked", "stopped"}:
            answer = "\n".join(str(line or "").rstrip() for line in self.answer_lines if str(line or "").strip()).strip()
            if self.event_log_path:
                return f"{answer} Trace: {self.event_log_path}"
            return answer
        status = self.status or ("completed" if exit_code == 0 else "finished")
        pieces = [f"{status}"]
        if self.validation:
            score = f" ({self.validation_score})" if self.validation_score else ""
            pieces.append(f"validation {self.validation}{score}")
        if self.changed_files:
            changed = ", ".join(self.changed_files[-4:])
            pieces.append(f"changed {changed}")
        elif self.artifacts:
            artifacts = ", ".join(self.artifacts[-4:])
            pieces.append(f"artifacts {artifacts}")
        elif self.answer_lines:
            pieces.append("answer " + _clip("; ".join(self.answer_lines[:2]), limit=160))
        if self.blockers:
            pieces.append(f"blockers {len(self.blockers)}")
        if self.event_log_path:
            pieces.append(f"trace {self.event_log_path}")
        return "Run " + "; ".join(pieces) + "."

    def is_narrator_mode(self) -> bool:
        return self.mode_line.startswith(NARRATOR_READ_ONLY)

    def _visible_narrator_lines(self, *, limit: int = 6) -> list[str]:
        answer_texts = {str(item or "").strip() for item in self.answer_lines}
        lines = [
            line
            for line in self.narrator_lines
            if str(line or "").strip() and str(line or "").strip() not in answer_texts
        ]
        return lines[-limit:]

    def is_terminal(self) -> bool:
        return self.status in {"completed", "failed", "blocked", "stopped"}

    def final_answer_lines(self, *, limit: int = _ANSWER_LINE_COUNT_LIMIT) -> list[str]:
        if self.is_narrator_mode():
            lines = _narrator_visible_answer_lines(self.answer_lines, limit=limit)
        else:
            lines = [str(line or "").strip() for line in self.answer_lines if str(line or "").strip()]
        if lines:
            return lines[:limit]
        if self.status == "completed":
            return ["Done."]
        if self.status in {"failed", "blocked", "stopped"}:
            for item in reversed(self.results):
                text = str(item or "").strip()
                if text and not text.startswith("Final result:"):
                    return [_clip(text, limit=220)]
            if self.blockers:
                return [f"Blocker: {_clip(self.blockers[-1], limit=180)}"]
        if self.status in {"failed", "blocked", "stopped"}:
            return [f"The run {self.status} before completing the request."]
        if self.current_step:
            return [_clip(self.current_step, limit=180)]
        return []

    def final_outcome_lines(self, *, include_trace: bool = False, limit: int = 12) -> list[str]:
        lines: list[str] = []
        if self.status and self.status != "completed":
            lines.append(f"- Status: {self.status}")
        if self.validation:
            score = f" ({self.validation_score})" if self.validation_score else ""
            lines.append(f"- Validation: {self.validation}{score}")
        seen_paths: set[str] = set()
        for path in self.changed_files[-6:]:
            text = str(path or "").strip()
            if text and text not in seen_paths:
                seen_paths.add(text)
                lines.append(f"- Changed: {text}")
        for path in self.artifacts[-6:]:
            text = str(path or "").strip()
            if text and text not in seen_paths:
                seen_paths.add(text)
                lines.append(f"- Artifact: {text}")
        for item in self.blockers[-4:]:
            lines.append(f"- Blocker: {_clip(item, limit=140)}")
        if self.status in {"failed", "blocked", "stopped"}:
            for item in self.results[-4:]:
                text = str(item or "").strip()
                if not text or text.startswith("Final result:") or text.startswith("Validation:"):
                    continue
                rendered = f"- {text}"
                if rendered not in lines:
                    lines.append(rendered)
        if include_trace and self.event_log_path:
            lines.append(f"- Trace: {self.event_log_path}")
        return lines[:limit]

    def final_summary_lines(self, *, include_trace: bool = False) -> list[str]:
        lines: list[str] = []
        answer = self.final_answer_lines()
        if answer:
            lines.append("Answer:")
            lines.extend(answer)
        outcome = self.final_outcome_lines(include_trace=include_trace)
        if outcome:
            lines.append("Outcome:")
            lines.extend(outcome)
        footer = self.elapsed_footer()
        if footer:
            lines.append(footer)
        return lines or [self.current_step or "No visible result."]

    def plain_snapshot(self) -> str:
        if self.is_terminal() and not self.debug_events:
            return "\n".join(self.final_summary_lines(include_trace=False))
        narrator_mode = self.is_narrator_mode()
        if narrator_mode and not self.debug_events:
            lines = [
                "Super DAN TUI",
                f"Status: {self.status}",
            ]
            if self.mode_line:
                lines.append(f"Mode: {_clip(self.mode_line, limit=180)}")
            answer_lines = _narrator_visible_answer_lines(self.answer_lines, limit=_ANSWER_LINE_COUNT_LIMIT)
            if answer_lines:
                lines.append("Answer:")
                lines.extend(answer_lines)
            elif self.results:
                lines.append("Status detail:")
                lines.extend(_tui_list_item(item) for item in self.results[-3:])
            elif self.current_step:
                lines.append(f"Status detail: {_clip(self.current_step, limit=140)}")
            footer = self.elapsed_footer()
            if footer:
                lines.append(footer)
            return "\n".join(lines)
        lines = [
            "Super DAN TUI",
            f"Status: {self.status}",
            f"Phase: {self.phase}",
        ]
        if self.task_id:
            lines.append(f"Run: {self.task_id}")
        if self.objective:
            lines.append(f"Objective: {_clip(self.objective, limit=140)}")
        if self.mode_line:
            lines.append(f"Mode: {_clip(self.mode_line, limit=180)}")
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
            lines.extend(_tui_list_item(path) for path in self.changed_files[-6:])
        if self.artifacts:
            lines.append("Artifacts:")
            lines.extend(_tui_list_item(path) for path in self.artifacts[-6:])
        if self.blockers:
            lines.append("Blockers:")
            lines.extend(_tui_list_item(_clip(item, limit=120)) for item in self.blockers[-4:])
        if self.event_log_path:
            lines.append(f"Event Log: {self.event_log_path}")
        if narrator_mode and self.answer_lines:
            lines.append("Answer:")
            lines.extend(_narrator_visible_answer_lines(self.answer_lines, limit=_ANSWER_LINE_COUNT_LIMIT))
        if not narrator_mode and self.answer_lines:
            lines.append("Answer:")
            lines.extend(_tui_list_item(item) for item in self.answer_lines[:_ANSWER_LINE_COUNT_LIMIT])
        visible_narrator = self._visible_narrator_lines(limit=6)
        if not narrator_mode and visible_narrator:
            lines.append("Narrator:")
            lines.extend(_tui_list_item(item) for item in visible_narrator)
        if not narrator_mode and not self.narrator_lines and (self.activity_lines or self.timeline):
            lines.append("Activity:")
            activity = self.activity_lines[-12:] if self.activity_lines else self.timeline[-12:]
            lines.extend(_tui_list_item(item) for item in activity)
        result_lines = self._result_event_lines(include_answers=not narrator_mode)
        if result_lines and not (self.narrator_lines and not self.debug_events):
            lines.append("Result:")
            lines.extend(_tui_list_item(item) for item in result_lines[-12:])
        footer = self.elapsed_footer()
        if footer:
            lines.append(footer)
        return "\n".join(lines)

    def _result_event_lines(self, *, include_answers: bool = True) -> list[str]:
        result_lines: list[str] = []
        if include_answers:
            result_lines.extend(self.answer_lines[:_ANSWER_LINE_COUNT_LIMIT])
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
        return result_lines

    def recent_event_lines(self) -> list[str]:
        if self.debug_events:
            lines = list(self.raw_events[-26:])
            if self.event_log_path:
                lines.append(f"Trace: {self.event_log_path}")
            return lines or ["Waiting for raw events."]
        if self.is_terminal():
            return self.final_summary_lines(include_trace=False)
        lines: list[str] = []
        narrator_mode = self.is_narrator_mode()
        if narrator_mode:
            answer_lines = _narrator_visible_answer_lines(self.answer_lines, limit=_ANSWER_LINE_COUNT_LIMIT)
            if answer_lines:
                lines.append("Answer:")
                for item in answer_lines:
                    lines.append(f"  {item}")
            elif self.results:
                lines.append("Status detail:")
                for item in self.results[-3:]:
                    lines.append(_tui_list_item(item, indent="  "))
            elif self.current_step:
                lines.append(f"Status: {_clip(self.current_step, limit=120)}")
            footer = self.elapsed_footer()
            if footer:
                lines.append(footer)
            return lines or ["Waiting for progress."]
        if self.mode_line:
            lines.append(f"Mode: {_clip(self.mode_line, limit=180)}")
        if self.current_step and not self.narrator_lines and not (
            narrator_mode
            and (
                self.current_step.startswith("Narrator")
                or self.current_step.startswith("Progress summary")
            )
        ):
            lines.append(f"Current: {_clip(self.current_step, limit=120)}")
        if self.workspace and not self.narrator_lines:
            lines.append(f"Context: workspace={self.workspace}")
        if self.model and not self.narrator_lines:
            lines.append(f"Context: model={self.model}")
        if self.queue_status and not self.narrator_lines:
            lines.append(f"Queue: {self.queue_status}")
        if narrator_mode and self.answer_lines:
            lines.append("Answer:")
            for item in self.answer_lines[:_ANSWER_LINE_COUNT_LIMIT]:
                lines.append(f"  {item}")
        if not narrator_mode and self.answer_lines:
            lines.append("Answer:")
            for item in self.answer_lines[:_ANSWER_LINE_COUNT_LIMIT]:
                lines.append(_tui_list_item(item, indent="  "))
        visible_narrator = self._visible_narrator_lines(limit=5)
        if not narrator_mode and visible_narrator:
            lines.append("Narrator:")
            for item in visible_narrator:
                lines.append(_tui_list_item(item, indent="  "))
        activity = [
            item
            for item in (self.activity_lines[-12:] if self.activity_lines else self.timeline[-12:])
            if item and not item.startswith("You asked:")
        ]
        if activity and not narrator_mode and not self.narrator_lines:
            lines.append("Activity:")
            lines.extend(_tui_list_item(item, indent="  ") for item in activity[-10:])
        result_lines = self._result_event_lines(include_answers=not narrator_mode)
        if result_lines and not (self.narrator_lines and not self.debug_events):
            lines.append("Result:")
            for item in result_lines[-9:]:
                lines.append(_tui_list_item(item, indent="  "))
        if self.event_log_path and not self.narrator_lines:
            lines.append(f"Trace: {self.event_log_path}")
        if not self.narrator_lines:
            for trace in self.trace_lines[-3:]:
                lines.append(f"Trace: {trace}")
        footer = self.elapsed_footer()
        if footer:
            lines.append(footer)
        return lines[-80:] or ["Waiting for events."]

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

        panel_title = (
            "Raw Events"
            if self.debug_events
            else "Summary"
            if self.is_terminal()
            else "Progress"
            if self.is_narrator_mode()
            else "Recent Events"
        )
        return Group(
            Panel(header, border_style="cyan"),
            Panel(recent, title=panel_title, border_style="magenta"),
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
        debug_events: bool = False,
    ) -> None:
        self.enabled = bool(enabled)
        self.state = SuperTuiState(objective=objective, workspace=workspace, debug_events=debug_events)
        self._plain = bool(plain)
        self._force_rich = bool(force_rich)
        self._console = None
        self._live = None
        self._live_cls = None
        self._rich_enabled = False
        self._last_line = ""
        self._last_narrator_line = ""
        self._sidecar_args: argparse.Namespace | None = None
        self._sidecar_thread: threading.Thread | None = None
        self._sidecar_lock = threading.Lock()
        self._sidecar_last_started_at = 0.0
        self._sidecar_last_snapshot_id = ""
        self._quiet_narrator_interval_seconds = 10.0
        self._quiet_narrator_last_started_at = 0.0
        self._quiet_narrator_last_signature = ""
        self._clock_stop = threading.Event()
        self._clock_thread: threading.Thread | None = None
        self._clock_last_footer = ""
        self._clock_interval_seconds = 1.0
        self._clock_line_active = False

    def configure_model_sidecar(self, args: argparse.Namespace) -> None:
        self._sidecar_args = args

    def __enter__(self) -> "SuperTuiProgressRenderer":
        if not self.enabled:
            return self
        if not self._plain and self._force_rich:
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
                console=self._console,
                refresh_per_second=8,
                transient=False,
                get_renderable=self.state.rich_renderable,
            )
            self._live.start()
        else:
            print("Super DAN TUI", flush=True)
            if self.state.objective:
                print(f"objective: {_clip(self.state.objective, limit=140)}", flush=True)
            if self.state.mode_line:
                print(f"mode: {_clip(self.state.mode_line, limit=180)}", flush=True)
        self._start_clock()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._stop_clock()
        self._clear_clock_line()
        if self._live is not None:
            self._live.stop()
            self._live = None

    def _start_clock(self) -> None:
        if not self.enabled:
            return
        self._clock_stop.clear()
        self._clock_thread = threading.Thread(target=self._clock_loop, daemon=True)
        self._clock_thread.start()

    def _stop_clock(self) -> None:
        self._clock_stop.set()
        if self._clock_thread is not None and self._clock_thread.is_alive():
            self._clock_thread.join(timeout=0.5)
        self._clock_thread = None

    def _clock_loop(self) -> None:
        while not self._clock_stop.wait(self._clock_interval_seconds):
            self._clock_tick_once()

    def _clock_tick_once(self) -> None:
        if not self.enabled or self.state.debug_events or self.state.is_terminal():
            return
        if not self.state.started_at_monotonic:
            return
        now = time.monotonic()
        self._maybe_start_quiet_model_narrator(now)
        footer = self.state.elapsed_footer()
        if not footer or footer == self._clock_last_footer:
            return
        self._clock_last_footer = footer
        if self._rich_enabled and self._live is not None:
            self._live.refresh()
            return
        self._write_clock_line(footer)

    def _write_clock_line(self, footer: str) -> None:
        rendered = f"{_TUI_STREAM_PREFIX} {str(footer or '').strip()}"
        sys.stdout.write("\r\x1b[2K" + rendered)
        sys.stdout.flush()
        self._clock_line_active = True

    def _clear_clock_line(self) -> None:
        if not self._clock_line_active:
            return
        sys.stdout.write("\r\x1b[2K")
        sys.stdout.flush()
        self._clock_line_active = False

    def __call__(self, event: dict[str, Any]) -> None:
        if not self.enabled:
            return
        event_name = str(event.get("event") or "").strip()
        previous_narrator_count = len(self.state.narrator_lines)
        line = self.state.observe(event)
        if event_name == "super.heartbeat" and self._model_sidecar_enabled():
            self._drop_latest_deterministic_heartbeat(previous_narrator_count)
        self._maybe_start_model_sidecar(event)
        if self.state.debug_events and self.state.raw_events:
            line = self.state.raw_events[-1]
        if self._rich_enabled and self._live is not None:
            self._live.refresh()
            return
        if not self.state.debug_events and self.state.narrator_lines:
            narrator_line = self.state.narrator_lines[-1]
            latest_report = self.state.narrator_reports[-1] if self.state.narrator_reports else {}
            if isinstance(latest_report, dict) and latest_report.get("kind") == "final":
                return
            if narrator_line and narrator_line != self._last_narrator_line:
                self._last_narrator_line = narrator_line
                self._last_line = narrator_line
                self._clear_clock_line()
                _print_tui_stream_line(narrator_line, plain=self._plain)
            return
        if self.state.debug_events and line and line != self._last_line:
            self._last_line = line
            self._clear_clock_line()
            _print_tui_stream_line(line, plain=self._plain)

    def _model_sidecar_enabled(self) -> bool:
        args = self._sidecar_args
        return bool(args is not None and not self.state.debug_events and getattr(args, "_tui_routed_with_model", False))

    def _drop_latest_deterministic_heartbeat(self, previous_narrator_count: int) -> None:
        if len(self.state.narrator_lines) <= previous_narrator_count:
            return
        if not self.state.narrator_reports:
            return
        latest = self.state.narrator_reports[-1]
        if not isinstance(latest, dict) or latest.get("kind") != "heartbeat":
            return
        text = str(latest.get("text") or "").strip()
        if text and self.state.narrator_lines and self.state.narrator_lines[-1] == text:
            self.state.narrator_lines.pop()
        self.state.narrator_reports.pop()
        self.state._last_narrator_report_key = ""

    def _quiet_narrator_signature(self) -> str:
        payload = {
            "phase": self.state.phase,
            "current_step": self.state.current_step,
            "source_event_count": len(self.state.raw_events),
            "changed_files": list(self.state.changed_files[-4:]),
            "artifacts": list(self.state.artifacts[-4:]),
            "validation": self.state.validation,
            "validation_score": self.state.validation_score,
            "blockers": list(self.state.blockers[-3:]),
            "queue_status": self.state.queue_status,
        }
        return json.dumps(payload, sort_keys=True, default=str)

    def _maybe_start_quiet_model_narrator(self, now: float) -> None:
        args = self._sidecar_args
        if not self._model_sidecar_enabled() or self.state.is_terminal():
            return
        elapsed = now - float(self.state.started_at_monotonic or now)
        if elapsed < self._quiet_narrator_interval_seconds:
            return
        if now - self._quiet_narrator_last_started_at < self._quiet_narrator_interval_seconds:
            return
        signature = self._quiet_narrator_signature()
        if signature and signature == self._quiet_narrator_last_signature:
            return
        event = {
            "event": "super.heartbeat",
            "phase": self.state.phase or "model",
            "detail": self.state.current_step,
            "elapsed_seconds": elapsed,
        }
        if self._maybe_start_model_sidecar(event, force=True):
            self._quiet_narrator_last_started_at = now
            self._quiet_narrator_last_signature = signature

    def _maybe_start_model_sidecar(self, event: Mapping[str, Any], *, force: bool = False) -> bool:
        args = self._sidecar_args
        if args is None or self.state.debug_events:
            return False
        if not bool(getattr(args, "_tui_routed_with_model", False)):
            return False
        name = str(event.get("event") or "").strip()
        trigger = _narrator_trigger_for_event(name, event)
        if trigger not in {"opening", "progress", "checkpoint", "blocker", "heartbeat"}:
            return False
        snapshot = self.state._narrator_snapshot()
        if not snapshot.has_run_context:
            return False
        now = time.monotonic()
        with self._sidecar_lock:
            if self._sidecar_thread is not None and self._sidecar_thread.is_alive():
                return False
            if not force and snapshot.snapshot_id and snapshot.snapshot_id == self._sidecar_last_snapshot_id:
                return False
            if not force and trigger not in {"opening", "blocker", "heartbeat"} and now - self._sidecar_last_started_at < 8.0:
                return False
            self._sidecar_last_started_at = now
            self._sidecar_last_snapshot_id = snapshot.snapshot_id
            request = NarratorRequest(
                request_id=f"tui-sidecar-{int(time.time() * 1000)}",
                question=self.state.objective or "Explain current executor progress.",
                snapshot=snapshot,
                surface="super-tui",
                max_tokens=420,
            )
            thread = threading.Thread(
                target=self._run_model_sidecar,
                args=(args, request, trigger),
                daemon=True,
            )
            self._sidecar_thread = thread
            thread.start()
            return True

    def _run_model_sidecar(self, args: argparse.Namespace, request: NarratorRequest, trigger: str) -> None:
        with self._sidecar_lock:
            recent_narrator = tuple(self.state.narrator_lines[-3:])
        text, _error = _run_tui_narrator_model_text(
            args,
            request,
            purpose=f"executor-{trigger}",
            recent_narrator=recent_narrator,
        )
        if not text:
            return
        lines = _split_answer_lines(text, limit=4)
        if not lines:
            return
        with self._sidecar_lock:
            if self.state.is_terminal():
                return
            current_snapshot = self.state._narrator_snapshot()
            if (
                request.snapshot.snapshot_id
                and current_snapshot.snapshot_id
                and request.snapshot.snapshot_id != current_snapshot.snapshot_id
            ):
                self.state.narrator_reports.append(
                    {
                        "kind": "model",
                        "text": "\n".join(lines),
                        "stale": True,
                        "source_snapshot_id": request.snapshot.snapshot_id,
                        "current_snapshot_id": current_snapshot.snapshot_id,
                    }
                )
                if len(self.state.narrator_reports) > 10:
                    del self.state.narrator_reports[: len(self.state.narrator_reports) - 10]
                return
            block_text = "\n".join(lines)
            _append_unique(self.state.narrator_lines, block_text, limit=10)
            self.state.narrator_reports.append(
                {
                    "kind": "model",
                    "text": block_text,
                    "source_snapshot_id": request.snapshot.snapshot_id,
                }
            )
            if len(self.state.narrator_reports) > 10:
                del self.state.narrator_reports[: len(self.state.narrator_reports) - 10]
        if self._rich_enabled and self._live is not None:
            self._live.refresh()
            return
        self._clear_clock_line()
        _print_tui_stream_block("Narrator", lines, plain=self._plain)

    def note(self, line: str) -> None:
        text = str(line or "").strip()
        if not self.enabled or not text:
            return
        _append_unique(self.state.recent, text, limit=8)
        _append_unique(self.state.activity_lines, text, limit=12)
        if self._rich_enabled and self._live is not None:
            self._live.refresh()
            return
        if text != self._last_line:
            self._last_line = text
            self._clear_clock_line()
            _print_tui_stream_line(text, plain=self._plain)

    def print_live_report(self, report: Any, live_result: dict[str, Any], *, verbose: bool = False) -> None:
        del report, verbose
        self.state.apply_live_result(live_result)
        self.print_current_live_report()

    def print_current_live_report(self) -> None:
        if self._rich_enabled and self._live is not None:
            self._live.refresh()
            return
        if self.state.debug_events:
            self._clear_clock_line()
            print(self.state.plain_snapshot(), flush=True)
            return
        answer = self.state.final_answer_lines()
        self._clear_clock_line()
        if answer:
            _print_tui_stream_block("Answer", answer, plain=self._plain)
        outcome = self.state.final_outcome_lines(include_trace=False)
        if outcome:
            _print_tui_stream_block("Outcome", outcome, plain=self._plain)
        footer = self.state.elapsed_footer()
        if footer:
            _print_tui_stream_line(footer, plain=self._plain)


def build_parser() -> argparse.ArgumentParser:
    parser = super_cli.build_parser()
    parser.prog = "dan-super-tui"
    parser.description = (
        "Run Super DAN through a terminal UI. This is a sibling surface to "
        "dan-super-organism and reuses the same live runner and event logs."
    )
    parser.epilog = (
        "Interactive commands: /plan, /progress, /last, /status, /skills, /reset [all|state], /help, /exit. "
        "Typing / opens command suggestions and typing $ opens skill suggestions when prompt_toolkit is available. "
        "Explicit slash commands route locally; natural-language input is model-routed as narrator read-only, executor read-only, executor write, or clarification. "
        "Progress/status questions use the snapshot-only narrator lane; workspace inspection stays read-only; "
        "write requests use direct simple writes or the normal Super DAN execution path. "
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
    parser.add_argument(
        "--raw-events",
        action="store_true",
        help="Show raw event names/tool metadata instead of the default conversational timeline.",
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
        debug_events=bool(getattr(args, "raw_events", False)),
    )
    renderer.state.set_intent_decision(getattr(args, "_tui_intent_decision", None))
    renderer.configure_model_sidecar(args)
    for token in getattr(args, "_tui_selected_skill_mentions", []) or []:
        _append_unique(renderer.state.recent, f"skill selected: ${token}", limit=8)

    def _renderer_factory(*, enabled: bool, args: argparse.Namespace) -> SuperTuiProgressRenderer:
        del enabled, args
        return renderer

    args._progress_renderer_factory = _renderer_factory

    def _live_report_printer(report: Any, live_result: dict[str, Any], *, verbose: bool = False) -> None:
        del report, verbose
        renderer.state.apply_live_result(live_result)
        _run_tui_final_model_answer(args, renderer.state)
        renderer.print_current_live_report()

    args._live_report_printer = _live_report_printer
    with renderer:
        exit_code = super_cli._run_super_turn(args, parser)
    transcript_workspace = str(getattr(args, "_tui_transcript_workspace", "") or "").strip()
    if transcript_workspace:
        _append_tui_transcript_entry(
            Path(transcript_workspace),
            role="assistant_final",
            text=renderer.state.transcript_summary(exit_code=exit_code),
            metadata={
                "status": renderer.state.status,
                "turn_id": renderer.state.task_id,
                "event_log_path": renderer.state.event_log_path,
                "narrator_final_summary": _latest_final_narrator_summary(renderer.state),
                "lane": getattr(getattr(args, "_tui_intent_decision", None), "lane", ""),
                "intent_rationale": getattr(getattr(args, "_tui_intent_decision", None), "rationale", ""),
            },
        )
    return exit_code


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


def _render_static_state(state: SuperTuiState, *, plain: bool, raw_events: bool = False) -> None:
    state.debug_events = bool(raw_events)
    if state.is_terminal() and not state.debug_events:
        answer = state.final_answer_lines()
        if answer:
            _print_tui_stream_block("Answer", answer, plain=plain)
        outcome = state.final_outcome_lines(include_trace=False)
        if outcome:
            _print_tui_stream_block("Outcome", outcome, plain=plain)
        footer = state.elapsed_footer()
        if footer:
            _print_tui_stream_line(footer, plain=plain)
        return
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


def _latest_final_narrator_summary(state: SuperTuiState) -> str:
    for payload in reversed(state.narrator_reports):
        if not isinstance(payload, dict):
            continue
        if payload.get("kind") != "final":
            continue
        text = str(payload.get("text") or "").strip()
        if text:
            return text
    return ""


def _resolve_transcript_event_log_path(workspace_root: Path, entry: TuiTranscriptEntry) -> Path | None:
    raw = str(entry.metadata.get("event_log_path") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = workspace_root / path
    return path


def _latest_super_event_log(workspace_root: Path) -> Path | None:
    runs_root = workspace_root / ".dan-super" / "runs"
    if not runs_root.exists():
        return None
    try:
        paths = [path for path in runs_root.glob("*/events.jsonl") if path.is_file()]
    except OSError:
        return None
    if not paths:
        return None
    return max(paths, key=lambda path: path.stat().st_mtime)


def _load_narrator_source_state(
    workspace_root: Path,
    transcript_entries: Sequence[TuiTranscriptEntry],
) -> tuple[SuperTuiState, int]:
    state = SuperTuiState(workspace=str(workspace_root))
    event_log_path: Path | None = None
    for entry in reversed(transcript_entries):
        candidate = _resolve_transcript_event_log_path(workspace_root, entry)
        if candidate is not None and candidate.exists():
            event_log_path = candidate
            break
    if event_log_path is None:
        candidate = _latest_super_event_log(workspace_root)
        if candidate is not None and candidate.exists():
            event_log_path = candidate
    if event_log_path is None:
        state.status = "idle"
        state.phase = "waiting"
        state.current_step = "No active run snapshot"
        return state, 0
    rows = _read_event_log(event_log_path)
    for row in rows:
        state.observe(row)
    if not state.event_log_path:
        state.event_log_path = str(event_log_path)
    return state, len(rows)


def _latest_transcript_narrator_answer(
    transcript_entries: Sequence[TuiTranscriptEntry],
    *,
    snapshot: RunNarratorSnapshot | None = None,
) -> list[str]:
    for entry in reversed(transcript_entries):
        if entry.role != "assistant_narrator":
            continue
        if snapshot is not None:
            entry_log = str(entry.metadata.get("event_log_path") or "").strip()
            if snapshot.trace_refs and not entry_log:
                continue
            if entry_log and snapshot.trace_refs and entry_log not in set(snapshot.trace_refs):
                continue
        lines = _split_answer_lines(entry.text, limit=_ANSWER_LINE_COUNT_LIMIT)
        if lines:
            return lines
    return []


def _build_tui_narrator_snapshot(workspace_root: Path) -> RunNarratorSnapshot:
    transcript_entries = _read_tui_transcript(workspace_root, limit=16)
    source_state, event_count = _load_narrator_source_state(workspace_root, transcript_entries)
    latest_narrator_answer = _latest_transcript_narrator_answer(transcript_entries)
    trace_refs = [source_state.event_log_path] if source_state.event_log_path else []
    trace_refs.extend(source_state.trace_lines[-3:])
    elapsed = 0.0
    if source_state.started_at_monotonic:
        end = source_state.ended_at_monotonic or time.monotonic()
        elapsed = max(0.0, end - source_state.started_at_monotonic)
    has_run_context = bool(event_count or source_state.task_id or source_state.objective)
    recent_events = tuple(source_state.recent_event_lines()[-10:]) if has_run_context else ()
    if latest_narrator_answer:
        recent_events = tuple([f"Latest narrator answer: {line}" for line in latest_narrator_answer[-4:]]) + recent_events
    activity = tuple(source_state.activity_lines[-12:] or source_state.timeline[-12:]) if has_run_context else ()
    results = tuple(source_state._result_event_lines()[-12:]) if has_run_context else ()
    snapshot_id = ":".join(
        part
        for part in (
            source_state.task_id or "tui",
            str(event_count),
            source_state.status,
            source_state.phase,
            str(len(transcript_entries)),
        )
        if part
    )
    return RunNarratorSnapshot(
        snapshot_id=snapshot_id,
        run_id=source_state.task_id,
        task_id=source_state.task_id,
        objective=source_state.objective,
        workspace=source_state.workspace or str(workspace_root),
        status=source_state.status,
        phase=source_state.phase,
        current_step=source_state.current_step,
        elapsed_seconds=elapsed,
        model=source_state.model,
        mode_line=source_state.mode_line,
        recent_events=recent_events,
        activity=activity,
        results=results,
        changed_files=tuple(source_state.changed_files[-8:]),
        artifacts=tuple(source_state.artifacts[-8:]),
        validation=source_state.validation,
        validation_score=source_state.validation_score,
        blockers=tuple(source_state.blockers[-6:]),
        queued_work=source_state.queue_status,
        trace_refs=tuple(path for path in trace_refs if path),
        transcript_tail=tuple(
            TranscriptRef(role=entry.role, text=entry.text, created_at=entry.created_at)
            for entry in transcript_entries[-6:]
        ),
        source_event_count=event_count,
    )


def _build_tui_narrator_live_provider(args: argparse.Namespace, model: str) -> Any:
    return super_cli._build_live_provider(
        model,
        api_key=getattr(args, "api_key", None),
        base_url=getattr(args, "base_url", None),
    )


def _build_tui_intent_router_live_provider(args: argparse.Namespace, model: str) -> Any:
    return super_cli._build_live_provider(
        model,
        api_key=getattr(args, "api_key", None),
        base_url=getattr(args, "base_url", None),
    )


def _build_tui_final_answer_live_provider(args: argparse.Namespace, model: str) -> Any:
    return super_cli._build_live_provider(
        model,
        api_key=getattr(args, "api_key", None),
        base_url=getattr(args, "base_url", None),
    )


def _tui_narrator_model_messages(
    request: NarratorRequest,
    *,
    purpose: str = "answer",
    recent_narrator: Sequence[str] = (),
) -> list[dict[str, str]]:
    is_heartbeat = "heartbeat" in str(purpose or "").lower()
    recent = "\n".join(f"- {line}" for line in recent_narrator if str(line or "").strip()) or "- none"
    if is_heartbeat:
        style_instruction = (
            "This is a quiet heartbeat during a wait. Write exactly one short natural sentence. "
            "Do not relist stable files, folders, or facts that appeared in recent narrator messages. "
            "Say only what changed since the last visible update, or if nothing changed, say what the executor is still waiting on in plain words."
        )
    else:
        style_instruction = (
            "Use one concise paragraph; add bullets only when they materially improve readability. "
            "Avoid repeating stable facts already present in recent narrator messages unless they changed."
        )
    return [
        {
            "role": "system",
            "content": (
                "You are the narrator voice for Super DAN TUI. Use only the sanitized run snapshot below. "
                "Do not call tools, do not claim to inspect files now, do not steer the executor, and do not invent hidden state. "
                "Write like a Codex-style assistant: direct, natural, and grounded. Do not echo the user's question. "
                "Do not output a mechanical field list with labels like Status, Next, Validation, or Blockers unless the user explicitly asks for a raw status report. "
                "Explain what the executor appears to be doing or has finished, why it matters for the request, what remains, and the most useful next step. "
                "For quiet heartbeat purposes, say what is visible now and what is still unknown instead of repeating a timer. "
                "If the visible executor activity appears unrelated to the user's request, say that as possible drift without trying to correct or steer it. "
                f"{style_instruction}"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Purpose: {purpose}\n"
                f"Recent narrator messages to avoid repeating:\n{recent}\n\n"
                f"{request.to_prompt_text()}\n\n"
                "Write the narrator response now."
            ),
        },
    ]


def _run_tui_narrator_model_text(
    args: argparse.Namespace,
    request: NarratorRequest,
    *,
    purpose: str = "answer",
    recent_narrator: Sequence[str] = (),
) -> tuple[str, str]:
    try:
        model = super_cli._resolve_live_model(str(getattr(args, "model", "") or ""))
    except ValueError as exc:
        return "", str(exc)

    async def _run() -> str:
        provider = _build_tui_narrator_live_provider(args, model)
        try:
            response = await provider.complete(
                messages=_tui_narrator_model_messages(
                    request,
                    purpose=purpose,
                    recent_narrator=recent_narrator,
                ),
                model=model,
                temperature=0.3,
                max_tokens=min(int(getattr(request, "max_tokens", 700) or 700), 700),
            )
            return str(getattr(response, "text", "") or "").strip()
        finally:
            await super_cli._close_live_provider(provider)

    try:
        text = asyncio.run(_run())
    except Exception as exc:
        return "", str(exc)
    lines = _split_answer_lines(text, limit=_ANSWER_LINE_COUNT_LIMIT)
    if not _answer_lines_have_useful_prose(lines):
        return "", "model returned no usable narrator prose"
    return "\n".join(lines), ""


def _route_tui_intent_with_model(args: argparse.Namespace) -> tuple[TuiIntentDecision, bool]:
    target = str(getattr(args, "target", "") or "").strip()
    try:
        model = super_cli._resolve_live_model(str(getattr(args, "model", "") or ""))
    except ValueError as exc:
        return (
            TuiIntentDecision(
                permission="",
                complexity="",
                confidence=0.0,
                rationale="model router unavailable",
                clarification=f"Model-assisted routing is required for free text, but no route model is configured: {_clip(exc, limit=160)}",
            ),
            False,
        )

    transcript_workspace = str(getattr(args, "_tui_transcript_workspace", "") or "").strip()
    transcript_tail = _read_tui_transcript(Path(transcript_workspace), limit=6) if transcript_workspace else []
    selected_skills = list(getattr(args, "_tui_selected_skill_mentions", []) or [])

    async def _run() -> Any:
        provider = _build_tui_intent_router_live_provider(args, model)
        try:
            return await route_agent_turn_intent_with_model(
                provider,
                target,
                model=model,
                transcript_tail=transcript_tail,
                selected_skills=selected_skills,
                surface="super-tui",
            )
        finally:
            await super_cli._close_live_provider(provider)

    try:
        core_decision = _run_with_tui_working_clock(args, lambda: asyncio.run(_run()))
    except Exception as exc:
        return (
            TuiIntentDecision(
                permission="",
                complexity="",
                confidence=0.0,
                rationale="model router failed",
                clarification=f"I could not route that request safely: {_clip(exc, limit=180)}",
            ),
            False,
        )
    return _tui_decision_from_core_decision(core_decision), True


def _tui_final_answer_messages(state: SuperTuiState) -> list[dict[str, str]]:
    changed = "\n".join(f"- {path}" for path in state.changed_files[-8:]) or "- none"
    artifacts = "\n".join(f"- {path}" for path in state.artifacts[-8:]) or "- none"
    blockers = "\n".join(f"- {item}" for item in state.blockers[-6:]) or "- none"
    recent = "\n".join(f"- {line}" for line in state.narrator_lines[-6:]) or "- none"
    validation = state.validation or "unknown"
    if state.validation_score:
        validation = f"{validation} ({state.validation_score})"
    return [
        {
            "role": "system",
            "content": (
                "You are the final answer writer for Super DAN TUI. "
                "Use only the sanitized run summary below. Do not call tools, do not claim to inspect files now, and do not invent work. "
                "Write naturally to the user. Avoid formulaic wording like 'Completed <original request>'. "
                "Mention the concrete outcome, important changed paths, validation, and any useful next step. "
                "Keep it concise: one short paragraph plus bullets only if they improve readability."
            ),
        },
        {
            "role": "user",
            "content": (
                f"User request: {state.objective or '(unknown)'}\n"
                f"Status: {state.status or 'unknown'}\n"
                f"Validation: {validation}\n"
                f"Elapsed: {state.elapsed_footer() or 'unknown'}\n"
                f"Changed files:\n{changed}\n\n"
                f"Artifacts:\n{artifacts}\n\n"
                f"Blockers:\n{blockers}\n\n"
                f"Recent run summary:\n{recent}\n\n"
                "Write the final answer now."
            ),
        },
    ]


def _run_tui_final_model_answer(args: argparse.Namespace, state: SuperTuiState) -> bool:
    if state.debug_events or not bool(getattr(args, "_tui_routed_with_model", False)):
        return False
    try:
        model = super_cli._resolve_live_model(str(getattr(args, "model", "") or ""))
    except ValueError:
        return False

    async def _run() -> str:
        provider = _build_tui_final_answer_live_provider(args, model)
        try:
            response = await provider.complete(
                messages=_tui_final_answer_messages(state),
                model=model,
                temperature=0.3,
                max_tokens=700,
            )
            return str(getattr(response, "text", "") or "").strip()
        finally:
            await super_cli._close_live_provider(provider)

    try:
        text = _run_with_tui_working_clock(args, lambda: asyncio.run(_run()))
    except Exception:
        return False
    lines = _split_answer_lines(text, limit=_ANSWER_LINE_COUNT_LIMIT)
    if not _answer_lines_have_useful_prose(lines):
        return False
    state.answer_lines.clear()
    for line in lines:
        state._record_answer(line)
    return True


def _run_tui_narrator_model_answer(
    args: argparse.Namespace,
    request: NarratorRequest,
    state: SuperTuiState,
) -> bool:
    state.current_step = "Narrator is preparing an answer"
    text, error = _run_tui_narrator_model_text(args, request, purpose="answer")
    if error:
        state._record_result(f"Narrator model failed: {_clip(error, limit=180)}")
    if not text:
        return False
    state.answer_lines.clear()
    for line in _split_answer_lines(text):
        state._record_answer(line)
    return True


def _wait_for_tui_narrator_model(args: argparse.Namespace, state: SuperTuiState, thread: threading.Thread) -> None:
    if not thread.is_alive():
        return
    last_footer = ""
    clock_line_active = False
    while thread.is_alive():
        footer = state.elapsed_footer()
        if footer and footer != last_footer:
            rendered = f"{_TUI_STREAM_PREFIX} {footer}"
            sys.stdout.write("\r\x1b[2K" + rendered)
            sys.stdout.flush()
            clock_line_active = True
            last_footer = footer
        time.sleep(0.25)
    if clock_line_active:
        sys.stdout.write("\r\x1b[2K")
        sys.stdout.flush()


def _run_tui_narrator(
    args: argparse.Namespace,
    decision: TuiIntentDecision,
    *,
    use_model_loop: bool = False,
) -> int:
    workspace_root = normalize_workspace_root(str(args.workspace))
    objective = str(getattr(args, "target", "") or "").strip()
    snapshot = _build_tui_narrator_snapshot(workspace_root)
    state = SuperTuiState(
        objective=objective,
        workspace=str(workspace_root),
        status="running",
        phase=NARRATOR_READ_ONLY,
        debug_events=bool(getattr(args, "raw_events", False)),
    )
    state.started_at_monotonic = time.monotonic()
    state.set_intent_decision(decision)
    state.current_step = "Progress summary requested"
    state._record_progress("Progress summary requested from the current run snapshot.")
    if snapshot.trace_refs:
        state.event_log_path = snapshot.trace_refs[-1]
    request = NarratorRequest(
        request_id=f"tui-narrator-{int(time.time() * 1000)}",
        question=objective,
        snapshot=snapshot,
        surface="super-tui",
    )
    transcript_entries = _read_tui_transcript(workspace_root, limit=16)
    latest_narrator_answer = _latest_transcript_narrator_answer(transcript_entries, snapshot=snapshot)
    if use_model_loop:
        result: dict[str, bool] = {"answered": False}

        def _model_worker() -> None:
            result["answered"] = _run_tui_narrator_model_answer(args, request, state)

        thread = threading.Thread(target=_model_worker, daemon=True)
        thread.start()
        _wait_for_tui_narrator_model(args, state, thread)
        thread.join()
        model_answered = result["answered"]
    else:
        model_answered = False
    if not model_answered and latest_narrator_answer:
        state.answer_lines.clear()
        for line in latest_narrator_answer:
            state._record_answer(line)
        state._record_progress("Reused the latest visible narrator answer from this TUI session.")
        model_answered = True
    if not model_answered:
        fallback = deterministic_narrator_response(request)
        for line in _split_answer_lines(fallback.text):
            state._record_answer(line)
        if bool(getattr(args, "raw_events", False)):
            state._record_progress("Narrator fallback summary ready.")
    state.status = "completed"
    state.phase = "done"
    state.current_step = "Narrator answer ready"
    state.ended_at_monotonic = time.monotonic()
    _render_static_state(
        state,
        plain=bool(getattr(args, "plain", False)),
        raw_events=bool(getattr(args, "raw_events", False)),
    )
    transcript_workspace = str(getattr(args, "_tui_transcript_workspace", "") or "").strip()
    if transcript_workspace:
        _append_tui_transcript_entry(
            Path(transcript_workspace),
            role="assistant_narrator",
            text="\n".join(state.answer_lines) or state.transcript_summary(exit_code=0),
            metadata={
                "status": state.status,
                "lane": decision.lane,
                "intent_rationale": decision.rationale,
                "snapshot_id": snapshot.snapshot_id,
                "event_log_path": snapshot.trace_refs[-1] if snapshot.trace_refs else "",
                "source_event_count": snapshot.source_event_count,
            },
        )
    return 0


def _run_tui_read_only(
    args: argparse.Namespace,
    decision: TuiIntentDecision,
    *,
    use_model_loop: bool = False,
) -> int:
    workspace_root = normalize_workspace_root(str(args.workspace))
    objective = str(getattr(args, "target", "") or "").strip()
    state = SuperTuiState(
        objective=objective,
        workspace=str(workspace_root),
        status="running",
        phase=decision.lane,
        debug_events=bool(getattr(args, "raw_events", False)),
    )
    state.started_at_monotonic = time.monotonic()
    state.set_intent_decision(decision)
    state.current_step = "Read-only request"
    state._record_progress("Read-only route selected.")
    model_answered = False
    if decision.complexity == "complex" and use_model_loop:
        model_answered = _run_tui_read_only_model_answer(args, decision, state)
    if not model_answered:
        if use_model_loop:
            state._record_answer("I could not get a usable model-written answer from the read-only review. No files were changed.")
        else:
            activity, answer = _build_read_only_answer(workspace_root, objective, decision)
            for line in activity:
                state._record_progress(line)
                _emit_tui_stream_line(args, line)
            for line in answer:
                state._record_answer(line)
    state.status = "completed"
    state.phase = "done"
    state.current_step = "Read-only answer ready"
    state.ended_at_monotonic = time.monotonic()
    state._record_progress("Read-only answer completed.")
    if _tui_stream_output(args):
        _emit_tui_stream_answer(args, state)
        footer = state.elapsed_footer()
        if footer:
            _emit_tui_stream_line(args, footer)
    else:
        _render_static_state(
            state,
            plain=bool(getattr(args, "plain", False)),
            raw_events=bool(getattr(args, "raw_events", False)),
        )
    transcript_workspace = str(getattr(args, "_tui_transcript_workspace", "") or "").strip()
    if transcript_workspace:
        _append_tui_transcript_entry(
            Path(transcript_workspace),
            role="assistant_final",
            text=state.transcript_summary(exit_code=0),
            metadata={
                "status": state.status,
                "lane": decision.lane,
                "intent_rationale": decision.rationale,
            },
        )
    return 0


def _run_tui_simple_write(args: argparse.Namespace, decision: TuiIntentDecision) -> int:
    workspace_root = normalize_workspace_root(str(args.workspace))
    objective = str(getattr(args, "target", "") or "").strip()
    state = SuperTuiState(
        objective=objective,
        workspace=str(workspace_root),
        status="running",
        phase=decision.lane,
        debug_events=bool(getattr(args, "raw_events", False)),
    )
    state.started_at_monotonic = time.monotonic()
    state.set_intent_decision(decision)
    state.current_step = "Simple write request"
    state._record_progress("Simple write route selected.")
    plan = _parse_simple_write_request(objective)
    if plan is None:
        state.status = "blocked"
        state.phase = "clarification"
        state.current_step = "Clarification needed"
        state._record_answer("Use an exact operation such as `copy source.md to docs/source.md`.")
    else:
        try:
            activity, results, changed = _run_simple_write_plan(workspace_root, plan)
        except ValueError as exc:
            state.status = "blocked"
            state.phase = "blocked"
            state.current_step = "Simple write blocked"
            state._record_result(f"Blocked: {exc}")
        except OSError as exc:
            state.status = "failed"
            state.phase = "failed"
            state.current_step = "Simple write failed"
            state._record_result(f"Write failed: {exc}")
        else:
            for line in activity:
                state._record_progress(line)
            for path in changed:
                _append_unique(state.changed_files, path)
            for line in results:
                state._record_result(line)
            state.status = "completed"
            state.phase = "done"
            state.current_step = "Simple write completed"
    state.ended_at_monotonic = time.monotonic()
    if state.status == "running":
        state.status = "completed"
        state.phase = "done"
    _render_static_state(
        state,
        plain=bool(getattr(args, "plain", False)),
        raw_events=bool(getattr(args, "raw_events", False)),
    )
    transcript_workspace = str(getattr(args, "_tui_transcript_workspace", "") or "").strip()
    if transcript_workspace:
        _append_tui_transcript_entry(
            Path(transcript_workspace),
            role="assistant_final",
            text=state.transcript_summary(exit_code=0 if state.status == "completed" else 1),
            metadata={
                "status": state.status,
                "lane": decision.lane,
                "intent_rationale": decision.rationale,
            },
        )
    return 0 if state.status == "completed" else 1


def _render_tui_clarification(args: argparse.Namespace, decision: TuiIntentDecision) -> int:
    workspace_root = normalize_workspace_root(str(args.workspace))
    objective = str(getattr(args, "target", "") or "").strip()
    state = SuperTuiState(
        objective=objective,
        workspace=str(workspace_root),
        status="blocked",
        phase="clarification",
        debug_events=bool(getattr(args, "raw_events", False)),
    )
    state.started_at_monotonic = time.monotonic()
    state.ended_at_monotonic = state.started_at_monotonic
    state.set_intent_decision(decision)
    state.current_step = "Clarification needed"
    state._record_answer(decision.clarification or "Please clarify the intended action.")
    _render_static_state(
        state,
        plain=bool(getattr(args, "plain", False)),
        raw_events=bool(getattr(args, "raw_events", False)),
    )
    transcript_workspace = str(getattr(args, "_tui_transcript_workspace", "") or "").strip()
    if transcript_workspace:
        _append_tui_transcript_entry(
            Path(transcript_workspace),
            role="assistant_final",
            text=decision.clarification or "Clarification needed.",
            metadata={
                "status": state.status,
                "lane": "clarification",
                "intent_rationale": decision.rationale,
            },
        )
    return 0


def _dispatch_tui_turn(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    *,
    force_live: bool = False,
) -> int:
    core_decision = classify_agent_turn_intent(str(getattr(args, "target", "") or ""))
    routed_with_model = False
    if core_decision.needs_clarification:
        decision, routed_with_model = _route_tui_intent_with_model(args)
    else:
        decision = _tui_decision_from_core_decision(core_decision)
    setattr(args, "_tui_routed_with_model", routed_with_model)
    setattr(args, "_tui_intent_decision", decision)
    if decision.needs_clarification:
        return _render_tui_clarification(args, decision)
    if decision.complexity == "narrator" and not bool(getattr(args, "plan_only", False)):
        use_model_loop = bool(routed_with_model or force_live or getattr(args, "live", False) or getattr(args, "_model_explicit", False))
        return _run_tui_narrator(args, decision, use_model_loop=use_model_loop)
    if decision.permission == "read-only" and not bool(getattr(args, "plan_only", False)):
        use_model_loop = bool(routed_with_model or force_live or getattr(args, "live", False) or getattr(args, "_model_explicit", False))
        return _run_tui_read_only(args, decision, use_model_loop=use_model_loop)
    if decision.lane == "simple write" and not bool(getattr(args, "plan_only", False)):
        return _run_tui_simple_write(args, decision)
    return _run_tui_turn(args, parser, force_live=bool(force_live or routed_with_model))


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
                body.append("Commands: /plan, /progress, /last, /status, /skills, /reset, /help, /exit\n", style="cyan")
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
    print("  Commands: /plan, /progress, /last, /status, /skills, /reset, /help, /exit", flush=True)
    print(f"  Skill suggestions: {skill_count} loaded", flush=True)
    print("  Use / for commands and $ for skills. Enter submits.", flush=True)


def _render_transcript_history(
    workspace_root: Path,
    *,
    plain: bool,
    limit: int = 16,
) -> None:
    entries = _read_tui_transcript(workspace_root, limit=limit)
    if not entries:
        return
    if not plain:
        Console, _ = _try_import_rich()
        if Console is not None:
            try:
                from rich.panel import Panel
                from rich.table import Table

                table = Table.grid(expand=True)
                table.add_column()
                for entry in entries:
                    table.add_row(_rich_semantic_text(_format_transcript_line(entry)))
                console = Console()
                console.print(Panel(table, title="Conversation", border_style="blue"))
                return
            except Exception:
                pass
    print("Conversation", flush=True)
    for entry in entries:
        print(f"  {_format_transcript_line(entry)}", flush=True)


def _reset_tui_context(workspace_root: Path, scope: str = "") -> str:
    normalized_scope = scope.strip().lower()
    result = super_cli._reset_super_context(workspace_root, normalized_scope)
    if normalized_scope in {"state", "queues", "queue"}:
        return result + "\nVisible TUI transcript preserved."
    if result.startswith("Archived Super DAN context:"):
        return result + "\nVisible TUI transcript archived with the Super DAN context."
    return result


def _render_event_log(args: argparse.Namespace) -> int:
    path = Path(str(args.event_log)).expanduser()
    if not path.exists():
        print(f"event log not found: {path}", file=sys.stderr)
        return 2
    state = SuperTuiState(
        workspace=str(normalize_workspace_root(str(args.workspace))),
        debug_events=bool(getattr(args, "raw_events", False)),
    )
    try:
        for row in _read_event_log(path):
            state.observe(row)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"could not read event log: {exc}", file=sys.stderr)
        return 2
    if not state.event_log_path:
        state.event_log_path = str(path)
    _render_static_state(
        state,
        plain=bool(getattr(args, "plain", False)),
        raw_events=bool(getattr(args, "raw_events", False)),
    )
    return 0


def _interactive_loop(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    workspace_root = normalize_workspace_root(str(args.workspace))
    commands = _TUI_COMMANDS
    skills = _load_tui_skill_suggestions(workspace_root)
    _render_transcript_history(
        workspace_root,
        plain=bool(getattr(args, "plain", False)),
    )
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
                "Commands: /plan <objective>, /progress, /last, /status, /skills [filter], /reset [all|state], /exit. "
                "Type / for command suggestions and $ for skill suggestions when prompt_toolkit is available. "
                "Progress/status questions use the narrator lane; workspace questions use read-only executor lanes; writes use Super DAN execution. "
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
            reset_message = _reset_tui_context(workspace_root, reset_scope)
            print(reset_message, flush=True)
            _append_tui_transcript_entry(
                workspace_root,
                role="system_notice",
                text=reset_message,
                metadata={"command": "reset", "scope": reset_scope or "all"},
            )
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
            _append_tui_transcript_entry(
                workspace_root,
                role="user",
                text=objective,
                metadata={"selected_skills": [skill.token for skill in parsed.selected]},
            )
            if parsed.message:
                _append_tui_transcript_entry(
                    workspace_root,
                    role="system_notice",
                    text=parsed.message,
                    metadata={"reason": "skill_selection"},
                )
            continue
        if not str(turn_args.target or "").strip():
            continue
        turn_args.plan_only = plan_only
        turn_args.json = False
        turn_args.output = None
        setattr(turn_args, "_tui_transcript_workspace", str(workspace_root))
        _append_tui_transcript_entry(
            workspace_root,
            role="user",
            text=objective,
            metadata={
                "plan_only": plan_only,
                "selected_skills": list(getattr(turn_args, "_tui_selected_skill_mentions", []) or []),
            },
        )
        try:
            exit_code = _dispatch_tui_turn(turn_args, parser, force_live=not plan_only)
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
    return _dispatch_tui_turn(args, parser)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
