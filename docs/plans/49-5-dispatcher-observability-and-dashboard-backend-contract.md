# 49-5: Dispatcher Observability and Dashboard Backend Contract

**Parent:** [49-concierge-service-hardening](49-concierge-service-hardening.md)
**Status:** completed
**Goal:** Expose stable backend snapshot and event contracts for task/dispatcher inspection so future manager-style or "dispatcher dashboard" UX can be built on a deliberate data model instead of reverse-engineering traces, queues, and live streams.

## Dependencies
- **49-1** must land first: snapshot schemas are projections of `ConciergeTask` model fields.
- **49-2** provides `DispatchMode` and background dispatch semantics that the dispatcher summary reflects.
- **49-3** provides the follow-up classification and task-ownership model that events attribute.
- **49-4** provides the attention model and notification events that the live-event stream carries.
- Field names and event types defined here must align with 49-1 model fields and 49-4 telemetry shapes. Coordinate early — per parent sequencing, draft schemas during 49-1 implementation.

## Success Criteria
- A `GET /api/concierge/snapshot` (or internal equivalent) returns a JSON object listing all non-terminal tasks, dispatcher slot usage, and per-project queue depths — sufficient for a future dashboard to render without additional queries.
- A `task_lifecycle_event` stream contract is documented with a fixed vocabulary of ≤12 event types covering start, pause, resume, complete, fail, cancel, supersede, stuck, and attention changes.
- Snapshot and event stream are reconcilable: replaying events from a known snapshot produces a valid current snapshot (no hidden state).
- Acceptance scenarios cover at least: multi-task concurrent state, queue pressure under cap, clarification wait, retry, and supersession — all verified against snapshot consistency.

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

- [x] 1. Define snapshot schemas for task and dispatcher state
  - [x] 1-1. Task summary schema (draft): `{task_id, project_id, title, summary, state, attention_reason, dispatch_mode, created_at, updated_at, attempt_count, current_attempt_ref (nullable), superseded_by (nullable)}`. `current_attempt_ref` is an opaque reference string (not a raw `session_id`) that a consumer can pass to `GET /api/session/{ref}` to retrieve the execution trace — this keeps session identity out of the manager-facing schema while preserving the link. No raw session tree or execution trace in the snapshot — those stay behind the session endpoint.
  - [x] 1-2. Dispatcher summary schema (draft): `{active_background_slots, max_background_slots, global_active, global_max, per_project: [{project_id, active_count, queue_depth, oldest_queued_age}]}`. Derived from `ConcurrentDispatcher` state and `TaskRegistry`.
  - [x] 1-3. Combined snapshot: `{tasks: [TaskSummary], dispatcher: DispatcherSummary, snapshot_at: ISO8601}`. The `current_session_id` field links to the execution trace without inlining it.
- [x] 2. Define live event contracts
  - [x] 2-1. Task lifecycle event vocabulary (≤12 types, soft cap — may grow to 15 if future phases add new transitions, but each addition must be justified): `task_created`, `task_started`, `task_paused` (→waiting_input), `task_resumed`, `task_completed`, `task_failed`, `task_cancelled`, `task_superseded`, `task_stuck`, `task_attention_change`, `task_queued`, `task_dequeued`. Each event carries `{event_type, task_id, project_id, timestamp, metadata: {}}`. The vocabulary is versioned with a `schema_version: 1` field on the event stream so consumers can detect incompatible changes.
  - [x] 2-2. Subscription contract: internal `async for event in task_event_stream(project_id=None)` generator. Future HTTP/SSE endpoint deferred to dashboard UI phase. The generator yields `TaskLifecycleEvent` dataclasses. Backpressure model: the generator reads from a bounded `asyncio.Queue(maxsize=256)` per subscriber. If a subscriber falls behind and the queue fills, the oldest unread events are dropped and replaced with a single `events_dropped` sentinel carrying the count of lost events — the subscriber must request a fresh snapshot to reconcile. If no subscribers are active, events are still written to the in-memory log (task 3-3) but not buffered for delivery.
  - [x] 2-3. Reconcilability invariant: `snapshot + replay(events since snapshot_at) == current snapshot`. This means every state-changing operation must emit exactly one event, and events carry enough metadata to apply the transition. No side-channel state mutations. Testing: build deterministic multi-task scenarios that capture a snapshot, replay subsequent events, and assert equality with a second snapshot taken after the events. The `events_dropped` sentinel breaks reconcilability — a consumer that receives it must request a fresh snapshot.
- [x] 3. Add operational metrics and audit fields
  - [x] 3-1. Audit fields on `TaskSummary`: `age_seconds`, `attempt_count`, `last_pause_reason` (nullable), `has_unresolved_input` (bool). These are computed at snapshot time, not stored separately.
  - [x] 3-2. Supersession/cancel/retry events carry `{predecessor_task_id, reason}` in metadata so the transition chain is explicit and traversable.
  - [x] 3-3. Event log retention: keep the last 1000 events in a circular in-memory buffer for snapshot reconciliation. When the buffer is full, the oldest event is evicted (FIFO). Older events are available only via telemetry/trace export. The buffer is lost on process restart — on startup, the system emits a synthetic `snapshot_reset` event so consumers know prior event history is unavailable and must request a fresh snapshot.
- [x] 4. Add acceptance and consistency coverage
  - [x] 4-1. Build deterministic multi-task scenarios that assert snapshot/stream consistency
  - [x] 4-2. Cover queue pressure, clarification waits, retries, and supersessions in the same acceptance battery
  - [x] 4-3. Verify the manager-facing contracts remain stable even when the lower-level execution/session internals evolve
- [x] 5. Document the backend-only boundary
  - [x] 5-1. Make it explicit that this plan defines the contract for a future dashboard but does not implement frontend UI
  - [x] 5-2. Document how the snapshot/event model relates to telemetry, traces, and status queries

## Primary Files

- `src/dan/server/concierge/task_snapshot.py` — new: snapshot builder, `TaskSummary`/`DispatcherSummary` schemas, event stream generator
- `src/dan/server/concierge/task_registry.py` — emit lifecycle events on state transitions
- `src/dan/server/concierge/dispatcher.py` — expose slot/queue counts for dispatcher summary
- `src/dan/server/telemetry.py` — register `task_lifecycle_event` type
- `tests/test_concierge/test_task_snapshot_consistency.py` — new: multi-task snapshot assertions, event replay reconciliation

## Decisions

- **Dashboard consumes task and dispatcher contracts, not raw streams.** The backend model should already answer the operational questions directly.
- **Snapshot and event models must reconcile.** A later dashboard should be able to recover from reconnects without bespoke repair logic.
- **This is backend-only in the 49 tranche.** UI implementation is intentionally deferred.

## Notes

- This plan is the explicit home for the "dispatcher manager dashboard" idea while keeping the current tranche backend-only.
- It should be drafted early enough that `49-1` through `49-4` do not invent task fields or event names that a later consumer cannot use cleanly.
- **`current_attempt_ref` vs `current_session_id`:** The snapshot schema intentionally uses an opaque `current_attempt_ref` instead of exposing raw `session_id` values. This keeps session identity as an implementation detail behind the session endpoint. The ref can be the `session_id` in practice, but the schema does not name it as such.
- **Authentication/authorization for snapshot endpoint:** Not addressed in this phase since the endpoint is internal. When a future HTTP/SSE surface is added, auth should be added at that layer, not inside the snapshot builder.
- **Event vocabulary growth:** The ≤12 cap is a soft guideline to keep the vocabulary manageable. If future phases (e.g., task delegation, sub-task creation) need new event types, they can be added up to ~15 with justification. Beyond that, consider compound event metadata instead of new top-level types.
