"""Health-check tests for the command registry (31-16 task 7-3).

Verifies that every registered command has a handler (where expected),
valid surfaces, and non-empty help text.
"""

from __future__ import annotations

import pytest

from dan.server.concierge.command_registry import (
    CommandDescriptor,
    CommandRegistry,
    get_default_registry,
)


@pytest.fixture
def registry() -> CommandRegistry:
    return get_default_registry()


class TestRegistryHealthCheck:
    """Every registered command should pass basic metadata validation."""

    def test_all_commands_have_help_text(self, registry: CommandRegistry):
        for cmd in registry.list_all():
            assert cmd.help_text, f"{cmd.name}: missing help_text"

    def test_all_commands_have_surfaces(self, registry: CommandRegistry):
        for cmd in registry.list_all():
            assert cmd.surfaces, f"{cmd.name}: no surfaces defined"

    def test_surfaces_are_valid(self, registry: CommandRegistry):
        valid = {"cli", "editor", "telegram", "whatsapp", "whatsapp-web", "email", "all"}
        for cmd in registry.list_all():
            for s in cmd.surfaces:
                assert s in valid, f"{cmd.name}: unknown surface '{s}'"

    def test_chat_commands_have_handlers(self, registry: CommandRegistry):
        no_handler_ok = {"/help", "/build"}
        for cmd in registry.list_by_kind("chat"):
            if cmd.name in no_handler_ok:
                continue
            assert cmd.handler, f"{cmd.name}: chat command with no handler"

    def test_handler_modules_importable(self, registry: CommandRegistry):
        """The module portion of every handler dotted path should be importable."""
        warnings = registry.health_check()
        module_warnings = [w for w in warnings if "not importable" in w]
        assert not module_warnings, (
            f"Unresolvable handler modules:\n" +
            "\n".join(f"  {w}" for w in module_warnings)
        )

    def test_health_check_returns_no_critical_warnings(self, registry: CommandRegistry):
        warnings = registry.health_check()
        critical = [
            w for w in warnings
            if "empty help_text" in w or "no surfaces" in w
        ]
        assert not critical, (
            f"Critical metadata issues:\n" +
            "\n".join(f"  {w}" for w in critical)
        )

    def test_no_duplicate_names(self, registry: CommandRegistry):
        names = [cmd.name for cmd in registry.list_all()]
        assert len(names) == len(set(names)), "Duplicate command names found"

    def test_no_duplicate_aliases(self, registry: CommandRegistry):
        seen: dict[str, str] = {}
        for cmd in registry.list_all():
            for alias in cmd.aliases:
                if alias in seen:
                    pytest.fail(
                        f"Alias '{alias}' used by both {seen[alias]} and {cmd.name}"
                    )
                seen[alias] = cmd.name


class TestHealthCheckMethod:
    def test_detects_empty_help(self):
        reg = CommandRegistry()
        reg.register(CommandDescriptor(
            name="/bad", kind="chat", surfaces=["all"], help_text="",
        ))
        warnings = reg.health_check()
        assert any("empty help_text" in w for w in warnings)

    def test_detects_no_surfaces(self):
        reg = CommandRegistry()
        reg.register(CommandDescriptor(
            name="/bad", kind="chat", surfaces=[], help_text="test",
        ))
        warnings = reg.health_check()
        assert any("no surfaces" in w for w in warnings)

    def test_detects_invalid_surface(self):
        reg = CommandRegistry()
        reg.register(CommandDescriptor(
            name="/bad", kind="chat", surfaces=["invalid_surface"], help_text="test",
        ))
        warnings = reg.health_check()
        assert any("unknown surface" in w for w in warnings)

    def test_detects_bad_handler_module(self):
        reg = CommandRegistry()
        reg.register(CommandDescriptor(
            name="/bad",
            kind="chat",
            surfaces=["all"],
            help_text="test",
            handler="nonexistent.module.func",
        ))
        warnings = reg.health_check()
        assert any("not importable" in w for w in warnings)

    def test_good_command_no_warnings(self):
        reg = CommandRegistry()
        reg.register(CommandDescriptor(
            name="/good",
            kind="chat",
            surfaces=["all"],
            help_text="A good command",
            handler="dan.server.concierge.command_registry.get_default_registry",
        ))
        warnings = reg.health_check()
        assert warnings == []
