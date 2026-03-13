"""Autonomous Execution Controller — the outer meta-orchestration loop.

Implements Plan 19-4: receives a high-level goal and drives it to
completion through iterative planning, execution, diagnosis, and
graduated repair, with human override at any checkpoint.
"""

from __future__ import annotations

import logging
import time
import uuid
from enum import Enum
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field

from .goal_contract import normalize_goal_contract

logger = logging.getLogger(__name__)

__all__ = [
    "HumanOverride",
    "MetaController",
    "MetaControllerConfig",
    "MetaSession",
    "MetaSessionStatus",
    "MetaSessionStore",
    "OverrideType",
]


# ---------------------------------------------------------------------------
# Session status & models
# ---------------------------------------------------------------------------


class MetaSessionStatus(str, Enum):
    PLANNING = "planning"
    EXECUTING = "executing"
    DIAGNOSING = "diagnosing"
    REPAIRING = "repairing"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class MetaSession(BaseModel):
    """Full state of a meta-orchestration session."""

    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    goal: str
    status: MetaSessionStatus = MetaSessionStatus.PLANNING
    plan_result: dict[str, Any] | None = None
    workflow_id: str | None = None
    run_history: list[str] = Field(default_factory=list)
    repair_history: list[dict[str, Any]] = Field(default_factory=list)
    pending_repair_action_ids: list[str] = Field(default_factory=list)
    iteration: int = 0
    max_iterations: int = 5
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    paused_at_stage: str | None = None
    pause_requested: bool = False
    human_override: list[dict[str, Any]] = Field(default_factory=list)
    error_context: str | None = None
    final_output: dict[str, Any] | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)

    # Multi-workflow system fields (19-7)
    system_plan: dict[str, Any] | None = None
    workflow_ids: list[str] = Field(default_factory=list)
    run_ids: list[str] = Field(default_factory=list)
    is_system: bool = False
    system_manifest: dict[str, Any] | None = None

    # Concierge goal context (29-4): adapt_workflow_id, memory_context, etc.
    goal_context: dict[str, Any] = Field(default_factory=dict)


class MetaControllerConfig(BaseModel):
    """Tuning knobs for the controller loop."""

    max_iterations: int = 5
    auto_approve_levels: list[int] = Field(default_factory=lambda: [1, 2])
    pause_before_execute: bool = False
    pause_before_repair: bool = False
    pause_on_redesign: bool = True
    timeout_seconds: float | None = None

    # Authoring configuration (19-6)
    enable_tool_authoring: bool = False
    enable_skill_authoring: bool = False
    require_authoring_approval: bool = True
    custom_tools_dir: str = ""
    custom_skills_dir: str = ""


# ---------------------------------------------------------------------------
# Human override protocol
# ---------------------------------------------------------------------------


class OverrideType(str, Enum):
    MODIFY_PLAN = "modify_plan"
    MODIFY_GRAPH = "modify_graph"
    SKIP_REPAIR = "skip_repair"
    ABORT = "abort"
    ACCEPT_AS_IS = "accept_as_is"


class HumanOverride(BaseModel):
    """A human-provided intervention at a checkpoint."""

    override_type: OverrideType
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: float = Field(default_factory=time.time)


# ---------------------------------------------------------------------------
# MetaSessionStore
# ---------------------------------------------------------------------------


class MetaSessionStore:
    """Persists MetaSession objects to a MemoryStore backend."""

    GLOBAL_WORKFLOW = "_global"
    SESSION_ID = "_meta_sessions"

    def __init__(self, memory_store: Any) -> None:
        self._store = memory_store

    async def save(self, session: MetaSession) -> None:
        """Persist a meta session."""
        from dan.engine.memory import MemoryEntry, MemoryScope

        entry = MemoryEntry(
            key=f"meta_session:{session.session_id}",
            value=session.model_dump(),
            scope=MemoryScope.GLOBAL,
        )
        await self._store.write(self.GLOBAL_WORKFLOW, self.SESSION_ID, entry)

    async def load(self, session_id: str) -> MetaSession | None:
        """Load a session by ID."""
        entry = await self._store.read(
            self.GLOBAL_WORKFLOW,
            self.SESSION_ID,
            f"meta_session:{session_id}",
        )
        if entry is None:
            return None
        return MetaSession.model_validate(entry.value)

    async def list_sessions(self) -> list[MetaSession]:
        """Return all stored sessions."""
        keys = await self._store.list_keys(self.GLOBAL_WORKFLOW, self.SESSION_ID)
        results: list[MetaSession] = []
        for key in keys:
            if not key.startswith("meta_session:"):
                continue
            entry = await self._store.read(self.GLOBAL_WORKFLOW, self.SESSION_ID, key)
            if entry is None:
                continue
            try:
                results.append(MetaSession.model_validate(entry.value))
            except Exception:
                logger.warning("Skipping corrupt meta session: %s", key)
        return results

    async def delete(self, session_id: str) -> bool:
        """Remove a session."""
        return await self._store.delete(
            self.GLOBAL_WORKFLOW,
            self.SESSION_ID,
            f"meta_session:{session_id}",
        )


# ---------------------------------------------------------------------------
# MetaController
# ---------------------------------------------------------------------------


class MetaController:
    """Outer meta-loop: goal → plan → execute → diagnose → repair → loop.

    **Deprecated (29-2):** Prefer the concierge with memory_kernel, which owns
    ConciergeGoal state and calls run_session internally. This controller remains
    for backward compatibility and as the execution backend used by the concierge.
    """

    def __init__(
        self,
        planner: Any | None = None,
        repair_escalator: Any | None = None,
        experience_store: Any | None = None,
        session_store: MetaSessionStore | None = None,
        run_workflow: Callable[..., Awaitable[dict[str, Any]]] | None = None,
        emit_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        graph_loader: Callable[[str], dict[str, Any] | None] | None = None,
        graph_saver: Callable[[str, dict[str, Any]], None] | None = None,
        architect: Any | None = None,
        runtime_author: Any | None = None,
    ) -> None:
        self._planner = planner
        self._repair = repair_escalator
        self._experience = experience_store
        self._session_store = session_store
        self._run_workflow = run_workflow
        self._emit_event = emit_event
        self._graph_loader = graph_loader
        self._graph_saver = graph_saver
        self._architect = architect
        self._runtime_author = runtime_author

    async def create_session(
        self,
        goal: str,
        config: MetaControllerConfig | None = None,
    ) -> MetaSession:
        """Create and persist a fresh meta session."""
        cfg = config or MetaControllerConfig()
        session = MetaSession(goal=goal, max_iterations=cfg.max_iterations)
        await self._save_session(session)
        await self._emit("META_SESSION_STARTED", session, {"goal": goal})
        return session

    async def create_session_for_goal(self, goal: Any, config: MetaControllerConfig | None = None) -> MetaSession:
        """Create and persist a MetaSession from a ConciergeGoal (29-2 legacy path)."""
        cfg = config or MetaControllerConfig(max_iterations=goal.max_iterations)
        session = MetaSession(
            session_id=goal.id,
            goal=goal.description,
            status=MetaSessionStatus.PLANNING,
            max_iterations=cfg.max_iterations,
            plan_result=goal.plan_result,
            workflow_id=goal.workflow_id,
            run_history=list(goal.run_history),
            iteration=goal.iteration,
            goal_context=dict(getattr(goal, "context", {}) or {}),
        )
        await self._save_session(session)
        await self._emit("META_SESSION_STARTED", session, {"goal": goal.description})
        return session

    async def run(
        self,
        goal: str,
        config: MetaControllerConfig | None = None,
    ) -> MetaSession:
        """Create and run a new meta session to completion or pause."""
        cfg = config or MetaControllerConfig()
        session = await self.create_session(goal, cfg)
        return await self.run_session(session, cfg)

    async def run_system(
        self,
        goal: str,
        config: MetaControllerConfig | None = None,
    ) -> MetaSession:
        """Create and run a multi-workflow system session.

        Uses SystemArchitect to decompose the goal, then plans and executes
        each workflow in dependency order.
        """
        cfg = config or MetaControllerConfig()
        session = await self.create_session(goal, cfg)
        session.is_system = True

        if self._architect is not None:
            try:
                system_plan = await self._architect.plan(goal)

                from dan.meta.architect import SystemPlan

                if isinstance(system_plan, SystemPlan):
                    session.system_plan = system_plan.model_dump()
                    session.status = MetaSessionStatus.EXECUTING
                    await self._save_session(session)

                    manifest = await self._execute_system(session, system_plan, cfg)
                    session.system_manifest = manifest.model_dump()
                    session.status = MetaSessionStatus.COMPLETED
                else:
                    session.is_system = False
                    return await self.run_session(session, cfg)
            except Exception as exc:
                session.status = MetaSessionStatus.FAILED
                session.error_context = str(exc)
                logger.error("System execution failed: %s", exc, exc_info=True)
        else:
            session.is_system = False
            return await self.run_session(session, cfg)

        await self._save_session(session)
        return session

    async def _execute_system(
        self,
        session: MetaSession,
        plan: Any,
        config: MetaControllerConfig,
    ) -> Any:
        """Execute workflows in dependency order."""
        from dan.meta.architect import SystemManifest, SystemPlan

        if not isinstance(plan, SystemPlan):
            plan = SystemPlan.model_validate(plan)

        ordered = self._topo_sort_workflows(plan.workflows)

        built_ids: list[str] = []
        run_ids: list[str] = []
        handoffs: dict[str, Any] = {}

        for spec in ordered:
            await self._emit("META_WORKFLOW_PLANNING", session, {"workflow": spec.name})

            if self._planner:
                plan_context_extra: dict[str, Any] = {}
                _deps = getattr(spec, 'depends_on', None) or []
                if _deps:
                    _upstream = {d: handoffs[d] for d in _deps if d in handoffs}
                    if _upstream:
                        _sections = []
                        _per = max(200, 1500 // len(_upstream))
                        for _dn, _dh in _upstream.items():
                            _sections.append(_dh.to_prompt_context(budget=_per))
                        plan_context_extra["upstream_handoffs"] = "## Upstream Results\n" + "\n".join(_sections)
                if spec.required_tools:
                    plan_context_extra["required_tools"] = spec.required_tools
                if spec.required_skills:
                    plan_context_extra["required_skills"] = spec.required_skills
                if spec.inputs:
                    plan_context_extra["inputs"] = spec.inputs
                if spec.outputs:
                    plan_context_extra["outputs"] = spec.outputs
                plan_context = self._build_plan_context(session, plan_context_extra)
                plan_output = await self._planner.plan(
                    spec.goal, plan_context=plan_context
                )
                if plan_output.review.valid:
                    graph_data = await self._planner.execute_plan(
                        plan_output.plan, domain=None, user_text=spec.goal,
                    )
                    wf_id = graph_data.get("workflow_id", spec.name) if isinstance(graph_data, dict) else spec.name
                    built_ids.append(wf_id)
                    session.workflow_ids.append(wf_id)

                    _wf_result: dict[str, Any] | None = None
                    if self._run_workflow:
                        try:
                            result = await self._run_workflow(
                                plan_output.plan, session.session_id
                            )
                            run_id = result.get("run_id", "") if isinstance(result, dict) else ""
                            run_ids.append(run_id)
                            session.run_ids.append(run_id)
                            _wf_result = result if isinstance(result, dict) else {"status": "completed"}
                        except Exception as exc:
                            logger.warning("Workflow %s execution failed: %s", spec.name, exc)
                            run_ids.append("")
                            _wf_result = {"status": "failed", "errors": [str(exc)]}
                    try:
                        from dan.server.concierge.boundary_handoff import WorkflowDepAssembler
                        _handoff = WorkflowDepAssembler.assemble(
                            _wf_result or {"status": "completed"}, spec.name,
                        )
                        handoffs[spec.name] = _handoff
                    except Exception:
                        logger.debug("Workflow handoff assembly failed for %s", spec.name, exc_info=True)

        return SystemManifest(
            plan=plan,
            workflow_ids=built_ids,
        )

    @staticmethod
    def _topo_sort_workflows(workflows: list[Any]) -> list[Any]:
        from dan.meta.utils import topo_sort_workflows
        return topo_sort_workflows(workflows)

    async def _enrich_goal_with_prior_experience(
        self,
        session: MetaSession,
    ) -> None:
        """Query ExperienceIndex for similar past sessions and inject context."""
        if not self._experience:
            return
        try:
            from dan.engine.experience import ExperienceIndex

            index = getattr(self._experience, "_index", None)
            if index is None:
                return
            hits = await index.search_similar(session.goal, top_k=3)
            if not hits:
                return

            prior_lines: list[str] = []
            for wf_id, score in hits:
                if score < 0.3:
                    continue
                exp = await self._experience.load_experience(wf_id)
                if exp is None:
                    continue
                line = f"- {exp.name} (workflow={wf_id}, runs={exp.run_count}, " \
                       f"success_rate={exp.success_count}/{exp.run_count})"
                if exp.failure_patterns:
                    line += f" failures: {exp.failure_patterns[:3]}"
                prior_lines.append(line)

            if prior_lines:
                prior_context = "Prior similar workflows:\n" + "\n".join(prior_lines)
                session.error_context = (
                    (session.error_context + "\n\n" + prior_context)
                    if session.error_context
                    else prior_context
                )
        except Exception:
            logger.debug("Cross-session learning lookup failed", exc_info=True)

    async def run_session(
        self,
        session: MetaSession,
        config: MetaControllerConfig | None = None,
        resume_stage: str | None = None,
    ) -> MetaSession:
        """Continue executing an existing session."""
        cfg = config or MetaControllerConfig(max_iterations=session.max_iterations)
        session.max_iterations = max(session.max_iterations, cfg.max_iterations)
        start_time = time.time()
        reexecute_current_plan = bool(
            resume_stage in {"before_execute", "before_repair", "before_redesign"}
            and session.plan_result is not None
        )

        if session.iteration == 0 and resume_stage is None:
            await self._enrich_goal_with_prior_experience(session)

        try:
            while session.iteration < session.max_iterations:
                session.iteration += 1
                session.updated_at = time.time()

                if cfg.timeout_seconds and (time.time() - start_time) > cfg.timeout_seconds:
                    await self._record_pending_repair_outcomes(session, success=False)
                    session.status = MetaSessionStatus.FAILED
                    session.error_context = "Timeout exceeded"
                    break

                if await self._checkpoint_pause(session, "before_planning"):
                    return session

                plan_obj: Any | None = None
                if not reexecute_current_plan:
                    session.status = MetaSessionStatus.PLANNING
                    await self._save_session(session)

                    if not self._planner:
                        session.status = MetaSessionStatus.FAILED
                        session.error_context = "No planner configured"
                        break

                    plan_context = self._build_plan_context(session)
                    planner_output = await self._planner.plan(
                        session.goal, session.error_context, plan_context=plan_context
                    )
                    session.plan_result = planner_output.plan.model_dump()
                    await self._emit("META_PLAN_CREATED", session, {
                        "plan": session.plan_result,
                        "iteration": session.iteration,
                    })
                    plan_obj = planner_output.plan
                else:
                    plan_obj = self._plan_from_dict(session.plan_result)
                    if plan_obj is None:
                        reexecute_current_plan = False
                        continue

                if cfg.pause_before_execute:
                    session.pause_requested = True
                if await self._checkpoint_pause(session, "before_execute"):
                    return session

                session.status = MetaSessionStatus.EXECUTING
                await self._save_session(session)
                await self._emit("META_EXECUTION_STARTED", session, {
                    "iteration": session.iteration,
                })

                exec_result = await self._execute_plan(session, plan_obj)
                if exec_result is None:
                    await self._record_pending_repair_outcomes(session, success=False)
                    session.status = MetaSessionStatus.FAILED
                    session.error_context = "Execution returned no result"
                    break

                run_id = str(exec_result.get("run_id", ""))
                if run_id:
                    session.run_history.append(run_id)
                workflow_id = exec_result.get("workflow_id")
                if workflow_id:
                    session.workflow_id = str(workflow_id)

                success = bool(exec_result.get("success", False))
                await self._record_pending_repair_outcomes(session, success=success)

                if success:
                    session.status = MetaSessionStatus.COMPLETED
                    session.final_output = exec_result
                    await self._emit("META_SESSION_COMPLETED", session, {
                        "iterations": session.iteration,
                    })
                    await self._update_experience(session)
                    break

                session.status = MetaSessionStatus.DIAGNOSING
                session.error_context = exec_result.get(
                    "error_context",
                    str(exec_result.get("errors", "")),
                )
                await self._save_session(session)
                await self._emit("META_DIAGNOSIS_STARTED", session, {
                    "error": session.error_context,
                })

                principles = exec_result.get("principles", [])
                if not principles and not self._repair:
                    session.status = MetaSessionStatus.FAILED
                    session.error_context = "No principles and no repair engine"
                    break

                if cfg.pause_before_repair:
                    session.pause_requested = True
                if await self._checkpoint_pause(session, "before_repair"):
                    return session

                session.status = MetaSessionStatus.REPAIRING
                await self._save_session(session)

                outcome = await self._apply_repairs(session, principles, cfg)
                if outcome is None:
                    return session
                reexecute_current_plan = outcome == "reexecute"

            else:
                await self._record_pending_repair_outcomes(session, success=False)
                session.status = MetaSessionStatus.FAILED
                session.error_context = f"Max iterations ({session.max_iterations}) reached"
                await self._emit("META_SESSION_FAILED", session, {
                    "reason": session.error_context,
                })
                await self._update_experience(session, success=False)

        except Exception as exc:
            logger.exception("MetaController error for session %s", session.session_id)
            await self._record_pending_repair_outcomes(session, success=False)
            session.status = MetaSessionStatus.FAILED
            session.error_context = str(exc)
            await self._update_experience(session, success=False)

        session.updated_at = time.time()
        await self._save_session(session)
        return session

    async def resume(
        self,
        session_id: str,
        override: HumanOverride | None = None,
    ) -> MetaSession:
        """Resume a paused session, optionally with a human override."""
        if not self._session_store:
            raise RuntimeError("MetaSessionStore required for resume")

        session = await self._session_store.load(session_id)
        if session is None:
            raise ValueError(f"Session {session_id} not found")
        if session.status != MetaSessionStatus.PAUSED:
            raise ValueError(
                f"Session {session_id} is not paused (status={session.status})",
            )

        if override:
            session.human_override.append(override.model_dump())
            if override.override_type == OverrideType.ABORT:
                await self._record_pending_repair_outcomes(session, success=False)
                session.status = MetaSessionStatus.FAILED
                session.error_context = "Aborted by user"
                session.updated_at = time.time()
                await self._save_session(session)
                return session
            if override.override_type == OverrideType.ACCEPT_AS_IS:
                await self._record_pending_repair_outcomes(session, success=True)
                session.status = MetaSessionStatus.COMPLETED
                session.updated_at = time.time()
                await self._save_session(session)
                return session
            if override.override_type == OverrideType.MODIFY_PLAN:
                candidate = override.data.get("plan") if isinstance(override.data, dict) else None
                if isinstance(candidate, dict):
                    session.plan_result = candidate
            if override.override_type == OverrideType.MODIFY_GRAPH:
                if session.workflow_id and isinstance(override.data, dict):
                    from dan.server.graph_mutator import GraphMutator, MutationPlan

                    plan_data = override.data.get("mutation_plan", override.data)
                    try:
                        mp = MutationPlan.model_validate(plan_data)
                        graph_data = self._graph_loader(session.workflow_id) if self._graph_loader else None
                        if graph_data is not None:
                            result = GraphMutator().apply(graph_data, mp)
                            if result.success and result.new_graph is not None and self._graph_saver:
                                self._graph_saver(session.workflow_id, result.new_graph)
                    except Exception:
                        logger.debug("Failed to apply ModifyGraph override", exc_info=True)
            if override.override_type == OverrideType.SKIP_REPAIR:
                await self._record_pending_repair_outcomes(session, success=False)

        session.pause_requested = False
        paused_stage = session.paused_at_stage
        session.paused_at_stage = None
        session.status = MetaSessionStatus.PLANNING
        await self._emit("META_RESUMED", session, {
            "override": override.model_dump() if override else None,
        })

        cfg = MetaControllerConfig(max_iterations=session.max_iterations)
        return await self.run_session(session, cfg, resume_stage=paused_stage)

    # -- Internal helpers --------------------------------------------------

    @staticmethod
    def _plan_from_dict(plan_dict: dict[str, Any] | None) -> Any | None:
        from dan.meta.utils import plan_from_dict
        return plan_from_dict(plan_dict)

    @staticmethod
    def _build_plan_context(
        session: MetaSession,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        plan_context = dict(extra or {})
        if session.goal_context:
            adapt_workflow_id = session.goal_context.get("adapt_workflow_id")
            if adapt_workflow_id:
                plan_context["adapt_workflow_id"] = adapt_workflow_id
            goal_contract = normalize_goal_contract(session.goal_context.get("goal_contract"))
            if goal_contract:
                plan_context["goal_contract"] = goal_contract
        return plan_context or None

    async def _checkpoint_pause(self, session: MetaSession, stage: str) -> bool:
        """Pause when requested by config or external API at step checkpoints."""
        if self._session_store is not None:
            latest = await self._session_store.load(session.session_id)
            if latest is not None and latest.pause_requested:
                session.pause_requested = True

        if not session.pause_requested:
            return False

        session.status = MetaSessionStatus.PAUSED
        session.paused_at_stage = stage
        session.pause_requested = False
        session.updated_at = time.time()
        await self._save_session(session)
        await self._emit("META_PAUSED", session, {"stage": stage})
        return True

    async def _execute_plan(
        self,
        session: MetaSession,
        plan: Any,
    ) -> dict[str, Any] | None:
        if not self._run_workflow:
            logger.error("No run_workflow callable configured")
            return None
        try:
            result = await self._run_workflow(plan, session.session_id)
            return result if isinstance(result, dict) else {
                "success": False,
                "error_context": "Invalid result type",
            }
        except Exception as exc:
            logger.exception("Execution failed for session %s", session.session_id)
            return {"success": False, "error_context": str(exc)}

    async def _record_pending_repair_outcomes(self, session: MetaSession, success: bool) -> None:
        if not self._repair or not session.workflow_id:
            session.pending_repair_action_ids.clear()
            return
        for action_id in list(session.pending_repair_action_ids):
            try:
                await self._repair.record_outcome(session.workflow_id, action_id, success)
            except Exception:
                logger.debug("Failed to record repair outcome %s", action_id, exc_info=True)
        session.pending_repair_action_ids.clear()

    def _load_graph_model(self, workflow_id: str | None):
        if not workflow_id or not self._graph_loader:
            return None
        try:
            from dan.models.graph import Graph

            data = self._graph_loader(workflow_id)
            if data is None:
                return None
            return Graph.model_validate(data)
        except Exception:
            logger.debug("Failed to load graph %s", workflow_id, exc_info=True)
            return None

    async def _apply_mutation(
        self,
        workflow_id: str,
        mutation_plan_dict: dict[str, Any],
    ) -> bool:
        if not self._graph_loader or not self._graph_saver:
            return False
        try:
            from dan.server.graph_mutator import GraphMutator, MutationPlan

            graph_data = self._graph_loader(workflow_id)
            if graph_data is None:
                return False
            plan = MutationPlan.model_validate(mutation_plan_dict)
            result = GraphMutator().apply(graph_data, plan)
            if not result.success or result.new_graph is None:
                return False
            self._graph_saver(workflow_id, result.new_graph)
            return True
        except Exception:
            logger.debug("Failed to apply mutation for %s", workflow_id, exc_info=True)
            return False

    async def _apply_repairs(
        self,
        session: MetaSession,
        principles: list[dict[str, Any]],
        cfg: MetaControllerConfig,
    ) -> Literal["reexecute", "replan"] | None:
        """Apply repairs. Returns reexecute/replan, or None when paused."""
        if not self._repair or not principles:
            return "replan"

        from dan.engine.error_memory import CausalPrinciple
        from dan.meta.repair import ParameterFix, Redesign, StructuralFix

        graph = self._load_graph_model(session.workflow_id)
        if graph is None:
            from dan.models.graph import Graph

            graph = Graph()

        should_reexecute = False
        for p_dict in principles:
            principle = CausalPrinciple.model_validate(p_dict)
            if not principle.workflow_id and session.workflow_id:
                principle = principle.model_copy(update={"workflow_id": session.workflow_id})

            action = await self._repair.repair(
                principle,
                graph,
                session.error_context or "",
            )
            action_dict = action.model_dump()
            session.repair_history.append(action_dict)
            action_id = action_dict.get("action_id", "")
            if action_id:
                session.pending_repair_action_ids.append(action_id)

            await self._emit("META_REPAIR_APPLIED", session, {"action": action_dict})

            if isinstance(action, Redesign):
                if cfg.pause_on_redesign:
                    session.pause_requested = True
                    if await self._checkpoint_pause(session, "before_redesign"):
                        await self._emit("META_REDESIGN_TRIGGERED", session, {
                            "reason": action.reason,
                        })
                        return None
                await self._emit("META_REDESIGN_TRIGGERED", session, {
                    "reason": action.reason,
                })
                return "replan"

            if isinstance(action, (ParameterFix, StructuralFix)) and session.workflow_id:
                applied = await self._apply_mutation(
                    session.workflow_id,
                    action.mutation_plan,
                )
                should_reexecute = should_reexecute or applied
                refreshed = self._load_graph_model(session.workflow_id)
                if refreshed is not None:
                    graph = refreshed

        return "reexecute" if should_reexecute else "replan"

    async def _update_experience(
        self,
        session: MetaSession,
        *,
        success: bool = True,
    ) -> None:
        """Save or update workflow experience on completion (success or failure)."""
        if not self._experience or not session.workflow_id:
            return
        try:
            from dan.engine.experience import (
                WorkflowExperience,
                consolidate_experience,
                extract_experience_from_graph,
            )

            exp = await self._experience.load_experience(session.workflow_id)

            if exp is None:
                graph = self._load_graph_model(session.workflow_id)
                if graph is not None:
                    exp = extract_experience_from_graph(graph)
                    exp = exp.model_copy(update={"workflow_id": session.workflow_id})
                else:
                    exp = WorkflowExperience(
                        workflow_id=session.workflow_id,
                        name=session.goal[:80],
                    )

            run_snapshots = [
                {
                    "run_id": rid,
                    "success": success and (rid == session.run_history[-1] if session.run_history else False),
                }
                for rid in session.run_history
            ]
            repair_principles = [
                entry.get("principle", entry)
                for entry in session.repair_history
                if isinstance(entry, dict)
            ]

            if not success:
                exp = exp.model_copy(update={
                    "failure_patterns": list(dict.fromkeys(
                        [session.error_context or "unknown failure"]
                        + exp.failure_patterns
                    ))[:10],
                })

            exp = consolidate_experience(exp, run_snapshots, repair_principles)
            await self._experience.save_experience(exp)
        except Exception:
            logger.warning(
                "Failed to update experience for %s", session.workflow_id, exc_info=True,
            )

    async def _save_session(self, session: MetaSession) -> None:
        if self._session_store:
            await self._session_store.save(session)

    async def _emit(
        self,
        event_type: str,
        session: MetaSession,
        data: dict[str, Any],
    ) -> None:
        event = {
            "event_type": event_type,
            "session_id": session.session_id,
            "timestamp": time.time(),
            "data": data,
        }
        session.events.append(event)
        if len(session.events) > 2000:
            session.events = session.events[-2000:]
        await self._save_session(session)

        if self._emit_event:
            try:
                await self._emit_event(event)
            except Exception:
                logger.debug("Failed to emit event %s", event_type)
