# 31-8: Plan Dependency Optimization

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** completed
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

- [x] 1. **Standalone scheduler module (`src/dan/engine/plan_scheduler.py`)**
  - [x] 1-1. `PlanTask` Pydantic model: `id: str`, `name: str`, `description: str`, `estimated_duration_minutes: float`, `dependencies: list[str]` (predecessor task IDs), `required_inputs: list[InputRequirement]`, `expected_outputs: list[OutputArtifact]`, `model_tier: str | None` (override), `priority: int = 0`
  - [x] 1-2. `InputRequirement` / `OutputArtifact` models: structured artifact contracts (`name`, `artifact_type`, `schema_hint`, `producer_task_id | None`) so dependency repair is mechanical instead of text-only
  - [x] 1-3. `PlanDAG` model: `tasks: list[PlanTask]`, `goal: str`, `constraints: PlanConstraints` — validates acyclicity on construction, computes topological order. For workflow-engine callers, the solver operates on an **acyclic planning graph** (native DAG or SCC-condensed graph), not arbitrary raw cyclic graphs.
  - [x] 1-4. `PlanConstraints` model: `max_parallel: int` (from `DAN_MAX_CONCURRENT_LLM`), `budget_dollars: float | None`, `deadline_minutes: float | None`, `quality_floor: Literal["micro", "routine", "reasoning", "critical"] = "routine"`
  - [x] 1-5. `PlanSchedule` model: `assignments: list[ScheduleAssignment]` — each assignment: `task_id`, `start_time`, `end_time`, `assigned_tier`, `is_critical_path: bool`, `slack: float`
  - [x] 1-6. **This module is the single solver — both concierge-driven tasks and workflow engine call it.** No duplication. Concierge builds a `PlanDAG` from user intent; workflow engine builds one from an acyclic execution view of graph topology. Both call `schedule_tasks()`.

- [x] 2. **Critical path solver**
  - [x] 2-1. Forward pass: compute earliest start/finish for each task
  - [x] 2-2. Backward pass: compute latest start/finish, identify slack
  - [x] 2-3. Critical path extraction: tasks with zero slack
  - [x] 2-4. `compute_critical_path(dag: PlanDAG) -> PlanSchedule` — returns schedule with unlimited parallelism (lower bound on makespan)

- [x] 3. **Resource-constrained scheduler (RCPSP)**
  - [x] 3-1. **Exact solver (default for n ≤ 50):** OR-Tools CP-SAT solver — provably optimal schedule. For typical plan sizes (5-50 tasks), CP-SAT solves in milliseconds. `ortools` as optional dep with `try: from ortools... except Exception` guard (broad catch handles protobuf version mismatches); graceful fallback to heuristic if not installed. Add a test that verifies LRP fallback fires when `ortools` is mocked as unavailable.
  - [x] 3-2. **LRP heuristic (fallback, or n > 50):** Longest-Remaining-Path-First list scheduling: O(n log n + e), ~50 lines. Used when `ortools` unavailable or task count exceeds threshold (`DAN_SCHEDULER_EXACT_THRESHOLD`, default 50).
  - [x] 3-3. `schedule_tasks(dag: PlanDAG, constraints: PlanConstraints) -> PlanSchedule` — auto-selects exact vs heuristic based on task count and `ortools` availability. Returns feasible schedule respecting `max_parallel`.
  - [x] 3-4. Model-tier assignment: critical-path tasks get higher tier (reasoning/critical), high-slack tasks get lower tier (micro/routine) — integrates with existing `TierPolicy`

- [x] 4. **Dynamic rescheduling**
  - [x] 4-1. `on_task_complete(task_id, actual_duration)` — remove completed task, re-run scheduler on remaining DAG. Re-run is microseconds (exact) or sub-microseconds (heuristic) — call after every single event.
  - [x] 4-2. Duration calibration: compute `calibration_factor = mean(actual/estimated)` for completed tasks, scale remaining estimates. Recalibrate on every completion.
  - [x] 4-3. **Pre-flight input validation** (the "validate mechanically" step): `validate_preflight()` checks `required_inputs` against completed predecessors' `expected_outputs`. Returns missing dependency descriptions for caller to add edge + reschedule.
  - [x] 4-4. Mid-execution dependency: `handle_mid_execution_dependency()` cancels running task, adds dependency edge, flags `cancelled_for_dependency` for re-run. v2 follow-up: true pause/resume via coroutine checkpointing for long tool executions.
  - [x] 4-5. New task insertion: if execution discovers an unplanned subtask is needed, insert into DAG and reschedule. DAG mutation is O(1); rescheduling is the same O(n log n) or CP-SAT call.

- [x] 5. **LLM planning prompt (the "plan optimistically" step)** — in `plan_prompts.py`
  - [x] 5-1. `DECOMPOSITION_PROMPT` template: goal → JSON task array, biased toward independence
  - [x] 5-2. `FEW_SHOT_EXAMPLES`: 5 golden decompositions (research report, code refactor, data pipeline, Kaggle competition, equity analysis) with correct dependency edges
  - [x] 5-3. `VALIDATION_PROMPT`: DAG review for missing/redundant edges, parallelism opportunities, estimate concerns
  - [x] 5-4. `estimate_from_experience()`: queries experience store for similar past tasks, averages `avg_elapsed_seconds`, falls back to None for LLM heuristic

- [x] 5B. **Deterministic dependency inference (the "infer before you run" step)**
  - [x] 5B-1. `infer_dependencies(dag: PlanDAG) -> PlanDAG` — runs **after** LLM decomposition (task 5), **before** scheduling. Adds edges the LLM missed and removes redundant ones.
  - [x] 5B-2. **Artifact-contract matching:** For every `InputRequirement` on task B, find all tasks whose `expected_outputs` produce a matching `OutputArtifact` (match by `name` exact or alias, then by `artifact_type`). If a producer exists and no edge producer→B exists, add the dependency edge (producer must complete before B starts) and set `producer_task_id` on the `InputRequirement`. Handles many-to-one (multiple consumers of one artifact) and ambiguous producers (multiple tasks produce same name → pick by topological order or flag for LLM disambiguation).
  - [x] 5B-3. **Transitive reduction:** After artifact-matching adds edges, run transitive reduction to prune redundant edges (A→C is redundant if A→B→C exists). Keeps the DAG minimal for cleaner schedule display and prevents over-serialization.
  - [x] 5B-4. **Template-based dependency patterns**: `DEPENDENCY_TEMPLATES` (5 domain patterns: research_report, code_refactor, data_pipeline, ml_experiment, equity_analysis), `apply_template_deps()` with fuzzy name matching and cycle prevention
  - [x] 5B-5. **Experience-based dependency prediction**: `predict_deps_from_experience()` learns task-type pairs from experience store, suggests edges above confidence threshold
  - [x] 5B-6. **Conflict detection:** Flag circular dependencies introduced by artifact matching (e.g., task A produces X consumed by B, and B produces Y consumed by A). Tiebreaker: preserve LLM-declared edges over inferred edges; among inferred edges, remove the one whose producer has the later topological position in the original LLM-declared ordering. Log a warning with the broken edge for diagnosis.

- [x] 6. **Concierge caller path**
  - [x] 6-1. When concierge builds a plan (solver plan mode, multi-step tasks), emit a `PlanDAG` instead of a flat task list. Wire through `PlanBuilder` (25-8).
  - [x] 6-2. `execute_plan_tasks()`: async dispatch via concierge with semaphore-based parallelism, topological ordering, concurrent batch execution
  - [x] 6-3. `format_schedule_display()`: Gantt-like text with critical path highlighted (█), non-critical (░), tier labels, parallelism utilization
  - [x] 6-4. `handle_plan_command()` registered as `/plan [--replan]` in command registry
  - [x] 6-5. `format_progress_update()`: completion count, done/pending status per task, revised ETA

- [x] 7. **Workflow engine caller path**
  - [x] 7-1. `dag_from_graph()`: builds acyclic PlanDAG from Graph via Tarjan SCC condensation; loop/cycle regions become supernodes
  - [x] 7-2. `schedule_dag_regions()`: schedules DAG regions and SCC supernodes; leaves intra-loop ordering to existing engine control flow
  - [x] 7-3. `PlanSchedule` enables smarter batching when nodes have heterogeneous durations
  - [x] 7-4. `build_branch_dag()`: builds PlanDAG from ParallelSubagentsExecutor branch sub-graphs
  - [x] 7-5. `apply_resource_budget()`: clamps `max_parallel` to `DAN_MAX_CONCURRENT_LLM` via ResourceBudget

- [x] 8. **Execution events and progress**
  - [x] 8-1. Pydantic event models: `PlanTaskStarted`, `PlanTaskCompleted`, `PlanRescheduled`, `PlanDependencyDiscovered`
  - [x] 8-2. Event payloads: critical path, makespan estimate, parallelism utilization, calibration factor, actual duration, success/failure, cancelled task ID

- [x] 9. **Tests and docs**
  - [x] 9-1. Unit tests: 76 tests in `test_plan_scheduler.py` (73 passed, 3 skipped for OR-Tools) — DAG construction, acyclicity, critical path, LRP/exact scheduling, auto-selection, dynamic rescheduling, calibration, dependency inference, transitive reduction, conflict detection, preflight validation, mid-execution dependency, format display, event models, resource budget
  - [x] 9-2. Integration test: `test_plan_integration.py` — mock tasks with varying durations, async parallel execution, failure handling, simulated fallback
  - [x] 9-3. Benchmark: serial vs parallel vs LRP vs critical path for benchmark DAG and golden examples (research report, Kaggle); verifies parallel beats serial
  - [x] 9-4. Dual-caller test: identical schedules, deterministic across 5 runs, critical paths match
  - [x] 9-5. Updated architecture.md, changelog.md, todo.md, plan file

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

- `src/dan/engine/plan_scheduler.py` — standalone solver module (models, scheduling, validation, execution, events)
- `src/dan/engine/plan_prompts.py` — LLM planning prompts, few-shot examples, templates, experience estimation
- `src/dan/server/concierge/command_registry.py` — `/plan` command registration
- `tests/test_engine/test_plan_scheduler.py` — 76 unit tests
- `tests/test_engine/test_plan_integration.py` — 25 integration tests

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
