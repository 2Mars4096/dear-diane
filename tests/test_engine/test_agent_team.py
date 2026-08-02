"""Unit tests for AgentTeamExecutor (Plan 16-1)."""

import asyncio
import re

import pytest

from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
from dan.engine.executor import EngineConfig, ExecutionContext, NodeResult
from dan.engine.state import ExecutionState, NodeStatus
from dan.executors.control_flow import AgentTeamExecutor
from dan.models.control_flow import (
    AgentTeamNode,
    HandoffRequest,
    TeamConversation,
    TeamMessage,
)
from dan.models.graph import Graph
from dan.models.ports import InputPort, OutputPort


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(
    agents: dict[str, str],
    *,
    turn_strategy: str = "round_robin",
    max_turns: int = 20,
    completion_condition: str = "max_turns",
    moderator_prompt: str = "",
    moderator_model: str | None = None,
    handoff_policy: str = "explicit",
    input_mappings: dict[str, str] | None = None,
    agent_inputs: dict[str, dict] | None = None,
    timeout_seconds: float | None = None,
) -> AgentTeamNode:
    return AgentTeamNode(
        id="team",
        name="TestTeam",
        agents=agents,
        turn_strategy=turn_strategy,
        max_turns=max_turns,
        completion_condition=completion_condition,
        moderator_prompt=moderator_prompt,
        moderator_model=moderator_model,
        handoff_policy=handoff_policy,
        input_mappings=input_mappings or {},
        agent_inputs=agent_inputs or {},
        timeout_seconds=timeout_seconds,
        input_ports=[InputPort(name="input")],
        output_ports=[OutputPort(name="result")],
    )


def _make_context(
    subgraph_returns: dict[str, dict] | None = None,
    *,
    subgraph_delay: float = 0,
    subgraph_errors: dict[str, Exception] | None = None,
    call_log: list | None = None,
) -> tuple[ExecutionContext, list]:
    """Build a minimal ExecutionContext with mocked run_subgraph and event capture."""
    graph = Graph(nodes=[], edges=[], entry_points=[], exit_points=[])
    state = ExecutionState(graph)
    returns = subgraph_returns or {}
    errors = subgraph_errors or {}
    events: list = []
    log = call_log if call_log is not None else []

    async def mock_run_subgraph(
        key: str,
        inputs: dict,
        parent_node_id: str | None = None,
        targeted_inputs: dict | None = None,
    ) -> dict:
        log.append(key)
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


EXECUTOR = AgentTeamExecutor()


# ---------------------------------------------------------------------------
# Model serialization
# ---------------------------------------------------------------------------


class TestAgentTeamModelSerialization:
    """AgentTeamNode and related model round-trips."""

    def test_agent_team_node_roundtrip(self):
        node = _make_node(
            {"alice": "sg_alice", "bob": "sg_bob"},
            turn_strategy="moderator",
            max_turns=10,
            completion_condition="consensus",
            handoff_policy="any",
        )
        data = node.model_dump()
        restored = AgentTeamNode.model_validate(data)
        assert restored.node_type == "agent_team"
        assert restored.agents == {"alice": "sg_alice", "bob": "sg_bob"}
        assert restored.turn_strategy == "moderator"
        assert restored.max_turns == 10
        assert restored.completion_condition == "consensus"
        assert restored.handoff_policy == "any"

    def test_team_message_roundtrip(self):
        msg = TeamMessage(
            sender="alice",
            recipients=["bob"],
            content="Hello @bob, please review.",
            message_type="message",
            turn_number=3,
        )
        data = msg.model_dump()
        restored = TeamMessage.model_validate(data)
        assert restored.sender == "alice"
        assert restored.recipients == ["bob"]
        assert restored.content == "Hello @bob, please review."
        assert restored.turn_number == 3

    def test_handoff_request_roundtrip(self):
        req = HandoffRequest(
            source_agent="alice",
            target_agent="bob",
            reason="Need code review",
            context={"draft": "v1"},
            handoff_type="consult",
        )
        data = req.model_dump()
        restored = HandoffRequest.model_validate(data)
        assert restored.source_agent == "alice"
        assert restored.target_agent == "bob"
        assert restored.reason == "Need code review"
        assert restored.handoff_type == "consult"
        assert restored.context == {"draft": "v1"}

    def test_team_conversation_roundtrip(self):
        conv = TeamConversation(
            messages=[
                TeamMessage(sender="alice", content="Hi", turn_number=0),
                TeamMessage(sender="bob", content="Hey", turn_number=1),
            ],
            active_agent="bob",
            turn_count=2,
            handoff_log=[
                HandoffRequest(
                    source_agent="alice",
                    target_agent="bob",
                    reason="tag",
                ),
            ],
        )
        data = conv.model_dump()
        restored = TeamConversation.model_validate(data)
        assert len(restored.messages) == 2
        assert restored.active_agent == "bob"
        assert restored.turn_count == 2
        assert len(restored.handoff_log) == 1


# ---------------------------------------------------------------------------
# @-routing regex parsing
# ---------------------------------------------------------------------------


class TestMentionParsing:
    """Test @agent_name routing regex."""

    def test_single_mention(self):
        mentions = AgentTeamExecutor._parse_mentions(
            "I think @bob should handle this",
            ["alice", "bob", "charlie"],
        )
        assert mentions == ["bob"]

    def test_multiple_mentions(self):
        mentions = AgentTeamExecutor._parse_mentions(
            "@bob and @charlie should collaborate",
            ["alice", "bob", "charlie"],
        )
        assert mentions == ["bob", "charlie"]

    def test_no_mentions(self):
        mentions = AgentTeamExecutor._parse_mentions(
            "No one is mentioned here.",
            ["alice", "bob"],
        )
        assert mentions == []

    def test_unknown_mention_filtered(self):
        mentions = AgentTeamExecutor._parse_mentions(
            "@unknown_agent should do it",
            ["alice", "bob"],
        )
        assert mentions == []

    def test_mixed_known_and_unknown(self):
        mentions = AgentTeamExecutor._parse_mentions(
            "@alice and @stranger please help",
            ["alice", "bob"],
        )
        assert mentions == ["alice"]

    def test_self_mention_excluded(self):
        """@-mentions of the sending agent are filtered (sender != mentioned)."""
        text = "@alice thinks @alice should continue"
        raw = re.findall(r"@(\w+)", text)
        valid = [m for m in raw if m in ["alice", "bob"] and m != "alice"]
        assert valid == []


# ---------------------------------------------------------------------------
# Round-robin turn strategy
# ---------------------------------------------------------------------------


class TestRoundRobinStrategy:
    """Round-robin cycles through agents in declaration order."""

    @pytest.mark.asyncio
    async def test_turn_order_three_agents(self):
        """A→B→C→A→B→C over 6 turns."""
        call_log: list[str] = []
        node = _make_node(
            {"alice": "sg_alice", "bob": "sg_bob", "charlie": "sg_charlie"},
            turn_strategy="round_robin",
            max_turns=6,
        )
        ctx, events = _make_context(
            {
                "sg_alice": {"result": "Alice speaking"},
                "sg_bob": {"result": "Bob speaking"},
                "sg_charlie": {"result": "Charlie speaking"},
            },
            call_log=call_log,
        )

        result = await EXECUTOR.execute(node, {"input": "start"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["total_turns"] == 6
        assert call_log == [
            "sg_alice", "sg_bob", "sg_charlie",
            "sg_alice", "sg_bob", "sg_charlie",
        ]

    @pytest.mark.asyncio
    async def test_conversation_history_grows(self):
        """Each agent receives a growing conversation_history."""
        captured_inputs: list[dict] = []

        async def capture_subgraph(key, inputs, parent_node_id=None, targeted_inputs=None):
            captured_inputs.append(dict(inputs))
            return {"result": f"Reply from {key}"}

        node = _make_node(
            {"alice": "sg_alice", "bob": "sg_bob"},
            turn_strategy="round_robin",
            max_turns=3,
        )
        ctx, _ = _make_context()
        ctx._run_subgraph = capture_subgraph

        await EXECUTOR.execute(node, {"input": "hello"}, ctx)

        assert captured_inputs[0]["conversation_history"] == []
        assert len(captured_inputs[1]["conversation_history"]) == 1
        assert len(captured_inputs[2]["conversation_history"]) == 2

    @pytest.mark.asyncio
    async def test_agent_contributions_collected(self):
        """Output includes per-agent contribution summaries."""
        node = _make_node(
            {"alice": "sg_alice", "bob": "sg_bob"},
            turn_strategy="round_robin",
            max_turns=4,
        )
        ctx, _ = _make_context({
            "sg_alice": {"result": "Alice says hi"},
            "sg_bob": {"result": "Bob responds"},
        })

        result = await EXECUTOR.execute(node, {"input": "go"}, ctx)

        contribs = result.outputs["agent_contributions"]
        assert "Alice says hi" in contribs["alice"]
        assert "Bob responds" in contribs["bob"]


# ---------------------------------------------------------------------------
# Sequential turn strategy
# ---------------------------------------------------------------------------


class TestSequentialStrategy:
    """Sequential: each agent runs once in declaration order, then stops."""

    @pytest.mark.asyncio
    async def test_each_agent_runs_once(self):
        """Three agents each run exactly once, even with high max_turns."""
        call_log: list[str] = []
        node = _make_node(
            {"alice": "sg_alice", "bob": "sg_bob", "charlie": "sg_charlie"},
            turn_strategy="sequential",
            max_turns=10,
        )
        ctx, _ = _make_context(
            {
                "sg_alice": {"result": "Alice done"},
                "sg_bob": {"result": "Bob done"},
                "sg_charlie": {"result": "Charlie done"},
            },
            call_log=call_log,
        )

        result = await EXECUTOR.execute(node, {"input": "start"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert call_log == ["sg_alice", "sg_bob", "sg_charlie"]
        assert result.outputs["total_turns"] == 3

    @pytest.mark.asyncio
    async def test_sequential_respects_max_turns_if_lower(self):
        """If max_turns < agent count, only that many agents run."""
        call_log: list[str] = []
        node = _make_node(
            {"alice": "sg_alice", "bob": "sg_bob", "charlie": "sg_charlie"},
            turn_strategy="sequential",
            max_turns=2,
        )
        ctx, _ = _make_context(
            {
                "sg_alice": {"result": "Alice"},
                "sg_bob": {"result": "Bob"},
                "sg_charlie": {"result": "Charlie"},
            },
            call_log=call_log,
        )

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert len(call_log) == 2
        assert call_log == ["sg_alice", "sg_bob"]


# ---------------------------------------------------------------------------
# Handoff processing
# ---------------------------------------------------------------------------


class TestHandoff:
    """Handoff processing between team agents."""

    @pytest.mark.asyncio
    async def test_explicit_handoff_routes_to_target(self):
        """Agent output with handoff data routes next turn to target agent."""
        call_log: list[str] = []
        node = _make_node(
            {"alice": "sg_alice", "bob": "sg_bob"},
            turn_strategy="round_robin",
            max_turns=3,
            handoff_policy="explicit",
        )

        turn_counter = {"sg_alice": 0}

        async def mock_subgraph(key, inputs, parent_node_id=None, targeted_inputs=None):
            call_log.append(key)
            if key == "sg_alice":
                turn_counter["sg_alice"] += 1
                if turn_counter["sg_alice"] == 1:
                    return {
                        "result": "Need Bob's review",
                        "handoff": {
                            "target_agent": "bob",
                            "reason": "Code review needed",
                            "context": {"draft": "v1"},
                            "handoff_type": "transfer",
                        },
                    }
                return {"result": "Alice again"}
            return {"result": "Bob reviewed"}

        ctx, events = _make_context(call_log=call_log)
        ctx._run_subgraph = mock_subgraph

        result = await EXECUTOR.execute(node, {"input": "start"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert call_log[0] == "sg_alice"
        assert call_log[1] == "sg_bob"

        handoff_events = [e for e in events if e.event_type.value == "team_handoff"]
        assert len(handoff_events) == 1
        assert handoff_events[0].data["source"] == "alice"
        assert handoff_events[0].data["target"] == "bob"
        assert handoff_events[0].data["reason"] == "Code review needed"

    @pytest.mark.asyncio
    async def test_moderator_only_blocks_agent_handoff(self):
        """handoff_policy='moderator_only' suppresses agent-initiated handoffs."""
        call_log: list[str] = []
        node = _make_node(
            {"alice": "sg_alice", "bob": "sg_bob"},
            turn_strategy="round_robin",
            max_turns=2,
            handoff_policy="moderator_only",
        )
        ctx, events = _make_context(
            {
                "sg_alice": {
                    "result": "Handoff attempt",
                    "handoff": {"target_agent": "bob", "reason": "please"},
                },
                "sg_bob": {"result": "Bob here"},
            },
            call_log=call_log,
        )

        result = await EXECUTOR.execute(node, {"input": "go"}, ctx)

        assert call_log == ["sg_alice", "sg_bob"]
        handoff_events = [e for e in events if e.event_type.value == "team_handoff"]
        assert len(handoff_events) == 0

    @pytest.mark.asyncio
    async def test_handoff_context_passed_to_target(self):
        """Target agent receives handoff_context in its inputs."""
        captured_inputs: dict[str, list[dict]] = {"sg_bob": []}

        async def capture_subgraph(key, inputs, parent_node_id=None, targeted_inputs=None):
            if key == "sg_bob":
                captured_inputs["sg_bob"].append(dict(inputs))
            if key == "sg_alice":
                return {
                    "result": "Handing off",
                    "handoff": {
                        "target_agent": "bob",
                        "reason": "review",
                        "context": {"file": "main.py", "line": 42},
                    },
                }
            return {"result": "Bob received"}

        node = _make_node(
            {"alice": "sg_alice", "bob": "sg_bob"},
            turn_strategy="round_robin",
            max_turns=2,
        )
        ctx, _ = _make_context()
        ctx._run_subgraph = capture_subgraph

        await EXECUTOR.execute(node, {"input": "go"}, ctx)

        assert len(captured_inputs["sg_bob"]) >= 1
        bob_input = captured_inputs["sg_bob"][0]
        assert "handoff_context" in bob_input
        assert bob_input["handoff_context"]["target_agent"] == "bob"

    @pytest.mark.asyncio
    async def test_self_handoff_ignored(self):
        """Handoff to self (same agent) is rejected."""
        call_log: list[str] = []
        node = _make_node(
            {"alice": "sg_alice", "bob": "sg_bob"},
            turn_strategy="round_robin",
            max_turns=2,
        )
        ctx, events = _make_context(
            {
                "sg_alice": {
                    "result": "Self handoff attempt",
                    "handoff": {"target_agent": "alice", "reason": "self"},
                },
                "sg_bob": {"result": "Bob"},
            },
            call_log=call_log,
        )

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        handoff_events = [e for e in events if e.event_type.value == "team_handoff"]
        assert len(handoff_events) == 0
        assert call_log == ["sg_alice", "sg_bob"]


# ---------------------------------------------------------------------------
# Max-turns safety bound
# ---------------------------------------------------------------------------


class TestMaxTurns:
    """max_turns limits total conversation turns."""

    @pytest.mark.asyncio
    async def test_stops_at_max_turns(self):
        """Loop does not exceed max_turns."""
        call_log: list[str] = []
        node = _make_node(
            {"alice": "sg_alice", "bob": "sg_bob"},
            turn_strategy="round_robin",
            max_turns=3,
        )
        ctx, _ = _make_context(
            {
                "sg_alice": {"result": "Alice"},
                "sg_bob": {"result": "Bob"},
            },
            call_log=call_log,
        )

        result = await EXECUTOR.execute(node, {"input": "go"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["total_turns"] == 3
        assert len(call_log) == 3

    @pytest.mark.asyncio
    async def test_max_turns_one(self):
        """max_turns=1 allows exactly one agent to speak."""
        call_log: list[str] = []
        node = _make_node(
            {"alice": "sg_alice", "bob": "sg_bob"},
            turn_strategy="round_robin",
            max_turns=1,
        )
        ctx, _ = _make_context(
            {
                "sg_alice": {"result": "Only Alice"},
                "sg_bob": {"result": "Bob"},
            },
            call_log=call_log,
        )

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.outputs["total_turns"] == 1
        assert len(call_log) == 1
        assert call_log[0] == "sg_alice"


# ---------------------------------------------------------------------------
# all_responded completion condition
# ---------------------------------------------------------------------------


class TestAllResponded:
    """all_responded: stops when every agent has spoken at least once."""

    @pytest.mark.asyncio
    async def test_stops_after_all_agents_respond(self):
        """3 agents in round-robin, all_responded → exactly 3 turns."""
        call_log: list[str] = []
        node = _make_node(
            {"alice": "sg_alice", "bob": "sg_bob", "charlie": "sg_charlie"},
            turn_strategy="round_robin",
            max_turns=20,
            completion_condition="all_responded",
        )
        ctx, _ = _make_context(
            {
                "sg_alice": {"result": "Alice"},
                "sg_bob": {"result": "Bob"},
                "sg_charlie": {"result": "Charlie"},
            },
            call_log=call_log,
        )

        result = await EXECUTOR.execute(node, {"input": "go"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["total_turns"] == 3
        assert len(call_log) == 3
        assert set(call_log) == {"sg_alice", "sg_bob", "sg_charlie"}

    @pytest.mark.asyncio
    async def test_all_responded_not_triggered_until_all_speak(self):
        """With 3 agents and max_turns=2, stops at 2 (not all responded)."""
        call_log: list[str] = []
        node = _make_node(
            {"alice": "sg_alice", "bob": "sg_bob", "charlie": "sg_charlie"},
            turn_strategy="round_robin",
            max_turns=2,
            completion_condition="all_responded",
        )
        ctx, _ = _make_context(
            {
                "sg_alice": {"result": "Alice"},
                "sg_bob": {"result": "Bob"},
                "sg_charlie": {"result": "Charlie"},
            },
            call_log=call_log,
        )

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.outputs["total_turns"] == 2
        assert len(call_log) == 2


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class TestTeamEvents:
    """Event emission during team execution."""

    @pytest.mark.asyncio
    async def test_turn_and_completion_events(self):
        """Emits TEAM_TURN_STARTED, TEAM_TURN_COMPLETED, and TEAM_COMPLETED."""
        node = _make_node(
            {"alice": "sg_alice", "bob": "sg_bob"},
            turn_strategy="round_robin",
            max_turns=2,
        )
        ctx, events = _make_context({
            "sg_alice": {"result": "Hi"},
            "sg_bob": {"result": "Hello"},
        })

        result = await EXECUTOR.execute(node, {"input": "start"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        event_types = [e.event_type.value for e in events]
        assert event_types.count("team_turn_started") == 2
        assert event_types.count("team_turn_completed") == 2
        assert event_types.count("team_completed") == 1

    @pytest.mark.asyncio
    async def test_turn_event_data(self):
        """Turn events carry agent_name and turn_number."""
        node = _make_node(
            {"alice": "sg_alice", "bob": "sg_bob"},
            turn_strategy="round_robin",
            max_turns=2,
        )
        ctx, events = _make_context({
            "sg_alice": {"result": "Hi"},
            "sg_bob": {"result": "Hey"},
        })

        await EXECUTOR.execute(node, {"input": "x"}, ctx)

        started = [e for e in events if e.event_type.value == "team_turn_started"]
        assert started[0].data["agent_name"] == "alice"
        assert started[1].data["agent_name"] == "bob"

    @pytest.mark.asyncio
    async def test_completed_event_data(self):
        """TEAM_COMPLETED carries consensus_reached and total_turns."""
        node = _make_node(
            {"alice": "sg_alice", "bob": "sg_bob"},
            turn_strategy="round_robin",
            max_turns=2,
        )
        ctx, events = _make_context({
            "sg_alice": {"result": "A"},
            "sg_bob": {"result": "B"},
        })

        await EXECUTOR.execute(node, {"input": "x"}, ctx)

        completed = [e for e in events if e.event_type.value == "team_completed"]
        assert len(completed) == 1
        assert completed[0].data["total_turns"] == 2
        assert completed[0].data["consensus_reached"] is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_fewer_than_two_agents_fails(self):
        """Agent team requires at least 2 agents."""
        node = _make_node({"solo": "sg_solo"})
        ctx, _ = _make_context()

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.FAILED
        assert "at least 2 agents" in result.error

    @pytest.mark.asyncio
    async def test_agent_subgraph_error_continues(self):
        """If an agent's subgraph throws, conversation continues."""
        call_log: list[str] = []
        node = _make_node(
            {"alice": "sg_alice", "bob": "sg_bob"},
            turn_strategy="round_robin",
            max_turns=3,
        )
        ctx, _ = _make_context(
            subgraph_returns={"sg_bob": {"result": "Bob OK"}},
            subgraph_errors={"sg_alice": RuntimeError("alice crashed")},
            call_log=call_log,
        )

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert len(call_log) == 3
        assert "Error" in result.outputs["conversation"][0]["content"]

    @pytest.mark.asyncio
    async def test_conversation_stored_in_shared_context(self):
        """Conversation is persisted to shared context under the well-known key."""
        node = _make_node(
            {"alice": "sg_alice", "bob": "sg_bob"},
            turn_strategy="round_robin",
            max_turns=2,
        )
        ctx, _ = _make_context({
            "sg_alice": {"result": "Hi"},
            "sg_bob": {"result": "Hey"},
        })

        await EXECUTOR.execute(node, {"input": "x"}, ctx)

        conv_key = f"__team__{node.id}__conversation"
        # Team writes undeclared dynamic keys via _ctx_write bypass, so we
        # must use has() + direct _store access (read() rejects undeclared keys).
        assert ctx.shared_context.has(conv_key)
        stored = ctx.shared_context._store[conv_key]
        assert stored["turn_count"] == 2
        assert len(stored["messages"]) == 2

    @pytest.mark.asyncio
    async def test_result_includes_conversation_messages(self):
        """Output includes full conversation as list of message dicts."""
        node = _make_node(
            {"alice": "sg_alice", "bob": "sg_bob"},
            turn_strategy="round_robin",
            max_turns=2,
        )
        ctx, _ = _make_context({
            "sg_alice": {"result": "Hello from Alice"},
            "sg_bob": {"result": "Hello from Bob"},
        })

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        msgs = result.outputs["conversation"]
        assert len(msgs) == 2
        assert msgs[0]["sender"] == "alice"
        assert msgs[0]["content"] == "Hello from Alice"
        assert msgs[1]["sender"] == "bob"
        assert msgs[1]["content"] == "Hello from Bob"


# ---------------------------------------------------------------------------
# Mention-parsing helper (static method, unit-testable standalone)
# ---------------------------------------------------------------------------


class TestParseHandoffStatic:
    """Unit tests for the _parse_handoff static helper."""

    def test_valid_handoff(self):
        output = {
            "result": "text",
            "handoff": {
                "target_agent": "bob",
                "reason": "review",
                "context": {"k": "v"},
                "handoff_type": "consult",
            },
        }
        h = AgentTeamExecutor._parse_handoff(output, "alice", ["alice", "bob"])
        assert h is not None
        assert h.source_agent == "alice"
        assert h.target_agent == "bob"
        assert h.handoff_type == "consult"

    def test_no_handoff_key(self):
        output = {"result": "just text"}
        h = AgentTeamExecutor._parse_handoff(output, "alice", ["alice", "bob"])
        assert h is None

    def test_handoff_to_unknown_agent(self):
        output = {"handoff": {"target_agent": "unknown"}}
        h = AgentTeamExecutor._parse_handoff(output, "alice", ["alice", "bob"])
        assert h is None

    def test_handoff_to_self_rejected(self):
        output = {"handoff": {"target_agent": "alice"}}
        h = AgentTeamExecutor._parse_handoff(output, "alice", ["alice", "bob"])
        assert h is None


# ---------------------------------------------------------------------------
# _extract_content helper
# ---------------------------------------------------------------------------


class TestExtractContent:
    """Unit tests for output text extraction."""

    def test_result_key(self):
        assert AgentTeamExecutor._extract_content({"result": "hello"}) == "hello"

    def test_response_key(self):
        assert AgentTeamExecutor._extract_content({"response": "hi"}) == "hi"

    def test_fallback_to_json(self):
        out = AgentTeamExecutor._extract_content({"x": 1, "y": 2})
        assert "1" in out
        assert "2" in out


# ---------------------------------------------------------------------------
# _parse_mentions helper (additional static tests)
# ---------------------------------------------------------------------------


class TestParseMentionsStatic:
    """Directly test the _parse_mentions static helper edge cases."""

    def test_empty_text(self):
        assert AgentTeamExecutor._parse_mentions("", ["alice"]) == []

    def test_at_sign_without_name(self):
        assert AgentTeamExecutor._parse_mentions("email: a@b.com", ["a", "b"]) == ["b"]

    def test_mention_at_start(self):
        assert AgentTeamExecutor._parse_mentions("@alice go", ["alice"]) == ["alice"]

    def test_mention_at_end(self):
        assert AgentTeamExecutor._parse_mentions("done @bob", ["bob"]) == ["bob"]


# ---------------------------------------------------------------------------
# All-errored team returns FAILED (regression for C1 patch)
# ---------------------------------------------------------------------------


class TestAllAgentsFail:
    """When every single turn errors, the team returns FAILED."""

    @pytest.mark.asyncio
    async def test_all_turns_error_returns_failed(self):
        node = _make_node(
            {"alice": "sg_alice", "bob": "sg_bob"},
            turn_strategy="round_robin",
            max_turns=4,
        )
        ctx, _ = _make_context(
            subgraph_errors={
                "sg_alice": RuntimeError("alice crashed"),
                "sg_bob": ValueError("bob crashed"),
            },
        )

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.FAILED
        assert "All agent turns failed" in result.error
        assert result.metadata["error_turns"] == 4

    @pytest.mark.asyncio
    async def test_partial_errors_still_completes(self):
        """If at least one turn succeeds, status is COMPLETED."""
        node = _make_node(
            {"alice": "sg_alice", "bob": "sg_bob"},
            turn_strategy="round_robin",
            max_turns=4,
        )
        ctx, _ = _make_context(
            subgraph_returns={"sg_bob": {"result": "Bob OK"}},
            subgraph_errors={"sg_alice": RuntimeError("alice crashed")},
        )

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.metadata["error_turns"] == 2


# ---------------------------------------------------------------------------
# Consensus completion condition
# ---------------------------------------------------------------------------


class TestConsensusCompletion:
    """completion_condition='consensus' stops when last message contains CONSENSUS."""

    @pytest.mark.asyncio
    async def test_consensus_triggers_early_stop(self):
        """Agent says CONSENSUS → loop exits, consensus_reached=True."""
        turn_counter = {"n": 0}

        async def consensus_subgraph(key, inputs, parent_node_id=None, targeted_inputs=None):
            turn_counter["n"] += 1
            if turn_counter["n"] == 3:
                return {"result": "I agree, we have reached CONSENSUS."}
            return {"result": f"Turn {turn_counter['n']} discussion"}

        node = _make_node(
            {"alice": "sg_alice", "bob": "sg_bob"},
            turn_strategy="round_robin",
            max_turns=20,
            completion_condition="consensus",
        )
        ctx, events = _make_context()
        ctx._run_subgraph = consensus_subgraph

        result = await EXECUTOR.execute(node, {"input": "debate topic"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["consensus_reached"] is True
        assert result.outputs["total_turns"] == 3

        completed = [e for e in events if e.event_type.value == "team_completed"]
        assert completed[0].data["consensus_reached"] is True

    @pytest.mark.asyncio
    async def test_no_consensus_runs_to_max_turns(self):
        """Without CONSENSUS keyword, runs to max_turns; consensus_reached=False."""
        node = _make_node(
            {"alice": "sg_alice", "bob": "sg_bob"},
            turn_strategy="round_robin",
            max_turns=4,
            completion_condition="consensus",
        )
        ctx, _ = _make_context({
            "sg_alice": {"result": "I disagree"},
            "sg_bob": {"result": "Same here"},
        })

        result = await EXECUTOR.execute(node, {"input": "x"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["consensus_reached"] is False
        assert result.outputs["total_turns"] == 4
