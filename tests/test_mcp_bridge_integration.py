from __future__ import annotations

import sys
from pathlib import Path

import pytest

from dan.mcp_bridge import MCPBridge, MCPServerConfig


@pytest.mark.asyncio
async def test_mcp_bridge_real_server_round_trip(tmp_path: Path):
    pytest.importorskip("mcp")

    server_script = tmp_path / "toy_mcp_server.py"
    server_script.write_text(
        (
            "from mcp.server.fastmcp import FastMCP\n"
            "\n"
            "mcp = FastMCP('toy-test-server')\n"
            "\n"
            "@mcp.tool()\n"
            "def add(a: int, b: int) -> str:\n"
            "    return str(a + b)\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    mcp.run(transport='stdio')\n"
        ),
        encoding="utf-8",
    )

    bridge = MCPBridge()
    config = MCPServerConfig(
        command=sys.executable,
        args=[str(server_script)],
        description="Toy MCP test server",
    )

    try:
        tools = await bridge.connect("toy", config)
        assert any(tool["name"] == "add" for tool in tools)

        result = await bridge.call_tool("toy", "add", {"a": 2, "b": 3})
        assert result["success"] is True
        assert "5" in result["result"]
    finally:
        await bridge.shutdown()
