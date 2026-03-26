"""Tests for the canonical command registry (31-16).

Covers: registry population, lookup by kind/surface/group, availability,
unknown-command suggestions, help generation, tab-completion, adapter
forwarding logic, and regression checks for core commands.
"""

from __future__ import annotations

import pytest

from dan.server.concierge.command_registry import (
    CommandDescriptor,
    CommandRegistry,
    SubcommandDescriptor,
    get_default_registry,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def empty_registry() -> CommandRegistry:
    return CommandRegistry()


@pytest.fixture
def sample_registry() -> CommandRegistry:
    """Registry with a handful of commands for focused testing."""
    reg = CommandRegistry()
    reg.register(CommandDescriptor(
        name="/model",
        kind="chat",
        surfaces=["all"],
        args_schema="[model_name]",
        help_text="Show or change model",
        group="model_config",
        handler="dan.server.concierge.runtime.Concierge._handle_model_command",
    ))
    reg.register(CommandDescriptor(
        name="/cost",
        kind="chat",
        surfaces=["all"],
        help_text="Show session cost",
        group="model_config",
        handler="dan.server.concierge.runtime.Concierge._handle_cost_command",
    ))
    reg.register(CommandDescriptor(
        name="/run",
        kind="repl",
        surfaces=["cli"],
        help_text="Run a workflow",
        group="workflow",
    ))
    reg.register(CommandDescriptor(
        name="/exit",
        kind="repl",
        surfaces=["cli"],
        help_text="Exit",
        group="session",
        aliases=["/quit"],
    ))
    reg.register(CommandDescriptor(
        name="/find",
        kind="adapter_local",
        surfaces=["whatsapp", "telegram"],
        args_schema="<query>",
        help_text="Find a file",
        group="file_navigation",
    ))
    reg.register(CommandDescriptor(
        name="/hidden-cmd",
        kind="chat",
        surfaces=["all"],
        help_text="Secret command",
        group="session",
        hidden=True,
    ))
    reg.register(CommandDescriptor(
        name="/mcp",
        kind="chat",
        surfaces=["all"],
        args_schema="<install|list|remove|tools>",
        help_text="Manage MCP servers",
        group="integration",
        handler="dan.server.concierge.runtime.Concierge._handle_mcp_command",
        subcommands={
            "install": SubcommandDescriptor(
                name="install", args_schema="<pkg>", help_text="Install MCP",
            ),
            "list": SubcommandDescriptor(name="list", help_text="List MCPs"),
        },
    ))
    return reg


# ---------------------------------------------------------------------------
# 8-1: Unit tests — registry population, lookup, availability, suggestions
# ---------------------------------------------------------------------------

class TestRegistryPopulation:
    def test_default_registry_populated(self):
        reg = get_default_registry()
        assert len(reg.list_all()) >= 15

    def test_register_and_get(self, empty_registry: CommandRegistry):
        desc = CommandDescriptor(
            name="/test", kind="chat", surfaces=["all"], help_text="test",
        )
        empty_registry.register(desc)
        assert empty_registry.get("test") is desc
        assert empty_registry.get("/test") is desc

    def test_unregister(self, empty_registry: CommandRegistry):
        desc = CommandDescriptor(
            name="/test", kind="chat", surfaces=["all"], help_text="test",
            aliases=["/t"],
        )
        empty_registry.register(desc)
        assert empty_registry.get("t") is desc
        empty_registry.unregister("/test")
        assert empty_registry.get("test") is None
        assert empty_registry.get("t") is None

    def test_alias_lookup(self, sample_registry: CommandRegistry):
        desc = sample_registry.get("quit")
        assert desc is not None
        assert desc.name == "/exit"

    def test_get_unknown(self, sample_registry: CommandRegistry):
        assert sample_registry.get("nonexistent") is None


class TestListByKind:
    def test_list_chat(self, sample_registry: CommandRegistry):
        cmds = sample_registry.list_by_kind("chat")
        names = {c.name for c in cmds}
        assert "/model" in names
        assert "/cost" in names
        assert "/run" not in names

    def test_list_repl(self, sample_registry: CommandRegistry):
        cmds = sample_registry.list_by_kind("repl")
        names = {c.name for c in cmds}
        assert "/run" in names
        assert "/exit" in names
        assert "/model" not in names

    def test_list_adapter_local(self, sample_registry: CommandRegistry):
        cmds = sample_registry.list_by_kind("adapter_local")
        assert len(cmds) == 1
        assert cmds[0].name == "/find"


class TestListBySurface:
    def test_list_cli_includes_all(self, sample_registry: CommandRegistry):
        cmds = sample_registry.list_by_surface("cli")
        names = {c.name for c in cmds}
        assert "/model" in names  # surfaces=["all"]
        assert "/run" in names    # surfaces=["cli"]
        assert "/find" not in names  # surfaces=["whatsapp", "telegram"]

    def test_list_telegram(self, sample_registry: CommandRegistry):
        cmds = sample_registry.list_by_surface("telegram")
        names = {c.name for c in cmds}
        assert "/model" in names   # surfaces=["all"]
        assert "/find" in names    # surfaces=["whatsapp", "telegram"]
        assert "/run" not in names  # surfaces=["cli"]

    def test_list_whatsapp(self, sample_registry: CommandRegistry):
        cmds = sample_registry.list_by_surface("whatsapp")
        names = {c.name for c in cmds}
        assert "/find" in names


class TestListByGroup:
    def test_list_model_config(self, sample_registry: CommandRegistry):
        cmds = sample_registry.list_by_group("model_config")
        names = {c.name for c in cmds}
        assert "/model" in names
        assert "/cost" in names
        assert len(cmds) == 2


class TestAvailability:
    def test_available_on_surface(self, sample_registry: CommandRegistry):
        assert sample_registry.is_available("/model", "cli")
        assert sample_registry.is_available("/model", "telegram")
        assert sample_registry.is_available("/find", "telegram")

    def test_not_available_on_wrong_surface(self, sample_registry: CommandRegistry):
        assert not sample_registry.is_available("/find", "cli")
        assert not sample_registry.is_available("/run", "telegram")

    def test_unknown_command_not_available(self, sample_registry: CommandRegistry):
        assert not sample_registry.is_available("/nonexistent", "cli")

    def test_requires_state(self):
        reg = CommandRegistry()
        reg.register(CommandDescriptor(
            name="/pii",
            kind="chat",
            surfaces=["all"],
            help_text="PII mgmt",
            requires=["pii_enabled"],
        ))
        assert reg.is_available("/pii", "cli", state={"pii_enabled": True})
        assert not reg.is_available("/pii", "cli", state={"pii_enabled": False})
        assert reg.is_available("/pii", "cli", state=None)
        assert reg.is_available("/pii", "cli", state={})


class TestMatch:
    def test_match_by_name(self, sample_registry: CommandRegistry):
        desc = sample_registry.match("/model gpt-4")
        assert desc is not None
        assert desc.name == "/model"

    def test_match_by_alias(self, sample_registry: CommandRegistry):
        desc = sample_registry.match("/quit")
        assert desc is not None
        assert desc.name == "/exit"

    def test_no_match(self, sample_registry: CommandRegistry):
        assert sample_registry.match("/nonexistent") is None


class TestSuggest:
    def test_suggest_close_match(self, sample_registry: CommandRegistry):
        suggestions = sample_registry.suggest("/modle")
        assert any("/model" in s for s in suggestions)

    def test_suggest_no_match(self, sample_registry: CommandRegistry):
        suggestions = sample_registry.suggest("/zzzzzzz")
        assert suggestions == []

    def test_suggest_empty_input(self, sample_registry: CommandRegistry):
        suggestions = sample_registry.suggest("")
        assert isinstance(suggestions, list)


class TestIsFastCommand:
    def test_chat_command_is_fast(self, sample_registry: CommandRegistry):
        assert sample_registry.is_fast_command("/model gpt-4")

    def test_repl_command_not_fast(self, sample_registry: CommandRegistry):
        assert not sample_registry.is_fast_command("/run my-workflow")


# ---------------------------------------------------------------------------
# Help generation
# ---------------------------------------------------------------------------

class TestFormatHelp:
    def test_help_text_groups_commands(self, sample_registry: CommandRegistry):
        text = sample_registry.format_help("cli")
        assert "/model" in text
        assert "/run" in text
        assert "/exit" in text

    def test_help_excludes_hidden(self, sample_registry: CommandRegistry):
        text = sample_registry.format_help("cli")
        assert "/hidden-cmd" not in text

    def test_help_includes_hidden_when_requested(self, sample_registry: CommandRegistry):
        text = sample_registry.format_help("cli", include_hidden=True)
        assert "/hidden-cmd" in text

    def test_help_filter_by_group(self, sample_registry: CommandRegistry):
        text = sample_registry.format_help("cli", group="model_config")
        assert "/model" in text
        assert "/run" not in text

    def test_subcommands_in_help(self, sample_registry: CommandRegistry):
        text = sample_registry.format_help("cli")
        assert "/mcp install" in text
        assert "/mcp list" in text


class TestFormatHelpPlain:
    def test_plain_no_markdown(self, sample_registry: CommandRegistry):
        text = sample_registry.format_help_plain("whatsapp")
        assert "**" not in text
        assert "`" not in text
        assert "/model" in text
        assert "/find" in text

    def test_plain_excludes_hidden(self, sample_registry: CommandRegistry):
        text = sample_registry.format_help_plain("whatsapp")
        assert "/hidden-cmd" not in text


class TestTelegramCommands:
    def test_telegram_commands_from_registry(self, sample_registry: CommandRegistry):
        cmds = sample_registry.telegram_commands()
        names = [c[0] for c in cmds]
        assert "model" in names
        assert "cost" in names

    def test_telegram_skips_hyphenated(self):
        reg = CommandRegistry()
        reg.register(CommandDescriptor(
            name="/memory-stats",
            kind="chat",
            surfaces=["all"],
            help_text="Memory stats",
        ))
        cmds = reg.telegram_commands()
        names = [c[0] for c in cmds]
        assert "memory-stats" not in names

    def test_telegram_max_15(self):
        reg = CommandRegistry()
        for i in range(20):
            reg.register(CommandDescriptor(
                name=f"/cmd{i}",
                kind="chat",
                surfaces=["all"],
                help_text=f"Command {i}",
            ))
        cmds = reg.telegram_commands()
        assert len(cmds) <= 15


# ---------------------------------------------------------------------------
# Completion candidates
# ---------------------------------------------------------------------------

class TestCompletionCandidates:
    def test_completion_includes_names_and_aliases(self, sample_registry: CommandRegistry):
        candidates = sample_registry.completion_candidates("cli")
        assert "/model" in candidates
        assert "/exit" in candidates
        assert "/quit" in candidates

    def test_completion_filters_by_surface(self, sample_registry: CommandRegistry):
        cli_cands = sample_registry.completion_candidates("cli")
        assert "/find" not in cli_cands
        tg_cands = sample_registry.completion_candidates("telegram")
        assert "/find" in tg_cands


# ---------------------------------------------------------------------------
# 8-2: Integration — mock command in help + dispatch
# ---------------------------------------------------------------------------

class TestIntegrationMockCommand:
    def test_mock_command_appears_in_help(self):
        reg = CommandRegistry()
        reg.register(CommandDescriptor(
            name="/my-custom",
            kind="chat",
            surfaces=["all"],
            help_text="My custom command",
            group="custom",
        ))
        text = reg.format_help("cli")
        assert "/my-custom" in text
        assert "My custom command" in text

    def test_mock_command_match_and_dispatch(self):
        reg = CommandRegistry()
        reg.register(CommandDescriptor(
            name="/my-custom",
            kind="chat",
            surfaces=["all"],
            help_text="My custom command",
            group="custom",
            handler="dan.server.concierge.command_registry.get_default_registry",
        ))
        desc = reg.match("/my-custom some args")
        assert desc is not None
        assert desc.name == "/my-custom"
        assert desc.kind == "chat"
        handler = reg.resolve_handler("/my-custom")
        assert handler is not None
        assert callable(handler)

    def test_mock_adapter_local_not_forwarded(self):
        reg = CommandRegistry()
        reg.register(CommandDescriptor(
            name="/local-only",
            kind="adapter_local",
            surfaces=["telegram"],
            help_text="Local command",
            group="file_navigation",
        ))
        desc = reg.match("/local-only foo")
        assert desc is not None
        assert desc.kind == "adapter_local"
        assert reg.is_fast_command("/local-only foo") is False


# ---------------------------------------------------------------------------
# 8-3: Regression — core commands dispatch correctly through registry
# ---------------------------------------------------------------------------

class TestRegressionCoreCommands:
    """Verify that existing core commands are registered, matchable,
    and have the expected metadata in the default registry."""

    @pytest.fixture(autouse=True)
    def _default_reg(self):
        self.reg = get_default_registry()

    @pytest.mark.parametrize("cmd_name", [
        "/help",
        "/autonomy",
        "/analytics",
    ])
    def test_core_command_registered(self, cmd_name: str):
        desc = self.reg.get(cmd_name)
        assert desc is not None, f"{cmd_name} not registered"
        assert desc.help_text, f"{cmd_name} has empty help_text"

    @pytest.mark.parametrize("cmd_name,expected_kind", [
        ("/run", "repl"),
        ("/show", "repl"),
        ("/exit", "repl"),
        ("/find", "adapter_local"),
        ("/send", "adapter_local"),
    ])
    def test_core_command_kind(self, cmd_name: str, expected_kind: str):
        desc = self.reg.get(cmd_name)
        assert desc is not None, f"{cmd_name} not registered"
        assert desc.kind == expected_kind

    def test_exit_alias(self):
        desc = self.reg.get("quit")
        assert desc is not None
        assert desc.name == "/exit"

    def test_help_available_everywhere(self):
        assert self.reg.is_available("/help", "cli")
        assert self.reg.is_available("/help", "telegram")
        assert self.reg.is_available("/help", "whatsapp")

    def test_repl_commands_cli_only(self):
        for name in ("/run", "/show", "/undo"):
            desc = self.reg.get(name)
            if desc is not None:
                assert "cli" in desc.surfaces or "all" in desc.surfaces
                assert not self.reg.is_available(name, "telegram")


# ---------------------------------------------------------------------------
# Adapter forwarding logic
# ---------------------------------------------------------------------------

class TestAdapterTranslation:
    def test_translate_adapter_local(self):
        from dan.cli.adapter import _translate_slash_command
        result = _translate_slash_command("/find my_file.txt")
        assert result is not None
        assert "my_file.txt" in result

    def test_translate_send(self):
        from dan.cli.adapter import _translate_slash_command
        result = _translate_slash_command("/send /path/to/file")
        assert result is not None
        assert "/path/to/file" in result

    def test_unknown_command_not_translated(self):
        from dan.cli.adapter import _translate_slash_command
        assert _translate_slash_command("/nonexistent") is None

    def test_non_slash_not_translated(self):
        from dan.cli.adapter import _translate_slash_command
        assert _translate_slash_command("hello world") is None

    def test_find_without_arg_not_translated(self):
        from dan.cli.adapter import _translate_slash_command
        assert _translate_slash_command("/find") is None

    def test_adapter_help_uses_registry(self):
        from dan.cli.adapter import _format_adapter_help
        text = _format_adapter_help("telegram")
        assert "/help" in text or "help" in text

    def test_whatsapp_help_is_plain(self):
        from dan.cli.adapter import _format_adapter_help
        text = _format_adapter_help("whatsapp")
        assert "**" not in text
        assert "`" not in text
