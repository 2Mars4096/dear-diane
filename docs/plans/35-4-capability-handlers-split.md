# 35-4: Split capability_handlers.py into Domain Modules

**Parent:** [35-server-module-decomposition](35-server-module-decomposition.md)
**Status:** not-started
**Goal:** Move capability handler implementations into `server/capabilities/` domain modules, while keeping `capability_handlers.py` as a stable compatibility/registration surface until the refactor is fully landed.

## Current State

`capability_handlers.py` contains every chat capability handler in a single file. The handlers cluster into clear domains:

| Domain | Handlers | Approx. lines |
|---|---|---|
| **File I/O** | `file_read`, `file_write`, `file_grep`, `pdf_read`, `file_copy`, `file_move`, `file_delete`, `list_directory`, `compress` | ~650 |
| **Web** | `web_search`, `web_fetch`, `http_request` | ~350 |
| **Git** | `git_status`, `git_diff`, `git_log`, `git_branch`, `git_commit`, `git_worktree` | ~200 |
| **Shell/system** | `shell_command`, `screenshot`, `clipboard`, `notify` | ~250 |
| **Config** | `get_config`, `set_config` (includes `_update_env_file`) | ~130 |
| **Data processing** | `python_eval`, `csv_read`, `spreadsheet_read`, `json_extract`, `regex_match`, `text_chunk`, `text_diff`, `text_translate` | ~350 |
| **Media** | `image_describe`, `audio_transcribe` | ~70 |
| **Experience/workflow catalog** | `search_workflow_history`, `get_workflow_details`, `search_run_history`, `get_learned_principles`, `discover_capabilities`, `list_my_workflows`, `search_workflows`, `show_workflow`, `fork_workflow` | ~500 |
| **Publish/blocks** | `publish_workflow`, `unpublish_workflow`, `export_workflow`, `share_workflow`, `list_published`, `get_publish_status`, `import_block`, `list_blocks` | ~350 |
| **Run lifecycle** | `start_run`, `get_run_status`, `list_active_runs`, `cancel_run`, `resume_run`, `get_run_logs`, `get_run_checkpoints`, `rerun_from_checkpoint`, `submit_human_input` | ~400 |
| **Browser/desktop** | `browser_open/click/type/fill/extract/screenshot/tabs`, `desktop_observe/focus/click/type/hotkey` | ~200 |
| **Introspection** | `inspect_node`, `list_test_cases`, `run_test_case` | ~180 |
| **Misc** | `current_datetime`, `telegram_poll`, `send_email`, `get_activity` | ~200 |
| **Registration** | 7 `register_*_capabilities` functions | ~350 |
| **Shared helpers** | `_truncate`, `_sanitize_web_content`, `_failure_result`, `_classify_network_exception`, `_resolve_user_path`, `_schedule_export_cleanup`, etc. | ~200 |

## Safety Rules

- Capability names, schemas, and registration order must remain stable.
- `capability_handlers.py` remains the compatibility surface during the phase.
- Do not mix handler extraction with schema rewrites or capability redesign.
- Capture a capability inventory/schema snapshot before the first move.

## Tasks

- [ ] 1. Capture a before-state capability snapshot
  - [ ] 1-1. Export capability names + schema summaries from the registry
  - [ ] 1-2. Use this snapshot for regression comparison after each extraction wave
- [ ] 2. Create `src/dan/server/capabilities/__init__.py`
  - [ ] 2-1. Re-export all `register_*_capabilities` functions for backward compatibility
- [ ] 3. Create `src/dan/server/capabilities/_helpers.py`
  - [ ] 3-1. Move shared utilities: `_truncate`, `_sanitize_web_content`, `_failure_result`, `_classify_network_exception`, `_resolve_user_path`, `_schedule_export_cleanup`
- [ ] 4. Extract low-risk implementation modules first
  - [ ] 4-1. `web.py` — `handle_web_search`, `handle_web_fetch`, `handle_http_request`
  - [ ] 4-2. `git.py` — all `handle_git_*`
  - [ ] 4-3. `media.py` — `handle_image_describe`, `handle_audio_transcribe`
  - [ ] 4-4. `misc.py` — `handle_current_datetime`, `handle_telegram_poll`, `handle_send_email`, `handle_get_activity`
- [ ] 5. Extract core implementation modules second
  - [ ] 5-1. `file_io.py` — file read/write/copy/move/delete/list/compress/pdf
  - [ ] 5-2. `shell.py` — shell, screenshot, clipboard, notify
  - [ ] 5-3. `data.py` — python/csv/spreadsheet/json/regex/text helpers
  - [ ] 5-4. `browser.py` — browser and desktop handlers + `_get_controller`
- [ ] 6. Extract domain-heavy modules last
  - [ ] 6-1. `experiences.py` — history/catalog handlers + `_format_experience_summary`
  - [ ] 6-2. `publishing.py` — publish/block handlers + `_resolve_graph`
  - [ ] 6-3. `runs.py` — run lifecycle handlers + run-reference helpers
  - [ ] 6-4. `introspection.py` — inspect/test-case handlers
  - [ ] 6-5. `config.py` if `get/set_config` proves too stateful for `misc.py`
- [ ] 7. Keep registration centralized at first
  - [ ] 7-1. `capability_handlers.py` becomes a thin registration hub importing moved handlers
  - [ ] 7-2. Preserve existing `register_*` signatures and schema dicts
  - [ ] 7-3. Only move registration functions later if they still feel too large
- [ ] 8. Verification
  - [ ] 8-1. Compare capability inventory/schema snapshot before/after
  - [ ] 8-2. Run targeted capability tests for each extracted module
  - [ ] 8-3. Smoke-test capability registration through server startup

## Decisions

- (filled in during execution)

## Notes

- This is a good Phase 25 track to run in parallel with Plan 34 because the boundary can stay stable at `capability_handlers.py`.
- Keeping registration centralized at first is the safer move; it avoids mixing handler extraction with schema churn.
- `CapabilityContext` and `CapabilityResult` stay in `capability_registry.py`; they are framework types, not implementation details.
- Some handlers import lazily inside function bodies. Preserve those patterns unless there is a clear reason to change them.
