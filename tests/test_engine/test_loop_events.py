"""Tests for 6-6: loop iteration events emitted by WhileLoop and ForEach executors."""

from __future__ import annotations

import asyncio
import pytest

from dan.engine import Engine, EngineConfig
from dan.engine.checkpoint import NullCheckpointStore
from dan.engine.events import EngineEvent, EventType
from dan.engine.executor import ExecutionContext, ExecutorRegistry, NodeResult
from dan.engine.state import NodeStatus
from dan.models.control_flow import ForEachNode, WhileLoopNode
from dan.models.context import FailurePolicy, MergeStrategy
from dan.models.edges import DataEdge
from dan.models.graph import Graph
from dan.models.nodes import CodeOperator, NodeBase
from dan.models.ports import InputPort, OutputPort


def _config():
    return EngineConfig(checkpoint_enabled=False)


class _Collector:
    """Collects engine events for assertions."""

    def __init__(self):
        self.events: list[dict] = []

    async def __call__(self, event: EngineEvent) -> None:
        self.events.append(event.to_dict())

    def of_type(self, event_type: str) -> list[dict]:
        return [e for e in self.events if e["event_type"] == event_type]


class TestWhileLoopIterationEvents:
    @pytest.mark.asyncio
    async def test_emits_iteration_events(self):
        body_node = CodeOperator(
            id="body", name="body",
            code="result = {'count': count + 1}",
            input_ports=[InputPort(name="count")],
            output_ports=[OutputPort(name="count")],
        )
        loop = WhileLoopNode(
            id="loop", name="loop",
            condition="count < 3",
            max_iterations=5,
            body_graph="loop_body",
            input_ports=[InputPort(name="count")],
            output_ports=[OutputPort(name="count")],
        )
        entry = CodeOperator(
            id="entry", name="entry",
            code="result = {'count': 0}",
            output_ports=[OutputPort(name="count")],
        )

        body_graph = Graph(
            nodes=[body_node],
            edges=[],
            entry_points=["body"],
            exit_points=["body"],
        )

        graph = Graph(
            nodes=[entry, loop],
            edges=[
                DataEdge(id="e1", source_node_id="entry", source_port="count",
                         target_node_id="loop", target_port="count"),
            ],
            entry_points=["entry"],
            exit_points=["loop"],
            sub_graphs={"loop_body": body_graph},
        )

        collector = _Collector()
        engine = Engine(
            config=_config(),
            checkpoint_store=NullCheckpointStore(),
            event_callback=collector,
        )
        result = await engine.run(graph, run_id="test-while")
        assert result.success

        iter_started = collector.of_type("iteration_started")
        iter_completed = collector.of_type("iteration_completed")

        assert len(iter_started) >= 1
        assert len(iter_completed) >= 1
        assert iter_started[0]["node_id"] == "loop"
        assert iter_started[0]["data"]["iteration"] == 0
        assert iter_started[0]["data"]["max_iterations"] == 5


class TestForEachIterationEvents:
    @pytest.mark.asyncio
    async def test_emits_per_item_events(self):
        body_node = CodeOperator(
            id="body", name="body",
            code="result = {'processed': item}",
            input_ports=[InputPort(name="item"), InputPort(name="index")],
            output_ports=[OutputPort(name="processed")],
        )
        entry = CodeOperator(
            id="entry", name="entry",
            code="result = {'items': ['a', 'b', 'c']}",
            output_ports=[OutputPort(name="items")],
        )
        foreach = ForEachNode(
            id="fe", name="fe",
            body_graph="fe_body",
            input_ports=[InputPort(name="items")],
            output_ports=[OutputPort(name="results")],
        )

        body_graph = Graph(
            nodes=[body_node],
            edges=[],
            entry_points=["body"],
            exit_points=["body"],
        )

        graph = Graph(
            nodes=[entry, foreach],
            edges=[
                DataEdge(id="e1", source_node_id="entry", source_port="items",
                         target_node_id="fe", target_port="items"),
            ],
            entry_points=["entry"],
            exit_points=["fe"],
            sub_graphs={"fe_body": body_graph},
        )

        collector = _Collector()
        engine = Engine(
            config=_config(),
            checkpoint_store=NullCheckpointStore(),
            event_callback=collector,
        )
        result = await engine.run(graph, run_id="test-foreach")
        assert result.success

        iter_started = collector.of_type("iteration_started")
        iter_completed = collector.of_type("iteration_completed")

        assert len(iter_started) == 3
        assert len(iter_completed) == 3
        assert iter_started[0]["data"]["total"] == 3


class TestHumanInputEvent:
    @pytest.mark.asyncio
    async def test_emits_human_input_needed(self):
        from dan.models.control_flow import HumanInTheLoopNode

        human_responses: dict[str, dict] = {}

        async def fake_callback(meta: dict) -> dict:
            request_id = meta["request_id"]
            human_responses[request_id] = {"response": "user said hello"}
            return {"response": "user said hello"}

        human_node = HumanInTheLoopNode(
            id="human", name="human",
            prompt="What do you think?",
            input_ports=[InputPort(name="draft")],
            output_ports=[OutputPort(name="response")],
        )
        entry = CodeOperator(
            id="entry", name="entry",
            code="result = {'draft': 'hello world'}",
            output_ports=[OutputPort(name="draft")],
        )

        graph = Graph(
            nodes=[entry, human_node],
            edges=[
                DataEdge(id="e1", source_node_id="entry", source_port="draft",
                         target_node_id="human", target_port="draft"),
            ],
            entry_points=["entry"],
            exit_points=["human"],
        )

        collector = _Collector()
        engine = Engine(
            config=_config(),
            checkpoint_store=NullCheckpointStore(),
            event_callback=collector,
            human_input_callback=fake_callback,
        )
        result = await engine.run(graph, run_id="test-human")
        assert result.success

        hi_events = collector.of_type("human_input_needed")
        assert len(hi_events) == 1
        assert hi_events[0]["node_id"] == "human"
        assert hi_events[0]["data"]["prompt"] == "What do you think?"
        assert "request_id" in hi_events[0]["data"]


class TestRunManagerHumanInput:
    @pytest.mark.asyncio
    async def test_submit_and_resume(self):
        from dan.server.run_manager import RunManager

        rm = RunManager(engine_config=_config())

        assert rm.submit_human_input("nonexistent", "bad-id", {}) is False

        pending = rm.get_pending_human_inputs("nonexistent")
        assert pending == []
