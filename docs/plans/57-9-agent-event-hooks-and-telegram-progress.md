# 57-9: Agent Event Hooks and Telegram Progress

**Parent:** [57-chat-agent-v2-control-plane](57-chat-agent-v2-control-plane.md)
**Status:** in-progress
**Goal:** Normalize the hooks between the V2 triage plane and DAN organisms, then render progress and final results through Telegram updates, the V2 frontend, and other surfaces from the same event stream.

## Tasks
- [x] 1. Define command and event hooks
  - [x] 1-1. `AgentRunCommand`: start, append_followup, continue_after_current, approve, deny, stop, retry, branch_from, reprioritize, and resume
  - [x] 1-2. `AgentRunEvent`: accepted, queued, queue_item_added, queue_item_injected, planned, worker_started, model_text_delta, tool_used, artifact_changed, validation_started, repair_started, branch_created, needs_input, token_usage_recorded, completed, failed, blocked, stopped
  - [x] 1-3. `TaskSnapshot`: stable projection from current Agent events and task state
  - [x] 1-4. Carry source backend event id/type/path for reversible debugging
  - [x] 1-5. Carry per-round token usage deltas, running totals, and round metadata
- [ ] 2. Map organism backend events
  - [x] 2-1. Map Super DAN `live.*`, `super.hook.*`, validation, repair, and completion rows into generic Agent events
  - [x] 2-2. Preserve raw `.dan-super/runs/turn-XX/events.jsonl` refs for replay and inspection
  - [x] 2-3. Keep the mapping compatible with universal organism event rows from Plan 56
  - [x] 2-4. Map Super DAN provider usage rows into `token_usage_recorded` events
  - [ ] 2-5. Include selected backend and mapping rationale in Agent run metadata
- [ ] 3. Implement surface progress sinks
  - [x] 3-1. Telegram sink: send or edit a progress message using native chat/thread/message handles
  - [ ] 3-2. Frontend sink: stream the same normalized events into the V2 Agent run panel
  - [x] 3-3. CLI/other messaging sinks: render compact phase lines and final summaries
  - [x] 3-4. Keep rendering surface-specific while event semantics remain backend-generic
- [ ] 4. Define Telegram update behavior
  - [x] 4-1. Acknowledge accepted/queued work quickly
  - [x] 4-2. Edit one progress bubble when possible instead of sending noisy message spam
  - [x] 4-3. Throttle progress edits and fall back to new messages when edit fails
  - [x] 4-4. Preserve forum topic id and reply target when sending progress/final messages
  - [x] 4-5. Mark terminal result with final status, artifacts, and concise next action if blocked
  - [x] 4-6. Include aggregate token usage in terminal Telegram result text when available
  - [x] 4-7. Refresh the same Telegram V2 progress bubble on quiet websocket periods from the last real Agent/Super DAN event state
  - [x] 4-8. Keep Telegram bot commands/help focused on V2 Agent, status, cancel, and help instead of dated legacy menu actions
  - [x] 4-9. Keep ordinary Telegram V2 text on V2 Chat while explicit `/agent` and task-like workspace turns use durable Agent runs
  - [x] 4-10. Expose `/reset` / `/clear` as Telegram recovery commands that clear selected session context and force-stop the selected active run state
- [ ] 5. Support reconnect and final delivery
  - [x] 5-1. Rebuild event stream from persisted Agent run state and raw trace refs
  - [ ] 5-2. Deliver final results even if the frontend disconnects during execution
  - [ ] 5-3. Route follow-up prompts or `needs_input` events back to the active private surface when allowed
  - [ ] 5-4. Expose open-log/open-artifact refs in the V2 frontend and concise text refs in Telegram
- [ ] 6. Validate with progress-delivery tests
  - [x] 6-1. Fake Agent run emits normalized events and Telegram receives ack/progress/final updates
  - [x] 6-2. Queue events render a clear lane-specific queue-position update for checkpoint append vs continue-after-current
  - [ ] 6-3. Branch-created events render lineage without looking like queued continuation work
  - [x] 6-4. Edit failures fall back to new Telegram messages without dropping terminal results
  - [x] 6-5. Reconnect from persisted trace restores event history
  - [x] 6-6. Quiet-period Telegram V2 heartbeat uses backend event state such as web/tool/summarizing phases, not prompt text
  - [x] 6-7. Quiet-period Telegram V2 heartbeat shows total elapsed time plus age of the last backend event

## Decisions
- The triage layer and surfaces consume normalized Agent events, not raw Super DAN event names.
- Raw organism logs remain authoritative for debugging; normalized events are the product/control-plane projection.
- Telegram progress should use native update/edit semantics, but those mechanics stay in the surface sink, not in Agent execution.
- Queue and branch events must be distinct in the product stream: queue events stay attached to a task/run lane, while branch events create visible sibling thread/task lineage.

## Notes
- 2026-04-30: Telegram V2 is now the default/pure path. The standalone fleet and in-process adapter bridge both resolve Telegram to V2 unless `DAN_TELEGRAM_ALLOW_V1=1` is deliberately set. The Telegram command menu was narrowed to `/agent`, `/status`, `/cancel`, and `/help`.
- 2026-05-22: Telegram `/reset` and `/clear` now clear the selected resume lane and request/confirm a stopped run state for the selected active Agent run, so a wedged Telegram context does not keep blocking new Agent starts.
- 2026-04-30: Telegram V2 now keeps plain chat turns on V2 Chat and routes only explicit `/agent` / `/run` / `/build` / `agent:` turns plus obvious workspace/task text into durable Agent execution. The standalone fleet streams Agent run events over WebSocket, and the in-process bridge polls persisted Agent events into the same editable Telegram progress message when available. Both paths pass bounded recent history and reply context into backend requests. Quiet-period Agent progress edits include total elapsed time and, when available, the age of the last real backend event.
- 2026-04-30: Added a Telegram/V2 live progress state machine in `chat_v2_progress.py` and wired the standalone Telegram fleet websocket loop to refresh the same progress bubble every `DAN_TELEGRAM_V2_PROGRESS_INTERVAL` seconds during quiet backend periods. The status text is derived from normalized Agent event/source event/tool metadata.
- 2026-04-30: Added first-class V2 token accounting. `model.responded` rows with provider usage now map to `token_usage_recorded`, run/task snapshots persist aggregate `token_usage` plus per-round records, and Telegram progress/final output renders token counts when available.
- 2026-04-30: Standalone Telegram fleet V2 Agent turns now create `/api/v2/agent-runs`, trigger background `/execute`, and stream `WS /api/v2/agent-runs/{run_id}/events` back into one edit-in-place Telegram progress message with final artifact refs. The in-process Telegram adapter path also recognizes `/agent ...` and executes the durable V2 run, returning the final result.
- 2026-04-29: Added `src/dan/server/chat_v2_organism.py` to project Super DAN/universal-organism log rows into normalized `AgentRunEvent` values while preserving raw source event ids/types/paths.
- 2026-04-29: Added `src/dan/server/chat_v2_progress.py` with compact Agent-event rendering and a Telegram progress sink that uses native chat/thread/reply handles, edits an existing progress message when available, supports throttling, and falls back to a new Telegram message on edit failure. Agent run event endpoints now persist normalized events for replay.
- 2026-04-29: First slice landed the typed command/event/snapshot models in `src/dan/server/chat_v2.py`; frontend Agent-run streaming remains open.
- This plan is the hook contract between "top-level triage" and the DAN super organism.
- It builds on [57-3](57-3-agent-run-control-plane.md) and [57-5](57-5-organism-backend-bridge.md).
