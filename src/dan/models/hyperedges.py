"""Hyperedge models — skills, guardrails, style rules, and overrides.

Hyperedges are graph-level constructs that attach to arbitrary subsets of
nodes and modify execution behavior at runtime via hooks (pre_prompt,
tool_call, post_output, validation).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


HyperedgeType = Literal["skill", "guardrail", "style", "override"]
HookType = Literal["pre_prompt", "tool_call", "post_output", "validation"]

VALID_HOOKS_BY_TYPE: dict[str, set[str]] = {
    "skill": {"pre_prompt"},
    "style": {"pre_prompt"},
    "guardrail": {"pre_prompt", "tool_call", "post_output", "validation"},
    "override": {"tool_call", "pre_prompt"},
}

TYPE_RANK: dict[str, int] = {"override": 0, "guardrail": 1, "style": 2, "skill": 3}

SCOPE_RANK: dict[str, int] = {"node_id": 0, "tag": 1, "type": 2, "subgraph": 3, "global": 4}


class Hyperedge(BaseModel):
    """A behavior modifier that attaches to multiple nodes simultaneously.

    Hyperedges live in ``Graph.hyperedges`` (not mixed into ``Graph.edges``).
    The resolver computes node attachments from selectors at runtime.
    """

    id: str
    name: str
    description: str = ""

    hyperedge_type: HyperedgeType
    hook: HookType
    content: str = Field(
        description="Injected text (skill/style), rule expression (guardrail/validation), or override spec (JSON/YAML)",
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Type-specific settings: severity, block_on_fail, etc.",
    )
    enabled: bool = True

    # --- Attachment selectors (at least one must be non-empty, or attach_globally=True) ---
    attach_to: list[str] = Field(default_factory=list, description="Specific node IDs")
    attach_to_type: list[str] = Field(default_factory=list, description="Node type strings")
    attach_to_tags: list[str] = Field(default_factory=list, description="User-defined tag labels")
    attach_to_subgraph: list[str] = Field(
        default_factory=list, description="Composite node IDs whose children are targeted",
    )
    attach_globally: bool = Field(
        default=False, description="When True, applies to all nodes in scope",
    )

    propagate: bool = Field(
        default=True,
        description="When True, propagates into sub-graphs during execution",
    )
    priority: int | None = Field(
        default=None,
        description="Explicit ordering within same type (lower wins); None = use default precedence",
    )

    @model_validator(mode="after")
    def _check_selectors(self) -> "Hyperedge":
        has_selector = (
            self.attach_globally
            or bool(self.attach_to)
            or bool(self.attach_to_type)
            or bool(self.attach_to_tags)
            or bool(self.attach_to_subgraph)
        )
        if not has_selector:
            raise ValueError(
                "Hyperedge must have at least one attachment selector "
                "(attach_to, attach_to_type, attach_to_tags, attach_to_subgraph) "
                "or attach_globally=True"
            )
        return self

    @model_validator(mode="after")
    def _check_hook_compat(self) -> "Hyperedge":
        valid = VALID_HOOKS_BY_TYPE.get(self.hyperedge_type, set())
        if self.hook not in valid:
            raise ValueError(
                f"Hyperedge type '{self.hyperedge_type}' does not support "
                f"hook '{self.hook}'; valid hooks: {sorted(valid)}"
            )
        return self


class ValidationResult(BaseModel):
    """Outcome of a single hyperedge validation check against node output."""

    hyperedge_id: str
    passed: bool
    message: str = ""
    severity: Literal["info", "warning", "error"] = "warning"


class HyperedgeViolation(Exception):
    """Raised when a guardrail/validation hyperedge blocks execution (strict mode)."""

    def __init__(self, results: list[ValidationResult], node_id: str) -> None:
        failed = [r for r in results if not r.passed]
        msg = f"Hyperedge violation on node '{node_id}': {len(failed)} check(s) failed"
        super().__init__(msg)
        self.results = results
        self.node_id = node_id
