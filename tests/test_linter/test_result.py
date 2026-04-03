from __future__ import annotations

from dan.linter import LintDiagnostic, LintResult, RuleSeverity, Tier


def test_lint_diagnostic_coerces_int_tier_and_preserves_field_path() -> None:
    diagnostic = LintDiagnostic(
        code="schema_conformance",
        message="score is not an integer",
        tier=1,
        severity=RuleSeverity.ERROR,
        field_path=["score"],
        metadata={"validator": "type"},
    )

    assert diagnostic.tier == Tier.STRUCTURAL
    assert diagnostic.field_path == ["score"]


def test_lint_result_tracks_auto_fixed_and_elapsed_metadata() -> None:
    result = LintResult(
        passed=True,
        auto_fixed=True,
        fixed_data={"score": 10},
        applied_fixes=["clamp"],
        diagnostics=[],
        tier_reached=Tier.STRUCTURAL,
        elapsed_ms=1.25,
    )

    assert result.auto_fixed is True
    assert result.tier_reached == Tier.STRUCTURAL
    assert result.elapsed_ms == 1.25
