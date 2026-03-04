# 21-4: Messaging Adapters

**Parent:** [21-author-distribute](21-author-distribute.md)
**Status:** completed
**Goal:** Enable DAN workflows to be triggered and interacted with through email, Telegram, and WhatsApp. Each adapter renders HumanNode I/O through the messaging channel — the messaging user IS the human in the workflow.

## Context

DAN's `HumanNode` is a protocol: pause execution, present input schema, collect output, resume. The visual editor's `HumanInputDialog` and the CLI's interactive prompts are two renderers. Messaging adapters are a third renderer family — the same workflow runs identically, but HumanNode I/O flows through email/Telegram/WhatsApp instead of a browser or terminal.

### Architecture Principle

```
┌─────────────────────────────────────────────────┐
│  DAN Engine (unchanged)                          │
│                                                  │
│  [Node A] → [HumanNode] → [Node B]             │
│                  ↕                               │
│         HumanRenderer protocol                   │
│  (dan.engine.executor.HumanRenderer)             │
│                  ↕                               │
│  ┌──────────┬───────────┬────────────────────┐  │
│  │ Browser  │   CLI     │ Messaging Adapter  │  │
│  │ (Legacy  │ (CLIHuman │ (email/TG/WA       │  │
│  │ Callback)│  Renderer)│  HumanRenderer)    │  │
│  └──────────┴───────────┴────────────────────┘  │
└─────────────────────────────────────────────────┘
```

The engine doesn't know or care which renderer is active. All renderers implement `HumanRenderer` from `dan.engine.executor`: `async render(HumanRenderRequest) -> HumanRenderResponse`.

## Tasks

- [x] 1. **Adapter protocol and framework**
  - [x] 1-1. Create `src/dan/adapters/__init__.py` package
  - [x] 1-2. Define `MessagingAdapter` protocol (all methods async): `async start()`, `async stop()`, `async send_prompt(session_id, prompt, schema) -> None`, `async wait_for_response(session_id, timeout) -> dict`, `async send_result(session_id, result) -> None`
  - [x] 1-3. Define `AdapterConfig` base model: `workflow_path`, `api_keys` (dict), `timeout` (seconds), `welcome_message`, `error_message`
  - [x] 1-4. `MessagingHumanRenderer` — implements `HumanRenderer` (from `dan.engine.executor`), bridges to `MessagingAdapter`. On `render(request)`: calls `adapter.send_prompt()`, `adapter.wait_for_response()`, returns `HumanRenderResponse`. One renderer per adapter instance.
  - [x] 1-5. Session management: `AdapterSessionStore` — maps external conversation IDs (email thread, Telegram chat_id, WhatsApp phone) to DAN `run_id` + workflow state
  - [x] 1-6. Conversation state machine: `idle` → `running` → `awaiting_human` → `running` → `completed`/`failed`
  - [x] 1-7. Multi-turn support: a single messaging conversation can span multiple HumanNode interactions across one workflow run

- [x] 2. **Trigger modes**
  - [x] 2-1. **Message trigger**: incoming message starts a new workflow run. The message content becomes the workflow input (mapped to the first `InputNode` variable or entry-node prompt placeholder).
  - [x] 2-2. **Keyword trigger**: only messages matching a keyword/pattern start a workflow (e.g., `/run <topic>`). Other messages are ignored or replied with help text.
  - [x] 2-3. **Always-on trigger**: every message in the channel starts a new workflow (1:1 bot mode).
  - [x] 2-4. Trigger configuration in `AdapterConfig`: `trigger_mode` = `keyword` | `always` | `pattern`, `trigger_pattern` (regex for pattern mode)

- [x] 3. **Email adapter**
  - [x] 3-1. Create `src/dan/adapters/email_adapter.py`
  - [x] 3-2. `EmailAdapterConfig`: IMAP host/port/credentials (receive), SMTP host/port/credentials (send), poll interval, target email address, subject filter
  - [x] 3-3. **Receive**: poll IMAP inbox for new messages matching filter (subject pattern, sender whitelist). Extract body text as workflow input. Use stdlib `imaplib` via `asyncio.to_thread()` (not `aioimaplib` — unmaintained). Use `aiosmtplib` for sending.
  - [x] 3-4. **Send prompt**: compose and send email with HumanNode prompt. Include structured prompt (form fields as numbered list, approval as "Reply YES or NO").
  - [x] 3-5. **Receive response**: poll for reply in the same email thread (In-Reply-To header matching). Parse response text.
  - [x] 3-6. **Send result**: send final workflow output as email (formatted markdown → HTML email body). Attach artifact files if present.
  - [x] 3-7. Thread tracking: map email Message-ID / thread to DAN session
  - [x] 3-8. Error handling: malformed replies get a "please try again" response with the original prompt (retry counter, max 3 retries, `_validate_reply()` checks schema)

- [x] 4. **Telegram adapter**
  - [x] 4-1. Create `src/dan/adapters/telegram_adapter.py`
  - [x] 4-2. `TelegramAdapterConfig`: bot token, allowed chat IDs (whitelist, empty = allow all), webhook URL (optional, falls back to polling)
  - [x] 4-3. **Receive**: `python-telegram-bot` library, long-polling or webhook mode. Message text becomes workflow input.
  - [x] 4-4. **Send prompt**: send Telegram message with HumanNode prompt. Use inline keyboards for `selection` and `approval` render modes. Use reply markup for `form` mode.
  - [x] 4-5. **Receive response**: wait for next message in the same chat. Parse text or callback_query data.
  - [x] 4-6. **Send result**: send formatted result message. Long outputs split into multiple messages (Telegram 4096 char limit). Send artifact files as document attachments.
  - [x] 4-7. **Progress updates**: send "Running node X..." status messages during execution (throttled, configurable)
  - [x] 4-8. **Commands**: `/start` (welcome + help), `/status` (current run status), `/cancel` (abort current run)
  - [x] 4-9. Session per chat_id: one active workflow per conversation. Concurrent runs queued.

- [x] 5. **WhatsApp adapter**
  - [x] 5-1. Create `src/dan/adapters/whatsapp_adapter.py`
  - [x] 5-2. `WhatsAppAdapterConfig`: WhatsApp Business API credentials (or Twilio API SID + auth token), phone number, webhook URL, verify token. **Important:** WhatsApp Business API requires a verified business account, phone number approval, and pre-approved message templates for proactive messaging. Document the setup prerequisites clearly — this is not a "pip install and go" situation.
  - [x] 5-3. **Webhook receiver**: FastAPI sub-app or standalone endpoint that receives WhatsApp webhook events (message received, status updates)
  - [x] 5-4. **Send prompt**: WhatsApp API `send_message` with HumanNode prompt text. Use interactive message templates for `selection` (list message) and `approval` (button message).
  - [x] 5-5. **Receive response**: webhook delivers user reply → parse text or button response
  - [x] 5-6. **Send result**: send formatted result. Long outputs split at word boundaries. Media attachments via WhatsApp media upload API.
  - [x] 5-7. Session per phone number: map sender phone → DAN session
  - [x] 5-8. Template messages: for proactive outreach (workflow result delivery), WhatsApp requires pre-approved templates. Document the template registration process.

- [x] 6. **CLI command: `dan-adapter`**
  - [x] 6-1. Create `src/dan/cli/adapter.py` with `main()` entry point
  - [x] 6-2. `dan-adapter email --workflow path --imap-host ... --smtp-host ...` — start email adapter
  - [x] 6-3. `dan-adapter telegram --workflow path --bot-token ...` — start Telegram adapter
  - [x] 6-4. `dan-adapter whatsapp --workflow path --api-key ... --webhook-url ...` — start WhatsApp adapter
  - [x] 6-5. `--config <path>` flag: load adapter config from JSON file (alternative to CLI flags). YAML support deferred — avoids adding `pyyaml` dependency.
  - [x] 6-6. Rich TUI: show adapter status, active sessions, message counts, errors
  - [x] 6-7. Entry point (`dan-adapter`) already wired in 21-1 — verify it resolves correctly

- [x] 7. **Integration with server** *(partially complete)*
  - [x] 7-1. When `dan-serve` is running, adapters can be started/stopped via REST API: `POST /api/adapters/start`, `POST /api/adapters/stop`, `GET /api/adapters/status`
  - [x] 7-2. Adapter status visible in the visual editor (toolbar indicator) — message bubble icon with count badge, click opens panel listing adapters (type icon, status, sessions, stop button), polls `GET /api/adapters/status` every 10s
  - [ ] 7-3. Adapter logs streamed to the editor's LogPanel (same event system) — deferred

- [x] 8. **Tests**
  - [x] 8-1. Unit tests for `MessagingAdapter` protocol compliance (all three adapters)
  - [x] 8-2. Unit tests for `MessagingHumanRenderer` — mock adapter, verify prompt/response flow via `HumanRenderer` protocol
  - [x] 8-3. Unit tests for session management — concurrent sessions, timeout, cleanup
  - [x] 8-4. Unit tests for email adapter — IMAP polling mock, SMTP send mock, thread tracking
  - [x] 8-5. Unit tests for Telegram adapter — message handling, inline keyboard generation, callback parsing
  - [x] 8-6. Unit tests for WhatsApp adapter — webhook parsing, message sending, template handling
  - [x] 8-7. Integration test: `tests/test_adapters/test_integration.py` — 18 tests covering protocol compliance, full render cycle (approval/text/selection), session state transitions, prompt formatting/parsing, trigger matching, session store concurrency, `as_callback` bridge

## Decisions

- **Adapters are HumanNode renderers**, not separate workflow triggers with their own execution model. The workflow is identical; only the rendering surface changes.
- **One active workflow per conversation.** A Telegram chat or email thread is a session. If a new message arrives while a workflow is running and awaiting human input, it's treated as the response. If no input is pending, it starts a new run (or queues, per config).
- **Dependencies are all optional** under `[messaging]` extra group. `python-telegram-bot` for Telegram, `aiosmtplib` for email sending (IMAP receiving uses stdlib `imaplib` + `asyncio.to_thread`), `httpx` (already a core dep) for WhatsApp Business API.
- **WhatsApp uses the official Business API**, not unofficial libraries. Twilio is the recommended middleware for simpler setup.
- **Progress messages are throttled** — at most one status update per 5 seconds to avoid flooding the messaging channel.
- **Security**: adapter configs contain API tokens. These are loaded from env vars or config files, never hardcoded. Config files should be `.gitignore`d.

## Notes

- Email is inherently async (polling delay), Telegram is near-real-time, WhatsApp is webhook-based (near-real-time). The adapter protocol abstracts these timing differences.
- WhatsApp Business API has rate limits and template message requirements. Document these constraints clearly.
- Future adapters (Slack, Discord, SMS) follow the same `MessagingAdapter` protocol — adding a new channel is implementing ~5 methods.
- The adapter framework can also power "scheduled" workflows: a cron job sends a trigger email/message, the workflow runs, and the result is delivered back through the same channel.
