"""Unit tests for the decompiler — Graph -> Python code -> Graph round-trip."""

from dan.builder import workflow, decompile
from dan.builder.decompiler import _to_var_name
from dan.models.context import ArtifactRef, ContextMode
from dan.models.control_flow import GateNode
from dan.models.edges import ContextEdge, ControlEdge, DataEdge
from dan.models.graph import Graph
from dan.models.nodes import CodeOperator
from dan.models.ports import InputPort, OutputPort


class TestVarNameGeneration:
    def test_simple_id(self):
        assert _to_var_name("idea_gen") == "idea_gen"

    def test_hyphens(self):
        assert _to_var_name("my-node") == "my_node"

    def test_leading_digit(self):
        assert _to_var_name("1_start") == "n_1_start"

    def test_keyword_collision(self):
        assert _to_var_name("input") == "input_node"

    def test_item_collision(self):
        assert _to_var_name("item") == "item_node"


class TestDecompileSimpleChain:
    def test_round_trip(self):
        wf = workflow("test_chain")
        a = wf.llm("gen", model="gpt-4o", prompt="Generate: {topic}")
        b = wf.llm("refine", prompt="Refine text")
        a >> b
        original = wf.build()

        code = decompile(original)

        assert "from dan.builder import workflow" in code
        assert "wf = workflow(" in code and "test_chain" in code
        assert "wf.llm" in code
        assert "graph = wf.build()" in code

    def test_decompiled_code_is_executable(self):
        wf = workflow("exec_test")
        a = wf.llm("step_a", model="m", prompt="A")
        b = wf.llm("step_b", prompt="B")
        a >> b
        original = wf.build()

        code = decompile(original)
        ns: dict = {}
        exec(code, ns)
        rebuilt = ns["graph"]

        assert isinstance(rebuilt, Graph)
        assert len(rebuilt.nodes) == len(original.nodes)
        assert len(rebuilt.edges) == len(original.edges)

    def test_chain_detection_emits_rshift(self):
        wf = workflow("chain_test")
        a = wf.llm("a", model="m", prompt="A")
        b = wf.llm("b", prompt="B")
        c = wf.llm("c", prompt="C")
        a >> b >> c
        original = wf.build()

        code = decompile(original)
        assert ">>" in code


class TestWorkerConvenienceAliases:
    def test_decompile_defaults_to_canonical_worker_output(self):
        wf = workflow("worker_canonical")
        wf.worker(
            "draft",
            model="gpt-4o",
            llm={"prompt_template": "Draft: {input}"},
        )

        graph = wf.build()
        code = decompile(graph)

        assert "wf.worker('draft'" in code
        assert "wf.llm('draft'" not in code

    def test_decompile_can_emit_llm_alias_for_simple_worker(self):
        wf = workflow("worker_llm_alias")
        wf.worker(
            "draft",
            model="gpt-4o",
            llm={
                "prompt_template": "Draft: {input}",
                "system_prompt": "You draft.",
                "temperature": 0.2,
            },
        )

        graph = wf.build()
        code = decompile(graph, use_convenience_aliases=True)

        assert "wf.llm('draft'" in code
        assert "input_ports=[{'name': 'input', 'required': False}]" in code
        assert "output_ports=[{'name': 'text'}]" in code

        ns: dict = {}
        exec(code, ns)
        rebuilt = ns["graph"].node_by_id("draft")
        assert rebuilt is not None
        assert rebuilt.node_type == "llm_operator"
        assert rebuilt.prompt_template == "Draft: {input}"
        assert rebuilt.system_prompt == "You draft."

    def test_decompile_can_emit_tool_alias_for_simple_worker(self):
        wf = workflow("worker_tool_alias")
        wf.worker("fetch", tool_ids=["web_search"])

        graph = wf.build()
        fetch = graph.node_by_id("fetch")
        assert fetch is not None
        fetch.metadata["tool_config"] = {"max_results": 3}

        code = decompile(graph, use_convenience_aliases=True)

        assert "wf.tool('fetch'" in code
        assert "tool_config={'max_results': 3}" in code

        ns: dict = {}
        exec(code, ns)
        rebuilt = ns["graph"].node_by_id("fetch")
        assert rebuilt is not None
        assert rebuilt.node_type == "tool_operator"
        assert rebuilt.tool_id == "web_search"
        assert rebuilt.tool_config == {"max_results": 3}

    def test_decompile_can_emit_code_alias_for_simple_worker(self):
        wf = workflow("worker_code_alias")
        wf.worker(
            "compute",
            code="result = {'answer': value * 2}",
            read_set=[{"key": "shared", "mode": "read"}],
            write_set=[{"key": "shared", "mode": "write"}],
        )

        graph = wf.build()
        code = decompile(graph, use_convenience_aliases=True)

        assert "wf.code('compute'" in code
        assert "read_set=[{'key': 'shared', 'mode': 'read'}]" in code
        assert "write_set=[{'key': 'shared', 'mode': 'write'}]" in code

        ns: dict = {}
        exec(code, ns)
        rebuilt = ns["graph"].node_by_id("compute")
        assert rebuilt is not None
        assert rebuilt.node_type == "code_operator"
        assert rebuilt.code == "result = {'answer': value * 2}"
        assert rebuilt.read_set[0].key == "shared"
        assert rebuilt.write_set[0].key == "shared"

    def test_decompile_can_emit_extended_aliases_for_simple_workers(self):
        wf = workflow("worker_extended_aliases", canonical_workers=True)
        inputs = wf.input_node(
            "inputs",
            variables=[{"name": "topic", "type": "string", "default": "agents"}],
        )
        retrieve = wf.rag("retrieve", collection="papers", top_k=3)
        reflect = wf.reflection(
            "reflect",
            reflection_prompt="Distill lessons.",
            reflection_model="test-model",
        )
        review = wf.human("review", prompt="Review the distilled lessons")
        checkpoint = wf.human_in_the_loop("checkpoint", prompt="Continue?")
        choose = wf.vote(
            "choose",
            prompt="Pick the best answer",
            candidates=["claude-sonnet-4-6", "gpt-4o"],
            num_votes=2,
        )
        aggregate = wf.reduce(
            "aggregate",
            reducer="len(input)",
            input_ports=[{"name": "input", "required": False}],
            output_ports=[{"name": "result"}],
        )
        inputs >> retrieve >> reflect >> review >> checkpoint >> choose >> aggregate

        graph = wf.build()
        code = decompile(graph, use_convenience_aliases=True)

        assert "wf.input_node('inputs'" in code
        assert "wf.rag('retrieve'" in code
        assert "wf.reflection('reflect'" in code
        assert "wf.human('review'" in code
        assert "wf.human_in_the_loop('checkpoint'" in code
        assert "wf.vote('choose'" in code
        assert "wf.reduce('aggregate'" in code

        ns: dict = {}
        exec(code, ns)
        rebuilt = ns["graph"]

        assert rebuilt.node_by_id("inputs").node_type == "input"
        assert rebuilt.node_by_id("retrieve").node_type == "rag_operator"
        assert rebuilt.node_by_id("reflect").node_type == "reflection"
        assert rebuilt.node_by_id("review").node_type == "human"
        assert rebuilt.node_by_id("checkpoint").node_type == "human_in_the_loop"
        assert rebuilt.node_by_id("choose").node_type == "vote"

    def test_decompile_input_node_round_trip_preserves_typed_output_schemas(self):
        wf = workflow("typed_input_roundtrip")
        wf.input_node(
            "workflow_inputs",
            variables=[
                {"name": "topic", "type": "string"},
                {"name": "research_depth", "type": "string", "default": "standard"},
                {"name": "approved", "type": "boolean"},
            ],
            description="Typed workflow inputs",
        )

        graph = wf.build()
        code = decompile(graph)

        ns: dict = {}
        exec(code, ns)
        rebuilt = ns["graph"].node_by_id("workflow_inputs")

        assert rebuilt is not None
        output_ports = {port.name: port for port in rebuilt.output_ports}
        assert output_ports["input"].json_schema == {"type": "object"}
        assert output_ports["topic"].json_schema == {"type": "string"}
        assert output_ports["research_depth"].json_schema == {"type": "string"}
        assert output_ports["approved"].json_schema == {"type": "boolean"}

    def test_decompile_llm_round_trip_preserves_explicit_empty_output_ports(self):
        wf = workflow("llm_empty_outputs")
        wf.llm(
            "survey_aspect",
            prompt="Summarize {papers}",
            output_schema={
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
            input_ports=[
                {"name": "papers", "required": True},
            ],
            output_ports=[],
        )

        graph = wf.build()
        code = decompile(graph)

        assert "output_ports=[]" in code

        ns: dict = {}
        exec(code, ns)
        rebuilt = ns["graph"].node_by_id("survey_aspect")

        assert rebuilt is not None
        assert rebuilt.output_ports == []

    def test_decompile_input_node_without_variables_preserves_untyped_input_port(self):
        wf = workflow("untyped_input_roundtrip")
        wf.input_node("workflow_inputs")

        graph = wf.build()
        code = decompile(graph)

        ns: dict = {}
        exec(code, ns)
        rebuilt = ns["graph"].node_by_id("workflow_inputs")

        assert rebuilt is not None
        output_ports = {port.name: port for port in rebuilt.output_ports}
        assert output_ports["input"].json_schema == {}

    def test_decompile_input_node_named_input_variable_preserves_untyped_port(self):
        wf = workflow("reserved_input_roundtrip")
        wf.input_node(
            "workflow_inputs",
            variables=[{"name": "input", "type": "string", "default": ""}],
        )

        graph = wf.build()
        code = decompile(graph)

        ns: dict = {}
        exec(code, ns)
        rebuilt = ns["graph"].node_by_id("workflow_inputs")

        assert rebuilt is not None
        output_ports = {port.name: port for port in rebuilt.output_ports}
        assert output_ports["input"].json_schema == {}


class TestDecompileWithSubGraph:
    def test_while_loop_emits_context_manager(self):
        wf = workflow("loop_test")
        with wf.while_loop("loop", condition="x < 5", max_iterations=10) as body:
            body.llm("step", model="m", prompt="Step")

        graph = wf.build()
        code = decompile(graph)

        assert "with wf.while_loop" in code
        assert "condition=" in code

    def test_for_each_emits_context_manager(self):
        wf = workflow("fe_test")
        with wf.for_each("fan", parallelism=4) as body:
            body.code("proc", code="result = item * 2")

        graph = wf.build()
        code = decompile(graph)

        assert "with wf.for_each" in code
        assert "parallelism=" in code


class TestDecompilePreservation:
    def test_shared_context_preserved(self):
        wf = workflow("ctx_test")
        wf.context("outline", json_schema={"type": "string"}, description="The outline")
        wf.llm("node", model="m", prompt="Hi")

        graph = wf.build()
        code = decompile(graph)

        assert "wf.context(" in code
        assert "'outline'" in code

    def test_metadata_preserved(self):
        wf = workflow("meta_test", description="A test workflow", tags=["test", "demo"])
        wf.llm("node", model="m", prompt="Hi")

        graph = wf.build()
        code = decompile(graph)

        assert "description=" in code
        assert "tags=" in code


class TestFullRoundTrip:
    def test_multi_node_round_trip(self):
        """Build a non-trivial graph, decompile, execute, and compare."""
        wf = workflow("round_trip")
        a = wf.llm("idea", model="gpt-4o", prompt="Generate idea about {topic}")
        b = wf.llm("plan", prompt="Plan for idea")
        c = wf.code("execute", code="result = {'done': True}")
        a >> b >> c

        original = wf.build()
        code = decompile(original)

        ns: dict = {}
        exec(code, ns)
        rebuilt = ns["graph"]

        assert len(rebuilt.nodes) == len(original.nodes)
        original_ids = sorted(n.id for n in original.nodes)
        rebuilt_ids = sorted(n.id for n in rebuilt.nodes)
        assert original_ids == rebuilt_ids


class TestDecompileFidelity:
    def test_custom_port_edge_not_rewritten_to_default_chain(self):
        a = CodeOperator(
            id="a",
            name="A",
            code="result={'x': 1}",
            output_ports=[OutputPort(name="custom_out")],
        )
        b = CodeOperator(
            id="b",
            name="B",
            code="result={'y': x}",
            input_ports=[InputPort(name="custom_in")],
            output_ports=[OutputPort(name="y")],
        )
        g = Graph(
            nodes=[a, b],
            edges=[
                DataEdge(
                    id="e1",
                    source_node_id="a",
                    source_port="custom_out",
                    target_node_id="b",
                    target_port="custom_in",
                )
            ],
            entry_points=["a"],
            exit_points=["b"],
        )

        code = decompile(g)
        ns: dict = {}
        exec(code, ns)
        rebuilt = ns["graph"]
        edge = rebuilt.edges[0]

        assert edge.source_port == "custom_out"
        assert edge.target_port == "custom_in"

    def test_control_and_context_edges_preserved(self):
        wf = workflow("edge_types")
        wf.context("ctx_key", json_schema={})

        src = wf.code(
            "src",
            code="result = {'flag': True}",
            output_ports=[{"name": "flag"}],
        )
        branch = wf.if_else(
            "branch",
            condition="flag",
            input_ports=[{"name": "flag"}],
            output_ports=[{"name": "branch"}, {"name": "flag"}],
        )
        wf.edge(src["flag"], branch["flag"])

        target = wf.code(
            "target",
            code="result = {'ok': True}",
            read_set=[{"key": "ctx_key", "mode": "read"}],
            input_ports=[
                {"name": "flag"},
                {"name": "branch", "required": False},
                {"name": "ctx", "required": False},
            ],
            output_ports=[{"name": "ok"}],
        )
        wf.edge(branch["flag"], target["flag"])
        wf.control_edge(branch["branch"], target["branch"], condition="true")

        with wf.for_each(
            "fan",
            read_set=[{"key": "ctx_key", "mode": "read"}],
            input_ports=[{"name": "items"}],
            output_ports=[{"name": "results"}],
        ) as body:
            body.code("worker", code="result = {'value': item}")
        from dan.builder.refs import NodeRef
        fan = NodeRef("fan", "for_each", wf)
        wf.context_edge(fan["results"], target["ctx"], context_key="ctx_key", mode=ContextMode.READ)

        original = wf.build()
        code = decompile(original)
        ns: dict = {}
        exec(code, ns)
        rebuilt = ns["graph"]

        original_types = sorted(e.edge_type for e in original.edges)
        rebuilt_types = sorted(e.edge_type for e in rebuilt.edges)
        assert rebuilt_types == original_types

    def test_ui_metadata_position_artifact_refs_preserved(self):
        node = CodeOperator(
            id="n",
            name="Node",
            code="result = {'ok': True}",
            output_ports=[OutputPort(name="ok")],
        )
        node.position.x = 12.0
        node.position.y = 34.0
        node.ui = {"collapsed": True}
        node.metadata = {"owner": "test"}
        g = Graph(
            nodes=[node],
            edges=[],
            entry_points=["n"],
            exit_points=["n"],
            artifact_refs=[ArtifactRef(uri="file://artifact.txt", description="test artifact")],
        )
        g.metadata.created_at = "2026-02-24T00:00:00Z"
        g.metadata.updated_at = "2026-02-24T01:00:00Z"

        code = decompile(g)
        ns: dict = {}
        exec(code, ns)
        rebuilt = ns["graph"]
        rn = rebuilt.node_by_id("n")

        assert rn.position.x == 12.0
        assert rn.position.y == 34.0
        assert rn.ui == {"collapsed": True}
        assert rn.metadata == {"owner": "test"}
        assert rebuilt.artifact_refs[0].uri == "file://artifact.txt"
        assert rebuilt.metadata.created_at == "2026-02-24T00:00:00Z"
        assert rebuilt.metadata.updated_at == "2026-02-24T01:00:00Z"


class TestGateRoundTrip:
    def test_gate_round_trip_if_else(self):
        wf = workflow("gate_ie")
        g = wf.gate("check", condition="score > 0.5")
        t = wf.code("yes", code="result = 'yes'")
        wf.edge(g["true"], t["input"])

        original = wf.build()
        code = decompile(original)

        assert "wf.gate(" in code
        assert "condition=" in code
        assert "score > 0.5" in code

        ns: dict = {}
        exec(code, ns)
        rebuilt = ns["graph"]

        assert isinstance(rebuilt, Graph)
        gate_nodes = [n for n in rebuilt.nodes if isinstance(n, GateNode)]
        assert len(gate_nodes) == 1
        assert gate_nodes[0].gate_mode == "if_else"
        assert gate_nodes[0].condition == "score > 0.5"

    def test_gate_round_trip_while(self):
        wf = workflow("gate_while")
        g = wf.gate("loop_gate", condition="iteration < 5", gate_mode="while", max_iterations=20)

        original = wf.build()
        code = decompile(original)

        assert "wf.gate(" in code
        assert "gate_mode='while'" in code
        assert "max_iterations=20" in code

        ns: dict = {}
        exec(code, ns)
        rebuilt = ns["graph"]

        gate_nodes = [n for n in rebuilt.nodes if isinstance(n, GateNode)]
        assert len(gate_nodes) == 1
        assert gate_nodes[0].gate_mode == "while"
        assert gate_nodes[0].max_iterations == 20
        port_names = {p.name for p in gate_nodes[0].output_ports}
        assert "continue" in port_names
        assert "done" in port_names

    def test_gate_default_mode_not_emitted(self):
        """When gate_mode is the default 'if_else', it shouldn't appear in output."""
        wf = workflow("gate_default")
        wf.gate("g", condition="x > 0")
        code = decompile(wf.build())
        assert "gate_mode=" not in code

    def test_gate_default_max_iterations_not_emitted(self):
        """When max_iterations is the default 10, it shouldn't appear in output."""
        wf = workflow("gate_mi")
        wf.gate("g", condition="x > 0", gate_mode="while")
        code = decompile(wf.build())
        assert "max_iterations=" not in code
