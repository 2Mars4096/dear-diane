# 1-10: Messaging Onboarding & Controls

**Parent:** [1-ui-spec](1-ui-spec.md)
**Status:** in-progress
**Goal:** Make Telegram and WhatsApp convenient, opt-in remote-control surfaces from the desktop app, with first-run connection, global settings-based lifecycle controls, and clear runtime status.

## Context

DAN already has substantial messaging infrastructure, but the desktop app exposes it poorly:

1. **The backend supports adapter lifecycle, but the desktop UI cannot start a connection.** `src/dan/server/routers/adapters.py` exposes `POST /api/adapters/start`, `POST /api/adapters/stop`, and `GET /api/adapters/status`. However, the frontend has no `startAdapter()` wrapper in `api.ts`, no guided setup, and no canonical settings surface. The existing adapter status/stop UI in `EditorToolbar.tsx` is only rendered inside `OperationsMode.tsx` — it is invisible from Chat, Code, and Research modes.
2. **Settings has no messaging section.** `GlobalSettingsPanel.tsx` currently covers `appearance | editor | research | runtime`. There is nowhere to turn Telegram or WhatsApp on or off, see errors, or configure startup behavior.
3. **First-run onboarding is planned but messaging is not part of it.** Plan [1-8-code-mode-patch](1-8-code-mode-patch.md) Task 3 tracks the first-run welcome/workspace skeleton. This plan hooks into that skeleton with an optional "connect remote chat" step.
4. **WhatsApp Web is the right convenience path, but it is terminal-first.** `WhatsAppWebAdapter` uses neonize for QR code pairing, but QR output goes to stderr and pairing status events fire in-process. There is no mechanism to relay QR data or pairing state to the UI.
5. **Telegram has two adapter paths.** The bare `TelegramAdapter` via `/api/adapters/start` is a simpler single-bot surface. The production `BotFleet` (`telegram_fleet.py`) supports streaming edits, reactions, forum topics, multi-bot routing, and is managed by the `dan-bot` CLI with config at `~/.dan/telegram/config.json`. Desktop v1 targets the simpler single-bot path; fleet management stays in the CLI.
6. **The server start route does not support WhatsApp Web.** `POST /api/adapters/start` maps `"whatsapp"` to the older `WhatsAppAdapter` (Business API). It does not recognize `"whatsapp-web"` or `WhatsAppWebAdapter`. This is a confirmed backend blocker.
7. **Existing config infrastructure.** Telegram: `TelegramFleetConfig` / `TelegramBotConfig` at `~/.dan/telegram/config.json` with token verification already in `dan-bot create` (`cli/bot.py`). WhatsApp Web: session DB at `~/.dan/whatsapp-web/session.sqlite3`, config via `WhatsAppWebAdapterConfig(db_path, allowed_jids, ...)`. Secrets live in `.env` (`DAN_TELEGRAM_BOT_TOKEN`) or on-disk session files, not in the frontend.

This plan turns messaging from a hidden backend capability into a first-class shell feature: discoverable in Settings, offered on first run, and easy to pause, resume, reconnect, or disable later. Users currently start adapters via `dan-adapter telegram` or `dan-adapter whatsapp-web` from the CLI; the desktop flow replaces that for desktop users.

## Tasks

### Slice A — Backend Gaps & API Surface

- [x] 1. **Register `whatsapp-web` adapter type in the server start route**
  - [x] 1-1. Add `elif adapter_type == "whatsapp-web"` branch in `routers/adapters.py` that creates a `WhatsAppWebAdapter` with `WhatsAppWebAdapterConfig`
  - [x] 1-2. Add a QR/pairing event relay: when `WhatsAppWebAdapter` emits QR data or pairing status, push those as SSE or WebSocket events on a new `GET /api/adapters/{id}/events` stream so the frontend can render them
  - [x] 1-3. Extend `GET /api/adapters/status` response with `connection_state` (`disconnected | starting | pairing | connected | reconnecting | error`), `last_error` (string), and `paired` (bool) so the UI can render richer status than today's `running / session_count / uptime`
  - [x] 1-4. Add `GET /api/adapters/config/{type}` that returns masked config summary (Telegram: masked token, allowed chats count; WhatsApp: paired flag, allowed JID count, session DB exists) — never returns raw secrets
  - [x] 1-5. Add `POST /api/adapters/config/{type}` that saves provider config server-side: Telegram bot token → `DAN_TELEGRAM_BOT_TOKEN` in `.env` plus `~/.dan/telegram/config.json`; WhatsApp allowed JIDs → `WhatsAppWebAdapterConfig` fields. Returns masked confirmation.

- [x] 2. **Add frontend API wrappers**
  - [x] 2-1. Add `startAdapter(type, config)` in `editor/src/lib/api.ts` wrapping `POST /api/adapters/start`
  - [x] 2-2. Add `getAdapterConfig(type)` and `saveAdapterConfig(type, config)` wrappers
  - [x] 2-3. Add `connectAdapterEvents(adapterId)` → `EventSource` wrapper for the SSE stream (QR codes, pairing state, errors)
  - [x] 2-4. Create `editor/src/store/useMessagingStore.ts` — Zustand store with per-provider state (`enabled`, `autoStart`, `connectionState`, `lastError`, `adapterId`, `sessionCount`, `paired`), persisted `enabled` and `autoStart` flags, and actions: `connect`, `disconnect`, `refreshStatus`

### Slice B — Settings Surface

- [x] 3. **Add a `Messaging` section to `GlobalSettingsPanel`**
  - [x] 3-1. Add a fifth section `{ id: "messaging", label: "Messaging", icon: <MessageSquare /> }` to the `SECTIONS` array
  - [x] 3-2. Render a provider card for Telegram: connection state badge, masked token preview or "Not configured", session count, primary CTA (`Connect` / `Reconnect` / `Stop`), `Auto-start on launch` toggle
  - [x] 3-3. Render a provider card for WhatsApp Web: connection state badge, paired/unpaired status, primary CTA (`Pair` / `Reconnect` / `Stop`), `Auto-start on launch` toggle
  - [x] 3-4. Expandable "Advanced" per provider: Telegram allowed-chat-IDs list, WhatsApp allowed-JIDs list, `Reset pairing` (deletes `~/.dan/whatsapp-web/session.sqlite3` and restarts), dependency status (`python-telegram-bot` / `neonize` installed or not with install command)
  - [x] 3-5. Brief explanatory copy per provider: Telegram = "Connect a Telegram bot. Requires a bot token from @BotFather."; WhatsApp = "Link your personal WhatsApp by scanning a QR code. No Business API needed."

- [x] 4. **Move adapter status from Operations-only toolbar to shell level**
  - [x] 4-1. Add a small messaging status indicator to `ModeBar.tsx` or `AppShell.tsx` (visible in all modes): dot color for connection state, click opens Settings > Messaging
  - [x] 4-2. `useMessagingStore` polls `GET /api/adapters/status` (reuse the 10s interval from `EditorToolbar`); the Operations-mode toolbar can consume the same store instead of its own local state

### Slice C — First-Run Messaging Onboarding

> **Implementation note:** the desktop app lands in `Chat` mode by default, so the first-run messaging step now lives in the Chat empty state instead of waiting on the older Code-mode onboarding skeleton.

- [x] 5. **Add optional messaging step to first-run onboarding**
  - [x] 5-1. After the base welcome/workspace step from 1-8 Task 3, present: `Connect Telegram`, `Connect WhatsApp`, `Skip for now`
  - [x] 5-2. Each choice opens the same connect flow used in Settings (Tasks 6/7 below); the onboarding frame just provides the sequencing wrapper
  - [x] 5-3. Fully skippable — persists `messaging_onboarding_offered: true` in settings store so the app never re-shows this step, but a subtle "Connect messaging" link remains in Settings
  - [x] 5-4. If the backend is unreachable or the optional Python dependency is missing, degrade to "Set this up later in Settings" instead of blocking first use

### Slice D — Telegram Connect Flow

- [x] 6. **Build a guided Telegram setup form (reusable in Settings and onboarding)**
  - [x] 6-1. Bot token input with "How to get a token" expandable helper (adapt the BotFather checklist from `cli/bot.py`'s `setup-guide` subcommand)
  - [x] 6-2. On submit: call `POST /api/adapters/config/telegram` to save the token, then `POST /api/adapters/start` with `type: "telegram"`. Surface backend validation errors (invalid token, network failure) in plain language
  - [x] 6-3. On success: show bot username (from the start response or a status poll), prompt user to send `/start` to their bot, transition card to `connected` state
  - [x] 6-4. Stop/restart does not require re-entering the token — `useMessagingStore` reads the masked config to confirm a token exists before offering `Reconnect`
  - [x] 6-5. Optional allowed-chat-IDs field (collapsed by default; explain that an empty list means "respond to anyone")

### Slice E — WhatsApp Web Connect Flow

- [x] 7. **Build an in-app WhatsApp QR pairing flow**
  - [x] 7-1. Call `POST /api/adapters/start` with `type: "whatsapp-web"`. The backend starts `WhatsAppWebAdapter`, which begins QR generation via neonize.
  - [x] 7-2. Frontend subscribes to `GET /api/adapters/{id}/events` SSE stream. Backend intercepts neonize's QR output (hook `NewClient`'s QR callback) and emits `{ type: "qr", data: "<qr-string>" }` events plus `{ type: "pair_status", status: "paired" | "waiting" | "failed" }` events.
  - [x] 7-3. Render the backend-provided QR SVG data URI in-app and show "Open WhatsApp > Linked Devices > Link a Device" instructions alongside.
  - [x] 7-4. On `pair_status: paired` → transition to `connected` state, hide QR, show "WhatsApp linked" confirmation
  - [x] 7-5. If the adapter is already paired (session DB exists and neonize reconnects without QR), skip QR and show `connected` directly — detected via `connection_state: connected` without any preceding `qr` event
  - [x] 7-6. `Disconnect / Reset pairing` action: stops adapter, deletes session DB, clears store state. Next connect triggers fresh QR.

### Slice F — Persistence, Startup, and Recovery

- [x] 8. **Make messaging survive restarts**
  - [x] 8-1. On desktop startup, after backend health check passes, auto-start each provider where `useMessagingStore.{provider}.enabled && autoStart` is true
  - [x] 8-2. Before auto-start, verify saved config exists (Telegram: `DAN_TELEGRAM_BOT_TOKEN` non-empty; WhatsApp: session DB exists) — skip with a log warning if not
  - [x] 8-3. Surface missing optional Python dependencies (`python-telegram-bot`, `neonize`) with actionable install commands in the Settings card instead of opaque backend errors
  - [x] 8-4. Reconcile adapter status on reconnect: if the backend already has an adapter running (server was not restarted), sync `useMessagingStore` from the status poll instead of starting a duplicate

### Slice G — Verification

- [ ] 9. **Verify end-to-end**
  - [ ] 9-1. Frontend tests: focused helper/store coverage landed, but Settings rendering and connect/stop/reconnect CTA coverage are still open
  - [x] 9-2. Backend tests: `whatsapp-web` adapter type in start route, config save/masked-read, adapter event stream, enriched status response
  - [ ] 9-3. Manual smoke: first run → connect Telegram → receive message; first run → connect WhatsApp → QR scan → receive message; restart → auto-reconnect; disable from Settings → adapter stops; re-enable → adapter starts

## Priority & Sequencing

```
Slice A (backend + api)     ──► foundation; nothing else works without this
Slice B (settings UI)       ──► can start once A is defined; the main deliverable
Slice D (Telegram flow)     ──► can start after A/B; simpler of the two providers
Slice E (WhatsApp flow)     ──► after A/B; pacing item because of QR event relay
Slice C (first-run step)    ──► after B/D/E plus 1-8 Task 3 onboarding skeleton
Slice F (startup/recovery)  ──► after provider flows work
Slice G (verify)            ──► final gate
```

Telegram and WhatsApp can land independently. The plan is complete when both providers are convenient to connect, disable, and recover from the desktop UI.

## Estimates

| Slice | Estimate |
|-------|----------|
| A. Backend gaps & API surface | 1.5d |
| B. Settings surface | 1d |
| C. First-run messaging step | 0.5d |
| D. Telegram connect flow | 0.5d |
| E. WhatsApp Web connect flow | 1.5d |
| F. Persistence & recovery | 0.5d |
| G. Verification | 0.5d |
| **Total** | **~6d** |

WhatsApp (Slice E) is the largest item because of the QR event relay plumbing between neonize → backend SSE → frontend rendering. Telegram is smaller because the bare adapter lifecycle is already functional.

## Out of Scope (future plans)

- Telegram multi-bot fleet management, topic routing, or per-bot project ownership (tracked under Plan 30; `dan-bot` CLI remains the fleet path)
- Slack, Discord, or additional messaging surfaces
- Swarm-grade bot individuality / employee model (backlog item in `todo.md`)
- Per-workspace messaging identities or routing policies
- Deep telemetry/analytics dashboards for messaging usage
- WhatsApp Business API adapter (the older `WhatsAppAdapter` remains available via CLI)

## Decisions

- Messaging connections are **app-global remote surfaces**, not per-workspace settings — one Telegram bot and one WhatsApp link for the whole app
- Desktop WhatsApp uses **`WhatsAppWebAdapter` with QR pairing** (personal account, no Business API)
- Desktop Telegram v1 targets **single-bot via `TelegramAdapter`** through `/api/adapters/start`; the multi-bot fleet (`BotFleet` / `dan-bot` CLI / `~/.dan/telegram/config.json`) remains the advanced Telegram path
- Telegram bot token is stored in **`.env` as `DAN_TELEGRAM_BOT_TOKEN`** and also written to `~/.dan/telegram/config.json` for fleet compat; the UI never reads the raw token back
- WhatsApp session is stored in **`~/.dan/whatsapp-web/session.sqlite3`** (existing neonize behavior); no new secret storage needed
- Config read endpoint returns **masked summaries** only — the UI knows "a token is configured" and "3 allowed chats", never the actual values
- The QR pairing handshake uses **SSE from a per-adapter event endpoint** (`GET /api/adapters/{id}/events`), not WebSocket, because the adapter lifecycle is request/response and SSE is simpler for unidirectional server→client events
- The shell-level status indicator lives in **`ModeBar` or `AppShell`** (visible in all modes), not in the Operations-only `EditorToolbar`
- First-run messaging setup is **optional and one-time**; it now uses the Chat empty state as the canonical first-run host because the desktop app opens in Chat by default
- Provider-aware onboarding buttons and shell-level status all deep-link into the same `Settings > Messaging` surface so connection, reset, and recovery stay on one control plane

## Notes

- Implemented on 2026-03-17: provider-aware first-run messaging onboarding in Chat, backend dependency summaries + WhatsApp reset route, Telegram helper text + saved bot username, advanced provider disclosures in Settings, and `EditorToolbar.tsx` consumption of `useMessagingStore` instead of local polling.
- Remaining gaps before this plan is fully complete: broader frontend rendering/CTA coverage and live manual smoke coverage across Telegram/WhatsApp connect, reconnect, and disable flows.
- `GlobalSettingsPanel.tsx` sections are a flat array (`SECTIONS`), so adding `"messaging"` is a one-line addition plus the render function.
- `dan-bot create` in `cli/bot.py` already has BotFather instructions and `_verify_token()` — reuse or adapt that content for the desktop Telegram helper text.
- WhatsApp QR relay is the single hardest piece. Neonize's `NewClient` fires QR data via an in-process callback; the backend task wrapping `adapter.start()` must intercept that callback and push events onto the SSE stream. This may require a small adapter-level hook (`on_qr_code`, `on_pair_status`) that `WhatsAppWebAdapter` exposes and the router wires to the event endpoint.
- The `whatsapp-web` adapter type addition is a confirmed backend blocker — without it, the desktop UI literally cannot start the right adapter.
