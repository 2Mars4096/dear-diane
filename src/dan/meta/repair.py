"""Structural Repair Engine — graduated fix escalation from prompt tweaks to redesign.

Implements Plan 19-3: classifies causal principles by severity, generates
proportional fixes (parameter swaps, graph mutations, full redesign), and
manages repair action persistence and escalation.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from enum import IntEnum
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field
from dan.workflow_generation_guidance import (
    render_workflow_generation_contract,
    workflow_generation_contract_enabled,
)

logger = logging.getLogger(__name__)

__all__ = [
    "Redesign",
    "RedesignResult",
    "NoAction",
    "ParameterFix",
    "ParameterRepairGenerator",
    "PromptFix",
    "RepairAction",
    "RepairActionRecord",
    "RepairActionStore",
    "RepairClassifier",
    "RepairEscalator",
    "RepairLevel",
    "RedesignTrigger",
    "StructuralFix",
    "StructuralRepairPlanner",
]


# ---------------------------------------------------------------------------
# RepairLevel enum
# ---------------------------------------------------------------------------


class RepairLevel(IntEnum):
    """Graduated severity levels for repair actions."""

    PROMPT = 1
    PARAMETER = 2
    STRUCTURAL = 3
    REDESIGN = 4

    @staticmethod
    def should_escalate(
        current_level: int, failure_count: int, max_attempts: int = 2,
    ) -> RepairLevel:
        """Return the next level if the current one has been exhausted."""
        if failure_count >= max_attempts and current_level < RepairLevel.REDESIGN:
            return RepairLevel(current_level + 1)
        return RepairLevel(current_level)


# ---------------------------------------------------------------------------
# RepairAction discriminated union
# ---------------------------------------------------------------------------


class PromptFix(BaseModel):
    """Level 1: prompt-level fix — route to RuleGenerator / hyperedge."""

    kind: Literal["prompt_fix"] = "prompt_fix"
    action_id: str = ""
    principle: dict[str, Any] = Field(default_factory=dict)


class ParameterFix(BaseModel):
    """Level 2: runtime parameter mutation (model/tool/config swap)."""

    kind: Literal["parameter_fix"] = "parameter_fix"
    action_id: str = ""
    mutation_plan: dict[str, Any] = Field(default_factory=dict)


class StructuralFix(BaseModel):
    """Level 3: graph topology mutation via GraphMutator."""

    kind: Literal["structural_fix"] = "structural_fix"
    action_id: str = ""
    mutation_plan: dict[str, Any] = Field(default_factory=dict)


class Redesign(BaseModel):
    """Level 4: full workflow redesign via WorkflowPlanner."""

    kind: Literal["redesign"] = "redesign"
    action_id: str = ""
    reason: str = ""


class NoAction(BaseModel):
    """Repair declined — cap reached or not actionable."""

    kind: Literal["no_action"] = "no_action"
    action_id: str = ""
    reason: str = ""


RepairAction = PromptFix | ParameterFix | StructuralFix | Redesign | NoAction


class RedesignResult(BaseModel):
    """Outcome of a level-4 full workflow redesign."""

    new_graph: dict[str, Any] = Field(default_factory=dict)
    old_workflow_id: str = ""
    reason: str = ""
    changes_summary: str = ""


# ---------------------------------------------------------------------------
# RepairActionRecord and RepairActionStore
# ---------------------------------------------------------------------------


class RepairActionRecord(BaseModel):
    """Tracks a single repair action across its lifecycle."""

    action_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    workflow_id: str = ""
    repair_level: int = RepairLevel.PROMPT
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    apply_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    status: Literal["active", "superseded", "failed"] = "active"


class RepairActionStore:
    """Persists RepairActionRecords to a MemoryStore backend."""

    GLOBAL_WORKFLOW = "_global"
    SESSION_ID = "_repairs"

    def __init__(self, memory_store: Any) -> None:
        self._store = memory_store

    async def save_action(self, record: RepairActionRecord) -> None:
        """Persist a repair action record."""
        from dan.engine.memory import MemoryEntry, MemoryScope

        entry = MemoryEntry(
            key=f"repair:{record.action_id}",
            value=record.model_dump(),
            scope=MemoryScope.GLOBAL,
        )
        await self._store.write(self.GLOBAL_WORKFLOW, self.SESSION_ID, entry)

    async def update_action(self, record: RepairActionRecord) -> None:
        """Overwrite an existing record."""
        await self.save_action(record)

    async def load_actions(self, workflow_id: str) -> list[RepairActionRecord]:
        """Load all repair actions for a workflow."""
        keys = await self._store.list_keys(self.GLOBAL_WORKFLOW, self.SESSION_ID)
        results: list[RepairActionRecord] = []
        for key in keys:
            if not key.startswith("repair:"):
                continue
            entry = await self._store.read(self.GLOBAL_WORKFLOW, self.SESSION_ID, key)
            if entry is None:
                continue
            try:
                rec = RepairActionRecord.model_validate(entry.value)
                if rec.workflow_id == workflow_id:
                    results.append(rec)
            except Exception:
                logger.warning("Skipping corrupt repair record: %s", key)
        return results

    async def get_actions_by_level(
        self, workflow_id: str, level: int,
    ) -> list[RepairActionRecord]:
        """Filter actions by workflow and repair level."""
        all_actions = await self.load_actions(workflow_id)
        return [r for r in all_actions if r.repair_level == level]

    async def load_action(self, action_id: str) -> RepairActionRecord | None:
        """Load a single repair action by ID."""
        entry = await self._store.read(
            self.GLOBAL_WORKFLOW,
            self.SESSION_ID,
            f"repair:{action_id}",
        )
        if entry is None:
            return None
        try:
            return RepairActionRecord.model_validate(entry.value)
        except Exception:
            logger.warning("Corrupt repair record %s", action_id)
            return None


# ---------------------------------------------------------------------------
# RepairClassifier
# ---------------------------------------------------------------------------

_PARAMETER_KEYWORDS = [
    "model", "temperature", "max_tokens", "timeout", "tool_id",
    "tool_config", "retry", "token_limit", "token limit",
]
_STRUCTURAL_KEYWORDS = [
    "add node", "remove node", "rewire", "validation step", "fallback",
    "error handling", "branch", "loop", "review step", "add a check",
]
_REDESIGN_KEYWORDS = [
    "fundamentally wrong", "completely different", "start over",
    "wrong approach", "redesign", "scrap", "start from scratch",
]


class RepairClassifier:
    """Classifies a CausalPrinciple into an appropriate RepairLevel."""

    def classify(
        self,
        principle: Any,
        failure_history: list[RepairActionRecord] | None = None,
        max_attempts_per_level: int = 2,
    ) -> RepairLevel:
        """Determine the repair level based on action text and escalation history."""
        repair_level_str = getattr(principle, "repair_level", "prompt_fix")

        if repair_level_str == "parameter_fix":
            base = RepairLevel.PARAMETER
        elif repair_level_str == "structural_fix":
            base = RepairLevel.STRUCTURAL
        elif repair_level_str == "redesign":
            base = RepairLevel.REDESIGN
        elif repair_level_str == "retry":
            base = RepairLevel.PROMPT
        else:
            base = self._classify_from_text(getattr(principle, "action", ""))

        if failure_history:
            level_failures = sum(
                1 for r in failure_history
                if r.repair_level == base and r.status == "failed"
            )
            base = RepairLevel.should_escalate(base, level_failures, max_attempts_per_level)

        return base

    @staticmethod
    def _classify_from_text(action_text: str) -> RepairLevel:
        lower = action_text.lower()
        if any(kw in lower for kw in _REDESIGN_KEYWORDS):
            return RepairLevel.REDESIGN
        if any(kw in lower for kw in _STRUCTURAL_KEYWORDS):
            return RepairLevel.STRUCTURAL
        if any(kw in lower for kw in _PARAMETER_KEYWORDS):
            return RepairLevel.PARAMETER
        return RepairLevel.PROMPT


# ---------------------------------------------------------------------------
# ParameterRepairGenerator
# ---------------------------------------------------------------------------

_WHITELISTED_CONFIG_KEYS = frozenset({
    "model", "temperature", "max_tokens", "timeout_seconds",
    "retry_policy", "tool_config", "tool_id",
})


class ParameterRepairGenerator:
    """Produces runtime graph parameter mutations for level-2 repairs."""

    def generate_mutation(self, principle: Any, graph: Any) -> Any:
        """Return a MutationPlan with whitelisted EditNode operations."""
        from dan.server.graph_mutator import MutationPlan

        operations: list[dict[str, Any]] = []
        suggested = getattr(principle, "suggested_parameter_changes", {})

        if suggested:
            for node_id, changes in suggested.items():
                filtered = {
                    k: v for k, v in changes.items()
                    if k in _WHITELISTED_CONFIG_KEYS
                }
                if filtered:
                    operations.append({
                        "op": "edit_node",
                        "node_id": node_id,
                        "updates": filtered,
                    })
        elif getattr(principle, "source_node_ids", None):
            inferred = self._infer_changes(getattr(principle, "action", ""))
            for node_id in principle.source_node_ids:
                if inferred:
                    operations.append({
                        "op": "edit_node",
                        "node_id": node_id,
                        "updates": dict(inferred),
                    })

        action_text = getattr(principle, "action", "")[:100]
        return MutationPlan(
            operations=operations,
            description=f"Parameter repair: {action_text}",
        )

    @staticmethod
    def _infer_changes(action_text: str) -> dict[str, Any]:
        """Heuristic: extract parameter changes from action text."""
        changes: dict[str, Any] = {}
        lower = action_text.lower()
        if "temperature" in lower:
            changes["temperature"] = 0.2
        if "max_tokens" in lower or "token limit" in lower:
            changes["max_tokens"] = 4096
        return changes


# ---------------------------------------------------------------------------
# StructuralRepairPlanner
# ---------------------------------------------------------------------------


class StructuralRepairPlanner:
    """LLM-driven structural repair: analyses graph topology and proposes mutations."""

    _BASE_SYSTEM_PROMPT = (
        "You are a workflow repair engineer. Given a principle about what went wrong "
        "and the current graph structure, produce a MutationPlan (JSON) to fix the issue.\n"
        "Output a JSON object with \"operations\" (list of mutation ops) and \"description\" (string).\n"
        "Supported operations:\n"
        "  add_node: {\"op\": \"add_node\", \"node_type\": \"...\", \"name\": \"...\", \"config\": {...}}\n"
        "  remove_node: {\"op\": \"remove_node\", \"node_id\": \"...\"}\n"
        "  edit_node: {\"op\": \"edit_node\", \"node_id\": \"...\", \"updates\": {...}}\n"
        "  add_edge: {\"op\": \"add_edge\", \"edge_type\": \"data\", \"source_id\": \"...\", "
        "\"source_port\": \"output\", \"target_id\": \"...\", \"target_port\": \"input\"}\n"
        "  remove_edge: {\"op\": \"remove_edge\", \"source_id\": \"...\", "
        "\"source_port\": \"...\", \"target_id\": \"...\", \"target_port\": \"...\"}\n"
    )

    @classmethod
    def _system_prompt(cls) -> str:
        prompt = cls._BASE_SYSTEM_PROMPT
        if workflow_generation_contract_enabled():
            prompt = (
                f"{prompt}"
                f"{render_workflow_generation_contract('repair', tools_available=False)}\n"
            )
        return f"{prompt}Output ONLY valid JSON."

    def __init__(
        self,
        llm_call: Callable[..., Awaitable[str]] | None = None,
        model: str | None = None,
        max_retries: int = 2,
    ) -> None:
        self._llm_call = llm_call
        self._model = model
        self._max_retries = max_retries

    async def plan_repair(
        self,
        principle: Any,
        graph: Any,
        error_context: str = "",
    ) -> Any | None:
        """Analyse principle + graph and produce a MutationPlan, or None on failure."""
        if not self._llm_call:
            logger.warning("No LLM callable configured; cannot plan structural repair")
            return None

        user_prompt = self._build_user_prompt(principle, graph, error_context)

        for _attempt in range(self._max_retries + 1):
            raw = await self._llm_call(
                self._system_prompt(), user_prompt, self._model, 0.3,
            )
            plan = self._parse_mutation_plan(raw)
            if plan is None:
                continue

            from dan.server.graph_mutator import GraphMutator
            graph_dict = graph.model_dump(mode="json")
            mutator = GraphMutator()
            result = mutator.dry_run(graph_dict, plan)
            if not result.errors:
                return plan
            user_prompt += f"\n\nPrevious attempt failed: {[e.message for e in result.errors]}. Please fix."

        return None

    @staticmethod
    def _build_user_prompt(principle: Any, graph: Any, error_context: str) -> str:
        nodes = getattr(graph, "nodes", [])
        edges = getattr(graph, "edges", [])
        node_summary = ", ".join(f"{n.name}({n.node_type})" for n in nodes)
        edge_summary = ", ".join(f"{e.source_node_id}->{e.target_node_id}" for e in edges)

        condition = getattr(principle, "condition", "")
        action = getattr(principle, "action", "")
        reason = getattr(principle, "reason", "")
        structural = getattr(principle, "structural_description", "")

        return (
            f"Principle: {condition} → {action}\n"
            f"Reason: {reason}\n"
            f"Structural description: {structural}\n\n"
            f"Graph nodes: {node_summary}\n"
            f"Graph edges: {edge_summary}\n\n"
            f"Error context: {error_context}\n\n"
            "Produce a MutationPlan to fix this issue."
        )

    @staticmethod
    def _parse_mutation_plan(raw: str) -> Any | None:
        """Extract JSON from LLM output and parse as MutationPlan."""
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        text = match.group(1) if match else raw.strip()
        try:
            data = json.loads(text)
            from dan.server.graph_mutator import MutationPlan
            return MutationPlan.model_validate(data)
        except (json.JSONDecodeError, Exception):
            logger.warning("Failed to parse structural repair plan from LLM output")
            return None


# ---------------------------------------------------------------------------
# RedesignTrigger
# ---------------------------------------------------------------------------


class RedesignTrigger:
    """Determines when structural repairs are exhausted and a full redesign is needed."""

    def __init__(self, max_structural_failures: int = 3) -> None:
        self._max_structural_failures = max_structural_failures

    def should_redesign(
        self, workflow_id: str, repair_history: list[RepairActionRecord],
    ) -> bool:
        """Return True when the workflow should be redesigned from scratch."""
        structural = [
            r for r in repair_history
            if r.repair_level == RepairLevel.STRUCTURAL
        ]
        failed_structural = [r for r in structural if r.status == "failed"]
        if len(failed_structural) >= self._max_structural_failures:
            return True
        if len(repair_history) >= 3:
            recent = repair_history[-3:]
            if all(r.status == "failed" for r in recent):
                return True
        return False


# ---------------------------------------------------------------------------
# RepairEscalator
# ---------------------------------------------------------------------------


class RepairEscalator:
    """Graduated repair: classify → check history → dispatch appropriate fix."""

    def __init__(
        self,
        classifier: RepairClassifier | None = None,
        param_generator: ParameterRepairGenerator | None = None,
        structural_planner: StructuralRepairPlanner | None = None,
        redesign_trigger: RedesignTrigger | None = None,
        action_store: RepairActionStore | None = None,
        max_attempts_per_level: int = 2,
        max_redesigns: int = 2,
    ) -> None:
        self._classifier = classifier or RepairClassifier()
        self._param_gen = param_generator or ParameterRepairGenerator()
        self._structural = structural_planner
        self._redesign = redesign_trigger or RedesignTrigger()
        self._store = action_store
        self._max_attempts = max_attempts_per_level
        self._max_redesigns = max_redesigns

    @staticmethod
    def _level_for_action(action: RepairAction) -> int:
        if isinstance(action, PromptFix):
            return int(RepairLevel.PROMPT)
        if isinstance(action, ParameterFix):
            return int(RepairLevel.PARAMETER)
        if isinstance(action, StructuralFix):
            return int(RepairLevel.STRUCTURAL)
        if isinstance(action, Redesign):
            return int(RepairLevel.REDESIGN)
        return int(RepairLevel.PROMPT)

    @staticmethod
    def _payload_for_action(action: RepairAction) -> dict[str, Any]:
        if isinstance(action, PromptFix):
            return {"principle": action.principle}
        if isinstance(action, ParameterFix):
            return {"mutation_plan": action.mutation_plan}
        if isinstance(action, StructuralFix):
            return {"mutation_plan": action.mutation_plan}
        if isinstance(action, Redesign):
            return {"reason": action.reason}
        if isinstance(action, NoAction):
            return {"reason": action.reason}
        return {}

    async def _persist_action(
        self,
        workflow_id: str,
        action: RepairAction,
    ) -> RepairAction:
        if self._store is None:
            return action
        if isinstance(action, NoAction):
            return action

        record = RepairActionRecord(
            workflow_id=workflow_id,
            repair_level=self._level_for_action(action),
            payload=self._payload_for_action(action),
            apply_count=1,
            status="active",
        )
        await self._store.save_action(record)
        return action.model_copy(update={"action_id": record.action_id})

    async def record_outcome(self, workflow_id: str, action_id: str, success: bool) -> None:
        """Record whether a previously emitted repair action helped."""
        if self._store is None or not action_id:
            return
        record = await self._store.load_action(action_id)
        if record is None:
            return
        if record.workflow_id != workflow_id:
            return

        updates = {
            "success_count": record.success_count + (1 if success else 0),
            "failure_count": record.failure_count + (0 if success else 1),
            "status": "active" if success else "failed",
        }
        await self._store.update_action(record.model_copy(update=updates))

    async def repair(
        self,
        principle: Any,
        graph: Any,
        error_context: str = "",
    ) -> RepairAction:
        """Main entry: classify, check history, dispatch to the proportional fix."""
        workflow_id = getattr(principle, "workflow_id", "")
        history: list[RepairActionRecord] = []
        if self._store:
            history = await self._store.load_actions(workflow_id)

        level = self._classifier.classify(principle, history, self._max_attempts)

        redesign_count = sum(
            1 for r in history if r.repair_level == RepairLevel.REDESIGN
        )
        if level == RepairLevel.REDESIGN and redesign_count >= self._max_redesigns:
            return NoAction(
                reason=f"Max redesigns ({self._max_redesigns}) reached for {workflow_id}",
            )

        if level == RepairLevel.PROMPT:
            return await self._persist_action(
                workflow_id,
                PromptFix(principle=principle.model_dump()),
            )

        if level == RepairLevel.PARAMETER:
            plan = self._param_gen.generate_mutation(principle, graph)
            return await self._persist_action(
                workflow_id,
                ParameterFix(mutation_plan=plan.model_dump()),
            )

        if level == RepairLevel.STRUCTURAL:
            if not self._structural:
                return NoAction(reason="Structural repair planner not configured")
            plan = await self._structural.plan_repair(principle, graph, error_context)
            if plan is None:
                return await self._persist_action(
                    workflow_id,
                    Redesign(reason="Structural repair planning failed — escalating"),
                )
            return await self._persist_action(
                workflow_id,
                StructuralFix(mutation_plan=plan.model_dump()),
            )

        if level == RepairLevel.REDESIGN:
            return await self._persist_action(
                workflow_id,
                Redesign(
                    reason=f"Escalated to redesign: {getattr(principle, 'action', '')[:200]}",
                ),
            )

        return NoAction(reason="Unhandled repair level")
