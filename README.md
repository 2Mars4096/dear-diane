# Deep Agent Network (DAN)

DAN is a focused agent product built as one stack:

1. **Universal Cell** — one bounded worker primitive driven by typed briefs.
2. **Universal Organism** — a dependency-aware plan of Universal Cells.
3. **Super DAN** — the general-purpose live agent runtime and terminal UI.
4. **Work/Notes** — the desktop and phone workspace for sessions, progress, artifacts, previews, and Markdown notes.

The pre-Universal graph builder, visual graph editor, Code/Research/Content modes, concierge, messaging adapters, publishing, RAG, and product-specific organism families were removed on 2026-08-02. They remain recoverable from Git history.

## Requirements

- Python 3.11+
- Node.js 20+ for the desktop/web GUI
- Flutter 3.41+ only when building the optional phone app

## Install

```bash
pip install -e ".[dev]"
cd editor
npm install
```

## Configure a model

Set the provider key and model you intend to use:

```env
OPENAI_API_KEY=...
DAN_LLM_MODEL=gpt-5.4
```

Use `.env.minimal` as the shortest copyable template; `.env.example` documents the optional retained settings.

Anthropic and Google models can use `ANTHROPIC_API_KEY` and `GOOGLE_API_KEY`. Model names route by prefix (`gpt-*`, `claude-*`, `gemini-*`); exact overrides can be supplied as JSON in `DAN_MODEL_PROVIDER_MAP`.

Useful paths:

```env
DAN_WORKSPACE_ROOT=/absolute/path/to/workspace
DAN_GRAPHS_DIR=/absolute/path/to/durable-state
DAN_NOTES_WORKSPACE_ROOT=/absolute/path/to/notes
```

## Run

Start the server:

```bash
dan-up
# or: dan serve --no-reload
```

Start the browser GUI:

```bash
cd editor
npm run dev
```

Open `http://localhost:5173/#workspace`.

Start the Electron desktop app in development:

```bash
cd editor
npm run electron:dev
```

Run Super DAN directly:

```bash
dan super-organism --model "$DAN_LLM_MODEL" "Inspect this workspace and implement the requested change"
dan super-tui
```

Other retained commands are `dan serve`, `dan up`, `dan down`, and `dan editor`.

## Product surface

Work provides:

- workspace and session management;
- protected task blueprints and execution attempts;
- durable Agent V2 task/run/event state;
- live progress, steering, validation, repair, evidence, and artifacts;
- file browsing and preview;
- structured image attachments.

Notes provides:

- Markdown/Hugo file navigation and editing;
- recent and collection views;
- rendered preview and knowledge navigation;
- learning-course/progress metadata;
- the same Super DAN composer and durable session model as Work.

The mobile app consumes the same loopback/WireGuard-safe HTTP API. `/api/workspace-wireguard` is read-only and never starts or reconfigures a host VPN service.

## Core Python API

```python
from dan import build_cell

cell = build_cell(model="gpt-5.4", role_label="implementer")
assert cell.metadata["universal_cell"] is True
```

For typed briefs and organism plans, see [docs/llm-api-guide.md](docs/llm-api-guide.md).

## Validation

```bash
python -m compileall -q src/dan

cd editor
npm test
npm run electron:compile
npm run build:verify
```

Focused Python tests live under `tests/test_worker`, `tests/test_cli`, `tests/test_server`, and `tests/eval`. Live-provider and real-browser evals remain opt-in.

## Repository map

```text
src/dan/worker/          Universal Cell, briefs, contracts, scheduler, organism
src/dan/worker/organisms Universal Organism, Super DAN, local tool runtime
src/dan/cli/             Retained server, Super DAN, and TUI commands
src/dan/server/          Work/Notes, sessions, and Agent V2 control plane
src/dan/providers/       OpenAI, Anthropic, and Google provider adapters
src/dan/tools/           Selectively loaded Super DAN capabilities
src/dan/skills/          Skill discovery and loading
editor/                  Work/Notes React + Electron app
mobile/                  Optional Flutter phone app
docs/plans/              Active numbered roadmap (1–5)
```

## Archive policy

Git is the archive. Do not add an in-tree legacy archive or reintroduce old product modes for compatibility. If historical code is needed, inspect `0d630dca`, the complete pushed pre-cutover recovery point (including legacy tests and their assets).
