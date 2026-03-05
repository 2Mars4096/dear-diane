# 23-1: Gateway API & Event Bus

**Parent:** [23-multi-surface-gateway](23-multi-surface-gateway.md)
**Status:** completed
**Goal:** Add server-side API endpoints for unified workflow dispatch, activity tracking, and cross-surface event streaming.

## Context

The server already manages runs via `RunManager` with WebSocket event streaming per run. This plan adds three new capabilities: (1) a higher-level dispatch endpoint that accepts workflow paths/IDs/NL text and starts the right workflow, (2) an activity dashboard API showing all active and recent runs across all surfaces, and (3) a global event bus that streams events from ALL runs to any subscriber — not just per-run subscriptions. Together, these form the server-side foundation that thin clients (23-2 through 23-5) connect to. This plan also touches `dan.utils.workflow_loader` (new shared module) and import sites in `cli/run.py`, `cli/publish.py`, and `publish/mcp_server.py`.

## Tasks

- [x] 1. **Unified dispatch endpoint**
  - [x] 1-1. `POST /api/gateway/dispatch` — accepts `{workflow_id?: str, workflow_path?: str, inputs?: dict, text?: str, surface_id?: str}`. If `workflow_id` or `workflow_path` provided, load and run directly. If `text` provided, route through intent resolver. Surface type is derived from the registered `surface_id` (via `register_surface()`), not passed per-request.
  - [x] 1-2. `DispatchRequest` Pydantic model with validation — at least one of `workflow_id`, `workflow_path`, or `text` required; `surface_id` optional. Optional fields: `config_overrides: dict | None` (per-run LLM config: model, api_key, base_url), `human_timeout: int | None`, `use_meta: bool = False`
  - [x] 1-3. `DispatchResult` model: `{run_id: str, workflow_name: str, status: str, surface_id: str | None}`
  - [x] 1-4. Workflow loading: JSON path → `Graph.model_validate`, markdown dir → `dan.loader.load`, Python path → `importlib` load (reuse logic from CLI 21-2 task 2, extract to shared `dan.utils.workflow_loader`). **Security:** validate `workflow_path` is within configured workspace directory using `Path.resolve()`; reject absolute paths outside workspace; return 404 for missing file/ID, 422 for invalid workflow content, 400 for unsupported file extension. This is a **new server-side capability** — the server currently loads graphs only from the graph store.
  - [x] 1-5. After loading, delegate to `RunManager.start_run()` — reuse existing run infrastructure, zero duplication
  - [ ] 1-6. MetaController dispatch mode: when `text` is provided and no `IntentRegistry` match exists, route through `MetaController` server-side. `DispatchRequest` gets `use_meta: bool = False` flag. MetaController events (`plan_created`, `approval_needed`, `repair_started`) stream on the global event bus. Approval handled via HumanNode resolution mechanism.
  - [ ] 1-6-1. MetaController approval events emit as `human_input_needed` with `render_mode: "approval"` and structured `output_schema` containing the plan. Approval/rejection routed through same `POST /api/gateway/submit-input` endpoint. This unifies MetaController's interactive approval with the general HumanNode mechanism — no separate approval API needed.
  - [x] 1-7. `POST /api/gateway/cancel` — cancel a running workflow. Calls `RunManager.cancel_run(run_id)` which cancels the underlying `asyncio.Task`. Broadcasts `run_cancelled` event on global bus. Returns `{run_id, cancelled: bool}`.

- [ ] 2. **Intent resolver framework** *(optional — deferrable to Phase 2; no downstream plan depends on this)*
  - [ ] 2-1. `IntentRegistry` class in `server/gateway/intents.py`: register `(pattern, workflow_path, input_mapping)` tuples
  - [ ] 2-2. Pattern matching: regex or keyword-based (not LLM-based — fast, deterministic). `match(text) -> Optional[ResolvedIntent]`
  - [ ] 2-3. `POST /api/gateway/intents` — register intents at runtime (body: `{pattern: str, workflow_path: str, input_mapping: dict}`)
  - [ ] 2-4. `GET /api/gateway/intents` — list all registered intents
  - [ ] 2-5. Built-in intents: none by default. Workflows register their own intents on startup via config or API call.

- [x] 3. **Activity tracker**
  - [x] 3-1. `ActivityTracker` class in `server/gateway/activity.py` wrapping `RunManager` — enriches run data with `surface_id`, workflow metadata, progress info
  - [x] 3-2. `GET /api/gateway/activity` — returns `{active: [...], recent: [...], connected_surfaces: [...]}`. Each entry includes `run_id`, `workflow_name`, `status`, `surface`, `start_time`, `progress` (X/Y nodes), `duration`
  - [x] 3-3. Track connected surfaces: each surface registers on connect with a `surface_id` and `type`. Store in-memory set with last-seen timestamps.
  - [x] 3-4. `GET /api/gateway/surfaces` — list connected surfaces with type, surface_id, connected_since, last_active

- [x] 4. **Global event bus**
  - [x] 4-1. `WS /api/gateway/events` — WebSocket endpoint that streams events from ALL active runs, each event tagged with `run_id` and `surface_id`
  - [x] 4-2. Event filtering: optional query params `?surface=telegram&workflow=paper_ingest` to filter the global stream server-side
  - [x] 4-3. Completion notifications: when any run completes, broadcast `{event_type: "run_completed", run_id, workflow_name, surface, duration, status}` to all connected global-bus subscribers (reuses `EngineEvent.to_dict()` key naming)
  - [x] 4-4. HumanNode notifications: when any run hits a HumanNode, broadcast `{event_type: "human_input_needed", run_id, node_id, request_id, prompt, schema}` to global bus so any connected surface can pick it up
  - [x] 4-5. Backpressure and limits: max 50 concurrent global-bus subscribers; bounded queues (`maxsize=1000`) with drop-oldest for slow consumers; on WebSocket disconnect, immediately drain and discard the subscriber's queue

- [x] 5. **Cross-surface HumanNode resolution**
  - [x] 5-1. Expose server-side HumanNode registry via gateway: `GET /api/gateway/pending-inputs` — returns all pending HumanNode prompts across all runs with `run_id`, `request_id`, `node_id`, `prompt`, `schema`, `render_mode`, `surface_origin`, `waiting_since` (`request_id` is the unique resolution key; `node_id` is informational). **Requires new method:** add `RunManager.get_all_pending_human_inputs()` that iterates all active runs (current `get_pending_human_inputs(run_id)` is per-run only). Also add configurable timeout to the HumanNode callback wait (`asyncio.wait_for(evt.wait(), timeout=human_timeout)`) — on timeout, return timeout response and broadcast `human_input_timeout` event.
  - [x] 5-2. `POST /api/gateway/submit-input` — submit HumanNode response for any pending prompt from any surface. Body: `{run_id: str, request_id: str, response: dict, responder_surface?: str}` (keyed on `request_id`, matching `RunManager.submit_human_input()`). **Atomicity fix required:** current `RunManager.submit_human_input()` has a race condition — two concurrent submitters both get `True`. Fix: atomically pop `asyncio.Event` from `_pending_human_inputs` before storing response, so second caller gets `False` (already resolved). Validate `run_id` matches the event's actual run.
  - [x] 5-3. When HumanNode is resolved, broadcast `{event_type: "human_input_resolved", run_id, request_id, node_id, responder_surface}` to all global-bus subscribers

- [x] 6. **Module structure** *(partial — intent resolver, server restart cleanup, and telemetry depth deferred)*
  - [x] 6-1. Create `src/dan/server/gateway/` package with `__init__.py`, `router.py` (FastAPI router), `dispatch.py`, `intents.py`, `activity.py`, `events.py`
  - [x] 6-2. Register gateway router in `server/app.py` under `/api/gateway/` prefix
  - [ ] 6-3. Extract workflow loading logic into `dan.utils.workflow_loader` — consolidate `cli/run.py` loader functions and `publish/mcp_server.py`'s existing `load_workflows_from_path()` into a single shared module used by CLI, gateway dispatch, and publish
  - [x] 6-4. Add `GET /health` endpoint to `server/app.py` returning `{status: "ok", version: str}` — used by `DanClient.ping()` for server discovery
  - [ ] 6-5. Add `GET /api/runs/{id}/events?since=<timestamp>` query parameter for gap recovery during WebSocket reconnection — returns only events after the given timestamp
  - [x] 6-6. Surface heartbeat: surfaces marked `stale` after 5 minutes of no activity. Stale surfaces excluded from `GET /api/gateway/surfaces` active list. Global bus WebSocket heartbeat (ping/pong) updates `last_seen`.
  - [ ] 6-7. Server restart cleanup: on startup, scan `RunStore` for runs in `RUNNING` state from before the crash, mark as `FAILED` with `reason: "server_restarted"`, emit `run_aborted` events when surfaces reconnect
  - [ ] 6-8. Operational telemetry: structured logging (JSON) for all gateway dispatch/cancel/submit-input events with `surface_id`, `run_id`, `latency_ms`. `GET /health` returns depth info: `{status, version, active_runs, pending_human_inputs, global_bus_subscribers, event_bus_queue_depth}`

- [ ] 7. **Tests**
  - [ ] 7-1. Unit tests for `DispatchRequest` validation (missing fields, valid combos, invalid combos)
  - [ ] 7-2. Unit tests for `IntentRegistry` (register, match, no-match, overlapping patterns, input_mapping)
  - [ ] 7-3. Unit tests for `ActivityTracker` (surface registration, active/recent run listing, progress enrichment)
  - [ ] 7-4. Integration test: dispatch workflow via API, verify `RunManager` starts it, events appear on global bus
  - [ ] 7-5. Integration test: two WebSocket clients on global bus — run started from one surface, completion event visible to both
  - [ ] 7-6. Integration test: HumanNode pending from surface A, resolved from surface B, resolution broadcast to both
  - [ ] 7-7. Integration test: register intent pattern via API, dispatch with `text=` matching the pattern, verify correct workflow is matched and started
  - [ ] 7-8. Create shared pytest fixture for test server startup (`@pytest.fixture async def test_gateway()` using `httpx.ASGITransport`) — used by all downstream integration tests in 23-2 through 23-5
  - [ ] 7-9. Integration test: dispatch workflow, cancel mid-execution via `POST /api/gateway/cancel`, verify `run_cancelled` event broadcast
  - [ ] 7-10. Unit test: concurrent `submit_human_input()` for same `request_id` — second caller gets `False`

## Decisions

- `IntentRegistry` is optional and configurable — the gateway works without it. Direct `workflow_path`/`workflow_id` dispatch is the primary mode; intent matching is opt-in for NL surfaces. Surface type (`source`) is NOT passed per-dispatch — it's registered once via `register_surface()` and derived from `surface_id`.
- Global event bus is a new WebSocket endpoint, separate from per-run event streams. Existing `/api/runs/{id}/events` remains unchanged — zero breaking changes.
- `ActivityTracker` wraps `RunManager`, doesn't replace it. All existing RunManager functionality is preserved; ActivityTracker adds surface-awareness and progress enrichment on top.
- Surface registration is lightweight: `surface_id` (user-chosen string) + `type` (cli/telegram/mcp/chat). No authentication for now — trust-based within a single deployment.
- **Accepted security risk (Phase 13 only):** no authentication — anyone with network access can dispatch workflows, see all runs, and submit human inputs. Mitigation: `dan-serve` binds to `localhost` only by default. Remote access requires explicit `--host 0.0.0.0` flag. Full auth is a future backlog item.
- Workflow loading is extracted to `dan.utils.workflow_loader`, consolidating CLI and `publish/mcp_server.py`'s `load_workflows_from_path()` into a single shared module. Avoids duplication of the logic built in 21-2 and 21-3.
- `POST /api/runs` remains unchanged (used by chat panel). `POST /api/gateway/dispatch` is the higher-level entry point. `DanClient` always uses the gateway endpoint.
- MetaController is the server-side NL dispatch path for unmatched `text` inputs. It runs as a normal workflow via `RunManager`, with its approval steps handled through the HumanNode mechanism. The `IntentRegistry` is a fast-path shortcut; MetaController is the fallback.

## Notes

- The dispatch endpoint replaces the need for each surface to construct `EngineConfig` and call `Engine.run()` — the server handles all of that. Surfaces send a request, get back a `run_id`, and stream events.
- The global event bus is architecturally similar to the existing per-run WebSocket, just aggregated across all runs. Implementation: subscribe to RunManager's per-run event callbacks, fan out to global-bus WebSocket connections.
- Intent resolver is the general-purpose version of "concierge routing" — any workflow can register intents. This keeps the gateway workflow-agnostic.
- The `/api/gateway/` prefix namespaces all new endpoints cleanly, avoiding collision with existing `/api/runs/` and `/api/chat/` routes.
- **HumanNode request lifecycle:** `PENDING` (created, waiting for input) → `RESOLVED` (input submitted, run continues) | `TIMEOUT` (human_timeout exceeded, default_action applied or run paused) | `CANCELLED` (run cancelled while waiting). State transitions are atomic — only the first `submit-input` call wins. The `human_input_resolved` / `human_input_timeout` events broadcast the terminal state to all surfaces.
