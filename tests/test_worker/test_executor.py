from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
from dan.engine.executor import EngineConfig, ExecutionContext, NodeResult
from dan.engine.state import NodeStatus
from dan.engine.state import ExecutionState
from dan.executors.llm import LLMExecutor
from dan.executors.tool import ToolExecutor, ToolRegistry
from dan.models.context import MergeStrategy
from dan.models.graph import Graph
from dan.models.nodes import RetryPolicy
from dan.providers import CompletionResult
from dan.providers.registry import ProviderRegistry
from dan.worker.executor import ExecutionMode, WorkerExecutor
from dan.worker.model import Worker


class _StubContext:
    def __init__(self) -> None:
        self.graph = None
        self.config = EngineConfig(checkpoint_enabled=False, llm_default_model="stub-model")
        self.tool_registry = None
        self.subgraph_calls: list[dict[str, object]] = []
        self.subgraph_result: dict[str, object] | None = None

    async def run_subgraph(
        self,
        sub_graph_key: str,
        inputs: dict[str, object],
        parent_node_id: str | None = None,
        targeted_inputs: dict[str, dict[str, object]] | None = None,
    ) -> dict[str, object]:
        self.subgraph_calls.append(
            {
                "sub_graph_key": sub_graph_key,
                "inputs": dict(inputs),
                "parent_node_id": parent_node_id,
                "targeted_inputs": targeted_inputs,
            }
        )
        if self.subgraph_result is not None:
            return dict(self.subgraph_result)
        return {"sub_graph_key": sub_graph_key, "parent_node_id": parent_node_id}


class _RecordingLLMExecutor:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def execute(self, node, inputs, context) -> NodeResult:
        self.calls.append(
            {
                "legacy_node": node,
                "node": node.id,
                "model": getattr(node, "model", None),
                "system_prompt": getattr(node, "system_prompt", ""),
                "task_tier": getattr(node, "task_tier", None),
                "retry_policy": getattr(node, "retry_policy", None),
                "inputs": dict(inputs),
                "tool_count": len(getattr(node, "tools", []) or []),
            }
        )
        return NodeResult(outputs={"text": "ok"}, status=NodeStatus.COMPLETED)


class _RecordingToolExecutor:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def execute(self, node, inputs, context) -> NodeResult:
        self.calls.append(
            {
                "node": node.id,
                "tool_id": getattr(node, "tool_id", None),
                "retry_policy": getattr(node, "retry_policy", None),
                "inputs": dict(inputs),
            }
        )
        return NodeResult(outputs={"result": "ok"}, status=NodeStatus.COMPLETED)


class _RecordingCodeExecutor:
    def __init__(self, outputs: dict[str, object] | None = None) -> None:
        self.outputs = outputs or {"result": "normalized"}
        self.calls: list[dict[str, object]] = []

    async def execute(self, node, inputs, context) -> NodeResult:
        self.calls.append({"node": node.id, "inputs": dict(inputs)})
        return NodeResult(outputs=dict(self.outputs), status=NodeStatus.COMPLETED)


class _RecordingSpecializedExecutor:
    def __init__(self, outputs: dict[str, object] | None = None) -> None:
        self.outputs = outputs or {"result": "ok"}
        self.calls: list[dict[str, object]] = []

    async def execute(self, node, inputs, context) -> NodeResult:
        self.calls.append(
            {
                "node": node.id,
                "node_type": getattr(node, "node_type", None),
                "retry_policy": getattr(node, "retry_policy", None),
                "task_tier": getattr(node, "task_tier", None),
                "inputs": dict(inputs),
                "route_descriptions": getattr(node, "route_descriptions", None),
                "collection": getattr(node, "collection", None),
                "parallelism": getattr(node, "parallelism", None),
                "vote_strategy": getattr(node, "vote_strategy", None),
                "render_mode": getattr(node, "render_mode", None),
                "gate_mode": getattr(node, "gate_mode", None),
                "condition": getattr(node, "condition", None),
            }
        )
        return NodeResult(outputs=dict(self.outputs), status=NodeStatus.COMPLETED)


class _FailingExecutor:
    def __init__(self, error: str) -> None:
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def execute(self, node, inputs, context) -> NodeResult:
        self.calls.append({"node": node.id, "inputs": dict(inputs)})
        return NodeResult(outputs={}, status=NodeStatus.FAILED, error=self.error)


class _SequenceProvider:
    def __init__(self, responses: list[CompletionResult]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.requests.append(
            {
                "messages": messages,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "tools": kwargs.get("tools"),
            }
        )
        if not self._responses:
            raise AssertionError("provider received more complete() calls than expected")
        return self._responses.pop(0)

    async def stream(self, *args, **kwargs):
        raise AssertionError("tool-enabled Worker LLM path should not use streaming")
        yield


def _make_execution_context(
    *,
    provider_registry: ProviderRegistry | None = None,
    tool_registry: ToolRegistry | None = None,
) -> ExecutionContext:
    graph = Graph.model_validate(
        {
            "version": "dan_graph_v1",
            "metadata": {"name": "worker-executor-test"},
            "nodes": [],
            "edges": [],
            "sub_graphs": {},
            "entry_points": [],
            "exit_points": [],
            "shared_context": [],
            "artifact_refs": [],
        }
    )
    state = ExecutionState(graph)
    return ExecutionContext(
        state=state,
        config=EngineConfig(checkpoint_enabled=False, llm_default_model="stub-model"),
        shared_context=SharedContextStore([]),
        artifacts=ArtifactStore(),
        local_state=LocalStateManager(),
        provider_registry=provider_registry,
        tool_registry=tool_registry,
        graph=graph,
    )


@pytest.mark.asyncio
async def test_leaf_worker_cannot_delegate_subworkers() -> None:
    worker = Worker(
        id="manager",
        name="Manager",
        sub_workers={"research": "research_graph"},
    )

    result = await WorkerExecutor().execute(worker, {}, _StubContext())

    assert result.status == NodeStatus.FAILED
    assert "not allowed to delegate" in (result.error or "")


@pytest.mark.asyncio
async def test_max_spawned_workers_cap_blocks_excess_subworkers() -> None:
    worker = Worker(
        id="manager",
        name="Manager",
        authority="delegate",
        sub_workers={"research": "research_graph", "review": "review_graph"},
        authority_policy={
            "allow_delegate": True,
            "max_spawned_workers": 1,
        },
    )

    result = await WorkerExecutor().execute(worker, {}, _StubContext())

    assert result.status == NodeStatus.FAILED
    assert "exceeds max_spawned_workers" in (result.error or "")


@pytest.mark.asyncio
async def test_task_tier_cap_blocks_over_cap_llm_execution() -> None:
    fake_llm = _RecordingLLMExecutor()
    worker = Worker(
        id="reviewer",
        name="Reviewer",
        model="test-model",
        llm_hints={"task_tier": "critical"},
        authority_policy={"task_tier_cap": "routine"},
    )

    result = await WorkerExecutor(llm_executor=fake_llm).execute(
        worker,
        {"input": "check the draft"},
        _StubContext(),
    )

    assert result.status == NodeStatus.FAILED
    assert "above cap 'routine'" in (result.error or "")
    assert fake_llm.calls == []


@pytest.mark.asyncio
async def test_task_tier_cap_blocks_over_cap_subworker_execution() -> None:
    worker = Worker(
        id="manager",
        name="Manager",
        authority="delegate",
        sub_workers={"research": "research_graph"},
        llm_hints={"task_tier": "critical"},
        authority_policy={
            "allow_delegate": True,
            "task_tier_cap": "routine",
        },
    )
    context = _StubContext()

    result = await WorkerExecutor().execute(worker, {"input": "draft"}, context)

    assert result.status == NodeStatus.FAILED
    assert "above cap 'routine'" in (result.error or "")
    assert context.subgraph_calls == []


@pytest.mark.asyncio
async def test_retry_policy_ref_applies_to_llm_execution() -> None:
    fake_llm = _RecordingLLMExecutor()
    ctx = _StubContext()
    ctx.graph = SimpleNamespace(
        worker_resources={
            "retry_policies": {
                "llm_retry": {
                    "max_retries": 2,
                    "backoff": 0.5,
                    "fallback_model": "fallback-model",
                }
            }
        }
    )
    worker = Worker(
        id="reviewer",
        name="Reviewer",
        model="test-model",
        context={"retry_policy_ref": "llm_retry"},
    )

    result = await WorkerExecutor(llm_executor=fake_llm).execute(
        worker,
        {"input": "check the draft"},
        ctx,
    )

    assert result.status == NodeStatus.COMPLETED
    assert len(fake_llm.calls) == 1
    retry_policy = fake_llm.calls[0]["retry_policy"]
    assert retry_policy is not None
    assert retry_policy.max_retries == 2
    assert retry_policy.backoff == 0.5
    assert retry_policy.fallback_model == "fallback-model"


@pytest.mark.asyncio
async def test_authority_policy_ref_blocks_disallowed_toolset_access() -> None:
    fake_tool = _RecordingToolExecutor()
    ctx = _StubContext()
    ctx.graph = SimpleNamespace(
        worker_resources={
            "toolsets": {
                "privileged": {
                    "tool_ids": ["dangerous_tool"],
                }
            },
            "authority_policies": {
                "limited": {
                    "allowed_toolset_refs": ["safe"],
                }
            },
        }
    )
    worker = Worker(
        id="operator",
        name="Operator",
        context={
            "toolset_refs": ["privileged"],
            "authority_policy_ref": "limited",
        },
    )

    result = await WorkerExecutor(tool_executor=fake_tool).execute(worker, {}, ctx)

    assert result.status == NodeStatus.FAILED
    assert "cannot access toolset refs ['privileged']" in (result.error or "")
    assert fake_tool.calls == []


@pytest.mark.asyncio
async def test_provider_policy_and_instruction_profile_refs_feed_llm_execution() -> None:
    fake_llm = _RecordingLLMExecutor()
    ctx = _StubContext()
    ctx.graph = SimpleNamespace(
        worker_resources={
            "instruction_profiles": {
                "review_profile": {
                    "instruction": "Review carefully and cite evidence.",
                }
            },
            "provider_policies": {
                "economy": {
                    "default_model": "provider-default-model",
                }
            },
        }
    )
    worker = Worker(
        id="reviewer",
        name="Reviewer",
        role="reviewer",
        context={
            "instruction_profile_ref": "review_profile",
            "provider_policy_ref": "economy",
        },
    )

    result = await WorkerExecutor(llm_executor=fake_llm).execute(
        worker,
        {"input": "draft"},
        ctx,
    )

    assert result.status == NodeStatus.COMPLETED
    assert len(fake_llm.calls) == 1
    assert fake_llm.calls[0]["model"] == "provider-default-model"
    assert "Role: reviewer" in fake_llm.calls[0]["system_prompt"]
    assert "Review carefully and cite evidence." in fake_llm.calls[0]["system_prompt"]


@pytest.mark.asyncio
async def test_context_bundle_refs_feed_instruction_provider_and_retry_defaults() -> None:
    fake_llm = _RecordingLLMExecutor()
    ctx = _StubContext()
    ctx.graph = SimpleNamespace(
        worker_resources={
            "context_bundles": {
                "ops_bundle": {
                    "instruction": "Use the operating handbook.",
                    "provider_policy": {"default_model": "bundle-model"},
                    "retry_policy": {"max_retries": 4, "backoff": 0.25},
                }
            }
        }
    )
    worker = Worker(
        id="ops",
        name="Ops",
        context={"context_bundle_refs": ["ops_bundle"]},
    )

    result = await WorkerExecutor(llm_executor=fake_llm).execute(
        worker,
        {"input": "draft"},
        ctx,
    )

    assert result.status == NodeStatus.COMPLETED
    assert len(fake_llm.calls) == 1
    assert fake_llm.calls[0]["model"] == "bundle-model"
    assert "Use the operating handbook." in fake_llm.calls[0]["system_prompt"]
    retry_policy = fake_llm.calls[0]["retry_policy"]
    assert retry_policy is not None
    assert retry_policy.max_retries == 4
    assert retry_policy.backoff == 0.25


def test_effective_config_resolves_memory_policy_refs_from_shared_catalog() -> None:
    ctx = _StubContext()
    ctx.graph = SimpleNamespace(
        worker_resources={
            "defaults": {
                "memory_policy": {
                    "read_namespaces": ["memory.public"],
                    "retention": "short",
                }
            },
            "context_bundles": {
                "ops_bundle": {
                    "memory_policy": {
                        "write_namespaces": ["memory.team"],
                    }
                }
            },
            "memory_policies": {
                "sensitive_review": {
                    "retention": "long",
                    "redaction": "pii",
                }
            },
        }
    )
    worker = Worker(
        id="memory_worker",
        name="Memory Worker",
        context={
            "context_bundle_refs": ["ops_bundle"],
            "memory_policy_ref": "sensitive_review",
        },
    )

    effective = WorkerExecutor()._resolve_effective_config(worker, ctx)

    assert effective.memory_policy == {
        "read_namespaces": ["memory.public"],
        "write_namespaces": ["memory.team"],
        "retention": "long",
        "redaction": "pii",
    }


@pytest.mark.asyncio
async def test_graph_defaults_feed_instruction_model_tool_and_retry_into_llm_execution() -> None:
    fake_llm = _RecordingLLMExecutor()
    ctx = _StubContext()
    ctx.graph = SimpleNamespace(
        worker_resources={
            "defaults": {
                "instruction": "Use the operating playbook.",
                "provider_policy": {"default_model": "default-ops-model"},
                "retry_policy": {"max_retries": 3, "backoff": 0.4},
                "tool_ids": ["lookup"],
            }
        }
    )
    worker = Worker(
        id="ops",
        name="Ops",
        llm_hints={"prompt_template": "Respond to {input}"},
    )

    result = await WorkerExecutor(llm_executor=fake_llm).execute(
        worker,
        {"input": "draft"},
        ctx,
    )

    assert result.status == NodeStatus.COMPLETED
    assert len(fake_llm.calls) == 1
    assert fake_llm.calls[0]["model"] == "default-ops-model"
    assert "Use the operating playbook." in fake_llm.calls[0]["system_prompt"]
    assert fake_llm.calls[0]["tool_count"] == 1
    retry_policy = fake_llm.calls[0]["retry_policy"]
    assert retry_policy is not None
    assert retry_policy.max_retries == 3
    assert retry_policy.backoff == 0.4


@pytest.mark.asyncio
async def test_inherit_defaults_false_skips_graph_default_worker_resources() -> None:
    fake_llm = _RecordingLLMExecutor()
    ctx = _StubContext()
    ctx.graph = SimpleNamespace(
        worker_resources={
            "defaults": {
                "instruction": "Use the operating playbook.",
                "provider_policy": {"default_model": "default-ops-model"},
                "retry_policy": {"max_retries": 3, "backoff": 0.4},
                "tool_ids": ["lookup"],
            }
        }
    )
    worker = Worker(
        id="ops",
        name="Ops",
        llm_hints={"prompt_template": "Respond to {input}"},
        context={"inherit_defaults": False},
    )

    result = await WorkerExecutor(llm_executor=fake_llm).execute(
        worker,
        {"input": "draft"},
        ctx,
    )

    assert result.status == NodeStatus.COMPLETED
    assert len(fake_llm.calls) == 1
    assert fake_llm.calls[0]["model"] == "stub-model"
    assert "Use the operating playbook." not in fake_llm.calls[0]["system_prompt"]
    assert fake_llm.calls[0]["tool_count"] == 0
    assert fake_llm.calls[0]["retry_policy"] is None


@pytest.mark.asyncio
async def test_static_llm_fast_path_reuses_resolved_legacy_template() -> None:
    fake_llm = _RecordingLLMExecutor()
    executor = WorkerExecutor(llm_executor=fake_llm)
    worker = Worker(
        id="reviewer",
        name="Reviewer",
        role="reviewer",
        model="test-model",
        instruction="Be concise and precise.",
    )

    first = await executor.execute(worker, {"input": "draft-a"}, _StubContext())
    second = await executor.execute(worker, {"input": "draft-b"}, _StubContext())

    assert first.status == NodeStatus.COMPLETED
    assert second.status == NodeStatus.COMPLETED
    assert len(fake_llm.calls) == 2
    assert fake_llm.calls[0]["legacy_node"] is fake_llm.calls[1]["legacy_node"]
    assert len(executor._static_llm_templates) == 1
    assert "Role: reviewer" in fake_llm.calls[0]["system_prompt"]
    assert "Be concise and precise." in fake_llm.calls[0]["system_prompt"]


@pytest.mark.asyncio
async def test_toolset_ref_supplies_direct_tool_execution() -> None:
    fake_tool = _RecordingToolExecutor()
    ctx = _StubContext()
    ctx.graph = SimpleNamespace(
        worker_resources={
            "toolsets": {
                "safe_tools": {
                    "tool_ids": ["lookup"],
                }
            }
        }
    )
    worker = Worker(
        id="operator",
        name="Operator",
        context={"toolset_refs": ["safe_tools"]},
    )

    result = await WorkerExecutor(tool_executor=fake_tool).execute(worker, {"query": "abc"}, ctx)

    assert result.status == NodeStatus.COMPLETED
    assert len(fake_tool.calls) == 1
    assert fake_tool.calls[0]["tool_id"] == "lookup"


@pytest.mark.asyncio
async def test_node_retry_policy_overrides_shared_retry_policy_ref() -> None:
    fake_llm = _RecordingLLMExecutor()
    ctx = _StubContext()
    ctx.graph = SimpleNamespace(
        worker_resources={
            "retry_policies": {
                "shared_retry": {
                    "max_retries": 5,
                    "backoff": 1.0,
                }
            }
        }
    )
    worker = Worker(
        id="override",
        name="Override",
        model="test-model",
        context={"retry_policy_ref": "shared_retry"},
        retry_policy=RetryPolicy(max_retries=1, backoff=0.1),
    )

    result = await WorkerExecutor(llm_executor=fake_llm).execute(
        worker,
        {"input": "draft"},
        ctx,
    )

    assert result.status == NodeStatus.COMPLETED
    assert len(fake_llm.calls) == 1
    retry_policy = fake_llm.calls[0]["retry_policy"]
    assert retry_policy is not None
    assert retry_policy.max_retries == 1
    assert retry_policy.backoff == 0.1


@pytest.mark.asyncio
async def test_authority_policy_blocks_disallowed_memory_write_scope() -> None:
    fake_code = _RecordingCodeExecutor()
    worker = Worker(
        id="writer",
        name="Writer",
        code="result = {'status': 'ok'}",
        write_set=[{"key": "memory.secret.plan", "mode": "write"}],
        authority_policy={"allow_memory_write_scopes": ["memory.public"]},
    )

    result = await WorkerExecutor(code_executor=fake_code).execute(worker, {}, _StubContext())

    assert result.status == NodeStatus.FAILED
    assert "cannot write memory scopes ['memory.secret.plan']" in (result.error or "")
    assert fake_code.calls == []


@pytest.mark.asyncio
async def test_authority_policy_allows_namespaced_memory_write_scope() -> None:
    fake_code = _RecordingCodeExecutor()
    worker = Worker(
        id="writer",
        name="Writer",
        code="result = {'status': 'ok'}",
        write_set=[{"key": "memory.team.notes", "mode": "write"}],
        authority_policy={"allow_memory_write_scopes": ["memory.team"]},
    )

    result = await WorkerExecutor(code_executor=fake_code).execute(worker, {}, _StubContext())

    assert result.status == NodeStatus.COMPLETED
    assert len(fake_code.calls) == 1


@pytest.mark.asyncio
async def test_body_graph_mode_runs_subgraph_with_parent_context() -> None:
    worker = Worker(
        id="composite",
        name="Composite",
        body_graph="body_graph_key",
        output_ports=[],
    )

    result = await WorkerExecutor().execute(worker, {"input": "draft"}, _StubContext())

    assert result.status == NodeStatus.COMPLETED
    assert result.outputs["sub_graph_key"] == "body_graph_key"
    assert result.outputs["parent_node_id"] == "composite"


@pytest.mark.asyncio
async def test_body_graph_mode_aliases_single_output_without_recursive_payload() -> None:
    worker = Worker(
        id="composite",
        name="Composite",
        body_graph="body_graph_key",
        output_ports=[{"name": "result"}],
    )
    context = _StubContext()
    context.subgraph_result = {"sub_graph_key": "body_graph_key", "parent_node_id": "composite"}

    result = await WorkerExecutor().execute(worker, {"input": "draft"}, context)

    assert result.status == NodeStatus.COMPLETED
    assert result.outputs["result"] == {
        "sub_graph_key": "body_graph_key",
        "parent_node_id": "composite",
    }
    assert result.outputs["result"] is not result.outputs
    json.dumps(result.outputs)


@pytest.mark.asyncio
async def test_body_graph_mode_applies_input_and_output_mappings() -> None:
    worker = Worker(
        id="composite",
        name="Composite",
        body_graph="body_graph_key",
        input_mappings={
            "draft": "rewrite::input",
            "context": "shared_context",
        },
        output_mappings={"summary": "result"},
        output_ports=[{"name": "result"}],
    )
    context = _StubContext()
    context.subgraph_result = {"summary": "tightened", "notes": ["a"]}

    result = await WorkerExecutor().execute(
        worker,
        {"draft": "draft-v1", "context": {"topic": "agents"}, "passthrough": True},
        context,
    )

    assert result.status == NodeStatus.COMPLETED
    assert context.subgraph_calls == [
        {
            "sub_graph_key": "body_graph_key",
            "inputs": {"shared_context": {"topic": "agents"}, "passthrough": True},
            "parent_node_id": "composite",
            "targeted_inputs": {"rewrite": {"input": "draft-v1"}},
        }
    ]
    assert result.outputs == {"result": "tightened", "notes": ["a"]}


@pytest.mark.asyncio
async def test_passthrough_maps_named_and_fallback_inputs() -> None:
    worker = Worker(
        id="passthrough",
        name="Passthrough",
        input_ports=[],
        output_ports=[
            {"name": "data"},
            {"name": "summary"},
        ],
    )

    result = await WorkerExecutor().execute(
        worker,
        {"data": {"value": 1}, "notes": "fallback"},
        _StubContext(),
    )

    assert result.status == NodeStatus.COMPLETED
    assert result.outputs == {"data": {"value": 1}, "summary": "fallback"}


@pytest.mark.asyncio
async def test_passthrough_preserves_input_node_variable_outputs_and_aggregate_payload() -> None:
    worker = Worker(
        id="workflow_inputs",
        name="Workflow Inputs",
        metadata={
            "input_variables": [
                {"name": "topic", "type": "string", "default": "fallback"},
            ]
        },
        output_ports=[
            {"name": "input"},
            {"name": "topic"},
        ],
    )

    result = await WorkerExecutor().execute(worker, {"topic": "agents"}, _StubContext())

    assert result.status == NodeStatus.COMPLETED
    assert result.outputs == {
        "topic": "agents",
        "input": {"topic": "agents"},
    }


def test_detect_modes_orders_combined_worker_capabilities() -> None:
    worker = Worker(
        id="combo",
        name="Combo",
        code="result = 'normalized'",
        model="test-model",
        tool_ids=["search"],
    )
    executor = WorkerExecutor()
    effective = executor._resolve_effective_config(worker, _StubContext())

    modes = executor._detect_modes(worker, effective)

    assert modes == [ExecutionMode.SCRIPT, ExecutionMode.LLM_WITH_TOOLS]


def test_detect_modes_treats_llm_hints_without_model_as_llm() -> None:
    worker = Worker(
        id="draft",
        name="Draft",
        llm_hints={"prompt_template": "Draft {input}"},
    )
    executor = WorkerExecutor()
    effective = executor._resolve_effective_config(worker, _StubContext())

    modes = executor._detect_modes(worker, effective)

    assert modes == [ExecutionMode.LLM]


def test_detect_modes_prioritizes_specialized_role_over_generic_llm_mode() -> None:
    worker = Worker(
        id="route",
        name="Route",
        role="router",
        model="test-model",
        metadata={"route_descriptions": {"research": "Do research"}},
    )
    executor = WorkerExecutor()
    effective = executor._resolve_effective_config(worker, _StubContext())

    modes = executor._detect_modes(worker, effective)

    assert modes == [ExecutionMode.ROUTER]


def test_detect_modes_prioritizes_control_flow_over_generic_llm_mode() -> None:
    worker = Worker(
        id="route",
        name="Route",
        model="test-model",
        llm_hints={"prompt_template": "Review {input}"},
        control_flow={"condition": "score > 0.5"},
    )
    executor = WorkerExecutor()
    effective = executor._resolve_effective_config(worker, _StubContext())

    modes = executor._detect_modes(worker, effective)

    assert modes == [ExecutionMode.GATE]


@pytest.mark.asyncio
async def test_llm_with_tools_mode_supplies_tool_schemas() -> None:
    fake_llm = _RecordingLLMExecutor()
    worker = Worker(
        id="assistant",
        name="Assistant",
        model="test-model",
        tool_ids=["file_read"],
    )

    result = await WorkerExecutor(llm_executor=fake_llm).execute(worker, {"input": "read notes"}, _StubContext())

    assert result.status == NodeStatus.COMPLETED
    assert len(fake_llm.calls) == 1
    assert fake_llm.calls[0]["tool_count"] == 1


@pytest.mark.asyncio
async def test_llm_with_tools_mode_runs_real_tool_loop_until_plain_text_response() -> None:
    tool_calls: list[str] = []

    async def lookup(*, query: str) -> dict[str, str]:
        tool_calls.append(query)
        return {"answer": query.upper()}

    tool_registry = ToolRegistry()
    tool_registry.register("lookup", lookup)

    provider = _SequenceProvider(
        [
            CompletionResult(
                text="",
                tool_calls=[
                    {
                        "id": "call_lookup",
                        "type": "function",
                        "function": {
                            "name": "lookup",
                            "arguments": '{"query":"read notes"}',
                        },
                    }
                ],
            ),
            CompletionResult(
                text="Tool answered: READ NOTES",
                tool_calls=[],
            ),
        ]
    )
    registry = ProviderRegistry()
    registry.register("default", provider)
    context = _make_execution_context(provider_registry=registry, tool_registry=tool_registry)

    worker = Worker(
        id="assistant",
        name="Assistant",
        model="custom-model",
        tool_ids=["lookup"],
        llm_hints={"max_tool_rounds": 2},
    )

    result = await WorkerExecutor(llm_executor=LLMExecutor()).execute(
        worker,
        {"input": "read notes"},
        context,
    )

    assert result.status == NodeStatus.COMPLETED
    assert result.outputs["text"] == "Tool answered: READ NOTES"
    assert tool_calls == ["read notes"]
    assert len(provider.requests) == 2
    assert len(provider.requests[0]["tools"] or []) == 1
    followup_messages = provider.requests[1]["messages"]
    assert any(message.get("role") == "tool" for message in followup_messages)
    assert any("READ NOTES" in str(message.get("content", "")) for message in followup_messages)


@pytest.mark.asyncio
async def test_model_less_llm_worker_uses_runtime_default_model() -> None:
    fake_llm = _RecordingLLMExecutor()
    worker = Worker(
        id="assistant",
        name="Assistant",
        llm_hints={"prompt_template": "Respond to {input}"},
    )

    result = await WorkerExecutor(llm_executor=fake_llm).execute(worker, {"input": "hello"}, _StubContext())

    assert result.status == NodeStatus.COMPLETED
    assert len(fake_llm.calls) == 1
    assert fake_llm.calls[0]["model"] == "stub-model"


@pytest.mark.asyncio
async def test_router_worker_delegates_through_specialized_executor() -> None:
    fake_router = _RecordingSpecializedExecutor(outputs={"route": "research"})
    worker = Worker(
        id="route",
        name="Route",
        role="router",
        model="test-model",
        metadata={"route_descriptions": {"research": "Do research"}},
        output_ports=[{"name": "route"}, {"name": "result"}],
    )

    result = await WorkerExecutor(router_executor=fake_router).execute(worker, {"input": "draft"}, _StubContext())

    assert result.status == NodeStatus.COMPLETED
    assert result.outputs == {"route": "research"}
    assert len(fake_router.calls) == 1
    assert fake_router.calls[0]["node_type"] == "router"
    assert fake_router.calls[0]["route_descriptions"] == {"research": "Do research"}


@pytest.mark.asyncio
async def test_validator_worker_delegates_through_specialized_executor() -> None:
    fake_validator = _RecordingSpecializedExecutor(outputs={"valid": {"summary": "ok"}})
    worker = Worker(
        id="validate",
        name="Validate",
        role="validator",
        metadata={
            "validation_rules": [{"rule_type": "required_keys", "config": {"keys": ["summary"]}}],
            "validator_on_failure": "route",
            "validator_strict_mode": True,
        },
        input_ports=[{"name": "data", "required": False}],
        output_ports=[{"name": "valid"}, {"name": "invalid"}],
    )

    result = await WorkerExecutor(validator_executor=fake_validator).execute(
        worker,
        {"data": {"summary": "ok"}},
        _StubContext(),
    )

    assert result.status == NodeStatus.COMPLETED
    assert result.outputs == {"valid": {"summary": "ok"}}
    assert len(fake_validator.calls) == 1
    assert fake_validator.calls[0]["node_type"] == "validator"


@pytest.mark.asyncio
async def test_validator_worker_uses_first_class_validation_rules() -> None:
    fake_validator = _RecordingSpecializedExecutor(outputs={"valid": {"summary": "ok"}})
    worker = Worker(
        id="validate",
        name="Validate",
        role="validator",
        validation_rules=[{"rule_type": "required_keys", "config": {"keys": ["summary"]}}],
        input_ports=[{"name": "data", "required": False}],
        output_ports=[{"name": "valid"}, {"name": "invalid"}],
    )

    result = await WorkerExecutor(validator_executor=fake_validator).execute(
        worker,
        {"data": {"summary": "ok"}},
        _StubContext(),
    )

    assert result.status == NodeStatus.COMPLETED
    assert len(fake_validator.calls) == 1
    assert fake_validator.calls[0]["node_type"] == "validator"


@pytest.mark.asyncio
async def test_reflection_worker_delegates_with_shared_retry_policy() -> None:
    fake_reflection = _RecordingSpecializedExecutor(outputs={"principles": []})
    ctx = _StubContext()
    ctx.graph = SimpleNamespace(
        worker_resources={
            "retry_policies": {
                "reflect_retry": {
                    "max_retries": 2,
                    "backoff": 0.25,
                }
            }
        }
    )
    worker = Worker(
        id="reflect",
        name="Reflect",
        role="reflection",
        model="test-model",
        context={"retry_policy_ref": "reflect_retry"},
        llm_hints={"task_tier": "reasoning"},
        metadata={"reflection_prompt": "Distill lessons."},
        output_ports=[{"name": "principles"}],
    )

    result = await WorkerExecutor(reflection_executor=fake_reflection).execute(worker, {"input": "draft"}, ctx)

    assert result.status == NodeStatus.COMPLETED
    assert len(fake_reflection.calls) == 1
    retry_policy = fake_reflection.calls[0]["retry_policy"]
    assert retry_policy is not None
    assert retry_policy.max_retries == 2
    assert retry_policy.backoff == 0.25
    assert fake_reflection.calls[0]["task_tier"] == "reasoning"


@pytest.mark.asyncio
async def test_sub_workers_respect_parallelism_and_last_write_wins_merge_strategy() -> None:
    worker = Worker(
        id="manager",
        name="Manager",
        authority="delegate",
        sub_workers={"research": "research_graph", "review": "review_graph"},
        authority_policy={"allow_delegate": True},
        parallelism=2,
        merge_strategy=MergeStrategy.LAST_WRITE_WINS,
        output_ports=[{"name": "result"}],
    )
    context = _StubContext()

    async def _fake_run_subgraph(sub_graph_key, inputs, parent_node_id=None, targeted_inputs=None):
        if sub_graph_key == "research_graph":
            return {"summary": "research", "sources": 3}
        return {"summary": "review", "approved": True}

    context.run_subgraph = _fake_run_subgraph

    result = await WorkerExecutor().execute(worker, {"input": "draft"}, context)

    assert result.status == NodeStatus.COMPLETED
    assert result.outputs["results"] == {
        "research": {"summary": "research", "sources": 3},
        "review": {"summary": "review", "approved": True},
    }
    assert result.outputs["result"] == {"summary": "review", "sources": 3, "approved": True}


@pytest.mark.asyncio
async def test_spawn_policy_max_spawns_per_node_blocks_excess_subworkers() -> None:
    worker = Worker(
        id="manager",
        name="Manager",
        authority="delegate",
        sub_workers={"research": "research_graph", "review": "review_graph"},
        authority_policy={"allow_delegate": True},
        spawn_policy={"max_spawns_per_node": 1},
    )

    result = await WorkerExecutor().execute(worker, {}, _StubContext())

    assert result.status == NodeStatus.FAILED
    assert "spawn_policy.max_spawns_per_node" in (result.error or "")


@pytest.mark.asyncio
async def test_rag_worker_delegates_through_specialized_executor() -> None:
    fake_rag = _RecordingSpecializedExecutor(outputs={"chunks": [{"text": "chunk"}]})
    worker = Worker(
        id="retrieve",
        name="Retrieve",
        role="rag",
        metadata={"rag_collection": "papers", "rag_top_k": 3},
        input_ports=[{"name": "query", "required": False}],
        output_ports=[{"name": "chunks"}],
    )

    result = await WorkerExecutor(rag_executor=fake_rag).execute(worker, {"query": "agents"}, _StubContext())

    assert result.status == NodeStatus.COMPLETED
    assert result.outputs == {"chunks": [{"text": "chunk"}]}
    assert len(fake_rag.calls) == 1
    assert fake_rag.calls[0]["node_type"] == "rag_operator"
    assert fake_rag.calls[0]["collection"] == "papers"


@pytest.mark.asyncio
async def test_human_worker_delegates_through_specialized_executor() -> None:
    fake_human = _RecordingSpecializedExecutor(outputs={"response": "approved"})
    worker = Worker(
        id="review",
        name="Review",
        role="human",
        metadata={
            "human_prompt": "Review the draft",
            "human_render_mode": "approval",
        },
        input_ports=[{"name": "input", "required": False}],
        output_ports=[{"name": "response"}],
    )

    result = await WorkerExecutor(human_executor=fake_human).execute(worker, {"input": "draft"}, _StubContext())

    assert result.status == NodeStatus.COMPLETED
    assert result.outputs == {"response": "approved"}
    assert len(fake_human.calls) == 1
    assert fake_human.calls[0]["node_type"] == "human"
    assert fake_human.calls[0]["render_mode"] == "approval"


@pytest.mark.asyncio
async def test_vote_worker_delegates_through_specialized_executor() -> None:
    fake_vote = _RecordingSpecializedExecutor(outputs={"winner": "claude-sonnet-4-6"})
    worker = Worker(
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
        input_ports=[{"name": "input", "required": False}],
        output_ports=[{"name": "winner"}],
    )

    result = await WorkerExecutor(vote_executor=fake_vote).execute(worker, {"input": "draft"}, _StubContext())

    assert result.status == NodeStatus.COMPLETED
    assert result.outputs == {"winner": "claude-sonnet-4-6"}
    assert len(fake_vote.calls) == 1
    assert fake_vote.calls[0]["node_type"] == "vote"
    assert fake_vote.calls[0]["vote_strategy"] == "judge"
    assert fake_vote.calls[0]["parallelism"] == 2


@pytest.mark.asyncio
async def test_control_flow_worker_delegates_through_gate_executor() -> None:
    fake_gate = _RecordingSpecializedExecutor(outputs={"true": {"score": 0.9}})
    worker = Worker(
        id="route",
        name="Route",
        control_flow={"condition": "score > 0.5", "gate_mode": "if_else"},
    )

    result = await WorkerExecutor(gate_executor=fake_gate).execute(
        worker,
        {"score": 0.9},
        _StubContext(),
    )

    assert result.status == NodeStatus.COMPLETED
    assert result.outputs == {"true": {"score": 0.9}}
    assert len(fake_gate.calls) == 1
    assert fake_gate.calls[0]["node_type"] == "gate"
    assert fake_gate.calls[0]["gate_mode"] == "if_else"
    assert fake_gate.calls[0]["condition"] == "score > 0.5"


@pytest.mark.asyncio
async def test_while_control_flow_worker_delegates_through_gate_executor() -> None:
    fake_gate = _RecordingSpecializedExecutor(outputs={"continue": {"counter": 1}})
    worker = Worker(
        id="loop_gate",
        name="Loop Gate",
        control_flow={
            "condition": "counter < 3",
            "gate_mode": "while",
            "max_iterations": 3,
        },
    )

    result = await WorkerExecutor(gate_executor=fake_gate).execute(
        worker,
        {"counter": 1},
        _StubContext(),
    )

    assert result.status == NodeStatus.COMPLETED
    assert result.outputs == {"continue": {"counter": 1}}
    assert len(fake_gate.calls) == 1
    assert fake_gate.calls[0]["node_type"] == "gate"
    assert fake_gate.calls[0]["gate_mode"] == "while"
    assert fake_gate.calls[0]["condition"] == "counter < 3"


@pytest.mark.asyncio
async def test_script_failure_short_circuits_followup_llm_mode() -> None:
    failing_code = _FailingExecutor("script exploded")
    fake_llm = _RecordingLLMExecutor()
    worker = Worker(
        id="combo",
        name="Combo",
        code="raise RuntimeError('boom')",
        model="test-model",
    )

    result = await WorkerExecutor(
        code_executor=failing_code,
        llm_executor=fake_llm,
    ).execute(
        worker,
        {"input": "raw draft"},
        _StubContext(),
    )

    assert result.status == NodeStatus.FAILED
    assert result.error == "script exploded"
    assert len(failing_code.calls) == 1
    assert fake_llm.calls == []


@pytest.mark.asyncio
async def test_direct_tool_failure_propagates_tool_executor_error() -> None:
    tool_registry = ToolRegistry()

    async def failing_lookup(**kwargs):
        raise RuntimeError("tool exploded")

    tool_registry.register("lookup", failing_lookup)
    worker = Worker(
        id="tooler",
        name="Tooler",
        tool_ids=["lookup"],
    )
    context = _make_execution_context(tool_registry=tool_registry)

    result = await WorkerExecutor(tool_executor=ToolExecutor(tool_registry)).execute(
        worker,
        {"input": "draft"},
        context,
    )

    assert result.status == NodeStatus.FAILED
    assert "tool exploded" in (result.error or "")


@pytest.mark.asyncio
async def test_llm_timeout_failure_propagates_error() -> None:
    failing_llm = _FailingExecutor("API timeout")
    worker = Worker(
        id="assistant",
        name="Assistant",
        model="test-model",
    )

    result = await WorkerExecutor(llm_executor=failing_llm).execute(
        worker,
        {"input": "hello"},
        _StubContext(),
    )

    assert result.status == NodeStatus.FAILED
    assert result.error == "API timeout"
    assert len(failing_llm.calls) == 1


@pytest.mark.asyncio
async def test_code_stage_feeds_followup_llm_inputs() -> None:
    fake_code = _RecordingCodeExecutor(outputs={"result": "normalized draft"})
    fake_llm = _RecordingLLMExecutor()
    worker = Worker(
        id="combo",
        name="Combo",
        code="result = normalize(input)",
        model="test-model",
    )

    result = await WorkerExecutor(
        code_executor=fake_code,
        llm_executor=fake_llm,
    ).execute(
        worker,
        {"source": "raw", "input": "raw draft"},
        _StubContext(),
    )

    assert result.status == NodeStatus.COMPLETED
    assert len(fake_code.calls) == 1
    assert len(fake_llm.calls) == 1
    assert fake_llm.calls[0]["retry_policy"] is None
    assert fake_llm.calls[0]["inputs"]["result"] == "normalized draft"
    assert fake_llm.calls[0]["inputs"]["input"] == "normalized draft"


@pytest.mark.asyncio
async def test_code_stage_feeds_followup_direct_tool_inputs() -> None:
    fake_code = _RecordingCodeExecutor(outputs={"result": "normalized draft"})
    fake_tool = _RecordingToolExecutor()
    worker = Worker(
        id="combo_tool",
        name="Combo Tool",
        code="result = normalize(input)",
        tool_ids=["lookup"],
    )

    result = await WorkerExecutor(
        code_executor=fake_code,
        tool_executor=fake_tool,
    ).execute(
        worker,
        {"source": "raw", "input": "raw draft"},
        _StubContext(),
    )

    assert result.status == NodeStatus.COMPLETED
    assert len(fake_code.calls) == 1
    assert len(fake_tool.calls) == 1
    assert fake_tool.calls[0]["inputs"]["result"] == "normalized draft"
    assert fake_tool.calls[0]["inputs"]["input"] == "normalized draft"
