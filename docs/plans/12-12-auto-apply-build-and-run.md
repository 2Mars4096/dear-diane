# 12-12: Auto-Apply Build & Run

**Parent:** [12-cursor-parity-chat](12-cursor-parity-chat.md)
**Status:** completed
**Goal:** Let the chat build, apply, and run a workflow in a single conversational turn via `auto_apply: true` on `plan_graph_mutations`.

## Tasks
- [x] 1. Add `auto_apply` boolean parameter to `MUTATION_TOOL_SCHEMA` in `prompts.py`.
- [x] 2. Add `applied: bool` field to `ChatMutationEvent` in `events.py`.
- [x] 3. Extend `_format_mutation_preview_content` to surface "Built and applied — ready to run" when `applied=True`.
- [x] 4. Implement auto-apply logic in `chat_manager.py` mutation handler.
  - [x] 4-1. After successful dry-run + `auto_apply=true`, call `GraphMutator().apply()` and `graph_store.save_graph()`.
  - [x] 4-2. Yield `ChatMutationEvent(applied=True)` with updated revision.
  - [x] 4-3. Build assistant + tool result messages and make a follow-up LLM call so the model can call `start_run`.
- [x] 5. Update prompt guidance for the build+run pattern.
  - [x] 5-1. Capability reference teaches `auto_apply: true` for build+run requests.
  - [x] 5-2. Unified system prompt rule 22 acknowledges auto-applied workflows.
- [x] 6. Add regression tests.
  - [x] 6-1. Test auto-apply path: mutation applied, graph saved, follow-up LLM call made.
  - [x] 6-2. Test no-auto-apply path: mutation stays proposed, no follow-up call.
  - [x] 6-3. Test follow-up uses `tool_choice="auto"` (not forced back to `plan_graph_mutations`).
  - [x] 6-4. Test text-based mutation with auto_apply uses user message instead of orphaned tool result.

## Decisions
- `auto_apply` only fires when the dry-run passes and the new graph is non-null.
- The follow-up LLM call uses the same model, temperature, and tool catalog as the rest of the tool loop.
- When `auto_apply` is false or omitted, behavior is unchanged (proposed preview, generator terminates).
- After auto-apply, `satisfied_tool_names` is updated to prevent `tool_choice` from forcing `plan_graph_mutations` on the follow-up.
- Text-based mutations (no native tool call) use a `user` message continuation instead of a `tool` result to avoid violating the provider API contract.

## Notes
- Validation: `python -m pytest tests/test_post_tool_followup_recovery.py tests/test_chat_prompt_modules.py -q`
