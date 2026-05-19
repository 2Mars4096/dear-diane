"""Terminal UI for Super DAN live runs.

This module is intentionally a sibling surface to ``dan super-organism``.
It reuses the Super DAN runner and event log, but owns terminal rendering.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import contextlib
import copy
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from collections import deque
from collections.abc import Mapping as MappingABC
from collections.abc import Sequence as SequenceABC
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from dan.agent_runtime.progress_narrator import (
    AgentCommunicationPolicy,
    ANSWER_BUDGET_BRIEF,
    ANSWER_BUDGET_DETAILED,
    ANSWER_BUDGET_NORMAL,
    INTERACTION_ANSWER_ONLY,
    INTERACTION_AUTONOMOUS_PROGRESS,
    INTERACTION_REVIEW,
    LATENCY_DEEP,
    LATENCY_FAST,
    NARRATOR_READ_ONLY,
    PLAN_MODE,
    PROGRESS_QUIET,
    PROGRESS_VERBOSE,
    NarratorRequest,
    NarratorReport,
    RunNarratorSnapshot,
    TranscriptRef,
    classify_agent_turn_intent,
    deterministic_narrator_report,
    deterministic_narrator_response,
    generate_narrator_response,
    normalize_agent_communication_policy,
    route_agent_turn_intent_with_model,
    start_narrator_job,
    tokenize_intent_text,
)
from dan.cli import _try_import_rich, load_env, normalize_workspace_root
from dan.cli.super_hooks import format_super_queue_status
from dan.cli import super_organism as super_cli
from dan.providers.multimodal import (
    SUPPORTED_IMAGE_EXTENSIONS,
    image_attachment_payloads,
    image_mime_type,
    is_supported_image_path,
)
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
    ("/tasks", "show active, queued, and recent task board rows"),
    ("/status", "show visible task status"),
    ("/inside", "inspect recent internal activity for a task"),
    ("/skills", "list skill mentions available for $ autocomplete"),
    ("/reset", "archive .dan-super context or state"),
    ("/help", "show shell commands"),
    ("/exit", "leave the TUI"),
    ("/new", "start a separate task turn"),
    ("/append", "add instructions to active async work"),
    ("/pause", "pause active async work when checkpoints allow it"),
    ("/resume", "resume paused async work"),
    ("/stop", "request stop for visible active work"),
    ("/focus", "choose the default visible task target"),
)
_PROMPT_TOOLKIT_FALLBACK_WARNED = False
_TUI_LOCAL_ASYNC_THREADS: list[threading.Thread] = []
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
_TUI_BRAND = "DAN"
_TUI_SEPARATOR = "·"
_TUI_STREAM_PREFIX = _TUI_BRAND
_TUI_STREAM_PREFIX_RE = re.compile(r"^(?:\[tui\]|dan:|DAN(?:\s*[·:])?)\s*")
_ANSWER_LINE_LIMIT = 4000
_ANSWER_LINE_COUNT_LIMIT = 80
_ANSWER_BUDGET_LINE_LIMITS = {
    ANSWER_BUDGET_BRIEF: 5,
    ANSWER_BUDGET_NORMAL: 12,
    ANSWER_BUDGET_DETAILED: _ANSWER_LINE_COUNT_LIMIT,
}
_ANSWER_BUDGET_MAX_TOKENS = {
    ANSWER_BUDGET_BRIEF: 360,
    ANSWER_BUDGET_NORMAL: 700,
    ANSWER_BUDGET_DETAILED: 1400,
}
_RICH_PATH_RE = re.compile(
    r"(?<![\w$])(?:"
    r"~|/|\./|\.\./|\.dan-super/|docs/|src/|tests/|apps/|website/|data/|raw/|figures/|tables/|logs/|beamer/"
    r")[^\s,;:)]+"
    r"|(?<![\w$])[\w.-]+\.(?:py|md|jsonl?|html|css|js|ts|tsx|txt|csv|parquet|tex|pdf|png|jpg|jpeg|svg)(?![\w])"
)
_TUI_ARTIFACT_GROUP_ORDER = ("Figures", "Tables", "Reports", "Data", "Code", "Run metadata", "Files")
_TUI_FIGURE_DIR_PARTS = {"figure", "figures", "fig", "figs", "image", "images", "plot", "plots"}
_TUI_TABLE_DIR_PARTS = {"table", "tables"}
_TUI_REPORT_DIR_PARTS = {"report", "reports", "result", "results", "beamer"}
_TUI_DATA_DIR_PARTS = {"data", "raw", "intermediate", "processed"}
_TUI_FIGURE_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"}


def _tui_policy_payload(policy: AgentCommunicationPolicy | Mapping[str, Any] | None) -> dict[str, Any]:
    if isinstance(policy, AgentCommunicationPolicy):
        return policy.to_payload()
    if isinstance(policy, MappingABC):
        return normalize_agent_communication_policy(policy).to_payload()
    return AgentCommunicationPolicy().to_payload()


def _tui_answer_line_limit(policy: AgentCommunicationPolicy | Mapping[str, Any] | None) -> int:
    payload = _tui_policy_payload(policy)
    budget = str(payload.get("answer_budget") or ANSWER_BUDGET_NORMAL)
    return int(_ANSWER_BUDGET_LINE_LIMITS.get(budget, _ANSWER_BUDGET_LINE_LIMITS[ANSWER_BUDGET_NORMAL]))


def _tui_answer_max_tokens(policy: AgentCommunicationPolicy | Mapping[str, Any] | None) -> int:
    payload = _tui_policy_payload(policy)
    budget = str(payload.get("answer_budget") or ANSWER_BUDGET_NORMAL)
    base = int(_ANSWER_BUDGET_MAX_TOKENS.get(budget, _ANSWER_BUDGET_MAX_TOKENS[ANSWER_BUDGET_NORMAL]))
    if str(payload.get("latency_preference") or "") == LATENCY_FAST:
        return min(base, 500)
    if str(payload.get("latency_preference") or "") == LATENCY_DEEP:
        return max(base, 1000)
    return base


def _tui_read_only_model_limits(policy: AgentCommunicationPolicy | Mapping[str, Any] | None) -> tuple[int, int]:
    payload = _tui_policy_payload(policy)
    if str(payload.get("latency_preference") or "") == LATENCY_FAST:
        return 2, 4
    if str(payload.get("latency_preference") or "") == LATENCY_DEEP:
        return 6, 20
    return 4, 12
_TUI_TABLE_SUFFIXES = {".csv", ".tsv", ".xlsx", ".xls"}
_TUI_REPORT_SUFFIXES = {".md", ".html", ".tex", ".pdf"}
_TUI_DATA_SUFFIXES = {".dta", ".parquet", ".feather", ".pkl", ".pickle", ".rds", ".sav"}
_TUI_CODE_SUFFIXES = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".css",
    ".html",
    ".sh",
    ".bash",
    ".zsh",
    ".sql",
    ".do",
    ".r",
    ".jl",
    ".ipynb",
}


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


def _tui_progress_tokens(value: Any) -> list[str]:
    normalized = "".join(ch.lower() if ch.isalnum() else " " for ch in str(value or ""))
    return [word for word in normalized.split() if len(word) > 2]


def _tui_progress_line_redundant(candidate: Any, existing: Any) -> bool:
    left = " ".join(str(candidate or "").split())
    right = " ".join(str(existing or "").split())
    if not left or not right:
        return False
    if left.casefold() == right.casefold():
        return True
    if min(len(left), len(right)) >= 32:
        left_fold = left.casefold()
        right_fold = right.casefold()
        if left_fold in right_fold or right_fold in left_fold:
            return True
    left_tokens = set(_tui_progress_tokens(left))
    right_tokens = set(_tui_progress_tokens(right))
    overlap_base = min(len(left_tokens), len(right_tokens))
    if overlap_base < 6:
        return False
    return (len(left_tokens & right_tokens) / overlap_base) >= 0.82


def _tui_progress_recent_lines(values: Sequence[Any], *, limit: int = 8) -> list[str]:
    lines: list[str] = []
    for value in values[-limit:]:
        for line in str(value or "").splitlines():
            clean = line.strip()
            if clean:
                lines.append(clean)
    return lines[-limit:]


def _tui_progress_text_redundant(candidate: Any, existing_values: Sequence[Any]) -> bool:
    candidate_lines = [line.strip() for line in str(candidate or "").splitlines() if line.strip()]
    if not candidate_lines:
        return True
    existing_lines = _tui_progress_recent_lines(existing_values)
    if not existing_lines:
        return False
    return all(
        any(_tui_progress_line_redundant(candidate_line, existing_line) for existing_line in existing_lines)
        for candidate_line in candidate_lines
    )


def _tui_list_item(value: Any, *, indent: str = "") -> str:
    text = str(value or "").rstrip()
    if text.startswith("- "):
        return f"{indent}  {text}"
    return f"{indent}- {text}"


def _tui_artifact_group(path: Any) -> str:
    text = str(path or "").strip()
    if not text:
        return "Files"
    candidate = Path(text)
    suffix = candidate.suffix.lower()
    parts = {part.lower() for part in candidate.parts}
    if ".dan-super" in parts:
        return "Run metadata"
    if parts & _TUI_FIGURE_DIR_PARTS:
        return "Figures"
    if parts & _TUI_TABLE_DIR_PARTS:
        return "Tables"
    if parts & _TUI_REPORT_DIR_PARTS:
        return "Reports"
    if parts & _TUI_DATA_DIR_PARTS:
        return "Data"
    if suffix in _TUI_FIGURE_SUFFIXES:
        return "Figures"
    if suffix in _TUI_TABLE_SUFFIXES:
        return "Tables"
    if suffix in _TUI_DATA_SUFFIXES:
        return "Data"
    if suffix in _TUI_CODE_SUFFIXES:
        return "Code"
    if suffix in _TUI_REPORT_SUFFIXES:
        return "Reports"
    return "Files"


def _tui_display_path(path: Any, *, workspace: str = "") -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    if workspace:
        try:
            candidate = Path(text).expanduser()
            root = Path(workspace).expanduser()
            if candidate.is_absolute():
                return str(candidate.resolve().relative_to(root.resolve()))
        except (OSError, ValueError):
            pass
    return text


def _group_tui_paths(
    paths: Sequence[Any],
    *,
    workspace: str = "",
    include_internal: bool = False,
    limit: int = 10,
) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {key: [] for key in _TUI_ARTIFACT_GROUP_ORDER}
    for raw in paths:
        text = str(raw or "").strip()
        if not text:
            continue
        text = _tui_display_path(text, workspace=workspace)
        group = _tui_artifact_group(text)
        if group == "Run metadata" and not include_internal:
            continue
        values = grouped.setdefault(group, [])
        if text not in values:
            values.append(text)
        if sum(len(items) for items in grouped.values()) >= limit:
            break
    return {key: values for key, values in grouped.items() if values}


def _grouped_artifact_lines(
    paths: Sequence[Any],
    *,
    label: str = "Artifact",
    seen: set[str] | None = None,
    workspace: str = "",
    include_internal: bool = False,
    limit: int = 10,
) -> list[str]:
    seen_paths = seen if seen is not None else set()
    clean_paths: list[str] = []
    for raw in paths:
        text = str(raw or "").strip()
        if text and text not in seen_paths:
            seen_paths.add(text)
            clean_paths.append(text)
    lines: list[str] = []
    for group, values in _group_tui_paths(
        clean_paths,
        workspace=workspace,
        include_internal=include_internal,
        limit=limit,
    ).items():
        if label == "Artifact" and group != "Files":
            line_label = group
        elif label == "Changed" and group == "Run metadata":
            line_label = "Run metadata"
        else:
            line_label = label
        for path in values:
            lines.append(f"- {line_label}: {path}")
            if len(lines) >= limit:
                return lines
    return lines


def _hide_internal_run_metadata_from_display_line(value: Any) -> str:
    text = _soften_tui_internal_metrics_for_display(str(value or "").strip())
    if ".dan-super" not in text:
        return text
    pieces = [piece.strip() for piece in text.split(",")]
    visible: list[str] = []
    for piece in pieces:
        if ".dan-super" not in piece:
            visible.append(piece)
            continue
        if ";" in piece:
            tail = piece.split(";", maxsplit=1)[1].strip()
            if tail:
                visible.append(tail)
    if not visible:
        return ""
    if len(visible) > 1 and visible[-1].endswith(".") and not visible[-2].endswith((".", ";")):
        return _soften_tui_internal_metrics_for_display(", ".join(visible[:-1]) + "; " + visible[-1])
    return _soften_tui_internal_metrics_for_display(", ".join(visible))


def _soften_tui_internal_metrics_for_display(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"\bvalidation:\s*passed \([0-9]+(?:\.[0-9]+)?\)", "Checks: passed", text, flags=re.IGNORECASE)
    text = re.sub(r"\bvalidation:\s*failed \([0-9]+(?:\.[0-9]+)?\)", "Checks: failed", text, flags=re.IGNORECASE)
    text = re.sub(r"\bvalidation passed \([0-9]+(?:\.[0-9]+)?\)", "checks passed", text, flags=re.IGNORECASE)
    text = re.sub(r"\bvalidation failed \([0-9]+(?:\.[0-9]+)?\)", "checks failed", text, flags=re.IGNORECASE)
    text = re.sub(r"\bfailed validation at [0-9]+(?:\.[0-9]+)?", "failed its checks", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\bfailed with validation at [0-9]+(?:\.[0-9]+)?",
        "failed because the checks did not pass",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\bwith validation at [0-9]+(?:\.[0-9]+)?", "after checks", text, flags=re.IGNORECASE)
    return text


def _tui_failure_detail(event: Mapping[str, Any]) -> str:
    error_type = str(event.get("error_type") or "").strip()
    error = str(event.get("error") or event.get("message") or "").strip().rstrip(".")
    if error_type and error:
        if error.lower().startswith(error_type.lower()):
            return error
        return f"{error_type}: {error}"
    return error_type or error


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
        if tool_id == "file_read":
            returned = result.get("returned_line_count", result.get("line_count"))
            total = result.get("total_line_count")
            if returned not in (None, "") and total not in (None, "") and total != returned:
                details.append(f"lines={returned}/{total}")
            elif returned not in (None, ""):
                details.append(f"lines={returned}")
            size = result.get("size")
            file_size = result.get("file_size")
            if size not in (None, "") and file_size not in (None, "") and file_size != size:
                details.append(f"bytes={size}/{file_size}")
            elif size not in (None, ""):
                details.append(f"bytes={size}")
        if not details:
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


def _tui_outbox_path(workspace_root: Path) -> Path:
    return workspace_root / ".dan-super" / "tui" / "outbox.jsonl"


def _tui_draft_path(workspace_root: Path) -> Path:
    return workspace_root / ".dan-super" / "tui" / "draft.json"


def _tui_prompt_history_path(workspace_root: Path) -> Path:
    return workspace_root / ".dan-super" / "tui" / "prompt-history.txt"


def _tui_plan_state_path(workspace_root: Path) -> Path:
    return workspace_root / ".dan-super" / "tui" / "plan-state.json"


def _tui_attachment_dir(workspace_root: Path) -> Path:
    return workspace_root / ".dan-super" / "tui" / "attachments"


def _tui_workspace_display_path(path: Path, workspace_root: Path) -> str:
    try:
        return path.relative_to(workspace_root).as_posix()
    except ValueError:
        return str(path)


def _tui_clipboard_capture_path(workspace_root: Path, *, suffix: str = ".png") -> Path:
    clean_suffix = suffix.lower() if suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS else ".png"
    return _tui_attachment_dir(workspace_root) / f"clipboard-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{time.time_ns()}{clean_suffix}"


def _store_tui_clipboard_image_bytes(
    workspace_root: Path,
    data: bytes,
    *,
    backend: str,
    suffix: str = ".png",
) -> TuiClipboardImageCapture:
    if not data:
        return TuiClipboardImageCapture(ok=False, message=f"{backend} returned no image bytes.", backend=backend)
    path = _tui_clipboard_capture_path(workspace_root, suffix=suffix)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except OSError as exc:
        return TuiClipboardImageCapture(ok=False, message=f"Could not save clipboard image: {exc}", backend=backend)
    return TuiClipboardImageCapture(
        ok=True,
        path=str(path),
        display_path=_tui_workspace_display_path(path, workspace_root),
        message=f"Attached screenshot {path.name}.",
        backend=backend,
    )


def _copy_tui_clipboard_image_file(
    workspace_root: Path,
    source: Path,
    *,
    backend: str,
) -> TuiClipboardImageCapture:
    try:
        resolved = source.expanduser().resolve()
        if not resolved.is_file():
            return TuiClipboardImageCapture(ok=False, message=f"Clipboard image path is not a file: {source}", backend=backend)
        if not is_supported_image_path(resolved):
            return TuiClipboardImageCapture(ok=False, message=f"Clipboard image type is not supported: {resolved.suffix}", backend=backend)
        data = resolved.read_bytes()
    except OSError as exc:
        return TuiClipboardImageCapture(ok=False, message=f"Could not read clipboard image path: {exc}", backend=backend)
    return _store_tui_clipboard_image_bytes(workspace_root, data, backend=backend, suffix=resolved.suffix)


def _capture_tui_clipboard_image_from_env(workspace_root: Path) -> TuiClipboardImageCapture:
    for name in ("DAN_SUPER_TUI_CLIPBOARD_IMAGE_PATH", "DAN_TUI_CLIPBOARD_IMAGE_PATH"):
        value = str(os.environ.get(name) or "").strip()
        if value:
            return _copy_tui_clipboard_image_file(workspace_root, Path(value), backend=name)
    return TuiClipboardImageCapture(ok=False, message="No clipboard image path env var set.", backend="env")


def _capture_tui_clipboard_image_with_pngpaste(workspace_root: Path) -> TuiClipboardImageCapture:
    binary = shutil.which("pngpaste")
    if not binary:
        return TuiClipboardImageCapture(ok=False, message="pngpaste is not installed.", backend="pngpaste")
    path = _tui_clipboard_capture_path(workspace_root, suffix=".png")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            [binary, str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return TuiClipboardImageCapture(ok=False, message=f"pngpaste failed: {exc}", backend="pngpaste")
    if completed.returncode == 0 and path.exists() and path.stat().st_size > 0:
        return TuiClipboardImageCapture(
            ok=True,
            path=str(path),
            display_path=_tui_workspace_display_path(path, workspace_root),
            message=f"Attached screenshot {path.name}.",
            backend="pngpaste",
        )
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    detail = (completed.stderr or completed.stdout or b"").decode("utf-8", errors="replace").strip()
    return TuiClipboardImageCapture(
        ok=False,
        message=_clip(detail or "Clipboard does not contain a PNG image.", limit=180),
        backend="pngpaste",
    )


def _capture_tui_clipboard_image_with_stream_command(
    workspace_root: Path,
    command: Sequence[str],
    *,
    backend: str,
) -> TuiClipboardImageCapture:
    if not command or not shutil.which(command[0]):
        return TuiClipboardImageCapture(ok=False, message=f"{backend} is not installed.", backend=backend)
    try:
        completed = subprocess.run(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return TuiClipboardImageCapture(ok=False, message=f"{backend} failed: {exc}", backend=backend)
    if completed.returncode == 0 and completed.stdout:
        return _store_tui_clipboard_image_bytes(workspace_root, completed.stdout, backend=backend, suffix=".png")
    detail = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
    return TuiClipboardImageCapture(
        ok=False,
        message=_clip(detail or "Clipboard does not contain a PNG image.", limit=180),
        backend=backend,
    )


def _capture_tui_clipboard_image_with_osascript(workspace_root: Path) -> TuiClipboardImageCapture:
    binary = shutil.which("osascript")
    if sys.platform != "darwin" or not binary:
        return TuiClipboardImageCapture(ok=False, message="osascript is not available.", backend="osascript")
    path = _tui_clipboard_capture_path(workspace_root, suffix=".png")
    script = r"""
ObjC.import('AppKit');
const env = $.NSProcessInfo.processInfo.environment;
const rawPath = env.objectForKey('DAN_TUI_CLIPBOARD_OUT');
if (!rawPath) { $.exit(3); }
const pb = $.NSPasteboard.generalPasteboard;
let data = pb.dataForType($.NSPasteboardTypePNG);
if (!data) {
  const image = $.NSImage.alloc.initWithPasteboard(pb);
  if (!image) { $.exit(2); }
  const tiff = image.TIFFRepresentation;
  if (!tiff) { $.exit(2); }
  const rep = $.NSBitmapImageRep.imageRepWithData(tiff);
  if (!rep) { $.exit(2); }
  data = rep.representationUsingTypeProperties($.NSPNGFileType, $());
}
if (!data) { $.exit(2); }
if (!data.writeToFileAtomically(rawPath, true)) { $.exit(4); }
"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "DAN_TUI_CLIPBOARD_OUT": str(path)}
        completed = subprocess.run(
            [binary, "-l", "JavaScript", "-e", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=3,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return TuiClipboardImageCapture(ok=False, message=f"osascript failed: {exc}", backend="osascript")
    if completed.returncode == 0 and path.exists() and path.stat().st_size > 0:
        return TuiClipboardImageCapture(
            ok=True,
            path=str(path),
            display_path=_tui_workspace_display_path(path, workspace_root),
            message=f"Attached screenshot {path.name}.",
            backend="osascript",
        )
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    detail = (completed.stderr or completed.stdout or b"").decode("utf-8", errors="replace").strip()
    return TuiClipboardImageCapture(
        ok=False,
        message=_clip(detail or "Clipboard does not contain an image.", limit=180),
        backend="osascript",
    )


def _capture_tui_clipboard_image_with_appkit(workspace_root: Path) -> TuiClipboardImageCapture:
    try:
        from AppKit import (  # type: ignore
            NSBitmapImageRep,
            NSImage,
            NSPasteboard,
            NSPasteboardTypePNG,
            NSPasteboardTypeTIFF,
            NSPNGFileType,
        )
    except Exception as exc:
        return TuiClipboardImageCapture(ok=False, message=f"AppKit unavailable: {exc}", backend="appkit")
    try:
        pasteboard = NSPasteboard.generalPasteboard()
        png_data = pasteboard.dataForType_(NSPasteboardTypePNG)
        if png_data is not None:
            return _store_tui_clipboard_image_bytes(workspace_root, bytes(png_data), backend="appkit", suffix=".png")
        tiff_data = pasteboard.dataForType_(NSPasteboardTypeTIFF)
        if tiff_data is not None:
            bitmap = NSBitmapImageRep.imageRepWithData_(tiff_data)
            if bitmap is None:
                image = NSImage.alloc().initWithData_(tiff_data)
                reps = list(image.representations() or []) if image is not None else []
                bitmap = reps[0] if reps else None
            if bitmap is not None:
                png = bitmap.representationUsingType_properties_(NSPNGFileType, {})
                if png is not None:
                    return _store_tui_clipboard_image_bytes(workspace_root, bytes(png), backend="appkit", suffix=".png")
    except Exception as exc:
        return TuiClipboardImageCapture(ok=False, message=f"AppKit clipboard read failed: {exc}", backend="appkit")
    return TuiClipboardImageCapture(ok=False, message="Clipboard does not contain an image.", backend="appkit")


def _capture_tui_clipboard_image(workspace_root: Path) -> TuiClipboardImageCapture:
    root = normalize_workspace_root(str(workspace_root))
    attempts: list[TuiClipboardImageCapture] = []
    readers = [
        lambda: _capture_tui_clipboard_image_from_env(root),
        lambda: _capture_tui_clipboard_image_with_pngpaste(root),
        lambda: _capture_tui_clipboard_image_with_osascript(root),
        lambda: _capture_tui_clipboard_image_with_appkit(root),
        lambda: _capture_tui_clipboard_image_with_stream_command(
            root,
            ["wl-paste", "--no-newline", "--type", "image/png"],
            backend="wl-paste",
        ),
        lambda: _capture_tui_clipboard_image_with_stream_command(
            root,
            ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
            backend="xclip",
        ),
    ]
    for reader in readers:
        result = reader()
        attempts.append(result)
        if result.ok:
            return result
    detail = "; ".join(
        f"{item.backend}: {item.message}"
        for item in attempts
        if item.backend and item.message
    )
    return TuiClipboardImageCapture(
        ok=False,
        message=_clip(detail or "No supported clipboard image backend found.", limit=260),
        backend="clipboard",
    )


def _iter_tui_image_mention_candidates(text: str) -> list[str]:
    try:
        tokens = shlex.split(str(text or ""))
    except ValueError:
        tokens = str(text or "").split()
    values: list[str] = []
    for token in tokens:
        clean = token.strip().strip(".,;:()[]{}\"'")
        if clean.startswith("@"):
            clean = clean[1:]
        if not clean:
            continue
        if is_supported_image_path(clean):
            values.append(clean)
    return values


def _tui_image_attachment_payloads_from_text(text: str, workspace_root: Path) -> list[dict[str, Any]]:
    root = normalize_workspace_root(str(workspace_root))
    raw: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in _iter_tui_image_mention_candidates(text):
        path = Path(candidate).expanduser()
        if not path.is_absolute():
            path = root / candidate
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if not _is_relative_to_path(resolved, root):
            continue
        if str(resolved) in seen or not resolved.is_file():
            continue
        seen.add(str(resolved))
        raw.append(
            {
                "kind": "image",
                "source_surface": "cli:super-tui",
                "local_path": str(resolved),
                "path": str(resolved),
                "display_name": resolved.name,
                "mime_type": image_mime_type(resolved),
                "caption": "Super TUI image attachment",
                "metadata": {
                    "source": "tui_image_mention",
                    "relative_path": _tui_workspace_display_path(resolved, root),
                },
            }
        )
    return image_attachment_payloads(raw)


def _set_tui_surface_attachments_from_text(args: argparse.Namespace, workspace_root: Path, text: str) -> list[dict[str, Any]]:
    attachments = _tui_image_attachment_payloads_from_text(text, workspace_root)
    setattr(args, "_tui_surface_attachments", attachments)
    setattr(args, "_tui_surface_image_attachments", attachments)
    setattr(args, "_surface_attachments", attachments)
    setattr(args, "_surface_image_attachments", attachments)
    return attachments


def _new_tui_message_id(text: str) -> str:
    payload = f"{time.time_ns()}:{text}".encode("utf-8", errors="ignore")
    return hashlib.sha1(payload).hexdigest()[:16]


def _append_tui_outbox_event(
    workspace_root: Path,
    *,
    message_id: str,
    status: str,
    text: str,
    route: str = "",
    metadata: Mapping[str, Any] | None = None,
) -> Path | None:
    clean_id = str(message_id or "").strip()
    clean_text = str(text or "").strip()
    clean_status = str(status or "").strip() or "submitted"
    if not clean_id or not clean_text:
        return None
    path = _tui_outbox_path(workspace_root)
    row = {
        "created_at": _now_iso(),
        "message_id": clean_id,
        "status": clean_status,
        "text": clean_text,
        "route": str(route or "").strip(),
        "metadata": dict(metadata or {}),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n")
    except OSError:
        return None
    return path


def _write_tui_draft_text(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"updated_at": _now_iso(), "text": str(text or "")}, sort_keys=True, ensure_ascii=True),
            encoding="utf-8",
        )
    except OSError:
        return


def _clear_tui_draft(workspace_root: Path) -> None:
    path = _tui_draft_path(workspace_root)
    try:
        if path.exists():
            path.unlink()
    except OSError:
        return


def _tui_plan_draft_to_dict(draft: TuiPlanDraft) -> dict[str, Any]:
    return {
        "summary": draft.summary,
        "next_step": draft.next_step,
        "questions": [
            {
                "question": question.question,
                "recommended": question.recommended,
                "choices": list(question.choices),
                "custom_label": question.custom_label,
            }
            for question in draft.questions
        ],
    }


def _tui_plan_draft_from_dict(value: Mapping[str, Any]) -> TuiPlanDraft | None:
    questions: list[TuiPlanQuestion] = []
    raw_questions = value.get("questions")
    if not isinstance(raw_questions, SequenceABC) or isinstance(raw_questions, (str, bytes)):
        return None
    for item in raw_questions:
        if not isinstance(item, MappingABC):
            continue
        raw_choices = item.get("choices")
        choices: list[str] = []
        if isinstance(raw_choices, SequenceABC) and not isinstance(raw_choices, (str, bytes)):
            choices = [str(choice) for choice in raw_choices if str(choice or "").strip()]
        question = str(item.get("question") or "").strip()
        if not question:
            continue
        questions.append(
            TuiPlanQuestion(
                question=question,
                recommended=str(item.get("recommended") or "").strip(),
                choices=tuple(choices),
                custom_label=str(item.get("custom_label") or "").strip()
                or "Other: describe your own preference",
            )
        )
    if not questions:
        return None
    return TuiPlanDraft(
        summary=str(value.get("summary") or "").strip(),
        questions=tuple(questions),
        next_step=str(value.get("next_step") or "").strip(),
    )


def _write_tui_pending_plan_state(workspace_root: Path, *, objective: str, draft: TuiPlanDraft) -> Path | None:
    path = _tui_plan_state_path(workspace_root)
    payload = {
        "created_at": _now_iso(),
        "status": "awaiting_reply",
        "objective": str(objective or "").strip(),
        "draft": _tui_plan_draft_to_dict(draft),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, sort_keys=True, ensure_ascii=True, indent=2), encoding="utf-8")
    except OSError:
        return None
    return path


def _load_tui_pending_plan_state(workspace_root: Path) -> dict[str, Any] | None:
    path = _tui_plan_state_path(workspace_root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("status") != "awaiting_reply":
        return None
    draft_value = value.get("draft")
    if not isinstance(draft_value, MappingABC):
        return None
    draft = _tui_plan_draft_from_dict(draft_value)
    if draft is None:
        return None
    return {**value, "draft_object": draft}


def _clear_tui_pending_plan_state(workspace_root: Path) -> None:
    path = _tui_plan_state_path(workspace_root)
    try:
        if path.exists():
            path.unlink()
    except OSError:
        return


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


def _transcript_role_label(role: str) -> str:
    return {
        "user": "You",
        "assistant_progress": "DAN",
        "assistant_final": "DAN",
        "assistant_narrator": "DAN",
        "system_notice": "System",
        "debug_ref": "Trace",
    }.get(role, role or "System")


def _transcript_async_admission_summary(entry: TuiTranscriptEntry) -> str:
    metadata = entry.metadata if isinstance(entry.metadata, MappingABC) else {}
    admission = metadata.get("admission") if isinstance(metadata.get("admission"), MappingABC) else {}
    action = str(admission.get("action") or metadata.get("status") or "").strip()
    task_id = str(admission.get("task_id") or admission.get("target_task_id") or "").strip()
    run_id = str(admission.get("run_id") or admission.get("target_run_id") or "").strip()
    visible_id = task_id or run_id
    if action == "start_parallel":
        detail = f" `{visible_id}`" if visible_id else ""
        return f"Background work started{detail}. Use /tasks when you want the full board."
    if action == "queue_after":
        detail = f" as `{visible_id}`" if visible_id else ""
        return f"This is queued behind active work{detail}."
    if action == "append_to_active":
        detail = f" for `{visible_id}`" if visible_id else ""
        return f"Your follow-up was added{detail}; the executor will pick it up at the next checkpoint."
    if action == "ask_clarification":
        question = str(admission.get("question") or "").strip()
        if question:
            return question
    return ""


def _transcript_display_text(entry: TuiTranscriptEntry) -> str:
    raw_text = str(entry.text or "").strip()
    if entry.role == "assistant_progress":
        summary = _transcript_async_admission_summary(entry)
        if summary:
            return summary
        raw_lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        if any(line in {"Admission:", "Board:", "Active:", "Progress:", "Intervene:"} for line in raw_lines):
            first = raw_lines[0] if raw_lines else ""
            return first or "Background work is active. Use /tasks for the full board."
    return raw_text


def _transcript_entry_body_lines(entry: TuiTranscriptEntry) -> list[str]:
    role = entry.role
    raw_text = _transcript_display_text(entry)
    per_line_limit = {
        "user": 220,
        "assistant_final": 360,
        "assistant_narrator": 320,
        "assistant_progress": 260,
        "system_notice": 260,
        "debug_ref": 260,
    }.get(role, 260)
    line_limit = {
        "user": 3,
        "assistant_final": 6,
        "assistant_narrator": 4,
        "assistant_progress": 4,
        "system_notice": 3,
        "debug_ref": 2,
    }.get(role, 3)
    raw_lines = [line.rstrip() for line in raw_text.splitlines() if line.strip()]
    if not raw_lines and raw_text:
        raw_lines = [raw_text]
    lines = [_clip(line, limit=per_line_limit) for line in raw_lines[:line_limit]]
    if len(raw_lines) > line_limit:
        lines.append(f"... {len(raw_lines) - line_limit} more line(s) in transcript")
    elif raw_text and len(raw_text) > sum(len(line) for line in raw_lines[:line_limit]) + 12:
        lines.append("... more in transcript")
    return lines


def _format_transcript_entry_lines(entry: TuiTranscriptEntry) -> list[str]:
    label = _transcript_role_label(entry.role)
    lines = _transcript_entry_body_lines(entry)
    if not lines:
        return [f"{label}:"]
    if len(lines) == 1:
        return [f"{label}: {lines[0]}"]
    return [f"{label}: {lines[0]}"] + [f"  {line}" for line in lines[1:]]


def _format_transcript_line(entry: TuiTranscriptEntry) -> str:
    return "\n".join(_format_transcript_entry_lines(entry))


def _tui_transcript_history_payload(
    workspace_root: Path,
    *,
    current_text: str = "",
    limit: int = 8,
) -> list[dict[str, str]]:
    entries = _read_tui_transcript(workspace_root, limit=max(limit + 6, 16))
    history: list[dict[str, str]] = []
    current = " ".join(str(current_text or "").split())
    for entry in entries:
        role = "user" if entry.role == "user" else "assistant" if entry.role.startswith("assistant_") else ""
        if not role:
            continue
        text = " ".join(_transcript_display_text(entry).split())
        if not text:
            continue
        history.append({"role": role, "content": _clip(text, limit=900)})
    if history and history[-1]["role"] == "user" and current:
        latest = " ".join(history[-1]["content"].split())
        if latest == current:
            history = history[:-1]
    return history[-max(1, int(limit)) :]


def _set_tui_surface_context_from_transcript(
    args: argparse.Namespace,
    workspace_root: Path,
    *,
    current_text: str,
) -> list[dict[str, str]]:
    history = _tui_transcript_history_payload(workspace_root, current_text=current_text)
    surface_context = {
        "workspace_root": str(workspace_root),
        "workspace_source": "super_tui",
        "conversation": {"recent_turns": history},
    }
    setattr(args, "_surface_history", history)
    setattr(args, "_surface_context", surface_context)
    setattr(args, "_tui_surface_history", history)
    return history


def _stream_line_without_prefix(line: str) -> str:
    text = str(line or "").strip()
    match = _TUI_STREAM_PREFIX_RE.match(text)
    if match:
        return text[match.end() :].strip()
    return text


def _sanitize_tui_progress_line(line: str, *, objective: str = "") -> str:
    text = str(line or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered.startswith("relevant context is available for "):
        return ""
    if lowered.startswith("checking the relevant workspace context for "):
        return "Checking the relevant workspace context."
    if lowered.startswith("thinking through the next step for "):
        return "Thinking through the next step."
    clean_objective = " ".join(str(objective or "").split())
    if len(clean_objective) >= 18:
        text = re.sub(re.escape(clean_objective), "this request", text, flags=re.IGNORECASE)
    return text.strip()


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
        "Checks:",
        "Blocker:",
        "Admission:",
        "Board:",
        "Intervene:",
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
        "Active:",
        "Queued:",
        "Recent:",
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
        r"^(\s*(?:(?:\[tui\]|dan:|DAN(?:\s*[·:])?)\s*)?(?:-\s*)?)([A-Z][A-Za-z /_-]*)(:)",
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
            "Admission": "bold cyan",
            "Board": "bold white",
            "Active": "bold cyan",
            "Queued": "bold yellow",
            "Recent": "bold green",
            "Intervene": "bold magenta",
            "Narrator": "bold magenta",
            "Source": "bold cyan",
            "Progress": "bold white",
            "Results": "bold white",
            "Trace": "bold cyan",
            "You asked": "bold cyan",
            "You": "bold blue",
            "DAN": "bold cyan",
            "Working": "bold yellow",
            "Elapsed": "bold green",
            "Validation": "bold green",
            "Checks": "bold green",
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
            "User": "bold blue",
            "Assistant": "bold green",
            "Executor": "bold cyan",
            "System": "bold white",
            "Figures": "bold cyan",
            "Tables": "bold cyan",
            "Reports": "bold cyan",
            "Data": "bold cyan",
            "Code": "bold cyan",
            "Files": "bold cyan",
            "Changed figures": "bold green",
            "Changed tables": "bold green",
            "Changed reports": "bold green",
            "Changed data": "bold green",
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


def _tui_section_title(title: str) -> str:
    clean = str(title or "").strip() or "Answer"
    return f"{_TUI_BRAND} {_TUI_SEPARATOR} {clean}:"


def _tui_panel_title(title: str) -> str:
    return _tui_section_title(title)


def _tui_vertical_ornament(height: int) -> str:
    count = max(1, int(height or 0))
    if count == 1:
        return "◆"
    return "\n".join(["╭", *(["│"] * max(0, count - 2)), "╰"])


def _tui_chatbox_thinking_text(started_at: float | None = None) -> str:
    started = float(started_at or time.monotonic())
    return f"Thinking {_format_elapsed_duration(max(0.0, time.monotonic() - started))}..."


def _format_tui_stream_line(text: str) -> str:
    clean = str(text or "").strip()
    if not clean:
        return _TUI_BRAND
    match = re.match(r"^([A-Z][A-Za-z /_-]{1,40}):\s*(.*)$", clean)
    if match:
        label = match.group(1).strip()
        body = match.group(2).strip()
        if body:
            return f"{_TUI_BRAND} {_TUI_SEPARATOR} {label}: {body}"
        return _tui_section_title(label)
    return f"{_TUI_BRAND} {_TUI_SEPARATOR} {clean}"


def _tui_section_border_style(title: str) -> str:
    lowered = str(title or "").strip().lower()
    if lowered in {"answer", "message", "conversation"}:
        return "cyan"
    if lowered in {"tasks", "status", "board"}:
        return "blue"
    if lowered in {"outcome", "result", "results"}:
        return "green"
    if lowered in {"command", "system", "help", "queues"}:
        return "yellow"
    if lowered in {"narrator", "progress", "activity"}:
        return "magenta"
    return "cyan"


def _print_tui_stream_line(text: str, *, plain: bool = False) -> None:
    rendered = _format_tui_stream_line(str(text or ""))
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


def _print_tui_stream_continuation(text: str, *, plain: bool = False) -> None:
    rendered = f"  {str(text or '').strip()}"
    if plain:
        print(rendered, flush=True)
        return
    Console, _ = _try_import_rich()
    if Console is None:
        print(rendered, flush=True)
        return
    try:
        console = Console(highlight=False)
        console.print(_rich_semantic_text(rendered, base_style="grey70"), soft_wrap=True)
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


def _tui_stream_block_uses_panel(title: str) -> bool:
    return str(title or "").strip().lower() not in {"narrator", "progress", "activity"}


def _print_tui_stream_block(title: str, lines: Sequence[str], *, plain: bool = False) -> None:
    title_text = str(title or "").strip() or "Answer"
    visible_lines = _wrap_tui_stream_lines([str(line or "").rstrip() for line in lines if str(line or "").strip()])
    if not visible_lines:
        return
    if not _tui_stream_block_uses_panel(title_text):
        for index, line in enumerate(visible_lines):
            if index == 0:
                _print_tui_stream_line(f"{title_text}: {line}", plain=plain)
            else:
                _print_tui_stream_continuation(line, plain=plain)
        return
    if plain:
        print(_tui_section_title(title_text), flush=True)
        for line in visible_lines:
            print(f"  {line}", flush=True)
        return
    Console, _ = _try_import_rich()
    if Console is None:
        print(_tui_section_title(title_text), flush=True)
        for line in visible_lines:
            print(f"  {line}", flush=True)
        return
    try:
        from rich import box
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        body = _rich_semantic_text("\n".join(visible_lines), base_style="white")
        ornament = Text(_tui_vertical_ornament(len(visible_lines)), style=_tui_section_border_style(title_text))
        content = Table.grid(expand=True)
        content.add_column(width=2, no_wrap=True)
        content.add_column(ratio=1)
        content.add_row(ornament, body)
        console = Console(highlight=False)
        console.print(
            Panel(
                content,
                title=_tui_panel_title(title_text),
                title_align="center",
                box=box.ROUNDED,
                border_style=_tui_section_border_style(title_text),
                padding=(0, 1),
            )
        )
    except Exception:
        print(_tui_section_title(title_text), flush=True)
        for line in visible_lines:
            print(f"  {line}", flush=True)


def _format_tui_clock_text(footer: str, *, label: str = "") -> str:
    text = str(footer or "").strip()
    if not text:
        return ""
    clean_label = str(label or "").strip()
    if text.startswith("Working:"):
        duration = text.split(":", maxsplit=1)[1].strip()
        return f"{clean_label or 'Working'} {duration}"
    if text.startswith("Elapsed:"):
        duration = text.split(":", maxsplit=1)[1].strip()
        return f"{clean_label or 'Elapsed'} {duration}"
    return text


def _style_tui_clock_text(text: str) -> str:
    if not text:
        return ""
    if _tui_stdout_supports_control_sequences():
        return f"\x1b[2m{text}\x1b[0m"
    return text


def _tui_stdout_supports_control_sequences() -> bool:
    term = str(os.environ.get("TERM") or "").strip().lower()
    if term == "dumb":
        return False
    try:
        if bool(sys.stdout.isatty()):
            return True
    except Exception:
        pass
    # prompt_toolkit can wrap stdout in a proxy that does not report isatty()
    # even though the interactive terminal can still handle carriage-return
    # line refreshes. In that case stdin remains the better terminal signal.
    try:
        return bool(sys.stdin.isatty())
    except Exception:
        return False


def _write_tui_clock_line(footer: str, *, label: str = "", force_newline: bool = False) -> bool:
    text = _format_tui_clock_text(footer, label=label)
    if not text:
        return False
    if _tui_stdout_supports_control_sequences() and not force_newline:
        sys.stdout.write("\r\x1b[2K" + _style_tui_clock_text(text))
    else:
        sys.stdout.write(text + "\n")
    sys.stdout.flush()
    return True


def _clear_tui_clock_line(active: bool, *, force_newline: bool = False) -> None:
    if not active:
        return
    if force_newline:
        return
    if _tui_stdout_supports_control_sequences():
        sys.stdout.write("\r\x1b[2K")
        sys.stdout.flush()
    else:
        return


def _run_with_tui_working_clock(
    args: argparse.Namespace,
    callback: Any,
    *,
    label: str = "",
    started_at: float | None = None,
) -> Any:
    """Run a blocking callback while refreshing one visible Working line."""

    if bool(getattr(args, "json", False)) or bool(getattr(args, "quiet_progress", False)):
        return callback()
    if bool(getattr(args, "_tui_background_dispatch", False)):
        return callback()

    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def _worker() -> None:
        try:
            result["value"] = callback()
        except BaseException as exc:  # pragma: no cover - re-raised in caller thread
            error["value"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    started = float(started_at or time.monotonic())
    last_footer = ""
    clock_active = False
    force_newline_clock = bool(getattr(args, "_tui_background_dispatch", False))
    thread.start()
    footer = f"Working: {_format_elapsed_duration(max(0.0, time.monotonic() - started))}"
    clock_active = _write_tui_clock_line(footer, label=label, force_newline=force_newline_clock) or clock_active
    last_footer = footer
    while thread.is_alive():
        elapsed_seconds = time.monotonic() - started
        footer = f"Working: {_format_elapsed_duration(elapsed_seconds)}"
        if footer != last_footer:
            clock_active = _write_tui_clock_line(footer, label=label, force_newline=force_newline_clock) or clock_active
            last_footer = footer
        time.sleep(0.25)
    thread.join()
    _clear_tui_clock_line(clock_active, force_newline=force_newline_clock)
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
class TuiPlanQuestion:
    question: str
    recommended: str = ""
    choices: tuple[str, ...] = ()
    custom_label: str = "Other: describe your own preference"


@dataclass(frozen=True)
class TuiPlanDraft:
    summary: str
    questions: tuple[TuiPlanQuestion, ...]
    next_step: str = ""


@dataclass(frozen=True)
class TuiPlanReplyDecision:
    lines: tuple[str, ...]
    action: str = "record_only"
    execution_objective: str = ""


@dataclass(frozen=True)
class TuiPathSuggestion:
    value: str
    meta: str = ""


@dataclass(frozen=True)
class TuiClipboardImageCapture:
    ok: bool
    path: str = ""
    display_path: str = ""
    message: str = ""
    backend: str = ""


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
class TuiBoardCommandResult:
    message: str
    event: Mapping[str, Any] = field(default_factory=dict)
    focused_board_id: str = ""


@dataclass(frozen=True)
class TuiAsyncAdmissionResult:
    ok: bool
    message: str
    response: Mapping[str, Any] = field(default_factory=dict)
    events: tuple[Mapping[str, Any], ...] = ()
    error: str = ""


@dataclass
class TuiBoardRow:
    task_id: str
    objective: str = ""
    status: str = "running"
    phase: str = ""
    run_id: str = ""
    action: str = ""
    admission: str = ""
    intervention: str = ""
    reason: str = ""
    trace_ref: str = ""
    changed_paths: list[str] = field(default_factory=list)
    answer_lines: list[str] = field(default_factory=list)
    started_at_monotonic: float = 0.0
    updated_at_monotonic: float = 0.0

    @property
    def display_id(self) -> str:
        return self.task_id or self.run_id or "task"


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
    communication_policy: AgentCommunicationPolicy = field(default_factory=AgentCommunicationPolicy)

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
    ".agent-subsessions",
    ".dan-code",
    ".dan-research",
    ".git",
    ".dan-super",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "memory",
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
_TUI_PATH_SUGGESTION_SUFFIXES = (
    _READ_ONLY_FILE_SUFFIXES
    | _CODE_REVIEW_FILE_SUFFIXES
    | _TUI_FIGURE_SUFFIXES
    | _TUI_TABLE_SUFFIXES
    | _TUI_DATA_SUFFIXES
    | {".pdf", ".log", ".out", ".est", ".tex"}
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
    communication_policy = normalize_agent_communication_policy(
        getattr(decision, "communication_policy", None),
        lane=lane,
        executor_effort=effort,
    )
    if lane == NARRATOR_READ_ONLY:
        return TuiIntentDecision(
            permission="read-only",
            complexity="narrator",
            confidence=confidence,
            rationale=rationale,
            communication_policy=communication_policy,
        )
    if lane == "executor read-only":
        return TuiIntentDecision(
            permission="read-only",
            complexity=effort,
            confidence=confidence,
            rationale=rationale,
            communication_policy=communication_policy,
        )
    if lane == "executor write":
        return TuiIntentDecision(
            permission="write",
            complexity=effort,
            confidence=confidence,
            rationale=rationale,
            communication_policy=communication_policy,
        )
    if lane == PLAN_MODE:
        return TuiIntentDecision(
            permission="mode",
            complexity="plan",
            confidence=confidence,
            rationale=rationale,
            communication_policy=communication_policy,
        )
    return TuiIntentDecision(
        permission="",
        complexity="",
        confidence=confidence,
        rationale=rationale,
        clarification=str(getattr(decision, "clarification", "") or "").strip()
        or "Should this be progress/status, read-only inspection, or workspace-changing work?",
        communication_policy=communication_policy,
    )


def _extract_read_only_path_mentions(text: str) -> list[str]:
    mentions: list[str] = []
    for raw_token in str(text or "").replace("\n", " ").split():
        token = raw_token.strip().strip("`'\"()[]{}")
        if not token.startswith("@"):
            continue
        token = token[1:].strip().rstrip(".,;:")
        if token and token not in mentions:
            mentions.append(token)
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


def _load_tui_path_suggestions(workspace_root: Path, *, limit: int = 500) -> list[TuiPathSuggestion]:
    root = normalize_workspace_root(str(workspace_root))
    rows: list[TuiPathSuggestion] = []
    seen: set[str] = set()
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(
                dirname
                for dirname in dirnames
                if dirname not in _READ_ONLY_SKIP_PARTS and not dirname.startswith(".")
            )
            rel_dir = Path(dirpath).relative_to(root)
            if any(part in _READ_ONLY_SKIP_PARTS for part in rel_dir.parts):
                continue
            for dirname in dirnames:
                rel = (rel_dir / dirname) if str(rel_dir) != "." else Path(dirname)
                value = str(rel).rstrip("/") + "/"
                if value not in seen:
                    seen.add(value)
                    rows.append(TuiPathSuggestion(value=value, meta="directory"))
                    if len(rows) >= limit:
                        return rows
            for filename in sorted(filenames):
                if filename.startswith("."):
                    continue
                path = Path(dirpath) / filename
                if path.suffix.lower() not in _TUI_PATH_SUGGESTION_SUFFIXES:
                    continue
                rel = path.relative_to(root)
                value = str(rel)
                if value in seen:
                    continue
                seen.add(value)
                group = _tui_artifact_group(value).lower()
                rows.append(TuiPathSuggestion(value=value, meta=group))
                if len(rows) >= limit:
                    return rows
    except OSError:
        return []
    return rows


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
    metadata_lines: list[str] = []
    for line in lines[:40]:
        text_line = line.strip()
        match = re.match(r"\*\*([^*:]{2,40}):\*\*\s*(.+)", text_line)
        if not match:
            continue
        label = " ".join(match.group(1).split())
        value = " ".join(match.group(2).split())
        if label and value:
            metadata_lines.append(f"{label}: {_clip(value, limit=180)}")
        if len(metadata_lines) >= 4:
            break
    if headings:
        result.append("Title: " + _clip(headings[0].lstrip("#").strip(), limit=120))
    if metadata_lines:
        result.extend(metadata_lines[:4])
    prose_lines: list[str] = []
    for line in lines:
        stripped = " ".join(line.strip().strip("> ").split())
        if not stripped:
            continue
        if stripped.startswith(("#", "|", "![", "<")):
            continue
        if re.fullmatch(r"[-:*_`#\s]+", stripped):
            continue
        if re.match(r"\*\*[^*:]{2,40}:\*\*", stripped):
            continue
        if re.search(r"- \[[ xX]\]", stripped):
            continue
        if len(re.findall(r"[A-Za-z][A-Za-z]{2,}", stripped)) < 4:
            continue
        prose_lines.append(_clip(stripped, limit=190))
        if len(prose_lines) >= 4:
            break
    if prose_lines and not open_count:
        result.append("Takeaways:")
        result.extend(f"- {line}" for line in prose_lines[:4])
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


def _tui_read_only_context_text(args: argparse.Namespace, objective: str) -> str:
    parts = [" ".join(str(objective or "").split())]
    history = getattr(args, "_tui_surface_history", []) or []
    if isinstance(history, SequenceABC) and not isinstance(history, (str, bytes)):
        for item in history[-6:]:
            if not isinstance(item, MappingABC):
                continue
            if str(item.get("role") or "").strip() != "user":
                continue
            text = " ".join(str(item.get("content") or "").split())
            if text and text not in parts:
                parts.append(text)
    return "\n".join(part for part in parts if part).strip()


def _build_read_only_answer(
    workspace_root: Path,
    objective: str,
    decision: TuiIntentDecision,
    *,
    context_text: str = "",
) -> tuple[list[str], list[str]]:
    search_context = context_text or objective
    sources = _resolve_read_only_sources(workspace_root, search_context)
    activity: list[str] = []
    answer: list[str] = []
    token_set = set(tokenize_intent_text(search_context))
    if token_set & {"find", "grep", "search"}:
        return _search_sources(workspace_root, search_context)
    wants_summary = bool(
        token_set
        & {
            "analyse",
            "analyze",
            "assess",
            "evaluate",
            "explain",
            "insight",
            "insights",
            "review",
            "summarise",
            "summarize",
            "summary",
            "takeaway",
            "takeaways",
        }
    )

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


def _format_tui_surface_history_for_prompt(args: argparse.Namespace, *, limit: int = 6) -> str:
    history = getattr(args, "_tui_surface_history", []) or []
    if not isinstance(history, SequenceABC) or isinstance(history, (str, bytes)):
        return "(none)"
    lines: list[str] = []
    for item in history[-limit:]:
        if not isinstance(item, MappingABC):
            continue
        role = str(item.get("role") or "").strip() or "turn"
        content = " ".join(str(item.get("content") or "").split())
        if content:
            lines.append(f"{role}: {_clip(content, limit=500)}")
    return "\n".join(lines) or "(none)"


def _tui_read_only_user_prompt(workspace_root: Path, objective: str, args: argparse.Namespace | None = None) -> str:
    recent_context = _format_tui_surface_history_for_prompt(args, limit=6) if args is not None else "(none)"
    return (
        f"Workspace root: {workspace_root}\n"
        f"User request: {objective}\n\n"
        f"Recent visible conversation context:\n{recent_context}\n\n"
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
            returned = result.get("returned_line_count") or result.get("line_count") or result.get("lines") or ""
            total = result.get("total_line_count") or ""
            header = f"file_read {path}".strip()
            if returned and total and total != returned:
                header += f" ({returned} returned lines, {total} total lines)"
            elif returned:
                header += f" ({returned} lines)"
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
    recent_context: str = "(none)",
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
                f"Recent visible conversation context:\n{recent_context}\n\n"
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
    if bool(getattr(args, "_tui_background_dispatch", False)) and not re.match(
        r"^(?:Elapsed|Working)\s*:",
        text,
        flags=re.IGNORECASE,
    ):
        started_at = float(getattr(args, "_tui_turn_started_at", 0.0) or 0.0) or None
        _print_tui_stream_block(
            "Chat -> Thinking",
            [_tui_chatbox_thinking_text(started_at), text],
            plain=bool(getattr(args, "plain", False)),
        )
        return
    _print_tui_stream_line(text, plain=bool(getattr(args, "plain", False)))


def _emit_tui_stream_answer(args: argparse.Namespace, state: "SuperTuiState") -> None:
    lines = state.final_answer_lines()
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
            max_rounds=_tui_read_only_model_limits(decision.communication_policy)[0],
            max_tool_calls=_tui_read_only_model_limits(decision.communication_policy)[1],
            event_callback=record_event,
        )
        try:
            response = await completion_provider.complete(
                CompletionRequest(
                    model=model,
                    system_prompt=_tui_read_only_system_prompt(tool_ids),
                    user_prompt=_tui_read_only_user_prompt(workspace_root, objective, args),
                    temperature=0.2,
                    max_tokens=_tui_answer_max_tokens(decision.communication_policy),
                    tools=_tui_tool_schemas(tool_ids),
                    metadata={
                        "worker_id": "super-dan.tui.read-only",
                        "tui_lane": decision.lane,
                        "communication_policy": decision.communication_policy.to_payload(),
                        "image_attachments": _tui_image_attachment_payloads_from_text(objective, workspace_root),
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
                            recent_context=_format_tui_surface_history_for_prompt(args),
                        ),
                        model=model,
                        temperature=0.2,
                        max_tokens=_tui_answer_max_tokens(decision.communication_policy),
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

    answer_lines = _split_answer_lines(text, limit=_tui_answer_line_limit(decision.communication_policy))
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
    paths: Sequence[TuiPathSuggestion | str] = (),
    limit: int = 24,
) -> list[TuiCompletionCandidate]:
    text = str(text_before_cursor or "")
    if text.startswith("/") and not any(char.isspace() for char in text):
        prefix = text.lower()
        rows = [
            TuiCompletionCandidate(command, meta, -len(text))
            for command, meta in commands
            if command.startswith(prefix)
        ]
        return rows[:limit]

    raw = text.rsplit(maxsplit=1)[-1] if text.strip() else text
    if raw.startswith("@"):
        prefix = raw[1:].lower()
        rows = []
        for item in paths:
            if isinstance(item, TuiPathSuggestion):
                value = item.value
                meta = item.meta
            else:
                value = str(item)
                meta = _tui_artifact_group(value).lower()
            if prefix and not value.lower().startswith(prefix):
                continue
            rows.append(TuiCompletionCandidate(f"@{value}", meta or "path", -len(raw)))
            if len(rows) >= limit:
                break
        return rows

    if not raw.startswith("$"):
        return []
    prefix = raw[1:].lower()
    if prefix and not prefix[0].isalpha():
        return []
    ranked_rows: list[tuple[int, int, TuiCompletionCandidate]] = []
    for index, skill in enumerate(skills):
        token = skill.token
        searchable = " ".join(
            part
            for part in (skill.token, skill.name, skill.description)
            if str(part or "").strip()
        ).lower()
        token_lower = token.lower()
        name_lower = skill.name.lower()
        if not prefix:
            rank = 0
        elif token_lower.startswith(prefix):
            rank = 1
        elif name_lower.startswith(prefix):
            rank = 2
        elif prefix in token_lower:
            rank = 3
        elif prefix in name_lower:
            rank = 4
        elif prefix in searchable:
            rank = 5
        else:
            continue
        meta = skill.description or skill.name
        if skill.source_scope:
            meta = f"{skill.source_scope}: {meta}"
        ranked_rows.append((rank, index, TuiCompletionCandidate(f"${token}", _clip(meta, limit=90), -len(raw))))
    ranked_rows.sort(key=lambda item: (item[0], item[1]))
    return [candidate for _, _, candidate in ranked_rows[:limit]]


def _parse_tui_run_command(text: str) -> TuiRunCommand | None:
    stripped = str(text or "").strip()
    lowered = stripped.lower()
    for command in ("/append", "/continue", "/pause", "/resume", "/cancel", "/stop", "/focus"):
        if lowered == command or lowered.startswith(command + " "):
            payload = stripped[len(command) :].strip()
            normalized_command = "cancel" if command == "/stop" else command[1:]
            if command == "/append":
                message = (
                    "Append is only available for async V2 active runs. "
                    "This direct blocking TUI path cannot accept active-run steering yet."
                )
            elif command == "/continue":
                message = (
                    "Continue-after-current is only available for async V2 active runs. "
                    "This direct blocking TUI path cannot queue continuation work yet."
                )
            elif command == "/pause":
                message = (
                    "Pause is not available for the direct blocking TUI path. "
                    "Async runs need checkpoint state before pause can take effect."
                )
            elif command == "/resume":
                message = (
                    "Resume is not available for this direct local TUI backend yet. "
                    "There is no paused async run to resume from this shell."
                )
            elif command == "/focus":
                message = (
                    "Focus is only a display target until async runs are active. "
                    "Use /tasks to inspect visible task ids first."
                )
            else:
                message = (
                    "Stop/cancel is not wired into direct local Super DAN runs yet. "
                    "Use Ctrl-C to interrupt this shell, or run through V2 once checkpoint cancellation lands."
                )
            return TuiRunCommand(command=normalized_command, payload=payload, message=message)
    return None


def _build_prompt_toolkit_completer(
    *,
    commands: Sequence[tuple[str, str]],
    skills: Sequence[TuiSkillSuggestion],
    paths: Sequence[TuiPathSuggestion | str] = (),
) -> Any:
    from prompt_toolkit.completion import Completer, Completion

    class SuperTuiCompleter(Completer):
        def get_completions(self, document: Any, complete_event: Any) -> Any:
            del complete_event
            for item in _completion_candidates(
                document.text_before_cursor,
                commands=commands,
                skills=skills,
                paths=paths,
            ):
                yield Completion(
                    item.value,
                    start_position=item.start_position,
                    display=item.value,
                    display_meta=item.meta,
                )

    return SuperTuiCompleter()


def _apply_prompt_completion_if_available(
    buffer: Any,
    *,
    commands: Sequence[tuple[str, str]] = _TUI_COMMANDS,
    skills: Sequence[TuiSkillSuggestion] = (),
    paths: Sequence[TuiPathSuggestion | str] = (),
) -> bool:
    complete_state = getattr(buffer, "complete_state", None)
    completion = getattr(complete_state, "current_completion", None) if complete_state is not None else None
    if completion is not None:
        buffer.apply_completion(completion)
        return True
    candidates = _completion_candidates(
        buffer.document.text_before_cursor,
        commands=commands,
        skills=skills,
        paths=paths,
    )
    if not candidates:
        return False
    text_before_cursor = str(buffer.document.text_before_cursor or "")
    current_token = text_before_cursor.rsplit(maxsplit=1)[-1] if text_before_cursor.strip() else text_before_cursor
    if any(candidate.value == current_token for candidate in candidates):
        return False
    from prompt_toolkit.completion import Completion

    candidate = candidates[0]
    buffer.apply_completion(
        Completion(
            candidate.value,
            start_position=candidate.start_position,
            display=candidate.value,
            display_meta=candidate.meta,
        )
    )
    return True


def _build_prompt_toolkit_key_bindings(
    *,
    commands: Sequence[tuple[str, str]] = _TUI_COMMANDS,
    skills: Sequence[TuiSkillSuggestion] = (),
    paths: Sequence[TuiPathSuggestion | str] = (),
    workspace_root: Path | None = None,
) -> Any:
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.filters import has_completions

    bindings = KeyBindings()

    def _accept_completion_or_submit(event: Any) -> None:
        buffer = event.current_buffer
        if _apply_prompt_completion_if_available(
            buffer,
            commands=commands,
            skills=skills,
            paths=paths,
        ):
            return
        buffer.validate_and_handle()

    def _submit_stop(event: Any) -> None:
        buffer = event.current_buffer
        buffer.text = "/stop"
        buffer.cursor_position = len(buffer.text)
        buffer.validate_and_handle()

    def _attach_clipboard_screenshot(event: Any) -> None:
        if workspace_root is None:
            return
        result = _capture_tui_clipboard_image(workspace_root)
        if not result.ok or not result.display_path:
            try:
                event.app.output.bell()
            except Exception:
                pass
            return
        buffer = event.current_buffer
        before = str(buffer.document.text_before_cursor or "")
        after = str(buffer.document.text_after_cursor or "")
        prefix = "" if not before or before.endswith((" ", "\n", "\t")) else " "
        suffix = "" if not after or after.startswith((" ", "\n", "\t")) else " "
        buffer.insert_text(f"{prefix}@{result.display_path}{suffix}")
        try:
            buffer.start_completion(select_first=False)
        except Exception:
            pass

    @bindings.add("/")
    def _slash(event: Any) -> None:
        event.current_buffer.insert_text("/")
        event.current_buffer.start_completion(select_first=True)

    @bindings.add("$")
    def _dollar(event: Any) -> None:
        event.current_buffer.insert_text("$")
        event.current_buffer.start_completion(select_first=True)

    @bindings.add("@")
    def _at(event: Any) -> None:
        event.current_buffer.insert_text("@")
        event.current_buffer.start_completion(select_first=True)

    @bindings.add("c-v")
    def _ctrl_v(event: Any) -> None:
        _attach_clipboard_screenshot(event)

    @bindings.add("enter")
    def _enter(event: Any) -> None:
        _accept_completion_or_submit(event)

    @bindings.add("escape")
    def _escape(event: Any) -> None:
        _submit_stop(event)

    @bindings.add("up", filter=~has_completions)
    def _history_up(event: Any) -> None:
        event.current_buffer.history_backward(count=1)

    @bindings.add("down", filter=~has_completions)
    def _history_down(event: Any) -> None:
        event.current_buffer.history_forward(count=1)

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
            "frame.border": "#22d3ee",
            "frame.label": "#22d3ee bold",
            "chatbox.border": "#22d3ee",
            "chatbox.title": "#22d3ee bold",
            "chatbox.prompt": "#cbd5e1 bold",
            "chatbox.placeholder": "#64748b",
        }
    )


def _tui_chatbox_prompt(prompt: str = "super-tui> ") -> list[tuple[str, str]]:
    return [
        ("class:chatbox.prompt", f" {prompt}"),
    ]


@contextlib.contextmanager
def _tui_prompt_rounded_frame_border(title: str = "Chat") -> Any:
    try:
        from prompt_toolkit.layout.containers import DynamicContainer, HSplit, VSplit, Window
        from prompt_toolkit.widgets import base as widgets_base
        prompt_module = __import__("prompt_toolkit.shortcuts.prompt", fromlist=["Frame"])
    except Exception:
        yield
        return

    border = widgets_base.Border
    original_frame = getattr(prompt_module, "Frame", None)
    original = {
        "TOP_LEFT": border.TOP_LEFT,
        "TOP_RIGHT": border.TOP_RIGHT,
        "BOTTOM_LEFT": border.BOTTOM_LEFT,
        "BOTTOM_RIGHT": border.BOTTOM_RIGHT,
    }

    class DanPromptFrame:
        def __init__(self, body: Any, *args: Any, **kwargs: Any) -> None:
            del args
            self.body = body
            self._dan_tui_frame_title = _tui_panel_title(title)
            style = "class:frame " + str(kwargs.get("style") or "")
            width = kwargs.get("width")
            height = kwargs.get("height")
            key_bindings = kwargs.get("key_bindings")
            modal = bool(kwargs.get("modal", False))

            def fill(**fill_kwargs: Any) -> Any:
                return Window(style="class:frame.border", **fill_kwargs)

            top_row = VSplit(
                [
                    fill(width=1, height=1, char="╭"),
                    fill(char="─"),
                    Window(
                        content=widgets_base.FormattedTextControl(
                            lambda: [("class:frame.label", f" {self._dan_tui_frame_title} ")]
                        ),
                        style="class:frame.label",
                        dont_extend_width=True,
                        height=1,
                    ),
                    fill(char="─"),
                    fill(width=1, height=1, char="╮"),
                ],
                height=1,
            )
            body_row = VSplit(
                [
                    fill(width=1, char="│"),
                    DynamicContainer(lambda: self.body),
                    fill(width=1, char="│"),
                ],
                padding=0,
            )
            bottom_row = VSplit(
                [
                    fill(width=1, height=1, char="╰"),
                    fill(char="─"),
                    fill(width=1, height=1, char="╯"),
                ],
                height=1,
            )
            self.container = HSplit(
                [top_row, body_row, bottom_row],
                width=width,
                height=height,
                style=style,
                key_bindings=key_bindings,
                modal=modal,
            )

        def __pt_container__(self) -> Any:
            return self.container

    def titled_frame(body: Any, *args: Any, **kwargs: Any) -> Any:
        if original_frame is None:
            return body
        return DanPromptFrame(body, *args, **kwargs)

    border.TOP_LEFT = "╭"
    border.TOP_RIGHT = "╮"
    border.BOTTOM_LEFT = "╰"
    border.BOTTOM_RIGHT = "╯"
    if original_frame is not None:
        prompt_module.Frame = titled_frame
    try:
        yield
    finally:
        for name, value in original.items():
            setattr(border, name, value)
        if original_frame is not None:
            prompt_module.Frame = original_frame


def _prompt_toolkit_chatbox_available() -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        import prompt_toolkit  # noqa: F401
    except Exception:
        return False
    return True


def _build_prompt_toolkit_session(
    *,
    commands: Sequence[tuple[str, str]],
    skills: Sequence[TuiSkillSuggestion],
    paths: Sequence[TuiPathSuggestion | str] = (),
    workspace_root: Path | None = None,
    draft_path: Path | None = None,
    history_path: Path | None = None,
) -> Any:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.shortcuts import CompleteStyle

    history = None
    if history_path is not None:
        try:
            history_path.parent.mkdir(parents=True, exist_ok=True)
            history = FileHistory(str(history_path))
        except Exception:
            history = None

    session = PromptSession(
        completer=_build_prompt_toolkit_completer(commands=commands, skills=skills, paths=paths),
        complete_while_typing=True,
        complete_style=CompleteStyle.COLUMN,
        key_bindings=_build_prompt_toolkit_key_bindings(
            commands=commands,
            skills=skills,
            paths=paths,
            workspace_root=workspace_root,
        ),
        reserve_space_for_menu=0,
        style=_build_prompt_toolkit_style(),
        history=history,
    )
    setattr(session, "_dan_reserve_space_for_menu", 0)
    if draft_path is not None:
        try:
            session.default_buffer.on_text_changed += (
                lambda _buffer: _write_tui_draft_text(draft_path, session.default_buffer.text)
            )
        except Exception:
            pass
    return session


def _readline_input(
    prompt: str,
    *,
    commands: Sequence[tuple[str, str]],
    skills: Sequence[TuiSkillSuggestion],
    paths: Sequence[TuiPathSuggestion | str] = (),
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
            candidates = _completion_candidates(line, commands=commands, skills=skills, paths=paths)
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
    paths: Sequence[TuiPathSuggestion | str] = (),
    workspace_root: Path | None = None,
) -> str:
    global _PROMPT_TOOLKIT_FALLBACK_WARNED
    if sys.stdin.isatty():
        try:
            with _tui_prompt_rounded_frame_border():
                session = _build_prompt_toolkit_session(
                    commands=commands,
                    skills=skills,
                    paths=paths,
                    workspace_root=workspace_root,
                    draft_path=_tui_draft_path(workspace_root) if workspace_root is not None else None,
                    history_path=_tui_prompt_history_path(workspace_root) if workspace_root is not None else None,
                )
                try:
                    from prompt_toolkit.patch_stdout import patch_stdout
                except Exception:
                    return session.prompt(
                        _tui_chatbox_prompt(prompt),
                        show_frame=True,
                    )
                with patch_stdout(raw=True):
                    return session.prompt(
                        _tui_chatbox_prompt(prompt),
                        show_frame=True,
                    )
        except Exception:
            if not _PROMPT_TOOLKIT_FALLBACK_WARNED:
                print(
                    "Dropdown suggestions unavailable; falling back to Tab completion.",
                    file=sys.stderr,
                )
                _PROMPT_TOOLKIT_FALLBACK_WARNED = True
            return _readline_input(prompt, commands=commands, skills=skills, paths=paths)
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


_TUI_BOARD_ADMISSION_EVENTS = {
    "tui.board.admission",
    "super.admission.completed",
    "super.task.admitted",
    "super.task.queued",
}
_TUI_BOARD_TERMINAL_STATUSES = {
    "appended",
    "blocked",
    "cancelled",
    "canceled",
    "completed",
    "failed",
    "needs-clarification",
    "reported",
    "stopped",
}
_TUI_BOARD_QUEUED_STATUSES = {
    "checkpoint-pending",
    "pending",
    "queued",
    "queued-behind",
    "waiting",
}
def _normalize_tui_board_status(value: Any, *, fallback: str = "running") -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    if not text:
        return fallback
    text = re.sub(r"\s+", "-", text)
    if "clarification" in text:
        return "needs-clarification"
    if "queue" in text or "waiting" in text:
        return "queued"
    if "append" in text:
        return "appended"
    if text in {"chat-or-status", "status", "status-only", "reported"}:
        return "reported"
    if "cancel" in text:
        return "cancel-pending" if "pending" in text else "cancelled"
    if "pause" in text:
        return "paused"
    if "parallel" in text or "started" in text or "accepted" in text:
        return "running"
    return text


def _tui_board_status_from_admission(event: Mapping[str, Any]) -> str:
    explicit = event.get("status")
    if explicit not in (None, ""):
        return _normalize_tui_board_status(explicit)
    decision = (
        event.get("decision")
        or event.get("admission")
        or event.get("admission_decision")
        or event.get("result")
    )
    return _normalize_tui_board_status(decision, fallback="running")


def _tui_board_bucket(row: TuiBoardRow) -> str:
    status = _normalize_tui_board_status(row.status)
    if status in _TUI_BOARD_QUEUED_STATUSES or "queue" in status or "waiting" in status or "pending" in status:
        return "queued"
    if (
        status in _TUI_BOARD_TERMINAL_STATUSES
        or status.startswith("done")
        or status.startswith("complete")
        or "failed" in status
    ):
        return "recent"
    return "active"


def _tui_board_row_is_status_only(row: TuiBoardRow) -> bool:
    status = _normalize_tui_board_status(row.status)
    phase = str(row.phase or "").strip().lower().replace("_", "-")
    if status != "reported" and phase != "chat-or-status":
        return False
    return not str(row.run_id or "").strip()


def _tui_board_event_paths(event: Mapping[str, Any]) -> list[str]:
    paths: list[str] = []
    for key in ("path", "target_path", "changed_path"):
        value = event.get(key)
        if value not in (None, ""):
            _append_unique(paths, value, limit=8)
    for key in ("paths", "target_paths", "changed_paths", "changed_files"):
        values = event.get(key)
        if isinstance(values, str):
            _append_unique(paths, values, limit=8)
        elif isinstance(values, SequenceABC):
            for value in values:
                if value not in (None, ""):
                    _append_unique(paths, value, limit=8)
    for payload_key in ("arguments", "result"):
        payload = event.get(payload_key)
        if isinstance(payload, Mapping):
            value = payload.get("path")
            if value not in (None, ""):
                _append_unique(paths, value, limit=8)
    return paths


def _tui_board_admission_text(event: Mapping[str, Any], row: TuiBoardRow) -> str:
    message = str(event.get("message") or event.get("admission_message") or "").strip()
    if message:
        return _clip(message, limit=220)
    decision = str(
        event.get("decision")
        or event.get("admission")
        or event.get("admission_decision")
        or event.get("result")
        or row.status
        or "accepted"
    ).strip()
    task = row.display_id
    against = str(event.get("against_task_id") or event.get("depends_on") or event.get("parent_task_id") or "").strip()
    if against and against not in decision:
        return f"{task}: {decision} ({against})"
    return f"{task}: {decision}"


def _tui_board_elapsed(row: TuiBoardRow) -> str:
    if not row.started_at_monotonic:
        return ""
    end = row.updated_at_monotonic or time.monotonic()
    return _format_elapsed_duration(end - row.started_at_monotonic)


def _format_tui_board_row(row: TuiBoardRow, *, width: int | None = None) -> list[str]:
    columns = int(width or shutil.get_terminal_size((120, 24)).columns or 120)
    line_limit = max(32, columns - 2)
    detail_limit = max(16, line_limit - 13)
    status = _normalize_tui_board_status(row.status)
    phase = str(row.phase or "").strip().replace("_", " ")
    status_text = status if not phase or phase == status or columns < 56 else f"{status}/{phase}"
    action = str(row.action or "").strip()
    objective = str(row.objective or "").strip()
    primary = action or objective or "waiting for visible work"
    available = max(16, line_limit - len(row.display_id) - len(status_text) - 8)
    line = f"- {row.display_id} [{status_text}] {_clip(primary, limit=available)}"
    meta: list[str] = []
    elapsed = _tui_board_elapsed(row)
    if elapsed:
        meta.append(elapsed)
    if row.run_id and row.run_id != row.task_id:
        meta.append(f"run {row.run_id}")
    if meta:
        line = f"{line} | {' | '.join(meta)}"
    lines = [_clip(line, limit=line_limit)]
    if objective and objective != primary:
        lines.append(f"  objective: {_clip(objective, limit=detail_limit)}")
    if row.intervention:
        lines.append(f"  intervention: {_clip(row.intervention, limit=detail_limit)}")
    if row.reason:
        lines.append(f"  reason: {_clip(row.reason, limit=detail_limit)}")
    if row.changed_paths:
        changed = ", ".join(row.changed_paths[-3:])
        lines.append(f"  changed: {_clip(changed, limit=detail_limit)}")
    if row.trace_ref:
        lines.append(f"  trace: {_clip(row.trace_ref, limit=detail_limit)}")
    if row.answer_lines:
        answer = " ".join(line for line in row.answer_lines[-2:] if line)
        if answer:
            lines.append(f"  answer: {_clip(answer, limit=detail_limit)}")
    return lines


def _clean_tui_task_text(value: Any, *, fallback: str = "", limit: int = 180) -> str:
    text = _hide_internal_run_metadata_from_display_line(value)
    text = " ".join(str(text or "").split())
    if not text:
        return fallback
    readable_events = {
        "model.requested": "asking the model for the next step",
        "model.responded": "model response received",
        "tool.started": "using a workspace tool",
        "tool.completed": "workspace tool finished",
        "validation.started": "checking the result",
        "validation.completed": "checks finished",
        "run.completed": "run finished",
        "run.failed": "run stopped with unresolved work",
    }
    if text in readable_events:
        return readable_events[text]
    if text[:1] in {"{", "["}:
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return fallback or "Background result available."
        if isinstance(parsed, MappingABC):
            for key in ("summary", "message", "status_text", "objective", "candidate_id"):
                candidate = parsed.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return _clip(candidate.strip(), limit=limit)
            change_summary = parsed.get("change_summary")
            if isinstance(change_summary, SequenceABC) and not isinstance(change_summary, (str, bytes)):
                values = [str(item).strip() for item in change_summary if str(item).strip()]
                if values:
                    return _clip("; ".join(values[:3]), limit=limit)
            fragment = parsed.get("candidate_fragment")
            if isinstance(fragment, MappingABC):
                names = [str(key).strip() for key in fragment.keys() if str(key).strip()]
                if names:
                    return _clip("Prepared candidate changes for " + ", ".join(names[:3]), limit=limit)
            return fallback or "Background result available."
        if isinstance(parsed, SequenceABC) and not isinstance(parsed, (str, bytes)):
            return fallback or "Background result available."
    return _clip(text, limit=limit)


def _short_tui_task_ref(row: TuiBoardRow) -> str:
    raw = str(row.task_id or row.run_id or row.display_id or "").strip()
    if not raw:
        return ""
    if len(raw) <= 12:
        return raw
    return raw[:8]


def _tui_board_row_matches(row: TuiBoardRow, target: str) -> bool:
    clean = str(target or "").strip().strip("`").strip()
    if clean.startswith("[") and clean.endswith("]"):
        clean = clean[1:-1].strip()
    if not clean:
        return False
    clean_lower = clean.lower()
    candidates: set[str] = set()
    for value in (row.task_id, row.run_id, row.display_id, _short_tui_task_ref(row)):
        text = str(value or "").strip()
        if not text:
            continue
        candidates.add(text)
        if len(text) > 12:
            candidates.add(text[:8])
    return clean_lower in {value.lower() for value in candidates}


def _task_status_sentence(label: str, row: TuiBoardRow, primary: str) -> str:
    if label == "Running":
        action = _clean_tui_task_text(row.action, limit=150)
        phase = _clean_tui_task_text(row.phase, limit=80).lower()
        if action and action.lower() not in {"run completed", "completed", "done", "run failed", "failed"}:
            return f"DAN is currently {action.rstrip('.')}."
        if "model" in phase:
            return "DAN is deciding the next concrete step from the current workspace context."
        if primary:
            return f"DAN is actively working on this request and will report concrete findings for: {primary}"
        return "DAN is actively working and will report concrete findings as soon as executor evidence is available."
    if label == "Queued":
        return "This is waiting until active work reaches a safe handoff."
    if label == "Failed":
        return "This finished with unresolved work; inspect it before depending on its outputs."
    return "This has finished and is kept here for context."


def _task_next_action(label: str, row: TuiBoardRow, *, has_recent_change: bool) -> str:
    if label == "Running":
        return "Next: wait for the next narrator update, or use /status <id> for a focused view."
    if label == "Queued":
        return "Next: wait, cancel it, or append clearer instructions before it starts."
    if label == "Failed":
        return "Next: rerun with a concrete repair request, or ask what blocked this task."
    if has_recent_change:
        return "Next: review the changed files before building on this result."
    return "Next: start a new task or ask a focused follow-up."


def _format_tui_task_overview_row(
    row: TuiBoardRow,
    *,
    width: int | None = None,
    workspace: str = "",
) -> list[str]:
    columns = int(width or shutil.get_terminal_size((120, 24)).columns or 120)
    detail_limit = max(48, columns - 8)
    bucket = _tui_board_bucket(row)
    status = _normalize_tui_board_status(row.status)
    if bucket == "active":
        label = "Running"
    elif bucket == "queued":
        label = "Queued"
    elif "failed" in status or "blocked" in status:
        label = "Failed"
    else:
        label = "Done"
    objective = _clean_tui_task_text(row.objective, limit=detail_limit)
    action = _clean_tui_task_text(row.action, limit=detail_limit)
    primary = objective or action or "visible task"
    ref = _short_tui_task_ref(row)
    suffix = f" [{ref}]" if ref else ""
    lines = [f"- {label}: {primary}{suffix}"]
    lines.append(f"  What: {_task_status_sentence(label, row, primary)}")
    generic_action = action.lower() in {"run completed", "completed", "done", "run failed", "failed"}
    if action and action != primary and not generic_action:
        lines.append(f"  Latest: {action}")
    reason = _clean_tui_task_text(row.reason, limit=detail_limit)
    reason_lower = reason.lower()
    if (
        reason
        and "no active or queued executor work" not in reason_lower
        and not reason_lower.startswith("queue depth")
    ):
        lines.append(f"  Note: {reason}")
    visible_paths = [
        path
        for path in _grouped_artifact_lines(
            row.changed_paths,
            label="Changed",
            workspace=workspace,
            include_internal=False,
            limit=3,
        )
        if path
    ]
    for path_line in visible_paths[:2]:
        lines.append("  " + path_line.lstrip("- "))
    lines.append(f"  {_task_next_action(label, row, has_recent_change=bool(visible_paths))}")
    return lines


def _format_tui_task_overview_lines(
    state: "SuperTuiState",
    *,
    target: str = "",
    width: int | None = None,
    include_controls: bool = True,
) -> list[str]:
    clean_target = str(target or "").strip()
    active, queued, recent = state.board_rows_by_bucket()
    lines: list[str] = []
    if clean_target:
        row = state.find_board_row(clean_target)
        if row is None:
            lines.append(f"No visible task matches `{clean_target}`.")
            if active or queued or recent:
                lines.append("Visible tasks:")
                for item in [*active, *queued, *recent[-3:]][:5]:
                    lines.extend(_format_tui_task_overview_row(item, width=width, workspace=state.workspace))
            return lines
        lines.extend(_format_tui_task_overview_row(row, width=width, workspace=state.workspace))
        if include_controls:
            lines.append("You can: /append <text>, /pause <id>, /resume <id>, or /stop <id>.")
        return lines
    if active:
        lines.append("Active")
        for row in active[:5]:
            lines.extend(_format_tui_task_overview_row(row, width=width, workspace=state.workspace))
    if queued:
        lines.append("Queued")
        for row in queued[:5]:
            lines.extend(_format_tui_task_overview_row(row, width=width, workspace=state.workspace))
    if not active and not queued:
        lines.append("No active executor work right now.")
    if recent:
        lines.append("Recent")
        for row in recent[-5:]:
            lines.extend(_format_tui_task_overview_row(row, width=width, workspace=state.workspace))
    if not active and not queued and not recent:
        lines.append("No task activity is visible yet.")
    if include_controls:
        lines.append(
            "You can: /status <id> for details, /append <text> to steer active work, "
            "/focus <id> to follow one task, or /new <objective> to start another."
        )
    return lines


def _format_tui_intervention_lines(*, width: int | None = None) -> list[str]:
    columns = int(width or shutil.get_terminal_size((120, 24)).columns or 120)
    line = (
        "Intervene: /tasks | /status [task] | /inside [task] | /new <objective> | /append <text> | "
        "/stop [task] | /pause [task] | /resume [task] | /focus <task>"
    )
    return textwrap.wrap(
        line,
        width=max(34, columns),
        subsequent_indent="  ",
        break_long_words=False,
        break_on_hyphens=False,
    ) or [line]


def _format_tui_board_progress_lines(
    state: "SuperTuiState",
    *,
    width: int | None = None,
    limit: int = 5,
) -> list[str]:
    columns = int(width or shutil.get_terminal_size((120, 24)).columns or 120)
    detail_limit = max(24, columns - 6)
    active, queued, recent = state.board_rows_by_bucket()
    lines: list[str] = []
    focused = state.find_board_row("")
    if focused is not None:
        action = str(focused.action or "").strip()
        if not action:
            action = str(focused.objective or "visible work").strip()
        lines.append(f"- Focus {focused.display_id}: {_clip(action, limit=detail_limit)}")
    elif state.current_step:
        lines.append(f"- Current: {_clip(state.current_step, limit=detail_limit)}")
    for row in active:
        if focused is not None and row.task_id == focused.task_id:
            continue
        action = str(row.action or row.objective or "").strip()
        if action:
            lines.append(f"- {row.display_id}: {_clip(action, limit=detail_limit)}")
        if len(lines) >= limit:
            break
    if len(lines) < limit and queued:
        queued_ids = ", ".join(row.display_id for row in queued[:4])
        suffix = f" ({len(queued)} queued)" if len(queued) > 4 else ""
        lines.append(f"- Queued: {queued_ids}{suffix}")
    if len(lines) < limit and recent:
        latest = recent[-1]
        lines.append(f"- Last finished: {latest.display_id} [{_normalize_tui_board_status(latest.status)}]")
    return lines[:limit]


def _format_tui_board_lane_summary(
    state: "SuperTuiState",
    *,
    include_recent: bool = False,
    limit: int = 8,
) -> list[str]:
    active, queued, recent = state.board_rows_by_bucket()
    rows: list[TuiBoardRow] = [*active, *queued]
    if include_recent:
        rows.extend(recent[-3:])
    lines: list[str] = []
    for row in rows[:limit]:
        status = _normalize_tui_board_status(row.status)
        phase = str(row.phase or "").strip().replace("_", " ")
        action = str(row.action or row.reason or row.objective or "").strip()
        phase_part = f"/{phase}" if phase and phase != status else ""
        action_part = f" - {_clip(action, limit=120)}" if action else ""
        lines.append(f"Board lane {row.display_id}: {status}{phase_part}{action_part}")
    return lines


def _format_tui_board_lines(
    state: "SuperTuiState",
    *,
    width: int | None = None,
    include_heading: bool = True,
    include_intervene: bool = True,
    include_empty: bool = False,
    include_recent: bool = True,
    include_progress: bool = True,
) -> list[str]:
    active, queued, recent = state.board_rows_by_bucket()
    has_rows = bool(active or queued or recent)
    if not has_rows and not state.latest_admission and not include_empty:
        return []
    lines: list[str] = []
    if state.latest_admission:
        lines.append(f"Admission: {_clip(state.latest_admission, limit=180)}")
    if include_heading:
        lines.append("Board:")
    sections: tuple[tuple[str, list[TuiBoardRow]], ...] = (
        ("Active", active),
        ("Queued", queued),
        ("Recent", recent if include_recent else []),
    )
    for label, rows in sections:
        if not rows and not include_empty:
            continue
        if label == "Recent" and not include_recent:
            continue
        lines.append(f"{label}:")
        if not rows:
            lines.append("  - none")
            continue
        for row in rows:
            for row_line in _format_tui_board_row(row, width=width):
                lines.append(f"  {row_line}")
    progress_lines = _format_tui_board_progress_lines(state, width=width) if include_progress else []
    if progress_lines:
        lines.append("Progress:")
        lines.extend(f"  {line}" for line in progress_lines)
    if include_intervene:
        lines.extend(_format_tui_intervention_lines(width=width))
    return lines


def _format_tui_board_status(
    state: "SuperTuiState",
    *,
    target: str = "",
    width: int | None = None,
) -> str:
    clean_target = str(target or "").strip()
    if not clean_target:
        return "\n".join(_format_tui_board_lines(state, width=width, include_empty=True))
    row = state.find_board_row(clean_target)
    if row is None:
        lines = [f"No visible task/run matches `{clean_target}`."]
        board = _format_tui_board_lines(state, width=width, include_empty=True, include_intervene=False)
        if board:
            lines.extend(board)
        return "\n".join(lines)
    lines = [f"Status: {row.display_id}"]
    lines.extend(_format_tui_board_row(row, width=width))
    lines.extend(_format_tui_intervention_lines(width=width))
    return "\n".join(lines)


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
    communication_policy: AgentCommunicationPolicy = field(
        default_factory=lambda: AgentCommunicationPolicy(answer_budget=ANSWER_BUDGET_DETAILED)
    )
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
    board_rows: dict[str, TuiBoardRow] = field(default_factory=dict)
    board_order: list[str] = field(default_factory=list)
    latest_admission: str = ""
    focused_board_id: str = ""
    tool_counts: dict[str, int] = field(default_factory=dict)
    _coalesce_indexes: dict[str, int] = field(default_factory=dict)
    started_at_monotonic: float = 0.0
    ended_at_monotonic: float = 0.0
    _last_narrator_report_key: str = ""

    def _board_row_id(self, event: Mapping[str, Any]) -> str:
        row_id = str(
            event.get("task_id")
            or event.get("run_id")
            or event.get("id")
            or self.task_id
            or self.focused_board_id
            or ""
        ).strip()
        return row_id or "current"

    def _ensure_board_row(
        self,
        row_id: str,
        *,
        objective: Any = "",
        status: Any = "",
        phase: Any = "",
        run_id: Any = "",
        action: Any = "",
        admission: Any = "",
        intervention: Any = "",
        reason: Any = "",
        trace_ref: Any = "",
        changed_paths: Sequence[Any] = (),
        answer_lines: Sequence[Any] = (),
    ) -> TuiBoardRow:
        clean_id = str(row_id or "").strip() or "current"
        now = time.monotonic()
        row = self.board_rows.get(clean_id)
        if row is None:
            row = TuiBoardRow(
                task_id=clean_id,
                started_at_monotonic=self.started_at_monotonic or now,
                updated_at_monotonic=now,
            )
            self.board_rows[clean_id] = row
            self.board_order.append(clean_id)
            del self.board_order[:-40]
        if objective not in (None, ""):
            row.objective = str(objective)
        if status not in (None, ""):
            row.status = _normalize_tui_board_status(status)
        if phase not in (None, ""):
            row.phase = str(phase)
        if run_id not in (None, ""):
            row.run_id = str(run_id)
        if action not in (None, ""):
            row.action = str(action)
        if admission not in (None, ""):
            row.admission = str(admission)
        if intervention not in (None, ""):
            row.intervention = str(intervention)
        if reason not in (None, ""):
            row.reason = str(reason)
        if trace_ref not in (None, ""):
            row.trace_ref = str(trace_ref)
        for path in changed_paths:
            if path not in (None, ""):
                _append_unique(row.changed_paths, str(path), limit=8)
        for answer_line in answer_lines:
            if answer_line not in (None, ""):
                _append_unique(row.answer_lines, str(answer_line), limit=6)
        row.updated_at_monotonic = now
        if not self.focused_board_id and _tui_board_bucket(row) == "active":
            self.focused_board_id = clean_id
        return row

    def find_board_row(self, target: str) -> TuiBoardRow | None:
        clean = str(target or "").strip()
        if not clean:
            clean = self.focused_board_id
        if not clean:
            return None
        if clean in self.board_rows:
            return self.board_rows[clean]
        for row in self.board_rows.values():
            if _tui_board_row_matches(row, clean):
                return row
        return None

    def board_rows_by_bucket(self) -> tuple[list[TuiBoardRow], list[TuiBoardRow], list[TuiBoardRow]]:
        active: list[TuiBoardRow] = []
        queued: list[TuiBoardRow] = []
        recent: list[TuiBoardRow] = []
        for row_id in self.board_order:
            row = self.board_rows.get(row_id)
            if row is None:
                continue
            if _tui_board_row_is_status_only(row):
                continue
            bucket = _tui_board_bucket(row)
            if bucket == "queued":
                queued.append(row)
            elif bucket == "recent":
                recent.append(row)
            else:
                active.append(row)
        return active, queued, recent[-6:]

    def board_signature(self) -> str:
        rows = []
        for row_id in self.board_order:
            row = self.board_rows.get(row_id)
            if row is None:
                continue
            rows.append(
                {
                    "task_id": row.task_id,
                    "run_id": row.run_id,
                    "status": row.status,
                    "phase": row.phase,
                    "action": row.action,
                    "intervention": row.intervention,
                    "reason": row.reason,
                    "changed_paths": list(row.changed_paths[-4:]),
                    "answer_lines": list(row.answer_lines[-2:]),
                    "trace_ref": row.trace_ref,
                }
            )
        return json.dumps(
            {"admission": self.latest_admission, "focused": self.focused_board_id, "rows": rows},
            sort_keys=True,
            default=str,
        )

    def attach_answer_to_board_row(
        self,
        *,
        row_id: str = "",
        lines: Sequence[Any] = (),
    ) -> None:
        answer_lines = [str(line or "").strip() for line in lines if str(line or "").strip()]
        if not answer_lines:
            return
        target = self.find_board_row(row_id or self.task_id or self.focused_board_id)
        if target is None:
            return
        target.answer_lines.clear()
        for line in answer_lines[-_ANSWER_LINE_COUNT_LIMIT:]:
            _append_unique(target.answer_lines, line, limit=_ANSWER_LINE_COUNT_LIMIT)
        target.updated_at_monotonic = time.monotonic()

    def _observe_board_event(self, name: str, event: Mapping[str, Any]) -> str:
        if name == "tui.board.intervention":
            row_id = self._board_row_id(event)
            row = self._ensure_board_row(
                row_id,
                objective=event.get("objective") or event.get("prompt") or self.objective,
                status=event.get("status") or "",
                phase=event.get("phase") or "",
                run_id=event.get("run_id") or "",
                action=event.get("action") or event.get("current_action") or "",
                intervention=event.get("intervention") or event.get("command") or "",
                reason=event.get("reason") or "",
                trace_ref=event.get("trace_ref") or event.get("event_log_path") or "",
                changed_paths=_tui_board_event_paths(event),
            )
            message = str(event.get("message") or "").strip()
            if not message:
                message = f"{row.display_id}: {row.intervention or row.action or row.status}"
            row.admission = message
            self.latest_admission = message
            if event.get("focused"):
                self.focused_board_id = row.task_id
            return message
        if name in _TUI_BOARD_ADMISSION_EVENTS:
            row_id = self._board_row_id(event)
            row = self._ensure_board_row(
                row_id,
                objective=event.get("objective") or event.get("prompt") or self.objective,
                status=_tui_board_status_from_admission(event),
                phase=event.get("phase") or "",
                run_id=event.get("run_id") or "",
                action=event.get("action") or event.get("current_action") or "",
                reason=event.get("reason") or event.get("conflict_reason") or "",
                trace_ref=event.get("trace_ref") or event.get("event_log_path") or "",
                changed_paths=_tui_board_event_paths(event),
            )
            admission = _tui_board_admission_text(event, row)
            row.admission = admission
            self.latest_admission = admission
            if _tui_board_bucket(row) == "active":
                self.focused_board_id = row.task_id
            return admission
        if name == "run.log.started":
            row = self._ensure_board_row(
                self._board_row_id(event),
                objective=event.get("objective") or self.objective,
                status="running",
                phase="starting",
                run_id=event.get("run_id") or event.get("task_id") or self.task_id,
                action="accepted; starting",
                trace_ref=event.get("event_log_path") or "",
            )
            if not self.latest_admission:
                self.latest_admission = f"{row.display_id}: accepted; running"
            self.focused_board_id = row.task_id
            return self.latest_admission
        if name in {"live.generic_execution.started", "live.generic_build.started"}:
            self._ensure_board_row(
                self._board_row_id(event),
                objective=self.objective,
                status="running",
                phase="building",
                action="working in the workspace",
            )
        elif name == "live.planning.started":
            self._ensure_board_row(
                self._board_row_id(event),
                objective=self.objective,
                status="running",
                phase="planning",
                action="planning the run",
            )
        elif name in {"model.requested", "model.responded"}:
            self._ensure_board_row(
                self._board_row_id(event),
                objective=self.objective,
                status="running",
                phase=self.phase,
                action=self.current_step,
            )
        elif name == "provider.build.failed":
            self._ensure_board_row(
                self._board_row_id(event),
                objective=self.objective,
                status="failed",
                phase=self.phase,
                action=self.current_step,
                reason=_tui_failure_detail(event),
            )
        elif name == "tool.started":
            tool_id = str(event.get("tool_id") or "tool")
            self._ensure_board_row(
                self._board_row_id(event),
                objective=self.objective,
                status="running",
                phase="tool",
                action=self.current_step or f"running {tool_id}",
            )
        elif name == "tool.completed":
            tool_id = str(event.get("tool_id") or "tool")
            paths = _tui_board_event_paths(event) if tool_id in {"file_write", "file_edit"} else ()
            self._ensure_board_row(
                self._board_row_id(event),
                objective=self.objective,
                status="running",
                phase=self.phase,
                action=self.current_step,
                changed_paths=paths,
            )
        elif name in {"tool.failed", "tool.denied"}:
            self._ensure_board_row(
                self._board_row_id(event),
                objective=self.objective,
                status="blocked",
                phase=self.phase,
                action=self.current_step,
                reason=event.get("error") or name,
            )
        elif name == "live.validation.started":
            self._ensure_board_row(
                self._board_row_id(event),
                objective=self.objective,
                status="running",
                phase="validation",
                action="validation running",
            )
        elif name in {"live.validation.model_completed", "live.validation.completed"}:
            self._ensure_board_row(
                self._board_row_id(event),
                objective=self.objective,
                status="running",
                phase="validation",
                action=self.current_step,
            )
        elif name == "super.hook.packet_enqueued":
            inbox = str(event.get("inbox_id") or "inbox")
            depth = event.get("queue_depth", "?")
            row = self._ensure_board_row(
                self._board_row_id(event) if event.get("task_id") or event.get("run_id") else f"queue:{inbox}",
                objective=f"follow-up for {inbox}",
                status="queued",
                phase="queued",
                action=f"waiting in {inbox}",
                reason=f"queue depth {depth}",
            )
            if not self.latest_admission:
                self.latest_admission = f"{row.display_id}: queued"
        elif name in {"run.log.completed", "run.log.failed"}:
            status = str(event.get("status") or ("failed" if name.endswith("failed") else "completed"))
            self._ensure_board_row(
                self._board_row_id(event),
                objective=self.objective,
                status=status,
                phase=self.phase,
                action=self.current_step or f"run {status}",
                reason=_tui_failure_detail(event) if status != "completed" else "",
                trace_ref=event.get("event_log_path") or self.event_log_path,
            )
        return ""

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
        board_activity = _format_tui_board_lane_summary(self, include_recent=True)
        activity_lines = list(board_activity)
        for item in self.activity_lines[-10:] or self.timeline[-10:]:
            if item not in activity_lines:
                activity_lines.append(item)
        queued_work = self.queue_status
        queued_lanes = [
            line
            for line in board_activity
            if "queued" in line.lower() or "pending" in line.lower() or "waiting" in line.lower()
        ]
        if queued_lanes:
            queued_work = "; ".join(queued_lanes[:4])
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
            activity=tuple(activity_lines[-12:]),
            results=tuple(self._result_event_lines(include_answers=False)[-10:]),
            changed_files=tuple(self.changed_files[-8:]),
            artifacts=tuple(self.artifacts[-8:]),
            validation=self.validation,
            validation_score=self.validation_score,
            blockers=tuple(self.blockers[-6:]),
            queued_work=queued_work,
            trace_refs=tuple(path for path in trace_refs if path),
            source_event_count=len(self.raw_events),
        )

    def _record_narrator_report(self, report: NarratorReport, *, limit: int = 10) -> None:
        if self.communication_policy.progress_detail == PROGRESS_QUIET and report.kind not in {"blocker", "final"}:
            return
        text = _sanitize_tui_progress_line(report.text, objective=self.objective)
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
        if report.kind != "final" and _tui_progress_text_redundant(text, self.narrator_lines):
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
        self.communication_policy = decision.communication_policy

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
        elif name == "provider.build.failed":
            self.phase = "model setup failed"
            self.model = str(event.get("model") or event.get("requested_model") or self.model)
            detail = _clip(_tui_failure_detail(event) or "provider setup failed", limit=220)
            self.current_step = f"Model provider failed: {detail}"
            line = f"Model provider failed: {detail}"
            self._record_progress(line)
            self._record_result(line)
            _append_unique(self.blockers, detail, limit=5)
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
        elif name == "live.validation.shell_check.started":
            self.phase = "validation"
            self.validation = "running"
            command = _clip(event.get("command") or "validation command", limit=140)
            self.current_step = f"Running validation command: {command}"
            line = f"Validation command started: {command}"
            self._record_progress(line)
            self._record_timeline(line, coalesce_key="validation:shell")
        elif name == "live.validation.shell_check.completed":
            self.phase = "validation"
            passed = bool(event.get("passed"))
            exit_code = event.get("exit_code")
            status = "passed" if passed else "failed"
            self.validation = status
            command = _clip(event.get("command") or "validation command", limit=100)
            suffix = f" exit {exit_code}" if exit_code is not None else ""
            self.current_step = f"Validation command {status}{suffix}: {command}"
            line = f"Validation command {status}{suffix}: {command}"
            self._record_progress(line)
            self._record_result(line)
            failure = str(event.get("failure") or "").strip()
            if failure:
                _append_unique(self.blockers, failure, limit=5)
                self._record_result(f"Validation gap: {_clip(failure, limit=140)}")
        elif name == "live.validation.shell_check.short_circuited":
            self.phase = "validation"
            self.validation = "failed"
            skipped = int(event.get("skipped_count") or 0)
            self.current_step = f"Validation stopped after failed command; skipped {skipped}"
            line = f"Validation stopped after a failed command; skipped {skipped} later check(s)."
            self._record_progress(line)
            self._record_result(line)
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
            if self.status == "completed":
                self.current_step = "Run completed"
                line = "Done."
            else:
                detail = _clip(_tui_failure_detail(event), limit=220)
                self.current_step = f"Run {self.status}: {detail}" if detail else f"Run {self.status}"
                line = f"The run {self.status}: {detail}." if detail else f"The run {self.status}."
                if detail:
                    _append_unique(self.blockers, detail, limit=5)
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
        board_line = self._observe_board_event(name, event)
        self._maybe_record_narrator_report(name, event)
        if name in {"run.log.completed", "run.log.failed"} and self.answer_lines:
            self.attach_answer_to_board_row(row_id=self._board_row_id(event), lines=self.answer_lines)
        if line:
            _append_unique(self.recent, line, limit=8)
        elif board_line:
            _append_unique(self.recent, board_line, limit=8)
        return line or board_line

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
        row_id = self.task_id or self.focused_board_id or "current"
        row = self._ensure_board_row(
            row_id,
            objective=self.objective,
            status=self.status,
            phase=self.phase,
            action=self.current_step,
            trace_ref=self.event_log_path,
            changed_paths=list(self.changed_files[-8:]),
        )
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
        if self.answer_lines:
            row.answer_lines.clear()
            for line in self.answer_lines[-_ANSWER_LINE_COUNT_LIMIT:]:
                _append_unique(row.answer_lines, line, limit=_ANSWER_LINE_COUNT_LIMIT)

    def transcript_summary(self, *, exit_code: int | None = None) -> str:
        if self.answer_lines and self.status in {"completed", "failed", "blocked", "stopped"}:
            answer = "\n".join(self.final_answer_lines()).strip()
            if self.event_log_path:
                return f"{answer} Trace: {self.event_log_path}"
            return answer
        status = self.status or ("completed" if exit_code == 0 else "finished")
        pieces = [f"{status}"]
        if self.validation:
            pieces.append(f"checks {self.validation}")
        if self.changed_files:
            changed = ", ".join(self.changed_files[-4:])
            pieces.append(f"changed {changed}")
        elif self.artifacts:
            artifacts = ", ".join(self.artifacts[-4:])
            pieces.append(f"artifacts {artifacts}")
        elif self.answer_lines:
            pieces.append("answer " + _clip("; ".join(self.answer_lines[:2]), limit=160))
        current_blockers = self._current_blocker_lines()
        if current_blockers:
            pieces.append(f"blockers {len(current_blockers)}")
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
        limit = min(int(limit), _tui_answer_line_limit(self.communication_policy))
        if self.is_narrator_mode():
            lines = [
                cleaned
                for line in _narrator_visible_answer_lines(self.answer_lines, limit=limit)
                for cleaned in [_hide_internal_run_metadata_from_display_line(line)]
                if cleaned
            ]
        else:
            lines = [
                cleaned
                for line in self.answer_lines
                for cleaned in [_hide_internal_run_metadata_from_display_line(line)]
                if cleaned
            ]
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

    def _current_blocker_lines(self) -> list[str]:
        if self.status == "completed" and self.validation == "passed":
            return []
        return list(self.blockers[-4:])

    def final_outcome_lines(self, *, include_trace: bool = False, limit: int = 12) -> list[str]:
        lines: list[str] = []
        if self.status and self.status != "completed":
            lines.append(f"- Status: {self.status}")
        if self.validation:
            lines.append(f"- Checks: {self.validation}")
        seen_paths: set[str] = set()
        lines.extend(
            _grouped_artifact_lines(
                self.changed_files[-8:],
                label="Changed",
                seen=seen_paths,
                workspace=self.workspace,
                limit=8,
            )
        )
        lines.extend(
            _grouped_artifact_lines(
                self.artifacts[-8:],
                label="Artifact",
                seen=seen_paths,
                workspace=self.workspace,
                limit=8,
            )
        )
        for item in self._current_blocker_lines():
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
            return "\n".join(
                self.final_summary_lines(include_trace=self.status in {"failed", "blocked", "stopped"})
            )
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
            lines.extend(_grouped_artifact_lines(self.changed_files[-8:], label="Changed", workspace=self.workspace, limit=8))
        if self.artifacts:
            lines.append("Artifacts:")
            lines.extend(_grouped_artifact_lines(self.artifacts[-8:], label="Artifact", workspace=self.workspace, limit=8))
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
            return self.final_summary_lines(include_trace=self.status in {"failed", "blocked", "stopped"})
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
        title = Text(_tui_section_title("Session"), style="bold cyan")
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
            Panel(recent, title=_tui_panel_title(panel_title), border_style=_tui_section_border_style(panel_title)),
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
        line_clock: bool = False,
        suppress_clock: bool = False,
        chatbox_progress: bool = False,
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
        self._line_clock = bool(line_clock)
        self._suppress_clock = bool(suppress_clock)
        self._chatbox_progress = bool(chatbox_progress)

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
        self._start_clock()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._stop_clock()
        self._clear_clock_line()
        if self._live is not None:
            self._live.stop()
            self._live = None

    def _start_clock(self) -> None:
        if not self.enabled or self._suppress_clock:
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
        rendered = _style_tui_clock_text(_format_tui_clock_text(footer))
        if not rendered:
            return
        if _tui_stdout_supports_control_sequences() and not self._line_clock:
            sys.stdout.write("\r\x1b[2K" + rendered)
        else:
            sys.stdout.write(_format_tui_clock_text(footer) + "\n")
        sys.stdout.flush()
        self._clock_line_active = True

    def _chatbox_progress_lines(self, *lines: str) -> list[str]:
        clean_lines = [str(line or "").strip() for line in lines if str(line or "").strip()]
        elapsed = _tui_chatbox_thinking_text(self.state.started_at_monotonic or None)
        if not clean_lines:
            return [elapsed]
        if clean_lines[0].startswith("Thinking "):
            return clean_lines
        return [elapsed, *clean_lines]

    def _print_progress_block(self, title: str, lines: Sequence[str]) -> None:
        if self._chatbox_progress:
            _print_tui_stream_block(
                "Chat -> Thinking",
                self._chatbox_progress_lines(*[str(line or "") for line in lines]),
                plain=self._plain,
            )
            return
        _print_tui_stream_block(title, lines, plain=self._plain)

    def _print_progress_line(self, text: str) -> None:
        if self._chatbox_progress:
            _print_tui_stream_block(
                "Chat -> Thinking",
                self._chatbox_progress_lines(text),
                plain=self._plain,
            )
            return
        _print_tui_stream_line(text, plain=self._plain)

    def _clear_clock_line(self) -> None:
        if not self._clock_line_active:
            return
        if self._line_clock:
            self._clock_line_active = False
            return
        if _tui_stdout_supports_control_sequences():
            sys.stdout.write("\r\x1b[2K")
            sys.stdout.flush()
        else:
            self._clock_line_active = False
            return
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
            if (
                narrator_line
                and narrator_line != self._last_narrator_line
                and not _tui_progress_line_redundant(narrator_line, self._last_narrator_line)
            ):
                self._last_narrator_line = narrator_line
                self._last_line = narrator_line
                self._clear_clock_line()
                self._print_progress_line(narrator_line)
            return
        if self.state.debug_events and line and line != self._last_line:
            self._last_line = line
            self._clear_clock_line()
            self._print_progress_line(line)

    def _model_sidecar_enabled(self) -> bool:
        args = self._sidecar_args
        return bool(
            args is not None
            and not self.state.debug_events
            and self.state.communication_policy.progress_detail != PROGRESS_QUIET
            and getattr(args, "_tui_routed_with_model", False)
        )

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
            "board": self.state.board_signature(),
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
                max_tokens=min(_tui_answer_max_tokens(self.state.communication_policy), 700),
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
        line_limit = 6 if self.state.communication_policy.progress_detail == PROGRESS_VERBOSE else 4
        lines = [
            clean
            for clean in (
                _sanitize_tui_progress_line(line, objective=self.state.objective)
                for line in _split_answer_lines(text, limit=line_limit)
            )
            if clean
        ]
        if not lines:
            return
        with self._sidecar_lock:
            if self.state.is_terminal():
                return
            recent_lines = _tui_progress_recent_lines(self.state.narrator_lines)
            fresh_lines: list[str] = []
            for line in lines:
                if any(_tui_progress_line_redundant(line, prior) for prior in [*recent_lines, *fresh_lines]):
                    continue
                fresh_lines.append(line)
            if not fresh_lines:
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
                        "text": "\n".join(fresh_lines),
                        "stale": True,
                        "source_snapshot_id": request.snapshot.snapshot_id,
                        "current_snapshot_id": current_snapshot.snapshot_id,
                    }
                )
                if len(self.state.narrator_reports) > 10:
                    del self.state.narrator_reports[: len(self.state.narrator_reports) - 10]
                return
            block_text = "\n".join(fresh_lines)
            if _tui_progress_text_redundant(block_text, self.state.narrator_lines):
                return
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
        self._print_progress_block("Narrator", fresh_lines)

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
            self._print_progress_line(text)

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
        outcome = self.state.final_outcome_lines(
            include_trace=self.state.status in {"failed", "blocked", "stopped"}
        )
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
        "Interactive commands: /plan, /progress, /tasks, /status [task], /inside [task], /new <objective>, "
        "/skills, /reset [all|state], /append, /pause, /resume, /stop, /focus, /help, /exit. "
        "Typing / opens command suggestions, $ opens skill suggestions, and @ opens workspace path suggestions when prompt_toolkit is available. "
        "Explicit slash commands route locally; natural-language input is model-routed as narrator read-only, executor read-only, executor write, or clarification. "
        "Progress/status questions use the snapshot-only narrator lane; workspace inspection stays read-only; "
        "write requests use direct simple writes or the normal Super DAN execution path. "
        "The interactive shell keeps the composer live while a submitted turn is running, similar to "
        "a chat box. Additional ordinary turns queue behind the current one; explicit --async-agent "
        "or --server sessions use V2 async admission for true background Agent runs."
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
    parser.add_argument(
        "--server",
        dest="server_url",
        default="",
        help="Optional DAN server URL for shared server-backed async Agent admission.",
    )
    parser.add_argument(
        "--async-agent",
        action="store_true",
        default=str(os.environ.get("DAN_SUPER_TUI_ASYNC", "")).strip().lower() in {"1", "true", "yes", "on"},
        help=(
            "Use V2 async admission in interactive mode even when no active work is visible. "
            "--server switches the transport to HTTP."
        ),
    )
    parser.add_argument(
        "--no-async-agent",
        action="store_true",
        help="Disable async Agent admission and use the older direct blocking local execution path.",
    )
    parser.add_argument(
        "--async-agent-backend",
        default=os.environ.get("DAN_SUPER_TUI_ASYNC_BACKEND", "super_dan"),
        help="V2 Agent backend for async admitted runs (default: super_dan).",
    )
    parser.add_argument(
        "--async-agent-max-parallel",
        type=int,
        default=int(os.environ.get("DAN_SUPER_TUI_ASYNC_MAX_PARALLEL", "4") or "4"),
        help="Maximum parallel V2 Agent runs admitted for this TUI board (default: 4).",
    )
    parser.add_argument(
        "--async-agent-timeout",
        type=float,
        default=float(os.environ.get("DAN_SUPER_TUI_ASYNC_TIMEOUT", "20") or "20"),
        help="HTTP timeout in seconds for foreground async admission (default: 20).",
    )
    return parser


def _argv_has_option(argv: Sequence[str], option: str) -> bool:
    return option in set(argv)


def _prepare_args(args: argparse.Namespace, raw_argv: Sequence[str]) -> None:
    setattr(args, "_live_explicit", _argv_has_option(raw_argv, "--live"))
    setattr(args, "_model_explicit", _argv_has_option(raw_argv, "--model"))
    setattr(args, "_artifact_dir_explicit", _argv_has_option(raw_argv, "--artifact-dir"))
    setattr(args, "_server_explicit", _argv_has_option(raw_argv, "--server"))
    setattr(args, "_async_agent_explicit", _argv_has_option(raw_argv, "--async-agent"))
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
        line_clock=bool(getattr(args, "_tui_background_dispatch", False)),
        suppress_clock=bool(getattr(args, "_tui_background_dispatch", False)),
        chatbox_progress=bool(getattr(args, "_tui_background_dispatch", False)),
    )
    renderer.state.set_intent_decision(getattr(args, "_tui_intent_decision", None))
    renderer.configure_model_sidecar(args)
    for token in getattr(args, "_tui_selected_skill_mentions", []) or []:
        _append_unique(renderer.state.recent, f"skill selected: ${token}", limit=8)
    turn_started_wall = time.time()
    live_report_printed = False

    def _renderer_factory(*, enabled: bool, args: argparse.Namespace) -> SuperTuiProgressRenderer:
        del enabled, args
        return renderer

    args._progress_renderer_factory = _renderer_factory
    setattr(args, "_tui_surface", True)
    setattr(args, "_suppress_live_failed_stderr", True)

    def _live_report_printer(report: Any, live_result: dict[str, Any], *, verbose: bool = False) -> None:
        nonlocal live_report_printed
        del report, verbose
        live_report_printed = True
        renderer.state.apply_live_result(live_result)
        _run_tui_final_model_answer(args, renderer.state)
        renderer.print_current_live_report()

    args._live_report_printer = _live_report_printer
    with renderer:
        exit_code = super_cli._run_super_turn(args, parser)
    if exit_code != 0 and not live_report_printed and not bool(getattr(args, "json", False)):
        replayed_state = _load_recent_super_event_log_state(
            Path(workspace),
            fallback_state=renderer.state,
            started_after=turn_started_wall,
        )
        if replayed_state is not None and replayed_state.is_terminal():
            renderer.state = replayed_state
            _render_static_state(
                renderer.state,
                plain=bool(getattr(args, "plain", False)),
                raw_events=bool(getattr(args, "raw_events", False)),
            )
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
                "communication_policy": renderer.state.communication_policy.to_payload(),
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


def _load_recent_super_event_log_state(
    workspace_root: Path,
    *,
    fallback_state: SuperTuiState,
    started_after: float = 0.0,
) -> SuperTuiState | None:
    event_log_path = _latest_super_event_log(workspace_root)
    if event_log_path is None or not event_log_path.exists():
        return None
    if started_after:
        try:
            if event_log_path.stat().st_mtime + 1.0 < started_after:
                return None
        except OSError:
            return None
    state = SuperTuiState(
        objective=fallback_state.objective,
        workspace=fallback_state.workspace or str(workspace_root),
        task_id=fallback_state.task_id,
        debug_events=fallback_state.debug_events,
    )
    try:
        for row in _read_event_log(event_log_path):
            state.observe(row)
    except (OSError, json.JSONDecodeError):
        return None
    if not state.event_log_path:
        state.event_log_path = str(event_log_path)
    return state


def _render_static_state(state: SuperTuiState, *, plain: bool, raw_events: bool = False) -> None:
    state.debug_events = bool(raw_events)
    if state.is_terminal() and not state.debug_events:
        answer = state.final_answer_lines()
        if answer:
            _print_tui_stream_block("Answer", answer, plain=plain)
        outcome = state.final_outcome_lines(include_trace=state.status in {"failed", "blocked", "stopped"})
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


def _load_tui_board_source_state(workspace_root: Path) -> SuperTuiState:
    transcript_entries = _read_tui_transcript(workspace_root, limit=80)
    state, _event_count = _load_narrator_source_state(workspace_root, transcript_entries)
    for entry in transcript_entries:
        board_events = entry.metadata.get("tui_board_events") if isinstance(entry.metadata, MappingABC) else None
        if isinstance(board_events, SequenceABC) and not isinstance(board_events, (str, bytes)):
            for board_event in board_events:
                if isinstance(board_event, MappingABC):
                    state.observe(dict(board_event))
        board_event = entry.metadata.get("tui_board_event") if isinstance(entry.metadata, MappingABC) else None
        if isinstance(board_event, MappingABC):
            state.observe(dict(board_event))
        focused = str(entry.metadata.get("focused_board_id") or "").strip() if isinstance(entry.metadata, MappingABC) else ""
        if focused and state.find_board_row(focused):
            state.focused_board_id = focused
    if not state.workspace:
        state.workspace = str(workspace_root)
    return state


def _format_tui_tasks_command(workspace_root: Path, *, target: str = "") -> str:
    state = _load_tui_board_source_state(workspace_root)
    text = _format_tui_board_status(state, target=target)
    if state.queue_status and "Queue:" not in text:
        text = f"{text}\nQueue: {state.queue_status}"
    return text


def _resolve_tui_event_log_path(workspace_root: Path, raw: str) -> Path | None:
    value = str(raw or "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = workspace_root / path
    return path


def _format_tui_inside_agent_event(event: Any) -> str:
    payload = event if isinstance(event, MappingABC) else getattr(event, "model_dump", lambda **_: {})()
    if not isinstance(payload, MappingABC):
        payload = getattr(event, "__dict__", {})
    event_type = str(payload.get("type") or payload.get("event") or "event").strip()
    summary = str(payload.get("summary") or "").strip()
    source = str(payload.get("source_event_type") or payload.get("source_event") or "").strip()
    if summary:
        text = f"{event_type}: {summary}"
    elif source:
        text = f"{event_type}: {source}"
    else:
        text = event_type
    artifact_refs = payload.get("artifact_refs")
    if isinstance(artifact_refs, SequenceABC) and not isinstance(artifact_refs, (str, bytes)) and artifact_refs:
        labels: list[str] = []
        for item in artifact_refs[:3]:
            if isinstance(item, MappingABC):
                label = str(item.get("path") or item.get("name") or item.get("uri") or "").strip()
            else:
                label = str(item or "").strip()
            if label:
                labels.append(label)
        if labels:
            text = f"{text}; artifacts: {', '.join(labels)}"
    return _clip(text, limit=260)


def _load_tui_inside_event_lines(workspace_root: Path, row: TuiBoardRow) -> list[str]:
    lines: list[str] = []
    if row.run_id:
        try:
            store = _open_tui_local_async_store(workspace_root)
            events = store.load_run_events(row.run_id)
        except Exception:
            events = []
        for event in events[-8:]:
            line = _format_tui_inside_agent_event(event)
            if line:
                _append_unique(lines, line, limit=8)
    trace_path = _resolve_tui_event_log_path(workspace_root, row.trace_ref)
    if trace_path is not None and trace_path.exists():
        try:
            trace_state = SuperTuiState(workspace=str(workspace_root))
            for event in _read_event_log(trace_path):
                trace_state.observe(event)
            for line in [*trace_state.activity_lines[-8:], *trace_state.recent_event_lines()[-8:]]:
                if line:
                    _append_unique(lines, line, limit=16)
        except Exception:
            pass
    return lines


def _is_tui_inside_activity_line(line: str) -> bool:
    clean = str(line or "").strip()
    if not clean:
        return False
    lowered = clean.lower()
    if lowered.startswith(("objective:", "trace:", "intervene:", "narrator:", "working:")):
        return False
    if clean.startswith("| /"):
        return False
    return True


def _format_tui_inside_lines(
    state: SuperTuiState,
    *,
    workspace_root: Path,
    target: str = "",
) -> list[str]:
    row = state.find_board_row(target)
    if row is None and not str(target or "").strip():
        active, queued, recent = state.board_rows_by_bucket()
        if active:
            row = active[0]
        elif queued:
            row = queued[0]
        elif recent:
            row = recent[-1]
    if row is None:
        lines = ["No visible task matches that request."]
        active, queued, recent = state.board_rows_by_bucket()
        visible = [*active, *queued, *recent[-3:]]
        if visible:
            labels = ", ".join(item.display_id for item in visible[:6])
            lines.append(f"Visible tasks: {labels}.")
        else:
            lines.append("There is no task activity in the visible TUI board yet.")
        return lines

    task_label = row.display_id or row.task_id or row.run_id
    lines = [f"Looking inside `{task_label}`."]
    objective = _clean_tui_task_text(row.objective, limit=260)
    if objective:
        lines.append(f"It is working on: {objective}")
    status_bits = [bit for bit in (_normalize_tui_board_status(row.status), row.phase) if str(bit or "").strip()]
    if status_bits:
        lines.append(f"Current state: {'; '.join(status_bits)}.")
    action = _clean_tui_task_text(row.action, limit=260)
    if action:
        lines.append(f"Latest visible movement: {action}")
    reason = _clean_tui_task_text(row.reason, limit=260)
    if reason and "no active or queued executor work" not in reason.lower():
        lines.append(f"Why it is in this lane: {reason}")

    activity = [line for line in _load_tui_inside_event_lines(workspace_root, row) if _is_tui_inside_activity_line(line)]
    if activity:
        lines.append("Recent internal activity:")
        lines.extend(f"- {line}" for line in activity[-10:])
    else:
        lines.append("No detailed event stream is visible yet; the task may still be in routing/admission or using a remote backend.")

    visible_paths = _grouped_artifact_lines(
        row.changed_paths,
        label="Changed",
        workspace=str(workspace_root),
        include_internal=False,
        limit=5,
    )
    if visible_paths:
        lines.append("Visible files:")
        lines.extend(visible_paths[:5])
    if row.trace_ref:
        lines.append(f"Trace: {row.trace_ref}")
    lines.append(f"Controls: `/status {task_label}`, `/append ...`, `/stop {task_label}`.")
    return lines


def _render_tui_inside(
    args: argparse.Namespace,
    workspace_root: Path,
    *,
    target: str = "",
    result: TuiAsyncAdmissionResult | None = None,
) -> None:
    if result is not None and not result.ok:
        _print_tui_stream_block(
            "Inside",
            [_clip(result.message, limit=500)],
            plain=bool(getattr(args, "plain", False)),
        )
        return
    state = _state_with_tui_async_events(workspace_root, result)
    lines = _format_tui_inside_lines(state, workspace_root=workspace_root, target=target)
    _print_tui_stream_block("Inside", lines, plain=bool(getattr(args, "plain", False)))


def _state_with_tui_async_events(workspace_root: Path, result: TuiAsyncAdmissionResult | None = None) -> SuperTuiState:
    state = _load_tui_board_source_state(workspace_root)
    if result is not None:
        for event in result.events:
            state.observe(dict(event))
    return state


def _render_tui_task_overview(
    args: argparse.Namespace,
    workspace_root: Path,
    *,
    title: str = "Tasks",
    target: str = "",
    result: TuiAsyncAdmissionResult | None = None,
) -> None:
    if result is not None and not result.ok:
        _print_tui_stream_block(
            title,
            [_clip(result.message, limit=500)],
            plain=bool(getattr(args, "plain", False)),
        )
        return
    state = _state_with_tui_async_events(workspace_root, result)
    lines = _format_tui_task_overview_lines(state, target=target)
    _print_tui_stream_block(title, lines, plain=bool(getattr(args, "plain", False)))


def _render_tui_text_command(args: argparse.Namespace, title: str, text: str) -> None:
    lines = [_clip(line, limit=2000) for line in str(text or "").splitlines() if line.strip()]
    _print_tui_stream_block(title, lines or ["No output."], plain=bool(getattr(args, "plain", False)))


def _tui_help_lines() -> list[str]:
    return [
        "Chat normally. DAN will decide whether the request is progress, read-only inspection, or workspace work.",
        "Most useful commands:",
        "- /tasks - see active, queued, and recent work",
        "- /status <id> - open one task",
        "- /inside [id] - inspect recent internal activity for a task",
        "- /progress - ask what is happening now",
        "- /append <text> - add instructions to active work",
        "- /new <objective> - start separate work",
        "- /stop or Esc - request stop for the visible active work",
        "- /skills [filter] - browse skill mentions",
        "- /reset [state|all] - clear Super DAN state",
        "While background work runs, keep typing to steer it; use /new when you mean a separate task.",
        "Autocomplete: / for commands, $ for skills, @ for files. Ctrl-V attaches a clipboard screenshot when the terminal exposes the key.",
        "Enter accepts the visible suggestion before sending.",
        "Debug detail: use --raw-events when you need raw event names and tool metadata.",
    ]


def _render_tui_help(args: argparse.Namespace) -> None:
    _print_tui_stream_block("Help", _tui_help_lines(), plain=bool(getattr(args, "plain", False)))


def _format_tui_queue_summary(raw_text: str) -> str:
    lines = [line.strip() for line in str(raw_text or "").splitlines() if line.strip()]
    if not lines:
        return "No queue state is visible."
    reactivity = ""
    active_inboxes: list[str] = []
    dead_inboxes: list[str] = []
    worktrees = ""
    for line in lines:
        if line.startswith("reactivity:"):
            reactivity = line.split(":", maxsplit=1)[1].strip()
            continue
        if line.startswith("Worktrees:"):
            worktrees = line.replace("Worktrees:", "Worktrees:").strip()
            continue
        if not line.startswith("- "):
            continue
        name, _, rest = line[2:].partition(":")
        values: dict[str, int] = {}
        for token in rest.split():
            key, sep, value = token.partition("=")
            if not sep:
                continue
            try:
                values[key] = int(value)
            except ValueError:
                continue
        pending = values.get("pending", 0)
        active = values.get("active", 0)
        dead = values.get("dead", 0)
        if pending or active:
            active_inboxes.append(f"- {name}: {pending} pending, {active} active")
        if dead:
            dead_inboxes.append(f"- {name}: {dead} dead letter(s)")
    summary: list[str] = []
    if active_inboxes:
        summary.append("Internal work is queued:")
        summary.extend(active_inboxes[:8])
    else:
        summary.append("No active internal queue work.")
    if dead_inboxes:
        summary.append("Needs attention:")
        summary.extend(dead_inboxes[:4])
    if reactivity:
        summary.append(f"Reactivity: {reactivity}")
    if worktrees:
        summary.append(worktrees)
    summary.append("Use /tasks for user-facing task state.")
    return "\n".join(summary)


def _first_tui_json_object(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(raw)):
        char = raw[index]
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(raw[start : index + 1])
                except json.JSONDecodeError:
                    return None
                return value if isinstance(value, dict) else None
    return None


def _fallback_tui_plan_draft(objective: str) -> TuiPlanDraft:
    clean = _clip(str(objective or "").strip() or "this work", limit=180)
    return TuiPlanDraft(
        summary=f"Before executing, narrow the plan for: {clean}",
        questions=(
            TuiPlanQuestion(
                question="What should this plan optimize for first?",
                recommended="A small, testable slice that proves the direction before broad implementation.",
                choices=(
                    "Fast prototype with minimal scope",
                    "Polished user experience first",
                    "Technical foundation and architecture first",
                ),
            ),
            TuiPlanQuestion(
                question="What should count as a successful first milestone?",
                recommended="A concrete artifact or demo that can be inspected without reading internal logs.",
                choices=(
                    "Playable or runnable demo",
                    "Detailed implementation plan with file-level tasks",
                    "Research/design document with risks and decisions",
                ),
            ),
            TuiPlanQuestion(
                question="How much freedom should the executor have after the plan is chosen?",
                recommended="Allow implementation inside the chosen scope, but ask again before expanding it.",
                choices=(
                    "Ask before any file changes",
                    "Implement the agreed first slice",
                    "Proceed broadly until blocked",
                ),
            ),
        ),
        next_step="Reply with choices such as `1A, 2B, 3B`, or describe your own direction. No files have been changed.",
    )


def _tui_plan_model_messages(objective: str, transcript_tail: Sequence[TuiTranscriptEntry]) -> list[dict[str, str]]:
    transcript = "\n".join(_format_transcript_line(entry) for entry in transcript_tail[-6:]) or "(none)"
    return [
        {
            "role": "system",
            "content": (
                "You are DAN TUI plan mode. Do not execute work and do not claim files were changed. "
                "Help the user refine direction before execution, like a planning dialogue. "
                "Return exactly one JSON object with keys summary, questions, next_step. "
                "questions must be an array of 2 to 4 objects. Each object has question, recommended, choices, custom_label. "
                "Each choices array should contain 2 to 4 concise mutually exclusive choices. "
                "custom_label must invite the user to define their own answer. "
                "Make the questions specific to the user's project and the latest visible context. "
                "Do not use generic filler. Keep summary and choices concise."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Recent visible transcript:\n{transcript}\n\n"
                f"Plan-mode request:\n{objective}\n\n"
                "Draft the planning questions now. Return JSON only."
            ),
        },
    ]


def _plan_draft_from_payload(payload: Mapping[str, Any] | None, objective: str) -> TuiPlanDraft:
    if not isinstance(payload, MappingABC):
        return _fallback_tui_plan_draft(objective)
    summary = _clip(str(payload.get("summary") or "").strip(), limit=500)
    next_step = _clip(str(payload.get("next_step") or "").strip(), limit=500)
    raw_questions = payload.get("questions")
    questions: list[TuiPlanQuestion] = []
    if isinstance(raw_questions, SequenceABC) and not isinstance(raw_questions, (str, bytes)):
        for item in raw_questions[:4]:
            if not isinstance(item, MappingABC):
                continue
            question = _clip(str(item.get("question") or "").strip(), limit=240)
            if not question:
                continue
            raw_choices = item.get("choices")
            choices: list[str] = []
            if isinstance(raw_choices, SequenceABC) and not isinstance(raw_choices, (str, bytes)):
                for choice in raw_choices[:4]:
                    text = _clip(str(choice or "").strip(), limit=160)
                    if text:
                        choices.append(text)
            questions.append(
                TuiPlanQuestion(
                    question=question,
                    recommended=_clip(str(item.get("recommended") or "").strip(), limit=220),
                    choices=tuple(choices),
                    custom_label=_clip(
                        str(item.get("custom_label") or "").strip()
                        or "Other: describe your own preference",
                        limit=160,
                    ),
                )
            )
    if not summary or not questions:
        return _fallback_tui_plan_draft(objective)
    return TuiPlanDraft(
        summary=summary,
        questions=tuple(questions),
        next_step=next_step or "Reply with choices such as `1A, 2B`, or describe your own direction. No files have been changed.",
    )


def _run_tui_plan_model_draft(
    args: argparse.Namespace,
    objective: str,
    workspace_root: Path,
) -> tuple[TuiPlanDraft, str]:
    try:
        model = super_cli._resolve_live_model(str(getattr(args, "model", "") or ""))
    except ValueError as exc:
        return _fallback_tui_plan_draft(objective), str(exc)
    transcript_tail = _read_tui_transcript(workspace_root, limit=8)

    async def _run() -> str:
        provider = _build_tui_narrator_live_provider(args, model)
        try:
            response = await provider.complete(
                messages=_tui_plan_model_messages(objective, transcript_tail),
                model=model,
                temperature=0.25,
                max_tokens=900,
            )
            return str(getattr(response, "text", "") or "")
        finally:
            await super_cli._close_live_provider(provider)

    try:
        text = _run_with_tui_working_clock(
            args,
            lambda: asyncio.run(_run()),
            label="Planning",
            started_at=getattr(args, "_tui_turn_started_at", None),
        )
    except Exception as exc:
        return _fallback_tui_plan_draft(objective), str(exc)
    return _plan_draft_from_payload(_first_tui_json_object(text), objective), ""


def _format_tui_plan_lines(draft: TuiPlanDraft) -> list[str]:
    lines: list[str] = []
    if draft.summary:
        lines.append(draft.summary)
    lines.append("Questions to settle before execution:")
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for index, question in enumerate(draft.questions, start=1):
        lines.append(f"{index}. {question.question}")
        if question.recommended:
            lines.append(f"   Recommended: {question.recommended}")
        for choice_index, choice in enumerate(question.choices, start=0):
            lines.append(f"   {letters[choice_index]}. {choice}")
        custom_letter = letters[len(question.choices)] if len(question.choices) < len(letters) else "Other"
        lines.append(f"   {custom_letter}. {question.custom_label}")
    if draft.next_step:
        lines.append(draft.next_step)
    return lines


def _tui_plan_reply_model_messages(
    pending: Mapping[str, Any],
    user_reply: str,
    transcript_tail: Sequence[TuiTranscriptEntry],
) -> list[dict[str, str]]:
    draft = pending.get("draft_object")
    objective = str(pending.get("objective") or "").strip()
    question_lines: list[str] = []
    if isinstance(draft, TuiPlanDraft):
        for index, question in enumerate(draft.questions, start=1):
            question_lines.append(f"{index}. {question.question}")
            if question.recommended:
                question_lines.append(f"   Recommended: {question.recommended}")
            for choice_index, choice in enumerate(question.choices, start=0):
                question_lines.append(f"   {chr(ord('A') + choice_index)}. {choice}")
            custom_letter = chr(ord("A") + len(question.choices))
            question_lines.append(f"   {custom_letter}. {question.custom_label}")
    transcript = "\n".join(_format_transcript_line(entry) for entry in transcript_tail[-6:]) or "(none)"
    return [
        {
            "role": "system",
            "content": (
                "You are continuing DAN TUI plan mode. The user is answering previously asked planning questions. "
                "Interpret their selections and preferences, then decide whether this reply only records planning direction "
                "or clearly authorizes starting execution now. Do not use fixed phrases or keyword matching; infer intent "
                "from the whole reply, the original objective, and the visible plan questions. Do not claim files changed. "
                "Return exactly one JSON object with keys answer, decisions, next_step, action, execution_objective. "
                "answer should be a concise natural paragraph. decisions must be an array of concise bullets. "
                "action must be exactly record_only or start_execution. Use start_execution only when the user has "
                "clearly authorized carrying out the planned work now. If action is start_execution, execution_objective "
                "must be the concrete workspace task to send to the executor, grounded in the original objective plus "
                "the selected decisions. If action is record_only, execution_objective must be empty and next_step should "
                "tell the user how to proceed when ready."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Original plan objective:\n{objective}\n\n"
                f"Plan questions:\n{chr(10).join(question_lines) or '(none)'}\n\n"
                f"Recent visible transcript:\n{transcript}\n\n"
                f"User's answer to the plan questions:\n{user_reply}\n\n"
                "Summarize the selected plan direction and choose the action. Return JSON only."
            ),
        },
    ]


def _fallback_tui_plan_reply_decision(
    pending: Mapping[str, Any],
    user_reply: str,
) -> TuiPlanReplyDecision:
    objective = str(pending.get("objective") or "the plan").strip()
    clean_reply = _clip(str(user_reply or "").strip(), limit=500)
    return TuiPlanReplyDecision(
        lines=(
            f"I captured this direction for {objective}: {clean_reply}",
            "No files have changed yet.",
            "Next: ask me to update the relevant plan file, or start a new implementation task once this direction looks right.",
        ),
        action="record_only",
    )


def _plan_reply_decision_from_payload(
    payload: Mapping[str, Any] | None,
    pending: Mapping[str, Any],
    user_reply: str,
) -> TuiPlanReplyDecision:
    if not isinstance(payload, MappingABC):
        return _fallback_tui_plan_reply_decision(pending, user_reply)
    lines: list[str] = []
    answer = _clip(str(payload.get("answer") or "").strip(), limit=900)
    if answer:
        lines.extend(_split_answer_lines(answer, limit=4))
    decisions = payload.get("decisions")
    if isinstance(decisions, SequenceABC) and not isinstance(decisions, (str, bytes)):
        for item in decisions[:8]:
            text = _clip(str(item or "").strip(), limit=220)
            if text:
                lines.append(f"- {text}")
    next_step = _clip(str(payload.get("next_step") or "").strip(), limit=500)
    if next_step:
        lines.append(f"Next: {next_step}")
    action = str(payload.get("action") or "").strip()
    if action != "start_execution":
        action = "record_only"
    execution_objective = _clip(str(payload.get("execution_objective") or "").strip(), limit=1000)
    if action == "start_execution" and not execution_objective:
        action = "record_only"
    if not lines:
        return _fallback_tui_plan_reply_decision(pending, user_reply)
    return TuiPlanReplyDecision(
        lines=tuple(lines),
        action=action,
        execution_objective=execution_objective if action == "start_execution" else "",
    )


def _run_tui_plan_reply_model(
    args: argparse.Namespace,
    pending: Mapping[str, Any],
    user_reply: str,
    workspace_root: Path,
) -> tuple[TuiPlanReplyDecision, str]:
    try:
        model = super_cli._resolve_live_model(str(getattr(args, "model", "") or ""))
    except ValueError as exc:
        return _fallback_tui_plan_reply_decision(pending, user_reply), str(exc)
    transcript_tail = _read_tui_transcript(workspace_root, limit=8)

    async def _run() -> str:
        provider = _build_tui_narrator_live_provider(args, model)
        try:
            response = await provider.complete(
                messages=_tui_plan_reply_model_messages(pending, user_reply, transcript_tail),
                model=model,
                temperature=0.25,
                max_tokens=900,
            )
            return str(getattr(response, "text", "") or "")
        finally:
            await super_cli._close_live_provider(provider)

    try:
        text = _run_with_tui_working_clock(
            args,
            lambda: asyncio.run(_run()),
            label="Planning",
            started_at=getattr(args, "_tui_turn_started_at", None),
        )
    except Exception as exc:
        return _fallback_tui_plan_reply_decision(pending, user_reply), str(exc)
    return _plan_reply_decision_from_payload(_first_tui_json_object(text), pending, user_reply), ""


def _run_tui_plan_reply(
    args: argparse.Namespace,
    user_reply: str,
    pending: Mapping[str, Any],
    parser: argparse.ArgumentParser | None = None,
) -> int:
    workspace_root = normalize_workspace_root(str(args.workspace))
    _append_tui_transcript_entry(
        workspace_root,
        role="user",
        text=user_reply,
        metadata={"plan_reply": True, "plan_objective": str(pending.get("objective") or "")},
    )
    decision, error = _run_tui_plan_reply_model(args, pending, user_reply, workspace_root)
    lines = list(decision.lines)
    if error and bool(getattr(args, "raw_events", False)):
        lines.append(f"Planner fallback: {_clip(error, limit=180)}")
    _print_tui_stream_block("Plan", lines, plain=bool(getattr(args, "plain", False)))
    _append_tui_transcript_entry(
        workspace_root,
        role="assistant_narrator",
        text="\n".join(lines),
        metadata={
            "lane": "plan",
            "status": "plan_reply",
            "action": decision.action,
            "plan_objective": str(pending.get("objective") or ""),
            "execution_objective": decision.execution_objective,
            "model_error": error,
        },
    )
    _clear_tui_pending_plan_state(workspace_root)
    if decision.action == "start_execution":
        execution_objective = decision.execution_objective.strip()
        if _tui_async_agent_enabled(args):
            result = _submit_tui_async_admission(
                args,
                workspace_root=workspace_root,
                text=execution_objective,
                forced_new=True,
                background=True,
            )
            _render_tui_async_admission_compact(args, result)
            _append_tui_async_admission_transcript(
                workspace_root,
                result,
                text=execution_objective,
                forced_new=True,
            )
            return 0 if result.ok else 1
        if parser is None:
            _print_tui_stream_block(
                "Outcome",
                ["Ready to execute this plan direction, but this call has no runner context attached."],
                plain=bool(getattr(args, "plain", False)),
            )
            return 1
        run_args = copy.copy(args)
        run_args.target = execution_objective
        setattr(run_args, "_tui_forced_new", True)
        setattr(run_args, "_tui_transcript_workspace", str(workspace_root))
        setattr(
            run_args,
            "_tui_intent_decision",
            TuiIntentDecision(
                permission="write",
                complexity="complex",
                confidence=1.0,
                rationale="plan reply authorized execution",
            ),
        )
        return _run_tui_turn(run_args, parser, force_live=True)
    _print_tui_stream_block(
        "Outcome",
        ["No files changed.", "This reply only updates the planning direction for the conversation."],
        plain=bool(getattr(args, "plain", False)),
    )
    return 0


def _run_tui_plan_mode(args: argparse.Namespace, objective: str) -> int:
    workspace_root = normalize_workspace_root(str(args.workspace))
    clean_objective = str(objective or "").strip()
    if not clean_objective:
        _print_tui_stream_block(
            "Plan",
            ["Give me the topic after `/plan`, for example `/plan refine the product visual direction`."],
            plain=bool(getattr(args, "plain", False)),
        )
        return 0
    draft, error = _run_tui_plan_model_draft(args, clean_objective, workspace_root)
    _write_tui_pending_plan_state(workspace_root, objective=clean_objective, draft=draft)
    lines = _format_tui_plan_lines(draft)
    if error and bool(getattr(args, "raw_events", False)):
        lines.append(f"Planner fallback: {_clip(error, limit=180)}")
    _print_tui_stream_block("Plan", lines, plain=bool(getattr(args, "plain", False)))
    _print_tui_stream_block(
        "Outcome",
        ["No files changed.", "Reply with your selections or say `/new <objective>` when ready to execute."],
        plain=bool(getattr(args, "plain", False)),
    )
    transcript_workspace = str(getattr(args, "_tui_transcript_workspace", "") or "").strip()
    if transcript_workspace:
        _append_tui_transcript_entry(
            Path(transcript_workspace),
            role="assistant_narrator",
            text="\n".join(lines),
            metadata={"lane": "plan", "status": "planning_questions", "model_error": error},
        )
    return 0


def _tui_async_agent_enabled(args: argparse.Namespace) -> bool:
    if bool(getattr(args, "no_async_agent", False)):
        return False
    env_value = str(os.environ.get("DAN_SUPER_TUI_ASYNC", "")).strip().lower()
    if (
        env_value in {"0", "false", "no", "off"}
        and not bool(getattr(args, "_async_agent_explicit", False))
    ):
        return False
    return True


def _tui_async_server_url(args: argparse.Namespace) -> str:
    explicit = str(getattr(args, "server_url", "") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    return ""


def _tui_async_use_server(args: argparse.Namespace) -> bool:
    return bool(_tui_async_server_url(args))


def _tui_async_thread_id(workspace_root: Path) -> str:
    try:
        session = super_cli._super_session_id(workspace_root)
        if session:
            return f"super-tui-{session}"
    except Exception:
        pass
    digest = hashlib.sha1(str(workspace_root).encode("utf-8")).hexdigest()[:12]
    return f"super-tui-{digest}"


def _tui_local_async_store_dir(workspace_root: Path) -> Path:
    return workspace_root / ".dan-super" / "chat-v2"


def _open_tui_local_async_store(workspace_root: Path):
    from dan.server.chat_v2_store import ChatV2Store

    return ChatV2Store(_tui_local_async_store_dir(workspace_root))


def _tui_async_execution_overrides(args: argparse.Namespace) -> dict[str, Any]:
    profile_policy: dict[str, Any] = {}
    if str(getattr(args, "model", "") or "").strip():
        profile_policy["model"] = str(getattr(args, "model"))
    if str(getattr(args, "base_url", "") or "").strip():
        profile_policy["base_url"] = str(getattr(args, "base_url"))
    if str(getattr(args, "artifact_dir", "") or "").strip():
        profile_policy["artifact_dir"] = str(getattr(args, "artifact_dir"))
    tool_policy: dict[str, Any] = {}
    if int(getattr(args, "max_tool_calls", 0) or 0) > 0:
        tool_policy["max_tool_calls"] = int(getattr(args, "max_tool_calls"))
    return {
        "profile_policy": profile_policy,
        "mutation_policy": {},
        "approval_policy": {},
        "tool_policy": tool_policy,
        "metadata": {
            "surface": "super-tui",
            "selected_skills": list(getattr(args, "_tui_selected_skill_mentions", []) or []),
            "tui_notify_completion": True,
            "tui_plain": bool(getattr(args, "plain", False)),
            "communication_policy": _tui_policy_payload(getattr(args, "_tui_communication_policy", None)),
        },
    }


def _build_tui_async_surface_turn(
    args: argparse.Namespace,
    *,
    workspace_root: Path,
    text: str,
    forced_new: bool = False,
):
    from dan.server.chat_v2 import AttachmentRef, SurfaceTurn

    selected_skills = list(getattr(args, "_tui_selected_skill_mentions", []) or [])
    communication_policy = _tui_policy_payload(getattr(args, "_tui_communication_policy", None))
    message = f"/new {text}".strip() if forced_new and not str(text).lstrip().startswith("/") else str(text)
    attachment_payloads = _tui_image_attachment_payloads_from_text(message, workspace_root)
    history_payload = _tui_transcript_history_payload(workspace_root, current_text=message)
    attachment_refs = [
        AttachmentRef(
            id=str(item.get("id") or ""),
            kind="image",
            source_surface="cli:super-tui",
            mime_type=str(item.get("mime_type") or "") or None,
            local_path=str(item.get("local_path") or "") or None,
            display_name=str(item.get("display_name") or "") or None,
            caption=str(item.get("caption") or ""),
            size_bytes=item.get("size_bytes") if isinstance(item.get("size_bytes"), int) else None,
            checksum=str(item.get("checksum") or "") or None,
            metadata={
                **(item.get("metadata") if isinstance(item.get("metadata"), dict) else {}),
                "path": str(item.get("local_path") or item.get("path") or ""),
            },
        )
        for item in attachment_payloads
    ]
    thread_id = _tui_async_thread_id(workspace_root)
    turn_seed = f"{workspace_root}|{thread_id}|{time.time_ns()}|{message}"
    turn_id = "tui-turn-" + hashlib.sha1(turn_seed.encode("utf-8")).hexdigest()[:16]
    return SurfaceTurn(
        id=turn_id,
        text=message,
        workspace_root=str(workspace_root),
        workspace_id=str(workspace_root),
        surface_type="cli",
        surface_id="super-tui",
        surface="cli:super-tui",
        session_id=thread_id,
        thread_id=thread_id,
        privacy_scope="private",
        attachments=attachment_refs,
        capabilities=[
            "foreground_admission",
            "background_agent_runs",
            "task_board",
            "checkpoint_commands",
        ],
        metadata={
            "workspace_source": "super_tui",
            "surface_topic_key": f"private:cli:user:{thread_id}",
            "selected_skills": selected_skills,
            "forced_new": bool(forced_new),
            "communication_policy": communication_policy,
            "attachments": attachment_payloads,
            "image_attachments": attachment_payloads,
            "surface_context": {
                "workspace_root": str(workspace_root),
                "workspace_source": "super_tui",
                "conversation": {"recent_turns": history_payload},
                "selected_skills": selected_skills,
                "forced_new": bool(forced_new),
                "communication_policy": communication_policy,
                "appended_attachments": attachment_payloads,
            },
            "history": history_payload,
        },
    )


def _tui_local_admission_response_status(admitted: Any) -> str:
    action = str(getattr(admitted.decision, "action", "") or "")
    if action == "start_parallel":
        return "started" if getattr(admitted.decision, "run_id", None) else "accepted"
    if action == "queue_after":
        return "queued"
    if action == "append_to_active":
        return "appended"
    if action == "ask_clarification":
        return "needs_input"
    if action == "reject_or_defer":
        return "blocked"
    return "reported"


def _tui_local_async_admission_response(
    store: Any,
    admitted: Any,
    turn: Any,
    *,
    max_parallel_runs: int,
) -> Mapping[str, Any]:
    from dan.server.chat_v2 import AgentRunEvent
    from dan.server.chat_v2_async_core import build_task_board_snapshot

    board = build_task_board_snapshot(
        store,
        workspace_root=turn.workspace_root,
        thread_id=turn.thread_id,
        max_parallel_runs=max_parallel_runs,
    )
    task_id = str(admitted.decision.task_id or admitted.decision.target_task_id or "")
    run_id = str(admitted.decision.run_id or admitted.decision.target_run_id or "")
    event_payload: dict[str, Any] | None = (
        admitted.event.model_dump(mode="json") if admitted.event is not None else None
    )
    if run_id:
        events = store.load_run_events(run_id)
        if events:
            try:
                event_payload = AgentRunEvent.model_validate(events[-1]).model_dump(mode="json")
            except Exception:
                event_payload = dict(events[-1])
    task = store.get_task_snapshot(task_id) if task_id else None
    run = store.get_run(run_id) if run_id else None
    return {
        "status": _tui_local_admission_response_status(admitted),
        "admission": admitted.decision.model_dump(mode="json"),
        "board": board.model_dump(mode="json"),
        "task": task.model_dump(mode="json") if task is not None else None,
        "run": run.model_dump(mode="json") if run is not None else None,
        "event": event_payload,
        "transport": "local",
        "store": str(_tui_local_async_store_dir(Path(turn.workspace_root))),
    }


def _record_tui_local_async_failure(workspace_root: Path, run_id: str, error: BaseException) -> None:
    try:
        from dan.server.chat_v2 import AgentRunEvent

        store = _open_tui_local_async_store(workspace_root)
        run = store.get_run(run_id)
        event = AgentRunEvent(
            type="failed",
            run_id=run_id,
            task_id=run.task_id if run is not None else None,
            summary=f"Local background Agent run failed: {_clip(error, limit=180)}",
            source_event_type="super_tui.local_background.failed",
            payload={"error_type": type(error).__name__, "error": str(error)},
        )
        store.record_agent_event(event)
        store.update_run_metadata(
            run_id,
            {"local_background_error": str(error), "local_background_error_type": type(error).__name__},
            status="failed",
        )
    except Exception:
        pass


def _tui_background_completion_lines(run: Any | None, events: Sequence[Any]) -> list[str]:
    if run is None:
        return ["Background run finished, but its final state is no longer available. Use `/tasks` to refresh."]
    status = str(getattr(run, "status", "") or "").strip() or "finished"
    run_id = str(getattr(run, "run_id", "") or "").strip()
    task_id = str(getattr(run, "task_id", "") or "").strip()
    visible_id = task_id or run_id
    latest = str(getattr(run, "latest_summary", "") or "").strip()
    if not latest:
        for event in reversed(list(events or [])):
            if isinstance(event, MappingABC):
                latest = str(event.get("summary") or "").strip()
            else:
                latest = str(getattr(event, "summary", "") or "").strip()
            if latest:
                break
    latest_lines = _human_tui_background_summary_lines(latest)
    prefix = "Background run"
    if visible_id:
        prefix = f"Background run `{visible_id}`"
    if status == "completed":
        first = f"{prefix} finished."
    elif status in {"failed", "blocked", "stopped"}:
        first = f"{prefix} needs attention."
    else:
        first = f"{prefix} is {status}."
    if latest_lines and latest_lines[0] not in first:
        first = f"{first} {latest_lines[0]}"
    lines = [first]
    lines.extend(latest_lines[1:3])
    trace_ref = ""
    metadata = getattr(run, "metadata", {}) or {}
    if isinstance(metadata, MappingABC):
        trace_ref = str(metadata.get("event_log_path") or metadata.get("trace_ref") or "").strip()
    if not trace_ref:
        for event in reversed(list(events or [])):
            payload = event if isinstance(event, MappingABC) else getattr(event, "payload", {})
            if isinstance(payload, MappingABC):
                trace_ref = str(payload.get("event_log_path") or payload.get("trace_ref") or "").strip()
            if trace_ref:
                break
    if trace_ref:
        lines.append(f"Trace: {trace_ref}")
    lines.append("Use `/tasks` for the latest board.")
    return lines


def _parse_tui_background_summary_payload(text: str) -> Mapping[str, Any] | None:
    value = str(text or "").strip()
    if not value or not value.startswith("{"):
        return None
    try:
        parsed = json.loads(value)
    except Exception:
        try:
            parsed = ast.literal_eval(value)
        except Exception:
            return None
    return parsed if isinstance(parsed, MappingABC) else None


def _human_tui_background_summary_lines(text: str) -> list[str]:
    value = str(text or "").strip()
    if not value:
        return []
    payload = _parse_tui_background_summary_payload(value)
    if payload is None:
        return [_clip(value, limit=420)]
    lines: list[str] = []
    change_summary = str(payload.get("change_summary") or payload.get("summary") or "").strip()
    files_created = payload.get("files_created")
    created: list[str] = []
    if isinstance(files_created, SequenceABC) and not isinstance(files_created, (str, bytes)):
        created = [str(item).strip() for item in files_created if str(item).strip()]
    files_modified = payload.get("files_modified")
    modified: list[str] = []
    if isinstance(files_modified, SequenceABC) and not isinstance(files_modified, (str, bytes)):
        modified = [str(item).strip() for item in files_modified if str(item).strip()]
    if created:
        files = ", ".join(f"`{_clip(path, limit=80)}`" for path in created[:4])
        if change_summary:
            lines.append(f"Created {files}: {_clip(change_summary, limit=260)}")
        else:
            lines.append(f"Created {files}.")
    elif modified:
        files = ", ".join(f"`{_clip(path, limit=80)}`" for path in modified[:4])
        if change_summary:
            lines.append(f"Updated {files}: {_clip(change_summary, limit=260)}")
        else:
            lines.append(f"Updated {files}.")
    elif change_summary:
        lines.append(_clip(change_summary, limit=320))
    risks = payload.get("risks")
    if isinstance(risks, SequenceABC) and not isinstance(risks, (str, bytes)):
        risk_lines = [str(item).strip() for item in risks if str(item).strip()]
        if risk_lines:
            lines.append("Note: " + _clip(risk_lines[0], limit=240))
    if not lines:
        lines.append(_clip(json.dumps(dict(payload), sort_keys=True), limit=420))
    return lines


def _notify_tui_local_async_background_finished(
    workspace_root: Path,
    run_id: str,
    overrides: Mapping[str, Any],
) -> None:
    metadata = overrides.get("metadata") if isinstance(overrides.get("metadata"), MappingABC) else {}
    if not bool(metadata.get("tui_notify_completion", False)):
        return
    try:
        store = _open_tui_local_async_store(workspace_root)
        run = store.get_run(run_id)
        events = store.load_run_events(run_id)
        lines = _tui_background_completion_lines(run, events)
        _print_tui_stream_block(
            "Answer",
            lines,
            plain=bool(metadata.get("tui_plain", False)),
        )
        _append_tui_transcript_entry(
            workspace_root,
            role="assistant_progress",
            text="\n".join(lines),
            metadata={
                "async_agent": True,
                "background_completion": True,
                "run_id": run_id,
                "status": str(getattr(run, "status", "") or ""),
            },
        )
    except Exception:
        return


async def _execute_tui_local_async_background_run(
    *,
    workspace_root: Path,
    run_id: str,
    backend_name: str,
    overrides: Mapping[str, Any],
    remaining_continuations: int,
) -> None:
    from dan.server.chat_v2_async_core import mark_background_run_started
    from dan.server.chat_v2_backend import run_agent_backend

    store = _open_tui_local_async_store(workspace_root)
    await run_agent_backend(
        store,
        run_id,
        backend_name=backend_name,
        overrides=dict(overrides),
    )
    _notify_tui_local_async_background_finished(workspace_root, run_id, overrides)
    run = store.get_run(run_id)
    next_run_id = (
        str(run.metadata.get("continued_run_id") or "")
        if run is not None and remaining_continuations > 0
        else ""
    )
    if next_run_id:
        next_run = store.get_run(next_run_id)
        if next_run is not None and next_run.status == "queued":
            store.update_run_metadata(
                next_run_id,
                {
                    "execution_mode": "background",
                    "requested_backend": backend_name,
                    "auto_execute_continuations": True,
                    "promoted_from_run_id": run_id,
                },
                status="running",
            )
            await _execute_tui_local_async_background_run(
                workspace_root=workspace_root,
                run_id=next_run_id,
                backend_name=backend_name,
                overrides=overrides,
                remaining_continuations=remaining_continuations - 1,
            )
    if run is None:
        return
    for ready in store.promote_waiting_dependency_runs(
        dependency_task_id=run.task_id,
        limit=max(1, remaining_continuations),
    ):
        mark_background_run_started(
            store,
            ready.run_id,
            backend=backend_name,
            reason="dependency completed",
        )
        _start_tui_local_async_background_run(
            workspace_root=workspace_root,
            run_id=ready.run_id,
            backend_name=backend_name,
            overrides=overrides,
            remaining_continuations=remaining_continuations,
        )


def _start_tui_local_async_background_run(
    *,
    workspace_root: Path,
    run_id: str,
    backend_name: str,
    overrides: Mapping[str, Any],
    remaining_continuations: int = 8,
) -> None:
    def _thread_main() -> None:
        try:
            asyncio.run(
                _execute_tui_local_async_background_run(
                    workspace_root=workspace_root,
                    run_id=run_id,
                    backend_name=backend_name,
                    overrides=overrides,
                    remaining_continuations=remaining_continuations,
                )
            )
        except Exception as exc:
            _record_tui_local_async_failure(workspace_root, run_id, exc)

    thread = threading.Thread(
        target=_thread_main,
        name=f"super-tui-agent-{run_id[:12]}",
        daemon=True,
    )
    thread.start()
    _TUI_LOCAL_ASYNC_THREADS.append(thread)
    del _TUI_LOCAL_ASYNC_THREADS[:-20]


def _submit_tui_local_async_admission(
    args: argparse.Namespace,
    *,
    workspace_root: Path,
    text: str,
    forced_new: bool = False,
    background: bool = True,
) -> TuiAsyncAdmissionResult:
    from dan.server.chat_v2_async_core import admit_foreground_turn, mark_background_run_started

    max_parallel_runs = max(1, int(getattr(args, "async_agent_max_parallel", 4) or 4))
    store = _open_tui_local_async_store(workspace_root)
    turn = _build_tui_async_surface_turn(
        args,
        workspace_root=workspace_root,
        text=text,
        forced_new=forced_new,
    )
    try:
        admitted = admit_foreground_turn(
            store,
            turn,
            max_parallel_runs=max_parallel_runs,
        )
        backend_name = str(getattr(args, "async_agent_backend", "") or "super_dan")
        if (
            background
            and admitted.decision.action == "start_parallel"
            and admitted.decision.run_id
        ):
            mark_background_run_started(
                store,
                admitted.decision.run_id,
                backend=backend_name,
                reason=admitted.decision.reason,
            )
            overrides = _tui_async_execution_overrides(args)
            store.update_run_metadata(
                admitted.decision.run_id,
                {
                    "auto_execute_continuations": True,
                    "max_promoted_continuations": 8,
                    "local_tui_background": True,
                    "communication_policy": _tui_policy_payload(getattr(args, "_tui_communication_policy", None)),
                },
            )
            _start_tui_local_async_background_run(
                workspace_root=workspace_root,
                run_id=admitted.decision.run_id,
                backend_name=backend_name,
                overrides=overrides,
                remaining_continuations=8,
            )
        response = _tui_local_async_admission_response(
            store,
            admitted,
            turn,
            max_parallel_runs=max_parallel_runs,
        )
    except Exception as exc:
        return TuiAsyncAdmissionResult(
            ok=False,
            message=f"Local async admission failed: {_clip(exc, limit=180)}.",
            error=str(exc),
        )
    events = _tui_async_board_events_from_response(response)
    message = _format_tui_async_admission_message(
        response,
        events=events,
        workspace_root=workspace_root,
    )
    return TuiAsyncAdmissionResult(ok=True, message=message, response=response, events=events)


def _tui_async_admission_payload(
    args: argparse.Namespace,
    *,
    workspace_root: Path,
    text: str,
    forced_new: bool = False,
    background: bool = True,
) -> dict[str, Any]:
    selected_skills = list(getattr(args, "_tui_selected_skill_mentions", []) or [])
    communication_policy = _tui_policy_payload(getattr(args, "_tui_communication_policy", None))
    message = f"/new {text}".strip() if forced_new and not str(text).lstrip().startswith("/") else str(text)
    profile_policy: dict[str, Any] = {}
    if str(getattr(args, "model", "") or "").strip():
        profile_policy["model"] = str(getattr(args, "model"))
    if str(getattr(args, "base_url", "") or "").strip():
        profile_policy["base_url"] = str(getattr(args, "base_url"))
    if str(getattr(args, "artifact_dir", "") or "").strip():
        profile_policy["artifact_dir"] = str(getattr(args, "artifact_dir"))
    tool_policy: dict[str, Any] = {}
    if int(getattr(args, "max_tool_calls", 0) or 0) > 0:
        tool_policy["max_tool_calls"] = int(getattr(args, "max_tool_calls"))
    thread_id = _tui_async_thread_id(workspace_root)
    attachment_payloads = _tui_image_attachment_payloads_from_text(message, workspace_root)
    history_payload = _tui_transcript_history_payload(workspace_root, current_text=message)
    return {
        "chat_request": {
            "workflow_id": "_scratch",
            "message": message,
            "mode": "agent",
            "surface_type": "cli",
            "surface_id": "super-tui",
            "surface": "cli:super-tui",
            "session_id": thread_id,
            "thread_id": thread_id,
            "history": history_payload,
            "surface_context": {
                "workspace_root": str(workspace_root),
                "workspace_source": "super_tui",
                "conversation": {"recent_turns": history_payload},
                "selected_skills": selected_skills,
                "forced_new": bool(forced_new),
                "communication_policy": communication_policy,
                "appended_attachments": attachment_payloads,
            },
        },
        "background": bool(background),
        "execute": {
            "backend": str(getattr(args, "async_agent_backend", "") or "super_dan"),
            "background": bool(background),
            "auto_execute_continuations": True,
            "max_promoted_continuations": 8,
            "profile_policy": profile_policy,
            "tool_policy": tool_policy,
            "metadata": {
                "surface": "super-tui",
                "selected_skills": selected_skills,
                "communication_policy": communication_policy,
                "attachments": attachment_payloads,
                "image_attachments": attachment_payloads,
            },
        },
        "max_parallel_runs": max(1, int(getattr(args, "async_agent_max_parallel", 4) or 4)),
    }


def _post_tui_async_admission(
    *,
    server_url: str,
    payload: Mapping[str, Any],
    timeout: float,
) -> Mapping[str, Any]:
    import httpx

    response = httpx.post(
        f"{server_url.rstrip('/')}/api/v2/agent-runs/admit",
        json=dict(payload),
        timeout=timeout,
    )
    response.raise_for_status()
    value = response.json()
    return value if isinstance(value, MappingABC) else {}


def _tui_async_board_events_from_response(response: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    board = response.get("board") if isinstance(response.get("board"), MappingABC) else {}
    decision = response.get("admission") if isinstance(response.get("admission"), MappingABC) else {}
    rows: list[Mapping[str, Any]] = []
    for bucket, status_fallback in (
        ("active_runs", "running"),
        ("queued_runs", "queued"),
        ("completed_runs", "completed"),
    ):
        values = board.get(bucket) if isinstance(board, MappingABC) else None
        if not isinstance(values, SequenceABC) or isinstance(values, (str, bytes)):
            continue
        for value in values[:20]:
            if not isinstance(value, MappingABC):
                continue
            raw_trace_refs = value.get("trace_refs")
            trace_refs = (
                raw_trace_refs
                if isinstance(raw_trace_refs, SequenceABC) and not isinstance(raw_trace_refs, (str, bytes))
                else []
            )
            trace_ref = str(value.get("event_log_path") or (trace_refs[0] if trace_refs else "") or "")
            rows.append(
                {
                    "event": "tui.board.admission",
                    "task_id": str(value.get("task_id") or ""),
                    "run_id": str(value.get("run_id") or ""),
                    "objective": str(value.get("objective") or ""),
                    "status": str(value.get("status") or status_fallback),
                    "phase": str(value.get("phase") or value.get("status") or status_fallback),
                    "action": str(value.get("latest_summary") or value.get("admission_action") or ""),
                    "reason": str(value.get("admission_reason") or ""),
                    "trace_ref": trace_ref,
                    "changed_paths": list(value.get("owned_paths") or []),
                    "decision": str(value.get("admission_action") or decision.get("action") or ""),
                }
            )
    if rows:
        return tuple(rows)
    action = str(decision.get("action") or response.get("status") or "").strip()
    if action == "chat_or_status":
        return tuple()
    if action:
        rows.append(
            {
                "event": "tui.board.admission",
                "task_id": str(decision.get("task_id") or decision.get("target_task_id") or "admission"),
                "run_id": str(decision.get("run_id") or decision.get("target_run_id") or ""),
                "objective": "",
                "status": "needs-clarification" if action == "ask_clarification" else action,
                "phase": action,
                "action": str(decision.get("status_text") or decision.get("question") or decision.get("reason") or ""),
                "reason": str(decision.get("reason") or ""),
                "decision": action,
            }
        )
    return tuple(rows)


def _format_tui_async_admission_message(
    response: Mapping[str, Any],
    *,
    events: Sequence[Mapping[str, Any]],
    workspace_root: Path,
) -> str:
    decision = response.get("admission") if isinstance(response.get("admission"), MappingABC) else {}
    action = str(decision.get("action") or response.get("status") or "").strip()
    run_id = str(decision.get("run_id") or decision.get("target_run_id") or "").strip()
    task_id = str(decision.get("task_id") or decision.get("target_task_id") or "").strip()
    reason = str(decision.get("reason") or "").strip()
    question = str(decision.get("question") or "").strip()
    status_text = str(decision.get("status_text") or "").strip()
    if action == "start_parallel":
        summary = f"Admitted {task_id or run_id or 'task'}; background run started."
    elif action == "queue_after":
        summary = f"Queued {task_id or run_id or 'task'} behind active work."
    elif action == "append_to_active":
        summary = f"Appended follow-up to {task_id or run_id or 'the active run'}; checkpoint pending."
    elif action == "chat_or_status":
        summary = status_text or "Board status returned."
    elif action == "ask_clarification":
        summary = question or "Clarification needed before admitting the turn."
    elif action:
        summary = action.replace("_", " ").strip().capitalize()
    else:
        summary = "Async admission returned."
    if reason and reason not in summary:
        summary = f"{summary} {reason}"
    _ = (events, workspace_root)
    return summary


def _tui_async_admission_objective(
    response: Mapping[str, Any],
    *,
    task_id: str = "",
    run_id: str = "",
) -> str:
    def _matches(row: Mapping[str, Any]) -> bool:
        row_task = str(row.get("task_id") or "")
        row_run = str(row.get("run_id") or "")
        return bool((task_id and row_task == task_id) or (run_id and row_run == run_id) or not (task_id or run_id))

    candidates: list[Mapping[str, Any]] = []
    for key in ("task", "run"):
        value = response.get(key)
        if isinstance(value, MappingABC):
            candidates.append(value)
    board = response.get("board") if isinstance(response.get("board"), MappingABC) else {}
    for bucket in ("active_runs", "queued_runs", "completed_runs"):
        rows = board.get(bucket) if isinstance(board, MappingABC) else None
        if not isinstance(rows, SequenceABC) or isinstance(rows, (str, bytes)):
            continue
        for row in rows:
            if isinstance(row, MappingABC):
                candidates.append(row)
    for row in candidates:
        if not _matches(row):
            continue
        objective = _clip(str(row.get("objective") or row.get("title") or "").strip(), limit=260)
        if objective:
            return objective
    return ""


def _tui_async_admission_matching_rows(
    response: Mapping[str, Any],
    *,
    task_id: str = "",
    run_id: str = "",
) -> list[Mapping[str, Any]]:
    def _matches(row: Mapping[str, Any]) -> bool:
        row_task = str(row.get("task_id") or "")
        row_run = str(row.get("run_id") or "")
        return bool((task_id and row_task == task_id) or (run_id and row_run == run_id) or not (task_id or run_id))

    rows: list[Mapping[str, Any]] = []
    for key in ("task", "run"):
        value = response.get(key)
        if isinstance(value, MappingABC) and _matches(value):
            rows.append(value)
    board = response.get("board") if isinstance(response.get("board"), MappingABC) else {}
    for bucket in ("active_runs", "queued_runs", "completed_runs"):
        values = board.get(bucket) if isinstance(board, MappingABC) else None
        if not isinstance(values, SequenceABC) or isinstance(values, (str, bytes)):
            continue
        for value in values:
            if isinstance(value, MappingABC) and _matches(value):
                rows.append(value)
    return rows


def _append_tui_async_detail_lines(
    lines: list[str],
    response: Mapping[str, Any],
    decision: Mapping[str, Any],
    *,
    task_id: str = "",
    run_id: str = "",
) -> None:
    objective = _tui_async_admission_objective(response, task_id=task_id, run_id=run_id)
    rows = _tui_async_admission_matching_rows(response, task_id=task_id, run_id=run_id)
    detail_candidates: list[str] = []
    for value in (
        decision.get("status_text"),
        decision.get("reason"),
        response.get("message"),
    ):
        _append_unique(detail_candidates, value, limit=4)
    scoped_paths: list[str] = []
    for row in rows:
        for key in ("latest_summary", "admission_reason", "reason", "phase"):
            _append_unique(detail_candidates, row.get(key), limit=4)
        raw_paths = row.get("owned_paths") or row.get("changed_paths") or row.get("artifacts") or []
        if isinstance(raw_paths, SequenceABC) and not isinstance(raw_paths, (str, bytes)):
            for path in raw_paths:
                _append_unique(scoped_paths, path, limit=4)
    useful_details: list[str] = []
    for detail in detail_candidates:
        clean = str(detail or "").strip()
        lowered = clean.lower()
        if (
            not clean
            or clean == objective
            or lowered in {"background_running", "background agent run started."}
        ):
            continue
        if "no active or queued executor work" in lowered:
            clean = "No other active or queued executor work was visible, so this can start right away."
        elif "do not overlap active or queued runs" in lowered:
            clean = "The requested files do not overlap visible active work, so this can run independently."
        _append_unique(useful_details, clean, limit=2)
    for detail in useful_details[:2]:
        lines.append(_clip(detail, limit=260))
    if scoped_paths:
        paths = ", ".join(f"`{_clip(path, limit=80)}`" for path in scoped_paths[:4])
        lines.append(f"The visible scope is {paths}.")


def _tui_async_admission_compact_lines(result: TuiAsyncAdmissionResult) -> list[str]:
    if not result.ok:
        return [_clip(result.message, limit=500)]
    response = result.response if isinstance(result.response, MappingABC) else {}
    decision = response.get("admission") if isinstance(response.get("admission"), MappingABC) else {}
    action = str(decision.get("action") or response.get("status") or "").strip()
    task_id = str(decision.get("task_id") or decision.get("target_task_id") or "").strip()
    run_id = str(decision.get("run_id") or decision.get("target_run_id") or "").strip()
    visible_id = task_id or run_id
    if action == "start_parallel":
        objective = _tui_async_admission_objective(response, task_id=task_id, run_id=run_id)
        if visible_id and objective:
            lines = [f"Started background task `{visible_id}` to work on: {objective}"]
        elif visible_id:
            lines = [f"Started background task `{visible_id}`."]
        elif objective:
            lines = [f"Started a background task to work on: {objective}"]
        else:
            lines = ["Started a background task."]
        _append_tui_async_detail_lines(lines, response, decision, task_id=task_id, run_id=run_id)
        lines.append("The composer stays open. Keep typing for a new routed turn; use `/append ...` only if you want to steer this task, or `/stop` to request a stop.")
        return lines
    if action == "queue_after":
        objective = _tui_async_admission_objective(response, task_id=task_id, run_id=run_id)
        if visible_id and objective:
            lines = [f"Queued task `{visible_id}` behind current work for: {objective}"]
        elif visible_id:
            lines = [f"Queued task `{visible_id}` behind current work."]
        elif objective:
            lines = [f"Queued this behind current work: {objective}"]
        else:
            lines = ["Queued this behind current work."]
        _append_tui_async_detail_lines(lines, response, decision, task_id=task_id, run_id=run_id)
        lines.append("Use `/tasks` when you want to see whether it has started.")
        return lines
    if action == "append_to_active":
        if visible_id:
            return [f"Noted. I queued that for `{visible_id}`; the executor will pick it up at the next safe checkpoint."]
        return ["Noted. I queued that for the active run; the executor will pick it up at the next safe checkpoint."]
    if action == "chat_or_status":
        status_text = str(decision.get("status_text") or "").strip()
        if status_text:
            return [status_text]
    if action == "ask_clarification":
        question = str(decision.get("question") or "").strip()
        if question:
            return [question]
    first_line = result.message.splitlines()[0].strip() if result.message else ""
    return [first_line or "The turn was accepted."]


def _render_tui_async_admission_compact(args: argparse.Namespace, result: TuiAsyncAdmissionResult) -> None:
    _print_tui_stream_block(
        "Answer",
        _tui_async_admission_compact_lines(result),
        plain=bool(getattr(args, "plain", False)),
    )


def _tui_visible_board_target(
    workspace_root: Path,
    *,
    allow_queued: bool = False,
) -> TuiBoardRow | None:
    state = _load_tui_board_source_state(workspace_root)
    focused = state.find_board_row("")
    if focused is not None:
        bucket = _tui_board_bucket(focused)
        if bucket == "active" or (allow_queued and bucket == "queued"):
            return focused
    active, queued, _recent = state.board_rows_by_bucket()
    if len(active) == 1:
        return active[0]
    if allow_queued and not active and len(queued) == 1:
        return queued[0]
    return None


def _tui_effective_async_text_for_write_turn(
    args: argparse.Namespace,
    workspace_root: Path,
    text: str,
) -> str:
    del args, workspace_root
    return str(text or "").strip()


def _tui_forces_new_async_work_for_prose(args: argparse.Namespace, text: str) -> bool:
    if bool(getattr(args, "_tui_forced_new", False)):
        return True
    if not bool(getattr(args, "_tui_async_interactive", False)):
        return False
    stripped = str(text or "").strip()
    if not stripped or stripped.startswith("/"):
        return False
    return True


def _tui_has_visible_active_or_queued_work(workspace_root: Path) -> bool:
    try:
        state = _load_tui_board_source_state(workspace_root)
        active, queued, _recent = state.board_rows_by_bucket()
    except Exception:
        return False
    return bool(active or queued)


def _tui_async_background_requested(args: argparse.Namespace) -> bool:
    return bool(
        getattr(args, "_async_agent_explicit", False)
        or getattr(args, "_server_explicit", False)
        or _tui_async_use_server(args)
    )


def _tui_should_use_async_background_for_turn(
    args: argparse.Namespace,
    *,
    async_agent_enabled: bool,
    workspace_root: Path,
) -> bool:
    del async_agent_enabled, workspace_root
    if bool(getattr(args, "plan_only", False)):
        return False
    try:
        stdin_is_tty = bool(getattr(args, "_stdin_is_tty", False)) or bool(sys.stdin.isatty())
    except Exception:
        stdin_is_tty = bool(getattr(args, "_stdin_is_tty", False))
    if not stdin_is_tty:
        return False
    return True


def _tui_should_use_async_background_for_write(args: argparse.Namespace, workspace_root: Path) -> bool:
    if not _tui_async_agent_enabled(args):
        return False
    if _tui_async_background_requested(args):
        return True
    return _tui_has_visible_active_or_queued_work(workspace_root)


def _request_tui_stop_from_shortcut(
    args: argparse.Namespace,
    workspace_root: Path,
    *,
    source: str,
) -> bool:
    if not _tui_async_agent_enabled(args):
        return False
    state = _load_tui_board_source_state(workspace_root)
    active, queued, _recent = state.board_rows_by_bucket()
    if not active and not queued:
        return False
    target = _tui_visible_board_target(workspace_root, allow_queued=True)
    command_text = "/stop"
    if target is not None:
        command_text = f"/stop {target.display_id}"
    _append_tui_transcript_entry(
        workspace_root,
        role="user",
        text=command_text,
        metadata={"shortcut": source, "control": "stop"},
    )
    result = _submit_tui_async_admission(
        args,
        workspace_root=workspace_root,
        text=command_text,
        background=False,
    )
    _print_tui_stream_block(
        "Command",
        _tui_async_admission_compact_lines(result),
        plain=bool(getattr(args, "plain", False)),
    )
    _append_tui_async_admission_transcript(
        workspace_root,
        result,
        text=command_text,
    )
    return True


def _submit_tui_async_admission(
    args: argparse.Namespace,
    *,
    workspace_root: Path,
    text: str,
    forced_new: bool = False,
    background: bool = True,
) -> TuiAsyncAdmissionResult:
    if not _tui_async_use_server(args):
        return _submit_tui_local_async_admission(
            args,
            workspace_root=workspace_root,
            text=text,
            forced_new=forced_new,
            background=background,
        )
    server_url = _tui_async_server_url(args)
    payload = _tui_async_admission_payload(
        args,
        workspace_root=workspace_root,
        text=text,
        forced_new=forced_new,
        background=background,
    )
    try:
        response = _post_tui_async_admission(
            server_url=server_url,
            payload=payload,
            timeout=float(getattr(args, "async_agent_timeout", 20.0) or 20.0),
        )
    except Exception as exc:
        message = (
            f"Async Agent admission failed at {server_url}: {_clip(exc, limit=180)}. "
            "The default local TUI path does not need a server; omit --server "
            "or pass --no-async-agent for the older direct blocking path."
        )
        return TuiAsyncAdmissionResult(ok=False, message=message, error=str(exc))
    events = _tui_async_board_events_from_response(response)
    message = _format_tui_async_admission_message(
        response,
        events=events,
        workspace_root=workspace_root,
    )
    return TuiAsyncAdmissionResult(ok=True, message=message, response=response, events=events)


def _append_tui_async_admission_transcript(
    workspace_root: Path,
    result: TuiAsyncAdmissionResult,
    *,
    text: str,
    forced_new: bool = False,
    communication_policy: AgentCommunicationPolicy | Mapping[str, Any] | None = None,
) -> None:
    metadata: dict[str, Any] = {
        "async_agent": True,
        "forced_new": bool(forced_new),
        "status": str(result.response.get("status") or "") if isinstance(result.response, MappingABC) else "",
    }
    if communication_policy is not None:
        metadata["communication_policy"] = _tui_policy_payload(communication_policy)
    if result.events:
        metadata["tui_board_events"] = [dict(event) for event in result.events]
        metadata["tui_board_event"] = dict(result.events[-1])
    admission = result.response.get("admission") if isinstance(result.response, MappingABC) else None
    if isinstance(admission, MappingABC):
        metadata["admission"] = dict(admission)
    _append_tui_transcript_entry(
        workspace_root,
        role="assistant_progress",
        text=result.message,
        metadata=metadata,
    )


def _tui_board_command_target_and_text(
    state: SuperTuiState,
    command: TuiRunCommand,
) -> tuple[TuiBoardRow | None, str, str]:
    payload = str(command.payload or "").strip()
    target_text = ""
    body_text = payload
    if command.command in {"cancel", "pause", "resume", "focus"}:
        target_text = payload
        body_text = ""
    elif command.command == "append" and payload:
        first, sep, rest = payload.partition(" ")
        if sep and state.find_board_row(first) is not None:
            target_text = first
            body_text = rest.strip()
    row = state.find_board_row(target_text)
    if row is None and not target_text:
        active, _queued, _recent = state.board_rows_by_bucket()
        if len(active) == 1:
            row = active[0]
    return row, target_text, body_text


def _apply_tui_board_command(
    state: SuperTuiState,
    command: TuiRunCommand,
    *,
    width: int | None = None,
) -> TuiBoardCommandResult:
    name = command.command.strip().lower()
    if name == "focus":
        target = command.payload.strip()
        if not target:
            return TuiBoardCommandResult("Use /focus <task|run> with an id shown by /tasks.")
        row = state.find_board_row(target)
        if row is None:
            return TuiBoardCommandResult(_format_tui_board_status(state, target=target, width=width))
        state.focused_board_id = row.task_id
        text = (
            f"Focused {row.display_id}. Future /append or /stop commands target this row by default.\n"
            f"{_format_tui_board_status(state, target=row.display_id, width=width)}"
        )
        return TuiBoardCommandResult(text, focused_board_id=row.task_id)

    row, target_text, body_text = _tui_board_command_target_and_text(state, command)
    if row is None:
        if target_text:
            return TuiBoardCommandResult(_format_tui_board_status(state, target=target_text, width=width))
        return TuiBoardCommandResult(
            "No focused or running task is visible. Use /tasks, then /focus <task|run> before steering a task."
        )
    if name in {"append", "cancel"} and _tui_board_bucket(row) == "recent":
        status = _normalize_tui_board_status(row.status)
        return TuiBoardCommandResult(
            f"{row.display_id} is already {status}; no active checkpoint is visible for this command."
        )

    if name == "append":
        if not body_text:
            return TuiBoardCommandResult("Use /append <text>, or /append <task|run> <text> when several tasks are visible.")
        message = f"{row.display_id}: append requested; checkpoint pending."
        event = {
            "event": "tui.board.intervention",
            "task_id": row.task_id,
            "run_id": row.run_id,
            "objective": row.objective,
            "status": "checkpoint-pending",
            "phase": row.phase or "intervention",
            "action": "append requested; waiting for checkpoint",
            "intervention": f"append: {_clip(body_text, limit=160)}",
            "reason": (
                "The TUI recorded the follow-up locally. Injection into an active executor "
                "requires an async run at a safe checkpoint."
            ),
            "message": message,
            "focused": True,
        }
        state.observe(event)
        text = f"{message}\n{_format_tui_board_status(state, target=row.display_id, width=width)}"
        return TuiBoardCommandResult(text, event=event, focused_board_id=row.task_id)

    if name == "cancel":
        message = f"{row.display_id}: cancel requested; checkpoint pending."
        event = {
            "event": "tui.board.intervention",
            "task_id": row.task_id,
            "run_id": row.run_id,
            "objective": row.objective,
            "status": "cancel-pending",
            "phase": row.phase or "intervention",
            "action": "cancel requested; waiting for checkpoint",
            "intervention": "cancel",
            "reason": (
                "The stop intent is visible on the board. Direct local execution can only be "
                "interrupted from this shell; async runs can honor it at checkpoints."
            ),
            "message": message,
            "focused": True,
        }
        state.observe(event)
        text = f"{message}\n{_format_tui_board_status(state, target=row.display_id, width=width)}"
        return TuiBoardCommandResult(text, event=event, focused_board_id=row.task_id)

    return TuiBoardCommandResult(command.message)


def _handle_tui_board_command(
    workspace_root: Path,
    command: TuiRunCommand,
    *,
    width: int | None = None,
) -> TuiBoardCommandResult:
    state = _load_tui_board_source_state(workspace_root)
    return _apply_tui_board_command(state, command, width=width)


def _tui_board_command_transcript_metadata(
    command: TuiRunCommand,
    result: TuiBoardCommandResult,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "command": command.command,
        "payload": command.payload,
    }
    if result.event:
        metadata["tui_board_event"] = dict(result.event)
    if result.focused_board_id:
        metadata["focused_board_id"] = result.focused_board_id
    return metadata


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
            "Say only what changed since the last visible update, or if nothing changed, say what the executor is still waiting on in plain words. "
            "Do not mention validation scores, run ids, board ids, or event logs in a heartbeat."
        )
    else:
        style_instruction = (
            "Use one concise paragraph; add bullets only when they materially improve readability. "
            "Avoid repeating stable facts already present in recent narrator messages unless they changed. "
            "For next-step or progress questions, lead with the practical answer: what is done, what is stuck or unfinished, and what should happen next. "
            "Do not open with validation status or validation scores; scores are internal diagnostics. "
            "Mention validation scores, run ids, board ids, or event logs only when the user asks for diagnostics. "
            "Avoid dramatic idioms; keep the tone plain and operational."
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
                "The user's goal is getting work done; internal validation scores are supporting evidence, not the main answer. "
                "If work is stuck, state the concrete missing artifact, command, file, or decision before mentioning any checker outcome. "
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


def _tui_live_route_failure_message(args: argparse.Namespace, model: str, exc: BaseException) -> str:
    detail = _clip(str(exc) or type(exc).__name__, limit=180)
    if detail.endswith(".") and not detail.endswith("..."):
        detail = detail[:-1].rstrip()
    model_label = _clip(model, limit=80) or "unresolved"
    source_notes: list[str] = []
    if str(getattr(args, "api_key", "") or "").strip():
        source_notes.append("--api-key")
    elif any(str(os.environ.get(name) or "").strip() for name in ("DAN_LLM_API_KEY", "OPENAI_API_KEY")):
        source_notes.append("DAN_LLM_API_KEY/OPENAI_API_KEY")
    else:
        source_notes.append("no generic live API key detected")
    if str(getattr(args, "base_url", "") or "").strip():
        source_notes.append("--base-url")
    elif any(str(os.environ.get(name) or "").strip() for name in ("DAN_LLM_BASE_URL", "DAN_BASE_URL")):
        source_notes.append("DAN_LLM_BASE_URL/DAN_BASE_URL")
    config_note = ", ".join(source_notes)
    return (
        f"Super TUI could not reach the live route model `{model_label}`: {detail}. "
        "This is a provider/configuration problem, not an ambiguous request. "
        f"Configuration seen: {config_note}. "
        "Check the model name, API key, base URL, and network access, then retry. "
        "Until the provider is healthy, use explicit local slash commands such as /progress, /status, /tasks, /inside, or --event-log."
    )


def _tui_explicit_live_router_failure_decision(model: str, exc: BaseException) -> TuiIntentDecision:
    return TuiIntentDecision(
        permission="write",
        complexity="complex",
        confidence=0.5,
        rationale=(
            "explicit --live requested; model router failed before selecting a lane, "
            f"so Super TUI is falling back to the normal executor path for model {_clip(model, limit=80)} "
            f"after {_clip(type(exc).__name__, limit=80)}"
        ),
    )


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
        core_decision = _run_with_tui_working_clock(
            args,
            lambda: asyncio.run(_run()),
            label="Thinking",
            started_at=getattr(args, "_tui_turn_started_at", None),
        )
    except Exception as exc:
        if bool(getattr(args, "_live_explicit", False)):
            return _tui_explicit_live_router_failure_decision(model, exc), False
        return (
            TuiIntentDecision(
                permission="",
                complexity="",
                confidence=0.0,
                rationale=f"model router provider failure: {_clip(type(exc).__name__, limit=80)}",
                clarification=_tui_live_route_failure_message(args, model, exc),
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
    policy = state.communication_policy.to_payload()
    answer_limit = _tui_answer_line_limit(state.communication_policy)
    style_note = "Use one short paragraph plus bullets only if they improve readability."
    if state.communication_policy.answer_budget == ANSWER_BUDGET_BRIEF:
        style_note = "Use at most five short lines. Prefer one direct paragraph."
    elif state.communication_policy.answer_budget == ANSWER_BUDGET_DETAILED:
        style_note = "Use findings, evidence, and next steps when useful; stay grounded in the summary."
    if state.communication_policy.interaction_style == INTERACTION_REVIEW:
        style_note += " For review-style answers, lead with concrete findings or risks before summary."
    elif state.communication_policy.interaction_style == INTERACTION_ANSWER_ONLY:
        style_note += " Answer directly without an implementation diary."
    elif state.communication_policy.interaction_style == INTERACTION_AUTONOMOUS_PROGRESS:
        style_note += " Keep autonomous progress compact; report the outcome and next controllable step."
    return [
        {
            "role": "system",
            "content": (
                "You are the final answer writer for Super DAN TUI. "
                "Use only the sanitized run summary below. Do not call tools, do not claim to inspect files now, and do not invent work. "
                "Write naturally to the user. Avoid formulaic wording like 'Completed <original request>'. "
                "Lead with the concrete outcome and the useful next step. Mention changed paths when they matter. "
                "Do not lead with validation status or validation scores. Mention scores only if the user asks for diagnostics; otherwise translate validation into plain language or omit it. "
                "If the work is incomplete, say what concrete artifact or action is missing before any internal checker details. "
                f"Respect the communication policy mechanically; do not exceed {answer_limit} visible lines. "
                f"{style_note}"
            ),
        },
        {
            "role": "user",
            "content": (
                f"User request: {state.objective or '(unknown)'}\n"
                f"Communication policy: {json.dumps(policy, sort_keys=True)}\n"
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
                max_tokens=_tui_answer_max_tokens(state.communication_policy),
            )
            return str(getattr(response, "text", "") or "").strip()
        finally:
            await super_cli._close_live_provider(provider)

    try:
        text = _run_with_tui_working_clock(args, lambda: asyncio.run(_run()), label="Preparing answer")
    except Exception:
        return False
    lines = _split_answer_lines(text, limit=_tui_answer_line_limit(state.communication_policy))
    if not _answer_lines_have_useful_prose(lines):
        return False
    state.answer_lines.clear()
    for line in lines:
        state._record_answer(line)
    state.attach_answer_to_board_row(lines=lines)
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
    if bool(getattr(args, "_tui_background_dispatch", False)):
        thread.join()
        return
    last_footer = ""
    clock_line_active = False
    force_newline_clock = bool(getattr(args, "_tui_background_dispatch", False))
    status_note_printed = False
    while thread.is_alive():
        footer = state.elapsed_footer()
        if footer and footer != last_footer:
            clock_line_active = (
                _write_tui_clock_line(
                    footer,
                    label="Preparing answer",
                    force_newline=force_newline_clock,
                )
                or clock_line_active
            )
            last_footer = footer
        if not status_note_printed and state.started_at_monotonic and time.monotonic() - state.started_at_monotonic >= 8.0:
            _clear_tui_clock_line(clock_line_active, force_newline=force_newline_clock)
            clock_line_active = False
            _print_tui_stream_line(
                "Preparing an answer from the latest visible run state.",
                plain=bool(getattr(args, "plain", False)),
            )
            status_note_printed = True
        time.sleep(0.25)
    if clock_line_active:
        _clear_tui_clock_line(clock_line_active, force_newline=force_newline_clock)


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
    state.started_at_monotonic = float(getattr(args, "_tui_turn_started_at", 0.0) or time.monotonic())
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
                "communication_policy": decision.communication_policy.to_payload(),
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
    state.started_at_monotonic = float(getattr(args, "_tui_turn_started_at", 0.0) or time.monotonic())
    state.set_intent_decision(decision)
    state.current_step = "Read-only request"
    state._record_progress("Read-only route selected.")
    model_answered = False
    if decision.complexity == "complex" and use_model_loop:
        model_answered = _run_tui_read_only_model_answer(args, decision, state)
    if not model_answered:
        if use_model_loop:
            state._record_progress("Model read-only answer was unavailable; using bounded workspace inspection.")
        activity, answer = _build_read_only_answer(
            workspace_root,
            objective,
            decision,
            context_text=_tui_read_only_context_text(args, objective),
        )
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
                "communication_policy": decision.communication_policy.to_payload(),
            },
        )
    return 0


def _run_tui_simple_write(
    args: argparse.Namespace,
    decision: TuiIntentDecision,
    parser: argparse.ArgumentParser | None = None,
) -> int:
    workspace_root = normalize_workspace_root(str(args.workspace))
    objective = str(getattr(args, "target", "") or "").strip()
    selected_skills = list(getattr(args, "_tui_selected_skill_mentions", []) or [])
    if selected_skills:
        plan = _parse_simple_write_request(objective)
        if plan is None:
            if parser is not None:
                setattr(
                    args,
                    "_tui_intent_decision",
                    TuiIntentDecision(
                        permission="write",
                        complexity="complex",
                        confidence=decision.confidence,
                        rationale=decision.rationale or "selected skill requires the executor path",
                        communication_policy=decision.communication_policy,
                    ),
                )
                return _run_tui_turn(args, parser, force_live=True)
            state = SuperTuiState(
                objective=objective,
                workspace=str(workspace_root),
                status="running",
                phase="complex write",
                debug_events=bool(getattr(args, "raw_events", False)),
            )
            state.started_at_monotonic = time.monotonic()
            state.set_intent_decision(
                TuiIntentDecision(
                    permission="write",
                    complexity="complex",
                    confidence=decision.confidence,
                    rationale=decision.rationale or "selected skill requires the skill-aware executor path",
                    communication_policy=decision.communication_policy,
                )
            )
            state._record_progress("Selected skill requires the skill-aware executor path.")
            state._record_answer("Selected skill routing requires the normal executor path.")
            state.status = "completed"
            state.phase = "done"
            state.current_step = "Selected skill routed"
            state.ended_at_monotonic = time.monotonic()
            _render_static_state(
                state,
                plain=bool(getattr(args, "plain", False)),
                raw_events=bool(getattr(args, "raw_events", False)),
            )
            return 0
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
        if parser is not None:
            run_args = copy.copy(args)
            setattr(
                run_args,
                "_tui_intent_decision",
                TuiIntentDecision(
                    permission="write",
                    complexity="complex",
                    confidence=decision.confidence,
                    rationale=decision.rationale or "request needs the executor path",
                ),
            )
            return _run_tui_turn(run_args, parser, force_live=True)
        state.status = "blocked"
        state.phase = "blocked"
        state.current_step = "Executor path unavailable"
        state._record_answer("This request needs the normal executor path, but no executor context is attached.")
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
    target_text = str(getattr(args, "target", "") or "").strip()
    lowered_target = target_text.lower()
    turn_started_at = float(getattr(args, "_tui_turn_started_at", 0.0) or time.monotonic())
    setattr(args, "_tui_turn_started_at", turn_started_at)
    workspace_root = normalize_workspace_root(str(args.workspace))
    _set_tui_surface_attachments_from_text(args, workspace_root, target_text)
    _set_tui_surface_context_from_transcript(args, workspace_root, current_text=target_text)
    if lowered_target in {"/exit", "/quit", "exit", "quit"}:
        return 0
    if lowered_target == "/plan" or lowered_target.startswith("/plan "):
        objective = target_text[5:].strip() if lowered_target.startswith("/plan") else target_text
        return _run_tui_plan_mode(args, objective)
    if lowered_target == "/help":
        _render_tui_help(args)
        return 0
    if lowered_target == "/skills" or lowered_target.startswith("/skills "):
        query = target_text.split(maxsplit=1)[1] if " " in target_text else ""
        skills = _load_tui_skill_suggestions(workspace_root)
        _render_tui_text_command(args, "Skills", _format_skill_suggestions(skills, query))
        return 0
    if lowered_target in {"/queues", "/queue"}:
        _render_tui_text_command(args, "Queues", _format_tui_queue_summary(format_super_queue_status(workspace_root)))
        return 0
    if lowered_target == "/tasks" or lowered_target.startswith("/tasks "):
        target = target_text.split(maxsplit=1)[1] if " " in target_text else ""
        if _tui_async_agent_enabled(args):
            result = _submit_tui_async_admission(
                args,
                workspace_root=workspace_root,
                text="/status",
                background=False,
            )
            _render_tui_task_overview(args, workspace_root, title="Tasks", target=target, result=result)
            return 0 if result.ok else 1
        _render_tui_task_overview(args, workspace_root, title="Tasks", target=target)
        return 0
    if lowered_target == "/status" or lowered_target.startswith("/status "):
        target = target_text.split(maxsplit=1)[1] if " " in target_text else ""
        if _tui_async_agent_enabled(args):
            result = _submit_tui_async_admission(
                args,
                workspace_root=workspace_root,
                text="/status",
                background=False,
            )
            _render_tui_task_overview(args, workspace_root, title="Status", target=target, result=result)
            return 0 if result.ok else 1
        _render_tui_task_overview(args, workspace_root, title="Status", target=target)
        return 0
    if lowered_target in {"/inside", "/trace"} or lowered_target.startswith(("/inside ", "/trace ")):
        target = target_text.split(maxsplit=1)[1] if " " in target_text else ""
        if _tui_async_agent_enabled(args):
            result = _submit_tui_async_admission(
                args,
                workspace_root=workspace_root,
                text="/status",
                background=False,
            )
            _render_tui_inside(args, workspace_root, target=target, result=result)
            return 0 if result.ok else 1
        _render_tui_inside(args, workspace_root, target=target)
        return 0
    if lowered_target == "/new":
        _render_tui_text_command(args, "Help", "Usage: /new <objective>")
        return 0
    if lowered_target.startswith("/new "):
        target_text = target_text[5:].strip()
        lowered_target = target_text.lower()
        setattr(args, "target", target_text)
        setattr(args, "_tui_forced_new", True)
        _clear_tui_pending_plan_state(workspace_root)
        if not target_text:
            _render_tui_text_command(args, "Help", "Usage: /new <objective>")
            return 0
    command = _parse_tui_run_command(target_text)
    if command is not None:
        if _tui_async_agent_enabled(args) and command.command in {"append", "pause", "resume", "cancel"}:
            result = _submit_tui_async_admission(
                args,
                workspace_root=workspace_root,
                text=target_text,
                background=False,
            )
            _print_tui_stream_block(
                "Command",
                _tui_async_admission_compact_lines(result),
                plain=bool(getattr(args, "plain", False)),
            )
            transcript_workspace = str(getattr(args, "_tui_transcript_workspace", "") or "").strip()
            if transcript_workspace:
                if result.ok:
                    _append_tui_async_admission_transcript(
                        Path(transcript_workspace),
                        result,
                        text=target_text,
                    )
                else:
                    _append_tui_transcript_entry(
                        Path(transcript_workspace),
                        role="system_notice",
                        text=result.message,
                        metadata={"async_agent": True, "command": command.command, "error": result.error},
                    )
            return 0 if result.ok else 1
        result = _handle_tui_board_command(workspace_root, command)
        _render_tui_text_command(args, "Command", result.message)
        transcript_workspace = str(getattr(args, "_tui_transcript_workspace", "") or "").strip()
        if transcript_workspace:
            _append_tui_transcript_entry(
                Path(transcript_workspace),
                role="system_notice",
                text=result.message,
                metadata=_tui_board_command_transcript_metadata(command, result),
            )
        return 0
    if lowered_target in {"/reset", "/clear"} or lowered_target.startswith(("/reset ", "/clear ")):
        reset_scope = target_text.split(maxsplit=1)[1] if " " in target_text else ""
        reset_message = _reset_tui_context(workspace_root, reset_scope)
        _render_tui_text_command(args, "System", reset_message)
        transcript_workspace = str(getattr(args, "_tui_transcript_workspace", "") or "").strip()
        if transcript_workspace:
            _append_tui_transcript_entry(
                Path(transcript_workspace),
                role="system_notice",
                text=reset_message,
                metadata={"command": "reset", "scope": reset_scope or "all"},
            )
        return 0
    if lowered_target in {"/progress", "/last"} or lowered_target.startswith(("/progress ", "/last ")):
        decision = TuiIntentDecision(
            permission="read-only",
            complexity="narrator",
            confidence=1.0,
            rationale="explicit slash command",
        )
        routed_with_model = False
    else:
        decision, routed_with_model = _route_tui_intent_with_model(args)
    setattr(args, "_tui_routed_with_model", routed_with_model)
    setattr(args, "_tui_intent_decision", decision)
    setattr(args, "_tui_communication_policy", decision.communication_policy.to_payload())
    if decision.needs_clarification:
        return _render_tui_clarification(args, decision)
    if decision.lane == PLAN_MODE and not bool(getattr(args, "plan_only", False)):
        return _run_tui_plan_mode(args, target_text)
    if decision.complexity == "narrator" and not bool(getattr(args, "plan_only", False)):
        use_model_loop = bool(routed_with_model or getattr(args, "_model_explicit", False))
        return _run_tui_narrator(args, decision, use_model_loop=use_model_loop)
    if decision.permission == "read-only" and not bool(getattr(args, "plan_only", False)):
        use_model_loop = bool(routed_with_model or getattr(args, "_model_explicit", False))
        return _run_tui_read_only(args, decision, use_model_loop=use_model_loop)
    if decision.lane == "simple write" and not bool(getattr(args, "plan_only", False)):
        selected_skills = list(getattr(args, "_tui_selected_skill_mentions", []) or [])
        if selected_skills:
            decision = TuiIntentDecision(
                permission="write",
                complexity="complex",
                confidence=decision.confidence,
                rationale=decision.rationale or "selected skill requires the skill-aware executor path",
                communication_policy=decision.communication_policy,
            )
            setattr(args, "_tui_intent_decision", decision)
            setattr(args, "_tui_communication_policy", decision.communication_policy.to_payload())
        else:
            return _run_tui_simple_write(args, decision, parser)
    if (
        decision.permission == "write"
        and not bool(getattr(args, "plan_only", False))
        and bool(getattr(args, "_tui_async_interactive", False))
        and _tui_async_agent_enabled(args)
        and _tui_should_use_async_background_for_write(args, workspace_root)
    ):
        raw_text = str(getattr(args, "target", "") or "")
        effective_text = _tui_effective_async_text_for_write_turn(args, workspace_root, raw_text)
        forced_new = _tui_forces_new_async_work_for_prose(args, effective_text)
        result = _submit_tui_async_admission(
            args,
            workspace_root=workspace_root,
            text=effective_text,
            forced_new=forced_new,
            background=True,
        )
        _render_tui_async_admission_compact(args, result)
        if str(getattr(args, "_tui_transcript_workspace", "") or "").strip():
            if result.ok:
                _append_tui_async_admission_transcript(
                    workspace_root,
                    result,
                    text=effective_text,
                    forced_new=forced_new,
                    communication_policy=decision.communication_policy,
                )
            else:
                _append_tui_transcript_entry(
                    workspace_root,
                    role="system_notice",
                    text=result.message,
                    metadata={"async_agent": True, "error": result.error},
                )
        if result.ok:
            return 0
        if bool(getattr(args, "async_agent", False)):
            return 1
    return _run_tui_turn(args, parser, force_live=bool(force_live or routed_with_model))


def _render_composer_hint(workspace_root: Path, *, plain: bool, skill_count: int, path_count: int = 0) -> None:
    if not plain:
        Console, _ = _try_import_rich()
        if Console is not None:
            try:
                from rich.panel import Panel
                from rich.text import Text

                body = Text()
                body.append("Type in the composer below. Submitted text is saved before routing.\n", style="bold")
                body.append(f"Workspace: {workspace_root}\n", style="dim")
                body.append(
                    "Commands: /plan, /progress, /tasks, /status, /inside, /new, /stop, /skills, /reset, /help, /exit\n",
                    style="cyan",
                )
                body.append(f"Skill suggestions: {skill_count} loaded\n", style="green" if skill_count else "dim")
                body.append(f"Path suggestions: {path_count} loaded\n", style="green" if path_count else "dim")
                body.append("Outbox: durable\n", style="green")
                body.append("Screenshot paste: Ctrl-V attaches clipboard image when available\n", style="dim")
                body.append("History: Up/Down recalls submitted messages", style="dim")
                console = Console()
                console.print(
                    Panel(
                        body,
                        title=_tui_panel_title("Message"),
                        border_style="cyan",
                        padding=(1, 2),
                    )
                )
                return
            except Exception:
                pass
    print(_tui_section_title("Message"), flush=True)
    print("  Type in the composer below. Submitted text is saved before routing.", flush=True)
    print(f"  Workspace: {workspace_root}", flush=True)
    print(
        "  Commands: /plan, /progress, /tasks, /status, /inside, /new, /stop, /skills, /reset, /help, /exit",
        flush=True,
    )
    print(f"  Skill suggestions: {skill_count} loaded", flush=True)
    print(f"  Path suggestions: {path_count} loaded", flush=True)
    print("  Outbox: durable", flush=True)
    print("  Screenshot paste: Ctrl-V attaches clipboard image when available", flush=True)
    print("  History: Up/Down recalls submitted messages", flush=True)


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
                from rich.text import Text

                table = Table.grid(expand=True)
                table.add_column(justify="right", no_wrap=True, width=7)
                table.add_column(no_wrap=True, width=2)
                table.add_column(ratio=1)
                for entry in entries:
                    label = _transcript_role_label(entry.role)
                    role_style = {
                        "You": "bold blue",
                        "DAN": "bold cyan",
                        "System": "dim white",
                        "Trace": "dim cyan",
                    }.get(label, "bold white")
                    body = "\n".join(_transcript_entry_body_lines(entry))
                    table.add_row(
                        Text(label, style=role_style),
                        Text("  "),
                        _rich_semantic_text(body, base_style="grey70"),
                    )
                console = Console()
                console.print(Panel(table, title=_tui_panel_title("Conversation"), border_style="blue"))
                return
            except Exception:
                pass
    print(_tui_section_title("Conversation"), flush=True)
    for entry in entries:
        for line in _format_transcript_entry_lines(entry):
            print(f"  {line}", flush=True)


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


def _should_dispatch_tui_turn_in_background(args: argparse.Namespace, *, async_agent_enabled: bool) -> bool:
    workspace_root = normalize_workspace_root(str(getattr(args, "workspace", "") or "."))
    return _tui_should_use_async_background_for_turn(
        args,
        async_agent_enabled=async_agent_enabled,
        workspace_root=workspace_root,
    )


def _start_tui_background_dispatch(
    turn_args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    *,
    workspace_root: Path,
    message_id: str,
    objective: str,
    forced_new: bool,
    plan_only: bool,
) -> threading.Thread:
    run_args = copy.copy(turn_args)
    setattr(run_args, "_tui_background_dispatch", True)

    def _runner() -> None:
        try:
            try:
                exit_code = _dispatch_tui_turn(run_args, parser, force_live=not plan_only)
            except SystemExit as exc:
                exit_code = int(exc.code or 0) if isinstance(exc.code, int) else 2
        except BaseException as exc:
            exit_code = 1
            _render_tui_text_command(
                run_args,
                "System",
                f"Turn failed before it could finish routing: {type(exc).__name__}: {exc}",
            )
        _append_tui_outbox_event(
            workspace_root,
            message_id=message_id,
            status="acknowledged" if exit_code in {0, 1} else "failed",
            text=objective,
            route="plan" if plan_only else "turn",
            metadata={
                "surface": "super-tui",
                "exit_code": exit_code,
                "forced_new": forced_new,
                "background_dispatch": True,
            },
        )

    thread = threading.Thread(target=_runner, name="super-tui-turn-dispatch", daemon=True)
    thread.start()
    return thread


@dataclass(frozen=True)
class TuiChatboxQueuedTurn:
    turn_args: argparse.Namespace
    message_id: str
    objective: str
    forced_new: bool
    plan_only: bool


class TuiChatboxTurnScheduler:
    """Single-lane UI worker that keeps the prompt usable while turns run."""

    def __init__(
        self,
        *,
        parser: argparse.ArgumentParser,
        workspace_root: Path,
        plain: bool = False,
    ) -> None:
        self._parser = parser
        self._workspace_root = workspace_root
        self._plain = bool(plain)
        self._lock = threading.Lock()
        self._queue: deque[TuiChatboxQueuedTurn] = deque()
        self._active_thread: threading.Thread | None = None

    def has_pending_work(self) -> bool:
        with self._lock:
            return bool((self._active_thread is not None and self._active_thread.is_alive()) or self._queue)

    def submit(self, item: TuiChatboxQueuedTurn) -> bool:
        with self._lock:
            if self._active_thread is not None and self._active_thread.is_alive():
                self._queue.append(item)
                queue_position = len(self._queue)
            else:
                self._start_locked(item)
                return True
        _print_tui_stream_block(
            "Chat -> Queue",
            [
                "Queued behind the active turn.",
                f"Position: {queue_position}. Composer stays open.",
            ],
            plain=self._plain,
        )
        return False

    def _start_locked(self, item: TuiChatboxQueuedTurn) -> None:
        started_at = time.monotonic()
        _print_tui_stream_block(
            "Chat -> Thinking",
            [
                _tui_chatbox_thinking_text(started_at),
                "Composer stays open while this turn is routed.",
            ],
            plain=self._plain,
        )
        thread = _start_tui_background_dispatch(
            item.turn_args,
            self._parser,
            workspace_root=self._workspace_root,
            message_id=item.message_id,
            objective=item.objective,
            forced_new=item.forced_new,
            plan_only=item.plan_only,
        )
        self._active_thread = thread
        watcher = threading.Thread(
            target=self._watch_thread,
            args=(thread,),
            name="super-tui-chatbox-queue",
            daemon=True,
        )
        watcher.start()

    def _watch_thread(self, thread: threading.Thread) -> None:
        thread.join()
        with self._lock:
            if self._active_thread is thread:
                self._active_thread = None
            if self._queue:
                self._start_locked(self._queue.popleft())


def _interactive_loop(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    workspace_root = normalize_workspace_root(str(args.workspace))
    commands = _TUI_COMMANDS
    skills = _load_tui_skill_suggestions(workspace_root)
    path_suggestions = _load_tui_path_suggestions(workspace_root)
    chatbox_scheduler = TuiChatboxTurnScheduler(
        parser=parser,
        workspace_root=workspace_root,
        plain=bool(getattr(args, "plain", False)),
    )
    _render_transcript_history(
        workspace_root,
        plain=bool(getattr(args, "plain", False)),
    )
    if not _prompt_toolkit_chatbox_available():
        _render_composer_hint(
            workspace_root,
            plain=bool(getattr(args, "plain", False)),
            skill_count=len(skills),
            path_count=len(path_suggestions),
        )
    exit_armed_for_running_turn = False
    while True:
        try:
            try:
                text = _read_interactive_line(
                    "super-tui> ",
                    commands=commands,
                    skills=skills,
                    paths=path_suggestions,
                    workspace_root=workspace_root,
                )
            except TypeError as exc:
                if "workspace_root" not in str(exc):
                    raise
                text = _read_interactive_line(
                    "super-tui> ",
                    commands=commands,
                    skills=skills,
                    paths=path_suggestions,
                )
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            if _request_tui_stop_from_shortcut(args, workspace_root, source="ctrl-c"):
                continue
            print()
            return 130
        objective = text.strip()
        if not objective:
            continue
        lowered = objective.lower()
        async_agent_enabled = _tui_async_agent_enabled(args)
        if lowered in {"/exit", "/quit", "exit", "quit"}:
            if chatbox_scheduler.has_pending_work() and not exit_armed_for_running_turn:
                _render_tui_text_command(
                    args,
                    "System",
                    "A turn is still running or queued. Type /exit again to quit anyway, or use /stop to request an interrupt.",
                )
                exit_armed_for_running_turn = True
                continue
            return 0
        exit_armed_for_running_turn = False
        message_id = _new_tui_message_id(objective)
        _clear_tui_draft(workspace_root)
        _append_tui_outbox_event(
            workspace_root,
            message_id=message_id,
            status="submitted",
            text=objective,
            metadata={"surface": "super-tui"},
        )

        def _mark_outbox(status: str, route: str, **metadata: Any) -> None:
            _append_tui_outbox_event(
                workspace_root,
                message_id=message_id,
                status=status,
                text=objective,
                route=route,
                metadata={"surface": "super-tui", **metadata},
            )

        if lowered == "/tasks" or lowered.startswith("/tasks "):
            target = objective.split(maxsplit=1)[1] if " " in objective else ""
            if async_agent_enabled:
                result = _submit_tui_async_admission(
                    args,
                    workspace_root=workspace_root,
                    text="/status",
                    background=False,
                )
                if result.ok:
                    _render_tui_task_overview(args, workspace_root, title="Tasks", target=target, result=result)
                    _append_tui_async_admission_transcript(
                        workspace_root,
                        result,
                        text=objective,
                    )
                    _mark_outbox("acknowledged", "tasks", ok=True)
                    continue
                _render_tui_task_overview(args, workspace_root, title="Tasks", target=target, result=result)
                _mark_outbox("acknowledged", "tasks", ok=False)
                continue
            _render_tui_task_overview(args, workspace_root, title="Tasks", target=target)
            _mark_outbox("acknowledged", "tasks")
            continue
        if lowered in {"/queues", "queues"}:
            _render_tui_text_command(args, "Queues", _format_tui_queue_summary(format_super_queue_status(workspace_root)))
            _mark_outbox("acknowledged", "queues")
            continue
        if lowered in {"/status", "status"} or lowered.startswith("/status "):
            target = objective.split(maxsplit=1)[1] if " " in objective else ""
            if async_agent_enabled:
                result = _submit_tui_async_admission(
                    args,
                    workspace_root=workspace_root,
                    text="/status",
                    background=False,
                )
                if result.ok:
                    _render_tui_task_overview(args, workspace_root, title="Status", target=target, result=result)
                    _append_tui_async_admission_transcript(
                        workspace_root,
                        result,
                        text=objective,
                    )
                    _mark_outbox("acknowledged", "status", ok=True)
                    continue
                _render_tui_task_overview(args, workspace_root, title="Status", target=target, result=result)
                _mark_outbox("acknowledged", "status", ok=False)
                continue
            _render_tui_task_overview(args, workspace_root, title="Status", target=target)
            _mark_outbox("acknowledged", "status")
            continue
        if lowered in {"/inside", "/trace"} or lowered.startswith(("/inside ", "/trace ")):
            target = objective.split(maxsplit=1)[1] if " " in objective else ""
            if async_agent_enabled:
                result = _submit_tui_async_admission(
                    args,
                    workspace_root=workspace_root,
                    text="/status",
                    background=False,
                )
                _render_tui_inside(args, workspace_root, target=target, result=result)
                if result.ok:
                    _append_tui_async_admission_transcript(
                        workspace_root,
                        result,
                        text=objective,
                    )
                _mark_outbox("acknowledged", "inside", ok=bool(result.ok))
                continue
            _render_tui_inside(args, workspace_root, target=target)
            _mark_outbox("acknowledged", "inside")
            continue
        if lowered in {"/help", "help"}:
            _render_tui_help(args)
            _mark_outbox("acknowledged", "help")
            continue
        if lowered == "/skills" or lowered.startswith("/skills "):
            query = objective.split(maxsplit=1)[1] if " " in objective else ""
            _render_tui_text_command(args, "Skills", _format_skill_suggestions(skills, query))
            _mark_outbox("acknowledged", "skills")
            continue
        forced_new = False
        if lowered == "/new":
            _render_tui_text_command(args, "Help", "Usage: /new <objective>")
            _mark_outbox("acknowledged", "new-help")
            continue
        if lowered.startswith("/new "):
            objective = objective[5:].strip()
            lowered = objective.lower()
            forced_new = True
            _clear_tui_pending_plan_state(workspace_root)
        command = _parse_tui_run_command(objective)
        if command is not None:
            if async_agent_enabled and command.command in {"append", "pause", "resume", "cancel"}:
                _append_tui_transcript_entry(
                    workspace_root,
                    role="user",
                    text=objective,
                    metadata={"async_agent": True, "command": command.command},
                )
                result = _submit_tui_async_admission(
                    args,
                    workspace_root=workspace_root,
                    text=objective,
                    background=False,
                )
                if result.ok:
                    _print_tui_stream_block(
                        "Command",
                        _tui_async_admission_compact_lines(result),
                        plain=bool(getattr(args, "plain", False)),
                    )
                    _append_tui_async_admission_transcript(
                        workspace_root,
                        result,
                        text=objective,
                    )
                    _mark_outbox("acknowledged", f"command:{command.command}", ok=True)
                    continue
                _print_tui_stream_block(
                    "Command",
                    _tui_async_admission_compact_lines(result),
                    plain=bool(getattr(args, "plain", False)),
                )
                _mark_outbox("acknowledged", f"command:{command.command}", ok=False)
                continue
            result = _handle_tui_board_command(workspace_root, command)
            _render_tui_text_command(args, "Command", result.message)
            _append_tui_transcript_entry(
                workspace_root,
                role="system_notice",
                text=result.message,
                metadata=_tui_board_command_transcript_metadata(command, result),
            )
            _mark_outbox("acknowledged", f"command:{command.command}")
            continue
        if lowered in {"/reset", "reset", "/clear", "clear"} or lowered.startswith(
            ("/reset ", "reset ", "/clear ", "clear ")
        ):
            reset_scope = objective.split(maxsplit=1)[1] if " " in objective else ""
            reset_message = _reset_tui_context(workspace_root, reset_scope)
            _render_tui_text_command(args, "System", reset_message)
            _append_tui_transcript_entry(
                workspace_root,
                role="system_notice",
                text=reset_message,
                metadata={"command": "reset", "scope": reset_scope or "all"},
            )
            _mark_outbox("acknowledged", "reset", scope=reset_scope or "all")
            continue
        plan_only = False
        if lowered.startswith("/plan "):
            objective = objective[6:].strip()
            _append_tui_transcript_entry(
                workspace_root,
                role="user",
                text=f"/plan {objective}".strip(),
                metadata={"plan_mode": True},
            )
            exit_code = _run_tui_plan_mode(args, objective)
            _mark_outbox("acknowledged" if exit_code == 0 else "failed", "plan-mode", exit_code=exit_code)
            if exit_code not in {0, 1}:
                return exit_code
            continue
        pending_plan = _load_tui_pending_plan_state(workspace_root)
        if pending_plan is not None and not objective.startswith("/") and not forced_new:
            exit_code = _run_tui_plan_reply(args, objective, pending_plan, parser)
            _mark_outbox("acknowledged" if exit_code == 0 else "failed", "plan-reply", exit_code=exit_code)
            if exit_code not in {0, 1}:
                return exit_code
            continue
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
            _mark_outbox("acknowledged", "skill-selection")
            continue
        if not str(turn_args.target or "").strip():
            continue
        attachments = _set_tui_surface_attachments_from_text(turn_args, workspace_root, str(turn_args.target or ""))
        _set_tui_surface_context_from_transcript(turn_args, workspace_root, current_text=str(turn_args.target or ""))
        turn_args.plan_only = plan_only
        turn_args.json = False
        turn_args.output = None
        setattr(turn_args, "_tui_transcript_workspace", str(workspace_root))
        setattr(turn_args, "_tui_async_interactive", async_agent_enabled)
        setattr(turn_args, "_tui_forced_new", forced_new)
        _append_tui_transcript_entry(
            workspace_root,
            role="user",
            text=objective,
            metadata={
                "plan_only": plan_only,
                "forced_new": forced_new,
                "selected_skills": list(getattr(turn_args, "_tui_selected_skill_mentions", []) or []),
                "attachments": attachments,
            },
        )
        if _should_dispatch_tui_turn_in_background(turn_args, async_agent_enabled=async_agent_enabled):
            chatbox_scheduler.submit(
                TuiChatboxQueuedTurn(
                    turn_args=turn_args,
                    message_id=message_id,
                    objective=objective,
                    forced_new=forced_new,
                    plan_only=plan_only,
                )
            )
            continue
        try:
            exit_code = _dispatch_tui_turn(turn_args, parser, force_live=not plan_only)
        except SystemExit as exc:
            exit_code = int(exc.code or 0) if isinstance(exc.code, int) else 2
        _mark_outbox(
            "acknowledged" if exit_code in {0, 1} else "failed",
            "plan" if plan_only else "turn",
            exit_code=exit_code,
            forced_new=forced_new,
        )
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
    _set_tui_surface_attachments_from_text(
        args,
        normalize_workspace_root(str(args.workspace)),
        str(getattr(args, "target", "") or ""),
    )
    _set_tui_surface_context_from_transcript(
        args,
        normalize_workspace_root(str(args.workspace)),
        current_text=str(getattr(args, "target", "") or ""),
    )
    return _dispatch_tui_turn(args, parser)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
