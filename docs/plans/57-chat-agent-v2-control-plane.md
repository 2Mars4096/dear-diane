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
