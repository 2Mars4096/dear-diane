# 7-3: Built-in Tool Library (`dan.tools`)

**Parent:** [7-core-hardening](7-core-hardening.md)
**Status:** in-progress
**Goal:** Ship ~10 batteries-included tools as `dan.tools` with auto-registration, rich descriptions (ACI quality), and input validation — so users can build practical workflows without writing custom tool functions.

## Tasks

- [x] 1. Package structure
  - [x] 1-1. Create `src/dan/tools/__init__.py` with `get_all_tools() -> dict[str, ToolFunction]` discovery function
  - [x] 1-2. Each tool is a module in `src/dan/tools/` exporting an async function with a `TOOL_METADATA` dict (description, parameters schema, examples, category)
  - [x] 1-3. `TOOL_METADATA` schema: `{ "tool_id": str, "description": str, "parameters": dict (JSON Schema), "examples": list[dict], "category": str, "returns": str }`
  - [x] 1-4. Auto-discovery via `importlib` — `get_all_tools()` scans the package, catches `ImportError` per tool module gracefully (logs warning, skips tool), so missing optional SDKs don't break startup
  - [x] 1-5. Shared `_workspace_root()` utility — resolves the workspace/project root (configurable via `DAN_WORKSPACE_ROOT` env, defaults to `cwd()`). All file-system tools resolve paths relative to this and reject escapes via `os.path.realpath` check
- [x] 2. File tools (all use `_workspace_root()` path guardrail from 1-5)
  - [x] 2-1. `file_read` — read file contents, optional line range (`start_line`, `end_line`), max size guard (1MB default), encoding parameter. Rejects paths outside workspace root.
  - [x] 2-2. `file_write` — write/append to file, create parent dirs, optional mode (`overwrite` / `append`), return bytes written. Rejects paths outside workspace root.
  - [x] 2-3. `list_directory` — list files/dirs with optional glob pattern, recursive flag, return structured entries `[{name, path, type, size}]`. Rejects paths outside workspace root.
- [x] 3. Web tools
  - [x] 3-1. `web_search` — DuckDuckGo zero-config backend (no API key required), return `[{title, url, snippet}]`. Optional dependency: `duckduckgo-search>=6.0`.
  - [x] 3-2. `web_fetch` — fetch URL via `httpx`, respect timeout, max content length, return `{url, content, status_code, content_type}`
  - [x] 3-3. `http_request` — general HTTP client (method, url, headers, body, timeout), return `{status_code, headers, body}`, covers REST API integration needs
- [x] 4. System tools
  - [x] 4-1. `shell_command` — run subprocess with timeout (default 30s), working directory, env vars, capture stdout/stderr, return `{exit_code, stdout, stderr}`
  - [x] 4-2. Security: configurable allowlist for commands via `DAN_SHELL_ALLOW` env var (comma-separated prefixes)
  - [x] 4-3. Resource limits: max output size (1MB), process timeout kill
- [x] 5. Document tools
  - [x] 5-1. `pdf_read` — extract text from PDF via `pypdf`, optional page range, return `{text, num_pages, metadata}`. Optional dependency: `pypdf>=4.0`.
  - [x] 5-2. `text_chunk` — split text into chunks by character count or word count, configurable overlap, return `[{chunk, index, start_char, end_char}]`. Standalone utility for RAG pipelines.
- [x] 6. Utility tools
  - [x] 6-1. `json_extract` — extract value from JSON string/dict using dot-notation key (supports array indexing), return `{value, path, found}`
  - [x] 6-2. `regex_match` — apply regex pattern to text, return all matches with groups, optional replacement mode
- [x] 7. ACI quality pass (applied per-tool during implementation, not a separate phase)
  - [x] 7-1. Every tool has a 2-3 sentence description explaining purpose, common use case, and key constraints
  - [x] 7-2. Every tool has `parameters` JSON Schema with `description` on each field, `enum` where applicable, sensible `default` values
  - [x] 7-3. Every tool has 1-2 `examples` in `TOOL_METADATA` showing realistic input → output pairs
  - [x] 7-4. Input validation: poka-yoke argument checking — all file tools (`file_read`, `file_write`, `list_directory`) enforce workspace-root sandboxing via shared `_workspace_root()` guard. `shell_command` enforces allowlist.
  - [x] 7-5. Error messages are actionable: explain what went wrong AND suggest a fix
- [x] 8. Auto-registration
  - [x] 8-1. `ToolRegistry.register_builtin_tools()` method — calls `dan.tools.get_all_tools()` and registers each
  - [x] 8-2. Server `app.py` `_build_tool_registry()` calls `register_builtin_tools()` before registering custom/domain tools (custom tools can override built-in IDs)
  - [ ] 8-3. Builder DSL: `wf.tool("name", tool_id="file_read")` works out of the box when engine has built-in tools
- [x] 9. Tests
  - [x] 9-1. Unit test per tool: happy path, edge cases (empty input, missing file, timeout, invalid URL)
  - [x] 9-2. Unit: `get_all_tools()` discovers all tools
  - [x] 9-3. Unit: `TOOL_METADATA` schema validation for every tool
  - [ ] 9-4. Integration: `ToolExecutor` with built-in tool registry runs a `file_read` tool node
  - [x] 9-5. Security: `shell_command` rejects blocked commands, all file tools reject path traversal outside workspace root (`../` escape, absolute path outside root)
- [x] 10. Dependencies
  - [x] 10-1. Add `httpx>=0.27.0` to main dependencies
  - [x] 10-2. Add `pypdf>=4.0` to `[pdf]` optional dependency group
  - [x] 10-3. Add `duckduckgo-search>=6.0` to `[search]` optional group; `[all-tools]` group bundles both
- [ ] 11. Docs sync
  - [ ] 11-1. Update `architecture.md` — new `tools/` directory, tool metadata schema, auto-registration
  - [ ] 11-2. Update `llm-api-guide.md` — built-in tool IDs, usage in builder, tool metadata format
  - [ ] 11-3. Update `README.md` — list of built-in tools in features section
  - [ ] 11-4. Update `todo.md` / `changelog.md`

## Decisions

- **DuckDuckGo only for web_search** — simplified to a single zero-config backend rather than the originally planned pluggable SerpAPI/Brave. Users needing higher rate limits can register a custom `web_search` tool that overrides the built-in.
- **`pypdf` over `pdfplumber`** — chosen for smaller dependency footprint and simpler API. `pypdf>=4.0` covers the extraction needs.
- **Explicit module list in `__init__.py`** — used a static `_TOOL_MODULES` list instead of `pkgutil.walk_packages()` to keep discovery deterministic and avoid importing `_workspace.py` or other private modules.
- **`httpx` promoted to main dependency** — required by `web_fetch` and `http_request`, both core tools. Removed from `[dev]` extras.
- **dot-notation over JSONPath for `json_extract`** — simpler, no extra dependency, supports array indexing via numeric keys (`items.0.name`).
- **Tasks 8-3, 9-4, 11-* deferred** — Builder DSL integration (8-3) depends on builder changes. ToolExecutor integration test (9-4) is more of an e2e test. Docs sync (11-*) deferred per instructions.

## Notes

- **Workspace root sandboxing** — all file tools share a `_workspace_root()` guard: resolve path via `os.path.realpath`, confirm it starts with the workspace root. Rejects `../` escapes, symlink escapes, and absolute paths outside root. Configurable via `DAN_WORKSPACE_ROOT` env var, defaults to `os.getcwd()`.
- **Graceful SDK import** — `get_all_tools()` wraps each tool module import in `try/except ImportError`. Missing optional SDKs (e.g., `pypdf`, `duckduckgo-search`) log a warning and skip that tool, not crash the server. Tools with missing SDKs are simply absent from the registry.
- `web_search` should have a zero-config fallback (DuckDuckGo, no API key) so first-run works out of the box. SerpAPI/Brave are for production use with higher rate limits.
- `shell_command` is the most security-sensitive tool. Default to conservative: no network commands, no root ops, short timeout. The allowlist approach is safer than a blocklist.
- `pdf_read` + `text_chunk` together enable local RAG workflows without a vector DB: read PDF → chunk → pass chunks as context to LLM nodes.
- `http_request` subsumes the old proposal for a separate `HTTPOperator` node type. A tool is more flexible and doesn't pollute the node type system.
- The `examples` in `TOOL_METADATA` serve dual purpose: (1) documentation for humans, (2) few-shot examples that could be injected into LLM prompts when the tool is used in an agent loop.
- Paper-writing tools (`save_paper`, `compile_latex`, etc.) remain as custom tools in `app.py` — they're domain-specific, not general-purpose.
