"""Backend adapters for Chat/Agent V2 runs."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Protocol

from pydantic import BaseModel, Field

from dan.server.chat_v2 import AgentRunEvent, normalize_token_usage
from dan.server.chat_v2_organism import map_organism_log_row_to_agent_event
from dan.server.chat_v2_store import AgentRunRecord, ChatV2Store, V2TaskRecord


AgentEventSink = Callable[[AgentRunEvent], None]


class AgentBackendRunRequest(BaseModel):
    """Normalized request sent from the V2 control plane into an Agent backend."""

    task_id: str
    run_id: str
    objective: str
    workspace_root: str
    workspace_id: str = ""
    thread_id: str = ""
    surface_turn_id: str = ""
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    history: list[dict[str, str]] = Field(default_factory=list)
    reply_context: dict[str, Any] = Field(default_factory=dict)
    surface_context: dict[str, Any] = Field(default_factory=dict)
    profile_policy: dict[str, Any] = Field(default_factory=dict)
    mutation_policy: dict[str, Any] = Field(default_factory=dict)
    approval_policy: dict[str, Any] = Field(default_factory=dict)
    tool_policy: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentBackendRunResult(BaseModel):
    """Final backend result projected into the V2 Agent run plane."""

    status: str
    backend: str
    summary: str = ""
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    trace_refs: list[str] = Field(default_factory=list)
    token_usage: dict[str, int] = Field(default_factory=dict)
    token_usage_rounds: list[dict[str, Any]] = Field(default_factory=list)
    raw_result: dict[str, Any] = Field(default_factory=dict)


class AgentBackendAdapter(Protocol):
    """Backend interface used by V2 Agent runs."""

    backend_name: str

    async def run(
        self,
        request: AgentBackendRunRequest,
        emit_event: AgentEventSink,
    ) -> AgentBackendRunResult:
        ...


class DeterministicAgentBackendAdapter:
    """Small local backend used for tests and provider-free smoke checks."""

    backend_name = "deterministic"

    async def run(
        self,
        request: AgentBackendRunRequest,
        emit_event: AgentEventSink,
    ) -> AgentBackendRunResult:
        usage_delta = {
            "prompt_tokens": max(1, len(request.objective.split())),
            "completion_tokens": 8,
            "total_tokens": max(1, len(request.objective.split())) + 8,
        }
        events = [
            AgentRunEvent(
                type="planned",
                run_id=request.run_id,
                task_id=request.task_id,
                summary="Planned deterministic Agent run.",
                source_event_type="chat_v2.backend.deterministic.planned",
                payload={"backend": self.backend_name},
            ),
            AgentRunEvent(
                type="worker_started",
                run_id=request.run_id,
                task_id=request.task_id,
                summary="Started deterministic Agent worker.",
                source_event_type="chat_v2.backend.deterministic.worker_started",
                payload={
                    "workspace_root": request.workspace_root,
                    "attachment_count": len(request.attachments),
                },
            ),
            AgentRunEvent(
                type="token_usage_recorded",
                run_id=request.run_id,
                task_id=request.task_id,
                summary="Token usage for deterministic round 1.",
                source_event_type="chat_v2.backend.deterministic.token_usage",
                token_usage_delta=usage_delta,
                token_usage_total=usage_delta,
                token_usage_round={
                    "round": 1,
                    "model": "deterministic",
                    "backend": self.backend_name,
                    "delta": usage_delta,
                    "total": usage_delta,
                },
                payload={"backend": self.backend_name},
            ),
            AgentRunEvent(
                type="completed",
                run_id=request.run_id,
                task_id=request.task_id,
                summary=_deterministic_summary(request.objective),
                artifact_refs=_attachment_artifact_refs(request.attachments),
                source_event_type="chat_v2.backend.deterministic.completed",
                payload={"backend": self.backend_name},
            ),
        ]
        for event in events:
            emit_event(event)
        return AgentBackendRunResult(
            status="completed",
            backend=self.backend_name,
            summary=events[-1].summary,
            artifact_refs=events[-1].artifact_refs,
            token_usage=usage_delta,
            token_usage_rounds=[events[2].token_usage_round],
        )


class SuperDanBackendAdapter:
    """Direct callable wrapper around Super DAN's live execution lane."""

    backend_name = "super_dan"

    async def run(
        self,
        request: AgentBackendRunRequest,
        emit_event: AgentEventSink,
    ) -> AgentBackendRunResult:
        mutation_mode = str(
            request.mutation_policy.get("mode")
            or request.mutation_policy.get("permission")
            or "workspace_mutation"
        ).strip()
        if mutation_mode in {"read_only", "readonly", "none", "deny"}:
            event = AgentRunEvent(
                type="blocked",
                run_id=request.run_id,
                task_id=request.task_id,
                summary="Super DAN backend requires workspace mutation permission.",
                source_event_type="chat_v2.backend.super_dan.blocked",
                payload={
                    "backend": self.backend_name,
                    "mutation_policy": dict(request.mutation_policy),
                },
            )
            emit_event(event)
            return AgentBackendRunResult(
                status="blocked",
                backend=self.backend_name,
                summary=event.summary,
            )

        super_cli = _load_super_dan_cli()
        workspace_root = super_cli.normalize_workspace_root(request.workspace_root or "~")
        effective_objective = _objective_with_surface_context(request)
        args = _build_super_dan_args(
            super_cli,
            request,
            workspace_root=workspace_root,
            objective=effective_objective,
        )
        report = super_cli.run_super_organism_demo(
            effective_objective,
            organism_id=str(getattr(args, "organism_id", "") or "super-dan-20"),
            cell_count=int(getattr(args, "cell_count", 20) or 20),
            active_cell_cap=int(getattr(args, "active_cell_cap", 8) or 8),
        )
        if not super_cli._supports_live_execution(report, args):
            event = AgentRunEvent(
                type="blocked",
                run_id=request.run_id,
                task_id=request.task_id,
                summary="No Super DAN live backend matched this Agent objective.",
                source_event_type="chat_v2.backend.super_dan.unsupported",
                payload={"backend": self.backend_name, "objective": request.objective},
            )
            emit_event(event)
            return AgentBackendRunResult(
                status="blocked",
                backend=self.backend_name,
                summary=event.summary,
            )

        workdir, turn_number = super_cli._build_super_run_workdir(workspace_root)
        event_log_path = workdir / "events.jsonl"
        trace_id = super_cli.new_trace_id()
        token_usage_rounds: list[dict[str, Any]] = []
        hook_runtime = super_cli.SuperHookRuntime(
            state_root=workspace_root / ".dan-super" / "state",
            run_id=request.run_id,
            turn_id=str(turn_number),
            task_id=request.task_id,
            trace_id=trace_id,
            reactivity_profile=str(
                request.profile_policy.get("reactivity")
                or getattr(args, "reactivity", "balanced")
                or "balanced"
            ),
            worktree_parallelism=max(
                0,
                int(request.tool_policy.get("worktree_parallelism") or 0),
            ),
        )

        def _on_raw_row(row: dict[str, Any]) -> None:
            event = map_organism_log_row_to_agent_event(
                dict(row),
                run_id=request.run_id,
                task_id=request.task_id,
                source_event_path=str(event_log_path),
            )
            if event.token_usage_round:
                token_usage_rounds.append(dict(event.token_usage_round))
            emit_event(event)

        event_logger = super_cli.SuperRunEventLogger(
            path=event_log_path,
            session_id=super_cli._super_session_id(workspace_root),
            turn_id=str(turn_number),
            task_id=request.task_id,
            organism_id=report.organism_id,
            organ_id="super-dan.live",
            trace_id=trace_id,
            progress_callback=_on_raw_row,
            hook_runtime=hook_runtime,
        )
        live_result: dict[str, Any] = {}
        try:
            super_cli._log_live_event(
                event_logger,
                "run.log.started",
                trace_id=trace_id,
                task_id=request.task_id,
                turn_number=turn_number,
                objective=report.target,
                original_objective=request.objective,
                objective_kind="general",
                workspace_root=str(workspace_root),
                workdir=str(workdir),
                requested_model=str(getattr(args, "model", "") or "").strip() or None,
                backend=self.backend_name,
            )
            try:
                model = super_cli._resolve_live_model(getattr(args, "model", None))
                super_cli._log_live_event(
                    event_logger,
                    "provider.build.started",
                    requested_model=str(getattr(args, "model", "") or "").strip() or None,
                    model=model,
                    base_url=str(getattr(args, "base_url", "") or "").strip() or None,
                )
                provider = super_cli._build_live_provider(
                    model,
                    api_key=getattr(args, "api_key", None),
                    base_url=getattr(args, "base_url", None),
                )
                super_cli._log_live_event(
                    event_logger,
                    "provider.build.completed",
                    model=model,
                    base_url=str(getattr(args, "base_url", "") or "").strip() or None,
                )
            except Exception as exc:
                super_cli._log_live_event(
                    event_logger,
                    "provider.build.failed",
                    error_type=type(exc).__name__,
                    error=str(exc),
                    requested_model=str(getattr(args, "model", "") or "").strip() or None,
                    base_url=str(getattr(args, "base_url", "") or "").strip() or None,
                )
                super_cli._log_live_event(
                    event_logger,
                    "run.log.failed",
                    trace_id=trace_id,
                    task_id=request.task_id,
                    status="failed",
                    error_type=type(exc).__name__,
                    error=str(exc),
                    event_log_path=str(event_log_path),
                    event_log_schema=super_cli.ORGANISM_LOG_SCHEMA_VERSION,
                )
                return AgentBackendRunResult(
                    status="failed",
                    backend=self.backend_name,
                    summary=str(exc),
                    trace_refs=[str(event_log_path)],
                    raw_result={"error": str(exc), "error_type": type(exc).__name__},
                )

            try:
                live_result = await super_cli._run_live_execution_with_provider_cleanup(
                    report,
                    args,
                    model=model,
                    provider=provider,
                    run_trace_id=trace_id,
                    run_task_id=request.task_id,
                    event_logger=event_logger,
                    objective_kind="general",
                )
            except Exception as exc:
                super_cli._log_live_event(
                    event_logger,
                    "run.log.failed",
                    trace_id=trace_id,
                    task_id=request.task_id,
                    status="failed",
                    error_type=type(exc).__name__,
                    error=str(exc),
                    event_log_path=str(event_log_path),
                    event_log_schema=super_cli.ORGANISM_LOG_SCHEMA_VERSION,
                )
                return AgentBackendRunResult(
                    status="failed",
                    backend=self.backend_name,
                    summary=str(exc),
                    trace_refs=[str(event_log_path)],
                    raw_result={"error": str(exc), "error_type": type(exc).__name__},
                )

            live_result = {
                **dict(live_result or {}),
                "event_log_path": str(event_log_path),
                "event_log_schema": super_cli.ORGANISM_LOG_SCHEMA_VERSION,
                "hook_state": event_logger.hook_state_snapshot(),
            }
            super_cli._log_live_event(
                event_logger,
                "run.log.completed",
                trace_id=trace_id,
                task_id=request.task_id,
                status=live_result.get("status"),
                objective_kind="general",
                validation_passed=bool(
                    dict(live_result.get("validation") or {}).get("passed")
                ),
                tool_calls=int(live_result.get("tool_calls") or 0),
                event_count=int(live_result.get("event_count") or 0),
                token_usage=normalize_token_usage(live_result.get("token_usage")),
                event_log_path=str(event_log_path),
                event_log_schema=super_cli.ORGANISM_LOG_SCHEMA_VERSION,
            )
        finally:
            event_logger.close()

        status = str(live_result.get("status") or "failed")
        artifact_refs = [
            {"path": str(path)}
            for path in (
                live_result.get("mutated_paths")
                or live_result.get("files")
                or []
            )
            if path
        ]
        return AgentBackendRunResult(
            status=status,
            backend=self.backend_name,
            summary=str(
                live_result.get("summary")
                or live_result.get("error")
                or f"Super DAN run {status}."
            ),
            artifact_refs=artifact_refs,
            trace_refs=[str(event_log_path)],
            token_usage=normalize_token_usage(live_result.get("token_usage")),
            token_usage_rounds=token_usage_rounds,
            raw_result=live_result,
        )


async def run_agent_backend(
    store: ChatV2Store,
    run_id: str,
    *,
    adapter: AgentBackendAdapter | None = None,
    backend_name: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> AgentBackendRunResult:
    """Execute a persisted V2 Agent run through a backend adapter."""

    run = store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    task = store.get_task(run.task_id)
    request = build_agent_backend_request(run, task, overrides=overrides)
    try:
        selected = adapter or select_agent_backend_adapter(request, backend_name=backend_name)
    except Exception as exc:
        event = AgentRunEvent(
            type="failed",
            run_id=run_id,
            task_id=run.task_id,
            summary=str(exc),
            source_event_type="chat_v2.backend.selection_failed",
            payload={
                "requested_backend": backend_name or "",
                "error_type": type(exc).__name__,
            },
        )
        store.record_agent_event(event)
        result = AgentBackendRunResult(
            status="failed",
            backend=str(backend_name or "unknown"),
            summary=str(exc),
            raw_result={"error": str(exc), "error_type": type(exc).__name__},
        )
        store.update_run_metadata(
            run_id,
            {
                "selected_backend": result.backend,
                "backend_result": result.model_dump(mode="json"),
            },
            status="failed",
        )
        return result
    store.update_run_metadata(
        run_id,
        {
            "selected_backend": selected.backend_name,
            "backend_selection": _backend_selection_reason(selected.backend_name),
        },
        status="running",
    )

    def _emit(event: AgentRunEvent) -> None:
        normalized = event.model_copy(
            update={
                "run_id": event.run_id or run_id,
                "task_id": event.task_id or run.task_id,
            }
        )
        store.record_agent_event(normalized)

    try:
        result = await selected.run(request, _emit)
    except Exception as exc:
        failed = AgentRunEvent(
            type="failed",
            run_id=run_id,
            task_id=run.task_id,
            summary=str(exc),
            source_event_type="chat_v2.backend.failed",
            payload={
                "backend": selected.backend_name,
                "error_type": type(exc).__name__,
            },
        )
        _emit(failed)
        result = AgentBackendRunResult(
            status="failed",
            backend=selected.backend_name,
            summary=str(exc),
            raw_result={"error": str(exc), "error_type": type(exc).__name__},
        )

    store.update_run_metadata(
        run_id,
        {
            "selected_backend": selected.backend_name,
            "backend_result": result.model_dump(mode="json"),
        },
        status=_terminal_status(result.status),
    )
    return result


def build_agent_backend_request(
    run: AgentRunRecord,
    task: V2TaskRecord | None = None,
    *,
    overrides: dict[str, Any] | None = None,
) -> AgentBackendRunRequest:
    payload = dict(run.command.payload or {})
    overrides = dict(overrides or {})
    profile_policy = _merged_dict(
        payload.get("profile_policy"),
        run.metadata.get("profile_policy"),
        overrides.get("profile_policy"),
        overrides.get("profile"),
    )
    mutation_policy = _merged_dict(
        {"mode": "workspace_mutation", "external_side_effects": "deny"},
        payload.get("mutation_policy"),
        run.metadata.get("mutation_policy"),
        overrides.get("mutation_policy"),
    )
    approval_policy = _merged_dict(
        {"mode": "auto_within_workspace"},
        payload.get("approval_policy"),
        run.metadata.get("approval_policy"),
        overrides.get("approval_policy"),
    )
    tool_policy = _merged_dict(
        {"max_tool_calls": payload.get("max_tool_calls") or 128},
        payload.get("tool_policy"),
        run.metadata.get("tool_policy"),
        overrides.get("tool_policy"),
    )
    workspace_root = str(
        overrides.get("workspace_root")
        or payload.get("workspace_root")
        or run.workspace_root
        or (task.workspace_root if task is not None else "")
        or "~"
    )
    workspace_id = str(
        overrides.get("workspace_id")
        or payload.get("workspace_id")
        or run.workspace_id
        or (task.workspace_id if task is not None else "")
        or workspace_root
    )
    history = _normalize_history(payload.get("history"))
    return AgentBackendRunRequest(
        task_id=run.task_id,
        run_id=run.run_id,
        objective=str(overrides.get("objective") or payload.get("text") or ""),
        workspace_root=workspace_root,
        workspace_id=workspace_id,
        thread_id=run.thread_id,
        surface_turn_id=run.surface_turn_id,
        attachments=list(payload.get("attachments") or []),
        history=history,
        reply_context=dict(payload.get("reply_context") or {}),
        surface_context=dict(payload.get("surface_context") or {}),
        profile_policy=profile_policy,
        mutation_policy=mutation_policy,
        approval_policy=approval_policy,
        tool_policy=tool_policy,
        metadata={
            "queue_key": run.metadata.get("queue_key", ""),
            "topic_key": payload.get("topic_key", ""),
            "history_turn_count": len(history),
            **dict(overrides.get("metadata") or {}),
        },
    )


def select_agent_backend_adapter(
    request: AgentBackendRunRequest,
    *,
    backend_name: str | None = None,
) -> AgentBackendAdapter:
    requested = (
        backend_name
        or request.profile_policy.get("backend")
        or request.metadata.get("backend")
        or os.environ.get("DAN_CHAT_V2_AGENT_BACKEND")
        or "super_dan"
    )
    normalized = str(requested or "").strip().lower().replace("-", "_")
    if normalized in {"deterministic", "fake", "test"}:
        return DeterministicAgentBackendAdapter()
    if normalized in {"super_dan", "superdan", "super_organism"}:
        return SuperDanBackendAdapter()
    raise ValueError(f"Unsupported Agent backend: {requested}")


def _load_super_dan_cli():
    from dan.cli import super_organism as super_cli

    super_cli.load_env()
    return super_cli


def _build_super_dan_args(
    super_cli: Any,
    request: AgentBackendRunRequest,
    *,
    workspace_root: Path,
    objective: str | None = None,
):
    argv = [
        objective or request.objective,
        "--live",
        "--workspace",
        str(workspace_root),
        "--quiet-progress",
        "--json",
    ]
    model = request.profile_policy.get("model") or os.environ.get("DAN_CHAT_V2_AGENT_MODEL")
    if model:
        argv.extend(["--model", str(model)])
    base_url = request.profile_policy.get("base_url") or os.environ.get("DAN_CHAT_V2_AGENT_BASE_URL")
    if base_url:
        argv.extend(["--base-url", str(base_url)])
    max_tool_calls = request.tool_policy.get("max_tool_calls")
    if max_tool_calls:
        argv.extend(["--max-tool-calls", str(max_tool_calls)])
    artifact_dir = request.profile_policy.get("artifact_dir")
    if artifact_dir:
        argv.extend(["--artifact-dir", str(artifact_dir)])
    args = super_cli.build_parser().parse_args(argv)
    setattr(args, "_live_explicit", True)
    setattr(args, "_model_explicit", bool(model))
    setattr(args, "_artifact_dir_explicit", bool(artifact_dir))
    setattr(args, "_implicit_live", False)
    setattr(args, "_code_like_live", True)
    setattr(args, "_stdin_is_tty", False)
    return args


def _terminal_status(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"completed", "failed", "blocked", "stopped"}:
        return normalized
    return "failed" if normalized else "completed"


def _backend_selection_reason(backend_name: str) -> str:
    if backend_name == "super_dan":
        return "Super DAN is the first V2 Agent backend for workspace build/operator tasks."
    if backend_name == "deterministic":
        return "Deterministic backend selected for provider-free test execution."
    return "Selected by V2 Agent backend policy."


def _merged_dict(*values: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for value in values:
        if isinstance(value, dict):
            merged.update(value)
    return merged


def _normalize_history(raw_history: Any) -> list[dict[str, str]]:
    if not isinstance(raw_history, list):
        return []
    history: list[dict[str, str]] = []
    for item in raw_history[-24:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        if role not in {"user", "assistant"}:
            continue
        content = _compact_context_text(item.get("content"), limit=1600)
        if content:
            history.append({"role": role, "content": content})
    return history


def _objective_with_surface_context(request: AgentBackendRunRequest) -> str:
    objective = " ".join(str(request.objective or "").split())
    context_lines: list[str] = []
    reply_text = _compact_context_text(
        request.reply_context.get("reply_to_text"),
        limit=1200,
    )
    if reply_text:
        context_lines.append(f"Replied-to message: {reply_text}")

    history = _history_without_current_objective(request.history, objective)
    if history:
        context_lines.append("Recent Telegram chat:")
        for turn in history[-10:]:
            role = "User" if turn["role"] == "user" else "Assistant"
            context_lines.append(f"- {role}: {turn['content']}")

    if not context_lines:
        return objective
    return (
        f"Operator request: {objective}\n\n"
        "Telegram context for resolving references only:\n"
        + "\n".join(context_lines)
        + "\n\nSatisfy the operator request above; do not treat the context as extra tasks."
    )


def _history_without_current_objective(
    history: list[dict[str, str]],
    objective: str,
) -> list[dict[str, str]]:
    normalized_objective = " ".join(str(objective or "").split())
    trimmed = list(history)
    if trimmed and trimmed[-1].get("role") == "user":
        last = " ".join(str(trimmed[-1].get("content") or "").split())
        if last == normalized_objective:
            trimmed = trimmed[:-1]
    return trimmed


def _compact_context_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _deterministic_summary(objective: str) -> str:
    text = " ".join(str(objective or "").split())
    if not text:
        return "Completed deterministic Agent run."
    return f"Completed deterministic Agent run for: {text[:160]}"


def _attachment_artifact_refs(attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        path = attachment.get("local_path")
        if path:
            refs.append({"path": str(path), "kind": attachment.get("kind") or "attachment"})
    return refs
