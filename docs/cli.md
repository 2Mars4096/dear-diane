# CLI Reference

DAN provides eleven command-line tools. All are installed automatically with `pip install -e ".[dev]"`.

## Overview

| Command | Purpose |
|---------|---------|
| `dan-serve` | Start the backend server (editor + API) |
| `dan-run` | Execute a workflow (JSON, markdown, Python, or NL goal) |
| `dan-chat` | Conversational REPL for building/modifying/running workflows |
| `dan-up` | Start server (if needed) and drop into `dan-chat` |
| `dan-down` | Stop background server via PID file |
| `dan-service` | OS-level service management (launchd / systemd) |
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
| `DAN_WHISPER_API_KEY` | Whisper transcription API key | `$DAN_OPENAI_API_KEY` → `$DAN_LLM_API_KEY` |
| `DAN_WHISPER_BASE_URL` | Whisper endpoint base URL | `https://api.openai.com/v1` → `$DAN_LLM_BASE_URL` |
| `DAN_WHISPER_MODEL` | Whisper model name | `whisper-1` |
| `DAN_ENABLE_TIER_POLICY` | Enable auto-assigning models by difficulty | `0` (off) |
| `DAN_TIER_MAP` | JSON map of tier levels to models | — |
| `DAN_LEARNING_MODE` | Enable all safe learning features | `0` (off) |
| `DAN_FULL_TOOLS` | Expose all 32+ tools in chat | `0` (off) |
| `DAN_SHOW_COST` | Show cost metrics in the UI and terminal | `1` (on) |
| `DAN_CONCIERGE_PREP_TIMEOUT` | Timeout for parallel capability prep | `10.0` |

Place these in a `.env` file at the project root; all commands auto-load it.

---

## `dan-serve`

Start the FastAPI backend. Powers the visual editor, chat API, run management, and all other commands that connect to a server.

```bash
dan-serve                    # default: 127.0.0.1:8000 with auto-reload
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
- The visual editor frontend connects to this server (default `http://127.0.0.1:8000`)
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
| `--server SERVER` | Server URL | `http://127.0.0.1:8000` |
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
dan-chat --workflow-id my-workflow    # load an existing workflow (fetches graph, defaults to mutate mode)
dan-chat --mode debug                # start in debug mode
dan-chat --server http://myhost:9000  # connect to a different server
```

Loading an existing workflow (`--workflow-id`) fetches the graph on startup and verifies it exists. If the workflow is not found (404), `dan-chat` exits with an error. Mode defaults to `mutate` for existing workflows (use `--mode build` to override).

**Options:**

| Flag | Description | Default |
|------|-------------|---------|
| `--workflow-id ID` | Workflow to load | `_scratch` |
| `--scratch` | Use scratch workflow (same as default) | — |
| `--server URL` | Server URL | `$DAN_SERVER_URL` or `http://127.0.0.1:8000` |
| `--mode MODE` | Chat mode | `build` (scratch) / `mutate` (existing) |
| `--confirm` | Require explicit `Apply? [Y/n]` before each mutation | Off (auto-apply) |
| `--ask` | Send a single question, print the response, and exit | — |
| `--pipe` | Read question from stdin, print response to stdout, and exit | — |
| `--output FILE` | Write response text to a file in addition to stdout | — |
| `--model NAME` | Override the model for this session | — |

`DAN_MUTATION_CONFIRM=1` is equivalent to `--confirm`.

**Input features:**
- **Up/down arrows** recall previous messages (readline history persisted to `~/.dan/chat_history`)
- **Type while streaming** — messages typed during LLM response are queued and sent after the current response finishes (shown as `[queued] >` when processed)
- **Concierge routing** — server and local chat now pass messages through the shared concierge layer first, so status checks/file-like requests can bypass the full mutation/tool path and project/task context is tracked separately from raw thread history

**Chat modes:**

| Mode | Behavior |
|------|----------|
| `build` | Build a workflow from scratch — LLM generates full graph mutations |
| `mutate` | Modify an existing workflow — LLM produces targeted mutations |
| `agent` | General-purpose assistant with graph-aware context and full tool access |
| `ask` | Q&A with read-only capability tools (experience search, run status, graph listing — no mutations) |
| `plan` | Planning mode with read-only tools — discuss architecture before building |
| `debug` | Debug mode — includes recent run failures in context, limited write tools |
| `auto` | Auto-detect mode from message content |

**REPL & chat commands:**

All slash commands (REPL-local and chat commands like `/model`, `/cost`, `/memory-*`, `/domains`, `/mcp`, etc.) are documented in the [Command Reference](commands.md), which is auto-generated from the canonical command registry. Use `/help` inside `dan-chat` to see commands available on the CLI surface.

**Typical session:**

```
> dan-chat

dan-chat — workflow: _scratch (mode: build)
Type /help for commands, /exit to quit.

> Create a workflow that scrapes a URL, extracts key points, and writes a summary

[LLM streams response...]

--- Mutation applied ---
  Scraping and summarization pipeline
  Operations: 5
    1. add_node (url_input)
    2. add_node (web_scraper)
    3. add_node (key_points_extractor)
    4. add_node (summary_writer)
    5. add_edge (url_input → web_scraper)

(Use /undo to revert. Use --confirm to require approval before each mutation.)

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

When exiting from `_scratch` with a non-empty graph, `dan-chat` auto-generates a name from your first message and prompts to save:

```
> /exit
Save as 'url-scraper'? [Y/n/custom name] y
Saved as 'url-scraper'.
```

Enter accepts the suggestion, `n` skips saving, or type a custom name.

**Server mode** (default): connects to `dan-serve` for full capabilities. **Local mode** (`--local`): runs in-process without a server — useful for quick sessions. `dan-up` handles this automatically.

---

## `dan-ask`

A fast, single-turn CLI wrapper around `dan-chat`. Use it to send a single question or task, stream the response to the terminal, and exit immediately. Ideal for quick questions, shell pipelines, and CI/CD scripts.

```bash
dan-ask "summarize this file"                # ask a question
echo "question" | dan-ask --pipe             # read from stdin
cat file.txt | dan-ask --pipe "summarize"    # pipe content + context
dan-ask "write test" --model claude-3-opus   # override model
dan-ask "draft email" -o out.txt             # save response to file
```

**Options:**

| Flag | Description | Default |
|------|-------------|---------|
| `prompt` | The question or task (positional) | — |
| `--pipe` | Read additional context from stdin | Auto-detected if stdin is piped |
| `--output FILE`, `-o` | Write the raw response to a file | — |
| `--model NAME` | Override the model for this query | `$DAN_CHAT_MODEL` |
| `--local` | Run in local mode (no server needed) | Auto |

---

## `dan-up`

Start the DAN server in the background (if not already running) and drop into `dan-chat`.

```bash
dan-up                # start server on port 8000, then chat
dan-up --port 9000    # custom port
```

**Options:**

| Flag | Description | Default |
|------|-------------|---------|
| `--port PORT` | Server port | `8000` |

**Behavior:**
1. Checks `~/.dan/server.pid` — if server is already running and healthy, skips to step 3.
2. Starts `dan-serve` in the background, writes PID file, polls `/health` until ready.
3. Drops into `dan-chat` connected to the running server.

**Logs:**
- `dan-up` writes the background server process to `~/.dan/logs/server.log`.
- The server now emits `dan.*` application logs there at `INFO`, so chat-manager/tool-loop diagnostics show up alongside uvicorn lines when you need to debug a stall.

---

## `dan-down`

Stop a background DAN server started by `dan-up`.

```bash
dan-down    # sends SIGTERM, falls back to SIGKILL after timeout
```

Reads `~/.dan/server.pid` to find the process.

---

## `dan-service`

OS-level service management. Install DAN as a login service so it starts automatically, with health monitoring and log management.

```bash
dan-service install                     # install as OS service (default port 8000)
dan-service install --port 9000         # custom port
dan-service uninstall                   # remove OS service
dan-service start                       # start the service now
dan-service stop                        # stop the service
dan-service status                      # show running/stopped, PID, port, health
dan-service health                      # health check probe (exit 0 = OK, 1 = fail)
dan-service logs                        # show last 50 lines from server logs
dan-service logs -f                     # follow live output
dan-service logs -n 100                 # last 100 lines
```

**Subcommands:**

| Subcommand | Description |
|------------|-------------|
| `install` | Generate and install OS service config (launchd plist on macOS, systemd unit on Linux). Enables auto-start at login with `KeepAlive`/`Restart=on-failure`. |
| `uninstall` | Remove the service config and stop the running service. |
| `start` | Start the service via `launchctl bootstrap` (macOS) or `systemctl --user start` (Linux). |
| `stop` | Stop the service via `launchctl bootout` (macOS) or `systemctl --user stop` (Linux). Falls back to PID file if no service installed. |
| `status` | Display server status: running/stopped, PID, port, health check result, active runs. Rich formatting if `rich` is installed. |
| `health` | Probe `GET /health`. Prints `OK` and exits 0, or prints `FAIL` and exits 1. |
| `logs` | Tail server logs from `~/.dan/logs/`. Supports `-f` (follow) and `-n N` (line count). |

**Install options:**

| Flag | Description | Default |
|------|-------------|---------|
| `--host HOST` | Bind address | `127.0.0.1` |
| `--port PORT` | Bind port | `8000` |

**Platform support:**
- **macOS:** Generates a launchd plist at `~/Library/LaunchAgents/com.dan.server.plist`. `RunAtLoad` + `KeepAlive` for automatic restart.
- **Linux:** Generates a systemd user unit at `~/.config/systemd/user/dan-server.service`. `Restart=on-failure`, `RestartSec=5`.
- **Other platforms:** Not supported for service install. Use `dan-up` for manual lifecycle.

**Log management:**
- Logs written to `~/.dan/logs/server.stdout.log` and `server.stderr.log`.
- Rotation: keeps last 5 files (`.1` through `.5`). Rotated on each service start.
- Total budget: 50 MB default, configurable via `DAN_LOG_MAX_SIZE` env var (bytes).
- `dan-up` uses `~/.dan/logs/server.log` for its manual background start path.

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

Connect DAN to messaging channels. Two modes:

- **Chat mode** (default, no `--workflow`): general-purpose conversational interface, same as `dan-chat` but over Telegram / WhatsApp / email. Build workflows, ask questions, run, share — all from your messaging app.
- **Workflow mode** (`--workflow FILE`): runs a specific workflow per incoming message, with HumanNode prompts relayed through the messaging channel.

```bash
# Chat mode — talk to DAN like dan-chat, over messaging:
dan-adapter telegram --bot-token "BOT_TOKEN"
dan-adapter whatsapp-web

# Workflow mode — run a specific workflow per message:
dan-adapter telegram --bot-token "BOT_TOKEN" --workflow wf.md
dan-adapter whatsapp-web --workflow wf.md

# WhatsApp Business API (commercial use)
dan-adapter whatsapp --access-token "TOKEN" --phone-number-id "ID"

# Email adapter
dan-adapter email --workflow wf.md --imap-host imap.gmail.com --smtp-host smtp.gmail.com \
  --imap-user user@gmail.com --imap-password "app-password" \
  --smtp-host smtp.gmail.com --target-email user@gmail.com

# From a config file
dan-adapter -c adapter-config.json telegram
```

**Subcommands:** `email`, `telegram`, `whatsapp`, `whatsapp-web`

| Subcommand | Setup | Best for |
|------------|-------|----------|
| `whatsapp-web` | QR code scan, 2 minutes | Personal use |
| `telegram` | BotFather token, 5 minutes | Personal/team |
| `whatsapp` | Meta Business API, 1-2 days | Commercial |
| `email` | IMAP/SMTP credentials | Formal workflows |

**Chat mode** requires `dan-serve` running (or `dan-up`). Messages route through the server's chat API — same capability router, experience memory, publish/share, run lifecycle, and now the shared concierge routing layer as `dan-chat`.

Adapter chat mode now routes natural-language messages through the server's concierge (same as `dan-chat`); explicit `/find` and `/send` still use local handling for their number-reply follow-ups. File responses from the concierge are detected and sent as attachments.

**Workflow mode** can run standalone (no server needed) — the adapter loads the workflow and runs the engine directly.

### WhatsApp Web setup

```bash
pip install 'dan[whatsapp-web]'    # install neonize dependency
dan-adapter whatsapp-web            # chat mode (general-purpose)
dan-adapter whatsapp-web -w wf.md   # workflow mode (specific workflow)
```

1. A QR code appears in the terminal
2. Open WhatsApp on your phone → Settings → Linked Devices → Link a Device
3. Scan the QR code
4. Send any message — DAN responds through your personal WhatsApp

Session data is stored in `~/.dan/whatsapp-web/` so you only need to pair once.

### Telegram setup

```bash
# 1. Message @BotFather on Telegram, send /newbot, get a token
# 2. Run:
dan-adapter telegram --bot-token "YOUR_TOKEN"
# 3. Message your bot — DAN responds
```

**Common options (all subcommands):**

| Flag | Description | Default |
|------|-------------|---------|
| `-w`, `--workflow` | Workflow file path. When omitted, runs in chat mode. | Chat mode |
| `--server` | DAN server URL (chat mode) | `$DAN_SERVER_URL` or `http://127.0.0.1:8000` |
| `--timeout` | Response timeout (seconds) | `300` |
| `--trigger-mode` | `always` / `keyword` / `pattern` | `always` |
| `--trigger-pattern` | Pattern for keyword/pattern trigger mode | — |
| `--welcome-message` | Greeting on `/start` | Default |
| `--error-message` | Error fallback message | Default |

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
# Quickest way — starts server + drops into chat
dan-up

# Or manually:
dan-serve &
dan-chat
```

### One-shot NL execution

```bash
dan-up    # if server not running
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
