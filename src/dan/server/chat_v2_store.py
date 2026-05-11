"""Durable Chat/Agent V2 task, queue, and run state."""

from __future__ import annotations

import hashlib
import json
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
    "running",
    "needs_input",
    "completed",
    "failed",
    "blocked",
    "stopped",
]
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

    def get_run(self, run_id: str) -> AgentRunRecord | None:
        path = self._run_path(run_id)
        if not path.exists():
            return None
        try:
            return AgentRunRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            return None

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

        event_type = {
            "stop": "stopped",
            "approve": "accepted",
            "deny": "blocked",
            "retry": "queued",
            "resume": "queued",
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
        active_status = {"queued", "running", "needs_input", "blocked"}
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
        active_status = {"queued", "running", "needs_input", "blocked"}
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

    def _task_path(self, task_id: str) -> Path:
        return self._tasks_dir / f"{_safe_id(task_id)}.json"

    def _run_path(self, run_id: str) -> Path:
        return self._runs_dir / f"{_safe_id(run_id)}.json"

    def _run_events_path(self, run_id: str) -> Path:
        return self._runs_dir / f"{_safe_id(run_id)}.events.jsonl"

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


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)


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
    if event_type in {
        "accepted",
        "planned",
        "worker_started",
        "model_text_delta",
        "tool_used",
        "artifact_changed",
        "validation_started",
        "repair_started",
        "queue_item_injected",
        "token_usage_recorded",
    }:
        return "running"
    if event_type in {"queued", "queue_item_added"}:
        return "queued"
    if event_type == "needs_input":
        return "needs_input"
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
    if command.command == "stop":
        return "Stop command accepted."
    if command.command == "retry":
        return "Retry command accepted."
    if command.command == "append_followup":
        return "Follow-up queued for checkpoint append."
    if command.command == "continue_after_current":
        return "Follow-up queued after the current run."
    if command.command == "branch_from":
        return "Branch command accepted."
    return f"{command.command} command accepted."


def _queue_progress(lane: QueueLane, position: int) -> str:
    if lane == "append":
        return f"Queued for checkpoint append at position {position}."
    return f"Queued to continue after the current run at position {position}."
