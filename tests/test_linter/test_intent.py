from __future__ import annotations

import pytest

from dan.linter.config import IntentConfig, RuleSeverity
from dan.linter.rules.intent import (
    build_intent_judge_prompt,
    parse_judgment_result,
    validate_intent,
)


def test_parse_judgment_result_accepts_multiple_live_response_shapes() -> None:
    result = parse_judgment_result(
        {
            "judgment": "partial",
            "confidence": "0.4",
            "reason": "Missing the final recommendation",
            "missing": "recommendation, risk summary",
        }
    )

    assert result.verdict == "partial"
    assert result.confidence == 0.4
    assert result.reason == "Missing the final recommendation"
    assert result.missing == ["recommendation", "risk summary"]
    assert result.covered == []


def test_build_intent_judge_prompt_handles_dict_and_completeness_variants() -> None:
    config = IntentConfig(
        intent="Executive finance briefing with recommendation",
        required_keywords=["finance", "recommendation"],
    )

    alignment_prompt = build_intent_judge_prompt(
        {"summary": "Revenue improved", "risk": "flat"},
        config,
    )
    completeness_prompt = build_intent_judge_prompt(
        ["Revenue improved", "Need recommendation"],
        config,
        prompt_variant="completeness",
        missing=["recommendation", "risk summary"],
    )

    assert "Executive finance briefing with recommendation" in alignment_prompt
    assert '"summary": "Revenue improved"' in alignment_prompt
    assert "Required keywords to keep in scope" in alignment_prompt
    assert "Focus specifically on these requirements" in completeness_prompt
    assert "recommendation, risk summary" in completeness_prompt
    assert 'keys verdict, score, reason, missing, covered' in completeness_prompt


@pytest.mark.asyncio
async def test_validate_intent_partial_returns_warning_with_missing_metadata() -> None:
    async def partial_judge(*args, **kwargs):
        return {
            "verdict": "partial",
            "score": 0.55,
            "reason": "Missing the final recommendation",
            "missing": ["recommendation"],
        }

    diagnostics, score = await validate_intent(
        "Executive briefing: revenue grew.",
        IntentConfig(intent="Executive finance briefing with recommendation"),
        severity=RuleSeverity.ERROR,
        judge=partial_judge,
    )

    assert score == 0.55
    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.code == "intent_partial"
    assert diagnostic.severity == RuleSeverity.WARNING
    assert diagnostic.metadata["missing"] == ["recommendation"]
    assert diagnostic.metadata["verdict"] == "partial"
    assert diagnostic.metadata["covered"] == []


@pytest.mark.asyncio
async def test_validate_intent_partial_runs_completeness_follow_up() -> None:
    calls: list[dict[str, object]] = []

    async def partial_then_completeness(data, config, context=None):
        calls.append(context or {})
        if context and context.get("prompt_variant") == "completeness":
            return {
                "verdict": "partial",
                "score": 0.6,
                "reason": "Covers revenue but not the recommendation",
                "missing": ["recommendation"],
                "covered": ["revenue"],
            }
        return {
            "verdict": "partial",
            "score": 0.55,
            "reason": "Needs the recommendation",
            "missing": ["recommendation"],
        }

    diagnostics, score = await validate_intent(
        {"summary": "Revenue grew 18%"},
        IntentConfig(intent="Executive finance briefing with recommendation"),
        severity=RuleSeverity.ERROR,
        judge=partial_then_completeness,
    )

    assert score == 0.55
    assert len(diagnostics) == 2
    partial, completeness = diagnostics
    assert partial.code == "intent_partial"
    assert partial.metadata["missing"] == ["recommendation"]
    assert completeness.code == "intent_completeness"
    assert completeness.metadata["missing"] == ["recommendation"]
    assert completeness.metadata["covered"] == ["revenue"]
    assert "Covered: revenue." in completeness.message
    assert "Missing: recommendation." in completeness.message
    assert calls[1]["prompt_variant"] == "completeness"
    assert calls[1]["missing"] == ["recommendation"]


@pytest.mark.asyncio
async def test_validate_intent_fail_carries_missing_items_into_message_and_metadata() -> None:
    async def fail_judge(*args, **kwargs):
        return {
            "result": "fail",
            "score": 0.1,
            "message": "",
            "missing": ["revenue", "risk"],
        }

    diagnostics, score = await validate_intent(
        "Weekend hiking checklist",
        IntentConfig(intent="Executive finance briefing with revenue and risk"),
        severity=RuleSeverity.ERROR,
        judge=fail_judge,
    )

    assert score == 0.1
    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.code == "intent_mismatch"
    assert diagnostic.severity == RuleSeverity.ERROR
    assert diagnostic.metadata["missing"] == ["revenue", "risk"]
    assert "Missing: revenue, risk." in diagnostic.message


@pytest.mark.asyncio
async def test_validate_intent_uses_keyword_fallback_without_judge() -> None:
    diagnostics, score = await validate_intent(
        "Revenue improved but no risk section",
        IntentConfig(intent="Executive finance briefing covering revenue and risk"),
        severity=RuleSeverity.ERROR,
        judge=None,
    )

    assert score is not None
    assert any(diag.code == "intent_keywords" for diag in diagnostics)
