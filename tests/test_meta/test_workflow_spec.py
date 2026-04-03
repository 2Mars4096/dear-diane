from __future__ import annotations

import pytest

from dan.meta.workflow_spec import (
    ArtifactKind,
    ArtifactSpec,
    CandidateWorkflowWorkspace,
    DataSourceKind,
    DataSourceSpec,
    ExecutionFamily,
    infer_execution_family,
    NodeWorkerHints,
    NodeGrounding,
    ScheduleIntent,
    StructuredSectioningConfig,
    WorkflowRiskLevel,
    WorkflowSpec,
    WorkflowSpecNode,
    workflow_spec_from_intent,
)
from dan.meta.intent_schema import StageIntent, StageType, WorkflowIntent
from dan.server.agent_runtime.workflow_sectioning import partition_workflow_spec


def _node(
    node_id: str,
    node_type: str,
    execution_family: ExecutionFamily,
    *,
    dependencies: list[str] | None = None,
    chapter_label: str | None = None,
    section_label: str | None = None,
    grounding: NodeGrounding | None = None,
) -> WorkflowSpecNode:
    return WorkflowSpecNode(
        node_id=node_id,
        purpose=f"Purpose for {node_id}",
        node_type=node_type,
        execution_family=execution_family,
        dependencies=dependencies or [],
        chapter_label=chapter_label,
        section_label=section_label,
        grounding=grounding or NodeGrounding(),
        test_expectations=[f"{node_id} completes"],
    )


def test_workflow_spec_rejects_misgrounded_nodes() -> None:
    with pytest.raises(ValueError, match="tool_id"):
        _node("fetch_reports", "tool_operator", ExecutionFamily.tool)

    with pytest.raises(ValueError, match="operation_type"):
        _node("run_code", "code_operator", ExecutionFamily.code)

    with pytest.raises(ValueError, match="external actions"):
        _node(
            "search_brief",
            "llm_operator",
            ExecutionFamily.llm,
            grounding=NodeGrounding(declared_actions=["search"]),
        )

    with pytest.raises(ValueError, match="tool execution must declare grounding.tool_id"):
        WorkflowSpecNode(
            node_id="worker_tool",
            purpose="Fetch data",
            node_type="worker",
            execution_family=ExecutionFamily.tool,
            config={"tool_ids": ["web_search"]},
            grounding=NodeGrounding(),
        )


def test_workflow_spec_rejects_unknown_dependency_and_cycles() -> None:
    with pytest.raises(ValueError, match="unknown node_id"):
        WorkflowSpec(
            goal="Build a workflow",
            nodes=[
                _node(
                    "start",
                    "tool_operator",
                    ExecutionFamily.tool,
                    grounding=NodeGrounding(tool_id="file_read"),
                ),
                _node(
                    "end",
                    "llm_operator",
                    ExecutionFamily.llm,
                    dependencies=["missing"],
                ),
            ],
        )

    with pytest.raises(ValueError, match="Circular dependency"):
        WorkflowSpec(
            goal="Build a workflow",
            nodes=[
                _node(
                    "a",
                    "tool_operator",
                    ExecutionFamily.tool,
                    dependencies=["b"],
                    grounding=NodeGrounding(tool_id="file_read"),
                ),
                _node(
                    "b",
                    "llm_operator",
                    ExecutionFamily.llm,
                    dependencies=["a"],
                ),
            ],
        )


def test_workflow_spec_serializes_schedule_and_candidate_workspace() -> None:
    spec = WorkflowSpec(
        workflow_id="equity-brief",
        goal="Build a weekly earnings briefing workflow",
        nodes=[
            _node(
                "collect",
                "tool_operator",
                ExecutionFamily.tool,
                grounding=NodeGrounding(
                    tool_id="web_search",
                    data_sources=[
                        DataSourceSpec(
                            kind=DataSourceKind.api,
                            name="earnings_api",
                            description="Earnings calendar API",
                        )
                    ],
                ),
            ),
            _node(
                "draft",
                "llm_operator",
                ExecutionFamily.llm,
                dependencies=["collect"],
            ),
        ],
        expected_inputs=[
            ArtifactSpec(
                name="watchlist",
                kind=ArtifactKind.input,
                description="Symbols to monitor",
            )
        ],
        expected_outputs=[
            ArtifactSpec(
                name="weekly_briefing",
                kind=ArtifactKind.output,
                description="Final weekly briefing",
            )
        ],
        schedule=ScheduleIntent(
            trigger="weekly",
            delivery_target="slack:#briefings",
            run_profile="long_running",
            timezone="UTC",
        ),
    )
    workspace = CandidateWorkflowWorkspace(
        workflow_id="equity-brief",
        thread_id="thread-123",
        spec=spec,
        persisted_graph_id=None,
    )

    dump = workspace.model_dump(mode="json")
    assert dump["workflow_id"] == "equity-brief"
    assert dump["spec"]["schedule"]["trigger"] == "weekly"
    assert dump["spec"]["expected_inputs"][0]["kind"] == "input"
    assert dump["spec"]["expected_outputs"][0]["name"] == "weekly_briefing"


def test_sectioning_partitions_by_dependency_order_and_labels() -> None:
    spec = WorkflowSpec(
        goal="Build a staged workflow",
        nodes=[
            _node(
                "publish",
                "tool_operator",
                ExecutionFamily.tool,
                dependencies=["brief"],
                chapter_label="report",
                grounding=NodeGrounding(tool_id="send_email"),
            ),
            _node(
                "brief",
                "llm_operator",
                ExecutionFamily.llm,
                dependencies=["analyze"],
                chapter_label="report",
            ),
            _node(
                "analyze",
                "llm_operator",
                ExecutionFamily.llm,
                dependencies=["normalize"],
                chapter_label="report",
            ),
            _node(
                "collect",
                "tool_operator",
                ExecutionFamily.tool,
                chapter_label="ingest",
                grounding=NodeGrounding(tool_id="web_search"),
            ),
            _node(
                "normalize",
                "code_operator",
                ExecutionFamily.code,
                dependencies=["collect"],
                chapter_label="ingest",
                grounding=NodeGrounding(operation_type="transform"),
            ),
        ],
    )

    config = StructuredSectioningConfig(max_section_size=10, min_section_size=1, cross_section_penalty=1.0)
    sections = partition_workflow_spec(spec, config)

    assert [section.node_ids for section in sections] == [
        ["collect", "normalize"],
        ["analyze", "brief", "publish"],
    ]
    assert sections[0].chapter_label == "ingest"
    assert sections[1].chapter_label == "report"
    assert sections[0].downstream_dependents == ["analyze"]
    assert sections[1].upstream_dependencies == ["normalize"]


def test_sectioning_config_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAN_STRUCTURED_MAX_SECTION_SIZE", "9")
    monkeypatch.setenv("DAN_STRUCTURED_MIN_SECTION_SIZE", "3")
    monkeypatch.setenv("DAN_STRUCTURED_CROSS_SECTION_PENALTY", "2.25")

    config = StructuredSectioningConfig.from_env()

    assert config.max_section_size == 9
    assert config.min_section_size == 3
    assert config.cross_section_penalty == pytest.approx(2.25)


def test_workflow_spec_from_intent_projects_stage_metadata() -> None:
    intent = WorkflowIntent(
        goal="Build a weekly earnings workflow",
        stages=[
            StageIntent(
                name="collect",
                description="Search for the latest earnings reports",
                stage_type=StageType.tool_call,
                outputs=["reports"],
                config={
                    "tool_id": "web_search",
                    "section_label": "research",
                    "test_expectations": ["reports output is populated"],
                    "test_intent": "executor_smoke",
                    "failure_risk": "high",
                    "expected_side_effects": ["network_io"],
                },
            ),
            StageIntent(
                name="draft",
                description="Draft the weekly briefing",
                stage_type=StageType.transform,
                inputs=["reports"],
                outputs=["briefing"],
                config={"chapter_label": "report"},
            ),
        ],
        global_inputs=["watchlist"],
        global_outputs=["briefing"],
    )

    spec = workflow_spec_from_intent(
        intent,
        workflow_id="earnings-brief",
        candidate_workspace_id="candidate-1",
        schedule=ScheduleIntent(trigger="weekly"),
    )

    assert spec.workflow_id == "earnings-brief"
    assert spec.candidate_workspace_id == "candidate-1"
    assert spec.schedule is not None
    assert spec.schedule.trigger == "weekly"
    assert [node.node_id for node in spec.nodes] == ["collect", "draft"]
    assert spec.nodes[0].node_type == "tool_operator"
    assert spec.nodes[0].grounding.tool_id == "web_search"
    assert spec.nodes[0].section_label == "research"
    assert spec.nodes[0].config["tool_id"] == "web_search"
    assert spec.nodes[0].worker_hints == NodeWorkerHints(
        test_intent="executor_smoke",
        failure_risk=WorkflowRiskLevel.high,
        expected_side_effects=["network_io"],
    )
    assert spec.nodes[1].node_type == "llm_operator"
    assert spec.nodes[1].dependencies == ["collect"]
    assert spec.nodes[1].chapter_label == "report"
    assert [artifact.name for artifact in spec.expected_inputs] == ["watchlist"]
    assert [artifact.name for artifact in spec.expected_outputs] == ["briefing"]


def test_workflow_spec_from_intent_preserves_grounded_llm_tool_ids() -> None:
    intent = WorkflowIntent(
        goal="Research and summarize recent filings",
        stages=[
            StageIntent(
                name="research_summary",
                description="Search recent filings and summarize them",
                stage_type=StageType.transform,
                outputs=["briefing"],
                config={
                    "tool_id": "web_search",
                    "declared_actions": ["search"],
                },
            ),
        ],
        global_outputs=["briefing"],
    )

    spec = workflow_spec_from_intent(intent, workflow_id="grounded-llm")

    assert spec.nodes[0].node_type == "llm_operator"
    assert spec.nodes[0].grounding.tool_id == "web_search"
    assert spec.nodes[0].grounding.declared_actions == ["search"]


def test_workflow_spec_from_intent_does_not_mark_plain_drafting_as_external_io() -> None:
    intent = WorkflowIntent(
        goal="Draft a digest from notes",
        stages=[
            StageIntent(
                name="draft_digest",
                description="Write a digest from the notes",
                stage_type=StageType.transform,
                inputs=["notes"],
                outputs=["digest"],
            ),
        ],
        global_inputs=["notes"],
        global_outputs=["digest"],
    )

    spec = workflow_spec_from_intent(intent, workflow_id="plain-draft")

    assert spec.nodes[0].node_type == "llm_operator"
    assert spec.nodes[0].grounding.tool_id is None
    assert spec.nodes[0].grounding.declared_actions == []


def test_workflow_spec_from_intent_prefers_worker_compute_nodes_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_WORKER_GENERATION", "enabled")
    intent = WorkflowIntent(
        goal="Search, summarize, and compute",
        stages=[
            StageIntent(
                name="search",
                description="Search the web for earnings commentary",
                stage_type=StageType.tool_call,
                config={"tool_id": "web_search"},
            ),
            StageIntent(
                name="summarize",
                description="Summarize the findings",
                stage_type=StageType.transform,
            ),
            StageIntent(
                name="compute",
                description="Compute the aggregate score",
                stage_type=StageType.code_execution,
                config={"code": "result = 2 + 2"},
            ),
        ],
    )

    spec = workflow_spec_from_intent(intent, workflow_id="worker-generate")

    assert [node.node_type for node in spec.nodes] == ["worker", "worker", "worker"]
    assert [node.execution_family for node in spec.nodes] == [
        ExecutionFamily.tool,
        ExecutionFamily.llm,
        ExecutionFamily.code,
    ]
    assert spec.nodes[0].config["tool_ids"] == ["web_search"]
    assert spec.nodes[1].config["role"] == "processor"
    assert spec.nodes[1].config["llm_hints"]["prompt_template"] == "Summarize the findings"
    assert spec.nodes[2].grounding.operation_type == "code_execution"


def test_infer_execution_family_handles_worker_configs() -> None:
    assert infer_execution_family("worker", config={"model": "gpt-5-mini"}) == ExecutionFamily.llm
    assert infer_execution_family("worker", config={"tool_ids": ["web_search"]}) == ExecutionFamily.tool
    assert infer_execution_family("worker", config={"code": "result = 2 + 2"}) == ExecutionFamily.code
