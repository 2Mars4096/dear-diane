"""dan-code — local coding product CLI built on the universal worker."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from pydantic import BaseModel, Field

from dan.cli import load_env, normalize_workspace_root, resolve_config
from dan.cli.code_product import (
    CODE_PRODUCT_NAME,
    CodeProductConfig,
    CodeProductPaths,
    CodingCliSession,
    CodingOrganismReport,
    append_code_product_transcript,
    load_code_product_config,
    load_code_product_session,
    resolve_code_product_paths,
    save_code_product_session,
    write_code_product_config,
)
from dan.providers import LLMProvider
from dan.providers.factory import build_provider_registry
from dan.server.runtime_config import build_engine_config_from_env
from dan.worker.composition import CrossCellTraceLog
from dan.worker.core.executor import WorkerCoreExecutor
from dan.worker.organisms import (
    CodingConversationContext,
    CodingConversationController,
    CodingConversationMessage,
    CodingConversationReportSummary,
    CodingTask,
    coding_execution_organism,
    execute_coding_organism,
)
from dan.worker.organisms.local_runtime import (
    DEFAULT_LIVE_ORGANISM_TOOL_IDS,
    LocalOrganismToolRuntime,
    ToolLoopCompletionProvider,
    attach_local_tooling_to_coding_organism,
    available_local_organism_tools,
)
from dan.worker.signaling import EvidenceRef

DEFAULT_CODING_OBJECTIVE = (
    "Inspect the workspace, produce one bounded coding candidate, and return "
    "explicit target files, focused validation, and risks."
)
DEFAULT_CODING_ACCEPTANCE_CRITERIA = [
    "Return exactly one bounded candidate.",
    "Name the target files explicitly.",
    "Include a focused validation plan.",
    "Keep the change summary and risks inspectable.",
]
RISKY_TOOL_IDS = frozenset(
    {
        "file_write",
        "file_delete",
        "file_move",
        "file_copy",
        "shell_command",
        "git_commit",
        "git_branch",
        "git_worktree",
        "python_eval",
    }
)
SESSION_RESULTS_MESSAGES = (
    "tell me the results",
    "show me the results",
    "give me the results",
    "give me results",
    "tell me results",
    "show me results",
    "give me responses",
    "show me responses",
    "tell me responses",
    "can you give me responses",
    "can you give me the results",
    "what are the results",
    "what were the results",
    "what happened",
    "latest result",
    "last result",
)
WORKSPACE_QUERY_MESSAGES = (
    "root dir",
    "root directory",
    "workspace root",
    "workspace directory",
    "current directory",
    "current dir",
    "working directory",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dan-code",
        description=(
            "Run DAN Code as a local coding product on top of the universal worker "
            "surface. Omit the task to start an interactive session. Per-workspace "
            "state is kept under .dan-code/."
        ),
    )
    parser.add_argument(
        "objective",
        nargs="?",
        default=None,
        help="Bounded coding objective. If omitted, start the interactive coding CLI.",
    )
    parser.add_argument(
        "--task-id",
        default="coding-organ-task",
        help="Task identifier recorded in the coding trace.",
    )
    parser.add_argument(
        "--organ-id",
        default="coding-build",
        help="Internal organ identifier stamped into the worker trace.",
    )
    parser.add_argument(
        "--organism-id",
        default="coding-organism",
        help="Internal organism identifier stamped into the worker trace.",
    )
    parser.add_argument(
        "--acceptance-criterion",
        dest="acceptance_criteria",
        action="append",
        default=[],
        help="Append one acceptance criterion.",
    )
    parser.add_argument(
        "--research-finding",
        dest="research_findings",
        action="append",
        default=[],
        help="Append one research finding or context note for the coding run.",
    )
    parser.add_argument(
        "--repair-brief",
        default="",
        help="Optional repair brief passed into the coding run.",
    )
    parser.add_argument(
        "--evidence-summary",
        dest="evidence_summaries",
        action="append",
        default=[],
        help="Append one compact evidence summary note.",
    )
    parser.add_argument(
        "--workdir",
        default=None,
        help="Directory for generated evidence note files. Defaults to .dan-code/runs inside the workspace.",
    )
    parser.add_argument(
        "--workspace",
        help="Workspace root for local tool calls. Defaults to DAN_WORKSPACE_ROOT or the current directory.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Live model. Falls back to workspace product config, .env, then runtime defaults.",
    )
    parser.add_argument(
        "--api-key",
        help="Optional default-provider API key override.",
    )
    parser.add_argument(
        "--base-url",
        help="Optional default-provider base URL override.",
    )
    parser.add_argument(
        "--tool",
        dest="tool_ids",
        action="append",
        default=[],
        help="Enable one local tool. Repeat to override the default coding basket.",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="List the available local coding tools and exit.",
    )
    parser.add_argument(
        "--approval-mode",
        choices=["auto", "confirm-risky", "confirm-all"],
        default=None,
        help=(
            "Tool approval policy. Defaults to confirm-risky for interactive sessions "
            "and auto for one-shot runs."
        ),
    )
    parser.add_argument(
        "--thinking-mode",
        choices=["auto", "enabled", "disabled"],
        default=None,
        help=(
            "Provider thinking mode for live model calls. Falls back to workspace "
            "product config, DAN_CODE_THINKING_MODE, then auto."
        ),
    )
    parser.add_argument(
        "--quiet-progress",
        action="store_true",
        help="Disable live tool/progress rendering for non-JSON runs.",
    )
    parser.add_argument(
        "--show-model-trace",
        action="store_true",
        help=(
            "Show public model request/response previews in CLI progress output. "
            "This does not expose hidden chain-of-thought."
        ),
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Initialize or refresh the workspace-local .dan-code/config.json and exit.",
    )
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="Show the resolved DAN Code product configuration and exit.",
    )
    parser.add_argument(
        "--session-file",
        help="Optional session file override. Relative paths resolve from the workspace root.",
    )
    parser.add_argument(
        "--new-session",
        action="store_true",
        help="Ignore any saved session and start from a fresh one.",
    )
    parser.add_argument(
        "--no-session-persist",
        action="store_true",
        help="Do not load or save workspace-local session state.",
    )
    parser.add_argument(
        "--max-tool-rounds",
        type=int,
        default=None,
        help="Maximum provider tool rounds per worker completion.",
    )
    parser.add_argument(
        "--max-tool-calls",
        type=int,
        default=None,
        help="Maximum provider tool calls per worker completion.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full coding report or config payload as JSON.",
    )
    parser.add_argument(
        "--output",
        help="Optional path to write the JSON report.",
    )
    return parser


def _dedupe(values: Sequence[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def _non_empty(values: Sequence[str], defaults: Sequence[str]) -> list[str]:
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    return cleaned or list(defaults)


def _resolve_user_path(value: str | Path, *, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _now_context() -> dict[str, str]:
    now = datetime.now().astimezone()
    return {
        "current_timestamp": now.isoformat(),
        "current_date": now.date().isoformat(),
        "timezone": str(now.tzinfo or ""),
    }


def _normalize_message(text: str) -> str:
    return " ".join(
        re.sub(r"[^a-z0-9/]+", " ", str(text or "").strip().lower()).split()
    )


def _is_workspace_query(text: str) -> bool:
    normalized = _normalize_message(text)
    if normalized == "pwd":
        return True
    if not any(token in normalized.split() for token in {"what", "where", "show", "tell", "print"}):
        return False
    return any(phrase in normalized for phrase in WORKSPACE_QUERY_MESSAGES)


def _is_results_query(text: str) -> bool:
    normalized = _normalize_message(text)
    return any(phrase in normalized for phrase in SESSION_RESULTS_MESSAGES)


def _print_workspace_query_answer(*, workspace_root: Path, session: CodingCliSession) -> None:
    print(f"workspace root: {workspace_root}")
    print(
        "current working directory: "
        f"{Path(os.environ.get('PWD') or str(workspace_root)).expanduser()}"
    )
    print(f"session: {session.session_id}")
    print("Use /status for the full session view.")


def _print_latest_result(session: CodingCliSession) -> None:
    if not session.turns:
        print("No saved coding results yet.")
        print("Use /status or /help to see what DAN Code can do.")
        return
    print("Latest saved result:")
    _print_report(session.turns[-1].model_dump(mode="json"))
    _print_session_rollup(session)
    print("Use /history for prior turns.")


def _handle_local_prompt(
    text: str,
    *,
    workspace_root: Path,
    session: CodingCliSession,
    trailing_blank_line: bool,
) -> bool:
    if _is_workspace_query(text):
        _print_workspace_query_answer(workspace_root=workspace_root, session=session)
    elif _is_results_query(text):
        _print_latest_result(session)
    else:
        return False
    if trailing_blank_line:
        print()
    return True


def _build_runtime_context(
    *,
    workspace_root: Path,
    model: str,
    tool_ids: Sequence[str],
    approval_mode: str,
    thinking_mode: str,
    task_id: str,
) -> dict[str, Any]:
    now_context = _now_context()
    return {
        "product_name": CODE_PRODUCT_NAME,
        "workspace_root": str(workspace_root),
        "current_working_directory": str(Path(os.environ.get("PWD") or str(workspace_root)).expanduser()),
        "model": model,
        "task_id": task_id,
        "tool_ids": list(tool_ids),
        "approval_mode": approval_mode,
        "thinking_mode": thinking_mode,
        **now_context,
    }


def _truncate_text(value: str, *, limit: int = 120) -> str:
    text = str(value or "").strip().replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _effective_approval_mode(requested: str | None, *, interactive: bool) -> str:
    if requested:
        return requested
    return "confirm-risky" if interactive else "auto"


def _effective_thinking_mode(
    requested: str | None,
    *,
    product_config: CodeProductConfig | None = None,
) -> str:
    candidate = str(requested or "").strip()
    if not candidate and product_config is not None:
        candidate = str(product_config.thinking_mode or "").strip()
    if not candidate:
        candidate = str(os.environ.get("DAN_CODE_THINKING_MODE") or "").strip()
    normalized = (candidate or "auto").lower()
    if normalized not in {"auto", "enabled", "disabled"}:
        raise ValueError(
            "DAN Code thinking mode must be one of: auto, enabled, disabled"
        )
    return normalized


def _summarize_items(items: Sequence[Any], *, limit: int = 3) -> str:
    texts = [str(item).strip() for item in items if str(item).strip()]
    if not texts:
        return ""
    summary = ", ".join(texts[:limit])
    if len(texts) > limit:
        summary += f", +{len(texts) - limit} more"
    return summary


def _summarize_briefs(briefs: Sequence[Any], *, limit: int = 2) -> str:
    previews: list[str] = []
    for brief in briefs[:limit]:
        text = _truncate_text(str(brief or ""), limit=90)
        if text:
            previews.append(text)
    if not previews:
        return ""
    summary = "; ".join(previews)
    if len(briefs) > limit:
        summary += f"; +{len(briefs) - limit} more"
    return summary


def _tool_request_summary(tool_id: str, arguments: dict[str, Any]) -> str:
    if tool_id == "file_write":
        mode = str(arguments.get("mode") or "overwrite")
        path = str(arguments.get("path") or "(missing path)")
        content = str(arguments.get("content") or "")
        return f"{mode} {path} ({len(content.encode('utf-8'))} bytes)"
    if tool_id == "shell_command":
        return _truncate_text(str(arguments.get("command") or "(missing command)"))
    if tool_id in {"git_status", "git_diff", "git_log"}:
        path = str(arguments.get("path") or ".")
        return f"path={path}"
    if "path" in arguments:
        return _truncate_text(str(arguments.get("path") or ""))
    try:
        return _truncate_text(json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str))
    except Exception:
        return _truncate_text(str(arguments))


def _tool_result_summary(tool_id: str, payload: dict[str, Any]) -> str:
    if not payload.get("ok"):
        return str(payload.get("error") or "tool failed")
    result = payload.get("result")
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
            size = result.get("size")
            line_count = result.get("line_count")
            path = result.get("path") or "(unknown path)"
            pieces: list[str] = []
            if line_count is not None:
                pieces.append(f"lines={line_count}")
            if size is not None:
                pieces.append(f"bytes={size}")
            prefix = " ".join(pieces) if pieces else "read file"
            return f"{prefix} path={path}"
        if tool_id == "file_write":
            return (
                f"wrote {result.get('bytes_written', '?')} bytes to "
                f"{result.get('path', '(unknown path)')}"
            )
        if tool_id == "shell_command":
            stdout = str(result.get("stdout") or "")
            stderr = str(result.get("stderr") or "")
            return (
                f"exit={result.get('exit_code', '?')} "
                f"stdout={len(stdout)} chars stderr={len(stderr)} chars"
            )
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
        if tool_id == "git_log":
            commits = result.get("commits") or []
            return f"commits={len(commits)}"
        if "path" in result:
            return _truncate_text(str(result.get("path") or ""))
        try:
            return _truncate_text(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
        except Exception:
            return _truncate_text(str(result))
    return _truncate_text(str(result))


class CodeToolApprovalState:
    """Interactive approval gate for risky local tool calls."""

    def __init__(self, *, mode: str, interactive: bool) -> None:
        self.mode = str(mode or "auto")
        self._interactive = bool(interactive)
        self._always_allow_tool_ids: set[str] = set()

    def _requires_confirmation(self, tool_id: str) -> bool:
        if self.mode == "auto":
            return False
        if self.mode == "confirm-all":
            return True
        return tool_id in RISKY_TOOL_IDS

    def __call__(self, tool_id: str, arguments: dict[str, Any], metadata: dict[str, Any]) -> bool:
        if not self._requires_confirmation(tool_id):
            return True
        if tool_id in self._always_allow_tool_ids:
            return True
        summary = _tool_request_summary(tool_id, arguments)
        if not self._interactive or not sys.stdin.isatty():
            print(
                f"approval required for {tool_id}: {summary}. "
                "Re-run with --approval-mode auto to allow non-interactively.",
                file=sys.stderr,
            )
            return False
        category = str(metadata.get("category") or "tool")
        print(f"approval required: {tool_id} [{category}]")
        print(f"  {summary}")
        while True:
            choice = input("allow? [y]es/[n]o/[a]lways-for-this-tool: ").strip().lower()
            if choice in {"y", "yes"}:
                return True
            if choice in {"n", "no", ""}:
                return False
            if choice in {"a", "always"}:
                self._always_allow_tool_ids.add(tool_id)
                return True
            print("enter y, n, or a")


class CodeProgressRenderer:
    """Render concise live progress for the coding CLI."""

    def __init__(self, *, enabled: bool, show_model_trace: bool = False) -> None:
        self._enabled = bool(enabled)
        self._show_model_trace = bool(show_model_trace)
        self._replace_cache: dict[tuple[str, str, str], str] = {}

    @staticmethod
    def _print_assistant(message: str) -> None:
        text = str(message or "").strip()
        if text:
            print(f"[assistant] {text}")

    @staticmethod
    def _scope_label(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if "." in text:
            return text.split(".", 1)[1]
        return text

    def _stream_prefix(self, channel: str, event: dict[str, Any]) -> str:
        scope = self._scope_label(event.get("worker_id"))
        if scope:
            return f"[{scope}][{channel}]"
        return f"[{channel}]"

    @staticmethod
    def _format_score(value: Any) -> str | None:
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _attempt_stage_label(attempt: Any, stage: Any) -> str:
        return f"attempt {attempt} / {str(stage or 'stage').strip()}"

    @staticmethod
    def _assistant_subject(event: dict[str, Any]) -> str:
        parts: list[str] = []
        attempt = event.get("attempt")
        if attempt not in {None, ""}:
            parts.append(f"attempt {attempt}")
        actor = str(event.get("actor") or "").strip()
        phase = str(event.get("phase") or "").strip()
        if actor:
            parts.append(actor)
        if phase and phase != actor:
            parts.append(phase)
        return " / ".join(parts)

    def _render_status_update(self, event: dict[str, Any]) -> None:
        message = _truncate_text(str(event.get("message") or ""), limit=240)
        if not message:
            return
        subject = self._assistant_subject(event)
        extras: list[str] = []
        worker_count = event.get("worker_count")
        if worker_count is not None and str(event.get("phase") or "").strip() == "orchestration":
            extras.append(f"workers={worker_count}")
        candidate_id = str(event.get("candidate_id") or "").strip()
        if candidate_id:
            extras.append(f"candidate={candidate_id}")
        score = self._format_score(event.get("score"))
        if score is not None:
            extras.append(f"score={score}")
        target_files = [str(item).strip() for item in (event.get("target_files") or []) if str(item).strip()]
        if target_files:
            extras.append(f"files={len(target_files)}")
        test_plan = [str(item).strip() for item in (event.get("test_plan") or []) if str(item).strip()]
        if test_plan:
            extras.append(f"validation={len(test_plan)}")
        missing_requirements = [
            str(item).strip()
            for item in (event.get("missing_requirements") or [])
            if str(item).strip()
        ]
        if missing_requirements:
            extras.append(f"gaps={len(missing_requirements)}")
        suffix = f" ({', '.join(extras)})" if extras else ""
        rendered = f"{subject}: {message}{suffix}" if subject else f"{message}{suffix}"
        if event.get("replace_last"):
            cache_key = (
                str(event.get("attempt") or ""),
                str(event.get("actor") or ""),
                str(event.get("phase") or ""),
            )
            if self._replace_cache.get(cache_key) == rendered:
                return
            self._replace_cache[cache_key] = rendered
        self._print_assistant(rendered)

    def __call__(self, event: dict[str, Any]) -> None:
        if not self._enabled:
            return
        name = str(event.get("event") or "")
        if name == "organism.started":
            objective = _truncate_text(str(event.get("objective") or ""), limit=160)
            print(f"[status] starting coding run: {objective}")
            return
        if name == "assistant.message":
            self._print_assistant(str(event.get("message") or ""))
            return
        if name == "attempt.started":
            attempt = event.get("attempt", "?")
            reason = str(event.get("reason") or "attempt")
            if attempt not in {1, "1"}:
                print()
            if reason == "repair":
                brief = _truncate_text(str(event.get("repair_brief") or ""), limit=160)
                if brief:
                    print(f"[status] attempt {attempt}: retrying from validator feedback: {brief}")
                else:
                    print(f"[status] attempt {attempt}: retrying from validator feedback")
            else:
                print(f"[status] attempt {attempt}: starting")
            return
        if name == "status.update":
            self._render_status_update(event)
            return
        if name == "stage.started":
            attempt = event.get("attempt", "?")
            stage = str(event.get("stage") or "stage")
            extras: list[str] = []
            if event.get("worker_count") is not None:
                extras.append(f"workers={event.get('worker_count')}")
            suffix = f" ({', '.join(extras)})" if extras else ""
            print(f"[status] {self._attempt_stage_label(attempt, stage)}: running{suffix}")
            return
        if name == "stage.completed":
            attempt = event.get("attempt", "?")
            stage = str(event.get("stage") or "stage")
            status = str(event.get("status") or "").strip()
            extras: list[str] = []
            if event.get("worker_count") is not None and stage in {"orchestration", "workers"}:
                extras.append(f"workers={event.get('worker_count')}")
            successful_workers = [
                str(item).strip()
                for item in (event.get("successful_workers") or [])
                if str(item).strip()
            ]
            if successful_workers and stage == "workers":
                extras.append(f"successful={len(successful_workers)}")
            if event.get("candidate_id"):
                extras.append(f"candidate={event.get('candidate_id')}")
            score = self._format_score(event.get("score"))
            if score is not None:
                extras.append(f"score={score}")
            if event.get("passed") is not None:
                extras.append(f"passed={'yes' if event.get('passed') else 'no'}")
            missing_requirements = [
                str(item).strip()
                for item in (event.get("missing_requirements") or [])
                if str(item).strip()
            ]
            if missing_requirements and stage == "validation" and not event.get("passed"):
                extras.append(f"gaps={len(missing_requirements)}")
            suffix = f" ({', '.join(extras)})" if extras else ""
            print(f"[status] {self._attempt_stage_label(attempt, stage)}: {status or 'completed'}{suffix}")
            return
        if name == "repair.requested":
            attempt = event.get("attempt", "?")
            details: list[str] = []
            score = self._format_score(event.get("score"))
            if score is not None:
                details.append(f"score={score}")
            missing = [
                str(item).strip()
                for item in (event.get("missing_requirements") or [])
                if str(item).strip()
            ]
            if missing:
                details.append(f"missing={len(missing)}")
            prefix = f"[status] validator requested repair after attempt {attempt}"
            if details:
                prefix += f" ({', '.join(details)})"
            print(prefix)
            return
        if name == "organism.completed":
            status = str(event.get("status") or "completed")
            attempt = event.get("selected_attempt")
            candidate_id = str(event.get("candidate_id") or "").strip()
            if status == "completed":
                suffix = []
                if attempt is not None:
                    suffix.append(f"attempt={attempt}")
                if candidate_id:
                    suffix.append(f"candidate={candidate_id}")
                summary = f" ({', '.join(suffix)})" if suffix else ""
                print(f"[status] coding run completed{summary}")
            else:
                error = _truncate_text(str(event.get("error") or "coding run failed"), limit=180)
                print(f"[status] coding run failed: {error}")
            return
        if name == "model.requested":
            if not self._show_model_trace:
                return
            prefix = self._stream_prefix("model", event)
            print(
                f"{prefix} request: "
                f"round={event.get('round', '?')} "
                f"model={event.get('model') or '(unknown)'} "
                f"tools={event.get('tool_count', 0)}"
            )
            return
        if name == "model.responded":
            if not self._show_model_trace:
                return
            prefix = self._stream_prefix("model", event)
            tool_calls = [
                str(tool_id).strip()
                for tool_id in (event.get("tool_calls") or [])
                if str(tool_id).strip()
            ]
            summary = (
                f"{prefix} response: "
                f"round={event.get('round', '?')} "
                f"model={event.get('model') or '(unknown)'}"
            )
            if tool_calls:
                summary += f" tool_calls={', '.join(tool_calls)}"
            finish_reason = str(event.get("finish_reason") or "").strip()
            if finish_reason:
                summary += f" finish_reason={finish_reason}"
            print(summary)
            text = _truncate_text(str(event.get("text") or ""), limit=240)
            if text:
                print(f"{prefix} preview: {text}")
            return
        if name == "tool.started":
            tool_id = str(event.get("tool_id") or "tool")
            arguments = dict(event.get("arguments") or {})
            prefix = self._stream_prefix("tool", event)
            print(f"{prefix} {tool_id}: {_tool_request_summary(tool_id, arguments)}")
            return
        if name == "tool.completed":
            tool_id = str(event.get("tool_id") or "tool")
            payload = {"ok": True, "result": event.get("result")}
            prefix = self._stream_prefix("tool", event)
            print(f"{prefix} ok {tool_id}: {_tool_result_summary(tool_id, payload)}")
            return
        if name in {"tool.failed", "tool.denied"}:
            tool_id = str(event.get("tool_id") or "tool")
            message = str(event.get("error") or "denied")
            prefix = self._stream_prefix("tool", event)
            print(f"{prefix} {name.split('.')[-1]} {tool_id}: {message}")
            return
        if name == "completion.completed":
            stop_reason = str(event.get("stop_reason") or "completed")
            if stop_reason != "completed":
                prefix = self._stream_prefix("model", event)
                print(f"{prefix} stop_reason={stop_reason}")


def _effective_live_tool_ids(
    explicit: Sequence[str],
    *,
    product_config: CodeProductConfig | None = None,
) -> list[str]:
    if any(str(value or "").strip() for value in explicit):
        return _dedupe(explicit)
    if product_config and product_config.default_tool_ids:
        return _dedupe(product_config.default_tool_ids)
    return list(DEFAULT_LIVE_ORGANISM_TOOL_IDS)


def _effective_acceptance_criteria(
    explicit: Sequence[str],
    *,
    product_config: CodeProductConfig | None = None,
) -> list[str]:
    if any(str(value or "").strip() for value in explicit):
        return _non_empty(explicit, DEFAULT_CODING_ACCEPTANCE_CRITERIA)
    if product_config and product_config.acceptance_criteria:
        return _non_empty(product_config.acceptance_criteria, DEFAULT_CODING_ACCEPTANCE_CRITERIA)
    return list(DEFAULT_CODING_ACCEPTANCE_CRITERIA)


def _effective_max_tool_rounds(
    explicit: int | None,
    *,
    product_config: CodeProductConfig | None = None,
) -> int:
    if explicit is not None:
        return int(explicit)
    if product_config:
        return int(product_config.max_tool_rounds)
    return 8


def _effective_max_tool_calls(
    explicit: int | None,
    *,
    product_config: CodeProductConfig | None = None,
) -> int:
    if explicit is not None:
        return int(explicit)
    if product_config:
        return int(product_config.max_tool_calls)
    return 24


def _print_tool_catalog(*, as_json: bool) -> None:
    catalog = available_local_organism_tools()
    if as_json:
        print(
            json.dumps(
                [
                    {
                        "tool_id": tool_id,
                        "description": str(metadata.get("description") or ""),
                        "category": str(metadata.get("category") or ""),
                    }
                    for tool_id, metadata in sorted(catalog.items())
                ],
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    for tool_id, metadata in sorted(catalog.items()):
        category = str(metadata.get("category") or "other")
        description = str(metadata.get("description") or "").strip()
        print(f"{tool_id} [{category}]")
        if description:
            print(f"  {description}")


def _resolve_live_model(
    requested_model: str | None,
    *,
    product_config: CodeProductConfig | None = None,
) -> str:
    text = str(requested_model or "").strip()
    if text:
        return text
    if product_config:
        configured = str(product_config.default_model or "").strip()
        if configured:
            return configured
    config = resolve_config()
    env_model = str(config.get("model") or "").strip()
    if env_model:
        return env_model
    engine_config = build_engine_config_from_env()
    fallback = str(engine_config.llm_default_model or "").strip()
    if fallback and fallback != "stub-model":
        return fallback
    raise ValueError("DAN Code requires --model, a .dan-code default_model, or a configured DAN_MODEL/DAN_LLM_MODEL")


def _maybe_resolve_live_model(
    requested_model: str | None,
    *,
    product_config: CodeProductConfig | None = None,
) -> str | None:
    try:
        return _resolve_live_model(requested_model, product_config=product_config)
    except Exception:
        return None


def _build_live_provider(model: str, *, api_key: str | None, base_url: str | None) -> LLMProvider:
    engine_config = build_engine_config_from_env()
    if api_key:
        engine_config.llm_api_key = api_key
    if base_url:
        engine_config.llm_base_url = base_url
    registry = build_provider_registry(engine_config)
    return registry.resolve(model)


def _build_evidence_refs(workdir: Path, evidence_summaries: Sequence[str]) -> list[EvidenceRef]:
    refs: list[EvidenceRef] = []
    workdir.mkdir(parents=True, exist_ok=True)
    for index, summary in enumerate(evidence_summaries, start=1):
        text = str(summary or "").strip()
        if not text:
            continue
        locator = workdir / f"evidence-{index}.md"
        locator.write_text(f"# evidence-{index}\n\n{text}\n", encoding="utf-8")
        refs.append(
            EvidenceRef(
                ref_id=f"coding-brief:evidence-{index}",
                label=f"Evidence {index}",
                summary=text,
                source="coding-cli",
                locator=str(locator),
            )
        )
    return refs


async def run_coding_organism_live(
    workdir: Path,
    *,
    llm_provider: LLMProvider,
    objective: str,
    model: str,
    task_id: str = "coding-organ-task",
    organ_id: str = "coding-build",
    organism_id: str = "coding-organism",
    acceptance_criteria: Sequence[str] | None = None,
    research_findings: Sequence[str] | None = None,
    repair_brief: str = "",
    evidence_summaries: Sequence[str] | None = None,
    tool_ids: Sequence[str] | None = None,
    workspace_root: str | Path | None = None,
    max_tool_rounds: int = 8,
    max_tool_calls: int = 24,
    thinking_mode: str = "auto",
    approval_callback=None,
    event_callback=None,
    session_context: dict[str, Any] | None = None,
) -> CodingOrganismReport:
    tool_runtime = LocalOrganismToolRuntime(
        tool_ids=tool_ids or DEFAULT_LIVE_ORGANISM_TOOL_IDS,
        workspace_root=workspace_root,
        approval_callback=approval_callback,
        event_callback=event_callback,
    )
    completion_provider = ToolLoopCompletionProvider(
        provider=llm_provider,
        tool_runtime=tool_runtime,
        default_model=model,
        max_rounds=max_tool_rounds,
        max_tool_calls=max_tool_calls,
        provider_request_overrides=_provider_request_overrides_for_thinking_mode(
            thinking_mode
        ),
        event_callback=event_callback,
    )
    executor = WorkerCoreExecutor(completion_provider=completion_provider)
    organism = attach_local_tooling_to_coding_organism(
        coding_execution_organism(
            organism_id=organism_id,
            model=model,
            base_id=organ_id,
        ),
        tool_ids=tool_runtime.tool_ids,
    )
    evidence_refs = _build_evidence_refs(workdir, evidence_summaries or [])
    task = CodingTask(
        task_id=task_id,
        objective=objective,
        acceptance_criteria=acceptance_criteria or DEFAULT_CODING_ACCEPTANCE_CRITERIA,
        research_findings=list(research_findings or []),
        repair_brief=repair_brief,
        evidence_refs=[ref.model_copy(deep=True) for ref in evidence_refs],
        session_context=dict(session_context or {}),
    )
    trace_log = CrossCellTraceLog()
    execution = await execute_coding_organism(
        executor=executor,
        organism=organism,
        task=task,
        trace_log=trace_log,
        event_callback=event_callback,
    )
    assert execution.result is not None
    trace_rows = list(execution.result.observability.trace_rows)
    outputs = dict(execution.result.final_output)
    return CodingOrganismReport(
        status=str(execution.result.status),
        trace_id=str(execution.result.observability.trace_id),
        organism_id=organism_id,
        organ_id=organ_id,
        task_id=task_id,
        objective=objective,
        candidate_id=(
            str(outputs.get("candidate_id")).strip()
            if outputs.get("candidate_id") is not None
            else None
        ),
        change_summary=str(outputs.get("change_summary") or ""),
        target_files=list(outputs.get("target_files") or []),
        test_plan=list(outputs.get("test_plan") or []),
        risks=list(outputs.get("risks") or []),
        outputs=outputs,
        handoff_count=sum(1 for row in trace_rows if row.get("kind") == "handoff"),
        signal_count=sum(1 for row in trace_rows if row.get("kind") == "signal"),
        error=str(execution.result.error) if execution.result.error else None,
        trace_rows=trace_rows,
    )


def _print_report(report: dict[str, object]) -> None:
    print(f"Status: {report['status']}")
    print(f"Run ID: {report['trace_id']}")
    print(f"Task ID: {report['task_id']}")
    print(f"Objective: {report['objective']}")
    if report.get("candidate_id") is not None:
        print(f"Candidate: {report['candidate_id']}")
    elif str(report.get("status") or "").strip() != "completed":
        print("Candidate: (no validated candidate)")
    summary = str(report.get("change_summary") or "").strip()
    if summary:
        print(f"Summary: {summary}")
    print(f"Activity: {report.get('handoff_count')} handoffs, {report.get('signal_count')} signals")
    target_files = [str(item).strip() for item in (report.get("target_files") or []) if str(item).strip()]
    if target_files:
        print("Files:")
        for item in target_files[:8]:
            print(f"- {item}")
        if len(target_files) > 8:
            print(f"- (+{len(target_files) - 8} more)")
    else:
        print("Files: (none)")
    test_plan = report.get("test_plan") or []
    if test_plan:
        print("Validation:")
        for item in test_plan:
            print(f"- {item}")
    risks = report.get("risks") or []
    if risks:
        print("Risks:")
        for item in risks:
            print(f"- {item}")
    error = str(report.get("error") or "").strip()
    if error:
        print(f"Error: {error}")
    raw_outputs = dict(report.get("outputs") or {})
    for key in ("candidate_id", "change_summary", "target_files", "test_plan", "risks"):
        raw_outputs.pop(key, None)
    if raw_outputs:
        print("\nRaw Output:")
        print(json.dumps(raw_outputs, indent=2, ensure_ascii=False, sort_keys=True))


def _build_run_workdir(run_root: Path, *, turn_number: int) -> Path:
    return run_root / f"turn-{turn_number:02d}"


def _provider_request_overrides_for_thinking_mode(
    thinking_mode: str,
) -> dict[str, Any]:
    normalized = str(thinking_mode or "auto").strip().lower()
    if normalized == "auto":
        return {}
    return {"thinking": {"type": normalized}}


class CodeConversationOutcome(BaseModel):
    """Rendered outcome of one orchestrated DAN Code turn."""

    status: str
    assistant_messages: list[str] = Field(default_factory=list)
    question: str | None = None
    reports: list[CodingOrganismReport] = Field(default_factory=list)


def _assistant_text(message: str) -> str:
    return " ".join(str(message or "").strip().split())


def _report_context(report: CodingOrganismReport) -> CodingConversationReportSummary:
    return CodingConversationReportSummary(
        status=report.status,
        task_id=report.task_id,
        objective=report.objective,
        candidate_id=report.candidate_id,
        change_summary=report.change_summary,
        target_files=list(report.target_files),
        test_plan=list(report.test_plan),
        risks=list(report.risks),
        error=report.error,
    )


def _report_finding(report: CodingOrganismReport) -> str:
    target_files = ", ".join(report.target_files) or "(none)"
    test_plan = "; ".join(report.test_plan) or "(none)"
    return (
        "Current bounded coding pass: "
        f"objective={report.objective}; "
        f"candidate={report.candidate_id or '(none)'}; "
        f"status={report.status}; "
        f"files={target_files}; "
        f"tests={test_plan}"
    )


def _conversation_context(
    *,
    session: CodingCliSession,
    workspace_root: Path,
    model: str,
    thinking_mode: str,
    tool_ids: Sequence[str],
    acceptance_criteria: Sequence[str],
    additional_reports: Sequence[CodingOrganismReport] | None = None,
) -> CodingConversationContext:
    recent_reports = [
        _report_context(report)
        for report in [*session.turns[-4:], *(additional_reports or [])]
    ]
    return CodingConversationContext(
        workspace_root=str(workspace_root),
        model=model,
        thinking_mode=thinking_mode,
        tool_ids=list(tool_ids),
        acceptance_criteria=list(acceptance_criteria),
        pending_clarification=session.pending_clarification,
        recent_conversation=[
            CodingConversationMessage(
                role=entry.role,
                text=entry.text,
                kind=entry.kind,
            )
            for entry in session.conversation[-8:]
        ],
        recent_reports=recent_reports[-6:],
    )


def _load_orchestrator_session(
    *,
    session: CodingCliSession,
    controller: CodingConversationController,
):
    try:
        return controller.load_session(
            dict(session.orchestrator_state.get("durable_session") or {})
        )
    except Exception:
        return None


def _store_orchestrator_session(
    *,
    session: CodingCliSession,
    controller: CodingConversationController,
    durable_session,
) -> None:
    session.orchestrator_state = {
        **dict(session.orchestrator_state),
        "durable_session": controller.dump_session(durable_session),
    }


def _emit_assistant_message(
    progress_renderer: CodeProgressRenderer,
    message: str,
) -> None:
    text = _assistant_text(message)
    if not text:
        return
    progress_renderer({"event": "assistant.message", "message": text})


async def _run_orchestrated_turn(
    *,
    args,
    controller: CodingConversationController,
    llm_provider: LLMProvider,
    workspace_root: Path,
    model: str,
    objective: str,
    session: CodingCliSession,
    tool_ids: Sequence[str],
    acceptance_criteria: Sequence[str],
    max_tool_rounds: int,
    max_tool_calls: int,
    approval_mode: str,
    thinking_mode: str,
    run_root: Path,
    approval_callback=None,
    progress_renderer: CodeProgressRenderer,
) -> CodeConversationOutcome:
    assistant_messages: list[str] = []
    reports: list[CodingOrganismReport] = []
    question: str | None = None
    session.record_message(role="user", text=objective)

    orchestrator_session = _load_orchestrator_session(
        session=session,
        controller=controller,
    )
    decision, orchestrator_session = await controller.decide_user_turn(
        session=orchestrator_session,
        user_message=objective,
        pending_clarification=session.pending_clarification,
        context=_conversation_context(
            session=session,
            workspace_root=workspace_root,
            model=model,
            thinking_mode=thinking_mode,
            tool_ids=tool_ids,
            acceptance_criteria=acceptance_criteria,
        ),
    )
    _store_orchestrator_session(
        session=session,
        controller=controller,
        durable_session=orchestrator_session,
    )

    def _record_assistant(text: str, *, kind: str = "message") -> None:
        cleaned = _assistant_text(text)
        if not cleaned:
            return
        assistant_messages.append(cleaned)
        session.record_message(role="assistant", text=cleaned, kind=kind)
        _emit_assistant_message(progress_renderer, cleaned)

    if decision.public_response:
        _record_assistant(decision.public_response)

    if decision.action == "respond":
        session.pending_clarification = None
        return CodeConversationOutcome(
            status="responded",
            assistant_messages=assistant_messages,
            question=None,
            reports=[],
        )

    if decision.action == "clarify":
        question = _assistant_text(decision.clarifying_question or decision.public_response)
        session.pending_clarification = question or None
        if question and question not in assistant_messages:
            _record_assistant(question, kind="clarification")
        return CodeConversationOutcome(
            status="clarify",
            assistant_messages=assistant_messages,
            question=question,
            reports=[],
        )

    session.pending_clarification = None
    next_objective = _assistant_text(decision.coding_objective or objective)
    next_repair_brief = _assistant_text(decision.repair_brief)
    next_research_findings = _dedupe(
        [*session.carry_forward_findings(), *list(decision.research_findings)]
    )
    effective_acceptance_criteria = _dedupe(
        [*list(acceptance_criteria), *list(decision.acceptance_criteria)]
    ) or list(acceptance_criteria)
    max_supervision_loops = 2
    base_turn_number = session.next_turn_number()

    for continuation_index in range(max_supervision_loops):
        report_turn_number = base_turn_number + len(reports)
        report = await _run_coding_turn(
            args=args,
            llm_provider=llm_provider,
            workspace_root=workspace_root,
            model=model,
            objective=next_objective,
            task_id=f"{args.task_id}:{report_turn_number}",
            research_findings=next_research_findings,
            evidence_summaries=[],
            workdir=_build_run_workdir(run_root, turn_number=report_turn_number),
            acceptance_criteria=effective_acceptance_criteria,
            tool_ids=tool_ids,
            max_tool_rounds=max_tool_rounds,
            max_tool_calls=max_tool_calls,
            approval_mode=approval_mode,
            thinking_mode=thinking_mode,
            repair_brief=next_repair_brief,
            approval_callback=approval_callback,
            event_callback=progress_renderer,
        )
        reports.append(report)

        review, orchestrator_session = await controller.review_coding_result(
            session=orchestrator_session,
            objective=next_objective,
            report_summary=_report_context(report),
            context=_conversation_context(
                session=session,
                workspace_root=workspace_root,
                model=model,
                thinking_mode=thinking_mode,
                tool_ids=tool_ids,
                acceptance_criteria=effective_acceptance_criteria,
                additional_reports=reports,
            ),
        )
        _store_orchestrator_session(
            session=session,
            controller=controller,
            durable_session=orchestrator_session,
        )
        if review.public_response:
            _record_assistant(review.public_response)

        if review.action == "continue" and continuation_index + 1 < max_supervision_loops:
            next_objective = _assistant_text(review.next_objective or next_objective)
            next_repair_brief = _assistant_text(review.repair_brief or report.error or next_repair_brief)
            next_research_findings = _dedupe(
                [*next_research_findings, _report_finding(report), *list(review.research_findings)]
            )
            continue

        if review.action == "clarify":
            question = _assistant_text(review.clarifying_question or review.public_response)
            session.pending_clarification = question or None
            if question and question not in assistant_messages:
                _record_assistant(question, kind="clarification")
            return CodeConversationOutcome(
                status="clarify",
                assistant_messages=assistant_messages,
                question=question,
                reports=reports,
            )
        break

    final_status = reports[-1].status if reports else "responded"
    return CodeConversationOutcome(
        status=final_status,
        assistant_messages=assistant_messages,
        question=question,
        reports=reports,
    )


async def _run_coding_turn(
    *,
    args,
    llm_provider: LLMProvider,
    workspace_root: Path,
    model: str,
    objective: str,
    task_id: str,
    research_findings: Sequence[str],
    evidence_summaries: Sequence[str],
    workdir: Path,
    acceptance_criteria: Sequence[str],
    tool_ids: Sequence[str],
    max_tool_rounds: int,
    max_tool_calls: int,
    approval_mode: str,
    thinking_mode: str,
    repair_brief: str,
    approval_callback=None,
    event_callback=None,
) -> CodingOrganismReport:
    session_context = _build_runtime_context(
        workspace_root=workspace_root,
        model=model,
        tool_ids=tool_ids,
        approval_mode=approval_mode,
        thinking_mode=thinking_mode,
        task_id=task_id,
    )
    result = await run_coding_organism_live(
        workdir,
        llm_provider=llm_provider,
        objective=objective,
        model=model,
        task_id=task_id,
        organ_id=str(args.organ_id),
        organism_id=str(args.organism_id),
        acceptance_criteria=acceptance_criteria,
        research_findings=_dedupe(list(args.research_findings) + list(research_findings)),
        repair_brief=str(repair_brief),
        evidence_summaries=_dedupe(list(args.evidence_summaries) + list(evidence_summaries)),
        tool_ids=tool_ids,
        workspace_root=workspace_root,
        max_tool_rounds=max_tool_rounds,
        max_tool_calls=max_tool_calls,
        thinking_mode=thinking_mode,
        approval_callback=approval_callback,
        event_callback=event_callback,
        session_context=session_context,
    )
    if isinstance(result, CodingOrganismReport):
        return result
    if hasattr(result, "model_dump"):
        return CodingOrganismReport.model_validate(result.model_dump(mode="json"))
    if isinstance(result, dict):
        return CodingOrganismReport.model_validate(result)
    raise TypeError(f"Unsupported coding report result: {type(result).__name__}")


def _print_repl_help() -> None:
    print("Commands:")
    print("/help    Show this help")
    print("/tools   List available local tools")
    print("/status  Show workspace/session status")
    print("/history Show recent coding turns")
    print("/summary Show session-level file/test summary")
    print("/reset   Clear carried-forward conversation and coding context")
    print("/clear   Alias for /reset")
    print("/exit    Exit the coding CLI")
    print()
    print("Notes:")
    print("- explicit workspace/result questions are answered locally")
    print("- concrete coding requests launch the coding organism")


def _print_session_status(
    *,
    session: CodingCliSession,
    workspace_root: Path,
    product_paths: CodeProductPaths,
    model: str,
    thinking_mode: str,
    tool_ids: Sequence[str],
    persist_session: bool,
    approval_mode: str,
) -> None:
    print(f"product: {CODE_PRODUCT_NAME}")
    print(f"workspace: {workspace_root}")
    print(f"session: {session.session_id}")
    print(f"turns: {len(session.turns)}")
    print(f"conversation messages: {len(session.conversation)}")
    print(f"model: {model}")
    print(f"thinking mode: {thinking_mode}")
    print(f"tools: {', '.join(tool_ids) or '(none)'}")
    print(f"approval mode: {approval_mode}")
    if session.pending_clarification:
        print(f"pending clarification: {session.pending_clarification}")
    print(f"session persistence: {'enabled' if persist_session else 'disabled'}")
    if persist_session:
        print(f"session file: {product_paths.session}")
        print(f"transcript: {product_paths.transcript}")


def _print_session_history(session: CodingCliSession, *, limit: int = 10) -> None:
    if not session.turns:
        print("No saved coding turns.")
        return
    print("Recent turns:")
    start_index = max(0, len(session.turns) - limit)
    for offset, report in enumerate(session.turns[start_index:], start=start_index + 1):
        print(
            f"{offset}. [{report.status}] {report.objective} "
            f"(files: {', '.join(report.target_files) or '(none)'})"
        )


def _print_session_rollup(session: CodingCliSession) -> None:
    if not session.turns:
        print("Session summary: no completed turns yet.")
        return
    files: list[str] = []
    tests: list[str] = []
    for report in session.turns:
        files.extend(report.target_files)
        tests.extend(report.test_plan)
    unique_files = _dedupe(files)
    unique_tests = _dedupe(tests)
    print(
        "Session summary: "
        f"{len(session.turns)} turns, "
        f"{len(unique_files)} files, "
        f"{len(unique_tests)} validation commands"
    )
    if unique_files:
        print(f"Files: {', '.join(unique_files[:8])}")
    if len(unique_files) > 8:
        print(f"More files: +{len(unique_files) - 8}")
    if unique_tests:
        print("Validation:")
        for item in unique_tests[:5]:
            print(f"- {item}")
        if len(unique_tests) > 5:
            print(f"- (+{len(unique_tests) - 5} more)")


def _render_report(
    report: CodingOrganismReport,
    *,
    as_json: bool,
    output_path: str | None,
    workspace_root: Path,
) -> None:
    if output_path:
        destination = _resolve_user_path(output_path, base_dir=workspace_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    if as_json:
        print(report.model_dump_json(indent=2))
    else:
        _print_report(report.model_dump(mode="json"))


def _load_or_create_session(
    *,
    product_paths: CodeProductPaths,
    workspace_root: Path,
    new_session: bool,
    persist_session: bool,
) -> CodingCliSession:
    if persist_session and not new_session:
        loaded = load_code_product_session(product_paths)
        if loaded is not None:
            if not loaded.workspace_root:
                loaded.workspace_root = str(workspace_root)
            return loaded
    return CodingCliSession(workspace_root=str(workspace_root))


def _persist_session(
    *,
    product_paths: CodeProductPaths,
    session: CodingCliSession,
    report: CodingOrganismReport,
) -> None:
    save_code_product_session(product_paths, session)
    append_code_product_transcript(product_paths, session=session, report=report)


def _initialize_product_config(
    *,
    product_paths: CodeProductPaths,
    workspace_root: Path,
    product_config: CodeProductConfig | None,
    args,
) -> CodeProductConfig:
    config = CodeProductConfig(
        workspace_root=str(workspace_root),
        default_model=_maybe_resolve_live_model(args.model, product_config=product_config),
        thinking_mode=_effective_thinking_mode(
            args.thinking_mode,
            product_config=product_config,
        ),
        default_tool_ids=_effective_live_tool_ids(args.tool_ids, product_config=product_config),
        acceptance_criteria=_effective_acceptance_criteria(
            args.acceptance_criteria,
            product_config=product_config,
        ),
        max_tool_rounds=_effective_max_tool_rounds(
            args.max_tool_rounds,
            product_config=product_config,
        ),
        max_tool_calls=_effective_max_tool_calls(
            args.max_tool_calls,
            product_config=product_config,
        ),
    )
    write_code_product_config(product_paths, config)
    return config


def _resolved_config_payload(
    *,
    workspace_root: Path,
    product_paths: CodeProductPaths,
    product_config: CodeProductConfig | None,
    session: CodingCliSession | None,
    requested_model: str | None,
    tool_ids: Sequence[str],
    acceptance_criteria: Sequence[str],
    max_tool_rounds: int,
    max_tool_calls: int,
    thinking_mode: str,
    persist_session: bool,
    approval_mode: str,
) -> dict[str, Any]:
    return {
        "product_name": CODE_PRODUCT_NAME,
        "workspace_root": str(workspace_root),
        "product_dir": product_paths.root,
        "config_path": product_paths.config,
        "session_path": product_paths.session,
        "transcript_path": product_paths.transcript,
        "config_exists": Path(product_paths.config).exists(),
        "session_exists": Path(product_paths.session).exists(),
        "persist_session": persist_session,
        "configured_default_model": (
            str(product_config.default_model).strip()
            if product_config and product_config.default_model is not None
            else None
        ),
        "configured_thinking_mode": (
            str(product_config.thinking_mode).strip()
            if product_config is not None
            else None
        ),
        "resolved_model": _maybe_resolve_live_model(requested_model, product_config=product_config),
        "resolved_thinking_mode": thinking_mode,
        "tool_ids": list(tool_ids),
        "acceptance_criteria": list(acceptance_criteria),
        "max_tool_rounds": int(max_tool_rounds),
        "max_tool_calls": int(max_tool_calls),
        "approval_mode": approval_mode,
        "saved_turns": len(session.turns) if session is not None else 0,
    }


def _print_config_payload(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
        return
    print(f"product: {payload['product_name']}")
    print(f"workspace: {payload['workspace_root']}")
    print(f"product dir: {payload['product_dir']}")
    print(f"config path: {payload['config_path']}")
    print(f"session path: {payload['session_path']}")
    print(f"resolved model: {payload.get('resolved_model') or '(unset)'}")
    print(f"thinking mode: {payload.get('resolved_thinking_mode') or 'auto'}")
    print(f"tools: {', '.join(payload['tool_ids']) or '(none)'}")
    print(f"approval mode: {payload['approval_mode']}")
    print(f"saved turns: {payload['saved_turns']}")
    print(f"session persistence: {'enabled' if payload['persist_session'] else 'disabled'}")


def _interactive_loop(
    *,
    args,
    controller: CodingConversationController,
    llm_provider: LLMProvider,
    workspace_root: Path,
    model: str,
    thinking_mode: str,
    session: CodingCliSession,
    product_paths: CodeProductPaths,
    tool_ids: Sequence[str],
    acceptance_criteria: Sequence[str],
    max_tool_rounds: int,
    max_tool_calls: int,
    persist_session: bool,
    run_root: Path,
    approval_state: CodeToolApprovalState,
    approval_mode: str,
    progress_renderer: CodeProgressRenderer,
) -> int:
    print(CODE_PRODUCT_NAME)
    print(f"workspace: {workspace_root}")
    print(f"session: {session.session_id}")
    if session.turns:
        print(f"resumed: {len(session.turns)} prior turns")
    print("enter a coding task, or /help")
    while True:
        try:
            raw = input("dancode> ")
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            return 0

        objective = str(raw or "").strip()
        if not objective:
            continue
        if objective in {"/exit", "exit", "quit", ":q"}:
            return 0
        if objective in {"/help", "help"}:
            _print_repl_help()
            continue
        if objective == "/tools":
            _print_tool_catalog(as_json=False)
            continue
        if objective == "/status":
            _print_session_status(
                session=session,
                workspace_root=workspace_root,
                product_paths=product_paths,
                model=model,
                thinking_mode=thinking_mode,
                tool_ids=tool_ids,
                persist_session=persist_session,
                approval_mode=approval_mode,
            )
            continue
        if objective == "/history":
            _print_session_history(session)
            continue
        if objective == "/summary":
            _print_session_rollup(session)
            continue
        if objective in {"/reset", "/clear"}:
            session = CodingCliSession(workspace_root=str(workspace_root))
            if persist_session:
                save_code_product_session(product_paths, session)
            print("session context cleared")
            continue
        if _handle_local_prompt(
            objective,
            workspace_root=workspace_root,
            session=session,
            trailing_blank_line=True,
        ):
            continue

        outcome = asyncio.run(
            _run_orchestrated_turn(
                args=args,
                controller=controller,
                llm_provider=llm_provider,
                workspace_root=workspace_root,
                model=model,
                objective=objective,
                session=session,
                tool_ids=tool_ids,
                acceptance_criteria=acceptance_criteria,
                max_tool_rounds=max_tool_rounds,
                max_tool_calls=max_tool_calls,
                approval_mode=approval_mode,
                thinking_mode=thinking_mode,
                run_root=run_root,
                approval_callback=approval_state,
                progress_renderer=progress_renderer,
            )
        )
        if outcome.reports:
            for report in outcome.reports:
                session.record_turn(report)
                if persist_session:
                    _persist_session(product_paths=product_paths, session=session, report=report)
            _render_report(
                outcome.reports[-1],
                as_json=bool(args.json),
                output_path=args.output,
                workspace_root=workspace_root,
            )
        elif persist_session:
            save_code_product_session(product_paths, session)
        if not args.json and outcome.reports:
            _print_session_rollup(session)
            print()


def main(argv: Sequence[str] | None = None) -> int:
    load_env()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.list_tools:
        _print_tool_catalog(as_json=bool(args.json))
        return 0

    resolved_config = resolve_config(workspace=args.workspace)
    workspace_root = normalize_workspace_root(str(resolved_config["workspace"]))
    os.environ["DAN_WORKSPACE_ROOT"] = str(workspace_root)

    product_paths = resolve_code_product_paths(
        workspace_root,
        session_file=args.session_file,
    )
    product_config = load_code_product_config(product_paths)
    persist_session = not bool(args.no_session_persist)
    session = _load_or_create_session(
        product_paths=product_paths,
        workspace_root=workspace_root,
        new_session=bool(args.new_session),
        persist_session=persist_session,
    )

    tool_ids = _effective_live_tool_ids(args.tool_ids, product_config=product_config)
    acceptance_criteria = _effective_acceptance_criteria(
        args.acceptance_criteria,
        product_config=product_config,
    )
    max_tool_rounds = _effective_max_tool_rounds(
        args.max_tool_rounds,
        product_config=product_config,
    )
    max_tool_calls = _effective_max_tool_calls(
        args.max_tool_calls,
        product_config=product_config,
    )
    try:
        thinking_mode = _effective_thinking_mode(
            args.thinking_mode,
            product_config=product_config,
        )
    except ValueError as exc:
        parser.error(str(exc))
    approval_mode = _effective_approval_mode(
        args.approval_mode,
        interactive=args.objective is None,
    )

    if args.init:
        config = _initialize_product_config(
            product_paths=product_paths,
            workspace_root=workspace_root,
            product_config=product_config,
            args=args,
        )
        payload = {
            **_resolved_config_payload(
                workspace_root=workspace_root,
                product_paths=product_paths,
                product_config=config,
                session=session,
                requested_model=args.model,
                tool_ids=config.default_tool_ids,
                acceptance_criteria=config.acceptance_criteria,
                max_tool_rounds=config.max_tool_rounds,
                max_tool_calls=config.max_tool_calls,
                thinking_mode=config.thinking_mode,
                persist_session=persist_session,
                approval_mode=approval_mode,
            ),
            "initialized": True,
        }
        _print_config_payload(payload, as_json=bool(args.json))
        return 0

    if args.show_config:
        payload = _resolved_config_payload(
            workspace_root=workspace_root,
            product_paths=product_paths,
            product_config=product_config,
            session=session,
            requested_model=args.model,
            tool_ids=tool_ids,
            acceptance_criteria=acceptance_criteria,
            max_tool_rounds=max_tool_rounds,
            max_tool_calls=max_tool_calls,
            thinking_mode=thinking_mode,
            persist_session=persist_session,
            approval_mode=approval_mode,
        )
        _print_config_payload(payload, as_json=bool(args.json))
        return 0

    if args.objective is not None and _handle_local_prompt(
        str(args.objective),
        workspace_root=workspace_root,
        session=session,
        trailing_blank_line=False,
    ):
        return 0

    try:
        live_model = _resolve_live_model(args.model, product_config=product_config)
        provider = _build_live_provider(
            live_model,
            api_key=args.api_key,
            base_url=args.base_url,
        )
    except Exception as exc:
        parser.error(str(exc))

    run_root = (
        _resolve_user_path(args.workdir, base_dir=workspace_root)
        if args.workdir
        else Path(product_paths.runs_dir)
    )
    approval_state = CodeToolApprovalState(
        mode=approval_mode,
        interactive=sys.stdin.isatty(),
    )
    progress_renderer = CodeProgressRenderer(
        enabled=not bool(args.json) and not bool(args.quiet_progress),
        show_model_trace=bool(args.show_model_trace),
    )
    conversation_controller = CodingConversationController(
        provider=provider,
        model=live_model,
        provider_request_overrides=_provider_request_overrides_for_thinking_mode(
            thinking_mode
        ),
        event_callback=progress_renderer,
    )

    if args.objective is None:
        return _interactive_loop(
            args=args,
            controller=conversation_controller,
            llm_provider=provider,
            workspace_root=workspace_root,
            model=live_model,
            thinking_mode=thinking_mode,
            session=session,
            product_paths=product_paths,
            tool_ids=tool_ids,
            acceptance_criteria=acceptance_criteria,
            max_tool_rounds=max_tool_rounds,
            max_tool_calls=max_tool_calls,
            persist_session=persist_session,
            run_root=run_root,
            approval_state=approval_state,
            approval_mode=approval_mode,
            progress_renderer=progress_renderer,
        )

    outcome = asyncio.run(
        _run_orchestrated_turn(
            args=args,
            controller=conversation_controller,
            llm_provider=provider,
            workspace_root=workspace_root,
            model=live_model,
            objective=str(args.objective),
            session=session,
            tool_ids=tool_ids,
            acceptance_criteria=acceptance_criteria,
            max_tool_rounds=max_tool_rounds,
            max_tool_calls=max_tool_calls,
            approval_mode=approval_mode,
            thinking_mode=thinking_mode,
            run_root=run_root,
            approval_callback=approval_state,
            progress_renderer=progress_renderer,
        )
    )
    for report in outcome.reports:
        session.record_turn(report)
        if persist_session:
            _persist_session(product_paths=product_paths, session=session, report=report)
    if persist_session and not outcome.reports:
        save_code_product_session(product_paths, session)

    if outcome.reports:
        _render_report(
            outcome.reports[-1],
            as_json=bool(args.json),
            output_path=args.output,
            workspace_root=workspace_root,
        )
        return 0 if outcome.reports[-1].status == "completed" else 1

    payload = outcome.model_dump(mode="json")
    if args.output:
        destination = _resolve_user_path(args.output, base_dir=workspace_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    elif not outcome.assistant_messages and outcome.question:
        print(f"[assistant] {outcome.question}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
