from __future__ import annotations

import asyncio
import copy
import gc
import math
import time
import tracemalloc
from typing import Any

import pytest

from dan.engine.checkpoint import CheckpointData, CheckpointStore
from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
from dan.engine.memory import MemoryEntry
from dan.engine.executor import EngineConfig, ExecutorRegistry, NodeResult
from dan.engine.scheduler import Engine
from dan.engine.state import ExecutionState, NodeStatus
from dan.models.context import ContextDeclaration, ContextMode, SharedContextDeclaration
from dan.models.control_flow import ForEachNode, GateNode, ParallelSubagentsNode, VoteNode
from dan.models.edges import ContextEdge, DataEdge
from dan.models.graph import Graph, GraphMetadata
from dan.models.nodes import LLMOperator, ToolOperator
from dan.models.ports import InputPort, OutputPort
from dan.providers import CompletionResult, StreamChunk


class RecordingCheckpointStore(CheckpointStore):
    def __init__(self) -> None:
        self._latest: dict[str, dict[str, Any]] = {}
        self.saves: list[dict[str, Any]] = []

    async def save(self, run_id: str, state: dict[str, Any]) -> None:
        payload = copy.deepcopy(state)
        self._latest[run_id] = payload
        self.saves.append(payload)

    async def load(self, run_id: str) -> dict[str, Any] | None:
        payload = self._latest.get(run_id)
        return copy.deepcopy(payload) if payload is not None else None

    async def list_runs(self) -> list[str]:
        return sorted(self._latest)


class RecordingMemoryStore:
    def __init__(self) -> None:
        self.entries: dict[tuple[str, str, str], MemoryEntry] = {}

    async def read(self, workflow_id: str, session_id: str, key: str) -> MemoryEntry | None:
        return self.entries.get((workflow_id, session_id, key))

    async def write(self, workflow_id: str, session_id: str, entry: MemoryEntry) -> None:
        self.entries[(workflow_id, session_id, entry.key)] = entry

    async def delete(self, workflow_id: str, session_id: str, key: str) -> bool:
        return self.entries.pop((workflow_id, session_id, key), None) is not None

    async def list_keys(self, workflow_id: str, session_id: str) -> list[str]:
        return sorted(k for wf, sess, k in self.entries if wf == workflow_id and sess == session_id)

    async def list_sessions(self, workflow_id: str) -> list[str]:
        return sorted({sess for wf, sess, _ in self.entries if wf == workflow_id})

    async def read_all(self, workflow_id: str, session_id: str) -> dict[str, MemoryEntry]:
        return {
            key: entry
            for (wf, sess, key), entry in self.entries.items()
            if wf == workflow_id and sess == session_id
        }

    async def clear_session(self, workflow_id: str, session_id: str) -> None:
        for key in [k for k in self.entries if k[0] == workflow_id and k[1] == session_id]:
            self.entries.pop(key, None)


class MemoryAwareCheckpointStore(RecordingCheckpointStore):
    def __init__(self, memory_store: RecordingMemoryStore, expected_key: str) -> None:
        super().__init__()
        self.memory_store = memory_store
        self.expected_key = expected_key

    async def save(self, run_id: str, state: dict[str, Any]) -> None:
        checkpoint_data = state.get("checkpoint_data", {})
        if checkpoint_data.get("checkpoint_trigger") == "batch":
            assert await self.memory_store.read("wf", "sess", self.expected_key) is not None
        await super().save(run_id, state)


class DeferredWriteCheckpointStore(RecordingCheckpointStore):
    def __init__(self, memory_store: RecordingMemoryStore, absent_key: str) -> None:
        super().__init__()
        self.memory_store = memory_store
        self.absent_key = absent_key

    async def save(self, run_id: str, state: dict[str, Any]) -> None:
        checkpoint_data = state.get("checkpoint_data", {})
        if checkpoint_data.get("checkpoint_trigger") == "batch" and checkpoint_data.get(
            "pending_node_ids"
        ) == ["writer"]:
            assert await self.memory_store.read("wf", "sess", self.absent_key) is None
        await super().save(run_id, state)


class FakeToolExecutor:
    def __init__(self) -> None:
        self.starts: dict[str, float] = {}
        self.ends: dict[str, float] = {}
        self.calls: list[str] = []
        self.active = 0
        self.max_active = 0

    async def execute(
        self,
        node: ToolOperator,
        inputs: dict[str, Any],
        context: Any,
    ) -> NodeResult:
        self.calls.append(node.id)
        self.starts[node.id] = time.monotonic()
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            delay = float(node.tool_config.get("delay", 0.0))
            if delay > 0:
                await asyncio.sleep(delay)
            memory_key = node.tool_config.get("memory_key")
            if memory_key:
                context.write_memory(
                    memory_key,
                    node.tool_config.get("memory_value", node.id),
                    writer_node_id=node.id,
                )
            post_memory_delay = float(node.tool_config.get("post_memory_delay", 0.0))
            if post_memory_delay > 0:
                await asyncio.sleep(post_memory_delay)
            output_port = node.tool_config.get("output_port", "out")
            mode = str(node.tool_config.get("mode", "const"))
            if mode == "fail_after_memory":
                raise RuntimeError(f"{node.id} failed after memory write")
            if mode == "increment":
                input_key = str(node.tool_config.get("input_key", "input"))
                base = inputs.get(input_key, 0)
                output_value = int(base) + int(node.tool_config.get("increment_by", 1))
            elif mode == "echo_input":
                input_key = str(node.tool_config.get("input_key", "input"))
                output_value = inputs.get(input_key)
            elif mode == "read_shared":
                context_key = str(node.tool_config.get("context_key", "memo"))
                output_value = context.shared_context.read(context_key)
            else:
                output_value = node.tool_config.get("value", node.id)
            return NodeResult(
                outputs={output_port: output_value},
                status=NodeStatus.COMPLETED,
            )
        finally:
            self.ends[node.id] = time.monotonic()
            self.active -= 1


class FakeProviderRegistry:
    def __init__(self, provider: Any) -> None:
        self.provider = provider

    def resolve(self, model: str) -> Any:
        return self.provider


class TrackingProvider:
    def __init__(self, *, delay: float = 0.05) -> None:
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self.starts: list[float] = []
        self.ends: list[float] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int | None = None,
        **_: Any,
    ) -> CompletionResult:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.starts.append(time.monotonic())
        try:
            await asyncio.sleep(self.delay)
            return CompletionResult(
                text=f"{model}:{messages[-1]['content']}",
                usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                model=model,
            )
        finally:
            self.ends.append(time.monotonic())
            self.active -= 1

    async def stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int | None = None,
        **_: Any,
    ):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.starts.append(time.monotonic())
        try:
            await asyncio.sleep(self.delay)
            text = f"{model}:{messages[-1]['content']}"
            yield StreamChunk(delta=text, accumulated=text, done=True, usage={"prompt_tokens": 1, "completion_tokens": 1})
        finally:
            self.ends.append(time.monotonic())
            self.active -= 1


def _schema() -> dict[str, Any]:
    return {"type": "string"}


def _in_port(name: str, *, required: bool = True) -> InputPort:
    return InputPort(name=name, json_schema=_schema(), required=required)


def _out_port(name: str) -> OutputPort:
    return OutputPort(name=name, json_schema=_schema())


def _tool_node(
    node_id: str,
    *,
    input_ports: list[InputPort] | None = None,
    output_ports: list[OutputPort] | None = None,
    delay: float = 0.0,
    value: str | None = None,
    memory_key: str | None = None,
) -> ToolOperator:
    config: dict[str, Any] = {"delay": delay}
    if value is not None:
        config["value"] = value
    if memory_key is not None:
        config["memory_key"] = memory_key
    return ToolOperator(
        id=node_id,
        name=node_id,
        tool_id=f"tool:{node_id}",
        input_ports=input_ports or [],
        output_ports=output_ports or [_out_port("out")],
        tool_config=config,
    )


def _llm_node(node_id: str, *, prompt: str) -> LLMOperator:
    return LLMOperator(
        id=node_id,
        name=node_id,
        model="fake-model",
        prompt_template=prompt,
        tools=[{
            "type": "function",
            "function": {"name": "noop", "description": "noop", "parameters": {"type": "object", "properties": {}}},
        }],
    )


def _edge(edge_id: str, source: str, source_port: str, target: str, target_port: str) -> DataEdge:
    return DataEdge(
        id=edge_id,
        source_node_id=source,
        source_port=source_port,
        target_node_id=target,
        target_port=target_port,
    )


def _loop_graph() -> Graph:
    gate = GateNode(
        id="gate",
        name="gate",
        gate_mode="while",
        condition="input < 2",
        max_iterations=4,
        input_ports=[_in_port("input")],
    )
    start = _tool_node("start", value=0)
    body = _tool_node(
        "body",
        input_ports=[_in_port("input")],
        delay=0.01,
    )
    body.tool_config.update({"mode": "increment", "increment_by": 1})
    sink = _tool_node(
        "sink",
        input_ports=[_in_port("input")],
    )
    sink.tool_config.update({"mode": "echo_input"})
    return Graph(
        nodes=[start, body, gate, sink],
        edges=[
            _edge("e1", "start", "out", "gate", "input"),
            _edge("e2", "gate", "continue", "body", "input"),
            _edge("e3", "body", "out", "gate", "input"),
            _edge("e4", "gate", "done", "sink", "input"),
        ],
        entry_points=["start"],
        exit_points=["sink"],
    )


def _loop_with_parallel_path_graph() -> Graph:
    gate = GateNode(
        id="gate",
        name="gate",
        gate_mode="while",
        condition="input < 2",
        max_iterations=4,
        input_ports=[_in_port("input")],
    )
    start = _tool_node("start", value=0)
    fast = _tool_node("fast", input_ports=[_in_port("inp")], delay=0.01, value="fast")
    after_fast = _tool_node("after_fast", input_ports=[_in_port("inp")], delay=0.01, value="after_fast")
    body = _tool_node(
        "body",
        input_ports=[_in_port("input")],
        delay=0.25,
    )
    body.tool_config.update({"mode": "increment", "increment_by": 1})
    sink = _tool_node(
        "sink",
        input_ports=[_in_port("input")],
    )
    sink.tool_config.update({"mode": "echo_input"})
    return Graph(
        nodes=[start, fast, after_fast, body, gate, sink],
        edges=[
            _edge("e1", "start", "out", "gate", "input"),
            _edge("e2", "start", "out", "fast", "inp"),
            _edge("e3", "fast", "out", "after_fast", "inp"),
            _edge("e4", "gate", "continue", "body", "input"),
            _edge("e5", "body", "out", "gate", "input"),
            _edge("e6", "gate", "done", "sink", "input"),
        ],
        entry_points=["start"],
        exit_points=["after_fast", "sink"],
    )


def _chain_graph(
    length: int,
    *,
    default_delay: float = 0.01,
    delay_overrides: dict[str, float] | None = None,
) -> Graph:
    delay_overrides = delay_overrides or {}
    nodes = []
    edges = []
    for index in range(length):
        node_id = f"n{index}"
        nodes.append(
            _tool_node(
                node_id,
                input_ports=[_in_port("inp")] if index > 0 else None,
                delay=delay_overrides.get(node_id, default_delay),
                value=node_id,
            ),
        )
        if index > 0:
            edges.append(_edge(f"e{index}", f"n{index - 1}", "out", node_id, "inp"))
    return Graph(
        nodes=nodes,
        edges=edges,
        entry_points=["n0"],
        exit_points=[f"n{length - 1}"],
    )


def _tool_engine(
    executor: FakeToolExecutor,
    *,
    eager_dispatch: bool = True,
    checkpoint_store: CheckpointStore | None = None,
    checkpoint_batch_size: int = 5,
    max_concurrency: int | None = None,
    memory_store: Any | None = None,
    node_semaphore_acquire_timeout_sec: float | None = 30.0,
    max_subgraph_depth: int = 32,
    runtime_self_healing_enabled: bool = False,
) -> Engine:
    registry = ExecutorRegistry()
    registry.register("tool_operator", executor)
    return Engine(
        config=EngineConfig(
            checkpoint_enabled=checkpoint_store is not None,
            eager_dispatch=eager_dispatch,
            checkpoint_batch_size=checkpoint_batch_size,
            checkpoint_interval_sec=60.0,
            max_concurrency=max_concurrency,
            node_semaphore_acquire_timeout_sec=node_semaphore_acquire_timeout_sec,
            max_subgraph_depth=max_subgraph_depth,
            runtime_self_healing_enabled=runtime_self_healing_enabled,
        ),
        executor_registry=registry,
        checkpoint_store=checkpoint_store,
        memory_store=memory_store,
    )


async def _measure_run_duration(
    engine: Engine,
    graph: Graph,
    *,
    inputs: dict[str, Any] | None = None,
) -> tuple[Any, float]:
    start = time.monotonic()
    result = await engine.run(graph, inputs=inputs or {})
    elapsed = time.monotonic() - start
    return result, elapsed


@pytest.mark.asyncio
async def test_linear_chain_executes_strictly_in_order() -> None:
    executor = FakeToolExecutor()
    engine = _tool_engine(executor, eager_dispatch=True)

    result = await engine.run(_chain_graph(5))

    assert result.success is True
    assert executor.calls == [f"n{i}" for i in range(5)]
    for index in range(1, 5):
        assert executor.starts[f"n{index}"] >= executor.ends[f"n{index - 1}"]


@pytest.mark.asyncio
async def test_diamond_graph_waits_for_both_parents_before_join() -> None:
    executor = FakeToolExecutor()
    engine = _tool_engine(executor, eager_dispatch=True)

    graph = Graph(
        nodes=[
            _tool_node("root", delay=0.01, value="root"),
            _tool_node("left", input_ports=[_in_port("inp")], delay=0.05, value="left"),
            _tool_node("right", input_ports=[_in_port("inp")], delay=0.05, value="right"),
            _tool_node("join", input_ports=[_in_port("left"), _in_port("right")], delay=0.01, value="join"),
        ],
        edges=[
            _edge("e1", "root", "out", "left", "inp"),
            _edge("e2", "root", "out", "right", "inp"),
            _edge("e3", "left", "out", "join", "left"),
            _edge("e4", "right", "out", "join", "right"),
        ],
        entry_points=["root"],
        exit_points=["join"],
    )

    result = await engine.run(graph)

    assert result.success is True
    assert executor.starts["left"] < executor.ends["right"]
    assert executor.starts["right"] < executor.ends["left"]
    assert executor.starts["join"] >= max(executor.ends["left"], executor.ends["right"])


@pytest.mark.asyncio
async def test_eager_dispatch_respects_global_node_semaphore_for_cpu_bound_nodes() -> None:
    executor = FakeToolExecutor()
    engine = _tool_engine(
        executor,
        eager_dispatch=True,
        max_concurrency=3,
    )

    graph = Graph(
        nodes=[_tool_node(f"n{i}", delay=0.05, value=f"n{i}") for i in range(10)],
        entry_points=[f"n{i}" for i in range(10)],
        exit_points=[f"n{i}" for i in range(10)],
    )

    result = await engine.run(graph)

    assert result.success is True
    assert executor.max_active <= 3


@pytest.mark.asyncio
async def test_ready_queue_tie_breaking_is_deterministic_with_single_node_slot() -> None:
    executor = FakeToolExecutor()
    engine = _tool_engine(
        executor,
        eager_dispatch=True,
        max_concurrency=1,
    )

    graph = Graph(
        nodes=[
            _tool_node("root", value="root"),
            _tool_node("a", input_ports=[_in_port("inp")], delay=0.01, value="a"),
            _tool_node("b", input_ports=[_in_port("inp")], delay=0.01, value="b"),
            _tool_node("c", input_ports=[_in_port("inp")], delay=0.01, value="c"),
        ],
        edges=[
            _edge("e1", "root", "out", "a", "inp"),
            _edge("e2", "root", "out", "b", "inp"),
            _edge("e3", "root", "out", "c", "inp"),
        ],
        entry_points=["root"],
        exit_points=["a", "b", "c"],
    )

    result = await engine.run(graph)

    assert result.success is True
    assert executor.calls == ["root", "a", "b", "c"]


@pytest.mark.asyncio
async def test_eager_dispatch_starts_downstream_before_slow_sibling_finishes() -> None:
    executor = FakeToolExecutor()
    engine = _tool_engine(executor, eager_dispatch=True)

    graph = Graph(
        metadata=GraphMetadata(name="eager"),
        nodes=[
            _tool_node("root", value="root"),
            _tool_node("slow", input_ports=[_in_port("inp")], delay=0.15),
            _tool_node("fast", input_ports=[_in_port("inp")], delay=0.02),
            _tool_node("after_fast", input_ports=[_in_port("inp")], delay=0.01),
        ],
        edges=[
            _edge("e1", "root", "out", "slow", "inp"),
            _edge("e2", "root", "out", "fast", "inp"),
            _edge("e3", "fast", "out", "after_fast", "inp"),
        ],
        entry_points=["root"],
        exit_points=["after_fast"],
    )

    result = await engine.run(graph)

    assert result.success is True
    assert executor.starts["after_fast"] < executor.ends["slow"]


@pytest.mark.asyncio
async def test_skipped_node_still_unblocks_successors() -> None:
    executor = FakeToolExecutor()
    engine = _tool_engine(executor, eager_dispatch=True)

    gate = GateNode(
        id="gate",
        name="gate",
        condition="True",
        gate_mode="if_else",
    )
    skipped = _tool_node("skipped", input_ports=[_in_port("inp", required=False)])
    downstream = _tool_node("downstream", input_ports=[_in_port("inp", required=False)], value="downstream")

    graph = Graph(
        nodes=[gate, skipped, downstream],
        edges=[
            _edge("e1", "gate", "false", "skipped", "inp"),
            _edge("e2", "skipped", "out", "downstream", "inp"),
        ],
        entry_points=["gate"],
        exit_points=["downstream"],
    )

    result = await engine.run(graph)

    assert result.success is True
    assert result.node_statuses["skipped"] == NodeStatus.SKIPPED.value
    assert result.node_statuses["downstream"] == NodeStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_context_edge_write_is_visible_to_downstream_reader_under_eager_dispatch() -> None:
    executor = FakeToolExecutor()
    engine = _tool_engine(executor, eager_dispatch=True)

    writer = _tool_node("writer", input_ports=[_in_port("inp")], value="memo")
    writer.write_set = [ContextDeclaration(key="shared.memo", mode=ContextMode.WRITE)]
    reader = _tool_node(
        "reader",
        input_ports=[_in_port("inp"), _in_port("memo", required=False)],
        delay=0.01,
    )
    reader.tool_config.update({"mode": "read_shared", "context_key": "shared.memo"})
    reader.read_set = [ContextDeclaration(key="shared.memo", mode=ContextMode.READ)]

    graph = Graph(
        nodes=[
            _tool_node("root", value="root"),
            writer,
            reader,
        ],
        edges=[
            _edge("e1", "root", "out", "writer", "inp"),
            _edge("e2", "writer", "out", "reader", "inp"),
            ContextEdge(
                id="cx1",
                source_node_id="writer",
                source_port="out",
                target_node_id="reader",
                target_port="memo",
                context_key="shared.memo",
                mode=ContextMode.WRITE,
            ),
            ContextEdge(
                id="cx2",
                source_node_id="writer",
                source_port="out",
                target_node_id="reader",
                target_port="memo",
                context_key="shared.memo",
                mode=ContextMode.READ,
            ),
        ],
        shared_context=[SharedContextDeclaration(key="shared.memo")],
        entry_points=["root"],
        exit_points=["reader"],
    )

    result = await engine.run(graph)

    assert result.success is True
    assert result.outputs["out"] == "memo"


@pytest.mark.asyncio
async def test_checkpoint_records_running_nodes_during_eager_dispatch() -> None:
    checkpoint_store = RecordingCheckpointStore()
    executor = FakeToolExecutor()
    engine = _tool_engine(
        executor,
        eager_dispatch=True,
        checkpoint_store=checkpoint_store,
        checkpoint_batch_size=1,
    )

    graph = Graph(
        nodes=[
            _tool_node("fast", delay=0.01, value="fast"),
            _tool_node("slow", delay=0.15, value="slow"),
        ],
        entry_points=["fast", "slow"],
        exit_points=["fast", "slow"],
    )

    result = await engine.run(graph)

    assert result.success is True
    assert checkpoint_store.saves
    assert any(
        payload.get("checkpoint_data", {}).get("pending_node_ids") == ["slow"]
        and payload.get("checkpoint_data", {}).get("checkpoint_trigger") == "batch"
        for payload in checkpoint_store.saves
    )


@pytest.mark.asyncio
async def test_memory_flush_happens_before_checkpoint_save() -> None:
    memory_store = RecordingMemoryStore()
    checkpoint_store = MemoryAwareCheckpointStore(memory_store, expected_key="memo")
    executor = FakeToolExecutor()
    engine = _tool_engine(
        executor,
        eager_dispatch=True,
        checkpoint_store=checkpoint_store,
        checkpoint_batch_size=1,
        memory_store=memory_store,
    )

    graph = Graph(
        nodes=[
            _tool_node("writer", delay=0.01, value="writer", memory_key="memo"),
            _tool_node("slow", delay=0.15, value="slow"),
        ],
        entry_points=["writer", "slow"],
        exit_points=["writer", "slow"],
    )

    result = await engine.run(graph, workflow_id="wf", session_id="sess")

    assert result.success is True
    entry = await memory_store.read("wf", "sess", "memo")
    assert entry is not None
    assert entry.value == "writer"


@pytest.mark.asyncio
async def test_running_node_memory_write_is_deferred_until_owner_finishes() -> None:
    memory_store = RecordingMemoryStore()
    checkpoint_store = DeferredWriteCheckpointStore(memory_store, absent_key="foo")
    executor = FakeToolExecutor()
    engine = _tool_engine(
        executor,
        eager_dispatch=True,
        checkpoint_store=checkpoint_store,
        checkpoint_batch_size=1,
        memory_store=memory_store,
    )

    writer = _tool_node("writer", value="writer", memory_key="foo")
    writer.tool_config["post_memory_delay"] = 0.2
    fast = _tool_node("fast", delay=0.05, value="fast")

    result = await engine.run(
        Graph(
            nodes=[writer, fast],
            entry_points=["writer", "fast"],
            exit_points=["writer", "fast"],
        ),
        workflow_id="wf",
        session_id="sess",
    )

    entry = await memory_store.read("wf", "sess", "foo")
    assert result.success is True
    assert checkpoint_store.saves
    assert entry is not None
    assert entry.value == "writer"


@pytest.mark.asyncio
async def test_failed_owner_memory_write_is_not_persisted() -> None:
    memory_store = RecordingMemoryStore()
    executor = FakeToolExecutor()
    engine = _tool_engine(
        executor,
        eager_dispatch=True,
        memory_store=memory_store,
    )

    writer = _tool_node("writer", memory_key="foo")
    writer.tool_config["mode"] = "fail_after_memory"

    result = await engine.run(
        Graph(
            nodes=[writer],
            entry_points=["writer"],
            exit_points=["writer"],
        ),
        workflow_id="wf",
        session_id="sess",
    )

    entry = await memory_store.read("wf", "sess", "foo")
    assert result.success is False
    assert entry is None


@pytest.mark.asyncio
async def test_memory_written_before_checkpoint_is_available_after_resume() -> None:
    memory_store = RecordingMemoryStore()
    checkpoint_store = RecordingCheckpointStore()
    interrupted_executor = FakeToolExecutor()
    interrupted_engine = _tool_engine(
        interrupted_executor,
        eager_dispatch=True,
        checkpoint_store=checkpoint_store,
        checkpoint_batch_size=1,
        memory_store=memory_store,
    )

    interrupted_graph = Graph(
        nodes=[
            _tool_node("writer", delay=0.01, value="writer", memory_key="foo"),
            _tool_node("slow", delay=1.0, value="slow"),
        ],
        entry_points=["writer", "slow"],
        exit_points=["writer", "slow"],
    )

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            interrupted_engine.run(interrupted_graph, workflow_id="wf", session_id="sess"),
            timeout=0.2,
        )
    await asyncio.sleep(0)

    checkpoint = next(
        payload
        for payload in checkpoint_store.saves
        if payload.get("checkpoint_data", {}).get("completed_node_ids") == ["writer"]
    )
    run_id = checkpoint["state"]["run_id"]
    checkpoint_store._latest[run_id] = checkpoint

    resumed_executor = FakeToolExecutor()
    resumed_engine = _tool_engine(
        resumed_executor,
        eager_dispatch=True,
        checkpoint_store=checkpoint_store,
        checkpoint_batch_size=1,
        memory_store=memory_store,
    )
    resumed_graph = Graph(
        nodes=[
            _tool_node("writer", delay=0.01, value="writer", memory_key="foo"),
            _tool_node("slow", delay=0.01, value="slow"),
        ],
        entry_points=["writer", "slow"],
        exit_points=["writer", "slow"],
    )

    result = await resumed_engine.resume(
        resumed_graph,
        run_id,
        workflow_id="wf",
        session_id="sess",
    )

    entry = await memory_store.read("wf", "sess", "foo")
    assert result.success is True
    assert entry is not None
    assert entry.value == "writer"
    assert resumed_executor.calls == ["slow"]


@pytest.mark.asyncio
async def test_memory_writes_are_idempotent_when_resuming_same_checkpoint_twice() -> None:
    memory_store = RecordingMemoryStore()
    checkpoint_store = RecordingCheckpointStore()
    interrupted_executor = FakeToolExecutor()
    interrupted_engine = _tool_engine(
        interrupted_executor,
        eager_dispatch=True,
        checkpoint_store=checkpoint_store,
        checkpoint_batch_size=1,
        memory_store=memory_store,
    )

    interrupted_graph = Graph(
        nodes=[
            _tool_node("writer", delay=0.01, value="writer", memory_key="foo"),
            _tool_node("slow", delay=1.0, value="slow"),
        ],
        entry_points=["writer", "slow"],
        exit_points=["writer", "slow"],
    )

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            interrupted_engine.run(interrupted_graph, workflow_id="wf", session_id="sess"),
            timeout=0.2,
        )
    await asyncio.sleep(0)

    checkpoint = next(
        payload
        for payload in checkpoint_store.saves
        if payload.get("checkpoint_data", {}).get("completed_node_ids") == ["writer"]
    )
    run_id = checkpoint["state"]["run_id"]
    checkpoint_store._latest[run_id] = checkpoint

    for _ in range(2):
        resumed_executor = FakeToolExecutor()
        resumed_engine = _tool_engine(
            resumed_executor,
            eager_dispatch=True,
            checkpoint_store=checkpoint_store,
            checkpoint_batch_size=1,
            memory_store=memory_store,
        )
        resumed_graph = Graph(
            nodes=[
                _tool_node("writer", delay=0.01, value="writer", memory_key="foo"),
                _tool_node("slow", delay=0.01, value="slow"),
            ],
            entry_points=["writer", "slow"],
            exit_points=["writer", "slow"],
        )
        result = await resumed_engine.resume(
            resumed_graph,
            run_id,
            workflow_id="wf",
            session_id="sess",
        )
        assert result.success is True

    entry = await memory_store.read("wf", "sess", "foo")
    assert entry is not None
    assert entry.value == "writer"


@pytest.mark.asyncio
async def test_resume_restarts_running_nodes_only() -> None:
    checkpoint_store = RecordingCheckpointStore()
    executor = FakeToolExecutor()
    engine = _tool_engine(executor, eager_dispatch=True, checkpoint_store=checkpoint_store)

    graph = Graph(
        nodes=[
            _tool_node("done", value="done"),
            _tool_node("rerun", value="rerun"),
        ],
        entry_points=["done", "rerun"],
        exit_points=["done", "rerun"],
    )

    state = ExecutionState(graph, run_id="resume-run")
    state.mark("done", NodeStatus.COMPLETED)
    state.port_data.set("done", "out", "done")
    state.mark("rerun", NodeStatus.RUNNING)

    checkpoint_store._latest["resume-run"] = engine._build_checkpoint_payload(
        state,
        SharedContextStore(graph.shared_context),
        ArtifactStore(),
        LocalStateManager(),
        graph=graph,
        graph_id="",
        checkpoint_trigger="batch",
    )

    result = await engine.resume(graph, "resume-run")

    assert result.success is True
    assert executor.calls == ["rerun"]
    assert result.outputs["out"] == "rerun"


@pytest.mark.asyncio
async def test_resume_cycle_boundary_checkpoint_reenters_loop() -> None:
    checkpoint_store = RecordingCheckpointStore()
    executor = FakeToolExecutor()
    engine = _tool_engine(executor, eager_dispatch=True, checkpoint_store=checkpoint_store)
    graph = _loop_graph()

    state = ExecutionState(graph, run_id="resume-loop-boundary")
    state.mark("start", NodeStatus.COMPLETED)
    state.port_data.set("start", "out", 0)
    state.mark("gate", NodeStatus.COMPLETED)
    state.port_data.set("gate", "continue", {"input": 0})

    checkpoint_store._latest["resume-loop-boundary"] = engine._build_checkpoint_payload(
        state,
        SharedContextStore(graph.shared_context),
        ArtifactStore(),
        LocalStateManager(),
        graph=graph,
        graph_id="",
        checkpoint_trigger="cycle_boundary",
    )

    result = await engine.resume(graph, "resume-loop-boundary")

    assert result.success is True
    assert result.outputs["out"] == 2
    assert executor.calls == ["body", "body", "sink"]


@pytest.mark.asyncio
async def test_resume_mid_iteration_loop_does_not_replay_completed_body() -> None:
    checkpoint_store = RecordingCheckpointStore()
    executor = FakeToolExecutor()
    engine = _tool_engine(executor, eager_dispatch=True, checkpoint_store=checkpoint_store)
    graph = _loop_graph()

    state = ExecutionState(graph, run_id="resume-loop-mid-iteration")
    state.mark("start", NodeStatus.COMPLETED)
    state.port_data.set("start", "out", 0)
    state.mark("body", NodeStatus.COMPLETED)
    state.port_data.set("body", "out", 1)
    state.mark("gate", NodeStatus.RUNNING)

    checkpoint_store._latest["resume-loop-mid-iteration"] = engine._build_checkpoint_payload(
        state,
        SharedContextStore(graph.shared_context),
        ArtifactStore(),
        LocalStateManager(),
        graph=graph,
        graph_id="",
        checkpoint_trigger="batch",
    )

    result = await engine.resume(graph, "resume-loop-mid-iteration")

    assert result.success is True
    assert result.outputs["out"] == 2
    assert executor.calls == ["body", "sink"]


@pytest.mark.asyncio
async def test_resume_reruns_all_parallel_subgraph_branches_when_subgraph_checkpoints_are_disabled() -> None:
    checkpoint_store = RecordingCheckpointStore()
    resume_executor = FakeToolExecutor()
    resume_engine = _tool_engine(
        resume_executor,
        eager_dispatch=True,
        checkpoint_store=checkpoint_store,
        checkpoint_batch_size=1,
    )

    parallel = ParallelSubagentsNode(
        id="parallel",
        name="parallel",
        branch_graphs=["a", "b", "c"],
        parallelism=3,
    )
    graph = Graph(
        nodes=[parallel],
        sub_graphs={
            "a": Graph(
                nodes=[_tool_node("branch_a", delay=0.01, value="a")],
                entry_points=["branch_a"],
                exit_points=["branch_a"],
            ),
            "b": Graph(
                nodes=[_tool_node("branch_b", delay=0.05, value="b")],
                entry_points=["branch_b"],
                exit_points=["branch_b"],
            ),
            "c": Graph(
                nodes=[_tool_node("branch_c", delay=0.05, value="c")],
                entry_points=["branch_c"],
                exit_points=["branch_c"],
            ),
        },
        entry_points=["parallel"],
        exit_points=["parallel"],
    )

    state = ExecutionState(graph, run_id="resume-subgraph-skip")
    state.node_statuses["parallel"] = NodeStatus.RUNNING
    checkpoint_payload = resume_engine._build_checkpoint_payload(
        state,
        SharedContextStore(),
        ArtifactStore(),
        LocalStateManager(),
        graph=graph,
        checkpoint_trigger="halt",
    )
    checkpoint_store._latest["resume-subgraph-skip"] = checkpoint_payload

    result = await resume_engine.resume(graph, "resume-subgraph-skip")

    assert result.success is True
    assert checkpoint_payload["checkpoint_data"]["pending_node_ids"] == ["parallel"]
    assert sorted(resume_executor.calls) == ["branch_a", "branch_b", "branch_c"]


@pytest.mark.asyncio
async def test_parallel_subgraphs_share_llm_semaphore() -> None:
    provider = TrackingProvider(delay=0.05)
    engine = Engine(
        config=EngineConfig(
            eager_dispatch=True,
            checkpoint_enabled=False,
            llm_max_concurrency=2,
        ),
    )
    engine.provider_registry = FakeProviderRegistry(provider)

    branch_graph = lambda branch_id: Graph(  # noqa: E731
        nodes=[_llm_node(branch_id, prompt=branch_id)],
        entry_points=[branch_id],
        exit_points=[branch_id],
    )

    parallel = ParallelSubagentsNode(
        id="parallel",
        name="parallel",
        branch_graphs=["a", "b", "c"],
        parallelism=3,
    )

    graph = Graph(
        nodes=[parallel],
        sub_graphs={
            "a": branch_graph("a_node"),
            "b": branch_graph("b_node"),
            "c": branch_graph("c_node"),
        },
        entry_points=["parallel"],
        exit_points=["parallel"],
    )

    result = await engine.run(graph)

    assert result.success is True
    assert provider.max_active <= 2


@pytest.mark.asyncio
async def test_parallel_subgraphs_explicit_llm_cap_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAN_MAX_CONCURRENT_LLM", "7")
    provider = TrackingProvider(delay=0.05)
    engine = Engine(
        config=EngineConfig(
            eager_dispatch=True,
            checkpoint_enabled=False,
            llm_max_concurrency=2,
        ),
    )
    engine.provider_registry = FakeProviderRegistry(provider)

    branch_graph = lambda branch_id: Graph(  # noqa: E731
        nodes=[_llm_node(branch_id, prompt=branch_id)],
        entry_points=[branch_id],
        exit_points=[branch_id],
    )

    parallel = ParallelSubagentsNode(
        id="parallel",
        name="parallel",
        branch_graphs=["a", "b", "c"],
        parallelism=3,
    )

    graph = Graph(
        nodes=[parallel],
        sub_graphs={
            "a": branch_graph("a_node"),
            "b": branch_graph("b_node"),
            "c": branch_graph("c_node"),
        },
        entry_points=["parallel"],
        exit_points=["parallel"],
    )

    result = await engine.run(graph)

    assert result.success is True
    assert provider.max_active <= 2


@pytest.mark.asyncio
async def test_vote_parallelism_composes_with_global_node_semaphore() -> None:
    provider = TrackingProvider(delay=0.05)
    engine = Engine(
        config=EngineConfig(
            eager_dispatch=True,
            checkpoint_enabled=False,
            max_concurrency=1,
            llm_max_concurrency=3,
        ),
    )
    engine.provider_registry = FakeProviderRegistry(provider)

    vote = VoteNode(
        id="vote",
        name="vote",
        candidates=["fake-model"],
        num_votes=3,
        parallelism=3,
        prompt_template="{inp}",
        input_ports=[_in_port("inp")],
    )

    graph = Graph(
        nodes=[vote],
        entry_points=["vote"],
        exit_points=["vote"],
    )

    result = await engine.run(graph, inputs={"inp": "vote-input"})

    assert result.success is True
    assert provider.max_active <= 1


@pytest.mark.asyncio
async def test_parallel_subagents_foreach_nesting_shares_global_node_semaphore() -> None:
    executor = FakeToolExecutor()
    engine = _tool_engine(
        executor,
        eager_dispatch=False,
        max_concurrency=2,
    )

    def branch_graph() -> Graph:
        worker = _tool_node(
            "worker",
            input_ports=[_in_port("item")],
            delay=0.03,
        )
        worker.tool_config.update({"mode": "echo_input", "input_key": "item"})
        foreach = ForEachNode(
            id="foreach",
            name="foreach",
            body_graph="body",
            parallelism=3,
            input_ports=[_in_port("items")],
        )
        return Graph(
            nodes=[foreach],
            sub_graphs={
                "body": Graph(
                    nodes=[worker],
                    entry_points=["worker"],
                    exit_points=["worker"],
                ),
            },
            entry_points=["foreach"],
            exit_points=["foreach"],
        )

    parallel = ParallelSubagentsNode(
        id="parallel",
        name="parallel",
        branch_graphs=["a", "b"],
        parallelism=2,
        branch_inputs={
            "a": {"items": [1, 2, 3]},
            "b": {"items": [4, 5, 6]},
        },
    )

    graph = Graph(
        nodes=[parallel],
        sub_graphs={
            "a": branch_graph(),
            "b": branch_graph(),
        },
        entry_points=["parallel"],
        exit_points=["parallel"],
    )

    result = await engine.run(graph)

    assert result.success is True
    assert executor.max_active <= 2


@pytest.mark.asyncio
async def test_foreach_parallelism_composes_with_global_node_semaphore_under_eager_dispatch() -> None:
    executor = FakeToolExecutor()
    engine = _tool_engine(
        executor,
        eager_dispatch=True,
        max_concurrency=2,
    )

    worker = _tool_node(
        "worker",
        input_ports=[_in_port("item")],
        delay=0.05,
    )
    worker.tool_config.update({"mode": "echo_input", "input_key": "item"})

    foreach = ForEachNode(
        id="foreach",
        name="foreach",
        body_graph="body",
        parallelism=4,
        input_ports=[_in_port("items")],
    )

    graph = Graph(
        nodes=[foreach],
        sub_graphs={
            "body": Graph(
                nodes=[worker],
                entry_points=["worker"],
                exit_points=["worker"],
            ),
        },
        entry_points=["foreach"],
        exit_points=["foreach"],
    )

    result = await engine.run(graph, inputs={"items": list(range(8))})

    assert result.success is True
    assert executor.max_active <= 2


@pytest.mark.asyncio
async def test_llm_semaphore_does_not_block_unrelated_tool_nodes() -> None:
    provider = TrackingProvider(delay=0.15)
    tool_executor = FakeToolExecutor()
    registry = ExecutorRegistry()
    registry.register("tool_operator", tool_executor)
    engine = Engine(
        config=EngineConfig(
            eager_dispatch=True,
            checkpoint_enabled=False,
            llm_max_concurrency=1,
        ),
        executor_registry=registry,
    )
    engine.provider_registry = FakeProviderRegistry(provider)

    root = _tool_node("root", value="root")
    tool = _tool_node("tool", input_ports=[_in_port("inp")], delay=0.02, value="tool")
    llm = _llm_node("llm", prompt="{inp}")
    llm.input_ports = [_in_port("inp")]

    graph = Graph(
        nodes=[root, tool, llm],
        edges=[
            _edge("e1", "root", "out", "tool", "inp"),
            _edge("e2", "root", "out", "llm", "inp"),
        ],
        entry_points=["root"],
        exit_points=["tool", "llm"],
    )

    result = await engine.run(graph)

    assert result.success is True
    assert provider.max_active <= 1
    assert provider.ends
    assert tool_executor.ends["tool"] < provider.ends[0]


@pytest.mark.asyncio
async def test_legacy_level_sync_respects_global_node_semaphore() -> None:
    executor = FakeToolExecutor()
    engine = _tool_engine(
        executor,
        eager_dispatch=False,
        max_concurrency=3,
    )

    graph = Graph(
        nodes=[_tool_node(f"n{i}", delay=0.05, value=f"n{i}") for i in range(10)],
        entry_points=[f"n{i}" for i in range(10)],
        exit_points=[f"n{i}" for i in range(10)],
    )

    result = await engine.run(graph)

    assert result.success is True
    assert executor.max_active <= 3


@pytest.mark.asyncio
async def test_foreach_parallelism_composes_with_global_node_semaphore() -> None:
    executor = FakeToolExecutor()
    engine = _tool_engine(
        executor,
        eager_dispatch=False,
        max_concurrency=3,
    )

    worker = _tool_node(
        "worker",
        input_ports=[_in_port("item")],
        delay=0.05,
    )
    worker.tool_config.update({"mode": "echo_input", "input_key": "item"})

    foreach = ForEachNode(
        id="foreach",
        name="foreach",
        body_graph="body",
        parallelism=4,
        input_ports=[_in_port("items")],
    )

    graph = Graph(
        nodes=[foreach],
        sub_graphs={
            "body": Graph(
                nodes=[worker],
                entry_points=["worker"],
                exit_points=["worker"],
            ),
        },
        entry_points=["foreach"],
        exit_points=["foreach"],
    )

    result = await engine.run(graph, inputs={"items": list(range(8))})

    assert result.success is True
    assert executor.max_active <= 3


@pytest.mark.asyncio
async def test_control_flow_subgraphs_do_not_deadlock_with_single_global_slot() -> None:
    executor = FakeToolExecutor()
    engine = _tool_engine(
        executor,
        eager_dispatch=False,
        max_concurrency=1,
    )

    worker = _tool_node(
        "worker",
        input_ports=[_in_port("item")],
        delay=0.01,
    )
    worker.tool_config.update({"mode": "echo_input", "input_key": "item"})

    foreach = ForEachNode(
        id="foreach",
        name="foreach",
        body_graph="body",
        parallelism=1,
        input_ports=[_in_port("items")],
    )

    graph = Graph(
        nodes=[foreach],
        sub_graphs={
            "body": Graph(
                nodes=[worker],
                entry_points=["worker"],
                exit_points=["worker"],
            ),
        },
        entry_points=["foreach"],
        exit_points=["foreach"],
    )

    result = await asyncio.wait_for(
        engine.run(graph, inputs={"items": [1]}),
        timeout=1.0,
    )

    assert result.success is True
    assert executor.calls == ["worker"]


@pytest.mark.asyncio
async def test_runtime_self_healing_does_not_deadlock_control_flow_subgraphs() -> None:
    executor = FakeToolExecutor()
    engine = _tool_engine(
        executor,
        eager_dispatch=False,
        max_concurrency=1,
        runtime_self_healing_enabled=True,
    )

    worker = _tool_node(
        "worker",
        input_ports=[_in_port("item")],
        delay=0.01,
    )
    worker.tool_config.update({"mode": "echo_input", "input_key": "item"})

    foreach = ForEachNode(
        id="foreach",
        name="foreach",
        body_graph="body",
        parallelism=1,
        input_ports=[_in_port("items")],
    )

    graph = Graph(
        nodes=[foreach],
        sub_graphs={
            "body": Graph(
                nodes=[worker],
                entry_points=["worker"],
                exit_points=["worker"],
            ),
        },
        entry_points=["foreach"],
        exit_points=["foreach"],
    )

    result = await asyncio.wait_for(
        engine.run(graph, inputs={"items": [1]}),
        timeout=1.0,
    )

    assert result.success is True
    assert executor.calls == ["worker"]


@pytest.mark.asyncio
async def test_parallel_subagents_foreach_llm_nesting_does_not_deadlock() -> None:
    provider = TrackingProvider(delay=0.05)
    engine = Engine(
        config=EngineConfig(
            eager_dispatch=False,
            checkpoint_enabled=False,
            max_concurrency=1,
            llm_max_concurrency=1,
        ),
    )
    engine.provider_registry = FakeProviderRegistry(provider)

    def branch_graph() -> Graph:
        worker = _llm_node("worker", prompt="{item}")
        worker.input_ports = [_in_port("item")]
        foreach = ForEachNode(
            id="foreach",
            name="foreach",
            body_graph="body",
            parallelism=2,
            input_ports=[_in_port("items")],
        )
        return Graph(
            nodes=[foreach],
            sub_graphs={
                "body": Graph(
                    nodes=[worker],
                    entry_points=["worker"],
                    exit_points=["worker"],
                ),
            },
            entry_points=["foreach"],
            exit_points=["foreach"],
        )

    parallel = ParallelSubagentsNode(
        id="parallel",
        name="parallel",
        branch_graphs=["a", "b"],
        parallelism=2,
        branch_inputs={
            "a": {"items": ["a1", "a2"]},
            "b": {"items": ["b1", "b2"]},
        },
    )

    graph = Graph(
        nodes=[parallel],
        sub_graphs={
            "a": branch_graph(),
            "b": branch_graph(),
        },
        entry_points=["parallel"],
        exit_points=["parallel"],
    )

    result = await asyncio.wait_for(engine.run(graph), timeout=2.0)

    assert result.success is True
    assert provider.max_active <= 1


@pytest.mark.asyncio
async def test_subgraph_depth_guard_fails_fast_on_excessive_nesting() -> None:
    executor = FakeToolExecutor()
    engine = _tool_engine(
        executor,
        eager_dispatch=True,
        max_subgraph_depth=1,
    )
    parent_graph = Graph(
        sub_graphs={
            "leaf": Graph(
                nodes=[_tool_node("worker", value="done")],
                entry_points=["worker"],
                exit_points=["worker"],
            ),
        },
    )
    parent_state = ExecutionState(parent_graph, run_id="depth-guard")

    with pytest.raises(RuntimeError, match="Subgraph nesting depth 2 exceeds configured limit 1"):
        await engine._run_subgraph(
            "leaf",
            {},
            parent_graph,
            parent_state,
            SharedContextStore(parent_graph.shared_context),
            ArtifactStore(),
            LocalStateManager(),
            layer_path=("outer", "inner"),
        )


def test_checkpoint_data_backward_compatibility_defaults() -> None:
    data = CheckpointData.model_validate({
        "run_id": "run-1",
        "graph_id": "graph-1",
        "completed_node_ids": ["a"],
        "node_outputs": {"a": {"out": "value"}},
    })

    assert data.pending_node_ids == []
    assert data.checkpoint_trigger == ""


@pytest.mark.asyncio
async def test_while_cycle_completes_with_eager_dispatch() -> None:
    executor = FakeToolExecutor()
    engine = _tool_engine(executor, eager_dispatch=True)

    result = await engine.run(_loop_graph())

    assert result.success is True
    assert result.outputs["out"] == 2
    assert executor.calls == ["start", "body", "body", "sink"]


@pytest.mark.asyncio
async def test_while_cycle_matches_level_sync_result() -> None:
    eager_executor = FakeToolExecutor()
    eager_engine = _tool_engine(eager_executor, eager_dispatch=True)

    legacy_executor = FakeToolExecutor()
    legacy_engine = _tool_engine(legacy_executor, eager_dispatch=False)

    eager_result = await eager_engine.run(_loop_graph())
    legacy_result = await legacy_engine.run(_loop_graph())

    assert eager_result.success is True
    assert legacy_result.success is True
    assert eager_result.outputs == legacy_result.outputs
    assert eager_executor.calls == legacy_executor.calls == ["start", "body", "body", "sink"]


@pytest.mark.asyncio
async def test_cycle_execution_records_cycle_boundary_checkpoints() -> None:
    checkpoint_store = RecordingCheckpointStore()
    executor = FakeToolExecutor()
    engine = _tool_engine(
        executor,
        eager_dispatch=True,
        checkpoint_store=checkpoint_store,
    )

    result = await engine.run(_loop_graph())

    assert result.success is True
    assert any(
        payload.get("checkpoint_data", {}).get("checkpoint_trigger") == "cycle_boundary"
        for payload in checkpoint_store.saves
    )


@pytest.mark.asyncio
async def test_outer_cycle_scheduler_allows_parallel_path_to_advance() -> None:
    eager_executor = FakeToolExecutor()
    eager_engine = _tool_engine(eager_executor, eager_dispatch=True)

    legacy_executor = FakeToolExecutor()
    legacy_engine = _tool_engine(legacy_executor, eager_dispatch=False)

    eager_result = await eager_engine.run(_loop_with_parallel_path_graph())
    legacy_result = await legacy_engine.run(_loop_with_parallel_path_graph())

    assert eager_result.success is True
    assert legacy_result.success is True
    assert eager_result.outputs == legacy_result.outputs
    assert eager_executor.ends["after_fast"] < eager_executor.ends["body"]
    assert legacy_executor.starts["after_fast"] > legacy_executor.ends["body"]


@pytest.mark.asyncio
async def test_env_flag_toggles_eager_dispatch_on_same_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = Graph(
        nodes=[
            _tool_node("root", value="root"),
            _tool_node("slow", input_ports=[_in_port("inp")], delay=0.15),
            _tool_node("fast", input_ports=[_in_port("inp")], delay=0.02),
            _tool_node("after_fast", input_ports=[_in_port("inp")], delay=0.01),
        ],
        edges=[
            _edge("e1", "root", "out", "slow", "inp"),
            _edge("e2", "root", "out", "fast", "inp"),
            _edge("e3", "fast", "out", "after_fast", "inp"),
        ],
        entry_points=["root"],
        exit_points=["after_fast"],
    )

    monkeypatch.setenv("DAN_EAGER_DISPATCH", "0")
    legacy_executor = FakeToolExecutor()
    legacy_engine = _tool_engine(legacy_executor, eager_dispatch=False)
    legacy_result = await legacy_engine.run(graph)

    monkeypatch.setenv("DAN_EAGER_DISPATCH", "1")
    eager_executor = FakeToolExecutor()
    eager_engine = _tool_engine(eager_executor, eager_dispatch=False)
    eager_result = await eager_engine.run(graph)

    assert legacy_result.success is True
    assert eager_result.success is True
    assert legacy_result.outputs == eager_result.outputs
    assert eager_executor.starts["after_fast"] < eager_executor.ends["slow"]
    assert legacy_executor.starts["after_fast"] > legacy_executor.ends["slow"]


@pytest.mark.asyncio
async def test_wide_fanout_dispatches_all_children_before_join() -> None:
    executor = FakeToolExecutor()
    engine = _tool_engine(executor, eager_dispatch=True)

    fanout_nodes = [
        _tool_node(f"b{i}", input_ports=[_in_port("inp")], delay=0.04, value=f"b{i}")
        for i in range(10)
    ]
    join = _tool_node(
        "join",
        input_ports=[_in_port(f"in{i}") for i in range(10)],
        delay=0.01,
        value="join",
    )
    graph = Graph(
        nodes=[_tool_node("root", delay=0.01, value="root"), *fanout_nodes, join],
        edges=[
            *[
                _edge(f"e{i}", "root", "out", f"b{i}", "inp")
                for i in range(10)
            ],
            *[
                _edge(f"j{i}", f"b{i}", "out", "join", f"in{i}")
                for i in range(10)
            ],
        ],
        entry_points=["root"],
        exit_points=["join"],
    )

    result = await engine.run(graph)

    assert result.success is True
    assert executor.max_active == 10
    assert executor.starts["join"] >= max(executor.ends[f"b{i}"] for i in range(10))


@pytest.mark.asyncio
async def test_crash_resume_checkpoint_matches_clean_run() -> None:
    checkpoint_store = RecordingCheckpointStore()

    interrupted_executor = FakeToolExecutor()
    interrupted_engine = _tool_engine(
        interrupted_executor,
        eager_dispatch=True,
        checkpoint_store=checkpoint_store,
        checkpoint_batch_size=1,
    )
    interrupted_graph = _chain_graph(
        10,
        delay_overrides={"n5": 1.0},
    )

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(interrupted_engine.run(interrupted_graph), timeout=0.25)
    await asyncio.sleep(0)

    expected_completed = [f"n{i}" for i in range(5)]
    checkpoint = next(
        payload
        for payload in checkpoint_store.saves
        if payload.get("checkpoint_data", {}).get("completed_node_ids") == expected_completed
    )
    run_id = checkpoint["state"]["run_id"]
    checkpoint_store._latest[run_id] = checkpoint

    resumed_executor = FakeToolExecutor()
    resumed_engine = _tool_engine(
        resumed_executor,
        eager_dispatch=True,
        checkpoint_store=checkpoint_store,
        checkpoint_batch_size=1,
    )
    resumed_result = await resumed_engine.resume(_chain_graph(10), run_id)

    clean_executor = FakeToolExecutor()
    clean_engine = _tool_engine(clean_executor, eager_dispatch=True)
    clean_result = await clean_engine.run(_chain_graph(10))

    assert resumed_result.success is True
    assert clean_result.success is True
    assert resumed_result.outputs == clean_result.outputs
    assert resumed_result.node_statuses == clean_result.node_statuses
    assert resumed_executor.calls == [f"n{i}" for i in range(5, 10)]


@pytest.mark.asyncio
@pytest.mark.slow
async def test_eager_dispatch_latency_benchmark_beats_level_sync() -> None:
    graph = Graph(
        metadata=GraphMetadata(name="latency-benchmark"),
        nodes=[
            _tool_node("root", value="root"),
            _tool_node("slow", input_ports=[_in_port("inp")], delay=0.3),
            _tool_node("fast1", input_ports=[_in_port("inp")], delay=0.06),
            _tool_node("fast2", input_ports=[_in_port("inp")], delay=0.06),
            _tool_node("fast3", input_ports=[_in_port("inp")], delay=0.06),
        ],
        edges=[
            _edge("e1", "root", "out", "slow", "inp"),
            _edge("e2", "root", "out", "fast1", "inp"),
            _edge("e3", "fast1", "out", "fast2", "inp"),
            _edge("e4", "fast2", "out", "fast3", "inp"),
        ],
        entry_points=["root"],
        exit_points=["slow", "fast3"],
    )

    eager_result, eager_elapsed = await _measure_run_duration(
        _tool_engine(FakeToolExecutor(), eager_dispatch=True),
        graph,
    )
    legacy_result, legacy_elapsed = await _measure_run_duration(
        _tool_engine(FakeToolExecutor(), eager_dispatch=False),
        graph,
    )

    assert eager_result.success is True
    assert legacy_result.success is True
    assert eager_elapsed <= legacy_elapsed * 0.8


@pytest.mark.asyncio
@pytest.mark.slow
async def test_eager_dispatch_throughput_stays_close_to_theoretical_limit() -> None:
    max_concurrency = 10
    delay = 0.05
    node_count = 50
    executor = FakeToolExecutor()
    engine = _tool_engine(
        executor,
        eager_dispatch=True,
        max_concurrency=max_concurrency,
    )
    graph = Graph(
        nodes=[_tool_node(f"n{i}", delay=delay, value=f"n{i}") for i in range(node_count)],
        entry_points=[f"n{i}" for i in range(node_count)],
        exit_points=[f"n{i}" for i in range(node_count)],
    )

    result, elapsed = await _measure_run_duration(engine, graph)
    theoretical = math.ceil(node_count / max_concurrency) * delay

    assert result.success is True
    assert theoretical / elapsed >= 0.8


@pytest.mark.asyncio
@pytest.mark.slow
async def test_checkpoint_batching_overhead_stays_below_threshold() -> None:
    graph = _chain_graph(20, default_delay=0.03)

    baseline_result, baseline_elapsed = await _measure_run_duration(
        _tool_engine(FakeToolExecutor(), eager_dispatch=True),
        graph,
    )
    checkpointed_result, checkpointed_elapsed = await _measure_run_duration(
        _tool_engine(
            FakeToolExecutor(),
            eager_dispatch=True,
            checkpoint_store=RecordingCheckpointStore(),
            checkpoint_batch_size=5,
        ),
        graph,
    )

    assert baseline_result.success is True
    assert checkpointed_result.success is True
    assert (checkpointed_elapsed - baseline_elapsed) / baseline_elapsed <= 0.15


@pytest.mark.asyncio
@pytest.mark.slow
async def test_repeated_large_graph_runs_do_not_accumulate_traced_memory() -> None:
    graph = _chain_graph(100, default_delay=0.001)
    engine = _tool_engine(
        FakeToolExecutor(),
        eager_dispatch=True,
        max_concurrency=10,
    )

    tracemalloc.start()
    retained: list[int] = []
    try:
        for _ in range(3):
            result = await engine.run(graph)
            assert result.success is True
            del result
            gc.collect()
            current, _peak = tracemalloc.get_traced_memory()
            retained.append(current)
    finally:
        tracemalloc.stop()

    assert retained[1] <= retained[0] * 1.2
    assert retained[2] <= retained[0] * 1.25
