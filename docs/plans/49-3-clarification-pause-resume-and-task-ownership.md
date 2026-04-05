# 49-3: Clarification, Pause/Resume, and Task Ownership

**Parent:** [49-concierge-service-hardening](49-concierge-service-hardening.md)
**Status:** completed
**Goal:** Bind clarification, retry, refinement, and supersession behavior to explicit task ownership so follow-up turns stop behaving like loose project-queue messages and start behaving like controlled task actions.

## Dependencies
- **49-1** must land first: task-scoped pending actions require `task_id` on the `ConciergeTask` model.
- **49-2** should land or be coordinated: background dispatch changes when a task enters `waiting_input` — a backgrounded task that pauses for clarification must notify the user and remain resumable, not silently stall.
- Consumes the existing pending-action infrastructure in `pending_actions.py`; this plan narrows it from project-scope to task-scope.
- The follow-up classification contract (task 2) becomes the canonical intake classifier for 49-4 (status queries) and 49-5 (event attribution).
- **49-4** defines `waiting_input` timeout thresholds (`DAN_TASK_WAITING_TIMEOUT_MINUTES`, default 30 min). When the timeout fires, 49-3's pending-action record must be cleared and the task transitioned — see task 1-2 note on timeout interaction.
- **Existing `TriageResult.resume_task_id`** already provides a triage-layer task-binding hint. The follow-up classifier (task 2) should consume this as the primary resolution signal before falling back to heuristic matching.

## Success Criteria
- Pending actions carry `task_id` and only block same-task follow-ups, not same-project queue drain.
- Follow-up turns are classified into one of 6 canonical types (`new_task`, `refine_task`, `supersede_task`, `retry_task`, `answer_clarification`, `query_status`) with explicit task binding.
- Ambiguous follow-ups that cannot resolve to a single task escalate to the user with a disambiguation prompt naming the candidate tasks, rather than silently attaching to the most recent.
- Retry preserves `task_id` while recording a new attempt; supersede creates a new task and marks the old one `superseded`.
- Clarification answers resume the paused task (transition from `waiting_input` → `running`) within the same `task_id`.

## Context

Recent concierge fixes already tightened low-confidence clarification persistence and queue ordering, but the remaining mental model is still closer to "this project has a pending follow-up" than "this task is waiting for input":

- pending actions are still easier to reason about at project/message scope than at task scope
- refinement, retry, and supersession are not yet a clean first-class follow-up classification contract
- once tasks become durable, pause/resume and ownership semantics need to become just as durable

This plan makes task ownership explicit.

## Tasks

- [x] 1. Make pending actions task-scoped
  - [x] 1-1. Add `task_id` and `attempt_session_id` fields to the pending-action record. Pending-action lookup switches from `get_by_project(project_id)` to `get_by_task(task_id)` as the primary path; project-level fallback remains for backward compat during migration.
  - [x] 1-2. When a pending action is created, transition the owning `ConciergeTask` to `waiting_input`. When resolved (answered or expired), transition back to `running`. The task state is the authority — pending-action presence alone is not enough to infer pause. Timeout interaction: when 49-4's stuck-detection sweep finds a `waiting_input` task exceeding `DAN_TASK_WAITING_TIMEOUT_MINUTES`, it should call back into this module to clear the pending action and transition the task to `failed` with `metadata: {reason: "waiting_input_timeout"}`.
  - [x] 1-3. Replay context: persist the last assistant message, the pending-action prompt, and up to 3 preceding user/assistant turns (configurable) on the pending-action record. This is the minimum context needed to resume without replaying the full session history. Cap total replay context at 4K tokens.
- [x] 2. Define the follow-up classification contract
  - [x] 2-1. Add a `FollowUpType` enum: `new_task`, `refine_task`, `supersede_task`, `retry_task`, `answer_clarification`, `query_status`. Classification runs after triage, before dispatch-mode selection. `FollowUpType` is orthogonal to the existing triage `intent` enum (`ask`, `agent`, `plan`): triage `intent` determines execution depth/budget, while `FollowUpType` determines task binding. Both are computed — `intent` by the triage classifier, `FollowUpType` by the follow-up classifier — and both are carried on the dispatch context. When `TriageResult.resume_task_id` is set by the triage layer, use it as the primary task-binding signal (equivalent to resolution priority (a)).
  - [x] 2-2. Resolution priority: (a) `TriageResult.resume_task_id` or explicit task reference in message (e.g. "retry task_abc123"); (b) active `waiting_input` task in the same project (unique match only); (c) most-recent non-terminal task if the message clearly continues the same intent — similarity measured by Jaccard coefficient on lowercased word stems (using the same stemming as triage keyword extraction), threshold ≥ 0.4; (d) if none match or multiple match, escalate. "Latest active task" is only used as heuristic (c), never as a silent default.
  - [x] 2-3. Escalation produces a disambiguation prompt listing candidate tasks by title, state, and age: "Which task did you mean? (1) {title_a} — running, 2m ago (2) {title_b} — waiting input, 5m ago". The user's reply is re-classified with the explicit reference. If the reply does not match any listed candidate (e.g., the user says something entirely unrelated), treat it as `new_task` rather than re-escalating — avoid infinite disambiguation loops.
- [x] 3. Tighten retry, resume, and supersession semantics
  - [x] 3-1. Clarification answers resume the paused task instead of just unblocking a project queue
  - [x] 3-2. Retry preserves task identity while recording a new execution attempt
  - [x] 3-3. Supersede and cancel paths leave explicit audit state on the older task rather than disappearing it from view
- [x] 4. Thread task ownership through surface metadata and traces
  - [x] 4-1. Preserve task ownership metadata in persisted user/assistant turns and session exports
  - [x] 4-2. Ensure status and notification systems can tell whether a follow-up affected an existing task or created a new one
  - [x] 4-3. Keep the task-ownership contract surface-neutral so CLI, editor, and adapters can consume the same backend semantics later
- [x] 5. Add focused regressions and docs
  - [x] 5-1. Cover clarification replay, retry history, supersession, and ambiguous follow-up handling
  - [x] 5-2. Document the task-ownership rules and any intentional fallback heuristics

## Primary Files

- `src/dan/server/concierge/pending_actions.py` — add `task_id`, replay context, task-scoped lookup
- `src/dan/server/concierge/followup_classifier.py` — new: `FollowUpType` enum, classification logic, disambiguation prompt
- `src/dan/server/concierge/tiered_dispatch.py` — integrate follow-up classification before dispatch
- `src/dan/server/concierge/task_registry.py` — consume for `waiting_input` transitions
- `tests/test_concierge/test_task_ownership.py` — new: clarification replay, retry identity, supersession, disambiguation

## Decisions

- **Task ownership is explicit by default.** "Use the latest active task" is allowed only as a bounded fallback, not as the main contract.
- **Retry is not a new task.** It should preserve the original task identity while recording a new attempt.
- **Clarification pauses a task, not a project.** Unrelated tasks should not remain blocked merely because one task is waiting for input.

## Notes

- This plan is where the current project-scoped pending-action behavior should be narrowed into a cleaner task-scoped model.
- It is also the plan that makes later task cards, status summaries, and a dashboard trustworthy instead of heuristic.
- **`FollowUpType` vs triage `intent`:** These are complementary, not competing. Triage `intent` (`ask`, `agent`, `plan`) determines the session tier and execution budget. `FollowUpType` determines whether the turn creates a new task, resumes an existing one, or queries status. Both are resolved before dispatch-mode selection. In practice, a `retry_task` follow-up with an `agent` intent starts a new attempt at tier 1/2, while a `query_status` follow-up with an `ask` intent stays inline.
- **Token counting for replay context (task 1-3):** Use the same character-based approximation as the existing context-pack infrastructure (4 chars ≈ 1 token). If a proper tokenizer is available on the execution path, prefer it, but do not add a tokenizer dependency solely for replay context sizing.
