# 58-1: Foreground Admission Controller

**Parent:** [58-async-core-and-background-runtime](58-async-core-and-background-runtime.md)
**Status:** completed
**Goal:** Add the narrow foreground controller needed for the first proof: every new user turn is accepted while executors are busy, reads a compact task-board snapshot, and returns one immediate admission result without waiting for active background runs.

## Tasks
- [x] 1. Define the MVP admission input contract
  - [x] 1-1. Include user text, surface metadata, workspace, thread/task ids, attachments, selected skills, and visible conversation context.
  - [x] 1-2. Include the compact active board summary: running runs, queued runs, owned paths, claimed artifacts, latest phase, and latest narrator/status summary.
  - [x] 1-3. Include command hints for explicit `/status`, `/append`, `/pause`, `/cancel`, and `/new` style turns.
  - [x] 1-4. Exclude full raw executor transcripts from the default admission prompt/context.
- [x] 2. Define the MVP admission output contract
  - [x] 2-1. `chat_or_status`: answer without starting executor work.
  - [x] 2-2. `append_to_active`: attach instructions to a running run at the next safe checkpoint.
  - [x] 2-3. `queue_after`: create a queued run with explicit prerequisite/conflict reason.
  - [x] 2-4. `start_parallel`: create and execute an independent background run.
  - [x] 2-5. `ask_clarification`: ask one concrete question when dependency/conflict state is unclear.
  - [x] 2-6. `reject_or_defer`: refuse unsafe or impossible work with a durable reason.
- [x] 3. Make the controller cheap and responsive
  - [x] 3-1. Prefer deterministic command parsing for explicit control commands.
  - [x] 3-2. Use deterministic path/resource evidence; model-assisted scheduling is outside Plan 58.
  - [x] 3-3. Defer model-only decisions until deterministic MVP evidence is assembled.
  - [x] 3-4. Never wait for the active executor to complete before deciding the new turn.
- [x] 4. Integrate with V2 ingress and Super TUI
  - [x] 4-1. Route V2 Chat/Agent incoming turns through admission before backend execution.
  - [x] 4-2. Let Super TUI show the same admission decision shape even before server-backed execution is fully default.
  - [x] 4-3. Preserve plain Chat behavior for ordinary questions.
- [x] 5. Add regressions
  - [x] 5-1. A second build request during a running build is classified immediately.
  - [x] 5-2. A status question during a running build returns board/narrator state without executor tools.
  - [x] 5-3. An explicit append command becomes a checkpoint-append item, not a new run.
  - [x] 5-4. A new independent task becomes a parallel run request, not an append.
  - [x] 5-5. An unclear second task asks one clarification rather than blocking on the first run.

## Decisions
- The foreground controller has control authority but no workspace mutation tools.
- The controller may use current board summaries and sanitized run snapshots, not raw executor transcripts by default.
- Ambiguous turns should ask one question instead of silently appending to the wrong run.
- Do not make model routing a prerequisite for the first proof; deterministic command/path handling must work alone.

## Notes
- This is the operational counterpart to the TUI model-router/narrator split: it keeps the user-facing loop alive while executors are busy.
- The first product demo should be boring and crisp: A is running, B is submitted, the controller immediately says "started in parallel" or "queued behind A because both target `script.py`."
- Implemented in `src/dan/server/chat_v2_async_core.py`; V2 routes use it through `POST /api/v2/agent-runs` and `POST /api/v2/agent-runs/admit`.
