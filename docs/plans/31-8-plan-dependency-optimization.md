# 31-8: Plan Dependency Optimization

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** not-started
**Goal:** Replace linear plan execution with dependency-aware parallel scheduling. Decompose goals into a task DAG with time estimates, solve for the optimal execution path (minimizing makespan under resource constraints), and dynamically reschedule as tasks complete.

## Problem

Today DAN (and every production IDE/agent) executes plans linearly: task 1, then 2, then 3. But most plans contain independent subtasks that could run in parallel. The scheduling problem is well-studied in operations research (Resource-Constrained Project Scheduling Problem — RCPSP) and recent research (DynTaskMAS ICAPS 2025, GAP NeurIPS 2025, LAMaS 2025) proves 20-46% makespan reduction. No production tool implements it.

DAN already has the execution substrate: `ParallelSubagentsExecutor` (7-9), topological scheduling, `ResourceBudget` with `DAN_MAX_CONCURRENT_LLM`. The missing piece is the scheduler itself and the LLM planning prompt to emit dependencies + time estimates.

## Design Philosophy

**Plan optimistically, validate mechanically, fix dynamically.**

1. Let the LLM plan aggressively — maximize parallelism, assume tasks are independent unless clearly not
2. Infer dependencies deterministically from artifact contracts — add edges the LLM missed, prune redundant ones
3. Run schema-level pre-flight checks before each task — catch structural gaps mechanically at runtime
4. Re-schedule after every single completion — adapt to reality in microseconds
5. On failure, diagnose and restructure — use the existing repair pipeline

The scheduler's job isn't to be right once — it's to be cheap enough to re-run constantly.

## Tasks

- [ ] 1. **Standalone scheduler module (`src/dan/engine/plan_scheduler.py`)**
  - [ ] 1-1. `PlanTask` Pydantic model: `id: str`, `name: str`, `description: str`, `estimated_duration_minutes: float`, `dependencies: list[str]` (predecessor task IDs), `required_inputs: list[InputRequirement]`, `expected_outputs: list[OutputArtifact]`, `model_tier: str | None` (override), `priority: int = 0`
  - [ ] 1-2. `InputRequirement` / `OutputArtifact` models: structured artifact contracts (`name`, `artifact_type`, `schema_hint`, `producer_task_id | None`) so dependency repair is mechanical instead of text-only
  - [ ] 1-3. `PlanDAG` model: `tasks: list[PlanTask]`, `goal: str`, `constraints: PlanConstraints` — validates acyclicity on construction, computes topological order. For workflow-engine callers, the solver operates on an **acyclic planning graph** (native DAG or SCC-condensed graph), not arbitrary raw cyclic graphs.
  - [ ] 1-4. `PlanConstraints` model: `max_parallel: int` (from `DAN_MAX_CONCURRENT_LLM`), `budget_dollars: float | None`, `deadline_minutes: float | None`, `quality_floor: Literal["micro", "routine", "reasoning", "critical"] = "routine"`
  - [ ] 1-5. `PlanSchedule` model: `assignments: list[ScheduleAssignment]` — each assignment: `task_id`, `start_time`, `end_time`, `assigned_tier`, `is_critical_path: bool`, `slack: float`
  - [ ] 1-6. **This module is the single solver — both concierge-driven tasks and workflow engine call it.** No duplication. Concierge builds a `PlanDAG` from user intent; workflow engine builds one from an acyclic execution view of graph topology. Both call `schedule_tasks()`.

- [ ] 2. **Critical path solver**
  - [ ] 2-1. Forward pass: compute earliest start/finish for each task
  - [ ] 2-2. Backward pass: compute latest start/finish, identify slack
  - [ ] 2-3. Critical path extraction: tasks with zero slack
  - [ ] 2-4. `compute_critical_path(dag: PlanDAG) -> PlanSchedule` — returns schedule with unlimited parallelism (lower bound on makespan)

- [ ] 3. **Resource-constrained scheduler (RCPSP)**
  - [ ] 3-1. **Exact solver (default for n ≤ 50):** OR-Tools CP-SAT solver — provably optimal schedule. For typical plan sizes (5-50 tasks), CP-SAT solves in milliseconds. `ortools` as optional dep with `try: from ortools... except ImportError` guard; graceful fallback to heuristic if not installed. Add a test that verifies LRP fallback fires when `ortools` is mocked as unavailable.
  - [ ] 3-2. **LRP heuristic (fallback, or n > 50):** Longest-Remaining-Path-First list scheduling: O(n log n + e), ~50 lines. Used when `ortools` unavailable or task count exceeds threshold (`DAN_SCHEDULER_EXACT_THRESHOLD`, default 50).
  - [ ] 3-3. `schedule_tasks(dag: PlanDAG, constraints: PlanConstraints) -> PlanSchedule` — auto-selects exact vs heuristic based on task count and `ortools` availability. Returns feasible schedule respecting `max_parallel`.
  - [ ] 3-4. Model-tier assignment: critical-path tasks get higher tier (reasoning/critical), high-slack tasks get lower tier (micro/routine) — integrates with existing `TierPolicy`

- [ ] 4. **Dynamic rescheduling**
  - [ ] 4-1. `on_task_complete(task_id, actual_duration)` — remove completed task, re-run scheduler on remaining DAG. Re-run is microseconds (exact) or sub-microseconds (heuristic) — call after every single event.
  - [ ] 4-2. Duration calibration: compute `calibration_factor = mean(actual/estimated)` for completed tasks, scale remaining estimates. Recalibrate on every completion.
  - [ ] 4-3. **Pre-flight input validation** (the "validate mechanically" step): before starting each task, check that structured `required_inputs` are satisfied by `expected_outputs` of completed predecessors. If not: find the producer task by artifact contract → add the dependency edge → reschedule. This is DAN's typed-port advantage — purely text-based systems can't do this.
  - [ ] 4-4. Mid-execution dependency: if a running task discovers it needs output from an unfinished task, **cancel the running task** (not pause — suspending a live LLM/tool call mid-stream is impractical), add the dependency edge, reschedule, and **re-run** the cancelled task after the dependency completes. v2 follow-up: true pause/resume via coroutine checkpointing for long tool executions.
  - [ ] 4-5. New task insertion: if execution discovers an unplanned subtask is needed, insert into DAG and reschedule. DAG mutation is O(1); rescheduling is the same O(n log n) or CP-SAT call.

- [ ] 5. **LLM planning prompt (the "plan optimistically" step)**
  - [ ] 5-1. Decomposition prompt: given a goal, emit `PlanTask` list with dependencies and time estimates. **Bias toward independence** — only mark dependencies when causally required. The pre-flight check (4-3) catches missed ones.
  - [ ] 5-2. Few-shot examples: 3-5 golden decompositions (research report, code refactor, data pipeline, Kaggle competition, equity analysis) with correct dependency edges
  - [ ] 5-3. Validation prompt: "here is a task DAG — are there missing dependencies or redundant edges?" — one extra LLM call at planning time, catches most semantic gaps
  - [ ] 5-4. Time estimation calibration: use experience memory (19-1) to look up similar past tasks and their actual durations; fall back to LLM heuristic estimate for novel tasks

- [ ] 5B. **Deterministic dependency inference (the "infer before you run" step)**
  - [ ] 5B-1. `infer_dependencies(dag: PlanDAG) -> PlanDAG` — runs **after** LLM decomposition (task 5), **before** scheduling. Adds edges the LLM missed and removes redundant ones.
  - [ ] 5B-2. **Artifact-contract matching:** For every `InputRequirement` on task B, find all tasks whose `expected_outputs` produce a matching `OutputArtifact` (match by `name` exact or alias, then by `artifact_type`). If a producer exists and no edge producer→B exists, add the dependency edge (producer must complete before B starts) and set `producer_task_id` on the `InputRequirement`. Handles many-to-one (multiple consumers of one artifact) and ambiguous producers (multiple tasks produce same name → pick by topological order or flag for LLM disambiguation).
  - [ ] 5B-3. **Transitive reduction:** After artifact-matching adds edges, run transitive reduction to prune redundant edges (A→C is redundant if A→B→C exists). Keeps the DAG minimal for cleaner schedule display and prevents over-serialization.
  - [ ] 5B-4. **Template-based dependency patterns** (optional, v2): for known task archetypes (e.g., `literature_review` → `methodology`, `data_collection` → `data_cleaning` → `analysis`), encode canonical dependency chains as reusable patterns. When the LLM's decomposition matches a known archetype (by task name or description similarity), overlay the template edges. Useful for domains where the same task structure recurs (research, data science, software development).
  - [ ] 5B-5. **Experience-based dependency prediction** (optional, v2): from `ExperienceStore` (19-1), learn which task-type pairs frequently co-occur with a dependency edge in historical successful runs. If a pair exceeds a confidence threshold, suggest the edge. This is a lightweight learned prior, not a hard constraint — the pre-flight check (4-3) remains the safety net.
  - [ ] 5B-6. **Conflict detection:** Flag circular dependencies introduced by artifact matching (e.g., task A produces X consumed by B, and B produces Y consumed by A). Tiebreaker: preserve LLM-declared edges over inferred edges; among inferred edges, remove the one whose producer has the later topological position in the original LLM-declared ordering. Log a warning with the broken edge for diagnosis.

- [ ] 6. **Concierge caller path**
  - [ ] 6-1. When concierge builds a plan (solver plan mode, multi-step tasks), emit a `PlanDAG` instead of a flat task list. Wire through `PlanBuilder` (25-8).
  - [ ] 6-2. For concierge-driven execution: each `PlanTask` maps to a concierge sub-interaction (tool call, LLM call, workflow run). The scheduler determines which sub-interactions run in parallel.
  - [ ] 6-3. User-facing schedule display: show the Gantt-like schedule with critical path highlighted, estimated completion time, and parallelism utilization
  - [ ] 6-4. `/plan` command shows the current schedule; `/plan --replan` forces a full re-decomposition
  - [ ] 6-5. Progress: after each task completes, show updated schedule with revised ETA

- [ ] 7. **Workflow engine caller path**
  - [ ] 7-1. For workflow graphs, build an **acyclic planning view** at run start via `dag_from_graph(graph: Graph) -> PlanDAG`: use the native DAG when possible; otherwise condense loop/cycle regions into SCC supernodes and schedule within/across those boundaries. SCC supernode duration estimation: use `estimated_iterations * body_duration` where `estimated_iterations` defaults to the loop's `max_iterations` cap (or configurable fallback, default 3) and `body_duration` is the sum of the critical path through the loop body.
  - [ ] 7-2. v1 scope: schedule explicit DAG regions and SCC-condensed supernodes; do **not** attempt to reorder semantics inside a loop body beyond the engine's existing control-flow rules
  - [ ] 7-3. Use `PlanSchedule` to order node dispatch in the engine scheduler instead of (or in addition to) plain topological levels. This enables smarter batching when nodes have heterogeneous durations.
  - [ ] 7-4. For `ParallelSubagentsExecutor` branches: build a `PlanDAG` from branch sub-graphs, schedule the cross-branch dispatch order
  - [ ] 7-5. Respect `DAN_MAX_CONCURRENT_LLM` via existing `ResourceBudget` semaphore

- [ ] 8. **Execution events and progress**
  - [ ] 8-1. Emit progress events: `PLAN_TASK_STARTED`, `PLAN_TASK_COMPLETED`, `PLAN_RESCHEDULED`, `PLAN_DEPENDENCY_DISCOVERED`
  - [ ] 8-2. Event payload includes: updated critical path, current makespan estimate, parallelism utilization (active slots / max slots), calibration factor

- [ ] 9. **Tests and docs**
  - [ ] 9-1. Unit tests: DAG construction, acyclicity validation, critical path computation, exact scheduling (CP-SAT), LRP scheduling, auto-selection, dynamic rescheduling, calibration, pre-flight validation, dependency discovery
  - [ ] 9-2. Integration test: mock tasks with varying durations, verify parallel execution and makespan improvement over serial
  - [ ] 9-3. Benchmark: compare makespan of exact vs LRP vs serial for the golden decomposition examples; measure heuristic gap vs exact on sampled instances instead of requiring exact equality
  - [ ] 9-4. Dual-caller test: same `PlanDAG` scheduled via concierge path and workflow path produces identical schedule
  - [ ] 9-5. Update architecture, llm-api-guide, changelog

## Decisions

- **Exact-first, heuristic-fallback.** Default to OR-Tools CP-SAT exact solver for n ≤ 50 (covers 99% of real plans). CP-SAT solves n=50 in single-digit milliseconds — no reason not to be optimal. Fall back to LRP heuristic when `ortools` is not installed or n > 50 (`DAN_SCHEDULER_EXACT_THRESHOLD`).
- **One solver, two callers.** `plan_scheduler.py` is a standalone module in `dan.engine`. The concierge calls it for multi-step chat tasks. The workflow engine calls it for graph execution ordering. Same code path, same optimality guarantees. No duplication.
- **Workflow integration uses acyclic planning views.** The solver works on DAGs. For cyclic workflow graphs, v1 schedules SCC-condensed supernodes / DAG regions and leaves intra-loop semantics to the existing engine control flow.
- **Plans are hypotheses.** The scheduler is designed to re-run after every event (task completion, failure, new dependency). Even exact solving at n < 50 costs milliseconds — zero perceptible overhead for constant rescheduling.
- **Typed ports catch missing deps.** DAN's typed input/output schemas on every node enable mechanical pre-flight checks that purely text-based systems can't do. This is the key advantage over DynTaskMAS, GAP, and every other research system.
- **Three-stage dependency pipeline.** (1) LLM declares dependencies optimistically (task 5). (2) Deterministic inference adds edges from artifact contracts and prunes redundant ones (task 5B) — this is the upfront prediction step that purely LLM-based systems lack. (3) Pre-flight validation at runtime catches anything both stages missed (task 4-3). Each stage is independently valuable; together they converge on the true dependency graph quickly.

## Dependencies

- `ParallelSubagentsExecutor` (7-9) for parallel execution
- `ResourceBudget` (29-5) for concurrency limits
- `TierPolicy` (18-5) for model-tier assignment
- `PlanBuilder` (25-8) for plan construction
- `ExperienceStore` (19-1) for time estimation calibration
- `CheckpointStore` for pause/resume of tasks with discovered dependencies

## Primary Files

- `src/dan/engine/plan_scheduler.py` — standalone solver module (PlanTask, PlanDAG, PlanConstraints, PlanSchedule, compute_critical_path, schedule_tasks, on_task_complete)
- `src/dan/server/concierge/solver.py` — concierge caller path (PlanBuilder emits PlanDAG)
- `src/dan/engine/scheduler.py` — workflow engine caller path (dag_from_graph conversion)
- `tests/test_engine/test_plan_scheduler.py` — unit + integration tests

## Estimate

3-4 days

## References

- DynTaskMAS (ICAPS 2025) — dynamic task graph with async parallel execution, 21-33% speedup
- GAP (NeurIPS 2025 Spotlight) — graph-based agent planning with trained dependency awareness
- AdaptOrch (arXiv Feb 2026) — topology routing algorithm, O(|V|+|E|)
- LAMaS (2025) — critical-path latency optimization, 38-46% reduction

## Notes

- This is the most architecturally significant feature in this batch. It makes DAN the first production system with proper dependency-aware parallel scheduling + dynamic rescheduling.
- The scheduling algorithm itself is trivial (~50 lines). The deterministic dependency inference (task 5B) adds ~100 lines. The hard part is the LLM decomposition prompt and time estimation accuracy — but the three-stage pipeline (LLM → inference → pre-flight) means no single stage needs to be perfect.
- Multi-objective extension (minimize α·makespan + β·cost) is a natural follow-up but not in scope for v1.
