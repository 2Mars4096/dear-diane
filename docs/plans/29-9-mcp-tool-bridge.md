# 29-9: MCP Tool Bridge — Consume External MCP Servers as Chat Tools

**Parent:** [29-concierge-memory-evolvement](29-concierge-memory-evolvement.md)
**Status:** completed
**Goal:** Let the concierge discover, connect to, and use tools from external MCP servers (like mcp_stata) alongside built-in tools — installable at runtime from chat.

## Problem

DAN has 32 built-in tools and a clean capability registry, but domain-specific tool servers (Stata, R, databases, custom APIs) require LLMs to compose raw shell commands — a task even strong models struggle with. The MCP ecosystem already has high-quality tool servers for these domains (e.g., `mcp-stata` with command execution, data inspection, graph export, stored results access). DAN should consume them as first-class tools.

Currently:
- DAN **publishes** workflows as MCP servers (`dan.publish.mcp_server`) but cannot **consume** external MCP servers
- The LLM's only path to Stata is `shell_command` → guess CLI syntax → parse raw output
- There's no way to add new tool servers at runtime — every tool must be a Python module in `dan.tools/`
- MCP servers provide structured tool schemas (name, description, JSON Schema parameters) that map directly to DAN's `ChatCapabilityRegistry` format

## Architecture

```
                        ┌─────────────────────────────────────┐
                        │      ChatCapabilityRegistry         │
                        │  (unified tool surface for LLM)     │
                        ├──────────────┬──────────────────────┤
                        │  Built-in    │  MCP Bridge          │
                        │  tools       │  tools               │
                        │  (32 tools)  │  (dynamic)           │
                        └──────┬───────┴──────────┬───────────┘
                               │                  │
                   ┌───────────┘          ┌───────┴───────┐
                   │                      │   MCPBridge   │
            dan.tools.*              ┌────┤  (client mgr) ├────┐
            (Python modules)         │    └───────────────┘    │
                                     │                         │
                              ┌──────┴──────┐         ┌───────┴──────┐
                              │ mcp_stata   │         │ other MCP    │
                              │ (stdio)     │         │ servers      │
                              └─────────────┘         └──────────────┘
```

**Transport:** stdio (spawn subprocess) is the primary transport. SSE is a stretch goal.

**Lifecycle:** Each MCP server connection is a child subprocess owned by `MCPBridge`. The bridge holds a single `contextlib.AsyncExitStack` to manage all `stdio_client` + `ClientSession` lifetimes. Connections persist across tool calls — the session stays open until explicit disconnect or DAN shutdown.

**Configuration:** `~/.dan/mcp.json` stores server definitions. Chat commands modify this file at runtime.

## Tasks

### 1. MCP client bridge (`src/dan/mcp_bridge.py`)

Single module (not a package — consistent with `dan.publish.mcp_server` being a single file).

- [x] 1-1. `MCPBridge` class: holds `AsyncExitStack`, manages `{server_name: MCPConnection}` dict where `MCPConnection` is a dataclass of `(session: ClientSession, tools: list[ToolInfo], process_info: ...)`
- [x] 1-2. `async connect(server_name, command, args, env)` — use `StdioServerParameters` + `stdio_client()` + `ClientSession` from the `mcp` package. Enter via `exit_stack.enter_async_context()`. Call `session.list_tools()` to discover tools. Store tool schemas. Return tool list.
- [x] 1-3. `async disconnect(server_name)` — remove from connections dict. Note: individual `stdio_client` contexts can't be exited independently from an `AsyncExitStack`; use a per-server `AsyncExitStack` instead so each connection can be torn down individually.
- [x] 1-4. `async call_tool(server_name, tool_name, arguments) -> dict` — proxy to `session.call_tool()`. Parse result: `TextContent` → `{"text": ...}`, `ImageContent` → `{"image": "<base64>", "mime_type": ...}`, `EmbeddedResource` → `{"resource": ...}`. When `result.isError` is `True`, return `{"error": content_text}`. Concatenate multiple content items.
- [x] 1-5. `list_servers() -> dict[str, ServerStatus]` — return name, connected bool, tool names, last error if any
- [x] 1-6. Reconnect: wrap `call_tool` with a try/except that catches connection failures. On failure, attempt one `disconnect` + `connect` cycle. If reconnect fails, return error result (don't crash). Log warnings.
- [x] 1-7. `async shutdown()` — close all per-server exit stacks. Called during app lifespan teardown. Safe to call multiple times.
- [x] 1-8. Guard all imports from `mcp` behind try/except `ImportError` so the module is importable even without the `mcp` optional dependency. Raise clear error on `connect()` if `mcp` is not installed.

### 2. MCP config persistence (`~/.dan/mcp.json`)
- [x] 2-1. Config schema (Pydantic model `MCPServerConfig`): `command: str`, `args: list[str]`, `env: dict[str, str] | None`, `autoConnect: bool = True`, `description: str = ""`. Top-level: `MCPConfig` with `servers: dict[str, MCPServerConfig]`.
- [x] 2-2. `load_mcp_config(path?) -> MCPConfig` / `save_mcp_config(config, path?)` — default path `~/.dan/mcp.json`. Atomic write (write tmp + rename). Create `~/.dan/` dir if needed. Return empty config if file missing.
- [x] 2-3. `DAN_MCP_CONFIG` env var overrides default config path.
- [x] 2-4. Config format is intentionally compatible with Cursor/Claude Desktop (`{"mcpServers": {...}}`) so users can copy server entries between tools. The Pydantic model reads from the `mcpServers` key.

### 3. Capability registration bridge
- [x] 3-1. Add `unregister(name: str)` method to `ChatCapabilityRegistry` — `del self._tools[name]` (needed for dynamic disconnect; currently missing).
- [x] 3-2. `register_mcp_tools(registry, bridge, server_name)` — for each tool from `bridge`, create a `CapabilityHandler` closure that calls `bridge.call_tool(server_name, tool_name, args)` and wraps the result in `CapabilityResult`. Register with `build_tool_schema()`.
- [x] 3-3. `unregister_mcp_tools(registry, server_name)` — remove all tools with the `mcp:{server_name}` category from the registry.
- [x] 3-4. Tool naming: `mcp_{server}_{tool}` (e.g., `mcp_stata_run_command`). Description prefixed with `[{server}]` so the LLM knows the source. Category set to `mcp:{server_name}` for bulk operations.
- [x] 3-5. Schema translation: MCP tool `inputSchema` is already JSON Schema — wrap directly in `build_tool_schema(name, description, input_schema)`. No transformation needed.
- [x] 3-6. Mode filtering: MCP tools registered for `agent`, `conversation`, `debug` modes (not `ask`/`plan` which are read-only, not `build`/`mutate` which are workflow-editing). Per-server override via config `modes` field (optional, defaults to the above).
- [x] 3-7. Tool count guard: if a server exposes >20 tools, log a warning. Large tool counts dilute LLM selection quality. Consider grouping or filtering in a future iteration.

### 4. Workflow-engine integration (ToolRegistry)
- [x] 4-1. Register MCP tools in `ToolRegistry` (not just `ChatCapabilityRegistry`) so they're also available during workflow execution via `ToolOperator` nodes. The `ToolRegistry` takes `async fn(...)` — create a thin wrapper around `bridge.call_tool()`.
- [x] 4-2. Wire into `_build_tool_registry()` in both `chat_factory.py` and `app.py`.
- [x] 4-3. Tool IDs in `ToolRegistry` match the namespaced names (`mcp_stata_run_command`) for consistency.

### 5. Chat commands for MCP management
- [x] 5-1. `/mcp list` — show configured servers with connection status, tool count, and description
- [x] 5-2. `/mcp install <name_or_pip_pkg>` — install an MCP server:
  - Check known-server registry first (e.g., `stata` → `pip install mcp-stata`)
  - Otherwise treat argument as a pip package name
  - Run `pip install <pkg>` via `asyncio.create_subprocess_exec` (never `pip.main()` — avoid corrupting the running process's import state)
  - On success: determine command/args from package entry points or known-server map, add to config, connect, register tools
  - Report result with tool list
- [x] 5-3. `/mcp remove <name>` — disconnect, remove from config, unregister tools
- [x] 5-4. `/mcp tools [name]` — list tools. If name given, list tools for that server. If omitted, list all MCP tools across all servers.
- [x] 5-5. `_handle_mcp_command(msg)` method on `Concierge` — follows the same pattern as `_handle_build_command` and `_handle_memory_command`. Add to the dispatch chain in `process()` between `_handle_memory_command` and `handle_preference_confirmation`.

### 6. Startup integration
- [x] 6-1. Wire `MCPBridge` creation into `build_chat_services()` in `chat_factory.py` — create bridge, load config, auto-connect servers with `autoConnect: true`, register tools. Log failures without blocking startup.
- [x] 6-2. Wire into `app.py` server lifespan — same auto-connect + `bridge.shutdown()` in lifespan teardown.
- [x] 6-3. Add `mcp_bridge: Any = None` field to `CapabilityContext` dataclass.
- [x] 6-4. Pass bridge reference to `Concierge.__init__` so `/mcp` commands can access it.
- [x] 6-5. Dynamic prompt hint: `get_mcp_tool_hint(bridge)` builds hint string listing MCP tools for system prompt.

### 7. Known-server registry (convenience)
- [x] 7-1. `KNOWN_MCP_SERVERS` dict in `mcp_bridge.py`: maps short names to install + config info. Initial entries:
  - `stata` → `{"pip": "mcp-stata", "command": "mcp-stata", "description": "Stata statistical analysis — run commands, inspect data, export graphs"}`
  - (More entries added as needed — keep the map small and verified)
- [x] 7-2. `/mcp install stata` resolves via this map. Unknown names fall through to raw `pip install <name>`.
- [x] 7-3. Users can always add servers manually via `~/.dan/mcp.json` for servers not in the known map.

### 8. Tests
- [x] 8-1. Unit tests for `MCPBridge` (mock the `mcp` client classes): connect stores session, disconnect removes, call_tool proxies, shutdown cleans up, reconnect on failure, import guard without `mcp` package
- [x] 8-2. Unit tests for config persistence: load/save/add/remove, atomic write, missing file creates empty, env var override
- [x] 8-3. Unit tests for capability registration: schema translation, result formatting (text, image, error, multi-content), naming convention, unregister, category-based bulk remove
- [x] 8-4. Unit tests for chat commands: parse `/mcp list`/`install`/`remove`/`tools`, error messages for bad args, known-server resolution
- [x] 8-5. Integration test (optional, requires `mcp` dep): spawn a trivial in-process MCP server, connect via bridge, list tools, call a tool, verify round-trip

### 9. Docs
- [x] 9-1. Update `docs/architecture.md` — add MCP bridge section under "Built-in Tools"
- [x] 9-2. Update `README.md` — add MCP integration section with quick-start (`/mcp install stata`)
- [x] 9-3. Changelog entry on completion

## Dependencies

- `mcp` Python package — already in `pyproject.toml` as optional dep (`mcp = ["mcp>=1.0"]`). The same package provides both server (`FastMCP`) and client (`ClientSession`, `StdioServerParameters`, `stdio_client`) classes.
- No new architecture — plugs into existing `ChatCapabilityRegistry` + `ToolRegistry` patterns.

## Decisions

- **Single module, not package.** `src/dan/mcp_bridge.py` — consistent with `mcp_server.py` living as a single file. Expand to package only if complexity warrants it.
- **Per-server `AsyncExitStack`.** Each connection gets its own exit stack so individual servers can be disconnected without tearing down all connections. The global `MCPBridge.shutdown()` closes all of them.
- **Stdio transport only for v1.** SSE/streamable-HTTP transport is a stretch goal. Stdio covers all common MCP servers (Stata, filesystem, databases).
- **Namespaced tool names.** `mcp_{server}_{tool}` prevents collisions with built-in tools and makes the source obvious in logs/audit.
- **Both registries.** MCP tools register in `ChatCapabilityRegistry` (for chat) AND `ToolRegistry` (for workflow execution). Same tool, two access paths.
- **pip install via subprocess.** `/mcp install` runs `pip install` as a child process, never via `pip.main()`. This avoids corrupting the running Python process's import cache. The downside is that newly installed packages require the MCP server to be spawned as a fresh subprocess (which it is — that's how stdio transport works).
- **Config format mirrors Cursor/Claude Desktop.** Users can copy `mcpServers` entries between tools. The `env` field may contain secrets (API keys for some MCP servers); these are stored in the user's home directory, same security model as `.env` files.
- **Tool count concern noted but not gated.** If an MCP server exposes 30 tools, all 30 register. A future iteration may add selective tool filtering per server. For now, log a warning above 20 tools.

## Notes

- The `mcp` package v1.26.0 (latest stable, Jan 2026) provides `ClientSession`, `StdioServerParameters`, `stdio_client` in `mcp.client.stdio`. These are the same classes used by Claude Desktop and Cursor internally.
- `tmonk/mcp-stata` (PyPI: `mcp-stata`) is lightweight: command execution, data inspection, codebook, graph export, stored results (`r()` and `e()`). Requires Stata 17+ installed locally.
- `SepineTam/stata-mcp` (93 stars, v1.13.40) is more full-featured: TOML config, RAM monitoring, security validation. Either works — the bridge is server-agnostic.
- The capability registry already supports dynamic registration — `register()` can be called at any time. Adding `unregister()` is a one-line method. The LLM sees whatever tools are registered at the time of the next `get_tools(mode)` call.
- The concierge slash-command dispatch chain in `process()` is sequential: `_handle_save_command` → `_handle_build_command` → `_handle_memory_command` → (new) `_handle_mcp_command` → preference confirmation → pending follow-up → normal routing. Each returns `None` to pass through.
- 2026-03-10 closeout: server-mode startup now wires MCP auto-connect and shutdown through `app.py` using the shared `autoconnect_configured_mcp_servers()` helper, capability-registry mode filtering is now actually enforced for MCP tools, `call_tool()` now preserves embedded MCP resources instead of stringifying them away, focused concierge command coverage was added in `tests/test_concierge/test_mcp_commands.py`, the optional real round-trip test lives in `tests/test_mcp_bridge_integration.py` and skips automatically when the `mcp` dependency is unavailable, and `README.md` now documents MCP quick-start usage.
