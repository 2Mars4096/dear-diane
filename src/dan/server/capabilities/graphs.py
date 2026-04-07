"""Graph delete and mutation-preview capability handlers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from dan.agent_runtime.graph_summary import compute_graph_revision
from dan.meta.workflow_contract import _describe_llm_external_action_issue
from dan.server.capability_registry import CapabilityContext, CapabilityResult
from dan.server.chat.mutation_previews import (
    get_latest_mutation_preview,
    mark_mutation_preview_applied,
)
from dan.server.graph_mutator import GraphMutator, MutationPlan
from dan.server.mutation_metrics import mutation_metrics
from dan.server.workflow_guards import (
    WorkflowContractError,
    collect_workflow_contract_messages,
    ensure_workflow_apply_ready,
)
from dan.server.workflow_identity import (
    resolve_workflow_reference,
    workflow_resolution_context_from_session,
)


def _resolve_graph_delete_target(
    graph_store: Any,
    requested_graph_id: str,
) -> tuple[str | None, str | None]:
    requested = str(requested_graph_id or "").strip()
    if not requested:
        return None, None
    if hasattr(graph_store, "list_graphs") and not hasattr(graph_store, "get_graph"):
        try:
            graphs = graph_store.list_graphs()
        except Exception:
            return requested, None
        requested_lower = requested.lower()
        exact_id = next(
            (
                str(graph.get("graph_id") or "").strip()
                for graph in graphs
                if str(graph.get("graph_id") or "").strip() == requested
            ),
            None,
        )
        if exact_id:
            return exact_id, None
        name_matches = [
            str(graph.get("graph_id") or "").strip()
            for graph in graphs
            if str(graph.get("name") or "").strip().lower() == requested_lower
            and str(graph.get("graph_id") or "").strip()
        ]
        if len(name_matches) == 1:
            resolved = name_matches[0]
            return resolved, f"Matched workflow name `{requested}` to graph ID `{resolved}`."
        if len(name_matches) > 1:
            sample = ", ".join(f"`{match}`" for match in name_matches[:5])
            return None, (
                f"Workflow name `{requested}` matches multiple graph IDs: {sample}. "
                "Use an exact `graph_id`."
            )
        return requested, None
    if not hasattr(graph_store, "list_graphs") or not hasattr(graph_store, "get_graph"):
        return requested, None
    resolution = resolve_workflow_reference(
        graph_store,
        requested,
        context=workflow_resolution_context_from_session(
            current_workflow_id=None,
            allow_scratch=False,
        ),
    )
    if not resolution.resolved:
        return None, resolution.resolution_message
    return resolution.graph_id, resolution.resolution_message


async def handle_delete_graph(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    if ctx.graph_store is None:
        return CapabilityResult(success=False, message="Graph store not available.")
    requested_graph_id = str(args.get("graph_id") or ctx.workflow_id or "").strip()
    if not requested_graph_id:
        return CapabilityResult(
            success=False,
            message="No workflow ID provided to delete.",
            error_type="invalid_input",
        )
    if requested_graph_id == "_scratch":
        return CapabilityResult(
            success=False,
            message="Cannot delete the ephemeral `_scratch` workflow. Save it first if you want to remove a named workflow.",
            error_type="invalid_input",
        )
    graph_id, resolution_message = _resolve_graph_delete_target(
        ctx.graph_store,
        requested_graph_id,
    )
    if graph_id is None:
        return CapabilityResult(
            success=False,
            message=resolution_message or f"Workflow `{requested_graph_id}` could not be resolved.",
            error_type="invalid_input",
        )
    try:
        deleted = bool(ctx.graph_store.delete_graph(graph_id))
    except ValueError as exc:
        return CapabilityResult(
            success=False,
            message=f"Invalid workflow ID: {exc}",
            error_type="invalid_input",
        )
    if not deleted:
        text = (
            f"Workflow `{requested_graph_id}` is not present in the current catalog, so there was nothing to delete."
        )
        if requested_graph_id != graph_id:
            text = (
                f"Workflow `{requested_graph_id}` resolved to `{graph_id}`, but it is already absent from the current catalog."
            )
        return CapabilityResult(
            success=True,
            message=text,
            data={
                "graph_id": graph_id,
                "requested_graph_id": requested_graph_id,
                "deleted": False,
                "already_absent": True,
            },
            output_preview=text,
        )
    text = f"Deleted workflow `{graph_id}`."
    if resolution_message:
        text = f"{resolution_message} {text}"
    return CapabilityResult(
        success=True,
        message=text,
        data={
            "graph_id": graph_id,
            "requested_graph_id": requested_graph_id,
            "deleted": True,
        },
        output_preview=text,
    )


def _chat_store_for_mutations(ctx: CapabilityContext) -> Any:
    manager = getattr(ctx, "chat_manager", None)
    return getattr(manager, "_chat_store", None) if manager is not None else None


def _apply_mutation_plan_via_graph_store(
    graph_store: Any,
    workflow_id: str,
    mutation_plan: dict[str, Any],
) -> CapabilityResult:
    if graph_store is None:
        return CapabilityResult(
            success=False,
            message="Graph store not available.",
            error_type="unavailable",
        )

    graph_data = graph_store.get_graph(workflow_id)
    if graph_data is None:
        return CapabilityResult(
            success=False,
            message=f"Workflow `{workflow_id}` not found.",
            error_type="not_found",
        )

    try:
        plan = MutationPlan.model_validate(mutation_plan)
    except Exception as exc:
        return CapabilityResult(
            success=False,
            message=f"Invalid mutation plan: {exc}",
            error_type="invalid_input",
        )

    revision = compute_graph_revision(graph_data)
    result = GraphMutator().apply(graph_data, plan, current_revision=revision)
    if not result.success:
        mutation_metrics.record_apply(False)
        first_error = result.errors[0].message if result.errors else "Mutation apply failed."
        if result.stale_plan:
            first_error = (
                "The workflow changed since this preview was created. "
                "Please rebuild or refresh the preview before applying it."
            )
        return CapabilityResult(
            success=False,
            message=first_error,
            data={
                "stale_plan": result.stale_plan,
                "errors": [error.model_dump() for error in result.errors],
            },
            output_preview=first_error,
            error_type="stale_plan" if result.stale_plan else "apply_failed",
        )

    try:
        guarded = ensure_workflow_apply_ready(result.new_graph, workflow_id=workflow_id)
    except WorkflowContractError as exc:
        mutation_metrics.record_apply(False)
        mutation_metrics.record_validation(False)
        errors = collect_workflow_contract_messages(
            exc.report,
            default_message=str(exc),
        )
        return CapabilityResult(
            success=False,
            message=str(exc),
            data={
                "errors": [{"message": msg} for msg in errors],
                "warnings": list(getattr(exc.report, "warnings", []) or []),
                "run_readiness_failure_mode": exc.failure_mode,
            },
            output_preview=str(exc),
            error_type="validation_failed",
        )

    # Keep chat-side preview apply aligned with the pre-workerized contract:
    # legacy llm_operator mutations may be compacted into worker nodes before
    # validation runs, but apply_last_mutation should still reject prompts that
    # imply external actions without tools.
    for node in list(result.new_graph.get("nodes", []) or []):
        if str(node.get("node_type") or "").strip() != "worker":
            continue
        if list(node.get("tool_ids") or []):
            continue
        llm_hints = node.get("llm_hints") or {}
        issue = _describe_llm_external_action_issue(
            SimpleNamespace(
                node_type="llm_operator",
                id=node.get("id", ""),
                name=node.get("name", ""),
                description=node.get("description", ""),
                prompt_template=llm_hints.get("prompt_template", ""),
                system_prompt=llm_hints.get("system_prompt", ""),
                tools=[],
            ),
            graph_label=f"Workflow `{workflow_id}`",
        )
        if issue:
            mutation_metrics.record_apply(False)
            mutation_metrics.record_validation(False)
            return CapabilityResult(
                success=False,
                message=(
                    f"Workflow `{workflow_id}` cannot be applied yet. "
                    f"Validated, but not run-ready. Next issue: {issue}"
                ),
                data={
                    "errors": [{"message": issue}],
                    "warnings": list(getattr(guarded.report, "warnings", []) or []),
                    "run_readiness_failure_mode": "not_run_ready",
                },
                output_preview=issue,
                error_type="validation_failed",
            )

    mutation_metrics.record_validation(True)
    mutation_metrics.record_apply(True)
    saved_graph = graph_store.save_graph(workflow_id, guarded.graph_dict)
    new_revision = compute_graph_revision(saved_graph)
    text = "Applied the workflow preview successfully."
    warnings = list(getattr(guarded.report, "warnings", []) or [])
    if warnings:
        text += f" Validation warnings: {warnings[0]}"
    return CapabilityResult(
        success=True,
        message=text,
        data={
            "workflow_id": workflow_id,
            "graph_revision": new_revision,
            "warnings": warnings,
            "diagnostics": result.diagnostics,
        },
        output_preview=text,
    )


async def handle_apply_last_mutation(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    workflow_id = str(args.get("workflow_id") or ctx.workflow_id or "").strip()
    if not workflow_id:
        return CapabilityResult(
            success=False,
            message="No workflow ID is available for applying the preview.",
            error_type="invalid_input",
        )

    preview, error = get_latest_mutation_preview(
        _chat_store_for_mutations(ctx),
        workflow_id,
        ctx.thread_id,
        message_id=args.get("message_id"),
    )
    if preview is None:
        return CapabilityResult(
            success=False,
            message=error or "No proposed workflow preview is available in this chat.",
            error_type="not_found",
        )

    result = _apply_mutation_plan_via_graph_store(
        ctx.graph_store,
        workflow_id,
        preview["mutation_plan"],
    )
    if result.success:
        mark_mutation_preview_applied(
            _chat_store_for_mutations(ctx),
            workflow_id,
            ctx.thread_id,
            message_id=preview.get("message_id"),
        )
        if isinstance(result.data, dict):
            result.data["message_id"] = preview.get("message_id")
    return result
