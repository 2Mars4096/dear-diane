"""Canonical command registry — single source of truth for all DAN commands.

Every slash command, REPL command, CLI binary, and adapter-local command is
registered here as a ``CommandDescriptor``.  The registry drives dispatch,
``/help`` text, Telegram ``set_my_commands()``, adapter forwarding decisions,
tab-completion, and generated docs.

How to add a new command
------------------------
1. Create a ``CommandDescriptor`` with ``name``, ``kind``, ``surfaces``,
   ``help_text``, ``handler`` (dotted path to the callable), and ``group``.
2. Call ``registry.register(descriptor)`` inside ``_populate_default_commands``
   in this module (or at module load time for plugin commands).
3. The handler function receives ``(concierge, message, args_str)`` for chat
   commands, or is a standalone coroutine for adapter-local commands.
4. That's it — the registry drives dispatch, ``/help``, Telegram menus,
   tab-completion, and ``docs/commands.md`` generation automatically.

Example::

    registry.register(CommandDescriptor(
        name="/my-cmd",
        kind="chat",
        surfaces=["all"],
        args_schema="<arg>",
        help_text="Does something useful",
        group="integration",
        handler="dan.server.concierge.my_module.handle_my_cmd",
    ))
"""

from __future__ import annotations

import importlib
import logging
import re
from dataclasses import field
from difflib import get_close_matches
from typing import Any, Callable, Coroutine, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
_TELEGRAM_COMMAND_RE = re.compile(r"^[a-z0-9_]{1,32}$")

CommandKind = Literal["binary", "repl", "chat", "adapter_local"]
SurfaceName = Literal[
    "cli", "editor", "telegram", "whatsapp", "whatsapp-web", "email", "all",
]


class SubcommandDescriptor(BaseModel):
    """Metadata for a subcommand (e.g. ``/schedule add``)."""

    name: str
    args_schema: str | None = None
    help_text: str = ""
    handler: str | None = None


class CommandDescriptor(BaseModel):
    """Metadata for a single command across all surfaces."""

    name: str
    aliases: list[str] = Field(default_factory=list)
    kind: CommandKind
    surfaces: list[str] = Field(default_factory=lambda: ["all"])
    args_schema: str | None = None
    help_text: str = ""
    examples: list[str] = Field(default_factory=list)
    handler: str | None = None
    requires: list[str] = Field(default_factory=list)
    group: str = "general"
    subcommands: dict[str, SubcommandDescriptor] = Field(default_factory=dict)
    hidden: bool = False
    is_async: bool = True

    def matches(self, text: str) -> bool:
        """Return *True* if *text* starts with this command name or an alias."""
        lower = text.strip().lower()
        candidates = [self.name] + self.aliases
        return any(
            lower == c or lower.startswith(c + " ") or lower.startswith(c + "\t")
            for c in candidates
        )


class CommandRegistry:
    """Singleton registry of all known commands."""

    def __init__(self) -> None:
        self._commands: dict[str, CommandDescriptor] = {}
        self._alias_map: dict[str, str] = {}

    # -- mutation ----------------------------------------------------------

    def register(self, descriptor: CommandDescriptor) -> None:
        self._commands[descriptor.name] = descriptor
        for alias in descriptor.aliases:
            self._alias_map[alias] = descriptor.name

    def unregister(self, name: str) -> None:
        desc = self._commands.pop(name, None)
        if desc:
            for alias in desc.aliases:
                self._alias_map.pop(alias, None)

    # -- lookup ------------------------------------------------------------

    def get(self, name: str) -> CommandDescriptor | None:
        name = name.lower().lstrip("/")
        prefixed = f"/{name}"
        if prefixed in self._commands:
            return self._commands[prefixed]
        if name in self._commands:
            return self._commands[name]
        canonical = self._alias_map.get(prefixed) or self._alias_map.get(name)
        if canonical:
            return self._commands.get(canonical)
        return None

    def match(self, text: str) -> CommandDescriptor | None:
        """Find the command descriptor that matches *text* (prefix match)."""
        lower = text.strip().lower()
        for desc in self._commands.values():
            if desc.matches(lower):
                return desc
        return None

    def list_all(self) -> list[CommandDescriptor]:
        return list(self._commands.values())

    def list_by_kind(self, kind: CommandKind) -> list[CommandDescriptor]:
        return [d for d in self._commands.values() if d.kind == kind]

    def list_by_surface(self, surface: str) -> list[CommandDescriptor]:
        return [
            d for d in self._commands.values()
            if "all" in d.surfaces or surface in d.surfaces
        ]

    def list_by_group(self, group: str) -> list[CommandDescriptor]:
        return [d for d in self._commands.values() if d.group == group]

    def is_available(self, name: str, surface: str, state: dict[str, Any] | None = None) -> bool:
        desc = self.get(name)
        if desc is None:
            return False
        if "all" not in desc.surfaces and surface not in desc.surfaces:
            return False
        if state and desc.requires:
            for req in desc.requires:
                if not state.get(req):
                    return False
        return True

    def suggest(self, text: str, max_results: int = 3) -> list[str]:
        """Suggest closest command names for unknown input."""
        name = text.strip().lower().split()[0] if text.strip() else ""
        all_names = list(self._commands.keys()) + list(self._alias_map.keys())
        return get_close_matches(name, all_names, n=max_results, cutoff=0.5)

    def is_fast_command(self, text: str) -> bool:
        """Check if *text* is a registered chat command (fast-path eligible)."""
        desc = self.match(text)
        return desc is not None and desc.kind == "chat"

    def resolve_handler(self, name: str) -> Callable[..., Any] | None:
        """Resolve a command handler from its dotted path string."""
        desc = self.get(name)
        if desc is None or desc.handler is None:
            return None
        try:
            parts = desc.handler.split(".")
            resolved_obj: Any = None
            attr_parts: list[str] = []
            for idx in range(len(parts), 0, -1):
                module_name = ".".join(parts[:idx])
                try:
                    resolved_obj = importlib.import_module(module_name)
                    attr_parts = parts[idx:]
                    break
                except ImportError:
                    continue
            if resolved_obj is None:
                return None
            for attr in attr_parts:
                resolved_obj = getattr(resolved_obj, attr, None)
                if resolved_obj is None:
                    return None
            if callable(resolved_obj):
                return resolved_obj
        except Exception:
            logger.warning("Failed to resolve handler for %s: %s", name, desc.handler)
        return None

    # -- help generation ---------------------------------------------------

    def format_help(
        self,
        surface: str = "all",
        group: str | None = None,
        include_hidden: bool = False,
    ) -> str:
        """Generate help text for a given surface and optional group filter."""
        commands = self.list_by_surface(surface)
        if group:
            commands = [c for c in commands if c.group == group]
        if not include_hidden:
            commands = [c for c in commands if not c.hidden]
        commands.sort(key=lambda c: (c.group, c.name))

        if not commands:
            return "No commands available."

        lines: list[str] = []
        current_group = ""
        for cmd in commands:
            if cmd.group != current_group:
                current_group = cmd.group
                lines.append(f"\n**{current_group.replace('_', ' ').title()}**")
            alias_str = f" ({', '.join(cmd.aliases)})" if cmd.aliases else ""
            args_str = f" {cmd.args_schema}" if cmd.args_schema else ""
            lines.append(f"  `{cmd.name}{args_str}`{alias_str} — {cmd.help_text}")
            if cmd.subcommands:
                for sub in cmd.subcommands.values():
                    sub_args = f" {sub.args_schema}" if sub.args_schema else ""
                    lines.append(f"    `{cmd.name} {sub.name}{sub_args}` — {sub.help_text}")

        return "\n".join(lines)

    def completion_candidates(self, surface: str = "all") -> list[str]:
        """Return command names and aliases for tab-completion on *surface*."""
        commands = self.list_by_surface(surface)
        names: list[str] = []
        for cmd in commands:
            names.append(cmd.name)
            names.extend(cmd.aliases)
        return sorted(set(names))

    def format_help_plain(
        self,
        surface: str = "all",
        group: str | None = None,
        include_hidden: bool = False,
    ) -> str:
        """Plain-text help suitable for WhatsApp and other non-markdown surfaces."""
        commands = self.list_by_surface(surface)
        if group:
            commands = [c for c in commands if c.group == group]
        if not include_hidden:
            commands = [c for c in commands if not c.hidden]
        commands.sort(key=lambda c: (c.group, c.name))

        if not commands:
            return "No commands available."

        lines: list[str] = []
        current_group = ""
        for cmd in commands:
            if cmd.group != current_group:
                current_group = cmd.group
                lines.append(f"\n{current_group.replace('_', ' ').upper()}")
            args_str = f" {cmd.args_schema}" if cmd.args_schema else ""
            lines.append(f"  {cmd.name}{args_str} - {cmd.help_text}")

        return "\n".join(lines)

    def health_check(self) -> list[str]:
        """Validate registry metadata and handler module importability.

        Returns a list of warning strings.  Called at server startup to detect
        stale or broken handler paths early.
        """
        warnings: list[str] = []
        _module_cache: dict[str, bool] = {}

        for cmd in self._commands.values():
            if not cmd.help_text:
                warnings.append(f"{cmd.name}: empty help_text")
            if not cmd.surfaces:
                warnings.append(f"{cmd.name}: no surfaces defined")
            valid_surfaces = {"cli", "editor", "telegram", "whatsapp", "whatsapp-web", "email", "all"}
            for s in cmd.surfaces:
                if s not in valid_surfaces:
                    warnings.append(f"{cmd.name}: unknown surface '{s}'")
            if cmd.handler:
                parts = cmd.handler.split(".")
                module_found = False
                for idx in range(len(parts), 0, -1):
                    mod_path = ".".join(parts[:idx])
                    if mod_path in _module_cache:
                        if _module_cache[mod_path]:
                            module_found = True
                            break
                        continue
                    try:
                        importlib.import_module(mod_path)
                        _module_cache[mod_path] = True
                        module_found = True
                        break
                    except ImportError:
                        _module_cache[mod_path] = False
                if not module_found:
                    warnings.append(
                        f"{cmd.name}: handler module not importable: {cmd.handler}"
                    )

        return warnings

    def telegram_commands(self, scope: str = "dm") -> list[tuple[str, str]]:
        """Return (command, description) tuples for Telegram set_my_commands."""
        commands = [
            c for c in self._commands.values()
            if not c.hidden
            and c.kind in ("chat", "repl")
            and ("all" in c.surfaces or "telegram" in c.surfaces)
        ]
        commands.sort(key=lambda c: c.name)
        result = []
        for cmd in commands:
            name = cmd.name.lstrip("/")
            if not _TELEGRAM_COMMAND_RE.fullmatch(name):
                logger.debug("Skipping non-Telegram command name: %s", cmd.name)
                continue
            result.append((name, cmd.help_text[:256] or name))
            if len(result) >= 15:
                break
        return result


# ---------------------------------------------------------------------------
# Default registry with all built-in commands
# ---------------------------------------------------------------------------

_default_registry: CommandRegistry | None = None


def get_default_registry() -> CommandRegistry:
    """Return (and lazily create) the singleton default command registry."""
    global _default_registry
    if _default_registry is None:
        _default_registry = CommandRegistry()
        _populate_default_commands(_default_registry)
    return _default_registry


def _populate_default_commands(registry: CommandRegistry) -> None:
    """Register all built-in commands."""

    # -- Workflow commands -------------------------------------------------
    registry.register(CommandDescriptor(
        name="/run",
        kind="repl",
        surfaces=["cli"],
        args_schema="[workflow_name] [--input key=value]",
        help_text="Run a workflow",
        group="workflow",
        handler="dan.cli.chat.handle_run_command",
    ))
    registry.register(CommandDescriptor(
        name="/run-node",
        kind="repl",
        surfaces=["cli"],
        args_schema="<node_id>",
        help_text="Run a single node from the current workflow",
        group="workflow",
    ))
    registry.register(CommandDescriptor(
        name="/show",
        kind="repl",
        surfaces=["cli"],
        args_schema="[--code|--json|--stats]",
        help_text="Show current workflow (ASCII DAG, code, JSON, or stats)",
        group="workflow",
    ))
    registry.register(CommandDescriptor(
        name="/save",
        kind="chat",
        surfaces=["all"],
        args_schema="[name]",
        help_text="Save the current workflow",
        group="workflow",
        handler="dan.server.concierge.runtime.Concierge._handle_save_command",
    ))
    registry.register(CommandDescriptor(
        name="/saveas",
        kind="repl",
        surfaces=["cli"],
        args_schema="<name>",
        help_text="Save workflow under a new name",
        group="workflow",
    ))
    registry.register(CommandDescriptor(
        name="/list",
        kind="repl",
        surfaces=["cli"],
        help_text="List saved workflows",
        group="workflow",
    ))
    registry.register(CommandDescriptor(
        name="/open",
        kind="repl",
        surfaces=["cli"],
        args_schema="<name>",
        help_text="Open a saved workflow",
        group="workflow",
    ))
    registry.register(CommandDescriptor(
        name="/new",
        kind="repl",
        surfaces=["cli"],
        help_text="Start a new empty workflow",
        group="workflow",
    ))
    registry.register(CommandDescriptor(
        name="/rename",
        kind="repl",
        surfaces=["cli"],
        args_schema="<new_name>",
        help_text="Rename current workflow",
        group="workflow",
    ))
    registry.register(CommandDescriptor(
        name="/undo",
        kind="repl",
        surfaces=["cli"],
        help_text="Undo last graph mutation",
        group="workflow",
    ))

    # -- Build commands ----------------------------------------------------
    # 32-7 §4-4: /build forces workflow-build mode (reusable artifact)
    registry.register(CommandDescriptor(
        name="/build",
        aliases=["/workflow"],
        kind="chat",
        surfaces=["all"],
        args_schema="<goal>",
        help_text="Force workflow-build mode for the current task (creates a reusable artifact)",
        examples=["/build Research X and summarize Y", "/build Create a data pipeline"],
        group="workflow",
    ))
    registry.register(CommandDescriptor(
        name="/build-status",
        kind="chat",
        surfaces=["all"],
        help_text="Show build session status",
        group="workflow",
        handler="dan.server.concierge.runtime.Concierge._handle_build_command",
    ))
    registry.register(CommandDescriptor(
        name="/build-stop",
        kind="chat",
        surfaces=["all"],
        help_text="Stop active build session",
        group="workflow",
        handler="dan.server.concierge.runtime.Concierge._handle_build_command",
    ))
    registry.register(CommandDescriptor(
        name="/build-logs",
        kind="chat",
        surfaces=["all"],
        help_text="Show build session logs",
        group="workflow",
        handler="dan.server.concierge.runtime.Concierge._handle_build_command",
    ))

    # -- Model & Config commands -------------------------------------------
    registry.register(CommandDescriptor(
        name="/model",
        kind="chat",
        surfaces=["all"],
        args_schema="[model_name]",
        help_text="Show or change the current LLM model",
        group="model_config",
        handler="dan.server.concierge.runtime.Concierge._handle_model_command",
    ))
    registry.register(CommandDescriptor(
        name="/cost",
        kind="chat",
        surfaces=["all"],
        help_text="Show session cost summary",
        group="model_config",
        handler="dan.server.concierge.runtime.Concierge._handle_cost_command",
    ))
    registry.register(CommandDescriptor(
        name="/analytics",
        kind="chat",
        surfaces=["all"],
        args_schema="[project <name>] [--since Nd] [--by model|surface|day|intent] | export [--format jsonl|csv]",
        subcommands={
            "project": SubcommandDescriptor(name="project", args_schema="<name> [--since Nd]", help_text="Project-specific usage report"),
            "export": SubcommandDescriptor(name="export", args_schema="[--since Nd] [--format jsonl|csv]", help_text="Export raw telemetry events"),
        },
        help_text="Show usage analytics (tokens, cost, timing)",
        group="model_config",
        handler="dan.server.concierge.runtime.Concierge._handle_analytics_command",
    ))
    registry.register(CommandDescriptor(
        name="/status",
        kind="chat",
        surfaces=["all"],
        help_text="Show system status and active runs",
        group="model_config",
        handler="dan.server.concierge.runtime.Concierge._handle_status_command",
    ))
    registry.register(CommandDescriptor(
        name="/retry",
        kind="chat",
        surfaces=["all"],
        help_text="Retry the last failed request",
        group="model_config",
        handler="dan.server.concierge.runtime.Concierge._handle_retry_command",
    ))

    # -- Project commands --------------------------------------------------
    registry.register(CommandDescriptor(
        name="/project",
        kind="chat",
        surfaces=["all"],
        args_schema="[list|info [name]|set <key> <value>|memory [name]|delete <name>]",
        subcommands={
            "list": SubcommandDescriptor(name="list", help_text="List all projects on this surface"),
            "info": SubcommandDescriptor(name="info", args_schema="[name]", help_text="Show project details"),
            "set": SubcommandDescriptor(name="set", args_schema="<key> <value>", help_text="Store a project-specific fact"),
            "memory": SubcommandDescriptor(name="memory", args_schema="[name]", help_text="Browse project memories"),
            "delete": SubcommandDescriptor(name="delete", args_schema="<name>", help_text="Mark project completed"),
        },
        help_text="Manage projects: list all, inspect details, store project-specific facts, browse project memories",
        group="project",
        handler="dan.server.concierge.runtime.Concierge._handle_project_command",
    ))

    # -- Memory commands ---------------------------------------------------
    registry.register(CommandDescriptor(
        name="/memory-stats",
        kind="chat",
        surfaces=["all"],
        help_text="Show memory usage statistics",
        group="memory",
        handler="dan.server.concierge.runtime.Concierge._handle_memory_command",
    ))
    registry.register(CommandDescriptor(
        name="/memory-search",
        kind="chat",
        surfaces=["all"],
        args_schema="<query>",
        help_text="Search stored memories",
        group="memory",
        handler="dan.server.concierge.runtime.Concierge._handle_memory_command",
    ))
    registry.register(CommandDescriptor(
        name="/memory-delete",
        kind="chat",
        surfaces=["all"],
        args_schema="<memory_id>",
        help_text="Delete a specific memory item",
        group="memory",
        handler="dan.server.concierge.runtime.Concierge._handle_memory_command",
    ))
    registry.register(CommandDescriptor(
        name="/memory-forget",
        kind="chat",
        surfaces=["all"],
        args_schema="<keyword>",
        help_text="Forget memories matching a keyword",
        group="memory",
        handler="dan.server.concierge.runtime.Concierge._handle_memory_command",
    ))
    registry.register(CommandDescriptor(
        name="/memory-confirm",
        kind="chat",
        surfaces=["all"],
        help_text="Confirm pending preference suggestions",
        group="memory",
        hidden=True,
        handler="dan.server.concierge.runtime.Concierge._handle_memory_command",
    ))
    registry.register(CommandDescriptor(
        name="/memory-reject",
        kind="chat",
        surfaces=["all"],
        help_text="Reject pending preference suggestions",
        group="memory",
        hidden=True,
        handler="dan.server.concierge.runtime.Concierge._handle_memory_command",
    ))

    # -- Integration commands ----------------------------------------------
    registry.register(CommandDescriptor(
        name="/mcp",
        kind="chat",
        surfaces=["all"],
        args_schema="<install|list|remove|tools> [args]",
        help_text="Manage MCP tool servers",
        group="integration",
        handler="dan.server.concierge.runtime.Concierge._handle_mcp_command",
        subcommands={
            "install": SubcommandDescriptor(
                name="install",
                args_schema="<package_or_name>",
                help_text="Install an MCP server package",
            ),
            "list": SubcommandDescriptor(
                name="list",
                help_text="List connected MCP servers",
            ),
            "remove": SubcommandDescriptor(
                name="remove",
                args_schema="<server_name>",
                help_text="Remove an MCP server",
            ),
            "tools": SubcommandDescriptor(
                name="tools",
                help_text="List tools from all MCP servers",
            ),
        },
    ))

    # -- Skill commands ----------------------------------------------------
    registry.register(CommandDescriptor(
        name="/skill",
        kind="chat",
        surfaces=["all"],
        args_schema="<list|info|import|scan> [args]",
        help_text="Manage skills (IDE-compatible SKILL.md format)",
        group="integration",
        handler="dan.server.skill_store.handle_skill_command",
        subcommands={
            "list": SubcommandDescriptor(
                name="list",
                args_schema="[user|project]",
                help_text="List loaded skills, optionally filter by scope",
            ),
            "info": SubcommandDescriptor(
                name="info",
                args_schema="<name>",
                help_text="Show skill details and content preview",
            ),
            "import": SubcommandDescriptor(
                name="import",
                args_schema="<path>",
                help_text="Import skill from Cursor/Claude/Codex or any path",
            ),
            "scan": SubcommandDescriptor(
                name="scan",
                help_text="Rescan all skill directories",
            ),
        },
    ))

    # -- Adapter-local commands --------------------------------------------
    registry.register(CommandDescriptor(
        name="/find",
        kind="adapter_local",
        surfaces=["whatsapp", "whatsapp-web", "telegram", "email"],
        args_schema="<query>",
        help_text="Find a file on your computer",
        group="file_navigation",
    ))
    registry.register(CommandDescriptor(
        name="/send",
        kind="adapter_local",
        surfaces=["whatsapp", "whatsapp-web", "telegram", "email"],
        args_schema="<path>",
        help_text="Send you a file",
        group="file_navigation",
    ))
    registry.register(CommandDescriptor(
        name="/cancel",
        kind="chat",
        surfaces=["all"],
        args_schema="[run_id|latest|last_failed|paused]",
        help_text="Cancel the current run or task",
        group="session",
        handler="dan.server.concierge.runtime.Concierge._handle_cancel_command",
    ))

    # -- Session commands --------------------------------------------------
    registry.register(CommandDescriptor(
        name="/help",
        kind="chat",
        surfaces=["all"],
        args_schema="[group]",
        help_text="Show available commands",
        group="session",
    ))
    registry.register(CommandDescriptor(
        name="/exit",
        kind="repl",
        surfaces=["cli"],
        help_text="Exit the chat session",
        group="session",
        aliases=["/quit"],
    ))

    # ======================================================================
    # Placeholder registrations for planned commands (31-6 through 31-17).
    # Handlers will be wired when each subplan is implemented.
    # ======================================================================

    # 31-6: Goal-Oriented Loop
    registry.register(CommandDescriptor(
        name="/goal",
        kind="chat",
        surfaces=["all"],
        args_schema='"<metric> <op> <target>" [--timeout <duration>] [--eval <mode>]',
        help_text="Start a goal-oriented loop: iterate until metric met or deadline expires",
        group="scheduling",
        handler="dan.server.concierge.goal_loop.handle_goal_command",
    ))
    registry.register(CommandDescriptor(
        name="/goal-status",
        kind="chat",
        surfaces=["all"],
        help_text="Show goal loop progress",
        group="scheduling",
        handler="dan.server.concierge.goal_loop.handle_goal_status_command",
    ))
    registry.register(CommandDescriptor(
        name="/goal-stop",
        kind="chat",
        surfaces=["all"],
        help_text="Stop active goal loop and return best result",
        group="scheduling",
        handler="dan.server.concierge.goal_loop.handle_goal_stop_command",
    ))

    # 31-7: Scheduled Tasks
    registry.register(CommandDescriptor(
        name="/schedule",
        kind="chat",
        surfaces=["all"],
        args_schema="<add|list|remove|pause|resume|history> [args]",
        help_text="Manage scheduled tasks",
        group="scheduling",
        handler="dan.server.concierge.scheduler.handle_schedule_command",
        subcommands={
            "add": SubcommandDescriptor(
                name="add",
                args_schema='"<action>" <cron_or_interval>',
                help_text="Add a new scheduled task",
            ),
            "list": SubcommandDescriptor(name="list", help_text="List all schedules"),
            "remove": SubcommandDescriptor(
                name="remove", args_schema="<id|name>",
                help_text="Remove a schedule",
            ),
            "pause": SubcommandDescriptor(
                name="pause", args_schema="<id|name>",
                help_text="Pause a schedule",
            ),
            "resume": SubcommandDescriptor(
                name="resume", args_schema="<id|name>",
                help_text="Resume a paused schedule",
            ),
            "history": SubcommandDescriptor(
                name="history", args_schema="<id|name>",
                help_text="Show run history for a schedule",
            ),
        },
    ))

    # 31-8: Plan Dependency Optimization
    registry.register(CommandDescriptor(
        name="/plan",
        kind="chat",
        surfaces=["all"],
        args_schema="[--replan]",
        help_text="Show current plan schedule; --replan forces re-decomposition",
        group="scheduling",
        handler="dan.engine.plan_scheduler.handle_plan_command",
    ))

    # 31-9: Completion Guard
    registry.register(CommandDescriptor(
        name="/completion",
        kind="chat",
        surfaces=["all"],
        help_text="Show completion check statistics",
        group="safety",
        handler="dan.server.concierge.completion_guard.handle_completion_command",
    ))

    # 31-10: PII Tokenization
    registry.register(CommandDescriptor(
        name="/pii",
        kind="chat",
        surfaces=["all"],
        args_schema="<add|list|remove|clear-session> [args]",
        help_text="Manage PII protection settings",
        group="safety",
        requires=["pii_enabled"],
        handler="dan.server.concierge.pii_tokenizer.handle_pii_command",
        subcommands={
            "add": SubcommandDescriptor(
                name="add",
                args_schema='"<value>" --category <category>',
                help_text="Add a sensitive word",
            ),
            "list": SubcommandDescriptor(name="list", help_text="List protected words"),
            "remove": SubcommandDescriptor(
                name="remove", args_schema='"<value>"',
                help_text="Remove a sensitive word",
            ),
            "clear-session": SubcommandDescriptor(
                name="clear-session",
                help_text="Clear current session mappings",
            ),
        },
    ))

    # 31-11: Cross-Session Resume
    registry.register(CommandDescriptor(
        name="/resume",
        kind="chat",
        surfaces=["all"],
        args_schema="[task_name]",
        help_text="Resume a paused or previous task",
        group="continuity",
        handler="dan.server.concierge.resume.handle_resume_command",
    ))

    # 31-12: Proactive Follow-Up
    registry.register(CommandDescriptor(
        name="/follow-ups",
        kind="chat",
        surfaces=["all"],
        args_schema="[on|off]",
        help_text="List or toggle proactive follow-ups",
        group="continuity",
        handler="dan.server.concierge.follow_up.handle_follow_ups_command",
        subcommands={
            "on": SubcommandDescriptor(name="on", help_text="Enable follow-ups"),
            "off": SubcommandDescriptor(name="off", help_text="Disable follow-ups"),
        },
    ))

    # 31-13: Multi-Surface Continuity
    registry.register(CommandDescriptor(
        name="/sync",
        kind="chat",
        surfaces=["all"],
        args_schema="[--allow-group]",
        help_text="Pull latest context from all surfaces for current project",
        group="continuity",
        handler="dan.server.concierge.continuity.handle_sync_command",
    ))

    # 31-14: Progressive Response UX
    registry.register(CommandDescriptor(
        name="/progress",
        kind="chat",
        surfaces=["all"],
        args_schema="[full|compact|minimal]",
        help_text="Set progress verbosity level",
        group="progress",
        handler="dan.server.concierge.progress_ux.handle_progress_command",
    ))

    # 31-15: Learning & Evolution Optimization
    registry.register(CommandDescriptor(
        name="/corrections",
        kind="chat",
        surfaces=["all"],
        help_text="List recent correction-driven learning events",
        group="learning",
        handler="dan.server.concierge.learning.handle_corrections_command",
    ))
    registry.register(CommandDescriptor(
        name="/adaptations",
        kind="chat",
        surfaces=["all"],
        help_text="List pending adaptations with confidence and approval status",
        group="learning",
        handler="dan.server.concierge.learning.handle_adaptations_command",
    ))

    # -- Behavior commands (31-22) -----------------------------------------
    registry.register(CommandDescriptor(
        name="/changes",
        kind="chat",
        surfaces=["all"],
        help_text="List recent behavioral adaptations",
        group="behavior",
        handler="dan.server.concierge.learning.handle_changes_command",
    ))
    registry.register(CommandDescriptor(
        name="/revert",
        kind="chat",
        surfaces=["all"],
        args_schema="<id>",
        help_text="Roll back a specific behavior change",
        group="behavior",
        handler="dan.server.concierge.learning.handle_revert_command",
    ))
    registry.register(CommandDescriptor(
        name="/behavior",
        kind="chat",
        surfaces=["all"],
        help_text="Inspect current behavior state",
        group="behavior",
        subcommands={
            "--key": SubcommandDescriptor(
                name="--key",
                args_schema="<key>",
                help_text="Show value, version, and history for a specific key",
            ),
            "--seeds": SubcommandDescriptor(
                name="--seeds",
                help_text="Compare current values to seed defaults",
            ),
            "--reset": SubcommandDescriptor(
                name="--reset",
                args_schema="<key>",
                help_text="Restore a key to its seed default",
            ),
        },
        handler="dan.server.concierge.learning.handle_behavior_command",
    ))

    # 31-17: Computer Control & Browser Automation
    registry.register(CommandDescriptor(
        name="/computer",
        kind="chat",
        surfaces=["all"],
        args_schema="<status|doctor|approve> [args]",
        help_text="Computer control status, diagnostics, and approvals",
        group="computer_use",
        handler="dan.server.concierge.computer_use.handle_computer_command",
        subcommands={
            "status": SubcommandDescriptor(
                name="status",
                help_text="Show computer control status and permissions",
            ),
            "doctor": SubcommandDescriptor(
                name="doctor",
                help_text="Check OS permissions and capabilities",
            ),
            "approve": SubcommandDescriptor(
                name="approve",
                args_schema="<request-id>",
                help_text="Approve a pending computer-use action",
            ),
        },
    ))
