"""Shared workflow identity resolution and compact workflow-context helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Iterable, Sequence

from dan.agent_runtime.graph_summary import compute_graph_revision
from dan.meta.workflow_contract import normalize_workflow_id
from dan.models.graph import Graph
from dan.utils.workflow_interface import derive_workflow_interface

_CURRENT_REFS = frozenset({"", "current", "this"})
_TOKEN_LIMIT = 600


@dataclass(frozen=True)
class WorkflowReferenceContext:
    """Inputs that influence workflow resolution."""

    current_workflow_id: str | None = None
    project_workflow_ids: tuple[str, ...] = ()
    expected_revision: str | None = None
    allow_scratch: bool = True


@dataclass(frozen=True)
class WorkflowResolution:
    """Resolved workflow target plus freshness and explanation metadata."""

    requested_reference: str
    graph_id: str | None
    display_name: str | None
    normalized_name: str | None
    revision: str | None
    fingerprint: str | None
    updated_at: str | None
    resolution_source: str
    status: str
    stale: bool = False
    resolution_message: str | None = None
    matched_graph_ids: tuple[str, ...] = ()
    resolved_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )

    @property
    def resolved(self) -> bool:
        return bool(self.graph_id and self.status in {"resolved", "stale"})


def _inventory_entry(
    graph_store: Any,
    graph_id: str,
    listing: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not hasattr(graph_store, "get_graph"):
        return None
    graph_dict = graph_store.get_graph(graph_id)
    if not isinstance(graph_dict, dict):
        return None
    metadata = graph_dict.get("metadata") if isinstance(graph_dict.get("metadata"), dict) else {}
    display_name = str(
        metadata.get("name")
        or (listing or {}).get("name")
        or graph_id
    ).strip() or graph_id
    return {
        "graph_id": graph_id,
        "display_name": display_name,
        "normalized_name": normalize_workflow_id(display_name) or "",
        "updated_at": str(
            metadata.get("updated_at")
            or (listing or {}).get("updated_at")
            or ""
        ).strip()
        or None,
        "graph_dict": graph_dict,
    }


def _graph_inventory(graph_store: Any) -> dict[str, dict[str, Any]]:
    inventory: dict[str, dict[str, Any]] = {}
    if not hasattr(graph_store, "list_graphs"):
        return inventory
    try:
        items = list(graph_store.list_graphs())
    except Exception:
        return inventory
    for item in items:
        graph_id = str(item.get("graph_id") or "").strip()
        if not graph_id:
            continue
        entry = _inventory_entry(graph_store, graph_id, item)
        if entry is not None:
            inventory[graph_id] = entry
    return inventory


def _build_resolution(
    entry: dict[str, Any],
    *,
    requested_reference: str,
    source: str,
    expected_revision: str | None = None,
    message: str | None = None,
) -> WorkflowResolution:
    graph_dict = entry["graph_dict"]
    revision = compute_graph_revision(graph_dict)
    stale = bool(expected_revision and expected_revision != revision)
    status = "stale" if stale else "resolved"
    if message is None and stale:
        message = (
            f"Workflow `{entry['graph_id']}` changed since revision `{expected_revision}`; "
            f"current revision is `{revision}`."
        )
    return WorkflowResolution(
        requested_reference=requested_reference,
        graph_id=entry["graph_id"],
        display_name=entry["display_name"],
        normalized_name=entry["normalized_name"],
        revision=revision,
        fingerprint=revision,
        updated_at=entry["updated_at"],
        resolution_source=source,
        status=status,
        stale=stale,
        resolution_message=message,
    )


def _missing_resolution(
    requested_reference: str,
    *,
    source: str,
    message: str,
) -> WorkflowResolution:
    return WorkflowResolution(
        requested_reference=requested_reference,
        graph_id=None,
        display_name=None,
        normalized_name=None,
        revision=None,
        fingerprint=None,
        updated_at=None,
        resolution_source=source,
        status="missing",
        resolution_message=message,
    )


def _ambiguous_resolution(
    requested_reference: str,
    *,
    source: str,
    matched_graph_ids: Sequence[str],
    message: str,
) -> WorkflowResolution:
    return WorkflowResolution(
        requested_reference=requested_reference,
        graph_id=None,
        display_name=None,
        normalized_name=None,
        revision=None,
        fingerprint=None,
        updated_at=None,
        resolution_source=source,
        status="ambiguous",
        resolution_message=message,
        matched_graph_ids=tuple(matched_graph_ids),
    )


def resolve_workflow_reference(
    graph_store: Any,
    reference: str | None,
    *,
    context: WorkflowReferenceContext | None = None,
) -> WorkflowResolution:
    """Resolve a workflow reference against saved graphs and current context."""

    ctx = context or WorkflowReferenceContext()
    requested = str(reference or "").strip()
    requested_lower = requested.lower()

    inventory = _graph_inventory(graph_store)

    def resolve_graph_id(
        graph_id: str,
        *,
        source: str,
        message: str | None = None,
    ) -> WorkflowResolution:
        candidate_id = str(graph_id or "").strip()
        if not candidate_id:
            return _missing_resolution(
                requested,
                source=source,
                message="No workflow reference was provided.",
            )
        if candidate_id == "_scratch" and not ctx.allow_scratch:
            return _missing_resolution(
                requested,
                source=source,
                message="`_scratch` is not valid for this action. Save the workflow first.",
            )
        entry = inventory.get(candidate_id)
        if entry is None:
            return _missing_resolution(
                requested,
                source=source,
                message=f"Workflow `{candidate_id}` was not found in the current catalog.",
            )
        return _build_resolution(
            entry,
            requested_reference=requested,
            source=source,
            expected_revision=ctx.expected_revision,
            message=message,
        )

    if requested and requested not in _CURRENT_REFS:
        if requested in inventory:
            return resolve_graph_id(requested, source="explicit_id")

        exact_name_matches = [
            entry
            for entry in inventory.values()
            if entry["display_name"].lower() == requested_lower
        ]
        if len(exact_name_matches) == 1:
            return _build_resolution(
                exact_name_matches[0],
                requested_reference=requested,
                source="exact_name",
                expected_revision=ctx.expected_revision,
                message=(
                    None
                    if exact_name_matches[0]["graph_id"] == requested
                    else f"Matched workflow name `{requested}` to `{exact_name_matches[0]['graph_id']}`."
                ),
            )
        if len(exact_name_matches) > 1:
            return _ambiguous_resolution(
                requested,
                source="exact_name",
                matched_graph_ids=[entry["graph_id"] for entry in exact_name_matches],
                message=(
                    f"Workflow name `{requested}` matches multiple saved workflows: "
                    + ", ".join(f"`{entry['graph_id']}`" for entry in exact_name_matches[:5])
                    + ". Use an exact workflow ID."
                ),
            )

        normalized_requested = normalize_workflow_id(requested) or ""
        if normalized_requested:
            normalized_matches = [
                entry
                for entry in inventory.values()
                if normalized_requested in {entry["graph_id"], entry["normalized_name"]}
            ]
            if len(normalized_matches) == 1:
                return _build_resolution(
                    normalized_matches[0],
                    requested_reference=requested,
                    source="normalized_name",
                    expected_revision=ctx.expected_revision,
                    message=f"Matched workflow reference `{requested}` to `{normalized_matches[0]['graph_id']}`.",
                )
            if len(normalized_matches) > 1:
                return _ambiguous_resolution(
                    requested,
                    source="normalized_name",
                    matched_graph_ids=[entry["graph_id"] for entry in normalized_matches],
                    message=(
                        f"Workflow reference `{requested}` matches multiple saved workflows: "
                        + ", ".join(f"`{entry['graph_id']}`" for entry in normalized_matches[:5])
                        + ". Use an exact workflow ID."
                    ),
                )

        return _missing_resolution(
            requested,
            source="explicit_reference",
            message=f"Workflow `{requested}` was not found in the current catalog.",
        )

    current_workflow_id = str(ctx.current_workflow_id or "").strip()
    if current_workflow_id:
        resolved = resolve_graph_id(current_workflow_id, source="current_workflow")
        if resolved.resolved:
            return resolved
        return _missing_resolution(
            requested,
            source="current_workflow",
            message=(
                f"The current workflow reference points to `{current_workflow_id}`, "
                "but that saved workflow is no longer present."
            ),
        )

    project_candidates = [
        str(item or "").strip()
        for item in ctx.project_workflow_ids
        if str(item or "").strip()
    ]
    if len(project_candidates) == 1:
        return resolve_graph_id(project_candidates[0], source="project_linked_workflow")
    if len(project_candidates) > 1:
        return _ambiguous_resolution(
            requested,
            source="project_linked_workflow",
            matched_graph_ids=project_candidates,
            message=(
                "Multiple project-linked workflows are available: "
                + ", ".join(f"`{graph_id}`" for graph_id in project_candidates[:5])
                + ". Tell me which workflow you want."
            ),
        )

    return _missing_resolution(
        requested,
        source="current_workflow",
        message="No current workflow is available for this action.",
    )


def build_workflow_context_pack(
    *,
    graph_store: Any,
    workflow_resolution: WorkflowResolution,
    thread_id: str | None = None,
    chat_store: Any | None = None,
    run_manager: Any | None = None,
    run_store: Any | None = None,
    schedule_store: Any | None = None,
    token_budget: int = _TOKEN_LIMIT,
) -> tuple[str, dict[str, Any]]:
    """Build a compact JSON workflow context pack for follow-up workflow turns."""

    if not workflow_resolution.resolved or not workflow_resolution.graph_id:
        return "", {}

    graph_dict = graph_store.get_graph(workflow_resolution.graph_id)
    if not isinstance(graph_dict, dict):
        return "", {}

    graph = Graph.model_validate(graph_dict)
    interface = derive_workflow_interface(graph)
    required_inputs = list(interface.input_schema.get("required") or [])

    pack: dict[str, Any] = {
        "identity": {
            "workflow_id": workflow_resolution.graph_id,
            "display_name": workflow_resolution.display_name,
            "revision": workflow_resolution.revision,
            "resolution_source": workflow_resolution.resolution_source,
            "updated_at": workflow_resolution.updated_at,
            "stale": workflow_resolution.stale,
        },
        "required_inputs": required_inputs,
    }

    mutation_preview = _latest_mutation_preview(
        chat_store,
        workflow_resolution.graph_id,
        thread_id,
    )
    if mutation_preview:
        pack["last_mutation_preview"] = mutation_preview

    recent_run = _latest_run_summary(
        workflow_resolution.graph_id,
        run_manager=run_manager,
        run_store=run_store,
    )
    if recent_run:
        pack["recent_run"] = recent_run

    recent_schedule = _latest_schedule_summary(
        workflow_resolution.graph_id,
        schedule_store=schedule_store,
    )
    if recent_schedule:
        pack["recent_schedule"] = recent_schedule

    serialized = _serialize_pack_with_budget(pack, token_budget=token_budget)
    return serialized, pack


def _latest_mutation_preview(
    chat_store: Any | None,
    workflow_id: str,
    thread_id: str | None,
) -> dict[str, Any] | None:
    if chat_store is None or not thread_id:
        return None
    thread = chat_store.get_thread(workflow_id, thread_id)
    if thread is not None:
        for message in reversed(getattr(thread, "messages", []) or []):
            mutation_plan = getattr(message, "mutation_plan", None)
            if not isinstance(mutation_plan, dict) or not mutation_plan:
                continue
            return {
                "description": str(mutation_plan.get("description") or "").strip() or None,
                "status": getattr(message, "mutation_status", None) or "proposed",
                "message_id": getattr(message, "id", None),
            }
    meta = chat_store.get_thread_meta(workflow_id, thread_id)
    preview = meta.get("latest_mutation_preview") if isinstance(meta, dict) else None
    if not isinstance(preview, dict):
        return None
    mutation_plan = preview.get("mutation_plan")
    if not isinstance(mutation_plan, dict):
        return None
    return {
        "description": str(mutation_plan.get("description") or "").strip() or None,
        "status": "proposed",
        "message_id": preview.get("message_id"),
    }


def _latest_run_summary(
    workflow_id: str,
    *,
    run_manager: Any | None,
    run_store: Any | None,
) -> dict[str, Any] | None:
    runs: list[dict[str, Any]] = []
    if run_manager is not None:
        try:
            runs.extend(
                run for run in run_manager.list_runs()
                if str(run.get("graph_id") or "") == workflow_id
            )
        except Exception:
            pass
    if not runs and run_store is not None:
        try:
            runs.extend(run_store.list_summaries(workflow_id=workflow_id, limit=1))
        except Exception:
            pass
    if not runs:
        return None
    runs.sort(key=lambda item: item.get("started_at", 0), reverse=True)
    latest = runs[0]
    return {
        "run_id": latest.get("run_id"),
        "status": latest.get("status"),
        "phase": latest.get("phase"),
        "started_at": latest.get("started_at"),
        "graph_revision": latest.get("graph_revision"),
    }


def _latest_schedule_summary(
    workflow_id: str,
    *,
    schedule_store: Any | None,
) -> dict[str, Any] | None:
    if schedule_store is None:
        return None
    try:
        entries = [
            entry
            for entry in schedule_store.list_all()
            if str(getattr(entry, "workflow_id", "") or "") == workflow_id
        ]
    except Exception:
        return None
    if not entries:
        return None
    entries.sort(
        key=lambda entry: (
            getattr(entry, "next_run", None) or datetime.min.replace(tzinfo=timezone.utc),
            getattr(entry, "created_at", None) or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )
    latest = entries[0]
    next_run = getattr(latest, "next_run", None)
    return {
        "schedule_id": getattr(latest, "id", None),
        "trigger": getattr(latest, "trigger", None),
        "timezone": getattr(latest, "timezone", None),
        "next_run": next_run.isoformat() if next_run is not None else None,
        "enabled": bool(getattr(latest, "enabled", True)),
    }


def _serialize_pack_with_budget(pack: dict[str, Any], *, token_budget: int) -> str:
    working = json.loads(json.dumps(pack))
    serialized = json.dumps(working, sort_keys=True, separators=(",", ":"))
    if _estimate_tokens(serialized) <= token_budget:
        return serialized

    for key in ("recent_run", "recent_schedule", "last_mutation_preview", "required_inputs"):
        if key in working:
            working.pop(key, None)
            serialized = json.dumps(working, sort_keys=True, separators=(",", ":"))
            if _estimate_tokens(serialized) <= token_budget:
                return serialized
    return serialized


def _estimate_tokens(text: str) -> int:
    try:
        import tiktoken  # type: ignore[import-not-found]

        encoder = tiktoken.get_encoding("cl100k_base")
        return len(encoder.encode(text))
    except Exception:
        return max(1, len(text) // 4)


def workflow_resolution_context_from_session(
    *,
    current_workflow_id: str | None = None,
    project_workflow_ids: Iterable[str] | None = None,
    expected_revision: str | None = None,
    allow_scratch: bool = True,
) -> WorkflowReferenceContext:
    return WorkflowReferenceContext(
        current_workflow_id=str(current_workflow_id or "").strip() or None,
        project_workflow_ids=tuple(
            str(item or "").strip()
            for item in (project_workflow_ids or [])
            if str(item or "").strip()
        ),
        expected_revision=str(expected_revision or "").strip() or None,
        allow_scratch=allow_scratch,
    )
