"""Tests for the builder DSL tool() method (7-3 task 8-3).

Covers:
- wf.tool() creates ToolOperator with correct tool_id and config
- config= alias works
- Wiring works (data edge from previous node via >>)
- tool_config= still works (backward compat)
"""

from __future__ import annotations

import pytest

from dan.builder import workflow


class TestToolDSL:
    def test_tool_creates_tool_operator(self):
        wf = workflow("tool_test", canonical_workers=False)
        ref = wf.tool("read_step", tool_id="file_read", config={"path": "/tmp/data.txt"})

        assert ref.node_id == "read_step"
        assert ref.node_type == "tool_operator"

        graph = wf.build()
        node = graph.node_by_id("read_step")
        assert node is not None
        assert node.node_type == "tool_operator"
        assert node.tool_id == "file_read"
        assert node.tool_config == {"path": "/tmp/data.txt"}

    def test_tool_config_param_still_works(self):
        """Backward-compatible: tool_config= parameter."""
        wf = workflow("tool_test2", canonical_workers=False)
        wf.tool("step", tool_id="shell_command", tool_config={"command": "ls"})

        graph = wf.build()
        node = graph.node_by_id("step")
        assert node.tool_id == "shell_command"
        assert node.tool_config == {"command": "ls"}

    def test_tool_config_alias_overrides(self):
        """When config= is provided but tool_config is not, config is used."""
        wf = workflow("alias_test", canonical_workers=False)
        wf.tool("s", tool_id="web_fetch", config={"url": "https://example.com"})

        graph = wf.build()
        node = graph.node_by_id("s")
        assert node.tool_config == {"url": "https://example.com"}

    def test_tool_wiring_with_rshift(self):
        """>> operator wires data edge from LLM to tool."""
        wf = workflow("wiring_test", canonical_workers=False)
        a = wf.llm("gen", prompt="Generate something")
        b = wf.tool("read", tool_id="file_read", config={"path": "/tmp/f.txt"})
        a >> b

        graph = wf.build()
        edges = [e for e in graph.edges if e.source_node_id == "gen" and e.target_node_id == "read"]
        assert len(edges) == 1

    def test_tool_empty_config(self):
        """tool() with no config produces empty dict."""
        wf = workflow("empty_config", canonical_workers=False)
        wf.tool("s", tool_id="list_directory")

        graph = wf.build()
        node = graph.node_by_id("s")
        assert node.tool_config == {}

    def test_tool_with_built_in_tool_ids(self):
        """Verify tool nodes can use built-in tool IDs from src/dan/tools/."""
        wf = workflow("builtin_tools", canonical_workers=False)
        wf.tool("t1", tool_id="file_read", config={"path": "/tmp/x.txt"})
        wf.tool("t2", tool_id="shell_command", config={"command": "echo hello"})
        wf.tool("t3", tool_id="web_fetch", config={"url": "https://example.com"})
        wf.tool("t4", tool_id="json_extract", config={"path": "$.data"})

        graph = wf.build()
        assert len(graph.nodes) == 4
        assert graph.node_by_id("t1").tool_id == "file_read"
        assert graph.node_by_id("t2").tool_id == "shell_command"
        assert graph.node_by_id("t3").tool_id == "web_fetch"
        assert graph.node_by_id("t4").tool_id == "json_extract"

    def test_tool_chain_multiple(self):
        """Chain: llm >> tool >> llm."""
        wf = workflow("chain_test", canonical_workers=False)
        a = wf.llm("gen", prompt="Generate query")
        b = wf.tool("fetch", tool_id="web_fetch", config={"url": "{query}"})
        c = wf.llm("process", prompt="Process: {result}")
        a >> b >> c

        graph = wf.build()
        assert len(graph.edges) == 2
        edge1 = [e for e in graph.edges if e.source_node_id == "gen" and e.target_node_id == "fetch"]
        edge2 = [e for e in graph.edges if e.source_node_id == "fetch" and e.target_node_id == "process"]
        assert len(edge1) == 1
        assert len(edge2) == 1
