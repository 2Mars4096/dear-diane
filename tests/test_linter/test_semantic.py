from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from dan.linter.config import RuleSeverity, SemanticConfig
from dan.linter.rules.semantic import (
    SEMANTIC_RULES,
    cosine_similarity,
    detect_language,
    text_from_data,
    validate_semantic,
)


def _local_minilm_snapshot() -> Path | None:
    base = Path("/Users/lizhi/.codex-accounts/Serb1989/.cache/huggingface/hub/models--sentence-transformers--all-MiniLM-L6-v2/snapshots")
    if not base.exists():
        return None
    snapshots = sorted(path for path in base.iterdir() if path.is_dir())
    return snapshots[-1] if snapshots else None


def test_semantic_rule_registry_describes_landed_rules() -> None:
    assert [rule.code for rule in SEMANTIC_RULES] == [
        "keyword_presence",
        "entity_presence",
        "language_detection",
        "contradiction_detection",
        "semantic_similarity",
    ]


def test_semantic_utilities_cover_dicts_and_vectors() -> None:
    assert '"summary": "Finance"' in text_from_data({"summary": "Finance"})
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert detect_language("This is an executive finance summary.")[0] == "en"
    assert detect_language("这是财务摘要。")[0] == "zh"


@pytest.mark.asyncio
async def test_validate_semantic_uses_mock_embedder_for_similarity() -> None:
    async def embed(text: str, model: str | None):
        if "finance" in text.lower():
            return [1.0, 0.0]
        return [0.0, 1.0]

    diagnostics, score = await validate_semantic(
        "Weekend hiking checklist",
        SemanticConfig(
            reference_text="Executive finance summary",
            min_similarity=0.8,
        ),
        severity=RuleSeverity.ERROR,
        embed=embed,
    )

    assert score == 0.0
    assert [diag.code for diag in diagnostics] == ["semantic_similarity"]


@pytest.mark.asyncio
async def test_validate_semantic_returns_keyword_overlap_score_without_embedder() -> None:
    diagnostics, score = await validate_semantic(
        "Finance risk summary",
        SemanticConfig(topic_keywords=["finance", "risk", "summary"]),
        severity=RuleSeverity.ERROR,
        embed=None,
    )

    assert diagnostics == []
    assert score == 1.0


@pytest.mark.asyncio
async def test_validate_semantic_uses_topic_keywords_as_similarity_reference_fallback() -> None:
    calls: list[str] = []

    async def embed(text: str, model: str | None):
        calls.append(text)
        if text == "finance summary":
            return [1.0, 0.0]
        return [0.0, 1.0]

    diagnostics, score = await validate_semantic(
        "finance summary",
        SemanticConfig(
            topic_keywords=["finance", "summary"],
            min_similarity=0.8,
        ),
        severity=RuleSeverity.ERROR,
        embed=embed,
    )

    assert diagnostics == []
    assert score == 1.0
    assert calls == ["finance summary", "finance summary"]


@pytest.mark.asyncio
async def test_validate_semantic_can_use_real_sentence_transformer_embedder() -> None:
    snapshot = _local_minilm_snapshot()
    if snapshot is None:
        pytest.skip("local all-MiniLM-L6-v2 snapshot not available")
    repo_root = Path(__file__).resolve().parents[2]
    script = textwrap.dedent(
        f"""
        import asyncio
        import json
        import sys

        from sentence_transformers import SentenceTransformer
        from dan.linter.config import RuleSeverity, SemanticConfig
        from dan.linter.rules.semantic import validate_semantic

        model = SentenceTransformer({str(snapshot)!r}, local_files_only=True)

        async def embed(text: str, model_name: str | None):
            vector = model.encode(text, normalize_embeddings=True)
            return vector.tolist()

        async def main():
            positive_text = "Executive finance summary for Acme revenue and margin trends."
            negative_text = "Weekend hiking checklist with water and trail snacks."
            reference_text = "Concise executive finance summary covering revenue and margins."

            _, positive_score = await validate_semantic(
                positive_text,
                SemanticConfig(reference_text=reference_text, min_similarity=0.0),
                severity=RuleSeverity.ERROR,
                embed=embed,
            )
            _, negative_score = await validate_semantic(
                negative_text,
                SemanticConfig(reference_text=reference_text, min_similarity=0.0),
                severity=RuleSeverity.ERROR,
                embed=embed,
            )
            threshold = (positive_score + negative_score) / 2.0
            positive_diagnostics, _ = await validate_semantic(
                positive_text,
                SemanticConfig(reference_text=reference_text, min_similarity=threshold),
                severity=RuleSeverity.ERROR,
                embed=embed,
            )
            negative_diagnostics, _ = await validate_semantic(
                negative_text,
                SemanticConfig(reference_text=reference_text, min_similarity=threshold),
                severity=RuleSeverity.ERROR,
                embed=embed,
            )
            print(json.dumps({{
                "positive_score": positive_score,
                "negative_score": negative_score,
                "positive_codes": [diag.code for diag in positive_diagnostics],
                "negative_codes": [diag.code for diag in negative_diagnostics],
            }}))

        asyncio.run(main())
        """
    )
    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{repo_root / 'src'}:{repo_root}" if not existing_pythonpath
        else f"{repo_root / 'src'}:{repo_root}:{existing_pythonpath}"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip().splitlines()
        detail = stderr[-1] if stderr else f"exit code {completed.returncode}"
        pytest.skip(f"real sentence-transformer runtime unavailable: {detail}")

    result = json.loads(completed.stdout)
    assert result["positive_score"] is not None
    assert result["negative_score"] is not None
    assert result["positive_score"] > result["negative_score"]
    assert result["positive_codes"] == []
    assert result["negative_codes"] == ["semantic_similarity"]


@pytest.mark.asyncio
async def test_validate_semantic_respects_min_keyword_ratio() -> None:
    diagnostics, score = await validate_semantic(
        "Finance memo with trend analysis",
        SemanticConfig(
            topic_keywords=["finance", "trend", "summary", "recommendation"],
            min_keyword_ratio=0.5,
        ),
        severity=RuleSeverity.ERROR,
        embed=None,
    )

    assert diagnostics == []
    assert score == 0.5

    fail_diagnostics, fail_score = await validate_semantic(
        "Finance memo with trend analysis",
        SemanticConfig(
            topic_keywords=["finance", "trend", "summary", "recommendation"],
            min_keyword_ratio=0.75,
        ),
        severity=RuleSeverity.ERROR,
        embed=None,
    )

    assert fail_score == 0.5
    assert [diag.code for diag in fail_diagnostics] == ["keyword_presence"]
    assert fail_diagnostics[0].metadata["missing"] == ["summary", "recommendation"]
    assert fail_diagnostics[0].metadata["ratio"] == 0.5


@pytest.mark.asyncio
async def test_validate_semantic_supports_fuzzy_entity_matching() -> None:
    diagnostics, score = await validate_semantic(
        "OpenAl released a new model update.",
        SemanticConfig(
            required_entities=["OpenAI"],
            entity_match_mode="fuzzy",
            entity_fuzzy_threshold=0.8,
        ),
        severity=RuleSeverity.ERROR,
        embed=None,
    )

    assert diagnostics == []
    assert score == 1.0


@pytest.mark.asyncio
async def test_validate_semantic_aggregates_keyword_and_entity_confidence() -> None:
    diagnostics, score = await validate_semantic(
        "Finance memo for Acme",
        SemanticConfig(
            topic_keywords=["finance", "memo"],
            required_entities=["Acme", "Globex"],
        ),
        severity=RuleSeverity.ERROR,
        embed=None,
    )

    assert [diag.code for diag in diagnostics] == ["entity_presence"]
    assert score == 0.5

    _, mean_score = await validate_semantic(
        "Finance memo for Acme",
        SemanticConfig(
            topic_keywords=["finance", "memo"],
            required_entities=["Acme", "Globex"],
            confidence_aggregation="mean",
        ),
        severity=RuleSeverity.ERROR,
        embed=None,
    )

    assert mean_score == 0.75


@pytest.mark.asyncio
async def test_validate_semantic_reports_language_mismatch_as_warning() -> None:
    diagnostics, score = await validate_semantic(
        "Este es un resumen financiero para liderazgo.",
        SemanticConfig(expected_language="en"),
        severity=RuleSeverity.ERROR,
        embed=None,
    )

    assert score is None
    assert [diag.code for diag in diagnostics] == ["language_detection"]
    assert diagnostics[0].severity == RuleSeverity.WARNING
    assert diagnostics[0].metadata["detected_language"] == "es"


@pytest.mark.asyncio
async def test_validate_semantic_detects_obvious_claim_contradictions() -> None:
    diagnostics, score = await validate_semantic(
        "Acme revenue increased to 8% this quarter.",
        SemanticConfig(
            contradiction_reference_text="Acme revenue decreased to 8% this quarter.",
        ),
        severity=RuleSeverity.ERROR,
        embed=None,
    )

    assert score == 0.0
    assert [diag.code for diag in diagnostics] == ["contradiction_detection"]
    assert diagnostics[0].metadata["reason"] == "opposing_polarity"
    assert diagnostics[0].metadata["opposing_groups"] == ["trend"]


@pytest.mark.asyncio
async def test_validate_semantic_ignores_non_comparable_reference_claims() -> None:
    diagnostics, score = await validate_semantic(
        "The roadmap remains on schedule.",
        SemanticConfig(
            contradiction_reference_text="Acme revenue decreased to 8% this quarter.",
        ),
        severity=RuleSeverity.ERROR,
        embed=None,
    )

    assert diagnostics == []
    assert score is None
