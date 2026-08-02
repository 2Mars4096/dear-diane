from __future__ import annotations

import pytest

import dan.mcp_bridge as mcp_bridge
from dan.executors.tool import ToolRegistry
from dan.server.capability_registry import ChatCapabilityRegistry


class FakeBridge:
    def __init__(self) -> None:
        self.connect_calls: list[tuple[str, mcp_bridge.MCPServerConfig]] = []

    async def connect(self, server_name: str, config: mcp_bridge.MCPServerConfig) -> list[dict]:
        self.connect_calls.append((server_name, config))
        if server_name == "broken":
            raise RuntimeError("boom")
        return [{"name": f"{server_name}_tool"}]


@pytest.mark.asyncio
async def test_autoconnect_configured_mcp_servers_registers_only_auto_connect(monkeypatch):
    bridge = FakeBridge()
    registry = ChatCapabilityRegistry()
    tool_registry = ToolRegistry()
    registered: list[str] = []

    def fake_register_mcp_tools(cap_registry, reg_tool_registry, reg_bridge, server_name):
        assert cap_registry is registry
        assert reg_tool_registry is tool_registry
        assert reg_bridge is bridge
        registered.append(server_name)
        return [f"mcp_{server_name}_tool"]

    monkeypatch.setattr(mcp_bridge, "register_mcp_tools", fake_register_mcp_tools)

    config = mcp_bridge.MCPConfig(
        servers={
            "alpha": mcp_bridge.MCPServerConfig(command="alpha", autoConnect=True),
            "beta": mcp_bridge.MCPServerConfig(command="beta", autoConnect=False),
        }
    )

    connected = await mcp_bridge.autoconnect_configured_mcp_servers(
        bridge,
        registry,
        tool_registry,
        config=config,
    )

    assert connected == ["alpha"]
    assert [name for name, _ in bridge.connect_calls] == ["alpha"]
    assert registered == ["alpha"]


@pytest.mark.asyncio
async def test_autoconnect_configured_mcp_servers_continues_after_failure(monkeypatch):
    bridge = FakeBridge()
    registry = ChatCapabilityRegistry()
    tool_registry = ToolRegistry()
    registered: list[str] = []

    def fake_register_mcp_tools(_cap_registry, _tool_registry, _bridge, server_name):
        registered.append(server_name)
        return [f"mcp_{server_name}_tool"]

    monkeypatch.setattr(mcp_bridge, "register_mcp_tools", fake_register_mcp_tools)

    config = mcp_bridge.MCPConfig(
        servers={
            "broken": mcp_bridge.MCPServerConfig(command="broken", autoConnect=True),
            "healthy": mcp_bridge.MCPServerConfig(command="healthy", autoConnect=True),
        }
    )

    connected = await mcp_bridge.autoconnect_configured_mcp_servers(
        bridge,
        registry,
        tool_registry,
        config=config,
    )

    assert connected == ["healthy"]
    assert [name for name, _ in bridge.connect_calls] == ["broken", "healthy"]
    assert registered == ["healthy"]
import asyncio
import json
import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from dan.mcp_bridge import (
    MCPBridge, MCPConfig, MCPServerConfig, MCPConnection, ServerStatus,
    load_mcp_config, save_mcp_config, _default_config_path,
    _mcp_tool_name, resolve_known_server, KNOWN_MCP_SERVERS,
    register_mcp_tools, unregister_mcp_tools, get_mcp_tool_hint,
)
from dan.server.capability_registry import (
    ChatCapabilityRegistry, CapabilityContext, CapabilityResult, build_tool_schema,
)


class TestMCPConfig:
    def test_server_config_defaults(self):
        cfg = MCPServerConfig(command="stata")
        assert cfg.command == "stata"
        assert cfg.args == []
        assert cfg.env is None
        assert cfg.auto_connect is True
        assert cfg.description == ""
        assert cfg.modes is None

    def test_config_empty(self):
        cfg = MCPConfig()
        assert cfg.servers == {}

    def test_config_with_servers(self):
        cfg = MCPConfig(servers={
            "stata": MCPServerConfig(command="mcp-stata", description="Stata")
        })
        assert "stata" in cfg.servers
        assert cfg.servers["stata"].command == "mcp-stata"


class TestConfigPersistence:
    def test_load_missing_file(self, tmp_path):
        cfg = load_mcp_config(tmp_path / "nonexistent.json")
        assert cfg.servers == {}

    def test_save_and_load_roundtrip(self, tmp_path):
        path = tmp_path / "mcp.json"
        cfg = MCPConfig(servers={
            "test": MCPServerConfig(command="test-cmd", args=["--flag"], description="Test server")
        })
        save_mcp_config(cfg, path)
        loaded = load_mcp_config(path)
        assert "test" in loaded.servers
        assert loaded.servers["test"].command == "test-cmd"
        assert loaded.servers["test"].args == ["--flag"]

    def test_saves_under_mcpServers_key(self, tmp_path):
        path = tmp_path / "mcp.json"
        cfg = MCPConfig(servers={"s": MCPServerConfig(command="c")})
        save_mcp_config(cfg, path)
        raw = json.loads(path.read_text())
        assert "mcpServers" in raw
        assert "s" in raw["mcpServers"]

    def test_loads_from_mcpServers_key(self, tmp_path):
        path = tmp_path / "mcp.json"
        path.write_text(json.dumps({"mcpServers": {"x": {"command": "xcmd"}}}))
        cfg = load_mcp_config(path)
        assert "x" in cfg.servers
        assert cfg.servers["x"].command == "xcmd"

    def test_env_var_override(self, tmp_path, monkeypatch):
        path = tmp_path / "custom.json"
        monkeypatch.setenv("DAN_MCP_CONFIG", str(path))
        assert _default_config_path() == path

    def test_creates_parent_dir(self, tmp_path):
        path = tmp_path / "subdir" / "mcp.json"
        cfg = MCPConfig(servers={"a": MCPServerConfig(command="a")})
        save_mcp_config(cfg, path)
        assert path.exists()

    def test_autoconnect_camelcase_roundtrip(self, tmp_path):
        """Config saves as 'autoConnect' (camelCase) for Cursor/Claude compatibility."""
        path = tmp_path / "mcp.json"
        cfg = MCPConfig(servers={
            "s": MCPServerConfig(command="c", auto_connect=False)
        })
        save_mcp_config(cfg, path)
        raw = json.loads(path.read_text())
        assert "autoConnect" in raw["mcpServers"]["s"]
        assert "auto_connect" not in raw["mcpServers"]["s"]
        assert raw["mcpServers"]["s"]["autoConnect"] is False
        loaded = load_mcp_config(path)
        assert loaded.servers["s"].auto_connect is False

    def test_loads_cursor_format_autoconnect(self, tmp_path):
        """Cursor-format JSON with camelCase 'autoConnect' key loads correctly."""
        path = tmp_path / "mcp.json"
        path.write_text(json.dumps({
            "mcpServers": {"x": {"command": "xcmd", "autoConnect": False}}
        }))
        cfg = load_mcp_config(path)
        assert cfg.servers["x"].auto_connect is False

    def test_corrupt_json_returns_empty(self, tmp_path):
        path = tmp_path / "mcp.json"
        path.write_text("{invalid json!!")
        cfg = load_mcp_config(path)
        assert cfg.servers == {}


class TestMCPBridge:
    def test_initial_state(self):
        bridge = MCPBridge()
        assert bridge.list_servers() == {}
        assert bridge.get_connected_servers() == {}
        assert not bridge.is_connected("foo")
        assert bridge.get_server_tools("foo") == []

    @pytest.mark.asyncio
    async def test_call_tool_not_connected(self):
        bridge = MCPBridge()
        result = await bridge.call_tool("noserver", "tool", {})
        assert result["success"] is False
        assert "not connected" in result["error"]

    @pytest.mark.asyncio
    async def test_shutdown_empty(self):
        bridge = MCPBridge()
        await bridge.shutdown()

    @pytest.mark.asyncio
    async def test_connect_without_mcp_package(self, monkeypatch):
        import dan.mcp_bridge as mod
        monkeypatch.setattr(mod, "_HAS_MCP", False)
        bridge = MCPBridge()
        with pytest.raises(ImportError, match="mcp"):
            await bridge.connect("test", MCPServerConfig(command="test"))

    @pytest.mark.asyncio
    async def test_connect_and_list(self):
        bridge = MCPBridge()

        mock_session = AsyncMock()
        mock_tool = MagicMock()
        mock_tool.name = "run_command"
        mock_tool.description = "Run a Stata command"
        mock_tool.inputSchema = {"type": "object", "properties": {"cmd": {"type": "string"}}}
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=MagicMock(tools=[mock_tool]))

        mock_read = MagicMock()
        mock_write = MagicMock()

        import dan.mcp_bridge as mod
        original_has_mcp = mod._HAS_MCP

        try:
            mod._HAS_MCP = True

            with patch.object(mod, "stdio_client") as mock_stdio, \
                 patch.object(mod, "ClientSession") as mock_cs_class, \
                 patch.object(mod, "StdioServerParameters") as mock_params:

                mock_stdio_ctx = AsyncMock()
                mock_stdio_ctx.__aenter__ = AsyncMock(return_value=(mock_read, mock_write))
                mock_stdio_ctx.__aexit__ = AsyncMock(return_value=False)
                mock_stdio.return_value = mock_stdio_ctx

                mock_cs_ctx = AsyncMock()
                mock_cs_ctx.__aenter__ = AsyncMock(return_value=mock_session)
                mock_cs_ctx.__aexit__ = AsyncMock(return_value=False)
                mock_cs_class.return_value = mock_cs_ctx

                config = MCPServerConfig(command="mcp-stata", description="Stata")
                tools = await bridge.connect("stata", config)

                assert len(tools) == 1
                assert tools[0]["name"] == "run_command"
                assert bridge.is_connected("stata")

                servers = bridge.list_servers()
                assert "stata" in servers
                assert servers["stata"].connected is True
                assert "run_command" in servers["stata"].tool_names
        finally:
            mod._HAS_MCP = original_has_mcp

    @pytest.mark.asyncio
    async def test_disconnect(self):
        bridge = MCPBridge()
        mock_stack = AsyncMock()
        mock_stack.aclose = AsyncMock()
        bridge._connections["test"] = MCPConnection(
            session=MagicMock(),
            tools=[{"name": "t1", "description": "", "input_schema": {}}],
            exit_stack=mock_stack,
            server_config=MCPServerConfig(command="test"),
        )
        assert bridge.is_connected("test")
        await bridge.disconnect("test")
        assert not bridge.is_connected("test")
        mock_stack.aclose.assert_called_once()

    def test_get_connected_servers_alias_returns_raw_connections(self):
        bridge = MCPBridge()
        bridge._connections["test"] = MCPConnection(
            session=MagicMock(),
            tools=[{"name": "t1", "description": "", "input_schema": {}}],
            exit_stack=AsyncMock(),
            server_config=MCPServerConfig(command="test"),
        )

        connected = bridge.get_connected_servers()

        assert list(connected.keys()) == ["test"]
        assert isinstance(connected["test"], MCPConnection)

    @pytest.mark.asyncio
    async def test_call_tool_success(self):
        bridge = MCPBridge()
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.isError = False
        mock_content = MagicMock()
        mock_content.text = "Success output"
        mock_result.content = [mock_content]
        mock_session.call_tool = AsyncMock(return_value=mock_result)

        bridge._connections["srv"] = MCPConnection(
            session=mock_session,
            tools=[],
            exit_stack=AsyncMock(),
            server_config=MCPServerConfig(command="cmd"),
        )

        result = await bridge.call_tool("srv", "tool1", {"arg": "val"})
        assert result["success"] is True
        assert result["result"] == "Success output"

    @pytest.mark.asyncio
    async def test_call_tool_error_result(self):
        bridge = MCPBridge()
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.isError = True
        mock_content = MagicMock()
        mock_content.text = "Error message"
        mock_result.content = [mock_content]
        mock_session.call_tool = AsyncMock(return_value=mock_result)

        bridge._connections["srv"] = MCPConnection(
            session=mock_session,
            tools=[],
            exit_stack=AsyncMock(),
            server_config=MCPServerConfig(command="cmd"),
        )

        result = await bridge.call_tool("srv", "tool1", {})
        assert result["success"] is False
        assert "Error message" in result["error"]


    @pytest.mark.asyncio
    async def test_call_tool_reconnect_on_failure(self):
        bridge = MCPBridge()
        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(side_effect=ConnectionError("broken pipe"))

        config = MCPServerConfig(command="cmd")
        bridge._connections["srv"] = MCPConnection(
            session=mock_session,
            tools=[{"name": "t", "description": "", "input_schema": {}}],
            exit_stack=AsyncMock(),
            server_config=config,
        )

        import dan.mcp_bridge as mod
        original = mod._HAS_MCP

        try:
            mod._HAS_MCP = True
            mock_new_session = AsyncMock()
            mock_ok_result = MagicMock()
            mock_ok_result.isError = False
            mock_ok_content = MagicMock()
            mock_ok_content.text = "recovered"
            mock_ok_result.content = [mock_ok_content]
            mock_new_session.call_tool = AsyncMock(return_value=mock_ok_result)
            mock_new_session.initialize = AsyncMock()
            mock_new_session.list_tools = AsyncMock(
                return_value=MagicMock(tools=[])
            )

            mock_read = MagicMock()
            mock_write = MagicMock()

            with patch.object(mod, "stdio_client") as mock_stdio, \
                 patch.object(mod, "ClientSession") as mock_cs_class, \
                 patch.object(mod, "StdioServerParameters"):
                mock_stdio_ctx = AsyncMock()
                mock_stdio_ctx.__aenter__ = AsyncMock(return_value=(mock_read, mock_write))
                mock_stdio_ctx.__aexit__ = AsyncMock(return_value=False)
                mock_stdio.return_value = mock_stdio_ctx
                mock_cs_ctx = AsyncMock()
                mock_cs_ctx.__aenter__ = AsyncMock(return_value=mock_new_session)
                mock_cs_ctx.__aexit__ = AsyncMock(return_value=False)
                mock_cs_class.return_value = mock_cs_ctx

                result = await bridge.call_tool("srv", "t", {})
                assert result["success"] is True
                assert result["result"] == "recovered"
        finally:
            mod._HAS_MCP = original

    @pytest.mark.asyncio
    async def test_call_tool_image_content(self):
        bridge = MCPBridge()
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.isError = False
        mock_img = MagicMock()
        mock_img.text = None
        del mock_img.text
        mock_img.data = "base64data=="
        mock_img.mimeType = "image/png"
        mock_result.content = [mock_img]
        mock_session.call_tool = AsyncMock(return_value=mock_result)

        bridge._connections["srv"] = MCPConnection(
            session=mock_session,
            tools=[],
            exit_stack=AsyncMock(),
            server_config=MCPServerConfig(command="cmd"),
        )

        result = await bridge.call_tool("srv", "graph", {})
        assert result["success"] is True
        assert result["images"][0]["data"] == "base64data=="
        assert result["images"][0]["mime_type"] == "image/png"

    @pytest.mark.asyncio
    async def test_call_tool_embedded_resource_content(self):
        bridge = MCPBridge()
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.isError = False

        class FakeResource:
            def model_dump(self, exclude_none: bool = True):
                return {"uri": "file:///tmp/out.txt", "mimeType": "text/plain", "text": "hello"}

        class FakeEmbeddedResourceItem:
            def __init__(self) -> None:
                self.resource = FakeResource()

        mock_result.content = [FakeEmbeddedResourceItem()]
        mock_session.call_tool = AsyncMock(return_value=mock_result)

        bridge._connections["srv"] = MCPConnection(
            session=mock_session,
            tools=[],
            exit_stack=AsyncMock(),
            server_config=MCPServerConfig(command="cmd"),
        )

        result = await bridge.call_tool("srv", "resource_tool", {})
        assert result["success"] is True
        assert result["resource"]["uri"] == "file:///tmp/out.txt"
        assert result["resource"]["mimeType"] == "text/plain"
        assert "[Resource: file:///tmp/out.txt]" in result["result"]


class TestToolNaming:
    def test_simple_names(self):
        assert _mcp_tool_name("stata", "run_command") == "mcp_stata_run_command"

    def test_hyphen_names(self):
        assert _mcp_tool_name("my-server", "do-thing") == "mcp_my_server_do_thing"

    def test_dot_names(self):
        assert _mcp_tool_name("my.server", "do.thing") == "mcp_my_server_do_thing"


class TestKnownServers:
    def test_stata_known(self):
        result = resolve_known_server("stata")
        assert result is not None
        assert result["pip"] == "mcp-stata"

    def test_unknown_returns_none(self):
        assert resolve_known_server("unknown_server_xyz") is None


class TestCapabilityRegistryUnregister:
    def test_unregister_existing(self):
        reg = ChatCapabilityRegistry()
        schema = build_tool_schema("test", "desc", {"type": "object"})

        async def handler(args, ctx):
            return CapabilityResult(success=True, message="ok")

        reg.register("test", schema, handler)
        assert reg.unregister("test") is True
        assert "test" not in reg.list_tool_names()

    def test_unregister_nonexistent(self):
        reg = ChatCapabilityRegistry()
        assert reg.unregister("nope") is False

    def test_unregister_by_category(self):
        reg = ChatCapabilityRegistry()
        schema = build_tool_schema("t1", "d", {"type": "object"})

        async def handler(args, ctx):
            return CapabilityResult(success=True, message="ok")

        reg.register("t1", schema, handler, category="mcp:stata")
        reg.register("t2", schema, handler, category="mcp:stata")
        reg.register("t3", schema, handler, category="builtin")
        assert reg.unregister_by_category("mcp:stata") == 2
        assert "t3" in reg.list_tool_names()
        assert "t1" not in reg.list_tool_names()

    def test_mode_filtered_tools_and_availability(self):
        reg = ChatCapabilityRegistry()
        schema = build_tool_schema("mcp_tool", "d", {"type": "object"})

        async def handler(args, ctx):
            return CapabilityResult(success=True, message="ok")

        reg.register("mcp_tool", schema, handler, modes=["agent", "debug"])
        assert [tool["function"]["name"] for tool in reg.get_tools("ask")] == []
        assert [tool["function"]["name"] for tool in reg.get_tools("agent")] == ["mcp_tool"]
        assert reg.is_available("mcp_tool", "ask") is False
        assert reg.is_available("mcp_tool", "debug") is True

    @pytest.mark.asyncio
    async def test_execute_respects_mode_filter(self):
        reg = ChatCapabilityRegistry()
        schema = build_tool_schema("mcp_tool", "d", {"type": "object"})

        async def handler(args, ctx):
            return CapabilityResult(success=True, message="ok")

        reg.register("mcp_tool", schema, handler, modes=["agent"])
        result = await reg.execute("mcp_tool", {}, CapabilityContext(workflow_id=""), mode="ask")
        assert result.success is False
        assert "not available" in result.message


class TestRegisterMCPTools:
    def test_register_and_unregister(self):
        cap_reg = ChatCapabilityRegistry()
        bridge = MCPBridge()
        bridge._connections["srv"] = MCPConnection(
            session=MagicMock(),
            tools=[
                {"name": "tool_a", "description": "Tool A", "input_schema": {"type": "object", "properties": {}}},
                {"name": "tool_b", "description": "Tool B", "input_schema": {"type": "object", "properties": {}}},
            ],
            exit_stack=AsyncMock(),
            server_config=MCPServerConfig(command="cmd"),
        )

        registered = register_mcp_tools(cap_reg, None, bridge, "srv")
        assert len(registered) == 2
        assert "mcp_srv_tool_a" in registered
        assert "mcp_srv_tool_b" in registered
        assert "mcp_srv_tool_a" in cap_reg.list_tool_names()

        count = unregister_mcp_tools(cap_reg, None, "srv")
        assert count == 2
        assert "mcp_srv_tool_a" not in cap_reg.list_tool_names()

    def test_register_empty_server(self):
        cap_reg = ChatCapabilityRegistry()
        bridge = MCPBridge()
        result = register_mcp_tools(cap_reg, None, bridge, "empty")
        assert result == []

    def test_get_mcp_tool_hint_empty(self):
        bridge = MCPBridge()
        assert get_mcp_tool_hint(bridge) == ""

    def test_get_mcp_tool_hint_with_tools(self):
        bridge = MCPBridge()
        bridge._connections["stata"] = MCPConnection(
            session=MagicMock(),
            tools=[{"name": "run", "description": "", "input_schema": {}}],
            exit_stack=AsyncMock(),
            server_config=MCPServerConfig(command="c", description="Stata"),
        )
        hint = get_mcp_tool_hint(bridge)
        assert "stata (MCP)" in hint
        assert "run" in hint

    def test_register_mcp_tools_respects_modes(self):
        cap_reg = ChatCapabilityRegistry()
        bridge = MCPBridge()
        bridge._connections["srv"] = MCPConnection(
            session=MagicMock(),
            tools=[
                {"name": "tool_a", "description": "Tool A", "input_schema": {"type": "object", "properties": {}}},
            ],
            exit_stack=AsyncMock(),
            server_config=MCPServerConfig(command="cmd", modes=["agent"]),
        )

        registered = register_mcp_tools(cap_reg, None, bridge, "srv")

        assert registered == ["mcp_srv_tool_a"]
        assert [tool["function"]["name"] for tool in cap_reg.get_tools("ask")] == []
        assert [tool["function"]["name"] for tool in cap_reg.get_tools("agent")] == ["mcp_srv_tool_a"]

    @pytest.mark.asyncio
    async def test_register_mcp_tools_preserves_resource_payload_in_capability_result(self):
        cap_reg = ChatCapabilityRegistry()
        bridge = MCPBridge()
        bridge._connections["srv"] = MCPConnection(
            session=MagicMock(),
            tools=[
                {"name": "tool_a", "description": "Tool A", "input_schema": {"type": "object", "properties": {}}},
            ],
            exit_stack=AsyncMock(),
            server_config=MCPServerConfig(command="cmd"),
        )

        async def fake_call_tool(server_name: str, tool_name: str, arguments: dict) -> dict:
            assert server_name == "srv"
            assert tool_name == "tool_a"
            return {
                "success": True,
                "result": "[Resource: file:///tmp/out.txt]",
                "resource": {"uri": "file:///tmp/out.txt", "mimeType": "text/plain"},
            }

        bridge.call_tool = fake_call_tool  # type: ignore[method-assign]
        register_mcp_tools(cap_reg, None, bridge, "srv")

        result = await cap_reg.execute(
            "mcp_srv_tool_a",
            {},
            CapabilityContext(workflow_id="", mcp_bridge=bridge),
            mode="agent",
        )
        assert result.success is True
        assert result.data == {"resource": {"uri": "file:///tmp/out.txt", "mimeType": "text/plain"}}
