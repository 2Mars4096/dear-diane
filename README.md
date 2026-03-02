# Deep Agent Network (DAN)

Typed graph orchestration for multi-agent LLM workflows. Design agent networks as directed graphs with typed edges, control-flow primitives, and heterogeneous models — then run them via Python, a visual editor, or (soon) markdown files.

## Key Concepts

- **Two-level nodes** — atomic operators (LLM call, tool call, code execution) and composite agents (sub-graphs that behave as single nodes with typed interfaces)
- **Typed edges** — data (schema-validated), control (conditionals, loops, routing), and context (shared state)
- **Control-flow primitives** — GateNode (if/else + while loop), ForEach, Reduce, Router, Human-in-the-Loop
- **Model heterogeneity** — each operator independently specifies its model (cheap for classification, strong for reasoning)
- **Output normalization** — built-in parse → validate → re-prompt → retry on every LLM operator
- **Checkpointing** — resume long-running workflows from the last completed level

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 20+ (for the visual editor)

### Install

```bash
git clone https://github.com/your-org/deep-agent-network.git
cd deep-agent-network

# Python package (editable)
pip install -e ".[dev]"

# Visual editor
cd editor && npm install && cd ..
```

### Configure

```bash
cp .env.example .env
# Edit .env with your LLM provider credentials:
#   DAN_LLM_BASE_URL=https://api.vectorengine.ai/v1
#   DAN_LLM_API_KEY=your-key
#   DAN_LLM_MODEL=claude-sonnet-4-6
```

### Run the Visual Editor

```bash
# Terminal 1 — backend
dan-serve
# or: python -m dan.server

# Terminal 2 — frontend (dev mode)
cd editor && npm run dev
```

Open `http://localhost:5173`. The editor connects to the backend at `localhost:8000`.

### Run a Workflow from Python

```python
from dan.builder import workflow
from dan.engine import Engine, EngineConfig

paper = workflow("paper_writing")
ideas = paper.llm("idea_gen", prompt="Generate research ideas about {topic}")
outline = paper.llm("planner", prompt=f"Create an outline for: {ideas}")
ideas >> outline
graph = paper.build()

config = EngineConfig(
    llm_base_url="https://api.vectorengine.ai/v1",
    llm_api_key="...",
    llm_default_model="claude-sonnet-4-6",
)
engine = Engine(config)
result = await engine.run(graph, inputs={"topic": "supply chain optimization"})
```

See `examples/paper_writing.py` for a full end-to-end workflow with parallel section writing, review-revise loops, and tool calls. See `examples/vibe_research_md/` for a simple factor-research workflow (markdown format, nested composite, mock data).

## Three Authoring Surfaces

All surfaces compile to the same `dan_graph_v1` JSON and coexist:

| Surface | Strength | When to use |
|---------|----------|-------------|
| **Python DSL** (`dan.builder`) | Most programmable — loops, parameterization, testing | Power users, CI, programmatic generation |
| **Visual Editor** | Most interactive — drag-and-drop, live execution, debugging | Exploration, debugging, demos |
| **Markdown agents** (`dan.loader`) | Most accessible — natural language, minimal syntax | Rapid authoring, non-programmers *(coming soon)* |

## Visual Editor

The editor is a full-featured workflow builder inspired by LangFlow, Flowise, and Coze:

- **Multi-tab workflows** — open multiple workflows as tabs, each with isolated editing and run state; background runs continue on the server and catch up when reactivated
- **Node palette** — searchable, categorized sidebar with 12 node types plus pre-built templates (ReAct, Plan-Execute, Gate nodes)
- **Full editing** — undo/redo, copy/paste/duplicate, right-click context menus, inline rename, port editor (add/remove/rename ports with schemas)
- **Multi-layer navigation** — double-click composite/loop nodes to drill into sub-graphs; breadcrumb bar for navigation
- **Gate-based control flow** — if/else and while-loop gates with branch-colored handles, visible back-edges, condition badges, and collapsible loop groups
- **Live execution** — pulse/glow animations on active nodes, particle flow on edges, duration badges, streaming LLM output, iteration counters
- **Human-in-the-loop** — popup dialog during execution for workflows that require user input
- **Rich logging** — expandable per-node log sections with LLM thinking, tool calls, code output; filtering and click-to-select
- **Workflow reuse** — wrap saved workflows as reusable composite nodes via palette or context menu
- **Run inputs** — auto-detects `{variable}` placeholders and shows an input dialog before execution
- **Validation** — port-aware connection validation, backend validation API, inline error badges with toast summaries
- **Import/export** — save and load graph JSON; Cmd+K command palette for node search
- **Edge types** — toggle between data/control/context edges; color-coded with labels
- **Auto-layout** — dagre-based layout with one click

## Builder DSL

```python
from dan.builder import workflow, decompile

wf = workflow("my_workflow")

# Nodes
classifier = wf.llm("classify", model="gpt-4o-mini", prompt="Classify: {input}")
writer = wf.llm("write", model="claude-sonnet-4-6", prompt=f"Write about: {classifier}")
classifier >> writer

# Sub-graphs
with wf.while_loop("refine", condition="score < 0.9", max_iterations=5) as body:
    body.llm("improve", prompt="Improve the draft...")

with wf.for_each("process", items=writer["items"], parallelism=4) as body:
    body.code("transform", code="result = process(item)")

graph = wf.build()          # -> validated Graph (dan_graph_v1 JSON)
code = decompile(graph)      # -> executable Python that reconstructs the graph
```

Four connection mechanisms: f-string magic (`f"Use: {node}"`), `>>` chaining, port subscript (`node["port"]`), explicit `wf.edge()`.

## Project Structure

```
deep-agent-network/
  src/dan/              # Python package
    models/             # Pydantic type system (ports, nodes, edges, graph)
    validation/         # Schema compatibility + graph well-formedness
    engine/             # Async execution engine (scheduler, state, checkpointing)
    executors/          # Built-in executors (LLM, tool, code, control flow)
    builder/            # Fluent DSL (builder, compiler, decompiler)
    migration/          # Graph migration helpers (legacy → gate nodes)
    server/             # FastAPI backend (CRUD, runs, WebSocket events)
  editor/               # React Flow visual editor (TypeScript + Vite)
  examples/             # Runnable workflow scripts
  tests/                # pytest suite (341 tests)
  graphs/               # Saved graph JSON files
  docs/                 # Project tracking and documentation
```

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=dan

# Run a specific test file
pytest tests/test_engine/test_scheduler.py
```

341 tests covering models, validation, engine, builder, server, migration, and end-to-end workflows.

## API Endpoints

The backend exposes a REST + WebSocket API:

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/graphs` | List all graphs |
| POST | `/api/graphs` | Create new graph |
| GET | `/api/graphs/{id}` | Load graph JSON |
| PUT | `/api/graphs/{id}` | Save graph JSON |
| DELETE | `/api/graphs/{id}` | Delete graph |
| POST | `/api/runs` | Start execution |
| POST | `/api/runs/{id}/resume` | Resume from checkpoint |
| GET | `/api/runs/{id}` | Run status |
| POST | `/api/runs/{id}/human-input` | Submit human-in-the-loop response |
| POST | `/api/validate` | Validate graph structure |
| WS | `/api/runs/{id}/events` | Live event stream |

## Roadmap

| Phase | Status |
|-------|--------|
| 0 — Formal spec (Pydantic types, typed edges, graph contract) | Done |
| 1 — Async execution engine (scheduler, checkpointing, all executors) | Done |
| 1.5 — Builder DSL (fluent API, compiler, decompiler) | Done |
| 2 — Visual editor (FastAPI + React Flow, live streaming) | Done |
| 3 — Paper-writing proof of concept (end-to-end workflow) | Done |
| 3.5 — Frontend design (multi-layer nav, execution viz, logging, palette, polish) | Done |
| 3.75 — Visual editor full editing (tabs, gates, ports, validation, workflow reuse) | Done |
| 4 — Memory & context scoping | Planned |
| 5 — Markdown agent format (`dan.loader`) | Planned |
| 6 — Shareable blocks / marketplace | Planned |

## License

Private — not yet published.
