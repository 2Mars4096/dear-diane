# 26-4: Notifications

**Parent:** [26-always-on-service](26-always-on-service.md)
**Status:** completed
**Goal:** Push notifications when runs complete, fail, or need human input. Server-side channels: macOS Notification Center and webhook callback via `GlobalEventBus`; client-side channel: terminal bell in CLI surfaces.

## Context

- `server/gateway/events.py`: `GlobalEventBus` — cross-surface event streaming with backpressure (max 50 subscribers, drop-oldest). Async iterator-based subscription.
- `engine/events.py`: `EngineEvent`, `EventType` — 14 typed runtime events including `run_completed`, `run_failed`, `human_input_needed`.
- `server/run_manager.py`: `RunManager` manages runs, emits events, has pubsub.
- `server/gateway/activity.py`: `ActivityTracker` — tracks active/recent runs.
- CLI: `dan-status` in `cli/status.py` lists active/recent runs from `~/.dan/runs/`.
- Background runs store PIDs in `~/.dan/runs/`.
- 26-2 introduces daemon mode with `~/.dan/logs/`.

## Tasks

- [x] 1. **NotificationManager class**
  - [x] 1-1. Create `src/dan/notifications/manager.py`. Subscribes to `GlobalEventBus`.
  - [x] 1-2. Filter for notification-worthy events: `run_completed`, `run_failed`, `human_input_needed`.
  - [x] 1-3. Configurable notification channels (enable/disable per channel).
  - [x] 1-4. Singleton per server process.

- [x] 2. **macOS Notification Center**
  - [x] 2-1. Create `src/dan/notifications/macos.py`. Use `osascript -e 'display notification ...'` for basic notifications.
  - [x] 2-2. Fall back to `terminal-notifier` (Homebrew) if available (supports click actions).
  - [x] 2-3. Title: "DAN: Run completed" / "DAN: Run failed" / "DAN: Input needed". Body: workflow name + run summary.
  - [x] 2-4. Detect macOS via `sys.platform`.

- [x] 3. **Terminal bell (CLI-side)**
  - [x] 3-1. Create `src/dan/notifications/terminal.py` as a tiny helper used by `cli/chat.py` and `cli/run.py`, not the daemon-side `NotificationManager`.
  - [x] 3-2. Ring `\a` (BEL) on `run_completed`, `run_failed`, and `human_input_needed` when the local CLI surface opts in via `DAN_NOTIFY_BELL=1`.
  - [x] 3-3. Do not rely on server-side "active terminal" detection; the current gateway/activity layer tracks connected surfaces, not OS focus.

- [x] 4. **Webhook callback**
  - [x] 4-1. Create `src/dan/notifications/webhook.py`. POST JSON payload to user-configured URL (`DAN_NOTIFY_WEBHOOK_URL`).
  - [x] 4-2. Payload should include the stable fields already present on gateway/run events when available: `{event_type, run_id, workflow_id|workflow_name, status, message, timestamp, surface_id}`.
  - [x] 4-3. Async httpx call with 10s timeout. Retry once on failure.
  - [x] 4-4. Supports custom headers via `DAN_NOTIFY_WEBHOOK_HEADERS` (JSON string).

- [x] 5. **Configuration**
  - [x] 5-1. Notification preferences in `~/.dan/notifications.json` or env vars.
  - [x] 5-2. Channels: `macos` (default on macOS), `bell` (default off), `webhook` (default off).
  - [x] 5-3. Per-event-type overrides (e.g., only notify on failures).
  - [x] 5-4. `NotificationConfig` Pydantic model.

- [x] 6. **GlobalEventBus wiring**
  - [x] 6-1. In `app.py` lifespan (or 26-2 daemon startup), instantiate `NotificationManager`, subscribe it to `GlobalEventBus`, and give it a stable subscriber ID for clean teardown.
  - [x] 6-2. Unsubscribe on shutdown.

- [x] 7. **Tests** — 74 tests across 5 files, all passing
  - [x] 7-1. Unit tests for NotificationManager (mock event bus). — 12 tests in `test_manager.py`
  - [x] 7-2. macOS notifier (mock subprocess). — 13 tests in `test_macos.py`
  - [x] 7-3. Webhook (mock httpx). — 11 tests in `test_webhook.py`
  - [x] 7-4. Config loading. — 19 tests in `test_config.py`
  - [x] 7-5. Terminal bell. — 19 tests in `test_terminal.py`
  - [x] 7-6. No real OS notification tests.

## Files to Touch

| File | Changes |
|------|---------|
| `src/dan/notifications/__init__.py` | New package init |
| `src/dan/notifications/manager.py` | New NotificationManager |
| `src/dan/notifications/macos.py` | New macOS notifier |
| `src/dan/notifications/terminal.py` | New terminal-bell helper shared by CLI surfaces |
| `src/dan/notifications/webhook.py` | New webhook callback |
| `src/dan/notifications/config.py` | New NotificationConfig model |
| `src/dan/server/app.py` | Wire NotificationManager in lifespan |
| `src/dan/cli/chat.py` | Trigger optional bell on chat/run events |
| `src/dan/cli/run.py` | Trigger optional bell on local/server run completion/input-needed events |
| `tests/test_notifications/` | New server-side notification tests |
| `tests/test_cli/test_chat.py` | Resume/profile helper tests added in integration pass (notifications exercised in server + channel tests) |

## Decisions

- `TerminalBellNotifier` also added as a channel adapter so the bell can optionally be used by `NotificationManager` (server-side), not only as a standalone CLI helper.
- Terminal bell emission writes to `stderr` so structured `stdout` output modes (JSON/JSONL) stay parseable.
- `httpx` is lazy-imported inside `WebhookNotifier._post()` to avoid import-time dependency when webhook is disabled.
- `load_notification_config()` applies env vars on top of file config, so env vars always take priority.
- `app.py` now owns NotificationManager lifecycle: create once after `init_gateway()`, subscribe to gateway `GlobalEventBus`, and stop/unsubscribe during lifespan shutdown.

## Notes

- Effort: ~2 days.
- Depends on 26-1 (server lifecycle) and 26-2 (daemon mode for always-on notifications).
- Server-side notifications should stay daemon-safe and surface-agnostic; terminal bells belong to the CLI processes that already consume those events.
