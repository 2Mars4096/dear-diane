# 31-12: Proactive Follow-Up

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** in-progress
**Goal:** Enable DAN to proactively initiate follow-up messages based on completed work, discovered opportunities, or time-based triggers — moving from purely reactive to anticipatory assistance.

## Problem

DAN is currently reactive — it only acts when a message arrives. An always-on assistant should proactively follow up when: a long-running task completes and has interesting findings, a scheduled check discovers something noteworthy, experience memory surfaces a relevant past result, or a user's task has been paused/blocked and could be unblocked.

## Tasks

- [x] 1. **Follow-up trigger system**
  - [x] 1-1. `FollowUpTrigger` model: `id`, `source` (run_completion / schedule / memory / blocker_resolved / stale_task), `priority: Literal["low", "medium", "high"]`, `message: str`, `context: dict`, `user_id: str | None`, `project_id: str | None`, `task_id: str | None`, `conversation_key: str | None`, `thread_key: str | None`, `target_surface: str | None`, `reply_correlation_id: str | None`, `created_at: datetime`, `expires_at: datetime | None`, `delivered: bool`, `delivered_at: datetime | None`
  - [x] 1-2. `FollowUpQueue` — in-memory priority queue with deduplication and expiry filtering
  - [x] 1-3. Deduplication: hash-based tracking (source + project_id + message prefix)

- [x] 2. **Trigger sources**
  - [x] 2-1. `create_run_completion_trigger()` — run completion follow-up factory
  - [x] 2-2. `create_schedule_result_trigger()` — scheduled task result follow-up factory
  - [x] 2-3. `create_stale_task_trigger()` — stale task nudge follow-up factory (uses `TaskSnapshot` from 31-11)
  - [ ] 2-4. **Memory-triggered:** when new information arrives that's relevant to a past task (e.g., a paper cited in a review was retracted), surface it proactively (deferred)

- [x] 3. **Delivery**
  - [x] 3-1. Quiet hours: `DAN_QUIET_HOURS` env var (e.g., `"22:00-08:00"`) — `is_quiet_hours()` with overnight wrap support
  - [x] 3-2. Rate limiting: `max_per_hour` (configurable, default 3) with sliding-window timestamp tracking
  - [x] 3-3. `FollowUpDeliveryEngine` with async background loop (60s interval), `deliver_pending()`, `start()`/`stop()`
  - [ ] 3-4. Surface routing: deliver to most recently active surface (deferred to 31-13 integration)

- [x] 4. **User controls**
  - [x] 4-1. `/follow-ups` command: list pending follow-ups
  - [x] 4-2. `/follow-ups off` / `/follow-ups on` — global toggle
  - [x] 4-3. `DAN_PROACTIVE_FOLLOW_UP=0` env var (default off; explicit opt-in)
  - [x] 4-4. `FollowUpConfig` with `enabled`, `quiet_hours`, `max_per_hour`, `stale_task_hours`; `load_follow_up_config()` reads all env vars
  - [ ] 4-5. Per-task opt-out: "don't follow up on this task" (deferred)

- [x] 5. **Tests and docs** (core module)
  - [x] 5-1. 44 unit tests: trigger creation (run/stale/schedule, edge cases), queue (enqueue/dedup/drain/expiry/priority ordering/list), quiet hours (parse/overnight/same-day/boundary/unconfigured), rate limiting (under/at/prune), delivery (enabled/disabled/quiet-hours/rate-limit/error-handling/config-property), stale task scanning (finds stale/no stale/ignores completed/empty store), command parsing (list/on/off/pending/bare), deduplication edge cases (different source/different project/exact duplicate), config loading (default off/enabled/false strings/custom values)
  - [ ] 5-2. Integration test: run completion triggers follow-up delivery (deferred to runtime wiring)
  - [x] 5-3. Update architecture, changelog

## Dependencies

- `NotificationManager` (26-4) for delivery
- `TaskScheduler` (31-7) for scheduled triggers
- `ProjectStore` for task status monitoring
- `ExperienceStore` / `MemoryKernel` for memory-triggered follow-ups
- `ConcurrentDispatcher` for surface routing

## Estimate

1.5-2 days

## Primary Files

- `src/dan/server/concierge/follow_up.py` — `FollowUpTrigger`, `FollowUpQueue`, trigger creation, deduplication, delivery (new)
- `src/dan/server/concierge/runtime.py` — run-completion trigger wiring (modify)
- `src/dan/server/capability_handlers.py` — `/follow-ups` command handler (modify)
- `src/dan/server/app.py` — follow-up background task in lifespan (modify)

## Notes

- Proactive != intrusive. The rate limiter and quiet hours ensure DAN doesn't become annoying. The default posture is helpful nudges, not constant pinging.
- Follow-ups are conversational messages, not notifications. The user can respond "yes, dig deeper" and DAN continues naturally. This distinguishes it from the existing `NotificationManager` which is only a fallback delivery path.
- The "memory-triggered" source (2-4) is the most ambitious — it requires background scanning of new information against past task contexts. Start with the simpler sources (run completion, stale task) and add memory-triggered later.
- Trigger sources are phased: `run_completion` and `schedule` can ship first; `stale_task` and `blocker_resolved` should wait until 31-11 provides structured task snapshots.
- **Surface routing (31-13 overlap):** Task 3-3's surface routing is intentionally simple (most-recently-active surface). When 31-13 lands with full presence tracking and `SurfaceRoutingPolicy`, this plan's routing should be upgraded to use 31-13's infrastructure. Design the v1 router as a thin function that can be swapped for 31-13's richer implementation.
- **Feature discoverability:** `DAN_PROACTIVE_FOLLOW_UP=0` (opt-in) means users need to discover the feature. Document in `.env.example` (31-5) and mention in the startup banner when the feature is available but off.
- **Graceful degradation:** If 31-7 (scheduled tasks) hasn't shipped yet, the "schedule" trigger source should be silently disabled (log a debug message), not crash.
- **Registry note (31-16):** Register `/follow-ups` with subcommands `on|off` via `CommandDescriptor` if 31-16 has landed.
