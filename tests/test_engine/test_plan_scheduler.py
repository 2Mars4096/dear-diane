"""Tests for plan_scheduler — DAG, critical path, RCPSP, dynamic rescheduling, inference."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

from dan.engine.plan_scheduler import (
    InputRequirement,
    OutputArtifact,
    PlanConstraints,
    PlanDAG,
    PlanDependencyDiscovered,
    PlanRescheduled,
    PlanSchedule,
    PlanTask,
    PlanTaskCompleted,
    PlanTaskResult,
    PlanTaskStarted,
    ScheduleAssignment,
    _has_cycle,
    _schedule_lrp,
    _transitive_reduction,
    apply_resource_budget,
    build_branch_dag,
    compute_critical_path,
    detect_conflicts,
    format_progress_update,
    format_schedule_display,
    handle_mid_execution_dependency,
    infer_dependencies,
    insert_task,
    on_task_complete,
    reset_calibration,
    schedule_tasks,
    validate_preflight,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _linear_chain(n: int = 4) -> PlanDAG:
    """A -> B -> C -> D linear chain, each 10 min."""
    tasks = []
    for i in range(n):
        tid = chr(65 + i)  # A, B, C, D ...
        deps = [chr(65 + i - 1)] if i > 0 else []
        tasks.append(PlanTask(id=tid, name=f"Task {tid}", estimated_duration_minutes=10, dependencies=deps))
    return PlanDAG(tasks=tasks, goal="linear chain")


def _diamond_dag() -> PlanDAG:
    """
    Diamond:   A
              / \\
             B   C
              \\ /
               D
    A=10, B=20, C=5, D=10
    Critical path: A -> B -> D  (40 min)
    """
    return PlanDAG(
        tasks=[
            PlanTask(id="A", name="A", estimated_duration_minutes=10),
            PlanTask(id="B", name="B", estimated_duration_minutes=20, dependencies=["A"]),
            PlanTask(id="C", name="C", estimated_duration_minutes=5, dependencies=["A"]),
            PlanTask(id="D", name="D", estimated_duration_minutes=10, dependencies=["B", "C"]),
        ],
        goal="diamond",
    )


def _parallel_dag() -> PlanDAG:
    """Three independent tasks — no dependencies."""
    return PlanDAG(
        tasks=[
            PlanTask(id="X", name="X", estimated_duration_minutes=10),
            PlanTask(id="Y", name="Y", estimated_duration_minutes=15),
            PlanTask(id="Z", name="Z", estimated_duration_minutes=8),
        ],
        goal="parallel",
    )


def _artifact_dag() -> PlanDAG:
    """Tasks with artifact contracts for dependency inference testing.

    A produces "report" (text).  B needs "report" (text) but has no explicit dep on A.
    C produces "data" (csv).  D needs "data" (csv) and "report" (text).
    """
    return PlanDAG(
        tasks=[
            PlanTask(
                id="A", name="Generate Report", estimated_duration_minutes=10,
                expected_outputs=[OutputArtifact(name="report", artifact_type="text")],
            ),
            PlanTask(
                id="B", name="Summarize Report", estimated_duration_minutes=5,
                required_inputs=[InputRequirement(name="report", artifact_type="text")],
            ),
            PlanTask(
                id="C", name="Collect Data", estimated_duration_minutes=8,
                expected_outputs=[OutputArtifact(name="data", artifact_type="csv")],
            ),
            PlanTask(
                id="D", name="Analyze", estimated_duration_minutes=12,
                required_inputs=[
                    InputRequirement(name="data", artifact_type="csv"),
                    InputRequirement(name="report", artifact_type="text"),
                ],
            ),
        ],
        goal="artifact inference test",
    )


# ---------------------------------------------------------------------------
# DAG construction and acyclicity validation
# ---------------------------------------------------------------------------

class TestDAGConstruction:
    def test_valid_dag(self) -> None:
        dag = _linear_chain()
        assert len(dag.tasks) == 4
        assert dag.topological_order() == ["A", "B", "C", "D"]

    def test_reject_cycle_direct(self) -> None:
        with pytest.raises(ValueError, match="cycle"):
            PlanDAG(tasks=[
                PlanTask(id="A", name="A", dependencies=["B"]),
                PlanTask(id="B", name="B", dependencies=["A"]),
            ])

    def test_reject_cycle_indirect(self) -> None:
        with pytest.raises(ValueError, match="cycle"):
            PlanDAG(tasks=[
                PlanTask(id="A", name="A", dependencies=["C"]),
                PlanTask(id="B", name="B", dependencies=["A"]),
                PlanTask(id="C", name="C", dependencies=["B"]),
            ])

    def test_reject_unknown_dependency(self) -> None:
        with pytest.raises(ValueError, match="unknown task"):
            PlanDAG(tasks=[
                PlanTask(id="A", name="A", dependencies=["NONEXISTENT"]),
            ])

    def test_empty_dag(self) -> None:
        dag = PlanDAG(tasks=[])
        assert dag.topological_order() == []

    def test_single_task(self) -> None:
        dag = PlanDAG(tasks=[PlanTask(id="solo", name="Solo")])
        assert dag.topological_order() == ["solo"]

    def test_diamond_topo_order(self) -> None:
        dag = _diamond_dag()
        order = dag.topological_order()
        assert order.index("A") < order.index("B")
        assert order.index("A") < order.index("C")
        assert order.index("B") < order.index("D")
        assert order.index("C") < order.index("D")

    def test_parallel_topo_order(self) -> None:
        dag = _parallel_dag()
        order = dag.topological_order()
        assert set(order) == {"X", "Y", "Z"}


# ---------------------------------------------------------------------------
# Critical path computation
# ---------------------------------------------------------------------------

class TestCriticalPath:
    def test_linear_chain(self) -> None:
        dag = _linear_chain()
        sched = compute_critical_path(dag)
        assert sched.makespan == 40.0
        assert sched.critical_path == ["A", "B", "C", "D"]
        assert all(a.is_critical_path for a in sched.assignments)

    def test_diamond(self) -> None:
        dag = _diamond_dag()
        sched = compute_critical_path(dag)
        # CP: A(10) + B(20) + D(10) = 40
        assert sched.makespan == 40.0
        assert "A" in sched.critical_path
        assert "B" in sched.critical_path
        assert "D" in sched.critical_path
        # C has slack = 15 (can start at 10, must finish by 30, takes 5)
        c_assign = next(a for a in sched.assignments if a.task_id == "C")
        assert c_assign.slack == pytest.approx(15.0)
        assert not c_assign.is_critical_path

    def test_parallel_independent(self) -> None:
        dag = _parallel_dag()
        sched = compute_critical_path(dag)
        assert sched.makespan == 15.0  # Y is the longest
        y_assign = next(a for a in sched.assignments if a.task_id == "Y")
        assert y_assign.is_critical_path

    def test_empty_dag(self) -> None:
        dag = PlanDAG(tasks=[])
        sched = compute_critical_path(dag)
        assert sched.makespan == 0.0
        assert sched.critical_path == []

    def test_critical_path_tasks_get_higher_tier(self) -> None:
        dag = _diamond_dag()
        sched = compute_critical_path(dag)
        for a in sched.assignments:
            if a.is_critical_path:
                assert a.assigned_tier in ("reasoning", "critical")


# ---------------------------------------------------------------------------
# LRP scheduling with resource constraints
# ---------------------------------------------------------------------------

class TestLRPScheduling:
    def test_linear_chain_max1(self) -> None:
        """Linear chain with max_parallel=1 should be serial."""
        dag = _linear_chain()
        constraints = PlanConstraints(max_parallel=1)
        sched = _schedule_lrp(dag, constraints)
        assert sched.makespan == 40.0

    def test_parallel_max1(self) -> None:
        """Three independent tasks with max_parallel=1 → serial."""
        dag = _parallel_dag()
        constraints = PlanConstraints(max_parallel=1)
        sched = _schedule_lrp(dag, constraints)
        assert sched.makespan == 33.0  # 10 + 15 + 8

    def test_parallel_unlimited(self) -> None:
        """Three independent tasks with max_parallel=10 → all start at 0."""
        dag = _parallel_dag()
        constraints = PlanConstraints(max_parallel=10)
        sched = _schedule_lrp(dag, constraints)
        assert sched.makespan == 15.0

    def test_diamond_max2(self) -> None:
        """Diamond with max_parallel=2 — B and C can run in parallel."""
        dag = _diamond_dag()
        constraints = PlanConstraints(max_parallel=2)
        sched = _schedule_lrp(dag, constraints)
        assert sched.makespan == 40.0  # A(10) + max(B(20), C(5)) + D(10)

    def test_resource_constraint_respected(self) -> None:
        """Verify no more than max_parallel tasks overlap at any point."""
        tasks = [PlanTask(id=f"T{i}", name=f"T{i}", estimated_duration_minutes=10) for i in range(6)]
        dag = PlanDAG(tasks=tasks)
        constraints = PlanConstraints(max_parallel=2)
        sched = _schedule_lrp(dag, constraints)
        # Check at every start/end point
        for a in sched.assignments:
            overlapping = sum(
                1 for b in sched.assignments
                if b.start_time < a.end_time and b.end_time > a.start_time
            )
            assert overlapping <= 2

    def test_model_tier_override(self) -> None:
        """Task with explicit model_tier should keep it."""
        dag = PlanDAG(tasks=[
            PlanTask(id="A", name="A", model_tier="critical"),
        ])
        sched = _schedule_lrp(dag, PlanConstraints())
        assert sched.assignments[0].assigned_tier == "critical"


# ---------------------------------------------------------------------------
# OR-Tools exact scheduling (skip if ortools not installed)
# ---------------------------------------------------------------------------

class TestExactScheduling:
    @pytest.fixture(autouse=True)
    def _check_ortools(self) -> None:
        from dan.engine.plan_scheduler import _ortools_available
        if not _ortools_available:
            pytest.skip("ortools not available")

    def test_diamond_exact(self) -> None:
        dag = _diamond_dag()
        constraints = PlanConstraints(max_parallel=2)
        from dan.engine.plan_scheduler import _schedule_exact
        sched = _schedule_exact(dag, constraints)
        assert sched.makespan == pytest.approx(40.0)

    def test_parallel_exact_max1(self) -> None:
        """Three independent tasks with max_parallel=1."""
        dag = _parallel_dag()
        constraints = PlanConstraints(max_parallel=1)
        from dan.engine.plan_scheduler import _schedule_exact
        sched = _schedule_exact(dag, constraints)
        assert sched.makespan == pytest.approx(33.0)

    def test_exact_respects_precedence(self) -> None:
        dag = _linear_chain(3)
        constraints = PlanConstraints(max_parallel=10)
        from dan.engine.plan_scheduler import _schedule_exact
        sched = _schedule_exact(dag, constraints)
        a_map = {a.task_id: a for a in sched.assignments}
        assert a_map["A"].end_time <= a_map["B"].start_time
        assert a_map["B"].end_time <= a_map["C"].start_time


# ---------------------------------------------------------------------------
# Auto-selection between exact and heuristic
# ---------------------------------------------------------------------------

class TestAutoSelection:
    def test_schedule_tasks_empty(self) -> None:
        dag = PlanDAG(tasks=[])
        sched = schedule_tasks(dag)
        assert sched.makespan == 0.0

    def test_schedule_tasks_returns_valid(self) -> None:
        dag = _diamond_dag()
        sched = schedule_tasks(dag, PlanConstraints(max_parallel=2))
        assert sched.makespan > 0
        assert len(sched.assignments) == 4

    def test_lrp_fallback_when_ortools_mocked_unavailable(self) -> None:
        """When ortools is unavailable, schedule_tasks falls back to LRP."""
        import dan.engine.plan_scheduler as mod

        original = mod._ortools_available
        try:
            mod._ortools_available = False
            dag = _diamond_dag()
            sched = schedule_tasks(dag, PlanConstraints(max_parallel=2))
            assert sched.makespan == pytest.approx(40.0)
            assert len(sched.assignments) == 4
        finally:
            mod._ortools_available = original

    def test_large_dag_uses_heuristic(self) -> None:
        """DAGs above threshold use LRP even when ortools is available."""
        import dan.engine.plan_scheduler as mod

        original_threshold = mod._SCHEDULER_EXACT_THRESHOLD
        try:
            mod._SCHEDULER_EXACT_THRESHOLD = 3
            dag = _linear_chain(4)  # n=4 > threshold=3
            sched = schedule_tasks(dag)
            assert sched.makespan == pytest.approx(40.0)
        finally:
            mod._SCHEDULER_EXACT_THRESHOLD = original_threshold


# ---------------------------------------------------------------------------
# Dynamic rescheduling and calibration
# ---------------------------------------------------------------------------

class TestDynamicRescheduling:
    def setup_method(self) -> None:
        reset_calibration()

    def test_on_task_complete_removes_task(self) -> None:
        dag = _linear_chain(3)  # A -> B -> C
        sched = schedule_tasks(dag)
        new_sched = on_task_complete(sched, dag, "A", actual_duration=10.0)
        task_ids = {a.task_id for a in new_sched.assignments}
        assert "A" not in task_ids
        assert "B" in task_ids
        assert "C" in task_ids

    def test_calibration_adjusts_estimates(self) -> None:
        dag = _linear_chain(3)  # A(10) -> B(10) -> C(10)
        sched = schedule_tasks(dag)
        # A took 20 instead of 10 → factor = 2.0
        new_sched = on_task_complete(sched, dag, "A", actual_duration=20.0)
        # B and C estimates should be calibrated to 10 * 2.0 = 20 each
        assert new_sched.makespan == pytest.approx(40.0)

    def test_calibration_factor_averages(self) -> None:
        """Multiple completions average the calibration factor."""
        dag = _linear_chain(4)  # A -> B -> C -> D
        sched = schedule_tasks(dag)
        # A took 10 (factor 1.0), then complete B
        sched2 = on_task_complete(sched, dag, "A", actual_duration=10.0)
        # Now factor = 1.0
        # Build new dag without A for the second on_task_complete
        dag2 = PlanDAG(tasks=[
            PlanTask(id="B", name="B", estimated_duration_minutes=10),
            PlanTask(id="C", name="C", estimated_duration_minutes=10, dependencies=["B"]),
            PlanTask(id="D", name="D", estimated_duration_minutes=10, dependencies=["C"]),
        ])
        sched3 = on_task_complete(sched2, dag2, "B", actual_duration=20.0)
        # factor = mean(1.0, 2.0) = 1.5, remaining C+D = 10*1.5 + 10*1.5 = 30
        assert sched3.makespan == pytest.approx(30.0)

    def test_on_task_complete_unknown_raises(self) -> None:
        dag = _linear_chain(2)
        sched = schedule_tasks(dag)
        with pytest.raises(ValueError, match="Unknown task"):
            on_task_complete(sched, dag, "UNKNOWN", 5.0)

    def test_on_task_complete_all_done(self) -> None:
        dag = PlanDAG(tasks=[PlanTask(id="A", name="A")])
        sched = schedule_tasks(dag)
        new_sched = on_task_complete(sched, dag, "A", actual_duration=5.0)
        assert new_sched.makespan == 0.0
        assert new_sched.assignments == []

    def test_insert_task(self) -> None:
        dag = _linear_chain(2)  # A -> B
        new_task = PlanTask(id="C", name="C", dependencies=["B"])
        new_dag = insert_task(dag, new_task)
        assert len(new_dag.tasks) == 3
        assert new_dag.topological_order() == ["A", "B", "C"]

    def test_insert_task_cycle_rejected(self) -> None:
        dag = _linear_chain(2)  # A -> B
        bad_task = PlanTask(id="C", name="C", dependencies=["B"])
        # First insert C depending on B — valid
        dag2 = insert_task(dag, bad_task)
        # Then try to make A depend on C — would create cycle
        with pytest.raises(ValueError, match="cycle"):
            PlanDAG(tasks=[
                PlanTask(id="A", name="A", dependencies=["C"]),
                PlanTask(id="B", name="B", dependencies=["A"]),
                PlanTask(id="C", name="C", dependencies=["B"]),
            ])


# ---------------------------------------------------------------------------
# Dependency inference from artifact contracts
# ---------------------------------------------------------------------------

class TestDependencyInference:
    def test_infer_adds_missing_edges(self) -> None:
        dag = _artifact_dag()
        inferred = infer_dependencies(dag)
        task_map = {t.id: t for t in inferred.tasks}
        # B should now depend on A (report text)
        assert "A" in task_map["B"].dependencies
        # D should depend on C (data csv) and A (report text)
        assert "C" in task_map["D"].dependencies
        assert "A" in task_map["D"].dependencies

    def test_infer_sets_producer_task_id(self) -> None:
        dag = _artifact_dag()
        inferred = infer_dependencies(dag)
        task_map = {t.id: t for t in inferred.tasks}
        b_input = task_map["B"].required_inputs[0]
        assert b_input.producer_task_id == "A"

    def test_infer_preserves_existing_deps(self) -> None:
        """Existing explicit dependencies are not duplicated."""
        dag = PlanDAG(tasks=[
            PlanTask(
                id="A", name="A",
                expected_outputs=[OutputArtifact(name="x", artifact_type="t")],
            ),
            PlanTask(
                id="B", name="B", dependencies=["A"],
                required_inputs=[InputRequirement(name="x", artifact_type="t")],
            ),
        ])
        inferred = infer_dependencies(dag)
        b = next(t for t in inferred.tasks if t.id == "B")
        assert b.dependencies.count("A") == 1

    def test_infer_no_self_dependency(self) -> None:
        """A task producing and consuming the same artifact shouldn't self-depend."""
        dag = PlanDAG(tasks=[
            PlanTask(
                id="A", name="A",
                expected_outputs=[OutputArtifact(name="x", artifact_type="t")],
                required_inputs=[InputRequirement(name="x", artifact_type="t")],
            ),
        ])
        inferred = infer_dependencies(dag)
        a = next(t for t in inferred.tasks if t.id == "A")
        assert "A" not in a.dependencies


# ---------------------------------------------------------------------------
# Transitive reduction
# ---------------------------------------------------------------------------

class TestTransitiveReduction:
    def test_removes_redundant_edge(self) -> None:
        """A->B->C and A->C → A->C is redundant, should be removed."""
        tasks = [
            PlanTask(id="A", name="A"),
            PlanTask(id="B", name="B", dependencies=["A"]),
            PlanTask(id="C", name="C", dependencies=["A", "B"]),
        ]
        reduced = _transitive_reduction(tasks)
        c = next(t for t in reduced if t.id == "C")
        assert "A" not in c.dependencies
        assert "B" in c.dependencies

    def test_preserves_necessary_edges(self) -> None:
        """No edges removed when all are necessary."""
        tasks = [
            PlanTask(id="A", name="A"),
            PlanTask(id="B", name="B", dependencies=["A"]),
            PlanTask(id="C", name="C", dependencies=["B"]),
        ]
        reduced = _transitive_reduction(tasks)
        b = next(t for t in reduced if t.id == "B")
        c = next(t for t in reduced if t.id == "C")
        assert b.dependencies == ["A"]
        assert c.dependencies == ["B"]

    def test_diamond_reduction(self) -> None:
        """Diamond with extra direct edge: A->B, A->C, B->D, C->D, A->D → remove A->D."""
        tasks = [
            PlanTask(id="A", name="A"),
            PlanTask(id="B", name="B", dependencies=["A"]),
            PlanTask(id="C", name="C", dependencies=["A"]),
            PlanTask(id="D", name="D", dependencies=["A", "B", "C"]),
        ]
        reduced = _transitive_reduction(tasks)
        d = next(t for t in reduced if t.id == "D")
        assert "A" not in d.dependencies
        assert "B" in d.dependencies
        assert "C" in d.dependencies


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------

class TestConflictDetection:
    def test_detect_mutual_dependency(self) -> None:
        dag = PlanDAG(tasks=[
            PlanTask(
                id="A", name="A",
                expected_outputs=[OutputArtifact(name="x", artifact_type="t")],
                required_inputs=[InputRequirement(name="y", artifact_type="t")],
            ),
            PlanTask(
                id="B", name="B",
                expected_outputs=[OutputArtifact(name="y", artifact_type="t")],
                required_inputs=[InputRequirement(name="x", artifact_type="t")],
            ),
        ])
        warnings = detect_conflicts(dag)
        assert len(warnings) > 0
        assert any("A" in w and "B" in w for w in warnings)

    def test_no_conflict_for_normal_dag(self) -> None:
        dag = _diamond_dag()
        warnings = detect_conflicts(dag)
        assert len(warnings) == 0


# ---------------------------------------------------------------------------
# Dual path test — concierge and workflow engine produce same schedule
# ---------------------------------------------------------------------------

class TestDualPath:
    def test_same_dag_same_schedule(self) -> None:
        """Both callers using schedule_tasks on the same PlanDAG get identical results."""
        dag = _diamond_dag()
        constraints = PlanConstraints(max_parallel=2)

        # Simulated concierge caller
        concierge_schedule = schedule_tasks(dag, constraints)

        # Simulated workflow engine caller — same DAG
        engine_schedule = schedule_tasks(dag, constraints)

        assert concierge_schedule.makespan == engine_schedule.makespan
        assert concierge_schedule.critical_path == engine_schedule.critical_path

        c_times = {a.task_id: (a.start_time, a.end_time) for a in concierge_schedule.assignments}
        e_times = {a.task_id: (a.start_time, a.end_time) for a in engine_schedule.assignments}
        assert c_times == e_times


# ---------------------------------------------------------------------------
# Model validation
# ---------------------------------------------------------------------------

class TestModels:
    def test_plan_constraints_defaults(self) -> None:
        c = PlanConstraints()
        assert c.max_parallel == 4
        assert c.budget_dollars is None
        assert c.deadline_minutes is None
        assert c.quality_floor == "routine"

    def test_schedule_assignment_defaults(self) -> None:
        a = ScheduleAssignment(task_id="t", start_time=0, end_time=5)
        assert a.assigned_tier == "routine"
        assert not a.is_critical_path
        assert a.slack == 0.0

    def test_plan_task_defaults(self) -> None:
        t = PlanTask(id="t", name="test")
        assert t.estimated_duration_minutes == 5.0
        assert t.dependencies == []
        assert t.required_inputs == []
        assert t.expected_outputs == []
        assert t.model_tier is None
        assert t.priority == 0

    def test_input_requirement(self) -> None:
        ir = InputRequirement(name="data", artifact_type="csv")
        assert ir.schema_hint is None
        assert ir.producer_task_id is None

    def test_output_artifact(self) -> None:
        oa = OutputArtifact(name="report", artifact_type="text", schema_hint="markdown")
        assert oa.schema_hint == "markdown"


# ---------------------------------------------------------------------------
# Edge cases and integration
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_single_task_schedule(self) -> None:
        dag = PlanDAG(tasks=[PlanTask(id="A", name="A", estimated_duration_minutes=7)])
        sched = schedule_tasks(dag)
        assert sched.makespan == pytest.approx(7.0)
        assert sched.critical_path == ["A"]

    def test_wide_parallel_dag(self) -> None:
        """10 independent tasks, max_parallel=3."""
        tasks = [PlanTask(id=f"T{i}", name=f"T{i}", estimated_duration_minutes=10) for i in range(10)]
        dag = PlanDAG(tasks=tasks)
        sched = schedule_tasks(dag, PlanConstraints(max_parallel=3))
        # ceil(10/3) * 10 = 40 (optimal packing)
        assert sched.makespan >= 30.0  # at least 4 batches of 10 = 40 expected
        # verify no time slot has >3 concurrent tasks
        for a in sched.assignments:
            overlapping = sum(
                1 for b in sched.assignments
                if b.start_time < a.end_time and b.end_time > a.start_time
            )
            assert overlapping <= 3

    def test_priority_ordering(self) -> None:
        """Higher priority tasks should be scheduled earlier when possible."""
        dag = PlanDAG(tasks=[
            PlanTask(id="lo", name="Low", estimated_duration_minutes=10, priority=0),
            PlanTask(id="hi", name="High", estimated_duration_minutes=10, priority=10),
        ])
        sched = schedule_tasks(dag, PlanConstraints(max_parallel=1))
        a_map = {a.task_id: a for a in sched.assignments}
        # High priority should start first
        assert a_map["hi"].start_time <= a_map["lo"].start_time

    def test_infer_then_schedule(self) -> None:
        """End-to-end: infer dependencies then schedule."""
        dag = _artifact_dag()
        inferred = infer_dependencies(dag)
        sched = schedule_tasks(inferred, PlanConstraints(max_parallel=2))
        assert sched.makespan > 0
        a_map = {a.task_id: a for a in sched.assignments}
        # B depends on A
        assert a_map["B"].start_time >= a_map["A"].end_time
        # D depends on A and C
        assert a_map["D"].start_time >= a_map["A"].end_time
        assert a_map["D"].start_time >= a_map["C"].end_time

    def test_quality_floor_affects_tiers(self) -> None:
        """Setting quality_floor=critical should make all tiers >= critical."""
        dag = _diamond_dag()
        dag_critical = PlanDAG(
            tasks=dag.tasks,
            goal=dag.goal,
            constraints=PlanConstraints(quality_floor="critical"),
        )
        sched = compute_critical_path(dag_critical)
        for a in sched.assignments:
            if a.is_critical_path:
                assert a.assigned_tier == "critical"

    def test_has_cycle_helper(self) -> None:
        assert _has_cycle([
            PlanTask(id="A", name="A", dependencies=["B"]),
            PlanTask(id="B", name="B", dependencies=["A"]),
        ])
        assert not _has_cycle([
            PlanTask(id="A", name="A"),
            PlanTask(id="B", name="B", dependencies=["A"]),
        ])


# ---------------------------------------------------------------------------
# Task 4-3: Pre-flight input validation
# ---------------------------------------------------------------------------

class TestPreflightValidation:
    def test_all_inputs_satisfied(self) -> None:
        completed = {
            "A": PlanTask(
                id="A", name="A",
                expected_outputs=[OutputArtifact(name="report", artifact_type="text")],
            ),
        }
        task = PlanTask(
            id="B", name="B",
            required_inputs=[InputRequirement(name="report", artifact_type="text", producer_task_id="A")],
            dependencies=["A"],
        )
        assert validate_preflight(task, completed) == []

    def test_missing_producer_not_completed(self) -> None:
        task = PlanTask(
            id="B", name="B",
            required_inputs=[InputRequirement(name="report", artifact_type="text", producer_task_id="A")],
        )
        missing = validate_preflight(task, {})
        assert len(missing) == 1
        assert "A" in missing[0]

    def test_missing_producer_unknown(self) -> None:
        task = PlanTask(
            id="B", name="B",
            required_inputs=[InputRequirement(name="data", artifact_type="csv")],
        )
        missing = validate_preflight(task, {})
        assert len(missing) == 1
        assert "no completed producer" in missing[0]

    def test_no_inputs_always_valid(self) -> None:
        task = PlanTask(id="X", name="X")
        assert validate_preflight(task, {}) == []


# ---------------------------------------------------------------------------
# Task 4-4: Mid-execution dependency discovery
# ---------------------------------------------------------------------------

class TestMidExecutionDependency:
    def test_adds_dependency_and_cancels(self) -> None:
        dag = PlanDAG(tasks=[
            PlanTask(id="A", name="A"),
            PlanTask(id="B", name="B"),
        ])
        new_dag = handle_mid_execution_dependency("B", "A", dag)
        b = next(t for t in new_dag.tasks if t.id == "B")
        assert "A" in b.dependencies
        assert b.cancelled_for_dependency is True

    def test_unknown_running_task_raises(self) -> None:
        dag = PlanDAG(tasks=[PlanTask(id="A", name="A")])
        with pytest.raises(ValueError, match="Unknown running task"):
            handle_mid_execution_dependency("UNKNOWN", "A", dag)

    def test_unknown_dependency_task_raises(self) -> None:
        dag = PlanDAG(tasks=[PlanTask(id="A", name="A")])
        with pytest.raises(ValueError, match="Unknown dependency task"):
            handle_mid_execution_dependency("A", "UNKNOWN", dag)

    def test_self_dependency_raises(self) -> None:
        dag = PlanDAG(tasks=[PlanTask(id="A", name="A")])
        with pytest.raises(ValueError, match="cannot depend on itself"):
            handle_mid_execution_dependency("A", "A", dag)

    def test_does_not_duplicate_edge(self) -> None:
        dag = PlanDAG(tasks=[
            PlanTask(id="A", name="A"),
            PlanTask(id="B", name="B", dependencies=["A"]),
        ])
        new_dag = handle_mid_execution_dependency("B", "A", dag)
        b = next(t for t in new_dag.tasks if t.id == "B")
        assert b.dependencies.count("A") == 1

    def test_cancelled_for_dependency_field(self) -> None:
        t = PlanTask(id="X", name="X")
        assert t.cancelled_for_dependency is False


# ---------------------------------------------------------------------------
# Task 6: Format schedule display and progress
# ---------------------------------------------------------------------------

class TestFormatDisplay:
    def test_format_schedule_display_nonempty(self) -> None:
        dag = _diamond_dag()
        sched = schedule_tasks(dag)
        display = format_schedule_display(sched, dag)
        assert "makespan" in display.lower()
        assert "critical path" in display.lower()

    def test_format_schedule_display_empty(self) -> None:
        sched = PlanSchedule(assignments=[], makespan=0.0, critical_path=[], parallelism_utilization=0.0)
        assert format_schedule_display(sched) == "(empty schedule)"

    def test_format_progress_update(self) -> None:
        dag = _linear_chain(3)
        sched = schedule_tasks(dag)
        progress = format_progress_update(sched, {"A"}, dag)
        assert "1/3" in progress
        assert "[done]" in progress
        assert "[pending]" in progress

    def test_format_progress_all_done(self) -> None:
        dag = PlanDAG(tasks=[PlanTask(id="A", name="A")])
        sched = schedule_tasks(dag)
        progress = format_progress_update(sched, {"A"}, dag)
        assert "1/1" in progress

    def test_critical_path_highlighted_in_display(self) -> None:
        dag = _diamond_dag()
        sched = compute_critical_path(dag)
        display = format_schedule_display(sched, dag)
        assert "█" in display  # critical path uses filled blocks


# ---------------------------------------------------------------------------
# Task 7: Workflow engine DAG conversion
# ---------------------------------------------------------------------------

class TestWorkflowEnginePath:
    def test_apply_resource_budget_clamps(self) -> None:
        c = PlanConstraints(max_parallel=10)
        from unittest.mock import MagicMock
        budget = MagicMock()
        budget.max_concurrent_llm_calls = 3
        result = apply_resource_budget(c, budget)
        assert result.max_parallel == 3

    def test_apply_resource_budget_noop_when_under(self) -> None:
        c = PlanConstraints(max_parallel=2)
        from unittest.mock import MagicMock
        budget = MagicMock()
        budget.max_concurrent_llm_calls = 10
        result = apply_resource_budget(c, budget)
        assert result.max_parallel == 2

    def test_apply_resource_budget_reads_env_when_budget_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DAN_MAX_CONCURRENT_LLM", "3")
        c = PlanConstraints(max_parallel=10)

        result = apply_resource_budget(c)

        assert result.max_parallel == 3

    def test_apply_resource_budget_ignores_invalid_env_value(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DAN_MAX_CONCURRENT_LLM", "not-an-int")
        c = PlanConstraints(max_parallel=10)

        result = apply_resource_budget(c)

        assert result.max_parallel == 10

    def test_plan_scheduler_has_no_server_imports(self) -> None:
        path = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "dan"
            / "engine"
            / "plan_scheduler.py"
        )
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        violations: list[str] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("dan.server"):
                    violations.append(f"{path.name}:{node.lineno} -> {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("dan.server"):
                        violations.append(
                            f"{path.name}:{node.lineno} -> {alias.name}"
                        )

        assert not violations, (
            "plan_scheduler should remain engine-only and must not import "
            "dan.server modules:\n" + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# Task 8: Event models
# ---------------------------------------------------------------------------

class TestEventModels:
    def test_plan_task_started(self) -> None:
        ev = PlanTaskStarted(task_id="A", task_name="Task A", makespan_estimate=40.0)
        assert ev.task_id == "A"
        assert ev.critical_path == []

    def test_plan_task_completed(self) -> None:
        ev = PlanTaskCompleted(
            task_id="A", success=True, actual_duration_minutes=12.5,
            calibration_factor=1.1,
        )
        assert ev.success
        assert ev.calibration_factor == pytest.approx(1.1)

    def test_plan_rescheduled(self) -> None:
        ev = PlanRescheduled(reason="calibration", new_makespan=35.0, tasks_remaining=3)
        assert ev.tasks_remaining == 3

    def test_plan_dependency_discovered(self) -> None:
        ev = PlanDependencyDiscovered(
            source_task_id="A", target_task_id="B",
            reason="missing artifact", cancelled_task_id="B",
        )
        assert ev.cancelled_task_id == "B"

    def test_plan_task_result(self) -> None:
        r = PlanTaskResult(task_id="X", success=True, output="done")
        assert r.output == "done"
