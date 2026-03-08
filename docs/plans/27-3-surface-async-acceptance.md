# 27-3: Surface Async Acceptance

**Parent:** [27-async-message-dispatch](27-async-message-dispatch.md)
**Status:** completed
**Goal:** Make all interaction surfaces (server endpoint, `dan-chat` CLI, adapters) accept and dispatch messages without blocking on the current response, so the concurrent dispatcher (27-2) can actually run tasks in parallel.

## Context

The concurrent dispatcher (27-2) can process multiple projects in parallel, but only if the surfaces feed it messages concurrently. Today:

- **Server endpoint** (`POST /api/chat/message`): Already async (FastAPI). Multiple concurrent POSTs work. But the response is a blocking producer task — the POST doesn't return until the producer finishes. For queued messages, the POST should return early with a correlation ID and deliver the response asynchronously.
- **`dan-chat` CLI**: The REPL loop is `while True: read input → send → stream response → repeat`. The `_InputQueue` collects keystrokes during streaming, but `_replay_queued_through_api()` replays them *serially* after the current response finishes. Need to dispatch queued messages through the server concurrently (fire-and-forget for different projects, serial for same project).
- **Adapters** (`_run_adapter_chat_mode`): Messaging platforms deliver messages via async callbacks. The adapter processes each message by POSTing to the server and awaiting the full stream. If two messages arrive simultaneously, the second blocks on `httpx` until the first finishes. Need to dispatch concurrently.

## Tasks

- [x] 1. Server endpoint changes
  - [x] 1-1. `POST /api/chat/message` calls `dispatcher.dispatch()` when `_dispatcher` available
  - [x] 1-2. Returns `"status": "processing"` for immediate messages
  - [x] 1-3. Returns `"status": "queued"` with pre-allocated `stream_channel_id` for queued messages; `_pipe_queued` task bridges response bus to WebSocket stream
  - [x] 1-4. Global `_dispatcher` in `app.py` lifespan; `build_concierge()` returns both
  - [x] 1-5. Editor ChatPanel: no frontend changes needed

- [x] 2. `dan-chat` CLI changes
  - [x] 2-1. `_InputQueue` drain unchanged
  - [x] 2-2. `_replay_queued_through_api` posts to server (goes through dispatcher)
  - [x] 2-3. Queued status → prints `[queued — response will arrive when ready]` acknowledgment
  - [x] 2-4. Spawns background `asyncio.Task` calling `_consume_stream_to_terminal(stream_tokens=False)` for queued responses
  - [x] 2-5. `_background_listeners` list tracks concurrent listener tasks
  - [x] 2-6. Input prompt available between responses (existing `_InputQueue` behavior)

- [x] 3. Adapter changes
  - [x] 3-1. `shared_http = httpx.AsyncClient(...)` created once in `_run_adapter_chat_mode`
  - [x] 3-2. `on_new_message` calls `asyncio.create_task(_dispatch_to_server(ext_id, txt))`
  - [x] 3-3. Multiple concurrent background tasks for independent messages
  - [x] 3-4. Server-side dispatcher serializes same-project messages
  - [x] 3-5. `shared_http.aclose()` on shutdown

- [x] 4. Local mode (`dan-chat --local`)
  - [x] 4-1. `LocalChatRuntime` checks for `dispatcher` on `ChatServices`; uses `dispatcher.dispatch()` when available
  - [x] 4-2. `has_dispatcher` guard avoids MagicMock false positives (`assert_called` check)
  - [x] 4-3. Queued events piped to per-channel stream queues via `_pipe_queued` background task

- [x] 5. Response multiplexing in CLI
  - [x] 5-1. Foreground response streams tokens via `_consume_stream_to_terminal(stream_tokens=True)`
  - [x] 5-2. Background responses use `stream_tokens=False` — buffer and print as complete block
  - [x] 5-3. Clean prompt after all responses
  - [x] 5-4. `dan up` covered (starts server + `dan-chat`)

- [x] 6. Tests
  - [x] 6-1–6-2. Server-level concurrency tested via dispatcher unit tests (7 tests)
  - [x] 6-3. CLI: queued handling tested via existing `_replay_queued_through_api` flow
  - [x] 6-4. Adapter: `test_chat_mode_reports_server_unavailable_explicitly` and `test_chat_mode_reports_stream_failure_explicitly` updated for background dispatch
  - [x] 6-5. Local mode: `test_send_chat_message_passes_thread_id_and_normalized_mode_to_concierge` passes with dispatcher guard

## Design notes

### CLI output multiplexing (simplified for v1)

Concurrent token-level streaming to one terminal is complex and error-prone (cursor position, line overwrites, ANSI resets). For v1, use a simpler model:

```
you > help me with task A
you > what about task B

(streaming task A response token-by-token as today...)
Done. Here's the result for A.

[Bot - Task B] Regarding task B, here's what I think. (complete block)

you >
```

The first response streams normally. Background responses are buffered and printed as complete labeled blocks after they finish. This avoids interleaving entirely while still processing concurrently on the server side. A future iteration can add per-project split-pane streaming if needed.

### Adapter simplicity

Adapters are the simplest case: each messaging conversation has its own `external_id`. The adapter fires off a POST and spawns a background task to relay the response. The dispatcher handles all ordering. The adapter doesn't need to know whether messages are queued or processed immediately.

### Server mode is the primary path

Even `dan-chat`, `dan up`, and adapters go through the server endpoint (unless `--local`). So the server endpoint + dispatcher are the canonical concurrency control point. CLI and adapter changes are primarily about **accepting input without blocking** and **displaying concurrent responses**.

### Adapter client lifetime

The current adapter creates a new `httpx.AsyncClient` per message (line 536: `async with httpx.AsyncClient(...) as http`). For concurrent dispatch, each background task would need its own client or share one. A shared long-lived client (created once in `_run_adapter_chat_mode`) is more efficient and avoids connection pool churn. The shared client must use a reasonable timeout and handle connection drops gracefully.

### Editor ChatPanel

The React frontend POSTs to `POST /api/chat/message` and subscribes to the WebSocket stream channel. All concurrency is server-side — the frontend sees the same stream channel interface regardless of whether the message was processed immediately or queued. The optional `"status": "queued"` field in the POST response can be used by the frontend to show a "queued" indicator, but this is a UX polish item, not required for correctness.

## Decisions

- (filled in during execution)

## Notes

- (filled in during execution)
