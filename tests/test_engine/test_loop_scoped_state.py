"""Tests for loop-scoped state on GateNode (Plan 7-6).

Covers:
  7-1:  GateNode with state_schema serialization round-trip
  7-5:  Integration: while-gate loop with state_schema persists state across iterations
  7-9:  Regression: gate works with and without state_schema
  7-10: Iteration counter matches cycle count
  7-14: Sequential body nodes see each other's scope updates
  7-15: Scope injection only when active_loop_scope_id is set
  7-16: Output keys matching state_schema written to scope, others not
  7-17: First pass uses normal edges, scope activates on re-execution
"""

from __future__ import annotations

from typing import Any

import pytest

from dan.engine.checkpoint import NullCheckpointStore
from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
from dan.engine.executor import EngineConfig, ExecutionContext, ExecutorRegistry, NodeResult
from dan.engine.scheduler import Engine
from dan.engine.state import ExecutionState, NodeStatus
from dan.executors.control_flow import GateExecutor
from dan.models.control_flow import GateNode
from dan.models.edges import DataEdge
from dan.models.graph import Graph
from dan.models.nodes import CodeOperator, NodeBase
from dan.models.ports import InputPort, OutputPort


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config() -> EngineConfig:
    return EngineConfig(checkpoint_enabled=False)


def _engine(registry: ExecutorRegistry | None = None) -> Engine:
    return Engine(
        config=_config(),
        checkpoint_store=NullCheckpointStore(),
        executor_registry=registry,
    )


def _make_context(graph: Graph) -> ExecutionContext:
    state = ExecutionState(graph)
    return ExecutionContext(
        state=state,
        config=_config(),
        shared_context=SharedContextStore(),
        artifacts=ArtifactStore(),
        local_state=LocalStateManager(),
    )


# ---------------------------------------------------------------------------
# Mock executors
# ---------------------------------------------------------------------------


class _DispatchExecutor:
    """Routes to a per-node-ID executor; falls back to a passthrough."""

    def __init__(self, executors: dict[str, Any]):
        self._executors = executors

    async def execute(
        self, node: NodeBase, inputs: dict[str, Any], context: ExecutionContext,
    ) -> NodeResult:
        ex = self._executors.get(node.id)
        if ex is not None:
            return await ex.execute(node, inputs, context)
        return NodeResult(outputs=dict(inputs))


class _BodyExecutor:
    """Increments counter from inputs (via scope injection or edges)."""

    def __init__(self):
        self.call_count = 0
        self.received_inputs: list[dict] = []

    async def execute(
        self, node: NodeBase, inputs: dict[str, Any], context: ExecutionContext,
    ) -> NodeResult:
        self.call_count += 1
        self.received_inputs.append(dict(inputs))
        counter = inputs.get("counter", 0)
        return NodeResult(outputs={"counter": counter + 1})


class _ConstExecutor:
    """Returns a fixed output dict."""

    def __init__(self, outputs: dict):
        self._outputs = outputs

    async def execute(
        self, node: NodeBase, inputs: dict[str, Any], context: ExecutionContext,
    ) -> NodeResult:
        return NodeResult(outputs=dict(self._outputs))


class _SinkExecutor:
    """Captures whatever inputs arrive."""

    def __init__(self):
        self.received_inputs: dict[str, Any] = {}

    async def execute(
        self, node: NodeBase, inputs: dict[str, Any], context: ExecutionContext,
    ) -> NodeResult:
        self.received_inputs = dict(inputs)
        return NodeResult(outputs=inputs)


class _AppendExecutor:
    """Appends a fixed tag to the 'items' list from inputs."""

    def __init__(self, tag: str):
        self._tag = tag
        self.call_count = 0
        self.received_items: list[list] = []

    async def execute(
        self, node: NodeBase, inputs: dict[str, Any], context: ExecutionContext,
    ) -> NodeResult:
        self.call_count += 1
        items = list(inputs.get("items", []))
        self.received_items.append(list(items))
        items.append(self._tag)
        counter = inputs.get("counter", 0)
        return NodeResult(outputs={"items": items, "counter": counter + 1, "trigger": True})


# ---------------------------------------------------------------------------
# Graph builders
# ---------------------------------------------------------------------------


def _scoped_loop_graph(max_iterations: int = 10) -> Graph:
    """Build: src -> body -> gate(while) --continue-> body, gate --done-> sink.

    state_schema manages counter and try_more; body increments counter.
    Gate condition: counter < 3.
    """
    src = CodeOperator(
        id="src", name="Source", code="...",
        output_ports=[OutputPort(name="counter")],
    )
    body = CodeOperator(
        id="body", name="Body", code="...",
        input_ports=[InputPort(name="counter", required=False)],
        output_ports=[OutputPort(name="counter")],
    )
    gate = GateNode(
        id="gate", name="Loop Gate",
        condition="counter < 3",
        gate_mode="while",
        max_iterations=max_iterations,
        state_schema={
            "counter": {"type": "integer"},
            "try_more": {"type": "boolean"},
        },
        state_defaults={"counter": 0, "try_more": True},
        input_ports=[InputPort(name="counter")],
        output_ports=[OutputPort(name="continue"), OutputPort(name="done")],
    )
    sink = CodeOperator(
        id="sink", name="Sink", code="...",
        input_ports=[InputPort(name="data", required=False)],
        output_ports=[OutputPort(name="result")],
    )
    return Graph(
        nodes=[src, body, gate, sink],
        edges=[
            DataEdge(id="e_src_body", source_node_id="src", source_port="counter",
                     target_node_id="body", target_port="counter"),
            DataEdge(id="e_body_gate", source_node_id="body", source_port="counter",
                     target_node_id="gate", target_port="counter"),
            DataEdge(id="e_back", source_node_id="gate", source_port="continue",
                     target_node_id="body", target_port="counter"),
            DataEdge(id="e_done", source_node_id="gate", source_port="done",
                     target_node_id="sink", target_port="data"),
        ],
        entry_points=["src"],
        exit_points=["sink"],
    )


def _loop_graph_no_schema(max_iterations: int = 10) -> Graph:
    """Same loop topology but WITHOUT state_schema on the gate."""
    src = CodeOperator(
        id="src", name="Source", code="...",
        output_ports=[OutputPort(name="counter")],
    )
    body = CodeOperator(
        id="body", name="Body", code="...",
        input_ports=[InputPort(name="counter", required=False)],
        output_ports=[OutputPort(name="counter")],
    )
    gate = GateNode(
        id="gate", name="Loop Gate",
        condition="counter < 3",
        gate_mode="while",
        max_iterations=max_iterations,
        input_ports=[InputPort(name="counter")],
        output_ports=[OutputPort(name="continue"), OutputPort(name="done")],
    )
    sink = CodeOperator(
        id="sink", name="Sink", code="...",
        input_ports=[InputPort(name="data", required=False)],
        output_ports=[OutputPort(name="result")],
    )
    return Graph(
        nodes=[src, body, gate, sink],
        edges=[
            DataEdge(id="e_src_body", source_node_id="src", source_port="counter",
                     target_node_id="body", target_port="counter"),
            DataEdge(id="e_body_gate", source_node_id="body", source_port="counter",
                     target_node_id="gate", target_port="counter"),
            DataEdge(id="e_back", source_node_id="gate", source_port="continue",
                     target_node_id="body", target_port="counter"),
            DataEdge(id="e_done", source_node_id="gate", source_port="done",
                     target_node_id="sink", target_port="data"),
        ],
        entry_points=["src"],
        exit_points=["sink"],
    )


def _sequential_body_graph() -> Graph:
    """Build: src -> nodeA -> nodeB -> gate(while), gate.continue -> nodeA.

    nodeA appends "a" to items, nodeB appends "b".
    Items and counter flow through scope; trigger edge enforces A-before-B ordering.
    The back-edge targets node_a.counter so that virtual input injection
    correctly populates a key present in gate's continue output.
    """
    src = CodeOperator(
        id="src", name="Source", code="...",
        output_ports=[OutputPort(name="start")],
    )
    node_a = CodeOperator(
        id="node_a", name="NodeA", code="...",
        input_ports=[
            InputPort(name="counter", required=False),
            InputPort(name="items", required=False),
        ],
        output_ports=[OutputPort(name="items"), OutputPort(name="trigger"),
                      OutputPort(name="counter")],
    )
    node_b = CodeOperator(
        id="node_b", name="NodeB", code="...",
        input_ports=[
            InputPort(name="trigger"),
            InputPort(name="items", required=False),
        ],
        output_ports=[OutputPort(name="items"), OutputPort(name="counter")],
    )
    gate = GateNode(
        id="gate", name="Loop Gate",
        condition="counter < 2",
        gate_mode="while",
        max_iterations=5,
        state_schema={
            "items": {"type": "array"},
            "counter": {"type": "integer"},
        },
        state_defaults={"items": [], "counter": 0},
        input_ports=[
            InputPort(name="items", required=False),
            InputPort(name="counter"),
        ],
        output_ports=[OutputPort(name="continue"), OutputPort(name="done")],
    )
    sink = CodeOperator(
        id="sink", name="Sink", code="...",
        input_ports=[InputPort(name="data", required=False)],
        output_ports=[OutputPort(name="result")],
    )
    return Graph(
        nodes=[src, node_a, node_b, gate, sink],
        edges=[
            DataEdge(id="e1", source_node_id="src", source_port="start",
                     target_node_id="node_a", target_port="counter"),
            DataEdge(id="e2", source_node_id="node_a", source_port="trigger",
                     target_node_id="node_b", target_port="trigger"),
            DataEdge(id="e3", source_node_id="node_b", source_port="counter",
                     target_node_id="gate", target_port="counter"),
            DataEdge(id="e3b", source_node_id="node_b", source_port="items",
                     target_node_id="gate", target_port="items"),
            # Back-edge targets counter so virtual input key matches continue data
            DataEdge(id="e_back", source_node_id="gate", source_port="continue",
                     target_node_id="node_a", target_port="counter"),
            DataEdge(id="e_done", source_node_id="gate", source_port="done",
                     target_node_id="sink", target_port="data"),
        ],
        entry_points=["src"],
        exit_points=["sink"],
    )


# ===================================================================
# 7-1: GateNode with state_schema serialization round-trip
# ===================================================================


class TestGateNodeStateSchemaRoundtrip:
    def test_roundtrip_with_schema(self):
        gate = GateNode(
            id="g", name="g", gate_mode="while", condition="x",
            state_schema={"counter": {"type": "integer"}},
            state_defaults={"counter": 0},
        )
        data = gate.model_dump()
        restored = GateNode.model_validate(data)
        assert restored.state_schema == {"counter": {"type": "integer"}}
        assert restored.state_defaults == {"counter": 0}

    def test_roundtrip_without_schema(self):
        gate = GateNode(id="g", name="g", gate_mode="while", condition="x")
        data = gate.model_dump()
        restored = GateNode.model_validate(data)
        assert restored.state_schema is None
        assert restored.state_defaults is None

    def test_roundtrip_multi_field_schema(self):
        gate = GateNode(
            id="g", name="g", gate_mode="while",
            condition="counter < 3",
            state_schema={
                "counter": {"type": "integer"},
                "flag": {"type": "boolean"},
                "items": {"type": "array"},
            },
            state_defaults={"counter": 0, "flag": True, "items": []},
        )
        data = gate.model_dump()
        restored = GateNode.model_validate(data)
        assert restored.state_schema == {
            "counter": {"type": "integer"},
            "flag": {"type": "boolean"},
            "items": {"type": "array"},
        }
        assert restored.state_defaults == {"counter": 0, "flag": True, "items": []}

    def test_schema_survives_full_graph_roundtrip(self):
        """state_schema survives Graph -> JSON -> Graph serialization."""
        gate = GateNode(
            id="g", name="g", gate_mode="while",
            condition="counter < 3",
            state_schema={"counter": {"type": "integer"}, "flag": {"type": "boolean"}},
            state_defaults={"counter": 0, "flag": True},
        )
        graph = Graph(nodes=[gate], entry_points=["g"], exit_points=["g"])
        data = graph.model_dump()
        restored = Graph.model_validate(data)
        rg = restored.node_by_id("g")
        assert rg is not None
        assert rg.state_schema == {"counter": {"type": "integer"}, "flag": {"type": "boolean"}}
        assert rg.state_defaults == {"counter": 0, "flag": True}


# ===================================================================
# 7-5: Integration: while-gate loop with state_schema persists state
# ===================================================================


class TestScopedLoopIntegration:
    @pytest.mark.asyncio
    async def test_state_persists_across_iterations(self):
        """state_schema-managed counter persists; body runs 3 times."""
        graph = _scoped_loop_graph(max_iterations=10)

        body_exec = _BodyExecutor()
        dispatch = _DispatchExecutor({
            "src": _ConstExecutor({"counter": 0}),
            "body": body_exec,
            "sink": _SinkExecutor(),
        })
        reg = ExecutorRegistry()
        reg.register("code_operator", dispatch)
        reg.register("gate", GateExecutor())

        engine = _engine(registry=reg)
        result = await engine.run(graph, inputs={"counter": 0})

        assert result.success, f"Run failed: {result.errors}"
        assert body_exec.call_count == 3

    @pytest.mark.asyncio
    async def test_counter_values_increment_correctly(self):
        """Verify counter values received by body on each call."""
        graph = _scoped_loop_graph(max_iterations=10)

        body_exec = _BodyExecutor()
        dispatch = _DispatchExecutor({
            "src": _ConstExecutor({"counter": 0}),
            "body": body_exec,
            "sink": _SinkExecutor(),
        })
        reg = ExecutorRegistry()
        reg.register("code_operator", dispatch)
        reg.register("gate", GateExecutor())

        result = await _engine(registry=reg).run(graph, inputs={"counter": 0})

        assert result.success
        counters = [inp.get("counter", -1) for inp in body_exec.received_inputs]
        assert counters == [0, 1, 2]


# ===================================================================
# 7-9: Regression: gate works with and without state_schema
# ===================================================================


class TestGateWithAndWithoutSchema:
    @pytest.mark.asyncio
    async def test_with_state_schema_engine_completes(self):
        """Full engine run with state_schema succeeds."""
        graph = _scoped_loop_graph(max_iterations=10)
        body_exec = _BodyExecutor()
        dispatch = _DispatchExecutor({
            "src": _ConstExecutor({"counter": 0}),
            "body": body_exec,
            "sink": _SinkExecutor(),
        })
        reg = ExecutorRegistry()
        reg.register("code_operator", dispatch)
        reg.register("gate", GateExecutor())

        result = await _engine(registry=reg).run(graph, inputs={"counter": 0})
        assert result.success, f"With state_schema failed: {result.errors}"
        assert body_exec.call_count >= 2

    @pytest.mark.asyncio
    async def test_without_state_schema_executor_succeeds(self):
        """GateExecutor evaluates condition correctly without state_schema."""
        executor = GateExecutor()
        gate = GateNode(
            id="g", name="Loop Gate",
            condition="counter < 3",
            gate_mode="while",
            max_iterations=10,
            input_ports=[InputPort(name="counter")],
        )

        class _MockCtx:
            def __init__(self):
                self.local_state = LocalStateManager()
            async def emit_event(self, **kwargs):
                pass

        ctx = _MockCtx()
        result = await executor.execute(gate, {"counter": 1}, ctx)
        assert result.status == NodeStatus.COMPLETED
        assert "continue" in result.outputs

        result = await executor.execute(gate, {"counter": 5}, ctx)
        assert result.status == NodeStatus.COMPLETED
        assert "done" in result.outputs

    @pytest.mark.asyncio
    async def test_with_state_schema_executor_uses_scope(self):
        """GateExecutor reads from scope when state_schema is present."""
        executor = GateExecutor()
        gate = GateNode(
            id="g", name="Loop Gate",
            condition="counter < 3",
            gate_mode="while",
            max_iterations=10,
            state_schema={"counter": {"type": "integer"}},
            state_defaults={"counter": 0},
            input_ports=[InputPort(name="counter")],
        )

        class _MockCtx:
            def __init__(self):
                self.local_state = LocalStateManager()
            async def emit_event(self, **kwargs):
                pass

        ctx = _MockCtx()
        # First call: scope is empty, initializes from defaults + inputs
        result = await executor.execute(gate, {"counter": 2}, ctx)
        assert result.status == NodeStatus.COMPLETED
        assert "continue" in result.outputs
        scope = ctx.local_state.get_scope("g")
        assert scope.get("counter") == 2

        # Second call: scope already populated, condition uses scope
        result = await executor.execute(gate, {"counter": 5}, ctx)
        assert result.status == NodeStatus.COMPLETED
        assert "done" in result.outputs


# ===================================================================
# 7-10: Iteration counter matches cycle count
# ===================================================================


class TestIterationCounterMatchesCycleCount:
    @pytest.mark.asyncio
    async def test_body_call_count_matches_expected(self):
        """counter < 3: body runs 3 times with counter 0, 1, 2."""
        graph = _scoped_loop_graph(max_iterations=10)

        body_exec = _BodyExecutor()
        dispatch = _DispatchExecutor({
            "src": _ConstExecutor({"counter": 0}),
            "body": body_exec,
            "sink": _SinkExecutor(),
        })
        reg = ExecutorRegistry()
        reg.register("code_operator", dispatch)
        reg.register("gate", GateExecutor())

        result = await _engine(registry=reg).run(graph, inputs={"counter": 0})

        assert result.success
        assert body_exec.call_count == 3

    @pytest.mark.asyncio
    async def test_iteration_visible_in_scope_during_reexecutions(self):
        """Re-execution passes see scheduler-written iteration values via scope."""
        graph = _scoped_loop_graph(max_iterations=10)
        gate = graph.node_by_id("gate")
        gate.state_schema["iteration"] = {"type": "integer"}
        gate.state_defaults["iteration"] = 0

        iterations_seen: list[int] = []

        class _TrackIterationExecutor:
            async def execute(self, node, inputs, context):
                iterations_seen.append(inputs.get("iteration", -1))
                counter = inputs.get("counter", 0)
                return NodeResult(outputs={"counter": counter + 1})

        dispatch = _DispatchExecutor({
            "src": _ConstExecutor({"counter": 0}),
            "body": _TrackIterationExecutor(),
            "sink": _SinkExecutor(),
        })
        reg = ExecutorRegistry()
        reg.register("code_operator", dispatch)
        reg.register("gate", GateExecutor())

        result = await _engine(registry=reg).run(graph, inputs={"counter": 0})

        assert result.success
        # First pass uses normal edges and does not inject loop scope yet.
        # Re-execution passes receive iteration = 1, 2, ... from scheduler scope.
        assert len(iterations_seen) == 3
        assert iterations_seen[0] in (-1, 0)
        assert all(it >= 1 for it in iterations_seen[1:])


# ===================================================================
# 7-14: Sequential body nodes see each other's scope updates
# ===================================================================


class TestSequentialBodyScopeUpdates:
    @pytest.mark.asyncio
    async def test_node_b_sees_node_a_scope_update(self):
        """nodeB sees nodeA's scope update within the same iteration.

        Graph: src -> nodeA -> nodeB -> gate(while).
        Items flow through scope (no direct items edge from A to B).
        On re-execution, A appends "a" to items via scope, then B sees
        the updated items and appends "b".
        """
        graph = _sequential_body_graph()

        a_exec = _AppendExecutor("a")
        b_exec = _AppendExecutor("b")
        sink_exec = _SinkExecutor()

        dispatch = _DispatchExecutor({
            "src": _ConstExecutor({"start": True}),
            "node_a": a_exec,
            "node_b": b_exec,
            "sink": sink_exec,
        })
        reg = ExecutorRegistry()
        reg.register("code_operator", dispatch)
        reg.register("gate", GateExecutor())

        result = await _engine(registry=reg).run(graph, inputs={"start": True})

        assert result.success, f"Run failed: {result.errors}"
        # nodeA should have run at least once during re-execution
        assert a_exec.call_count >= 1
        # nodeB should see nodeA's "a" in items on re-execution.
        # B's received_items captures items BEFORE B appends its tag.
        if b_exec.call_count >= 2:
            for items_before_append in b_exec.received_items[1:]:
                if "a" in items_before_append:
                    break
            else:
                pytest.fail(
                    f"NodeB never saw 'a' from NodeA's scope update. "
                    f"B received: {b_exec.received_items}"
                )


# ===================================================================
# 7-15: Scope injection only when active_loop_scope_id is set
# ===================================================================


class TestScopeInjectionRequiresActiveId:
    @pytest.mark.asyncio
    async def test_no_scope_injection_without_active_loop_scope_id(self):
        """Without active_loop_scope_id, executor does NOT see scope values.

        A standalone body node (no loop) has scope data in LocalStateManager
        for a gate ID, but since there's no loop active, the scope values
        are not injected.
        """
        body = CodeOperator(
            id="body", name="Body", code="...",
            input_ports=[InputPort(name="x", required=False)],
            output_ports=[OutputPort(name="out")],
        )
        graph = Graph(
            nodes=[body],
            edges=[],
            entry_points=["body"],
            exit_points=["body"],
        )

        received: dict[str, Any] = {}

        class _CaptureExecutor:
            async def execute(self, node, inputs, context):
                received.update(inputs)
                return NodeResult(outputs={"out": "done"})

        reg = ExecutorRegistry()
        reg.register("code_operator", _CaptureExecutor())

        result = await _engine(registry=reg).run(graph)
        assert result.success
        # No loop active, so no scope injection — counter absent from inputs
        assert "counter" not in received

    @pytest.mark.asyncio
    async def test_scope_injected_when_active_in_loop(self):
        """During re-execution (active_loop_scope_id set), scope values ARE injected."""
        graph = _scoped_loop_graph(max_iterations=10)

        body_exec = _BodyExecutor()
        dispatch = _DispatchExecutor({
            "src": _ConstExecutor({"counter": 0}),
            "body": body_exec,
            "sink": _SinkExecutor(),
        })
        reg = ExecutorRegistry()
        reg.register("code_operator", dispatch)
        reg.register("gate", GateExecutor())

        result = await _engine(registry=reg).run(graph, inputs={"counter": 0})

        assert result.success
        assert body_exec.call_count >= 2
        # On re-executions, body receives incrementing counter from scope injection.
        later_inputs = body_exec.received_inputs[1:]
        assert all(inp.get("counter", 0) > 0 for inp in later_inputs)


# ===================================================================
# 7-16: Output keys matching state_schema written to scope, others not
# ===================================================================


class TestScopeWriteFiltering:
    @pytest.mark.asyncio
    async def test_only_schema_keys_written_to_scope(self):
        """Body outputs {counter, unrelated}. Only counter (in state_schema)
        should persist via scope; 'unrelated' should not appear in later inputs.
        """
        gate = GateNode(
            id="gate", name="Gate",
            condition="counter < 2",
            gate_mode="while",
            max_iterations=5,
            state_schema={"counter": {"type": "integer"}},
            state_defaults={"counter": 0},
            input_ports=[InputPort(name="counter", required=False)],
            output_ports=[OutputPort(name="continue"), OutputPort(name="done")],
        )
        src = CodeOperator(
            id="src", name="Src", code="...",
            output_ports=[OutputPort(name="counter")],
        )
        body = CodeOperator(
            id="body", name="Body", code="...",
            input_ports=[InputPort(name="counter", required=False)],
            output_ports=[
                OutputPort(name="counter"),
                OutputPort(name="unrelated"),
            ],
        )
        sink = CodeOperator(
            id="sink", name="Sink", code="...",
            input_ports=[InputPort(name="data", required=False)],
            output_ports=[OutputPort(name="result")],
        )
        graph = Graph(
            nodes=[src, body, gate, sink],
            edges=[
                DataEdge(id="e1", source_node_id="src", source_port="counter",
                         target_node_id="body", target_port="counter"),
                DataEdge(id="e2", source_node_id="body", source_port="counter",
                         target_node_id="gate", target_port="counter"),
                DataEdge(id="e_back", source_node_id="gate", source_port="continue",
                         target_node_id="body", target_port="counter"),
                DataEdge(id="e_done", source_node_id="gate", source_port="done",
                         target_node_id="sink", target_port="data"),
            ],
            entry_points=["src"],
            exit_points=["sink"],
        )

        all_body_inputs: list[dict] = []

        class _MixedOutputExecutor:
            async def execute(self, node, inputs, context):
                all_body_inputs.append(dict(inputs))
                counter = inputs.get("counter", 0)
                return NodeResult(outputs={"counter": counter + 1, "unrelated": "x"})

        dispatch = _DispatchExecutor({
            "src": _ConstExecutor({"counter": 0}),
            "body": _MixedOutputExecutor(),
            "sink": _SinkExecutor(),
        })
        reg = ExecutorRegistry()
        reg.register("code_operator", dispatch)
        reg.register("gate", GateExecutor())

        result = await _engine(registry=reg).run(graph, inputs={"counter": 0})

        assert result.success, f"Run failed: {result.errors}"
        assert len(all_body_inputs) >= 2

        # On re-execution, 'unrelated' must NOT appear in body inputs because
        # it's not in state_schema and thus was never written to scope.
        for i, inp in enumerate(all_body_inputs[1:], start=1):
            assert "counter" in inp, f"Re-exec {i}: counter missing"
            assert "unrelated" not in inp, (
                f"Re-exec {i}: 'unrelated' leaked into inputs via scope"
            )


# ===================================================================
# 7-17: First pass uses normal edges, scope activates on re-execution
# ===================================================================


class TestFirstPassUsesEdges:
    @pytest.mark.asyncio
    async def test_first_body_call_gets_correct_counter(self):
        """Body's first call receives counter=0 (from scope/virtual-inputs).
        Subsequent calls get incrementing values.
        """
        graph = _scoped_loop_graph(max_iterations=10)

        body_exec = _BodyExecutor()
        dispatch = _DispatchExecutor({
            "src": _ConstExecutor({"counter": 0}),
            "body": body_exec,
            "sink": _SinkExecutor(),
        })
        reg = ExecutorRegistry()
        reg.register("code_operator", dispatch)
        reg.register("gate", GateExecutor())

        result = await _engine(registry=reg).run(graph, inputs={"counter": 0})

        assert result.success, f"Run failed: {result.errors}"
        assert body_exec.call_count >= 2
        assert body_exec.received_inputs[0]["counter"] == 0
        for i, inp in enumerate(body_exec.received_inputs[1:], start=1):
            assert inp["counter"] >= i

    @pytest.mark.asyncio
    async def test_active_loop_scope_id_set_during_reexecution_body_execution(self):
        """Only re-execution body passes run with the active loop scope set."""
        graph = _scoped_loop_graph(max_iterations=10)
        body_scope_ids: list[str | None] = []

        class _ScopeTrackingExecutor:
            async def execute(self, node, inputs, context):
                body_scope_ids.append(context.active_loop_scope_id)
                counter = inputs.get("counter", 0)
                return NodeResult(outputs={"counter": counter + 1})

        dispatch = _DispatchExecutor({
            "src": _ConstExecutor({"counter": 0}),
            "body": _ScopeTrackingExecutor(),
            "sink": _SinkExecutor(),
        })
        reg = ExecutorRegistry()
        reg.register("code_operator", dispatch)
        reg.register("gate", GateExecutor())

        result = await _engine(registry=reg).run(graph, inputs={"counter": 0})

        assert result.success
        assert len(body_scope_ids) >= 2
        assert body_scope_ids[0] is None
        for sid in body_scope_ids[1:]:
            assert sid == "gate"
