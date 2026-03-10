# 30-2: Multi-Bot Group Chat

**Parent:** [30-telegram-platform](30-telegram-platform.md)
**Status:** completed
**Goal:** Multiple DAN bots in a single Telegram group, each focused on its own projects, with intelligent message routing and friend support.

## Architecture

```
                        Telegram Group Chat
                     ┌──────────────────────┐
                     │  @ResearchBot        │
                     │  @DataBot            │
                     │  @DAN (default)      │
                     │  👤 Owner            │
                     │  👤 Friend A         │
                     │  👤 Friend B         │
                     └──────────┬───────────┘
                                │
                     ┌──────────┴───────────┐
                     │     BotFleet         │
                     │  ┌───────────────┐   │
                     │  │ MessageRouter │   │
                     │  │ (who answers?)│   │
                     │  └───────┬───────┘   │
                     │    ┌─────┼─────┐     │
                     │    │     │     │     │
                     │  Bot A Bot B Bot C   │
                     │  (own   (own  (own   │
                     │  token) token) token) │
                     └──────────┬───────────┘
                                │
                     ┌──────────┴───────────┐
                     │     Concierge        │
                     │  (shared instance)   │
                     │  memory / tools /    │
                     │  workflows           │
                     └──────────────────────┘
```

## Core Model

### BotInstance

```python
@dataclass
class BotInstance:
    name: str                    # display name (e.g. "ResearchBot")
    token: str                   # BotFather token
    projects: list[str]          # assigned project names/keywords
    personality: str             # personality prompt snippet
    is_default: bool             # responds when no other bot matches
    adapter: TelegramAdapter     # the running adapter instance
    bot_username: str            # @username from Telegram (resolved at startup)
```

### BotFleet

```python
class BotFleet:
    bots: dict[str, BotInstance]
    router: MessageRouter
    concierge: Concierge          # shared
    dispatcher: ConcurrentDispatcher  # shared
```

### MessageRouter

Decides which bot responds to a group message. Returns exactly one bot (or None to ignore).

## Tasks

### 1. BotFleet coordinator (`src/dan/adapters/telegram_fleet.py`)
- [x] 1-1. `BotFleet` class: holds `dict[str, BotInstance]`, `MessageRouter`, shared `httpx.AsyncClient`
- [x] 1-2. `async start()`: initialize all bot adapters (each has its own `Application` instance), resolve `bot_username` via `bot.get_me()`, start polling for each. Uses the lower-level `initialize()` + `start()` + `updater.start_polling()` pattern (not `run_polling()` which blocks) so multiple bots share one event loop.
- [x] 1-3. `async stop()`: stop all adapters gracefully (reverse order of start)
- [x] 1-4. **Message deduplication:** maintain a bounded `seen_messages: set[tuple[int, int]]` (chat_id, message_id) with TTL eviction (60 seconds). When any bot's handler fires, check seen set first. Only the first bot to process a message_id runs the `MessageRouter`. Other bots skip. This prevents N router evaluations per message.
- [x] 1-5. Each bot registers a message callback that checks dedup → runs `MessageRouter` → dispatches only if this bot is the selected one
- [x] 1-6. `BotFleet.load_from_config(config_path)` — load `~/.dan/telegram/config.json` (see 30-4)
- [x] 1-7. All bots share a single `httpx.AsyncClient` to the server (connection pooling)
- [x] 1-8. Deployment model: the fleet is a **standalone process** that communicates with `dan-serve` via HTTP (same as `dan-adapter`). It does NOT run inside the server process. The server's own `Concierge` / `ConcurrentDispatcher` handles routing, memory, and project serialization server-side.

### 2. MessageRouter (`src/dan/adapters/telegram_router.py`)
- [x] 2-1. `MessageRouter` class with `route(message, sender, chat_type, bots) -> BotInstance | None`
- [x] 2-2. Rule 1 — **@mention**: if message contains `@BotUsername`, route to that bot. Highest priority.
- [x] 2-3. Rule 2 — **Forum topic**: if message is in a topic thread assigned to a bot, route to that bot.
- [x] 2-4. Rule 3 — **Project keyword**: score message text against each bot's `projects` list. Route to highest-scoring bot (simple keyword overlap, no LLM needed).
- [x] 2-5. Rule 4 — **Default bot**: if no rule matches, route to the bot marked `is_default=True`.
- [x] 2-6. Rule 5 — **DM (private chat)**: always route to the bot that received the DM. In DMs, project scope does NOT apply — the bot responds to any topic (project assignments are for group routing only).
- [x] 2-7. Anti-collision: the router returns **exactly one** bot. Combined with the fleet's message dedup (task 1-4), this ensures exactly one bot processes each message.
- [x] 2-8. Anti-loop: never route messages from bots (check `message.from_user.is_bot`). This prevents bots responding to each other's messages.
- [x] 2-9. `route()` returns `None` if the message should be ignored (e.g., from a bot, or from a non-allowed user in a restricted group).
- [x] 2-10. **Multi-group support:** the router operates per-message using the message's `chat_id`. Different groups can have different topic_maps. The fleet config supports multiple group entries (not just one). If a group isn't in the config, the default bot handles all messages in it.

### 3. Per-bot identity and prompts
- [x] 3-1. Each bot gets its own system prompt section injected into the concierge:
  ```
  You are {bot_name}, a {personality} assistant.
  You focus on these projects: {projects}.
  Other bots in this group: {other_bot_names_and_roles}.
  Only respond to messages routed to you.
  ```
- [x] 3-2. The `surface` parameter passed to `/api/chat/message` includes the bot name: `"telegram:{bot_name}"`
- [x] 3-3. Each bot uses its own `DAN_BOT_NAME` equivalent for reply prefixes
- [x] 3-4. Add `"telegram:{bot_name}"` to `SURFACE_HINTS` (inherits base Telegram hints + bot-specific personality)

### 4. Friend interactions
- [x] 4-1. Any human in the group can send messages. Routing logic applies equally — no owner vs. friend distinction.
- [x] 4-2. If `allowed_chat_ids` is set on the fleet config, restrict to those group chats only. Individual user filtering is NOT applied in groups (anyone in an allowed group can talk).
- [x] 4-3. Per-bot `allowed_users` (optional) — if set, only these Telegram user IDs can trigger that specific bot. Default: empty (everyone).
- [x] 4-4. Friends see the same bot responses as the owner. No private side-channels within the group.

### 5. Cross-bot awareness
- [x] 5-1. Each bot's message callback receives all group messages (requires disabling privacy mode in BotFather — document this in setup guide)
- [x] 5-2. The `MessageRouter` runs in the fleet coordinator, not in individual bots. Only one process evaluates each message.
- [x] 5-3. When a bot responds, it can reference what other bots have said (shared conversation history in the concierge's project context)
- [x] 5-4. A bot can suggest involving another bot: "For the data analysis part, try asking @DataBot" — this is a natural language suggestion, not programmatic delegation.

### 6. Server-side integration
- [x] 6-1. All bots POST to the same `dan-serve` chat endpoint (`/api/chat/message`). The server's `ConcurrentDispatcher` handles per-project serialization, so messages about the same project are serialized even if from different bots. The fleet does NOT directly access `ConcurrentDispatcher` — it's a server-side component.
- [x] 6-2. The `surface` parameter includes the bot name: `"telegram:{bot_name}"` (e.g., `"telegram:research-bot"`). The server uses this to select the correct `SURFACE_HINTS` and inject bot-specific personality into the system prompt.
- [x] 6-3. The `thread_id` parameter uses a composite key: `"{chat_id}:{thread_id|main}:{bot_name}"` so each bot maintains its own conversation history per chat/topic, even though they share a server.
- [x] 6-4. Memory items created during a bot's response are tagged with the bot name via the surface parameter for provenance.

### 7. Group message flow (end-to-end)
- [x] 7-1. User sends "@DataBot analyze this CSV" in group
- [x] 7-2. All bots receive the message (privacy mode off)
- [x] 7-3. `BotFleet.on_group_message()` runs `MessageRouter.route()` → selects DataBot
- [x] 7-4. Only DataBot's callback fires `dispatch_to_server()`
- [x] 7-5. Server processes via concierge with DataBot's identity/personality
- [x] 7-6. Response sent back through DataBot's `bot.send_message()`
- [x] 7-7. Other bots do nothing for this message

### 8. Tests
- [x] 8-1. Unit tests for `MessageRouter`: @mention routing, topic routing, keyword scoring, default fallback, DM routing, bot-message filtering
- [x] 8-2. Unit tests for `BotFleet`: start/stop lifecycle, config loading, callback wiring
- [x] 8-3. Integration test: multi-bot group message → correct bot responds (mock Telegram API)
- [x] 8-4. Integration test: friend message → routed and responded (no owner-only restriction)
- [x] 8-5. Anti-collision test: same message → only one bot responds

## Decisions

- **Single process, multiple bot tokens.** All bots run in the same Python process, sharing one event loop. Each bot has its own `Application` instance from `python-telegram-bot` using the lower-level `initialize()` + `start()` + `updater.start_polling()` pattern (not `run_polling()` which is blocking). This avoids IPC complexity and lets bots share the dedup set and router directly.
- **Fleet is standalone, concierge is server-side.** The fleet process talks to `dan-serve` via HTTP, just like `dan-adapter`. All concierge/memory/dispatcher logic runs on the server. The fleet only handles Telegram I/O, message routing, and dedup.
- **Router + dedup runs before dispatch.** The decision about which bot responds happens before any HTTP call to the server. The fleet-level dedup set ensures the router runs at most once per message. This is cheaper and more predictable than having all bots start processing and then racing.
- **No bot-to-bot delegation (yet).** A bot can suggest another bot in its reply text, but there's no programmatic "hand off this message to @OtherBot." That's a future extension.
- **Privacy mode must be disabled.** Each bot needs privacy mode turned off in BotFather (`/setprivacy` → Disable) so it receives all group messages, not just commands and @mentions. The setup guide (30-4) will walk through this. Caveat: with privacy mode off, every group message triggers a Telegram webhook/poll delivery to every bot. The fleet's dedup set handles this efficiently — one dict lookup per bot per message.

## Notes

- `python-telegram-bot` v21 supports running multiple `Application` instances in the same event loop. Each application has its own updater and bot instance. Tested in the library's examples.
- Telegram rate limits: 30 messages per second to the same group, 20 messages per minute per chat for non-inline. With throttled progress updates this isn't a concern.
- Forum topics are identified by `message.message_thread_id`. Topic creation requires admin rights. The fleet can request admin or use existing topics.
- Fleet conversation/workflow keys must include `message_thread_id` when present so multiple project topics owned by the same bot do not bleed history into each other.
- Post-ship hardening (2026-03-10): fleet now treats messages authored by known bot usernames as bot-originated even if Telegram omits the normal bot flag, and Telegram send paths retry without `reply_to_message_id` when the replied message no longer exists. This closed one real group-chat loop where a bot could consume its own outgoing message and recursively trigger more replies.
- Follow-up hardening (2026-03-10): the fleet now pre-seeds its dedup cache with outbound Telegram `message_id`s returned by `send_message` / `send_or_edit`, so if Telegram later re-delivers that same bot-authored message via polling, it is dropped before routing. Bot-id matching is also used as a second author-detection signal when usernames are missing from the update payload.
- Follow-up hardening (2026-03-10, cross-bot ingress dedup): fresh live traces showed the same human group message can arrive at each fleet bot with a different local `message_id`, so `(chat_id, message_id)` is not sufficient as the only dedup key across bots. The fleet now also uses a bot-agnostic inbound fingerprint derived from shared message metadata (chat/thread, sender, timestamp, visible content/media identity) for non-private messages, so all four bot-local copies collapse to one routed dispatch before the default bot can reply four times.
