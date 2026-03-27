from __future__ import annotations

from dan.builder import workflow
from dan.meta.workflow_contract import (
    classify_run_readiness_issues,
    validate_workflow_build_contract,
    workflow_build_provenance,
    workflow_build_summary,
)


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


def _build_colliding_node_id_graph_dict(*, ambiguous_ports: bool = False) -> dict:
    wf = workflow("duplicate_id_contract")
    source = wf.llm(
        "source",
        prompt="Draft {topic}",
        input_ports=[{"name": "topic", "json_schema": {"type": "string"}}],
        output_ports=[{"name": "headline", "json_schema": {"type": "string"}}],
    )
    review = wf.llm(
        "review",
        prompt="Review {headline}",
        input_ports=[{"name": "headline", "json_schema": {"type": "string"}}],
        output_ports=[{"name": "draft", "json_schema": {"type": "string"}}],
    )
    publish = wf.llm(
        "publish",
        prompt="Publish {headline}" if ambiguous_ports else "Publish {draft}",
        input_ports=[
            {
                "name": "headline" if ambiguous_ports else "draft",
                "json_schema": {"type": "string"},
            }
        ],
        output_ports=[
            {
                "name": "draft" if ambiguous_ports else "article",
                "json_schema": {"type": "string"},
            }
        ],
    )
    wf.edge(source["headline"], review["headline"])
    wf.edge(review["draft"], publish["headline" if ambiguous_ports else "draft"])
    graph_dict = wf.build().model_dump(mode="json")
    graph_dict["nodes"][2]["id"] = "review"
    graph_dict["edges"][1]["target_node_id"] = "review"
    graph_dict["exit_points"] = ["review"]
    return graph_dict


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
    provenance = workflow_build_provenance(report)
    assert provenance["build_status"] == "run_ready"
    assert provenance["auto_fix_count"] >= 1
    assert provenance["build_summary"] == workflow_build_summary(report)


def test_workflow_contract_normalizes_legacy_edge_fields() -> None:
    graph_dict = _build_single_port_chain_dict()
    edge = graph_dict["edges"][0]
    edge["from"] = edge.pop("source_node_id")
    edge["to"] = edge.pop("target_node_id")
    edge["sourcePort"] = edge.pop("source_port")
    edge["targetPort"] = edge.pop("target_port")
    edge.pop("edge_type")
    edge.pop("id")

    report = validate_workflow_build_contract(
        graph_dict,
        workflow_id="legacy-edge-shape",
        apply_repairs=True,
    )

    assert report.validated is True
    assert report.run_ready is True
    assert report.graph_dict is not None
    assert report.graph_dict["edges"][0]["edge_type"] == "data"
    assert report.graph_dict["edges"][0]["source_node_id"] == "source"
    assert report.graph_dict["edges"][0]["target_node_id"] == "sink"
    assert report.graph_dict["edges"][0]["source_port"] == "payload"
    assert report.graph_dict["edges"][0]["target_port"] == "payload"
    assert report.graph_dict["edges"][0]["id"] == "source.payload->sink.payload"


def test_workflow_contract_infers_single_ports_for_legacy_edges() -> None:
    graph_dict = _build_single_port_chain_dict()
    edge = graph_dict["edges"][0]
    edge["source"] = edge.pop("source_node_id")
    edge["target"] = edge.pop("target_node_id")
    edge.pop("source_port")
    edge.pop("target_port")
    edge.pop("id")

    report = validate_workflow_build_contract(
        graph_dict,
        workflow_id="legacy-edge-single-port",
        apply_repairs=True,
    )

    assert report.validated is True
    assert report.run_ready is True
    assert report.graph_dict is not None
    assert report.graph_dict["edges"][0]["source_port"] == "payload"
    assert report.graph_dict["edges"][0]["target_port"] == "payload"
    assert report.graph_dict["edges"][0]["id"] == "source.payload->sink.payload"


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
    provenance = workflow_build_provenance(report)
    assert provenance["build_status"] == "validated"
    assert provenance["failure_bucket"] == "semantic_reprompt_or_diagnosis"
    assert provenance["handoff_reason"] == "run_readiness_gap"


def test_workflow_contract_rejects_placeholder_code_nodes_as_not_run_ready() -> None:
    wf = workflow("placeholder_contract")
    wf.code(
        "compute",
        code='result = {"status": "placeholder", "task": "compute metrics"}',
    )
    graph_dict = wf.build().model_dump(mode="json")

    report = validate_workflow_build_contract(
        graph_dict,
        workflow_id="placeholder-contract",
        apply_repairs=True,
    )

    assert report.validated is True
    assert report.run_ready is False
    assert any("placeholder" in issue.lower() for issue in report.run_readiness_issues)


def test_workflow_contract_rejects_foreach_without_iterable_source() -> None:
    wf = workflow("ungrounded_foreach")
    with wf.for_each("process_items") as body:
        body.llm("worker", prompt="Process each item")
    graph_dict = wf.build().model_dump(mode="json")

    report = validate_workflow_build_contract(
        graph_dict,
        workflow_id="ungrounded-foreach",
        apply_repairs=True,
    )

    assert report.validated is True
    assert report.run_ready is False
    assert any(
        "for-each node 'process_items' has no iterable input source" in issue.lower()
        for issue in report.run_readiness_issues
    )


def test_workflow_contract_rejects_toolless_llm_external_action_prompts() -> None:
    wf = workflow("toolless_external_action")
    wf.llm(
        "save_report",
        prompt="Save the final report to dated file paths for archival and access",
    )
    graph_dict = wf.build().model_dump(mode="json")

    report = validate_workflow_build_contract(
        graph_dict,
        workflow_id="toolless-external-action",
        apply_repairs=True,
    )

    assert report.validated is True
    assert report.run_ready is False
    assert any(
        "llm node 'save_report' has no tools" in issue.lower()
        for issue in report.run_readiness_issues
    )


def test_workflow_contract_dedupes_obvious_colliding_node_ids() -> None:
    graph_dict = _build_colliding_node_id_graph_dict()

    report = validate_workflow_build_contract(
        graph_dict,
        workflow_id="duplicate-node-ids",
        apply_repairs=True,
    )

    assert report.validated is True
    assert report.run_ready is True
    assert report.graph_dict is not None
    assert [node["id"] for node in report.graph_dict["nodes"]] == [
        "source",
        "review",
        "review-2",
    ]
    assert report.graph_dict["edges"][0]["target_node_id"] == "review"
    assert report.graph_dict["edges"][1]["source_node_id"] == "review"
    assert report.graph_dict["edges"][1]["target_node_id"] == "review-2"
    assert report.graph_dict["exit_points"] == ["review-2"]
    assert any(
        "Deduped colliding node id 'review'" in fix
        for fix in report.auto_fixes_applied
    )


def test_workflow_contract_keeps_ambiguous_colliding_node_ids_fatal() -> None:
    graph_dict = _build_colliding_node_id_graph_dict(ambiguous_ports=True)

    report = validate_workflow_build_contract(
        graph_dict,
        workflow_id="duplicate-node-ids",
        apply_repairs=True,
    )

    assert report.validated is False
    assert report.run_ready is False
    assert any(
        issue.category == "node_identity"
        and issue.severity == "fatal"
        and issue.artifact_id == "review"
        for issue in report.errors
    )
    assert report.graph_dict is not None
    assert [node["id"] for node in report.graph_dict["nodes"]].count("review") == 2
    assert not any(
        "Deduped colliding node id 'review'" in fix
        for fix in report.auto_fixes_applied
    )
    provenance = workflow_build_provenance(report)
    assert provenance["build_status"] == "invalid"
    assert provenance["failure_bucket"] == "hard_fail"
    assert provenance["handoff_reason"] == "duplicate_node_id"


def test_workflow_contract_classifies_repairable_errors_without_repairs_as_mechanical() -> None:
    graph_dict = _build_single_port_chain_dict()
    graph_dict["entry_points"] = ["missing-node"]

    report = validate_workflow_build_contract(
        graph_dict,
        workflow_id="repairable-contract-error",
        apply_repairs=False,
    )

    provenance = workflow_build_provenance(report)
    assert provenance["build_status"] == "invalid"
    assert provenance["failure_bucket"] == "mechanical_auto_fix"
    assert provenance["handoff_reason"] == "repairable_contract_error"


def test_classify_run_readiness_issues_distinguishes_code_specific_failures() -> None:
    assert classify_run_readiness_issues(
        ["Code node 'compute' has empty code, so it is not run-ready."]
    ) == "unresolved_code"
    assert classify_run_readiness_issues(
        ["Code node 'compute' contains placeholder status payload code instead of runnable logic."]
    ) == "non_runnable_code"
    assert classify_run_readiness_issues(
        ["Workflow has no entry points, so it is not run-ready."]
    ) == "not_run_ready"
