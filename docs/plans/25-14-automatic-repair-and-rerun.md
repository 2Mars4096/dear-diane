# 25-14: Automatic Repair And Rerun

**Parent:** [25-chat-control-plane](25-chat-control-plane.md)
**Status:** completed
**Goal:** Keep failure handling automatic by default. When workflow generation or workflow runtime exhausts its first-line bounded recovery, invoke a bounded LLM recovery controller that selects the smallest safe fix, applies it through existing primitives, and continues via retry/resume/rerun before any user-facing escalation.

## Why This Fits Here

- The repo already has the lower-level ingredients:
  - generation fallback, contract validation, and bounded diagnosis in `workflow_generation.py`, `workflow_generation_acceptance.py`, and `meta/diagnosis.py`
  - runtime bounded self-healing, repair lineage, and checkpoint-safe overlays from [37-6](37-6-runtime-self-healing-and-adaptation.md)
  - run lifecycle controls from [25-4](25-4-run-lifecycle-from-chat.md): `resume_run`, `rerun_from_checkpoint`, `submit_human_input`
  - the user-facing "never just stop" policy from [25-11](25-11-fallback-and-completion-policy.md)
- The missing piece is not raw retry logic. It is the controller that sits above existing repair layers and turns "repair exhausted" into one more automatic fix-selection step using the primitives already in the system.

## Scope

### 1. Define one recovery envelope
- [x] 1-1. Introduce a structured payload covering both build-time and run-time failures. Include: `scope` (`generation` or `run`), `workflow_id`, `run_id`, `node_id` when relevant, `failure_summary`, `attempted_fixes`, `available_actions`, and checkpoint/rerun metadata already available.
- [x] 1-2. Reuse existing repair lineage / failure-bucket vocabulary where possible so the recovery controller sees the same cause labels already surfaced by generation/runtime layers.
- [x] 1-3. Track a bounded recovery chain (`attempt_index`, `recovery_budget_remaining`, `last_action`, `last_outcome`) so this layer cannot spin indefinitely.

### 2. Add a bounded LLM recovery planner
- [x] 2-1. Runtime path: when bounded runtime repair ends in advisory/exhausted state, ask the recovery planner to choose one safe next action from an allowlist such as `apply_pending_overlay`, `resume_run`, `rerun_from_checkpoint`, or "patch workflow then start a derived rerun/new run".
- [x] 2-2. Generation path: when build-contract repair plus diagnosis still cannot produce a run-ready workflow, ask the same planner to choose the smallest repair step: focused re-prompt, bounded graph mutation/repair, or full regeneration from improved failure context.
- [x] 2-3. Keep the planner bounded and stateful: a small attempt budget, explicit no-progress detection, and no open-ended redesign loop.

### 3. Execute recovery through existing primitives
- [x] 3-1. Route recovery actions through current source-of-truth paths rather than inventing a second execution system:
  - `resume_run`
  - `rerun_from_checkpoint`
  - pending-node overlay application
  - normal workflow mutation/build path followed by rerun/new run
- [x] 3-2. Prefer durable fixes over repeated retries when the failure is structural or repeatable: patch workflow or generation inputs, then rerun, rather than hammering the same node again.
- [x] 3-3. Preserve current checkpoint/resume semantics and persisted repair lineage so automatic recovery remains inspectable and replayable.

### 4. Keep the user-facing experience automatic first
- [x] 4-1. Chat/CLI/editor should report this as "auto-repairing" / "rerunning with fixes" progress, not immediate failure, while recovery is still within budget.
- [x] 4-2. Run-manager/API payloads should expose the recovery chain so surfaces can render what DAN tried and what changed.
- [x] 4-3. Only after automatic recovery is exhausted or blocked by policy should the surface fall back to a user-visible escalation summary.

### 5. Tests and acceptance
- [x] 5-1. Regression: runtime self-healing budget exhausted -> recovery planner selects a safe next action -> overlay/rerun/new run continues automatically.
- [x] 5-2. Regression: workflow generation diagnosis exhausted -> recovery planner improves context/repair step -> generation either succeeds or exits cleanly after bounded attempts.
- [x] 5-3. Regression: recovery uses only real existing primitives and never fabricates unavailable controls or mutates live-running graph state outside existing safety boundaries.

## Primary Files

- `src/dan/server/agent_runtime/workflow_generation.py`
- `src/dan/server/agent_runtime/workflow_generation_acceptance.py`
- `src/dan/server/run_manager.py`
- `src/dan/engine/scheduler.py`
- `src/dan/engine/runtime_repair.py`
- `src/dan/server/concierge/executor.py`
- `src/dan/server/concierge/policy.py`
- `src/dan/client/client.py`
- relevant tests under `tests/test_engine/`, `tests/test_server/`, and workflow-generation regressions

## Dependencies

- Reuses [25-4](25-4-run-lifecycle-from-chat.md), [25-11](25-11-fallback-and-completion-policy.md), and [37-6](37-6-runtime-self-healing-and-adaptation.md).
- Should avoid reopening engine architecture or introducing a second repair stack. This layer chooses among existing repair/mutation/rerun primitives; it should not replace them.

## Decisions

- 2026-03-26: Runtime automatic recovery v1 is checkpoint-gated and only emits one bounded `rerun_from_checkpoint` candidate after node-local self-healing is exhausted.
- 2026-03-26: Generation automatic recovery v1 reuses the existing codegen path with improved failure context after diagnosis exhaustion instead of introducing a second planner stack.

## Notes

- Implemented in `workflow_generation.py`, `runtime_repair.py`, `scheduler.py`, `run_manager.py`, `chat_events.py`, plus focused generation/runtime regressions.
- Generation `chat_generation_summary` / `chat_validation_result` and runtime `RunResult.metadata` now share the same `automatic_recovery` vocabulary: scope, action, failure summary, attempted fixes, and bounded chain counters.
- `scoped_run.py`, `cli/run_progress.py`, and `cli/run.py` now surface runtime automatic recovery as explicit "auto-repair started/completed/exhausted" progress instead of a silent extra rerun.
- Primary chat/run subscriptions now keep the parent run stream open through automatic recovery instead of terminating on the first `run_failed`; the parent stream now treats `automatic_recovery_completed` as the real bounded-recovery terminal marker.
- Parent run subscriptions now also mirror the recovery child rerun's node/tool progress back onto the parent stream with `automatic_recovery` provenance, so the rerun no longer looks like a hidden side process.
- Exhausted generation/runtime recovery payloads now include `escalation_needed`, `escalation_summary`, and `recommended_actions`, and the primary chat/CLI surfaces render that short escalation summary instead of ending with a bare exhausted marker.
- The post-merge aggregate regression slice is now stable too: chat integration fixtures disable the real startup `model_gateway`, redirect audit/project persistence to temp roots, and wait longer for detached in-process stream snapshots under ASGI transport. The touched validation slice passed with `200 passed`.
- Any later adapter/editor-specific polish can follow as a separate surface-parity task; the core automatic repair-and-rerun contract in this plan is complete.

## Acceptance Criteria

- When generation or runtime recovery stalls, DAN attempts at least one bounded LLM-guided repair-and-rerun path before surfacing failure.
- Automatic recovery reports what it tried and why, and remains bounded and inspectable.
- Existing mutation/checkpoint/resume/rerun flows remain the source of truth for applying and continuing fixes.
- Human escalation, when needed, is a last-resort outcome after bounded automatic recovery is exhausted or blocked by safety policy.
