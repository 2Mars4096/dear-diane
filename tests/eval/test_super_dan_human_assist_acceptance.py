from __future__ import annotations

import json
from pathlib import Path

from tests.eval import super_dan_human_assist_acceptance as acceptance


def test_human_assist_cases_cover_academic_and_market_handoffs() -> None:
    cases = acceptance.human_assist_cases()

    assert {case.case_id for case in cases} == {
        "long-academic-first-draft",
        "long-market-strategy-framework",
    }
    assert all("human-review.md" in case.required_paths for case in cases)


def test_academic_first_draft_package_passes_review_readiness_gate(
    tmp_path: Path,
) -> None:
    _write_academic_fixture(tmp_path)
    case = acceptance.get_human_assist_case("long-academic-first-draft")

    report = acceptance.validate_human_assist_workspace(case, tmp_path)
    payload = acceptance.report_payload(report)

    assert report.machine_ready is True
    assert report.human_handoff_ready is True
    assert report.passed is True
    assert payload["claim_boundary"].startswith("This gate proves review readiness")


def test_academic_gate_rejects_supported_claim_without_source_and_fake_citation(
    tmp_path: Path,
) -> None:
    _write_academic_fixture(tmp_path)
    ledger_path = tmp_path / "evidence-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["claims"][0]["source_refs"] = []
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    manuscript_path = tmp_path / "manuscript.md"
    manuscript_path.write_text(
        manuscript_path.read_text(encoding="utf-8") + "\nDOI: 10.0000/example\n",
        encoding="utf-8",
    )

    report = acceptance.validate_human_assist_workspace(
        acceptance.get_human_assist_case("long-academic-first-draft"),
        tmp_path,
    )
    failed = {check.name for check in report.domain.checks if not check.passed}

    assert report.passed is False
    assert "evidence-ledger:claim-contract" in failed
    assert "no-disguised-placeholder-citations" in failed


def test_academic_gate_requires_claim_links_and_resolvable_sources(
    tmp_path: Path,
) -> None:
    _write_academic_fixture(tmp_path)
    ledger_path = tmp_path / "evidence-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["claims"][0]["source_refs"] = ["missing-source"]
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    manuscript_path = tmp_path / "manuscript.md"
    manuscript_path.write_text(
        manuscript_path.read_text(encoding="utf-8").replace(
            "External validity remains uncertain [C2].",
            "External validity remains uncertain.",
        ),
        encoding="utf-8",
    )

    report = acceptance.validate_human_assist_workspace(
        acceptance.get_human_assist_case("long-academic-first-draft"),
        tmp_path,
    )
    failed = {check.name for check in report.domain.checks if not check.passed}

    assert "evidence-ledger:manuscript-links" in failed
    assert "evidence-ledger:source-resolution" in failed


def test_market_strategy_package_passes_test_framework_gate(
    tmp_path: Path,
) -> None:
    _write_market_fixture(tmp_path)
    case = acceptance.get_human_assist_case("long-market-strategy-framework")

    report = acceptance.validate_human_assist_workspace(case, tmp_path)

    assert report.machine_ready is True
    assert report.human_handoff_ready is True
    assert report.passed is True


def test_market_gate_rejects_non_point_in_time_profit_guarantee_and_weak_tests(
    tmp_path: Path,
) -> None:
    _write_market_fixture(tmp_path)
    assumptions_path = tmp_path / "assumptions.json"
    assumptions = json.loads(assumptions_path.read_text(encoding="utf-8"))
    assumptions["point_in_time"] = False
    assumptions_path.write_text(json.dumps(assumptions), encoding="utf-8")
    strategy_path = tmp_path / "strategy.md"
    strategy_path.write_text(
        strategy_path.read_text(encoding="utf-8") + "\nGuaranteed profit.\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_strategy.py").write_text(
        "def test_placeholder():\n    assert True\n",
        encoding="utf-8",
    )

    report = acceptance.validate_human_assist_workspace(
        acceptance.get_human_assist_case("long-market-strategy-framework"),
        tmp_path,
    )
    failed = {check.name for check in report.domain.checks if not check.passed}

    assert report.passed is False
    assert "no-unsupported-profit-guarantee" in failed
    assert "assumptions-safety-values" in failed
    assert "strategy-regression-tests" in failed


def test_market_gate_executes_strategy_tests_against_backtest(tmp_path: Path) -> None:
    _write_market_fixture(tmp_path)
    backtest_path = tmp_path / "backtest.py"
    backtest_path.write_text(
        backtest_path.read_text(encoding="utf-8").replace(
            "return [float(value) - cost for value in returns]",
            "return [float(value) for value in returns]",
        ),
        encoding="utf-8",
    )

    report = acceptance.validate_human_assist_workspace(
        acceptance.get_human_assist_case("long-market-strategy-framework"),
        tmp_path,
    )
    checks = {check.name: check for check in report.domain.checks}

    assert checks["strategy-test-contract"].passed is True
    assert checks["strategy-regression-tests"].passed is False
    assert "failed" in checks["strategy-regression-tests"].detail


def test_human_handoff_requires_unchecked_review_decisions(
    tmp_path: Path,
) -> None:
    _write_academic_fixture(tmp_path)
    review = tmp_path / "human-review.md"
    review.write_text(review.read_text(encoding="utf-8").replace("[ ]", "[x]"))

    report = acceptance.validate_human_assist_workspace(
        acceptance.get_human_assist_case("long-academic-first-draft"),
        tmp_path,
    )

    assert report.machine_ready is True
    assert report.human_handoff_ready is False
    assert report.passed is False


def _write_academic_fixture(root: Path) -> None:
    (root / "manuscript.md").write_text(
        """# Title
## Abstract
Draft abstract.
## Introduction
Question and contribution.
## Related Literature
Grounded comparison.
## Data
Point-in-time sample.
## Methods
Identification and estimation.
## Results
Provisional result linked to claim C1.
## Discussion
Interpretation.
## Limitations
External validity remains uncertain [C2].
## Conclusion
Summary.
## References
[S1] Verified source.
""",
        encoding="utf-8",
    )
    (root / "evidence-ledger.json").write_text(
        json.dumps(
            {
                "claims": [
                    {
                        "claim_id": "C1",
                        "claim": "The estimated relationship is positive.",
                        "status": "supported",
                        "source_refs": ["S1", "table-1"],
                    },
                    {
                        "claim_id": "C2",
                        "claim": "External validity remains uncertain.",
                        "status": "unresolved",
                        "source_refs": [],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (root / "reproducibility.md").write_text(
        """# Reproducibility
## Data provenance
Inputs and dates.
## Environment
Python and package versions.
## Execution
Commands and order.
## Random seed
Seed 42.
## Limitations
Unavailable inputs and expected differences.
""",
        encoding="utf-8",
    )
    (root / "human-review.md").write_text(
        """# Researcher review
- [ ] Verify every major claim.
- [ ] Verify every citation and source.
- [ ] Approve the method and identification.
- [ ] Reproduce every result.
- [ ] Assess each limitation.
- [ ] Record the final decision and required revision.
""",
        encoding="utf-8",
    )


def _write_market_fixture(root: Path) -> None:
    (root / "strategy.md").write_text(
        """# Strategy
## Hypothesis
Cross-sectional information may predict returns.
## Universe
Liquid names with point-in-time membership.
## Signal
Lagged composite rank.
## Entry
Enter after signal publication.
## Exit
Rebalance monthly.
## Point-in-time data and leakage
Use release timestamps and purge overlapping labels.
## Walk-forward evaluation
Expanding train and held-out test windows.
## Transaction costs and slippage
Deduct both from every rebalance.
## Failure criteria
Stop if out-of-sample performance or stability fails.
## Paper testing
No live capital or order authority.
""",
        encoding="utf-8",
    )
    (root / "assumptions.json").write_text(
        json.dumps(
            {
                "data_as_of": "2026-07-27",
                "point_in_time": True,
                "transaction_cost_bps": 8,
                "slippage_bps": 5,
                "walk_forward": {"train_months": 36, "test_months": 6},
                "leakage_controls": ["release_lag", "purged_labels"],
                "paper_only": True,
            }
        ),
        encoding="utf-8",
    )
    (root / "backtest.py").write_text(
        """def compute_signal(rows):
    return [float(row["feature"]) for row in rows]

def walk_forward_backtest(rows, train_size=3, test_size=1):
    windows = []
    for test_start in range(train_size, len(rows), test_size):
        test_end = min(test_start + test_size, len(rows))
        windows.append(
            {
                "train_indices": list(range(test_start - train_size, test_start)),
                "test_indices": list(range(test_start, test_end)),
            }
        )
    return windows

def apply_costs(returns, bps):
    cost = float(bps) / 10_000
    return [float(value) - cost for value in returns]

def evaluate_metrics(returns):
    values = list(returns)
    return {
        "count": len(values),
        "mean": sum(values) / len(values) if values else 0.0,
    }
""",
        encoding="utf-8",
    )
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_strategy.py").write_text(
        """from backtest import apply_costs, walk_forward_backtest

def test_leakage_guard():
    windows = walk_forward_backtest(list(range(7)))
    assert all(
        max(window["train_indices"]) < min(window["test_indices"])
        for window in windows
    )

def test_cost_deduction():
    net = apply_costs([0.02, -0.01], 10)
    assert net == [0.019, -0.011]

def test_walk_forward_split():
    windows = walk_forward_backtest(list(range(7)), train_size=3, test_size=2)
    assert [window["test_indices"] for window in windows] == [[3, 4], [5, 6]]
""",
        encoding="utf-8",
    )
    (root / "human-review.md").write_text(
        """# Human review
- [ ] Verify data lineage and timestamps.
- [ ] Challenge leakage controls.
- [ ] Approve cost and slippage assumptions.
- [ ] Inspect parameter and regime stability.
- [ ] Apply failure criteria.
- [ ] Review paper-test results only.
- [ ] Record the final decision.
""",
        encoding="utf-8",
    )
