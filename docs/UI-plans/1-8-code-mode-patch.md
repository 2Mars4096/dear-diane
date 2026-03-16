# 1-8: Code Mode Production Patch

**Parent:** [1-ui-spec](1-ui-spec.md)
**Status:** in-progress
**Goal:** Fix broken infrastructure and close the gap between "180 checkboxes done" and "I can actually develop a real project in this IDE."

## Context

Code mode's feature checklist reads 180/180, but an honest audit ([Code Mode production readiness review](f6bb5e88-981c-4a9b-9d4f-253f4136f090)) found that several subsystems are demo-quality rather than production-quality:

1. **The editor doesn't build.** ~70 TypeScript errors block `npm run build`, Electron packaging, and CI.
2. **Two terminals required.** Users run `dan-serve` in one terminal and `npm run dev` in another. No single-launch.
3. **No first-run experience.** Cold open gives no guidance — no "open a folder," no feature tour.
4. **LSP partially wired.** Language servers run, but OutlineView (`// Regex-based symbol extraction (until LSP is wired)`), SymbolSearch (regex over open files only), and PeekDefinition (`// Search open files for definitions (simple heuristic without LSP)`) still use regex fallbacks instead of querying the running servers.
5. **AI coding limited to open files.** No multi-file agent edit flow wired (component exists but unused), no @ references in chat.
6. **Unimplemented commands.** `/cost` and `/retry` are listed as bypass prefixes in `dispatcher.py` but have no handler implementations.

This plan fixes the **blocking** issues, upgrades the **weakest links** to real-tool quality, and leaves large new features (codebase embeddings, full agent loop) as separate future plans.

## Tasks

### Slice A — Build & Launch (blocking)

- [ ] 1. **Fix TypeScript build errors**
  - [ ] 1-1. Remove unused imports/variables (TS6133) across ~20 files (ModePreview, CallHierarchy, CodebaseQA, DebugPanel, ExtensionsPanel, FileExplorer, GitGraph, GitHubPanel, GitPanel, InlineEdit, MergeEditor, MonacoTabs, ProblemsPanel, TaskRunner, CodeCells, DataBrowser, PageSummary, WritingPane, RunHistoryPanel, PersistentChatBar, ThreadTabs, useLspDocSync)
  - [ ] 1-2. Fix ConfigPanel `graphId` scope — variable used in TestCaseSection block but not in scope in that render branch
  - [ ] 1-3. Fix GraphCanvas type errors — CompositeNode `input_ports` schema mismatch, `IsValidConnection` type
  - [ ] 1-4. Fix LogPanel — `React.JSX.Element` not found; replace with `React.ReactElement` or global `JSX.Element` from `react/jsx-runtime`, and verify React import style matches tsconfig (`import React` vs `import * as React`)
  - [ ] 1-5. Fix ProblemsPanel — `filePath` incorrectly derived from `m.source` (diagnostic source string like `"typescript"`) instead of `m.resource` (file URI from `IMarker`); use `m.resource?.toString()` for file path
  - [ ] 1-6. Fix InlineCompletion — remove unnecessary `(settings as unknown as Record<string, unknown>).inlineCompletionEnabled` cast; `useSettingsStore.getState()` returns `SettingsState` which includes `inlineCompletionEnabled` directly
  - [ ] 1-7. Fix InlineEdit + WritingPane — `editor.IRange` not directly exported from Monaco; use `monaco.IRange` or `import type { IRange } from "monaco-editor"` (top-level re-export)
  - [ ] 1-8. Fix useMonacoLsp — `CompletionList` return type mismatch between LSP and Monaco provider
  - [ ] 1-9. Fix ExtensionsPanel, GitPanel, GitHubPanel — `Expected 1 arguments, but got 0` call-site mismatches
  - [ ] 1-10. Verify `npm run build` passes with zero errors; add build check to CI if not already present

- [ ] 2. **Single-launch experience**
  - [ ] 2-1. Electron main process spawns `dan-serve` (or embedded uvicorn) as a child process on app start, with health-check polling before rendering
  - [ ] 2-2. Backend process stdout/stderr piped to an "DAN Server" output channel
  - [ ] 2-3. Graceful shutdown: `before-quit` kills the backend child process
  - [ ] 2-4. Settings toggle: "Auto-start backend" (default on), with manual "Start/Stop Server" in command palette
  - [ ] 2-5. Status indicator in the status bar (green dot = connected, yellow = starting, red = disconnected) with click-to-restart
  - [ ] 2-6. Fallback: if `dan-serve` is already running externally, detect via `/api/health` and skip spawn

- [ ] 3. **First-run onboarding**
  - [ ] 3-1. Welcome screen on first launch (no workspaces exist): app name/logo, "Open Folder" button, "New Workspace" button, recent workspaces list (empty on first run)
  - [ ] 3-2. Workspace setup: after opening a folder, auto-detect project type (reuse `workspaceIntelligence.ts`), show detected stack with "Looks good" / "Change" option
  - [ ] 3-3. Quick feature tour: 4-step tooltip walkthrough — (1) mode bar, (2) chat bar, (3) file explorer, (4) terminal. Dismissible, "Don't show again" persisted to settings
  - [ ] 3-4. Recommended settings: suggest enabling Quick Profiles (`DAN_LEARNING_MODE`, `DAN_FULL_TOOLS`) if not already set; offer one-click `.env` patch

### Slice B — LSP Quality Upgrade

- [ ] 4. **Wire LSP to OutlineView**
  - [ ] 4-1. Replace regex-based symbol extraction in `OutlineView.tsx` with `nativeLsp.documentSymbol(filePath)` call
  - [ ] 4-2. Fall back to regex extraction when no LSP server is running for the language
  - [ ] 4-3. Hierarchical display from LSP `DocumentSymbol[]` (nested children) vs flat `SymbolInformation[]`
  - [ ] 4-4. Auto-refresh on `textDocument/didChange` debounced (500ms)

- [ ] 5. **Wire LSP to SymbolSearch**
  - [ ] 5-1. Replace regex-based workspace symbol search with `workspace/symbol` LSP request in `SymbolSearch.tsx`
  - [ ] 5-2. Expand search scope beyond open files — currently only searches `openFiles` array; with LSP, search the full workspace
  - [ ] 5-3. Fall back to regex extraction over open files (current behavior) when no LSP server available for the language
  - [ ] 5-4. Show symbol kind icons from LSP `SymbolKind` enum

- [ ] 6. **Wire LSP to PeekDefinition**
  - [ ] 6-1. Replace open-file-only search in `PeekDefinition.tsx` with `textDocument/definition` LSP request
  - [ ] 6-2. Multi-location support: when definition returns multiple locations, show tabs in peek window
  - [ ] 6-3. Load file content from disk for definitions not currently open
  - [ ] 6-4. Fall back to open-file regex search when LSP unavailable

- [ ] 7. **Additional language server configs**
  - [ ] 7-1. Add Go (`gopls`) to `SERVER_CONFIGS` in `lspManager.ts` — detect via `go.mod`/`go.sum`; runtime detection with helpful "install gopls: `go install golang.org/x/tools/gopls@latest`" message if binary not found
  - [ ] 7-2. Add Rust (`rust-analyzer`) — detect via `Cargo.toml`; unlike TS/Python servers, this is a system binary (not npm-installable), so detect with `which rust-analyzer` and show install instructions if missing
  - [ ] 7-3. Add C/C++ (`clangd`) — detect via `CMakeLists.txt`, `Makefile` with `.c`/`.cpp` files, `compile_commands.json`; same system-binary pattern as Go/Rust
  - [ ] 7-4. Document how to add custom language servers in settings or workspace config

### Slice C — AI Coding Upgrade

- [ ] 8. **@ references in chat sidebar**
  - [ ] 8-1. `@` trigger in chat input: typing `@` opens a dropdown with file/symbol/folder completions
  - [ ] 8-2. File completions: search pinned roots + recent files, show file icons, insert as structured mention
  - [ ] 8-3. Symbol completions: search workspace symbols (via LSP or regex), show kind icon
  - [ ] 8-4. Folder completions: directory tree navigation
  - [ ] 8-5. Render mentions as styled chips in the input and include full file/symbol content in the message context sent to the backend
  - [ ] 8-6. Backend: `chat_manager` recognizes `@file:path` and `@symbol:name` annotations, expands them into the system prompt context window

- [ ] 9. **Multi-file agent edit flow** — `MultiFileEdit.tsx` already exists with per-file DiffEditor, accept/reject, and Accept All. The gap is wiring it into the chat flow.
  - [ ] 9-1. When chat produces edits to multiple files (via `file_write` tool calls), collect all changes into a single `MultiFileEdit` review panel instead of opening individual diffs — currently `ChatSidebar` renders a separate "View Diff" button per file via regex detection
  - [x] 9-2. Per-file diff editor with accept/reject per file *(exists in `MultiFileEdit.tsx`)*
  - [x] 9-3. Accept All / Reject All across all files *(exists in `MultiFileEdit.tsx`)*
  - [ ] 9-4. "Apply and Run Tests" action: accept all changes then run the detected test suite, showing pass/fail inline
  - [ ] 9-5. Wire ChatSidebar in Code mode to detect multi-file write sequences and auto-open the review panel — `extractFilePaths()` already detects multiple paths but treats them individually

- [ ] 10. **Project-aware AI context**
  - [ ] 10-1. Send open file paths + active file content + selection to `/api/chat/editor/message` (extend existing `activeFileContext`)
  - [ ] 10-2. Include import graph neighbors: for the active file, resolve imported modules (1 hop) and include their signatures in context
  - [ ] 10-3. Include workspace-level context: project type, framework, key config files (package.json scripts, tsconfig paths) as structured preamble
  - [ ] 10-4. Context budget: fit within model context window, prioritize active file > selection > imports > project info

### Slice D — Bug Fixes & Polish

- [ ] 11. **Implement or remove `/cost` and `/retry` commands** — these are registered as bypass prefixes in `dispatcher.py` (line 21) but have no handler implementations anywhere in the codebase; `bugs.md` documents the intent but the handlers were never written
  - [ ] 11-1. Decision: implement handlers or remove the bypass prefixes. If implementing: `/cost` should show session token/cost totals from `audit_metadata`; `/retry` should resend the last user message
  - [ ] 11-2. If implementing, add `_handle_cost_command` and `_handle_retry_command` to `runtime.py` with correct model references (`session.usage` / `chat_store` thread lookup — `Project` has no `thread_id` field)
  - [ ] 11-3. Add test coverage for both commands

- [ ] 12. **Debug quality-of-life**
  - [ ] 12-1. Auto-generate launch config from workspace detection: Node.js (`node --inspect-brk`), Python (`debugpy`), detected main entry point
  - [ ] 12-2. Debug hover evaluation: when paused, hovering a variable in the editor evaluates it via DAP `evaluate` request and shows the result in a tooltip
  - [ ] 12-3. Conditional breakpoint UI: right-click breakpoint gutter → "Edit Condition" input, sends condition to DAP `setBreakpoints`

- [ ] 13. **Editor reliability**
  - [ ] 13-1. Suppress stale-revision warning when no graph is loaded — the server-authored `graph_revision` fix landed 2026-03-12 (`getClientGraphRevision` prefers server revision); remaining issue is the banner still renders whenever `staleRevision` is true regardless of whether `graphId` is null
  - [x] 13-2. Fix `persistent-chat:send` event loss — Research mode's QuickStartPanel, PdfReader, and ReferencePanel still dispatch `persistent-chat:send`, but the shared `ModeChatSidebar` now installs a bridge that routes those events into the active mode sidebar, auto-opens the sidebar when needed, and queues/replays messages if the sender is not mounted yet

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

- (filled in during execution)

## Notes

- The TypeScript build fix (Task 1) is the single highest-leverage item — it unblocks Electron packaging, CI, and the single-launch experience.
- LSP wiring (Tasks 4-6) is low-hanging fruit: the servers are already running and the IPC bridge already exists. The three UI components just need to call through instead of doing their own regex parsing. `OutlineView.tsx` even has the comment `// Regex-based symbol extraction (until LSP is wired)`.
- The @ references (Task 8) and multi-file edits (Task 9) are what make the chat sidebar feel like a real development tool rather than a ChatGPT window next to an editor.
- Task 7 (new language servers) differs from existing servers: Go/Rust/C++ language servers are system binaries, not npm packages. The existing TS/Python/JSON servers use `npx --no-install` from local devDeps — that pattern won't work for `gopls` or `rust-analyzer`. Need a separate detection + user-friendly install-hint pattern.
- `MultiFileEdit.tsx` already covers the component UI (DiffEditor, per-file accept/reject, Accept All). The work is pure wiring: detecting multi-file write batches in ChatSidebar and routing them to the existing component.
- The `persistent-chat:send` race is particularly insidious because it silently drops messages — no error, no feedback. Research mode quick-starts rely on this event to kick off workflows via chat.
- 2026-03-16: Added shared `editorChat.ts` so editor/research AI callers that still pointed at the dead `/api/chat/editor/message` route now go through the live `/api/chat/message` pipeline instead. This rewired `ModeChatSidebar`, `InlineEdit`, `aiCodeActions`, `WritingPane`, `CodeCells`, and `PageSummary`.
- 2026-03-16: Restored a compatibility backend route for `/api/chat/editor/complete` so `InlineCompletion.tsx` keeps working while the rest of the editor AI surface migrates to the shared helper.
- 2026-03-16: Per-mode chat sidebars and full Chat now turn appended file chips into explicit prompt context, and Research figure cards gained an `Ask AI` action that appends figure metadata/source context to Research chat.
- 2026-03-16: Follow-up hardening for attachments: frontend now passes the first attached path via `attachment_path`, uploaded research figures retain their original file path, and the backend promotes attached file/image metadata into explicit system instructions with tool hints so the assistant acknowledges visible attachments and can inspect attached image paths instead of denying they exist.
