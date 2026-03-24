"""Shared candidate-graph acceptance helpers for workflow generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class WorkflowGenerationAcceptanceResult:
    """Acceptance decision for one candidate workflow graph."""

    accepted_graph: dict[str, Any] | None = None
    errors: tuple[Any, ...] = ()


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
        errors = list(validation.errors)

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
    "WorkflowGenerationAcceptanceResult",
    "accept_candidate_graph",
]
