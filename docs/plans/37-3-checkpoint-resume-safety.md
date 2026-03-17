# 37-3: Checkpoint & Resume Safety

**Parent:** [37-engine-runtime-parallelism](37-engine-runtime-parallelism.md)
**Status:** not-started
**Goal:** Adapt checkpoint triggers, memory flushes, and resume logic so they work correctly with eager/ready-queue dispatch where nodes complete in arbitrary order rather than level-aligned batches.

## Context

The current scheduler checkpoints after every topological level:

```python
for level in levels:
    await asyncio.gather(*tasks)
    await self._flush_memory_writes(...)
    await self._save_checkpoint(...)     # ← per-level
```

With eager dispatch (37-1), there are no discrete levels — nodes complete individually. Checkpointing after every single node is safe but adds overhead. Checkpointing too infrequently risks losing work on failure.

### Current checkpoint implementation
`_save_checkpoint` (L2062-2113) serializes:
- `state.snapshot()` — node statuses, port data
- `shared_context.snapshot()` — shared KV store
- `artifacts.snapshot()` — artifact store
- `local_state.snapshot()` — per-node local state
- `CheckpointData` — completed node IDs, outputs, graph revision hash

`_flush_memory_writes` (L2016-2061) drains queued memory writes from executors and persists them.

Both are `await`-ed inline and block the scheduling loop.

### Resume
`Engine.resume()` (scheduler.py L643-682) loads the checkpoint, calls `state.restore_from_snapshot()` to mark completed nodes, restores `SharedContextStore`, `ArtifactStore`, and `LocalStateManager`, then calls `_execute()` — the same method as a fresh run. The level-sync loop skips completed nodes via:
```python
ready = [nid for nid in level
         if state.node_statuses.get(nid) == NodeStatus.PENDING]
```

With eager dispatch, `_execute()` must re-initialize the dependency tracker from the restored state: nodes marked COMPLETED/SKIPPED should not be re-run, and their successors' in-degree should be pre-decremented so the tracker starts with the correct ready set. This is purely an initialization concern inside the eager dispatch loop — `resume()` itself does not change.

## Tasks

### 1. Checkpoint trigger strategy
- [ ] 1-1. Implement a batched checkpoint: accumulate completed nodes and flush after every K completions or T seconds (whichever comes first). Configurable via `DAN_CHECKPOINT_BATCH_SIZE` (default 5) and `DAN_CHECKPOINT_INTERVAL_SEC` (default 10).
- [ ] 1-2. Always checkpoint on halt (error, cancellation, all-done).
- [ ] 1-3. Always checkpoint before entering/exiting a cycle region.
- [ ] 1-4. Preserve the existing per-level checkpoint when `DAN_EAGER_DISPATCH=0` (feature flag off).

### 2. Non-blocking checkpoint I/O
- [ ] 2-1. Move `_save_checkpoint` to a background task: `asyncio.create_task(self._save_checkpoint(...))`. The scheduling loop does not wait for it.
- [ ] 2-2. Ensure at-most-one-outstanding: if a previous checkpoint write is still in flight, wait for it before starting a new one (simple `asyncio.Lock` or flag).
- [ ] 2-3. On halt, wait for the in-flight checkpoint to complete before returning.
- [ ] 2-4. Error handling: if a background checkpoint fails, log a warning but do not crash the run.

### 3. Memory flush batching
- [ ] 3-1. Align `_flush_memory_writes` with checkpoint batching: flush accumulated writes at the same trigger points as checkpoints.
- [ ] 3-2. Memory writes must be flushed *before* the checkpoint that covers them — otherwise resume could skip nodes whose memory writes were lost.
- [ ] 3-3. Ensure memory writes are idempotent on resume (same key+value written again should not corrupt state).

### 4. Resume with dependency tracker
- [ ] 4-1. On resume, rebuild the dependency tracker from the checkpoint's `completed_node_ids`.
- [ ] 4-2. For each completed node, decrement successors' in-degree. Nodes whose in-degree reaches 0 and are PENDING go into the ready set.
- [ ] 4-3. In-progress nodes at checkpoint time (started but not completed) should be reset to PENDING and re-dispatched. This is the same as current behavior but must be explicit.
- [ ] 4-4. Cycle regions: if a cycle was mid-iteration at checkpoint time, restore the iteration counter and the cycle region's internal state.

### 5. Checkpoint data model
- [ ] 5-1. Add `pending_node_ids` to `CheckpointData` (Pydantic model in `engine/checkpoint.py` L89-110) — nodes that were in-flight but not completed when the checkpoint was taken. On resume, these are re-dispatched. Use `Field(default_factory=list)` for backward compatibility.
- [ ] 5-2. Add `checkpoint_trigger` field (for debugging): `"batch"`, `"timer"`, `"halt"`, `"cycle_boundary"`. Also `Field(default="")` for backward compatibility.
- [ ] 5-3. Backward compatibility: checkpoints from the level-sync scheduler (without `pending_node_ids` or `checkpoint_trigger`) must load correctly. Both fields default to empty so `CheckpointData.model_validate(old_data)` works without migration.
- [ ] 5-4. `FileSystemCheckpointStore.save()` (checkpoint.py L47-52) does synchronous `path.write_text()` inside an `async def`. If task 2-1 moves checkpointing to a background task, consider wrapping the write in `asyncio.to_thread()` to avoid blocking the event loop for large checkpoints.

### 6. Subgraph checkpoints
- [ ] 6-1. Subgraphs currently skip checkpoints (`skip_checkpoint=True` in `_execute_with_cycles` called from `_run_subgraph`). Decide whether to enable them under eager dispatch or keep skipping.
- [ ] 6-2. If enabled: subgraph checkpoint state must be nested inside the parent checkpoint, not overwrite it.
- [ ] 6-3. If kept skipped: document that subgraph progress is lost on crash and must be re-run from scratch.

## Decisions

- (filled in during execution)

## Notes

- The biggest risk is checkpoint-resume equivalence: a run that crashes and resumes must produce the same result as a run that completes without interruption. This is testable (37-4 covers it).
- Background checkpoint I/O (task 2) is an optimization, not a correctness requirement. If it proves too complex, inline checkpointing (blocking but batched) is an acceptable fallback.
- Memory write ordering matters: if node A writes key X and node B reads key X, and both were in the same former level, the eager scheduler might let B run before A completes. This is a pre-existing issue (shared mutable state) but becomes more likely with eager dispatch. 37-4 should include a test for this.
- The `FileSystemCheckpointStore` does a single JSON dump. For large graphs with many artifacts, this can be slow. Background I/O (task 2) mitigates this but a more efficient incremental format is a future optimization.
