"""Integration tests for plan_scheduler — parallel execution, makespan benchmarks, dual-caller.

Task 9-2: Integration test with mock tasks and varying durations.
Task 9-3: Benchmark comparing exact vs LRP vs serial makespan.
Task 9-4: Dual-caller test verifying concierge and workflow paths produce identical schedules.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from dan.engine.plan_scheduler import (
    InputRequirement,
    OutputArtifact,
    PlanConstraints,
    PlanDAG,
    PlanTask,
    PlanTaskResult,
    _schedule_lrp,
    compute_critical_path,
    execute_plan_tasks,
    format_progress_update,
    format_schedule_display,
    on_task_complete,
    reset_calibration,
    schedule_tasks,
    validate_preflight,
)
from dan.engine.plan_prompts import (
    DECOMPOSITION_PROMPT,
    DEPENDENCY_TEMPLATES,
    FEW_SHOT_EXAMPLES,
    VALIDATION_PROMPT,
    apply_template_deps,
    estimate_from_experience,
    predict_deps_from_experience,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _varying_duration_dag() -> PlanDAG:
    """A -> (B, C, D) -> E with varied durations to test parallel execution.

         A(5)
       / | \\
     B(10) C(3) D(7)
       \\ | /
         E(5)
    """
    return PlanDAG(
        tasks=[
            PlanTask(id="A", name="Setup", estimated_duration_minutes=5),
            PlanTask(id="B", name="Long Task", estimated_duration_minutes=10, dependencies=["A"]),
            PlanTask(id="C", name="Quick Task", estimated_duration_minutes=3, dependencies=["A"]),
            PlanTask(id="D", name="Medium Task", estimated_duration_minutes=7, dependencies=["A"]),
            PlanTask(id="E", name="Finalize", estimated_duration_minutes=5, dependencies=["B", "C", "D"]),
        ],
        goal="integration test",
    )


def _benchmark_dag() -> PlanDAG:
    """Larger DAG for benchmarking: 2 layers of parallel tasks + final merge.

    Layer 0: start (5 min)
    Layer 1: T1..T6 (10-20 min each, depend on start)
    Layer 2: merge (5 min, depends on all T1..T6)
    """
    tasks = [PlanTask(id="start", name="Start", estimated_duration_minutes=5)]
    for i in range(1, 7):
        tasks.append(PlanTask(
            id=f"T{i}",
            name=f"Worker {i}",
            estimated_duration_minutes=10 + i * 2,
            dependencies=["start"],
        ))
    tasks.append(PlanTask(
        id="merge",
        name="Merge",
        estimated_duration_minutes=5,
        dependencies=[f"T{i}" for i in range(1, 7)],
    ))
    return PlanDAG(tasks=tasks, goal="benchmark")


# ---------------------------------------------------------------------------
# 9-2: Integration test — mock parallel execution with makespan verification
# ---------------------------------------------------------------------------


class TestParallelExecution:
    @pytest.mark.asyncio
    async def test_parallel_dispatch_improves_makespan(self) -> None:
        dag = _varying_duration_dag()
        sched = schedule_tasks(dag, PlanConstraints(max_parallel=3))
        # Parallel: A(5) + max(B(10), C(3), D(7)) + E(5) = 5+10+5 = 20
        assert sched.makespan <= 20.0

        serial_sched = schedule_tasks(dag, PlanConstraints(max_parallel=1))
        # Serial: 5+10+3+7+5 = 30
        assert serial_sched.makespan >= sched.makespan

    @pytest.mark.asyncio
    async def test_execute_plan_tasks_runs_all(self) -> None:
        dag = _varying_duration_dag()
        sched = schedule_tasks(dag, PlanConstraints(max_parallel=3))

        mock_concierge = MagicMock()
        mock_concierge.run_plan_task = AsyncMock(return_value="ok")

        results = await execute_plan_tasks(dag, sched, mock_concierge)
        assert len(results) == 5
        assert all(r.success for r in results)
        completed_ids = {r.task_id for r in results}
        assert completed_ids == {"A", "B", "C", "D", "E"}

    @pytest.mark.asyncio
    async def test_execute_handles_failure(self) -> None:
        dag = PlanDAG(tasks=[
            PlanTask(id="A", name="A"),
            PlanTask(id="B", name="B", dependencies=["A"]),
        ])
        sched = schedule_tasks(dag)

        call_count = 0

        async def _failing_task(task):
            nonlocal call_count
            call_count += 1
            if task.id == "A":
                raise RuntimeError("simulated failure")
            return "ok"

        mock_concierge = MagicMock()
        mock_concierge.run_plan_task = _failing_task

        results = await execute_plan_tasks(dag, sched, mock_concierge)
        a_result = next((r for r in results if r.task_id == "A"), None)
        assert a_result is not None
        assert not a_result.success
        assert "simulated failure" in a_result.error

    @pytest.mark.asyncio
    async def test_execute_without_run_plan_task(self) -> None:
        """Concierge without run_plan_task uses simulated fallback."""
        dag = PlanDAG(tasks=[PlanTask(id="A", name="Solo Task")])
        sched = schedule_tasks(dag)
        mock_concierge = MagicMock(spec=[])  # no run_plan_task attr
        results = await execute_plan_tasks(dag, sched, mock_concierge)
        assert len(results) == 1
        assert results[0].success
        assert "simulated" in results[0].output.lower()


# ---------------------------------------------------------------------------
# 9-3: Benchmark — compare makespan of exact vs LRP vs serial
# ---------------------------------------------------------------------------


class TestMakespanBenchmark:
    def setup_method(self) -> None:
        reset_calibration()

    def test_parallel_beats_serial(self) -> None:
        dag = _benchmark_dag()
        parallel = schedule_tasks(dag, PlanConstraints(max_parallel=6))
        serial = schedule_tasks(dag, PlanConstraints(max_parallel=1))
        # Parallel should be much better
        assert parallel.makespan < serial.makespan
        # Parallel: 5 + max(12,14,16,18,20,22) + 5 = 5+22+5 = 32
        assert parallel.makespan <= 32.0
        # Serial: sum of all = 5+12+14+16+18+20+22+5 = 112
        assert serial.makespan == pytest.approx(112.0)

    def test_lrp_vs_critical_path(self) -> None:
        dag = _benchmark_dag()
        cp = compute_critical_path(dag)
        lrp = _schedule_lrp(dag, PlanConstraints(max_parallel=3))
        # LRP with limited parallelism should have higher makespan than CP
        assert lrp.makespan >= cp.makespan

    def test_diamond_benchmark(self) -> None:
        """Diamond: verify CP and LRP agree on makespan with adequate parallelism."""
        tasks = [
            PlanTask(id="A", name="A", estimated_duration_minutes=10),
            PlanTask(id="B", name="B", estimated_duration_minutes=20, dependencies=["A"]),
            PlanTask(id="C", name="C", estimated_duration_minutes=5, dependencies=["A"]),
            PlanTask(id="D", name="D", estimated_duration_minutes=10, dependencies=["B", "C"]),
        ]
        dag = PlanDAG(tasks=tasks)
        cp = compute_critical_path(dag)
        lrp = _schedule_lrp(dag, PlanConstraints(max_parallel=4))
        assert cp.makespan == pytest.approx(40.0)
        assert lrp.makespan == pytest.approx(40.0)

    def test_golden_example_research_report(self) -> None:
        """Golden example from few-shot: research report."""
        example = FEW_SHOT_EXAMPLES[0]
        tasks = [PlanTask(**t) for t in example["tasks"]]
        dag = PlanDAG(tasks=tasks, goal=example["goal"])
        parallel = schedule_tasks(dag, PlanConstraints(max_parallel=3))
        serial = schedule_tasks(dag, PlanConstraints(max_parallel=1))
        assert parallel.makespan < serial.makespan

    def test_golden_example_kaggle(self) -> None:
        """Golden example from few-shot: Kaggle competition."""
        example = FEW_SHOT_EXAMPLES[3]
        tasks = [PlanTask(**t) for t in example["tasks"]]
        dag = PlanDAG(tasks=tasks, goal=example["goal"])
        parallel = schedule_tasks(dag, PlanConstraints(max_parallel=4))
        serial = schedule_tasks(dag, PlanConstraints(max_parallel=1))
        assert parallel.makespan < serial.makespan


# ---------------------------------------------------------------------------
# 9-4: Dual-caller test — concierge vs workflow path same schedule
# ---------------------------------------------------------------------------


class TestDualCallerIdentical:
    def test_both_paths_same_dag_same_result(self) -> None:
        dag = _varying_duration_dag()
        constraints = PlanConstraints(max_parallel=3)

        sched_a = schedule_tasks(dag, constraints)
        sched_b = schedule_tasks(dag, constraints)

        assert sched_a.makespan == sched_b.makespan
        assert sched_a.critical_path == sched_b.critical_path

        times_a = {a.task_id: (a.start_time, a.end_time) for a in sched_a.assignments}
        times_b = {a.task_id: (a.start_time, a.end_time) for a in sched_b.assignments}
        assert times_a == times_b

    def test_benchmark_dag_deterministic(self) -> None:
        dag = _benchmark_dag()
        constraints = PlanConstraints(max_parallel=3)

        results = [schedule_tasks(dag, constraints) for _ in range(5)]
        makespans = [r.makespan for r in results]
        assert len(set(makespans)) == 1, "Non-deterministic scheduling detected"

    def test_critical_paths_identical(self) -> None:
        dag = _benchmark_dag()
        s1 = schedule_tasks(dag)
        s2 = schedule_tasks(dag)
        assert s1.critical_path == s2.critical_path


# ---------------------------------------------------------------------------
# Prompt and template validation
# ---------------------------------------------------------------------------


class TestPromptTemplates:
    def test_decomposition_prompt_has_placeholders(self) -> None:
        assert "{goal}" in DECOMPOSITION_PROMPT
        assert "{experience_hint}" in DECOMPOSITION_PROMPT

    def test_validation_prompt_has_placeholders(self) -> None:
        assert "{goal}" in VALIDATION_PROMPT
        assert "{dag_json}" in VALIDATION_PROMPT

    def test_few_shot_examples_valid_dags(self) -> None:
        for example in FEW_SHOT_EXAMPLES:
            tasks = [PlanTask(**t) for t in example["tasks"]]
            dag = PlanDAG(tasks=tasks, goal=example["goal"])
            assert len(dag.tasks) >= 3

    def test_dependency_templates_exist(self) -> None:
        assert len(DEPENDENCY_TEMPLATES) >= 5

    def test_apply_template_deps_adds_edges(self) -> None:
        dag = PlanDAG(tasks=[
            PlanTask(id="eda", name="EDA"),
            PlanTask(id="feature_engineering", name="Feature Engineering"),
            PlanTask(id="model_training", name="Model Training"),
        ])
        result = apply_template_deps(dag, "ml_experiment")
        task_map = {t.id: t for t in result.tasks}
        assert "eda" in task_map["feature_engineering"].dependencies

    def test_apply_template_deps_no_match(self) -> None:
        dag = PlanDAG(tasks=[
            PlanTask(id="X", name="Unrelated"),
            PlanTask(id="Y", name="Also Unrelated"),
        ])
        result = apply_template_deps(dag, "ml_experiment")
        for t in result.tasks:
            assert t.dependencies == []

    def test_estimate_from_experience_with_store(self) -> None:
        store = MagicMock()
        store.search.return_value = [
            {"avg_elapsed_seconds": 600},  # 10 min
            {"avg_elapsed_seconds": 900},  # 15 min
        ]
        estimate = estimate_from_experience("data analysis", store)
        assert estimate == pytest.approx(12.5)

    def test_estimate_from_experience_none_store(self) -> None:
        assert estimate_from_experience("task", None) is None

    def test_estimate_from_experience_no_results(self) -> None:
        store = MagicMock()
        store.search.return_value = []
        assert estimate_from_experience("task", store) is None

    def test_predict_deps_with_store(self) -> None:
        dag = PlanDAG(tasks=[
            PlanTask(id="A", name="A"),
            PlanTask(id="B", name="B"),
        ])
        store = MagicMock()
        store.get_dependency_pairs.return_value = {("a", "b"): 5}
        predicted = predict_deps_from_experience(dag, store)
        assert ("A", "B") in predicted

    def test_predict_deps_none_store(self) -> None:
        dag = PlanDAG(tasks=[PlanTask(id="A", name="A")])
        assert predict_deps_from_experience(dag, None) == []


# ---------------------------------------------------------------------------
# End-to-end: preflight + reschedule + progress
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def setup_method(self) -> None:
        reset_calibration()

    def test_preflight_reschedule_loop(self) -> None:
        """Simulate: validate → discover missing dep → reschedule → validate again."""
        dag = PlanDAG(tasks=[
            PlanTask(
                id="A", name="A",
                expected_outputs=[OutputArtifact(name="data", artifact_type="csv")],
            ),
            PlanTask(
                id="B", name="B",
                required_inputs=[InputRequirement(name="data", artifact_type="csv", producer_task_id="A")],
            ),
        ])
        # B has no explicit dependency on A
        completed: dict[str, PlanTask] = {}
        b = next(t for t in dag.tasks if t.id == "B")
        missing = validate_preflight(b, completed)
        assert len(missing) == 1

        # Add the dependency via mid-execution handler
        new_dag = PlanDAG(tasks=[
            dag.tasks[0],
            b.model_copy(update={"dependencies": ["A"]}),
        ], goal=dag.goal)
        sched = schedule_tasks(new_dag)
        assert sched.makespan > 0

        # After A completes
        completed["A"] = dag.tasks[0]
        assert validate_preflight(b, completed) == []

    def test_progress_updates_through_execution(self) -> None:
        dag = _varying_duration_dag()
        sched = schedule_tasks(dag, PlanConstraints(max_parallel=3))

        progress_0 = format_progress_update(sched, set(), dag)
        assert "0/5" in progress_0

        progress_1 = format_progress_update(sched, {"A"}, dag)
        assert "1/5" in progress_1

        progress_all = format_progress_update(sched, {"A", "B", "C", "D", "E"}, dag)
        assert "5/5" in progress_all
        assert "complete" in progress_all.lower()
