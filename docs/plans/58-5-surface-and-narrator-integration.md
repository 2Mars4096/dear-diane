# 58-5: Surface and Narrator Integration

**Parent:** [58-async-core-and-background-runtime](58-async-core-and-background-runtime.md)
**Status:** completed
**Goal:** Make the first async proof visible: surfaces immediately acknowledge whether the new turn started in parallel, queued behind an active run, appended to a run, or needs clarification, while narrator/status output explains board/run progress without owning execution.

## Tasks
- [x] 1. Define MVP user-visible states
  - [x] 1-1. Accepted and running in background.
  - [x] 1-2. Queued behind dependency.
  - [x] 1-3. Running in parallel.
  - [x] 1-4. Appended to active run.
  - [x] 1-5. Waiting for clarification.
  - [x] 1-6. Stopping, cancelled, failed, blocked, completed.
- [x] 2. Update narrator snapshots
  - [x] 2-1. Include task board summary: active runs, queued work, dependencies, blockers, and latest terminal results.
  - [x] 2-2. Distinguish "foreground accepted your new task" from "background executor is still building".
  - [x] 2-3. Narrate parallel work as separate run lanes, not one blended progress stream.
  - [x] 2-4. Keep narrator tool-free and mutation-free.
- [x] 3. Add first surface behavior
  - [x] 3-1. TUI accepts a new prompt while a background executor is active, with details tracked in [58-6-tui-background-board.md](58-6-tui-background-board.md).
  - [x] 3-2. V2/GUI surfaces display the admission result and board entry instead of blocking on execution.
  - [x] 3-3. Telegram can report accepted/queued/running/completed states compactly.
  - [x] 3-4. CLI non-interactive commands can opt into background mode and print a run id/event-log path.
- [x] 4. Add minimum board commands
  - [x] 4-1. `/tasks` lists active and queued board items.
  - [x] 4-2. `/status <task|run>` reports compact progress.
  - [x] 4-3. `/new` forces a separate task when admission would append.
  - [x] 4-4. `/append` forces active-run steering when safe.
  - [x] 4-5. Defer `/focus` and richer board navigation until the proof works.
- [x] 5. Add regressions
  - [x] 5-1. TUI can submit a second task while the first executor is running.
  - [x] 5-2. Narrator reports one line per active run without leaking raw logs.
  - [x] 5-3. Telegram receives a compact queued/dependency message for dependent work.
  - [x] 5-4. Final answers link to the correct task/run, not the latest foreground turn.
  - [x] 5-5. A parallel-start admission acknowledgement appears before the first task finishes.

## Decisions
- Surfaces show the task board by default and raw executor logs only behind debug/detail views.
- The narrator remains response/progress projection. It may explain the scheduler's decision but cannot override it.
- Default UI copy should make background execution explicit so users understand why a second task may run in parallel or queue.
- The first UX should optimize for clarity over richness: "Started task B in parallel with task A" or "Queued task B behind task A because both target `script.py`."

## Notes
- This plan completes the analogy: narrator/executor is the communication split; foreground core/background executor is the operational split; surfaces must make both visible without mixing authorities.
- TUI-specific layout, commands, and regressions live in `58-6`; this plan keeps the cross-surface contract aligned.
- V2 admission responses now include both the admission decision and board snapshot; direct TUI projection already consumes the same shape and server-backed TUI control remains the follow-up tracked in `58-6`.
