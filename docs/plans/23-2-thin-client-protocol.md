# 23-2: Thin Client Protocol

**Parent:** [23-multi-surface-gateway](23-multi-surface-gateway.md)
**Status:** completed
**Goal:** Create a shared `DanClient` library that CLI, messaging adapters, and MCP publish all use to communicate with `dan-serve`, providing a unified interface for workflow dispatch, event streaming, and HumanNode interaction.

## Context

Currently CLI (`cli/run.py`), adapters (`adapters/`), and MCP (`publish/`) each create their own Engine instance. After 23-1 adds gateway API endpoints, these surfaces need a shared client library to communicate with the server. DanClient abstracts the HTTP + WebSocket communication so each surface only implements its presentation layer.

## Tasks

- [x] 1. **DanClient class** (`src/dan/client/__init__.py`)
  - [x] 1-1. Create `src/dan/client/` package
  - [x] 1-2. `DanClient(base_url: str, api_key: str | None = None, timeout: float = 30.0)` — async HTTP client using httpx
  - [x] 1-3. Server discovery: if `base_url` not provided, check `DAN_SERVER_URL` env var, then try `http://localhost:8000`
  - [x] 1-4. Health check: `async ping() -> bool` — calls `GET /health`, returns True if server is reachable
  - [x] 1-5. `async is_server_available() -> bool` — ping with short timeout, for fallback detection
- [x] 2. **Dispatch methods**
  - [x] 2-1. `async dispatch(workflow_id=None, workflow_path=None, inputs=None, text=None, surface_id=None, use_meta: bool = False, config_overrides: dict | None = None, human_timeout: int | None = None) -> DispatchResult` — calls `POST /api/gateway/dispatch`. All optional params map 1:1 to `DispatchRequest` fields.
  - [x] 2-2. `DispatchResult` model: `run_id`, `workflow_name`, `status`, `surface_id`
  - [x] 2-3. `async get_run_status(run_id: str) -> RunStatus` — calls existing `GET /api/runs/{id}`
  - [x] 2-4. `async list_runs() -> list[RunSummary]` — calls existing `GET /api/runs`
  - [x] 2-5. `async get_run_events(run_id: str) -> list[EngineEvent]` — calls `GET /api/runs/{id}/events` (REST mode), returns historical events for completed/in-progress runs
  - [x] 2-6. `async cancel_run(run_id: str) -> bool` — calls `POST /api/gateway/cancel`, returns `True` if cancelled, `False` if run not found or already completed (server returns `{run_id, cancelled: bool}`, client extracts the bool)
  - [x] 2-7. `async resume_run(run_id: str, graph_id: str | None = None) -> DispatchResult` — calls existing `POST /api/runs/{id}/resume`
  - [x] 2-8. `async get_checkpoints(run_id: str) -> list[CheckpointInfo]` — calls existing `GET /api/runs/{id}/checkpoints`
- [x] 3. **Event streaming**
  - [x] 3-1. `async subscribe_run(run_id: str) -> AsyncIterator[EngineEvent]` — WebSocket connection to existing `/api/runs/{id}/events`, yields parsed events
  - [x] 3-2. `async subscribe_global(surface_filter=None, workflow_filter=None) -> AsyncIterator[EngineEvent]` — WebSocket to new `/api/gateway/events`
  - [x] 3-3. Automatic reconnection with exponential backoff on WebSocket disconnect
  - [ ] 3-4. Event deserialization: raw JSON → typed EngineEvent models. Add `EngineEvent.from_dict()` class method to `dan.engine.events` (inverse of existing `to_dict()`) — reused by all surfaces
- [x] 4. **HumanNode interaction**
  - [x] 4-1. `async get_pending_inputs() -> list[PendingInput]` — calls `GET /api/gateway/pending-inputs`
  - [x] 4-2. `async submit_human_input(run_id: str, request_id: str, response: dict, responder_surface: str | None = None) -> bool` — calls `POST /api/gateway/submit-input`
  - [x] 4-3. `PendingInput` model: `run_id`, `request_id`, `node_id`, `prompt`, `schema`, `render_mode`, `surface_origin`, `waiting_since`
- [x] 5. **Activity and surface registration**
  - [x] 5-1. `async get_activity() -> ActivitySnapshot` — calls `GET /api/gateway/activity`
  - [x] 5-2. `async register_surface(surface_id: str, surface_type: str) -> None` — register on the global event bus
  - [x] 5-3. `ActivitySnapshot` model: `active` runs, `recent` runs, `connected_surfaces`
- [x] 6. **Fallback mode**
  - [x] 6-1. `DanClientOrLocal` wrapper: tries DanClient first; if server unreachable, falls back to direct Engine execution. Constructor accepts local-mode dependencies: `engine_config: EngineConfig | None`, `human_renderer: HumanRenderer | None`, `tool_registry: ToolRegistry | None`, `workflow_loader: Callable | None`
  - [x] 6-2. Unified interface: same method signatures regardless of mode (server vs local). Local `dispatch()` loads the graph via `workflow_loader`, creates Engine with `engine_config` + `human_renderer`, and runs it.
  - [x] 6-3. Mode detection: check server availability on first call, cache result (with periodic re-check every 60s)
  - [x] 6-4. `LocalEventStream`: wraps Engine's fire-and-forget async callback into `AsyncIterator[EngineEvent]` using an `asyncio.Queue` bridge. This is non-trivial — the engine callback is `async def cb(event)` but DanClient returns `AsyncIterator`.
  - [x] 6-5. When in local mode, events come from `LocalEventStream`; HumanNode uses the surface's own renderer passed to the constructor
  - [ ] 6-6. MetaController in local mode: when `use_meta=True` and in local fallback, `DanClientOrLocal` creates MetaController directly (same as CLI's `_run_nl_goal()`), wires approval events through the local HumanRenderer. The surface-provided `human_renderer` handles approval prompts.
- [ ] 7. **Error handling**
  - [x] 7-1. Define `DanClientError` hierarchy: `ConnectionError`, `DispatchError` (4xx), `NotFoundError` (404), `ServerError` (5xx), `RunLostError`
  - [ ] 7-2. HTTP retry policy: retry 5xx with exponential backoff (max 3 retries); never retry 4xx
  - [ ] 7-3. Fallback trigger: on `ConnectionError`, `DanClientOrLocal` switches to local mode
  - [ ] 7-4. WebSocket reconnect gap recovery: on reconnect, fetch missed events via `GET /api/runs/{id}/events` with `last_event_timestamp`, then resume streaming
- [ ] 8. **Tests**
  - [x] 8-1. Unit tests for DanClient with mocked HTTP responses
  - [ ] 8-2. Unit tests for WebSocket event streaming with mock server
  - [x] 8-3. Unit tests for fallback mode: server available → client mode, server down → local mode
  - [ ] 8-4. Unit tests for `register_surface()` and `get_activity()` with mocked server responses
  - [ ] 8-5. Integration test: real dan-serve + DanClient → dispatch, subscribe, receive events

## Decisions

- httpx for HTTP (already a core dependency), standard websockets for WS (already a core dependency)
- DanClient is async-only (all surfaces already use async)
- Fallback to direct engine is essential for backward compatibility — users who don't run dan-serve should not be broken
- No new dependencies required — httpx and websockets are already in pyproject.toml
- DanClient lives in `src/dan/client/` (new package), not inside `src/dan/cli/` — it's shared across all surfaces
- `EngineEvent.from_dict()` is added to the engine module (not the client) — it's a general-purpose utility that benefits any consumer of serialized events
- `DanClientOrLocal` is the primary consumer interface — surfaces should use it instead of `DanClient` directly, so fallback is always available
- Error types are defined in `dan.client.errors` — surfaces catch specific exceptions rather than raw HTTP status codes
- `RunLostError` is a specific error for mid-run server loss — raised when a subscribed run's WebSocket disconnects and reconnection fails after max retries. Surfaces should catch this and display: "Server connection lost. Run {run_id} was executing on the server and cannot be recovered locally."
- **Event durability contract:** events are NOT persisted by the client. During WebSocket disconnect, events emitted by the server are buffered server-side (bounded queue, drop-oldest). On reconnect, gap recovery via `GET /api/runs/{id}/events?since=<timestamp>` fetches missed events. Events dropped from bounded queues are permanently lost — surfaces should display a "some events may have been missed" warning if gap recovery detects discontinuity.

## Notes

- DanClient is the single integration point — CLI, adapters, and MCP all import from `dan.client`
- The fallback mode means existing workflows continue to work even without the server
- Event type reuse: DanClient returns the same EngineEvent types that the engine itself emits, so presentation layers don't need to distinguish between server and local modes
- `GET /api/runs` returns `{runs: [...], total: int}` (wrapped response) — `DanClient.list_runs()` unwraps this to return `list[RunSummary]` directly
- Events should carry a monotonic `sequence_id` (integer, per-run) for deduplication during gap recovery — if a reconnected client receives events it already has, it can skip duplicates by comparing sequence IDs
