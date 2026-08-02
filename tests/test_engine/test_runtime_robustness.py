from __future__ import annotations

import asyncio
import copy
from types import MethodType
from typing import Any, Callable

import pytest

from dan.engine.checkpoint import CheckpointStore
from dan.engine.executor import EngineConfig, ExecutionContext, ExecutorRegistry, NodeResult
from dan.engine.scheduler import Engine
from dan.engine.state import NodeStatus
from dan.models.context import BoundaryContract
from dan.models.control_flow import DynamicExpansionSpec, SpawnPolicy
from dan.models.edges import DataEdge
from dan.models.graph import Graph
from dan.models.nodes import CodeOperator, LLMOperator, RetryPolicy, ToolOperator
from dan.models.ports import InputPort, OutputPort
from dan.providers import CompletionResult
from dan.server.run_manager import RunManager, RunStatus


class RecordingCheckpointStore(CheckpointStore):
    def __init__(self) -> None:
        self._latest: dict[str, dict[str, Any]] = {}
        self.saves: list[dict[str, Any]] = []

    async def save(self, run_id: str, state: dict[str, Any]) -> None:
        payload = copy.deepcopy(state)
        self._latest[run_id] = payload
        self.saves.append(payload)

    async def load(self, run_id: str) -> dict[str, Any] | None:
        payload = self._latest.get(run_id)
        return copy.deepcopy(payload) if payload is not None else None

    async def list_runs(self) -> list[str]:
        return sorted(self._latest)


def _port(name: str, *, required: bool = True) -> InputPort:
    return InputPort(name=name, json_schema={"type": "string"}, required=required)


def _out(name: str = "out") -> OutputPort:
    return OutputPort(name=name, json_schema={"type": "string"})


def _tool(node_id: str, *, delay: float = 0.0) -> ToolOperator:
    return ToolOperator(
        id=node_id,
        name=node_id,
        tool_id=f"tool:{node_id}",
        input_ports=[_port("inp")] if node_id != "n0" else [],
        output_ports=[_out()],
        tool_config={"delay": delay},
    )


def _chain_graph(delays: list[float]) -> Graph:
    nodes = [_tool(f"n{i}", delay=delay) for i, delay in enumerate(delays)]
    edges = [
        DataEdge(
            id=f"e{i}",
            source_node_id=f"n{i - 1}",
            source_port="out",
            target_node_id=f"n{i}",
            target_port="inp",
        )
        for i in range(1, len(delays))
    ]
    return Graph(
        nodes=nodes,
        edges=edges,
        entry_points=["n0"],
        exit_points=[f"n{len(delays) - 1}"],
    )


class DelayExecutor:
    async def execute(
        self,
        node: ToolOperator,
        inputs: dict[str, Any],
        context: Any,
    ) -> NodeResult:
        delay = float(node.tool_config.get("delay", 0.0))
        if delay > 0:
            await asyncio.sleep(delay)
        value = node.id if "inp" not in inputs else f"{inputs['inp']}->{node.id}"
        return NodeResult(outputs={"out": value}, status=NodeStatus.COMPLETED)


class CostExecutor:
    async def execute(
        self,
        node: ToolOperator,
        inputs: dict[str, Any],
        context: Any,
    ) -> NodeResult:
        context.cost_tracker.record(
            node.id,
            "gpt-4o-mini",
            {"prompt_tokens": 1000, "completion_tokens": 1000},
        )
        return NodeResult(outputs={"out": node.id}, status=NodeStatus.COMPLETED)


class FakeProviderRegistry:
    def __init__(self, provider: Any) -> None:
        self.provider = provider

    def resolve(self, model: str) -> Any:
        return self.provider


class InvalidJSONProvider:
    def __init__(self) -> None:
        self.complete_calls = 0

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int | None = None,
        **_: Any,
    ) -> CompletionResult:
        self.complete_calls += 1
        return CompletionResult(
            text='{"verdict":"accept","score":0.9,}',
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            model=model,
        )

    async def stream(self, *args: Any, **kwargs: Any):
        raise NotImplementedError


class SchemaMismatchProvider:
    def __init__(self) -> None:
        self.complete_calls = 0

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int | None = None,
        **_: Any,
    ) -> CompletionResult:
        self.complete_calls += 1
        return CompletionResult(
            text='{"verdict":123,"score":"high"}',
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            model=model,
        )

    async def stream(self, *args: Any, **kwargs: Any):
        raise NotImplementedError


class DynamicTopologyExecutor:
    def __init__(self) -> None:
        self.child_calls = 0

    async def execute(
        self,
        node: ToolOperator,
        inputs: dict[str, Any],
        context: Any,
    ) -> NodeResult:
        if node.id == "spawn":
            first = await context.run_child_workflow(
                DynamicExpansionSpec(mode="sub_graph", ref="child"),
                {"seed": "alpha"},
                parent_node_id=node.id,
            )
            second = await context.run_child_workflow(
                DynamicExpansionSpec(mode="sub_graph", ref="child"),
                {"seed": "alpha"},
                parent_node_id=node.id,
            )
            return NodeResult(
                outputs={
                    "out": f"{first.outputs['value']}|{second.outputs['value']}",
                },
                status=NodeStatus.COMPLETED,
            )

        self.child_calls += 1
        return NodeResult(
            outputs={"value": f"child-{self.child_calls}"},
            status=NodeStatus.COMPLETED,
        )


class DynamicBoundaryExecutor:
    def __init__(self) -> None:
        self.child_calls = 0

    async def execute(
        self,
        node: ToolOperator,
        inputs: dict[str, Any],
        context: Any,
    ) -> NodeResult:
        if node.id == "spawn":
            await context.run_child_workflow(
                DynamicExpansionSpec(
                    mode="sub_graph",
                    ref="child",
                    boundary_contract=BoundaryContract(
                        accepts={
                            "type": "object",
                            "properties": {"seed": {"type": "string"}},
                            "required": ["seed"],
                        },
                        returns={
                            "type": "object",
                            "properties": {"value": {"type": "string"}},
                            "required": ["value"],
                        },
                    ),
                ),
                {"seed": 123},
                parent_node_id=node.id,
            )
            return NodeResult(outputs={"out": "unreachable"}, status=NodeStatus.COMPLETED)

        self.child_calls += 1
        return NodeResult(
            outputs={"value": f"child-{self.child_calls}"},
            status=NodeStatus.COMPLETED,
        )


class DynamicSpawnLimitExecutor:
    def __init__(self) -> None:
        self.child_calls = 0

    async def execute(
        self,
        node: ToolOperator,
        inputs: dict[str, Any],
        context: Any,
    ) -> NodeResult:
        if node.id == "spawn":
            spec = DynamicExpansionSpec(
                mode="sub_graph",
                ref="child",
                spawn_policy=SpawnPolicy(
                    max_spawns_per_node=1,
                    max_total_children=8,
                ),
            )
            first = await context.run_child_workflow(
                spec,
                {"seed": "alpha"},
                parent_node_id=node.id,
            )
            assert first.outputs["value"] == "child-1"
            await context.run_child_workflow(
                spec,
                {"seed": "beta"},
                parent_node_id=node.id,
            )
            return NodeResult(outputs={"out": "unreachable"}, status=NodeStatus.COMPLETED)

        self.child_calls += 1
        return NodeResult(
            outputs={"value": f"child-{self.child_calls}"},
            status=NodeStatus.COMPLETED,
        )


class DynamicTimeoutExecutor:
    async def execute(
        self,
        node: ToolOperator,
        inputs: dict[str, Any],
        context: Any,
    ) -> NodeResult:
        if node.id == "spawn":
            await context.run_child_workflow(
                DynamicExpansionSpec(
                    mode="sub_graph",
                    ref="child",
                    spawn_policy=SpawnPolicy(timeout_seconds=0.01),
                ),
                {"seed": "alpha"},
                parent_node_id=node.id,
            )
            return NodeResult(outputs={"out": "unreachable"}, status=NodeStatus.COMPLETED)

        delay = float(node.tool_config.get("delay", 0.0))
        if delay > 0:
            await asyncio.sleep(delay)
        return NodeResult(
            outputs={"value": "slow-child"},
            status=NodeStatus.COMPLETED,
        )


class DynamicTemplateBranchExecutor:
    def __init__(self) -> None:
        self.child_calls = 0

    async def execute(
        self,
        node: ToolOperator,
        inputs: dict[str, Any],
        context: Any,
    ) -> NodeResult:
        if node.id == "spawn":
            envelope = await context.run_child_workflow(
                DynamicExpansionSpec(mode="template_branch", ref="child"),
                {"seed": "alpha"},
                parent_node_id=node.id,
            )
            return NodeResult(
                outputs={"out": envelope.outputs["value"]},
                status=NodeStatus.COMPLETED,
            )

        self.child_calls += 1
        return NodeResult(
            outputs={"value": f"template-{self.child_calls}"},
            status=NodeStatus.COMPLETED,
        )


class DynamicWorkflowRefExecutor:
    def __init__(self) -> None:
        self.child_calls = 0

    async def execute(
        self,
        node: ToolOperator,
        inputs: dict[str, Any],
        context: Any,
    ) -> NodeResult:
        if node.id == "spawn":
            envelope = await context.run_child_workflow(
                DynamicExpansionSpec(mode="workflow_ref", ref="child-workflow"),
                {"seed": "alpha"},
                parent_node_id=node.id,
            )
            return NodeResult(
                outputs={"out": envelope.outputs["value"]},
                status=NodeStatus.COMPLETED,
            )

        self.child_calls += 1
        return NodeResult(
            outputs={"value": f"workflow-{self.child_calls}"},
            status=NodeStatus.COMPLETED,
        )


class AlwaysFailToolExecutor:
    async def execute(
        self,
        node: ToolOperator,
        inputs: dict[str, Any],
        context: Any,
    ) -> NodeResult:
        return NodeResult(
            outputs={},
            status=NodeStatus.FAILED,
            error="unknown tool config",
        )


class PolicyInspectExecutor:
    def __init__(self) -> None:
        self.seen_retry_policies: dict[str, int | None] = {}

    async def execute(
        self,
        node: ToolOperator,
        inputs: dict[str, Any],
        context: Any,
    ) -> NodeResult:
        retry_policy = getattr(node, "retry_policy", None)
        self.seen_retry_policies[node.id] = (
            int(getattr(retry_policy, "max_retries", 0))
            if retry_policy is not None
            else None
        )
        return NodeResult(
            outputs={"out": node.id},
            status=NodeStatus.COMPLETED,
        )


class FlakyToolExecutor:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(
        self,
        node: ToolOperator,
        inputs: dict[str, Any],
        context: Any,
    ) -> NodeResult:
        self.calls += 1
        if self.calls == 1:
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error="timeout while calling tool",
            )
        return NodeResult(
            outputs={"out": "recovered"},
            status=NodeStatus.COMPLETED,
        )


class OverlayRepairExecutor:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(
        self,
        node: ToolOperator,
        inputs: dict[str, Any],
        context: Any,
    ) -> NodeResult:
        self.calls += 1
        if node.tool_id != "tool:fixed":
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error="unknown tool config",
            )
        return NodeResult(
            outputs={"out": "fixed"},
            status=NodeStatus.COMPLETED,
        )


class HappyPathExecutor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(
        self,
        node: ToolOperator,
        inputs: dict[str, Any],
        context: Any,
    ) -> NodeResult:
        self.calls.append(node.id)
        value = node.id if "inp" not in inputs else f"{inputs['inp']}->{node.id}"
        return NodeResult(outputs={"out": value}, status=NodeStatus.COMPLETED)


class CodeRepairExecutor:
    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    async def execute(
        self,
        node: CodeOperator,
        inputs: dict[str, Any],
        context: Any,
    ) -> NodeResult:
        self.calls[node.id] = self.calls.get(node.id, 0) + 1
        if node.code == "print('fixed')":
            return NodeResult(
                outputs={"out": "fixed"},
                status=NodeStatus.COMPLETED,
            )
        return NodeResult(
            outputs={},
            status=NodeStatus.FAILED,
            error="code exploded",
        )


class DynamicCostResumeExecutor:
    def __init__(self) -> None:
        self.child_calls = 0

    async def execute(
        self,
        node: ToolOperator,
        inputs: dict[str, Any],
        context: Any,
    ) -> NodeResult:
        if node.id == "spawn":
            envelope = await context.run_child_workflow(
                DynamicExpansionSpec(mode="sub_graph", ref="child"),
                {"seed": "alpha"},
                parent_node_id=node.id,
            )
            return NodeResult(
                outputs={"out": envelope.outputs["value"]},
                status=NodeStatus.COMPLETED,
            )

        if node.id == "child":
            self.child_calls += 1
            context.cost_tracker.record(
                node.id,
                "gpt-4o-mini",
                {"prompt_tokens": 1000, "completion_tokens": 1000},
            )
            return NodeResult(
                outputs={"value": f"child-{self.child_calls}"},
                status=NodeStatus.COMPLETED,
            )

        return NodeResult(
            outputs={"out": f"{inputs['inp']}|tail"},
            status=NodeStatus.COMPLETED,
        )


class OverlayRepairResumeExecutor:
    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    async def execute(
        self,
        node: ToolOperator,
        inputs: dict[str, Any],
        context: Any,
    ) -> NodeResult:
        self.calls[node.id] = self.calls.get(node.id, 0) + 1

        if node.id == "repairable-tool":
            if node.tool_id != "tool:fixed":
                return NodeResult(
                    outputs={},
                    status=NodeStatus.FAILED,
                    error="unknown tool config",
                )
            context.cost_tracker.record(
                node.id,
                "gpt-4o-mini",
                {"prompt_tokens": 1000, "completion_tokens": 1000},
            )
            return NodeResult(
                outputs={"out": "fixed"},
                status=NodeStatus.COMPLETED,
            )

        return NodeResult(
            outputs={"out": f"{inputs['inp']}|tail"},
            status=NodeStatus.COMPLETED,
        )


@pytest.mark.asyncio
async def test_max_duration_returns_partial_result_and_resume_uses_persisted_policy() -> None:
    checkpoint_store = RecordingCheckpointStore()
    registry = ExecutorRegistry()
    registry.register("tool_operator", DelayExecutor())
    graph = _chain_graph([0.01, 0.06, 0.01])

    engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=True,
            eager_dispatch=True,
            checkpoint_batch_size=99,
            checkpoint_interval_sec=99.0,
        ),
        executor_registry=registry,
        checkpoint_store=checkpoint_store,
    )

    result = await engine.run(
        graph,
        run_id="duration-limit-run",
        run_policy={"profile": "long_running", "max_duration": 0.02},
    )

    assert result.success is True
    assert result.metadata["partial"] is True
    assert result.metadata["resumable"] is True
    assert result.metadata["stop_reason"] == "duration_limit"
    assert result.metadata["pending_node_ids"] == ["n2"]
    assert checkpoint_store.saves[-1]["checkpoint_data"]["effective_run_policy"]["profile"] == "long_running"

    resumed_engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=True,
            eager_dispatch=True,
            checkpoint_batch_size=50,
            checkpoint_interval_sec=50.0,
        ),
        executor_registry=registry,
        checkpoint_store=checkpoint_store,
    )
    resumed = await resumed_engine.resume(
        graph,
        "duration-limit-run",
        run_policy={"max_duration": 1.0},
    )

    assert resumed.success is True
    assert resumed.outputs["out"].endswith("n2")
    assert resumed.metadata["effective_run_policy"]["profile"] == "long_running"
    assert resumed.metadata["effective_run_policy"]["checkpoint_batch_size"] == 1
    assert resumed.metadata["run_phase"] == "completed"


@pytest.mark.asyncio
async def test_long_running_policy_precedence_and_authored_retry_policy_are_preserved() -> None:
    registry = ExecutorRegistry()
    inspector = PolicyInspectExecutor()
    registry.register("tool_operator", inspector)

    implicit_graph = Graph(
        nodes=[
            ToolOperator(
                id="implicit",
                name="implicit",
                tool_id="tool:implicit",
                output_ports=[_out()],
            )
        ],
        entry_points=["implicit"],
        exit_points=["implicit"],
    )
    explicit_graph = Graph(
        nodes=[
            ToolOperator(
                id="explicit",
                name="explicit",
                tool_id="tool:explicit",
                retry_policy=RetryPolicy(max_retries=7),
                output_ports=[_out()],
            )
        ],
        entry_points=["explicit"],
        exit_points=["explicit"],
    )

    engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=False,
            checkpoint_batch_size=9,
        ),
        executor_registry=registry,
    )

    implicit = await engine.run(
        implicit_graph,
        run_policy={"profile": "long_running", "checkpoint_batch_size": 3},
    )
    explicit = await engine.run(
        explicit_graph,
        run_policy={"profile": "long_running"},
    )

    assert implicit.success is True
    assert implicit.metadata["effective_run_policy"]["profile"] == "long_running"
    assert implicit.metadata["effective_run_policy"]["checkpoint_batch_size"] == 3
    assert inspector.seen_retry_policies["implicit"] == 2
    assert explicit.success is True
    assert inspector.seen_retry_policies["explicit"] == 7


@pytest.mark.asyncio
async def test_max_cost_stops_at_safe_boundary_with_checkpointed_partial_result() -> None:
    checkpoint_store = RecordingCheckpointStore()
    registry = ExecutorRegistry()
    registry.register("tool_operator", CostExecutor())
    graph = _chain_graph([0.0, 0.0])

    engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=True,
            eager_dispatch=True,
            checkpoint_batch_size=99,
            checkpoint_interval_sec=99.0,
        ),
        executor_registry=registry,
        checkpoint_store=checkpoint_store,
    )

    result = await engine.run(
        graph,
        run_id="cost-limit-run",
        run_policy={"max_cost": 0.000001},
    )

    assert result.metadata["stop_reason"] == "cost_limit"
    assert result.metadata["partial"] is True
    assert result.metadata["pending_node_ids"] == ["n1"]
    assert checkpoint_store.saves[-1]["checkpoint_data"]["stop_reason"] == "cost_limit"


@pytest.mark.asyncio
async def test_runtime_self_healing_retries_transient_tool_failure() -> None:
    executor = FlakyToolExecutor()
    registry = ExecutorRegistry()
    registry.register("tool_operator", executor)
    graph = Graph(
        nodes=[
            ToolOperator(
                id="flaky",
                name="flaky",
                tool_id="tool:flaky",
                output_ports=[_out()],
            )
        ],
        entry_points=["flaky"],
        exit_points=["flaky"],
    )

    engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=False,
            runtime_self_healing_enabled=True,
        ),
        executor_registry=registry,
    )

    result = await engine.run(graph)

    assert result.success is True
    assert result.outputs["out"] == "recovered"
    assert executor.calls == 2
    lineage = result.metadata["repair_lineage"]["flaky"]
    assert lineage["attempts"][0]["kind"] == "retry"
    assert lineage["last_summary"]["repair_attempted"] == "retry"


@pytest.mark.asyncio
async def test_runtime_self_healing_does_not_alias_legacy_worker_cache_modes() -> None:
    executor = FlakyToolExecutor()
    registry = ExecutorRegistry()
    registry.register("tool_operator", executor)
    flaky_graph = Graph(
        nodes=[
            ToolOperator(
                id="flaky",
                name="flaky",
                tool_id="tool:flaky",
                output_ports=[_out()],
            )
        ],
        entry_points=["flaky"],
        exit_points=["flaky"],
    )

    flaky_engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=False,
            runtime_self_healing_enabled=True,
        ),
        executor_registry=registry,
    )
    flaky_result = await flaky_engine.run(flaky_graph)
    assert flaky_result.success is True
    assert flaky_result.outputs["out"] == "recovered"

    linear_graph = Graph(
        nodes=[
            CodeOperator(
                id="a",
                name="A",
                code="result = {'value': 1}",
                output_ports=[OutputPort(name="value")],
            ),
            CodeOperator(
                id="b",
                name="B",
                code="result = {'value': value * 10}",
                input_ports=[InputPort(name="value")],
                output_ports=[OutputPort(name="value")],
            ),
            CodeOperator(
                id="c",
                name="C",
                code="result = {'value': value + 5}",
                input_ports=[InputPort(name="value")],
                output_ports=[OutputPort(name="value")],
            ),
        ],
        edges=[
            DataEdge(
                id="e1",
                source_node_id="a",
                source_port="value",
                target_node_id="b",
                target_port="value",
            ),
            DataEdge(
                id="e2",
                source_node_id="b",
                source_port="value",
                target_node_id="c",
                target_port="value",
            ),
        ],
        entry_points=["a"],
        exit_points=["c"],
    )

    linear_result = await Engine(
        config=EngineConfig(checkpoint_enabled=False),
    ).run(linear_graph)

    assert linear_result.success is True
    assert linear_result.outputs["value"] == 15


@pytest.mark.asyncio
async def test_runtime_self_healing_recovers_invalid_json_with_mechanical_repair() -> None:
    provider = InvalidJSONProvider()
    graph = Graph(
        nodes=[
            LLMOperator(
                id="llm",
                name="llm",
                model="fake-model",
                prompt_template="Return structured output.",
                output_json_schema={
                    "type": "object",
                    "properties": {
                        "verdict": {"type": "string"},
                        "score": {"type": "number"},
                    },
                    "required": ["verdict", "score"],
                },
            )
        ],
        entry_points=["llm"],
        exit_points=["llm"],
    )

    engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=False,
            runtime_self_healing_enabled=True,
        ),
    )
    engine.provider_registry = FakeProviderRegistry(provider)

    result = await engine.run(graph)

    assert result.success is True
    assert result.outputs["verdict"] == "accept"
    assert provider.complete_calls == 1
    assert "mechanical_json_repair" in result.metadata["llm"]["runtime_repair"]["recovery_path"]


@pytest.mark.asyncio
async def test_runtime_self_healing_surfaces_schema_mismatch_exhaustion() -> None:
    provider = SchemaMismatchProvider()
    graph = Graph(
        nodes=[
            LLMOperator(
                id="llm",
                name="llm",
                model="fake-model",
                prompt_template="Return structured output.",
                output_json_schema={
                    "type": "object",
                    "properties": {
                        "verdict": {"type": "string"},
                        "score": {"type": "number"},
                    },
                    "required": ["verdict", "score"],
                },
            )
        ],
        entry_points=["llm"],
        exit_points=["llm"],
    )

    engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=False,
            runtime_self_healing_enabled=True,
            output_norm_max_retries=1,
        ),
    )
    engine.provider_registry = FakeProviderRegistry(provider)

    result = await engine.run(graph)

    assert result.success is False
    assert provider.complete_calls >= 2
    assert result.metadata["llm"]["runtime_repair"]["schema_reprompt_used"] is True
    assert "schema_reprompt_requested" in result.metadata["llm"]["runtime_repair"]["recovery_path"]


@pytest.mark.asyncio
async def test_runtime_self_healing_applies_tool_overlay_and_persists_repair_lineage() -> None:
    checkpoint_store = RecordingCheckpointStore()
    executor = OverlayRepairExecutor()
    registry = ExecutorRegistry()
    registry.register("tool_operator", executor)
    graph = Graph(
        nodes=[
            ToolOperator(
                id="repairable-tool",
                name="repairable-tool",
                tool_id="tool:missing",
                metadata={"repair_overlay": {"tool_id": "tool:fixed"}},
                output_ports=[_out()],
            )
        ],
        entry_points=["repairable-tool"],
        exit_points=["repairable-tool"],
    )

    engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=True,
            runtime_self_healing_enabled=True,
        ),
        executor_registry=registry,
        checkpoint_store=checkpoint_store,
    )

    result = await engine.run(graph, run_id="repair-lineage-run")

    assert result.success is True
    assert result.outputs["out"] == "fixed"
    assert executor.calls == 2
    lineage = result.metadata["repair_lineage"]["repairable-tool"]
    assert lineage["attempts"][0]["kind"] == "parameter_fix"
    assert lineage["attempts"][0]["diagnostic_record"]["error_category"] == "tool_failure"
    assert lineage["last_summary"]["repair_attempted"] == "parameter_fix"
    assert lineage["last_summary"]["post_run_repair_level"] == "parameter"
    persisted = checkpoint_store.saves[-1]["state"]["run_state"]["repair_lineage"]["repairable-tool"]
    assert persisted["attempts"][0]["kind"] == "parameter_fix"


@pytest.mark.asyncio
async def test_runtime_self_healing_filters_structural_overlay_fields_and_keeps_graph_immutable() -> None:
    executor = OverlayRepairExecutor()
    registry = ExecutorRegistry()
    registry.register("tool_operator", executor)
    graph = Graph(
        nodes=[
            ToolOperator(
                id="repairable-tool",
                name="repairable-tool",
                tool_id="tool:missing",
                metadata={
                    "repair_overlay": {
                        "tool_id": "tool:fixed",
                        "body_graph": "should_not_apply",
                        "nodes": [{"id": "new-node"}],
                    }
                },
                output_ports=[_out()],
            )
        ],
        entry_points=["repairable-tool"],
        exit_points=["repairable-tool"],
    )

    engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=False,
            runtime_self_healing_enabled=True,
        ),
        executor_registry=registry,
    )

    result = await engine.run(graph)

    assert result.success is True
    assert graph.node_by_id("repairable-tool").tool_id == "tool:missing"
    attempt_overlay = result.metadata["repair_lineage"]["repairable-tool"]["attempts"][0]["overlay"]["patch"]
    assert attempt_overlay == {"tool_id": "tool:fixed"}


@pytest.mark.asyncio
async def test_runtime_self_healing_happy_path_does_not_retry_successful_nodes() -> None:
    executor = HappyPathExecutor()
    registry = ExecutorRegistry()
    registry.register("tool_operator", executor)
    graph = _chain_graph([0.0, 0.0])

    engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=False,
            runtime_self_healing_enabled=True,
        ),
        executor_registry=registry,
    )

    result = await engine.run(graph)

    assert result.success is True
    assert executor.calls == ["n0", "n1"]
    assert result.metadata["repair_lineage"]["n0"]["attempts"] == []
    assert result.metadata["repair_lineage"]["n1"]["attempts"] == []


@pytest.mark.asyncio
async def test_runtime_self_healing_resume_preserves_repair_lineage_after_partial_stop() -> None:
    checkpoint_store = RecordingCheckpointStore()
    executor = OverlayRepairResumeExecutor()
    registry = ExecutorRegistry()
    registry.register("tool_operator", executor)
    graph = Graph(
        nodes=[
            ToolOperator(
                id="repairable-tool",
                name="repairable-tool",
                tool_id="tool:missing",
                metadata={"repair_overlay": {"tool_id": "tool:fixed"}},
                output_ports=[_out()],
            ),
            ToolOperator(
                id="tail",
                name="tail",
                tool_id="tool:tail",
                input_ports=[_port("inp")],
                output_ports=[_out()],
            ),
        ],
        edges=[
            DataEdge(
                id="repair-to-tail",
                source_node_id="repairable-tool",
                source_port="out",
                target_node_id="tail",
                target_port="inp",
            )
        ],
        entry_points=["repairable-tool"],
        exit_points=["tail"],
    )

    engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=True,
            eager_dispatch=True,
            runtime_self_healing_enabled=True,
        ),
        executor_registry=registry,
        checkpoint_store=checkpoint_store,
    )

    partial = await engine.run(
        graph,
        run_id="repair-lineage-resume",
        run_policy={"max_cost": 0.000001},
    )

    assert partial.metadata["stop_reason"] == "cost_limit"
    assert partial.metadata["pending_node_ids"] == ["tail"]
    assert partial.metadata["repair_lineage"]["repairable-tool"]["attempts"][0]["kind"] == "parameter_fix"
    assert executor.calls["repairable-tool"] == 2

    resumed_engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=True,
            eager_dispatch=True,
            runtime_self_healing_enabled=True,
        ),
        executor_registry=registry,
        checkpoint_store=checkpoint_store,
    )

    resumed = await resumed_engine.resume(
        graph,
        "repair-lineage-resume",
        run_policy={"max_cost": 1.0},
    )

    assert resumed.success is True
    assert resumed.outputs["out"] == "fixed|tail"
    assert executor.calls["repairable-tool"] == 2
    assert resumed.metadata["repair_lineage"]["repairable-tool"]["attempts"][0]["kind"] == "parameter_fix"


@pytest.mark.asyncio
async def test_dynamic_topology_child_workflow_reuses_completed_child_result() -> None:
    executor = DynamicTopologyExecutor()
    registry = ExecutorRegistry()
    registry.register("tool_operator", executor)

    child_graph = Graph(
        nodes=[
            ToolOperator(
                id="child",
                name="child",
                tool_id="tool:child",
                output_ports=[OutputPort(name="value", json_schema={"type": "string"})],
            )
        ],
        entry_points=["child"],
        exit_points=["child"],
    )
    graph = Graph(
        nodes=[
            ToolOperator(
                id="spawn",
                name="spawn",
                tool_id="tool:spawn",
                output_ports=[OutputPort(name="out", json_schema={"type": "string"})],
            )
        ],
        sub_graphs={"child": child_graph},
        entry_points=["spawn"],
        exit_points=["spawn"],
    )

    engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=False,
            dynamic_topology_enabled=True,
        ),
        executor_registry=registry,
    )

    result = await engine.run(graph)

    assert result.success is True
    assert result.outputs["out"] == "child-1|child-1"
    assert executor.child_calls == 1
    assert result.metadata["dynamic_topology"]["total_children"] == 1
    assert len(result.metadata["dynamic_topology"]["records"]) == 1


@pytest.mark.asyncio
async def test_dynamic_topology_budget_rollup_and_resume_do_not_rerun_child() -> None:
    checkpoint_store = RecordingCheckpointStore()
    executor = DynamicCostResumeExecutor()
    registry = ExecutorRegistry()
    registry.register("tool_operator", executor)

    child_graph = Graph(
        nodes=[
            ToolOperator(
                id="child",
                name="child",
                tool_id="tool:child",
                output_ports=[OutputPort(name="value", json_schema={"type": "string"})],
            )
        ],
        entry_points=["child"],
        exit_points=["child"],
    )
    graph = Graph(
        nodes=[
            ToolOperator(
                id="spawn",
                name="spawn",
                tool_id="tool:spawn",
                output_ports=[OutputPort(name="out", json_schema={"type": "string"})],
            ),
            ToolOperator(
                id="tail",
                name="tail",
                tool_id="tool:tail",
                input_ports=[_port("inp")],
                output_ports=[OutputPort(name="out", json_schema={"type": "string"})],
            ),
        ],
        edges=[
            DataEdge(
                id="spawn-to-tail",
                source_node_id="spawn",
                source_port="out",
                target_node_id="tail",
                target_port="inp",
            )
        ],
        sub_graphs={"child": child_graph},
        entry_points=["spawn"],
        exit_points=["tail"],
    )

    engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=True,
            eager_dispatch=True,
            dynamic_topology_enabled=True,
        ),
        executor_registry=registry,
        checkpoint_store=checkpoint_store,
    )

    partial = await engine.run(
        graph,
        run_id="dynamic-budget-resume",
        run_policy={"max_cost": 0.000001},
    )

    assert partial.metadata["stop_reason"] == "cost_limit"
    assert partial.metadata["partial"] is True
    assert partial.metadata["pending_node_ids"] == ["tail"]
    assert executor.child_calls == 1

    resumed_engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=True,
            eager_dispatch=True,
            dynamic_topology_enabled=True,
        ),
        executor_registry=registry,
        checkpoint_store=checkpoint_store,
    )

    resumed = await resumed_engine.resume(
        graph,
        "dynamic-budget-resume",
        run_policy={"max_cost": 1.0},
    )

    assert resumed.success is True
    assert resumed.outputs["out"] == "child-1|tail"
    assert executor.child_calls == 1
    assert resumed.metadata["dynamic_topology"]["total_children"] == 1


@pytest.mark.asyncio
async def test_dynamic_topology_template_branch_uses_engine_child_runtime() -> None:
    executor = DynamicTemplateBranchExecutor()
    registry = ExecutorRegistry()
    registry.register("tool_operator", executor)

    child_graph = Graph(
        nodes=[
            ToolOperator(
                id="child",
                name="child",
                tool_id="tool:child",
                output_ports=[OutputPort(name="value", json_schema={"type": "string"})],
            )
        ],
        entry_points=["child"],
        exit_points=["child"],
    )
    graph = Graph(
        nodes=[
            ToolOperator(
                id="spawn",
                name="spawn",
                tool_id="tool:spawn",
                output_ports=[OutputPort(name="out", json_schema={"type": "string"})],
            )
        ],
        sub_graphs={"child": child_graph},
        entry_points=["spawn"],
        exit_points=["spawn"],
    )

    engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=False,
            dynamic_topology_enabled=True,
        ),
        executor_registry=registry,
    )

    result = await engine.run(graph)

    assert result.success is True
    assert result.outputs["out"] == "template-1"
    assert executor.child_calls == 1
    assert result.metadata["dynamic_topology"]["total_children"] == 1
    record = next(iter(result.metadata["dynamic_topology"]["records"].values()))
    assert record["metadata"]["mode"] == "template_branch"


@pytest.mark.asyncio
async def test_dynamic_topology_child_timeout_is_actionable() -> None:
    registry = ExecutorRegistry()
    registry.register("tool_operator", DynamicTimeoutExecutor())

    child_graph = Graph(
        nodes=[
            ToolOperator(
                id="child",
                name="child",
                tool_id="tool:child",
                tool_config={"delay": 0.05},
                output_ports=[OutputPort(name="value", json_schema={"type": "string"})],
            )
        ],
        entry_points=["child"],
        exit_points=["child"],
    )
    graph = Graph(
        nodes=[
            ToolOperator(
                id="spawn",
                name="spawn",
                tool_id="tool:spawn",
                output_ports=[OutputPort(name="out", json_schema={"type": "string"})],
            )
        ],
        sub_graphs={"child": child_graph},
        entry_points=["spawn"],
        exit_points=["spawn"],
    )

    engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=False,
            dynamic_topology_enabled=True,
        ),
        executor_registry=registry,
    )

    result = await engine.run(graph)

    assert result.success is False
    assert "timed out" in result.errors["spawn"].lower()
    assert result.metadata["dynamic_topology"]["total_children"] == 1
    failed_record = next(iter(result.metadata["dynamic_topology"]["records"].values()))
    assert failed_record["status"] == "failed"
    assert "timed out" in failed_record["metadata"]["error"].lower()


@pytest.mark.asyncio
async def test_dynamic_topology_boundary_validation_failure_is_actionable() -> None:
    executor = DynamicBoundaryExecutor()
    registry = ExecutorRegistry()
    registry.register("tool_operator", executor)

    child_graph = Graph(
        nodes=[
            ToolOperator(
                id="child",
                name="child",
                tool_id="tool:child",
                output_ports=[OutputPort(name="value", json_schema={"type": "string"})],
            )
        ],
        entry_points=["child"],
        exit_points=["child"],
    )
    graph = Graph(
        nodes=[
            ToolOperator(
                id="spawn",
                name="spawn",
                tool_id="tool:spawn",
                output_ports=[OutputPort(name="out", json_schema={"type": "string"})],
            )
        ],
        sub_graphs={"child": child_graph},
        entry_points=["spawn"],
        exit_points=["spawn"],
    )

    engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=False,
            dynamic_topology_enabled=True,
        ),
        executor_registry=registry,
    )

    result = await engine.run(graph)

    assert result.success is False
    assert "input validation failed" in result.errors["spawn"].lower()
    assert executor.child_calls == 0
    assert result.metadata["dynamic_topology"]["total_children"] == 1
    assert len(result.metadata["dynamic_topology"]["records"]) == 1
    assert result.metadata["dynamic_topology"]["completed_calls"] == {}
    failed_record = next(iter(result.metadata["dynamic_topology"]["records"].values()))
    assert failed_record["status"] == "failed"
    assert "validation failed" in failed_record["metadata"]["error"].lower()


@pytest.mark.asyncio
async def test_dynamic_topology_spawn_limits_fail_without_caching_failed_child() -> None:
    executor = DynamicSpawnLimitExecutor()
    registry = ExecutorRegistry()
    registry.register("tool_operator", executor)

    child_graph = Graph(
        nodes=[
            ToolOperator(
                id="child",
                name="child",
                tool_id="tool:child",
                output_ports=[OutputPort(name="value", json_schema={"type": "string"})],
            )
        ],
        entry_points=["child"],
        exit_points=["child"],
    )
    graph = Graph(
        nodes=[
            ToolOperator(
                id="spawn",
                name="spawn",
                tool_id="tool:spawn",
                output_ports=[OutputPort(name="out", json_schema={"type": "string"})],
            )
        ],
        sub_graphs={"child": child_graph},
        entry_points=["spawn"],
        exit_points=["spawn"],
    )

    engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=False,
            dynamic_topology_enabled=True,
        ),
        executor_registry=registry,
    )

    result = await engine.run(graph)

    assert result.success is False
    assert "max_spawns_per_node=1" in result.errors["spawn"]
    assert executor.child_calls == 1
    assert result.metadata["dynamic_topology"]["total_children"] == 2
    assert len(result.metadata["dynamic_topology"]["records"]) == 2
    assert len(result.metadata["dynamic_topology"]["completed_calls"]) == 1
    statuses = sorted(
        record["status"]
        for record in result.metadata["dynamic_topology"]["records"].values()
    )
    assert statuses == ["completed", "failed"]


@pytest.mark.asyncio
async def test_runtime_self_healing_does_not_retry_static_code_operator() -> None:
    executor = CodeRepairExecutor()
    registry = ExecutorRegistry()
    registry.register("code_operator", executor)
    graph = Graph(
        nodes=[
            CodeOperator(
                id="static-code",
                name="static-code",
                code="print('broken')",
                output_ports=[_out()],
            )
        ],
        entry_points=["static-code"],
        exit_points=["static-code"],
    )

    engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=False,
            runtime_self_healing_enabled=True,
        ),
        executor_registry=registry,
    )

    result = await engine.run(graph)

    assert result.success is False
    assert executor.calls["static-code"] == 1
    assert result.metadata["static-code"]["runtime_repair_summary"]["repair_attempted"] == "advisory"


@pytest.mark.asyncio
async def test_runtime_self_healing_repairs_explicitly_repairable_code_operator() -> None:
    executor = CodeRepairExecutor()
    registry = ExecutorRegistry()
    registry.register("code_operator", executor)
    graph = Graph(
        nodes=[
            CodeOperator(
                id="repairable-code",
                name="repairable-code",
                code="print('broken')",
                metadata={
                    "repairable_code": True,
                    "repair_overlay": {"code": "print('fixed')"},
                },
                output_ports=[_out()],
            )
        ],
        entry_points=["repairable-code"],
        exit_points=["repairable-code"],
    )

    engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=False,
            runtime_self_healing_enabled=True,
        ),
        executor_registry=registry,
    )

    result = await engine.run(graph)

    assert result.success is True
    assert result.outputs["out"] == "fixed"
    assert executor.calls["repairable-code"] == 2
    assert result.metadata["repair_lineage"]["repairable-code"]["attempts"][0]["kind"] == "code_fix"


@pytest.mark.asyncio
async def test_runtime_self_healing_emits_repair_failed_event_when_budget_exhausted() -> None:
    events: list[dict[str, Any]] = []

    async def capture(event: Any) -> None:
        events.append(event.to_dict())

    registry = ExecutorRegistry()
    registry.register("tool_operator", AlwaysFailToolExecutor())
    graph = Graph(
        nodes=[
            ToolOperator(
                id="broken",
                name="broken",
                tool_id="tool:broken",
                output_ports=[OutputPort(name="out", json_schema={"type": "string"})],
            )
        ],
        entry_points=["broken"],
        exit_points=["broken"],
    )

    engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=False,
            runtime_self_healing_enabled=True,
        ),
        executor_registry=registry,
        event_callback=capture,
    )

    result = await engine.run(graph)

    assert result.success is False
    event_types = [event["event_type"] for event in events if event.get("node_id") == "broken"]
    assert "node_repair_started" in event_types
    assert "node_repair_applied" in event_types
    assert "node_repair_failed" in event_types
    assert result.metadata["broken"]["runtime_repair_summary"]["repair_attempted"] == "advisory"


@pytest.mark.asyncio
async def test_engine_cleans_active_run_state_when_execute_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = ExecutorRegistry()
    registry.register("tool_operator", DelayExecutor())
    graph = _chain_graph([0.0])
    engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=False,
            eager_dispatch=True,
        ),
        executor_registry=registry,
    )

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("context setup exploded")

    monkeypatch.setattr(engine, "_make_context", boom)

    with pytest.raises(RuntimeError, match="context setup exploded"):
        await engine.run(graph, run_id="cleanup-run")

    assert "cleanup-run" not in engine._active_run_states


@pytest.mark.asyncio
async def test_dynamic_topology_workflow_ref_uses_engine_loader() -> None:
    executor = DynamicWorkflowRefExecutor()
    registry = ExecutorRegistry()
    registry.register("tool_operator", executor)

    child_graph = Graph(
        nodes=[
            ToolOperator(
                id="child",
                name="child",
                tool_id="tool:child",
                output_ports=[OutputPort(name="value", json_schema={"type": "string"})],
            )
        ],
        entry_points=["child"],
        exit_points=["child"],
    )
    graph = Graph(
        nodes=[
            ToolOperator(
                id="spawn",
                name="spawn",
                tool_id="tool:spawn",
                output_ports=[OutputPort(name="out", json_schema={"type": "string"})],
            )
        ],
        entry_points=["spawn"],
        exit_points=["spawn"],
    )

    engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=False,
            dynamic_topology_enabled=True,
        ),
        executor_registry=registry,
        workflow_loader=lambda workflow_id: (
            child_graph.model_dump() if workflow_id == "child-workflow" else None
        ),
    )

    result = await engine.run(graph)

    assert result.success is True
    assert result.outputs["out"] == "workflow-1"
    assert executor.child_calls == 1
    record = next(iter(result.metadata["dynamic_topology"]["records"].values()))
    assert record["metadata"]["mode"] == "workflow_ref"
    assert record["template_key"] == "child-workflow"


@pytest.mark.asyncio
async def test_dynamic_topology_direct_child_api_respects_feature_flag() -> None:
    executor = DynamicWorkflowRefExecutor()
    registry = ExecutorRegistry()
    registry.register("tool_operator", executor)
    graph = Graph(
        nodes=[
            ToolOperator(
                id="spawn",
                name="spawn",
                tool_id="tool:spawn",
                output_ports=[OutputPort(name="out", json_schema={"type": "string"})],
            )
        ],
        entry_points=["spawn"],
        exit_points=["spawn"],
    )

    engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=False,
            dynamic_topology_enabled=False,
        ),
        executor_registry=registry,
        workflow_loader=lambda workflow_id: None,
    )

    result = await engine.run(graph)

    assert result.success is False
    assert "dynamic topology is disabled" in result.errors["spawn"].lower()


@pytest.mark.asyncio
async def test_dynamic_topology_workflow_ref_requires_loader() -> None:
    executor = DynamicWorkflowRefExecutor()
    registry = ExecutorRegistry()
    registry.register("tool_operator", executor)
    graph = Graph(
        nodes=[
            ToolOperator(
                id="spawn",
                name="spawn",
                tool_id="tool:spawn",
                output_ports=[OutputPort(name="out", json_schema={"type": "string"})],
            )
        ],
        entry_points=["spawn"],
        exit_points=["spawn"],
    )

    engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=False,
            dynamic_topology_enabled=True,
        ),
        executor_registry=registry,
    )

    result = await engine.run(graph)

    assert result.success is False
    assert "workflow_loader" in result.errors["spawn"].lower()


@pytest.mark.asyncio
async def test_long_running_profile_critical_node_checkpoint_and_progress_payload() -> None:
    checkpoint_store = RecordingCheckpointStore()
    events: list[dict[str, Any]] = []

    async def capture(event: Any) -> None:
        events.append(event.to_dict())

    registry = ExecutorRegistry()
    registry.register("tool_operator", DelayExecutor())

    n0 = _tool("n0", delay=0.01)
    critical = _tool("critical", delay=0.01)
    critical.tags = ["critical"]
    n2 = _tool("n2", delay=0.01)
    graph = Graph(
        nodes=[n0, critical, n2],
        edges=[
            DataEdge(
                id="e1",
                source_node_id="n0",
                source_port="out",
                target_node_id="critical",
                target_port="inp",
            ),
            DataEdge(
                id="e2",
                source_node_id="critical",
                source_port="out",
                target_node_id="n2",
                target_port="inp",
            ),
        ],
        entry_points=["n0"],
        exit_points=["n2"],
    )

    engine = Engine(
        config=EngineConfig(
            checkpoint_enabled=True,
            eager_dispatch=True,
            checkpoint_batch_size=99,
            checkpoint_interval_sec=99.0,
        ),
        executor_registry=registry,
        checkpoint_store=checkpoint_store,
        event_callback=capture,
    )

    result = await engine.run(
        graph,
        run_id="critical-progress-run",
        run_policy={"profile": "long_running"},
    )

    assert result.success is True
    assert any(
        save["checkpoint_data"]["checkpoint_trigger"] == "critical_node"
        for save in checkpoint_store.saves
    )
    progress_events = [event for event in events if event["event_type"] == "run_progress"]
    assert progress_events
    payload = progress_events[-1]["data"]
    for key in (
        "completed_nodes",
        "total_nodes",
        "remaining_nodes",
        "progress_fraction",
        "completed_node_ids",
        "pending_node_ids",
        "active_node_ids",
        "ready_node_ids",
        "failed_node_ids",
        "eta_seconds",
        "stage_label",
        "phase",
    ):
        assert key in payload
    assert payload["total_nodes"] == 3
    assert result.metadata["effective_run_policy"]["profile"] == "long_running"
    assert result.metadata["progress"]["total_nodes"] == 3


class ControlledLLMExecutor:
    def __init__(
        self,
        handler: Callable[[LLMOperator, dict[str, Any], ExecutionContext], NodeResult],
    ) -> None:
        self._handler = handler
        self.calls: list[tuple[str, str]] = []

    async def execute(
        self,
        node: LLMOperator,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        self.calls.append((context.run_id, node.id))
        return self._handler(node, inputs, context)


def _auto_recovery_llm_node(node_id: str, *, needs_input: bool = False) -> LLMOperator:
    return LLMOperator(
        id=node_id,
        name=node_id,
        model="test-model",
        prompt_template="{text}" if needs_input else "hello",
        input_ports=(
            [InputPort(name="text", json_schema={"type": "string"})]
            if needs_input
            else []
        ),
        output_ports=[OutputPort(name="text", json_schema={"type": "string"})],
    )


def _auto_recovery_single_node_graph(node_id: str = "task") -> Graph:
    node = _auto_recovery_llm_node(node_id)
    return Graph(
        nodes=[node],
        edges=[],
        entry_points=[node_id],
        exit_points=[node_id],
    )


def _auto_recovery_two_failure_graph() -> Graph:
    node_a = _auto_recovery_llm_node("node-a")
    node_b = _auto_recovery_llm_node("node-b", needs_input=True)
    node_c = _auto_recovery_llm_node("node-c")
    return Graph(
        nodes=[node_a, node_b, node_c],
        edges=[
            DataEdge(
                id="edge-a-b",
                source_node_id="node-a",
                source_port="text",
                target_node_id="node-b",
                target_port="text",
            ),
        ],
        entry_points=["node-a", "node-c"],
        exit_points=["node-b", "node-c"],
    )


def _auto_recovery_engine_config(tmp_path: Any) -> EngineConfig:
    return EngineConfig(
        checkpoint_enabled=True,
        checkpoint_dir=str(tmp_path / "checkpoints"),
        memory_enabled=False,
        state_store_enabled=False,
        runtime_self_healing_enabled=True,
        checkpoint_batch_size=1,
        checkpoint_interval_sec=0.01,
    )


def _build_auto_recovery_engine(
    tmp_path: Any,
    executor: ControlledLLMExecutor,
) -> Engine:
    registry = ExecutorRegistry()
    registry.register("llm_operator", executor)
    return Engine(
        config=_auto_recovery_engine_config(tmp_path),
        executor_registry=registry,
        provider_registry=object(),
        embedding_registry=object(),
    )


def _build_auto_recovery_run_manager(
    tmp_path: Any,
    executor: ControlledLLMExecutor,
) -> RunManager:
    manager = RunManager(engine_config=_auto_recovery_engine_config(tmp_path))

    def _make_executor_registry(self) -> ExecutorRegistry:
        registry = ExecutorRegistry()
        registry.register("llm_operator", executor)
        return registry

    def _make_engine(self, **kwargs: Any) -> Engine:
        return Engine(
            config=self._config,
            workflow_loader=self._graph_loader,
            model_gateway=self._model_gateway,
            provider_registry=object(),
            embedding_registry=object(),
            **kwargs,
        )

    manager._make_executor_registry = MethodType(_make_executor_registry, manager)
    manager._make_engine = MethodType(_make_engine, manager)
    return manager


def _auto_recovery_failure_result(message: str = "provider timeout") -> NodeResult:
    return NodeResult(
        status=NodeStatus.FAILED,
        error=message,
    )


def _auto_recovery_success_result(node_id: str) -> NodeResult:
    return NodeResult(
        outputs={"text": f"ok:{node_id}"},
        status=NodeStatus.COMPLETED,
    )


@pytest.mark.asyncio
async def test_runtime_repair_selects_smallest_checkpoint_rerun_scope(tmp_path: Any) -> None:
    graph = _auto_recovery_two_failure_graph()
    executor = ControlledLLMExecutor(
        lambda node, _inputs, _context: (
            _auto_recovery_failure_result()
            if node.id in {"node-a", "node-c"}
            else _auto_recovery_success_result(node.id)
        ),
    )
    engine = _build_auto_recovery_engine(tmp_path, executor)

    result = await engine.run(graph, run_id="run-smallest")

    assert result.success is False
    assert result.metadata["automatic_recovery"]["selected_action"] == "rerun_from_checkpoint"
    assert result.metadata["automatic_recovery"]["target_node_id"] == "node-c"
    assert result.metadata["automatic_recovery"]["scope_size"] == 1
    assert result.metadata["node-a"]["runtime_repair_summary"]["automatic_recovery"]["status"] == "ready"
    assert result.metadata["node-c"]["runtime_repair_summary"]["automatic_recovery"]["status"] == "ready"


@pytest.mark.asyncio
async def test_run_manager_starts_bounded_automatic_checkpoint_rerun(tmp_path: Any) -> None:
    graph = _auto_recovery_single_node_graph("task")
    executor = ControlledLLMExecutor(
        lambda node, _inputs, context: (
            _auto_recovery_success_result(node.id)
            if context.run_id.startswith("rerun-")
            else _auto_recovery_failure_result()
        ),
    )
    manager = _build_auto_recovery_run_manager(tmp_path, executor)

    parent = await manager.start_run(graph, graph_id="wf-runtime", run_id="run-parent")
    await manager._tasks[parent.run_id]

    source_record = manager.get_run(parent.run_id)
    assert source_record is not None
    recovery_run_id = source_record.automatic_recovery["recovery_run_id"]

    await manager._tasks[recovery_run_id]
    await asyncio.sleep(0.05)

    source_record = manager.get_run(parent.run_id)
    child_record = manager.get_run(recovery_run_id)
    assert source_record is not None
    assert child_record is not None

    assert source_record.status == RunStatus.FAILED
    assert source_record.snapshot()["automatic_recovery"]["status"] == "completed"
    assert source_record.snapshot()["automatic_recovery"]["success"] is True

    assert child_record.status == RunStatus.COMPLETED
    assert child_record.result is not None
    assert child_record.result.success is True
    assert child_record.result.metadata["automatic_recovery"]["source_run_id"] == parent.run_id
    assert child_record.result.metadata["__rerun_provenance__"]["source_checkpoint_id"] == parent.run_id


@pytest.mark.asyncio
async def test_automatic_recovery_carries_lineage_and_does_not_loop(tmp_path: Any) -> None:
    graph = _auto_recovery_single_node_graph("task")
    executor = ControlledLLMExecutor(
        lambda _node, _inputs, _context: _auto_recovery_failure_result(),
    )
    manager = _build_auto_recovery_run_manager(tmp_path, executor)

    parent = await manager.start_run(graph, graph_id="wf-runtime", run_id="run-parent")
    await manager._tasks[parent.run_id]

    source_record = manager.get_run(parent.run_id)
    assert source_record is not None
    recovery_run_id = source_record.automatic_recovery["recovery_run_id"]

    await manager._tasks[recovery_run_id]
    await asyncio.sleep(0.05)

    source_record = manager.get_run(parent.run_id)
    child_record = manager.get_run(recovery_run_id)
    assert source_record is not None
    assert child_record is not None

    assert len(manager._runs) == 2
    assert source_record.snapshot()["automatic_recovery"]["status"] == "exhausted"
    assert child_record.snapshot()["automatic_recovery"]["status"] == "exhausted"
    assert child_record.status == RunStatus.FAILED
    assert child_record.result is not None
    assert child_record.result.metadata["repair_lineage"]["task"]["remaining_budgets"]["rerun_from_checkpoint"] == 0
