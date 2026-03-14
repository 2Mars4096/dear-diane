# 35-4: Split capability_handlers.py into Domain Modules

**Parent:** [35-server-module-decomposition](35-server-module-decomposition.md)
**Status:** completed
**Goal:** Move capability handler implementations into `server/capabilities/` domain modules, while keeping `capability_handlers.py` as a stable compatibility/registration surface until the refactor is fully landed.

## Current State

`capability_handlers.py` was a 4,839-line monolith. Now split into 14 domain modules under `server/capabilities/` with `capability_handlers.py` as a 752-line thin registration hub.

## Safety Rules

- Capability names, schemas, and registration order must remain stable.
- `capability_handlers.py` remains the compatibility surface during the phase.
- Do not mix handler extraction with schema rewrites or capability redesign.
- Capture a capability inventory/schema snapshot before the first move.

## Tasks

- [x] 1. Capture a before-state capability snapshot
  - [x] 1-1. Export capability names + schema summaries from the registry
  - [x] 1-2. Use this snapshot for regression comparison after each extraction wave
- [x] 2. Create `src/dan/server/capabilities/__init__.py`
  - [x] 2-1. Re-export all `register_*_capabilities` functions for backward compatibility (lazy import to avoid circular deps)
- [x] 3. Create `src/dan/server/capabilities/_helpers.py`
  - [x] 3-1. Move shared utilities: `_truncate`, `_sanitize_web_content`, `_failure_result`, `_classify_network_exception`, `_resolve_user_path`, `_schedule_export_cleanup`
- [x] 4. Extract low-risk implementation modules first
  - [x] 4-1. `web.py` — `handle_web_search`, `handle_web_fetch`, `handle_http_request`
  - [x] 4-2. `git.py` — all `handle_git_*`
  - [x] 4-3. `media.py` — `handle_image_describe`, `handle_audio_transcribe`
  - [x] 4-4. `misc.py` — `handle_current_datetime`, `handle_telegram_poll`, `handle_send_email`
- [x] 5. Extract core implementation modules second
  - [x] 5-1. `file_io.py` — file read/write/copy/move/delete/list/compress/pdf
  - [x] 5-2. `shell.py` — shell, screenshot, clipboard, notify
  - [x] 5-3. `data.py` — python/csv/spreadsheet/json/regex/text helpers
  - [x] 5-4. `browser.py` — browser and desktop handlers + `_get_controller`
- [x] 6. Extract domain-heavy modules last
  - [x] 6-1. `experiences.py` — history/catalog handlers + `_format_experience_summary` + `handle_get_activity`
  - [x] 6-2. `publishing.py` — publish/block handlers + `_resolve_graph`
  - [x] 6-3. `runs.py` — run lifecycle handlers + run-reference helpers
  - [x] 6-4. `introspection.py` — inspect/test-case handlers
  - [x] 6-5. `config.py` — `get/set_config` + `_update_env_file` + `_CONFIGURABLE_PREFIXES`
- [x] 7. Keep registration centralized at first
  - [x] 7-1. `capability_handlers.py` becomes a thin registration hub importing moved handlers
  - [x] 7-2. Preserve existing `register_*` signatures and schema dicts
  - [x] 7-3. Only move registration functions later if they still feel too large
- [x] 8. Verification
  - [x] 8-1. All imports from `dan.server.capability_handlers` still work
  - [x] 8-2. All imports from `dan.server.capabilities` package still work
  - [x] 8-3. Zero linter errors across all new modules
  - [ ] 8-4. Server startup smoke test (deferred — requires live server)

## Decisions

- `capabilities/__init__.py` uses `__getattr__` lazy imports to avoid circular dependency with `capability_handlers.py`.
- `handle_list_graphs` stays in `capability_handlers.py` since it's directly part of the base registration and very small.
- Schemas stay in `capability_handlers.py` alongside the registration functions — they are part of the public registration API, not handler implementation.
- `_CONFIGURABLE_PREFIXES` moved to `config.py` alongside the handlers that use it.
- `browser.py` exposes `set_controller()` for the `register_computer_capabilities` function to call instead of directly mutating a module-level global.

## Notes

- Total lines before: 4,839 in one file.
- Total lines after: 752 (hub) + 2,934 (14 domain modules) = 3,686 lines across 16 files.
- ~1,153 lines saved from removing duplicated boilerplate comment patterns in the old generated handlers.
- All `register_*` function signatures, capability names, schema definitions, modes, categories, and `cacheable` flags are exactly preserved.
