# 58-5: Surface and Narrator Integration

**Parent:** [58-async-core-and-background-runtime](58-async-core-and-background-runtime.md)
**Status:** not-started
**Goal:** Make the first async proof visible: surfaces immediately acknowledge whether the new turn started in parallel, queued behind an active run, appended to a run, or needs clarification, while narrator/status output explains board/run progress without owning execution.

## Tasks
- [ ] 1. Define MVP user-visible states
  - [ ] 1-1. Accepted and running in background.
  - [ ] 1-2. Queued behind dependency.
  - [ ] 1-3. Running in parallel.
  - [ ] 1-4. Appended to active run.
  - [ ] 1-5. Waiting for clarification.
  - [ ] 1-6. Stopping, cancelled, failed, blocked, completed.
- [ ] 2. Update narrator snapshots
  - [ ] 2-1. Include task board summary: active runs, queued work, dependencies, blockers, and latest terminal results.
  - [ ] 2-2. Distinguish "foreground accepted your new task" from "background executor is still building".
  - [ ] 2-3. Narrate parallel work as separate run lanes, not one blended progress stream.
  - [ ] 2-4. Keep narrator tool-free and mutation-free.
- [ ] 3. Add first surface behavior
  - [ ] 3-1. TUI accepts a new prompt while a background executor is active.
  - [ ] 3-2. V2/GUI surfaces display the admission result and board entry instead of blocking on execution.
  - [ ] 3-3. Telegram can report accepted/queued/running/completed states compactly.
  - [ ] 3-4. CLI non-interactive commands can opt into background mode and print a run id/event-log path.
- [ ] 4. Add minimum board commands
  - [ ] 4-1. `/tasks` lists active and queued board items.
  - [ ] 4-2. `/status <task|run>` reports compact progress.
  - [ ] 4-3. `/new` forces a separate task when admission would append.
  - [ ] 4-4. `/append` forces active-run steering when safe.
  - [ ] 4-5. Defer `/focus` and richer board navigation until the proof works.
- [ ] 5. Add regressions
  - [ ] 5-1. TUI can submit a second task while the first executor is running.
  - [ ] 5-2. Narrator reports one line per active run without leaking raw logs.
  - [ ] 5-3. Telegram receives a compact queued/dependency message for dependent work.
  - [ ] 5-4. Final answers link to the correct task/run, not the latest foreground turn.
  - [ ] 5-5. A parallel-start admission acknowledgement appears before the first task finishes.

## Decisions
- Surfaces show the task board by default and raw executor logs only behind debug/detail views.
- The narrator remains response/progress projection. It may explain the scheduler's decision but cannot override it.
- Default UI copy should make background execution explicit so users understand why a second task may run in parallel or queue.
- The first UX should optimize for clarity over richness: "Started task B in parallel with task A" or "Queued task B behind task A because both target `script.py`."

## Notes
- This plan completes the analogy: narrator/executor is the communication split; foreground core/background executor is the operational split; surfaces must make both visible without mixing authorities.
