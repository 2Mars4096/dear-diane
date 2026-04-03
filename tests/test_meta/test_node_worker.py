from __future__ import annotations

import pytest

from dan.meta.workflow_spec import ExecutionFamily, NodeGrounding, WorkflowSpec, WorkflowSpecNode
from dan.server.agent_runtime.node_worker import (
    NodeExecutorKind,
    NodeWorkerRuntimeConfig,
    build_node_plan,
    build_node_plans,
)


def _spec_node(
    node_id: str,
    node_type: str,
    execution_family: ExecutionFamily,
    *,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
    dependencies: list[str] | None = None,
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
        grounding=grounding or NodeGrounding(),
        config=config or {},
        test_expectations=[f"{node_id} completes"],
    )


def test_node_worker_builds_tool_plan_with_reachable_inputs() -> None:
    spec = WorkflowSpec(
        goal="Fetch a page and summarize it",
        global_inputs=["url"],
        nodes=[
            _spec_node(
                "fetch_page",
                "tool_operator",
                ExecutionFamily.tool,
                inputs=["url"],
                outputs=["page_text"],
                grounding=NodeGrounding(tool_id="web_fetch"),
            )
        ],
    )

    result = build_node_plans(spec)[0]

    assert result.accepted is True
    assert result.plan is not None
    assert result.plan.executor_kind == NodeExecutorKind.tool
    assert result.plan.executor_config["tool_id"] == "web_fetch"
    assert [port.name for port in result.plan.input_ports] == ["url"]
    assert [port.name for port in result.plan.output_ports] == ["page_text"]
    assert result.plan.input_bindings[0].source_kind.value == "global_input"
    assert result.plan.test_contracts[0].kind == "contract_validation"
    assert result.plan.metadata["worker_pool_cap"] >= 1


def test_node_worker_builds_dependency_binding_for_downstream_node() -> None:
    spec = WorkflowSpec(
        goal="Fetch and summarize a page",
        global_inputs=["url"],
        nodes=[
            _spec_node(
                "fetch_page",
                "tool_operator",
                ExecutionFamily.tool,
                inputs=["url"],
                outputs=["page_text"],
                grounding=NodeGrounding(tool_id="web_fetch"),
            ),
            _spec_node(
                "summarize",
                "llm_operator",
                ExecutionFamily.llm,
                inputs=["page_text"],
                outputs=["briefing"],
                dependencies=["fetch_page"],
                grounding=NodeGrounding(declared_actions=[]),
            ),
        ],
    )

    result = build_node_plan(spec, spec.nodes[1], node_lookup={node.node_id: node for node in spec.nodes})

    assert result.accepted is True
    assert result.plan is not None
    assert result.plan.executor_kind == NodeExecutorKind.llm
    assert result.plan.input_bindings[0].source_kind.value == "dependency_output"
    assert result.plan.input_bindings[0].source_node_id == "fetch_page"
    assert result.plan.input_bindings[0].source_port == "page_text"


def test_node_worker_prefers_dependency_binding_over_same_named_global_input() -> None:
    spec = WorkflowSpec(
        goal="Fetch then normalize a URL payload",
        global_inputs=["url"],
        nodes=[
            _spec_node(
                "resolve_url",
                "tool_operator",
                ExecutionFamily.tool,
                inputs=["url"],
                outputs=["url"],
                grounding=NodeGrounding(tool_id="web_fetch"),
            ),
            _spec_node(
                "normalize_url",
                "code_operator",
                ExecutionFamily.code,
                inputs=["url"],
                outputs=["normalized_url"],
                dependencies=["resolve_url"],
                grounding=NodeGrounding(operation_type="transform"),
                config={"code": "result = {'normalized_url': url}"},
            ),
        ],
    )

    result = build_node_plan(spec, spec.nodes[1], node_lookup={node.node_id: node for node in spec.nodes})

    assert result.accepted is True
    assert result.plan is not None
    assert result.plan.input_bindings[0].source_kind.value == "dependency_output"
    assert result.plan.input_bindings[0].source_node_id == "resolve_url"
    assert result.plan.input_bindings[0].source_port == "url"


def test_node_worker_infers_single_dependency_single_output_binding_when_names_differ() -> None:
    spec = WorkflowSpec(
        goal="Fetch then normalize records",
        nodes=[
            _spec_node(
                "fetch_records",
                "tool_operator",
                ExecutionFamily.tool,
                outputs=["result"],
                grounding=NodeGrounding(tool_id="web_fetch"),
            ),
            _spec_node(
                "normalize_records",
                "code_operator",
                ExecutionFamily.code,
                inputs=["records"],
                outputs=["normalized_records"],
                dependencies=["fetch_records"],
                grounding=NodeGrounding(operation_type="transform"),
                config={"code": "result = {'normalized_records': records}"},
            ),
        ],
    )

    result = build_node_plan(spec, spec.nodes[1], node_lookup={node.node_id: node for node in spec.nodes})

    assert result.accepted is True
    assert result.plan is not None
    assert result.plan.input_bindings[0].source_kind.value == "dependency_output"
    assert result.plan.input_bindings[0].source_node_id == "fetch_records"
    assert result.plan.input_bindings[0].source_port == "result"


def test_node_worker_rejects_unknown_tool() -> None:
    spec = WorkflowSpec(
        goal="Use a made-up tool",
        nodes=[
            _spec_node(
                "bad_tool",
                "tool_operator",
                ExecutionFamily.tool,
                grounding=NodeGrounding(tool_id="totally_missing_tool"),
            )
        ],
    )

    result = build_node_plans(spec)[0]

    assert result.accepted is False
    assert any(issue.code == "unknown_tool" for issue in result.issues)


def test_node_worker_rejects_code_node_without_code_and_derives_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DAN_STRUCTURED_NODE_WORKER_TIMEOUT", raising=False)
    monkeypatch.setenv("DAN_MAX_GENERATION_SECONDS", "90")

    spec = WorkflowSpec(
        goal="Transform records",
        nodes=[
            _spec_node(
                "transform",
                "code_operator",
                ExecutionFamily.code,
                inputs=["records"],
                outputs=["normalized_records"],
                grounding=NodeGrounding(operation_type="transform"),
                config={},
            )
        ],
    )

    runtime_config = NodeWorkerRuntimeConfig.from_env(expected_node_count=3)
    result = build_node_plans(spec, runtime_config=runtime_config)[0]

    assert runtime_config.node_worker_timeout_seconds == pytest.approx(30.0)
    assert result.accepted is False
    assert any(issue.code == "missing_executable_code" for issue in result.issues)


def test_node_worker_classifies_gate_as_control_flow() -> None:
    spec = WorkflowSpec(
        goal="Route based on a condition",
        nodes=[
            _spec_node(
                "gate",
                "gate",
                ExecutionFamily.control_flow,
                inputs=["payload"],
                outputs=["accepted", "rejected"],
                config={"condition": "payload.ok == true"},
            )
        ],
    )

    result = build_node_plans(spec)[0]

    assert result.accepted is True
    assert result.plan is not None
    assert result.plan.executor_kind == NodeExecutorKind.control_flow


def test_node_worker_builds_worker_tool_plan_with_reachable_inputs() -> None:
    spec = WorkflowSpec(
        goal="Fetch a page through a Worker",
        global_inputs=["url"],
        nodes=[
            _spec_node(
                "fetch_page",
                "worker",
                ExecutionFamily.tool,
                inputs=["url"],
                outputs=["result"],
                grounding=NodeGrounding(tool_id="web_fetch"),
                config={
                    "role": "tool_runner",
                    "tool_ids": ["web_fetch"],
                    "tool_config": {"url": "https://example.com"},
                },
            )
        ],
    )

    result = build_node_plans(spec)[0]

    assert result.accepted is True
    assert result.plan is not None
    assert result.plan.executor_kind == NodeExecutorKind.tool
    assert result.plan.node_type == "worker"
    assert result.plan.executor_config["tool_id"] == "web_fetch"
    assert result.plan.executor_config["tool_ids"] == ["web_fetch"]


def test_node_worker_builds_worker_llm_plan_with_text_output() -> None:
    spec = WorkflowSpec(
        goal="Summarize through a Worker",
        global_inputs=["page_text"],
        nodes=[
            _spec_node(
                "summarize",
                "worker",
                ExecutionFamily.llm,
                inputs=["page_text"],
                grounding=NodeGrounding(),
                config={
                    "role": "processor",
                    "model": "gpt-5-mini",
                    "llm_hints": {"prompt_template": "Summarize {input}"},
                },
            )
        ],
    )

    result = build_node_plans(spec)[0]

    assert result.accepted is True
    assert result.plan is not None
    assert result.plan.executor_kind == NodeExecutorKind.llm
    assert [port.name for port in result.plan.output_ports] == ["text"]
    assert result.plan.executor_config["prompt_template"] == "Summarize {input}"
