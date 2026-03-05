# 21-6: CLI Chat Mode

**Parent:** [21-author-distribute](21-author-distribute.md)
**Status:** completed
**Goal:** Add `dan-chat` REPL that talks to the server's chat API for conversational workflow authoring — CLI parity with the editor's ChatPanel (like Claude Code vs Claude Code extension).

## Context

Today the editor's ChatPanel provides multi-turn chat with the meta orchestrator: build-from-scratch, NL→graph mutations, diff preview, apply/reject, and scoped run. The CLI has only one-shot NL execution via `dan-run "goal"` (local MetaController). There is no way to have a conversational "talk to meta orchestrator and edit workflow nodes" experience from the terminal.

This plan adds `dan-chat` — a REPL that sends messages to `POST /api/chat/message` and streams responses via `WS /api/chat/{channel_id}/events`, reusing the existing chat infrastructure.

## Tasks

- [x] 1. **dan-chat command**
  - [x] 1-1. New entry point `dan-chat` in `pyproject.toml`
  - [x] 1-2. Flags: `dan-chat` (scratch), `dan-chat --workflow-id X`, `dan-chat --scratch`
  - [x] 1-3. Requires server — exit with clear message if server unavailable
  - [x] 1-4. REPL loop: read input, send to `POST /api/chat/message`, connect to `WS /api/chat/{channel_id}/events`, render streamed response
  - [x] 1-5. Commands: `/run`, `/exit`, `/help` (optional)

- [x] 2. **Chat client**
  - [x] 2-1. Add `ChatClient` in `src/dan/cli/chat.py`: `send_chat_message()`, `stream_chat_events()` (HTTP + WebSocket; standalone to avoid 21-7 merge conflicts)
  - [x] 2-2. Scratch workflow: `workflow_id="_scratch"` — server bootstraps empty graph on first message (task 2-4)
  - [x] 2-3. Default mode: `"build"` for build-from-scratch (per 10-9; uses `BUILD_FROM_INTENT_PROMPT`)
  - [x] 2-4. Server scratch bootstrap: in `chat_message` handler, when `workflow_id="_scratch"` and graph missing, create empty graph via `_graph_store.save_graph("_scratch", {"nodes": [], "edges": []})` before processing

- [x] 3. **Mutation confirmation**
  - [x] 3-1. On `chat_mutation` events, show diff summary in terminal
  - [x] 3-2. Prompt: `Apply mutation? [Y/n]`
  - [x] 3-3. If Y: `POST /api/graphs/{id}/apply-mutation` with mutation plan
  - [x] 3-4. Track `client_graph_revision` for follow-up messages

- [x] 4. **Run from chat**
  - [x] 4-1. `/run` (or `/run full`, `/run node X`) as chat message prefix — backend `parse_run_command` handles it via `_handle_run_command`
  - [x] 4-2. Stream run events to terminal (similar to dan-run TUI)

- [x] 5. **Tests**
  - [x] 5-1. Unit tests for CLI argument parsing
  - [x] 5-2. Integration test with mocked server (message send + stream handling)
  - [x] 5-3. Unit test for scratch bootstrap: `workflow_id="_scratch"` with missing graph → empty graph created

## Decisions

- No local fallback for MVP — `dan-chat` requires a running server. Local fallback is 21-8.
- Reuse existing chat API — no new server endpoints; only thin client + REPL.
- Scratch workflow: `workflow_id="_scratch"` with server-side bootstrap of empty graph on first use.

## Files to Touch

| File | Changes |
|------|---------|
| `src/dan/cli/chat.py` | New: `dan-chat` REPL, chat client usage |
| `src/dan/client/client.py` | Not extended (ChatClient in chat.py to avoid 21-7 merge conflicts) |
| `src/dan/server/app.py` | Scratch bootstrap in `chat_message` handler |
| `pyproject.toml` | Add `dan-chat` entry point |
| `tests/test_cli/test_chat.py` | New: 21 unit tests |

## Notes

- Depends on server being running for `dan-serve`; chat API requires `ChatManager` and graph store.
- 21-7 (gateway text dispatch) unblocks `dan-run "goal"` with server; 21-6 unblocks conversational CLI chat.
