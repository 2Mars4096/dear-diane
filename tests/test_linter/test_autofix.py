from __future__ import annotations

from dan.linter.autofix.strategies import (
    ClampFix,
    CoerceFix,
    FillDefaultsFix,
    RefocusFix,
    RetryWithFeedbackFix,
    TruncateFix,
)
from dan.linter.config import LintConfig, RuleSeverity, SemanticConfig, StructuralConfig
from dan.linter.result import LintDiagnostic


def test_fill_defaults_fix_uses_schema_defaults() -> None:
    fix = FillDefaultsFix()
    diagnostic = LintDiagnostic(
        code="required_keys",
        message="Missing required keys",
        tier=1,
        severity="error",
        metadata={"missing": ["status"]},
    )
    config = StructuralConfig(
        json_schema={
            "type": "object",
            "properties": {"status": {"type": "string", "default": "todo"}},
        }
    )

    result = fix.fix({"name": "draft"}, diagnostic, config)

    assert result.fixed is True
    assert result.data == {"name": "draft", "status": "todo"}


def test_truncate_fix_prefers_word_boundary() -> None:
    fix = TruncateFix()
    diagnostic = LintDiagnostic(
        code="string_max_length",
        message="too long",
        tier=1,
        severity="error",
        metadata={"key": "draft", "max_length": 12},
    )

    result = fix.fix({"draft": "alpha beta gamma"}, diagnostic, StructuralConfig())

    assert result.fixed is True
    assert result.data["draft"] == "alpha beta"


def test_clamp_fix_enforces_numeric_bounds() -> None:
    fix = ClampFix()
    diagnostic = LintDiagnostic(
        code="numeric_range",
        message="above max",
        tier=1,
        severity="error",
        metadata={"key": "score", "minimum": 0, "maximum": 10},
    )

    result = fix.fix({"score": 11}, diagnostic, StructuralConfig())

    assert result.fixed is True
    assert result.data["score"] == 10


def test_coerce_fix_handles_schema_type_mismatch() -> None:
    fix = CoerceFix()
    diagnostic = LintDiagnostic(
        code="schema_conformance",
        message="'11' is not of type 'integer'",
        tier=1,
        severity="error",
        metadata={
            "path": ["score"],
            "validator": "type",
            "expected_type": "integer",
        },
    )

    result = fix.fix({"score": "11"}, diagnostic, StructuralConfig())

    assert result.fixed is True
    assert result.data["score"] == 11


def test_retry_with_feedback_fix_generates_contract_prompt() -> None:
    fix = RetryWithFeedbackFix()
    diagnostics = [
        LintDiagnostic(
            code="intent_mismatch",
            message="Output does not satisfy the downstream intent. Missing: recommendation.",
            tier=3,
            severity=RuleSeverity.ERROR,
            metadata={"missing": ["recommendation"], "covered": ["revenue"]},
        )
    ]

    result = fix.fix(
        "Executive finance briefing without recommendation",
        diagnostics[0],
        LintConfig(
            intent={"intent": "Executive finance briefing with recommendation"},
            severity=RuleSeverity.ERROR,
        ),
        diagnostics=diagnostics,
    )

    assert result.fixed is False
    assert result.feedback is not None
    assert "Receiving agent's need: Executive finance briefing with recommendation" in result.feedback
    assert "Already covered: revenue" in result.feedback
    assert "Still missing: recommendation" in result.feedback
    assert "Previous output:" in result.feedback


def test_refocus_fix_generates_semantic_prompt() -> None:
    fix = RefocusFix()
    diagnostics = [
        LintDiagnostic(
            code="keyword_presence",
            message="Missing topic keyword 'finance'",
            tier=2,
            severity=RuleSeverity.ERROR,
            metadata={},
        )
    ]

    result = fix.fix(
        "Weekend hiking checklist",
        diagnostics[0],
        LintConfig(
            semantic=SemanticConfig(
                topic_keywords=["finance", "risk"],
                reference_text="Executive finance summary for risk review",
            ),
            severity=RuleSeverity.ERROR,
        ),
        diagnostics=diagnostics,
    )

    assert result.fixed is False
    assert result.feedback is not None
    assert "Refocus the response on the required downstream subject matter." in result.feedback
    assert "Reference topic: Executive finance summary for risk review" in result.feedback
    assert "Required topic keywords: finance, risk" in result.feedback
