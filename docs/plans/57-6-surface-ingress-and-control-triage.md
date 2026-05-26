# 57-6: Surface Ingress and Control Triage

**Parent:** [57-chat-agent-v2-control-plane](57-chat-agent-v2-control-plane.md)
**Status:** in-progress
**Goal:** Add the thin top-level intake layer that normalizes Telegram/frontend/CLI turns, decides Chat vs Agent vs control commands, and hands durable work to the right thread/task boundary.

## Tasks
- [x] 1. Define the canonical `SurfaceTurn` envelope
  - [x] 1-1. Include `surface_type`, `surface_id`, `session_id`, native chat/thread/message ids, user identity, timestamp, text, reply chain, privacy scope, and surface capabilities
  - [x] 1-2. Include structured `AttachmentRef` items from [57-8](57-8-structured-attachments-and-vision-inputs.md)
  - [x] 1-3. Include surface update handles for progress delivery from [57-9](57-9-agent-event-hooks-and-telegram-progress.md)
  - [x] 1-4. Keep backward-compatible mapping from the legacy chat portal fields during rollout
- [x] 2. Define the triage decision contract
  - [x] 2-1. Produce `chat_response`, `agent_suggested`, `agent_requested`, `append_to_active_run`, `continue_after_current`, `control_command`, or `ignored`
  - [x] 2-2. Route status, cancel, retry, approval, and clarification replies before any heavy model or organism call
  - [x] 2-3. Preserve explicit user choices for Chat or Agent over classifier guesses
  - [x] 2-4. Treat task-family hints such as coding, research, website, operator, or workflow as internal routing evidence only
  - [x] 2-5. Require an explicit surface action or command for append-vs-continue placement; do not infer an auto queue lane from free text
- [ ] 3. Map Telegram into `SurfaceTurn`
  - [x] 3-1. Map private chats, groups, and forum topics from chat id plus `message_thread_id`
  - [x] 3-2. Carry `message_id`, `reply_to_message_id`, caption, and reply-chain text as routing evidence
  - [ ] 3-3. Map photo/document/voice/media downloads into structured attachment refs, not only text markers
  - [x] 3-4. Preserve the existing Telegram fleet and single-adapter paths while routing V2 through one ingress contract
  - [x] 3-5. Carry bounded Telegram history, reply context, and lane/conversation ids into V2 Agent run payloads
  - [x] 3-6. Carry message-scoped Telegram lane ids for fresh Agent starts so separate requests do not share one chat-wide admission lane
- [ ] 4. Add privacy and policy gates at ingress
  - [ ] 4-1. Default private surfaces to continuity-safe behavior
  - [ ] 4-2. Require explicit opt-in before leaking prior private task context into group/shared chats
  - [ ] 4-3. Keep messaging surfaces confirm-first for mutation and external side effects unless policy explicitly overrides
  - [x] 4-4. Thread workspace root, mutation permission, and approval envelope into Agent runs
  - [x] 4-5. Infer workspace root from explicit path wording in the user turn when no surface workspace is provided
- [ ] 5. Validate with focused ingress tests
  - [x] 5-1. Telegram text turn becomes a complete `SurfaceTurn`
  - [x] 5-2. Telegram photo plus caption becomes a `SurfaceTurn` with `AttachmentRef`
  - [ ] 5-3. Reply to an active Agent progress message resolves as a follow-up/control turn
  - [ ] 5-4. Group-chat privacy defaults prevent unintended task-context injection

## Decisions
- The surface ingress layer is the top control plane, but it must stay thin. It owns identity, policy, queueing, and handoff, not worker topology.
- `SurfaceTurn` is the canonical V2 intake object. Legacy chat request bodies are adapters into this object.
- The triage seam is deterministic-first, model-assisted second, and replaceable behind typed decisions.
- Queue placement is explicit. Ingress may bind a turn to a task from reply/thread evidence, but the append-vs-continue lane must come from a deliberate user action, shortcut, button, or command.

## Notes
- 2026-04-30: Telegram now defaults to pure V2 in both standalone fleet and in-process adapter paths. Legacy/v1 overrides are ignored unless `DAN_TELEGRAM_ALLOW_V1=1` is explicitly set for debugging. The Telegram command menu/help text is reduced to V2-relevant commands only.
- 2026-05-22: Telegram Agent intake now mirrors GUI session isolation more closely. Chat V2 admission honors `surface_context.conversation.lane_key`, fresh Telegram Agent starts can fork per-message lanes, `/new` bypasses selected-session context, and `/reset` / `/clear` force-clear Telegram resume state.
- 2026-05-22: Focused bridge coverage now verifies that the backend-owned Telegram adapter sends per-message lane keys into Chat V2 and that reset forces a selected active run out of the lane before clearing history.
- 2026-04-30: Telegram V2 surface context now includes reply text, sender ids, conversation key, lane key, reply lane, and history counts; `SurfaceTurn.metadata` and Agent run payloads persist bounded recent history plus reply context so Agent runs can resolve short phone follow-ups.
- 2026-04-30: Workspace resolution now supports phone-friendly path inference. If a V2 turn says `path /repo/app`, `repo: ~/project`, or `in /workspace/project` and no explicit surface workspace was supplied, `SurfaceTurn.workspace_source` becomes `message_path` and the resolved path is carried into the Agent run.
- 2026-04-30: Telegram V2 is wired for both standalone fleet and in-process adapter paths. Set `DAN_TELEGRAM_CONTROL_PLANE=v2` or `DAN_ADAPTERS_CONTROL_PLANE=v2`; normal turns use the V2 chat ingress, while `/agent ...`, `/run ...`, `/build ...`, or `agent: ...` creates and executes a durable V2 Agent run. `DAN_TELEGRAM_WORKSPACE_ROOT` / `DAN_TELEGRAM_WORKSPACE_ID` bind Telegram sessions to a workspace; omitted values still fall through to the V2 default `~`.
- 2026-04-29: First slice landed `src/dan/server/chat_v2.py` with `SurfaceTurn`, `SurfaceUpdateHandle`, deterministic first-pass triage, explicit append/continue lane detection, legacy `ChatMessageRequest` normalization, and `/api/v2/chat/message` bridge coverage.
- This plan should reuse the identifier normalization from Plan 31-25 instead of inventing a second portal schema.
- This plan feeds [57-7](57-7-thread-task-identity-and-topic-queues.md) for existing/new thread and task binding.
- The first implementation can be additive beside `/api/chat/message`; changing the legacy endpoint is not required.
