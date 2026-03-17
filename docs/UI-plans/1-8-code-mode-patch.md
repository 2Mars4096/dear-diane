# 1-8: Code Mode Production Patch

**Parent:** [1-ui-spec](1-ui-spec.md)
**Status:** completed
**Goal:** Fix broken infrastructure and close the gap between "180 checkboxes done" and "I can actually develop a real project in this IDE."

## Context

Code mode's feature checklist reads 180/180, but an honest audit ([Code Mode production readiness review](f6bb5e88-981c-4a9b-9d4f-253f4136f090)) found that several subsystems are demo-quality rather than production-quality:

1. **The editor doesn't build.** ~70 TypeScript errors block `npm run build`, Electron packaging, and CI.
2. **Two terminals required.** Users run `dan-serve` in one terminal and `npm run dev` in another. No single-launch.
3. **No first-run experience.** Cold open gives no guidance — no "open a folder," no feature tour.
4. **LSP partially wired.** Language servers run, but OutlineView, SymbolSearch, and PeekDefinition still use regex fallbacks instead of querying the running servers.
5. **AI coding limited to open files.** No multi-file agent edit flow wired (component exists but unused), no @ references in chat.
6. **Unimplemented commands.** `/cost` and `/retry` are listed as bypass prefixes in `dispatcher.py` but have no handler implementations.

This plan fixes the **blocking** issues, upgrades the **weakest links** to real-tool quality, and leaves large new features (codebase embeddings, full agent loop) as separate future plans.

## Tasks

### Slice A — Build & Launch (blocking)

- [x] 1. **Fix TypeScript build errors** *(build already passes — all errors were fixed in prior sessions)*
  - [x] 1-1 through 1-9: All type errors resolved
  - [x] 1-10. `npm run build` passes with zero errors

- [x] 2. **Single-launch experience**
  - [x] 2-1. Electron main process spawns `dan-serve` as a child process on app start, with health-check polling *(already existed in main.ts startBackend())*
  - [x] 2-2. Backend process stdout/stderr piped to renderer via `backend:log` IPC events; `nativeBackend.onLog()` bridge
  - [x] 2-3. Graceful shutdown: `before-quit` kills the backend child process *(already existed)*
  - [x] 2-4. Settings toggle: `autoStartBackend` in EditorSettings (default on); `backend:restart` and `backend:stop` IPC handlers; toggle in GlobalSettingsPanel
  - [x] 2-5. Status indicator: `BackendStatusIndicator.tsx` in AppShell status bar (green/yellow/red dot, click-to-restart)
  - [x] 2-6. Fallback: if `dan-serve` is already running externally, detect via port check and skip spawn *(already existed)*

- [x] 3. **First-run onboarding**
  - [x] 3-1. Enhanced WelcomeScreen: DAN logo, "Open Folder" button wired to dialog, recent workspaces list from pinnedRoots, keyboard shortcut hints
  - [x] 3-2. ProjectDetectionToast: auto-detects project type when folder opened, shows frameworks/stack, auto-dismiss after 5s
  - [x] 3-3. FeatureTour: 4-step tooltip walkthrough (mode bar, AI chat, file explorer, terminal) with SVG mask backdrop, persisted to localStorage
  - [x] 3-4. Quick Setup section in WelcomeScreen: toggle switches for minimap, sticky scroll, bracket colors, word wrap, format-on-save

### Slice B — LSP Quality Upgrade

- [x] 4. **Wire LSP to OutlineView**
  - [x] 4-1. Replace regex-based symbol extraction with `nativeLsp.documentSymbol()` call
  - [x] 4-2. Fall back to regex extraction when no LSP server is running
  - [x] 4-3. Hierarchical display from LSP `DocumentSymbol[]`
  - [x] 4-4. Auto-refresh on `textDocument/didChange` debounced (500ms)

- [x] 5. **Wire LSP to SymbolSearch**
  - [x] 5-1. Replace regex-based workspace symbol search with `workspace/symbol` LSP request
  - [x] 5-2. Expand search scope beyond open files — full workspace via LSP
  - [x] 5-3. Fall back to regex extraction over open files when no LSP server available
  - [x] 5-4. Show symbol kind icons from LSP `SymbolKind` enum

- [x] 6. **Wire LSP to PeekDefinition**
  - [x] 6-1. Replace open-file-only search with `textDocument/definition` LSP request
  - [x] 6-2. Multi-location support: show tabs in peek window
  - [x] 6-3. Load file content from disk for definitions not currently open
  - [x] 6-4. Fall back to open-file regex search when LSP unavailable

- [x] 7. **Additional language server configs**
  - [x] 7-1. Add Go (`gopls`) — detect via `go.mod`/`go.sum`; `commandExists` check with install hint
  - [x] 7-2. Add Rust (`rust-analyzer`) — detect via `Cargo.toml`; system binary check with install hint
  - [x] 7-3. Add C/C++ (`clangd`) — detect via `CMakeLists.txt`/`compile_commands.json`/`Makefile`; system binary check
  - [x] 7-4. Document how to add custom language servers in settings or workspace config

### Slice C — AI Coding Upgrade

- [x] 8. **@ references in chat sidebar**
  - [x] 8-1. `@` trigger in chat input: `CodeMentionAutocomplete.tsx` with file/symbol/folder completions
  - [x] 8-2. File completions: search pinned roots (2 levels deep) + open files, file icons by extension
  - [x] 8-3. Symbol completions: `nativeLsp.workspaceSymbol()` with 300ms debounce, SymbolKind icons
  - [x] 8-4. Folder completions: directory tree navigation from pinned roots
  - [x] 8-5. Mention chips strip above composer; `expandMentionContext()` reads file content, lists folders, collects symbols into `surfaceContext`
  - [x] 8-6. Backend: `mention_resolver.py` extended with `symbol` and `folder` resolution; `MentionRef` type accepts new mention types

- [x] 9. **Multi-file agent edit flow**
  - [x] 9-1. Multi-file writes collected into `MultiFileEdit` review panel via `useCodeStore`; `ChatMessage.tsx` detects 2+ file-write tool calls; `ModeChatSidebar` snapshots pre-write content for diff
  - [x] 9-2. Per-file diff editor with accept/reject per file
  - [x] 9-3. Accept All / Reject All across all files
  - [x] 9-4. "Apply & Test" action: accepts all edits then runs detected test suite via terminal
  - [x] 9-5. Both `ChatSidebar` and `ModeChatSidebar` wired to open multi-file review panel

- [x] 10. **Project-aware AI context**
  - [x] 10-1. Send active file path/content/language + open file paths in `surfaceContext`
  - [x] 10-2. Import graph neighbors: `importResolver.ts` extracts ES import/require/Python import paths
  - [x] 10-3. Workspace-level context: `detectProjectType()` cached with 60s TTL, includes type/name/frameworks/package_manager
  - [x] 10-4. Context budget: `contextBudget.ts` prioritized truncation (15kB: active file 8kB > selection 2kB > imports 4kB > project always)

### Slice D — Bug Fixes & Polish

- [x] 11. **Implement `/cost` and `/retry` commands**
  - [x] 11-1. Both implemented. `/cost` queries telemetry store with per-model breakdown. `/retry` replays last user message.
  - [x] 11-2. `handle_cost_command` in runtime.py; `/retry` via inline dispatch
  - [x] 11-3. 8 new tests for both commands (all pass)

- [x] 12. **Debug quality-of-life**
  - [x] 12-1. Auto-generate launch configs from workspace detection (Node.js + Jest/Vitest, Python + pytest/FastAPI, Go, Rust)
  - [x] 12-2. Debug hover evaluation: Monaco hover provider evaluates variables via DAP `evaluate` when paused
  - [x] 12-3. Conditional breakpoint UI: gutter right-click menu, inline condition editor, syncs to DAP `setBreakpoints`

- [x] 13. **Editor reliability**
  - [x] 13-1. Suppress stale-revision warning when no graph is loaded — guarded by `graphId && graphId !== "_scratch"`
  - [x] 13-2. Fix `persistent-chat:send` event loss — bridge routes events into active mode sidebar

## Priority & Sequencing

```
Slice A (blocking)     ──▶  Must complete first; nothing ships without a build
  Task 1 (build fix)          Parallelizable internally
  Task 2 (single-launch)      Depends on working build
  Task 3 (onboarding)         Depends on single-launch

Slice B (LSP upgrade)  ──▶  Independent of Slice A; can start in parallel
  Tasks 4,5,6                 Parallelizable (3 different components)
  Task 7                      After 4-6 land (same lspManager pattern)

Slice C (AI upgrade)   ──▶  Mostly independent; highest user-perceived value
  Task 8 (@ refs)             Soft dep on Task 5 (symbol completions benefit from LSP)
  Task 9 (multi-file)         No dependency
  Task 10 (context)           No dependency

Slice D (bug fixes)    ──▶  Independent; small items parallelizable
  Tasks 11-13                 All independent
```

## Out of Scope (future plans)

These are important but large enough to warrant their own plans:

- **Codebase indexing / embeddings** — Vector store, incremental updates, semantic search across entire project. High impact but architecturally complex.
- **Full agent mode loop** — DAN autonomously creates files, runs terminals, reads errors, fixes, iterates (Cursor agent-style). Backend capability exists; editor UI/orchestration is the gap.
- **Remote development** — SSH, containers, WSL. Very high difficulty.
- **Notebook / Jupyter support** — Important for data science; separate plan.
- **Extension host full API** — Current shim covers static contributions; dynamic `vscode.*` API coverage is a long tail.

## Decisions

- Task 1: Build was already fixed in prior sessions — `npm run build` passes with 0 errors
- Task 2: Backend spawn/shutdown/fallback already existed in main.ts. Added IPC log forwarding, status indicator, and settings toggle. Backend bridge added to ElectronAPI interface (review removed `any` casts).
- Task 3: Enhanced existing WelcomeScreen rather than creating a separate component. Feature tour uses SVG mask-based backdrop with data-tour attribute selectors.
- Task 7: Go/Rust/C++ servers use `commandExists()` system binary detection pattern with user-friendly install hints
- Task 8: Created CodeMentionAutocomplete separate from the graph MentionAutocomplete; code mode swaps component conditionally. Extended MentionType with "symbol" | "folder".
- Task 9: Multi-file review wired via both tool-call detection (ModeChatSidebar) and regex path detection (ChatSidebar) for completeness
- Task 10: Import resolver uses inline path helpers instead of path-browserify to avoid a new dependency. Context budget uses 15kB total with priority tiers.
- Task 11: Both `/cost` and `/retry` implemented (not removed). `/cost` queries telemetry store with per-model breakdown. `/retry` replays last user message through normal dispatch
- Task 12: Auto-detect suggests configs but doesn't overwrite existing ones. Debug hover only activates when status === "paused". Conditional breakpoints use right-click gutter menu.
- Task 13-1: Stale-revision banner suppressed with simple `graphId` null/scratch guard

## Review Fixes Applied

- ChatMessage mention regex: added `symbol|folder` to the render pattern
- BackendStatusIndicator: imported and rendered in ModeBar
- Conditional breakpoint: clearing condition now calls `setBreakpointCondition("")` instead of silently preserving
- Peek definition: "Open File" loads from disk via `nativeFs.readFile` when file not in editor
- DebugPanel: null-coalesced `frame.line` to prevent `undefined` in `setPausedLocation`
- ProjectDetectionToast: fixed hook ordering and stale closure for `handleDismiss`
- Backend IPC: added missing handlers in main.ts, bridge in preload.cjs, typed interface in electronBridge.ts
- Multi-file review: now detects backend `file_write` tool calls, preserves tri-state review state, and accept/reject applies real filesystem writes or deletes rejected newly created files
- Workspace symbols: completed missing Electron IPC chain (`preload.cjs` -> `main.ts` -> `lspClient.ts`) so symbol autocomplete/search can use LSP results
- Surface context: `ModeChatSidebar` now builds project-aware context (active file, open files, import neighbors, project detection, code mentions), tier executors/router forward it, and `ChatManager` injects it into prompts
- Onboarding mount: `ProjectDetectionToast` and `FeatureTour` are now mounted in `CodeMode`, with working `data-tour="mode-bar"` and `data-tour="bottom-panel"` anchors
- `/retry` + bypass: user turns now persist replay-safe metadata, `/retry` restores it, and dispatcher bypass commands no longer create bogus projects/tasks before processing
- Historical multi-file review: review buttons now open immutable per-message snapshots instead of reconstructing from transient sidebar state, so delayed review clicks cannot misclassify existing files as newly created or delete the wrong path
- Multi-root `Apply & Test`: test execution now resolves a common pinned root from the reviewed files and skips the shell handoff instead of running the wrong suite when edits span roots
- Workspace-symbol dedupe: aggregate IPC results now keep symbols from distinct source ranges even when name/container/URI match
- Surface-context prompt budget: `ChatManager` now truncates oversized active-file, selection, mentioned-file, and folder payloads server-side to cap prompt growth
- Dispatcher serialization: `/build` and `/retry` are excluded from synthetic bypass IDs so they queue and reserve the real project lock like other long-running turns
- Stop fallback: Code Mode sidebar stop now recovers locally when `/api/chat/{channel}/stop` returns `404` for an already-finished/missing stream, so compact chat no longer stays stuck in a permanent "Stop generation" state after live smoke tests

## Notes

- 2026-03-16: Added shared `editorChat.ts` so editor/research AI callers that still pointed at the dead `/api/chat/editor/message` route now go through the live `/api/chat/message` pipeline instead.
- 2026-03-16: Per-mode chat sidebars and full Chat now turn appended file chips into explicit prompt context.
- 2026-03-16: Follow-up hardening for attachments.
- 2026-03-17: All 13 tasks completed and reviewed. Prior session changes (Tasks 4-7, 9, 11, 13) were lost from disk and re-implemented. Remaining minor items (7-4 docs, 8-6 backend mentions) also completed. Full review found and fixed 6 bugs + 1 missing infrastructure gap. Final state: build 0 errors, 849 tests pass.
- 2026-03-17: Live smoke testing with the backend running showed `/retry` itself was healthy; the reproducible frontend bug was the compact Code Mode stop button getting wedged after a `404` stop response. Sidebar stop now treats that case as a completed local stop so follow-up turns can proceed without a refresh.
- 2026-03-17: Residual-risk patch landed after the final review. Targeted concierge regressions pass, editor build passes, and the remaining end-to-end gaps (multi-file review, workspace symbols, surface context, onboarding mount, `/retry` semantics, dispatcher bypass) are closed.
- 2026-03-17: Second review hardening landed after the residual-risk patch. Historical multi-file review snapshots are now persisted per message, `/build` and `/retry` serialize on the real project ID, server-side `surface_context` is budgeted, multi-root `Apply & Test` avoids false-positive test runs, and focused validation passes (`75 passed, 1 skipped`, editor build, `ChatMessage` unit test).
