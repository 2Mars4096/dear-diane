from tests.eval import EvalRecord, GraphSummary, PromptFixture, ValidationResult
from tests.eval.judge import _parse_scores
from tests.eval.report import ReportGenerator
from tests.eval.runner import _build_graph_summary, _check_expectations
from dan.meta.structural_mutations import wrap_in_review_loop


def test_build_graph_summary_uses_runtime_schema_aliases():
    graph = {
        "nodes": [
            {"id": "writer", "node_type": "llm_operator"},
            {"id": "review", "node_type": "while_loop"},
            {"id": "search", "node_type": "tool_operator"},
            {"id": "calc", "node_type": "code_operator"},
        ],
        "edges": [
            {
                "id": "e1",
                "source_node_id": "writer",
                "source_port": "text",
                "target_node_id": "review",
                "target_port": "draft",
                "edge_type": "data",
            },
            {
                "id": "e2",
                "source_node_id": "review",
                "source_port": "draft",
                "target_node_id": "search",
                "target_port": "query",
                "edge_type": "data",
            },
        ],
    }

    summary = _build_graph_summary(graph)

    assert summary.node_count == 4
    assert summary.edge_count == 2
    assert summary.has_loop is True
    assert "llm" in summary.node_types
    assert "gate" in summary.node_types
    assert "tool" in summary.node_types
    assert "code" in summary.node_types


def test_build_graph_summary_classifies_unified_gate_modes():
    loop_graph = {
        "nodes": [{"id": "loop", "node_type": "gate", "config": {"gate_mode": "while"}}],
        "edges": [],
    }
    conditional_graph = {
        "nodes": [{"id": "cond", "node_type": "gate", "config": {"gate_mode": "if_else"}}],
        "edges": [],
    }

    loop_summary = _build_graph_summary(loop_graph)
    conditional_summary = _build_graph_summary(conditional_graph)

    assert loop_summary.has_loop is True
    assert loop_summary.has_review_loop is True
    assert loop_summary.has_conditional is False
    assert conditional_summary.has_loop is False
    assert conditional_summary.has_review_loop is False
    assert conditional_summary.has_conditional is True


def test_build_graph_summary_counts_nested_review_loops():
    graph = {
        "nodes": [
            {"id": "loop_a", "node_type": "while_loop"},
            {"id": "loop_b", "node_type": "gate", "config": {"gate_mode": "while"}},
        ],
        "edges": [],
    }

    summary = _build_graph_summary(graph)

    assert summary.has_review_loop is True
    assert summary.review_loop_count == 2


def test_check_expectations_accepts_runtime_aliases():
    fixture = PromptFixture(
        id="p02",
        tier="T1",
        lane="build",
        prompt="Create a review loop",
        expected={
            "min_nodes": 2,
            "node_types": ["llm", "gate"],
            "topology": ["review_loop"],
        },
    )
    summary = GraphSummary(
        node_count=2,
        node_types=["gate", "llm", "while_loop"],
        edge_count=1,
        has_loop=True,
        has_review_loop=True,
        has_fan_out=False,
    )

    assert _check_expectations(fixture, summary) == []


def test_check_expectations_flags_missing_supported_topology():
    fixture = PromptFixture(
        id="p05",
        tier="T3",
        lane="build",
        prompt="fan out and merge",
        expected={"topology": ["fan_out", "code"]},
    )
    summary = GraphSummary(
        node_count=3,
        node_types=["llm"],
        edge_count=2,
        has_loop=False,
        has_fan_out=False,
    )

    assert _check_expectations(fixture, summary) == [
        "expected topology 'fan_out' not satisfied",
        "expected topology 'code' not satisfied",
    ]


def test_review_loop_topology_requires_while_loop_not_foreach():
    fixture = PromptFixture(
        id="p02",
        tier="T1",
        lane="build",
        prompt="Create a review loop",
        expected={"topology": ["review_loop"]},
    )
    summary = GraphSummary(
        node_count=2,
        node_types=["for_each", "llm"],
        edge_count=1,
        has_loop=True,
        has_review_loop=False,
        has_fan_out=True,
    )

    assert _check_expectations(fixture, summary) == [
        "expected topology 'review_loop' not satisfied",
    ]


def test_nested_review_loops_requires_two_loops():
    fixture = PromptFixture(
        id="p07",
        tier="T4",
        lane="build",
        prompt="parallel teams with review loops",
        expected={"topology": ["nested_review_loops"]},
    )
    summary = GraphSummary(
        node_count=8,
        node_types=["gate", "llm", "while_loop"],
        edge_count=10,
        has_loop=True,
        has_review_loop=True,
        review_loop_count=1,
    )

    assert _check_expectations(fixture, summary) == [
        "expected topology 'nested_review_loops' not satisfied",
    ]


def test_wrap_in_review_loop_adds_feedback_control_edge():
    graph = {
        "nodes": [
            {
                "id": "draft",
                "node_type": "llm_operator",
                "config": {"name": "draft", "prompt_template": "Write the draft"},
            },
            {"id": "save", "node_type": "tool_operator", "config": {"name": "save"}},
        ],
        "edges": {
            "data": [
                {
                    "source_node_id": "draft",
                    "source_port": "text",
                    "target_node_id": "save",
                    "target_port": "input",
                },
            ],
            "control": [],
            "context": [],
        },
    }

    result = wrap_in_review_loop(graph, "draft")

    assert result.success is True
    control_edges = graph["edges"]["control"]
    data_edges = graph["edges"]["data"]
    review_gate = next(node for node in graph["nodes"] if node["id"].startswith("review_gate_"))
    reviewer = next(node for node in graph["nodes"] if node["id"].startswith("reviewer_"))

    assert any(
        edge["source_node_id"] == review_gate["id"]
        and edge["source_port"] == "continue"
        and edge["target_node_id"] == "draft"
        for edge in control_edges
    )
    assert any(
        edge["source_node_id"] == review_gate["id"]
        and edge["source_port"] == "done"
        and edge["target_node_id"] == "save"
        for edge in data_edges
    )
    assert any(
        edge["source_node_id"] == "draft"
        and edge["target_node_id"] == reviewer["id"]
        for edge in data_edges
    )
    assert any(
        edge["source_node_id"] == reviewer["id"]
        and edge["source_port"] == "quality_score"
        and edge["target_node_id"] == review_gate["id"]
        for edge in data_edges
    )
    assert any(
        edge["source_node_id"] == reviewer["id"]
        and edge["source_port"] == "feedback"
        and edge["target_node_id"] == "draft"
        and edge["target_port"] == "feedback"
        for edge in data_edges
    )
    assert "{feedback}" in graph["nodes"][0]["config"]["prompt_template"]


def test_parse_scores_extracts_and_clamps_json():
    text = (
        "Here is the result:\n"
        '{"prompt_faithfulness": 11, "node_specificity": 8, '
        '"data_flow_correctness": -2, "executability": 6}'
    )

    assert _parse_scores(text) == {
        "prompt_faithfulness": 10,
        "node_specificity": 8,
        "data_flow_correctness": 0,
        "executability": 6,
    }


def test_report_includes_lowest_scoring_judged_graphs():
    records = [
        EvalRecord(
            id="good",
            tier="T1",
            lane="build",
            prompt="good",
            judge_scores={
                "prompt_faithfulness": 9,
                "node_specificity": 8,
                "data_flow_correctness": 8,
                "executability": 8,
            },
        ),
        EvalRecord(
            id="bad",
            tier="T4",
            lane="build",
            prompt="bad",
            judge_scores={
                "prompt_faithfulness": 2,
                "node_specificity": 3,
                "data_flow_correctness": 4,
                "executability": 2,
            },
        ),
    ]

    summary = ReportGenerator(records).summary()
    worst = summary["judge_worst_examples"]

    assert worst[0]["id"] == "bad"
    assert worst[0]["avg_score"] < worst[1]["avg_score"]


def test_report_summarizes_workflow_contract_variants_and_guidance():
    records = [
        EvalRecord(
            id="enabled-pass",
            tier="CONTRACT",
            lane="build",
            prompt="enabled",
            status="passed",
            workflow_contract_variant="enabled",
            audit_found=True,
            workflow_guidance_injected=True,
            workflow_guidance_surface="build",
        ),
        EvalRecord(
            id="enabled-fail",
            tier="CONTRACT",
            lane="build",
            prompt="enabled-fail",
            status="failed",
            workflow_contract_variant="enabled",
            audit_found=True,
            workflow_guidance_injected=False,
            workflow_guidance_surface="",
        ),
        EvalRecord(
            id="disabled-fail",
            tier="CONTRACT",
            lane="build",
            prompt="disabled",
            status="failed",
            workflow_contract_variant="disabled",
            audit_found=True,
            workflow_guidance_injected=False,
            workflow_guidance_surface="",
        ),
    ]

    summary = ReportGenerator(records).summary()

    enabled = summary["workflow_contract_variants"]["enabled"]
    disabled = summary["workflow_contract_variants"]["disabled"]
    guidance = summary["workflow_guidance_summary"]
    correlation = summary["workflow_guidance_correlation"]

    assert enabled["total"] == 2
    assert enabled["passed"] == 1
    assert disabled["total"] == 1
    assert guidance["with_audit"] == 3
    assert guidance["with_guidance"] == 1
    assert guidance["by_surface"]["build"] == 1
    assert correlation["injected"]["total"] == 1
    assert correlation["injected"]["pass_rate"] == 1.0
    assert correlation["not_injected"]["total"] == 2


def test_report_workflow_success_metrics_cover_validity_repair_overclaim_and_leakage():
    records = [
        EvalRecord(
            id="repair-follow1",
            tier="CONTRACT",
            lane="build",
            prompt="Repair the workflow so it becomes runnable.",
            status="failed",
            failure_mode="validation_error",
            graph_created=True,
            validation=ValidationResult(passed=False, errors=["bad"], run_ready=False),
            workflow_contract_variant="enabled",
            audit_found=True,
            workflow_guidance_injected=True,
            workflow_guidance_surface="repair",
            response_text="The workflow is runnable now.",
        ),
        EvalRecord(
            id="build-pass",
            tier="CONTRACT",
            lane="build",
            prompt="Build a runnable workflow.",
            status="passed",
            graph_created=True,
            validation=ValidationResult(passed=True, errors=[], run_ready=True),
            workflow_contract_variant="enabled",
            audit_found=True,
            workflow_guidance_injected=True,
            workflow_guidance_surface="build",
        ),
        EvalRecord(
            id="ask-leak",
            tier="T5",
            lane="agent",
            prompt="What is the weather in Hong Kong today?",
            status="passed",
            workflow_contract_variant="enabled",
            audit_found=True,
            workflow_guidance_injected=True,
            workflow_guidance_surface="build",
        ),
    ]

    summary = ReportGenerator(records).summary()
    metrics = summary["workflow_success_metrics"]

    assert metrics["validated_total"] == 2
    assert metrics["structural_validity_rate"] == 0.5
    assert metrics["run_ready_rate"] == 0.5
    assert metrics["repair_turns_total"] == 1
    assert metrics["repair_sequences"] == 1
    assert metrics["false_confidence_count"] == 1
    assert metrics["workflow_guidance_leakage_count"] == 1
    assert metrics["workflow_guidance_leakage_rate"] == 1.0
