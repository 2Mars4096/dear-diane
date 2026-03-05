"""System Architect — multi-workflow system decomposition and coordination.

Implements Plan 19-7: sits above WorkflowPlanner to classify intent
complexity, decompose high-level goals into coordinated multi-workflow
systems with shared memory, cross-cutting tools/skills, and routing.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections import deque
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from pydantic import BaseModel, Field

from dan.meta.discovery import DiscoveryResult, DiscoveryService
from dan.meta.planner import PlannerOutput, WorkflowPlanner

if TYPE_CHECKING:
    from dan.meta.authoring import SkillSpec, ToolSpec

logger = logging.getLogger(__name__)

__all__ = [
    "RoutingConfig",
    "SystemArchitect",
    "SystemManifest",
    "SystemPlan",
    "SystemValidationResult",
    "WorkflowSpec",
]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class WorkflowSpec(BaseModel):
    """Specification for a single workflow within a system."""

    name: str
    goal: str
    inputs: list[dict[str, str]] = Field(default_factory=list)
    outputs: list[dict[str, str]] = Field(default_factory=list)
    shared_state_keys: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    trigger: str = "manual"
    depends_on: list[str] = Field(default_factory=list)


class RoutingConfig(BaseModel):
    """Configuration for intent-based routing across workflows."""

    strategy: str = "intent_classifier"
    intent_model: str = ""
    fallback_workflow: str = ""
    intent_map: dict[str, str] = Field(default_factory=dict)


class SystemPlan(BaseModel):
    """A coordinated multi-workflow system plan."""

    name: str = ""
    description: str = ""
    workflows: list[WorkflowSpec] = Field(default_factory=list)
    shared_tools: list[dict[str, Any]] = Field(default_factory=list)
    shared_skills: list[dict[str, Any]] = Field(default_factory=list)
    shared_memory_keys: list[str] = Field(default_factory=list)
    routing_config: RoutingConfig | None = None


class SystemManifest(BaseModel):
    """Describes a fully assembled system — generated after planning + wiring."""

    plan: SystemPlan
    workflow_ids: list[str] = Field(default_factory=list)
    created_tools: list[str] = Field(default_factory=list)
    created_skills: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)


class SystemValidationResult(BaseModel):
    """Result of validating a system plan."""

    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_CLASSIFY_SYSTEM_PROMPT = """\
You are a workflow system architect. Given a user's goal, determine if it requires:
- "single": One workflow can accomplish it
- "multi": Multiple cooperating workflows are needed

Indicators for "multi":
- Multiple distinct processing stages with different inputs/outputs
- Need for persistent shared state across independent processes
- Different triggers (some manual, some event-based, some scheduled)
- Intent-based routing to different specialized handlers

Output ONLY "single" or "multi"."""

_DECOMPOSE_SYSTEM_PROMPT = """\
You are a system architect for DAN (Deep Agent Network). Decompose the user's \
goal into a system of cooperating workflows.

Output a JSON object with this schema:
{
  "name": "system name",
  "description": "what this system does",
  "workflows": [
    {
      "name": "workflow_name",
      "goal": "what this workflow does",
      "inputs": [{"name": "...", "type": "string|number|object|array"}],
      "outputs": [{"name": "...", "type": "..."}],
      "shared_state_keys": ["key1", "key2"],
      "required_tools": ["tool_id1"],
      "required_skills": ["skill_name"],
      "trigger": "manual|schedule|event|upstream-output",
      "depends_on": ["other_workflow_name"]
    }
  ],
  "shared_memory_keys": ["all shared keys"],
  "routing_config": null or {
    "strategy": "intent_classifier",
    "intent_map": {"intent": "workflow_name"},
    "fallback_workflow": "..."
  }
}

Output ONLY valid JSON. No markdown fences, no explanation."""


# ---------------------------------------------------------------------------
# SystemArchitect
# ---------------------------------------------------------------------------


class SystemArchitect:
    """Decomposes complex intents into coordinated multi-workflow systems."""

    def __init__(
        self,
        planner: WorkflowPlanner | None = None,
        discovery: DiscoveryService | None = None,
        llm_call: Callable[..., Awaitable[str]] | None = None,
        model: str | None = None,
    ) -> None:
        self._planner = planner
        self._discovery = discovery
        self._llm_call = llm_call
        self._model = model

    # -- Public API --------------------------------------------------------

    async def classify(self, goal: str) -> str:
        """Determine if goal needs ``single`` workflow or ``multi`` workflow system."""
        raw = await self._call_llm(_CLASSIFY_SYSTEM_PROMPT, goal)
        label = raw.strip().lower()
        if "multi" in label:
            return "multi"
        return "single"

    async def decompose(
        self,
        goal: str,
        context: DiscoveryResult | None = None,
    ) -> SystemPlan:
        """LLM decomposes intent into a :class:`SystemPlan`."""
        user_sections = [f"## Goal\n{goal}"]
        if context:
            if context.tools:
                user_sections.append(
                    "## Available Tools\n"
                    + ", ".join(t.tool_id for t in context.tools)
                )
            if context.skills:
                user_sections.append(
                    "## Available Skills\n"
                    + ", ".join(s.name for s in context.skills)
                )
        user_prompt = "\n\n".join(user_sections)

        raw = await self._call_llm(_DECOMPOSE_SYSTEM_PROMPT, user_prompt)
        data = self._parse_json(raw)
        return self._parse_system_plan(data)

    async def plan(
        self,
        goal: str,
        context: DiscoveryResult | None = None,
    ) -> SystemPlan | PlannerOutput:
        """Main entry: classify -> decompose if multi, or delegate to planner if single."""
        complexity = await self.classify(goal)
        if complexity == "single":
            if self._planner is None:
                raise RuntimeError("No WorkflowPlanner configured for single-workflow goals")
            return await self._planner.plan(goal)

        if context is None and self._discovery is not None:
            context = await self._discovery.discover_all(goal)

        system_plan = await self.decompose(goal, context)
        validation = self.validate_system(system_plan)
        if validation.warnings:
            logger.warning(
                "System plan warnings for %r: %s",
                system_plan.name,
                "; ".join(validation.warnings),
            )
        if validation.errors:
            logger.error(
                "System plan errors for %r: %s",
                system_plan.name,
                "; ".join(validation.errors),
            )
        return system_plan

    def validate_system(self, plan: SystemPlan) -> SystemValidationResult:
        """Validate system plan for structural correctness."""
        errors: list[str] = []
        warnings: list[str] = []

        wf_names = {wf.name for wf in plan.workflows}

        if not plan.workflows:
            errors.append("System plan has no workflows")
            return SystemValidationResult(valid=False, errors=errors, warnings=warnings)

        # --- Interface compatibility: upstream-output triggers ----------------
        wf_by_name = {wf.name: wf for wf in plan.workflows}
        for wf in plan.workflows:
            if wf.trigger != "upstream-output":
                continue
            if not wf.depends_on:
                warnings.append(
                    f"Workflow '{wf.name}' has trigger=upstream-output but no depends_on"
                )
                continue
            for dep_name in wf.depends_on:
                upstream = wf_by_name.get(dep_name)
                if upstream is None:
                    errors.append(
                        f"Workflow '{wf.name}' depends on '{dep_name}' which does not exist"
                    )
                    continue
                upstream_output_names = {o.get("name", "") for o in upstream.outputs}
                this_input_names = {i.get("name", "") for i in wf.inputs}
                if this_input_names and upstream_output_names and not (
                    this_input_names & upstream_output_names
                ):
                    warnings.append(
                        f"Workflow '{wf.name}' inputs {sorted(this_input_names)} have no "
                        f"overlap with upstream '{dep_name}' outputs "
                        f"{sorted(upstream_output_names)}"
                    )

        # --- Memory key consistency ------------------------------------------
        all_shared_keys: set[str] = set()
        for wf in plan.workflows:
            all_shared_keys.update(wf.shared_state_keys)
        undeclared = all_shared_keys - set(plan.shared_memory_keys)
        if undeclared:
            warnings.append(
                f"Shared state keys used by workflows but not in "
                f"shared_memory_keys: {sorted(undeclared)}"
            )

        # --- Circular dependency detection (Kahn's algorithm) ----------------
        cycle_error = self._detect_cycles(plan.workflows, wf_names)
        if cycle_error:
            errors.append(cycle_error)

        # --- depends_on references exist -------------------------------------
        for wf in plan.workflows:
            for dep in wf.depends_on:
                if dep not in wf_names:
                    errors.append(
                        f"Workflow '{wf.name}' depends_on unknown workflow '{dep}'"
                    )

        # --- Tool availability -----------------------------------------------
        shared_tool_ids = {
            t.get("tool_id", t.get("name", "")) for t in plan.shared_tools
        }
        for wf in plan.workflows:
            for tool_id in wf.required_tools:
                if tool_id not in shared_tool_ids:
                    warnings.append(
                        f"Workflow '{wf.name}' requires tool '{tool_id}' not in shared_tools"
                    )

        valid = len(errors) == 0
        return SystemValidationResult(valid=valid, errors=errors, warnings=warnings)

    async def build_system(self, plan: SystemPlan) -> SystemManifest:
        """Plan each workflow, create shared tools/skills, wire the system, validate."""
        validation = self.validate_system(plan)
        manifest = SystemManifest(
            plan=plan,
            validation_errors=validation.errors,
        )

        if not self._planner:
            manifest.validation_errors.append("No WorkflowPlanner configured — cannot build workflows")
            return manifest

        for spec in plan.workflows:
            try:
                planner_output = await self._planner.plan(spec.goal)
                wf_id = f"sys-{uuid.uuid4().hex[:10]}"
                manifest.workflow_ids.append(wf_id)
                logger.info(
                    "Planned workflow %r -> %s (action=%s)",
                    spec.name,
                    wf_id,
                    planner_output.plan.action,
                )
            except Exception as exc:
                logger.error("Failed to plan workflow %r: %s", spec.name, exc)
                manifest.validation_errors.append(
                    f"Failed to plan workflow '{spec.name}': {exc}"
                )

        return manifest

    # -- Internal helpers --------------------------------------------------

    async def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        if self._llm_call is None:
            raise RuntimeError("No LLM callable configured for SystemArchitect")
        return await self._llm_call(system_prompt, user_prompt, self._model, 0.3)

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        """Extract a JSON object from potentially fenced LLM output."""
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        text = match.group(1) if match else raw.strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON from LLM: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"Expected JSON object, got {type(data).__name__}")
        return data

    @staticmethod
    def _parse_system_plan(data: dict[str, Any]) -> SystemPlan:
        """Parse raw dict into a validated :class:`SystemPlan`."""
        workflows: list[WorkflowSpec] = []
        for wf_raw in data.get("workflows", []):
            if isinstance(wf_raw, dict):
                workflows.append(WorkflowSpec.model_validate(wf_raw))

        routing_raw = data.get("routing_config")
        routing: RoutingConfig | None = None
        if isinstance(routing_raw, dict):
            routing = RoutingConfig.model_validate(routing_raw)

        return SystemPlan(
            name=data.get("name", ""),
            description=data.get("description", ""),
            workflows=workflows,
            shared_tools=data.get("shared_tools", []),
            shared_skills=data.get("shared_skills", []),
            shared_memory_keys=data.get("shared_memory_keys", []),
            routing_config=routing,
        )

    @staticmethod
    def _detect_cycles(workflows: list[WorkflowSpec], wf_names: set[str]) -> str | None:
        """Kahn's algorithm — returns an error message if cycles exist, else None."""
        in_degree: dict[str, int] = {name: 0 for name in wf_names}
        adjacency: dict[str, list[str]] = {name: [] for name in wf_names}

        for wf in workflows:
            for dep in wf.depends_on:
                if dep not in wf_names:
                    continue
                adjacency[dep].append(wf.name)
                in_degree[wf.name] = in_degree.get(wf.name, 0) + 1

        queue: deque[str] = deque(n for n, d in in_degree.items() if d == 0)
        visited = 0
        while queue:
            node = queue.popleft()
            visited += 1
            for neighbor in adjacency[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if visited < len(wf_names):
            remaining = sorted(n for n, d in in_degree.items() if d > 0)
            return f"Circular dependency detected among workflows: {remaining}"
        return None
