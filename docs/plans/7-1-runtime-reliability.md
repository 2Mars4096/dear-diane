# 7-1: Runtime Reliability

**Parent:** [7-core-hardening](7-core-hardening.md)
**Status:** completed
**Goal:** Add configurable retry/backoff/fallback to all executor types and audit concurrency controls, replacing hardcoded retry logic with a unified `retry_policy` model field.

## Tasks

- [x] 1. `RetryPolicy` model
  - [x] 1-1. Add `RetryPolicy` Pydantic model: `max_retries: int = 0`, `backoff: float = 1.0`, `backoff_max: float = 60.0`, `fallback_model: str | None = None`, `on_failure: Literal["error", "skip", "halt"] = "error"` — field name `backoff` matches architecture.md contract, `backoff_max` is additive (caps exponential growth)
  - [x] 1-2. Add `retry_policy: RetryPolicy | None = None` field on `NodeBase` (all node types inherit it)
  - [x] 1-3. Update `dan_graph_v1` JSON contract — `retry_policy` serializes as nested object, `None` omitted
  - [x] 1-4. Update TypeScript `DanNode` type in `types/graph.ts` with optional `retry_policy` field
  - [x] 1-5. Add `RETRY_ATTEMPTED = "retry_attempted"` to `EventType` enum in `events.py` — prerequisite for tasks 2-4 and 3-3 (executor `emit_event` validates via enum)
- [x] 2. Wire retry into `LLMExecutor`
  - [x] 2-1. Replace hardcoded `max_retries=3, backoff=1.0` in `_call_llm` with `node.retry_policy` (fall back to `RetryPolicy(max_retries=3)` default when `None`)
  - [x] 2-2. Implement `fallback_model` — on final retry failure, if `fallback_model` is set, re-attempt with the alternate model string
  - [x] 2-3. Implement `on_failure="skip"` — return `NodeStatus.SKIPPED` with empty outputs instead of `FAILED`
  - [x] 2-4. Implement `on_failure="halt"` — return `NodeResult` with `status=FAILED` and `metadata={"halt": True}`. Scheduler checks this flag after `_execute_node` returns and stops scheduling further nodes, writes a checkpoint at the current level, then returns `RunResult(success=False)`. Already-running parallel nodes in the same level finish but no new levels are dispatched.
  - [x] 2-5. Emit `retry_attempted` event (requires 1-5) with attempt number, error, model used
- [x] 3. Wire retry into `ToolExecutor`
  - [x] 3-1. Add retry loop around `fn(**merged_args)` with exponential backoff for transient exceptions (`TimeoutError`, `ConnectionError`, `OSError`)
  - [x] 3-2. Respect `node.retry_policy` (fall back to `RetryPolicy(max_retries=0)` — no retry by default for tools)
  - [x] 3-3. Emit `retry_attempted` event per retry (requires 1-5)
  - [x] 3-4. Classify exceptions: transient (retry) vs permanent (fail immediately)
  - [x] 3-5. Support `on_failure="halt"` — same `metadata={"halt": True}` flag as LLMExecutor
- [x] 4. Wire retry into `CodeExecutor`
  - [x] 4-1. Respect `node.retry_policy` for `on_failure` semantics (skip/halt). Retry on code exec is rarely useful since code is deterministic — but the policy fields should still be honored
  - [x] 4-2. **No retry loop for code** — `exec()` is synchronous with no timeout/preemption mechanism. Retrying identical code produces identical results. If a future sandbox adds timeouts (Phase 6), retry becomes meaningful. For now, `max_retries` is ignored on code nodes, documented in the Decisions section
- [x] 5. `max_concurrency` audit
  - [x] 5-1. Verify `ForEachNode.parallelism` + semaphore covers per-node concurrency (already done — confirm no gaps)
  - [x] 5-2. Evaluate whether `EngineConfig.max_concurrency` is needed as a graph-wide ceiling (across all parallel dispatches)
  - [x] 5-3. If yes, implement global semaphore in scheduler that wraps `_execute_node` calls
  - [x] 5-4. Document decision in this plan's Decisions section
- [x] 6. Config panel UI
  - [x] 6-1. Add collapsible "Retry Policy" section in `ConfigPanel.tsx` for all node types
  - [x] 6-2. Fields: max_retries (number), backoff (number, seconds), fallback_model (text — gets model autocomplete from 7-2), on_failure (dropdown: error/skip/halt)
  - [x] 6-3. Hide section when retry_policy is null; "Add retry policy" button to initialize
  - [x] 6-4. `backoff_max` in a collapsible "Advanced" sub-section within retry policy (most users won't need it)
- [x] 7. Tests
  - [x] 7-1. Unit: `RetryPolicy` model validation (defaults, serialization, round-trip)
  - [x] 7-2. Unit: `LLMExecutor` retry with mock — verify retry count, backoff sleep calls, fallback model switch
  - [x] 7-3. Unit: `LLMExecutor` `on_failure="skip"` returns SKIPPED
  - [x] 7-4. Unit: `LLMExecutor` `on_failure="halt"` — returns FAILED with `metadata.halt=True`
  - [x] 7-5. Unit: `ToolExecutor` retry with transient exception mock
  - [x] 7-6. Unit: `ToolExecutor` permanent exception fails immediately (no retry)
  - [x] 7-7. Integration: graph with retry_policy on LLM node — engine respects policy
  - [x] 7-8. Integration: `on_failure="halt"` stops the engine at current level, writes checkpoint, returns `success=False`
  - [x] 7-9. Integration: halt — parallel nodes in the same level finish, but next level is not dispatched
  - [x] 7-10. Audit test: confirm ForEach parallelism semaphore behavior with concurrent mock
  - [x] 7-11. Unit: `RETRY_ATTEMPTED` event type exists and is emittable
- [ ] 8. Docs sync
  - [ ] 8-1. Update `architecture.md` — document RetryPolicy model, executor retry behavior
  - [ ] 8-2. Update `llm-api-guide.md` — add retry_policy to node parameter tables
  - [ ] 8-3. Update `todo.md` / `changelog.md`

## Decisions

- **LLM default retry:** When `retry_policy` is `None` on an LLM node, the executor falls back to `RetryPolicy(max_retries=3)` to preserve backward-compatible behavior with the previous hardcoded `max_retries=3`.
- **Tool default retry:** When `retry_policy` is `None` on a tool node, the executor falls back to `RetryPolicy(max_retries=0)` — no retry by default, since tool failures are often permanent.
- **Transient exception classification for tools:** `TimeoutError`, `ConnectionError`, `OSError` are retried; all other exceptions fail immediately. Matches the LLM executor's `RateLimitError`/`APITimeoutError` pattern.
- **Code executor: no retry loop.** `exec()` is synchronous and deterministic — retrying produces identical results. `max_retries` is silently ignored on code nodes. `on_failure` semantics (skip/halt/error) are honored.
- **`max_concurrency` added to `EngineConfig`:** Optional `int | None` field (default `None` = unlimited). When set, creates a global `asyncio.Semaphore` in the scheduler that wraps all `_execute_node` calls, including those in sub-graphs and cycle iterations. `ForEachNode.parallelism` still governs per-foreach concurrency independently — the global semaphore is an additional ceiling.
- **Halt semantics:** When any node returns `metadata={"halt": True}`, the scheduler saves a checkpoint (if configured) and stops dispatching further topological levels. Already-running parallel nodes in the same level finish (can't cancel safely). `_build_result` returns `success=False`. Applied in all three scheduling paths: DAG fast-path, `_execute_with_cycles`, and `_run_subgraph`.
- **Fallback model:** Single re-attempt after all retries are exhausted with `policy.fallback_model`. Uses the same OpenAI client (same provider). Cross-provider fallback deferred to plan 7-2.
- **`_guarded_execute_node` wrapper:** Added to centralize the global semaphore logic without modifying the core `_execute_node` method, keeping the change minimal and backward-compatible.

## Notes

- `LLMExecutor._call_llm` currently catches `RateLimitError`, `APITimeoutError` as transient. The new retry should preserve this classification and extend it to `ToolExecutor`.
- **`on_failure="halt"` semantics (decided):** stop scheduling new levels. Already-running parallel nodes in the same topological level finish (can't safely cancel async tasks). Scheduler writes a checkpoint at the halt point and returns `RunResult(success=False)`. This means "halt at this point and save state for potential resume" — not "kill everything immediately." The checkpoint enables the user to inspect, fix, and resume.
- **`exec()` has no timeout** — `CodeExecutor` runs user code synchronously via `exec()` with no preemption mechanism. Retry on deterministic code is pointless. Phase 6 (Script execution / sandbox) plans proper subprocess timeouts, at which point code retry becomes useful. For now, `max_retries` is ignored for code nodes.
- **Field naming:** `backoff` (not `backoff_base`) to match architecture.md. `backoff_max` is additive — the architecture contract says `backoff` only, but a cap prevents runaway exponential waits.
- `fallback_model` in 7-1 uses the same provider (single OpenAI client). Cross-provider fallback (e.g., Claude → GPT-4o) comes in 7-2.
- **`retry_attempted` event** must be added to `EventType` enum before executors can emit it — `emit_event` validates via `EventType(event_type)` which throws `ValueError` on unknown strings.
