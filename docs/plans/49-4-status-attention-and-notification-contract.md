# 49-4: Status, Attention, and Notification Contract

**Parent:** [49-concierge-service-hardening](49-concierge-service-hardening.md)
**Status:** not-started
**Goal:** Define task-aware status summaries, attention rules, and notification semantics so the concierge can supervise multiple tasks without interrupting the user noisily or forcing them to reverse-engineer raw stream state.

## Context

The current system has progress, status commands, and telemetry, but the control-plane semantics are still too low-level:

- active work is easier to observe as stream activity, queue state, or traces than as human-meaningful task state
- completion, failure, and needs-input conditions do not yet flow through one deliberate attention model
- natural-language status questions should eventually read from a task registry, not from ad hoc project memory plus whatever stream is currently active

This plan defines that contract before any frontend task-card surface is added.

## Tasks

- [ ] 1. Define the task attention model
  - [ ] 1-1. Add explicit attention-oriented task states such as `silent_running`, `needs_input`, `completed_unread`, and `failed_unread` or their equivalent derived flags
  - [ ] 1-2. Define which state transitions are silent, which deserve immediate surfacing, and which should be batched
  - [ ] 1-3. Keep the attention model derived from task state and policy rather than raw websocket behavior
- [ ] 2. Tighten the status query contract
  - [ ] 2-1. Define how `/status`, "what's happening", and similar follow-ups map to active and recent tasks
  - [ ] 2-2. Prefer task-aware summaries over project-wide prose when the user is clearly asking about running work
  - [ ] 2-3. Ensure multiple active tasks can be summarized without turning status into noise
- [ ] 3. Define notification semantics
  - [ ] 3-1. Surface completion, failure, and needs-input transitions deliberately; keep ordinary background progress quiet by default
  - [ ] 3-2. Add batching/coalescing rules when multiple tasks finish while the user is elsewhere
  - [ ] 3-3. Define summary-vs-detail behavior so large results do not become full notifications by accident
- [ ] 4. Add telemetry and analytics hooks
  - [ ] 4-1. Emit task attention and notification events separately from raw execution events
  - [ ] 4-2. Record stuck/aging indicators and completion-latency buckets for operational review
  - [ ] 4-3. Keep the event model stable enough for later dashboard and adapter consumption
- [ ] 5. Add focused regressions and docs
  - [ ] 5-1. Cover status-query summaries, coalesced completions, and needs-input notification behavior
  - [ ] 5-2. Document the attention budget and notification policy explicitly

## Primary Files

- `src/dan/server/concierge/command_registry.py`
- `src/dan/server/concierge/runtime/`
- `src/dan/server/telemetry.py`
- `tests/test_concierge/`

## Decisions

- **Status is task-first.** Status and natural-language supervision queries should read from task state, not infer from stream presence or the latest assistant response.
- **Attention budget is a concierge concern.** Surface-specific rendering can differ later, but the state transitions that deserve attention should be consistent at the backend contract layer.
- **Ordinary background progress stays quiet.** Completion, failure, and needs-input transitions matter more than verbose heartbeat spam.

## Notes

- This plan should leave the backend ready for future task cards and notifications without forcing frontend work now.
- It also gives `/status` and related follow-up turns a much cleaner data source than today's mixed project/session heuristics.
