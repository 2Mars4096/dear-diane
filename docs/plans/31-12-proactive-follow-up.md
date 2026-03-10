# 31-12: Proactive Follow-Up

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** not-started
**Goal:** Enable DAN to proactively initiate follow-up messages based on completed work, discovered opportunities, or time-based triggers — moving from purely reactive to anticipatory assistance.

## Problem

DAN is currently reactive — it only acts when a message arrives. An always-on assistant should proactively follow up when: a long-running task completes and has interesting findings, a scheduled check discovers something noteworthy, experience memory surfaces a relevant past result, or a user's task has been paused/blocked and could be unblocked.

## Tasks

- [ ] 1. **Follow-up trigger system**
  - [ ] 1-1. `FollowUpTrigger` model: `id`, `source` (run_completion / schedule / memory / blocker_resolved), `priority: Literal["low", "medium", "high"]`, `message: str`, `context: dict`, `user_id: str | None`, `project_id: str | None`, `task_id: str | None`, `conversation_key: str | None`, `thread_key: str | None`, `target_surface: str | None`, `reply_correlation_id: str | None`, `created_at: datetime`, `expires_at: datetime | None`
  - [ ] 1-2. `FollowUpQueue` — scoped priority queue of pending follow-ups, persisted in a per-user / per-project store (not a single flat global JSON file)
  - [ ] 1-3. Deduplication: don't send the same follow-up twice (hash-based tracking)

- [ ] 2. **Trigger sources**
  - [ ] 2-1. **Run completion insights:** after a workflow/task completes, if the result contains notable findings (anomalies, errors, unexpected patterns), queue a follow-up: "Your equity report finished — found an unusual pattern in sector rotation. Want me to dig deeper?"
  - [ ] 2-2. **Scheduled task results:** scheduled tasks (31-7) that produce actionable results trigger follow-ups instead of raw result dumps
  - [ ] 2-3. **Stale task nudge:** if a task has been paused/blocked for > N hours (configurable, default 24h), nudge: "Your literature review has been paused for 2 days. The blocker was X — I think I can work around it now." This depends on structured task state from 31-11.
  - [ ] 2-4. **Memory-triggered:** when new information arrives that's relevant to a past task (e.g., a paper cited in a review was retracted), surface it proactively

- [ ] 3. **Delivery**
  - [ ] 3-1. Respect quiet hours: `DAN_QUIET_HOURS` env var (e.g., `"22:00-08:00"`) — queue follow-ups during quiet hours, deliver at first opportunity after
  - [ ] 3-2. Rate limiting: max N follow-ups per hour (configurable, default 3) to avoid notification fatigue
  - [ ] 3-3. Surface routing: deliver to the user's most recently active surface, or to the surface specified in the trigger
  - [ ] 3-4. Conversational delivery: follow-ups are sent through the normal chat / adapter pipeline so the user can respond naturally; `NotificationManager` is fallback-only when the preferred surface is unavailable

- [ ] 4. **User controls**
  - [ ] 4-1. `/follow-ups` command: list pending follow-ups
  - [ ] 4-2. `/follow-ups off` / `/follow-ups on` — global toggle
  - [ ] 4-3. `DAN_PROACTIVE_FOLLOW_UP=0` env var (default off; explicit opt-in because outbound DAN-initiated messaging changes product behavior)
  - [ ] 4-4. Per-task opt-out: "don't follow up on this task"

- [ ] 5. **Tests and docs**
  - [ ] 5-1. Unit tests: trigger creation, queue management, deduplication, quiet hours, rate limiting
  - [ ] 5-2. Integration test: run completion triggers follow-up delivery
  - [ ] 5-3. Update architecture, changelog

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
