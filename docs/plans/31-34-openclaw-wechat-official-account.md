# 31-34: OpenClaw-Style WeChat Official Account Adapter

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** in-progress
**Goal:** Add a dedicated WeChat Official Account surface for DAN that matches the newly released OpenClaw-style WeChat channel while reusing DAN's existing messaging adapter, gateway dispatch, session, and status infrastructure as much as the WeChat platform contract allows.

## Progress update

As of 2026-03-24, the main v1 runtime slice is landed:

- Dedicated adapter module exists at `src/dan/adapters/wechat_official_account_adapter.py`.
- Adapter package exports, control-plane start/stop/status wiring, config save/load, and startup auto-start restore are in place.
- The server now exposes `GET/POST /api/adapters/wechat/callback` plus configurable callback-path aliases under `GET/POST /api/adapters/wechat/{callback_path...}` with plaintext and AES-encrypted signature verification, XML parsing, passive reply generation, timeout fallback, and customer-service async follow-up sends.
- WeChat callback turns now reuse the shared chat-surface stream flow instead of dispatching once for the passive reply attempt and then redispatching again for async follow-up.
- When `server_url` is configured, WeChat now relays through the external `POST /api/chat/message` + chat-event stream path instead of short-circuiting directly into the local router.
- WeChat now participates in the shared messaging-surface policy/verbosity rules used by concierge.
- The desktop editor settings UI now exposes WeChat-specific credential, callback, reply-behavior, relay, and AES options, and the chat/toolbar onboarding entry points can open that panel directly.
- Focused unit/router/app integration tests exist for signature verification, encrypted callback round-trips, XML parsing, passive reply routing, token refresh, config round-trip, startup restore, and callback endpoint behavior.

Still open: live smoke execution against a real Official Account deployment, and deciding later whether the same-process relay fallback should remain for local/test mode once the external path has seen real production use.

## Operator setup and runtime contract

As of 2026-03-24, the operator-facing contract is:

- Plaintext/raw callbacks are supported today.
- The accepted callback path is configurable and enforced at runtime.
- Callback turns can relay through the saved `server_url` using `POST /api/chat/message` plus chat-event streaming. If `server_url` is unset, DAN falls back to the same-process chat stream path for local/test mode.
- AES-encrypted callback verification and reply envelopes are supported when `support_encrypted_callbacks=true` and both `app_id` and `encoding_aes_key` are configured. When the flag is `false`, `encrypt_type=aes` is rejected with `400`.
- Async follow-up delivery uses the Official Account customer-service text API and therefore requires working `app_id` + `app_secret` credentials.

### Required config fields

Saved WeChat settings live at `~/.dan/wechat-official-account/config.json`.

- `token`: required for both `GET` verification and `POST` message signature checks.
- `app_id` and `app_secret`: required for production use because any reply that misses the passive-reply window falls back to customer-service follow-up.
- `callback_path`: optional; defaults to `callback`. This is the runtime path DAN enforces.
- `passive_reply_budget_seconds`: optional; defaults to `4.0`. DAN waits up to this long for a ready chat reply before falling back.
- `passive_reply_fallback_text`: optional; defaults to `Working on it...`. This is the passive XML text returned when the reply is not ready in time.
- `auto_start`: optional; when true, startup restores the adapter from the saved config.
- `account_name` and `app_name`: optional metadata shown in status/config summaries.
- `welcome_message`: optional; used for inline `subscribe` replies and now persisted through the adapter config APIs.
- `api_base_url` and `access_token_refresh_margin_seconds`: optional tuning/testing overrides for the token + customer-service API path.
- `support_encrypted_callbacks`: optional; enables AES callback verification/decryption and encrypted passive replies when the required fields are present.
- `encoding_aes_key`: required only for AES callback mode.
- `webhook_url`: persisted for operator reference, but the live server routes do not derive their callback mount from this field.
- `server_url`: optional; when set, WeChat relays turns through that DAN server's `/api/chat/message` and chat-event stream endpoints. If omitted, callback handling falls back to the same-process path.

### Callback URL and path expectations

- Default callback endpoints are `GET /api/adapters/wechat/callback` and `POST /api/adapters/wechat/callback`.
- If `callback_path` is set to `openclaw/callback`, only `GET/POST /api/adapters/wechat/openclaw/callback` are accepted. The default `/callback` alias returns `404`.
- `callback_path` is normalized before storage. All of the following save to the same runtime path:
  - `callback`
  - `openclaw/callback`
  - `https://dan.example.com/api/adapters/wechat/openclaw/callback`
- The adapter must be running. Otherwise both verification and message callbacks return `404` with `No active WeChat adapter`.
- `webhook_url` can still store the public URL you plan to paste into the WeChat Official Account console, but current request routing is driven by the active adapter plus `callback_path`, not by `webhook_url`.

Example public callback URL when `callback_path=openclaw/callback`:

```text
https://<your-public-host>/api/adapters/wechat/openclaw/callback
```

### Passive reply and follow-up behavior

- `GET` verification returns the raw `echostr` after signature validation.
- `POST` message handling verifies the same WeChat signature, parses plaintext XML, and normalizes text/event payloads.
- `subscribe` events return the adapter `welcome_message` inline. If you leave the inherited base default in place, the inline reply is `Welcome! Send a message to start a workflow.`. `Connected to DAN.` is only used if `welcome_message` is explicitly blank.
- `unsubscribe` events return an empty `200` body and do not schedule follow-up work.
- Text messages and event payloads with usable text start one shared DAN chat stream for that turn.
- If the DAN reply is ready within `passive_reply_budget_seconds`, DAN returns the final text inline as passive reply XML.
- If the DAN reply is not ready in time, DAN returns `passive_reply_fallback_text` inline and continues the same result task in the background.
- Background follow-up text is stripped to plain messaging text, split when needed, and sent via `POST /cgi-bin/message/custom/send`.
- If `app_id`/`app_secret` are missing or customer-service delivery is unavailable, the passive fallback reply still returns, but the later follow-up send fails in the background and only shows up in server logs/status.

### Practical smoke checklist

#### Local control-plane and callback-path smoke

1. Save config with at least `token`, and preferably `app_id`, `app_secret`, `callback_path`, `passive_reply_budget_seconds`, and `passive_reply_fallback_text`.
2. Start the adapter and confirm `/api/adapters/status` reports a running `wechat` adapter with `connection_state=connected`.
3. If you set a non-default `callback_path`, confirm the default `/api/adapters/wechat/callback` endpoint returns `404` and the configured alias returns `200`.
4. Verify the `GET` callback handshake echoes `echostr` exactly.
5. Send a signed plaintext `POST` with a text XML body and confirm the response is XML containing `<MsgType><![CDATA[text]]></MsgType>`.

Minimal local verification commands. Replace `openclaw/callback` below with your configured `callback_path`:

```bash
export TOKEN='wechat-token-value'
export TS='1710000000'
export NONCE='998877'
export SIG="$(python - <<'PY'
import hashlib, os
parts = sorted([os.environ['TOKEN'], os.environ['TS'], os.environ['NONCE']])
print(hashlib.sha1(''.join(parts).encode()).hexdigest())
PY
)"
```

```bash
curl "http://127.0.0.1:8000/api/adapters/wechat/openclaw/callback?signature=$SIG&timestamp=$TS&nonce=$NONCE&echostr=echo-me"
```

```bash
curl -X POST "http://127.0.0.1:8000/api/adapters/wechat/openclaw/callback?signature=$SIG&timestamp=$TS&nonce=$NONCE" \
  -H 'content-type: application/xml' \
  --data '<xml><ToUserName><![CDATA[gh_public]]></ToUserName><FromUserName><![CDATA[openid-123]]></FromUserName><CreateTime>1710000000</CreateTime><MsgType><![CDATA[text]]></MsgType><Content><![CDATA[hello from wechat]]></Content><MsgId>42</MsgId></xml>'
```

#### Timeout and follow-up smoke

1. Lower `passive_reply_budget_seconds` to a very small value such as `0.01`.
2. Send a prompt that is expected to take longer than that budget.
3. Confirm the webhook response still returns `200` with the configured fallback text in the XML body.
4. With real `app_id`/`app_secret` credentials and customer-service capability enabled on the Official Account, confirm the eventual answer arrives later as one or more customer-service text messages.
5. Without real credentials, treat this as a partial smoke only: you can validate the inline fallback behavior locally, but not the end-to-end async delivery.

#### Encrypted-mode smoke

1. Leave `support_encrypted_callbacks=false` and confirm `encrypt_type=aes` is rejected with `400`.
2. Set `support_encrypted_callbacks=true` with valid `app_id` and `encoding_aes_key`.
3. Verify the encrypted `GET` handshake returns the decrypted `echostr`.
4. Verify an encrypted `POST` callback body produces either an encrypted inline reply or an encrypted fallback XML body.

### Known limitations and current mismatches

- `webhook_url` is still operator reference only; callback-path routing is driven by the active adapter plus `callback_path`, not by the stored public URL.
- `server_url` externalizes the relay path when set, but the callback handler still keeps a same-process fallback for local/test mode.
- The desktop settings UI now covers the main WeChat fields, but masked saved secrets are still not rehydrated into drafts; clearing a stored secret still means overwriting it or using `Reset config`.
- The adapter status/config summary advertises `pip install 'dan[wechat]'`. The checked-in runtime does not currently use `wechatpy`; the extra exists mainly for operator parity and future Tencent-side helper experiments.
- Real customer-service follow-up validation still requires a live Official Account deployment. The local smoke above validates DAN's routing and passive reply behavior, not Tencent-side entitlement.

## Context

DAN already has a reusable messaging foundation:

- `src/dan/adapters/base.py` — `MessagingAdapter`, `AdapterConfig`, `AdapterSessionStore`, `MessagingHumanRenderer`, prompt/response formatting
- `src/dan/adapters/telegram_fleet.py` — `BotFleet` chat-mode adapter: dispatches to `POST /api/chat/message`, relays WS streaming events, handles progress/queue/redirect
- `src/dan/cli/adapter.py` — generic `run_adapter_chat_mode` chat-mode dispatch surface used by WhatsApp and other non-fleet adapters
- `src/dan/adapters/gateway_mixin.py` — older workflow-mode dispatch (`GatewayAdapterMixin`); modern chat-mode adapters bypass this
- `src/dan/server/routers/adapters.py` — adapter config/start/stop/status/heartbeat/runtime plumbing
- Existing messaging surfaces: email, Telegram (fleet), WhatsApp, WhatsApp Web

However, WeChat Official Account is not a Telegram/WhatsApp clone. It imposes a distinct transport/runtime contract:

- inbound webhook verification
- XML payload parsing instead of JSON bot updates
- plaintext and/or encrypted message handling
- a passive reply deadline of roughly 5 seconds
- async follow-up delivery for longer answers through customer-service messaging
- stricter public-domain deployment assumptions than Telegram or WhatsApp Web

OpenClaw's published WeChat channel uses this Official Account model, so DAN should target that public integration shape first instead of assuming a private Tencent-native package or a WeCom group-robot webhook.

## Problem

At plan start, DAN had no WeChat adapter at all, and the current messaging abstraction assumed a more symmetric conversational surface than WeChat Official Account actually offers.

The result is a double gap:

- **No dedicated WeChat channel.** DAN cannot currently receive, verify, route, or answer WeChat Official Account messages.
- **No WeChat-aware timing model.** Existing adapters assume they can wait for a response stream or send progress directly, but WeChat requires a fast passive-reply path and an async customer-service follow-up path.
- **No Official Account credential lifecycle.** DAN has no `appid/appsecret/access_token` management or WeChat-specific status reporting.
- **No operator setup path.** The adapter settings, status endpoints, and startup flows do not currently model WeChat Official Account deployment.

The correct approach is a dedicated WeChat adapter that reuses DAN's shared messaging infrastructure where possible, while explicitly specializing the webhook, timing, and async-delivery semantics.

## Key code references

| Area | File | Key symbols |
|------|------|-------------|
| Generic adapter protocol | `src/dan/adapters/base.py` | `MessagingAdapter`, `AdapterConfig`, `AdapterSessionStore`, `MessagingHumanRenderer` |
| Server relay mixin (workflow-mode only) | `src/dan/adapters/gateway_mixin.py` | `GatewayAdapterMixin`, `dispatch_via_gateway` — used only by older workflow-mode adapters; modern chat-mode adapters dispatch directly to the portal |
| Chat-mode dispatch (generic) | `src/dan/cli/adapter.py` | `run_adapter_chat_mode`, `_dispatch_to_server` — generic chat-mode relay used by WhatsApp and other non-fleet adapters; dispatches to `POST /api/chat/message` |
| Chat-mode dispatch (Telegram fleet) | `src/dan/adapters/telegram_fleet.py` | `BotFleet`, `_dispatch`, `_iter_chat_stream_events` — the primary Telegram chat-mode surface with portal dispatch, WS streaming relay, progress/queue handling |
| Fleet config persistence | `src/dan/adapters/telegram_config.py` | `TelegramFleetConfig`, `TelegramBotConfig`, `load_fleet_config`, `save_fleet_config` |
| Webhook-based adapter pattern | `src/dan/adapters/whatsapp_web_adapter.py` | `WhatsAppWebAdapter`, session/JID mapping, reconnection, event dispatch |
| Older workflow-mode adapter | `src/dan/adapters/telegram_adapter.py` | `TelegramAdapter`, `TelegramAdapterConfig` — workflow-mode HumanNode adapter; WeChat should follow the chat-mode fleet pattern instead |
| Adapter runtime/control plane | `src/dan/server/routers/adapters.py` | `start_adapter` (if/elif dispatch chain), `stop_adapter`, config save/load helpers, heartbeat, status snapshots |
| Adapter package exports | `src/dan/adapters/__init__.py` | public re-exports for all adapter classes and configs |
| Portal request model | `src/dan/server/app.py` | `ChatMessageRequest` — the canonical portal request shape with `surface_type`, `surface_id`, `session_id`, `surface_context` |
| Existing messaging reliability rules | `docs/plans/31-25-messaging-reliability.md` | canonical portal contract, `surface_context`, queue/progress behavior |
| Existing lifecycle/status rules | `docs/plans/31-32-messaging-lifecycle-and-feedback.md` | adapter status honesty, heartbeat, operator controls |

## External constraints

This plan deliberately targets **WeChat Official Account** in the OpenClaw style, not WeCom group robots.

Evidence sources:

- OpenClaw docs: WeChat channel is documented as an Official Account integration with webhook handling and deferred replies
- Tencent Cloud docs: Official Account customer-service integration is a real conversational channel
- Tencent Cloud docs: WeCom group robots are webhook/broadcast oriented and should not be treated as equivalent to a conversational adapter

This means the v1 design should assume:

- Official Account identity (`appid`, `appsecret`, token, optional AES key)
- passive reply budget for fast acknowledgement
- async customer-service send path for non-trivial DAN responses

## Strategy

Build a **dedicated WeChat Official Account adapter** with a WeChat-specific transport layer, while reusing as much of DAN's shared messaging runtime as possible.

### Chat-mode, not workflow-mode

DAN has two adapter dispatch modes:

- **Workflow-mode** (older): adapter runs a specific graph via `GatewayAdapterMixin.dispatch_via_gateway()`, relays `HumanNode` I/O through `MessagingHumanRenderer`. Used by the original `TelegramAdapter` and `EmailAdapter`.
- **Chat-mode** (modern): adapter dispatches user messages directly to `POST /api/chat/message` and streams responses back via WS. Used by `BotFleet` (Telegram) and `WhatsAppWebAdapter`. This is the path that integrates with the concierge, workflow generation, tool calling, and the full DAN runtime.

WeChat should target **chat-mode** from the start. The older `GatewayAdapterMixin` / `MessagingHumanRenderer` path is not the right integration point — it bypasses the concierge, session management, and the modern portal contract.

### Reuse and specialization

- **Reuse**
  - `AdapterSessionStore` for conversation-to-session mapping
  - shared adapter config/status/start-stop plumbing in `routers/adapters.py`
  - chat-mode portal dispatch pattern (`POST /api/chat/message` with `surface_type`, `surface_id`, `session_id`, `surface_context`) — follow `telegram_fleet.py::_dispatch` or `cli/adapter.py::_dispatch_to_server` as the reference shape
  - WS streaming relay for response delivery (follow `telegram_fleet.py::_iter_chat_stream_events`)
  - shared `surface_context` / normalized identifier rules from 31-25
  - shared status-heartbeat/operator patterns from 31-32
  - adapter package registration pattern in `adapters/__init__.py`
- **Specialize**
  - webhook verification and XML request parsing (inbound transport is fundamentally different from Telegram polling or WhatsApp Web neonize events)
  - passive-reply XML response for fast acknowledgement (returned synchronously on the webhook HTTP response, not sent via a separate API call)
  - async follow-up delivery through WeChat customer-service messaging API for longer DAN responses
  - access-token lifecycle (`appid`/`appsecret` → access token caching/refresh → customer-service send)
  - WeChat-specific timing model: the adapter must respond within ~5 seconds on the webhook or send a passive "working on it" reply and follow up asynchronously

## Tasks

### 0. Contract definition and boundary decisions
- [x] 0-1. Define the exact v1 scope: WeChat Official Account only, no WeCom group-robot mode.
- [x] 0-2. Document the adapter contract split: passive reply, deferred async reply, inbound event classes, and session-key rules.
- [x] 0-3. Decide whether WeChat implements `MessagingAdapter` directly or uses a thin WeChat-specific subclass/protocol over the shared contract.
- [x] 0-4. Document the reuse map explicitly: which parts of `base.py`, `routers/adapters.py`, and the chat-mode portal dispatch pattern (from `telegram_fleet.py` / `cli/adapter.py`) are reused unchanged and which are wrapped. Note that `gateway_mixin.py` is the older workflow-mode path and should not be a primary reuse target.

### 1. Dedicated adapter skeleton
- [x] 1-1. Add `src/dan/adapters/wechat_official_account_adapter.py`.
- [x] 1-2. Add `WeChatOfficialAccountAdapterConfig` with:
  - `app_id`
  - `app_secret`
  - `token`
  - `encoding_aes_key` (optional / mode-dependent)
  - server/local dispatch flags reused from `AdapterConfig`
  - allowlist / safety toggles as needed for rollout
- [x] 1-3. Add adapter lifecycle hooks compatible with the existing adapter control plane.
- [x] 1-4. Register WeChat config/save/load/status support in the existing adapter-management surface: add `elif adapter_type == "wechat"` branch in `routers/adapters.py::start_adapter()`, and add config save/load helpers following the `_load_telegram_desktop_state`/`_save_telegram_desktop_state` pattern.
- [x] 1-5. Register exports in `src/dan/adapters/__init__.py` (`WeChatOfficialAccountAdapter`, `WeChatOfficialAccountAdapterConfig`, plus `__all__` entries).

### 2. Webhook route and inbound parsing
- [x] 2-1. Add a FastAPI webhook route (GET for verification, POST for message/event reception). Either mount as a sub-app on the existing server or add a dedicated router — follow the WhatsApp adapter's `_webhook_app` pattern or the Telegram adapter's FastAPI sub-app pattern. The route must be mountable at a configurable callback URL path.
- [x] 2-2. Implement signature verification for WeChat webhook requests (GET verification: `echostr` echo; POST verification: `signature`/`timestamp`/`nonce` HMAC check against the configured `token`).
- [x] 2-3. Support plaintext callback mode first; decide whether encrypted mode lands in v1 or behind a follow-up flag.
- [x] 2-4. Parse XML payloads for the v1 message/event set:
  - text messages
  - subscribe/unsubscribe events
  - menu click events
  - minimal media placeholders where needed
- [x] 2-5. Normalize inbound messages into DAN's canonical portal fields (`surface_type`, `surface_id`, `session_id`, `surface_context`) matching the `ChatMessageRequest` shape in `app.py`.
- [x] 2-6. Map WeChat user identity (`FromUserName` / OpenID) and account metadata into `surface_context.identity`.

### 3. Chat-mode dispatch and response delivery
- [x] 3-1. Implement the chat-mode dispatch path: on receiving a text message via the webhook, dispatch to `POST /api/chat/message` with the normalized portal fields (following `telegram_fleet.py::_dispatch` or `cli/adapter.py::_dispatch_to_server` as the reference pattern).
- [x] 3-2. Implement the passive reply path: return an XML text reply on the webhook HTTP response within the ~5-second deadline. This is a synchronous XML response body (`<xml><ToUserName>…</ToUserName><Content>…</Content>…</xml>`), not a separate API call. Use for short acknowledgements or "working on it" messages.
- [x] 3-3. Implement the async follow-up path through WeChat customer-service messaging API (`POST /cgi-bin/message/custom/send`) for longer DAN responses that arrive after the passive reply window.
- [x] 3-4. Implement WS streaming relay to collect the DAN response stream and deliver it via the async follow-up path (follow `telegram_fleet.py::_iter_chat_stream_events` as the reference pattern for stream collection, progress throttling, and queue-redirect handling).
- [x] 3-5. Define a deterministic decision rule for when to:
  - return a direct inline XML reply (response ready within ~3 seconds)
  - return a "working on it" passive XML reply, then continue via async follow-up
  - return an empty/success passive reply and deliver entirely via async follow-up
- [x] 3-6. Ensure DAN's existing progress/queue semantics from 31-25 and 31-32 are collapsed into WeChat-appropriate user-visible messages instead of copied literally from Telegram/WhatsApp.

### 4. Access-token and API lifecycle
- [x] 4-1. Implement Official Account access-token caching and refresh.
- [x] 4-2. Implement customer-service send helpers for async replies.
- [x] 4-3. Add clear error/status reporting for invalid credentials, expired tokens, or disabled customer-service capability.
- [x] 4-4. Add a WeChat-specific connection/config snapshot method compatible with adapter status APIs and heartbeat refresh.

### 5. Session continuity and DAN dispatch integration
- [x] 5-1. Define the WeChat session key strategy (account + user/openid, with any required event-context distinctions).
- [x] 5-2. Reuse `AdapterSessionStore` for one active DAN session per WeChat conversation identity.
- [x] 5-3. Reuse the existing chat-mode portal dispatch path (`POST /api/chat/message`) to route inbound WeChat messages into the shared server chat surface.
- [x] 5-4. Verify that `surface_context`, normalized identifiers, and adapter status remain aligned with the portal contract from 31-25.
- [x] 5-5. Ensure WeChat turn state can resume or continue safely across delayed async replies and webhook retries.

### 6. Operator UX and deployment plumbing
- [x] 6-1. Add WeChat Official Account config round-trip support in the adapter settings APIs.
- [x] 6-2. Add desktop settings support for WeChat adapter configuration.
- [x] 6-3. Add concise operator-facing setup guidance:
  - callback URL
  - token fields
  - passive reply timing caveat
  - Official Account prerequisites
- [x] 6-4. Add health/status notes for partially configured deployments and unsupported modes.

### 7. Tests
- [x] 7-1. Unit tests for webhook signature verification.
- [x] 7-2. Unit tests for XML request parsing and event normalization.
- [x] 7-3. Unit tests for passive-reply versus async-follow-up routing.
- [x] 7-4. Unit tests for access-token refresh and async send behavior.
- [x] 7-5. Adapter API/config/status tests in the existing adapter router suite.
- [x] 7-6. Integration tests for session mapping and server gateway dispatch.
- [x] 7-7. Focused smoke checklist for a real Official Account deployment.

### 8. Docs and rollout
- [x] 8-1. Update changelog and architecture docs.
- [x] 8-2. Add WeChat Official Account setup notes and known limitations.
- [x] 8-3. Document deferred items (encrypted mode if postponed, media limitations, Mini Program / WeCom out of scope).

## Execution Order

```text
0 contract + boundary decisions (chat-mode confirmed, not workflow-mode)
→ 1 adapter skeleton + __init__.py + router registration
→ 2 webhook route + inbound verification/parsing
→ 3 chat-mode dispatch + passive XML reply + async follow-up delivery
→ 4 token + customer-service API lifecycle
→ 5 DAN session/portal integration
→ 6 operator UX and config plumbing
→ 7 tests
→ 8 docs and rollout notes
```

## Dependencies

- **31-25 Messaging Reliability**: WeChat should follow the normalized portal contract (`surface_type`, `surface_id`, `session_id`, clean `history`, canonical `surface_context`)
- **31-32 Messaging Lifecycle & Feedback**: reuse adapter status/config/heartbeat/operator patterns
- Existing adapter control plane in `routers/adapters.py`
- Existing messaging session store and gateway dispatch seams

## Non-Goals

- WeCom group robots / enterprise group-webhook broadcast mode
- WeChat Mini Program customer service
- A generic "all Tencent surfaces" abstraction in v1
- A private Tencent-native SDK integration that cannot be verified from public docs
- A ground-up rewrite of the messaging adapter framework

## Decisions

- This is a **dedicated WeChat adapter**, not a thin alias over Telegram or WhatsApp behavior.
- The adapter targets **chat-mode** (portal dispatch to `POST /api/chat/message` + WS streaming relay), not the older workflow-mode `GatewayAdapterMixin` path. This matches how `BotFleet` and `WhatsAppWebAdapter` integrate with the full DAN runtime (concierge, tool calling, workflow generation).
- Reuse is mandatory for session, dispatch, config, and status infrastructure, but WeChat transport semantics stay explicit.
- Official Account is the first supported WeChat surface because it matches OpenClaw's published integration model and DAN's conversational goals better than WeCom group-robot webhooks.
- Passive reply plus async customer-service follow-up is the core runtime contract; the adapter should not pretend that all DAN responses fit in a synchronous bot-reply model.
- The passive reply is an XML response body returned synchronously on the webhook HTTP response, not a separate API call.
- The shipped v1 now includes AES callback handling plus an externalized relay option through `server_url`, while still keeping a same-process fallback for local/test mode.

## Risks

- WeChat's timing and customer-service rules may force a partial mismatch with `MessagingAdapter` symmetry assumptions.
- Official Account deployment prerequisites may make local development and smoke testing harder than Telegram/WhatsApp.
- DAN's existing progress vocabulary may need WeChat-specific collapsing to avoid noisy or awkward user-visible sequences.
- Webhook route mounting must not conflict with the existing FastAPI server's routes; careful path prefix selection or sub-app mounting is needed.
- Customer-service messaging API requires the Official Account to have customer-service capability enabled — this is not automatic for all account types.

## Primary Files

- `src/dan/adapters/wechat_official_account_adapter.py` — new dedicated adapter (webhook routes, XML parsing, token lifecycle, customer-service send, chat-mode dispatch)
- `src/dan/adapters/__init__.py` — register new adapter + config exports
- `src/dan/adapters/base.py` — shared contract touchpoints if the protocol needs extension
- `src/dan/server/routers/adapters.py` — config/start/stop/status wiring (new `elif "wechat"` branch, config save/load helpers)
- `src/dan/cli/adapter.py` — generic chat-mode dispatch surface (reference pattern; may need extension for WeChat-specific timing or may be bypassed in favor of a dedicated dispatch handler)
- `src/dan/adapters/telegram_fleet.py` — reference pattern for chat-mode portal dispatch, WS streaming relay, and progress handling
- `tests/test_adapters/` — WeChat adapter and adapter API regressions
- `docs/architecture.md` — updated messaging-surface ownership notes

## Estimate

~4-6 days for the first production-worthy slice, depending on whether encrypted callback mode lands in the same plan or is split into a follow-up hardening patch.

## Notes

- OpenClaw's documented WeChat path is the reference integration shape for this plan, not an unverified private Tencent package.
- The design intent is "dedicated surface, maximum shared runtime reuse" — not "treat WeChat as a generic webhook sender."
- If the released bot surface later proves to expose a stable public SDK/package, that should be evaluated as an implementation option, not assumed at the plan level.

## Questions

None currently blocking.
