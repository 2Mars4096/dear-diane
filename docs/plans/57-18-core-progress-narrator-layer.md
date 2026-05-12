# 57-18: Core Progress Narrator Layer

**Parent:** [57-chat-agent-v2-control-plane](57-chat-agent-v2-control-plane.md)
**Status:** completed
**Goal:** Add a reusable core narrator lane that turns run snapshots and event streams into non-blocking read-only progress answers without affecting executor behavior.

## Tasks
- [x] 1. Replace the TUI-only four-lane framing with a three-lane core intent contract
  - [x] 1-1. Define `narrator read-only` as conversation/status/explanation over already-known run, transcript, and event state only.
  - [x] 1-2. Define `executor read-only` as inspection/synthesis work that may use read-only workspace tools but must not mutate files or tracking docs.
  - [x] 1-3. Define `executor write` as task execution that may plan, mutate, validate, repair, and update tracking docs.
  - [x] 1-4. Preserve `simple` versus `complex` as an execution-effort hint inside the executor lanes, not as the top-level permission boundary.
  - [x] 1-5. Route uncertainty to one short clarification instead of silently promoting narrator requests into executor work.
- [x] 2. Define the narrator snapshot contract
  - [x] 2-1. Add a stable `RunNarratorSnapshot` shape with run id, task id, objective, workspace, status, phase, elapsed time, current step, recent events, queued work, changed files, artifacts, validation state, blockers, and trace refs.
  - [x] 2-2. Build snapshots from existing event logs, run state, transcript state, and queue state without reading arbitrary workspace files.
  - [x] 2-3. Include snapshot version, source timestamps, and stale/newer-than-response metadata so surfaces can explain when an answer may lag the run.
  - [x] 2-4. Keep raw provider/tool telemetry out of the default snapshot while preserving trace refs for debug drill-down.
- [x] 3. Define the narrator request/response API
  - [x] 3-1. Add `NarratorRequest` fields for user question, snapshot, requested style, surface, verbosity, and response budget.
  - [x] 3-2. Add `NarratorResponse` fields for status, answer text, cited snapshot refs, confidence/limits, stale flag, and failure fallback.
  - [x] 3-3. Prompt the narrator to answer only from the snapshot, admit unknowns, avoid claiming hidden actions, and never request mutation tools.
  - [x] 3-4. Provide a deterministic fallback answer when no model provider is configured or the narrator call fails.
- [x] 4. Make narration non-blocking and independent of execution
  - [x] 4-1. Run narrator calls as cancellable background work that cannot hold executor locks or delay tool execution.
  - [x] 4-2. Debounce repeated progress questions and cancel or mark stale narrator jobs when a newer snapshot supersedes them.
  - [x] 4-3. Emit core narrator events such as `narrator.requested`, `narrator.started`, `narrator.delta`, `narrator.completed`, `narrator.failed`, and `narrator.stale`.
  - [x] 4-4. Keep executor events flowing while narrator work is in flight.
- [x] 5. Enforce capability boundaries
  - [x] 5-1. Give narrator requests no `file_read`, `list_directory`, `workspace_check`, shell, write, edit, plan, changelog, or todo-update tools.
  - [x] 5-2. Give executor read-only requests only bounded read/search/status tools.
  - [x] 5-3. Give executor write requests the existing mutation, validation, and tracking-doc capabilities.
  - [x] 5-4. Record the chosen lane and rationale in durable metadata for later debugging.
- [x] 6. Add regression coverage
  - [x] 6-1. `current progress` and `what is happening` route to `narrator read-only`, not executor read-only or executor write.
  - [x] 6-2. Narrator requests do not create `.dan-super/runs/**/plans`, write files, update tracking docs, or call workspace tools.
  - [x] 6-3. Narrator responses can complete while an executor run continues producing progress events.
  - [x] 6-4. Stale narrator responses are marked or discarded when newer run state exists.
  - [x] 6-5. Provider failure returns a deterministic snapshot-based fallback instead of a silent gap.
- [x] 7. Document the contract
  - [x] 7-1. Update architecture docs with the narrator/executor lane split when implementation lands.
  - [x] 7-2. Update user-facing docs to explain that progress questions are answered by a read-only narrator over current state.

## Decisions
- `narrator read-only` is a separate lane, not just a small `executor read-only` request.
- The narrator observes and explains state. It does not gather new evidence, steer execution, or decide whether work should continue.
- The executor remains the only owner of planning, workspace inspection beyond the snapshot, mutation, validation, repair, and final delivery.
- The narrator API belongs below TUI/GUI/CLI so every surface gets the same state, safety boundary, and fallback behavior.
- The first implementation should prefer snapshot-only narration. Optional read-only evidence expansion can be considered later, but it would belong to `executor read-only`, not the narrator lane.

## Notes
- This plan supersedes the top-level `{read-only, write} x {simple, complex}` intent framing from [57-16](57-16-super-dan-tui-four-lane-intent-gate.md). That framing remains useful inside executor routing, but progress/status questions need their own narrator lane.
- The target user experience is: immediate deterministic progress line, visible elapsed time, then a short model-written answer when available, without blocking or changing the active run.
- The core layer should expose the minimum text/state needed for progressive feedback; surfaces decide how to render it.
- 2026-05-12: First implementation landed in `src/dan/agent_runtime/progress_narrator.py`: three-lane intent classification, snapshot/request/response contracts, deterministic snapshot fallback, snapshot-only provider narration with no tool argument, and core tests. True background/cancellable narrator jobs remain open.
- 2026-05-12: Core narrator jobs now run through `start_narrator_job(...)` / `run_narrator_job(...)`, emit requested/started/completed/failed/stale events, cancel older jobs for repeated progress questions, and mark responses stale when a newer snapshot exists before completion.
