# Known Issues & Failed Approaches

## Open Bugs

(none)

## Resolved Bugs

- **Drop position wrong when zoomed/panned**: Fixed — replaced manual `clientX - bounds.left` with `screenToFlowPosition()` from `useReactFlow()` in `GraphCanvas.tsx`.
- **Node ID collisions after page refresh**: Fixed — replaced module-level `_counter` with `Date.now()` + random suffix in `graphAdapter.ts`.
- **Delete key doesn't fire reliably**: Fixed — removed wrapper `onKeyDown` handler, added `deleteKeyCode={["Delete", "Backspace"]}` prop to `<ReactFlow>` in `GraphCanvas.tsx`.
- **`onConnect` creates mismatched edge IDs**: Fixed — extracted `Date.now()` to a single `edgeId` const in `useGraphStore.ts`.
- **`hasBodyGraph` in App.tsx uses useCallback, should be useMemo**: Fixed — changed to `useMemo` returning a boolean, updated call site from `hasBodyGraph()` to `hasBodyGraph`.
- **`Object.groupBy` compatibility in `NodePalette`**: Fixed — replaced `Object.groupBy` with a typed `reduce` grouping implementation, removing ES2024 dependency.

## Known Limitations

- **No graph validation in editor**: The frontend saves graphs without running the Python validation rules (`validate_graph`). Invalid graphs (missing entry points, broken edges) can be saved and will fail at run time. Validation feedback in the editor is a future enhancement.
- **Single-user only**: The run manager holds state in memory. Multiple browser tabs can connect to the same run via WebSocket, but there's no multi-user session isolation.
- **No error feedback — silent API failures**: All store actions (`saveGraph`, `startRun`, `loadGraphList`, etc.) have no try/catch. If the backend is down or returns an error, the user sees nothing — no toast, no alert, no status change. Need a notification/toast system and try/catch wrappers.
- **No loading states**: No spinners or indicators for graph loading, saving, run starting, or initial graph list fetch. User clicks "Run" and the button grays out with no indication something is happening until WebSocket events arrive.
- **No connection validation**: React Flow allows connecting any handle to any handle — output to output, incompatible types, etc. No `isValidConnection` callback. Invalid wiring saves fine and fails at engine runtime with confusing errors.
- **ConfigPanel is a generic field dump**: Iterates all non-skipped fields without logical grouping. `prompt_template` and `code` textareas are only `h-20` (80px). JSON fields (`tool_config`, `route_descriptions`) have a raw textarea with silent `JSON.parse` catch — no validation feedback. Edge config is a read-only `<pre>` block. No way to add/remove/rename ports.
- **Fixed-height bottom panel**: `h-44` (176px) is hardcoded. Not resizable. Too small for log inspection during long runs, too big when user wants canvas space. Standard UX is a draggable splitter.
- **Two header bars waste vertical space**: `GraphSwitcher` and `RunPanel` are separate bars stacked vertically (~70px total). Could be merged into one compact toolbar.
- **No keyboard shortcuts**: No Cmd+S for save, no standard key bindings beyond the (broken) Delete key.
- **No undo/redo**: React Flow supports undo/redo via state snapshots, but it's not wired up yet.
- **App title/favicon are Vite defaults**: HTML title is "editor", favicon is `vite.svg`. Basic branding gap.
- **Node.js version warning**: Vite 7.x and `@vitejs/plugin-react` require Node.js 20.19+ or 22.12+. Current dev environment runs 20.17 — builds succeed with warnings but may break on future Vite updates. Fix: upgrade Node.js.

## Failed Approaches (do not retry)

(none yet)
