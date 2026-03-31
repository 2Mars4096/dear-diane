# 12-15: Workflow Identity Save-As

**Parent:** [12-cursor-parity-chat](12-cursor-parity-chat.md)
**Status:** completed
**Goal:** Let users promote `_scratch` or any working workflow into a durable named workflow ID without keeping everything under the draft slot.

## Tasks
- [x] 1. Add a safe backend save-as path for graph persistence.
  - [x] 1-1. Add normalized, deduped graph ID suggestion in `GraphStore`.
  - [x] 1-2. Add `/api/graphs/{graph_id}/save-as` for named workflow promotion from the current graph snapshot.
- [x] 2. Make workflow catalog forking produce stable human-manageable workflow IDs.
  - [x] 2-1. Update `fork_workflow` to derive graph IDs from `new_name` instead of random IDs.
  - [x] 2-2. Support optional exact `new_workflow_id` with collision protection.
- [x] 3. Expose the save-as flow in the editor.
  - [x] 3-1. Add editor API/store support for saving the current graph as a named workflow.
  - [x] 3-2. Add a `Save As` control in `GraphSwitcher` that switches the active tab to the new named workflow.
- [x] 4. Add focused regressions and build validation.
  - [x] 4-1. Cover backend workflow-catalog + API save-as behavior.
  - [x] 4-2. Cover editor store save-as behavior and pass the editor build.

## Decisions
- `_scratch` remains the draft slot; the fix is promotion into a named workflow, not destructive in-place rename of all `_scratch` history.
- Save-as uses the current in-memory graph snapshot so local unsaved edits are preserved when promoting to a named workflow.
- Durable workflow IDs should be normalized from human names and deduped predictably (`name`, `name-2`, ...).

## Notes
- Implemented in `src/dan/server/graph_store.py`, `src/dan/server/routers/graphs.py`, `src/dan/server/capabilities/experiences.py`, `src/dan/server/capability_handlers.py`, `editor/src/lib/api.ts`, `editor/src/store/useGraphStore.ts`, and `editor/src/components/GraphSwitcher.tsx`.
- Validation: `pytest -q tests/test_server/test_workflow_catalog.py tests/test_server/test_api.py -k 'fork_workflow or save_graph_as'` (`10 passed, 22 deselected`), `cd editor && npm run test -- --run src/store/__tests__/saveGraphAs.test.ts` (`1 passed`), and `cd editor && npm run build` (passed; Vite emitted the existing large-chunk warnings plus a Node-version warning but completed successfully).
