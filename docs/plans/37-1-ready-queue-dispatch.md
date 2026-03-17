# 37-1: Ready-Queue Dispatch

**Parent:** [37-engine-runtime-parallelism](37-engine-runtime-parallelism.md)
**Status:** not-started
**Goal:** Replace the level-synchronous `asyncio.gather()` barrier in the engine scheduler with an eager/ready-queue model where nodes dispatch as soon as all inbound edges are satisfied.

## Context

The current scheduler groups nodes into topological levels and runs each level as a batch:

```python
for level in levels:
    tasks = [self._guarded_execute_node(nid, ...) for nid in ready]
    await asyncio.gather(*tasks)        # ← barrier
    await self._flush_memory_writes(...)
    await self._save_checkpoint(...)
```

This appears in three places:
- `_execute` (L870-900) — main acyclic path
- `_execute_with_cycles` (L1002-1022) — cycle-aware path
- `_run_subgraph` (L1955-1971) — subgraph execution

`plan_scheduler.py` (L906-997) already has a ready-queue implementation with in-degree tracking and completion-driven dispatch. That is the reference pattern.

## Tasks

### 1. Dependency tracker data structure
- [ ] 1-1. Create `_DependencyTracker` class (or inline dict) that maintains per-node in-degree counts from data edges, a ready set, and a completion callback that decrements successors.
- [ ] 1-2. Initialize from `Graph` edges. Primary dependency edges are `DataEdge` (same as `_topological_levels`). `ControlEdge` with conditions does not affect in-degree — it is handled at execution time by `_should_skip` (L1673-1710), which checks branch conditions and gate port data availability.
- [ ] 1-3. Neither `ContextEdge(mode="read")` nor `ContextEdge(mode="write")` blocks dispatch — reads are resolved at execution time in `_read_context_edges`, writes happen on the output side in `_write_context_edges`.
- [ ] 1-4. Handle nodes with zero in-degree (entry points) — seed the ready set.
- [ ] 1-5. Skipped nodes must still "complete" in the tracker: `_execute_node` (L1200-1206) may mark a node as `SKIPPED` via `_should_skip`. The tracker must treat SKIPPED the same as COMPLETED for successor decrement purposes, otherwise downstream nodes never become ready.

### 2. Eager dispatch loop (acyclic path)
- [ ] 2-1. Replace the `for level in levels` loop in `_execute` with a while-loop that pops from the ready set, dispatches via `_guarded_execute_node`, and on completion decrements successors. Use `asyncio.create_task` + a done callback or `asyncio.wait(return_when=FIRST_COMPLETED)`.
- [ ] 2-2. Preserve halt semantics: if `_check_halt(state)` is true after any node completes, stop dispatching and cancel pending tasks.
- [ ] 2-3. Preserve error isolation: a failed node should not prevent unrelated downstream paths from running (same as current `gather` behavior with `return_exceptions`).
- [ ] 2-4. Guard with feature flag `DAN_EAGER_DISPATCH` env var (default `"0"`). When off, fall back to existing level-sync loop.

### 3. Cycle-aware dispatch
- [ ] 3-1. Adapt `_execute_with_cycles` to use the ready-queue model. Nodes inside a `cycle_region` for a given gate must respect the gate's iteration: they should not re-dispatch until the gate releases them (back-edge fires).
- [ ] 3-2. Gate nodes in `while` mode: when the gate outputs on the `continue`/`loop` port, reset ALL nodes in the cycle region to PENDING (matching `_iterate_cycle` L1118-1120: `state.port_data.clear_node(cn); state.mark(cn, NodeStatus.PENDING)`), inject continue_data into the back-edge target's virtual input ports (L1122-1126), and re-seed the back-edge target node into the ready set. The tracker must recompute in-degree for cycle-region nodes each iteration.
- [ ] 3-3. Gate nodes outputting on `done`/`exit`/`false`: mark cycle region as complete, do not re-seed. Preserve the state_schema final-scope writeback and artifact merge logic from `_iterate_cycle` (L1163-1174).
- [ ] 3-4. Preserve `max_iterations` cap from current `_iterate_cycle`.
- [ ] 3-5. Preserve feedback selector and artifact port extraction logic (`_apply_feedback_selector`, `artifact_ports`) from `_iterate_cycle` L1102-1116.

### 4. Subgraph dispatch
- [ ] 4-1. Apply the same ready-queue model inside `_run_subgraph` (L1955-1971).
- [ ] 4-2. Share the feature flag — if `DAN_EAGER_DISPATCH` is off, subgraphs also use level-sync.

### 5. Plan scheduler alignment
- [ ] 5-1. Verify `execute_plan_tasks` (plan_scheduler.py L906-997) remains correct and compatible. It already uses a ready-queue pattern; no changes expected but confirm no regressions.

### 6. Ordering guarantees
- [ ] 6-1. Document which ordering guarantees change: within a former "level", nodes may now complete in any order.
- [ ] 6-2. Ensure deterministic tie-breaking for nodes that become ready simultaneously (sorted by node_id, matching current `sorted(queue)` in Kahn's).
- [ ] 6-3. Verify that `_write_context_edges` still works correctly when nodes from different former levels are in-flight simultaneously.

## Decisions

- (filled in during execution)

## Notes

- The `_topological_levels_with_backedges` function is still needed to identify back-edges and cycle regions. The ready-queue replaces how we *dispatch* within levels, not how we *detect* cycles.
- `_topological_levels` (the simpler version) can be deprecated once eager dispatch is stable, but keep it around behind the feature flag.
- The biggest risk is subtle ordering changes in `SharedContextStore` writes when nodes from different former levels overlap. Task 6-3 explicitly validates this.
- Checkpoint and memory flush timing are handled by 37-3, not here. This plan assumes they will be adapted. During development, the eager loop can checkpoint after every node completion as a safe default.
- `_execute_node` (L1180-1269) is the inner per-node method. It calls `_should_skip`, resolves inputs, dispatches to the executor, and stores outputs. The eager loop calls `_guarded_execute_node` which wraps `_execute_node` with the semaphore. Both methods remain unchanged by this plan — only the outer loop that decides *when* to call them changes.
- `resume()` (L643-682) restores state and then calls `_execute()`. Under eager dispatch, the ready-set initialization in `_execute` must handle pre-completed nodes from the restored state: completed/skipped nodes should pre-decrement successors' in-degree so the tracker starts with the correct ready set. This is covered by 37-3 task 4.
