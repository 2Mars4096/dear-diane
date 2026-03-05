"""Pydantic models for the gateway API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, model_validator


class DispatchRequest(BaseModel):
    """Request to dispatch a workflow from any surface."""

    workflow_id: str | None = None
    workflow_path: str | None = None
    inputs: dict[str, Any] | None = None
    text: str | None = None
    surface_id: str | None = None
    auto_approve: bool = False
    use_meta: bool = False
    config_overrides: dict[str, Any] | None = None
    human_timeout: int | None = None

    @model_validator(mode="after")
    def check_at_least_one_source(self) -> "DispatchRequest":
        def _present(value: str | None) -> bool:
            return value is not None and bool(value.strip())

        if not any(
            _present(x)
            for x in [self.workflow_id, self.workflow_path, self.text]
        ):
            raise ValueError(
                "At least one of workflow_id, workflow_path, or text is required"
            )
        return self


class DispatchResult(BaseModel):
    """Response from dispatching a workflow."""

    run_id: str
    workflow_name: str
    status: str
    surface_id: str | None = None


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


class SubmitInputRequest(BaseModel):
    """Request to submit a human input response."""

    run_id: str
    request_id: str
    response: dict[str, Any]
    responder_surface: str | None = None


class CancelRequest(BaseModel):
    """Request to cancel a running workflow."""

    run_id: str


class CancelResult(BaseModel):
    """Response from cancelling a workflow."""

    run_id: str
    cancelled: bool


class ActivitySnapshot(BaseModel):
    """Snapshot of current gateway activity."""

    active: list[dict[str, Any]]
    recent: list[dict[str, Any]]
    connected_surfaces: list[dict[str, Any]]


class SurfaceRegistration(BaseModel):
    """Register a surface with the gateway."""

    surface_id: str
    surface_type: str
