# 11-2: File Audit — Findings

**Plan:** [11-2-file-audit](11-2-file-audit.md)  
**Date:** 2026-03-01

---

## 1. Module Boundaries

### 1-1. Package Separation

| Package | Purpose | Dependencies (dan.*) | Status |
|---------|---------|----------------------|--------|
| `models/` | Formal type system (ports, nodes, edges, graph, control flow) | — (foundation) | ✅ Clear |
| `engine/` | Async execution engine (scheduler, state, executor, checkpoint, events) | models | ✅ Clear |
| `executors/` | Built-in node executors (llm, tool, code, rag, control_flow, validator, input) | models, engine, sandbox, rag | ✅ Clear |
| `builder/` | Fluent Python DSL (workflow, refs, compiler, decompiler, importer) | models, validation | ✅ Clear |
| `loader/` | Markdown authoring (parser, flow_parser, compiler, decompiler, types) | models | ✅ Clear |
| `server/` | FastAPI backend (app, run_manager, chat_manager, graph_store, graph_mutator, exec, etc.) | models, engine, builder, loader, executors, validation | ✅ Clear |
| `validation/` | Graph well-formedness, schema, boundaries | models | ✅ Clear |
| `providers/` | LLM provider abstraction | — | ✅ Clear |
| `tools/` | Built-in tool library | — (internal _workspace) | ✅ Clear |
| `sandbox/` | Subprocess sandbox | — | ✅ Clear |
| `rag/` | RAG subsystem (indexer, stores) | tools.text_chunk | ✅ Clear |
| `migration/` | Legacy gate migration | — | ✅ Clear |
| `registry.py` | NodeTypeRegistry (top-level) | models | ✅ Clear |

**Findings:**
- All six primary packages (`models`, `engine`, `executors`, `builder`, `loader`, `server`) are clearly separated with unidirectional dependencies.
- Dependency flow: `models` → `engine` → `executors`; `models` → `builder`, `loader`, `validation`; all → `server` for orchestration.

### 1-2. Circular Imports

**Result:** No circular imports detected.

- Import trace: `python -c "import dan; ..."` — all submodules load successfully.
- AST-based cycle detection across `src/dan/**/*.py` — no cycles found.
- Import graph is acyclic: models (leaf) → engine, validation, builder, loader → executors, server.

---

## 2. Directory Layout

### 2-1. Consistency

| Area | Pattern | Status |
|------|---------|--------|
| `rag/stores/` | Subpackage with `__init__.py`, memory/faiss/chroma implementations | ✅ Consistent |
| `providers/` | Top-level package, registry + provider implementations | ✅ Consistent |
| `tools/` | Top-level package, `get_all_tools()` + individual tool modules | ✅ Consistent |
| `sandbox/` | Top-level package (Phase 6), adapters + runner | ✅ Correct place — operational guardrails |
| `executors/` | Top-level package, one executor per node type | ✅ Correct place — engine extension point |

**Findings:**
- `rag/stores/`, `providers/`, `tools/` follow the same pattern: `__init__.py` with protocol/registry + implementation modules.
- `sandbox` and `executors` are appropriately placed: sandbox for subprocess isolation, executors for node execution.

### 2-2. Architecture Doc Alignment

| Item | docs/architecture.md | Actual | Status |
|------|------------------------|--------|--------|
| server/ | Lists exec.py, graph_mutator.py, etc. | + `layout.py`, `mutation_metrics.py` | ⚠️ Doc missing 2 files |
| loader/ | parser.py, flow_parser.py | Both exist | ✅ |
| builder/ | importer.py | Exists | ✅ |
| executors/ | input.py | Exists | ✅ |
| rag/stores/ | memory, faiss_store, chroma_store | All exist | ✅ |

**Refactor candidates:**
- Update `docs/architecture.md` to include `server/layout.py` and `server/mutation_metrics.py` in the directory listing.

---

## 3. Import Structure

### 3-1. Public API

**`from dan import ...`** (top-level `dan/__init__.py`):
- Exposes: `InputPort`, `OutputPort`, context types, node types, edge types, `Graph`, `GraphMetadata`, `Node`, `Edge`, `NodeTypeRegistry`.
- Does **not** expose: `Engine`, `EngineConfig`, `workflow`, `load`, `compile_workflow`, `GraphMutator`, etc.

**Subpackage public APIs:**
- `dan.engine`: `Engine`, `EngineConfig`, `RunResult`, `ExecutionContext`, etc.
- `dan.builder`: `workflow`, `WorkflowBuilder`, `decompile`, `NodeRef`, `PortRef`, `namespace_graph`, `derive_ports`
- `dan.loader`: `load`, `load_agents`, `compile_workflow`
- `dan.validation`: (no explicit `__all__` in package; used internally)
- `dan.rag`: `EmbeddingProvider`, `EmbeddingRegistry`, stores
- `dan.providers`: `CompletionResult`, `StreamChunk`, `ProviderConfig`
- `dan.tools`: `get_all_tools()`
- `dan.sandbox`: `SandboxConfig`, `SandboxResult`

**Findings:**
- Top-level `dan` is model-centric (types + registry). Engine, builder, loader are accessed via subpackages.
- Public vs internal is implicit: `dan.models.*` is public for type construction; `dan.engine.*` internals (e.g. `scheduler`, `state`) are not re-exported at package level but are importable.

### 3-2. pyproject.toml

| Item | Value |
|------|-------|
| Package name | `dan` |
| Entry point | `dan-serve = dan.server.__main__:main` |
| Build | `hatchling`, packages = `["src/dan"]` |
| Optional deps | pdf, search, anthropic, google, faiss, chroma, embeddings, jsonschema, quant, all, dev |
| pytest | testpaths = ["tests"], pythonpath = ["src"] |

**Findings:**
- Single CLI entry point. No `dan` or `dan-build` console scripts for builder/loader.
- Optional dependencies align with architecture (providers, RAG stores, tools).

---

## 4. Separation of Concerns

### 4-1. graph_mutator

| Aspect | Current | Assessment |
|--------|---------|------------|
| Location | `dan.server.graph_mutator` | Server-specific (used by chat_manager, app) |
| Dependencies | `dan.models.graph`, `dan.validation.graph` | No server/HTTP deps |
| Used by | `app.py` (apply-mutation), `chat_manager.py` | Server only |

**Refactor candidate:** Move to `dan.mutator` (or `dan.graph_mutator`).
- **Pros:** Pure graph transformation; could be reused by CLI, tests, or future tooling without server.
- **Cons:** Chat manager and app would need to import from new location; low immediate benefit if only server uses it.

### 4-2. exec.py

| Aspect | Current | Assessment |
|--------|---------|------------|
| Location | `dan.server.exec` | Server package |
| Dependencies | `dan.executors.code._ALLOWED_BUILTINS` | Executor internals |
| Used by | `run_python` tool, `run_strategy_script` (vibe_research), server lifespan | Server + tools |

**Refactor candidate:** Move to `dan.executors.python_exec` or `dan.tools._python_exec`.
- **Pros:** Shared Python execution is a cross-cutting concern; tools and server both need it.
- **Cons:** `_ALLOWED_BUILTINS` is in `executors.code`; would need to extract to shared module to avoid server→executors dependency inversion.

### 4-3. Other Candidates

| Module | Concern | Notes |
|--------|---------|-------|
| `server/layout.py` | Topological layout for graphs | Pure graph logic; could live in `dan.layout` or `dan.validation` |
| `server/mutation_metrics.py` | Mutation quality metrics | Server/chat-specific; OK in server |
| `server/scoped_run.py` | Scoped run builder | Tightly coupled to run_manager; OK in server |
| `loader/diagnostics.py` | CompileResult, Diagnostic | Loader-internal; OK |

---

## 5. Editor Internal Structure

### 5-1. Module Boundaries

| Layer | Location | Responsibility |
|-------|----------|----------------|
| **Store** | `store/useGraphStore.ts` | Zustand store: graph, selection, run state, layers, history, toasts, port ops |
| **Components** | `components/*.tsx` | UI: DanNode, GraphCanvas, ConfigPanel, ChatPanel, etc. |
| **Lib** | `lib/*.ts` | Adapters (graphAdapter, api), utilities (layout, graphDiff, connectionValidation, etc.) |
| **Hooks** | `hooks/useKeyboardShortcuts.ts` | Keyboard shortcuts (save, undo/redo, copy/paste) |
| **Types** | `types/graph.ts`, `types/chat.ts` | TypeScript types mirroring dan_graph_v1 |

**Findings:**
- Clear separation: components import from store, lib, types; lib has no store dependency; hooks are minimal.
- Store is the single source of truth; components are presentational + store subscribers.

### 5-2. Shared vs Editor-Specific

| File | Shared? | Notes |
|------|---------|-------|
| `lib/graphAdapter.ts` | Editor-specific | DAN ↔ React Flow conversion |
| `lib/api.ts` | Editor-specific | HTTP/WebSocket client for backend |
| `lib/layout.ts` | Editor-specific | dagre auto-layout |
| `lib/nodeIcons.tsx` | Editor-specific | SVG icons |
| `lib/graphDiff.ts` | Could be shared | Pure diff logic; no React/store deps |
| `types/graph.ts` | Contract | Mirrors dan_graph_v1; shared contract with backend |

**Findings:**
- No blur: `lib/` is editor-specific. `types/` defines the shared contract.
- `lib/portOrdering.ts`, `lib/mentionParser.ts`, `lib/graphImporter.ts` are editor utilities.

### 5-3. Deprecated Components

- `RunPanel.tsx`, `GraphSwitcher.tsx` — marked deprecated in architecture; merged into `EditorToolbar.tsx`. Files still exist but are not imported in `App.tsx`. **Refactor:** Remove or move to `_deprecated/` if kept for reference.

---

## 6. Test Layout

### 6-1. Mirror Structure

| src/dan Package | tests/ | Status |
|-----------------|-------|--------|
| models/ | test_models/ | ✅ Mirrored |
| validation/ | test_validation/ | ✅ Mirrored |
| migration/ | test_migration/ | ✅ Mirrored |
| engine/ | test_engine/ | ✅ Mirrored |
| builder/ | test_builder/ | ✅ Mirrored |
| loader/ | test_loader/ | ✅ Mirrored |
| server/ | test_server/ | ✅ Mirrored |
| providers/ | test_providers/ | ✅ Mirrored |
| tools/ | test_tools/ | ✅ Mirrored |
| rag/ | — | ⚠️ No test_rag/; RAG tests in test_engine/test_rag.py |
| sandbox/ | — | ⚠️ No test_sandbox/; sandbox tests in test_engine/test_sandbox.py |
| executors/ | — | ⚠️ No test_executors/; executor tests in test_engine/ |

**Findings:**
- Core packages (models, validation, migration, engine, builder, loader, server, providers, tools) have dedicated test directories.
- RAG, sandbox, executors are tested via `test_engine/` (integration-style). This is acceptable: RAG executor and sandbox are exercised through the engine.

### 6-2. Orphaned Tests

- None identified. All test files map to a src package or example.

---

## 7. Examples

### 7-1. Organization

| Directory | Contents | Pattern |
|-----------|----------|---------|
| `examples/` | simple_chain.py, paper_writing.py, fan_out_fan_in.py, review_revise.py, rag_qa.py, react_agent.py, equity_research.py | Top-level runnable scripts |
| `examples/paper_writing_md/` | workflow.md + agent .md files | Markdown loader fixture |
| `examples/vibe_research_md/` | workflow_multi_dept.md, many agent .md, quant_lib/, run_multi_dept.py, output/ | Full workflow + quant lib + outputs |

**Findings:**
- Top-level examples are flat; `paper_writing_md` and `vibe_research_md` are self-contained subdirs.
- `vibe_research_md` has substantial scope: quant_lib (load_compustat, backtest, etc.), output/ (results, scripts), and many .md agents. It is a full application, not a minimal example.

### 7-2. Dependencies

| Example | Imports |
|---------|---------|
| simple_chain.py | dan.builder, dan.engine |
| rag_qa.py | dan.builder, dan.engine, dan.tools |
| test_templates.py | sys.path += examples; imports simple_chain, rag_qa, etc. |

**Findings:**
- Examples depend on `dan` only. No examples depend on editor or server.
- `test_templates.py` adds `examples/` to `sys.path` and imports `build_*` functions. Examples are used as modules for testing — acceptable but couples test layout to example layout.

---

## Refactor Candidates Summary

| Priority | Refactor | Effort | Impact |
|----------|----------|--------|--------|
| Low | Move `graph_mutator` to `dan.mutator` | Medium | Enables reuse outside server |
| Low | Move `exec.py` to shared layer (e.g. `dan.executors.python_exec`) | Medium | Cleaner separation |
| Low | Add `server/layout.py`, `server/mutation_metrics.py` to architecture.md | Trivial | Doc accuracy |
| Low | Remove or relocate deprecated `RunPanel.tsx`, `GraphSwitcher.tsx` | Trivial | Cleanup |
| Optional | Create `test_rag/` for rag/stores unit tests | Low | Better mirroring |
| Optional | Extract `server/layout.py` to `dan.layout` | Low | Reuse outside server |

---

## Checklist (from plan)

- [x] 1-1. Package separation — Documented
- [x] 1-2. Circular imports — None found
- [x] 2-1. Consistency — rag/providers/tools/sandbox/executors assessed
- [x] 2-2. Architecture alignment — 2 files missing from doc
- [x] 3-1. Public API — Documented
- [x] 3-2. pyproject.toml — Documented
- [x] 4-1. graph_mutator — Refactor candidate: dan.mutator
- [x] 4-2. exec.py — Refactor candidate: shared layer
- [x] 4-3. Other candidates — layout.py, mutation_metrics noted
- [x] 5-1. Editor boundaries — Store/components/lib/hooks clear
- [x] 5-2. Shared vs editor-specific — No blur
- [x] 6-1. Test mirroring — Documented; RAG/sandbox/executors in test_engine
- [x] 6-2. Orphaned tests — None
- [x] 7-1. Examples organization — Documented
- [x] 7-2. Examples dependencies — dan-only; clear
