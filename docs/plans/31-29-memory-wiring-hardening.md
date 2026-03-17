# 31-29: Memory Wiring Hardening

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** completed
**Goal:** Close the live memory regressions so project-scoped retrieval, remembered directories, preference extraction, and legacy persistence behave reliably in normal chat usage.

## Tasks
- [x] 1. Fix project-scoped retrieval and duplicate prompt/persistence paths
 - [x] 1-1. Refresh concierge memory/domain context after `_resolve_context()` with the active `project_id`
 - [x] 1-2. Pass explicit memory flags from tier executors into `ChatManager`
 - [x] 1-3. Skip duplicate conversation/episode writes when `ChatManager` already recorded the turn
- [x] 2. Promote remembered directories into structured memory
 - [x] 2-1. Label extracted directory facts (`papers directory`, `notes directory`) and tag them as `search_dir`
 - [x] 2-2. Persist extracted directories into `UserProfile.search_dirs`
 - [x] 2-3. Inject saved directories back into chat prompt hints
- [x] 3. Harden path-sensitive preference extraction
 - [x] 3-1. Strip path/file-like tokens before domain/output-format inference
 - [x] 3-2. Remove over-broad `paper` / `md` / `pdf` preference triggers
 - [x] 3-3. Preserve heuristic path facts even when `DAN_MEMORY_EXTRACTION_LLM` is enabled
- [x] 4. Harden persistence and legacy dual-write compatibility
 - [x] 4-1. Use unique atomic temp files for memory/profile/conversation indexes
 - [x] 4-2. Update `DualWriteAdapter` to the live legacy store APIs
- [x] 5. Add regressions for the new behavior
 - [x] 5-1. Preference/path false-positive tests
 - [x] 5-2. Directory fact extraction + LLM merge tests
 - [x] 5-3. ChatManager / tiered-dispatch wiring tests
 - [x] 5-4. Profile + dual-write persistence tests

## Decisions
- Pass explicit `memory_project_id` / `include_memory_kernel_context` flags from concierge executors into `ChatManager` instead of inferring duplicate memory injection from prompt text.
- Keep remembered directories in both project-scoped kernel facts and `UserProfile.search_dirs`: kernel facts drive semantic recall; profile dirs provide durable prompt hints.
- Always preserve heuristic directory/path facts when LLM-based extraction is enabled, because local path detection is cheap and high-confidence.

## Notes
- Validation passed: `python -m py_compile` on edited runtime/engine files.
- Validation passed: targeted pytest sweeps for `test_preference_extractor.py`, `test_memory_extractor.py`, `test_user_profile.py`, `test_memory_kernel.py`, `test_project_scoped_memory.py`, `test_conversation_memory.py`, `test_chat_manager.py`, and `test_tiered_dispatch.py`.
