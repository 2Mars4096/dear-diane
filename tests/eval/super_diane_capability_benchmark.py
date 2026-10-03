"""Capability benchmark matrix and scoring helpers for Diane.

This module is intentionally deterministic and CI-safe. It freezes the task
families Diane should be evaluated on, and it can score real
``.dan-super/.../events.jsonl`` traces produced by live operator runs.

Usage:
    PYTHONPATH=src python -m tests.eval.super_diane_capability_benchmark \
        .dan-super/runs/turn-01/events.jsonl
"""

from __future__ import annotations

import argparse
import math
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from tests.eval.super_diane_flagship_acceptance import flagship_cases

RESULTS_DIR = Path(__file__).parent / "results"


@dataclass(frozen=True, slots=True)
class SuperDanCapabilityCase:
    case_id: str
    family: str
    task_length: str
    prompt: str
    expected_surfaces: tuple[str, ...]
    expected_tool_families: tuple[str, ...]
    required_outputs: tuple[str, ...]
    target_wall_time_seconds: float
    target_total_tokens: int
    min_delivery_score: float
    min_overall_score: float = 0.70
    min_changed_artifacts: int = 0
    requires_validation: bool = False
    capability_packs: tuple[str, ...] = ()
    scoring_dimensions: tuple[str, ...] = (
        "delivered_performance",
        "token_efficiency",
        "time_efficiency",
        "validation_evidence",
    )


@dataclass(frozen=True, slots=True)
class SuperDanCapabilityObservation:
    case_id: str
    status: str
    wall_time_seconds: float
    token_usage: Mapping[str, int] = field(default_factory=dict)
    validation_passed: bool = False
    artifacts_changed: tuple[str, ...] = ()
    tests_passed: int = 0
    tests_failed: int = 0
    latest_test_exit_code: int | None = None
    event_count: int = 0
    tool_call_count: int = 0
    output_quality_score: float | None = None
    answer_present: bool = False
    blockers: tuple[str, ...] = ()
    source_log: str | None = None
    domain_acceptance_passed: bool | None = None
    domain_acceptance_detail: str | None = None

    @property
    def total_tokens(self) -> int:
        return int(self.token_usage.get("total_tokens", 0) or 0)


@dataclass(frozen=True, slots=True)
class SuperDanCapabilityScore:
    case_id: str
    passed: bool
    overall_score: float
    delivered_performance: float
    token_efficiency: float
    time_efficiency: float
    validation_evidence: float
    reasons: tuple[str, ...]


def _benchmark_cases() -> list[SuperDanCapabilityCase]:
    """Frozen Diane capability matrix from short to long operator tasks."""
    cases = [
        SuperDanCapabilityCase(
            case_id="short-note-create",
            family="workspace_artifact",
            task_length="short",
            prompt="Create a concise markdown note in the workspace and report the path.",
            expected_surfaces=("super_tui", "workspace_gui", "telegram"),
            expected_tool_families=("files",),
            required_outputs=("note.md",),
            target_wall_time_seconds=90,
            target_total_tokens=8_000,
            min_delivery_score=0.70,
            min_changed_artifacts=1,
        ),
        SuperDanCapabilityCase(
            case_id="short-validation-truth",
            family="validation_gate",
            task_length="short",
            prompt="Tell me whether the current focused tests pass, using fresh validation.",
            expected_surfaces=("super_tui", "workspace_gui"),
            expected_tool_families=("shell", "workspace_check"),
            required_outputs=(),
            target_wall_time_seconds=60,
            target_total_tokens=6_000,
            min_delivery_score=0.75,
            requires_validation=True,
        ),
        SuperDanCapabilityCase(
            case_id="medium-existing-site-polish",
            family="website_patch",
            task_length="medium",
            prompt=(
                "Improve the existing static site layout and visual hierarchy without "
                "rewriting it from scratch."
            ),
            expected_surfaces=("super_tui", "workspace_gui"),
            expected_tool_families=("files", "shell", "workspace_check"),
            required_outputs=("index.html", "styles.css", "app.js", "README.md"),
            target_wall_time_seconds=300,
            target_total_tokens=40_000,
            min_delivery_score=0.78,
            min_changed_artifacts=3,
            requires_validation=True,
        ),
        SuperDanCapabilityCase(
            case_id="medium-source-repair",
            family="source_repair",
            task_length="medium",
            prompt=(
                "Fix the failing Python behavior, keep the patch bounded, and run the "
                "focused regression tests."
            ),
            expected_surfaces=("super_tui", "workspace_gui", "telegram"),
            expected_tool_families=("files", "shell", "git"),
            required_outputs=("src", "tests"),
            target_wall_time_seconds=420,
            target_total_tokens=55_000,
            min_delivery_score=0.80,
            min_changed_artifacts=1,
            requires_validation=True,
        ),
        SuperDanCapabilityCase(
            case_id="medium-research-report",
            family="research_report",
            task_length="medium",
            prompt=(
                "Research the current market context, cite grounded evidence, and save "
                "a markdown report."
            ),
            expected_surfaces=("super_tui", "workspace_gui", "telegram"),
            expected_tool_families=("files", "web_search"),
            required_outputs=("report.md",),
            target_wall_time_seconds=480,
            target_total_tokens=65_000,
            min_delivery_score=0.76,
            min_changed_artifacts=1,
            requires_validation=True,
        ),
        SuperDanCapabilityCase(
            case_id="long-greenfield-project",
            family="greenfield_project",
            task_length="long",
            prompt=(
                "Build a small but usable CLI/API project with tests, documentation, "
                "and a focused local smoke check."
            ),
            expected_surfaces=("super_tui", "workspace_gui"),
            expected_tool_families=("files", "shell", "git", "workspace_check"),
            required_outputs=("pyproject.toml", "README.md", "src", "tests"),
            target_wall_time_seconds=1_200,
            target_total_tokens=140_000,
            min_delivery_score=0.82,
            min_changed_artifacts=5,
            requires_validation=True,
        ),
        SuperDanCapabilityCase(
            case_id="long-followup-adaptation",
            family="followup_adaptation",
            task_length="long",
            prompt=(
                "Continue from the prior generated project, add a scoped feature, and "
                "preserve existing behavior."
            ),
            expected_surfaces=("super_tui", "workspace_gui", "telegram"),
            expected_tool_families=("files", "shell", "git"),
            required_outputs=("src", "tests", "README.md"),
            target_wall_time_seconds=900,
            target_total_tokens=110_000,
            min_delivery_score=0.82,
            min_changed_artifacts=2,
            requires_validation=True,
        ),
        SuperDanCapabilityCase(
            case_id="long-cross-surface-operator",
            family="cross_surface_operator",
            task_length="long",
            prompt=(
                "Use browser or desktop evidence, update the local workspace artifact, "
                "and prepare an externally safe status summary."
            ),
            expected_surfaces=("super_tui", "workspace_gui", "telegram"),
            expected_tool_families=("browser", "desktop", "files", "adapters"),
            required_outputs=("summary.md",),
            target_wall_time_seconds=900,
            target_total_tokens=120_000,
            min_delivery_score=0.78,
            min_changed_artifacts=1,
            requires_validation=True,
            capability_packs=("browser_control", "desktop_control"),
        ),
        SuperDanCapabilityCase(
            case_id="long-indie-game-vertical-slice",
            family="human_assist_indie_game",
            task_length="long",
            prompt=(
                "Build one original, compact indie-game vertical slice with a complete "
                "play loop, deterministic browser checks, a short playtest protocol, "
                "and a human review handoff. Aim for a coherent prototype, not AAA parity."
            ),
            expected_surfaces=("workspace_gui",),
            expected_tool_families=("files", "shell", "browser", "workspace_check"),
            required_outputs=(
                "index.html",
                "styles.css",
                "game.js",
                "README.md",
                "human-review.md",
            ),
            target_wall_time_seconds=1_800,
            target_total_tokens=240_000,
            min_delivery_score=0.80,
            min_overall_score=0.75,
            min_changed_artifacts=5,
            requires_validation=True,
            capability_packs=("browser_control",),
        ),
        SuperDanCapabilityCase(
            case_id="long-academic-first-draft",
            family="human_assist_academic_draft",
            task_length="long",
            prompt=(
                "Given an explicit research question, supplied sources, and available "
                "data/code, produce a complete first manuscript draft with a claim-to-"
                "source ledger, methods and reproducibility audit, unresolved-evidence "
                "markers, and a section-by-section human review checklist. Do not claim "
                "journal readiness or invent citations/results."
            ),
            expected_surfaces=("workspace_gui",),
            expected_tool_families=("files", "shell", "web_search", "workspace_check"),
            required_outputs=(
                "manuscript.md",
                "evidence-ledger.json",
                "reproducibility.md",
                "human-review.md",
            ),
            target_wall_time_seconds=2_400,
            target_total_tokens=300_000,
            min_delivery_score=0.80,
            min_overall_score=0.75,
            min_changed_artifacts=4,
            requires_validation=True,
        ),
        SuperDanCapabilityCase(
            case_id="long-market-strategy-framework",
            family="human_assist_market_strategy",
            task_length="long",
            prompt=(
                "Develop a falsifiable stock-selection or prediction-market strategy "
                "framework with point-in-time feature definitions, leakage controls, "
                "walk-forward evaluation, fees/slippage assumptions, failure criteria, "
                "paper-testing code, and an explicit human approval checklist. Do not "
                "place trades or present an untested framework as profitable."
            ),
            expected_surfaces=("workspace_gui",),
            expected_tool_families=("files", "shell", "web_search", "workspace_check"),
            required_outputs=(
                "strategy.md",
                "assumptions.json",
                "backtest.py",
                "tests",
                "human-review.md",
            ),
            target_wall_time_seconds=2_400,
            target_total_tokens=300_000,
            min_delivery_score=0.82,
            min_overall_score=0.76,
            min_changed_artifacts=5,
            requires_validation=True,
        ),
    ]
    cases.extend(
        SuperDanCapabilityCase(
            case_id=flagship.case_id,
            family=(
                "flagship_product_site"
                if flagship.case_id == "flagship-premium-site"
                else "flagship_browser_rts"
            ),
            task_length="long",
            prompt=flagship.prompt,
            expected_surfaces=("workspace_gui",),
            expected_tool_families=("files", "shell", "browser", "workspace_check"),
            required_outputs=flagship.required_files,
            target_wall_time_seconds=flagship.target_wall_time_seconds,
            target_total_tokens=flagship.target_total_tokens,
            min_delivery_score=0.85,
            min_overall_score=0.78,
            min_changed_artifacts=len(flagship.required_files),
            requires_validation=True,
            capability_packs=("browser_control",),
            scoring_dimensions=(
                "delivered_performance",
                "token_efficiency",
                "time_efficiency",
                "validation_evidence",
                "artifact_acceptance",
                "steering_delivery",
            ),
        )
        for flagship in flagship_cases()
    )
    return cases


def score_observed_run(
    case: SuperDanCapabilityCase,
    observation: SuperDanCapabilityObservation,
) -> SuperDanCapabilityScore:
    status_score = 1.0 if _is_completed_status(observation.status) else 0.0
    validation_score = _validation_score(case, observation)
    artifact_score = _artifact_score(case, observation)
    quality_score = _quality_score(observation)

    delivered_performance = _clamp01(
        (0.30 * status_score)
        + (0.25 * validation_score)
        + (0.25 * artifact_score)
        + (0.20 * quality_score)
    )
    time_efficiency = _bounded_efficiency(
        budget=case.target_wall_time_seconds,
        actual=observation.wall_time_seconds,
    )
    token_efficiency = _bounded_efficiency(
        budget=float(case.target_total_tokens),
        actual=float(observation.total_tokens),
    )
    validation_evidence = _evidence_score(case, observation)
    overall_score = _clamp01(
        (0.45 * delivered_performance)
        + (0.20 * token_efficiency)
        + (0.20 * time_efficiency)
        + (0.15 * validation_evidence)
    )

    reasons = _score_reasons(
        case=case,
        observation=observation,
        delivered_performance=delivered_performance,
        overall_score=overall_score,
        token_efficiency=token_efficiency,
    )
    passed = not reasons
    return SuperDanCapabilityScore(
        case_id=case.case_id,
        passed=passed,
        overall_score=round(overall_score, 4),
        delivered_performance=round(delivered_performance, 4),
        token_efficiency=round(token_efficiency, 4),
        time_efficiency=round(time_efficiency, 4),
        validation_evidence=round(validation_evidence, 4),
        reasons=tuple(reasons),
    )


def summarize_observations(
    cases: Sequence[SuperDanCapabilityCase],
    observations: Sequence[SuperDanCapabilityObservation],
) -> dict[str, Any]:
    case_map = {case.case_id: case for case in cases}
    rows: list[dict[str, Any]] = []
    for observation in observations:
        case = case_map.get(observation.case_id)
        if case is None:
            continue
        score = score_observed_run(case, observation)
        rows.append(
            {
                "case_id": case.case_id,
                "family": case.family,
                "task_length": case.task_length,
                "passed": score.passed,
                "overall_score": score.overall_score,
                "delivered_performance": score.delivered_performance,
                "total_tokens": observation.total_tokens,
                "wall_time_seconds": observation.wall_time_seconds,
                "reasons": list(score.reasons),
            }
        )

    total = len(rows)
    passed = sum(1 for row in rows if row["passed"])
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": passed / total if total else 0.0,
        "total_tokens": sum(int(row["total_tokens"] or 0) for row in rows),
        "total_wall_time_seconds": round(
            sum(float(row["wall_time_seconds"] or 0.0) for row in rows),
            3,
        ),
        "by_task_length": _group_rows(rows, "task_length"),
        "by_family": _group_rows(rows, "family"),
        "top_token_consumers": sorted(
            rows,
            key=lambda row: int(row["total_tokens"] or 0),
            reverse=True,
        )[:5],
        "top_slowest": sorted(
            rows,
            key=lambda row: float(row["wall_time_seconds"] or 0.0),
            reverse=True,
        )[:5],
    }


def load_event_log_observation(
    path: Path,
    *,
    case_id: str | None = None,
    workspace: Path | None = None,
) -> SuperDanCapabilityObservation:
    events = _read_jsonl(path)
    token_usage: dict[str, int] = {}
    aggregate_usage_candidates: list[dict[str, int]] = []
    model_usage_seen: set[str] = set()
    artifacts: list[str] = []
    artifact_seen: set[str] = set()
    status = ""
    validation_passed = False
    answer_present = False
    tests_passed = 0
    tests_failed = 0
    latest_test_exit_code: int | None = None
    tool_call_count = 0
    blockers: list[str] = []
    quality_values: list[float] = []
    first_time: float | None = None
    last_time: float | None = None
    max_elapsed = 0.0
    run_terminal_event_seen = False

    def add_artifact(value: Any) -> None:
        text = str(value or "").strip()
        if not text or text in artifact_seen:
            return
        artifact_seen.add(text)
        artifacts.append(text)

    def collect_usage(payload: Mapping[str, Any]) -> None:
        nonlocal token_usage
        usage = _usage_from_mapping(payload)
        if not usage:
            return
        if _is_model_usage_payload(payload):
            usage_key = _usage_identity(payload, usage)
            if usage_key in model_usage_seen:
                return
            model_usage_seen.add(usage_key)
            token_usage = _merge_usage(token_usage, usage)
        else:
            aggregate_usage_candidates.append(usage)

    for event in events:
        event_payload = (
            event.get("payload") if isinstance(event.get("payload"), Mapping) else None
        )
        trace_row = (
            event.get("trace_row") if isinstance(event.get("trace_row"), dict) else None
        )
        collect_usage(event)
        if event_payload:
            collect_usage(event_payload)
        if trace_row:
            collect_usage(trace_row)

        event_name = str(
            event.get("event") or event.get("name") or event.get("type") or ""
        ).strip()
        source_event_name = str(event.get("source_event_type") or "").strip()
        row_status = str(event.get("status") or "").strip().lower()
        if event_name == "run.log.completed" or (
            event_name == "completed"
            and source_event_name != "live.execution_attempt.updated"
        ):
            status = row_status or "completed"
            run_terminal_event_seen = True
        elif event_name == "run.log.failed" or event_name in {
            "failed",
            "blocked",
            "stopped",
        }:
            status = "failed"
            run_terminal_event_seen = True

        if event.get("validation_passed") is not None:
            validation_passed = validation_passed or bool(
                event.get("validation_passed")
            )
        if event_payload and event_payload.get("validation_passed") is not None:
            validation_passed = validation_passed or bool(
                event_payload.get("validation_passed")
            )
        validation = (
            event.get("validation") if isinstance(event.get("validation"), dict) else {}
        )
        if (
            not validation
            and event_payload
            and isinstance(event_payload.get("validation"), Mapping)
        ):
            validation = event_payload["validation"]
        if validation.get("passed") is not None:
            validation_passed = validation_passed or bool(validation.get("passed"))
        if event.get("passed") is True and "validation" in event_name:
            validation_passed = True

        if "shell_check.completed" in event_name:
            exit_code = _safe_int(event.get("exit_code"), default=0)
            latest_test_exit_code = exit_code
            if exit_code == 0:
                tests_passed += 1
            else:
                tests_failed += 1
        is_completed_tool = (
            event_name.endswith("tool.completed")
            or event_name == "tool.completed"
            or source_event_name.endswith("tool.completed")
            or source_event_name == "tool.completed"
        )
        if is_completed_tool:
            tool_id = str(event.get("tool_id") or "").strip()
            result = (
                event.get("result") if isinstance(event.get("result"), Mapping) else {}
            )
            arguments = (
                event.get("arguments")
                if isinstance(event.get("arguments"), Mapping)
                else {}
            )
            if event_payload:
                tool_id = tool_id or str(event_payload.get("tool_id") or "").strip()
                if not result and isinstance(event_payload.get("result"), Mapping):
                    result = event_payload["result"]
                if not arguments and isinstance(
                    event_payload.get("arguments"), Mapping
                ):
                    arguments = event_payload["arguments"]
            command = str(arguments.get("command") or "").lower()
            if tool_id == "shell_command" and _looks_like_test_command(command):
                exit_code = _safe_int(result.get("exit_code"), default=1)
                latest_test_exit_code = exit_code
                if exit_code == 0:
                    tests_passed += 1
                else:
                    tests_failed += 1
        tests_passed += max(0, _safe_int(event.get("tests_passed"), default=0))
        tests_failed += max(0, _safe_int(event.get("tests_failed"), default=0))

        if is_completed_tool:
            tool_call_count += 1
        if isinstance(event.get("tool_calls"), int):
            tool_call_count = max(tool_call_count, int(event["tool_calls"]))

        _collect_artifacts_from_event(
            event, add_artifact=add_artifact, event_name=event_name
        )
        if event_payload:
            payload_event_name = str(
                event_payload.get("event")
                or event_payload.get("name")
                or event_payload.get("type")
                or source_event_name
            ).strip()
            _collect_artifacts_from_event(
                event_payload,
                add_artifact=add_artifact,
                event_name=payload_event_name,
            )
        if trace_row:
            trace_event_name = str(
                trace_row.get("event") or trace_row.get("name") or ""
            ).strip()
            _collect_artifacts_from_event(
                trace_row,
                add_artifact=add_artifact,
                event_name=trace_event_name,
            )

        for key in ("overall_score", "quality_score", "delivery_score"):
            value = _safe_float(event.get(key))
            if value is not None:
                quality_values.append(value)
            if event_payload:
                payload_value = _safe_float(event_payload.get(key))
                if payload_value is not None:
                    quality_values.append(payload_value)
        if validation:
            value = _safe_float(validation.get("overall_score"))
            if value is not None:
                quality_values.append(value)

        if any(event.get(key) for key in ("response_text", "final_answer", "summary")):
            answer_present = True
        if event_name in {"assistant.final", "narrator.final", "run.log.completed"}:
            answer_present = True

        for key in ("blocker", "error", "failure_reason"):
            value = str(event.get(key) or "").strip()
            if value:
                blockers.append(value)

        parsed_time = _parse_time(
            event.get("timestamp")
            or event.get("time")
            or event.get("ts")
            or (event_payload or {}).get("timestamp")
            or (event_payload or {}).get("time")
            or (event_payload or {}).get("ts")
        )
        if parsed_time is not None:
            first_time = (
                parsed_time if first_time is None else min(first_time, parsed_time)
            )
            last_time = (
                parsed_time if last_time is None else max(last_time, parsed_time)
            )
        elapsed = _safe_float(
            event.get("elapsed_seconds") or (event_payload or {}).get("elapsed_seconds")
        )
        if elapsed is not None:
            max_elapsed = max(max_elapsed, elapsed)

    wall_time = max_elapsed
    if first_time is not None and last_time is not None:
        wall_time = max(wall_time, last_time - first_time)
    if not token_usage and aggregate_usage_candidates:
        token_usage = max(
            aggregate_usage_candidates,
            key=lambda usage: int(usage.get("total_tokens", 0) or 0),
        )
    if events and not run_terminal_event_seen:
        status = "incomplete"

    resolved_case_id = case_id or path.parent.name
    domain_acceptance_passed, domain_acceptance_detail = (
        _human_assist_domain_acceptance(
            resolved_case_id,
            workspace=workspace,
        )
    )
    return SuperDanCapabilityObservation(
        case_id=resolved_case_id,
        status=status or "unknown",
        wall_time_seconds=round(wall_time, 3),
        token_usage=token_usage,
        validation_passed=validation_passed,
        artifacts_changed=tuple(artifacts),
        tests_passed=tests_passed,
        tests_failed=tests_failed,
        latest_test_exit_code=latest_test_exit_code,
        event_count=len(events),
        tool_call_count=tool_call_count,
        output_quality_score=max(quality_values) if quality_values else None,
        answer_present=answer_present,
        blockers=tuple(blockers),
        source_log=str(path),
        domain_acceptance_passed=domain_acceptance_passed,
        domain_acceptance_detail=domain_acceptance_detail,
    )


def _validation_score(
    case: SuperDanCapabilityCase,
    observation: SuperDanCapabilityObservation,
) -> float:
    if (
        _requires_human_assist_domain_acceptance(case)
        and observation.domain_acceptance_passed is not True
    ):
        return 0.0
    if observation.latest_test_exit_code == 0:
        return 1.0
    if observation.latest_test_exit_code is not None:
        return 0.0
    if observation.validation_passed and observation.tests_failed == 0:
        return 1.0
    if observation.tests_failed > 0:
        return 0.0
    if observation.tests_passed > 0:
        return 0.8
    if not case.requires_validation and _is_completed_status(observation.status):
        return 0.7
    return 0.0


def _artifact_score(
    case: SuperDanCapabilityCase,
    observation: SuperDanCapabilityObservation,
) -> float:
    if not case.required_outputs and case.min_changed_artifacts <= 0:
        return (
            1.0
            if observation.answer_present or _is_completed_status(observation.status)
            else 0.0
        )
    count_score = (
        _clamp01(
            len(observation.artifacts_changed) / max(case.min_changed_artifacts, 1)
        )
        if case.min_changed_artifacts > 0
        else 1.0
    )
    if not case.required_outputs:
        return count_score
    matched = _matched_required_outputs(case, observation)
    required_score = len(matched) / len(case.required_outputs)
    return _clamp01(min(count_score, required_score))


def _quality_score(observation: SuperDanCapabilityObservation) -> float:
    if observation.output_quality_score is not None:
        return _clamp01(observation.output_quality_score)
    if observation.validation_passed:
        return 0.85
    if _is_completed_status(observation.status):
        return 0.60
    return 0.0


def _evidence_score(
    case: SuperDanCapabilityCase,
    observation: SuperDanCapabilityObservation,
) -> float:
    event_score = 0.30 if observation.event_count > 0 else 0.0
    token_score = 0.25 if observation.total_tokens > 0 else 0.0
    tool_or_answer_score = (
        0.20 if observation.tool_call_count > 0 or observation.answer_present else 0.0
    )
    validation_score = 0.25 if _validation_score(case, observation) >= 0.8 else 0.0
    return _clamp01(event_score + token_score + tool_or_answer_score + validation_score)


def _score_reasons(
    *,
    case: SuperDanCapabilityCase,
    observation: SuperDanCapabilityObservation,
    delivered_performance: float,
    overall_score: float,
    token_efficiency: float,
) -> list[str]:
    reasons: list[str] = []
    if not _is_completed_status(observation.status):
        reasons.append(f"status:{observation.status or 'unknown'}")
    if delivered_performance < case.min_delivery_score:
        reasons.append(
            f"delivery_score:{delivered_performance:.2f}<required:{case.min_delivery_score:.2f}"
        )
    if overall_score < case.min_overall_score:
        reasons.append(
            f"overall_score:{overall_score:.2f}<required:{case.min_overall_score:.2f}"
        )
    if token_efficiency <= 0:
        reasons.append("missing_token_usage")
    if case.requires_validation and _validation_score(case, observation) < 0.8:
        reasons.append("missing_fresh_validation")
    if (
        _requires_human_assist_domain_acceptance(case)
        and observation.domain_acceptance_passed is not True
    ):
        reasons.append("missing_domain_acceptance")
    missing_outputs = [
        output
        for output in case.required_outputs
        if output not in _matched_required_outputs(case, observation)
    ]
    if missing_outputs:
        reasons.append("missing_required_outputs:" + ",".join(missing_outputs))
    if len(observation.artifacts_changed) < case.min_changed_artifacts:
        reasons.append(
            f"changed_artifacts:{len(observation.artifacts_changed)}<required:{case.min_changed_artifacts}"
        )
    return reasons


def _requires_human_assist_domain_acceptance(
    case: SuperDanCapabilityCase,
) -> bool:
    return case.case_id in {
        "long-academic-first-draft",
        "long-market-strategy-framework",
    }


def _matched_required_outputs(
    case: SuperDanCapabilityCase,
    observation: SuperDanCapabilityObservation,
) -> set[str]:
    normalized_artifacts = [
        str(path).strip().replace("\\", "/").rstrip("/")
        for path in observation.artifacts_changed
        if str(path).strip()
    ]
    matched: set[str] = set()
    for raw_required in case.required_outputs:
        required = str(raw_required).strip().replace("\\", "/").strip("/")
        if not required:
            continue
        required_is_directory = "." not in Path(required).name
        for artifact in normalized_artifacts:
            normalized = artifact.lstrip("./")
            exact_or_suffix = normalized == required or normalized.endswith(
                "/" + required
            )
            directory_member = required_is_directory and (
                normalized.startswith(required + "/")
                or f"/{required}/" in f"/{normalized}/"
            )
            if exact_or_suffix or directory_member:
                matched.add(raw_required)
                break
    return matched


def _human_assist_domain_acceptance(
    case_id: str,
    *,
    workspace: Path | None,
) -> tuple[bool | None, str | None]:
    if case_id not in {
        "long-academic-first-draft",
        "long-market-strategy-framework",
    }:
        return None, None
    if workspace is None:
        return None, "workspace not supplied; domain acceptance was not run"
    try:
        from tests.eval.super_diane_human_assist_acceptance import (
            get_human_assist_case,
            validate_human_assist_workspace,
        )

        report = validate_human_assist_workspace(
            get_human_assist_case(case_id),
            workspace,
        )
    except (KeyError, OSError, ValueError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    failed = [
        check.name
        for section in (report.artifact, report.domain, report.human_handoff)
        for check in section.checks
        if not check.passed
    ]
    return report.passed, (
        "passed" if report.passed else "failed checks: " + ", ".join(failed[:10])
    )


def _bounded_efficiency(*, budget: float, actual: float) -> float:
    if budget <= 0:
        return 1.0
    if actual <= 0 or not math.isfinite(actual):
        return 0.0
    if actual <= budget:
        return 1.0
    return _clamp01(budget / actual)


def _is_completed_status(status: str) -> bool:
    return str(status or "").strip().lower() in {
        "completed",
        "complete",
        "passed",
        "success",
        "done",
    }


def _looks_like_test_command(command: str) -> bool:
    text = command.strip().lower()
    if not text:
        return False
    return any(
        marker in text
        for marker in ("pytest", "unittest", "npm test", "cargo test", "go test")
    )


def _group_rows(
    rows: Sequence[Mapping[str, Any]], key: str
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        group_key = str(row.get(key) or "unknown")
        bucket = grouped.setdefault(
            group_key,
            {"total": 0, "passed": 0, "failed": 0, "avg_overall_score": 0.0},
        )
        bucket["total"] += 1
        bucket["passed"] += 1 if row.get("passed") else 0
        bucket["failed"] += 0 if row.get("passed") else 1
        bucket["avg_overall_score"] += float(row.get("overall_score") or 0.0)
    for bucket in grouped.values():
        total = int(bucket["total"] or 0)
        bucket["pass_rate"] = bucket["passed"] / total if total else 0.0
        bucket["avg_overall_score"] = (
            round(bucket["avg_overall_score"] / total, 4) if total else 0.0
        )
    return grouped


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _usage_from_mapping(payload: Mapping[str, Any]) -> dict[str, int]:
    for key in ("token_usage_delta", "token_usage", "usage"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            return _normalize_usage(value)
    provider_result = payload.get("provider_result")
    if isinstance(provider_result, Mapping) and isinstance(
        provider_result.get("usage"), Mapping
    ):
        return _normalize_usage(provider_result["usage"])
    raw_response = payload.get("raw_response")
    if isinstance(raw_response, Mapping):
        for key in ("token_usage", "usage"):
            value = raw_response.get(key)
            if isinstance(value, Mapping):
                return _normalize_usage(value)
    return {}


def _is_model_usage_payload(payload: Mapping[str, Any]) -> bool:
    event_name = str(
        payload.get("event") or payload.get("name") or payload.get("type") or ""
    ).strip()
    source_event_name = str(payload.get("source_event_type") or "").strip()
    span_kind = str(payload.get("span_kind") or "").strip()
    return (
        event_name in {"model.responded", "token_usage_recorded"}
        or source_event_name == "model.responded"
        or span_kind == "model_call"
    )


def _usage_identity(payload: Mapping[str, Any], usage: Mapping[str, int]) -> str:
    token_round = (
        payload.get("token_usage_round")
        if isinstance(payload.get("token_usage_round"), Mapping)
        else {}
    )
    explicit_id = (
        payload.get("model_call_id")
        or payload.get("span_id")
        or token_round.get("model_call_id")
        or token_round.get("span_id")
    )
    if explicit_id:
        return (
            "|".join(
                str(payload.get(key) or token_round.get(key) or "")
                for key in (
                    "contract_id",
                    "parallel_lane",
                    "worker_id",
                    "sequence",
                    "timestamp",
                    "round",
                )
            )
            + f"|{explicit_id}"
        )
    return (
        "|".join(
            str(payload.get(key) or "")
            for key in ("timestamp", "event", "span_kind", "worker_id")
        )
        + f"|{usage.get('prompt_tokens', 0)}:{usage.get('completion_tokens', 0)}"
    )


def _collect_artifacts_from_event(
    event: Mapping[str, Any],
    *,
    add_artifact: Any,
    event_name: str,
) -> None:
    _add_artifact_refs(event.get("artifact_refs"), add_artifact=add_artifact)
    for key in (
        "mutated_paths",
        "changed_paths",
        "changed_required_files",
        "target_files",
        "files_created",
        "files_modified",
        "modified_files",
        "files",
    ):
        _add_artifact_values(event.get(key), add_artifact=add_artifact)

    result = event.get("result") if isinstance(event.get("result"), Mapping) else {}
    changes = (
        result.get("workspace_changes")
        if isinstance(result.get("workspace_changes"), Mapping)
        else {}
    )
    for key in (
        "changed_paths",
        "created_paths",
        "modified_paths",
        "files_created",
        "files_modified",
    ):
        _add_artifact_values(changes.get(key), add_artifact=add_artifact)

    tool_id = str(event.get("tool_id") or "").strip()
    is_completed_tool = (
        event_name.endswith("tool.completed") or event_name == "tool.completed"
    )
    is_mutation_tool = tool_id in {"file_write", "file_edit"}
    if is_completed_tool and is_mutation_tool:
        if isinstance(result, Mapping):
            _add_artifact_values(result.get("path"), add_artifact=add_artifact)
        arguments = (
            event.get("arguments")
            if isinstance(event.get("arguments"), Mapping)
            else {}
        )
        _add_artifact_values(arguments.get("path"), add_artifact=add_artifact)

    capsules = event.get("capsules")
    if isinstance(capsules, Sequence) and not isinstance(capsules, (str, bytes)):
        for capsule in capsules:
            if isinstance(capsule, Mapping):
                _collect_artifacts_from_capsule(capsule, add_artifact=add_artifact)

    parsed_text = _json_object_from_text(event.get("text"))
    if isinstance(parsed_text, Mapping):
        for key in (
            "mutated_paths",
            "changed_paths",
            "changed_required_files",
            "files_created",
            "files_modified",
            "files",
        ):
            _add_artifact_values(parsed_text.get(key), add_artifact=add_artifact)


def _collect_artifacts_from_capsule(
    capsule: Mapping[str, Any], *, add_artifact: Any
) -> None:
    capsule_kind = str(capsule.get("kind") or "").strip()
    if capsule_kind != "implementation_delta":
        return
    metadata = (
        capsule.get("metadata") if isinstance(capsule.get("metadata"), Mapping) else {}
    )
    _add_artifact_values(metadata.get("path"), add_artifact=add_artifact)
    for key in ("raw_refs", "retained_evidence"):
        refs = capsule.get(key)
        if isinstance(refs, Sequence) and not isinstance(refs, (str, bytes)):
            for ref in refs:
                if isinstance(ref, Mapping):
                    _add_artifact_values(ref.get("path"), add_artifact=add_artifact)


def _add_artifact_values(value: Any, *, add_artifact: Any) -> None:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            add_artifact(item)
    elif value is not None:
        add_artifact(value)


def _add_artifact_refs(value: Any, *, add_artifact: Any) -> None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return
    for item in value:
        if isinstance(item, Mapping):
            add_artifact(item.get("path") or item.get("uri") or item.get("url"))
        else:
            add_artifact(item)


def _json_object_from_text(value: Any) -> Mapping[str, Any] | None:
    text = str(value or "").strip()
    if not text or not text.startswith("{"):
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, Mapping) else None


def _normalize_usage(raw: Mapping[str, Any]) -> dict[str, int]:
    prompt = _safe_int(raw.get("prompt_tokens", raw.get("prompt")), default=0)
    completion = _safe_int(
        raw.get("completion_tokens", raw.get("completion")), default=0
    )
    total = _safe_int(raw.get("total_tokens"), default=prompt + completion)
    if total <= 0:
        total = prompt + completion
    normalized = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }
    for key in ("cached_input_tokens", "cache_write_tokens"):
        value = _safe_int(raw.get(key), default=0)
        if value:
            normalized[key] = value
    return normalized


def _merge_usage(left: Mapping[str, int], right: Mapping[str, int]) -> dict[str, int]:
    merged = dict(left)
    for key, value in right.items():
        merged[key] = int(merged.get(key, 0) or 0) + int(value or 0)
    return merged


def _parse_time(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _safe_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _clamp01(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return max(0.0, min(1.0, value))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("event_logs", nargs="+", type=Path)
    parser.add_argument("--case-id", default=None)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help=(
            "Artifact workspace used to run the academic/market domain gate. "
            "Required for those human-assist cases to pass."
        ),
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(list(argv) if argv is not None else None)

    cases = _benchmark_cases()
    observations = [
        load_event_log_observation(
            path,
            case_id=args.case_id,
            workspace=args.workspace,
        )
        for path in args.event_logs
    ]
    summary = summarize_observations(cases, observations)
    payload = {
        "summary": summary,
        "observations": [asdict(observation) for observation in observations],
    }
    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
