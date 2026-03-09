# 26-6: WhatsApp Web Adapter for Personal Use

**Parent:** [26-always-on-service](26-always-on-service.md)
**Status:** completed
**Goal:** Add a WhatsApp Web adapter using QR code pairing (neonize/whatsmeow) so DAN can be used from personal WhatsApp — no Business API, no Meta verification, no webhook.

## Motivation

DAN's existing WhatsApp adapter uses the official Cloud API (Business API), which requires a Meta Business account, phone number verification, and webhook hosting. For personal use, QR code pairing (like WhatsApp Web/Desktop) is far simpler: scan once, stay linked.

## Tasks

- [x] 1. Create `WhatsAppWebAdapter` class
  - [x] 1-1. `src/dan/adapters/whatsapp_web_adapter.py` with neonize `NewClient`
  - [x] 1-2. QR code pairing via terminal display (scan with WhatsApp > Linked Devices)
  - [x] 1-3. Session persistence in `~/.dan/whatsapp-web/session.sqlite3`
  - [x] 1-4. `WhatsAppWebAdapterConfig` with `db_path`, `allowed_jids`, `progress_throttle`
- [x] 2. Message handling
  - [x] 2-1. `_handle_incoming` extracts text from `conversation` and `extendedTextMessage`
  - [x] 2-2. Self-message echo suppression (outbound echo window to avoid loops)
  - [x] 2-3. JID ↔ session ID mapping (`register_session`, `_jid_from_session`)
  - [x] 2-4. `allowed_jids` filtering for access control
- [x] 3. Outbound messaging
  - [x] 3-1. `send_prompt` — text messages with auto-splitting for >4096 chars
  - [x] 3-2. `send_file` — classify as image/video/audio/document, enforce WhatsApp size limits, `_guess_mimetype` for correct MIME
  - [x] 3-3. `send_progress` — throttled progress updates
  - [x] 3-4. `send_result` — formatted workflow results
- [x] 4. CLI integration
  - [x] 4-1. `dan-adapter whatsapp-web` subcommand in `cli/adapter.py`
  - [x] 4-2. Chat mode (no `--workflow`) routes through server concierge
  - [x] 4-3. Workflow mode (with `--workflow`) runs specific workflow per message
  - [x] 4-4. `--db-path` and `--allowed-jids` CLI options
- [x] 5. File commands
  - [x] 5-1. `/find <query>` — search local files with normalized token matching
  - [x] 5-2. `/send <path>` — send local file via WhatsApp
  - [x] 5-3. Auto-send on single match from `/find`
  - [x] 5-4. Number selection from `/find` results
- [x] 6. Tests and docs
  - [x] 6-1. 16+ tests in `tests/test_adapters/test_whatsapp_web.py`
  - [x] 6-2. Updated `docs/cli.md` with setup guide and comparison table
  - [x] 6-3. Updated `docs/changelog.md`

## Files

| File | Action |
|---|---|
| `src/dan/adapters/whatsapp_web_adapter.py` | Created — full adapter implementation |
| `src/dan/cli/adapter.py` | Modified — `whatsapp-web` subcommand, chat-mode wiring |
| `tests/test_adapters/test_whatsapp_web.py` | Created — JID mapping, echo suppression, file classification, MIME detection |
| `docs/cli.md` | Updated — whatsapp-web setup guide |

## Dependencies

- **neonize** (`pip install neonize`) — Python WhatsApp Web library backed by whatsmeow (Go)
- **No Meta Business account, no webhook, no phone verification required**

## Known Limitations

- Inbound media (images, documents, audio, video) not yet handled — silently dropped. Tracked in [26-7](26-7-whatsapp-inbound-media.md).
- Mutation-confirmation UX on messaging surfaces deferred to 25-7.
- Live device smoke test requires physical phone + QR scan.

## Notes

- Session data persists in `~/.dan/whatsapp-web/` — scan QR once, reconnects automatically
- Uses neonize's synchronous `connect()` in a thread executor, with async event handling via `run_coroutine_threadsafe`
- LID (Linked ID) to phone resolution handled transparently for outbound messages
