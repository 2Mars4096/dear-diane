# DAN Command Reference

> Auto-generated from the command registry. Do not edit manually — run `python scripts/generate_command_docs.py > docs/commands.md` to regenerate.

## Surface Availability

| Group | CLI | Editor | Telegram | WhatsApp |
|-------|-----|--------|----------|----------|
| Workflow | Yes | Yes | Yes | Yes |
| Scheduling | Yes | Yes | Yes | Yes |
| Safety | Yes | Yes | Yes | Yes |
| Progress | Yes | Yes | Yes | Yes |
| Learning | Yes | Yes | Yes | Yes |
| Computer Use | Yes | Yes | Yes | Yes |
| Integration | Yes | Yes | Yes | Yes |
| File & Navigation | — | — | Yes | Yes |
| Session | Yes | Yes | Yes | Yes |

## Workflow Commands

### `/build`

- **Aliases:** `/workflow`
- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Arguments:** `<goal>`
- **Description:** Force workflow-build mode for the current task (creates a reusable artifact)

**Examples:**
- `/build Research X and summarize Y`
- `/build Create a data pipeline`

**Related:** `/goal`, `/list`, `/new`, `/open`, `/rename`, `/run`, `/run-node`, `/saveas`, `/show`, `/undo`

### `/goal`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Arguments:** `[<description>|list|clear]`
- **Description:** Track a high-level goal (stored for context; autonomous execution not yet wired)

**Related:** `/build`, `/list`, `/new`, `/open`, `/rename`, `/run`, `/run-node`, `/saveas`, `/show`, `/undo`

### `/list`

- **Surfaces:** `CLI`
- **Kind:** repl
- **Description:** List saved workflows

**Related:** `/build`, `/goal`, `/new`, `/open`, `/rename`, `/run`, `/run-node`, `/saveas`, `/show`, `/undo`

### `/new`

- **Surfaces:** `CLI`
- **Kind:** repl
- **Description:** Start a new empty workflow

**Related:** `/build`, `/goal`, `/list`, `/open`, `/rename`, `/run`, `/run-node`, `/saveas`, `/show`, `/undo`

### `/open`

- **Surfaces:** `CLI`
- **Kind:** repl
- **Arguments:** `<name>`
- **Description:** Open a saved workflow

**Related:** `/build`, `/goal`, `/list`, `/new`, `/rename`, `/run`, `/run-node`, `/saveas`, `/show`, `/undo`

### `/rename`

- **Surfaces:** `CLI`
- **Kind:** repl
- **Arguments:** `<new_name>`
- **Description:** Rename current workflow

**Related:** `/build`, `/goal`, `/list`, `/new`, `/open`, `/run`, `/run-node`, `/saveas`, `/show`, `/undo`

### `/run`

- **Surfaces:** `CLI`
- **Kind:** repl
- **Arguments:** `[workflow_name] [--input key=value]`
- **Description:** Run a workflow

**Related:** `/build`, `/goal`, `/list`, `/new`, `/open`, `/rename`, `/run-node`, `/saveas`, `/show`, `/undo`

### `/run-node`

- **Surfaces:** `CLI`
- **Kind:** repl
- **Arguments:** `<node_id>`
- **Description:** Run a single node from the current workflow

**Related:** `/build`, `/goal`, `/list`, `/new`, `/open`, `/rename`, `/run`, `/saveas`, `/show`, `/undo`

### `/saveas`

- **Surfaces:** `CLI`
- **Kind:** repl
- **Arguments:** `<name>`
- **Description:** Save workflow under a new name

**Related:** `/build`, `/goal`, `/list`, `/new`, `/open`, `/rename`, `/run`, `/run-node`, `/show`, `/undo`

### `/show`

- **Surfaces:** `CLI`
- **Kind:** repl
- **Arguments:** `[--code|--json|--stats]`
- **Description:** Show current workflow (ASCII DAG, code, JSON, or stats)

**Related:** `/build`, `/goal`, `/list`, `/new`, `/open`, `/rename`, `/run`, `/run-node`, `/saveas`, `/undo`

### `/undo`

- **Surfaces:** `CLI`
- **Kind:** repl
- **Description:** Undo last graph mutation

**Related:** `/build`, `/goal`, `/list`, `/new`, `/open`, `/rename`, `/run`, `/run-node`, `/saveas`, `/show`

## Scheduling Commands

### `/plan`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Arguments:** `[--replan]`
- **Description:** Show current plan schedule; --replan forces re-decomposition

**Related:** `/schedule`

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

**Related:** `/plan`

## Safety Commands

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

## Progress Commands

### `/progress`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Arguments:** `[full|compact|minimal]`
- **Description:** Set progress verbosity level

## Learning Commands

### `/domains`

- **Aliases:** `/domain`
- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Arguments:** `<list|known|add|remove|clear> [domain or comma-separated domains]`
- **Description:** Inspect and edit saved canonical domain preferences

| Subcommand | Arguments | Description |
|------------|-----------|-------------|
| `/domains list` | — | Show saved profile domains and a known-domain preview |
| `/domains known` | — | List all known canonical domains from the live taxonomy |
| `/domains add` | `<domain or comma-separated domains>` | Add one or more saved common domains |
| `/domains remove` | `<domain or comma-separated domains>` | Remove one or more saved common domains |
| `/domains clear` | — | Clear all saved common domains |

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

### `/autonomy`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Arguments:** `[auto|careful|balanced|aggressive] [--project]`
- **Description:** Show or change autonomy level for this session or project

**Related:** `/cost`, `/exit`, `/help`, `/retry`, `/search`

### `/cost`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Description:** Show session token usage and estimated cost per model

**Related:** `/autonomy`, `/exit`, `/help`, `/retry`, `/search`

### `/exit`

- **Aliases:** `/quit`
- **Surfaces:** `CLI`
- **Kind:** repl
- **Description:** Exit the chat session

**Related:** `/autonomy`, `/cost`, `/help`, `/retry`, `/search`

### `/help`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Arguments:** `[group]`
- **Description:** Show available commands

**Related:** `/autonomy`, `/cost`, `/exit`, `/retry`, `/search`

### `/retry`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Description:** Resend the last user message in the current task

**Related:** `/autonomy`, `/cost`, `/exit`, `/help`, `/search`

### `/search`

- **Surfaces:** `CLI` `Editor` `TG` `WA`
- **Kind:** chat
- **Arguments:** `<query>`
- **Description:** Run an explicit grounded web search with fetched excerpts

**Related:** `/autonomy`, `/cost`, `/exit`, `/help`, `/retry`

---

*For CLI binary commands (`dan-serve`, `dan-chat`, `dan-run`, etc.), see [docs/cli.md](cli.md).*

