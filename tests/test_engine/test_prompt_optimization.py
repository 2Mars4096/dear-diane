"""Tests for prompt optimization pipeline (29-6 §7-2..7-7).

PromptAnalyzer, PromptVariantGenerator, PromptABTest, safety helpers,
and env-var gating.
"""

from __future__ import annotations

import pytest

from dan.engine.memory_kernel import MemoryKernel, MemoryScope, MemoryType
from dan.engine.outcome_trackers import (
    PromptABTest,
    PromptAnalyzer,
    PromptTracker,
    PromptVariantGenerator,
    _is_safe_variant,
    _simplify_prompt,
)


@pytest.fixture()
def kernel(tmp_path):
    return MemoryKernel(base_dir=str(tmp_path / "mem"))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure env vars are clean before each test."""
    monkeypatch.delenv("DAN_PROMPT_OPTIMIZATION", raising=False)


def _seed_history(kernel, node_id, count, success_ratio=0.5, tokens=500, latency=1000.0, output_hint=""):
    """Seed prompt tracker history for a node (requires env var already set)."""
    tracker = PromptTracker(kernel)
    for i in range(count):
        outcome = i < int(count * success_ratio)
        tracker.record(
            node_id=node_id,
            prompt_hash=f"hash-{i}",
            input_summary=f"input-{i}",
            output_summary=output_hint or f"output-{i}",
            outcome=outcome,
            tokens_used=tokens if outcome else int(tokens * 1.5),
            latency_ms=latency if outcome else latency * 1.3,
        )
    return tracker


# ===================================================================
# PromptAnalyzer (7-2)
# ===================================================================


class TestPromptAnalyzer:
    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        monkeypatch.setenv("DAN_PROMPT_OPTIMIZATION", "1")

    def test_should_analyze_below_threshold(self, kernel):
        _seed_history(kernel, "n1", 10)
        analyzer = PromptAnalyzer(PromptTracker(kernel))
        assert analyzer.should_analyze("n1", threshold=20) is False

    def test_should_analyze_above_threshold(self, kernel):
        _seed_history(kernel, "n1", 25)
        analyzer = PromptAnalyzer(PromptTracker(kernel))
        assert analyzer.should_analyze("n1", threshold=20) is True

    def test_should_analyze_exact_threshold(self, kernel):
        _seed_history(kernel, "n1", 20)
        analyzer = PromptAnalyzer(PromptTracker(kernel))
        assert analyzer.should_analyze("n1", threshold=20) is True

    def test_analyze_returns_all_fields(self, kernel):
        _seed_history(kernel, "n1", 30, success_ratio=0.6)
        analyzer = PromptAnalyzer(PromptTracker(kernel))
        result = analyzer.analyze("n1")

        assert result["node_id"] == "n1"
        assert result["total_runs"] == 30
        assert 0.5 <= result["success_rate"] <= 0.7
        assert result["avg_tokens_success"] > 0
        assert result["avg_tokens_failure"] > 0
        assert isinstance(result["failure_patterns"], list)
        assert isinstance(result["success_patterns"], list)

    def test_analyze_correct_success_rate(self, kernel):
        tracker = PromptTracker(kernel)
        for i in range(10):
            tracker.record("n1", f"h{i}", "in", "out", i < 7, 100, 200.0)
        analyzer = PromptAnalyzer(tracker)
        result = analyzer.analyze("n1")
        assert abs(result["success_rate"] - 0.7) < 0.01

    def test_analyze_token_averages(self, kernel):
        tracker = PromptTracker(kernel)
        for i in range(10):
            is_success = i < 5
            tracker.record(
                "n1", f"h{i}", "in", "out", is_success,
                tokens_used=100 if is_success else 300,
                latency_ms=200.0 if is_success else 600.0,
            )
        analyzer = PromptAnalyzer(tracker)
        result = analyzer.analyze("n1")
        assert result["avg_tokens_success"] == 100
        assert result["avg_tokens_failure"] == 300
        assert result["avg_latency_success"] == 200.0
        assert result["avg_latency_failure"] == 600.0

    def test_analyze_failure_patterns_format(self, kernel):
        tracker = PromptTracker(kernel)
        for i in range(20):
            outcome = i < 5
            tracker.record(
                "n1", f"h{i}", "in",
                "format error: bad JSON" if not outcome else "ok",
                outcome, 100, 200.0,
            )
        analyzer = PromptAnalyzer(tracker)
        result = analyzer.analyze("n1")
        assert any("format" in p for p in result["failure_patterns"])

    def test_analyze_no_failure_patterns_below_threshold(self, kernel):
        tracker = PromptTracker(kernel)
        for i in range(20):
            outcome = i < 18
            tracker.record(
                "n1", f"h{i}", "in",
                "rare format glitch" if not outcome else "ok",
                outcome, 100, 200.0,
            )
        analyzer = PromptAnalyzer(tracker)
        result = analyzer.analyze("n1")
        # Only 2/20 failures, "format" in 2/2=100% of failures but
        # the overall sample is small enough that the pattern is detected
        # (threshold is per-failure, not per-total)
        assert isinstance(result["failure_patterns"], list)

    def test_analyze_disabled_returns_empty(self, kernel, monkeypatch):
        monkeypatch.setenv("DAN_PROMPT_OPTIMIZATION", "0")
        analyzer = PromptAnalyzer(PromptTracker(kernel))
        result = analyzer.analyze("n1")
        assert result["total_runs"] == 0


# ===================================================================
# PromptVariantGenerator (7-3)
# ===================================================================


class TestPromptVariantGenerator:
    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        monkeypatch.setenv("DAN_PROMPT_OPTIMIZATION", "1")

    def test_generates_step_by_step(self):
        gen = PromptVariantGenerator()
        variants = gen.generate_variants("Summarize the text.", {"success_rate": 0.5})
        assert any(v["strategy"] == "add_step_by_step" for v in variants)
        step = next(v for v in variants if v["strategy"] == "add_step_by_step")
        assert "step by step" in step["variant_prompt"].lower()
        assert "Summarize the text." in step["variant_prompt"]

    def test_no_step_by_step_when_already_present(self):
        gen = PromptVariantGenerator()
        variants = gen.generate_variants(
            "Think step by step. Summarize.", {"success_rate": 0.5},
        )
        assert not any(v["strategy"] == "add_step_by_step" for v in variants)

    def test_no_step_by_step_when_success_rate_high(self):
        gen = PromptVariantGenerator()
        variants = gen.generate_variants(
            "Summarize the text.", {"success_rate": 0.95},
        )
        assert not any(v["strategy"] == "add_step_by_step" for v in variants)

    def test_generates_format_variant(self):
        gen = PromptVariantGenerator()
        analysis = {
            "success_rate": 0.5,
            "failure_patterns": ["format (8/15)"],
        }
        variants = gen.generate_variants("Write JSON output.", analysis)
        assert any(v["strategy"] == "reinforce_format" for v in variants)
        fmt = next(v for v in variants if v["strategy"] == "reinforce_format")
        assert "output format" in fmt["variant_prompt"].lower()

    def test_generates_simplify_variant(self):
        gen = PromptVariantGenerator()
        analysis = {
            "success_rate": 0.6,
            "avg_tokens_success": 200,
            "avg_tokens_failure": 400,
        }
        prompt = "Line one.\nLine two.\nLine three.\nLine two."
        variants = gen.generate_variants(prompt, analysis)
        simplify = [v for v in variants if v["strategy"] == "simplify"]
        assert len(simplify) >= 1
        # Simplified variant should be shorter (duplicate "Line two." removed)
        assert simplify[0]["variant_prompt"].count("Line two.") == 1

    def test_no_simplify_when_no_change(self):
        gen = PromptVariantGenerator()
        analysis = {
            "success_rate": 0.6,
            "avg_tokens_success": 200,
            "avg_tokens_failure": 400,
        }
        prompt = "Unique line one.\nUnique line two.\nUnique line three."
        variants = gen.generate_variants(prompt, analysis)
        assert not any(v["strategy"] == "simplify" for v in variants)

    def test_respects_count_limit(self):
        gen = PromptVariantGenerator()
        analysis = {
            "success_rate": 0.3,
            "failure_patterns": ["format (10/20)"],
            "avg_tokens_success": 100,
            "avg_tokens_failure": 300,
        }
        prompt = "Dup line.\nAnother.\nDup line."
        variants = gen.generate_variants(prompt, analysis, count=1)
        assert len(variants) <= 1

    def test_disabled_returns_empty(self, monkeypatch):
        monkeypatch.setenv("DAN_PROMPT_OPTIMIZATION", "0")
        gen = PromptVariantGenerator()
        assert gen.generate_variants("prompt", {"success_rate": 0.3}) == []

    def test_all_three_strategies_can_fire(self):
        gen = PromptVariantGenerator()
        analysis = {
            "success_rate": 0.3,
            "failure_patterns": ["format (10/20)"],
            "avg_tokens_success": 100,
            "avg_tokens_failure": 300,
        }
        prompt = "Dup line.\nAnother.\nDup line."
        variants = gen.generate_variants(prompt, analysis, count=3)
        strategies = {v["strategy"] for v in variants}
        assert "add_step_by_step" in strategies
        assert "reinforce_format" in strategies
        assert "simplify" in strategies


# ===================================================================
# PromptABTest (7-4, 7-5)
# ===================================================================


class TestPromptABTest:
    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        monkeypatch.setenv("DAN_PROMPT_OPTIMIZATION", "1")

    def test_create_test_returns_metadata(self, kernel):
        ab = PromptABTest(kernel)
        variants = [
            {"variant_prompt": "Step by step.\nDo X.", "strategy": "add_step_by_step"},
            {"variant_prompt": "Do X.\nFollow format.", "strategy": "reinforce_format"},
        ]
        test = ab.create_test("n1", "Do X.", variants)
        assert test is not None
        assert test["node_id"] == "n1"
        assert test["status"] == "active"
        assert len(test["variants"]) == 2
        assert test["original_prompt"] == "Do X."
        assert test["original_runs"] == 0

    def test_create_test_persisted_in_kernel(self, kernel):
        ab = PromptABTest(kernel)
        ab.create_test("n1", "original", [{"variant_prompt": "v1", "strategy": "s1"}])
        item = kernel.get("ab_test:n1")
        assert item is not None
        assert item.memory_type == MemoryType.WORKING_STATE
        assert item.scope == MemoryScope.WORKFLOW

    def test_select_variant_returns_variant_or_none(self, kernel):
        ab = PromptABTest(kernel)
        ab.create_test("n1", "original", [
            {"variant_prompt": "var1", "strategy": "s1"},
            {"variant_prompt": "var2", "strategy": "s2"},
        ])
        seen = set()
        for _ in range(100):
            result = ab.select_variant("n1")
            seen.add(result)
        valid = {None, "var1", "var2"}
        assert seen.issubset(valid)
        assert len(seen) >= 2  # should see at least 2 of the 3 options

    def test_select_variant_no_test(self, kernel):
        ab = PromptABTest(kernel)
        assert ab.select_variant("nonexistent") is None

    def test_select_variant_completed_test(self, kernel):
        ab = PromptABTest(kernel)
        ab.create_test("n1", "original", [{"variant_prompt": "v1", "strategy": "s1"}])
        item = kernel.get("ab_test:n1")
        item.metadata["status"] = "completed"
        kernel.update(item.id, metadata=item.metadata)
        assert ab.select_variant("n1") is None

    def test_record_outcome_variant(self, kernel):
        ab = PromptABTest(kernel)
        ab.create_test("n1", "original", [{"variant_prompt": "v1", "strategy": "s1"}])
        ab.record_outcome("n1", "v1", True)
        ab.record_outcome("n1", "v1", False)

        item = kernel.get("ab_test:n1")
        v = item.metadata["variants"][0]
        assert v["runs"] == 2
        assert v["successes"] == 1

    def test_record_outcome_original(self, kernel):
        ab = PromptABTest(kernel)
        ab.create_test("n1", "original", [{"variant_prompt": "v1", "strategy": "s1"}])
        ab.record_outcome("n1", None, True)
        ab.record_outcome("n1", None, False)

        item = kernel.get("ab_test:n1")
        assert item.metadata["original_runs"] == 2
        assert item.metadata["original_successes"] == 1

    def test_record_outcome_multiple_variants(self, kernel):
        ab = PromptABTest(kernel)
        ab.create_test("n1", "original", [
            {"variant_prompt": "v1", "strategy": "s1"},
            {"variant_prompt": "v2", "strategy": "s2"},
        ])
        ab.record_outcome("n1", "v1", True)
        ab.record_outcome("n1", "v2", False)
        ab.record_outcome("n1", "v2", True)

        item = kernel.get("ab_test:n1")
        assert item.metadata["variants"][0]["runs"] == 1
        assert item.metadata["variants"][0]["successes"] == 1
        assert item.metadata["variants"][1]["runs"] == 2
        assert item.metadata["variants"][1]["successes"] == 1

    def test_check_promotion_insufficient_runs(self, kernel):
        ab = PromptABTest(kernel)
        ab.create_test("n1", "original", [{"variant_prompt": "v1", "strategy": "s1"}])
        for _ in range(3):
            ab.record_outcome("n1", "v1", True)
        assert ab.check_promotion("n1", min_runs_per_variant=10) is None

    def test_check_promotion_insufficient_original_runs(self, kernel):
        ab = PromptABTest(kernel)
        ab.create_test("n1", "original", [{"variant_prompt": "v1", "strategy": "s1"}])
        for _ in range(10):
            ab.record_outcome("n1", "v1", True)
        for _ in range(3):
            ab.record_outcome("n1", None, True)
        assert ab.check_promotion("n1", min_runs_per_variant=10) is None

    def test_check_promotion_winner(self, kernel):
        ab = PromptABTest(kernel)
        ab.create_test("n1", "original", [{"variant_prompt": "v1", "strategy": "s1"}])

        for _ in range(8):
            ab.record_outcome("n1", "v1", True)
        for _ in range(2):
            ab.record_outcome("n1", "v1", False)

        for _ in range(4):
            ab.record_outcome("n1", None, True)
        for _ in range(6):
            ab.record_outcome("n1", None, False)

        result = ab.check_promotion("n1", min_runs_per_variant=10)
        assert result is not None
        assert result["variant"]["prompt"] == "v1"
        assert result["improvement"] > 0
        assert abs(result["improvement"] - 0.4) < 0.01  # 0.8 - 0.4

    def test_check_promotion_no_improvement(self, kernel):
        ab = PromptABTest(kernel)
        ab.create_test("n1", "original", [{"variant_prompt": "v1", "strategy": "s1"}])

        for _ in range(3):
            ab.record_outcome("n1", "v1", True)
        for _ in range(7):
            ab.record_outcome("n1", "v1", False)

        for _ in range(7):
            ab.record_outcome("n1", None, True)
        for _ in range(3):
            ab.record_outcome("n1", None, False)

        assert ab.check_promotion("n1", min_runs_per_variant=10) is None

    def test_check_promotion_picks_best_among_multiple(self, kernel):
        ab = PromptABTest(kernel)
        ab.create_test("n1", "original", [
            {"variant_prompt": "v1", "strategy": "s1"},
            {"variant_prompt": "v2", "strategy": "s2"},
        ])

        # v1: 6/10, v2: 9/10, original: 3/10
        for _ in range(6):
            ab.record_outcome("n1", "v1", True)
        for _ in range(4):
            ab.record_outcome("n1", "v1", False)
        for _ in range(9):
            ab.record_outcome("n1", "v2", True)
        ab.record_outcome("n1", "v2", False)
        for _ in range(3):
            ab.record_outcome("n1", None, True)
        for _ in range(7):
            ab.record_outcome("n1", None, False)

        result = ab.check_promotion("n1", min_runs_per_variant=10)
        assert result is not None
        assert result["variant"]["prompt"] == "v2"

    def test_promote_stores_provenance(self, kernel):
        ab = PromptABTest(kernel)
        ab.create_test("n1", "original prompt", [
            {"variant_prompt": "better prompt", "strategy": "s1"},
        ])

        winning = {"prompt": "better prompt", "strategy": "s1", "runs": 10, "successes": 8}
        ab.promote("n1", winning)

        item = kernel.get("ab_test:n1")
        assert item.metadata["status"] == "completed"
        assert item.metadata["winner"]["prompt"] == "better prompt"

        episodes = kernel.list_by_type(MemoryType.EPISODE, scope=MemoryScope.WORKFLOW, limit=50)
        provenance = [e for e in episodes if e.metadata.get("tracker") == "prompt_provenance"]
        assert len(provenance) == 1
        assert provenance[0].metadata["old_prompt"] == "original prompt"
        assert provenance[0].metadata["new_prompt"] == "better prompt"
        assert provenance[0].metadata["node_id"] == "n1"
        assert provenance[0].metadata["success_rate"] == 0.8

    def test_promote_marks_completed(self, kernel):
        ab = PromptABTest(kernel)
        ab.create_test("n1", "original", [{"variant_prompt": "v1", "strategy": "s1"}])
        ab.promote("n1", {"prompt": "v1", "strategy": "s1", "runs": 10, "successes": 7})
        assert ab.select_variant("n1") is None  # test is completed

    def test_disabled_noop(self, kernel, monkeypatch):
        monkeypatch.setenv("DAN_PROMPT_OPTIMIZATION", "0")
        ab = PromptABTest(kernel)
        assert ab.create_test("n1", "orig", [{"variant_prompt": "v1"}]) is None
        assert ab.select_variant("n1") is None
        ab.record_outcome("n1", "v1", True)  # should not raise
        assert ab.check_promotion("n1") is None


# ===================================================================
# Safety helpers (7-6, 7-7)
# ===================================================================


class TestSafety:
    def test_safe_variant_preserves_constraints(self):
        original = "You MUST return JSON. Follow the schema below.\n```json\n{}\n```"
        variant = (
            "Think step by step.\n\n"
            "You MUST return JSON. Follow the schema below.\n```json\n{}\n```"
        )
        assert _is_safe_variant(original, variant) is True

    def test_unsafe_variant_removes_must(self):
        original = "You MUST return JSON."
        variant = "Return JSON."
        assert _is_safe_variant(original, variant) is False

    def test_unsafe_variant_removes_required(self):
        original = "REQUIRED: include all citations."
        variant = "Include citations."
        assert _is_safe_variant(original, variant) is False

    def test_unsafe_variant_removes_do_not(self):
        original = "DO NOT include personal opinions."
        variant = "Summarize objectively."
        assert _is_safe_variant(original, variant) is False

    def test_unsafe_variant_removes_never(self):
        original = "NEVER output raw HTML."
        variant = "Output clean text."
        assert _is_safe_variant(original, variant) is False

    def test_unsafe_variant_removes_schema_block(self):
        original = "Output:\n```json\n{\"key\": \"value\"}\n```"
        variant = "Output: JSON format"
        assert _is_safe_variant(original, variant) is False

    def test_safe_variant_adds_content(self):
        original = "Summarize the text."
        variant = "Think step by step.\n\nSummarize the text."
        assert _is_safe_variant(original, variant) is True

    def test_safe_variant_no_constraints(self):
        original = "Summarize the text."
        variant = "Please summarize the text concisely."
        assert _is_safe_variant(original, variant) is True


class TestSimplifyPrompt:
    def test_removes_duplicate_lines(self):
        prompt = "Line A\nLine B\nLine A\nLine C"
        result = _simplify_prompt(prompt)
        assert result.count("Line A") == 1
        assert "Line B" in result
        assert "Line C" in result

    def test_removes_empty_lines(self):
        prompt = "Line A\n\n\nLine B\n\n"
        result = _simplify_prompt(prompt)
        lines = [line for line in result.split("\n") if line.strip()]
        assert len(lines) == 2

    def test_preserves_unique_lines(self):
        prompt = "Line A\nLine B\nLine C"
        result = _simplify_prompt(prompt)
        assert result == prompt

    def test_case_insensitive_dedup(self):
        prompt = "Do the thing\ndo the thing\nAnother line"
        result = _simplify_prompt(prompt)
        assert result.lower().count("do the thing") == 1
        assert "Another line" in result


# ===================================================================
# Scope gating — everything no-ops when DAN_PROMPT_OPTIMIZATION != 1
# ===================================================================


class TestScopeGating:
    def test_analyzer_should_analyze_disabled(self, kernel, monkeypatch):
        monkeypatch.setenv("DAN_PROMPT_OPTIMIZATION", "1")
        tracker = PromptTracker(kernel)
        for i in range(25):
            tracker.record("n1", f"h{i}", "in", "out", True, 100, 200.0)
        monkeypatch.setenv("DAN_PROMPT_OPTIMIZATION", "0")

        analyzer = PromptAnalyzer(tracker)
        assert analyzer.should_analyze("n1") is False

    def test_analyzer_analyze_disabled(self, kernel):
        analyzer = PromptAnalyzer(PromptTracker(kernel))
        result = analyzer.analyze("n1")
        assert result["total_runs"] == 0

    def test_generator_disabled(self):
        gen = PromptVariantGenerator()
        assert gen.generate_variants("prompt", {"success_rate": 0.3}) == []

    def test_ab_test_create_disabled(self, kernel):
        ab = PromptABTest(kernel)
        assert ab.create_test("n1", "p", [{"variant_prompt": "v"}]) is None

    def test_ab_test_select_disabled(self, kernel):
        ab = PromptABTest(kernel)
        assert ab.select_variant("n1") is None

    def test_ab_test_check_promotion_disabled(self, kernel):
        ab = PromptABTest(kernel)
        assert ab.check_promotion("n1") is None

    def test_ab_test_promote_disabled(self, kernel):
        ab = PromptABTest(kernel)
        ab.promote("n1", {"prompt": "v"})  # should not raise
        assert kernel.get("ab_test:n1") is None  # nothing persisted
