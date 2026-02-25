# 10-5: Chat History & Execution Integration

**Parent:** [10-chatbox-nl-workflow](10-chatbox-nl-workflow.md)
**Status:** not-started
**Goal:** Persist chat conversations per workflow so they survive page reloads and tab switches. Add the ability to run workflows, nodes, or sub-graphs directly from chat and stream execution results back into the conversation thread.

## Tasks

- [ ] 1. Chat message data model
  - [ ] 1-1. `ChatMessage` Pydantic model in `src/dan/server/chat_store.py`:
    - `id: str` (UUID)
    - `role: "user" | "assistant" | "system"`
    - `content: str` (plain text with serialized mentions `@[name](type:id)`)
    - `mentions: list[Mention]` (resolved mention references)
    - `mutation_plan: MutationPlan | None` (if assistant suggested graph changes)
    - `mutation_status: "proposed" | "applied" | "partial" | "rejected" | "reverted" | None`
    - `graph_snapshot_id: str | None` (undo stack pointer before this message's mutations)
    - `token_usage: { prompt: int, completion: int } | None`
    - `timestamp: datetime`
    - `run_ref: RunRef | None` (if this message triggered or displays a run)
  - [ ] 1-2. `ChatThread` model: `{ id: str, workflow_id: str, title: str, messages: list[ChatMessage], created_at, updated_at }`
  - [ ] 1-3. TypeScript mirror types in `editor/src/types/chat.ts`

- [ ] 2. Chat persistence backend
  - [ ] 2-1. `ChatStore` class in `src/dan/server/chat_store.py`: filesystem-based persistence (JSON files in `./chats/{workflow_id}/` directory, same pattern as `GraphStore`)
  - [ ] 2-2. API endpoints:
    - `GET /api/chats/{workflow_id}` — list all threads for a workflow
    - `GET /api/chats/{workflow_id}/{thread_id}` — load a specific thread
    - `POST /api/chats/{workflow_id}` — create a new thread
    - `PUT /api/chats/{workflow_id}/{thread_id}` — update thread (append messages)
    - `DELETE /api/chats/{workflow_id}/{thread_id}` — delete a thread
  - [ ] 2-3. Auto-save: after each assistant response completes, persist the thread to disk
  - [ ] 2-4. Wire endpoints in `app.py`

- [ ] 3. Chat history UI
  - [ ] 3-1. Thread list sidebar in `ChatPanel.tsx`: shows all threads for the current workflow, sorted by `updated_at` desc. Each row: title (auto-generated from first user message), message count, relative timestamp.
  - [ ] 3-2. "New chat" button: creates a fresh thread (auto-titled from first message)
  - [ ] 3-3. Click thread → load messages into the chat panel. The active thread is highlighted.
  - [ ] 3-4. Delete thread: swipe-to-delete or right-click delete with confirmation
  - [ ] 3-5. Thread title editing: click title to rename (inline edit)
  - [ ] 3-6. Toggle between thread list and active chat: compact header with back-arrow when in active chat

- [ ] 4. Auto-restore on reload / tab switch
  - [ ] 4-1. On editor load: fetch the most recent thread for the active workflow and populate chat messages
  - [ ] 4-2. On tab switch: save current thread, load the thread for the new tab's workflow. Store `activeThreadId` per tab in `TabSnapshot`.
  - [ ] 4-3. On workflow delete: delete associated chat threads (cascade)
  - [ ] 4-4. Offline resilience: if the server is unreachable, cache messages in `localStorage` and sync on reconnect

- [ ] 5. Graph delta tracking
  - [ ] 5-1. Each assistant message that produces mutations records the `MutationPlan` and `graph_snapshot_id` in its `ChatMessage` metadata
  - [ ] 5-2. In the chat UI, messages with mutations show a "Changes" badge: "Added 2 nodes, removed 1 edge". Clickable to re-open the diff preview (10-4).
  - [ ] 5-3. Thread timeline view (stretch): a vertical timeline showing how the graph evolved through the conversation. Each mutation message is a node on the timeline with a before/after snapshot.
  - [ ] 5-4. Export thread: download the full conversation with mutation history as a Markdown or JSON file (for sharing, documentation, or reproducibility)

- [ ] 6. Run from chat
  - [ ] 6-1. Detect run intent in user messages: explicit commands (`/run`, `/run @NodeName`, `/run @WorkflowName`) or natural language ("run the workflow", "execute the writer node", "test this pipeline")
  - [ ] 6-2. `RunFromChat` actions:
    - **Run full workflow**: equivalent to clicking the Run button. Uses existing `startRun` flow.
    - **Run single node**: execute just the mentioned node with mock/default inputs. Uses existing `startRun` with node-scoped entry point.
    - **Run sub-graph**: execute a composite node's sub-graph. Drill-in + run.
  - [ ] 6-3. Before running: if the workflow has input variables (InputNode), show the `RunInputsDialog` or prompt for inputs in chat ("What topic should I use?")
  - [ ] 6-4. The `ChatManager` calls `RunManager.start_run()` (existing infrastructure) and subscribes to run events

- [ ] 7. Execution streaming in chat
  - [ ] 7-1. While a run is active, stream key events into the chat thread as special message blocks:
    - **Run started**: "Running workflow..." with a progress indicator
    - **Node completed**: compact summary "✓ Planner completed (1.2s, 450 tokens)" — collapsed by default, expandable to show output
    - **Node failed**: "✗ Reviewer failed: [error message]" with red styling
    - **Run completed**: "Run finished in 12.3s (2,450 tokens)" with a summary
    - **Run failed**: error details with suggestion to fix
  - [ ] 7-2. These execution blocks are interleaved with regular chat messages. The user can ask questions mid-run: "why did the planner take so long?" or "what did the reviewer say?"
  - [ ] 7-3. Execution blocks are visually distinct from regular messages: different background color, monospace font for outputs, collapsible sections
  - [ ] 7-4. Link to log panel: each execution block has a "View in logs" link that switches to the LogPanel and scrolls to the relevant event
  - [ ] 7-5. After run completes, the user can say "the output looks wrong, change the reviewer prompt to be more critical" — seamless transition between execution and editing

- [ ] 8. Error diagnosis from chat
  - [ ] 8-1. When a run fails, include the error context in the chat (node that failed, error message, input data)
  - [ ] 8-2. Auto-suggest: "The Reviewer node failed with a schema validation error. Would you like me to fix the output schema?" — the LLM proactively suggests graph mutations based on the error.
  - [ ] 8-3. "Fix it" shortcut: user can reply "yes" or "fix it" and the LLM produces a mutation plan to address the error
  - [ ] 8-4. Re-run after fix: after applying the fix, suggest "Run again?" in chat

- [ ] 9. Tests
  - [ ] 9-1. `ChatStore` tests: create/read/update/delete threads, persistence to disk, load on startup
  - [ ] 9-2. Chat endpoint tests: thread CRUD via httpx test client
  - [ ] 9-3. Run-from-chat: mock run manager, verify run starts from chat command, events stream to response
  - [ ] 9-4. Graph delta tracking: verify mutation_plan and snapshot_id stored in messages
  - [ ] 9-5. Auto-restore: verify thread loaded on tab switch, cascading delete on workflow delete
  - [ ] 9-6. Frontend: verify thread list, run status blocks, execution streaming (component test or manual)

- [ ] 10. Docs sync
  - [ ] 10-1. `architecture.md`: add `ChatStore`, chat persistence directory, chat endpoints to API table, execution-from-chat flow
  - [ ] 10-2. `llm-api-guide.md`: document chat thread API for programmatic access
  - [ ] 10-3. `README.md`: add conversational workflow authoring to feature summary
  - [ ] 10-4. `changelog.md`: implementation entry

## Decisions

- (to be filled during execution: run intent detection approach, execution block rendering, thread storage format)

## Notes

- Chat history persistence follows the same filesystem pattern as `GraphStore`. JSON files in a `chats/` directory, one file per thread. Simple, no database dependency, version-controllable.
- Run-from-chat blurs the line between design-time and run-time. The chat becomes a unified interface for both building and testing workflows. This is a major UX differentiator.
- Error diagnosis from chat is powerful but requires the LLM to understand error messages and map them to graph fixes. The system prompt needs to cover common error patterns (schema mismatch, missing ports, disconnected nodes).
- The `/run` command syntax is intentionally simple. Natural language run intent ("test this") is handled by the LLM interpreting the message. Both paths converge on the same `RunManager` infrastructure.
- Thread timeline view (stretch) would make the conversation a visual history of graph evolution. Powerful for understanding how a workflow was iteratively built. Defer to post-MVP.
