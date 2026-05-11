from __future__ import annotations

import json
from pathlib import Path

from dan.cli.super_hooks import (
    SuperHookRuntime,
    SuperQueuePolicy,
    format_super_queue_status,
    replay_super_hook_events,
)


def _write_event(sequence: int, path: str = "index.html") -> dict[str, object]:
    return {
        "event": "tool.completed",
        "sequence": sequence,
        "task_id": "super-dan-live:1",
        "turn_id": "1",
        "tool_id": "file_write",
        "status": "completed",
        "arguments": {"path": path},
        "result": {"path": path},
    }


def _heartbeat_event(sequence: int, elapsed_seconds: int) -> dict[str, object]:
    return {
        "event": "super.heartbeat",
        "sequence": sequence,
        "task_id": "super-dan-live:1",
        "turn_id": "1",
        "elapsed_seconds": elapsed_seconds,
        "phase": "model",
    }


def _validation_event(
    sequence: int,
    *,
    passed: bool = False,
    changed_required_files: list[str] | None = None,
    deterministic_failures: list[str] | None = None,
    missing_requirements: list[str] | None = None,
    builder_retry_attempted: bool = False,
    repair_attempted: bool = False,
    repair_exhausted: bool = False,
) -> dict[str, object]:
    return {
        "event": "live.validation.completed",
        "sequence": sequence,
        "task_id": "super-dan-live:1",
        "turn_id": "1",
        "passed": passed,
        "overall_score": 0.0 if not passed else 0.9,
        "changed_required_files": list(changed_required_files or []),
        "deterministic_failures": list(deterministic_failures or []),
        "missing_requirements": list(missing_requirements or []),
        "builder_retry_attempted": builder_retry_attempted,
        "repair_attempted": repair_attempted,
        "repair_exhausted": repair_exhausted,
    }


def test_super_hook_runtime_routes_material_write_to_validation_and_persists_state(
    tmp_path: Path,
) -> None:
    runtime = SuperHookRuntime(
        state_root=tmp_path / ".dan-super" / "state",
        run_id="super-dan-live:1",
        turn_id="1",
        task_id="super-dan-live:1",
        trace_id="trace:test",
        reactivity_profile="balanced",
    )

    events = runtime.process_event(_write_event(3, "website/index.html"))

    assert any(event["event"] == "super.hook.packet_enqueued" for event in events)
    assert any(event["event"] == "super.lease.acquired" for event in events)
    assert any(event["event"] == "super.owner_lock.acquired" for event in events)
    assert any(event["event"] == "super.lease.released" for event in events)
    packets_path = tmp_path / ".dan-super" / "state" / "packets.jsonl"
    packets = [
        json.loads(line)
        for line in packets_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(packets) == 1
    packet = packets[0]
    assert packet["inbox_id"] == "validation"
    assert packet["packet_type"] == "validation_requested"
    assert packet["source_event_id"] == "event:3"
    assert packet["critical_path"] is True
    assert packet["owner_scope"] == "file:website/index.html"
    assert packet["coalesce_key"] == "validation:super-dan-live:1:1"
    state = json.loads(
        (tmp_path / ".dan-super" / "state" / "inboxes.json").read_text(encoding="utf-8")
    )
    validation = state["inboxes"]["validation"]
    assert validation["pending_packet_ids"] == []
    assert validation["active_lease_ids"] == []
    assert validation["metrics"]["enqueued"] == 1
    assert validation["metrics"]["leased"] == 1
    assert validation["metrics"]["completed"] == 1


def test_super_hook_runtime_routes_generic_repair_completion_to_validation(
    tmp_path: Path,
) -> None:
    runtime = SuperHookRuntime(
        state_root=tmp_path / ".dan-super" / "state",
        run_id="super-dan-live:1",
        turn_id="1",
        task_id="super-dan-live:1",
        trace_id="trace:test",
        reactivity_profile="balanced",
    )

    events = runtime.process_event(
        {
            "event": "live.generic_repair.completed",
            "sequence": 4,
            "task_id": "super-dan-live:1",
            "turn_id": "1",
            "changed_required_files": ["/tmp/animation/index.html"],
        }
    )

    assert any(
        event["event"] == "super.hook.packet_enqueued"
        and event["inbox_id"] == "validation"
        and event["packet_type"] == "validation_requested"
        for event in events
    )


def test_super_hook_runtime_routes_no_write_validation_to_builder_retry(
    tmp_path: Path,
) -> None:
    runtime = SuperHookRuntime(
        state_root=tmp_path / ".dan-super" / "state",
        run_id="super-dan-live:1",
        turn_id="1",
        task_id="super-dan-live:1",
        trace_id="trace:test",
        reactivity_profile="balanced",
    )

    events = runtime.process_event(
        _validation_event(
            4,
            deterministic_failures=[
                "The live run did not change any required website files."
            ],
        )
    )

    assert any(
        event["event"] == "super.hook.packet_enqueued"
        and event["inbox_id"] == "builder.retry"
        and event["packet_type"] == "builder_retry_requested"
        for event in events
    )
    assert not any(
        event.get("inbox_id") == "repair"
        and event.get("packet_type") == "repair_requested"
        for event in events
    )


def test_super_hook_runtime_routes_generic_no_mutation_validation_to_builder_retry(
    tmp_path: Path,
) -> None:
    runtime = SuperHookRuntime(
        state_root=tmp_path / ".dan-super" / "state",
        run_id="super-dan-live:1",
        turn_id="1",
        task_id="super-dan-live:1",
        trace_id="trace:test",
        reactivity_profile="balanced",
    )

    events = runtime.process_event(
        _validation_event(
            4,
            missing_requirements=["No workspace file mutations were observed."],
        )
    )

    assert any(
        event["event"] == "super.hook.packet_enqueued"
        and event["inbox_id"] == "builder.retry"
        and event["packet_type"] == "builder_retry_requested"
        for event in events
    )
    assert not any(event.get("inbox_id") == "repair" for event in events)


def test_super_hook_runtime_routes_generic_builder_retry_completion_to_validation(
    tmp_path: Path,
) -> None:
    runtime = SuperHookRuntime(
        state_root=tmp_path / ".dan-super" / "state",
        run_id="super-dan-live:1",
        turn_id="1",
        task_id="super-dan-live:1",
        trace_id="trace:test",
        reactivity_profile="balanced",
    )

    events = runtime.process_event(
        {
            "event": "live.builder_retry.completed",
            "sequence": 4,
            "task_id": "super-dan-live:1",
            "turn_id": "1",
            "changed_required_files": ["/tmp/report.md"],
        }
    )

    assert any(
        event["event"] == "super.hook.packet_enqueued"
        and event["inbox_id"] == "validation"
        and event["packet_type"] == "validation_requested"
        for event in events
    )


def test_super_hook_runtime_ignores_empty_generic_builder_retry_completion(
    tmp_path: Path,
) -> None:
    runtime = SuperHookRuntime(
        state_root=tmp_path / ".dan-super" / "state",
        run_id="super-dan-live:1",
        turn_id="1",
        task_id="super-dan-live:1",
        trace_id="trace:test",
        reactivity_profile="balanced",
    )

    events = runtime.process_event(
        {
            "event": "live.builder_retry.completed",
            "sequence": 4,
            "task_id": "super-dan-live:1",
            "turn_id": "1",
            "changed_required_files": [],
        }
    )

    assert events == []


def test_super_hook_runtime_routes_changed_failed_validation_to_repair(
    tmp_path: Path,
) -> None:
    runtime = SuperHookRuntime(
        state_root=tmp_path / ".dan-super" / "state",
        run_id="super-dan-live:1",
        turn_id="1",
        task_id="super-dan-live:1",
        trace_id="trace:test",
        reactivity_profile="balanced",
    )

    events = runtime.process_event(
        _validation_event(
            5,
            changed_required_files=["/tmp/site/index.html"],
            deterministic_failures=["The changed website still echoes the raw prompt."],
        )
    )

    assert any(
        event["event"] == "super.hook.packet_enqueued"
        and event["inbox_id"] == "repair"
        and event["packet_type"] == "repair_requested"
        for event in events
    )
    assert not any(event.get("inbox_id") == "builder.retry" for event in events)


def test_super_hook_runtime_routes_quality_failure_without_changed_required_files_to_repair(
    tmp_path: Path,
) -> None:
    runtime = SuperHookRuntime(
        state_root=tmp_path / ".dan-super" / "state",
        run_id="super-dan-live:1",
        turn_id="1",
        task_id="super-dan-live:1",
        trace_id="trace:test",
        reactivity_profile="balanced",
    )

    events = runtime.process_event(
        _validation_event(
            6,
            deterministic_failures=[
                "The smoke is still chunky and needs smaller particles, turbulence, and lighter blending."
            ],
        )
    )

    assert any(
        event["event"] == "super.hook.packet_enqueued"
        and event["inbox_id"] == "repair"
        and event["packet_type"] == "repair_requested"
        for event in events
    )
    assert not any(event.get("inbox_id") == "builder.retry" for event in events)


def test_super_hook_runtime_suppresses_no_write_recovery_after_attempt_exhausted(
    tmp_path: Path,
) -> None:
    runtime = SuperHookRuntime(
        state_root=tmp_path / ".dan-super" / "state",
        run_id="super-dan-live:1",
        turn_id="1",
        task_id="super-dan-live:1",
        trace_id="trace:test",
        reactivity_profile="balanced",
    )

    events = runtime.process_event(
        _validation_event(
            6,
            deterministic_failures=[
                "The live run did not change any required website files."
            ],
            builder_retry_attempted=True,
        )
    )

    assert events == []


def test_super_hook_runtime_suppresses_repair_after_attempt_exhausted(
    tmp_path: Path,
) -> None:
    runtime = SuperHookRuntime(
        state_root=tmp_path / ".dan-super" / "state",
        run_id="super-dan-live:1",
        turn_id="1",
        task_id="super-dan-live:1",
        trace_id="trace:test",
        reactivity_profile="balanced",
    )

    events = runtime.process_event(
        _validation_event(
            7,
            changed_required_files=["/tmp/site/index.html"],
            deterministic_failures=[
                "The generated website still looks like the generic Super DAN contract/demo template."
            ],
            repair_attempted=True,
            repair_exhausted=True,
        )
    )

    assert events == []


def test_super_hook_runtime_coalesces_validation_bursts_when_inbox_is_full(
    tmp_path: Path,
) -> None:
    runtime = SuperHookRuntime(
        state_root=tmp_path / ".dan-super" / "state",
        run_id="super-dan-live:1",
        turn_id="1",
        task_id="super-dan-live:1",
        trace_id="trace:test",
        policies={
            "validation": SuperQueuePolicy(
                "validation",
                max_pending=1,
                max_active_leases=0,
                queue_full_action="coalesce",
            )
        },
        auto_ack=False,
    )

    runtime.process_event(_write_event(1, "index.html"))
    events = runtime.process_event(_write_event(2, "styles.css"))

    assert any(event["event"] == "super.inbox.packet_coalesced" for event in events)
    state = runtime.snapshot()
    validation = state["inboxes"]["validation"]
    assert len(validation["pending_packet_ids"]) == 1
    assert validation["metrics"]["enqueued"] == 2
    assert validation["metrics"]["coalesced"] == 1
    packets = [
        json.loads(line)
        for line in (tmp_path / ".dan-super" / "state" / "packets.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert packets[-1]["owner_scope"] == "file:styles.css"


def test_super_hook_runtime_holds_and_releases_single_owner_lease(
    tmp_path: Path,
) -> None:
    runtime = SuperHookRuntime(
        state_root=tmp_path / ".dan-super" / "state",
        run_id="super-dan-live:1",
        turn_id="1",
        task_id="super-dan-live:1",
        trace_id="trace:test",
        policies={
            "validation": SuperQueuePolicy(
                "validation",
                max_pending=2,
                max_active_leases=1,
                queue_full_action="coalesce",
            )
        },
        auto_ack=False,
    )

    runtime.process_event(_write_event(1, "index.html"))
    runtime.process_event(_write_event(2, "styles.css"))
    state = runtime.snapshot()
    validation = state["inboxes"]["validation"]
    assert len(validation["active_lease_ids"]) == 1
    assert len(validation["pending_packet_ids"]) == 1
    lease_id = validation["active_lease_ids"][0]

    release_events = runtime.release_lease(lease_id)

    assert any(event["event"] == "super.lease.released" for event in release_events)
    state = runtime.snapshot()
    assert state["inboxes"]["validation"]["active_lease_ids"] == []
    assert state["owner_locks"]["active"] == {}


def test_super_hook_runtime_holds_same_owner_packet_behind_lock(
    tmp_path: Path,
) -> None:
    runtime = SuperHookRuntime(
        state_root=tmp_path / ".dan-super" / "state",
        run_id="super-dan-live:1",
        turn_id="1",
        task_id="super-dan-live:1",
        trace_id="trace:test",
        policies={
            "validation": SuperQueuePolicy(
                "validation",
                max_pending=2,
                max_active_leases=2,
                queue_full_action="coalesce",
            )
        },
        auto_ack=False,
    )

    runtime.process_event(_write_event(1, "index.html"))
    events = runtime.process_event(_write_event(2, "index.html"))

    assert any(
        event["event"] == "super.inbox.packet_waiting_on_owner_lock"
        for event in events
    )
    state = runtime.snapshot()
    validation = state["inboxes"]["validation"]
    assert len(validation["active_lease_ids"]) == 1
    assert len(validation["pending_packet_ids"]) == 1
    assert len(state["owner_locks"]["active"]) == 1


def test_super_hook_runtime_drops_stale_heartbeat_packets(tmp_path: Path) -> None:
    runtime = SuperHookRuntime(
        state_root=tmp_path / ".dan-super" / "state",
        run_id="super-dan-live:1",
        turn_id="1",
        task_id="super-dan-live:1",
        trace_id="trace:test",
        policies={
            "immune": SuperQueuePolicy(
                "immune",
                max_pending=4,
                max_active_leases=0,
                queue_full_action="priority_preempt",
            )
        },
        auto_ack=False,
    )

    runtime.process_event(_heartbeat_event(1, 31))
    events = runtime.process_event(_heartbeat_event(2, 45))

    assert any(event["event"] == "super.inbox.packet_dropped" for event in events)
    state = runtime.snapshot()
    immune = state["inboxes"]["immune"]
    assert len(immune["pending_packet_ids"]) == 1
    assert immune["metrics"]["enqueued"] == 2
    assert immune["metrics"]["dropped"] == 1


def test_super_hook_runtime_admits_worktree_diff_without_main_workspace_mutation(
    tmp_path: Path,
) -> None:
    runtime = SuperHookRuntime(
        state_root=tmp_path / ".dan-super" / "state",
        run_id="super-dan-live:1",
        turn_id="1",
        task_id="super-dan-live:1",
        trace_id="trace:test",
        policies={
            "validation": SuperQueuePolicy(
                "validation",
                max_pending=1,
                max_active_leases=0,
                queue_full_action="coalesce",
            )
        },
        worktree_parallelism=1,
        auto_ack=False,
    )
    runtime.process_event(_write_event(1, "index.html"))
    packet_id = runtime.snapshot()["inboxes"]["validation"]["pending_packet_ids"][0]

    task, task_events = runtime.plan_worktree_task(packet_id, reason="test_conflict")
    assert task is not None
    assert any(event["event"] == "super.worktree.task_planned" for event in task_events)
    diff_events = runtime.admit_worktree_diff(
        {
            "task_id": task.task_id,
            "owner_scope": task.owner_scope,
            "changed_files": ["index.html"],
            "summary": "candidate patch from isolated worktree",
            "validation_evidence": ["read index.html"],
            "candidate_score": 0.81,
            "diff_ref": "diff:abc123",
        }
    )

    assert diff_events == [
        {
            "event": "super.worktree.diff_admitted",
            "diff_id": diff_events[0]["diff_id"],
            "task_id": task.task_id,
            "packet_id": task.packet_id,
            "owner_scope": task.owner_scope,
            "changed_files": ["index.html"],
            "candidate_score": 0.81,
            "merge_required": True,
            "operator_visible": True,
        }
    ]
    assert not (tmp_path / "index.html").exists()
    state = runtime.snapshot()
    assert state["worktrees"]["total_tasks"] == 1
    assert state["worktrees"]["total_diffs"] == 1
    diff = next(iter(state["worktrees"]["diffs"].values()))
    assert diff["status"] == "admitted"


def test_super_hook_runtime_rejects_empty_worktree_diff(tmp_path: Path) -> None:
    runtime = SuperHookRuntime(
        state_root=tmp_path / ".dan-super" / "state",
        run_id="super-dan-live:1",
        turn_id="1",
        task_id="super-dan-live:1",
        trace_id="trace:test",
        policies={
            "validation": SuperQueuePolicy(
                "validation",
                max_pending=1,
                max_active_leases=0,
                queue_full_action="coalesce",
            )
        },
        worktree_parallelism=1,
        auto_ack=False,
    )
    runtime.process_event(_write_event(1, "index.html"))
    packet_id = runtime.snapshot()["inboxes"]["validation"]["pending_packet_ids"][0]
    task, _events = runtime.plan_worktree_task(packet_id)
    assert task is not None

    diff_events = runtime.admit_worktree_diff(
        {
            "task_id": task.task_id,
            "owner_scope": task.owner_scope,
            "changed_files": [],
            "summary": "empty candidate",
        }
    )

    assert diff_events[0]["event"] == "super.worktree.diff_rejected"
    assert "changed files" in diff_events[0]["reason"]
    diff = next(iter(runtime.snapshot()["worktrees"]["diffs"].values()))
    assert diff["status"] == "rejected"


def test_super_hook_replay_rebuilds_state_without_duplicate_packets(tmp_path: Path) -> None:
    event_log = tmp_path / ".dan-super" / "runs" / "turn-01" / "events.jsonl"
    event_log.parent.mkdir(parents=True)
    rows = [
        {
            "event": "run.log.started",
            "sequence": 1,
            "task_id": "super-dan-live:1",
            "turn_id": "1",
            "trace_id": "trace:test",
        },
        _write_event(2, "index.html"),
        {
            "event": "live.validation.completed",
            "sequence": 3,
            "task_id": "super-dan-live:1",
            "turn_id": "1",
            "passed": True,
            "overall_score": 0.9,
        },
    ]
    event_log.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )

    first = replay_super_hook_events(event_log)
    second = replay_super_hook_events(event_log)

    assert first["status"] == "completed"
    assert second["status"] == "completed"
    packets = [
        json.loads(line)
        for line in (tmp_path / ".dan-super" / "state" / "packets.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert len(packets) == 2
    assert {packet["inbox_id"] for packet in packets} == {"validation", "synthesis"}
    assert len(
        [
            event
            for event in second["emitted_events"]
            if event["event"] == "super.hook.packet_deduplicated"
        ]
    ) == 2


def test_super_hook_status_formats_persisted_inbox_state(tmp_path: Path) -> None:
    runtime = SuperHookRuntime(
        state_root=tmp_path / ".dan-super" / "state",
        run_id="super-dan-live:1",
        turn_id="1",
        task_id="super-dan-live:1",
        trace_id="trace:test",
        reactivity_profile="immediate",
        worktree_parallelism=2,
    )
    runtime.process_event(_write_event(1, "index.html"))

    status = format_super_queue_status(tmp_path)

    assert "Super DAN queues" in status
    assert "reactivity: immediate" in status
    assert "worktree parallelism: 2" in status
    assert "- validation:" in status
    assert "enqueued=1" in status
