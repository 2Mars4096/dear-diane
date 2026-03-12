# 1-1: App Shell & Panel Framework

**Parent:** [1-ui-spec](1-ui-spec.md)
**Status:** in-progress
**Goal:** Build the shared infrastructure that all job-based modes require: app shell, mode switching, panel system, project sidebar, and persistent chat bar.

## Context

The existing `editor/` React app (React + Tailwind v4 + Zustand + Vite) is the foundation. Currently it's a single-purpose workflow editor with a chat panel. This plan transforms it into a multi-mode application shell wrapped in Electron for native desktop capabilities and cross-platform rendering consistency, while keeping existing editor functionality intact.

## Tasks

### 0. Electron Setup
- [x] 0-1. Initialize Electron project (`electron/` alongside `editor/`): main process entry, preload script, IPC bridge
- [x] 0-2. Configure Electron to load Vite dev server in development, bundled build in production
- [x] 0-3. Native file dialog APIs (`dialog.showOpenDialog`, `dialog.showSaveDialog`) exposed to renderer via IPC
- [x] 0-4. File system access via Node.js `fs` in main process, exposed through contextBridge
- [ ] 0-5. File watcher (`fs.watch` / chokidar) for local file change detection
- [x] 0-6. System tray icon with menu (open window, status, quit) — pairs with `dan-serve` daemon
- [x] 0-7. Window configuration: title, min size, remember position/size (`electron-window-state`)
- [ ] 0-8. Auto-updater (`electron-updater`), release pipeline (GitHub Releases or custom)
- [ ] 0-9. macOS build + code signing + notarization; Windows NSIS/Squirrel installer; Linux AppImage/deb
- [x] 0-10. Fallback: the same React app still works as a browser web app when served directly by `dan-serve`
- [x] 0-11. Security: contextIsolation enabled, nodeIntegration disabled, all native APIs through preload + IPC

### 1. App Layout & Mode Bar
- [x] 1-1. Top-level layout component: mode bar (top) + sidebar (left) + workspace (center) + chat bar (bottom)
- [x] 1-2. Mode bar component with icons + labels for each mode (Chat active by default; unbuilt modes show placeholder)
- [x] 1-3. Mode switching: mode bar click, keyboard shortcut (Cmd+1..6), `/mode <name>` in chat bar
- [x] 1-4. Mode state in URL hash (`#research`, `#dev`, `#analytics`, etc.) for bookmarkable deep links
- [ ] 1-5. Transition animation between modes (panel crossfade, ~200ms)
- [ ] 1-6. Mode indicator in page title and favicon accent

### 2. Panel Framework
- [ ] 2-1. Generic `Panel` component: header (title, actions, collapse toggle), content area, resize handles
- [ ] 2-2. `PanelLayout` component: accepts a mode-specific layout config (which panels, grid positions, initial sizes)
- [ ] 2-3. Panel resize via drag handles (horizontal and vertical splits, min/max constraints)
- [ ] 2-4. Panel collapse to icon strip (click icon to restore, keyboard shortcut per panel)
- [ ] 2-5. Layout persistence in localStorage per mode (remembers user's panel sizes and collapse state)
- [ ] 2-6. Default layout configs shipped as JSON per mode (user overrides layered on top)
- [ ] 2-7. Panel content slot: each mode registers its panel components; PanelLayout renders them by ID
- [ ] 2-8. Responsive breakpoints: panels stack vertically on narrow viewports, hide optional panels on mobile

### 3. Project Sidebar
- [ ] 3-1. Project list from existing ProjectStore API (`/api/projects`)
- [ ] 3-2. Active project indicator + click-to-switch
- [ ] 3-3. Task list within active project (from existing TaskStore)
- [ ] 3-4. Quick-access section: recent files, recent runs, pinned items
- [ ] 3-5. Collapsible sidebar (toggle via button or Cmd+B)
- [ ] 3-6. New project / new task inline creation
- [ ] 3-7. Project search / filter

### 4. Persistent Chat Bar
- [ ] 4-1. Always-visible chat input at bottom of every mode (fixed position, not part of panel grid)
- [ ] 4-2. Single-line by default, expands to multi-line on focus or Shift+Enter
- [ ] 4-3. Existing features carry over: slash commands, @mentions, file drag-and-drop
- [ ] 4-4. Routes through existing chat API (`/api/chat/message`)
- [ ] 4-5. Response routing: in Chat mode → full conversation view; in other modes → panel-specific or toast
- [ ] 4-6. Toggle to expand chat bar into split chat panel (Cmd+J)
- [ ] 4-7. Streaming indicator (typing dots) while response is generating
- [ ] 4-8. Quick-action buttons: attach file, voice input, command palette

### 5. Shared Components
- [ ] 5-1. Notification center (top-right): run completions, scheduled task results, errors, proactive follow-ups
- [ ] 5-2. Settings panel (sidebar or modal): model config, verbosity, mode defaults, domain profile selection
- [ ] 5-3. Command palette (Cmd+K): unified search across modes, projects, commands, recent actions
- [ ] 5-4. Keyboard shortcut system: global shortcuts (mode switch, project switch, command palette), per-mode shortcuts registered dynamically
- [ ] 5-5. Loading/skeleton states for all panels
- [x] 5-6. Error boundary per panel (one panel crash doesn't take down the workspace) — `ErrorBoundary` class component wraps each mode in `AppShell`
- [ ] 5-7. Breadcrumb / context indicator: shows active mode > project > task

### 6. State Management
- [x] 6-1. Zustand store redesign: global slice (active mode, active project, user prefs, notifications) + per-mode state slices
- [x] 6-2. Mode state isolation: switching modes preserves each mode's internal state independently
- [ ] 6-3. Project context carryover: mode switch keeps active project, task, and conversation
- [ ] 6-4. WebSocket connection sharing: single connection per session, events dispatched to active mode's handler
- [ ] 6-5. Event router: `EngineEvent → mode handler → panel update` mapping table per mode

### 7. Migration & Backward Compatibility
- [x] 7-1. Wrap existing workflow editor (React Flow canvas + log panel + toolbar) as Operations mode workspace
- [x] 7-2. Wrap existing ChatPanel as Chat mode workspace (full-screen layout)
- [x] 7-3. All existing editor functionality (multi-tab workflows, execution, logging, validation) works within Operations mode
- [x] 7-4. URL routing: `/` → Chat mode, `/editor` or `/operations` → Operations mode (backward compat)
- [x] 7-5. Existing API endpoints unchanged — no backend modifications in this plan

## Decisions
- `useAppStore` is a separate Zustand store from `useGraphStore` — keeps mode/shell state independent from graph state
- Both Chat and Operations modes are always mounted (hidden via CSS `hidden` class) to preserve state across switches
- `ChatPanel` gained a `fullScreen` prop (default `false`) — avoids forking the component
- `CommandPalette` moved into `OperationsMode` (not global shell) because it depends on `useReactFlow()` which needs `ReactFlowProvider`
- `electronBridge.ts` abstracts native APIs so the same React code works in both Electron and browser

## Notes
- The existing editor is additive, not rewritten. This plan wraps it.
- Panel framework must be generic enough for all 6 modes (don't bake in Research-specific assumptions).
- Performance budget: mode switch < 100ms, panel resize < 16ms (60fps), initial load < 3s.
- Consider allotments/Allotment.js or similar for panel splits, or custom implementation.
- Test with: switch modes rapidly, resize panels to extremes, collapse all panels, use on 13" screen.
- Electron main process is Node.js/TypeScript — same language as the frontend, no Rust toolchain needed.
- File operations split: UI-level interactions (open/save dialogs, file browsing, drag-drop) use Electron native APIs via IPC. Agent-driven file ops (tool calls, artifact storage) continue using `dan-serve` backend.
- The React code must stay Electron-agnostic where possible (abstract native APIs behind a service layer with `window.electronAPI` bridge) so the browser fallback keeps working.
- Security: renderer process has no direct Node.js access. All native operations go through preload script + contextBridge. Same security model as Cursor/VS Code.
