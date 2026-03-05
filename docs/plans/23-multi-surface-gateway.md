# 23: Multi-Surface Gateway

**Status:** completed
**Goal:** Unify all interaction surfaces (CLI, messaging adapters, MCP) through `dan-serve` as a central hub with shared run management, cross-surface event streaming, and activity tracking.

## Motivation

Today, only the chat panel routes through the server. CLI, messaging adapters, and MCP each instantiate their own `Engine`, meaning they can't see each other's active runs, can't share HumanNode sessions, and can't receive cross-surface notifications. A workflow started from Telegram is invisible to the chat panel; a HumanNode prompt triggered by a CLI run can only be answered from that same terminal.

After this phase, any surface can trigger a workflow, any surface can observe activity from any other surface, and HumanNode interactions are managed centrally. `dan-serve` becomes the single process that owns all run state — CLI, adapters, and MCP become thin HTTP/WebSocket clients that dispatch requests and stream events through the server.

This is the infrastructure foundation for persistent multi-surface agent systems. A coding assistant workflow started from the CLI can be monitored from the chat panel and receive human approvals via Telegram. A research workflow triggered by email can stream progress to all connected surfaces simultaneously. The gateway is general-purpose — not hardcoded to any particular workflow.

## Existing Infrastructure

| Component | Location | Relevance |
|---|---|---|
| `RunManager` | `server/run_manager.py` | Already manages background runs, event pub/sub, human-input registry, WebSocket streaming per run |
| `HumanRenderer` protocol | `engine/executor.py` | Formal protocol for HumanNode rendering — `render(HumanRenderRequest) -> HumanRenderResponse` |
| CLI (`dan-run`) | `cli/run.py` | Runs Engine directly with `CLIHumanRenderer`; will become a thin client |
| Messaging Adapters | `adapters/` | Run Engine directly with `MessagingHumanRenderer`; will become thin clients |
| MCP Publish | `publish/` | Runs Engine directly with `PublishedHumanRenderer`; will become a thin client |
| Chat panel | `server/app.py` | Already routes through the server — reference implementation for thin-client pattern |
| `dan-status` / `dan-logs` | `cli/status.py`, `cli/logs.py` | Background run monitoring — will switch from local PID files to server API |
| Workflow loading | `cli/run.py` task 2 | JSON/markdown/Python/NL loading logic — server dispatch reuses this |

## Sub-Plans

| # | Sub-Plan | Scope | Effort | Dependencies |
|---|----------|-------|--------|--------------|
| [23-1](23-1-gateway-api.md) | Gateway API & Event Bus | Server-side dispatch endpoint, activity tracker, global event bus, cross-surface HumanNode resolution, run cancellation, MetaController routing | ~4 days | None (foundational); touches `server/`, `utils/`, import sites in `cli/` and `publish/` |
| [23-2](23-2-thin-client-protocol.md) | Thin Client Protocol | Shared `DanClient` library: HTTP dispatch, WebSocket event streaming, HumanNode relay, fallback mode, error handling, run lifecycle (cancel/resume) | ~2 days | 23-1 (API shape) |
| [23-3](23-3-cli-thin-client.md) | CLI Thin Client | Refactor `dan-run`, `dan-status`, `dan-logs` to use `DanClient`; fallback to direct engine | ~1 day | 23-2 (client library) |
| [23-4](23-4-adapter-thin-client.md) | Adapter Thin Client | Refactor messaging adapters to use `DanClient`; relay HumanNode I/O through server | ~1 day | 23-2 (client library) |
| [23-5](23-5-mcp-thin-client.md) | MCP Thin Client | Refactor `dan-publish` to use `DanClient`; published MCP tools dispatch through server | ~1 day | 23-2 (client library) |

## Dependencies / Sequencing

```
23-1 (Gateway API & Event Bus) ← foundational, start here
  └→ 23-2 (Thin Client Protocol) ← depends on API shape from 23-1
       ├→ 23-3 (CLI Thin Client)     ← independent, uses DanClient
       ├→ 23-4 (Adapter Thin Client) ← independent, uses DanClient
       └→ 23-5 (MCP Thin Client)     ← independent, uses DanClient
```

**Parallelizable:** After 23-2 completes, 23-3/23-4/23-5 can run in parallel — they each consume `DanClient` but don't depend on each other.

## Key Decisions

- **Backward compatible.** CLI, adapters, and MCP fall back to direct engine mode when the server is not running. Cross-surface features require the server, but standalone execution always works.
- **Server owns all run state.** When routing through `dan-serve`, surfaces don't instantiate their own Engine. The server loads the workflow, starts the run, and streams events back.
- **General-purpose gateway.** The dispatch endpoint and event bus are not hardcoded to any workflow. Any workflow that can be loaded (JSON/markdown/Python) can be dispatched from any surface.
- **Configurable intent→workflow routing.** The intent resolver is optional — surfaces can always dispatch by explicit workflow path/ID. NL dispatch via intent patterns is opt-in, registered per-workflow at startup.
- **Surface registration is lightweight.** Surfaces identify themselves with a `surface_id` (user-chosen string) and `type` (cli/telegram/mcp/chat). No authentication layer in this phase.
- **Global event bus is additive.** Existing per-run WebSocket endpoints (`/api/runs/{id}/events`) remain unchanged. The global bus is a new endpoint that aggregates across all runs.
- **Server failure mid-run.** If the server crashes during a run, the run is lost. Thin clients detect the disconnect and report an error. Run recovery/persistence is out of scope for this phase (backlog).
- **`workflow_path` is local-only.** When dispatching by file path, the server must have filesystem access to that path. This works for same-machine and shared-filesystem deployments. For remote servers, use `workflow_id` (loaded from graph store) instead.
- **Adapter dual-pattern.** Server-managed adapters (existing `POST /api/adapters/start`) and thin-client adapters (new, via `DanClient`) coexist. Server-managed adapters already route through `RunManager`; thin-client pattern is for external adapter processes.
- **Surface registration is optional for dispatch.** Surfaces can dispatch without pre-registering. The server tracks surface identity when available but doesn't require it. `register_surface()` is needed only for global event bus subscriptions and activity tracking.
- **`POST /api/runs` coexists with `POST /api/gateway/dispatch`.** The existing `/api/runs` endpoint remains unchanged (used by chat panel and direct API consumers). The gateway `/api/gateway/dispatch` is the higher-level entry point that handles workflow loading, intent resolution, surface tracking, and MetaController routing before delegating to `RunManager`. `DanClient` always uses the gateway endpoint.
- **CORS is permissive for development.** `allow_origins=["*"]` is the default. Production deployments should configure `DAN_CORS_ORIGINS`. WebSocket connections are not restricted by CORS — surface registration provides lightweight identity.
- **Intent resolver is deferred.** The `IntentRegistry` (regex/keyword pattern matching) is a nice-to-have but unused by any downstream plan (CLI uses MetaController for NL, adapters dispatch by path, MCP by ID). It remains in the plan as an optional task but is not blocking for the MVP gateway.

## Success Criteria

- A CLI run started via the server is visible in the chat panel activity view and triggers a completion notification on a connected Telegram adapter.
- `dan-status` shows runs from all surfaces (CLI, chat, Telegram, MCP) when connected to the server.
- A HumanNode prompt from a Telegram-started run can be resolved from the chat panel or CLI.
- `POST /api/gateway/dispatch` accepts workflow path, starts run, and returns `run_id` — events stream on global WebSocket.
- All existing tests pass. Each surface still works in standalone (direct engine) mode when the server is not running.
- Two WebSocket clients connected to the global event bus both see events from a single run.
- Cross-surface HumanNode pickup is configurable per-adapter (`forward_external_prompts` opt-in flag).
- A run started from CLI can be cancelled from the chat panel via `POST /api/gateway/cancel`.
- `dan-run --bg` in server mode dispatches and exits immediately; `dan-status` shows the backgrounded run.
- `dan-run --resume <run_id>` resumes a checkpointed run through the server.

## Notes

- Post-implementation hardening completed: deferred minors from final review were patched (blank dispatch source validation, non-terminal WebSocket close handling in `DanClient.subscribe_run`, stable publish `surface_id`, timeout-aware non-blocking CLI server-mode input).
- The chat panel is the reference implementation — it already routes through the server. The architectural change is making the server the *only* engine host, with all other surfaces as clients.
- This phase does not add authentication or multi-tenancy. Surface registration is trust-based. Auth is a future concern (backlog).
- Effort estimate: ~7–9 days total across all sub-plans. 23-1 is ~4 days (largest, most sub-tasks), 23-2 is ~2 days (DanClientOrLocal fallback is substantial), then 23-3/23-4/23-5 parallelize (~1 day each, overlapping).
- **Mid-run server loss UX:** if the server crashes while a run is executing, thin clients detect the disconnect (WebSocket close + HTTP failures). `DanClient` raises `RunLostError` after reconnection retries are exhausted. Surfaces should display: "Server connection lost — run {run_id} cannot be recovered. Use `--local` for independent execution." On server restart, orphaned runs are marked `FAILED` with `reason: server_restarted`.
- **Event durability contract:** events are not persisted on the client side. During disconnection, events buffer server-side in bounded queues (drop-oldest if full). Gap recovery on reconnect fetches missed events via `GET /api/runs/{id}/events?since=<timestamp>`. Events dropped from bounded queues are permanently lost. Each event carries a monotonic `sequence_id` for deduplication.
- **HumanNode request lifecycle:** requests follow a state machine: `PENDING` → `RESOLVED` (first submit wins) | `TIMEOUT` (configurable `human_timeout`) | `CANCELLED` (run cancelled). Transitions are atomic — concurrent submitters race, only the first succeeds. Terminal state is broadcast to all surfaces.
- **Surface session recovery:** adapter process restart resets local session stores (conversation → run_id mappings). Server-side run state is preserved, but the adapter loses its mapping. This is accepted for MVP. Persistent session stores are a future enhancement.
