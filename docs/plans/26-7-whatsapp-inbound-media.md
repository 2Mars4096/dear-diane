# 26-7: WhatsApp Inbound Media & Voice Input

**Parent:** [26-always-on-service](26-always-on-service.md)
**Status:** completed
**Goal:** Handle all inbound WhatsApp media types (images, documents, audio/voice, video, contacts, location) so DAN can receive files, read documents, view images, and transcribe voice messages.

## Problem

Today `_handle_incoming` in `WhatsAppWebAdapter` only extracts text from `conversation` and `extendedTextMessage`. All other message types (image, document, audio, video, sticker, contact, location) hit `if not text: return` and are silently dropped.

## Neonize API (confirmed from docs)

```python
msg = event.Message
# Media types:
msg.imageMessage       # .caption, download via client.download_any(msg)
msg.documentMessage    # .fileName, .fileLength, download via client.download_any(msg)
msg.audioMessage       # .PTT (True = voice note), download via client.download_any(msg)
msg.videoMessage       # .caption, download via client.download_any(msg)
msg.stickerMessage     # download via client.download_any(msg)
msg.contactMessage     # .displayName, .vcard
msg.locationMessage    # .degreesLatitude, .degreesLongitude

# Download any media to bytes:
data: bytes = client.download_any(msg)
```

## Tasks

### 1. Media download infrastructure
- [x] 1-1. Add `_download_media(msg) -> tuple[bytes, str, str]` helper to `WhatsAppWebAdapter` returning `(data, filename, mime_type)`. Use `client.download_any(msg)` in a thread executor (neonize download is sync). Infer filename from `documentMessage.fileName` or generate from message ID + extension.
- [x] 1-2. Add `_save_media_temp(data, filename) -> Path` that writes to `~/.dan/whatsapp-web/media/` with cleanup after 1 hour. Return the temp path.
- [x] 1-3. Add media size guard — skip download if `fileLength` exceeds 50 MB (configurable via `WhatsAppWebAdapterConfig.max_inbound_media_mb`).

### 2. Document/file handling
- [x] 2-1. When `msg.documentMessage` is received, download the file, save to temp, and pass `text = f"[User sent file: {filename}]\n{caption}"` plus `metadata={"attachment_path": str(path)}` to the message callback.
- [x] 2-2. When `msg.imageMessage` is received, download, save, and pass `text = f"[User sent image]\n{caption}"` plus the attachment path. If image is a photo of a document/receipt, it can be processed by the chat pipeline.
- [x] 2-3. When `msg.videoMessage` is received, download, save, and pass `text = f"[User sent video]\n{caption}"` plus attachment path.
- [x] 2-4. When `msg.stickerMessage` is received, acknowledge with a brief text response (stickers don't carry actionable content).

### 3. Voice message transcription
- [x] 3-1. When `msg.audioMessage` is received (especially `PTT=True` voice notes), download the audio bytes.
- [x] 3-2. Add `_transcribe_audio(data: bytes, mime: str) -> str` that posts to an OpenAI-compatible `/v1/audio/transcriptions` endpoint. Provider resolution order:
  - `DAN_WHISPER_API_KEY` + `DAN_WHISPER_BASE_URL` + `DAN_WHISPER_MODEL` (dedicated transcription provider — e.g., a local Whisper server, Groq, or a different OpenAI org)
  - Falls back to `DAN_OPENAI_API_KEY` + default OpenAI base URL + model `whisper-1` (same provider as LLM)
  - Falls back to `DAN_LLM_API_KEY` + `DAN_LLM_BASE_URL` + model `whisper-1` (shared LLM provider — works if the endpoint supports the transcriptions API)
  - No key available → graceful failure
- [x] 3-3. Pass the transcribed text as the message content: `text = f"[Voice message]: {transcription}"`. Include the original audio path in metadata for reference.
- [x] 3-4. If transcription fails or no API key, reply with "I received your voice message but couldn't transcribe it. Please send as text."
- [x] 3-5. Add `DAN_WHISPER_API_KEY`, `DAN_WHISPER_BASE_URL`, `DAN_WHISPER_MODEL` to `.env.example` and `docs/cli.md` env var table.

### 4. Contact and location handling
- [x] 4-1. When `msg.contactMessage` is received, extract `displayName` and `vcard`, pass as `text = f"[User shared contact: {name}]"`.
- [x] 4-2. When `msg.locationMessage` is received, extract lat/lon, pass as `text = f"[User shared location: {lat}, {lon}]"`.

### 5. Adapter chat-mode integration
- [x] 5-1. Update `on_new_message` in `_run_adapter_chat_mode` to detect `[Attachment: ...]` and `[Voice note: ...]` prefixes, extracting attachment paths and transcribing voice notes.
- [x] 5-2. When attachments are present, include `attachment_path` in the server API call body so the concierge can route appropriately.
- [x] 5-3. For PDF attachments, auto-prepend a review hint to the message text before sending to concierge.

### 6. Tests
- [x] 6-1. Unit test: `_download_media` returns correct filename/mime for each media type (mock neonize client).
- [x] 6-2. Unit test: `_transcribe_audio` calls Whisper API and returns text (mock httpx).
- [x] 6-3. Unit test: `_handle_incoming` routes image/document/audio/video/contact/location messages correctly (not dropped).
- [x] 6-4. Integration test: voice note → transcription → chat response flow.
- [x] 6-5. Integration test: document received → temp file → concierge routing.

## Files

| File | Action |
|---|---|
| `src/dan/adapters/whatsapp_web_adapter.py` | Modify — add `_download_media`, `_save_media_temp`, `_transcribe_audio`, update `_handle_incoming` for all media types |
| `src/dan/cli/adapter.py` | Modify — update `on_new_message` callback signature to accept attachments, pass to server API |
| `src/dan/adapters/base.py` | Modify — add optional `attachments` parameter to `MessagingAdapter.send_prompt` or add new callback signature |
| `tests/test_adapters/test_whatsapp_media.py` | Create — media download, transcription, routing tests |

## Dependencies

- **neonize** — `client.download_any(msg)` for media download
- **OpenAI-compatible transcription API** — any provider exposing `/v1/audio/transcriptions` (OpenAI, Groq, local Whisper server, etc.). Configured via `DAN_WHISPER_*` env vars with cascading fallback to `DAN_OPENAI_*` then `DAN_LLM_*`.
- **No new packages required** — uses `httpx` (already installed) for API calls

## Environment Variables (new)

| Variable | Purpose | Fallback |
|---|---|---|
| `DAN_WHISPER_API_KEY` | API key for transcription provider | `DAN_OPENAI_API_KEY` → `DAN_LLM_API_KEY` |
| `DAN_WHISPER_BASE_URL` | Base URL for transcription endpoint | `https://api.openai.com/v1` → `DAN_LLM_BASE_URL` |
| `DAN_WHISPER_MODEL` | Transcription model name | `whisper-1` |

## Acceptance Criteria

- Sending a PDF via WhatsApp → DAN acknowledges receipt and can review/summarize it
- Sending a photo → DAN acknowledges receipt with caption context
- Sending a voice note → DAN transcribes and responds to the spoken content
- Sending a document → DAN saves temporarily and can process it
- Large files (>50 MB) are politely declined
- Missing Whisper API key → graceful fallback message
- No media type is silently dropped

## Notes

- Temp media files are stored in `~/.dan/whatsapp-web/media/` and cleaned up after 1 hour
- Voice transcription uses any OpenAI-compatible `/v1/audio/transcriptions` endpoint. Dedicated `DAN_WHISPER_*` env vars allow pointing to a different provider (e.g., local Whisper server on `http://localhost:8080/v1`, Groq's Whisper endpoint, or a different OpenAI org). When not set, falls back to the main LLM provider config.
- The adapter callback signature change is backward-compatible (attachments default to empty list)
- Image understanding (OCR, visual QA) is out of scope — images are saved but not visually analyzed. Future plan could add vision model support.
