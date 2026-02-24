# Changelog

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

## 2026-02-24 (Phase 1.5 post-review fixes)
- [fix] Builder/compiler idempotence: repeated `build()` calls now produce stable output (no in-place mutation of pending node kwargs/ports during compilation)
- [fix] Sub-graph entry refs: `body.input` / `body.item` now compile to usable entry input placeholders (`{input}` / `{item}`) with proper input-port generation for sub-graph entry nodes
- [feat] Builder typed-edge support: added `wf.control_edge(...)` and `wf.context_edge(...)`; compiler now materializes `ControlEdge` and `ContextEdge` (not only `DataEdge`)
- [fix] Decompiler edge fidelity: emits executable `control_edge`/`context_edge` calls instead of comments; round-trip now preserves edge types
- [fix] Decompiler chain safety: `>>` sugar is emitted only for true default-port chains; custom port wiring is preserved via explicit `wf.edge(...)`
- [fix] Decompiler metadata fidelity: preserves node `position`/`ui`/`metadata`, graph `created_at`/`updated_at`, and `artifact_refs`
- [feat] Builder artifact support: added `wf.artifact_ref(...)` and compiler support for graph-level `artifact_refs`
- [test] Added 6 regression tests for post-review issues (build idempotence, sub-graph entry refs, control/context edge round-trip, custom-port chain fidelity, UI/metadata/artifact preservation); total test suite now 243 passing
