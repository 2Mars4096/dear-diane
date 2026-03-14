# 35-3: Split chat_manager.py into a `server/chat/` Package

**Parent:** [35-server-module-decomposition](35-server-module-decomposition.md)
**Status:** completed
**Goal:** Extract support code from `chat_manager.py` into a dedicated `server/chat/` package, while keeping `src/dan/server/chat_manager.py` as a stable compatibility facade during Plan 34.

## Current State

`chat_manager.py` mixes six distinct responsibilities:

| Responsibility | Lines (approx.) | Functions/classes |
|---|---|---|
| **Event/model types** | 1586–1800 (~215) | `ChatTokenEvent`, `ChatCompleteEvent`, `ChatErrorEvent`, `ChatMutationEvent`, `ChatInterruptedEvent`, 10+ more event models, `NodeSummary`, `EdgeSummary`, `GraphSummary` |
| **Prompt construction** | 350–930 (~580) | `_build_mutation_tool_schema`, `_build_node_type_reference`, `generate_capability_reference`, `_resolve_surface_hints`, `_looks_like_research_report_request`, system prompt assembly |
| **Token estimation & context compaction** | 1329–1600 (~270) | `_get_context_window`, `_completion_max_tokens`, `estimate_tokens`, `_estimate_messages_tokens`, `_truncate_assistant_message`, `_compact_context`, `compact_history` |
| **Graph summary serialization** | 1782–1945 (~165) | `compute_graph_revision`, `build_graph_summary`, `_format_node_line`, `serialize_for_prompt` |
| **Mutation parsing & normalization** | 1945–2230 (~285) | `_normalize_usage`, `_merge_usage_totals`, `_try_parse_mutation_json`, `_coerce_strict_edges`, `_normalize_generated_mutation_ops`, `_build_args_preview`, `_build_dry_run_preview`, `_try_persist_audit` |
| **Mode detection & debug helpers** | 1033–1329 (~300) | `normalize_chat_mode`, `detect_chat_mode`, `build_debug_context`, `recent_run_failed_for_workflow`, `_clean_tool_result`, `_extract_cited_sources` |
| **ChatManager class** | 2232–5669 (~3,440) | The actual class with `send_message`, `send_message_with_tools`, multi-turn tool loop, preflight hooks, telemetry |

## Safety Rules

- `ChatManager`, `send_message`, and `send_message_with_tools` signatures do not change in this phase.
- `src/dan/server/chat_manager.py` remains importable and re-exports moved symbols until Plan 34 is merged.
- Do not force a global import-path churn while `34-3` / `34-4` are in flight.
- Move code first; optional import cleanup comes after the facade is stable.

## Tasks

- [x] 1. Create `src/dan/server/chat/` package
  - [x] 1-1. Add `__init__.py` with re-exports for public chat symbols (95 lines)
  - [x] 1-2. Keep `src/dan/server/chat_manager.py` as a compatibility facade that imports/re-exports from `server/chat/*` (3,647 lines, down from 5,668)
- [x] 2. Create `src/dan/server/chat/events.py` (193 lines)
  - [x] 2-1. Move all `Chat*Event` Pydantic models (16 event types)
  - [x] 2-2. Move `ChatStreamEvent` union type
  - [x] 2-3. Move `NodeSummary`, `EdgeSummary`, `GraphSummary` models
- [x] 3. Create `src/dan/server/chat/prompts.py` (733 lines)
  - [x] 3-1. Move `_build_mutation_tool_schema` and related schema constants
  - [x] 3-2. Move `_build_node_type_reference`
  - [x] 3-3. Move `generate_capability_reference`, `invalidate_capability_cache`
  - [x] 3-4. Move `_resolve_surface_hints`, `_looks_like_research_report_request`
  - [x] 3-5. Move shared system-prompt assembly helpers (SYSTEM_PROMPT_TEMPLATE, BUILD_FROM_INTENT_PROMPT, UNIFIED_SYSTEM_PROMPT, WORKFLOW_TEMPLATES, SURFACE_HINTS)
- [x] 4. Create `src/dan/server/chat/tokens.py` (309 lines)
  - [x] 4-1. Move `_get_context_window`, `_completion_max_tokens`, `estimate_tokens`
  - [x] 4-2. Move `_estimate_messages_tokens`, `_truncate_assistant_message`
  - [x] 4-3. Move `_compact_context`, `compact_history`
- [x] 5. Create `src/dan/server/chat/graph_summary.py` (155 lines)
  - [x] 5-1. Move `compute_graph_revision`, `build_graph_summary`
  - [x] 5-2. Move `_format_node_line`, `serialize_for_prompt`
- [x] 6. Create `src/dan/server/chat/mutation_parser.py` (294 lines)
  - [x] 6-1. Move `_try_parse_mutation_json`, `_coerce_strict_edges`, `_normalize_generated_mutation_ops`
  - [x] 6-2. Move `_build_args_preview`, `_build_dry_run_preview`
  - [x] 6-3. Move `_try_persist_audit`
  - [x] 6-4. Move `_normalize_usage`, `_merge_usage_totals`
- [x] 7. Create `src/dan/server/chat/helpers.py` (508 lines)
  - [x] 7-1. Move `normalize_chat_mode`, `detect_chat_mode`
  - [x] 7-2. Move `build_debug_context`, `recent_run_failed_for_workflow`
  - [x] 7-3. Move `_clean_tool_result`, `_extract_cited_sources`
  - [x] 7-4. Move action-hint helpers (`_dedupe_action_hints`, `_missing_action_hints`, `_tool_choice_for_action_hints`, etc.)
  - [x] 7-5. Move tool-routing helpers (`_tool_schema_name`, `_parallel_tool_family`, `_force_single_tool_request`, etc.)
- [x] 8. Keep `ChatManager` class in `chat_manager.py` for now
  - [x] 8-1. Updated imports to pull from `server/chat/*`
  - [x] 8-2. Class unchanged — still at ~3,440 lines
- [x] 9. Stabilize compatibility surface before import cleanup
  - [x] 9-1. Verified 6 import-smoke tests (facade imports, new-style imports, module-level asyncio, object identity, ChatManager callable, private helpers accessible)
  - [x] 9-2. `from dan.server.chat_manager import ...` confirmed working for all `__all__` symbols
  - [x] 9-3. Internal imports remain on facade for now (safe parallel lane)
- [x] 10. Verification
  - [x] 10-1. 57 tests pass: multi-turn tools (20), codegen resilience (6), tiered dispatch (10), triage (4), session manager (8), plus 9 more
  - [x] 10-2. `chat_manager_module.asyncio` accessibility confirmed for test_chat_manager_codegen_resilience.py

## Decisions

- Used absolute imports (`from dan.server.chat.X import ...`) in the facade rather than relative imports for clarity.
- Kept all private names (`_compact_context`, `_clean_tool_result`, etc.) importable in the facade since ChatManager methods reference them as module-level names.
- `_RECENT_MESSAGES_COUNT` moved to tokens.py (used by `compact_history`); other ChatManager-specific env vars stayed in the facade.
- `pii_session_var` stays in the facade since it's tightly coupled to the ChatManager class.

## Notes

- Total extracted: 2,287 lines across 7 new files (events.py, helpers.py, prompts.py, tokens.py, graph_summary.py, mutation_parser.py, __init__.py).
- Facade reduced from 5,668 → 3,647 lines (2,021 lines removed).
- All pre-existing tests pass (57 tests across 5 test files). Pre-existing failures from missing packages (fastapi, openai) are unrelated.
- `events.py` is the highest-value extraction because those models are imported widely across `server/`, `concierge/`, and tests.
- Internal callers can now use `from dan.server.chat import ChatCompleteEvent` for new code, while existing `from dan.server.chat_manager import ChatCompleteEvent` remains stable.
