# DAN Command Reference

> Auto-generated from the command registry. Do not edit manually — run `python scripts/generate_command_docs.py > docs/commands.md` to regenerate.

## Surface Availability

| Group | CLI | Editor | Telegram | WhatsApp |
|-------|-----|--------|----------|----------|
| Workflow | Yes | Yes | Yes | Yes |
| Model & Config | Yes | Yes | Yes | Yes |
| Memory | Yes | Yes | Yes | Yes |
| Scheduling | Yes | Yes | Yes | Yes |
| Safety | Yes | Yes | Yes | Yes |
| Continuity | Yes | Yes | Yes | Yes |
| Progress | Yes | Yes | Yes | Yes |
| Learning | Yes | Yes | Yes | Yes |
| Computer Use | Yes | Yes | Yes | Yes |
| Integration | Yes | Yes | Yes | Yes |
| File & Navigation | — | — | Yes | Yes |
| Session | Yes | Yes | Yes | Yes |

## Workflow Commands

### `/build-logs`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Description:** Show build session logs

**Related:** `/build-status`, `/build-stop`, `/list`, `/new`, `/open`, `/rename`, `/run`, `/run-node`, `/save`, `/saveas`, `/show`, `/undo`

### `/build-status`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Description:** Show build session status

**Related:** `/build-logs`, `/build-stop`, `/list`, `/new`, `/open`, `/rename`, `/run`, `/run-node`, `/save`, `/saveas`, `/show`, `/undo`

### `/build-stop`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Description:** Stop active build session

**Related:** `/build-logs`, `/build-status`, `/list`, `/new`, `/open`, `/rename`, `/run`, `/run-node`, `/save`, `/saveas`, `/show`, `/undo`

### `/list`

- **Surfaces:** `CLI`
- **Kind:** repl
- **Description:** List saved workflows

**Related:** `/build-logs`, `/build-status`, `/build-stop`, `/new`, `/open`, `/rename`, `/run`, `/run-node`, `/save`, `/saveas`, `/show`, `/undo`

### `/new`

- **Surfaces:** `CLI`
- **Kind:** repl
- **Description:** Start a new empty workflow

**Related:** `/build-logs`, `/build-status`, `/build-stop`, `/list`, `/open`, `/rename`, `/run`, `/run-node`, `/save`, `/saveas`, `/show`, `/undo`

### `/open`

- **Surfaces:** `CLI`
- **Kind:** repl
- **Arguments:** `<name>`
- **Description:** Open a saved workflow

**Related:** `/build-logs`, `/build-status`, `/build-stop`, `/list`, `/new`, `/rename`, `/run`, `/run-node`, `/save`, `/saveas`, `/show`, `/undo`

### `/rename`

- **Surfaces:** `CLI`
- **Kind:** repl
- **Arguments:** `<new_name>`
- **Description:** Rename current workflow

**Related:** `/build-logs`, `/build-status`, `/build-stop`, `/list`, `/new`, `/open`, `/run`, `/run-node`, `/save`, `/saveas`, `/show`, `/undo`

### `/run`

- **Surfaces:** `CLI`
- **Kind:** repl
- **Arguments:** `[workflow_name] [--input key=value]`
- **Description:** Run a workflow

**Related:** `/build-logs`, `/build-status`, `/build-stop`, `/list`, `/new`, `/open`, `/rename`, `/run-node`, `/save`, `/saveas`, `/show`, `/undo`

### `/run-node`

- **Surfaces:** `CLI`
- **Kind:** repl
- **Arguments:** `<node_id>`
- **Description:** Run a single node from the current workflow

**Related:** `/build-logs`, `/build-status`, `/build-stop`, `/list`, `/new`, `/open`, `/rename`, `/run`, `/save`, `/saveas`, `/show`, `/undo`

### `/save`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Arguments:** `[name]`
- **Description:** Save the current workflow

**Related:** `/build-logs`, `/build-status`, `/build-stop`, `/list`, `/new`, `/open`, `/rename`, `/run`, `/run-node`, `/saveas`, `/show`, `/undo`

### `/saveas`

- **Surfaces:** `CLI`
- **Kind:** repl
- **Arguments:** `<name>`
- **Description:** Save workflow under a new name

**Related:** `/build-logs`, `/build-status`, `/build-stop`, `/list`, `/new`, `/open`, `/rename`, `/run`, `/run-node`, `/save`, `/show`, `/undo`

### `/show`

- **Surfaces:** `CLI`
- **Kind:** repl
- **Arguments:** `[--code|--json|--stats]`
- **Description:** Show current workflow (ASCII DAG, code, JSON, or stats)

**Related:** `/build-logs`, `/build-status`, `/build-stop`, `/list`, `/new`, `/open`, `/rename`, `/run`, `/run-node`, `/save`, `/saveas`, `/undo`

### `/undo`

- **Surfaces:** `CLI`
- **Kind:** repl
- **Description:** Undo last graph mutation

**Related:** `/build-logs`, `/build-status`, `/build-stop`, `/list`, `/new`, `/open`, `/rename`, `/run`, `/run-node`, `/save`, `/saveas`, `/show`

## Model & Config Commands

### `/cost`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Description:** Show session cost summary

**Related:** `/model`, `/retry`, `/status`

### `/model`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Arguments:** `[model_name]`
- **Description:** Show or change the current LLM model

**Related:** `/cost`, `/retry`, `/status`

### `/retry`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Description:** Retry the last failed request

**Related:** `/cost`, `/model`, `/status`

### `/status`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Description:** Show system status and active runs

**Related:** `/cost`, `/model`, `/retry`

## Memory Commands

### `/memory-delete`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Arguments:** `<memory_id>`
- **Description:** Delete a specific memory item

**Related:** `/memory-forget`, `/memory-search`, `/memory-stats`

### `/memory-forget`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Arguments:** `<keyword>`
- **Description:** Forget memories matching a keyword

**Related:** `/memory-delete`, `/memory-search`, `/memory-stats`

### `/memory-search`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Arguments:** `<query>`
- **Description:** Search stored memories

**Related:** `/memory-delete`, `/memory-forget`, `/memory-stats`

### `/memory-stats`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Description:** Show memory usage statistics

**Related:** `/memory-delete`, `/memory-forget`, `/memory-search`

## Scheduling Commands

### `/goal`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Arguments:** `"<metric> <op> <target>" [--timeout <duration>] [--eval <mode>]`
- **Description:** Start a goal-oriented loop: iterate until metric met or deadline expires

**Related:** `/goal-status`, `/goal-stop`, `/schedule`

### `/goal-status`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Description:** Show goal loop progress

**Related:** `/goal`, `/goal-stop`, `/schedule`

### `/goal-stop`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Description:** Stop active goal loop and return best result

**Related:** `/goal`, `/goal-status`, `/schedule`

### `/schedule`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Arguments:** `<add|list|remove|pause|resume|history> [args]`
- **Description:** Manage scheduled tasks

| Subcommand | Arguments | Description |
|------------|-----------|-------------|
| `/schedule add` | `"<action>" <cron_or_interval>` | Add a new scheduled task |
| `/schedule list` | — | List all schedules |
| `/schedule remove` | `<id|name>` | Remove a schedule |
| `/schedule pause` | `<id|name>` | Pause a schedule |
| `/schedule resume` | `<id|name>` | Resume a paused schedule |
| `/schedule history` | `<id|name>` | Show run history for a schedule |

**Related:** `/goal`, `/goal-status`, `/goal-stop`

## Safety Commands

### `/completion`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Description:** Show completion check statistics

**Related:** `/pii`

### `/pii`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Arguments:** `<add|list|remove|clear-session> [args]`
- **Description:** Manage PII protection settings
- **Requires:** `pii_enabled`

| Subcommand | Arguments | Description |
|------------|-----------|-------------|
| `/pii add` | `"<value>" --category <category>` | Add a sensitive word |
| `/pii list` | — | List protected words |
| `/pii remove` | `"<value>"` | Remove a sensitive word |
| `/pii clear-session` | — | Clear current session mappings |

**Related:** `/completion`

## Continuity Commands

### `/follow-ups`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Arguments:** `[on|off]`
- **Description:** List or toggle proactive follow-ups

| Subcommand | Arguments | Description |
|------------|-----------|-------------|
| `/follow-ups on` | — | Enable follow-ups |
| `/follow-ups off` | — | Disable follow-ups |

**Related:** `/resume`, `/sync`

### `/resume`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Arguments:** `[task_name]`
- **Description:** Resume a paused or previous task

**Related:** `/follow-ups`, `/sync`

### `/sync`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Arguments:** `[--allow-group]`
- **Description:** Pull latest context from all surfaces for current project

**Related:** `/follow-ups`, `/resume`

## Progress Commands

### `/progress`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Arguments:** `[full|compact|minimal]`
- **Description:** Set progress verbosity level

## Learning Commands

### `/adaptations`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Description:** List pending adaptations with confidence and approval status

**Related:** `/corrections`

### `/corrections`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Description:** List recent correction-driven learning events

**Related:** `/adaptations`

## Computer Use Commands

### `/computer`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Arguments:** `<status|doctor|approve> [args]`
- **Description:** Computer control status, diagnostics, and approvals

| Subcommand | Arguments | Description |
|------------|-----------|-------------|
| `/computer status` | — | Show computer control status and permissions |
| `/computer doctor` | — | Check OS permissions and capabilities |
| `/computer approve` | `<request-id>` | Approve a pending computer-use action |

## Integration Commands

### `/mcp`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Arguments:** `<install|list|remove|tools> [args]`
- **Description:** Manage MCP tool servers

| Subcommand | Arguments | Description |
|------------|-----------|-------------|
| `/mcp install` | `<package_or_name>` | Install an MCP server package |
| `/mcp list` | — | List connected MCP servers |
| `/mcp remove` | `<server_name>` | Remove an MCP server |
| `/mcp tools` | — | List tools from all MCP servers |

**Related:** `/skill`

### `/skill`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Arguments:** `<list|info|import|scan> [args]`
- **Description:** Manage skills (IDE-compatible SKILL.md format)

| Subcommand | Arguments | Description |
|------------|-----------|-------------|
| `/skill list` | `[user|project]` | List loaded skills, optionally filter by scope |
| `/skill info` | `<name>` | Show skill details and content preview |
| `/skill import` | `<path>` | Import skill from Cursor/Claude/Codex or any path |
| `/skill scan` | — | Rescan all skill directories |

**Related:** `/mcp`

## File & Navigation Commands

### `/find`

- **Surfaces:** `TG` `WA`
- **Kind:** adapter_local
- **Arguments:** `<query>`
- **Description:** Find a file on your computer

**Related:** `/send`

### `/send`

- **Surfaces:** `TG` `WA`
- **Kind:** adapter_local
- **Arguments:** `<path>`
- **Description:** Send you a file

**Related:** `/find`

## Session Commands

### `/cancel`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Arguments:** `[run_id|latest|last_failed|paused]`
- **Description:** Cancel the current run or task

**Related:** `/exit`, `/help`

### `/exit`

- **Aliases:** `/quit`
- **Surfaces:** `CLI`
- **Kind:** repl
- **Description:** Exit the chat session

**Related:** `/cancel`, `/help`

### `/help`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Arguments:** `[group]`
- **Description:** Show available commands

**Related:** `/cancel`, `/exit`

---

*For CLI binary commands (`dan-serve`, `dan-chat`, `dan-run`, etc.), see [docs/cli.md](cli.md).*

