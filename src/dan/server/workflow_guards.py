"""Shared workflow contract guards for save/apply/run/schedule entrypoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from dan.meta.workflow_contract import (
    WorkflowBuildContractReport,
    classify_run_readiness_issues,
    validate_workflow_build_contract,
    workflow_build_summary,
)
from dan.models.graph import Graph

GuardAction = Literal["apply", "run", "schedule", "schedule_execution"]


@dataclass(frozen=True)
class WorkflowGuardResult:
    graph: Graph
    graph_dict: dict[str, Any]
    report: WorkflowBuildContractReport
    failure_mode: str | None = None


class WorkflowContractError(ValueError):
    """Raised when a workflow fails the stronger build-contract gate."""

    def __init__(
        self,
        message: str,
        *,
        workflow_id: str,
        report: WorkflowBuildContractReport,
        action: GuardAction,
        failure_mode: str | None,
    ) -> None:
        super().__init__(message)
        self.workflow_id = workflow_id
        self.report = report
        self.action = action
        self.failure_mode = failure_mode


def _workflow_prefix(workflow_id: str, *, action: GuardAction) -> str:
    label = f"Workflow `{workflow_id}`" if workflow_id else "Workflow"
    if action == "apply":
        return f"{label} cannot be applied yet."
    if action == "schedule":
        return f"{label} cannot be scheduled yet."
    if action == "schedule_execution":
        return f"{label} cannot be executed from the current schedule."
    return f"{label} is not run-ready."


def collect_workflow_contract_messages(
    report: WorkflowBuildContractReport,
    *,
    limit: int = 5,
    default_message: str,
) -> list[str]:
    """Collect the highest-signal contract failures for user-facing surfaces."""

    messages: list[str] = []
    for issue in list(getattr(report, "errors", []) or [])[:limit]:
        message = str(getattr(issue, "message", "") or "").strip()
        if message and message not in messages:
            messages.append(message)
    for issue in list(getattr(report, "run_readiness_issues", []) or [])[:limit]:
        message = str(issue or "").strip()
        if message and message not in messages:
            messages.append(message)
    if messages:
        return messages
    return [default_message]


def _contract_error_message(
    workflow_id: str,
    report: WorkflowBuildContractReport,
    *,
    action: GuardAction,
) -> str:
    prefix = _workflow_prefix(workflow_id, action=action)
    summary = workflow_build_summary(report)
    if summary:
        return f"{prefix} {summary}"
    return prefix


def ensure_workflow_contract(
    graph_or_dict: Graph | dict[str, Any],
    *,
    workflow_id: str,
    action: GuardAction,
    apply_repairs: bool,
) -> WorkflowGuardResult:
    """Validate a workflow candidate against the stronger build contract."""

    graph_dict = (
        graph_or_dict.model_dump(mode="json")
        if isinstance(graph_or_dict, Graph)
        else graph_or_dict
    )
    report = validate_workflow_build_contract(
        graph_dict,
        workflow_id=workflow_id,
        apply_repairs=apply_repairs,
    )
    failure_mode = classify_run_readiness_issues(
        list(getattr(report, "run_readiness_issues", []) or [])
    )
    if (
        not getattr(report, "validated", False)
        or not getattr(report, "run_ready", False)
        or not isinstance(getattr(report, "graph_dict", None), dict)
    ):
        raise WorkflowContractError(
            _contract_error_message(workflow_id, report, action=action),
            workflow_id=workflow_id,
            report=report,
            action=action,
            failure_mode=failure_mode,
        )

    graph = getattr(report, "graph", None)
    if graph is None:
        graph = Graph.model_validate(report.graph_dict)
    return WorkflowGuardResult(
        graph=graph,
        graph_dict=report.graph_dict,
        report=report,
        failure_mode=failure_mode,
    )


def ensure_workflow_apply_ready(
    graph_dict: dict[str, Any],
    *,
    workflow_id: str,
) -> WorkflowGuardResult:
    return ensure_workflow_contract(
        graph_dict,
        workflow_id=workflow_id,
        action="apply",
        apply_repairs=True,
    )


def ensure_workflow_run_ready(
    graph_or_dict: Graph | dict[str, Any],
    *,
    workflow_id: str,
    action: Literal["run", "schedule", "schedule_execution"] = "run",
) -> WorkflowGuardResult:
    return ensure_workflow_contract(
        graph_or_dict,
        workflow_id=workflow_id,
        action=action,
        apply_repairs=False,
    )
