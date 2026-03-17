# Deep Agent Network (DAN)

Typed graph orchestration for multi-agent LLM workflows. Design agent networks as directed graphs with typed edges, control-flow primitives, and heterogeneous models — then run them via Python, a visual editor, or (soon) markdown files.

## Key Concepts

- **Two-level nodes** — atomic operators (LLM call, tool call, code execution) and composite agents (sub-graphs that behave as single nodes with typed interfaces)
- **Typed edges** — data (schema-validated), control (conditionals, loops, routing), and context (shared state)
- **Control-flow primitives** — GateNode (if/else + while loop), ForEach, Reduce, Router, Human-in-the-Loop
- **Model heterogeneity** — each operator independently specifies its model (cheap for classification, strong for reasoning)
- **Output normalization** — built-in parse → validate → re-prompt → retry on every LLM operator
- **Retry & fallback** — per-node `RetryPolicy` with exponential backoff, fallback models, and halt/skip/error failure modes
- **Multi-provider LLM** — built-in support for OpenAI, Anthropic, and Google; prefix-based routing (`gpt-*`, `claude-*`, `gemini-*`) with per-node model override
- **11 built-in tools** — file I/O, web search/fetch, HTTP, shell commands, PDF reading, text chunking, JSON extraction, regex — all sandboxed to workspace root
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

DAN is highly configurable via environment variables. See `.env.example` for the full list of options, including model tiering, learning features, cost controls, and MCP servers.

```bash
cp .env.example .env
```

**Quick Start Setup:**
To enable the best daily-use experience, uncomment these bundles in your `.env`:
```env
DAN_LEARNING_MODE=1      # Turn on all safe learning features (prompt optimization, memory, etc.)
DAN_FULL_TOOLS=1         # Expose all 32+ tools in chat (file ops, git, system)
DAN_ENABLE_TIER_POLICY=1 # Auto-assign models by task difficulty
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
| **Markdown agents** (`dan.loader`) | Most accessible — natural language, minimal syntax | Rapid authoring, non-programmers |

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
- **Desktop messaging controls** — top-shell Settings can connect Telegram or WhatsApp Web as app-global remote-control surfaces, with provider-aware first-run onboarding, Telegram token/helper setup plus bot username hints, in-app WhatsApp QR pairing and reset, dependency guidance, auto-start toggles, and a shell status button visible from every mode
- **Workflow reuse** — wrap saved workflows as reusable composite nodes via palette or context menu
- **Run inputs** — auto-detects `{variable}` placeholders and shows an input dialog before execution
- **Validation** — port-aware connection validation, backend validation API, inline error badges with toast summaries
- **Import/export** — save and load graph JSON; Cmd+K command palette for node search
- **Edge types** — toggle between data/control/context edges; color-coded with labels
- **Auto-layout** — dagre-based layout with one click

## CLI Tools

### `dan-run` — Execute a Workflow

```bash
dan-run workflow.json                     # run a JSON graph
dan-run examples/paper_writing.py         # run a Python builder script
dan-run "Summarize the latest AI papers"  # natural-language goal → MetaController plans + runs
dan-run workflow.json --interactive        # prompt for HumanNode inputs
```

### `dan-chat` — Conversational Workflow Authoring

Build, modify, and run workflows through an interactive REPL. Chat is the unified control plane — the LLM can search workflow history, start/cancel runs, publish/export workflows, and manage the full run lifecycle without leaving the conversation. In the desktop editor, full-screen Chat now supports safe branch-based exploration with a collapsible branch tree, and the compact Research/Development sidebars inherit most of the same day-to-day UX: mode pills, slash-command affordances, mentions, smart paste, queued follow-ups, stop generation, richer tool/run rendering, and one-click handoff into full Chat.

```bash
dan-chat                                  # start with scratch workflow
dan-chat --workflow-id my-workflow        # load an existing workflow
dan-chat --local                          # force local mode (no server required)
dan-chat --confirm                        # require approval before mutations
```

`dan-chat` works without a running server — when the server is unavailable, it automatically falls back to local mode with an in-process ChatManager. Use `--local` to force local mode even if a server is running.

**Chat Commands:**
Inside the REPL, use slash commands to manage your session:
- `/model [name]` — view or change the LLM for this chat
- `/cost` — display cumulative session cost
- `/status` — view active servers, channels, and session info
- `/run` — execute the current workflow
- `/show` — display current graph as ASCII DAG
- `/list`, `/open`, `/save`, `/saveas`, `/new`, `/rename` — manage files
- `/mcp list`, `/mcp tools`, `/mcp install` — manage MCP servers
- `/undo` — revert the last graph mutation
- `/retry` — retry the last prompt
- `/memory-delete`, `/memory-forget`, `/memory-confirm`, `/memory-reject` — manage learned memory
- `/domains [list|known|add|remove|clear]` — inspect or edit saved common domains
- `/help` — list all commands

### MCP Server Integration

DAN can also consume external MCP servers as first-class tools in chat and workflow execution.

```bash
# Install the optional MCP client dependency
pip install -e ".[mcp]"

# Start chat or the server
dan-chat
# or
dan-serve

# From chat, install and connect a known MCP server
/mcp install stata

# Inspect configured servers and tools
/mcp list
/mcp tools stata
```

Configured MCP servers are stored in `~/.dan/mcp.json` using the `mcpServers` format shared by tools like Cursor and Claude Desktop. Servers with `autoConnect: true` are connected automatically on startup.

### `dan-up` / `dan-down` — Server Lifecycle

```bash
dan-up                                    # start server (if needed) and drop into chat
dan-up --port 9000                        # use custom port
dan-down                                  # stop background server
```

Inside the REPL:
- **Chat naturally** — describe what you want and the LLM proposes and auto-applies mutations
- **24 capability tools** — experience search, run lifecycle, publish/share/export, graph listing — the LLM picks the right tool based on your intent
- `/undo` — revert the last mutation; `/show` — inspect current graph; `/run` — execute
- `/help` — list all commands

See [docs/cli.md](docs/cli.md) for the full CLI reference with all options, environment variables, and common workflows.

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
| POST | `/api/graphs/{id}/apply-mutation` | Apply chat-generated mutation |
| POST | `/api/chat/message` | Send chat message (NL workflow authoring) |
| WS | `/api/chat/{channel}/events` | Stream chat/mutation events |
| POST | `/api/gateway/dispatch` | Unified workflow dispatch (JSON, NL text, workflow ID) |
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
| 4 — Core hardening (retry/fallback, multi-provider, tools, templates) | Done |
| 5 — Markdown agent format (`dan.loader`) | Done |
| 6 — Extended capabilities (RAG, sandbox, validators) | Done |
| 7 — Author & Distribute (CLI, publish API/MCP, messaging, blocks, PyPI) | Done |
| 7.1 — Structure review | Done |
| 7.2 — Cursor-parity chat experience | Done |
| 8 — Observe & recover (persistence, debug workbench) | Done |
| 9 — Deep systems (memory, behavior modifiers, execution primitives, self-evolving) | Done |
| 10 — Token optimization (compression, caching, context management, analytics) | Done |
| 11 — Meta-orchestrator (autonomous planning, repair, self-knowledge) | In progress |
| 12 — Author & Distribute v2 (dan-chat, gateway text dispatch) | Done |
| 13 — Multi-surface gateway | Done |

## License

Private — not yet published.
