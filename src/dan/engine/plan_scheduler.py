"""RCPSP plan scheduler — dependency-aware parallel scheduling with critical path analysis.

Standalone solver module: both concierge-driven tasks and workflow engine call
`schedule_tasks()`. No duplication — one solver, two callers.

Design: plan optimistically, validate mechanically, fix dynamically.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict, deque
from typing import Literal

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)

_SCHEDULER_EXACT_THRESHOLD = int(
    os.environ.get("DAN_SCHEDULER_EXACT_THRESHOLD", "50")
)

_TIER_HIERARCHY: dict[str, int] = {
    "micro": 0,
    "routine": 1,
    "reasoning": 2,
    "critical": 3,
}

_TIER_BY_RANK = {v: k for k, v in _TIER_HIERARCHY.items()}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class InputRequirement(BaseModel):
    """Structured input contract for a plan task."""

    name: str
    artifact_type: str
    schema_hint: str | None = None
    producer_task_id: str | None = None


class OutputArtifact(BaseModel):
    """Structured output contract for a plan task."""

    name: str
    artifact_type: str
    schema_hint: str | None = None


class PlanTask(BaseModel):
    """A single task in the plan DAG."""

    id: str
    name: str
    description: str = ""
    estimated_duration_minutes: float = 5.0
    dependencies: list[str] = Field(default_factory=list)
    required_inputs: list[InputRequirement] = Field(default_factory=list)
    expected_outputs: list[OutputArtifact] = Field(default_factory=list)
    model_tier: str | None = None
    priority: int = 0


class PlanConstraints(BaseModel):
    """Resource and quality constraints for scheduling."""

    max_parallel: int = 4
    budget_dollars: float | None = None
    deadline_minutes: float | None = None
    quality_floor: Literal["micro", "routine", "reasoning", "critical"] = "routine"


class ScheduleAssignment(BaseModel):
    """Scheduled placement of a single task."""

    task_id: str
    start_time: float
    end_time: float
    assigned_tier: str = "routine"
    is_critical_path: bool = False
    slack: float = 0.0


class PlanSchedule(BaseModel):
    """Complete schedule for a plan DAG."""

    assignments: list[ScheduleAssignment]
    makespan: float
    critical_path: list[str]
    parallelism_utilization: float


class PlanDAG(BaseModel):
    """Acyclic task graph with validation."""

    tasks: list[PlanTask]
    goal: str = ""
    constraints: PlanConstraints = Field(default_factory=PlanConstraints)

    @model_validator(mode="after")
    def _validate_acyclicity(self) -> PlanDAG:
        task_ids = {t.id for t in self.tasks}
        for t in self.tasks:
            for dep in t.dependencies:
                if dep not in task_ids:
                    raise ValueError(
                        f"Task '{t.id}' depends on unknown task '{dep}'"
                    )
        if _has_cycle(self.tasks):
            raise ValueError("PlanDAG contains a cycle")
        return self

    def topological_order(self) -> list[str]:
        """Return task IDs in topological order (Kahn's algorithm)."""
        return _topo_sort(self.tasks)


# ---------------------------------------------------------------------------
# Internal graph helpers
# ---------------------------------------------------------------------------


def _has_cycle(tasks: list[PlanTask]) -> bool:
    """Detect cycles via Kahn's algorithm — True if cycle exists."""
    in_degree: dict[str, int] = {t.id: 0 for t in tasks}
    adj: dict[str, list[str]] = {t.id: [] for t in tasks}
    for t in tasks:
        for dep in t.dependencies:
            adj[dep].append(t.id)
            in_degree[t.id] += 1
    queue = deque(tid for tid, d in in_degree.items() if d == 0)
    visited = 0
    while queue:
        node = queue.popleft()
        visited += 1
        for succ in adj[node]:
            in_degree[succ] -= 1
            if in_degree[succ] == 0:
                queue.append(succ)
    return visited != len(tasks)


def _topo_sort(tasks: list[PlanTask]) -> list[str]:
    """Kahn's topological sort — deterministic (priority-aware)."""
    priority_map = {t.id: t.priority for t in tasks}
    in_degree: dict[str, int] = {t.id: 0 for t in tasks}
    adj: dict[str, list[str]] = {t.id: [] for t in tasks}
    for t in tasks:
        for dep in t.dependencies:
            adj[dep].append(t.id)
            in_degree[t.id] += 1
    queue = sorted(
        [tid for tid, d in in_degree.items() if d == 0],
        key=lambda x: (-priority_map[x], x),
    )
    result: list[str] = []
    while queue:
        node = queue.pop(0)
        result.append(node)
        successors = []
        for succ in adj[node]:
            in_degree[succ] -= 1
            if in_degree[succ] == 0:
                successors.append(succ)
        successors.sort(key=lambda x: (-priority_map[x], x))
        queue = sorted(queue + successors, key=lambda x: (-priority_map[x], x))
    return result


def _build_adj(
    tasks: list[PlanTask],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Return (successors, predecessors) adjacency maps."""
    succ: dict[str, list[str]] = {t.id: [] for t in tasks}
    pred: dict[str, list[str]] = {t.id: [] for t in tasks}
    for t in tasks:
        for dep in t.dependencies:
            succ[dep].append(t.id)
            pred[t.id].append(dep)
    return succ, pred


# ---------------------------------------------------------------------------
# Critical path solver
# ---------------------------------------------------------------------------


def compute_critical_path(dag: PlanDAG) -> PlanSchedule:
    """Forward + backward pass with unlimited parallelism.

    Returns a schedule representing the lower bound on makespan (no resource
    constraints). Tasks with zero slack are on the critical path.
    """
    dur = {t.id: t.estimated_duration_minutes for t in dag.tasks}
    succ, pred = _build_adj(dag.tasks)
    order = dag.topological_order()

    # Forward pass — earliest start / earliest finish
    es: dict[str, float] = {}
    ef: dict[str, float] = {}
    for tid in order:
        es[tid] = max((ef[p] for p in pred[tid]), default=0.0)
        ef[tid] = es[tid] + dur[tid]

    makespan = max(ef.values()) if ef else 0.0

    # Backward pass — latest start / latest finish
    lf: dict[str, float] = {}
    ls: dict[str, float] = {}
    for tid in reversed(order):
        lf[tid] = min((ls[s] for s in succ[tid]), default=makespan)
        ls[tid] = lf[tid] - dur[tid]

    # Slack and critical path
    slack = {tid: ls[tid] - es[tid] for tid in order}
    cp_set = {tid for tid, s in slack.items() if abs(s) < 1e-9}

    cp_order = [tid for tid in order if tid in cp_set]

    quality_floor_rank = _TIER_HIERARCHY.get(dag.constraints.quality_floor, 1)

    assignments = []
    for tid in order:
        on_cp = tid in cp_set
        tier = _assign_tier(on_cp, slack[tid], makespan, quality_floor_rank)
        assignments.append(
            ScheduleAssignment(
                task_id=tid,
                start_time=es[tid],
                end_time=ef[tid],
                assigned_tier=tier,
                is_critical_path=on_cp,
                slack=slack[tid],
            )
        )

    active_at: dict[float, int] = defaultdict(int)
    for a in assignments:
        active_at[a.start_time] += 1
        active_at[a.end_time] -= 1

    max_par = _peak_parallelism(assignments)
    n = len(dag.tasks)
    utilization = max_par / n if n > 0 else 0.0

    return PlanSchedule(
        assignments=assignments,
        makespan=makespan,
        critical_path=cp_order,
        parallelism_utilization=utilization,
    )


def _assign_tier(
    is_critical: bool,
    slack: float,
    makespan: float,
    floor_rank: int,
) -> str:
    """Assign model tier: critical-path → higher, high-slack → lower."""
    if is_critical:
        rank = max(floor_rank, 2)  # at least "reasoning"
    elif makespan > 0 and slack / makespan > 0.5:
        rank = max(floor_rank - 1, 0)  # drop one tier
    else:
        rank = floor_rank
    return _TIER_BY_RANK.get(rank, "routine")


def _peak_parallelism(assignments: list[ScheduleAssignment]) -> int:
    """Compute peak number of concurrently running tasks."""
    events: list[tuple[float, int]] = []
    for a in assignments:
        events.append((a.start_time, 1))
        events.append((a.end_time, -1))
    events.sort(key=lambda e: (e[0], e[1]))
    peak = 0
    current = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


# ---------------------------------------------------------------------------
# RCPSP scheduler — exact (OR-Tools) + heuristic (LRP) fallback
# ---------------------------------------------------------------------------

# OR-Tools availability
_ortools_available = False
try:
    from ortools.sat.python import cp_model as _cp_model  # type: ignore[import-untyped]

    _ortools_available = True
except Exception:
    _cp_model = None


def _schedule_exact(dag: PlanDAG, constraints: PlanConstraints) -> PlanSchedule:
    """Exact RCPSP solver using OR-Tools CP-SAT."""
    assert _cp_model is not None
    model = _cp_model.CpModel()
    dur = {t.id: t.estimated_duration_minutes for t in dag.tasks}
    task_map = {t.id: t for t in dag.tasks}
    succ, pred = _build_adj(dag.tasks)
    order = dag.topological_order()

    horizon = int(sum(dur.values()) + 1)

    starts: dict[str, Any] = {}
    ends: dict[str, Any] = {}
    intervals: dict[str, Any] = {}

    for tid in order:
        d = int(max(1, round(dur[tid])))
        s = model.new_int_var(0, horizon, f"s_{tid}")
        e = model.new_int_var(0, horizon, f"e_{tid}")
        iv = model.new_interval_var(s, d, e, f"iv_{tid}")
        starts[tid] = s
        ends[tid] = e
        intervals[tid] = iv

    # Precedence constraints
    for tid in order:
        for p in pred[tid]:
            model.add(starts[tid] >= ends[p])

    # Resource constraint: at most max_parallel tasks at any time
    if constraints.max_parallel < len(dag.tasks):
        demands = [1] * len(dag.tasks)
        model.add_cumulative(
            [intervals[tid] for tid in order],
            demands,
            constraints.max_parallel,
        )

    # Objective: minimize makespan
    makespan_var = model.new_int_var(0, horizon, "makespan")
    model.add_max_equality(makespan_var, list(ends.values()))
    model.minimize(makespan_var)

    solver = _cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10.0
    status = solver.solve(model)

    if status not in (_cp_model.OPTIMAL, _cp_model.FEASIBLE):
        logger.warning("CP-SAT did not find a feasible schedule, falling back to LRP")
        return _schedule_lrp(dag, constraints)

    # Extract solution
    es = {tid: float(solver.value(starts[tid])) for tid in order}
    ef = {tid: float(solver.value(ends[tid])) for tid in order}
    actual_makespan = float(solver.value(makespan_var))

    # Compute slack via backward pass on the solved makespan
    lf: dict[str, float] = {}
    ls: dict[str, float] = {}
    for tid in reversed(order):
        lf[tid] = min((es[s] for s in succ[tid]), default=actual_makespan)
        ls[tid] = lf[tid] - dur[tid]
    slack = {tid: max(0.0, ls[tid] - es[tid]) for tid in order}

    cp_set = {tid for tid, s in slack.items() if abs(s) < 1e-9}
    cp_order = [tid for tid in order if tid in cp_set]

    quality_floor_rank = _TIER_HIERARCHY.get(constraints.quality_floor, 1)

    assignments = []
    for tid in order:
        on_cp = tid in cp_set
        tier_override = task_map[tid].model_tier
        tier = (
            tier_override
            if tier_override
            else _assign_tier(on_cp, slack[tid], actual_makespan, quality_floor_rank)
        )
        assignments.append(
            ScheduleAssignment(
                task_id=tid,
                start_time=es[tid],
                end_time=ef[tid],
                assigned_tier=tier,
                is_critical_path=on_cp,
                slack=slack[tid],
            )
        )

    max_par = _peak_parallelism(assignments)
    n = len(dag.tasks)
    utilization = max_par / n if n > 0 else 0.0

    return PlanSchedule(
        assignments=assignments,
        makespan=actual_makespan,
        critical_path=cp_order,
        parallelism_utilization=utilization,
    )


def _schedule_lrp(dag: PlanDAG, constraints: PlanConstraints) -> PlanSchedule:
    """Longest-Remaining-Path-First list scheduling heuristic.

    O(n log n + e) — used when ortools unavailable or n > threshold.
    """
    dur = {t.id: t.estimated_duration_minutes for t in dag.tasks}
    task_map = {t.id: t for t in dag.tasks}
    succ, pred = _build_adj(dag.tasks)
    order = dag.topological_order()

    # Compute longest remaining path (LRP) from each task
    lrp: dict[str, float] = {}
    for tid in reversed(order):
        child_max = max((lrp[s] for s in succ[tid]), default=0.0)
        lrp[tid] = dur[tid] + child_max

    # Priority queue sorted by (-lrp, priority, id)
    ready = sorted(
        [tid for tid in order if not pred[tid]],
        key=lambda x: (-lrp[x], -task_map[x].priority, x),
    )

    max_par = constraints.max_parallel
    es: dict[str, float] = {}
    ef: dict[str, float] = {}
    in_degree = {tid: len(pred[tid]) for tid in order}
    resource_ends: list[float] = []  # tracks when each slot frees up

    while ready:
        tid = ready.pop(0)
        earliest_pred = max((ef[p] for p in pred[tid]), default=0.0)

        if len(resource_ends) < max_par:
            slot_free = 0.0
        else:
            resource_ends.sort()
            slot_free = resource_ends[0]
            resource_ends.pop(0)

        start = max(earliest_pred, slot_free)
        end = start + dur[tid]
        es[tid] = start
        ef[tid] = end
        resource_ends.append(end)

        for s in succ[tid]:
            in_degree[s] -= 1
            if in_degree[s] == 0:
                ready.append(s)
        ready.sort(key=lambda x: (-lrp[x], -task_map[x].priority, x))

    makespan = max(ef.values()) if ef else 0.0

    # Backward pass for slack
    lf_map: dict[str, float] = {}
    ls_map: dict[str, float] = {}
    for tid in reversed(order):
        lf_map[tid] = min((es[s] for s in succ[tid]), default=makespan)
        ls_map[tid] = lf_map[tid] - dur[tid]
    slack = {tid: max(0.0, ls_map[tid] - es[tid]) for tid in order}

    cp_set = {tid for tid, s in slack.items() if abs(s) < 1e-9}
    cp_order = [tid for tid in order if tid in cp_set]

    quality_floor_rank = _TIER_HIERARCHY.get(constraints.quality_floor, 1)

    assignments = []
    for tid in order:
        on_cp = tid in cp_set
        tier_override = task_map[tid].model_tier
        tier = (
            tier_override
            if tier_override
            else _assign_tier(on_cp, slack[tid], makespan, quality_floor_rank)
        )
        assignments.append(
            ScheduleAssignment(
                task_id=tid,
                start_time=es[tid],
                end_time=ef[tid],
                assigned_tier=tier,
                is_critical_path=on_cp,
                slack=slack[tid],
            )
        )

    max_p = _peak_parallelism(assignments)
    n = len(dag.tasks)
    utilization = max_p / n if n > 0 else 0.0

    return PlanSchedule(
        assignments=assignments,
        makespan=makespan,
        critical_path=cp_order,
        parallelism_utilization=utilization,
    )


def schedule_tasks(
    dag: PlanDAG,
    constraints: PlanConstraints | None = None,
) -> PlanSchedule:
    """Auto-select exact or heuristic scheduler based on task count and availability.

    For n <= DAN_SCHEDULER_EXACT_THRESHOLD and ortools available: exact CP-SAT.
    Otherwise: LRP heuristic fallback.
    """
    c = constraints or dag.constraints
    n = len(dag.tasks)
    if n == 0:
        return PlanSchedule(
            assignments=[],
            makespan=0.0,
            critical_path=[],
            parallelism_utilization=0.0,
        )
    if _ortools_available and n <= _SCHEDULER_EXACT_THRESHOLD:
        logger.debug("Using exact CP-SAT solver for %d tasks", n)
        return _schedule_exact(dag, c)
    logger.debug("Using LRP heuristic for %d tasks", n)
    return _schedule_lrp(dag, c)


# ---------------------------------------------------------------------------
# Dynamic rescheduling
# ---------------------------------------------------------------------------


class _CalibrationState:
    """Tracks actual vs estimated durations for calibration."""

    def __init__(self) -> None:
        self.ratios: list[float] = []

    @property
    def factor(self) -> float:
        if not self.ratios:
            return 1.0
        return sum(self.ratios) / len(self.ratios)

    def record(self, estimated: float, actual: float) -> None:
        if estimated > 0:
            self.ratios.append(actual / estimated)


_calibration = _CalibrationState()


def reset_calibration() -> None:
    """Reset the global calibration state (useful for tests)."""
    _calibration.ratios.clear()


def on_task_complete(
    schedule: PlanSchedule,
    dag: PlanDAG,
    task_id: str,
    actual_duration: float,
) -> PlanSchedule:
    """Remove completed task, recalibrate estimates, and reschedule remaining tasks."""
    task_map = {t.id: t for t in dag.tasks}
    if task_id not in task_map:
        raise ValueError(f"Unknown task: {task_id}")

    # Record calibration
    estimated = task_map[task_id].estimated_duration_minutes
    _calibration.record(estimated, actual_duration)
    cal = _calibration.factor

    # Build new DAG with remaining tasks, calibrated durations
    remaining = []
    for t in dag.tasks:
        if t.id == task_id:
            continue
        new_deps = [d for d in t.dependencies if d != task_id]
        calibrated_dur = t.estimated_duration_minutes * cal
        remaining.append(
            t.model_copy(
                update={
                    "dependencies": new_deps,
                    "estimated_duration_minutes": calibrated_dur,
                }
            )
        )

    if not remaining:
        return PlanSchedule(
            assignments=[],
            makespan=0.0,
            critical_path=[],
            parallelism_utilization=0.0,
        )

    new_dag = PlanDAG(tasks=remaining, goal=dag.goal, constraints=dag.constraints)
    return schedule_tasks(new_dag, dag.constraints)


def insert_task(dag: PlanDAG, task: PlanTask) -> PlanDAG:
    """Insert a new task into the DAG (validates acyclicity)."""
    new_tasks = list(dag.tasks) + [task]
    return PlanDAG(tasks=new_tasks, goal=dag.goal, constraints=dag.constraints)


# ---------------------------------------------------------------------------
# Dependency inference
# ---------------------------------------------------------------------------


def infer_dependencies(dag: PlanDAG) -> PlanDAG:
    """Match InputRequirement to OutputArtifact by name/type, add missing edges.

    Runs transitive reduction afterward to keep the DAG minimal.
    """
    task_map = {t.id: t for t in dag.tasks}

    # Build output index: (name, type) -> list of producer task IDs
    output_index: dict[tuple[str, str], list[str]] = defaultdict(list)
    for t in dag.tasks:
        for out in t.expected_outputs:
            output_index[(out.name, out.artifact_type)].append(t.id)

    # For each task's required inputs, find producers
    new_edges: list[tuple[str, str]] = []
    existing_edges: set[tuple[str, str]] = set()
    for t in dag.tasks:
        for dep in t.dependencies:
            existing_edges.add((dep, t.id))

    updated_tasks: list[PlanTask] = []
    for t in dag.tasks:
        new_deps = list(t.dependencies)
        updated_inputs = list(t.required_inputs)
        changed = False
        for i, inp in enumerate(updated_inputs):
            candidates = output_index.get((inp.name, inp.artifact_type), [])
            candidates = [c for c in candidates if c != t.id]
            if not candidates:
                continue
            producer = candidates[0]
            if (producer, t.id) not in existing_edges:
                new_deps.append(producer)
                existing_edges.add((producer, t.id))
                new_edges.append((producer, t.id))
            if inp.producer_task_id is None:
                updated_inputs[i] = inp.model_copy(
                    update={"producer_task_id": producer}
                )
                changed = True

        if new_deps != list(t.dependencies) or changed:
            updated_tasks.append(
                t.model_copy(
                    update={
                        "dependencies": new_deps,
                        "required_inputs": updated_inputs,
                    }
                )
            )
        else:
            updated_tasks.append(t)

    # Check for cycles introduced by inference
    if _has_cycle(updated_tasks):
        logger.warning(
            "Dependency inference introduced a cycle — "
            "removing inferred edges that conflict"
        )
        updated_tasks = _break_inferred_cycles(dag.tasks, new_edges)

    # Transitive reduction
    updated_tasks = _transitive_reduction(updated_tasks)

    return PlanDAG(tasks=updated_tasks, goal=dag.goal, constraints=dag.constraints)


def _break_inferred_cycles(
    original_tasks: list[PlanTask],
    inferred_edges: list[tuple[str, str]],
) -> list[PlanTask]:
    """Remove inferred edges that cause cycles, preserving LLM-declared edges."""
    task_map = {t.id: t.model_copy() for t in original_tasks}
    # Original (LLM-declared) edges
    original_edges: set[tuple[str, str]] = set()
    for t in original_tasks:
        for dep in t.dependencies:
            original_edges.add((dep, t.id))

    # Start from original + all inferred, remove inferred edges until acyclic
    all_tasks = list(task_map.values())
    for t in all_tasks:
        extra_deps = []
        for src, tgt in inferred_edges:
            if tgt == t.id and (src, tgt) not in original_edges:
                extra_deps.append(src)
        if extra_deps:
            new_deps = list(t.dependencies) + [
                d for d in extra_deps if d not in t.dependencies
            ]
            t_copy = t.model_copy(update={"dependencies": new_deps})
            task_map[t.id] = t_copy

    tasks_list = list(task_map.values())

    # Remove inferred edges from the end until no cycle
    for src, tgt in reversed(inferred_edges):
        if not _has_cycle(tasks_list):
            break
        t = task_map[tgt]
        new_deps = [d for d in t.dependencies if d != src]
        task_map[tgt] = t.model_copy(update={"dependencies": new_deps})
        tasks_list = list(task_map.values())
        logger.warning("Broke inferred edge %s -> %s to resolve cycle", src, tgt)

    return tasks_list


def _transitive_reduction(tasks: list[PlanTask]) -> list[PlanTask]:
    """Remove redundant edges: A->C is redundant if A->B->C exists."""
    succ, pred = _build_adj(tasks)

    # For each task, compute full reachable set via DFS from each successor
    def _reachable_via_intermediaries(tid: str, direct_preds: list[str]) -> set[str]:
        """Find all predecessors reachable from tid's other predecessors (transitive)."""
        reachable: set[str] = set()
        for p in direct_preds:
            stack = [p]
            while stack:
                node = stack.pop()
                for pp in pred[node]:
                    if pp not in reachable:
                        reachable.add(pp)
                        stack.append(pp)
        return reachable

    result = []
    for t in tasks:
        if len(t.dependencies) <= 1:
            result.append(t)
            continue
        reachable = _reachable_via_intermediaries(t.id, t.dependencies)
        reduced_deps = [d for d in t.dependencies if d not in reachable]
        if len(reduced_deps) < len(t.dependencies):
            result.append(t.model_copy(update={"dependencies": reduced_deps}))
        else:
            result.append(t)
    return result


def detect_conflicts(dag: PlanDAG) -> list[str]:
    """Flag circular dependency conflicts. Returns list of warning messages."""
    warnings: list[str] = []
    # Check for tasks that mutually produce/consume each other's artifacts
    output_map: dict[str, set[str]] = {}
    for t in dag.tasks:
        output_map[t.id] = {
            (o.name, o.artifact_type) for o in t.expected_outputs  # type: ignore[misc]
        }

    for t in dag.tasks:
        for inp in t.required_inputs:
            key = (inp.name, inp.artifact_type)
            for other_id, outputs in output_map.items():
                if other_id == t.id:
                    continue
                if key in outputs:
                    reverse_key_set = output_map.get(t.id, set())
                    for other_inp in next(
                        (ot for ot in dag.tasks if ot.id == other_id), PlanTask(id="", name="")
                    ).required_inputs:
                        other_key = (other_inp.name, other_inp.artifact_type)
                        if other_key in reverse_key_set:
                            warnings.append(
                                f"Circular artifact dependency: {t.id} and {other_id} "
                                f"mutually produce/consume artifacts"
                            )
    return list(set(warnings))
