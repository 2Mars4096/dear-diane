# Changelog

## 2026-02-24 (README + tracking rule)
- [docs] Created `README.md` — project overview, quick start, builder DSL examples, visual editor features, API endpoints, roadmap
- [docs] Updated `.cursor/rules/project-tracking.mdc` — added `README.md` to document inventory and "After Each Modification" checklist (update on new features, setup changes, CLI commands, roadmap milestones)

## 2026-02-24 (Phase 3.75 roadmap + MVP fixes)
- [docs] Added Phase 3.75 — Visual Editor Full Editing to `todo.md`: 13 items covering port editor, inline rename, edge reconnection, copy/paste, undo/redo, multi-select, context menu, sub-graph creation from selection, schema editor, validation feedback, import/export, node search
- [fix] Sub-graph editing: removed read-only guards from GraphCanvas, ConfigPanel, NodePalette, BreadcrumbBar; `saveGraph` is now layer-aware (writes to correct `sub_graphs[key]`)
- [fix] Auto-layout on graph load: detects degenerate positions (all nodes at 0,0) and applies dagre layout automatically
- [fix] Server `.env` loading: added `load_dotenv()` to `app.py` so `DAN_LLM_API_KEY` is picked up from `.env`
- [fix] `RunInputsDialog`: replaced `useEffect`-based variable detection with synchronous `useMemo` to prevent premature auto-run before variables are computed

## 2026-02-24 (MVP — end-to-end runnable from UI)
- [feat] **Server-side tool registry:** `RunManager` now accepts a `ToolRegistry` parameter; engines created for runs use it. `app.py` lifespan registers `save_paper` as a built-in tool. Paper-writing workflow now runs end-to-end from the visual editor.
- [feat] **Run-inputs dialog:** `RunInputsDialog.tsx` — modal that detects `{variable}` template placeholders from entry node prompts + unconnected input ports. Shows a form before execution so users can provide workflow inputs (e.g. `topic` for paper writing). Graphs with no inputs run immediately. Cmd/Ctrl+Enter shortcut to submit.
- [refactor] `EditorToolbar.tsx`: Run button now opens `RunInputsDialog` instead of calling `startRun()` directly
- [infra] 256 backend tests passing, 0 TypeScript errors

## 2026-02-24 (Phase 3.5 — full implementation)
- [feat] **5-1 Multi-Layered Graph Navigation:**
  - Backend: Added `is_blackbox: bool = False` to `CompositeNode`; created `CompositeExecutor` (input/output mapping + single `run_subgraph` call); registered in scheduler
  - Frontend: `layerStack` + `drillIn`/`drillOut`/`jumpToLayer` in Zustand store; double-click drill-in on composite/while_loop/for_each nodes; read-only guards when drilled in; `BreadcrumbBar.tsx` (Root > Node > Node navigation); `PortMappingOverlay.tsx` (input/output port mapping display); deleted `CompositePreview.tsx` modal; CSS fade-in animation
  - Tests: `test_composite_executor.py` (4 tests, all passing)
- [feat] **5-2 Live Execution Visualization:**
  - `nodeTimings` + `activeExecutionPath` in store; `handleRunEvent` tracks start/end timestamps per node
  - `DanNode.tsx`: CSS pulse animation on active nodes, completion flash, duration badges (e.g. "123ms", "1.2s"), opacity dimming for inactive nodes during runs
  - `AnimatedEdge.tsx`: custom React Flow edge with SVG particle flow on active edges (source completed → target started), dimming for inactive edges; registered as `smoothstep` override
  - `ExecutionTimeline.tsx`: horizontal timeline bar with colored segments per node, click-to-select; mounted above bottom tabs
- [feat] **5-3 Rich Logging Window:**
  - Backend: 5 new `EventType` values (`LLM_THINKING`, `TOOL_CALL_STARTED`, `TOOL_CALL_RESULT`, `CODE_OUTPUT`, `INTERMEDIATE_TEXT`); `emit_event` on `ExecutionContext`; unified parent `run_id` for sub-graph events; rolling latest-500 event buffer (was first-500)
  - LLMExecutor emits `LLM_THINKING` (model, prompt preview); ToolExecutor emits `TOOL_CALL_STARTED`/`TOOL_CALL_RESULT`; CodeExecutor captures stdout/stderr via redirect and emits `CODE_OUTPUT`
  - Frontend: `EVENT_CATEGORY` mapping; rebuilt `LogPanel.tsx` with grouped-by-node sections, sub-grouped by category, inline SVG icons (brain, wrench, terminal, X), color coding, text/node/type filtering, click-to-select, auto-scroll
- [feat] **5-4 Build Palette:**
  - `paletteTemplates.ts`: extensible template factory (`TemplateResult` with `subGraphs: Record<string, DanGraph>`); ReAct template (WhileLoop + LLM→Tool body); Plan-Execute template (Composite + Planner→Executor body)
  - `selectedEdgeType` + `addTemplateNode` in store; `onConnect` uses selected edge type with proper styling (color, label, animation for context edges)
  - Rebuilt `NodePalette.tsx`: search input, collapsible categories (Operators, Control Flow, Pre-defined Agents, MCP/Wrapped Agents, Composite), template drag-drop with `template:` prefix, disabled MCP placeholders, compact edge type selector, `NODE_DESCRIPTIONS` hover tooltips
- [feat] **5-5 UI/UX Polish:**
  - Toast system: `ToastContainer.tsx` (fixed bottom-right, slide-in, auto-dismiss 4s/6s); `addToast`/`removeToast` in store; all async actions wrapped with success/error toasts
  - Loading states: `Spinner.tsx`; `loadingGraph`/`savingGraph` flags in store
  - Connection validation: `connectionValidation.ts` (no self-connect, no duplicates, port existence); `isValidConnection` prop on ReactFlow
  - Merged toolbar: `EditorToolbar.tsx` combining GraphSwitcher + RunPanel (DAN branding, graph selector, save/run/resume/disconnect, status badge, auto-layout button); replaced both components in App.tsx
  - Node icons: `nodeIcons.tsx` (inline SVG for all 10 types); added to `DanNode.tsx` header
  - Keyboard shortcuts: `useKeyboardShortcuts.ts` (Cmd/Ctrl+S → save)
  - Edge labels: data edges now show `source_port → target_port`
  - Auto-layout: `layout.ts` using dagre (LR, nodesep 40, ranksep 60); `applyAutoLayout` in store
  - Favicon: updated title + `favicon.svg`; ConfigPanel: larger textareas, editable edge config
- [infra] All changes validated: 256 backend tests passing, 0 TypeScript errors, 0 lint errors

## 2026-02-24 (Phase 3.5 plan consistency pass)
- [docs] Normalized all 5 sub-plans for consistent formatting: bold-keyword Notes (5-1), standardized "Stretch:" label for deferred items (all plans), unified "Docs sync" task naming with architecture.md → todo.md → changelog.md order (all plans), removed V1/V1.1 version labels (5-4), aligned test paths to existing `tests/test_*` layout (5-1, 5-3)

## 2026-02-24 (Phase 3.5 plan refinements — decision lock)
- [docs] Updated `plans/5-phase-3.5-frontend-design.md` shared decisions: locked unified parent `run_id` event stream for sub-graphs, navigation-only/read-only scope for 5-1 drill-in, and simple-first-but-extensible template strategy for 5-4
- [docs] Updated `plans/5-1-multi-layered-graph.md`: clarified read-only drill-in MVP, added explicit UI guardrails to disable edits while inside nested layers, and updated test scope accordingly
- [docs] Updated `plans/5-3-rich-logging.md`: added tasks for parent `run_id` reuse in `_run_subgraph`, hierarchy metadata tags (`graph_key`, `layer_path`, `parent_node_id`), and rolling latest-500 event buffer validation
- [docs] Updated `plans/5-4-build-palette.md`: changed template factory contract to support multi-level sub-graphs via extensible `subGraphs` payload while keeping V1 template implementations simple

## 2026-02-24 (Phase 3.5 — detailed sub-plans)
- [docs] Created top-level plan `plans/5-phase-3.5-frontend-design.md` — dependency graph, sequencing recommendation, shared decisions across all 5 sub-plans
- [docs] Created `plans/5-1-multi-layered-graph.md` — CompositeExecutor, `is_blackbox`, canvas drill-in replacing modal, breadcrumb bar, animated transitions, port mapping visualization (7 tasks, 23 sub-tasks)
- [docs] Created `plans/5-2-live-execution-viz.md` — pulse/glow CSS animations, animated edges with particles, execution path dimming, timeline/playback scrubber, duration badges (7 tasks)
- [docs] Created `plans/5-3-rich-logging.md` — 5 new backend event types, executor emission, structured collapsible log UI, icons/colors, click-to-select, filtering (6 tasks, 22 sub-tasks)
- [docs] Created `plans/5-4-build-palette.md` — searchable categorized sidebar, ReAct/Plan-Execute agent templates, MCP placeholder, edge type selector, hover preview tooltips (7 tasks)
- [docs] Created `plans/5-5-ui-polish.md` — error handling/toasts, loading states, connection validation, toolbar merge, resizable panels, keyboard shortcuts, auto-layout, syntax highlighting (13 task groups)
- [docs] Updated `todo.md` — replaced inline Phase 3.5 bullet lists with linked sub-plan references

## 2026-02-24 (todo restructure — Phase 3.5 Frontend Design)
- [docs] `todo.md`: added Phase 3.5 — Frontend Design with 5 sub-groups (A. Multi-Layered Graph Navigation, B. Live Execution Visualization, C. Rich Logging Window, D. Build Palette, E. UI/UX Polish)
- [docs] `todo.md`: absorbed old Phase 4 (Composite Nodes) into Phase 3.5-A, old Phase 5 (Execution Visualization) into Phase 3.5-B, old Phase 2.5 non-bug-fix items into Phase 3.5-E
- [docs] `todo.md`: removed Phase 2.5 section (completed bug fixes retained in changelog; remaining items moved to Phase 3.5-E); renumbered Phase 6 → Phase 4
- [docs] `architecture.md`: updated Composite Node Preview section — replaced "Phase 4" reference with Phase 3.5-A drill-in navigation plan
- [docs] Plan file created: `phase_3.5_frontend_design_99e66334.plan.md` with full breakdown, review fixes (sub-plan split guidance, typed event contract lockstep note, `is_blackbox` field), and documentation checklist

## 2026-02-24 (editor build cleanup — TypeScript fixes)
- [fix] `App.tsx`, `DanNode.tsx`, `CompositePreview.tsx`: replaced unsafe `Record<string, unknown>` casts with explicit node-type narrowing for `body_graph` access
- [fix] `NodePalette.tsx`: replaced `Object.groupBy` with typed `reduce` grouping to remove ES2024 dependency and fix strict TypeScript inference errors
- [fix] `useGraphStore.ts`: removed unused `portHandleId` import and unused `EMPTY_GRAPH` constant to satisfy strict compile checks
- [test] `editor/`: `npm run build` now passes (TypeScript + Vite build successful); only Node.js version warning remains (20.17 vs Vite recommended 20.19+)
- [docs] `bugs.md`: moved `Object.groupBy` issue out of Known Limitations and into Resolved Bugs
- [docs] `todo.md`: added and checked off "Fix editor TypeScript build blockers" item in Phase 2.5

## 2026-02-24 (Phase 2.5 — fix 5 open editor bugs)
- [fix] `GraphCanvas.tsx`: replaced manual `clientX - bounds.left` drop position calculation with `screenToFlowPosition()` from `useReactFlow()` — nodes now land correctly when canvas is zoomed/panned
- [fix] `graphAdapter.ts`: replaced module-level `_counter` with `Date.now()` + random suffix for node IDs — eliminates collisions after page refresh
- [fix] `GraphCanvas.tsx`: removed wrapper `onKeyDown` handler and `tabIndex`, added `deleteKeyCode={["Delete", "Backspace"]}` prop to `<ReactFlow>` — delete key now fires reliably via React Flow's native handling
- [fix] `useGraphStore.ts`: extracted `Date.now()` to a single `edgeId` const in `onConnect` — React Flow edge ID and `danEdge.id` are now always identical
- [fix] `App.tsx`: changed `hasBodyGraph` from `useCallback` to `useMemo`, updated call site to use the memoized boolean directly — avoids redundant recomputation on every render
- [fix] `GraphCanvas.tsx`: removed unused `danNodeToReactFlow` import
- [docs] `bugs.md`: moved all 5 bugs from "Open Bugs" to new "Resolved Bugs" section
- [docs] `todo.md`: checked off 5 bug-fix items in Phase 2.5

## 2026-02-24 (visual editor review — bugs + polish backlog)
- [docs] `bugs.md`: added 5 open bugs found during frontend code review — drop position wrong when zoomed/panned, node ID collisions after page refresh, delete key unreliable, onConnect edge ID mismatch, hasBodyGraph useCallback/useMemo
- [docs] `bugs.md`: added 9 known limitations — no error feedback, no loading states, no connection validation, ConfigPanel generic field dump, fixed-height bottom panel, two header bars, no keyboard shortcuts, app title/favicon defaults
- [docs] `todo.md`: added Phase 2.5 (Visual Editor Polish) with 19 MVP-critical items covering bug fixes, UX improvements, and visual polish

## 2026-02-24 (Phase 3)
- [feat] Phase 3 — Paper-Writing Proof of Concept (Extended):
  - `examples/paper_writing.py`: end-to-end workflow using builder DSL (~200 lines)
    - 8 nodes: LLMOperator x3 (idea_gen, lit_survey, outline_planner), ForEach (section_writers with parallel section writing), CodeOperator x2 (assembler, format_output), WhileLoop (review_loop with structured review-and-revise), ToolOperator (save_paper)
    - Structured output normalization on outline_planner (JSON schema → title, abstract, sections) and review_and_revise (verdict, feedback, draft)
    - Explicit ToolRegistry wiring: custom ExecutorRegistry with pre-registered save_paper tool function
    - Configurable CLI: topic, max review iterations, verbose logging
    - Event callback for live progress tracking
    - Saves compiled graph JSON to `graphs/paper_writing.json` and paper output to `output/`
  - `tests/test_examples/test_paper_writing_e2e.py`: 9 tests covering 3 categories
    - Happy-path mock e2e (6 tests): graph compilation, node types, entry/exit, JSON round-trip, full mock run, section count
    - Tool-failure recovery (2 tests): tool exception → FAILED status, unknown tool_id → FAILED status
    - Checkpoint/resume (1 test): full run with filesystem checkpointing, verify checkpoint files created, resume succeeds
  - Live-tested against vectorengine.ai with claude-sonnet-4-6: all 8 nodes completed, outline needed 2 normalization attempts, review loop ran 2 iterations, paper saved to output/
- [test] 9 new tests (252 total)
- [docs] Created `docs/plans/4-phase-3-paper-writing.md` with task breakdown
- [docs] Updated `todo.md`: marked Phase 3 complete, added 3 reliability backlog items (tool retry, per-node retry policy, handoff validators)
- [docs] Updated `architecture.md`: added `examples/` directory to tree

## 2026-02-24 (architecture expansion — hyperedges, HumanNode, context scoping, applications)
- [docs] `architecture.md`: added "Context Scoping Across Agent Boundaries" — four scopes (global, local, pass_down, emit_up) with explicit schemas at agent boundaries, upward signals (sticky/non-sticky), revised agent boundary contract (accepts, returns, reads_global, writes_global, signals)
- [docs] `architecture.md`: added "Hyperedges: Skills and Rules" — skills and rules modeled as hyperedges attaching to multiple nodes. Four types (skill, guardrail, style, override), attachment scope (node ID, type, tags, subgraph), precedence rules, execution hooks (pre_prompt, tool_call, post_output, validation)
- [docs] `architecture.md`: added "HumanNode (Generalized)" — human as a first-class node type, not external to the graph. Chat UI as renderer for HumanNode I/O. Adjustable autonomy via topology. Background mode = zero HumanNodes.
- [docs] `architecture.md`: added "Four Top-Level Agents Architecture" — Ask, Agent, Debug, Plan as independent DAN networks sharing a common context layer. Mode switching via shared context serialization.
- [docs] `development-plan.md`: added Section 6 "Applications: Rebuilding Real Systems on DAN" with two subsections:
  - 6.1 Coding Assistant (Cursor-like): ~15 node types, every mode as a graph template, four top-level agents with shared context
  - 6.2 Research IDE (science-cursor): scholar engines as DAN agents, PaperOrchestrator as a DAN network, rebuild strategy
- [docs] `todo.md`: added 5 backlog items — hyperedges, HumanNode generalization, context scoping, coding assistant PoC, science-cursor rebuild

## 2026-02-24 (docs review fixes)
- [docs] `architecture.md`: moved "Workflows as Code" out of Phase 1 section into its own "Phase 1.5 — not yet built" section (was misleading — `dan.builder` doesn't exist yet)
- [docs] `architecture.md`: fixed checkpointing description — "per topological level" not "per node"; removed stale "Targeted for Phase 1"
- [docs] `architecture.md`: removed duplicate frontend tech stack subsection (already in top-level Tech Stack)
- [docs] `architecture.md`: added missing files to directory tree (`__init__.py` files, `editor/package.json`, `vite.config.ts`, `tsconfig.json`)
- [docs] `architecture.md`: removed hardcoded test count from directory tree
- [docs] `changelog.md`: reordered to consistent newest-first (Phase 0 was at top despite being oldest)
- [docs] `development-plan.md`: fixed stale "Policies (to be defined in Phase 0)" → "(defined in Phase 0)"
- [docs] `development-plan.md`: updated Section 4 recommendation — struck through completed items, changed tense to past
- [docs] `bugs.md`: moved Node.js version warning and `Object.groupBy` from "Open Bugs" to "Known Limitations" (environment notes, not code bugs)
- [docs] `todo.md`: renumbered Phase 1.5 plan ID from `8:` to `1-5:` so future plan file sorts correctly between Phases 0 and 1

## 2026-02-24 (post-Phase 2 docs sync)
- [docs] Updated `architecture.md` tech stack — added FastAPI, Zustand, Tailwind CSS, pytest/httpx to explicitly list all dependencies
- [docs] Updated `development-plan.md` roadmap table — replaced "Est. Effort" with "Status" column, marked Phases 0/1/2 as Done with test counts
- [docs] Populated `bugs.md` — added Node.js version warning, `Object.groupBy` ES2024 requirement, known limitations (no editor-side validation, single-user, no undo/redo)

## 2026-02-24 (Phase 2)
- [feat] Phase 2 — Visual Editor full-stack implementation:
  - Engine event system: 9 typed events (`EngineEvent`, `EventType`), opt-in `event_callback` on `Engine`, backward compatible
  - Run manager (`server/run_manager.py`): background task execution, event pubsub via async queues, catch-up snapshots on subscriber reconnect
  - FastAPI backend (`server/app.py`): graph CRUD (list, create, get, update, delete), run endpoints (start, resume, status, list), WebSocket live event stream
  - Graph store (`server/graph_store.py`): filesystem-based JSON persistence in `./graphs/`, `last_opened` tracking
  - CLI entry point: `dan-serve` / `python -m dan.server` starts backend on localhost:8000
  - React Flow editor (`editor/`): Vite + React + TypeScript + React Flow v12 + Zustand + Tailwind CSS v4
  - Bidirectional DAN <-> React Flow adapter (`graphAdapter.ts`): port handles, edge type colors, node factory for all 10 types
  - Custom `DanNode` component: per-type color coding, port labels, execution status rings
  - `NodePalette`: draggable + click-to-add for all 10 node types grouped by category
  - `ConfigPanel`: dynamic form-based editing of all node/edge properties
  - `GraphCanvas`: React Flow canvas with drag-and-drop from palette, minimap, controls
  - `RunPanel`: save, run, resume, disconnect controls with live status badge
  - `LogPanel`: scrolling timestamped event log with color-coded event types
  - `OutputPreview`: per-node output viewer for selected node
  - `CompositePreview`: read-only sub-graph modal for body_graph-backed nodes
  - `GraphSwitcher`: graph list dropdown, create new, delete, auto-load last opened
- [infra] Added `fastapi`, `uvicorn[standard]`, `websockets`, `httpx` dependencies to `pyproject.toml`; bumped version to 0.2.0
- [infra] Added `dan-serve` script entry point in `pyproject.toml`
- [infra] Vite proxy config: `/api` routes to backend at localhost:8000 during development
- [fix] Replaced `assert _run_manager` in API endpoints with proper `_require_run_manager()` returning HTTP 503
- [test] 27 new tests (167 total): server API integration tests (CRUD + runs), run manager unit tests, engine event instrumentation tests
- [docs] Created `docs/plans/3-phase-2-visual-editor.md` with full task breakdown
- [docs] Updated `architecture.md` with Phase 2 modules, server architecture, API endpoints, frontend layout
- [docs] Updated `todo.md` Phase 2 line with plan link

## 2026-02-24 (Phase 1)
- [feat] Phase 1 complete — async execution engine built on Phase 0 type system:
  - `engine/state.py`: NodeStatus enum, PortDataStore (port data routing), ExecutionState (run-level aggregate)
  - `engine/context_runtime.py`: SharedContextStore (Layer 3), ArtifactStore (Layer 4, immutable versioning), LocalStateManager (Layer 2, scoped to composites)
  - `engine/executor.py`: EngineConfig, NodeExecutor protocol, ExecutionContext (scoped access for executors), ExecutorRegistry
  - `engine/conditions.py`: safe Python expression evaluator with restricted builtins for IfElse/WhileLoop conditions
  - `engine/normalizer.py`: OutputNormalizer pipeline — JSON extraction (fenced/inline), schema validation, re-prompt message builder
  - `engine/checkpoint.py`: CheckpointStore protocol, FileSystemCheckpointStore, NullCheckpointStore
  - `engine/scheduler.py`: Kahn's topological sort with parallel-level detection, asyncio.gather dispatch, sub-graph recursion, Engine.run()/resume() public API
  - `executors/llm.py`: LLMExecutor using AsyncOpenAI SDK (vectorengine.ai default, claude-sonnet-4-6), output normalization loop, transient API retry with backoff
  - `executors/tool.py`: ToolRegistry + ToolExecutor for function-based dispatch
  - `executors/code.py`: CodeExecutor with sandboxed Python exec and restricted builtins
  - `executors/control_flow.py`: IfElseExecutor, WhileLoopExecutor (with compaction + stagnation detection), ForEachExecutor (semaphore-based parallelism), ReduceExecutor, RouterExecutor (LLM-powered), HumanInTheLoopExecutor (callback-based with timeout)
- [infra] Added `openai>=1.0` and `pytest-asyncio` dependencies to `pyproject.toml`
- [infra] Added `.env` and `.env.example` for LLM provider config (vectorengine.ai endpoint)
- [test] 49 new engine tests (140 total): unit tests for state stores, expression evaluator, normalizer; integration tests for linear chain, IfElse branching, WhileLoop with condition exit, ForEach parallel fan-out, checkpoint/resume
- [docs] Added `docs/plans/2-phase-1-orchestration-engine.md` with full task breakdown
- [docs] Updated `architecture.md` with engine module layout and execution engine section
- [docs] Marked Phase 1 complete in `todo.md`
- [docs] Added "Workflows as Code" as first-class design principle — every workflow must be definable in Python code, not just visually. Added to `development-plan.md` Section 5, `architecture.md`, and key decisions.
- [docs] Added Phase 1.5 (Workflow Builder API) to roadmap — fluent Python DSL (`dan.builder`) that compiles to `dan_graph_v1` JSON, round-trips with visual editor. Inserted before Phase 2 in `todo.md` and `development-plan.md`.

## 2026-02-24 (Phase 0 + project setup)
- [docs] Created project tracking structure: `docs/` directory with `architecture.md`, `changelog.md`, `todo.md`, `bugs.md`, and `plans/`
- [docs] Moved `development-plan.md` from project root to `docs/`
- [docs] Scaffolded `architecture.md` from development plan sections 4–5
- [infra] Added `.cursor/rules/project-tracking.mdc` — always-apply rule governing doc maintenance workflow
- [docs] Changed plan naming from flat sequential (`plan-01-name`) to hierarchical (`1-name`, `1-1-name`, `1-1-1-name`) to mirror the task tree
- [docs] Added `docs/plans/1-phase-0-formal-spec.md` and linked Phase 0 in `docs/todo.md`
- [docs] Resequenced roadmap: visual editor moved to Phase 2, paper-writing proof shifted to Phase 3, advanced execution visualization moved to Phase 5
- [docs] Updated architecture to lock language split (Python core + TypeScript editor) and shared `dan_graph_v1` JSON contract
- [feat] Phase 0 complete — implemented formal spec as Python Pydantic types:
  - Port models (`InputPort`, `OutputPort`) with JSON Schema type declarations
  - 10 node types: 3 operators (LLM, Tool, Code), 6 control-flow (IfElse, WhileLoop, ForEach, Reduce, Router, HumanInTheLoop), 1 composite
  - 3 edge types (Data, Control, Context) with discriminated-union deserialization
  - Four-layer context model (edge data, node-local state, shared context store, artifact store)
  - Composite-node contract (external schemas, control state, local state, read/write sets, compaction rules, projections)
  - `dan_graph_v1` JSON serialization contract with UI metadata (position, ui dict) for lossless editor round-trips
  - Graph with recursive sub-graphs, entry/exit points, shared context declarations
  - Node type registry for extensibility
  - Validation: port schema compatibility (MVP structural), graph well-formedness (7 checks), cycle detection
  - 75 tests passing including full paper-writing motivating example
- [docs] Added four-layer context management model to `development-plan.md` Section 5 and `architecture.md`: edge data (bounded), node-local state (scoped), shared context store (blackboard), artifact store (by reference)
- [docs] Added context projection pattern — scope boundary functions that extract minimal views per consumer (loop controller vs. reviser vs. parent graph)
- [docs] Defined composite node contract (external schemas, control state, local working set, read/write sets, compaction rule) and context policies (mutation, parallel merge, compaction, failure exits)
- [docs] Expanded Phase 0 plan with context management tasks (3-1 through 3-6), context-related validation rules (5-3, 5-4), and context scoping tests (6-3)
- [docs] Added memory system concept to `development-plan.md` Section 5 (short-term vs. long-term, encoding/consolidation/retrieval) and backlog item in `todo.md`. Parked as backlog — not yet designed.
- [docs] Added output normalization (batch-norm analogy — deterministic parse → validate → re-prompt → retry on every LLM operator) to `development-plan.md`, `architecture.md`, and Phase 0 plan task 2-5
- [docs] Added operator-level retry policy (`max_retries`, `backoff`, `fallback_model`, `on_failure`) to `development-plan.md`, `architecture.md`, and Phase 0 plan task 2-6
- [docs] Added checkpointing/resumability to Phase 1 scope (`development-plan.md` roadmap, `todo.md`, `architecture.md`)
- [infra] Added root `.gitignore` with Python runtime/build/test ignores and future TypeScript editor artifacts (`node_modules`, JS package-manager logs)
- [docs] Added backlog items: dynamic `model_policy` (budget-aware + learned assignment for cases beyond strategies 1-4), `max_concurrency` on For-Each/Map
- [fix] Validation: edge endpoint checks (node + port existence) now apply to all edge types, not just DataEdge
- [fix] Validation: ContextEdge mode enforcement — read edges require key in node's `read_set`, write/append edges require key in `write_set`
- [fix] Validation: empty-schema data edges now emit a warning ("schema safety bypassed") instead of silently passing
- [fix] Model: added `control_state_schema` to `ForEachNode` and `CompositeNode` to match composite-node contract in docs
- [fix] Registry docstring corrected — registry is for programmatic discovery, not JSON deserialization (which uses Pydantic discriminated union)
- [test] Test suite expanded from 75 to 91 tests: added edge endpoint tests (all edge types), context permission enforcement, empty schema warnings, registry discovery

## 2026-02-24 (Phase 1.5)
- [feat] Phase 1.5 complete — fluent workflow builder DSL (`dan.builder`):
  - `builder/refs.py`: `NodeRef` and `PortRef` compile-time proxies with `__format__` (marker emission), `__rshift__` (>> chaining), `__getitem__` (port subscript), sanitized alias generation
  - `builder/builder.py`: `WorkflowBuilder` class with `workflow()` factory, node creation methods (`.llm()`, `.tool()`, `.code()`, `.if_else()`, `.reduce()`, `.router()`, `.human_in_the_loop()`), `.edge()` explicit wiring, context-manager sub-graphs (`.while_loop()`, `.for_each()`, `.composite()`), `.to_json()` / `.to_dict()` serialization
  - `builder/compiler.py`: compiles builder state → `Graph` model. Resolves f-string markers (`<<dan:node_id:port>>`), auto-generates InputPort/OutputPort, auto-generates DataEdge objects, node-type output contract map (matches runtime executor ports), entry/exit point detection, `validate_graph()` integration
  - `builder/decompiler.py`: `decompile(graph) -> str` produces executable Python. Topological sort with deterministic ordering, chain detection (>> sugar), context-manager emission for sub-graphs, `NodeRef` wrappers for sub-graph wiring, lossless preservation of metadata/context/edge types
- [feat] Four connection mechanisms: (1) f-string magic auto-wiring, (2) >> operator chaining, (3) PortRef passing, (4) explicit wf.edge()
- [feat] Node-type output contract map: `DEFAULT_OUTPUT_PORTS` maps each node_type to its actual runtime output port name (e.g. `llm_operator` → `text`, `for_each` → `results`, `if_else` → `branch`)
- [test] 70 new builder tests (237 total): unit tests for NodeRef/PortRef, marker resolution, compiler (each node type), sub-graph context managers, decompiler round-trip; integration tests for paper-writing workflow, engine execution (code chain, while-loop, for-each), editor round-trip golden test
- [docs] Added `docs/plans/1-5-builder-api.md` with full task breakdown
- [docs] Updated `architecture.md` with builder module layout, DSL design, connection mechanisms, decompiler details
- [docs] Marked Phase 1.5 complete in `todo.md`

## 2026-02-24 (Phase 4 roadmap — review fixes)
- [docs] `todo.md`: reworded Phase 4 description — markdown is a third authoring surface alongside Python DSL and visual editor, not a replacement
- [docs] `todo.md`: added plan file link placeholder (`6:`) for Phase 4; renumbered Phase 5 marketplace to `7:` to avoid collision with Phase 3.5's `plans/5-*` prefix
- [docs] `todo.md`: clarified backlog overlap — split hyperedge items into "engine runtime" (execution hooks, attachment logic) vs "markdown authoring syntax" (`.md` file references in workflow)
- [docs] `todo.md`: added 5 backlog items — markdown/Python coexistence policy, `dan.loader` ↔ `dan.builder` parity checklist, compiler diagnostics + source maps, markdown round-trip conformance tests, markdown format versioning
- [docs] `architecture.md`: replaced "Code-first, visual-second" key decision with "Three authoring surfaces, one IR" (Python DSL, markdown, visual editor all compile to `dan_graph_v1`)
- [docs] `development-plan.md`: replaced "Workflows as Code" section with "Three Authoring Surfaces, One IR" — table comparing Python / markdown / visual editor strengths and use cases
- [docs] `development-plan.md`: updated roadmap table — marked Phases 1.5 and 3 as Done with test counts, added Phase 3.5 and Phase 4, removed stale old Phases 4/5 (composite + visualization, now in 3.5)

## 2026-02-24 (Phase 4 Memory & Context Scoping)
- [docs] Promoted memory & context scoping from backlog to Phase 4 — context scoping across agent boundaries (global/local/pass_down/emit_up) and memory system for long chains (encoding, consolidation, retrieval)
- [docs] Renumbered Markdown Agent Format → Phase 5, Shareable Blocks / Marketplace → Phase 6

## 2026-02-24 (Phase 4 roadmap — now Phase 5)
- [docs] Added Phase 4 — Markdown Agent Format to `todo.md`: agent file format (YAML frontmatter + natural language), workflow file format (arrow notation), flow notation parser, port type inference, auto-wiring, `dan.loader` compiler, and paper-writing rewrite as validation
- [docs] Renumbered Shareable Blocks / Marketplace to Phase 5
- [docs] Added backlog items: composite agents in markdown, skills/rules as markdown hyperedges, markdown round-trip from visual editor, linked JSON Schema files

## 2026-02-24 (Phase 3.5 post-review bug fixes)
- [fix] Read-only drill-in guard: disabled click-to-add, drag, and editing in `NodePalette` and `ConfigPanel` when drilled into sub-graph layer
- [fix] Template port contracts: LLM nodes output port renamed `output` → `text` (matching `LLMExecutor` output key); ReAct loop condition changed `"not done"` → `"True"` (avoids `ConditionError`); Plan-Execute composite given proper `input_mappings`/`output_mappings`
- [fix] `startRun()` stale-graph guard: `saveGraph()` now returns `boolean`; `startRun` aborts if save fails
- [fix] Edge type styling sync: `updateEdgeData` now updates top-level `animated`, `label`, and `style.stroke` when `edge_type` changes (previously only updated `data.danEdge`)
- [fix] Subgraph event hierarchy tags: added `layer_path: tuple[str, ...]` to `ExecutionContext`; `emit_event` injects `layer_path` into event data; `run_subgraph` accepts `parent_node_id` and threads it to child contexts via scheduler
- [fix] LogPanel node names: `nodeNameMap` now reads `data.name` (matching DanNode model) with `data.label` as fallback
- [test] Fixed composite executor test mock to accept new `parent_node_id` parameter; 256 tests pass, TypeScript zero errors

## 2026-02-24 (Phase 1.5 post-review fixes)
- [fix] Builder/compiler idempotence: repeated `build()` calls now produce stable output (no in-place mutation of pending node kwargs/ports during compilation)
- [fix] Sub-graph entry refs: `body.input` / `body.item` now compile to usable entry input placeholders (`{input}` / `{item}`) with proper input-port generation for sub-graph entry nodes
- [feat] Builder typed-edge support: added `wf.control_edge(...)` and `wf.context_edge(...)`; compiler now materializes `ControlEdge` and `ContextEdge` (not only `DataEdge`)
- [fix] Decompiler edge fidelity: emits executable `control_edge`/`context_edge` calls instead of comments; round-trip now preserves edge types
- [fix] Decompiler chain safety: `>>` sugar is emitted only for true default-port chains; custom port wiring is preserved via explicit `wf.edge(...)`
- [fix] Decompiler metadata fidelity: preserves node `position`/`ui`/`metadata`, graph `created_at`/`updated_at`, and `artifact_refs`
- [feat] Builder artifact support: added `wf.artifact_ref(...)` and compiler support for graph-level `artifact_refs`
- [test] Added 6 regression tests for post-review issues (build idempotence, sub-graph entry refs, control/context edge round-trip, custom-port chain fidelity, UI/metadata/artifact preservation); total test suite now 243 passing
