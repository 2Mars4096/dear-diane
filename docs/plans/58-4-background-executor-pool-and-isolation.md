# 58-4: Background Executor Pool and Isolation

**Parent:** [58-async-core-and-background-runtime](58-async-core-and-background-runtime.md)
**Status:** completed
**Goal:** Run the first admitted tasks as durable background executor jobs that return control immediately, emit board-visible events, and permit a second independent job to run concurrently under simple capacity and path-lock rules.

## Tasks
- [x] 1. Define MVP executor slots
  - [x] 1-1. Capacity per workspace/thread and global capacity, defaulting to a small safe parallelism.
  - [x] 1-2. Per-run backend selection: Super DAN, universal organism facade, deterministic test backend.
  - [x] 1-3. Per-run budget and timeout policy.
  - [x] 1-4. Keep priority/fairness beyond FIFO outside Plan 58.
- [x] 2. Start background runs durably
  - [x] 2-1. Persist run before execution begins.
  - [x] 2-2. Emit `background_run.started` with board/task/run ids.
  - [x] 2-3. Attach normalized Agent/Super DAN event streams to the board.
  - [x] 2-4. Return control to the surface immediately after admission/start acknowledgement.
- [x] 3. Add minimum checkpointed control
  - [x] 3-1. Stop/pause requests are recorded immediately and admitted at safe executor checkpoints.
  - [x] 3-2. Append items are injected at safe checkpoints with compact operator-context packets.
  - [x] 3-3. Defer rich resume/retry until nonblocking start/append/status/cancel works.
- [x] 4. Enforce minimum workspace isolation
  - [x] 4-1. Main-workspace writes require non-overlapping ownership locks.
  - [x] 4-2. Conflicting write work queues for the MVP.
  - [x] 4-3. Keep worktree diff admission outside Plan 58 unless a separate product need is opened.
  - [x] 4-4. Final validation belongs to each executor run; cross-run validation comes after merge/isolation support.
- [x] 5. Add regressions
  - [x] 5-1. Background run start returns before executor completion.
  - [x] 5-2. Two independent background jobs both emit events and terminal results.
  - [x] 5-3. Cancel pauses/stops only at a safe checkpoint.
  - [x] 5-4. Conflicting same-path work queues instead of starting a second main-workspace writer.

## Decisions
- The executor pool is not a chat loop. It consumes admitted task/run records and emits events.
- Capacity control belongs above provider-call gateway dispatch; gateway dispatch remains only the model-call queue.
- Isolated worktree output is non-authoritative until admitted.
- For the first proof, queueing conflicts is preferable to implementing new merge behavior.

## Notes
- Super DAN already has many pieces: event logs, hook/inbox state, worktree policy, and diff admission. This plan makes those capabilities a shared background-executor contract.
- Implemented through the V2 background execution path plus async admission metadata; ready dependency runs are promoted after their prerequisite task completes.
