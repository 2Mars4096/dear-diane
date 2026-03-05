# 23-4: Adapter Thin Client

**Parent:** [23-multi-surface-gateway](23-multi-surface-gateway.md)
**Status:** completed
**Goal:** Refactor messaging adapters (email, Telegram, WhatsApp) to route workflow execution through `dan-serve` via `DanClient`, making HumanNode resolution server-managed and enabling cross-surface visibility of adapter-started runs.

## Context

Messaging adapters (built in Phase 12, plan 21-4) currently implement `MessagingHumanRenderer` and run the engine in-process. This means a run started from Telegram is invisible to the chat panel, and HumanNode prompts can only be resolved by the same adapter that started the run. After this refactor, adapters become thin relay clients: they receive messages, dispatch workflows via the server, subscribe to events, and relay HumanNode prompts/responses through the messaging channel.

## Tasks

- [x] 0. **Update AdapterConfig model**
  - [x] 0-1. Add gateway fields to `AdapterConfig`: `server_url: str | None = None`, `local_mode: bool = False`, `forward_external_prompts: bool = False`
  - [x] 0-2. These fields drive the thin-client vs local decision at adapter startup

- [ ] 1. **Refactor MessagingHumanRenderer to use server-side resolution**
  - [ ] 1-1. Current pattern: adapter's `MessagingHumanRenderer` implements `HumanRenderer` protocol, engine calls `render()` directly → adapter sends messaging prompt → waits for reply → returns response. This is in-process.
  - [ ] 1-2. New pattern: adapter dispatches workflow via `DanClient.dispatch()`. When event stream contains `human_input_needed` event (matching `EngineEvent.HUMAN_INPUT_NEEDED`), adapter sends prompt via messaging channel. When user replies, adapter calls `DanClient.submit_human_input()`.
  - [ ] 1-3. `MessagingHumanRenderer` is no longer needed in server mode — HumanNode rendering is decoupled from the engine process.
  - [ ] 1-4. Keep `MessagingHumanRenderer` for fallback (local) mode — unchanged from 21-4.
- [ ] 2. **Adapter event relay**
  - [ ] 2-1. After `DanClient.dispatch()`, subscribe to run events via `DanClient.subscribe_run(run_id)`
  - [ ] 2-2. Map engine events to messaging updates: `node_started` → progress message (throttled), `run_completed` → result message, `run_failed` → error message
  - [ ] 2-3. Throttling unchanged from 21-4: max one progress message per 5 seconds
  - [ ] 2-4. Long outputs split per messaging platform limits (Telegram 4096 chars, WhatsApp limits)
  - [ ] 2-5. Run cancellation: if user sends `/cancel` during an active run, call `client.cancel_run(run_id)`. Map `run_cancelled` event to a cancellation confirmation message.
- [ ] 3. **Session management update**
  - [ ] 3-1. `AdapterSessionStore` maps conversation IDs to `run_id` (unchanged from 21-4)
  - [ ] 3-2. In server mode, session also stores `surface_id` for the adapter instance
  - [ ] 3-3. On adapter startup: register as surface via `DanClient.register_surface(surface_id="telegram-bot-1", surface_type="telegram")`. Registration is optional for basic dispatch but required for global event bus subscription (task 3-4) and cross-surface HumanNode pickup (task 4).
  - [ ] 3-4. Subscribe to global event bus for cross-surface notifications (optional): adapter receives completion events for runs started from other surfaces
- [ ] 4. **Cross-surface HumanNode pickup (optional, advanced)**
  - [ ] 4-1. When an adapter is subscribed to the global event bus and a HumanNode prompt arrives from ANY run (not just adapter-started ones), the adapter CAN relay it to the messaging user
  - [ ] 4-2. This enables: start a run from CLI, HumanNode prompt forwarded to Telegram for mobile response
  - [ ] 4-3. Configurable: `forward_external_prompts: bool = False` in AdapterConfig — off by default
  - [ ] 4-4. When enabled, adapter listens on global bus for `human_input_needed` events, renders via messaging
- [ ] 5. **Fallback mode**
  - [ ] 5-1. If server is unreachable, adapter falls back to direct engine mode (existing 21-4 behavior)
  - [ ] 5-2. `DanClientOrLocal` handles detection automatically
  - [ ] 5-3. Startup message: "Connected to dan-serve at localhost:8000" or "Running in local mode"
- [ ] 6. **dan-adapter CLI update**
  - [ ] 6-1. `--server <url>` flag for server URL override
  - [ ] 6-2. `--local` flag to force local engine mode (consistent with `dan-run --local`)
  - [ ] 6-3. Status display: show connection mode, active sessions, server activity (if connected)
- [ ] 7. **Tests**
  - [x] 7-1. Unit tests for adapter in server mode: dispatch → event relay → HumanNode relay → completion
  - [x] 7-2. Unit tests for fallback: server down → local mode → existing behavior
  - [ ] 7-3. Unit tests for cross-surface HumanNode pickup (when enabled)
  - [ ] 7-4. Integration test: start run via `POST /api/gateway/dispatch` → HumanNode prompt forwarded to mock Telegram adapter via global bus → response submitted via `DanClient.submit_human_input()` → run completes (no dependency on CLI thin client)

## Decisions

- Cross-surface HumanNode pickup is opt-in (off by default) — it's powerful but could be surprising if every HumanNode prompt lands in your Telegram
- Adapters keep their existing trigger modes (keyword, always, pattern) — the trigger determines WHEN to dispatch, DanClient handles HOW
- Event-to-message mapping is adapter-specific (Telegram inline keyboards, email HTML, etc.) — unchanged from 21-4
- Session management is local to the adapter (not server-managed) — the server only sees runs, not messaging conversations
- Multi-workflow routing per adapter is deferred — use one adapter instance per workflow, or register intent patterns on the server and let adapters dispatch with `text=message_text` for server-side routing

## Notes

- The biggest behavior change: adapter no longer creates Engine/EngineConfig. All configuration is on the server side.
- Adapter becomes much simpler: receive message → dispatch → relay events → relay HumanNode. No engine setup, no ToolRegistry, no provider config.
- WhatsApp's webhook-based architecture maps naturally to this pattern: webhook → dispatch, status webhook → event relay.
- **Coexistence with server-managed adapters:** The existing `POST /api/adapters/start` pattern (adapters running in-process with `dan-serve`) continues to work. Those adapters already route through `RunManager` and benefit from the gateway automatically. The thin-client pattern in this plan is for adapters running as separate external processes.
- When server is not running and `--local` is not set, adapter prints: "dan-serve not detected — running in local mode. Start dan-serve for cross-surface features."
- **Adapter restart = session loss (accepted for MVP).** If an adapter process crashes and restarts, it re-registers with the same `surface_id` but its local `AdapterSessionStore` is reset. In-flight runs on the server continue, but the adapter loses the mapping from messaging conversation → `run_id`. Future enhancement: persist session store to disk or server-side.
