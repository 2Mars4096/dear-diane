# Continuous voice pilot

Start the local Mac host with `--voice` in addition to the existing `--env-file` and `--model` options. In Personal, click the microphone, allow access, and speak. A pause ends your turn automatically; the session keeps listening. Speak during playback to interrupt. End closes the microphone. Activity, workspace navigation, tab hiding and page closure also end the session.

## Four consistent presets

| Preset | Inspiration | Fixed synthesis model / voice | Delivery |
| --- | --- | --- | --- |
| Warm | Her-like female warmth | `qwen/qwen-audio-3.0-tts-flash` / `longanfengyue` | Soft, conversational; rate 0.96 |
| Bright | Anime-like female expressiveness | Same Qwen model / `longanlingxi` | Lively; rate 1.04 |
| Steady | Terminator-like male restraint | Same Qwen model / `loongjohn` | Measured; rate 0.88 |
| Composed | Jarvis-like male composure | `hexgrad/kokoro-82m` / `bm_george` | British English; rate 1.0 |

These are original preset voices inspired by the requested qualities, not exact actor or character reproductions. Fixed provider voice IDs preserve identity; a short profile instruction shapes wording, and a fixed playback rate shapes pacing. The choice persists locally. Change the selector during listening/playback to begin a fresh session in that voice. Composed is an English voice; multilingual pronunciation across all presets is not yet qualified.

## Conversation and context

- Recognition: `qwen/qwen3-asr-1.7b` through OpenRouter. Text reasoning/actions retain the configured personal model (currently DeepSeek V4.1 Flash).
- Voice uses the same saved conversation and record snapshot as text. User messages from failed turns retain location, budget and other stated preferences. Context currently includes the last 20 turns and up to 50 records; long-term semantic memory/retrieval is not implemented.
- Spoken replies come from completed conversation receipts. Interrupting playback does not undo a completed action. The next voice turn includes the interrupted reply ID so Diane does not assume its whole answer was heard.
- One session stays open with client-side voice activity detection, automatic turn submission and speech interruption. This is a chained recognition/chat/synthesis implementation, not a native full-duplex speech model. First integrated synthetic response: 5.90s recognition, 3.03s conversation, 5.98s synthesis (about 15s total). Model catalog price checks are cached for 60s. A separate short audio-only comparison measured 11.06s recognition + 4.19s synthesis cold, and 5.58s + 4.46s with cached catalog checks; this excludes reasoning and is not a guaranteed speedup. Latency remains unsuitable for claiming native realtime parity.

## Bounds, spending and retention

- 30-second mono PCM16/16kHz segments, 2,000 characters spoken per reply, 4 MiB provider-response bound. Longer replies remain fully readable in chat. Silence alone does not submit a turn.
- Each recognition call reserves $0.005 and synthesis reserves $0.05, sharing existing daily AI limits with conversation/extraction. The normal conversation reservation is additional. Defaults allow about five complete voice exchanges per UTC day when no other AI work has run; existing usage reduces this.
- Audio uses catalog-checked cost estimates, not chat endpoint `max_price` enforcement. Provider invoices may differ. Failed/stopped/uncertain requests retain reservations; no automatic paid retry. Pause/caps are rechecked immediately before the paid call and on UTC rollover.
- Keys stay on the host. Raw microphone and synthesized audio are transient, not written to the database. The private SQLite voice journal retains request hashes, reservations and successful transcripts, including transcripts that never reach chat after an interruption. Existing backup/history retention applies.
- Speech begins only after a microphone click. Browser echo cancellation is requested; noisy-room/speaker echo behavior still needs a human microphone test. Headphones can help with acoustic feedback.

## Verification (2026-09-30)

- Four fixed voice IDs returned MP3 audio. Real Qwen recognition correctly transcribed synthetic speech.
- Full synthetic HTTP recognition → personal conversation → synthesis passes, with a $0.360152 combined reservation.
- Chromium: two automatic turns, interruption, 390px layout and stopped tracks after End pass using generated microphone input and mocked response endpoints.
- Native Electron: synthetic microphone start/end, Listening state and no overflow pass; Mac package builds and passes ad-hoc signature verification with a microphone usage description. The installed app/backend are not replaced.
- Automated checks cover input validation, ownership, replay, no repeat after uncertain calls, catalog rejection, shared budget/rollover, migration backup, context, continuous turns and late permission cleanup.
- Physical microphone/OS permission acceptance, natural voice quality and multilingual quality remain unverified. Physical-phone tests remain deferred.

## References

- [OpenRouter recognition](https://openrouter.ai/docs/guides/overview/multimodal/stt) and [speech synthesis](https://openrouter.ai/docs/guides/overview/multimodal/tts).
- [Qwen voice identifiers](https://docs.qwencloud.com/developer-guides/speech/voice-list/qwen-audio-tts).
- Interaction patterns informed by [OpenAI voice activity detection](https://developers.openai.com/api/docs/guides/realtime-vad) and [interruption/context handling](https://developers.openai.com/api/docs/guides/realtime-conversations). No OpenAI Realtime connection is used by this implementation.
