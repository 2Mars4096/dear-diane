"""Unit tests for engine event instrumentation."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from dan.engine.events import EngineEvent, EventType
from dan.engine.executor import EngineConfig
from dan.engine.scheduler import Engine
from dan.models.graph import Graph, GraphMetadata
from dan.models.nodes import LLMOperator, CodeOperator
from dan.models.ports import InputPort, OutputPort
from dan.models.edges import DataEdge


@pytest.mark.asyncio
async def test_events_emitted_on_run():
    """Engine emits run_started, node lifecycle, and run_completed events."""
    events: list[EngineEvent] = []

    async def capture(event: EngineEvent) -> None:
        events.append(event)

    node = CodeOperator(
        id="n1", name="adder",
        input_ports=[InputPort(name="x", schema={"type": "number"})],
        output_ports=[OutputPort(name="output", schema={"type": "number"})],
        code="result = {'output': x + 1}",
    )
    graph = Graph(
        metadata=GraphMetadata(name="test"),
        nodes=[node],
        edges=[],
        entry_points=["n1"],
        exit_points=["n1"],
    )

    engine = Engine(
        config=EngineConfig(checkpoint_enabled=False),
        event_callback=capture,
    )
    result = await engine.run(graph, inputs={"x": 5})

    assert result.success
    types = [e.event_type for e in events]
    assert EventType.RUN_STARTED in types
    assert EventType.NODE_STARTED in types
    assert EventType.NODE_COMPLETED in types
    assert EventType.RUN_COMPLETED in types


@pytest.mark.asyncio
async def test_no_events_without_callback():
    """Engine works fine with no event callback — backward compatible."""
    node = CodeOperator(
        id="n1", name="adder",
        input_ports=[InputPort(name="x", schema={"type": "number"})],
        output_ports=[OutputPort(name="output", schema={"type": "number"})],
        code="result = {'output': x + 1}",
    )
    graph = Graph(
        metadata=GraphMetadata(name="test"),
        nodes=[node],
        edges=[],
        entry_points=["n1"],
        exit_points=["n1"],
    )

    engine = Engine(config=EngineConfig(checkpoint_enabled=False))
    result = await engine.run(graph, inputs={"x": 5})
    assert result.success


@pytest.mark.asyncio
async def test_node_output_event_emitted():
    """NODE_OUTPUT event is emitted with the node's outputs."""
    events: list[EngineEvent] = []

    async def capture(event: EngineEvent) -> None:
        events.append(event)

    node = CodeOperator(
        id="n1", name="doubler",
        input_ports=[InputPort(name="val", schema={"type": "number"})],
        output_ports=[OutputPort(name="result", schema={"type": "number"})],
        code="result = {'result': val * 2}",
    )
    graph = Graph(
        metadata=GraphMetadata(name="test"),
        nodes=[node],
        edges=[],
        entry_points=["n1"],
        exit_points=["n1"],
    )

    engine = Engine(
        config=EngineConfig(checkpoint_enabled=False),
        event_callback=capture,
    )
    result = await engine.run(graph, inputs={"val": 3})

    assert result.success
    output_events = [e for e in events if e.event_type == EventType.NODE_OUTPUT]
    assert len(output_events) == 1
    assert output_events[0].data["outputs"]["result"] == {"result": 6}


@pytest.mark.asyncio
async def test_event_to_dict():
    e = EngineEvent(
        event_type=EventType.NODE_STARTED,
        run_id="r1",
        node_id="n1",
        node_type="code_operator",
    )
    d = e.to_dict()
    assert d["event_type"] == "node_started"
    assert d["run_id"] == "r1"
    assert d["node_id"] == "n1"
    assert "timestamp" in d


@pytest.mark.asyncio
async def test_failed_callback_does_not_break_engine():
    """A broken event callback doesn't crash the engine."""
    async def bad_callback(event: EngineEvent) -> None:
        raise RuntimeError("callback error")

    node = CodeOperator(
        id="n1", name="ok",
        input_ports=[],
        output_ports=[OutputPort(name="output", schema={})],
        code="result = {'output': 42}",
    )
    graph = Graph(
        metadata=GraphMetadata(name="test"),
        nodes=[node],
        edges=[],
        entry_points=["n1"],
        exit_points=["n1"],
    )

    engine = Engine(
        config=EngineConfig(checkpoint_enabled=False),
        event_callback=bad_callback,
    )
    result = await engine.run(graph)
    assert result.success
