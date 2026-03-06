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
  - [x] 4-3. Human input handling during `/run` — prompt user, submit via `POST /api/runs/{id}/human-input`, approval + text modes
  - [x] 4-4. `human_input_needed` and `run_cancelled` added to `_CHAT_EVENT_TYPES` + `map_run_event_to_chat_block`
  - [x] 4-5. `_pipe_run_events` breaks on `run_cancelled` (was 300s hang)
  - [x] 4-6. REPL breaks event loop on terminal run states (`run_completed`, `run_failed`, `run_cancelled`)

- [x] 5. **Graph inspection**
  - [x] 5-1. `/show` command: `GET /api/graphs/{id}` → formatted summary (nodes, edges, types, labels)
  - [x] 5-2. `ChatClient.get_graph()` method

- [x] 6. **Robustness**
  - [x] 6-1. History bounded at 40 messages to prevent context overflow
  - [x] 6-2. `ChatClient.submit_human_input()` and `ChatClient.cancel_run()` methods
  - [x] 6-3. Clean Rich import (removed unused `import rich`)

- [x] 7. **Tests**
  - [x] 7-1. Unit tests for CLI argument parsing
  - [x] 7-2. Integration test with mocked server (message send + stream handling)
  - [x] 7-3. Unit test for scratch bootstrap: `workflow_id="_scratch"` with missing graph → empty graph created
  - [x] 7-4. Tests for `get_graph`, `submit_human_input`, `cancel_run` client methods
  - [x] 7-5. Tests for `_format_graph_summary` (empty, populated, truncation)
  - [x] 7-6. Tests for `map_run_event_to_chat_block` with `human_input_needed` and `run_cancelled`

## Decisions

- No local fallback for MVP — `dan-chat` requires a running server. Local fallback is 21-8.
- Reuse existing chat API — no new server endpoints; only thin client + REPL.
- Scratch workflow: `workflow_id="_scratch"` with server-side bootstrap of empty graph on first use.
- Human input during `/run` mirrors `dan-run` implementation (approval + text modes, timeout, cancel).
- History bounded at 40 messages; trimmed from oldest on overflow.
- Build-mode mutation generation no longer force-coerces all `add_edge` ops to `strict=True`; generated semantic target ports are allowed to auto-create during apply/dry-run.
- Chat manager runs bounded multi-attempt dry-run repair before surfacing mutation (`DAN_MUTATION_AUTO_RETRY_MAX`).
- CLI does not prompt apply when mutation dry-run failed; user is prompted to request repair instead.
- Chat manager normalizes a small set of high-frequency LLM schema drifts before dry-run (`parallel_subagents` branch fields and validator `required_field(s)` rule aliases) to reduce repeated parse failures.

## Files to Touch

| File | Changes |
|------|---------|
| `src/dan/cli/chat.py` | `dan-chat` REPL, ChatClient (ping, send, stream, apply, get_graph, submit_human_input, cancel_run), `/show`, human input handler, history bounding |
| `src/dan/server/app.py` | Scratch bootstrap in `chat_message` handler; `_pipe_run_events` `run_cancelled` break |
| `src/dan/server/scoped_run.py` | `_CHAT_EVENT_TYPES` + `map_run_event_to_chat_block` for `human_input_needed`, `run_cancelled` |
| `src/dan/server/gateway/router.py` | `_run_after_approval` `start_run` exception handling |
| `src/dan/server/chat_manager.py` | Chat-plan normalization, strict-edge coercion, mutation auto-repair |
| `src/dan/server/graph_mutator.py` | Node alias tracking, explicit ID collision handling |
| `src/dan/executors/input.py` | Aggregate input payload for generated ports |
| `pyproject.toml` | `dan-chat` entry point |
| `tests/test_cli/test_chat.py` | unit + integration tests |

## Patch: Workflow Management Commands

**Goal:** Allow users to list, open, save-as, create, and rename workflows entirely from inside the `dan-chat` REPL — no restarts, no CLI flags.

- [x] 8. **ChatClient API methods**
  - [x] 8-1. `list_graphs()` — `GET /api/graphs`, returns list of `{graph_id, name, description, updated_at}`
  - [x] 8-2. `create_graph(graph_id, data=None)` — `POST /api/graphs`
  - [x] 8-3. `save_graph(graph_id, data)` — `PUT /api/graphs/{id}`

- [x] 9. **REPL commands**
  - [x] 9-1. `/list` — display all saved workflows (table: ID, name, updated)
  - [x] 9-2. `/open <id>` — switch session to an existing workflow (fetch graph, update workflow_id/revision, reset history, auto-switch mode to `mutate` if non-scratch)
  - [x] 9-3. `/saveas <name>` — copy current graph to new ID `<name>`, switch session to it
  - [x] 9-4. `/new [name]` — create empty graph (auto-name if omitted), switch to it, set mode to `build`
  - [x] 9-5. `/rename <name>` — update current workflow's `metadata.name` field via PUT
  - [x] 9-6. `/save [name]` — primary save action: on `_scratch` prompts for name + creates permanent copy; on named workflows confirms auto-persistence
  - [x] 9-7. `/exit` save prompt — nudge save when leaving `_scratch` with a non-empty graph

- [x] 10. **UX polish**
  - [x] 10-1. Dynamic header — print current workflow/mode after every switch
  - [x] 10-2. Updated `/help` text listing all commands
  - [x] 10-3. `/show` now shows workflow_id alongside metadata name

- [x] 11. **Tests**
  - [x] 11-1. `TestChatClientListGraphs` — success and empty
  - [x] 11-2. `TestChatClientCreateGraph` — success and conflict (409)
  - [x] 11-3. `TestChatClientSaveGraph` — success and error
  - [x] 11-4. `TestFormatWorkflowList` — empty, with marker, no marker

## Notes

- Depends on server being running for `dan-serve`; chat API requires `ChatManager` and graph store.
- 21-7 (gateway text dispatch) unblocks `dan-run "goal"` with server; 21-6 unblocks conversational CLI chat.
- Server already has full graph CRUD: `GET /api/graphs`, `POST /api/graphs`, `GET /api/graphs/{id}`, `PUT /api/graphs/{id}`, `DELETE /api/graphs/{id}` — no new server endpoints needed.
