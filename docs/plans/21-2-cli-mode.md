# 21-2: CLI Mode

**Parent:** [21-author-distribute](21-author-distribute.md)
**Status:** completed
**Goal:** Run DAN workflows from the terminal with a Rich TUI for progress, interactive HumanNode support, meta-orchestrator NL path, and supervisor-style background execution.

## Context

DAN workflows currently require either the visual editor (browser) or raw Python scripting. A CLI enables headless/CI/server deployments, background agents, and terminal-native interaction. The CLI leverages the existing `Engine`, `MetaController`, and `HumanInTheLoop` infrastructure.

## Tasks

- [x] 1. **Core CLI framework (`src/dan/cli/`)**
  - [x] 1-1. Extend `src/dan/cli/__init__.py` with shared utilities: `load_env()`, `resolve_config()`, `ensure_dan_dir()`, `_try_import_rich()`
  - [x] 1-2. Create `src/dan/cli/run.py` with `main()` entry point using `argparse`
  - [x] 1-3. Configuration resolution order: CLI flags → `.env` file → environment variables → defaults
  - [x] 1-4. Common flags: `--api-key`, `--model`, `--base-url`, `--workspace` (overrides `DAN_WORKSPACE_ROOT`)
  - [x] 1-5. Entry points already wired in 21-1 (`dan-run`, `dan-status`, `dan-logs`) — verified

- [x] 2. **Workflow loading and execution**
  - [x] 2-1. Accept workflow sources: JSON file path, markdown directory path, Python file path, or quoted NL goal string
  - [x] 2-2. **JSON path** → `Graph.model_validate(json.load(f))` → `Engine.run(graph, inputs)`
  - [x] 2-3. **Markdown path** → `dan.loader.load(path)` → `Engine.run(graph, inputs)`
  - [x] 2-4. **Python file path** → `importlib.util.spec_from_file_location` to load module, look for `graph` attribute or `build()` callable
  - [x] 2-5. **NL goal string** (detected by absence of file extension / path) → `MetaController` session → autonomous plan + execute loop
  - [x] 2-6. `--input key=value` flag (repeatable) for workflow input injection; parsed into `dict[str, str]`
  - [x] 2-7. `--input-json '{"key": "value"}'` for complex input values
  - [x] 2-8. Error handling: file not found, invalid graph, missing inputs → clear error messages

- [x] 3. **Rich TUI progress display**
  - [x] 3-1. Graceful fallback to plain text if rich not installed (`PlainDisplay` fallback)
  - [x] 3-2. **Live status table**: node name, type, status (pending/running/completed/failed), duration, tokens via `TUIDisplay`
  - [x] 3-3. **Progress counter**: X/Y nodes completed in table title
  - [x] 3-4. **Streaming output**: LLM thinking and intermediate text tracked via event callback
  - [x] 3-5. **Final summary**: total time, total tokens, total cost, node success/failure breakdown
  - [x] 3-6. **Quiet mode**: `--quiet` / `-q` via `QuietDisplay` — outputs only final result JSON
  - [x] 3-7. **Verbose mode**: `--verbose` / `-v` shows all engine events
  - [x] 3-8. **JSON output mode**: `--output-format json` outputs structured JSONL events via `JSONLDisplay`

- [x] 4. **Interactive HumanNode rendering**
  - [x] 4-1. `--interactive` flag (default when stdin is a TTY)
  - [x] 4-2. `--headless` flag: auto-skips HumanNode via `AutoRenderer`
  - [x] 4-3. Support render_mode types: `approval` (y/n), `form` (key-value), `selection` (numbered list), `text` (free text)
  - [x] 4-4. Timeout: `--human-timeout 300` with default/skip on timeout
  - [x] 4-5. `CLIHumanRenderer` conforming to `HumanRenderer` protocol. Added `human_renderer` parameter to `Engine.__init__()`, passed through to `ExecutionContext`.

- [x] 5. **Meta-orchestrator NL path**
  - [x] 5-1. NL goal detection → `MetaController.create_session()` + `run_session()`
  - [x] 5-2. Display planning phase: "Planning workflow..."
  - [x] 5-3. Plan review: "Proceed? [Y/n]" in interactive mode
  - [x] 5-4. `--auto-approve` flag for headless NL execution
  - [x] 5-5. On failure: show repair classification from session history

- [x] 6. **Background / supervisor mode**
  - [x] 6-1. `--background` / `--bg` via `subprocess.Popen`, PID to `~/.dan/runs/{run_id}.pid`
  - [x] 6-2. `src/dan/cli/status.py` — `dan-status`: lists runs with Rich table or plain text
  - [x] 6-3. `src/dan/cli/logs.py` — `dan-logs <run_id>`: tails event log
  - [x] 6-4. `dan-logs --follow` streams new events (poll + seek)
  - [x] 6-5. Background writes events to `~/.dan/runs/{run_id}.events.jsonl`
  - [x] 6-6. SIGTERM/SIGINT signal handler for graceful shutdown
  - [x] 6-7. Entry points verified

- [x] 7. **Output and artifacts**
  - [x] 7-1. Default: print final output with Rich formatting
  - [x] 7-2. `--output <path>`: write final output JSON to file
  - [x] 7-3. `--artifacts-dir <path>`: directory for artifacts (default: `./output/`)
  - [x] 7-4. Print artifact manifest on completion

- [x] 8. **Tests** (63 tests, all passing)
  - [x] 8-1. Unit tests for CLI argument parsing and config resolution (`test_args.py`)
  - [x] 8-2. Unit tests for workflow source detection (`test_source_detection.py`)
  - [x] 8-3. Integration tests: headless JSON workflow with mocked engine (`test_integration.py`)
  - [x] 8-4. Unit tests for Rich TUI rendering — PlainDisplay, QuietDisplay, JSONLDisplay, TUIDisplay (`test_tui.py`)
  - [x] 8-5. Unit tests for HumanNode interactive rendering — all render modes, timeout, EOF (`test_human_renderer.py`)

## Decisions

- Use `argparse` (stdlib) rather than `click`/`typer` to avoid adding a core dependency. Rich is optional (TUI enhancement).
- Separate entry points (`dan-run`, `dan-status`, `dan-logs`) rather than a single `dan` command with subcommands. Keeps each command simple and independently usable. No unified entry point needed.
- Background mode uses `subprocess.Popen` (not `os.fork`) to spawn a separate process. Avoids asyncio event loop complications. PID file + event JSONL log for monitoring. Adequate for local use; production deployments would use systemd/docker.
- NL goal detection heuristic: if the argument doesn't have a file extension, doesn't exist as a path, and is not a recognized format → treat as NL goal. Explicit `--goal` flag as override.
- The CLI reuses `Engine` directly — it does not go through the FastAPI server. The server is for the visual editor; the CLI is a direct engine consumer.
- `CLIHumanRenderer` implements the existing `HumanRenderer` protocol from `dan.engine.executor`. Headless mode uses the existing `AutoRenderer`. No new protocol needed.
- Python file execution uses `importlib` (not `exec`). Convention: file must export a `graph` attribute (Graph instance) or a `build()` function returning one.

## Notes

- The CLI is the foundation for `dan-publish` (21-3) — both need headless execution, event streaming, and HumanNode handling.
- Rich TUI is a presentation layer over the existing engine event system — no changes to the engine itself.
- Background mode event logs use the same JSONL format as `RunStore`, enabling `dan-logs` to work on both CLI-started and server-started runs.
