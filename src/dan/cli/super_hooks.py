"""Internal hook/inbox state for Super DAN live runs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence


QueueFullAction = Literal[
    "coalesce",
    "backpressure",
    "priority_preempt",
    "drop_stale",
    "dead_letter",
]
ParallelMode = Literal["serial", "parallel", "worktree"]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().isoformat().replace("+00:00", "Z")


def _stable_digest(payload: Mapping[str, Any]) -> str:
    blob = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _compact(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in dict(payload).items() if value is not None}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _parse_iso(value: Any) -> datetime | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class SuperQueuePolicy:
    inbox_id: str
    max_pending: int = 4
    max_active_leases: int = 1
    max_wait_ms: int = 500
    queue_full_action: QueueFullAction = "coalesce"
    priority_bands: dict[str, int] = field(
        default_factory=lambda: {
            "normal": 50,
            "validation": 80,
            "repair": 95,
            "immune": 100,
        }
    )


@dataclass
class SuperHookRule:
    rule_id: str
    source_event: str
    inbox_id: str
    packet_type: str
    priority: int
    owner_scope: str = ""
    critical_path: bool = False
    queue_full_action: QueueFullAction | None = None


@dataclass
class SuperPacket:
    packet_id: str
    inbox_id: str
    packet_type: str
    source_event_id: str
    source_event: str
    parent_packet_id: str
    run_id: str
    turn_id: str
    state_version: int
    idempotency_key: str
    priority: int
    created_at: str
    owner_scope: str
    critical_path: bool
    conflict_scope: str
    parallel_mode: ParallelMode
    merge_required: bool
    coalesce_key: str
    payload: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"


@dataclass
class SuperLease:
    lease_id: str
    packet_id: str
    inbox_id: str
    owner_scope: str
    worker_id: str
    acquired_at: str
    expires_at: str
    status: str = "active"
    released_at: str = ""


@dataclass
class SuperOwnerLock:
    lock_id: str
    owner_scope: str
    lease_id: str
    acquired_at: str
    expires_at: str
    status: str = "active"
    released_at: str = ""


@dataclass
class SuperWorktreeTask:
    task_id: str
    packet_id: str
    owner_scope: str
    worktree_path: str
    branch_name: str
    merge_required: bool = True
    status: str = "planned"


@dataclass
class SuperWorktreeDiffPacket:
    diff_id: str
    task_id: str
    packet_id: str
    owner_scope: str
    changed_files: list[str]
    summary: str
    validation_evidence: list[str] = field(default_factory=list)
    candidate_score: float = 0.0
    merge_risk: str = ""
    diff_ref: str = ""
    status: str = "pending_admission"
    admitted_at: str = ""
    rejected_reason: str = ""


@dataclass
class SuperInbox:
    inbox_id: str
    policy: SuperQueuePolicy
    pending_packet_ids: list[str] = field(default_factory=list)
    active_lease_ids: list[str] = field(default_factory=list)
    metrics: dict[str, int] = field(default_factory=dict)


def default_super_queue_policies(profile: str = "balanced") -> dict[str, SuperQueuePolicy]:
    selected = _clean_text(profile).lower() or "balanced"
    if selected not in {"immediate", "balanced", "batch"}:
        selected = "balanced"
    if selected == "immediate":
        return {
            "brain.review": SuperQueuePolicy("brain.review", max_pending=1, max_active_leases=1, max_wait_ms=0, queue_full_action="coalesce"),
            "validation": SuperQueuePolicy("validation", max_pending=1, max_active_leases=1, max_wait_ms=0, queue_full_action="coalesce"),
            "builder.retry": SuperQueuePolicy("builder.retry", max_pending=1, max_active_leases=1, max_wait_ms=0, queue_full_action="priority_preempt"),
            "repair": SuperQueuePolicy("repair", max_pending=1, max_active_leases=1, max_wait_ms=0, queue_full_action="priority_preempt"),
            "immune": SuperQueuePolicy("immune", max_pending=2, max_active_leases=1, max_wait_ms=0, queue_full_action="priority_preempt"),
            "synthesis": SuperQueuePolicy("synthesis", max_pending=1, max_active_leases=1, max_wait_ms=0, queue_full_action="coalesce"),
        }
    if selected == "batch":
        return {
            "brain.review": SuperQueuePolicy("brain.review", max_pending=16, max_active_leases=1, max_wait_ms=2500, queue_full_action="coalesce"),
            "validation": SuperQueuePolicy("validation", max_pending=4, max_active_leases=1, max_wait_ms=1200, queue_full_action="coalesce"),
            "builder.retry": SuperQueuePolicy("builder.retry", max_pending=2, max_active_leases=1, max_wait_ms=500, queue_full_action="priority_preempt"),
            "repair": SuperQueuePolicy("repair", max_pending=2, max_active_leases=1, max_wait_ms=500, queue_full_action="priority_preempt"),
            "immune": SuperQueuePolicy("immune", max_pending=8, max_active_leases=1, max_wait_ms=0, queue_full_action="priority_preempt"),
            "synthesis": SuperQueuePolicy("synthesis", max_pending=4, max_active_leases=1, max_wait_ms=1000, queue_full_action="coalesce"),
        }
    return {
        "brain.review": SuperQueuePolicy("brain.review", max_pending=6, max_active_leases=1, max_wait_ms=600, queue_full_action="coalesce"),
        "validation": SuperQueuePolicy("validation", max_pending=2, max_active_leases=1, max_wait_ms=250, queue_full_action="coalesce"),
        "builder.retry": SuperQueuePolicy("builder.retry", max_pending=1, max_active_leases=1, max_wait_ms=0, queue_full_action="priority_preempt"),
        "repair": SuperQueuePolicy("repair", max_pending=1, max_active_leases=1, max_wait_ms=0, queue_full_action="priority_preempt"),
        "immune": SuperQueuePolicy("immune", max_pending=4, max_active_leases=1, max_wait_ms=0, queue_full_action="priority_preempt"),
        "synthesis": SuperQueuePolicy("synthesis", max_pending=2, max_active_leases=1, max_wait_ms=500, queue_full_action="coalesce"),
    }


def default_super_hook_rules() -> list[SuperHookRule]:
    return [
        SuperHookRule(
            rule_id="material-write-validation",
            source_event="tool.completed",
            inbox_id="validation",
            packet_type="validation_requested",
            priority=80,
            critical_path=True,
        ),
        SuperHookRule(
            rule_id="validation-failure-repair",
            source_event="live.validation.completed",
            inbox_id="repair",
            packet_type="repair_requested",
            priority=95,
            critical_path=True,
        ),
        SuperHookRule(
            rule_id="validation-no-write-builder-retry",
            source_event="live.validation.completed",
            inbox_id="builder.retry",
            packet_type="builder_retry_requested",
            priority=96,
            critical_path=True,
        ),
        SuperHookRule(
            rule_id="validation-pass-synthesis",
            source_event="live.validation.completed",
            inbox_id="synthesis",
            packet_type="synthesis_requested",
            priority=60,
            critical_path=True,
        ),
        SuperHookRule(
            rule_id="repair-validation",
            source_event="live.website_repair.completed",
            inbox_id="validation",
            packet_type="validation_requested",
            priority=85,
            critical_path=True,
        ),
        SuperHookRule(
            rule_id="builder-retry-validation",
            source_event="live.builder_retry.completed",
            inbox_id="validation",
            packet_type="validation_requested",
            priority=86,
            critical_path=True,
        ),
        SuperHookRule(
            rule_id="reader-brain-review",
            source_event="reader.completed",
            inbox_id="brain.review",
            packet_type="brain_review_requested",
            priority=50,
            critical_path=False,
        ),
        SuperHookRule(
            rule_id="scout-brain-review",
            source_event="scout.completed",
            inbox_id="brain.review",
            packet_type="brain_review_requested",
            priority=50,
            critical_path=False,
        ),
        SuperHookRule(
            rule_id="stale-heartbeat-immune",
            source_event="super.heartbeat",
            inbox_id="immune",
            packet_type="immune_check_requested",
            priority=100,
            critical_path=True,
        ),
        SuperHookRule(
            rule_id="model-timeout-immune",
            source_event="model.timeout",
            inbox_id="immune",
            packet_type="immune_check_requested",
            priority=100,
            critical_path=True,
        ),
    ]


class SuperHookRuntime:
    """Replayable internal hook and organ-inbox projection for Super DAN."""

    def __init__(
        self,
        *,
        state_root: Path,
        run_id: str,
        turn_id: str,
        task_id: str,
        trace_id: str,
        reactivity_profile: str = "balanced",
        worktree_parallelism: int = 0,
        auto_ack: bool = True,
        policies: Mapping[str, SuperQueuePolicy] | None = None,
        lease_seconds: int = 120,
    ) -> None:
        self.state_root = state_root.resolve()
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.run_id = _clean_text(run_id)
        self.turn_id = _clean_text(turn_id)
        self.task_id = _clean_text(task_id)
        self.trace_id = _clean_text(trace_id)
        self.reactivity_profile = (
            _clean_text(reactivity_profile).lower()
            if _clean_text(reactivity_profile).lower() in {"immediate", "balanced", "batch"}
            else "balanced"
        )
        self.worktree_parallelism = max(0, int(worktree_parallelism or 0))
        self.auto_ack = bool(auto_ack)
        self.lease_seconds = max(1, int(lease_seconds or 120))
        self.hook_rules = default_super_hook_rules()
        self.policies = dict(policies or default_super_queue_policies(self.reactivity_profile))
        self._pending: dict[str, SuperPacket] = {}
        self._leases: dict[str, SuperLease] = {}
        self._owner_locks: dict[str, SuperOwnerLock] = {}
        self._worktree_tasks: dict[str, SuperWorktreeTask] = {}
        self._worktree_diffs: dict[str, SuperWorktreeDiffPacket] = {}
        self._metrics: dict[str, dict[str, int]] = {
            inbox_id: {
                "enqueued": 0,
                "coalesced": 0,
                "preempted": 0,
                "backpressured": 0,
                "dead_lettered": 0,
                "leased": 0,
                "completed": 0,
                "dropped": 0,
            }
            for inbox_id in self.policies
        }
        self._seen_idempotency_keys = self._load_seen_idempotency_keys()
        self._state_version = self._load_state_version()
        self._runtime_started = False
        self._load_existing_state()
        self._write_static_config()
        self._write_snapshots()

    def process_event(self, row: Mapping[str, Any]) -> list[dict[str, Any]]:
        event_name = _clean_text(row.get("event"))
        if not event_name or self._is_internal_event(event_name):
            return []
        events: list[dict[str, Any]] = []
        if event_name == "run.log.started" and not self._runtime_started:
            self._runtime_started = True
            events.append(
                {
                    "event": "super.hook.runtime.started",
                    "reactivity_profile": self.reactivity_profile,
                    "state_root": str(self.state_root),
                    "worktree_parallelism": self.worktree_parallelism,
                    "inboxes": sorted(self.policies),
                    "operator_visible": True,
                }
            )
            if self.worktree_parallelism > 0:
                events.append(
                    {
                        "event": "super.worktree.policy.configured",
                        "worktree_parallelism": self.worktree_parallelism,
                        "worktree_root": str(self.state_root.parent / "worktrees"),
                        "merge_required": True,
                        "operator_visible": True,
                    }
                )
            return events
        packets = self._packets_for_event(row)
        for packet in packets:
            events.extend(self._enqueue_packet(packet))
        if packets:
            self._write_snapshots()
        return events

    def release_lease(self, lease_id: str, *, status: str = "completed") -> list[dict[str, Any]]:
        lease = self._leases.get(lease_id)
        if lease is None or lease.status != "active":
            return []
        released_at = _utcnow_iso()
        lease.status = _clean_text(status) or "completed"
        lease.released_at = released_at
        lock_events: list[dict[str, Any]] = []
        for lock in self._owner_locks.values():
            if lock.lease_id != lease_id or lock.status != "active":
                continue
            lock.status = "released"
            lock.released_at = released_at
            lock_events.append(
                {
                    "event": "super.owner_lock.released",
                    "lock_id": lock.lock_id,
                    "lease_id": lease_id,
                    "owner_scope": lock.owner_scope,
                    "status": lock.status,
                }
            )
        self._metrics_for(lease.inbox_id)["completed"] += 1
        self._append_jsonl("decisions.jsonl", asdict(lease))
        self._write_snapshots()
        return [
            {
                "event": "super.lease.released",
                "lease_id": lease.lease_id,
                "packet_id": lease.packet_id,
                "inbox_id": lease.inbox_id,
                "owner_scope": lease.owner_scope,
                "status": lease.status,
                "operator_visible": False,
            },
            *lock_events,
        ]

    def plan_worktree_task(
        self,
        packet_id: str,
        *,
        owner_scope: str = "",
        reason: str = "conflicting_owner",
    ) -> tuple[SuperWorktreeTask | None, list[dict[str, Any]]]:
        """Record an isolated worktree task for a packet without mutating main state."""

        if self.worktree_parallelism <= 0:
            return None, [
                {
                    "event": "super.worktree.task_rejected",
                    "packet_id": packet_id,
                    "reason": "worktree parallelism is disabled",
                    "operator_visible": True,
                }
            ]
        active_or_planned = [
            task
            for task in self._worktree_tasks.values()
            if task.status in {"planned", "active"}
        ]
        if len(active_or_planned) >= self.worktree_parallelism:
            return None, [
                {
                    "event": "super.worktree.task_rejected",
                    "packet_id": packet_id,
                    "reason": "worktree parallelism cap reached",
                    "worktree_parallelism": self.worktree_parallelism,
                    "operator_visible": True,
                }
            ]
        packet = self._pending.get(packet_id)
        if packet is None:
            for existing_packet in self._pending.values():
                if existing_packet.packet_id == packet_id:
                    packet = existing_packet
                    break
        resolved_owner = _clean_text(owner_scope) or (
            packet.owner_scope if packet is not None else f"packet:{packet_id}"
        )
        task_id = f"super-worktree:{_stable_digest({'packet_id': packet_id, 'owner_scope': resolved_owner})}"
        existing = self._worktree_tasks.get(task_id)
        if existing is not None:
            return existing, [
                {
                    "event": "super.worktree.task_deduplicated",
                    "task_id": task_id,
                    "packet_id": packet_id,
                    "owner_scope": existing.owner_scope,
                    "operator_visible": False,
                }
            ]
        safe_task_name = task_id.replace(":", "-")
        task = SuperWorktreeTask(
            task_id=task_id,
            packet_id=packet_id,
            owner_scope=resolved_owner,
            worktree_path=str(self.state_root.parent / "worktrees" / safe_task_name),
            branch_name=f"super-dan/{self.run_id or 'run'}/{safe_task_name}",
            merge_required=True,
            status="planned",
        )
        self._worktree_tasks[task_id] = task
        self._append_jsonl(
            "worktree-tasks.jsonl",
            {"reason": reason, **asdict(task)},
        )
        self._write_snapshots()
        return task, [
            {
                "event": "super.worktree.task_planned",
                "task_id": task.task_id,
                "packet_id": task.packet_id,
                "owner_scope": task.owner_scope,
                "worktree_path": task.worktree_path,
                "branch_name": task.branch_name,
                "merge_required": task.merge_required,
                "reason": reason,
                "operator_visible": True,
            }
        ]

    def admit_worktree_diff(
        self,
        diff_packet: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        """Admit or reject a worktree diff packet before it can touch main workspace."""

        task_id = _clean_text(diff_packet.get("task_id"))
        task = self._worktree_tasks.get(task_id)
        changed_files = [
            _clean_text(item)
            for item in list(diff_packet.get("changed_files") or [])
            if _clean_text(item)
        ]
        diff_id = _clean_text(diff_packet.get("diff_id")) or f"super-diff:{_stable_digest(diff_packet)}"
        summary = _clean_text(diff_packet.get("summary"))
        if task is None:
            diff = SuperWorktreeDiffPacket(
                diff_id=diff_id,
                task_id=task_id,
                packet_id=_clean_text(diff_packet.get("packet_id")),
                owner_scope=_clean_text(diff_packet.get("owner_scope")),
                changed_files=changed_files,
                summary=summary,
                status="rejected",
                rejected_reason="unknown worktree task",
            )
            self._worktree_diffs[diff_id] = diff
            self._append_jsonl("worktree-diffs.jsonl", asdict(diff))
            self._write_snapshots()
            return [
                {
                    "event": "super.worktree.diff_rejected",
                    "diff_id": diff.diff_id,
                    "task_id": task_id,
                    "reason": diff.rejected_reason,
                    "operator_visible": True,
                }
            ]
        owner_scope = _clean_text(diff_packet.get("owner_scope")) or task.owner_scope
        validation_evidence = [
            _clean_text(item)
            for item in list(diff_packet.get("validation_evidence") or [])
            if _clean_text(item)
        ]
        diff = SuperWorktreeDiffPacket(
            diff_id=diff_id,
            task_id=task.task_id,
            packet_id=task.packet_id,
            owner_scope=owner_scope,
            changed_files=changed_files,
            summary=summary,
            validation_evidence=validation_evidence,
            candidate_score=float(diff_packet.get("candidate_score") or 0.0),
            merge_risk=_clean_text(diff_packet.get("merge_risk")),
            diff_ref=_clean_text(diff_packet.get("diff_ref")),
        )
        reject_reason = ""
        if not changed_files:
            reject_reason = "diff packet did not report changed files"
        elif owner_scope != task.owner_scope:
            reject_reason = "diff owner scope does not match planned task"
        if reject_reason:
            diff.status = "rejected"
            diff.rejected_reason = reject_reason
            task.status = "rejected"
            event_name = "super.worktree.diff_rejected"
            event_payload = {
                "event": event_name,
                "diff_id": diff.diff_id,
                "task_id": task.task_id,
                "packet_id": task.packet_id,
                "reason": reject_reason,
                "changed_files": list(changed_files),
                "operator_visible": True,
            }
        else:
            diff.status = "admitted"
            diff.admitted_at = _utcnow_iso()
            task.status = "admitted"
            event_payload = {
                "event": "super.worktree.diff_admitted",
                "diff_id": diff.diff_id,
                "task_id": task.task_id,
                "packet_id": task.packet_id,
                "owner_scope": task.owner_scope,
                "changed_files": list(changed_files),
                "candidate_score": diff.candidate_score,
                "merge_required": True,
                "operator_visible": True,
            }
        self._worktree_diffs[diff.diff_id] = diff
        self._append_jsonl("worktree-diffs.jsonl", asdict(diff))
        self._write_snapshots()
        return [event_payload]

    def snapshot(self) -> dict[str, Any]:
        active_leases = {
            lease_id: asdict(lease)
            for lease_id, lease in self._leases.items()
            if lease.status == "active"
        }
        all_leases = {lease_id: asdict(lease) for lease_id, lease in self._leases.items()}
        active_locks = {
            lock_id: asdict(lock)
            for lock_id, lock in self._owner_locks.items()
            if lock.status == "active"
        }
        all_locks = {lock_id: asdict(lock) for lock_id, lock in self._owner_locks.items()}
        inboxes = {}
        for inbox_id, policy in self.policies.items():
            pending = [
                packet_id
                for packet_id, packet in self._pending.items()
                if packet.inbox_id == inbox_id and packet.status == "pending"
            ]
            active = [
                lease_id
                for lease_id, lease in self._leases.items()
                if lease.inbox_id == inbox_id and lease.status == "active"
            ]
            inboxes[inbox_id] = asdict(
                SuperInbox(
                    inbox_id=inbox_id,
                    policy=policy,
                    pending_packet_ids=pending,
                    active_lease_ids=active,
                    metrics=dict(self._metrics_for(inbox_id)),
                )
            )
        return {
            "schema": "super_hook_state_v1",
            "updated_at": _utcnow_iso(),
            "state_root": str(self.state_root),
            "run_id": self.run_id,
            "turn_id": self.turn_id,
            "task_id": self.task_id,
            "trace_id": self.trace_id,
            "reactivity_profile": self.reactivity_profile,
            "worktree_parallelism": self.worktree_parallelism,
            "inboxes": inboxes,
            "leases": {
                "active": active_leases,
                "all": all_leases,
                "total": len(self._leases),
            },
            "owner_locks": {
                "active": active_locks,
                "all": all_locks,
                "total": len(self._owner_locks),
            },
            "worktrees": {
                "tasks": {
                    task_id: asdict(task)
                    for task_id, task in self._worktree_tasks.items()
                },
                "diffs": {
                    diff_id: asdict(diff)
                    for diff_id, diff in self._worktree_diffs.items()
                },
                "total_tasks": len(self._worktree_tasks),
                "total_diffs": len(self._worktree_diffs),
            },
        }

    def _packets_for_event(self, row: Mapping[str, Any]) -> list[SuperPacket]:
        event_name = _clean_text(row.get("event"))
        if event_name == "tool.completed":
            return self._packets_for_tool_completed(row)
        if event_name == "live.validation.completed":
            packet = self._packet_for_validation_completed(row)
            return [packet] if packet is not None else []
        if event_name in {"live.website_repair.completed", "live.generic_repair.completed"}:
            return [
                self._packet_from_rule(
                    self._rule("repair-validation"),
                    row,
                    owner_scope=self._run_owner_scope(row),
                    coalesce_key=self._validation_coalesce_key(row),
                )
            ]
        if event_name == "live.builder_retry.completed":
            changed = row.get("changed_required_files")
            if not (
                isinstance(changed, list)
                and any(_clean_text(item) for item in changed)
            ):
                return []
            return [
                self._packet_from_rule(
                    self._rule("builder-retry-validation"),
                    row,
                    owner_scope=self._run_owner_scope(row),
                    coalesce_key=self._validation_coalesce_key(row),
                )
            ]
        if event_name in {"reader.completed", "scout.completed"}:
            rule_id = "reader-brain-review" if event_name == "reader.completed" else "scout-brain-review"
            return [
                self._packet_from_rule(
                    self._rule(rule_id),
                    row,
                    owner_scope=self._run_owner_scope(row),
                    coalesce_key=f"brain.review:{self.run_id}:{self.turn_id}",
                    critical_path=False,
                    parallel_mode="parallel",
                )
            ]
        if event_name == "super.heartbeat":
            elapsed = int(row.get("elapsed_seconds") or 0)
            if elapsed < 30:
                return []
            return [
                self._packet_from_rule(
                    self._rule("stale-heartbeat-immune"),
                    row,
                    owner_scope=self._run_owner_scope(row),
                    coalesce_key=f"immune:heartbeat:{self.run_id}:{self.turn_id}",
                )
            ]
        if event_name == "model.timeout":
            return [
                self._packet_from_rule(
                    self._rule("model-timeout-immune"),
                    row,
                    owner_scope=self._run_owner_scope(row),
                    coalesce_key=f"immune:model:{self.run_id}:{self.turn_id}",
                )
            ]
        return []

    def _packets_for_tool_completed(self, row: Mapping[str, Any]) -> list[SuperPacket]:
        tool_id = _clean_text(row.get("tool_id"))
        status = _clean_text(row.get("status") or "completed")
        if tool_id not in {"file_write", "file_edit"} or status in {"failed", "denied"}:
            return []
        if self._is_worktree_tool_row(row):
            return []
        return [
            self._packet_from_rule(
                self._rule("material-write-validation"),
                row,
                owner_scope=self._tool_owner_scope(row),
                coalesce_key=self._validation_coalesce_key(row),
            )
        ]

    def _is_worktree_tool_row(self, row: Mapping[str, Any]) -> bool:
        worker_id = _clean_text(row.get("worker_id"))
        if ".worktree." in worker_id:
            return True
        worktree_root = (self.state_root.parent / "worktrees").resolve(strict=False)
        for key in ("workspace_root", "artifact_root"):
            raw_root = _clean_text(row.get(key))
            if not raw_root:
                continue
            try:
                Path(raw_root).expanduser().resolve(strict=False).relative_to(worktree_root)
                return True
            except ValueError:
                pass
        arguments = row.get("arguments") if isinstance(row.get("arguments"), dict) else {}
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        raw_path = _clean_text(
            result.get("path")
            or arguments.get("path")
            or arguments.get("file_path")
            or row.get("path")
        )
        if not raw_path:
            return False
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            return False
        try:
            path.resolve(strict=False).relative_to(worktree_root)
            return True
        except ValueError:
            return False

    def _packet_for_validation_completed(self, row: Mapping[str, Any]) -> SuperPacket | None:
        passed = bool(row.get("passed"))
        if passed:
            rule_id = "validation-pass-synthesis"
        elif self._validation_completed_has_no_required_write(row):
            if bool(row.get("builder_retry_attempted")):
                return None
            rule_id = "validation-no-write-builder-retry"
        elif bool(row.get("repair_exhausted")):
            return None
        else:
            rule_id = "validation-failure-repair"
        rule = self._rule(rule_id)
        return self._packet_from_rule(
            rule,
            row,
            owner_scope=self._run_owner_scope(row),
            coalesce_key=(
                f"synthesis:{self.run_id}:{self.turn_id}"
                if passed
                else (
                    f"builder.retry:{self.run_id}:{self.turn_id}"
                    if rule_id == "validation-no-write-builder-retry"
                    else f"repair:{self.run_id}:{self.turn_id}"
                )
            ),
        )

    @staticmethod
    def _validation_completed_has_no_required_write(row: Mapping[str, Any]) -> bool:
        changed = row.get("changed_required_files")
        changed_files = (
            [_clean_text(item) for item in changed if _clean_text(item)]
            if isinstance(changed, list)
            else []
        )
        failures = row.get("deterministic_failures")
        missing = row.get("missing_requirements")
        candidates: list[str] = []
        if isinstance(failures, list):
            candidates.extend(_clean_text(item).lower() for item in failures)
        if isinstance(missing, list):
            candidates.extend(_clean_text(item).lower() for item in missing)
        for key in ("repair_brief", "comparison_note", "error"):
            value = _clean_text(row.get(key)).lower()
            if value:
                candidates.append(value)
        no_write_markers = (
            "did not change any required website files",
            "no workspace file mutations were observed",
            "without any workspace file mutations",
            "no workspace files were changed",
        )
        return not changed_files and any(
            any(marker in candidate for marker in no_write_markers)
            for candidate in candidates
        )

    def _packet_from_rule(
        self,
        rule: SuperHookRule,
        row: Mapping[str, Any],
        *,
        owner_scope: str,
        coalesce_key: str,
        critical_path: bool | None = None,
        parallel_mode: ParallelMode | None = None,
    ) -> SuperPacket:
        self._state_version += 1
        source_event_id = self._source_event_id(row)
        idempotency_key = _stable_digest(
            {
                "rule_id": rule.rule_id,
                "source_event_id": source_event_id,
                "source_event": row.get("event"),
                "owner_scope": owner_scope,
                "coalesce_key": coalesce_key,
            }
        )
        packet_id = f"super-packet:{_stable_digest({'idempotency_key': idempotency_key})}"
        resolved_critical = rule.critical_path if critical_path is None else bool(critical_path)
        resolved_parallel = parallel_mode or ("serial" if resolved_critical else "parallel")
        merge_required = resolved_parallel == "worktree"
        return SuperPacket(
            packet_id=packet_id,
            inbox_id=rule.inbox_id,
            packet_type=rule.packet_type,
            source_event_id=source_event_id,
            source_event=_clean_text(row.get("event")),
            parent_packet_id=_clean_text(row.get("packet_id") or row.get("handoff_packet_id")),
            run_id=self.run_id or _clean_text(row.get("task_id")),
            turn_id=self.turn_id or _clean_text(row.get("turn_id")),
            state_version=self._state_version,
            idempotency_key=idempotency_key,
            priority=int(rule.priority),
            created_at=_utcnow_iso(),
            owner_scope=owner_scope,
            critical_path=resolved_critical,
            conflict_scope=owner_scope if resolved_critical else "",
            parallel_mode=resolved_parallel,
            merge_required=merge_required,
            coalesce_key=coalesce_key,
            payload=self._bounded_payload(row),
        )

    def _enqueue_packet(self, packet: SuperPacket) -> list[dict[str, Any]]:
        if packet.idempotency_key in self._seen_idempotency_keys:
            return [
                {
                    "event": "super.hook.packet_deduplicated",
                    "packet_id": packet.packet_id,
                    "inbox_id": packet.inbox_id,
                    "source_event": packet.source_event,
                    "idempotency_key": packet.idempotency_key,
                    "operator_visible": False,
                }
            ]
        policy = self.policies.get(packet.inbox_id) or SuperQueuePolicy(packet.inbox_id)
        self.policies.setdefault(packet.inbox_id, policy)
        events: list[dict[str, Any]] = []
        stale_drop_events = self._drop_stale_equivalent_packets(packet)
        events.extend(stale_drop_events)
        pending = self._pending_for(packet.inbox_id)
        if len(pending) >= max(0, int(policy.max_pending)):
            handled, decision_events = self._handle_full_queue(packet, policy, pending)
            events.extend(decision_events)
            if not handled:
                self._write_snapshots()
                return events
        packet.status = "pending"
        self._pending[packet.packet_id] = packet
        self._seen_idempotency_keys.add(packet.idempotency_key)
        self._metrics_for(packet.inbox_id)["enqueued"] += 1
        self._append_jsonl("packets.jsonl", asdict(packet))
        events.append(
            {
                "event": "super.hook.packet_enqueued",
                "packet_id": packet.packet_id,
                "packet_type": packet.packet_type,
                "inbox_id": packet.inbox_id,
                "source_event_id": packet.source_event_id,
                "source_event": packet.source_event,
                "priority": packet.priority,
                "owner_scope": packet.owner_scope,
                "critical_path": packet.critical_path,
                "parallel_mode": packet.parallel_mode,
                "merge_required": packet.merge_required,
                "queue_depth": len(self._pending_for(packet.inbox_id)),
                "reactivity_profile": self.reactivity_profile,
                "operator_visible": True,
            }
        )
        events.extend(self._admit_available(packet.inbox_id))
        return events

    def _drop_stale_equivalent_packets(self, packet: SuperPacket) -> list[dict[str, Any]]:
        if packet.source_event != "super.heartbeat":
            return []
        dropped: list[dict[str, Any]] = []
        for victim in list(self._pending_for(packet.inbox_id)):
            if victim.coalesce_key != packet.coalesce_key:
                continue
            victim.status = "dropped"
            self._pending.pop(victim.packet_id, None)
            self._metrics_for(packet.inbox_id)["dropped"] += 1
            self._append_jsonl(
                "decisions.jsonl",
                {
                    "decision": "drop_stale",
                    "packet_id": victim.packet_id,
                    "replacement_packet_id": packet.packet_id,
                    "inbox_id": packet.inbox_id,
                    "coalesce_key": packet.coalesce_key,
                },
            )
            dropped.append(
                {
                    "event": "super.inbox.packet_dropped",
                    "inbox_id": packet.inbox_id,
                    "dropped_packet_id": victim.packet_id,
                    "replacement_packet_id": packet.packet_id,
                    "coalesce_key": packet.coalesce_key,
                    "queue_full_action": "drop_stale",
                    "operator_visible": True,
                }
            )
        return dropped

    def _handle_full_queue(
        self,
        packet: SuperPacket,
        policy: SuperQueuePolicy,
        pending: Sequence[SuperPacket],
    ) -> tuple[bool, list[dict[str, Any]]]:
        action = policy.queue_full_action
        if action in {"coalesce", "drop_stale"}:
            candidates = [item for item in pending if item.coalesce_key == packet.coalesce_key]
            if candidates:
                victim = sorted(candidates, key=lambda item: item.created_at)[0]
                victim.status = "coalesced" if action == "coalesce" else "dropped"
                self._pending.pop(victim.packet_id, None)
                metric = "coalesced" if action == "coalesce" else "dropped"
                self._metrics_for(packet.inbox_id)[metric] += 1
                event_name = "super.inbox.packet_coalesced" if action == "coalesce" else "super.inbox.packet_dropped"
                return True, [
                    {
                        "event": event_name,
                        "inbox_id": packet.inbox_id,
                        "dropped_packet_id": victim.packet_id,
                        "replacement_packet_id": packet.packet_id,
                        "coalesce_key": packet.coalesce_key,
                        "queue_full_action": action,
                        "operator_visible": True,
                    }
                ]
            if action == "drop_stale":
                return self._dead_letter(packet, reason="queue full and no stale equivalent packet")
        if action == "priority_preempt":
            victim = sorted(pending, key=lambda item: (item.priority, item.created_at))[0]
            if victim.priority < packet.priority:
                victim.status = "preempted"
                self._pending.pop(victim.packet_id, None)
                self._metrics_for(packet.inbox_id)["preempted"] += 1
                return True, [
                    {
                        "event": "super.inbox.packet_preempted",
                        "inbox_id": packet.inbox_id,
                        "dropped_packet_id": victim.packet_id,
                        "replacement_packet_id": packet.packet_id,
                        "dropped_priority": victim.priority,
                        "replacement_priority": packet.priority,
                        "operator_visible": True,
                    }
                ]
            return self._dead_letter(packet, reason="queue full and existing packet has equal or higher priority")
        if action == "backpressure":
            packet.status = "backpressured"
            self._metrics_for(packet.inbox_id)["backpressured"] += 1
            self._append_jsonl("dead-letters.jsonl", {**asdict(packet), "reason": "queue backpressure"})
            return False, [
                {
                    "event": "super.inbox.packet_backpressured",
                    "inbox_id": packet.inbox_id,
                    "packet_id": packet.packet_id,
                    "queue_full_action": action,
                    "reason": "queue is full",
                    "operator_visible": True,
                }
            ]
        return self._dead_letter(packet, reason="queue full")

    def _dead_letter(self, packet: SuperPacket, *, reason: str) -> tuple[bool, list[dict[str, Any]]]:
        packet.status = "dead_lettered"
        self._metrics_for(packet.inbox_id)["dead_lettered"] += 1
        self._append_jsonl("dead-letters.jsonl", {**asdict(packet), "reason": reason})
        return False, [
            {
                "event": "super.inbox.packet_dead_lettered",
                "inbox_id": packet.inbox_id,
                "packet_id": packet.packet_id,
                "reason": reason,
                "operator_visible": True,
            }
        ]

    def _admit_available(self, inbox_id: str) -> list[dict[str, Any]]:
        policy = self.policies.get(inbox_id) or SuperQueuePolicy(inbox_id)
        events: list[dict[str, Any]] = []
        while len(self._active_leases_for(inbox_id)) < max(0, int(policy.max_active_leases)):
            candidates = sorted(
                self._pending_for(inbox_id),
                key=lambda item: (-item.priority, item.created_at),
            )
            if not candidates:
                break
            packet = candidates[0]
            if self._owner_locked(packet.owner_scope):
                events.append(
                    {
                        "event": "super.inbox.packet_waiting_on_owner_lock",
                        "packet_id": packet.packet_id,
                        "inbox_id": packet.inbox_id,
                        "owner_scope": packet.owner_scope,
                        "operator_visible": True,
                    }
                )
                break
            events.extend(self._acquire_lease(packet))
        return events

    def _acquire_lease(self, packet: SuperPacket) -> list[dict[str, Any]]:
        packet.status = "leased"
        self._pending.pop(packet.packet_id, None)
        now = _utcnow()
        acquired_at = now.isoformat().replace("+00:00", "Z")
        expires_at = (now + timedelta(seconds=self.lease_seconds)).isoformat().replace("+00:00", "Z")
        lease_id = f"super-lease:{_stable_digest({'packet_id': packet.packet_id, 'version': packet.state_version})}"
        lease = SuperLease(
            lease_id=lease_id,
            packet_id=packet.packet_id,
            inbox_id=packet.inbox_id,
            owner_scope=packet.owner_scope,
            worker_id=self._worker_for_inbox(packet.inbox_id),
            acquired_at=acquired_at,
            expires_at=expires_at,
        )
        self._leases[lease_id] = lease
        self._metrics_for(packet.inbox_id)["leased"] += 1
        self._append_jsonl("decisions.jsonl", {"decision": "lease_acquired", **asdict(lease)})
        events = [
            {
                "event": "super.lease.acquired",
                "lease_id": lease.lease_id,
                "packet_id": packet.packet_id,
                "inbox_id": packet.inbox_id,
                "owner_scope": packet.owner_scope,
                "worker_id": lease.worker_id,
                "expires_at": expires_at,
                "operator_visible": False,
            }
        ]
        if packet.owner_scope:
            lock = SuperOwnerLock(
                lock_id=f"super-lock:{_stable_digest({'lease_id': lease_id, 'owner_scope': packet.owner_scope})}",
                owner_scope=packet.owner_scope,
                lease_id=lease_id,
                acquired_at=acquired_at,
                expires_at=expires_at,
            )
            self._owner_locks[lock.lock_id] = lock
            events.append(
                {
                    "event": "super.owner_lock.acquired",
                    "lock_id": lock.lock_id,
                    "lease_id": lease_id,
                    "owner_scope": lock.owner_scope,
                    "expires_at": expires_at,
                    "operator_visible": False,
                }
            )
        if self.auto_ack:
            events.extend(self.release_lease(lease_id, status="acknowledged"))
        return events

    def _rule(self, rule_id: str) -> SuperHookRule:
        for rule in self.hook_rules:
            if rule.rule_id == rule_id:
                return rule
        raise KeyError(rule_id)

    def _metrics_for(self, inbox_id: str) -> dict[str, int]:
        return self._metrics.setdefault(
            inbox_id,
            {
                "enqueued": 0,
                "coalesced": 0,
                "preempted": 0,
                "backpressured": 0,
                "dead_lettered": 0,
                "leased": 0,
                "completed": 0,
                "dropped": 0,
            },
        )

    def _pending_for(self, inbox_id: str) -> list[SuperPacket]:
        return [
            packet
            for packet in self._pending.values()
            if packet.inbox_id == inbox_id and packet.status == "pending"
        ]

    def _active_leases_for(self, inbox_id: str) -> list[SuperLease]:
        self._expire_stale_leases()
        return [
            lease
            for lease in self._leases.values()
            if lease.inbox_id == inbox_id and lease.status == "active"
        ]

    def _owner_locked(self, owner_scope: str) -> bool:
        self._expire_stale_leases()
        if not owner_scope:
            return False
        return any(
            lock.status == "active" and lock.owner_scope == owner_scope
            for lock in self._owner_locks.values()
        )

    def _expire_stale_leases(self) -> None:
        now = _utcnow()
        changed = False
        for lease in self._leases.values():
            if lease.status != "active":
                continue
            expires = _parse_iso(lease.expires_at)
            if expires is not None and expires <= now:
                lease.status = "expired"
                lease.released_at = _utcnow_iso()
                changed = True
        for lock in self._owner_locks.values():
            if lock.status != "active":
                continue
            lease = self._leases.get(lock.lease_id)
            if lease is not None and lease.status != "active":
                lock.status = "released"
                lock.released_at = lease.released_at
                changed = True
        if changed:
            self._write_snapshots()

    @staticmethod
    def _is_internal_event(event_name: str) -> bool:
        return event_name.startswith(
            (
                "super.hook.",
                "super.inbox.",
                "super.lease.",
                "super.owner_lock.",
                "super.worktree.",
            )
        )

    def _source_event_id(self, row: Mapping[str, Any]) -> str:
        sequence = row.get("sequence")
        if sequence is not None and _clean_text(sequence):
            return f"event:{sequence}"
        return f"event:{_stable_digest(dict(row))}"

    def _run_owner_scope(self, row: Mapping[str, Any]) -> str:
        workspace = _clean_text(row.get("workspace_root") or row.get("artifact_root"))
        if workspace:
            return f"workspace:{workspace}"
        task = _clean_text(row.get("task_id") or self.task_id or self.run_id)
        return f"run:{task or 'active'}"

    def _tool_owner_scope(self, row: Mapping[str, Any]) -> str:
        arguments = row.get("arguments") if isinstance(row.get("arguments"), dict) else {}
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        path = _clean_text(
            result.get("path")
            or arguments.get("path")
            or arguments.get("file_path")
            or row.get("path")
        )
        if path:
            return f"file:{path}"
        return self._run_owner_scope(row)

    def _validation_coalesce_key(self, row: Mapping[str, Any]) -> str:
        return f"validation:{self.run_id or row.get('task_id') or 'run'}:{self.turn_id or row.get('turn_id') or 'turn'}"

    def _bounded_payload(self, row: Mapping[str, Any]) -> dict[str, Any]:
        keys = (
            "event",
            "sequence",
            "timestamp",
            "worker_id",
            "tool_id",
            "status",
            "model",
            "round",
            "passed",
            "overall_score",
            "attempt",
            "error",
            "elapsed_seconds",
            "finish_reason",
            "builder_retry_attempted",
        )
        payload = {key: row.get(key) for key in keys if row.get(key) is not None}
        changed = row.get("changed_required_files")
        if isinstance(changed, list):
            payload["changed_required_files"] = [
                str(item)[:240] for item in changed[:12] if str(item).strip()
            ]
        arguments = row.get("arguments") if isinstance(row.get("arguments"), dict) else {}
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        path = _clean_text(
            result.get("path")
            or arguments.get("path")
            or arguments.get("file_path")
            or row.get("path")
        )
        if path:
            payload["path"] = path
        missing = row.get("missing_requirements")
        if isinstance(missing, list):
            payload["missing_requirements"] = [str(item)[:240] for item in missing[:8]]
        failures = row.get("deterministic_failures")
        if isinstance(failures, list):
            payload["deterministic_failures"] = [str(item)[:240] for item in failures[:8]]
        return _compact(payload)

    @staticmethod
    def _worker_for_inbox(inbox_id: str) -> str:
        if inbox_id == "brain.review":
            return "super-dan.brain"
        if inbox_id == "validation":
            return "super-dan.validator"
        if inbox_id == "builder.retry":
            return "super-dan.builder.retry"
        if inbox_id == "repair":
            return "super-dan.immune.repair"
        if inbox_id == "immune":
            return "super-dan.immune"
        if inbox_id == "synthesis":
            return "super-dan.synthesis"
        return f"super-dan.{inbox_id}"

    def _load_existing_state(self) -> None:
        state_path = self.state_root / "inboxes.json"
        if not state_path.exists():
            return
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(state, dict):
            return
        packet_records = self._load_packet_records()
        inboxes = state.get("inboxes") if isinstance(state.get("inboxes"), dict) else {}
        for inbox_id, inbox in inboxes.items():
            if not isinstance(inbox, dict):
                continue
            policy = self._queue_policy_from_payload(inbox.get("policy"), fallback_id=str(inbox_id))
            if policy is not None and inbox_id not in self.policies:
                self.policies[str(inbox_id)] = policy
            metrics = inbox.get("metrics") if isinstance(inbox.get("metrics"), dict) else {}
            if metrics:
                current = self._metrics_for(str(inbox_id))
                for key, value in metrics.items():
                    try:
                        current[str(key)] = int(value)
                    except (TypeError, ValueError):
                        continue
            for packet_id in list(inbox.get("pending_packet_ids") or []):
                packet = packet_records.get(str(packet_id))
                if packet is None:
                    continue
                packet.status = "pending"
                self._pending[packet.packet_id] = packet
        leases = state.get("leases") if isinstance(state.get("leases"), dict) else {}
        lease_payloads = leases.get("all") or leases.get("active") or {}
        if isinstance(lease_payloads, dict):
            for lease_id, payload in lease_payloads.items():
                lease = self._lease_from_payload(payload, fallback_id=str(lease_id))
                if lease is not None:
                    self._leases[lease.lease_id] = lease
        locks = state.get("owner_locks") if isinstance(state.get("owner_locks"), dict) else {}
        lock_payloads = locks.get("all") or locks.get("active") or {}
        if isinstance(lock_payloads, dict):
            for lock_id, payload in lock_payloads.items():
                lock = self._owner_lock_from_payload(payload, fallback_id=str(lock_id))
                if lock is not None:
                    self._owner_locks[lock.lock_id] = lock
        worktrees = state.get("worktrees") if isinstance(state.get("worktrees"), dict) else {}
        task_payloads = worktrees.get("tasks") if isinstance(worktrees.get("tasks"), dict) else {}
        for task_id, payload in task_payloads.items():
            task = self._worktree_task_from_payload(payload, fallback_id=str(task_id))
            if task is not None:
                self._worktree_tasks[task.task_id] = task
        diff_payloads = worktrees.get("diffs") if isinstance(worktrees.get("diffs"), dict) else {}
        for diff_id, payload in diff_payloads.items():
            diff = self._worktree_diff_from_payload(payload, fallback_id=str(diff_id))
            if diff is not None:
                self._worktree_diffs[diff.diff_id] = diff

    def _load_packet_records(self) -> dict[str, SuperPacket]:
        path = self.state_root / "packets.jsonl"
        packets: dict[str, SuperPacket] = {}
        if not path.exists():
            return packets
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return packets
        for line in lines:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            packet = self._packet_from_payload(payload)
            if packet is not None:
                packets[packet.packet_id] = packet
        return packets

    @staticmethod
    def _queue_policy_from_payload(payload: Any, *, fallback_id: str) -> SuperQueuePolicy | None:
        if not isinstance(payload, dict):
            return None
        return SuperQueuePolicy(
            inbox_id=_clean_text(payload.get("inbox_id")) or fallback_id,
            max_pending=int(payload.get("max_pending") or 0),
            max_active_leases=int(payload.get("max_active_leases") or 0),
            max_wait_ms=int(payload.get("max_wait_ms") or 0),
            queue_full_action=payload.get("queue_full_action") or "coalesce",
            priority_bands=(
                dict(payload.get("priority_bands"))
                if isinstance(payload.get("priority_bands"), dict)
                else {}
            ),
        )

    @staticmethod
    def _packet_from_payload(payload: Any) -> SuperPacket | None:
        if not isinstance(payload, dict):
            return None
        required = ["packet_id", "inbox_id", "packet_type", "source_event", "idempotency_key"]
        if any(not _clean_text(payload.get(key)) for key in required):
            return None
        try:
            return SuperPacket(
                packet_id=_clean_text(payload.get("packet_id")),
                inbox_id=_clean_text(payload.get("inbox_id")),
                packet_type=_clean_text(payload.get("packet_type")),
                source_event_id=_clean_text(payload.get("source_event_id")),
                source_event=_clean_text(payload.get("source_event")),
                parent_packet_id=_clean_text(payload.get("parent_packet_id")),
                run_id=_clean_text(payload.get("run_id")),
                turn_id=_clean_text(payload.get("turn_id")),
                state_version=int(payload.get("state_version") or 0),
                idempotency_key=_clean_text(payload.get("idempotency_key")),
                priority=int(payload.get("priority") or 0),
                created_at=_clean_text(payload.get("created_at")),
                owner_scope=_clean_text(payload.get("owner_scope")),
                critical_path=bool(payload.get("critical_path")),
                conflict_scope=_clean_text(payload.get("conflict_scope")),
                parallel_mode=payload.get("parallel_mode") or "serial",
                merge_required=bool(payload.get("merge_required")),
                coalesce_key=_clean_text(payload.get("coalesce_key")),
                payload=dict(payload.get("payload") or {}),
                status=_clean_text(payload.get("status")) or "pending",
            )
        except Exception:
            return None

    @staticmethod
    def _lease_from_payload(payload: Any, *, fallback_id: str) -> SuperLease | None:
        if not isinstance(payload, dict):
            return None
        lease_id = _clean_text(payload.get("lease_id")) or fallback_id
        if not lease_id:
            return None
        return SuperLease(
            lease_id=lease_id,
            packet_id=_clean_text(payload.get("packet_id")),
            inbox_id=_clean_text(payload.get("inbox_id")),
            owner_scope=_clean_text(payload.get("owner_scope")),
            worker_id=_clean_text(payload.get("worker_id")),
            acquired_at=_clean_text(payload.get("acquired_at")),
            expires_at=_clean_text(payload.get("expires_at")),
            status=_clean_text(payload.get("status")) or "active",
            released_at=_clean_text(payload.get("released_at")),
        )

    @staticmethod
    def _owner_lock_from_payload(payload: Any, *, fallback_id: str) -> SuperOwnerLock | None:
        if not isinstance(payload, dict):
            return None
        lock_id = _clean_text(payload.get("lock_id")) or fallback_id
        if not lock_id:
            return None
        return SuperOwnerLock(
            lock_id=lock_id,
            owner_scope=_clean_text(payload.get("owner_scope")),
            lease_id=_clean_text(payload.get("lease_id")),
            acquired_at=_clean_text(payload.get("acquired_at")),
            expires_at=_clean_text(payload.get("expires_at")),
            status=_clean_text(payload.get("status")) or "active",
            released_at=_clean_text(payload.get("released_at")),
        )

    @staticmethod
    def _worktree_task_from_payload(payload: Any, *, fallback_id: str) -> SuperWorktreeTask | None:
        if not isinstance(payload, dict):
            return None
        task_id = _clean_text(payload.get("task_id")) or fallback_id
        if not task_id:
            return None
        return SuperWorktreeTask(
            task_id=task_id,
            packet_id=_clean_text(payload.get("packet_id")),
            owner_scope=_clean_text(payload.get("owner_scope")),
            worktree_path=_clean_text(payload.get("worktree_path")),
            branch_name=_clean_text(payload.get("branch_name")),
            merge_required=bool(payload.get("merge_required", True)),
            status=_clean_text(payload.get("status")) or "planned",
        )

    @staticmethod
    def _worktree_diff_from_payload(payload: Any, *, fallback_id: str) -> SuperWorktreeDiffPacket | None:
        if not isinstance(payload, dict):
            return None
        diff_id = _clean_text(payload.get("diff_id")) or fallback_id
        if not diff_id:
            return None
        return SuperWorktreeDiffPacket(
            diff_id=diff_id,
            task_id=_clean_text(payload.get("task_id")),
            packet_id=_clean_text(payload.get("packet_id")),
            owner_scope=_clean_text(payload.get("owner_scope")),
            changed_files=[
                _clean_text(item)
                for item in list(payload.get("changed_files") or [])
                if _clean_text(item)
            ],
            summary=_clean_text(payload.get("summary")),
            validation_evidence=[
                _clean_text(item)
                for item in list(payload.get("validation_evidence") or [])
                if _clean_text(item)
            ],
            candidate_score=float(payload.get("candidate_score") or 0.0),
            merge_risk=_clean_text(payload.get("merge_risk")),
            diff_ref=_clean_text(payload.get("diff_ref")),
            status=_clean_text(payload.get("status")) or "pending_admission",
            admitted_at=_clean_text(payload.get("admitted_at")),
            rejected_reason=_clean_text(payload.get("rejected_reason")),
        )

    def _write_static_config(self) -> None:
        self._write_json(
            "hook-rules.json",
            {
                "schema": "super_hook_rules_v1",
                "updated_at": _utcnow_iso(),
                "rules": [asdict(rule) for rule in self.hook_rules],
                "policies": {key: asdict(value) for key, value in self.policies.items()},
                "reactivity_profile": self.reactivity_profile,
            },
        )
        self._write_json(
            "worktree-policy.json",
            {
                "schema": "super_worktree_policy_v1",
                "updated_at": _utcnow_iso(),
                "worktree_parallelism": self.worktree_parallelism,
                "worktree_root": str(self.state_root.parent / "worktrees"),
                "merge_required": True,
                "critical_path_single_writer": True,
            },
        )

    def _write_snapshots(self) -> None:
        snapshot = self.snapshot()
        self._write_json("inboxes.json", snapshot)
        self._write_json("leases.json", snapshot["leases"])
        self._write_json("owner-locks.json", snapshot["owner_locks"])

    def _write_json(self, filename: str, payload: Mapping[str, Any]) -> None:
        path = self.state_root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(dict(payload), indent=2, ensure_ascii=False, sort_keys=True, default=str)
            + "\n",
            encoding="utf-8",
        )

    def _append_jsonl(self, filename: str, payload: Mapping[str, Any]) -> None:
        path = self.state_root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, default=str))
            handle.write("\n")

    def _load_seen_idempotency_keys(self) -> set[str]:
        path = self.state_root / "packets.jsonl"
        seen: set[str] = set()
        if not path.exists():
            return seen
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                payload = json.loads(line)
                key = _clean_text(payload.get("idempotency_key"))
                if key:
                    seen.add(key)
        except Exception:
            return seen
        return seen

    def _load_state_version(self) -> int:
        path = self.state_root / "packets.jsonl"
        highest = 0
        if not path.exists():
            return highest
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                payload = json.loads(line)
                highest = max(highest, int(payload.get("state_version") or 0))
        except Exception:
            return highest
        return highest


def read_super_hook_state(workspace_root: Path) -> dict[str, Any] | None:
    path = workspace_root.resolve() / ".dan-super" / "state" / "inboxes.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def replay_super_hook_events(
    event_log_path: Path,
    *,
    state_root: Path | None = None,
    reactivity_profile: str = "balanced",
    worktree_parallelism: int = 0,
) -> dict[str, Any]:
    """Rebuild Super DAN hook state from one append-only event log."""

    path = event_log_path.resolve()
    rows: list[dict[str, Any]] = []
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return {
            "status": "failed",
            "error": f"event log not found: {path}",
            "event_log_path": str(path),
        }
    for line in raw_lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    first = next((row for row in rows if not SuperHookRuntime._is_internal_event(_clean_text(row.get("event")))), {})
    resolved_state_root = state_root or _state_root_for_event_log(path)
    task_id = _clean_text(first.get("task_id")) or "super-dan-live:replay"
    runtime = SuperHookRuntime(
        state_root=resolved_state_root,
        run_id=task_id,
        turn_id=_clean_text(first.get("turn_id")) or "replay",
        task_id=task_id,
        trace_id=_clean_text(first.get("trace_id")) or f"trace:replay:{_stable_digest({'path': str(path)})}",
        reactivity_profile=reactivity_profile,
        worktree_parallelism=worktree_parallelism,
    )
    emitted: list[dict[str, Any]] = []
    for row in rows:
        emitted.extend(runtime.process_event(row))
    return {
        "status": "completed",
        "event_log_path": str(path),
        "state_root": str(resolved_state_root.resolve()),
        "source_events": len(rows),
        "emitted_events": emitted,
        "snapshot": runtime.snapshot(),
    }


def _state_root_for_event_log(path: Path) -> Path:
    parent = path.parent
    if parent.name.startswith("turn-") and parent.parent.name == "runs":
        return parent.parent.parent / "state"
    return parent / "state"


def format_super_queue_status(workspace_root: Path) -> str:
    root = workspace_root.resolve()
    state = read_super_hook_state(root)
    state_root = root / ".dan-super" / "state"
    if not state:
        return f"Super DAN queues\nstate: {state_root}\nstatus: no hook state found"
    lines = [
        "Super DAN queues",
        f"state: {state.get('state_root') or state_root}",
        f"reactivity: {state.get('reactivity_profile') or 'balanced'}",
        f"worktree parallelism: {int(state.get('worktree_parallelism') or 0)}",
        "Inboxes:",
    ]
    inboxes = state.get("inboxes") if isinstance(state.get("inboxes"), dict) else {}
    for inbox_id in sorted(inboxes):
        inbox = inboxes.get(inbox_id) if isinstance(inboxes.get(inbox_id), dict) else {}
        pending = len(list(inbox.get("pending_packet_ids") or []))
        active = len(list(inbox.get("active_lease_ids") or []))
        metrics = inbox.get("metrics") if isinstance(inbox.get("metrics"), dict) else {}
        lines.append(
            "- "
            + f"{inbox_id}: pending={pending} active={active} "
            + f"enqueued={int(metrics.get('enqueued') or 0)} "
            + f"leased={int(metrics.get('leased') or 0)} "
            + f"coalesced={int(metrics.get('coalesced') or 0)} "
            + f"backpressured={int(metrics.get('backpressured') or 0)} "
            + f"dead={int(metrics.get('dead_lettered') or 0)}"
        )
    leases = state.get("leases") if isinstance(state.get("leases"), dict) else {}
    locks = state.get("owner_locks") if isinstance(state.get("owner_locks"), dict) else {}
    lines.append(
        f"Leases: active={len(dict(leases.get('active') or {}))} total={int(leases.get('total') or 0)}"
    )
    lines.append(
        f"Owner Locks: active={len(dict(locks.get('active') or {}))} total={int(locks.get('total') or 0)}"
    )
    worktrees = state.get("worktrees") if isinstance(state.get("worktrees"), dict) else {}
    if worktrees:
        lines.append(
            "Worktrees: "
            + f"tasks={int(worktrees.get('total_tasks') or 0)} "
            + f"diffs={int(worktrees.get('total_diffs') or 0)}"
        )
    return "\n".join(lines)
