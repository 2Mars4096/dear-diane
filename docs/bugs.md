# Known Issues & Failed Approaches

## Open Bugs

(none currently)

## Known Limitations

- **No graph validation in editor**: The frontend saves graphs without running the Python validation rules (`validate_graph`). Invalid graphs (missing entry points, broken edges) can be saved and will fail at run time. Validation feedback in the editor is a future enhancement.
- **Single-user only**: The run manager holds state in memory. Multiple browser tabs can connect to the same run via WebSocket, but there's no multi-user session isolation.
- **No undo/redo**: React Flow supports undo/redo via state snapshots, but it's not wired up yet.
- **Node.js version warning**: Vite 7.x and `@vitejs/plugin-react` require Node.js 20.19+ or 22.12+. Current dev environment runs 20.17 — builds succeed with warnings but may break on future Vite updates. Fix: upgrade Node.js.
- **`Object.groupBy` requires ES2024**: `NodePalette` uses `Object.groupBy` which is available in all modern browsers (Chrome 117+, Safari 17.4+, Firefox 119+) but not in older targets. If older browser support is needed, replace with a manual reduce or lodash `groupBy`.

## Failed Approaches (do not retry)

(none yet)
