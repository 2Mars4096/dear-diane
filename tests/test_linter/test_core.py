from __future__ import annotations

import pytest

from dan.linter import IntentConfig, LintConfig, RuleSeverity, SemanticConfig, StructuralConfig, lint


@pytest.mark.asyncio
async def test_structural_lint_fill_defaults_autofix() -> None:
    result = await lint(
        {"name": "draft"},
        LintConfig(
            structural=StructuralConfig(
                json_schema={
                    "type": "object",
                    "properties": {"status": {"type": "string", "default": "todo"}},
                },
                required_keys=["status"],
            ),
            severity=RuleSeverity.ERROR,
            autofix=["fill_defaults"],
        ),
    )

    assert result.passed is True
    assert result.fixed_data == {"name": "draft", "status": "todo"}
    assert result.applied_fixes == ["fill_defaults"]


@pytest.mark.asyncio
async def test_semantic_and_intent_lint_fail_without_required_keywords() -> None:
    result = await lint(
        "This text is unrelated.",
        LintConfig(
            semantic=SemanticConfig(topic_keywords=["finance", "summary"]),
            intent=IntentConfig(intent="finance summary for an executive briefing"),
            severity=RuleSeverity.ERROR,
            tier3_threshold=1.0,
        ),
    )

    assert result.passed is False
    codes = {diag.code for diag in result.diagnostics}
    assert "keyword_presence" in codes
    assert "intent_keywords" in codes


@pytest.mark.asyncio
async def test_structural_lint_clamp_autofix_from_ranges() -> None:
    result = await lint(
        {"score": 11},
        LintConfig(
            structural=StructuralConfig(ranges={"score": {"minimum": 0, "maximum": 10}}),
            severity=RuleSeverity.ERROR,
            autofix=["clamp"],
        ),
    )

    assert result.passed is True
    assert result.fixed_data == {"score": 10}
    assert result.applied_fixes == ["clamp"]


@pytest.mark.asyncio
async def test_structural_lint_coerce_then_clamp_relint_cycle() -> None:
    result = await lint(
        {"score": "11"},
        LintConfig(
            structural=StructuralConfig(
                json_schema={
                    "type": "object",
                    "properties": {"score": {"type": "integer"}},
                },
                ranges={"score": {"minimum": 0, "maximum": 10}},
            ),
            severity=RuleSeverity.ERROR,
            autofix=["coerce", "clamp"],
        ),
    )

    assert result.passed is True
    assert result.fixed_data == {"score": 10}
    assert set(result.applied_fixes) == {"coerce", "clamp"}


@pytest.mark.asyncio
async def test_structural_lint_pattern_failure_reports_diagnostic() -> None:
    result = await lint(
        {"ticket_id": "abc-12"},
        LintConfig(
            structural=StructuralConfig(format_patterns={"ticket_id": r"[A-Z]{3}-\d{4}"}),
            severity=RuleSeverity.ERROR,
        ),
    )

    assert result.passed is False
    assert {diag.code for diag in result.diagnostics} == {"format_pattern"}


@pytest.mark.asyncio
async def test_semantic_lint_skips_similarity_when_embedder_is_unavailable() -> None:
    async def broken_embed(*args, **kwargs):
        raise KeyError("embedding provider unavailable")

    result = await lint(
        "finance summary",
        LintConfig(
            semantic=SemanticConfig(
                reference_text="finance summary for leadership",
                min_similarity=0.8,
            ),
            severity=RuleSeverity.ERROR,
        ),
        runtime=type("Runtime", (), {"embed": broken_embed, "judge_intent": None})(),
    )

    assert result.passed is True
    assert result.diagnostics == []
    assert result.semantic_score is None


@pytest.mark.asyncio
async def test_intent_lint_skips_tier_when_judge_backend_is_unavailable() -> None:
    async def broken_judge(*args, **kwargs):
        raise RuntimeError("judge backend unavailable")

    result = await lint(
        "finance summary",
        LintConfig(
            intent=IntentConfig(intent="finance summary for leadership"),
            severity=RuleSeverity.ERROR,
        ),
        runtime=type("Runtime", (), {"embed": None, "judge_intent": broken_judge})(),
    )

    assert result.passed is True
    assert result.diagnostics == []
    assert result.intent_score is None


@pytest.mark.asyncio
async def test_intent_lint_keeps_required_keywords_guardrail_even_with_live_judge() -> None:
    async def permissive_judge(*args, **kwargs):
        return {"passed": True, "score": 1.0, "message": "looks fine"}

    result = await lint(
        "weekend hiking checklist",
        LintConfig(
            intent=IntentConfig(
                intent="A concise executive finance briefing",
                required_keywords=["executive", "finance", "briefing"],
            ),
            severity=RuleSeverity.ERROR,
        ),
        runtime=type("Runtime", (), {"embed": None, "judge_intent": permissive_judge})(),
    )

    assert result.passed is False
    assert result.intent_score == 1.0
    assert {diag.code for diag in result.diagnostics} == {"intent_keywords"}


@pytest.mark.asyncio
async def test_intent_lint_plans_retry_feedback_when_requested() -> None:
    async def failing_judge(*args, **kwargs):
        return {
            "verdict": "fail",
            "score": 0.1,
            "reason": "Missing recommendation",
            "missing": ["recommendation"],
        }

    result = await lint(
        "Executive finance briefing without recommendation",
        LintConfig(
            intent=IntentConfig(intent="Executive finance briefing with recommendation"),
            severity=RuleSeverity.ERROR,
            autofix=["retry_with_feedback"],
        ),
        runtime=type("Runtime", (), {"embed": None, "judge_intent": staticmethod(failing_judge)})(),
    )

    assert result.passed is False
    assert result.retry_feedback is not None
    assert result.refocus_feedback is None
    assert result.suggested_fixes == ["retry_with_feedback"]
    assert "Still missing: recommendation" in result.retry_feedback


@pytest.mark.asyncio
async def test_semantic_lint_plans_refocus_feedback_when_requested() -> None:
    result = await lint(
        "Weekend hiking checklist",
        LintConfig(
            semantic=SemanticConfig(
                topic_keywords=["finance", "risk"],
                reference_text="Executive finance summary for risk review",
            ),
            severity=RuleSeverity.ERROR,
            autofix=["refocus"],
        ),
    )

    assert result.passed is False
    assert result.retry_feedback is None
    assert result.refocus_feedback is not None
    assert result.suggested_fixes == ["refocus"]
    assert "Required topic keywords: finance, risk" in result.refocus_feedback


@pytest.mark.asyncio
async def test_model_autofix_planning_prefers_retry_feedback_before_refocus() -> None:
    async def failing_judge(*args, **kwargs):
        return {
            "verdict": "fail",
            "score": 0.1,
            "reason": "Missing recommendation",
            "missing": ["recommendation"],
        }

    result = await lint(
        "Weekend hiking checklist",
        LintConfig(
            semantic=SemanticConfig(
                topic_keywords=["finance", "risk"],
                reference_text="Executive finance summary for risk review",
            ),
            intent=IntentConfig(intent="Executive finance briefing with recommendation"),
            severity=RuleSeverity.ERROR,
            autofix=["refocus", "retry_with_feedback"],
        ),
        runtime=type("Runtime", (), {"embed": None, "judge_intent": staticmethod(failing_judge)})(),
    )

    assert result.passed is False
    assert result.suggested_fixes == ["retry_with_feedback", "refocus"]
    assert result.retry_feedback is not None
    assert result.refocus_feedback is not None


@pytest.mark.asyncio
async def test_intent_tier_is_skipped_when_semantic_score_clears_threshold() -> None:
    judge_calls = 0

    async def embed(text: str, model: str | None):
        return [1.0, 0.0]

    async def judge(*args, **kwargs):
        nonlocal judge_calls
        judge_calls += 1
        return {"passed": True, "score": 1.0, "message": "ok"}

    result = await lint(
        "finance summary for leadership",
        LintConfig(
            semantic=SemanticConfig(
                reference_text="finance summary for leadership",
                min_similarity=0.8,
            ),
            intent=IntentConfig(intent="finance summary for leadership"),
            severity=RuleSeverity.ERROR,
            tier3_threshold=0.6,
        ),
        runtime=type(
            "Runtime",
            (),
            {"embed": staticmethod(embed), "judge_intent": staticmethod(judge)},
        )(),
    )

    assert result.passed is True
    assert result.semantic_score == 1.0
    assert result.intent_score is None
    assert judge_calls == 0


@pytest.mark.asyncio
async def test_semantic_confidence_aggregation_can_trigger_or_skip_intent_tier() -> None:
    judge_calls = 0

    async def judge(*args, **kwargs):
        nonlocal judge_calls
        judge_calls += 1
        return {"passed": True, "score": 1.0, "message": "ok"}

    min_result = await lint(
        "Finance memo for Acme",
        LintConfig(
            semantic=SemanticConfig(
                topic_keywords=["finance", "memo"],
                required_entities=["Acme", "Globex"],
                confidence_aggregation="min",
            ),
            intent=IntentConfig(intent="Finance memo covering Acme and Globex"),
            severity=RuleSeverity.ERROR,
            tier3_threshold=0.6,
        ),
        runtime=type(
            "Runtime",
            (),
            {"embed": None, "judge_intent": staticmethod(judge)},
        )(),
    )

    assert min_result.semantic_score == 0.5
    assert min_result.intent_score == 1.0
    assert judge_calls == 1

    judge_calls = 0
    mean_result = await lint(
        "Finance memo for Acme",
        LintConfig(
            semantic=SemanticConfig(
                topic_keywords=["finance", "memo"],
                required_entities=["Acme", "Globex"],
                confidence_aggregation="mean",
            ),
            intent=IntentConfig(intent="Finance memo covering Acme and Globex"),
            severity=RuleSeverity.ERROR,
            tier3_threshold=0.6,
        ),
        runtime=type(
            "Runtime",
            (),
            {"embed": None, "judge_intent": staticmethod(judge)},
        )(),
    )

    assert mean_result.semantic_score == 0.75
    assert mean_result.intent_score is None
    assert judge_calls == 0


@pytest.mark.asyncio
async def test_language_mismatch_warning_does_not_fail_lint() -> None:
    result = await lint(
        "Este es un resumen financiero para liderazgo.",
        LintConfig(
            semantic=SemanticConfig(expected_language="en"),
            severity=RuleSeverity.ERROR,
        ),
    )

    assert result.passed is True
    assert result.semantic_score is None
    assert [diag.code for diag in result.diagnostics] == ["language_detection"]
    assert result.diagnostics[0].severity == RuleSeverity.WARNING


@pytest.mark.asyncio
async def test_contradiction_detection_triggers_intent_tier_when_score_drops() -> None:
    judge_calls = 0

    async def judge(*args, **kwargs):
        nonlocal judge_calls
        judge_calls += 1
        return {"passed": True, "score": 1.0, "message": "ok"}

    result = await lint(
        "Acme revenue increased to 8% this quarter.",
        LintConfig(
            semantic=SemanticConfig(
                contradiction_reference_text="Acme revenue decreased to 8% this quarter.",
            ),
            intent=IntentConfig(intent="Accurately restate the revenue change for Acme."),
            severity=RuleSeverity.ERROR,
            tier3_threshold=0.6,
        ),
        runtime=type(
            "Runtime",
            (),
            {"embed": None, "judge_intent": staticmethod(judge)},
        )(),
    )

    assert result.passed is False
    assert result.semantic_score == 0.0
    assert {diag.code for diag in result.diagnostics} == {"contradiction_detection"}
    assert result.intent_score == 1.0
    assert judge_calls == 1
