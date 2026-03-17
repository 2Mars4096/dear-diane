# User-Path Review

Date: 2026-03-17

Scope: review from the user's perspective, following the main usage path through startup, `/api/chat/message`, mode routing, capabilities, built-in tools, and Furnace lifecycle wiring.

## What I Ran

- `HOME=$(mktemp -d) DAN_FURNACE_API_ENABLED=0 DAN_TELEMETRY_DB=$(mktemp /tmp/dan-telemetry-XXXXXX.db) pytest -q tests/test_server/test_api.py tests/test_server/test_mutation_regression.py tests/test_server/test_telemetry.py`
- `pytest -q tests/test_furnace_api.py tests/test_server/test_startup.py`
- `pytest -q tests/scenarios/test_casual_utility.py`
- `pytest -q tests/test_server/test_chat_integration.py tests/test_server/test_chat_stream_lifecycle.py tests/test_server/test_chat_request_validation.py`
- `pytest -q tests/test_tools/test_builtin_tools.py`

Results:

- startup, API, telemetry, Furnace, and scenario checks passed
- chat integration and clipboard built-in tool checks exposed two confirmed user-facing regressions

## Findings

### P1: Non-concierge `/api/chat/message` crashes before streaming because `surface_context` is undefined

Evidence:

- `src/dan/server/routers/chat.py:498` references `surface_context`, but `chat_message()` never defines that name in its local scope before `_produce()` uses it.
- The failing branch is the direct chat-manager path (`concierge=false`), right before `cm.send_message()` / `cm.send_message_with_tools()` is invoked.
- `pytest -q tests/test_server/test_chat_integration.py tests/test_server/test_chat_stream_lifecycle.py tests/test_server/test_chat_request_validation.py` fails with:
  - `NameError: name 'surface_context' is not defined`
- The same root cause breaks multiple user-visible chat behaviors in `tests/test_server/test_chat_integration.py`, including:
  - ask mode text responses (`:600`-`:616`)
  - agent mode mutation responses (`:649`-`:666`)
  - token/complete streaming (`:694`-`:709`)
  - mention handling (`:739`-`:760`)
  - stop-generation flow (`:781`-`:805`)
  - fast run-stream handoff (`:816`-`:864`)

Why this matters:

- This is a core chat path failure, not a narrow edge case: the request is accepted, but the producer crashes before the stream yields normal chat events.
- From the user's perspective, ask/agent mode requests collapse into `chat_error`, and follow-on features like stop-generation fail because the stream dies early.

### P2: Clipboard copy is no longer wired as a write-only operation and now fails if the session cannot read the clipboard first

Evidence:

- `src/dan/tools/clipboard.py:109`-`118` makes `ensure_clipboard_available()` call `read_clipboard_text()` whenever a read utility exists.
- `src/dan/tools/clipboard.py:121`-`126` calls that probe before every clipboard write.
- `src/dan/server/capabilities/shell.py:115`-`123` routes clipboard writes through the same tool, so the capability handler inherits the same behavior.
- `pytest -q tests/test_tools/test_builtin_tools.py` fails in `tests/test_tools/test_builtin_tools.py:592`-`597` on this machine:
  - `await clipboard(text="unit test payload")`
  - observed error: `RuntimeError: Clipboard utility exists but is unavailable in this session`

Why this matters:

- A user asking DAN to copy text can now fail even when the write command exists, because the code insists on a successful clipboard read before attempting the write.
- That turns a basic “copy this” action into a session-capability check that is stricter than the write operation itself, and it affects both the built-in tool and the shell clipboard capability.

## Notes

- I re-checked the earlier domain-preference concern from the previous follow-up note; it does not reproduce on a clean rerun and is not carried forward as an active finding.
- I did not confirm additional wiring regressions in startup, telemetry, Furnace, or the broader casual-utility scenario path during this pass.
