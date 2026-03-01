# 11-3 Cross-Cutting Audit — Findings

**Plan:** [11-3-cross-cutting-audit](11-3-cross-cutting-audit.md)  
**Date:** 2026-03-01  
**Status:** Complete

---

## 1. Data flow and serialization paths

### 1-1. End-to-end trace

| Stage | Path | Primary files |
|-------|------|----------------|
| **JSON file** | `graphs/*.json` → GraphStore | `server/graph_store.py` |
| **Graph model** | `Graph.model_validate(data)` or `Graph.model_dump(mode="json")` | `models/graph.py` |
| **Builder** | `WorkflowBuilder` → `compile_graph()` → `Graph` | `builder/builder.py`, `builder/compiler.py` |
| **Loader** | Markdown → `parse_workflow_file()` → `compile_workflow()` → `Graph` | `loader/parser.py`, `loader/compiler.py` |
| **Mutator** | `MutationPlan` → `GraphMutator.apply()` → raw dict | `server/graph_mutator.py` |
| **Editor** | `graphAdapter.ts`: `danGraphToReactFlow` / `reactFlowToDanGraph` | `editor/src/lib/graphAdapter.ts` |
| **API** | REST: `get_graph`, `update_graph`, `apply-mutation`; WebSocket: run events, chat events | `server/app.py` |

**Flow summary:**
- **Load:** JSON file → dict → `Graph.model_validate()` (optional) → editor receives dict
- **Save:** Editor sends dict via `PUT /api/graphs/{id}` or mutation response
- **Builder:** Python DSL → `Graph` → `model_dump()` → JSON
- **Loader:** Markdown → `Graph` → `model_dump()` → JSON
- **Gate migration:** Applied on GET when `DAN_GATE_MIGRATION_ENABLED=true` before returning to client

### 1-2. Lossy conversions

| Location | Issue | Severity |
|----------|-------|----------|
| **graphAdapter `reactFlowEdgeToDan`** | When `rfEdge.data?.danEdge` is absent, creates new edge with `edge_type: "data"`, `ui: {}`, `metadata: {}` — loses `edge_type` (control/context), `spread`, `context_key`, `mode`, `condition` | **Medium** |
| **graphAdapter `reactFlowToDanGraph`** | Filters out `loopGroup` nodes (visual-only) — correct; no data loss | Low |
| **graphAdapter `stripLoopGroups`** | Removes synthetic edges and hidden flags — correct | Low |
| **Loader** | Emits only `DataEdge`; no `ControlEdge` or `ContextEdge` in flow compilation | **Medium** (Loader gap) |
| **Loader context** | `SharedContextDeclaration` from `ContextSpec` omits `json_schema` (only key, description) | Low |
| **Builder → Loader** | Builder supports `if_else` and `while_loop` node types; Loader compiles to `GateNode` only — round-trip via Loader loses legacy node types (intentional) | Low |

**Recommendations:**
- Ensure `reactFlowEdgeToDan` always preserves `danEdge` on edges; avoid creating edges without `danEdge` in the editor flow.
- Document that new edges created in the editor (e.g. drag-drop) must initialize `danEdge` with full edge metadata.

### 1-3. Dict vs model

| Location | Usage | Notes |
|----------|-------|------|
| **GraphStore** | Stores/returns raw dict | By design — allows migration before validation |
| **GraphMutator** | Operates on `dict[str, Any]` | Intentional — applies to graph dict before Pydantic parse |
| **apply-mutation** | `MutationPlan.model_validate(req.mutation_plan)`; result is dict | Plan is validated; new_graph is dict until optional `Graph.model_validate()` |
| **gate_migration** | Operates on dict | Correct — runs before model parse |
| **Server endpoints** | Mix: `load_as_model()` returns `Graph`; `get_graph` returns dict | Consistent with migration-on-load pattern |

**Finding:** Dicts are used intentionally at API and storage boundaries; Pydantic models used for validation and engine execution. No inappropriate raw-dict usage in core logic.

---

## 2. Naming and casing conventions

### 2-1. Python vs JSON vs TypeScript

| Surface | Convention | Examples |
|---------|------------|----------|
| **Python models** | `snake_case` | `source_node_id`, `target_port`, `body_graph` |
| **JSON (dan_graph_v1)** | `snake_case` | Same as Python — Pydantic `model_dump()` preserves |
| **TypeScript (graph.ts)** | `snake_case` | `source_node_id`, `target_node_id` — mirrors Python |
| **graphAdapter** | `snake_case` | DanNode, DanEdge use snake_case throughout |

**Convention:** **snake_case everywhere** for graph schema. No camelCase in graph JSON or TS types.

### 2-2. API payloads

| Endpoint | Request/Response | Convention |
|----------|------------------|------------|
| `GET/PUT /api/graphs/{id}` | Graph dict | snake_case |
| `POST apply-mutation` | `MutationPlan` (op, node_type, source_id, etc.) | snake_case |
| `POST /api/runs` | `RunRequest` (graph_id, inputs) | snake_case |
| WebSocket events | `EngineEvent`, `ChatStreamEvent` | snake_case |
| Chat messages | `toBackendMessage`/`fromBackendMessage` do camelCase↔snake_case for chat-specific fields | Mixed (chat only) |

**Finding:** REST/WebSocket graph and run payloads use snake_case. Chat message format uses camelCase↔snake_case conversion (per changelog).

### 2-3. graphAdapter naming

**Lightweight check (11-1 findings not available):**
- `graphAdapter` maps `source_node_id` ↔ `rfEdge.source`, `target_node_id` ↔ `rfEdge.target`
- Port handles: `port:${portName}` — consistent
- `reactFlowNodeToDan` spreads `data` into `DanNode` — preserves all node fields
- `createDefaultNode` uses `gate_if_else` / `gate_while` as palette types but emits `node_type: "gate"` with `gate_mode` — correct

**Recommendation:** Full naming consistency audit should reference 11-1 task 6-2 findings when available.

---

## 3. Validation and error contracts

### 3-1. When validation runs

| Phase | Location | Trigger |
|-------|----------|---------|
| **Design-time (builder)** | `compile_graph()` → `validate_graph()` | On `build()`; raises `BuildError` on fatal errors |
| **Design-time (loader)** | `compile_workflow()` → `validate_graph()` | After graph assembly; diagnostics include validation messages |
| **Design-time (mutator)** | `apply-mutation` with `DAN_STRICT_MUTATION_VALIDATION=true` | After apply; `Graph.model_validate()` + `validate_graph()` |
| **Runtime (engine)** | Scheduler / executors | No pre-run validation; relies on design-time checks |
| **On-demand** | `POST /api/graphs/{id}/validate` | Explicit validation endpoint |

### 3-2. Invalid graph semantics

| Scenario | Behavior |
|----------|----------|
| **Builder** | Fail-fast: `BuildError` with error list; no partial graph |
| **Loader** | Fail-fast when `strict=True`; otherwise returns `graph=None` if any error in diagnostics |
| **Mutator** | `all_or_nothing`: rollback on any op error; `partial`: apply successful ops, report errors |
| **Engine** | No pre-validation; runtime errors (e.g. missing port data) surface as `NodeStatus.FAILED` and events |

### 3-3. Centralization

| Check | Location | Duplication |
|-------|----------|-------------|
| Entry/exit points | `validation/graph.py` | Single source |
| Reachability | `validation/graph.py` | Single source |
| Required ports | `validation/graph.py` | Single source |
| Sub-graph refs | `validation/graph.py` | Single source |
| Edge endpoints | `validation/graph.py` | Single source |
| Data-edge schemas | `validation/graph.py` + `schema.py` | Single source |
| Context declarations | `validation/graph.py` | Single source |
| Context edge permissions | `validation/graph.py` | Single source |
| Data cycles / gate cycles | `validation/graph.py` | Single source |
| Warning patterns | `_VALIDATION_WARNING_PATTERNS` in builder, loader, mutator | **Duplicated** (3 copies) |

**Recommendation:** Extract `_VALIDATION_WARNING_PATTERNS` and `_is_validation_warning()` to a shared module (e.g. `validation/utils.py`).

---

## 4. Schema evolution and versioning

### 4-1. dan_graph_v1

| Aspect | Current state |
|--------|----------------|
| **Version field** | `Graph.version = "dan_graph_v1"` |
| **Migration strategy** | No formal versioning; migrations (gate_migration) apply when env flag set |
| **New fields** | Pydantic models use `Field(default=...)` for optional fields — backward compatible |

### 4-2. Backward compatibility

| Mechanism | Usage |
|-----------|-------|
| **Optional fields** | New fields added with defaults (e.g. `state_schema`, `state_defaults`, `is_blackbox`) |
| **Discriminated unions** | `node_type` / `edge_type` drive Pydantic resolution |
| **Migration layer** | `gate_migration.py` converts legacy `if_else`/`while_loop` to `gate` on load |

### 4-3. Migration layer

| Migration | Location | Trigger |
|-----------|----------|---------|
| **IfElseNode → GateNode** | `migration/gate_migration.py` | `DAN_GATE_MIGRATION_ENABLED=true` on GET |
| **WhileLoopNode → flat GateNode** | `migration/gate_migration.py` | Same |
| **Version bump** | None | No `dan_graph_v2` or version-based migration path |

**Recommendation:** Document migration strategy for future schema changes; consider explicit version field checks before applying migrations.

---

## 5. Legacy and dead code

### 5-1. Legacy node types (IfElseNode / WhileLoopNode)

| Location | Usage |
|----------|-------|
| **models/control_flow.py** | `IfElseNode`, `WhileLoopNode` defined |
| **models/graph.py** | Both in Node union |
| **builder/compiler.py** | `if_else` → `IfElseNode`, `while_loop` → `WhileLoopNode` |
| **registry.py** | Both registered |
| **executors/control_flow.py** | Both have executors; emit deprecation warnings |
| **validation/graph.py** | `WhileLoopNode` in sub-graph ref check |
| **editor graph.ts** | Both in `DanNode` union |
| **migration** | Converts both to `GateNode` when enabled |

**Migration status:** Migration exists and is opt-in. Legacy nodes still work; deprecation warnings at runtime. Full migration status should reference 11-1 task 1-3 when available.

### 5-2. Orphaned paths

| Item | Status |
|------|--------|
| **Builder `if_else` / `while_loop`** | Still used; builder supports both legacy and gate |
| **Loader** | Compiles to `GateNode` only — no legacy node emission |
| **Decompiler (Python)** | Handles `WhileLoopNode`, `GateNode`; produces gate-style code for both |
| **Decompiler (Markdown)** | Skips `gate`, `for_each`, `parallel_subagents` in agent file write; `_SUPPORTED_NODE_TYPES` excludes `if_else`, `while_loop` |
| **ReAct template** | Uses `CompositeNode` + `GateNode(while)` per changelog — no `WhileLoopNode` |

**Finding:** No clearly orphaned code paths. Legacy support is intentional for backward compatibility.

---

## 6. Cross-surface feature parity

### 6-1. Feature matrix

| Feature | Builder | Loader | Editor | Mutator |
|---------|---------|--------|--------|---------|
| **LLM operator** | ✓ | ✓ | ✓ | ✓ |
| **Tool operator** | ✓ | ✓ | ✓ | ✓ |
| **Code operator** | ✓ | ✓ | ✓ | ✓ |
| **RAG operator** | ✓ | ✗ | ✓ | ✓ |
| **Validator** | ✓ | ✗ | ✓ | ✓ |
| **Input node** | ✗ | ✓ (auto) | ✓ | ✓ |
| **Gate (if_else/while)** | ✓ | ✓ (as gate) | ✓ | ✓ |
| **IfElseNode (legacy)** | ✓ | ✗ | ✓ (type exists) | ✓ |
| **WhileLoopNode (legacy)** | ✓ | ✗ | ✓ (type exists) | ✓ |
| **ForEach** | ✓ | ✓ | ✓ | ✓ |
| **Parallel subagents** | ✓ | ✓ | ✓ | ✓ |
| **Reduce** | ✓ | ✗ | ✓ | ✓ |
| **Router** | ✓ | ✓ | ✓ | ✓ |
| **Human-in-the-loop** | ✓ | ✓ | ✓ | ✓ |
| **Composite** | ✓ | ✓ | ✓ | ✓ |
| **DataEdge** | ✓ | ✓ | ✓ | ✓ |
| **ControlEdge** | ✓ | ✗ | ✓ | ✓ |
| **ContextEdge** | ✓ | ✗ | ✓ | ✓ |
| **Spread edge** | ✓ | ✗ | ✓ (mutator) | ✓ |
| **state_schema / state_defaults** | ✓ (gate, while_loop) | ✓ (loop) | ✓ (GateNode, WhileLoopNode) | ✓ |
| **read_set / write_set** | ✓ | ✗ | Partial (types) | ✓ |
| **shared_context** | ✓ | ✓ | ✓ | ✓ |
| **artifact_refs** | ✓ | ✗ | ✓ | ✓ |
| **loop_groups** | ✗ | ✗ | ✓ (visual only) | ✗ |

### 6-2. Gaps

| Gap | Surface | Impact |
|-----|---------|--------|
| **Loader: no ControlEdge/ContextEdge** | Loader | Markdown workflows cannot express control or context edges |
| **Loader: no RAG, Validator, Reduce** | Loader | Markdown cannot define these node types |
| **Loader: no artifact_refs** | Loader | Round-trip loses artifact refs |
| **Loader: SharedContextDeclaration without json_schema** | Loader | Context declarations lack schema in compiled output |
| **Editor: read_set/write_set** | Editor | Types exist in graph.ts but no dedicated UI; graphAdapter passes through |

### 6-3. Round-trip loss

| Path | Loss |
|------|------|
| **Editor → Save → Load** | Edges created without `danEdge` lose edge_type, spread, context_key, mode |
| **Builder → JSON → Loader** | N/A (different input formats) |
| **Graph → Markdown decompile → Loader** | Control/context edges, RAG, Validator, Reduce, artifact_refs, loop_groups |
| **Graph → Python decompile → Builder** | Lossless per decompiler design |

---

## 7. Config and env handling

### 7-1. Config loading

| Source | Location | Purpose |
|--------|----------|---------|
| **.env** | `load_dotenv()` in `app.py` | API keys, feature flags |
| **EngineConfig** | `_get_engine_config()` in `app.py` | LLM URL/key/model, providers, embedding, checkpoint dir |
| **Server** | `app.py` globals | `DAN_GRAPHS_DIR`, `DAN_GATE_MIGRATION_ENABLED`, `DAN_LAYOUT_ON_LOAD`, `DAN_STRICT_MUTATION_VALIDATION`, `DAN_MUTATION_AUTO_RETRY` |
| **RAG** | `_get_indexer()` | `DAN_RAG_STORE_BACKEND`, `DAN_RAG_PERSIST_DIR` |
| **Sandbox** | `executors/code.py`, `tools/shell_command.py` | `DAN_SANDBOX_SHELL`, `DAN_SANDBOX_TIMEOUT`, `DAN_SANDBOX_MEMORY_MB`, `DAN_SHELL_ALLOW` |
| **Workspace** | `_workspace_root()` | `DAN_WORKSPACE_ROOT` |

### 7-2. Schema for config

| Aspect | State |
|--------|-------|
| **Graph schema** | Config is **external** — not part of `dan_graph_v1` |
| **EngineConfig** | Dataclass; no Pydantic validation of env values |
| **Optional deps** | `pyproject.toml` optional-dependencies: `anthropic`, `google`, `pdf`, `search`, `faiss`, `chroma`, `embeddings`, `quant` |
| **Provider loading** | `app.py` tries `anthropic`, `google` with `try/except ImportError` — graceful degradation |

**Recommendation:** Document all `DAN_*` env vars in `.env.example` and `docs/architecture.md` (or a dedicated config doc).

---

## 8. Trust and sandbox boundaries

### 8-1. Code executor, shell tool, sandbox

| Component | Trust boundary | Notes |
|------------|----------------|--------|
| **CodeExecutor (inline)** | In-process `exec()` with `_ALLOWED_BUILTINS` | Restricted builtins; no filesystem/network isolation |
| **CodeExecutor (subprocess)** | `SandboxRunner` + `SandboxConfig` | Timeout, memory cap, output truncation, env filtering — **operational guardrails, NOT security sandbox** |
| **shell_command tool** | `DAN_SHELL_ALLOW` allowlist when set | Prefix-based allowlist; `DAN_SANDBOX_SHELL` routes to SandboxRunner |
| **File tools** | `DAN_WORKSPACE_ROOT` | Rejects `../` escapes, symlink escapes, paths outside root |
| **SandboxRunner** | `resource.setrlimit` (best-effort) | Memory limits; platform-dependent |

### 8-2. Model vs runtime

| Aspect | State |
|--------|-------|
| **sandbox_config** | Part of **graph schema** — `CodeOperator.sandbox_config` (dict) in node model |
| **SandboxConfig** | Pydantic model in `sandbox/__init__.py` — parsed from `sandbox_config` at runtime |
| **Trust policy** | Not in graph — env vars (`DAN_SHELL_ALLOW`, `DAN_SANDBOX_SHELL`) control runtime behavior |

**Finding:** Sandbox config is per-node in the graph; trust policy (allowlist, sandbox mode) is external via env.

---

## Summary of recommendations

1. **Data flow:** Preserve `danEdge` on all editor-created edges; avoid lossy `reactFlowEdgeToDan` fallback.
2. **Validation:** Centralize `_VALIDATION_WARNING_PATTERNS` and `_is_validation_warning()`.
3. **Schema evolution:** Document migration strategy; consider version checks before applying migrations.
4. **Config:** Document all `DAN_*` env vars in `.env.example` and architecture docs.
5. **Feature parity:** Loader gaps (ControlEdge, ContextEdge, RAG, Validator, Reduce, artifact_refs) — document and prioritize if markdown round-trip matters.
6. **11-1 follow-up:** Use 11-1 task 6-2 for full graphAdapter naming audit; use 11-1 task 1-3 for legacy migration completeness.
