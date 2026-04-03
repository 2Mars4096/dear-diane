from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import dan.engine.scheduler as scheduler_module
from dan.engine import Engine, EngineConfig
from dan.engine.checkpoint import NullCheckpointStore
from dan.engine.events import EngineEvent, EventType
from dan.engine.executor import ExecutorRegistry, NodeResult
from dan.engine.state import NodeStatus
from dan.executors.tool import ToolExecutor, ToolRegistry
from dan.linter import IntentConfig
from dan.models.edges import DataEdge
from dan.models.graph import Graph
from dan.models.legacy import CodeOperator
from dan.models.ports import InputPort, OutputPort
from dan.providers import CompletionResult
from dan.worker.executor import WorkerExecutor
from dan.worker.model import Worker


def _engine_with_worker(worker_executor: WorkerExecutor, callback=None) -> Engine:
    registry = ExecutorRegistry()
    registry.register("worker", worker_executor)
    return Engine(
        config=EngineConfig(checkpoint_enabled=False),
        checkpoint_store=NullCheckpointStore(),
        executor_registry=registry,
        event_callback=callback,
    )


@pytest.mark.asyncio
async def test_worker_lint_failure_blocks_handoff_and_skips_downstream() -> None:
    source = Worker(
        id="source",
        name="Source",
        code="result = {'summary': 'hello'}",
        input_ports=[InputPort(name="input", required=False)],
        output_ports=[OutputPort(name="result")],
    )
    target = Worker(
        id="target",
        name="Target",
        input_ports=[InputPort(name="input", required=True)],
        output_ports=[OutputPort(name="result")],
    )
    graph = Graph(
        nodes=[source, target],
        edges=[
            DataEdge(
                id="e1",
                source_node_id="source",
                source_port="result",
                target_node_id="target",
                target_port="input",
                lint={
                    "structural": {"required_keys": ["missing_key"]},
                    "severity": "error",
                },
            )
        ],
        entry_points=["source"],
        exit_points=["target"],
    )

    events: list[EngineEvent] = []

    async def capture(event: EngineEvent) -> None:
        events.append(event)

    engine = _engine_with_worker(WorkerExecutor(), callback=capture)
    result = await engine.run(graph)

    assert result.success is False
    assert result.node_statuses["source"] == "failed"
    assert result.node_statuses["target"] == "skipped"
    assert any(event.event_type == EventType.LINT_FAILED for event in events)


@pytest.mark.asyncio
async def test_worker_lint_autofix_publishes_fixed_handoff() -> None:
    source = Worker(
        id="source",
        name="Source",
        code="result = {'draft': 'abcdef'}",
        input_ports=[InputPort(name="input", required=False)],
        output_ports=[OutputPort(name="result")],
    )
    target = Worker(
        id="target",
        name="Target",
        input_ports=[InputPort(name="input", required=False)],
        output_ports=[OutputPort(name="result")],
    )
    graph = Graph(
        nodes=[source, target],
        edges=[
            DataEdge(
                id="e1",
                source_node_id="source",
                source_port="result",
                target_node_id="target",
                target_port="input",
                metadata={
                    "lint": {
                        "structural": {
                            "string_max_lengths": {"draft": 3},
                        },
                        "autofix": ["truncate"],
                        "severity": "error",
                    }
                },
            )
        ],
        entry_points=["source"],
        exit_points=["target"],
    )

    events: list[EngineEvent] = []

    async def capture(event: EngineEvent) -> None:
        events.append(event)

    engine = _engine_with_worker(WorkerExecutor(), callback=capture)
    result = await engine.run(graph)

    assert result.success is True
    assert result.outputs["result"]["draft"] == "abc"
    assert any(event.event_type == EventType.LINT_AUTO_FIXED for event in events)


@pytest.mark.asyncio
async def test_disabled_edge_lint_skips_runtime_and_emits_no_lint_events() -> None:
    source = Worker(
        id="source",
        name="Source",
        code="result = {'summary': 'hello'}",
        input_ports=[InputPort(name="input", required=False)],
        output_ports=[OutputPort(name="result")],
    )
    target = Worker(
        id="target",
        name="Target",
        input_ports=[InputPort(name="input", required=False)],
        output_ports=[OutputPort(name="result")],
    )
    graph = Graph(
        nodes=[source, target],
        edges=[
            DataEdge(
                id="e1",
                source_node_id="source",
                source_port="result",
                target_node_id="target",
                target_port="input",
                lint={
                    "enabled": False,
                    "structural": {"required_keys": ["missing_key"]},
                    "severity": "error",
                },
            )
        ],
        entry_points=["source"],
        exit_points=["target"],
    )

    events: list[EngineEvent] = []

    async def capture(event: EngineEvent) -> None:
        events.append(event)

    engine = _engine_with_worker(WorkerExecutor(), callback=capture)
    result = await engine.run(graph)

    assert result.success is True
    assert result.node_statuses["source"] == "completed"
    assert result.node_statuses["target"] == "completed"
    assert not any(
        event.event_type in {EventType.LINT_PASSED, EventType.LINT_FAILED, EventType.LINT_AUTO_FIXED}
        for event in events
    )


@pytest.mark.asyncio
async def test_runtime_lint_autogen_does_not_run_for_legacy_only_edges() -> None:
    source = CodeOperator(
        id="source",
        name="Source",
        code="result = {'value': 1}",
        output_ports=[OutputPort(name="value", json_schema={"type": "integer"})],
    )
    target = CodeOperator(
        id="target",
        name="Target",
        code="result = {'value': value + 1}",
        input_ports=[InputPort(name="value", json_schema={"type": "integer"})],
        output_ports=[OutputPort(name="value", json_schema={"type": "integer"})],
    )
    graph = Graph(
        nodes=[source, target],
        edges=[
            DataEdge(
                id="e1",
                source_node_id="source",
                source_port="value",
                target_node_id="target",
                target_port="value",
            )
        ],
        entry_points=["source"],
        exit_points=["target"],
    )

    events: list[EngineEvent] = []

    async def capture(event: EngineEvent) -> None:
        events.append(event)

    engine = Engine(
        config=EngineConfig(checkpoint_enabled=False),
        checkpoint_store=NullCheckpointStore(),
        event_callback=capture,
    )
    result = await engine.run(graph)

    assert result.success is True
    assert result.outputs["value"] == 2
    assert not any(
        event.event_type in {EventType.LINT_PASSED, EventType.LINT_FAILED, EventType.LINT_AUTO_FIXED}
        for event in events
    )


@pytest.mark.asyncio
async def test_worker_semantic_lint_without_embedding_provider_skips_tier_instead_of_failing() -> None:
    source = Worker(
        id="source",
        name="Source",
        code="result = 'finance summary'",
        input_ports=[InputPort(name="input", required=False)],
        output_ports=[OutputPort(name="result")],
    )
    target = Worker(
        id="target",
        name="Target",
        input_ports=[InputPort(name="input", required=False)],
        output_ports=[OutputPort(name="result")],
    )
    graph = Graph(
        nodes=[source, target],
        edges=[
            DataEdge(
                id="e1",
                source_node_id="source",
                source_port="result",
                target_node_id="target",
                target_port="input",
                lint={
                    "semantic": {
                        "reference_text": "finance summary for leadership",
                        "min_similarity": 0.8,
                    },
                    "severity": "error",
                },
            )
        ],
        entry_points=["source"],
        exit_points=["target"],
    )

    events: list[EngineEvent] = []

    async def capture(event: EngineEvent) -> None:
        events.append(event)

    engine = _engine_with_worker(WorkerExecutor(), callback=capture)
    result = await engine.run(graph)

    assert result.success is True
    assert result.node_statuses["source"] == "completed"
    assert result.node_statuses["target"] == "completed"
    assert not any(event.event_type == EventType.LINT_FAILED for event in events)


@pytest.mark.asyncio
async def test_worker_intent_judge_accepts_judgment_key_from_live_style_response() -> None:
    class _Gateway:
        async def complete(self, *args, **kwargs):
            return CompletionResult(
                text='{"judgment": "pass", "score": 0.9, "message": "ok"}',
            )

    engine = _engine_with_worker(WorkerExecutor())
    context = SimpleNamespace(
        embedding_registry=None,
        model_gateway=_Gateway(),
        config=engine.config,
    )

    runtime = engine._build_lint_runtime(context)
    assert runtime.judge_intent is not None

    result = await runtime.judge_intent(
        "finance summary",
        IntentConfig(intent="A concise executive finance summary."),
    )

    assert result == {
        "passed": True,
        "verdict": "pass",
        "score": 0.9,
        "message": "ok",
        "missing": [],
        "covered": [],
    }


@pytest.mark.asyncio
async def test_worker_intent_judge_builds_completeness_prompt_with_missing_context() -> None:
    prompts: list[str] = []

    class _Gateway:
        async def complete(self, messages, *args, **kwargs):
            prompts.append(messages[-1]["content"])
            return CompletionResult(
                text='{"judgment":"partial","score":0.5,"reason":"Need recommendation","missing":["recommendation"],"covered":["revenue"]}',
            )

    engine = _engine_with_worker(WorkerExecutor())
    context = SimpleNamespace(
        embedding_registry=None,
        model_gateway=_Gateway(),
        config=engine.config,
    )

    runtime = engine._build_lint_runtime(context)
    assert runtime.judge_intent is not None

    result = await runtime.judge_intent(
        {"summary": "Revenue improved"},
        IntentConfig(intent="Executive finance briefing with recommendation"),
        {"prompt_variant": "completeness", "missing": ["recommendation"]},
    )

    assert result["verdict"] == "partial"
    assert result["missing"] == ["recommendation"]
    assert result["covered"] == ["revenue"]
    assert "Focus specifically on these requirements" in prompts[0]
    assert "recommendation" in prompts[0]


@pytest.mark.asyncio
async def test_worker_intent_partial_warning_does_not_block_handoff() -> None:
    class _Gateway:
        async def complete(self, *args, **kwargs):
            return CompletionResult(
                text='{"verdict":"partial","score":0.4,"reason":"Missing recommendation","missing":["recommendation"]}',
            )

    source = Worker(
        id="source",
        name="Source",
        code="result = 'Executive briefing: revenue improved.'",
        input_ports=[InputPort(name="input", required=False)],
        output_ports=[OutputPort(name="result")],
    )
    target = Worker(
        id="target",
        name="Target",
        input_ports=[InputPort(name="input", required=False)],
        output_ports=[OutputPort(name="result")],
    )
    graph = Graph(
        nodes=[source, target],
        edges=[
            DataEdge(
                id="e1",
                source_node_id="source",
                source_port="result",
                target_node_id="target",
                target_port="input",
                lint={
                    "intent": {
                        "intent": "Executive finance briefing with recommendation",
                    },
                    "severity": "error",
                },
            )
        ],
        entry_points=["source"],
        exit_points=["target"],
    )

    events: list[EngineEvent] = []

    async def capture(event: EngineEvent) -> None:
        events.append(event)

    engine = _engine_with_worker(WorkerExecutor(), callback=capture)
    engine.model_gateway = _Gateway()
    result = await engine.run(graph)

    assert result.success is True
    assert result.node_statuses["source"] == "completed"
    assert result.node_statuses["target"] == "completed"
    assert any(event.event_type == EventType.LINT_PASSED for event in events)


@pytest.mark.asyncio
async def test_worker_resource_lock_serializes_parallel_entry_nodes() -> None:
    order: list[str] = []

    async def locked_tool(label: str) -> dict[str, str]:
        order.append(f"start:{label}")
        await asyncio.sleep(0.01)
        order.append(f"end:{label}")
        return {"result": label}

    tool_registry = ToolRegistry()
    tool_registry.register("locked_tool", locked_tool)
    worker_executor = WorkerExecutor(tool_executor=ToolExecutor(tool_registry))

    a = Worker(
        id="a",
        name="A",
        tool_ids=["locked_tool"],
        execution={"resource_locks": ["shared"], "blocking_mode": "exclusive"},  # type: ignore[arg-type]
        metadata={"tool_config": {"label": "a"}},
        output_ports=[OutputPort(name="result")],
    )
    b = Worker(
        id="b",
        name="B",
        tool_ids=["locked_tool"],
        execution={"resource_locks": ["shared"], "blocking_mode": "exclusive"},  # type: ignore[arg-type]
        metadata={"tool_config": {"label": "b"}},
        output_ports=[OutputPort(name="result")],
    )
    graph = Graph(
        nodes=[a, b],
        edges=[],
        entry_points=["a", "b"],
        exit_points=["a", "b"],
    )

    engine = _engine_with_worker(worker_executor)
    result = await engine.run(graph)

    assert result.success is True
    assert order in (["start:a", "end:a", "start:b", "end:b"], ["start:b", "end:b", "start:a", "end:a"])


@pytest.mark.asyncio
async def test_worker_resource_locks_do_not_block_unrelated_workers() -> None:
    active = 0
    max_active = 0
    both_started = asyncio.Event()
    start_count = 0

    async def tracked_tool(label: str) -> dict[str, str]:
        nonlocal active, max_active, start_count
        active += 1
        start_count += 1
        max_active = max(max_active, active)
        if start_count >= 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=0.1)
        await asyncio.sleep(0.01)
        active -= 1
        return {"result": label}

    tool_registry = ToolRegistry()
    tool_registry.register("tracked_tool", tracked_tool)
    worker_executor = WorkerExecutor(tool_executor=ToolExecutor(tool_registry))

    a = Worker(
        id="a",
        name="A",
        tool_ids=["tracked_tool"],
        execution={"resource_locks": ["alpha"], "blocking_mode": "exclusive"},  # type: ignore[arg-type]
        metadata={"tool_config": {"label": "a"}},
        output_ports=[OutputPort(name="result")],
    )
    b = Worker(
        id="b",
        name="B",
        tool_ids=["tracked_tool"],
        execution={"resource_locks": ["beta"], "blocking_mode": "exclusive"},  # type: ignore[arg-type]
        metadata={"tool_config": {"label": "b"}},
        output_ports=[OutputPort(name="result")],
    )
    graph = Graph(
        nodes=[a, b],
        edges=[],
        entry_points=["a", "b"],
        exit_points=["a", "b"],
    )

    engine = _engine_with_worker(worker_executor)
    result = await engine.run(graph)

    assert result.success is True
    assert max_active >= 2


class _RetryAwareLLMExecutor:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def execute(self, node, inputs, context) -> NodeResult:
        feedback = inputs.get("__lint_feedback__")
        self.calls.append({"system_prompt": getattr(node, "system_prompt", ""), "feedback": feedback})
        if feedback:
            return NodeResult(outputs={"result": {"summary": "repaired"}}, status=NodeStatus.COMPLETED)
        return NodeResult(outputs={"result": {"draft": "missing summary"}}, status=NodeStatus.COMPLETED)


@pytest.mark.asyncio
async def test_worker_lint_retry_with_feedback_reexecutes_producer() -> None:
    source = Worker(
        id="source",
        name="Source",
        model="test-model",
        input_ports=[InputPort(name="input", required=False)],
        output_ports=[OutputPort(name="result")],
    )
    target = Worker(
        id="target",
        name="Target",
        input_ports=[InputPort(name="input", required=True)],
        output_ports=[OutputPort(name="result")],
    )
    graph = Graph(
        nodes=[source, target],
        edges=[
            DataEdge(
                id="e1",
                source_node_id="source",
                source_port="result",
                target_node_id="target",
                target_port="input",
                metadata={
                    "lint": {
                        "structural": {"required_keys": ["summary"]},
                        "severity": "error",
                        "autofix": ["retry_with_feedback"],
                        "max_retries": 1,
                    }
                },
            )
        ],
        entry_points=["source"],
        exit_points=["target"],
    )

    events: list[EngineEvent] = []

    async def capture(event: EngineEvent) -> None:
        events.append(event)

    fake_llm = _RetryAwareLLMExecutor()
    engine = _engine_with_worker(WorkerExecutor(llm_executor=fake_llm), callback=capture)
    result = await engine.run(graph)

    assert result.success is True
    assert result.outputs["result"]["summary"] == "repaired"
    assert len(fake_llm.calls) == 2
    assert fake_llm.calls[1]["feedback"] is not None

    failed_event = next(event for event in events if event.event_type == EventType.LINT_FAILED)
    retry_event = next(event for event in events if event.event_type == EventType.RETRY_ATTEMPTED)
    passed_event = next(event for event in events if event.event_type == EventType.LINT_PASSED)

    assert failed_event.data["retry_scheduled"] is True
    assert failed_event.data["tier_reached"] == 1
    assert failed_event.data["attempt"] == 1
    assert retry_event.data["reason"] == "lint_feedback"
    assert retry_event.data["attempt"] == 1
    assert passed_event.data["attempt"] == 2
    assert passed_event.data["handoff_committed"] is True


@pytest.mark.asyncio
async def test_worker_lint_retry_with_feedback_does_not_reexecute_non_llm_workers() -> None:
    source = Worker(
        id="source",
        name="Source",
        code="result = {'draft': 'missing summary'}",
        output_ports=[OutputPort(name="result")],
    )
    target = Worker(
        id="target",
        name="Target",
        input_ports=[InputPort(name="input", required=True)],
        output_ports=[OutputPort(name="result")],
    )
    graph = Graph(
        nodes=[source, target],
        edges=[
            DataEdge(
                id="e1",
                source_node_id="source",
                source_port="result",
                target_node_id="target",
                target_port="input",
                lint={
                    "structural": {"required_keys": ["summary"]},
                    "severity": "error",
                    "autofix": ["retry_with_feedback"],
                    "max_retries": 1,
                },
            )
        ],
        entry_points=["source"],
        exit_points=["target"],
    )

    events: list[EngineEvent] = []

    async def capture(event: EngineEvent) -> None:
        events.append(event)

    engine = _engine_with_worker(WorkerExecutor(), callback=capture)
    result = await engine.run(graph)

    assert result.success is False
    assert result.node_statuses["source"] == "failed"
    assert result.node_statuses["target"] == "skipped"
    assert len([event for event in events if event.event_type == EventType.RETRY_ATTEMPTED]) == 0
    failed_event = next(event for event in events if event.event_type == EventType.LINT_FAILED)
    assert failed_event.data["retry_supported"] is False
    assert failed_event.data["retry_scheduled"] is False


@pytest.mark.asyncio
async def test_worker_lint_retry_feedback_includes_missing_requirements_and_output_preview() -> None:
    class _Gateway:
        async def complete(self, messages, *args, **kwargs):
            prompt = messages[-1]["content"]
            if "repaired recommendation" in prompt:
                return CompletionResult(text='{"verdict":"pass","score":1.0,"message":"ok"}')
            return CompletionResult(
                text='{"verdict":"fail","score":0.1,"reason":"Missing recommendation","missing":["recommendation"]}',
            )

    class _IntentRetryLLMExecutor:
        def __init__(self) -> None:
            self.feedbacks: list[str | None] = []

        async def execute(self, node, inputs, context) -> NodeResult:
            feedback = inputs.get("__lint_feedback__")
            self.feedbacks.append(feedback if isinstance(feedback, str) else None)
            if feedback:
                return NodeResult(
                    outputs={"result": "Executive finance briefing with repaired recommendation"},
                    status=NodeStatus.COMPLETED,
                )
            return NodeResult(
                outputs={"result": "Executive finance briefing without the final action"},
                status=NodeStatus.COMPLETED,
            )

    source = Worker(
        id="source",
        name="Source",
        model="test-model",
        output_ports=[OutputPort(name="result")],
    )
    target = Worker(
        id="target",
        name="Target",
        input_ports=[InputPort(name="input", required=True)],
        output_ports=[OutputPort(name="result")],
    )
    graph = Graph(
        nodes=[source, target],
        edges=[
            DataEdge(
                id="e1",
                source_node_id="source",
                source_port="result",
                target_node_id="target",
                target_port="input",
                lint={
                    "intent": {
                        "intent": "Executive finance briefing with recommendation",
                    },
                    "severity": "error",
                    "autofix": ["retry_with_feedback"],
                    "max_retries": 1,
                },
            )
        ],
        entry_points=["source"],
        exit_points=["target"],
    )

    fake_llm = _IntentRetryLLMExecutor()
    engine = _engine_with_worker(WorkerExecutor(llm_executor=fake_llm))
    engine.model_gateway = _Gateway()
    result = await engine.run(graph)

    assert result.success is True
    assert len(fake_llm.feedbacks) == 2
    retry_feedback = fake_llm.feedbacks[1]
    assert retry_feedback is not None
    assert "Still missing: recommendation" in retry_feedback
    assert "Downstream intent: Executive finance briefing with recommendation" in retry_feedback
    assert "Previous output:" in retry_feedback
    assert "Executive finance briefing without the final action" in retry_feedback


@pytest.mark.asyncio
async def test_worker_lint_refocus_feedback_reexecutes_producer() -> None:
    class _RefocusRetryLLMExecutor:
        def __init__(self) -> None:
            self.feedbacks: list[str | None] = []

        async def execute(self, node, inputs, context) -> NodeResult:
            feedback = inputs.get("__lint_feedback__")
            self.feedbacks.append(feedback if isinstance(feedback, str) else None)
            if feedback:
                return NodeResult(
                    outputs={"result": "Finance risk summary with mitigation plan"},
                    status=NodeStatus.COMPLETED,
                )
            return NodeResult(
                outputs={"result": "Weekend hiking checklist with snacks and boots"},
                status=NodeStatus.COMPLETED,
            )

    source = Worker(
        id="source",
        name="Source",
        model="test-model",
        output_ports=[OutputPort(name="result")],
    )
    target = Worker(
        id="target",
        name="Target",
        input_ports=[InputPort(name="input", required=True)],
        output_ports=[OutputPort(name="result")],
    )
    graph = Graph(
        nodes=[source, target],
        edges=[
            DataEdge(
                id="e1",
                source_node_id="source",
                source_port="result",
                target_node_id="target",
                target_port="input",
                lint={
                    "semantic": {
                        "topic_keywords": ["finance", "risk"],
                        "reference_text": "Finance risk summary with mitigation plan",
                    },
                    "severity": "error",
                    "autofix": ["refocus"],
                    "max_retries": 1,
                },
            )
        ],
        entry_points=["source"],
        exit_points=["target"],
    )

    fake_llm = _RefocusRetryLLMExecutor()
    engine = _engine_with_worker(WorkerExecutor(llm_executor=fake_llm))
    result = await engine.run(graph)

    assert result.success is True
    assert len(fake_llm.feedbacks) == 2
    retry_feedback = fake_llm.feedbacks[1]
    assert retry_feedback is not None
    assert "Refocus the response on the required downstream subject matter." in retry_feedback
    assert "Required topic keywords: finance, risk" in retry_feedback
    assert "Weekend hiking checklist with snacks and boots" in retry_feedback


@pytest.mark.asyncio
async def test_worker_lint_retry_budget_exhaustion_stops_further_retries(monkeypatch) -> None:
    tick = {"value": 0.0}

    def fake_time() -> float:
        current = tick["value"]
        tick["value"] += 0.001
        return current

    monkeypatch.setattr(scheduler_module._time, "time", fake_time)

    class _BudgetAwareLLMExecutor:
        def __init__(self) -> None:
            self.feedbacks: list[str | None] = []

        async def execute(self, node, inputs, context) -> NodeResult:
            feedback = inputs.get("__lint_feedback__")
            self.feedbacks.append(feedback if isinstance(feedback, str) else None)
            return NodeResult(outputs={"result": {"draft": "still missing summary"}}, status=NodeStatus.COMPLETED)

    source = Worker(
        id="source",
        name="Source",
        model="test-model",
        output_ports=[OutputPort(name="result")],
    )
    target = Worker(
        id="target",
        name="Target",
        input_ports=[InputPort(name="input", required=True)],
        output_ports=[OutputPort(name="result")],
    )
    graph = Graph(
        nodes=[source, target],
        edges=[
            DataEdge(
                id="e1",
                source_node_id="source",
                source_port="result",
                target_node_id="target",
                target_port="input",
                lint={
                    "structural": {"required_keys": ["summary"]},
                    "severity": "error",
                    "autofix": ["retry_with_feedback"],
                    "max_retries": 2,
                    "retry_budget_ms": 4,
                },
            )
        ],
        entry_points=["source"],
        exit_points=["target"],
    )

    events: list[EngineEvent] = []

    async def capture(event: EngineEvent) -> None:
        events.append(event)

    fake_llm = _BudgetAwareLLMExecutor()
    engine = _engine_with_worker(WorkerExecutor(llm_executor=fake_llm), callback=capture)
    result = await engine.run(graph)

    assert result.success is False
    assert result.node_statuses["source"] == "failed"
    assert result.node_statuses["target"] == "skipped"
    assert len(fake_llm.feedbacks) == 2
    exhausted_event = next(
        event
        for event in events
        if event.event_type == EventType.LINT_FAILED and event.data.get("retry_budget_exhausted") is True
    )
    assert exhausted_event.data["retry_budget_ms"] == 4
    assert exhausted_event.data["retry_elapsed_ms"] >= 4


@pytest.mark.asyncio
async def test_worker_without_lint_config_emits_no_lint_events() -> None:
    source = Worker(
        id="source",
        name="Source",
        code="result = {'summary': 'hello'}",
        output_ports=[OutputPort(name="result")],
    )
    target = Worker(
        id="target",
        name="Target",
        input_ports=[InputPort(name="input", required=False)],
        output_ports=[OutputPort(name="result")],
    )
    graph = Graph(
        nodes=[source, target],
        edges=[
            DataEdge(
                id="e1",
                source_node_id="source",
                source_port="result",
                target_node_id="target",
                target_port="input",
            )
        ],
        entry_points=["source"],
        exit_points=["target"],
    )

    events: list[EngineEvent] = []

    async def capture(event: EngineEvent) -> None:
        events.append(event)

    engine = _engine_with_worker(WorkerExecutor(), callback=capture)
    result = await engine.run(graph)

    assert result.success is True
    assert all(
        event.event_type not in {EventType.LINT_PASSED, EventType.LINT_FAILED, EventType.LINT_AUTO_FIXED}
        for event in events
    )
