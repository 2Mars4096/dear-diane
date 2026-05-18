# 58: Async Core and Background Runtime

**Status:** completed
**Goal:** Prove the core stays responsive while long-running Agent/Super DAN work executes in durable background runs: while task A is still running, a new user turn for task B is admitted immediately, classified conservatively as parallel / queued-behind / append / clarify, reflected on a task board, and surfaced to the user without waiting for A to finish.

## Tasks
- [x] 1. Land the narrow nonblocking proof
  - [x] 1-1. Start task A as a durable background Agent/Super DAN run and return control to the foreground immediately.
  - [x] 1-2. Accept task B while task A is still running without waiting for A's executor.
  - [x] 1-3. Classify B into one of four MVP outcomes: `start_parallel`, `queue_after`, `append_to_active`, or `ask_clarification`.
  - [x] 1-4. Show both A and B on the task board with run ids, statuses, and the admission reason.
  - [x] 1-5. Keep status/progress/cancel/append commands responsive while A and/or B execute.
  - [x] 1-6. Link final answers/results back to the correct task/run instead of the most recent foreground turn.
- [x] 2. Build the foreground admission controller via [58-1-foreground-admission-controller.md](58-1-foreground-admission-controller.md)
- [x] 3. Normalize a minimum durable task board and run ledger via [58-2-durable-task-board-and-run-ledger.md](58-2-durable-task-board-and-run-ledger.md)
- [x] 4. Add conservative dependency/conflict scheduling via [58-3-dependency-conflict-scheduler.md](58-3-dependency-conflict-scheduler.md)
- [x] 5. Run background executors through resumable worker slots and basic isolation via [58-4-background-executor-pool-and-isolation.md](58-4-background-executor-pool-and-isolation.md)
- [x] 6. Expose the async relationship through surfaces and narrator via [58-5-surface-and-narrator-integration.md](58-5-surface-and-narrator-integration.md)
- [x] 7. Prove the terminal board UX via [58-6-tui-background-board.md](58-6-tui-background-board.md)
- [x] 9. Add rollout flags and migration rules
  - [x] 9-1. Keep direct `dan super-organism` behavior stable until the async board path is explicitly enabled.
  - [x] 9-2. Use V2 Chat/Agent surfaces as the first default async product path.
  - [x] 9-3. Preserve deterministic fallback for no-provider/local tests.
  - [x] 9-4. Document where legacy single-turn blocking behavior remains intentional.

## Decisions
- The foreground core is the operational analogue of the narrator/executor split: it observes state and talks to the user like the narrator, but it also has control authority to admit, queue, schedule, pause, cancel, branch, and attach follow-ups.
- The background executor owns expensive tool/model work. It should never own the user conversation loop.
- A running executor must not block the next user turn. Every new turn enters the foreground admission controller first.
- The first implementation should not try to solve general semantic dependency perfectly. It should prove nonblocking admission with conservative deterministic evidence first.
- MVP user turns are classified as `chat_or_status`, `append_to_active`, `queue_after`, `start_parallel`, `ask_clarification`, plus explicit `pause` / `cancel` / `status`.
- Parallelism is allowed only when deterministic path/resource evidence says it is safe. Unsafe or unclear overlap queues or asks for clarification.
- The scheduler should reuse Plan 57 V2 task/run records rather than introduce a parallel product architecture.
- The task board is the durable truth. Narrator output is a projection over board/run events, not the state itself.
- Direct operator instructions can change queue policy, but DAN safety/tool boundaries and workspace isolation rules remain hard gates.

## Notes
- This plan generalizes the UX pattern from `57-20`: narrator and executor already split response from work; Plan 58 applies that same split to the operational core.
- This plan does not replace Plan 57. Plan 57 defines the Chat/Agent product and command/event contracts; Plan 58 makes the core runtime behind those contracts nonblocking and dependency-aware.
- Super DAN worktrees and V2 active-run command queues are the nearest existing substrates. The implementation should consolidate them rather than create a third queue family.
- First acceptance target: `build script 1` starts in the background; before it completes, `hey can you build this as well` is accepted immediately; if paths/resources do not overlap it starts in parallel, otherwise it queues or asks a concise clarification.
- The terminal board is the fastest proof surface because `dan super-tui` already owns a live prompt, transcript, narrator stream, and compact progress rendering.
- Implementation landed in `chat_v2_async_core.py`, `ChatV2Store`, `routers/chat_v2.py`, and the default interactive `dan super-tui` local async path; `--server` remains an optional shared transport.
- Deliberate scope boundary: Plan 58 stops at the admission gate, durable board/queue, background execution, and dependency promotion. Richer policy concepts such as model dependency classifiers, priority/fairness scheduling, reward optimization, and worktree diff admission should stay out of this plan until there is a separate product need.
