# 57-10: Active-Run Operator Steering

**Parent:** [57-chat-agent-v2-control-plane](57-chat-agent-v2-control-plane.md)
**Status:** in-progress
**Goal:** Let GUI, TUI, Telegram, and CLI users steer an active Agent/Super DAN run through one V2 command and queue contract, with safe checkpoint admission and no surface-specific human chat queue.

## Tasks
- [x] 1. Freeze the operator-message contract
  - [x] 1-1. Keep human active-run messages in Chat/Agent V2 task/run queues, not in Super DAN organ inboxes.
  - [x] 1-2. Treat Super DAN `.dan-super/state/` inboxes as internal validation, repair, synthesis, scout, memory, and safety queues only.
  - [x] 1-3. Define canonical lanes for append-at-checkpoint, continue-after-current, status, pause, cancel, retry, and branch.
    - [x] Status is a read-only command lane that returns current run/task/queue state without appending to the run event log.
    - [x] Branch creates a sibling queued Agent task/run with explicit source-run/source-task lineage.
  - [x] 1-4. Make GUI, TUI, Telegram, and CLI commands call the same `AgentRunCommand` surface.
- [x] 2. Bind V2 Agent runs to live Super DAN execution
  - [x] 2-1. Extend the `AgentBackendAdapter` lifecycle with active-run command polling.
  - [x] 2-2. Bind each backend run to the durable V2 task, run, workspace, and queue state.
  - [x] 2-3. Preserve raw Super DAN event-log refs while emitting normalized command-admission events.
  - [x] 2-4. Keep `dan super-organism` direct CLI behavior available for compatibility, but do not make it the owner of human queue semantics.
- [ ] 3. Admit operator messages only at safe checkpoints
  - [x] 3-1. Poll queued commands after model responses.
  - [x] 3-2. Poll after tool calls and before the next model request.
  - [ ] 3-3. Poll before mutating writes, validation, repair, and final completion.
  - [x] 3-4. Emit visible held/admitted/injected events when commands cannot be applied immediately.
- [x] 4. Convert admitted messages into structured run context
  - [x] 4-1. Classify hard constraints, soft preferences, target-path changes, validation requirements, and follow-up objectives.
  - [x] 4-2. Update the operator-intent policy packet or compact context packets instead of appending opaque chat blobs.
  - [x] 4-3. Carry selected attachment refs and reply/history metadata through the same command packet.
  - [x] 4-4. Show the admitted context in progress views without exposing raw internal prompts.
- [x] 5. Add safe pause and cancel semantics
  - [x] 5-1. Pause at the next checkpoint and persist state, changed files, pending queue items, and latest trace refs.
  - [x] 5-2. Cancel at the next checkpoint without interrupting an in-flight mutating tool call.
  - [x] 5-3. Report whether the run is resumable, terminal, or blocked.
  - [x] 5-4. Add retry/resume behavior only after the persisted state contract is proven.
    - [x] Resume paused runs by clearing pause flags and requeueing the same Agent run under an explicit restart-from-paused-boundary policy.
    - [x] Retry terminal runs by preserving a compact previous-attempt record, clearing interruption flags, and requeueing the same Agent run under an explicit restart-from-original-request policy.
- [x] 6. Validate the lifecycle
  - [x] 6-1. Test append command injection before a write.
  - [x] 6-2. Test continue-after-current waits for terminal completion.
  - [x] 6-3. Test pause and cancel close streams and persist interrupted state.
  - [x] 6-4. Test GUI/TUI/Telegram/CLI command payloads normalize to the same backend contract.
  - [x] 6-5. Test human queue state stays separate from Super DAN organ inbox state.

## Decisions
- Chat/Agent V2 owns human active-run messages.
- Super DAN owns execution and internal organ queues.
- Queue placement must be explicit; no hidden auto-interrupt lane.
- Safe checkpoint injection is the first target. Mid-tool interruption is deferred.
- The TUI and GUI are sibling surfaces over the same command contract.

## Notes
- Checked out from idea-cart items `IC-001` through `IC-005`, `IC-011`, `IC-012`, and the active-run portions of `IC-013`.
- This plan builds on [57-3](57-3-agent-run-control-plane.md), [57-5](57-5-organism-backend-bridge.md), and [57-7](57-7-thread-task-identity-and-topic-queues.md).
- 2026-05-12: First implementation slice landed in the V2 store/backend layer. `POST /api/v2/agent-runs/{run_id}/commands` now turns append/continue commands into durable task queue items, `AgentBackendRuntime` lets backends admit checkpoint-append items, and append items emit `queue_item_injected` at backend start and Super DAN raw-event safe checkpoints. Direct `dan super-organism` remains unchanged.
- 2026-05-12: Continue-after-current now has a first terminal promotion path: when the current run reaches a terminal state, the first queued continue item is marked injected, linked back to the previous run, and promoted into the next queued Agent run with the same task/workspace/thread binding. Automatic background execution of promoted follow-up runs remains a later policy choice.
- 2026-05-12: `cancel` now shares the stop-request path: the command records `stop_requested` immediately, keeps the run active/queued until a backend checkpoint, then `AgentBackendRuntime` confirms `stopped` at the safe checkpoint. Pause/resume were still open in this slice.
- 2026-05-12: Queued operator messages now carry a deterministic compact `operator_context` packet with raw text, hard constraints, soft preferences, target paths, validation requirements, follow-up objective, and attachments. Admitted backend context renders those signals instead of only passing opaque chat text.
- 2026-05-12: Background execution now drains promoted after-current runs by policy. `AgentRunExecuteRequest` has `auto_execute_continuations` plus `max_promoted_continuations`; background runs default to executing promoted continuations serially, while synchronous execute still returns after promoting the next queued run. Terminal parent runs stay terminal after emitting the promotion event.
- 2026-05-12: `pause` now mirrors the safe checkpoint model: it records `pause_requested`, confirms `paused` through `AgentBackendRuntime` at a backend checkpoint, persists the checkpoint on run/task metadata, and treats `paused` as a stream-closing resumable status.
- 2026-05-12: `resume` now requeues paused runs by clearing pause flags, recording `resume_policy=restart_backend_run_from_paused_boundary`, and letting the normal execute endpoint run again. Richer mid-run stateful resume remains a future runtime capability.
- 2026-05-12: Surface-triaged queue items now carry the same compact `operator_context` packet as direct run commands, and queueing an append while a run is paused preserves the paused status until an explicit resume command.
- 2026-05-12: `retry` now has a first persisted Agent-run contract: terminal runs can be queued for a fresh execution attempt, previous attempt state is kept in compact `retry_history`, stop/pause flags and stale backend result metadata are cleared, and normal `/execute` performs the actual retry.
- 2026-05-12: Backend checkpoint detection now treats mutation-capable `tool.started` rows (`file_write`, `file_edit`, `shell_command`) as safe admission boundaries before the mutating call proceeds. Focused regression coverage now also proves V2 human queue items stay in ChatV2 state and do not create `.dan-super/state` organ inbox data.
- 2026-05-12: Focused payload-normalization coverage now locks the shared command queue contract across likely GUI/TUI/Telegram/CLI text aliases (`text`, `message`, `content`, and `objective`).
- 2026-05-12: `status` is now a read-only Agent-run command. It returns a normalized `status_reported` event payload with run/task/queue state through the shared command endpoint without mutating run status, queue state, or the persisted run event log.
- 2026-05-12: `branch_from` now creates a sibling queued Agent task/run with explicit `branched_from_*` lineage metadata. The original run/task stays untouched, while the command response includes the branch task/run snapshots for GUI/TUI/Telegram/CLI surfaces.
