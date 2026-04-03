from __future__ import annotations

from dan.models.control_flow import (
    GateNode,
    HumanInTheLoopNode,
    HumanNode,
    InputNode,
    InputVariable,
    ReduceNode,
    RouterNode,
    ValidationRule,
    ValidatorNode,
    VoteNode,
)
from dan.models.edges import DataEdge
from dan.models.graph import Graph
from dan.models.node_taxonomy import RUNTIME_NODE_TYPE_MAP
from dan.models.nodes import CodeOperator, LLMOperator, RAGOperator, ReflectionNode, ToolOperator
from dan.models.ports import InputPort, OutputPort
from dan.worker import (
    BRIDGED_LEGACY_NODE_TYPES,
    EXPLICIT_NON_BRIDGED_LEGACY_NODE_TYPES,
    convert_graph,
    legacy_to_worker,
    role,
    supports_legacy_conversion,
    validate_conversion,
    worker_to_legacy,
)
from dan.worker.model import LLMHints, Worker


def test_role_factory_returns_standard_worker() -> None:
    reviewer = role("reviewer", model="test-model", persona="Review carefully")

    assert isinstance(reviewer, Worker)
    assert reviewer.id == "reviewer"
    assert reviewer.name == "reviewer"
    assert reviewer.role == "reviewer"
    assert reviewer.model == "test-model"
    assert reviewer.persona == "Review carefully"


def test_legacy_llm_round_trips_through_worker_bridge() -> None:
    legacy = LLMOperator(
        id="draft",
        name="Draft",
        model="test-model",
        prompt_template="Draft {topic}",
        system_prompt="You are concise.",
        temperature=0.2,
        output_ports=[OutputPort(name="text")],
    )

    worker = legacy_to_worker(legacy)

    assert isinstance(worker, Worker)
    assert worker.model == "test-model"
    assert worker.llm_hints is not None
    assert worker.llm_hints.prompt_template == "Draft {topic}"
    assert worker.llm_hints.system_prompt == "You are concise."

    rebuilt = worker_to_legacy(worker)

    assert isinstance(rebuilt, LLMOperator)
    assert rebuilt.model == "test-model"
    assert rebuilt.prompt_template == "Draft {topic}"
    assert rebuilt.system_prompt == "You are concise."


def test_convert_graph_workerizes_convertible_nodes_and_preserves_structure() -> None:
    graph = Graph(
        nodes=[
            LLMOperator(
                id="draft",
                name="Draft",
                model="test-model",
                prompt_template="Draft {topic}",
                output_ports=[OutputPort(name="text")],
            ),
            ToolOperator(
                id="fetch",
                name="Fetch",
                tool_id="file_read",
                output_ports=[OutputPort(name="result")],
            ),
            CodeOperator(
                id="format",
                name="Format",
                code="result = input",
                output_ports=[OutputPort(name="result")],
            ),
            InputNode(
                id="entry",
                name="Entry",
                variables=[],
                output_ports=[OutputPort(name="input")],
            ),
        ],
        edges=[
            DataEdge(
                id="e1",
                source_node_id="entry",
                source_port="input",
                target_node_id="draft",
                target_port="input",
            ),
            DataEdge(
                id="e2",
                source_node_id="draft",
                source_port="text",
                target_node_id="format",
                target_port="input",
            ),
        ],
        sub_graphs={
            "draft_body": Graph(
                nodes=[
                    ToolOperator(
                        id="lookup",
                        name="Lookup",
                        tool_id="web_search",
                        output_ports=[OutputPort(name="result")],
                    )
                ],
                edges=[],
                entry_points=["lookup"],
                exit_points=["lookup"],
            )
        },
        entry_points=["entry"],
        exit_points=["format"],
        worker_resources={"instruction_profiles": {"review": {"instruction": "Review"}}},
    )

    converted = convert_graph(graph)

    assert all(isinstance(node, Worker) for node in converted.nodes)
    assert isinstance(converted.sub_graphs["draft_body"].nodes[0], Worker)
    assert converted.worker_resources == graph.worker_resources
    assert validate_conversion(graph, converted) == []


def test_worker_with_llm_hints_without_model_projects_to_legacy_llm_operator() -> None:
    worker = Worker(
        id="draft",
        name="Draft",
        llm_hints=LLMHints(
            prompt_template="Draft {input}",
            system_prompt="Stay concise.",
        ),
    )

    rebuilt = worker_to_legacy(worker)

    assert isinstance(rebuilt, LLMOperator)
    assert rebuilt.model == ""
    assert rebuilt.prompt_template == "Draft {input}"
    assert rebuilt.system_prompt == "Stay concise."


def test_worker_control_flow_projects_to_legacy_gate_node() -> None:
    worker = Worker(
        id="route",
        name="Route",
        control_flow={
            "condition": "score > 0.5",
            "gate_mode": "if_else",
            "max_iterations": 3,
        },
    )

    rebuilt = worker_to_legacy(worker)

    assert isinstance(rebuilt, GateNode)
    assert rebuilt.condition == "score > 0.5"
    assert rebuilt.gate_mode == "if_else"
    assert rebuilt.max_iterations == 3


def test_worker_metadata_bridges_project_specialized_legacy_nodes() -> None:
    input_worker = Worker(
        id="entry",
        name="Entry",
        metadata={"input_variables": [{"name": "topic", "type": "string"}]},
        output_ports=[OutputPort(name="input"), OutputPort(name="topic")],
    )
    router_worker = Worker(
        id="route",
        name="Route",
        role="router",
        model="test-model",
        metadata={"route_descriptions": {"research": "Do research"}},
        output_ports=[OutputPort(name="route"), OutputPort(name="result")],
    )
    validator_worker = Worker(
        id="validate",
        name="Validate",
        role="validator",
        metadata={
            "validation_rules": [{"rule_type": "required_keys", "config": {"keys": ["summary"]}}],
            "validator_on_failure": "halt",
            "validator_strict_mode": True,
        },
        input_ports=[InputPort(name="data")],
        output_ports=[OutputPort(name="valid"), OutputPort(name="invalid")],
    )
    reflection_worker = Worker(
        id="reflect",
        name="Reflect",
        role="reflection",
        model="test-model",
        metadata={
            "reflection_prompt": "Distill lessons.",
            "reflection_source": "last_run",
            "reflection_output_format": "principles",
            "reflection_max_principles": 8,
            "reflection_min_confidence": 0.4,
            "reflection_dedup_strategy": "exact_key",
        },
        output_ports=[OutputPort(name="principles")],
    )
    rag_worker = Worker(
        id="retrieve",
        name="Retrieve",
        role="rag",
        metadata={
            "rag_collection": "papers",
            "rag_top_k": 7,
            "rag_query_template": "Find chunks for {query}",
            "rag_include_metadata": False,
            "rag_rerank": True,
        },
        input_ports=[InputPort(name="query", required=False)],
        output_ports=[OutputPort(name="chunks")],
    )
    human_worker = Worker(
        id="review",
        name="Review",
        role="human",
        metadata={
            "human_prompt": "Review the draft",
            "human_render_mode": "approval",
            "human_instructions": "Approve or request changes",
            "human_render_target": "both",
        },
        input_ports=[InputPort(name="input", required=False)],
        output_ports=[OutputPort(name="response")],
    )
    hitl_worker = Worker(
        id="checkpoint",
        name="Checkpoint",
        role="human_in_the_loop",
        metadata={
            "human_prompt": "Continue?",
            "human_timeout_seconds": 30.0,
            "human_default_action": "resume",
        },
        input_ports=[InputPort(name="input", required=False)],
        output_ports=[OutputPort(name="response")],
    )
    vote_worker = Worker(
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
        input_ports=[InputPort(name="input", required=False)],
        output_ports=[OutputPort(name="winner")],
    )

    rebuilt_input = worker_to_legacy(input_worker)
    rebuilt_router = worker_to_legacy(router_worker)
    rebuilt_validator = worker_to_legacy(validator_worker)
    rebuilt_reflection = worker_to_legacy(reflection_worker)
    rebuilt_rag = worker_to_legacy(rag_worker)
    rebuilt_human = worker_to_legacy(human_worker)
    rebuilt_hitl = worker_to_legacy(hitl_worker)
    rebuilt_vote = worker_to_legacy(vote_worker)

    assert isinstance(rebuilt_input, InputNode)
    assert [variable.name for variable in rebuilt_input.variables] == ["topic"]

    assert isinstance(rebuilt_router, RouterNode)
    assert rebuilt_router.route_descriptions == {"research": "Do research"}

    assert isinstance(rebuilt_validator, ValidatorNode)
    assert rebuilt_validator.on_failure == "halt"
    assert rebuilt_validator.strict_mode is True
    assert rebuilt_validator.validation_rules[0].rule_type == "required_keys"

    assert isinstance(rebuilt_reflection, ReflectionNode)
    assert rebuilt_reflection.reflection_prompt == "Distill lessons."
    assert rebuilt_reflection.max_principles == 8
    assert rebuilt_reflection.dedup_strategy == "exact_key"

    assert isinstance(rebuilt_rag, RAGOperator)
    assert rebuilt_rag.collection == "papers"
    assert rebuilt_rag.top_k == 7
    assert rebuilt_rag.query_template == "Find chunks for {query}"
    assert rebuilt_rag.include_metadata is False
    assert rebuilt_rag.rerank is True

    assert isinstance(rebuilt_human, HumanNode)
    assert rebuilt_human.prompt == "Review the draft"
    assert rebuilt_human.render_mode == "approval"
    assert rebuilt_human.render_target == "both"

    assert isinstance(rebuilt_hitl, HumanInTheLoopNode)
    assert rebuilt_hitl.prompt == "Continue?"
    assert rebuilt_hitl.timeout_seconds == 30.0
    assert rebuilt_hitl.default_action == "resume"

    assert isinstance(rebuilt_vote, VoteNode)
    assert rebuilt_vote.candidates == ["claude-sonnet-4-6", "gpt-4o"]
    assert rebuilt_vote.num_votes == 2
    assert rebuilt_vote.prompt_template == "Pick the best answer"
    assert rebuilt_vote.vote_strategy == "judge"
    assert rebuilt_vote.parallelism == 2


def test_legacy_specialized_compute_nodes_capture_worker_metadata_on_bridge() -> None:
    rag = RAGOperator(
        id="retrieve",
        name="Retrieve",
        collection="papers",
        top_k=7,
        query_template="Find chunks for {query}",
        include_metadata=False,
        rerank=True,
        input_ports=[InputPort(name="query", required=False)],
        output_ports=[OutputPort(name="chunks")],
    )
    human = HumanNode(
        id="review",
        name="Review",
        prompt="Review the draft",
        render_mode="approval",
        instructions="Approve or request changes",
        render_target="both",
        input_ports=[InputPort(name="input", required=False)],
        output_ports=[OutputPort(name="response")],
    )
    vote = VoteNode(
        id="choose_best",
        name="Choose Best",
        candidates=["claude-sonnet-4-6", "gpt-4o"],
        num_votes=2,
        prompt_template="Pick the best answer",
        vote_strategy="judge",
        parallelism=2,
        input_ports=[InputPort(name="input", required=False)],
        output_ports=[OutputPort(name="winner")],
    )

    rag_worker = legacy_to_worker(rag)
    human_worker = legacy_to_worker(human)
    vote_worker = legacy_to_worker(vote)

    assert isinstance(rag_worker, Worker)
    assert rag_worker.role == "rag"
    assert rag_worker.metadata["rag_collection"] == "papers"
    assert rag_worker.metadata["rag_query_template"] == "Find chunks for {query}"

    assert isinstance(human_worker, Worker)
    assert human_worker.role == "human"
    assert human_worker.metadata["human_prompt"] == "Review the draft"
    assert human_worker.metadata["human_render_target"] == "both"

    assert isinstance(vote_worker, Worker)
    assert vote_worker.role == "vote"
    assert vote_worker.metadata["vote_candidates"] == ["claude-sonnet-4-6", "gpt-4o"]
    assert vote_worker.metadata["vote_strategy"] == "judge"


def test_validate_conversion_reports_structural_drift() -> None:
    original = Graph(
        nodes=[
            ToolOperator(
                id="fetch",
                name="Fetch",
                tool_id="file_read",
                output_ports=[OutputPort(name="result")],
            ),
        ],
        edges=[],
        entry_points=["fetch"],
        exit_points=["fetch"],
    )
    converted = Graph(
        nodes=[
            Worker(
                id="fetch_renamed",
                name="Fetch",
                tool_ids=["file_read"],
                output_ports=[OutputPort(name="result")],
            ),
        ],
        edges=[],
        entry_points=["fetch"],
        exit_points=["fetch"],
    )

    errors = validate_conversion(original, converted)

    assert any("node ids changed" in error for error in errors)


def test_preset_bridge_explicitly_partitions_runtime_taxonomy() -> None:
    runtime_legacy_types = set(RUNTIME_NODE_TYPE_MAP) - {"worker"}

    assert BRIDGED_LEGACY_NODE_TYPES.isdisjoint(EXPLICIT_NON_BRIDGED_LEGACY_NODE_TYPES)
    assert BRIDGED_LEGACY_NODE_TYPES | EXPLICIT_NON_BRIDGED_LEGACY_NODE_TYPES == runtime_legacy_types


def test_supports_legacy_conversion_matches_bridge_constant() -> None:
    runtime_legacy_types = set(RUNTIME_NODE_TYPE_MAP) - {"worker"}

    assert {
        node_type for node_type in runtime_legacy_types if supports_legacy_conversion(node_type)
    } == BRIDGED_LEGACY_NODE_TYPES


def test_legacy_to_worker_supports_each_bridged_runtime_type() -> None:
    samples = {
        "llm_operator": LLMOperator(
            id="draft",
            name="Draft",
            model="test-model",
            prompt_template="Draft {input}",
            input_ports=[InputPort(name="input", required=False)],
            output_ports=[OutputPort(name="text")],
        ),
        "tool_operator": ToolOperator(
            id="fetch",
            name="Fetch",
            tool_id="file_read",
            input_ports=[InputPort(name="path", required=False)],
            output_ports=[OutputPort(name="result")],
        ),
        "code_operator": CodeOperator(
            id="normalize",
            name="Normalize",
            code="result = input",
            input_ports=[InputPort(name="input", required=False)],
            output_ports=[OutputPort(name="result")],
        ),
        "input": InputNode(
            id="entry",
            name="Entry",
            variables=[InputVariable(name="topic", type="string")],
            output_ports=[OutputPort(name="input"), OutputPort(name="topic")],
        ),
        "router": RouterNode(
            id="route",
            name="Route",
            model="test-model",
            route_descriptions={"research": "Do research", "draft": "Draft an answer"},
            output_ports=[OutputPort(name="route"), OutputPort(name="result")],
        ),
        "reduce": ReduceNode(
            id="merge",
            name="Merge",
            reducer="sum",
            input_ports=[InputPort(name="left", required=False), InputPort(name="right", required=False)],
            output_ports=[OutputPort(name="result")],
        ),
        "validator": ValidatorNode(
            id="validate",
            name="Validate",
            validation_rules=[
                ValidationRule(
                    rule_type="required_keys",
                    config={"keys": ["summary"]},
                )
            ],
        ),
        "reflection": ReflectionNode(
            id="reflect",
            name="Reflect",
            reflection_prompt="Distill lessons.",
            reflection_model="test-model",
        ),
        "rag_operator": RAGOperator(
            id="retrieve",
            name="Retrieve",
            collection="papers",
            top_k=3,
            query_template="Find chunks for {query}",
            input_ports=[InputPort(name="query", required=False)],
            output_ports=[OutputPort(name="chunks")],
        ),
        "human": HumanNode(
            id="review",
            name="Review",
            prompt="Review the draft",
            render_mode="approval",
            instructions="Approve or request changes",
            input_ports=[InputPort(name="input", required=False)],
            output_ports=[OutputPort(name="response")],
        ),
        "human_in_the_loop": HumanInTheLoopNode(
            id="checkpoint",
            name="Checkpoint",
            prompt="Continue?",
            timeout_seconds=30.0,
            default_action="resume",
            input_ports=[InputPort(name="input", required=False)],
            output_ports=[OutputPort(name="response")],
        ),
        "vote": VoteNode(
            id="choose_best",
            name="Choose Best",
            candidates=["claude-sonnet-4-6", "gpt-4o"],
            num_votes=2,
            prompt_template="Pick the best answer",
            vote_strategy="judge",
            parallelism=2,
            input_ports=[InputPort(name="input", required=False)],
            output_ports=[OutputPort(name="winner")],
        ),
    }

    assert set(samples) == BRIDGED_LEGACY_NODE_TYPES

    for node_type, node in samples.items():
        converted = legacy_to_worker(node)
        assert supports_legacy_conversion(node_type) is True
        assert isinstance(converted, Worker)
        assert converted.id == node.id
        assert converted.node_type == "worker"
