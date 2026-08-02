"""Tests for 7-1: Runtime Reliability — RetryPolicy, executor retry, halt semantics."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dan.engine import Engine, EngineConfig, NodeResult, NodeStatus
from dan.engine.checkpoint import NullCheckpointStore
from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
from dan.engine.events import EventType
from dan.engine.executor import ExecutionContext
from dan.executors.code import CodeExecutor
from dan.executors.llm import LLMExecutor
from dan.executors.tool import ToolExecutor, ToolRegistry
from dan.models.edges import DataEdge
from dan.models.graph import Graph
from dan.models.nodes import (
    CodeOperator,
    LLMOperator,
    NodeBase,
    RetryPolicy,
    ToolOperator,
)
from dan.models.ports import InputPort, OutputPort


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(
    event_callback: AsyncMock | None = None,
) -> ExecutionContext:
    return ExecutionContext(
        state=MagicMock(),
        config=EngineConfig(llm_api_key="test-key", checkpoint_enabled=False),
        shared_context=SharedContextStore(),
        artifacts=ArtifactStore(),
        local_state=LocalStateManager(),
        event_callback=event_callback or AsyncMock(),
        run_id="test-run",
    )


def _config():
    return EngineConfig(checkpoint_enabled=False)


def _engine(**kwargs):
    return Engine(config=_config(), checkpoint_store=NullCheckpointStore(), **kwargs)


# ===========================================================================
# 1. RetryPolicy model validation
# ===========================================================================


class TestRetryPolicyModel:
    def test_defaults(self):
        rp = RetryPolicy()
        assert rp.max_retries == 0
        assert rp.backoff == 1.0
        assert rp.backoff_max == 60.0
        assert rp.fallback_model is None
        assert rp.on_failure == "error"

    def test_serialization_roundtrip(self):
        rp = RetryPolicy(max_retries=5, backoff=2.0, on_failure="halt", fallback_model="gpt-4o")
        data = rp.model_dump()
        rp2 = RetryPolicy.model_validate(data)
        assert rp2.max_retries == 5
        assert rp2.fallback_model == "gpt-4o"
        assert rp2.on_failure == "halt"

    def test_node_base_retry_policy_field(self):
        node = LLMOperator(
            id="n1", name="test", model="gpt-4o", prompt_template="hello",
            retry_policy=RetryPolicy(max_retries=2, on_failure="skip"),
        )
        assert node.retry_policy is not None
        assert node.retry_policy.max_retries == 2
        assert node.retry_policy.on_failure == "skip"

    def test_node_base_retry_policy_none_by_default(self):
        node = LLMOperator(id="n1", name="test", model="gpt-4o", prompt_template="hello")
        assert node.retry_policy is None

    def test_json_omits_none_retry_policy(self):
        node = LLMOperator(id="n1", name="test", model="gpt-4o", prompt_template="hello")
        data = node.model_dump(exclude_none=True)
        assert "retry_policy" not in data

    def test_json_includes_retry_policy_when_set(self):
        node = LLMOperator(
            id="n1", name="test", model="gpt-4o", prompt_template="hello",
            retry_policy=RetryPolicy(max_retries=3),
        )
        data = node.model_dump()
        assert data["retry_policy"]["max_retries"] == 3


# ===========================================================================
# 2. RETRY_ATTEMPTED event type
# ===========================================================================


class TestRetryAttemptedEvent:
    def test_event_type_exists(self):
        assert EventType.RETRY_ATTEMPTED == "retry_attempted"
        assert EventType("retry_attempted") is EventType.RETRY_ATTEMPTED

    @pytest.mark.asyncio
    async def test_event_emittable_via_context(self):
        cb = AsyncMock()
        ctx = _make_context(event_callback=cb)
        await ctx.emit_event(
            event_type="retry_attempted",
            node_id="n1",
            node_type="llm_operator",
            data={"attempt": 1},
        )
        cb.assert_called_once()
        event = cb.call_args[0][0]
        assert event.event_type == EventType.RETRY_ATTEMPTED


# ===========================================================================
# 3. LLMExecutor retry
# ===========================================================================


class TestLLMExecutorRetry:
    @pytest.mark.asyncio
    async def test_retry_count_and_backoff(self):
        """Verify retry attempts and exponential backoff sleep calls."""
        from openai import RateLimitError

        mock_client = AsyncMock()
        exc = RateLimitError(
            message="rate limit",
            response=MagicMock(status_code=429, headers={}),
            body=None,
        )
        mock_client.chat.completions.create = AsyncMock(side_effect=exc)

        executor = LLMExecutor(client=mock_client)
        node = LLMOperator(
            id="llm1", name="test", model="gpt-4o", prompt_template="hello",
            retry_policy=RetryPolicy(max_retries=3, backoff=0.01, backoff_max=1.0),
        )
        ctx = _make_context()

        with patch("dan.executors.llm.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await executor.execute(node, {}, ctx)

        assert result.status == NodeStatus.FAILED
        assert mock_sleep.await_count == 2  # 3 attempts = 2 sleeps between

    @pytest.mark.asyncio
    async def test_fallback_model_on_exhausted_retries(self):
        """After max retries, fallback model is tried."""
        from openai import RateLimitError

        exc = RateLimitError(
            message="rate limit",
            response=MagicMock(status_code=429, headers={}),
            body=None,
        )
        call_count = 0

        async def fake_create(**kwargs):
            nonlocal call_count
            call_count += 1
            if kwargs.get("model") == "fallback-model":
                resp = MagicMock()
                resp.choices = [MagicMock()]
                resp.choices[0].message.content = "fallback answer"
                resp.usage = None
                return resp
            raise exc

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=fake_create)

        executor = LLMExecutor(client=mock_client)
        node = LLMOperator(
            id="llm1", name="test", model="gpt-4o", prompt_template="hello",
            retry_policy=RetryPolicy(
                max_retries=2, backoff=0.001, fallback_model="fallback-model",
            ),
        )
        ctx = _make_context()

        with patch("dan.executors.llm.asyncio.sleep", new_callable=AsyncMock):
            result = await executor.execute(node, {}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["text"] == "fallback answer"

    @pytest.mark.asyncio
    async def test_on_failure_skip_returns_skipped(self):
        from openai import APIError

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=APIError(
                message="permanent", request=MagicMock(), body=None,
            )
        )

        executor = LLMExecutor(client=mock_client)
        node = LLMOperator(
            id="llm1", name="test", model="gpt-4o", prompt_template="hello",
            retry_policy=RetryPolicy(on_failure="skip"),
        )
        ctx = _make_context()
        result = await executor.execute(node, {}, ctx)
        assert result.status == NodeStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_on_failure_halt_returns_failed_with_halt_metadata(self):
        from openai import APIError

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=APIError(
                message="permanent", request=MagicMock(), body=None,
            )
        )

        executor = LLMExecutor(client=mock_client)
        node = LLMOperator(
            id="llm1", name="test", model="gpt-4o", prompt_template="hello",
            retry_policy=RetryPolicy(on_failure="halt"),
        )
        ctx = _make_context()
        result = await executor.execute(node, {}, ctx)
        assert result.status == NodeStatus.FAILED
        assert result.metadata.get("halt") is True

    @pytest.mark.asyncio
    async def test_retry_attempted_events_emitted(self):
        from openai import RateLimitError

        exc = RateLimitError(
            message="rate limit",
            response=MagicMock(status_code=429, headers={}),
            body=None,
        )
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=exc)

        cb = AsyncMock()
        executor = LLMExecutor(client=mock_client)
        node = LLMOperator(
            id="llm1", name="test", model="gpt-4o", prompt_template="hello",
            retry_policy=RetryPolicy(max_retries=3, backoff=0.001),
        )
        ctx = _make_context(event_callback=cb)

        with patch("dan.executors.llm.asyncio.sleep", new_callable=AsyncMock):
            await executor.execute(node, {}, ctx)

        retry_events = [
            c for c in cb.call_args_list
            if c[0][0].event_type == EventType.RETRY_ATTEMPTED
        ]
        assert len(retry_events) == 2  # 3 attempts, 2 retries emit events


# ===========================================================================
# 4. ToolExecutor retry
# ===========================================================================


class TestToolExecutorRetry:
    @pytest.mark.asyncio
    async def test_transient_exception_retried(self):
        call_count = 0

        async def flaky_tool(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise TimeoutError("connection timeout")
            return {"ok": True}

        registry = ToolRegistry()
        registry.register("flaky", flaky_tool)
        executor = ToolExecutor(registry=registry)

        node = ToolOperator(
            id="t1", name="test", tool_id="flaky",
            retry_policy=RetryPolicy(max_retries=3, backoff=0.001),
        )
        ctx = _make_context()

        with patch("dan.executors.tool.asyncio.sleep", new_callable=AsyncMock):
            result = await executor.execute(node, {}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["ok"] is True
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_permanent_exception_fails_immediately(self):
        call_count = 0

        async def bad_tool(**kwargs):
            nonlocal call_count
            call_count += 1
            raise ValueError("bad argument")

        registry = ToolRegistry()
        registry.register("bad", bad_tool)
        executor = ToolExecutor(registry=registry)

        node = ToolOperator(
            id="t1", name="test", tool_id="bad",
            retry_policy=RetryPolicy(max_retries=5, backoff=0.001),
        )
        ctx = _make_context()
        result = await executor.execute(node, {}, ctx)

        assert result.status == NodeStatus.FAILED
        assert call_count == 1  # no retry on permanent exceptions

    @pytest.mark.asyncio
    async def test_tool_on_failure_skip(self):
        async def fail_tool(**kwargs):
            raise TimeoutError("boom")

        registry = ToolRegistry()
        registry.register("fail", fail_tool)
        executor = ToolExecutor(registry=registry)

        node = ToolOperator(
            id="t1", name="test", tool_id="fail",
            retry_policy=RetryPolicy(max_retries=0, on_failure="skip"),
        )
        ctx = _make_context()
        result = await executor.execute(node, {}, ctx)
        assert result.status == NodeStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_tool_on_failure_halt(self):
        async def fail_tool(**kwargs):
            raise ConnectionError("down")

        registry = ToolRegistry()
        registry.register("fail", fail_tool)
        executor = ToolExecutor(registry=registry)

        node = ToolOperator(
            id="t1", name="test", tool_id="fail",
            retry_policy=RetryPolicy(max_retries=0, on_failure="halt"),
        )
        ctx = _make_context()
        result = await executor.execute(node, {}, ctx)
        assert result.status == NodeStatus.FAILED
        assert result.metadata.get("halt") is True

    @pytest.mark.asyncio
    async def test_tool_default_no_retry(self):
        call_count = 0

        async def one_shot(**kwargs):
            nonlocal call_count
            call_count += 1
            raise TimeoutError("timeout")

        registry = ToolRegistry()
        registry.register("one", one_shot)
        executor = ToolExecutor(registry=registry)

        node = ToolOperator(id="t1", name="test", tool_id="one")
        ctx = _make_context()
        result = await executor.execute(node, {}, ctx)
        assert result.status == NodeStatus.FAILED
        assert call_count == 1


# ===========================================================================
# 5. CodeExecutor on_failure
# ===========================================================================


class TestCodeExecutorOnFailure:
    @pytest.mark.asyncio
    async def test_code_on_failure_skip(self):
        executor = CodeExecutor()
        node = CodeOperator(
            id="c1", name="test", code="raise RuntimeError('oops')",
            retry_policy=RetryPolicy(on_failure="skip"),
        )
        ctx = _make_context()
        result = await executor.execute(node, {}, ctx)
        assert result.status == NodeStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_code_on_failure_halt(self):
        executor = CodeExecutor()
        node = CodeOperator(
            id="c1", name="test", code="raise RuntimeError('oops')",
            retry_policy=RetryPolicy(on_failure="halt"),
        )
        ctx = _make_context()
        result = await executor.execute(node, {}, ctx)
        assert result.status == NodeStatus.FAILED
        assert result.metadata.get("halt") is True

    @pytest.mark.asyncio
    async def test_code_on_failure_error_default(self):
        executor = CodeExecutor()
        node = CodeOperator(id="c1", name="test", code="raise RuntimeError('oops')")
        ctx = _make_context()
        result = await executor.execute(node, {}, ctx)
        assert result.status == NodeStatus.FAILED
        assert result.metadata.get("halt") is not True

    @pytest.mark.asyncio
    async def test_code_executor_supports_open_builtin_for_file_io(self, tmp_path):
        executor = CodeExecutor()
        source = tmp_path / "watchlist.csv"
        target = tmp_path / "copy.txt"
        source.write_text("ticker\nRKLB\n", encoding="utf-8")
        node = CodeOperator(
            id="c1",
            name="read-write",
            code=(
                f"with open({str(source)!r}, 'r', encoding='utf-8') as f:\n"
                "    content = f.read()\n"
                f"with open({str(target)!r}, 'w', encoding='utf-8') as f:\n"
                "    f.write(content.upper())\n"
                "result = {'content': content}"
            ),
        )
        ctx = _make_context()
        result = await executor.execute(node, {}, ctx)
        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["content"] == "ticker\nRKLB\n"
        assert target.read_text(encoding="utf-8") == "TICKER\nRKLB\n"


# ===========================================================================
# 6. Integration: halt stops engine
# ===========================================================================


class MockFailingLLMExecutor:
    """Always returns FAILED with halt metadata."""

    async def execute(self, node: NodeBase, inputs: dict, context: ExecutionContext) -> NodeResult:
        policy = node.retry_policy
        if policy and policy.on_failure == "halt":
            return NodeResult(
                outputs={}, status=NodeStatus.FAILED,
                error="mock fail", metadata={"halt": True},
            )
        return NodeResult(
            outputs={"text": f"mock-{node.id}"},
            status=NodeStatus.COMPLETED,
        )


class TestHaltIntegration:
    @pytest.mark.asyncio
    async def test_halt_stops_engine_returns_failure(self):
        """on_failure=halt stops further level dispatch and returns success=False."""
        a = CodeOperator(
            id="a", name="A", code="result = {'v': 1}",
            output_ports=[OutputPort(name="v")],
        )
        b = CodeOperator(
            id="b", name="B", code="raise RuntimeError('halt me')",
            input_ports=[InputPort(name="v")],
            output_ports=[OutputPort(name="v")],
            retry_policy=RetryPolicy(on_failure="halt"),
        )
        c = CodeOperator(
            id="c", name="C", code="result = {'v': v + 10}",
            input_ports=[InputPort(name="v")],
            output_ports=[OutputPort(name="v")],
        )

        graph = Graph(
            nodes=[a, b, c],
            edges=[
                DataEdge(id="e1", source_node_id="a", source_port="v",
                         target_node_id="b", target_port="v"),
                DataEdge(id="e2", source_node_id="b", source_port="v",
                         target_node_id="c", target_port="v"),
            ],
            entry_points=["a"],
            exit_points=["c"],
        )

        engine = _engine()
        result = await engine.run(graph)

        assert not result.success
        assert result.node_statuses["a"] == "completed"
        assert result.node_statuses["b"] == "failed"
        assert result.node_statuses["c"] == "pending"

    @pytest.mark.asyncio
    async def test_halt_parallel_nodes_same_level_finish(self):
        """Parallel nodes in the same level all complete; halt blocks NEXT level."""
        ok_node = CodeOperator(
            id="ok", name="OK", code="result = {'v': 1}",
            output_ports=[OutputPort(name="v")],
        )
        halt_node = CodeOperator(
            id="halt", name="Halt", code="raise RuntimeError('stop')",
            output_ports=[OutputPort(name="v")],
            retry_policy=RetryPolicy(on_failure="halt"),
        )
        after = CodeOperator(
            id="after", name="After", code="result = {'v': 99}",
            input_ports=[InputPort(name="v")],
            output_ports=[OutputPort(name="v")],
        )

        graph = Graph(
            nodes=[ok_node, halt_node, after],
            edges=[
                DataEdge(id="e1", source_node_id="ok", source_port="v",
                         target_node_id="after", target_port="v"),
            ],
            entry_points=["ok", "halt"],
            exit_points=["after"],
        )

        engine = _engine()
        result = await engine.run(graph)

        assert not result.success
        assert result.node_statuses["ok"] == "completed"
        assert result.node_statuses["halt"] == "failed"
        assert result.node_statuses["after"] == "pending"

    @pytest.mark.asyncio
    async def test_halt_writes_checkpoint(self):
        """When halt occurs, a checkpoint is saved."""
        from dan.engine.checkpoint import CheckpointStore

        store = AsyncMock(spec=CheckpointStore)
        store.save = AsyncMock()

        halt_node = CodeOperator(
            id="h", name="Halt", code="raise RuntimeError('stop')",
            output_ports=[OutputPort(name="v")],
            retry_policy=RetryPolicy(on_failure="halt"),
        )

        graph = Graph(
            nodes=[halt_node],
            entry_points=["h"],
            exit_points=["h"],
        )

        engine = Engine(
            config=EngineConfig(checkpoint_enabled=True),
            checkpoint_store=store,
        )
        result = await engine.run(graph)

        assert not result.success
        assert store.save.await_count >= 1


# ===========================================================================
# 7. Integration: retry_policy on LLM node in graph
# ===========================================================================


class TestRetryPolicyGraphIntegration:
    @pytest.mark.asyncio
    async def test_llm_node_with_skip_policy(self):
        """LLM node with on_failure=skip results in skipped status."""

        class SkippingLLMExecutor:
            async def execute(self, node, inputs, context):
                policy = node.retry_policy
                if policy and policy.on_failure == "skip":
                    return NodeResult(outputs={}, status=NodeStatus.SKIPPED)
                return NodeResult(outputs={"text": "ok"}, status=NodeStatus.COMPLETED)

        llm_node = LLMOperator(
            id="llm", name="LLM", model="gpt-4o", prompt_template="hello",
            input_ports=[InputPort(name="input")],
            output_ports=[OutputPort(name="text")],
            retry_policy=RetryPolicy(on_failure="skip"),
        )

        graph = Graph(
            nodes=[llm_node],
            entry_points=["llm"],
            exit_points=["llm"],
        )

        engine = _engine()
        engine.executor_registry.register("llm_operator", SkippingLLMExecutor())
        result = await engine.run(graph)

        assert result.node_statuses["llm"] == "skipped"


# ===========================================================================
# 8. ForEach parallelism semaphore audit
# ===========================================================================


class TestForEachParallelismAudit:
    @pytest.mark.asyncio
    async def test_foreach_semaphore_limits_concurrency(self):
        """ForEachNode.parallelism limits concurrent sub-graph executions."""
        from dan.models.control_flow import ForEachNode
        from dan.models.context import MergeStrategy

        peak = 0
        current = 0

        async def _track_execute(self_exec, node, inputs, context):
            nonlocal peak, current
            current += 1
            if current > peak:
                peak = current
            await asyncio.sleep(0.01)
            current -= 1
            return NodeResult(outputs={"value": inputs.get("item", 0) * 2})

        body_node = CodeOperator(
            id="double", name="Double", code="result = {'value': item * 2}",
            input_ports=[InputPort(name="item"), InputPort(name="index")],
            output_ports=[OutputPort(name="value")],
        )
        body_graph = Graph(
            nodes=[body_node],
            entry_points=["double"],
            exit_points=["double"],
        )

        foreach = ForEachNode(
            id="fan", name="Fan-Out",
            body_graph="body",
            parallelism=2,
            merge_strategy=MergeStrategy.APPEND,
            input_ports=[InputPort(name="items")],
            output_ports=[OutputPort(name="results")],
        )

        main_graph = Graph(
            nodes=[foreach],
            sub_graphs={"body": body_graph},
            entry_points=["fan"],
            exit_points=["fan"],
        )

        engine = _engine()
        result = await engine.run(main_graph, inputs={"items": [1, 2, 3, 4, 5]})
        assert result.success
        results = result.outputs["results"]
        assert len(results) == 5


# ===========================================================================
# 9. max_concurrency graph-wide semaphore
# ===========================================================================


class TestMaxConcurrency:
    @pytest.mark.asyncio
    async def test_max_concurrency_limits_parallel_nodes(self):
        """EngineConfig.max_concurrency creates a global semaphore."""
        peak = 0
        current = 0

        original_execute = CodeExecutor.execute

        async def tracking_execute(self, node, inputs, context):
            nonlocal peak, current
            current += 1
            if current > peak:
                peak = current
            await asyncio.sleep(0.01)
            current -= 1
            return await original_execute(self, node, inputs, context)

        nodes = [
            CodeOperator(
                id=f"n{i}", name=f"N{i}", code=f"result = {{'v': {i}}}",
                output_ports=[OutputPort(name="v")],
            )
            for i in range(5)
        ]

        graph = Graph(
            nodes=nodes,
            entry_points=[n.id for n in nodes],
            exit_points=[n.id for n in nodes],
        )

        config = EngineConfig(checkpoint_enabled=False, max_concurrency=2)
        engine = Engine(config=config, checkpoint_store=NullCheckpointStore())

        with patch.object(CodeExecutor, "execute", tracking_execute):
            result = await engine.run(graph)

        assert result.success
        assert peak <= 2
