# CLI Reference

DAN provides eight command-line tools. All are installed automatically with `pip install -e ".[dev]"`.

## Overview

| Command | Purpose |
|---------|---------|
| `dan-serve` | Start the backend server (editor + API) |
| `dan-run` | Execute a workflow (JSON, markdown, Python, or NL goal) |
| `dan-chat` | Conversational REPL for building/modifying/running workflows |
| `dan-status` | List active and recent runs |
| `dan-logs` | Tail event logs for a run |
| `dan-publish` | Publish workflows as MCP servers or HTTP APIs |
| `dan-adapter` | Start messaging adapters (email, Telegram, WhatsApp) |
| `dan-blocks` | Manage shareable workflow blocks |

## Environment Variables

These apply across all commands:

| Variable | Purpose | Default |
|----------|---------|---------|
| `DAN_LLM_API_KEY` | LLM provider API key | (required) |
| `DAN_LLM_MODEL` | Default model name | `claude-sonnet-4-6` |
| `DAN_LLM_BASE_URL` | LLM API base URL | Provider default |
| `DAN_SERVER_URL` | Server URL for client commands | `http://127.0.0.1:8000` |
| `DAN_WORKSPACE_ROOT` | Sandbox root for file tools | Current directory |
| `DAN_CHAT_MODEL` | Override model for chat/authoring | `$DAN_LLM_MODEL` |

Place these in a `.env` file at the project root; all commands auto-load it.

---

## `dan-serve`

Start the FastAPI backend. Powers the visual editor, chat API, run management, and all other commands that connect to a server.

```bash
dan-serve                    # default: localhost:8000 with auto-reload
dan-serve --port 9000        # custom port
dan-serve --no-reload        # production mode (no file watcher)
```

**Options:**

| Flag | Description | Default |
|------|-------------|---------|
| `--host HOST` | Bind address | `127.0.0.1` |
| `--port PORT` | Bind port | `8000` |
| `--reload` | Auto-reload on file changes | On |
| `--no-reload` | Disable auto-reload | Off |

**Notes:**
- The visual editor frontend connects to this server (default `http://localhost:8000`)
- Other commands (`dan-chat`, `dan-run` in server mode, `dan-status`, `dan-logs`) require `dan-serve` running
- Graphs are stored in `./graphs/` as JSON files

---

## `dan-run`

Execute a workflow from the terminal. Supports four source types: JSON graph files, markdown agent directories, Python builder scripts, and natural-language goals.

```bash
# From a JSON graph file
dan-run workflow.json

# From markdown agents
dan-run examples/paper_writing_md/

# From a Python builder script
dan-run examples/paper_writing.py

# From a natural-language goal (MetaController plans + runs)
dan-run "Summarize the latest AI papers"

# With inputs
dan-run workflow.json --input topic="supply chain" --input format="INFORMS"

# Background mode
dan-run workflow.json --bg
```

**Options:**

| Flag | Description | Default |
|------|-------------|---------|
| `source` | Workflow file, directory, or NL goal string | (required) |
| `--input KEY=VALUE`, `-i` | Workflow input (repeatable) | — |
| `--input-json JSON` | Inputs as a JSON object string | — |
| `--interactive` | Enable HumanNode prompts | On (when TTY) |
| `--headless` | Auto-skip HumanNode prompts | Off |
| `--human-timeout SECS` | Timeout for interactive prompts | `300` |
| `--auto-approve` | Skip MetaController plan confirmation | Off |
| `--quiet`, `-q` | Suppress TUI; output final result JSON only | Off |
| `--verbose`, `-v` | Show all engine events | Off |
| `--output-format {text,json}` | Output format | `text` |
| `--output PATH`, `-o` | Write final output JSON to file | — |
| `--artifacts-dir DIR` | Directory for generated artifacts | `./output/` |
| `--background`, `--bg` | Run in background; print run ID and exit | Off |
| `--goal` | Force NL goal interpretation | Auto-detected |
| `--local` | Force local engine (skip server) | Auto |
| `--server SERVER` | Server URL | `http://localhost:8000` |
| `--api-key` | LLM API key | `$DAN_LLM_API_KEY` |
| `--model` | Default LLM model | `$DAN_LLM_MODEL` |
| `--base-url` | LLM base URL | `$DAN_LLM_BASE_URL` |
| `--workspace` | Workspace root directory | Current directory |

**Execution modes:**
- **Server mode** (default when `dan-serve` is running): dispatches via the gateway API. Run events stream back via WebSocket. HumanNode prompts are interactive.
- **Local mode** (`--local` or server unavailable): runs the engine directly in-process. No server needed.

---

## `dan-chat`

Interactive REPL for conversational workflow authoring. Think Claude Code for workflow graphs — describe what you want, the LLM proposes mutations, you review and apply, then run.

```bash
dan-chat                              # start with a scratch (empty) workflow
dan-chat --workflow-id my-workflow    # load an existing workflow
dan-chat --mode debug                # start in debug mode
dan-chat --server http://myhost:9000  # connect to a different server
```

**Options:**

| Flag | Description | Default |
|------|-------------|---------|
| `--workflow-id ID` | Workflow to load | `_scratch` |
| `--scratch` | Use scratch workflow (same as default) | — |
| `--server URL` | Server URL | `$DAN_SERVER_URL` or `http://127.0.0.1:8000` |
| `--mode MODE` | Chat mode | `build` |

**Chat modes:**

| Mode | Behavior |
|------|----------|
| `build` | Build a workflow from scratch — LLM generates full graph mutations |
| `mutate` | Modify an existing workflow — LLM produces targeted mutations |
| `agent` | General-purpose assistant with graph-aware context |
| `ask` | Text-only Q&A (no tool calling / mutations) |
| `plan` | Planning mode — discuss architecture before building |
| `debug` | Debug mode — includes recent run failures in context |
| `auto` | Auto-detect mode from message content |

**REPL commands:**

| Command | Action |
|---------|--------|
| `/run` | Run the full workflow |
| `/run-node @[Name](node:id)` | Run a single node |
| `/run-subgraph @[Name](subgraph:key)` | Run a subgraph |
| `/show` | Display current graph (nodes, edges, types) |
| `/save [name]` | Save workflow — prompts for a name if on `_scratch` |
| `/list` | List all saved workflows |
| `/open <id>` | Open an existing workflow (switches session) |
| `/saveas <id>` | Copy workflow to a new ID and switch |
| `/new [id]` | Create a new empty workflow (auto-names if omitted) |
| `/rename <name>` | Rename current workflow's display name |
| `/help` | Show available commands |
| `/exit` | Exit the REPL |

**Typical session:**

```
> dan-chat

dan-chat — workflow: _scratch (mode: build)
Type /help for commands, /exit to quit.

> Create a workflow that scrapes a URL, extracts key points, and writes a summary

[LLM streams response...]

--- Mutation proposed ---
  Scraping and summarization pipeline
  Operations: 5
    1. add_node (url_input)
    2. add_node (web_scraper)
    3. add_node (key_points_extractor)
    4. add_node (summary_writer)
    5. add_edge (url_input → web_scraper)
Apply mutation? [Y/n] y
Mutation applied.

> /show
  Workflow: _scratch
  Nodes: 4  Edges: 3
  ---
  [input_node] url_input (url_input)
  [tool_executor] web_scraper (web_scraper)
  [llm_operator] key_points_extractor (key_points_extractor)
  [llm_operator] summary_writer (summary_writer)
  ---
  url_input -> web_scraper
  web_scraper -> key_points_extractor
  key_points_extractor -> summary_writer

> Add a human review step between extraction and the final summary

[LLM proposes another mutation...]
Apply mutation? [Y/n] y
Mutation applied.

> /run
Started full run.
Node 'url_input' started
Node 'url_input' completed
Node 'web_scraper' started
...
Waiting for input: Review the extracted key points. Approve to continue?
[Y/n] y
Node 'summary_writer' started
Node 'summary_writer' completed
Run completed successfully
```

**Workflow management (inside the REPL):**

```
> /save url-scraper
Saved as 'url-scraper'.
dan-chat — workflow: url-scraper (mode: mutate)

> /list
  ID                          Name                 Updated
  -----------------------------------------------------------------
  _scratch                     _scratch             2026-03-05T12:00
  url-scraper                  url-scraper          2026-03-05T12:01 *

> /new research-assistant
Created 'research-assistant'.
dan-chat — workflow: research-assistant (mode: build)

> /open url-scraper
dan-chat — workflow: url-scraper (mode: mutate)

> /rename URL Scraper Pipeline
Renamed to 'URL Scraper Pipeline'.

> /exit
```

When exiting from `_scratch` with a non-empty graph, `dan-chat` prompts to save:

```
> /exit
Save workflow before exiting? [name / Enter to skip] my-pipeline
Saved as 'my-pipeline'.
```

**Requirements:** `dan-serve` must be running. For local-only chat, see plan 21-8 (not yet implemented).

---

## `dan-status`

List active and recent workflow runs.

```bash
dan-status             # list all recent runs
dan-status --json      # output as JSON
dan-status --kill RUN_ID   # terminate a running process
```

**Options:**

| Flag | Description |
|------|-------------|
| `--json` | Output as JSON |
| `--kill RUN_ID` | Send SIGTERM to a running background process |

---

## `dan-logs`

Tail event logs for a specific run. Works with both active and completed runs.

```bash
dan-logs run-12345          # show last 50 events
dan-logs run-12345 -f       # follow (like tail -f)
dan-logs run-12345 -n 100   # show last 100 events
dan-logs run-12345 --json   # raw JSONL output
```

**Options:**

| Flag | Description | Default |
|------|-------------|---------|
| `run_id` | Run ID (from `dan-status` or `dan-run --bg`) | (required) |
| `--follow`, `-f` | Stream new events as they arrive | Off |
| `--last N`, `-n N` | Show last N events (0 for all) | `50` |
| `--json` | Output raw JSONL | Off |

---

## `dan-publish`

Publish workflows as callable MCP servers or HTTP APIs. Other tools (Cursor, Claude Desktop, custom apps) can call your workflow as a tool.

```bash
# Publish as MCP server (for Cursor / Claude Desktop)
dan-publish workflow.json

# Publish as HTTP API
dan-publish workflow.json --type http --port 8001

# Generate MCP client config for Cursor
dan-publish workflow.json --generate-config

# Generate API documentation
dan-publish workflow.json --docs

# Publish all workflows in a directory
dan-publish --dir ./graphs/
```

**Options:**

| Flag | Description | Default |
|------|-------------|---------|
| `source` | Workflow file (.json, .md, .py) or directory | — |
| `--dir PATH` | Publish all workflows in a directory | — |
| `--type {mcp,http,both}` | Server type | `mcp` |
| `--port PORT` | HTTP server port | `8001` |
| `--host HOST` | HTTP server host | `0.0.0.0` |
| `--name NAME` | Override published tool/API name | Workflow name |
| `--api-key KEY` | Require API key for HTTP endpoints | — |
| `--human-timeout SECS` | HumanNode timeout | `300` |
| `--local` | Force local engine execution | Auto |
| `--server URL` | Override `dan-serve` URL | — |
| `--generate-config` | Output MCP client config JSON | — |
| `--docs` | Generate markdown API documentation | — |
| `--openapi` | Output OpenAPI 3.1 spec as JSON | — |

---

## `dan-adapter`

Start messaging adapters that bridge HumanNode interactions to external channels. Users can interact with running workflows via email, Telegram, or WhatsApp.

```bash
# Email adapter
dan-adapter email --imap-host imap.gmail.com --smtp-host smtp.gmail.com \
  --email user@gmail.com --password "app-password"

# Telegram bot
dan-adapter telegram --token "BOT_TOKEN"

# WhatsApp Business API
dan-adapter whatsapp --phone-id "PHONE_NUMBER_ID" --token "ACCESS_TOKEN"

# From a config file
dan-adapter -c adapter-config.json email
```

**Subcommands:** `email`, `telegram`, `whatsapp`

Each adapter registers as a HumanNode renderer — when a workflow run reaches a HumanNode, the adapter delivers the prompt through the configured channel and waits for a response.

---

## `dan-blocks`

Manage shareable workflow blocks — packaged, versioned workflow components that can be exported, shared, and installed.

```bash
dan-blocks list                          # list installed blocks
dan-blocks info my-block                 # show block metadata
dan-blocks export my-workflow            # export workflow as a block
dan-blocks export my-workflow/node-id    # export a composite node
dan-blocks install ./my-block/           # install from local path
dan-blocks pack ./my-block/              # compress to .dan-block.tar.gz
dan-blocks remove my-block               # uninstall a block
```

**Subcommands:**

| Subcommand | Description |
|------------|-------------|
| `list` | List installed blocks |
| `info` | Show block metadata and schemas |
| `export` | Export a workflow or composite node as a block |
| `install` | Install a block from path or URL |
| `remove` | Remove an installed block |
| `pack` | Compress a block directory to `.dan-block.tar.gz` |

---

## Common Workflows

### Build and run from the terminal

```bash
# 1. Start the server
dan-serve &

# 2. Build a workflow interactively
dan-chat

# 3. After building, run it
dan-run graphs/_scratch.json
```

### One-shot NL execution

```bash
dan-serve &
dan-run "Analyze sentiment in customer reviews from reviews.csv" --auto-approve
```

### Background execution with monitoring

```bash
dan-run workflow.json --bg       # prints: run-17345...
dan-status                       # check all runs
dan-logs run-17345 -f            # follow the logs
```

### Publish a workflow as an API

```bash
dan-serve &
dan-publish graphs/summarizer.json --type http --port 8001
# Now: curl http://localhost:8001/run -d '{"text": "..."}'
```
