# 10-5: Chat History & Session Integration

**Parent:** [10-chatbox-nl-workflow](10-chatbox-nl-workflow.md)
**Status:** completed — core persistence & UI done; graph delta tracking UI, cascade delete, offline resilience, tests, and docs deferred to Phase 7.2 (plan 12-*)
**Goal:** Persist chat conversations per workflow across reloads/tab switches, track graph mutation provenance in chat, and add session-scoped rollback metadata. Run-from-chat execution is split into [10-6](10-6-scoped-run-from-chat.md) for deeper backend scope.

## Tasks

- [x] 1. Chat message data model
  - [x] 1-1. `ChatMessage` Pydantic model in `src/dan/server/chat_store.py`:
    - `id: str` (UUID)
    - `role: "user" | "assistant" | "system"`
    - `content: str` (plain text with serialized mentions `@[name](type:id)`)
    - `mentions: list[Mention]` (resolved mention references)
    - `tool_calls: list[dict]` (persisted tool trace shown in chat UI)
    - `run_events: list[dict]` (execution status blocks streamed into chat)
    - `attachments: list[dict]` (file/artifact chips such as generated reports)
    - `mutation_plan: MutationPlan | None` (if assistant suggested graph changes)
    - `mutation_id: str | None` (stable identifier for mutation proposal/application lifecycle)
    - `mutation_status: "proposed" | "applied" | "partial" | "rejected" | "reverted" | None`
    - `token_usage: { prompt: int, completion: int } | None`
    - `timestamp: datetime`
    - `run_ref: RunRef | None` (optional execution reference populated by 10-6)
  - [x] 1-2. `ChatThread` model: `{ id: str, workflow_id: str, title: str, messages: list[ChatMessage], created_at, updated_at }`
  - [x] 1-3. TypeScript mirror types in `editor/src/types/chat.ts`

- [x] 2. Chat persistence backend
  - [x] 2-1. `ChatStore` class in `src/dan/server/chat_store.py`: filesystem-based persistence (JSON files in `./chats/{workflow_id}/` directory, same pattern as `GraphStore`)
  - [x] 2-2. API endpoints:
    - `GET /api/chats/{workflow_id}` — list all threads for a workflow
    - `GET /api/chats/{workflow_id}/{thread_id}` — load a specific thread
    - `POST /api/chats/{workflow_id}` — create a new thread
    - `PUT /api/chats/{workflow_id}/{thread_id}` — update thread (append messages)
    - `DELETE /api/chats/{workflow_id}/{thread_id}` — delete a thread
  - [x] 2-3. Auto-save: after each assistant response completes, persist the thread to disk
  - [x] 2-4. Wire endpoints in `app.py`

- [x] 3. Chat history UI
  - [x] 3-1. Thread list sidebar in `ChatPanel.tsx`: shows all threads for the current workflow, sorted by `updated_at` desc. Each row: title (auto-generated from first user message), message count, relative timestamp.
  - [x] 3-2. "New chat" button: creates a fresh thread (auto-titled from first message)
  - [x] 3-3. Click thread → load messages into the chat panel. The active thread is highlighted.
  - [x] 3-4. Delete thread: swipe-to-delete or right-click delete with confirmation
  - [x] 3-5. Thread title editing: click title to rename (inline edit)
  - [x] 3-6. Toggle between thread list and active chat: compact header with back-arrow when in active chat

- [x] 4. Auto-restore on reload / tab switch
  - [x] 4-1. On editor load: fetch the most recent thread for the active workflow and populate chat messages
  - [x] 4-2. On tab switch: save current thread, load the thread for the new tab's workflow.
  - [ ] 4-3. On workflow delete: delete associated chat threads (cascade) *(deferred to 12-5)*
  - [ ] 4-4. Offline resilience: if the server is unreachable, cache unsent messages in `localStorage` and sync on reconnect *(deferred to 12-1)*

- [ ] 5. Graph delta tracking *(partially done — model fields exist; UI deferred)*
  - [x] 5-1. Each assistant message that produces mutations records `MutationPlan` + `mutation_id` in persisted `ChatMessage` metadata. Session rollback cursor is kept client-side only.
  - [ ] 5-2. In the chat UI, messages with mutations show a "Changes" badge: "Added 2 nodes, removed 1 edge". Clickable to re-open the diff preview (10-4). *(deferred to 12-4)*
  - [ ] 5-3. Thread timeline view (stretch): a vertical timeline showing how the graph evolved through the conversation. Each mutation message is a node on the timeline with a before/after snapshot. *(deferred to Backlog)*
  - [ ] 5-4. Export thread: download the full conversation with mutation history as a Markdown or JSON file (for sharing, documentation, or reproducibility) *(deferred to 12-5)*

- [x] 6. Session-scoped rollback metadata (frontend-only)
  - [x] 6-1. Add `sessionMarkers: Record<messageId, { historyCursor: number }>` in `ChatPanel` local state to map chat messages to active-session undo positions.
  - [x] 6-2. `_recordMutationMarker(messageId)` helper records marker on mutation apply. Integration point for future mutation flow.
  - [x] 6-3. On reload/new session, markers are intentionally dropped. UI falls back to disabled "Revert" with tooltip "Available in current session only".
  - [x] 6-4. Markers cleared on thread/workflow switch to prevent cross-context corruption.

- [x] 7. Integration hooks for scoped execution (implemented by 10-6)
  - [x] 7-1. `runRef` rendered as status blocks: running (spinner), completed (check + View logs), failed (X + View logs).
  - [ ] 7-2. Add thread-level helper methods to append execution block messages from external producer (`appendRunEventMessage(threadId, runRef, payload)`). *(deferred to 12-4)*
  - [x] 7-3. Mutation and execution blocks coexist in one timeline without schema conflicts.
  - [ ] 7-4. Document clear boundary: this plan handles persistence/rendering, while 10-6 handles run intent detection, scoped execution API, and event fan-out. *(deferred — docs task)*

- [ ] 8. Tests *(deferred to 12-6)*
  - [x] 8-1. `ChatStore` tests: create/read/update/delete threads, persistence to disk, load on startup
  - [ ] 8-2. Chat endpoint tests: thread CRUD via httpx test client
  - [ ] 8-3. Graph delta tracking: verify mutation metadata (`mutation_plan`, `mutation_id`, `mutation_status`) stored in messages
  - [ ] 8-4. Auto-restore: verify thread loaded on tab switch, cascading delete on workflow delete
  - [ ] 8-5. Session-only rollback metadata: verify markers work before reload and are cleared/disabled after reload
  - [ ] 8-6. Frontend: verify thread list, mutation badges, and execution placeholder blocks (component test or manual)
  - [x] 8-7. Frontend serializer regression: preserve `tool_calls`, `run_events`, and `attachments` across save/load mapping.

- [ ] 9. Docs sync *(deferred — update when features land)*
  - [ ] 9-1. `architecture.md`: add `ChatStore`, chat persistence directory, chat endpoints to API table, and integration boundary with 10-6 scoped execution
  - [ ] 9-2. `llm-api-guide.md`: document chat thread API for programmatic access
  - [ ] 9-3. `README.md`: add conversational workflow authoring to feature summary
  - [ ] 9-4. `changelog.md`: implementation entry

## Decisions

- Rollback metadata is session-scoped and frontend-only (not persisted in `ChatStore`).
- `ChatStore` persists conversation/mutation metadata; scoped run execution mechanics live in 10-6.
- Cross-reload "Revert to here" is explicitly out of scope.

## Notes

- **Deferred to Phase 7.2:** Cascade delete (→ 12-5), offline resilience (→ 12-1), graph delta tracking UI — changes badge (→ 12-4), export thread (→ 12-5), execution block helpers (→ 12-4), all tests (→ 12-6), docs sync (→ when features land). Thread timeline (5-3) moved to Backlog as stretch goal.
- Chat history persistence follows the same filesystem pattern as `GraphStore`. JSON files in a `chats/` directory, one file per thread. Simple, no database dependency, version-controllable.
- Post-completion follow-up hardened the persistence contract so thread reloads preserve tool traces, run events, `estimated_cost`, scoped `run_ref` targets, and attachment chips. Shared mapping now lives in `editor/src/lib/chatMessagePersistence.ts`, with regressions in both Python and Vitest suites.
- 2026-03-19 follow-up: startup restore now prefers the workflow-scoped saved full-screen chat selection over the broader workspace-wide active-thread fallback, while still allowing explicit in-session handoffs (`useAppStore.activeChatThreadId`) to win. This fixes launches that reopened an older `_scratch` page even though the correct last-used full-screen chat had been persisted already.
- Run-from-chat is intentionally split into 10-6 so backend execution scope design (full/node/sub-graph) is specified in detail and tested independently.
- Session-only rollback keeps implementation simple and aligned with current in-memory undo stack; durable rollback/versioning can be a future enhancement.
- Thread timeline view (stretch) would make the conversation a visual history of graph evolution. Powerful for understanding how a workflow was iteratively built. Defer to post-MVP.
