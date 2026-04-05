# 49-2: Background Dispatch and Concierge Availability

**Parent:** [49-concierge-service-hardening](49-concierge-service-hardening.md)
**Status:** completed
**Goal:** Let the concierge acknowledge backgroundable work immediately, dispatch it without monopolizing the active chat turn, and stay available for new user input while tracked task execution continues.

## Dependencies
- **49-1** must land first: background dispatch requires a durable `ConciergeTask` with stable `task_id` so the acknowledgment can reference a real task, not just a queue slot.
- Consumes `TaskRegistry.create(...)` and `TaskRegistry.transition(...)` from 49-1.
- The existing `ConcurrentDispatcher` already serializes by `project_id`; this plan narrows that to `project_id:task_id` for independent tasks (architecture.md queue-identity note).
- Existing `chat_queued` redirect and Telegram queue-hint behavior (landed in the 48/Telegram hardening) should be preserved and extended, not replaced.

## Success Criteria
- A backgroundable task is acknowledged to the user within 500ms p95 of intake, with `task_id` and summary in the ack payload.
- The concierge remains available for new input immediately after ack — a second unrelated turn does not queue behind the first within the same project.
- Foreground tasks (fast commands, social, short ask) still complete inline without creating a background task.
- Active background task cap starts at 3 per project; overflow queues with explicit position feedback.
- Cancellation and supersession of background tasks are explicit state transitions on the `ConciergeTask`, not silent queue drops.

## Context

The current dispatcher and tiered dispatch path already support queueing, streaming, and cross-project concurrency, but the default same-project contract is still too blocking for the "always-on concierge" model:

- a project with one active task still tends to serialize follow-up intake behind that active task
- the system has good stream and progress primitives, but no explicit background-dispatch contract at the task level
- the run-slot logic is closer to execution capacity than concierge availability, which makes the assistant feel busier than it should

This plan tightens the dispatch model so the concierge is always ready to take the next instruction even when workers are still running.

## Tasks

- [x] 1. Define launch/dispatch modes
  - [x] 1-1. Add a `DispatchMode` enum: `inline` (completes within the intake turn), `foreground` (streams to the active surface, blocks same-task follow-ups), `background` (acks immediately, runs detached, notifies on completion). This is orthogonal to `SessionTier` — tier controls execution depth/budget, dispatch mode controls concierge availability.
  - [x] 1-2. Default dispatch-mode routing: fast commands → `inline`; social/factual ask → `inline`; read-only workflow understanding/query turns → `foreground`; workflow run/build → `background`; workflow schedule → `background`; solver/goal-loop → `background`; workflow edit/preview that requires user confirmation (mutation preview) → `foreground` (user needs to see and approve the diff before it applies); short tool-augmented answers (single-step ask with tool) → `foreground`. The route is determined after triage/intent classification, before session creation. Workflow edits that apply a pre-confirmed mutation can be `background` if no further user feedback is needed.
  - [x] 1-3. Keep `inline` turns on the existing immediate path — no task creation, no ack payload, no detached execution.
- [x] 2. Add a background-dispatch acknowledgment path
  - [x] 2-1. Emit a `task_ack` chat event with `{task_id, title, state: "running", dispatch_mode: "background"}` on the intake stream, then close the intake stream. Surface adapters render this as: Telegram → bubble edit with "Working on: {title}"; CLI → progress line; editor → panel badge.
  - [x] 2-2. Start background execution on a new asyncio task with its own stream channel. Completion/failure transitions the `ConciergeTask` state and triggers a notification event (consumed by 49-4).
  - [x] 2-3. Preserve the existing `chat_queued` redirect for foreground-mode turns that hit a same-task lock. Background-mode turns get their own channel from the start and never redirect. `task_ack` replaces `chat_queued` for background-dispatched turns — a turn that would have been `chat_queued` under the old model now receives a `task_ack` with `state: "queued"` or `state: "running"` depending on whether it starts immediately or waits for a slot.
- [x] 3. Narrow serialization from project level toward task level
  - [x] 3-1. Extend the `ConcurrentDispatcher` queue key from `project_id` to `project_id:task_id` for background-dispatched tasks. Inline and foreground turns that share no task with running work proceed immediately. Bypass commands (`_is_bypass_command`) remain unaffected — they continue to use the synthetic `bypass:{uuid}` project key and never create or bind to a `ConciergeTask`.
  - [x] 3-2. Keep serialization where justified: a task in `waiting_input` state blocks same-task follow-ups (not same-project); a task holding a workflow mutation lock blocks other tasks targeting the same `graph_id`.
  - [x] 3-3. Two background tasks in the same project with different `graph_id` targets (or no workflow target) run concurrently. Document the ownership matrix: `{task_id, graph_id, pending_action_id}` determines the lock scope.
- [x] 4. Tighten resource and fairness policy
  - [x] 4-1. Active background task cap: 3 per project (configurable via `DAN_MAX_BACKGROUND_TASKS`). Queue overflow returns a `task_queued` ack with position. Global cap across projects: 10 (configurable via `DAN_MAX_GLOBAL_TASKS`). These caps are independent of `ResourceTracker.max_concurrent_runs` — the task cap governs concierge-level intake admission, while `ResourceTracker` governs execution slot allocation. A task can be accepted (under task cap) but internally queued for an execution slot (waiting for `ResourceTracker.try_acquire("run")`). Both must be checked: task cap at intake, run slot at execution start.
  - [x] 4-2. Concierge availability is independent of worker execution slots. The concierge intake path (triage + ack) always runs even when all background slots are occupied — it just queues the task instead of starting it. The existing `PriorityQueue` in `resources.py` can be reused for task-level overflow ordering when the execution slot cap is reached.
  - [x] 4-3. Cancellation and supersession are explicit `TaskRegistry.transition(...)` calls that set the old task to `cancelled`/`superseded`, record `superseded_by`, and interrupt the running asyncio task if still active. No silent queue drops.
- [x] 5. Add focused regressions and docs
  - [x] 5-1. Cover same-project concurrent intake, background acknowledgments, and task-aware blocking behavior
  - [x] 5-2. Document the newer availability contract and any remaining guardrails clearly

## Primary Files

- `src/dan/server/concierge/dispatcher.py` — extend queue key, add background dispatch path
- `src/dan/server/concierge/tiered_dispatch.py` — dispatch-mode classification after triage
- `src/dan/server/concierge/runtime/__init__.py` — background ack emission, detached execution
- `src/dan/server/concierge/task_registry.py` — consume for task creation and state transitions
- `tests/test_concierge/test_background_dispatch.py` — new: concurrent intake, ack delivery, cap enforcement

## Decisions

- **Concierge availability is more important than project-level serialization.** Blocking should be justified by task ownership or resource constraints, not by the mere fact that one project already has work running.
- **Dispatch mode is not the same as tier.** Tier stays about execution depth/budget; launch mode decides whether the concierge stays in the foreground or releases the turn after acknowledgement.
- **Background dispatch still needs a durable task object.** A queued or detached stream by itself is not the contract we want to harden.
- **Read-only workflow inspection is foreground work.** `workflow_query` turns can still require tools and rich context, but they are asking for an answer now, not a background job acknowledgement.

## Notes

- This plan intentionally stays inside concierge/dispatcher/runtime behavior. It does not attempt frontend task cards or new shell UI.
- The existing detached/background streaming behavior is still useful, but it should become an execution detail underneath explicit task-state transitions.
- **Existing `ResourceTracker`** in `resources.py` already manages `max_concurrent_runs` / `max_concurrent_llm_calls` with `try_acquire`/`release` and env-var configuration. The per-project background task cap (task 4-1) should be a separate counter — `ResourceTracker` governs execution slots, while the task cap governs concierge-level intake. Both must be checked: a task can be accepted (under task cap) but queued for execution (waiting for a run slot).
- **Existing `PriorityQueue`** in `resources.py` is heapq-backed with priority classification. Background-dispatched tasks can use this for overflow ordering when the task cap is reached.
- **Restart and in-flight tasks:** Background tasks run as `asyncio.Task` instances which are lost on process restart. Per the parent plan's restart recovery decision, 49-1 handles zombie cleanup (transitioning stale `running` tasks to `failed`). This plan does not need separate restart logic, but the `task_ack` rendering path should tolerate a `ConciergeTask` that transitioned to `failed` between ack emission and the user's next interaction.
- **`_is_bypass_command` interaction:** The existing bypass list includes `status`, `what's happening`, `/cancel`, etc. These remain dispatcher-level bypass commands and never create background tasks. When 49-4 registers `/status` as a structured fast command, bypass routing continues to apply — the bypass simply routes to the newly registered handler instead of full triage.
- **2026-04-05 follow-up:** read-only workflow-review turns such as `review existing workflows` and `what does this workflow do?` now stay `foreground` even when they also carry safe hints like `search_web`. This closes an availability bug where query-only workflow turns emitted `task_ack`, ended the intake stream, and fell through to the generic missing-terminal fallback instead of answering inline.
- **2026-04-05 follow-up 2:** saved-workflow catalog questions no longer depend on current-workflow continuity resolution before they can answer. `tier_executors.py` now skips the `current workflow` resolver path for inventory-style `workflow_query` turns like `review existing workflows`, and if a true follow-up still references a missing current workflow after restart, the reply is rendered as a normal assistant sentence instead of the raw resolver string.
