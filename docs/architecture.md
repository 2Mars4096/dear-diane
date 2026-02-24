# Architecture

## Tech Stack

- **Core engine:** Python 3.11+, Pydantic v2, OpenAI SDK
- **Execution server:** FastAPI, uvicorn, websockets
- **Visual editor:** TypeScript, React, React Flow v12 (`@xyflow/react`), Zustand, Tailwind CSS v4, Vite
- **Schema validation:** JSON Schema (typed edges)
- **Shared contract:** versioned graph JSON (`dan_graph_v1`) between Python and TypeScript
- **Testing:** pytest, pytest-asyncio, httpx (ASGI test client)

## Directory Structure

```
deep-agent-network/
  docs/                          # All project tracking and documentation
    development-plan.md          # Vision, roadmap, research landscape
    architecture.md              # This file — tech stack, conventions, decisions
    changelog.md                 # Append-only log of completed work
    todo.md                      # High-level task list, links to plan files
    bugs.md                      # Known issues and failed approaches
    plans/                       # Numbered detailed plans (just-in-time)
  src/dan/                       # Python package
    __init__.py                  # Top-level package exports
    models/                      # Phase 0 — formal type system
      ports.py                   # InputPort, OutputPort
      context.py                 # NodeLocalState, SharedContextDeclaration, ArtifactRef, ContextProjection, policies
      nodes.py                   # NodeBase, LLMOperator, ToolOperator, CodeOperator
      control_flow.py            # IfElse, WhileLoop, ForEach, Reduce, Router, HumanInTheLoop, CompositeNode
      edges.py                   # DataEdge, ControlEdge, ContextEdge
      graph.py                   # Graph container, Node/Edge discriminated unions, dan_graph_v1 contract
    validation/
      schema.py                  # Port schema compatibility (MVP structural check)
      graph.py                   # Graph well-formedness validation
    registry.py                  # NodeTypeRegistry — maps node_type strings to classes
    engine/                      # Phase 1 — async execution engine
      __init__.py                # Public API: Engine, EngineConfig, RunResult, etc.
      state.py                   # NodeStatus, PortDataStore, ExecutionState
      context_runtime.py         # SharedContextStore, ArtifactStore, LocalStateManager (Layers 2-4)
      executor.py                # EngineConfig, NodeExecutor protocol, ExecutionContext, ExecutorRegistry
      conditions.py              # Safe expression evaluator for IfElse/WhileLoop conditions
      normalizer.py              # OutputNormalizer — JSON extraction, schema validation, re-prompt
      checkpoint.py              # CheckpointStore protocol, FileSystemCheckpointStore
      events.py                  # EngineEvent, EventType — typed runtime events
      scheduler.py               # Topological sort, parallel dispatch, Engine.run()/resume(), event emission
    executors/                   # Phase 1 — built-in node executors
      __init__.py                # Auto-registers built-in executors
      llm.py                     # LLMExecutor — OpenAI-compatible (vectorengine.ai default)
      tool.py                    # ToolExecutor + ToolRegistry — function dispatch
      code.py                    # CodeExecutor — sandboxed Python exec
      control_flow.py            # IfElse, WhileLoop, ForEach, Reduce, Router, HumanInTheLoop
    server/                      # Phase 2 — FastAPI backend for visual editor
      __init__.py
      __main__.py                # CLI entry point: `dan-serve` / `python -m dan.server`
      app.py                     # FastAPI application — CRUD, runs, WebSocket
      graph_store.py             # Filesystem-based graph JSON persistence
      run_manager.py             # Background run execution + event pubsub + catch-up
  editor/                        # Phase 2 — React Flow visual editor
    package.json                 # Dependencies: react, @xyflow/react, zustand, tailwindcss
    vite.config.ts               # Vite config: Tailwind plugin, /api proxy to backend
    tsconfig.json                # TypeScript config
    src/
      types/graph.ts             # TypeScript types mirroring dan_graph_v1
      lib/graphAdapter.ts        # Bidirectional DAN <-> React Flow conversion
      lib/api.ts                 # HTTP/WebSocket API client
      store/useGraphStore.ts     # Zustand store — graph, selection, run state, events
      components/DanNode.tsx     # Custom React Flow node with port handles + status
      components/NodePalette.tsx # Draggable node type catalogue
      components/ConfigPanel.tsx # Node/edge property editor
      components/GraphCanvas.tsx # Main React Flow canvas with drop handling
      components/RunPanel.tsx    # Run/resume/save controls + status badge
      components/LogPanel.tsx    # Scrolling timestamped event log
      components/OutputPreview.tsx   # Per-node output viewer
      components/CompositePreview.tsx # Read-only sub-graph modal
      components/GraphSwitcher.tsx   # Graph list/create/delete header
      App.tsx                    # Main layout: palette + canvas + panels
  tests/                         # pytest suite
    test_models/                 # Unit tests for all model types
    test_validation/             # Validation logic tests
    test_examples/               # Paper-writing motivating example
    test_engine/                 # Engine unit + integration tests
    test_server/                 # Server API, run manager, and event tests
  graphs/                        # Saved graph JSON files (filesystem persistence)
  pyproject.toml                 # Pydantic v2 + OpenAI SDK + FastAPI + uvicorn + pytest
  .env.example                   # Environment variable template
  .cursor/rules/                 # AI agent rules
```

## Core Abstractions

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
| Reduce | Fan-in: aggregate results from parallel branches |
| Router | LLM-powered routing — model decides which branch |
| Human-in-the-Loop | Pause execution, wait for human input, resume |

### Model Heterogeneity

Each operator node independently specifies its model. Cheap/fast for classification, strong for reasoning, code-specialized for generation. First-class design principle, not afterthought.

### Output Normalization (built-in)

Like batch normalization in DNNs, every LLM operator has a deterministic, built-in output normalization layer: parse → validate against output schema → re-prompt with error on failure → retry up to N times. This is automatic (not a user-visible node) and guarantees every data edge carries schema-valid data or an explicit error.

### Error Handling / Retry Policy

Every operator carries a `retry_policy`: `max_retries`, `backoff`, `fallback_model`, `on_failure` (error / skip / halt). Separate from output normalization — this handles call-level failures (rate limits, timeouts, network errors).

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

### Context Projection

At every scope boundary (entering a sub-graph, entering a loop iteration), a **projection function** extracts only what the next consumer needs. Each consumer gets a minimal view — the loop controller sees only iteration count + convergence metrics, the reviser sees only current draft + latest comments, the parent graph sees only the final output.

### Composite Node Contract

Every composite/loop node declares:
- `external_input_schema` / `external_output_schema` — what the parent sees
- `control_state` — iteration count, stop flags, thresholds (loop controller only)
- `local_working_set` — latest working data, not full history
- `read_set` / `write_set` — declared dependencies on shared context store
- `compaction_rule` — how local history is summarized between iterations

### Context Policies

- **Mutation**: nodes read shared context by default; writes require declaration
- **Parallel merge**: fan-out branches must specify merge rules (append, last-write-wins, or reducer node)
- **Compaction**: configurable per composite node (sliding window, summarization gate, diff-based)
- **Failure exits**: `max_iterations`, `stagnation`, `timeout`

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
- Sub-graph execution is recursive: WhileLoop/ForEach/Composite executors call back into the scheduler

### Executor Protocol

- `NodeExecutor` is a `Protocol` with `async execute(node, inputs, context) -> NodeResult`
- `ExecutorRegistry` maps `node_type` strings to executor instances; users can register custom executors
- Built-in executors for all 10 node types auto-registered on Engine creation

### LLM Integration

- Default executor uses `openai.AsyncOpenAI(base_url=...)` — supports any OpenAI-compatible endpoint
- Output normalization built into LLM executor: extract JSON -> validate against schema -> re-prompt with error -> retry
- Transient API errors (rate limits, timeouts) retried with exponential backoff

### Condition Evaluation

- IfElse/WhileLoop `condition` strings evaluated as Python expressions via restricted `eval()`
- No `__builtins__`; whitelist of safe functions (len, min, max, all, any, etc.)
- Variables populated from upstream port data

### Checkpointing

- `CheckpointStore` protocol with filesystem default (`FileSystemCheckpointStore`)
- Checkpoint written after each topological level completes
- `Engine.resume()` loads checkpoint and continues from pending nodes

## Workflow Builder API (Phase 1.5 — not yet built)

A fluent Python DSL (`dan.builder`) for defining workflows in code. Compiles to `dan_graph_v1` JSON and round-trips losslessly with the visual editor. Code is the primary authoring interface — the visual editor is a second interface over the same graph JSON. See `development-plan.md` Section 5 for the full design rationale and API sketch.

## Visual Editor Backend (Phase 2)

### Server Architecture

Local full-stack: FastAPI backend + React Flow frontend. Runs locally like Jupyter — `dan-serve` or `python -m dan.server` starts the server, open `localhost:8000` in browser.

### Engine Event System

- 9 typed events: `run_started`, `run_completed`, `run_failed`, `node_started`, `node_completed`, `node_failed`, `node_skipped`, `node_output`, `log`
- Opt-in `event_callback` parameter on `Engine` constructor — no events emitted if not set (backward compatible)
- Events are fire-and-forget; callback failures never break execution

### Run Manager

- Executes `Engine.run()` / `Engine.resume()` as asyncio background tasks
- Multiplexes events to WebSocket subscribers via async queues
- Catch-up snapshot on subscribe: current node statuses + buffered recent events (up to 500)
- Tracks active/completed runs with status snapshots

### API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/graphs` | List all graphs + last opened |
| POST | `/api/graphs` | Create new graph |
| GET | `/api/graphs/{id}` | Load graph JSON |
| PUT | `/api/graphs/{id}` | Save graph JSON |
| DELETE | `/api/graphs/{id}` | Delete graph |
| POST | `/api/runs` | Start execution |
| POST | `/api/runs/{id}/resume` | Resume checkpointed run |
| GET | `/api/runs/{id}` | Get run status snapshot |
| GET | `/api/runs` | List all runs |
| WS | `/api/runs/{id}/events` | Live event stream |

### Graph Persistence

- Filesystem-based: JSON files in `./graphs/` directory
- `last_opened` tracking for auto-load on editor open
- `dan_graph_v1` JSON contract unchanged — the backend reads/writes the same format

## Visual Editor Frontend (Phase 2)

### DAN <-> React Flow Adapter

Bidirectional conversion layer (`graphAdapter.ts`):
- DAN `input_ports`/`output_ports` map to React Flow handles via `port:<name>` ID convention
- 3 edge types visually differentiated: data (indigo), control (amber), context (emerald, animated)
- All 10 node types rendered through a single `DanNode` custom component with per-type color coding
- Node execution status shown as colored rings (yellow=running, green=completed, red=failed)

### UI Layout

```
┌─────────────────────────────────────────────────┐
│  GraphSwitcher (graph list, create, delete)      │
│  RunPanel (save, run, resume, disconnect, status)│
├──────┬─────────────────────────────┬─────────────┤
│      │                             │             │
│ Node │      GraphCanvas            │  Config     │
│Palette│   (React Flow + minimap)   │  Panel      │
│      │                             │             │
│      ├─────────────────────────────┤             │
│      │ Logs | Output               │             │
│      │ (tab bar + scrolling panel) │             │
└──────┴─────────────────────────────┴─────────────┘
```

### Composite Node Preview

- Nodes with `body_graph` (while_loop, for_each, composite) show a "View Sub-graph" button
- Opens a read-only React Flow modal rendering the referenced sub-graph
- No nested editing — that's Phase 4

## Key Decisions

- **Build, don't buy.** Existing tools (Langflow, Flowise, Dify) cannot handle while-loops, composable sub-graphs, or typed edges natively. See development-plan.md sections 3-4 for full analysis.
- **Code-first, visual-second.** Python builder API is the primary workflow authoring interface. The visual editor reads/writes the same graph JSON. Both are first-class, but code comes first.
- **Language split.** Python for orchestration runtime and validation; TypeScript for the visual editor and interaction layer.
- **Roadmap resequencing.** Build the core engine first, then immediately build a full visual editor baseline to test the system early via UI.
- **Hierarchical plan numbering.** Plan files use hierarchical numbering (`1-name`, `1-1-name`, `1-1-1-name`) to mirror the task tree.
