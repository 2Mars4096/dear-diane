# 49-5: Dispatcher Observability and Dashboard Backend Contract

**Parent:** [49-concierge-service-hardening](49-concierge-service-hardening.md)
**Status:** not-started
**Goal:** Expose stable backend snapshot and event contracts for task/dispatcher inspection so future manager-style or "dispatcher dashboard" UX can be built on a deliberate data model instead of reverse-engineering traces, queues, and live streams.

## Context

The repo already has rich internal observability:

- dispatcher queues and response buses
- session trees and exported traces
- progress events
- telemetry rollups

But there is still no clean manager-facing contract that answers obvious operational questions directly:

- what tasks are active right now?
- which ones are waiting for input, stuck, or superseded?
- what is the dispatcher doing per project?
- how many run slots are busy and what is queued behind them?

This plan defines those backend surfaces without requiring frontend work yet.

## Tasks

- [ ] 1. Define snapshot schemas for task and dispatcher state
  - [ ] 1-1. Add a task summary contract for active, paused, queued, completed, failed, and superseded tasks
  - [ ] 1-2. Add a dispatcher summary contract for slots, queue depth, per-project active counts, and resource pressure
  - [ ] 1-3. Link task summaries to their current or latest root-session attempt without exposing raw internal structures as the primary API
- [ ] 2. Define live event contracts
  - [ ] 2-1. Add task lifecycle event shapes for start, pause, resume, completion, failure, cancellation, and supersession
  - [ ] 2-2. Define a stable stream or subscription contract for later dashboard consumption
  - [ ] 2-3. Keep the event vocabulary narrow enough that snapshots and streams can be reconciled deterministically
- [ ] 3. Add operational metrics and audit fields
  - [ ] 3-1. Record task aging, retry counts, pause reasons, and unresolved-input markers
  - [ ] 3-2. Surface supersession/cancel/retry transitions explicitly instead of only as completion text
  - [ ] 3-3. Preserve enough audit detail that analytics and later operator tooling can explain what happened
- [ ] 4. Add acceptance and consistency coverage
  - [ ] 4-1. Build deterministic multi-task scenarios that assert snapshot/stream consistency
  - [ ] 4-2. Cover queue pressure, clarification waits, retries, and supersessions in the same acceptance battery
  - [ ] 4-3. Verify the manager-facing contracts remain stable even when the lower-level execution/session internals evolve
- [ ] 5. Document the backend-only boundary
  - [ ] 5-1. Make it explicit that this plan defines the contract for a future dashboard but does not implement frontend UI
  - [ ] 5-2. Document how the snapshot/event model relates to telemetry, traces, and status queries

## Primary Files

- `src/dan/server/concierge/dispatcher.py`
- `src/dan/server/concierge/runtime/`
- `src/dan/server/telemetry.py`
- `tests/test_concierge/`

## Decisions

- **Dashboard consumes task and dispatcher contracts, not raw streams.** The backend model should already answer the operational questions directly.
- **Snapshot and event models must reconcile.** A later dashboard should be able to recover from reconnects without bespoke repair logic.
- **This is backend-only in the 49 tranche.** UI implementation is intentionally deferred.

## Notes

- This plan is the explicit home for the "dispatcher manager dashboard" idea while keeping the current tranche backend-only.
- It should be drafted early enough that `49-1` through `49-4` do not invent task fields or event names that a later consumer cannot use cleanly.
