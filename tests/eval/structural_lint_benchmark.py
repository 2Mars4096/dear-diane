"""Deterministic benchmark for Tier 1 structural lint latency.

Usage:
    PYTHONPATH=src python -m tests.eval.structural_lint_benchmark --repeats 1000
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

from dan.linter.config import RuleSeverity, StructuralConfig
from dan.linter.rules.structural import validate_structural

RESULTS_DIR = Path(__file__).parent / "results"


def _payload() -> dict[str, object]:
    return {
        "summary": "Acme Q4 finance summary with revenue, margin, cash, and risk notes. " * 20,
        "confidence_score": 0.82,
        "ticket_id": "FIN-2026",
        "recommendations": [
            "watch cash burn",
            "prepare margin drilldown",
        ],
        "metadata": {
            "owner": "finance",
            "priority": "high",
            "region": "global",
        },
    }


def _config() -> StructuralConfig:
    return StructuralConfig(
        json_schema={
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "confidence_score": {"type": "number"},
                "ticket_id": {"type": "string"},
                "recommendations": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "metadata": {"type": "object"},
            },
            "required": [
                "summary",
                "confidence_score",
                "ticket_id",
                "recommendations",
            ],
        },
        required_keys=[
            "summary",
            "confidence_score",
            "ticket_id",
            "recommendations",
        ],
        non_empty_keys=["summary", "recommendations"],
        ranges={"confidence_score": {"minimum": 0.0, "maximum": 1.0}},
        format_patterns={"ticket_id": r"[A-Z]{3}-\d{4}"},
        string_max_lengths={"summary": 2048},
    )


def _run_benchmark(*, repeats: int, max_median_ms: float) -> dict[str, object]:
    payload = _payload()
    config = _config()

    for _ in range(20):
        diagnostics, fixed_data, applied_fixes = validate_structural(
            payload,
            config,
            severity=RuleSeverity.ERROR,
        )
        assert diagnostics == []
        assert fixed_data is None
        assert applied_fixes == []

    samples_ms: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        diagnostics, fixed_data, applied_fixes = validate_structural(
            payload,
            config,
            severity=RuleSeverity.ERROR,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        assert diagnostics == []
        assert fixed_data is None
        assert applied_fixes == []
        samples_ms.append(elapsed_ms)

    ordered = sorted(samples_ms)
    median_ms = statistics.median(ordered)
    p95_ms = ordered[max(0, int(len(ordered) * 0.95) - 1)]
    return {
        "kind": "structural_lint_benchmark",
        "generated_at": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        "repeats": repeats,
        "max_median_ms": max_median_ms,
        "median_ms": median_ms,
        "mean_ms": statistics.fmean(ordered),
        "min_ms": ordered[0],
        "p95_ms": p95_ms,
        "max_ms": ordered[-1],
        "passes_gate": median_ms <= max_median_ms,
        "payload_bytes": len(json.dumps(payload)),
    }


def _write_report(report: dict[str, object]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output = RESULTS_DIR / f"{report['generated_at']}_structural_lint_benchmark.json"
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return output


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tests.eval.structural_lint_benchmark",
        description="Measure Tier 1 structural lint latency on a realistic payload.",
    )
    parser.add_argument("--repeats", type=int, default=1000, help="Timed structural-lint iterations.")
    parser.add_argument(
        "--max-median-ms",
        type=float,
        default=1.0,
        help="Maximum allowed median latency in milliseconds.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if the median latency exceeds --max-median-ms.",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    report = _run_benchmark(repeats=args.repeats, max_median_ms=args.max_median_ms)
    output = _write_report(report)
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"\nSaved report to {output}")
    if args.strict and not report["passes_gate"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
