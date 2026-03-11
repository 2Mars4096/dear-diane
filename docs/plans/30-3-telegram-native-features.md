# 30-3: Telegram-Native Features

**Parent:** [30-telegram-platform](30-telegram-platform.md)
**Status:** completed
**Goal:** Leverage Telegram-specific capabilities that other messaging platforms lack — forum topics, message editing for streaming, reactions, polls, large file support, reply threading, bot commands, and Mini Apps.

## Why This Matters

Telegram's Bot API offers features that make it a superior agent surface compared to WhatsApp or email. Using them creates a significantly better UX:

| Feature | WhatsApp | Telegram | Impact |
|---------|----------|----------|--------|
| Message editing | No | Yes | Stream responses by editing in-place instead of sending new messages |
| Reactions | No | Yes (Bot API 7.3+) | Lightweight status: ⏳ → ✅ without a new message |
| Forum topics | No | Yes | Per-project thread organization in a single group |
| File size | 64MB | 50MB upload / 20MB download (2GB via local Bot API server) | Send larger files than WhatsApp |
| Polls | No | Yes (native) | Decision-making, voting, multi-option choices |
| Bot commands menu | No | Yes | Autocomplete in chat, per-bot command lists |
| Reply-to | Limited | Full | Quote any message, create clear threads |
| Code blocks | Limited | Full markdown | Proper code rendering with syntax |
| Inline keyboards | No | Yes | Interactive buttons on messages |
| Pinned messages | No (groups only) | Yes | Pin deliverables and summaries |
| Mini Apps | No | Yes | Embed interactive web content |

## Tasks

### 1. Forum topics — per-project threads
- [x] 1-1. Detect if group has forum mode enabled (`chat.is_forum`)
- [x] 1-2. Auto-create topics for each bot's assigned projects on fleet startup (requires admin rights)
- [x] 1-3. Map `message.message_thread_id` to project names in the `MessageRouter`
- [x] 1-4. When a bot creates a new project during conversation, auto-create a corresponding topic
- [x] 1-5. Store topic_id ↔ project_name mapping in fleet config (persisted)
- [x] 1-6. "General" topic for messages that don't belong to any project
- [x] 1-7. `/topic create <name>` command to manually create a topic and assign to a bot
- [x] 1-8. Fallback: if forum mode is off, operate in flat group mode (no topics, just message routing)

### 2. Message editing for streaming responses
**Pre-requisite:** The current `_run_adapter_chat_mode()` in `adapter.py` uses a **collect-then-send** pattern: it receives all WS events into a list, then calls `_consume_chat_stream_events()` to extract the full reply, then sends it as one message. Streaming edits require **incremental processing** — editing the message as tokens arrive. This requires a new WS event processing path for Telegram (either a Telegram-specific override of `_dispatch_to_server()` or a pluggable stream handler).
- [x] 2-1. Add a `_dispatch_to_server_streaming()` method that processes WS events incrementally instead of collecting them. Reuses the same HTTP POST to get `channel_id`, but processes the WS loop differently.
- [x] 2-2. On first `chat_token` event: send a message with initial text, store `message_id`
- [x] 2-3. As `chat_token` events stream in, batch tokens (every 500ms or 100 chars, whichever comes first) and call `bot.edit_message_text(message_id, accumulated_text)` to update in-place
- [x] 2-4. On `chat_complete`: final edit with the complete response
- [x] 2-5. Rate-limit edits to avoid hitting Telegram's edit rate limit (~30 edits/min per message). Use a debounce timer.
- [x] 2-6. Fallback: if edit fails (message too old, deleted by user, `BadRequest` error), send remaining text as new message
- [x] 2-7. Config: `streaming_edits: bool = True` — when false, uses the existing collect-then-send behavior
- [x] 2-8. For long responses that exceed 4096 chars during streaming: finalize current message (stop editing), start a new message for the remainder

### 3. Reactions for status
- [x] 3-1. On message received: add ⏳ reaction (Bot API `setMessageReaction`)
- [x] 3-2. On response complete: replace with ✅ reaction
- [x] 3-3. On error: replace with ❌ reaction
- [x] 3-4. For long-running tasks: add 🔄 reaction during processing
- [x] 3-5. Config: `use_reactions: bool = True` — disable to skip reactions
- [x] 3-6. Graceful degradation: if bot lacks reaction permissions, silently skip
- [x] 3-7. Per-chat reaction backoff: after the first Telegram `BadRequest` for reactions in a chat, stop retrying reactions in that chat to avoid repeated Bot API 400 noise

### 4. Reply-to threading
- [x] 4-1. When responding to a user message: use `reply_to_message_id` to quote the original
- [x] 4-2. When user replies to a bot message: include the quoted bot message as context in the prompt
- [x] 4-3. Multi-turn threading: if user replies to a reply, build the reply chain as conversation context (up to 5 levels deep)
- [x] 4-4. In group chats: always reply-to the triggering message so it's clear which message the bot is responding to

### 5. Telegram-native polls
- [x] 5-1. New capability tool: `telegram_poll` — creates a Telegram poll in the chat
- [x] 5-2. Parameters: `question: str`, `options: list[str]`, `is_anonymous: bool = False`, `allows_multiple: bool = False`
- [x] 5-3. Wire poll results back to the concierge when the poll closes or has enough votes
- [x] 5-4. Use for HumanNode selection render mode: if options <= 10, create a poll instead of inline keyboard
- [x] 5-5. `PollAnswerHandler` collects votes and resolves pending HumanNode futures

### 6. Large file support
- [x] 6-1. **Upload limits:** Standard Bot API supports up to 50MB for `send_document()`. For files > 50MB, use `InputFile` with streaming upload. The 2GB limit is only available when running a [Local Bot API Server](https://core.telegram.org/bots/api#using-a-local-bot-api-server) — note this as an optional power-user setup.
- [x] 6-2. **Download limits:** `getFile()` API supports up to 20MB. For larger inbound files, the bot receives file metadata but can't download. Inform the user with a clear message: "I can see you sent [filename] (X MB), but Telegram limits bot downloads to 20MB. Please send a smaller file or share via a link."
- [x] 6-3. Send with descriptive caption: filename, size, type
- [x] 6-4. For image results: send as photo (up to 10MB, auto-compressed) with caption, or document (preserves quality) for larger or non-image files

### 7. Bot commands registration
- [x] 7-1. On fleet startup: register commands with Telegram via `bot.set_my_commands()`
- [x] 7-2. Default commands: `/help`, `/status`, `/cancel`, `/find`, `/send`, `/list`, `/show`, `/mcp`
- [x] 7-3. Per-bot custom commands based on project focus (e.g., ResearchBot gets `/search`, DataBot gets `/analyze`)
- [x] 7-4. Bot command scopes: set different command lists for group chats vs DMs (`BotCommandScope`)
- [x] 7-5. Command descriptions for autocomplete: clear one-line descriptions
- [x] 7-6. Telegram-safe command publishing: command menus now skip command names that are invalid for Telegram `set_my_commands()` (for example hyphenated names), avoiding startup `400 Bad Request` errors while keeping richer DAN command names on other surfaces

### 8. Inline keyboards (enhanced)
- [x] 8-1. Already have approval/selection keyboards — extend with quick-action buttons on responses
- [x] 8-2. After a workflow run: "📋 Details | 🔄 Re-run | 📁 Export" keyboard
- [x] 8-3. After a search result: "📄 Read Paper | 📚 Add to Library | ⏭️ Next" keyboard
- [x] 8-4. Callback data routing: prefix with bot name to avoid cross-bot callback conflicts in groups
- [x] 8-5. Auto-expire keyboards: remove inline keyboard after 5 minutes if no interaction

### 9. Pinned messages
- [x] 9-1. When a workflow produces a final deliverable (PDF, report, summary): offer to pin it
- [x] 9-2. `/pin` command: pin the last bot message in the chat
- [x] 9-3. Auto-pin configurable: `auto_pin_deliverables: bool = False`
- [x] 9-4. Requires admin rights — graceful failure if bot isn't admin

### 10. Mini Apps (stretch goal)
- [x] 10-1. Register a Mini App URL that opens the DAN visual editor in a Telegram WebApp panel
- [x] 10-2. Use `MenuButtonWebApp` to add "Open Editor" button to bot menu
- [ ] 10-3. The WebApp communicates with the DAN server via the same API the editor uses
- [x] 10-4. Authentication: use Telegram's `initData` to verify the user
- [ ] 10-5. Scope: read-only workflow viewer first, full editor later

### 11. Tests
- [x] 11-1. Unit tests for forum topic creation/mapping
- [x] 11-2. Unit tests for message editing (streaming simulation)
- [x] 11-3. Unit tests for reaction lifecycle (mock Bot API)
- [x] 11-4. Unit tests for reply-to chain building
- [x] 11-5. Unit tests for poll creation and result handling
- [x] 11-6. Unit tests for command registration
- [x] 11-7. Integration test: streaming edit sequence (mock Telegram API)

## Decisions

- **Plain text by default; MarkdownV2 for code blocks only.** Consistent with 30-1 task 4-2. Global MarkdownV2 is too fragile for LLM output — 18 special characters need escaping, and LLMs don't escape consistently. HTML is more predictable but unnatural. The helper `_try_send_markdown()` from 30-1 handles the try-parse-fallback pattern. For streaming edits (task 2), the intermediate edits use plain text; only the final edit attempts MarkdownV2.
- **Reactions require Bot API 7.3+ (Telegram Server 7.3, Feb 2024).** Fall back gracefully if the server version is older or bot lacks permissions. `python-telegram-bot` v21.0+ supports `setMessageReaction`.
- **Forum topics require supergroup + admin.** The setup guide will recommend creating a supergroup with forum mode enabled. If the bot isn't admin, it can still read topics but can't create them — manual creation is the fallback.
- **Mini Apps are a stretch goal.** The core value is chat-based. Mini Apps add visual editor access but require HTTPS hosting and a publicly accessible DAN server. Defer to after core features are stable.
- **File limits are asymmetric.** Standard Bot API: 50MB upload, 20MB download. The 2GB figure from Telegram marketing only applies to the Local Bot API Server (self-hosted). Document this clearly to avoid user confusion.

## Notes

- Telegram Bot API rate limits: ~30 msg/sec globally, ~20 msg/min per chat, ~30 edits/min per message. The streaming edit batching (task 2-4) stays well within these limits.
- `python-telegram-bot` v21 exposes all these features natively. No custom HTTP calls needed.
- Forum topics are a supergroup-only feature (the group must be converted to a supergroup, which Telegram does automatically when certain thresholds are hit, or manually via settings).
- `telegram_poll` capability tool is now fully wired: LLM calls `telegram_poll` -> ChatManager emits `ChatPollRequestEvent` -> adapter/fleet sends native Telegram poll. Poll answers flow back via `PollAnswerHandler`.
- Mini App menu button is now configurable via `settings.mini_app_url` in fleet config. When set, fleet startup calls `set_menu_button()` for each bot. The actual WebApp frontend (10-3, 10-5) is deferred until the DAN editor is deployed to a public HTTPS URL.
- Private-DM threading now distinguishes server continuity from fleet-side locking. The fleet still sends the broad conversation key (`chat_id:main:bot`) to the server so concierge/project continuity works normally, but its local history/lock scope is reply-aware: replies reuse the earlier lane; unrelated private-DM messages get independent local lanes. This keeps quote-based threading intact without serializing every DM behind one long-running request.
