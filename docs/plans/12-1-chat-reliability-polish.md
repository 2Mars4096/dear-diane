# 12-1: Chat Reliability & Polish

**Parent:** [12-cursor-parity-chat](12-cursor-parity-chat.md)
**Status:** in-progress

**Goal:** Fix existing trust gaps, bugs, and architectural debt in the chat system so it forms a solid foundation before adding new features.

## Tasks

- [x] 1. Wire `client_graph_revision` end-to-end
  - [x] 1-1. `ChatPanel.tsx`: compute and send `clientGraphRevision` in `sendChatMessage` call (currently param exists in `api.ts` but is never populated)
  - [x] 1-2. `chat_manager.py`: compare `client_graph_revision` against server-side hash; reject if stale with descriptive error
  - [x] 1-3. Frontend: show "graph changed since your last message — refresh context" banner when stale rejection arrives
- [x] 2. History compaction / context window management
  - [x] 2-1. Implement token counting for chat history (`tiktoken` for OpenAI, approximate for others)
  - [x] 2-2. Add configurable `max_context_tokens` (default: 80% of model limit)
  - [x] 2-3. Implement sliding-window compaction: summarize older messages when history exceeds budget
  - [x] 2-4. Show token usage indicator in chat panel header (e.g., "3.2k / 128k tokens")
- [x] 3. Fix dead CTAs and UI gaps
  - [x] 3-1. `RunRefBlock` "View logs" button: wire to open LogPanel filtered to that run ID
  - [x] 3-2. Fix `retryLast` stale closure: capture latest `messages` state at retry time, not callback creation time
  - [x] 3-3. Add loading/disabled state to send button while streaming
  - [x] 3-4. Show error toast when WebSocket disconnects mid-stream, with reconnect button
- [x] 4. Wire run events into chat thread timeline
  - [x] 4-1. Extend `map_run_event_to_chat_block` to cover all event types (node_started, node_output, tool_call, errors)
  - [x] 4-2. Stream run events through the chat WebSocket (new event type `chat_run_event`)
  - [x] 4-3. Frontend: `chat.ts` adds `RunEventPayload` + `chat_run_event` type; `ChatPanel.tsx` handles run event WS messages
  - [ ] 4-4. Collapse verbose run output by default; expand on click *(deferred — needs richer rendering)*
- [x] 5. Build-mode strict edge safety in retry/replan paths
  - [x] 5-1. Ensure `_coerce_strict_edges` runs on retry and replan mutation plans, not just the first attempt
  - [ ] 5-2. Add test: build-mode mutation → validation failure → auto-retry → verify strict edges on second attempt *(deferred — edge-case coverage, not blocking reliability)*
- [x] 6. Environment variable reconciliation
  - [x] 6-1. Audit all env var references: `DAN_LLM_MODEL`, `DAN_LLM_DEFAULT_MODEL`, `DAN_CHAT_MODEL`
  - [x] 6-2. Consolidate to two vars: `DAN_LLM_MODEL` (execution default) and `DAN_CHAT_MODEL` (chat override, falls back to `DAN_LLM_MODEL`)
  - [x] 6-3. Update `.env.example`, `README.md`, `llm-api-guide.md` with canonical names
- [x] 7. Integration tests for chat endpoints (`tests/test_server/test_chat_integration.py`)
  - [x] 7-1. Test `/api/chat/message` round-trip (stream channel returned, mock provider streams events)
  - [x] 7-2. Test thread persistence (create, list, get, update messages, delete)
  - [x] 7-3. Test stale revision rejection (wrong `client_graph_revision` → `revision_mismatch` flag)
  - [x] 7-4. Test `/run` command dispatch (full scope, node scope w/ missing input error, nonexistent graph, stream channel)
  - [ ] 7-5. Test mutation flow: NL request → `chat_mutation` event → apply → verify graph state *(deferred — needs richer mock)*
- [ ] 8. Reconcile plan documentation with actual scope *(deferred — housekeeping task, not blocking feature work)*
  - [ ] 8-1. Audit plans 10-5, 10-6, 10-2: mark genuinely incomplete tasks as deferred (not completed) *(deferred — same as parent)*
  - [ ] 8-2. Move deferred items to backlog in `todo.md` or into this phase's tasks *(deferred — same as parent)*
  - [ ] 8-3. Update plan statuses to reflect reality *(deferred — same as parent)*

## Decisions

- Token counting: `tiktoken` added as main dependency (pre-built wheels on all major platforms); `cl100k_base` encoding used as fallback for non-OpenAI models; `len(text) // 4` only when tiktoken unavailable.
- Compaction is transparent: user sees full history in UI, LLM receives compacted version.
- 4-phase compaction: (1) keep system prompt, (2) keep recent N messages, (3) truncate older assistant messages (first + last sentence), (4) drop oldest if still over budget.
- `context_window` field added to `ChatCompleteEvent` and `ChatMutationEvent` to communicate model capacity to the frontend.

## Notes

- This sub-plan is a prerequisite for all other 12-* sub-plans. No new features until trust gaps are closed.
- Task 2 (history compaction) is the highest-impact reliability item — unbounded history causes silent context overflow and degraded LLM quality.
- Task 7 (integration tests) provides the safety net for all subsequent work in Phase 7.2.
