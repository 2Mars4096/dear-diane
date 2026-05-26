"""Foreground admission and durable board projection for Chat/Agent V2.

This module keeps the user-facing control loop separate from background
executor work. It classifies a new turn against the compact task board and
persists only the scheduling decision, never raw executor transcripts.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from dan.server.chat_v2 import (
    AgentRunCommand,
    AgentRunEvent,
    SurfaceTurn,
    TriageDecisionRecord,
    V2BridgeContext,
    triage_surface_turn,
)
from dan.server.chat_v2_store import (
    AgentRunRecord,
    ChatV2Store,
    TaskStatus,
    V2TaskRecord,
    V2TurnAcceptance,
    structured_operator_context,
)


AdmissionAction = Literal[
    "chat_or_status",
    "append_to_active",
    "queue_after",
    "start_parallel",
    "ask_clarification",
    "reject_or_defer",
]
RelationLabel = Literal[
    "independent",
    "dependent_or_conflicting",
    "conflicting",
    "continuation",
    "unclear",
]


class BoardRun(BaseModel):
    run_id: str
    task_id: str
    objective: str = ""
    backend: str = ""
    status: str = "queued"
    phase: str = ""
    workspace_root: str = ""
    workspace_id: str = ""
    thread_id: str = ""
    owned_paths: list[str] = Field(default_factory=list)
    claimed_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    event_log_path: str = ""
    trace_refs: list[str] = Field(default_factory=list)
    terminal_result_ref: dict[str, Any] = Field(default_factory=dict)
    latest_summary: str = ""
    admission_action: str = ""
    admission_reason: str = ""
    relation: str = ""
    depends_on_task_ids: list[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class BoardTask(BaseModel):
    task_id: str
    objective: str = ""
    status: str = "queued"
    phase: str = ""
    owner_surface: str = ""
    workspace_root: str = ""
    workspace_id: str = ""
    thread_id: str = ""
    dependencies: list[str] = Field(default_factory=list)
    current_run_ids: list[str] = Field(default_factory=list)
    latest_summary: str = ""
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class BoardQueueItem(BaseModel):
    id: str
    task_id: str
    run_id: str = ""
    lane: str = ""
    status: str = "queued"
    text: str = ""
    admission_action: str = ""
    dependency_reason: str = ""
    operator_context: dict[str, Any] = Field(default_factory=dict)
    position: int = 0
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskBoard(BaseModel):
    workspace_root: str = ""
    workspace_id: str = ""
    thread_id: str = ""
    active_task_ids: list[str] = Field(default_factory=list)
    queued_task_ids: list[str] = Field(default_factory=list)
    completed_task_ids: list[str] = Field(default_factory=list)
    branch_lineage: dict[str, Any] = Field(default_factory=dict)
    capacity_policy: dict[str, Any] = Field(default_factory=dict)


class BoardSnapshot(BaseModel):
    board: TaskBoard
    tasks: list[BoardTask] = Field(default_factory=list)
    active_runs: list[BoardRun] = Field(default_factory=list)
    queued_runs: list[BoardRun] = Field(default_factory=list)
    completed_runs: list[BoardRun] = Field(default_factory=list)
    queue_items: list[BoardQueueItem] = Field(default_factory=list)
    latest_summaries: list[str] = Field(default_factory=list)


class AdmissionCommandHints(BaseModel):
    command: str = ""
    explicit_status: bool = False
    explicit_append: bool = False
    explicit_new: bool = False
    explicit_pause: bool = False
    explicit_cancel: bool = False
    explicit_resume: bool = False
    target_id: str = ""
    payload_text: str = ""


class ForegroundAdmissionInput(BaseModel):
    surface_turn: SurfaceTurn
    user_text: str = ""
    surface_metadata: dict[str, Any] = Field(default_factory=dict)
    workspace_root: str = ""
    workspace_id: str = ""
    thread_id: str = ""
    task_id: str = ""
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    selected_skills: list[str] = Field(default_factory=list)
    visible_context: list[dict[str, Any]] = Field(default_factory=list)
    board_snapshot: BoardSnapshot
    command_hints: AdmissionCommandHints
    operator_context: dict[str, Any] = Field(default_factory=dict)


class AdmissionDecision(BaseModel):
    action: AdmissionAction
    relation: RelationLabel = "unclear"
    reason: str = ""
    question: str = ""
    task_id: str | None = None
    run_id: str | None = None
    queue_item_id: str | None = None
    queue_position: int | None = None
    target_task_id: str | None = None
    target_run_id: str | None = None
    owned_paths: list[str] = Field(default_factory=list)
    depends_on_task_ids: list[str] = Field(default_factory=list)
    capacity_blocked: bool = False
    status_text: str = ""
    event: AgentRunEvent | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ForegroundAdmissionResult(BaseModel):
    input: ForegroundAdmissionInput
    decision: AdmissionDecision
    board: BoardSnapshot
    acceptance: V2TurnAcceptance | None = None
    event: AgentRunEvent | None = None


def build_task_board_snapshot(
    store: ChatV2Store,
    *,
    workspace_root: str = "",
    thread_id: str = "",
    limit: int = 100,
    max_parallel_runs: int = 4,
    surface_topic_key: str = "",
) -> BoardSnapshot:
    """Project persisted V2 task/run records into the compact async board."""

    tasks = store.list_task_records(
        workspace_root=workspace_root,
        thread_id=thread_id,
        limit=limit,
    )
    if surface_topic_key:
        wanted = str(surface_topic_key)
        tasks = [
            task
            for task in tasks
            if str(task.metadata.get("surface_topic_key") or "") == wanted
        ]
    runs = store.list_run_records(
        workspace_root=workspace_root,
        thread_id=thread_id,
        limit=limit,
    )
    if surface_topic_key:
        task_ids = {task.task_id for task in tasks}
        runs = [run for run in runs if run.task_id in task_ids]
    tasks_by_id = {task.task_id: task for task in tasks}
    task_rows = [_board_task_from_record(task, runs) for task in tasks]
    active_runs: list[BoardRun] = []
    queued_runs: list[BoardRun] = []
    completed_runs: list[BoardRun] = []
    latest_summaries: list[str] = []
    queue_items: list[BoardQueueItem] = []

    for run in runs:
        row = _board_run_from_record(run, tasks_by_id.get(run.task_id))
        bucket = _run_bucket(row.status)
        if bucket == "active":
            active_runs.append(row)
        elif bucket == "queued":
            queued_runs.append(row)
        else:
            completed_runs.append(row)
        if row.latest_summary:
            latest_summaries.append(row.latest_summary)
        if row.status == "waiting_dependency":
            queue_items.append(
                BoardQueueItem(
                    id=f"dependency:{row.run_id}",
                    task_id=row.task_id,
                    run_id=row.run_id,
                    lane="queue_after",
                    status="waiting_dependency",
                    text=row.objective,
                    admission_action=row.admission_action or "queue_after",
                    dependency_reason=row.admission_reason,
                    operator_context=dict(row.metadata.get("operator_context") or {}),
                    position=len(queue_items) + 1,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                    metadata={
                        "depends_on_task_ids": list(row.depends_on_task_ids),
                        "relation": row.relation,
                    },
                )
            )

    for task in tasks:
        for item in task.queue_items:
            if item.status != "queued":
                continue
            metadata = dict(item.metadata or {})
            queue_items.append(
                BoardQueueItem(
                    id=item.id,
                    task_id=item.task_id,
                    run_id=task.active_run_id or "",
                    lane=item.lane,
                    status=item.status,
                    text=item.text,
                    admission_action=str(metadata.get("admission_action") or item.lane),
                    dependency_reason=str(metadata.get("dependency_reason") or ""),
                    operator_context=dict(metadata.get("operator_context") or {}),
                    position=item.position,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                    metadata=metadata,
                )
            )

    active_task_ids = _unique([run.task_id for run in active_runs])
    queued_task_ids = _unique([run.task_id for run in queued_runs])
    completed_task_ids = _unique([run.task_id for run in completed_runs[:10]])
    board = TaskBoard(
        workspace_root=workspace_root,
        thread_id=thread_id,
        active_task_ids=active_task_ids,
        queued_task_ids=queued_task_ids,
        completed_task_ids=completed_task_ids,
        capacity_policy={
            "max_parallel_runs": max_parallel_runs,
            "active_run_count": len(active_runs),
            "available_slots": max(0, max_parallel_runs - len(active_runs)),
        },
    )
    if task_rows:
        board.workspace_id = task_rows[0].workspace_id
        board.workspace_root = board.workspace_root or task_rows[0].workspace_root
        board.thread_id = board.thread_id or task_rows[0].thread_id
    return BoardSnapshot(
        board=board,
        tasks=task_rows,
        active_runs=active_runs,
        queued_runs=queued_runs,
        completed_runs=completed_runs[:20],
        queue_items=queue_items,
        latest_summaries=latest_summaries[:12],
    )


def build_foreground_admission_input(
    store: ChatV2Store,
    turn: SurfaceTurn,
    *,
    selected_skills: list[str] | None = None,
    visible_context: list[dict[str, Any]] | None = None,
    max_parallel_runs: int = 4,
) -> ForegroundAdmissionInput:
    """Build the cheap foreground context for one new turn."""

    attachments = [item.model_dump(mode="json") for item in turn.attachments]
    surface_topic_key = str(turn.metadata.get("surface_topic_key") or "")
    board = build_task_board_snapshot(
        store,
        workspace_root=turn.workspace_root,
        thread_id=turn.thread_id,
        max_parallel_runs=max_parallel_runs,
        surface_topic_key=surface_topic_key,
    )
    operator_context = structured_operator_context(turn.text, attachments=attachments)
    return ForegroundAdmissionInput(
        surface_turn=turn,
        user_text=turn.text,
        surface_metadata={
            "surface": turn.surface,
            "surface_type": turn.surface_type,
            "surface_id": turn.surface_id,
            "session_id": turn.session_id,
            "privacy_scope": turn.privacy_scope,
            "native_chat_id": turn.native_chat_id,
            "native_thread_id": turn.native_thread_id,
        },
        workspace_root=turn.workspace_root,
        workspace_id=turn.workspace_id,
        thread_id=turn.thread_id,
        task_id=str(turn.metadata.get("task_id") or ""),
        attachments=attachments,
        selected_skills=list(selected_skills or []),
        visible_context=list(visible_context or _visible_context_from_turn(turn)),
        board_snapshot=board,
        command_hints=parse_admission_command(turn.text),
        operator_context=operator_context,
    )


def classify_foreground_turn(admission: ForegroundAdmissionInput) -> AdmissionDecision:
    """Classify a turn from deterministic command/path evidence only."""

    hints = admission.command_hints
    board = admission.board_snapshot
    owned_paths = _target_paths_from_admission(admission)
    if hints.explicit_status:
        return AdmissionDecision(
            action="chat_or_status",
            relation="independent",
            reason="explicit status request",
            owned_paths=owned_paths,
            status_text=format_board_status(board),
            metadata={"command": hints.command},
        )
    if hints.explicit_pause or hints.explicit_cancel or hints.explicit_resume:
        return _classify_control_command(admission, owned_paths=owned_paths)
    if hints.explicit_append:
        target = _select_active_target(board, target_id=hints.target_id)
        if target is None:
            return AdmissionDecision(
                action="ask_clarification",
                relation="unclear",
                reason="append requested but no unambiguous active run exists",
                question="Which active task or run should I append this to?",
                owned_paths=owned_paths,
            )
        return AdmissionDecision(
            action="append_to_active",
            relation="continuation",
            reason="explicit append command targets the active run",
            target_task_id=target.task_id,
            target_run_id=target.run_id,
            owned_paths=owned_paths,
        )

    active_and_queued = board.active_runs + board.queued_runs
    if not active_and_queued:
        return AdmissionDecision(
            action="start_parallel",
            relation="independent",
            reason="no active or queued executor work in this board scope",
            owned_paths=owned_paths,
        )

    conflicts = _path_conflicts(owned_paths, active_and_queued)
    if conflicts:
        deps = _unique([run.task_id for run, _path in conflicts])
        target = conflicts[0][0]
        paths = _unique([path for _run, path in conflicts])
        reason = "path conflict: " + ", ".join(paths)
        return AdmissionDecision(
            action="queue_after",
            relation="conflicting",
            reason=reason,
            target_task_id=target.task_id,
            target_run_id=target.run_id,
            owned_paths=owned_paths,
            depends_on_task_ids=deps,
            metadata={"conflicting_paths": paths},
        )

    active_count = len(board.active_runs)
    max_parallel = int(board.board.capacity_policy.get("max_parallel_runs") or 4)
    if active_count >= max_parallel:
        deps = _unique([run.task_id for run in board.active_runs])
        return AdmissionDecision(
            action="queue_after",
            relation="independent",
            reason=f"parallel capacity is full ({active_count}/{max_parallel})",
            owned_paths=owned_paths,
            depends_on_task_ids=deps,
            capacity_blocked=True,
        )

    if owned_paths or hints.explicit_new:
        return AdmissionDecision(
            action="start_parallel",
            relation="independent",
            reason=(
                "explicit target paths do not overlap active or queued runs"
                if owned_paths
                else "explicit separate task requested"
            ),
            owned_paths=owned_paths,
        )

    return AdmissionDecision(
        action="ask_clarification",
        relation="unclear",
        reason="no deterministic target path or explicit separate-task evidence",
        question=(
            "Should this be appended to an active task, queued behind one, "
            "or started as a separate task?"
        ),
        owned_paths=owned_paths,
    )


def admit_foreground_turn(
    store: ChatV2Store,
    turn: SurfaceTurn,
    *,
    max_parallel_runs: int = 4,
) -> ForegroundAdmissionResult:
    """Classify and persist a foreground turn without waiting for executors."""

    admission = build_foreground_admission_input(
        store,
        turn,
        max_parallel_runs=max_parallel_runs,
    )
    decision = classify_foreground_turn(admission)
    acceptance: V2TurnAcceptance | None = None
    event: AgentRunEvent | None = None

    if decision.action == "start_parallel":
        acceptance = _create_new_agent_run(
            store,
            turn,
            decision,
            status="queued",
            phase="admitted_parallel",
        )
        decision = decision.model_copy(
            update={
                "task_id": acceptance.task_id,
                "run_id": acceptance.run_id,
                "event": acceptance.event,
            }
        )
        event = acceptance.event
    elif decision.action == "queue_after":
        acceptance = _create_new_agent_run(
            store,
            turn,
            decision,
            status="waiting_dependency",
            phase="waiting_dependency",
        )
        decision = decision.model_copy(
            update={
                "task_id": acceptance.task_id,
                "run_id": acceptance.run_id,
                "event": acceptance.event,
            }
        )
        event = acceptance.event
    elif decision.action == "append_to_active":
        decision, event = _persist_append(store, turn, decision)
    elif decision.metadata.get("control_command"):
        decision, event = _persist_control_command(store, turn, decision)

    refreshed = build_task_board_snapshot(
        store,
        workspace_root=turn.workspace_root,
        thread_id=turn.thread_id,
        max_parallel_runs=max_parallel_runs,
        surface_topic_key=str(turn.metadata.get("surface_topic_key") or ""),
    )
    return ForegroundAdmissionResult(
        input=admission,
        decision=decision,
        board=refreshed,
        acceptance=acceptance,
        event=event,
    )


def mark_background_run_started(
    store: ChatV2Store,
    run_id: str,
    *,
    backend: str = "",
    reason: str = "background executor slot started",
) -> AgentRunRecord | None:
    """Record that an admitted run has been handed to a background executor."""

    run = store.get_run(run_id)
    if run is None:
        return None
    event = AgentRunEvent(
        type="background_run_started",
        run_id=run.run_id,
        task_id=run.task_id,
        summary="Background Agent run started.",
        source_event_type="chat_v2.background_run.started",
        payload={
            "backend": backend,
            "reason": reason,
            "workspace_root": run.workspace_root,
            "workspace_id": run.workspace_id,
        },
    )
    return store.annotate_run_for_board(
        run_id,
        run_metadata={
            "execution_mode": "background",
            "requested_backend": backend,
            "background_started_reason": reason,
        },
        status="running",
        phase="background_running",
        latest_progress=event.summary,
        event=event,
    )


def format_board_status(board: BoardSnapshot) -> str:
    """Return a compact narrator/status summary of the board."""

    parts: list[str] = []
    if board.active_runs:
        parts.append(
            "Active: "
            + ", ".join(_short_run_status(run) for run in board.active_runs[:4])
        )
    if board.queued_runs:
        parts.append(
            "Queued: "
            + ", ".join(_short_run_status(run) for run in board.queued_runs[:4])
        )
    if board.completed_runs:
        parts.append(
            "Recent: "
            + ", ".join(_short_run_status(run) for run in board.completed_runs[:3])
        )
    return " ".join(parts) if parts else "No active or queued Agent runs."


def parse_admission_command(text: str) -> AdmissionCommandHints:
    stripped = " ".join(str(text or "").split())
    if not stripped:
        return AdmissionCommandHints()
    first, _, rest = stripped.partition(" ")
    command = first.lower()
    target_id = _first_target_id(rest)
    payload_text = rest
    if target_id and rest.startswith(target_id):
        payload_text = rest[len(target_id) :].strip()
    if command in {"/status", "status", "/tasks", "tasks", "progress"}:
        return AdmissionCommandHints(
            command=command,
            explicit_status=True,
            target_id=target_id,
            payload_text=payload_text,
        )
    if command in {"/append", "/continue", "/inject"}:
        return AdmissionCommandHints(
            command=command,
            explicit_append=True,
            target_id=target_id,
            payload_text=payload_text,
        )
    if command in {"/new", "/separate"}:
        return AdmissionCommandHints(
            command=command,
            explicit_new=True,
            target_id=target_id,
            payload_text=payload_text,
        )
    if command in {"/pause", "pause"}:
        return AdmissionCommandHints(
            command="pause",
            explicit_pause=True,
            target_id=target_id,
            payload_text=payload_text,
        )
    if command in {"/cancel", "cancel", "/stop", "stop"}:
        return AdmissionCommandHints(
            command="cancel",
            explicit_cancel=True,
            target_id=target_id,
            payload_text=payload_text,
        )
    if command in {"/resume", "resume"}:
        return AdmissionCommandHints(
            command="resume",
            explicit_resume=True,
            target_id=target_id,
            payload_text=payload_text,
        )
    return AdmissionCommandHints(payload_text=stripped)


def _board_task_from_record(
    task: V2TaskRecord,
    runs: list[AgentRunRecord],
) -> BoardTask:
    current_runs = [
        run.run_id
        for run in runs
        if run.task_id == task.task_id
        and run.status not in {"completed", "failed", "blocked", "stopped"}
    ]
    deps = [
        str(item)
        for item in task.metadata.get("depends_on_task_ids", [])
        if str(item).strip()
    ]
    objective = _task_objective(task, runs)
    return BoardTask(
        task_id=task.task_id,
        objective=objective,
        status=task.status,
        phase=task.phase,
        owner_surface=str(
            task.metadata.get("surface") or task.metadata.get("surface_type") or ""
        ),
        workspace_root=task.workspace_root,
        workspace_id=task.workspace_id,
        thread_id=task.thread_id,
        dependencies=deps,
        current_run_ids=current_runs,
        latest_summary=task.latest_progress,
        created_at=task.created_at,
        updated_at=task.updated_at,
        metadata=dict(task.metadata),
    )


def _board_run_from_record(
    run: AgentRunRecord,
    task: V2TaskRecord | None,
) -> BoardRun:
    payload = dict(run.command.payload or {})
    metadata = dict(run.metadata or {})
    result = dict(metadata.get("backend_result") or {})
    trace_refs = _trace_refs(run, task)
    event_log_path = next((ref for ref in trace_refs if ref.endswith(".jsonl")), "")
    backend = str(
        metadata.get("selected_backend")
        or metadata.get("requested_backend")
        or payload.get("backend")
        or ""
    )
    return BoardRun(
        run_id=run.run_id,
        task_id=run.task_id,
        objective=str(payload.get("text") or ""),
        backend=backend,
        status=run.status,
        phase=str(
            (task.phase if task is not None else "") or metadata.get("phase") or ""
        ),
        workspace_root=run.workspace_root,
        workspace_id=run.workspace_id,
        thread_id=run.thread_id,
        owned_paths=_owned_paths_for_run(run),
        claimed_artifacts=[
            dict(item) if isinstance(item, dict) else {"path": str(item)}
            for item in result.get("artifact_refs", [])
        ],
        event_log_path=event_log_path,
        trace_refs=trace_refs,
        terminal_result_ref=result,
        latest_summary=run.latest_summary
        or (task.latest_progress if task is not None else ""),
        admission_action=str(metadata.get("admission_action") or ""),
        admission_reason=str(metadata.get("admission_reason") or ""),
        relation=str(metadata.get("admission_relation") or ""),
        depends_on_task_ids=[
            str(item)
            for item in metadata.get("depends_on_task_ids", [])
            if str(item).strip()
        ],
        created_at=run.created_at,
        updated_at=run.updated_at,
        metadata=metadata,
    )


def _task_objective(task: V2TaskRecord, runs: list[AgentRunRecord]) -> str:
    for run in runs:
        if run.task_id != task.task_id:
            continue
        text = str(dict(run.command.payload or {}).get("text") or "").strip()
        if text:
            return text
    turn = dict(task.metadata.get("last_surface_turn") or {})
    return str(turn.get("text") or task.latest_progress or "")


def _trace_refs(run: AgentRunRecord, task: V2TaskRecord | None) -> list[str]:
    refs: list[str] = []
    result = dict(run.metadata.get("backend_result") or {})
    for value in result.get("trace_refs", []) or []:
        _append_unique(refs, str(value), limit=20)
    for value in task.trace_refs if task is not None else []:
        _append_unique(refs, str(value), limit=20)
    return refs


def _owned_paths_for_run(run: AgentRunRecord) -> list[str]:
    metadata = dict(run.metadata or {})
    payload = dict(run.command.payload or {})
    operator_context = dict(
        payload.get("operator_context")
        or metadata.get("operator_context")
        or {}
    )
    paths: list[str] = []
    for key in ("owned_paths", "target_paths", "claimed_paths"):
        values = metadata.get(key)
        if isinstance(values, str):
            _append_unique(paths, values, limit=50)
        elif isinstance(values, list):
            for value in values:
                _append_unique(paths, str(value), limit=50)
    for value in operator_context.get("target_paths", []) or []:
        _append_unique(paths, str(value), limit=50)
    return [_normalize_path_key(path) for path in paths if _normalize_path_key(path)]


def _run_bucket(status: str) -> Literal["active", "queued", "completed"]:
    normalized = str(status or "").strip().lower()
    if normalized in {"completed", "failed", "stopped"}:
        return "completed"
    if normalized in {"queued", "waiting_dependency"}:
        return "queued"
    return "active"


def _target_paths_from_admission(admission: ForegroundAdmissionInput) -> list[str]:
    paths = [
        _normalize_path_key(path)
        for path in admission.operator_context.get("target_paths", []) or []
    ]
    for item in admission.attachments:
        if not isinstance(item, dict):
            continue
        local_path = str(item.get("local_path") or "").strip()
        if local_path:
            _append_unique(paths, _normalize_path_key(local_path), limit=50)
    return [path for path in _unique(paths) if path]


def _path_conflicts(
    candidate_paths: list[str],
    runs: list[BoardRun],
) -> list[tuple[BoardRun, str]]:
    conflicts: list[tuple[BoardRun, str]] = []
    if not candidate_paths:
        return conflicts
    for run in runs:
        for owned in run.owned_paths:
            for candidate in candidate_paths:
                if _paths_overlap(candidate, owned):
                    conflicts.append(
                        (
                            run,
                            candidate
                            if candidate == owned
                            else f"{candidate} vs {owned}",
                        )
                    )
    return conflicts


def _paths_overlap(left: str, right: str) -> bool:
    left_key = _normalize_path_key(left)
    right_key = _normalize_path_key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    left_prefix = left_key.rstrip("/") + "/"
    right_prefix = right_key.rstrip("/") + "/"
    return left_prefix.startswith(right_prefix) or right_prefix.startswith(left_prefix)


def _normalize_path_key(path: str) -> str:
    text = str(path or "").strip().strip(".,;:()[]{}\"'")
    if not text:
        return ""
    text = text.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    text = re.sub(r"/+", "/", text)
    return text.rstrip("/") or text


def _classify_control_command(
    admission: ForegroundAdmissionInput,
    *,
    owned_paths: list[str],
) -> AdmissionDecision:
    hints = admission.command_hints
    target = _select_active_target(admission.board_snapshot, target_id=hints.target_id)
    command = "resume" if hints.explicit_resume else "pause" if hints.explicit_pause else "cancel"
    if target is None:
        return AdmissionDecision(
            action="ask_clarification",
            relation="unclear",
            reason=f"{command} requested but no unambiguous active run exists",
            question=f"Which task or run should I {command}?",
            owned_paths=owned_paths,
            metadata={"control_command": command},
        )
    return AdmissionDecision(
        action="chat_or_status",
        relation="continuation",
        reason=f"explicit {command} command targets a current run",
        target_task_id=target.task_id,
        target_run_id=target.run_id,
        owned_paths=owned_paths,
        metadata={"control_command": command},
    )


def _select_active_target(
    board: BoardSnapshot,
    *,
    target_id: str = "",
) -> BoardRun | None:
    candidates = board.active_runs + board.queued_runs
    if target_id:
        for run in candidates:
            if target_id in {run.run_id, run.task_id}:
                return run
        return None
    running = [run for run in board.active_runs if run.status == "running"]
    if len(running) == 1:
        return running[0]
    if len(board.active_runs) == 1:
        return board.active_runs[0]
    return None


def _create_new_agent_run(
    store: ChatV2Store,
    turn: SurfaceTurn,
    decision: AdmissionDecision,
    *,
    status: TaskStatus,
    phase: str,
) -> V2TurnAcceptance:
    triage = triage_surface_turn(turn, requested_mode="agent")
    triage = TriageDecisionRecord(
        action="agent_requested",
        reason=decision.reason,
        task_binding="new_task",
        topic_key=triage.topic_key,
        queue_key=f"{triage.queue_key}:async:{decision.action}",
        task_family_hints=triage.task_family_hints,
        metadata={
            **dict(triage.metadata or {}),
            "async_admission": True,
            "admission_action": decision.action,
            "admission_relation": decision.relation,
        },
    )
    context = V2BridgeContext(
        surface_turn=turn,
        triage_decision=triage,
        delegated_to="/api/v2/agent-runs/admit",
    ).model_dump(mode="json")
    acceptance = store.accept_bridge_context(context)
    if acceptance.run_id is None:
        return acceptance
    summary = _admission_summary(decision, run_id=acceptance.run_id)
    event = (
        AgentRunEvent(
            type="waiting_dependency",
            run_id=acceptance.run_id,
            task_id=acceptance.task_id,
            summary=summary,
            source_event_type=f"chat_v2.async_admission.{decision.action}",
            payload={
                "admission_action": decision.action,
                "admission_relation": decision.relation,
                "admission_reason": decision.reason,
                "owned_paths": list(decision.owned_paths),
                "depends_on_task_ids": list(decision.depends_on_task_ids),
                "target_task_id": decision.target_task_id or "",
                "target_run_id": decision.target_run_id or "",
                "capacity_blocked": bool(decision.capacity_blocked),
                **dict(decision.metadata or {}),
            },
        )
        if status == "waiting_dependency"
        else None
    )
    store.annotate_run_for_board(
        acceptance.run_id,
        run_metadata={
            "admission_action": decision.action,
            "admission_relation": decision.relation,
            "admission_reason": decision.reason,
            "owned_paths": list(decision.owned_paths),
            "target_paths": list(decision.owned_paths),
            "depends_on_task_ids": list(decision.depends_on_task_ids),
            "target_task_id": decision.target_task_id or "",
            "target_run_id": decision.target_run_id or "",
            "operator_context": structured_operator_context(
                turn.text,
                attachments=[item.model_dump(mode="json") for item in turn.attachments],
            ),
        },
        task_metadata={
            "admission_action": decision.action,
            "admission_relation": decision.relation,
            "admission_reason": decision.reason,
            "depends_on_task_ids": list(decision.depends_on_task_ids),
            "target_task_id": decision.target_task_id or "",
            "target_run_id": decision.target_run_id or "",
        },
        status=status,
        phase=phase,
        latest_progress=summary,
        event=event,
    )
    refreshed = store.get_task_snapshot(str(acceptance.task_id or ""))
    return acceptance.model_copy(
        update={"snapshot": refreshed, "event": event or acceptance.event}
    )


def _persist_append(
    store: ChatV2Store,
    turn: SurfaceTurn,
    decision: AdmissionDecision,
) -> tuple[AdmissionDecision, AgentRunEvent]:
    payload_text = turn.text
    hints = parse_admission_command(turn.text)
    if hints.payload_text:
        payload_text = hints.payload_text
    command = AgentRunCommand(
        command="append_followup",
        task_id=decision.target_task_id,
        run_id=decision.target_run_id,
        surface_turn_id=turn.id,
        idempotency_key=_stable_id(
            "async-append",
            decision.target_run_id or "",
            turn.id,
            payload_text,
        ),
        payload={
            "text": payload_text,
            "admission_action": decision.action,
            "admission_reason": decision.reason,
            "attachments": [item.model_dump(mode="json") for item in turn.attachments],
        },
    )
    event = store.record_agent_command(command)
    return (
        decision.model_copy(
            update={
                "task_id": event.task_id,
                "run_id": event.run_id,
                "queue_item_id": event.payload.get("queue_item_id"),
                "queue_position": event.payload.get("queue_position"),
                "event": event,
            }
        ),
        event,
    )


def _persist_control_command(
    store: ChatV2Store,
    turn: SurfaceTurn,
    decision: AdmissionDecision,
) -> tuple[AdmissionDecision, AgentRunEvent | None]:
    command_name = str(decision.metadata.get("control_command") or "")
    if command_name not in {"pause", "cancel", "resume"}:
        return decision, None
    command = AgentRunCommand(
        command=command_name,  # type: ignore[arg-type]
        task_id=decision.target_task_id,
        run_id=decision.target_run_id,
        surface_turn_id=turn.id,
        idempotency_key=_stable_id(
            "async-control",
            command_name,
            decision.target_run_id or "",
            turn.id,
        ),
        payload={
            "text": parse_admission_command(turn.text).payload_text,
            "admission_action": "control_command",
        },
    )
    event = store.record_agent_command(command)
    return decision.model_copy(update={"event": event, "status_text": event.summary}), event


def _admission_summary(decision: AdmissionDecision, *, run_id: str) -> str:
    if decision.action == "queue_after":
        suffix = f" {decision.reason}" if decision.reason else ""
        return f"Queued Agent run {run_id} behind active work.{suffix}".strip()
    if decision.action == "start_parallel":
        suffix = f" {decision.reason}" if decision.reason else ""
        return f"Admitted Agent run {run_id} as parallel work.{suffix}".strip()
    return decision.reason or f"Admission decision: {decision.action}."


def _first_target_id(text: str) -> str:
    for token in str(text or "").split():
        clean = token.strip(".,;:()[]{}\"'")
        if clean.startswith("arun-") or clean.startswith("task"):
            return clean
    return ""


def _visible_context_from_turn(turn: SurfaceTurn) -> list[dict[str, Any]]:
    history = turn.metadata.get("history")
    if not isinstance(history, list):
        return []
    compact: list[dict[str, Any]] = []
    for item in history[-6:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        content = " ".join(str(item.get("content") or "").split())
        if not role or not content:
            continue
        compact.append({"role": role, "content": content[:500]})
    return compact


def _short_run_status(run: BoardRun) -> str:
    run_id = run.run_id[:12] if run.run_id else "run"
    objective = " ".join(run.objective.split())[:60]
    if objective:
        return f"{run_id} {run.status} ({objective})"
    return f"{run_id} {run.status}"


def _append_unique(values: list[str], value: str, *, limit: int) -> None:
    text = str(value or "").strip()
    if not text or text in values:
        return
    if len(values) >= limit:
        return
    values.append(text)


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _stable_id(*parts: str) -> str:
    payload = "\n".join(str(part or "") for part in parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
