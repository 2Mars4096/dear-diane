# 58-4: Background Executor Pool and Isolation

**Parent:** [58-async-core-and-background-runtime](58-async-core-and-background-runtime.md)
**Status:** not-started
**Goal:** Run the first admitted tasks as durable background executor jobs that return control immediately, emit board-visible events, and permit a second independent job to run concurrently under simple capacity and path-lock rules.

## Tasks
- [ ] 1. Define MVP executor slots
  - [ ] 1-1. Capacity per workspace/thread and global capacity, defaulting to a small safe parallelism.
  - [ ] 1-2. Per-run backend selection: Super DAN, universal organism facade, deterministic test backend.
  - [ ] 1-3. Per-run budget and timeout policy.
  - [ ] 1-4. Defer priority/fairness beyond FIFO until the proof is green.
- [ ] 2. Start background runs durably
  - [ ] 2-1. Persist run before execution begins.
  - [ ] 2-2. Emit `background_run.started` with board/task/run ids.
  - [ ] 2-3. Attach normalized Agent/Super DAN event streams to the board.
  - [ ] 2-4. Return control to the surface immediately after admission/start acknowledgement.
- [ ] 3. Add minimum checkpointed control
  - [ ] 3-1. Stop/pause requests are recorded immediately and admitted at safe executor checkpoints.
  - [ ] 3-2. Append items are injected at safe checkpoints with compact operator-context packets.
  - [ ] 3-3. Defer rich resume/retry until nonblocking start/append/status/cancel works.
- [ ] 4. Enforce minimum workspace isolation
  - [ ] 4-1. Main-workspace writes require non-overlapping ownership locks.
  - [ ] 4-2. Conflicting write work queues for the MVP.
  - [ ] 4-3. Defer worktree diff admission to the post-MVP expansion unless already available with no extra risk.
  - [ ] 4-4. Final validation belongs to each executor run; cross-run validation comes after merge/isolation support.
- [ ] 5. Add regressions
  - [ ] 5-1. Background run start returns before executor completion.
  - [ ] 5-2. Two independent background jobs both emit events and terminal results.
  - [ ] 5-3. Cancel pauses/stops only at a safe checkpoint.
  - [ ] 5-4. Conflicting same-path work queues instead of starting a second main-workspace writer.

## Decisions
- The executor pool is not a chat loop. It consumes admitted task/run records and emits events.
- Capacity control belongs above provider-call gateway dispatch; gateway dispatch remains only the model-call queue.
- Isolated worktree output is non-authoritative until admitted.
- For the first proof, queueing conflicts is preferable to implementing new merge behavior.

## Notes
- Super DAN already has many pieces: event logs, hook/inbox state, worktree policy, and diff admission. This plan makes those capabilities a shared background-executor contract.
