"""Run: python -m tests.quality_suite

Loads golden intent fixtures, evaluates through both codegen and intent
compiler paths, compares against baseline thresholds, and exits non-zero
on regression.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tests.quality_suite.codegen_runner import run_codegen_evaluation
from tests.quality_suite.intent_runner import run_intent_evaluation
from tests.quality_suite.loader import load_golden_intents
from tests.quality_suite.report import (
    GenerationQualityReport,
    build_report,
    compare_to_baseline,
)

_BASELINE_PATH = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "generation_quality_baseline.json"
)


def _print_report(report: GenerationQualityReport) -> None:
    header = f"=== {report.path.upper()} PATH ==="
    print(f"\n{header}")
    print(
        f"  Pass: {report.passed}/{report.total_intents} "
        f"({report.pass_rate:.0%})"
    )
    print(f"  Fail: {report.failed}")
    if report.failure_modes:
        print("  Failure modes:")
        for fm in report.failure_modes:
            print(f"    - {fm.error_type}: {fm.count}")
            for ex in fm.examples[:2]:
                print(f"        {ex[:120]}")
    print(
        f"  Latency: p50={report.latency_p50_ms:.0f}ms "
        f"p95={report.latency_p95_ms:.0f}ms"
    )
    if report.coverage_stats:
        cs = report.coverage_stats
        print(
            f"  Coverage: {cs.get('covered', 0)}/{cs.get('total', 0)} "
            f"covered, {cs.get('fallback', 0)} fallback"
        )


def main() -> int:
    print("Loading golden intents...")
    intents = load_golden_intents()
    print(f"  {len(intents)} fixtures loaded\n")

    # --- Codegen path ---
    print("Running codegen evaluation...")
    codegen_results = run_codegen_evaluation(intents)
    codegen_report = build_report(codegen_results, "codegen")

    # --- Intent path ---
    print("Running intent evaluation...")
    intent_results = run_intent_evaluation(intents)
    intent_report = build_report(intent_results, "intent")

    # --- Reports ---
    _print_report(codegen_report)
    _print_report(intent_report)

    # --- Baseline comparison ---
    regressions: list[str] = []
    if _BASELINE_PATH.exists():
        baseline = json.loads(_BASELINE_PATH.read_text())
        regressions += compare_to_baseline(
            codegen_report, baseline.get("codegen", {})
        )
        regressions += compare_to_baseline(
            intent_report, baseline.get("intent", {})
        )
    else:
        print(f"\n[warn] No baseline found at {_BASELINE_PATH}")

    if regressions:
        print("\n!!! REGRESSIONS DETECTED !!!")
        for r in regressions:
            print(f"  - {r}")
        return 1

    print("\nAll baselines met.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
