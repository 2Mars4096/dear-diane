# 37-5: Long-Running Workflow Robustness

**Parent:** [37-engine-runtime-parallelism](37-engine-runtime-parallelism.md)
**Status:** in-progress
**Goal:** Add a per-run execution policy/profile layer for long-running workflows so the engine can apply stronger checkpoint cadence, enforce wall-clock and cost ceilings, stop safely with partial results, and expose resume-friendly progress summaries without regressing the default scheduler path.

## Context

- Phase 37's first tranche delivered eager dispatch, shared concurrency budgets, batched/background checkpoints, and a real engine regression suite.
- `EngineConfig` currently exposes process-wide defaults such as checkpoint cadence and `run_budget`, but there is no clean per-run overlay for "treat this as a long-running workflow" semantics.
- `Engine.run()` / `Engine.resume()` can checkpoint and resume, but they do not yet expose a first-class notion of hard run ceilings, resumable partial completion, or structured progress/ETA for multi-hour workflows.
- The backlog items in `docs/todo.md` for node-level checkpointing, long-running profiles, wall-clock/cost ceilings, and human-readable workflow progress all sit in the same runtime-robustness cluster and should land together on the existing Phase 37 substrate.
- This slice should build on 37-3's checkpoint/resume semantics instead of reopening scheduler correctness.
- The 2026-03-17 module audit also called out a user-facing gap: long-running task states are not explicit enough. This plan should make status and stop-state reporting concrete enough that server/UI layers can clearly distinguish active, paused, partial, resumable, failed, and completed runs.

## Tasks

### 1. Define per-run execution policy and profile models
- [x] 1-1. Introduce a run-scoped policy model in the engine layer (`RunPolicy` / `ExecutionProfile`) that overlays `EngineConfig` for a single `Engine.run()` / `Engine.resume()` call.
- [x] 1-2. Support named presets at minimum: `"default"` and `"long_running"`, with precedence `per-run overrides > profile defaults > EngineConfig`.
- [x] 1-3. Include fields for `max_duration`, `max_cost`, checkpoint cadence, critical-node checkpointing, partial-result behavior, and progress-summary settings.
- [x] 1-4. Persist the effective run policy into checkpoint metadata so resumed runs use the same ceilings/cadence instead of current process defaults.

### 2. Add long-running profile defaults without overriding authored node behavior
- [x] 2-1. Define the `"long_running"` preset around existing terminology: stronger retry/backoff defaults, fallback-model support, and partial-success bias for unfinished branches.
- [x] 2-2. Apply profile retry/failure defaults only when a node does not already define explicit `retry_policy`, `failure_policy`, or `model_policy`.
- [x] 2-3. Keep default-profile behavior unchanged so current workflows do not silently become slower or more checkpoint-heavy.
- [x] 2-4. Document how the long-running preset composes with `default_model_policy`, `TierPolicy`, and the existing `run_budget` / cost-tracking path.

### 3. Enforce hard run ceilings at safe scheduler boundaries
- [x] 3-1. Add `max_duration` wall-clock enforcement in the scheduler using monotonic time, checked before dispatching new ready nodes and after node completion.
- [x] 3-2. Implement `max_cost` as a hard run ceiling that builds on the existing `CostTracker` / `run_budget` path instead of creating a competing budget system.
- [x] 3-3. When a ceiling is hit, stop scheduling new work, emit a deterministic stop reason, checkpoint immediately, and return a partial run result instead of an opaque failure.
- [x] 3-4. Keep in-flight node/provider work at safe boundaries only; mid-request cancellation of LLM/tool calls is out of scope for this slice.

### 4. Make partial results and resume state first-class run outputs
- [x] 4-1. Extend `RunResult` metadata with long-running status fields such as `stop_reason`, `partial`, `resumable`, `completed_node_ids`, `pending_node_ids`, `remaining_node_ids`, and latest checkpoint trigger/timestamp.
- [x] 4-2. Preserve partial outputs from completed exit paths/nodes even when the overall run stops on duration/cost ceilings.
- [x] 4-3. Surface the same summary through `RunManager` / run snapshots so server and UI layers can show "completed so far / remaining work / resumable from checkpoint".
- [x] 4-4. Make the reporting explicit when current checkpoint semantics still require subgraphs to restart from their entry boundary.
- [x] 4-5. Introduce an explicit long-running run-state vocabulary for consumers, either by extending `RunStatus` or by adding a supplemental run-phase/state field on `RunRecord`/snapshots (`active`, `paused`, `stopping_on_limit`, `partial`, `resumable`, `completed`, `failed`, `backgrounded`) instead of leaving surfaces to infer state from low-level fields.

### 5. Strengthen checkpoint cadence for long-tail runs
- [x] 5-1. Add a policy-driven checkpoint mode for long-running runs that can tighten cadence beyond 37-3's batch/timer defaults.
- [x] 5-2. Support critical-node checkpoint triggers for nodes marked via stable engine-visible metadata/tags, plus always-on checkpoints around halt, human-input boundaries, and terminal completion.
- [x] 5-3. Ensure stronger cadence still uses the serialized/background checkpoint path from 37-3 rather than reintroducing blocking writes in the scheduler loop.
- [x] 5-4. Record why a checkpoint was taken (`batch`, `timer`, `critical_node`, `duration_limit`, `cost_limit`, `halt`) for resume/debug clarity.

### 6. Emit structured progress, stage, and ETA summaries
- [x] 6-1. Add engine-level progress snapshots derived from scheduler state: completed/total nodes, active frontier, pending critical path, elapsed time, and estimated remaining time.
- [x] 6-2. Reuse existing graph/schedule concepts where possible instead of inventing an unrelated progress model.
- [x] 6-3. Emit structured progress events/metadata from the engine; keep concierge/UI natural-language rendering as a consumer concern.
- [x] 6-4. Provide coarse stage labels from graph structure or node/subgraph names, but keep this deterministic and heuristic-driven rather than LLM-authored prose inside the engine.

### 7. Backfill regression coverage for long-running behavior
- [x] 7-1. Add tests for policy precedence, long-running preset defaults, and resume using persisted effective policy rather than current process defaults.
- [x] 7-2. Add ceiling tests for `max_duration` and `max_cost`: checkpoint written, partial result returned, pending work preserved, and resume completes equivalently.
- [x] 7-3. Add checkpoint-policy tests for critical-node cadence without regressing the default profile's batching behavior.
- [x] 7-4. Add progress-summary tests that verify stable counts, stop reasons, and ETA/progress payload shape under eager dispatch.

### 8. Review follow-up: run-event tail latency and durability
- [ ] 8-1. Move run-event persistence off the synchronous hot path behind an ordered writer or batcher so event-heavy runs do not pay per-event disk latency inline.
- [ ] 8-2. Keep live subscriber and event emission ordering deterministic while persistence flushes asynchronously, with bounded backpressure and explicit overflow behavior.
- [ ] 8-3. Add crash-safe replay and resume handling for buffered event writes so durability does not regress when batching is enabled.
- [ ] 8-4. Benchmark and regress long event streams (large runs, tool-heavy workflows, high-frequency progress updates) to prove p95 and p99 tail improvements plus ordered recovery after restart.

## Primary Files

- `src/dan/engine/executor.py`
- `src/dan/engine/scheduler.py`
- `src/dan/engine/state.py`
- `src/dan/engine/checkpoint.py`
- `src/dan/engine/events.py`
- `src/dan/providers/cost_tracker.py`
- `src/dan/server/run_manager.py`
- `src/dan/server/run_store.py`
- `src/dan/server/concierge/progress.py`
- `tests/test_engine/test_scheduler_parallelism.py`

## Dependencies / Sequencing

- Depends on 37-3 and 37-4; this plan should build on the eager-dispatch + batched-checkpoint substrate rather than reopening those primitives.
- Start with Task 1 so the policy/profile shape is fixed before ceiling enforcement or reporting code branches on it.
- Task 3 should reuse the existing `run_budget` / `CostTracker` path and model-policy precedence rather than adding a second budget router.
- Task 5 should land before Task 4 so partial-result metadata reflects the real checkpoint behavior.
- Task 6 should stay engine-structured; concierge/UI wording can layer on later without blocking engine robustness.

## Success Criteria

- [x] `Engine.run()` and `Engine.resume()` can operate under an explicit per-run policy/profile, and resumed runs honor the checkpointed effective policy.
- [x] The `"long_running"` profile enables stronger checkpointing and safer retry/fallback defaults without changing behavior for existing default-profile runs.
- [x] Hitting `max_duration` or `max_cost` produces a checkpointed, resumable partial result with a clear stop reason and remaining-work summary.
- [x] Run metadata/event streams expose structured progress data sufficient for server/UI layers to show completed work, active frontier, and coarse ETA.
- [x] Critical-node / stronger-cadence checkpoints work through the non-blocking checkpoint path and do not regress eager-dispatch scheduler correctness.
- [x] Regression tests cover policy precedence, ceiling handling, partial-result reporting, critical-node checkpointing, and resume equivalence.
- [ ] Run-event durability no longer sits on the synchronous hot path, and long event streams preserve ordering and replayability without dominating tail latency.

## Decisions

- Prefer one run-scoped policy overlay over many new top-level kwargs; `EngineConfig` remains the process default surface.
- Treat `max_cost` as the hard-ceiling form of the existing run-budget machinery rather than introducing a second budget system.
- Do not require mid-request cancellation of active LLM/tool calls in this slice; safe-boundary stopping plus immediate checkpointing is the right robustness tradeoff.
- The long-running preset should fill in missing retry/failure/model defaults, not silently override authored per-node settings.
- Keep configuration explicit and runtime-scoped rather than adding more module-import-time env/config resolution.
- Reuse the existing `RunManager` / `RunRecord` / `RunStatus` surface where possible instead of inventing a parallel run-status model.

## Notes

- Engine progress should stay structured and deterministic; human-readable prose belongs in `RunManager`, concierge, or the UI.
- Keep the current 37-3 boundary explicit: subgraph checkpoints remain entry-boundary only unless a later plan expands nested checkpoint semantics.
- This slice is about durability and reporting, not runtime graph mutation.
- Review-driven UX requirement: long-running task states must be explicit enough that higher layers can render trustworthy status chips instead of guessing from sparse engine fields.
- Long-running policy composition:
  explicit per-run overrides win first, then the named profile preset, then `EngineConfig`.
- Long-running policy composition:
  `default_model_policy` is only injected onto nodes that do not already author a `model_policy`, so tiered `ModelSelector` / `TierPolicy` behavior still runs through the effective node policy instead of overriding authored node intent.
- Long-running policy composition:
  `max_cost` is the hard-ceiling form of the existing budget path; when unset, the engine continues to use `EngineConfig.run_budget`, and child/subgraph execution shares the same `CostTracker`.
- 2026-03-21 review follow-up: reopened by [product review](../reviews/2026-03-21-product-review.md) for run-event write-path hardening. Chat and project snapshot persistence remains in 29-5, and memory-kernel index write amplification remains in 29-1; this plan owns the run-log tail-latency slice.

## Estimate

~2-3 days
