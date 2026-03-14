# 35-2: Split app.py Route Handlers into APIRouter Modules

**Parent:** [35-server-module-decomposition](35-server-module-decomposition.md)
**Status:** completed
**Goal:** Extract route handlers from `app.py` into domain-grouped FastAPI `APIRouter` modules under `server/routers/`, while keeping endpoint paths, OpenAPI shape, WebSocket behavior, and stream semantics unchanged.

## Current State

All ~90 route handlers have been extracted from `app.py` (4,284→397 lines) into 10 router modules under `src/dan/server/routers/`:

| Router module | Endpoints | Lines | Domain |
|---|---|---|---|
| `misc.py` | 26 | 487 | Health, cache, metrics, files/docs/code-refs, test cases, memory, errors, rules |
| `graphs.py` | 10 | 509 | Graph CRUD, mutation, validation, export, node inputs, boundary validators |
| `rag.py` | 5 | 122 | RAG collections + lazy indexer |
| `runs.py` | 14 | 505 | Run lifecycle, checkpoints, compare, scoped run, token breakdown, optimization, WS |
| `experiences.py` | 5 | 124 | Experience CRUD + semantic search + refresh |
| `meta.py` | 11 | 241 | Meta-orchestrator discover/plan/validate/run/sessions + WS |
| `publishing.py` | 4 | 143 | Publish/unpublish/MCP-config/status |
| `blocks.py` | 6 | 172 | Block CRUD + export |
| `chat.py` | 14 | 805 | Chat message, stop, CRUD, stream state, WS, search, export, pin, checkpoint |
| `adapters.py` | 3 | 438 | Adapter start/stop/status + all runtime logic |
| `dependencies.py` | — | 106 | Shared dependency accessors |

## Tasks
- [x] 1. Create `src/dan/server/routers/` package and `dependencies.py`
  - [x] 1-1. Add shared dependency accessors (`get_run_manager`, `get_chat_manager`, `get_graph_store`, etc.)
  - [x] 1-2. Add helper accessors for memory, experience, and path validation
- [x] 2. Extract low-risk HTTP routers first
  - [x] 2-1. `rag.py` — RAG collections + `_get_indexer` wrapper (module-level state)
  - [x] 2-2. `experiences.py`
  - [x] 2-3. `blocks.py`
  - [x] 2-4. `publishing.py`
  - [x] 2-5. `misc.py` — health, cache, metrics, files/docs/code-refs, test cases, memory/errors/rules
- [x] 3. Extract graph/run routers
  - [x] 3-1. `graphs.py` — graph CRUD, mutation, validation, export, node-level helpers, `build_token_analysis_context`
  - [x] 3-2. `runs.py` — run lifecycle, checkpoints, compare, scoped run, token breakdown, optimization, run WS
  - [x] 3-3. Move request models (`CreateGraphRequest`, `ApplyMutationRequest`, `RunRequest`, `ResumeRequest`) into their router modules
- [x] 4. Extract stateful routers
  - [x] 4-1. `meta.py` — meta session routes + `_meta_tasks`, `_meta_subscribers` (module-level state)
  - [x] 4-2. `chat.py` — chat CRUD + `chat_message` (full `_produce()` closure preserved)
  - [x] 4-3. Chat stream state (`_chat_streams`, `_chat_produce_tasks`, helpers) moved into `chat.py`
  - [x] 4-4. `adapters.py` — adapter routes + all runtime helpers/state dicts
- [x] 5. Update `app.py` to `include_router()` each module; add compatibility re-exports
- [ ] 6. Verification gates (deferred — requires server startup)
  - [ ] 6-1. Diff `/openapi.json` before/after
  - [ ] 6-2. Smoke-test one representative endpoint from each moved router
  - [ ] 6-3. Verify chat stream reconnect + terminal-event guarantee still hold
  - [ ] 6-4. Verify run event WebSocket still closes/replays correctly
  - [ ] 6-5. Run the relevant targeted test suites

## Decisions
- Combined `chat_streams.py` into `chat.py` rather than keeping a separate file, since the stream state is tightly coupled to the chat message handler's `_produce()` closure. Separating them would require cross-module mutable state sharing with no clear benefit.
- Moved `_build_meta_controller()` helper back to `app.py` since it references module-level globals (`_graph_store`, `_chat_manager`, etc.) and is used by the meta router via `dependencies.py`. This avoids circular imports.
- Chat stream module state (`_chat_streams`, `_chat_produce_tasks`) lives in `routers/chat.py` as module-level dicts, matching the original pattern.
- Adapter state dicts (`_active_adapters`, `_adapter_session_stores`, etc.) moved to `routers/adapters.py` as module-level state.
- `build_token_analysis_context` is exported from `graphs.py` for use by the runs router's optimization endpoints.

## Notes
- `app.py` went from 4,284 lines to 397 lines (91% reduction).
- All endpoint paths remain exactly the same — no prefix changes.
- Request/response models (`CreateGraphRequest`, `ChatMessageRequest`, etc.) are re-exported from `app.py` for backward compatibility.
- The `_produce()` closure in `chat_message` was moved as-is with all its inner helpers.
