from __future__ import annotations

from dan.builder import workflow
from dan.builder.decompiler import decompile


def test_builder_supports_input_node_with_aggregate_output() -> None:
    wf = workflow("input_parity")
    workflow_inputs = wf.input_node(
        "workflow_inputs",
        variables=[{"name": "topic", "type": "string", "description": "Research topic"}],
    )
    draft = wf.llm("draft", prompt="Draft about {topic}")
    wf.edge(workflow_inputs["topic"], draft["input"])

    graph = wf.build()
    input_node = graph.node_by_id("workflow_inputs")

    assert input_node is not None
    assert input_node.node_type == "input"
    assert {port.name for port in input_node.output_ports} == {"input", "topic"}

    code = decompile(graph)
    assert "wf.input_node('workflow_inputs'" in code
    assert "variables=[{'name': 'topic'" in code


def test_builder_supports_rag_node_and_decompiles_to_rag_api() -> None:
    wf = workflow("rag_parity")
    retriever = wf.rag(
        "retrieve",
        collection="papers",
        top_k=7,
        query_template="Find chunks for {query}",
        include_metadata=False,
        rerank=True,
    )

    graph = wf.build()
    node = graph.node_by_id("retrieve")

    assert node is not None
    assert node.node_type == "rag_operator"
    assert node.collection == "papers"
    assert node.top_k == 7
    assert node.query_template == "Find chunks for {query}"
    assert node.include_metadata is False
    assert node.rerank is True
    assert [port.name for port in node.input_ports] == ["query"]
    assert [port.name for port in node.output_ports] == ["chunks"]

    code = decompile(graph)
    assert "wf.rag('retrieve'" in code
    assert "collection='papers'" in code
    assert "top_k=7" in code
    assert "rerank=True" in code


def test_builder_supports_human_node_and_decompiles_to_human_api() -> None:
    wf = workflow("human_parity")
    reviewer = wf.human(
        "review",
        prompt="Review the draft",
        render_mode="approval",
        instructions="Approve or request changes",
        render_target="both",
    )

    graph = wf.build()
    node = graph.node_by_id("review")

    assert node is not None
    assert node.node_type == "human"
    assert [port.name for port in node.input_ports] == ["input"]
    assert [port.name for port in node.output_ports] == ["response"]

    code = decompile(graph)
    assert "wf.human('review'" in code
    assert "render_mode='approval'" in code
    assert "render_target='both'" in code


def test_builder_supports_human_alias_helpers() -> None:
    wf = workflow("human_aliases")
    approval = wf.approval("approve", prompt="Approve this draft")
    form = wf.form(
        "intake_form",
        prompt="Fill out the intake form",
        schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    )

    graph = wf.build()
    approval_node = graph.node_by_id("approve")
    form_node = graph.node_by_id("intake_form")

    assert approval_node is not None
    assert approval_node.node_type == "human"
    assert approval_node.render_mode == "approval"

    assert form_node is not None
    assert form_node.node_type == "human"
    assert form_node.render_mode == "form"
    assert form_node.output_schema is not None


def test_builder_supports_vote_node_and_decompiles_to_vote_api() -> None:
    wf = workflow("vote_parity")
    chooser = wf.vote(
        "choose_best",
        prompt="Pick the best answer",
        candidates=["claude-sonnet-4-6", "gpt-4o"],
        num_votes=2,
        strategy="judge",
    )

    graph = wf.build()
    node = graph.node_by_id("choose_best")

    assert node is not None
    assert node.node_type == "vote"
    assert [port.name for port in node.input_ports] == ["input"]
    assert [port.name for port in node.output_ports] == ["winner"]

    code = decompile(graph)
    assert "wf.vote('choose_best'" in code
    assert "candidates=['claude-sonnet-4-6', 'gpt-4o']" in code
    assert "strategy='judge'" in code


def test_builder_supports_ensemble_alias() -> None:
    wf = workflow("ensemble_alias")
    chooser = wf.ensemble(
        "choose_best",
        prompt="Pick the best answer",
        models=["claude-sonnet-4-6", "gpt-4o", "gemini-2.5-pro"],
    )

    graph = wf.build()
    node = graph.node_by_id("choose_best")

    assert node is not None
    assert node.node_type == "vote"
    assert node.vote_strategy == "judge"
    assert node.candidates == ["claude-sonnet-4-6", "gpt-4o", "gemini-2.5-pro"]
    assert node.num_votes == 3


def test_builder_supports_worker_control_flow_and_decompiles_to_worker_api() -> None:
    wf = workflow("worker_control_flow_parity")
    route = wf.worker(
        "route",
        control_flow={"condition": "score > 0.5", "gate_mode": "if_else"},
    )

    graph = wf.build()
    node = graph.node_by_id(route.node_id)

    assert node is not None
    assert node.node_type == "worker"
    assert node.control_flow is not None
    assert node.control_flow.condition == "score > 0.5"
    assert node.control_flow.gate_mode == "if_else"
    assert [port.name for port in node.output_ports] == ["true", "false"]

    code = decompile(graph)
    assert "wf.worker('route'" in code
    assert "control_flow={'condition': 'score > 0.5'" in code
    assert "'gate_mode': 'if_else'" in code
