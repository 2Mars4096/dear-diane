"""Tests for ToolRegistry.register_builtin_tools() integration."""

from __future__ import annotations

import pytest

from dan.executors.tool import ToolRegistry


class TestRegisterBuiltinTools:
    def test_registers_tools(self):
        registry = ToolRegistry()
        registered = registry.register_builtin_tools()
        assert len(registered) > 0
        for tid in registered:
            assert registry.has(tid)

    def test_returns_tool_ids(self):
        registry = ToolRegistry()
        registered = registry.register_builtin_tools()
        assert "file_read" in registered
        assert "json_extract" in registered

    def test_does_not_override_custom(self):
        registry = ToolRegistry()

        async def custom_file_read(**kwargs):
            return {"custom": True}

        registry.register("file_read", custom_file_read)
        registered = registry.register_builtin_tools()
        assert "file_read" not in registered
        fn = registry.get("file_read")
        assert fn is custom_file_read

    def test_idempotent(self):
        registry = ToolRegistry()
        first = registry.register_builtin_tools()
        second = registry.register_builtin_tools()
        assert second == []
        assert len(registry.registered_ids()) == len(first) + len(second) or len(registry.registered_ids()) >= len(first)
