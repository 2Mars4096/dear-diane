# 35-2: Split app.py Route Handlers into APIRouter Modules

**Parent:** [35-server-module-decomposition](35-server-module-decomposition.md)
**Status:** not-started
**Goal:** Extract route handlers from `app.py` into domain-grouped FastAPI `APIRouter` modules under `server/routers/`, while keeping endpoint paths, OpenAPI shape, WebSocket behavior, and stream semantics unchanged.

## Current State

`app.py` lines 2076–5311 contain ~90 `@app.get/post/put/delete/websocket` handlers spanning 12+ unrelated API domains:

| Domain | Endpoints | Approx. lines |
|---|---|---|
| Graphs CRUD + mutation + validation + export | 10 | ~400 |
| Runs (start, resume, rerun, compare, checkpoints, events, human-input, scoped) | 12 | ~500 |
| Chat (message, stop, CRUD, search, export, pin, checkpoint, WebSocket) | 12 | ~600 |
| RAG collections | 5 | ~60 |
| Meta (discover, plan, validate, run, sessions) | 10 | ~200 |
| Experiences | 5 | ~120 |
| Blocks (list, get, import, export, delete) | 5 | ~150 |
| Publishing | 4 | ~120 |
| Adapters (start, stop, status + message routing) | 3 + ~400 internal | ~500 |
| Test cases | 4 | ~150 |
| Memory / Errors / Rules | 9 | ~200 |
| Misc (health, cache, metrics, token breakdown, optimization, files, docs, code-refs) | 11 | ~350 |

## Safety Rules

- This sub-plan is **blocked on Plan 34 integration stabilizing**. Do not land it in parallel with `34-4`.
- Do not combine router extraction with the `AppState` migration from `35-5`.
- Preserve endpoint paths, tags, request/response models, and WebSocket close/terminal-event semantics.
- Extract in waves: low-state routers first, chat/adapters last.

## Tasks

- [ ] 1. Create `src/dan/server/routers/` package and `src/dan/server/dependencies.py`
  - [ ] 1-1. Add shared dependency accessors (`get_run_manager`, `get_chat_manager`, `get_graph_store`, etc.)
  - [ ] 1-2. Add any shared response/model helpers that multiple routers need
- [ ] 2. Extract low-risk HTTP routers first
  - [ ] 2-1. `rag.py` — RAG collections + `_get_indexer` wrapper
  - [ ] 2-2. `experiences.py`
  - [ ] 2-3. `blocks.py`
  - [ ] 2-4. `publishing.py`
  - [ ] 2-5. `misc.py` — health, cache, metrics, files/docs/code-refs, test cases, memory/errors/rules if they stay small
- [ ] 3. Extract graph/run routers second
  - [ ] 3-1. `graphs.py` — graph CRUD, mutation, validation, export, node-level helpers
  - [ ] 3-2. `runs.py` — run lifecycle, checkpoints, compare, logs, scoped run, run WS
  - [ ] 3-3. Move `CreateGraphRequest`, `ApplyMutationRequest`, `RunRequest`, `ResumeRequest` next to their router modules
- [ ] 4. Extract stateful routers last
  - [ ] 4-1. `meta.py` — meta session routes + `_meta_tasks`, `_meta_subscribers`
  - [ ] 4-2. `chat.py` — chat CRUD + `chat_message`
  - [ ] 4-3. `chat_streams.py` — chat stream state, stop endpoint, WebSocket handlers, reconnect helpers
  - [ ] 4-4. `adapters.py` — adapter routes + adapter runtime helpers/state
- [ ] 5. Update `app.py` to `include_router()` each module with stable prefixes/tags
- [ ] 6. Verification gates
  - [ ] 6-1. Diff `/openapi.json` before/after
  - [ ] 6-2. Smoke-test one representative endpoint from each moved router
  - [ ] 6-3. Verify chat stream reconnect + terminal-event guarantee still hold
  - [ ] 6-4. Verify run event WebSocket still closes/replays correctly
  - [ ] 6-5. Run the relevant targeted test suites

## Decisions

- (filled in during execution)

## Notes

- `chat_message` is the highest-risk route move. Treat it as its own PR, and do not combine it with the chat CRUD move if the diff becomes noisy.
- `chat_streams.py` is intentionally separate from `chat.py` so the transport/state machinery does not get mixed back into CRUD endpoints.
- Initial router extraction can still call existing helpers through compatibility imports. The deeper dependency cleanup happens in `35-5`.
- The scoped-run handler is large enough to warrant a helper module if it keeps growing during extraction.
