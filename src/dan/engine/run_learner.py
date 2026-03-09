"""Post-run learning and cross-section reinforcement (29-6 §2, §3).

Extracts knowledge from workflow run outcomes and applies cross-section
feedback (boost, demote, update) to memory items based on real outcomes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from dan.engine.memory_kernel import MemoryItem, MemoryScope, MemoryType

logger = logging.getLogger(__name__)


def _classify_error(error_str: str) -> str:
    """Classify an error string into a broad category."""
    lower = error_str.lower()
    if "timeout" in lower or "timed out" in lower:
        return "timeout"
    if "validat" in lower or "schema" in lower or "invalid" in lower:
        return "validation"
    if "api" in lower or "401" in lower or "403" in lower or "429" in lower or "500" in lower:
        return "api_error"
    if "rate" in lower and "limit" in lower:
        return "rate_limit"
    if "permission" in lower or "denied" in lower:
        return "permission"
    if "not found" in lower or "404" in lower:
        return "not_found"
    return "unknown"


def _detect_topology(workflow: dict[str, Any]) -> str | None:
    """Detect workflow topology from graph dict (mirrors BuildSessionManager logic)."""
    nodes = workflow.get("nodes") or []
    edges = workflow.get("edges") or []
    if len(nodes) < 2:
        return None

    node_ids: set[str] = set()
    for n in nodes:
        nid = n.get("id") if isinstance(n, dict) else getattr(n, "id", None)
        if nid:
            node_ids.add(nid)
    if not node_ids:
        return None

    out_degree: dict[str, int] = {nid: 0 for nid in node_ids}
    in_degree: dict[str, int] = {nid: 0 for nid in node_ids}
    for e in edges:
        src = e.get("source_node_id") if isinstance(e, dict) else getattr(e, "source_node_id", None)
        tgt = e.get("target_node_id") if isinstance(e, dict) else getattr(e, "target_node_id", None)
        if src and tgt and src in out_degree and tgt in in_degree:
            out_degree[src] += 1
            in_degree[tgt] += 1

    has_loop = any(
        (n.get("node_type") if isinstance(n, dict) else getattr(n, "node_type", ""))
        in ("while_loop", "for_each")
        for n in nodes
    )
    if has_loop:
        return "loop"

    max_out = max(out_degree.values()) if out_degree else 0
    max_in = max(in_degree.values()) if in_degree else 0
    if max_out >= 2 and max_in >= 2:
        return "dag"
    if max_out >= 2:
        return "fan_out"
    if max_in >= 2:
        return "fan_in"
    return "linear"


class RunLearner:
    """Extracts knowledge from workflow run outcomes and stores in memory kernel."""

    def __init__(self, memory_kernel: Any) -> None:
        self.memory_kernel = memory_kernel

    # ------------------------------------------------------------------
    # 2-1: Main extraction entry point
    # ------------------------------------------------------------------

    def extract_run_learnings(
        self,
        run_result: dict[str, Any],
        workflow: dict[str, Any] | None = None,
        goal_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Extract and store memory items from a run outcome.

        Returns list of dicts describing stored/updated items.
        """
        stored: list[dict[str, Any]] = []
        if not run_result:
            return stored

        success = run_result.get("success")
        if success is None:
            return stored

        workflow_id = run_result.get("graph_id", "")
        run_id = run_result.get("run_id", "")

        if success:
            stored.extend(self._on_success(run_result, workflow, workflow_id, run_id))
            if goal_context:
                stored.extend(self._link_repair_strategy(goal_context, run_result))
        else:
            stored.extend(self._on_failure(run_result, workflow, goal_context, workflow_id, run_id))

        return stored

    async def extract_run_learnings_async(
        self,
        run_result: dict[str, Any],
        workflow: dict[str, Any] | None = None,
        goal_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Fan out independent post-run learning sub-tasks (29-5 §5-2).

        On success: asset update, pattern reinforcement, and repair strategy
        link are independent — fan them out.
        On failure: failure pattern creation and principle boosting are
        independent (error_category is pre-computed).
        """
        if not run_result:
            return []

        success = run_result.get("success")
        if success is None:
            return []

        workflow_id = run_result.get("graph_id", "")
        run_id = run_result.get("run_id", "")

        if success:
            tasks = [
                asyncio.to_thread(
                    self._update_or_create_asset, run_result, workflow_id, run_id,
                ),
            ]
            if workflow:
                tasks.append(asyncio.to_thread(
                    self._reinforce_pattern, workflow, workflow_id,
                ))
            if goal_context:
                tasks.append(asyncio.to_thread(
                    self._link_repair_strategy, goal_context, run_result,
                ))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            stored: list[dict[str, Any]] = []
            for r in results:
                if isinstance(r, Exception):
                    logger.debug("Post-run learning sub-task failed", exc_info=r)
                elif isinstance(r, list):
                    stored.extend(r)
            return stored
        else:
            error_category, first_error, first_node_id, node_type = (
                self._prepare_failure_info(run_result, workflow)
            )
            tasks_fail = [
                asyncio.to_thread(
                    self._store_failure_pattern,
                    workflow_id, run_id, first_node_id, node_type,
                    first_error, error_category, goal_context,
                ),
                asyncio.to_thread(
                    self._boost_matching_principles, error_category,
                ),
                asyncio.to_thread(
                    self._maybe_extract_new_principle,
                    first_error, error_category, workflow,
                ),
            ]
            results = await asyncio.gather(*tasks_fail, return_exceptions=True)
            stored = []
            for r in results:
                if isinstance(r, Exception):
                    logger.debug("Post-run learning sub-task failed", exc_info=r)
                elif isinstance(r, list):
                    stored.extend(r)
            return stored

    # ------------------------------------------------------------------
    # 2-2: Success → WORKFLOW_ASSET
    # ------------------------------------------------------------------

    def _update_or_create_asset(
        self,
        run_result: dict[str, Any],
        workflow_id: str,
        run_id: str,
    ) -> list[dict[str, Any]]:
        """Create or update the workflow asset for a successful run."""
        stored: list[dict[str, Any]] = []
        existing = self._find_asset_for_workflow(workflow_id)
        if existing:
            self.memory_kernel.update(
                existing.id,
                access_count=existing.access_count + 1,
                importance=min(existing.importance + 0.05, 1.0),
                metadata={
                    **existing.metadata,
                    "last_run_id": run_id,
                },
            )
            stored.append({"action": "updated_asset", "item_id": existing.id, "workflow_id": workflow_id})
        else:
            content = f"Workflow {workflow_id} completed successfully (run {run_id})."
            item = self.memory_kernel.store_workflow_asset(
                content=content,
                workflow_id=workflow_id,
                success_rate=1.0,
            )
            stored.append({"action": "created_asset", "item_id": item.id, "workflow_id": workflow_id})
        return stored

    def _on_success(
        self,
        run_result: dict[str, Any],
        workflow: dict[str, Any] | None,
        workflow_id: str,
        run_id: str,
    ) -> list[dict[str, Any]]:
        stored = self._update_or_create_asset(run_result, workflow_id, run_id)
        if workflow:
            stored.extend(self._reinforce_pattern(workflow, workflow_id))
        return stored

    # ------------------------------------------------------------------
    # 2-3: Success + pattern match → boost WORKFLOW_PATTERN
    # ------------------------------------------------------------------

    def _reinforce_pattern(
        self,
        workflow: dict[str, Any],
        workflow_id: str,
    ) -> list[dict[str, Any]]:
        stored: list[dict[str, Any]] = []
        detected = _detect_topology(workflow)
        if not detected:
            return stored

        matching = self.memory_kernel.list_by_type(MemoryType.WORKFLOW_PATTERN)
        for item in matching:
            pat_name = item.metadata.get("pattern_name") or ""
            content_lower = item.content.lower()
            if pat_name == detected or f"pattern: {detected}" in content_lower:
                self.memory_kernel.update(
                    item.id,
                    access_count=item.access_count + 1,
                    importance=min(item.importance + 0.05, 1.0),
                )
                stored.append({"action": "reinforced_pattern", "item_id": item.id, "pattern": detected})
        return stored

    # ------------------------------------------------------------------
    # 2-4: Failure → FAILURE_PATTERN
    # ------------------------------------------------------------------

    @staticmethod
    def _prepare_failure_info(
        run_result: dict[str, Any],
        workflow: dict[str, Any] | None,
    ) -> tuple[str, str, str, str]:
        """Extract error_category, first_error, first_node_id, node_type from a failed run."""
        errors = run_result.get("errors") or {}
        first_node_id = ""
        first_error = ""
        for nid, msg in errors.items():
            first_node_id = nid
            first_error = str(msg)[:300]
            break
        if not first_error:
            first_error = run_result.get("error") or "Unknown error"
            first_error = str(first_error)[:300]

        node_type = ""
        if workflow and first_node_id:
            for n in workflow.get("nodes") or []:
                nid = n.get("id") if isinstance(n, dict) else getattr(n, "id", None)
                if nid == first_node_id:
                    node_type = (
                        n.get("node_type") if isinstance(n, dict)
                        else getattr(n, "node_type", "")
                    )
                    break
        return _classify_error(first_error), first_error, first_node_id, node_type

    def _store_failure_pattern(
        self,
        workflow_id: str,
        run_id: str,
        first_node_id: str,
        node_type: str,
        first_error: str,
        error_category: str,
        goal_context: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        content = f"Workflow {workflow_id} failed at node {first_node_id} ({node_type}): {first_error}"
        item = self.memory_kernel.store_failure_pattern(
            content=content,
            metadata={
                "error_category": error_category,
                "node_type": node_type,
                "workflow_id": workflow_id,
                "node_id": first_node_id,
                "run_id": run_id,
            },
        )
        if goal_context is not None:
            goal_context["failure_pattern_id"] = item.id
        return [{
            "action": "created_failure_pattern",
            "item_id": item.id,
            "error_category": error_category,
        }]

    def _on_failure(
        self,
        run_result: dict[str, Any],
        workflow: dict[str, Any] | None,
        goal_context: dict[str, Any] | None,
        workflow_id: str,
        run_id: str,
    ) -> list[dict[str, Any]]:
        error_category, first_error, first_node_id, node_type = (
            self._prepare_failure_info(run_result, workflow)
        )
        stored = self._store_failure_pattern(
            workflow_id, run_id, first_node_id, node_type,
            first_error, error_category, goal_context,
        )
        stored.extend(self._boost_matching_principles(error_category))
        stored.extend(self._maybe_extract_new_principle(first_error, error_category, workflow))
        return stored

    # ------------------------------------------------------------------
    # 2-5: Failure + existing principle → boost confidence
    # ------------------------------------------------------------------

    def _boost_matching_principles(self, error_category: str) -> list[dict[str, Any]]:
        stored: list[dict[str, Any]] = []
        principles = self.memory_kernel.list_by_type(MemoryType.PRINCIPLE)
        for principle in principles:
            p_category = principle.metadata.get("error_category", "")
            p_tags = principle.tags or []
            if p_category == error_category or error_category in p_tags:
                confidence = float(principle.metadata.get("confidence", 0.5))
                self.memory_kernel.update(
                    principle.id,
                    metadata={**principle.metadata, "confidence": min(confidence + 0.1, 1.0)},
                )
                stored.append({"action": "boosted_principle", "item_id": principle.id})
        return stored

    # ------------------------------------------------------------------
    # 2-6: New failure pattern → extract principle via heuristic analysis
    # ------------------------------------------------------------------

    def _maybe_extract_new_principle(
        self,
        error_str: str,
        error_category: str,
        workflow: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """If no existing principle covers this failure category, extract a new one.

        Uses heuristic analysis (no LLM call) inspired by ReflectionNode-style
        causal principle extraction.  New principles start at low confidence (0.3)
        and are promoted by subsequent matching failures via
        ``_boost_matching_principles``.
        """
        existing = self.memory_kernel.list_by_type(MemoryType.PRINCIPLE)
        covered = any(
            p.metadata.get("error_category") == error_category
            or error_category in (p.tags or [])
            for p in existing
        )
        if covered:
            return []

        principle_content = f"Avoid {error_category} errors: {error_str[:200]}"
        if workflow:
            node_types = [
                n.get("node_type", "?")
                for n in (workflow.get("nodes") or [])
                if isinstance(n, dict)
            ]
            if node_types:
                principle_content += f" (workflow had: {', '.join(sorted(set(node_types)))})"

        item = self.memory_kernel.store_principle(
            content=principle_content,
            confidence=0.3,
            tags=[error_category],
        )
        return [{"action": "extracted_principle", "item_id": item.id, "error_category": error_category}]

    # ------------------------------------------------------------------
    # 2-7: Repair success → link strategy to failure pattern
    # ------------------------------------------------------------------

    def _link_repair_strategy(
        self,
        goal_context: dict[str, Any],
        run_result: dict[str, Any],
    ) -> list[dict[str, Any]]:
        stored: list[dict[str, Any]] = []
        repair_strategy = goal_context.get("repair_strategy")
        if not repair_strategy:
            return stored

        failure_item_id = str(goal_context.get("failure_pattern_id") or "").strip()
        if not failure_item_id:
            return stored

        is_repair_success = run_result.get("success", False)
        if not is_repair_success:
            return stored

        failure_item = self.memory_kernel.get(failure_item_id)
        if failure_item:
            self.memory_kernel.update(
                failure_item.id,
                metadata={
                    **failure_item.metadata,
                    "repair_strategy": repair_strategy,
                    "repair_success": True,
                },
            )
            stored.append({
                "action": "linked_repair",
                "item_id": failure_item_id,
                "repair_strategy": repair_strategy,
            })
        return stored

    # ------------------------------------------------------------------
    # 3-1: Reuse boost
    # ------------------------------------------------------------------

    def boost_reused_asset(self, workflow_id: str) -> list[dict[str, Any]]:
        """Boost importance of reused asset and linked patterns."""
        stored: list[dict[str, Any]] = []
        for item in self.memory_kernel.list_by_type(MemoryType.WORKFLOW_ASSET):
            if item.metadata.get("workflow_id") == workflow_id:
                self.memory_kernel.update(
                    item.id,
                    importance=min(item.importance + 0.1, 1.0),
                )
                stored.append({"action": "boosted_asset", "item_id": item.id})
                for related_id in item.related_ids or []:
                    related = self.memory_kernel.get(related_id)
                    if related and related.memory_type == MemoryType.WORKFLOW_PATTERN:
                        self.memory_kernel.update(
                            related.id,
                            importance=min(related.importance + 0.05, 1.0),
                        )
                        stored.append({"action": "boosted_related_pattern", "item_id": related.id})
        return stored

    # ------------------------------------------------------------------
    # 3-2: Principle prevention boost
    # ------------------------------------------------------------------

    def boost_preventing_principle(self, principle_id: str) -> list[dict[str, Any]]:
        """Boost principle that prevented a failure."""
        stored: list[dict[str, Any]] = []
        items = self.memory_kernel.list_by_type(MemoryType.PRINCIPLE)
        for item in items:
            if item.id == principle_id:
                conf = float(item.metadata.get("confidence", 0.5))
                self.memory_kernel.update(
                    item.id,
                    metadata={**item.metadata, "confidence": min(conf + 0.1, 1.0)},
                )
                stored.append({"action": "boosted_principle", "item_id": item.id})
                break
        return stored

    # ------------------------------------------------------------------
    # 3-3: Preference demotion
    # ------------------------------------------------------------------

    def demote_overridden_preference(
        self,
        preference_id: str,
        override_reason: str = "",
    ) -> list[dict[str, Any]]:
        """Demote preference when user overrides it."""
        stored: list[dict[str, Any]] = []
        items = self.memory_kernel.list_by_type(MemoryType.PREFERENCE)
        for item in items:
            if item.id == preference_id:
                self.memory_kernel.update(
                    item.id,
                    importance=max(item.importance - 0.2, 0.1),
                    metadata={
                        **item.metadata,
                        "overridden": True,
                        "override_reason": override_reason,
                    },
                )
                stored.append({"action": "demoted_preference", "item_id": item.id})
                break
        return stored

    # ------------------------------------------------------------------
    # 3-4: Fact contradiction update
    # ------------------------------------------------------------------

    def update_contradicted_fact(
        self,
        fact_id: str,
        new_content: str,
    ) -> list[dict[str, Any]]:
        """Update fact with new info, keep old in provenance."""
        stored: list[dict[str, Any]] = []
        items = self.memory_kernel.list_by_type(MemoryType.FACT)
        for item in items:
            if item.id == fact_id:
                old_content = item.content
                self.memory_kernel.update(
                    item.id,
                    content=new_content,
                    metadata={
                        **item.metadata,
                        "previous_content": old_content,
                        "contradicted_at": time.time(),
                    },
                )
                stored.append({
                    "action": "updated_fact",
                    "item_id": item.id,
                    "previous_content": old_content,
                })
                break
        return stored

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_asset_for_workflow(self, workflow_id: str) -> MemoryItem | None:
        if not workflow_id:
            return None
        for item in self.memory_kernel.list_by_type(MemoryType.WORKFLOW_ASSET):
            if item.metadata.get("workflow_id") == workflow_id:
                return item
        return None
