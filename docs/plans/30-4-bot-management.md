# 30-4: Bot Management

**Parent:** [30-telegram-platform](30-telegram-platform.md)
**Status:** completed
**Goal:** Make it trivially easy to create, configure, and manage multiple Telegram bots — from initial BotFather setup through fleet operation.

## User Experience

### First bot (30 seconds)

```bash
$ dan-bot create research-bot
🤖 Let's set up "research-bot"!

1. Open Telegram and message @BotFather
2. Send /newbot
3. Choose a name (e.g., "Research Bot")
4. Choose a username (e.g., "my_research_bot")
5. Copy the token BotFather gives you

Paste your bot token: 123456789:ABCdefGHIjklMNOpqrSTUvwxYZ

✅ Bot created! Token verified — @my_research_bot is ready.

Personality (optional, press Enter to skip):
> Academic research specialist. Thorough and citation-heavy.

Assign projects (comma-separated, press Enter to skip):
> literature-review, paper-writing

Make this the default bot? [y/N]: n

✅ Saved to ~/.dan/telegram/config.json

Start chatting: dan-bot start research-bot
Start all bots: dan-bot start-all
```

### Fleet overview

```bash
$ dan-bot list
┌──────────────┬─────────────────────┬──────────────────────┬─────────┐
│ Name         │ Username            │ Projects             │ Status  │
├──────────────┼─────────────────────┼──────────────────────┼─────────┤
│ research-bot │ @my_research_bot    │ lit-review, papers   │ running │
│ data-bot     │ @my_data_bot        │ equity, analysis     │ running │
│ dan          │ @my_dan_bot         │ (default)            │ stopped │
└──────────────┴─────────────────────┴──────────────────────┴─────────┘

Group: "Research Lab" (-1001234567890) — forum topics enabled
```

### Start the fleet

```bash
$ dan-bot start-all
🤖 Starting 3 bots...
  ✅ research-bot (@my_research_bot) — polling
  ✅ data-bot (@my_data_bot) — polling
  ✅ dan (@my_dan_bot) — polling (default)

All bots running. Press Ctrl+C to stop.
Group: "Research Lab" — 3 bots active
```

## Config File

```json
{
  "_comment": "DAN Telegram bot fleet configuration",
  "bots": {
    "research-bot": {
      "token": "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ",
      "personality": "Academic research specialist. Thorough, citation-heavy.",
      "projects": ["literature-review", "paper-writing"],
      "default": false
    },
    "data-bot": {
      "token": "987654321:ZYXwvuTSRqpONMlkjIHGfedCBA",
      "personality": "Data analysis expert. Concise, numbers-focused.",
      "projects": ["equity-research", "data-analysis"],
      "default": false
    },
    "dan": {
      "token": "111222333:AAABBBcccDDDeeefffGGGhhhiii",
      "personality": "General-purpose assistant. Helpful and versatile.",
      "projects": [],
      "default": true
    }
  },
  "groups": {
    "-1001234567890": {
      "forum_topics": true,
      "topic_map": {
        "12345": "literature-review",
        "12346": "equity-research"
      }
    }
  },
  "settings": {
    "streaming_edits": true,
    "use_reactions": true,
    "auto_pin_deliverables": false,
    "progress_throttle": 5.0,
    "max_inbound_media_mb": 20.0
  }
}
```

Uses JSON for consistency with `~/.dan/mcp.json`. No PyYAML dependency needed.

## Tasks

### 1. Config model (`src/dan/adapters/telegram_config.py`)
- [x] 1-1. `TelegramBotConfig` Pydantic model: `token`, `personality`, `projects: list[str]`, `default: bool`, `allowed_users: list[int]` (optional)
- [x] 1-2. `TelegramGroupConfig` model: `chat_id: int`, `forum_topics: bool`, `topic_map: dict[int, str]`
- [x] 1-3. `TelegramSettings` model: `streaming_edits`, `use_reactions`, `auto_pin_deliverables`, `progress_throttle`, `max_inbound_media_mb`
- [x] 1-4. `TelegramFleetConfig` top-level: `bots: dict[str, TelegramBotConfig]`, `groups: dict[int, TelegramGroupConfig]` (keyed by chat_id, supports multiple groups), `settings: TelegramSettings`
- [x] 1-5. `load_fleet_config(path?)` / `save_fleet_config(config, path?)` — default `~/.dan/telegram/config.json`. Atomic write (tmp + rename). Create `~/.dan/telegram/` if needed. **Use JSON** (consistent with `~/.dan/mcp.json` pattern; avoids adding PyYAML dependency). Include a `_comment` field for inline documentation.
- [x] 1-6. Env var `DAN_TELEGRAM_CONFIG` overrides default config path

### 2. `dan-bot` CLI (`src/dan/cli/bot.py`)

Convention: `dan-bot` with hyphen, matching `dan-run`, `dan-chat`, `dan-serve`, `dan-adapter` etc.

- [x] 2-1. `dan-bot create <name>` — interactive setup:
  - Prompt for bot token
  - Verify token via `asyncio.run(bot.get_me())` (wrap async call for sync CLI context)
  - Resolve `@username` from API response
  - Prompt for personality (optional)
  - Prompt for project assignments (optional, comma-separated)
  - Prompt for default flag
  - Save to config file
- [x] 2-2. `dan-bot list` — Rich table showing name, @username, projects, status (running/stopped), default flag
- [x] 2-3. `dan-bot start <name>` — start a single bot in chat-mode (foreground, Ctrl+C to stop). Reuses `_run_adapter_chat_mode()` from `adapter.py`.
- [x] 2-4. `dan-bot start-all` — start all configured bots as a fleet (foreground). Creates `BotFleet` from 30-2.
- [x] 2-5. `dan-bot stop <name>` — stop a running bot (when running as daemon/background)
- [x] 2-6. `dan-bot remove <name>` — remove bot from config (with confirmation prompt)
- [x] 2-7. `dan-bot edit <name>` — modify personality, projects, default flag interactively
- [x] 2-8. `dan-bot assign <name> <project1,project2,...>` — shortcut for project assignment
- [x] 2-9. `dan-bot group set <chat_id>` — set a group chat ID for multi-bot mode
- [x] 2-10. `dan-bot group info` — show group details (if bot is admin: member list, forum status)
- [x] 2-11. `dan-bot token <name>` — show the stored token (masked by default, `--reveal` flag to unmask)

### 3. Token verification and BotFather guide
- [x] 3-1. `verify_bot_token(token) -> BotInfo` — call `getMe`, return bot username, name, can_join_groups, can_read_all_group_messages
- [x] 3-2. On `dan-bot create`: after verification, check `can_read_all_group_messages`. If false, print warning:
  ```
  ⚠️  Privacy mode is ON for @my_bot.
  For multi-bot group chat, disable it:
    1. Message @BotFather
    2. Send /setprivacy
    3. Select @my_bot
    4. Choose "Disable"
  ```
- [x] 3-3. On `dan-bot start-all`: if any bot has privacy mode on, print the same warning
- [x] 3-4. `dan-bot setup-guide` — print full BotFather setup guide (create bot, disable privacy, set commands, set description)

### 4. Fleet startup integration
- [x] 4-1. `dan-bot start-all` creates `BotFleet` from config, calls `fleet.start()`, blocks until SIGINT
- [x] 4-2. Single `httpx.AsyncClient` shared across all bots for server communication
- [x] 4-3. Each bot's adapter gets its config from the fleet config (token, personality, projects)
- [x] 4-4. On startup: print fleet status panel (Rich formatted)
- [x] 4-5. On Ctrl+C: graceful shutdown of all bots with status messages
- [x] 4-6. Signal handling: first Ctrl+C → graceful stop, second → force exit

### 5. Daemon mode integration
- [x] 5-1. `dan-bot start-all --daemon` — start fleet as a background daemon (uses `dan-service` infrastructure from 26-2)
- [x] 5-2. PID file at `~/.dan/telegram/fleet.pid`
- [x] 5-3. `dan-bot status` — show if fleet daemon is running
- [x] 5-4. `dan-bot logs` — tail the fleet log file (`~/.dan/logs/telegram-fleet.log`)
- [x] 5-5. `dan-service` integration: add telegram fleet as an optional managed service

### 6. Entry point wiring
- [x] 6-1. Register `dan-bot = "dan.cli.bot:main"` in `pyproject.toml` `[project.scripts]`
- [x] 6-2. Also make `dan bot` work as a subcommand of a unified `dan` CLI (if it exists)
- [x] 6-3. `--config` flag on all subcommands to override default config path

### 7. Tests
- [x] 7-1. Unit tests for config load/save/validate
- [x] 7-2. Unit tests for token verification (mock Telegram API)
- [x] 7-3. Unit tests for privacy mode detection and warning
- [x] 7-4. CLI tests: `dan-bot create` with mocked input, `dan-bot list` output format
- [x] 7-5. Fleet startup test: config → BotFleet → all bots running (mock adapters)

## Decisions

- **JSON over YAML for config.** Consistent with `~/.dan/mcp.json` pattern. No PyYAML dependency needed (the project doesn't use it anywhere). Uses a `_comment` field for inline documentation. Users who prefer YAML can write a converter — but keeping one format across all DAN config files reduces complexity.
- **Interactive `create` over manual config editing.** The guided flow verifies the token and checks privacy mode immediately. Users can still edit the JSON directly for advanced configuration.
- **Fleet as foreground process by default.** `--daemon` is opt-in. Most personal use cases benefit from seeing the output in a terminal tab. The daemon mode is for always-on deployment.
- **`dan-bot` as a top-level command, not `dan-adapter telegram`.** The multi-bot use case is fundamentally different from the single-adapter pattern. `dan-adapter telegram` still works for single-bot (backward compat), but `dan-bot` is the primary path for multi-bot. Uses hyphen convention matching `dan-run`, `dan-chat`, `dan-serve` etc.

## Notes

- `python-telegram-bot` v21 `bot.get_me()` returns a `User` object with `username`, `first_name`, `can_join_groups`, `can_read_all_group_messages`, `supports_inline_queries`. This is sufficient for verification and privacy mode detection. The call is async, so the CLI wraps it with `asyncio.run()`.
- The config file intentionally stores tokens in plain text (same as `.env` files and MCP config at `~/.dan/mcp.json`). Users who want encryption can use filesystem-level encryption or a secrets manager. Future: optional keyring integration.
- `dan-bot start <name>` in single-bot mode reuses the existing `_run_adapter_chat_mode()` infrastructure. The fleet mode (`start-all`) uses the new `BotFleet` coordinator from 30-2.
- `dan-adapter telegram --bot-token TOKEN` continues to work for backward compatibility. `dan-bot start <name>` is the recommended path for configured bots (reads token from config, no CLI flags needed).
- Per-bot stop is now implemented: `dan-bot stop <name>` writes a control command to `~/.dan/telegram/fleet.ctl`, which the fleet daemon watches every 2 seconds. `BotFleet.stop_bot(name)` gracefully stops a single bot's polling and removes it from the active set.
- Unified `dan` CLI entry point added at `src/dan/cli/main.py`, registered as `dan = "dan.cli.main:main"` in pyproject.toml. `dan bot ...` dispatches to `dan-bot`.
