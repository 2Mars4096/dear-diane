# Deep Agent Network (DAN)

Typed graph orchestration for the hard 5% of long-running agentic tasks. Design persistent agent networks as directed graphs with typed edges, control-flow primitives, and heterogeneous models — then run them via Python, a visual editor, or (soon) markdown files.

DAN is aimed at deep work that ordinary single-agent copilots handle poorly: multi-stage research, long-running builds, high-trust workflows, and tasks that may genuinely benefit from hierarchical swarms of specialized workers. The goal is not to make everyday chat heavier. The goal is to make rare, high-value tasks tractable, inspectable, and repeatable.

## Who DAN Is For

- Long-running tasks that need planning, decomposition, tool use, checkpoints, and recovery over hours or days
- High-trust research and operational workflows where provenance, reviewability, and explicit control matter
- Problems that can be broken into many bounded workers, from a few specialists up to large hierarchical swarms when the task justifies it

## Who DAN Is Not For

- Everyday chat, shallow one-shot requests, or simple tasks a normal copilot can finish faster
- Flat “more agents = better” swarm setups without bounded roles, aggregation, or operator control
- Teams looking for the lightest possible AI wrapper rather than a durable workflow and execution system

## Key Concepts

- **Two-level nodes** — atomic operators (LLM call, tool call, code execution) and composite agents (sub-graphs that behave as single nodes with typed interfaces)
- **Worker-first compute surface** — new compute stages can be authored as `wf.worker(...)` with shared context/tool/memory refs, while pure control primitives stay explicit instead of being forced into one opaque super-node
- **Typed edges** — data (schema-validated), control (conditionals, loops, routing), and context (shared state)
- **Tiered handoff lint** — optional per-edge structural → semantic → intent validation with autofix blocks bad handoffs before downstream nodes consume them
- **Control-flow primitives** — GateNode (if/else + while loop), ForEach, Reduce, Router, Human-in-the-Loop
- **Hierarchical swarms** — scale from one agent to many bounded specialists when decomposition pays off; large swarms are useful only with supervision, aggregation, and recoverability
- **Model heterogeneity** — each operator independently specifies its model (cheap for classification, strong for reasoning)
- **Output normalization** — built-in parse → validate → re-prompt → retry on every LLM operator
- **Retry & fallback** — per-node `RetryPolicy` with exponential backoff, fallback models, and halt/skip/error failure modes
- **Multi-provider LLM** — built-in support for OpenAI, Anthropic, and Google; prefix-based routing (`gpt-*`, `claude-*`, `gemini-*`) with per-node model override, plus a shared provider-layer retry wrapper for transient API failures so agent surfaces do not each reinvent recovery logic
- **11 built-in tools** — file I/O, web search/fetch, HTTP, shell commands, PDF reading, text chunking, JSON extraction, regex. Web fetch can optionally recover through DAN's persistent browser for JS-heavy or auth-gated pages; relative paths stay sandboxed to the workspace root, while explicit absolute paths are trusted and allowed. For untrusted LLM callers, keep inputs relative or add an approval layer
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

DAN is highly configurable via environment variables.

**Fastest path** — copy the minimal config (just API key + model):
```bash
cp .env.minimal .env
# Edit .env and fill in your API key
```

**Full config** — copy `.env.example` for all available options (model tiering, learning features, cost controls, MCP servers, sandbox settings, and more):
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

**Chat: build + run a workflow** — Open a workflow tab, set chat to **Agent**, use `plan_graph_mutations` to edit, then ask the model to **`start_run`** with any `inputs` (e.g. `watchlist_path`). **Author equity/watchlist workflows in DAN (Agent chat), not by pasting large generated graphs/code from outside.** Prompts: [`docs/chat-equity-workflow-cookbook.md`](docs/chat-equity-workflow-cookbook.md).

### Run the Reference Coding Organism

This is a local, no-server CLI around the bounded `research -> coding -> validation -> synthesis` reference organism. Demo mode stays deterministic; live mode uses the same organism with a local tool basket:

```bash
dan organism --json
# or
dan-organism --json
```

You can override the bounded coding objective directly:

```bash
dan organism "Repair the validator path and keep the delivery summary explicit."
```

Live local coding mode:

```bash
dan organism --live --model gpt-4.1
```

The default live tool basket is:
- `list_directory`
- `file_read`
- `file_edit`
- `file_write`
- `shell_command`
- `web_search`
- `git_status`
- `git_diff`
- `git_log`

You can inspect or narrow it explicitly:

```bash
dan organism --list-tools
dan organism --live --model gpt-4.1 --tool file_read --tool file_write --tool shell_command
```

### Run DAN Code

If you want only the coding surface, use the dedicated `dan code` product instead of the full reference organism:

```bash
dan code --workspace .
# or
dan-code --workspace .
# or
dancode --workspace .
```

It uses `.env` / environment settings by default for the provider and model, and keeps product state under `./.dan-code/` inside the workspace:

- `config.json` — workspace-local DAN Code defaults
- `session.json` — resumable session state
- `transcript.jsonl` — compact run history with `event_log_path` pointers
- `runs/` — per-turn evidence notes plus `events.jsonl` step logs

`dan code` runs directly on a dedicated coding organism built from the same universal worker membrane. Its runtime shape is explicit:

- one tool-free orchestrator
- a dynamic pool of homogeneous coding workers
- one aggregation cell
- one validator cell

The orchestrator decides worker fan-out per attempt, the workers stay cloneable, the aggregator owns the final bounded candidate, and the validator can reject that candidate and force a repair round. It does not route through the full DAN graph engine unless you choose to build that bridge later.

The stack is intentionally thin and composable: `provider wrapper -> durable orchestrator -> bounded coding organism`. The provider wrapper owns transient API retries, the durable orchestrator owns user-facing decisions, and the bounded coding organism owns implementation/validation work. Those layers talk through small explicit contracts instead of one large shared runtime blob. The durable layer now also carries one shared runtime/session facts packet, so the CLI and orchestrator read workspace root, effective working directory, latest run status, and similar basics from the same source of truth.

In the CLI, that same orchestrator is now the public voice of the run: deterministic lifecycle/status lines stay in `[status]`, while assistant-style narration comes from structured runtime updates (`status.update`) emitted by the orchestrator, workers, aggregator, validator, and final delivery path. The orchestrator plan now also includes a formal `public_response`, so it can first state the interpreted user intent and next action before the implementation plan. Hidden chain-of-thought still stays private.

Role-level tool exposure is also explicit:

- orchestrator: no local tools
- workers: read-oriented local tools only; raw `shell_command` is treated as mutation-capable and is therefore excluded from the read-only basket
- aggregator: full selected tool basket, including writes and raw shell access when enabled
- validator: read-oriented local tools for inspection and focused validation

Each real coding turn now carries standard runtime context automatically, including workspace root, current working directory, current date/time/timezone, active model, enabled tool IDs, and approval mode. That keeps the model from wasting early tool calls on facts the runtime already knows.

Each bounded coding run now also writes `.dan-code/runs/turn-XX/events.jsonl`, a timestamped JSONL stream of the emitted run events. That file is the durable step-by-step audit trail for later analysis, and both the final CLI report and `transcript.jsonl` keep the matching `event_log_path`.

For the chat-like control layer above the coding organism, `dan code` now also uses a bounded hedge on real orchestrator/review LLM decisions: if one no-tool controller call stays slow, a second provider call can launch after a short delay, and the first response that already satisfies the structured decision schema wins. This keeps the hedge above the agents rather than inside the write-heavy coding organism.

The default hedge stays conservative: up to `2` controller attempts with a `2.0s` stagger. You can override those controls with `DAN_CODE_CONTROL_HEDGE_MAX_ATTEMPTS` and `DAN_CODE_CONTROL_HEDGE_DELAY_SECONDS` when you want to test more or less aggressive control-plane speculation.

Provider thinking mode is also configurable per workspace or per run:

- `--thinking-mode auto` keeps the provider default/compatibility behavior
- `--thinking-mode disabled` favors faster visible answer text on providers like Kimi
- `--thinking-mode enabled` keeps explicit reasoning mode on when the provider supports it

`dan code` resolves thinking mode in this order: CLI flag -> `.dan-code/config.json` -> `DAN_CODE_THINKING_MODE` from `.env` / environment -> `auto`.

Bootstrap the workspace-local product config explicitly:

```bash
dan code --workspace . --init
dan code --workspace . --show-config
```

If you omit the task, it starts an interactive coding session and resumes the saved workspace session automatically when present:

```bash
dan code --workspace .
```

That interactive shell now keeps a durable orchestrator alive across turns. Ordinary natural-language messages go to the orchestrator first, and the orchestrator decides whether to answer directly, ask one clarifying question, or launch one bounded coding run. It now reserves bounded coding runs for directly actionable repo/coding tasks instead of defaulting short chat/meta turns into the worker pool, uses recent conversation/report context to resolve referential requests like `the website we talked about`, and clarifies when that reference is still not specific enough. When it does launch coding, the bounded `orchestrator -> worker pool -> aggregator -> validator` organism still does the heavy implementation work, but the resulting report is routed back into the same orchestrator session so it can decide `done`, `continue`, or `clarify`. The shell/orchestrator seam is now explicit as typed facts/context/report packets, so the layers stay diagrammable and replaceable.

Inside the session:
- `/help` shows commands
- `/tools` lists available local tools
- `/status` shows model, tools, and session paths
- `/history` shows recent coding turns
- `/summary` shows the session-level file/test rollup
- `/reset` clears carried-forward session context
- `/clear` is an alias for `/reset`
- `/exit` leaves the session

Interactive sessions now default to `--approval-mode confirm-risky`, which prompts before `file_edit`, `file_write`, `shell_command`, and other mutating tools. One-shot runs default to `--approval-mode auto`. You can override that explicitly:

```bash
dan code --workspace . --approval-mode auto
dan code --workspace . --approval-mode confirm-all
dan code --workspace . --thinking-mode disabled
dan code --workspace . --show-model-trace
```

Tool rounds are unbounded by default now. If you want a hard cap for a particular run, set `--max-tool-rounds N`; leaving it unset, or setting `--max-tool-rounds 0`, keeps the local tool loop unbounded while `max_tool_calls` still acts as the broader safety rail.

The local tool loop now also injects a shared structured-tool policy. In practice that means `dan code` should prefer `list_directory`, `file_read`, `file_edit`, `file_write`, `web_search`, and structured git tools over shell fallbacks, use `file_edit` for targeted line-based edits to existing files, batch multiple non-overlapping edits to the same file into one `file_edit(edits=[...])` call when possible, use `file_write` for whole-file create/replace/append flows, use `web_search` for live external lookups instead of guessing current facts, avoid shell heredocs when direct file tools are available, and cut down on repeated discovery once it already has the needed fact.

The structured-output seam is more tolerant now as well. Fenced ` ```json ... ``` ` replies from the orchestrator or bounded organs are parsed as structured payloads instead of being dumped back to the console as raw text, and local file tools accept `file_path` as a compatibility alias for `path` so minor argument-name mismatches do not waste a tool round.

Greetings, lightweight chat, and natural-language status/workspace/result questions now go through the durable orchestrator instead of a canned local fast-path, so the shell can respond conversationally before deciding whether any coding work is needed. The orchestrator answers those turns from the shared runtime/session facts plus recent report context, while `/clear` still resets the durable conversation state.

By default, `dan code` now prints deterministic lifecycle `[status]` lines plus dynamic assistant updates from both the durable orchestrator and the bounded coding organism, so you can see intake, planning, worker execution, aggregation, validation, retries, and completion without falling back to raw tool spam. Common read-heavy tools such as `list_directory` and `file_read` are also summarized compactly so the useful signal stays visible.

If a bounded coding run fails after producing real files or a partial candidate, `dan code` now preserves and reports that material output instead of collapsing the run to `files: (none)`. Failed runs still stay failed, but the shell will show the concrete files/change summary it actually got far enough to produce.

The same shared facts packet also drives orchestrator-owned status/workspace answers. Questions like current status or current working directory now use the effective DAN Code workspace/tool context instead of falling back to generic assistant copy, regex shortcuts, or the host shell `PWD`.

For speed, the durable control layer now also skips two low-signal model hops in common coding flows: plainly-direct imperative coding/edit requests can jump straight into one bounded coding run without paying an extra intent-classification turn first, and clearly successful completed bounded passes can stop without paying an extra review-model turn.

Those orchestrator turns also intentionally avoid reusing the worker-core continuation from the prior coding turn. The durable shell already carries explicit recent conversation plus recent report context, so meta questions like current status or latest results do not need latent acquisition state from the previous coding task.

The orchestrator also normalizes partial replies conservatively now. If the model answers a meta question with plain text or a partial structured payload, `dan code` treats that as a conversational/status response instead of silently launching the bounded coding organism.

Transient provider failures now retry in the shared LLM provider layer before the shell gives up, which is especially important for OpenAI-compatible endpoints such as Kimi. That recovery behavior is shared across agent surfaces rather than being hardcoded into `dan code`.

`--show-model-trace` adds a more detailed public progress trace to the CLI. It prints worker-scoped model request/response previews alongside tool calls, and text-only replies now stream their public deltas live inline (for example `[worker-1][model] stream: ...`) instead of waiting for a final preview line. It still does not expose hidden chain-of-thought or private scratchpad text.

Give it a bounded coding objective directly:

```bash
dan code --workspace . \
  "Inspect the failing worker tests, patch the smallest viable fix, run focused validation, and summarize the result."
```

Useful options:

```bash
dan code --list-tools
dan code --workspace . --json
dan code --workspace . --tool file_read --tool file_write --tool shell_command
dan code --workspace . --new-session
dan code --workspace . --no-session-persist
dan code --workspace . --quiet-progress
dan code --workspace . --show-model-trace
```

### Measure Raw API Latency

If you want to isolate provider latency from DAN runtime overhead, use the raw parallel chat-completions probe:

```bash
python scripts/measure_raw_llm_latency.py --model gpt-4.1 --count 10 --concurrency 10
```

It hits an OpenAI-compatible `/chat/completions` endpoint directly and reports per-call timings plus aggregate latency stats (`avg`, `median`, `p95`, `min`, `max`, total wall time).

If you want streaming-first responsiveness numbers, use:

```bash
python scripts/measure_raw_llm_latency.py --model gpt-4.1 --count 10 --concurrency 10 --stream
```

That additionally reports:

- `first_chunk_*` latency
- `first_text_*` latency for the first visible streamed text chunk
- `first_answer_text_*` latency for the first actual answer-content chunk

and can print the streamed text live with:

```bash
python scripts/measure_raw_llm_latency.py --model gpt-4.1 --stream --print-stream
```

It reads the same env settings when present:

- `DAN_MODEL` / `DAN_LLM_MODEL`
- `DAN_LLM_API_KEY` / `OPENAI_API_KEY`
- `DAN_LLM_BASE_URL` / `DAN_BASE_URL` / `OPENAI_BASE_URL`

You can also force provider thinking mode when the backend supports it:

```bash
python scripts/measure_raw_llm_latency.py --model kimi-k2.5 --stream --thinking-mode enabled
python scripts/measure_raw_llm_latency.py --model kimi-k2.5 --stream --thinking-mode disabled
```

This matters for Kimi-style responses where streaming may emit `reasoning_content` before normal answer text. The probe treats either path as first visible text, while still measuring first answer-text latency separately.

You can also benchmark an exact raw payload:

```bash
python scripts/measure_raw_llm_latency.py \
  --payload-file .tmp/chat-payload.json \
  --count 10 \
  --concurrency 10 \
  --json
```

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
- **Workflow Save As** — promote `_scratch` or any working draft into a durable named workflow ID without losing the current graph state
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
dan-run examples/paper_writing.py --server http://127.0.0.1:8000
dan-run "Summarize the latest AI papers"  # natural-language goal → MetaController plans + runs
dan-run workflow.json --interactive        # prompt for HumanNode inputs
```

For benchmark-grade or demo-grade workflow execution, prefer server mode and pass `--server` explicitly. `dan-run` now fails closed when an explicit server target is unreachable, instead of silently falling back to local mode. Use `--local` only for convenience/dev runs, not as the default evidence path for tool-heavy workflows.

### Paper-Writing Smoke Path

The canonical benchmark/demo acceptance path for the paper-writing workflow is the direct Python CLI, not a thin wrapper:

```bash
export DAN_PAPER_WRITING_MODEL_PROFILE=smoke
export DAN_PAPER_WRITING_OUTPUT_DIR=output/paper-writing-smoke

python examples/paper_writing.py \
  "supply chain resilience" \
  --no-human \
  --output-dir "$DAN_PAPER_WRITING_OUTPUT_DIR"
```

Expected result:
- on a fully configured machine, a submission-ready bundle under `output/paper-writing-smoke/`
- on a partially configured machine, a structured degraded artifact bundle that still records dependency and prerequisite failures

`examples/paper_writing.py` now also exports `build()`, so `dan-run examples/paper_writing.py --server ...` is a supported path once the backend is up.

### `dan-chat` — Conversational Workflow Authoring

Build, modify, and run workflows through an interactive REPL. Chat is the unified control plane — the LLM can search workflow history, start/cancel runs, publish/export workflows, and manage the full run lifecycle without leaving the conversation. In the desktop editor, full-screen Chat now supports safe branch-based exploration with a collapsible branch tree plus cross-workflow history discovery for older conversations, and the compact Research/Development sidebars inherit most of the same day-to-day UX: mode pills, slash-command affordances, mentions, smart paste, queued follow-ups, stop generation, richer tool/run rendering, and one-click handoff into full Chat.

```bash
dan-chat                                  # start with scratch workflow
dan-chat --workflow-id my-workflow        # load an existing workflow
dan-chat --local                          # force local mode for in-process chat workflows
dan-chat --confirm                        # require approval before mutations
```

`dan-chat` works without a running server — when the server is unavailable, it automatically falls back to local mode with an in-process ChatManager. Use `--local` to force local mode even if a server is running.

Workflow-aware scheduling is also part of the chat surface now: `/schedule workflow current daily at 8am --timezone Asia/Hong_Kong` binds against the resolved current/linked workflow, and natural follow-ups like `schedule it daily at 8am Hong Kong time` reuse the same scheduler path.

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
- `/search <query>` — force an explicit grounded web search with fetched excerpts
- `/memory-delete`, `/memory-forget`, `/memory-confirm`, `/memory-reject` — manage learned memory
- `/domains [list|known|add|remove|clear]` — inspect or edit saved common domains
- `/schedule [list|add|remove|pause|resume|workflow]` — manage schedules; `/schedule workflow current ...` targets the resolved current/linked workflow and supports `--timezone`
- `/timezone [show|set|clear]` — inspect or set the default scheduling timezone used when a schedule does not specify one
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

`dan-up` also reuses an already-healthy DAN server on the requested port even if it was started by DAN Desktop or another launcher. `dan-down` only stops the PID-managed background server started through the manual CLI path.

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
