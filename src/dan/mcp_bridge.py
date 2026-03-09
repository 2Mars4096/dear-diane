"""MCP client bridge — lets DAN consume external MCP servers as tool sources."""

from __future__ import annotations

import asyncio
import logging
import os
import json
import tempfile
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dan.executors.tool import ToolRegistry
from dan.server.capability_registry import (
    CapabilityContext,
    CapabilityResult,
    ChatCapabilityRegistry,
    build_tool_schema,
)

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    _HAS_MCP = True
except ImportError:
    _HAS_MCP = False
    ClientSession = None
    StdioServerParameters = None
    stdio_client = None

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config models
# ---------------------------------------------------------------------------


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server."""

    model_config = ConfigDict(populate_by_name=True)

    command: str
    args: list[str] = []
    env: dict[str, str] | None = None
    auto_connect: bool = Field(True, alias="autoConnect")
    description: str = ""
    modes: list[str] | None = None


class MCPConfig(BaseModel):
    """Top-level MCP configuration."""

    servers: dict[str, MCPServerConfig] = {}


# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------


def _default_config_path() -> Path:
    env = os.environ.get("DAN_MCP_CONFIG")
    if env:
        return Path(env)
    return Path.home() / ".dan" / "mcp.json"


def load_mcp_config(path: str | Path | None = None) -> MCPConfig:
    p = Path(path) if path else _default_config_path()
    if not p.exists():
        return MCPConfig()
    try:
        raw = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read MCP config %s: %s", p, exc)
        return MCPConfig()
    servers_raw = raw.get("mcpServers", {})
    servers = {name: MCPServerConfig(**cfg) for name, cfg in servers_raw.items()}
    return MCPConfig(servers=servers)


def save_mcp_config(config: MCPConfig, path: str | Path | None = None) -> None:
    p = Path(path) if path else _default_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mcpServers": {
            name: cfg.model_dump(by_alias=True, exclude_none=True)
            for name, cfg in config.servers.items()
        }
    }
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, str(p))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Connection / status dataclasses
# ---------------------------------------------------------------------------


@dataclass
class MCPConnection:
    session: Any
    tools: list[dict]
    exit_stack: AsyncExitStack
    server_config: MCPServerConfig
    last_error: str | None = None


@dataclass
class ServerStatus:
    name: str
    connected: bool
    tool_names: list[str]
    description: str
    last_error: str | None = None


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------


class MCPBridge:
    def __init__(self) -> None:
        self._connections: dict[str, MCPConnection] = {}

    async def connect(
        self, server_name: str, config: MCPServerConfig
    ) -> list[dict]:
        if not _HAS_MCP:
            raise ImportError(
                "The 'mcp' package is required for MCP bridge. "
                "Install it with: pip install mcp"
            )

        if server_name in self._connections:
            await self.disconnect(server_name)

        stack = AsyncExitStack()
        try:
            params = StdioServerParameters(
                command=config.command,
                args=config.args,
                env=config.env,
            )
            stdio_transport = await stack.enter_async_context(stdio_client(params))
            read_stream, write_stream = stdio_transport
            session: ClientSession = await stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()

            raw_tools = await session.list_tools()
            tools: list[dict] = []
            for t in raw_tools.tools:
                tools.append(
                    {
                        "name": t.name,
                        "description": getattr(t, "description", ""),
                        "input_schema": (
                            t.inputSchema if hasattr(t, "inputSchema") else {}
                        ),
                    }
                )

            if len(tools) > 20:
                logger.warning(
                    "MCP server %r exposes %d tools (>20) — may affect LLM context",
                    server_name,
                    len(tools),
                )

            self._connections[server_name] = MCPConnection(
                session=session,
                tools=tools,
                exit_stack=stack,
                server_config=config,
            )
            logger.info(
                "Connected to MCP server %r (%d tools)", server_name, len(tools)
            )
            return tools

        except Exception as exc:
            await stack.aclose()
            logger.error("Failed to connect to MCP server %r: %s", server_name, exc)
            raise

    async def disconnect(self, server_name: str) -> None:
        conn = self._connections.pop(server_name, None)
        if conn is None:
            return
        try:
            await conn.exit_stack.aclose()
        except Exception as exc:
            logger.warning("Error closing MCP server %r: %s", server_name, exc)

    async def call_tool(
        self, server_name: str, tool_name: str, arguments: dict
    ) -> dict:
        conn = self._connections.get(server_name)
        if conn is None:
            return {"error": f"Server {server_name!r} is not connected", "success": False}

        try:
            result = await conn.session.call_tool(tool_name, arguments)
        except Exception as exc:
            logger.warning(
                "call_tool failed on %r/%r, attempting reconnect: %s",
                server_name,
                tool_name,
                exc,
            )
            try:
                await self.disconnect(server_name)
                await self.connect(server_name, conn.server_config)
                reconn = self._connections.get(server_name)
                if reconn is None:
                    return {"error": f"Reconnect to {server_name!r} failed", "success": False}
                result = await reconn.session.call_tool(tool_name, arguments)
            except Exception as exc2:
                msg = f"Reconnect to {server_name!r} failed: {exc2}"
                logger.error(msg)
                return {"error": msg, "success": False}

        texts: list[str] = []
        images: list[dict] = []
        for item in result.content:
            if hasattr(item, "text"):
                texts.append(item.text)
            elif hasattr(item, "data") and hasattr(item, "mimeType"):
                images.append({"data": item.data, "mime_type": item.mimeType})
                texts.append(f"[Image: {item.mimeType}]")
            else:
                texts.append(str(item))

        combined = "\n".join(texts)
        if result.isError:
            return {"error": combined, "success": False}
        out: dict[str, Any] = {"result": combined, "success": True}
        if images:
            out["images"] = images
        return out

    def list_servers(self) -> dict[str, ServerStatus]:
        out: dict[str, ServerStatus] = {}
        for name, conn in self._connections.items():
            out[name] = ServerStatus(
                name=name,
                connected=True,
                tool_names=[t["name"] for t in conn.tools],
                description=conn.server_config.description,
                last_error=conn.last_error,
            )
        return out

    def get_server_tools(self, server_name: str) -> list[dict]:
        conn = self._connections.get(server_name)
        if conn is None:
            return []
        return list(conn.tools)

    def is_connected(self, server_name: str) -> bool:
        return server_name in self._connections

    async def shutdown(self) -> None:
        names = list(self._connections.keys())
        for name in names:
            await self.disconnect(name)


# ---------------------------------------------------------------------------
# Known server registry
# ---------------------------------------------------------------------------

KNOWN_MCP_SERVERS: dict[str, dict] = {
    "stata": {
        "pip": "mcp-stata",
        "command": "mcp-stata",
        "description": "Stata statistical analysis — run commands, inspect data, export graphs",
    },
    "filesystem": {
        "pip": "mcp-server-filesystem",
        "command": "mcp-server-filesystem",
        "description": "File system operations via MCP",
    },
}


def resolve_known_server(name: str) -> dict | None:
    return KNOWN_MCP_SERVERS.get(name)


# ---------------------------------------------------------------------------
# pip install helper
# ---------------------------------------------------------------------------


async def install_mcp_package(package_name: str) -> tuple[bool, str]:
    """Install a pip package asynchronously. Returns (success, output)."""
    import sys

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "pip",
        "install",
        "-q",
        package_name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    success = proc.returncode == 0
    output = stdout.decode() if success else stderr.decode()
    return success, output.strip()


# ---------------------------------------------------------------------------
# Capability + ToolRegistry integration
# ---------------------------------------------------------------------------

_MCP_TOOL_MODES = ["agent", "conversation", "debug"]


def _mcp_tool_name(server_name: str, tool_name: str) -> str:
    """Build namespaced tool id: mcp_{server}_{tool}."""
    safe_server = server_name.replace("-", "_").replace(".", "_")
    safe_tool = tool_name.replace("-", "_").replace(".", "_")
    return f"mcp_{safe_server}_{safe_tool}"


def register_mcp_tools(
    capability_registry: ChatCapabilityRegistry,
    tool_registry: ToolRegistry | None,
    bridge: MCPBridge,
    server_name: str,
) -> list[str]:
    """Register all tools from a connected MCP server into both registries.

    Returns list of registered tool IDs.
    """
    tools = bridge.get_server_tools(server_name)
    if not tools:
        return []

    registered: list[str] = []
    category = f"mcp:{server_name}"

    for tool_info in tools:
        raw_name = tool_info["name"]
        tool_id = _mcp_tool_name(server_name, raw_name)
        description = f"[{server_name}] {tool_info.get('description', raw_name)}"
        input_schema = tool_info.get("input_schema", {"type": "object", "properties": {}})

        schema = build_tool_schema(tool_id, description, input_schema)

        # Create capability handler closure
        _server = server_name
        _tool = raw_name

        async def _handler(
            args: dict, ctx: CapabilityContext, _s: str = _server, _t: str = _tool
        ) -> CapabilityResult:
            if ctx.mcp_bridge is None:
                return CapabilityResult(success=False, message="MCP bridge not available")
            result = await ctx.mcp_bridge.call_tool(_s, _t, args)
            if result.get("success"):
                return CapabilityResult(
                    success=True,
                    message=result.get("result", ""),
                    output_preview=result.get("result", "")[:500],
                )
            return CapabilityResult(
                success=False,
                message=result.get("error", "Unknown MCP tool error"),
            )

        conn = bridge._connections.get(server_name)
        modes = _MCP_TOOL_MODES
        if conn and conn.server_config.modes:
            modes = conn.server_config.modes

        capability_registry.register(
            tool_id, schema, _handler, modes=modes, category=category
        )

        # Also register in workflow ToolRegistry
        if tool_registry is not None:
            _s2 = server_name
            _t2 = raw_name

            async def _tool_fn(
                _s: str = _s2, _t: str = _t2, **kwargs: Any
            ) -> dict:
                return await bridge.call_tool(_s, _t, kwargs)

            tool_registry.register(tool_id, _tool_fn)

        registered.append(tool_id)

    logger.info(
        "Registered %d MCP tools from %r: %s",
        len(registered), server_name, registered,
    )
    return registered


def unregister_mcp_tools(
    capability_registry: ChatCapabilityRegistry,
    tool_registry: ToolRegistry | None,
    server_name: str,
) -> int:
    """Remove all tools for an MCP server from both registries."""
    category = f"mcp:{server_name}"
    count = capability_registry.unregister_by_category(category)
    # ToolRegistry doesn't have unregister — tools stay but become inert
    # (call_tool on the bridge will fail with "not connected")
    if count:
        logger.info("Unregistered %d MCP tools for %r", count, server_name)
    return count


def get_mcp_tool_hint(bridge: MCPBridge) -> str:
    """Build a prompt hint string listing available MCP tools for the system prompt."""
    lines: list[str] = []
    for name, status in bridge.list_servers().items():
        if status.connected and status.tool_names:
            tool_list = ", ".join(status.tool_names[:10])
            suffix = f" (+{len(status.tool_names) - 10} more)" if len(status.tool_names) > 10 else ""
            lines.append(f"**{name} (MCP):** {tool_list}{suffix}")
    return "\n".join(lines)
