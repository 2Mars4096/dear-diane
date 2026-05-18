"""dan-code — local coding product CLI built on the universal worker."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Sequence

from pydantic import BaseModel, Field, field_validator

from dan.cli import load_env, normalize_workspace_root, resolve_config
from dan.cli import live_gateway
from dan.cli.task_lanes import classify_code_task_lane
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
from dan.server.runtime_config import build_engine_config_from_env
from dan.worker.composition import CrossCellTraceLog
from dan.worker.core.executor import WorkerCoreExecutor
from dan.worker.core.interfaces import CallbackEventSink
from dan.worker.organism_log import (
    ORGANISM_LOG_SCHEMA_VERSION,
    OrganismLogContext,
    OrganismLogWriter,
    new_trace_id,
)
from dan.worker.organisms import (
    CodingConversationFacts,
    CodingConversationContext,
    CodingConversationController,
    CodingConversationMessage,
    CodingProjectPlan,
    CodingProjectPlannerContext,
    CodingProjectPlannerController,
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
from dan.worker.organisms.coding_conversation import (
    _fallback_project_planner_decision,
    _fallback_review_decision,
)
from dan.worker.signaling import EvidenceRef
from dan.tools.git_diff import git_diff as git_diff_tool

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
        "file_edit",
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

_SWEBENCH_DEFAULT_ACCEPTANCE_CRITERIA = [
    "Resolve the benchmark issue in the current checked-out repository with the smallest correct patch you can justify.",
    "Avoid unrelated refactors or cleanup outside the benchmark issue scope.",
    "Match the checked-out branch's public contract exactly when the fix affects warnings, exceptions, messages, identifiers, deprecation behavior, or visible side effects.",
    "Preserve surrounding compatibility behavior in the checked-out branch while fixing the target issue.",
    "Treat issue examples and reproduction snippets as illustrative rather than exhaustive; inspect nearby tests, helpers, parametrizations, and symmetric code paths so the final patch covers the checked-out branch's full contract.",
    "Use benchmark tests for validation, but keep the final prediction patch focused on product/source changes rather than adding or editing benchmark test files.",
    "Leave the resulting workspace diff intact so it can be exported as a SWE-bench prediction artifact.",
    "If a local shell or host-Python reproduction exposes unrelated environment drift, do not patch that drift; continue focusing on the benchmark issue itself.",
    "Converge quickly once the smallest correct patch is in place; do not spend extra turns on optional validation, broader cleanup, or environment/package repair unless they directly block confirming the target contract.",
]

_DEFAULT_SUPERVISION_LOOPS = 2
_BENCHMARK_SUPERVISION_LOOPS = 4


def _stringify_context_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    try:
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        rendered = str(value)
    cleaned = rendered.strip()
    return cleaned or None


def _normalize_context_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return []
        try:
            parsed = json.loads(cleaned)
        except Exception:
            return [cleaned]
        value = parsed
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = [value]
    normalized: list[str] = []
    for item in items:
        cleaned = _stringify_context_value(item)
        if cleaned:
            normalized.append(cleaned)
    return normalized


def _markdown_bullets(
    title: str,
    items: Sequence[str],
    *,
    limit: int = 50,
) -> str | None:
    normalized = [str(item).strip() for item in items if str(item).strip()]
    if not normalized:
        return None
    return f"{title}\n\n- " + "\n- ".join(normalized[:limit])


class SweBenchInstance(BaseModel):
    """Minimal SWE-bench-compatible instance record."""

    instance_id: str
    problem_statement: str
    repo: str | None = None
    base_commit: str | None = None
    requirements: str | None = None
    interface: str | None = None
    before_repo_set_cmd: str | None = None
    selected_test_files_to_run: list[str] = Field(default_factory=list)
    fail_to_pass: list[str] = Field(default_factory=list)
    pass_to_pass: list[str] = Field(default_factory=list)

    @field_validator(
        "instance_id",
        "problem_statement",
        "repo",
        "base_commit",
        "requirements",
        "interface",
        "before_repo_set_cmd",
        mode="before",
    )
    @classmethod
    def _normalize_text_fields(cls, value: Any) -> str | None:
        return _stringify_context_value(value)

    @field_validator(
        "selected_test_files_to_run",
        "fail_to_pass",
        "pass_to_pass",
        mode="before",
    )
    @classmethod
    def _normalize_list_fields(cls, value: Any) -> list[str]:
        return _normalize_context_list(value)


class SweBenchRunContext(BaseModel):
    """Resolved benchmark context for one DAN Code run."""

    instance: SweBenchInstance
    objective: str
    task_id_base: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    evidence_summaries: list[str] = Field(default_factory=list)
    benchmark_context: dict[str, Any] = Field(default_factory=dict)
    instance_file: str
    predictions_path: str | None = None


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
        help="Optional maximum provider tool rounds per worker completion. Unset or <= 0 leaves it unbounded.",
    )
    parser.add_argument(
        "--max-tool-calls",
        type=int,
        default=None,
        help="Maximum provider tool calls per worker completion.",
    )
    parser.add_argument(
        "--completion-timeout-seconds",
        type=float,
        default=None,
        help=(
            "Maximum wall-clock seconds for one provider completion before DAN Code "
            "fails that completion boundedly. Falls back to workspace product config, "
            "DAN_CODE_COMPLETION_TIMEOUT_SECONDS, then 90 seconds. Set <= 0 to disable."
        ),
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
    parser.add_argument(
        "--swebench-instance-file",
        help=(
            "Optional SWE-bench-compatible instance JSON/JSONL file. "
            "If provided without a free-form objective, DAN Code synthesizes the "
            "benchmark objective from the instance."
        ),
    )
    parser.add_argument(
        "--swebench-predictions-path",
        help=(
            "Optional JSONL file to append scorer-compatible SWE-bench predictions "
            "(`instance_id`, `model_name_or_path`, `model_patch`)."
        ),
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


def _load_swebench_instance(
    value: str | Path,
    *,
    base_dir: Path,
) -> SweBenchInstance:
    path = _resolve_user_path(value, base_dir=base_dir)
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        rows = [line.strip() for line in raw.splitlines() if line.strip()]
        if len(rows) != 1:
            raise ValueError(
                f"SWE-bench instance file {path} must contain exactly one JSONL row"
            )
        payload = json.loads(rows[0])
    else:
        payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(
            f"SWE-bench instance file {path} must decode to a JSON object"
        )
    try:
        instance = SweBenchInstance.model_validate(payload)
    except Exception as exc:
        raise ValueError(
            f"Invalid SWE-bench instance file {path}: {exc}"
        ) from exc
    if not instance.instance_id or not instance.problem_statement:
        raise ValueError(
            f"SWE-bench instance file {path} is missing required fields "
            "`instance_id` and/or `problem_statement`"
        )
    return instance


def _swebench_objective(
    instance: SweBenchInstance,
    *,
    operator_objective: str | None,
) -> str:
    preface = _assistant_text(operator_objective or "")
    if not preface:
        preface = (
            f"Resolve SWE-bench instance {instance.instance_id} in the current "
            "checked-out repository and produce the smallest correct patch."
        )
    sections = [preface]
    if instance.repo:
        sections.append(f"Repository: {instance.repo}")
    if instance.base_commit:
        sections.append(f"Benchmark base commit: {instance.base_commit}")
    sections.append(f"Instance ID: {instance.instance_id}")
    sections.append(f"Issue:\n{instance.problem_statement}")
    if instance.requirements:
        sections.append(f"Benchmark requirements:\n{instance.requirements}")
    if instance.interface:
        sections.append(f"Relevant interface/context:\n{instance.interface}")
    if instance.selected_test_files_to_run:
        sections.append(
            "Selected tests:\n- "
            + "\n- ".join(instance.selected_test_files_to_run[:20])
        )
    return "\n\n".join(section for section in sections if section.strip())


def _build_swebench_context(
    instance: SweBenchInstance,
    *,
    instance_file: Path,
    predictions_path: Path | None,
    operator_objective: str | None,
    task_id_base: str,
) -> SweBenchRunContext:
    fail_to_pass = list(instance.fail_to_pass)
    pass_to_pass = list(instance.pass_to_pass)
    evidence_summaries = [
        "\n".join(
            line
            for line in (
                "# SWE-bench instance metadata",
                f"- instance_id: {instance.instance_id}",
                f"- repo: {instance.repo or '(unknown)'}",
                f"- base_commit: {instance.base_commit or '(unknown)'}",
                f"- instance_file: {instance_file}",
            )
            if line
        ),
        f"# SWE-bench problem statement\n\n{instance.problem_statement}",
    ]
    if instance.requirements:
        evidence_summaries.append(
            f"# SWE-bench benchmark requirements\n\n{instance.requirements}"
        )
    if instance.interface:
        evidence_summaries.append(
            f"# SWE-bench interface/context notes\n\n{instance.interface}"
        )
    if instance.selected_test_files_to_run:
        evidence_summaries.append(
            "# SWE-bench selected tests\n\n- "
            + "\n- ".join(instance.selected_test_files_to_run[:50])
        )
    fail_to_pass_summary = _markdown_bullets(
        "# SWE-bench FAIL_TO_PASS tests",
        fail_to_pass,
    )
    if fail_to_pass_summary is not None:
        evidence_summaries.append(fail_to_pass_summary)
    pass_to_pass_summary = _markdown_bullets(
        "# SWE-bench PASS_TO_PASS tests",
        pass_to_pass,
    )
    if pass_to_pass_summary is not None:
        evidence_summaries.append(pass_to_pass_summary)
    if instance.before_repo_set_cmd:
        evidence_summaries.append(
            f"# SWE-bench setup/reset command\n\n{instance.before_repo_set_cmd}"
        )
    acceptance_criteria = list(_SWEBENCH_DEFAULT_ACCEPTANCE_CRITERIA)
    if fail_to_pass:
        acceptance_criteria.append(
            "Make the benchmark's FAIL_TO_PASS coverage pass; treat it as the exact target contract."
        )
    if pass_to_pass:
        acceptance_criteria.append(
            "Keep the benchmark's PASS_TO_PASS coverage green while fixing the target behavior."
        )
    return SweBenchRunContext(
        instance=instance,
        objective=_swebench_objective(
            instance,
            operator_objective=operator_objective,
        ),
        task_id_base=task_id_base,
        acceptance_criteria=acceptance_criteria,
        evidence_summaries=evidence_summaries,
        benchmark_context={
            "benchmark_name": "SWE-bench",
            "instance_id": instance.instance_id,
            "repo": instance.repo,
            "base_commit": instance.base_commit,
            "selected_test_files_to_run": list(instance.selected_test_files_to_run),
            "fail_to_pass": fail_to_pass,
            "pass_to_pass": pass_to_pass,
        },
        instance_file=str(instance_file),
        predictions_path=str(predictions_path) if predictions_path is not None else None,
    )


def _append_jsonl_record(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def _normalize_patch_path(value: str) -> str:
    return str(value or "").strip().replace("\\", "/").lstrip("./")


def _swebench_prediction_excluded_paths(instance: SweBenchInstance) -> list[str]:
    excluded: list[str] = []
    for value in (
        *list(instance.selected_test_files_to_run),
        *list(instance.fail_to_pass),
        *list(instance.pass_to_pass),
    ):
        path = _normalize_patch_path(str(value).split("::", 1)[0])
        if path:
            excluded.append(path)
    return sorted(set(excluded))


def _diff_section_path(header: str) -> str | None:
    parts = str(header or "").strip().split()
    if len(parts) < 4 or parts[0] != "diff" or parts[1] != "--git":
        return None
    for raw_path in (parts[3], parts[2]):
        path = _normalize_patch_path(raw_path)
        if path.startswith("b/") or path.startswith("a/"):
            path = path[2:]
        if path:
            return path
    return None


def _filter_diff_excluding_paths(
    patch_text: str,
    excluded_paths: Sequence[str],
) -> str:
    excluded = {_normalize_patch_path(path) for path in excluded_paths if path}
    if not excluded:
        return patch_text

    kept: list[str] = []
    current: list[str] = []

    def _flush_current() -> None:
        if not current:
            return
        header_path = _diff_section_path(current[0])
        if header_path not in excluded:
            kept.extend(current)
        current.clear()

    for line in patch_text.splitlines(keepends=True):
        if line.startswith("diff --git "):
            _flush_current()
        current.append(line)
    _flush_current()
    return "".join(kept)


def _capture_workspace_patch_text(
    workspace_root: Path,
    *,
    excluded_paths: Sequence[str] | None = None,
) -> str:
    result = asyncio.run(git_diff_tool(path=str(workspace_root)))
    patch_text = str(result.get("diff_text") or "")
    return _filter_diff_excluding_paths(patch_text, excluded_paths or [])


def _write_swebench_artifacts(
    report: CodingOrganismReport,
    *,
    swebench: SweBenchRunContext,
    workspace_root: Path,
    model: str,
) -> None:
    artifact_dir = (
        Path(report.event_log_path).expanduser().resolve().parent
        if report.event_log_path
        else workspace_root
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    instance_artifact_path = artifact_dir / "swebench-instance.json"
    patch_artifact_path = artifact_dir / "swebench.patch"
    prediction_artifact_path = artifact_dir / "swebench-prediction.json"
    excluded_patch_paths = _swebench_prediction_excluded_paths(swebench.instance)
    patch_text = _capture_workspace_patch_text(
        workspace_root,
        excluded_paths=excluded_patch_paths,
    )
    prediction_payload = {
        "instance_id": swebench.instance.instance_id,
        "model_name_or_path": model,
        "model_patch": patch_text,
    }
    instance_artifact_path.write_text(
        swebench.instance.model_dump_json(indent=2),
        encoding="utf-8",
    )
    patch_artifact_path.write_text(patch_text, encoding="utf-8")
    prediction_artifact_path.write_text(
        json.dumps(prediction_payload, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    if swebench.predictions_path:
        _append_jsonl_record(Path(swebench.predictions_path), prediction_payload)
    swebench_output = dict(report.outputs.get("swebench") or {})
    swebench_output.update(
        {
            "instance_id": swebench.instance.instance_id,
            "repo": swebench.instance.repo,
            "base_commit": swebench.instance.base_commit,
            "instance_file": swebench.instance_file,
            "instance_artifact_path": str(instance_artifact_path),
            "patch_artifact_path": str(patch_artifact_path),
            "prediction_artifact_path": str(prediction_artifact_path),
            "predictions_path": swebench.predictions_path,
            "model_name_or_path": model,
            "patch_bytes": len(patch_text.encode("utf-8")),
            "excluded_patch_paths": excluded_patch_paths,
        }
    )
    report.outputs["swebench"] = swebench_output


def _now_context() -> dict[str, str]:
    now = datetime.now().astimezone()
    return {
        "current_timestamp": now.isoformat(),
        "current_date": now.date().isoformat(),
        "timezone": str(now.tzinfo or ""),
    }


def _conversation_facts(
    *,
    session: CodingCliSession,
    workspace_root: Path,
    model: str,
    thinking_mode: str,
    tool_ids: Sequence[str],
    approval_mode: str,
    benchmark_context: dict[str, Any] | None = None,
    additional_reports: Sequence[CodingOrganismReport] | None = None,
) -> CodingConversationFacts:
    latest_report = (
        list(additional_reports or [])[-1]
        if additional_reports
        else (session.turns[-1] if session.turns else None)
    )
    now_context = _now_context()
    shell_process_directory = str(
        Path(os.environ.get("PWD") or str(workspace_root)).expanduser()
    )
    return CodingConversationFacts(
        product_name=CODE_PRODUCT_NAME,
        workspace_root=str(workspace_root),
        effective_working_directory=str(workspace_root),
        shell_process_directory=shell_process_directory,
        session_id=session.session_id,
        active_model=str(model or "").strip(),
        thinking_mode=str(thinking_mode or "").strip(),
        approval_mode=str(approval_mode or "").strip(),
        enabled_tools=list(tool_ids),
        coding_turn_count=len(session.turns) + len(list(additional_reports or [])),
        conversation_message_count=len(session.conversation),
        pending_clarification=session.pending_clarification,
        latest_report_status=str(getattr(latest_report, "status", "") or ""),
        latest_report_objective=str(getattr(latest_report, "objective", "") or ""),
        latest_report_target_files=list(getattr(latest_report, "target_files", []) or []),
        latest_report_error=getattr(latest_report, "error", None),
        benchmark_mode=bool(benchmark_context),
        current_timestamp=now_context["current_timestamp"],
        current_date=now_context["current_date"],
        timezone=now_context["timezone"],
    )


def _build_runtime_context(
    *,
    workspace_root: Path,
    model: str,
    tool_ids: Sequence[str],
    approval_mode: str,
    thinking_mode: str,
    task_id: str,
    completion_timeout_seconds: float | None = None,
    benchmark_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    shell_process_directory = str(
        Path(os.environ.get("PWD") or str(workspace_root)).expanduser()
    )
    now_context = _now_context()
    payload = {
        "product_name": CODE_PRODUCT_NAME,
        "workspace_root": str(workspace_root),
        "current_working_directory": str(workspace_root),
        "shell_process_directory": shell_process_directory,
        "model": model,
        "task_id": task_id,
        "tool_ids": list(tool_ids),
        "approval_mode": approval_mode,
        "thinking_mode": thinking_mode,
        **now_context,
    }
    if completion_timeout_seconds is not None:
        payload["completion_timeout_seconds"] = float(completion_timeout_seconds)
        payload["short_completion_timeout"] = _short_completion_timeout_active(
            completion_timeout_seconds
        )
    if benchmark_context:
        payload["benchmark_context"] = dict(benchmark_context)
    return payload


def _benchmark_task_guidance(
    benchmark_context: dict[str, Any] | None,
) -> tuple[list[str], list[str], list[str]]:
    if not isinstance(benchmark_context, dict) or not benchmark_context:
        return [], [], []

    benchmark_name = str(
        benchmark_context.get("benchmark_name") or "Benchmark"
    ).strip()
    fail_to_pass = _normalize_context_list(benchmark_context.get("fail_to_pass"))
    pass_to_pass = _normalize_context_list(benchmark_context.get("pass_to_pass"))
    selected_test_files = _normalize_context_list(
        benchmark_context.get("selected_test_files_to_run")
    )

    research_findings = [
        (
            f"{benchmark_name} tasks are graded against branch-specific tests and "
            "compatibility expectations; inspect nearby tests and existing "
            "implementation patterns before finalizing public-contract details."
        ),
        (
            "Issue examples are often narrower than the real checked-out contract; "
            "read nearby tests, helpers, and parametrizations, and inspect symmetric "
            "paths such as read/write or parser/serializer pairs before finalizing "
            "the patch."
        ),
        (
            f"For {benchmark_name} speed, stop once the minimal source patch satisfies "
            "the target contract; extra cleanup, wider exploration, or local "
            "environment surgery usually hurts convergence."
        ),
    ]
    fail_to_pass_summary = _markdown_bullets(
        "These FAIL_TO_PASS tests define the exact target behavior change:",
        fail_to_pass,
        limit=25,
    )
    if fail_to_pass_summary is not None:
        research_findings.append(fail_to_pass_summary)
    pass_to_pass_summary = _markdown_bullets(
        "These PASS_TO_PASS tests protect surrounding behavior and compatibility:",
        pass_to_pass,
        limit=25,
    )
    if pass_to_pass_summary is not None:
        research_findings.append(pass_to_pass_summary)
    selected_tests_summary = _markdown_bullets(
        "These selected test files are high-signal sources for the local contract and compatibility style:",
        selected_test_files,
        limit=25,
    )
    if selected_tests_summary is not None:
        research_findings.append(selected_tests_summary)
    if fail_to_pass or pass_to_pass:
        research_findings.append(
            "If a listed benchmark test id is not present in the local checkout, treat it as harness-supplied contract evidence and infer the intended behavior from the issue plus nearby existing tests rather than adding that missing test to the repo."
        )

    hard_constraints = [
        "Do not invent new warning/error identifiers, messages, stdout side effects, or compatibility behavior unless nearby tests or existing branch conventions justify them.",
        "Do not replace a warning or deprecation path with an immediate hard failure unless the checked-out branch already expects that stricter behavior.",
        "Do not silently absorb or delete conflicting state when the intended contract requires a visible warning, audit message, or migration notice.",
        "Do not stop at one literal repro string when nearby tests or helper code imply equivalent variants belong to the same contract (for example case variants, sibling grammar tokens, or symmetric read/write behavior).",
        "Do not add or edit benchmark test files as part of the final prediction patch; use them only to understand and validate the required behavior.",
        "Do not add missing benchmark test ids or parametrizations to the repo just because the instance metadata names them; assume the benchmark harness may supply them separately.",
        "Do not spend benchmark turns on package install/downgrade, interpreter rebuild, or broad environment repair unless that step is directly required by the checked-out repo to validate the target contract.",
    ]
    if fail_to_pass:
        hard_constraints.append(
            "Treat the listed FAIL_TO_PASS tests as the exact target contract, not merely a hint."
        )
    if pass_to_pass:
        hard_constraints.append(
            "Keep the listed PASS_TO_PASS behavior green while fixing the target contract."
        )

    soft_constraints = [
        "Prefer the smallest compatibility-preserving fix that reuses nearby implementation and test conventions.",
        "For edge cases, preserve input/output shape and public API semantics, not just non-crashing behavior.",
        "When touching parsers, serializers, or format adapters, inspect neighboring helpers and round-trip tests so the minimal fix generalizes across equivalent tokens and I/O directions.",
        "If the minimal fix is already present in the workspace, inspect and validate that state instead of re-planning or re-applying the same edit.",
    ]
    return research_findings, hard_constraints, soft_constraints


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
    if tool_id == "file_edit":
        mode = str(arguments.get("mode") or "replace")
        path = str(arguments.get("path") or arguments.get("file_path") or "(missing path)")
        start_line = arguments.get("start_line")
        end_line = arguments.get("end_line", start_line)
        return f"{mode} {path}:{start_line}-{end_line}"
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
            line_count = result.get("returned_line_count", result.get("line_count"))
            total_line_count = result.get("total_line_count")
            path = result.get("path") or "(unknown path)"
            pieces: list[str] = []
            if line_count is not None and total_line_count is not None and total_line_count != line_count:
                pieces.append(f"lines={line_count}/{total_line_count}")
            elif line_count is not None:
                pieces.append(f"lines={line_count}")
            if size is not None:
                file_size = result.get("file_size")
                if file_size is not None and file_size != size:
                    pieces.append(f"bytes={size}/{file_size}")
                else:
                    pieces.append(f"bytes={size}")
            prefix = " ".join(pieces) if pieces else "read file"
            return f"{prefix} path={path}"
        if tool_id == "file_write":
            return (
                f"wrote {result.get('bytes_written', '?')} bytes to "
                f"{result.get('path', '(unknown path)')}"
            )
        if tool_id == "file_edit":
            return (
                f"{result.get('mode', 'edit')} "
                f"{result.get('path', '(unknown path)')}:"
                f"{result.get('start_line', '?')}-{result.get('end_line', '?')}"
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
        self._open_model_streams: dict[tuple[str, str], bool] = {}
        self._completed_model_streams: dict[tuple[str, str], bool] = {}

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
    def _stream_key(event: dict[str, Any]) -> tuple[str, str]:
        return (
            str(event.get("worker_id") or ""),
            str(event.get("round") or ""),
        )

    def _flush_open_model_streams(self) -> None:
        if not self._open_model_streams:
            return
        if any(self._open_model_streams.values()):
            print()
        self._completed_model_streams.update(self._open_model_streams)
        self._open_model_streams.clear()

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
        if name == "model.stream.started":
            if not self._show_model_trace:
                return
            key = self._stream_key(event)
            if key in self._open_model_streams:
                return
            self._completed_model_streams.pop(key, None)
            self._open_model_streams[key] = False
            return
        if name == "model.stream.delta":
            if not self._show_model_trace:
                return
            key = self._stream_key(event)
            if key not in self._open_model_streams or not self._open_model_streams[key]:
                prefix = self._stream_prefix("model", event)
                print(f"{prefix} stream: ", end="", flush=True)
            self._open_model_streams[key] = True
            delta = str(event.get("delta") or "").replace("\r", "").replace("\n", "\\n")
            if delta:
                print(delta, end="", flush=True)
            return
        if name == "model.stream.completed":
            if not self._show_model_trace:
                return
            key = self._stream_key(event)
            had_visible_output = bool(self._open_model_streams.pop(key, False))
            self._completed_model_streams[key] = had_visible_output
            if had_visible_output:
                print()
            return
        if name not in {"model.stream.started", "model.stream.delta"}:
            self._flush_open_model_streams()
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
            self._completed_model_streams.pop(self._stream_key(event), None)
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
            key = self._stream_key(event)
            had_visible_stream = bool(self._completed_model_streams.pop(key, False))
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
            if event.get("streamed") and had_visible_stream:
                return
            if text:
                print(f"{prefix} preview: {text}")
            return
        if name == "model.timeout":
            scope = self._scope_label(event.get("worker_id"))
            prefix = "[status] model timeout"
            if scope:
                prefix += f" [{scope}]"
            details = [
                f"round={event.get('round', '?')}",
                f"model={event.get('model') or '(unknown)'}",
            ]
            timeout_seconds = event.get("timeout_seconds")
            if timeout_seconds not in {None, ""}:
                details.append(f"timeout={float(timeout_seconds):.2f}s")
            print(f"{prefix}: {' '.join(details)}")
            return
        if name == "code.heartbeat":
            scope = self._scope_label(event.get("worker_id"))
            phase = str(event.get("phase") or "running").strip() or "running"
            detail = _truncate_text(str(event.get("detail") or ""), limit=160)
            elapsed_seconds = event.get("elapsed_seconds")
            summary = f"[status] heartbeat: {phase}"
            if scope:
                summary += f" [{scope}]"
            if detail:
                summary += f" ({detail})"
            if elapsed_seconds not in {None, ""}:
                summary += f" idle={elapsed_seconds}s"
            print(summary)
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


def _event_timestamp_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _emit_code_event(event_callback, event: dict[str, Any]) -> None:
    if event_callback is None:
        return
    event_callback(dict(event))


def _log_code_event(logger: "CodeRunEventLogger | None", event: str, **payload: Any) -> None:
    if logger is None:
        return
    logger.emit({"event": event, **payload})


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(0.0, float(raw.strip()))
    except (TypeError, ValueError):
        return default


class CodeHeartbeatMonitor:
    """Emit sparse heartbeat events when a bounded DAN Code turn goes quiet."""

    def __init__(
        self,
        *,
        event_callback,
        enabled: bool = True,
        idle_seconds: float = 10.0,
        repeat_seconds: float = 15.0,
        poll_seconds: float = 2.0,
    ) -> None:
        self._event_callback = event_callback
        self._enabled = bool(enabled) and event_callback is not None
        self._idle_seconds = max(0.01, float(idle_seconds))
        self._repeat_seconds = max(self._idle_seconds, float(repeat_seconds))
        self._poll_seconds = max(0.01, float(poll_seconds))
        self._last_activity = 0.0
        self._last_heartbeat = 0.0
        self._phase = "starting"
        self._worker_id = ""
        self._detail = ""
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    def observe(self, event: dict[str, Any]) -> None:
        if not self._enabled:
            return
        name = str(event.get("event") or "").strip()
        if not name or name == "code.heartbeat":
            return
        loop = asyncio.get_running_loop()
        self._last_activity = loop.time()
        worker_id = str(event.get("worker_id") or "").strip()
        if worker_id:
            self._worker_id = worker_id

        if name == "run.log.started":
            self._phase = "run"
            self._detail = _truncate_text(str(event.get("objective") or ""), limit=160)
            return
        if name == "assistant.message":
            self._phase = "assistant"
            self._detail = _truncate_text(str(event.get("message") or ""), limit=160)
            return
        if name == "status.update":
            self._phase = str(event.get("phase") or event.get("actor") or "status").strip() or "status"
            self._detail = _truncate_text(str(event.get("message") or ""), limit=160)
            return
        if name == "stage.started":
            self._phase = str(event.get("stage") or "stage").strip() or "stage"
            self._detail = f"attempt={event.get('attempt', '?')}"
            return
        if name == "stage.completed":
            self._phase = f"{str(event.get('stage') or 'stage').strip() or 'stage'}-completed"
            self._detail = _truncate_text(str(event.get("status") or "completed"), limit=160)
            return
        if name == "model.requested":
            self._phase = "model"
            self._detail = (
                f"round={event.get('round', '?')} "
                f"tools={event.get('tool_count', 0)}"
            )
            return
        if name == "model.responded":
            finish = str(event.get("finish_reason") or "stop").strip() or "stop"
            self._phase = "model-response"
            self._detail = f"finish_reason={finish}"
            return
        if name == "model.timeout":
            timeout_seconds = event.get("timeout_seconds")
            timeout_detail = (
                f" timeout={float(timeout_seconds):.2f}s"
                if timeout_seconds not in {None, ""}
                else ""
            )
            self._phase = "model-timeout"
            self._detail = f"round={event.get('round', '?')}{timeout_detail}"
            return
        if name == "tool.started":
            self._phase = "tool"
            self._detail = _truncate_text(
                _tool_request_summary(
                    str(event.get("tool_id") or ""),
                    dict(event.get("arguments") or {}),
                ),
                limit=160,
            )
            return
        if name == "tool.completed":
            self._phase = "post-tool"
            self._detail = _truncate_text(
                _tool_request_summary(
                    str(event.get("tool_id") or ""),
                    dict(event.get("arguments") or {}),
                ),
                limit=160,
            )
            return
        if name in {"tool.failed", "tool.denied"}:
            self._phase = "tool-error"
            self._detail = _truncate_text(
                f"{event.get('tool_id') or 'tool'}: {event.get('error') or name}",
                limit=160,
            )
            return
        if name == "completion.completed":
            self._phase = "completion"
            self._detail = _truncate_text(str(event.get("stop_reason") or "completed"), limit=160)
            return
        if name in {"organism.completed", "run.log.completed"}:
            self._phase = "completed"
            self._detail = _truncate_text(str(event.get("status") or "completed"), limit=160)
            return
        if name == "run.log.failed":
            self._phase = "failed"
            self._detail = _truncate_text(str(event.get("error") or "run failed"), limit=160)

    async def start(self) -> None:
        if not self._enabled or self._task is not None:
            return
        now = asyncio.get_running_loop().time()
        if self._last_activity <= 0:
            self._last_activity = now
        self._last_heartbeat = now
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(self._poll_seconds)
            now = asyncio.get_running_loop().time()
            if (now - self._last_activity) < self._idle_seconds:
                continue
            if (now - self._last_heartbeat) < self._repeat_seconds:
                continue
            self._last_heartbeat = now
            _emit_code_event(
                self._event_callback,
                {
                    "event": "code.heartbeat",
                    "worker_id": self._worker_id or None,
                    "phase": self._phase,
                    "detail": self._detail,
                    "elapsed_seconds": int(now - self._last_activity),
                },
            )


class CodeRunEventLogger:
    """Persist the DAN Code event stream for one bounded run or control-plane log."""

    def __init__(
        self,
        *,
        path: Path,
        stream_kind: Literal["bounded_run", "control_plane"] = "bounded_run",
        session_id: str = "",
        turn_id: str = "",
        task_id: str = "",
        organism_id: str = "",
        organ_id: str = "",
        trace_id: str = "",
    ) -> None:
        self._writer = OrganismLogWriter(
            path=path,
            context=OrganismLogContext(
                product="dan_code",
                stream_kind=stream_kind,
                session_id=session_id,
                turn_id=turn_id,
                task_id=task_id,
                trace_id=trace_id,
                organism_id=organism_id,
                organ_id=organ_id,
            ),
        )
        self.path = self._writer.path

    def emit(self, event: dict[str, Any]) -> None:
        self._writer.emit(event)

    def emit_trace_rows(self, trace_rows: Sequence[dict[str, Any]]) -> None:
        self._writer.emit_trace_rows(trace_rows)

    def update_context(self, **updates: Any) -> None:
        self._writer.update_context(**updates)

    def close(self) -> None:
        self._writer.close()


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
) -> int | None:
    if explicit is not None:
        value = int(explicit)
        return None if value <= 0 else value
    if product_config:
        configured = product_config.max_tool_rounds
        if configured is None:
            return None
        value = int(configured)
        return None if value <= 0 else value
    return None


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


def _effective_completion_timeout_seconds(
    explicit: float | None,
    *,
    product_config: CodeProductConfig | None = None,
) -> float | None:
    if explicit is not None:
        value = float(explicit)
        return None if value <= 0 else value
    if product_config and product_config.completion_timeout_seconds is not None:
        value = float(product_config.completion_timeout_seconds)
        return None if value <= 0 else value
    raw = os.environ.get("DAN_CODE_COMPLETION_TIMEOUT_SECONDS")
    if raw is not None:
        try:
            value = float(str(raw).strip())
        except (TypeError, ValueError):
            return 90.0
        return None if value <= 0 else value
    return 90.0


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
    return live_gateway.build_gateway_backed_live_provider(
        model,
        api_key=api_key,
        base_url=base_url,
    )


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
    hard_constraints: Sequence[str] | None = None,
    soft_constraints: Sequence[str] | None = None,
    repair_brief: str = "",
    evidence_summaries: Sequence[str] | None = None,
    tool_ids: Sequence[str] | None = None,
    workspace_root: str | Path | None = None,
    max_tool_rounds: int | None = None,
    max_tool_calls: int = 24,
    completion_timeout_seconds: float | None = None,
    thinking_mode: str = "auto",
    stream_model_trace: bool = False,
    approval_callback=None,
    event_callback=None,
    session_context: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> CodingOrganismReport:
    benchmark_context = None
    if isinstance(session_context, dict):
        raw_benchmark_context = session_context.get("benchmark_context")
        if isinstance(raw_benchmark_context, dict) and raw_benchmark_context:
            benchmark_context = dict(raw_benchmark_context)
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
        completion_timeout_seconds=completion_timeout_seconds,
        stream_text_responses=bool(stream_model_trace),
        provider_request_overrides=_provider_request_overrides_for_thinking_mode(
            thinking_mode,
            completion_timeout_seconds=completion_timeout_seconds,
        ),
        event_callback=event_callback,
    )
    executor = WorkerCoreExecutor(
        completion_provider=completion_provider,
        event_sink=CallbackEventSink(event_callback),
    )
    organism = coding_execution_organism(
            organism_id=organism_id,
            model=model,
            base_id=organ_id,
        )
    effective_max_repair_rounds = _effective_coding_organism_max_repair_rounds(
        int(organism.max_repair_rounds),
        benchmark_context=benchmark_context,
        completion_timeout_seconds=completion_timeout_seconds,
    )
    _emit_code_event(
        event_callback,
        {
            "event": "repair.policy.selected",
            "configured_max_repair_rounds": int(organism.max_repair_rounds),
            "effective_max_repair_rounds": effective_max_repair_rounds,
            "benchmark_context": bool(benchmark_context),
            "short_completion_timeout": _short_completion_timeout_active(
                completion_timeout_seconds
            ),
            "completion_timeout_seconds": completion_timeout_seconds,
            "organ_id": organ_id,
            "organism_id": organism_id,
            "task_id": task_id,
        },
    )
    if effective_max_repair_rounds != int(organism.max_repair_rounds):
        organism = organism.model_copy(
            update={
                "max_repair_rounds": effective_max_repair_rounds,
            }
        )
    organism = attach_local_tooling_to_coding_organism(
        organism,
        tool_ids=tool_runtime.tool_ids,
    )
    evidence_refs = _build_evidence_refs(workdir, evidence_summaries or [])
    runtime_session_context = dict(session_context or {})
    if completion_timeout_seconds is not None:
        runtime_session_context["completion_timeout_seconds"] = float(completion_timeout_seconds)
        runtime_session_context["short_completion_timeout"] = _short_completion_timeout_active(
            completion_timeout_seconds
        )
    task = CodingTask(
        task_id=task_id,
        objective=objective,
        acceptance_criteria=acceptance_criteria or DEFAULT_CODING_ACCEPTANCE_CRITERIA,
        research_findings=list(research_findings or []),
        hard_constraints=list(hard_constraints or []),
        soft_constraints=list(soft_constraints or []),
        repair_brief=repair_brief,
        evidence_refs=[ref.model_copy(deep=True) for ref in evidence_refs],
        session_context=runtime_session_context,
    )
    trace_log = CrossCellTraceLog(event_callback=event_callback)
    execute_kwargs = {
        "executor": executor,
        "organism": organism,
        "task": task,
        "trace_log": trace_log,
        "event_callback": event_callback,
    }
    if trace_id is not None:
        execute_kwargs["trace_id"] = trace_id
    execution = await execute_coding_organism(**execute_kwargs)
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
        target_files=outputs.get("target_files"),
        test_plan=outputs.get("test_plan"),
        risks=outputs.get("risks"),
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
    event_log_path = str(report.get("event_log_path") or "").strip()
    if event_log_path:
        print(f"Event Log: {event_log_path}")
    control_log_path = str(report.get("control_log_path") or "").strip()
    if control_log_path:
        print(f"Control Log: {control_log_path}")
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
    *,
    completion_timeout_seconds: float | None = None,
) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    normalized = str(thinking_mode or "auto").strip().lower()
    if normalized != "auto":
        overrides["thinking"] = {"type": normalized}
    elif (
        completion_timeout_seconds is not None
        and 0 < float(completion_timeout_seconds) <= 60.0
    ):
        overrides["thinking"] = {"type": "disabled"}
    if completion_timeout_seconds is not None:
        overrides["timeout"] = float(completion_timeout_seconds)
    return overrides


def _short_completion_timeout_active(
    completion_timeout_seconds: float | None,
) -> bool:
    return (
        completion_timeout_seconds is not None
        and 0 < float(completion_timeout_seconds) <= 60.0
    )


def _effective_coding_organism_max_repair_rounds(
    configured_rounds: int,
    *,
    benchmark_context: dict[str, Any] | None,
    completion_timeout_seconds: float | None,
) -> int:
    effective = max(int(configured_rounds), 0)
    if benchmark_context:
        effective = max(effective, 2)
    if _short_completion_timeout_active(completion_timeout_seconds):
        return 0
    return effective


def _effective_supervision_loop_count(
    *,
    benchmark_context: dict[str, Any] | None,
    completion_timeout_seconds: float | None,
) -> int:
    if _short_completion_timeout_active(completion_timeout_seconds):
        return 1
    if benchmark_context:
        return _BENCHMARK_SUPERVISION_LOOPS
    return _DEFAULT_SUPERVISION_LOOPS


class CodeConversationOutcome(BaseModel):
    """Rendered outcome of one orchestrated DAN Code turn."""

    status: str
    assistant_messages: list[str] = Field(default_factory=list)
    question: str | None = None
    reports: list[CodingOrganismReport] = Field(default_factory=list)
    control_log_path: str | None = None
    control_log_schema: str | None = None


def _assistant_text(message: str) -> str:
    return " ".join(str(message or "").strip().split())


def _workspace_check_available(tool_ids: Sequence[str]) -> bool:
    return any(str(tool_id or "").strip() == "workspace_check" for tool_id in tool_ids)


def _looks_like_stale_html_shell_count_objective(text: str) -> bool:
    normalized = " ".join(str(text or "").strip().lower().split())
    if not normalized or "index.html" not in normalized:
        return False
    if not any(marker in normalized for marker in ("shell_command", "shell command", "grep -c")):
        return False
    if not any(marker in normalized for marker in ("count", "counts", "tag balance", "structural tag")):
        return False
    html_tag_markers = (
        "<html",
        "</html",
        "<head",
        "</head",
        "<body",
        "</body",
        "<main",
        "</main",
    )
    return sum(1 for marker in html_tag_markers if marker in normalized) >= 4


def _structured_workspace_check_html_objective() -> str:
    return (
        "Use the structured read-only `workspace_check` tool for the HTML structural "
        "repair check instead of shell tag-count commands. Run `workspace_check` with "
        '`check="html_tags"`, `path="index.html"`, and '
        '`tags=["html","head","body","main"]`. If the check reports balanced single '
        "document tags, do not fabricate an edit; return a bounded verified candidate "
        "with the workspace_check result and `target_files=[\"index.html\"]`. If the "
        "check reports duplicate or imbalanced tags, call `file_read` for `index.html`, "
        "repair only the broken structural tags with one bounded `file_edit`, rerun the "
        "same workspace_check, and return the before/after check results plus every "
        "modified file name. Do not use `shell_command` for this tag-count check."
    )


def _normalize_structured_workspace_check_objective(
    objective: str,
    *,
    tool_ids: Sequence[str],
) -> tuple[str, bool]:
    cleaned = _assistant_text(objective)
    if not _workspace_check_available(tool_ids):
        return cleaned, False
    if not _looks_like_stale_html_shell_count_objective(cleaned):
        return cleaned, False
    return _structured_workspace_check_html_objective(), True


def _criterion_is_stale_html_shell_count(text: str) -> bool:
    normalized = " ".join(str(text or "").strip().lower().split())
    if not normalized:
        return False
    if not any(marker in normalized for marker in ("shell_command", "shell command", "grep -c")):
        return False
    return "index.html" in normalized or any(
        marker in normalized for marker in ("<html", "</html", "<head", "<body", "<main")
    )


def _normalize_structured_workspace_check_acceptance_criteria(
    criteria: Sequence[str],
    *,
    objective: str,
    tool_ids: Sequence[str],
) -> tuple[list[str], bool]:
    original = _dedupe(criteria)
    if not _workspace_check_available(tool_ids):
        return original, False
    if not _looks_like_stale_html_shell_count_objective(objective) and not any(
        _criterion_is_stale_html_shell_count(criterion) for criterion in original
    ):
        return original, False

    retained = [
        criterion
        for criterion in original
        if not _criterion_is_stale_html_shell_count(criterion)
    ]
    normalized = _dedupe(
        [
            *retained,
            "Use `workspace_check` with `check=\"html_tags\"` for `index.html` structural tag verification.",
            "If the HTML structure is already balanced, return a verified no-edit candidate instead of fabricating an edit.",
            "If a repair is needed, modify only `index.html` and include the post-repair `workspace_check` result.",
        ]
    )
    return normalized, normalized != original


def _normalize_structured_workspace_check_project_plan(
    plan: CodingProjectPlan | None,
    *,
    tool_ids: Sequence[str],
) -> tuple[CodingProjectPlan | None, bool]:
    if plan is None:
        return None, False

    changed = False
    project_goal, goal_changed = _normalize_structured_workspace_check_objective(
        plan.project_goal,
        tool_ids=tool_ids,
    )
    changed = changed or goal_changed

    milestones = []
    for milestone in plan.milestones:
        objective, objective_changed = _normalize_structured_workspace_check_objective(
            milestone.objective,
            tool_ids=tool_ids,
        )
        acceptance_criteria, criteria_changed = (
            _normalize_structured_workspace_check_acceptance_criteria(
                milestone.acceptance_criteria,
                objective=milestone.objective,
                tool_ids=tool_ids,
            )
        )
        if objective_changed or criteria_changed:
            milestone = milestone.model_copy(
                update={
                    "objective": objective,
                    "acceptance_criteria": acceptance_criteria,
                }
            )
            changed = True
        milestones.append(milestone)

    if not changed:
        return plan, False
    return (
        plan.model_copy(
            update={
                "project_goal": project_goal,
                "milestones": milestones,
            }
        ),
        True,
    )


def _normalize_structured_workspace_check_planner_decision(
    planner_decision: Any,
    *,
    tool_ids: Sequence[str],
) -> tuple[Any, bool]:
    changed = False
    project_plan = CodingProjectPlan(
        project_goal=getattr(planner_decision, "project_goal", ""),
        plan_summary=getattr(planner_decision, "plan_summary", ""),
        milestones=list(getattr(planner_decision, "milestones", []) or []),
        active_milestone_id=getattr(planner_decision, "active_milestone_id", None),
    )
    project_plan, plan_changed = _normalize_structured_workspace_check_project_plan(
        project_plan,
        tool_ids=tool_ids,
    )
    changed = changed or plan_changed

    active_objective, active_objective_changed = _normalize_structured_workspace_check_objective(
        getattr(planner_decision, "active_objective", ""),
        tool_ids=tool_ids,
    )
    active_acceptance_criteria, active_criteria_changed = (
        _normalize_structured_workspace_check_acceptance_criteria(
            list(getattr(planner_decision, "active_acceptance_criteria", []) or []),
            objective=getattr(planner_decision, "active_objective", ""),
            tool_ids=tool_ids,
        )
    )
    changed = changed or active_objective_changed or active_criteria_changed
    if not changed or project_plan is None:
        return planner_decision, False

    return (
        planner_decision.model_copy(
            update={
                "project_goal": project_plan.project_goal,
                "milestones": list(project_plan.milestones),
                "active_objective": active_objective,
                "active_acceptance_criteria": active_acceptance_criteria,
            }
        ),
        True,
    )


def _mark_benchmark_continuation_exhausted(
    report: CodingOrganismReport,
    *,
    review,
    loop_count: int,
) -> CodingOrganismReport:
    next_work = _assistant_text(
        getattr(review, "repair_brief", None)
        or getattr(review, "next_objective", None)
        or getattr(review, "public_response", None)
        or "The benchmark reviewer requested another repair or validation pass."
    )
    reason = (
        f"Benchmark review requested continuation after {loop_count} supervised "
        "coding passes; refusing to export an unfinished SWE-bench prediction."
    )
    if next_work:
        reason = f"{reason} Next requested work: {next_work}"
    report.status = "incomplete"
    report.error = reason
    risks = list(getattr(report, "risks", None) or [])
    if reason not in risks:
        report.risks = [*risks, reason]
    return report


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
    approval_mode: str,
    acceptance_criteria: Sequence[str],
    benchmark_context: dict[str, Any] | None = None,
    additional_reports: Sequence[CodingOrganismReport] | None = None,
) -> CodingConversationContext:
    facts = _conversation_facts(
        session=session,
        workspace_root=workspace_root,
        model=model,
        thinking_mode=thinking_mode,
        tool_ids=tool_ids,
        approval_mode=approval_mode,
        benchmark_context=benchmark_context,
        additional_reports=additional_reports,
    )
    session_reports = session.context_reports(limit=4)
    additional_context_reports = [
        report for report in list(additional_reports or []) if not report.is_failed_no_output()
    ]
    recent_reports = [
        _report_context(report)
        for report in [*session_reports, *additional_context_reports]
    ]
    return CodingConversationContext(
        workspace_root=str(workspace_root),
        model=model,
        thinking_mode=thinking_mode,
        tool_ids=list(tool_ids),
        acceptance_criteria=list(acceptance_criteria),
        pending_clarification=session.pending_clarification,
        facts=facts,
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


def _project_planner_context(
    *,
    session: CodingCliSession,
    workspace_root: Path,
    model: str,
    thinking_mode: str,
    tool_ids: Sequence[str],
    approval_mode: str,
    acceptance_criteria: Sequence[str],
    existing_plan: CodingProjectPlan | None,
    benchmark_context: dict[str, Any] | None = None,
    additional_reports: Sequence[CodingOrganismReport] | None = None,
) -> CodingProjectPlannerContext:
    facts = _conversation_facts(
        session=session,
        workspace_root=workspace_root,
        model=model,
        thinking_mode=thinking_mode,
        tool_ids=tool_ids,
        approval_mode=approval_mode,
        benchmark_context=benchmark_context,
        additional_reports=additional_reports,
    )
    session_reports = session.context_reports(limit=4)
    additional_context_reports = [
        report for report in list(additional_reports or []) if not report.is_failed_no_output()
    ]
    recent_reports = [
        _report_context(report)
        for report in [*session_reports, *additional_context_reports]
    ]
    return CodingProjectPlannerContext(
        workspace_root=str(workspace_root),
        model=model,
        thinking_mode=thinking_mode,
        tool_ids=list(tool_ids),
        acceptance_criteria=list(acceptance_criteria),
        pending_clarification=session.pending_clarification,
        facts=facts,
        recent_conversation=[
            CodingConversationMessage(
                role=entry.role,
                text=entry.text,
                kind=entry.kind,
            )
            for entry in session.conversation[-8:]
        ],
        recent_reports=recent_reports[-6:],
        existing_plan=existing_plan,
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


def _load_project_planner_session(
    *,
    session: CodingCliSession,
    planner: CodingProjectPlannerController,
):
    try:
        return planner.load_session(
            dict(session.orchestrator_state.get("project_planner_session") or {})
        )
    except Exception:
        return None


def _store_project_planner_session(
    *,
    session: CodingCliSession,
    planner: CodingProjectPlannerController,
    durable_session,
) -> None:
    session.orchestrator_state = {
        **dict(session.orchestrator_state),
        "project_planner_session": planner.dump_session(durable_session),
    }


def _load_project_plan(session: CodingCliSession) -> CodingProjectPlan | None:
    try:
        payload = dict(session.orchestrator_state.get("project_plan") or {})
    except Exception:
        return None
    if not payload:
        return None
    try:
        return CodingProjectPlan.model_validate(payload)
    except Exception:
        return None


def _store_project_plan(
    *,
    session: CodingCliSession,
    plan: CodingProjectPlan | None,
) -> None:
    orchestrator_state = dict(session.orchestrator_state)
    if plan is None:
        orchestrator_state.pop("project_plan", None)
    else:
        orchestrator_state["project_plan"] = plan.model_dump(mode="json")
    session.orchestrator_state = orchestrator_state


def _emit_assistant_message(
    event_callback,
    message: str,
) -> None:
    text = _assistant_text(message)
    if not text:
        return
    _emit_code_event(event_callback, {"event": "assistant.message", "message": text})


async def _run_orchestrated_turn(
    *,
    args,
    controller: CodingConversationController,
    project_planner: CodingProjectPlannerController,
    llm_provider: LLMProvider,
    workspace_root: Path,
    model: str,
    objective: str,
    session: CodingCliSession,
    tool_ids: Sequence[str],
    acceptance_criteria: Sequence[str],
    evidence_summaries: Sequence[str],
    max_tool_rounds: int | None,
    max_tool_calls: int,
    completion_timeout_seconds: float | None,
    approval_mode: str,
    thinking_mode: str,
    run_root: Path,
    task_id_base: str,
    benchmark_context: dict[str, Any] | None = None,
    approval_callback=None,
    progress_renderer: CodeProgressRenderer,
    control_logger: CodeRunEventLogger | None = None,
) -> CodeConversationOutcome:
    assistant_messages: list[str] = []
    reports: list[CodingOrganismReport] = []
    question: str | None = None
    current_event_logger: CodeRunEventLogger | None = None
    current_trace_rows_streamed = False
    session.record_message(role="user", text=objective)
    _log_code_event(
        control_logger,
        "orchestrator.turn.started",
        session_id=session.session_id,
        objective=objective,
        pending_clarification=session.pending_clarification,
        prior_turns=len(session.turns),
    )

    def _run_event_callback(event: dict[str, Any]) -> None:
        nonlocal current_trace_rows_streamed
        heartbeat.observe(event)
        _emit_code_event(progress_renderer, event)
        trace_row = event.get("trace_row")
        if (
            current_event_logger is not None
            and str(event.get("event") or "").strip() == "trace.row"
            and isinstance(trace_row, dict)
        ):
            current_trace_rows_streamed = True
            current_event_logger.emit_trace_rows([trace_row])
            return
        if current_event_logger is not None:
            current_event_logger.emit(event)

    def _control_event_callback(event: dict[str, Any]) -> None:
        nonlocal current_trace_rows_streamed
        heartbeat.observe(event)
        _emit_code_event(progress_renderer, event)
        if control_logger is not None:
            control_logger.emit(event)
        trace_row = event.get("trace_row")
        if (
            current_event_logger is not None
            and str(event.get("event") or "").strip() == "trace.row"
            and isinstance(trace_row, dict)
        ):
            current_trace_rows_streamed = True
            current_event_logger.emit_trace_rows([trace_row])
            return
        if current_event_logger is not None:
            current_event_logger.emit(event)

    heartbeat = CodeHeartbeatMonitor(
        event_callback=_control_event_callback,
        enabled=True,
        idle_seconds=_env_float("DAN_CODE_HEARTBEAT_IDLE_SECONDS", 10.0),
        repeat_seconds=_env_float("DAN_CODE_HEARTBEAT_INTERVAL_SECONDS", 15.0),
        poll_seconds=_env_float("DAN_CODE_HEARTBEAT_POLL_SECONDS", 2.0),
    )
    controller.set_event_callback(_control_event_callback)
    project_planner.set_event_callback(_control_event_callback)
    await heartbeat.start()

    def _build_outcome(
        *,
        status: str,
        assistant_messages: Sequence[str],
        question: str | None,
        reports: Sequence[CodingOrganismReport],
    ) -> CodeConversationOutcome:
        return CodeConversationOutcome(
            status=status,
            assistant_messages=list(assistant_messages),
            question=question,
            reports=list(reports),
            control_log_path=(
                str(control_logger.path) if control_logger is not None else None
            ),
            control_log_schema=(
                ORGANISM_LOG_SCHEMA_VERSION if control_logger is not None else None
            ),
        )

    try:
        orchestrator_session = _load_orchestrator_session(
            session=session,
            controller=controller,
        )
        _log_code_event(
            control_logger,
            "orchestrator.turn.decision.started",
            objective=objective,
            pending_clarification=session.pending_clarification,
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
                approval_mode=approval_mode,
                acceptance_criteria=acceptance_criteria,
                benchmark_context=benchmark_context,
            ),
        )
        _store_orchestrator_session(
            session=session,
            controller=controller,
            durable_session=orchestrator_session,
        )
        _log_code_event(
            control_logger,
            "orchestrator.turn.decision.completed",
            action=decision.action,
            coding_objective=decision.coding_objective,
            has_public_response=bool(decision.public_response),
            has_clarifying_question=bool(decision.clarifying_question),
        )

        def _record_assistant(text: str, *, kind: str = "message") -> None:
            cleaned = _assistant_text(text)
            if not cleaned:
                return
            assistant_messages.append(cleaned)
            session.record_message(role="assistant", text=cleaned, kind=kind)
            _emit_assistant_message(_control_event_callback, cleaned)

        if decision.public_response:
            _record_assistant(decision.public_response)

        if decision.action == "respond":
            session.pending_clarification = None
            _log_code_event(
                control_logger,
                "orchestrator.turn.completed",
                final_status="responded",
                report_count=0,
            )
            return _build_outcome(
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
            _log_code_event(
                control_logger,
                "orchestrator.turn.completed",
                final_status="clarify",
                report_count=0,
                question=question,
            )
            return _build_outcome(
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
        raw_next_objective = next_objective
        next_objective, objective_normalized = _normalize_structured_workspace_check_objective(
            next_objective,
            tool_ids=tool_ids,
        )
        effective_acceptance_criteria, criteria_normalized = (
            _normalize_structured_workspace_check_acceptance_criteria(
                effective_acceptance_criteria,
                objective=raw_next_objective,
                tool_ids=tool_ids,
            )
        )
        if objective_normalized or criteria_normalized:
            _log_code_event(
                control_logger,
                "orchestrator.structured_check_objective.normalized",
                stage="pre_planner",
                objective_normalized=objective_normalized,
                acceptance_criteria_normalized=criteria_normalized,
            )
        existing_project_plan = _load_project_plan(session)
        existing_project_plan, existing_plan_normalized = (
            _normalize_structured_workspace_check_project_plan(
                existing_project_plan,
                tool_ids=tool_ids,
            )
        )
        if existing_plan_normalized:
            _store_project_plan(session=session, plan=existing_project_plan)
            _log_code_event(
                control_logger,
                "orchestrator.structured_check_objective.normalized",
                stage="existing_project_plan",
                objective_normalized=True,
                acceptance_criteria_normalized=True,
            )
        task_lane_policy = classify_code_task_lane(
            user_message=objective,
            coding_objective=next_objective,
            benchmark_mode=benchmark_context is not None,
            pending_clarification=bool(session.pending_clarification),
            existing_plan_milestones=(
                len(existing_project_plan.milestones)
                if existing_project_plan is not None
                else 0
            ),
        )
        if task_lane_policy.use_fallback_pre_run_planner:
            _log_code_event(
                control_logger,
                "orchestrator.project_planner.fallback",
                requested_objective=next_objective,
                requested_acceptance_criteria=list(effective_acceptance_criteria),
                existing_milestone_count=(
                    len(existing_project_plan.milestones)
                    if existing_project_plan is not None
                    else 0
                ),
            )
            planner_decision = _fallback_project_planner_decision(
                user_message=objective,
                requested_objective=next_objective,
                requested_acceptance_criteria=list(effective_acceptance_criteria),
                existing_plan=existing_project_plan,
            )
            orchestrator_state = dict(session.orchestrator_state)
            orchestrator_state.pop("project_planner_session", None)
            session.orchestrator_state = orchestrator_state
        else:
            project_planner_session = _load_project_planner_session(
                session=session,
                planner=project_planner,
            )
            _log_code_event(
                control_logger,
                "orchestrator.project_planner.started",
                requested_objective=next_objective,
                requested_acceptance_criteria=list(effective_acceptance_criteria),
                existing_milestone_count=(
                    len(existing_project_plan.milestones)
                    if existing_project_plan is not None
                    else 0
                ),
            )
            planner_decision, project_planner_session = await project_planner.plan_project(
                session=project_planner_session,
                user_message=objective,
                requested_objective=next_objective,
                requested_acceptance_criteria=effective_acceptance_criteria,
                context=_project_planner_context(
                    session=session,
                    workspace_root=workspace_root,
                    model=model,
                    thinking_mode=thinking_mode,
                    tool_ids=tool_ids,
                    approval_mode=approval_mode,
                    acceptance_criteria=effective_acceptance_criteria,
                    existing_plan=existing_project_plan,
                    benchmark_context=benchmark_context,
                ),
            )
            _store_project_planner_session(
                session=session,
                planner=project_planner,
                durable_session=project_planner_session,
            )
        planner_decision, planner_normalized = (
            _normalize_structured_workspace_check_planner_decision(
                planner_decision,
                tool_ids=tool_ids,
            )
        )
        if planner_normalized:
            _log_code_event(
                control_logger,
                "orchestrator.structured_check_objective.normalized",
                stage="project_planner_decision",
                objective_normalized=True,
                acceptance_criteria_normalized=True,
            )
        if not task_lane_policy.use_fallback_pre_run_planner:
            _log_code_event(
                control_logger,
                "orchestrator.project_planner.completed",
                active_objective=planner_decision.active_objective,
                active_milestone_id=planner_decision.active_milestone_id,
                milestone_count=len(planner_decision.milestones),
            )
        project_plan = CodingProjectPlan(
            project_goal=planner_decision.project_goal,
            plan_summary=planner_decision.plan_summary,
            milestones=list(planner_decision.milestones),
            active_milestone_id=planner_decision.active_milestone_id,
        )
        _store_project_plan(session=session, plan=project_plan)
        if planner_decision.public_response:
            _record_assistant(planner_decision.public_response)
        next_objective = _assistant_text(planner_decision.active_objective or next_objective)
        effective_acceptance_criteria = _dedupe(
            [
                *list(effective_acceptance_criteria),
                *list(planner_decision.active_acceptance_criteria),
            ]
        ) or list(effective_acceptance_criteria)
        max_supervision_loops = _effective_supervision_loop_count(
            benchmark_context=benchmark_context,
            completion_timeout_seconds=completion_timeout_seconds,
        )
        _log_code_event(
            control_logger,
            "supervision.policy.selected",
            max_supervision_loops=max_supervision_loops,
            benchmark_context=bool(benchmark_context),
            short_completion_timeout=_short_completion_timeout_active(
                completion_timeout_seconds
            ),
            completion_timeout_seconds=completion_timeout_seconds,
        )
        base_turn_number = session.next_turn_number()

        for continuation_index in range(max_supervision_loops):
            report_turn_number = base_turn_number + len(reports)
            task_id = f"{task_id_base}:{report_turn_number}"
            workdir = _build_run_workdir(run_root, turn_number=report_turn_number)
            run_trace_id = new_trace_id()
            event_logger = CodeRunEventLogger(
                path=workdir / "events.jsonl",
                session_id=session.session_id,
                turn_id=str(report_turn_number),
                task_id=task_id,
                organism_id=args.organism_id,
                organ_id=args.organ_id,
                trace_id=run_trace_id,
            )
            current_event_logger = event_logger
            current_trace_rows_streamed = False
            run_completed = False
            try:
                _log_code_event(
                    control_logger,
                    "run.turn.started",
                    task_id=task_id,
                    turn_number=report_turn_number,
                    objective=next_objective,
                    workdir=str(workdir),
                    active_milestone_id=project_plan.active_milestone_id,
                    milestone_count=len(project_plan.milestones),
                )
                _emit_code_event(
                    _run_event_callback,
                    {
                        "event": "run.log.started",
                        "trace_id": run_trace_id,
                        "task_id": task_id,
                        "turn_number": report_turn_number,
                        "objective": next_objective,
                        "workdir": str(workdir),
                        "repair_brief": next_repair_brief,
                        "acceptance_criteria": list(effective_acceptance_criteria),
                        "project_goal": project_plan.project_goal,
                        "project_plan_summary": project_plan.plan_summary,
                        "active_milestone_id": project_plan.active_milestone_id,
                        "milestone_count": len(project_plan.milestones),
                    },
                )
                report = await _run_coding_turn(
                    args=args,
                    llm_provider=llm_provider,
                    workspace_root=workspace_root,
                    model=model,
                    objective=next_objective,
                    task_id=task_id,
                    research_findings=next_research_findings,
                    workdir=workdir,
                    acceptance_criteria=effective_acceptance_criteria,
                    tool_ids=tool_ids,
                    evidence_summaries=evidence_summaries,
                    max_tool_rounds=max_tool_rounds,
                    max_tool_calls=max_tool_calls,
                    completion_timeout_seconds=completion_timeout_seconds,
                    approval_mode=approval_mode,
                    thinking_mode=thinking_mode,
                    repair_brief=next_repair_brief,
                    benchmark_context=benchmark_context,
                    stream_model_trace=bool(args.show_model_trace),
                    approval_callback=approval_callback,
                    event_callback=_run_event_callback,
                    trace_id=run_trace_id,
                )
                report = report.model_copy(
                    update={
                        "event_log_path": str(event_logger.path),
                        "event_log_schema": ORGANISM_LOG_SCHEMA_VERSION,
                        "control_log_path": str(control_logger.path) if control_logger is not None else None,
                        "control_log_schema": (
                            ORGANISM_LOG_SCHEMA_VERSION if control_logger is not None else None
                        ),
                    }
                )
                if (
                    current_event_logger is not None
                    and not current_trace_rows_streamed
                    and list(report.trace_rows or [])
                ):
                    current_event_logger.emit_trace_rows(list(report.trace_rows or []))
                event_logger.update_context(trace_id=report.trace_id)
                reports.append(report)

                review_report_context = _report_context(report)
                if task_lane_policy.use_fallback_post_run_review:
                    review = _fallback_review_decision(
                        objective=next_objective,
                        report_summary=review_report_context,
                        benchmark_mode=benchmark_context is not None,
                    )
                else:
                    review, orchestrator_session = await controller.review_coding_result(
                        session=orchestrator_session,
                        objective=next_objective,
                        report_summary=review_report_context,
                        context=_conversation_context(
                            session=session,
                            workspace_root=workspace_root,
                            model=model,
                            thinking_mode=thinking_mode,
                            tool_ids=tool_ids,
                            approval_mode=approval_mode,
                            acceptance_criteria=effective_acceptance_criteria,
                            benchmark_context=benchmark_context,
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

                if review.action == "continue":
                    next_objective = _assistant_text(review.next_objective or next_objective)
                    next_repair_brief = _assistant_text(
                        review.repair_brief or report.error or next_repair_brief
                    )
                    next_research_findings = _dedupe(
                        [
                            *next_research_findings,
                            _report_finding(report),
                            *list(review.research_findings),
                        ]
                    )
                    if continuation_index + 1 >= max_supervision_loops and benchmark_context:
                        report = _mark_benchmark_continuation_exhausted(
                            report,
                            review=review,
                            loop_count=max_supervision_loops,
                        )
                        reports[-1] = report
                        _emit_code_event(
                            _run_event_callback,
                            {
                                "event": "run.log.completed",
                                "task_id": task_id,
                                "trace_id": report.trace_id,
                                "status": report.status,
                                "review_action": review.action,
                                "candidate_id": report.candidate_id,
                                "event_log_path": report.event_log_path,
                            },
                        )
                        _log_code_event(
                            control_logger,
                            "run.turn.completed",
                            task_id=task_id,
                            trace_id=report.trace_id,
                            status=report.status,
                            review_action=review.action,
                            candidate_id=report.candidate_id,
                            event_log_path=report.event_log_path,
                        )
                        run_completed = True
                        break
                    if continuation_index + 1 >= max_supervision_loops:
                        _emit_code_event(
                            _run_event_callback,
                            {
                                "event": "run.log.completed",
                                "task_id": task_id,
                                "trace_id": report.trace_id,
                                "status": report.status,
                                "review_action": review.action,
                                "candidate_id": report.candidate_id,
                                "event_log_path": report.event_log_path,
                            },
                        )
                        _log_code_event(
                            control_logger,
                            "run.turn.completed",
                            task_id=task_id,
                            trace_id=report.trace_id,
                            status=report.status,
                            review_action=review.action,
                            candidate_id=report.candidate_id,
                            event_log_path=report.event_log_path,
                        )
                        run_completed = True
                        break
                    _emit_code_event(
                        _run_event_callback,
                        {
                            "event": "run.log.completed",
                            "task_id": task_id,
                            "trace_id": report.trace_id,
                            "status": report.status,
                            "review_action": review.action,
                            "candidate_id": report.candidate_id,
                            "event_log_path": report.event_log_path,
                        },
                    )
                    _log_code_event(
                        control_logger,
                        "run.turn.completed",
                        task_id=task_id,
                        trace_id=report.trace_id,
                        status=report.status,
                        review_action=review.action,
                        candidate_id=report.candidate_id,
                        event_log_path=report.event_log_path,
                    )
                    run_completed = True
                    continue

                if review.action == "clarify":
                    question = _assistant_text(
                        review.clarifying_question or review.public_response
                    )
                    session.pending_clarification = question or None
                    if question and question not in assistant_messages:
                        _record_assistant(question, kind="clarification")
                    _emit_code_event(
                        _run_event_callback,
                        {
                            "event": "run.log.completed",
                            "task_id": task_id,
                            "trace_id": report.trace_id,
                            "status": report.status,
                            "review_action": review.action,
                            "candidate_id": report.candidate_id,
                            "question": question,
                            "event_log_path": report.event_log_path,
                        },
                    )
                    _log_code_event(
                        control_logger,
                        "run.turn.completed",
                        task_id=task_id,
                        trace_id=report.trace_id,
                        status=report.status,
                        review_action=review.action,
                        candidate_id=report.candidate_id,
                        question=question,
                        event_log_path=report.event_log_path,
                    )
                    run_completed = True
                    _log_code_event(
                        control_logger,
                        "orchestrator.turn.completed",
                        final_status="clarify",
                        report_count=len(reports),
                        question=question,
                    )
                    return _build_outcome(
                        status="clarify",
                        assistant_messages=assistant_messages,
                        question=question,
                        reports=reports,
                    )
                _emit_code_event(
                    _run_event_callback,
                    {
                        "event": "run.log.completed",
                        "task_id": task_id,
                        "trace_id": report.trace_id,
                        "status": report.status,
                        "review_action": review.action,
                        "candidate_id": report.candidate_id,
                        "event_log_path": report.event_log_path,
                    },
                )
                _log_code_event(
                    control_logger,
                    "run.turn.completed",
                    task_id=task_id,
                    trace_id=report.trace_id,
                    status=report.status,
                    review_action=review.action,
                    candidate_id=report.candidate_id,
                    event_log_path=report.event_log_path,
                )
                run_completed = True
                break
            except Exception as exc:
                if not run_completed:
                    _emit_code_event(
                        _run_event_callback,
                        {
                            "event": "run.log.failed",
                            "task_id": task_id,
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        },
                    )
                    _log_code_event(
                        control_logger,
                        "run.turn.failed",
                        task_id=task_id,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                raise
            finally:
                current_event_logger = None
                event_logger.close()

        final_status = reports[-1].status if reports else "responded"
        _log_code_event(
            control_logger,
            "orchestrator.turn.completed",
            final_status=final_status,
            report_count=len(reports),
        )
        return _build_outcome(
            status=final_status,
            assistant_messages=assistant_messages,
            question=question,
            reports=reports,
        )
    except Exception as exc:
        _log_code_event(
            control_logger,
            "orchestrator.turn.failed",
            error_type=type(exc).__name__,
            error=str(exc),
            report_count=len(reports),
        )
        raise
    finally:
        controller.set_event_callback(progress_renderer)
        project_planner.set_event_callback(progress_renderer)
        await heartbeat.stop()


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
    benchmark_context: dict[str, Any] | None = None,
    max_tool_rounds: int | None,
    max_tool_calls: int,
    completion_timeout_seconds: float | None,
    approval_mode: str,
    thinking_mode: str,
    repair_brief: str,
    stream_model_trace: bool = False,
    approval_callback=None,
    event_callback=None,
    trace_id: str | None = None,
) -> CodingOrganismReport:
    session_context = _build_runtime_context(
        workspace_root=workspace_root,
        model=model,
        tool_ids=tool_ids,
        approval_mode=approval_mode,
        thinking_mode=thinking_mode,
        task_id=task_id,
        completion_timeout_seconds=completion_timeout_seconds,
        benchmark_context=benchmark_context,
    )
    (
        benchmark_research_findings,
        benchmark_hard_constraints,
        benchmark_soft_constraints,
    ) = _benchmark_task_guidance(benchmark_context)
    result = await run_coding_organism_live(
        workdir,
        llm_provider=llm_provider,
        objective=objective,
        model=model,
        task_id=task_id,
        organ_id=str(args.organ_id),
        organism_id=str(args.organism_id),
        acceptance_criteria=acceptance_criteria,
        research_findings=_dedupe(
            list(args.research_findings)
            + list(research_findings)
            + benchmark_research_findings
        ),
        hard_constraints=benchmark_hard_constraints,
        soft_constraints=benchmark_soft_constraints,
        repair_brief=str(repair_brief),
        evidence_summaries=_dedupe(list(args.evidence_summaries) + list(evidence_summaries)),
        tool_ids=tool_ids,
        workspace_root=workspace_root,
        max_tool_rounds=max_tool_rounds,
        max_tool_calls=max_tool_calls,
        completion_timeout_seconds=completion_timeout_seconds,
        thinking_mode=thinking_mode,
        stream_model_trace=stream_model_trace,
        approval_callback=approval_callback,
        event_callback=event_callback,
        session_context=session_context,
        trace_id=trace_id,
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
    print("- natural-language turns go through the durable orchestrator first")
    print("- only explicit slash commands stay local to the shell")
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
    completion_timeout_seconds: float | None,
) -> None:
    print(f"product: {CODE_PRODUCT_NAME}")
    print(f"workspace: {workspace_root}")
    print(f"effective working directory: {workspace_root}")
    print(
        "shell process directory: "
        f"{Path(os.environ.get('PWD') or str(workspace_root)).expanduser()}"
    )
    print(f"session: {session.session_id}")
    print(f"turns: {len(session.turns)}")
    print(f"conversation messages: {len(session.conversation)}")
    print(f"model: {model}")
    print(f"thinking mode: {thinking_mode}")
    print(f"tools: {', '.join(tool_ids) or '(none)'}")
    if completion_timeout_seconds is None:
        print("completion timeout: disabled")
    else:
        print(f"completion timeout: {float(completion_timeout_seconds):.2f}s")
    print(f"approval mode: {approval_mode}")
    if session.pending_clarification:
        print(f"pending clarification: {session.pending_clarification}")
    print(f"session persistence: {'enabled' if persist_session else 'disabled'}")
    if persist_session:
        print(f"session file: {product_paths.session}")
        print(f"transcript: {product_paths.transcript}")
        print(f"control log: {product_paths.control_log}")


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
            should_persist = False
            if not loaded.workspace_root:
                loaded.workspace_root = str(workspace_root)
                should_persist = True
            if loaded.refresh_for_current_runtime():
                should_persist = True
            if should_persist:
                save_code_product_session(product_paths, loaded)
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
        completion_timeout_seconds=_effective_completion_timeout_seconds(
            args.completion_timeout_seconds,
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
    max_tool_rounds: int | None,
    max_tool_calls: int,
    completion_timeout_seconds: float | None,
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
        "control_log_path": product_paths.control_log,
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
        "max_tool_rounds": max_tool_rounds,
        "max_tool_calls": int(max_tool_calls),
        "completion_timeout_seconds": completion_timeout_seconds,
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
    print(f"control log: {payload['control_log_path']}")
    print(f"resolved model: {payload.get('resolved_model') or '(unset)'}")
    print(f"thinking mode: {payload.get('resolved_thinking_mode') or 'auto'}")
    print(f"tools: {', '.join(payload['tool_ids']) or '(none)'}")
    max_tool_rounds = payload.get("max_tool_rounds")
    print(
        "max tool rounds: "
        + (str(max_tool_rounds) if max_tool_rounds is not None else "unbounded")
    )
    print(f"max tool calls: {payload['max_tool_calls']}")
    timeout_seconds = payload.get("completion_timeout_seconds")
    if timeout_seconds is None:
        print("completion timeout: disabled")
    else:
        print(f"completion timeout: {float(timeout_seconds):.2f}s")
    print(f"approval mode: {payload['approval_mode']}")
    print(f"saved turns: {payload['saved_turns']}")
    print(f"session persistence: {'enabled' if payload['persist_session'] else 'disabled'}")


def _interactive_loop(
    *,
    args,
    controller: CodingConversationController,
    project_planner: CodingProjectPlannerController,
    llm_provider: LLMProvider,
    workspace_root: Path,
    model: str,
    thinking_mode: str,
    session: CodingCliSession,
    product_paths: CodeProductPaths,
    tool_ids: Sequence[str],
    acceptance_criteria: Sequence[str],
    max_tool_rounds: int | None,
    max_tool_calls: int,
    completion_timeout_seconds: float | None,
    persist_session: bool,
    run_root: Path,
    approval_state: CodeToolApprovalState,
    approval_mode: str,
    progress_renderer: CodeProgressRenderer,
    control_logger: CodeRunEventLogger | None,
) -> int:
    print(CODE_PRODUCT_NAME)
    print(f"workspace: {workspace_root}")
    print(f"session: {session.session_id}")
    if session.turns:
        print(f"resumed: {len(session.turns)} prior turns")
    print("enter a coding task, or /help")
    _log_code_event(
        control_logger,
        "interactive.started",
        workspace_root=str(workspace_root),
        session_id=session.session_id,
    )
    while True:
        try:
            raw = input("dancode> ")
        except EOFError:
            _log_code_event(control_logger, "interactive.exited", reason="eof")
            print()
            return 0
        except KeyboardInterrupt:
            _log_code_event(control_logger, "interactive.exited", reason="keyboard_interrupt")
            print()
            return 0

        objective = str(raw or "").strip()
        if not objective:
            continue
        if objective in {"/exit", "exit", "quit", ":q"}:
            _log_code_event(control_logger, "interactive.exited", reason="user_exit")
            return 0
        if objective in {"/help", "help"}:
            _log_code_event(control_logger, "interactive.command.executed", command="help")
            _print_repl_help()
            continue
        if objective == "/tools":
            _log_code_event(control_logger, "interactive.command.executed", command="tools")
            _print_tool_catalog(as_json=False)
            continue
        if objective == "/status":
            _log_code_event(control_logger, "interactive.command.executed", command="status")
            _print_session_status(
                session=session,
                workspace_root=workspace_root,
                product_paths=product_paths,
                model=model,
                thinking_mode=thinking_mode,
                tool_ids=tool_ids,
                persist_session=persist_session,
                approval_mode=approval_mode,
                completion_timeout_seconds=completion_timeout_seconds,
            )
            continue
        if objective == "/history":
            _log_code_event(control_logger, "interactive.command.executed", command="history")
            _print_session_history(session)
            continue
        if objective == "/summary":
            _log_code_event(control_logger, "interactive.command.executed", command="summary")
            _print_session_rollup(session)
            continue
        if objective in {"/reset", "/clear"}:
            session = CodingCliSession(workspace_root=str(workspace_root))
            if control_logger is not None:
                control_logger.update_context(session_id=session.session_id)
            if persist_session:
                save_code_product_session(product_paths, session)
            _log_code_event(control_logger, "interactive.command.executed", command="reset")
            print("session context cleared")
            continue
        _log_code_event(control_logger, "interactive.turn.started", objective=objective)
        outcome = asyncio.run(
            _run_orchestrated_turn(
                args=args,
                controller=controller,
                project_planner=project_planner,
                llm_provider=llm_provider,
                workspace_root=workspace_root,
                model=model,
                objective=objective,
                session=session,
                tool_ids=tool_ids,
                acceptance_criteria=acceptance_criteria,
                evidence_summaries=[],
                max_tool_rounds=max_tool_rounds,
                max_tool_calls=max_tool_calls,
                completion_timeout_seconds=completion_timeout_seconds,
                approval_mode=approval_mode,
                thinking_mode=thinking_mode,
                run_root=run_root,
                task_id_base=str(args.task_id),
                benchmark_context=None,
                approval_callback=approval_state,
                progress_renderer=progress_renderer,
                control_logger=control_logger,
            )
        )
        _log_code_event(
            control_logger,
            "interactive.turn.completed",
            objective=objective,
            status=outcome.status,
            report_count=len(outcome.reports),
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
    completion_timeout_seconds = _effective_completion_timeout_seconds(
        args.completion_timeout_seconds,
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
        interactive=args.objective is None and not bool(args.swebench_instance_file),
    )
    swebench: SweBenchRunContext | None = None
    run_objective = str(args.objective or "").strip() or None
    task_id_base = str(args.task_id or "").strip() or "coding-organ-task"
    if args.swebench_instance_file:
        try:
            instance_path = _resolve_user_path(
                args.swebench_instance_file,
                base_dir=workspace_root,
            )
            predictions_path = (
                _resolve_user_path(
                    args.swebench_predictions_path,
                    base_dir=workspace_root,
                )
                if args.swebench_predictions_path
                else None
            )
            swebench_instance = _load_swebench_instance(
                instance_path,
                base_dir=workspace_root,
            )
            swebench = _build_swebench_context(
                swebench_instance,
                instance_file=instance_path,
                predictions_path=predictions_path,
                operator_objective=run_objective,
                task_id_base=(
                    swebench_instance.instance_id
                    if task_id_base == "coding-organ-task"
                    else task_id_base
                ),
            )
        except Exception as exc:
            parser.error(str(exc))
        run_objective = swebench.objective
        task_id_base = swebench.task_id_base

    control_logger = CodeRunEventLogger(
        path=Path(product_paths.control_log),
        stream_kind="control_plane",
        session_id=session.session_id,
        organism_id=str(args.organism_id),
        organ_id="dan-code.control-plane",
    )
    _log_code_event(
        control_logger,
        "cli.started",
        workspace_root=str(workspace_root),
        session_id=session.session_id,
        persist_session=persist_session,
        interactive=run_objective is None,
        swebench_mode=swebench is not None,
        objective=run_objective,
        control_log_path=str(control_logger.path),
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
                completion_timeout_seconds=config.completion_timeout_seconds,
                thinking_mode=config.thinking_mode,
                persist_session=persist_session,
                approval_mode=approval_mode,
            ),
            "initialized": True,
        }
        _print_config_payload(payload, as_json=bool(args.json))
        _log_code_event(control_logger, "cli.completed", exit_code=0, mode="init")
        control_logger.close()
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
            completion_timeout_seconds=completion_timeout_seconds,
            thinking_mode=thinking_mode,
            persist_session=persist_session,
            approval_mode=approval_mode,
        )
        _print_config_payload(payload, as_json=bool(args.json))
        _log_code_event(control_logger, "cli.completed", exit_code=0, mode="show_config")
        control_logger.close()
        return 0

    try:
        live_model = _resolve_live_model(args.model, product_config=product_config)
        provider = _build_live_provider(
            live_model,
            api_key=args.api_key,
            base_url=args.base_url,
        )
    except Exception as exc:
        _log_code_event(
            control_logger,
            "cli.failed",
            error_type=type(exc).__name__,
            error=str(exc),
            mode="provider_build",
        )
        control_logger.close()
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
        stream_text_responses=bool(args.show_model_trace),
        provider_request_overrides=_provider_request_overrides_for_thinking_mode(
            thinking_mode,
            completion_timeout_seconds=completion_timeout_seconds,
        ),
        event_callback=progress_renderer,
    )
    project_planner = CodingProjectPlannerController(
        provider=provider,
        model=live_model,
        stream_text_responses=bool(args.show_model_trace),
        provider_request_overrides=_provider_request_overrides_for_thinking_mode(
            thinking_mode,
            completion_timeout_seconds=completion_timeout_seconds,
        ),
        event_callback=progress_renderer,
    )

    effective_acceptance_criteria = _dedupe(
        [
            *list(acceptance_criteria),
            *list(swebench.acceptance_criteria if swebench is not None else []),
        ]
    ) or list(acceptance_criteria)
    effective_evidence_summaries = _dedupe(
        [
            *list(args.evidence_summaries),
            *list(swebench.evidence_summaries if swebench is not None else []),
        ]
    )

    if run_objective is None:
        try:
            exit_code = _interactive_loop(
                args=args,
                controller=conversation_controller,
                project_planner=project_planner,
                llm_provider=provider,
                workspace_root=workspace_root,
                model=live_model,
                thinking_mode=thinking_mode,
                session=session,
                product_paths=product_paths,
                tool_ids=tool_ids,
                acceptance_criteria=effective_acceptance_criteria,
                max_tool_rounds=max_tool_rounds,
                max_tool_calls=max_tool_calls,
                completion_timeout_seconds=completion_timeout_seconds,
                persist_session=persist_session,
                run_root=run_root,
                approval_state=approval_state,
                approval_mode=approval_mode,
                progress_renderer=progress_renderer,
                control_logger=control_logger,
            )
        except Exception as exc:
            _log_code_event(
                control_logger,
                "cli.failed",
                error_type=type(exc).__name__,
                error=str(exc),
                mode="interactive",
            )
            control_logger.close()
            raise
        _log_code_event(
            control_logger,
            "cli.completed",
            exit_code=exit_code,
            mode="interactive",
        )
        control_logger.close()
        return exit_code

    try:
        outcome = asyncio.run(
            _run_orchestrated_turn(
                args=args,
                controller=conversation_controller,
                project_planner=project_planner,
                llm_provider=provider,
                workspace_root=workspace_root,
                model=live_model,
                objective=run_objective,
                session=session,
                tool_ids=tool_ids,
                acceptance_criteria=effective_acceptance_criteria,
                evidence_summaries=effective_evidence_summaries,
                max_tool_rounds=max_tool_rounds,
                max_tool_calls=max_tool_calls,
                completion_timeout_seconds=completion_timeout_seconds,
                approval_mode=approval_mode,
                thinking_mode=thinking_mode,
                run_root=run_root,
                task_id_base=task_id_base,
                benchmark_context=(
                    swebench.benchmark_context if swebench is not None else None
                ),
                approval_callback=approval_state,
                progress_renderer=progress_renderer,
                control_logger=control_logger,
            )
        )
    except Exception as exc:
        _log_code_event(
            control_logger,
            "cli.failed",
            error_type=type(exc).__name__,
            error=str(exc),
            mode="single_turn",
        )
        control_logger.close()
        raise
    if swebench is not None and outcome.reports:
        final_report = outcome.reports[-1]
        if outcome.status == "completed" and final_report.status == "completed":
            _write_swebench_artifacts(
                final_report,
                swebench=swebench,
                workspace_root=workspace_root,
                model=live_model,
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
        exit_code = 0 if outcome.reports[-1].status == "completed" else 1
        _log_code_event(
            control_logger,
            "cli.completed",
            exit_code=exit_code,
            mode="single_turn",
            status=outcome.status,
            report_count=len(outcome.reports),
        )
        control_logger.close()
        return exit_code

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
    _log_code_event(
        control_logger,
        "cli.completed",
        exit_code=0,
        mode="single_turn",
        status=outcome.status,
        report_count=0,
    )
    control_logger.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
