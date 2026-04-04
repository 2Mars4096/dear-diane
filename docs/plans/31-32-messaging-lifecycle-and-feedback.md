# 31-32: Messaging Lifecycle & Feedback

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** completed
**Goal:** Make messaging adapters survive restarts, report honest status, deliver unified progress/queue feedback, and polish Telegram operator UX — so remote access via Telegram and WhatsApp is reliably autonomous.

## Context

Messaging adapters (Telegram, WhatsApp Web) work when manually started, but several gaps prevent truly autonomous remote access:

1. **No auto-start at boot.** `startup.py` does not restore adapters. If the server restarts (or `dan-up` relaunches), remote access silently disappears until a user manually re-starts from the desktop UI or CLI.
2. **Status honesty gaps.** `TelegramAdapter` has no `get_connection_snapshot()` and no `_connection_state` tracking — `_running` is the only signal. `routers/adapters.py` infers `"connected"` from the task being alive. The fleet process is invisible to `GET /api/adapters/status` entirely. WhatsApp is more honest (`get_connection_snapshot()` with real `_connection_state`) but the desktop UI can show stale "Connected" state after backend disconnection because `useMessagingStore.refreshStatus()` preserves the last-known `connectionState` on network errors.
3. **Progress/queue is adapter-local and phase events are silently filtered.** Telegram fleet has its own progress timer and queue-hint logic. The server-side adapter path (`_relay_adapter_event` in `routers/adapters.py` L667-671) **explicitly filters out** `progress_ack` events, so `ProgressSession` phase transitions never reach messaging surfaces. The CLI adapter has an independent 5s one-shot timer. Neither surface drives progress from the concierge's canonical phases.
4. **Telegram operator features exist but aren't desktop-controllable.** `TelegramAdapter.register_custom_commands()` / `set_menu_button()` are callable on the in-process adapter, and `telegram_fleet.py` invokes them at fleet start, but there's no desktop API endpoint to trigger them after startup, and no Settings UI to inspect/edit the command menu or Mini App URL.

**Prerequisite:** Plan 1-10 (Messaging Onboarding & Controls) is functionally complete — only broader frontend coverage tests and live smoke remain. This plan builds on 1-10's backend API surface and `useMessagingStore`.

## Key code references

| Area | File | Key symbols |
|------|------|-------------|
| Adapter state | `routers/adapters.py` L34 | `_active_adapters: dict` (not `_running_adapters`) |
| Start route | `routers/adapters.py` L612-702 | `start_adapter()` — no duplicate guard |
| Config save | `routers/adapters.py` L719-756 | `POST /api/adapters/config/{type}` — no `auto_start` |
| Config path (WA) | `routers/adapters.py` L46 | `_DEFAULT_WHATSAPP_WEB_CONFIG_PATH` |
| Adapter relay | `routers/adapters.py` L665-723 | `_relay_adapter_event()` — filters `progress_ack` |
| Same-type lookup | `routers/adapters.py` L514-520 | `_adapter_ids_for_surface()` — exists, used only by reset |
| Startup lifespan | `startup.py` L801-826 | `init_*` phases, then `yield`; no adapter step |
| Shutdown | `startup.py` L756-776 | Stops adapters; no state persistence |
| Telegram polling | `telegram_adapter.py` L173-174 | `start_polling()` — no retry, no `_connection_state` |
| Telegram health | `telegram_adapter.py` | No `get_connection_snapshot()` |
| WA reconnect | `whatsapp_web_adapter.py` L284-344 | `_connect_with_retry()` — has retry |
| WA snapshot | `whatsapp_web_adapter.py` | `get_connection_snapshot()` — exists |
| Fleet commands | `telegram_fleet.py` L165-188 | `register_custom_commands`, `set_menu_button` — fleet start only |
| Progress session | `concierge/progress_ux.py` L106-181 | `ProgressSession` — phase events via renderer protocol |
| CLI progress | `cli/adapter.py` L371-381 | `_send_progress()` — 5s one-shot timer |
| UI session copy | `GlobalSettingsPanel.tsx` L819-820 | `"{N} session(s)"` for Telegram |
| UI toolbar copy | `EditorToolbar.tsx` L764-765 | `"{N} session(s)"` |
| UI status store | `useMessagingStore.ts` L500-558 | `refreshStatus()` — no "unknown" on error |

## Tasks

### 0. Backend-owned adapter autostart & reconnect
> Persist `auto_start` on the backend and restore adapters at server boot.

- [x] 0-1. **Add `auto_start` to backend adapter config.** Extend `POST /api/adapters/config/{type}` (L719-756) with an `auto_start: bool` field. Write it alongside existing config at `~/.dan/telegram/config.json` and `_DEFAULT_WHATSAPP_WEB_CONFIG_PATH`. `GET /api/adapters/config/{type}` should also return the saved value.
- [x] 0-2. **Restore adapters during server startup.** In `startup.py` lifespan, add an `init_adapters()` phase after `init_background` (around L817). Scan saved adapter configs at `~/.dan/telegram/config.json` and `_DEFAULT_WHATSAPP_WEB_CONFIG_PATH`. For each with `auto_start=True` and sufficient config (Telegram: token present; WhatsApp: session DB exists), start the adapter in a background task with a short delay (2-3s stagger between adapters) so boot isn't blocked. Log a warning and skip if config is missing or dependency is unavailable.
- [x] 0-3. **Telegram connection state tracking and reconnect.** `TelegramAdapter` currently has no `_connection_state` — only `_running`. Add `_connection_state: str` field with values matching WhatsApp's enum (`disconnected / starting / connected / reconnecting / error`). Wrap the `start_polling()` call (L173-174) in a retry loop (exponential backoff, 5 attempts, 2s-60s). On transient failure, set `_connection_state="reconnecting"` and emit an adapter event. On terminal failure, set `_connection_state="error"` with `_last_error`. On success, set `_connection_state="connected"`.
- [x] 0-4. **Guard duplicate starts.** In `start_adapter()` (L612), check `_active_adapters` for an existing adapter of the same surface type using `_adapter_ids_for_surface()` (L514-520). If one is already running, return 409 instead of starting a second instance. This protects against autostart + manual start races and double-click in the UI.
- [x] 0-5. **Persist adapter-was-running at shutdown.** In `startup.py` `shutdown()` (L756-776), before stopping adapters, write a `~/.dan/adapters-state.json` snapshot of which adapters were active. `init_adapters()` can use this as a secondary signal when `auto_start` is not explicitly set.
- [x] 0-6. **Regression tests:** autostart config round-trip (save/read), startup restore with mock `init_adapters()`, duplicate-start 409 response, Telegram reconnect success/failure state transitions, shutdown state persistence, WhatsApp reconnect (already exists — verify `_connect_with_retry` coverage).

### 1. Messaging status honesty
> Report real adapter health, not just "task is alive."

- [x] 1-1. **Telegram `get_connection_snapshot()`.** Add a `get_connection_snapshot()` method to `TelegramAdapter` that calls `bot.get_me()` (lightweight, not rate-limited by Telegram for polling bots). Return `connection_state` based on success/failure, plus `last_error`, `bot_username`, and `session_count` (count of unique `chat_id`s seen). This brings Telegram to parity with WhatsApp's existing snapshot interface.
- [x] 1-2. **Periodic heartbeat.** In `routers/adapters.py`, add a background task started during adapter startup that calls `adapter.get_connection_snapshot()` at a configurable interval (`DAN_ADAPTER_HEARTBEAT_SECONDS`, default 60). Cache the result in `_adapter_status_snapshots` so `GET /api/adapters/status` via `_build_adapter_status_payload()` returns fresh data. Use 60s (not 30s) to stay well within Telegram's rate limits. Skip adapters that don't implement `get_connection_snapshot()`.
- [x] 1-3. **Invalidate stale UI state on backend disconnect.** In `useMessagingStore.ts` `refreshStatus()` (L500-558), when `getAdapterStatus()` throws a network error, set `connectionState="unknown"` and `statusNote="Backend unreachable"` for each provider instead of preserving the last-known state. The Settings panel and ModeBar status dot should show a distinct "unknown" visual (e.g. gray dot) when the backend is unreachable.
- [x] 1-4. **Replace misleading session copy.** In `GlobalSettingsPanel.tsx` (L819-820) and `EditorToolbar.tsx` (L764-765), replace `"{N} session(s)"` with context-sensitive copy: Telegram running + 0 sessions → "Listening (no messages yet)"; WhatsApp paired + idle → "Linked (idle)"; otherwise show the real count. Use `summarizeMessagingStatus()` to compute the label centrally.
- [x] 1-5. **Regression tests:** Telegram `get_connection_snapshot()` success/failure, heartbeat updates cached snapshot, `useMessagingStore` transitions to `"unknown"` on network error, session-copy variants in `summarizeMessagingStatus()`.

### 2. Unified progress & queue visibility
> Drive messaging feedback from the concierge phase model instead of adapter-local timers.

- [x] 2-1. **Stop filtering phase events in adapter relay.** `_relay_adapter_event()` (L667-671) currently drops all `progress_ack` events. Change the filter: instead of dropping them outright, check for a new `phase_label` field. If present, treat it as a relayable phase event. Continue dropping legacy elapsed-timer-only progress events that have no phase label, so the old "Working on it — Xs" messages don't spam users.
- [x] 2-2. **Emit phase-labeled progress events from `ProgressSession`.** When `ProgressSession.start_phase()` / `update_phase()` fires (L106-181 in `progress_ux.py`), the existing renderer produces `ChatCompleteEvent` with `detected_mode="progress_ack"`. Add a `phase_label` field to these events (e.g. `"Searching your files…"`, `"Writing response…"`) so `_relay_adapter_event` can distinguish actionable phase updates from generic elapsed-timer ticks.
- [x] 2-3. **WhatsApp progress messages.** Wire `_relay_adapter_event()` to send a progress message to the WhatsApp user when a phase-labeled `progress_ack` event arrives. Cap at 1 progress message per 15s to avoid chat spam (reuse the Telegram fleet's throttle pattern). The existing CLI adapter 5s timer in `cli/adapter.py` (`_send_progress`, L371-381) becomes the fallback when the concierge does not emit phase events.
- [x] 2-4. **Telegram fleet: prefer phase text over elapsed timer.** In `_stream_with_edits()` (L506-531 in `telegram_fleet.py`), when a WS `chat_complete` event arrives with `detected_mode="progress_ack"` and a `phase_label`, use the `phase_label` as the progress text instead of "Working on it — Xs elapsed". Fall back to the elapsed timer only when no phase events have arrived within the `_PROGRESS_INITIAL_DELAY` window. This keeps the Telegram fleet's existing progress UX intact for non-phased turns.
- [x] 2-5. **Queue position visibility on both surfaces.** Standardize the queue-position copy: "Queued (position N) — I'll reply when ready." Both Telegram fleet (in `_iter_chat_stream_events`, L893-961) and the server-side `_relay_adapter_event` should use this format when `chat_queued` fires, instead of each surface having its own formatting.
- [ ] 2-6. **Audit same-project serialization.** Verify that `ConcurrentDispatcher` allows unrelated messages from different projects/surfaces to proceed in parallel while same-project messages serialize correctly. Document any edge cases in `docs/bugs.md`.
- [x] 2-7. **Regression tests:** `_relay_adapter_event` passes phase-labeled events but drops timer-only ones, `ProgressSession` emits `phase_label`, WhatsApp progress delivery with throttle, Telegram phase-text preference, queue copy standardization on both surfaces.

### 3. Telegram operator UX polish
> Make the bot command menu and onboarding controllable from the desktop.

**Scope note:** This task targets the in-process `TelegramAdapter` started via `POST /api/adapters/start` (the desktop path). The fleet (`telegram_fleet.py`) is a separate process managed by `dan-bot` CLI and is out of scope — fleet users continue using the CLI for command/menu customization.

- [x] 3-1. **Desktop bot-command editor.** Add a "Bot Commands" subsection in `Settings > Messaging > Telegram` (`GlobalSettingsPanel.tsx`). Read the current command list from `GET /api/adapters/config/telegram` (extend the response to include `commands: [{command, description}]`). Let the operator add/remove/reorder custom commands. Save via `POST /api/adapters/config/telegram` (extend to accept `commands`). Add a `POST /api/adapters/{adapter_id}/apply-commands` endpoint that calls `adapter.register_custom_commands()` on the running in-process adapter. The Settings UI calls this after saving if the adapter is running.
- [x] 3-2. **Mini App URL config.** Extend `GET /api/adapters/config/telegram` and `POST /api/adapters/config/telegram` with `mini_app_url: str | null`. Expose in the Telegram config card in Settings. Add a `POST /api/adapters/{adapter_id}/apply-menu` endpoint that calls `adapter.set_menu_button(url)` (or resets to default when cleared) on the running in-process adapter. The Settings UI calls this after saving.
- [x] 3-3. **Onboarding/help text tightening.** Update `TelegramAdapter._register_default_commands()` and the `/start` welcome message to include a 1-line "what this bot can do" summary derived from the project description (if set via config). Keep the default generic when no project description is active. Update `/help` similarly.
- [x] 3-4. **Regression tests:** command list config round-trip, `apply-commands` calls `register_custom_commands`, Mini App URL set/clear via `apply-menu`, welcome message with/without project context.

## Execution Order

```
0 (autostart & reconnect)  ← foundation; nothing else is autonomous without this
1 (status honesty)          ← can start in parallel with 0; independent
2 (progress & queue)        ← after 0+1 stabilize; needs running adapters
3 (Telegram operator UX)    ← after 0; needs config persistence
```

Tasks 0 and 1 are independent and can run in parallel. Task 2 depends on 0 being functional (adapters running) and benefits from 1 (accurate status). Task 3 depends on 0 (config persistence) and is independent of 2.

## Estimates

| Task | Estimate |
|------|----------|
| 0. Autostart & reconnect | 1.5d |
| 1. Status honesty | 1d |
| 2. Progress & queue | 1.5d |
| 3. Telegram operator UX | 1d |
| **Total** | **~5d** |

## Dependencies

- **1-10 (Messaging Onboarding & Controls):** backend adapter API (`POST /api/adapters/start`, `GET /api/adapters/status`, `POST /api/adapters/config/{type}`), `useMessagingStore`, Settings > Messaging panel
- **31-25 (Messaging Reliability):** unified portal contract, normalized identifiers, error visibility, `_relay_adapter_event` dispatch path
- **31-14 (Progressive Response UX):** `ProgressSession` phase model, `ProgressRenderer` protocol, `_make_phase_event` in runtime
- **31-2 (Visibility & Feedback):** `/status` command, notification wiring

## What stays in backlog (explicitly deferred)

- **Unified autonomy policy control** — separate concern; should be its own plan once messaging lifecycle is stable
- **Self-contained desktop packaging** — infrastructure/distribution; orthogonal to messaging reliability
- **Formal RL-style learning loop** — premature until messaging is boringly reliable
- **Swarm-grade bot individuality** — wait for single-bot messaging to be solid first
- **Fleet-level desktop controls** — the fleet is a separate process (`dan-bot`), and fleet management is tracked under Plan 30

## Non-Goals

- Telegram fleet management (multi-bot, topic routing, per-bot projects) — remains under Plan 30
- Slack, Discord, or new messaging surfaces
- WhatsApp Business API adapter
- Cross-surface session handoff (tracked under 31-13)

## Decisions

- (filled in during execution)

## Notes

- `_relay_adapter_event` progress filtering (L667-671) is the single biggest code-level blocker for task 2. Must be updated before any phase event can reach a messaging surface.
- `TelegramAdapter` needs both `_connection_state` tracking (task 0-3) and `get_connection_snapshot()` (task 1-1). These are separate tasks because state tracking is needed for reconnect UX while the snapshot is needed for status reporting, but they will likely be implemented together.
- The heartbeat interval is configurable via `DAN_ADAPTER_HEARTBEAT_SECONDS` (default 60s) to stay within Telegram API rate limits. `bot.get_me()` is lightweight and not subject to normal message-sending rate limits, but 60s is conservative.
- The CLI adapter's 5s progress timer (`cli/adapter.py` L371-381) remains as a fallback when the concierge does not emit phase events (e.g., during a fast Tier 0/1 turn). It should not be removed.
- Task 3 targets the in-process `TelegramAdapter` (desktop path), not the fleet process. Fleet users continue using `dan-bot` CLI for command/menu customization.
- Post-plan follow-up (2026-03-20): the shell Messaging pill now keys red/error state off current `connectionState` rather than any stale `lastError`, and adapter `status` events clear cached `last_error` when a provider reports `connected` or `disconnected`.
- Post-plan follow-up (2026-03-20): `useMessagingStore` now schedules one silent status recheck shortly after the shell Messaging state turns red while another provider is still active, so remote Telegram use can self-clear transient stale-red status without requiring a manual "Refresh from server" click.
- Post-plan follow-up (2026-03-20): the chat portal now allows distinct `session_id` and `thread_id` values, tier executors prefer `msg.session_id` for the ChatManager-facing `thread_id`, and `/cost` also resolves lane-scoped metadata from `session_id` first. This was required to make Telegram lane concurrency real instead of conflicting with the old request validator and broad-thread persistence assumptions.
- Post-plan follow-up (2026-04-04): Telegram fleet reply delivery now treats a missing non-progress terminal event as a user-visible interruption instead of a silent success. `_stream_with_edits()` and `_stream_collect()` synthesize the same "continue from the latest progress" fallback the main chat surface uses when the Telegram-side relay closes cleanly before any terminal answer arrives.
- Post-plan follow-up (2026-04-04): Telegram per-conversation `_fleet_*` workflow ids are now only cached after the graph lookup/create path succeeds, so transient backend failures do not poison a lane with a dead workflow id.
