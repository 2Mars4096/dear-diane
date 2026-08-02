"""Tests for agent_runtime types — construction and default values."""

from __future__ import annotations

from dan.agent_runtime.types import AgentEvent, AgentProfile, AgentRequest, AgentResult


class TestAgentProfile:
    def test_all_profile_values(self) -> None:
        expected = {"direct_task", "build", "planning", "debug", "review"}
        actual = {p.value for p in AgentProfile}
        assert actual == expected

    def test_profile_count(self) -> None:
        assert len(AgentProfile) == 5

    def test_profile_from_value(self) -> None:
        assert AgentProfile("build") is AgentProfile.BUILD


class TestAgentRequest:
    def test_minimal_construction(self) -> None:
        req = AgentRequest(messages=[{"role": "user", "content": "hi"}])
        assert req.messages == [{"role": "user", "content": "hi"}]

    def test_defaults(self) -> None:
        req = AgentRequest(messages=[])
        assert req.model == "gpt-4o"
        assert req.profile is AgentProfile.DIRECT_TASK
        assert req.temperature == 0.7
        assert req.max_tokens is None
        assert req.tools is None
        assert req.tool_budget == 25
        assert req.context == {}
        assert req.metadata == {}

    def test_custom_values(self) -> None:
        tools = [{"type": "function", "function": {"name": "test"}}]
        req = AgentRequest(
            messages=[{"role": "user", "content": "test"}],
            model="gpt-4o-mini",
            profile=AgentProfile.BUILD,
            temperature=0.3,
            max_tokens=1000,
            tools=tools,
            tool_budget=10,
            context={"workflow_id": "wf-1"},
            metadata={"trace_id": "abc"},
        )
        assert req.model == "gpt-4o-mini"
        assert req.profile is AgentProfile.BUILD
        assert req.temperature == 0.3
        assert req.max_tokens == 1000
        assert req.tools == tools
        assert req.tool_budget == 10
        assert req.context["workflow_id"] == "wf-1"
        assert req.metadata["trace_id"] == "abc"

    def test_context_not_shared_across_instances(self) -> None:
        r1 = AgentRequest(messages=[])
        r2 = AgentRequest(messages=[])
        r1.context["x"] = 1
        assert "x" not in r2.context


class TestAgentEvent:
    def test_text_delta_event(self) -> None:
        e = AgentEvent(kind="text_delta", data="Hello")
        assert e.kind == "text_delta"
        assert e.data == "Hello"
        assert e.metadata == {}

    def test_tool_call_event(self) -> None:
        e = AgentEvent(
            kind="tool_call",
            data={"name": "search", "args": {"q": "test"}},
            metadata={"tool_index": 0},
        )
        assert e.kind == "tool_call"
        assert e.data["name"] == "search"
        assert e.metadata["tool_index"] == 0

    def test_error_event(self) -> None:
        e = AgentEvent(kind="error", data="timeout")
        assert e.kind == "error"

    def test_complete_event(self) -> None:
        e = AgentEvent(kind="complete", data="Full response text")
        assert e.kind == "complete"


class TestAgentResult:
    def test_defaults(self) -> None:
        r = AgentResult()
        assert r.text == ""
        assert r.tool_calls_made == 0
        assert r.model_used == ""
        assert r.usage is None
        assert r.events == []
        assert r.error is None

    def test_success_result(self) -> None:
        r = AgentResult(
            text="The answer is 42.",
            model_used="gpt-4o",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )
        assert r.text == "The answer is 42."
        assert r.model_used == "gpt-4o"
        assert r.usage["prompt_tokens"] == 10

    def test_error_result(self) -> None:
        r = AgentResult(error="Model unavailable")
        assert r.error == "Model unavailable"
        assert r.text == ""

    def test_events_not_shared_across_instances(self) -> None:
        r1 = AgentResult()
        r2 = AgentResult()
        r1.events.append(AgentEvent(kind="text_delta"))
        assert len(r2.events) == 0
