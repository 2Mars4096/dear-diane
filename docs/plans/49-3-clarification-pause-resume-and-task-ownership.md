# 49-3: Clarification, Pause/Resume, and Task Ownership

**Parent:** [49-concierge-service-hardening](49-concierge-service-hardening.md)
**Status:** not-started
**Goal:** Bind clarification, retry, refinement, and supersession behavior to explicit task ownership so follow-up turns stop behaving like loose project-queue messages and start behaving like controlled task actions.

## Context

Recent concierge fixes already tightened low-confidence clarification persistence and queue ordering, but the remaining mental model is still closer to "this project has a pending follow-up" than "this task is waiting for input":

- pending actions are still easier to reason about at project/message scope than at task scope
- refinement, retry, and supersession are not yet a clean first-class follow-up classification contract
- once tasks become durable, pause/resume and ownership semantics need to become just as durable

This plan makes task ownership explicit.

## Tasks

- [ ] 1. Make pending actions task-scoped
  - [ ] 1-1. Carry `task_id` and attempt/session linkage on pending-action records
  - [ ] 1-2. Add explicit `waiting_input` task semantics instead of inferring the paused state only from pending-action presence
  - [ ] 1-3. Preserve enough replay context to resume safely after clarification or approval
- [ ] 2. Define the follow-up classification contract
  - [ ] 2-1. Distinguish `new_task`, `refine_task`, `supersede_task`, `retry_task`, `answer_clarification`, and `query_status`
  - [ ] 2-2. Prefer explicit task references and bounded heuristics over "latest active task" guessing
  - [ ] 2-3. Escalate ambiguous ownership questions instead of silently hijacking the wrong task
- [ ] 3. Tighten retry, resume, and supersession semantics
  - [ ] 3-1. Clarification answers resume the paused task instead of just unblocking a project queue
  - [ ] 3-2. Retry preserves task identity while recording a new execution attempt
  - [ ] 3-3. Supersede and cancel paths leave explicit audit state on the older task rather than disappearing it from view
- [ ] 4. Thread task ownership through surface metadata and traces
  - [ ] 4-1. Preserve task ownership metadata in persisted user/assistant turns and session exports
  - [ ] 4-2. Ensure status and notification systems can tell whether a follow-up affected an existing task or created a new one
  - [ ] 4-3. Keep the task-ownership contract surface-neutral so CLI, editor, and adapters can consume the same backend semantics later
- [ ] 5. Add focused regressions and docs
  - [ ] 5-1. Cover clarification replay, retry history, supersession, and ambiguous follow-up handling
  - [ ] 5-2. Document the task-ownership rules and any intentional fallback heuristics

## Primary Files

- `src/dan/server/concierge/pending_actions.py`
- `src/dan/server/concierge/tiered_dispatch.py`
- `src/dan/server/concierge/dispatcher.py`
- `tests/test_concierge/`

## Decisions

- **Task ownership is explicit by default.** "Use the latest active task" is allowed only as a bounded fallback, not as the main contract.
- **Retry is not a new task.** It should preserve the original task identity while recording a new attempt.
- **Clarification pauses a task, not a project.** Unrelated tasks should not remain blocked merely because one task is waiting for input.

## Notes

- This plan is where the current project-scoped pending-action behavior should be narrowed into a cleaner task-scoped model.
- It is also the plan that makes later task cards, status summaries, and a dashboard trustworthy instead of heuristic.
