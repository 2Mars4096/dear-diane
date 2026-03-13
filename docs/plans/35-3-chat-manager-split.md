# 35-3: Split chat_manager.py into a `server/chat/` Package

**Parent:** [35-server-module-decomposition](35-server-module-decomposition.md)
**Status:** not-started
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

- [ ] 1. Create `src/dan/server/chat/` package
  - [ ] 1-1. Add `__init__.py` with re-exports for public chat symbols
  - [ ] 1-2. Keep `src/dan/server/chat_manager.py` as a compatibility facade that imports/re-exports from `server/chat/*`
- [ ] 2. Create `src/dan/server/chat/events.py`
  - [ ] 2-1. Move all `Chat*Event` Pydantic models
  - [ ] 2-2. Move `ChatStreamEvent` union type
  - [ ] 2-3. Move `NodeSummary`, `EdgeSummary`, `GraphSummary` models
- [ ] 3. Create `src/dan/server/chat/prompts.py`
  - [ ] 3-1. Move `_build_mutation_tool_schema` and related schema constants
  - [ ] 3-2. Move `_build_node_type_reference`
  - [ ] 3-3. Move `generate_capability_reference`, `invalidate_capability_cache`
  - [ ] 3-4. Move `_resolve_surface_hints`, `_looks_like_research_report_request`
  - [ ] 3-5. Move shared system-prompt assembly helpers
- [ ] 4. Create `src/dan/server/chat/tokens.py`
  - [ ] 4-1. Move `_get_context_window`, `_completion_max_tokens`, `estimate_tokens`
  - [ ] 4-2. Move `_estimate_messages_tokens`, `_truncate_assistant_message`
  - [ ] 4-3. Move `_compact_context`, `compact_history`
- [ ] 5. Create `src/dan/server/chat/graph_summary.py`
  - [ ] 5-1. Move `compute_graph_revision`, `build_graph_summary`
  - [ ] 5-2. Move `_format_node_line`, `serialize_for_prompt`
- [ ] 6. Create `src/dan/server/chat/mutation_parser.py`
  - [ ] 6-1. Move `_try_parse_mutation_json`, `_coerce_strict_edges`, `_normalize_generated_mutation_ops`
  - [ ] 6-2. Move `_build_args_preview`, `_build_dry_run_preview`
  - [ ] 6-3. Move `_try_persist_audit`
  - [ ] 6-4. Move `_normalize_usage`, `_merge_usage_totals`
- [ ] 7. Create `src/dan/server/chat/helpers.py`
  - [ ] 7-1. Move `normalize_chat_mode`, `detect_chat_mode`
  - [ ] 7-2. Move `build_debug_context`, `recent_run_failed_for_workflow`
  - [ ] 7-3. Move `_clean_tool_result`, `_extract_cited_sources`
- [ ] 8. Keep `ChatManager` class in `chat_manager.py` for now
  - [ ] 8-1. Only update its imports to pull from `server/chat/*`
  - [ ] 8-2. Do not move the class itself while Plan 34 executors/runtime are still changing
- [ ] 9. Stabilize compatibility surface before import cleanup
  - [ ] 9-1. Add import-smoke tests for commonly imported names
  - [ ] 9-2. Ensure `from dan.server.chat_manager import ...` still works
  - [ ] 9-3. Only then update internal imports opportunistically
- [ ] 10. Verification
  - [ ] 10-1. Run targeted chat manager / tool-handoff / prompt-design tests
  - [ ] 10-2. Run one end-to-end chat round-trip smoke test

## Decisions

- (filled in during execution)

## Notes

- This is one of the safest Phase 25 tracks to run in parallel with Plan 34, but only if the facade rule is followed strictly.
- `events.py` is the highest-value extraction because those models are imported widely across `server/`, `concierge/`, and tests.
- `compact_history` and the chat-mode helpers are part of the compatibility surface today; do not break those imports mid-phase.
