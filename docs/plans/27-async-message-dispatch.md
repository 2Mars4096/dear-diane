# 27: Async Message Dispatch

**Status:** completed
**Goal:** Process independent user messages concurrently across projects while serializing within the same task — making the router feel like a parallel assistant, not a serial queue.

## Motivation

Today the concierge processes messages one at a time. When a user sends three rapid messages about unrelated topics, the second and third are queued behind the first and replayed serially after it finishes. This feels sluggish and unnatural — especially on always-on surfaces (WhatsApp, Telegram, `dan up`) where the user fires off thoughts as they come.

The correct behavior:
- **Different tasks** → process concurrently, respond as each finishes
- **Same task, additive/follow-up** → serialize: wait for the prior response, inject its result into context, then process the follow-up
- **Status checks** → always immediate (already handled)

### Current gaps

| Component | Gap |
|---|---|
| `Concierge.process()` | Serial: processes one `SurfaceMessage` at a time. `_drain_queued_messages()` replays queued messages serially after the active one completes. |
| `ProjectMessageQueue` | Returns `QueueDecision.QUEUED` for same-task messages but execution is serial drain. No concurrent dispatch for `PARALLEL` decisions. |
| `dan-chat` CLI | `_InputQueue` collects input during streaming, but `_replay_queued_through_api()` replays all queued messages serially after current response finishes. |
| Adapters | Messaging platforms deliver messages concurrently (async callbacks), but `_run_adapter_chat_mode` processes them serially per conversation. |
| Server endpoint | `POST /api/chat/message` is async (FastAPI), but the concierge path inside awaits the full producer task — multiple concurrent POSTs will contend on the concierge's serial queue. |
| Bot identity | `[DAN` is hardcoded in ~15 locations across runtime, handlers, progress, promotion, adapter. Should be configurable. |

## Existing Infrastructure

| Component | Location | Relevance |
|---|---|---|
| `Concierge` | `server/concierge/runtime.py` | Main dispatch loop — needs concurrent task management |
| `ProjectMessageQueue` | `server/concierge/queue.py` | Queue decisions (IMMEDIATE/QUEUED/PARALLEL) — needs to drive concurrent execution |
| `ProjectContextResolver` | `server/concierge/context_resolver.py` | Resolves project/task for each message — used to determine independence |
| `classify_intent` | `server/concierge/classifier.py` | Intent classification — runs per message before dispatch |
| `_InputQueue` | `cli/chat.py` | CLI input queue during streaming — needs parallel replay |
| `_run_adapter_chat_mode` | `cli/adapter.py` | Adapter chat loop — needs concurrent message dispatch |
| `chat_message` endpoint | `server/app.py` | Server entry point — already async, needs to work with concurrent concierge |
| `_format_reply_label` | `server/concierge/runtime.py` | Reply prefix — hardcodes `[DAN` |
| `_DAN_PREFIX` | `cli/adapter.py` | Adapter prefix — hardcodes `[DAN]` |

## Sub-Plans

| # | Sub-Plan | Scope | Effort | Dependencies |
|---|----------|-------|--------|--------------|
| [27-1](27-1-configurable-bot-identity.md) | Configurable Bot Identity | Replace all hardcoded `[DAN` with configurable bot name from env/config. Single source of truth for prefix formatting. | ~0.5 day | None |
| [27-2](27-2-concurrent-project-dispatcher.md) | Concurrent Project Dispatcher | Core concierge change: per-project asyncio task pool, same-task serialization, cross-project parallelism. New `ConcurrentDispatcher` wrapping `Concierge`. | ~2 days | 27-1 (uses configurable prefix) |
| [27-3](27-3-surface-async-acceptance.md) | Surface Async Acceptance | Make CLI, adapters, and server endpoint accept and dispatch messages without blocking on the current response. CLI parallel replay, adapter concurrent dispatch. | ~1.5 days | 27-2 (depends on concurrent dispatcher) |

**Total effort:** ~4 days

## Dependencies / Sequencing

```
27-1 (Bot Identity) ← standalone, do first
  └→ 27-2 (Concurrent Dispatcher) ← core runtime change
       └→ 27-3 (Surface Async Acceptance) ← wire surfaces to the dispatcher
```

## Architecture

### Message flow (before)

```
Surface → POST /api/chat/message → Concierge.process() → serial
                                         ↓
                                   QueueDecision.QUEUED → "Queued" reply
                                         ↓
                                   _drain_queued_messages() → serial replay
```

### Message flow (after)

```
Surface → POST /api/chat/message → ConcurrentDispatcher.dispatch()
                                         ↓
                                   resolve project/task + classify
                                         ↓
                              ┌──── different project? ────┐
                              │                            │
                          yes │                        no  │
                              ↓                            ↓
                    spawn new asyncio.Task      same task active?
                    (parallel processing)            ↓        ↓
                                                yes  │    no  │
                                                     ↓        ↓
                                              enqueue for   process
                                              serial drain  immediately
                                              (after current
                                               response done)
```

### Per-project task tracking

```python
class ConcurrentDispatcher:
    _active_tasks: dict[str, asyncio.Task]     # project_id → running task
    _project_queues: dict[str, asyncio.Queue]  # project_id → pending messages
    _concierge: Concierge

    async def dispatch(self, msg: SurfaceMessage) -> AsyncIterator[ChatStreamEvent]:
        context = resolve(msg)          # sync — no interleave risk
        project_id = context.project.project_id

        if project_id in self._active_tasks:
            # Same project → queue for serial processing
            channel_id = f"queued-{uuid.uuid4().hex[:10]}"
            await self._project_queues[project_id].put((msg, channel_id))
            yield ChatQueuedEvent(stream_channel_id=channel_id)
            return
        else:
            # New/different project → process immediately in a new task
            task = asyncio.create_task(self._process_and_drain(project_id, msg))
            self._active_tasks[project_id] = task
            # yield events from concierge.process() directly
```

Note: `project_id` alone is a sufficient key because project IDs are UUIDs (globally unique). The context resolver already creates projects scoped to a surface, so two surfaces never share a `project_id`.

### Response delivery

Each concurrent task produces `ChatStreamEvent`s that need to reach the correct surface:

1. **Server mode**: Each POST gets a `stream_channel_id`. For queued messages, the POST still returns a `stream_channel_id` (backward compatible) plus `"status": "queued"`. The client subscribes to the WebSocket stream channel as usual — events arrive when the queued message is eventually processed. Existing clients that ignore the `status` field keep working.
2. **Editor ChatPanel**: Same as server mode — React frontend POSTs and subscribes to the stream. No frontend changes needed.
3. **CLI / Adapter mode**: Responses are printed/sent with project labels. Only the "active" response streams token-by-token; background responses print as complete blocks to avoid interleaving.
4. **`dan up`**: Starts the server and drops into `dan-chat` — covered by CLI changes.

## Key Decisions

- **No batch window.** Messages process immediately. The only serialization is within the same project/task.
- **Project-level parallelism, task-level serialization.** Different projects run concurrently. Within a project, messages queue behind the active one and replay with the prior result in context.
- **Configurable bot name.** `DAN_BOT_NAME` env var (default `"DAN"`). All prefix formatting reads from one place.
- **Responses are always project-labeled** when multiple projects are active. This is already the behavior of `_format_reply_label()` — the change is that it will trigger more often since multiple projects will genuinely be active simultaneously.
- **Queued same-task messages see the prior result.** When message 2 runs after message 1 finishes for the same task, message 1's response is already in the task's turn history, so message 2's processing has full context.

## Success Criteria

- Sending "help with task A" and "what about task B" in quick succession → both process concurrently, responses arrive independently with project labels.
- Sending "do task A" then "also try X for task A" → second message waits for first to finish, then processes with the first result in context.
- `DAN_BOT_NAME=Jarvis` → all prefixes show `[Jarvis - <Project>]` instead of `[DAN - <Project>]`.
- No regression in existing serial behavior — single messages still work identically.
- Works across all surfaces: `dan-chat`, `dan up`, Telegram adapter, WhatsApp adapter, editor ChatPanel (no frontend changes needed — server-side dispatch is transparent).
