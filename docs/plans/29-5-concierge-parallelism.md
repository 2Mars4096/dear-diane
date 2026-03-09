# 29-5: Concierge Parallelism

**Parent:** [29-concierge-memory-evolvement](29-concierge-memory-evolvement.md)
**Status:** not-started
**Goal:** Apply one universal rule throughout the concierge stack: **if sub-tasks are independent, fan them out; if they depend on prior results, serialize them.** This applies to concierge preparation, tool execution, diagnosis, memory extraction, build session steps, and information gathering. Also: replace hard project caps with resource-based concurrency, add priority queuing, and ensure independent work starts immediately.

## Context

The `ConcurrentDispatcher` (Phase 17) provides per-project serialization with cross-project parallelism. This is a good foundation but has limitations:

1. **No intra-turn parallelism.** When the concierge needs to (a) search memory, (b) look up similar workflows, and (c) check user preferences — these run sequentially within `process()`. They should fan out.
2. **Sequential everywhere else too.** Tool calls within a multi-turn loop run one at a time. Diagnosis queries memory, then loads principles, then checks errors — all sequential. Build sessions validate then test then diagnose — but validation and input generation are independent. Memory extraction after a turn extracts facts, then preferences, then episodes — all independent.
3. **Hard cap of 5 concurrent projects.** A 6th independent project waits even when resources are available. The cap should be based on actual resource pressure (LLM API concurrency, memory), not a fixed number.
4. **No priority.** All queued messages are FIFO. A critical monitoring workflow waits behind a casual "summarize this article."
5. **Two queue systems.** `ConcurrentDispatcher` and `ProjectMessageQueue` are parallel implementations. The dispatcher bypasses the queue via `skip_queue=True`. One system should handle all concurrency.

### The universal principle

Every operation in the concierge stack should follow one rule:

```
given a list of sub-tasks:
  partition into {independent, depends_on_prior}
  fan_out(independent)  # asyncio.gather
  for group in depends_on_prior:
    await previous
    fan_out(group)
```

This applies at every level: message dispatch, turn preparation, tool execution, diagnosis, memory operations, build session steps, information gathering (grep, file reads, web searches).

## Tasks

### 1. Core fan-out utility
- [ ] 1-1. Define `SubTask` protocol: `async def __call__() -> Any`. Lightweight, no framework overhead.
- [ ] 1-2. `fan_out(tasks: list[SubTask]) -> list[result]` — wraps `asyncio.gather(*tasks, return_exceptions=True)`. Handles partial failures: successful results returned, failed tasks logged and returned as error objects.
- [ ] 1-3. `fan_out_dict(tasks: dict[str, SubTask]) -> dict[str, result]` — same but keyed, for named sub-tasks.
- [ ] 1-4. Built-in timeout per sub-task: `fan_out(tasks, timeout_per=30)`. Timed-out tasks return `TimeoutError` without blocking others.

### 2. Concierge turn preparation (parallelize the gather phase)
- [ ] 2-1. Refactor `Concierge.process()` to fan out these independent steps:
  - Memory kernel retrieval
  - Workflow/experience search
  - Context resolution (project/task inference)
  - User profile load
  - Active goal state load
- [ ] 2-2. Sequential phase starts after all preparation results are collected: goal detection → planning → execution
- [ ] 2-3. `DAN_CONCIERGE_PARALLEL_PREP=1` env var (default on)

### 3. Tool execution parallelism
- [ ] 3-1. ChatManager multi-turn tool loop: when the LLM requests multiple tool calls in one response, they already run via `asyncio.gather()` (Phase 18). Verify this works correctly and extend to capability tools.
- [ ] 3-2. Within a single tool call that internally needs multiple sub-operations (e.g., `list_directory` + `file_read` × N for gathering context), fan out the reads.
- [ ] 3-3. Information gathering pattern: when the concierge/LLM needs to read multiple files, search multiple queries, or fetch multiple URLs — batch into `fan_out()` instead of sequential calls.
- [ ] 3-4. Example: "summarize these 5 files" → `fan_out([file_read(f) for f in files])` → aggregate → summarize. Not: read file 1 → read file 2 → ... → read file 5 → summarize.

### 4. Diagnosis parallelism (29-3 integration)
- [ ] 4-1. When a build session enters DIAGNOSING state, fan out:
  - Memory kernel query (WORKFLOW_REPAIR policy)
  - Error pattern search
  - Principle retrieval
  - Similar workflow lookup (for structural comparison)
- [ ] 4-2. Aggregate results into diagnosis context, then run the sequential analysis (LLM call with all context).
- [ ] 4-3. When diagnosis suggests multiple independent fixes (e.g., fix node A prompt + fix node B port), apply them concurrently.

### 5. Memory extraction parallelism (29-6 integration)
- [ ] 5-1. Post-interaction memory extraction: fact extraction, preference extraction, and episode summarization are independent — fan them out.
- [ ] 5-2. Post-run learning: success pattern update, failure pattern creation, principle check, and experience store update are independent — fan them out.
- [ ] 5-3. Consolidation: when the background consolidation job runs, it processes multiple memory sections — fan out across sections.

### 6. Build session parallelism (29-3 integration)
- [ ] 6-1. VALIDATING state: graph validation and sample input generation are independent — fan out.
- [ ] 6-2. Reuse-first check (29-4): memory search and intent extraction can run concurrently — the reuse result gates whether to continue generating.
- [ ] 6-3. Post-build: memory extraction + workflow save + experience update are independent — fan out.

### 7. Resource-based concurrency
- [ ] 7-1. Define `ResourceBudget` model: `max_concurrent_llm_calls` (default 10), `max_concurrent_runs` (default 5), `max_memory_mb` (optional)
- [ ] 7-2. `ResourceTracker` class: tracks active LLM calls, active runs, estimated memory usage
- [ ] 7-3. Replace `max_concurrent_projects` hard cap with `ResourceTracker` check: if resources available → start immediately, regardless of project count
- [ ] 7-4. `DAN_MAX_CONCURRENT_LLM` and `DAN_MAX_CONCURRENT_RUNS` env vars
- [ ] 7-5. Backpressure: when resources are exhausted, new work queues with estimated wait time reported to user

### 8. Priority queuing
- [ ] 8-1. Define `MessagePriority` enum: `CRITICAL` (monitoring, error alerts), `HIGH` (active goal continuation), `NORMAL` (new requests), `LOW` (background tasks, consolidation)
- [ ] 8-2. `classify_priority(message, active_goals) -> MessagePriority` — infer from message content, active goal state, and explicit user markers
- [ ] 8-3. Replace FIFO `asyncio.Queue` in dispatcher with priority queue (heapq-backed)
- [ ] 8-4. Within same priority: FIFO ordering preserved
- [ ] 8-5. User override: `/priority high` prefix or `urgent:` keyword promotes a message

### 9. Unified queue
- [ ] 9-1. Remove `ProjectMessageQueue` (`concierge/queue.py`) — its logic is subsumed by the dispatcher
- [ ] 9-2. Dispatcher handles all concurrency decisions: per-project serialization, cross-project parallelism, priority, resource checks
- [ ] 9-3. Remove `skip_queue` metadata flag — no longer needed
- [ ] 9-4. Concierge `process()` never does its own queueing; all queueing is in the dispatcher

### 10. Immediate start guarantee
- [ ] 10-1. Decision rule: if a message's project has no active task AND resources are available → process immediately, never queue
- [ ] 10-2. If project has an active task → queue behind it (same-project serialization preserved)
- [ ] 10-3. If resources exhausted → queue with priority, dequeue when resources free up
- [ ] 10-4. Status check / cancel commands always bypass queue (CRITICAL priority, instant processing)
- [ ] 10-5. Emit `processing_started` or `queued_with_position` event immediately so user knows what happened

### 11. Tests
- [ ] 11-1. Unit tests for fan_out / fan_out_dict (concurrent execution, partial failure, timeout)
- [ ] 11-2. Unit tests for ResourceTracker (budget accounting, backpressure)
- [ ] 11-3. Unit tests for priority queue (ordering, within-priority FIFO)
- [ ] 11-4. Integration test: concierge turn preparation runs 5 sub-tasks concurrently (measure wall-clock < sum of individual)
- [ ] 11-5. Integration test: "read 10 files" fans out, completes faster than sequential
- [ ] 11-6. Integration test: diagnosis phase fans out memory + error + principle queries
- [ ] 11-7. Integration test: 10 independent projects start concurrently (no artificial cap)
- [ ] 11-8. Integration test: high-priority message jumps queue ahead of low-priority
- [ ] 11-9. Integration test: same-project messages serialize correctly even under parallelism

## Decisions

- (to be filled during execution)

## Notes

- The `ConcurrentDispatcher` is a good foundation. Changes are incremental: add resource tracking, add priority, add intra-turn fan-out. Not a rewrite.
- Same-project serialization is important and preserved. Messages within a goal must see prior results. Cross-project parallelism is what gets more aggressive.
- The engine scheduler (`asyncio.gather` per topological level) handles intra-workflow parallelism. This plan handles intra-concierge parallelism. They are independent.
- The `fan_out` utility is deliberately simple — just `asyncio.gather` with error handling and timeout. No task framework, no scheduler. The complexity is in identifying which operations are independent at each call site, not in the parallelism mechanism.
- Tasks 3-6 (tool/diagnosis/memory/build parallelism) are integration points with 29-3, 29-4, and 29-6. Those plans define the operations; this plan ensures they run concurrently where possible. The sub-task items in 3-6 should be implemented when the corresponding plan is being built.
