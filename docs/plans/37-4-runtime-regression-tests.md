# 37-4: Runtime Regression Tests

**Parent:** [37-engine-runtime-parallelism](37-engine-runtime-parallelism.md)
**Status:** not-started
**Goal:** Backfill engine-level test coverage for the scheduler, concurrency control, and checkpoint/resume. Currently zero tests exist for these code paths.

## Context

Existing test coverage:
- `tests/test_concierge/test_parallelism_integration.py` — 22 tests for concierge fan-out, ResourceTracker, PriorityQueue. Does **not** cover the engine scheduler.
- `tests/test_concierge/test_scheduler.py` — tests for `TaskScheduler` (cron/interval scheduling). Does **not** cover the graph execution scheduler.
- No tests exist for `_topological_levels`, `_execute`, `_guarded_execute_node`, `_run_subgraph`, `_save_checkpoint`, `_resolve_input_references`, or nested executor concurrency.

This plan creates a dedicated test module `tests/test_engine/test_scheduler_parallelism.py` covering the four categories below.

## Tasks

### 1. Dispatch ordering invariant tests
- [ ] 1-1. **Linear chain correctness** — 5-node chain A→B→C→D→E. Assert execution order is sequential (each node starts only after predecessor completes).
- [ ] 1-2. **Diamond graph** — A→{B,C}→D. Assert B and C run concurrently, D starts only after both B and C complete.
- [ ] 1-3. **Heterogeneous latency** — A→{B(slow),C(fast)}→D; A→{E(fast)}→F. With eager dispatch, F should start before B completes. With level-sync, F waits for B.
- [ ] 1-4. **Wide fan-out** — A→{B1..B10}→C. Assert all B nodes run concurrently (up to semaphore limit). C starts after all B nodes complete.
- [ ] 1-5. **Cycle/while-loop** — A→Gate(while)→Body→Gate. Assert body re-executes on continue, stops on done, respects `max_iterations`.
- [ ] 1-6. **Feature flag toggle** — same graph produces identical results with `DAN_EAGER_DISPATCH=0` and `DAN_EAGER_DISPATCH=1`.

### 2. Concurrency invariant tests
- [ ] 2-1. **Global semaphore cap** — 10 parallel nodes, `max_concurrency=3`. Assert at most 3 nodes are executing simultaneously (use a shared counter + assertion inside mock executor).
- [ ] 2-2. **Nested subgraph shares semaphore** — parent has 3 parallel branches, each running a 3-node subgraph. With `max_concurrency=5`, assert global in-flight count never exceeds 5.
- [ ] 2-3. **LLM semaphore vs node semaphore** — if split semaphores are implemented, assert LLM calls are bounded by `DAN_MAX_CONCURRENT_LLM` while node execution is not blocked.
- [ ] 2-4. **ForEach + global semaphore composition** — ForEach with 8 items, `parallelism=4`, global `max_concurrency=3`. Assert at most 3 items execute concurrently (global wins).
- [ ] 2-5. **No deadlock under nesting** — ParallelSubagents spawns ForEach spawns LLM nodes. Run to completion without hanging (timeout test, 30 s).
- [ ] 2-6. **Busy-poll elimination** — verify `_run_children_parallel` does not call `asyncio.sleep(0.01)` in the new implementation (or verify event latency is < 5 ms).

### 3. Checkpoint / resume tests
- [ ] 3-1. **Crash-resume equivalence** — run a 10-node graph, kill after node 5 completes, resume from checkpoint, assert final result matches a clean run.
- [ ] 3-2. **Checkpoint data completeness** — after checkpoint, load it and verify all expected fields are present (`completed_node_ids`, `node_outputs`, `pending_node_ids`).
- [ ] 3-3. **Resume re-dispatches in-progress nodes** — checkpoint taken while node X is in-flight. On resume, X is re-dispatched from scratch.
- [ ] 3-4. **Memory flush before checkpoint** — node writes memory key `foo`. Checkpoint is taken. Resume and verify `foo` is in memory store.
- [ ] 3-5. **Checkpoint backward compatibility** — load a checkpoint from the level-sync scheduler (no `pending_node_ids`), resume with eager dispatch. Assert success.
- [ ] 3-6. **Cycle region checkpoint** — checkpoint mid-iteration of a while-loop. Resume and verify the loop completes correctly from the right iteration.
- [ ] 3-7. **Subgraph progress on crash** — parallel subagents with 3 branches. Branch 1 completes, crash, resume. If 37-3 task 6-1 enables subgraph checkpoints: assert branch 1 is not re-run. If subgraph checkpoints remain skipped: assert all 3 branches re-run (document this as expected behavior, not a failure).
- [ ] 3-8. **Skipped-node successor dispatch** — graph with a conditional branch (ControlEdge). One branch is skipped. Assert downstream nodes that depend on the skipped branch's successors still dispatch correctly (skipped nodes decrement successors).

### 4. Performance benchmark tests
- [ ] 4-1. **Latency benchmark** — 3-level diamond graph with heterogeneous node latencies (mock sleep). Measure wall-clock time with eager vs level-sync. Assert eager is ≥20% faster.
- [ ] 4-2. **Throughput benchmark** — 50-node graph with `max_concurrency=10`. Measure time to complete. Assert eager dispatch achieves ≥80% of theoretical throughput (sum of node durations / max_concurrency).
- [ ] 4-3. **Checkpoint overhead** — 20-node graph. Measure time with checkpointing enabled vs disabled. Assert overhead is < 15% of total run time.
- [ ] 4-4. **Memory usage** — run a 100-node graph and verify peak memory stays within 2x of a 10-node graph (no per-node memory leak).

### 5. Test infrastructure
- [ ] 5-1. Create `tests/test_engine/test_scheduler_parallelism.py`.
- [ ] 5-2. Create test fixtures: `MockExecutor` that records call times and supports configurable sleep, `MockCheckpointStore` that captures checkpoint data, `simple_graph_builder` helper that creates test graphs without the full builder DSL.
- [ ] 5-3. Use `pytest.mark.asyncio` and generous timing margins (≥2x expected) to avoid flakiness, matching the pattern in `test_parallelism_integration.py`.
- [ ] 5-4. Performance benchmarks should be `pytest.mark.benchmark` or at minimum `pytest.mark.slow` so they don't run in the fast CI path.

## Decisions

- (filled in during execution)

## Notes

- Tests should use mock executors (configurable sleep), not real LLM calls. The goal is to test scheduling logic, not LLM behavior.
- The crash-resume test (3-1) can simulate a crash by running the engine with a mock executor that raises after N completions, then resuming from the checkpoint store.
- Performance benchmarks are advisory, not blocking. They establish a baseline and detect regressions but should not fail CI on small fluctuations.
- Consider reusing the `fan_out` test patterns from `test_parallelism_integration.py` (timing-based concurrency assertions with generous margins).
- Test 3-7 has a dependency on 37-3 task 6-1 (subgraph checkpoint decision). Write the test to handle both outcomes (checkpoints enabled vs skipped) so it doesn't break regardless of the decision.
- Test graphs need to include `ControlEdge` scenarios (conditional branching) and `ContextEdge` scenarios (shared context reads/writes) in addition to `DataEdge` dependency chains, since the eager dispatch must correctly handle all three edge types.
