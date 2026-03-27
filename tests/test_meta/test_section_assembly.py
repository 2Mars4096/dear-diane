from __future__ import annotations

from dan.meta.workflow_spec import (
    ExecutionFamily,
    NodeGrounding,
    StructuredSectioningConfig,
    WorkflowSection,
    WorkflowSpec,
    WorkflowSpecNode,
)
from dan.server.agent_runtime.node_worker import NodePlan, build_node_plans
from dan.server.agent_runtime.section_assembly import SectionAssemblyConfig, assemble_section, assemble_sections
from dan.server.agent_runtime.workflow_boundary_linking import (
    BoundaryDiagnosticKind,
    link_assembled_sections,
    link_sections_into_candidate_graph,
)
from dan.server.agent_runtime.workflow_sectioning import partition_workflow_spec


def _node(
    node_id: str,
    node_type: str,
    execution_family: ExecutionFamily,
    *,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
    dependencies: list[str] | None = None,
    chapter_label: str | None = None,
    grounding: NodeGrounding | None = None,
    config: dict | None = None,
) -> WorkflowSpecNode:
    return WorkflowSpecNode(
        node_id=node_id,
        purpose=f"Purpose for {node_id}",
        node_type=node_type,
        execution_family=execution_family,
        inputs=inputs or [],
        outputs=outputs or [],
        dependencies=dependencies or [],
        chapter_label=chapter_label,
        grounding=grounding or NodeGrounding(),
        config=config or {},
        test_expectations=[f"{node_id} completes"],
    )


def _accepted_plans(spec: WorkflowSpec) -> list[NodePlan]:
    return [result.plan for result in build_node_plans(spec) if result.plan is not None]


def _sections(spec: WorkflowSpec) -> list[WorkflowSection]:
    return partition_workflow_spec(
        spec,
        StructuredSectioningConfig(max_section_size=10, min_section_size=1, cross_section_penalty=1.0),
    )


def test_section_assembly_builds_internal_edges_and_boundary_ports() -> None:
    spec = WorkflowSpec(
        goal="Fetch, summarize, and publish",
        global_inputs=["url"],
        global_outputs=["delivery_status"],
        nodes=[
            _node(
                "fetch_page",
                "tool_operator",
                ExecutionFamily.tool,
                inputs=["url"],
                outputs=["page_text"],
                chapter_label="research",
                grounding=NodeGrounding(tool_id="web_fetch"),
            ),
            _node(
                "summarize",
                "llm_operator",
                ExecutionFamily.llm,
                inputs=["page_text"],
                outputs=["briefing"],
                dependencies=["fetch_page"],
                chapter_label="research",
            ),
            _node(
                "publish",
                "code_operator",
                ExecutionFamily.code,
                inputs=["briefing"],
                outputs=["delivery_status"],
                dependencies=["summarize"],
                chapter_label="delivery",
                grounding=NodeGrounding(operation_type="publish"),
                config={"code": "result = {'status': 'delivered'}"},
            ),
        ],
    )
    sections = _sections(spec)

    artifacts = assemble_sections(spec, sections, _accepted_plans(spec))

    assert [artifact.acceptance for artifact in artifacts] == ["accepted", "accepted"]
    assert [edge.edge_id for edge in artifacts[0].internal_edges] == [
        f"{sections[0].section_id}:fetch_page.page_text->summarize.page_text"
    ]
    assert artifacts[0].entry_ports[0].port.name == "url"
    assert artifacts[0].exit_ports[0].port.name == "briefing"
    assert artifacts[0].exit_ports[0].target_section_ids == [sections[1].section_id]
    assert artifacts[1].entry_ports[0].source_section_id == sections[0].section_id
    assert artifacts[1].entry_ports[0].source_port == "briefing"


def test_section_assembly_repairs_single_port_drift() -> None:
    spec = WorkflowSpec(
        goal="Normalize a payload locally",
        nodes=[
            _node(
                "source",
                "tool_operator",
                ExecutionFamily.tool,
                outputs=["source_payload"],
                grounding=NodeGrounding(tool_id="web_fetch"),
            ),
            _node(
                "target",
                "llm_operator",
                ExecutionFamily.llm,
                inputs=["target_payload"],
                dependencies=["source"],
            ),
        ],
    )
    section = WorkflowSection(
        section_id="section-one",
        index=0,
        node_ids=["source", "target"],
        nodes=spec.nodes,
        estimated_weight=3.0,
    )
    source_plan = NodePlan(
        node_id="source",
        section_id="section-one",
        node_type="tool_operator",
        execution_family=ExecutionFamily.tool,
        executor_kind="tool",
        purpose="source",
        output_ports=[{"name": "result", "required": False}],
        input_ports=[],
        input_bindings=[],
        executor_config={"tool_id": "web_fetch", "tool_config": {}},
        grounding_checks=[],
        test_contracts=[],
        metadata={},
    )
    target_plan = NodePlan(
        node_id="target",
        section_id="section-one",
        node_type="llm_operator",
        execution_family=ExecutionFamily.llm,
        executor_kind="llm",
        purpose="target",
        input_ports=[{"name": "input", "required": True}],
        output_ports=[{"name": "summary", "required": False}],
        input_bindings=[
            {
                "input_port": "target_payload",
                "source_kind": "dependency_output",
                "source_node_id": "source",
                "source_port": "missing_payload",
            }
        ],
        executor_config={"model": "gpt-5-mini", "prompt_template": "Summarize"},
        grounding_checks=[],
        test_contracts=[],
        metadata={},
    )

    artifact = assemble_section(
        spec,
        section,
        [source_plan, target_plan],
        all_node_plans=[source_plan, target_plan],
        config=SectionAssemblyConfig(assembler_cap=1, repair_max=2),
    )

    assert artifact.acceptance == "accepted"
    assert artifact.internal_edges[0].source_port == "result"
    assert artifact.internal_edges[0].target_port == "input"
    assert [note.action for note in artifact.repair_notes] == [
        "remap_source_output",
        "remap_target_input",
    ]


def test_boundary_linker_stitches_adjacent_sections() -> None:
    spec = WorkflowSpec(
        goal="Summarize and publish",
        global_inputs=["url"],
        nodes=[
            _node(
                "fetch_page",
                "tool_operator",
                ExecutionFamily.tool,
                inputs=["url"],
                outputs=["page_text"],
                chapter_label="research",
                grounding=NodeGrounding(tool_id="web_fetch"),
            ),
            _node(
                "summarize",
                "llm_operator",
                ExecutionFamily.llm,
                inputs=["page_text"],
                outputs=["briefing"],
                dependencies=["fetch_page"],
                chapter_label="research",
            ),
            _node(
                "publish",
                "code_operator",
                ExecutionFamily.code,
                inputs=["briefing"],
                outputs=["delivery_status"],
                dependencies=["summarize"],
                chapter_label="delivery",
                grounding=NodeGrounding(operation_type="publish"),
                config={"code": "result = {'status': 'delivered'}"},
            ),
        ],
    )
    sections = _sections(spec)

    artifacts = assemble_sections(spec, sections, _accepted_plans(spec))
    linked = link_assembled_sections(artifacts)

    assert linked.accepted is True
    assert [link.link_id for link in linked.boundary_links] == [
        f"{sections[0].section_id}:summarize.briefing->{sections[1].section_id}:publish.briefing"
    ]
    assert linked.diagnostics == []


def test_boundary_linker_reports_missing_source_output() -> None:
    spec = WorkflowSpec(
        goal="Summarize and publish",
        global_inputs=["url"],
        nodes=[
            _node(
                "fetch_page",
                "tool_operator",
                ExecutionFamily.tool,
                inputs=["url"],
                outputs=["page_text"],
                chapter_label="research",
                grounding=NodeGrounding(tool_id="web_fetch"),
            ),
            _node(
                "summarize",
                "llm_operator",
                ExecutionFamily.llm,
                inputs=["page_text"],
                outputs=["briefing"],
                dependencies=["fetch_page"],
                chapter_label="research",
            ),
            _node(
                "publish",
                "code_operator",
                ExecutionFamily.code,
                inputs=["briefing"],
                outputs=["delivery_status"],
                dependencies=["summarize"],
                chapter_label="delivery",
                grounding=NodeGrounding(operation_type="publish"),
                config={"code": "result = {'status': 'delivered'}"},
            ),
        ],
    )
    sections = _sections(spec)
    artifacts = assemble_sections(spec, sections, _accepted_plans(spec))
    broken_delivery = artifacts[1].model_copy(deep=True)
    broken_delivery.entry_ports[0].source_port = "brief_payload"

    linked = link_assembled_sections([artifacts[0], broken_delivery])

    assert linked.accepted is False
    assert linked.diagnostics[0].kind in {
        BoundaryDiagnosticKind.missing_source_output,
        BoundaryDiagnosticKind.renamed_contract_field,
    }
    assert linked.diagnostics[0].suggestions


def test_boundary_linker_repairs_single_join_alias_drift_before_materialization() -> None:
    spec = WorkflowSpec(
        goal="Summarize and publish",
        global_inputs=["url"],
        nodes=[
            _node(
                "fetch_page",
                "tool_operator",
                ExecutionFamily.tool,
                inputs=["url"],
                outputs=["page_text"],
                chapter_label="research",
                grounding=NodeGrounding(tool_id="web_fetch"),
            ),
            _node(
                "summarize",
                "llm_operator",
                ExecutionFamily.llm,
                inputs=["page_text"],
                outputs=["briefing"],
                dependencies=["fetch_page"],
                chapter_label="research",
            ),
            _node(
                "publish",
                "code_operator",
                ExecutionFamily.code,
                inputs=["briefing"],
                outputs=["delivery_status"],
                dependencies=["summarize"],
                chapter_label="delivery",
                grounding=NodeGrounding(operation_type="publish"),
                config={"code": "result = {'status': 'delivered'}"},
            ),
        ],
    )
    sections = _sections(spec)
    artifacts = assemble_sections(spec, sections, _accepted_plans(spec))
    broken_delivery = artifacts[1].model_copy(deep=True)
    broken_delivery.entry_ports[0].source_port = "brief_payload"

    linked = link_sections_into_candidate_graph(
        spec,
        [artifacts[0], broken_delivery],
        workflow_id="wf-boundary-repair",
    )

    assert linked.status == "accepted"
    assert linked.candidate_graph is not None
    assert any(
        edge["source_node_id"] == "summarize"
        and edge["source_port"] == "briefing"
        and edge["target_node_id"] == "publish"
        and edge["target_port"] == "briefing"
        for edge in linked.candidate_graph["edges"]
    )
    assert any("Boundary repair attempt" in note for note in linked.repair_notes)
