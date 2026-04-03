"""Unit tests for sub-graph context managers — while_loop, for_each, composite."""

from dan.builder import workflow
from dan.builder.refs import NodeRef


class TestWhileLoop:
    def test_basic_while_loop(self):
        wf = workflow("test")
        with wf.while_loop("loop", condition="counter < 5", max_iterations=10) as body:
            inc = body.code("inc", code="result = {'counter': counter + 1}")

        graph = wf.build()

        assert len(graph.nodes) == 1
        loop_node = graph.node_by_id("loop")
        assert loop_node is not None
        assert loop_node.node_type == "while_loop"
        assert loop_node.condition == "counter < 5"
        assert loop_node.max_iterations == 10
        assert loop_node.body_graph == "loop_body"
        assert "loop_body" in graph.sub_graphs

        sub = graph.sub_graphs["loop_body"]
        assert len(sub.nodes) == 1
        assert sub.nodes[0].id == "inc"

    def test_while_loop_with_chain(self):
        wf = workflow("test")
        with wf.while_loop("loop", condition="True") as body:
            a = body.llm("reviewer", model="m", prompt="Review")
            b = body.llm("reviser", prompt="Revise")
            a >> b

        graph = wf.build()
        sub = graph.sub_graphs["loop_body"]
        assert len(sub.nodes) == 2
        assert len(sub.edges) == 1

    def test_input_ref_creates_entry_input_port(self):
        wf = workflow("test")
        with wf.while_loop("loop", condition="True") as body:
            body.llm("step", model="m", prompt=f"Input: {body.input}")

        graph = wf.build()
        sub = graph.sub_graphs["loop_body"]
        step = sub.node_by_id("step")
        assert step is not None
        assert "input" in {p.name for p in step.input_ports}
        assert step.prompt_template == "Input: {input}"

    def test_entry_input_alias_creates_entry_input_port(self):
        wf = workflow("test")
        with wf.while_loop("loop", condition="True") as body:
            body.llm("step", model="m", prompt=f"Input: {body.entry_input}")

        graph = wf.build()
        sub = graph.sub_graphs["loop_body"]
        step = sub.node_by_id("step")
        assert step is not None
        assert "input" in {p.name for p in step.input_ports}
        assert step.prompt_template == "Input: {input}"


class TestForEach:
    def test_basic_for_each(self):
        wf = workflow("test")
        src = wf.llm("source", model="m", prompt="Generate list")
        with wf.for_each("fan", items=src["items"], parallelism=3) as body:
            proc = body.code("double", code="result = {'value': item * 2}")

        graph = wf.build()

        fan_node = graph.node_by_id("fan")
        assert fan_node is not None
        assert fan_node.node_type == "for_each"
        assert fan_node.parallelism == 3
        assert "fan_body" in graph.sub_graphs

        items_edge = [e for e in graph.edges if e.target_node_id == "fan" and e.target_port == "items"]
        assert len(items_edge) == 1
        assert items_edge[0].source_node_id == "source"
        assert items_edge[0].source_port == "items"

    def test_item_ref_creates_entry_input_port(self):
        wf = workflow("test")
        with wf.for_each("fan") as body:
            body.llm("writer", model="m", prompt=f"Write: {body.item}")

        graph = wf.build()
        sub = graph.sub_graphs["fan_body"]
        writer = sub.node_by_id("writer")
        assert writer is not None
        assert "item" in {p.name for p in writer.input_ports}
        assert writer.prompt_template == "Write: {item}"

    def test_entry_item_alias_creates_entry_input_port(self):
        wf = workflow("test")
        with wf.for_each("fan") as body:
            body.llm("writer", model="m", prompt=f"Write: {body.entry_item}")

        graph = wf.build()
        sub = graph.sub_graphs["fan_body"]
        writer = sub.node_by_id("writer")
        assert writer is not None
        assert "item" in {p.name for p in writer.input_ports}
        assert writer.prompt_template == "Write: {item}"


class TestComposite:
    def test_basic_composite(self):
        wf = workflow("test")
        with wf.composite("block", input_mappings={"data": "input"}, output_mappings={"output": "result"}) as sub:
            proc = sub.llm("inner", model="m", prompt="Process")

        graph = wf.build()

        comp_node = graph.node_by_id("block")
        assert comp_node is not None
        assert comp_node.node_type == "composite"
        assert comp_node.input_mappings == {"data": "input"}
        assert "block_body" in graph.sub_graphs


class TestValidatedComposite:
    """Tests for validated_composite — entry/exit validators with >> chaining."""

    def test_validated_composite_flows_through_validators(self):
        wf = workflow("test")
        a = wf.llm("a", model="m", prompt="A")
        with wf.validated_composite(
            "block",
            entry_schema={"type": "object", "required": ["x"]},
            exit_schema={"type": "object", "required": ["y"]},
        ) as block:
            block.llm("inner", model="m", prompt="Inner")
        b = wf.llm("b", model="m", prompt="B")
        a >> block >> b

        graph = wf.build()

        node_ids = {n.id for n in graph.nodes}
        assert "block__entry_validator" in node_ids
        assert "block__exit_validator" in node_ids
        assert "block" in node_ids

        data_edges = [(e.source_node_id, e.target_node_id) for e in graph.edges if getattr(e, "edge_type", None) == "data" or not hasattr(e, "edge_type")]
        if not data_edges:
            data_edges = [(e.source_node_id, e.target_node_id) for e in graph.edges]
        assert ("a", "block__entry_validator") in data_edges
        assert ("block__entry_validator", "block") in data_edges
        assert ("block", "block__exit_validator") in data_edges
        assert ("block__exit_validator", "b") in data_edges

    def test_validated_composite_with_custom_ports(self):
        wf = workflow("test")
        a = wf.llm("a", model="m", prompt="A")
        with wf.validated_composite(
            "block",
            input_ports=[{"name": "payload"}],
            output_ports=[{"name": "answer"}],
            entry_schema={"type": "object"},
            exit_schema={"type": "object"},
        ) as block:
            block.llm("inner", model="m", prompt="Inner")
        b = wf.llm("b", model="m", prompt="B")
        a >> block >> b

        graph = wf.build()

        data_edges = [e for e in graph.edges if getattr(e, "edge_type", None) == "data"]
        if not data_edges:
            data_edges = list(graph.edges)

        entry_to_block = [e for e in data_edges if e.source_node_id == "block__entry_validator" and e.target_node_id == "block"]
        assert len(entry_to_block) == 1
        assert entry_to_block[0].target_port == "payload"

        block_to_exit = [e for e in data_edges if e.source_node_id == "block" and e.target_node_id == "block__exit_validator"]
        assert len(block_to_exit) == 1
        assert block_to_exit[0].source_port == "answer"


class TestNestedSubGraphs:
    def test_chain_before_loop(self):
        wf = workflow("test")
        prep = wf.llm("prep", model="m", prompt="Prepare")
        with wf.while_loop("loop", condition="True", max_iterations=3) as body:
            body.llm("step", model="m", prompt="Step")
        loop_ref = NodeRef("loop", "while_loop", wf)
        prep >> loop_ref

        graph = wf.build()
        assert len(graph.nodes) == 2
        assert len(graph.sub_graphs) == 1
