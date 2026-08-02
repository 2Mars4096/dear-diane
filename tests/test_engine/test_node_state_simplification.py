"""Tests for code node port defaults and spread edges (Plan 7-6).

Covers:
  7-2:  _default_for_schema returns correct defaults for all JSON types
  7-3:  CodeExecutor fills missing optional port defaults
  7-4:  PortDataStore.resolve_inputs spread behavior
  7-6:  Integration — code node with optional ports gets schema defaults
  7-7:  Integration — spread edge carries struct, target receives individual fields
  7-11: Spread edge validation — port existence enforced, schema check skipped
  7-12: Spread edge resolve_inputs — landing port + individual fields, edge cases
"""

from __future__ import annotations

import pytest

from dan.engine.checkpoint import NullCheckpointStore
from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
from dan.engine.executor import EngineConfig, ExecutionContext, ExecutorRegistry, NodeResult
from dan.engine.scheduler import Engine
from dan.engine.state import ExecutionState, NodeStatus, PortDataStore
from dan.executors.code import CodeExecutor, _default_for_schema
from dan.models.edges import DataEdge
from dan.models.graph import Graph
from dan.models.nodes import CodeOperator, NodeBase
from dan.models.ports import InputPort, OutputPort
from dan.validation.graph import validate_graph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _config():
    return EngineConfig(checkpoint_enabled=False)


def _engine():
    return Engine(config=_config(), checkpoint_store=NullCheckpointStore())


def _make_context(graph: Graph) -> ExecutionContext:
    """Minimal ExecutionContext wired to a fresh ExecutionState."""
    state = ExecutionState(graph)
    return ExecutionContext(
        state=state,
        config=_config(),
        shared_context=SharedContextStore(),
        artifacts=ArtifactStore(),
        local_state=LocalStateManager(),
    )


class _DictOutputExecutor:
    """Mock executor that returns a fixed dict on a named output port."""

    def __init__(self, outputs: dict):
        self._outputs = outputs

    async def execute(self, node: NodeBase, inputs: dict, context: ExecutionContext) -> NodeResult:
        return NodeResult(outputs=self._outputs)


# ===================================================================
# 7-2: _default_for_schema returns correct defaults for all JSON types
# ===================================================================

class TestDefaultForSchema:
    def test_array(self):
        assert _default_for_schema({"type": "array"}) == []

    def test_object(self):
        assert _default_for_schema({"type": "object"}) == {}

    def test_number(self):
        assert _default_for_schema({"type": "number"}) == 0

    def test_integer(self):
        assert _default_for_schema({"type": "integer"}) == 0

    def test_string(self):
        assert _default_for_schema({"type": "string"}) == ""

    def test_boolean(self):
        assert _default_for_schema({"type": "boolean"}) is False

    def test_none_schema(self):
        assert _default_for_schema(None) is None

    def test_empty_schema(self):
        assert _default_for_schema({}) is None

    def test_unknown_type(self):
        assert _default_for_schema({"type": "null"}) is None

    def test_each_default_is_fresh_instance(self):
        """Mutable defaults (list, dict) must be independent objects."""
        a = _default_for_schema({"type": "array"})
        b = _default_for_schema({"type": "array"})
        assert a == b
        assert a is not b

        c = _default_for_schema({"type": "object"})
        d = _default_for_schema({"type": "object"})
        assert c == d
        assert c is not d


# ===================================================================
# 7-3: CodeExecutor fills missing optional port defaults
# ===================================================================

class TestCodeExecutorPortDefaults:
    @pytest.mark.asyncio
    async def test_optional_ports_get_schema_defaults(self):
        """Missing optional ports should receive type-appropriate defaults."""
        node = CodeOperator(
            id="code1",
            name="Code1",
            code="result = {'items': items, 'config': config, 'count': count}",
            input_ports=[
                InputPort(name="items", required=False, json_schema={"type": "array"}),
                InputPort(name="config", required=False, json_schema={"type": "object"}),
                InputPort(name="count", required=False, json_schema={"type": "integer"}),
            ],
            output_ports=[
                OutputPort(name="items"),
                OutputPort(name="config"),
                OutputPort(name="count"),
            ],
        )
        graph = Graph(nodes=[node], entry_points=["code1"], exit_points=["code1"])
        ctx = _make_context(graph)
        executor = CodeExecutor()

        result = await executor.execute(node, {}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["items"] == []
        assert result.outputs["config"] == {}
        assert result.outputs["count"] == 0

    @pytest.mark.asyncio
    async def test_provided_values_not_overwritten(self):
        """Ports that ARE supplied should keep their provided values."""
        node = CodeOperator(
            id="code2",
            name="Code2",
            code="result = {'items': items}",
            input_ports=[
                InputPort(name="items", required=False, json_schema={"type": "array"}),
            ],
            output_ports=[OutputPort(name="items")],
        )
        graph = Graph(nodes=[node], entry_points=["code2"], exit_points=["code2"])
        ctx = _make_context(graph)
        executor = CodeExecutor()

        result = await executor.execute(node, {"items": [1, 2, 3]}, ctx)

        assert result.outputs["items"] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_required_ports_not_filled(self):
        """Missing required ports must NOT be filled — the code should fail."""
        node = CodeOperator(
            id="code3",
            name="Code3",
            code="result = {'x': x}",
            input_ports=[
                InputPort(name="x", required=True, json_schema={"type": "number"}),
            ],
            output_ports=[OutputPort(name="x")],
        )
        graph = Graph(nodes=[node], entry_points=["code3"], exit_points=["code3"])
        ctx = _make_context(graph)
        executor = CodeExecutor()

        result = await executor.execute(node, {}, ctx)

        assert result.status == NodeStatus.FAILED

    @pytest.mark.asyncio
    async def test_inputs_dict_injected(self):
        """Code should have access to an `inputs` dict alongside raw locals."""
        node = CodeOperator(
            id="code4",
            name="Code4",
            code="result = {'has_inputs': isinstance(inputs, dict), 'val': inputs.get('x', -1)}",
            input_ports=[
                InputPort(name="x", required=False, json_schema={"type": "number"}),
            ],
            output_ports=[OutputPort(name="has_inputs"), OutputPort(name="val")],
        )
        graph = Graph(nodes=[node], entry_points=["code4"], exit_points=["code4"])
        ctx = _make_context(graph)
        executor = CodeExecutor()

        result = await executor.execute(node, {}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["has_inputs"] is True
        assert result.outputs["val"] == 0  # default for number

    @pytest.mark.asyncio
    async def test_mixed_optional_types(self):
        """All optional schema types filled simultaneously."""
        node = CodeOperator(
            id="code5",
            name="Code5",
            code=(
                "result = {'a': items, 'b': cfg, 'c': n, 'd': s, 'e': flag, 'f': mystery}"
            ),
            input_ports=[
                InputPort(name="items", required=False, json_schema={"type": "array"}),
                InputPort(name="cfg", required=False, json_schema={"type": "object"}),
                InputPort(name="n", required=False, json_schema={"type": "number"}),
                InputPort(name="s", required=False, json_schema={"type": "string"}),
                InputPort(name="flag", required=False, json_schema={"type": "boolean"}),
                InputPort(name="mystery", required=False, json_schema={}),
            ],
            output_ports=[
                OutputPort(name="a"), OutputPort(name="b"), OutputPort(name="c"),
                OutputPort(name="d"), OutputPort(name="e"), OutputPort(name="f"),
            ],
        )
        graph = Graph(nodes=[node], entry_points=["code5"], exit_points=["code5"])
        ctx = _make_context(graph)
        executor = CodeExecutor()

        result = await executor.execute(node, {}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["a"] == []
        assert result.outputs["b"] == {}
        assert result.outputs["c"] == 0
        assert result.outputs["d"] == ""
        assert result.outputs["e"] is False
        assert result.outputs["f"] is None


# ===================================================================
# 7-4: PortDataStore.resolve_inputs spread behavior
# ===================================================================

class TestResolveInputsSpread:
    def _make_spread_graph(self, extra_edges=None):
        """Two-node graph with a spread edge from source.state -> target.state."""
        src = CodeOperator(
            id="src", name="Src", code="",
            output_ports=[OutputPort(name="state", json_schema={"type": "object"})],
        )
        tgt = CodeOperator(
            id="tgt", name="Tgt", code="",
            input_ports=[
                InputPort(name="state", json_schema={"type": "object"}),
                InputPort(name="a", required=False),
                InputPort(name="b", required=False),
            ],
        )
        edges = [
            DataEdge(
                id="e_spread",
                source_node_id="src", source_port="state",
                target_node_id="tgt", target_port="state",
                spread=True,
            ),
        ]
        if extra_edges:
            edges.extend(extra_edges)

        return Graph(
            nodes=[src, tgt],
            edges=edges,
            entry_points=["src"],
            exit_points=["tgt"],
        )

    def test_spread_populates_landing_port_and_fields(self):
        graph = self._make_spread_graph()
        store = PortDataStore()
        store.set("src", "state", {"a": 1, "b": 2})

        inputs = store.resolve_inputs("tgt", graph)

        assert inputs["state"] == {"a": 1, "b": 2}
        assert inputs["a"] == 1
        assert inputs["b"] == 2

    def test_explicit_edge_takes_precedence_over_spread(self):
        """A scalar edge to key 'a' should win over the spread field 'a'."""
        extra_src = CodeOperator(
            id="scalar_src", name="ScalarSrc", code="",
            output_ports=[OutputPort(name="a")],
        )
        explicit_edge = DataEdge(
            id="e_explicit",
            source_node_id="scalar_src", source_port="a",
            target_node_id="tgt", target_port="a",
        )
        src = CodeOperator(
            id="src", name="Src", code="",
            output_ports=[OutputPort(name="state", json_schema={"type": "object"})],
        )
        tgt = CodeOperator(
            id="tgt", name="Tgt", code="",
            input_ports=[
                InputPort(name="state", json_schema={"type": "object"}),
                InputPort(name="a", required=False),
                InputPort(name="b", required=False),
            ],
        )
        graph = Graph(
            nodes=[src, tgt, extra_src],
            edges=[
                DataEdge(
                    id="e_spread",
                    source_node_id="src", source_port="state",
                    target_node_id="tgt", target_port="state",
                    spread=True,
                ),
                explicit_edge,
            ],
            entry_points=["src", "scalar_src"],
            exit_points=["tgt"],
        )

        store = PortDataStore()
        store.set("src", "state", {"a": 10, "b": 20})
        store.set("scalar_src", "a", 999)

        inputs = store.resolve_inputs("tgt", graph)

        assert inputs["state"] == {"a": 10, "b": 20}
        assert inputs["b"] == 20
        # Explicit edge wins — depends on edge iteration order, but 'a' in
        # inputs is either 999 from the explicit edge or 10 from spread.
        # The explicit edge writes inputs["a"] = 999 unconditionally; the
        # spread only writes if key is absent. Regardless of edge order,
        # the final value must be 999.
        assert inputs["a"] == 999

    def test_non_dict_value_not_spread(self):
        """If the source value is not a dict, spread should be a no-op."""
        graph = self._make_spread_graph()
        store = PortDataStore()
        store.set("src", "state", "not-a-dict")

        inputs = store.resolve_inputs("tgt", graph)

        assert inputs["state"] == "not-a-dict"
        assert "a" not in inputs


# ===================================================================
# 7-6: Integration — code node with optional ports, no NameError
# ===================================================================

class TestCodeNodeDefaultsIntegration:
    @pytest.mark.asyncio
    async def test_optional_ports_no_name_error(self):
        """Code node referencing optional ports should not NameError when unwired."""
        code_node = CodeOperator(
            id="calc",
            name="Calc",
            code="result = {'total': sum(items) + count}",
            input_ports=[
                InputPort(name="items", required=False, json_schema={"type": "array"}),
                InputPort(name="count", required=False, json_schema={"type": "integer"}),
            ],
            output_ports=[OutputPort(name="total")],
        )
        graph = Graph(
            nodes=[code_node],
            edges=[],
            entry_points=["calc"],
            exit_points=["calc"],
        )

        engine = _engine()
        result = await engine.run(graph)

        assert result.success, f"Expected success but got errors: {result.errors}"
        assert result.outputs["total"] == 0

    @pytest.mark.asyncio
    async def test_optional_port_with_upstream_value(self):
        """When an upstream edge supplies a value, the default is not used."""
        source = CodeOperator(
            id="source",
            name="Source",
            code="result = {'data': [10, 20, 30]}",
            output_ports=[OutputPort(name="data")],
        )
        consumer = CodeOperator(
            id="consumer",
            name="Consumer",
            code="result = {'total': sum(data)}",
            input_ports=[
                InputPort(name="data", required=False, json_schema={"type": "array"}),
            ],
            output_ports=[OutputPort(name="total")],
        )
        graph = Graph(
            nodes=[source, consumer],
            edges=[
                DataEdge(
                    id="e1",
                    source_node_id="source", source_port="data",
                    target_node_id="consumer", target_port="data",
                ),
            ],
            entry_points=["source"],
            exit_points=["consumer"],
        )

        engine = _engine()
        result = await engine.run(graph)

        assert result.success
        assert result.outputs["total"] == 60


# ===================================================================
# 7-7: Integration — spread edge carries struct, target receives fields
# ===================================================================

class TestSpreadEdgeIntegration:
    @pytest.mark.asyncio
    async def test_spread_edge_delivers_individual_fields(self):
        """Source outputs a dict on port 'state'; target receives individual fields."""
        source = CodeOperator(
            id="source",
            name="Source",
            code="result = {'state': {'a': 1, 'b': 2}}",
            output_ports=[
                OutputPort(name="state", json_schema={"type": "object"}),
            ],
        )
        target = CodeOperator(
            id="target",
            name="Target",
            code="result = {'got_a': a, 'got_b': b, 'got_state': state}",
            input_ports=[
                InputPort(name="state", json_schema={"type": "object"}),
                InputPort(name="a", required=False, json_schema={"type": "integer"}),
                InputPort(name="b", required=False, json_schema={"type": "integer"}),
            ],
            output_ports=[
                OutputPort(name="got_a"),
                OutputPort(name="got_b"),
                OutputPort(name="got_state"),
            ],
        )
        graph = Graph(
            nodes=[source, target],
            edges=[
                DataEdge(
                    id="e_spread",
                    source_node_id="source", source_port="state",
                    target_node_id="target", target_port="state",
                    spread=True,
                ),
            ],
            entry_points=["source"],
            exit_points=["target"],
        )

        engine = _engine()
        result = await engine.run(graph)

        assert result.success, f"Expected success but got errors: {result.errors}"
        assert result.outputs["got_a"] == 1
        assert result.outputs["got_b"] == 2
        assert result.outputs["got_state"] == {"a": 1, "b": 2}


# ===================================================================
# 7-11: Spread edge validation
# ===================================================================

class TestSpreadEdgeValidation:
    def test_spread_edge_with_valid_ports_passes(self):
        """A spread edge between existing ports should pass validation."""
        src = CodeOperator(
            id="src", name="Src", code="",
            output_ports=[OutputPort(name="state", json_schema={"type": "object"})],
        )
        tgt = CodeOperator(
            id="tgt", name="Tgt", code="",
            input_ports=[InputPort(name="state", json_schema={"type": "object"})],
        )
        graph = Graph(
            nodes=[src, tgt],
            edges=[
                DataEdge(
                    id="e1",
                    source_node_id="src", source_port="state",
                    target_node_id="tgt", target_port="state",
                    spread=True,
                ),
            ],
            entry_points=["src"],
            exit_points=["tgt"],
        )

        errors = validate_graph(graph)
        port_errors = [e for e in errors if "port" in e.lower() and "not found" in e.lower()]
        assert port_errors == []

    def test_spread_edge_missing_port_fails(self):
        """Spread edges still enforce port existence."""
        src = CodeOperator(
            id="src", name="Src", code="",
            output_ports=[OutputPort(name="state")],
        )
        tgt = CodeOperator(
            id="tgt", name="Tgt", code="",
            input_ports=[],  # no 'state' port
        )
        graph = Graph(
            nodes=[src, tgt],
            edges=[
                DataEdge(
                    id="e1",
                    source_node_id="src", source_port="state",
                    target_node_id="tgt", target_port="state",
                    spread=True,
                ),
            ],
            entry_points=["src"],
            exit_points=["tgt"],
        )

        errors = validate_graph(graph)
        port_errors = [e for e in errors if "no input port" in e.lower()]
        assert len(port_errors) == 1

    def test_spread_edge_skips_per_field_schema_check(self):
        """Spread edges skip schema compatibility — object→object is sufficient."""
        src = CodeOperator(
            id="src", name="Src", code="",
            output_ports=[
                OutputPort(name="state", json_schema={
                    "type": "object",
                    "properties": {"x": {"type": "number"}},
                }),
            ],
        )
        tgt = CodeOperator(
            id="tgt", name="Tgt", code="",
            input_ports=[
                InputPort(name="state", json_schema={
                    "type": "object",
                    "properties": {"completely_different": {"type": "string"}},
                }),
            ],
        )
        graph = Graph(
            nodes=[src, tgt],
            edges=[
                DataEdge(
                    id="e1",
                    source_node_id="src", source_port="state",
                    target_node_id="tgt", target_port="state",
                    spread=True,
                ),
            ],
            entry_points=["src"],
            exit_points=["tgt"],
        )

        errors = validate_graph(graph)
        schema_errors = [e for e in errors if "schema incompatibility" in e.lower()]
        assert schema_errors == [], f"Spread edge should skip schema check, got: {schema_errors}"

    def test_non_spread_edge_still_checks_schema(self):
        """Sanity check: a normal (non-spread) edge DOES check schema compat."""
        src = CodeOperator(
            id="src", name="Src", code="",
            output_ports=[OutputPort(name="out", json_schema={"type": "string"})],
        )
        tgt = CodeOperator(
            id="tgt", name="Tgt", code="",
            input_ports=[InputPort(name="in", json_schema={"type": "integer"})],
        )
        graph = Graph(
            nodes=[src, tgt],
            edges=[
                DataEdge(
                    id="e1",
                    source_node_id="src", source_port="out",
                    target_node_id="tgt", target_port="in",
                ),
            ],
            entry_points=["src"],
            exit_points=["tgt"],
        )

        errors = validate_graph(graph)
        schema_errors = [e for e in errors if "schema incompatibility" in e.lower()]
        assert len(schema_errors) >= 1


# ===================================================================
# 7-12: Spread edge resolve_inputs — detailed edge cases
# ===================================================================

class TestSpreadResolveInputsEdgeCases:
    def _graph_with_spread(self, target_ports=None):
        """Helper: src -> tgt with spread=True on port 'state'."""
        src = CodeOperator(
            id="src", name="Src", code="",
            output_ports=[OutputPort(name="state", json_schema={"type": "object"})],
        )
        tgt_ports = target_ports or [
            InputPort(name="state", json_schema={"type": "object"}),
        ]
        tgt = CodeOperator(id="tgt", name="Tgt", code="", input_ports=tgt_ports)
        return Graph(
            nodes=[src, tgt],
            edges=[
                DataEdge(
                    id="e1",
                    source_node_id="src", source_port="state",
                    target_node_id="tgt", target_port="state",
                    spread=True,
                ),
            ],
            entry_points=["src"],
            exit_points=["tgt"],
        )

    def test_empty_dict_spread(self):
        """Spreading an empty dict produces only the landing port."""
        graph = self._graph_with_spread()
        store = PortDataStore()
        store.set("src", "state", {})

        inputs = store.resolve_inputs("tgt", graph)

        assert inputs == {"state": {}}

    def test_nested_dict_values_preserved(self):
        """Nested dicts are passed through as-is to spread fields."""
        graph = self._graph_with_spread()
        store = PortDataStore()
        store.set("src", "state", {"config": {"nested": True}, "count": 5})

        inputs = store.resolve_inputs("tgt", graph)

        assert inputs["state"] == {"config": {"nested": True}, "count": 5}
        assert inputs["config"] == {"nested": True}
        assert inputs["count"] == 5

    def test_landing_port_always_gets_full_dict(self):
        """The landing port should always receive the complete original dict."""
        graph = self._graph_with_spread()
        store = PortDataStore()
        payload = {"x": 1, "y": 2, "z": 3}
        store.set("src", "state", payload)

        inputs = store.resolve_inputs("tgt", graph)

        assert inputs["state"] == payload
        assert set(inputs.keys()) == {"state", "x", "y", "z"}

    def test_overlapping_key_with_explicit_edge(self):
        """Explicit scalar edge wins over spread field with same key."""
        extra_src = CodeOperator(
            id="extra", name="Extra", code="",
            output_ports=[OutputPort(name="x")],
        )
        src = CodeOperator(
            id="src", name="Src", code="",
            output_ports=[OutputPort(name="state", json_schema={"type": "object"})],
        )
        tgt = CodeOperator(
            id="tgt", name="Tgt", code="",
            input_ports=[
                InputPort(name="state", json_schema={"type": "object"}),
                InputPort(name="x", required=False),
            ],
        )
        graph = Graph(
            nodes=[src, tgt, extra_src],
            edges=[
                DataEdge(
                    id="e_spread",
                    source_node_id="src", source_port="state",
                    target_node_id="tgt", target_port="state",
                    spread=True,
                ),
                DataEdge(
                    id="e_scalar",
                    source_node_id="extra", source_port="x",
                    target_node_id="tgt", target_port="x",
                ),
            ],
            entry_points=["src", "extra"],
            exit_points=["tgt"],
        )

        store = PortDataStore()
        store.set("src", "state", {"x": "from_spread", "y": "only_spread"})
        store.set("extra", "x", "from_explicit")

        inputs = store.resolve_inputs("tgt", graph)

        assert inputs["state"] == {"x": "from_spread", "y": "only_spread"}
        assert inputs["y"] == "only_spread"
        assert inputs["x"] == "from_explicit"

    def test_spread_with_many_fields(self):
        """Spread a dict with many keys — all appear as individual inputs."""
        graph = self._graph_with_spread()
        store = PortDataStore()
        big_dict = {f"field_{i}": i for i in range(20)}
        store.set("src", "state", big_dict)

        inputs = store.resolve_inputs("tgt", graph)

        assert inputs["state"] == big_dict
        for key, val in big_dict.items():
            assert inputs[key] == val
