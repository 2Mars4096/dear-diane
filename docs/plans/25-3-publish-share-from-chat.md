# 25-3: Publish & Share from Chat

**Parent:** [25-chat-control-plane](25-chat-control-plane.md)
**Status:** completed
**Goal:** Make publish, share, and export capabilities accessible from chat via tool calls — users can "Publish this as MCP", "Export as block", "Share via Telegram" without leaving the chat session.

**Depends on:** [25-1-capability-router](25-1-capability-router.md) — publish/share tools register in `ChatCapabilityRegistry` and use the same dispatcher pattern.

## Design

### Tool Schemas

| Tool | Purpose | Key params |
|------|---------|------------|
| `publish_workflow` | Publish current workflow to the publish registry (MCP/HTTP endpoint) | `graph_id?` (defaults to active), `name_override?`, `api_key?`, `rate_limit?` |
| `unpublish_workflow` | Remove workflow from publish registry | `graph_id?` (defaults to active) |
| `export_workflow` | Export as block, markdown, or Python | `graph_id?` (defaults to active), `format` (block \| markdown \| python), `name?`, `version?` |
| `share_workflow` | Generate shareable config/artifact (MCP config snippet, API docs, OpenAPI spec) | `graph_id?` (defaults to active), `format` (mcp_config \| api_docs \| openapi) |
| `list_published` | Show what's currently published | — |
| `get_publish_status` | Check whether a graph is published and return saved config | `graph_id?` (defaults to active) |
| `import_block` | Install a block from path/URL | `source`, `scope?`, `workspace?`, `force?` |
| `list_blocks` | Show installed blocks | `scope?` |

### Example Interactions

- **"Publish this as MCP"** → `publish_workflow(graph_id)` → then `share_workflow(graph_id, format="mcp_config")` returns inline JSON snippet for `.cursor/mcp.json`
- **"Export as block"** → `export_workflow(graph_id, format="block", name="my-workflow", version="0.1.0")` → returns path or download URL
- **"Share via Telegram"** → `share_workflow(graph_id, format="mcp_config")` → returns a config or docs artifact the user can paste/send via Telegram; direct outbound sending is out of scope for this phase
- **"Is this already published?"** → `get_publish_status(graph_id)` → returns published flag, workflow slug, and persisted publish config
- **"What's published?"** → `list_published()` → returns list of workflow_id, name, description

### Implementation Pattern

Thin wrappers that call existing APIs:

- `publish_workflow` → `PublishRegistry.register(graph, name_override?, api_key?, rate_limit?)`
- `unpublish_workflow` → `PublishRegistry.unregister()`
- `export_workflow` → `export_workflow_block()`, `export_composite_block()` from `blocks/export.py`, or graph export endpoints
- `share_workflow` → `generate_mcp_config()`, `generate_api_docs()`, `generate_openapi_spec()` from `publish/portal.py`
- `list_published` → `PublishRegistry.list_all()`
- `get_publish_status` → existing `publish_status` endpoint logic (`registry.is_published(slug)` + `graphs/{graph_id}.publish.json`)

## Tasks

- [x] 1. Define publish/share tool schemas
  - [x] 1-1. Add `publish_workflow` schema: `graph_id?` (default active workflow), `name_override?`, `api_key?`, `rate_limit?`
  - [x] 1-2. Add `unpublish_workflow` schema: `graph_id?` (default active workflow)
  - [x] 1-3. Add `export_workflow` schema: `graph_id?` (default active workflow), `format` (block \| markdown \| python), `name?`, `version?`, `node_id?` (for composite export)
  - [x] 1-4. Add `share_workflow` schema: `graph_id?` (default active workflow), `format` (mcp_config \| api_docs \| openapi), `base_url?`
  - [x] 1-5. Add `list_published` schema: no params
  - [x] 1-6. Add `get_publish_status` schema: `graph_id?` (default active workflow)
  - [x] 1-7. Add `import_block` schema: `source`, `scope?`, `workspace?`, `force?`
  - [x] 1-8. Add `list_blocks` schema: optional `scope`
  - [x] 1-9. Register schemas in `ChatCapabilityRegistry` (from 25-1)

- [x] 2. Implement tool handlers (thin wrappers)
  - [x] 2-1. `publish_workflow`: resolve graph from `graph_id` via `GraphStore.get_graph()`, validate to `Graph`, call `PublishRegistry.register()`, and persist `graphs/{graph_id}.publish.json` by mirroring the existing `app.py` publish endpoint logic
  - [x] 2-2. `unpublish_workflow`: derive slug from graph interface (same as existing endpoint), then call `PublishRegistry.unregister(slug)` and remove `.publish.json`
  - [x] 2-3. `export_workflow`: branch on format — block → `export_workflow_block()` or `export_composite_block()`, markdown/python → call graph export logic (or internal helpers)
  - [x] 2-4. `share_workflow`: call `generate_mcp_config()`, `generate_api_docs()`, or `generate_openapi_spec()` from `publish/portal.py`, return result inline
  - [x] 2-5. `list_published`: call `PublishRegistry.list_all()`, format as readable list
  - [x] 2-6. `get_publish_status`: mirror existing publish-status logic (derive slug, check `registry.is_published(slug)`, include persisted `.publish.json` config if present)
  - [x] 2-7. `import_block`: call `blocks.importer.import_block()`, return installed block metadata
  - [x] 2-8. `list_blocks`: call `BlockRegistry.scan()` and `BlockRegistry.list_blocks()`, format as readable list
  - [x] 2-9. Wire handlers through `ChatCapabilityRegistry` / `capability_handlers.py` from 25-1

- [x] 3. MCP config generation in chat
  - [x] 3-1. Ensure `share_workflow(..., format="mcp_config")` returns JSON suitable for `.cursor/mcp.json` or `claude_desktop_config.json`
  - [x] 3-2. Include workflow path in config (from `generate_mcp_config(workflow_path, name=...)`)
  - [x] 3-3. Render config snippet in chat response (code block with copy hint)

- [x] 4. Block export/import from chat
  - [x] 4-1. Block export: `export_workflow` with `format="block"` → `export_workflow_block()` from `blocks/export.py`; for composite, `export_composite_block(graph, node_id, ...)`
  - [x] 4-2. Return block path or temp URL; for server mode, consider `BackgroundTask` + signed URL pattern (or return path if local)
  - [x] 4-3. Block import: add `import_block` tool — `source` (path/URL), `scope` (user \| workspace), `force?`; call `blocks.importer.import_block()`
  - [x] 4-4. List blocks: add `list_blocks` tool → `BlockRegistry.list_blocks()` (or `scan()` + format)

- [x] 5. Cross-surface rendering
  - [x] 5-1. Config snippets: return as structured content (e.g. `{"snippet": "...", "language": "json"}`) so CLI/editor/Telegram can render appropriately
  - [x] 5-2. File paths vs inline: for `export_workflow` block — return path when local; for server, return download URL or inline tarball base64 (prefer URL for large outputs)
  - [x] 5-3. Telegram/WhatsApp: long configs may need truncation or "see full config at …" link; document in Notes

- [x] 6. Tests
  - [x] 6-1. Unit tests for each tool handler (mock `PublishRegistry`, `GraphStore`, `BlockRegistry`)
  - [x] 6-2. Integration test: chat message "Publish this workflow" → tool call → verify registry state
  - [x] 6-3. Integration test: "Export as block" → verify block dir created
  - [x] 6-4. Integration test: "Share as MCP config" → verify valid JSON returned

## Dependencies

- **25-1 (Capability Router):** Assumes `ChatCapabilityRegistry` and tool dispatcher exist. Publish/share tools are registered in the registry and handlers are invoked via the same dispatch path as other capability tools.

## Files to Touch

| File | Changes |
|------|---------|
| `src/dan/server/capability_registry.py` | Register publish/share tool schemas (from 25-1) |
| `src/dan/server/capability_handlers.py` | Add handlers for publish_workflow, unpublish_workflow, export_workflow, share_workflow, list_published, get_publish_status, import_block, list_blocks |
| `src/dan/server/chat_manager.py` | Wire new tools via registry (if not auto-discovered) |
| `src/dan/server/app.py` | Expose `_get_publish_registry()`, `_graph_store`, `_block_registry` to capability handlers (or pass via ChatManager init) |
| `src/dan/server/graph_store.py` | Already has `get_graph()` — no change if chat has access |
| `src/dan/publish/portal.py` | Use as-is: `generate_mcp_config()`, `generate_api_docs()`, `generate_openapi_spec()` |
| `src/dan/publish/http_server.py` | Use `PublishRegistry` as-is |
| `src/dan/blocks/export.py` | Use `export_workflow_block()`, `export_composite_block()` as-is |
| `src/dan/blocks/importer.py` | Use `import_block()` as-is |
| `tests/test_server/test_chat_publish_share.py` | Create — integration tests for publish/share chat tools |
| `tests/test_server/test_capability_publish_share.py` | Create — unit tests for publish/share handlers |
| `docs/cli.md` | Update — document chat-driven publish/export flows if exposed in `dan-chat` |

## Decisions

- (To be filled during execution)

## Notes

- ChatManager receives `workflow_id` / `graph_id` from session context; tools use that as default when user says "publish this" or "export this".
- The messaging adapters (Telegram, WhatsApp) render HumanNode I/O during runs. "Share via Telegram" in this phase means generating a shareable config or docs artifact, not automatically sending the workflow through the adapter.
- `generate_mcp_config()` expects `workflow_path` (file path). In server mode, graph may live in `graphs/{graph_id}.json`; use that path when generating config.
- `unpublish` must use the same slug-derivation logic as the existing endpoint (derive interface name → slug), not blindly treat `graph_id` as slug.
- Block export creates temp dirs; server must clean up via `BackgroundTask` (see `_cleanup_export_dir` in app.py). Chat tool should return download URL or path, not hold temp dir open.
