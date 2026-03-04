# 6-2: Clipboard, Context Menu, Edge Reconnection

**Parent:** [6-phase-3.75-visual-editor-editing](6-phase-3.75-visual-editor-editing.md)
**Status:** completed
**Goal:** Add copy/paste/duplicate for nodes, right-click context menus for common actions on canvas/node/edge, and drag-to-rewire edge reconnection. Depends on 6-1 for multi-select state.

## Tasks

- [x] 1. Clipboard state and copy/paste logic
  - [x] 1-1. Add `clipboard` slice to Zustand store in `editor/src/store/useGraphStore.ts`: `{ nodes: DanNode[], edges: Record<string, unknown>[] }` (serialized selection, initially empty)
  - [x] 1-2. Add `copySelected()` action — serialize all nodes in `selectedNodeIds` (or single `selectedNodeId`) plus all edges where both source and target are in the selected set. Store deep clones in `clipboard`.
  - [x] 1-3. Add `pasteClipboard(position?: {x, y})` action — deserialize clipboard nodes with new UUIDs, offset positions by +30px from original (or place at `position` if provided). Remap edge IDs: generate new edge UUIDs, map old node IDs to new node IDs for `source`/`target`. Edges referencing nodes outside the clipboard are dropped. Inserts into current layer (RF state is layer-aware).
  - [x] 1-4. Add `duplicateSelected()` action — `copySelected()` then `pasteClipboard()` in one step.
  - [x] 1-5. Cross-layer paste: node IDs are remapped normally; edges to outside nodes are dropped. No special handling needed beyond layer-aware insertion (1-3).

- [x] 2. Clipboard keyboard shortcuts
  - [x] 2-1. Bind `Cmd/Ctrl+C` → `copySelected()` in `editor/src/hooks/useKeyboardShortcuts.ts`
  - [x] 2-2. Bind `Cmd/Ctrl+V` → `pasteClipboard()` in `useKeyboardShortcuts.ts`
  - [x] 2-3. Bind `Cmd/Ctrl+D` → `duplicateSelected()` in `useKeyboardShortcuts.ts`
  - [x] 2-4. Prevent default browser behavior for Cmd+C/V/D to avoid interference
  - [x] 2-5. Ensure shortcuts are disabled when a text input/textarea is focused (avoid intercepting normal text editing)

- [x] 3. Context menu component
  - [x] 3-1. Create `editor/src/components/ContextMenu.tsx` — positioned fixed at right-click coordinates; renders a list of action buttons with labels and shortcut hints
  - [x] 3-2. Add state as local component state in GraphCanvas: `contextMenu: { type: 'canvas' | 'node' | 'edge', position: {x, y}, targetId?: string } | null`
  - [x] 3-3. Wire React Flow callbacks in `GraphCanvas.tsx`:
    - `onPaneContextMenu` → open canvas context menu
    - `onNodeContextMenu` → open node context menu (pass node ID)
    - `onEdgeContextMenu` → open edge context menu (pass edge ID)
  - [x] 3-4. Prevent default browser right-click menu via `event.preventDefault()` in all three handlers
  - [x] 3-5. Dismiss context menu on: click-away (pane click, window click capture), Escape key, or any action selection

- [x] 4. Context menu actions
  - [x] 4-1. **Canvas menu items:** "Paste" (calls `pasteClipboard` at click position; disabled if clipboard empty)
  - [x] 4-2. **Node menu items:** "Copy" (`copySelected`), "Duplicate" (`duplicateSelected`), "Delete" (`deleteSelected`)
  - [x] 4-3. **Edge menu items:** "Delete" (removes edge via `onEdgesChange`), "Change to Data/Control/Context" (calls `updateEdgeData` with new `edge_type`)
  - [x] 4-4. Each action pushes an undo snapshot before executing (via the wrapped store actions from 6-1)

- [x] 5. Edge reconnection
  - [x] 5-1. Enable React Flow edge reconnection in `GraphCanvas.tsx`: add `edgesReconnectable` prop to `<ReactFlow>`
  - [x] 5-2. Add `onReconnect` handler: when user drags an edge handle to a new target/source, update the edge's source/target/handles and embedded `danEdge` data. Push undo snapshot before applying.
  - [x] 5-3. Run `isValidConnection` check during reconnection to prevent invalid rewiring (self-connect, duplicate, etc.)

- [ ] 6. Tests
  - [ ] 6-1. Copy/paste: copied nodes get new UUIDs different from originals; edges between copied nodes are remapped to new IDs; edges to nodes outside clipboard are dropped
  - [ ] 6-2. Paste layer-awareness: copy nodes at root, drill into sub-graph, paste — verify pasted nodes appear in sub-graph, not root
  - [ ] 6-3. Duplicate: duplicated nodes appear offset from originals with new IDs; internal edges preserved
  - [ ] 6-4. Context menu rendering: right-click on canvas shows canvas items; right-click on node shows node items; right-click on edge shows edge items
  - [ ] 6-5. Context menu "Group into Composite" disabled: select 2 nodes with a control edge crossing boundary — verify option is disabled
  - [ ] 6-6. Edge reconnection: drag edge to new target — verify `danGraph` edge updated; undo restores original wiring
  - [ ] 6-7. Keyboard shortcuts: Cmd+C/V/D trigger correct store actions; shortcuts disabled when text input focused

- [ ] 7. Docs sync
  - [ ] 7-1. Update `docs/architecture.md` — document clipboard slice, ContextMenu component, edge reconnection
  - [ ] 7-2. Update `docs/todo.md` — check off 6-2 items
  - [ ] 7-3. Append `docs/changelog.md` entry

## Decisions

- Clipboard edge type is `Record<string, unknown>[]` (not `DanEdge[]`) to match the store's generic edge data pattern; cast to `DanEdge` only when converting back to React Flow edges via `danEdgeToReactFlow`.
- Context menu state lives in local `useState` in `GraphCanvas` (not in the Zustand store) since it's ephemeral UI state.
- Context menu uses `position: fixed` with `clientX`/`clientY` to avoid coordinate system issues with the parent div.
- Edge reconnection validation reuses the existing `isValidConnection` utility (self-connect + duplicate checks) before applying the change.
- Advanced context menu items (Add Node, Drill In, Group into Composite, Select All) deferred to later sub-plans that implement the underlying features (6-3 for group, 6-5 for composite creation).

## Notes

- Clipboard is in-app only (Zustand state), not system clipboard. We don't use the browser Clipboard API because DAN nodes are complex objects, not plain text.
- Cross-layer paste intentionally drops edges that reference nodes not in the clipboard. The alternative (creating stub nodes) would be surprising and hard to undo.
- "Group into Composite" is surfaced in the node context menu but the actual grouping algorithm is implemented in 6-5. The context menu in 6-2 just calls the store action and handles the disabled state.
- `onReconnect` in React Flow v12 replaces the older `onEdgeUpdate` callback. The handler receives both the old and new connection, making it straightforward to update the DAN edge.
