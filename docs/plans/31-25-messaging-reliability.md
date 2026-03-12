# 31-25: Messaging Reliability

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** in-progress
**Goal:** Normalize the chat portal contract so any frontend speaks the same protocol, then close the remaining reliability and UX gaps on Telegram and WhatsApp.

## Context

The server already has a unified portal (`POST /api/chat/message`), but each adapter interprets the contract differently:

| Field | Telegram fleet | WhatsApp/generic | CLI chat |
|-------|---------------|-----------------|----------|
| `history` | Clean (no system msgs) | Injects `_ADAPTER_CONTEXT` | Clean |
| `surface_context` | Sends identity + peers | Not sent | Not sent |
| `thread_id` | Conversation key (`chat:topic:bot`) | `ext_id` string | Explicit |
| `surface` | `"telegram:<bot>"` | `"whatsapp"` | `"cli"` |
| `workflow_id` | Bot-derived project key | Adapter-generated | User-specified |

This means swapping frontends, adding a custom app, or duplicating the portal for a swarm requires reverse-engineering adapter-specific assumptions. Task 0 normalizes the portal contract once; tasks 1–4 fix the remaining per-surface bugs on top of it.

Evidence sources:
- Code review of `telegram_fleet.py`, `whatsapp_web_adapter.py`, `cli/adapter.py`, `app.py`
- `docs/bugs.md` open items (most messaging bugs fixed in prior sessions)
- Live terminal logs (healthy — gaps are in edge paths, not happy path)
- User-reported experience: "why is WhatsApp so much better than Telegram?"

## Tasks

### 0. Unified portal contract
> One canonical request/response schema. Any frontend — Telegram, WhatsApp, custom web app, voice, or a duplicated swarm portal — speaks the same protocol.

- [x] 0-1. **Normalize `ChatMessageRequest` identifiers.** Add clear, flexible fields that any frontend can populate:
  - `surface_type` (str) — platform kind: `"telegram"`, `"whatsapp"`, `"web"`, `"cli"`, `"custom"`, etc. Replaces the overloaded `surface` field (which currently mixes type with instance ID like `"telegram:scholar"`).
  - `surface_id` (str) — unique instance identifier within that surface type: bot username, phone number, browser session ID. Allows the same type to have multiple instances (swarm duplication).
  - `session_id` (str) — conversation continuity key. Replaces the ambiguous `thread_id` which means different things per adapter.
  - Keep `surface` as a computed backward-compat alias (`"{surface_type}:{surface_id}"`) so existing code doesn't break.
  - File: `src/dan/server/app.py` (`ChatMessageRequest`), `src/dan/server/concierge/models.py` (`SurfaceMessage`)
- [x] 0-2. **Ban system messages in `history`.** Validate that `history` contains only `user`/`assistant` roles on the portal side. Move the WhatsApp/generic adapter's `_ADAPTER_CONTEXT` injection into `surface_context` (matching what Telegram already does).
  - File: `src/dan/cli/adapter.py` (~L290–310, `_ADAPTER_CONTEXT` injection)
- [x] 0-3. **Make `surface_context` the canonical identity envelope.** Ensure all adapters send `surface_context` with at minimum `{"identity": {"name": ..., "role": ...}}`. The concierge already renders this via `_surface_identity_instructions`; this task just ensures every adapter populates it.
  - Files: `src/dan/cli/adapter.py`, `src/dan/adapters/whatsapp_web_adapter.py`
- [x] 0-4. **Standardize the streaming response protocol.** Document the WS event types (`chat_complete`, `chat_queued`, `chat_token`, `chat_error`, `chat_interrupted`) and their semantics in a short reference block in `docs/llm-api-guide.md`. This is what any new frontend implements against.
- [x] 0-5. **Regression tests** for portal contract (valid/invalid identifiers, system-message rejection, surface_context propagation across all adapter types).

### 1. Error visibility — tell users when things go wrong
> Both platforms silently swallow failures today. Users see ❌ or nothing.

- [x] 1-1. **Telegram: error text on HTTP non-200.** When `_dispatch` gets a non-200 response, send a short error message (e.g. "Sorry, I couldn't process that — please try again.") before returning. Keep the ❌ reaction.
  - File: `src/dan/adapters/telegram_fleet.py`, `_dispatch` (~L620–628)
- [x] 1-2. **Telegram: error text on dispatch exception.** Same treatment in the `except Exception` block — send a brief apology instead of just ❌.
  - File: `src/dan/adapters/telegram_fleet.py`, `_dispatch` (~L473–481)
- [x] 1-3. **WhatsApp: retry + error feedback on send failure.** `_send_text` currently logs and returns on exception. Add one retry with short backoff, then send an error message via a fallback path.
  - File: `src/dan/adapters/whatsapp_web_adapter.py`, `_send_text` (~L477–491)
- [x] 1-4. **Regression tests** for error paths on both platforms.

### 2. WhatsApp chat-mode gap closure
> The generic adapter chat-mode path (`cli/adapter.py`) is used by WhatsApp. It lacks three features Telegram has.

- [x] 2-1. **Handle `chat_queued` redirect.** When the WS stream receives a `chat_queued` event with a replacement `stream_channel_id`, close the current socket and reconnect to the new channel. Port the redirect-following pattern from `telegram_fleet._iter_chat_stream_events`.
  - File: `src/dan/cli/adapter.py`, WS event loop (~L348–367)
  - Reference: `src/dan/adapters/telegram_fleet.py` (~L1150–1200)
- [x] 2-2. **WhatsApp reconnection on disconnect.** Wrap `_client.connect()` in a reconnect loop with exponential backoff and max-retry cap. Handle neonize disconnect or detect closed connection and restart.
  - File: `src/dan/adapters/whatsapp_web_adapter.py`, `start()` (~L121–124)
- [x] 2-3. **Progress indication during long waits.** After N seconds (e.g. 5s, matching Telegram's reassurance timer), send a "Working on it..." message to the WhatsApp user if no response has been delivered yet. Send at most once per turn.
  - File: `src/dan/cli/adapter.py`, dispatch path
- [x] 2-4. **Regression tests** for `chat_queued` redirect, reconnection, progress timing.

### 3. Telegram forum-topic threading
> Replies in forum groups go to General instead of the correct topic.

- [x] 3-1. **Thread `message_thread_id` through adapter send methods.** Add optional `thread_id: int | None = None` parameter to `send_or_edit`, `_send_text`, and `send_poll` in `telegram_adapter.py`. Pass `message_thread_id=thread_id` in the Telegram API kwargs when set.
  - File: `src/dan/adapters/telegram_adapter.py` (~L357–381)
- [x] 3-2. **Pass `ctx.thread_id` from fleet to all send call sites.** Update `_send_reply`, `_stream_with_edits`, `_stream_collect`, `_show_progress`, and any other fleet methods that call adapter send methods to include `thread_id=ctx.thread_id`.
  - File: `src/dan/adapters/telegram_fleet.py` (multiple call sites)
- [x] 3-3. **Regression tests** for forum-topic delivery.

### 4. Cross-platform UX polish
> Minor UX gaps that affect both platforms.

- [x] 4-1. **Render clarification options as numbered list.** When the concierge yields a `ClarificationRequest` with options, format them as `1. Option A\n2. Option B\n...` so messaging users can reply with a number.
  - File: `src/dan/server/concierge/runtime.py`, clarification yield path (~L2600)
- [x] 4-2. **WhatsApp poll fallback.** When a handler returns poll requests and the adapter lacks `send_poll_for_session`, render the poll as a numbered text list instead of silently dropping it.
  - File: `src/dan/cli/adapter.py`, poll handling (~L424)
- [x] 4-3. **Regression tests** for clarification formatting and poll text fallback.

### 5. Verify end-to-end
- [x] 5-1. Run focused test suite for adapter and concierge paths.
- [ ] 5-2. Live smoke test on both Telegram and WhatsApp.
- [x] 5-3. Update changelog, architecture, bugs, and parent plan.

## Execution Order

```
0 (portal contract) ──► 1 (error visibility) ──► 2 (WhatsApp gaps) ──► 3 (forum topics) ──► 4 (UX polish) ──► 5 (verify)
       │                        │
       └─ foundation ──────────►└─ all later tasks use normalized identifiers
```

Task 0 lands first — it normalizes identifiers and the history contract, which simplifies every subsequent task. Tasks 1 and 3 are independent of each other. Tasks 2 and 4 are independent of each other.

## Estimates

| Task | Estimate |
|------|----------|
| 0. Portal contract | 0.75d |
| 1. Error visibility | 0.5d |
| 2. WhatsApp gap closure | 1d |
| 3. Forum-topic threading | 0.5d |
| 4. UX polish | 0.5d |
| 5. Verify & docs | 0.25d |
| **Total** | **~3.5d** |

## Decisions
- All tasks implemented in parallel via 4 non-overlapping agent scopes (portal+docs, telegram, cli/whatsapp, ux-polish)
- `surface` kept as backward-compat alias — `_normalize_identifiers` model validator handles bidirectional sync
- System message filtering done at portal level (ChatMessageRequest validator) rather than per-adapter
- WhatsApp reconnection uses exponential backoff (2s base, 300s max, 10 attempts)
- Progress indication fires after 5s, sent at most once per turn
- Poll text fallback renders as numbered list matching clarification format
- Post-review hardening tightened contract validation (reject conflicting/partial identifiers and malformed `surface=":"` alias), restored adapter delivery instructions via `surface_context`, and completed Telegram file-threading / edit-fallback coverage
- WhatsApp post-review patch added best-effort fallback apology, outbound-echo suppression for that apology, early abort after retry exhaustion, and fixed an infinite-loop edge case in `_split_message()` for leading-space remainders
- WhatsApp live-smoke safety now reuses `allowed_jids` as an exact inbound/outbound allowlist; canonical matching accepts bare numbers and strips `:device` suffixes from `s.whatsapp.net` JIDs so self-chat runs stay pinned to one recipient. Inbound guard moved to `chat_jid` (before media download) and non-phone-server JIDs (`@lid`, `@g.us`) no longer cross-match phone allowlist entries.

## Notes
- The portal is already `POST /api/chat/message` — this plan does not change the URL, just normalizes the schema and adapter usage.
- `surface_type` + `surface_id` replaces the overloaded `surface` field. For swarms, you duplicate the portal and each instance gets its own `surface_id` while sharing the same `surface_type`.
- `session_id` replaces `thread_id` at the portal level. Adapters map their native concepts (Telegram `chat_id:thread:bot`, WhatsApp JID, etc.) into this single key.
- `history` must be clean — no system messages. All identity/persona/context goes through `surface_context`. This is what Telegram already does post-31-24; WhatsApp needs to catch up.
- The streaming protocol (`chat_complete`, `chat_queued`, `chat_token`, etc.) is already consistent; documenting it in the API guide is what makes new frontends self-serve.
- WhatsApp's simpler architecture is actually an advantage for most flows — reliability gaps are in edge paths.
- Forum-topic support may have low real-world impact but is a clean fix worth landing.
- Code changes and focused regression suites are complete; only live Telegram/WhatsApp smoke verification (`5-2`) remains open.
- Post-review verification: `84` targeted messaging tests and `73` adapter regression tests pass with no linter errors.
- Additional self-only WhatsApp guard verification: `tests/test_adapters/test_whatsapp_web.py` (`27`) + `tests/test_cli_whatsapp_messaging.py` (`15`) = `42` passing tests confirm exact allowlist matching, outbound blocking, cross-server rejection, chat-level inbound guard, and media-download short-circuit for disallowed JIDs.
