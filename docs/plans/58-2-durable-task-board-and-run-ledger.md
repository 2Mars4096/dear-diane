# 58-2: Durable Task Board and Run Ledger

**Parent:** [58-async-core-and-background-runtime](58-async-core-and-background-runtime.md)
**Status:** completed
**Goal:** Create the minimum durable board needed for nonblocking admission: active/queued/completed task and run records with owned paths, admission reasons, event refs, and terminal result refs, so foreground control can decide the next user turn without inspecting private executor state.

## Tasks
- [x] 1. Define MVP board records
  - [x] 1-1. `TaskBoard`: workspace/thread scope, active task ids, queued task ids, branch lineage, and capacity policy.
  - [x] 1-2. `BoardTask`: objective, status, owner surface, workspace, created/updated timestamps, dependencies, and current run ids.
  - [x] 1-3. `BoardRun`: backend, status, phase, owned paths, claimed artifacts, event log path, and terminal result refs.
  - [x] 1-4. `BoardQueueItem`: append/new/control command, admission decision, dependency/conflict reason, and operator-context packet.
- [x] 2. Normalize MVP state transitions
  - [x] 2-1. queued -> admitted -> running -> validating -> completed/failed/blocked/stopped.
  - [x] 2-2. queued -> waiting_dependency when prerequisites are unresolved.
  - [x] 2-3. running -> stop_requested only becomes stopped at safe checkpoints.
  - [x] 2-4. Defer paused/resume/retry branch richness until the nonblocking proof is green.
- [x] 3. Project existing state into the board without broad rewrites
  - [x] 3-1. Reuse `ChatV2Store` task/run records where possible.
  - [x] 3-2. Project only the Super DAN event fields needed for phase/status/event-log/result refs.
  - [x] 3-3. Keep `.dan-super/state/` hook/inbox/worktree records as implementation details for the first slice.
- [x] 4. Add query APIs/helpers
  - [x] 4-1. Read compact board snapshot for controller prompts.
  - [x] 4-2. Read compact board snapshot for TUI/CLI status.
  - [x] 4-3. List active owned paths and resource locks.
  - [x] 4-4. Resolve latest narrator/status summary per run.
- [x] 5. Add regressions
  - [x] 5-1. Board snapshots survive process restart.
  - [x] 5-2. A queued dependent task keeps dependency ids and reason.
  - [x] 5-3. Two active independent tasks appear in one board snapshot.
  - [x] 5-4. Terminal result refs link back to the initiating user turn.

## Decisions
- The task board is the durable public state boundary for foreground control and surfaces.
- Executor-private logs remain full-fidelity, but the board stores compact, typed state.
- Board schema should be backend-neutral: Super DAN is the first backend, not the schema owner.
- The first board should be small enough to implement quickly; branch lineage, rich pause/resume, retries, priorities, and fairness can extend it later.

## Notes
- This should consolidate the V2 Agent state, Super DAN event-log projections, and hook/inbox snapshots into one operator-facing state shape.
- Implemented as a projection over `ChatV2Store` plus `waiting_dependency` / `background_run_started` ledger events; no separate store was introduced.
