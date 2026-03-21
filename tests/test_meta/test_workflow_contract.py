from __future__ import annotations

from dan.builder import workflow
from dan.meta.workflow_contract import validate_workflow_build_contract


def _build_single_port_chain_dict() -> dict:
    wf = workflow("contract_chain")
    source = wf.llm(
        "source",
        prompt="Hello {topic}",
        input_ports=[{"name": "topic", "json_schema": {"type": "string"}}],
        output_ports=[{"name": "payload", "json_schema": {"type": "string"}}],
    )
    sink = wf.llm(
        "sink",
        prompt="Expand {payload}",
        input_ports=[{"name": "payload", "json_schema": {"type": "string"}}],
        output_ports=[{"name": "text", "json_schema": {"type": "string"}}],
    )
    wf.edge(source["payload"], sink["payload"])
    return wf.build().model_dump(mode="json")


def test_workflow_contract_applies_safe_mechanical_repairs() -> None:
    graph_dict = _build_single_port_chain_dict()
    graph_dict["metadata"]["name"] = ""
    graph_dict["entry_points"] = []
    graph_dict["exit_points"] = []
    graph_dict["edges"][0]["source_port"] = "output"
    graph_dict["edges"][0]["target_port"] = "input"

    report = validate_workflow_build_contract(
        graph_dict,
        workflow_id=" Workflow Draft ",
        apply_repairs=True,
    )

    assert report.validated is True
    assert report.run_ready is True
    assert report.normalized_workflow_id == "workflow-draft"
    assert report.graph_dict is not None
    assert report.graph_dict["metadata"]["name"] == "Workflow Draft"
    assert report.graph_dict["entry_points"] == ["source"]
    assert report.graph_dict["exit_points"] == ["sink"]
    assert report.graph_dict["edges"][0]["source_port"] == "payload"
    assert report.graph_dict["edges"][0]["target_port"] == "payload"
    assert report.auto_fixes_applied


def test_workflow_contract_distinguishes_validated_from_run_ready() -> None:
    empty_graph = workflow("empty_contract").build().model_dump(mode="json")

    report = validate_workflow_build_contract(
        empty_graph,
        workflow_id="empty-contract",
        apply_repairs=True,
    )

    assert report.validated is True
    assert report.run_ready is False
    assert any("no nodes" in issue.lower() for issue in report.run_readiness_issues)
