from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from dan.cli import super_organism as super_cli
from dan.cli.super_blueprint import (
    UnsafeBlueprintUpdate,
    _legacy_nodes_and_edges,
    _permission_scope,
    infer_task_family,
)


@pytest.mark.parametrize(
    ("objective", "work_mode", "expected"),
    [
        ("Explain this function", "chat_answer", "direct"),
        ("Fix the current failing parser test", "workspace_change", "debugging"),
        ("Research the literature and cite the papers", "workspace_read", "research"),
        ("Design three dashboard wireframes", "workspace_change", "design"),
        (
            "Turn this meeting transcript into decisions and action items",
            "workspace_change",
            "meeting",
        ),
        (
            "Prepare a DFM review and supplier quote package",
            "workspace_change",
            "manufacturing",
        ),
        ("Build a small local utility", "workspace_change", "general"),
    ],
)
def test_super_blueprint_family_inference(
    objective: str,
    work_mode: str,
    expected: str,
) -> None:
    assert infer_task_family(objective, work_mode=work_mode) == expected


def test_super_blueprint_permission_scope_never_widens_path_limited_policy() -> None:
    permissions = _permission_scope(
        {
            "allow_workspace_mutation": True,
            "allow_shell_command": False,
            "forbid_other_workspace_inputs": True,
            "allowed_read_paths": ["input.csv"],
            "allowed_write_paths": ["report.md"],
        }
    )

    assert permissions == (
        "workspace:read:input.csv",
        "workspace:write:report.md",
    )
    assert "workspace:read" not in permissions
    assert "workspace:write" not in permissions


def test_super_blueprint_maps_heterogeneous_planner_tasks_to_semantic_roles() -> None:
    tasks = [
        {"task_id": "h", "goal": "Probe the primary hypothesis"},
        {"task_id": "e", "goal": "Collect counter-evidence"},
        {"task_id": "v", "goal": "Develop a contrasting variant"},
        {"task_id": "a", "goal": "Request explicit approval"},
        {"task_id": "d", "goal": "Run the DFM review"},
        {"task_id": "s", "goal": "Prepare the engineering specification"},
        {"task_id": "q", "goal": "Compare supplier quotes"},
        {"task_id": "r", "goal": "Reconcile conflicting decisions"},
    ]

    nodes, _ = _legacy_nodes_and_edges(tasks, criterion_ids=[])
    roles = {node.node_id: (node.kind, node.topology_role) for node in nodes}

    assert roles == {
        "h": ("work", "hypothesis"),
        "e": ("work", "evidence"),
        "v": ("work", "variant"),
        "a": ("gate", "approval"),
        "d": ("gate", "dfm_gate"),
        "s": ("artifact", "specification"),
        "q": ("artifact", "quote_comparison"),
        "r": ("decision", "reconciliation"),
    }


def test_super_event_logger_keeps_legacy_graph_event_and_adds_canonical_event(
    tmp_path,
) -> None:
    legacy_state = {
        "schema": "super_dan_task_graph_v1",
        "revision": 2,
        "tasks": [{"task_id": "1", "goal": "Do the work", "state": "ready"}],
    }

    class _Bridge:
        def observe_event(self, event):
            assert event["task_graph_state"] == legacy_state
            return [
                {
                    "event": "live.task_blueprint.updated",
                    "task_blueprint": {"schema": "dan_task_blueprint_v1"},
                }
            ]

        def snapshot(self):
            return {"task_blueprint": {"schema": "dan_task_blueprint_v1"}}

        def compile_plan_context(self, plan_context):
            return dict(plan_context)

    path = tmp_path / "events.jsonl"
    logger = super_cli.SuperRunEventLogger(path=path, blueprint_bridge=_Bridge())
    logger.emit(
        {
            "event": "live.task_graph.updated",
            "source": "planner",
            "task_graph_state": legacy_state,
        }
    )
    logger.close()

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["event"] for row in rows] == [
        "live.task_graph.updated",
        "live.task_blueprint.updated",
    ]
    assert rows[0]["source"] == "planner"
    assert rows[0]["task_graph_state"] == legacy_state
    assert rows[1]["task_blueprint"]["schema"] == "dan_task_blueprint_v1"
    assert (
        logger.blueprint_snapshot()["task_blueprint"]["schema"]
        == "dan_task_blueprint_v1"
    )


def test_super_blueprint_rejects_rewrite_of_active_legacy_task() -> None:
    from dan.cli.super_blueprint import SuperBlueprintEventBridge

    bridge = SuperBlueprintEventBridge(
        task_id="run-1",
        objective="Fix the failing test",
        workspace_root="/tmp/workspace",
    )
    bridge._legacy_specs = {
        "1": {
            "task_id": "1",
            "goal": "Reproduce the failure",
            "depends_on": [],
        }
    }
    bridge._node_states = {"1": "active"}

    with pytest.raises(UnsafeBlueprintUpdate, match="cannot rewrite active task"):
        bridge._guard_pinned_nodes(
            {
                "1": {
                    "task_id": "1",
                    "goal": "Replace the running task",
                    "depends_on": [],
                }
            }
        )


def test_super_blueprint_separates_semantic_revisions_from_attempt_progress(
    monkeypatch,
) -> None:
    from dan.cli import super_blueprint as bridge_module

    def blueprint(revision, tasks):
        return SimpleNamespace(
            blueprint_id="bp-1",
            revision_id=f"bp-1:r{revision}",
            revision=revision,
            family="debugging",
            nodes=tuple(
                SimpleNamespace(
                    node_id=task["task_id"],
                    title=task["goal"],
                    topology_role="legacy_task",
                    active=True,
                )
                for task in tasks
            ),
            edges=tuple(
                SimpleNamespace(
                    edge_id=f"dep:{dependency}:{task['task_id']}",
                    source_node_id=dependency,
                    target_node_id=task["task_id"],
                    kind="dependency",
                )
                for task in tasks
                for dependency in task.get("depends_on") or []
            ),
            model_dump=lambda mode="json": {
                "schema": "dan_task_blueprint_v1",
                "blueprint_id": "bp-1",
                "revision_id": f"bp-1:r{revision}",
                "revision": revision,
                "family": "debugging",
                "nodes": [{"node_id": task["task_id"]} for task in tasks],
            },
        )

    monkeypatch.setattr(
        bridge_module,
        "_create_initial_blueprint",
        lambda **kwargs: blueprint(1, kwargs["tasks"]),
    )
    monkeypatch.setattr(
        bridge_module,
        "_sync_legacy_graph",
        lambda current, **kwargs: blueprint(current.revision + 1, kwargs["tasks"]),
    )
    monkeypatch.setattr(
        bridge_module,
        "_strengthen_blueprint_contract",
        lambda current, **kwargs: current,
    )
    monkeypatch.setattr(
        bridge_module,
        "_make_execution_attempt",
        lambda **kwargs: {
            "schema": "dan_execution_attempt_v1",
            "status": kwargs["status"],
            "phase": kwargs["phase"],
            "blueprint_revision_id": kwargs["blueprint"].revision_id,
            "node_states": dict(kwargs["node_states"]),
        },
    )

    bridge = bridge_module.SuperBlueprintEventBridge(
        task_id="run-1",
        objective="Fix the failing parser test",
        workspace_root="/tmp/workspace",
    )
    first = bridge.observe_event(
        {
            "event": "live.task_graph.updated",
            "source": "request_understanding",
            "plan_context": {
                "request_understanding": {
                    "request_kind": "software",
                    "confidence_scoped_acceptance": [{"criterion": "The test passes."}],
                    "stop_rule": "Stop after focused tests pass.",
                }
            },
            "task_graph_state": {
                "tasks": [
                    {
                        "task_id": "1",
                        "goal": "Reproduce the parser failure",
                        "depends_on": [],
                        "state": "ready",
                    }
                ]
            },
        }
    )
    assert [event["event"] for event in first] == [
        "live.task_blueprint.updated",
        "live.execution_attempt.updated",
    ]
    assert first[0]["task_blueprint"]["revision"] == 1

    progress_only = bridge.observe_event(
        {
            "event": "live.task_graph.updated",
            "source": "execution_frontier",
            "task_graph_state": {
                "tasks": [
                    {
                        "task_id": "1",
                        "goal": "Reproduce the parser failure",
                        "depends_on": [],
                        "state": "active",
                    }
                ]
            },
        }
    )
    assert [event["event"] for event in progress_only] == [
        "live.execution_attempt.updated"
    ]
    assert bridge.snapshot()["task_blueprint"]["revision"] == 1
    assert bridge.snapshot()["execution_attempt"]["node_states"] == {"1": "active"}

    projected = bridge.compile_plan_context(
        {
            "task_graph": [{"task_id": "untrusted", "goal": "Do something else"}],
            "ready_task_ids": ["1", "untrusted"],
            "task_graph_state": {
                "tasks": [{"task_id": "1", "state": "active"}],
                "active_task_ids": ["1", "untrusted"],
            },
        }
    )
    assert [task["task_id"] for task in projected["task_graph"]] == ["1"]
    assert projected["ready_task_ids"] == ["1"]
    assert projected["task_graph_state"]["active_task_ids"] == ["1"]
    assert projected["task_blueprint_ref"]["revision_id"] == "bp-1:r1"


def test_super_blueprint_bridge_builds_canonical_contract_graph_and_attempt() -> None:
    from dan.cli.super_blueprint import SuperBlueprintEventBridge

    bridge = SuperBlueprintEventBridge(
        task_id="run-1",
        objective="Fix the failing parser test",
        workspace_root="/tmp/workspace",
        operator_policy={
            "work_mode": "workspace_change",
            "allow_workspace_mutation": True,
            "allow_shell_command": True,
            "constraints": ["Only touch parser.py."],
        },
        execution_budget={
            "max_tool_calls": 10,
            "max_work_seconds": 30,
            "max_auto_fix_rounds": 2,
            "max_validation_cycles": 2,
            "max_parallel_workers": 2,
        },
        requested_model="test-model",
    )

    emitted = bridge.observe_event(
        {
            "event": "live.task_graph.updated",
            "source": "request_understanding",
            "plan_context": {
                "request_understanding": {
                    "request_kind": "software",
                    "confidence_scoped_acceptance": [
                        {"criterion": "The focused parser test passes."}
                    ],
                    "stop_rule": "Stop after focused tests pass.",
                }
            },
            "task_graph_state": {
                "tasks": [
                    {
                        "task_id": "1",
                        "goal": "Reproduce the parser failure",
                        "depends_on": [],
                        "validation": ["Run the focused parser test"],
                        "state": "ready",
                    }
                ]
            },
        }
    )

    assert [event["event"] for event in emitted] == [
        "live.task_blueprint.updated",
        "live.execution_attempt.updated",
    ]
    snapshot = bridge.snapshot()
    blueprint = snapshot["task_blueprint"]
    attempt = snapshot["execution_attempt"]
    assert blueprint["schema"] == "dan_task_blueprint_v1"
    assert blueprint["family"] == "debugging"
    assert blueprint["contract"]["goal"] == "Fix the failing parser test"
    assert blueprint["contract"]["permissions"] == [
        "workspace:read",
        "workspace:write",
        "shell:execute",
    ]
    assert len(blueprint["contract"]["acceptance_criteria"]) == 2
    assert any(node["topology_role"] == "reproduction" for node in blueprint["nodes"])
    assert any(
        node["topology_role"] == "acceptance_gate" for node in blueprint["nodes"]
    )
    assert attempt["schema"] == "dan_execution_attempt_v1"
    assert attempt["blueprint_revision_id"] == blueprint["revision_id"]
    assert attempt["policy_snapshot"]["budget"]["max_tool_calls"] == 10
    assert attempt["model_summary"] == ["test-model"]

    terminal = bridge.observe_event(
        {"event": "run.log.completed", "status": "completed"}
    )
    assert [event["event"] for event in terminal] == ["live.execution_attempt.updated"]
    completed_attempt = bridge.snapshot()["execution_attempt"]
    assert completed_attempt["status"] == "completed"
    assert completed_attempt["phase"] == "completed"
    assert completed_attempt["completed_at"]


def test_super_blueprint_strengthens_acceptance_without_weakening_existing_contract() -> (
    None
):
    from dan.cli.super_blueprint import SuperBlueprintEventBridge

    bridge = SuperBlueprintEventBridge(
        task_id="run-criteria",
        objective="Research the current evidence",
        workspace_root="/tmp/workspace",
        operator_policy={"work_mode": "workspace_read"},
    )
    task_state = {
        "tasks": [
            {
                "task_id": "1",
                "goal": "Collect evidence",
                "depends_on": [],
                "state": "ready",
            }
        ]
    }
    bridge.observe_event(
        {
            "event": "live.task_graph.updated",
            "source": "request_understanding",
            "plan_context": {
                "request_understanding": {
                    "confidence_scoped_acceptance": [
                        {"criterion": "Every claim has a source."}
                    ],
                    "stop_rule": "Stop after source coverage is checked.",
                }
            },
            "task_graph_state": task_state,
        }
    )

    strengthened = bridge.observe_event(
        {
            "event": "live.task_graph.updated",
            "source": "planner",
            "plan_context": {
                "request_understanding": {
                    "confidence_scoped_acceptance": [
                        {"criterion": "Contradictory evidence is represented."}
                    ],
                    "stop_rule": "Stop after source coverage is checked.",
                }
            },
            "task_graph_state": task_state,
        }
    )

    blueprint_events = [
        event
        for event in strengthened
        if event["event"] == "live.task_blueprint.updated"
    ]
    assert len(blueprint_events) == 1
    blueprint = bridge.snapshot()["task_blueprint"]
    descriptions = {
        item["description"] for item in blueprint["contract"]["acceptance_criteria"]
    }
    assert descriptions == {
        "Every claim has a source.",
        "Stop after source coverage is checked.",
        "Contradictory evidence is represented.",
    }
    assert blueprint["revision"] == 2
    assert blueprint["derived_state"]["uncovered_criterion_ids"] == []


def test_super_dan_live_run_emits_canonical_events_and_returns_separate_attempt(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    from tests.test_cli.test_super_organism import _FakeLiveCodingProvider

    fake_provider = _FakeLiveCodingProvider()
    monkeypatch.setattr(
        super_cli,
        "_build_live_provider",
        lambda model, api_key=None, base_url=None: fake_provider,
    )
    output_path = tmp_path / "result.json"

    exit_code = super_cli.main(
        [
            "Fix the current failing parser test with a focused source edit.",
            "--live",
            "--model",
            "fake-live-model",
            "--workspace",
            str(tmp_path),
            "--output",
            str(output_path),
            "--json",
        ]
    )

    assert exit_code == 0
    capsys.readouterr()
    result = json.loads(output_path.read_text(encoding="utf-8"))["live_build"]
    assert result["task_blueprint"]["schema"] == "dan_task_blueprint_v1"
    assert result["execution_attempt"]["schema"] == "dan_execution_attempt_v1"
    assert (
        result["execution_attempt"]["blueprint_revision_id"]
        == result["task_blueprint"]["revision_id"]
    )
    assert "execution_attempt" not in result["task_blueprint"]
    assert (
        "super-dan.live.general-builder"
        in result["execution_attempt"]["worker_summary"]
    )
    assert result["execution_attempt"]["model_summary"] == ["fake-live-model"]
    assert "file_write" in result["execution_attempt"]["tool_summary"]
    assert result["execution_attempt"]["schedule"] == "dependency_frontier"

    event_path = tmp_path / ".dan-super" / "runs" / "turn-01" / "events.jsonl"
    rows = [
        json.loads(line)
        for line in event_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    blueprint_events = [
        row for row in rows if row["event"] == "live.task_blueprint.updated"
    ]
    attempt_events = [
        row for row in rows if row["event"] == "live.execution_attempt.updated"
    ]
    assert blueprint_events
    assert attempt_events
    assert all(row["schema"] == "organism_log_v1" for row in rows)
    assert blueprint_events[-1]["task_blueprint"]["schema"] == "dan_task_blueprint_v1"
    assert attempt_events[-1]["execution_attempt"]["status"] == "completed"
    assert rows[-1]["event"] == "run.log.completed"
