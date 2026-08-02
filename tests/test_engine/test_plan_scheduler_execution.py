from __future__ import annotations

import asyncio
import time

import pytest

from dan.engine.plan_scheduler import (
    PlanConstraints,
    PlanDAG,
    PlanSchedule,
    PlanTask,
    execute_plan_tasks,
)


class RecordingConcierge:
    def __init__(self, delays: dict[str, float]) -> None:
        self.delays = delays
        self.starts: dict[str, float] = {}
        self.ends: dict[str, float] = {}
        self.calls: list[str] = []
        self.active = 0
        self.max_active = 0

    async def run_plan_task(self, task: PlanTask) -> str:
        self.calls.append(task.id)
        self.starts[task.id] = time.monotonic()
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delays.get(task.id, 0.0))
            return f"done:{task.id}"
        finally:
            self.ends[task.id] = time.monotonic()
            self.active -= 1


def _empty_schedule() -> PlanSchedule:
    return PlanSchedule(
        assignments=[],
        makespan=0.0,
        critical_path=[],
        parallelism_utilization=0.0,
    )


@pytest.mark.asyncio
async def test_execute_plan_tasks_releases_downstream_before_slow_sibling_finishes() -> None:
    dag = PlanDAG(
        tasks=[
            PlanTask(id="root", name="root"),
            PlanTask(id="slow", name="slow", dependencies=["root"]),
            PlanTask(id="fast", name="fast", dependencies=["root"]),
            PlanTask(id="after_fast", name="after_fast", dependencies=["fast"]),
        ],
        constraints=PlanConstraints(max_parallel=2),
    )
    concierge = RecordingConcierge(
        delays={
            "root": 0.01,
            "slow": 0.15,
            "fast": 0.02,
            "after_fast": 0.01,
        }
    )

    results = await execute_plan_tasks(dag, _empty_schedule(), concierge)

    assert [result.task_id for result in results] == ["root", "fast", "after_fast", "slow"]
    assert concierge.starts["after_fast"] < concierge.ends["slow"]


@pytest.mark.asyncio
async def test_execute_plan_tasks_respects_max_parallel_limit() -> None:
    dag = PlanDAG(
        tasks=[PlanTask(id=f"t{i}", name=f"t{i}") for i in range(5)],
        constraints=PlanConstraints(max_parallel=2),
    )
    concierge = RecordingConcierge({f"t{i}": 0.05 for i in range(5)})

    results = await execute_plan_tasks(dag, _empty_schedule(), concierge)

    assert len(results) == 5
    assert all(result.success for result in results)
    assert concierge.max_active <= 2


@pytest.mark.asyncio
async def test_execute_plan_tasks_returns_completion_order_not_submission_order() -> None:
    dag = PlanDAG(
        tasks=[
            PlanTask(id="a_slow", name="a_slow"),
            PlanTask(id="b_fast", name="b_fast"),
        ],
        constraints=PlanConstraints(max_parallel=2),
    )
    concierge = RecordingConcierge({"a_slow": 0.08, "b_fast": 0.01})

    results = await execute_plan_tasks(dag, _empty_schedule(), concierge)

    assert [result.task_id for result in results] == ["b_fast", "a_slow"]
