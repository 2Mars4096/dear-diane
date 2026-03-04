# 21-5: Shareable Blocks

**Parent:** [21-author-distribute](21-author-distribute.md)
**Status:** completed
**Goal:** Package composite nodes, workflows, and markdown agent files as shareable, versioned blocks that can be exported, imported, and reused across workspaces.

## Context

DAN already supports reusable compositions:
- **Workflow-as-node** (`graphImporter.ts`) wraps a saved workflow as a composite node in another workflow
- **`import_workflow()`** (`builder/importer.py`) imports a `Graph` into a builder workflow as a composite
- **Markdown agent files** (`loader/`) define agents as `.md` files that compile to graph nodes

What's missing is **packaging** — a standardized way to bundle, version, describe, and share these artifacts so they can be installed into another workspace and appear in the palette.

## Tasks

- [x] 1. **Block package format**
  - [x] 1-1. Define `DanBlock` Pydantic model: `name`, `version` (semver string), `description`, `author`, `license`, `tags` (list), `block_type` (composite | workflow | agent_collection), `entry_point` (relative path), `dependencies` (list of other block names+versions), `input_schema`, `output_schema`
  - [x] 1-2. Package layout — a directory:
    ```
    my-block/
      dan-block.json       # DanBlock metadata
      graph.json           # for composite/workflow blocks
      agents/              # for agent_collection blocks
        agent1.md
        agent2.md
        WORKFLOW.md
      README.md            # human-readable description
    ```
  - [x] 1-3. `dan-block.json` is the manifest — required for a directory to be recognized as a block
  - [x] 1-4. Compressed format: `.dan-block.tar.gz` for distribution (tarball of the directory)

- [x] 2. **Export blocks**
  - [x] 2-1. Create `src/dan/blocks/__init__.py` package
  - [x] 2-2. `export_composite_block(graph, node_id) -> Path` — extract a composite node and its sub-graph as a block directory
  - [x] 2-3. `export_workflow_block(graph) -> Path` — export an entire workflow as a block
  - [x] 2-4. `export_agent_collection_block(agents_dir) -> Path` — bundle a directory of markdown agent files as a block
  - [x] 2-5. Auto-derive `input_schema` / `output_schema` from the workflow's entry/exit interface via `derive_workflow_interface()` in `src/dan/utils/workflow_interface.py` (shared module, also used by 21-3 publish). Whichever sub-plan is implemented first creates this module; the other reuses it.
  - [x] 2-6. Version prompt: default `0.1.0`, accept override via parameter
  - [x] 2-7. `pack_block(block_dir) -> Path` — compress block directory to `.dan-block.tar.gz`

- [x] 3. **Import blocks**
  - [x] 3-1. `import_block(source) -> InstalledBlock` — install a block from: local directory, `.dan-block.tar.gz` file, or URL (download + extract)
  - [x] 3-2. Install target: `~/.dan/blocks/{name}/{version}/` (user-level) or `{workspace}/.dan/blocks/{name}/{version}/` (workspace-level)
  - [x] 3-3. `InstalledBlock` model: `name`, `version`, `install_path`, `block_type`, `metadata` (full DanBlock)
  - [x] 3-4. Version conflict resolution: if a block with the same name but different version exists, warn and allow `--force` override. Same name+version = skip (already installed).
  - [x] 3-5. Dependency resolution: if block A depends on block B, check if B is installed; warn if missing (no auto-install for now — keep simple)

- [x] 4. **Block registry (local)**
  - [x] 4-1. `BlockRegistry` class: scans `~/.dan/blocks/` and `{workspace}/.dan/blocks/` for installed blocks
  - [x] 4-2. `list_blocks() -> list[InstalledBlock]` — enumerate all installed blocks with metadata
  - [x] 4-3. `get_block(name, version=None) -> InstalledBlock` — look up by name (latest version if not specified)
  - [x] 4-4. `remove_block(name, version) -> bool` — uninstall by deleting the directory
  - [x] 4-5. Registry index file: `~/.dan/blocks/_index.json` — cached inventory, rebuilt on scan

- [x] 5. **Integration with engine and builder**
  - [x] 5-1. `BlockResolver` + `resolve_node_block()` — when a node's metadata has `block_name`/`block_version`, load the block's graph
  - [x] 5-2. `load_block_as_graph(block_ref, registry)` — standalone convenience for one-shot block→Graph resolution
  - [x] 5-3. Loader integration: markdown workflow files can reference blocks in flow lines: `-> [my-block@0.1.0] ->` — flow parser accepts `name@version` block refs, compiler resolves via `BlockRegistry` and inlines as `CompositeNode`
  - [x] 5-4. Node metadata: block-sourced nodes carry `metadata.block_name` and `metadata.block_version` for provenance

- [x] 6. **Integration with visual editor**
  - [x] 6-1. New palette section: "Installed Blocks" — lists blocks from `BlockRegistry` with name, description, version
  - [x] 6-2. Drag block from palette → creates a composite node backed by the block's graph
  - [x] 6-3. Block node visual indicator: small "block" badge on the node (like the existing loop/composite badges)
  - [x] 6-4. Context menu: "Export as Block..." → opens dialog with name/version/description fields, calls `export_composite_block()`
  - [x] 6-5. Import dialog: "Import Block..." → file picker for `.dan-block.tar.gz` or directory, calls `import_block()`
  - [x] 6-6. API endpoints: `GET /api/blocks` (list), `GET /api/blocks/{name}` (info), `POST /api/blocks/import` (install from path/URL), `POST /api/blocks/export/{graph_id}` (export workflow), `POST /api/blocks/export/{graph_id}/{node_id}` (export composite), `DELETE /api/blocks/{name}/{version}` (uninstall)

- [x] 7. **CLI command: `dan-blocks`**
  - [x] 7-1. Create `src/dan/cli/blocks.py` with `main()` entry point
  - [x] 7-2. `dan-blocks list` — list installed blocks (table: name, version, type, description)
  - [x] 7-3. `dan-blocks install <path_or_url>` — import a block
  - [x] 7-4. `dan-blocks export <workflow_path> [--node <node_id>]` — export workflow or composite node as block
  - [x] 7-5. `dan-blocks remove <name> [--version <ver>]` — uninstall a block
  - [x] 7-6. `dan-blocks pack <block_dir>` — compress to `.dan-block.tar.gz`
  - [x] 7-7. `dan-blocks info <name>` — show block metadata, schemas, README
  - [x] 7-8. Entry point (`dan-blocks`) already wired in 21-1 — verify it resolves correctly

- [x] 8. **Tests**
  - [x] 8-1. Unit tests for `DanBlock` model validation (required fields, semver format, block_type enum)
  - [x] 8-2. Unit tests for export — composite node extraction, workflow export, agent collection export
  - [x] 8-3. Unit tests for import — directory install, tarball install, version conflict handling
  - [x] 8-4. Unit tests for `BlockRegistry` — scan, list, get, remove, index rebuild
  - [x] 8-5. Integration test: export workflow → import to fresh registry → scan → load graph; plus tarball export/import, resolver caching, missing block error (`tests/test_blocks/test_integration.py` — 6 tests)
  - [x] 8-6. Unit tests for `dan-blocks` CLI argument parsing

## Decisions

- **Local-only registry for now.** Remote registry (GitHub-based, npm-style) is a natural extension but deferred to backlog. The `import_block(url)` path downloads a tarball from any URL, which covers GitHub release assets.
- **Blocks are read-only once installed.** Editing an installed block requires exporting, modifying, and re-installing at a new version. This prevents drift between the block source and its installed copy.
- **No auto-dependency resolution.** Block dependencies are declared and checked, but not auto-installed. This keeps the system simple and predictable. Users install dependencies manually.
- **Block format is filesystem-based**, not a custom binary format. A block is a directory with a manifest — easy to inspect, edit, and version-control.
- **Workspace-level blocks override user-level.** If the same block name+version exists in both `{workspace}/.dan/blocks/` and `~/.dan/blocks/`, the workspace copy wins.
- **Block nesting is allowed but single-level is recommended.** A block's graph may reference other installed blocks. The engine resolves block references at load time (look up block → load graph → inline as composite). Circular block references are rejected. Deep nesting (block within block within block) works but is discouraged — document single-level as the recommended pattern.
- **`input_schema`/`output_schema` are stored in the manifest** for quick access (palette preview, `dan-blocks info`) without loading the full graph. They are derived from the graph at export time and are the source of truth for compatibility checking during import.

## Notes

- Shareable blocks are the distribution primitive. Combined with `dan-publish` (21-3), a block can be both imported as a reusable node AND published as a callable service.
- The block format is intentionally simple — no build step, no compilation, no resolution graph. It's "copy this directory and it works."
- Future: a public block registry (like npm for DAN blocks) would need auth, namespacing, search, and popularity metrics. This is explicitly out of scope for Phase 12 but the format is designed to be registry-compatible.
- Existing `import_workflow()` in the builder already does the hard work of namespace isolation and port mapping. Blocks add packaging metadata on top.
