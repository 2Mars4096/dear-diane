# 6-4: Validation

**Parent:** [6-phase-3.75-visual-editor-editing](6-phase-3.75-visual-editor-editing.md)
**Status:** in-progress
**Goal:** Extend connection validation with port-awareness and schema compatibility, and add graph-level validation feedback (inline badges + toasts) triggered on save. Depends on 6-3 for port schemas.

## Tasks

- [x] 1. Port-aware connection validation
  - [x] 1-1. Extend `isValidConnection` in `editor/src/lib/connectionValidation.ts` — add check: source node must have an output port matching `sourceHandle`, target node must have an input port matching `targetHandle`. Reject if either port doesn't exist.
  - [x] 1-2. Add single-incoming-edge check: if the target input port already has an incoming edge, reject the new connection (each input port accepts one incoming edge by default). This prevents ambiguous data flow.
  - [x] 1-3. Add JSON Schema compatibility check: if both source output port and target input port have `json_schema` defined, perform a structural check (source schema is assignable to target schema — same type, or source is a subtype). If incompatible, reject with a reason string.
  - [ ] 1-4. Update `isValidConnection` return type or add a parallel `getConnectionWarnings()` function that returns `{ valid: boolean, reason?: string }` for richer feedback.

- [ ] 2. Visual feedback during edge drag
  - [ ] 2-1. Add CSS classes to port handles in `DanNode.tsx` that indicate compatibility state: `.handle-compatible` (green glow), `.handle-incompatible` (dimmed/red), `.handle-neutral` (default, no schema info).
  - [ ] 2-2. During edge drag, React Flow calls `isValidConnection` for each potential target. Use this to dynamically apply CSS classes to target handles. This may require tracking "currently dragging" state and the source port's schema.
  - [ ] 2-3. Add CSS keyframes or transitions for the glow/dim effect — keep lightweight (CSS-only, no JS animation loops).

- [x] 3. Validation API endpoint
  - [x] 3-1. Add `POST /api/graphs/{graph_id}/validate` endpoint in `src/dan/server/app.py` — loads the graph from store, runs `validate_graph()` from `src/dan/validation/graph.py`, returns JSON response: `{ "errors": [{"node_id": "...", "message": "..."}], "warnings": [{"node_id": "...", "message": "..."}] }`.
  - [x] 3-2. Add corresponding client function `validateGraph(graphId: string)` in `editor/src/lib/api.ts`.

- [x] 4. Validation feedback in UI
  - [x] 4-1. Add `validationErrors: Record<string, string[]>` to Zustand store, keyed by node or edge ID. Values are arrays of error/warning message strings.
  - [x] 4-2. Call `validateGraph()` automatically after every successful `saveGraph()` (after `PUT /api/graphs/{id}` returns 200).
  - [x] 4-3. Display per-node badges in `DanNode.tsx`: if `validationErrors[nodeId]` has entries, show a small red dot (error) on the node corner. Tooltip on hover shows the error messages.
  - [ ] 4-4. Display per-edge indicators: if `validationErrors[edgeId]` has entries, render the edge with dashed red stroke.
  - [x] 4-5. Display toast summary after validation: "N errors" or "Validation passed". Uses existing `addToast()` action.
  - [x] 4-6. Clear `validationErrors` on: graph load (`loadGraph`) and `saveGraph` start (stale results removed until next save triggers re-validation).

- [ ] 5. Tests
  - [ ] 5-1. Connection validation: self-connect rejected; duplicate edge rejected; missing port rejected; schema-incompatible pair rejected; compatible pair accepted; single-incoming-edge rule enforced
  - [ ] 5-2. Validation API: `POST /api/graphs/{id}/validate` returns expected error list for a graph with a missing edge target, orphan port, or cycle
  - [ ] 5-3. Validation badges: node with errors in `validationErrors` shows red dot; node without errors shows no dot
  - [ ] 5-4. Validation cleared on mutation: after adding a node, `validationErrors` is empty (stale results cleared)
  - [ ] 5-5. Toast summary: after save + validate, toast shows correct error/warning counts

- [ ] 6. Docs sync
  - [ ] 6-1. Update `docs/architecture.md` — document validation API endpoint, `validationErrors` store slice, handle CSS classes
  - [ ] 6-2. Update `docs/todo.md` — check off 6-4 items
  - [ ] 6-3. Append `docs/changelog.md` entry

## Decisions

- `validate_graph()` returns `list[str]` (not exceptions); the endpoint regex-parses error messages to extract `node_id` / `edge_id` for structured response.
- Schema compatibility check is type-level only (top-level `type` field comparison). Full JSON Schema subtyping deferred.
- `validationErrors` cleared at `loadGraph` and `saveGraph` start (not on every mutation) for simplicity — avoids scattering clears across many actions.
- Task 1-4 (rich return type) and 4-4 (per-edge indicators) deferred — current boolean return + red dot badges cover the MVP.
- Task 2 (visual feedback during edge drag) deferred — requires tracking drag state which adds complexity.
- Fixed pre-existing bug: `BaseModel` not imported in `dan/models/control_flow.py`.

## Notes

- Schema compatibility checking is best-effort in v1 — exact JSON Schema subtyping is complex. Start with type-level checks (string vs number vs object) and structural shape checks for objects (required keys present). Full JSON Schema validation can be added later.
- The single-incoming-edge rule is a simplification. Some node types (like Reduce) may legitimately accept multiple inputs on one port. This can be relaxed per-node-type in a future iteration.
- Validation runs on save, not on every keystroke, to avoid performance overhead. The stale-results-clearing strategy (clear on any mutation) ensures users don't see outdated badges.
