from __future__ import annotations

import json

import pytest

from dan.models.control_flow import (
    GateNode,
    HumanInTheLoopNode,
    HumanNode,
    InputNode,
    InputVariable,
    ReduceNode,
    RouterNode,
    ValidationRule,
    ValidatorNode,
    VoteNode,
)
from dan.models.edges import DataEdge
from dan.models.graph import Graph
from dan.models.node_taxonomy import RUNTIME_NODE_TYPE_MAP
from dan.models.nodes import CodeOperator, LLMOperator, RAGOperator, ReflectionNode, ToolOperator
from dan.models.ports import InputPort, OutputPort
from dan.worker import (
    BRIDGED_LEGACY_NODE_TYPES,
    CellAddress,
    CellHandoffPacket,
    CrossCellTraceLog,
    EXPLICIT_NON_BRIDGED_LEGACY_NODE_TYPES,
    ExecutionRequest,
    HandoffExecution,
    HandoffTask,
    OutputContract,
    SignalTrace,
    TissueExecution,
    TissueExecutionResult,
    TissueMember,
    TissuePoolLimits,
    WorkerExecutionResult,
    WorkerCoreExecutor,
    WorkerDefinition,
    compact_universal_validator_organ,
    convert_graph,
    execute_tissue_pattern,
    infer_execution_effect,
    legacy_to_worker,
    mutation_claim_requires_evidence,
    normalize_structured_outcome_payload,
    role,
    structured_outcome_view,
    supports_legacy_conversion,
    universal_validator_organ,
    validate_conversion,
    worker_to_legacy,
    parallel_worker_pool,
    execute_coding_organism,
)
from dan.worker.core.interfaces import CompletionResponse
from dan.worker.model import LLMHints, Worker
from dan.worker.organisms.coding_execution import (
    CodingTask,
    _apply_frontend_contract_hygiene,
    _artifact_readiness_ledger,
    _candidate_readiness_key,
    _deterministic_validation_precheck,
    _incremental_worker_merge_candidate,
    _incremental_validation_precheck_record,
    _normalize_orchestrator_plan,
    _partial_aggregation_fragment,
    _worker_material_yield_signal,
    _worker_pool_pattern,
    coding_execution_organism,
)
from dan.worker.organisms.local_runtime import LocalOrganismToolRuntime


def test_role_factory_returns_standard_worker() -> None:
    reviewer = role("reviewer", model="test-model", persona="Review carefully")

    assert isinstance(reviewer, Worker)
    assert reviewer.id == "reviewer"
    assert reviewer.name == "reviewer"
    assert reviewer.role == "reviewer"
    assert reviewer.model == "test-model"
    assert reviewer.persona == "Review carefully"


@pytest.mark.asyncio
async def test_tissue_member_completion_callback_observes_each_member_before_merge() -> None:
    members = [
        TissueMember(
            member_id="worker-1",
            address=CellAddress(
                cell_id="pool.worker-1",
                tissue_id="pool",
                organism_id="test-organism",
            ),
            worker=WorkerDefinition(id="pool.worker-1", role="coding_worker"),
            input_payload_overrides={"worker_index": 1},
        ),
        TissueMember(
            member_id="worker-2",
            address=CellAddress(
                cell_id="pool.worker-2",
                tissue_id="pool",
                organism_id="test-organism",
            ),
            worker=WorkerDefinition(id="pool.worker-2", role="coding_worker"),
            input_payload_overrides={"worker_index": 2},
        ),
    ]
    pattern = parallel_worker_pool(
        "pool",
        members=members,
        limits=TissuePoolLimits(max_concurrency=2),
    )
    packet = CellHandoffPacket(
        trace=SignalTrace(trace_id="trace:tissue-callback", root_task_id="task"),
        sender=CellAddress(cell_id="orchestrator", organism_id="test-organism"),
        recipient=CellAddress(
            cell_id="pool.coordinator",
            tissue_id="pool",
            organism_id="test-organism",
        ),
        task=HandoffTask(task_id="task:workers", instruction="Run workers."),
        output_contract=OutputContract(),
    )
    observed: list[tuple[str, int]] = []

    execution = await execute_tissue_pattern(
        executor=WorkerCoreExecutor(),
        pattern=pattern,
        packet=packet,
        member_completion_callback=lambda member, _packet, member_execution: observed.append(
            (
                member.member_id,
                int(member_execution.result.outputs["result"]["worker_index"]),
            )
        ),
    )

    assert execution.result.status == "completed"
    assert sorted(observed) == [("worker-1", 1), ("worker-2", 2)]
    assert sorted(execution.result.outputs["member_results"]) == ["worker-1", "worker-2"]


def test_legacy_llm_round_trips_through_worker_bridge() -> None:
    legacy = LLMOperator(
        id="draft",
        name="Draft",
        model="test-model",
        prompt_template="Draft {topic}",
        system_prompt="You are concise.",
        temperature=0.2,
        output_ports=[OutputPort(name="text")],
    )

    worker = legacy_to_worker(legacy)

    assert isinstance(worker, Worker)
    assert worker.model == "test-model"
    assert worker.llm_hints is not None
    assert worker.llm_hints.prompt_template == "Draft {topic}"
    assert worker.llm_hints.system_prompt == "You are concise."

    rebuilt = worker_to_legacy(worker)

    assert isinstance(rebuilt, LLMOperator)
    assert rebuilt.model == "test-model"
    assert rebuilt.prompt_template == "Draft {topic}"
    assert rebuilt.system_prompt == "You are concise."


def test_convert_graph_workerizes_convertible_nodes_and_preserves_structure() -> None:
    graph = Graph(
        nodes=[
            LLMOperator(
                id="draft",
                name="Draft",
                model="test-model",
                prompt_template="Draft {topic}",
                output_ports=[OutputPort(name="text")],
            ),
            ToolOperator(
                id="fetch",
                name="Fetch",
                tool_id="file_read",
                output_ports=[OutputPort(name="result")],
            ),
            CodeOperator(
                id="format",
                name="Format",
                code="result = input",
                output_ports=[OutputPort(name="result")],
            ),
            InputNode(
                id="entry",
                name="Entry",
                variables=[],
                output_ports=[OutputPort(name="input")],
            ),
        ],
        edges=[
            DataEdge(
                id="e1",
                source_node_id="entry",
                source_port="input",
                target_node_id="draft",
                target_port="input",
            ),
            DataEdge(
                id="e2",
                source_node_id="draft",
                source_port="text",
                target_node_id="format",
                target_port="input",
            ),
        ],
        sub_graphs={
            "draft_body": Graph(
                nodes=[
                    ToolOperator(
                        id="lookup",
                        name="Lookup",
                        tool_id="web_search",
                        output_ports=[OutputPort(name="result")],
                    )
                ],
                edges=[],
                entry_points=["lookup"],
                exit_points=["lookup"],
            )
        },
        entry_points=["entry"],
        exit_points=["format"],
        worker_resources={"instruction_profiles": {"review": {"instruction": "Review"}}},
    )

    converted = convert_graph(graph)

    assert all(isinstance(node, Worker) for node in converted.nodes)
    assert isinstance(converted.sub_graphs["draft_body"].nodes[0], Worker)
    assert converted.worker_resources == graph.worker_resources
    assert validate_conversion(graph, converted) == []


def test_structured_outcome_normalization_repairs_char_split_fields() -> None:
    payload = {
        "candidate_id": "candidate-1",
        "change_summary": "Verification only - no code changes required.",
        "target_files": ["/workspace/absharks/shark-anatomy.html"],
        "test_plan": list("1. Open shark-anatomy.html\n2. Verify navbar"),
        "risks": "- Visual verification still required",
    }

    normalized = normalize_structured_outcome_payload(payload)

    assert normalized["candidate_id"] == "candidate-1"
    assert normalized["test_plan"] == [
        "1. Open shark-anatomy.html",
        "2. Verify navbar",
    ]
    assert normalized["risks"] == ["Visual verification still required"]
    assert normalized["workspace_effect"] == "verified"


def test_structured_outcome_mutation_proof_policy_keys_off_effect_not_file_presence() -> None:
    verified = structured_outcome_view(
        {
            "candidate_id": "candidate-verified",
            "change_summary": "No code changes required.",
            "target_files": ["/workspace/absharks/shark-anatomy.html"],
        }
    )
    modified = structured_outcome_view(
        {
            "candidate_id": "candidate-modified",
            "change_summary": "Patched the navbar hover treatment.",
            "target_files": ["/workspace/absharks/styles.css"],
        }
    )

    assert infer_execution_effect(verified) == "verified"
    assert mutation_claim_requires_evidence(verified) is False
    assert infer_execution_effect(modified) == "modified"
    assert mutation_claim_requires_evidence(modified) is True


def test_universal_validator_accepts_explicit_tie_break_priority() -> None:
    organ = universal_validator_organ(
        organism_id="test-organism",
        review_tie_break_priority=["pass", "repair"],
    )

    assert organ.tissue is not None
    assert organ.tissue.quorum is not None
    assert organ.tissue.quorum.tie_break_priority == ["pass", "repair"]
    assert organ.metadata["review_tie_break_priority"] == ["pass", "repair"]


def test_compact_universal_validator_reuses_report_contract_without_quorum() -> None:
    organ = compact_universal_validator_organ(organism_id="test-organism")

    assert organ.kind.value == "universal_validator"
    assert organ.tissue is None
    assert organ.tissue_coordinator is None
    assert organ.boundary_contract.required_output_keys == [
        "passed",
        "overall_score",
        "dimension_scores",
        "repair_brief",
        "missing_requirements",
        "comparison_note",
    ]
    assert organ.metadata["validation_mode"] == "compact_model_review"


def test_coding_orchestrator_plan_collapses_duplicate_briefs_back_to_one_worker() -> None:
    plan = _normalize_orchestrator_plan(
        outputs={
            "worker_count": 3,
            "worker_briefs": [
                "Fix the navbar hover effect in styles.css.",
                "Fix the navbar hover effect in styles.css.",
                "Fix the navbar hover effect in styles.css.",
            ],
        },
        organism=coding_execution_organism(model="gpt-test"),
        task=CodingTask(task_id="coding-task", objective="Fix the navbar hover effect."),
    )

    assert plan.worker_count == 1
    assert plan.worker_briefs == ["Fix the navbar hover effect in styles.css."]


def test_deterministic_validation_precheck_selects_compact_policy_for_static_blockers(tmp_path) -> None:
    (tmp_path / "index.html").write_text(
        """
<!doctype html>
<html>
<head><link rel="stylesheet" href="./missing.css"></head>
<body><main><h1>Updated</h1></main></body>
</html>
""".strip(),
        encoding="utf-8",
    )
    task = CodingTask(
        task_id="coding-task",
        objective="Refresh index.html quickly.",
        session_context={
            "workspace_root": str(tmp_path),
            "completion_timeout_seconds": 45.0,
            "short_completion_timeout": True,
        },
    )
    plan = _normalize_orchestrator_plan(
        outputs={
            "worker_count": 1,
            "worker_briefs": ["Patch index.html."],
            "validator_focus": "Validate the frontend candidate.",
        },
        organism=coding_execution_organism(model="gpt-test"),
        task=task,
    )

    report = _deterministic_validation_precheck(
        task=task,
        plan=plan,
        candidate_payload={
            "candidate_id": "candidate-1",
            "change_summary": "Updated index.html.",
            "target_files": ["index.html"],
            "test_plan": ["open index.html"],
            "workspace_effect": "modified",
        },
        candidate_source="worker",
    )

    assert report["policy_source"] == "compact_validator"
    assert report["blocking_errors"] == [
        "index.html references missing local file ./missing.css"
    ]
    assert report["checks"] == ["frontend_static_contract"]


def test_orchestrator_plan_records_scheduler_action_selection_for_artifact_partitions(tmp_path) -> None:
    for path in ("index.html", "styles.css", "app.js"):
        (tmp_path / path).write_text(f"/* {path} */\n", encoding="utf-8")
    task = CodingTask(
        task_id="coding-task",
        objective="Refresh the existing target files as non-overlapping artifacts.",
        session_context={
            "workspace_root": str(tmp_path),
            "target_artifacts": ["index.html", "styles.css", "app.js"],
        },
    )

    plan = _normalize_orchestrator_plan(
        outputs={
            "worker_count": 1,
            "worker_briefs": ["Patch the existing files."],
            "validator_focus": "Validate the frontend candidate.",
        },
        organism=coding_execution_organism(model="gpt-test"),
        task=task,
    )

    assert plan.worker_count == 3
    assert plan.scheduler_policy_source == "generic_artifact_partition"
    assert plan.scheduler_decision["selected_action"] == "parallel"
    assert plan.scheduler_decision["selected_proposal"]["required_capacity"] == 3
    assert len(plan.scheduler_decision["rankings"]) == 2
    assert plan.artifact_owner_paths == ["index.html", "styles.css", "app.js"]


def test_orchestrator_plan_avoids_owner_lanes_for_non_mutated_artifacts(tmp_path) -> None:
    for path in ("index.html", "styles.css", "app.js", "index_backup.html"):
        (tmp_path / path).write_text(f"/* {path} */\n", encoding="utf-8")
    task = CodingTask(
        task_id="coding-task",
        objective=(
            "Read index.html from disk. Use a shell command to report exact structural "
            "tag counts. If needed, overwrite index.html with a single full-file write. "
            "Do not modify styles.css or app.js."
        ),
        session_context={"workspace_root": str(tmp_path)},
    )

    plan = _normalize_orchestrator_plan(
        outputs={
            "worker_count": 4,
            "worker_briefs": ["Run the bounded repair."],
            "validator_focus": "Validate the frontend candidate.",
        },
        organism=coding_execution_organism(model="gpt-test"),
        task=task,
    )

    assert plan.worker_count == 1
    assert plan.scheduler_policy_source == ""
    assert plan.scheduler_decision == {}
    assert plan.artifact_owner_paths == []


def test_orchestrator_plan_ignores_backup_workspace_artifacts(tmp_path) -> None:
    for path in ("index.html", "styles.css", "app.js", "index_backup.html"):
        (tmp_path / path).write_text(f"/* {path} */\n", encoding="utf-8")
    task = CodingTask(
        task_id="coding-task",
        objective="Refresh the existing target files as non-overlapping artifacts.",
        session_context={"workspace_root": str(tmp_path)},
    )

    plan = _normalize_orchestrator_plan(
        outputs={
            "worker_count": 1,
            "worker_briefs": ["Patch the existing files."],
            "validator_focus": "Validate the frontend candidate.",
        },
        organism=coding_execution_organism(model="gpt-test"),
        task=task,
    )

    assert plan.worker_count == 3
    assert plan.scheduler_policy_source == "generic_artifact_partition"
    assert plan.artifact_owner_paths == ["index.html", "styles.css", "app.js"]


@pytest.mark.asyncio
async def test_file_edit_missing_content_fails_before_approval(tmp_path) -> None:
    events: list[dict[str, object]] = []
    approval_calls: list[tuple[str, dict[str, object]]] = []
    (tmp_path / "index.html").write_text("<main>ok</main>\n", encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_edit"],
        workspace_root=tmp_path,
        approval_callback=lambda tool_id, arguments, metadata: approval_calls.append(
            (tool_id, dict(arguments))
        )
        or True,
        event_callback=events.append,
    )

    with pytest.raises(ValueError, match="file_edit: content"):
        await runtime.call(
            "file_edit",
            {
                "path": "index.html",
                "start_line": 1,
                "end_line": 1,
            },
            worker_id="coding-build.worker-3",
        )

    assert approval_calls == []
    assert [event["event"] for event in events] == ["tool.started", "tool.failed"]
    assert "tool_arguments_invalid: missing required arguments for file_edit: content" in str(
        events[1]["error"]
    )


@pytest.mark.asyncio
async def test_file_edit_placeholder_content_fails_before_approval(tmp_path) -> None:
    events: list[dict[str, object]] = []
    approval_calls: list[tuple[str, dict[str, object]]] = []
    target = tmp_path / "index.html"
    target.write_text("<main>ok</main>\n", encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_edit"],
        workspace_root=tmp_path,
        approval_callback=lambda tool_id, arguments, metadata: approval_calls.append(
            (tool_id, dict(arguments))
        )
        or True,
        event_callback=events.append,
    )

    with pytest.raises(ValueError, match="dummy read-instruction"):
        await runtime.call(
            "file_edit",
            {
                "path": "index.html",
                "start_line": 1,
                "end_line": 1,
                "content": "dummy call to read - will error, use file_read instead",
            },
            worker_id="coding-build.worker-1",
        )

    assert approval_calls == []
    assert target.read_text(encoding="utf-8") == "<main>ok</main>\n"
    assert [event["event"] for event in events] == ["tool.started", "tool.failed"]
    assert "content contains dummy read-instruction edit content" in str(
        events[1]["error"]
    )


@pytest.mark.asyncio
async def test_file_edit_delete_replacement_fails_before_approval(tmp_path) -> None:
    events: list[dict[str, object]] = []
    approval_calls: list[tuple[str, dict[str, object]]] = []
    target = tmp_path / "index.html"
    target.write_text("<main>ok</main>\n", encoding="utf-8")
    runtime = LocalOrganismToolRuntime(
        tool_ids=["file_edit"],
        workspace_root=tmp_path,
        approval_callback=lambda tool_id, arguments, metadata: approval_calls.append(
            (tool_id, dict(arguments))
        )
        or True,
        event_callback=events.append,
    )

    with pytest.raises(ValueError, match="delete mode"):
        await runtime.call(
            "file_edit",
            {
                "path": "index.html",
                "start_line": 1,
                "end_line": 1,
                "mode": "delete",
                "new_string": "<main>patched</main>\n",
            },
            worker_id="coding-build.worker-1",
        )

    assert approval_calls == []
    assert target.read_text(encoding="utf-8") == "<main>ok</main>\n"
    assert [event["event"] for event in events] == ["tool.started", "tool.failed"]
    assert "new_string cannot be used with delete mode" in str(events[1]["error"])


def test_worker_material_yield_signal_separates_candidate_from_written_artifact() -> None:
    read_only_signal = _worker_material_yield_signal(
        member_id="worker-1",
        payload={
            "candidate_id": "candidate-readonly",
            "change_summary": "Proposed a bounded patch but did not write it.",
            "target_files": ["src/app.py"],
            "test_plan": ["pytest -q"],
            "risks": [],
            "workspace_effect": "modified",
        },
        executed_tools=[
            {
                "tool_id": "file_read",
                "ok": True,
                "arguments": {"path": "src/app.py"},
                "result": {"path": "src/app.py"},
            }
        ],
    )
    written_signal = _worker_material_yield_signal(
        member_id="worker-2",
        owner_path="src/app.py",
        payload={
            "candidate_id": "candidate-written",
            "change_summary": "Patched the app.",
            "target_files": ["src/app.py"],
            "test_plan": ["pytest -q"],
            "risks": [],
            "workspace_effect": "modified",
        },
        executed_tools=[
            {
                "tool_id": "file_edit",
                "ok": True,
                "arguments": {"path": "src/app.py"},
                "result": {"path": "src/app.py"},
            }
        ],
    )
    timeout_signal = _worker_material_yield_signal(
        member_id="worker-3",
        payload={
            "candidate_id": "Provider completion timed out before generation finished.",
            "change_summary": "Provider completion timed out before generation finished.",
            "target_files": [],
            "test_plan": [],
            "risks": [],
        },
        executed_tools=[],
    )

    assert read_only_signal["candidate_material"] is True
    assert read_only_signal["materialized_artifact"] is False
    assert read_only_signal["no_material_reason"] == "missing_mutation_evidence"
    assert written_signal["candidate_material"] is True
    assert written_signal["materialized_artifact"] is True
    assert written_signal["matching_mutation_evidence"] is True
    assert written_signal["owner_path"] == "src/app.py"
    assert timeout_signal["candidate_material"] is False
    assert timeout_signal["materialized_artifact"] is False
    assert timeout_signal["no_material_reason"] == "timeout_or_blocked_no_output"


def test_artifact_readiness_ledger_marks_capsule_backed_owner_lanes_ready() -> None:
    def mutation_tool(worker_id: str, path: str, owner_path: str) -> dict[str, object]:
        capsule_id = f"ctx:{worker_id}:{owner_path}"
        return {
            "tool_id": "file_edit",
            "ok": True,
            "arguments": {"path": path},
            "result": {"path": path},
            "context_capsules": [
                {
                    "capsule_id": capsule_id,
                    "kind": "implementation_delta",
                    "summary": f"Changed {owner_path}",
                    "artifact_state": "useful_for_downstream",
                    "unlocks": ["implementation_delta", f"file_changed:{path}"],
                    "confidence": 0.9,
                }
            ],
            "readiness_signal": {
                "readiness_id": f"ready:{worker_id}:{owner_path}",
                "capsule_ids": [capsule_id],
                "ready_for_downstream": True,
                "predicate": "tool_context_available",
                "summary": f"{owner_path} is ready for downstream aggregation.",
                "blockers": [],
                "downstream_task_ids": [],
            },
        }

    organism = coding_execution_organism(model="gpt-test")
    task = CodingTask(
        task_id="coding-task",
        objective="Refresh the existing target files as non-overlapping artifacts.",
        session_context={"target_artifacts": ["index.html", "styles.css"]},
    )
    plan = _normalize_orchestrator_plan(
        outputs={
            "worker_count": 1,
            "worker_briefs": ["Patch the existing files."],
            "validator_focus": "Validate the frontend candidate.",
        },
        organism=organism,
        task=task,
    )
    pattern, coordinator = _worker_pool_pattern(
        organism=organism,
        attempt=1,
        plan=plan,
    )
    packet = CellHandoffPacket(
        trace=SignalTrace(trace_id="trace:readiness", root_task_id=task.task_id),
        sender=CellAddress(cell_id="orchestrator", organism_id=organism.organism_id),
        recipient=coordinator,
        task=HandoffTask(
            task_id=f"{task.task_id}:workers:1",
            instruction="Run owner lanes.",
        ),
        output_contract=OutputContract(),
    )
    member_results = {
        "worker-1": {
            "candidate_fragment": {"index.html": "<main>Updated</main>\n"},
            "change_summary": "Updated index.html.",
            "target_files": ["index.html"],
            "test_plan": ["open index.html"],
            "risks": [],
            "workspace_effect": "modified",
        },
        "worker-2": {
            "candidate_fragment": {"styles.css": "body { color: white; }\n"},
            "change_summary": "Updated styles.css.",
            "target_files": ["styles.css"],
            "test_plan": ["open index.html"],
            "risks": [],
            "workspace_effect": "modified",
        },
    }
    member_executions = [
        HandoffExecution(
            packet=packet,
            request=ExecutionRequest(task="worker-1"),
            result=WorkerExecutionResult(
                status="completed",
                outputs=member_results["worker-1"],
                metadata={
                    "raw_response": {
                        "executed_tools": [
                            mutation_tool(
                                "worker-1",
                                "/workspace/site/index.html",
                                "index.html",
                            )
                        ]
                    }
                },
            ),
        ),
        HandoffExecution(
            packet=packet,
            request=ExecutionRequest(task="worker-2"),
            result=WorkerExecutionResult(
                status="completed",
                outputs=member_results["worker-2"],
                metadata={
                    "raw_response": {
                        "executed_tools": [
                            mutation_tool(
                                "worker-2",
                                "/workspace/site/styles.css",
                                "styles.css",
                            )
                        ]
                    }
                },
            ),
        ),
    ]
    worker_execution = TissueExecution(
        pattern=pattern,
        packet=packet,
        result=TissueExecutionResult(
            status="completed",
            outputs={"member_results": member_results},
            metadata={"successful_member_ids": ["worker-1", "worker-2"]},
        ),
        member_executions=member_executions,
    )

    ledger = _artifact_readiness_ledger(
        plan=plan,
        member_results=member_results,
        worker_execution=worker_execution,
    )

    assert ledger["all_owner_paths_ready"] is True
    assert ledger["ready_owner_paths"] == ["index.html", "styles.css"]
    assert ledger["missing_owner_paths"] == []
    assert {lane["readiness_source"] for lane in ledger["lanes"]} == {
        "capsule_scheduler"
    }
    assert all(lane["scheduler_readiness"]["ready"] is True for lane in ledger["lanes"])

    fragments = [
        _partial_aggregation_fragment(
            member_id="worker-1",
            owner_path="index.html",
            payload=member_results["worker-1"],
            executed_tools=[
                mutation_tool(
                    "worker-1",
                    "/workspace/site/index.html",
                    "index.html",
                )
            ],
        ),
        _partial_aggregation_fragment(
            member_id="worker-2",
            owner_path="styles.css",
            payload=member_results["worker-2"],
            executed_tools=[
                mutation_tool(
                    "worker-2",
                    "/workspace/site/styles.css",
                    "styles.css",
                )
            ],
        ),
    ]
    candidate = _incremental_worker_merge_candidate(
        fragments=[fragment for fragment in fragments if fragment is not None],
        owner_paths=["index.html", "styles.css"],
        attempt=1,
    )

    assert candidate is not None
    assert candidate["candidate_id"] == "candidate-1-worker-merge"
    assert candidate["target_files"] == ["index.html", "styles.css"]
    assert candidate["candidate_fragment"] == {
        "index.html": "<main>Updated</main>\n",
        "styles.css": "body { color: white; }\n",
    }
    assert candidate["synthesized_from_incremental_reducer"] is True
    precheck_record = _incremental_validation_precheck_record(
        task=task,
        plan=plan,
        candidate_payload=candidate,
        candidate_source="worker_merge",
        attempt=1,
        fragment_count=2,
        owner_paths=["index.html", "styles.css"],
    )

    assert precheck_record["candidate_key"] == _candidate_readiness_key(candidate)
    assert precheck_record["candidate_id"] == "candidate-1-worker-merge"
    assert precheck_record["policy_source"] == "compact_validator"
    assert precheck_record["blocking_errors"] == []
    assert precheck_record["checks"] == ["artifact_owner_coverage"]
    assert precheck_record["validation_precheck"]["candidate_source"] == "worker_merge"


@pytest.mark.asyncio
async def test_coding_execution_reuses_speculative_compact_validation_from_incremental_merge(
    tmp_path,
) -> None:
    def mutation_tool(worker_id: str, path: str, owner_path: str) -> dict[str, object]:
        capsule_id = f"ctx:{worker_id}:{owner_path}"
        return {
            "tool_id": "file_edit",
            "ok": True,
            "arguments": {"path": path},
            "result": {"path": path},
            "context_capsules": [
                {
                    "capsule_id": capsule_id,
                    "kind": "implementation_delta",
                    "summary": f"Changed {owner_path}",
                    "artifact_state": "useful_for_downstream",
                    "unlocks": ["implementation_delta", f"file_changed:{path}"],
                    "confidence": 0.9,
                }
            ],
            "readiness_signal": {
                "readiness_id": f"ready:{worker_id}:{owner_path}",
                "capsule_ids": [capsule_id],
                "ready_for_downstream": True,
                "predicate": "tool_context_available",
                "summary": f"{owner_path} is ready for downstream validation.",
                "blockers": [],
                "downstream_task_ids": [],
            },
        }

    class Provider:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def complete(self, request) -> CompletionResponse:
            worker_id = str(request.metadata.get("worker_id") or "")
            self.calls.append(worker_id)
            if worker_id == "coding-build.orchestrator":
                payload = {
                    "public_response": "Patch two owned artifacts in parallel.",
                    "worker_count": 2,
                    "worker_briefs": [
                        "EXCLUSIVE WRITE OWNER: src/app.py. Patch the app module only.",
                        "EXCLUSIVE WRITE OWNER: README.md. Patch the README only.",
                    ],
                    "aggregation_focus": "Merge the two owner outputs into one candidate.",
                    "validator_focus": "Validate the merged candidate.",
                    "pass_threshold": 0.9,
                }
                return CompletionResponse(text=json.dumps(payload, sort_keys=True))
            if worker_id == "coding-build.worker-1":
                payload = {
                    "candidate_fragment": {"src/app.py": "print('ready')\n"},
                    "change_summary": "Updated app module.",
                    "target_files": ["src/app.py"],
                    "test_plan": ["python -m py_compile src/app.py"],
                    "risks": [],
                    "workspace_effect": "modified",
                }
                return CompletionResponse(
                    text=json.dumps(payload, sort_keys=True),
                    raw={
                        "executed_tools": [
                            mutation_tool(
                                "worker-1",
                                str(tmp_path / "src/app.py"),
                                "src/app.py",
                            )
                        ]
                    },
                )
            if worker_id == "coding-build.worker-2":
                payload = {
                    "candidate_fragment": {"README.md": "# Ready\n"},
                    "change_summary": "Updated README.",
                    "target_files": ["README.md"],
                    "test_plan": ["review README.md"],
                    "risks": [],
                    "workspace_effect": "modified",
                }
                return CompletionResponse(
                    text=json.dumps(payload, sort_keys=True),
                    raw={
                        "executed_tools": [
                            mutation_tool(
                                "worker-2",
                                str(tmp_path / "README.md"),
                                "README.md",
                            )
                        ]
                    },
                )
            if worker_id == "coding-build.validator.lead":
                payload = {
                    "passed": True,
                    "overall_score": 0.96,
                    "dimension_scores": {"boundedness": 0.96},
                    "repair_brief": "",
                    "missing_requirements": [],
                    "comparison_note": "Speculative compact validation passed.",
                }
                return CompletionResponse(text=json.dumps(payload, sort_keys=True))
            raise AssertionError(f"Unexpected worker_id: {worker_id}")

    provider = Provider()
    events: list[dict[str, object]] = []
    task = CodingTask(
        task_id="coding-task",
        objective="Patch two non-overlapping artifacts.",
        session_context={"workspace_root": str(tmp_path)},
    )

    execution = await execute_coding_organism(
        executor=WorkerCoreExecutor(completion_provider=provider),
        organism=coding_execution_organism(model="gpt-test"),
        task=task,
        trace_log=CrossCellTraceLog(),
        event_callback=events.append,
    )

    event_names = [str(event["event"]) for event in events]
    assert execution.result is not None
    assert execution.result.status == "completed"
    assert "validation.speculative_model.started" in event_names
    assert "validation.speculative_model.completed" in event_names
    assert "validation.speculative_model.reused" in event_names
    assert provider.calls.count("coding-build.validator.lead") == 1
    speculative_history = execution.result.metadata["speculative_validation_history"]
    assert speculative_history[0]["reused_at_final_validation"] is True
    assert speculative_history[0]["status"] == "completed"


def test_frontend_contract_hygiene_runs_for_normal_timeout_parallel_owner_merge(tmp_path) -> None:
    (tmp_path / "index.html").write_text(
        """
<!doctype html>
<html lang="en">
<head>
  <link rel="stylesheet" href="./styles.css" />
  <style>
    body { color: white; }
    <section class="organs reveal-on-scroll">
</head>
<body>
  <main>
    <section id="features" class="features">
      <article data-delay="1"><h2>Decision trails</h2></article>
    </section>
    <script src="./app.js"></script>
  </main>
</body>
</html>

    <script src="./app.js"></script>
  </main>
</body>
</html>
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "styles.css").write_text("body { color: white; }\n", encoding="utf-8")
    (tmp_path / "app.js").write_text("console.log('ready');\n", encoding="utf-8")
    task = CodingTask(
        task_id="coding-task",
        objective="Refresh the website.",
        session_context={
            "workspace_root": str(tmp_path),
            "completion_timeout_seconds": 90.0,
            "short_completion_timeout": False,
        },
    )
    candidate_payload = {
        "candidate_id": "candidate-1-worker-merge",
        "change_summary": "Merged parallel website lanes.",
        "target_files": ["index.html", "styles.css", "app.js"],
        "test_plan": [],
        "risks": [],
        "workspace_effect": "modified",
    }

    report = _apply_frontend_contract_hygiene(
        task=task,
        candidate_payload=candidate_payload,
        owner_paths=["index.html", "styles.css", "app.js"],
    )

    assert report is not None
    assert report["errors"] == []
    assert "closed unclosed style blocks before </head>" in report["repairs"]
    assert "removed duplicate document tail tags" in report["repairs"]
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "<section" not in html.split("</head>", 1)[0]
    assert html.count("</style>") == 1
    assert html.count('src="./app.js"') == 1
    assert html.count("</main>") == 1
    assert html.count("</body>") == 1
    assert html.count("</html>") == 1
    assert "data-animate-delay" in html


def test_worker_with_llm_hints_without_model_projects_to_legacy_llm_operator() -> None:
    worker = Worker(
        id="draft",
        name="Draft",
        llm_hints=LLMHints(
            prompt_template="Draft {input}",
            system_prompt="Stay concise.",
        ),
    )

    rebuilt = worker_to_legacy(worker)

    assert isinstance(rebuilt, LLMOperator)
    assert rebuilt.model == ""
    assert rebuilt.prompt_template == "Draft {input}"
    assert rebuilt.system_prompt == "Stay concise."


def test_worker_control_flow_projects_to_legacy_gate_node() -> None:
    worker = Worker(
        id="route",
        name="Route",
        control_flow={
            "condition": "score > 0.5",
            "gate_mode": "if_else",
            "max_iterations": 3,
        },
    )

    rebuilt = worker_to_legacy(worker)

    assert isinstance(rebuilt, GateNode)
    assert rebuilt.condition == "score > 0.5"
    assert rebuilt.gate_mode == "if_else"
    assert rebuilt.max_iterations == 3


def test_worker_metadata_bridges_project_specialized_legacy_nodes() -> None:
    input_worker = Worker(
        id="entry",
        name="Entry",
        metadata={"input_variables": [{"name": "topic", "type": "string"}]},
        output_ports=[OutputPort(name="input"), OutputPort(name="topic")],
    )
    router_worker = Worker(
        id="route",
        name="Route",
        role="router",
        model="test-model",
        metadata={"route_descriptions": {"research": "Do research"}},
        output_ports=[OutputPort(name="route"), OutputPort(name="result")],
    )
    validator_worker = Worker(
        id="validate",
        name="Validate",
        role="validator",
        metadata={
            "validation_rules": [{"rule_type": "required_keys", "config": {"keys": ["summary"]}}],
            "validator_on_failure": "halt",
            "validator_strict_mode": True,
        },
        input_ports=[InputPort(name="data")],
        output_ports=[OutputPort(name="valid"), OutputPort(name="invalid")],
    )
    reflection_worker = Worker(
        id="reflect",
        name="Reflect",
        role="reflection",
        model="test-model",
        metadata={
            "reflection_prompt": "Distill lessons.",
            "reflection_source": "last_run",
            "reflection_output_format": "principles",
            "reflection_max_principles": 8,
            "reflection_min_confidence": 0.4,
            "reflection_dedup_strategy": "exact_key",
        },
        output_ports=[OutputPort(name="principles")],
    )
    rag_worker = Worker(
        id="retrieve",
        name="Retrieve",
        role="rag",
        metadata={
            "rag_collection": "papers",
            "rag_top_k": 7,
            "rag_query_template": "Find chunks for {query}",
            "rag_include_metadata": False,
            "rag_rerank": True,
        },
        input_ports=[InputPort(name="query", required=False)],
        output_ports=[OutputPort(name="chunks")],
    )
    human_worker = Worker(
        id="review",
        name="Review",
        role="human",
        metadata={
            "human_prompt": "Review the draft",
            "human_render_mode": "approval",
            "human_instructions": "Approve or request changes",
            "human_render_target": "both",
        },
        input_ports=[InputPort(name="input", required=False)],
        output_ports=[OutputPort(name="response")],
    )
    hitl_worker = Worker(
        id="checkpoint",
        name="Checkpoint",
        role="human_in_the_loop",
        metadata={
            "human_prompt": "Continue?",
            "human_timeout_seconds": 30.0,
            "human_default_action": "resume",
        },
        input_ports=[InputPort(name="input", required=False)],
        output_ports=[OutputPort(name="response")],
    )
    vote_worker = Worker(
        id="choose_best",
        name="Choose Best",
        role="vote",
        metadata={
            "vote_candidates": ["claude-sonnet-4-6", "gpt-4o"],
            "vote_num_votes": 2,
            "vote_prompt_template": "Pick the best answer",
            "vote_strategy": "judge",
            "vote_parallelism": 2,
        },
        input_ports=[InputPort(name="input", required=False)],
        output_ports=[OutputPort(name="winner")],
    )

    rebuilt_input = worker_to_legacy(input_worker)
    rebuilt_router = worker_to_legacy(router_worker)
    rebuilt_validator = worker_to_legacy(validator_worker)
    rebuilt_reflection = worker_to_legacy(reflection_worker)
    rebuilt_rag = worker_to_legacy(rag_worker)
    rebuilt_human = worker_to_legacy(human_worker)
    rebuilt_hitl = worker_to_legacy(hitl_worker)
    rebuilt_vote = worker_to_legacy(vote_worker)

    assert isinstance(rebuilt_input, InputNode)
    assert [variable.name for variable in rebuilt_input.variables] == ["topic"]

    assert isinstance(rebuilt_router, RouterNode)
    assert rebuilt_router.route_descriptions == {"research": "Do research"}

    assert isinstance(rebuilt_validator, ValidatorNode)
    assert rebuilt_validator.on_failure == "halt"
    assert rebuilt_validator.strict_mode is True
    assert rebuilt_validator.validation_rules[0].rule_type == "required_keys"

    assert isinstance(rebuilt_reflection, ReflectionNode)
    assert rebuilt_reflection.reflection_prompt == "Distill lessons."
    assert rebuilt_reflection.max_principles == 8
    assert rebuilt_reflection.dedup_strategy == "exact_key"

    assert isinstance(rebuilt_rag, RAGOperator)
    assert rebuilt_rag.collection == "papers"
    assert rebuilt_rag.top_k == 7
    assert rebuilt_rag.query_template == "Find chunks for {query}"
    assert rebuilt_rag.include_metadata is False
    assert rebuilt_rag.rerank is True

    assert isinstance(rebuilt_human, HumanNode)
    assert rebuilt_human.prompt == "Review the draft"
    assert rebuilt_human.render_mode == "approval"
    assert rebuilt_human.render_target == "both"

    assert isinstance(rebuilt_hitl, HumanInTheLoopNode)
    assert rebuilt_hitl.prompt == "Continue?"
    assert rebuilt_hitl.timeout_seconds == 30.0
    assert rebuilt_hitl.default_action == "resume"

    assert isinstance(rebuilt_vote, VoteNode)
    assert rebuilt_vote.candidates == ["claude-sonnet-4-6", "gpt-4o"]
    assert rebuilt_vote.num_votes == 2
    assert rebuilt_vote.prompt_template == "Pick the best answer"
    assert rebuilt_vote.vote_strategy == "judge"
    assert rebuilt_vote.parallelism == 2


def test_legacy_specialized_compute_nodes_capture_worker_metadata_on_bridge() -> None:
    rag = RAGOperator(
        id="retrieve",
        name="Retrieve",
        collection="papers",
        top_k=7,
        query_template="Find chunks for {query}",
        include_metadata=False,
        rerank=True,
        input_ports=[InputPort(name="query", required=False)],
        output_ports=[OutputPort(name="chunks")],
    )
    human = HumanNode(
        id="review",
        name="Review",
        prompt="Review the draft",
        render_mode="approval",
        instructions="Approve or request changes",
        render_target="both",
        input_ports=[InputPort(name="input", required=False)],
        output_ports=[OutputPort(name="response")],
    )
    vote = VoteNode(
        id="choose_best",
        name="Choose Best",
        candidates=["claude-sonnet-4-6", "gpt-4o"],
        num_votes=2,
        prompt_template="Pick the best answer",
        vote_strategy="judge",
        parallelism=2,
        input_ports=[InputPort(name="input", required=False)],
        output_ports=[OutputPort(name="winner")],
    )

    rag_worker = legacy_to_worker(rag)
    human_worker = legacy_to_worker(human)
    vote_worker = legacy_to_worker(vote)

    assert isinstance(rag_worker, Worker)
    assert rag_worker.role == "rag"
    assert rag_worker.metadata["rag_collection"] == "papers"
    assert rag_worker.metadata["rag_query_template"] == "Find chunks for {query}"

    assert isinstance(human_worker, Worker)
    assert human_worker.role == "human"
    assert human_worker.metadata["human_prompt"] == "Review the draft"
    assert human_worker.metadata["human_render_target"] == "both"

    assert isinstance(vote_worker, Worker)
    assert vote_worker.role == "vote"
    assert vote_worker.metadata["vote_candidates"] == ["claude-sonnet-4-6", "gpt-4o"]
    assert vote_worker.metadata["vote_strategy"] == "judge"


def test_validate_conversion_reports_structural_drift() -> None:
    original = Graph(
        nodes=[
            ToolOperator(
                id="fetch",
                name="Fetch",
                tool_id="file_read",
                output_ports=[OutputPort(name="result")],
            ),
        ],
        edges=[],
        entry_points=["fetch"],
        exit_points=["fetch"],
    )
    converted = Graph(
        nodes=[
            Worker(
                id="fetch_renamed",
                name="Fetch",
                tool_ids=["file_read"],
                output_ports=[OutputPort(name="result")],
            ),
        ],
        edges=[],
        entry_points=["fetch"],
        exit_points=["fetch"],
    )

    errors = validate_conversion(original, converted)

    assert any("node ids changed" in error for error in errors)


def test_preset_bridge_explicitly_partitions_runtime_taxonomy() -> None:
    runtime_legacy_types = set(RUNTIME_NODE_TYPE_MAP) - {"worker"}

    assert BRIDGED_LEGACY_NODE_TYPES.isdisjoint(EXPLICIT_NON_BRIDGED_LEGACY_NODE_TYPES)
    assert BRIDGED_LEGACY_NODE_TYPES | EXPLICIT_NON_BRIDGED_LEGACY_NODE_TYPES == runtime_legacy_types


def test_supports_legacy_conversion_matches_bridge_constant() -> None:
    runtime_legacy_types = set(RUNTIME_NODE_TYPE_MAP) - {"worker"}

    assert {
        node_type for node_type in runtime_legacy_types if supports_legacy_conversion(node_type)
    } == BRIDGED_LEGACY_NODE_TYPES


def test_legacy_to_worker_supports_each_bridged_runtime_type() -> None:
    samples = {
        "llm_operator": LLMOperator(
            id="draft",
            name="Draft",
            model="test-model",
            prompt_template="Draft {input}",
            input_ports=[InputPort(name="input", required=False)],
            output_ports=[OutputPort(name="text")],
        ),
        "tool_operator": ToolOperator(
            id="fetch",
            name="Fetch",
            tool_id="file_read",
            input_ports=[InputPort(name="path", required=False)],
            output_ports=[OutputPort(name="result")],
        ),
        "code_operator": CodeOperator(
            id="normalize",
            name="Normalize",
            code="result = input",
            input_ports=[InputPort(name="input", required=False)],
            output_ports=[OutputPort(name="result")],
        ),
        "input": InputNode(
            id="entry",
            name="Entry",
            variables=[InputVariable(name="topic", type="string")],
            output_ports=[OutputPort(name="input"), OutputPort(name="topic")],
        ),
        "router": RouterNode(
            id="route",
            name="Route",
            model="test-model",
            route_descriptions={"research": "Do research", "draft": "Draft an answer"},
            output_ports=[OutputPort(name="route"), OutputPort(name="result")],
        ),
        "reduce": ReduceNode(
            id="merge",
            name="Merge",
            reducer="sum",
            input_ports=[InputPort(name="left", required=False), InputPort(name="right", required=False)],
            output_ports=[OutputPort(name="result")],
        ),
        "validator": ValidatorNode(
            id="validate",
            name="Validate",
            validation_rules=[
                ValidationRule(
                    rule_type="required_keys",
                    config={"keys": ["summary"]},
                )
            ],
        ),
        "reflection": ReflectionNode(
            id="reflect",
            name="Reflect",
            reflection_prompt="Distill lessons.",
            reflection_model="test-model",
        ),
        "rag_operator": RAGOperator(
            id="retrieve",
            name="Retrieve",
            collection="papers",
            top_k=3,
            query_template="Find chunks for {query}",
            input_ports=[InputPort(name="query", required=False)],
            output_ports=[OutputPort(name="chunks")],
        ),
        "human": HumanNode(
            id="review",
            name="Review",
            prompt="Review the draft",
            render_mode="approval",
            instructions="Approve or request changes",
            input_ports=[InputPort(name="input", required=False)],
            output_ports=[OutputPort(name="response")],
        ),
        "human_in_the_loop": HumanInTheLoopNode(
            id="checkpoint",
            name="Checkpoint",
            prompt="Continue?",
            timeout_seconds=30.0,
            default_action="resume",
            input_ports=[InputPort(name="input", required=False)],
            output_ports=[OutputPort(name="response")],
        ),
        "vote": VoteNode(
            id="choose_best",
            name="Choose Best",
            candidates=["claude-sonnet-4-6", "gpt-4o"],
            num_votes=2,
            prompt_template="Pick the best answer",
            vote_strategy="judge",
            parallelism=2,
            input_ports=[InputPort(name="input", required=False)],
            output_ports=[OutputPort(name="winner")],
        ),
    }

    assert set(samples) == BRIDGED_LEGACY_NODE_TYPES

    for node_type, node in samples.items():
        converted = legacy_to_worker(node)
        assert supports_legacy_conversion(node_type) is True
        assert isinstance(converted, Worker)
        assert converted.id == node.id
        assert converted.node_type == "worker"
