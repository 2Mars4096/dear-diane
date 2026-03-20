from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from dan.meta.generation_defaults import (
    DefaultProfile,
    DefaultsEnricher,
    DomainGenerationProfile,
    GenerationDefaults,
    build_domain_prompt_context,
    clear_profile_cache,
    detect_suppressions,
    get_domain_profile,
)


def test_robust_profile_enriches_graph_end_to_end() -> None:
    graph = {
        "nodes": [
            {
                "id": "fetch",
                "node_type": "tool_operator",
                "config": {"tool_id": "web_search"},
            },
            {
                "id": "draft",
                "node_type": "llm_operator",
                "config": {"prompt_template": "Write a first draft of the article"},
            },
            {
                "id": "revise",
                "node_type": "llm_operator",
                "config": {"prompt_template": "Refine the article draft"},
            },
            {
                "id": "summarize",
                "node_type": "llm_operator",
                "config": {"prompt_template": "Summarize the refined article"},
            },
            {
                "id": "final",
                "node_type": "llm_operator",
                "config": {"prompt_template": "Write the final article for publication"},
            },
        ],
        "edges": [
            {
                "source_node_id": "fetch",
                "source_port": "results",
                "target_node_id": "draft",
                "target_port": "context",
            },
            {
                "source_node_id": "draft",
                "source_port": "text",
                "target_node_id": "revise",
                "target_port": "text",
            },
            {
                "source_node_id": "revise",
                "source_port": "text",
                "target_node_id": "summarize",
                "target_port": "text",
            },
            {
                "source_node_id": "summarize",
                "source_port": "text",
                "target_node_id": "final",
                "target_port": "text",
            },
        ],
    }

    enricher = DefaultsEnricher(
        GenerationDefaults.from_profile(DefaultProfile.robust)
    )
    enriched = enricher.enrich(deepcopy(graph))

    tool_node = next(node for node in enriched["nodes"] if node["id"] == "fetch")
    llm_nodes = [
        node for node in enriched["nodes"] if node["node_type"] == "llm_operator"
    ]
    original_llm_nodes = [
        node
        for node in llm_nodes
        if node.get("id") in {"draft", "revise", "summarize", "final"}
    ]
    validator_nodes = [
        node for node in enriched["nodes"] if node["node_type"] == "validator"
    ]
    reviewer_nodes = [
        node
        for node in llm_nodes
        if str(node.get("config", {}).get("name", "")).startswith("review_")
    ]

    assert tool_node["config"]["retry_policy"]["max_retries"] == 3
    assert all("retry_policy" in node["config"] for node in original_llm_nodes)
    assert [node["config"].get("model_tier") for node in original_llm_nodes] == [
        "routine",
        "standard",
        "standard",
        "premium",
    ]
    assert len(validator_nodes) == 1
    assert len(reviewer_nodes) == 1
    assert isinstance(enriched["edges"], list)
    assert any(
        edge["target_node_id"] == validator_nodes[0]["id"]
        for edge in enriched["edges"]
    )
    assert any(
        edge["target_node_id"] == reviewer_nodes[0]["id"]
        for edge in enriched["edges"]
    )


class TestDefaultProfile:
    def test_minimal_disables_all(self):
        d = GenerationDefaults.from_profile(DefaultProfile.minimal)
        assert not d.retry_on_llm
        assert not d.retry_on_tools
        assert not d.validation_gate
        assert not d.review_on_content

    def test_standard_has_retry_and_validation(self):
        d = GenerationDefaults.from_profile(DefaultProfile.standard)
        assert d.retry_on_llm
        assert d.retry_on_tools
        assert d.validation_gate
        assert not d.review_on_content

    def test_robust_enables_all(self):
        d = GenerationDefaults.from_profile(DefaultProfile.robust)
        assert d.retry_on_llm
        assert d.validation_gate
        assert d.review_on_content
        assert d.model_tiering
        assert d.error_notification


class TestSuppressionDetection:
    def test_no_suppression(self):
        result = detect_suppressions("Build a research workflow")
        assert result == {}

    def test_no_review(self):
        result = detect_suppressions("Build a simple chain without review")
        assert result.get("review_on_content") is False

    def test_skip_validation(self):
        result = detect_suppressions("Skip validation on this pipeline")
        assert result.get("validation_gate") is False

    def test_same_model(self):
        result = detect_suppressions("Use the same model for all nodes")
        assert result.get("model_tiering") is False


class TestDefaultsEnricher:
    def _make_graph(self, nodes):
        return {"nodes": nodes, "edges": {"data": [], "control": [], "context": []}}

    def test_minimal_no_changes(self):
        enricher = DefaultsEnricher(GenerationDefaults.from_profile(DefaultProfile.minimal))
        graph = self._make_graph([{"node_type": "llm_operator", "config": {}}])
        result = enricher.enrich(graph)
        assert "retry_policy" not in result["nodes"][0].get("config", {})

    def test_standard_adds_llm_retry(self):
        enricher = DefaultsEnricher()
        graph = self._make_graph([{"node_type": "llm_operator", "config": {}}])
        result = enricher.enrich(graph)
        assert "retry_policy" in result["nodes"][0]["config"]

    def test_existing_retry_not_overridden(self):
        enricher = DefaultsEnricher()
        custom_policy = {"max_retries": 5}
        graph = self._make_graph([{"node_type": "llm_operator", "config": {"retry_policy": custom_policy}}])
        result = enricher.enrich(graph)
        assert result["nodes"][0]["config"]["retry_policy"]["max_retries"] == 5

    def test_tool_retry_only_external(self):
        enricher = DefaultsEnricher()
        graph = self._make_graph([
            {"node_type": "tool_operator", "config": {"tool_id": "web_search"}},
            {"node_type": "tool_operator", "config": {"tool_id": "file_read"}},
        ])
        result = enricher.enrich(graph)
        assert "retry_policy" in result["nodes"][0]["config"]
        assert "retry_policy" not in result["nodes"][1].get("config", {})

    def test_model_tiering_requires_4_nodes(self):
        enricher = DefaultsEnricher(GenerationDefaults.from_profile(DefaultProfile.robust))
        nodes = [{"node_type": "llm_operator", "config": {}} for _ in range(4)]
        graph = self._make_graph(nodes)
        result = enricher.enrich(graph)
        tiers = [n["config"].get("model_tier") for n in result["nodes"]]
        assert "routine" in tiers
        assert "premium" in tiers

    def test_has_content_workflow(self):
        nodes = [{"node_type": "llm_operator", "config": {"prompt_template": "Write a report about X"}}]
        assert DefaultsEnricher.has_content_workflow(nodes)

    def test_no_content_workflow(self):
        nodes = [{"node_type": "llm_operator", "config": {"prompt_template": "Parse this JSON"}}]
        assert not DefaultsEnricher.has_content_workflow(nodes)


class TestValidationGateTopologies:
    """Tests for _ensure_validation_gate with various graph topologies."""

    def _make_graph(self, nodes, edges):
        return {"nodes": nodes, "edges": {"data": edges, "control": [], "context": []}}

    def test_linear_chain_inserts_before_terminal_llm(self):
        """A -> B (llm): validator should be inserted before B."""
        enricher = DefaultsEnricher(GenerationDefaults(validation_gate=True, retry_on_llm=False, retry_on_tools=False))
        graph = self._make_graph(
            [
                {"id": "a", "node_type": "tool_operator", "config": {}},
                {"id": "b", "node_type": "llm_operator", "config": {}},
            ],
            [{"source_node_id": "a", "source_port": "result", "target_node_id": "b", "target_port": "text"}],
        )
        result = enricher.enrich(graph)
        node_types = [n["node_type"] for n in result["nodes"]]
        assert "validator" in node_types
        validator = next(n for n in result["nodes"] if n["node_type"] == "validator")
        val_to_b = [e for e in result["edges"] if e["source_node_id"] == validator["id"] and e["target_node_id"] == "b"]
        assert len(val_to_b) == 1

    def test_branching_graph_finds_correct_sink(self):
        """A -> B, A -> C (llm): C is a terminal LLM node, B is not (non-LLM sink)."""
        enricher = DefaultsEnricher(GenerationDefaults(validation_gate=True, retry_on_llm=False, retry_on_tools=False))
        graph = self._make_graph(
            [
                {"id": "a", "node_type": "tool_operator", "config": {}},
                {"id": "b", "node_type": "tool_operator", "config": {}},
                {"id": "c", "node_type": "llm_operator", "config": {}},
            ],
            [
                {"source_node_id": "a", "source_port": "r", "target_node_id": "b", "target_port": "d"},
                {"source_node_id": "a", "source_port": "r", "target_node_id": "c", "target_port": "text"},
            ],
        )
        result = enricher.enrich(graph)
        validator = next((n for n in result["nodes"] if n["node_type"] == "validator"), None)
        assert validator is not None
        assert validator["config"]["name"] == "validate_before_c"

    def test_no_terminal_llm_skips_insertion(self):
        """A (tool) -> B (tool): no terminal LLM node, no validator inserted."""
        enricher = DefaultsEnricher(GenerationDefaults(validation_gate=True, retry_on_llm=False, retry_on_tools=False))
        graph = self._make_graph(
            [
                {"id": "a", "node_type": "tool_operator", "config": {}},
                {"id": "b", "node_type": "tool_operator", "config": {}},
            ],
            [{"source_node_id": "a", "source_port": "r", "target_node_id": "b", "target_port": "d"}],
        )
        result = enricher.enrich(graph)
        assert not any(n["node_type"] == "validator" for n in result["nodes"])

    def test_existing_validator_skips(self):
        """Graph already has a validator — should not insert another."""
        enricher = DefaultsEnricher(GenerationDefaults(validation_gate=True, retry_on_llm=False, retry_on_tools=False))
        graph = self._make_graph(
            [
                {"id": "a", "node_type": "tool_operator", "config": {}},
                {"id": "v", "node_type": "validator", "config": {}},
                {"id": "b", "node_type": "llm_operator", "config": {}},
            ],
            [
                {"source_node_id": "a", "source_port": "r", "target_node_id": "v", "target_port": "d"},
                {"source_node_id": "v", "source_port": "valid", "target_node_id": "b", "target_port": "text"},
            ],
        )
        result = enricher.enrich(graph)
        validators = [n for n in result["nodes"] if n["node_type"] == "validator"]
        assert len(validators) == 1

    def test_diamond_graph_picks_llm_sink(self):
        """A -> B -> D (llm), A -> C -> D (llm): D is the only sink."""
        enricher = DefaultsEnricher(GenerationDefaults(validation_gate=True, retry_on_llm=False, retry_on_tools=False))
        graph = self._make_graph(
            [
                {"id": "a", "node_type": "tool_operator", "config": {}},
                {"id": "b", "node_type": "llm_operator", "config": {}},
                {"id": "c", "node_type": "llm_operator", "config": {}},
                {"id": "d", "node_type": "llm_operator", "config": {}},
            ],
            [
                {"source_node_id": "a", "source_port": "r", "target_node_id": "b", "target_port": "text"},
                {"source_node_id": "a", "source_port": "r", "target_node_id": "c", "target_port": "text"},
                {"source_node_id": "b", "source_port": "text", "target_node_id": "d", "target_port": "text"},
                {"source_node_id": "c", "source_port": "text", "target_node_id": "d", "target_port": "text"},
            ],
        )
        result = enricher.enrich(graph)
        validator = next((n for n in result["nodes"] if n["node_type"] == "validator"), None)
        assert validator is not None
        assert validator["config"]["name"] == "validate_before_d"


class TestReviewOnContentTopologies:
    """Tests for _ensure_review_on_content with various graph topologies."""

    def _make_graph(self, nodes, edges):
        return {"nodes": nodes, "edges": {"data": edges, "control": [], "context": []}}

    def test_side_branch_preserves_downstream_flow(self):
        """Content -> Consumer: reviewer should be a side branch, not interrupt flow."""
        enricher = DefaultsEnricher(GenerationDefaults(
            review_on_content=True, validation_gate=False,
            retry_on_llm=False, retry_on_tools=False,
        ))
        graph = self._make_graph(
            [
                {"id": "writer", "node_type": "llm_operator", "config": {"prompt_template": "Write a report"}},
                {"id": "consumer", "node_type": "llm_operator", "config": {"prompt_template": "Summarize"}},
            ],
            [{"source_node_id": "writer", "source_port": "text", "target_node_id": "consumer", "target_port": "text"}],
        )
        result = enricher.enrich(graph)

        reviewer = next((n for n in result["nodes"] if "review" in n.get("config", {}).get("name", "")), None)
        assert reviewer is not None

        writer_to_consumer = [
            e for e in result["edges"]
            if e["source_node_id"] == "writer" and e["target_node_id"] == "consumer"
        ]
        assert len(writer_to_consumer) == 1, "Original content flow should be preserved"

        writer_to_reviewer = [
            e for e in result["edges"]
            if e["source_node_id"] == "writer" and e["target_node_id"] == reviewer["id"]
        ]
        assert len(writer_to_reviewer) == 1, "Reviewer should receive content as side branch"

    def test_skips_when_review_loop_exists(self):
        """Graph with an existing while_loop should not add a reviewer."""
        enricher = DefaultsEnricher(GenerationDefaults(
            review_on_content=True, validation_gate=False,
            retry_on_llm=False, retry_on_tools=False,
        ))
        graph = self._make_graph(
            [
                {"id": "writer", "node_type": "llm_operator", "config": {"prompt_template": "Write a draft"}},
                {"id": "loop", "node_type": "while_loop", "config": {"name": "review_loop"}},
            ],
            [{"source_node_id": "writer", "source_port": "text", "target_node_id": "loop", "target_port": "data"}],
        )
        result = enricher.enrich(graph)
        assert len(result["nodes"]) == 2

    def test_content_node_without_id_is_skipped(self):
        """Node missing 'id' should not crash the enricher."""
        enricher = DefaultsEnricher(GenerationDefaults(
            review_on_content=True, validation_gate=False,
            retry_on_llm=False, retry_on_tools=False,
        ))
        graph = self._make_graph(
            [{"node_type": "llm_operator", "config": {"prompt_template": "Write an essay"}}],
            [],
        )
        result = enricher.enrich(graph)
        assert len(result["nodes"]) == 1


class TestSuppressionWithEnricher:
    """Negative tests: user suppression phrases should disable enricher features."""

    def _make_content_graph(self):
        return {
            "nodes": [
                {"id": "a", "node_type": "tool_operator", "config": {}},
                {"id": "b", "node_type": "llm_operator", "config": {"prompt_template": "Write a report"}},
            ],
            "edges": {
                "data": [
                    {"source_node_id": "a", "source_port": "r", "target_node_id": "b", "target_port": "text"},
                ],
                "control": [],
                "context": [],
            },
        }

    def test_no_review_suppresses_reviewer(self):
        """'without review' should suppress review_on_content."""
        suppressions = detect_suppressions("Build a data pipeline without review")
        defaults = GenerationDefaults.from_profile(DefaultProfile.robust)
        for field, value in suppressions.items():
            setattr(defaults, field, value)

        enricher = DefaultsEnricher(defaults)
        graph = self._make_content_graph()
        result = enricher.enrich(graph)
        assert not any("review" in n.get("config", {}).get("name", "") for n in result["nodes"])

    def test_skip_validation_suppresses_gate(self):
        """'skip validation' should suppress validation_gate."""
        suppressions = detect_suppressions("Generate a quick script, skip validation")
        defaults = GenerationDefaults.from_profile(DefaultProfile.robust)
        for field, value in suppressions.items():
            setattr(defaults, field, value)

        enricher = DefaultsEnricher(defaults)
        graph = self._make_content_graph()
        result = enricher.enrich(graph)
        assert not any(n["node_type"] == "validator" for n in result["nodes"])

    def test_simple_does_not_suppress_review(self):
        """After M-4 fix: 'simple' alone should NOT suppress review_on_content."""
        suppressions = detect_suppressions("Build a simple data pipeline")
        assert "review_on_content" not in suppressions

    def test_minimal_suppresses_review(self):
        """'minimal' should suppress review_on_content."""
        suppressions = detect_suppressions("Build a minimal pipeline")
        assert suppressions.get("review_on_content") is False


class TestEnricherPipelineIntegration:
    """Integration test: domain -> profile -> defaults -> enricher -> graph."""

    def test_domain_profile_to_enricher_end_to_end(self):
        """Simulate the planner's enricher pipeline: domain -> profile -> defaults -> enrich."""
        clear_profile_cache()
        domain = "literature_review"

        dp = get_domain_profile(domain)
        assert dp is not None

        gen_defaults = GenerationDefaults.from_profile(DefaultProfile(dp.default_profile))

        user_text = "Research and summarize recent papers"
        suppressions = detect_suppressions(user_text)
        for field, value in suppressions.items():
            setattr(gen_defaults, field, value)

        enricher = DefaultsEnricher(gen_defaults)

        graph = {
            "nodes": [
                {"id": "search", "node_type": "tool_operator", "config": {"tool_id": "web_search"}},
                {"id": "analyze", "node_type": "llm_operator", "config": {"prompt_template": "Analyze the papers"}},
                {"id": "synthesize", "node_type": "llm_operator", "config": {"prompt_template": "Write a report"}},
            ],
            "edges": {
                "data": [
                    {"source_node_id": "search", "source_port": "result", "target_node_id": "analyze", "target_port": "text"},
                    {"source_node_id": "analyze", "source_port": "text", "target_node_id": "synthesize", "target_port": "text"},
                ],
                "control": [],
                "context": [],
            },
        }

        original_ids = {n["id"] for n in graph["nodes"]}
        result = enricher.enrich(graph)

        if gen_defaults.retry_on_llm:
            for n in result["nodes"]:
                if n["node_type"] == "llm_operator" and n.get("id") in original_ids:
                    assert "retry_policy" in n["config"]

        if gen_defaults.retry_on_tools:
            search_node = next(n for n in result["nodes"] if n["id"] == "search")
            assert "retry_policy" in search_node["config"]

        if gen_defaults.validation_gate:
            assert any(n["node_type"] == "validator" for n in result["nodes"])

    def test_domain_profile_with_suppression(self):
        """User suppression should override domain profile settings."""
        clear_profile_cache()
        domain = "literature_review"
        dp = get_domain_profile(domain)
        assert dp is not None
        gen_defaults = GenerationDefaults.from_profile(DefaultProfile(dp.default_profile))

        user_text = "Quick search, skip validation, no retry"
        suppressions = detect_suppressions(user_text)
        for field, value in suppressions.items():
            setattr(gen_defaults, field, value)

        assert gen_defaults.validation_gate is False
        assert gen_defaults.retry_on_llm is False
        assert gen_defaults.retry_on_tools is False

        enricher = DefaultsEnricher(gen_defaults)
        graph = {
            "nodes": [
                {"id": "a", "node_type": "llm_operator", "config": {}},
            ],
            "edges": {"data": [], "control": [], "context": []},
        }
        result = enricher.enrich(graph)
        assert "retry_policy" not in result["nodes"][0].get("config", {})
        assert not any(n["node_type"] == "validator" for n in result["nodes"])


class TestDomainGenerationProfile:
    def test_model_roundtrip(self):
        profile = DomainGenerationProfile(
            domain="test",
            description="Test profile",
            preferred_tools=["web_search"],
            preferred_patterns=["linear_chain"],
        )
        data = profile.model_dump()
        recovered = DomainGenerationProfile.model_validate(data)
        assert recovered.domain == "test"


class TestGetDomainProfile:
    def setup_method(self):
        clear_profile_cache()

    def test_seed_profiles_load(self):
        for domain in ["literature_review", "paper_rendering", "equity_research", "data_analysis", "code_generation"]:
            profile = get_domain_profile(domain)
            assert profile is not None, f"Seed profile for {domain} not found"
            assert profile.domain == domain
            assert len(profile.preferred_tools) > 0
            assert len(profile.preferred_patterns) > 0

    def test_unknown_domain_returns_none(self):
        assert get_domain_profile("nonexistent_domain_xyz") is None

    def test_caching(self):
        p1 = get_domain_profile("literature_review")
        p2 = get_domain_profile("literature_review")
        assert p1 is p2


class TestBuildDomainPromptContext:
    def test_includes_tools(self):
        profile = DomainGenerationProfile(
            domain="test",
            preferred_tools=["web_search", "pdf_read"],
        )
        ctx = build_domain_prompt_context(profile)
        assert "web_search" in ctx
        assert "pdf_read" in ctx

    def test_includes_patterns(self):
        profile = DomainGenerationProfile(
            domain="test",
            preferred_patterns=["research_review"],
        )
        ctx = build_domain_prompt_context(profile)
        assert "research_review" in ctx

    def test_includes_hints(self):
        profile = DomainGenerationProfile(
            domain="test",
            prompt_hints=["Use systematic approach"],
            validation_hints=["Check citations"],
        )
        ctx = build_domain_prompt_context(profile)
        assert "systematic approach" in ctx
        assert "Check citations" in ctx
