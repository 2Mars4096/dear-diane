# 58-2: Durable Task Board and Run Ledger

**Parent:** [58-async-core-and-background-runtime](58-async-core-and-background-runtime.md)
**Status:** not-started
**Goal:** Create the minimum durable board needed for nonblocking admission: active/queued/completed task and run records with owned paths, admission reasons, event refs, and terminal result refs, so foreground control can decide the next user turn without inspecting private executor state.

## Tasks
- [ ] 1. Define MVP board records
  - [ ] 1-1. `TaskBoard`: workspace/thread scope, active task ids, queued task ids, branch lineage, and capacity policy.
  - [ ] 1-2. `BoardTask`: objective, status, owner surface, workspace, created/updated timestamps, dependencies, and current run ids.
  - [ ] 1-3. `BoardRun`: backend, status, phase, owned paths, claimed artifacts, event log path, and terminal result refs.
  - [ ] 1-4. `BoardQueueItem`: append/new/control command, admission decision, dependency/conflict reason, and operator-context packet.
- [ ] 2. Normalize MVP state transitions
  - [ ] 2-1. queued -> admitted -> running -> validating -> completed/failed/blocked/stopped.
  - [ ] 2-2. queued -> waiting_dependency when prerequisites are unresolved.
  - [ ] 2-3. running -> stop_requested only becomes stopped at safe checkpoints.
  - [ ] 2-4. Defer paused/resume/retry branch richness until the nonblocking proof is green.
- [ ] 3. Project existing state into the board without broad rewrites
  - [ ] 3-1. Reuse `ChatV2Store` task/run records where possible.
  - [ ] 3-2. Project only the Super DAN event fields needed for phase/status/event-log/result refs.
  - [ ] 3-3. Keep `.dan-super/state/` hook/inbox/worktree records as implementation details for the first slice.
- [ ] 4. Add query APIs/helpers
  - [ ] 4-1. Read compact board snapshot for controller prompts.
  - [ ] 4-2. Read compact board snapshot for TUI/CLI status.
  - [ ] 4-3. List active owned paths and resource locks.
  - [ ] 4-4. Resolve latest narrator/status summary per run.
- [ ] 5. Add regressions
  - [ ] 5-1. Board snapshots survive process restart.
  - [ ] 5-2. A queued dependent task keeps dependency ids and reason.
  - [ ] 5-3. Two active independent tasks appear in one board snapshot.
  - [ ] 5-4. Terminal result refs link back to the initiating user turn.

## Decisions
- The task board is the durable public state boundary for foreground control and surfaces.
- Executor-private logs remain full-fidelity, but the board stores compact, typed state.
- Board schema should be backend-neutral: Super DAN is the first backend, not the schema owner.
- The first board should be small enough to implement quickly; branch lineage, rich pause/resume, retries, priorities, and fairness can extend it later.

## Notes
- This should consolidate the V2 Agent state, Super DAN event-log projections, and hook/inbox snapshots into one operator-facing state shape.
