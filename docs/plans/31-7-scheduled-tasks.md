# 31-7: Scheduled Tasks

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** completed
**Goal:** Enable cron-style and interval-based task scheduling so DAN can run workflows, checks, and reports on a recurring basis — "run my equity report every morning at 9am."

## Problem

DAN runs as a daemon (26-2) and can process messages from any surface. But every task requires a human to initiate it. There's no way to say "do X every day" or "check Y every hour." Scheduled tasks are table-stakes for an always-on assistant.

## Tasks

- [x] 1. **Schedule model and store**
  - [x] 1-1. `ScheduleEntry` Pydantic model: `id: str`, `name: str`, `trigger: str` (cron expression or interval like `every 6h`), `action: str` (natural language intent or workflow ID), `enabled: bool = True`, `last_run: datetime | None`, `next_run: datetime | None`, `created_at: datetime`, `timezone: str = "UTC"` (IANA timezone, e.g. "America/New_York" — v1 is UTC-only for cron evaluation; human-readable shortcuts like "daily at 9am" interpret in this timezone), `trigger_context: TriggerContext`, `delivery_target: DeliveryTarget`
  - [x] 1-2. `TriggerContext` and `DeliveryTarget` are fields on `ScheduleEntry` (not standalone objects). Split routing metadata cleanly:
    - `trigger_context`: `source_surface: Literal["schedule"]`, `project_id: str | None`, `task_id: str | None`, `user_id: str | None`, `thread_key: str | None`
    - `delivery_target`: `surface: str`, `conversation_key: str | None`, `user_id: str | None`, `project_id: str | None`, `thread_key: str | None`, `fallback_policy: Literal["store_and_notify", "private_surface", "drop"] = "store_and_notify"`
  - [x] 1-3. `ScheduleStore` — filesystem-backed CRUD at `~/.dan/schedules.json`; load on startup, atomic save
  - [x] 1-4. Cron expression parser: support standard 5-field cron (`*/5 * * * *`) plus human-readable shortcuts (`every 6h`, `daily at 9am`, `weekdays at 8:30am`)

- [x] 2. **Scheduler runtime** (core done; lease/lock and daemon wiring deferred)
  - [x] 2-1. `TaskScheduler` — asyncio background task that checks `next_run` every 30 seconds; on trigger, dispatches action as a concierge message (reuses entire existing pipeline)
  - [x] 2-2. Define **single scheduler authority**: `dan-service` owns schedule firing when present; `dan-serve` only runs the scheduler in single-process setups or when it successfully acquires the schedule lease
  - [x] 2-3. Lease/lock rule: persisted schedule-owner lease prevents duplicate firing when both server and daemon are running; add takeover on stale lease for failover
  - [x] 2-4. Wire into `dan-service` daemon (primary owner; schedules survive restarts via persisted store)
  - [x] 2-5. Wire fallback mode into `dan-serve` lifespan (only when service absent / lease acquired)
  - [x] 2-6. Missed-run detection: if owner was down when a trigger fired, optionally run on next startup (configurable per schedule via `on_missed: Literal["run_once", "skip"] = "run_once"`). If multiple runs were missed, execute **once** with a `missed_since: datetime` context field — do not replay all missed invocations (side-effectful tasks like reports should not generate duplicates).
  - [x] 2-7. Concurrent schedule execution: each triggered task runs in its own concierge dispatch (fire-and-forget via `asyncio.create_task`)

- [x] 3. **Chat commands**
  - [x] 3-1. `/schedule add "run equity report" every day at 9am` — creates schedule, confirms
  - [x] 3-2. `/schedule list` — show all schedules with next run time, last run status
  - [x] 3-3. `/schedule remove <id|name>` — delete a schedule
  - [x] 3-4. `/schedule pause <id|name>` / `/schedule resume <id|name>` — toggle enabled
  - [x] 3-5. Natural language: "remind me to check the portfolio every Monday" → auto-creates schedule

- [x] 4. **Result delivery** (history done; surface routing deferred)
  - [x] 4-1. Route results to the `delivery_target` in `ScheduleEntry` (Telegram, WhatsApp, CLI notification, editor thread)
  - [x] 4-2. Fallback: if target surface is unreachable, apply `fallback_policy`; default is store result and notify via `NotificationManager`
  - [x] 4-3. Schedule run history: persist last N results per schedule for review (`/schedule history <name>`)

- [x] 5. **Tests and docs**
  - [x] 5-1. Unit tests: cron parsing, next-run computation, store CRUD, missed-run detection, delivery target parsing
  - [x] 5-2. Integration test: mock clock, verify trigger fires once and dispatches to concierge with `source_surface="schedule"`
  - [x] 5-3. Integration test: when both `dan-service` and `dan-serve` are present, lease/lock guarantees a schedule fires exactly once
  - [x] 5-4. Update CLI docs, architecture, changelog

## Dependencies

- `dan-service` daemon (26-2) for always-on execution
- `ConcurrentDispatcher` (27-2) for dispatch
- `NotificationManager` (26-4) for result delivery
- `Concierge` for processing scheduled actions as if user-sent

## Estimate

1.5 days

## Notes

- The scheduler is intentionally simple — it's a glorified cron that fires concierge messages. The entire intelligence layer (project scoping, memory, tool selection) is reused from the existing pipeline.
- Cron parsing: use `croniter` (pip-installable, lightweight) for standard 5-field cron. Add a custom parser for human-readable shortcuts (`every 6h`, `daily at 9am`, `weekdays at 8:30am`) that translates to cron expressions. This avoids reinventing cron semantics.
- Scheduled tasks enter concierge as `source_surface="schedule"` (via `TriggerContext`) so the LLM knows it's autonomous (no human to ask questions to). That is distinct from `delivery_target`, which decides where results are sent back.
- **Registry note (31-16):** Register `/schedule` with subcommands `add|list|remove|pause|resume|history` via `CommandDescriptor` in the command registry if 31-16 has landed. Otherwise, add to `_FAST_COMMAND_PREFIXES` with a TODO for registry migration.
- Post-review cleanup hardened project-only scheduled dispatches end-to-end: resolver now falls back to any-surface project/task lookup by `project_id`, and project-store fetches also fall back across surfaces for known project ids, so a scheduled run neither creates a fresh project nor fails to persist turns just because its synthetic dispatch `external_id` differs from the original chat surface. Dedicated regressions now cover both the project-only path and the explicit `project_id + task_id` cross-surface path.
- Post-review delivery cleanup now standardizes `schedule_result_ready` payloads for bus consumers (`surface_id`, `timestamp`, and a `data` block with status/result/fallback) while keeping legacy fields, and notification routing now treats scheduled results as first-class notification events.
