"""Runtime resolver for hyperedge-based behavior modification.

The HyperedgeResolver matches hyperedges to nodes at execution time,
applies pre-prompt injections, post-output validations, tool-call
interceptions, and expression-based validation checks.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from dan.models.hyperedges import (
    SCOPE_RANK,
    TYPE_RANK,
    Hyperedge,
    HyperedgeViolation,
    ValidationResult,
)

if TYPE_CHECKING:
    from dan.models.graph import Graph
    from dan.models.nodes import NodeBase

logger = logging.getLogger(__name__)


class HyperedgeResolver:
    """Resolves, matches, and applies hyperedges to nodes at runtime.

    Constructor merges graph-level hyperedges with optional parent-graph
    hyperedges (for sub-graph propagation).  Only ``enabled=True``
    hyperedges survive the merge.
    """

    def __init__(
        self,
        graph: Graph,
        parent_hyperedges: list[Hyperedge] | None = None,
        parent_scope_node_id: str | None = None,
    ) -> None:
        merged: dict[str, Hyperedge] = {}

        if parent_hyperedges:
            for he in parent_hyperedges:
                if he.propagate:
                    merged[he.id] = he

        for he in graph.hyperedges:
            merged[he.id] = he

        self._hyperedges = [he for he in merged.values() if he.enabled]
        self._graph = graph
        self._cache: dict[tuple[str, str], list[Hyperedge]] = {}
        self._parent_scope_node_id = parent_scope_node_id

        self._subgraph_members: dict[str, set[str]] = {}
        for he in self._hyperedges:
            for sg_id in he.attach_to_subgraph:
                if sg_id not in self._subgraph_members:
                    comp_node = graph.node_by_id(sg_id)
                    if comp_node is not None:
                        body_key = getattr(comp_node, "body_graph", None)
                        if body_key and body_key in graph.sub_graphs:
                            sub = graph.sub_graphs[body_key]
                            self._subgraph_members[sg_id] = {n.id for n in sub.nodes}
                        else:
                            self._subgraph_members[sg_id] = set()
                    else:
                        self._subgraph_members[sg_id] = set()

    @property
    def active_hyperedges(self) -> list[Hyperedge]:
        """All merged, enabled hyperedges — used for child propagation."""
        return list(self._hyperedges)

    # ------------------------------------------------------------------
    # Resolve
    # ------------------------------------------------------------------

    def resolve(self, node: NodeBase, hook: str) -> list[Hyperedge]:
        """Return matching hyperedges for *node* + *hook*, sorted by precedence.

        Results are cached per ``(node.id, hook)`` tuple.
        """
        cache_key = (node.id, hook)
        if cache_key in self._cache:
            return self._cache[cache_key]

        node_tags = set(getattr(node, "tags", [])) | set(
            getattr(node, "metadata", {}).get("tags", [])
        )
        node_type = getattr(node, "node_type", "")

        matches: list[tuple[Hyperedge, int]] = []
        for he in self._hyperedges:
            if he.hook != hook:
                continue
            scope = self._match_scope(he, node, node_tags, node_type)
            if scope is not None:
                matches.append((he, scope))

        matches.sort(
            key=lambda pair: (
                TYPE_RANK.get(pair[0].hyperedge_type, 99),
                pair[1],
                pair[0].priority if pair[0].priority is not None else float("inf"),
                pair[0].id,
            )
        )

        result = [he for he, _ in matches]
        self._cache[cache_key] = result
        return result

    def _match_scope(
        self,
        he: Hyperedge,
        node: NodeBase,
        node_tags: set[str],
        node_type: str,
    ) -> int | None:
        """Return the most-specific scope rank if *he* matches *node*, else ``None``."""
        best: int | None = None

        if node.id in he.attach_to:
            return SCOPE_RANK["node_id"]

        if he.attach_to_tags and node_tags & set(he.attach_to_tags):
            best = SCOPE_RANK["tag"]

        if node_type and node_type in he.attach_to_type:
            rank = SCOPE_RANK["type"]
            if best is None or rank < best:
                best = rank

        for sg_id in he.attach_to_subgraph:
            if sg_id == self._parent_scope_node_id:
                rank = SCOPE_RANK["subgraph"]
                if best is None or rank < best:
                    best = rank
                break
            members = self._subgraph_members.get(sg_id, set())
            if node.id in members:
                rank = SCOPE_RANK["subgraph"]
                if best is None or rank < best:
                    best = rank
                break

        if he.attach_globally:
            rank = SCOPE_RANK["global"]
            if best is None or rank < best:
                best = rank

        return best

    # ------------------------------------------------------------------
    # Pre-prompt hook
    # ------------------------------------------------------------------

    def apply_pre_prompt(
        self, node: NodeBase, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Inject hyperedge content into *messages* based on type.

        Returns a new list (original is not mutated).
        """
        resolved = self.resolve(node, "pre_prompt")
        if not resolved:
            return messages

        messages = [dict(m) for m in messages]

        for he in resolved:
            if he.hyperedge_type == "skill":
                sys_idx = next(
                    (i for i, m in enumerate(messages) if m.get("role") == "system"),
                    None,
                )
                if sys_idx is not None:
                    messages.insert(sys_idx, {"role": "system", "content": he.content})
                else:
                    messages.insert(0, {"role": "system", "content": he.content})

            elif he.hyperedge_type == "style":
                sys_idx = next(
                    (i for i, m in enumerate(messages) if m.get("role") == "system"),
                    None,
                )
                if sys_idx is not None:
                    messages[sys_idx] = dict(messages[sys_idx])
                    messages[sys_idx]["content"] += f"\n\n{he.content}"
                else:
                    messages.insert(0, {"role": "system", "content": he.content})

            elif he.hyperedge_type == "guardrail":
                sys_idx = next(
                    (i for i, m in enumerate(messages) if m.get("role") == "system"),
                    None,
                )
                if sys_idx is not None:
                    messages[sys_idx] = dict(messages[sys_idx])
                    messages[sys_idx]["content"] += f"\n\n[Constraint] {he.content}"
                else:
                    messages.insert(
                        0,
                        {"role": "system", "content": f"[Constraint] {he.content}"},
                    )

            elif he.hyperedge_type == "override":
                sys_idx = next(
                    (i for i, m in enumerate(messages) if m.get("role") == "system"),
                    None,
                )
                if sys_idx is not None:
                    messages[sys_idx] = dict(messages[sys_idx])
                    messages[sys_idx]["content"] = he.content
                else:
                    messages.insert(0, {"role": "system", "content": he.content})

        return messages

    # ------------------------------------------------------------------
    # Post-output hook
    # ------------------------------------------------------------------

    def apply_post_output(
        self,
        node: NodeBase,
        result: Any,
        enforcement: str = "warn",
    ) -> tuple[Any, list[ValidationResult]]:
        """Evaluate post_output rules against *result.outputs*.

        Returns ``(result, violations)``.  In strict mode with
        ``block_on_fail``, raises :class:`HyperedgeViolation`.
        """
        resolved = self.resolve(node, "post_output")
        if not resolved:
            return result, []

        from dan.engine.conditions import ConditionError, evaluate_condition

        violations: list[ValidationResult] = []
        outputs = getattr(result, "outputs", {}) or {}

        for he in resolved:
            content = he.content.strip()
            block_on_fail = he.config.get("block_on_fail", False)
            severity = he.config.get("severity", "warning")

            try:
                passed = evaluate_condition(content, outputs)
            except ConditionError as exc:
                vr = ValidationResult(
                    hyperedge_id=he.id,
                    passed=False,
                    message=f"Evaluation error: {exc}",
                    severity=severity,
                )
                violations.append(vr)
                if enforcement == "strict" and block_on_fail:
                    raise HyperedgeViolation(violations, node.id)
                continue

            vr = ValidationResult(
                hyperedge_id=he.id,
                passed=passed,
                message="" if passed else f"Rule failed: {content}",
                severity=severity if not passed else "info",
            )
            violations.append(vr)

            if not passed and enforcement == "strict" and block_on_fail:
                raise HyperedgeViolation(violations, node.id)

        return result, violations

    # ------------------------------------------------------------------
    # Tool-call hook
    # ------------------------------------------------------------------

    def apply_tool_call(
        self,
        node: NodeBase,
        tool_id: str,
        args: dict[str, Any],
    ) -> tuple[str, dict[str, Any], bool]:
        """Intercept a tool call — override may modify or block it.

        Returns ``(tool_id, args, allow)``.
        """
        resolved = self.resolve(node, "tool_call")
        if not resolved:
            return tool_id, args, True

        allow = True
        for he in resolved:
            if he.hyperedge_type == "override":
                try:
                    spec = json.loads(he.content)
                except (json.JSONDecodeError, ValueError):
                    spec = {}
                if "tool_id" in spec:
                    tool_id = spec["tool_id"]
                if isinstance(spec.get("args"), dict):
                    args = {**args, **spec["args"]}
                if spec.get("block", False):
                    allow = False

            elif he.hyperedge_type == "guardrail":
                block_on_fail = he.config.get("block_on_fail", False)
                try:
                    from dan.engine.conditions import evaluate_condition

                    passed = evaluate_condition(
                        he.content, {"tool_id": tool_id, "args": args}
                    )
                except Exception:
                    passed = False
                if not passed and block_on_fail:
                    allow = False

        return tool_id, args, allow

    # ------------------------------------------------------------------
    # Validation hook
    # ------------------------------------------------------------------

    def apply_validation(
        self,
        node: NodeBase,
        outputs: dict[str, Any],
        enforcement: str = "warn",
    ) -> list[ValidationResult]:
        """Evaluate validation expressions/schemas against *outputs*.

        In strict mode with ``block_on_fail``, raises
        :class:`HyperedgeViolation` on the first blocking failure.
        """
        resolved = self.resolve(node, "validation")
        if not resolved:
            return []

        from dan.engine.conditions import ConditionError, evaluate_condition

        results: list[ValidationResult] = []

        for he in resolved:
            content = he.content.strip()
            severity = he.config.get("severity", "warning")
            block_on_fail = he.config.get("block_on_fail", False)

            try:
                passed = evaluate_condition(content, outputs)
            except ConditionError as exc:
                vr = ValidationResult(
                    hyperedge_id=he.id,
                    passed=False,
                    message=f"Validation error: {exc}",
                    severity=severity,
                )
                results.append(vr)
                if enforcement == "strict" and block_on_fail:
                    raise HyperedgeViolation(results, node.id)
                continue

            vr = ValidationResult(
                hyperedge_id=he.id,
                passed=passed,
                message="" if passed else f"Validation failed: {content}",
                severity=severity if not passed else "info",
            )
            results.append(vr)

            if not passed and enforcement == "strict" and block_on_fail:
                raise HyperedgeViolation(results, node.id)

        return results
