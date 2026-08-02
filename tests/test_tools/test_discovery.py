"""Tests for dan.tools auto-discovery and metadata validation."""

from __future__ import annotations

import asyncio
import inspect

import pytest

from dan.tools import get_all_tools

REQUIRED_METADATA_KEYS = {"tool_id", "description", "parameters", "examples", "category", "returns"}


class TestGetAllTools:
    def test_discovers_all_expected_tools(self):
        tools = get_all_tools()
        expected = {
            "file_read", "file_write", "list_directory",
            "web_fetch", "http_request",
            "shell_command",
            "text_chunk",
            "json_extract", "regex_match", "workspace_check",
        }
        # These are always available (no optional deps).
        # web_search / pdf_read may be absent if optional deps are missing.
        assert expected.issubset(set(tools.keys())), f"Missing: {expected - set(tools.keys())}"

    def test_metadata_has_required_fields(self):
        tools = get_all_tools()
        for tool_id, (_fn, meta) in tools.items():
            missing = REQUIRED_METADATA_KEYS - set(meta)
            assert not missing, f"Tool '{tool_id}' metadata missing: {missing}"

    def test_tool_id_matches_metadata(self):
        tools = get_all_tools()
        for tool_id, (_fn, meta) in tools.items():
            assert meta["tool_id"] == tool_id

    def test_functions_are_async_callable(self):
        tools = get_all_tools()
        for tool_id, (fn, _meta) in tools.items():
            assert callable(fn), f"Tool '{tool_id}' function is not callable"
            assert inspect.iscoroutinefunction(fn), f"Tool '{tool_id}' function is not async"

    def test_parameters_has_properties(self):
        tools = get_all_tools()
        for tool_id, (_fn, meta) in tools.items():
            params = meta["parameters"]
            assert "properties" in params, f"Tool '{tool_id}' parameters missing 'properties'"

    def test_examples_are_non_empty(self):
        tools = get_all_tools()
        for tool_id, (_fn, meta) in tools.items():
            assert len(meta["examples"]) >= 1, f"Tool '{tool_id}' has no examples"
