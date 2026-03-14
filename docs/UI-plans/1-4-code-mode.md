# 1-4: Code Mode (Full IDE Workspace)

**Parent:** [1-ui-spec](1-ui-spec.md)
**Status:** completed
**Goal:** Build a full IDE workspace inside DAN that can replace Cursor/VS Code for daily development. Phase 1 delivers a self-hostable coding environment. Phase 2 reaches VS Code parity with extensions, LSP, and debugger.

## Context

DAN's chat already handles the AI layer — tool calls, file operations, code generation, debugging. What's missing is the visual workspace: a code editor, file tree, terminal, diff viewer, and search. The key insight: **Cursor's value is "AI in the editor." DAN's value is "editor in the AI."** The chat brain is already more powerful (multi-agent, memory, self-evolution). The code panels are its hands — displaying what the AI does and letting the user intervene directly.

Code mode is the critical path to self-hosting: once it works, DAN development happens inside DAN.

## Layout

```
┌─ Workspace Tabs ──────────────────────────────────────────────────────┐
│ [Supply Chain Paper] [DAN Dev] [Kaggle Comp] [+]                      │
├───────────────────────────────────────────────────────────────────────┤
│ Mode: Chat │ ●Code │ Research │ Analytics │ Operations                │
├──────────────┬──────────────────────────────────┬─────────────────────┤
│ File Explorer│ Editor Tabs                      │ Chat Panel          │
│              │ [prompts.py] [ChatPanel.tsx] [+]  │ (toggleable Cmd+J) │
│ 📂 Pinned   │                                  │                     │
│  ~/deep-a…/ │  ┌──────────────────────────────┐ │ Thread: Debug auth  │
│  ~/science…/ │  │ (Monaco Editor)              │ │                     │
│              │  │                              │ │ User: The login     │
│ 🕐 Recent   │  │                              │ │ endpoint returns    │
│  prompts.py │  │                              │ │ 401 after...        │
│  helpers.py │  │                              │ │                     │
│  ChatPanel… │  │                              │ │ DAN: Looking at     │
│              │  └──────────────────────────────┘ │ the auth middleware │
│ 💡 DAN      │ ┌─ Bottom Panel ────────────────┐ │ [file_read]...      │
│  utils.ts   │ │ Terminal │ Output │ Problems   │ │                     │
│  (new)      │ │ $ npm run dev                 │ │ [Apply Fix]         │
│              │ │ > Server on :3000             │ │                     │
├──────────────┴─┴───────────────────────────────┴┴─────────────────────┤
│ Chat Bar: > "fix the auth middleware bug in routes/login.ts"          │
└───────────────────────────────────────────────────────────────────────┘
```

## Delivery Slices

To keep this buildable, Code mode should ship in narrow slices rather than as one giant IDE rewrite:

### Slice A — Daily Edit Loop
- Open a repo
- Browse files
- Open multiple tabs
- Edit and save
- Run commands in a terminal

### Slice B — Inspect and Repair
- Cross-file search
- Diff view
- Problems panel
- Chat opens files, shows diffs, and streams shell output into terminal tabs

### Slice C — Replace Cursor for DAN Development
- Git status + commit flow
- Active file/selection sent to chat
- Inline "Ask DAN / Fix this" actions
- Stable workspace persistence

### Slice D — Production Daily Driver
- Autosave + hot exit + crash recovery
- Back/forward navigation + symbol search + reopen closed editor
- Output/log channels for tasks, git, LSP, extensions, debugger
- Test explorer + rerun failed + coverage view
- Format on save + code actions on save

**Phase 1 exit criteria:** you can open the DAN repo, edit multiple files, run tests, inspect diffs, apply an AI-generated patch, and commit the result without leaving DAN.

**Production daily-driver baseline:** Phase 1 exit criteria plus recovery/navigation features, Phase 2A language intelligence/debug basics, and a first usable testing surface. Phase 1 alone is self-hostable; the production-ready bar is higher.

## Phase 1: Core IDE (Self-Hostable MVP)

Goal: Replace Cursor for daily DAN development within 3-4 weeks.

### 1. Monaco Editor (Primary Panel)
- [x] 1-1. **Multi-tab editing**: open multiple files as tabs, close/reorder tabs, tab overflow scroll
- [x] 1-2. **Syntax highlighting**: auto-detect language from extension, all major languages (TS/JS/Python/Rust/Go/Java/C/CSS/HTML/JSON/YAML/Markdown/SQL/Shell/LaTeX)
- [x] 1-3. **File open/save**: open via file explorer click, Cmd+P, or Electron native dialog; save via Cmd+S (write through Electron IPC to filesystem)
- [x] 1-4. **Find/replace**: Cmd+F (in-file), Cmd+H (replace), regex support, match case, whole word *(Monaco built-in)*
- [x] 1-5. **Go to line**: Cmd+G *(Monaco built-in)*
- [x] 1-6. **Multiple cursors**: Cmd+D (add selection to next match), Option+Click (add cursor), Cmd+Shift+L (select all occurrences) *(Monaco built-in)*
- [x] 1-7. **Minimap**: toggleable code overview on right edge *(via Settings panel + Command Palette)*
- [x] 1-8. **Code folding**: collapse/expand functions, classes, blocks, imports *(Monaco built-in)*
- [x] 1-9. **Bracket matching**: highlight matching brackets, auto-close brackets/quotes *(Monaco built-in + bracketPairColorization)*
- [x] 1-10. **Auto-indent**: smart indent on Enter, re-indent on paste *(Monaco built-in)*
- [x] 1-11. **Word wrap**: toggle soft wrap (Cmd+Option+W), configurable wrap column *(via Settings + Command Palette)*
- [x] 1-12. **Line numbers**: absolute, relative, or off (configurable) *(via Settings panel)*
- [x] 1-13. **Tab settings**: tab size (2/4/8), spaces vs tabs, per-language defaults (`.editorconfig` support) *(via Settings panel)*
- [x] 1-14. **Encoding detection**: UTF-8 default, detect and display encoding in status bar, convert on save
- [x] 1-15. **Unsaved changes indicator**: dot on tab, confirm dialog on close if unsaved
- [x] 1-16. **File watcher**: auto-reload when file changes externally (via Electron `fs.watch`), conflict resolution if also dirty locally
- [x] 1-17. **Status bar**: line:col, language, encoding, indentation, git branch, EOL style
- [x] 1-18. **Autosave modes**: off / after delay / on focus change / on window change *(debounced autosave with configurable delay in Settings)*
- [x] 1-19. **Save all / revert file**: Cmd+Option+S save all, revert-from-disk for current file with diff confirmation
- [x] 1-20. **Reopen closed editor**: reopen last closed tab/editor group with cursor position + scroll preserved *(Cmd+Shift+T, recently-closed stack of 20)*
- [x] 1-21. **Back/forward navigation**: jump through cursor history across files and symbols (like VS Code/Cursor Alt+Left/Right) *(useCursorHistory, debounced 500ms, 100-entry stack)*
- [x] 1-22. **Symbol quick open**: Cmd+T fuzzy symbol search across active workspace; separate open-editors switcher *(SymbolSearch.tsx — regex-based extraction for TS/JS/Python/Rust/Go, kind-colored badges, fuzzy filter)*
- [x] 1-23. **Hot exit / session restore**: restore open tabs, dirty buffers, split layout, and active file on restart
- [x] 1-24. **Crash recovery**: periodic snapshots of dirty buffers; recover after process crash or forced restart
- [x] 1-25. **Large-file mode**: degrade expensive features (tokenization, minimap, diff decorations) for very large files instead of freezing
- [x] 1-26. **Local history / timeline**: lightweight per-file edit history independent of git so recent local changes can be recovered *(localHistory.ts — localStorage, 50 entries/file, 60s min interval; LocalHistoryPanel.tsx — timeline sidebar, compare/restore; pushLocalHistory on save/autosave)*

### 2. File Explorer (Left Sidebar)
- [x] 2-1. **Pinned roots**: display multiple pinned directories as collapsible trees (not single root)
- [x] 2-2. **Add/remove roots**: `+ Add Folder` button, right-click → Remove
- [x] 2-3. **Tree view**: lazy-load directory contents on expand, file type icons, folder icons
- [x] 2-4. **File operations**: new file, new folder, rename (F2), delete (confirm dialog), move (drag-and-drop within tree)
- [x] 2-5. **Copy path**: right-click → Copy Path / Copy Relative Path
- [x] 2-6. **Recent files section**: workspace-scoped, last 20 files, click to open *(RecentFilesSection in FileExplorer, 30 MRU, persisted in session)*
- [x] 2-7. **Agent-suggested files section**: files DAN mentions or operates on appear here with "(new)" badge
- [x] 2-8. **Cmd+P quick open**: global filesystem fuzzy search (not just pinned roots). Recent files ranked first. Preview on arrow-key navigation.
- [x] 2-9. **File icons**: language-specific icons (via `vscode-icons` or similar icon set)
- [x] 2-10. **Filter/search within tree**: type to filter visible files in the explorer *(ExplorerFilter input, recursive matchesFilter, auto-expand matched dirs, yellow highlight on matching text)*
- [x] 2-11. **Drag-and-drop**: drag file from explorer to editor area to open; drag from Finder/Explorer into the tree to copy/move *(TreeNode draggable with text/uri-list, MonacoTabs drop zone with visual overlay, OS file drop support)*
- [x] 2-12. **Hidden files toggle**: show/hide dotfiles (`.git`, `.env`, etc.)
- [x] 2-13. **Gitignore-aware**: respect `.gitignore` by default, toggle to show ignored files *(fs:readGitignore IPC, parseGitignore matcher, Eye/EyeOff toggle in header)*
- [x] 2-14. **Trusted roots model**: pinned roots are frictionless read/write zones for the workspace
- [x] 2-15. **Out-of-root write confirmation**: opening/searching anywhere is allowed, but the first mutating action outside trusted roots asks for confirmation or offers to pin that location into the workspace

### 3. Integrated Terminal (Bottom Panel)
- [x] 3-1. **Terminal emulator**: xterm.js with proper ANSI color support, 256-color, TrueColor
- [x] 3-2. **Shell integration**: `node-pty` via Electron IPC — proper PTY with SIGWINCH resize, xterm-256color, platform-aware shell detection (zsh/bash/powershell), login shell env, `@electron/rebuild` for native module compilation
- [x] 3-3. **Multiple terminals**: tab bar for terminal instances, `+` to create new, right-click to rename/close
- [x] 3-4. **Split terminal**: horizontal split within the terminal panel (side-by-side terminals)
- [x] 3-5. **Terminal scrollback**: configurable scrollback buffer (default 10,000 lines)
- [x] 3-6. **Copy/paste**: selection → auto-copy or Cmd+C, Cmd+V to paste
- [x] 3-7. **Clickable links**: detect URLs and file paths, Cmd+click to open *(via WebLinksAddon)*
- [x] 3-8. **Toggle**: Cmd+` to show/hide terminal panel
- [x] 3-9. **Working directory**: terminal starts in workspace's primary pinned path (or last used cwd)
- [x] 3-10. **Shell command from chat**: when chat runs `shell_command`, output streams to a terminal tab labeled with the command
- [x] 3-11. **Resize**: drag handle between editor and terminal, collapse to zero height *(via Allotment)*
- [x] 3-12. **Terminal profiles**: default login shell, project shell, and custom profile presets with env/cwd overrides

### 3A. Output & Logs Panel
- [x] 3A-1. **Output channels**: separate log channels for DAN, tasks, git, LSP, extension host, debugger *(Extension Host, DAN Server, Git channels)*
- [x] 3A-2. **Search/copy/export logs**: filter channel output, copy selected range, save log to file
- [x] 3A-3. **Actionable logs**: parse file/line references in output and open the corresponding source location on click

### 4. Cross-File Search (Bottom Panel Tab)
- [x] 4-1. **Search panel**: Cmd+Shift+F opens search in bottom panel tab
- [x] 4-2. **ripgrep backend**: search across all pinned paths using `rg` (pre-installed or bundled)
- [x] 4-3. **Regex support**: toggle regex mode, case sensitivity, whole word
- [x] 4-4. **Include/exclude filters**: glob patterns for file filtering (e.g., `*.py`, `!node_modules`)
- [x] 4-5. **Results list**: file-grouped results with line numbers, click to open file at line
- [x] 4-6. **Search and replace**: multi-file replace with preview (show all changes before applying)
- [x] 4-7. **Search in selection**: when editor has selection, search only within selected range

### 5. Git Integration (Source Control)
- [x] 5-1. **Branch display**: current branch name in status bar, click to switch branch
- [x] 5-2. **Changed files list**: sidebar section or bottom panel tab showing modified/staged/untracked files
- [x] 5-3. **Diff view**: click changed file → Monaco diff editor (side-by-side or unified), syntax highlighted
- [x] 5-4. **Stage/unstage**: click `+`/`-` per file or stage all
- [x] 5-5. **Commit**: message input + commit button, Cmd+Enter to commit staged
- [x] 5-6. **Push/pull**: buttons in git panel header, branch ahead/behind indicator
- [x] 5-7. **Git blame (inline)**: toggle gutter annotations showing last commit per line
- [x] 5-8. **Git log (basic)**: scrollable commit list for current branch, click to view diff
- [x] 5-9. **Merge conflict markers**: detect `<<<<<<<` markers, highlight in editor, provide accept current/incoming/both actions
- [x] 5-10. **Branch management**: create, switch, delete branches from a dropdown or panel

### 6. Diff View (for AI-Proposed Changes)
- [x] 6-1. **Monaco diff editor**: side-by-side comparison of original vs proposed code
- [x] 6-2. **Accept/reject per change**: buttons per hunk or per line to accept or reject AI-proposed edits *(Accept All / Reject All + editable modified side)*
- [x] 6-3. **Accept all / reject all**: bulk actions for the entire diff
- [x] 6-4. **Inline diff in editor**: toggle inline decorations (green additions, red deletions) in the normal editor
- [x] 6-5. **Diff from chat**: when chat proposes code changes, show "View Diff" button that opens the diff editor *(Apply button on code blocks)*
- [x] 6-6. **Navigate changes**: next/prev change buttons, Cmd+] / Cmd+[ keyboard shortcuts *(Alt+Up/Down)*

### 7. Chat Integration (AI-Editor Bridge)
- [x] 7-1. **Chat sidebar panel**: toggleable right panel (Cmd+J), reuses existing ChatPanel with thread context from active workspace. The bottom chat bar remains the single active composer; the sidebar primarily shows transcript, progress, queue, and actions.
- [x] 7-2. **File mentions → editor**: when chat mentions a file path, click to open in Monaco editor tab
- [x] 7-3. **Chat edits → diff view**: when chat uses `file_write`, open the modified file and show diff (before/after)
- [x] 7-4. **Chat shell → terminal**: when chat runs `shell_command`, stream output to a terminal tab
- [x] 7-5. **Editor → chat context**: right-click selection in Monaco → "Ask DAN about this" / "Fix this" / "Explain this" / "Refactor this"
- [x] 7-6. **Apply button**: code blocks in chat have an "Apply to [file]" button that writes the code to the relevant file
- [x] 7-7. **Active file context**: chat bar shows which file is focused; the agent sees the active file's path and selection range
- [x] 7-8. **Error → chat**: click on a problem in the Problems panel → auto-populates chat with "Fix this error: [error text]" + file context
- [x] 7-9. **Inline code actions** (Cmd+K): select code in editor → floating input box → type instruction → AI edits inline with diff preview

### 8. Problems Panel (Bottom Panel Tab)
- [x] 8-1. **Diagnostics display**: list of errors/warnings from linters, TypeScript, ESLint, etc.
- [x] 8-2. **Click to navigate**: click error → opens file at the error line
- [x] 8-3. **Severity icons**: error (red), warning (yellow), info (blue)
- [x] 8-4. **Filter by severity**: show only errors, warnings, or all
- [x] 8-5. **Badge count**: error/warning counts in the bottom panel tab label *(shown in status bar)*
- [x] 8-6. **File-grouped view**: errors grouped by file path, expandable
- [x] 8-7. **Integration source**: initially from DAN's tool output (linter results from shell commands); Phase 2 adds LSP diagnostics

### 9. Settings & Configuration
- [x] 9-1. **Editor settings**: font family, font size, theme (dark/light), tab size, word wrap, minimap, line numbers *(SettingsPanel with full controls)*
- [x] 9-2. **Theme system**: ship dark + light themes (Monaco built-in), allow custom themes *(vs-dark, vs, hc-black via Settings + Command Palette)*
- [x] 9-3. **Keybinding configuration**: default keybindings, allow user overrides, preset profiles (VS Code, Vim, Emacs)
- [x] 9-4. **Workspace settings**: per-workspace overrides for editor settings (tab size, formatter, etc.)
- [x] 9-5. **Settings sync**: persist settings to `~/.dan/settings.json`, carry across reinstalls *(localStorage via zustand persist)*
- [x] 9-6. **Workbench behavior**: settings for autosave policy, hot exit, restore last session, terminal persistence, and AI inline-edit defaults

## Phase 2: VS Code Parity + Extensions

Goal: Full IDE feature parity with VS Code/Cursor. Extension ecosystem. Advanced AI coding features.

Sequence matters:
- **Phase 2A:** native language intelligence first (LSP, diagnostics, formatter wiring, debugger basics)
- **Phase 2B:** general VS Code extension compatibility for the curated high-value subset
- **Phase 2C:** advanced AI coding UX (inline completion, multi-file edits, codebase Q&A)

### 10. Language Server Protocol (LSP)
- [x] 10-1. **LSP client**: generic LSP client in the Electron main process, communicates with Monaco via IPC — `lspClient.ts` with JSON-RPC over stdio, Content-Length framing, 10s timeout
- [x] 10-2. **Auto-detect servers**: detect project type (package.json → TypeScript, pyproject.toml → Python, etc.) and launch appropriate language server — `lspManager.ts` with auto-detection
- [x] 10-3. **Go to definition**: Cmd+click or F12 on symbols → navigate to definition (cross-file) — Monaco DefinitionProvider registered via `useMonacoLsp`
- [x] 10-4. **Find references**: Shift+F12 → list all usages of a symbol — Monaco ReferenceProvider via `useMonacoLsp`
- [x] 10-5. **Hover information**: hover over symbol → type info, documentation, signature — Monaco HoverProvider via `useMonacoLsp`
- [x] 10-6. **Auto-complete (IntelliSense)**: context-aware completion as you type, documentation popups — Monaco CompletionItemProvider via `useMonacoLsp`
- [x] 10-7. **Diagnostics**: real-time errors/warnings from the language server → Problems panel — `publishDiagnostics` listener sets Monaco markers
- [x] 10-8. **Code actions**: quick fixes (auto-import, fix typo, extract variable), lightbulb icon — Monaco CodeActionProvider via `useMonacoLsp`
- [x] 10-9. **Rename symbol**: F2 → rename across all files — Monaco RenameProvider via `useMonacoLsp`
- [x] 10-10. **Format document**: Cmd+Shift+F → run language-specific formatter — Monaco DocumentFormattingEditProvider via `useMonacoLsp`
- [x] 10-11. **Signature help**: parameter hints in function calls — Monaco SignatureHelpProvider via `useMonacoLsp`
- [x] 10-12. **Document symbols / outline**: symbol tree in sidebar — OutlineView.tsx + DocumentSymbolProvider
- [x] 10-13. **Pre-installed language servers**: TypeScript (typescript-language-server), Python (pyright), JSON (vscode-json-languageserver) — `SERVER_CONFIGS` in lspManager.ts
- [x] 10-14. **Format on save**: configurable per language/workspace — `useLspDocSync` applies formatting edits on save
- [x] 10-15. **Code actions on save**: organize imports, fix safe lint issues, language-specific safe transforms

### 11. VS Code Extension Support
- [x] 11-1. **Extension host**: `extensionActivator.ts` — loads extension contributions (themes, grammars, snippets, language configs) on startup; `MarketplaceManager` singleton routes to registered adapters
- [x] 11-2. **Extension API compatibility**: `themeLoader.ts` (VS Code→Monaco theme conversion), `grammarLoader.ts` (TextMate→Monarch), `snippetLoader.ts`, `langConfigLoader.ts` — partial `vscode` namespace for static contributions
- [x] 11-3. **Extension discovery**: `ExtensionsPanel.tsx` — sidebar with search, tabs (All/VS Code/Installed), category filter pills, sort options; `openVsxAdapter.ts` (Open VSX API + VSIX import)
- [x] 11-4. **Install/uninstall**: Electron IPC downloads VSIX, extracts to `~/.dan/extensions/`, maintains manifest.json; enable/disable per extension
- [x] 11-5. **Theme extensions**: `themeLoader.ts` — VS Code tokenColors→Monaco rules + 10 workbench color CSS custom properties
- [x] 11-6. **Language extensions**: `grammarLoader.ts` (TextMate→Monarch), `snippetLoader.ts` (VS Code snippet JSON), `langConfigLoader.ts` (comments/brackets/folding/indent)
- [x] 11-7. **Extension settings**: per-extension config in `ExtensionsPanel.tsx` detail view, contributes.configuration rendering
- [x] 11-8. **Popular extensions out-of-box**: recommended section in `ExtensionsPanel.tsx` based on workspace detection (`detectProjectType`), popular suggestions in empty state

### 12. Debugger (DAP - Debug Adapter Protocol)
- [x] 12-1. **DAP client**: `dapClient.ts` — Content-Length framed JSON protocol, all DAP commands, 15s timeout
- [x] 12-2. **Breakpoints**: `useBreakpointDecorations.ts` — gutter click toggle, red/yellow/blue decorations, conditional + logpoint support
- [x] 12-3. **Debug controls**: toolbar in `DebugPanel.tsx` — Continue/Step Over/Step Into/Step Out/Pause/Stop/Restart, F5/F10/F11 shortcuts
- [x] 12-4. **Variables panel**: lazy-load tree in `DebugPanel.tsx` — expand objects via `nativeDebug.variables(ref)`
- [x] 12-5. **Watch expressions**: editable list in `DebugPanel.tsx` — add/evaluate/remove, evaluates on each pause
- [x] 12-6. **Call stack**: frame list in `DebugPanel.tsx` — click to navigate to source file:line, active frame highlighted
- [x] 12-7. **Debug console**: `DebugConsole` component — REPL expression evaluation when paused, scrollable output
- [x] 12-8. **Launch configurations**: persisted config selector in `DebugPanel.tsx` — name/type/request/program/args/cwd/env
- [x] 12-9. **Inline values**: after-decorations in `useBreakpointDecorations.ts` — variable = value shown on paused line
- [x] 12-10. **Pre-installed debug adapters**: `debugManager.ts` — Node.js (node --inspect-brk), Python (debugpy)
- [x] 12-11. **Attach-to-process**: `AttachToProcessModal` in DebugPanel — scans for Node.js (port 9229 via lsof) and Python (debugpy via ps), PID/name/port listing, click to attach via DAP attach request, `attachSession` in debugManager
- [x] 12-12. **Breakpoint persistence**: localStorage per workspace in `useDebugStore.ts`

### 13. Advanced Editor Features
- [x] 13-1. **Breadcrumbs**: file path + symbol path below editor tabs, click to navigate *(Breadcrumbs.tsx — regex symbol extraction for 5 languages, kind-colored active symbol, click-to-navigate)*
- [x] 13-2. **Peek definition**: Alt+F12 → inline peek window showing definition without leaving current file *(PeekDefinition.tsx — searches open files, read-only Monaco preview, multi-definition tabs)*
- [x] 13-3. **Outline view**: sidebar showing document structure (classes, functions, variables) *(OutlineView.tsx — hierarchical symbol tree, filter, sort, kind icons, click-to-navigate)*
- [x] 13-4. **Call hierarchy**: view callers/callees of a function
- [x] 13-5. **Color picker**: inline color preview and picker for CSS color values *(registerColorProvider — hex/rgb/hsl parsing across CSS/SCSS/LESS/HTML/JS/TS)*
- [x] 13-6. **Emmet**: HTML/CSS abbreviation expansion (e.g., `div.container>ul>li*3`) *(registerEmmetProvider — tag.class#id>child*N+sibling expansion)*
- [x] 13-7. **Linked editing**: rename matching HTML open/close tags simultaneously *(registerLinkedEditingProvider — tag-pair detection with depth tracking)*
- [x] 13-8. **Snippet system**: built-in snippets per language, user-defined snippets, Tab-stop fields *(snippets.ts — 60+ snippets across 7 languages, Monaco CompletionItemProvider)*
- [x] 13-9. **Zen mode**: distraction-free fullscreen editing (Cmd+K Z) *(ZenMode.tsx — centered 900px, enlarged font, all chrome hidden, Escape to exit)*
- [x] 13-10. **Split editor**: side-by-side editing of two files or two views of the same file *(SplitEditor.tsx — Allotment split, Cmd+\\, file switcher dropdown)*
- [x] 13-11. **Sticky scroll**: pin parent scope (function/class) at top while scrolling *(stickyScroll Monaco option, togglable via settings)*
- [x] 13-12. **Indent guides**: visual vertical lines for indentation levels *(guides.indentation + guides.bracketPairs, togglable via settings)*

### 14. AI Code Features (Beyond Chat)
- [x] 14-1. **Inline completion (Copilot-style)**: ghost text suggestions as you type, Tab to accept — `InlineCompletion.tsx` registers Monaco InlineCompletionsProvider with 500ms debounce, cancellation, settings toggle
- [x] 14-2. **Cmd+K inline editing**: select code → type instruction → AI edits inline with diff preview (enhanced version of Phase 1 task 7-9) — already exists as `InlineEdit.tsx`
- [x] 14-3. **Code explanation**: right-click selection → "Explain this code" → `ExplainPopup.tsx` floating popup near selection with AI explanation
- [x] 14-4. **Refactoring suggestions**: AI analyzes selected code and proposes refactoring via `suggestRefactoring` → sends structured suggestions to chat
- [x] 14-5. **Test generation**: right-click function → "Generate tests" → `generateTests` creates test file with intelligent path derivation
- [x] 14-6. **Documentation generation**: right-click function/class → "Generate docs" → `generateDocs` applies inline replacement
- [x] 14-7. **Error fix suggestions**: `suggestErrorFix` in `aiCodeActions.ts` — wired for context menu use
- [x] 14-8. **Codebase Q&A**: Cmd+Shift+I → `CodebaseQA.tsx` floating panel with open-file context
- [x] 14-9. **Multi-file edits**: `MultiFileEdit.tsx` — per-file DiffEditor review with accept/reject/accept-all
- [x] 14-10. **Commit message generation**: sparkle button in `GitPanel.tsx` calls `generateCommitMessage` with staged diff

### 15. Source Control (Full Git UI)
- [x] 15-1. **Interactive rebase**: `InteractiveRebase.tsx` — modal dialog with draggable commit list, action dropdown (pick/reword/edit/squash/fixup/drop), color-coded badges, commit count selector, programmatic GIT_SEQUENCE_EDITOR rebase, conflict/abort/continue flow
- [x] 15-2. **Stash management**: collapsible section in GitPanel — stash/apply/pop/drop/view diff, auto-refresh
- [x] 15-3. **Cherry-pick**: hover button on commits in history, success/error notifications
- [x] 15-4. **GitHub integration**: view PRs, create PRs, review PRs (diff + comments), view issues
- [x] 15-5. **Inline blame with hover**: tooltip with author/email, hash, message, dates, copy hash action
- [x] 15-6. **Git graph**: `GitGraph.tsx` — SVG branch visualization, lane assignment, 8-color palette, ref badges, lazy scroll loading
- [x] 15-7. **Conflict resolution UI**: `MergeEditor.tsx` — three-way merge editor (ours/theirs/base/result) with Allotment panes, conflict marker parsing, Accept Current/Incoming/Both/Base buttons, conflict navigation (Alt+Up/Down), Mark Resolved stages file

### 16. Task Runner
- [x] 16-1. **Auto-detect tasks**: parse `package.json` scripts, `Makefile` targets, `pyproject.toml` scripts, `Cargo.toml`
- [x] 16-2. **Run tasks**: click to run, output in terminal, status indicator
- [x] 16-3. **Problem matchers**: parse task output for errors/warnings, link to source files
- [x] 16-4. **Custom tasks**: user-defined tasks (run command in directory)
- [x] 16-5. **Build/test shortcuts**: Cmd+Shift+B (build), Cmd+Shift+T (test)

### 16A. Test Explorer & Coverage
- [x] 16A-1. **Framework detection**: pytest, unittest, jest, vitest, mocha, go test, cargo test, and generic command fallback
- [x] 16A-2. **Test explorer tree**: discover suites/files/tests and render pass/fail/skipped state in a sidebar or bottom panel
- [x] 16A-3. **Run current test / file / suite / last failed**: one-click and keyboard-driven
- [x] 16A-4. **Test results panel**: duration, stdout/stderr, failure diff, rerun button
- [x] 16A-5. **Coverage support**: load coverage reports when available and show line/gutter overlays plus summary panel
- [x] 16A-6. **Problems integration**: failing tests and stack traces link directly back to source locations

### 17. Workspace Intelligence
- [x] 17-1. **Auto-detect project type**: `workspaceIntelligence.ts` — parallel detection for Node/Python/Rust/Go/Docker, frameworks, package managers
- [x] 17-2. **Recommended extensions**: `WorkspaceInfo.tsx` — dismissible banner with framework tags and extension suggestions
- [x] 17-3. **Workspace-specific memory**: `useWorkspaceMemory.ts` — tracks frequent files, commands, searches per workspace (localStorage)
- [x] 17-4. **File change detection**: `useFileChangeDetection.ts` — notifications for externally modified open files with reload/ignore actions

## Decisions
- Monaco + native DAN integrations ship before any general extension host. Code mode must be useful without VS Code extension compatibility.
- The bottom chat bar is the single source of truth for composing messages in Code mode. The right chat panel is transcript/status/action UI, not a second competing composer.
- File access is desktop-wide for read/search, but pinned roots define trusted write scopes for low-friction editing.
- Git/LSP/debugger integrations should prefer standard protocols or native CLIs (`git`, LSP, DAP) over bespoke reimplementation.
- Production-ready daily use means more than "can edit files": recovery, test/debug loops, and navigation speed are non-negotiable and must be explicitly tracked in the plan.

## Notes
- Phase 1 is the MVP for self-hosting. It should be usable for real daily development within 3-4 weeks.
- Monaco editor is the backbone — well-documented, battle-tested, same engine as VS Code. Use `@monaco-editor/react` wrapper.
- xterm.js + node-pty is the standard Electron terminal solution (same as VS Code, Hyper, etc.)
- The chat integration (task 7) is where DAN becomes better than Cursor: the agent's actions are visible and interactive in real-time across all workspace panels.
- Phase 2 extension support is a large undertaking. Do not let it block Slice A/B/C of Phase 1. Consider compatibility layers like OpenSumi or Eclipse Theia rather than reimplementing the entire VS Code extension host from scratch.
- The inline completion (14-1) requires streaming token generation wired to Monaco's inline suggest API — this is technically complex but high-value.
- ripgrep for cross-file search is the standard approach (same as VS Code). Bundle it or require it as a dependency.
- File explorer should NOT be a single-root tree. The pinned-paths model is core to the workspace design.
- Git operations should shell out to `git` CLI (same approach as VS Code's built-in git), not use a JS git library.
- Consider using `simple-git` npm package as a convenience wrapper around git CLI.
- If the app cannot survive restart/crash without losing the user's editing context, it is not yet a serious daily-driver IDE. Hot exit and recovery are part of the product bar, not polish.
