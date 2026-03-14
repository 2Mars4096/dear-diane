# 1-4: Code Mode (Full IDE Workspace)

**Parent:** [1-ui-spec](1-ui-spec.md)
**Status:** not-started
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
- [ ] 1-1. **Multi-tab editing**: open multiple files as tabs, close/reorder tabs, tab overflow scroll
- [ ] 1-2. **Syntax highlighting**: auto-detect language from extension, all major languages (TS/JS/Python/Rust/Go/Java/C/CSS/HTML/JSON/YAML/Markdown/SQL/Shell/LaTeX)
- [ ] 1-3. **File open/save**: open via file explorer click, Cmd+P, or Electron native dialog; save via Cmd+S (write through Electron IPC to filesystem)
- [ ] 1-4. **Find/replace**: Cmd+F (in-file), Cmd+H (replace), regex support, match case, whole word
- [ ] 1-5. **Go to line**: Cmd+G
- [ ] 1-6. **Multiple cursors**: Cmd+D (add selection to next match), Option+Click (add cursor), Cmd+Shift+L (select all occurrences)
- [ ] 1-7. **Minimap**: toggleable code overview on right edge
- [ ] 1-8. **Code folding**: collapse/expand functions, classes, blocks, imports
- [ ] 1-9. **Bracket matching**: highlight matching brackets, auto-close brackets/quotes
- [ ] 1-10. **Auto-indent**: smart indent on Enter, re-indent on paste
- [ ] 1-11. **Word wrap**: toggle soft wrap (Cmd+Option+W), configurable wrap column
- [ ] 1-12. **Line numbers**: absolute, relative, or off (configurable)
- [ ] 1-13. **Tab settings**: tab size (2/4/8), spaces vs tabs, per-language defaults (`.editorconfig` support)
- [ ] 1-14. **Encoding detection**: UTF-8 default, detect and display encoding in status bar, convert on save
- [ ] 1-15. **Unsaved changes indicator**: dot on tab, confirm dialog on close if unsaved
- [ ] 1-16. **File watcher**: auto-reload when file changes externally (via Electron `fs.watch`), conflict resolution if also dirty locally
- [ ] 1-17. **Status bar**: line:col, language, encoding, indentation, git branch, EOL style
- [ ] 1-18. **Autosave modes**: off / after delay / on focus change / on window change
- [ ] 1-19. **Save all / revert file**: Cmd+Option+S save all, revert-from-disk for current file with diff confirmation
- [ ] 1-20. **Reopen closed editor**: reopen last closed tab/editor group with cursor position + scroll preserved
- [ ] 1-21. **Back/forward navigation**: jump through cursor history across files and symbols (like VS Code/Cursor Alt+Left/Right)
- [ ] 1-22. **Symbol quick open**: Cmd+T fuzzy symbol search across active workspace; separate open-editors switcher
- [ ] 1-23. **Hot exit / session restore**: restore open tabs, dirty buffers, split layout, and active file on restart
- [ ] 1-24. **Crash recovery**: periodic snapshots of dirty buffers; recover after process crash or forced restart
- [ ] 1-25. **Large-file mode**: degrade expensive features (tokenization, minimap, diff decorations) for very large files instead of freezing
- [ ] 1-26. **Local history / timeline**: lightweight per-file edit history independent of git so recent local changes can be recovered

### 2. File Explorer (Left Sidebar)
- [ ] 2-1. **Pinned roots**: display multiple pinned directories as collapsible trees (not single root)
- [ ] 2-2. **Add/remove roots**: `+ Add Folder` button, right-click → Remove
- [ ] 2-3. **Tree view**: lazy-load directory contents on expand, file type icons, folder icons
- [ ] 2-4. **File operations**: new file, new folder, rename (F2), delete (confirm dialog), move (drag-and-drop within tree)
- [ ] 2-5. **Copy path**: right-click → Copy Path / Copy Relative Path
- [ ] 2-6. **Recent files section**: workspace-scoped, last 20 files, click to open
- [ ] 2-7. **Agent-suggested files section**: files DAN mentions or operates on appear here with "(new)" badge
- [ ] 2-8. **Cmd+P quick open**: global filesystem fuzzy search (not just pinned roots). Recent files ranked first. Preview on arrow-key navigation.
- [ ] 2-9. **File icons**: language-specific icons (via `vscode-icons` or similar icon set)
- [ ] 2-10. **Filter/search within tree**: type to filter visible files in the explorer
- [ ] 2-11. **Drag-and-drop**: drag file from explorer to editor area to open; drag from Finder/Explorer into the tree to copy/move
- [ ] 2-12. **Hidden files toggle**: show/hide dotfiles (`.git`, `.env`, etc.)
- [ ] 2-13. **Gitignore-aware**: respect `.gitignore` by default, toggle to show ignored files
- [ ] 2-14. **Trusted roots model**: pinned roots are frictionless read/write zones for the workspace
- [ ] 2-15. **Out-of-root write confirmation**: opening/searching anywhere is allowed, but the first mutating action outside trusted roots asks for confirmation or offers to pin that location into the workspace

### 3. Integrated Terminal (Bottom Panel)
- [ ] 3-1. **Terminal emulator**: xterm.js with proper ANSI color support, 256-color, TrueColor
- [ ] 3-2. **Shell integration**: node-pty via Electron IPC, configurable shell (zsh/bash/fish), inherit user's shell env
- [ ] 3-3. **Multiple terminals**: tab bar for terminal instances, `+` to create new, right-click to rename/close
- [ ] 3-4. **Split terminal**: horizontal split within the terminal panel (side-by-side terminals)
- [ ] 3-5. **Terminal scrollback**: configurable scrollback buffer (default 10,000 lines)
- [ ] 3-6. **Copy/paste**: selection → auto-copy or Cmd+C, Cmd+V to paste
- [ ] 3-7. **Clickable links**: detect URLs and file paths, Cmd+click to open
- [ ] 3-8. **Toggle**: Cmd+` to show/hide terminal panel
- [ ] 3-9. **Working directory**: terminal starts in workspace's primary pinned path (or last used cwd)
- [ ] 3-10. **Shell command from chat**: when chat runs `shell_command`, output streams to a terminal tab labeled with the command
- [ ] 3-11. **Resize**: drag handle between editor and terminal, collapse to zero height
- [ ] 3-12. **Terminal profiles**: default login shell, project shell, and custom profile presets with env/cwd overrides

### 3A. Output & Logs Panel
- [ ] 3A-1. **Output channels**: separate log channels for DAN, tasks, git, LSP, extension host, debugger
- [ ] 3A-2. **Search/copy/export logs**: filter channel output, copy selected range, save log to file
- [ ] 3A-3. **Actionable logs**: parse file/line references in output and open the corresponding source location on click

### 4. Cross-File Search (Bottom Panel Tab)
- [ ] 4-1. **Search panel**: Cmd+Shift+F opens search in bottom panel tab
- [ ] 4-2. **ripgrep backend**: search across all pinned paths using `rg` (pre-installed or bundled)
- [ ] 4-3. **Regex support**: toggle regex mode, case sensitivity, whole word
- [ ] 4-4. **Include/exclude filters**: glob patterns for file filtering (e.g., `*.py`, `!node_modules`)
- [ ] 4-5. **Results list**: file-grouped results with line numbers, click to open file at line
- [ ] 4-6. **Search and replace**: multi-file replace with preview (show all changes before applying)
- [ ] 4-7. **Search in selection**: when editor has selection, search only within selected range

### 5. Git Integration (Source Control)
- [ ] 5-1. **Branch display**: current branch name in status bar, click to switch branch
- [ ] 5-2. **Changed files list**: sidebar section or bottom panel tab showing modified/staged/untracked files
- [ ] 5-3. **Diff view**: click changed file → Monaco diff editor (side-by-side or unified), syntax highlighted
- [ ] 5-4. **Stage/unstage**: click `+`/`-` per file or stage all
- [ ] 5-5. **Commit**: message input + commit button, Cmd+Enter to commit staged
- [ ] 5-6. **Push/pull**: buttons in git panel header, branch ahead/behind indicator
- [ ] 5-7. **Git blame (inline)**: toggle gutter annotations showing last commit per line
- [ ] 5-8. **Git log (basic)**: scrollable commit list for current branch, click to view diff
- [ ] 5-9. **Merge conflict markers**: detect `<<<<<<<` markers, highlight in editor, provide accept current/incoming/both actions
- [ ] 5-10. **Branch management**: create, switch, delete branches from a dropdown or panel

### 6. Diff View (for AI-Proposed Changes)
- [ ] 6-1. **Monaco diff editor**: side-by-side comparison of original vs proposed code
- [ ] 6-2. **Accept/reject per change**: buttons per hunk or per line to accept or reject AI-proposed edits
- [ ] 6-3. **Accept all / reject all**: bulk actions for the entire diff
- [ ] 6-4. **Inline diff in editor**: toggle inline decorations (green additions, red deletions) in the normal editor
- [ ] 6-5. **Diff from chat**: when chat proposes code changes, show "View Diff" button that opens the diff editor
- [ ] 6-6. **Navigate changes**: next/prev change buttons, Cmd+] / Cmd+[ keyboard shortcuts

### 7. Chat Integration (AI-Editor Bridge)
- [ ] 7-1. **Chat sidebar panel**: toggleable right panel (Cmd+J), reuses existing ChatPanel with thread context from active workspace. The bottom chat bar remains the single active composer; the sidebar primarily shows transcript, progress, queue, and actions.
- [ ] 7-2. **File mentions → editor**: when chat mentions a file path, click to open in Monaco editor tab
- [ ] 7-3. **Chat edits → diff view**: when chat uses `file_write`, open the modified file and show diff (before/after)
- [ ] 7-4. **Chat shell → terminal**: when chat runs `shell_command`, stream output to a terminal tab
- [ ] 7-5. **Editor → chat context**: right-click selection in Monaco → "Ask DAN about this" / "Fix this" / "Explain this" / "Refactor this"
- [ ] 7-6. **Apply button**: code blocks in chat have an "Apply to [file]" button that writes the code to the relevant file
- [ ] 7-7. **Active file context**: chat bar shows which file is focused; the agent sees the active file's path and selection range
- [ ] 7-8. **Error → chat**: click on a problem in the Problems panel → auto-populates chat with "Fix this error: [error text]" + file context
- [ ] 7-9. **Inline code actions** (Cmd+K): select code in editor → floating input box → type instruction → AI edits inline with diff preview

### 8. Problems Panel (Bottom Panel Tab)
- [ ] 8-1. **Diagnostics display**: list of errors/warnings from linters, TypeScript, ESLint, etc.
- [ ] 8-2. **Click to navigate**: click error → opens file at the error line
- [ ] 8-3. **Severity icons**: error (red), warning (yellow), info (blue)
- [ ] 8-4. **Filter by severity**: show only errors, warnings, or all
- [ ] 8-5. **Badge count**: error/warning counts in the bottom panel tab label
- [ ] 8-6. **File-grouped view**: errors grouped by file path, expandable
- [ ] 8-7. **Integration source**: initially from DAN's tool output (linter results from shell commands); Phase 2 adds LSP diagnostics

### 9. Settings & Configuration
- [ ] 9-1. **Editor settings**: font family, font size, theme (dark/light), tab size, word wrap, minimap, line numbers
- [ ] 9-2. **Theme system**: ship dark + light themes (Monaco built-in), allow custom themes
- [ ] 9-3. **Keybinding configuration**: default keybindings, allow user overrides, preset profiles (VS Code, Vim, Emacs)
- [ ] 9-4. **Workspace settings**: per-workspace overrides for editor settings (tab size, formatter, etc.)
- [ ] 9-5. **Settings sync**: persist settings to `~/.dan/settings.json`, carry across reinstalls
- [ ] 9-6. **Workbench behavior**: settings for autosave policy, hot exit, restore last session, terminal persistence, and AI inline-edit defaults

## Phase 2: VS Code Parity + Extensions

Goal: Full IDE feature parity with VS Code/Cursor. Extension ecosystem. Advanced AI coding features.

Sequence matters:
- **Phase 2A:** native language intelligence first (LSP, diagnostics, formatter wiring, debugger basics)
- **Phase 2B:** general VS Code extension compatibility for the curated high-value subset
- **Phase 2C:** advanced AI coding UX (inline completion, multi-file edits, codebase Q&A)

### 10. Language Server Protocol (LSP)
- [ ] 10-1. **LSP client**: generic LSP client in the Electron main process, communicates with Monaco via IPC
- [ ] 10-2. **Auto-detect servers**: detect project type (package.json → TypeScript, pyproject.toml → Python, etc.) and launch appropriate language server
- [ ] 10-3. **Go to definition**: Cmd+click or F12 on symbols → navigate to definition (cross-file)
- [ ] 10-4. **Find references**: Shift+F12 → list all usages of a symbol
- [ ] 10-5. **Hover information**: hover over symbol → type info, documentation, signature
- [ ] 10-6. **Auto-complete (IntelliSense)**: context-aware completion as you type, documentation popups
- [ ] 10-7. **Diagnostics**: real-time errors/warnings from the language server → Problems panel
- [ ] 10-8. **Code actions**: quick fixes (auto-import, fix typo, extract variable), lightbulb icon
- [ ] 10-9. **Rename symbol**: F2 → rename across all files
- [ ] 10-10. **Format document**: Cmd+Shift+F → run language-specific formatter (prettier, black, gofmt)
- [ ] 10-11. **Signature help**: parameter hints in function calls
- [ ] 10-12. **Document symbols / outline**: symbol tree in sidebar (functions, classes, variables)
- [ ] 10-13. **Pre-installed language servers**: TypeScript (tsserver), Python (pyright/pylsp), Rust (rust-analyzer), Go (gopls), JSON, YAML, HTML/CSS
- [ ] 10-14. **Format on save**: configurable per language/workspace; uses formatter provider or workspace command
- [ ] 10-15. **Code actions on save**: organize imports, fix safe lint issues, language-specific safe transforms

### 11. VS Code Extension Support
- [ ] 11-1. **Extension host**: isolated process that loads VS Code-style extensions from the marketplace adapter layer
- [ ] 11-2. **Extension API compatibility**: implement core VS Code extension API surface (vscode namespace: window, workspace, languages, commands, debug)
- [ ] 11-3. **Extension discovery**: browse configured VS Code-style extension sources from within the app (Open VSX by default; local VSIX import and future compatible sources via [1-5-marketplace-extensions](1-5-marketplace-extensions.md))
- [ ] 11-4. **Install/uninstall**: download, extract, activate extensions; manage in settings
- [ ] 11-5. **Theme extensions**: VS Code themes (color themes, icon themes) load and apply
- [ ] 11-6. **Language extensions**: syntax highlighting grammars (TextMate), language servers, snippets
- [ ] 11-7. **Extension settings**: per-extension configuration via settings UI
- [ ] 11-8. **Popular extensions out-of-box**: ship with or auto-suggest essential extensions (Python, ESLint, Prettier, GitLens, Tailwind CSS IntelliSense)

### 12. Debugger (DAP - Debug Adapter Protocol)
- [ ] 12-1. **DAP client**: generic DAP client in Electron main process
- [ ] 12-2. **Breakpoints**: click gutter to toggle, conditional breakpoints, logpoints
- [ ] 12-3. **Debug controls**: start/stop/pause, step over, step into, step out, restart
- [ ] 12-4. **Variables panel**: inspect local/global/closure variables during pause
- [ ] 12-5. **Watch expressions**: user-defined expressions evaluated on each step
- [ ] 12-6. **Call stack**: navigate the call stack during pause
- [ ] 12-7. **Debug console**: evaluate expressions in the paused context
- [ ] 12-8. **Launch configurations**: `launch.json` equivalent (per-workspace debug configs)
- [ ] 12-9. **Inline values**: show variable values inline in the editor during debugging
- [ ] 12-10. **Pre-installed debug adapters**: Node.js (built-in), Python (debugpy), Go (dlv)
- [ ] 12-11. **Attach-to-process**: attach debugger to an already running local process when supported
- [ ] 12-12. **Breakpoint persistence**: remember breakpoints, conditions, and logpoints per workspace

### 13. Advanced Editor Features
- [ ] 13-1. **Breadcrumbs**: file path + symbol path below editor tabs, click to navigate
- [ ] 13-2. **Peek definition**: Alt+F12 → inline peek window showing definition without leaving current file
- [ ] 13-3. **Outline view**: sidebar showing document structure (classes, functions, variables)
- [ ] 13-4. **Call hierarchy**: view callers/callees of a function
- [ ] 13-5. **Color picker**: inline color preview and picker for CSS color values
- [ ] 13-6. **Emmet**: HTML/CSS abbreviation expansion (e.g., `div.container>ul>li*3`)
- [ ] 13-7. **Linked editing**: rename matching HTML open/close tags simultaneously
- [ ] 13-8. **Snippet system**: built-in snippets per language, user-defined snippets, Tab-stop fields
- [ ] 13-9. **Zen mode**: distraction-free fullscreen editing (Cmd+K Z)
- [ ] 13-10. **Split editor**: side-by-side editing of two files or two views of the same file
- [ ] 13-11. **Sticky scroll**: pin parent scope (function/class) at top while scrolling
- [ ] 13-12. **Indent guides**: visual vertical lines for indentation levels

### 14. AI Code Features (Beyond Chat)
- [ ] 14-1. **Inline completion (Copilot-style)**: ghost text suggestions as you type, Tab to accept
- [ ] 14-2. **Cmd+K inline editing**: select code → type instruction → AI edits inline with diff preview (enhanced version of Phase 1 task 7-9)
- [ ] 14-3. **Code explanation**: right-click selection → "Explain this code" → explanation in chat or inline popup
- [ ] 14-4. **Refactoring suggestions**: AI analyzes selected code and proposes refactoring (extract function, simplify, rename)
- [ ] 14-5. **Test generation**: right-click function → "Generate tests" → creates test file with relevant test cases
- [ ] 14-6. **Documentation generation**: right-click function/class → "Generate docs" → creates docstring/JSDoc
- [ ] 14-7. **Error fix suggestions**: when an error occurs, AI suggests a fix inline (similar to Cursor's "Fix" action)
- [ ] 14-8. **Codebase Q&A**: Cmd+Shift+I → ask questions about the codebase, AI searches and answers with file references
- [ ] 14-9. **Multi-file edits**: describe a change that spans multiple files → AI proposes edits in all affected files with unified diff review
- [ ] 14-10. **Commit message generation**: stage changes → AI generates commit message from diff

### 15. Source Control (Full Git UI)
- [ ] 15-1. **Interactive rebase**: visual rebase UI (reorder, squash, edit, drop commits)
- [ ] 15-2. **Stash management**: stash, pop, apply, drop, view stash contents
- [ ] 15-3. **Cherry-pick**: select commits from other branches to apply
- [ ] 15-4. **GitHub integration**: view PRs, create PRs, review PRs (diff + comments), view issues
- [ ] 15-5. **Inline blame with hover**: hover over blame annotation → full commit info popup
- [ ] 15-6. **Git graph**: visual branch/merge graph (like GitLens graph)
- [ ] 15-7. **Conflict resolution UI**: three-way merge editor (base, current, incoming, result)

### 16. Task Runner
- [ ] 16-1. **Auto-detect tasks**: parse `package.json` scripts, `Makefile` targets, `pyproject.toml` scripts, `Cargo.toml`
- [ ] 16-2. **Run tasks**: click to run, output in terminal, status indicator
- [ ] 16-3. **Problem matchers**: parse task output for errors/warnings, link to source files
- [ ] 16-4. **Custom tasks**: user-defined tasks (run command in directory)
- [ ] 16-5. **Build/test shortcuts**: Cmd+Shift+B (build), Cmd+Shift+T (test)

### 16A. Test Explorer & Coverage
- [ ] 16A-1. **Framework detection**: pytest, unittest, jest, vitest, mocha, go test, cargo test, and generic command fallback
- [ ] 16A-2. **Test explorer tree**: discover suites/files/tests and render pass/fail/skipped state in a sidebar or bottom panel
- [ ] 16A-3. **Run current test / file / suite / last failed**: one-click and keyboard-driven
- [ ] 16A-4. **Test results panel**: duration, stdout/stderr, failure diff, rerun button
- [ ] 16A-5. **Coverage support**: load coverage reports when available and show line/gutter overlays plus summary panel
- [ ] 16A-6. **Problems integration**: failing tests and stack traces link directly back to source locations

### 17. Workspace Intelligence
- [ ] 17-1. **Auto-detect project type**: scan workspace for config files (package.json, pyproject.toml, etc.), suggest relevant extensions and settings
- [ ] 17-2. **Recommended extensions**: suggest extensions based on project type
- [ ] 17-3. **Workspace-specific memory**: DAN remembers project conventions, frequently edited files, common commands, and coding patterns
- [ ] 17-4. **File change detection**: DAN notices when files change and can proactively offer help ("You modified the auth middleware. Want me to update the tests?")

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
