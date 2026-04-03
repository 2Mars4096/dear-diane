"""Async tiered lint engine."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Awaitable, Callable

from dan.linter.autofix.strategies import plan_model_autofixes
from dan.linter.config import LintConfig, RuleSeverity
from dan.linter.result import LintResult
from dan.linter.rules import Tier
from dan.linter.rules.intent import validate_intent
from dan.linter.rules.semantic import validate_semantic
from dan.linter.rules.structural import validate_structural


@dataclass
class LintRuntime:
    embed: Callable[[str, str | None], Awaitable[list[float]]] | None = None
    judge_intent: Callable[..., Awaitable[dict[str, Any]]] | None = None


async def lint(
    data: Any,
    config: LintConfig,
    *,
    runtime: LintRuntime | None = None,
) -> LintResult:
    started = time.perf_counter()
    runtime = runtime or LintRuntime()
    diagnostics = []
    fixed_data = None
    applied_fixes: list[str] = []
    suggested_fixes: list[str] = []
    retry_feedback: str | None = None
    refocus_feedback: str | None = None
    semantic_score: float | None = None
    intent_score: float | None = None
    tier_reached: Tier | None = None

    if not config.enabled:
        return LintResult(
            passed=True,
            diagnostics=[],
            tier_reached=None,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
        )

    if config.structural is not None:
        tier_reached = Tier.STRUCTURAL
        diag, fixed_data, fixes = validate_structural(
            data,
            config.structural,
            severity=config.severity,
            autofix=config.autofix,
        )
        diagnostics.extend(diag)
        applied_fixes.extend(fixes)
        if any(item.severity == RuleSeverity.ERROR for item in diag):
            auto_fixed = fixed_data is not None and bool(applied_fixes) and not diag
            return LintResult(
                passed=False,
                auto_fixed=auto_fixed,
                diagnostics=diagnostics,
                fixed_data=fixed_data,
                applied_fixes=applied_fixes,
                tier_reached=tier_reached,
                elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
            )

    candidate = fixed_data if fixed_data is not None else data

    if config.semantic is not None:
        tier_reached = Tier.SEMANTIC
        diag, semantic_score = await validate_semantic(
            candidate,
            config.semantic,
            severity=config.severity,
            embed=runtime.embed,
        )
        diagnostics.extend(diag)

    if config.intent is not None:
        should_run_tier3 = semantic_score is None or semantic_score < config.tier3_threshold
        if should_run_tier3:
            tier_reached = Tier.INTENT
            diag, intent_score = await validate_intent(
                candidate,
                config.intent,
                severity=config.severity,
                judge=runtime.judge_intent,
            )
            diagnostics.extend(diag)

    passed = not any(diag.severity == RuleSeverity.ERROR for diag in diagnostics)
    if fixed_data is not None and applied_fixes and all(d.tier == 1 for d in diagnostics):
        passed = True
    elif diagnostics:
        suggested_fixes, retry_feedback, refocus_feedback = plan_model_autofixes(
            candidate,
            diagnostics,
            config,
            config.autofix,
        )

    auto_fixed = fixed_data is not None and bool(applied_fixes)

    return LintResult(
        passed=passed,
        auto_fixed=auto_fixed,
        diagnostics=diagnostics,
        fixed_data=fixed_data,
        applied_fixes=applied_fixes,
        suggested_fixes=suggested_fixes,
        retry_feedback=retry_feedback,
        refocus_feedback=refocus_feedback,
        semantic_score=semantic_score,
        intent_score=intent_score,
        tier_reached=tier_reached,
        elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
    )
