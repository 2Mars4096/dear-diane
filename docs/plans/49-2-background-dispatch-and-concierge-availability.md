# 49-2: Background Dispatch and Concierge Availability

**Parent:** [49-concierge-service-hardening](49-concierge-service-hardening.md)
**Status:** not-started
**Goal:** Let the concierge acknowledge backgroundable work immediately, dispatch it without monopolizing the active chat turn, and stay available for new user input while tracked task execution continues.

## Context

The current dispatcher and tiered dispatch path already support queueing, streaming, and cross-project concurrency, but the default same-project contract is still too blocking for the "always-on concierge" model:

- a project with one active task still tends to serialize follow-up intake behind that active task
- the system has good stream and progress primitives, but no explicit background-dispatch contract at the task level
- the run-slot logic is closer to execution capacity than concierge availability, which makes the assistant feel busier than it should

This plan tightens the dispatch model so the concierge is always ready to take the next instruction even when workers are still running.

## Tasks

- [ ] 1. Define launch/dispatch modes
  - [ ] 1-1. Add an explicit launch-mode or dispatch-mode concept distinct from existing session tiering
  - [ ] 1-2. Decide which routes default to foreground versus background dispatch and which always stay inline
  - [ ] 1-3. Keep fast commands, social turns, and short deterministic answers on the existing immediate path
- [ ] 2. Add a background-dispatch acknowledgment path
  - [ ] 2-1. Emit a task-aware acknowledgment result with `task_id`, summary, and initial state
  - [ ] 2-2. Start background execution without holding the original intake stream open for the entire task lifetime
  - [ ] 2-3. Preserve compatibility with existing progress/run handoff paths where foreground execution still makes sense
- [ ] 3. Narrow serialization from project level toward task level
  - [ ] 3-1. Replace same-project blocking intake with task-aware routing and ownership decisions
  - [ ] 3-2. Keep serialization or locking where one task truly owns a shared resource or unresolved clarification
  - [ ] 3-3. Ensure unrelated tasks in the same project can coexist without false queueing
- [ ] 4. Tighten resource and fairness policy
  - [ ] 4-1. Define active-task caps, queue overflow policy, and per-project fairness for background work
  - [ ] 4-2. Separate concierge availability from worker execution capacity in the dispatcher/resource model
  - [ ] 4-3. Make cancellation and supersession of background tasks explicit instead of treating them like generic queue replacement
- [ ] 5. Add focused regressions and docs
  - [ ] 5-1. Cover same-project concurrent intake, background acknowledgments, and task-aware blocking behavior
  - [ ] 5-2. Document the newer availability contract and any remaining guardrails clearly

## Primary Files

- `src/dan/server/concierge/dispatcher.py`
- `src/dan/server/concierge/tiered_dispatch.py`
- `src/dan/server/concierge/resources.py`
- `tests/test_concierge/`

## Decisions

- **Concierge availability is more important than project-level serialization.** Blocking should be justified by task ownership or resource constraints, not by the mere fact that one project already has work running.
- **Dispatch mode is not the same as tier.** Tier stays about execution depth/budget; launch mode decides whether the concierge stays in the foreground or releases the turn after acknowledgement.
- **Background dispatch still needs a durable task object.** A queued or detached stream by itself is not the contract we want to harden.

## Notes

- This plan intentionally stays inside concierge/dispatcher/runtime behavior. It does not attempt frontend task cards or new shell UI.
- The existing detached/background streaming behavior is still useful, but it should become an execution detail underneath explicit task-state transitions.
