# 57: Chat / Agent V2 Control Plane

**Status:** in-progress
**Goal:** Build a cleaner V2 product plane with only two user-facing modes, Chat and Agent, backed by a surface-ingress triage layer that can bind Telegram/frontend/CLI turns to durable threads, topic queues, and Agent runs while leaving the current editor, modes, and legacy chat/control surfaces cleanly present but unwired from the new path.

## Tasks
- [ ] 1. Define the two-mode product contract via [57-1-chat-agent-product-contract.md](57-1-chat-agent-product-contract.md)
- [ ] 2. Split conversation handling from task execution via [57-2-slim-chat-manager-v2.md](57-2-slim-chat-manager-v2.md)
- [ ] 3. Expose Super-DAN-style Agent runs through a backend control plane via [57-3-agent-run-control-plane.md](57-3-agent-run-control-plane.md)
- [ ] 4. Normalize Agent profiles and hyperparameters via [57-4-agent-profile-policy.md](57-4-agent-profile-policy.md)
- [ ] 5. Decide and implement the organism backend bridge via [57-5-organism-backend-bridge.md](57-5-organism-backend-bridge.md)
- [ ] 6. Normalize surface ingress and control triage via [57-6-surface-ingress-and-control-triage.md](57-6-surface-ingress-and-control-triage.md)
- [ ] 7. Bind threads, tasks, and topic queues via [57-7-thread-task-identity-and-topic-queues.md](57-7-thread-task-identity-and-topic-queues.md)
- [ ] 8. Make attachments and figure inputs first-class via [57-8-structured-attachments-and-vision-inputs.md](57-8-structured-attachments-and-vision-inputs.md)
- [ ] 9. Normalize Agent event hooks and Telegram progress delivery via [57-9-agent-event-hooks-and-telegram-progress.md](57-9-agent-event-hooks-and-telegram-progress.md)
- [ ] 10. Wire the lightweight V2 frontend through the UI companion plan [../UI-plans/5-chat-agent-v2-shell.md](../UI-plans/5-chat-agent-v2-shell.md)
- [ ] 11. Keep legacy surfaces present but isolated
  - [ ] 11-1. Leave existing Chat, Research, Development, Content, Operations, and graph editor routes available
  - [ ] 11-2. Avoid routing V2 Agent runs through old mode-specific frontend state
  - [ ] 11-3. Keep old `/api/chat/message` behavior stable until V2 endpoints are proven
- [ ] 12. Add observability, persistence, and rollback gates
  - [ ] 12-1. Persist Agent runs separately from chat messages while linking final results back into the thread
  - [x] 12-2. Stream task events with reconnect semantics equivalent to chat streams
  - [x] 12-3. Persist per-round and aggregate token usage in Agent events, runs, and task snapshots
  - [ ] 12-4. Add a feature flag or alternate route so V2 can be disabled without touching legacy flows
  - [ ] 12-5. Add focused server/frontend regressions before making V2 the default
- [ ] 13. Add active-run operator steering via [57-10-active-run-operator-steering.md](57-10-active-run-operator-steering.md) — in progress: append/continue commands now share durable V2 queue records with compact operator-context packets, checkpoint append has backend admission, continue-after-current promotes the next queued run after terminal completion, background execution can drain promoted continuations, status reports are read-only, branch creates sibling queued Agent runs with lineage, cancel/pause confirm only at backend checkpoints, paused runs can be resumed by requeueing, and terminal runs can be retried by requeueing under an explicit restart policy
- [ ] 14. Add shared progress and checkpoint UX via [57-11-progress-and-checkpoint-ux.md](57-11-progress-and-checkpoint-ux.md)
- [ ] 15. Add a terminal-first Super DAN TUI via [57-12-super-dan-terminal-tui.md](57-12-super-dan-terminal-tui.md) — in progress: first local TUI/event-log slice landed with a boxed message composer and richer one-panel progress/results projection; active-run command steering remains under 57-10
- [ ] 16. Add skill mention and autocomplete UX via [57-13-skill-mention-ux.md](57-13-skill-mention-ux.md) — in progress: shared `$skill-name` parsing/metadata/preflight now lives below surfaces, TUI and plain Super DAN CLI pass selected skills to the runner, explicit Super DAN skill-packet forcing with selected-skill constraints/reference excerpts landed; GUI composer and V2/Telegram metadata remain open
- [x] 17. Add Super DAN TUI conversational timeline rendering via [57-14-super-dan-tui-conversational-timeline.md](57-14-super-dan-tui-conversational-timeline.md)
- [x] 18. Add Super DAN TUI session transcript continuity via [57-15-super-dan-tui-session-transcript.md](57-15-super-dan-tui-session-transcript.md)
- [x] 19. Add a four-lane Super DAN TUI intent gate via [57-16-super-dan-tui-four-lane-intent-gate.md](57-16-super-dan-tui-four-lane-intent-gate.md)
- [x] 20. Add neutral TUI progress and result surfaces via [57-17-super-dan-tui-neutral-progress-and-result-surface.md](57-17-super-dan-tui-neutral-progress-and-result-surface.md)
- [ ] 21. Add a core progress narrator lane via [57-18-core-progress-narrator-layer.md](57-18-core-progress-narrator-layer.md)
- [ ] 22. Integrate progress narration across surfaces via [57-19-surface-progress-narrator-integration.md](57-19-surface-progress-narrator-integration.md)

## Decisions
- The user-facing product modes are only **Chat** and **Agent**.
- Chat is for fast conversation, explanation, and inspection. It should not own worker orchestration.
- Agent is for bounded task execution. It owns planning, worker allocation, parallelism, validation, repair, and final reporting.
- Super DAN is the proving implementation for the first Agent path because its live execution semantics, progress stream, validation pass, and hook/inbox state already behave closest to the desired product.
- Agent knobs should be profile-first (`Fast`, `Balanced`, `Deep`, `Max`) rather than exposing raw agent counts as the primary UI.
- Raw hyperparameters still exist internally: agent count, parallelism cap, worker timeout, tool-call budget, repair rounds, validator count, planner depth, artifact partitioning policy, write permission, and approval policy.
- The current heavy editor remains available. V2 grows beside it and should not require deleting or rewiring existing modes before it proves itself.
- The top-level triage layer owns surface normalization, thread/task identity, queueing, approval/status/cancel handling, and progress delivery. It should not become a second organism.
- V2 queue placement is explicit rather than automatic: active Agent runs expose separate checkpoint-append and continue-after-current lanes, and surfaces must not hide that choice behind an `auto` send mode.
- The organism layer owns task execution: planning, worker allocation, tool use, validation, repair, and final reporting.
- Hooks between triage and organisms should be typed and durable: `AgentRunCommand`, `AgentRunEvent`, `TaskSnapshot`, `AttachmentRef`, and `PolicyEnvelope`.
- Telegram and other messaging surfaces must pass structured attachment refs and native update handles into V2. Legacy text markers such as `[Attachment: path]` may remain compatibility input but must not be the V2 canonical contract.

## Notes
- 2026-04-30: V2 now infers a task workspace from explicit path wording in the user turn (`path /repo`, `repo: ~/project`, `in /workspace`) when no surface workspace is provided, and same-topic explicit follow-ups can inherit the active task workspace instead of falling back to `~`. Agent events/runs/tasks now also persist per-round and aggregate token usage, and Telegram progress/final text renders the counts when available.
- 2026-04-30: Telegram can now use the V2 path. With `DAN_TELEGRAM_CONTROL_PLANE=v2` or `DAN_ADAPTERS_CONTROL_PLANE=v2`, normal Telegram turns go to `/api/v2/chat/message`; `/agent ...`, `/run ...`, `/build ...`, or `agent: ...` creates a durable V2 Agent run, executes it, and the standalone fleet streams normalized Agent events back through Telegram edit-in-place progress.
- 2026-04-30: Agent runs now have an executable backend path. `/api/v2/agent-runs/{run_id}/execute` selects an internal backend, defaults to Super DAN, persists backend metadata/final results, and the new WebSocket event stream replays normalized Agent events from durable JSONL for reconnect. The deterministic backend covers provider-free tests; live stop/cancel interruption is still open.
- 2026-04-30: V2 surface sessions now carry explicit workspace binding. `SurfaceTurn` includes `workspace_root` and `workspace_id`, omitted workspace input defaults to server-expanded `~`, topic keys include workspace identity to avoid cross-workspace task binding, and task/run snapshots plus start-command payloads persist the resolved workspace.
- 2026-05-11: The first terminal-first Super DAN TUI slice is available as `dan super-tui` / `dan-super-tui`. It reuses local Super DAN event logs and opt-in renderer hooks while leaving default `dan super-organism` output stable; live steering commands remain deferred to Plan 57-10.
- 2026-05-11: The TUI keeps the compact header plus one `Recent Events` panel, but that panel now shows current step, run context, progress details, concrete tool summaries, results, blockers, and trace refs.
- 2026-05-11: Super DAN TUI now has prompt-toolkit command suggestions after `/` and skill suggestions after `$`, backed by the same read-through DAN/Codex/Claude/Cursor skill catalog used for Super DAN live skill packets.
- 2026-05-11: The TUI now treats bare `$skill` text as mention selection/disambiguation instead of a runnable objective, and direct local `/append` / `/continue` / `/pause` / `/cancel` output is explicit that real steering requires the server-backed V2 command queue.
- 2026-05-11: The TUI idle shell now renders a boxed message composer, and TUI-selected `$skill-name` mentions are passed into Super DAN live briefs as explicit advisory skill packets before passive auto-selection.
- 2026-05-11: Explicit TUI skill mentions now strengthen the Super DAN live brief with selected-skill constraints and bounded companion reference excerpts, so scaffold skills such as `$scaffold-research` carry concrete scaffold requirements.
- 2026-05-11: Explicit skill runs now route through the shared `src/dan/skills/invocation.py` contract. TUI/CLI surfaces provide autocomplete or raw `$skill-name` parsing and pass selected-skill metadata, while the Super DAN runner owns packet injection, selected-skill constraints/reference excerpts, and conventional file-backed preflight hooks.
- 2026-04-29: Second implementation slice added durable V2 task/run state and retrieval APIs. `src/dan/server/chat_v2_store.py` persists task snapshots, Agent run records, explicit append/continue queue items, and run event JSONL; `/api/v2/chat/message` records state while delegating to legacy streams; `/api/v2/agent-runs`, task/thread lookup, run event, and run command endpoints expose the control-plane state. `src/dan/server/chat_v2_organism.py` maps organism rows into normalized Agent events, and `src/dan/server/chat_v2_progress.py` adds the first Telegram progress sink over those normalized events.
- 2026-04-29: First implementation slice landed the V2 ingress contract and bridge. `src/dan/server/chat_v2.py` now defines `SurfaceTurn`, `AttachmentRef`, `AgentRunCommand`, `AgentRunEvent`, `TaskSnapshot`, deterministic first-pass triage, explicit queue-lane handling, and a legacy bridge payload; `/api/v2/chat/message` forces `control_plane_mode=v2` while delegating to the existing chat stream machinery; legacy `/api/chat/message` remains unchanged by default and selected `v2` requests return a compact V2 bridge summary without mutating legacy `surface_context`.
- This plan follows Plan 56 rather than replacing it. Plan 56 makes organism execution more universal; Plan 57 turns that organism substrate into a simpler product/control-plane boundary.
- Plan 55 scheduler work supplies the reusable agent-count, parallelism, artifact partition, readiness, and validation policy vocabulary that Agent profiles should consume.
- Initial implementation should prefer additive endpoints such as `/api/agent-runs` over changing `/api/chat/message` semantics in-place.
- The organism bridge is intentionally separated from the public Agent API so the first backend can be Super DAN while the long-term backend migrates toward universal organism facades.
- The desired request shape is:
  `surface turn -> ingress triage -> existing/new thread and task -> chat response OR queued Agent run -> normalized Agent events -> surface progress updates -> final result linked back to chat`.
- Legacy mode surfaces may still inspect Agent logs later, but they should not be required for the V2 happy path.
- The Telegram target flow is: Telegram message/media -> surface ingress -> triage -> existing/new task binding -> topic queue -> Agent run retrieval/execution -> throttled Telegram message edits/progress -> final result and artifacts.
- Branching must remain visibly distinct from queueing: a branch creates a sibling thread/task lineage, while checkpoint append and continue-after-current stay attached to the current task.
- 2026-05-11: Idea-cart UX items were checked out into four follow-up plans: active-run operator steering, progress/checkpoint rendering, a terminal-first TUI, and skill mention UX. GUI and TUI are sibling surfaces over shared V2 command/event contracts, not competing owners of task semantics.
- 2026-05-11: Later TUI feedback was checked out into two additional follow-up plans: default progress should become a flowing conversational timeline with raw telemetry as a debug view, and the TUI should preserve visible chat history across turns until `/reset`.
- 2026-05-11: `57-14` and `57-15` landed in the TUI layer without changing Super DAN core execution: the default progress view is now a conversational timeline with coalescing and `--raw-events`, and interactive sessions persist visible transcript history under `.dan-super/tui/`.
- 2026-05-12: The first `57-10` slice landed below the surfaces: direct append/continue `AgentRunCommand` payloads now persist as V2 task queue items, backend execution admits checkpoint-append items through `AgentBackendRuntime`, and continue-after-current terminal promotion creates the next queued Agent run from the first queued follow-up.
- 2026-05-12: Cancel now uses the same checkpoint boundary: `cancel` records `stop_requested`, then `AgentBackendRuntime` confirms `stopped` at a backend checkpoint instead of marking a run stopped before execution has actually reached a boundary.
- 2026-05-12: Queued operator messages now carry compact deterministic operator-context packets for constraints, preferences, target paths, validation requirements, objectives, and attachments, so backend prompts/progress can consume structured context instead of opaque follow-up blobs.
- 2026-05-12: Background Agent execution can now auto-run promoted `continue_after_current` follow-ups serially within a bounded continuation policy; synchronous execution still returns after the current run and leaves the promoted run queued.
- 2026-05-12: Pause now follows the same safe checkpoint boundary as cancel: `pause` records `pause_requested`, backend runtime confirms `paused` at a checkpoint, and `paused` is treated as a stream-closing resumable status.
- 2026-05-12: Resume now clears pause state and requeues the same Agent run under an explicit restart-from-paused-boundary policy. This gives V2 a durable resume command while leaving richer mid-run restoration to future runtime state work.
- 2026-05-12: Retry now requeues terminal Agent runs under an explicit restart-from-original-request policy, keeps compact previous-attempt metadata, and clears stale stop/pause/backend-result flags before normal execution starts again.
- 2026-05-12: Safe checkpoint coverage now includes mutation-capable `tool.started` rows for `file_write`, `file_edit`, and `shell_command`, and the focused V2 suite now covers human queue state staying out of Super DAN `.dan-super/state` inboxes.
- 2026-05-12: Shared command-payload normalization coverage now proves GUI/TUI/Telegram/CLI-style text aliases land in the same durable V2 queue item and compact operator-context contract.
- 2026-05-12: `status` is now a read-only shared Agent-run command lane, returning a `status_reported` payload without appending to the run event log or changing task/run state.
- 2026-05-12: `branch_from` now creates sibling queued Agent task/run records with explicit lineage and returns branch snapshots through the shared command response.
- 2026-05-12: Added follow-up plans for a generic Super DAN TUI four-lane intent gate (`read-only/write` x `simple/complex`) and a neutral progress/result surface so small read-only requests can answer directly instead of starting autonomous planner/build work.
- 2026-05-12: Added follow-up plans for a separate core `narrator read-only` lane plus TUI/GUI/CLI narrator integration. The new top-level intent split is `narrator read-only`, `executor read-only`, and `executor write`; `simple` versus `complex` remains an executor-effort hint rather than the primary permission boundary.
- 2026-05-12: The first `57-18` / `57-19` slice landed: core narrator contracts live in `src/dan/agent_runtime/progress_narrator.py`, and Super TUI progress/status turns route to `narrator read-only` with immediate snapshot fallback, optional no-tool model narration, `Answer:` rendering, and flat `assistant_narrator` transcript persistence.
- 2026-05-12: `57-18` is complete: the core narrator now has cancellable async jobs, lifecycle events, stale-response marking, and repeated-question cancellation. `57-19` remains open for GUI/Telegram consumption plus direct-local active-run prompt concurrency.
