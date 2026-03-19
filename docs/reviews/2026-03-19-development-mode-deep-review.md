# Development Mode (CodeMode) Deep Review

**Reviewer:** Claude Opus 4.6
**Date:** 2026-03-19
**Scope:** Development Mode features, VS Code/Cursor parity analysis

---

## 1. Core IDE Capabilities

### Feature Inventory

The IDE layout follows a VS Code-faithful pattern: `ActivityBar | Sidebar | (EditorTabs / BottomPanel) | StatusBar`. The activity bar has 13 sidebar panels organized in 4 groups. 42 `.tsx` components in `editor/src/components/code/`.

| Feature | Component | Status |
|---------|-----------|--------|
| File explorer with tree | `FileExplorer.tsx` | Implemented -- filter, gitignore, context menu, new/rename/delete |
| Monaco editor with tabs | `MonacoTabs.tsx` | Implemented -- tab context menu (close others/all/right), drag n/a |
| Terminal (xterm.js) | `TerminalPanel.tsx` | Implemented -- multi-tab, split, profiles, copy/paste, resize |
| Quick Open (Cmd+P) | `QuickOpen.tsx` | Implemented |
| Command Palette (Cmd+Shift+P) | `CommandPalette.tsx` | Implemented -- themes, toggles, file ops, navigation |
| Symbol search (Cmd+T) | `SymbolSearch.tsx` | Implemented |
| Search & Replace (ripgrep) | `SearchPanel.tsx` | Implemented -- regex, case sensitivity, whole word, globs |
| Breadcrumbs | `Breadcrumbs.tsx` | Implemented |
| Diff view | `DiffView.tsx` | Implemented |
| Split editor (Cmd+\\) | `SplitEditor.tsx` | Implemented |
| Zen mode (Cmd+K Z) | `ZenMode.tsx` | Implemented |
| Settings panel (Cmd+,) | `SettingsPanel.tsx` | Implemented |
| Keybindings panel | `KeybindingsPanel.tsx` | Implemented |
| Problems panel | `ProblemsPanel.tsx` | Implemented |
| Output panel | `OutputPanel.tsx` | Implemented |
| Outline view | `OutlineView.tsx` | Implemented |
| Call hierarchy | `CallHierarchy.tsx` | Implemented |
| Local history / timeline | `LocalHistoryPanel.tsx` | Implemented |
| Crash recovery | `CrashRecoveryBanner.tsx`, `useSessionRestore.ts` | Implemented |
| Write confirmation dialog | `WriteConfirmDialog.tsx` | Implemented -- safety for external files |
| Codebase Q&A | `CodebaseQA.tsx` | Implemented |
| Task runner | `TaskRunner.tsx` | Implemented -- build (Cmd+Shift+B), test (Cmd+Shift+T) |
| Test explorer | `TestExplorer.tsx` | Implemented -- auto-detect framework, run, coverage |
| Coverage overlay | `CoverageOverlay.tsx` | Implemented |
| Merge editor | `MergeEditor.tsx` | Implemented -- 3-way merge with conflict regions |
| Interactive rebase | `InteractiveRebase.tsx` | Implemented |
| Multi-file edit review | `MultiFileEdit.tsx` | Implemented -- accept/reject per file |
| Peek definition | `PeekDefinition.tsx` | Implemented |
| Inline edit (Cmd+K) | `InlineEdit.tsx` | Implemented -- AI-powered in-place code edits |
| Feature tour | `FeatureTour.tsx` | Implemented -- onboarding |
| Project detection | `ProjectDetectionToast.tsx` | Implemented -- Node, Python, Rust, Go, Docker |
| Workspace intelligence | `workspaceIntelligence.ts` | Implemented -- auto-detect project type/frameworks |
| File watcher | `useFileWatcher.ts` | Implemented |
| Cursor history | `useCursorHistory.ts` | Implemented -- Alt+Left/Right |
| Workspace memory | `useWorkspaceMemory.ts` | Implemented |

### Strengths

- **S1:** Remarkably complete VS Code-like layout with all major panels (Explorer, Search, Git, Debug, Extensions, Terminal, Problems, Output).
- **S2:** Keyboard shortcuts closely mirror VS Code conventions (Cmd+P, Cmd+Shift+P, Cmd+\`, F5, F10, F11, etc.).
- **S3:** StatusBar faithfully reproduces VS Code's status bar: branch, error/warning counts, cursor position, tab size, encoding, language.
- **S4:** Crash recovery with autosave and 24-hour snapshot window is a strong resilience feature.
- **S5:** Write confirmation dialog prevents accidental writes to files outside pinned workspace roots -- better security than VS Code default.
- **S6:** Multi-file edit review panel with per-file accept/reject and auto-test-after-apply is a Cursor-like AI workflow.

### P1 Issues

- **P1-1: No tab drag-and-drop reordering.** `MonacoTabs.tsx` implements tabs with close/context-menu but no drag reorder. VS Code users heavily rely on this.
- **P1-2: Terminal defaults to `/bin/zsh` everywhere with no platform detection.** `TerminalPanel.tsx` line 147: `const shell = resolvedProfile?.shell || "/bin/zsh"`. Also `ChatSidebar.tsx` line 453 and `ModeChatSidebar.tsx` line 776 hardcode `/bin/zsh`. This breaks on Linux and Windows.

### P2 Issues

- **P2-1: No workspace folder management UI.** While `pinnedRoots` exist internally, there's no "Add Folder to Workspace" dialog equivalent.
- **P2-2: No breadcrumb navigation for nested symbols.** `Breadcrumbs.tsx` exists but the outline-level breadcrumb clicking is basic compared to VS Code's breadcrumb dropdown.
- **P2-3: Workflow and Furnace sidebar panels are stubs.** `WorkflowSidebarPanel` (line 85-101) and `FurnaceSidebarPanel` (line 104-121) are placeholder UI only.
- **P2-4: Bottom panel tabs are hardcoded to 4.** No extensibility for custom output channels from extensions.

### P3 Issues

- **P3-1: No editor minimap toggle from context menu** -- only available via command palette.
- **P3-2: No file icon theme switching** -- `FileIcon.tsx` exists but no theme selector.
- **P3-3: Command palette has ~25 commands.** VS Code has hundreds. Missing: format document, sort lines, transform case, etc.

---

## 2. LSP Integration

### Languages Supported

| Language | Server | Detection | Install Method |
|----------|--------|-----------|----------------|
| TypeScript/JavaScript | typescript-language-server | package.json/tsconfig.json | npx (auto) |
| Python | pyright-langserver | pyproject.toml/requirements.txt | npx (auto) |
| Go | gopls | go.mod/go.sum | System binary (manual) |
| Rust | rust-analyzer | Cargo.toml | System binary (manual) |
| C/C++ | clangd | CMakeLists.txt | System binary (manual) |
| JSON | vscode-json-language-server | Always started | npx (auto) |
| CSS/SCSS/Less | vscode-css-language-server | Always started | npx (auto) |
| HTML | vscode-html-language-server | Always started | npx (auto) |

### LSP Features Registered in Monaco

All registered at `useMonacoLsp.ts` via `monaco.languages.register*Provider("*")`:
- Completion with trigger characters `. / < " ' @ :`
- Hover, Go to Definition, Find References
- Document Symbols, Signature Help, Code Actions
- Rename, Document Formatting, Diagnostics

### Strengths

- **S7:** Comprehensive LSP-to-Monaco bridge covering 10 provider types with correct CompletionItemKind/SymbolKind mapping.
- **S8:** Proper LSP lifecycle: JSON-RPC framing, 10s request timeouts, pending request tracking, graceful shutdown.
- **S9:** Automatic project detection triggers the right servers with install hints for missing system binaries.
- **S10:** Call hierarchy support (prepareCallHierarchy, incoming/outgoing calls).

### P1 Issues

- **P1-3: `commandExists()` at `lspManager.ts` line 25-31 uses `which` which is macOS/Linux only.** On Windows, `where` is needed. Go, Rust, and C++ LSP detection will fail on Windows.

### P2 Issues

- **P2-5: No multi-root workspace support in LSP.** `useMonacoLsp.ts` line 174-177 only starts LSP for `pinnedRoots[0]`.
- **P2-6: Missing LSP features:** No document highlights, code lens, semantic tokens, folding range, or selection range provider.
- **P2-7: No LSP restart/reconnect mechanism.** If a language server crashes, it's just deleted, no automatic restart.
- **P2-8: No YAML, Dockerfile, or Markdown language server support.**

### P3 Issues

- **P3-4:** Completion provider registered for `"*"` (all languages) even when no LSP server supports the file type.
- **P3-5:** `npx --no-install` pattern requires packages in the project. No global fallback.

---

## 3. AI Code Features

### Feature Set

| Feature | Implementation | Trigger |
|---------|---------------|---------|
| Ghost text (inline completion) | `InlineCompletion.tsx` -- FIM-style | Automatic (500ms debounce) |
| Explain code | `aiCodeActions.ts` | Right-click context menu |
| Generate tests | `aiCodeActions.ts` | Right-click context menu |
| Generate docs | `aiCodeActions.ts` | Right-click context menu |
| Suggest refactoring | `aiCodeActions.ts` | Right-click context menu |
| Fix error | `aiCodeActions.ts` | Right-click context menu |
| Generate commit message | `aiCodeActions.ts` | Git panel button |
| Codebase Q&A | `aiCodeActions.ts` | Cmd+Shift+I |
| Inline edit | `InlineEdit.tsx` -- Cmd+K | Selection + Cmd+K |
| Chat sidebar | `ChatSidebar.tsx` | Cmd+J toggle |
| Multi-file edit review | `MultiFileEdit.tsx` | AI writes trigger review |

### Strengths

- **S11:** Proper Copilot-style FIM inline completion with prefix/suffix context (30/10 lines), debounce, cancellation, and settings toggle.
- **S12:** Inline edit uses function-level context extraction, prompt history (localStorage, max 5), and strips code fences.
- **S13:** Chat sidebar code blocks have copy/insert/apply buttons, and shell commands get a "Run" button for terminal execution.
- **S14:** File path linkification in chat responses and "View Diff" buttons for file-write mentions.
- **S15:** AI-generated commit messages integrated directly into the Git panel.

### P2 Issues

- **P2-9: No multi-turn context for inline completion.** Each request is independent. No awareness of recent edits or cursor patterns.
- **P2-10: `askCodebase()` only sends 5 open files, truncated to 2000 chars each.** No codebase indexing, no embedding-based retrieval.
- **P2-11: No diff preview before applying inline edit.** `InlineEdit.tsx` replaces selected code directly. Cursor shows a diff overlay before accepting.

### P3 Issues

- **P3-6:** Requires at least 3 non-whitespace characters before triggering completions. Misses common cases like completing after braces.
- **P3-7:** No "Accept Word" or "Accept Line" for inline completions -- only full accept via Tab.

---

## 4. Debug Support

### Debug Adapters

| Language | Adapter | Notes |
|----------|---------|-------|
| Node.js | `@vscode/js-debug` | Via `node --inspect-brk` |
| Python | `debugpy.adapter` | Via `python3 -m debugpy.adapter` |

### UI Features

Launch config management, attach-to-process modal, variables tree, call stack, watch expressions, debug console, breakpoint gutter decorations, keyboard shortcuts (F5/F10/F11), paused location highlighting.

### Strengths

- **S16:** Full DAP client with JSON-RPC framing, 15s timeouts, proper cleanup.
- **S17:** Breakpoints persisted across sessions via localStorage.
- **S18:** Watch expressions auto-evaluated on each stop event.
- **S19:** Attach-to-Process modal with auto-scanning for debuggable Node/Python processes.

### P2 Issues

- **P2-12: Only Node.js and Python debug adapters.** No Go, Rust, C++, or Java.
- **P2-13: No conditional breakpoint UI in the gutter.** The type definition supports it but no UI.
- **P2-14: No launch.json support.** Configs stored in localStorage, not version-controlled.

### P3 Issues

- **P3-8:** No data, exception, or function breakpoints.
- **P3-9:** Inline values type exists but not rendered.
- **P3-10:** `resolveJsDebugAdapter()` uses hardcoded path that may not resolve in production builds.

---

## 5. Extension System

### Architecture

Extensions run in a **forked Node.js process** (`extensionHostWorker.ts`) with a 512MB memory limit. Communication uses Node IPC. The host provides a **partial `vscode` API shim** injected via `require("module")` monkey-patching.

### vscode API Surface Coverage

| API | Coverage |
|-----|----------|
| `vscode.commands` | registerCommand, executeCommand, getCommands |
| `vscode.window` | showInfo/Warning/ErrorMessage, createStatusBarItem, createOutputChannel |
| `vscode.workspace` | getConfiguration (stub), onDidChange/Save/Open/CloseTextDocument |
| `vscode.languages` | registerCompletion/Hover/DefinitionProvider, createDiagnosticCollection |
| `vscode.extensions` | getExtension (always null), all (empty) |
| Core classes | Position, Range, Location, Diagnostic, CompletionItem, TreeItem, EventEmitter |

### Strengths

- **S20:** Isolated process model with 512MB memory cap prevents extension crashes from taking down the editor.
- **S21:** Module interception pattern overrides `Module._resolveFilename` so `require("vscode")` returns the shim without filesystem tricks.
- **S22:** Activation event model follows VS Code's lazy activation pattern.
- **S23:** Full marketplace abstraction with search, install, update, and version checking.

### P1 Issues

- **P1-4: Language provider registrations from extensions are event-only, not functional.** `extensionHostWorker.ts` lines 174-199: `registerCompletionItemProvider`, `registerHoverProvider`, `registerDefinitionProvider` all just `sendEvent()` without routing provider calls. **Extension-contributed language features do not work.**

### P2 Issues

- **P2-15: `workspace.getConfiguration` is a stub** that always returns default values.
- **P2-16: Missing major vscode API surfaces:** `vscode.debug`, `vscode.tasks`, `vscode.scm`, `vscode.tests`, `vscode.authentication`, `vscode.env`, `vscode.workspace.fs`, `TextEditor`, `TextDocument`, `webview`, `TreeView`, `CustomEditor`.
- **P2-17: Grammar/theme contributions typed but no loading code.** TextMate grammars and color themes from extensions can't actually be applied.

---

## 6. Git Integration

### Feature Set

Full git feature set with 30 operations: status, diff, log, stage/unstage, commit, branch, push/pull, blame, stash (list/pop/apply/drop/show), cherry-pick, interactive rebase, 3-way merge, GitHub PR/issue integration via `gh` CLI.

### Strengths

- **S24:** Extremely comprehensive. Interactive rebase and 3-way merge editor are rare outside VS Code.
- **S25:** Git graph visualization with lane assignment algorithm, colored commit dots, and ref labels.
- **S26:** GitHub integration via `gh` CLI with PR listing, details, and issue tracking.
- **S27:** AI-generated commit messages integrated directly in the commit flow.

### P2 Issues

- **P2-18: No diff gutter indicators in the editor.** VS Code shows green/red bars for added/modified/deleted lines.
- **P2-19: Git operations only work for `pinnedRoots[0]`.** Multi-root git is unsupported.

---

## 7. Cross-Platform Readiness

The application is **macOS-only in practice**. `main.ts:507` has the only platform-aware shell selection, but the renderer process hardcodes `/bin/zsh` in 3 locations.

### P1 Issues

- **P1-5: Three renderer-side locations hardcode `/bin/zsh`** without platform detection. Windows/Linux terminal creation will fail.
- **P1-6: Default terminal profiles only define Unix shells** (`zsh`, `bash`, `sh`). No PowerShell or CMD.

### P2 Issues

- **P2-20: `commandExists()` uses `which` (macOS/Linux only).** Needs `where` on Windows.
- **P2-21: `#!/bin/sh` shebang in generated editor scripts.** Won't work on Windows.

---

## Consolidated Priority Summary

### P1 -- Must Fix

| ID | Issue | File(s) |
|----|-------|---------|
| P1-1 | No tab drag-and-drop reordering | `MonacoTabs.tsx` |
| P1-2 | Terminal hardcodes `/bin/zsh` (breaks Linux/Windows) | `TerminalPanel.tsx:147` |
| P1-3 | `commandExists()` uses `which` (macOS/Linux only) | `lspManager.ts:27` |
| P1-4 | Extension language providers are no-ops (sendEvent only) | `extensionHostWorker.ts:174-199` |
| P1-5 | Three renderer locations hardcode `/bin/zsh` | `ChatSidebar.tsx:453`, `ModeChatSidebar.tsx:776`, `TerminalPanel.tsx:412` |
| P1-6 | Default terminal profiles are Unix-only | `useSettingsStore.ts:15-17` |

### P2 -- Important

| ID | Issue | Area |
|----|-------|------|
| P2-5 | LSP only for first pinned root | LSP |
| P2-6 | Missing LSP features (highlights, code lens, semantic tokens) | LSP |
| P2-7 | No LSP crash auto-restart | LSP |
| P2-9 | No multi-turn context for inline completion | AI |
| P2-10 | Codebase Q&A limited to 5 open files | AI |
| P2-12 | Only Node+Python debug adapters | Debug |
| P2-14 | No launch.json support | Debug |
| P2-16 | Missing major vscode API surfaces | Extensions |
| P2-18 | No diff gutter indicators in editor | Git |

---

## Overall Assessment

Development Mode is an **impressively ambitious and largely complete** VS Code-like IDE. The feature breadth is remarkable: 42 code components, 8 language servers, AI code assistance, full DAP debugging, and comprehensive git (including 3-way merge and interactive rebase).

**Key differentiators vs VS Code/Cursor:**
- Tighter AI integration (inline edit, chat sidebar, AI commit messages, codebase Q&A, multi-file review)
- DAN-specific panels for orchestration integration
- Write confirmation safety for files outside workspace roots

**Primary gaps vs VS Code/Cursor:**
1. Cross-platform support is macOS-only in practice (P1-2, P1-5, P1-6)
2. Extension system is structurally present but functionally incomplete (P1-4, P2-16)
3. Debug adapter coverage limited to Node+Python (P2-12)
4. No semantic tokens, code lens, or document highlights from LSP (P2-6)
5. No launch.json or workspace-level configuration files (P2-14)

The most impactful improvements would be: (1) fixing cross-platform shell handling, (2) making extension language providers functional, and (3) adding LSP auto-restart.
