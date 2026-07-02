"""Durable Chat/Agent V2 task, queue, and run state."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from dan.server.chat_v2 import (
    AgentRunCommand,
    AgentRunEvent,
    SurfaceTurn,
    TaskSnapshot,
    TriageDecisionRecord,
    merge_token_usage,
    normalize_token_usage,
)


TaskStatus = Literal[
    "queued",
    "waiting_dependency",
    "running",
    "needs_input",
    "paused",
    "completed",
    "failed",
    "blocked",
    "stopped",
]
_INTERRUPTION_METADATA_KEYS = (
    "pause_requested",
    "pause_requested_at",
    "pause_payload",
    "pause_surface_turn_id",
    "pause_confirmed_at",
    "pause_checkpoint",
    "stop_requested",
    "stop_requested_at",
    "stop_command",
    "stop_payload",
    "stop_surface_turn_id",
    "stop_confirmed_at",
    "stop_checkpoint",
)
QueueLane = Literal["append", "continue_after_current"]
QueueItemStatus = Literal["queued", "injected", "completed", "cancelled"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class QueueItemRecord(BaseModel):
    id: str
    task_id: str
    lane: QueueLane
    status: QueueItemStatus = "queued"
    surface_turn_id: str
    text: str = ""
    dedup_key: str = ""
    position: int = 0
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRunRecord(BaseModel):
    run_id: str
    task_id: str
    thread_id: str = ""
    workspace_root: str = ""
    workspace_id: str = ""
    status: TaskStatus = "queued"
    command: AgentRunCommand
    surface_turn_id: str = ""
    stream_channel_id: str = ""
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    latest_event_type: str = ""
    latest_summary: str = ""
    token_usage: dict[str, int] = Field(default_factory=dict)
    token_usage_rounds: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class V2TaskRecord(BaseModel):
    task_id: str
    thread_id: str = ""
    workspace_root: str = ""
    workspace_id: str = ""
    topic_key: str = ""
    queue_key: str = ""
    status: TaskStatus = "queued"
    phase: str = ""
    active_run_id: str | None = None
    latest_progress: str = ""
    latest_artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    blocker: str = ""
    trace_refs: list[str] = Field(default_factory=list)
    token_usage: dict[str, int] = Field(default_factory=dict)
    token_usage_rounds: list[dict[str, Any]] = Field(default_factory=list)
    queue_items: list[QueueItemRecord] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    surface_turn_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def snapshot(self) -> TaskSnapshot:
        queued_items = [item for item in self.queue_items if item.status == "queued"]
        queue_position = min((item.position for item in queued_items), default=None)
        return TaskSnapshot(
            task_id=self.task_id,
            thread_id=self.thread_id,
            status=self.status,
            phase=self.phase,
            queue_position=queue_position,
            latest_progress=self.latest_progress,
            latest_artifact_refs=list(self.latest_artifact_refs),
            blocker=self.blocker,
            trace_refs=list(self.trace_refs),
            token_usage=dict(self.token_usage),
            latest_token_usage_round=(
                dict(self.token_usage_rounds[-1]) if self.token_usage_rounds else {}
            ),
            metadata={
                **dict(self.metadata),
                "topic_key": self.topic_key,
                "queue_key": self.queue_key,
                "workspace_root": self.workspace_root,
                "workspace_id": self.workspace_id,
                "active_run_id": self.active_run_id,
                "token_usage": dict(self.token_usage),
                "token_usage_rounds": list(self.token_usage_rounds),
                "append_queue_length": sum(
                    1
                    for item in queued_items
                    if item.lane == "append"
                ),
                "continue_queue_length": sum(
                    1
                    for item in queued_items
                    if item.lane == "continue_after_current"
                ),
                "queue_items": [
                    item.model_dump(mode="json")
                    for item in self.queue_items
                    if item.status == "queued"
                ],
                "created_at": self.created_at,
                "updated_at": self.updated_at,
            },
        )


class V2TurnAcceptance(BaseModel):
    surface_turn_id: str
    triage_action: str
    task_id: str | None = None
    run_id: str | None = None
    queue_item_id: str | None = None
    queue_position: int | None = None
    snapshot: TaskSnapshot | None = None
    event: AgentRunEvent | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatV2Store:
    """Filesystem-backed V2 task/run state.

    The store is intentionally plain JSON plus JSONL so task state can be read
    during a live debugging session and replayed without a database migration.
    """

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)
        self._tasks_dir.mkdir(parents=True, exist_ok=True)
        self._runs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @property
    def _tasks_dir(self) -> Path:
        return self.base_dir / "tasks"

    @property
    def _runs_dir(self) -> Path:
        return self.base_dir / "runs"

    @property
    def _prompt_logs_dir(self) -> Path:
        return self.base_dir / "prompt_logs"

    def accept_bridge_context(
        self,
        context: dict[str, Any],
        *,
        stream_channel_id: str = "",
    ) -> V2TurnAcceptance:
        """Persist the durable task/queue projection for one triaged turn."""

        turn = SurfaceTurn.model_validate(context.get("surface_turn") or {})
        decision = TriageDecisionRecord.model_validate(
            context.get("triage_decision") or {}
        )
        if decision.action in {"chat_response", "control_command", "ignored"}:
            return V2TurnAcceptance(
                surface_turn_id=turn.id,
                triage_action=decision.action,
                metadata={
                    "task_binding": decision.task_binding,
                    "control_command": decision.control_command,
                },
            )

        with self._lock:
            task = self._resolve_task_for_turn(turn, decision)
            self._remember_surface_turn(task, turn)

            if decision.action == "agent_requested":
                acceptance = self._start_run_for_task(
                    task,
                    turn=turn,
                    decision=decision,
                    stream_channel_id=stream_channel_id,
                )
            elif decision.action == "append_to_active_run":
                acceptance = self._queue_followup(
                    task,
                    turn=turn,
                    decision=decision,
                    lane="append",
                )
            elif decision.action == "continue_after_current":
                acceptance = self._queue_followup(
                    task,
                    turn=turn,
                    decision=decision,
                    lane="continue_after_current",
                )
            else:
                task.status = "needs_input"
                task.phase = "needs_queue_lane"
                task.latest_progress = decision.reason or "Needs an explicit Agent lane."
                task.queue_key = decision.queue_key
                task.updated_at = _now()
                self._save_task(task)
                event = AgentRunEvent(
                    type="needs_input",
                    task_id=task.task_id,
                    summary=task.latest_progress,
                    source_event_type="chat_v2.triage.needs_lane",
                    payload={
                        "surface_turn_id": turn.id,
                        "triage_action": decision.action,
                    },
                )
                acceptance = V2TurnAcceptance(
                    surface_turn_id=turn.id,
                    triage_action=decision.action,
                    task_id=task.task_id,
                    snapshot=task.snapshot(),
                    event=event,
                )
            return acceptance

    def get_task(self, task_id: str) -> V2TaskRecord | None:
        path = self._task_path(task_id)
        if not path.exists():
            return None
        try:
            return V2TaskRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def get_task_snapshot(self, task_id: str) -> TaskSnapshot | None:
        task = self.get_task(task_id)
        return task.snapshot() if task is not None else None

    def list_thread_tasks(self, thread_id: str, *, limit: int = 50) -> list[TaskSnapshot]:
        tasks = [
            task
            for task in self._iter_tasks()
            if task.thread_id == thread_id
        ]
        tasks.sort(key=lambda task: task.updated_at, reverse=True)
        return [task.snapshot() for task in tasks[: max(1, limit)]]

    def list_task_records(
        self,
        *,
        workspace_root: str = "",
        thread_id: str = "",
        limit: int = 100,
    ) -> list[V2TaskRecord]:
        """Return durable task records for board/admission projections."""

        tasks = [
            task
            for task in self._iter_tasks()
            if (not workspace_root or task.workspace_root == workspace_root)
            and (not thread_id or task.thread_id == thread_id)
        ]
        tasks.sort(key=lambda task: task.updated_at, reverse=True)
        return [task.model_copy(deep=True) for task in tasks[: max(1, limit)]]

    def get_run(self, run_id: str) -> AgentRunRecord | None:
        path = self._run_path(run_id)
        if not path.exists():
            return None
        try:
            return AgentRunRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def list_run_records(
        self,
        *,
        workspace_root: str = "",
        task_id: str = "",
        thread_id: str = "",
        limit: int = 100,
    ) -> list[AgentRunRecord]:
        """Return durable run records for board/admission projections."""

        runs = [
            run
            for run in self._iter_runs()
            if (not workspace_root or run.workspace_root == workspace_root)
            and (not task_id or run.task_id == task_id)
            and (not thread_id or run.thread_id == thread_id)
        ]
        runs.sort(key=lambda run: run.updated_at, reverse=True)
        return [run.model_copy(deep=True) for run in runs[: max(1, limit)]]

    def recover_interrupted_runs_after_restart(self) -> int:
        """Mark persisted running Agent runs as stopped after process restart.

        Chat V2 background workers are in-process tasks today. If the server
        restarts, a durable ``running`` record no longer has a live worker
        behind it, so exposing it as active makes the GUI look stuck.
        """

        with self._lock:
            now = _now()
            summary = "Stopped because the backend restarted before this run completed."
            recovered = 0
            running_task_ids: set[str] = set()
            for run in self._iter_runs():
                if run.status != "running":
                    continue
                previous_status = run.status
                run.status = "stopped"
                run.latest_event_type = "process_restarted"
                run.latest_summary = summary
                run.updated_at = now
                run.metadata.update({
                    "restart_recovered_at": now,
                    "restart_recovery_reason": "process_restart",
                    "previous_status": previous_status,
                })
                self._save_run(run)
                self._append_run_event(
                    run.run_id,
                    AgentRunEvent(
                        type="stopped",
                        run_id=run.run_id,
                        task_id=run.task_id,
                        summary=summary,
                        source_event_type="chat_v2.recovery.process_restart",
                        payload={
                            "reason": "process_restart",
                            "previous_status": previous_status,
                            "recovered_at": now,
                        },
                    ),
                )
                running_task_ids.add(run.task_id)
                recovered += 1

            terminal_statuses = {"completed", "failed", "blocked", "stopped"}
            recovered_tasks = 0
            for task in self._iter_tasks():
                if task.task_id not in running_task_ids and task.status != "running":
                    continue
                if task.status in terminal_statuses:
                    continue
                previous_status = task.status
                task.status = "stopped"
                task.phase = "process_restarted"
                task.latest_progress = summary
                task.updated_at = now
                task.metadata.update({
                    "restart_recovered_at": now,
                    "restart_recovery_reason": "process_restart",
                    "previous_status": previous_status,
                })
                self._save_task(task)
                recovered_tasks += 1
            return recovered + recovered_tasks

    def annotate_run_for_board(
        self,
        run_id: str,
        *,
        run_metadata: dict[str, Any] | None = None,
        task_metadata: dict[str, Any] | None = None,
        status: TaskStatus | None = None,
        phase: str | None = None,
        latest_progress: str | None = None,
        event: AgentRunEvent | None = None,
    ) -> AgentRunRecord | None:
        """Update the public board projection fields for a persisted run."""

        with self._lock:
            run = self.get_run(run_id)
            if run is None:
                return None
            task = self.get_task(run.task_id)
            now = _now()
            if run_metadata:
                run.metadata.update(dict(run_metadata))
            if status is not None:
                run.status = status
            if latest_progress:
                run.latest_summary = latest_progress
            if event is not None:
                run.latest_event_type = event.type
                run.latest_summary = event.summary
            run.updated_at = now
            self._save_run(run)

            if task is not None:
                if task_metadata:
                    task.metadata.update(dict(task_metadata))
                if status is not None:
                    task.status = status
                if phase is not None:
                    task.phase = phase
                if latest_progress:
                    task.latest_progress = latest_progress
                if event is not None and event.summary:
                    task.latest_progress = event.summary
                task.metadata["active_run_id"] = run.run_id
                task.updated_at = now
                self._save_task(task)

            if event is not None:
                self._append_run_event(run.run_id, event)
            return run.model_copy(deep=True)

    def promote_waiting_dependency_runs(
        self,
        *,
        dependency_task_id: str = "",
        limit: int = 8,
    ) -> list[AgentRunRecord]:
        """Release queued dependency runs whose prerequisites are terminal."""

        promoted: list[AgentRunRecord] = []
        with self._lock:
            waiting = [
                run
                for run in self._iter_runs()
                if run.status == "waiting_dependency"
            ]
            waiting.sort(key=lambda run: run.created_at)
            for run in waiting:
                deps = [
                    str(item)
                    for item in run.metadata.get("depends_on_task_ids", [])
                    if str(item).strip()
                ]
                if dependency_task_id and dependency_task_id not in deps:
                    continue
                if not deps:
                    continue
                dep_tasks = [self.get_task(dep_id) for dep_id in deps]
                if any(task is None for task in dep_tasks):
                    continue
                dep_statuses = {
                    str(task.status)
                    for task in dep_tasks
                    if task is not None
                }
                if not dep_statuses.issubset({"completed", "failed", "blocked", "stopped"}):
                    continue

                task = self.get_task(run.task_id)
                now = _now()
                if dep_statuses == {"completed"}:
                    summary = "Dependencies completed; Agent run queued for execution."
                    run.status = "queued"
                    run.latest_event_type = "queued"
                    run.latest_summary = summary
                    run.metadata["dependencies_satisfied_at"] = now
                    if task is not None:
                        task.status = "queued"
                        task.phase = "dependency_satisfied"
                        task.latest_progress = summary
                        task.metadata["dependencies_satisfied_at"] = now
                    event = AgentRunEvent(
                        type="queued",
                        run_id=run.run_id,
                        task_id=run.task_id,
                        summary=summary,
                        source_event_type="chat_v2.async_admission.dependency_satisfied",
                        payload={
                            "depends_on_task_ids": deps,
                            "dependency_statuses": sorted(dep_statuses),
                        },
                    )
                    promoted.append(run.model_copy(deep=True))
                else:
                    summary = "Dependency run did not complete successfully; queued work is blocked."
                    run.status = "blocked"
                    run.latest_event_type = "blocked"
                    run.latest_summary = summary
                    run.metadata["dependency_blocked_at"] = now
                    if task is not None:
                        task.status = "blocked"
                        task.phase = "dependency_blocked"
                        task.latest_progress = summary
                        task.blocker = summary
                        task.metadata["dependency_blocked_at"] = now
                    event = AgentRunEvent(
                        type="blocked",
                        run_id=run.run_id,
                        task_id=run.task_id,
                        summary=summary,
                        source_event_type="chat_v2.async_admission.dependency_blocked",
                        payload={
                            "depends_on_task_ids": deps,
                            "dependency_statuses": sorted(dep_statuses),
                        },
                    )
                run.updated_at = now
                self._save_run(run)
                if task is not None:
                    task.updated_at = now
                    self._save_task(task)
                self._append_run_event(run.run_id, event)
                if len(promoted) >= max(1, limit):
                    break
        return promoted

    def update_run_metadata(
        self,
        run_id: str,
        metadata: dict[str, Any],
        *,
        status: TaskStatus | None = None,
    ) -> AgentRunRecord | None:
        """Merge backend/runtime metadata onto a persisted run."""

        with self._lock:
            run = self.get_run(run_id)
            if run is None:
                return None
            run.metadata.update(dict(metadata or {}))
            result = run.metadata.get("backend_result")
            if isinstance(result, dict):
                summary = str(result.get("summary") or "")
                if summary:
                    run.latest_summary = summary
                result_usage = normalize_token_usage(result.get("token_usage"))
                if result_usage:
                    run.token_usage = _prefer_token_usage_total(
                        run.token_usage,
                        result_usage,
                    )
                    run.metadata["token_usage"] = dict(run.token_usage)
                result_rounds = result.get("token_usage_rounds")
                if isinstance(result_rounds, list):
                    run.token_usage_rounds = _merge_token_usage_rounds(
                        run.token_usage_rounds,
                        result_rounds,
                    )
                    run.metadata["token_usage_rounds"] = list(run.token_usage_rounds)
            if status is not None:
                run.status = status
            run.updated_at = _now()
            self._save_run(run)

            task = self.get_task(run.task_id)
            if task is not None:
                if status is not None:
                    task.status = status
                task.metadata.update(
                    {
                        "active_run_id": run.run_id,
                        "selected_backend": run.metadata.get("selected_backend", ""),
                        "backend_selection": run.metadata.get("backend_selection", ""),
                    }
                )
                for key in (
                    "normalized_request",
                    "context_composer",
                    "shared_evidence_context",
                ):
                    if key in run.metadata:
                        task.metadata[key] = run.metadata[key]
                if run.token_usage:
                    task.token_usage = dict(run.token_usage)
                    task.metadata["token_usage"] = dict(run.token_usage)
                if run.token_usage_rounds:
                    task.token_usage_rounds = list(run.token_usage_rounds)
                    task.metadata["token_usage_rounds"] = list(run.token_usage_rounds)
                if isinstance(result, dict):
                    summary = str(result.get("summary") or "")
                    if summary:
                        task.latest_progress = summary
                    artifacts = result.get("artifact_refs")
                    if isinstance(artifacts, list):
                        task.latest_artifact_refs = [
                            dict(item) if isinstance(item, dict) else {"path": str(item)}
                            for item in artifacts
                        ]
                    for trace_ref in result.get("trace_refs") or []:
                        trace_text = str(trace_ref or "")
                        if trace_text and trace_text not in task.trace_refs:
                            task.trace_refs.append(trace_text)
                task.updated_at = _now()
                self._save_task(task)
            return run

    def load_run_events(self, run_id: str) -> list[dict[str, Any]]:
        path = self._run_events_path(run_id)
        if not path.exists():
            return []
        events: list[dict[str, Any]] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                events.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
        return events

    def thread_prompt_log(self, thread_id: str) -> dict[str, Any]:
        """Render and persist a human-readable prompt/response log for a session."""

        runs = self.list_run_records(thread_id=thread_id, limit=500)
        runs.sort(key=lambda run: run.created_at)
        path = self._thread_prompt_log_path(thread_id)
        lines: list[str] = [
            "# Session Prompt Log",
            "",
            f"- Session: `{thread_id}`",
            f"- Generated: {_now()}",
            f"- Runs: {len(runs)}",
            "",
        ]
        entry_count = 0
        if not runs:
            lines.extend(
                [
                    "No Agent runs were found for this session yet.",
                    "",
                ]
            )
        for run_index, run in enumerate(runs, start=1):
            events = self.load_run_events(run.run_id)
            model_events = [
                event
                for event in events
                if _model_event_name(event) in {"model.requested", "model.responded"}
            ]
            lines.extend(
                [
                    f"## Run {run_index}: `{run.run_id}`",
                    "",
                    f"- Task: `{run.task_id}`",
                    f"- Status: {run.status}",
                    f"- Workspace: `{run.workspace_root}`" if run.workspace_root else "- Workspace: not recorded",
                    "",
                ]
            )
            if not model_events:
                lines.extend(["No model prompt/response events were recorded for this run.", ""])
                continue

            responses_by_call: dict[str, list[dict[str, Any]]] = {}
            requested_events: list[dict[str, Any]] = []
            loose_responses: list[dict[str, Any]] = []
            for event in model_events:
                payload = _agent_event_payload(event)
                call_id = str(payload.get("model_call_id") or event.get("source_event_id") or "").strip()
                if _model_event_name(event) == "model.requested":
                    requested_events.append(event)
                elif call_id:
                    responses_by_call.setdefault(call_id, []).append(event)
                else:
                    loose_responses.append(event)

            for call_index, request_event in enumerate(requested_events, start=1):
                entry_count += 1
                payload = _agent_event_payload(request_event)
                call_id = str(payload.get("model_call_id") or request_event.get("source_event_id") or "").strip()
                round_number = str(payload.get("round") or "?")
                model = str(payload.get("model") or "(unknown model)")
                tool_ids = [str(item) for item in payload.get("tool_ids") or [] if str(item).strip()]
                prompt_messages = payload.get("prompt_messages")
                lines.extend(
                    [
                        f"### Model Call {call_index}",
                        "",
                        f"- Model: `{model}`",
                        f"- Round: {round_number}",
                        f"- Call ID: `{call_id or 'not recorded'}`",
                        f"- Tools enabled: {', '.join(f'`{tool}`' for tool in tool_ids) if tool_ids else 'none'}",
                        "",
                        "#### Prompt Sent",
                        "",
                    ]
                )
                if isinstance(prompt_messages, list) and prompt_messages:
                    for message_index, message in enumerate(prompt_messages, start=1):
                        if not isinstance(message, dict):
                            continue
                        role = str(message.get("role") or f"message-{message_index}")
                        lines.append(f"##### {message_index}. {role}")
                        lines.append("")
                        lines.append(_fenced_text(_message_debug_text(message)))
                        lines.append("")
                else:
                    lines.extend(
                        [
                            "_Prompt messages were not captured for this older run._",
                            "",
                        ]
                    )
                responses = responses_by_call.pop(call_id, []) if call_id else []
                for response_index, response_event in enumerate(responses, start=1):
                    lines.extend(_response_log_lines(response_event, response_index=response_index))

            for response_events in responses_by_call.values():
                for response_event in response_events:
                    entry_count += 1
                    lines.extend(_response_log_lines(response_event, response_index=1))
            for response_event in loose_responses:
                entry_count += 1
                lines.extend(_response_log_lines(response_event, response_index=1))

        content = "\n".join(lines).rstrip() + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {
            "thread_id": thread_id,
            "path": str(path),
            "content": content,
            "entry_count": entry_count,
            "run_ids": [run.run_id for run in runs],
        }

    def record_agent_event(self, event: AgentRunEvent) -> TaskSnapshot | None:
        """Append a normalized Agent event and update the task projection."""

        with self._lock:
            run = self.get_run(str(event.run_id or ""))
            task = self.get_task(str(event.task_id or "")) if event.task_id else None
            if run is not None and task is None:
                task = self.get_task(run.task_id)
            if run is not None and not event.task_id:
                event = event.model_copy(update={"task_id": run.task_id})
            if run is None and task is None:
                return None

            if run is not None:
                run.latest_event_type = event.type
                run.latest_summary = event.summary
                run.status = _status_for_event(event.type, fallback=run.status)
                _apply_token_usage_event(run, event)
                run.updated_at = _now()
                self._save_run(run)
                self._append_run_event(run.run_id, event)

            assert task is not None
            task.status = _status_for_event(event.type, fallback=task.status)
            if event.summary:
                task.latest_progress = event.summary
            if event.artifact_refs:
                task.latest_artifact_refs = list(event.artifact_refs)
            if event.type == "blocked":
                task.blocker = event.summary
            if event.source_event_path and event.source_event_path not in task.trace_refs:
                task.trace_refs.append(event.source_event_path)
            if run is not None and run.token_usage:
                task.token_usage = dict(run.token_usage)
                task.metadata["token_usage"] = dict(task.token_usage)
            else:
                _apply_token_usage_event(task, event)
            if run is not None and run.token_usage_rounds:
                task.token_usage_rounds = list(run.token_usage_rounds)
                task.metadata["token_usage_rounds"] = list(task.token_usage_rounds)
            elif task.token_usage_rounds:
                task.metadata["token_usage_rounds"] = list(task.token_usage_rounds)
            task.updated_at = _now()
            self._save_task(task)
            return task.snapshot()

    def record_agent_command(self, command: AgentRunCommand) -> AgentRunEvent:
        """Persist a control command as a normalized event."""

        if command.command in {"append_followup", "continue_after_current"}:
            return self.queue_agent_command(command)
        if command.command in {"stop", "cancel"}:
            return self.request_agent_run_stop(command)
        if command.command == "pause":
            return self.request_agent_run_pause(command)
        if command.command == "resume":
            return self.resume_agent_run(command)
        if command.command == "retry":
            return self.retry_agent_run(command)
        if command.command == "status":
            return self.report_agent_run_status(command)
        if command.command == "branch_from":
            return self.branch_agent_run(command)

        event_type = {
            "approve": "accepted",
            "deny": "blocked",
            "retry": "queued",
            "resume": "queued",
            "status": "status_reported",
            "append_followup": "queue_item_added",
            "continue_after_current": "queue_item_added",
            "branch_from": "branch_created",
        }.get(command.command, "accepted")
        event = AgentRunEvent(
            type=event_type,  # type: ignore[arg-type]
            run_id=command.run_id,
            task_id=command.task_id,
            summary=_summary_for_command(command),
            source_event_type="chat_v2.command",
            payload={
                "command": command.command,
                "surface_turn_id": command.surface_turn_id,
                **dict(command.payload or {}),
            },
        )
        self.record_agent_event(event)
        return event

    def report_agent_run_status(self, command: AgentRunCommand) -> AgentRunEvent:
        """Return a read-only status event for an Agent run command surface."""

        run = self.get_run(str(command.run_id or ""))
        task_id = str(command.task_id or (run.task_id if run is not None else ""))
        task = self.get_task(task_id) if task_id else None
        if run is None and task is None:
            return AgentRunEvent(
                type="blocked",
                run_id=command.run_id,
                task_id=command.task_id,
                summary="Cannot report status because the Agent run was not found.",
                source_event_type="chat_v2.command.status_failed",
                payload={"reason": "run_not_found"},
            )
        queued_items = list(task.queue_items if task is not None else [])
        append_count = sum(
            1
            for item in queued_items
            if item.status == "queued" and item.lane == "append"
        )
        continue_count = sum(
            1
            for item in queued_items
            if item.status == "queued" and item.lane == "continue_after_current"
        )
        run_status = run.status if run is not None else ""
        task_status = task.status if task is not None else ""
        summary_status = run_status or task_status or "unknown"
        return AgentRunEvent(
            type="status_reported",
            run_id=run.run_id if run is not None else command.run_id,
            task_id=task.task_id if task is not None else command.task_id,
            summary=f"Agent run status: {summary_status}.",
            source_event_type="chat_v2.command.status_reported",
            payload={
                "command": command.command,
                "surface_turn_id": command.surface_turn_id,
                "run_status": run_status,
                "task_status": task_status,
                "latest_event_type": run.latest_event_type if run is not None else "",
                "latest_summary": run.latest_summary if run is not None else "",
                "task_latest_progress": task.latest_progress if task is not None else "",
                "append_queue_length": append_count,
                "continue_queue_length": continue_count,
                "active_run_id": task.active_run_id if task is not None else "",
                "workspace_root": task.workspace_root if task is not None else "",
                "workspace_id": task.workspace_id if task is not None else "",
                **dict(command.payload or {}),
            },
        )

    def request_agent_run_stop(self, command: AgentRunCommand) -> AgentRunEvent:
        """Record a stop/cancel request without pretending the backend halted mid-step."""

        with self._lock:
            run = self.get_run(str(command.run_id or ""))
            task_id = str(command.task_id or (run.task_id if run is not None else ""))
            task = self.get_task(task_id) if task_id else None
            if run is None and task is None:
                return AgentRunEvent(
                    type="blocked",
                    run_id=command.run_id,
                    task_id=command.task_id,
                    summary="Cannot stop run because the Agent run was not found.",
                    source_event_type="chat_v2.command.stop_failed",
                    payload={
                        "command": command.command,
                        "reason": "run_not_found",
                    },
                )

            now = _now()
            metadata = {
                "stop_requested": True,
                "stop_requested_at": now,
                "stop_command": command.command,
                "stop_payload": dict(command.payload or {}),
                "stop_surface_turn_id": command.surface_turn_id or "",
            }
            if run is not None:
                run.metadata.update(metadata)
                run.updated_at = now
                self._save_run(run)
            if task is not None:
                task.metadata.update(metadata)
                task.latest_progress = _stop_requested_summary(command.command)
                task.updated_at = now
                self._save_task(task)

            event = AgentRunEvent(
                type="stop_requested",
                run_id=run.run_id if run is not None else command.run_id,
                task_id=task.task_id if task is not None else command.task_id,
                summary=_stop_requested_summary(command.command),
                source_event_type="chat_v2.command.stop_requested",
                payload={
                    "command": command.command,
                    "surface_turn_id": command.surface_turn_id,
                    **dict(command.payload or {}),
                },
            )
            if run is not None:
                run.latest_event_type = event.type
                run.latest_summary = event.summary
                run.updated_at = _now()
                self._save_run(run)
                self._append_run_event(run.run_id, event)
            return event

    def request_agent_run_pause(self, command: AgentRunCommand) -> AgentRunEvent:
        """Record a pause request without interrupting an unsafe backend step."""

        with self._lock:
            run = self.get_run(str(command.run_id or ""))
            task_id = str(command.task_id or (run.task_id if run is not None else ""))
            task = self.get_task(task_id) if task_id else None
            if run is None and task is None:
                return AgentRunEvent(
                    type="blocked",
                    run_id=command.run_id,
                    task_id=command.task_id,
                    summary="Cannot pause run because the Agent run was not found.",
                    source_event_type="chat_v2.command.pause_failed",
                    payload={
                        "command": command.command,
                        "reason": "run_not_found",
                    },
                )

            now = _now()
            metadata = {
                "pause_requested": True,
                "pause_requested_at": now,
                "pause_payload": dict(command.payload or {}),
                "pause_surface_turn_id": command.surface_turn_id or "",
            }
            if run is not None:
                run.metadata.update(metadata)
                run.latest_event_type = "pause_requested"
                run.latest_summary = _pause_requested_summary()
                run.updated_at = now
                self._save_run(run)
            if task is not None:
                task.metadata.update(metadata)
                task.latest_progress = _pause_requested_summary()
                task.updated_at = now
                self._save_task(task)

            event = AgentRunEvent(
                type="pause_requested",
                run_id=run.run_id if run is not None else command.run_id,
                task_id=task.task_id if task is not None else command.task_id,
                summary=_pause_requested_summary(),
                source_event_type="chat_v2.command.pause_requested",
                payload={
                    "command": command.command,
                    "surface_turn_id": command.surface_turn_id,
                    **dict(command.payload or {}),
                },
            )
            if run is not None:
                self._append_run_event(run.run_id, event)
            return event

    def confirm_agent_run_stopped(
        self,
        run_id: str,
        *,
        checkpoint: str,
        reason: str = "",
    ) -> AgentRunEvent | None:
        """Transition a stop-requested run to stopped at a safe checkpoint."""

        with self._lock:
            run = self.get_run(run_id)
            if run is None:
                return None
            task = self.get_task(run.task_id)
            if run.status == "stopped":
                return AgentRunEvent(
                    type="stopped",
                    run_id=run.run_id,
                    task_id=run.task_id,
                    summary=run.latest_summary or "Run stopped.",
                    source_event_type="chat_v2.command.stop_already_confirmed",
                    payload={"checkpoint": checkpoint},
                )
            run.metadata["stop_confirmed_at"] = _now()
            run.metadata["stop_checkpoint"] = checkpoint
            self._save_run(run)
            event = AgentRunEvent(
                type="stopped",
                run_id=run.run_id,
                task_id=run.task_id,
                summary=reason or f"Stopped at safe checkpoint: {checkpoint}.",
                source_event_type="chat_v2.command.stop_confirmed",
                payload={
                    "checkpoint": checkpoint,
                    "stop_command": run.metadata.get("stop_command", "stop"),
                },
            )
            self.record_agent_event(event)
            if task is not None:
                task.metadata["stop_checkpoint"] = checkpoint
                task.updated_at = _now()
                self._save_task(task)
            return event

    def confirm_agent_run_paused(
        self,
        run_id: str,
        *,
        checkpoint: str,
        reason: str = "",
    ) -> AgentRunEvent | None:
        """Transition a pause-requested run to paused at a safe checkpoint."""

        with self._lock:
            run = self.get_run(run_id)
            if run is None:
                return None
            task = self.get_task(run.task_id)
            if run.status == "paused":
                return AgentRunEvent(
                    type="paused",
                    run_id=run.run_id,
                    task_id=run.task_id,
                    summary=run.latest_summary or "Run paused.",
                    source_event_type="chat_v2.command.pause_already_confirmed",
                    payload={"checkpoint": checkpoint},
                )
            run.metadata["pause_confirmed_at"] = _now()
            run.metadata["pause_checkpoint"] = checkpoint
            self._save_run(run)
            event = AgentRunEvent(
                type="paused",
                run_id=run.run_id,
                task_id=run.task_id,
                summary=reason or f"Paused at safe checkpoint: {checkpoint}.",
                source_event_type="chat_v2.command.pause_confirmed",
                payload={"checkpoint": checkpoint},
            )
            self.record_agent_event(event)
            if task is not None:
                task.metadata["pause_checkpoint"] = checkpoint
                task.updated_at = _now()
                self._save_task(task)
            return event

    def resume_agent_run(self, command: AgentRunCommand) -> AgentRunEvent:
        """Clear pause state and queue the run for explicit resumed execution."""

        with self._lock:
            run = self.get_run(str(command.run_id or ""))
            task_id = str(command.task_id or (run.task_id if run is not None else ""))
            task = self.get_task(task_id) if task_id else None
            if run is None or task is None:
                return AgentRunEvent(
                    type="blocked",
                    run_id=command.run_id,
                    task_id=command.task_id,
                    summary="Cannot resume run because the paused Agent run was not found.",
                    source_event_type="chat_v2.command.resume_failed",
                    payload={"reason": "run_not_found"},
                )
            if run.status != "paused":
                event = AgentRunEvent(
                    type="blocked",
                    run_id=run.run_id,
                    task_id=run.task_id,
                    summary="Only paused Agent runs can be resumed.",
                    source_event_type="chat_v2.command.resume_blocked",
                    payload={
                        "current_status": run.status,
                        "surface_turn_id": command.surface_turn_id,
                    },
                )
                self.record_agent_event(event)
                return event

            for key in (
                "pause_requested",
                "pause_requested_at",
                "pause_payload",
                "pause_surface_turn_id",
                "pause_confirmed_at",
                "pause_checkpoint",
            ):
                run.metadata.pop(key, None)
                task.metadata.pop(key, None)
            run.metadata["resume_requested_at"] = _now()
            run.metadata["resume_payload"] = dict(command.payload or {})
            run.metadata["resume_policy"] = "restart_backend_run_from_paused_boundary"
            run.status = "queued"
            run.latest_event_type = "queued"
            run.latest_summary = "Resume queued for paused Agent run."
            run.updated_at = _now()
            task.status = "queued"
            task.latest_progress = run.latest_summary
            task.metadata["resume_policy"] = run.metadata["resume_policy"]
            task.updated_at = _now()
            self._save_run(run)
            self._save_task(task)
            event = AgentRunEvent(
                type="queued",
                run_id=run.run_id,
                task_id=run.task_id,
                summary=run.latest_summary,
                source_event_type="chat_v2.command.resume_queued",
                payload={
                    "surface_turn_id": command.surface_turn_id,
                    "resume_policy": run.metadata["resume_policy"],
                    **dict(command.payload or {}),
                },
            )
            self._append_run_event(run.run_id, event)
            return event

    def retry_agent_run(self, command: AgentRunCommand) -> AgentRunEvent:
        """Queue a terminal run for a fresh execution attempt."""

        with self._lock:
            run = self.get_run(str(command.run_id or ""))
            task_id = str(command.task_id or (run.task_id if run is not None else ""))
            task = self.get_task(task_id) if task_id else None
            if run is None or task is None:
                return AgentRunEvent(
                    type="blocked",
                    run_id=command.run_id,
                    task_id=command.task_id,
                    summary="Cannot retry run because the Agent run was not found.",
                    source_event_type="chat_v2.command.retry_failed",
                    payload={"reason": "run_not_found"},
                )
            retryable_statuses = {"completed", "failed", "blocked", "stopped"}
            if run.status not in retryable_statuses:
                event = AgentRunEvent(
                    type="blocked",
                    run_id=run.run_id,
                    task_id=run.task_id,
                    summary="Only terminal Agent runs can be retried.",
                    source_event_type="chat_v2.command.retry_blocked",
                    payload={
                        "current_status": run.status,
                        "surface_turn_id": command.surface_turn_id,
                    },
                )
                self._append_run_event(run.run_id, event)
                return event

            now = _now()
            previous_attempt = {
                "status": run.status,
                "latest_event_type": run.latest_event_type,
                "latest_summary": run.latest_summary,
                "backend_result": dict(run.metadata.get("backend_result") or {}),
                "stop_checkpoint": run.metadata.get("stop_checkpoint", ""),
                "pause_checkpoint": run.metadata.get("pause_checkpoint", ""),
                "recorded_at": now,
            }
            retry_history = [
                dict(item)
                for item in run.metadata.get("retry_history", [])
                if isinstance(item, dict)
            ]
            retry_history.append(previous_attempt)
            retry_count = int(run.metadata.get("retry_count") or 0) + 1
            for key in _INTERRUPTION_METADATA_KEYS:
                run.metadata.pop(key, None)
                task.metadata.pop(key, None)
            run.metadata.pop("backend_result", None)
            run.metadata["retry_count"] = retry_count
            run.metadata["retry_requested_at"] = now
            run.metadata["retry_payload"] = dict(command.payload or {})
            run.metadata["retry_surface_turn_id"] = command.surface_turn_id or ""
            run.metadata["retry_policy"] = "restart_backend_run_from_original_request"
            run.metadata["retry_history"] = retry_history[-20:]
            retry_gap = (
                _queue_text_from_command(command)
                or str(dict(command.payload or {}).get("reason") or "").strip()
                or "Retry requested; continue toward the original user-visible goal."
            )
            original_request = _original_request_from_run(run)
            if original_request:
                run.metadata["original_request"] = original_request
            run.metadata["satisfaction_gap"] = retry_gap
            run.metadata["goal_context"] = {
                "original_request": original_request,
                "current_request": retry_gap,
                "satisfaction_gap": retry_gap,
                "previous_run_id": run.run_id,
                "previous_status": previous_attempt["status"],
                "previous_summary": previous_attempt["latest_summary"],
                "source": "retry",
            }
            run.status = "queued"
            run.latest_event_type = "queued"
            run.latest_summary = "Retry queued for Agent run."
            run.updated_at = now

            task.status = "queued"
            task.phase = "retry_queued"
            task.active_run_id = run.run_id
            task.latest_progress = run.latest_summary
            task.blocker = ""
            task.metadata["retry_count"] = retry_count
            task.metadata["retry_policy"] = run.metadata["retry_policy"]
            task.metadata["retry_history"] = list(run.metadata["retry_history"])
            if original_request:
                task.metadata["original_request"] = original_request
            task.metadata["satisfaction_gap"] = retry_gap
            task.metadata["goal_context"] = dict(run.metadata["goal_context"])
            task.updated_at = now
            self._save_run(run)
            self._save_task(task)

            event = AgentRunEvent(
                type="queued",
                run_id=run.run_id,
                task_id=run.task_id,
                summary=run.latest_summary,
                source_event_type="chat_v2.command.retry_queued",
                payload={
                    "surface_turn_id": command.surface_turn_id,
                    "retry_count": retry_count,
                    "previous_status": previous_attempt["status"],
                    "retry_policy": run.metadata["retry_policy"],
                    **dict(command.payload or {}),
                },
            )
            self._append_run_event(run.run_id, event)
            return event

    def branch_agent_run(self, command: AgentRunCommand) -> AgentRunEvent:
        """Create a sibling queued Agent task/run from an existing run."""

        with self._lock:
            source_run = self.get_run(str(command.run_id or ""))
            source_task_id = str(
                command.task_id
                or (source_run.task_id if source_run is not None else "")
            )
            source_task = self.get_task(source_task_id) if source_task_id else None
            if source_run is None or source_task is None:
                return AgentRunEvent(
                    type="blocked",
                    run_id=command.run_id,
                    task_id=command.task_id,
                    summary="Cannot branch because the source Agent run was not found.",
                    source_event_type="chat_v2.command.branch_failed",
                    payload={"reason": "run_not_found"},
                )

            payload = dict(command.payload or {})
            source_payload = dict(source_run.command.payload or {})
            objective = _queue_text_from_command(command) or str(
                source_payload.get("text") or ""
            )
            label = " ".join(str(payload.get("branch_label") or "").split())
            branch_seed = _stable_id(
                "branch",
                source_run.run_id,
                command.idempotency_key or "",
                command.surface_turn_id or "",
                objective,
                label,
            )
            branch_task_id = _stable_id("branch-task", source_task.task_id, branch_seed)
            branch_run_id = f"arun-branch-{branch_seed[:12]}"
            existing_run = self.get_run(branch_run_id)
            if existing_run is not None:
                event = AgentRunEvent(
                    type="branch_created",
                    run_id=source_run.run_id,
                    task_id=source_task.task_id,
                    summary=f"Branch already exists as Agent run {branch_run_id}.",
                    source_event_type="chat_v2.command.branch_coalesced",
                    payload={
                        "branch_task_id": branch_task_id,
                        "branch_run_id": branch_run_id,
                        "branched_from_run_id": source_run.run_id,
                        "branched_from_task_id": source_task.task_id,
                    },
                )
                self._append_run_event(source_run.run_id, event)
                return event

            suffix = branch_seed[:8]
            branch_topic = f"{source_task.topic_key}:branch:{suffix}"
            branch_thread = str(payload.get("thread_id") or "").strip() or (
                f"{source_run.thread_id or source_task.thread_id}:branch:{suffix}"
            )
            attachments = list(payload.get("attachments") or source_payload.get("attachments") or [])
            history = list(payload.get("history") or source_payload.get("history") or [])
            reply_context = dict(
                payload.get("reply_context") or source_payload.get("reply_context") or {}
            )
            surface_context = dict(
                payload.get("surface_context") or source_payload.get("surface_context") or {}
            )
            branch_task = V2TaskRecord(
                task_id=branch_task_id,
                thread_id=branch_thread,
                workspace_root=source_task.workspace_root,
                workspace_id=source_task.workspace_id,
                topic_key=branch_topic,
                queue_key=f"task:{branch_topic}",
                status="queued",
                phase="branch_queued",
                active_run_id=branch_run_id,
                latest_progress=(
                    f"Branch queued from Agent run {source_run.run_id}."
                ),
                surface_turn_ids=[command.surface_turn_id] if command.surface_turn_id else [],
                metadata={
                    "branch": True,
                    "branch_label": label,
                    "branched_from_run_id": source_run.run_id,
                    "branched_from_task_id": source_task.task_id,
                    "branched_from_thread_id": source_run.thread_id,
                    "workspace_root": source_task.workspace_root,
                    "workspace_id": source_task.workspace_id,
                },
            )
            start_payload = {
                "text": objective,
                "attachments": attachments,
                "workspace_root": source_task.workspace_root,
                "workspace_id": source_task.workspace_id,
                "triage_action": "branch_from",
                "topic_key": branch_topic,
                "history": history,
                "reply_context": reply_context,
                "surface_context": surface_context,
                "branch_label": label,
                "branched_from_run_id": source_run.run_id,
                "branched_from_task_id": source_task.task_id,
                "operator_context": _structured_operator_context(
                    objective,
                    attachments=[
                        dict(item) if isinstance(item, dict) else {"path": str(item)}
                        for item in attachments
                    ],
                ),
            }
            branch_command = AgentRunCommand(
                command="start",
                task_id=branch_task_id,
                run_id=branch_run_id,
                surface_turn_id=command.surface_turn_id,
                idempotency_key=_stable_id("branch-start", branch_task_id, branch_run_id),
                payload=start_payload,
            )
            branch_run = AgentRunRecord(
                run_id=branch_run_id,
                task_id=branch_task_id,
                thread_id=branch_thread,
                workspace_root=source_task.workspace_root,
                workspace_id=source_task.workspace_id,
                status="queued",
                command=branch_command,
                surface_turn_id=command.surface_turn_id,
                metadata={
                    "branch": True,
                    "branch_label": label,
                    "queue_key": branch_task.queue_key,
                    "workspace_root": source_task.workspace_root,
                    "workspace_id": source_task.workspace_id,
                    "branched_from_run_id": source_run.run_id,
                    "branched_from_task_id": source_task.task_id,
                },
            )
            self._save_task(branch_task)
            self._save_run(branch_run)
            accepted = AgentRunEvent(
                type="accepted",
                run_id=branch_run_id,
                task_id=branch_task_id,
                summary=branch_task.latest_progress,
                source_event_type="chat_v2.run.branch_accepted",
                payload={
                    "surface_turn_id": command.surface_turn_id,
                    "branched_from_run_id": source_run.run_id,
                    "branched_from_task_id": source_task.task_id,
                    "branch_label": label,
                    "workspace_root": source_task.workspace_root,
                    "workspace_id": source_task.workspace_id,
                },
            )
            self._append_run_event(branch_run_id, accepted)

            event = AgentRunEvent(
                type="branch_created",
                run_id=source_run.run_id,
                task_id=source_task.task_id,
                summary=f"Created branch Agent run {branch_run_id}.",
                source_event_type="chat_v2.command.branch_created",
                payload={
                    "branch_task_id": branch_task_id,
                    "branch_run_id": branch_run_id,
                    "branch_thread_id": branch_thread,
                    "branch_label": label,
                    "branched_from_run_id": source_run.run_id,
                    "branched_from_task_id": source_task.task_id,
                    "workspace_root": source_task.workspace_root,
                    "workspace_id": source_task.workspace_id,
                },
            )
            self._append_run_event(source_run.run_id, event)
            return event

    def queue_agent_command(self, command: AgentRunCommand) -> AgentRunEvent:
        """Persist an explicit active-run queue command as a durable queue item."""

        lane: QueueLane = (
            "append"
            if command.command == "append_followup"
            else "continue_after_current"
        )
        with self._lock:
            run = self.get_run(str(command.run_id or ""))
            task_id = str(command.task_id or (run.task_id if run is not None else ""))
            task = self.get_task(task_id) if task_id else None
            if task is None:
                event = AgentRunEvent(
                    type="blocked",
                    run_id=command.run_id,
                    task_id=command.task_id,
                    summary="Cannot queue command because the Agent task was not found.",
                    source_event_type="chat_v2.command.queue_failed",
                    payload={
                        "command": command.command,
                        "reason": "task_not_found",
                    },
                )
                if run is not None:
                    self._append_run_event(run.run_id, event)
                return event

            run_id = str(command.run_id or task.active_run_id or "")
            command_payload = dict(command.payload or {})
            text = _queue_text_from_command(command)
            dedup_key = str(command.idempotency_key or "").strip() or _stable_id(
                "command-queue",
                task.task_id,
                lane,
                command.surface_turn_id or "",
                text,
            )
            for item in task.queue_items:
                if item.dedup_key == dedup_key and item.status == "queued":
                    event = AgentRunEvent(
                        type="queue_item_added",
                        run_id=run_id or None,
                        task_id=task.task_id,
                        summary=f"Already queued in {lane.replace('_', '-')} lane.",
                        source_event_type="chat_v2.command.queue_coalesced",
                        payload={
                            "command": command.command,
                            "queue_item_id": item.id,
                            "lane": lane,
                            "queue_position": item.position,
                            "surface_turn_id": command.surface_turn_id,
                        },
                    )
                    if run_id:
                        self._append_run_event(run_id, event)
                    return event

            position = 1 + sum(
                1
                for item in task.queue_items
                if item.lane == lane and item.status == "queued"
            )
            item = QueueItemRecord(
                id=_stable_id(
                    "command-queue-item",
                    task.task_id,
                    lane,
                    command.surface_turn_id or "",
                    dedup_key,
                ),
                task_id=task.task_id,
                lane=lane,
                surface_turn_id=command.surface_turn_id or command.idempotency_key or "",
                text=text,
                dedup_key=dedup_key,
                position=position,
                metadata={
                    "command": command.command,
                    "command_payload": command_payload,
                    "idempotency_key": command.idempotency_key or "",
                    "queue_key": task.queue_key,
                    "workspace_root": task.workspace_root,
                    "workspace_id": task.workspace_id,
                    "operator_context": dict(command_payload.get("operator_context") or {})
                    or _structured_operator_context(
                        text,
                        attachments=list(command_payload.get("attachments") or []),
                    ),
                    "original_request": command_payload.get("original_request") or "",
                    "satisfaction_gap": command_payload.get("satisfaction_gap") or text,
                    "goal_context": dict(command_payload.get("goal_context") or {}),
                    "history": list(command_payload.get("history") or []),
                    "reply_context": dict(command_payload.get("reply_context") or {}),
                    "surface_context": dict(command_payload.get("surface_context") or {}),
                    "attachments": list(command_payload.get("attachments") or []),
                    "profile_policy": dict(command_payload.get("profile_policy") or {}),
                    "mutation_policy": dict(command_payload.get("mutation_policy") or {}),
                    "approval_policy": dict(command_payload.get("approval_policy") or {}),
                    "tool_policy": dict(command_payload.get("tool_policy") or {}),
                    "auto_backend_continuation": bool(
                        command_payload.get("auto_backend_continuation")
                    ),
                    "auto_continuation_depth": command_payload.get("auto_continuation_depth"),
                    "max_auto_backend_continuations": command_payload.get(
                        "max_auto_backend_continuations"
                    ),
                    "previous_run_status": command_payload.get("previous_run_status"),
                    "previous_failure_or_blocker": command_payload.get(
                        "previous_failure_or_blocker"
                    ),
                    "previous_final_response": command_payload.get("previous_final_response"),
                    "previous_validation_summary": command_payload.get(
                        "previous_validation_summary"
                    ),
                    "previous_backend_result": dict(
                        command_payload.get("previous_backend_result") or {}
                    ),
                    "scheduler_budget_extensions": [
                        dict(item)
                        for item in command_payload.get("scheduler_budget_extensions") or []
                        if isinstance(item, dict)
                    ],
                },
            )
            task.queue_items.append(item)
            task.latest_progress = _queue_progress(lane, position)
            if task.status not in {"running", "needs_input", "blocked", "paused"}:
                task.status = "queued"
            task.updated_at = _now()
            self._save_task(task)

            event = AgentRunEvent(
                type="queue_item_added",
                run_id=run_id or None,
                task_id=task.task_id,
                summary=task.latest_progress,
                source_event_type="chat_v2.command.queue_added",
                payload={
                    "command": command.command,
                    "queue_item_id": item.id,
                    "lane": lane,
                    "queue_position": position,
                    "surface_turn_id": command.surface_turn_id,
                    "workspace_root": task.workspace_root,
                    "workspace_id": task.workspace_id,
                    "text": text,
                },
            )
            if run_id:
                self._update_run_progress_for_event(run_id, event)
                self._append_run_event(run_id, event)
            return event

    def claim_queued_run_items(
        self,
        run_id: str,
        *,
        lane: QueueLane = "append",
        checkpoint: str = "",
        limit: int = 16,
    ) -> list[QueueItemRecord]:
        """Mark queued active-run items as admitted at a safe backend checkpoint."""

        with self._lock:
            run = self.get_run(run_id)
            if run is None:
                return []
            task = self.get_task(run.task_id)
            if task is None:
                return []
            queued = [
                item
                for item in task.queue_items
                if item.lane == lane and item.status == "queued"
            ][: max(1, limit)]
            if not queued:
                return []

            now = _now()
            events: list[AgentRunEvent] = []
            for item in queued:
                item.status = "injected"
                item.updated_at = now
                item.metadata["admitted_at"] = now
                item.metadata["admission_checkpoint"] = checkpoint
                events.append(
                    AgentRunEvent(
                        type="queue_item_injected",
                        run_id=run.run_id,
                        task_id=task.task_id,
                        summary=_queue_injected_summary(item, checkpoint=checkpoint),
                        source_event_type="chat_v2.command.queue_injected",
                        payload={
                            "queue_item_id": item.id,
                            "lane": item.lane,
                            "checkpoint": checkpoint,
                            "surface_turn_id": item.surface_turn_id,
                            "text": item.text,
                            "metadata": dict(item.metadata),
                        },
                    )
                )
            task.latest_progress = events[-1].summary
            task.status = "running"
            task.updated_at = now
            self._save_task(task)

            for event in events:
                self.record_agent_event(event)
            return [item.model_copy(deep=True) for item in queued]

    def promote_next_continue_after_current(
        self,
        run_id: str,
    ) -> AgentRunRecord | None:
        """Promote one queued after-current item into the next queued Agent run."""

        with self._lock:
            run = self.get_run(run_id)
            if run is None or run.status not in {"completed", "failed", "blocked", "stopped"}:
                return None
            task = self.get_task(run.task_id)
            if task is None:
                return None
            item = next(
                (
                    candidate
                    for candidate in task.queue_items
                    if candidate.lane == "continue_after_current"
                    and candidate.status == "queued"
                ),
                None,
            )
            if item is None:
                return None

            next_run_id = f"arun-{uuid.uuid4().hex[:12]}"
            now = _now()
            item.status = "injected"
            item.updated_at = now
            item.metadata["admitted_at"] = now
            item.metadata["admission_checkpoint"] = "terminal"
            item.metadata["continued_run_id"] = next_run_id
            self._save_task(task)

            injected = AgentRunEvent(
                type="queue_item_injected",
                run_id=run.run_id,
                task_id=task.task_id,
                summary=(
                    "Promoted after-current follow-up "
                    f"to queued Agent run {next_run_id}."
                ),
                source_event_type="chat_v2.command.continue_promoted",
                payload={
                    "queue_item_id": item.id,
                    "lane": item.lane,
                    "checkpoint": "terminal",
                    "continued_run_id": next_run_id,
                    "surface_turn_id": item.surface_turn_id,
                    "text": item.text,
                    "metadata": dict(item.metadata),
                },
            )
            self.record_agent_event(injected)

            run = self.get_run(run.run_id)
            if run is not None:
                run.metadata["continued_run_id"] = next_run_id
                run.metadata["continued_queue_item_id"] = item.id
                run.updated_at = _now()
                self._save_run(run)

            command = AgentRunCommand(
                command="start",
                task_id=task.task_id,
                run_id=next_run_id,
                surface_turn_id=item.surface_turn_id,
                idempotency_key=_stable_id("continue-start", task.task_id, item.id),
                payload=_start_payload_from_queue_item(
                    task,
                    item,
                    previous_run=run,
                    previous_run_id=run_id,
                    previous_run_events=self.load_run_events(run_id),
                ),
            )
            start_payload = dict(command.payload or {})
            next_run = AgentRunRecord(
                run_id=next_run_id,
                task_id=task.task_id,
                thread_id=task.thread_id,
                workspace_root=task.workspace_root,
                workspace_id=task.workspace_id,
                status="queued",
                command=command,
                surface_turn_id=item.surface_turn_id,
                metadata={
                    "queue_key": task.queue_key,
                    "workspace_root": task.workspace_root,
                    "workspace_id": task.workspace_id,
                    "continued_from_run_id": run_id,
                    "queue_item_id": item.id,
                    "original_request": str(start_payload.get("original_request") or ""),
                    "satisfaction_gap": str(start_payload.get("satisfaction_gap") or ""),
                    "goal_context": dict(start_payload.get("goal_context") or {}),
                },
            )
            task.active_run_id = next_run_id
            task.status = "queued"
            task.phase = "queued_continue_after_current"
            task.latest_progress = (
                f"Queued after-current follow-up as Agent run {next_run_id}."
            )
            task.updated_at = _now()
            self._save_run(next_run)
            self._save_task(task)
            accepted = AgentRunEvent(
                type="accepted",
                run_id=next_run_id,
                task_id=task.task_id,
                summary=task.latest_progress,
                source_event_type="chat_v2.run.continue_accepted",
                payload={
                    "surface_turn_id": item.surface_turn_id,
                    "queue_item_id": item.id,
                    "continued_from_run_id": run_id,
                    "queue_key": task.queue_key,
                    "workspace_root": task.workspace_root,
                    "workspace_id": task.workspace_id,
                },
            )
            self._append_run_event(next_run_id, accepted)
            return next_run

    def _resolve_task_for_turn(
        self,
        turn: SurfaceTurn,
        decision: TriageDecisionRecord,
    ) -> V2TaskRecord:
        explicit_task_id = str(turn.metadata.get("task_id") or "").strip()
        if explicit_task_id:
            existing = self.get_task(explicit_task_id)
            if existing is not None:
                return existing

        if decision.task_binding in {"existing_task", "append", "continue_after_current"}:
            active = self._latest_active_task_for_topic(decision.topic_key)
            if active is None and turn.metadata.get("workspace_source") == "default_home":
                active = self._latest_active_task_for_surface_topic(
                    str(turn.metadata.get("surface_topic_key") or "")
                )
            if active is not None:
                return active

        task_id = _stable_id("task", decision.topic_key, turn.id, turn.text)
        existing = self.get_task(task_id)
        if existing is not None:
            return existing

        return V2TaskRecord(
            task_id=task_id,
            thread_id=turn.thread_id or turn.session_id or decision.topic_key,
            workspace_root=turn.workspace_root,
            workspace_id=turn.workspace_id,
            topic_key=decision.topic_key,
            queue_key=decision.queue_key,
            status="queued",
            phase="accepted",
            latest_progress="Accepted by Chat/Agent V2.",
            metadata={
                "privacy_scope": turn.privacy_scope,
                "surface": turn.surface,
                "surface_type": turn.surface_type,
                "surface_id": turn.surface_id,
                "workspace_root": turn.workspace_root,
                "workspace_id": turn.workspace_id,
                "workspace_source": turn.metadata.get("workspace_source", ""),
                "surface_topic_key": turn.metadata.get("surface_topic_key", ""),
                "native_chat_id": turn.native_chat_id,
                "native_thread_id": turn.native_thread_id,
                "triage_action": decision.action,
                "task_binding": decision.task_binding,
            },
        )

    def _start_run_for_task(
        self,
        task: V2TaskRecord,
        *,
        turn: SurfaceTurn,
        decision: TriageDecisionRecord,
        stream_channel_id: str,
    ) -> V2TurnAcceptance:
        run_id = f"arun-{uuid.uuid4().hex[:12]}"
        command = AgentRunCommand(
            command="start",
            task_id=task.task_id,
            run_id=run_id,
            surface_turn_id=turn.id,
            idempotency_key=_stable_id("start", task.task_id, turn.id),
            payload={
                "text": turn.text,
                "original_request": turn.text,
                "attachments": [
                    item.model_dump(mode="json")
                    for item in turn.attachments
                ],
                "workspace_root": turn.workspace_root,
                "workspace_id": turn.workspace_id,
                "triage_action": decision.action,
                "topic_key": decision.topic_key,
                "history": list(turn.metadata.get("history") or []),
                "reply_context": dict(turn.metadata.get("reply_context") or {}),
                "surface_context": dict(turn.metadata.get("surface_context") or {}),
                "operator_context": _structured_operator_context(
                    turn.text,
                    attachments=[
                        ref.model_dump(mode="json")
                        for ref in turn.attachments
                    ],
                ),
                "goal_context": {
                    "original_request": turn.text,
                    "current_request": turn.text,
                    "source": "initial_surface_turn",
                },
            },
        )
        run = AgentRunRecord(
            run_id=run_id,
            task_id=task.task_id,
            thread_id=task.thread_id,
            workspace_root=task.workspace_root,
            workspace_id=task.workspace_id,
            status="running" if stream_channel_id else "queued",
            command=command,
            surface_turn_id=turn.id,
            stream_channel_id=stream_channel_id,
            metadata={
                "delegated_to": "/api/chat/message" if stream_channel_id else "",
                "queue_key": decision.queue_key,
                "workspace_root": task.workspace_root,
                "workspace_id": task.workspace_id,
                "original_request": turn.text,
            },
        )
        task.status = run.status
        task.phase = "delegated" if stream_channel_id else "queued"
        task.active_run_id = run_id
        task.queue_key = decision.queue_key
        task.latest_progress = (
            "Accepted and delegated to the chat stream machinery."
            if stream_channel_id
            else "Accepted and queued for Agent execution."
        )
        task.updated_at = _now()
        self._save_run(run)
        self._save_task(task)
        event = AgentRunEvent(
            type="accepted",
            run_id=run_id,
            task_id=task.task_id,
            summary=task.latest_progress,
            source_event_type="chat_v2.run.accepted",
            payload={
                "surface_turn_id": turn.id,
                "stream_channel_id": stream_channel_id,
                "queue_key": decision.queue_key,
                "workspace_root": task.workspace_root,
                "workspace_id": task.workspace_id,
            },
        )
        self._append_run_event(run_id, event)
        return V2TurnAcceptance(
            surface_turn_id=turn.id,
            triage_action=decision.action,
            task_id=task.task_id,
            run_id=run_id,
            snapshot=task.snapshot(),
            event=event,
        )

    def _queue_followup(
        self,
        task: V2TaskRecord,
        *,
        turn: SurfaceTurn,
        decision: TriageDecisionRecord,
        lane: QueueLane,
    ) -> V2TurnAcceptance:
        dedup_key = _stable_id(
            "queue",
            task.task_id,
            lane,
            turn.text,
            "|".join(ref.id for ref in turn.attachments),
        )
        for item in task.queue_items:
            if item.dedup_key == dedup_key and item.status == "queued":
                event = AgentRunEvent(
                    type="queue_item_added",
                    run_id=task.active_run_id,
                    task_id=task.task_id,
                    summary=f"Already queued in {lane.replace('_', '-')} lane.",
                    source_event_type="chat_v2.queue.coalesced",
                    payload={"queue_item_id": item.id, "lane": lane},
                )
                return V2TurnAcceptance(
                    surface_turn_id=turn.id,
                    triage_action=decision.action,
                    task_id=task.task_id,
                    run_id=task.active_run_id,
                    queue_item_id=item.id,
                    queue_position=item.position,
                    snapshot=task.snapshot(),
                    event=event,
                    metadata={"coalesced": True},
                )

        position = 1 + sum(
            1
            for item in task.queue_items
            if item.lane == lane and item.status == "queued"
        )
        item = QueueItemRecord(
            id=_stable_id("queue-item", task.task_id, lane, turn.id),
            task_id=task.task_id,
            lane=lane,
            surface_turn_id=turn.id,
            text=turn.text,
            dedup_key=dedup_key,
            position=position,
            metadata={
                "attachments": [
                    ref.model_dump(mode="json")
                    for ref in turn.attachments
                ],
                "queue_key": decision.queue_key,
                "workspace_root": task.workspace_root,
                "workspace_id": task.workspace_id,
                "surface_turn_workspace_root": turn.workspace_root,
                "surface_turn_workspace_id": turn.workspace_id,
                "history": list(turn.metadata.get("history") or []),
                "reply_context": dict(turn.metadata.get("reply_context") or {}),
                "surface_context": dict(turn.metadata.get("surface_context") or {}),
                "operator_context": _structured_operator_context(
                    turn.text,
                    attachments=[
                        ref.model_dump(mode="json")
                        for ref in turn.attachments
                    ],
                ),
                "original_request": _original_request_from_task(task),
                "satisfaction_gap": turn.text,
            },
        )
        task.queue_items.append(item)
        task.queue_key = decision.queue_key
        task.latest_progress = _queue_progress(lane, position)
        task.updated_at = _now()
        self._save_task(task)
        event = AgentRunEvent(
            type="queue_item_added",
            run_id=task.active_run_id,
            task_id=task.task_id,
            summary=task.latest_progress,
            source_event_type="chat_v2.queue.added",
            payload={
                "queue_item_id": item.id,
                "lane": lane,
                "queue_position": position,
                "surface_turn_id": turn.id,
                "workspace_root": task.workspace_root,
                "workspace_id": task.workspace_id,
            },
        )
        if task.active_run_id:
            self._append_run_event(task.active_run_id, event)
        return V2TurnAcceptance(
            surface_turn_id=turn.id,
            triage_action=decision.action,
            task_id=task.task_id,
            run_id=task.active_run_id,
            queue_item_id=item.id,
            queue_position=position,
            snapshot=task.snapshot(),
            event=event,
        )

    def _latest_active_task_for_topic(self, topic_key: str) -> V2TaskRecord | None:
        active_status = {
            "queued",
            "waiting_dependency",
            "running",
            "needs_input",
            "blocked",
            "paused",
        }
        matches = [
            task
            for task in self._iter_tasks()
            if task.topic_key == topic_key and task.status in active_status
        ]
        matches.sort(key=lambda task: task.updated_at, reverse=True)
        return matches[0] if matches else None

    def _latest_active_task_for_surface_topic(
        self,
        surface_topic_key: str,
    ) -> V2TaskRecord | None:
        if not surface_topic_key:
            return None
        active_status = {
            "queued",
            "waiting_dependency",
            "running",
            "needs_input",
            "blocked",
            "paused",
        }
        matches = [
            task
            for task in self._iter_tasks()
            if str(task.metadata.get("surface_topic_key") or "") == surface_topic_key
            and task.status in active_status
        ]
        matches.sort(key=lambda task: task.updated_at, reverse=True)
        return matches[0] if matches else None

    @staticmethod
    def _remember_surface_turn(task: V2TaskRecord, turn: SurfaceTurn) -> None:
        if turn.id not in task.surface_turn_ids:
            task.surface_turn_ids.append(turn.id)
        task.metadata["last_surface_turn"] = {
            "id": turn.id,
            "text": turn.text,
            "attachments": [
                item.model_dump(mode="json")
                for item in turn.attachments
            ],
            "update_handle": (
                turn.update_handle.model_dump(mode="json")
                if turn.update_handle is not None
                else None
            ),
            "workspace_root": turn.workspace_root,
            "workspace_id": turn.workspace_id,
            "surface_topic_key": turn.metadata.get("surface_topic_key", ""),
            "task_workspace_root": task.workspace_root,
            "task_workspace_id": task.workspace_id,
        }

    def _iter_tasks(self) -> list[V2TaskRecord]:
        tasks: list[V2TaskRecord] = []
        for path in self._tasks_dir.glob("*.json"):
            try:
                tasks.append(
                    V2TaskRecord.model_validate_json(path.read_text(encoding="utf-8"))
                )
            except Exception:
                continue
        return tasks

    def _iter_runs(self) -> list[AgentRunRecord]:
        runs: list[AgentRunRecord] = []
        for path in self._runs_dir.glob("*.json"):
            if path.name.endswith(".events.jsonl"):
                continue
            try:
                runs.append(
                    AgentRunRecord.model_validate_json(path.read_text(encoding="utf-8"))
                )
            except Exception:
                continue
        return runs

    def _task_path(self, task_id: str) -> Path:
        return self._tasks_dir / f"{_safe_id(task_id)}.json"

    def _run_path(self, run_id: str) -> Path:
        return self._runs_dir / f"{_safe_id(run_id)}.json"

    def _run_events_path(self, run_id: str) -> Path:
        return self._runs_dir / f"{_safe_id(run_id)}.events.jsonl"

    def _thread_prompt_log_path(self, thread_id: str) -> Path:
        return self._prompt_logs_dir / f"{_safe_id(thread_id)}.md"

    def _save_task(self, task: V2TaskRecord) -> None:
        path = self._task_path(task.task_id)
        _write_json(path, task.model_dump(mode="json"))

    def _save_run(self, run: AgentRunRecord) -> None:
        path = self._run_path(run.run_id)
        _write_json(path, run.model_dump(mode="json"))

    def _append_run_event(self, run_id: str, event: AgentRunEvent) -> None:
        path = self._run_events_path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")

    def _update_run_progress_for_event(
        self,
        run_id: str,
        event: AgentRunEvent,
    ) -> None:
        run = self.get_run(run_id)
        if run is None:
            return
        run.latest_event_type = event.type
        run.latest_summary = event.summary
        run.status = _status_for_event(event.type, fallback=run.status)
        run.updated_at = _now()
        self._save_run(run)


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)


def _agent_event_payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return dict(payload) if isinstance(payload, dict) else {}


def _model_event_name(event: dict[str, Any]) -> str:
    payload = _agent_event_payload(event)
    for key in ("source_event_type", "event", "event_type", "type", "kind", "name"):
        value = event.get(key) if key == "source_event_type" else payload.get(key)
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _debug_value_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _message_debug_text(message: dict[str, Any]) -> str:
    content = _debug_value_text(message.get("content")).strip()
    extras = {
        key: value
        for key, value in message.items()
        if key not in {"role", "content"} and value not in (None, "", [], {})
    }
    if not extras:
        return content or json.dumps(message, ensure_ascii=False, indent=2, default=str)
    sections: list[str] = []
    if content:
        sections.append(content)
    sections.append("Extra message fields:")
    sections.append(json.dumps(extras, ensure_ascii=False, indent=2, default=str))
    return "\n\n".join(sections)


def _fenced_text(value: str) -> str:
    text = str(value or "")
    fence = "````" if "```" in text else "```"
    return f"{fence}text\n{text}\n{fence}"


def _response_log_lines(
    event: dict[str, Any],
    *,
    response_index: int,
) -> list[str]:
    payload = _agent_event_payload(event)
    model = str(payload.get("model") or "(unknown model)")
    finish = str(payload.get("finish_reason") or "").strip() or "not recorded"
    tool_calls = [
        str(item)
        for item in payload.get("tool_calls") or []
        if str(item).strip()
    ]
    response_text = str(
        payload.get("response_text")
        or payload.get("text")
        or event.get("summary")
        or ""
    )
    assistant_message = payload.get("assistant_message")
    if not response_text and isinstance(assistant_message, dict):
        response_text = _message_debug_text(assistant_message)
    lines = [
        f"#### Response {response_index}",
        "",
        f"- Model: `{model}`",
        f"- Finish reason: {finish}",
        f"- Tool calls requested: {', '.join(f'`{tool}`' for tool in tool_calls) if tool_calls else 'none'}",
        "",
    ]
    if response_text:
        lines.append(_fenced_text(response_text))
    else:
        lines.append("_No assistant response text was recorded._")
    lines.append("")
    return lines


def _stable_id(*parts: str) -> str:
    payload = "\n".join(str(part or "") for part in parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def _apply_token_usage_event(
    record: AgentRunRecord | V2TaskRecord,
    event: AgentRunEvent,
) -> None:
    delta = normalize_token_usage(event.token_usage_delta)
    total = normalize_token_usage(event.token_usage_total)
    if total:
        record.token_usage = _prefer_token_usage_total(record.token_usage, total)
    elif delta:
        record.token_usage = merge_token_usage(record.token_usage, delta)

    round_record = _token_usage_round_from_event(event, delta=delta, total=total)
    if round_record:
        record.token_usage_rounds = _merge_token_usage_rounds(
            record.token_usage_rounds,
            [round_record],
        )
    if record.token_usage:
        record.metadata["token_usage"] = dict(record.token_usage)
    if record.token_usage_rounds:
        record.metadata["token_usage_rounds"] = list(record.token_usage_rounds)


def _token_usage_round_from_event(
    event: AgentRunEvent,
    *,
    delta: dict[str, int],
    total: dict[str, int],
) -> dict[str, Any]:
    round_record = dict(event.token_usage_round or {})
    if not round_record and not delta and not total:
        return {}
    if delta:
        round_record["delta"] = dict(delta)
    if total:
        round_record["total"] = dict(total)
    if event.source_event_id:
        round_record.setdefault("source_event_id", event.source_event_id)
    if event.source_event_type:
        round_record.setdefault("source_event_type", event.source_event_type)
    if event.source_event_path:
        round_record.setdefault("source_event_path", event.source_event_path)
    if event.run_id:
        round_record.setdefault("run_id", event.run_id)
    if event.task_id:
        round_record.setdefault("task_id", event.task_id)
    return round_record


def _merge_token_usage_rounds(
    existing: list[dict[str, Any]],
    incoming: list[Any],
) -> list[dict[str, Any]]:
    merged = [dict(item) for item in existing if isinstance(item, dict)]
    seen = {
        _token_usage_round_key(item)
        for item in merged
        if _token_usage_round_key(item)
    }
    for item in incoming:
        if not isinstance(item, dict):
            continue
        normalized = dict(item)
        key = _token_usage_round_key(normalized)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        merged.append(normalized)
    return merged[-1000:]


def _token_usage_round_key(item: dict[str, Any]) -> str:
    for key in ("source_event_id", "model_call_id", "id"):
        value = str(item.get(key) or "").strip()
        if value:
            return f"{key}:{value}"
    source = str(item.get("source_event_type") or "").strip()
    round_id = str(item.get("round") or "").strip()
    if source and round_id:
        return f"{source}:{round_id}"
    return ""


def _prefer_token_usage_total(
    current: dict[str, int],
    candidate: dict[str, int],
) -> dict[str, int]:
    current_usage = normalize_token_usage(current)
    candidate_usage = normalize_token_usage(candidate)
    if not candidate_usage:
        return current_usage
    if not current_usage:
        return candidate_usage
    if int(candidate_usage.get("total_tokens", 0)) >= int(current_usage.get("total_tokens", 0)):
        return candidate_usage
    return current_usage


def _status_for_event(event_type: str, *, fallback: TaskStatus) -> TaskStatus:
    if event_type == "queue_item_injected" and fallback in {
        "completed",
        "failed",
        "blocked",
        "stopped",
    }:
        return fallback
    if event_type in {
        "accepted",
        "background_run_started",
        "planned",
        "worker_started",
        "model_text_delta",
        "tool_used",
        "artifact_changed",
        "validation_started",
        "repair_started",
        "queue_item_injected",
        "status_reported",
        "pause_requested",
        "stop_requested",
        "token_usage_recorded",
    }:
        if event_type == "status_reported":
            return fallback
        return "running"
    if event_type == "waiting_dependency":
        return "waiting_dependency"
    if event_type == "queue_item_added" and fallback in {
        "running",
        "waiting_dependency",
        "needs_input",
        "blocked",
        "paused",
    }:
        return fallback
    if event_type in {"queued", "queue_item_added"}:
        return "queued"
    if event_type == "needs_input":
        return "needs_input"
    if event_type == "paused":
        return "paused"
    if event_type == "completed":
        return "completed"
    if event_type == "failed":
        return "failed"
    if event_type == "blocked":
        return "blocked"
    if event_type == "stopped":
        return "stopped"
    return fallback


def _summary_for_command(command: AgentRunCommand) -> str:
    if command.command == "status":
        return "Status command accepted."
    if command.command == "pause":
        return _pause_requested_summary()
    if command.command in {"stop", "cancel"}:
        return _stop_requested_summary(command.command)
    if command.command == "retry":
        return "Retry command accepted."
    if command.command == "append_followup":
        return "Follow-up queued for checkpoint append."
    if command.command == "continue_after_current":
        return "Follow-up queued after the current run."
    if command.command == "branch_from":
        return "Branch command accepted."
    return f"{command.command} command accepted."


def _stop_requested_summary(command: str) -> str:
    label = "Cancel" if command == "cancel" else "Stop"
    return f"{label} requested; the run will stop at the next safe checkpoint."


def _pause_requested_summary() -> str:
    return "Pause requested; the run will pause at the next safe checkpoint."


def _queue_progress(lane: QueueLane, position: int) -> str:
    if lane == "append":
        return f"Queued for checkpoint append at position {position}."
    return f"Queued to continue after the current run at position {position}."


def _queue_text_from_command(command: AgentRunCommand) -> str:
    payload = dict(command.payload or {})
    for key in ("text", "message", "objective", "content"):
        text = " ".join(str(payload.get(key) or "").split())
        if text:
            return text
    return ""


def _compact_user_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _original_request_from_run(run: AgentRunRecord | None) -> str:
    if run is None:
        return ""
    payload = dict(run.command.payload or {})
    metadata = dict(run.metadata or {})
    for source in (payload, metadata):
        text = _compact_user_text(source.get("original_request"))
        if text:
            return text
        goal_context = source.get("goal_context")
        if isinstance(goal_context, dict):
            text = _compact_user_text(goal_context.get("original_request"))
            if text:
                return text
    return _compact_user_text(payload.get("text"))


def _original_request_from_task(task: V2TaskRecord | None) -> str:
    if task is None:
        return ""
    metadata = dict(task.metadata or {})
    text = _compact_user_text(metadata.get("original_request"))
    if text:
        return text
    goal_context = metadata.get("goal_context")
    if isinstance(goal_context, dict):
        text = _compact_user_text(goal_context.get("original_request"))
        if text:
            return text
    return ""


def _previous_failure_or_blocker_from_result(
    result: dict[str, Any],
    *,
    previous_status: str,
    fallback: str,
) -> str:
    normalized_status = str(previous_status or result.get("status") or "").strip().lower()
    if normalized_status not in {"failed", "blocked", "stopped"}:
        return ""
    raw = result.get("raw_result") if isinstance(result.get("raw_result"), dict) else {}
    return (
        _compact_user_text(result.get("summary"))
        or _compact_user_text(raw.get("error"))
        or _compact_user_text(raw.get("stderr"))
        or fallback
    )


def _previous_final_response_from_result(
    result: dict[str, Any],
    *,
    previous_status: str,
    fallback: str,
) -> str:
    normalized_status = str(previous_status or result.get("status") or "").strip().lower()
    raw = result.get("raw_result") if isinstance(result.get("raw_result"), dict) else {}
    for value in (
        raw.get("final_text"),
        raw.get("final_response"),
        result.get("final_text"),
        result.get("summary") if normalized_status == "completed" else "",
        fallback if normalized_status == "completed" else "",
    ):
        text = _compact_user_text(value)
        if text and text.lower() not in {"codex completed.", "codex completed"}:
            return text
    return ""


def _previous_validation_summary_from_sources(
    result: dict[str, Any],
    events: list[dict[str, Any]],
) -> str:
    raw = result.get("raw_result") if isinstance(result.get("raw_result"), dict) else {}
    for key in ("validation_summary", "validation", "validation_result"):
        text = _compact_user_text(raw.get(key))
        if text:
            return text
    lines: list[str] = []
    for event in reversed(events[-80:]):
        if not isinstance(event, dict):
            continue
        event_name = " ".join(
            str(event.get(key) or "")
            for key in ("type", "source_event_type", "latest_event_type")
        ).lower()
        if "validation" not in event_name:
            continue
        text = _compact_user_text(event.get("summary"))
        if text:
            lines.append(text)
        if len(lines) >= 4:
            break
    lines.reverse()
    return " | ".join(lines)


def _queue_injected_summary(item: QueueItemRecord, *, checkpoint: str = "") -> str:
    lane = "checkpoint append" if item.lane == "append" else "after-current follow-up"
    text = " ".join(str(item.text or "").split())
    if len(text) > 140:
        text = text[:137].rstrip() + "..."
    suffix = f" at {checkpoint}" if checkpoint else ""
    if text:
        return f"Admitted {lane}{suffix}: {text}"
    return f"Admitted {lane}{suffix}."


def _start_payload_from_queue_item(
    task: V2TaskRecord,
    item: QueueItemRecord,
    *,
    previous_run: AgentRunRecord | None = None,
    previous_run_id: str,
    previous_run_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    metadata = dict(item.metadata or {})
    previous_payload = dict(previous_run.command.payload or {}) if previous_run is not None else {}
    original_request = (
        _compact_user_text(metadata.get("original_request"))
        or _original_request_from_run(previous_run)
        or _original_request_from_task(task)
        or item.text
    )
    satisfaction_gap = _compact_user_text(metadata.get("satisfaction_gap")) or item.text
    previous_result = dict(previous_run.metadata.get("backend_result") or {}) if previous_run is not None else {}
    previous_summary = (
        _compact_user_text(previous_result.get("summary"))
        or _compact_user_text(previous_run.latest_summary if previous_run is not None else "")
    )
    previous_status = str(previous_run.status) if previous_run is not None else ""
    previous_failure_or_blocker = _previous_failure_or_blocker_from_result(
        previous_result,
        previous_status=previous_status,
        fallback=previous_summary,
    )
    previous_final_response = _previous_final_response_from_result(
        previous_result,
        previous_status=previous_status,
        fallback=previous_summary,
    )
    previous_validation_summary = _previous_validation_summary_from_sources(
        previous_result,
        previous_run_events or [],
    )
    previous_failure_or_blocker = (
        _compact_user_text(metadata.get("previous_failure_or_blocker"))
        or previous_failure_or_blocker
    )
    previous_final_response = (
        _compact_user_text(metadata.get("previous_final_response"))
        or previous_final_response
    )
    previous_validation_summary = (
        _compact_user_text(metadata.get("previous_validation_summary"))
        or previous_validation_summary
    )
    goal_context = {
        "original_request": original_request,
        "current_request": item.text,
        "satisfaction_gap": satisfaction_gap,
        "previous_run_id": previous_run_id,
        "previous_status": previous_status,
        "previous_summary": previous_summary,
        "previous_failure_or_blocker": previous_failure_or_blocker,
        "previous_final_response": previous_final_response,
        "previous_validation_summary": previous_validation_summary,
        "queue_item_id": item.id,
        "source": "continue_after_current",
    }
    payload = {
        "text": item.text,
        "original_request": original_request,
        "follow_up_request": item.text,
        "satisfaction_gap": satisfaction_gap,
        "goal_context": goal_context,
        "attachments": list(metadata.get("attachments") or []),
        "workspace_root": task.workspace_root,
        "workspace_id": task.workspace_id,
        "triage_action": "continue_after_current",
        "topic_key": task.topic_key,
        "history": list(metadata.get("history") or []),
        "reply_context": dict(metadata.get("reply_context") or {}),
        "surface_context": dict(metadata.get("surface_context") or {}),
        "continued_from_run_id": previous_run_id,
        "previous_run_status": previous_status,
        "previous_failure_or_blocker": previous_failure_or_blocker,
        "previous_final_response": previous_final_response,
        "previous_validation_summary": previous_validation_summary,
        "queue_item_id": item.id,
        "operator_context": dict(metadata.get("operator_context") or {}),
    }
    if previous_result:
        payload["previous_backend_result"] = previous_result
    for key in (
        "profile_policy",
        "mutation_policy",
        "approval_policy",
        "tool_policy",
    ):
        value = metadata.get(key)
        # Reclassify each human follow-up; a read-only prior run must not stamp
        # the next run's mutation policy before backend intent checks rerun.
        if not value and key != "mutation_policy":
            value = previous_payload.get(key)
        if isinstance(value, dict) and value:
            payload[key] = dict(value)
    for key in (
        "auto_backend_continuation",
        "auto_continuation_depth",
        "max_auto_backend_continuations",
        "previous_backend_result",
        "scheduler_budget_extensions",
    ):
        if key in metadata and metadata.get(key) not in (None, ""):
            payload[key] = metadata.get(key)
        elif key in previous_payload and previous_payload.get(key) not in (None, ""):
            payload[key] = previous_payload.get(key)
    return payload


def _structured_operator_context(
    text: str,
    *,
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    clean = " ".join(str(text or "").split())
    lower = clean.lower()
    context: dict[str, Any] = {
        "raw_text": clean,
        "hard_constraints": [],
        "soft_preferences": [],
        "target_paths": _operator_context_paths(clean),
        "validation_requirements": [],
        "follow_up_objective": clean,
        "attachments": list(attachments or []),
    }
    if _contains_any(
        lower,
        (
            "do not",
            "don't",
            "must not",
            "never",
            "only ",
            "must ",
            "required",
            "require ",
        ),
    ):
        context["hard_constraints"].append(clean)
    if _contains_any(lower, ("prefer", "if possible", "nice to", "try to", "should ")):
        context["soft_preferences"].append(clean)
    if _contains_any(
        lower,
        (
            "test",
            "tests",
            "pytest",
            "validate",
            "validation",
            "verify",
            "check ",
            "lint",
            "typecheck",
        ),
    ):
        context["validation_requirements"].append(clean)
    return context


def structured_operator_context(
    text: str,
    *,
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Public wrapper for compact operator-context extraction."""

    return _structured_operator_context(text, attachments=attachments)


def _operator_context_paths(text: str) -> list[str]:
    file_pattern = re.compile(
        r"(?<![\w$])(?:[./~\w-]+/)?[\w.-]+\."
        r"(?:py|md|txt|json|jsonl|yaml|yml|toml|html|css|js|jsx|ts|tsx|csv|parquet|sql|sh)"
    )
    absolute_path_pattern = re.compile(
        r"(?<![:/\w$])(?:/|~/(?:[^/\s\"'`,;:()[\]{}<>]+/)*)"
        r"[^/\s\"'`,;:()[\]{}<>]+(?:/[^/\s\"'`,;:()[\]{}<>]+)*"
    )
    seen: set[str] = set()
    paths: list[str] = []
    for pattern in (file_pattern, absolute_path_pattern):
        for match in pattern.finditer(text):
            value = match.group(0).strip(".,;:()[]{}\"'?")
            if value and value not in seen:
                seen.add(value)
                paths.append(value)
    return paths[:20]


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)
