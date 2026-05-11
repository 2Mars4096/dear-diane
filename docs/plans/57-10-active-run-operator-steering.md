# 57-10: Active-Run Operator Steering

**Parent:** [57-chat-agent-v2-control-plane](57-chat-agent-v2-control-plane.md)
**Status:** not-started
**Goal:** Let GUI, TUI, Telegram, and CLI users steer an active Agent/Super DAN run through one V2 command and queue contract, with safe checkpoint admission and no surface-specific human chat queue.

## Tasks
- [ ] 1. Freeze the operator-message contract
  - [ ] 1-1. Keep human active-run messages in Chat/Agent V2 task/run queues, not in Super DAN organ inboxes.
  - [ ] 1-2. Treat Super DAN `.dan-super/state/` inboxes as internal validation, repair, synthesis, scout, memory, and safety queues only.
  - [ ] 1-3. Define canonical lanes for append-at-checkpoint, continue-after-current, status, pause, cancel, retry, and branch.
  - [ ] 1-4. Make GUI, TUI, Telegram, and CLI commands call the same `AgentRunCommand` surface.
- [ ] 2. Bind V2 Agent runs to live Super DAN execution
  - [ ] 2-1. Extend the `AgentBackendAdapter` lifecycle with active-run command polling.
  - [ ] 2-2. Bind each backend run to the durable V2 task, run, workspace, and queue state.
  - [ ] 2-3. Preserve raw Super DAN event-log refs while emitting normalized command-admission events.
  - [ ] 2-4. Keep `dan super-organism` direct CLI behavior available for compatibility, but do not make it the owner of human queue semantics.
- [ ] 3. Admit operator messages only at safe checkpoints
  - [ ] 3-1. Poll queued commands after model responses.
  - [ ] 3-2. Poll after tool calls and before the next model request.
  - [ ] 3-3. Poll before mutating writes, validation, repair, and final completion.
  - [ ] 3-4. Emit visible held/admitted/injected events when commands cannot be applied immediately.
- [ ] 4. Convert admitted messages into structured run context
  - [ ] 4-1. Classify hard constraints, soft preferences, target-path changes, validation requirements, and follow-up objectives.
  - [ ] 4-2. Update the operator-intent policy packet or compact context packets instead of appending opaque chat blobs.
  - [ ] 4-3. Carry selected attachment refs and reply/history metadata through the same command packet.
  - [ ] 4-4. Show the admitted context in progress views without exposing raw internal prompts.
- [ ] 5. Add safe pause and cancel semantics
  - [ ] 5-1. Pause at the next checkpoint and persist state, changed files, pending queue items, and latest trace refs.
  - [ ] 5-2. Cancel at the next checkpoint without interrupting an in-flight mutating tool call.
  - [ ] 5-3. Report whether the run is resumable, terminal, or blocked.
  - [ ] 5-4. Add retry/resume behavior only after the persisted state contract is proven.
- [ ] 6. Validate the lifecycle
  - [ ] 6-1. Test append command injection before a write.
  - [ ] 6-2. Test continue-after-current waits for terminal completion.
  - [ ] 6-3. Test pause and cancel close streams and persist interrupted state.
  - [ ] 6-4. Test GUI/TUI/Telegram/CLI command payloads normalize to the same backend contract.
  - [ ] 6-5. Test human queue state stays separate from Super DAN organ inbox state.

## Decisions
- Chat/Agent V2 owns human active-run messages.
- Super DAN owns execution and internal organ queues.
- Queue placement must be explicit; no hidden auto-interrupt lane.
- Safe checkpoint injection is the first target. Mid-tool interruption is deferred.
- The TUI and GUI are sibling surfaces over the same command contract.

## Notes
- Checked out from idea-cart items `IC-001` through `IC-005`, `IC-011`, `IC-012`, and the active-run portions of `IC-013`.
- This plan builds on [57-3](57-3-agent-run-control-plane.md), [57-5](57-5-organism-backend-bridge.md), and [57-7](57-7-thread-task-identity-and-topic-queues.md).
