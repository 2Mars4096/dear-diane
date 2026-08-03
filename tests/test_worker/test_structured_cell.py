from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from dan.worker.core.contracts import OutputContract
from dan.worker.core.executor import WorkerExecutionResult
from dan.worker.structured_cell import (
    CHILD_JOIN_CONTEXT_KEY,
    CHILD_REPORTS_CONTEXT_KEY,
    LINEAGE_CONTEXT_KEY,
    PREVIOUS_REPORT_CONTEXT_KEY,
    CellAcceptance,
    CellContract,
    CellInvocation,
    CellRecord,
    CellReport,
    CellRuntimeConfig,
    CellSpec,
    CellTopology,
    ContextView,
    ExecutorRef,
    ForkJoinRelation,
    JoinPolicy,
    RecordRequirement,
    SequenceRelation,
    UniversalCellConfig,
    bind_cell,
    build_cell_spec,
    build_structured_cell,
    collapse_child_reports,
    compile_cell,
    execute_structured_cell,
    fan_out_and_collapse,
    handoff_to_next,
    inherit_previous_report,
    join_ready,
    link_linear,
    prepare_children,
)


class _FakeExecutor:
    def __init__(self, *outcomes: WorkerExecutionResult | Exception) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0
        self.worker = None
        self.request = None

    async def execute(self, worker, request) -> WorkerExecutionResult:
        self.calls += 1
        self.worker = worker
        self.request = request
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _parent_contract() -> CellContract:
    return CellContract(
        allowed_tools={"file_read", "file_edit", "web_search"},
        dos=("cite evidence",),
        donts=("leave the workspace",),
        preferences=("inspect focused files first",),
        limits={"max_tool_calls": 12, "max_tokens": 4000},
        acceptance=CellAcceptance(
            output=OutputContract(definition_of_done="Return a grounded result."),
            checks={"citations_required": True},
        ),
    )


def _child_contract() -> CellContract:
    return CellContract(
        allowed_tools={"file_read", "web_search"},
        dos=("return one evidence note",),
        donts=("write files",),
        preferences=("prefer primary sources",),
        limits={"max_tool_calls": 4, "max_tokens": 1200},
        acceptance=CellAcceptance(checks={"evidence_note": True}),
    )


def _fork_config(
    *,
    child_count: int = 3,
    join_policy: JoinPolicy | None = None,
    runtime_concurrency: int = 4,
    fork_concurrency: int | None = 2,
) -> UniversalCellConfig:
    parent = build_cell_spec(
        {
            "task": "Coordinate the child wave.",
            "role": "coordinator",
            "shared": {
                "sources": ["a", "b"],
                "brief": {"public": "usable", "secret": "must not descend"},
            },
            "private_notes": "parent only",
        },
        _parent_contract(),
    )
    children = {
        f"child-{index}": build_cell_spec(
            {"task": f"Inspect lane {index}.", "role": "reader", "lane": index},
            _child_contract(),
        )
        for index in range(child_count)
    }
    next_cell = build_cell_spec(
        {"task": "Use the joined result.", "role": "synthesizer", "priority": "local"}
    )
    child_ids = tuple(children)
    fork = ForkJoinRelation(
        parent_cell_id="parent",
        child_cell_ids=child_ids,
        context_views={
            child_id: ContextView(
                include_paths=("shared.sources", "shared.brief.public"),
            )
            for child_id in child_ids
        },
        join_policy=join_policy or JoinPolicy(),
        max_concurrency=fork_concurrency,
    )
    return UniversalCellConfig(
        cells={"parent": parent, **children, "next": next_cell},
        executors={
            "parent": "model-parent",
            **{child_id: "model-child" for child_id in children},
            "next": "model-next",
        },
        topology=CellTopology(
            sequences=(
                SequenceRelation(source_cell_id="parent", target_cell_id="next"),
            ),
            fork_joins=(fork,),
        ),
        runtime=CellRuntimeConfig(max_concurrency=runtime_concurrency),
    )


def test_cell_definition_is_only_context_and_contract() -> None:
    cell = build_cell_spec(
        {"task": "Review the implementation."},
        CellContract(allowed_tools={"file_read"}),
    )

    assert set(CellSpec.model_fields) == {"context", "contract"}
    assert "model" not in CellSpec.model_fields
    assert "cell_id" not in CellSpec.model_fields
    assert "status" not in CellSpec.model_fields
    assert "relationships" not in CellSpec.model_fields
    assert cell.context["task"] == "Review the implementation."


def test_contract_uses_the_reduced_consistent_vocabulary() -> None:
    assert set(CellContract.model_fields) == {
        "allowed_tools",
        "dos",
        "donts",
        "preferences",
        "limits",
        "acceptance",
    }
    assert "directions" not in CellContract.model_fields
    assert "forbidden_tools" not in CellContract.model_fields
    assert "budgets" not in CellContract.model_fields


def test_executor_and_identity_are_external_invocation_bindings() -> None:
    cell = build_cell_spec({"task": "Perform the same work."})

    first = bind_cell(cell, "model-a", cell_id="same-cell")
    second = bind_cell(cell, ExecutorRef(executor_id="model-b"), cell_id="same-cell")

    assert set(CellInvocation.model_fields) == {"cell_id", "cell", "executor"}
    assert first.cell is cell
    assert first.executor.executor_id == "model-a"
    assert second.executor.executor_id == "model-b"
    assert first.cell == second.cell


def test_convenience_builder_still_builds_and_binds_in_one_call() -> None:
    invocation = build_structured_cell(
        "model-v3",
        {"task": "Produce one answer."},
        CellContract(dos=("be concise",)),
        cell_id="one",
    )

    assert invocation.cell_id == "one"
    assert invocation.cell.contract.dos == ("be concise",)
    assert invocation.executor.model_name == "model-v3"


def test_compiler_lowers_reduced_contract_into_original_runtime_contracts() -> None:
    output = OutputContract(
        definition_of_done="Return a grounded verdict.",
        expected_return_shape="verdict plus findings",
        output_schema={
            "type": "object",
            "properties": {"verdict": {"type": "string"}},
            "required": ["verdict"],
        },
    )
    invocation = build_structured_cell(
        "model-v3",
        {
            "task": "Review the implementation.",
            "scope": "src/dan/worker",
            "role": "reviewer",
            "output_contract": {"definition_of_done": "must be ignored"},
        },
        CellContract(
            allowed_tools={"file_read"},
            dos=("inspect evidence",),
            donts=("modify files",),
            preferences=("start with the smallest relevant module",),
            limits={"max_tool_calls": 3, "max_tokens": 900},
            acceptance=CellAcceptance(
                output=output,
                checks={"minimum_findings": 1},
                required_records=(
                    RecordRequirement(kind="artifact", role="deliverable"),
                ),
            ),
        ),
        cell_id="review",
    )

    compiled = compile_cell(invocation)

    assert compiled.worker.model == "model-v3"
    assert compiled.worker.role == "reviewer"
    assert compiled.worker.llm_hints.max_tokens == 900
    assert compiled.brief.task == "Review the implementation."
    assert compiled.brief.output_contract == output
    assert compiled.brief.soft_constraints == [
        "start with the smallest relevant module"
    ]
    assert compiled.request.constraints.scope == "src/dan/worker"
    assert compiled.request.tooling.allowed_tool_ids == ["file_read"]
    assert compiled.request.tooling.max_tool_calls == 3
    assert compiled.request.output_contract == output
    assert compiled.request.metadata["validation_policy"]["minimum_findings"] == 1
    assert (
        compiled.request.metadata["validation_policy"]["required_records"][0]["role"]
        == "deliverable"
    )
    assert "Do not: modify files" in compiled.request.constraints.hard_constraints


def test_nested_context_view_subsamples_without_leaking_unselected_context() -> None:
    projection = ContextView(
        include_paths=("project.brief.public", "sources"),
        exclude_paths=("sources.private",),
    ).project(
        {
            "project": {"brief": {"public": "yes", "secret": "no"}},
            "sources": {"public": ["a"], "private": ["b"]},
            "private_notes": "do not copy",
        }
    )

    assert projection.selected_context == {
        "project": {"brief": {"public": "yes"}},
        "sources": {"public": ["a"]},
    }
    assert projection.model_context == projection.selected_context
    assert "secret" not in str(projection.model_context)
    assert "private_notes" not in str(projection.model_context)


def test_model_projection_is_bounded_while_logical_context_remains_complete() -> None:
    logical_context = {
        "task": "Use a large logical context.",
        "context_packet": {"private": "must not bypass the view"},
        "payload": "z" * 2000,
        "workspace_root": "/not/projected",
    }
    invocation = build_structured_cell("model-v3", logical_context, cell_id="bounded")
    view = ContextView(include_paths=("task", "payload"), max_rendered_chars=128)

    compiled = compile_cell(invocation, context_view=view)

    assert compiled.context_projection.selected_context["payload"] == "z" * 2000
    assert compiled.context_projection.truncated is True
    assert len(compiled.request.input_payload["_bounded_context_json"]) == 128
    assert "context_packet" not in compiled.request.input_payload
    assert "workspace_root" not in compiled.request.input_payload
    assert compiled.brief.context_packet == {}


def test_topology_is_external_and_linear_linking_does_not_mutate_cells() -> None:
    cells = tuple(
        build_structured_cell("model", {"task": f"Run {cell_id}."}, cell_id=cell_id)
        for cell_id in ("previous", "focal", "next")
    )
    before = tuple(cell.model_dump(mode="python") for cell in cells)

    topology = link_linear(*cells)

    assert [
        (edge.source_cell_id, edge.target_cell_id) for edge in topology.sequences
    ] == [
        ("previous", "focal"),
        ("focal", "next"),
    ]
    assert tuple(cell.model_dump(mode="python") for cell in cells) == before


def test_config_requires_exact_external_executor_bindings() -> None:
    cell = build_cell_spec({"task": "A"})

    with pytest.raises(
        ValidationError, match="executor bindings must match cells exactly"
    ):
        UniversalCellConfig(cells={"a": cell}, executors={})

    config = UniversalCellConfig(cells={"a": cell}, executors={"a": "model-a"})
    invocation = config.invocation("a")
    assert invocation.cell is cell
    assert invocation.executor.model_name == "model-a"


def test_overall_config_rejects_unknown_refs_and_horizontal_cycles() -> None:
    a = build_cell_spec({"task": "A"})
    b = build_cell_spec({"task": "B"})

    with pytest.raises(ValidationError, match="unknown cells"):
        UniversalCellConfig(
            cells={"a": a},
            executors={"a": "model"},
            topology=CellTopology(
                sequences=(
                    SequenceRelation(source_cell_id="a", target_cell_id="missing"),
                )
            ),
        )

    with pytest.raises(
        ValidationError, match="sequence topology cannot contain cycles"
    ):
        UniversalCellConfig(
            cells={"a": a, "b": b},
            executors={"a": "model", "b": "model"},
            topology=CellTopology(
                sequences=(
                    SequenceRelation(source_cell_id="a", target_cell_id="b"),
                    SequenceRelation(source_cell_id="b", target_cell_id="a"),
                )
            ),
        )


def test_overall_config_rejects_vertical_cycles() -> None:
    a = build_cell_spec({"task": "A"})
    b = build_cell_spec({"task": "B"})
    topology = CellTopology(
        fork_joins=(
            ForkJoinRelation(
                parent_cell_id="a",
                child_cell_ids=("b",),
                context_views={"b": ContextView()},
            ),
            ForkJoinRelation(
                parent_cell_id="b",
                child_cell_ids=("a",),
                context_views={"a": ContextView()},
            ),
        )
    )

    with pytest.raises(ValidationError, match="parent/child cycles"):
        UniversalCellConfig(
            cells={"a": a, "b": b},
            executors={"a": "model", "b": "model"},
            topology=topology,
        )


def test_vertical_child_gets_exact_view_and_monotone_contract() -> None:
    config = _fork_config(child_count=1)

    parent, children = prepare_children(config, "parent")
    child = children[0]

    assert parent.cell_id == "parent"
    assert child.context == {
        "shared": {"sources": ["a", "b"], "brief": {"public": "usable"}},
        "task": "Inspect lane 0.",
        "role": "reader",
        "lane": 0,
    }
    assert "secret" not in str(child.context)
    assert "private_notes" not in child.context
    assert child.contract.allowed_tools == frozenset({"file_read", "web_search"})
    assert child.contract.limits == {"max_tool_calls": 4, "max_tokens": 1200}
    assert child.contract.dos == ("cite evidence", "return one evidence note")
    assert child.contract.donts == ("leave the workspace", "write files")
    assert child.contract.preferences == (
        "inspect focused files first",
        "prefer primary sources",
    )
    assert child.contract.acceptance.checks == {
        "citations_required": True,
        "evidence_note": True,
    }


@pytest.mark.parametrize(
    ("requested", "message"),
    [
        (CellContract(allowed_tools={"database_admin"}), "cannot add tools"),
        (
            CellContract(
                allowed_tools={"file_read"},
                limits={"max_tool_calls": 13},
            ),
            "cannot exceed parent limit",
        ),
        (
            CellContract(
                acceptance=CellAcceptance(checks={"citations_required": False})
            ),
            "cannot replace parent key",
        ),
    ],
)
def test_vertical_delegation_rejects_contract_widening(
    requested: CellContract,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _parent_contract().narrow_for_child(requested)


def test_horizontal_inheritance_preserves_context_and_compact_report() -> None:
    previous = build_structured_cell(
        "model",
        {"shared": {"evidence": [1, 2]}, "priority": "old", "task": "Previous"},
        cell_id="previous",
    )
    report = CellReport(
        cell_id="previous",
        context_delta={"derived": {"answer": 42}},
        result={"answer": 42},
        records=(CellRecord(kind="source", resource="evidence://one"),),
    )
    target = build_structured_cell(
        "model",
        {"task": "Continue the work.", "priority": "local"},
        cell_id="focal",
    )
    relation = SequenceRelation(source_cell_id="previous", target_cell_id="focal")

    inherited = inherit_previous_report(previous, report, target, relation)

    assert inherited.context["shared"] == {"evidence": [1, 2]}
    assert inherited.context["derived"] == {"answer": 42}
    assert inherited.context["priority"] == "local"
    assert inherited.context[LINEAGE_CONTEXT_KEY] == ["previous"]
    compact = inherited.context[PREVIOUS_REPORT_CONTEXT_KEY]
    assert compact["result"] == {"answer": 42}
    assert compact["records"][0]["kind"] == "source"
    assert "context" not in compact


@pytest.mark.asyncio
async def test_children_are_bounded_parallel_and_collapse_compact_reports() -> None:
    config = _fork_config(child_count=3, runtime_concurrency=4, fork_concurrency=2)
    active = 0
    max_active = 0

    async def runner(child: CellInvocation) -> CellReport:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return CellReport(
            cell_id=child.cell_id,
            context_delta={"finding_count": 1},
            result={"lane": child.context["lane"], "finding": "ok"},
            records=(
                CellRecord(
                    kind="artifact",
                    resource=f"artifact://{child.cell_id}",
                    role="intermediate",
                ),
            ),
        )

    collapsed, reports = await fan_out_and_collapse(config, "parent", runner)
    relation = config.topology.fork_for_parent("parent")
    assert relation is not None

    assert max_active == 2
    assert len(reports) == 3
    assert join_ready(collapsed, relation) is True
    compact_reports = collapsed.context[CHILD_REPORTS_CONTEXT_KEY]
    assert list(compact_reports) == ["child-0", "child-1", "child-2"]
    assert all("context" not in report for report in compact_reports.values())

    advanced = handoff_to_next(
        config,
        collapsed,
        CellReport(cell_id="parent", result={"joined": True}),
    )
    assert set(advanced.context[CHILD_REPORTS_CONTEXT_KEY]) == {
        "child-0",
        "child-1",
        "child-2",
    }
    assert advanced.context["task"] == "Use the joined result."
    assert advanced.context["priority"] == "local"
    assert advanced.context[PREVIOUS_REPORT_CONTEXT_KEY]["result"] == {"joined": True}


@pytest.mark.asyncio
async def test_failed_child_settles_but_all_success_join_blocks_next() -> None:
    config = _fork_config(child_count=2, join_policy=JoinPolicy(mode="all_success"))

    async def runner(child: CellInvocation) -> CellReport:
        if child.cell_id == "child-1":
            raise RuntimeError("source unavailable")
        return CellReport(cell_id=child.cell_id, result="ok")

    collapsed, reports = await fan_out_and_collapse(config, "parent", runner)
    relation = config.topology.fork_for_parent("parent")
    assert relation is not None

    assert [report.outcome for report in reports] == ["completed", "failed"]
    assert collapsed.context[CHILD_JOIN_CONTEXT_KEY]["all_settled"] is True
    assert join_ready(collapsed, relation) is False
    with pytest.raises(ValueError, match="join is accepted"):
        handoff_to_next(
            config,
            collapsed,
            CellReport(cell_id="parent", result="premature"),
        )


def test_join_policies_are_evaluated_after_exact_all_settled_collapse() -> None:
    reports = (
        CellReport(cell_id="child-0", result="ok"),
        CellReport(cell_id="child-1", outcome="failed", error="failed"),
        CellReport(cell_id="child-2", result="ok"),
    )

    assert JoinPolicy(mode="all_success").accepts(reports) is False
    assert JoinPolicy(mode="all_settled").accepts(reports) is True
    assert JoinPolicy(mode="at_least_one").accepts(reports) is True
    assert JoinPolicy(mode="quorum", quorum=2).accepts(reports) is True
    assert JoinPolicy(mode="quorum", quorum=3).accepts(reports) is False

    config = _fork_config(child_count=3, join_policy=JoinPolicy(mode="all_settled"))
    parent = config.invocation("parent")
    relation = config.topology.fork_for_parent("parent")
    assert relation is not None
    collapsed = collapse_child_reports(parent, relation, reports)
    assert join_ready(collapsed, relation) is True

    with pytest.raises(ValueError, match="exactly the declared reports"):
        collapse_child_reports(parent, relation, reports[:-1])


@pytest.mark.asyncio
async def test_execution_collects_sources_line_modifications_outputs_and_deliverables() -> (
    None
):
    invocation = build_structured_cell(
        "model-v3",
        {
            "task": "Create the deliverable.",
            "input_sources": [
                {
                    "resource": "input/spec.md",
                    "digest": "sha256:input",
                    "provenance": {"method": "file_read"},
                }
            ],
        },
        cell_id="tracked",
    )
    executor = _FakeExecutor(
        WorkerExecutionResult(
            status="completed",
            outputs={
                "modifications": [
                    {
                        "path": "src/example.py",
                        "line_start": 10,
                        "line_end": 14,
                        "before_digest": "sha256:before",
                        "after_digest": "sha256:after",
                    }
                ],
                "output_files": ["output/intermediate.json"],
                "deliverables": [
                    {
                        "path": "output/report.pdf",
                        "media_type": "application/pdf",
                        "digest": "sha256:pdf",
                    }
                ],
            },
        )
    )

    report = await execute_structured_cell(executor, invocation)

    assert report.outcome == "completed"
    source = next(record for record in report.records if record.kind == "source")
    change = next(record for record in report.records if record.kind == "modification")
    deliverable = next(
        record for record in report.records if record.role == "deliverable"
    )
    assert source.resource == "input/spec.md"
    assert source.role == "input"
    assert change.resource == "src/example.py"
    assert change.location == {"line_start": 10, "line_end": 14}
    assert change.metadata["before_digest"] == "sha256:before"
    assert change.metadata["after_digest"] == "sha256:after"
    assert deliverable.resource == "output/report.pdf"
    assert deliverable.digest == "sha256:pdf"
    assert deliverable.metadata["media_type"] == "application/pdf"


@pytest.mark.asyncio
async def test_required_record_acceptance_is_enforced() -> None:
    contract = CellContract(
        acceptance=CellAcceptance(
            required_records=(
                RecordRequirement(
                    kind="artifact",
                    role="deliverable",
                    media_type="application/pdf",
                    resource_pattern="output/*.pdf",
                ),
            )
        )
    )
    invocation = build_structured_cell(
        "model-v3",
        {"task": "Return a PDF."},
        contract,
        cell_id="required",
    )

    missing = await execute_structured_cell(
        _FakeExecutor(
            WorkerExecutionResult(status="completed", outputs={"text": "no file"})
        ),
        invocation,
    )
    accepted = await execute_structured_cell(
        _FakeExecutor(
            WorkerExecutionResult(
                status="completed",
                outputs={
                    "deliverables": [
                        {"path": "output/final.pdf", "media_type": "application/pdf"}
                    ]
                },
            )
        ),
        invocation,
    )

    assert missing.outcome == "failed"
    assert "missing required records" in str(missing.error)
    assert accepted.outcome == "completed"


def test_record_kind_is_open_for_future_tracking_extensions() -> None:
    record = CellRecord(
        kind="human-approval",
        resource="approval://review-7",
        role="gate",
        metadata={"reviewer": "operator"},
    )
    requirement = RecordRequirement(kind="human-approval", role="gate")

    assert requirement.matches(record) is True
    report = CellReport(cell_id="cell", records=(record,))
    assert set(CellReport.model_fields) == {
        "cell_id",
        "outcome",
        "result",
        "context_delta",
        "records",
        "error",
    }
    assert "status" not in CellReport.model_fields
    assert "output" not in CellReport.model_fields
    assert report.records == (record,)


@pytest.mark.asyncio
async def test_standalone_execution_uses_compiler_and_runtime_retries() -> None:
    executor = _FakeExecutor(
        WorkerExecutionResult(status="failed", error="temporary"),
        WorkerExecutionResult(status="completed", outputs={"text": "done"}),
    )
    invocation = build_structured_cell(
        "model-v3",
        {"task": "Produce one answer.", "role": "answerer"},
        cell_id="one-cell",
    )

    report = await execute_structured_cell(
        executor,
        invocation,
        CellRuntimeConfig(max_retries=1),
    )

    assert executor.calls == 2
    assert executor.worker.model == "model-v3"
    assert executor.request.metadata["structured_cell_id"] == "one-cell"
    assert report.outcome == "completed"
    assert report.result["text"] == "done"


@pytest.mark.asyncio
async def test_exhausted_execution_exceptions_become_failed_report() -> None:
    executor = _FakeExecutor(RuntimeError("first"), RuntimeError("second"))
    invocation = build_structured_cell(
        "model-v3", {"task": "Try safely."}, cell_id="safe"
    )

    report = await execute_structured_cell(
        executor,
        invocation,
        CellRuntimeConfig(max_retries=1),
    )

    assert executor.calls == 2
    assert report.outcome == "failed"
    assert report.error == "RuntimeError: second"


@pytest.mark.asyncio
async def test_runtime_timeout_is_enforced_per_attempt() -> None:
    class _SlowExecutor:
        calls = 0

        async def execute(self, _worker, _request) -> WorkerExecutionResult:
            self.calls += 1
            await asyncio.sleep(0.05)
            return WorkerExecutionResult(status="completed", outputs={"late": True})

    executor = _SlowExecutor()
    invocation = build_structured_cell(
        "model-v3",
        {"task": "Stay bounded."},
        cell_id="timed",
    )

    report = await execute_structured_cell(
        executor,
        invocation,
        CellRuntimeConfig(max_retries=1, timeout_seconds=0.001),
    )

    assert executor.calls == 2
    assert report.outcome == "failed"
    assert report.error is not None
    assert report.error.startswith("TimeoutError:")


def test_reduced_contracts_are_available_from_worker_public_api() -> None:
    from dan import worker

    assert worker.CellSpec is CellSpec
    assert worker.CellContract is CellContract
    assert worker.CellAcceptance is CellAcceptance
    assert worker.CellInvocation is CellInvocation
    assert worker.ExecutorRef is ExecutorRef
    assert worker.CellRecord is CellRecord
    assert worker.RecordRequirement is RecordRequirement
    assert worker.build_cell_spec is build_cell_spec
    assert worker.bind_cell is bind_cell
