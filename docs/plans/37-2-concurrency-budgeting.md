# 37-2: Concurrency Budgeting

**Parent:** [37-engine-runtime-parallelism](37-engine-runtime-parallelism.md)
**Status:** completed
**Goal:** Unify concurrency control across nested subgraphs, move the semaphore to LLM-call granularity, and eliminate the busy-poll pattern in tier executors.

## Context

Three related problems in the current concurrency model:

### Fresh semaphore per subgraph
`_run_subgraph` (scheduler.py L1949-1951) creates a new semaphore:
```python
global_sem: asyncio.Semaphore | None = None
if self.config.max_concurrency is not None and self.config.max_concurrency > 0:
    global_sem = asyncio.Semaphore(self.config.max_concurrency)
```

When `ParallelSubagentsExecutor` (control_flow.py L479) spawns N branches, each calling `context.run_subgraph(...)`, each subgraph gets its own full-budget semaphore. With `max_concurrency=5` and 3 branches, the effective limit is 15, not 5.

### Semaphore wraps entire node, not LLM call
`_guarded_execute_node` (L964-977) holds the semaphore for the full duration of node execution. An LLM node spends most of its time in I/O wait, blocking the slot for other nodes that could start.

### Busy-poll in `_run_children_parallel`
`tier_executors.py` L886-902 polls per-child queues with `asyncio.sleep(0.01)`, burning CPU and adding 10 ms latency per event.

## Tasks

### 1. Propagate parent semaphore into subgraphs
- [x] 1-1. Add `parent_semaphore: asyncio.Semaphore | None` parameter to `_run_subgraph`.
- [x] 1-2. When `parent_semaphore` is provided, use it instead of creating a new one. Only create a fresh semaphore at the top-level `_execute` call.
- [x] 1-3. Thread the semaphore through `ExecutionContext` so executors like `ParallelSubagentsExecutor`, `ForEachExecutor`, and `VoteExecutor` can pass it down when calling `context.run_subgraph()`.
- [x] 1-4. Control-flow executors' local semaphores (`node.parallelism`) should compose with the global semaphore: acquire both (global first, then local) to prevent deadlock.

### 2. Move semaphore to LLM-call granularity
- [x] 2-1. Remove the semaphore from `_guarded_execute_node`. Let nodes start executing freely.
- [x] 2-2. Add a `llm_semaphore` field to `ExecutionContext` (or `EngineConfig`). The LLM call site — `LLMExecutor._call_via_provider` (llm.py L1167-1252) — acquires this semaphore before making the API call and releases it when the response arrives.
- [x] 2-3. For streaming LLM calls (`_call_via_provider` with `stream=True`), the semaphore must be held for the entire stream duration, not just the initial request. The slot is released when the stream is fully consumed (or errors/times out). This prevents stream connections from being invisible to the concurrency budget.
- [x] 2-4. Keep a separate `node_semaphore` for CPU-bound work (optional, can start with just the LLM semaphore).
- [x] 2-5. `DAN_MAX_CONCURRENT_LLM` env var controls the LLM semaphore size. `max_concurrency` on `EngineConfig` controls the node-level semaphore (if retained).
- [x] 2-6. Ensure `ResourceTracker` in concierge (resources.py) and the engine's LLM semaphore don't conflict — they operate at different layers (concierge = cross-run, engine = within-run).

### 3. Fix busy-poll in tier executors
- [x] 3-1. Replace the `while active` + `get_nowait()` + `asyncio.sleep(0.01)` loop in `_run_children_parallel` with `asyncio.Queue.get()` (blocking) or `asyncio.wait()` on child tasks.
- [x] 3-2. Preserve cancellation semantics: `_cancel_requested(session)` must still be checked.
- [x] 3-3. Preserve event ordering: events from different children can interleave but each child's events must be in order.

### 4. Nested executor composability
- [x] 4-1. Verify `ParallelSubagentsExecutor` + `ForEachExecutor` nesting works: a ForEach inside a parallel branch should share the global semaphore.
- [x] 4-2. Verify `VoteExecutor` (which creates its own local semaphore at L2107) composes correctly with the global semaphore.
- [x] 4-3. Add a maximum-depth guard to prevent deeply nested subgraphs from deadlocking on the global semaphore (all slots held by outer frames waiting for inner frames). Consider a `timeout` on semaphore acquisition.

## Decisions

- Shared LLM concurrency uses a dedicated semaphore threaded through `ExecutionContext` and child subgraphs.
- `_guarded_execute_node()` is now only a scheduler handoff: CPU-bound throttling moved into `ExecutionContext.node_slot()` so the scheduler no longer serializes whole nodes at dispatch time.
- The node-level semaphore remains separate from the LLM semaphore and is now available in eager mode too, but it only wraps CPU-bound node execution paths and direct vote fan-out calls rather than LLM waits.
- Subgraph fan-out composes by sharing the parent `node_semaphore` / `llm_semaphore`; direct vote fan-out explicitly acquires `global node slot -> local vote semaphore`, and child subgraph depth is capped with a fail-fast runtime guard.

## Notes

- The two-semaphore approach (global for LLM calls, local per control-flow node for fan-out width) is the cleanest design. The global semaphore prevents runaway API concurrency; the local semaphore prevents a single ForEach from monopolizing all slots.
- Deadlock risk: if the global semaphore has N=5 slots and a ForEach with 5 items each needs 1 LLM call, they can all acquire concurrently. But if each item needs 2 LLM calls, the first round of 5 acquires fills the semaphore and the second round blocks — this is correct backpressure, not deadlock. True deadlock requires nested semaphore acquisition in inconsistent order. Task 4-3 guards against this.
- The concierge's `ResourceTracker.try_acquire("llm")` is advisory and non-blocking. The engine's semaphore is mandatory and blocking. They serve different purposes and should coexist.
- Task 3 (busy-poll fix) targets `tier_executors.py`, which is concierge-side code, not engine code. It is included here because the busy-poll directly affects parallel execution latency for any workflow that uses the tiered dispatch path. The fix is self-contained and does not depend on 37-1.
- Implemented in this slice: `llm_semaphore` on `ExecutionContext`, re-entrant `node_slot()` with timeout-backed acquisition, semaphore propagation through `_make_context()` / `_run_subgraph()`, LLM-slot wrapping in `LLMExecutor`, `VoteExecutor`, router/orchestrator control-flow calls, `ReflectionExecutor`, `RAGExecutor`, the tiered-dispatch busy-poll replacement, scheduler removal of dispatch-time node wrapping in `_guarded_execute_node()`, eager-mode CPU-bound node throttling, explicit vote/ForEach/nested-subgraph semaphore regressions, and a fail-fast max-subgraph-depth guard.
