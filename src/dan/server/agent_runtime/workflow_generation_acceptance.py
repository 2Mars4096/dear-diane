"""Shared candidate-graph acceptance helpers for workflow generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class WorkflowGenerationAcceptanceResult:
    """Acceptance decision for one candidate workflow graph."""

    accepted_graph: dict[str, Any] | None = None
    errors: tuple[Any, ...] = ()


def collect_validation_errors(validation: Any) -> list[Any]:
    errors = list(getattr(validation, "errors", []) or [])
    if errors:
        return errors

    if getattr(validation, "run_ready", True):
        return []

    issues = [
        str(issue).strip()
        for issue in (getattr(validation, "run_readiness_issues", None) or [])
        if str(issue).strip()
    ]
    if not issues:
        contract_report = getattr(validation, "contract_report", None)
        issues = [
            str(issue).strip()
            for issue in (getattr(contract_report, "run_readiness_issues", None) or [])
            if str(issue).strip()
        ]
    if not issues:
        return []

    from dan.meta.diagnosis import GenerationError, GenerationErrorType, GenerationStage

    return [
        GenerationError(
            stage=GenerationStage.validation,
            error_type=GenerationErrorType.build_error,
            message=issue,
            recoverable=True,
        )
        for issue in issues
    ]


def accept_candidate_graph(
    graph_dict: dict[str, Any],
    *,
    validate_graph: Callable[[dict[str, Any]], Any],
    build_validation_event: Callable[[Any], Any],
    emit_event: Callable[[Any], None],
    quality_error_for_graph: Callable[[dict[str, Any]], Any | None],
    fit_check: Callable[[dict[str, Any]], None],
    record_gen_outcome: Callable[..., None],
    success_method: str,
    failure_method: str,
    failure_fix_needed: bool,
    pattern: str,
    defer_success_recording: bool = False,
) -> WorkflowGenerationAcceptanceResult:
    """Validate, quality-check, and record one candidate workflow graph."""

    validation = validate_graph(graph_dict)
    emit_event(build_validation_event(validation))

    errors: list[Any]
    if (
        validation.success
        and validation.run_ready
        and validation.graph is not None
    ):
        quality_error = quality_error_for_graph(graph_dict)
        if quality_error is None:
            fit_check(graph_dict)
            if not defer_success_recording:
                record_gen_outcome(
                    success_method,
                    success=True,
                    pattern=pattern,
                )
            return WorkflowGenerationAcceptanceResult(
                accepted_graph=validation.graph.model_dump(mode="json"),
            )
        errors = [quality_error]
    else:
        errors = collect_validation_errors(validation)

    error_type = (
        getattr(getattr(errors[0], "error_type", None), "value", None)
        if errors
        else None
    ) or "validation"
    record_gen_outcome(
        failure_method,
        success=False,
        error_type=error_type,
        fix_needed=failure_fix_needed,
        pattern=pattern,
    )
    return WorkflowGenerationAcceptanceResult(errors=tuple(errors))


__all__ = [
    "collect_validation_errors",
    "WorkflowGenerationAcceptanceResult",
    "accept_candidate_graph",
]
