from __future__ import annotations

import ast
from pathlib import Path

import pytest

from dan.linter import IntentConfig, LintConfig, RuleSeverity, SemanticConfig, StructuralConfig, Tier, lint


@pytest.mark.asyncio
async def test_lint_short_circuits_after_structural_error() -> None:
    embed_calls = 0
    judge_calls = 0

    async def embed(*args, **kwargs):
        nonlocal embed_calls
        embed_calls += 1
        return [1.0, 0.0]

    async def judge(*args, **kwargs):
        nonlocal judge_calls
        judge_calls += 1
        return {"passed": True, "score": 1.0}

    result = await lint(
        {"score": "bad"},
        LintConfig(
            structural=StructuralConfig(
                json_schema={"type": "object", "properties": {"score": {"type": "integer"}}}
            ),
            semantic=SemanticConfig(reference_text="finance summary", min_similarity=0.9),
            intent=IntentConfig(intent="finance summary"),
            severity=RuleSeverity.ERROR,
        ),
        runtime=type(
            "Runtime",
            (),
            {"embed": staticmethod(embed), "judge_intent": staticmethod(judge)},
        )(),
    )

    assert result.passed is False
    assert result.tier_reached == Tier.STRUCTURAL
    assert embed_calls == 0
    assert judge_calls == 0


@pytest.mark.asyncio
async def test_lint_disabled_config_returns_clean_result() -> None:
    result = await lint(
        {"score": "bad"},
        LintConfig(
            structural=StructuralConfig(
                json_schema={"type": "object", "properties": {"score": {"type": "integer"}}}
            ),
            enabled=False,
        ),
    )

    assert result.passed is True
    assert result.diagnostics == []
    assert result.tier_reached is None
    assert result.elapsed_ms is not None


@pytest.mark.asyncio
async def test_lint_result_reports_elapsed_and_tier_reached() -> None:
    result = await lint(
        "finance summary",
        LintConfig(
            semantic=SemanticConfig(topic_keywords=["finance"]),
            severity=RuleSeverity.ERROR,
        ),
    )

    assert result.passed is True
    assert result.tier_reached == Tier.SEMANTIC
    assert result.elapsed_ms is not None
    assert result.elapsed_ms >= 0


def test_linter_package_does_not_import_engine_worker_or_models() -> None:
    package_root = Path("src/dan/linter")
    banned_prefixes = ("dan.engine", "dan.worker", "dan.models")
    offenders: list[str] = []

    for path in sorted(package_root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(banned_prefixes):
                        offenders.append(f"{path}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.startswith(banned_prefixes):
                    offenders.append(f"{path}: from {module} import ...")

    assert offenders == []
