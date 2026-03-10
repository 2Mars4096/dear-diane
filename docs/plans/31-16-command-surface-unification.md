# 31-16: Command Surface Unification

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** not-started
**Goal:** Create one canonical command model for DAN across CLI binaries, REPL, chat, and messaging surfaces — a single registry that drives dispatch, help text, availability checks, and docs. Eliminate the current fragmentation where command metadata is scattered across runtime code, adapter translation tables, Telegram menus, and docs/cli.md.

## Problem

DAN's command surface is fragmented across four layers that drift independently:

- **Fast-command prefixes** are hardcoded in `concierge/runtime.py`: `("/save", "/build-", "/memory-", "/mcp", "/model", "/cost", "/retry", "/status")`. Adding a new command means editing this tuple, the corresponding `_try_fast_command()` method, and hoping the docs and adapters get updated.
- **Adapter slash-command translation** is a separate handwritten dict in `cli/adapter.py` that maps a subset of commands to natural-language equivalents. It has its own `/help` response with a different command list than the concierge.
- **Telegram bot commands** are set via `set_my_commands()` in the adapter with yet another subset of available commands.
- **User-facing docs** (`docs/cli.md`) mix binary commands (`dan-serve`), REPL commands (`/run`, `/show`), and chat fast commands (`/model`, `/cost`) in one table without distinguishing their kind, surface availability, or state requirements.
- **Planned commands** from 31-6 through 31-15 will add ~12 more commands (`/goal`, `/goal-status`, `/goal-stop`, `/schedule`, `/resume`, `/follow-ups`, `/pii`, `/sync`, `/progress`, `/completion`, `/corrections`, `/adaptations`). Without a registry, each will repeat the same manual wiring in 3-4 places.

The result: users can't discover which commands work on their current surface, and contributors can't be sure a new command is wired everywhere it should be.

## Tasks

- [ ] 1. **Command taxonomy**
  - [ ] 1-1. Define 4 command kinds: `binary` (shell entry points like `dan-serve`), `repl` (REPL-only like `/run`, `/show`, `/undo`), `chat` (slash commands handled by concierge — work on all surfaces), `adapter_local` (handled by the adapter before server dispatch, like `/find`, `/send`)
  - [ ] 1-2. For each existing command, classify its kind, supported surfaces, arguments, state requirements (e.g., requires active workflow, requires server, requires daemon), and handler owner
  - [ ] 1-3. Produce a canonical command inventory as a structured data file (Python module or JSON) that becomes the single source of truth

- [ ] 2. **CommandRegistry**
  - [ ] 2-1. `CommandDescriptor` model: `name: str`, `aliases: list[str]`, `kind: Literal["binary", "repl", "chat", "adapter_local"]`, `surfaces: list[str]` (cli, editor, telegram, whatsapp, whatsapp-web, email, all), `args_schema: str | None` (brief arg syntax like `<name>`, `<id> [--force]`), `help_text: str`, `examples: list[str]`, `handler: str` (dotted path to callable, e.g. `"dan.server.concierge.runtime.Concierge._handle_model_command"`), `requires: list[str]` (e.g., "active_workflow", "server", "daemon", "pii_enabled"), `group: str` (e.g., "workflow", "memory", "model", "scheduling", "safety", "status", "learning"), `subcommands: dict[str, SubcommandDescriptor] | None` (for commands like `/schedule add|list|remove`, `/pii add|list|remove|clear-session`, `/mcp install|list|remove|tools`), `hidden: bool = False` (suppress from `/help` and `set_my_commands` — for internal pseudo-commands like preference confirmation words), `is_async: bool = True` (whether the handler is a coroutine — default async; dispatch awaits accordingly)
  - [ ] 2-1b. `SubcommandDescriptor` model: `name: str`, `args_schema: str | None`, `help_text: str`, `handler: str | None` (if None, parent handler dispatches internally based on subcommand name). Parent command's `handler` is called when no subcommand matches.
  - [ ] 2-2. `CommandRegistry` — singleton loaded at startup. Provides: `get(name) -> CommandDescriptor | None`, `list_by_kind(kind)`, `list_by_surface(surface)`, `list_by_group(group)`, `is_available(name, surface, state) -> bool`
  - [ ] 2-3. Populate registry with all existing commands (binary, REPL, chat, adapter_local) from the taxonomy in task 1
  - [ ] 2-4. Replace `_FAST_COMMAND_PREFIXES` tuple in `runtime.py` with `registry.list_by_kind("chat")` — no more manual prefix maintenance

- [ ] 3. **Concierge dispatch integration**
  - [ ] 3-1. `_is_fast_command()` checks registry instead of hardcoded tuple
  - [ ] 3-2. `_try_fast_command()` dispatches via registry handler lookup instead of a chain of if/elif blocks. Each handler is a callable registered in the descriptor (or discovered by module path).
  - [ ] 3-3. New commands from 31-6+ just register a `CommandDescriptor` — no manual wiring in `_try_fast_command()` needed
  - [ ] 3-4. Keep backward compat: existing commands work identically; only the dispatch path changes

- [ ] 4. **Adapter surface integration**
  - [ ] 4-1. Replace the handwritten `_translate_slash_command()` dict in `cli/adapter.py` with registry lookup: if command is `adapter_local`, handle locally; if command is `chat`, forward to server; if command is unknown, forward to server (concierge handles unknowns gracefully)
  - [ ] 4-2. Generate `/help` response from `registry.list_by_surface(current_surface)` filtered by `hidden=False` so help text is always complete and current. Support filtered help: `/help memory`, `/help scheduling` shows only that group.
  - [ ] 4-3. Generate Telegram `set_my_commands()` list from `registry.list_by_surface("telegram")` filtered by `hidden=False` — bot command menu stays in sync automatically. UX note: Telegram menus work best with 10-12 commands; if more are available, pick the top-priority commands for the menu and make the rest discoverable via `/help`.
  - [ ] 4-4. Generate WhatsApp-compatible help text from registry (plain text, no buttons)

- [ ] 5. **REPL integration**
  - [ ] 5-1. `dan-chat` REPL `/help` output derived from `registry.list_by_kind("repl") + registry.list_by_kind("chat")` for the CLI surface
  - [ ] 5-2. Tab-completion candidates derived from registry command names + aliases
  - [ ] 5-3. Command not-found message: if user types an unknown `/command`, suggest the closest match from registry (Levenshtein distance or prefix match)

- [ ] 6. **Centered command docs**
  - [ ] 6-1. New `docs/commands.md` — the canonical command reference, organized by group:
    - **Workflow** (`/run`, `/run-node`, `/run-subgraph`, `/show`, `/save`, `/saveas`, `/list`, `/open`, `/new`, `/rename`, `/undo`)
    - **Model & Config** (`/model`, `/cost`, `/status`, `/retry`)
    - **Memory** (`/memory-stats`, `/memory-search`, `/memory-delete`, `/memory-forget`, `/memory-confirm`, `/memory-reject`)
    - **Scheduling** (`/goal`, `/goal-status`, `/goal-stop`, `/schedule` with subcommands `add|list|remove|pause|resume|history`)
    - **Safety** (`/pii` with subcommands `add|list|remove|clear-session`)
    - **Continuity** (`/resume`, `/sync`, `/follow-ups`)
    - **Progress** (`/progress`, `/completion`)
    - **Learning** (`/corrections`, `/adaptations`)
    - **Integration** (`/mcp` with subcommands `install|list|remove|tools`, `/build-status`, `/build-stop`, `/build-logs`)
    - **File & Navigation** (`/find`, `/send`, `/cancel`) — adapter-local commands
    - **Session** (`/help`, `/exit`) — cross-surface or REPL-only
    Note: `set_config` / `get_config` are LLM-invokable capability tools, not slash commands — they belong in `docs/llm-api-guide.md`, not here. `/save` appears once under Workflow (not duplicated in Integration).
  - [ ] 6-2. Each command entry: name, aliases, surfaces where available (icons or badges), arguments, description, example, related commands
  - [ ] 6-3. Surface availability matrix at the top: which command groups work on which surfaces
  - [ ] 6-4. `docs/cli.md` stays as the binary reference (dan-serve, dan-chat, dan-run, etc.) but links to `docs/commands.md` for slash/REPL commands instead of duplicating them
  - [ ] 6-5. Generation script (optional): `scripts/generate_command_docs.py` that reads the registry and produces `docs/commands.md` so docs can never drift from the registry. Manual editing of generated sections is discouraged.

- [ ] 7. **Future command registration contract**
  - [ ] 7-1. Document the "how to add a new command" contract: create a `CommandDescriptor`, register it in the module's `__init__` or a dedicated `commands.py`, and the registry auto-discovers it at startup
  - [ ] 7-2. Add a startup health check: registry scans for commands whose `handler` dotted path can't be resolved (module import or attribute lookup failure) — log a warning
  - [ ] 7-3. Add a CI/test check: `test_command_registry.py` that asserts every registered command has a handler, valid surfaces, and non-empty help text

- [ ] 8. **Tests and docs**
  - [ ] 8-1. Unit tests: registry population, lookup by kind/surface/group, availability checks, unknown-command suggestions
  - [ ] 8-2. Integration test: add a mock command descriptor, verify it appears in `/help` output, concierge dispatch, and adapter forwarding
  - [ ] 8-3. Regression test: existing commands (`/model`, `/cost`, `/status`, `/retry`, `/memory-*`, `/mcp`) still dispatch correctly through registry path
  - [ ] 8-4. Update architecture.md, changelog

## Decisions

- **Registry is code, not config.** Commands register via Python descriptors, not JSON files. This keeps handlers and metadata co-located, enables type checking, and avoids a config-loading dependency at startup.
- **Backward compatible.** The dispatch change is internal. All existing commands work identically from the user's perspective. The only visible change is richer `/help` output.
- **Docs follow from registry.** `docs/commands.md` is either generated from or closely mirrors the registry. This is the single source of truth for command docs.
- **Adapter translation shrinks, not grows.** The `_translate_slash_command()` pattern only stays for the small set of truly `adapter_local` commands. Everything else forwards to the server where the registry handles it.

## Primary Files

- `src/dan/server/concierge/command_registry.py` — `CommandDescriptor`, `CommandRegistry` (new)
- `src/dan/server/concierge/runtime.py` — replace `_FAST_COMMAND_PREFIXES` and `_try_fast_command()` with registry dispatch
- `src/dan/cli/adapter.py` — replace `_translate_slash_command()` with registry lookup
- `src/dan/cli/chat.py` — REPL `/help` and tab-completion from registry
- `src/dan/adapters/telegram_adapter.py` — `set_my_commands()` from registry
- `docs/commands.md` — centered command reference (new)
- `docs/cli.md` — trim slash-command table, link to `docs/commands.md`
- `tests/test_concierge/test_command_registry.py` — registry tests (new)

## Dependencies

- `Concierge` runtime (29-2) — dispatch integration target
- `ChatCapabilityRegistry` (25-7) — existing capability system (commands are distinct from capabilities but share the concierge entry point)
- `cli/adapter.py` — adapter command forwarding
- Telegram adapter (30-3) — bot command menu
- Existing `/help` implementations across surfaces

## Estimate

2-3 days

## Notes

- This plan should ideally land **before** the 31-6 through 31-15 command additions. Each of those plans introduces 1-3 new slash commands. If the registry exists first, adding them is a one-liner descriptor registration instead of manual wiring in 3-4 files. If registry lands after, a migration pass will be needed.
- The registry does **not** replace the `ChatCapabilityRegistry` (25-7). Capabilities are LLM-invocable tools with schemas; commands are user-typed slash directives with direct handlers. They share the concierge entry point but have different semantics.
- The generation script (6-5) should be treated as the default path — run it in CI to verify `docs/commands.md` is up to date. Without it, the single-source-of-truth goal is undermined by the same drift the plan is trying to eliminate.
