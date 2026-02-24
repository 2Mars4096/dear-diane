# 5-1: Multi-Layered Graph Navigation

**Parent:** [5-phase-3.5-frontend-design](5-phase-3.5-frontend-design.md)
**Status:** completed
**Goal:** Replace the sub-graph modal with read-only full-canvas drill-in navigation, wire up `CompositeExecutor` in the backend scheduler, add `is_blackbox` for marketplace nodes, and provide breadcrumb navigation with animated zoom and port mapping visualization.

## Tasks

- [ ] 1. Backend: Add `is_blackbox` and create `CompositeExecutor`
  - [ ] 1-1. Add `is_blackbox: bool = False` to `CompositeNode` in `src/dan/models/control_flow.py` — when true, node is opaque (no drill-in, no sub-graph preview); used for marketplace/imported blocks
  - [ ] 1-2. Create `CompositeExecutor` in `src/dan/executors/control_flow.py` — apply `input_mappings` (outer → inner) to inputs, call `context.run_subgraph(node.body_graph, mapped_inputs)`, apply `output_mappings` (inner → outer) to outputs, return `NodeResult`; follow same pattern as `WhileLoopExecutor`/`ForEachExecutor` (single `run_subgraph` call, no iteration)
  - [ ] 1-3. Import `CompositeNode` and `CompositeExecutor` in scheduler, add `("composite", CompositeExecutor())` to `_register_defaults` in `src/dan/engine/scheduler.py`
  - [ ] 1-4. Add unit test for `CompositeExecutor` in `tests/test_engine/` — mock `run_subgraph`, verify mappings applied correctly, sub-graph output returned
  - [ ] 1-5. Add `is_blackbox` to `CompositeNode` TS type in `editor/src/types/graph.ts`; update `createDefaultNode` in `editor/src/lib/graphAdapter.ts` to include `is_blackbox: false`

- [ ] 2. Frontend: Navigation state and layer stack
  - [ ] 2-1. Add `layerStack: Array<{ graphKey: string; nodeId: string; nodeName?: string }>` to `useGraphStore.ts` — empty at root, each entry = (sub-graph key, parent composite node id)
  - [ ] 2-2. Add `drillIn(nodeId: string)`, `drillOut()`, `jumpToLayer(index: number)` actions to store — drillIn validates node has `body_graph` and `!is_blackbox`, pushes to stack; drillOut pops; jumpToLayer slices stack
  - [ ] 2-3. Derive `currentGraph` and `currentGraphKey` from `danGraph` + `layerStack` — if stack empty, current = root; else current = `danGraph.sub_graphs[layerStack[last].graphKey]`; ensure `loadGraph` / `createGraph` resets `layerStack` to `[]`
  - [ ] 2-4. Update `danGraphToReactFlow` usage in store so `nodes`/`edges` reflect `currentGraph` (not root) when drilled in — refactor `loadGraph` and add `syncNodesEdgesFromCurrentGraph()` or equivalent that recomputes RF state from current layer

- [ ] 3. Replace `CompositePreview` modal with read-only canvas drill-in
  - [ ] 3-1. Remove `CompositePreview.tsx` modal and `showCompositePreview` state from `App.tsx`; remove "View Sub-graph" button from bottom panel
  - [ ] 3-2. Add double-click handler on composite nodes in `GraphCanvas` (or `DanNode`) — on double-click, if node has `body_graph` and `!is_blackbox`, call `drillIn(nodeId)`
  - [ ] 3-3. Update `DanNode.tsx` — hide ▶ drill-in icon when `is_blackbox`; optionally show a lock icon for blackbox composites
  - [ ] 3-4. Ensure `GraphCanvas` renders nodes/edges from the derived `currentGraph` (via store sync in 2-4)
  - [ ] 3-5. Add read-only guardrails for drilled layers (`layerStack.length > 0`): disable node drag, node add/drop, edge connect/delete, and node/edge config edits; show a subtle "Read-only sub-graph view" badge near breadcrumb

- [ ] 4. Breadcrumb bar
  - [ ] 4-1. Create `BreadcrumbBar.tsx` — shows `Root > NodeName1 > NodeName2` (or graph names); segments from `layerStack` with `nodeName` or resolved from nodes; "Root" for index -1
  - [ ] 4-2. Each segment clickable — click jumps to that layer via `jumpToLayer(index)`; current layer not clickable or styled differently
  - [ ] 4-3. Mount `BreadcrumbBar` in `App.tsx` — below toolbar or above canvas, fixed position; only visible when `layerStack.length > 0` (or always show "Root" when at root)

- [ ] 5. Animated zoom transition
  - [ ] 5-1. Add CSS transitions for drill-in/out — on `drillIn`: scale down + fade out current viewport, then swap graph data and scale up + fade in; on `drillOut` reverse
  - [ ] 5-2. Use React Flow `useReactFlow().setViewport` for programmatic zoom — optionally animate viewport change (e.g. `transform: scale(0.95)` → `scale(1)` over ~200ms) via `requestAnimationFrame` or CSS; ensure `fitView` runs after graph swap
  - [ ] 5-3. Coordinate timing — trigger graph swap at midpoint of animation or after fade-out; avoid flash of wrong graph

- [ ] 6. Sub-graph entry/exit port visualization
  - [ ] 6-1. When viewing a composite's sub-graph, resolve `input_mappings` and `output_mappings` from the parent composite node (`layerStack[last]`)
  - [ ] 6-2. Add `PortMappingOverlay` or extend sub-graph header — show "outer_port → inner_port" for inputs and "inner_port → outer_port" for outputs; render as small badges or a collapsible panel near the breadcrumb
  - [ ] 6-3. Stretch: highlight entry/exit nodes in sub-graph (entry_points, exit_points) with distinct styling; show port labels on those nodes that correspond to mappings

- [ ] 7. Tests
  - [ ] 7-1. Backend: `CompositeExecutor` unit test in `tests/test_engine/` (task 1-4)
  - [ ] 7-2. Frontend: shallow tests for `drillIn`/`drillOut` state transitions and read-only guard behavior in drilled layers

- [ ] 8. Docs sync
  - [ ] 8-1. Update `docs/architecture.md` — document `layerStack`, `CompositeExecutor`, `is_blackbox`, breadcrumb, drill-in UX
  - [ ] 8-2. Update `docs/todo.md` — mark Section A items complete
  - [ ] 8-3. Append `docs/changelog.md` entry on completion

## Decisions

- (filled in during execution)

## Notes

- **CompositeExecutor semantics:** Does NOT iterate; runs the body sub-graph once with mapped inputs, like a single "call" to the sub-graph. WhileLoop/ForEach handle iteration; Composite is a simple pass-through.
- **Sub-graph runner:** `_run_subgraph` in scheduler already injects inputs to entry-point nodes and collects outputs from exit-point nodes. CompositeExecutor must map outer port names to inner port names via `input_mappings` and vice versa for `output_mappings`.
- **Port mapping display:** `CompositeNode.input_mappings` = `{outer: inner}`, `output_mappings` = `{inner: outer}`. Sub-graph entry/exit points are node IDs; the actual port names come from those nodes' input_ports/output_ports.
- **Read-only scope:** 5-1 is navigation-only — drilled-in sub-graphs are read-only. This intentionally avoids sub-graph edit persistence/write-back complexity in this phase.
- **Frontend layer display:** The frontend currently uses a single `danGraph` + flat `nodes`/`edges`. For drill-in display, we derive current-layer nodes/edges from `danGraph` and `layerStack`; save behavior remains root-focused in 5-1.
