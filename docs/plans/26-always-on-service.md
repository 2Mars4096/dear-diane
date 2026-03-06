# 26: Always-On Personal Service

**Status:** completed
**Goal:** Make DAN a persistent background service — always running, always reachable, always remembering — across all chat surfaces.

## Motivation

Phase 15 (Plan 25) collapses all capabilities behind the conversational interface. Phase 16 makes that interface **persistent**: always running, always reachable, always remembering. DAN becomes a background service you talk to anytime — from `dan-chat`, the editor ChatPanel, Telegram, WhatsApp, or any future chat surface. It remembers your past work, preferences, and patterns across sessions. It notifies you when things finish or need attention.

Today's gaps:
- `dan-chat` requires a running `dan-serve` — no local fallback, no zero-friction entry.
- No daemon mode — server dies with the terminal session.
- No cross-session memory — DAN forgets preferences and past context between sessions.
- No notifications — users must poll for run completion or human-input-needed.
- CLI output is plain text — no visual graph display, no live progress, no structured diffs.

## Existing Infrastructure

| Component | Location | Relevance |
|---|---|---|
| `dan-serve` | `server/__main__.py` | FastAPI/uvicorn server, `--host`, `--port`, `--reload` |
| `ChatClient` | `cli/chat.py` | httpx/websockets client for server-mode `dan-chat` |
| `DanClientOrLocal` | `client/local.py` | Server/local fallback pattern — already proven for `dan-run` |
| `ChatManager` | `server/chat_manager.py` | 5 modes, mutation tools, streaming, context window management |
| `GraphStore` | `server/graph_store.py` | Filesystem JSON persistence |
| `ChatStore` | `server/chat_store.py` | Per-workflow chat thread persistence |
| `GlobalEventBus` | `gateway/events.py` | Cross-surface event streaming, async subscriptions |
| `ExperienceStore/Index` | `engine/experience.py` | Workflow history, semantic search |
| `MemoryStore` | `engine/memory_store.py` | Key-value memory with index sidecar |
| `CLIHumanRenderer` | `cli/run.py` | Terminal HumanNode prompt rendering |
| `TUIDisplay` | `cli/run.py` | Rich TUI for `dan-run` (status panels, progress) |
| `_try_import_rich()` | `cli/__init__.py` | Optional Rich library detection |
| `ensure_dan_dir()` | `cli/__init__.py` | Creates `~/.dan/runs/` |
| `decompile()` | `builder/decompiler.py` | Graph → Python builder code (for `/show --code`) |

## Sub-Plans

| # | Sub-Plan | Scope | Effort | Dependencies |
|---|----------|-------|--------|--------------|
| [26-1](26-1-local-chat-and-launcher.md) | Local Chat and Launcher | `dan-chat` works without `dan-serve` (in-process ChatManager). `dan up` / `dan down` for zero-friction server lifecycle. PID file at `~/.dan/server.pid`. | ~3 days | None (foundational) |
| [26-2](26-2-daemon-mode.md) | Daemon Mode | `dan service install/uninstall/start/stop/status/logs`. macOS launchd plist + Linux systemd unit. Auto-start at login. Health checks. Log rotation to `~/.dan/logs/`. | ~2 days | 26-1 (PID file, server lifecycle) |
| [26-3](26-3-persistent-user-context.md) | Persistent User Context | Cross-session conversation memory. Preference extraction (models, formats, domains). Quick-resume recent workflows on startup. Preference suggestions. | ~3 days | 26-1 (local mode needs profile) |
| [26-4](26-4-notifications.md) | Notifications | Server-side notifications on run completion/failure/human-input-needed via macOS Notification Center + webhook callback, plus opt-in CLI terminal bell. `NotificationManager` wired to `GlobalEventBus`. | ~2 days | 26-1, 26-2 (daemon for always-on) |
| [26-5](26-5-rich-cli-display.md) | Rich CLI Display | ASCII DAG for `/show`. Streaming node-by-node progress for `/run`. Mutation diff display. Rich table for `/list`. `/show --code`/`--json`/`--stats`. | ~2 days | None (purely presentation) |

**Total effort:** ~12 days

## Dependencies / Sequencing

```
26-1 (Local Chat + Launcher) ← foundational, start here
  ├→ 26-2 (Daemon Mode) ← wraps server lifecycle from 26-1
  ├→ 26-3 (Persistent User Context) ← needs local mode + chat sessions
  └→ 26-4 (Notifications) ← needs server running (from 26-1 or 26-2)

26-5 (Rich CLI Display) ← independent, can start anytime
```

**Recommended sequence:**
1. **26-1** first — defines local mode and `dan up`/`dan down`, the foundation for everything else.
2. **26-5** can start in parallel with 26-1 (no overlap in files or concepts).
3. **26-2** after 26-1 — extends the server lifecycle to OS-level daemon management.
4. **26-3** after 26-1 — needs local chat sessions to extract preferences from.
5. **26-4** after 26-1 and 26-2 — notifications are most useful when the server is always running.

## Architecture

### `~/.dan/` Directory Layout (Phase 16 additions)

```
~/.dan/
  server.pid            # PID + port of background server (26-1)
  server.lock           # Lock file for concurrent start prevention (26-1)
  profile.json          # User preferences and recent workflows (26-3)
  notifications.json    # Notification channel config (26-4)
  local/                # Local-mode storage (26-1)
    graphs/             # GraphStore base dir for local mode
    chats/              # ChatStore threads live here under the local root
    runs/               # Local RunStore metadata for chat parity
  logs/                 # Server and service logs (26-2)
    server.stdout.log
    server.stderr.log
  conversation_memory/  # Cross-session conversation summaries (26-3)
  runs/                 # Background run PIDs and events (existing)
  cache/                # Node result cache (existing)
  blocks/               # User-level blocks (existing)
```

### Local vs Server Mode

```
dan-chat / dan up
  │
  ├─ Server available? ──yes──→ ChatClient (httpx/WS) → dan-serve
  │
  └─ No / --local ──→ LocalChatRuntime (in-process)
                         ├── ChatManager
                         ├── GraphStore (~/.dan/local/)
                         ├── ChatStore (~/.dan/local/)
                         └── Engine (for /run)
```

### Notification Flow

```
Engine events → RunManager → GlobalEventBus → NotificationManager
                                                  ├── macOS (osascript)
                                                  ├── Terminal bell (\a)
                                                  └── Webhook (httpx POST)
```

## Key Decisions

- **Local mode is real, not degraded.** All REPL commands work in local mode. The only difference is no server process — ChatManager runs in the same process as the REPL.
- **Chat parity requires local run management, not just local execution.** Local mode should preserve `/run` command parsing, run-event streaming, cancellation, and HumanNode submission semantics closely enough that the existing REPL loop keeps working.
- **`dan up` is the single entry point.** One command from zero to chatting. It manages the server lifecycle transparently.
- **OS service management is optional.** `dan up`/`dan down` work without `dan service install`. Daemon mode is for users who want auto-start at login.
- **Preferences are extracted, not configured.** Users don't fill out a profile form. DAN learns from usage patterns and confirms before persisting.
- **Rich output degrades gracefully.** All display features work without the Rich library — just less pretty.
- **Notifications are split by responsibility.** Daemon-side channels stay surface-agnostic (macOS/webhook), while terminal bells are emitted by the CLI processes that already consume run/chat events.

## Success Criteria

- `dan up` from a fresh install drops into a working chat in < 15 seconds.
- `dan-chat --local` works fully offline (LLM API key aside).
- After 5+ sessions, DAN remembers the user's preferred model and suggests it.
- Run completion triggers a macOS notification when running as a daemon.
- `/show` renders a readable ASCII DAG for a 10-node workflow.
- All features degrade gracefully (no Rich, no macOS, no server).
