"""Tests for HyperedgeResolver — attachment matching, precedence, and hooks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from dan.engine.hyperedge_runtime import HyperedgeResolver
from dan.models.hyperedges import (
    SCOPE_RANK,
    TYPE_RANK,
    Hyperedge,
    HyperedgeViolation,
    ValidationResult,
)


# ---------------------------------------------------------------------------
# Minimal fakes (avoid importing heavy Graph/Node machinery in unit tests)
# ---------------------------------------------------------------------------


class FakeNode:
    """Mimics NodeBase with the fields HyperedgeResolver reads."""

    def __init__(
        self,
        id: str,
        node_type: str = "llm_operator",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.id = id
        self.node_type = node_type
        self.tags = tags or []
        self.metadata = metadata or {}


class FakeGraph:
    """Mimics Graph with .hyperedges, .nodes, .sub_graphs, .node_by_id."""

    def __init__(
        self,
        hyperedges: list[Hyperedge] | None = None,
        nodes: list[Any] | None = None,
        sub_graphs: dict[str, Any] | None = None,
    ) -> None:
        self.hyperedges = hyperedges or []
        self.nodes = nodes or []
        self.sub_graphs = sub_graphs or {}

    def node_by_id(self, node_id: str) -> Any | None:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None


@dataclass
class FakeResult:
    outputs: dict[str, Any] = field(default_factory=dict)
    status: str = "completed"
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers — build common hyperedge configs
# ---------------------------------------------------------------------------


def _he(
    id: str,
    *,
    htype: str = "guardrail",
    hook: str = "pre_prompt",
    content: str = "test content",
    attach_to: list[str] | None = None,
    attach_to_type: list[str] | None = None,
    attach_to_tags: list[str] | None = None,
    attach_to_subgraph: list[str] | None = None,
    attach_globally: bool = False,
    enabled: bool = True,
    propagate: bool = True,
    priority: int | None = None,
    config: dict[str, Any] | None = None,
) -> Hyperedge:
    return Hyperedge(
        id=id,
        name=id,
        hyperedge_type=htype,
        hook=hook,
        content=content,
        attach_to=attach_to or [],
        attach_to_type=attach_to_type or [],
        attach_to_tags=attach_to_tags or [],
        attach_to_subgraph=attach_to_subgraph or [],
        attach_globally=attach_globally,
        enabled=enabled,
        propagate=propagate,
        priority=priority,
        config=config or {},
    )


# ===================================================================
# 1. Attachment matching
# ===================================================================


class TestAttachmentMatching:
    """Verify all five selector types plus global."""

    def test_attach_globally(self):
        he = _he("g1", attach_globally=True)
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)
        node = FakeNode("n1")
        assert resolver.resolve(node, "pre_prompt") == [he]

    def test_attach_to_node_id(self):
        he = _he("n1_he", attach_to=["n1"])
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)
        assert resolver.resolve(FakeNode("n1"), "pre_prompt") == [he]
        assert resolver.resolve(FakeNode("n2"), "pre_prompt") == []

    def test_attach_to_type(self):
        he = _he("t1", attach_to_type=["llm_operator"])
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)
        assert resolver.resolve(FakeNode("a", node_type="llm_operator"), "pre_prompt") == [he]
        assert resolver.resolve(FakeNode("b", node_type="tool_operator"), "pre_prompt") == []

    def test_attach_to_tags(self):
        he = _he("tag1", attach_to_tags=["important"])
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)
        assert resolver.resolve(FakeNode("a", tags=["important"]), "pre_prompt") == [he]
        assert resolver.resolve(FakeNode("b", tags=["other"]), "pre_prompt") == []

    def test_attach_to_tags_from_metadata(self):
        he = _he("tag_meta", attach_to_tags=["meta_tag"])
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)
        node = FakeNode("a", metadata={"tags": ["meta_tag"]})
        assert resolver.resolve(node, "pre_prompt") == [he]

    def test_attach_to_subgraph(self):
        """Nodes inside a composite's body graph match attach_to_subgraph."""
        child_node = FakeNode("child1")
        child_graph = FakeGraph(nodes=[child_node])

        comp_node = FakeNode("comp1")
        comp_node.body_graph = "comp1_body"

        he = _he("sg1", attach_to_subgraph=["comp1"])
        parent_graph = FakeGraph(
            hyperedges=[he],
            nodes=[comp_node],
            sub_graphs={"comp1_body": child_graph},
        )

        resolver = HyperedgeResolver(parent_graph)
        assert resolver.resolve(child_node, "pre_prompt") == [he]
        assert resolver.resolve(FakeNode("outside"), "pre_prompt") == []

    def test_no_match_without_selector(self):
        """Hyperedge with no matching selector yields no matches."""
        he = _he("x", attach_to=["other_node"])
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)
        assert resolver.resolve(FakeNode("n1"), "pre_prompt") == []

    def test_hook_filtering(self):
        he_pre = _he("pre", hook="pre_prompt", attach_globally=True)
        he_post = _he("post", hook="post_output", attach_globally=True)
        graph = FakeGraph(hyperedges=[he_pre, he_post])
        resolver = HyperedgeResolver(graph)
        assert resolver.resolve(FakeNode("n1"), "pre_prompt") == [he_pre]
        assert resolver.resolve(FakeNode("n1"), "post_output") == [he_post]

    def test_disabled_excluded(self):
        he = _he("dis", attach_globally=True, enabled=False)
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)
        assert resolver.resolve(FakeNode("n1"), "pre_prompt") == []


# ===================================================================
# 2. Precedence sorting
# ===================================================================


class TestPrecedenceSorting:
    """Verify type rank, scope rank, priority, and deterministic id tiebreak."""

    def test_type_rank_ordering(self):
        """override < guardrail < style < skill."""
        he_skill = _he("s", htype="skill", hook="pre_prompt", attach_globally=True)
        he_style = _he("t", htype="style", hook="pre_prompt", attach_globally=True)
        he_guard = _he("g", htype="guardrail", hook="pre_prompt", attach_globally=True)
        he_over = _he("o", htype="override", hook="pre_prompt", attach_globally=True)

        graph = FakeGraph(hyperedges=[he_skill, he_style, he_guard, he_over])
        resolver = HyperedgeResolver(graph)
        result = resolver.resolve(FakeNode("n1"), "pre_prompt")
        assert result == [he_over, he_guard, he_style, he_skill]

    def test_scope_rank_ordering(self):
        """node_id < tag < type < global for same type."""
        he_global = _he("g1", attach_globally=True)
        he_type = _he("g2", attach_to_type=["llm_operator"])
        he_tag = _he("g3", attach_to_tags=["x"])
        he_node = _he("g4", attach_to=["n1"])

        graph = FakeGraph(hyperedges=[he_global, he_type, he_tag, he_node])
        resolver = HyperedgeResolver(graph)

        node = FakeNode("n1", node_type="llm_operator", tags=["x"])
        result = resolver.resolve(node, "pre_prompt")
        assert result == [he_node, he_tag, he_type, he_global]

    def test_priority_ordering(self):
        """Lower priority wins; None sorts last."""
        he1 = _he("a", attach_globally=True, priority=10)
        he2 = _he("b", attach_globally=True, priority=1)
        he3 = _he("c", attach_globally=True, priority=None)

        graph = FakeGraph(hyperedges=[he1, he2, he3])
        resolver = HyperedgeResolver(graph)
        result = resolver.resolve(FakeNode("n1"), "pre_prompt")
        assert result == [he2, he1, he3]

    def test_deterministic_id_tiebreak(self):
        """Same type/scope/priority → sorted by id."""
        he_b = _he("beta", attach_globally=True)
        he_a = _he("alpha", attach_globally=True)

        graph = FakeGraph(hyperedges=[he_b, he_a])
        resolver = HyperedgeResolver(graph)
        result = resolver.resolve(FakeNode("n1"), "pre_prompt")
        assert result == [he_a, he_b]

    def test_cache_returns_same_object(self):
        he = _he("c1", attach_globally=True)
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)
        node = FakeNode("n1")
        first = resolver.resolve(node, "pre_prompt")
        second = resolver.resolve(node, "pre_prompt")
        assert first is second


# ===================================================================
# 3. apply_pre_prompt
# ===================================================================


class TestApplyPrePrompt:
    def test_skill_prepends_system_message(self):
        he = _he("sk1", htype="skill", hook="pre_prompt", attach_globally=True, content="Be creative.")
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)

        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        result, _ = resolver.apply_pre_prompt(FakeNode("n1"), msgs)
        assert len(result) == 3
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "Be creative."
        assert result[1]["content"] == "You are helpful."

    def test_skill_adds_system_when_missing(self):
        he = _he("sk2", htype="skill", hook="pre_prompt", attach_globally=True, content="Be precise.")
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)

        msgs = [{"role": "user", "content": "Hello"}]
        result, _ = resolver.apply_pre_prompt(FakeNode("n1"), msgs)
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "Be precise."

    def test_style_appends_to_system(self):
        he = _he("st1", htype="style", hook="pre_prompt", attach_globally=True, content="Use formal tone.")
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)

        msgs = [
            {"role": "system", "content": "Base instructions."},
            {"role": "user", "content": "Hello"},
        ]
        result, _ = resolver.apply_pre_prompt(FakeNode("n1"), msgs)
        assert len(result) == 2
        assert "Base instructions." in result[0]["content"]
        assert "Use formal tone." in result[0]["content"]

    def test_guardrail_appends_constraint(self):
        he = _he("gr1", htype="guardrail", hook="pre_prompt", attach_globally=True, content="No PII.")
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)

        msgs = [
            {"role": "system", "content": "System."},
            {"role": "user", "content": "Query"},
        ]
        result, _ = resolver.apply_pre_prompt(FakeNode("n1"), msgs)
        assert "[Constraint] No PII." in result[0]["content"]

    def test_guardrail_creates_system_when_missing(self):
        he = _he("gr2", htype="guardrail", hook="pre_prompt", attach_globally=True, content="Safe only.")
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)

        msgs = [{"role": "user", "content": "Query"}]
        result, _ = resolver.apply_pre_prompt(FakeNode("n1"), msgs)
        assert result[0]["role"] == "system"
        assert "[Constraint] Safe only." in result[0]["content"]

    def test_does_not_mutate_original(self):
        he = _he("sk3", htype="skill", hook="pre_prompt", attach_globally=True, content="X")
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)

        original = [{"role": "user", "content": "Hello"}]
        result, _ = resolver.apply_pre_prompt(FakeNode("n1"), original)
        assert len(original) == 1
        assert len(result) == 2

    def test_no_hyperedges_returns_messages_unchanged(self):
        graph = FakeGraph(hyperedges=[])
        resolver = HyperedgeResolver(graph)

        msgs = [{"role": "user", "content": "Hi"}]
        result, jit = resolver.apply_pre_prompt(FakeNode("n1"), msgs)
        assert result == msgs
        assert jit is False


# ===================================================================
# 4. apply_post_output
# ===================================================================


class TestApplyPostOutput:
    def test_warn_mode_returns_violations(self):
        he = _he(
            "po1",
            hook="post_output",
            content="len(text) > 100",
            attach_globally=True,
            config={"severity": "warning"},
        )
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)

        result = FakeResult(outputs={"text": "short"})
        new_result, violations = resolver.apply_post_output(FakeNode("n1"), result, "warn")
        assert new_result is result
        assert len(violations) == 1
        assert not violations[0].passed

    def test_passing_rule(self):
        he = _he(
            "po2",
            hook="post_output",
            content="len(text) > 2",
            attach_globally=True,
        )
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)

        result = FakeResult(outputs={"text": "hello world"})
        _, violations = resolver.apply_post_output(FakeNode("n1"), result, "warn")
        assert all(v.passed for v in violations)

    def test_strict_mode_raises_on_block(self):
        he = _he(
            "po3",
            hook="post_output",
            content="score > 0.5",
            attach_globally=True,
            config={"block_on_fail": True, "severity": "error"},
        )
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)

        result = FakeResult(outputs={"score": 0.1})
        with pytest.raises(HyperedgeViolation) as exc_info:
            resolver.apply_post_output(FakeNode("n1"), result, "strict")
        assert exc_info.value.node_id == "n1"

    def test_strict_without_block_flag_does_not_raise(self):
        he = _he(
            "po4",
            hook="post_output",
            content="score > 0.5",
            attach_globally=True,
            config={"block_on_fail": False},
        )
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)

        result = FakeResult(outputs={"score": 0.1})
        _, violations = resolver.apply_post_output(FakeNode("n1"), result, "strict")
        assert not violations[0].passed

    def test_evaluation_error_is_caught(self):
        he = _he(
            "po5",
            hook="post_output",
            content="undefined_var > 0",
            attach_globally=True,
        )
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)

        result = FakeResult(outputs={})
        _, violations = resolver.apply_post_output(FakeNode("n1"), result, "warn")
        assert len(violations) == 1
        assert not violations[0].passed
        assert "Evaluation error" in violations[0].message


# ===================================================================
# 5. apply_tool_call
# ===================================================================


class TestApplyToolCall:
    def test_override_modifies_args(self):
        import json

        spec = json.dumps({"args": {"timeout": 30}})
        he = _he(
            "tc1",
            htype="override",
            hook="tool_call",
            content=spec,
            attach_globally=True,
        )
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)

        tid, args, allow = resolver.apply_tool_call(
            FakeNode("n1"), "my_tool", {"key": "val"}
        )
        assert tid == "my_tool"
        assert args["timeout"] == 30
        assert args["key"] == "val"
        assert allow is True

    def test_override_replaces_tool_id(self):
        import json

        spec = json.dumps({"tool_id": "new_tool"})
        he = _he(
            "tc2",
            htype="override",
            hook="tool_call",
            content=spec,
            attach_globally=True,
        )
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)

        tid, _, allow = resolver.apply_tool_call(FakeNode("n1"), "old_tool", {})
        assert tid == "new_tool"
        assert allow is True

    def test_override_blocks(self):
        import json

        spec = json.dumps({"block": True})
        he = _he(
            "tc3",
            htype="override",
            hook="tool_call",
            content=spec,
            attach_globally=True,
        )
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)

        _, _, allow = resolver.apply_tool_call(FakeNode("n1"), "t", {})
        assert allow is False

    def test_guardrail_blocks_on_fail(self):
        he = _he(
            "tc4",
            htype="guardrail",
            hook="tool_call",
            content="tool_id != 'dangerous_tool'",
            attach_globally=True,
            config={"block_on_fail": True},
        )
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)

        _, _, allow = resolver.apply_tool_call(FakeNode("n1"), "dangerous_tool", {})
        assert allow is False

    def test_guardrail_passes(self):
        he = _he(
            "tc5",
            htype="guardrail",
            hook="tool_call",
            content="tool_id != 'dangerous_tool'",
            attach_globally=True,
            config={"block_on_fail": True},
        )
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)

        _, _, allow = resolver.apply_tool_call(FakeNode("n1"), "safe_tool", {})
        assert allow is True

    def test_no_hyperedges_allows(self):
        graph = FakeGraph(hyperedges=[])
        resolver = HyperedgeResolver(graph)
        tid, args, allow = resolver.apply_tool_call(FakeNode("n1"), "t", {"k": "v"})
        assert tid == "t"
        assert args == {"k": "v"}
        assert allow is True


# ===================================================================
# 6. apply_validation
# ===================================================================


class TestApplyValidation:
    def test_expression_pass(self):
        he = _he(
            "v1",
            hook="validation",
            content="len(items) > 0",
            attach_globally=True,
        )
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)

        results = resolver.apply_validation(FakeNode("n1"), {"items": [1, 2]}, "warn")
        assert len(results) == 1
        assert results[0].passed

    def test_expression_fail(self):
        he = _he(
            "v2",
            hook="validation",
            content="score >= 0.8",
            attach_globally=True,
        )
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)

        results = resolver.apply_validation(FakeNode("n1"), {"score": 0.3}, "warn")
        assert not results[0].passed
        assert "Validation failed" in results[0].message

    def test_strict_raises_on_block(self):
        he = _he(
            "v3",
            hook="validation",
            content="count > 5",
            attach_globally=True,
            config={"block_on_fail": True, "severity": "error"},
        )
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)

        with pytest.raises(HyperedgeViolation):
            resolver.apply_validation(FakeNode("n1"), {"count": 2}, "strict")

    def test_strict_without_block_does_not_raise(self):
        he = _he(
            "v4",
            hook="validation",
            content="count > 5",
            attach_globally=True,
            config={"block_on_fail": False},
        )
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)

        results = resolver.apply_validation(FakeNode("n1"), {"count": 2}, "strict")
        assert not results[0].passed

    def test_eval_error_caught(self):
        he = _he(
            "v5",
            hook="validation",
            content="nonexistent_fn(x)",
            attach_globally=True,
        )
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)

        results = resolver.apply_validation(FakeNode("n1"), {}, "warn")
        assert not results[0].passed
        assert "Validation error" in results[0].message

    def test_empty_returns_empty(self):
        graph = FakeGraph(hyperedges=[])
        resolver = HyperedgeResolver(graph)
        assert resolver.apply_validation(FakeNode("n1"), {}, "warn") == []


# ===================================================================
# 7. Propagation (parent → child, child override)
# ===================================================================


class TestPropagation:
    def test_parent_propagates_to_child(self):
        parent_he = _he("p1", attach_globally=True, propagate=True)
        parent_graph = FakeGraph(hyperedges=[parent_he])
        parent_resolver = HyperedgeResolver(parent_graph)

        child_graph = FakeGraph(hyperedges=[])
        child_resolver = HyperedgeResolver(
            child_graph, parent_hyperedges=parent_resolver.active_hyperedges,
        )

        result = child_resolver.resolve(FakeNode("c1"), "pre_prompt")
        assert len(result) == 1
        assert result[0].id == "p1"

    def test_parent_no_propagate_excluded(self):
        parent_he = _he("p2", attach_globally=True, propagate=False)
        parent_graph = FakeGraph(hyperedges=[parent_he])
        parent_resolver = HyperedgeResolver(parent_graph)

        child_graph = FakeGraph(hyperedges=[])
        child_resolver = HyperedgeResolver(
            child_graph, parent_hyperedges=parent_resolver.active_hyperedges,
        )

        assert child_resolver.resolve(FakeNode("c1"), "pre_prompt") == []

    def test_child_overrides_parent_same_id(self):
        parent_he = _he("shared_id", attach_globally=True, content="parent version")
        parent_graph = FakeGraph(hyperedges=[parent_he])
        parent_resolver = HyperedgeResolver(parent_graph)

        child_he = _he("shared_id", attach_globally=True, content="child version")
        child_graph = FakeGraph(hyperedges=[child_he])
        child_resolver = HyperedgeResolver(
            child_graph, parent_hyperedges=parent_resolver.active_hyperedges,
        )

        result = child_resolver.resolve(FakeNode("c1"), "pre_prompt")
        assert len(result) == 1
        assert result[0].content == "child version"

    def test_parent_and_child_merge(self):
        parent_he = _he("p_only", attach_globally=True, content="parent")
        parent_graph = FakeGraph(hyperedges=[parent_he])
        parent_resolver = HyperedgeResolver(parent_graph)

        child_he = _he("c_only", attach_globally=True, content="child")
        child_graph = FakeGraph(hyperedges=[child_he])
        child_resolver = HyperedgeResolver(
            child_graph, parent_hyperedges=parent_resolver.active_hyperedges,
        )

        result = child_resolver.resolve(FakeNode("c1"), "pre_prompt")
        ids = {he.id for he in result}
        assert ids == {"p_only", "c_only"}


# ===================================================================
# 8. Backward compatibility
# ===================================================================


class TestBackwardCompat:
    def test_empty_hyperedges(self):
        graph = FakeGraph(hyperedges=[])
        resolver = HyperedgeResolver(graph)
        node = FakeNode("n1")

        assert resolver.resolve(node, "pre_prompt") == []
        msgs, _ = resolver.apply_pre_prompt(node, [{"role": "user", "content": "Hi"}])
        assert msgs == [{"role": "user", "content": "Hi"}]
        result = FakeResult(outputs={"x": 1})
        r, v = resolver.apply_post_output(node, result)
        assert r is result
        assert v == []
        assert resolver.apply_tool_call(node, "t", {}) == ("t", {}, True)
        assert resolver.apply_validation(node, {}) == []

    def test_enforcement_off_skips_hooks(self):
        """With enforcement='off', the resolver still works but callers skip it."""
        he = _he("e1", attach_globally=True)
        graph = FakeGraph(hyperedges=[he])
        resolver = HyperedgeResolver(graph)

        result = resolver.resolve(FakeNode("n1"), "pre_prompt")
        assert len(result) == 1

    def test_active_hyperedges_property(self):
        he1 = _he("a1", attach_globally=True)
        he2 = _he("a2", attach_globally=True, enabled=False)
        graph = FakeGraph(hyperedges=[he1, he2])
        resolver = HyperedgeResolver(graph)

        active = resolver.active_hyperedges
        assert len(active) == 1
        assert active[0].id == "a1"
        assert active is not resolver._hyperedges
