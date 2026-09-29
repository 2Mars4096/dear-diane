"""Universal scheduling-agent builders."""

from __future__ import annotations

from dan.worker.core.model import WorkerDefinition
from dan.worker.specialized_agents import (
    SpecializedAgentKind,
    build_specialized_agent_worker,
    default_scheduler_guardrails,
)


DEFAULT_UNIVERSAL_SCHEDULER_INSTRUCTION = (
    "You are the universal scheduling agent on top of Diane's universal-agent substrate. "
    "Given a framed objective, current task graph, worker reports, validator results, "
    "artifacts, and context debt, propose the next scheduling move. You may choose "
    "serial, parallel, dispatch, split, duplicate/hedge, wait, improve context, "
    "validate, cancel, reopen, finalize, or stop. "
    "Prefer the smallest schedule change that improves time-to-quality. Treat branch "
    "parallelism as a reward/cost decision over expected value, latency, material-yield "
    "probability, and rework risk rather than a task-name rule. Treat branch "
    "cancellation conservatively: only prune when the optimistic branch value plus slack "
    "is clearly below the incumbent lower bound. Do not assume missing dependencies are "
    "resolved; ask for sharper context or wait when the barrier is real."
)


def build_universal_scheduling_worker(
    *,
    worker_id: str,
    model: str | None,
    role: str = "universal_scheduler",
    instruction: str | None = None,
    contract_name: str = "universal_scheduling_agent",
) -> WorkerDefinition:
    """Build the shared universal scheduling agent worker."""

    return build_specialized_agent_worker(
        worker_id=worker_id,
        role=role,
        instruction=instruction or DEFAULT_UNIVERSAL_SCHEDULER_INSTRUCTION,
        model=model,
        specialization=SpecializedAgentKind.SCHEDULER,
        contract_name=contract_name,
        recurrent_loop="framed_objective -> task_graph_update -> schedule_action -> guardrail_check -> worker_report",
        typed_action_contract="schedule_action_proposal",
        deterministic_guardrails=default_scheduler_guardrails(),
        metadata={
            "specialized_scheduler": True,
        },
    )


__all__ = [
    "DEFAULT_UNIVERSAL_SCHEDULER_INSTRUCTION",
    "build_universal_scheduling_worker",
]
