"""Tests for Hyperedge JIT loading (Plan 18-1 task 7-5)."""

from __future__ import annotations

from typing import Any

import pytest

from dan.engine.hyperedge_runtime import (
    HYPEREDGE_JIT_TOOL,
    JIT_SYSTEM_INSTRUCTION,
    HyperedgeResolver,
)
from dan.models.hyperedges import Hyperedge


# ---------------------------------------------------------------------------
# Minimal fakes
# ---------------------------------------------------------------------------


class FakeNode:
    def __init__(
        self,
        id: str,
        node_type: str = "llm_operator",
        tags: list[str] | None = None,
    ) -> None:
        self.id = id
        self.node_type = node_type
        self.tags = tags or []


class FakeGraph:
    def __init__(
        self,
        hyperedges: list[Hyperedge] | None = None,
        nodes: list[Any] | None = None,
        sub_graphs: dict[str, Any] | None = None,
    ) -> None:
        self.hyperedges = hyperedges or []
        self.nodes = nodes or []
        self.sub_graphs = sub_graphs or {}


def _he(
    id: str,
    *,
    name: str | None = None,
    htype: str = "skill",
    hook: str = "pre_prompt",
    content: str = "test content",
    attach_globally: bool = True,
) -> Hyperedge:
    return Hyperedge(
        id=id,
        name=name or id,
        hyperedge_type=htype,
        hook=hook,
        content=content,
        attach_to=[],
        attach_to_type=[],
        attach_to_tags=[],
        attach_to_subgraph=[],
        attach_globally=attach_globally,
        enabled=True,
        propagate=True,
    )


# ===================================================================
# 1. Summary generation
# ===================================================================


class TestSummarizeHyperedge:
    def test_summary_produces_compact_output(self):
        he = _he("s1", content="Always be concise. Use short sentences. Avoid jargon.")
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)
        summary = resolver._summarize_hyperedge(he)
        assert "[skill: s1]" in summary
        assert "Always be concise" in summary
        assert len(summary) < 150

    def test_summary_uses_name_when_content_empty(self):
        he = _he("empty", content="")
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)
        summary = resolver._summarize_hyperedge(he)
        assert "[skill: empty] empty" == summary

    def test_summary_truncates_long_first_sentence(self):
        he = _he("long", content="A" * 200 + ". Second sentence.")
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)
        summary = resolver._summarize_hyperedge(he)
        assert len(summary) <= 120  # [skill: long] + first 100 chars


# ===================================================================
# 2. JIT mode injects summaries
# ===================================================================


class TestJitModeInjectsSummaries:
    def test_jit_mode_injects_summary_for_large_hyperedge(self):
        # ~250 tokens (chars/4) - exceeds default 500? No, 250*4=1000 chars. estimate_tokens uses len/4 so 1000 chars = 250 tokens. Below 500.
        # Use longer content to exceed 500 tokens: 500*4 = 2000 chars
        long_content = "Rule one: do X. " * 150  # ~3000 chars -> ~750 tokens
        he = _he("big", content=long_content, htype="guardrail")
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)

        msgs = [{"role": "user", "content": "Hi"}]
        result, jit_needed = resolver.apply_pre_prompt(
            FakeNode("n1"), msgs, jit_loading=True, jit_threshold=500,
        )
        assert jit_needed is True
        assert "[guardrail: big]" in result[0]["content"]
        assert long_content not in result[0]["content"]
        assert JIT_SYSTEM_INSTRUCTION in result[0]["content"]

    def test_jit_mode_injects_full_content_for_small_hyperedge(self):
        he = _he("small", content="Be brief.")
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)

        msgs = [{"role": "user", "content": "Hi"}]
        result, jit_needed = resolver.apply_pre_prompt(
            FakeNode("n1"), msgs, jit_loading=True, jit_threshold=500,
        )
        assert jit_needed is False
        assert "Be brief." in result[0]["content"]
        assert JIT_SYSTEM_INSTRUCTION not in result[0]["content"]


# ===================================================================
# 3. Non-JIT mode unchanged
# ===================================================================


class TestNonJitModeUnchanged:
    def test_jit_loading_false_injects_full_content(self):
        long_content = "A" * 3000  # ~750 tokens
        he = _he("big", content=long_content)
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)

        msgs = [{"role": "user", "content": "Hi"}]
        result, jit_needed = resolver.apply_pre_prompt(
            FakeNode("n1"), msgs, jit_loading=False, jit_threshold=500,
        )
        assert jit_needed is False
        assert long_content in result[0]["content"]
        assert JIT_SYSTEM_INSTRUCTION not in result[0]["content"]


# ===================================================================
# 4. Threshold logic
# ===================================================================


class TestThresholdLogic:
    def test_small_rules_injected_fully(self):
        he = _he("tiny", content="X")
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)
        result, jit = resolver.apply_pre_prompt(
            FakeNode("n1"), [{"role": "user", "content": "Hi"}],
            jit_loading=True, jit_threshold=500,
        )
        assert jit is False
        assert "X" in result[0]["content"]

    def test_large_rules_summarized(self):
        big = "word " * 600  # ~3000 chars -> ~750 tokens, exceeds 500
        he = _he("large", content=big)
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)
        result, jit = resolver.apply_pre_prompt(
            FakeNode("n1"), [{"role": "user", "content": "Hi"}],
            jit_loading=True, jit_threshold=500,
        )
        assert jit is True
        assert "[skill: large]" in result[0]["content"]
        assert big not in result[0]["content"]


# ===================================================================
# 5. load_hyperedge tool
# ===================================================================


class TestLoadHyperedgeTool:
    def test_tool_schema_structure(self):
        assert HYPEREDGE_JIT_TOOL["type"] == "function"
        assert HYPEREDGE_JIT_TOOL["function"]["name"] == "load_hyperedge"
        assert "name" in HYPEREDGE_JIT_TOOL["function"]["parameters"]["required"]

    def test_get_hyperedge_content_returns_correct_content(self):
        he = _he("my_skill", name="my_skill", content="Full skill instructions here.")
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)

        content = resolver.get_hyperedge_content("my_skill")
        assert content == "Full skill instructions here."

    def test_get_hyperedge_content_returns_none_for_unknown(self):
        graph = FakeGraph(hyperedges=[])
        resolver = HyperedgeResolver(graph)
        assert resolver.get_hyperedge_content("nonexistent") is None

    def test_get_hyperedge_content_by_name_not_id(self):
        he = _he("id_1", name="DisplayName", content="Content")
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)
        assert resolver.get_hyperedge_content("DisplayName") == "Content"
        assert resolver.get_hyperedge_content("id_1") is None
