"""Edge definitions — typed channels between nodes."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dan.linter.config import LintConfig
from dan.models.context import ContextMode


class EdgeBase(BaseModel):
    """Fields shared by every edge type.

    An edge connects an output port on a source node to an input port
    on a target node.  ``source_port`` / ``target_port`` correspond to
    ``OutputPort.name`` / ``InputPort.name`` on the respective nodes.
    """

    id: str
    source_node_id: str
    source_port: str
    target_node_id: str
    target_port: str
    ui: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary UI hints (label, color, waypoints, …)",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class DataEdge(EdgeBase):
    """Carries structured data between two ports.

    Schema compatibility between the source output port and the target
    input port is validated at graph-construction time.
    """

    model_config = ConfigDict(validate_assignment=True)

    edge_type: Literal["data"] = "data"
    spread: bool = False
    lint: LintConfig | None = None

    @model_validator(mode="before")
    @classmethod
    def _lift_metadata_lint(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if value.get("lint") is not None:
            return value
        metadata = value.get("metadata")
        if isinstance(metadata, dict) and metadata.get("lint") is not None:
            updated = dict(value)
            updated["lint"] = metadata["lint"]
            return updated
        return value

    @model_validator(mode="after")
    def _sync_lint_metadata(self) -> "DataEdge":
        metadata = dict(self.metadata or {})
        if self.lint is None:
            metadata.pop("lint", None)
        else:
            metadata["lint"] = self._compact_lint_payload(self.lint)
        object.__setattr__(self, "metadata", metadata)
        return self

    @staticmethod
    def _compact_lint_payload(lint: LintConfig) -> dict[str, Any]:
        def prune(value: Any) -> Any:
            if isinstance(value, dict):
                pruned = {
                    key: prune(inner)
                    for key, inner in value.items()
                }
                return {
                    key: inner
                    for key, inner in pruned.items()
                    if inner not in (None, "", [], {})
                }
            if isinstance(value, list):
                pruned_items = [prune(item) for item in value]
                return [item for item in pruned_items if item not in (None, "", [], {})]
            return value

        payload = prune(lint.model_dump(mode="json", exclude_none=True))
        if payload.get("tier3_threshold") == 0.6:
            payload.pop("tier3_threshold", None)
        if payload.get("autofix") == []:
            payload.pop("autofix", None)
        if payload.get("max_retries") == 0:
            payload.pop("max_retries", None)
        if payload.get("retry_budget_ms") == 0:
            payload.pop("retry_budget_ms", None)
        return payload


class ControlEdge(EdgeBase):
    """Encodes routing / flow-control signals (branch selection, loop-back).

    ``condition`` is an optional expression for conditional routing
    (e.g. the true/false branch of an IfElseNode).
    """

    edge_type: Literal["control"] = "control"
    condition: str | None = None


class ContextEdge(EdgeBase):
    """Connects a node to a shared-context key.

    ``mode`` must be one of read / write / append and must match the
    node's declared ``read_set`` or ``write_set``.
    """

    edge_type: Literal["context"] = "context"
    context_key: str = Field(description="Shared-context key this edge references")
    mode: ContextMode
    # -- 18-1: Smart context assembly ------------------------------------------
    pass_by_reference: bool = Field(
        default=False,
        description="When true, pass artifact reference instead of inline content; lazy-loaded on demand",
    )
