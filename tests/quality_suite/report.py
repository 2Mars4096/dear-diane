"""Generation quality report and baseline comparison.

Aggregates ``IntentResult`` lists into structured ``GenerationQualityReport``
objects with pass-rate, failure-mode breakdown, latency percentiles, and
coverage statistics.  ``compare_to_baseline`` detects regressions against a
saved baseline.
"""

from __future__ import annotations

import statistics
from collections import Counter

from pydantic import BaseModel, Field

from tests.quality_suite.codegen_runner import IntentResult


# ---------------------------------------------------------------------------
# Report models
# ---------------------------------------------------------------------------


class FailureMode(BaseModel):
    error_type: str
    count: int
    examples: list[str] = Field(default_factory=list)


class GenerationQualityReport(BaseModel):
    path: str  # "codegen" | "intent" | "combined"
    total_intents: int
    passed: int
    failed: int
    pass_rate: float
    failure_modes: list[FailureMode] = Field(default_factory=list)
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    coverage_stats: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------


def build_report(
    results: list[IntentResult], path: str
) -> GenerationQualityReport:
    """Build a quality report from intent results."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed

    pass_rate = passed / total if total > 0 else 0.0

    # Failure modes
    error_counter: Counter[str] = Counter()
    error_examples: dict[str, list[str]] = {}
    for r in results:
        if not r.passed and r.error_type:
            error_counter[r.error_type] += 1
            examples = error_examples.setdefault(r.error_type, [])
            if len(examples) < 3 and r.error_message:
                examples.append(r.error_message)

    failure_modes = [
        FailureMode(
            error_type=et,
            count=count,
            examples=error_examples.get(et, []),
        )
        for et, count in error_counter.most_common()
    ]

    # Latency percentiles
    latencies = [r.latency_ms for r in results if r.latency_ms > 0]
    p50 = _percentile(latencies, 50) if latencies else 0.0
    p95 = _percentile(latencies, 95) if latencies else 0.0

    # Coverage stats (intent path only)
    coverage_stats: dict = {}
    if path == "intent":
        covered = sum(
            1 for r in results
            if r.topology_check.get("coverage", {}).get("fully_covered", False)
        )
        fallback = sum(
            1 for r in results
            if r.error_type == "coverage_fallback"
        )
        coverage_stats = {
            "covered": covered,
            "fallback": fallback,
            "total": total,
        }

    return GenerationQualityReport(
        path=path,
        total_intents=total,
        passed=passed,
        failed=failed,
        pass_rate=pass_rate,
        failure_modes=failure_modes,
        latency_p50_ms=p50,
        latency_p95_ms=p95,
        coverage_stats=coverage_stats,
    )


# ---------------------------------------------------------------------------
# Baseline comparison
# ---------------------------------------------------------------------------


def compare_to_baseline(
    report: GenerationQualityReport, baseline: dict
) -> list[str]:
    """Compare report to baseline thresholds.  Return list of regressions.

    Expected baseline keys::

        {"total": 18, "min_pass": 15, "max_fail": 3}
        # intent path adds: "min_covered": 10

    Returns an empty list when all thresholds are met.
    """
    if not baseline:
        return []

    regressions: list[str] = []

    expected_total = baseline.get("total")
    if expected_total is not None and report.total_intents != expected_total:
        regressions.append(
            f"[{report.path}] total intents {report.total_intents} != "
            f"baseline total {expected_total}"
        )

    min_pass = baseline.get("min_pass")
    if min_pass is not None and report.passed < min_pass:
        regressions.append(
            f"[{report.path}] pass count {report.passed} < "
            f"baseline min_pass {min_pass}"
        )

    max_fail = baseline.get("max_fail")
    if max_fail is not None and report.failed > max_fail:
        regressions.append(
            f"[{report.path}] fail count {report.failed} > "
            f"baseline max_fail {max_fail}"
        )

    min_covered = baseline.get("min_covered")
    if min_covered is not None:
        covered = report.coverage_stats.get("covered", 0)
        if covered < min_covered:
            regressions.append(
                f"[{report.path}] covered count {covered} < "
                f"baseline min_covered {min_covered}"
            )

    return regressions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _percentile(data: list[float], pct: float) -> float:
    """Compute the *pct*-th percentile of *data*."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    n = len(sorted_data)
    if n == 1:
        return sorted_data[0]
    k = (pct / 100.0) * (n - 1)
    lo = int(k)
    hi = min(lo + 1, n - 1)
    frac = k - lo
    return sorted_data[lo] + frac * (sorted_data[hi] - sorted_data[lo])
