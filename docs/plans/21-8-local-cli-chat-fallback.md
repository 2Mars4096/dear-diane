# 21-8: Local CLI Chat Fallback

**Parent:** [21-author-distribute](21-author-distribute.md)
**Status:** not-started
**Goal:** Allow `dan-chat` to work without a server by running ChatManager locally — full CLI chat parity when dan-serve is not running.

## Context

21-6 (CLI Chat Mode) requires a running server. Users who prefer headless or offline workflows cannot use `dan-chat` without starting `dan-serve`. This plan adds a local fallback: when the server is unavailable, `dan-chat` instantiates ChatManager, graph store, and chat store locally and runs the chat loop in-process.

## Tasks

- [ ] 1. **Local chat runtime**
  - [ ] 1-1. Extract or reuse `ChatManager` initialization from `server/app.py` lifespan
  - [ ] 1-2. Local base dir: `~/.dan/dan-chat/` (or `{workspace}/.dan/dan-chat/` when `--workspace` set). GraphStore and ChatStore share this base; graphs at `{base}/*.json`, chats at `{base}/chats/{workflow_id}/`
  - [ ] 1-3. Bootstrap scratch: ensure `_scratch` empty graph exists on first use (same as 21-6 server bootstrap)
  - [ ] 1-4. `LocalChatRuntime` class: `send_message()`, `stream_events()` — same interface as HTTP+WS client

- [ ] 2. **dan-chat fallback detection**
  - [ ] 2-1. On startup: if server unavailable, switch to `LocalChatRuntime` instead of exiting
  - [ ] 2-2. Print "Running in local mode (no server)" so user knows the mode
  - [ ] 2-3. `--local` flag: force local mode even if server is available

- [ ] 3. **Mutation apply (local)**
  - [ ] 3-1. Local apply: `GraphMutator().apply()` in-process, update graph store
  - [ ] 3-2. No HTTP apply-mutation call when in local mode

- [ ] 4. **Run from chat (local)**
  - [ ] 4-1. `/run` in local mode: instantiate `Engine`, run graph directly, stream events to terminal
  - [ ] 4-2. Reuse `DanClientOrLocal`-style pattern for execution

- [ ] 5. **Tests**
  - [ ] 5-1. Unit tests for LocalChatRuntime
  - [ ] 5-2. Integration test: dan-chat local mode, message → mutation → apply → run
  - [ ] 5-3. Lazy imports: avoid loading ChatManager/graph_store when using server mode (reduce startup cost)

## Decisions

- Heavier dependency: local mode pulls in ChatManager, graph store, chat store, LLM providers, Engine. Acceptable for full parity.
- Defer if 21-6 server-mode usage is sufficient — this is optional/stretch.

## Files to Touch

| File | Changes |
|------|---------|
| `src/dan/cli/chat.py` | Add `LocalChatRuntime`, fallback detection, `--local` flag |
| `src/dan/cli/chat_runtime.py` | New: `LocalChatRuntime` (or colocate in chat.py) |
| `src/dan/server/chat_manager.py` | May need extractable init for reuse |

## Notes

- Depends on 21-6 (CLI Chat Mode) — local fallback extends the same REPL.
- Storage layout matches server: GraphStore `{base}/*.json`, ChatStore `{base}/chats/{workflow_id}/` (ChatStore uses `base_dir` for both).
