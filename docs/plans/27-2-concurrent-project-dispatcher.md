# 27-2: Concurrent Project Dispatcher

**Parent:** [27-async-message-dispatch](27-async-message-dispatch.md)
**Status:** completed
**Goal:** Process messages for different projects concurrently while serializing messages within the same project/task, so independent tasks run in parallel and follow-ups see prior results.

## Context

Today `Concierge.process()` is called once per message and runs to completion before the next message starts. The `ProjectMessageQueue` returns `QueueDecision.QUEUED` for same-task messages but the drain is serial. Cross-project messages that return `QueueDecision.PARALLEL` still execute serially because the caller awaits each `process()` call.

The fix is a new dispatch layer that:
1. Resolves project/task for each incoming message (fast, no LLM call)
2. If the project has no active processing task → spawn one immediately
3. If the project already has an active task → enqueue the message; the active task drains it after the current response finishes
4. Multiple projects run as concurrent `asyncio.Task`s

### Key invariant

> Within a project/task: strict serial order. Across projects: concurrent.

This means follow-up messages ("also try X for task 1") always see the result of the prior message, because they run after it within the same project's serial queue.

## Tasks

- [x] 1. `ConcurrentDispatcher` class
  - [x] 1-1. New file: `server/concierge/dispatcher.py`
  - [x] 1-2. `ConcurrentDispatcher.__init__(concierge, max_concurrent_projects, max_queue_depth)`
  - [x] 1-3. Internal state: `_active_tasks`, `_project_queues`, `_response_buses`, `_global_queue`
  - [x] 1-4. `dispatch(msg) -> AsyncIterator[ChatStreamEvent]` with immediate/queued paths
  - [x] 1-5. `ChatQueuedEvent(stream_channel_id, correlation_id)` in `chat_manager.py`
  - [x] 1-6. `_process_immediately()` + `_drain_project_queue()` + `_drain_global_overflow()`
  - [x] 1-7. `close()` cancels all active tasks

- [x] 2. Response delivery for queued messages
  - [x] 2-1. Correlation ID + pre-allocated stream_channel_id per queued message
  - [x] 2-2. Response events pushed to `_response_buses[channel_id]`
  - [x] 2-3. Server endpoint pipes response buses to WebSocket stream channels (backward compatible)
  - [x] 2-4. CLI/adapter consume response bus via stream channel subscription

- [x] 3. Refactor `ProjectMessageQueue` interaction
  - [x] 3-1. `_drain_queued_messages()` skips early when `skip_queue=True` (dispatcher always sets it)
  - [x] 3-2. `QueueDecision` block bypassed via existing `skip_queue` metadata
  - [x] 3-3. Queue-vs-parallel decision moved to `ConcurrentDispatcher.dispatch()`
  - [x] 3-4. `ProjectMessageQueue` class kept for backward compatibility

- [x] 4. Wire into `Concierge`
  - [x] 4-1. `build_concierge(enable_dispatcher=True)` returns `(Concierge, ConcurrentDispatcher)` tuple
  - [x] 4-2. `app.py` uses `_dispatcher.dispatch()`, `chat_factory.py` stores dispatcher on `ChatServices`
  - [x] 4-3. `Concierge.process()` unchanged — dispatcher wraps it
  - [x] 4-4. `enable_dispatcher=False` returns `Concierge` only (backward compatible)

- [x] 5. Concurrency guards
  - [x] 5-1. `max_concurrent_projects` (default 5) with global overflow queue
  - [x] 5-2. Per-project queue depth limit (default 20) → "too many pending messages"
  - [x] 5-3. Task cleanup on error via `try/finally` in `_process_immediately()`

- [x] 6. Tests (7 total)
  - [x] 6-1. `test_different_projects_run_concurrently`
  - [x] 6-2. `test_same_project_serialized`
  - [x] 6-3. `test_max_concurrent_projects_overflow`
  - [x] 6-4. `test_task_crash_does_not_block_queue`
  - [x] 6-5. `test_close_cancels_tasks`
  - [x] 6-6. `test_three_message_burst_mixed_parallelism` — 3-message burst integration test
  - [x] 6-bonus. `test_queued_message_gets_response_bus`

## Design notes

### Why a wrapper, not inline in `Concierge.process()`

`Concierge.process()` is already complex (solver path, handler dispatch, pending action resolution, etc.). Adding asyncio task management inside it would make it harder to test and reason about. The `ConcurrentDispatcher` is a clean separation: it manages **when** messages process, while `Concierge` manages **how** each message processes.

### Response delivery challenge

The trickiest part is delivering responses for queued messages back to the right caller:

- **Server mode**: Each HTTP POST gets a `stream_channel_id`. If the message is queued, the caller still needs to receive the response eventually. Options: (a) the POST blocks until the queued message is processed (simple but defeats the purpose for the caller), (b) the POST returns immediately with a correlation ID and the client subscribes to a WebSocket channel for the response (preferred).
- **CLI mode**: The REPL is a single terminal. Responses for concurrent projects interleave on stdout. Project labels (`[Jarvis - Task A]`, `[Jarvis - Task B]`) disambiguate.
- **Adapter mode**: Each messaging conversation has its own `external_id`. Responses are sent back to the correct conversation via `adapter.send_prompt(external_id, ...)`.

### Interaction with existing queue

The existing `ProjectMessageQueue` and `_drain_queued_messages()` are superseded by the dispatcher's per-project queuing. The old queue's `QueueDecision` enum semantics are preserved but execution is now concurrent instead of serial-drain. When the dispatcher is wired, all messages pass through `dispatch()` with `skip_queue=True` metadata, so the old queue block in `Concierge.process()` is a no-op.

### Asyncio safety

`dispatch()` calls `context_resolver.resolve()` and `classify_intent()` — both fully synchronous. Python asyncio is cooperative (single-threaded), so two concurrent `dispatch()` calls cannot interleave between context resolution and `_active_tasks` bookkeeping. The first `await` is inside `concierge.process()`, which is where actual concurrency begins (correct: different projects run their `process()` calls concurrently).

### Pending action interaction

If message 1 sets a "confirm" or "clarify" pending action on a project, and message 2 arrives for the same project, message 2 is queued. When it drains, `Concierge.process()` calls `_resolve_pending_follow_up()`, which checks whether message 2 is a confirmation answer (e.g., "yes"/"no") or a clarification response. This is correct behavior — serial within a project means follow-ups naturally see pending state left by the prior message.

### Local mode

`LocalChatRuntime` (`cli/chat_local.py`) creates an in-process `Concierge`. The dispatcher pattern applies here too: `LocalChatRuntime` should create a `ConcurrentDispatcher` wrapping its local `Concierge`. This is wired in 27-3 task 4.

## Decisions

- (filled in during execution)

## Notes

- (filled in during execution)
