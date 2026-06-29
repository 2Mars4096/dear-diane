"""Backend adapters for Chat/Agent V2 runs."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Protocol

from pydantic import BaseModel, Field

from dan.notes import enrich_notes_surface_context
from dan.server.chat_v2 import AgentRunEvent, normalize_token_usage
from dan.server.chat_v2_organism import map_organism_log_row_to_agent_event
from dan.server.chat_v2_store import (
    AgentRunRecord,
    ChatV2Store,
    QueueItemRecord,
    V2TaskRecord,
)


AgentEventSink = Callable[[AgentRunEvent], None]


class AgentBackendStopped(RuntimeError):
    """Raised when a V2 Agent backend stops at an admitted safe checkpoint."""

    def __init__(self, message: str, *, checkpoint: str) -> None:
        super().__init__(message)
        self.checkpoint = checkpoint


class AgentBackendPaused(RuntimeError):
    """Raised when a V2 Agent backend pauses at an admitted safe checkpoint."""

    def __init__(self, message: str, *, checkpoint: str) -> None:
        super().__init__(message)
        self.checkpoint = checkpoint


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


class _CodexObservableTaskGraph:
    """Small live graph built from observable Codex CLI events."""

    def __init__(
        self,
        request: AgentBackendRunRequest,
        *,
        workspace_root: Path,
        objective: str,
    ) -> None:
        self.request = request
        self.workspace_root = workspace_root
        self.objective = " ".join(str(objective or request.objective or "").split())
        self.revision = 0
        self._item_counter = 0
        self._item_keys: dict[str, str] = {}
        self._tasks: dict[str, dict[str, Any]] = {}
        self._completed: set[str] = set()
        self._active: set[str] = set()
        self._branches: dict[str, list[str]] = {}

    def start(self) -> AgentRunEvent:
        request_goal = self.objective or "Handle the operator request"
        self._upsert_task(
            "codex-request",
            branch_id="request",
            goal=f"Receive request: {request_goal[:140]}",
            state="done",
            validation=["The exact request is attached to this run."],
        )
        self._upsert_task(
            "codex-worker",
            branch_id="workspace",
            goal="Start Codex worker in the selected workspace",
            state="active",
            depends_on=["codex-request"],
            owned_paths=[str(self.workspace_root)],
            validation=["Codex CLI worker starts and begins emitting observable work items."],
        )
        return self._event(
            source="codex",
            reason="Codex worker started; observable task graph is now tracking emitted work items.",
            changed_task_ids=["codex-request", "codex-worker"],
            changed_branch_ids=["request", "workspace"],
        )

    def events_for_row(self, row: dict[str, Any]) -> list[AgentRunEvent]:
        row_type = str(row.get("type") or "").strip()
        if row_type in {"item.started", "item.completed"}:
            item = row.get("item") if isinstance(row.get("item"), dict) else {}
            item_task_id = self._task_id_for_item(item)
            branch_id = self._branch_for_item(item)
            self._upsert_task(
                item_task_id,
                branch_id=branch_id,
                goal=self._goal_for_item(item, row_type=row_type),
                state="active" if row_type == "item.started" else "done",
                depends_on=["codex-worker"],
                owned_paths=self._owned_paths_for_item(item),
                deliverables=self._deliverables_for_item(item),
                validation=self._validation_for_item(item, row_type=row_type),
            )
            return [
                self._event(
                    source="codex",
                    reason=(
                        "Codex started an observable work item."
                        if row_type == "item.started"
                        else "Codex completed an observable work item."
                    ),
                    changed_task_ids=[item_task_id],
                    changed_branch_ids=[branch_id],
                )
            ]
        if row_type == "turn.completed":
            return [self.finish("Codex completed the run and returned terminal output.")]
        return []

    def finish(self, reason: str) -> AgentRunEvent:
        self._active.discard("codex-worker")
        self._completed.add("codex-worker")
        if "codex-worker" in self._tasks:
            self._tasks["codex-worker"]["state"] = "done"
            self._tasks["codex-worker"]["status"] = "done"
        self._upsert_task(
            "codex-final",
            branch_id="answer",
            goal="Return the final user-facing response",
            state="done",
            depends_on=self._latest_done_dependencies() or ["codex-worker"],
            validation=["The Codex run reached terminal completion."],
        )
        return self._event(
            source="codex",
            reason=reason or "Codex completed the run.",
            changed_task_ids=["codex-worker", "codex-final"],
            changed_branch_ids=["workspace", "answer"],
        )

    def _upsert_task(
        self,
        task_id: str,
        *,
        branch_id: str,
        goal: str,
        state: str,
        depends_on: list[str] | None = None,
        owned_paths: list[str] | None = None,
        deliverables: list[str] | None = None,
        validation: list[str] | None = None,
    ) -> None:
        normalized_state = state if state in {"ready", "active", "done", "deferred"} else "ready"
        task = self._tasks.get(task_id, {})
        task.update(
            {
                "task_id": task_id,
                "branch_id": branch_id,
                "goal": goal or "Codex work item",
                "depends_on": list(depends_on or task.get("depends_on") or []),
                "owned_paths": list(owned_paths or task.get("owned_paths") or []),
                "deliverables": list(deliverables or task.get("deliverables") or []),
                "validation": list(validation or task.get("validation") or []),
                "state": normalized_state,
                "status": normalized_state,
                "parallel_safe": branch_id not in {"workspace", "answer"},
            }
        )
        self._tasks[task_id] = task
        branch_tasks = self._branches.setdefault(branch_id, [])
        if task_id not in branch_tasks:
            branch_tasks.append(task_id)
        self._active.discard(task_id)
        self._completed.discard(task_id)
        if normalized_state == "active":
            self._active.add(task_id)
        elif normalized_state == "done":
            self._completed.add(task_id)

    def _event(
        self,
        *,
        source: str,
        reason: str,
        changed_task_ids: list[str],
        changed_branch_ids: list[str],
    ) -> AgentRunEvent:
        self.revision += 1
        graph_state = self._graph_state(
            source=source,
            reason=reason,
            changed_task_ids=changed_task_ids,
            changed_branch_ids=changed_branch_ids,
        )
        return AgentRunEvent(
            type="worker_started",
            run_id=self.request.run_id,
            task_id=self.request.task_id,
            summary=reason,
            source_event_type="live.task_graph.updated",
            payload={
                "backend": "codex",
                "source": source,
                "task_graph_state": graph_state,
                "plan_context": {
                    "enabled": True,
                    "usable": True,
                    "persistence": "run_memory",
                    "task_graph": list(graph_state["tasks"]),
                    "ready_task_ids": list(graph_state["ready_task_ids"]),
                    "active_task_ids": list(graph_state["active_task_ids"]),
                    "completed_task_ids": list(graph_state["completed_task_ids"]),
                    "deferred_task_ids": list(graph_state["deferred_task_ids"]),
                    "task_graph_state": graph_state,
                },
            },
        )

    def _graph_state(
        self,
        *,
        source: str,
        reason: str,
        changed_task_ids: list[str],
        changed_branch_ids: list[str],
    ) -> dict[str, Any]:
        tasks = list(self._tasks.values())
        ready = [
            str(task["task_id"])
            for task in tasks
            if task.get("state") == "ready"
        ]
        active = sorted(self._active)
        completed = sorted(self._completed)
        deferred = [
            str(task["task_id"])
            for task in tasks
            if task.get("state") == "deferred"
        ]
        branches = []
        for branch_id, task_ids in self._branches.items():
            branches.append(
                {
                    "branch_id": branch_id,
                    "task_ids": list(task_ids),
                    "ready_task_ids": [task_id for task_id in task_ids if task_id in ready],
                    "active_task_ids": [task_id for task_id in task_ids if task_id in active],
                    "completed_task_ids": [task_id for task_id in task_ids if task_id in completed],
                    "deferred_task_ids": [task_id for task_id in task_ids if task_id in deferred],
                }
            )
        version_id = f"codex.r{self.revision}"
        return {
            "schema": "super_dan_task_graph_v1",
            "revision": self.revision,
            "version_id": version_id,
            "root_version_id": "codex",
            "base_version_id": "codex",
            "parent_version_ids": [f"codex.r{self.revision - 1}"] if self.revision > 1 else [],
            "source": source,
            "update_scope": "observable_event",
            "update_reason": reason,
            "changed_task_ids": list(dict.fromkeys(changed_task_ids)),
            "changed_branch_ids": list(dict.fromkeys(changed_branch_ids)),
            "tasks": tasks,
            "ready_task_ids": ready,
            "active_task_ids": active,
            "completed_task_ids": completed,
            "deferred_task_ids": deferred,
            "plan_generation_queue_length": 4,
            "plan_execution_queue_length": 4,
            "task_execution_queue_length": 4,
            "parallel_groups": self._parallel_groups(),
            "branches": branches,
            "branch_refs": [
                {
                    "branch_id": branch_id,
                    "version_id": f"{version_id}.{_safe_graph_token(branch_id)}",
                    "parent_version_id": f"codex.r{self.revision - 1}" if self.revision > 1 else "codex",
                    "base_version_id": "codex",
                    "local_revision": self.revision,
                    "changed": branch_id in set(changed_branch_ids),
                }
                for branch_id in self._branches
            ],
        }

    def _parallel_groups(self) -> list[list[str]]:
        groups = [
            [task_id for task_id in task_ids if task_id in self._active or task_id in self._completed]
            for task_ids in self._branches.values()
        ]
        return [group for group in groups if len(group) > 1]

    def _task_id_for_item(self, item: dict[str, Any]) -> str:
        stable = _first_compact_text(
            item.get("id"),
            item.get("call_id"),
            item.get("item_id"),
            item.get("name"),
            limit=120,
        )
        if not stable:
            command = _first_compact_text(item.get("command"), item.get("cmd"), limit=120)
            if command:
                stable = f"command:{command}"
        if not stable:
            item_type = str(item.get("type") or "item").strip()
            text = _first_compact_text(
                item.get("text"),
                item.get("summary"),
                item.get("message"),
                limit=120,
            )
            if text:
                stable = f"{item_type}:{text}"
        if not stable:
            self._item_counter += 1
            stable = f"item-{self._item_counter}"
        if stable not in self._item_keys:
            self._item_keys[stable] = f"codex-{_safe_graph_token(stable)}"
        return self._item_keys[stable]

    def _branch_for_item(self, item: dict[str, Any]) -> str:
        item_type = str(item.get("type") or "").lower()
        if item_type == "agent_message":
            return "answer"
        if "file" in item_type or self._deliverables_for_item(item):
            return "workspace"
        if item.get("command") or item.get("cmd"):
            return "workspace"
        if "tool" in item_type:
            return "workspace"
        return "codex"

    def _goal_for_item(self, item: dict[str, Any], *, row_type: str) -> str:
        item_type = str(item.get("type") or "").strip()
        command = _first_compact_text(item.get("command"), item.get("cmd"), limit=160)
        text = _first_compact_text(item.get("text"), item.get("summary"), item.get("message"), limit=160)
        if command:
            return f"Run `{command}`"
        if item_type == "agent_message":
            return "Draft the user-facing response"
        if "file" in item_type:
            return "Update workspace artifact"
        if text:
            return text
        if item_type:
            return f"{'Start' if row_type == 'item.started' else 'Complete'} {item_type.replace('_', ' ')}"
        return "Codex work item"

    def _owned_paths_for_item(self, item: dict[str, Any]) -> list[str]:
        return [
            ref["path"]
            for ref in _codex_artifact_refs(item)
            if isinstance(ref.get("path"), str)
        ][:8]

    def _deliverables_for_item(self, item: dict[str, Any]) -> list[str]:
        return self._owned_paths_for_item(item)

    def _validation_for_item(self, item: dict[str, Any], *, row_type: str) -> list[str]:
        if row_type == "item.completed":
            return ["Codex emitted the item completion event."]
        return ["Codex emitted the item start event."]

    def _latest_done_dependencies(self) -> list[str]:
        ordered = [
            task_id
            for task_id in self._tasks
            if task_id in self._completed and task_id != "codex-request"
        ]
        return ordered[-4:]


def _safe_graph_token(value: str) -> str:
    token = re.sub(r"[^a-zA-Z0-9]+", "-", str(value).strip()).strip("-").lower()
    return token[:48] or "item"


class AgentBackendRuntime:
    """Runtime bridge from backend execution into durable V2 run control."""

    def __init__(self, store: ChatV2Store, *, run_id: str, task_id: str) -> None:
        self._store = store
        self.run_id = run_id
        self.task_id = task_id

    def admit_checkpoint(
        self,
        checkpoint: str,
        *,
        limit: int = 16,
    ) -> list[QueueItemRecord]:
        """Admit pending checkpoint-append messages at a safe boundary."""

        return self._store.claim_queued_run_items(
            self.run_id,
            lane="append",
            checkpoint=checkpoint,
            limit=limit,
        )

    def raise_if_stop_requested(self, checkpoint: str) -> None:
        run = self._store.get_run(self.run_id)
        if run is None or not bool(run.metadata.get("stop_requested")):
            return
        event = self._store.confirm_agent_run_stopped(
            self.run_id,
            checkpoint=checkpoint,
        )
        summary = event.summary if event is not None else "Run stopped."
        raise AgentBackendStopped(summary, checkpoint=checkpoint)

    def raise_if_pause_requested(self, checkpoint: str) -> None:
        run = self._store.get_run(self.run_id)
        if run is None or not bool(run.metadata.get("pause_requested")):
            return
        event = self._store.confirm_agent_run_paused(
            self.run_id,
            checkpoint=checkpoint,
        )
        summary = event.summary if event is not None else "Run paused."
        raise AgentBackendPaused(summary, checkpoint=checkpoint)

    def raise_if_interrupted(self, checkpoint: str) -> None:
        self.raise_if_stop_requested(checkpoint)
        self.raise_if_pause_requested(checkpoint)


class AgentBackendAdapter(Protocol):
    """Backend interface used by V2 Agent runs."""

    backend_name: str

    async def run(
        self,
        request: AgentBackendRunRequest,
        emit_event: AgentEventSink,
        runtime: AgentBackendRuntime | None = None,
    ) -> AgentBackendRunResult:
        ...


class DeterministicAgentBackendAdapter:
    """Small local backend used for tests and provider-free smoke checks."""

    backend_name = "deterministic"

    async def run(
        self,
        request: AgentBackendRunRequest,
        emit_event: AgentEventSink,
        runtime: AgentBackendRuntime | None = None,
    ) -> AgentBackendRunResult:
        if runtime is not None:
            runtime.raise_if_interrupted("deterministic.start")
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


class CodexAgentBackendAdapter:
    """Subprocess-backed adapter for Codex CLI agent runs."""

    backend_name = "codex"

    async def run(
        self,
        request: AgentBackendRunRequest,
        emit_event: AgentEventSink,
        runtime: AgentBackendRuntime | None = None,
    ) -> AgentBackendRunResult:
        if runtime is not None:
            runtime.raise_if_interrupted("codex.start")

        workspace_root = Path(request.workspace_root or "~").expanduser().resolve(strict=False)
        objective = _objective_with_surface_context(request)
        codex_bin = _resolve_codex_binary(request)
        if not codex_bin:
            event = AgentRunEvent(
                type="blocked",
                run_id=request.run_id,
                task_id=request.task_id,
                summary="Codex CLI was not found on PATH.",
                source_event_type="chat_v2.backend.codex.missing_cli",
                payload={"backend": self.backend_name},
            )
            emit_event(event)
            return AgentBackendRunResult(
                status="blocked",
                backend=self.backend_name,
                summary=event.summary,
            )

        command = _build_codex_exec_command(
            codex_bin,
            request,
            workspace_root=workspace_root,
            objective=objective,
        )
        emit_event(
            AgentRunEvent(
                type="planned",
                run_id=request.run_id,
                task_id=request.task_id,
                summary="Prepared Codex Agent run.",
                source_event_type="chat_v2.backend.codex.planned",
                payload={
                    "backend": self.backend_name,
                    "workspace_root": str(workspace_root),
                    "model": _codex_model(request),
                    "reasoning_effort": _codex_reasoning_effort(request),
                    "sandbox": _codex_sandbox(request),
                },
            )
        )
        emit_event(
            AgentRunEvent(
                type="worker_started",
                run_id=request.run_id,
                task_id=request.task_id,
                summary="Started Codex CLI Agent worker.",
                source_event_type="chat_v2.backend.codex.worker_started",
                payload={
                    "backend": self.backend_name,
                    "command": _redacted_command_for_event(command),
                },
            )
        )
        codex_task_graph = _CodexObservableTaskGraph(
            request,
            workspace_root=workspace_root,
            objective=objective,
        )
        emit_event(codex_task_graph.start())

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(workspace_root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            event = AgentRunEvent(
                type="blocked",
                run_id=request.run_id,
                task_id=request.task_id,
                summary=f"Codex CLI executable was not found: {codex_bin}",
                source_event_type="chat_v2.backend.codex.missing_cli",
                payload={"backend": self.backend_name},
            )
            emit_event(event)
            return AgentBackendRunResult(
                status="blocked",
                backend=self.backend_name,
                summary=event.summary,
            )

        stderr_task = asyncio.create_task(_read_stream_text(process.stderr))
        final_summary = ""
        token_usage: dict[str, int] = {}
        token_usage_rounds: list[dict[str, Any]] = []
        artifact_refs: list[dict[str, Any]] = []
        raw_events: list[dict[str, Any]] = []

        assert process.stdout is not None
        async for raw_line in process.stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                emit_event(
                    AgentRunEvent(
                        type="model_text_delta",
                        run_id=request.run_id,
                        task_id=request.task_id,
                        summary=line[:300],
                        source_event_type="chat_v2.backend.codex.stdout",
                        payload={"backend": self.backend_name, "text": line},
                    )
                )
                continue
            if isinstance(row, dict):
                raw_events.append(row)
                for event in codex_task_graph.events_for_row(row):
                    emit_event(event)
                for event in _codex_events_from_json_row(
                    row,
                    request=request,
                    backend_name=self.backend_name,
                ):
                    if event.type == "completed" and event.summary:
                        final_summary = event.summary
                    elif event.type == "model_text_delta" and event.summary:
                        final_summary = _first_compact_text(
                            event.payload.get("text"),
                            event.summary,
                        )
                    if event.token_usage_round:
                        token_usage_rounds.append(dict(event.token_usage_round))
                    if event.token_usage_total:
                        token_usage = normalize_token_usage(event.token_usage_total)
                    if event.artifact_refs:
                        artifact_refs = _dedupe_artifact_refs(
                            [*artifact_refs, *event.artifact_refs]
                        )
                    emit_event(event)

        return_code = await process.wait()
        stderr_text = await stderr_task
        if return_code != 0:
            summary = _first_compact_text(
                final_summary,
                stderr_text,
                f"Codex exited with status {return_code}.",
            )
            emit_event(
                AgentRunEvent(
                    type="failed",
                    run_id=request.run_id,
                    task_id=request.task_id,
                    summary=summary,
                    source_event_type="chat_v2.backend.codex.failed",
                    payload={
                        "backend": self.backend_name,
                        "return_code": return_code,
                        "stderr": stderr_text[-4000:],
                    },
                )
            )
            return AgentBackendRunResult(
                status="failed",
                backend=self.backend_name,
                summary=summary,
                artifact_refs=artifact_refs,
                token_usage=token_usage,
                token_usage_rounds=token_usage_rounds,
                raw_result={
                    "return_code": return_code,
                    "stderr": stderr_text,
                    "events": raw_events[-100:],
                },
            )

        summary = _first_compact_text(final_summary, "Codex completed.")
        if not any(row.get("type") == "turn.completed" for row in raw_events):
            emit_event(codex_task_graph.finish("Codex exited after emitting observable work items."))
            emit_event(
                AgentRunEvent(
                    type="completed",
                    run_id=request.run_id,
                    task_id=request.task_id,
                    summary=summary,
                    artifact_refs=artifact_refs,
                    source_event_type="chat_v2.backend.codex.completed",
                    payload={
                        "backend": self.backend_name,
                        "return_code": return_code,
                        "final_text": final_summary,
                    },
                )
            )
        return AgentBackendRunResult(
            status="completed",
            backend=self.backend_name,
            summary=summary,
            artifact_refs=artifact_refs,
            token_usage=token_usage,
            token_usage_rounds=token_usage_rounds,
            raw_result={
                "return_code": return_code,
                "stderr": stderr_text,
                "events": raw_events[-100:],
            },
        )


class SuperDanBackendAdapter:
    """Direct callable wrapper around Super DAN's live execution lane."""

    backend_name = "super_dan"

    async def run(
        self,
        request: AgentBackendRunRequest,
        emit_event: AgentEventSink,
        runtime: AgentBackendRuntime | None = None,
    ) -> AgentBackendRunResult:
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
            if runtime is not None and _is_safe_backend_checkpoint(row):
                checkpoint = _checkpoint_name(row)
                runtime.admit_checkpoint(checkpoint)
                runtime.raise_if_interrupted(checkpoint)
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
            except AgentBackendStopped as exc:
                super_cli._log_live_event(
                    event_logger,
                    "run.log.stopped",
                    trace_id=trace_id,
                    task_id=request.task_id,
                    status="stopped",
                    checkpoint=exc.checkpoint,
                    event_log_path=str(event_log_path),
                    event_log_schema=super_cli.ORGANISM_LOG_SCHEMA_VERSION,
                )
                return AgentBackendRunResult(
                    status="stopped",
                    backend=self.backend_name,
                    summary=str(exc),
                    trace_refs=[str(event_log_path)],
                    raw_result={
                        "status": "stopped",
                        "checkpoint": exc.checkpoint,
                    },
                )
            except AgentBackendPaused as exc:
                super_cli._log_live_event(
                    event_logger,
                    "run.log.paused",
                    trace_id=trace_id,
                    task_id=request.task_id,
                    status="paused",
                    checkpoint=exc.checkpoint,
                    event_log_path=str(event_log_path),
                    event_log_schema=super_cli.ORGANISM_LOG_SCHEMA_VERSION,
                )
                return AgentBackendRunResult(
                    status="paused",
                    backend=self.backend_name,
                    summary=str(exc),
                    trace_refs=[str(event_log_path)],
                    raw_result={
                        "status": "paused",
                        "checkpoint": exc.checkpoint,
                    },
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
            final_text = str(live_result.get("summary") or "").strip()
            super_cli._log_live_event(
                event_logger,
                "run.log.completed",
                trace_id=trace_id,
                task_id=request.task_id,
                status=live_result.get("status"),
                summary=final_text,
                final_text=final_text,
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
                or ""
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
    runtime = AgentBackendRuntime(store, run_id=run_id, task_id=run.task_id)
    initial_operator_items = runtime.admit_checkpoint("backend.start")
    if initial_operator_items:
        request = _request_with_admitted_operator_messages(
            request,
            initial_operator_items,
            checkpoint="backend.start",
        )
    try:
        runtime.raise_if_interrupted("backend.start")
    except (AgentBackendStopped, AgentBackendPaused) as exc:
        status = "paused" if isinstance(exc, AgentBackendPaused) else "stopped"
        result = AgentBackendRunResult(
            status=status,
            backend=str(backend_name or "unknown"),
            summary=str(exc),
            raw_result={"status": status, "checkpoint": exc.checkpoint},
        )
        store.update_run_metadata(
            run_id,
            {"backend_result": result.model_dump(mode="json")},
            status=status,  # type: ignore[arg-type]
        )
        return result
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
        result = await selected.run(request, _emit, runtime=runtime)
    except AgentBackendStopped as exc:
        result = AgentBackendRunResult(
            status="stopped",
            backend=selected.backend_name,
            summary=str(exc),
            raw_result={"status": "stopped", "checkpoint": exc.checkpoint},
        )
    except AgentBackendPaused as exc:
        result = AgentBackendRunResult(
            status="paused",
            backend=selected.backend_name,
            summary=str(exc),
            raw_result={"status": "paused", "checkpoint": exc.checkpoint},
        )
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
    store.promote_next_continue_after_current(run_id)
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
    surface_context = enrich_notes_surface_context(
        dict(payload.get("surface_context") or {}),
        workspace_root=workspace_root,
    )
    metadata = {
        "queue_key": run.metadata.get("queue_key", ""),
        "topic_key": payload.get("topic_key", ""),
        "history_turn_count": len(history),
        "operator_context": dict(payload.get("operator_context") or {}),
        "original_request": _first_compact_text(
            payload.get("original_request"),
            run.metadata.get("original_request"),
            _goal_context_value(payload, "original_request"),
            _goal_context_value(run.metadata, "original_request"),
        ),
        "follow_up_request": _first_compact_text(payload.get("follow_up_request")),
        "satisfaction_gap": _first_compact_text(
            payload.get("satisfaction_gap"),
            run.metadata.get("satisfaction_gap"),
            _goal_context_value(payload, "satisfaction_gap"),
            _goal_context_value(run.metadata, "satisfaction_gap"),
        ),
        "continued_from_run_id": _first_compact_text(
            payload.get("continued_from_run_id"),
            run.metadata.get("continued_from_run_id"),
        ),
        "goal_context": _merged_dict(
            payload.get("goal_context"),
            run.metadata.get("goal_context"),
        ),
        "retry_payload": dict(run.metadata.get("retry_payload") or {}),
        "retry_policy": run.metadata.get("retry_policy", ""),
        "retry_history": [
            dict(item)
            for item in run.metadata.get("retry_history", [])
            if isinstance(item, dict)
        ],
        **dict(overrides.get("metadata") or {}),
    }
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
        surface_context=surface_context,
        profile_policy=profile_policy,
        mutation_policy=mutation_policy,
        approval_policy=approval_policy,
        tool_policy=tool_policy,
        metadata=metadata,
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
    if normalized in {"codex", "codex_cli", "openai_codex"}:
        return CodexAgentBackendAdapter()
    if normalized in {"super_dan", "superdan", "super_organism"}:
        return SuperDanBackendAdapter()
    raise ValueError(f"Unsupported Agent backend: {requested}")


def _load_super_dan_cli():
    from dan.cli import super_organism as super_cli

    super_cli.load_env()
    return super_cli


def _resolve_codex_binary(request: AgentBackendRunRequest) -> str:
    configured = _first_compact_text(
        request.profile_policy.get("codex_bin"),
        request.metadata.get("codex_bin"),
        os.environ.get("DAN_CODEX_BIN"),
        "codex",
        limit=400,
    )
    if os.path.sep in configured or (os.path.altsep and os.path.altsep in configured):
        return configured
    resolved = shutil.which(configured)
    return resolved or ""


def _codex_model(request: AgentBackendRunRequest) -> str:
    return _first_compact_text(
        request.profile_policy.get("codex_model"),
        request.profile_policy.get("model"),
        request.metadata.get("codex_model"),
        os.environ.get("DAN_CODEX_MODEL"),
        limit=400,
    )


def _codex_sandbox(request: AgentBackendRunRequest) -> str:
    requested = _first_compact_text(
        request.profile_policy.get("codex_sandbox"),
        request.metadata.get("codex_sandbox"),
        os.environ.get("DAN_CODEX_SANDBOX"),
        limit=80,
    )
    if requested in {"read-only", "workspace-write", "danger-full-access"}:
        return requested
    mutation_mode = str(request.mutation_policy.get("mode") or "").strip().lower()
    mutation_permission = str(request.mutation_policy.get("permission") or "").strip().lower()
    if "read" in mutation_mode and "mutation" not in mutation_mode:
        return "read-only"
    if mutation_permission in {"forbidden", "read_only", "read-only"}:
        return "read-only"
    return "workspace-write"


def _codex_reasoning_effort(request: AgentBackendRunRequest) -> str:
    requested = _first_compact_text(
        request.profile_policy.get("codex_reasoning_effort"),
        request.profile_policy.get("reasoning_effort"),
        request.metadata.get("codex_reasoning_effort"),
        request.metadata.get("selected_reasoning_effort"),
        os.environ.get("DAN_CODEX_REASONING_EFFORT"),
        limit=80,
    )
    if requested in {"low", "medium", "high", "xhigh"}:
        return requested
    return ""


def _build_codex_exec_command(
    codex_bin: str,
    request: AgentBackendRunRequest,
    *,
    workspace_root: Path,
    objective: str,
) -> list[str]:
    command = [
        codex_bin,
        "exec",
        "--json",
        "--color",
        "never",
        "--sandbox",
        _codex_sandbox(request),
        "--cd",
        str(workspace_root),
        "--skip-git-repo-check",
    ]
    model = _codex_model(request)
    if model:
        command.extend(["--model", model])
    reasoning_effort = _codex_reasoning_effort(request)
    if reasoning_effort:
        command.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
    if bool(request.profile_policy.get("codex_ephemeral", True)):
        command.append("--ephemeral")
    command.append(objective)
    return command


def _redacted_command_for_event(command: list[str]) -> list[str]:
    redacted: list[str] = []
    skip_next = False
    for token in command:
        if skip_next:
            redacted.append("<redacted>")
            skip_next = False
            continue
        redacted.append(token)
        if token in {"--api-key", "--token"}:
            skip_next = True
    return redacted


async def _read_stream_text(stream: Any) -> str:
    if stream is None:
        return ""
    chunks: list[bytes] = []
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace").strip()


def _codex_events_from_json_row(
    row: dict[str, Any],
    *,
    request: AgentBackendRunRequest,
    backend_name: str,
) -> list[AgentRunEvent]:
    row_type = str(row.get("type") or "").strip()
    events: list[AgentRunEvent] = []
    if row_type == "thread.started":
        thread_id = _first_compact_text(row.get("thread_id"), limit=200)
        events.append(
            AgentRunEvent(
                type="planned",
                run_id=request.run_id,
                task_id=request.task_id,
                summary="Codex thread started.",
                source_event_type=row_type,
                payload={"backend": backend_name, "codex_thread_id": thread_id},
            )
        )
        return events

    if row_type in {"turn.started", "item.started"}:
        item = row.get("item") if isinstance(row.get("item"), dict) else {}
        summary = _codex_item_summary(item, default="Codex is working.")
        events.append(
            AgentRunEvent(
                type="status_reported",
                run_id=request.run_id,
                task_id=request.task_id,
                summary=summary,
                source_event_type=row_type,
                payload={"backend": backend_name, "codex_event": row},
            )
        )
        return events

    if row_type == "item.completed":
        item = row.get("item") if isinstance(row.get("item"), dict) else {}
        item_type = str(item.get("type") or "").strip()
        artifact_refs = _codex_artifact_refs(item)
        if item_type == "agent_message":
            text = _first_compact_text(
                item.get("text"),
                item.get("message"),
                item.get("content"),
                limit=8000,
            )
            events.append(
                AgentRunEvent(
                    type="model_text_delta",
                    run_id=request.run_id,
                    task_id=request.task_id,
                    summary=text[:500] if text else "Codex wrote a message.",
                    artifact_refs=artifact_refs,
                    source_event_type=row_type,
                    payload={"backend": backend_name, "codex_event": row, "text": text},
                )
            )
        elif "file" in item_type or artifact_refs:
            events.append(
                AgentRunEvent(
                    type="artifact_changed",
                    run_id=request.run_id,
                    task_id=request.task_id,
                    summary=_codex_item_summary(item, default="Codex changed workspace artifacts."),
                    artifact_refs=artifact_refs,
                    source_event_type=row_type,
                    payload={"backend": backend_name, "codex_event": row},
                )
            )
        else:
            events.append(
                AgentRunEvent(
                    type="tool_used",
                    run_id=request.run_id,
                    task_id=request.task_id,
                    summary=_codex_item_summary(item, default="Codex completed a work item."),
                    artifact_refs=artifact_refs,
                    source_event_type=row_type,
                    payload={"backend": backend_name, "codex_event": row},
                )
            )
        return events

    if row_type == "turn.completed":
        usage = normalize_token_usage(row.get("usage"))
        if usage:
            events.append(
                AgentRunEvent(
                    type="token_usage_recorded",
                    run_id=request.run_id,
                    task_id=request.task_id,
                    summary="Token usage for Codex run.",
                    source_event_type=row_type,
                    token_usage_delta=usage,
                    token_usage_total=usage,
                    token_usage_round={
                        "round": 1,
                        "model": _first_compact_text(row.get("model"), _codex_model(request), "codex"),
                        "backend": backend_name,
                        "delta": usage,
                        "total": usage,
                    },
                    payload={"backend": backend_name},
                )
            )
        final_text = _codex_final_text(row)
        events.append(
            AgentRunEvent(
                type="completed",
                run_id=request.run_id,
                task_id=request.task_id,
                summary=final_text or "Codex completed.",
                artifact_refs=_codex_artifact_refs(row),
                source_event_type=row_type,
                payload={
                    "backend": backend_name,
                    "codex_event": row,
                    "final_text": final_text,
                },
            )
        )
        return events

    if row_type in {"turn.failed", "error"}:
        events.append(
            AgentRunEvent(
                type="failed",
                run_id=request.run_id,
                task_id=request.task_id,
                summary=_first_compact_text(
                    row.get("message"),
                    row.get("error"),
                    row.get("detail"),
                    "Codex run failed.",
                ),
                source_event_type=row_type,
                payload={"backend": backend_name, "codex_event": row},
            )
        )
    return events


def _codex_item_summary(item: dict[str, Any], *, default: str) -> str:
    item_type = _first_compact_text(item.get("type"), limit=120)
    command = _first_compact_text(item.get("command"), item.get("cmd"), limit=300)
    text = _first_compact_text(item.get("text"), item.get("summary"), item.get("message"), limit=300)
    if command:
        return f"Codex ran `{command}`."
    if text:
        return text
    if item_type:
        return f"Codex completed {item_type.replace('_', ' ')}."
    return default


def _codex_final_text(row: dict[str, Any]) -> str:
    return _first_compact_text(
        row.get("final_response"),
        row.get("final_message"),
        row.get("message"),
        row.get("text"),
        limit=8000,
    )


def _codex_artifact_refs(value: Any) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            path_value = _first_compact_text(
                item.get("path"),
                item.get("file_path"),
                item.get("relative_path"),
                limit=800,
            )
            if path_value and not path_value.startswith("item_"):
                refs.append({"path": path_value})
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return _dedupe_artifact_refs(refs)


def _dedupe_artifact_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in refs:
        path = _first_compact_text(ref.get("path"), limit=1000)
        if not path or path in seen:
            continue
        seen.add(path)
        result.append({"path": path})
    return result


def _request_policy_payload(request: AgentBackendRunRequest, key: str) -> dict[str, Any]:
    for source in (request.metadata, request.surface_context):
        value = source.get(key) if isinstance(source, dict) else None
        if isinstance(value, dict):
            return dict(value)
    return {}


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
    setattr(args, "_surface_attachments", list(request.attachments or []))
    surface_context = enrich_notes_surface_context(
        dict(request.surface_context or {}),
        workspace_root=request.workspace_root,
    )
    setattr(args, "_surface_context", surface_context)
    history = _normalize_history(request.history)
    if not history:
        conversation = surface_context.get("conversation") if isinstance(surface_context, dict) else {}
        recent_turns = conversation.get("recent_turns") if isinstance(conversation, dict) else []
        history = _normalize_history(recent_turns)
    if history:
        setattr(args, "_surface_history", history)
        setattr(args, "_tui_surface_history", history)
    communication_policy = _request_policy_payload(request, "communication_policy")
    if communication_policy:
        setattr(args, "_tui_communication_policy", communication_policy)
    execution_policy = _request_policy_payload(request, "execution_policy")
    if execution_policy:
        setattr(args, "_tui_execution_policy", execution_policy)
    surface_policy = _request_policy_payload(request, "surface_policy")
    if surface_policy:
        setattr(args, "_tui_surface_policy", surface_policy)
    setattr(
        args,
        "_surface_image_attachments",
        [
            dict(item)
            for item in list(request.attachments or [])
            if isinstance(item, dict)
            and str(item.get("kind") or "").strip().lower() in {"image", "figure", ""}
        ],
    )
    return args


def _terminal_status(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"completed", "failed", "blocked", "paused", "stopped"}:
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


def _goal_context_value(source: dict[str, Any], key: str) -> Any:
    goal_context = source.get("goal_context") if isinstance(source, dict) else None
    if not isinstance(goal_context, dict):
        return ""
    return goal_context.get(key)


def _first_compact_text(*values: Any, limit: int = 2000) -> str:
    for value in values:
        text = _compact_context_text(value, limit=limit)
        if text:
            return text
    return ""


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
    goal_context = _resolved_goal_context(request, objective)
    original_request = str(goal_context.get("original_request") or "").strip()
    satisfaction_gap = str(goal_context.get("satisfaction_gap") or "").strip()
    is_continuation = bool(goal_context.get("is_continuation"))
    admitted_messages = _admitted_operator_messages(request.metadata)
    if admitted_messages:
        context_lines.append("Operator updates admitted from the active-run queue:")
        for item in admitted_messages[-10:]:
            lane = str(item.get("lane") or "append").replace("_", "-")
            text = _compact_context_text(item.get("text"), limit=1200)
            if text:
                context_lines.append(f"- {lane}: {text}")
            operator_context = (
                item.get("operator_context")
                if isinstance(item.get("operator_context"), dict)
                else {}
            )
            paths = [
                str(path)
                for path in list(operator_context.get("target_paths") or [])[:8]
                if path
            ]
            if paths:
                context_lines.append(f"  target paths: {', '.join(paths)}")
            if operator_context.get("validation_requirements"):
                context_lines.append("  includes validation requirement")
            if operator_context.get("hard_constraints"):
                context_lines.append("  includes hard constraint")

    if satisfaction_gap and (is_continuation or satisfaction_gap != objective):
        context_lines.append(f"Current follow-up / satisfaction gap: {satisfaction_gap}")
    previous_summary = _compact_context_text(
        goal_context.get("previous_summary"),
        limit=1200,
    )
    if previous_summary:
        context_lines.append(f"Previous run result summary: {previous_summary}")

    reply_text = _compact_context_text(
        request.reply_context.get("reply_to_text"),
        limit=1200,
    )
    if reply_text:
        context_lines.append(f"Replied-to message: {reply_text}")

    history = _history_without_current_objective(request.history, objective)
    if not history:
        conversation = request.surface_context.get("conversation") if isinstance(request.surface_context, dict) else {}
        recent_turns = conversation.get("recent_turns") if isinstance(conversation, dict) else []
        history = _normalize_history(recent_turns)
    if history:
        context_lines.append("Recent surface conversation:")
        for turn in history[-10:]:
            role = "User" if turn["role"] == "user" else "Assistant"
            context_lines.append(f"- {role}: {turn['content']}")

    if request.attachments:
        context_lines.append("Surface attachments:")
        for item in request.attachments[:8]:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "attachment").strip() or "attachment"
            path = str(item.get("local_path") or item.get("path") or "").strip()
            name = str(item.get("display_name") or item.get("name") or Path(path).name).strip()
            mime_type = str(item.get("mime_type") or "").strip()
            detail = path or name
            if detail:
                suffix = f" ({mime_type})" if mime_type else ""
                context_lines.append(f"- {kind}: {detail}{suffix}")

    if not context_lines:
        return objective
    if is_continuation and original_request:
        return (
            f"Original operator request: {original_request}\n\n"
            f"Current follow-up / satisfaction gap: {satisfaction_gap or objective}\n\n"
            "Continuation contract:\n"
            "- Continue toward the same user-visible goal instead of completing an internal run ticket.\n"
            "- Use the follow-up as the missing answer, correction, or steering note for that goal.\n"
            "- Resolve the work mode from the original operator request; answer in-session for explanation, summary, review, diagnosis, or status requests unless the user explicitly asks for project edits or a saved deliverable.\n\n"
            "Additional surface context for resolving references and active-run updates:\n"
            + "\n".join(context_lines)
            + "\n\nSatisfy the original request plus the current gap; do not treat older chat context as extra tasks."
        )
    return (
        f"Operator request: {objective}\n\n"
        "Additional surface context for resolving references and active-run updates:\n"
        + "\n".join(context_lines)
        + "\n\nSatisfy the operator request and admitted updates above; do not treat older chat context as extra tasks."
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


def _resolved_goal_context(
    request: AgentBackendRunRequest,
    objective: str,
) -> dict[str, Any]:
    metadata = dict(request.metadata or {})
    goal_context = dict(metadata.get("goal_context") or {})
    original_request = _first_compact_text(
        metadata.get("original_request"),
        goal_context.get("original_request"),
    )
    satisfaction_gap = _first_compact_text(
        metadata.get("satisfaction_gap"),
        metadata.get("follow_up_request"),
        goal_context.get("satisfaction_gap"),
        goal_context.get("current_request"),
    )
    previous_summary = _first_compact_text(goal_context.get("previous_summary"))
    continued_from_run_id = _first_compact_text(metadata.get("continued_from_run_id"))
    retry_policy = _first_compact_text(metadata.get("retry_policy"))
    if not satisfaction_gap and (continued_from_run_id or retry_policy):
        satisfaction_gap = objective

    history = _history_without_current_objective(request.history, objective)
    if not original_request and _looks_like_continuation_gap(objective):
        for turn in reversed(history):
            if turn.get("role") != "user":
                continue
            candidate = _compact_context_text(turn.get("content"), limit=2000)
            if candidate and not _looks_like_continuation_gap(candidate):
                original_request = candidate
                break
    is_continuation = bool(
        original_request
        and (
            original_request != objective
            or satisfaction_gap
            or continued_from_run_id
            or retry_policy
            or _looks_like_continuation_gap(objective)
        )
    )
    if is_continuation and not satisfaction_gap:
        satisfaction_gap = objective
    return {
        "original_request": original_request,
        "satisfaction_gap": satisfaction_gap,
        "previous_summary": previous_summary,
        "continued_from_run_id": continued_from_run_id,
        "retry_policy": retry_policy,
        "is_continuation": is_continuation,
    }


def _looks_like_continuation_gap(text: str) -> bool:
    lowered = " ".join(str(text or "").lower().split())
    if not lowered:
        return False
    patterns = (
        r"^(?:yes|yep|yeah|ok|okay|sure|please)\b.*\b(?:proceed|continue|go ahead|keep going|keep working|try again|retry)\b",
        r"^(?:continue|proceed|go ahead|keep going|keep working|retry|try again)\b",
        r"\b(?:not good|not enough|not satisfactory|not satisfied|still missing|still don['’]?t get|still dont get|where is the answer|where is the response)\b",
        r"\b(?:same request|same goal|finish it|complete it|keep improving)\b",
    )
    return any(re.search(pattern, lowered) for pattern in patterns)


def _compact_context_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _request_with_admitted_operator_messages(
    request: AgentBackendRunRequest,
    items: list[QueueItemRecord],
    *,
    checkpoint: str,
) -> AgentBackendRunRequest:
    metadata = dict(request.metadata or {})
    existing = [
        dict(item)
        for item in metadata.get("admitted_operator_messages", [])
        if isinstance(item, dict)
    ]
    existing.extend(
        _operator_message_from_queue_item(item, checkpoint=checkpoint)
        for item in items
    )
    metadata["admitted_operator_messages"] = existing[-50:]
    return request.model_copy(update={"metadata": metadata})


def _operator_message_from_queue_item(
    item: QueueItemRecord,
    *,
    checkpoint: str,
) -> dict[str, Any]:
    return {
        "queue_item_id": item.id,
        "lane": item.lane,
        "text": item.text,
        "surface_turn_id": item.surface_turn_id,
        "checkpoint": checkpoint,
        "operator_context": dict(item.metadata.get("operator_context") or {}),
        "metadata": dict(item.metadata),
    }


def _admitted_operator_messages(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    raw = metadata.get("admitted_operator_messages") if isinstance(metadata, dict) else []
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _is_safe_backend_checkpoint(row: dict[str, Any]) -> bool:
    event = str(row.get("event") or row.get("event_type") or row.get("type") or "").lower()
    if event == "tool.started" and _is_mutating_tool_row(row):
        return True
    if event in {
        "live.generic_build.started",
        "model.responded",
        "tool.completed",
        "tool.ok",
        "live.validation.started",
        "live.generic_repair.started",
        "live.builder_retry.started",
    }:
        return True
    return False


def _is_mutating_tool_row(row: dict[str, Any]) -> bool:
    tool_id = str(
        row.get("tool_id")
        or row.get("tool")
        or row.get("name")
        or row.get("function")
        or ""
    ).strip()
    return tool_id in {"file_write", "file_edit", "shell_command"}


def _checkpoint_name(row: dict[str, Any]) -> str:
    event = str(row.get("event") or row.get("event_type") or row.get("type") or "").strip()
    if not event:
        return "backend.event"
    return event


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
