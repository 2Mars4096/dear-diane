# 31-7: Scheduled Tasks

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** not-started
**Goal:** Enable cron-style and interval-based task scheduling so DAN can run workflows, checks, and reports on a recurring basis — "run my equity report every morning at 9am."

## Problem

DAN runs as a daemon (26-2) and can process messages from any surface. But every task requires a human to initiate it. There's no way to say "do X every day" or "check Y every hour." Scheduled tasks are table-stakes for an always-on assistant.

## Tasks

- [ ] 1. **Schedule model and store**
  - [ ] 1-1. `ScheduleEntry` Pydantic model: `id: str`, `name: str`, `trigger: str` (cron expression or interval like `every 6h`), `action: str` (natural language intent or workflow ID), `enabled: bool = True`, `last_run: datetime | None`, `next_run: datetime | None`, `created_at: datetime`, `surface: str` (which surface receives results)
  - [ ] 1-2. `ScheduleStore` — filesystem-backed CRUD at `~/.dan/schedules.json`; load on startup, atomic save
  - [ ] 1-3. Cron expression parser: support standard 5-field cron (`*/5 * * * *`) plus human-readable shortcuts (`every 6h`, `daily at 9am`, `weekdays at 8:30am`)

- [ ] 2. **Scheduler runtime**
  - [ ] 2-1. `TaskScheduler` — asyncio background task that checks `next_run` every 30 seconds; on trigger, dispatches action as a concierge message (reuses entire existing pipeline)
  - [ ] 2-2. Wire into `dan-serve` lifespan (start on server boot, stop on shutdown)
  - [ ] 2-3. Wire into `dan-service` daemon (schedules survive restarts via persisted store)
  - [ ] 2-4. Missed-run detection: if server was down when a trigger fired, optionally run on next startup (configurable per schedule)
  - [ ] 2-5. Concurrent schedule execution: each triggered task runs in its own concierge dispatch (uses existing `ConcurrentDispatcher`)

- [ ] 3. **Chat commands**
  - [ ] 3-1. `/schedule add "run equity report" every day at 9am` — creates schedule, confirms
  - [ ] 3-2. `/schedule list` — show all schedules with next run time, last run status
  - [ ] 3-3. `/schedule remove <id|name>` — delete a schedule
  - [ ] 3-4. `/schedule pause <id|name>` / `/schedule resume <id|name>` — toggle enabled
  - [ ] 3-5. Natural language: "remind me to check the portfolio every Monday" → auto-creates schedule

- [ ] 4. **Result delivery**
  - [ ] 4-1. Route results to the surface specified in `ScheduleEntry.surface` (Telegram, WhatsApp, CLI notification)
  - [ ] 4-2. Fallback: if target surface is unreachable, store result and notify via `NotificationManager`
  - [ ] 4-3. Schedule run history: persist last N results per schedule for review (`/schedule history <name>`)

- [ ] 5. **Tests and docs**
  - [ ] 5-1. Unit tests: cron parsing, next-run computation, store CRUD, missed-run detection
  - [ ] 5-2. Integration test: mock clock, verify trigger fires and dispatches to concierge
  - [ ] 5-3. Update CLI docs, architecture, changelog

## Dependencies

- `dan-service` daemon (26-2) for always-on execution
- `ConcurrentDispatcher` (27-2) for dispatch
- `NotificationManager` (26-4) for result delivery
- `Concierge` for processing scheduled actions as if user-sent

## Estimate

1.5 days

## Notes

- The scheduler is intentionally simple — it's a glorified cron that fires concierge messages. The entire intelligence layer (project scoping, memory, tool selection) is reused from the existing pipeline.
- No external dependency for cron parsing — implement a minimal parser or use `croniter` (pip-installable, lightweight).
- Scheduled tasks are identified as `surface: "schedule"` in the concierge, which adds `[Scheduled]` context to the system prompt so the LLM knows it's autonomous (no human to ask questions to).
