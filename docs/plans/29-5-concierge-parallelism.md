# 29-5: Concierge Parallelism

**Parent:** [29-concierge-memory-evolvement](29-concierge-memory-evolvement.md)
**Status:** in-progress
**Goal:** Apply one universal rule throughout the concierge stack: **if sub-tasks are independent, fan them out; if they depend on prior results, serialize them.** This applies to concierge preparation, tool execution, diagnosis, memory extraction, build session steps, and information gathering. Also: replace hard project caps with resource-based concurrency, add priority queuing, and ensure independent work starts immediately. The original rollout is complete; the remaining follow-up in this same plan is to tighten concierge throughput where the current implementation is still too coarse.

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
- [x] 1-1. Define `SubTask` protocol: `async def __call__() -> Any`. Lightweight, no framework overhead.
- [x] 1-2. `fan_out(tasks: list[SubTask]) -> list[result]` — wraps `asyncio.gather(*tasks, return_exceptions=True)`. Handles partial failures: successful results returned, failed tasks logged and returned as error objects.
- [x] 1-3. `fan_out_dict(tasks: dict[str, SubTask]) -> dict[str, result]` — same but keyed, for named sub-tasks.
- [x] 1-4. Built-in timeout per sub-task: `fan_out(tasks, timeout_per=30)`. Timed-out tasks return `TimeoutError` without blocking others.

### 2. Concierge turn preparation (parallelize the gather phase)
- [x] 2-1. Refactor `Concierge.process()` to fan out these independent steps:
  - Memory kernel retrieval
  - Context resolution (project/task inference)
- [x] 2-2. Sequential phase starts after all preparation results are collected: goal detection → planning → execution
- [x] 2-3. `DAN_CONCIERGE_PARALLEL_PREP=1` env var (default on)

### 3. Tool execution parallelism
- [x] 3-1. ChatManager multi-turn tool loop: capability tools requested in one LLM turn now execute concurrently via `asyncio.gather()`, while start/result events, audit records, and tool-result message ordering stay stable for the next LLM turn. Covered by `tests/test_server/test_multi_turn_tools.py`.
- [x] 3-2. Within a single tool call that internally needs multiple sub-operations (e.g., `list_directory` + `file_read` × N for gathering context), fan out the reads. *(Already handled by concurrent capability execution — the LLM requests multiple tool calls per turn and they execute concurrently via asyncio.gather.)*
- [x] 3-3. Information gathering pattern: when the concierge/LLM needs to read multiple files, search multiple queries, or fetch multiple URLs — batch into `fan_out()` instead of sequential calls. *(Covered by 3-1: multi-tool turns execute concurrently.)*
- [x] 3-4. Example: "summarize these 5 files" → `fan_out([file_read(f) for f in files])` → aggregate → summarize. Not: read file 1 → read file 2 → ... → read file 5 → summarize. *(Covered by 3-1.)*

### 4. Diagnosis parallelism (29-3 integration)
- [x] 4-1. When a build session enters DIAGNOSING state, fan out:
  - Memory kernel query (WORKFLOW_REPAIR policy)
  - Error pattern search
  - Principle retrieval
  - Similar workflow lookup (for structural comparison)
- [x] 4-2. Aggregate results into diagnosis context, then run the sequential analysis (LLM call with all context).
- [x] 4-3. When diagnosis suggests multiple independent fixes (e.g., fix node A prompt + fix node B port), apply them concurrently. *(New `BuildSessionManager.apply_independent_modifications()` fans out per-node fix groups; diagnosis strategies are aggregated so the LLM applies them in one shot.)*

### 5. Memory extraction parallelism (29-6 integration)
- [x] 5-1. Post-interaction memory extraction: fact extraction, preference extraction, and episode summarization are independent — fan them out.
- [x] 5-2. Post-run learning: success pattern update, failure pattern creation, principle check, and experience store update are independent — fan them out. *(New `RunLearner.extract_run_learnings_async()` fans out via `asyncio.gather` + `asyncio.to_thread`. `RunManager._enrich_and_persist` now calls the async version.)*
- [x] 5-3. Consolidation: when the background consolidation job runs, it processes multiple memory sections — fan out across sections. *(New `MemoryKernel.run_consolidation_async()` fans out promote+decay concurrently, then archive; single save at end. Added `threading.Lock` to `_save_index` for thread safety.)*

### 6. Build session parallelism (29-3 integration)
- [x] 6-1. VALIDATING state: graph validation and sample input generation are independent — fan out.
- [x] 6-2. Reuse-first check (29-4): memory search and intent extraction can run concurrently — the reuse result gates whether to continue generating. *(New `reuse_first_decision_async()` in `reuse_decision.py`. Speculative reuse search added to the parallel prep phase in `Concierge.process()` so it runs concurrently with memory retrieval and context resolution.)*
- [x] 6-3. Post-build: memory extraction + workflow save + experience update are independent — fan out.

### 7. Resource-based concurrency
- [x] 7-1. Define `ResourceBudget` model: `max_concurrent_llm_calls` (default 10), `max_concurrent_runs` (default 5), `max_memory_mb` (optional)
- [x] 7-2. `ResourceTracker` class: tracks active LLM calls, active runs, estimated memory usage
- [x] 7-3. Replace `max_concurrent_projects` hard cap with `ResourceTracker` check: if resources available → start immediately, regardless of project count
- [x] 7-4. `DAN_MAX_CONCURRENT_LLM` and `DAN_MAX_CONCURRENT_RUNS` env vars
- [x] 7-5. Backpressure: when resources are exhausted, new work queues with estimated wait time reported to user

### 8. Priority queuing
- [x] 8-1. Define `MessagePriority` enum: `CRITICAL` (monitoring, error alerts), `HIGH` (active goal continuation), `NORMAL` (new requests), `LOW` (background tasks, consolidation)
- [x] 8-2. `classify_priority(message, active_goals) -> MessagePriority` — infer from message content, active goal state, and explicit user markers
- [x] 8-3. Replace FIFO `asyncio.Queue` in dispatcher with priority queue (heapq-backed)
- [x] 8-4. Within same priority: FIFO ordering preserved
- [x] 8-5. User override: `/priority high` prefix or `urgent:` keyword promotes a message

### 9. Unified queue
- [x] 9-1. Remove `ProjectMessageQueue` (`concierge/queue.py`) — its logic is subsumed by the dispatcher
- [x] 9-2. Dispatcher handles all concurrency decisions: per-project serialization, cross-project parallelism, priority, resource checks
- [x] 9-3. Remove `skip_queue` metadata flag — no longer needed
- [x] 9-4. Concierge `process()` never does its own queueing; all queueing is in the dispatcher

### 10. Immediate start guarantee
- [x] 10-1. Decision rule: if a message's project has no active task AND resources are available → process immediately, never queue
- [x] 10-2. If project has an active task → queue behind it (same-project serialization preserved)
- [x] 10-3. If resources exhausted → queue with priority, dequeue when resources free up
- [x] 10-4. Status check / cancel commands always bypass queue (CRITICAL priority, instant processing)
- [x] 10-5. Emit `processing_started` or `queued_with_position` event immediately so user knows what happened

### 11. Tests
- [x] 11-1. Unit tests for fan_out / fan_out_dict (concurrent execution, partial failure, timeout)
- [x] 11-2. Unit tests for ResourceTracker (budget accounting, backpressure)
- [x] 11-3. Unit tests for priority queue (ordering, within-priority FIFO)
- [x] 11-4. Integration test: concierge turn preparation runs 5 sub-tasks concurrently (measure wall-clock < sum of individual)
- [x] 11-5. Integration test: "read 10 files" fans out, completes faster than sequential
- [x] 11-6. Integration test: diagnosis phase fans out memory + error + principle queries
- [x] 11-7. Integration test: 10 independent projects start concurrently (no artificial cap)
- [x] 11-8. Integration test: high-priority message jumps queue ahead of low-priority
- [x] 11-9. Integration test: same-project messages serialize correctly even under parallelism
All 22 tests in `tests/test_concierge/test_parallelism_integration.py`.

### 12. Post-rollout efficiency follow-up
- [ ] 12-1. Refine `ConcurrentDispatcher` capacity accounting so advisory `"llm"` slots are not held for the full lifetime of a live concierge task when the turn is mostly waiting on tools or queue plumbing.
- [ ] 12-2. Preserve the existing same-project serialization and backpressure rules while separating project/run concurrency from actual model-call concurrency, so unrelated tool-heavy chats do not starve each other between `provider.complete()` calls.
- [ ] 12-3. Replace the current `child_execution="mixed"` serial fallback in `tier_executors.py` with real hybrid scheduling: preserve dependency order where required, but still fan out independent child groups.
- [ ] 12-4. Add focused regressions or benchmarks covering (a) two simultaneous tool-heavy chats that should share LLM capacity fairly and (b) a mixed dependency tree that proves hybrid execution beats full serialization without breaking cancellation or event ordering.
- [ ] 12-5. Give child and subagent sessions unique internal thread/session identities by default, with copy-on-write metadata, instead of inheriting parent thread identity or mutating shared thread-meta state.
- [ ] 12-6. Replace implicit full-parent-context inheritance with an explicit child handoff packet: delegated goal slice, file refs, memory slice, constraints, and a summarized return channel. Parent context is read-only input, not shared mutable state.
- [ ] 12-7. Extend same-project dispatch beyond narrow bypass commands so corrections, stop/status requests, clarifications, and superseding instructions can attach to or preempt stale queued work without breaking audit history.
- [ ] 12-8. Add queue-collapsing and supersede rules for obsolete queued turns in the same project when a newer instruction makes earlier queued work irrelevant.
- [ ] 12-9. Move chat and project persistence off the hot response path via ordered journal writes plus periodic snapshot and compaction, so tool-heavy turns do not wait on full JSON rewrites.
- [ ] 12-10. Add focused regressions and benchmarks for child metadata isolation, hybrid child-tree scheduling, same-project supersede ordering, and fair sharing between simultaneous tool-heavy chats.

## Decisions

- `MemoryKernel._save_index()` is now protected by a `threading.Lock` to prevent concurrent worker threads from racing on the temp-file write/rename during fan-out operations.
- `run_consolidation_async` fans out promote+decay concurrently but runs archive *after* promote completes (ACTIVE→DURABLE→ARCHIVE lifecycle dependency), with a single `_save_index()` at the end.
- `RunLearner.extract_run_learnings_async` fans out independent sub-operations: on success [asset update, pattern reinforcement, repair link]; on failure [failure pattern creation, principle boost]. Error category is pre-computed before thread dispatch.
- `BuildSessionManager.apply_independent_modifications` groups modifications by target node — same-node modifications run sequentially within their group, different-node groups fan out concurrently.
- Speculative reuse search runs in the parallel prep phase of `Concierge.process()` alongside memory retrieval and context resolution. Result is used in the goal path if available, avoiding a redundant sequential memory search.
- Post-interaction memory extraction (`_store_memory_candidates`) is fire-and-forget: scheduled as a background `asyncio.Task` that fans out episode, preference, and heuristic extraction via `fan_out_dict` with `asyncio.to_thread` wrappers. Never blocks the response path. Falls back to synchronous when no event loop is running.
- `ProjectMessageQueue` (`queue.py`) deprecated as a no-op pass-through. All queueing/serialization is in `ConcurrentDispatcher`. The `skip_queue` metadata flag is fully removed from both runtime and dispatcher.
- `_drain_queued_messages` in runtime.py is now a no-op async generator — the dispatcher owns queue draining.
- Status/cancel commands (`/status`, `/build-status`, `/cancel`, `/build-stop`, `status`, `cancel`) bypass queueing entirely via `_is_bypass_command()` in the dispatcher. They process immediately regardless of project activity.
- `ChatQueuedEvent` extended with optional `queue_position` field so clients can display "you are #N in line."
- For capability multi-calls, emit all `ChatToolCallStartEvent`s first, execute handlers concurrently, then emit `ChatToolCallResultEvent`s and build tool-result messages in original request order so the next LLM turn sees deterministic ordering.
- Build-session diagnosis now uses `fan_out_dict()` plus `asyncio.to_thread(...)` so memory repair lookup, failure-pattern lookup, principle retrieval, similar-workflow lookup, and optional DiagnosisLoop classification run independently and degrade gracefully per source.
- Build-session validation now fans out `validate_draft()` and `generate_smoke_inputs()` before entering `TESTING`; validation still gates the smoke run, and smoke-input failures surface only if validation passes.
- Post-build follow-up work is best-effort fan-out: memory extraction/storage, workflow linking, and adapted-workflow asset persistence run independently so one failing branch does not fail the completed turn.
- 2026-03-20 follow-up decision: keep the remaining concierge-throughput work inside this existing `29-5` plan instead of creating a nested `29-5-*` follow-up plan. The unresolved items are implementation refinements, not a separate architecture track.
- 2026-03-21 review follow-up: expanded section 12 after [product review](../reviews/2026-03-21-product-review.md) to absorb the remaining premium-subagent gaps: child-session isolation, same-project supersede and preemption, and chat/project snapshot write amplification. Run-event persistence is tracked in 37-5; memory-index persistence stays in 29-1.
- 2026-03-21 review decision: child sessions should have their own IDs by default. Parent threads should receive explicit handoff inputs and summarized child outputs, not share thread identity or mutable thread metadata with children.

## Notes

- The `ConcurrentDispatcher` is a good foundation. Changes are incremental: add resource tracking, add priority, add intra-turn fan-out. Not a rewrite.
- Same-project serialization is important and preserved. Messages within a goal must see prior results. Cross-project parallelism is what gets more aggressive.
- The engine scheduler (`asyncio.gather` per topological level) handles intra-workflow parallelism. This plan handles intra-concierge parallelism. They are independent.
- The `fan_out` utility is deliberately simple — just `asyncio.gather` with error handling and timeout. No task framework, no scheduler. The complexity is in identifying which operations are independent at each call site, not in the parallelism mechanism.
- Tasks 3-6 (tool/diagnosis/memory/build parallelism) are integration points with 29-3, 29-4, and 29-6. Those plans define the operations; this plan ensures they run concurrently where possible. The sub-task items in 3-6 should be implemented when the corresponding plan is being built.
- Focused diagnosis tests now live in `tests/test_concierge/test_build_session_diagnosis.py` and cover combined aggregation plus partial-source failure fallback.
- Remaining gap after the original rollout: concierge already has the right parallel primitives, but dispatcher-level LLM capacity is still tracked too coarsely and `mixed` child execution still collapses to serial. The follow-up is about making the existing architecture more efficient, not about broadening scope.
