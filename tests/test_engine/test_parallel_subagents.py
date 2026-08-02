"""Unit tests for ParallelSubagentsExecutor."""

import asyncio

import pytest

from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
from dan.engine.executor import EngineConfig, ExecutionContext, NodeResult
from dan.engine.state import ExecutionState, NodeStatus
from dan.executors.control_flow import ParallelSubagentsExecutor
from dan.models.context import FailurePolicy, MergeStrategy
from dan.models.control_flow import ParallelSubagentsNode
from dan.models.graph import Graph
from dan.models.ports import InputPort, OutputPort


def _make_node(
    branch_graphs: list[str],
    *,
    parallelism: int = 10,
    merge_strategy: MergeStrategy = MergeStrategy.APPEND,
    reducer: str | None = None,
    input_mappings: dict[str, str] | None = None,
    branch_inputs: dict[str, dict] | None = None,
    failure_policy: FailurePolicy | None = None,
) -> ParallelSubagentsNode:
    return ParallelSubagentsNode(
        id="par",
        name="Parallel",
        branch_graphs=branch_graphs,
        parallelism=parallelism,
        merge_strategy=merge_strategy,
        reducer=reducer,
        input_mappings=input_mappings or {},
        branch_inputs=branch_inputs or {},
        failure_policy=failure_policy or FailurePolicy(),
        input_ports=[InputPort(name="input")],
        output_ports=[OutputPort(name="results")],
    )


def _make_context(
    subgraph_returns: dict[str, dict] | None = None,
    *,
    subgraph_delay: float = 0,
    subgraph_errors: dict[str, Exception] | None = None,
) -> tuple[ExecutionContext, list[dict]]:
    """Build a minimal ExecutionContext with mocked run_subgraph and event capture."""
    graph = Graph(nodes=[], edges=[], entry_points=[], exit_points=[])
    state = ExecutionState(graph)
    returns = subgraph_returns or {}
    errors = subgraph_errors or {}
    events: list[dict] = []

    async def mock_run_subgraph(
        key: str,
        inputs: dict,
        parent_node_id: str | None = None,
        targeted_inputs: dict | None = None,
    ) -> dict:
        if subgraph_delay > 0:
            await asyncio.sleep(subgraph_delay)
        if key in errors:
            raise errors[key]
        return returns.get(key, inputs)

    async def mock_emit_event(event: object) -> None:
        events.append(event)

    ctx = ExecutionContext(
        state=state,
        config=EngineConfig(checkpoint_enabled=False),
        shared_context=SharedContextStore([]),
        artifacts=ArtifactStore(),
        local_state=LocalStateManager(),
        run_subgraph=mock_run_subgraph,
        event_callback=mock_emit_event,
        run_id="test-run",
    )
    return ctx, events


EXECUTOR = ParallelSubagentsExecutor()


class TestParallelSubagentsExecutor:
    """Core executor behavior: launch, merge, events."""

    @pytest.mark.asyncio
    async def test_append_merge_gathers_results(self):
        """Executor launches N branches, gathers results, applies APPEND merge."""
        node = _make_node(["team_a", "team_b", "team_c"])
        ctx, events = _make_context(
            {
                "team_a": {"summary": "A done"},
                "team_b": {"summary": "B done"},
                "team_c": {"summary": "C done"},
            }
        )

        result = await EXECUTOR.execute(node, {"input": "hello"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert isinstance(result.outputs["results"], list)
        assert len(result.outputs["results"]) == 3
        summaries = [r["summary"] for r in result.outputs["results"]]
        assert set(summaries) == {"A done", "B done", "C done"}

        assert result.metadata["branch_count"] == 3
        assert result.metadata["succeeded"] == 3
        assert result.metadata["failed"] == 0

    @pytest.mark.asyncio
    async def test_last_write_wins_merge(self):
        """LAST_WRITE_WINS: last branch's keys overwrite earlier."""
        node = _make_node(
            ["b1", "b2"],
            merge_strategy=MergeStrategy.LAST_WRITE_WINS,
        )
        ctx, _ = _make_context(
            {
                "b1": {"key": "from_b1", "only_b1": True},
                "b2": {"key": "from_b2", "only_b2": True},
            }
        )

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        merged = result.outputs["results"]
        assert isinstance(merged, dict)
        assert merged["key"] == "from_b2"
        assert merged["only_b1"] is True
        assert merged["only_b2"] is True

    @pytest.mark.asyncio
    async def test_reducer_merge(self):
        """REDUCER: expression evaluated over branch outputs."""
        node = _make_node(
            ["b1", "b2"],
            merge_strategy=MergeStrategy.REDUCER,
            reducer="len(inputs)",
        )
        ctx, _ = _make_context(
            {
                "b1": {"val": 10},
                "b2": {"val": 20},
            }
        )

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["results"] == 2

    @pytest.mark.asyncio
    async def test_all_branches_fail(self):
        """All branches fail -> FAILED status with error."""
        node = _make_node(["b1", "b2"])
        ctx, _ = _make_context(
            subgraph_errors={
                "b1": RuntimeError("b1 exploded"),
                "b2": ValueError("b2 exploded"),
            }
        )

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.FAILED
        assert "All parallel subagent branches failed" in result.error
        assert "b1" in result.error
        assert "b2" in result.error

    @pytest.mark.asyncio
    async def test_partial_failure_returns_succeeded(self):
        """Partial failure: still returns succeeded results."""
        node = _make_node(["ok", "fail"])
        ctx, _ = _make_context(
            subgraph_returns={"ok": {"result": "good"}},
            subgraph_errors={"fail": RuntimeError("boom")},
        )

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert len(result.outputs["results"]) == 1
        assert result.outputs["results"][0]["result"] == "good"
        assert result.metadata["succeeded"] == 1
        assert result.metadata["failed"] == 1

    @pytest.mark.asyncio
    async def test_timeout_via_failure_policy(self):
        """Timeout via failure_policy.timeout_seconds."""
        node = _make_node(
            ["slow"],
            failure_policy=FailurePolicy(timeout_seconds=0.05),
        )
        ctx, _ = _make_context(
            subgraph_returns={"slow": {"done": True}},
            subgraph_delay=1.0,
        )

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.FAILED
        assert "timed out" in result.error

    @pytest.mark.asyncio
    async def test_events_emitted(self):
        """Events: PARALLEL_BRANCH_STARTED, PARALLEL_BRANCH_COMPLETED, PARALLEL_FAN_IN_COMPLETED."""
        node = _make_node(["alpha", "beta"])
        ctx, events = _make_context(
            {"alpha": {"a": 1}, "beta": {"b": 2}}
        )

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.COMPLETED

        event_types = [e.event_type.value for e in events]
        assert event_types.count("parallel_branch_started") == 2
        assert event_types.count("parallel_branch_completed") == 2
        assert event_types.count("parallel_fan_in_completed") == 1

        started_events = [e for e in events if e.event_type.value == "parallel_branch_started"]
        branch_keys = {e.data["branch_key"] for e in started_events}
        assert branch_keys == {"alpha", "beta"}

        fan_in = [e for e in events if e.event_type.value == "parallel_fan_in_completed"][0]
        assert fan_in.data["branch_count"] == 2
        assert fan_in.data["succeeded"] == 2

    @pytest.mark.asyncio
    async def test_empty_branch_graphs_error(self):
        """Empty branch_graphs -> FAILED with error."""
        node = _make_node([])
        ctx, _ = _make_context()

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.FAILED
        assert "no branches" in result.error


class TestParallelSubagentsInputMapping:
    """Input routing via input_mappings and branch_inputs."""

    @pytest.mark.asyncio
    async def test_input_mappings_applied(self):
        """input_mappings routes outer ports to inner ports."""
        node = _make_node(
            ["b1"],
            input_mappings={"input": "query"},
        )
        captured = {}

        async def capture_subgraph(key, inputs, parent_node_id=None, targeted_inputs=None):
            captured.update(inputs)
            return {"result": "ok"}

        ctx, _ = _make_context()
        ctx._run_subgraph = capture_subgraph

        await EXECUTOR.execute(node, {"input": "hello world"}, ctx)

        assert captured["query"] == "hello world"

    @pytest.mark.asyncio
    async def test_branch_inputs_override(self):
        """branch_inputs provides per-branch overrides."""
        node = _make_node(
            ["b1", "b2"],
            input_mappings={"input": "query"},
            branch_inputs={"b2": {"extra": "special"}},
        )
        captured_per_branch: dict[str, dict] = {}

        async def capture_subgraph(key, inputs, parent_node_id=None, targeted_inputs=None):
            captured_per_branch[key] = dict(inputs)
            return {"result": key}

        ctx, _ = _make_context()
        ctx._run_subgraph = capture_subgraph

        await EXECUTOR.execute(node, {"input": "shared"}, ctx)

        assert captured_per_branch["b1"]["query"] == "shared"
        assert "extra" not in captured_per_branch["b1"]
        assert captured_per_branch["b2"]["query"] == "shared"
        assert captured_per_branch["b2"]["extra"] == "special"
