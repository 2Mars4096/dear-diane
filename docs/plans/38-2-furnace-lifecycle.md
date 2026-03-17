# 38-2: Furnace Lifecycle State Machine

**Parent:** [38-review-hardening](38-review-hardening.md)
**Status:** not-started
**Goal:** Make Furnace session execution a proper state machine that prevents runaway workers, duplicate runs, and single-subscriber limitations.

## Context

The current Furnace runtime has several lifecycle correctness issues:
- `cancel_session()` sets status to `"failed"` but the running worker doesn't check for it, so work continues in the background.
- `start_session()` / `resume_session()` can queue duplicate background tasks because the lock is acquired inside the task, not before scheduling.
- `delete_session()` only blocks on `status == "active"`, so a just-cancelled session with a still-running worker can be deleted.
- SSE progress streaming stores one queue per `session_id`, so a second subscriber overwrites the first.

Relevant code:
- `src/dan/server/routers/furnace.py`: `start_session()`, `resume_session()`, `cancel_session()`, `delete_session()`, `session_events()`, `_execute_furnace_pipeline()`, `_session_progress`

## Tasks

- [ ] 1. Define explicit session states
  - [ ] 1-1. Add `SessionState` enum: `queued`, `active`, `paused`, `cancelling`, `cancelled`, `failed`, `completed`
  - [ ] 1-2. Define allowed transitions (e.g. `active → cancelling → cancelled`, `queued → active`, etc.)
  - [ ] 1-3. Replace bare string status assignments with state transition calls that enforce the transition table
- [ ] 2. Fix cancellation
  - [ ] 2-1. Add a `cancelling` state and a per-session `CancellationToken` (or `asyncio.Event`)
  - [ ] 2-2. `cancel_session()` sets `cancelling` and signals the token
  - [ ] 2-3. `_execute_furnace_pipeline()` checks the token at each source/phase boundary and exits cleanly
  - [ ] 2-4. On clean exit, transition to `cancelled`
- [ ] 3. Prevent duplicate workers
  - [ ] 3-1. Acquire the session lock (or check a scheduling flag) before `asyncio.create_task()`, not inside the task
  - [ ] 3-2. `resume_session()` must check current state and reject if already `active` or `queued`
  - [ ] 3-3. Add a `worker_task` reference on the session so the system knows whether a task is already scheduled
- [ ] 4. Fix delete/cancel race
  - [ ] 4-1. `delete_session()` should block or reject if state is `cancelling` (not just `active`)
  - [ ] 4-2. Optionally: `delete_session()` cancels first, awaits the worker, then deletes
- [ ] 5. Multi-subscriber SSE
  - [ ] 5-1. Replace the single-queue `_session_progress[session_id]` with a set/list of subscriber queues
  - [ ] 5-2. Progress events fan out to all active subscribers
  - [ ] 5-3. Subscriber cleanup removes only that subscriber's queue, not the whole session entry
- [ ] 6. Add tests
  - [ ] 6-1. Test that cancel actually stops a running pipeline (mock a slow source, cancel, verify no further writes)
  - [ ] 6-2. Test that rapid repeated start/resume returns an error instead of queuing duplicates
  - [ ] 6-3. Test delete-after-cancel waits for worker cleanup
  - [ ] 6-4. Test two SSE subscribers on the same session both receive progress events
  - [ ] 6-5. Test that disconnecting one subscriber doesn't affect the other

## Decisions

- (filled in during execution)

## Notes

- The module audit suggests modeling this as a state machine with explicit transitions. That approach addresses tasks 1-4 as a cohesive design rather than separate patches.
- Consider whether `worker_task` should be stored on the session object itself or in a separate registry. Session object is simpler; registry is cleaner for serialization.
