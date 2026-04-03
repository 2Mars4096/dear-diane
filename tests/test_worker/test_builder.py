from __future__ import annotations

from dan.builder import decompile, workflow
from dan.models.context import MergeStrategy
from dan.models.graph import Graph
from dan.worker.model import Worker


def test_worker_builder_compiles_resource_refs_and_edge_lint() -> None:
    wf = workflow("worker_graph")
    wf.resource("instruction_profiles", "review_profile", {"instruction": "Review carefully"})
    wf.resource("toolsets", "fs_tools", {"tool_ids": ["file_read"]})

    source = wf.worker(
        "source",
        code="result = {'summary': 'hello'}",
        output_ports=[{"name": "result", "schema": {}}],
    )
    review = wf.worker(
        "review",
        role="reviewer",
        instruction="Double-check the summary",
        model="test-model",
        context={
            "instruction_profile_ref": "review_profile",
            "toolset_refs": ["fs_tools"],
        },
        llm_hints={"prompt_template": "Review {input}"},
        input_ports=[{"name": "input", "schema": {}, "required": False}],
        output_ports=[{"name": "text", "schema": {}}],
    )
    wf.edge(
        source["result"],
        review["input"],
        lint={"structural": {"required_keys": ["summary"]}, "severity": "error"},
    )

    graph = wf.build()
    review_node = graph.node_by_id("review")
    assert isinstance(review_node, Worker)
    assert review_node.context is not None
    assert review_node.context.instruction_profile_ref == "review_profile"
    assert review_node.context.toolset_refs == ["fs_tools"]
    assert graph.worker_resources["toolsets"]["fs_tools"]["tool_ids"] == ["file_read"]
    assert graph.edges[0].lint is not None
    assert graph.edges[0].lint.structural is not None
    assert graph.edges[0].lint.structural.required_keys == ["summary"]
    assert graph.edges[0].metadata["lint"]["structural"]["required_keys"] == ["summary"]


def test_worker_builder_round_trips_resources_and_lint() -> None:
    wf = workflow("worker_roundtrip")
    wf.resource("instruction_profiles", "judge", {"instruction": "Judge quality"})
    source = wf.worker(
        "writer",
        code="result = {'draft': 'ok'}",
        output_ports=[{"name": "result", "schema": {}}],
    )
    target = wf.worker(
        "judge",
        role="judge",
        context={"instruction_profile_ref": "judge"},
        input_ports=[{"name": "input", "schema": {}, "required": False}],
        output_ports=[{"name": "result", "schema": {}}],
    )
    wf.edge(
        source["result"],
        target["input"],
        lint={
            "structural": {"required_keys": ["draft"]},
            "severity": "error",
        },
    )

    graph = wf.build()
    assert isinstance(graph.node_by_id("writer"), Worker)
    assert graph.worker_resources["instruction_profiles"]["judge"]["instruction"] == "Judge quality"
    assert graph.edges[0].lint is not None
    assert graph.edges[0].lint.structural is not None
    assert graph.edges[0].lint.structural.required_keys == ["draft"]
    lint_cfg = graph.edges[0].metadata["lint"]
    assert lint_cfg["structural"]["required_keys"] == ["draft"]

    code = decompile(graph)
    assert "wf.resource('instruction_profiles', 'judge'" in code
    assert "wf.worker('judge'" in code
    assert "'required_keys': ['draft']" in code
    assert "'severity': 'error'" in code
    assert "'enabled': True" in code


def test_worker_scope_round_trips_body_and_named_sub_workers() -> None:
    wf = workflow("worker_scope_rt")
    with wf.worker_scope(
        "manager",
        role="manager",
        instruction="Coordinate the local plan and delegate parallel work",
        model="test-model",
        llm={"prompt_template": "Plan from {input}"},
        input_mappings={"brief": "planner::input"},
        output_mappings={"text": "final_text"},
        parallelism=2,
        merge_strategy=MergeStrategy.LAST_WRITE_WINS,
        spawn_policy={"max_spawns_per_node": 2},
        external_input_schema={"type": "object", "required": ["brief"]},
        external_output_schema={"type": "object", "required": ["text"]},
        control_state_schema={"type": "object", "properties": {"iteration": {"type": "integer"}}},
        local_state={
            "json_schema": {"type": "object", "properties": {"history": {"type": "array"}}},
            "description": "manager-local state",
        },
        compaction_rule={"strategy": "sliding_window", "window_size": 2},
        failure_policy={"max_iterations": 3, "stagnation_threshold": 2},
        projections=[{"name": "manager_view", "local_state_keys": ["history"]}],
        output_ports=[{"name": "text"}],
    ) as manager:
        planner = manager.code("planner", code="result = {'plan': 'draft'}")
        reviewer = manager.worker(
            "reviewer",
            role="reviewer",
            instruction="Review the draft plan",
            validation_rules=[{"rule_type": "required_keys", "config": {"keys": ["plan"]}}],
            input_ports=[{"name": "input", "required": False}],
        )
        manager.edge(
            planner["result"],
            reviewer["input"],
            lint={"structural": {"required_keys": ["plan"]}, "severity": "error"},
        )
        with manager.sub_worker("research") as research:
            research.code("collect", code="result = {'notes': ['a', 'b']}")

    graph = wf.build()
    manager_node = graph.node_by_id("manager")
    assert isinstance(manager_node, Worker)
    assert manager_node.body_graph == "manager_body"
    assert manager_node.sub_workers == {"research": "manager_research"}
    assert manager_node.input_mappings == {"brief": "planner::input"}
    assert manager_node.output_mappings == {"text": "final_text"}
    assert manager_node.parallelism == 2
    assert manager_node.merge_strategy == MergeStrategy.LAST_WRITE_WINS
    assert manager_node.spawn_policy is not None
    assert manager_node.spawn_policy.max_spawns_per_node == 2
    assert manager_node.external_input_schema == {"type": "object", "required": ["brief"]}
    assert manager_node.external_output_schema == {"type": "object", "required": ["text"]}
    assert manager_node.control_state_schema["type"] == "object"
    assert manager_node.local_state.description == "manager-local state"
    assert manager_node.compaction_rule is not None
    assert manager_node.compaction_rule.window_size == 2
    assert manager_node.failure_policy.max_iterations == 3
    assert manager_node.projections[0].name == "manager_view"
    assert "manager_body" in graph.sub_graphs
    assert "manager_research" in graph.sub_graphs
    reviewer_node = graph.sub_graphs["manager_body"].node_by_id("reviewer")
    assert isinstance(reviewer_node, Worker)
    assert reviewer_node.validation_rules[0].rule_type == "required_keys"
    assert graph.sub_graphs["manager_body"].edges[0].lint is not None
    assert graph.sub_graphs["manager_body"].edges[0].lint.severity.value == "error"
    assert graph.sub_graphs["manager_body"].edges[0].metadata["lint"]["severity"] == "error"

    code = decompile(graph)
    assert "with wf.worker_scope('manager'" in code
    assert ".sub_worker('research')" in code
    assert "input_mappings={'brief': 'planner::input'}" in code
    assert "output_mappings={'text': 'final_text'}" in code
    assert "parallelism=2" in code
    assert "merge_strategy='last_write_wins'" in code
    assert "spawn_policy={" in code
    assert "external_input_schema={'type': 'object', 'required': ['brief']}" in code
    assert "external_output_schema={'type': 'object', 'required': ['text']}" in code
    assert "control_state_schema={'type': 'object', 'properties': {'iteration': {'type': 'integer'}}}" in code
    assert "local_state={'json_schema': {'type': 'object', 'properties': {'history': {'type': 'array'}}}, 'description': 'manager-local state'}" in code
    assert "compaction_rule={'strategy': 'sliding_window', 'window_size': 2, 'require_persistent_recall': True}" in code
    assert "failure_policy={'max_iterations': 3, 'stagnation_threshold': 2}" in code
    assert "projections=[{'name': 'manager_view', 'context_keys': [], 'local_state_keys': ['history'], 'artifact_uris': [], 'description': ''}]" in code
    assert "'max_spawns_per_node': 2" in code
    assert "validation_rules=[{'rule_type': 'required_keys', 'config': {'keys': ['plan']}}]" in code
    assert "'required_keys': ['plan']" in code
    assert "'severity': 'error'" in code
    assert "'enabled': True" in code

    ns: dict[str, object] = {}
    exec(code, ns)
    rebuilt = ns["graph"]
    assert isinstance(rebuilt, Graph)
    rebuilt_manager = rebuilt.node_by_id("manager")
    assert isinstance(rebuilt_manager, Worker)
    assert rebuilt_manager.body_graph == "manager_body"
    assert rebuilt_manager.sub_workers == {"research": "manager_research"}
    assert rebuilt_manager.input_mappings == {"brief": "planner::input"}
    assert rebuilt_manager.output_mappings == {"text": "final_text"}
    assert rebuilt_manager.parallelism == 2
    assert rebuilt_manager.merge_strategy == MergeStrategy.LAST_WRITE_WINS
    assert rebuilt_manager.spawn_policy is not None
    assert rebuilt_manager.spawn_policy.max_spawns_per_node == 2
    assert rebuilt_manager.external_input_schema == {"type": "object", "required": ["brief"]}
    assert rebuilt_manager.external_output_schema == {"type": "object", "required": ["text"]}
    assert rebuilt_manager.control_state_schema["type"] == "object"
    assert rebuilt_manager.local_state.description == "manager-local state"
    assert rebuilt_manager.compaction_rule is not None
    assert rebuilt_manager.compaction_rule.window_size == 2
    assert rebuilt_manager.failure_policy.max_iterations == 3
    assert rebuilt_manager.projections[0].name == "manager_view"
    rebuilt_reviewer = rebuilt.sub_graphs["manager_body"].node_by_id("reviewer")
    assert isinstance(rebuilt_reviewer, Worker)
    assert rebuilt_reviewer.validation_rules[0].rule_type == "required_keys"
    assert rebuilt.sub_graphs["manager_body"].edges[0].lint is not None
    assert rebuilt.sub_graphs["manager_body"].edges[0].lint.severity.value == "error"
    assert rebuilt.sub_graphs["manager_body"].edges[0].metadata["lint"]["severity"] == "error"


def test_worker_builder_round_trips_composite_contract_fields() -> None:
    wf = workflow("worker_composite_contract")
    node = wf.worker(
        "review",
        role="manager",
        external_input_schema={"type": "object", "required": ["draft"]},
        external_output_schema={"type": "object", "required": ["summary"]},
        control_state_schema={"type": "object", "properties": {"iteration": {"type": "integer"}}},
        local_state={
            "json_schema": {"type": "object", "properties": {"history": {"type": "array"}}},
            "description": "review-local state",
        },
        compaction_rule={"strategy": "sliding_window", "window_size": 2},
        failure_policy={"max_iterations": 4},
        projections=[{"name": "review_view", "local_state_keys": ["history"]}],
    )

    graph = wf.build()
    worker = graph.node_by_id(node.node_id)
    assert isinstance(worker, Worker)
    assert worker.external_input_schema == {"type": "object", "required": ["draft"]}
    assert worker.external_output_schema == {"type": "object", "required": ["summary"]}
    assert worker.control_state_schema["type"] == "object"
    assert worker.local_state.description == "review-local state"
    assert worker.compaction_rule is not None
    assert worker.failure_policy.max_iterations == 4
    assert worker.projections[0].name == "review_view"

    code = decompile(graph)
    assert "external_input_schema={'type': 'object', 'required': ['draft']}" in code
    assert "external_output_schema={'type': 'object', 'required': ['summary']}" in code
    assert "compaction_rule={'strategy': 'sliding_window', 'window_size': 2, 'require_persistent_recall': True}" in code


def test_worker_scope_accepts_llm_alias() -> None:
    wf = workflow("worker_scope_llm")
    worker = wf.worker(
        "judge",
        model="test-model",
        llm={"prompt_template": "Judge {input}", "temperature": 0.1},
    )

    graph = wf.build()
    node = graph.node_by_id(worker.node_id)
    assert isinstance(node, Worker)
    assert node.llm_hints is not None
    assert node.llm_hints.prompt_template == "Judge {input}"
    assert node.llm_hints.temperature == 0.1


def test_worker_builder_preserves_tool_config_metadata() -> None:
    wf = workflow("worker_tool_config")
    worker = wf.worker(
        "fetch",
        role="tool_runner",
        tool_ids=["file_read"],
        tool_config={"path": "notes.txt"},
        output_ports=[{"name": "result"}],
    )

    graph = wf.build()
    node = graph.node_by_id(worker.node_id)
    assert isinstance(node, Worker)
    assert node.metadata["tool_config"] == {"path": "notes.txt"}

    code = decompile(graph)
    assert "{'tool_config': {'path': 'notes.txt'}}" in code


def test_worker_builder_round_trips_control_flow_config() -> None:
    wf = workflow("worker_control_flow")
    route = wf.worker(
        "route",
        control_flow={
            "condition": "score > 0.5",
            "gate_mode": "if_else",
            "max_iterations": 3,
        },
    )

    graph = wf.build()
    node = graph.node_by_id(route.node_id)
    assert isinstance(node, Worker)
    assert node.control_flow is not None
    assert node.control_flow.condition == "score > 0.5"
    assert node.control_flow.gate_mode == "if_else"
    assert [port.name for port in node.output_ports] == ["true", "false"]

    code = decompile(graph)
    assert "wf.worker('route'" in code
    assert "control_flow={'condition': 'score > 0.5', 'gate_mode': 'if_else', 'max_iterations': 3}" in code

    ns: dict[str, object] = {}
    exec(code, ns)
    rebuilt = ns["graph"]
    assert isinstance(rebuilt, Graph)
    rebuilt_node = rebuilt.node_by_id("route")
    assert isinstance(rebuilt_node, Worker)
    assert rebuilt_node.control_flow is not None
    assert rebuilt_node.control_flow.condition == "score > 0.5"
    assert rebuilt_node.control_flow.gate_mode == "if_else"


def test_builder_can_emit_worker_native_llm_tool_code_aliases() -> None:
    wf = workflow("canonical_worker_aliases", canonical_workers=True)
    seed = wf.code("seed", code="result = 'draft'")
    review = wf.llm("review", prompt=f"Review {seed}")
    publish = wf.tool("publish", tool_id="file_write")
    review >> publish

    graph = wf.build()
    seed_node = graph.node_by_id("seed")
    review_node = graph.node_by_id("review")
    publish_node = graph.node_by_id("publish")

    assert isinstance(seed_node, Worker)
    assert isinstance(review_node, Worker)
    assert isinstance(publish_node, Worker)
    assert [port.name for port in seed_node.output_ports] == ["result"]
    assert [port.name for port in review_node.output_ports] == ["text"]
    assert [port.name for port in publish_node.output_ports] == ["result"]
    assert review_node.llm_hints is not None
    assert review_node.llm_hints.prompt_template == "Review {seed}"
    assert {(edge.source_node_id, edge.source_port, edge.target_node_id, edge.target_port) for edge in graph.edges} == {
        ("seed", "result", "review", "seed"),
        ("review", "text", "publish", "input"),
    }


def test_builder_can_emit_worker_native_extended_compute_aliases() -> None:
    wf = workflow("canonical_worker_extended_aliases", canonical_workers=True)
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
    review = wf.human("review", prompt="Approve the distilled lessons")
    checkpoint = wf.human_in_the_loop("checkpoint", prompt="Continue to voting?")
    choose = wf.vote(
        "choose",
        prompt="Pick the best answer",
        candidates=["claude-sonnet-4-6", "gpt-4o"],
        num_votes=2,
    )
    consensus = wf.ensemble(
        "consensus",
        prompt="Choose the strongest model output",
        models=["claude-sonnet-4-6", "gpt-4o"],
    )

    inputs >> retrieve >> reflect >> review >> checkpoint >> choose
    checkpoint >> consensus

    graph = wf.build()
    inputs_node = graph.node_by_id("inputs")
    retrieve_node = graph.node_by_id("retrieve")
    reflect_node = graph.node_by_id("reflect")
    review_node = graph.node_by_id("review")
    checkpoint_node = graph.node_by_id("checkpoint")
    choose_node = graph.node_by_id("choose")
    consensus_node = graph.node_by_id("consensus")

    assert isinstance(inputs_node, Worker)
    assert isinstance(retrieve_node, Worker)
    assert isinstance(reflect_node, Worker)
    assert isinstance(review_node, Worker)
    assert isinstance(checkpoint_node, Worker)
    assert isinstance(choose_node, Worker)
    assert isinstance(consensus_node, Worker)

    assert [port.name for port in inputs_node.output_ports] == ["input", "topic"]
    assert [port.name for port in retrieve_node.input_ports] == ["query"]
    assert [port.name for port in retrieve_node.output_ports] == ["chunks"]
    assert [port.name for port in reflect_node.output_ports] == ["principles"]
    assert [port.name for port in review_node.output_ports] == ["response"]
    assert [port.name for port in checkpoint_node.output_ports] == ["response"]
    assert [port.name for port in choose_node.output_ports] == ["winner"]
    assert [port.name for port in consensus_node.output_ports] == ["winner"]

    assert inputs_node.metadata["input_variables"][0]["name"] == "topic"
    assert retrieve_node.metadata["rag_collection"] == "papers"
    assert reflect_node.metadata["reflection_prompt"] == "Distill lessons."
    assert review_node.metadata["human_prompt"] == "Approve the distilled lessons"
    assert checkpoint_node.metadata["human_prompt"] == "Continue to voting?"
    assert choose_node.metadata["vote_num_votes"] == 2
    assert consensus_node.metadata["vote_num_votes"] == 2

    assert {
        (edge.source_node_id, edge.source_port, edge.target_node_id, edge.target_port)
        for edge in graph.edges
    } == {
        ("inputs", "input", "retrieve", "query"),
        ("retrieve", "chunks", "reflect", "input"),
        ("reflect", "principles", "review", "input"),
        ("review", "response", "checkpoint", "input"),
        ("checkpoint", "response", "choose", "input"),
        ("checkpoint", "response", "consensus", "input"),
    }


def test_builder_env_gate_can_enable_worker_native_aliases(monkeypatch) -> None:
    monkeypatch.setenv("DAN_WORKER_BUILDER", "enabled")
    wf = workflow("env_worker_aliases")
    node = wf.llm("draft", prompt="Write a short draft")

    graph = wf.build()
    built = graph.node_by_id(node.node_id)

    assert isinstance(built, Worker)
    assert [port.name for port in built.output_ports] == ["text"]
    assert built.llm_hints is not None
    assert built.llm_hints.prompt_template == "Write a short draft"


def test_builder_env_gate_can_enable_extended_compute_aliases(monkeypatch) -> None:
    monkeypatch.setenv("DAN_WORKER_BUILDER", "enabled")
    wf = workflow("env_worker_extended_aliases")
    inputs = wf.input_node(
        "inputs",
        variables=[{"name": "topic", "type": "string", "default": "agents"}],
    )
    retrieve = wf.rag("retrieve", collection="papers", top_k=3)
    inputs >> retrieve

    graph = wf.build()
    inputs_node = graph.node_by_id("inputs")
    retrieve_node = graph.node_by_id("retrieve")

    assert isinstance(inputs_node, Worker)
    assert isinstance(retrieve_node, Worker)
    assert [port.name for port in inputs_node.output_ports] == ["input", "topic"]
    assert [port.name for port in retrieve_node.input_ports] == ["query"]
    assert [port.name for port in retrieve_node.output_ports] == ["chunks"]
    assert {
        (edge.source_node_id, edge.source_port, edge.target_node_id, edge.target_port)
        for edge in graph.edges
    } == {("inputs", "input", "retrieve", "query")}


def test_input_node_infers_output_port_schemas_from_variables() -> None:
    wf = workflow("typed_input_node")
    wf.input_node(
        "workflow_inputs",
        variables=[
            {"name": "topic", "type": "string"},
            {"name": "budget", "type": "number"},
            {"name": "approved", "type": "boolean"},
        ],
    )

    graph = wf.build()
    node = graph.node_by_id("workflow_inputs")

    assert node is not None
    output_ports = {port.name: port for port in node.output_ports}
    assert output_ports["input"].json_schema == {"type": "object"}
    assert output_ports["topic"].json_schema == {"type": "string"}
    assert output_ports["budget"].json_schema == {"type": "number"}
    assert output_ports["approved"].json_schema == {"type": "boolean"}


def test_input_node_without_variables_keeps_untyped_input_port() -> None:
    wf = workflow("untyped_input_node")
    wf.input_node("workflow_inputs")

    graph = wf.build()
    node = graph.node_by_id("workflow_inputs")

    assert node is not None
    output_ports = {port.name: port for port in node.output_ports}
    assert output_ports["input"].json_schema == {}


def test_input_node_named_input_variable_keeps_legacy_untyped_port() -> None:
    wf = workflow("reserved_input_variable")
    wf.input_node(
        "workflow_inputs",
        variables=[{"name": "input", "type": "string", "default": ""}],
    )

    graph = wf.build()
    node = graph.node_by_id("workflow_inputs")

    assert node is not None
    output_ports = {port.name: port for port in node.output_ports}
    assert output_ports["input"].json_schema == {}


def test_worker_builder_autogenerates_lint_when_enabled() -> None:
    wf = workflow("worker_autogen", lint_autogen="enabled")
    source = wf.worker(
        "source",
        code="result = {'summary': 'hello'}",
        output_ports=[{"name": "result", "schema": {}}],
    )
    target = wf.worker(
        "review",
        role="reviewer",
        instruction="Review the summary for completeness",
        description="Reviews the summary for quality and completeness.",
        input_ports=[
            {
                "name": "input",
                "schema": {"type": "object", "required": ["summary"]},
                "description": "Summary draft for review",
                "required": False,
            }
        ],
        output_ports=[{"name": "result", "schema": {}}],
    )
    wf.edge(source["result"], target["input"])

    graph = wf.build()
    assert graph.edges[0].lint is not None
    assert graph.edges[0].lint.structural is not None
    assert graph.edges[0].lint.structural.required_keys == ["summary"]
    lint_cfg = graph.edges[0].metadata["lint"]
    assert lint_cfg["structural"]["required_keys"] == ["summary"]
    assert lint_cfg["semantic"]["reference_text"]
    assert "reviewer" in lint_cfg["intent"]["intent"]
    review_node = graph.node_by_id("review")
    assert isinstance(review_node, Worker)
    assert review_node.input_ports[0].json_schema["required"] == ["summary"]


def test_worker_builder_autogen_canary_limits_to_worker_edges() -> None:
    wf = workflow("worker_autogen_canary", lint_autogen="canary")
    source = wf.tool(
        "fetch",
        tool_id="file_read",
        output_ports=[{"name": "result", "schema": {}}],
    )
    target = wf.worker(
        "review",
        role="reviewer",
        instruction="Review the fetched content",
        input_ports=[
            {
                "name": "input",
                "schema": {"type": "object", "required": ["summary"]},
                "description": "Summary draft for review",
                "required": False,
            }
        ],
        output_ports=[{"name": "result", "schema": {}}],
    )
    wf.edge(source["result"], target["input"])

    graph = wf.build()
    assert graph.edges[0].lint is None
    assert "lint" not in graph.edges[0].metadata


def test_worker_builder_explicit_lint_overrides_autogen() -> None:
    wf = workflow("worker_autogen_override", lint_autogen="enabled")
    source = wf.worker(
        "source",
        code="result = {'summary': 'hello'}",
        output_ports=[{"name": "result", "schema": {}}],
    )
    target = wf.worker(
        "review",
        role="reviewer",
        instruction="Review the summary for completeness",
        input_ports=[
            {
                "name": "input",
                "schema": {"type": "object", "required": ["summary"]},
                "description": "Summary draft for review",
                "required": False,
            }
        ],
        output_ports=[{"name": "result", "schema": {}}],
    )
    wf.edge(
        source["result"],
        target["input"],
        lint={"structural": {"required_keys": ["title"]}, "severity": "warning"},
    )

    graph = wf.build()
    assert graph.edges[0].lint is not None
    assert graph.edges[0].lint.structural is not None
    assert graph.edges[0].lint.structural.required_keys == ["title"]
    assert graph.edges[0].lint.severity.value == "warning"
    lint_cfg = graph.edges[0].metadata["lint"]
    assert lint_cfg["structural"]["required_keys"] == ["title"]
    assert lint_cfg["severity"] == "warning"


def test_worker_builder_autogen_can_use_optional_intent_refiner() -> None:
    seen: dict[str, object] = {}

    def refiner(base_intent: str, context: dict[str, object]) -> str:
        seen["base_intent"] = base_intent
        seen["context"] = context
        return "Evidence completeness reviewer handoff"

    wf = workflow(
        "worker_autogen_refined",
        lint_autogen="enabled",
        lint_intent_refiner=refiner,
    )
    source = wf.worker(
        "source",
        code="result = {'summary': 'hello'}",
        output_ports=[{"name": "result", "schema": {}}],
    )
    target = wf.worker(
        "review",
        role="reviewer",
        instruction="Review the summary for completeness",
        description="Reviews the summary for quality and completeness.",
        input_ports=[
            {
                "name": "input",
                "schema": {"type": "object", "required": ["summary"]},
                "description": "Summary draft for review",
                "required": False,
            }
        ],
        output_ports=[{"name": "result", "schema": {}}],
    )
    wf.edge(source["result"], target["input"])

    graph = wf.build()
    lint_cfg = graph.edges[0].lint
    assert lint_cfg is not None
    assert lint_cfg.intent is not None
    assert lint_cfg.intent.intent == "Evidence completeness reviewer handoff"
    assert seen["base_intent"] == (
        "reviewer Review the summary for completeness Summary draft for review "
        "Reviews the summary for quality and completeness."
    )
    assert seen["context"] is not None
    assert seen["context"]["target_node_id"] == "review"
    assert seen["context"]["target_port"] == "input"


def test_reduce_alias_can_emit_worker_or_legacy_shapes() -> None:
    worker_wf = workflow("reduce_worker_alias", canonical_workers=True)
    worker_wf.reduce(
        "aggregate",
        reducer="sum(item['score'] for item in input)",
        input_ports=[{"name": "input", "required": False}],
        output_ports=[{"name": "result"}],
    )
    worker_graph = worker_wf.build()
    worker_node = worker_graph.node_by_id("aggregate")

    assert isinstance(worker_node, Worker)
    assert worker_node.role == "reduce"
    assert worker_node.metadata["reduce_expression"] == "sum(item['score'] for item in input)"

    legacy_wf = workflow("reduce_legacy_alias", canonical_workers=False)
    legacy_wf.reduce(
        "aggregate",
        reducer="sum(item['score'] for item in input)",
        input_ports=[{"name": "input", "required": False}],
        output_ports=[{"name": "result"}],
    )
    legacy_graph = legacy_wf.build()
    legacy_node = legacy_graph.node_by_id("aggregate")

    assert legacy_node is not None
    assert legacy_node.node_type == "reduce"
    assert getattr(legacy_node, "reducer", None) == "sum(item['score'] for item in input)"


def test_legacy_compute_aliases_delegate_through_worker_projection(monkeypatch) -> None:
    from dan.worker import presets as worker_presets

    seen: list[Worker] = []
    real_worker_to_legacy = worker_presets.worker_to_legacy

    def recording_worker_to_legacy(node: Worker):
        seen.append(node)
        return real_worker_to_legacy(node)

    monkeypatch.setattr(worker_presets, "worker_to_legacy", recording_worker_to_legacy)

    wf = workflow("legacy_alias_projection")
    wf.llm("draft", prompt="Draft {input}")
    wf.tool("fetch", tool_id="file_read", config={"path": "notes.txt"})
    wf.code("format", code="result = input")
    wf.rag("retrieve", collection="papers", top_k=7)
    wf.input_node(
        "workflow_inputs",
        variables=[{"name": "topic", "type": "string", "description": "Research topic"}],
    )
    wf.router(
        "route",
        model="test-model",
        route_descriptions={"research": "Do research", "draft": "Draft the answer"},
    )
    wf.validator(
        "validate",
        rules=[{"rule_type": "required_keys", "config": {"keys": ["summary"]}}],
        on_failure="halt",
        strict_mode=True,
    )
    wf.reflection(
        "reflect",
        reflection_prompt="Distill lessons.",
        reflection_model="test-model",
    )
    wf.human(
        "review",
        prompt="Review the draft",
        render_mode="approval",
        instructions="Approve or request changes",
        render_target="both",
    )
    wf.human_in_the_loop(
        "checkpoint",
        prompt="Continue?",
        timeout_seconds=30.0,
        default_action="resume",
    )
    wf.vote(
        "choose_best",
        prompt="Pick the best answer",
        candidates=["claude-sonnet-4-6", "gpt-4o"],
        num_votes=2,
        strategy="judge",
        parallelism=2,
    )

    graph = wf.build()

    assert [node.id for node in seen] == [
        "draft",
        "fetch",
        "format",
        "retrieve",
        "workflow_inputs",
        "route",
        "validate",
        "reflect",
        "review",
        "checkpoint",
        "choose_best",
    ]
    assert all(isinstance(node, Worker) for node in seen)
    assert graph.node_by_id("draft").node_type == "llm_operator"
    assert graph.node_by_id("fetch").node_type == "tool_operator"
    assert graph.node_by_id("format").node_type == "code_operator"
    assert graph.node_by_id("fetch").tool_config == {"path": "notes.txt"}
    assert graph.node_by_id("retrieve").node_type == "rag_operator"
    assert [port.name for port in graph.node_by_id("retrieve").input_ports] == ["query"]
    assert [port.name for port in graph.node_by_id("retrieve").output_ports] == ["chunks"]
    assert graph.node_by_id("retrieve").collection == "papers"
    assert graph.node_by_id("retrieve").top_k == 7
    assert graph.node_by_id("workflow_inputs").node_type == "input"
    assert [port.name for port in graph.node_by_id("workflow_inputs").output_ports] == ["input", "topic"]
    assert graph.node_by_id("route").node_type == "router"
    assert [port.name for port in graph.node_by_id("route").output_ports] == ["route", "result"]
    assert graph.node_by_id("route").route_descriptions == {
        "research": "Do research",
        "draft": "Draft the answer",
    }
    assert graph.node_by_id("validate").node_type == "validator"
    assert [port.name for port in graph.node_by_id("validate").input_ports] == ["data"]
    assert [port.name for port in graph.node_by_id("validate").output_ports] == ["valid", "invalid"]
    assert graph.node_by_id("validate").on_failure == "halt"
    assert graph.node_by_id("validate").strict_mode is True
    assert graph.node_by_id("reflect").node_type == "reflection"
    assert [port.name for port in graph.node_by_id("reflect").output_ports] == ["principles"]
    assert graph.node_by_id("reflect").reflection_prompt == "Distill lessons."
    assert graph.node_by_id("review").node_type == "human"
    assert [port.name for port in graph.node_by_id("review").input_ports] == ["input"]
    assert [port.name for port in graph.node_by_id("review").output_ports] == ["response"]
    assert graph.node_by_id("review").render_mode == "approval"
    assert graph.node_by_id("review").render_target == "both"
    assert graph.node_by_id("checkpoint").node_type == "human_in_the_loop"
    assert [port.name for port in graph.node_by_id("checkpoint").output_ports] == ["response"]
    assert graph.node_by_id("checkpoint").prompt == "Continue?"
    assert graph.node_by_id("checkpoint").timeout_seconds == 30.0
    assert graph.node_by_id("choose_best").node_type == "vote"
    assert [port.name for port in graph.node_by_id("choose_best").output_ports] == ["winner"]
    assert graph.node_by_id("choose_best").vote_strategy == "judge"
    assert graph.node_by_id("choose_best").parallelism == 2
