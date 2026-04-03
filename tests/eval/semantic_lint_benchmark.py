"""Deterministic benchmark for Tier 2 semantic lint latency.

Usage:
    PYTHONPATH=src python -m tests.eval.semantic_lint_benchmark --repeats 500
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

from dan.linter.config import RuleSeverity, SemanticConfig
from dan.linter.rules.semantic import validate_semantic

RESULTS_DIR = Path(__file__).parent / "results"


async def _mock_embed(text: str, model: str | None) -> list[float]:
    lowered = text.lower()
    if "finance" in lowered or "revenue" in lowered or "summary" in lowered:
        return [1.0, 0.0, 0.0]
    return [0.0, 1.0, 0.0]


async def _run_benchmark(*, repeats: int, max_median_ms: float) -> dict[str, object]:
    config = SemanticConfig(
        reference_text="Executive finance summary for quarterly revenue and margin trends.",
        topic_keywords=["finance", "revenue", "summary", "margin"],
        required_entities=["Acme", "Q4"],
        min_keyword_ratio=0.5,
        min_similarity=0.7,
        confidence_aggregation="min",
    )
    data = {
        "summary": "Acme Q4 finance summary with revenue and margin highlights.",
        "notes": "Prepared for the executive briefing.",
    }

    samples_ms: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        diagnostics, score = await validate_semantic(
            data,
            config,
            severity=RuleSeverity.ERROR,
            embed=_mock_embed,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        assert diagnostics == []
        assert score is not None
        samples_ms.append(elapsed_ms)

    ordered = sorted(samples_ms)
    median_ms = statistics.median(ordered)
    p95_ms = ordered[max(0, int(len(ordered) * 0.95) - 1)]
    return {
        "kind": "semantic_lint_benchmark",
        "generated_at": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        "repeats": repeats,
        "max_median_ms": max_median_ms,
        "median_ms": median_ms,
        "mean_ms": statistics.fmean(ordered),
        "min_ms": ordered[0],
        "p95_ms": p95_ms,
        "max_ms": ordered[-1],
        "passes_gate": median_ms <= max_median_ms,
    }


def _write_report(report: dict[str, object]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output = RESULTS_DIR / f"{report['generated_at']}_semantic_lint_benchmark.json"
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return output


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tests.eval.semantic_lint_benchmark",
        description="Measure Tier 2 semantic lint latency with a mock embedder.",
    )
    parser.add_argument("--repeats", type=int, default=500, help="Timed semantic-lint iterations.")
    parser.add_argument(
        "--max-median-ms",
        type=float,
        default=10.0,
        help="Maximum allowed median latency in milliseconds.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if the median latency exceeds --max-median-ms.",
    )
    return parser


async def _main_async(args: argparse.Namespace) -> int:
    report = await _run_benchmark(repeats=args.repeats, max_median_ms=args.max_median_ms)
    output = _write_report(report)
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"\nSaved report to {output}")
    if args.strict and not report["passes_gate"]:
        return 1
    return 0


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main_async(args)))


if __name__ == "__main__":
    main()
