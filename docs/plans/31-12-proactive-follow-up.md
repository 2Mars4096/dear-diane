# 31-12: Proactive Follow-Up

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** not-started
**Goal:** Enable DAN to proactively initiate follow-up messages based on completed work, discovered opportunities, or time-based triggers — moving from purely reactive to anticipatory assistance.

## Problem

DAN is currently reactive — it only acts when a message arrives. An always-on assistant should proactively follow up when: a long-running task completes and has interesting findings, a scheduled check discovers something noteworthy, experience memory surfaces a relevant past result, or a user's task has been paused/blocked and could be unblocked.

## Tasks

- [ ] 1. **Follow-up trigger system**
  - [ ] 1-1. `FollowUpTrigger` model: `id`, `source` (run_completion / schedule / memory / blocker_resolved), `priority: Literal["low", "medium", "high"]`, `message: str`, `context: dict`, `target_surface: str`, `created_at: datetime`, `expires_at: datetime | None`
  - [ ] 1-2. `FollowUpQueue` — priority queue of pending follow-ups, persisted to `~/.dan/follow_ups.json`
  - [ ] 1-3. Deduplication: don't send the same follow-up twice (hash-based tracking)

- [ ] 2. **Trigger sources**
  - [ ] 2-1. **Run completion insights:** after a workflow/task completes, if the result contains notable findings (anomalies, errors, unexpected patterns), queue a follow-up: "Your equity report finished — found an unusual pattern in sector rotation. Want me to dig deeper?"
  - [ ] 2-2. **Scheduled task results:** scheduled tasks (31-7) that produce actionable results trigger follow-ups instead of raw result dumps
  - [ ] 2-3. **Stale task nudge:** if a task has been paused/blocked for > N hours (configurable, default 24h), nudge: "Your literature review has been paused for 2 days. The blocker was X — I think I can work around it now."
  - [ ] 2-4. **Memory-triggered:** when new information arrives that's relevant to a past task (e.g., a paper cited in a review was retracted), surface it proactively

- [ ] 3. **Delivery**
  - [ ] 3-1. Respect quiet hours: `DAN_QUIET_HOURS` env var (e.g., `"22:00-08:00"`) — queue follow-ups during quiet hours, deliver at first opportunity after
  - [ ] 3-2. Rate limiting: max N follow-ups per hour (configurable, default 3) to avoid notification fatigue
  - [ ] 3-3. Surface routing: deliver to the user's most recently active surface, or to the surface specified in the trigger
  - [ ] 3-4. Conversational delivery: follow-ups are sent as normal chat messages, so the user can respond naturally

- [ ] 4. **User controls**
  - [ ] 4-1. `/follow-ups` command: list pending follow-ups
  - [ ] 4-2. `/follow-ups off` / `/follow-ups on` — global toggle
  - [ ] 4-3. `DAN_PROACTIVE_FOLLOW_UP=1` env var (default on when daemon mode is active)
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

## Notes

- Proactive != intrusive. The rate limiter and quiet hours ensure DAN doesn't become annoying. The default posture is helpful nudges, not constant pinging.
- Follow-ups are conversational messages, not notifications. The user can respond "yes, dig deeper" and DAN continues naturally. This distinguishes it from the existing `NotificationManager` which sends fire-and-forget alerts.
- The "memory-triggered" source (2-4) is the most ambitious — it requires background scanning of new information against past task contexts. Start with the simpler sources (run completion, stale task) and add memory-triggered later.
