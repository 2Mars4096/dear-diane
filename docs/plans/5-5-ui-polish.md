# 5-5: UI/UX Polish

**Parent:** [5-phase-3.5-frontend-design](5-phase-3.5-frontend-design.md)
**Status:** completed
**Goal:** Polish the DAN visual editor with error handling, loading states, connection validation, a merged toolbar, app identity, ConfigPanel improvements, resizable panels, node icons, keyboard shortcuts, edge labels, syntax highlighting, and auto-layout.

## Tasks

- [ ] 1. Error handling + toast notifications
  - [ ] 1-1. Add a Zustand toast slice to `useGraphStore.ts`: `toasts: Array<{ id, type, message, duration? }>`, `addToast(toast)`, `removeToast(id)`; toast types: `success`, `error`, `info`, `warning`
  - [ ] 1-2. Create `editor/src/components/ToastContainer.tsx` — fixed bottom-right container, CSS-animated slide-in/fade-out; each toast: small card with icon (✓, ✗, ⓘ, ⚠) and message; auto-dismiss after 4s (error: 6s)
  - [ ] 1-3. Wrap critical async actions in `useGraphStore`: `loadGraph`, `loadGraphList`, `saveGraph`, `createGraph`, `deleteGraph`, `startRun`, `resumeRun` — on success show `addToast({ type: 'success', message })`, on catch show `addToast({ type: 'error', message: err.message })`
  - [ ] 1-4. Add a React error boundary around `App.tsx` content (or the main layout) — on error render fallback UI with message + "Reload" button; optional: `addToast({ type: 'error', message })` in componentDidCatch
  - [ ] 1-5. Mount `ToastContainer` in `App.tsx` above the main layout

- [ ] 2. Loading states (spinners for graph load, save, run)
  - [ ] 2-1. Add loading flags to `useGraphStore.ts`: `loadingList`, `loadingGraph`, `savingGraph`, `running` (or reuse `runStatus === 'running'` for run); set true at start of each async action, false on resolve/reject
  - [ ] 2-2. Add simple CSS spinner (Tailwind `animate-spin` + `border-2 border-t-transparent` or inline SVG) — create `editor/src/components/Spinner.tsx` reusable component (props: `size?: 'sm' | 'md'`)
  - [ ] 2-3. In toolbar (merged Task 4): show spinner on Save button when `savingGraph`, Run button when `running` (or disable); graph dropdown when `loadingGraph`; optionally show overlay on canvas during `loadingGraph`
  - [ ] 2-4. Wire `loadGraphList`, `loadGraph`, `saveGraph`, `startRun` to set/clear these flags; ensure `addToast` is called after load/save/run completes even on error

- [ ] 3. Connection validation (`isValidConnection`)
  - [ ] 3-1. Add `isValidConnection` prop to `ReactFlow` in `GraphCanvas.tsx` — callback `(params) => boolean` that receives `{ source, target, sourceHandle, targetHandle }`
  - [ ] 3-2. Implement validation: (a) prevent self-connections (`source === target`); (b) prevent duplicate edges (same source/target/handles); (c) for data edges: ensure source port exists on source node and target port exists on target node (read `nodes` and `edges` from store)
  - [ ] 3-3. Stretch: schema compatibility — compare `output_ports[sourceHandle].schema` with `input_ports[targetHandle].schema`; if schema types differ (e.g. `{ type: 'string' }` vs `{ type: 'array' }`), reject or show warning toast
  - [ ] 3-4. Export validation logic as `editor/src/lib/connectionValidation.ts` for testability; consume in GraphCanvas

- [ ] 4. Merge GraphSwitcher + RunPanel into one compact toolbar
  - [ ] 4-1. Create `editor/src/components/EditorToolbar.tsx` — single horizontal bar: `flex items-center justify-between px-3 py-1.5 bg-white border-b border-gray-200`
  - [ ] 4-2. Left section: "DAN" branding + divider + graph dropdown (from `GraphSwitcher`) + Create/Delete (+ New inline flow) — migrate logic from `GraphSwitcher.tsx`
  - [ ] 4-3. Center-right: Save (with dirty indicator), Run, Resume, Disconnect — migrate from `RunPanel.tsx`; use same button styles, integrate loading spinners from Task 2
  - [ ] 4-4. Far right: status badge (runStatus + runId) — same `STATUS_COLORS` as current RunPanel
  - [ ] 4-5. Update `App.tsx`: replace `<GraphSwitcher />` and `<RunPanel />` with single `<EditorToolbar />`
  - [ ] 4-6. Delete or deprecate `GraphSwitcher.tsx` and `RunPanel.tsx` (or keep as internal sub-components if preferred — document in plan notes)

- [ ] 5. App title + favicon
  - [ ] 5-1. Update `editor/index.html`: `<title>DAN — Deep Agent Network</title>`; replace `href="/vite.svg"` with a custom favicon (create `public/favicon.svg` or `public/favicon.ico` — simple "DAN" or graph icon)
  - [ ] 5-2. Add `public/favicon.svg` — minimal SVG (e.g. node/edge diagram or "D" letter) compatible with `type="image/svg+xml"`

- [ ] 6. ConfigPanel improvements (field grouping, larger textareas, validation, editable edge props)
  - [ ] 6-1. Field grouping: define `FIELD_GROUPS` in `ConfigPanel.tsx` or `editor/src/lib/configSchema.ts` — group node fields by semantics (e.g. "Model", "Prompt", "Code", "Control", "Metadata"); render grouped sections with `<h3>` headers
  - [ ] 6-2. Larger textareas: raise threshold from 60 chars to ~120 for auto-textarea; for `prompt_template`, `code`, `system_prompt` use `min-h-32` or `h-40`; add `resize-y` and `font-mono` for code fields
  - [ ] 6-3. Basic validation: required fields (e.g. `model`, `prompt_template` for LLM) — show red border or inline error when empty on blur; optional: `addToast({ type: 'warning' })` when saving with incomplete config
  - [ ] 6-4. Editable edge props: replace read-only `<pre>` in Edge Config with form — `edge_type` (select: data/control/context), `condition` (for control), `context_key` and `mode` (for context); add `updateEdgeData(edgeId, partial)` to `useGraphStore` and wire ConfigPanel
  - [ ] 6-5. JSON fields: keep textarea for complex objects but add "Format" button to prettify; show syntax errors in red (try/catch on parse)

- [ ] 7. Resizable panels (draggable splitters)
  - [ ] 7-1. Add `react-resizable-panels` or `allotment` as dependency — choose one; `allotment` is lightweight and works well with flex layouts
  - [ ] 7-2. Wrap main layout in `App.tsx`: left `NodePalette` (min 120px, default 208px), center `GraphCanvas + bottom panel`, right `ConfigPanel` (min 200px, default 288px); use `Allotment` or `PanelGroup` with `Panel` and `PanelResizeHandle`
  - [ ] 7-3. Bottom panel (Logs/Output): make vertically resizable — min height 80px, default 176px (`h-44`), max ~50vh
  - [ ] 7-4. Stretch: persist panel sizes in `localStorage` via a small hook (`usePanelSizes`)

- [ ] 8. Node type icons in palette and node header
  - [ ] 8-1. Create `editor/src/lib/nodeIcons.tsx` — map `NodeTypeString` to inline SVG icons (compact, ~16x16): llm (sparkle/chat), tool (wrench), code (braces), if_else (branch), while_loop (loop), for_each (grid/list), reduce (merge), router (compass), human (user), composite (box/layers)
  - [ ] 8-2. Add icon to each palette item in `NodePalette.tsx` — render `<NodeIcon type={item.type} />` left of label; maintain same drag/click behavior
  - [ ] 8-3. Add icon to `DanNode.tsx` header — render same `NodeIcon` left of `d.name || d.node_type`; keep header compact

- [ ] 9. Keyboard shortcuts (Cmd+S, standard bindings)
  - [ ] 9-1. Add `useEffect` in `App.tsx` or create `editor/src/hooks/useKeyboardShortcuts.ts`: listen for `keydown` with `metaKey`/`ctrlKey` + `s` → prevent default, call `saveGraph()`
  - [ ] 9-2. Ensure Delete/Backspace continues to work via React Flow `deleteKeyCode` (already set in `GraphCanvas.tsx`)
  - [ ] 9-3. Stretch: Cmd+Z undo — requires storing history in `useGraphStore` (e.g. `pastGraphs: DanGraph[]`, `futureGraphs: DanGraph[]`); implement only if straightforward
  - [ ] 9-4. Add shortcut hint to Save button tooltip or status bar: "⌘S to save"

- [ ] 10. Edge labels (port names on data edges)
  - [ ] 10-1. Update `graphAdapter.ts` `danEdgeToReactFlow`: for data edges, set `label` to `source_port → target_port` or `source_port` (user preference: show both ports for clarity); currently only non-data edges get labels
  - [ ] 10-2. Create custom edge type or use React Flow `BaseEdge` + `getBezierPath` with `EdgeLabelRenderer` — render label at edge midpoint; or use built-in `label` prop if React Flow supports it on default edges
  - [ ] 10-3. Style labels: small font (`text-[10px]`), `font-mono`, background `bg-white/90` or `bg-gray-50`, padding, rounded; ensure label doesn't overlap handles

- [ ] 11. Syntax highlighting (code fields, JSON preview)
  - [ ] 11-1. Add `highlight.js` (or `prism-react-renderer`) to `editor/package.json` — `highlight.js` is lighter, prism more customizable
  - [ ] 11-2. Create `editor/src/components/CodeField.tsx` — wraps `<textarea>` for edit mode; in view/preview mode render `<pre><code>` with syntax highlighting (language: `python` for code, `json` for JSON)
  - [ ] 11-3. Use in ConfigPanel: for `code`, `prompt_template` (optional), and JSON-serialized fields — render with highlight; for edit mode keep plain textarea, add "Preview" toggle or show highlight on blur
  - [ ] 11-4. JSON preview in LogPanel or OutputPreview: if output is object, render syntax-highlighted JSON; check `OutputPreview.tsx` and `LogPanel.tsx` for existing JSON display

- [ ] 12. Auto-layout (dagre)
  - [ ] 12-1. Add `@dagrejs/dagre` to `editor/package.json`; add `@types/dagre` if needed
  - [ ] 12-2. Create `editor/src/lib/layout.ts` — function `layoutGraph(nodes, edges): Position[]` that runs dagre layout, returns updated positions keyed by node id; use `rankdir: 'LR'`, `nodesep: 40`, `ranksep: 60`
  - [ ] 12-3. Add `applyAutoLayout` action to `useGraphStore` — call `layoutGraph(nodes, edges)`, then `set` nodes with new positions, set `dirty: true`
  - [ ] 12-4. Add "Layout" or "Auto-layout" button to toolbar (in `EditorToolbar.tsx`) — triggers `applyAutoLayout`; optional: add to canvas context menu or as floating control

- [ ] 13. Docs sync
  - [ ] 13-1. Update `docs/architecture.md` — add UI/UX Polish subsection under Visual Editor Frontend: toast system, loading states, connection validation, merged toolbar, ConfigPanel structure, resizable panels, node icons, shortcuts, edge labels, syntax highlighting, auto-layout
  - [ ] 13-2. Update `docs/todo.md` — mark Section E (UI/UX Polish) items complete per plan checklist
  - [ ] 13-3. Append `docs/changelog.md` entry on plan completion

## Decisions

- (filled in during execution)

## Notes

- **Toast implementation:** No external UI library — Zustand slice + CSS transitions. Avoids adding react-hot-toast, sonner, or similar.
- **Connection validation scope:** Initially validate port existence and no self/duplicate. Schema compatibility is a stretch goal — exact type matching may be too strict for flexible workflows.
- **Toolbar merge:** Single row reduces vertical chrome; graph selector + actions + status in one place. Aligns with LangFlow/Flowise compact header.
- **Resizable panels:** `allotment` or `react-resizable-panels` — both support controlled sizes and persistence; pick based on bundle size and API ergonomics.
- **Node icons:** Inline SVG preferred over icon font or image URLs — no extra requests, consistent with Tailwind-only approach.
- **Cmd+Z undo:** Stretch goal for this plan; can be added in a future iteration if state history is trivial to implement.
- **Edge labels:** Data edges currently have no label in `danEdgeToReactFlow`; adding `source_port` (and optionally `target_port`) improves readability when multiple edges connect same node pair.
- **Syntax highlighting:** `highlight.js` core + languages (python, json) — small footprint; alternative `prism` if theme/customization is needed.
- **Auto-layout:** dagre is the standard for React Flow; elkjs offers hierarchical layout but adds complexity — dagre first.
