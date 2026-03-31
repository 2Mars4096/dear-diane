# 12-11: Workflow Delete Safety

**Parent:** [12-cursor-parity-chat](12-cursor-parity-chat.md)
**Status:** completed
**Goal:** Prevent chat from issuing stale workflow deletes after inventory lookups and make delete UX safer when a workflow is already absent.

## Tasks
- [x] 1. Identify why `list_graphs` success could still be followed by failing `delete_graph` calls.
 - [x] 1-1. Confirm the model could emit `list_graphs` and speculative `delete_graph` calls in the same tool batch.
 - [x] 1-2. Confirm the delete failures were caused by stale remembered IDs, not by mismatched graph/list identifiers.
- [x] 2. Make workflow inventory/delete sequencing safer in the capability tool loop.
 - [x] 2-1. Defer `delete_graph` when the same tool batch also includes workflow inventory tools.
 - [x] 2-2. Inject follow-up guidance telling the model to delete only exact `graph_id` values from the fresh inventory result.
- [x] 2-3. Promote Ask-mode workflow-action follow-ups back to agent execution when recent history clearly shows active workflow work.
- [x] 3. Harden `delete_graph` UX.
 - [x] 3-1. Resolve exact metadata-name matches back to `graph_id`.
 - [x] 3-2. Treat already-absent workflows as a safe no-op instead of a hard failure.
- [x] 4. Add regression coverage for batch splitting, name resolution, already-absent deletes, and prompt guidance.

## Decisions
- Workflow inventory plus destructive deletes is a dependency-sensitive sequence, so the server now splits it across tool-loop turns instead of trusting the model’s single-batch plan.
- Chat capability deletion is idempotent at the UX layer: “already absent” is surfaced as a successful no-op.

## Notes
- Validation: `python -m pytest tests/test_concierge/test_live_data.py tests/test_post_tool_followup_recovery.py tests/test_chat_prompt_modules.py -q`
- Follow-up hardening on 2026-03-31: delete-like workflow turns such as “remove that obsolete workflow” now prefer `delete_graph` over `plan_graph_mutations`, while remove-node/remove-edge edits stay on the mutation path. Focused validation: `pytest -q tests/test_post_tool_followup_recovery.py -k 'preferred_workflow_edit_tool or forces_plan_graph_mutations_for_workflow_edit or forces_delete_graph_for_workflow_delete_request or defers_delete_graph_until_after_inventory'` (`7 passed, 26 deselected`) and `pytest -q tests/test_concierge/test_live_data.py -k 'delete_graph'` (`6 passed, 47 deselected`).
