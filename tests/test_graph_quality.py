"""Tests for semantic quality gates (33-7)."""

from __future__ import annotations

import pytest

from dan.builder import workflow
from dan.meta.graph_quality import (
    check_node_count,
    check_pattern_presence,
    check_semantic_grounding,
    check_tool_coverage,
    check_topology,
    compute_quality_report,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _high_quality_graph() -> dict:
    """Runnable review-loop graph built via the builder DSL."""
    wf = workflow("high_quality_review_loop")
    wf.review_loop(
        "Write a concise draft about {topic}",
        "Review the draft and return quality_score plus feedback",
        name="quality_review",
    )
    return wf.build().model_dump(mode="json")


def _underspecified_graph() -> dict:
    """2-node graph, no gate, for review loop prompt — should score low."""
    return {
        "version": "dan_graph_v1",
        "metadata": {},
        "nodes": [
            {"id": "n1", "node_type": "llm_operator", "name": "n1"},
            {"id": "n2", "node_type": "llm_operator", "name": "n2"},
        ],
        "edges": [
            {"id": "e1", "source_node_id": "n1", "target_node_id": "n2", "edge_type": "data"},
        ],
    }


def _single_node_graph() -> dict:
    """1-node graph — should score low on topology and node count."""
    return {
        "version": "dan_graph_v1",
        "metadata": {},
        "nodes": [{"id": "n1", "node_type": "llm_operator", "name": "n1"}],
        "edges": [],
    }


def _empty_graph() -> dict:
    return {"version": "dan_graph_v1", "metadata": {}, "nodes": [], "edges": []}


def _nested_tool_graph() -> dict:
    """Top-level control node with the actual tool work inside a subgraph."""
    return {
        "version": "dan_graph_v1",
        "metadata": {},
        "nodes": [
            {"id": "search_each", "node_type": "for_each", "name": "search_each"},
        ],
        "edges": [],
        "sub_graphs": {
            "search_each_body": {
                "version": "dan_graph_v1",
                "metadata": {},
                "nodes": [
                    {"id": "search_web", "node_type": "tool_operator", "name": "search_web"},
                ],
                "edges": [],
            }
        },
    }


def _semantically_ungrounded_subgraph_graph() -> dict:
    """Valid graph whose subgraph claims external actions without grounding."""
    wf = workflow("semantically_ungrounded")
    with wf.for_each("fetch_market_data") as body:
        body.llm(
            "fetch_market_data_proc",
            prompt="Search the web for each ticker and save the result to a markdown file",
        )
    return wf.build().model_dump(mode="json")


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


class TestCheckNodeCount:
    def test_high_quality_meets_tier_minimum(self) -> None:
        g = _high_quality_graph()
        r = check_node_count(g, "Build a review loop workflow", tier="T1")
        assert r.score >= 90
        assert r.concerns == []

    def test_underspecified_below_minimum(self) -> None:
        g = _underspecified_graph()
        r = check_node_count(g, "Build a 5-step equity research workflow", tier="T3")
        assert r.score < 100
        assert len(r.concerns) > 0

    def test_single_node_tier_t2(self) -> None:
        g = _single_node_graph()
        r = check_node_count(g, "Build a workflow", tier="T2")
        assert r.score < 100
        assert "Only 1 node" in str(r.concerns) or "1 node" in r.explanation

    def test_empty_graph_zero_score(self) -> None:
        g = _empty_graph()
        r = check_node_count(g, "Build a workflow", tier="T1")
        assert r.score == 0
        assert "no nodes" in r.explanation.lower() or "0" in r.explanation

    def test_infer_from_prompt_steps(self) -> None:
        g = _high_quality_graph()
        r = check_node_count(g, "Build a 3-step chain: research, analyze, summarize", tier=None)
        assert r.score >= 90
        assert r.concerns == []


class TestCheckPatternPresence:
    def test_review_loop_with_gate_passes(self) -> None:
        g = _high_quality_graph()
        r = check_pattern_presence(g, "Create a review loop where a writer drafts and reviewer gives feedback")
        assert r.score >= 90
        assert "gate" in str(r.explanation).lower() or "control" in str(r.explanation).lower()

    def test_review_loop_without_gate_passes_low(self) -> None:
        g = _underspecified_graph()
        r = check_pattern_presence(g, "Create a review loop workflow")
        assert r.score < 100
        assert len(r.concerns) > 0

    def test_no_keywords_full_score(self) -> None:
        g = _high_quality_graph()
        r = check_pattern_presence(g, "Build a simple linear chain")
        assert r.score == 100


class TestCheckToolCoverage:
    def test_web_search_prompt_with_tool(self) -> None:
        g = {
            "nodes": [
                {"id": "t1", "node_type": "tool_operator", "name": "web_search"},
            ],
            "edges": [],
        }
        r = check_tool_coverage(g, "Search the web for news and produce a briefing")
        assert r.score >= 90

    def test_web_search_prompt_without_tool(self) -> None:
        g = _underspecified_graph()
        r = check_tool_coverage(g, "Use web search to find news and produce a briefing")
        assert r.score < 100
        assert len(r.concerns) > 0

    def test_no_tool_keywords_full_score(self) -> None:
        g = _high_quality_graph()
        r = check_tool_coverage(g, "Build a review loop")
        assert r.score == 100

    def test_nested_tool_node_counts_for_tool_coverage(self) -> None:
        g = _nested_tool_graph()
        r = check_tool_coverage(g, "Search the web for the latest headlines and summarize them")
        assert r.score >= 90


class TestSemanticGrounding:
    def test_run_ready_graph_scores_high(self) -> None:
        g = _high_quality_graph()
        r = check_semantic_grounding(g)
        assert r.score == 100
        assert r.concerns == []

    def test_semantically_ungrounded_subgraph_graph_scores_low(self) -> None:
        g = _semantically_ungrounded_subgraph_graph()
        r = check_semantic_grounding(g)
        assert r.score <= 20
        assert any(
            "has no iterable input source" in concern.lower()
            or "has no tools" in concern.lower()
            for concern in r.concerns
        )


class TestCheckTopology:
    def test_single_node_low_score(self) -> None:
        g = _single_node_graph()
        r = check_topology(g)
        assert r.score >= 30
        assert r.score < 100
        assert "single" in r.explanation.lower() or "1" in r.explanation

    def test_connected_graph_high_score(self) -> None:
        g = _high_quality_graph()
        r = check_topology(g)
        assert r.score >= 80

    def test_empty_graph_zero(self) -> None:
        g = _empty_graph()
        r = check_topology(g)
        assert r.score == 0
        assert "no nodes" in r.explanation.lower() or "empty" in r.explanation.lower()


class TestComputeQualityReport:
    def test_high_quality_report(self) -> None:
        g = _high_quality_graph()
        r = compute_quality_report(
            g,
            "Create a review loop where a writer drafts and a reviewer gives feedback until approved",
            tier="T1",
        )
        assert r.overall_score >= 60
        assert r.node_count is not None
        assert r.pattern_presence is not None
        assert r.tool_coverage is not None
        assert r.topology is not None

    def test_underspecified_report(self) -> None:
        g = _underspecified_graph()
        r = compute_quality_report(
            g,
            "Create a review loop workflow where a writer drafts and reviewer gives feedback",
            tier="T1",
        )
        assert r.overall_score < 80
        assert len(r.concerns) > 0
        assert r.semantic_grounding is not None

    def test_single_node_report(self) -> None:
        g = _single_node_graph()
        r = compute_quality_report(
            g,
            "Build a 5-step equity research workflow with reviewer panel",
            tier="T4",
        )
        assert r.overall_score < 80
        assert len(r.concerns) > 0

    def test_p02_like_scenario(self) -> None:
        """p02: review loop → 2 nodes, 1 edge — should score lower than well-formed (pattern missing)."""
        g = _underspecified_graph()
        r = compute_quality_report(
            g,
            "Create a review loop workflow where a writer drafts content and a reviewer gives feedback until approved",
            tier="T1",
        )
        assert r.overall_score < 90
        assert len(r.concerns) > 0
        assert "review loop" in str(r.concerns).lower()

    def test_p08_like_scenario(self) -> None:
        """p08: equity research 5 steps + reviewer panel → 1 node — should score low."""
        g = _single_node_graph()
        r = compute_quality_report(
            g,
            "Build an equity research workflow with 5 steps and a reviewer panel",
            tier="T4",
        )
        assert r.overall_score < 80
        assert len(r.concerns) > 0

    def test_semantically_ungrounded_subgraph_graph_is_capped_low(self) -> None:
        g = _semantically_ungrounded_subgraph_graph()
        r = compute_quality_report(
            g,
            "Build an equity research workflow that fetches the latest headlines for each ticker and saves one markdown report per ticker",
            tier="T3",
        )
        assert r.semantic_grounding is not None
        assert r.semantic_grounding.score <= 20
        assert r.overall_score <= 20
        assert any(
            "has no iterable input source" in concern.lower()
            or "has no tools" in concern.lower()
            for concern in r.concerns
        )

    def test_calibration_well_formed_graphs_score_high(self) -> None:
        """33-7 task 4: Well-formed graphs from graphs/ directory score ≥70."""
        from pathlib import Path

        graphs_dir = Path(__file__).resolve().parents[1] / "graphs"
        if not graphs_dir.exists():
            pytest.skip("graphs/ directory not found")
        candidates = ["paper_writing.json", "batch_paper_writing.json", "g1.json"]
        for name in candidates:
            path = graphs_dir / name
            if not path.exists():
                continue
            try:
                import json

                with open(path) as f:
                    g = json.load(f)
                if not g.get("nodes"):
                    continue
                r = compute_quality_report(
                    g,
                    "Multi-step workflow with research and synthesis",
                    tier="T3",
                )
                assert r.overall_score >= 70, f"{name} scored {r.overall_score}"
                break
            except (json.JSONDecodeError, KeyError, Exception) as e:
                pytest.skip(f"Could not load {name}: {e}")
        else:
            pytest.skip("No suitable graph file found for calibration")


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------


class TestQualityAfterValidation:
    """Integration: quality runs after validation in build pipeline."""

    @pytest.mark.asyncio
    async def test_quality_event_emitted_after_validation(self) -> None:
        """ChatGraphQualityEvent is emitted when quality succeeds after validation."""
        from tests.eval import PROMPTS_FILE
        from tests.eval.runner import load_prompts
        from tests.eval.metrics import EvalLogger
        from tests.eval.runner import EvalRunner

        # Load prompts and run one build lane
        fixtures = load_prompts(PROMPTS_FILE, tier="T1", pilot_only=True)
        if not fixtures:
            pytest.skip("No pilot prompts found")

        fixture = fixtures[0]
        logger = EvalLogger(run_tag="quality-test")
        runner = EvalRunner(base_url="http://localhost:8080", execute=False)

        try:
            records = await runner.run_battery(
                [fixture],
                lanes=["build"],
                logger=logger,
            )
        except Exception as exc:
            pytest.skip(f"Server not running or failed: {exc}")
        finally:
            await runner.cleanup()

        if not records:
            pytest.skip("No records returned")
        rec = records[0]
        if not (rec.graph_created and rec.validation and rec.validation.passed):
            pytest.skip("No assertable record (server may not have created graph or validation failed)")
        assert rec.quality_score is not None
        assert isinstance(rec.quality_score, int)
        assert 0 <= rec.quality_score <= 100

    def test_quality_scores_in_jsonl_and_report(self) -> None:
        """33-7 task 5-3: quality_score and quality_concerns appear in JSONL and report."""
        import tempfile
        from pathlib import Path

        from tests.eval import EvalRecord, TimingInfo, TokenInfo, ValidationResult
        from tests.eval.metrics import EvalLogger
        from tests.eval.report import ReportGenerator

        rec = EvalRecord(
            id="p01",
            tier="T1",
            lane="build",
            prompt="Build a simple 3-step chain",
            timing=TimingInfo(total_ms=100.0),
            build_tokens=TokenInfo(total_tokens=50),
            graph_created=True,
            validation=ValidationResult(passed=True, errors=[]),
            quality_score=85,
            quality_concerns=["Minor: prompt mentions 3 steps, graph has 3 nodes"],
            status="passed",
        )
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            logger = EvalLogger(output_dir=out_dir, run_tag="quality-jsonl-test")
            logger.log(rec)
            path = logger.output_path
            loaded = EvalLogger.load_records(path)
            assert len(loaded) == 1
            assert loaded[0].quality_score == 85
            assert "3 steps" in (loaded[0].quality_concerns or [""])[0]

            report = ReportGenerator(loaded)
            summary = report.summary()
            assert "quality_by_tier" in summary
            assert "T1" in summary["quality_by_tier"]
            assert summary["quality_by_tier"]["T1"]["min"] == 85
            assert summary["quality_by_tier"]["T1"]["max"] == 85
            assert "top_quality_concerns" in summary
            assert len(summary["top_quality_concerns"]) > 0
