"""Tests for LLM-driven OrchestratorExecutor (Plan 16-5)."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
from dan.engine.executor import EngineConfig, ExecutionContext
from dan.engine.state import ExecutionState, NodeStatus
from dan.executors.control_flow import OrchestratorExecutor
from dan.models.control_flow import DynamicExpansionSpec, OrchestratorNode
from dan.models.graph import Graph
from dan.models.ports import InputPort, OutputPort
from dan.providers import CompletionResult
from dan.providers.registry import ProviderRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(
    teams: dict[str, str],
    *,
    orchestrator_prompt: str = "",
    orchestrator_model: str | None = None,
    completion_condition: str = "all_done",
    max_iterations: int = 100,
    max_llm_calls: int = 50,
    timeout_seconds: float | None = None,
    input_mappings: dict[str, str] | None = None,
    team_inputs: dict[str, dict] | None = None,
    team_expansions: dict[str, DynamicExpansionSpec] | None = None,
) -> OrchestratorNode:
    return OrchestratorNode(
        id="orch",
        name="Orchestrator",
        teams=teams,
        orchestrator_prompt=orchestrator_prompt,
        orchestrator_model=orchestrator_model,
        completion_condition=completion_condition,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        timeout_seconds=timeout_seconds,
        input_mappings=input_mappings or {},
        team_inputs=team_inputs or {},
        team_expansions=team_expansions or {},
        input_ports=[InputPort(name="input")],
        output_ports=[OutputPort(name="results")],
    )


class MockProvider:
    """Mock LLM provider returning a sequence of predefined CompletionResults."""

    def __init__(self, responses: list[CompletionResult]) -> None:
        self._responses = list(responses)
        self._call_count = 0
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        self.calls.append({
            "messages": messages,
            "model": model,
            "kwargs": kwargs,
        })
        idx = min(self._call_count, len(self._responses) - 1)
        self._call_count += 1
        return self._responses[idx]

    async def stream(self, *args: Any, **kwargs: Any):
        raise NotImplementedError


def _dispatch_tool_call(team_name: str, inputs: dict | None = None) -> dict:
    return {
        "id": f"call_{team_name}",
        "type": "function",
        "function": {
            "name": "dispatch_to_team",
            "arguments": json.dumps({
                "team_name": team_name,
                "inputs": inputs or {},
            }),
        },
    }


def _halt_tool_call(reason: str = "done", final_result: dict | None = None) -> dict:
    return {
        "id": "call_halt",
        "type": "function",
        "function": {
            "name": "halt_orchestrator",
            "arguments": json.dumps({
                "reason": reason,
                "final_result": final_result or {},
            }),
        },
    }


def _make_context(
    subgraph_returns: dict[str, dict] | None = None,
    *,
    subgraph_delay: float = 0,
    subgraph_errors: dict[str, Exception] | None = None,
    child_workflow_returns: dict[str, dict] | None = None,
    child_workflow_errors: dict[str, Exception] | None = None,
    mock_provider: MockProvider | None = None,
) -> tuple[ExecutionContext, list]:
    graph = Graph(nodes=[], edges=[], entry_points=[], exit_points=[])
    state = ExecutionState(graph)
    returns = subgraph_returns or {}
    errors = subgraph_errors or {}
    child_returns = child_workflow_returns or {}
    child_errors = child_workflow_errors or {}
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

    async def mock_run_child_workflow(
        spec: DynamicExpansionSpec | dict[str, Any] | str,
        inputs: dict[str, Any],
        *,
        parent_node_id: str,
        source: str = "engine",
        boundary_contract: Any | None = None,
    ) -> Any:
        if isinstance(spec, str):
            spec_obj = DynamicExpansionSpec(ref=spec)
        elif isinstance(spec, dict):
            spec_obj = DynamicExpansionSpec(**spec)
        else:
            spec_obj = spec
        if spec_obj.ref in child_errors:
            raise child_errors[spec_obj.ref]
        return {
            "status": "completed",
            "outputs": child_returns.get(spec_obj.ref, inputs),
            "run_id": f"child:{spec_obj.ref}",
        }

    registry = None
    if mock_provider is not None:
        registry = ProviderRegistry()
        registry.register("default", mock_provider)

    ctx = ExecutionContext(
        state=state,
        config=EngineConfig(checkpoint_enabled=False),
        shared_context=SharedContextStore([]),
        artifacts=ArtifactStore(),
        local_state=LocalStateManager(),
        run_subgraph=mock_run_subgraph,
        event_callback=mock_emit_event,
        run_id="test-run",
        provider_registry=registry,
        run_child_workflow=mock_run_child_workflow,
    )
    return ctx, events


EXECUTOR = OrchestratorExecutor()


# ---------------------------------------------------------------------------
# Backward compatibility (static fan-out, no LLM)
# ---------------------------------------------------------------------------


class TestStaticFanout:
    """When no prompt/model is set, orchestrator falls back to static fan-out."""

    @pytest.mark.asyncio
    async def test_no_prompt_no_model_uses_static_fanout(self):
        node = _make_node(
            {"alpha": "sg_alpha", "beta": "sg_beta"},
        )
        ctx, events = _make_context({
            "sg_alpha": {"summary": "Alpha done"},
            "sg_beta": {"summary": "Beta done"},
        })

        result = await EXECUTOR.execute(node, {"input": "hello"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["results"]["alpha"]["summary"] == "Alpha done"
        assert result.outputs["results"]["beta"]["summary"] == "Beta done"
        assert result.outputs["team_status"]["alpha"] == "completed"
        assert result.outputs["team_status"]["beta"] == "completed"

    @pytest.mark.asyncio
    async def test_static_fanout_can_use_child_workflow_expansion(self):
        node = _make_node(
            {"reporter": "sg_reporter"},
            team_expansions={
                "reporter": DynamicExpansionSpec(mode="workflow_ref", ref="wf_reporter"),
            },
        )
        ctx, events = _make_context(
            child_workflow_returns={"wf_reporter": {"summary": "typed child"}},
        )

        result = await EXECUTOR.execute(node, {"input": "hello"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["results"]["reporter"]["summary"] == "typed child"
        branch_started = [
            e for e in events
            if e.event_type.value == "parallel_branch_started"
        ]
        assert branch_started[0].data["dispatch_mode"] == "workflow_ref"

    @pytest.mark.asyncio
    async def test_empty_teams_fails(self):
        node = _make_node({})
        ctx, _ = _make_context()
        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)
        assert result.status == NodeStatus.FAILED
        assert "no teams" in result.error

    @pytest.mark.asyncio
    async def test_static_all_teams_fail(self):
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
    async def test_static_events_emitted(self):
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

    @pytest.mark.asyncio
    async def test_event_callback_restored(self):
        node = _make_node({"t": "sg_t"})
        ctx, _ = _make_context({"sg_t": {"ok": True}})
        original_cb = ctx._event_callback
        await EXECUTOR.execute(node, {"input": "x"}, ctx)
        assert ctx._event_callback is original_cb


# ---------------------------------------------------------------------------
# LLM-driven orchestrator
# ---------------------------------------------------------------------------


class TestLLMDrivenOrchestrator:
    """Core LLM-driven orchestration: dispatch, wait, react, halt."""

    @pytest.mark.asyncio
    async def test_dispatch_team_a_then_team_b_then_halt(self):
        """LLM dispatches Team A, waits for result, dispatches Team B, halts."""
        provider = MockProvider([
            CompletionResult(
                text="Dispatching research team first.",
                tool_calls=[_dispatch_tool_call("research")],
            ),
            CompletionResult(
                text="Research done, now dispatching writer.",
                tool_calls=[_dispatch_tool_call("writer")],
            ),
            CompletionResult(
                text="All work complete.",
                tool_calls=[_halt_tool_call(
                    "All teams finished",
                    {"paper": "final output"},
                )],
            ),
        ])

        node = _make_node(
            {"research": "sg_research", "writer": "sg_writer"},
            orchestrator_prompt="You are a research orchestrator.",
            orchestrator_model="test-model",
        )
        ctx, events = _make_context(
            {
                "sg_research": {"findings": "data collected"},
                "sg_writer": {"draft": "paper written"},
            },
            mock_provider=provider,
        )

        result = await EXECUTOR.execute(node, {"input": "topic"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["results"] == {"paper": "final output"}
        assert result.outputs["halt_reason"] == "All teams finished"
        assert result.outputs["team_results"]["research"]["findings"] == "data collected"
        assert result.outputs["team_results"]["writer"]["draft"] == "paper written"
        assert result.metadata["halted"] is True
        assert result.metadata["llm_calls"] == 3

        assert provider._call_count == 3
        assert "tools" in provider.calls[0]["kwargs"]
        assert len(provider.calls[0]["kwargs"]["tools"]) == 2

    @pytest.mark.asyncio
    async def test_dispatch_multiple_teams_simultaneously(self):
        """LLM dispatches two teams in one call, waits for both, halts."""
        provider = MockProvider([
            CompletionResult(
                text="Dispatching both teams.",
                tool_calls=[
                    _dispatch_tool_call("alpha"),
                    _dispatch_tool_call("beta"),
                ],
            ),
            CompletionResult(
                text="Done.",
                tool_calls=[_halt_tool_call("complete", {"merged": "result"})],
            ),
        ])

        node = _make_node(
            {"alpha": "sg_alpha", "beta": "sg_beta"},
            orchestrator_prompt="Dispatch both teams.",
            orchestrator_model="test-model",
        )
        ctx, events = _make_context(
            {
                "sg_alpha": {"out": "a"},
                "sg_beta": {"out": "b"},
            },
            mock_provider=provider,
        )

        result = await EXECUTOR.execute(node, {"input": "go"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["results"] == {"merged": "result"}
        assert result.outputs["team_status"]["alpha"] == "completed"
        assert result.outputs["team_status"]["beta"] == "completed"
        assert result.metadata["halted"] is True

        branch_started = [
            e for e in events
            if e.event_type.value == "parallel_branch_started"
        ]
        assert len(branch_started) == 2

    @pytest.mark.asyncio
    async def test_dispatch_team_via_child_workflow_expansion(self):
        provider = MockProvider([
            CompletionResult(
                text="Dispatch the typed child workflow.",
                tool_calls=[_dispatch_tool_call("reporter")],
            ),
            CompletionResult(
                text="Done.",
                tool_calls=[_halt_tool_call("complete", {"merged": "result"})],
            ),
        ])

        node = _make_node(
            {"reporter": "sg_reporter"},
            orchestrator_prompt="Dispatch through the engine child workflow contract.",
            orchestrator_model="test-model",
            team_expansions={
                "reporter": DynamicExpansionSpec(mode="workflow_ref", ref="wf_reporter"),
            },
        )
        ctx, events = _make_context(
            child_workflow_returns={"wf_reporter": {"draft": "typed child output"}},
            mock_provider=provider,
        )

        result = await EXECUTOR.execute(node, {"input": "go"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["team_results"]["reporter"]["draft"] == "typed child output"
        branch_started = [
            e for e in events
            if e.event_type.value == "parallel_branch_started"
        ]
        assert branch_started[0].data["dispatch_mode"] == "workflow_ref"
        assert branch_started[0].data["branch_key"] == "wf_reporter"

    @pytest.mark.asyncio
    async def test_child_workflow_team_can_be_redispatched_after_completion(self):
        provider = MockProvider([
            CompletionResult(
                text="Dispatch once.",
                tool_calls=[_dispatch_tool_call("reporter", {"round": 1})],
            ),
            CompletionResult(
                text="Dispatch again after completion.",
                tool_calls=[_dispatch_tool_call("reporter", {"round": 2})],
            ),
            CompletionResult(
                text="Halt.",
                tool_calls=[_halt_tool_call("done", {"ok": True})],
            ),
        ])

        child_calls: list[dict[str, Any]] = []

        async def capture_child_workflow(
            spec: DynamicExpansionSpec | dict[str, Any] | str,
            inputs: dict[str, Any],
            *,
            parent_node_id: str,
            source: str = "engine",
            boundary_contract: Any | None = None,
        ) -> Any:
            spec_obj = spec if isinstance(spec, DynamicExpansionSpec) else DynamicExpansionSpec.model_validate(spec)
            child_calls.append({"ref": spec_obj.ref, "inputs": dict(inputs)})
            return {
                "status": "completed",
                "outputs": {"round": inputs["round"]},
                "run_id": f"child:{len(child_calls)}",
            }

        node = _make_node(
            {"reporter": "sg_reporter"},
            orchestrator_prompt="Redispatch when the child completes.",
            orchestrator_model="test-model",
            team_expansions={
                "reporter": DynamicExpansionSpec(mode="workflow_ref", ref="wf_reporter"),
            },
        )
        ctx, _ = _make_context(mock_provider=provider)
        ctx._run_child_workflow = capture_child_workflow

        result = await EXECUTOR.execute(node, {"input": "go"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert child_calls == [
            {"ref": "wf_reporter", "inputs": {"round": 1}},
            {"ref": "wf_reporter", "inputs": {"round": 2}},
        ]
        assert result.outputs["team_results"]["reporter"]["round"] == 2

    @pytest.mark.asyncio
    async def test_halt_orchestrator_tool_call(self):
        """halt_orchestrator immediately stops the loop and returns result."""
        provider = MockProvider([
            CompletionResult(
                text="Nothing to do.",
                tool_calls=[_halt_tool_call(
                    "no work needed",
                    {"status": "idle"},
                )],
            ),
        ])

        node = _make_node(
            {"t": "sg_t"},
            orchestrator_prompt="Orchestrate.",
            orchestrator_model="test-model",
        )
        ctx, events = _make_context(mock_provider=provider)

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["results"] == {"status": "idle"}
        assert result.outputs["halt_reason"] == "no work needed"
        assert result.metadata["halted"] is True
        assert result.metadata["llm_calls"] == 1

    @pytest.mark.asyncio
    async def test_unknown_team_error_feedback(self):
        """Dispatching an unknown team sends error feedback to LLM."""
        provider = MockProvider([
            CompletionResult(
                text="Dispatch unknown.",
                tool_calls=[_dispatch_tool_call("nonexistent")],
            ),
            CompletionResult(
                text="OK, halting.",
                tool_calls=[_halt_tool_call("gave up", {})],
            ),
        ])

        node = _make_node(
            {"real": "sg_real"},
            orchestrator_prompt="Test.",
            orchestrator_model="test-model",
        )
        ctx, _ = _make_context(mock_provider=provider)

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.metadata["halted"] is True

        second_call_messages = provider.calls[1]["messages"]
        error_msgs = [
            m for m in second_call_messages
            if m["role"] == "user" and "Error:" in m.get("content", "")
        ]
        assert len(error_msgs) >= 1
        assert "nonexistent" in error_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_duplicate_dispatch_error_feedback(self):
        """Dispatching a team that is already running returns error."""
        provider = MockProvider([
            CompletionResult(
                text="Dispatch slow twice.",
                tool_calls=[
                    _dispatch_tool_call("slow"),
                    _dispatch_tool_call("slow"),
                ],
            ),
            CompletionResult(
                text="halting",
                tool_calls=[_halt_tool_call("done", {})],
            ),
        ])

        node = _make_node(
            {"slow": "sg_slow"},
            orchestrator_prompt="Test.",
            orchestrator_model="test-model",
        )
        ctx, _ = _make_context(
            {"sg_slow": {"done": True}},
            subgraph_delay=0.5,
            mock_provider=provider,
        )

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        second_call_messages = provider.calls[1]["messages"]
        error_msgs = [
            m for m in second_call_messages
            if m["role"] == "user" and "already running" in m.get("content", "")
        ]
        assert len(error_msgs) >= 1

    @pytest.mark.asyncio
    async def test_no_tool_calls_nudge(self):
        """LLM returning only text (no tools) gets nudged to use tools."""
        provider = MockProvider([
            CompletionResult(text="I'm thinking...", tool_calls=None),
            CompletionResult(
                text="OK",
                tool_calls=[_halt_tool_call("done", {"answer": 42})],
            ),
        ])

        node = _make_node(
            {"t": "sg_t"},
            orchestrator_prompt="Test.",
            orchestrator_model="test-model",
        )
        ctx, _ = _make_context(mock_provider=provider)

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.metadata["llm_calls"] == 2

        second_call_messages = provider.calls[1]["messages"]
        nudge_msgs = [
            m for m in second_call_messages
            if m["role"] == "user" and "must use tool calls" in m.get("content", "")
        ]
        assert len(nudge_msgs) >= 1


# ---------------------------------------------------------------------------
# Safety bounds
# ---------------------------------------------------------------------------


class TestSafetyBounds:
    """max_llm_calls and max_iterations enforce hard limits."""

    @pytest.mark.asyncio
    async def test_max_llm_calls_stops_loop(self):
        provider = MockProvider([
            CompletionResult(text="thinking", tool_calls=None),
        ] * 10)

        node = _make_node(
            {"t": "sg_t"},
            orchestrator_prompt="Test.",
            orchestrator_model="test-model",
            max_llm_calls=3,
        )
        ctx, _ = _make_context(mock_provider=provider)

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.FAILED
        assert result.metadata["llm_calls"] == 3
        assert "Safety bound" in (result.error or "")

    @pytest.mark.asyncio
    async def test_max_iterations_stops_loop(self):
        provider = MockProvider([
            CompletionResult(text="thinking", tool_calls=None),
        ] * 100)

        node = _make_node(
            {"t": "sg_t"},
            orchestrator_prompt="Test.",
            orchestrator_model="test-model",
            max_iterations=5,
            max_llm_calls=100,
        )
        ctx, _ = _make_context(mock_provider=provider)

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.FAILED
        assert result.metadata["iterations"] == 5
        assert "Safety bound" in (result.error or "")


# ---------------------------------------------------------------------------
# Timeout handling
# ---------------------------------------------------------------------------


class TestTimeout:
    """Timeout stops the orchestrator and cancels running teams."""

    @pytest.mark.asyncio
    async def test_timeout_before_llm_call(self):
        provider = MockProvider([
            CompletionResult(
                text="dispatch",
                tool_calls=[_dispatch_tool_call("slow")],
            ),
            CompletionResult(
                text="still waiting",
                tool_calls=[_dispatch_tool_call("slow")],
            ),
        ])

        node = _make_node(
            {"slow": "sg_slow"},
            orchestrator_prompt="Test.",
            orchestrator_model="test-model",
            timeout_seconds=0.1,
        )
        ctx, _ = _make_context(
            {"sg_slow": {"done": True}},
            subgraph_delay=5.0,
            mock_provider=provider,
        )

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        log_actions = [
            entry.get("action")
            for entry in result.outputs.get("orchestrator_log", [])
            if "action" in entry
        ]
        assert "timeout" in log_actions or "timeout_while_waiting" in log_actions
        assert result.outputs["team_status"]["slow"] != "completed"

    @pytest.mark.asyncio
    async def test_static_fanout_timeout(self):
        node = _make_node(
            {"slow": "sg_slow"},
            timeout_seconds=0.05,
        )
        ctx, _ = _make_context(
            {"sg_slow": {"done": True}},
            subgraph_delay=5.0,
        )

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.outputs["team_status"]["slow"] != "completed"


# ---------------------------------------------------------------------------
# Conversation & tool schema validation
# ---------------------------------------------------------------------------


class TestConversationFlow:
    """Verify the conversation structure sent to the LLM."""

    @pytest.mark.asyncio
    async def test_system_message_included(self):
        provider = MockProvider([
            CompletionResult(
                text="halt",
                tool_calls=[_halt_tool_call("done", {})],
            ),
        ])

        node = _make_node(
            {"t": "sg_t"},
            orchestrator_prompt="You are the boss.",
            orchestrator_model="test-model",
        )
        ctx, _ = _make_context(mock_provider=provider)
        await EXECUTOR.execute(node, {"input": "x"}, ctx)

        first_call = provider.calls[0]["messages"]
        assert first_call[0]["role"] == "system"
        assert first_call[0]["content"] == "You are the boss."

    @pytest.mark.asyncio
    async def test_initial_user_message_contains_teams_and_inputs(self):
        provider = MockProvider([
            CompletionResult(
                text="halt",
                tool_calls=[_halt_tool_call("done", {})],
            ),
        ])

        node = _make_node(
            {"research": "sg_r", "writer": "sg_w"},
            orchestrator_prompt="Go.",
            orchestrator_model="test-model",
        )
        ctx, _ = _make_context(mock_provider=provider)
        await EXECUTOR.execute(node, {"input": "quantum computing"}, ctx)

        first_call = provider.calls[0]["messages"]
        user_msg = first_call[1]["content"]
        assert "research" in user_msg
        assert "writer" in user_msg
        assert "quantum computing" in user_msg

    @pytest.mark.asyncio
    async def test_tool_schemas_passed_to_provider(self):
        provider = MockProvider([
            CompletionResult(
                text="halt",
                tool_calls=[_halt_tool_call("done", {})],
            ),
        ])

        node = _make_node(
            {"t": "sg_t"},
            orchestrator_prompt="Go.",
            orchestrator_model="test-model",
        )
        ctx, _ = _make_context(mock_provider=provider)
        await EXECUTOR.execute(node, {"input": "x"}, ctx)

        tools = provider.calls[0]["kwargs"]["tools"]
        assert len(tools) == 2
        names = {t["function"]["name"] for t in tools}
        assert names == {"dispatch_to_team", "halt_orchestrator"}

    @pytest.mark.asyncio
    async def test_team_completion_events_in_conversation(self):
        """After a team completes, the LLM receives the result as a user message."""
        provider = MockProvider([
            CompletionResult(
                text="dispatch",
                tool_calls=[_dispatch_tool_call("alpha")],
            ),
            CompletionResult(
                text="got it, halting",
                tool_calls=[_halt_tool_call("done", {"final": True})],
            ),
        ])

        node = _make_node(
            {"alpha": "sg_alpha"},
            orchestrator_prompt="Go.",
            orchestrator_model="test-model",
        )
        ctx, _ = _make_context(
            {"sg_alpha": {"data": "result_value"}},
            mock_provider=provider,
        )

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.COMPLETED

        second_call_msgs = provider.calls[1]["messages"]
        team_event_msgs = [
            m for m in second_call_msgs
            if m["role"] == "user" and "alpha" in m.get("content", "").lower()
            and "result_value" in m.get("content", "")
        ]
        assert len(team_event_msgs) >= 1


# ---------------------------------------------------------------------------
# Input mappings in LLM mode
# ---------------------------------------------------------------------------


class TestLLMInputMappings:
    """input_mappings and team_inputs work in LLM-driven mode too."""

    @pytest.mark.asyncio
    async def test_input_mappings_applied_to_dispatched_team(self):
        captured_inputs: dict[str, dict] = {}

        async def capture_subgraph(key, inputs, parent_node_id=None, targeted_inputs=None):
            captured_inputs[key] = dict(inputs)
            return {"result": "ok"}

        provider = MockProvider([
            CompletionResult(
                text="dispatch",
                tool_calls=[_dispatch_tool_call("t", {"extra": "llm_added"})],
            ),
            CompletionResult(
                text="halt",
                tool_calls=[_halt_tool_call("done", {})],
            ),
        ])

        node = _make_node(
            {"t": "sg_t"},
            orchestrator_prompt="Go.",
            orchestrator_model="test-model",
            input_mappings={"input": "query"},
        )
        ctx, _ = _make_context(mock_provider=provider)
        ctx._run_subgraph = capture_subgraph

        await EXECUTOR.execute(node, {"input": "hello world"}, ctx)

        assert captured_inputs["sg_t"]["query"] == "hello world"
        assert captured_inputs["sg_t"]["extra"] == "llm_added"


# ---------------------------------------------------------------------------
# Orchestrator log & metadata
# ---------------------------------------------------------------------------


class TestOrchestratorLog:
    """Orchestrator log captures LLM calls, dispatches, and halts."""

    @pytest.mark.asyncio
    async def test_log_records_dispatches_and_halt(self):
        provider = MockProvider([
            CompletionResult(
                text="dispatch alpha",
                tool_calls=[_dispatch_tool_call("alpha")],
            ),
            CompletionResult(
                text="halt",
                tool_calls=[_halt_tool_call("done", {})],
            ),
        ])

        node = _make_node(
            {"alpha": "sg_alpha"},
            orchestrator_prompt="Go.",
            orchestrator_model="test-model",
        )
        ctx, _ = _make_context(
            {"sg_alpha": {"out": 1}},
            mock_provider=provider,
        )

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        log = result.outputs["orchestrator_log"]
        actions = [entry.get("action") for entry in log if "action" in entry]
        assert "dispatch" in actions
        assert "halt" in actions

        llm_entries = [entry for entry in log if "llm_call" in entry]
        assert len(llm_entries) == 2


# ---------------------------------------------------------------------------
# Backward-compat condition: prompt-only or model-only → static fanout
# ---------------------------------------------------------------------------


class TestBackwardCompatCondition:
    """Both orchestrator_prompt AND orchestrator_model must be set for LLM mode."""

    @pytest.mark.asyncio
    async def test_prompt_only_falls_back_to_static(self):
        """Prompt set but no model → static fan-out, not LLM-driven."""
        node = _make_node(
            {"t": "sg_t"},
            orchestrator_prompt="You are a manager.",
            orchestrator_model=None,
        )
        ctx, _ = _make_context({"sg_t": {"done": True}})

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["results"]["t"]["done"] is True
        assert "orchestrator_log" in result.outputs

    @pytest.mark.asyncio
    async def test_model_only_falls_back_to_static(self):
        """Model set but no prompt → static fan-out, not LLM-driven."""
        node = _make_node(
            {"t": "sg_t"},
            orchestrator_prompt="",
            orchestrator_model="test-model",
        )
        ctx, _ = _make_context({"sg_t": {"done": True}})

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["results"]["t"]["done"] is True


# ---------------------------------------------------------------------------
# C2 regression: halt overrides all-teams-failed
# ---------------------------------------------------------------------------


class TestHaltOverridesAllFailed:
    """When the LLM explicitly halts, status is COMPLETED even if all teams failed."""

    @pytest.mark.asyncio
    async def test_halt_after_all_teams_fail(self):
        provider = MockProvider([
            CompletionResult(
                text="dispatch",
                tool_calls=[_dispatch_tool_call("broken")],
            ),
            CompletionResult(
                text="team failed, halting gracefully",
                tool_calls=[_halt_tool_call(
                    "team failed but that's expected",
                    {"fallback": "manual"},
                )],
            ),
        ])

        node = _make_node(
            {"broken": "sg_broken"},
            orchestrator_prompt="Go.",
            orchestrator_model="test-model",
        )
        ctx, _ = _make_context(
            subgraph_errors={"sg_broken": RuntimeError("team exploded")},
            mock_provider=provider,
        )

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.metadata["halted"] is True
        assert result.outputs["halt_reason"] == "team failed but that's expected"
        assert result.error is None


# ---------------------------------------------------------------------------
# C3 regression: safety-bound without dispatch → FAILED
# ---------------------------------------------------------------------------


class TestSafetyBoundNoDispatch:
    """Exceeding safety bounds without dispatching any work returns FAILED."""

    @pytest.mark.asyncio
    async def test_max_llm_calls_no_dispatch_is_failed(self):
        provider = MockProvider([
            CompletionResult(text="thinking", tool_calls=None),
        ] * 10)

        node = _make_node(
            {"t": "sg_t"},
            orchestrator_prompt="Go.",
            orchestrator_model="test-model",
            max_llm_calls=3,
        )
        ctx, _ = _make_context(mock_provider=provider)

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.FAILED
        assert "without dispatching" in result.error

    @pytest.mark.asyncio
    async def test_safety_bound_with_dispatch_is_completed(self):
        """If some work was dispatched before hitting bound, status is COMPLETED."""
        provider = MockProvider([
            CompletionResult(
                text="dispatch",
                tool_calls=[_dispatch_tool_call("t")],
            ),
            CompletionResult(text="thinking", tool_calls=None),
            CompletionResult(text="still thinking", tool_calls=None),
        ])

        node = _make_node(
            {"t": "sg_t"},
            orchestrator_prompt="Go.",
            orchestrator_model="test-model",
            max_llm_calls=3,
        )
        ctx, _ = _make_context(
            {"sg_t": {"done": True}},
            mock_provider=provider,
        )

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["team_status"]["t"] == "completed"
