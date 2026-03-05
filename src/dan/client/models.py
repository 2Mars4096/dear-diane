"""Client-side models for DanClient responses."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class DispatchResult(BaseModel):
    """Result from dispatching a workflow."""

    run_id: str
    workflow_name: str
    status: str
    surface_id: str | None = None


class RunSummary(BaseModel):
    """Summary of a run from list_runs."""

    run_id: str
    graph_id: str = ""
    status: str = ""
    start_time: float | None = None
    end_time: float | None = None


class PendingInput(BaseModel):
    """A pending HumanNode input request."""

    run_id: str
    request_id: str
    node_id: str | None = None
    prompt: str | None = None
    input_schema: dict[str, Any] | None = None
    render_mode: str | None = None
    surface_origin: str | None = None
    waiting_since: float | None = None


class ActivitySnapshot(BaseModel):
    """Snapshot of gateway activity."""

    active: list[dict[str, Any]] = []
    recent: list[dict[str, Any]] = []
    connected_surfaces: list[dict[str, Any]] = []


class CancelResult(BaseModel):
    """Result from cancelling a run."""

    run_id: str
    cancelled: bool
