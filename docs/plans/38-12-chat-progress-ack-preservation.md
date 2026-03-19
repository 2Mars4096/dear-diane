# 38-12: Chat Progress-Ack Preservation

**Parent:** [38-review-hardening](38-review-hardening.md)
**Status:** completed
**Goal:** Keep backend `progress_ack` chat events labeled as non-terminal progress so full-screen chat does not close the live stream before later tool or mutation events arrive.

## Tasks
- [x] 1. Preserve explicit per-event stream labels.
 - [x] 1-1. Stop the chat router from overwriting `progress_ack` `detected_mode` values with the request-level auto-detected mode.
- [x] 2. Add focused regression coverage.
 - [x] 2-1. Prove auto-mode requests still backfill the final terminal mode while preserving intermediate `progress_ack` events.

## Decisions
- Request-level auto-detected mode should only be used as a fallback for terminal chat events that do not already declare their own `detected_mode`.
- Preserve the existing frontend terminal/non-terminal contract instead of adding another client-side workaround for malformed backend event labels.

## Notes
- Root cause was reproduced from a real stuck `chat-*` stream replay: the buffered sequence contained later tool and `chat_mutation` events, but intermediate empty `chat_complete` heartbeats had been relabeled from `progress_ack` to `agent`, so `ChatPanel` treated the first one as terminal and closed the socket.
- Validation: `python -m pytest tests/test_concierge/test_chat_router.py -q`.
