"""Graph well-formedness validation tests."""

from dan.models.context import (
    ContextDeclaration,
    ContextMode,
    SharedContextDeclaration,
)
from dan.models.control_flow import CompositeNode, ForEachNode, WhileLoopNode
from dan.models.edges import ContextEdge, ControlEdge, DataEdge
from dan.models.graph import Graph, GraphMetadata
from dan.models.node_taxonomy import RUNTIME_NODE_TYPE_MAP
from dan.models.nodes import LLMOperator
from dan.models.ports import InputPort, OutputPort
from dan.registry import NodeTypeRegistry
from dan.validation.graph import validate_graph

_STR = {"type": "string"}


def _node(
    id: str,
    *,
    inp: list[InputPort] | None = None,
    out: list[OutputPort] | None = None,
) -> LLMOperator:
    return LLMOperator(
        id=id, name=id, model="m", prompt_template="p",
        input_ports=inp or [],
        output_ports=out or [],
    )


def _wired_pair():
    """Two nodes with matching ports and a data edge."""
    a = _node("a", out=[OutputPort(name="o", json_schema=_STR)])
    b = _node("b", inp=[InputPort(name="i", json_schema=_STR)])
    e = DataEdge(id="e", source_node_id="a", source_port="o", target_node_id="b", target_port="i")
    return a, b, e


# -----------------------------------------------------------------------
# Entry / exit
# -----------------------------------------------------------------------

class TestEntryExit:
    def test_valid(self):
        g = Graph(nodes=[_node("a")], entry_points=["a"], exit_points=["a"])
        assert validate_graph(g) == []

    def test_missing_entry_node(self):
        g = Graph(nodes=[_node("a")], entry_points=["missing"])
        errors = validate_graph(g)
        assert any("missing" in e for e in errors)

    def test_no_entry_points(self):
        g = Graph(nodes=[_node("a")])
        errors = validate_graph(g)
        assert any("no entry points" in e for e in errors)


# -----------------------------------------------------------------------
# Reachability
# -----------------------------------------------------------------------

class TestReachability:
    def test_all_reachable(self):
        a, b, e = _wired_pair()
        g = Graph(nodes=[a, b], edges=[e], entry_points=["a"], exit_points=["b"])
        assert validate_graph(g) == []

    def test_orphan_node(self):
        g = Graph(
            nodes=[_node("a"), _node("orphan")],
            entry_points=["a"], exit_points=["a"],
        )
        errors = validate_graph(g)
        assert any("orphan" in e and "unreachable" in e for e in errors)


# -----------------------------------------------------------------------
# Required ports
# -----------------------------------------------------------------------

class TestRequiredPorts:
    def test_connected(self):
        a, b, e = _wired_pair()
        b.input_ports[0].required = True
        g = Graph(nodes=[a, b], edges=[e], entry_points=["a"], exit_points=["b"])
        assert validate_graph(g) == []

    def test_unconnected_required(self):
        a = _node("a", out=[OutputPort(name="o", json_schema=_STR)])
        b = _node("b", inp=[
            InputPort(name="connected", json_schema=_STR, required=False),
            InputPort(name="missing", json_schema=_STR, required=True),
        ])
        e = DataEdge(id="e", source_node_id="a", source_port="o", target_node_id="b", target_port="connected")
        g = Graph(nodes=[a, b], edges=[e], entry_points=["a"], exit_points=["b"])
        errors = validate_graph(g)
        assert any("required input port 'missing'" in e for e in errors)

    def test_optional_port_ok(self):
        a = _node("a", out=[OutputPort(name="o", json_schema=_STR)])
        b = _node("b", inp=[InputPort(name="i", json_schema=_STR, required=False)])
        e = DataEdge(id="e", source_node_id="a", source_port="o", target_node_id="b", target_port="i")
        g = Graph(nodes=[a, b], edges=[e], entry_points=["a"], exit_points=["b"])
        errors = validate_graph(g)
        assert not any("required input port" in e for e in errors)


# -----------------------------------------------------------------------
# Sub-graph references
# -----------------------------------------------------------------------

class TestSubGraphRefs:
    def test_valid_ref(self):
        inner = Graph(nodes=[_node("inner_a")], entry_points=["inner_a"], exit_points=["inner_a"])
        loop = WhileLoopNode(
            id="loop", name="loop", condition="True", body_graph="inner",
        )
        g = Graph(
            nodes=[loop], sub_graphs={"inner": inner},
            entry_points=["loop"], exit_points=["loop"],
        )
        errors = validate_graph(g)
        assert not any("sub-graph" in e for e in errors)

    def test_missing_ref(self):
        loop = WhileLoopNode(
            id="loop", name="loop", condition="True", body_graph="nonexistent",
        )
        g = Graph(nodes=[loop], entry_points=["loop"], exit_points=["loop"])
        errors = validate_graph(g)
        assert any("nonexistent" in e for e in errors)


# -----------------------------------------------------------------------
# Edge endpoint existence (all edge types)
# -----------------------------------------------------------------------

class TestEdgeEndpoints:
    def test_valid_data_edge(self):
        a, b, e = _wired_pair()
        g = Graph(nodes=[a, b], edges=[e], entry_points=["a"], exit_points=["b"])
        assert not any("not found" in err for err in validate_graph(g))

    def test_data_edge_missing_source_node(self):
        b = _node("b", inp=[InputPort(name="i", json_schema=_STR)])
        e = DataEdge(id="e", source_node_id="ghost", source_port="o", target_node_id="b", target_port="i")
        g = Graph(nodes=[b], edges=[e], entry_points=["b"], exit_points=["b"])
        errors = validate_graph(g)
        assert any("source node 'ghost' not found" in e for e in errors)

    def test_data_edge_missing_port(self):
        a = _node("a", out=[OutputPort(name="real_out", json_schema=_STR)])
        b = _node("b", inp=[InputPort(name="i", json_schema=_STR)])
        e = DataEdge(id="e", source_node_id="a", source_port="wrong_port", target_node_id="b", target_port="i")
        g = Graph(nodes=[a, b], edges=[e], entry_points=["a"], exit_points=["b"])
        errors = validate_graph(g)
        assert any("no output port 'wrong_port'" in e for e in errors)

    def test_control_edge_missing_node(self):
        a = _node("a", out=[OutputPort(name="o", json_schema=_STR)])
        e = ControlEdge(id="ce", source_node_id="a", source_port="o", target_node_id="ghost", target_port="i")
        g = Graph(nodes=[a], edges=[e], entry_points=["a"], exit_points=["a"])
        errors = validate_graph(g)
        assert any("target node 'ghost' not found" in e for e in errors)

    def test_control_edge_missing_port(self):
        a = _node("a", out=[OutputPort(name="o", json_schema=_STR)])
        b = _node("b", inp=[InputPort(name="i", json_schema=_STR)])
        e = ControlEdge(id="ce", source_node_id="a", source_port="bad", target_node_id="b", target_port="i")
        g = Graph(nodes=[a, b], edges=[e], entry_points=["a"], exit_points=["b"])
        errors = validate_graph(g)
        assert any("no output port 'bad'" in e for e in errors)

    def test_context_edge_missing_node(self):
        a = _node("a", out=[OutputPort(name="o", json_schema=_STR)])
        e = ContextEdge(
            id="cxe", source_node_id="ghost", source_port="o",
            target_node_id="a", target_port="o",
            context_key="k", mode=ContextMode.READ,
        )
        g = Graph(
            nodes=[a], edges=[e], entry_points=["a"], exit_points=["a"],
            shared_context=[SharedContextDeclaration(key="k")],
        )
        errors = validate_graph(g)
        assert any("source node 'ghost' not found" in e for e in errors)


# -----------------------------------------------------------------------
# Schema compatibility
# -----------------------------------------------------------------------

class TestSchemaCompatibility:
    def test_compatible_edge(self):
        a, b, e = _wired_pair()
        g = Graph(nodes=[a, b], edges=[e], entry_points=["a"], exit_points=["b"])
        assert validate_graph(g) == []

    def test_incompatible_edge(self):
        a = _node("a", out=[OutputPort(name="o", json_schema={"type": "integer"})])
        b = _node("b", inp=[InputPort(name="i", json_schema={"type": "string"})])
        e = DataEdge(id="e", source_node_id="a", source_port="o", target_node_id="b", target_port="i")
        g = Graph(nodes=[a, b], edges=[e], entry_points=["a"], exit_points=["b"])
        errors = validate_graph(g)
        assert any("schema incompatibility" in e for e in errors)

    def test_empty_schema_warning(self):
        a = _node("a", out=[OutputPort(name="o")])
        b = _node("b", inp=[InputPort(name="i")])
        e = DataEdge(id="e", source_node_id="a", source_port="o", target_node_id="b", target_port="i")
        g = Graph(nodes=[a, b], edges=[e], entry_points=["a"], exit_points=["b"])
        errors = validate_graph(g)
        assert any("schema safety bypassed" in e for e in errors)

    def test_one_side_empty_schema_warning(self):
        a = _node("a", out=[OutputPort(name="o", json_schema=_STR)])
        b = _node("b", inp=[InputPort(name="i")])
        e = DataEdge(id="e", source_node_id="a", source_port="o", target_node_id="b", target_port="i")
        g = Graph(nodes=[a, b], edges=[e], entry_points=["a"], exit_points=["b"])
        errors = validate_graph(g)
        assert any("schema safety bypassed" in e for e in errors)


# -----------------------------------------------------------------------
# Context declarations
# -----------------------------------------------------------------------

class TestContextDeclarations:
    def test_declared_context_ok(self):
        a = _node("a", out=[OutputPort(name="o", json_schema=_STR)])
        b = _node("b", inp=[InputPort(name="i", json_schema=_STR)])
        g = Graph(
            nodes=[a, b],
            edges=[
                DataEdge(id="e1", source_node_id="a", source_port="o", target_node_id="b", target_port="i"),
                ContextEdge(
                    id="ce1", source_node_id="a", source_port="o",
                    target_node_id="b", target_port="i",
                    context_key="outline", mode=ContextMode.READ,
                ),
            ],
            entry_points=["a"], exit_points=["b"],
            shared_context=[SharedContextDeclaration(key="outline", json_schema=_STR)],
        )
        errors = validate_graph(g)
        assert not any("undeclared" in e for e in errors)

    def test_undeclared_context(self):
        a = _node("a", out=[OutputPort(name="o", json_schema=_STR)])
        b = _node("b", inp=[InputPort(name="i", json_schema=_STR)])
        g = Graph(
            nodes=[a, b],
            edges=[
                DataEdge(id="e1", source_node_id="a", source_port="o", target_node_id="b", target_port="i"),
                ContextEdge(
                    id="ce1", source_node_id="a", source_port="o",
                    target_node_id="b", target_port="i",
                    context_key="secret", mode=ContextMode.WRITE,
                ),
            ],
            entry_points=["a"], exit_points=["b"],
        )
        errors = validate_graph(g)
        assert any("undeclared" in e and "secret" in e for e in errors)


# -----------------------------------------------------------------------
# Context edge permission enforcement
# -----------------------------------------------------------------------

class TestContextEdgePermissions:
    def test_read_declared_in_read_set(self):
        loop = WhileLoopNode(
            id="loop", name="loop", condition="True", body_graph="inner",
            input_ports=[InputPort(name="i", json_schema=_STR)],
            output_ports=[OutputPort(name="o", json_schema=_STR)],
            read_set=[ContextDeclaration(key="outline", mode=ContextMode.READ)],
        )
        inner = Graph(nodes=[_node("x")], entry_points=["x"], exit_points=["x"])
        g = Graph(
            nodes=[loop],
            edges=[
                ContextEdge(
                    id="ce", source_node_id="loop", source_port="o",
                    target_node_id="loop", target_port="i",
                    context_key="outline", mode=ContextMode.READ,
                ),
            ],
            sub_graphs={"inner": inner},
            entry_points=["loop"], exit_points=["loop"],
            shared_context=[SharedContextDeclaration(key="outline", json_schema=_STR)],
        )
        errors = validate_graph(g)
        assert not any("read_set" in e for e in errors)

    def test_read_not_declared(self):
        a = _node("a", out=[OutputPort(name="o", json_schema=_STR)])
        b = _node("b", inp=[InputPort(name="i", json_schema=_STR)])
        g = Graph(
            nodes=[a, b],
            edges=[
                DataEdge(id="e1", source_node_id="a", source_port="o", target_node_id="b", target_port="i"),
                ContextEdge(
                    id="ce", source_node_id="a", source_port="o",
                    target_node_id="b", target_port="i",
                    context_key="outline", mode=ContextMode.READ,
                ),
            ],
            entry_points=["a"], exit_points=["b"],
            shared_context=[SharedContextDeclaration(key="outline", json_schema=_STR)],
        )
        errors = validate_graph(g)
        assert any("read_set" in e and "outline" in e for e in errors)
        # READ validates target (consumer), not source
        assert any("'b'" in e for e in errors)

    def test_read_validates_target_not_source(self):
        """READ mode: target consumes context; validation checks target's read_set, not source's."""
        a = _node("a", out=[OutputPort(name="o", json_schema=_STR)])
        loop = WhileLoopNode(
            id="loop", name="loop", condition="True", body_graph="inner",
            input_ports=[InputPort(name="i", json_schema=_STR)],
            output_ports=[OutputPort(name="o", json_schema=_STR)],
            read_set=[ContextDeclaration(key="outline", mode=ContextMode.READ)],
        )
        inner = Graph(nodes=[_node("x")], entry_points=["x"], exit_points=["x"])
        g = Graph(
            nodes=[a, loop],
            edges=[
                ContextEdge(
                    id="ce", source_node_id="a", source_port="o",
                    target_node_id="loop", target_port="i",
                    context_key="outline", mode=ContextMode.READ,
                ),
            ],
            sub_graphs={"inner": inner},
            entry_points=["a"], exit_points=["loop"],
            shared_context=[SharedContextDeclaration(key="outline", json_schema=_STR)],
        )
        errors = validate_graph(g)
        assert not any("read_set" in e for e in errors)

    def test_write_declared_in_write_set(self):
        loop = WhileLoopNode(
            id="loop", name="loop", condition="True", body_graph="inner",
            input_ports=[InputPort(name="i", json_schema=_STR)],
            output_ports=[OutputPort(name="o", json_schema=_STR)],
            write_set=[ContextDeclaration(key="draft", mode=ContextMode.WRITE)],
        )
        inner = Graph(nodes=[_node("x")], entry_points=["x"], exit_points=["x"])
        g = Graph(
            nodes=[loop],
            edges=[
                ContextEdge(
                    id="ce", source_node_id="loop", source_port="o",
                    target_node_id="loop", target_port="i",
                    context_key="draft", mode=ContextMode.WRITE,
                ),
            ],
            sub_graphs={"inner": inner},
            entry_points=["loop"], exit_points=["loop"],
            shared_context=[SharedContextDeclaration(key="draft", json_schema=_STR)],
        )
        errors = validate_graph(g)
        assert not any("write_set" in e for e in errors)

    def test_write_not_declared(self):
        a = _node("a", out=[OutputPort(name="o", json_schema=_STR)])
        b = _node("b", inp=[InputPort(name="i", json_schema=_STR)])
        g = Graph(
            nodes=[a, b],
            edges=[
                DataEdge(id="e1", source_node_id="a", source_port="o", target_node_id="b", target_port="i"),
                ContextEdge(
                    id="ce", source_node_id="a", source_port="o",
                    target_node_id="b", target_port="i",
                    context_key="draft", mode=ContextMode.WRITE,
                ),
            ],
            entry_points=["a"], exit_points=["b"],
            shared_context=[SharedContextDeclaration(key="draft", json_schema=_STR)],
        )
        errors = validate_graph(g)
        assert any("write_set" in e and "draft" in e for e in errors)

    def test_append_requires_write_set(self):
        a = _node("a", out=[OutputPort(name="o", json_schema=_STR)])
        b = _node("b", inp=[InputPort(name="i", json_schema=_STR)])
        g = Graph(
            nodes=[a, b],
            edges=[
                DataEdge(id="e1", source_node_id="a", source_port="o", target_node_id="b", target_port="i"),
                ContextEdge(
                    id="ce", source_node_id="a", source_port="o",
                    target_node_id="b", target_port="i",
                    context_key="log", mode=ContextMode.APPEND,
                ),
            ],
            entry_points=["a"], exit_points=["b"],
            shared_context=[SharedContextDeclaration(key="log", json_schema=_STR)],
        )
        errors = validate_graph(g)
        assert any("write_set" in e and "log" in e for e in errors)


# -----------------------------------------------------------------------
# Data-edge cycles
# -----------------------------------------------------------------------

class TestDataCycles:
    def test_no_cycle(self):
        a = _node("a", out=[OutputPort(name="o", json_schema=_STR)])
        b = _node("b", inp=[InputPort(name="i", json_schema=_STR)], out=[OutputPort(name="o", json_schema=_STR)])
        c = _node("c", inp=[InputPort(name="i", json_schema=_STR)])
        g = Graph(
            nodes=[a, b, c],
            edges=[
                DataEdge(id="e1", source_node_id="a", source_port="o", target_node_id="b", target_port="i"),
                DataEdge(id="e2", source_node_id="b", source_port="o", target_node_id="c", target_port="i"),
            ],
            entry_points=["a"], exit_points=["c"],
        )
        errors = validate_graph(g)
        assert not any("cycle" in e.lower() for e in errors)

    def test_cycle_detected(self):
        a = _node("a", inp=[InputPort(name="i", json_schema=_STR)], out=[OutputPort(name="o", json_schema=_STR)])
        b = _node("b", inp=[InputPort(name="i", json_schema=_STR)], out=[OutputPort(name="o", json_schema=_STR)])
        g = Graph(
            nodes=[a, b],
            edges=[
                DataEdge(id="e1", source_node_id="a", source_port="o", target_node_id="b", target_port="i"),
                DataEdge(id="e2", source_node_id="b", source_port="o", target_node_id="a", target_port="i"),
            ],
            entry_points=["a"], exit_points=["b"],
        )
        errors = validate_graph(g)
        assert any("cycle" in e.lower() for e in errors)


# -----------------------------------------------------------------------
# Registry discovery
# -----------------------------------------------------------------------

class TestNodeTypeRegistry:
    def test_builtins_registered(self):
        assert NodeTypeRegistry.is_registered("llm_operator")
        assert NodeTypeRegistry.is_registered("composite")
        assert NodeTypeRegistry.all_types() == RUNTIME_NODE_TYPE_MAP

    def test_get_known_type(self):
        cls = NodeTypeRegistry.get("while_loop")
        assert cls is WhileLoopNode

    def test_get_unknown_type_raises(self):
        import pytest
        with pytest.raises(KeyError, match="custom_node"):
            NodeTypeRegistry.get("custom_node")
