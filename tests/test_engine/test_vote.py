"""Tests for VoteExecutor — voting/ensemble quality primitive."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
from dan.engine.executor import EngineConfig, ExecutionContext, NodeResult
from dan.engine.state import ExecutionState, NodeStatus
from dan.executors.control_flow import VoteExecutor
from dan.models.control_flow import VoteConfig, VoteNode
from dan.models.graph import Graph
from dan.models.ports import InputPort, OutputPort
from dan.providers import CompletionResult
from dan.providers.registry import ProviderRegistry


# ---------------------------------------------------------------------------
# Fake provider for deterministic LLM responses
# ---------------------------------------------------------------------------


class FakeProvider:
    """Returns predefined responses in order, cycling if exhausted."""

    def __init__(self, responses: list[str], usage: dict[str, int] | None = None) -> None:
        self.responses = responses
        self.usage = usage or {"prompt_tokens": 10, "completion_tokens": 20}
        self.calls: list[dict[str, Any]] = []
        self._index = 0

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kw):
        resp = self.responses[self._index % len(self.responses)]
        self._index += 1
        self.calls.append({"model": model, "messages": messages, "temperature": temperature})
        return CompletionResult(text=resp, usage=dict(self.usage), model=model)

    async def stream(self, messages, model, **kw):
        yield  # pragma: no cover


class FailingProvider:
    """Always raises on complete()."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error or RuntimeError("provider error")

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kw):
        raise self.error

    async def stream(self, messages, model, **kw):
        yield  # pragma: no cover


class PartialProvider:
    """Succeeds for specific call indices, fails for others."""

    def __init__(
        self,
        responses: list[str],
        fail_indices: set[int],
        usage: dict[str, int] | None = None,
    ) -> None:
        self.responses = responses
        self.fail_indices = fail_indices
        self.usage = usage or {"prompt_tokens": 10, "completion_tokens": 20}
        self._index = 0

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kw):
        idx = self._index
        self._index += 1
        if idx in self.fail_indices:
            raise RuntimeError(f"call {idx} failed")
        resp = self.responses[idx % len(self.responses)]
        return CompletionResult(text=resp, usage=dict(self.usage), model=model)

    async def stream(self, messages, model, **kw):
        yield  # pragma: no cover


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(
    candidates: list[str],
    *,
    num_votes: int = 3,
    prompt_template: str = "Evaluate: {input}",
    system_prompt: str = "",
    temperature: float = 0.7,
    vote_strategy: str = "majority",
    vote_config: VoteConfig | None = None,
    parallelism: int | None = None,
) -> VoteNode:
    return VoteNode(
        id="vote1",
        name="Test Vote",
        candidates=candidates,
        num_votes=num_votes,
        prompt_template=prompt_template,
        system_prompt=system_prompt,
        temperature=temperature,
        vote_strategy=vote_strategy,
        vote_config=vote_config,
        parallelism=parallelism or num_votes,
        input_ports=[InputPort(name="input")],
        output_ports=[OutputPort(name="winner")],
    )


def _make_context(
    registry: ProviderRegistry,
) -> tuple[ExecutionContext, list]:
    graph = Graph(nodes=[], edges=[], entry_points=[], exit_points=[])
    state = ExecutionState(graph)
    events: list = []

    async def mock_emit(event: object) -> None:
        events.append(event)

    ctx = ExecutionContext(
        state=state,
        config=EngineConfig(checkpoint_enabled=False),
        shared_context=SharedContextStore([]),
        artifacts=ArtifactStore(),
        local_state=LocalStateManager(),
        event_callback=mock_emit,
        run_id="test-vote-run",
        provider_registry=registry,
    )
    return ctx, events


EXECUTOR = VoteExecutor()


# ===========================================================================
# Model serialization tests
# ===========================================================================


class TestVoteModelSerialization:
    """VoteNode and VoteConfig serialize/deserialize correctly."""

    def test_vote_node_roundtrip(self):
        node = _make_node(["gpt-4o"], num_votes=5, vote_strategy="majority")
        data = node.model_dump()
        restored = VoteNode.model_validate(data)
        assert restored.node_type == "vote"
        assert restored.candidates == ["gpt-4o"]
        assert restored.num_votes == 5
        assert restored.vote_strategy == "majority"

    def test_vote_config_defaults(self):
        config = VoteConfig()
        assert config.judge_model is None
        assert config.quality_metric is None
        assert config.unanimity_threshold == 1.0
        assert config.consensus_mode == "whole"

    def test_vote_config_roundtrip(self):
        config = VoteConfig(
            judge_model="claude-opus-4",
            judge_prompt="Pick the best answer",
            quality_metric="len(output)",
            unanimity_threshold=0.8,
        )
        data = config.model_dump()
        restored = VoteConfig.model_validate(data)
        assert restored.judge_model == "claude-opus-4"
        assert restored.unanimity_threshold == 0.8

    def test_vote_node_with_config(self):
        config = VoteConfig(judge_model="gpt-4o", quality_metric="len(output) > 50")
        node = _make_node(
            ["gpt-4o", "claude-sonnet-4"],
            vote_strategy="judge",
            vote_config=config,
        )
        data = node.model_dump()
        restored = VoteNode.model_validate(data)
        assert restored.vote_config is not None
        assert restored.vote_config.judge_model == "gpt-4o"

    def test_vote_node_in_graph_union(self):
        """VoteNode is in the Node discriminated union."""
        node = _make_node(["gpt-4o"])
        data = node.model_dump()
        assert data["node_type"] == "vote"

    def test_output_json_schema_roundtrip(self):
        """output_json_schema is serialized and deserialized correctly."""
        schema = {
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
                "confidence": {"type": "number"},
            },
            "required": ["answer"],
        }
        node = VoteNode(
            id="v1", name="Structured",
            candidates=["gpt-4o"],
            num_votes=3,
            output_json_schema=schema,
            input_ports=[InputPort(name="input")],
            output_ports=[OutputPort(name="winner")],
        )
        data = node.model_dump()
        assert data["output_json_schema"] == schema
        restored = VoteNode.model_validate(data)
        assert restored.output_json_schema == schema
        assert restored.output_json_schema["required"] == ["answer"]

    def test_output_json_schema_default_none(self):
        """output_json_schema is None by default."""
        node = _make_node(["gpt-4o"])
        assert node.output_json_schema is None


# ===========================================================================
# Prompt template rendering
# ===========================================================================


class TestPromptRendering:
    """Prompt template {key} placeholder substitution."""

    def test_single_placeholder(self):
        rendered = VoteExecutor._render_prompt("Question: {input}", {"input": "hello"})
        assert rendered == "Question: hello"

    def test_multiple_placeholders(self):
        rendered = VoteExecutor._render_prompt(
            "{topic} - analyze {data}",
            {"topic": "AI", "data": "metrics"},
        )
        assert rendered == "AI - analyze metrics"

    def test_missing_placeholder_preserved(self):
        rendered = VoteExecutor._render_prompt("Use {missing}", {"input": "val"})
        assert rendered == "Use {missing}"

    def test_empty_template(self):
        rendered = VoteExecutor._render_prompt("", {"input": "val"})
        assert rendered == ""


# ===========================================================================
# Candidate collection (round-robin)
# ===========================================================================


class TestCandidateCollection:
    """Round-robin model assignment."""

    def test_single_candidate_repeats(self):
        node = _make_node(["gpt-4o"], num_votes=3)
        pairs = VoteExecutor._collect_candidates(node)
        assert pairs == [(0, "gpt-4o"), (1, "gpt-4o"), (2, "gpt-4o")]

    def test_multi_candidate_round_robin(self):
        node = _make_node(["gpt-4o", "claude-sonnet-4", "gemini-2.5-pro"], num_votes=3)
        pairs = VoteExecutor._collect_candidates(node)
        assert pairs == [
            (0, "gpt-4o"),
            (1, "claude-sonnet-4"),
            (2, "gemini-2.5-pro"),
        ]

    def test_round_robin_wraps(self):
        node = _make_node(["a", "b"], num_votes=5)
        pairs = VoteExecutor._collect_candidates(node)
        models = [m for _, m in pairs]
        assert models == ["a", "b", "a", "b", "a"]


# ===========================================================================
# Majority voting
# ===========================================================================


class TestMajorityVoting:
    """Strategy: majority — hash-compare outputs, pick most common."""

    @pytest.mark.asyncio
    async def test_unanimous_3_identical(self):
        """3 identical responses → unanimous consensus."""
        provider = FakeProvider(["The answer is 42"])
        reg = ProviderRegistry()
        reg.register("default", provider)

        node = _make_node(["gpt-4o"], num_votes=3, vote_strategy="majority")
        ctx, events = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["winner"] == "The answer is 42"
        assert result.outputs["consensus_reached"] is True
        assert result.outputs["vote_count"] == 3
        assert result.outputs["strategy_used"] == "majority"

    @pytest.mark.asyncio
    async def test_majority_2_vs_1(self):
        """2 identical + 1 different → majority wins."""
        provider = FakeProvider(["answer A", "answer A", "answer B"])
        reg = ProviderRegistry()
        reg.register("default", provider)

        node = _make_node(["gpt-4o"], num_votes=3, vote_strategy="majority")
        ctx, events = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["winner"] == "answer A"
        assert result.outputs["consensus_reached"] is False

    @pytest.mark.asyncio
    async def test_majority_tie_break_first_occurrence(self):
        """Tie → first occurrence wins."""
        provider = FakeProvider(["X", "Y", "Z"])
        reg = ProviderRegistry()
        reg.register("default", provider)

        node = _make_node(["gpt-4o"], num_votes=3, vote_strategy="majority")
        ctx, events = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["winner"] == "X"

    @pytest.mark.asyncio
    async def test_whitespace_normalization(self):
        """Whitespace differences are ignored in comparison."""
        provider = FakeProvider(["hello  world", "hello world", "hello\n world"])
        reg = ProviderRegistry()
        reg.register("default", provider)

        node = _make_node(["gpt-4o"], num_votes=3, vote_strategy="majority")
        ctx, events = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["consensus_reached"] is True


# ===========================================================================
# Weighted voting
# ===========================================================================


class TestWeightedVoting:
    """Strategy: weighted — evaluate quality_metric expression."""

    @pytest.mark.asyncio
    async def test_weighted_by_length(self):
        """Longer output wins with len(output) metric."""
        provider = FakeProvider(["short", "this is a much longer answer", "medium length"])
        reg = ProviderRegistry()
        reg.register("default", provider)

        config = VoteConfig(quality_metric="len(output)")
        node = _make_node(
            ["gpt-4o"],
            num_votes=3,
            vote_strategy="weighted",
            vote_config=config,
        )
        ctx, _ = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["winner"] == "this is a much longer answer"
        assert result.outputs["consensus_reached"] is False

    @pytest.mark.asyncio
    async def test_weighted_with_custom_expression(self):
        """Custom expression using output content."""
        provider = FakeProvider(["no refs", "see references below", "refs: paper1"])
        reg = ProviderRegistry()
        reg.register("default", provider)

        config = VoteConfig(quality_metric="1 if 'references' in output else 0")
        node = _make_node(
            ["gpt-4o"],
            num_votes=3,
            vote_strategy="weighted",
            vote_config=config,
        )
        ctx, _ = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert "references" in result.outputs["winner"]

    @pytest.mark.asyncio
    async def test_weighted_default_metric(self):
        """Falls back to len(output) when no quality_metric set."""
        provider = FakeProvider(["a", "longer answer wins"])
        reg = ProviderRegistry()
        reg.register("default", provider)

        config = VoteConfig()
        node = _make_node(
            ["gpt-4o"],
            num_votes=2,
            vote_strategy="weighted",
            vote_config=config,
        )
        ctx, _ = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["winner"] == "longer answer wins"


# ===========================================================================
# Judge / best_of_n voting
# ===========================================================================


class TestJudgeVoting:
    """Strategy: judge / best_of_n — LLM picks the best answer."""

    @pytest.mark.asyncio
    async def test_judge_picks_by_index(self):
        """Judge returns index → that candidate wins."""
        candidate_provider = FakeProvider(["answer A", "answer B", "answer C"])
        judge_provider = FakeProvider(["1"])
        reg = ProviderRegistry()
        reg.register("default", candidate_provider)
        reg.register("openai", candidate_provider)
        reg.register("anthropic", judge_provider)

        config = VoteConfig(judge_model="claude-opus-4")
        node = _make_node(
            ["gpt-4o"],
            num_votes=3,
            vote_strategy="judge",
            vote_config=config,
        )
        ctx, _ = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["winner"] == "answer B"
        assert result.outputs["winner_index"] == 1

    @pytest.mark.asyncio
    async def test_best_of_n_same_as_judge(self):
        """best_of_n uses same logic as judge."""
        candidate_provider = FakeProvider(["X", "Y"])
        judge_provider = FakeProvider(["0"])
        reg = ProviderRegistry()
        reg.register("default", candidate_provider)
        reg.register("anthropic", judge_provider)

        config = VoteConfig(judge_model="claude-opus-4")
        node = _make_node(
            ["gpt-4o"],
            num_votes=2,
            vote_strategy="best_of_n",
            vote_config=config,
        )
        ctx, _ = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["winner"] == "X"

    @pytest.mark.asyncio
    async def test_judge_fallback_on_bad_index(self):
        """Judge returns unparseable text → falls back to first candidate."""
        candidate_provider = FakeProvider(["A", "B"])
        judge_provider = FakeProvider(["I can't decide"])
        reg = ProviderRegistry()
        reg.register("default", candidate_provider)
        reg.register("anthropic", judge_provider)

        config = VoteConfig(judge_model="claude-opus-4")
        node = _make_node(
            ["gpt-4o"],
            num_votes=2,
            vote_strategy="judge",
            vote_config=config,
        )
        ctx, _ = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["winner"] in ("A", "B")


# ===========================================================================
# Unanimous voting
# ===========================================================================


class TestUnanimousVoting:
    """Strategy: unanimous — check if all agree within threshold."""

    @pytest.mark.asyncio
    async def test_fully_unanimous(self):
        """All identical → consensus_reached=True."""
        provider = FakeProvider(["same"])
        reg = ProviderRegistry()
        reg.register("default", provider)

        config = VoteConfig(unanimity_threshold=1.0)
        node = _make_node(
            ["gpt-4o"],
            num_votes=3,
            vote_strategy="unanimous",
            vote_config=config,
        )
        ctx, _ = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["consensus_reached"] is True
        assert result.outputs["winner"] == "same"

    @pytest.mark.asyncio
    async def test_not_unanimous(self):
        """Disagreement → consensus_reached=False, majority returned."""
        provider = FakeProvider(["A", "A", "B"])
        reg = ProviderRegistry()
        reg.register("default", provider)

        config = VoteConfig(unanimity_threshold=1.0)
        node = _make_node(
            ["gpt-4o"],
            num_votes=3,
            vote_strategy="unanimous",
            vote_config=config,
        )
        ctx, _ = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["consensus_reached"] is False
        assert result.outputs["winner"] == "A"

    @pytest.mark.asyncio
    async def test_threshold_partial_agreement(self):
        """2/3 agree, threshold=0.6 → consensus_reached=True."""
        provider = FakeProvider(["yes", "yes", "no"])
        reg = ProviderRegistry()
        reg.register("default", provider)

        config = VoteConfig(unanimity_threshold=0.6)
        node = _make_node(
            ["gpt-4o"],
            num_votes=3,
            vote_strategy="unanimous",
            vote_config=config,
        )
        ctx, _ = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["consensus_reached"] is True
        assert result.outputs["winner"] == "yes"


# ===========================================================================
# Partial failure handling
# ===========================================================================


class TestPartialFailure:
    """Proceeds if at least ceil(num_votes/2) succeed."""

    @pytest.mark.asyncio
    async def test_2_of_3_succeed(self):
        """2 succeed, 1 fails → still COMPLETED (ceil(3/2)=2)."""
        provider = PartialProvider(
            responses=["ok", "ok", "ok"],
            fail_indices={2},
        )
        reg = ProviderRegistry()
        reg.register("default", provider)

        node = _make_node(["gpt-4o"], num_votes=3, vote_strategy="majority")
        ctx, _ = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["vote_count"] == 2

    @pytest.mark.asyncio
    async def test_1_of_3_succeed_fails(self):
        """Only 1 succeeds → FAILED (need 2)."""
        provider = PartialProvider(
            responses=["ok", "ok", "ok"],
            fail_indices={0, 2},
        )
        reg = ProviderRegistry()
        reg.register("default", provider)

        node = _make_node(["gpt-4o"], num_votes=3, vote_strategy="majority")
        ctx, _ = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.status == NodeStatus.FAILED
        assert "1/3" in result.error

    @pytest.mark.asyncio
    async def test_all_fail(self):
        """All fail → FAILED."""
        provider = FailingProvider()
        reg = ProviderRegistry()
        reg.register("default", provider)

        node = _make_node(["gpt-4o"], num_votes=3, vote_strategy="majority")
        ctx, _ = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.status == NodeStatus.FAILED
        assert "0/3" in result.error


# ===========================================================================
# Same-model voting (single candidate, multiple votes)
# ===========================================================================


class TestSameModelVoting:
    """Single candidate model runs N times."""

    @pytest.mark.asyncio
    async def test_single_candidate_3_votes(self):
        """All 3 calls go to the same model."""
        provider = FakeProvider(["result"])
        reg = ProviderRegistry()
        reg.register("default", provider)

        node = _make_node(["gpt-4o"], num_votes=3)
        ctx, _ = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert len(provider.calls) == 3
        assert all(c["model"] == "gpt-4o" for c in provider.calls)
        assert result.outputs["winner_model"] == "gpt-4o"

    @pytest.mark.asyncio
    async def test_single_candidate_5_votes(self):
        """5-vote same-model works."""
        provider = FakeProvider(["A", "A", "B", "A", "B"])
        reg = ProviderRegistry()
        reg.register("default", provider)

        node = _make_node(["gpt-4o"], num_votes=5, parallelism=5)
        ctx, _ = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["winner"] == "A"
        assert result.outputs["vote_count"] == 5


# ===========================================================================
# Cross-model ensemble (multiple candidates)
# ===========================================================================


class TestCrossModelEnsemble:
    """Multiple different candidate models."""

    @pytest.mark.asyncio
    async def test_3_different_models(self):
        """3 candidates with num_votes=3 → each model called once."""
        openai_p = FakeProvider(["OpenAI answer"])
        anthropic_p = FakeProvider(["Anthropic answer"])
        google_p = FakeProvider(["Google answer"])

        reg = ProviderRegistry()
        reg.register("openai", openai_p)
        reg.register("anthropic", anthropic_p)
        reg.register("google", google_p)

        node = _make_node(
            ["gpt-4o", "claude-sonnet-4", "gemini-2.5-pro"],
            num_votes=3,
            vote_strategy="majority",
        )
        ctx, _ = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["vote_count"] == 3
        assert len(result.outputs["all_votes"]) == 3

        models_used = {v["model"] for v in result.outputs["all_votes"]}
        assert models_used == {"gpt-4o", "claude-sonnet-4", "gemini-2.5-pro"}

    @pytest.mark.asyncio
    async def test_cross_model_majority_agreement(self):
        """2 of 3 models agree → majority wins."""
        openai_p = FakeProvider(["consensus"])
        anthropic_p = FakeProvider(["consensus"])
        google_p = FakeProvider(["different"])

        reg = ProviderRegistry()
        reg.register("openai", openai_p)
        reg.register("anthropic", anthropic_p)
        reg.register("google", google_p)

        node = _make_node(
            ["gpt-4o", "claude-sonnet-4", "gemini-2.5-pro"],
            num_votes=3,
            vote_strategy="majority",
        )
        ctx, _ = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["winner"] == "consensus"
        assert result.outputs["consensus_reached"] is False

    @pytest.mark.asyncio
    async def test_round_robin_5_votes_2_models(self):
        """5 votes across 2 models = round-robin distribution."""
        openai_p = FakeProvider(["A", "A", "A"])
        anthropic_p = FakeProvider(["B", "B"])

        reg = ProviderRegistry()
        reg.register("openai", openai_p)
        reg.register("anthropic", anthropic_p)

        node = _make_node(
            ["gpt-4o", "claude-sonnet-4"],
            num_votes=5,
            vote_strategy="majority",
            parallelism=5,
        )
        ctx, _ = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["vote_count"] == 5
        assert len(openai_p.calls) == 3
        assert len(anthropic_p.calls) == 2


# ===========================================================================
# Event emission
# ===========================================================================


class TestVoteEvents:
    """VOTE_STARTED, VOTE_CAST, VOTE_COMPLETED events."""

    @pytest.mark.asyncio
    async def test_events_emitted(self):
        provider = FakeProvider(["ok"])
        reg = ProviderRegistry()
        reg.register("default", provider)

        node = _make_node(["gpt-4o"], num_votes=3)
        ctx, events = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.status == NodeStatus.COMPLETED

        event_types = [e.event_type.value for e in events]
        assert event_types.count("vote_started") == 1
        assert event_types.count("vote_cast") == 3
        assert event_types.count("vote_completed") == 1

    @pytest.mark.asyncio
    async def test_vote_started_data(self):
        provider = FakeProvider(["ok"])
        reg = ProviderRegistry()
        reg.register("default", provider)

        node = _make_node(["gpt-4o", "claude-sonnet-4"], num_votes=2, vote_strategy="majority")
        ctx, events = _make_context(reg)

        await EXECUTOR.execute(node, {"input": "test"}, ctx)

        started = [e for e in events if e.event_type.value == "vote_started"][0]
        assert started.data["num_votes"] == 2
        assert started.data["candidates"] == ["gpt-4o", "claude-sonnet-4"]
        assert started.data["strategy"] == "majority"

    @pytest.mark.asyncio
    async def test_vote_completed_data(self):
        provider = FakeProvider(["ok"])
        reg = ProviderRegistry()
        reg.register("default", provider)

        node = _make_node(["gpt-4o"], num_votes=3)
        ctx, events = _make_context(reg)

        await EXECUTOR.execute(node, {"input": "test"}, ctx)

        completed = [e for e in events if e.event_type.value == "vote_completed"][0]
        assert "winner" in completed.data
        assert "consensus" in completed.data
        assert "cost" in completed.data


# ===========================================================================
# Cost tracking
# ===========================================================================


class TestCostTracking:
    """Total cost accumulation from CompletionResult.usage."""

    @pytest.mark.asyncio
    async def test_total_cost_accumulated(self):
        provider = FakeProvider(
            ["ok"],
            usage={"prompt_tokens": 100, "completion_tokens": 50},
        )
        reg = ProviderRegistry()
        reg.register("default", provider)

        node = _make_node(["gpt-4o"], num_votes=3)
        ctx, _ = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["total_cost"] > 0
        assert isinstance(result.outputs["total_cost"], float)

    @pytest.mark.asyncio
    async def test_cost_with_unknown_model(self):
        """Unknown model → cost=0 per vote, still runs."""
        provider = FakeProvider(["ok"])
        reg = ProviderRegistry()
        reg.register("default", provider)

        node = _make_node(["unknown-model-xyz"], num_votes=2)
        ctx, _ = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["total_cost"] == 0.0


# ===========================================================================
# Output structure
# ===========================================================================


class TestOutputStructure:
    """Verify all expected fields in the result."""

    @pytest.mark.asyncio
    async def test_all_output_fields_present(self):
        provider = FakeProvider(["result"])
        reg = ProviderRegistry()
        reg.register("default", provider)

        node = _make_node(["gpt-4o"], num_votes=3)
        ctx, _ = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        out = result.outputs
        assert "winner" in out
        assert "winner_model" in out
        assert "winner_index" in out
        assert "all_votes" in out
        assert "consensus_reached" in out
        assert "vote_count" in out
        assert "total_cost" in out
        assert "strategy_used" in out

    @pytest.mark.asyncio
    async def test_all_votes_structure(self):
        provider = FakeProvider(["a", "b"])
        reg = ProviderRegistry()
        reg.register("default", provider)

        node = _make_node(["gpt-4o"], num_votes=2)
        ctx, _ = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        for vote in result.outputs["all_votes"]:
            assert "model" in vote
            assert "text" in vote
            assert "score" in vote

    @pytest.mark.asyncio
    async def test_metadata_fields(self):
        provider = FakeProvider(["ok"])
        reg = ProviderRegistry()
        reg.register("default", provider)

        node = _make_node(["gpt-4o"], num_votes=2)
        ctx, _ = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.metadata["num_votes"] == 2
        assert result.metadata["succeeded"] == 2
        assert result.metadata["strategy"] == "majority"


# ===========================================================================
# System prompt handling
# ===========================================================================


class TestSystemPrompt:
    """System prompt is prepended to messages."""

    @pytest.mark.asyncio
    async def test_system_prompt_included(self):
        provider = FakeProvider(["ok"])
        reg = ProviderRegistry()
        reg.register("default", provider)

        node = _make_node(
            ["gpt-4o"],
            num_votes=1,
            system_prompt="You are a helpful assistant.",
        )
        ctx, _ = _make_context(reg)

        await EXECUTOR.execute(node, {"input": "test"}, ctx)

        messages = provider.calls[0]["messages"]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are a helpful assistant."
        assert messages[1]["role"] == "user"

    @pytest.mark.asyncio
    async def test_no_system_prompt(self):
        provider = FakeProvider(["ok"])
        reg = ProviderRegistry()
        reg.register("default", provider)

        node = _make_node(["gpt-4o"], num_votes=1, system_prompt="")
        ctx, _ = _make_context(reg)

        await EXECUTOR.execute(node, {"input": "test"}, ctx)

        messages = provider.calls[0]["messages"]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"


# ===========================================================================
# Parallelism / semaphore
# ===========================================================================


class TestParallelism:
    """Semaphore controls concurrent LLM calls."""

    @pytest.mark.asyncio
    async def test_parallelism_respected(self):
        """Parallelism=1 → calls are sequential (max 1 concurrent)."""
        concurrent_count = 0
        max_concurrent = 0
        lock = asyncio.Lock()

        class TrackingProvider:
            async def complete(self, messages, model, temperature=0.7, **kw):
                nonlocal concurrent_count, max_concurrent
                async with lock:
                    concurrent_count += 1
                    max_concurrent = max(max_concurrent, concurrent_count)
                await asyncio.sleep(0.01)
                async with lock:
                    concurrent_count -= 1
                return CompletionResult(text="ok", usage={"prompt_tokens": 1, "completion_tokens": 1}, model=model)

            async def stream(self, *a, **kw):
                yield  # pragma: no cover

        reg = ProviderRegistry()
        reg.register("default", TrackingProvider())

        node = _make_node(["gpt-4o"], num_votes=5, parallelism=1)
        ctx, _ = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert max_concurrent == 1


# ===========================================================================
# Timeout enforcement (S1 regression)
# ===========================================================================


class TestVoteTimeout:
    """VoteNode.timeout_seconds is enforced in VoteExecutor."""

    @pytest.mark.asyncio
    async def test_timeout_returns_failed(self):
        """Slow provider exceeds timeout → FAILED."""

        class SlowProvider:
            async def complete(self, messages, model, temperature=0.7, **kw):
                await asyncio.sleep(10)
                return CompletionResult(
                    text="too late",
                    usage={"prompt_tokens": 1, "completion_tokens": 1},
                    model=model,
                )

            async def stream(self, *a, **kw):
                yield  # pragma: no cover

        reg = ProviderRegistry()
        reg.register("default", SlowProvider())

        node = VoteNode(
            id="vote_timeout",
            name="Timeout Vote",
            candidates=["gpt-4o"],
            num_votes=3,
            prompt_template="test {input}",
            vote_strategy="majority",
            timeout_seconds=0.05,
            parallelism=3,
            input_ports=[InputPort(name="input")],
            output_ports=[OutputPort(name="winner")],
        )
        ctx, _ = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.status == NodeStatus.FAILED
        assert "timed out" in result.error.lower()

    @pytest.mark.asyncio
    async def test_no_timeout_completes_normally(self):
        """When timeout_seconds is None, no timeout is applied."""
        provider = FakeProvider(["ok"])
        reg = ProviderRegistry()
        reg.register("default", provider)

        node = VoteNode(
            id="vote_no_timeout",
            name="No Timeout Vote",
            candidates=["gpt-4o"],
            num_votes=3,
            prompt_template="test {input}",
            vote_strategy="majority",
            timeout_seconds=None,
            parallelism=3,
            input_ports=[InputPort(name="input")],
            output_ports=[OutputPort(name="winner")],
        )
        ctx, _ = _make_context(reg)

        result = await EXECUTOR.execute(node, {"input": "test"}, ctx)

        assert result.status == NodeStatus.COMPLETED
