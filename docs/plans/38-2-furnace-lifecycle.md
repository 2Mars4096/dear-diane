# 38-2: Furnace Lifecycle State Machine

**Parent:** [38-review-hardening](38-review-hardening.md)
**Status:** completed
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

- [x] 1. Define explicit session states
  - [x] 1-1. Add `SessionState` enum: `queued`, `active`, `paused`, `cancelling`, `cancelled`, `failed`, `completed`
  - [x] 1-2. Define allowed transitions (e.g. `active → cancelling → cancelled`, `queued → active`, etc.)
  - [x] 1-3. Replace bare string status assignments with state transition calls that enforce the transition table
- [x] 2. Fix cancellation
  - [x] 2-1. Add a `cancelling` state and a per-session `CancellationToken` (or `asyncio.Event`)
  - [x] 2-2. `cancel_session()` sets `cancelling` and signals the token
  - [x] 2-3. `_execute_furnace_pipeline()` checks the token at each source/phase boundary and exits cleanly
  - [x] 2-4. On clean exit, transition to `cancelled`
- [x] 3. Prevent duplicate workers
  - [x] 3-1. Acquire the session lock (or check a scheduling flag) before `asyncio.create_task()`, not inside the task
  - [x] 3-2. `resume_session()` must check current state and reject if already `active` or `queued`
  - [x] 3-3. Add a `worker_task` reference on the session so the system knows whether a task is already scheduled
- [x] 4. Fix delete/cancel race
  - [x] 4-1. `delete_session()` should block or reject if state is `cancelling` (not just `active`)
  - [x] 4-2. Chosen contract: reject deletes while a live worker is queued/active/cancelling, but recover stale persisted worker states with no live task
- [x] 5. Multi-subscriber SSE
  - [x] 5-1. Replace the single-queue `_session_progress[session_id]` with a set/list of subscriber queues
  - [x] 5-2. Progress events fan out to all active subscribers
  - [x] 5-3. Subscriber cleanup removes only that subscriber's queue, not the whole session entry
- [x] 6. Add tests
  - [x] 6-1. Test that cancel actually stops a running pipeline (mock a slow source, cancel, verify no further writes)
  - [x] 6-2. Test that rapid repeated start/resume returns an error instead of queuing duplicates
  - [x] 6-3. Test that delete rejects while a live worker exists, but stale queued/active states with no worker are recovered instead of staying stuck forever
  - [x] 6-4. Test two SSE subscribers on the same session both receive progress events
  - [x] 6-5. Test that disconnecting one subscriber doesn't affect the other

## Decisions

- Keep worker task references and cancellation tokens in router-local registries rather than serializing live task handles into session JSON.
- Use `queued` as the pre-scheduled persisted state so duplicate `start` / `resume` requests can be rejected before the worker acquires the lock.
- Handle delete-vs-cancel races by rejecting deletes while a worker is queued/active/cancelling rather than implicitly awaiting worker cleanup inside the delete endpoint.
- Recover stale persisted `active` / `queued` / `cancelling` states to `paused` / `cancelled` when no live worker exists (for example after process restart), so sessions do not get stuck in a phantom-running state.

## Notes

- The module audit suggests modeling this as a state machine with explicit transitions. That approach addresses tasks 1-4 as a cohesive design rather than separate patches.
- Consider whether `worker_task` should be stored on the session object itself or in a separate registry. Session object is simpler; registry is cleaner for serialization.
- Implementation landed in `models.py`, `session_store.py`, and `furnace.py`; regressions added in `tests/test_furnace_api.py` and `tests/test_recipe_distillation.py`.
