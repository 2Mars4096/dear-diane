# 3: Phase 2 Visual Editor

**Status:** completed
**Goal:** Build a local full-stack visual editor (FastAPI backend + React Flow frontend) for authoring, executing, and monitoring DAN graphs with full streaming execution visibility.

## Tasks
- [x] 1. Tracking docs bootstrap
  - [x] 1-1. Create this plan file and link from `docs/todo.md`
- [x] 2. Engine event instrumentation
  - [x] 2-1. Define typed event model (`engine/events.py`)
  - [x] 2-2. Add optional `event_callback` to `Engine` constructor
  - [x] 2-3. Emit events from `_execute_node` and `_execute`
- [x] 3. Async run manager
  - [x] 3-1. Create `src/dan/server/run_manager.py` — background task execution
  - [x] 3-2. Implement event pubsub with catch-up snapshots on reconnect
  - [x] 3-3. Track active/completed runs with status snapshots
- [x] 4. Backend service layer (FastAPI)
  - [x] 4-1. Add `fastapi`, `uvicorn`, `websockets` dependencies to `pyproject.toml`
  - [x] 4-2. Create `src/dan/server/` package with `app.py`, `graph_store.py`
  - [x] 4-3. Implement graph CRUD endpoints (list, create, get, update, delete)
  - [x] 4-4. Implement run endpoints (`POST /api/runs`, `POST /api/runs/{runId}/resume`, `GET /api/runs/{runId}`)
  - [x] 4-5. Implement WebSocket endpoint (`WS /api/runs/{runId}/events`)
  - [x] 4-6. CLI entry point (`dan-serve` / `python -m dan.server`)
- [x] 5. Frontend editor scaffold
  - [x] 5-1. Scaffold `editor/` with Vite + React + TypeScript + React Flow + Zustand + Tailwind
  - [x] 5-2. Define TypeScript types mirroring `dan_graph_v1` models
  - [x] 5-3. Build `graphAdapter.ts` — bidirectional DAN <-> React Flow conversion
  - [x] 5-4. Implement `GraphCanvas` — node placement, edge wiring, selection, drag-and-drop
  - [x] 5-5. Implement `NodePalette` — draggable node creation for all 10 node types
  - [x] 5-6. Implement `ConfigPanel` — form-based node/edge property editing
  - [x] 5-7. Auto-load last opened graph; graph switcher menu
- [x] 6. Frontend run UX
  - [x] 6-1. Implement `RunPanel` — start/resume/disconnect controls + status badge
  - [x] 6-2. Live node status coloring via WebSocket events (status rings on DanNode)
  - [x] 6-3. Per-node output preview panel (`OutputPreview`)
  - [x] 6-4. Scrolling log timeline panel (`LogPanel`)
- [x] 7. Composite read-only preview
  - [x] 7-1. Detect `body_graph`-backed nodes (while_loop, for_each, composite)
  - [x] 7-2. Render referenced sub-graph in modal (read-only, `CompositePreview`)
- [x] 8. Tests and docs sync
  - [x] 8-1. Backend integration tests (API + run manager + events — 27 new tests, 167 total)
  - [x] 8-2. Frontend type check passes (tsc --noEmit), Vite production build succeeds
  - [x] 8-3. Update `docs/changelog.md`, `docs/architecture.md`, `docs/todo.md`

## Decisions
- Full-stack local architecture: FastAPI (Python) + React Flow (TypeScript). Like Jupyter — `dan-serve` opens browser.
- Full streaming execution visibility: live node status, output preview, log panel.
- Composite nodes get read-only sub-graph preview; nested editing deferred to Phase 4.
- Engine event instrumentation is opt-in callback — no breaking changes to existing `Engine.run()`.
- Graph persistence: filesystem-based JSON in `./graphs/` directory.
- Editor lives in `editor/` at project root; backend extends `src/dan/server/`.
- Frontend state management: Zustand.
- DAN <-> React Flow bidirectional adapter as explicit conversion module (`graphAdapter.ts`).
- Auto-load last opened graph on editor open; graph switcher menu for navigation.
- WebSocket reconnection: full catch-up snapshot (current node statuses + buffered recent events).
- Execution order: engine events -> run manager -> backend API -> frontend scaffold -> run UX -> composite preview.

## Notes
- `Object.groupBy` in NodePalette requires ES2024 — works in modern browsers, may need polyfill for older targets.
- `assert _run_manager` pattern replaced with `_require_run_manager()` returning HTTP 503 for production safety.
- Vite warns about Node.js version (needs 20.19+) but builds successfully on 20.17.
