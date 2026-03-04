# 21-3: Publish Workflow as API/MCP

**Parent:** [21-author-distribute](21-author-distribute.md)
**Status:** completed
**Goal:** Turn any DAN workflow into a callable service — primarily as an MCP server (for Cursor, Claude Desktop, and other MCP clients), with HTTP REST as a fallback. Stateful streaming and HumanNode support included. Easy portal: minimal setup for consumers.

## Context

DAN workflows are currently internal — they run inside the engine or the visual editor. "Publish" means exposing a workflow as an external service that others can invoke without knowing DAN. The MCP (Model Context Protocol) standard is the primary target because it's the emerging standard for tool interop in AI applications.

### MCP Background

MCP servers expose **tools** and **resources** to MCP clients (like Cursor, Claude Desktop). A published DAN workflow becomes one or more MCP tools with typed input/output schemas derived from the workflow's entry/exit nodes. The MCP client calls the tool, DAN runs the workflow, and returns the result.

## Tasks

- [x] 1. **Workflow-to-schema derivation**
  - [x] 1-1. `derive_workflow_interface(graph: Graph) -> WorkflowInterface` — already existed from 21-5 in `src/dan/utils/workflow_interface.py`
  - [x] 1-2. `WorkflowInterface` model — already existed from 21-5
  - [x] 1-3. Handle multi-entry/multi-exit workflows — already handled by 21-5
  - [x] 1-4. Handle workflows with no explicit InputNode — already handled by 21-5

- [x] 2. **MCP server generation**
  - [x] 2-1. Create `src/dan/publish/__init__.py` package
  - [x] 2-2. Create `src/dan/publish/mcp_server.py` — generates and runs an MCP server from a workflow
  - [x] 2-3. Each workflow → `{name}_run` tool (+ `_status` + `_submit_input` for human workflows)
  - [x] 2-4. Tool invocation: MCP client calls tool → DAN loads graph → `Engine.run(graph, inputs)` → return output
  - [x] 2-5. Support `stdio` transport (default) and `streamable-http` transport via FastMCP `.run(transport=...)`
  - [x] 2-6. Multi-workflow MCP server: `dan-publish --dir ./graphs/` publishes all workflows
  - [x] 2-7. MCP resource: `workflow://{slug}/metadata` exposes workflow metadata for discovery

- [x] 3. **Stateful execution (streaming + HumanNode)**
  - [x] 3-1. Multi-call MCP pattern: `run` → `status` → `submit_input` for human workflows
  - [x] 3-2. HTTP SSE endpoint for streaming events — `GET /api/published/{workflow_id}/events?session_id=X` with `PublishSessionStore.subscribe_events()` async generator
  - [x] 3-3. HTTP WebSocket endpoint for bidirectional HumanNode I/O — `WS /api/published/{workflow_id}/ws` with start/submit_input message types
  - [x] 3-4. `PublishSessionStore` tracks active sessions with lock-protected CRUD
  - [x] 3-5. `PublishedHumanRenderer` implements `HumanRenderer` — parks prompt, waits on `asyncio.Event`
  - [x] 3-6. Configurable timeout (default 5 min), returns `default_action` or raises `TimeoutError`

- [x] 4. **HTTP REST fallback**
  - [x] 4-1. `POST /api/published/{workflow_id}/run` — synchronous execution
  - [x] 4-2. `POST /api/published/{workflow_id}/run-async` → session_id + `GET .../runs/{sid}` polling
  - [x] 4-3. `GET /api/published/{workflow_id}/schema` — WorkflowInterface as JSON
  - [x] 4-4. `GET /api/published/` — list all published workflows
  - [x] 4-5. Authentication: `X-API-Key` header or `?api_key=` query param, per-workflow
  - [x] 4-6. Rate limiting — `RateLimiter` class with sliding-window counter, per-workflow configurable via publish config

- [x] 5. **CLI command: `dan-publish`**
  - [x] 5-1. Full argparse in `src/dan/cli/publish.py` replacing placeholder
  - [x] 5-2. `--type mcp` — start MCP server (stdio by default)
  - [x] 5-3. `--type http` — start HTTP server
  - [x] 5-4. `--type both` — HTTP in thread + MCP stdio on main
  - [x] 5-5. `--port <N>` (default 8001)
  - [x] 5-6. `--name <name>` override
  - [x] 5-7. `--api-key <key>` for auth
  - [x] 5-8. Rich TUI startup banner with graceful fallback

- [x] 6. **Portal: easy consumer setup**
  - [x] 6-1. `--generate-config` outputs MCP client config JSON
  - [x] 6-2. `--docs` generates markdown API documentation
  - [x] 6-3. `--openapi` outputs OpenAPI 3.1 spec
  - [x] 6-4. `GET /health` returns status + workflow count + uptime

- [x] 7. **Integration with server**
  - [x] 7-1. Mount publish router on `dan-serve` at `/api/published/` via `app.include_router()` in lifespan
  - [x] 7-2. "Publish" button in visual editor toolbar — dropdown with Publish as MCP/HTTP, Unpublish, Copy MCP Config; status indicator (green dot when published)
  - [x] 7-3. Publish state persisted in `graphs/{id}.publish.json`, auto-loaded on startup via `_auto_register_published_workflows()`

- [x] 8. **Tests** (76 tests)
  - [x] 8-1. Unit tests for `derive_workflow_interface()` integration (already tested by 21-5; conftest reuses patterns)
  - [x] 8-2. Unit tests for MCP server generation — tool schema correctness, mock FastMCP, multi-workflow
  - [ ] 8-3. Integration test: full MCP client invocation — deferred (requires live MCP SDK)
  - [x] 8-4. Unit tests for HTTP endpoints — health, list, schema, run-async, auth (via httpx AsyncClient)
  - [x] 8-5. Unit tests for session management — create/update/remove, HumanRenderer submit/timeout/transitions
  - [x] 8-6. Unit tests for CLI argument parsing

## Decisions

- **MCP SDK dependency** is optional (`[mcp]` extra). HTTP REST works without it.
- **MCP stdio transport** is the default because it's what Cursor and Claude Desktop expect for local tools. Streamable HTTP transport available for remote deployments.
- **One workflow = up to three MCP tools** (run, get_status, submit_input). Workflows without HumanNodes only expose the `run` tool. The multi-call pattern is necessary because MCP tools are request/response and cannot pause for interactive input.
- **One MCP server can host multiple workflows.** Each workflow's tools are prefixed with the workflow name (e.g., `paper_writing_run`, `paper_writing_status`).
- **Authentication is optional** — published APIs are intended for local/trusted use by default. API key support is there for shared/remote deployments.
- **The publish module does NOT require the visual editor.** It's a standalone capability that works with the engine directly.

## Notes

- MCP is evolving rapidly. Pin to a stable SDK version and isolate the MCP integration so protocol changes are contained.
- The "portal" concept is key UX: the output of `dan-publish` should be copy-pasteable into a consumer's config with zero edits.
- Stateful published APIs are architecturally similar to the existing `RunManager` WebSocket subscription — the publish layer is a thin adapter over the same engine event system.
- The multi-call MCP pattern (run → status → submit_input) mirrors how Cursor's own tool execution handles long-running operations. It's the idiomatic MCP approach.
- `PublishedHumanRenderer` implements `HumanRenderer` from `dan.engine.executor` — the same protocol that CLIHumanRenderer (21-2) and messaging adapters (21-4) implement. No new engine-level protocol needed.
