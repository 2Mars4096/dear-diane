"""Server-side helpers for validated workflow saves and terminal summaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from dan.agent_runtime.graph_summary import build_graph_summary
from dan.models.graph import Graph
from dan.server.chat.events import (
    ChatCompleteEvent,
    ChatGenerationSummaryEvent,
    ChatGraphCreatedEvent,
)


@dataclass(frozen=True)
class PreparedWorkflowSave:
    """Validation outcome for a workflow graph before graph-store persistence."""

    status: str
    graph_to_save: dict[str, Any] | None = None
    validation_errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class PreparedWorkflowTerminalReply:
    """Server-ready reply bundle after a workflow graph has been persisted."""

    assistant_message: str
    graph_revision: str
    graph_created_event: ChatGraphCreatedEvent
    complete_event: ChatCompleteEvent


def prepare_validated_workflow_save(
    graph_dict: dict[str, Any],
    *,
    validate_graph: Callable[[dict[str, Any]], Any],
    collect_contract_errors: Callable[..., list[str]],
    blocked_default_message: str,
) -> PreparedWorkflowSave:
    """Validate a candidate workflow graph and return the persistence decision."""
    try:
        contract_report = validate_graph(graph_dict)
    except Exception as exc:
        return PreparedWorkflowSave(
            status="validation_error",
            validation_errors=(str(exc),),
        )

    if getattr(contract_report, "validated", False) and getattr(contract_report, "run_ready", False):
        return PreparedWorkflowSave(
            status="ready_to_save",
            graph_to_save=getattr(contract_report, "graph_dict", None) or graph_dict,
        )

    return PreparedWorkflowSave(
        status="blocked",
        validation_errors=tuple(
            collect_contract_errors(
                contract_report,
                default_message=blocked_default_message,
            )
        ),
    )


def build_codegen_saved_message(
    *,
    workflow_id: str,
    workflow_name: str,
    node_count: int,
    edge_count: int,
    node_preview_items: list[str],
    generation_summary_event: ChatGenerationSummaryEvent | None = None,
) -> str:
    """Render the terminal success message for the codegen fast path."""
    path_suffix = ""
    if generation_summary_event is not None:
        wall_seconds = generation_summary_event.wall_clock_ms / 1000
        path_taken = generation_summary_event.path_taken
        if len(generation_summary_event.fallback_chain) > 1 or wall_seconds > 10:
            path_suffix = f" Built via {path_taken} in {wall_seconds:.1f}s."

    node_preview = ""
    preview_items = [item for item in node_preview_items if item]
    if preview_items:
        extra_nodes = max(0, node_count - len(preview_items))
        preview_text = ", ".join(f"`{item}`" for item in preview_items)
        if extra_nodes:
            preview_text += f", +{extra_nodes} more"
        node_preview = f" Nodes: {preview_text}."

    return (
        f"Workflow saved to current id `{workflow_id}`"
        + (
            f' with name "{workflow_name}"'
            if workflow_name and workflow_name != workflow_id
            else ""
        )
        + f". Created with {node_count} nodes and {edge_count} edges."
        + node_preview
        + " Validated and run-ready."
        + path_suffix
    )


def build_structural_macro_message(dispatch: Any) -> str:
    """Render the terminal success message for a structural-macro fast path."""
    results = getattr(dispatch, "results", None) or []
    if results:
        if len(results) == 1:
            result = results[0]
            return (
                f"Applied `{dispatch.macro_names[0]}`: "
                f"{result.edges_added} edges added, "
                f"{len(result.nodes_added)} nodes added. "
                "Validated and run-ready."
            )

        parts = []
        for name, result in zip(getattr(dispatch, "macro_names", []), results):
            parts.append(
                f"`{name}` ({len(result.nodes_added)} nodes, {result.edges_added} edges)"
            )
        return (
            f"Applied {len(results)} macros: {', '.join(parts)}. "
            "Validated and run-ready."
        )

    result = getattr(dispatch, "result", None)
    return (
        f"Applied `{dispatch.macro_name}`: "
        f"{result.edges_added} edges added, "
        f"{len(result.nodes_added)} nodes added. "
        "Validated and run-ready."
    )


def build_persisted_workflow_reply(
    *,
    workflow_id: str,
    saved_graph: dict[str, Any],
    message_id: str,
    assistant_message: str,
    context_window: int,
    detected_mode: str,
) -> PreparedWorkflowTerminalReply:
    """Build the common graph-created + terminal-complete reply for a saved workflow."""
    graph = Graph.model_validate(saved_graph)
    summary = build_graph_summary(graph, workflow_id)
    return PreparedWorkflowTerminalReply(
        assistant_message=assistant_message,
        graph_revision=summary.revision,
        graph_created_event=ChatGraphCreatedEvent(
            workflow_id=workflow_id,
            node_count=summary.node_count,
            edge_count=summary.edge_count,
            graph_revision=summary.revision,
        ),
        complete_event=ChatCompleteEvent(
            message_id=message_id,
            content=assistant_message,
            token_usage={},
            context_window=context_window,
            graph_revision=summary.revision,
            revision_mismatch=False,
            detected_mode=detected_mode,
        ),
    )


__all__ = [
    "PreparedWorkflowTerminalReply",
    "PreparedWorkflowSave",
    "build_codegen_saved_message",
    "build_persisted_workflow_reply",
    "build_structural_macro_message",
    "prepare_validated_workflow_save",
]
