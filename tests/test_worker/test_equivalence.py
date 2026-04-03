from __future__ import annotations

from collections.abc import Sequence

import pytest

from dan.builder import workflow
from dan.builder.refs import NodeRef
from dan.engine import Engine, EngineConfig
from dan.engine.checkpoint import NullCheckpointStore
from dan.engine.events import EngineEvent, EventType
from dan.engine.executor import ExecutorRegistry, NodeResult
from dan.engine.state import NodeStatus
from dan.executors.code import CodeExecutor
from dan.executors import control_flow as control_flow_executors
from dan.executors.tool import ToolExecutor, ToolRegistry
from dan.models.context import MergeStrategy
from dan.models.edges import DataEdge
from dan.models.graph import Graph
from dan.models.control_flow import GateNode, InputNode, InputVariable, VoteNode
from dan.models.nodes import CodeOperator, LLMOperator, NodeBase, ToolOperator
from dan.models.ports import InputPort, OutputPort
from dan.providers import CompletionResult
from dan.worker.executor import WorkerExecutor
from dan.worker.model import Worker
from dan.worker.presets import convert_graph, legacy_to_worker, validate_conversion


class _StubLLMExecutor:
    async def execute(self, node, inputs, context) -> NodeResult:
        value = inputs.get("input")
        if value is None:
            value = inputs.get("result")
        return NodeResult(
            outputs={"text": f"{node.id}:{value}"},
            status=NodeStatus.COMPLETED,
        )


class _SequenceProvider:
    def __init__(self, responses: Sequence[CompletionResult]) -> None:
        self._responses = list(responses)

    async def complete(self, *args, **kwargs) -> CompletionResult:
        assert self._responses, "provider responses exhausted"
        return self._responses.pop(0)


def _engine() -> Engine:
    tool_registry = ToolRegistry()

    async def prefix_tool(input: str = "", prefix: str = "") -> dict[str, str]:
        return {"result": f"{prefix}{input}"}

    tool_registry.register("prefix_tool", prefix_tool)

    llm_executor = _StubLLMExecutor()
    registry = ExecutorRegistry()
    registry.register("code_operator", CodeExecutor())
    registry.register("tool_operator", ToolExecutor(tool_registry))
    registry.register("llm_operator", llm_executor)
    registry.register(
        "worker",
        WorkerExecutor(
            llm_executor=llm_executor,
            tool_executor=ToolExecutor(tool_registry),
            code_executor=CodeExecutor(),
        ),
    )
    return Engine(
        config=EngineConfig(
            llm_api_key="test",
            llm_base_url="http://localhost:1",
            llm_default_model="stub-model",
            checkpoint_enabled=False,
        ),
        checkpoint_store=NullCheckpointStore(),
        executor_registry=registry,
    )


def _graph_for(node: NodeBase) -> Graph:
    return Graph(
        nodes=[node],
        edges=[],
        entry_points=[node.id],
        exit_points=[node.id],
    )


def _normalize_node_events(events: Sequence[EngineEvent]) -> list[tuple[str, str]]:
    interesting = {
        EventType.NODE_STARTED,
        EventType.NODE_OUTPUT,
        EventType.NODE_COMPLETED,
    }
    return [
        (event.event_type.value, event.node_id or "")
        for event in events
        if event.event_type in interesting
    ]


def _default_engine(callback=None) -> Engine:
    return Engine(
        config=EngineConfig(
            llm_api_key="test",
            llm_base_url="http://localhost:1",
            llm_default_model="stub-model",
            checkpoint_enabled=False,
        ),
        checkpoint_store=NullCheckpointStore(),
        event_callback=callback,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("legacy_node", "inputs"),
    [
        (
            InputNode(
                id="workflow_inputs",
                name="Workflow Inputs",
                variables=[
                    InputVariable(
                        name="topic",
                        type="string",
                        default="fallback",
                    )
                ],
                output_ports=[
                    OutputPort(name="input"),
                    OutputPort(name="topic"),
                ],
            ),
            {"topic": "hello"},
        ),
        (
            CodeOperator(
                id="format",
                name="Format",
                code="result = input.upper()",
                input_ports=[InputPort(name="input", required=False)],
                output_ports=[OutputPort(name="result")],
            ),
            {"input": "hello"},
        ),
        (
            ToolOperator(
                id="fetch",
                name="Fetch",
                tool_id="prefix_tool",
                tool_config={"prefix": "tool:"},
                input_ports=[InputPort(name="input", required=False)],
                output_ports=[OutputPort(name="result")],
            ),
            {"input": "hello"},
        ),
        (
            LLMOperator(
                id="draft",
                name="Draft",
                model="stub-model",
                prompt_template="Draft {input}",
                input_ports=[InputPort(name="input", required=False)],
                output_ports=[OutputPort(name="text")],
            ),
            {"input": "hello"},
        ),
    ],
)
async def test_workerized_tier1_nodes_match_legacy_engine_outputs_and_events(
    legacy_node: NodeBase,
    inputs: dict[str, object],
) -> None:
    worker_node = legacy_to_worker(legacy_node)

    assert isinstance(worker_node, Worker)

    legacy_events: list[EngineEvent] = []
    worker_events: list[EngineEvent] = []

    async def capture_legacy(event: EngineEvent) -> None:
        legacy_events.append(event)

    async def capture_worker(event: EngineEvent) -> None:
        worker_events.append(event)

    legacy_engine = _engine()
    legacy_engine.event_callback = capture_legacy
    worker_engine = _engine()
    worker_engine.event_callback = capture_worker

    legacy_result = await legacy_engine.run(_graph_for(legacy_node), inputs=inputs)
    worker_result = await worker_engine.run(_graph_for(worker_node), inputs=inputs)

    assert legacy_result.success is True
    assert worker_result.success is True
    assert worker_result.outputs == legacy_result.outputs
    assert worker_result.node_statuses == legacy_result.node_statuses
    assert _normalize_node_events(worker_events) == _normalize_node_events(legacy_events)


@pytest.mark.asyncio
async def test_worker_control_flow_matches_legacy_gate_engine_outputs_and_events() -> None:
    legacy_node = GateNode(
        id="route",
        name="Route",
        condition="score > 0.5",
        gate_mode="if_else",
        input_ports=[InputPort(name="score", required=False)],
        output_ports=[OutputPort(name="true"), OutputPort(name="false")],
    )
    worker_node = Worker(
        id="route",
        name="Route",
        control_flow={"condition": "score > 0.5", "gate_mode": "if_else"},
        input_ports=[InputPort(name="score", required=False)],
        output_ports=[OutputPort(name="true"), OutputPort(name="false")],
    )

    legacy_events: list[EngineEvent] = []
    worker_events: list[EngineEvent] = []

    async def capture_legacy(event: EngineEvent) -> None:
        legacy_events.append(event)

    async def capture_worker(event: EngineEvent) -> None:
        worker_events.append(event)

    legacy_result = await _default_engine(capture_legacy).run(
        _graph_for(legacy_node),
        inputs={"score": 0.9},
    )
    worker_result = await _default_engine(capture_worker).run(
        _graph_for(worker_node),
        inputs={"score": 0.9},
    )

    assert legacy_result.success is True
    assert worker_result.success is True
    assert worker_result.outputs == legacy_result.outputs
    assert worker_result.node_statuses == legacy_result.node_statuses
    assert _normalize_node_events(worker_events) == _normalize_node_events(legacy_events)


@pytest.mark.asyncio
async def test_worker_vote_matches_legacy_vote_engine_outputs_and_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_node = VoteNode(
        id="choose",
        name="Choose",
        candidates=["stub-model"],
        num_votes=3,
        prompt_template="Pick the best answer for {input}",
        vote_strategy="majority",
        input_ports=[InputPort(name="input", required=False)],
        output_ports=[OutputPort(name="winner")],
    )
    worker_node = Worker(
        id="choose",
        name="Choose",
        role="vote",
        metadata={
            "vote_candidates": ["stub-model"],
            "vote_num_votes": 3,
            "vote_prompt_template": "Pick the best answer for {input}",
            "vote_strategy": "majority",
            "vote_parallelism": 3,
        },
        input_ports=[InputPort(name="input", required=False)],
        output_ports=[OutputPort(name="winner")],
    )

    provider = _SequenceProvider(
        [
            CompletionResult(text="answer-a"),
            CompletionResult(text="answer-a"),
            CompletionResult(text="answer-b"),
            CompletionResult(text="answer-a"),
            CompletionResult(text="answer-a"),
            CompletionResult(text="answer-b"),
        ]
    )
    monkeypatch.setattr(
        control_flow_executors,
        "resolve_completion_provider",
        lambda *args, **kwargs: provider,
    )

    legacy_events: list[EngineEvent] = []
    worker_events: list[EngineEvent] = []

    async def capture_legacy(event: EngineEvent) -> None:
        legacy_events.append(event)

    async def capture_worker(event: EngineEvent) -> None:
        worker_events.append(event)

    legacy_result = await _default_engine(capture_legacy).run(
        _graph_for(legacy_node),
        inputs={"input": "topic"},
    )
    worker_result = await _default_engine(capture_worker).run(
        _graph_for(worker_node),
        inputs={"input": "topic"},
    )

    assert legacy_result.success is True
    assert worker_result.success is True
    assert worker_result.outputs == legacy_result.outputs
    assert worker_result.node_statuses == legacy_result.node_statuses
    assert _normalize_node_events(worker_events) == _normalize_node_events(legacy_events)


@pytest.mark.asyncio
async def test_convert_graph_preserves_engine_behavior_for_tier1_compute_chain() -> None:
    legacy_graph = Graph(
        nodes=[
            CodeOperator(
                id="format",
                name="Format",
                code="result = input.upper()",
                input_ports=[InputPort(name="input", required=False)],
                output_ports=[OutputPort(name="result")],
            ),
            ToolOperator(
                id="fetch",
                name="Fetch",
                tool_id="prefix_tool",
                tool_config={"prefix": "tool:"},
                input_ports=[InputPort(name="input", required=False)],
                output_ports=[OutputPort(name="result")],
            ),
            LLMOperator(
                id="draft",
                name="Draft",
                model="stub-model",
                prompt_template="Draft {input}",
                input_ports=[InputPort(name="input", required=False)],
                output_ports=[OutputPort(name="text")],
            ),
        ],
        edges=[
            DataEdge(
                id="e1",
                source_node_id="format",
                source_port="result",
                target_node_id="fetch",
                target_port="input",
            ),
            DataEdge(
                id="e2",
                source_node_id="fetch",
                source_port="result",
                target_node_id="draft",
                target_port="input",
            ),
        ],
        entry_points=["format"],
        exit_points=["draft"],
    )
    worker_graph = convert_graph(legacy_graph)

    assert validate_conversion(legacy_graph, worker_graph) == []

    legacy_events: list[EngineEvent] = []
    worker_events: list[EngineEvent] = []

    async def capture_legacy(event: EngineEvent) -> None:
        legacy_events.append(event)

    async def capture_worker(event: EngineEvent) -> None:
        worker_events.append(event)

    legacy_engine = _engine()
    legacy_engine.event_callback = capture_legacy
    worker_engine = _engine()
    worker_engine.event_callback = capture_worker

    legacy_result = await legacy_engine.run(legacy_graph, inputs={"input": "hello"})
    worker_result = await worker_engine.run(worker_graph, inputs={"input": "hello"})

    assert legacy_result.success is True
    assert worker_result.success is True
    assert worker_result.outputs == legacy_result.outputs
    assert worker_result.node_statuses == legacy_result.node_statuses
    assert _normalize_node_events(worker_events) == _normalize_node_events(legacy_events)


@pytest.mark.asyncio
async def test_convert_graph_preserves_behavior_when_control_primitives_stay_specialized() -> None:
    wf = workflow("foreach_worker_parity")
    with wf.for_each(
        "fan",
        parallelism=1,
        merge_strategy=MergeStrategy.APPEND,
        input_ports=[{"name": "items"}],
        output_ports=[{"name": "results"}],
    ) as body:
        body.code(
            "double",
            code="result = {'value': item * 2}",
            input_ports=[{"name": "item"}, {"name": "index"}],
            output_ports=[{"name": "value"}],
        )

    legacy_graph = wf.build()
    worker_graph = convert_graph(legacy_graph)

    assert validate_conversion(legacy_graph, worker_graph) == []
    assert worker_graph.node_by_id("fan").node_type == "for_each"
    assert worker_graph.sub_graphs["fan_body"].node_by_id("double").node_type == "worker"

    legacy_events: list[EngineEvent] = []
    worker_events: list[EngineEvent] = []

    async def capture_legacy(event: EngineEvent) -> None:
        legacy_events.append(event)

    async def capture_worker(event: EngineEvent) -> None:
        worker_events.append(event)

    legacy_result = await _default_engine(capture_legacy).run(legacy_graph, inputs={"items": [1, 2, 3]})
    worker_result = await _default_engine(capture_worker).run(worker_graph, inputs={"items": [1, 2, 3]})

    assert legacy_result.success is True
    assert worker_result.success is True
    assert worker_result.outputs == legacy_result.outputs
    assert worker_result.node_statuses == legacy_result.node_statuses
    assert _normalize_node_events(worker_events) == _normalize_node_events(legacy_events)


@pytest.mark.asyncio
async def test_convert_graph_preserves_behavior_for_specialized_while_loop_with_workerized_body() -> None:
    wf = workflow("while_worker_parity")
    with wf.while_loop(
        "loop",
        condition="counter < 5",
        max_iterations=20,
        input_ports=[{"name": "counter"}],
        output_ports=[{"name": "counter"}],
    ) as body:
        body.code(
            "inc",
            code="result = {'counter': counter + 1}",
            input_ports=[{"name": "counter"}],
            output_ports=[{"name": "counter"}],
        )

    legacy_graph = wf.build()
    worker_graph = convert_graph(legacy_graph)

    assert validate_conversion(legacy_graph, worker_graph) == []
    assert worker_graph.node_by_id("loop").node_type == "while_loop"
    assert worker_graph.sub_graphs["loop_body"].node_by_id("inc").node_type == "worker"

    legacy_events: list[EngineEvent] = []
    worker_events: list[EngineEvent] = []

    async def capture_legacy(event: EngineEvent) -> None:
        legacy_events.append(event)

    async def capture_worker(event: EngineEvent) -> None:
        worker_events.append(event)

    legacy_result = await _default_engine(capture_legacy).run(legacy_graph, inputs={"counter": 0})
    worker_result = await _default_engine(capture_worker).run(worker_graph, inputs={"counter": 0})

    assert legacy_result.success is True
    assert worker_result.success is True
    assert worker_result.outputs == legacy_result.outputs
    assert worker_result.node_statuses == legacy_result.node_statuses
    assert _normalize_node_events(worker_events) == _normalize_node_events(legacy_events)


@pytest.mark.asyncio
async def test_convert_graph_preserves_behavior_for_specialized_goal_loop_with_workerized_body() -> None:
    wf = workflow("goal_loop_worker_parity")
    with wf.goal_loop(
        "improve",
        goal_text="Reach a strong draft score",
        metric_name="score",
        target_value=0.85,
        comparison=">=",
        max_iterations=5,
        input_ports=[{"name": "draft"}, {"name": "score"}],
        output_ports=[
            {"name": "draft"},
            {"name": "score"},
            {"name": "goal_met"},
            {"name": "iterations"},
            {"name": "best_score"},
        ],
    ) as body:
        body.code(
            "advance",
            code=(
                "next_score = round(min(score + 0.3, 1.0), 2)\n"
                "result = {\n"
                "    'draft': f'{draft}|iter{next_score}',\n"
                "    'score': next_score,\n"
                "}"
            ),
            input_ports=[{"name": "draft"}, {"name": "score"}],
            output_ports=[{"name": "draft"}, {"name": "score"}],
        )

    legacy_graph = wf.build()
    worker_graph = convert_graph(legacy_graph)

    assert validate_conversion(legacy_graph, worker_graph) == []
    assert worker_graph.node_by_id("improve").node_type == "goal_loop"
    assert worker_graph.sub_graphs["improve_body"].node_by_id("advance").node_type == "worker"

    legacy_events: list[EngineEvent] = []
    worker_events: list[EngineEvent] = []

    async def capture_legacy(event: EngineEvent) -> None:
        legacy_events.append(event)

    async def capture_worker(event: EngineEvent) -> None:
        worker_events.append(event)

    legacy_result = await _default_engine(capture_legacy).run(
        legacy_graph,
        inputs={"draft": "seed", "score": 0.2},
    )
    worker_result = await _default_engine(capture_worker).run(
        worker_graph,
        inputs={"draft": "seed", "score": 0.2},
    )

    assert legacy_result.success is True
    assert worker_result.success is True
    assert worker_result.outputs == legacy_result.outputs
    assert worker_result.node_statuses == legacy_result.node_statuses
    assert _normalize_node_events(worker_events) == _normalize_node_events(legacy_events)


@pytest.mark.asyncio
async def test_convert_graph_preserves_behavior_for_parallel_subagents_with_workerized_branches() -> None:
    wf = workflow("parallel_worker_parity")
    with wf.parallel_subagents(
        "teams",
        parallelism=2,
        merge_strategy=MergeStrategy.APPEND,
        input_mappings={"input": "input"},
        input_ports=[{"name": "input"}],
        output_ports=[{"name": "results"}],
    ) as parallel:
        with parallel.branch("alpha") as sub:
            alpha = sub.code(
                "alpha_task",
                code="result = {'branch': 'alpha', 'value': f'{input}-alpha'}",
                input_ports=[{"name": "input", "required": False}],
                output_ports=[{"name": "branch"}, {"name": "value"}],
            )
            sub.edge(sub.entry_input, alpha["input"])
        with parallel.branch("beta") as sub:
            beta = sub.code(
                "beta_task",
                code="result = {'branch': 'beta', 'value': f'{input}-beta'}",
                input_ports=[{"name": "input", "required": False}],
                output_ports=[{"name": "branch"}, {"name": "value"}],
            )
            sub.edge(sub.entry_input, beta["input"])

    legacy_graph = wf.build()
    worker_graph = convert_graph(legacy_graph)

    assert validate_conversion(legacy_graph, worker_graph) == []
    assert worker_graph.node_by_id("teams").node_type == "parallel_subagents"
    assert worker_graph.sub_graphs["teams_alpha"].node_by_id("alpha_task").node_type == "worker"
    assert worker_graph.sub_graphs["teams_beta"].node_by_id("beta_task").node_type == "worker"

    legacy_events: list[EngineEvent] = []
    worker_events: list[EngineEvent] = []

    async def capture_legacy(event: EngineEvent) -> None:
        legacy_events.append(event)

    async def capture_worker(event: EngineEvent) -> None:
        worker_events.append(event)

    legacy_result = await _default_engine(capture_legacy).run(legacy_graph, inputs={"input": "seed"})
    worker_result = await _default_engine(capture_worker).run(worker_graph, inputs={"input": "seed"})

    assert legacy_result.success is True
    assert worker_result.success is True
    assert worker_result.outputs == legacy_result.outputs
    assert worker_result.node_statuses == legacy_result.node_statuses
    assert _normalize_node_events(worker_events) == _normalize_node_events(legacy_events)


@pytest.mark.asyncio
async def test_convert_graph_preserves_behavior_for_agent_team_with_workerized_members() -> None:
    wf = workflow("agent_team_worker_parity")
    with wf.team(
        "review_team",
        turn_strategy="sequential",
        completion_condition="all_responded",
        input_mappings={"input": "input"},
        input_ports=[{"name": "input"}],
        output_ports=[{"name": "result"}],
    ) as team:
        with team.agent("researcher") as sub:
            research = sub.code(
                "research",
                code="result = f'research:{input}'",
                input_ports=[{"name": "input", "required": False}],
                output_ports=[{"name": "result"}],
            )
            sub.edge(sub.entry_input, research["input"])
        with team.agent("writer") as sub:
            write = sub.code(
                "write",
                code="result = f'write:{input}'",
                input_ports=[{"name": "input", "required": False}],
                output_ports=[{"name": "result"}],
            )
            sub.edge(sub.entry_input, write["input"])

    legacy_graph = wf.build()
    worker_graph = convert_graph(legacy_graph)

    assert validate_conversion(legacy_graph, worker_graph) == []
    assert worker_graph.node_by_id("review_team").node_type == "agent_team"
    assert worker_graph.sub_graphs["review_team_researcher"].node_by_id("research").node_type == "worker"
    assert worker_graph.sub_graphs["review_team_writer"].node_by_id("write").node_type == "worker"

    legacy_events: list[EngineEvent] = []
    worker_events: list[EngineEvent] = []

    async def capture_legacy(event: EngineEvent) -> None:
        legacy_events.append(event)

    async def capture_worker(event: EngineEvent) -> None:
        worker_events.append(event)

    legacy_result = await _default_engine(capture_legacy).run(legacy_graph, inputs={"input": "topic"})
    worker_result = await _default_engine(capture_worker).run(worker_graph, inputs={"input": "topic"})

    assert legacy_result.success is True
    assert worker_result.success is True
    assert worker_result.outputs == legacy_result.outputs
    assert worker_result.node_statuses == legacy_result.node_statuses
    assert _normalize_node_events(worker_events) == _normalize_node_events(legacy_events)


@pytest.mark.asyncio
async def test_convert_graph_preserves_behavior_for_static_orchestrator_with_workerized_teams() -> None:
    wf = workflow("orchestrator_worker_parity")
    with wf.orchestrator(
        "coord",
        completion_condition="all_done",
        input_mappings={"input": "input"},
        input_ports=[{"name": "input"}],
        output_ports=[{"name": "results"}],
    ) as orch:
        with orch.team("research") as sub:
            research = sub.code(
                "research_task",
                code="result = {'team': 'research', 'value': f'{input}-research'}",
                input_ports=[{"name": "input", "required": False}],
                output_ports=[{"name": "team"}, {"name": "value"}],
            )
            sub.edge(sub.entry_input, research["input"])
        with orch.team("draft") as sub:
            draft = sub.code(
                "draft_task",
                code="result = {'team': 'draft', 'value': f'{input}-draft'}",
                input_ports=[{"name": "input", "required": False}],
                output_ports=[{"name": "team"}, {"name": "value"}],
            )
            sub.edge(sub.entry_input, draft["input"])

    legacy_graph = wf.build()
    worker_graph = convert_graph(legacy_graph)

    assert validate_conversion(legacy_graph, worker_graph) == []
    assert worker_graph.node_by_id("coord").node_type == "orchestrator"
    assert worker_graph.sub_graphs["coord_research"].node_by_id("research_task").node_type == "worker"
    assert worker_graph.sub_graphs["coord_draft"].node_by_id("draft_task").node_type == "worker"

    legacy_events: list[EngineEvent] = []
    worker_events: list[EngineEvent] = []

    async def capture_legacy(event: EngineEvent) -> None:
        legacy_events.append(event)

    async def capture_worker(event: EngineEvent) -> None:
        worker_events.append(event)

    legacy_result = await _default_engine(capture_legacy).run(legacy_graph, inputs={"input": "topic"})
    worker_result = await _default_engine(capture_worker).run(worker_graph, inputs={"input": "topic"})

    assert legacy_result.success is True
    assert worker_result.success is True
    assert worker_result.outputs == legacy_result.outputs
    assert worker_result.node_statuses == legacy_result.node_statuses
    assert _normalize_node_events(worker_events) == _normalize_node_events(legacy_events)


@pytest.mark.asyncio
async def test_convert_graph_preserves_behavior_for_llm_driven_orchestrator_with_workerized_teams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wf = workflow("llm_orchestrator_worker_parity")
    with wf.orchestrator(
        "coord",
        orchestrator_prompt="Dispatch the right teams and then halt.",
        orchestrator_model="stub-orchestrator",
        completion_condition="orchestrator_halt",
        input_mappings={"input": "input"},
        input_ports=[{"name": "input"}],
        output_ports=[{"name": "results"}],
    ) as orch:
        with orch.team("research") as sub:
            research = sub.code(
                "research_task",
                code="result = {'team': 'research', 'value': f'{input}-research'}",
                input_ports=[{"name": "input", "required": False}],
                output_ports=[{"name": "team"}, {"name": "value"}],
            )
            sub.edge(sub.entry_input, research["input"])

    legacy_graph = wf.build()
    worker_graph = convert_graph(legacy_graph)

    assert validate_conversion(legacy_graph, worker_graph) == []
    assert worker_graph.node_by_id("coord").node_type == "orchestrator"
    assert worker_graph.sub_graphs["coord_research"].node_by_id("research_task").node_type == "worker"

    responses = [
        CompletionResult(
            text="Dispatch research",
            tool_calls=[
                {
                    "function": {
                        "name": "dispatch_to_team",
                        "arguments": '{"team_name": "research", "inputs": {}}',
                    }
                }
            ],
        ),
        CompletionResult(
            text="Stop orchestration",
            tool_calls=[
                {
                    "function": {
                        "name": "halt_orchestrator",
                        "arguments": (
                            '{"reason": "research complete", '
                            '"final_result": {"winner": "research"}}'
                        ),
                    }
                }
            ],
        ),
        CompletionResult(
            text="Dispatch research",
            tool_calls=[
                {
                    "function": {
                        "name": "dispatch_to_team",
                        "arguments": '{"team_name": "research", "inputs": {}}',
                    }
                }
            ],
        ),
        CompletionResult(
            text="Stop orchestration",
            tool_calls=[
                {
                    "function": {
                        "name": "halt_orchestrator",
                        "arguments": (
                            '{"reason": "research complete", '
                            '"final_result": {"winner": "research"}}'
                        ),
                    }
                }
            ],
        ),
    ]
    provider = _SequenceProvider(responses)
    monkeypatch.setattr(
        control_flow_executors,
        "resolve_completion_provider",
        lambda *args, **kwargs: provider,
    )

    legacy_events: list[EngineEvent] = []
    worker_events: list[EngineEvent] = []

    async def capture_legacy(event: EngineEvent) -> None:
        legacy_events.append(event)

    async def capture_worker(event: EngineEvent) -> None:
        worker_events.append(event)

    legacy_result = await _default_engine(capture_legacy).run(
        legacy_graph,
        inputs={"input": "topic"},
    )
    worker_result = await _default_engine(capture_worker).run(
        worker_graph,
        inputs={"input": "topic"},
    )

    assert legacy_result.success is True
    assert worker_result.success is True
    assert worker_result.outputs == legacy_result.outputs
    assert worker_result.node_statuses == legacy_result.node_statuses
    assert _normalize_node_events(worker_events) == _normalize_node_events(legacy_events)


@pytest.mark.asyncio
async def test_convert_graph_preserves_nested_workflow_with_specialized_control_and_workerized_compute() -> None:
    wf = workflow("nested_worker_parity")
    workflow_inputs = wf.input_node(
        "workflow_inputs",
        variables=[{"name": "topic", "type": "string"}],
    )
    idea = wf.llm(
        "idea",
        model="stub-model",
        prompt="Generate idea about {input}",
    )
    wf.edge(workflow_inputs["topic"], idea["input"])

    seed = wf.code(
        "seed",
        code=(
            "result = {"
            "'items': [f'{input}-A', f'{input}-B'], "
            "'counter': 0"
            "}"
        ),
        input_ports=[{"name": "input", "required": False}],
        output_ports=[{"name": "items"}, {"name": "counter"}],
    )
    wf.edge(idea["text"], seed["input"])

    with wf.for_each(
        "sections",
        items=seed["items"],
        parallelism=1,
        merge_strategy=MergeStrategy.APPEND,
        output_ports=[{"name": "results"}],
    ) as body:
        writer = body.llm(
            "writer",
            model="stub-model",
            prompt="Write section for {input}",
        )
        body.edge(body.entry_item, writer["input"])
    sections_ref = NodeRef("sections", "for_each", wf)

    assemble = wf.code(
        "assemble",
        code=(
            "texts = []\n"
            "for entry in results:\n"
            "    if isinstance(entry, dict):\n"
            "        texts.append(str(entry.get('text', '')))\n"
            "    else:\n"
            "        texts.append(str(entry))\n"
            "result = {'draft': '|'.join(texts), 'counter': counter}"
        ),
        input_ports=[{"name": "results"}, {"name": "counter"}],
        output_ports=[{"name": "draft"}, {"name": "counter"}],
    )
    wf.edge(seed["counter"], assemble["counter"])
    wf.edge(sections_ref["results"], assemble["results"])

    with wf.while_loop(
        "refine_loop",
        condition="counter < 2",
        max_iterations=10,
        input_ports=[{"name": "draft"}, {"name": "counter"}],
        output_ports=[{"name": "draft"}, {"name": "counter"}],
    ) as body:
        advance = body.code(
            "advance",
            code=(
                "result = {"
                "'draft': f'{draft}|iter{counter + 1}', "
                "'counter': counter + 1"
                "}"
            ),
            input_ports=[{"name": "draft"}, {"name": "counter"}],
            output_ports=[{"name": "draft"}, {"name": "counter"}],
        )
    refine_loop_ref = NodeRef("refine_loop", "while_loop", wf)

    wf.edge(assemble["draft"], refine_loop_ref["draft"])
    wf.edge(assemble["counter"], refine_loop_ref["counter"])

    legacy_graph = wf.build()
    worker_graph = convert_graph(legacy_graph)

    assert validate_conversion(legacy_graph, worker_graph) == []
    assert worker_graph.node_by_id("idea").node_type == "worker"
    assert worker_graph.node_by_id("sections").node_type == "for_each"
    assert worker_graph.node_by_id("refine_loop").node_type == "while_loop"
    assert worker_graph.sub_graphs["sections_body"].node_by_id("writer").node_type == "worker"
    assert worker_graph.sub_graphs["refine_loop_body"].node_by_id("advance").node_type == "worker"

    legacy_events: list[EngineEvent] = []
    worker_events: list[EngineEvent] = []

    async def capture_legacy(event: EngineEvent) -> None:
        legacy_events.append(event)

    async def capture_worker(event: EngineEvent) -> None:
        worker_events.append(event)

    legacy_engine = _engine()
    legacy_engine.event_callback = capture_legacy
    worker_engine = _engine()
    worker_engine.event_callback = capture_worker

    legacy_result = await legacy_engine.run(legacy_graph, inputs={"topic": "agents"})
    worker_result = await worker_engine.run(worker_graph, inputs={"topic": "agents"})

    assert legacy_result.success is True
    assert worker_result.success is True
    assert worker_result.outputs == legacy_result.outputs
    assert worker_result.node_statuses == legacy_result.node_statuses
    assert _normalize_node_events(worker_events) == _normalize_node_events(legacy_events)
