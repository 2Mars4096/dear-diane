# 6-1: History and Multi-Select

**Parent:** [6-phase-3.75-visual-editor-editing](6-phase-3.75-visual-editor-editing.md)
**Status:** not-started
**Goal:** Add undo/redo history and multi-select so all subsequent editing operations are reversible and can work on node groups. Foundational for 6-2 (clipboard) and 6-5 (sub-graph from selection).

## Tasks

- [ ] 1. Undo/Redo history stack
  - [ ] 1-1. Add `history` slice to Zustand store in `editor/src/store/useGraphStore.ts`: `past: GraphSnapshot[]`, `future: GraphSnapshot[]` (max depth 50). `GraphSnapshot` captures `{ nodes, edges, danGraph }` only — strictly excludes run state (`runId`, `runStatus`, `nodeStatuses`, `logs`, `nodeTimings`, `ws`, `activeExecutionPath`), toasts, and layer stack.
  - [ ] 1-2. Add `pushSnapshot()` action — deep-clones current `{ nodes, edges, danGraph }`, pushes to `past`, clears `future`. Called before every mutating action.
  - [ ] 1-3. Add `undo()` action — pops from `past`, pushes current state to `future`, restores popped snapshot. No-op if `past` is empty.
  - [ ] 1-4. Add `redo()` action — pops from `future`, pushes current state to `past`, restores popped snapshot. No-op if `future` is empty.
  - [ ] 1-5. Wrap all mutating store actions to call `pushSnapshot()` before applying: `addNode`, `deleteSelected`, `onConnect`, `updateNodeData`, `updateEdgeData`, `applyAutoLayout`, `addTemplateNode`
  - [ ] 1-6. Debounce node position changes — do NOT snapshot on every `onNodesChange` position update. Instead, use React Flow's `onNodeDragStop` callback in `GraphCanvas.tsx` to snapshot once per drag operation.
  - [ ] 1-7. Disable `undo()` / `redo()` when `runStatus === "running"` to prevent edit/run desync.
  - [ ] 1-8. Cap `past` array at 50 entries; drop oldest when exceeded.
  - [ ] 1-9. Evaluate [zundo](https://github.com/charkour/zundo) (Zustand undo middleware) vs manual implementation; choose based on snapshot filtering needs (excluding run state).

- [ ] 2. Undo/Redo keyboard shortcuts
  - [ ] 2-1. Bind `Cmd/Ctrl+Z` → `undo()` in `editor/src/hooks/useKeyboardShortcuts.ts`
  - [ ] 2-2. Bind `Cmd/Ctrl+Shift+Z` → `redo()` in `useKeyboardShortcuts.ts`
  - [ ] 2-3. Prevent default browser behavior for both shortcuts (avoid browser-level undo in text fields outside our control)

- [ ] 3. Multi-select support
  - [ ] 3-1. Enable React Flow built-in multi-select in `GraphCanvas.tsx`: add `selectionOnDrag={true}` and `selectionMode={SelectionMode.Partial}` props to `<ReactFlow>`
  - [ ] 3-2. Add `selectedNodeIds: Set<string>` to Zustand store alongside existing `selectedNodeId: string | null`. Keep `selectedNodeId` as the "primary" for ConfigPanel single-node editing.
  - [ ] 3-3. Sync `selectedNodeIds` from React Flow's `onSelectionChange` callback — updates the Set whenever lasso or shift-click changes selection
  - [ ] 3-4. Extend `deleteSelected()` to handle `selectedNodeIds.size > 1`: delete all selected nodes + all edges where source or target is in the selected set
  - [ ] 3-5. Bulk move: no additional work — React Flow handles multi-node drag natively once `selectionOnDrag` is enabled
  - [ ] 3-6. Snapshot on multi-node drag stop: use `onSelectionDragStop` callback to call `pushSnapshot()` once after a group drag

- [ ] 4. Multi-select UI integration
  - [ ] 4-1. Update `ConfigPanel.tsx`: when `selectedNodeIds.size > 1`, show "N nodes selected" summary with a bulk-delete button; hide single-node property editing fields and `OutputPreview`
  - [ ] 4-2. Update `selectNodeFromLog(nodeId)`: clear `selectedNodeIds`, set single `selectedNodeId` to focus one node from log click
  - [ ] 4-3. Ensure `OutputPreview` is hidden when multiple nodes are selected (no ambiguous "which node's output?")

- [ ] 5. Tests
  - [ ] 5-1. Store unit tests: `pushSnapshot` / `undo` / `redo` cycle preserves graph state correctly; undo after redo clears future stack; history capped at max depth (51st push drops oldest)
  - [ ] 5-2. Run-state isolation test: perform undo — verify `runId`, `nodeStatuses`, `logs` are unchanged
  - [ ] 5-3. Multi-select: `deleteSelected` with 3 selected nodes removes all 3 nodes + their dangling edges
  - [ ] 5-4. Keyboard shortcut tests: mock `Cmd+Z` / `Cmd+Shift+Z` events trigger `undo()` / `redo()` store actions
  - [ ] 5-5. Drag debounce: multiple `onNodesChange` position events do NOT create multiple snapshots; one `onNodeDragStop` creates exactly one snapshot

- [ ] 6. Docs sync
  - [ ] 6-1. Update `docs/architecture.md` — document history slice, `GraphSnapshot` type, multi-select state (`selectedNodeIds`)
  - [ ] 6-2. Update `docs/todo.md` — check off 6-1 items
  - [ ] 6-3. Append `docs/changelog.md` entry

## Decisions

- (filled in during execution)

## Notes

- React Flow's `selectionOnDrag` enables rubber-band selection by default. Combined with shift-click, this covers the two primary multi-select interaction patterns.
- `GraphSnapshot` intentionally excludes `layerStack` — undo should not navigate layers. If user undoes while drilled in, the undo applies to the current layer's graph state, not the navigation history.
- `onNodeDragStop` vs `onNodesChange`: React Flow fires many position-change events during a drag. Snapshotting each would flood the history. `onNodeDragStop` fires once at the end of a drag, which is the correct commit point.
- zundo may simplify implementation but requires careful configuration to exclude non-graph state from tracking. Manual implementation gives full control over what's snapshotted.
