# Architecture

## Tech Stack

- **Core engine:** Python 3.11+, Pydantic v2, OpenAI SDK
- **Execution server:** FastAPI, uvicorn, websockets
- **Desktop app / visual editor:** TypeScript, React, React Flow v12 (`@xyflow/react`), Zustand, Tailwind CSS v4, Vite, Electron; Monaco (`@monaco-editor/react`), xterm.js (`@xterm/xterm`) for Code mode; react-pdf, KaTeX for Research mode; LSP (JSON-RPC over stdio) for language intelligence
- **App shell:** Mode-based workspace architecture with `AppShell` → `ModeBar` → mode components (`ChatMode`, `OperationsMode`, `CodeMode`, `ResearchMode`, future: Analytics, Content); state in `useAppStore`, `useCodeStore`, and `useResearchStore`; `electronBridge.ts` abstracts native APIs with browser fallback; `electron/main.ts` provides terminal IPC (create/write/resize/kill), ripgrep search IPC (`search:ripgrep`, `search:replaceInFile`), fs IPC (mkdir, rename, delete, exists), watch IPC (start/stop), git IPC (status, diff, log, stage, unstage, commit, branch, fileShow, push, pull, branchList, checkout, createBranch, remoteInfo, aheadBehind, blame), and LSP IPC (start, didOpen/didChange/didSave/didClose, completion, hover, definition, references, documentSymbol, formatting, codeAction, rename, signatureHelp) for Code mode
- **Schema validation:** JSON Schema (typed edges)
- **Shared contract:** versioned graph JSON (`dan_graph_v1`) between Python and TypeScript
- **Testing:** pytest, pytest-asyncio, httpx (ASGI test client); vitest (editor unit tests)

## Code Mode Component Architecture

- **`CodeMode.tsx`** — VS Code-like layout using allotment for resizable panels: activity bar (40px, files/search/git/outline/tasks/testing icons) switches sidebar content; sidebar (explorer/search/git/outline/tasks/testing, 250px default); editor area (Monaco tabs + optional SplitEditor); diff view overlay; chat sidebar (right pane); bottom panel tabs (terminal/problems/output); status bar; zen mode overlay (Cmd+K Z); codebase Q&A overlay (Cmd+Shift+I). `useCodeShortcuts` expanded: Cmd+\\ split editor, Cmd+Shift+B build, Cmd+Shift+T test. `useMonacoLsp` + `useLspDocSync` for language intelligence. Session restore, autosave.
- **`useCodeStore.ts`** — Zustand store: pinned roots, file tree, open files with dirty tracking, terminal instances, sidebar/terminal visibility, `activeSidebarPanel` (explorer/search/git/outline/tasks/testing), diff state, session restore, quick open, `zenModeFilePath`, `splitFilePath`.
- **`MonacoTabs.tsx`** — Multi-tab Monaco editor: tab bar (dirty/close/context menu/drag reorder), `Breadcrumbs.tsx` (file path + symbol navigation for 5 languages), PeekDefinition overlay (Alt+F12), large-file warning, drop zone, Cmd+S save, cursor tracking, inline git blame, merge conflict decorations, inline diff, context menu with AI actions (Explain/Tests/Docs/Refactor), inline completion status, sticky scroll + indent guides, snippet/color/emmet/linked-editing providers.
- **LSP infrastructure** — `electron/lspClient.ts` (JSON-RPC over stdio, Content-Length framing, 10s timeout), `electron/lspManager.ts` (multi-server lifecycle: TypeScript/Python/JSON auto-detected, file-extension routing), 14 IPC handlers in main.ts; `useMonacoLsp.ts` registers 9 Monaco providers (completion, hover, definition, references, symbols, signature help, code actions, rename, formatting); `useLspDocSync.ts` for document open/change/save/close sync + format-on-save.
- **AI code features** — `InlineCompletion.tsx` (Copilot-style ghost text via InlineCompletionsProvider, 500ms debounce, settings toggle), `aiCodeActions.ts` (8 functions: explain, tests, docs, refactor, error fix, commit message, codebase Q&A, multi-file edit), `ExplainPopup.tsx` (floating), `MultiFileEdit.tsx` (DiffEditor review), `CodebaseQA.tsx` (Cmd+Shift+I).
- **Task runner** — `taskDetector.ts` (auto-detect npm/make/poetry/cargo tasks), `TaskRunner.tsx` (sidebar panel, grouped by category, run/stop, custom tasks), Cmd+Shift+B build shortcut.
- **DAP debugger** — `electron/dapClient.ts` (Content-Length JSON protocol, 15s timeout, all DAP commands), `electron/debugManager.ts` (Node.js + Python adapters, session lifecycle, event forwarding), `useDebugStore.ts` (state + breakpoint/config persistence to localStorage), `DebugPanel.tsx` (4 collapsible sections: variables tree with lazy-load, watch expressions, call stack, breakpoints; controls toolbar; launch config selector; `DebugConsole` as bottom panel tab), `useBreakpointDecorations.ts` (gutter click toggle, red/yellow/blue decorations, execution line highlight, inline values).
- **Advanced Git UI** — Stash management in GitPanel (push/apply/pop/drop/view), cherry-pick on commits, `GitGraph.tsx` (SVG branch visualization with lane assignment algorithm, 8-color palette, ref badges, lazy scroll loading), enhanced blame hover tooltip (author/email/hash/message/dates/copy hash).
- **Workspace intelligence** — `workspaceIntelligence.ts` (parallel project type detection: Node/Python/Rust/Go/Docker, framework identification, extension recommendations), `WorkspaceInfo.tsx` (dismissible banner with project info + framework tags + suggested extensions), `useWorkspaceMemory.ts` (per-workspace memory: frequent files/commands/searches, persisted to localStorage), `useFileChangeDetection.ts` (external modification notifications for open files with reload/ignore actions).
- **Extension marketplace** — `marketplace/types.ts` (MarketplaceItem, InstalledItem, MarketplaceRegistry interface), `marketplace/openVsxAdapter.ts` (Open VSX API + 30s cache + configurable base URL), `marketplace/skillsAdapter.ts` (DAN Skills: local scan, frontmatter parsing, import-from-URL, template creation), `marketplace/mcpAdapter.ts` (MCP Servers: 8 curated servers, health polling, start/stop/status, auto-connect on startup), `marketplace/marketplaceManager.ts` (singleton router with `searchAll` + periodic update checking + configurable settings), `marketplace/trustModel.ts` (7-permission model), `marketplace/safeMode.ts` (failure recording + auto-disable after 3 failures), `marketplace/enableScope.ts` (global + per-workspace), `marketplace/compatibilityTier.ts` (Full/Partial/Static/Unsupported classification), `marketplace/nativeFallback.ts` (9 built-in capabilities, prefer-native for 6 extension IDs), `marketplace/curatedEssentials.ts` (10 essential extensions with project-type suggestion). `useMarketplaceStore.ts` (tabs: All/VS Code/Skills/MCP/Installed), `ExtensionsPanel.tsx` (search/filter/sort/install/detail/recommended/VSIX import + Skills/MCP sub-views with health dots + compat badges + marketplace settings panel).
- **Extension host** — `electron/extensionHost.ts` (forked Node.js process manager with IPC, 512MB heap cap, 30s request timeout), `electron/extensionHostWorker.ts` (sandboxed worker with partial `vscode` namespace shim: commands, window, workspace, languages, 12 core types; module interception for `require("vscode")`; activation events: `*`, `onLanguage:*`, `onCommand:*`, `workspaceContains:*`).
- **Icon themes** — `extensions/iconThemeLoader.ts` (loads VS Code icon theme JSON, filename→extension→compound→language→default resolution, caching), `FileIcon.tsx` (themed icons with 18-color lucide fallback).
- **MCP chat integration** — `mcpChatIntegration.ts` (curated tool catalog for 6 MCP server types, `refreshMcpTools`/`getMcpTools`/`getMcpToolSuggestions`).
- **Recipe Store** — `marketplace/recipeModel.ts` (Recipe, RecipeMetadata, DomainVector, ExtractedPattern, MemoryItem types; create/serialize/deserialize/bumpVersion/mergeKnowledge), `marketplace/recipeAdapter.ts` (RecipeStoreAdapter: 5 sample domain recipes, local storage at `~/.dan/recipes/`, category/sort filtering, README generation), "Recipes" tab in ExtensionsPanel.
- **Skill Hub remote** — `skillsAdapter.ts` enhanced with `enableHub()`/`searchHub()`, 60s cached fetch, local-first deduplication, cloud toggle in Skills tab UI.
- **Adapter documentation** — `marketplace/ADAPTER_GUIDE.md` (step-by-step guide for adding new registries, interface reference, patterns, future registry ideas, `RemoteRegistryAdapter` template).
- **Auto-updater** — `electron-updater` integration in `main.ts` (no auto-download, 4h check interval, production-only), `UpdateNotification.tsx` (banner with download progress).
- **Platform builds** — `electron-builder` config in `package.json` (macOS DMG+ZIP with entitlements, Windows NSIS, Linux AppImage+deb), `dist`/`dist:mac`/`dist:win`/`dist:linux` scripts.
- **Terminal (node-pty)** — `node-pty` via Electron IPC for true PTY with SIGWINCH resize, xterm-256color, platform-aware shell detection, `@electron/rebuild` for native module compilation.
- **Extension runtime** — `extensions/themeLoader.ts` (VS Code→Monaco theme + 10 CSS workbench vars), `extensions/grammarLoader.ts` (TextMate→Monarch for 15 scope categories), `extensions/snippetLoader.ts` (VS Code snippet JSON→Monaco provider), `extensions/langConfigLoader.ts` (comments/brackets/folding/indent with JSONC), `extensions/extensionActivator.ts` (orchestrates loading all contributions on startup). Extensions stored at `~/.dan/extensions/`.
- **Call hierarchy** — `CallHierarchy.tsx` (incoming/outgoing calls panel, LSP `prepareCallHierarchy`/`incomingCalls`/`outgoingCalls` + regex fallback).
- **Problem matchers** — `problemMatcher.ts` (8 regex patterns for TS/ESLint/Python/GCC/Rust/Go, integrated with TaskRunner output).
- **Coverage** — `coverageLoader.ts` (LCOV/Istanbul JSON/Cobertura XML parsers, auto-discovery from 7 paths), `CoverageOverlay.tsx` (green/red Monaco decorations + summary bar + toggle).
- **Interactive rebase** — `InteractiveRebase.tsx` (modal with draggable commits, 6 actions, programmatic GIT_SEQUENCE_EDITOR, conflict/abort/continue flow).
- **Merge editor** — `MergeEditor.tsx` (3-way Allotment merge: ours/theirs/base/result, conflict marker parsing, Accept buttons, Alt+Up/Down navigation, Mark Resolved).
- **GitHub integration** — `GitHubPanel.tsx` (PRs + issues via `gh` CLI: list/detail/create/merge/review/checkout, state badges, file changes), toggled from GitPanel header.
- **Test explorer** — `testDetector.ts` (jest/vitest/pytest/go test framework detection + output parsing), `TestExplorer.tsx` (sidebar panel, tree view, stats bar, failure details with file:line linking), Cmd+Shift+T shortcut.
- **Advanced editor** — `OutlineView.tsx` (symbol tree sidebar), `SplitEditor.tsx` (Cmd+\\), `ZenMode.tsx` (Cmd+K Z), `PeekDefinition.tsx` (Alt+F12), `snippets.ts` (60+ snippets for 7 languages + color/emmet/linked-editing providers).
- **WebSocket + event routing** — `wsConnection.ts` (singleton WebSocket with exponential backoff reconnection), `eventRouter.ts` (mode-specific event dispatch: chat/research/code/global routes), `useEventRouter.ts` (React hook wired into AppShell).
- **Chat enhancements** — `WorkspaceContextBar.tsx` (pinned paths + active task), `ModePreview.tsx` (miniature mode layout previews), `switchPreference.ts` (always/ask/never per mode), `VoiceInput.tsx` (Web Speech API).
- **Research enhancements** — `SplitPdfReader.tsx` (dual-pane Allotment), persistent PDF annotations (5 colors + notes + export), citation-linked navigation, `PageSummary.tsx` (per-page AI summaries), tool integration for code cells (5 tools).
- **`FileExplorer.tsx`** — VS Code-style explorer: pinned root sections (collapsible, lazy-loaded), recursive tree nodes, file type icons, context menus (file and root), hidden-file filtering. InlineInput for new file/folder/rename; F2 rename, Cmd+Backspace delete. ExplorerFilter for search within tree. Gitignore-aware filtering with Eye/EyeOff toggle. Draggable file nodes (text/uri-list). AgentSuggestedSection for DAN-mentioned files with "(new)" badge.
- **`SearchPanel.tsx`** — Cross-file search via ripgrep: case-sensitive, whole-word, regex toggles; include/exclude filters; debounced search; results grouped by file; full search-and-replace. Search in selection mode (TextSelect toggle) for searching within editor selection via JS regex.
- **`GitPanel.tsx`** — Git source control: current branch, staged/unstaged/untracked groups (parsed from `git status --porcelain`), stage/unstage buttons, push/pull buttons (loading/error states), branch switcher dropdown (search, local/remote branches, create new), ahead/behind indicators, commit message + commit button, last 20 commits, click-to-diff, auto-refresh on visibility.
- **`DiffView.tsx`** — Side-by-side diff viewer (Monaco DiffEditor): file names header, close button, side-by-side/inline toggle, previous/next change navigation, addition/deletion counts. Accept All / Reject All buttons; modified side editable for pre-accept tweaks. Theme from `useSettingsStore` (dark default).
- **`ChatSidebar.tsx`** — Lightweight AI chat for Code mode: message bubbles, markdown, workspace context, quick actions, streaming API. Code block Apply/Insert/Copy/Run buttons; file path linkification; View Diff buttons for file_write patterns; `dispatchShellCommand` for terminal streaming; `extractFilePaths` pushes to agent-suggested files.
- **`ProblemsPanel.tsx`** — VS Code-style problems: polls Monaco markers every 2s, merged with external diagnostics (from DAN tool output via `pushExternalDiagnostic`), grouped by file, severity icons, click-to-navigate, filter input; exports `useProblemsCount` hook. "Send to AI" button via `sendToChat()`. Purple "DAN" source badge for external diagnostics.
- **`TerminalPanel.tsx`** — Integrated terminal: @xterm/xterm + fit/web-links addons, dark theme, tab bar (create/close/switch), split terminal, ResizeObserver auto-fit, Cmd+C/V copy/paste, right-click context menu, profile dropdown (zsh/bash/sh), `chat:shellCommand` event listener for creating named command terminals.
- **`OutputPanel.tsx`** — Output/logs panel with selectable channels, timestamped log entries, search/filter bar (Cmd+F), copy to clipboard, export as .log file, actionable file:line links that open files in editor.
- **`sessionPersistence.ts`** — Persist/restore editor sessions to localStorage: file paths, pinned roots, layout state.
- **`autosave.ts`** — Autosave dirty files: 1s debounce, `beforeunload` safety save, `requestIdleCallback` optimization. Pushes local history on save.
- **`useSessionRestore.ts`** — React hook: restore session on mount, check crash snapshots, start autosave + crash recovery, periodic session save every 10s.
- **`crashRecovery.ts`** — 30s periodic localStorage snapshots of dirty buffers; `getCrashSnapshot` / `clearCrashSnapshot` for restore. `CrashRecoveryBanner.tsx` shows dismissible yellow banner.
- **`largeFileMode.ts`** — `getLargeFileConfig(contentLength)` returns feature-disable flags at 1MB/5MB thresholds (minimap, tokenization, bracket colorization, folding, git blame).
- **`localHistory.ts`** — Per-file edit history in localStorage: max 50 entries/file, 60s min interval, 100 max files. `pushLocalHistory`, `getFileHistory`, `clearFileHistory`.
- **`gitignoreFilter.ts`** — `parseGitignore(content)` returns a predicate for gitignore patterns (*.ext, dir/, wildcards, negation).
- **`keybindings.ts`** — Default keybindings (13 actions) with localStorage user overrides, `formatKey` for platform symbols.
- **`useInlineDiff.ts`** — Inline diff decorations: `computeInlineDiffs` compares original vs modified content; `useInlineDiffDecorations` applies Monaco deltaDecorations with green/red backgrounds.
- **`QuickOpen.tsx`** — Cmd+P fuzzy file finder: recursively indexes files across pinned roots (up to 10K, skips node_modules/.git/etc.), fuzzy matching with scoring (consecutive, boundary, filename-start bonuses), recently-opened boost, highlighted match characters, keyboard nav (arrows, Enter, Escape), dark theme dropdown modal.
- **`useFileWatcher.ts`** — File watcher hook: syncs watchers with open files, debounces fs.watch events (100ms), reloads non-dirty files silently, skips reload if user typed within 500ms, cleans up on unmount.
- **`InlineEdit.tsx`** — Cmd+K AI inline edit: prompt input → AI stream → preview diff → accept/reject.
- **`SettingsPanel.tsx`** — Full settings UI with User/Workspace tabs: Appearance (theme), Editor (fontSize, tabSize, wordWrap), Files (autosave), workspace overrides.
- **`CommandPalette.tsx`** — Cmd+Shift+P command palette with built-in commands including symbol search, local history, keybindings.
- **`useSettingsStore.ts`** — Zustand persist store for editor settings, terminal profiles, workspace overrides per pinned root.
- **`KeybindingsPanel.tsx`** — Keyboard shortcuts UI: searchable table grouped by category, inline key capture, per-binding reset.
- **`SymbolSearch.tsx`** — Cmd+T fuzzy symbol search: regex-based extraction for TS/JS/Python/Rust/Go, kind-colored badges, keyboard nav.
- **`LocalHistoryPanel.tsx`** — Timeline sidebar showing per-file edit history, compare/restore actions, relative timestamps.
- **`WriteConfirmDialog.tsx`** — Modal for first write outside pinned roots: Allow Once / Pin parent / Cancel.
- **`useGitBlame.ts`** — Git blame hook: porcelain-format parser, relative dates, used by MonacoTabs for inline blame decoration.
- **`useMergeConflicts.ts`** — Merge conflict detection: `detectConflicts()` parses `<<<<<<<`/`=======`/`>>>>>>>` markers; `useMergeConflictDecorations()` applies Monaco decorations (green/blue regions, overview ruler, inline action labels).
- **`lspClient.ts`** (electron) — Generic LSP client over stdio: JSON-RPC framing (Content-Length headers), request/response with timeout, notification passthrough, document sync (didOpen/didChange/didSave/didClose), language features (completion, hover, definition, references, documentSymbol, formatting, rangeFormatting, codeAction, rename, signatureHelp), graceful shutdown.
- **`lspManager.ts`** (electron) — Manages multiple `LspClient` instances per language server. Auto-detects project type (TS/Python/JSON) from root files, routes requests by file extension or language ID, forwards diagnostics and notifications to renderer via IPC. Configs for TypeScript, Python, JSON, CSS, HTML servers.
- **`useMonacoLsp.ts`** — React hook: registers 9 Monaco language providers (completion, hover, definition, references, documentSymbol, signatureHelp, codeAction, rename, formatting) backed by `nativeLsp` IPC calls. LSP-to-Monaco type converters (CompletionItemKind, SymbolKind, Range, MarkerSeverity, WorkspaceEdit). Diagnostics listener updates Monaco model markers in real-time.
- **`useLspDocSync.ts`** — React hook: subscribes to `useCodeStore` for open/close/change events, sends LSP didOpen/didChange (100ms debounce)/didClose notifications. Listens for `lsp:fileSaved` CustomEvent to send didSave + trigger format-on-save (applies LSP formatting edits to store and disk).

## Research Mode Component Architecture

- **`ResearchMode.tsx`** — Research workspace layout using Allotment: left sidebar (ResearchNav + PipelineProgress), primary panel (WritingPane/PdfReader), context panel (References/Reviews/Outline/Notes/Distillation tabs), secondary panel (CodeCells/FigureGallery/DataBrowser). All panels loaded via React.lazy + Suspense. Quick-start empty state with 6 workflow templates. Domain profile selector in sidebar. Keyboard shortcuts: Cmd+E toggle editor/reader, Cmd+Shift+R references, Cmd+Shift+O outline.
- **`useResearchStore.ts`** — Zustand store: papers CRUD, pipeline stages (with presets: research/distillation), notes, document content, tab state (primary/context/secondary), layout toggles, domain profile, active quick-start. Pipeline manipulation: add/remove/reorder stages. PipelineStageDetails for per-stage metrics.
- **`WritingPane.tsx`** — Monaco editor in markdown mode: serif font with 1.8 line-height for research writing; Edit/Split/Preview view modes; KaTeX rendering for `$...$` and `$$...$$` math via `MarkdownPreview`; floating AI toolbar on selection (Expand/Formal/Cite/Simplify/Translate); citation autocomplete on `[@`; track changes with accept/reject; section status badges; export to Markdown/LaTeX.
- **`PdfReader.tsx`** — react-pdf viewer: zoom, page navigation, in-PDF text search with highlight, text selection actions (Summarize/Add to Notes/Cite/Ask), drag-and-drop PDF ingest.
- **`ReferencePanel.tsx`** — Reference management: search/sort (by year/title/added), add via file picker, BibTeX export, citation formatting (APA/Chicago/Harvard), status dots (unread/reading/read), expandable abstracts, DOI links, "Find Similar" action.
- **`ReviewPanel.tsx`** — Structured review feedback: round-based navigation with major/minor counts, iteration tracker (R1→R2→R3 trend), severity badges, accept/dismiss/reply actions, manual review input form, reviewer criteria config.
- **`OutlinePanel.tsx`** — Document outline synced from markdown headings: hierarchical indentation, per-section word count, status dots, click-to-scroll, "Generate" and "Add section" actions.
- **`NotesPanel.tsx`** — Quick-capture notes: Ctrl+Enter save, `#tag` extraction, tag filter chips, full-text search, source type icons (PDF/chat/manual), relative timestamps.
- **`DistillationTab.tsx`** — "Learn 100 Papers" with 3 views: setup (topic/count/criteria/sources/exploratory toggle), running (progress bar + 6-metric grid + paper management + pause/resume/cancel), results (6 sub-tabs: Terms/Methods/Questions/Tensions/Concept Map/Benchmarks + recipe export).
- **`CodeCells.tsx`** — Jupyter-like cells: per-cell Monaco editor (auto-grow, Python/R/JS selector), run button with status indicators, mock execution (pattern-based: print→text, pd.DataFrame→table, matplotlib→Chart.js), "Insert into paper" on outputs.
- **`FigureGallery.tsx`** — 2-column grid with lightbox, upload, "Insert into paper" action per figure.
- **`DataBrowser.tsx`** — File list (CSV/JSON/XLSX), click-to-preview sortable/filterable table, file import.
- **`QuickStartPanel.tsx`** — 6 pre-configured workflows (Literature Review, New Paper, Review Paper, Compare Papers, Summarize Paper, Learn 100 Papers), each with pipeline stages and initial content. Filterable by domain profile.
- **`DomainProfile.tsx`** — 3 profiles (Academic/Market Research/Policy Analysis): each defines citation style, export format, writing tone, templates, review criteria. Persisted to localStorage.
- **`researchEventRouter.ts`** — Pub/sub event routing: 10 event types (artifact_created, review_output, distillation_progress, node_started/completed, llm_chunk, tool_output, node_error) dispatched to Research store and panels. Singleton init with teardown.
- **`useResearchEvents.ts`** — Hook that initializes event routing on mount and bridges `dan:engine-event-raw` CustomEvents.
- **`researchEventDemo.ts`** — Pipeline simulation utility for demo/testing with timed events across 7 stages.

## Panel Framework (Generic)

- **`Panel.tsx`** — Generic panel component: header (title, icon, actions), collapse toggle (chevron), content area. Supports controlled collapse via `collapsed`/`onCollapsedChange` for layout persistence. Low-dependency building block for mode-specific layouts.
- **`PanelLayout.tsx`** — Configurable layout using Allotment: left/center/right/bottom slots via `PanelLayoutConfig`. Each mode registers panel components by ID; PanelLayout renders them. Code mode keeps its own Allotment setup; this framework is for new modes (Chat, Research, Operations, etc.).
- **`layoutPersistence.ts`** — Per-mode (and optionally per-workspace) layout state in localStorage: `saveLayoutState`, `loadLayoutState`, `usePanelLayoutPersistence` hook for panel sizes and collapsed panels.
- **`defaultLayouts.ts`** — Default layout descriptions per mode (chat, development, operations, research); user overrides layered via layoutPersistence.
- **`useResponsiveLayout.ts`** — Debounced (150ms) viewport width hook: `isNarrow` (<768px), `isMedium` (768–1200), `isWide` (≥1200), `shouldHideSidebar`, `shouldStackPanels` for responsive breakpoints.

## Server Startup & State

- `src/dan/server/app_state.py` — `AppState` dataclass: typed container for all server-side runtime state (stores, managers, registries, integrations, background tasks, adapters). Attached to `app.state.dan` during lifespan. Includes `require_*` accessor guards that raise HTTP 503 if a subsystem isn't initialized yet.
- `src/dan/server/startup.py` — server lifespan and phased initialization (Plan 35-5). Contains all helper functions moved from `app.py` (`_get_engine_config`, `_build_chat_provider_registry`, MCP bridge helpers, `_auto_register_published_workflows`, `_consolidation_loop`, `_build_tool_registry`) plus 8 phased init functions: `init_learning_tiers()`, `init_stores(state)`, `init_engine(state)`, `init_capabilities(state)`, `init_managers(state)`, `init_integrations(state, app)`, `init_background(state, app)`, `shutdown(state)`. The `lifespan(app)` async context manager composes all phases and mirrors state to `app.py` module globals via `_mirror_state_to_globals()` for backward compat.
- `app.py` is the composition root (397 lines): `FastAPI(lifespan=lifespan)`, CORS middleware, `include_router()` calls for 10 router modules, compatibility re-exports, and static file serving. All route handlers live in `src/dan/server/routers/`. Module globals (`_graph_store`, `_run_manager`, etc.) are kept as a compatibility layer, populated from `AppState` during lifespan.

## Route Handlers Package

- `src/dan/server/routers/` — domain-grouped FastAPI `APIRouter` modules extracted from `app.py` (Plan 35-2).
- `dependencies.py` — shared accessor functions (`get_graph_store`, `get_run_manager`, `get_chat_manager`, etc.) using deferred imports to avoid circular dependencies.
- `misc.py` (487 lines) — health, cache, metrics, files/docs/code-refs, test cases, memory, errors, rules.
- `graphs.py` (509 lines) — graph CRUD, mutation, validation, export, node inputs, boundary validators, `build_token_analysis_context`.
- `rag.py` (122 lines) — RAG collection CRUD + lazy-initialized indexer.
- `runs.py` (505 lines) — run lifecycle, checkpoints, compare, scoped run, token breakdown, optimization, WebSocket.
- `experiences.py` (124 lines) — experience CRUD + semantic search + refresh.
- `meta.py` (241 lines) — meta-orchestrator discover/plan/validate/run/sessions + WebSocket. Module-level `_meta_tasks` and `_meta_subscribers` state.
- `publishing.py` (143 lines) — publish/unpublish/MCP-config/status.
- `blocks.py` (172 lines) — block CRUD + export.
- `chat.py` (805 lines) — chat message (with full `_produce()` closure), stop, CRUD, search, export, pin, checkpoint, WebSocket. Module-level chat stream state (`_chat_streams`, `_chat_produce_tasks`).
- `adapters.py` (438 lines) — adapter start/stop/status + all runtime logic. Module-level adapter state dicts.

## Server Tools Package

- `src/dan/server/tools/` — domain-specific tool implementations extracted from `app.py` (Plan 35-1).
- `_shared.py` — low-level helpers (`_http_get_json`, `_run_command`) shared across tool modules.
- `latex.py` — LaTeX compilation, paper saving, submission packaging, citation key extraction, `informs3.cls` auto-fetch.
- `research.py` — Semantic Scholar paper search, citation verification.
- `quant.py` — `run_strategy_script` (script-as-param strategy execution with factor validation), `run_backtest` (subprocess backtest), `save_grid_csv` / `plot_backtest` (deprecated, kept for backward compat).
- `general.py` — `run_python` (generic code execution), `get_department_state` / `update_department_state`, `rag_index_documents` (dependency-injected `get_indexer`).
- `__init__.py` — re-exports all tool functions and provides `register_server_tools(registry, *, get_indexer=None)` which wires all 14 tool IDs into a `ToolRegistry`.
- `app.py`'s `_build_tool_registry()` delegates to `register_server_tools()` for domain tools and only handles built-in tool registration and custom-tool discovery itself.

## Capability Handlers Package

- `src/dan/server/capabilities/` — domain-grouped handler implementations extracted from `capability_handlers.py` (Plan 35-4).
- `_helpers.py` — shared utilities: `_truncate`, `_sanitize_web_content`, `_failure_result`, `_classify_network_exception`, `_resolve_user_path`, `_schedule_export_cleanup`, `_FILE_READ_MAX`.
- `web.py` — `handle_web_search`, `handle_web_fetch`, `handle_http_request`.
- `file_io.py` — `handle_file_read`, `handle_file_write`, `handle_file_grep`, `handle_pdf_read`, `handle_list_directory`, `handle_file_copy/move/delete`, `handle_compress`.
- `git.py` — `handle_git_status/diff/log/branch/commit/worktree`.
- `shell.py` — `handle_shell_command`, `handle_screenshot`, `handle_clipboard`, `handle_notify`.
- `data.py` — `handle_python_eval`, `handle_csv_read`, `handle_spreadsheet_read`, `handle_json_extract`, `handle_regex_match`, `handle_text_chunk/diff/translate`.
- `media.py` — `handle_image_describe`, `handle_audio_transcribe`.
- `config.py` — `handle_get_config`, `handle_set_config`, `_update_env_file`.
- `experiences.py` — workflow history/catalog/activity handlers + `_format_experience_summary`.
- `publishing.py` — publish/export/share/block handlers + `_resolve_graph`.
- `runs.py` — run lifecycle handlers + `resolve_run_reference`, `get_pending_run_disambiguation`.
- `browser.py` — browser/desktop computer-control handlers + `_get_controller`, `set_controller`.
- `introspection.py` — `handle_inspect_node`, `handle_list_test_cases`, `handle_run_test_case`.
- `misc.py` — `handle_current_datetime`, `handle_telegram_poll`, `handle_send_email`.
- `__init__.py` — lazy re-exports of `register_*` functions for convenience (canonical import: `dan.server.capability_handlers`).
- `capability_handlers.py` is now a thin registration hub: all schemas, all 8 `register_*` functions, and imports from domain modules.

## Concierge Runtime

- `src/dan/server/concierge/` is the shared Phase 15 control-plane package.
- `ProjectStore` plus `ResolvedContext` give the concierge a durable `Project` / `Task` scope above raw chat threads. `runtime.py` resolves context directly via `_resolve_context()`; the old `ProjectContextResolver` module was deleted.
- `models.py` is the canonical home for shared concierge state and routing types: `SurfaceMessage`, `Project`, `Task`, `PendingAction`, `ConciergeState`, `IntentCategory`, `RouteMode`, `RouteDecision`, and `ResolvedContext`.
- `runtime.py` is now a thin shell: registry-backed fast-command dispatch, per-turn telemetry, progress/reassurance, task finalization, memory/domain hooks, and handoff into the tiered dispatcher. The legacy classifier/handler/solver/build-session/goal-loop path is gone.
- `triage.py` owns the single structured triage contract: `TriageResult` / `EntityRef`, the triage prompt+parser, context-needs normalization, entity ID grounding, and resume-cue filling. On model failure it returns a safe default triage result instead of falling back to the deleted heuristic classifier stack.
- `tiered_dispatch.py` is the only concierge execution path. It coordinates triage → targeted context gathering → tier executor dispatch → session telemetry → root-session persistence. `session.py` provides `SessionManager`, `Session`, `SessionTier`, `SessionState`, `SessionResult`, and `SessionTrace`.
- `tier_executors.py` owns `InstantExecutor`, `SingleShotExecutor`, and `MultiStepExecutor`. Tier 1 and Tier 2 call `chat_manager.send_message_with_tools()` directly; there are no handler wrappers or solver indirection layers left.
- **Concierge Tiered Execution (31-26):** Each concierge pipeline stage is mapped to a canonical tier (`micro`, `routine`, `reasoning`, `critical`) via `CONCIERGE_STAGE_TIERS` in `concierge/tiering.py`. The `ConciergeTierResolver` resolves stages to concrete models using the user's normalized `DAN_TIER_MAP`. This is separate from the workflow engine's `TierPolicy` scoring — concierge uses explicit stage-based mapping while the engine uses dynamic task scoring. Stage assignments: `classification` → micro (fast structured routing); `intent_extraction`, `goal_resolver`, `workflow_build`, `conversation_plan`, `conversation_debug` → reasoning (complex planning / higher-cognition modes); `conversation`, `direct_task`, `file_review`, `experience_fallback` → routine (everyday tasks). Numeric shorthand (`"1"`, `"2"`, `"3"`) is normalized by `normalize_tier_map()` in `providers/tier_defaults.py` before reaching either system.
- `command_registry.py` is still the canonical slash-command inventory (31-16), but only live commands remain registered. Deleted concierge handlers and their slash commands were removed from the registry.
- `dispatcher.py` provides `ConcurrentDispatcher` — a per-project concurrency layer wrapping `Concierge`. Different projects process in parallel; same-project messages queue and drain serially.
- `policy.py`, `progress.py`, `progress_ux.py`, `resources.py`, `identity.py`, `scheduler.py`, `computer_policy.py`, `computer_use.py`, and `domain_learning.py` are the kept support modules. Deleted legacy modules include `classifier.py`, `handlers.py`, `solver.py`, `context_resolver.py`, `resume.py`, `follow_up.py`, `promotion.py`, and `queue.py`.
- `identity.py` is the single source of truth for bot name and prefix formatting. `get_bot_name()` reads `DAN_BOT_NAME` env var (default `"DAN"`). `extract_label_from_prefix()` parses `[DAN - Project / Task]` → `("Project / Task", "remaining text")`. `format_compact_label()` produces Telegram-friendly `[Project]` headers without the bot name.
- `telegram_router.py` uses a signal-based routing architecture. `MessageRouter` evaluates a configurable chain of `RoutingSignal` instances in priority order; the first non-None result wins. Default signal chain: `MentionSignal` (confidence 1.0) → `ReplyOwnershipSignal` (0.9, new: replying to a bot's message routes to that bot) → `TopicMappingSignal` (0.85, forum topic → project) → `ProjectKeywordSignal` (0.7) → `DefaultBotSignal` (0.1). Signals are pluggable: `add_signal()`, `remove_signal()`, `set_signals()`. `RoutingContext` carries all routing inputs including `reply_to_bot` (extracted from the fleet's outbound message tracking). A failing signal is caught and skipped.
- **Portal contract (31-25):** `ChatMessageRequest` has unified identifiers: `surface_type` (platform kind), `surface_id` (instance within type), `session_id` (conversation continuity key). A model validator syncs `surface` ↔ `surface_type:surface_id` and `session_id` ↔ `thread_id` bidirectionally, rejects conflicting/partial aliases, and keeps `history` to `user`/`assistant` roles only. `SurfaceMessage` has matching `surface_type`/`surface_id`/`session_id` fields. All adapters must send `surface_context` with at minimum `{"identity": {"name": ..., "role": ...}}` — the concierge renders this via `_surface_identity_instructions`. Streaming protocol documented in `docs/llm-api-guide.md`: 10 WS event types (`chat_token`, `chat_complete`, `chat_queued`, `chat_error`, `chat_interrupted`, `chat_tool_call_start`, `chat_tool_call_result`, `chat_file_attachment`, `chat_poll_request`, `chat_mutation`).
- `telegram_fleet.py` converts the concierge's `[DAN - Project]` prefix to a compact `[Project]` header via `_format_for_telegram()` (replacing the old `_strip_prefix_and_html()` which stripped prefixes entirely). Combined with Telegram's native `reply_to_message_id`, this gives users both project context and conversational threading. The Telegram adapter keeps replied-to text in `MessageContext.reply_to_text`, but no longer prepends quoted reply text into the primary user message body sent upstream; lane keys and history preserve continuity without polluting classification, task labels, or file-search queries with historical quoted text. The fleet now also sends raw conversation history plus structured `surface_context` (`identity` + `peers`) to `/api/chat/message` instead of injecting adapter-authored system messages into `history`; concierge handlers turn that metadata into the final prompt instructions so persona/project-focus rules live in one place. Telegram adapter send methods (`send_or_edit`, `_send_text`, `_try_send_markdown`, `send_poll`) all accept `thread_id: int | None = None` for forum-topic threading; the fleet passes `ctx.thread_id` to all 15+ call sites. Error visibility: `_dispatch` sends a user-visible error message on HTTP non-200 and on dispatch exceptions (not just ❌ reaction). The fleet's `_lookup_reply_to_bot()` extracts the bot name from `_message_lanes` (keyed by `(chat_id, message_id)`) and passes it to the router for reply-sticky ownership. Progress updates use exponential backoff: 10s initial → 20s → 30s → ... up to `DAN_TELEGRAM_PROGRESS_MAX_INTERVAL` (default 300s), configurable via `DAN_TELEGRAM_PROGRESS_BACKOFF` (default 1.5). When conversation has moved on (other messages sent since last progress edit), the adapter sends a new message instead of editing the stale one.
- `telegram_fleet.py` keeps two different Telegram notions of continuity on purpose. The broad conversation key (`chat_id:thread_id-or-main:bot`) is still sent to the server as `thread_id` so concierge/project continuity survives across turns. But the fleet's local history lock is narrower in private chats: replies reuse the earlier lane, while unrelated private messages get per-message lanes. This mirrors the WhatsApp Web adapter's "no adapter-wide execution lock" behavior and prevents one long-running DM turn from blocking an unrelated one before the shared dispatcher has a chance to classify and queue it properly.
- `telegram_fleet.py` also has to follow dispatcher queue handoffs explicitly. When `/api/chat/message` emits `chat_queued`, the payload can contain a replacement `stream_channel_id`; the fleet's direct WebSocket client now transparently reconnects to that redirected channel in both streaming and non-streaming paths so queued Telegram turns still receive their eventual terminal reply.
- **CLI/WhatsApp adapter reliability (31-25):** The generic adapter (`cli/adapter.py`) now sends clean history (no system messages) and moves `_ADAPTER_CONTEXT` into `surface_context.adapter_instructions` with structured identity envelope, matching the Telegram fleet pattern. The WS event loop handles `chat_queued` redirects with cycle detection (ported from fleet). A background task sends "Working on it..." after 5s of silence (once per turn). Poll text fallback renders polls as numbered lists for adapters without `send_poll_for_session`. WhatsApp `_send_text` retries once with 2s backoff. `_connect_with_retry()` adds exponential backoff reconnection (2s base, 300s max, 10 attempts). WhatsApp Web `allowed_jids` is now an exact inbound/outbound allowlist for live-safety controls: it accepts bare phone numbers or full JIDs, strips `:device` suffixes on `s.whatsapp.net` JIDs, and blocks disallowed text/file/fallback sends before recipient resolution.
- **Clarification UX (31-25):** `_format_clarification_text()` in `runtime.py` renders `ClarificationRequest` options as numbered lists (`1. Option A\n2. Option B`) so messaging users can reply with a number.
- `FileHandler` treats explicit filesystem paths as scope hints, not ranking terms. If a message says "look into X, notes in /path/to/projects", the handler keeps `/path/to/projects` as the search root, strips the explicit path from the ranking query, and reports unique directory matches as `Found folder: ...` instead of `Found file: ...`.
- `DIRECT_TASK` uses the non-build fast path for all high-confidence direct tasks (skipping the heavy solver is critical — the solver/intent-compiler takes 600+ seconds for research prompts). Within the handler, `DirectTaskHandler` now distinguishes simple vs complex direct tasks via `_looks_like_complex_direct_task()`: simple tasks use `conversation` mode, while complex artifact-producing asks (comprehensive reports, papers with citations/figures, output-format constraints) are promoted to `agent` mode with `allow_mutation_tool=False`, so they get richer capability-tool behavior without exposing the workflow-mutation lane.
- `resources.py` provides `ResourceBudget` (configurable limits read from `DAN_MAX_CONCURRENT_LLM`/`DAN_MAX_CONCURRENT_RUNS`/`DAN_MAX_MEMORY_MB` env vars), `ResourceTracker`, `MessagePriority`, and `PriorityQueue`.
- `terminal_output.py` is a side-effect-free server helper for collapsing async chat event streams into terminal-facing content. Scheduled dispatch uses it to preserve multi-fragment terminal answers while filtering reassurance / `progress_ack` noise, and tests should import this helper instead of `app.py` to avoid `.env` side effects from `load_dotenv()`.
- `chat_titles.py` owns server-side thread naming for persisted chat history. Threads may start with a readable fallback title derived from the first user message, then upgrade asynchronously to a micro-tier generated title after the first turn is saved. Title metadata lives alongside other thread meta flags (`title_source`, `title_locked`, generation status), so manual renames are preserved across surfaces. `app.py` retries generation on later message saves if an earlier provider call failed, instead of permanently suppressing future attempts.
- Chat thread persistence is shared across server and editor now: `src/dan/server/chat_store.py` persists `tool_calls`, `run_events`, and file `attachments` alongside core message text/metadata, and `editor/src/lib/chatMessagePersistence.ts` is the single save/load mapper used by `ChatPanel` so autosave and thread reload keep the same contract for tool traces, `estimated_cost`, scoped `run_ref`, and artifact chips. `ChatPanel` now saves the initial user+assistant turn as soon as a stream is accepted, debounces partial streaming saves, persists milestone updates (tool results, attachments, run events), and flushes pending snapshots on disconnect/error so mid-stream history survives reloads. The editor-side coordination logic lives in `editor/src/lib/threadPersistenceCoordinator.ts`, which ensures a newer immediate terminal save for a thread cancels any older delayed snapshot that would otherwise overwrite it a few seconds later.
- Live chat streams are now reconnect-tolerant. `app.py` keeps the in-flight chat queue/producer alive when `/api/chat/{channel}/events` disconnects unexpectedly while the producer task is still running, and it now also keeps a channel mapped for a short TTL whenever a reconnectable terminal snapshot still exists. Missing chat channels accept the WebSocket first and then close with code `4004`, so clients see a proper lost-stream signal instead of an HTTP 403 handshake rejection. `ChatPanel.tsx` retries the same channel with exponential backoff, probes `/api/health` to tell "backend unavailable/restarting" apart from "stream itself was lost", and reloads the latest saved thread snapshot if the backend comes back after a restart but no longer has that live stream. The same area also suppresses repeated attachment chips for the same file path, and `ChatManager` maintains a per-request capability-result cache so exact duplicate tool calls plus already-covered `file_read` ranges can be reused instead of re-executed.
- `app.py` now enforces an outer stream-boundary invariant for chat turns: every `/api/chat/message` producer must emit one non-`progress_ack` terminal chat event before the queue closes. If an inner concierge/chat-manager event stream ends silently after tool or progress events, the app-level producer synthesizes a fallback `chat_complete` so reconnect logic can replay a terminal snapshot instead of surfacing a misleading “backend restarted or forgot this live response stream” error.
- `src/dan/server/chat_stream_buffer.py` now owns the in-memory buffering policy for chat/run stream channels. `ReconnectableChatStream` replaces the old plain `asyncio.Queue()` usage in `app.py`, bounds buffered events while no client is attached, coalesces repeated detached `chat_token` updates, preserves the current unsent event on disconnect, and can replay a terminal snapshot on reconnect if the socket race happens after the producer has already exited.
- Stream lifecycle rules are now centralized on the editor side in `editor/src/lib/chatStreamLifecycle.ts`. `ChatPanel.tsx` uses that helper for both primary chat sockets and `run-*` handoff streams, so reconnect policy, restart-aware disconnect messaging, and "is the last assistant bubble still actively streaming?" all follow the same rules. Clean terminal closes (`1000`/`1005`) and terminally missing channels (`4004`) are treated as non-retryable even if the browser also reported a transport error, which prevents reconnect loops against replay-only terminal snapshots. This also keeps the loading placeholder visible until assistant text arrives even if tool cards or run events have already started rendering.
- `editor/src/lib/chatThreadTitle.ts` now owns client-side title hygiene for the active header/history rows. It normalizes user edits, derives a cleaner draft title from the first prompt by stripping path-heavy noise and conversational wrappers, and lets `ChatPanel` refresh the active title against the server-authored thread title shortly after the first save so the header converges quickly even while the first turn is still streaming.
- Tool-call presentation now has its own frontend helper layer: `editor/src/lib/toolCallPresentation.ts` groups repeated same-turn `file_read` calls by path so `ChatMessage.tsx` can render one logical file-read entry with merged range summaries instead of one card per raw range read. `ToolCallCard.tsx` uses the Electron shell bridge to expose an open-file link for these grouped reads. `describeLatestToolProgress()` returns `ToolProgressInfo { text, filePath? }` so the progress line can link to the active file.
- Background streaming is managed by `editor/src/lib/backgroundStreamRegistry.ts`: a module-level `Map<threadId, BackgroundStream>` holds detached WebSockets that continue processing events and saving to the server while the user works in a different thread. `ChatPanel` detaches on thread switch instead of closing the WS. The registry notifies subscribers when streams complete so the active thread can auto-reload. Thread list rows show a pulsing dot for threads with active background streams. `shutdownAll()` closes every background WS and flushes saves — called from both `beforeunload` and the component's unmount cleanup.
- Message queueing: when a stream is active, new messages are pushed to a `pendingQueue` state array instead of being silently dropped. The composer stays enabled with a queue-specific placeholder. Each queued item can be edited (popped back to the input), reordered, or deleted via inline actions. When `isStreaming` transitions to false, the first queued message auto-sends after a short delay. The queue is cleared on thread switch or new chat creation.
- Mid-session message injection: `ChatManager` now maintains per-channel injection queues (`_injection_queues: dict[str, asyncio.Queue]`) alongside cancel events. `POST /api/chat/{channel}/inject` pushes a message; between tool rounds in `send_message_with_tools`'s multi-turn loop (after `_turn > 0`), pending injections are drained and appended to the LLM `messages` list as user messages. Each injection emits `ChatInjectedMessageEvent` on the WebSocket. The frontend's first queued item has a "Push" button that calls the inject API and inserts the acknowledged message into the conversation above the assistant bubble. The `stream_channel_id` is threaded through both the direct `send()` call and the concierge's `_extract_chat_params`.
- Graph CRUD responses now include canonical `graph_revision` values from the backend. The editor stores that revision in `useGraphStore` and reuses it for chat stale-revision checks instead of hashing the layout-adjusted `GET /api/graphs/{id}?layout=true` payload. When the graph is dirty locally, the editor intentionally falls back to a local canonical hash so stale detection still reflects unsaved edits instead of blindly sending the last saved server revision.
- Provider-layer timeout policy is now shared. `resolve_provider_timeout()` in `src/dan/providers/__init__.py` resolves a default timeout from `ProviderConfig.extra["timeout_seconds"]`, then `DAN_PROVIDER_TIMEOUT`, then `DAN_LLM_CALL_TIMEOUT`. The OpenAI and Anthropic chat providers propagate that timeout to their SDK requests by default, and the Google provider wraps both complete and stream paths in asyncio timeouts. Chat-specific timeout guards in `ChatManager` still exist as a second line of defense for progress/cancellation behavior, but the provider layer now protects other direct `provider.complete()` callers too.
- `src/dan/server/__main__.py` now builds the shared uvicorn log config for manual and service-managed server starts. It promotes `dan.*` loggers to `INFO`, so `dan-up`'s persistent `~/.dan/logs/server.log` and service log files capture `chat_manager` tool-loop diagnostics (`finish_reason`, turn summaries, follow-up failures) instead of only uvicorn process lines. `src/dan/cli/service_runner.py` reuses the same config for background service launches.
- `src/dan/server/chat_manager.py` now logs the post-tool follow-up lifecycle explicitly. Before the multi-turn loop asks the model for its after-tools answer, it records the prior tool set, any still-missing required actions, whether `file_write` escalation is active, the last downstream `stream_channel_id`, and the compacted context size. If that guarded completion exits without a `CompletionResult`, it logs the emitted step trace (`chat_complete`, `chat_complete:progress_ack`, `chat_interrupted`, etc.) before surfacing the generic fallback. This is the primary inner-boundary diagnostic for “tools succeeded, then the stream ended” failures.
- `src/dan/server/chat_manager.py` now also distinguishes **external task cancellation** from an **unexpected provider-side `asyncio.CancelledError`** inside `_iter_guarded_complete()`. If the current task is truly being cancelled or the stream cancel-event was set, cancellation still propagates. But if `provider.complete()` itself aborts without the outer task being cancelled, the manager logs the request shape and converts that into a normal runtime failure so the tool loop can emit a terminal response instead of silently terminating the whole chat stream.
- The guarded-completion heartbeat path now keeps the underlying `provider.complete()` task alive across progress timeouts. `emit_progress_ack=True` still emits a periodic `chat_complete(detected_mode="progress_ack")` heartbeat while waiting, but the timeout loop no longer cancels the still-running completion task after each heartbeat. This was the concrete root cause behind recent “disrupted” tool-heavy turns: the system was aborting its own post-tool follow-up completion after ~8 seconds, then only later reporting the resulting cancellation symptom.
- `src/dan/server/chat_manager.py` now normalizes token usage into both UI-friendly aliases (`prompt`, `completion`) and telemetry/cost-friendly aliases (`prompt_tokens`, `completion_tokens`, `total_tokens`, cache counters). That keeps `ChatPanel`, estimated-cost calculations, and concierge turn telemetry aligned even when a single answer spans multiple guarded `complete()` calls inside the tool loop.
- `src/dan/server/chat_manager.py` now preserves tool-emitted `stream_channel_id` values through every post-tool terminal `ChatCompleteEvent` path, including normal follow-up completions, continuation-failure exits, and forced partial synthesis at the tool-turn cap. That keeps the chat text and any downstream `run-*` event stream tied together until the client can hand off cleanly.
- `src/dan/server/chat_manager.py` now treats required-action hints (`read_file`, `search_web`, `write_file`) as satisfied only after successful tool completion. Failed tool attempts no longer clear the completion gate, and the loop refuses to finalize a required-action task when the last required step is still missing. When multiple required action paths remain, the manager keeps generic `tool_choice="required"`. When exactly one unmet action remains and it maps to one concrete tool (for example `write_file` → `file_write`) and the active provider supports exact tool targeting, the manager escalates to a function-specific `tool_choice` so the model cannot keep satisfying the gate with unrelated tools. Additional hardening now keeps write-oriented tasks from stalling in read-only loops: two consecutive `file_read` / `file_grep` / `list_directory` turns without a successful `file_write`, or an immediate missing-target read failure, force the next completion toward `file_write` with an explicit stop-reading-and-write instruction. Capability handlers also get one bounded internal retry only when they explicitly return `retryable=True`; ordinary tool failures are surfaced without blanket retries. Chunked `file_read` usage remains allowed; the guidance is to prefer targeted `start_line` / `end_line` reads over whole-file rereads rather than enforcing a fixed read-count cap.
- `src/dan/server/chat_manager.py` now sanitizes orphan `tool` messages inside `_compact_context()` before any early budget return. Valid assistant+tool bundles are still preserved atomically, but malformed transcripts no longer carry standalone tool-result messages forward into later provider calls where they would violate tool-calling transcript rules.
- `src/dan/server/telemetry.py` now persists adaptive-parameter telemetry end-to-end in SQLite. The `events` table includes `parameter_key` and `parameter_value`, `_INSERT_SQL` writes them, and `SQLiteTelemetryStore._init_db()` performs an `ALTER TABLE` migration for older local databases so `ParameterDecisionLogger` output is no longer silently dropped in production.
- `src/dan/server/chat_manager.py` now normalizes model names when resolving context windows (`claude-opus-4.6`, `gpt5.4`, `glm5`, `MiniMax-M2.5-highspeed`, etc.), so newer large-context families do not silently fall back to the 128K default. The tool-loop completion budget is also softer now: `_completion_max_tokens()` scales by effective context window with non-streaming-friendly soft caps of roughly 32K / 48K / 64K instead of the earlier flat ~16K ceiling.
- Chat Capability handlers include 13 tool-wrapping handlers (exposed via `DAN_FULL_TOOLS=1`), workflow introspection tools (`inspect_node`, `list_test_cases`), and `get_config`/`set_config`.
- Error retry UX: the `/retry` command resets transient errors and re-evaluates the last prompt.
- Tier policy: governed by `DAN_ENABLE_TIER_POLICY` and `DAN_TIER_MAP` to auto-assign models to tasks based on difficulty.
- Learning features: governed by `DAN_LEARNING_TIER` (0/1/2) with individual env var overrides (`DAN_PROMPT_OPTIMIZATION`, `DAN_MODEL_LEARNING`, `DAN_TOPOLOGY_LEARNING`, `DAN_SKILL_LEARNING`). Legacy `DAN_LEARNING_MODE=1` maps to tier 1.
- Post-response memory writes are fanned out in the background: episode/failure capture, preference extraction, and `MemoryExtractor.extract_with_llm()` for fact/preference/episode candidates. `DAN_MEMORY_EXTRACTION=1` enables the extraction path, and `DAN_MEMORY_EXTRACTION_LLM` now accepts either an enable flag (`1`/`true`) or a concrete model name; enabled LLM extraction falls back to heuristics if the model call is unavailable or fails.

### Learning & Evolution Optimization (31-15)

- **`learning_tiers.py`** — `resolve_learning_tier()` reads `DAN_LEARNING_TIER` (default 0, backward compat with `DAN_LEARNING_MODE=1`→tier 1). Three cumulative tiers: tier 0 (baseline: memory, post-run learning, reuse scoring, preference evolution), tier 1 (advisory: + topology suggestions, model recommendations, prompt variant proposals), tier 2 (active: + A/B prompt promotion, skill refinement, auto-adaptation). `is_feature_enabled(feature, tier)` checks tier-cumulative feature sets with individual env var overrides (`DAN_PROMPT_OPTIMIZATION`, `DAN_MODEL_LEARNING`, etc. — `"1"` force-enables, `"0"` force-disables regardless of tier). `features_enabled_at_tier()` returns the resolved set. `check_tier_promotion_gates()` validates health, precision, and false-positive criteria before manual tier 0→1 promotion. `LearningHealthCounters` tracks attempted/succeeded/skipped/failed per learning path with `record_failure()` for rate-limited warning (first failure → `logger.warning`, subsequent → `logger.debug`) and `format_status()` for `/status`. `MemoryBackend` protocol with `JsonFileBackend` (default) and `SqliteBackend` (optional, `DAN_MEMORY_BACKEND=sqlite`).
- **`correction_memory.py`** — `detect_correction()` heuristic detector (negation/override/style/redo/preference patterns, 3 confidence tiers), `route_correction()` producing preference/principle/negative_evidence actions, `CorrectionStore` in-memory record store. `/corrections` command lists recent correction events. Wired into `Concierge._process_inner()` to detect corrections after each user message; preferences and principles from corrections are stored in `MemoryKernel` for retrieval on similar future tasks.
- **`adaptation_registry.py`** — `AdaptationCandidate` model with lifecycle (pending→applied→rejected/rolled_back), `AdaptationRegistry` with approve/reject/rollback/`check_regression()` (>15% quality drop auto-rollback). `/adaptations` command shows pending and applied adaptations.
- **`behavior_store.py`** — `BehaviorStore` manages `~/.dan/behavior/` for externalizing DAN's own behavioral parameters (prompts, thresholds, taxonomy, domains, models) following the seed→override→learn→revert lifecycle. Stat-based hot-reload on `get()`, per-category `RLock`, max-10 version history for rollback. `AdaptableParameter` + `AdaptableParameterRegistry` provide declarative metadata (risk level, evidence type, min evidence count, bounds) per adaptable dimension. `BehaviorChangeLog` is an append-only JSONL audit trail. `ParameterDecisionLogger` wraps telemetry emission with parameter context. `PatternAccumulator` clusters unrecognized patterns by Jaccard keyword overlap. `ThresholdCalibrator` proposes bounded adjustments (±20% max step) gated by tier (0=observe, 1=propose, 2=auto-apply).
- **`planning_calibration.py`** — `DurationEstimator` (keyword-based quick/medium/complex classification), `FailureHotspotPredictor` (node-type failure rates), `ModelPreference` (node-type and task-pattern → model tier recommendation). Wired into `WorkflowPlanner.plan()` — calibration data injected as `plan_context["calibration_hints"]` and rendered in the LLM planning prompt under "Experience-Based Calibration".
- **`outcome_trackers.py`** — `NodeOutcome` model with per-node `quality_score` based on retry count (`1/(1+retries*0.3)` clamped [0.1,1.0]) and schema-valid-first-try flag. `compute_workflow_quality()` encodes partial success as `completed_nodes / total_nodes`. Quality signals wired into `PromptTracker`, `ModelOutcomeTracker`, `TopologyOutcomeTracker`. `PromptTracker` supports two scopes: `"workflow"` (default, keyed by node_id) and `"system"` (keyed by BehaviorStore prompt key). `record_system_prompt_outcome()` stores lightweight system-prompt effectiveness records; `get_system_prompt_history()` retrieves them.
- **`memory_kernel.py`** — `MemoryKernel` now maintains a `_type_index` (`dict[str, list[str]]` keyed by memory_type) to eliminate linear scans in `list_by_type` and `retrieve`. Accepts optional `backend: MemoryBackend` parameter for backend delegation. **Project-scoped memory (31-18):** `retrieve()` and `retrieve_by_task()` accept optional `project_id` — `PROJECT`-scoped items whose `metadata["project_id"]` doesn't match are filtered out, while matching items get a 0.3 score bonus. `USER`/`GLOBAL`/`SESSION`/`WORKFLOW` items pass unfiltered (like always-applied rules). `store_fact()`, `store_preference()`, `store_principle()` accept optional `project_id` — when provided, `scope` is set to `PROJECT` and `metadata["project_id"]` is populated automatically. The concierge threads the resolved project ID through all store/retrieve paths so memories activate by context, not manual tagging.
- **`learning.py`** — lightweight `/corrections` and `/adaptations` handlers; imports engine stores only for type checking so command registry/help wiring does not eagerly pull in heavy learning dependencies.

### Domain Learning (31-21)

- `src/dan/server/concierge/domain_learning.py` — domain detection, knowledge extraction, validation, template management
- `src/dan/data/domain_templates/` — seed JSON templates for 4 domains (paper_rendering, equity_research, data_analysis, literature_review)
- `src/dan/data/generation_profiles/` — seed domain generation profile JSON files for 5 domains (literature_review, paper_rendering, equity_research, data_analysis, code_generation); specifies preferred tools, patterns, model tier hints, default profile, and prompt guidance per domain
- Domain knowledge uses existing `MemoryType` values with `tags=["domain_knowledge"]` and `metadata={"domain": "...", "category": "..."}`
- `DomainReflector` runs post-task (triggered from `_finalize_task()`) to extract domain knowledge via LLM
- `DomainValidator` checks responses against domain rules (keyword-level, no LLM cost)
- `ContextPackage` model assembles domain expertise, artifacts, task state, and memory context for enriched prompts
- Gated behind `DAN_DOMAIN_LEARNING` and `DAN_DOMAIN_VALIDATION` env vars (tier 1 features)

### Self-Adaptive Behavior (31-22)

- **`behavior_store.py`** — `BehaviorStore` manages `~/.dan/behavior/` directory with stat-based hot-reload and per-category `RLock`. Seed defaults are returned without disk write; mutations create versioned JSON files. `BehaviorArtifact` tracks value, version, evidence, and up to 10 previous versions for rollback. `BehaviorChangeLog` is an append-only JSONL audit trail. `AdaptableParameter` + `AdaptableParameterRegistry` provide a declarative taxonomy of adaptable parameters with category, evidence type, min evidence count, risk level, and bounds. `ParameterDecisionLogger` wraps telemetry emission with parameter context. `PatternAccumulator` clusters unrecognized patterns by Jaccard keyword overlap. `ThresholdCalibrator` proposes threshold adjustments: tier 0 logs only, tier 1 proposes for user approval, tier 2 auto-applies within ±20% bounds.
- **`behavior_seeds.py`** — `register_all_seeds()` registers seed defaults for prompts (5 keys), heuristics (8 keys), memory ranking weights (7 keys), intent categories, domain keyword maps, model tier maps, and cost tables. Seeds follow the seed→override→learn→revert lifecycle.
- Prompt externalization: `PromptTracker` extended with `scope` parameter (`"workflow"` or `"system"`) and `record_system_prompt_outcome()` for system prompt effectiveness tracking.
- Threshold externalization: `reuse_decision.py` and `classifier.py` load thresholds from `BehaviorStore` with backward-compatible optional parameters and `ParameterDecisionLogger` wiring.
- Unified chat prompt guidance is registry-backed and section-based: `generate_capability_reference()` in `chat_manager.py` builds a compact live tool-family summary from `get_all_tools()` (instead of a stale hardcoded list), `_build_messages()` assembles the system prompt from explicit sections, and research-report guidance is injected only for research-like asks rather than every turn.
- Extended models: `TelemetryEvent` gains `parameter_key`/`parameter_value`; `AdaptationCandidate` gains `before_value`/`after_value`/`parameter_key`; `CorrectionRecord` gains `active_prompt_key`.
- Learning tiers: 4 new tier-0 observe features, 5 tier-1 advise features, 6 tier-2 auto-apply features with 8 env var overrides.
- Commands: `/changes` (behavioral change history), `/revert <id>` (rollback), `/behavior` (inspection with `--key`, `--seeds`, `--reset`).
- **AdaptationRegistry extensions**: `record_post_adaptation_outcome()` for post-adaptation quality tracking; `auto_apply_candidate()` for tier 2 auto-application; `_active_scopes` for max-1-active scope enforcement with `queued` field; `needs_measurement()` / `complete_measurement()` for measurement lifecycle; `_write_changelog()` wires approve/rollback/auto-apply to `BehaviorChangeLog`.
- **Safety invariants**: `SAFETY_INVARIANTS` dict + `check_safety_invariants()` + `validate_adaptation_safety()` in `learning_tiers.py`. Codifies max ±20% calibration step, append-only prompts at tier 1, additions-only taxonomy/domain, max 3 keyword expansion, measurement window bounds.
- **Tier 1→2 promotion**: `check_tier_1_to_2_promotion_gates()` with 4 gates (health, approved proposals, false positives, evidence breadth).
- **Intent discovery**: `propose_intent_discoveries()`, `apply_new_intent()`, `evolve_classifier_prompt()` in `classifier.py` — tier-gated intent emergence from `PatternAccumulator` clusters.
- **Domain discovery**: `propose_domain_discoveries()`, `apply_new_domain()`, `propose_keyword_expansion()` in `domain_learning.py` — tier-gated domain emergence and marginal-match keyword expansion.
- **Prompt self-tuning**: `PromptTracker.propose_prompt_variant()` (tier-gated proposals after N negative signals), `check_prompt_regression()` (failure rate regression detection with configurable threshold).

### Goal-Oriented Loop (31-6)

- **`goal_loop.py`** — `GoalSpec`, `EvaluationResult`, `AttemptRecord`, `GoalLoopState` models; `ComparisonOp` type alias; `is_target_met()` / `is_better()` comparison helpers; `Evaluator` protocol with `ScriptEvaluator`, `LLMJudgeEvaluator`, `TestSuiteEvaluator`, `CustomEvaluator`; `GoalLoopExecutor` (attempt→evaluate→best-so-far→escalate loop with wall-clock deadline); `/goal`, `/goal-status`, `/goal-stop` command handlers; JSON state serialization. `GoalLoopState` now optionally carries a normalized `goal_contract`, and `get_tier_prompt()` injects it when present so attempt escalation still stays aligned with the original goal/deliverable/completion contract.
- **Engine-level `GoalLoopNode`** (31-6 task 5) — `src/dan/models/control_flow.py` defines `GoalLoopNode` with `goal_text`, `metric_name`, `target_value`, `comparison` (all ComparisonOp values), `max_iterations`, `evaluator`, `success_criteria`, `body_graph`, plus the full composite-node contract. Registered in `NodeTypeRegistry` (`src/dan/registry.py`) and `ExecutorRegistry` (`src/dan/engine/scheduler.py`). `GoalLoopExecutor` in `src/dan/executors/control_flow.py` iterates a body sub-graph, extracts the metric from output, tracks best-so-far, and emits `iteration_started`/`iteration_completed` events. Builder DSL: `wf.goal_loop("id", goal_text=..., metric_name=..., target_value=..., comparison=..., body=...)` as a context manager. Markdown loader: `type: goal_loop` with frontmatter fields (`goal_text`, `metric_name`, `target_value`, `comparison`, `max_iterations`, `evaluator`, `success_criteria`). Compiler: builder `compiler.py` maps `goal_loop` in `_build_node()` and `DEFAULT_OUTPUT_PORTS`; loader `compiler.py` maps `agent_type="goal_loop"` in `_compile_agent()`.

### Plan Dependency Optimization (31-8)

- **`plan_scheduler.py`** — Core RCPSP scheduler with dual callers (concierge + workflow engine). `PlanTask` (with `cancelled_for_dependency` field), `PlanDAG`, `PlanSchedule`, `PlanConstraints` models. Critical path solver (forward+backward pass), exact RCPSP via OR-Tools CP-SAT (when available and n <= threshold), LRP heuristic fallback. Dynamic rescheduling: `on_task_complete()` with duration calibration, `insert_task()`, `infer_dependencies()` (artifact-contract matching + transitive reduction), `detect_conflicts()`. Pre-flight validation: `validate_preflight()` checks required inputs against completed predecessors. Mid-execution dependency: `handle_mid_execution_dependency()` cancels, adds edge, flags for re-run. Concierge path: `execute_plan_tasks()` dispatches via concierge with semaphore-based parallelism, `format_schedule_display()` (Gantt-like text with critical path markers), `format_progress_update()`, `handle_plan_command()` (`/plan [--replan]`). Workflow path: `dag_from_graph()` builds acyclic PlanDAG from Graph (Tarjan SCC condensation), `build_branch_dag()` for ParallelSubagentsExecutor branches, `apply_resource_budget()` clamps parallelism to `DAN_MAX_CONCURRENT_LLM`. Event models: `PlanTaskStarted`, `PlanTaskCompleted`, `PlanRescheduled`, `PlanDependencyDiscovered` with critical path, makespan estimate, parallelism utilization, calibration factor payloads.
- **`plan_prompts.py`** — LLM planning prompts and domain templates. `DECOMPOSITION_PROMPT` (goal → JSON task array, biased toward independence), `FEW_SHOT_EXAMPLES` (5 golden decompositions: research report, code refactor, data pipeline, Kaggle competition, equity analysis), `VALIDATION_PROMPT` (DAG review for missing/redundant edges, parallelism opportunities, estimate concerns). `estimate_from_experience()` queries an experience store for similar past tasks. `DEPENDENCY_TEMPLATES` (5 domain patterns: research_report, code_refactor, data_pipeline, ml_experiment, equity_analysis), `apply_template_deps()` overlays template edges with fuzzy name matching and cycle prevention. `predict_deps_from_experience()` learns dependency patterns from past DAGs.

### Scheduled Tasks (31-7)

- **`scheduler.py`** — `TriggerContext`, `DeliveryTarget`, `ScheduleEntry`, `ScheduleRunRecord` models; `parse_trigger()` (human-readable → cron: `every 6h`, `daily at 9am`, `weekdays at 8:30am`, pass-through standard cron); `compute_next_run()` via `croniter` (optional dep with fallback); `ScheduleStore` (filesystem CRUD at `~/.dan/schedules.json`, atomic save, case-insensitive name lookup); `ScheduleHistoryStore` (`~/.dan/schedule_history.json`, 20-record cap per schedule); `TaskScheduler` (asyncio background loop, 30s poll, fire-and-forget dispatch, missed-run detection with `on_missed`); `/schedule add|list|remove|pause|resume|history` command handler. `ProjectContextResolver` and `ProjectStore` now honor `trigger_context.project_id` across surface/external-id boundaries via project-id fallback lookup so project-only scheduled dispatches resume and persist against the intended task instead of creating a fresh project or failing on write-back.

### Progressive Response UX (31-14)

- **`progress_ux.py`** — `ProgressPhase`, `CheckpointOption`, `CheckpointOptions`, `InteractionRequest` Pydantic v2 models; `VerbosityLevel` type alias (`full`/`compact`/`minimal`); `ProgressRenderer` protocol (6 methods: `announce_plan`, `phase_update`, `phase_complete`, `checkpoint`, `deliver_result`, `heartbeat`); `ProgressSession` (phase lifecycle, configurable throttling, elapsed tracking); four surface renderers — `CLIProgressRenderer` (text), `TelegramProgressRenderer` (edit-in-place, 4096-char limit), `WhatsAppProgressRenderer` (bookend: 1 start + 1 end), `EditorProgressRenderer` (streaming sections); `resolve_verbosity()` (auto per surface + `DAN_PROGRESS_VERBOSITY` env override); `generate_preflight_questions()` (`DAN_PREFLIGHT_CLARIFY`, `DAN_PREFLIGHT_THRESHOLD_SECONDS`); `/progress` command handler. User overrides are stored per requesting `external_id`, not as a single process-global setting, so concurrent surfaces can keep independent verbosity preferences.
- `progress_ack` is a compatibility special case: it still travels as `ChatCompleteEvent(detected_mode="progress_ack")`, but consumers must not treat it as terminal. Stream readers should continue until a non-`progress_ack` `chat_complete`, `chat_interrupted`, or `chat_error`. Messaging surfaces that cannot edit in place should prefer suppressing or softening standalone `progress_ack` text instead of presenting it as the final answer.
- **Progress ownership**: the concierge reassurance timer (timeout-based "Working on it..." messages) is **disabled for messaging surfaces** (telegram, whatsapp, email). Those surfaces own their own progress UX in the adapter layer. The Telegram fleet's `_stream_with_edits` has its own timer (`_PROGRESS_INITIAL_DELAY` = 10s, `_PROGRESS_REPEAT_INTERVAL` = 20s) that only fires when the server stream is genuinely slow; fast replies never produce a progress bubble. Queue wait hints (from `_iter_chat_stream_events`) also live in the adapter and include elapsed time + queue position. The CLI/editor path still uses the concierge reassurance timer since those surfaces don't have an adapter-level progress renderer. This separation keeps the concierge surface-agnostic: it classifies, resolves context, and streams events; the adapter decides when/how to show interim progress.
- **Queue identity**: the server dispatcher serializes by `project_id` — same project messages queue serially, different projects run concurrently. The `task_id` stays contextual inside the resolved context, not part of the queue key. If intra-project parallelism is ever needed, the queue key can be extended to `project_id:task_id` as an explicit opt-in without restructuring the dispatcher.

### Request Guard Pipeline (31-19)

The message lifecycle gains inter-step guards and entity awareness. The pipeline (classify → resolve → execute → post-process) now has validation between steps via cascading `GuardContext`. Industry consensus (CrewAI task guardrails, Google ADK 6-point callbacks, LangGraph Reflexion, DeerFlow plan-and-reflect) is that production agent systems need explicit guard hooks between pipeline stages.

Pipeline with guards:

```
fast_command → entity_grounding → classify → GUARD 1 → resolve → GUARD 2 → execute → GUARD 3 → deliver
```

- `entity_grounding.py` — Request guard pipeline (31-19): `EntityContext`, `GuardContext`, `GuardResult` dataclasses. `ground_entities()` scans messages against ProjectStore. Three guards: `guard_classification()` (coherence + short-circuit), `guard_understanding()` (assumption grounding + goal fidelity), `guard_response_relevance()` (topic match + entity coherence). Env vars: `DAN_GUARD_PIPELINE`, `DAN_GUARD_CLASSIFICATION`, `DAN_GUARD_UNDERSTANDING`, `DAN_GUARD_RELEVANCE`.
- **Entity grounding** — `ground_entities()` scans the user message against `ProjectStore` to produce an `EntityContext` (matched projects, tasks, `is_about_project` flag). Injected into classification, solver context, and system prompt so the LLM knows which entities are local DAN objects vs external topics.
- **Guard 1 (classification coherence)** — if entity context shows "this is a question about a known project" but classifier said `direct_task`, short-circuit to the `/project info` response. If confidence < 0.6 and entity matches exist, ask for clarification.
- **Guard 2 (understanding coherence)** — after solver returns `SolverDecision`, validate that assumptions are grounded in user text and entity context. If the solver's goal doesn't paraphrase the user's request, halt and ask.
- **Guard 3 (response relevance)** — extends existing completion guard with topic-match and entity-coherence checks. Advisory: appends clarification suggestion rather than blocking.

Guards are heuristic (not LLM-heavy), on by default (`DAN_GUARD_PIPELINE=0` to disable), and each guard sees cumulative context from all prior stages.

### Unified Telemetry (31-20)

Single `TelemetryEvent` model + SQLite-backed `TelemetryStore` (`src/dan/server/telemetry.py`) captures every LLM interaction with timing, token counts, cost, and project/surface/model scope. Event types: `chat_turn`, `fast_command`, `workflow_run`, `workflow_node`, `guard_check`, `classification`, `memory_retrieval`, `tool_call`. Three store backends: `SQLiteTelemetryStore` (default, WAL mode), `InMemoryTelemetryStore` (tests), `NullTelemetryStore` (disabled). Emission sites: `Concierge.process()` (chat turns), `RunManager._enrich_and_persist()` (workflow runs/nodes), guard functions, `classify_intent_llm()`, `MemoryKernel.retrieve()`. `/analytics` command provides aggregated reports with `--by model|surface|day|intent`, `--since Nd`, project drill-down, and JSONL/CSV export. Lifecycle: created in `app.py` lifespan and `chat_factory.py`, passed to `Concierge` and `RunManager`. Env: `DAN_TELEMETRY`, `DAN_TELEMETRY_DB`, `DAN_TELEMETRY_RETENTION_DAYS`.

### Solver Runtime Layer (25-8 through 25-11)

The solver sits above the concierge foundation and changes the top-level control flow from classifier-first to goal-first:

- **`solver.py`** — `GoalResolver` (fast-path + LLM + heuristic), `PlanBuilder`, `SolverDecision` model, `ExecutionMode` (10 modes), `TerminalOutcome`
- **`memory_bridge.py`** — `WorkflowMemoryIndex` wrapping `ExperienceStore`/`ExperienceIndex` for semantic retrieval, reuse scoring, duplicate detection
- **`executor.py`** — `ExecutionSelector` mapping solver decisions to handler backends, fallback execution, apology detection, partial-result formatting
- **`policy.py` additions** — `validate_terminal_content()` for the solver's terminal-content guardrails

Flow: `classify_intent()` → fast-path check → `GoalResolver.resolve()` → `PlanBuilder.build_plan()` → `ExecutionSelector.execute()` → handler backend → terminal outcome validation. The solver path is optional; when `goal_resolver` is `None`, the old handler-dispatch path runs unchanged.

### Tool-Aware Conversation (25-12)

- `web_search` registered as a capability tool for `conversation` mode
- `ConversationHandler` uses `send_message_with_tools` instead of text-only `send_message`
- `DirectTaskHandler` fallback uses `conversation` mode for tool access
- Solver `_SOLVER_SYSTEM_PROMPT` routes live-data queries to `direct_action`
- `_check_unsourced_claims()` appends training-data disclaimer on unsourced numeric patterns
- `completion_guard.py` provides pre-delivery requirement-level completeness validation: `RequirementExtractor` (heuristic-first with LLM fallback) parses user messages into structured `Requirement` items, `CompletionChecker` validates response coverage via keyword matching (with suffix normalization) and optional LLM verification for `must`-priority misses, `augment_response()` appends notes or returns follow-up messages for missed items with anti-loop protection. Configurable via `DAN_COMPLETION_CHECK` (default on) and `DAN_COMPLETION_CHECK_THRESHOLD` (default 2). `/completion` command shows persistent stats.
- `resume.py` provides cross-session task resume (31-11): `TaskSnapshot` model for lightweight resume summaries; `snapshot_from_task()` builder; `ResumeProtocol` with `check_resumable_tasks()` (scans all surfaces for active/paused/blocked tasks within 7 days), `generate_resume_prompt()`, `auto_resume_match()` (intent detection + task-name keyword overlap with ambiguity guard), `update_task_state()`; `persist_task_state()` for cross-surface task persistence; `extract_structured_state()` for heuristic step extraction from conversation text; `/resume [task_name]` command handler. `Task` model extended with `completed_steps`, `pending_steps`, `current_blocker`, `artifacts`, `last_activity` fields (backward-compatible defaults).
- `follow_up.py` provides proactive follow-up (31-12): `FollowUpTrigger` and `FollowUpConfig` Pydantic v2 models; `FollowUpQueue` — in-memory priority queue with hash-based deduplication (source + project_id + message prefix), priority ordering, expiry filtering; trigger factories `create_run_completion_trigger()`, `create_stale_task_trigger()` (uses `TaskSnapshot` from 31-11), `create_schedule_result_trigger()`; `FollowUpDeliveryEngine` — async delivery with quiet-hours gating (`DAN_QUIET_HOURS`), sliding-window rate limiting (`DAN_FOLLOW_UP_MAX_PER_HOUR`, default 3), background loop (60s); `scan_stale_tasks()` for finding paused/blocked tasks beyond `DAN_STALE_TASK_HOURS` (default 24); `load_follow_up_config()` reads `DAN_PROACTIVE_FOLLOW_UP` (default 0, opt-in); `/follow-ups [on|off]` command handler.
- `continuity.py` provides multi-surface continuity (31-13): `SurfaceRoutingPolicy` (per-project visibility/context/follow-up policy), `SurfacePresence`, `ConversationTurn`, `CrossSurfaceContext` models; `ProjectConversationStore` view over `ProjectStore` aggregating task turns across surfaces into a unified timeline; `detect_surface_switch()` heuristic handoff detection via keyword overlap with projects on other surfaces; `generate_handoff_context()` builds `CrossSurfaceContext` payload (TaskSnapshot + recent turns + summary); `PresenceTracker` in-memory surface activity tracker; `route_message_to_surface()` for DAN-initiated message delivery routing (private-preferred, group blocked without opt-in); `/sync [--allow-group]` command handler.

### Computer Control & Browser Automation (31-17)

- **`computer_policy.py`** — `ComputerControlConfig` (load from `~/.dan/computer_control.json` + `DAN_COMPUTER_CONTROL` env override), `ChunkPolicies` (6 capability chunks: observe/browser/input/window/files/system each with `ChunkPolicy`), `BrowserDomainRule` (pattern + subdomain + redirect + download flags), `SessionOverride` (temporary per-session approvals with expiry), `ActionType` classification (5 levels: read_only/benign_input/sensitive_input/destructive/system_level), `classify_action()` with destructive-target escalation regex, `requires_approval()` with session override support, `is_domain_allowed()` (subdomain-aware URL matching), `is_app_allowed()` (case-insensitive app matching), `AuditEntry` + `AuditLog` (in-memory audit with `add`/`recent`/`format_summary`), `VisionExportPolicy` (enabled/require_pii_protection/require_redaction — disabled by default, blocks screenshot export to external LLMs unless PII protection active), `check_vision_export()`, `FileSafetyPolicy` (allowed_download_dirs, allowed_upload_roots, overwrite confirmation, auto-open, TTL cleanup for screenshots/temp crops/downloads).
- **`computer_use.py`** — `ObservedElement` and `UIObservation` Pydantic v2 perception models (browser/desktop surface type, screenshot path, OCR text, elements list), `ComputerUseLeaseManager` (async single-session guard: acquire/release/reentrant, read-only observation always allowed), `ComputerUseController` (high-level observe→act→verify runtime, browser/desktop dispatch, policy enforcement, lease acquisition, audit logging, progress phase emissions via `ProgressSession`, approval request creation via `InteractionRequest` from 31-14, `_resolve_perception_method()` for deterministic perception ordering: DOM selectors → AX tree → local OCR → vision model fallback), `handle_computer_command()` for `/computer status|doctor|approve` (registered via 31-16 `CommandDescriptor`).
- **`browser_control.py`** (in `dan.tools`) — `BrowserController` protocol (12 methods: open, click, type_text, fill, select, wait_for, extract_text, screenshot, download, list_tabs, switch_tab, close), `PlaywrightBrowserController` (lazy browser launch, domain allowlist enforcement via `_check_domain`, `BrowserSessionContext` per-task state, screenshot dir with cleanup, **persistent profile support** via `profile` kwarg → `launch_persistent_context(user_data_dir)` for login session reuse across runs, `DAN_BROWSER_PROFILE` env var sets profile name, `~/.dan/browser-profiles/<name>/` storage), `MockBrowserController` (action recording with configurable responses).
- **`desktop_control.py`** (in `dan.tools`) — `DesktopController` protocol (9 methods: screenshot, ocr, list_windows, focus_window, click, type_text, hotkey, clipboard_read, clipboard_write), `MacOSDesktopController` (screencapture, pbcopy/pbpaste, AppleScript window/keyboard control, platform guard), `WindowsDesktopController` (stub — raises `NotImplementedError`, follow-on), `LinuxDesktopController` (stub — raises `NotImplementedError`, follow-on), `detect_platform()` factory (returns appropriate controller for current OS), `MockDesktopController` (action recording), `PermissionStatus` model, `check_macos_permissions()` (screen recording + accessibility + apple events detection).

## Directory Structure

```
deep-agent-network/
  docs/                          # All project tracking and documentation
    development-plan.md          # Vision, roadmap, research landscape
    architecture.md              # This file — tech stack, conventions, decisions
    changelog.md                 # Append-only log of completed work
    todo.md                      # High-level task list, links to plan files
    bugs.md                      # Known issues and failed approaches
    llm-api-guide.md             # LLM-facing API reference (auto-updated on API changes)
    plans/                       # Numbered detailed plans (just-in-time); 11 = Phase 7.1 structure review
  src/dan/                       # Python package
    __init__.py                  # Top-level package exports
    models/                      # Phase 0 — formal type system
      ports.py                   # InputPort, OutputPort
      context.py                 # NodeLocalState, SharedContextDeclaration, ArtifactRef, ContextProjection, FeedbackSelector, CompactionRule, policies
      nodes.py                   # NodeBase, LLMOperator, ToolOperator, CodeOperator, ReflectionNode
      control_flow.py            # GateNode (unified if_else/while), IfElse, WhileLoop, ForEach, ParallelSubagentsNode, OrchestratorNode, Reduce, Router, HumanInTheLoop, ValidatorNode, CompositeNode
      edges.py                   # DataEdge, ControlEdge, ContextEdge
      graph.py                   # Graph container, Node/Edge discriminated unions, dan_graph_v1 contract
    validation/
      schema.py                  # Port schema compatibility (MVP structural check)
      graph.py                   # Graph well-formedness validation
      boundaries.py              # Boundary auto-insert: generate entry/exit ValidatorNodes for composite nodes
    migration/
      gate_migration.py          # Legacy IfElse/WhileLoop → GateNode graph-dict migration helpers
    registry.py                  # NodeTypeRegistry — maps node_type strings to classes
    providers/                   # Phase 4 — multi-provider LLM abstraction
      __init__.py                # LLMProvider protocol, CompletionResult, StreamChunk, ProviderConfig
      openai_provider.py         # OpenAIProvider — wraps AsyncOpenAI (any OpenAI-compatible endpoint)
      anthropic_provider.py      # AnthropicProvider — wraps AsyncAnthropic (optional dep)
      google_provider.py         # GoogleProvider — wraps google.generativeai (optional dep)
      registry.py                # ProviderRegistry — model→provider routing (override→prefix→default)
      costs.py                   # Static COST_PER_1K_TOKENS table + estimate_cost()
      tier_defaults.py             # DEFAULT_TIER_MAPS, DEFAULT_TIER_PARAMS, resolve_tier_map/resolve_tier_params
      tier_scorer.py               # DifficultyScorer, ImpactScorer, RecoverabilityScorer, TierScorer, TierResult
    mcp_bridge.py                # Phase 19 (29-9) — MCP client bridge: consume external MCP servers as tools
    tools/                       # Phase 4 — built-in tool library (dan.tools)
      __init__.py                # get_all_tools() auto-discovery, get_preflight_tools() hook discovery
      _workspace.py              # Workspace root sandboxing utility
      _git_helpers.py            # Shared _find_repo, _run_git for git tools
      _browser_session.py        # Shared browser singleton for workflow tools (DAN_BROWSER_PROFILE, DAN_BROWSER_HEADLESS)
      browser_control.py         # Phase 21 (31-17) — BrowserController protocol, PlaywrightBrowserController (persistent profile, domain allowlist, session context), MockBrowserController
      browser_open.py            # Open URL in persistent browser session
      browser_click.py           # Click element by CSS selector
      browser_type.py            # Type text into element (append)
      browser_fill.py            # Clear + fill element
      browser_extract.py         # Extract text from page/element
      browser_screenshot.py      # Take page screenshot
      browser_wait.py            # Wait for element or network idle
      file_read.py               # Read file with line range, size guard
      file_write.py              # Write/append with parent dir creation
      list_directory.py          # List with glob and recursive mode
      web_search.py              # DuckDuckGo search (optional dep)
      web_fetch.py               # URL content fetch via httpx
      desktop_control.py         # Phase 21 (31-17) — DesktopController protocol, MacOSDesktopController, WindowsDesktopController (stub), LinuxDesktopController (stub), detect_platform() factory, MockDesktopController, check_macos_permissions()
      http_request.py            # General HTTP client
      shell_command.py            # Subprocess with timeout and allowlist
      pdf_read.py                # PDF text extraction (optional dep)
      text_chunk.py              # Text chunking with overlap
      json_extract.py            # Dot-notation JSON extraction
      regex_match.py             # Regex match/replace
    sandbox/                     # Phase 6 — subprocess sandbox (operational guardrails)
      __init__.py                # SandboxConfig (Pydantic), SandboxResult (dataclass), defaults
      adapters.py                # LanguageAdapter protocol, PythonAdapter, ShellAdapter, ADAPTERS registry
      runner.py                  # SandboxRunner — subprocess exec with timeout, memory limits, env filtering, output truncation
    engine/                      # Phase 1 — async execution engine
      __init__.py                # Public API: Engine, EngineConfig, RunResult, etc.
      state.py                   # NodeStatus, PortDataStore, ExecutionState
      context_runtime.py         # SharedContextStore, ArtifactStore, LocalStateManager, ScopedContextView; resolve_reference/create_reference for pass_by_reference (18-1)
      executor.py                # EngineConfig, NodeExecutor protocol, ExecutionContext, ExecutorRegistry
      conditions.py              # Safe expression evaluator for IfElse/WhileLoop conditions
      normalizer.py              # OutputNormalizer — JSON extraction, schema validation, re-prompt
      checkpoint.py              # CheckpointStore protocol, FileSystemCheckpointStore
      events.py                  # EngineEvent, EventType — typed runtime events
      memory.py                  # Phase 9A — MemoryEntry, MemoryScope, WriteMode, MemoryWriteRequest models
      memory_store.py            # Phase 9A — MemoryStore protocol, FileSystemMemoryStore (atomic JSON, index sidecar)
      memory_pipeline.py         # Phase 9A — ShortTermMemory buffer, CompactionStrategy activation, ConsolidationPipeline
      error_memory.py            # Phase 9D — ErrorRecord, ErrorCategory, extract_error_records(), ErrorMemoryIndex (RAG), CausalPrinciple, PrincipleStore, ErrorContextProvider
      rule_generator.py          # Phase 9D — RuleGenerator (principle→hyperedge), GeneratedRule, ParameterMutation, RuleLifecycleManager (filesystem-backed lifecycle, TTL, pruning, mutations)
      experience.py              # Phase 11 (19-1) — WorkflowExperience, ExperienceStore, ExperienceIndex, extract_experience_from_graph(), consolidate_experience()
      state_store.py             # Phase 10 (18-3) — StateStore protocol, FileSystemStateStore (atomic JSON), NullStateStore; typed schemas (LoopIterationState, TeamTurnState, NodeExecutionSummary)
      token_optimization.py      # Phase 10 (18-1/18-3/18-4) — SummarizationConfig, PromptAnalyzer, ContextSelector, PayloadPruner, ToolSchemaResolver, ContextToolProvider, HistoryManager, LoopCompactor, TokenBudgetAdvisor, TokenWasteAnalyzer, WasteFinding, TokenOptimizationReport, OptimizationPlaybook, PlaybookEntry
      cache.py                   # Phase 10 (18-2) — NodeResultCache (LRU+TTL+disk) and SemanticCache (EmbeddingRegistry + VectorStore)
      user_profile.py            # Phase 16 (26-3) — UserProfile Pydantic model, RecentWorkflow, load/save to ~/.dan/profile.json, format_recent_workflows()
      preference_extractor.py    # Phase 16 (26-3) — PreferenceExtractor heuristic extraction (model preferences, domains, output format) from conversation history
      memory_extractor.py        # Phase 29-6 — MemoryExtractor heuristic extraction (facts, preferences, episodes) from user/assistant interactions; complements PreferenceExtractor
      pattern_extractor.py       # Phase 29-6 §4 — PatternExtractor: structural pattern extraction from workflow graphs; runs inside run_consolidation() to discover recurring sub-structures as WORKFLOW_PATTERN items
      outcome_trackers.py        # Phase 29-6 §7/§8/§10 — PromptTracker (prompt/outcome pairs), ModelOutcomeTracker + ModelRecommender (per-node model selection learning), TopologyOutcomeTracker + TopologyAdvisor (structural pattern correlation); all opt-in via env vars
      conversation_memory.py     # Phase 16 (26-3) — ConversationMemoryStore, ConversationSummary, cross-session keyword search, context block formatting
      scheduler.py               # Topological sort (DAG fast-path + cycle-aware for gate loops), parallel dispatch, Engine.run()/resume(), event emission
      plan_scheduler.py          # Phase 21 (31-8) — RCPSP plan scheduler: PlanTask/PlanDAG/PlanSchedule models, compute_critical_path(), schedule_tasks() (OR-Tools exact + LRP heuristic), on_task_complete() dynamic rescheduling with calibration, infer_dependencies() artifact-contract matching + transitive reduction, detect_conflicts(), validate_preflight() pre-flight input validation, handle_mid_execution_dependency() cancel-and-reschedule, execute_plan_tasks() async concierge dispatch, format_schedule_display() Gantt text, format_progress_update(), handle_plan_command() /plan handler, dag_from_graph() workflow→DAG with SCC condensation, build_branch_dag(), apply_resource_budget(), PlanTaskStarted/PlanTaskCompleted/PlanRescheduled/PlanDependencyDiscovered event models
      plan_prompts.py            # Phase 21 (31-8) — DECOMPOSITION_PROMPT, FEW_SHOT_EXAMPLES (5 golden decompositions), VALIDATION_PROMPT, estimate_from_experience(), DEPENDENCY_TEMPLATES (5 domain patterns), apply_template_deps(), predict_deps_from_experience()
      correction_memory.py       # Phase 21 (31-15) — CorrectionSignal, detect_correction() heuristic detector, route_correction(), CorrectionStore
      adaptation_registry.py     # Phase 21 (31-15) — AdaptationCandidate lifecycle model, AdaptationRegistry (add/approve/reject/rollback/regression check)
      behavior_store.py          # Phase 21 (31-22) — BehaviorStore (~/.dan/behavior/, stat-based hot-reload, per-category RLock), BehaviorArtifact, AdaptableParameter/Registry, BehaviorChangeLog (JSONL), ParameterDecisionLogger, PatternAccumulator, ThresholdCalibrator
      behavior_seeds.py          # Phase 21 (31-22) — register_seed_prompts(), register_seed_heuristics(), register_seed_execution() (32-7), register_memory_ranking_params(), register_all_seeds(); seed prompt/heuristic/execution/memory-ranking defaults with AdaptableParameter metadata
      planning_calibration.py    # Phase 21 (31-15) — DurationEstimator, FailureHotspotPredictor, ModelPreference (planning-time calibration from experience)
      learning_tiers.py          # Phase 21 (31-15) — resolve_learning_tier(), is_feature_enabled(), LearningHealthCounters, MemoryBackend protocol, JsonFileBackend, SqliteBackend
    rag/                         # Phase 6 — RAG / knowledge retrieval subsystem
      __init__.py                # EmbeddingProvider protocol, EmbeddingResult, OpenAI/Local providers, EmbeddingRegistry
      indexer.py                 # Indexer — create/populate/manage vector store indexes with chunking + batch embedding
      stores/
        __init__.py              # VectorStore protocol, DocumentRecord, QueryResult, VectorStoreConfig, VectorStoreFactory
        memory.py                # MemoryVectorStore — pure-Python stdlib-only (cosine sim via math), O(n) scan
        faiss_store.py           # FAISSVectorStore — faiss.IndexFlatIP, L2-normalized inner product, persistence, metadata sidecar
        chroma_store.py          # ChromaVectorStore — chromadb.PersistentClient, native metadata filtering
    utils/                       # Phase 9A — shared utilities
      tokens.py                  # estimate_tokens() — tiktoken-backed or character approximation
      workflow_interface.py      # Phase 12 (21-5) — WorkflowInterface model, derive_workflow_interface() for input/output schema extraction
    client/                      # Phase 13 (23-2) — shared thin client library for dan-serve gateway
      __init__.py                # Public exports: DanClient, DanClientOrLocal, error types, response models
      client.py                  # DanClient — async httpx/websockets client (dispatch, runs, events, HumanNode, activity)
      local.py                   # DanClientOrLocal — transparent server/local wrapper (server via DanClient, fallback to direct Engine)
      errors.py                  # DanClientError hierarchy: ConnectionError, DispatchError, NotFoundError, ServerError, RunLostError
      models.py                  # Client-side Pydantic models: DispatchResult, RunSummary, PendingInput, ActivitySnapshot, CancelResult
    adapters/                    # Phase 12 (21-4) — messaging adapter framework
      __init__.py                # Public exports: adapters, configs, renderer, session store
      base.py                    # MessagingAdapter protocol, AdapterConfig, MessagingHumanRenderer, AdapterSessionStore, SessionState, trigger/parse helpers
      email_adapter.py           # EmailAdapter — IMAP receive (asyncio.to_thread), aiosmtplib send, thread tracking
      telegram_adapter.py        # TelegramAdapter — python-telegram-bot, inline keyboards, /start /status /cancel, message splitting
      whatsapp_adapter.py        # WhatsAppAdapter — WhatsApp Cloud API (httpx), FastAPI webhook, interactive messages, signature verification
    meta/                        # Phase 11 — meta-orchestrator
      __init__.py
      discovery.py               # DiscoveryService — enumerates tools, skills, patterns, past workflows (ToolInfo, SkillInfo, PatternInfo, WorkflowMatch, DiscoveryResult)
      goal_contract.py           # Shared bounded goal-contract normalization/rendering helpers for planner, intent extraction, and orchestration prompts
      planner.py                 # WorkflowPlanner — LLM-driven reuse-first planning (PlanningPromptBuilder, ReusePlan, AdaptPlan, GeneratePlan, PlanResult, PlanReview, PlannerOutput)
      repair.py                  # Structural Repair Engine — RepairLevel, RepairClassifier, ParameterRepairGenerator, StructuralRepairPlanner, RedesignTrigger, RepairEscalator, RepairActionStore/Record
      controller.py              # Autonomous Execution Controller — MetaSession, MetaSessionStore, MetaController, MetaControllerConfig, HumanOverride
      utils.py                   # Shared utilities extracted from MetaController (29-2 §5-1) — plan_from_dict, topo_sort_workflows, create_meta_session, goal_to_session_fields, validate_session_resumable, session_is_terminal
      self_knowledge.py          # Phase 11 (19-5) — SelfKnowledgeIndex, RetrievedChunk; indexes DAN's own docs for planner grounding
      authoring.py               # Phase 11 (19-6) — RuntimeAuthor, ToolSpec, SkillSpec; dynamic tool/skill generation, sandbox testing, registration, persistence
      architect.py               # Phase 11 (19-7) — SystemArchitect, SystemPlan, WorkflowSpec, RoutingConfig, SystemManifest; multi-workflow system decomposition
      intent_schema.py           # Phase 24-2 — WorkflowIntent, StageIntent, StageType Pydantic models; structured intent for deterministic compilation
      intent_compiler.py         # Phase 24-2 — IntentCompiler (WorkflowIntent → builder DSL code), CoverageChecker, CoverageResult; deterministic fast path for common workflow shapes
      intent_extraction.py      # Phase 24-2 — Intent extraction prompt, tool schema, few-shot examples for LLM function-calling
      tool_catalog.py            # Phase 33-10 — shared live tool catalog helpers for codegen/extraction prompts
      config.py                  # Phase 32-7 — Direct execution config: is_direct_build_enabled(), is_direct_build_only(), get_materialize_threshold(); reads DAN_DIRECT_BUILD, DAN_MATERIALIZE_THRESHOLD
      graph_quality.py           # Phase 33-7 — QualityCheck, GraphQualityReport, check_node_count/pattern_presence/tool_coverage/topology, compute_quality_report; semantic quality scoring (0-100) after structural validation; `DAN_GRAPH_QUALITY_THRESHOLD` (default 0) enables opt-in rejection; shared prompt-complexity signal: estimate_prompt_complexity() (→T1/T2/T3/T4), expected_node_range() (→(min,max)), tier_quality_threshold() (T1→30, T2→40, T3→50, T4→60), is_acceptable_simple_graph() (skip quality gate for correct single-pattern graphs)
      diagnosis.py               # Phase 24-4 — GenerationError, ErrorClassifier, ArtifactMapper, CorrectionStrategySelector, AutoFixApplier, DiagnosisLoop, DiagnosisMetrics; bounded repair for failed generations
      generation_defaults.py     # Phase 32-3/32-5 — DefaultProfile, GenerationDefaults, DefaultsEnricher (smart defaults), DomainGenerationProfile, get_domain_profile() registry, build_domain_prompt_context()
      structural_mutations.py    # Phase 32-4 — 6 structural mutation macros (wrap_in_review_loop, fan_out_node, insert_validator, insert_tool, parallelize, unwrap_loop), resolve_node(), dispatch_structural_mutation(), summarize_graph()
    notifications/               # Phase 16 (26-4) — push notification channels for run events
      __init__.py                # Public API: NotificationConfig, NotificationManager, load_notification_config
      config.py                  # NotificationConfig, ChannelConfig, WebhookConfig, load/save from ~/.dan/notifications.json + env vars
      manager.py                 # NotificationManager — subscribes to GlobalEventBus, filters notification events, dispatches to channels
      macos.py                   # MacOSNotifier — osascript / terminal-notifier for macOS Notification Center
      terminal.py                # Terminal bell helpers (should_ring_bell, ring_bell, maybe_ring_on_event) + TerminalBellNotifier channel adapter
      webhook.py                 # WebhookNotifier — async httpx POST with retry, custom headers, structured JSON payload
    executors/                   # Phase 1 — built-in node executors
      __init__.py                # Auto-registers built-in executors
      llm.py                     # LLMExecutor — OpenAI-compatible (vectorengine.ai default)
      tool.py                    # ToolExecutor + ToolRegistry — function dispatch
      code.py                    # CodeExecutor — sandboxed Python exec
      rag.py                     # RAGExecutor — embed query → vector search → chunk retrieval, event emission, store caching
      control_flow.py            # GateExecutor (unified branching/looping), IfElse, WhileLoop, ForEach, ParallelSubagents, Orchestrator, Reduce, Router, HumanInTheLoop
      validator.py               # ValidatorExecutor — rule-based data validation with valid/invalid routing
      reflection.py              # ReflectionExecutor — LLM-based causal analysis of run failures, principle extraction
    builder/                     # Phase 1.5 — fluent workflow builder DSL
      __init__.py                # Public API: workflow(), WorkflowBuilder, NodeRef, PortRef, decompile(), namespace_graph, derive_ports
      refs.py                    # NodeRef, PortRef — compile-time proxies with __format__, __rshift__, __getitem__
      builder.py                 # WorkflowBuilder — node creation, edge registration, context managers, import_workflow()
      compiler.py                # Compile builder state -> Graph model (marker resolution, port/edge generation)
      importer.py                # namespace_graph(), derive_ports() — import pre-built Graph as composite node
      decompiler.py              # Graph -> Python builder code string (for visual editor round-trip)
    loader/                      # Phase 5 — markdown authoring surface (workflow.md + agent .md files)
      __init__.py                # Public API: load(), load_agents(), compile_workflow()
      models.py                  # Parsed markdown IR: AgentSpec, WorkflowSpec, FlowStatement, PortSpec
      parser.py                  # Markdown parser (frontmatter, sections, ports, context, flow extraction)
      flow_parser.py             # Flow-line parser (chain / each / loop / if)
      types.py                   # Port schema inference + linked-schema loading
      compiler.py                # Markdown→Graph compiler (agent→node, flow→edge, auto-wiring, InputNode, diagnostics)
      decompiler.py              # Graph→Markdown decompiler (node→agent.md, edge→flow, round-trip)
      diagnostics.py             # Diagnostic, CompileResult, DecompileResult, format_diagnostics()
    blocks/                      # Phase 12 (21-5) — shareable block packaging
      __init__.py                # Public API: DanBlock, BlockRegistry, export/import functions, BlockResolver
      models.py                  # DanBlock manifest, BlockDependency, InstalledBlock
      export.py                  # export_workflow_block(), export_composite_block(), export_agent_collection_block(), pack_block()
      importer.py                # import_block() — install from directory, tarball, or URL
      registry.py                # BlockRegistry — scan/list/get/remove, _index.json cache
      executor.py                # BlockResolver, load_block_as_graph(), resolve_node_block()
    publish/                     # Phase 12 (21-3) — publish workflows as MCP/HTTP services
      __init__.py                # Public API: runtime, session, schema, http, portal exports
      runtime.py                 # PublishRuntime ABC, GatewayRuntime (dan-serve), LocalRuntime (direct Engine), create_publish_runtime() factory — unified execution backend
      session.py                 # PublishSession, PublishSessionStore, PublishedHumanRenderer (asyncio.Event-based wait), submit_human_input() — used by LocalRuntime
      schema.py                  # slugify(), workflow_to_mcp_tools(), workflow_to_openapi_paths/spec()
      mcp_server.py              # build_mcp_server(), run_mcp_stdio/http(), load_workflows_from_path() — FastMCP integration (optional mcp dep), uses PublishRuntime via _RuntimeHolder
      http_server.py             # PublishRegistry, create_publish_router(), create_publish_app() — FastAPI REST endpoints, runtime initialized via lifespan
      gateway_mode.py            # DEPRECATED — backwards-compat shim re-exporting PublishGatewayClient (use PublishRuntime instead)
      portal.py                  # generate_mcp_config(), generate_api_docs(), generate_openapi_spec() — consumer-facing output
    cli/                         # Phase 12 — terminal CLI for headless execution
      __init__.py                # load_env(), resolve_config(), ensure_dan_dir(), _try_import_rich()
      run.py                     # dan-run entry point: argparse CLI, source detection, CLIHumanRenderer, TUI display, background mode, optional terminal bell on run-complete/fail/input-needed events
      status.py                  # dan-status entry point: list active/recent background runs from ~/.dan/runs/
      logs.py                    # dan-logs entry point: tail JSONL event logs with --follow streaming
      publish.py                 # dan-publish entry point: argparse CLI, MCP/HTTP/both modes, --generate-config/--docs/--openapi output modes (21-3)
      adapter.py                 # dan-adapter placeholder (21-4)
      blocks.py                  # dan-blocks CLI — list/install/export/remove/pack/info subcommands (21-5)
      dag_display.py             # ASCII DAG renderer: render_dag(), render_stats(), format_workflow_table(); topological sort, box-drawing (Unicode/ASCII), per-type colors via Rich
      mutation_diff.py           # Mutation diff display: format_mutation_diff(); ANSI-colored +/-/~ prefixes with graceful degradation
      run_progress.py            # RunProgressTracker: streaming node execution progress with status icons, elapsed time, final summary
      chat.py                    # dan-chat and dan-ask entry points: REPL and one-shot wrappers for chat API with --local fallback
      chat_local.py              # LocalChatRuntime — in-process ChatManager mirroring ChatClient interface for serverless operation (26-1)
      up.py                      # dan-up entry point: check/start server, startup lock (`~/.dan/server.lock`), PID file management, drop into dan-chat (26-1)
      down.py                    # dan-down entry point: stop background server via PID file (26-1)
      service.py                 # dan-service entry point: OS-level service management (install/uninstall/start/stop/status/health/logs) — macOS launchd + Linux systemd (26-2)
      service_runner.py          # Shared service runner: log rotation + PID bookkeeping + uvicorn launch, used by launchd/systemd/manual starts (26-2)
      main.py                    # Unified `dan` CLI entry point: dispatches `dan bot/serve/run/chat/ask/...` to submodules (Phase 20)
      bot.py                     # dan-bot: create/list/start/stop/start-all/remove/edit/assign/group Telegram bots; fleet daemon management with `~/.dan/telegram/fleet.lock`, `fleet.pid`, and `fleet.ctl` coordination files (Phase 20)
    server/                      # Phase 2 — FastAPI backend for visual editor
      __init__.py
      __main__.py                # CLI entry point: `dan-serve` / `python -m dan.server`
      app.py                     # FastAPI application — CRUD, runs, WebSocket, built-in tool registry, experience APIs, and meta-orchestrator APIs (plan/validate/run/pause/resume/events); lifespan wires ChatManager with user profile + conversation memory and manages NotificationManager subscription to GlobalEventBus
      chat_factory.py            # Shared factory for LocalChatRuntime chat dependencies; now builds capability context + concierge for local parity (26-1, 25-6)
      exec.py                    # execute_python() — shared Python executor for run_python and run_strategy_script
      graph_store.py             # Filesystem-based graph JSON persistence
      graph_mutator.py           # GraphMutator: applies MutationPlan (add/remove/edit nodes+edges) to graph dicts with transactional semantics + dry-run; TOOL_PORT_MANIFESTS for tool-specific port declarations; ApplySkill mutation op
      skill_library.py           # SKILL_LIBRARY: domain-specific prompt-injection skills (management_science_writing, informs_latex_style) targeted by node tags
      capability_registry.py     # Phase 15 (25-1) — ChatCapabilityRegistry, CapabilityContext, CapabilityResult, build_tool_schema(); mode-aware multi-tool dispatch for chat-as-control-plane
      capability_handlers.py     # Phase 15 (25-1–25-4, 25-13) — 36 capability tool handlers (experience, run lifecycle, publish/share/export, graph, all 11 built-in tools, telegram_poll); register_*_capabilities() functions
      chat_manager.py            # ChatManager: graph-aware LLM conversations, function-calling for graph mutations (MUTATION_TOOL_SCHEMA) + capability tools (ChatCapabilityRegistry), text-streaming fallback, context window management (MODEL_CONTEXT_WINDOWS, estimate_tokens, compact_history), profile/memory prompt injection, preflight tool hook execution (_run_preflight_hooks), and conversation-summary persistence (26-3 integration)
      chat_store.py              # Filesystem-based chat persistence (per-workflow threads)
      concierge/                # Phase 15 (25-6/25-7) — deterministic routing/runtime layer: project/task store, classifier, handlers, policy, queue, progress, promotion, goal loop (31-6)
        completion_guard.py      # Phase 21 (31-9) — Completion guard: RequirementExtractor (heuristic+LLM), CompletionChecker (keyword+LLM), augment_response(), run_completion_check(), /completion command, CompletionStats
        pii_tokenizer.py         # Phase 21 (31-10) — PII tokenization: SensitiveWordRegistry, PIISession, tokenize/detokenize, TokenizingProviderWrapper, /pii commands, auto-detection (email/phone/SSN/CC/IP)
        computer_policy.py       # Phase 21 (31-17) — Computer control policy: ComputerControlConfig, ChunkPolicies (6 chunks), BrowserDomainRule, SessionOverride, VisionExportPolicy, FileSafetyPolicy, action classification, domain/app allowlists, AuditLog
        computer_use.py          # Phase 21 (31-17) — Computer use controller: UIObservation, ObservedElement, ComputerUseLeaseManager, ComputerUseController (observe/act/verify, progress emissions, approval integration), /computer commands
        learning.py              # Phase 21 (31-15) — /corrections and /adaptations command handlers
        progress_ux.py           # Phase 21 (31-14) — Progressive response UX: ProgressRenderer protocol, ProgressSession, 4 surface renderers (CLI/Telegram/WhatsApp/Editor), verbosity control, pre-flight clarification, /progress command
        scheduler.py             # Phase 21 (31-7) — Scheduled tasks: TriggerContext, DeliveryTarget, ScheduleEntry, ScheduleRunRecord, parse_trigger(), compute_next_run(), ScheduleStore, ScheduleHistoryStore, TaskScheduler, /schedule commands
        resume.py                # Phase 21 (31-11) — Cross-session resume: TaskSnapshot, ResumeProtocol (check_resumable_tasks, generate_resume_prompt, auto_resume_match, update_task_state), persist_task_state, extract_structured_state, /resume command
        follow_up.py             # Phase 21 (31-12) — Proactive follow-up: FollowUpTrigger, FollowUpConfig, FollowUpQueue, FollowUpDeliveryEngine, trigger factories, scan_stale_tasks, /follow-ups command
        continuity.py            # Phase 21 (31-13) — Multi-surface continuity: SurfaceRoutingPolicy, ProjectConversationStore, detect_surface_switch(), generate_handoff_context(), PresenceTracker, route_message_to_surface(), /sync command
      run_manager.py             # Background run execution + event pubsub + catch-up + ToolRegistry injection + human-input registry + streaming coalescing + RunStore integration + metric enrichment + learning event emissions (dual origin/reflection routing) + incremental experience consolidation/indexing + emit_rule_lifecycle_event() for API-driven rule management
      run_store.py               # Filesystem-backed persistence for run summaries (JSON) and event logs (JSONL). Layout: runs/{workflow_id}/{run_id}.json + .events.jsonl
      scoped_run.py              # Scoped execution: full/node/subgraph run builder
      layout.py                  # Topological layout for graph JSON (DAN_LAYOUT_ON_LOAD)
      mutation_metrics.py        # Mutation quality metrics for chat/LLM feedback
      variable_inspector.py      # Compute upstream inputs for a node: walks incoming edges, infers types, detects missing required inputs (Plan 13-2)
      test_cases.py              # NodeTestCase schema, TestCaseRunResult, TestCaseStore (filesystem CRUD at test_cases/{workflow_id}/{node_id}.json) (Plan 13-2)
      gateway/                   # Phase 13 — multi-surface gateway
        __init__.py              # Package marker
        models.py                # DispatchRequest/Result, PendingInput, SubmitInputRequest, CancelRequest/Result, ActivitySnapshot, SurfaceRegistration
        activity.py              # ActivityTracker — surface-aware run activity tracking wrapping RunManager
        events.py                # GlobalEventBus — cross-surface event streaming with backpressure (max 50 subscribers, drop-oldest)
        router.py                # FastAPI router at /api/gateway/ — dispatch, cancel, activity, surfaces, pending-inputs, submit-input, WebSocket events
  editor/                        # Phase 2+3.5 — React Flow visual editor
    package.json                 # Dependencies: react, @xyflow/react, zustand, tailwindcss, dagre, allotment, highlight.js, lucide-react, @monaco-editor/react, monaco-editor, @xterm/xterm, @xterm/addon-fit, @xterm/addon-web-links
    vite.config.ts               # Vite config: Tailwind plugin, /api proxy to backend
    tsconfig.json                # TypeScript config
    src/
      types/graph.ts             # TypeScript types mirroring dan_graph_v1 + NODE_DESCRIPTIONS
      types/chat.ts              # ChatMessage, ChatThread, ChatStreamEvent types
      lib/graphAdapter.ts        # Bidirectional DAN <-> React Flow conversion + EDGE_COLORS + edge labels
      lib/api.ts                 # HTTP/WebSocket API client
      lib/paletteTemplates.ts    # Extensible template factories (ReAct, Plan-Execute)
      lib/connectionValidation.ts # isValidConnection — no self-connect, no duplicates
      lib/graphImporter.ts       # Workflow-as-node: converts saved graph into CompositeNode with autonomous-entry filtering, node-aware port mappings, entry/exit validation
      lib/layout.ts              # Auto-layout via dagre (LR direction)
      lib/nodeIcons.tsx          # Inline SVG icons for all 16 node types
      lib/mentionParser.ts       # @mention serialization (`@[name](type:id)`), parsing, cursor detection, co-navigation dispatch, type colors
      lib/graphDiff.ts           # Before/after graph diff computation
      lib/portOrdering.ts        # Deterministic port ordering (orderPorts, computePortReorder) for DanNode display
      store/useGraphStore.ts     # Zustand store — graph, selection, run state, events, layers, toasts, timings, clipboard, history, port ops, loop iterations, streaming, human input, workflow import
      hooks/useKeyboardShortcuts.ts # Keyboard shortcuts: save, undo/redo, copy/paste/duplicate
      components/DanNode.tsx     # Custom node: port handles, status ring, pulse/glow, duration badge, icons, dimming, inline rename, loop badges/counters
      components/AnimatedEdge.tsx # Custom edge: particle flow on active edges, dimming on inactive
      components/NodePalette.tsx  # Searchable categorized sidebar: templates, edge selector, hover previews, saved workflows
      components/ConfigPanel.tsx  # Node/edge property editor, port editor (add/rename/delete), SchemaEditor (visual + raw JSON), test cases section
      components/TestCasePanel.tsx # Node test case management: collapsible list, create/edit modal, run/pass/fail display, context menu integration (Plan 13-2)
      components/ContextMenu.tsx  # Right-click context menu: canvas/node/edge actions (paste, copy, delete, edge type, add test case)
      components/GraphCanvas.tsx  # Main canvas: drop handling, drill-in, validation, animated edges, context menu, edge reconnection
      components/EditorToolbar.tsx # Merged toolbar: graph selector + run controls + auto-layout
      components/TabBar.tsx       # Horizontal workflow tabs with run status badge, close, "+ New" template picker
      components/CommandPalette.tsx # Cmd+K modal: search nodes by name/type, center viewport on select
      components/LoopGroupNode.tsx  # Collapsed/expanded loop group visualization (visual-only node)
      components/RunInputsDialog.tsx # Modal for collecting entry-point input variables before run
      components/BreadcrumbBar.tsx # Layer navigation: Root > Node1 > Node2
      components/PortMappingOverlay.tsx # Input/output port mapping display when drilled in
      components/LogPanel.tsx     # Rich structured logs: grouped by node, icons, filtering, click-to-select
      components/ExecutionTimeline.tsx # Horizontal timeline bar with per-node segments
      components/OutputPreview.tsx    # Per-node output viewer with streaming text support
      components/HumanInputDialog.tsx # Modal popup for mid-run human-in-the-loop input submission
      components/MentionAutocomplete.tsx # Floating @ mention dropdown: nodes/workflows/subgraphs, keyboard nav, fuzzy filter
      components/ChatPanel.tsx        # Resizable chat sidebar: message send/stream, @ mention integration, mutation event handling + GraphDiffPreview
      components/ChatMessage.tsx      # Message bubble: markdown render, mention chips with click-to-navigate, grouped tool call cards, run output blocks
      components/ToolCallCard.tsx     # Expandable tool call card: status icon, args/output sections, file links, operations list, duration badge
      components/RunOutputBlock.tsx   # Structured run output: per-node status, collapsible output, timing, "View logs" / "View in History" links
      components/RunHistoryPanel.tsx  # Run history bottom panel tab: filterable run list, event replay view, side-by-side comparison, deep-link support
      components/TokenAnalyticsPanel.tsx # Token optimization bottom panel tab: waste findings, category filters, one-click apply mutations, re-analyze (Plan 18-4)
      components/GraphDiffPreview.tsx  # Mutation diff preview modal: accept/reject/partial-accept
      components/ToastContainer.tsx   # Fixed bottom-right toast notifications
      components/Spinner.tsx          # Reusable loading spinner
      components/RunPanel.tsx         # (deprecated — merged into EditorToolbar)
      components/GraphSwitcher.tsx    # (deprecated — merged into EditorToolbar)
      App.tsx                    # Main layout: toolbar + palette + canvas + panels + toasts
  examples/                      # Phase 3+ — runnable workflow scripts
    paper_writing.py             # INFORMS-oriented workflow: internet-grounded lit search, human interview loop, parallel section drafting, multi-role review, LaTeX/PDF packaging
    paper_writing_md/            # Markdown rewrite of paper-writing pipeline (Phase 5 validation fixture)
    simple_chain.py              # Phase 4 template: 3-node linear pipeline (LLM→LLM→Code)
    fan_out_fan_in.py            # Phase 4 template: ForEach + Reduce parallel processing
    review_revise.py             # Phase 4 template: GateNode while-loop draft→review→revise
    rag_qa.py                    # Phase 4 template: tool-based RAG Q&A (no vector DB)
    react_agent.py               # Phase 4 template: ReAct agent loop with web tools
  tests/                         # pytest suite (771 passed, 15 skipped)
    test_models/                 # Unit tests for all model types
    test_validation/             # Validation logic tests
    test_examples/               # Paper-writing motivating example + e2e tests
    test_engine/                 # Engine unit + integration tests
    test_builder/                # Builder DSL unit + integration tests
    test_loader/                 # Markdown loader parser/compiler/type-inference tests
    test_snapshots/              # Snapshot/regression tests (builder + loader output vs stored JSON)
    test_migration/              # Migration helper tests (legacy → gate)
    test_cli/                   # CLI unit tests (LocalChatRuntime, PID management, dan up/down, chat parser flags, chat_factory)
    test_server/                 # Server API, run manager, and event tests
    test_notifications/          # Notification infrastructure tests (config, manager, macos, webhook, terminal bell)
    quality_suite/               # Generation quality suite: golden intent loader + tests
      loader.py                  # load_golden_intents() — validates and returns fixture list
      graph_equivalence.py       # GraphEquivalenceChecker + round_trip_check()
      codegen_runner.py          # Codegen path evaluator: fixture → WorkflowIntent → compile → exec → validate → round-trip
      intent_runner.py           # Intent compiler path evaluator: fixture → coverage → compile → validate → round-trip
      report.py                  # GenerationQualityReport + baseline regression comparison
      __main__.py                # python -m tests.quality_suite CLI entry point
      test_quality_suite.py      # 25 tests for runners, reports, baseline, topology, conversions
    eval/                        # Phase 33 workflow generation quality evaluation harness; see docs/eval-run-guide.md
      __init__.py                # Shared Pydantic models: PromptFixture, EvalRecord, TimingInfo, TokenInfo, etc.
      client.py                  # Async HTTP/WS client (httpx + websockets); stream_events() follows chat_queued redirects
      telemetry_reader.py        # Sync read-only SQLite reader for ~/.dan/telemetry.db
      runner.py                  # EvalRunner: run_battery(), run_single(), run_multi_turn(), load_prompts()
      metrics.py                 # EvalLogger: timestamped JSONL writer with round-trip load_records()
      report.py                  # ReportGenerator: per-tier/lane/path breakdowns, Rich table output
      durability_checks.py       # D1-D4 durability smoke checks (repeat-run, reload, export, mutation)
      __main__.py                # CLI: python -m tests.eval [--tier T1] [--pilot] [--execute] [--report ...]
      prompts.json               # 44 prompt fixtures (T1-T5, pilot, multi-turn, edge cases)
    test_eval_runner.py            # Regression tests for eval runner stream handling and clarification auto-reply
    test_concierge_runtime_modes.py  # Tests for _requested_mode_forces_solver_path() build/mutate bypass
    fixtures/
      golden_intents/            # 18 golden intent JSON fixtures (5 families × 3 variants + 3 edge cases) + schema.json
      generation_quality_baseline.json  # Committed pass-rate baseline for regression detection
      markdown/                  # Markdown loader test fixtures
  graphs/                        # Saved graph JSON files (filesystem persistence)
  pyproject.toml                 # Pydantic v2 + OpenAI SDK + FastAPI + uvicorn + httpx + pytest; optional: anthropic, google-generativeai, pypdf, duckduckgo-search
  README.md                      # User-facing project overview, quick start, feature summary
  .env.example                   # Environment variable template
  .cursor/rules/                 # AI agent rules
```

## Import and API Conventions

- **Top-level `dan`:** Exposes models (InputPort, OutputPort, node/edge types, Graph, NodeTypeRegistry). Does not expose Engine, builder, loader, or mutator.
- **Subpackages:** `dan.engine` (Engine, EngineConfig, RunResult, ExecutionContext, etc.), `dan.builder` (workflow, decompile, NodeRef, PortRef), `dan.loader` (load, compile_workflow), `dan.validation` (internal). Public vs internal is implicit — `dan.engine.scheduler` is importable but not re-exported at package level.
- **Graph schema:** snake_case everywhere (Python models, JSON, TypeScript graph.ts). REST/WebSocket payloads use snake_case; chat message format uses camelCase↔snake_case conversion at API boundary.

## Core Abstractions

### Object Design Principles (NodeBase)

All 16 node types inherit from `NodeBase` with fields: `id`, `name`, `description`, `input_ports`, `output_ports`, `position`, `ui`, `metadata`, `retry_policy`, `read_set`, `write_set`. Composite/loop nodes override `read_set`/`write_set` for context declarations. GateNode and ValidatorNode use `model_post_init` to set default output ports when empty. InputNode uses `variables` instead of `input_ports` for external inputs. Sub-graph keys follow `{parent_id}__body` or `{parent_id}__{branch_name}`.

### Two-Level Node Model (inspired by AFlow)

- **Operator (atomic):** Single LLM call, API call, code execution, database query, or conditional. The fundamental unit. Each operator independently specifies its model.
- **Agent (composite):** A group of operators wired into a sub-graph that behaves as a single unit with a defined interface (input schema → output schema). Inspectable — double-click to zoom into internal graph.

### Typed Edges (inspired by supply chain management)

| Edge Type | Purpose |
|-----------|---------|
| **Data edge** | Structured output of node A feeds node B. Validated with JSON Schema at design time. |
| **Control edge** | Conditional routing (if/else), loops (for-each, while), parallel fan-out/fan-in, retry logic. |
| **Context edge** | Shared memory or state (conversation history, accumulated knowledge, file system) readable/writable by multiple agents. |

### Control-Flow Primitives

| Primitive | Behavior |
|-----------|----------|
| If/Else | Route based on condition evaluated on upstream data |
| While Loop | Repeat until condition met or max iterations reached |
| For-Each / Map | Fan-out: apply sub-graph to each item in a list, in parallel |
| Parallel Subagents | Fan-out: run heterogeneous sub-graphs concurrently, merge at fan-in |
| Reduce | Fan-in: aggregate results from parallel branches |
| Router | LLM-powered routing — model decides which branch |
| Human-in-the-Loop | Pause execution, wait for human input, resume |

### Model Heterogeneity

Each operator node independently specifies its model. Cheap/fast for classification, strong for reasoning, code-specialized for generation. First-class design principle, not afterthought.

**Task-level model tiering (18-5):** When no per-node model is set, the `TierPolicy` strategy automatically scores each call on three dimensions — difficulty (reasoning depth), impact (downstream blast radius), recoverability (validator/retry safety net) — and maps the combined score to one of four model tiers: `micro` (cheapest), `routine`, `reasoning`, `critical` (strongest). Tier → model mapping is provider-aware and configurable. Adaptive escalation bumps the tier on normalizer/validator failure; telemetry-driven de-escalation suggests cheaper tiers after repeated success.

### Output Normalization (built-in)

Like batch normalization in DNNs, every LLM operator has a deterministic, built-in output normalization layer: parse → validate against output schema → re-prompt with error on failure → retry up to N times. This is automatic (not a user-visible node) and guarantees every data edge carries schema-valid data or an explicit error.

### Error Handling / Retry Policy

Every operator carries a `retry_policy`: `max_retries`, `backoff`, `backoff_max`, `fallback_model`, `on_failure` (error / skip / halt). Separate from output normalization — this handles call-level failures (rate limits, timeouts, network errors). `on_failure="halt"` stops the engine at the current topological level (already-running parallel nodes finish) and writes a checkpoint for later resume.

**`RetryPolicy` model** (Pydantic, in `dan.models.nodes`):

| Field | Type | Default | Semantics |
|---|---|---|---|
| `max_retries` | `int` | `0` | Retry attempts after initial call |
| `backoff` | `float` | `1.0` | Initial delay (seconds), doubles each retry |
| `backoff_max` | `float` | `60.0` | Ceiling on backoff delay |
| `fallback_model` | `str \| None` | `None` | Alternative model on final failure (LLM only) |
| `on_failure` | `Literal["error", "skip", "halt"]` | `"error"` | Post-exhaustion behavior |

Attaches to `NodeBase.retry_policy` (optional, defaults to `None` → no retries). `ToolExecutor` reads the policy, catches transient exceptions (`TimeoutError`, `ConnectionError`, `OSError`), retries with exponential backoff capped at `backoff_max`, emits `retry_attempted` events. `LLMExecutor` handles transient API errors similarly, with optional `fallback_model` switch on the final retry.

### Checkpointing / Resumability

After each topological level completes, the engine persists outputs, artifact state, context store snapshot, and execution pointer. On restart, resumes from the last completed level.

### Four-Layer Context Model

Direct edge data handles simple input/output. Growing payloads, shared state, and dynamic updates are handled by four distinct layers:

| Layer | What It Holds | Scope | Mutability |
|-------|--------------|-------|------------|
| **1. Edge Data** | Typed, bounded payloads on data edges | Between two nodes | Immutable per edge |
| **2. Node-Local State** | Private working memory (iteration history, convergence metrics) | Scoped to a composite agent / loop | Mutable within scope, invisible to parent |
| **3. Shared Context Store** | Namespaced key-value blackboard (`context.outline`, `context.bibliography`) | Graph-wide, opt-in via declared `read_set` / `write_set` | Mutable; write modes: `write`, `append` |
| **4. Artifact Store** | Large objects (drafts, datasets, figures) stored by reference | Graph-wide | Immutable (new version per revision) |

> **Layer 2 active usage:** `LocalStateManager` is now used for loop-scoped state in while-gate loops (Plan 7-6). When `GateNode.state_schema` is present, the scheduler maintains a state bag via `LocalStateManager` scoped to the gate — body nodes receive state fields as regular inputs and outputs matching `state_schema` keys are merged back into scope automatically.

- **Code node port defaults (7-6):** `CodeExecutor` injects type-appropriate defaults for missing optional input ports based on `json_schema` (array→[], object→{}, number→0, string→"", boolean→False). Eliminates `try/except NameError` boilerplate.
- **Spread edges (7-6):** `DataEdge` with `spread=True` destructures source dict fields into target node input ports. One edge replaces many scalar edges for struct passthrough.

### Context Projection

At every scope boundary (entering a sub-graph, entering a loop iteration), a **projection function** extracts only what the next consumer needs. Each consumer gets a minimal view — the loop controller sees only iteration count + convergence metrics, the reviser sees only current draft + latest comments, the parent graph sees only the final output.

### Composite Node Contract

Every composite/loop node declares:
- `external_input_schema` / `external_output_schema` — what the parent sees
- `control_state` — iteration count, stop flags, thresholds (loop controller only)
- `local_working_set` — latest working data, not full history
- `read_set` / `write_set` — declared dependencies on shared context store (composite/loop nodes; atomic operators inherit from NodeBase for context-edge targets)
- `compaction_rule` — how local history is summarized between iterations
- `feedback_selector` — `FeedbackSelector(include/exclude/rename/transform)` on `GateNode`/`WhileLoopNode` controls which body outputs cycle back vs. become side-effect artifacts; `artifact_ports` is sugar for extracting and accumulating named ports across iterations

### Context Policies

- **Mutation**: nodes read shared context by default; writes require declaration
- **Parallel merge**: fan-out branches must specify merge rules (append, last-write-wins, or reducer node)
- **Compaction**: configurable per composite node (sliding window, summarization gate, diff-based)
- **Failure exits**: `max_iterations`, `stagnation`, `timeout`

### Context Scoping Across Agent Boundaries

The four-layer context model describes *what kinds* of context exist. Context *scoping* describes *where* context is visible when agents are nested (agents containing sub-agents containing sub-sub-agents).

Four scopes govern visibility at every nesting level:

| Scope | Analogy | Direction | What It Holds |
|-------|---------|-----------|---------------|
| **global** | Global variable | Everywhere (read by all layers) | Codebase index, conversation history, workspace config, rules |
| **local** | Local variable | Stays at current layer | Working memory, retry counts, loop counters, chain-of-thought |
| **pass_down** | Function arguments | Parent → child | Task description, relevant files, constraints, plan |
| **emit_up** | Return value | Child → parent | Result summary, status, discovered signals |

**`pass_down` is explicit, not inherited.** A parent doesn't dump its local context to children. Each child declares an input schema — only what it needs crosses the boundary. This prevents context pollution.

**`emit_up` is explicit, not leaked.** A child returns a structured output, not its entire working memory. The parent decides what to do with it. This prevents noise.

**`global` is read-heavy, write-careful.** Most nodes only read global context. Writes need declaration and conflict resolution (especially during parallel fan-out).

**`local` is invisible outside.** Bulk of working memory. Dies when the agent finishes.

#### Upward Signals

Not everything emitted upward has the same semantics:

- **Results** — the expected structured output. Schema-validated. Consumed by the immediate parent.
- **Signals** — unexpected discoveries that higher layers should know about. Two sub-types:
  - **Sticky signals** — written to global context (everyone should know). Example: "this codebase uses pnpm, not npm."
  - **Non-sticky signals** — propagate up one layer. The parent decides whether to act, relay further, or discard. Example: "circular import detected in module X."

#### Agent Boundary Contract (revised)

Every agent (composite node) formalizes its boundary:

```python
agent PaperWriter:
  accepts:       { topic: str, papers: Paper[], data: Dataset }   # pass_down schema
  returns:       { draft: LaTeX, figures: Fig[], bib: BibTeX }    # emit_up schema
  reads_global:  [codebase_index, style_rules]                    # global dependencies
  writes_global: []                                               # global mutations
  signals:       [quality_warning, missing_data, style_violation] # possible upward signals
```

This supersedes the earlier composite node contract for cross-layer communication. The original `external_input_schema` / `external_output_schema` / `read_set` / `write_set` still apply for the within-graph four-layer model; the boundary contract adds `signals` and clarifies directional semantics.

### Hyperedges: Skills and Rules

Standard edges connect two nodes. **Hyperedges** connect an arbitrary subset of nodes simultaneously. Skills and rules are modeled as hyperedges — graph-level constructs that apply to multiple nodes at once.

```
            ┌──────────────────────────────────┐
            │  "INFORMS Style Guide" (skill)   │  ← hyperedge
            └──┬──────────┬───────────┬────────┘
               ↓          ↓           ↓
         [section-draft] [citation-fmt] [latex-compile]
```

#### Hyperedge Types

| Type | Semantics | Execution Hook | Example |
|------|-----------|----------------|---------|
| **Skill** | Adds knowledge/capability to attached nodes | `pre_prompt` — injected into LLM context | "Scientific writing conventions" |
| **Rule (guardrail)** | Constrains behavior | `post_output` + `validation` — checks output | "Never use GPT-3.5 for final output" |
| **Rule (style)** | Enforces consistency | `pre_prompt` — style context injected | "APA 7th edition citations" |
| **Rule (override)** | Intercepts/rewrites | `tool_call` — modifies or blocks tool invocations | "All shell commands require approval" |

#### Attachment Scope

Hyperedges attach to nodes by:

- **Node ID** — specific node (`attach_to: ["section-draft-1"]`)
- **Node type** — all nodes of a type (`attach_to_type: "llm_operator"`)
- **Tags** — user-defined labels (`attach_to_tags: ["writing", "review"]`)
- **Subgraph** — all nodes within a composite (`attach_to_subgraph: "paper-writer"`)

Inheritance: hyperedges on a parent graph propagate to sub-graphs unless explicitly excluded.

#### Precedence

When multiple hyperedges attach to the same node, they compose in order: `policy > rule > skill`. Within the same type, more specific scope wins (node ID > tag > type > subgraph).

#### Hyperedge JIT Loading (18-1)

When `EngineConfig.hyperedge_jit_loading=True`, hyperedges whose content exceeds `hyperedge_jit_threshold` (default 500 tokens) are injected as compact one-line summaries instead of full content. The LLM receives a `load_hyperedge(name)` tool to fetch full skill/rule content on demand. This reduces prompt tokens for workflows with many or large hyperedges.

#### Skill Store (IDE-compatible)

Skills are stored as `SKILL.md` files with YAML frontmatter — the same format used by Cursor, Claude Code, and Codex. `SkillStore` (`src/dan/server/skill_store.py`) scans three tiers on startup:

1. **User-level** — `~/.dan/skills/<name>/SKILL.md` (cross-project)
2. **Project-level** — `.dan/skills/<name>/SKILL.md` (per-project)
3. **Legacy** — flat `.md` files from `DAN_CUSTOM_SKILLS_DIR`

Narrower scopes shadow broader ones (project > user > extra). Each `SkillDescriptor` converts to a `Hyperedge` for engine runtime use. The `/skill import <path>` command imports skills from other IDE skill directories.

The frontmatter schema is a superset: `name` + `description` (shared with all IDEs) plus optional DAN extensions (`tags`, `hyperedge_type`, `hook`, `attach_to_*`, `scope`). DAN skills are readable by other IDE agents because unknown frontmatter keys are silently ignored.

#### Why Hyperedges, Not Context Edges

Context edges (Layer 3) carry *data* — key-value pairs that nodes read/write. Hyperedges carry *behavior modifiers* — they change how nodes execute, not what data they consume. A skill doesn't add a key to the shared context store; it modifies the prompt of every node it's attached to. This is a fundamentally different concern.

### HumanNode (Generalized)

The Human-in-the-Loop control-flow primitive is generalized into a first-class node type: `HumanNode`. The human is not outside the graph talking *to* it — the human is a node *in* the graph.

**Interface:** Same as any other node — typed input schema (what to show the human) and typed output schema (what the human provides).

**Behavior:** Execution pauses at a HumanNode. The rendering layer (chat panel, web UI, CLI) presents the input and collects the output. Execution resumes.

**Implications:**

- **Chat is rendering.** The chat panel is a view that renders whichever HumanNode is currently active. Message appears → human types → output flows to the next node.
- **Adjustable autonomy is topology.** Full autopilot = no HumanNodes in the graph. Careful oversight = HumanNode between every agent. Approve only final output = one HumanNode at the end. This is a graph design decision, not a mode switch.
- **Background mode = zero HumanNodes.** A background agent is just a graph with no human nodes. "Check in every N steps" is a HumanNode inside a while-loop with a counter-based conditional.
- **Multi-point interaction.** Different HumanNodes ask different things. One asks "which papers?", another asks "approve this figure?", another asks "accept this draft?". The rendering layer sequences them.
- **Rendering is decoupled.** The same graph runs behind a CLI, a web app, a VS Code extension, or a Jupyter notebook. The rendering surface resolves HumanNode I/O; everything else is identical.

```
┌─────────────────────────────────────────────────────────┐
│                    DAN Graph                             │
│                                                          │
│  Nodes:   [Human] [LLM Operator] [Tool Op] [Agent]     │
│  Edges:   data ──→  control ──→  context ──→            │
│  Hyperedges:  ═══ skills ═══  ═══ rules ═══             │
│                                                          │
└─────────────────────────────────────────────────────────┘
         ↕ render                    ↕ render
   ┌────────────┐            ┌──────────────┐
   │ Chat Panel  │            │ React Flow    │
   │ (human I/O) │            │ (graph viz)   │
   └────────────┘            └──────────────┘
```

### Four Top-Level Agents Architecture

For application-level systems (coding assistants, research IDEs), a practical architecture is four independent top-level agents sharing a common context layer:

```
┌───────────────────────────────────────────────────────┐
│              Shared Context Layer                      │
│  (codebase index, conversation history, file state,   │
│   linter output, workspace config, rules, skills)     │
├─────────────┬─────────────┬────────────┬──────────────┤
│  Ask Agent  │ Agent Mode  │Debug Agent │ Plan Agent   │
│  (Q&A       │ (ReAct +    │(hypothesis │ (tree search │
│   graph)    │  tools +    │ driven +   │  + outline   │
│             │  fan-out)   │ auto-diag) │  generation) │
└─────────────┴─────────────┴────────────┴──────────────┘
     each is a complex DAN sub-graph internally
```

The shared context layer is **not** part of any graph. It's a read/write store that all four agents access. Each agent internally is a full DAN network with its own working memory and control flow.

**Why four:** These represent fundamentally different control-flow patterns (linear Q&A vs. ReAct loop vs. hypothesis-driven diagnosis vs. tree search), different tool sets, and different stopping conditions.

**Mode switching:** Serialize the active agent's relevant outputs to the shared context layer → activate the new agent → it reads from shared context on startup. The conversation history carries over; the internal working memory does not.

**Context model:**
- **Global** (shared context layer) — codebase index, conversation history, workspace config, session state. All agents read; writes are declared.
- **Local** (within each agent) — the agent's DAN sub-graph manages its own working memory, loop state, intermediate results. Private. Dies when the agent finishes or the user switches modes. Only durable outputs (file changes, conversation messages, plan artifacts) persist to global.
- **pass_down / emit_up** — standard directional scoping within each agent's internal sub-graph.

## Execution Engine (Phase 1)

### Engine API

```python
from dan.engine import Engine, EngineConfig

config = EngineConfig(
    llm_base_url="https://api.vectorengine.ai/v1",
    llm_api_key="...",
    llm_default_model="claude-sonnet-4-6",
)
engine = Engine(config)
result = await engine.run(graph, inputs={"idea": "..."})
result = await engine.resume(graph, run_id="abc123")
```

### Scheduling

- Async-first: `Engine.run()` is async; parallel fan-out uses `asyncio.gather()`
- Kahn's algorithm groups nodes into topological levels; nodes in the same level execute concurrently
- Cycle-aware scheduling for `GateNode(while)` back-edges: detects gate-controlled cycles, iterates cycle regions bounded by `max_iterations`, DAG fast-path preserved for non-cyclic graphs
- Input injection is virtualized per node (`__input__<node_id>`). Scheduler maps these values into both standard `input_ports` and `InputNode.variables` so `Engine.run(inputs=...)` reaches workflow InputNodes.
- While-gate `continue/loop` routing is phase-aware: loop bodies wait for the initial gate signal, then consume virtual loop-feedback injections during subsequent iterations.
- Sub-graph execution is recursive: WhileLoop/ForEach/Composite executors call back into the scheduler
- Legacy `IfElseNode`/`WhileLoopNode` continue to work (with deprecation warnings); migration helpers in `dan.migration` convert to gate patterns

### Executor Protocol

- `NodeExecutor` is a `Protocol` with `async execute(node, inputs, context) -> NodeResult`
- `ExecutorRegistry` maps `node_type` strings to executor instances; users can register custom executors
- Built-in executors for all 16 node types (including `CompositeExecutor`, `ParallelSubagentsExecutor`, `OrchestratorExecutor`, `RAGExecutor`, `ValidatorExecutor`) auto-registered on Engine creation

### LLM Integration

- **Multi-provider dispatch:** `ProviderRegistry` (in `dan.providers.registry`) routes model names to the correct API. Resolution order: (1) exact `model_provider_map` override → (2) prefix pattern match (`gpt-*`/`o1*`/`o3*`/`o4*`→OpenAI, `claude-*`→Anthropic, `gemini-*`→Google) → (3) `"default"` provider fallback (OpenAI-compatible endpoint). Custom prefix patterns can be added via `registry.add_prefix_pattern()`.
- **Built-in providers:** `OpenAIProvider` (any OpenAI-compatible endpoint, default), `AnthropicProvider` (optional), `GoogleProvider` (optional). Provider SDKs are optional deps.
- **Key management:** Env vars `DAN_OPENAI_API_KEY`, `DAN_ANTHROPIC_API_KEY`, `DAN_GOOGLE_API_KEY` are scanned at server startup (`app.py` `_get_engine_config()`). Each non-empty key auto-registers the corresponding provider. `DAN_LLM_API_KEY` + `DAN_LLM_BASE_URL` configure the default provider (backward compatible with existing vectorengine.ai setup). `DAN_TAVILY_API_KEY` enables Tavily for the `web_search` tool (recommended); `DAN_BRAVE_API_KEY` enables Brave Search as second choice; falls back to DuckDuckGo scraping when neither is set.
- **`DAN_USE_CODEGEN_BUILD`** (default `"1"`): When `"1"`, empty-graph build mode uses the builder-codegen generation path (Phase 14). Set to `"0"` to force the legacy mutation-JSON path for all builds. Only affects new workflow creation; edit-mode mutations are always unchanged.
- **`DAN_DIRECT_BUILD`** (default `"on"`): Direct execution architecture (32-7). `"on"` = inline execution for eligible goals with codegen fallback; `"off"` = always use codegen/MetaController path; `"only"` = never fall back to codegen (for testing). Read via `dan.meta.config.is_direct_build_enabled()` / `is_direct_build_only()`.
- **`DAN_MATERIALIZE_THRESHOLD`** (default `3`): Minimum node count for post-hoc graph materialization. Graphs with fewer nodes are not saved as workflow artifacts. Read via `dan.meta.config.get_materialize_threshold()`.
- **`DAN_MAX_GENERATION_SECONDS`** (default `120`): Hard wall-clock cap across the entire `_generate_workflow_from_intent()` call — intent extraction + codegen + sandbox + diagnosis combined. When exceeded, generation stops immediately with a clear timeout event. Prevents runaway generation from consuming unbounded time.
- **`DAN_GRAPH_QUALITY_THRESHOLD`** (default `0`): Minimum quality score (0-100) for generated graphs. `0` = accept all valid graphs (advisory-only scoring). Tier-adaptive thresholds (T1→30, T2→40, T3→50, T4→60) are recommended via `tier_quality_threshold()` in `graph_quality.py`. Simple single-pattern graphs (chain, fan-out, review loop, conditional branch) that match their expected topology are exempt via `is_acceptable_simple_graph()`.
- Output normalization built into LLM executor: extract JSON -> validate against schema -> re-prompt with error -> retry
- Transient API errors (rate limits, timeouts) retried with configurable `retry_policy`
- **Cost estimation:** static `COST_PER_1K_TOKENS` table in `providers/costs.py` covering major models. `estimate_cost()` utility function. Best-effort — unknown models return None.

### Built-in Tools (`dan.tools`)

- 39 batteries-included tools organized by category:
  - **System** — `current_datetime`, `clipboard`, `python_eval`, `notify`
  - **File I/O** — `file_read`, `file_write`, `list_directory`, `file_move`, `file_copy`, `file_delete` (sandboxed to `DAN_WORKSPACE_ROOT`)
  - **Data** — `csv_read`, `spreadsheet_read` (openpyxl, optional dep)
  - **Web** — `web_search` (Tavily → Brave → DuckDuckGo cascade), `web_fetch` (URL content), `http_request` (general HTTP)
  - **Browser** — `browser_open`, `browser_click`, `browser_type`, `browser_fill`, `browser_extract`, `browser_screenshot`, `browser_wait` (Playwright-backed, persistent profile via `DAN_BROWSER_PROFILE`, shared session singleton via `_browser_session.py`, `DAN_BROWSER_HEADLESS` control)
  - **Shell** — `shell_command` (subprocess with timeout and allowlist)
  - **Document** — `pdf_read` (PDF text extraction + optional vision mode)
  - **Text Processing** — `text_chunk` (chunking with overlap), `json_extract` (dot-notation), `regex_match` (match/replace), `text_diff` (unified diff), `text_translate` (LLM-powered)
  - **Git** — `git_status`, `git_diff`, `git_log`, `git_commit`, `git_branch`, `git_worktree` (no force-push/hard-reset; safe 90% of git usage)
  - **Media** — `image_describe` (vision LLM), `audio_transcribe` (Whisper API)
  - **Communication** — `send_email` (SMTP via aiosmtplib)
  - **Archive** — `compress` (zip/tar.gz)
- **Auto-discovery:** Each module exports `TOOL_METADATA` dict (keys: `tool_id`, `description`, `parameters`, `examples`, `category`, `returns`) and an async callable with the same name as `tool_id`. `get_all_tools()` scans all modules and returns `{tool_id: (function, metadata)}`.
- **Preflight hooks:** Tools can declare an optional `preflight` block in `TOOL_METADATA` for automatic pre-invocation before the LLM sees the message. Keys: `trigger` (`"always"` — every message, or `"pattern"` — only when regex matches), `patterns` (list of regex for pattern trigger), `format` (Python format string applied to tool result dict), `inject_as` (`"system_context"`), `args` (default kwargs). `get_preflight_tools()` returns only hook-enabled tools. `ChatManager._run_preflight_hooks()` executes matching hooks at message-build time and injects results into the system prompt. `current_datetime` uses `trigger: "always"` so the LLM always knows the date without a tool call.
- Auto-registered during server lifespan via `ToolRegistry.register_builtin_tools()` — custom tools can override built-in IDs
- Graceful degradation: optional SDK tools (`pypdf` for `pdf_read`, `duckduckgo-search` for `web_search` fallback, `openpyxl` for `spreadsheet_read`, `openai` for `audio_transcribe`/`image_describe`) skip with warning if SDK not installed
- **Web search provider cascade:** `web_search` checks `DAN_TAVILY_API_KEY` → Tavily (recommended, built for LLM agents); then `DAN_BRAVE_API_KEY` → Brave Search; then DuckDuckGo scraping (zero-config). Each provider auto-falls back to the next on failure.
- Workspace root sandboxing: all file tools enforce `DAN_WORKSPACE_ROOT` boundary
- **Workflow catalog tools** (capability-level): `list_my_workflows`, `search_workflows`, `show_workflow`, `fork_workflow` — browse and duplicate saved workflows from any chat mode

### MCP Tool Bridge (`dan.mcp_bridge`)

- **Consume external MCP servers** as first-class tools alongside the 32 built-ins. Any MCP-compliant server (Stata, R, databases, custom APIs) can be connected and its tools registered into both `ChatCapabilityRegistry` (chat) and `ToolRegistry` (workflow execution).
- **`MCPBridge`** — manages multiple `ClientSession` connections over stdio transport. Per-server `AsyncExitStack` for independent connect/disconnect. Reconnect-on-failure with single retry. `call_tool()` parses `TextContent`/`ImageContent`/`EmbeddedResource` results.
- **Config at `~/.dan/mcp.json`** — Cursor/Claude Desktop compatible format (`mcpServers` key). `DAN_MCP_CONFIG` env var overrides path.
- **Chat commands:** `/mcp install <name>` (pip install + connect + register), `/mcp list`, `/mcp remove <name>`, `/mcp tools [name]`. Dispatched in `Concierge.process()`.
- **Known-server registry:** `KNOWN_MCP_SERVERS` maps short names (e.g., `stata`) to pip packages and commands for one-step install.
- **Tool naming:** `mcp_{server}_{tool}` (e.g., `mcp_stata_run_command`). Category `mcp:{server}` for bulk operations.
- **Startup auto-connect:** servers with `autoConnect: true` in config connect on startup. Failures logged without blocking.
- **Optional dep:** requires `mcp` package (`pip install dan[mcp]`). Module importable without it; `connect()` raises `ImportError` if missing.

### Tool design (Plan 7-5)

- **Generic over domain-specific:** State-of-the-art IDEs (Cursor, Claude Code) use a single generic execution tool; the model generates code, the tool runs it. `run_python(code, **context)` executes model-generated Python with injected context; returns `{ result, stdout, stderr }`. Replaces hardcoded `plot_backtest`/`save_grid_csv` (deprecated). Shared executor in `dan.server.exec`.

### Condition Evaluation

- IfElse/WhileLoop `condition` strings evaluated as Python expressions via restricted `eval()`
- No `__builtins__`; whitelist of safe functions (len, min, max, all, any, etc.)
- Variables populated from upstream port data

### Checkpointing

- `CheckpointStore` protocol with filesystem default (`FileSystemCheckpointStore`)
- Checkpoint written after each topological level completes
- `Engine.resume()` loads checkpoint and continues from pending nodes

### Checkpoint Portals (Phase 8, Plan 13-2)

- **`CheckpointData`** model extends raw checkpoint dict with `graph_revision` (deterministic hash of nodes + edges), `completed_node_ids`, and `node_outputs` — populated on every checkpoint save.
- **`RerunScope`** model defines three rerun scopes: `downstream_of` (target + all downstream nodes), `single_node` (only target with checkpoint inputs), `subgraph` (all nodes in a named sub-graph).
- **`compute_graph_revision_hash(graph)`** — deterministic SHA-256 of graph structure (nodes + edges only; metadata excluded so cosmetic changes do not invalidate).
- **`check_checkpoint_staleness(revision, graph)`** — returns `StalenessResult` with `compatible`, `stale`, `missing_nodes`, and human-readable `message`. Used by API to reject stale reruns with 409.
- **`RunManager.rerun_from_checkpoint()`** — validates scope, checks staleness, rehydrates `PortDataStore` with checkpoint outputs for skipped nodes, marks skipped nodes as `SKIPPED`, creates new `run_id` with provenance. Result metadata tagged with `__rerun_provenance__`.
- **API endpoints**: `GET /api/runs/{id}/checkpoints` (list with staleness), `GET /api/runs/{id}/checkpoints/{cpid}` (detail), `POST /api/runs/{id}/rerun` (partial rerun with `RerunScope` body).

### Variable Inspector (Phase 8, Plan 13-2)

- **`compute_upstream_variables(node_id, graph)`** in `server/variable_inspector.py` — walks incoming edges to collect source node/port names, infer types from output port `json_schema`, and detect unconnected required input ports.
- **API endpoint**: `GET /api/graphs/{graph_id}/nodes/{node_id}/inputs` — returns upstream variable descriptors with optional runtime value enrichment from a specific `run_id`.
- Each variable entry includes: `variable_name`, `source_node`, `source_port`, `type_hint`, `required`, `edge_type`, `connected`.

### Node Test Cases (Phase 8, Plan 13-2)

- **`NodeTestCase`** schema (Pydantic, in `server/test_cases.py`): `id`, `name`, `node_id`, `inputs`, `expected_outputs`, `assertions`, `tags`, `notes`, `created_at`, `updated_at`.

### Direct Execution Architecture (Plan 32-7)

Complex tasks can be executed via two paths:

| Path | When | How |
|------|------|-----|
| **Inline execution** (default) | Natural-language complex tasks: "Research X and summarize Y" | `IntentCompiler.build_graph()` → `Engine.run()` — no codegen |
| **Codegen/sandbox** (fallback) | Explicit workflow building: "Build me a pipeline for…" or when inline fails | `IntentCompiler.compile()` → sandbox exec → `Engine.run()` |

Key components:
- `IntentCompiler.build_graph()` — constructs `Graph` directly by calling builder API in-process
- `DirectBuildError` — typed exception for inline build failures; triggers codegen fallback
- `graph_to_builder_code()` — post-hoc materialization: generates readable builder DSL from an executed Graph
- `_should_execute_inline()` — routing heuristic on `Concierge` class
- `_execute_goal_inline()` — async generator for inline execution with Engine

Config:
- `DAN_DIRECT_BUILD`: `"on"` (default) | `"off"` | `"only"` — controls inline execution
- `DAN_MATERIALIZE_THRESHOLD`: minimum node count for post-hoc script generation (default 3)
- **`TestCaseRunResult`**: `passed`, `actual_outputs`, `expected_outputs`, `diff`, `execution_metadata`, `error`.
- **`TestCaseStore`** — filesystem-backed CRUD at `{base_dir}/test_cases/{workflow_id}/{node_id}.json` (JSON array of test case dicts). Upsert semantics on save.
- **API endpoints**:
  - `GET /api/test-cases/{workflow_id}/{node_id}` — list test cases
  - `POST /api/test-cases/{workflow_id}/{node_id}` — create/update test case
  - `DELETE /api/test-cases/{workflow_id}/{node_id}/{case_id}` — delete test case
  - `POST /api/test-cases/{workflow_id}/{node_id}/{case_id}/run` — execute test case in isolation (builds synthetic single-node graph, runs via RunManager)

## Workflow Builder API (Phase 1.5)

### Builder DSL

```python
from dan.builder import workflow, decompile

paper = workflow("paper_writing")
ideas = paper.llm("idea_gen", model="claude-opus-4", prompt="Generate ideas about {topic}")
outline = paper.llm("planner", prompt=f"Create outline for: {ideas}")
ideas >> outline
graph = paper.build()  # -> validated Graph (dan_graph_v1)
code = decompile(graph)  # -> executable Python that reconstructs the graph
```

### Four Connection Mechanisms

1. **f-string magic**: `prompt=f"Use: {ideas}"` — `NodeRef.__format__` emits a compile-time marker `<<dan:node_id:port>>`. The compiler parses prompts, creates DataEdges, and replaces markers with sanitized input port aliases.
2. **`>>` operator**: `a >> b` — DataEdge from default output to default input. Chainable: `a >> b >> c`.
3. **PortRef passing**: `items=node["port"]` — subscript on NodeRef returns PortRef, resolved at compile time.
4. **Explicit edge**: `wf.edge(a["out"], b["in"])` — fully explicit port-to-port wiring.

Builder also supports typed non-data edges: `wf.control_edge(...)` and `wf.context_edge(...)`, plus graph-level artifacts via `wf.artifact_ref(...)`.

### Sub-Graph Context Managers

```python
with wf.while_loop("loop", condition="x < 5", max_iterations=10) as body:
    body.llm("step", ...)
with wf.for_each("fan", items=node["items"], parallelism=4) as body:
    body.code("proc", ...)
with wf.composite("block") as sub:
    sub.llm("inner", ...)
```

### Node-Type Output Contract Map

Each node type has a known default output port matching the runtime executor (e.g., `llm_operator` -> `text`, `for_each` -> `results`, `if_else` -> `branch`). The compiler uses this map for `>>` wiring and f-string marker resolution. **Mode-aware gate defaults (7-8):** While-mode gates use `continue` (not `true`) for chain wiring; if_else gates use `true`.

### Decompiler

`decompile(graph: Graph) -> str` produces an executable Python module string. Topological sort with deterministic ordering, chain detection for `>>` sugar, context managers for sub-graph nodes, `NodeRef` wrappers for sub-graph edge wiring. Preserves `ui`, `metadata`, `shared_context`, and all edge types.

### Workflow pipeline hardening (7-8)

- **Strict parse mode:** `compile_workflow(path, strict=True)` treats flow parse failures and ambiguous bare-edges as fatal (default `strict=False` for backward compat). Recommended for LLM-generated workflows.

## Visual Editor Backend (Phase 2)

### Server Architecture

Local full-stack: FastAPI backend + React Flow frontend. Runs locally like Jupyter — `dan-serve` or `python -m dan.server` starts the server, open `localhost:8000` in browser.

### Engine Event System & Per-Node Logs

- 14 typed events: `run_started`, `run_completed`, `run_failed`, `node_started`, `node_completed`, `node_failed`, `node_skipped`, `node_output`, `log`, `llm_thinking`, `tool_call_started`, `tool_call_result`, `code_output`, `intermediate_text`
- Opt-in `event_callback` parameter on `Engine` constructor — no events emitted if not set (backward compatible)
- `ExecutionContext.emit_event()` — executors emit rich events (LLM thinking, tool calls, code output) during execution. The engine automatically tags every emitted event with the active `node_id`.
- `ExecutionContext` also exposes public `run_id`, `workflow_id`, and `pii_session_key()` accessors so executors and cross-cutting wrappers can identify a stable session without reaching into private engine fields.
- **Per-Node Log Aggregation:**
  - **Storage:** `RunStore` persists all raw events sequentially to `{run_id}.events.jsonl`, inherently preserving the `node_id` association for every token, tool call, and state change.
  - **Editor Log Panel:** `LogPanel.tsx` groups the event stream by `node_id` (falling back to `"__run__"`). This creates a collapsible, node-centric timeline where all interleaved execution outputs (e.g. parallel branches) are cleanly segregated by their source node.
  - **Chat Run Output:** `RunOutputBlock.tsx` derives a condensed per-node status list from the stream, selectively parsing `node_started`/`completed`/`failed`/`output` events to show high-level node progress and final output snippets directly in the chat, while providing deep-links to the full per-node log history.
- Sub-graph events use parent `run_id` (unified stream) — `_run_subgraph` inherits parent state's run_id
- Events are fire-and-forget; callback failures never break execution

### Run Manager

- Executes `Engine.run()` / `Engine.resume()` as asyncio background tasks
- Accepts `ToolRegistry` — creates `ExecutorRegistry` with pre-configured `ToolExecutor` per run so Engine inherits server-registered tools
- Multiplexes events to WebSocket subscribers via async queues
- Catch-up snapshot on subscribe: current node statuses + buffered recent events (latest 500, rolling window)
- Tracks active/completed runs with status snapshots
- Built-in tools registered in `app.py` lifespan: `save_paper`, `search_papers`, `citation_verifier`, `check_latex_deps`, `compile_latex`, `package_submission` (paper-writing workflow)
- `compile_latex` hardening: auto-bootstrap `informs3.cls` into `output/`, normalize LaTeX preamble for `plainnat` compatibility (`hyperref`, `\newblock`), and auto-fill missing BibTeX citation keys with placeholder entries before `pdflatex`/`bibtex` passes

### API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/graphs` | List all graphs + last opened |
| POST | `/api/graphs` | Create new graph |
| GET | `/api/graphs/{id}` | Load graph JSON |
| PUT | `/api/graphs/{id}` | Save graph JSON |
| DELETE | `/api/graphs/{id}` | Delete graph |
| POST | `/api/graphs/{id}/nodes/{nid}/add-boundary-validators` | Insert entry/exit validator nodes around a composite |
| POST | `/api/graphs/{id}/apply-mutation` | Apply chat-generated mutation plan (GraphMutator.apply), persist, return new graph |
| POST | `/api/graphs/{id}/validate` | Validate graph (design-time checks), return errors/warnings |
| GET | `/api/graphs/{id}/export/markdown` | Export graph as markdown workflow |
| GET | `/api/graphs/{id}/export/python` | Export graph as Python builder code |
| GET | `/api/metrics/mutations` | Get mutation quality metrics (apply_success_rate, etc.) |
| POST | `/api/metrics/mutations/reset` | Reset mutation metrics |
| GET | `/api/rag/collections` | List RAG collections |
| POST | `/api/rag/collections` | Create collection with documents |
| GET | `/api/rag/collections/{name}/stats` | Collection stats |
| POST | `/api/rag/collections/{name}/documents` | Add documents |
| DELETE | `/api/rag/collections/{name}` | Delete collection |
| POST | `/api/runs` | Start execution |
| POST | `/api/runs/{id}/resume` | Resume checkpointed run |
| GET | `/api/runs/{id}/checkpoints` | List checkpoint markers with staleness info |
| GET | `/api/runs/{id}/checkpoints/{cpid}` | Checkpoint detail (completed nodes, output keys) |
| POST | `/api/runs/{id}/rerun` | Partial rerun from checkpoint with RerunScope |
| GET | `/api/runs/{id}` | Get run status snapshot |
| GET | `/api/runs` | List all runs |
| WS | `/api/runs/{id}/events` | Live event stream |
| POST | `/api/chat/message` | Send chat message, get streaming response |
| WS | `/api/chat/{channel_id}/events` | Chat token streaming |
| GET | `/api/chats/{workflow_id}` | List chat threads |
| GET | `/api/chats/{workflow_id}/{thread_id}` | Load chat thread |
| POST | `/api/chats/{workflow_id}` | Create chat thread |
| PUT | `/api/chats/{workflow_id}/{thread_id}` | Update chat thread |
| DELETE | `/api/chats/{workflow_id}/{thread_id}` | Delete chat thread |
| POST | `/api/runs/scoped` | Start scoped run (full/node/subgraph) |
| POST | `/api/graphs/{id}/publish` | Publish workflow to API registry |
| POST | `/api/graphs/{id}/unpublish` | Remove from publish registry |
| GET | `/api/graphs/{id}/publish-status` | Check if workflow is published |
| GET | `/api/blocks` | List installed blocks |
| GET | `/api/blocks/{name}` | Block info + README |
| POST | `/api/blocks/import` | Import block from path/URL |
| POST | `/api/blocks/export/{graph_id}` | Export workflow as block |
| POST | `/api/blocks/export/{graph_id}/{node_id}` | Export composite node as block |
| DELETE | `/api/blocks/{name}/{version}` | Uninstall a block |
| POST | `/api/adapters/start` | Start messaging adapter |
| POST | `/api/adapters/stop` | Stop adapter |
| GET | `/api/adapters/status` | List running adapters |
| GET | `/api/published/{wf_id}/events` | SSE stream for published workflow |
| WS | `/api/published/{wf_id}/ws` | Bidirectional WebSocket for published workflow |
| GET | `/health` | Server health check for discovery |
| POST | `/api/gateway/dispatch` | Unified workflow dispatch from any surface |
| POST | `/api/gateway/cancel` | Cancel a running workflow |
| GET | `/api/gateway/activity` | Activity snapshot (active/recent runs, surfaces) |
| GET | `/api/gateway/surfaces` | List connected surfaces |
| POST | `/api/gateway/surfaces/register` | Register a surface |
| GET | `/api/gateway/pending-inputs` | List all pending HumanNode inputs |
| POST | `/api/gateway/submit-input` | Submit HumanNode response from any surface |
| WS | `/api/gateway/events` | Global event bus (all runs, filterable) |

### Graph Persistence

- Filesystem-based: JSON files in `./graphs/` directory
- `last_opened` tracking for auto-load on editor open
- `dan_graph_v1` JSON contract unchanged — the backend reads/writes the same format
- **Server-side layout** — `GET /api/graphs/{id}?layout=true` (or `DAN_LAYOUT_ON_LOAD=1`) applies topological layout. Optionally flattens while-loop body composites (`flatten_loop_bodies`) for a flat view; disable with `DAN_FLATTEN_LOOP_BODIES=0`. No example-specific logic.

## Visual Editor Frontend (Phase 2)

### DAN <-> React Flow Adapter

Bidirectional conversion layer (`graphAdapter.ts`):
- DAN `input_ports`/`output_ports` map to React Flow handles via `port:<name>` ID convention
- 3 edge types visually differentiated: data (indigo), control (amber), context (emerald, animated)
- All 16 node types rendered through a single `DanNode` custom component with per-type color coding
- Node execution status shown as colored rings (yellow=running, green=completed, red=failed)

### UI Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  EditorToolbar (graph selector, run controls, auto-layout)       │
├──────┬─────────────────────────────┬──────────────┬─────────────┤
│      │                             │              │             │
│ Node │      GraphCanvas            │  Config      │   Chat      │
│Palette│   (React Flow + minimap)   │  Panel       │   Panel     │
│      │                             │              │             │
│      ├─────────────────────────────┤              │             │
│      │ Logs | Output               │              │             │
│      │ (tab bar + scrolling panel) │              │             │
└──────┴─────────────────────────────┴──────────────┴─────────────┘
```

### Multi-Layered Graph Navigation (Phase 3.5-A + 7-7 Hardening)

- **CompositeExecutor** — backend executor that maps input/output ports and delegates to `run_subgraph`; supports node-aware mapping format (`nodeId::portName`) for targeted per-entry-node input injection (backward compatible with legacy flat mappings); registered in scheduler alongside WhileLoop/ForEach
- **`_run_subgraph` targeted injection** — optional `targeted_inputs: dict[str, dict[str, Any]]` parameter routes inputs to specific entry-point nodes instead of broadcasting to all entries; solves routing collisions when multiple entry nodes share port names
- **`is_blackbox`** field on CompositeNode — when true, node is opaque (no drill-in, no sub-graph preview)
- **Canvas drill-in** — double-click composite/while_loop/for_each nodes to navigate into their sub-graph; read-only (no edits while drilled in)
- **`resolveGraphAtStack(root, stack)`** — single source of truth for nested graph resolution. Walks the layer stack by traversing `sub_graphs` at each depth level. Returns `null` on invalid path or depth > `MAX_DRILL_DEPTH` (3). All navigation/save code paths (`drillIn`, `drillOut`, `jumpToLayer`, `saveGraph`, `PortMappingOverlay`) use this helper — no ad-hoc `sub_graphs[key]` lookups.
- **`deepSetSubGraph(root, stack, updatedSub)`** — immutable deep update: produces a new root `DanGraph` with the sub-graph replaced at the depth indicated by the layer stack. Used by `saveGraph` for nested save.
- **Depth-3 cap** — `MAX_DRILL_DEPTH = 3` (root → level-1 → level-2 → level-3). `resolveGraphAtStack` returns `null` beyond this; callers auto-reset to root and show toast. BreadcrumbBar visually indicates max depth.
- **Loop feedback arrows** — when drilling into `while_loop` or `for_each`, synthetic dashed edges (tagged `data.synthetic=true`) are injected from exit-point output ports back to entry-point input ports by name matching; generic fallback arrow when names don't match; `saveGraph()` filters out synthetic edges before serialization
- **`layerStack`** in Zustand store — tracks navigation depth; `drillIn`/`drillOut`/`jumpToLayer` actions recompute React Flow nodes/edges from `danGraph.sub_graphs`
- **BreadcrumbBar** — "Root > Node1 > Node2" navigation bar; each segment clickable
- **PortMappingOverlay** — shows input/output port mappings when viewing a composite node's sub-graph
- **Animated zoom** — CSS fade-in + `fitView()` on layer change

### Port Ordering & Edge Routing (Plan 7-7)

- **`orderPorts(ports, edges, nodes, nodeId, direction, nodeType?, portReorder?)`** — deterministic display-only sort in `portOrdering.ts`. Scoring bands (non-overlapping): P0 gate pins (0–9, `true`/`continue` → 0, `false`/`done` → 1), P1 connected ports (1000–1999, peer Y clamped to [0,999]), P2 unconnected (10000+, alphabetical sub-sort). Alphabetical tie-breaker. Used in `DanNode.tsx`; `ConfigPanel` keeps raw authoring order.
- **`computePortReorder`** — crossing minimization heuristic, runs once post-layout. Results passed as `portReorder` hint to `orderPorts` for P1-band sub-sorting.
- **Edge routing optimizations** — dagre port-aware edge weights, per-port smoothstep offsets, data-edge label deduplication, crossing minimization via port reorder.

### Live Execution Visualization (Phase 3.5-B)

- **Node pulse/glow** — CSS `@keyframes dan-node-pulse` on active nodes; completion flash animation
- **Duration badges** — per-node "123ms" / "1.2s" shown on completed nodes; tracked via `nodeTimings` in store
- **AnimatedEdge** — custom React Flow edge with SVG particle flow (`<animateMotion>`) on active edges (source completed → target started); dimming on inactive edges
- **Execution path highlighting** — nodes without status dimmed to `opacity-40` during runs
- **ExecutionTimeline** — horizontal bar with colored segments per node (ordered by start time); click to select node

### Rich Logging (Phase 3.5-C)

- **5 new event types** — `LLM_THINKING`, `TOOL_CALL_STARTED`, `TOOL_CALL_RESULT`, `CODE_OUTPUT`, `INTERMEDIATE_TEXT`
- **`ExecutionContext.emit_event()`** — executors emit structured events during execution
- **Unified run stream** — sub-graph events inherit parent `run_id`; single WebSocket subscription per run
- **LogPanel rebuild** — grouped by node_id with collapsible sections, sub-grouped by `EVENT_CATEGORY` (thinking/tool/output/error/lifecycle), inline SVG icons, color coding, text/node/type filtering, click-to-select-node

### Build Palette (Phase 3.5-D)

- **Searchable sidebar** — text input filters NODE_TYPE_CATALOG; collapsible category sections
- **Template factories** — extensible `TemplateResult` contract (`{ node, rootSubGraphKey, subGraphs }`); ReAct and Plan-Execute pre-built templates
- **Edge type selector** — compact toggle (Data/Control/Context) using EDGE_COLORS; `onConnect` creates edges with selected type
- **MCP placeholders** — disabled entries with "Coming soon" badge
- **Hover previews** — `NODE_DESCRIPTIONS` with port info shown on tooltip

### UI/UX Polish (Phase 3.5-E)

- **Toast notifications** — Zustand slice (`addToast`/`removeToast`); all async actions wrapped with success/error toasts
- **Loading states** — `loadingGraph`/`savingGraph` flags; `Spinner.tsx` component
- **Connection validation** — `isValidConnection` (no self-connect, no duplicates, port existence)
- **Merged toolbar** — `EditorToolbar.tsx` combines GraphSwitcher + RunPanel into one bar (DAN branding, graph selector, save/run/resume, status, auto-layout)
- **Node type icons** — inline SVG icons for all 16 node types (in DanNode header and palette)
- **Keyboard shortcuts** — Cmd/Ctrl+S → save
- **Edge labels** — data edges show `source_port → target_port`
- **Auto-layout** — dagre-based (LR direction, `applyAutoLayout` store action)

### Node & Port Editing (Phase 3.75-C)

- **Port editor** — `ConfigPanel.tsx` renders editable port rows per node (input and output). Each row: name input (commit-on-blur), required checkbox (input only), delete button. "Add Port" button appends with auto-generated unique name (`input_N`/`output_N`). Validation: no duplicates, no empty names (inline red styling).
- **Atomic port rename** — `renamePort` store action updates the port name on the node AND all connected edges' `source_port`/`target_port` + React Flow `sourceHandle`/`targetHandle` in a single `pushSnapshot` (one undo step).
- **Port delete with edge cleanup** — `deletePort` store action removes the port and filters out all edges referencing it.
- **Inline node rename** — double-click the name span in `DanNode.tsx` header to enter edit mode (controlled `<input>`, transparent background matching header style). Enter/blur commits via `updateNodeData`; Escape reverts. `stopPropagation` prevents composite drill-in. Auto-select text via ref + useEffect.
- **Output schema editor** — `SchemaEditor` component (inline in ConfigPanel) for `llm_operator` and `router` nodes. Visual mode: property rows (name, type dropdown, required checkbox, delete). Raw JSON mode: textarea with parse-on-blur. Toggle between modes; invalid JSON blocks switch to visual. Empty/null schema initializes as `{type: "object", properties: {}}` on first visual switch.

## Conversational Workflow Authoring (Phase 7)

### Chat Panel
- Resizable right-side panel with streaming LLM responses
- Graph-aware system prompt: serializes current workflow as `GraphSummary` for LLM context
- `@` mention system: reference nodes, workflows, sub-graphs with Cursor-style autocomplete
- Thread management: per-workflow persistent chat history, thread list, auto-restore
- **Context window management:** `compact_history()` transparently compacts chat history to fit within model context window. 4-phase sliding window: (1) system prompt always kept, (2) recent N messages in full, (3) older assistant messages truncated (first + last sentence), (4) oldest dropped. `MODEL_CONTEXT_WINDOWS` lookup table (20 models). Configurable via `DAN_CHAT_MAX_CONTEXT_RATIO` (default 0.8) and `DAN_CHAT_RECENT_MESSAGES` (default 10). Token counting via `tiktoken` with `len//4` fallback. Header shows "~Xk / Yk" context usage indicator.

### NL→Graph Mutation Engine
- `GraphMutator` applies atomic operations (add/remove/edit nodes and edges) to graph dicts
- LLM function-calling: `plan_graph_mutations` tool returns structured `MutationPlan`
- Transactional by default (`all_or_nothing`); partial apply is opt-in
- Optimistic concurrency via `base_graph_revision` / hash matching
- `GraphDiffPreview` shows visual diff before applying; accept/reject/partial-accept
- **Validation gate:** After `GraphMutator.apply()` succeeds, the `apply-mutation` endpoint runs `Graph.model_validate()` + `validate_graph()` before persisting. Fatal validation errors reject the apply; warnings are returned alongside the saved graph.
- **Auto-retry:** If the LLM's mutation plan fails dry-run validation, the chat manager feeds the errors back to the LLM for one correction attempt before surfacing the failure to the user.
- **`TOOL_PORT_MANIFESTS`** — tool-specific port declarations for 10 common tools (`file_read`, `list_directory`, `pdf_read`, `compile_latex`, `save_paper`, `package_submission`, `citation_verifier`, `check_latex_deps`, `rag_index_documents`, `web_search`). Used by `_default_ports` to auto-declare input/output ports for `tool_operator` nodes by `tool_id`.
- **`ApplySkill` mutation op** — targets nodes by ID or `metadata.tags`; injects domain-specific prompt prefixes from `SKILL_LIBRARY` (in `skill_library.py`) into `system_prompt` (or `prompt_template` fallback). Skills: `management_science_writing`, `informs_latex_style`.
- **Mutator diagnostics (7-8):** When `add_edge` auto-creates a missing target port, a diagnostic is emitted. Optional `strict=True` on the op fails instead of auto-creating.
- **`clarify_intent()`** — `ChatManager` method that detects underspecified build-mode intents and asks the user for clarification before planning.

### Build-from-Intent Mode
- **Two-mode chat:** `ChatMessageRequest.mode` accepts `"mutate"` (default) or `"build"`. Mode `"build"` uses `BUILD_FROM_INTENT_PROMPT` (intent-first workflow creation); `"mutate"` uses `SYSTEM_PROMPT_TEMPLATE` (graph-aware editing). Empty graphs auto-switch to build mode regardless of the `mode` parameter.
- **Intent-first prompt:** `BUILD_FROM_INTENT_PROMPT` guides the LLM through task decomposition (goal → stages → node types → data flow), references the pattern library (chain, review_loop, fan_out, rag_qa), and maps common intents to patterns (paper writing → review_loop + chain, RAG QA → rag_qa).
- **Template registry:** `WORKFLOW_TEMPLATES` dict maps template names (paper_writing, rag_qa, chain_3) to pre-built `expand_pattern` operation sequences. Templates reduce LLM variability for common workflows.
- **Empty-graph bootstrap:** `build_graph_summary` handles empty graphs (nodes=[], edges=[]) — returns valid `GraphSummary` with `node_count=0` and a deterministic revision hash. `base_graph_revision` is injected from the empty graph state so the mutator's stale-plan check works for build-from-scratch.
- **Editor UX:** "Build with AI" entry point in TabBar creates a blank graph and opens the chat in build mode. After the LLM returns a mutation plan, the editor shows a diff preview (empty → new graph), and auto-switches to mutate mode on apply.

### Scoped Execution from Chat
- `/run`, `/run-node @Node`, `/run-subgraph @Node` commands in chat
- `build_scoped_graph()` derives minimal executable graphs for node or subgraph scopes
- Run events stream back into chat thread as status blocks

### Session-Scoped Rollback
- Each mutation records a frontend-only `historyCursor` marker
- "Revert to here" walks the undo stack; markers cleared on page reload

## Key Decisions

- **Build, don't buy.** Existing tools (Langflow, Flowise, Dify) cannot handle while-loops, composable sub-graphs, or typed edges natively. See development-plan.md sections 3-4 for full analysis.
- **Three authoring surfaces, one IR.** Python builder DSL (most programmable), markdown agent files (most accessible), and visual editor (most interactive) all compile to the same `dan_graph_v1` JSON. They coexist — users pick the surface that fits. Python and markdown are file-based and version-controllable; the visual editor is for interactive exploration and debugging.
- **Language split.** Python for orchestration runtime and validation; TypeScript for the visual editor and interaction layer.
- **Roadmap resequencing.** Build the core engine first, then immediately build a full visual editor baseline to test the system early via UI.
- **Hierarchical plan numbering.** Plan files use hierarchical numbering (`1-name`, `1-1-name`, `1-1-1-name`) to mirror the task tree.
