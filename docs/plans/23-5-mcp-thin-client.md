# 23-5: MCP Thin Client

**Parent:** [23-multi-surface-gateway](23-multi-surface-gateway.md)
**Status:** completed
**Goal:** Refactor `dan-publish` MCP servers and HTTP REST endpoints to route workflow execution through `dan-serve` via `DanClient`, enabling published workflows to share run management and cross-surface visibility.

## Context

MCP publish (built in Phase 12, plan 21-3) generates MCP servers that invoke the engine directly. Published MCP tools create their own Engine instance per invocation. This means published workflow runs are invisible to the chat panel and other surfaces. After this refactor, MCP tool invocations route through the server's gateway, and the multi-call HumanNode pattern (run→status→submit_input) uses server-managed sessions.

## Tasks

- [x] 1. **Refactor MCP tool invocation**
  - [x] 1-1. Current pattern: MCP tool handler creates Engine, calls `Engine.run(graph, inputs)`, returns result. Direct execution.
  - [x] 1-2. New pattern: MCP tool handler calls `DanClient.dispatch(workflow_id=..., inputs=..., surface_id="mcp-{workflow_name}")`, then polls/subscribes for completion, returns result.
  - [x] 1-3. For simple (no-HumanNode) workflows: dispatch → wait for completion event → return output. Blocking from the MCP client's perspective.
  - [x] 1-4. For HumanNode workflows: dispatch → return `run_id` (from `DispatchResult`) immediately. Status and submit_input tools use `DanClient.get_pending_inputs()` and `DanClient.submit_human_input()`.
  - [x] 1-5. **MCP tool interface normalization:** `PublishRuntime` abstraction ensures MCP tools use `session_id` consistently in both modes. `LocalRuntime.run_async()` returns a UUID session_id; `GatewayRuntime.run_async()` returns a run_id. Both work as session identifiers for `status` / `submit_input` / `cancel` tools.

- [x] 2. **Session management via server** *(completed via PublishRuntime refactor)*
  - [x] 2-1. Replace `PublishSessionStore` (local, in-process) with server-side sessions: `GatewayRuntime` routes through RunManager; `LocalRuntime` retains `PublishSessionStore` for standalone mode
  - [x] 2-2. `{name}_status` MCP tool: delegates to `PublishRuntime.get_status()` — in gateway mode calls `DanClient.get_run_status()` + `get_pending_inputs()`
  - [x] 2-3. `{name}_submit_input` MCP tool: delegates to `PublishRuntime.submit_input()` — in gateway mode auto-resolves `request_id` from pending inputs
  - [x] 2-4. `PublishedHumanRenderer` no longer needed in server mode — `GatewayRuntime` manages HumanNode through server
  - [x] 2-5. Add `{name}_cancel` MCP tool: delegates to `PublishRuntime.cancel()`. Returns `{run_id, cancelled: bool}`.
- [x] 3. **HTTP REST endpoint routing** (these endpoints exist in `publish/http_server.py`; change is routing through `DanClient` instead of direct engine)
  - [x] 3-1. `POST /api/published/{id}/run` (sync): `DanClient.dispatch()` → wait for completion → return output
  - [x] 3-2. `POST /api/published/{id}/run-async`: `DanClient.dispatch()` → return run_id immediately
  - [x] 3-3. `GET /api/published/{id}/runs/{run_id}`: delegates to `PublishRuntime.get_status()`
  - [x] 3-4. SSE/WebSocket streaming: delegates to `PublishRuntime.subscribe_events()`. `GatewayRuntime` maps `EngineEvent` dicts to session-level format via `_map_engine_event()`; `LocalRuntime` passes through `PublishSessionStore` events directly.
- [x] 4. **Multi-workflow MCP server update**
  - [x] 4-1. `dan-publish --dir ./graphs/` still works: each workflow becomes MCP tools, all route through one DanClient instance
  - [x] 4-2. Single server connection shared across all published workflows
  - [x] 4-3. Surface registration: `DanClient.register_surface(surface_id="mcp-published", surface_type="mcp")`
- [x] 5. **Fallback mode**
  - [x] 5-1. If server unreachable, fall back to direct engine execution (existing 21-3 behavior)
  - [x] 5-2. Published MCP servers work standalone — important for distribution (consumers may not run dan-serve)
  - [x] 5-3. `--local` flag to force local mode (consistent with `dan-run --local`)
  - [x] 5-4. `--server <url>` flag for server URL override
- [x] 6. **dan-publish CLI update**
  - [x] 6-1. Add `--server <url>` and `--local` flags
  - [x] 6-2. When connected to server: logs "Publish connected to dan-serve (gateway mode)" via create_publish_runtime
  - [x] 6-3. When local: logs "Publish running in local mode" via create_publish_runtime
- [x] 7. **Integration with dan-serve** *(completed via PublishRuntime + gateway lifespan)*
  - [x] 7-1. `create_publish_app` lifespan calls `create_publish_runtime()` which probes dan-serve availability and creates `GatewayRuntime` when reachable
  - [x] 7-2. `dan-publish` as a separate process connects to `dan-serve` as a client via `GatewayRuntime`, benefiting from shared state
- [x] 8. **Tests** *(27 new tests in test_runtime.py)*
  - [x] 8-1. Unit tests for LocalRuntime: run_sync, run_async, get_status, cancel, close
  - [x] 8-2. Unit tests for GatewayRuntime: run_sync, run_async, submit_input, cancel, close
  - [x] 8-3. Unit tests for fallback mode + factory (create_publish_runtime)
  - [x] 8-4. Unit tests for engine event → session event mapping (_map_engine_event)
  - [ ] 8-5. Integration test: HumanNode workflow via MCP multi-call pattern using server sessions

## Decisions

- Fallback to standalone is essential for MCP distribution — published MCP servers must work without dan-serve (consumers install the MCP server, not the full DAN platform)
- When in server mode, `PublishSessionStore` and `PublishedHumanRenderer` are not used — all state is server-side
- HTTP REST endpoints (sync and async) route through DanClient the same way MCP tools do — unified path
- MCP server generation itself is unchanged (tool schemas, transport config) — only the execution path changes
- Rate limiting: `publish/http_server.py` has a `RateLimiter` for per-workflow limits. In server mode, rate limiting should be enforced at the gateway dispatch level (or preserved locally in the MCP handler before dispatching). Deferred: gateway-level rate limiting is not in scope for this plan — the MCP handler checks limits locally before calling `DanClient.dispatch()`.
- **PublishRuntime ABC chosen over per-call branching:** Instead of `if is_server_mode:` at every call site, a single `PublishRuntime` abstraction (`GatewayRuntime` or `LocalRuntime`) is resolved once at startup. This also fixes a real bug where MCP status/submit tools hit the local session store even when the run was dispatched through the gateway.
- **`gateway_mode.py` kept as deprecated shim** rather than deleted, to avoid breaking any external code that imports `PublishGatewayClient` directly.

## Notes

- The simplification is significant: MCP tools go from "create Engine + EngineConfig + configure providers + register tools + set up HumanRenderer + run" to `DanClient.dispatch(workflow_id, inputs)`
- For distributed MCP servers (installed by consumers far from the DAN server), local mode is the default. Server mode is for local-network or same-machine setups.
- When server is not running and `--local` is not set, `dan-publish` prints: "dan-serve not detected — publishing in local mode."
- **Behavior change in server mode:** workflows executed via server dispatch inherit the server's full `EngineConfig` including checkpointing, error memory, and reflection — which they did not have in local MCP mode. This is generally beneficial but may change execution behavior for workflows that previously ran without these features.
- **Session recovery after MCP server restart:** in server mode, run state is on the server and survives MCP process restart. A restarted MCP server can resume monitoring a run via `DanClient.get_run_status(run_id)` if the client retains the `run_id`. In local mode, runs are lost on process restart (same as current behavior).
- The multi-call HumanNode pattern maps cleanly to gateway API: run→dispatch, status→get_pending_inputs, submit_input→submit_human_input
