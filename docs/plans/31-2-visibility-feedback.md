# 31-2: Visibility & Feedback

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** completed
**Goal:** Add cost visibility, notification wiring for all runs, error retry UX, and run status visibility so users get clear feedback during daily use.

## Problem

Workflow runs have cost tracking and notifications, but the chat path does not. Users see raw exception strings on LLM failures, no retry affordance, and no quick way to check run status or session cost. Notifications only fire for gateway-initiated runs, not for REST or chat-initiated runs.

## Tasks

- [x] 1. Chat cost tracking
  - [x] 1-1. In `ChatManager.send_message_with_tools()` (and any path that yields `ChatCompleteEvent`), after each LLM call: call `estimate_cost(model, prompt_tokens, completion_tokens)` from `src/dan/providers/costs.py` using `token_usage` and current model.
  - [x] 1-2. Add `estimated_cost: float | None = None` field to `ChatCompleteEvent` in `src/dan/server/chat_manager.py`.
  - [x] 1-3. Store `estimated_cost` on persisted `ChatMessage` when saving assistant messages (if `ChatStore` / `StoreChatMessage` supports it; otherwise track in session state).
  - [x] 1-4. Add `/cost` fast command in `src/dan/server/concierge/runtime.py` (`_try_fast_command` / `_handle_*`): sum `estimated_cost` of all messages in current thread; return formatted total (e.g. `Session cost: $0.0234`).
  - [x] 1-5. Add `DAN_SHOW_COST=1` support: when set, append cost footer to adapter responses (e.g. `[~$0.0012]`) when emitting `ChatCompleteEvent`. Wire in adapter render path or concierge output formatting.
  - [x] 1-6. Extend `_FAST_COMMAND_PREFIXES` in `runtime.py` and `_BYPASS_PREFIXES` in `dispatcher.py` to include `/cost`.

- [x] 2. Notification wiring for chat-initiated runs
  - [x] 2-1. In `src/dan/server/app.py`: after any `rm.start_run()` (REST endpoints), spawn a relay task that subscribes to `RunManager.subscribe(run_id)` and broadcasts events to `GlobalEventBus` (mirror `_relay_run_events_to_bus` from `gateway/router.py`). Use `_require_bus()` or equivalent to get the bus; surface_id can be derived from request or left empty.
  - [x] 2-2. In `src/dan/server/capability_handlers.py` `handle_start_run`: after successful `start_run`, relay run events to `GlobalEventBus`. Add `event_bus: Any = None` to `CapabilityContext` in `capability_registry.py` and populate it in `app.py` lifespan so the handler can access it. Alternatively: inject a relay callback into `RunManager` or use a shared event relay service.
  - [x] 2-3. Add "Response ready" notification for long chat turns: in concierge `process()` or dispatcher, track turn start time; when a `ChatCompleteEvent` is yielded after > 30s, call `NotificationManager` (or broadcast `chat_turn_completed` to bus). Add `chat_turn_completed` to `NOTIFICATION_EVENTS` in `src/dan/notifications/manager.py` if needed, or reuse existing channel with a new event payload.
  - [x] 2-4. Ensure `NotificationManager` receives events from the same `GlobalEventBus` used by app (already wired in lifespan). Verify `run_completed`, `run_failed`, `human_input_needed` fire for chat-initiated runs.

- [x] 3. Error retry UX in chat
  - [x] 3-1. In `ChatManager.send_message_with_tools()` (and concierge paths that call LLM): detect transient errors (rate limit, timeout, network) by exception type/message. Use a small helper (e.g. `_is_transient_error(exc)`) that checks for `RateLimitError`, `TimeoutError`, `asyncio.TimeoutError`, `ConnectionError`, `APIConnectionError`, etc.
  - [x] 3-2. For transient errors: auto-retry once with exponential backoff (e.g. 2–5s delay). Log retry attempt. If retry fails, fall through to user-facing error.
  - [x] 3-3. For non-transient errors: format a user-friendly message instead of raw `str(exc)`. Map common errors (e.g. "Invalid API key", "Model not found") to readable text. Use `ChatErrorEvent(error=user_friendly_message)`.
  - [x] 3-4. Add `/retry` fast command: re-send the last user message in the current thread. Store last user message in concierge state or chat store; on `/retry`, replay it through the normal flow. Add to `_FAST_COMMAND_PREFIXES` (runtime), `_BYPASS_PREFIXES` (dispatcher), and `_try_fast_command`.
  - [x] 3-5. Ensure `/retry` works when previous turn ended in `ChatErrorEvent` (no assistant message to replay).

- [x] 4. Run status visibility from chat
  - [x] 4-1. Add `/status` fast command in `src/dan/server/concierge/runtime.py`: call `handle_get_run_status` / `handle_list_active_runs` / `handle_get_activity` via `CapabilityContext` (or equivalent). Format output: active runs, queued messages, current project, model name, cost so far, memory stats.
  - [x] 4-2. Include in status output: `list_active_runs` result, `get_activity` snapshot, current model (from `get_config` or session), session cost total, memory stats (from `MemoryKernel` or `/memory-stats` logic).
  - [x] 4-3. Verify existing `/status` handling: `StatusHandler` in `handlers.py` may already handle status queries via the classifier path. The new `/status` fast command should consolidate with or replace the handler path — fast commands bypass the classifier, so `/status` as a fast command is strictly better for latency. Add to `_FAST_COMMAND_PREFIXES` and `_BYPASS_PREFIXES` (dispatcher). If `StatusHandler` becomes unreachable, note it for 28-5 dead code cleanup.

- [x] 5. Tests
  - [x] 5-1. Test cost estimation in chat path: mock LLM response with `token_usage`, assert `ChatCompleteEvent.estimated_cost` is set and non-zero for known model.
  - [x] 5-2. Test `/cost` fast command: send messages, then `/cost`, assert response contains session total.
  - [x] 5-3. Test notification on chat-initiated run: start run via capability (e.g. "run workflow X"), assert `run_completed` or `run_failed` reaches `NotificationManager` (mock or spy).
  - [x] 5-4. Test error retry: mock transient error (e.g. `RateLimitError`), assert retry occurs and eventually succeeds or yields friendly message.
  - [x] 5-5. Test non-transient error: mock permanent error, assert user-friendly message in `ChatErrorEvent`, not raw exception.
  - [x] 5-6. Test `/retry` fast command: send message, get error, send `/retry`, assert last message is re-sent.
  - [x] 5-7. Test `/status` fast command: with active run, send `/status`, assert output includes run info, model, cost, memory stats.

## Decisions

- (filled in during execution)

## Notes

- `CostTracker` in `src/dan/providers/cost_tracker.py` is used for workflow runs; chat path can use `estimate_cost()` directly without full `CostTracker` unless we want budget enforcement for chat.
- `_relay_run_events_to_bus` lives in `gateway/router.py`; consider extracting to a shared helper (e.g. `dan/server/run_relay.py`) for reuse in app.py and capability_handlers.
- `DAN_SHOW_COST` is additive; default off to avoid noise.

## Estimate

~1.5 days
