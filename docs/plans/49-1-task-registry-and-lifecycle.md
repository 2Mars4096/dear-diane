# 49-1: Task Registry and Lifecycle

**Parent:** [49-concierge-service-hardening](49-concierge-service-hardening.md)
**Status:** not-started
**Goal:** Introduce a first-class concierge task model and registry above root sessions so long-running, background, paused, retried, or superseded work has stable identity and explicit lifecycle state.

## Context

The current concierge runtime has strong session and trace primitives, but the primary durable unit is still the root session created from a user message. That is not enough for the operating model we want next:

- a single user-visible task may span multiple root-session attempts because of clarification, retry, or supersession
- background work should be trackable even when the original stream is no longer the active surface
- status and notification logic should talk about tasks, not raw sessions or channel IDs

This plan creates that missing control-plane layer.

## Tasks

- [ ] 1. Define the concierge task model
  - [ ] 1-1. Add a `ConciergeTask` model with stable `task_id`, title/summary, ownership metadata, and timestamps
  - [ ] 1-2. Define explicit task states such as `queued`, `running`, `waiting_input`, `completed`, `failed`, `cancelled`, and `superseded`
  - [ ] 1-3. Separate task state from session state so tasks can outlive one root-session attempt
- [ ] 2. Add a task registry layer
  - [ ] 2-1. Create registry operations for create/get/list/update by task id, project, thread, and surface context
  - [ ] 2-2. Record task-to-root-session linkage and preserve prior attempts for retry/resume history
  - [ ] 2-3. Define pruning and retention rules so recent completed tasks remain inspectable without keeping execution state forever
- [ ] 3. Integrate task creation into concierge intake
  - [ ] 3-1. Define which turns remain ephemeral and taskless versus which turns create tracked tasks
  - [ ] 3-2. Thread `task_id` through dispatcher, session creation, pending-action metadata, and assistant-turn metadata
  - [ ] 3-3. Preserve compatibility for current stream/channel behavior while making task identity canonical
- [ ] 4. Make task provenance observable
  - [ ] 4-1. Include `task_id`, task state, and latest attempt metadata in persisted turn metadata and exported traces
  - [ ] 4-2. Extend telemetry so task lifecycle events can be aggregated separately from raw session completion events
  - [ ] 4-3. Ensure the task model can support future task-card and dashboard surfaces without exposing session internals directly
- [ ] 5. Add focused regressions and docs
  - [ ] 5-1. Cover creation, state transitions, retry/supersede history, and retention behavior
  - [ ] 5-2. Document the task-vs-session boundary clearly in concierge docs and tests

## Primary Files

- `src/dan/server/concierge/` task/session/dispatcher runtime modules
- `tests/test_concierge/`
- `docs/todo.md`
- `docs/changelog.md`

## Decisions

- **Task is not the same as session.** A task is the long-lived user-facing work item; a session is one execution attempt or branch within that task.
- **One active root attempt at a time is enough for the first iteration.** The registry should still preserve prior attempts so retry/resume/supersede history is explicit.
- **Task identity must be explicit and inspectable.** It should not be reconstructed later from stream channel IDs or by guessing from the latest message.

## Notes

- This plan is the foundation for every later concierge-only hardening step in the 49 family.
- Existing `SessionTrace` and exported session trees remain valuable, but they should become subordinate execution detail rather than the main control-plane object.
