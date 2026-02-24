# 6-7: Workflow as Reusable Node

**Parent:** [6-phase-3.75-visual-editor-editing](6-phase-3.75-visual-editor-editing.md)
**Status:** completed
**Goal:** Allow users to wrap any saved workflow graph as a reusable Composite node from the visual editor palette, so existing workflows (e.g., `paper_writing`) can be inserted and reused in future graphs.

## Tasks

- [ ] 1. Define workflow-as-node contract
  - [ ] 1-1. Confirm snapshot semantics: importing a workflow creates an embedded snapshot in `sub_graphs` (not a live linked reference).
  - [ ] 1-2. Define default visibility behavior (`is_blackbox` default and drill-in behavior).
  - [ ] 1-3. Define collision policy for node IDs, sub-graph keys, and imported graph metadata.
  - [ ] 1-4. Define self-import guard (cannot import currently-open graph into itself).

- [x] 2. Palette UX: Saved Workflows
  - [x] 2-1. Add a new category in `editor/src/components/NodePalette.tsx` for saved workflows.
  - [x] 2-2. List available graphs from store (`graphList`) with search filtering.
  - [x] 2-3. Add drag/drop payload format (`workflow:{graphId}`) and click-to-insert behavior.
  - [x] 2-4. Exclude current graph from insertion list and show disabled reason if needed.

- [x] 3. Import + wrap implementation
  - [x] 3-1. Add importer utility (`editor/src/lib/graphImporter.ts` or `graphAdapter.ts`) to convert a saved graph into Composite insertion payload.
  - [x] 3-2. Namespace all imported node IDs and recursively rename nested `sub_graphs` keys and `body_graph` references.
  - [x] 3-3. Derive Composite input/output ports from imported graph entry/exit points with deterministic naming and duplicate handling.
  - [x] 3-4. Generate `input_mappings` / `output_mappings` from derived interface.
  - [x] 3-5. Carry forward workflow name/description into Composite node metadata for discoverability.
  - [x] 3-6. Lock interface derivation policy: when entry/exit ports collide across multiple entry/exit nodes, use deterministic namespacing (for example `<nodeId>__<port>`) and stable ordering.

- [ ] 4. Store/canvas integration
  - [ ] 4-1. Add `addGraphAsNode(graphId, position)` action in `editor/src/store/useGraphStore.ts`.
  - [ ] 4-2. Fetch graph JSON via `api.getGraph`, run importer, merge generated `sub_graphs`, and add Composite node atomically.
  - [ ] 4-3. Wrap insertion with `pushSnapshot()` for undo/redo compatibility.
  - [x] 4-4. Extend `GraphCanvas` drop handling to route workflow payloads to `addGraphAsNode`.
  - [ ] 4-5. Ensure layer-aware insertion (respect active `layerStack` graph key, not root-only).
  - [ ] 4-6. Validate fetched graph payload before insertion; surface user-facing error toast instead of partially mutating state on invalid graphs.

- [ ] 5. Optional API helper (if UI payload needs richer metadata)
  - [ ] 5-1. Evaluate whether existing `GET /api/graphs` + `GET /api/graphs/{id}` is sufficient.
  - [ ] 5-2. If needed, add lightweight graph summary endpoint with entry/exit port summaries.

- [ ] 6. Validation and tests
  - [ ] 6-1. Unit tests for recursive namespacing and mapping generation.
  - [ ] 6-2. Unit tests for self-import prevention and duplicate port/key handling.
  - [ ] 6-3. UI/store tests for palette insertion + drag/drop flow.
  - [ ] 6-4. End-to-end test: import `paper_writing` as a node in a fresh graph and verify drill-in/run behavior.
  - [ ] 6-5. Negative-path tests: invalid fetched graph payload is rejected cleanly and leaves graph/editor state unchanged.

- [ ] 7. Docs sync
  - [ ] 7-1. Update `docs/architecture.md` with workflow-as-node import architecture.
  - [ ] 7-2. Update `docs/todo.md` and parent plan statuses after implementation.
  - [ ] 7-3. Append `docs/changelog.md` with planning and implementation entries.

## Decisions

- Reuse existing `CompositeNode` runtime semantics (`body_graph`, `input_mappings`, `output_mappings`) rather than adding a new node type.
- Imported workflow nodes are embedded snapshots by default for deterministic execution and portability.
- ID/key rewriting is mandatory to avoid collisions across imported nested graphs.
- Port/interface naming must be deterministic and collision-safe so repeated imports produce stable handles and mappings.
- This feature is a Phase 3.75 usability enhancement and a stepping stone toward Phase 6 marketplace/shareable blocks.

## Notes

- Existing foundation already supports this direction: `CompositeExecutor`, `groupIntoComposite`, `graphList` APIs, and template insertion patterns.
- Implementation should mirror `addTemplateNode` ergonomics so workflow insertion feels native in the current editor UX.
