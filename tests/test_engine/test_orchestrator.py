"""Unit tests for OrchestratorExecutor."""

import asyncio

import pytest

from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
from dan.engine.executor import EngineConfig, ExecutionContext, NodeResult
from dan.engine.state import ExecutionState, NodeStatus
from dan.executors.control_flow import OrchestratorExecutor
from dan.models.control_flow import OrchestratorNode
from dan.models.graph import Graph
from dan.models.ports import InputPort, OutputPort


def _make_node(
    teams: dict[str, str],
    *,
    completion_condition: str = "all_done",
    max_iterations: int = 100,
    timeout_seconds: float | None = None,
    input_mappings: dict[str, str] | None = None,
    team_inputs: dict[str, dict] | None = None,
    orchestrator_prompt: str = "",
) -> OrchestratorNode:
    return OrchestratorNode(
        id="orch",
        name="Orchestrator",
        teams=teams,
        completion_condition=completion_condition,
        max_iterations=max_iterations,
        timeout_seconds=timeout_seconds,
        input_mappings=input_mappings or {},
        team_inputs=team_inputs or {},
        orchestrator_prompt=orchestrator_prompt,
        input_ports=[InputPort(name="input")],
        output_ports=[OutputPort(name="results")],
    )


def _make_context(
    subgraph_returns: dict[str, dict] | None = None,
    *,
    subgraph_delay: float = 0,
    subgraph_errors: dict[str, Exception] | None = None,
) -> tuple[ExecutionContext, list]:
    """Build a minimal ExecutionContext with mocked run_subgraph and event capture."""
    graph = Graph(nodes=[], edges=[], entry_points=[], exit_points=[])
    state = ExecutionState(graph)
    returns = subgraph_returns or {}
    errors = subgraph_errors or {}
    events: list = []

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


EXECUTOR = OrchestratorExecutor()


class TestOrchestratorBasic:
    """Core executor behavior: launch teams, collect results, emit events."""

    @pytest.mark.asyncio
    async def test_two_teams_both_complete(self):
        """Orchestrator launches 2 teams, both complete, results collected."""
        node = _make_node({"alpha": "sg_alpha", "beta": "sg_beta"})
        ctx, events = _make_context({
            "sg_alpha": {"summary": "Alpha done"},
            "sg_beta": {"summary": "Beta done"},
        })

        result = await EXECUTOR.execute(node, {"input": "hello"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert "results" in result.outputs
        assert result.outputs["results"]["alpha"]["summary"] == "Alpha done"
        assert result.outputs["results"]["beta"]["summary"] == "Beta done"
        assert result.outputs["team_status"]["alpha"] == "completed"
        assert result.outputs["team_status"]["beta"] == "completed"
        assert result.metadata["team_count"] == 2

    @pytest.mark.asyncio
    async def test_empty_teams_fails(self):
        """Empty teams dict -> FAILED status."""
        node = _make_node({})
        ctx, _ = _make_context()

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.FAILED
        assert "no teams" in result.error

    @pytest.mark.asyncio
    async def test_all_teams_fail(self):
        """All teams fail -> FAILED status."""
        node = _make_node({"a": "sg_a", "b": "sg_b"})
        ctx, _ = _make_context(
            subgraph_errors={
                "sg_a": RuntimeError("a exploded"),
                "sg_b": ValueError("b exploded"),
            }
        )

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.FAILED
        assert result.error == "All teams failed"

    @pytest.mark.asyncio
    async def test_partial_failure_succeeds(self):
        """Partial failure: still COMPLETED, captures error for failed team."""
        node = _make_node({"ok": "sg_ok", "fail": "sg_fail"})
        ctx, _ = _make_context(
            subgraph_returns={"sg_ok": {"result": "good"}},
            subgraph_errors={"sg_fail": RuntimeError("boom")},
        )

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["team_status"]["ok"] == "completed"
        assert result.outputs["team_status"]["fail"] == "failed"
        assert "error" in result.outputs["results"]["fail"]


class TestOrchestratorCompletion:
    """Completion conditions: all_done, any_done, timeout."""

    @pytest.mark.asyncio
    async def test_any_done_stops_after_first(self):
        """completion_condition=any_done: stops once first team finishes."""
        node = _make_node(
            {"fast": "sg_fast", "slow": "sg_slow"},
            completion_condition="any_done",
        )

        fast_done = asyncio.Event()
        returns = {"sg_fast": {"result": "fast done"}, "sg_slow": {"result": "slow done"}}

        async def mock_run_subgraph(key, inputs, parent_node_id=None, targeted_inputs=None):
            if key == "sg_fast":
                await asyncio.sleep(0.01)
                fast_done.set()
                return returns[key]
            else:
                await asyncio.sleep(5.0)
                return returns[key]

        ctx, events = _make_context()
        ctx._run_subgraph = mock_run_subgraph

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["team_status"]["fast"] == "completed"

    @pytest.mark.asyncio
    async def test_timeout_cancels_remaining(self):
        """timeout_seconds cancels teams that haven't finished."""
        node = _make_node(
            {"slow": "sg_slow"},
            timeout_seconds=0.05,
        )
        ctx, _ = _make_context(
            subgraph_returns={"sg_slow": {"done": True}},
            subgraph_delay=5.0,
        )

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.outputs["team_status"]["slow"] in ("running", "failed")


class TestOrchestratorEvents:
    """Event routing and orchestrator log."""

    @pytest.mark.asyncio
    async def test_events_emitted(self):
        """Emits PARALLEL_BRANCH_STARTED and PARALLEL_FAN_IN_COMPLETED events."""
        node = _make_node({"t1": "sg_t1", "t2": "sg_t2"})
        ctx, events = _make_context({
            "sg_t1": {"a": 1},
            "sg_t2": {"b": 2},
        })

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.COMPLETED

        event_types = [e.event_type.value for e in events]
        assert event_types.count("parallel_branch_started") == 2
        assert event_types.count("parallel_fan_in_completed") == 1

        started_events = [e for e in events if e.event_type.value == "parallel_branch_started"]
        team_names = {e.data["team_name"] for e in started_events}
        assert team_names == {"t1", "t2"}

    @pytest.mark.asyncio
    async def test_orchestrator_log_captures_events(self):
        """Orchestrator log captures events routed through the event queue."""
        node = _make_node({"team": "sg_team"})
        ctx, events = _make_context({"sg_team": {"result": "done"}})

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert "orchestrator_log" in result.outputs
        assert isinstance(result.outputs["orchestrator_log"], list)

    @pytest.mark.asyncio
    async def test_event_callback_restored_after_execution(self):
        """Original event callback is restored after orchestrator finishes."""
        node = _make_node({"t": "sg_t"})
        ctx, events = _make_context({"sg_t": {"ok": True}})
        original_cb = ctx._event_callback

        await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert ctx._event_callback is original_cb


class TestOrchestratorInputMapping:
    """Input routing via input_mappings and team_inputs."""

    @pytest.mark.asyncio
    async def test_input_mappings_applied(self):
        """input_mappings routes outer ports to inner ports."""
        node = _make_node(
            {"t": "sg_t"},
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
    async def test_team_inputs_override(self):
        """team_inputs provides per-team overrides."""
        node = _make_node(
            {"t1": "sg_t1", "t2": "sg_t2"},
            input_mappings={"input": "query"},
            team_inputs={"t2": {"extra": "special"}},
        )
        captured_per_team: dict[str, dict] = {}

        async def capture_subgraph(key, inputs, parent_node_id=None, targeted_inputs=None):
            captured_per_team[key] = dict(inputs)
            return {"result": key}

        ctx, _ = _make_context()
        ctx._run_subgraph = capture_subgraph

        await EXECUTOR.execute(node, {"input": "shared"}, ctx)

        assert captured_per_team["sg_t1"]["query"] == "shared"
        assert "extra" not in captured_per_team["sg_t1"]
        assert captured_per_team["sg_t2"]["query"] == "shared"
        assert captured_per_team["sg_t2"]["extra"] == "special"
