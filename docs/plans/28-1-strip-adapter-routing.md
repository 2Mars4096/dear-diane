# 28-1: Strip Adapter-Side Routing

**Parent:** [28-llm-first-chat](28-llm-first-chat.md)
**Status:** completed
**Goal:** Remove all keyword-based routing and local special cases from the adapter. Every message goes to the server.

## Problem

The adapter has its own routing layer (`_classify_adapter_intent`) with 5 intent categories, `/find` and `/send` commands, pending-action state, and polite-send detection — all duplicating logic that belongs on the server. This causes inconsistency: some messages route locally, others route to the concierge, and the user can't predict which path runs.

## Tasks

- [x] 1. Remove `_classify_adapter_intent()` and all local routing in `on_new_message()`
  - [x] 1-1. Delete the function and its supporting helpers (`_extract_search_query_from_send_request`, `_CONTINUATION_WORDS`, etc.)
  - [x] 1-2. `on_new_message()` becomes: handle explicit `/slash` commands → else `_dispatch_to_server()`
- [x] 2. Remove local `/find` and `/send` handlers
  - [x] 2-1. Delete `_handle_find_command()`, `_handle_send_command()`, local file search helpers
  - [x] 2-2. The LLM handles file requests via `list_directory` + `file_read` + file delivery action
  - [x] 2-3. Keep `/find` and `/send` as convenience aliases that translate to natural language and dispatch to server (e.g. `/find late payment` → send "find the file matching 'late payment'" to server)
- [x] 3. Remove `conversation_pending` state machine
  - [x] 3-1. Delete `_PendingAction`, `conversation_pending` dict, number-selection handlers
  - [x] 3-2. Clarification and multi-step interactions happen in the LLM conversation context, not adapter state
- [x] 4. Simplify `on_new_message()` to ~20 lines
  - [x] 4-1. Parse slash commands → dispatch to server → stream response → send to user
  - [x] 4-2. Remove all intermediate routing, pending state, mutation auto-apply
- [x] 5. Update tests
  - [x] 5-1. Deleted `test_adapter_intent.py` (30 intent classification tests) and `test_adapter_file_search.py` (2 local file search tests)
  - [x] 5-2. Updated `test_adapter_chat_mode.py`: removed local `/send` test, added 7 `_translate_slash_command` tests, migrated `_surface_name_for_adapter_type` + mutation stream tests from deleted files, added slash dispatch + unknown command integration tests (18 tests total)

## Files

| File | Action |
|---|---|
| `src/dan/cli/adapter.py` | Major rewrite of `on_new_message()` and removal of routing helpers |
| `tests/test_adapters/test_adapter_intent.py` | Delete or replace |
| `tests/test_adapters/test_adapter_chat_mode.py` | Simplify |
| `tests/test_adapters/test_adapter_file_search.py` | Delete (server-side now) |

## Notes

- Mutation auto-apply (`auto_approve` + `conversation_pending["apply_mutation"]`) needs to move to the Response Actions layer (28-3) or stay as a server-side concierge behavior
- The adapter's `_ADAPTER_CONTEXT` system prompt injection stays — it tells the LLM about the messaging surface
