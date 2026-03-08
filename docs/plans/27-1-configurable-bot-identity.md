# 27-1: Configurable Bot Identity

**Parent:** [27-async-message-dispatch](27-async-message-dispatch.md)
**Status:** completed
**Goal:** Replace all hardcoded `[DAN` prefix strings with a single configurable bot name, read from environment or config.

## Context

The string `DAN` is hardcoded in ~15 locations across the concierge runtime, handlers, progress reporter, promotion, and adapter code. Every `f"[DAN - {project.label}]"` and `"[DAN]"` should read from a shared config so the user can rename their bot (e.g., `Jarvis`, `Friday`, or a team-specific name).

### Affected files (current hardcoded sites)

| File | Hardcoded strings |
|---|---|
| `server/concierge/runtime.py` | `[DAN - {project.label}]`, `[DAN - {project.label} / {task.label}]`, `[DAN]`, `.startswith("[DAN")` |
| `server/concierge/handlers.py` | `[DAN - {context.project.label}]` |
| `server/concierge/progress.py` | `[DAN - {snapshot.project_label}]` |
| `server/concierge/promotion.py` | `[DAN - {project.label}]` |
| `cli/adapter.py` | `_DAN_PREFIX = "[DAN]"`, `_DAN_SCOPED_PREFIX = "[DAN - "`, `_ADAPTER_CONTEXT` (`"You are DAN, a messaging AI assistant"`), `_server_unavailable_message()` (`"DAN server unavailable"`), `_stream_unavailable_message()` (`"DAN accepted the request"`) |
| `adapters/whatsapp_web_adapter.py` | `_REPLY_PREFIX = "[DAN] "` |

## Tasks

- [x] 1. Add `bot_name` config
  - [x] 1-1. Add `DAN_BOT_NAME` env var (default `"DAN"`) loaded via `identity.py`
  - [x] 1-2. Create `dan/server/concierge/identity.py` with `get_bot_name()`, `format_prefix()`, `format_bare_prefix()`, `starts_with_prefix()`, `strip_prefix()`
  - [x] 1-3. Add `bot_name` parameter to `Concierge.__init__()` (optional, falls back to env var). `_format_reply_label()` passes `self.bot_name` to `format_prefix()`.

- [x] 2. Replace all hardcoded sites
  - [x] 2-1. `runtime.py` — all `f"[DAN - ..."` → `format_prefix()`, `.startswith("[DAN")` → `starts_with_prefix()`
  - [x] 2-2. `handlers.py` — `f"[DAN - ..."` → `format_prefix()`
  - [x] 2-3. `progress.py` — `f"[DAN - ..."` → `format_prefix()`
  - [x] 2-4. `promotion.py` — `f"[DAN - ..."` → `format_prefix()`
  - [x] 2-5. `cli/adapter.py` — `_DAN_PREFIX`/`_DAN_SCOPED_PREFIX` → identity module; `_strip_dan_prefix()` → `strip_prefix()`; `_ADAPTER_CONTEXT`, `_server_unavailable_message()`, `_stream_unavailable_message()` → `get_bot_name()`
  - [x] 2-6. `adapters/whatsapp_web_adapter.py` — `_REPLY_PREFIX` → `_reply_prefix()` static method calling `format_bare_prefix()`
  - [x] 2-7. Updated `.env.example` with `DAN_BOT_NAME=` entry

- [x] 3. Tests
  - [x] 3-1. 26 unit tests for `identity.py` — default name, env override, prefix formatting, prefix detection, strip
  - [x] 3-2. Existing concierge tests pass unchanged (default bot name is "DAN", so assertions still hold)
  - [x] 3-3. Adapter integration test for custom bot name (`TestAdapterIntegrationWithCustomName`)

## Decisions

- (filled in during execution)

## Notes

- The `identity.py` module is deliberately minimal — just string formatting. No persistence, no per-user config. If we later want per-surface or per-project bot names, the `format_prefix()` API is the extension point.
- The `starts_with_prefix()` function handles the detection pattern so changing the bot name doesn't break response deduplication logic in the adapter.
