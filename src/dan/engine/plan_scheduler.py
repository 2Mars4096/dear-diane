"""RCPSP plan scheduler — dependency-aware parallel scheduling with critical path analysis.

Standalone solver module: both concierge-driven tasks and workflow engine call
`schedule_tasks()`. No duplication — one solver, two callers.

Design: plan optimistically, validate mechanically, fix dynamically.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict, deque
from typing import Any, Literal

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


def _max_concurrent_llm_calls_from_env() -> int | None:
    """Resolve the engine-local LLM concurrency cap from environment.

    ``plan_scheduler`` only needs the numeric limit, not the concierge's
    full ``ResourceBudget`` model.
    """
    raw = str(os.environ.get("DAN_MAX_CONCURRENT_LLM", "") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.debug(
            "Invalid DAN_MAX_CONCURRENT_LLM=%r; ignoring resource budget clamp",
            raw,
        )
        return None


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
    cancelled_for_dependency: bool = False


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

# OR-Tools availability — probed via subprocess to avoid Bus Error / Segfault
# crashes that kill the host process on broken native SWIG bindings (e.g.
# Anaconda Python 3.11 on macOS ARM with protobuf mismatches).
_ortools_available: bool | None = None
_cp_model: Any = None


def _check_ortools() -> bool:
    """Probe OR-Tools CP-SAT availability without risking a host-process crash.

    The native SWIG bindings can Bus Error / Segfault on import when protobuf
    or platform binaries are incompatible.  A ``try/except`` cannot catch OS
    signals, so we run the import in a throwaway subprocess first.  If that
    succeeds we import into the current process; if it crashes we mark
    OR-Tools as unavailable and fall back to the LRP heuristic.
    """
    global _ortools_available, _cp_model  # noqa: PLW0603
    if _ortools_available is not None:
        return _ortools_available

    import subprocess, sys  # noqa: E401
    try:
        probe = subprocess.run(
            [sys.executable, "-c", "from ortools.sat.python import cp_model"],
            capture_output=True,
            timeout=10,
        )
        if probe.returncode != 0:
            raise RuntimeError("ortools probe failed")
        from ortools.sat.python import cp_model  # type: ignore[import-untyped]
        _cp_model = cp_model
        _ortools_available = True
    except Exception:
        _cp_model = None
        _ortools_available = False
    return _ortools_available


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
    if _check_ortools() and n <= _SCHEDULER_EXACT_THRESHOLD:
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
    *,
    calibration: _CalibrationState | None = None,
) -> PlanSchedule:
    """Remove completed task, recalibrate estimates, and reschedule remaining tasks.

    Pass a per-execution ``calibration`` state to avoid cross-contamination
    between concurrent plan runs.  Falls back to the module-level singleton
    for backward compatibility.
    """
    cal_state = calibration if calibration is not None else _calibration
    task_map = {t.id: t for t in dag.tasks}
    if task_id not in task_map:
        raise ValueError(f"Unknown task: {task_id}")

    estimated = task_map[task_id].estimated_duration_minutes
    cal_state.record(estimated, actual_duration)
    cal = cal_state.factor

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


def validate_preflight(
    task: PlanTask, completed: dict[str, PlanTask]
) -> list[str]:
    """Check that *task*'s required_inputs are satisfied by completed predecessors.

    Returns a list of missing-dependency descriptions.  An empty list means the
    task is ready to start.  When non-empty, the caller should add the missing
    dependency edges and reschedule.
    """
    completed_outputs: dict[tuple[str, str], str] = {}
    for cid, ct in completed.items():
        for out in ct.expected_outputs:
            completed_outputs[(out.name, out.artifact_type)] = cid

    missing: list[str] = []
    for inp in task.required_inputs:
        key = (inp.name, inp.artifact_type)
        producer = completed_outputs.get(key)
        if producer is None:
            # Try to find the expected producer from the requirement hint
            if inp.producer_task_id and inp.producer_task_id not in completed:
                missing.append(
                    f"Input '{inp.name}' ({inp.artifact_type}) requires task "
                    f"'{inp.producer_task_id}' which has not completed"
                )
            elif not inp.producer_task_id:
                missing.append(
                    f"Input '{inp.name}' ({inp.artifact_type}) has no completed "
                    f"producer among predecessors"
                )
    return missing


def handle_mid_execution_dependency(
    running_task_id: str, needed_from_task_id: str, dag: PlanDAG
) -> PlanDAG:
    """Cancel *running_task_id*, add a dependency on *needed_from_task_id*, reschedule.

    The cancelled task is marked ``cancelled_for_dependency=True`` so the caller
    knows to re-run it after the new dependency completes.

    Returns a new PlanDAG with the added edge and the cancelled task flagged.
    """
    task_ids = {t.id for t in dag.tasks}
    if running_task_id not in task_ids:
        raise ValueError(f"Unknown running task: {running_task_id}")
    if needed_from_task_id not in task_ids:
        raise ValueError(f"Unknown dependency task: {needed_from_task_id}")
    if running_task_id == needed_from_task_id:
        raise ValueError("A task cannot depend on itself")

    updated: list[PlanTask] = []
    for t in dag.tasks:
        if t.id == running_task_id:
            new_deps = list(t.dependencies)
            if needed_from_task_id not in new_deps:
                new_deps.append(needed_from_task_id)
            updated.append(
                t.model_copy(
                    update={
                        "dependencies": new_deps,
                        "cancelled_for_dependency": True,
                    }
                )
            )
        else:
            updated.append(t)

    return PlanDAG(tasks=updated, goal=dag.goal, constraints=dag.constraints)


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


# ---------------------------------------------------------------------------
# Task 6: Concierge caller path
# ---------------------------------------------------------------------------


class PlanTaskResult(BaseModel):
    """Result of executing a single plan task."""

    task_id: str
    success: bool
    output: str = ""
    error: str = ""
    actual_duration_minutes: float = 0.0


async def execute_plan_tasks(
    dag: PlanDAG,
    schedule: PlanSchedule,
    concierge: Any,
) -> list[PlanTaskResult]:
    """Execute plan tasks via a concierge, respecting scheduled parallelism.

    Each PlanTask maps to a concierge sub-interaction.  Tasks are dispatched
    in topological order, with independent tasks launched concurrently up to
    ``dag.constraints.max_parallel``.

    Returns results in completion order.
    """
    import time as _time

    task_map = {t.id: t for t in dag.tasks}
    order = dag.topological_order()
    order_index = {tid: idx for idx, tid in enumerate(order)}

    remaining_in_degree: dict[str, int] = {tid: 0 for tid in order}
    succ_map: dict[str, list[str]] = {tid: [] for tid in order}
    for t in dag.tasks:
        for dep in t.dependencies:
            succ_map[dep].append(t.id)
            remaining_in_degree[t.id] += 1

    ready = [tid for tid in order if remaining_in_degree[tid] == 0]
    results: list[PlanTaskResult] = []
    completed_set: set[str] = set()
    in_flight: dict[asyncio.Task[PlanTaskResult], str] = {}
    max_parallel = max(1, int(getattr(dag.constraints, "max_parallel", 1) or 1))
    _ = schedule

    async def _run_one(task_id: str) -> PlanTaskResult:
        t0 = _time.monotonic()
        task = task_map[task_id]
        try:
            run_task = getattr(concierge, "run_plan_task", None)
            if run_task is not None:
                output = await run_task(task)
            else:
                output = f"[simulated] {task.name}"
            elapsed = (_time.monotonic() - t0) / 60.0
            return PlanTaskResult(
                task_id=task_id,
                success=True,
                output=str(output),
                actual_duration_minutes=elapsed,
            )
        except Exception as exc:
            elapsed = (_time.monotonic() - t0) / 60.0
            return PlanTaskResult(
                task_id=task_id,
                success=False,
                error=str(exc),
                actual_duration_minutes=elapsed,
            )

    while ready or in_flight:
        while ready and len(in_flight) < max_parallel:
            task_id = ready.pop(0)
            if task_id in completed_set or task_id in in_flight.values():
                continue
            in_flight[asyncio.create_task(_run_one(task_id))] = task_id

        if not in_flight:
            break

        done, _ = await asyncio.wait(
            in_flight.keys(),
            return_when=asyncio.FIRST_COMPLETED,
        )

        for finished_task in done:
            task_id = in_flight.pop(finished_task)
            try:
                result = finished_task.result()
            except BaseException as exc:
                result = PlanTaskResult(
                    task_id=task_id,
                    success=False,
                    error=str(exc),
                )
            results.append(result)
            completed_set.add(task_id)
            in_flight_ids = set(in_flight.values())
            for successor_id in succ_map.get(task_id, []):
                remaining_in_degree[successor_id] -= 1
                if (
                    remaining_in_degree[successor_id] == 0
                    and successor_id not in completed_set
                    and successor_id not in ready
                    and successor_id not in in_flight_ids
                ):
                    ready.append(successor_id)
        ready.sort(key=lambda task_id: order_index[task_id])

    return results


def format_schedule_display(schedule: PlanSchedule, dag: PlanDAG | None = None) -> str:
    """Gantt-like text display with critical path highlighted.

    Each task gets one row.  The timeline is divided into columns, each
    representing a time unit.  Critical-path tasks are marked with ``*``.
    """
    if not schedule.assignments:
        return "(empty schedule)"

    max_end = max(a.end_time for a in schedule.assignments)
    if max_end == 0:
        return "(empty schedule)"

    task_names: dict[str, str] = {}
    if dag:
        task_names = {t.id: t.name for t in dag.tasks}

    cols = 40
    scale = cols / max_end if max_end > 0 else 1.0

    lines: list[str] = []
    lines.append(f"Schedule — makespan {schedule.makespan:.1f} min, "
                 f"critical path: {' → '.join(schedule.critical_path)}")
    lines.append("")

    name_width = 20
    for a in sorted(schedule.assignments, key=lambda x: x.start_time):
        label = task_names.get(a.task_id, a.task_id)[:name_width].ljust(name_width)
        bar_start = int(a.start_time * scale)
        bar_end = max(bar_start + 1, int(a.end_time * scale))
        char = "█" if a.is_critical_path else "░"
        bar = "·" * bar_start + char * (bar_end - bar_start) + "·" * (cols - bar_end)
        cp_marker = " *" if a.is_critical_path else "  "
        tier_str = f"[{a.assigned_tier}]"
        lines.append(f"  {label} |{bar}|{cp_marker} {tier_str}")

    lines.append("")
    lines.append(f"  Parallelism utilization: {schedule.parallelism_utilization:.0%}")
    return "\n".join(lines)


def format_progress_update(
    schedule: PlanSchedule,
    completed_ids: set[str],
    dag: PlanDAG | None = None,
) -> str:
    """Format a progress update after task completion with revised ETA."""
    total = len(schedule.assignments)
    done = len(completed_ids)

    remaining_time = 0.0
    for a in schedule.assignments:
        if a.task_id not in completed_ids:
            remaining_time = max(remaining_time, a.end_time)

    elapsed_time = 0.0
    for a in schedule.assignments:
        if a.task_id in completed_ids:
            elapsed_time = max(elapsed_time, a.end_time)

    lines: list[str] = []
    lines.append(f"Progress: {done}/{total} tasks complete")

    task_names: dict[str, str] = {}
    if dag:
        task_names = {t.id: t.name for t in dag.tasks}

    for a in schedule.assignments:
        name = task_names.get(a.task_id, a.task_id)
        if a.task_id in completed_ids:
            lines.append(f"  [done] {name}")
        else:
            cp = " (critical)" if a.is_critical_path else ""
            lines.append(f"  [pending] {name}{cp}")

    if remaining_time > elapsed_time:
        eta = remaining_time - elapsed_time
        lines.append(f"\nEstimated remaining: {eta:.1f} min")
    else:
        lines.append("\nAll tasks complete!")

    return "\n".join(lines)


async def handle_plan_command(
    concierge: Any, message: str, args_str: str
) -> str:
    """Handle ``/plan`` command: show current schedule or force re-decomposition.

    Reads the active PlanDAG/PlanSchedule from concierge state.
    ``/plan --replan`` forces re-decomposition of the current goal.
    """
    state = getattr(concierge, "plan_state", None)
    if state is None:
        return "No active plan. Start one by describing a multi-step goal."

    dag: PlanDAG | None = getattr(state, "dag", None)
    sched: PlanSchedule | None = getattr(state, "schedule", None)

    if "--replan" in args_str:
        replan = getattr(concierge, "replan", None)
        if replan is not None:
            try:
                await replan()
                dag = getattr(state, "dag", None)
                sched = getattr(state, "schedule", None)
            except Exception as exc:
                return f"Re-plan failed: {exc}"

    if dag and sched:
        completed: set[str] = set(getattr(state, "completed_ids", set()))
        display = format_schedule_display(sched, dag)
        progress = format_progress_update(sched, completed, dag)
        return f"{display}\n\n{progress}"

    return "Plan exists but schedule not yet computed."


# ---------------------------------------------------------------------------
# Task 7: Workflow engine caller path
# ---------------------------------------------------------------------------


def dag_from_graph(graph: Any) -> PlanDAG:
    """Build an acyclic PlanDAG from a workflow Graph.

    Condenses strongly-connected components (loops) into supernodes so the
    resulting DAG is always acyclic.
    """
    from dan.models.graph import Graph

    if not isinstance(graph, Graph):
        raise TypeError(f"Expected Graph, got {type(graph).__name__}")

    # Build adjacency from edges
    node_ids = [n.id for n in graph.nodes]
    adj: dict[str, list[str]] = {nid: [] for nid in node_ids}
    for edge in graph.edges:
        src = getattr(edge, "source", None) or getattr(edge, "from_node", None)
        tgt = getattr(edge, "target", None) or getattr(edge, "to_node", None)
        if src and tgt and src in adj:
            adj[src].append(tgt)

    # Tarjan's SCC to find cycles
    sccs = _tarjan_scc(node_ids, adj)

    # Map each node to its SCC supernode ID
    node_to_scc: dict[str, str] = {}
    scc_members: dict[str, list[str]] = {}
    for scc in sccs:
        if len(scc) == 1:
            node_to_scc[scc[0]] = scc[0]
            scc_members[scc[0]] = scc
        else:
            supernode_id = f"scc_{'_'.join(sorted(scc))}"
            for nid in scc:
                node_to_scc[nid] = supernode_id
            scc_members[supernode_id] = scc

    # Build PlanTasks from supernodes
    node_map = {n.id: n for n in graph.nodes}
    seen_supernodes: set[str] = set()
    tasks: list[PlanTask] = []
    supernode_deps: dict[str, set[str]] = {sid: set() for sid in scc_members}

    for nid in node_ids:
        sid = node_to_scc[nid]
        for tgt in adj.get(nid, []):
            tgt_sid = node_to_scc[tgt]
            if tgt_sid != sid:
                supernode_deps[tgt_sid].add(sid)

    for sid, members in scc_members.items():
        if sid in seen_supernodes:
            continue
        seen_supernodes.add(sid)

        if len(members) == 1:
            n = node_map[members[0]]
            name = getattr(n, "name", n.id)
            desc = getattr(n, "description", "")
        else:
            name = f"Loop({', '.join(sorted(members))})"
            desc = f"Condensed SCC containing {len(members)} nodes"

        deps = [d for d in sorted(supernode_deps.get(sid, set()))]
        tasks.append(PlanTask(
            id=sid,
            name=name,
            description=desc,
            dependencies=deps,
        ))

    return PlanDAG(tasks=tasks, goal=graph.metadata.name or "workflow")


def _tarjan_scc(node_ids: list[str], adj: dict[str, list[str]]) -> list[list[str]]:
    """Tarjan's algorithm for strongly connected components."""
    index_counter = [0]
    stack: list[str] = []
    lowlink: dict[str, int] = {}
    index: dict[str, int] = {}
    on_stack: set[str] = set()
    result: list[list[str]] = []

    def strongconnect(v: str) -> None:
        index[v] = index_counter[0]
        lowlink[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack.add(v)

        for w in adj.get(v, []):
            if w not in index:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], index[w])

        if lowlink[v] == index[v]:
            component: list[str] = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                component.append(w)
                if w == v:
                    break
            result.append(component)

    for nid in node_ids:
        if nid not in index:
            strongconnect(nid)

    return result


def schedule_dag_regions(dag: PlanDAG) -> PlanSchedule:
    """Schedule DAG regions and SCC supernodes without reordering inside loops."""
    return schedule_tasks(dag)


def build_branch_dag(branch_keys: list[str], graph: Any) -> PlanDAG:
    """Build a PlanDAG from ParallelSubagentsExecutor branch sub-graphs."""
    from dan.models.graph import Graph

    if not isinstance(graph, Graph):
        raise TypeError(f"Expected Graph, got {type(graph).__name__}")

    tasks: list[PlanTask] = []
    for bk in branch_keys:
        sub = graph.sub_graphs.get(bk)
        if sub is None:
            tasks.append(PlanTask(id=bk, name=bk, description=f"Branch {bk}"))
            continue
        tasks.append(PlanTask(
            id=bk,
            name=sub.metadata.name or bk,
            description=sub.metadata.description or f"Branch {bk}",
        ))

    return PlanDAG(tasks=tasks, goal=f"parallel branches of {graph.metadata.name}")


def apply_resource_budget(
    constraints: PlanConstraints, budget: Any | None = None
) -> PlanConstraints:
    """Clamp ``max_parallel`` to respect the shared LLM concurrency budget."""
    max_llm = (
        getattr(budget, "max_concurrent_llm_calls", None)
        if budget is not None
        else _max_concurrent_llm_calls_from_env()
    )
    if max_llm is not None and constraints.max_parallel > max_llm:
        return constraints.model_copy(update={"max_parallel": max_llm})
    return constraints


# ---------------------------------------------------------------------------
# Task 8: Execution events
# ---------------------------------------------------------------------------


class PlanTaskStarted(BaseModel):
    """Emitted when a plan task begins execution."""

    task_id: str
    task_name: str = ""
    scheduled_start: float = 0.0
    critical_path: list[str] = Field(default_factory=list)
    makespan_estimate: float = 0.0


class PlanTaskCompleted(BaseModel):
    """Emitted when a plan task finishes execution."""

    task_id: str
    task_name: str = ""
    success: bool = True
    actual_duration_minutes: float = 0.0
    critical_path: list[str] = Field(default_factory=list)
    makespan_estimate: float = 0.0
    parallelism_utilization: float = 0.0
    calibration_factor: float = 1.0


class PlanRescheduled(BaseModel):
    """Emitted when the schedule is recomputed (e.g. after calibration)."""

    reason: str = ""
    new_makespan: float = 0.0
    critical_path: list[str] = Field(default_factory=list)
    parallelism_utilization: float = 0.0
    calibration_factor: float = 1.0
    tasks_remaining: int = 0


class PlanDependencyDiscovered(BaseModel):
    """Emitted when a new dependency is discovered mid-execution."""

    source_task_id: str
    target_task_id: str
    reason: str = ""
    cancelled_task_id: str | None = None
    new_makespan: float = 0.0
    critical_path: list[str] = Field(default_factory=list)
