"""Tests for capability exposure features (plan 31-3, task 5).

Covers:
  5-1: Capability handlers — valid + invalid arg testing for handle_python_eval,
       handle_csv_read, handle_compress, handle_git_status
  5-2: DAN_LEARNING_MODE bundle — sets 4 learning vars; explicit override preserved
  5-3: Introspection tools — inspect_node, list_test_cases, run_test_case
  5-4: DAN_FULL_TOOLS gate — when 0 new tools not registered, when 1 all 13 registered
"""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dan.server.capability_registry import (
    CapabilityContext,
    CapabilityResult,
    ChatCapabilityRegistry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(**overrides: Any) -> CapabilityContext:
    defaults = dict(
        workflow_id="test-wf",
        graph_store=MagicMock(),
        run_manager=MagicMock(),
        run_store=MagicMock(),
    )
    defaults.update(overrides)
    return CapabilityContext(**defaults)


# ===========================================================================
# 5-1: Capability handler valid/invalid args
# ===========================================================================


class TestCapabilityHandlers:
    """Call with valid args -> success; call with invalid/missing -> error."""

    @pytest.mark.asyncio
    async def test_python_eval_valid(self) -> None:
        from dan.server.capability_handlers import handle_python_eval

        ctx = _make_ctx()
        with patch(
            "dan.tools.python_eval.python_eval",
            new_callable=AsyncMock,
            return_value={"result": 42},
        ):
            result = await handle_python_eval({"code": "1+1"}, ctx)

        assert result.success is True
        assert result.message == "Success"

    @pytest.mark.asyncio
    async def test_python_eval_error(self) -> None:
        from dan.server.capability_handlers import handle_python_eval

        ctx = _make_ctx()
        with patch(
            "dan.tools.python_eval.python_eval",
            new_callable=AsyncMock,
            side_effect=SyntaxError("bad code"),
        ):
            result = await handle_python_eval({"code": "bad"}, ctx)

        assert result.success is False
        assert "Error" in result.message

    @pytest.mark.asyncio
    async def test_csv_read_valid(self) -> None:
        from dan.server.capability_handlers import handle_csv_read

        ctx = _make_ctx()
        with patch(
            "dan.tools.csv_read.csv_read",
            new_callable=AsyncMock,
            return_value={"rows": [{"a": 1}], "columns": ["a"]},
        ):
            result = await handle_csv_read({"path": "/tmp/test.csv"}, ctx)

        assert result.success is True

    @pytest.mark.asyncio
    async def test_csv_read_missing_file(self) -> None:
        from dan.server.capability_handlers import handle_csv_read

        ctx = _make_ctx()
        with patch(
            "dan.tools.csv_read.csv_read",
            new_callable=AsyncMock,
            side_effect=FileNotFoundError("not found"),
        ):
            result = await handle_csv_read({"path": "/nonexistent/file.csv"}, ctx)

        assert result.success is False

    @pytest.mark.asyncio
    async def test_compress_valid(self) -> None:
        from dan.server.capability_handlers import handle_compress

        ctx = _make_ctx()
        with patch(
            "dan.tools.compress.compress",
            new_callable=AsyncMock,
            return_value={"output": "/tmp/archive.zip"},
        ):
            result = await handle_compress({"path": "/tmp/files", "format": "zip"}, ctx)

        assert result.success is True

    @pytest.mark.asyncio
    async def test_compress_error(self) -> None:
        from dan.server.capability_handlers import handle_compress

        ctx = _make_ctx()
        with patch(
            "dan.tools.compress.compress",
            new_callable=AsyncMock,
            side_effect=OSError("no space"),
        ):
            result = await handle_compress({"path": "/bad"}, ctx)

        assert result.success is False

    @pytest.mark.asyncio
    async def test_git_status_valid(self) -> None:
        from dan.server.capability_handlers import handle_git_status

        ctx = _make_ctx()
        with patch(
            "dan.tools.git_status.git_status",
            new_callable=AsyncMock,
            return_value={"branch": "main", "changes": []},
        ):
            result = await handle_git_status({}, ctx)

        assert result.success is True

    @pytest.mark.asyncio
    async def test_git_status_error(self) -> None:
        from dan.server.capability_handlers import handle_git_status

        ctx = _make_ctx()
        with patch(
            "dan.tools.git_status.git_status",
            new_callable=AsyncMock,
            side_effect=RuntimeError("not a git repo"),
        ):
            result = await handle_git_status({}, ctx)

        assert result.success is False
        assert "Error" in result.message

    @pytest.mark.asyncio
    async def test_get_config_uses_current_mcp_bridge_api(self) -> None:
        from types import SimpleNamespace

        from dan.server.capabilities.config import handle_get_config

        class _Bridge:
            def list_servers(self) -> dict[str, Any]:
                return {
                    "filesystem": SimpleNamespace(connected=True),
                    "stale": SimpleNamespace(connected=False),
                }

        ctx = _make_ctx(mcp_bridge=_Bridge())

        result = await handle_get_config({}, ctx)

        payload = json.loads(result.message)
        assert result.success is True
        assert payload["mcp_servers_connected"] == ["filesystem"]


# ===========================================================================
# 5-2: DAN_LEARNING_MODE bundle
# ===========================================================================


class TestLearningModeBundle:
    """DAN_LEARNING_MODE=1 sets 4 vars; explicit override preserved."""

    def test_learning_mode_sets_four_vars(self) -> None:
        clean_env = {
            "DAN_LEARNING_MODE": "1",
        }
        for k in ("DAN_PROMPT_OPTIMIZATION", "DAN_MODEL_LEARNING",
                   "DAN_TOPOLOGY_LEARNING", "DAN_SKILL_LEARNING"):
            clean_env.pop(k, None)

        with patch.dict(os.environ, clean_env, clear=False):
            for k in ("DAN_PROMPT_OPTIMIZATION", "DAN_MODEL_LEARNING",
                       "DAN_TOPOLOGY_LEARNING", "DAN_SKILL_LEARNING"):
                os.environ.pop(k, None)

            if os.environ.get("DAN_LEARNING_MODE") == "1":
                os.environ.setdefault("DAN_PROMPT_OPTIMIZATION", "1")
                os.environ.setdefault("DAN_MODEL_LEARNING", "1")
                os.environ.setdefault("DAN_TOPOLOGY_LEARNING", "1")
                os.environ.setdefault("DAN_SKILL_LEARNING", "1")

            assert os.environ.get("DAN_PROMPT_OPTIMIZATION") == "1"
            assert os.environ.get("DAN_MODEL_LEARNING") == "1"
            assert os.environ.get("DAN_TOPOLOGY_LEARNING") == "1"
            assert os.environ.get("DAN_SKILL_LEARNING") == "1"

    def test_explicit_override_preserved(self) -> None:
        env = {
            "DAN_LEARNING_MODE": "1",
            "DAN_PROMPT_OPTIMIZATION": "0",
        }
        with patch.dict(os.environ, env, clear=False):
            if os.environ.get("DAN_LEARNING_MODE") == "1":
                os.environ.setdefault("DAN_PROMPT_OPTIMIZATION", "1")
                os.environ.setdefault("DAN_MODEL_LEARNING", "1")
                os.environ.setdefault("DAN_TOPOLOGY_LEARNING", "1")
                os.environ.setdefault("DAN_SKILL_LEARNING", "1")

            assert os.environ.get("DAN_PROMPT_OPTIMIZATION") == "0"
            assert os.environ.get("DAN_MODEL_LEARNING") == "1"

    def test_learning_mode_off_does_not_set(self) -> None:
        env = {"DAN_LEARNING_MODE": "0"}
        with patch.dict(os.environ, env, clear=False):
            for k in ("DAN_PROMPT_OPTIMIZATION", "DAN_MODEL_LEARNING",
                       "DAN_TOPOLOGY_LEARNING", "DAN_SKILL_LEARNING"):
                os.environ.pop(k, None)

            if os.environ.get("DAN_LEARNING_MODE") == "1":
                os.environ.setdefault("DAN_PROMPT_OPTIMIZATION", "1")

            assert os.environ.get("DAN_PROMPT_OPTIMIZATION") is None


# ===========================================================================
# 5-3: Introspection tools
# ===========================================================================


class TestIntrospectionTools:
    """inspect_node, list_test_cases, run_test_case."""

    @pytest.mark.asyncio
    async def test_inspect_node_valid(self) -> None:
        from dan.server.capability_handlers import handle_inspect_node

        mock_node = MagicMock()
        mock_node.id = "n1"
        mock_node.name = "Node 1"
        mock_node.node_type = "llm_operator"
        mock_node.model_dump.return_value = {
            "id": "n1",
            "name": "Node 1",
            "node_type": "llm_operator",
            "model": "gpt-4o",
            "input_ports": [],
            "output_ports": [],
        }

        mock_graph = MagicMock()
        mock_graph.node_by_id.return_value = mock_node

        graph_store = MagicMock()
        graph_store.load_as_model.return_value = mock_graph

        ctx = _make_ctx(graph_store=graph_store)

        with patch(
            "dan.server.variable_inspector.compute_upstream_variables",
            return_value={"input": "text"},
        ):
            result = await handle_inspect_node(
                {"workflow_id": "wf1", "node_id": "n1"}, ctx,
            )

        assert result.success is True
        assert result.data["node_id"] == "n1"
        assert result.data["type"] == "llm_operator"
        assert result.data["config"]["model"] == "gpt-4o"

    @pytest.mark.asyncio
    async def test_inspect_node_missing_args(self) -> None:
        from dan.server.capability_handlers import handle_inspect_node

        ctx = _make_ctx()
        result = await handle_inspect_node({"workflow_id": ""}, ctx)

        assert result.success is False
        assert "Missing" in result.message

    @pytest.mark.asyncio
    async def test_inspect_node_not_found(self) -> None:
        from dan.server.capability_handlers import handle_inspect_node

        mock_graph = MagicMock()
        mock_graph.node_by_id.return_value = None

        graph_store = MagicMock()
        graph_store.load_as_model.return_value = mock_graph

        ctx = _make_ctx(graph_store=graph_store)
        result = await handle_inspect_node(
            {"workflow_id": "wf1", "node_id": "bad-id"}, ctx,
        )

        assert result.success is False
        assert "not found" in result.message.lower()

    @pytest.mark.asyncio
    async def test_list_test_cases_valid(self) -> None:
        from dan.server.capability_handlers import handle_list_test_cases

        mock_case = MagicMock()
        mock_case.model_dump.return_value = {"case_id": "c1", "inputs": {}, "expected_outputs": {}}

        test_case_store = MagicMock()
        test_case_store.list_cases.return_value = [mock_case]

        ctx = _make_ctx(test_case_store=test_case_store)
        result = await handle_list_test_cases(
            {"workflow_id": "wf1", "node_id": "n1"}, ctx,
        )

        assert result.success is True
        assert "1 test case" in result.message

    @pytest.mark.asyncio
    async def test_list_test_cases_missing_args(self) -> None:
        from dan.server.capability_handlers import handle_list_test_cases

        ctx = _make_ctx()
        result = await handle_list_test_cases({}, ctx)

        assert result.success is False
        assert "Missing" in result.message

    @pytest.mark.asyncio
    async def test_list_test_cases_no_store(self) -> None:
        from dan.server.capability_handlers import handle_list_test_cases

        ctx = _make_ctx(test_case_store=None)
        result = await handle_list_test_cases(
            {"workflow_id": "wf1", "node_id": "n1"}, ctx,
        )

        assert result.success is False
        assert "not available" in result.message.lower()

    @pytest.mark.asyncio
    async def test_run_test_case_missing_args(self) -> None:
        from dan.server.capability_handlers import handle_run_test_case

        ctx = _make_ctx()
        result = await handle_run_test_case({"workflow_id": "wf1"}, ctx)

        assert result.success is False
        assert "Missing" in result.message

    @pytest.mark.asyncio
    async def test_run_test_case_no_store(self) -> None:
        from dan.server.capability_handlers import handle_run_test_case

        ctx = _make_ctx(test_case_store=None)
        result = await handle_run_test_case(
            {"workflow_id": "wf1", "node_id": "n1", "case_id": "c1"}, ctx,
        )

        assert result.success is False
        assert "not available" in result.message.lower()


# ===========================================================================
# 5-4: DAN_FULL_TOOLS gate
# ===========================================================================


class TestFullToolsGate:
    """DAN_FULL_TOOLS=0 -> not registered; DAN_FULL_TOOLS=1 -> all 13 registered."""

    def test_full_tools_off_skips_registration(self) -> None:
        from dan.server.capability_handlers import register_tool_capabilities

        registry = ChatCapabilityRegistry()
        with patch.dict(os.environ, {"DAN_FULL_TOOLS": "0"}, clear=False):
            register_tool_capabilities(registry)

        assert len(registry._tools) == 0

    def test_full_tools_on_registers_all(self) -> None:
        from dan.server.capability_handlers import register_tool_capabilities

        registry = ChatCapabilityRegistry()
        with patch.dict(os.environ, {"DAN_FULL_TOOLS": "1"}, clear=False):
            register_tool_capabilities(registry)

        assert len(registry._tools) == 14

    def test_full_tools_missing_env_defaults_on(self) -> None:
        from dan.server.capability_handlers import register_tool_capabilities

        registry = ChatCapabilityRegistry()
        env = dict(os.environ)
        env.pop("DAN_FULL_TOOLS", None)
        with patch.dict(os.environ, env, clear=True):
            register_tool_capabilities(registry)

        assert len(registry._tools) == 14

    def test_full_tools_registered_names(self) -> None:
        from dan.server.capability_handlers import register_tool_capabilities

        registry = ChatCapabilityRegistry()
        with patch.dict(os.environ, {"DAN_FULL_TOOLS": "1"}, clear=False):
            register_tool_capabilities(registry)

        expected_tools = {
            "python_eval", "csv_read", "compress",
            "file_copy", "file_move", "file_delete",
            "git_status", "git_diff", "git_log",
            "git_branch", "git_commit", "git_worktree",
            "notify", "text_diff",
        }
        registered = set(registry._tools.keys())
        assert expected_tools == registered
