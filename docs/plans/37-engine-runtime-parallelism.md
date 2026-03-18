# 37: Engine Runtime Parallelism

**Status:** in-progress
**Goal:** Replace the level-sync barrier in the graph scheduler with eager/ready-queue dispatch, unify concurrency budgeting across nested subgraphs, harden checkpoint/resume semantics for the new model, backfill engine-level regression tests, and extend that substrate into long-running/tail-workflow runtime robustness.

## Motivation

The engine scheduler (`src/dan/engine/scheduler.py`) groups nodes into topological levels via Kahn's algorithm and executes each level with `asyncio.gather()`. This is clean but leaves significant latency on the table: if level N has 5 nodes and 4 finish in 1 s but 1 takes 30 s, all downstream nodes that only depend on the fast 4 wait 29 s unnecessarily.

Additional issues compound the throughput ceiling:

1. **Level-sync barrier** — `await asyncio.gather(*tasks)` at L883, L1014, L1968 forces full-level completion before any next-level node starts.
2. **Semaphore at wrong granularity** — `global_sem` wraps entire node execution (`_guarded_execute_node`), holding the slot through the I/O wait of LLM API calls.
3. **Fresh semaphore per subgraph** — `_run_subgraph` (L1949-1951) creates a new `asyncio.Semaphore(max_concurrency)` instead of inheriting the parent's, so N parallel branches each get the full budget.
4. **Per-level checkpoint is synchronous** — `_save_checkpoint` + `_flush_memory_writes` run between every level, adding serial overhead proportional to the number of levels.
5. **Sequential reference resolution** — `_resolve_input_references` resolves all artifact refs synchronously before node execution.
6. **No adaptive token budget for parallel siblings** — each node gets its own budget with no global coordination. `TokenBudgetAdvisor` tracks usage but doesn't coordinate across concurrent siblings. (Low impact — addressed as a note, not a sub-plan task.)
7. **Busy-poll in `_run_children_parallel`** — `tier_executors.py` L886-902 polls per-child queues with `asyncio.sleep(0.01)`. (Concierge-side code, included in 37-2 for completeness since it's a parallelism bottleneck.)

**Out of scope:** LLM call batching across parallel nodes (provider APIs are per-call; the OpenAI batch API is offline-only and not applicable to interactive workflows).

Plan 29-5 (concierge parallelism) explicitly scoped engine work out. Plan 32 (workflow generation optimization) says engine-level improvements are separate backlog items. This plan covers the engine execution path, plus one concierge-side fix (busy-poll) that directly affects parallel execution latency.

## Approach

Seven sub-plans, ordered by dependency:

1. **Ready-queue dispatch** — the single biggest latency win. Replace level-sync with eager dispatch where nodes fire as soon as all inbound edges are satisfied.
2. **Concurrency budgeting** — unify the semaphore across nested subgraphs and move it to LLM-call granularity so slots aren't wasted on I/O waits.
3. **Checkpoint/resume safety** — adapt checkpointing to work with non-level-aligned completion order and ensure resume produces equivalent results.
4. **Runtime regression tests** — backfill engine-level tests covering dispatch ordering, concurrency caps, checkpoint/resume, and performance benchmarks.
5. **Long-running workflow robustness** — add per-run execution profiles, hard duration/cost ceilings, partial-result semantics, tighter checkpoint cadence, and structured progress/ETA data for multi-hour workflows.
6. **Bounded runtime self-healing** — add execution-local repair paths, repair lineage, and safe pending-node overlays without opening the door to structural/redesign autonomy during normal runs.
7. **Validated dynamic topology & child workflow composition** — add engine-owned, schema-validated child workflow spawning/composition with lineage, budget inheritance, and checkpoint-safe execution overlays.

## Existing Infrastructure

| Component | Location | Relevance |
|---|---|---|
| `_topological_levels` | `engine/scheduler.py` L90-121 | Kahn's algorithm — replaced by ready-queue |
| `_topological_levels_with_backedges` | `engine/scheduler.py` L132-258 | Cycle detection — must be preserved |
| `_execute` (main loop) | `engine/scheduler.py` L714-962 | Level-sync loop — rewritten |
| `_guarded_execute_node` | `engine/scheduler.py` L964-977 | Semaphore wrapper — changed to LLM-call level |
| `_run_subgraph` | `engine/scheduler.py` L1856-1986 | Fresh semaphore — changed to inherit parent |
| `_save_checkpoint` | `engine/scheduler.py` L2062-2113 | Per-level — changed to per-node or batched |
| `_flush_memory_writes` | `engine/scheduler.py` L2016-2061 | Per-level — changed to batched |
| `_resolve_input_references` | `engine/scheduler.py` L1729-1736 | Synchronous — optionally parallelized |
| `ParallelSubagentsExecutor` | `executors/control_flow.py` L452-554 | Local semaphore per branch |
| `ForEachExecutor` | `executors/control_flow.py` L700-804 | Local semaphore per item |
| `VoteExecutor` | `executors/control_flow.py` L2084-2199 | Local semaphore per vote |
| `_run_children_parallel` | `server/concierge/tier_executors.py` L863-909 | Busy-poll loop |
| `execute_plan_tasks` | `engine/plan_scheduler.py` L906-997 | Plan-level scheduling (already has ready-queue pattern — reuse as reference) |
| `TokenBudgetAdvisor` | `engine/token_optimization.py` L925-977 | Advisory per-node budgets |
| `ResourceTracker` | `server/concierge/resources.py` L43-102 | Concierge-side resource tracking |

## Sub-Plans

| # | Sub-Plan | Scope | Effort | Dependencies |
|---|----------|-------|--------|--------------|
| [37-1](37-1-ready-queue-dispatch.md) | Ready-Queue Dispatch | Replace level-sync with dependency-tracking eager dispatch in `_execute`, `_execute_with_cycles`, `_run_subgraph` | ~3 days | None (foundational) |
| [37-2](37-2-concurrency-budgeting.md) | Concurrency Budgeting | Propagate parent semaphore into subgraphs, move semaphore to LLM-call granularity, fix busy-poll | ~2 days | 37-1 |
| [37-3](37-3-checkpoint-resume-safety.md) | Checkpoint & Resume Safety | Adapt checkpoint triggers, memory flushes, and resume logic for non-level-aligned execution | ~2 days | 37-1 |
| [37-4](37-4-runtime-regression-tests.md) | Runtime Regression Tests | Engine-level test suite: dispatch ordering, concurrency invariants, checkpoint/resume, performance benchmarks | ~2 days | 37-1, 37-2, 37-3 |
| [37-5](37-5-long-running-workflow-robustness.md) | Long-Running Workflow Robustness | Per-run execution profiles, hard duration/cost ceilings, partial results, stronger checkpoint cadence, structured progress | ~2-3 days | 37-3, 37-4 |
| [37-6](37-6-runtime-self-healing-and-adaptation.md) | Bounded Runtime Self-Healing & Pending-Node Adaptation | Runtime failure classification, bounded repair matrix, repair lineage, pending-node overlays | ~3 days | 37-3, 37-4 |
| [37-7](37-7-dynamic-topology-and-workflow-composition.md) | Validated Dynamic Topology & Child Workflow Composition | Constrained runtime expansion, child workflow operator, typed handoff, lineage, resume-safe execution overlays | ~3-4 days | 37-5, 37-6 |

## Dependencies / Sequencing

```
37-1 (Ready-Queue Dispatch) ← foundational, start here
  ├→ 37-2 (Concurrency Budgeting) ← needs new dispatch loop
  ├→ 37-3 (Checkpoint/Resume Safety) ← needs new dispatch loop
  └→ 37-4 (Runtime Regression Tests) ← needs all three above
       ├→ 37-5 (Long-Running Workflow Robustness) ← needs stable checkpoint/resume + test harness
       ├→ 37-6 (Runtime Self-Healing & Adaptation) ← needs stable checkpoint/resume + test harness
       └→ 37-7 (Dynamic Topology & Workflow Composition) ← after 37-5 and 37-6
```

37-2 and 37-3 can run in parallel after 37-1 completes.
37-4 waits for all three (tests verify the combined behavior).
37-5 and 37-6 can proceed in parallel once 37-3/37-4 have stabilized the runtime substrate.
37-7 should start only after the long-running durability and bounded runtime-repair semantics are explicit.

## Success Criteria

- [ ] End-to-end latency reduction on heterogeneous graphs (target: ≥30% on a 3-level graph where one node per level is 10x slower)
- [ ] Nested subgraph concurrency respects a single global budget (no multiplication)
- [ ] Checkpoint/resume produces identical results with the new scheduler
- [ ] Feature flag `DAN_EAGER_DISPATCH=1` (default off initially) to safely roll out
- [ ] ≥40 engine-level tests covering dispatch, concurrency, checkpoint/resume
- [ ] No regressions in existing test suite (810+ concierge tests, 92+ quality suite)
- [ ] `plan_scheduler.py` ready-queue pattern reused or aligned with engine scheduler
- [ ] Per-run long-running workflow profiles can stop safely on duration/cost ceilings and return resumable partial results
- [ ] Runtime self-healing remains bounded, observable, and checkpoint-safe without auto-escalating into structural/redesign autonomy
- [ ] Dynamic child workflow composition stays schema-validated, budget-aware, and deterministic across checkpoint/resume

## Decisions

- Eager dispatch now covers acyclic graphs, nested subgraphs, cycle re-iterations, outer cycle-aware scheduling, the concierge-facing `plan_scheduler.execute_plan_tasks()` path, and the slow benchmark/leak guard coverage for the scheduler runtime.
- The legacy `max_concurrency` node semaphore is preserved only when eager dispatch is off. Under eager dispatch, `DAN_MAX_CONCURRENT_LLM` / `llm_max_concurrency` governs shared LLM-call concurrency instead.
- CPU-bound throttling no longer lives in `_guarded_execute_node()`; it now routes through `ExecutionContext.node_slot()` so eager mode can keep a separate node semaphore without reintroducing whole-node LLM stalls.
- Subgraph checkpoints stay disabled in this phase; a checkpoint can record only the still-running parent control-flow node, so resume restarts child subgraphs from their entry boundary instead of attempting partial nested restore.
- The original parallelism tranche (37-1 through 37-4) is the substrate. 37-5 through 37-7 extend that same runtime path into long-tail workflow robustness rather than reopening workflow generation or concierge-planning scope.

## Notes

- `plan_scheduler.py` already implements a ready-queue pattern (L963-995) with in-degree tracking and completion-driven dispatch. This is the reference implementation for 37-1.
- The engine scheduler also handles cycle regions (while-loop back-edges). The ready-queue must respect cycle boundaries: nodes inside a cycle region should not eagerly dispatch until the gate node releases them.
- Feature flag rollout is critical. The level-sync scheduler is battle-tested; the eager scheduler needs time to prove equivalence before becoming default.
- This plan does not touch concierge parallelism (29-5) or workflow generation (32). It is strictly engine execution path.
- Implemented so far: eager dispatch for acyclic graphs/subgraphs plus cycle-aware outer/inner loop execution, shared LLM semaphore propagation into nested subgraphs, re-entrant node-slot throttling for CPU-bound work, timeout/depth guards for nested subgraphs, batched/background checkpoint saves with compatibility fields, deferral of memory writes from still-running owner scopes, resumed loop re-entry from checkpointed gate/body state, and a 42-test engine regression suite (`tests/test_engine/test_scheduler_parallelism.py` with 39 tests = 35 default-path + 4 slow benchmarks, plus `tests/test_engine/test_plan_scheduler_execution.py` with 3 plan-scheduler alignment tests) plus concierge busy-poll proof.
- 37-5 through 37-7 intentionally stay bounded: no arbitrary runtime graph mutation, no hidden persistence of execution-local overlays back into saved workflows, and no engine-owned free-form redesign loop during ordinary runs.
