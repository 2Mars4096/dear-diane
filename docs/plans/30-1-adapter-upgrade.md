# 30-1: Telegram Adapter Upgrade

**Parent:** [30-telegram-platform](30-telegram-platform.md)
**Status:** completed
**Goal:** Bring the Telegram adapter to full feature parity with the WhatsApp Web adapter — chat-mode routing via concierge, media handling, voice transcription, file commands, and surface-aware formatting.

## Current State

The existing `TelegramAdapter` (315 LOC) is a basic HumanNode renderer:
- Long-polling or webhook via `python-telegram-bot`
- `/start`, `/status`, `/cancel` commands
- Inline keyboards for approval/selection
- Message splitting at 4096 chars
- Session management (chat_id ↔ session_id)

Missing (that WhatsApp Web has):
- Chat-mode routing via concierge/dispatcher
- Inbound media handling (documents, images, audio, video, voice)
- Voice transcription via Whisper
- File send/find commands
- Self-message echo suppression (not needed — bots can't receive their own messages)
- Surface hints in system prompt (a sparse 3-line entry exists at `SURFACE_HINTS["telegram"]` — needs expansion)
- Reply prefix (bot identity)

Already working (verify only):
- `dan-adapter telegram --bot-token TOKEN` (no `--workflow`) already enters chat-mode via `_run_adapter_chat_mode()` in `adapter.py`. The Telegram path is functional but untested end-to-end.
- The `GatewayAdapterMixin` is for server-embedded adapters (running inside `dan-serve`). Standalone `dan-adapter telegram` uses `_run_adapter_chat_mode()` directly — no mixin needed.

## Tasks

### 1. Chat-mode concierge routing
- [x] 1-1. Add `send_message(session_id, text)` method — alias for `send_prompt(session_id, text, None)`. Needed by chat-mode's `adapter.send_prompt()` calls.
- [x] 1-2. Verify `_on_text_message` calls `_on_new_message(str(chat_id), text)` for non-pending messages (it already does this at line 240 — verify it works end-to-end with the chat-mode callback in `adapter.py`)
- [x] 1-3. Verify `dan-adapter telegram --bot-token TOKEN` (no `--workflow`) enters chat-mode via `_run_adapter_chat_mode()` — test the full path: message → server POST → WS stream → response sent back
- [x] 1-4. Add `DAN_TELEGRAM_BOT_TOKEN` env var fallback so `--bot-token` flag is optional when the env var is set
- [x] 1-5. Test: send message in DM → routed to server chat API → LLM response returned

### 2. Inbound media handling
- [x] 2-1. **Register media handlers in `start()`** — the current adapter only adds `MessageHandler(filters.TEXT & ~filters.COMMAND, ...)`. Add handlers for: `filters.PHOTO`, `filters.Document.ALL`, `filters.VOICE | filters.AUDIO`, `filters.VIDEO | filters.VIDEO_NOTE`, `filters.Sticker.ALL`, `filters.CONTACT`, `filters.LOCATION`. Each routes to a new `_on_media_message()` method.
- [x] 2-2. Handle `photo` messages: download largest photo size via `photo[-1].get_file()` then `file.download_to_drive()`, pass as `[Attachment: /path]` prefix
- [x] 2-3. Handle `document` messages: download, pass as attachment. Detect PDF for review path.
- [x] 2-4. Handle `voice` / `audio` messages: download, transcribe via `transcribe_audio()` from `whatsapp_web_adapter`, pass transcription as text. Prefix with `[Voice note: /path]` for adapter.py's existing transcription routing.
- [x] 2-5. Handle `video` / `video_note` messages: download, pass as attachment with description
- [x] 2-6. Handle `sticker` messages: `[User sent a sticker]`
- [x] 2-7. Handle `contact` messages: `[User shared contact: Name]`
- [x] 2-8. Handle `location` messages: `[User shared location: lat, lon]`
- [x] 2-9. Handle `caption` on media messages — use caption as the text alongside the attachment
- [x] 2-10. Media directory: `~/.dan/telegram/media/` with 1-hour cleanup (same pattern as WhatsApp)
- [x] 2-11. Size check: Telegram Bot API limits downloads to **20MB** (`getFile` API limit). For larger files, inform user. Reject files > `max_inbound_media_mb` config value with friendly message.

### 3. Outbound file sending
- [x] 3-1. Add `send_file(session_id, file_path)` method using `bot.send_document()` / `bot.send_photo()` based on MIME type
- [x] 3-2. For PDFs: send as document with filename caption
- [x] 3-3. For images: send as photo (Telegram auto-compresses) or document (preserves quality), based on size
- [x] 3-4. Wire into chat-mode's `attachment_paths` delivery (already checks `hasattr(adapter, "send_file")`)

### 4. Surface hints and formatting
- [x] 4-1. **Expand** the existing `SURFACE_HINTS["telegram"]` entry in `chat_manager.py` (currently only 3 sparse lines at line 453). Add:
  - Telegram supports Markdown: **bold**, _italic_, `code`, ```code blocks```, [links](url), ~~strikethrough~~, ||spoilers||
  - Keep replies concise (1-5 sentences for simple tasks, structured sections for reports)
  - Use code blocks for data/code output
  - No raw HTML tags — use Markdown
  - Never echo full script/code from tool results (same rule as WhatsApp)
  - Never reproduce raw extracted text from pdf_read or file_read
  - URLs on their own line
- [x] 4-2. **Plain text by default; try-parse for code blocks only.** Don't set a global `parse_mode` — Telegram's MarkdownV2 requires escaping 18 special characters (`. ! - ( ) { } # + = | ~ > _  * [ ] `), which LLM output frequently contains unescaped. Strategy: send messages as plain text. When the response contains triple-backtick code blocks, wrap those specific blocks with MarkdownV2 formatting and send with `parse_mode="MarkdownV2"`. If the parse fails (Telegram returns 400), retry without `parse_mode`. Add a helper `_try_send_markdown()`.
- [x] 4-3. Reply prefix: use `identity.py`'s `format_reply_prefix()` or equivalent

### 5. Telegram-specific message handlers
- [x] 5-1. `/help` command: list available commands and capabilities, including /find, /send, /status, /cancel, /show, /list, /mcp
- [x] 5-2. Forward unrecognized `/` commands to server (same pattern as WhatsApp adapter — already handled by adapter.py's `_translate_slash_command()` fallback)
- [x] 5-3. Handle `reply_to_message` — when user replies to a bot message, include the quoted bot message text as additional context in the dispatched message (e.g., prefix with `[Replying to: "..."]`). Advanced reply-chain threading (multi-level) is deferred to 30-3 task 4.

### 6. Config additions
- [x] 6-1. Add `max_inbound_media_mb: float = 20.0` to `TelegramAdapterConfig` (matches Bot API `getFile` download limit)
- [x] 6-2. Add `try_markdown: bool = True` to config — controls whether `_try_send_markdown()` attempts MarkdownV2 for code-block responses
- [x] 6-3. `progress_throttle: float = 5.0` (already exists — no change needed)

### 7. Tests
- [x] 7-1. Unit tests for media download/routing (mock `telegram.File`)
- [x] 7-2. Unit tests for `send_file` (mock `bot.send_document` / `bot.send_photo`)
- [x] 7-3. Unit tests for markdown escaping
- [x] 7-4. Unit tests for `/help`, command forwarding, reply-to context
- [x] 7-5. Integration test: DM message → concierge → response (mock server)

## Notes

- `python-telegram-bot` v21 uses async natively. The current adapter uses the correct lower-level pattern: `Application.builder().build()` → `initialize()` → `start()` → `updater.start_polling()`, which is compatible with running in an existing event loop (unlike `run_polling()` which blocks).
- Telegram Bot API has no QR code flow — just paste the token from BotFather. Much simpler setup than WhatsApp.
- Bots can't see messages from other bots in groups unless privacy mode is disabled in BotFather. This is handled in 30-2.
- The `_run_adapter_chat_mode` in `adapter.py` already handles Telegram — it strips HTML, splits messages, and dispatches to the server. Task 1 is mainly about verifying this works end-to-end and filling gaps.
- **Telegram download limit:** The Bot API `getFile` method only supports files up to 20MB. For larger inbound files, the bot receives metadata but can't download the content. This is a Telegram Bot API limitation (MTProto clients can download up to 2GB, but we use the Bot API). Upload limit for bots is 50MB via `send_document()` (2GB only via local Bot API server).
- The `GatewayAdapterMixin` is NOT needed for the standalone `dan-adapter telegram` path. It's used when adapters run embedded inside `dan-serve` (see `app.py` adapter endpoints). The standalone path uses `_run_adapter_chat_mode()` which handles server communication via `httpx.AsyncClient`.
