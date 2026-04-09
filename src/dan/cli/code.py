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
from dan.worker.organisms import CodingTask, coding_execution_organism, execute_coding_organism
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
NON_TASK_MESSAGES = frozenset(
    {
        "hi",
        "hello",
        "hey",
        "yo",
        "sup",
        "help",
        "thanks",
        "thank you",
    }
)
NON_TASK_QUESTIONS = frozenset(
    {
        "what can you do",
        "who are you",
        "what do you do",
        "can we chat",
        "how should i use this",
        "how do i use this",
        "how does this work",
        "are you like claude code",
        "is this like claude code",
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


def _is_non_task_message(text: str) -> bool:
    normalized = _normalize_message(text)
    if not normalized:
        return True
    if normalized in NON_TASK_MESSAGES:
        return True
    return normalized in NON_TASK_QUESTIONS


def _non_task_guidance() -> str:
    return (
        "I’m the coding shell for this workspace. I can inspect the repo, explain code, "
        "patch files, run focused validation, and summarize recent coding turns.\n"
        "Try one of these:\n"
        "- summarize the CLI wiring under src/dan/cli\n"
        "- inspect the failing tests and patch the smallest viable fix\n"
        "- explain how dan code is built on the universal worker\n"
        "Use /help to see CLI commands."
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
    if _is_non_task_message(text):
        print(_non_task_guidance())
    elif _is_workspace_query(text):
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

    @staticmethod
    def _print_assistant(message: str) -> None:
        text = str(message or "").strip()
        if text:
            print(f"[assistant] {text}")

    def __call__(self, event: dict[str, Any]) -> None:
        if not self._enabled:
            return
        name = str(event.get("event") or "")
        if name == "organism.started":
            objective = _truncate_text(str(event.get("objective") or ""), limit=160)
            print(f"[status] starting coding run: {objective}")
            self._print_assistant("I’m reviewing the request and planning the first coding attempt.")
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
        if name == "stage.started":
            attempt = event.get("attempt", "?")
            message = _truncate_text(str(event.get("message") or ""), limit=160)
            stage = str(event.get("stage") or "stage")
            if message:
                self._print_assistant(f"attempt {attempt} / {stage}: {message}")
            else:
                self._print_assistant(f"attempt {attempt} / {stage}")
            return
        if name == "stage.completed":
            attempt = event.get("attempt", "?")
            stage = str(event.get("stage") or "stage")
            status = str(event.get("status") or "").strip()
            message = _truncate_text(str(event.get("message") or ""), limit=160)
            extras: list[str] = []
            if event.get("worker_count") is not None and stage in {"orchestration", "workers"}:
                extras.append(f"workers={event.get('worker_count')}")
            if event.get("candidate_id"):
                extras.append(f"candidate={event.get('candidate_id')}")
            if event.get("score") is not None:
                try:
                    extras.append(f"score={float(event.get('score')):.2f}")
                except (TypeError, ValueError):
                    pass
            if event.get("passed") is not None:
                extras.append(f"passed={'yes' if event.get('passed') else 'no'}")
            suffix = f" ({', '.join(extras)})" if extras else ""
            if message:
                self._print_assistant(f"attempt {attempt} / {stage} {status}: {message}{suffix}")
            else:
                self._print_assistant(f"attempt {attempt} / {stage} {status}{suffix}")
            if stage == "orchestration":
                brief_summary = _summarize_briefs(event.get("worker_briefs") or [])
                if brief_summary:
                    self._print_assistant(f"attempt {attempt} plan: {brief_summary}")
            elif stage == "workers":
                workers = _summarize_items(event.get("successful_workers") or [])
                if workers:
                    self._print_assistant(f"attempt {attempt} worker replies: {workers}")
            elif stage == "aggregation":
                candidate_id = str(event.get("candidate_id") or "").strip()
                files = _summarize_items(event.get("target_files") or [])
                validation_count = len(event.get("test_plan") or [])
                parts: list[str] = []
                if candidate_id:
                    parts.append(f"candidate {candidate_id}")
                if files:
                    parts.append(f"targets {files}")
                if validation_count:
                    parts.append(f"validation commands={validation_count}")
                if parts:
                    self._print_assistant("aggregated " + "; ".join(parts) + ".")
            elif stage == "validation":
                missing = _summarize_items(event.get("missing_requirements") or [], limit=4)
                if event.get("passed"):
                    self._print_assistant("validator accepted the current candidate.")
                elif missing:
                    self._print_assistant(f"attempt {attempt} gaps: {missing}")
            return
        if name == "repair.requested":
            attempt = event.get("attempt", "?")
            score = event.get("score")
            brief = _truncate_text(str(event.get("repair_brief") or ""), limit=180)
            missing = [str(item).strip() for item in (event.get("missing_requirements") or []) if str(item).strip()]
            prefix = f"[status] validator requested repair after attempt {attempt}"
            if score is not None:
                try:
                    prefix += f" (score={float(score):.2f})"
                except (TypeError, ValueError):
                    pass
            print(prefix)
            if brief:
                print(f"[status] repair brief: {brief}")
                self._print_assistant("I’m starting a repair-focused retry from that validator brief.")
            if missing:
                print(f"[status] missing: {', '.join(missing[:4])}")
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
                self._print_assistant("The final report below lists the selected candidate, files, validation, and risks.")
            else:
                error = _truncate_text(str(event.get("error") or "coding run failed"), limit=180)
                print(f"[status] coding run failed: {error}")
                self._print_assistant("The run stopped without a validated candidate. Review the failure details below.")
            return
        if name == "model.requested":
            if not self._show_model_trace:
                return
            print(
                "[model] request: "
                f"round={event.get('round', '?')} "
                f"model={event.get('model') or '(unknown)'} "
                f"tools={event.get('tool_count', 0)}"
            )
            return
        if name == "model.responded":
            if not self._show_model_trace:
                return
            tool_calls = [
                str(tool_id).strip()
                for tool_id in (event.get("tool_calls") or [])
                if str(tool_id).strip()
            ]
            summary = (
                "[model] response: "
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
                print(f"[model] preview: {text}")
            return
        if name == "tool.started":
            tool_id = str(event.get("tool_id") or "tool")
            arguments = dict(event.get("arguments") or {})
            print(f"[tool] {tool_id}: {_tool_request_summary(tool_id, arguments)}")
            return
        if name == "tool.completed":
            tool_id = str(event.get("tool_id") or "tool")
            payload = {"ok": True, "result": event.get("result")}
            print(f"[tool] ok {tool_id}: {_tool_result_summary(tool_id, payload)}")
            return
        if name in {"tool.failed", "tool.denied"}:
            tool_id = str(event.get("tool_id") or "tool")
            message = str(event.get("error") or "denied")
            print(f"[tool] {name.split('.')[-1]} {tool_id}: {message}")
            return
        if name == "completion.completed":
            stop_reason = str(event.get("stop_reason") or "completed")
            if stop_reason != "completed":
                print(f"[model] stop_reason={stop_reason}")


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
    return await run_coding_organism_live(
        workdir,
        llm_provider=llm_provider,
        objective=objective,
        model=model,
        task_id=task_id,
        organ_id=str(args.organ_id),
        organism_id=str(args.organism_id),
        acceptance_criteria=acceptance_criteria,
        research_findings=_dedupe(list(args.research_findings) + list(research_findings)),
        repair_brief=str(args.repair_brief),
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


def _print_repl_help() -> None:
    print("Commands:")
    print("/help    Show this help")
    print("/tools   List available local tools")
    print("/status  Show workspace/session status")
    print("/history Show recent coding turns")
    print("/summary Show session-level file/test summary")
    print("/reset   Clear carried-forward session context")
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
    print(f"model: {model}")
    print(f"thinking mode: {thinking_mode}")
    print(f"tools: {', '.join(tool_ids) or '(none)'}")
    print(f"approval mode: {approval_mode}")
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
        if objective == "/reset":
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

        turn_number = session.next_turn_number()
        report = asyncio.run(
            _run_coding_turn(
                args=args,
                llm_provider=llm_provider,
                workspace_root=workspace_root,
                model=model,
                objective=objective,
                task_id=session.task_id_for(str(args.task_id)),
                research_findings=session.carry_forward_findings(),
                evidence_summaries=[],
                workdir=_build_run_workdir(run_root, turn_number=turn_number),
                acceptance_criteria=acceptance_criteria,
                tool_ids=tool_ids,
                max_tool_rounds=max_tool_rounds,
                max_tool_calls=max_tool_calls,
                approval_mode=approval_mode,
                thinking_mode=thinking_mode,
                approval_callback=approval_state,
                event_callback=progress_renderer,
            )
        )
        session.record_turn(report)
        if persist_session:
            _persist_session(product_paths=product_paths, session=session, report=report)
        _render_report(
            report,
            as_json=bool(args.json),
            output_path=args.output,
            workspace_root=workspace_root,
        )
        if not args.json:
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

    if args.objective is None:
        return _interactive_loop(
            args=args,
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

    report = asyncio.run(
        _run_coding_turn(
            args=args,
            llm_provider=provider,
            workspace_root=workspace_root,
            model=live_model,
            objective=str(args.objective),
            task_id=session.task_id_for(str(args.task_id)),
            research_findings=session.carry_forward_findings(),
            evidence_summaries=[],
            workdir=_build_run_workdir(run_root, turn_number=session.next_turn_number()),
            acceptance_criteria=acceptance_criteria,
            tool_ids=tool_ids,
            max_tool_rounds=max_tool_rounds,
            max_tool_calls=max_tool_calls,
            approval_mode=approval_mode,
            thinking_mode=thinking_mode,
            approval_callback=approval_state,
            event_callback=progress_renderer,
        )
    )
    session.record_turn(report)
    if persist_session:
        _persist_session(product_paths=product_paths, session=session, report=report)
    _render_report(
        report,
        as_json=bool(args.json),
        output_path=args.output,
        workspace_root=workspace_root,
    )

    return 0 if report.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
