# 12-5: Conversation Lifecycle

**Parent:** [12-cursor-parity-chat](12-cursor-parity-chat.md)
**Status:** in-progress
**Goal:** Add conversation management primitives that modern chat UX demands: stop generation, message queuing, checkpoints, export, search, and thread operations.

## Current State

- ~~No stop button (must wait for LLM to finish or close the tab)~~ → DONE
- Cannot send follow-up messages while LLM is generating (deferred — task 2)
- ~~No conversation checkpoints (session-scoped undo only)~~ → basic auto-checkpoint DONE
- ~~No export (can't share or archive a conversation)~~ → DONE
- ~~No cross-thread search~~ → DONE
- No explicit branch metadata/visual lineage yet (stretch — task 7 follow-ups)

## Tasks

- [x] 1. Stop generation
  - [x] 1-1. Frontend: "Stop" button (red square) replaces "Send" while streaming
  - [x] 1-2. Backend: `POST /api/chat/{channel_id}/stop` cancels a specific active stream by channel_id
  - [x] 1-3. `chat_manager.py`: per-stream cancellation map (`channel_id -> asyncio.Event`); `register_stream`, `cancel_stream`, `unregister_stream` methods; provider stream loops check the event
  - [x] 1-4. Partial response preserved as-is with `*[generation stopped]*` suffix
  - [x] 1-5. WebSocket: emit `chat_interrupted` event with partial content; added `ChatInterruptedEvent` model
- [ ] 2. Message queuing (deferred — complex, see Notes)
  - [ ] 2-1. Frontend: allow typing and sending while LLM is generating (queue in local state) *(deferred — complex interaction with mutation context)*
  - [ ] 2-2. Queued messages shown in chat with "pending" indicator *(deferred — depends on 2-1)*
  - [ ] 2-3. After current response completes, next queued message auto-sends *(deferred — depends on 2-1)*
  - [ ] 2-4. Queue reordering: user can delete queued messages before they send *(deferred — depends on 2-1)*
  - [ ] 2-5. Backend: sequential processing (no parallel LLM calls per thread) *(deferred — depends on 2-1)*
- [x] 3. Conversation checkpoints (basic)
  - [x] 3-1. Auto-checkpoint: save graph state snapshot after each mutation apply (`save_checkpoint` in `chat_store.py`, `POST /api/chats/{wf}/{tid}/checkpoint` endpoint, called from `handleApplyMutation`)
  - [ ] 3-2. Manual checkpoint: "Save checkpoint" button in chat header → named snapshot *(deferred — auto-checkpoint is the MVP)*
  - [ ] 3-3. Checkpoint list: show in thread sidebar with timestamp, name, and graph thumbnail *(deferred — restore UI not yet needed)*
  - [ ] 3-4. Restore checkpoint: reverts graph and truncates chat history to that point *(deferred — restore UI not yet needed)*
  - [x] 3-5. Persist checkpoints to disk alongside thread data (`checkpoints/` subdirectory)
- [x] 4. Export conversation
  - [x] 4-1. Export as Markdown: user messages, assistant messages, mutation summaries, timestamps
  - [x] 4-2. Export as JSON: full thread data via `export_thread_json`
  - [x] 4-3. Copy single message: "Copy as Markdown" button on each message bubble
  - [x] 4-4. Frontend: export button (Download icon) in thread header → file download
- [x] 5. Cross-thread search
  - [x] 5-1. `chat_store.py`: `search_threads(query, workflow_id=None)` → case-insensitive substring search across messages
  - [ ] 5-2. Search index: simple in-memory inverted index built on thread load (scale later if needed) — deferred; substring search is sufficient for now
  - [x] 5-3. Frontend: search bar in thread list → results with message preview
  - [x] 5-4. Click result → opens thread (scroll-to-message deferred)
- [x] 6. Thread operations
  - [x] 6-1. Rename thread: click title to edit inline (pre-existing)
  - [x] 6-2. Delete thread: confirm dialog (pre-existing)
  - [x] 6-3. Pin thread: pinned threads stay at top of list (metadata stored in `.meta.json` sidecar)
  - [ ] 6-4. Archive thread: move to "Archived" section, excluded from search by default (deferred)
- [ ] 7. Thread branching (stretch — deferred)
  - [x] 7-1. "Branch from here": creates new thread with history up to selected message *(landed in [12-7](12-7-chat-branching-tree.md) via branch-based edit/resend + regenerate)*
  - [ ] 7-2. Branch indicator: show which thread was branched from *(deferred — stretch goal)*
  - [ ] 7-3. Visual: branching icon in thread list, parent-child relationship *(deferred — stretch goal)*
  - [x] 7-4. User turn action: edit a past message and resend it into a new branched thread *(landed in [12-7](12-7-chat-branching-tree.md))*
  - [x] 7-5. Assistant/result action: regenerate from the preceding user turn into a new branched thread *(landed in [12-7](12-7-chat-branching-tree.md))*

## Decisions

- **Stop mechanism:** Per-channel `asyncio.Event` stored on `ChatManager._cancel_events`. The `cancel_event` is passed through `send_message`, `send_message_with_tools`, and `_stream_with_json_fallback`. Checked once per streaming chunk — minimal latency.
- **Checkpoint storage:** Sidecar `checkpoints/` directory under `chats/{workflow_id}/`. Each checkpoint is a JSON file named `{thread_id}_{message_id}_{timestamp}.json` containing the full graph snapshot.
- **Pin storage:** Thread metadata (including `pinned` flag) stored in `.meta.json` sidecar files alongside thread JSON. This avoids modifying the `ChatThread` Pydantic model.
- **Search:** Simple substring search (no inverted index). Sufficient for current scale. Can be upgraded later.
- **Export format:** Markdown uses `### Role` headings with timestamps. JSON exports the full `ChatThread` model dump.

## Notes

- Stop generation (task 1) and export (task 4) are the highest-impact, lowest-effort items.
- Message queuing (task 2) is surprisingly complex because queued messages might reference context that changes after the current response applies mutations. The simplest approach: re-resolve mentions when a queued message actually sends. **Deferred.**
- Thread branching (task 7) is a stretch goal. **Deferred.**
- 2026-03-16 follow-up: v1 branch-based turn rewriting now exists via [12-7](12-7-chat-branching-tree.md), but explicit parent/child branch metadata and lineage UI remain deferred.
- Checkpoints (task 3) extend the existing `pushSnapshot` mechanism from in-memory to persistent. Only auto-checkpoint (3-1) and persistence (3-5) implemented; restore UI deferred.
- Search scroll-to-message (5-4) and inverted index (5-2) deferred — substring search with thread-open is the MVP.
