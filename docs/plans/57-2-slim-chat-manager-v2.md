# 57-2: Slim Chat Manager V2

**Parent:** [57-chat-agent-v2-control-plane](57-chat-agent-v2-control-plane.md)
**Status:** not-started
**Goal:** Create a smaller conversation manager that owns chat state and routing decisions but delegates bounded task execution to an Agent control plane.

## Tasks
- [ ] 1. Define `ChatManagerV2`
  - [ ] 1-1. Own thread/session identity, history normalization, streaming chat responses, and persistence
  - [ ] 1-2. Keep tool orchestration out of the chat manager except for narrow read-only/context helpers
  - [ ] 1-3. Produce a typed route decision: `chat_response`, `agent_suggested`, or `agent_requested`
- [ ] 2. Define the turn classifier seam
  - [ ] 2-1. Use deterministic rules for obvious Chat vs Agent cases first
  - [ ] 2-2. Add a replaceable model-assisted classifier only behind the typed decision contract
  - [ ] 2-3. Preserve user-forced Chat/Agent choices over classifier guesses
- [ ] 3. Define stream behavior
  - [ ] 3-1. Chat response streams remain token/message-oriented
  - [ ] 3-2. Agent handoff emits a task-run start event, then task events stream from the Agent plane
  - [ ] 3-3. Thread persistence stores the final assistant summary plus a task-run reference
- [ ] 4. Avoid legacy growth
  - [ ] 4-1. Do not add more product modes to the existing `ChatManager`
  - [ ] 4-2. Keep V1 `/api/chat/message` behavior stable until V2 endpoints are ready
  - [ ] 4-3. Add focused tests around routing decisions and persistence shape

## Decisions
- ChatManager V2 is a conversation shell, not an organism runner.
- It may call an Agent control plane, but it does not decide worker topology directly.
- The old `ChatManager` remains in place for legacy surfaces during rollout.

## Notes
- Candidate endpoints: `/api/v2/chat/message` for the new product contract, or a V2 body flag on `/api/chat/message` only after compatibility risk is reviewed.
