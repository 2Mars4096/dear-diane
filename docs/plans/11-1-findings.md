# 11-1: Object Audit — Findings

**Plan:** [11-1-object-audit](11-1-object-audit.md)  
**Date:** 2026-03-01  
**Status:** Complete

---

## 1. Node type consistency

### 1-1. Base contract

**Checklist:** Do all 14 node types follow the same base contract? Document `NodeBase` fields and which node types override or extend.

**Findings:**

- **NodeBase fields** (`models/nodes.py`): `id`, `name`, `description`, `input_ports`, `output_ports`, `position`, `ui`, `metadata`, `retry_policy`, `read_set`, `write_set`.
- **All 14 node types** inherit from `NodeBase` and add a `node_type` literal. No node type omits base fields.
- **Overrides:**
  - **Composite-node contract** (WhileLoopNode, ForEachNode, ParallelSubagentsNode, CompositeNode): Override `read_set`/`write_set` with explicit `Field(default_factory=list)` for consistency; add `external_input_schema`, `external_output_schema`, `control_state_schema`, `local_state`, `compaction_rule`, `failure_policy` (where applicable), `projections`.
  - **GateNode:** `model_post_init` sets default output ports when empty; uses `state_schema`/`state_defaults` (7-6 baseline).
  - **ValidatorNode:** `model_post_init` sets default output ports when empty.
  - **InputNode:** Uses `variables` instead of `input_ports` for external inputs; `output_ports` derive from variables.

**Recommendations:** None. Base contract is consistent.

---

### 1-2. read_set / write_set

**Checklist:** Are context declarations used consistently across atomic operators and control-flow nodes?

**Findings:**

- **NodeBase** defines `read_set` and `write_set` as `list[ContextDeclaration]` with `default_factory=list`.
- **Atomic operators** (LLMOperator, ToolOperator, CodeOperator, RAGOperator): Inherit from NodeBase; no `read_set`/`write_set` overrides. Default empty.
- **Control-flow nodes** (IfElseNode, GateNode, ReduceNode, RouterNode, HumanInTheLoopNode, ValidatorNode): Inherit from NodeBase; no overrides.
- **Composite/loop nodes** (WhileLoopNode, ForEachNode, ParallelSubagentsNode, CompositeNode): Explicitly override `read_set` and `write_set` with `Field(default_factory=list)` for the same semantics — no functional difference.

**Recommendations:** None. Usage is consistent. Validation (`_check_context_edge_permissions`) enforces that `ContextEdge` modes match `read_set`/`write_set` on target (READ) or source (WRITE/APPEND).

---

### 1-3. Legacy vs gate

**Checklist:** Any remaining `IfElseNode`/`WhileLoopNode` usage? Migration completeness.

**Findings:**

- **IfElseNode** and **WhileLoopNode** remain in the codebase: registry, graph models, executors, decompiler, builder compiler, migration.
- **Executor:** `IfElseExecutor` and `WhileLoopExecutor` emit deprecation warnings when used.
- **Migration:** `dan.migration.gate_migration` converts `IfElseNode` → `GateNode(gate_mode='if_else')` and `WhileLoopNode` → flat gate-controlled cycles. Migration is opt-in via `DAN_GATE_MIGRATION_ENABLED`.
- **Builder:** Still supports `if_else` and `while_loop` node types; loader compiles `loop()` and `if()` to `GateNode`; builder compiles to legacy types when explicitly requested.
- **chat_manager NODE_TYPES:** Omits `if_else` and `while_loop` — only `gate` is listed for mutation tool schema.
- **graph.ts:** Still defines `IfElseNode` and `WhileLoopNode`; `NODE_TYPE_CATALOG` uses `gate_if_else` and `gate_while` (UI variants of gate).

**Recommendations:**

1. **Deprecation path:** Document in `docs/llm-api-guide.md` that `if_else` and `while_loop` are deprecated; prefer `gate` with `gate_mode`.
2. **Future work:** Remove `IfElseNode`/`WhileLoopNode` from registry and union once migration is complete; gate_migration can remain for loading legacy graphs.

---

## 2. Graph schema

### 2-1. dan_graph_v1 stability

**Checklist:** Document the canonical schema. Are `sub_graphs`, `entry_points`, `exit_points` used consistently across builder, loader, mutator, engine?

**Findings:**

**Canonical schema** (`models/graph.py`):

- `version`: `"dan_graph_v1"`
- `metadata`: `GraphMetadata` (name, description, created_at, updated_at, tags)
- `nodes`: list of discriminated `Node` union
- `edges`: list of discriminated `Edge` union (DataEdge, ControlEdge, ContextEdge)
- `sub_graphs`: `dict[str, Graph]` — recursive
- `entry_points`: `list[str]` (node IDs)
- `exit_points`: `list[str]` (node IDs)
- `shared_context`: `list[SharedContextDeclaration]`
- `artifact_refs`: `list[ArtifactRef]`

**Consistency:**

| Component | sub_graphs | entry_points | exit_points |
|-----------|------------|--------------|-------------|
| `builder/compiler` | Assembles from `_PendingSubGraph`; keys from `sub_graph_key` | `_find_entry_points` (data edges) | `_find_exit_points` (data edges) |
| `loader/compiler` | Assembles from flow; keys like `{agent}__body`, `{parallel_id}__{branch}` | `_find_entry_points`; overridden by input_node | `_find_exit_points` |
| `graph_mutator` | `_recompute_entry_exit_points` (data edges only) | Recomputed | Recomputed |
| `engine/scheduler` | `sub_graph.sub_graphs.get(sub_graph_key)` | Injects inputs via `graph.entry_points` | Collects outputs via `graph.exit_points` |
| `validation/graph` | `_check_sub_graph_refs` for WhileLoopNode, ForEachNode, CompositeNode | `_check_entry_exit` validates IDs exist | Same |

**Gaps:**

- **Mutator:** `_recompute_entry_exit_points` overwrites `entry_points`/`exit_points` entirely. No preservation of explicit user-set entry/exit points.
- **Loader:** Uses `entry_points = [input_node.id]` when input_node exists; otherwise `_find_entry_points`. Logic is consistent with builder.

**Recommendations:**

1. Document mutator behaviour: entry/exit points are always recomputed from data-edge connectivity after structural changes. If explicit entry/exit semantics are needed, add a separate field or convention.
2. Add canonical schema to `docs/llm-api-guide.md` (or extend existing schema section).

---

### 2-2. Sub-graph key conventions

**Checklist:** How are `body_graph` keys assigned? Any collisions or ambiguity?

**Findings:**

- **Builder:** `{parent_node_id}__body` or explicit `sub_graph_key` from `_PendingSubGraph`.
- **Loader:** `{foreach_id}__body`, `{parallel_id}__{branch_agent}`, `{name}__body` for composite.
- **Validation:** `_check_sub_graph_refs` ensures each `body_graph`/`branch_graphs` key exists in `graph.sub_graphs`.
- **Loader collision check:** `_ensure_unique_id` used for `sub_key` and `foreach_id`; errors on duplicate keys during compilation.

**Recommendations:** None. Conventions are consistent; loader validates uniqueness. Document pattern: `{parent_id}__body` or `{parent_id}__{branch_name}`.

---

## 3. Edge semantics

### 3-1. Data / Control / Context

**Checklist:** Document semantics and enforcement points. (7-8 fixed ContextEdge read validation; check for similar gaps.)

**Findings:**

**DataEdge:**

- Carries structured data between ports.
- Schema compatibility checked in `_check_data_edge_schemas`; validation warns on empty schemas.
- Scheduler uses `PortDataStore.resolve_inputs` to collect upstream values; `DataEdge` only.

**ControlEdge:**

- Encodes routing / flow-control signals (branch selection).
- `condition` optional; deprecated in favour of `GateNode` branch ports (`_check_deprecated_edge_conditions` emits warnings).
- Scheduler `_should_skip` uses `ControlEdge.condition` and `active_branch` from source outputs; also gate branch-port routing (no data on inactive port → skip).

**ContextEdge:**

- Connects node to shared-context key; `mode` in `{read, write, append}`.
- **Validation (7-8):** `_check_context_edge_permissions` enforces:
  - READ: target must have `context_key` in `read_set`
  - WRITE/APPEND: source must have `context_key` in `write_set`
- **Runtime:** `_read_context_edges` injects into `inputs`; `_write_context_edges` writes from `outputs` to `SharedContextStore`.

**Gaps:** None identified. 7-8 baseline is in place.

---

### 3-2. Spread edges

**Checklist:** `DataEdge.spread` usage and validation alignment.

**Findings:**

- **Model:** `DataEdge.spread: bool = False`.
- **Validation:** `_check_data_edge_schemas` requires object-type ports when `spread=True`; errors if source/target port schema type is not `object`.
- **Runtime:** `PortDataStore.resolve_inputs` spreads dict values: `inputs[k] = v` for each `k, v in value.items()` when `edge.spread` and value is dict.
- **Mutation tool:** `add_edge` schema includes `spread` with description. `graph_mutator._op_add_edge` sets `edge["spread"] = True` when `op.edge_type == "data" and op.spread`.

**Recommendations:** None. Spread semantics and validation are aligned.

---

## 4. Execution state

### 4-1. ExecutionState, PortDataStore, NodeStatus

**Checklist:** Aligned with what executors and scheduler expect? Redundant or missing fields?

**Findings:**

**ExecutionState** (`engine/state.py`):

- `graph`, `run_id`, `node_statuses`, `port_data`, `node_errors`, `node_metadata`
- `snapshot()` / `restore_from_snapshot()` for checkpointing

**PortDataStore:**

- `(node_id, port_name) -> value`; `resolve_inputs` for downstream collection; `spread` handling
- `get_node_outputs`, `clear_node` used by scheduler

**NodeStatus:** `PENDING`, `WAITING`, `RUNNING`, `COMPLETED`, `FAILED`, `SKIPPED`

**Scheduler usage:**

- `node_statuses` for ready/terminal checks
- `port_data` for input resolution and output storage
- `node_errors` for `RunResult.errors`
- `node_metadata` for halt signal, usage aggregation

**RunResult** (`scheduler.py`): `run_id`, `outputs`, `success`, `node_statuses`, `errors`, `metadata`

**Recommendations:** None. State model is aligned. `WAITING` exists but is not actively used; consider documenting or removing if unused.

---

### 4-2. Loop-scoped state

**Checklist:** `LocalStateManager`, `state_schema`, `state_defaults` usage (7-6 baseline).

**Findings:**

- **LocalStateManager:** Scoped by `scope_id` (node_id); `get_scope`, `update_scope`, `set_scope`, `delete_scope`; `snapshot`/`restore` for checkpointing.
- **GateNode:** `state_schema`, `state_defaults`; `model_post_init` normalizes `state_schema` to flat key→schema.
- **WhileLoopNode:** Same `state_schema`, `state_defaults`; `_normalize_state_schema` in `model_post_init`.
- **Scheduler:** `context.active_loop_scope_id = gate_id` when gate has `state_schema`; injects `context.local_state.get_scope` into inputs; updates scope from `result.outputs`; writes final scope to `port_data` on exit.
- **7-6 dependency:** `state_schema`/`state_defaults` may change in 7-6; audit should flag. Current usage is consistent.

**Recommendations:** None. 7-6 may change `state_schema`/`state_defaults`; note in 11-3 if 7-6 is imminent.

---

## 5. API contracts

### 5-1. REST / WebSocket payloads

**Checklist:** Do they match the models? Document `MutationPlan`, `GraphSummary`, `RunResult` shapes.

**Findings:**

**MutationPlan** (`graph_mutator.py`):

- `plan_id`, `base_graph_revision`, `base_graph_hash`, `apply_mode`, `operations`, `description`, `reasoning`

**GraphSummary** (`chat_manager.py`):

- `workflow_id`, `name`, `description`, `node_count`, `edge_count`, `nodes`, `edges`, `entry_points`, `exit_points`, `revision`

**RunResult** (`scheduler.py`):

- `run_id`, `outputs`, `success`, `node_statuses`, `errors`, `metadata`

**REST:** `GET /api/graphs/{id}` returns `{graph_id, data}`; `POST /api/graphs/{id}/apply-mutation` accepts `ApplyMutationRequest` with `mutation_plan`, `idempotency_key`; returns `{success, new_graph, errors, warnings, diagnostics, stale_plan, idempotent_hit?}`.

**Recommendations:** Document these shapes in `docs/llm-api-guide.md` or a dedicated API schema doc.

---

### 5-2. apply-mutation endpoint

**Checklist:** Request/response contract.

**Findings:**

- **Request:** `ApplyMutationRequest`: `mutation_plan: dict`, `idempotency_key?: str`
- **Response (success):** `{success: true, new_graph, errors: [], warnings, diagnostics, stale_plan: false, idempotent_hit?: true}`
- **Response (failure):** `{success: false, new_graph: null, errors: [{message}], stale_plan?, message?}`
- **Validation:** `_STRICT_MUTATION_VALIDATION`; `Graph.model_validate`; `validate_graph`; fatal vs warnings split.

**Recommendations:** None. Contract is clear.

---

### 5-3. Event payload shapes

**Checklist:** Run events, chat events, mutation events (WebSocket). Document `EngineEvent`, `ChatStreamEvent`, mutation tool schema.

**Findings:**

**EngineEvent** (`engine/events.py`):

- `event_type`, `run_id`, `timestamp`, `node_id?`, `node_type?`, `data`
- `to_dict()` for serialisation

**ChatStreamEvent** (union):

- `ChatTokenEvent`: `type`, `delta`, `accumulated`
- `ChatCompleteEvent`: `type`, `message_id`, `content`, `token_usage`, `graph_revision`, `revision_mismatch`
- `ChatErrorEvent`: `type`, `error`
- `ChatMutationEvent`: `type`, `message_id`, `content`, `mutation_plan`, `dry_run_result`, `token_usage`, `graph_revision`, `revision_mismatch`

**Mutation tool schema:** `MUTATION_TOOL_SCHEMA` in `chat_manager.py`; `oneOf` by `op`; `plan_graph_mutations` function.

**Recommendations:** Add `EngineEvent` and `ChatStreamEvent` shapes to API docs.

---

### 5-4. Chat-specific contracts

**Checklist:** `ChatMessage`, `ChatThread`; parity with `editor/src/types/chat.ts`.

**Findings:**

**Python (chat_store.py):**

- `ChatMessage`: `id`, `role`, `content`, `mentions`, `mutation_plan`, `dry_run_result`, `mutation_id`, `mutation_status`, `token_usage`, `timestamp`, `run_ref`
- `ChatThread`: `id`, `workflow_id`, `title`, `messages`, `created_at`, `updated_at`

**TypeScript (chat.ts):**

- `ChatMessage`: `id`, `role`, `content`, `timestamp` (number), `tokenUsage?`, `mutationPlan?`, `dryRunResult?`, `mutationId?`, `mutationStatus?`, `runRef?`, `mentions?`
- `ChatThread`: `id`, `workflow_id`, `title`, `messages`, `created_at`, `updated_at`

**Parity differences:**

- `timestamp`: Python `datetime` (ISO string in JSON); TS `number` (ms epoch).
- `token_usage` vs `tokenUsage`: Python snake_case; TS camelCase. API likely returns snake_case; frontend may need mapping.
- `run_ref` vs `runRef`: Same casing difference.

**Recommendations:** Document casing convention (API snake_case vs TS camelCase). If API returns snake_case, ensure `graphAdapter` or API layer maps consistently.

---

### 5-5. Error / diagnostic contracts

**Checklist:** How do validation errors, runtime errors, mutation diagnostics propagate? Consistent shapes?

**Findings:**

- **Validation:** `validate_graph` returns `list[str]`. Endpoints map to `{message, edge_id?, node_id?}` via regex.
- **Mutation errors:** `OperationError`: `op_index`, `op_type`, `message`. Returned as `errors: [{message}]` or `[e.model_dump() for e in result.errors]`.
- **Runtime:** `RunResult.errors` is `dict[str, str]` (node_id → error string).
- **Validate endpoint:** `{errors: [{message, edge_id?, node_id?}], warnings: [...]}`.

**Recommendations:** Standardise error shape: `{code?, message, node_id?, edge_id?, op_index?}` for consistency across validation, mutation, and runtime.

---

## 6. TypeScript / Python parity

### 6-1. graph.ts vs Python models

**Checklist:** Does `editor/src/types/graph.ts` stay in sync? (e.g. `read_set`/`write_set` on `NodeBase`.)

**Findings:**

**Gaps:**

- **NodeBase:** TS `NodeBase` lacks `read_set` and `write_set`. Python has them.
- **Composite/loop nodes:** TS `WhileLoopNode`, `ForEachNode`, `CompositeNode`, `ParallelSubagentsNode` lack `read_set`, `write_set`, `control_state_schema`, `local_state`, `compaction_rule`, `failure_policy`, `projections` where applicable.
- **InputNode:** TS `InputNodeType`; Python `InputNode` — same.
- **NODE_TYPE_CATALOG:** TS uses `gate_if_else` and `gate_while` as palette types; Python uses `gate` with `gate_mode`. Editor creates `node_type: "gate"` with correct `gate_mode` — correct.
- **RagOperator:** TS uses `RagOperator` (capital R); Python `RAGOperator`. Naming inconsistency only.

**Recommendations:**

1. Add `read_set` and `write_set` to TS `NodeBase` (and composite node interfaces) for round-trip fidelity.
2. Add missing composite/loop fields to TS interfaces if editor needs to display or edit them.

---

### 6-2. graphAdapter conversion

**Checklist:** Any lossy or inconsistent conversions?

**Findings:**

- **danNodeToReactFlow:** Passes entire node as `data`; no loss.
- **danEdgeToReactFlow:** Passes `danEdge` in `data`; handles back-edges for gate(while).
- **reactFlowNodeToDan:** Spreads `data`, overwrites `id` and `position`; assumes `data` contains full node. No explicit `read_set`/`write_set` — loss if TS types omit them.
- **reactFlowEdgeToDan:** If `data.danEdge` exists, uses it and updates source/target/ports; otherwise creates default DataEdge. No loss for edges with `danEdge`.

**Recommendations:** Ensure `danEdge` is always preserved when editing edges. Add `read_set`/`write_set` to TS so they round-trip.

---

## 7. Port / schema conventions

### 7-1. Default ports

**Checklist:** `DEFAULT_OUTPUT_PORTS` consistency across builder, loader, executors.

**Findings:**

| Node type | builder/compiler | loader/compiler | graph_mutator |
|-----------|------------------|-----------------|---------------|
| llm_operator | text | text | text |
| tool_operator | result | result | result |
| code_operator | result | result | result |
| rag_operator | chunks | (not in loader) | chunks |
| if_else | branch | (not in loader) | true, false |
| gate | true / continue (by mode) | true | true / continue (by mode) |
| while_loop | result | (not in loader) | output |
| for_each | results | results | results |
| parallel_subagents | results | results | results |
| reduce | result | (not in loader) | result |
| router | route | route | route |
| human_in_the_loop | response | response | response |
| validator | valid | (not in loader) | valid, invalid |
| composite | result | result | output |
| input | (N/A) | output | input |

**Gaps:**

- **composite:** Builder uses `result`; mutator uses `output`. Builder `DEFAULT_OUTPUT_PORTS["composite"] = "result"`; mutator `[{"name": "output", "schema": {}}]`. Inconsistent.
- **input:** Loader `input: "output"`; mutator outputs `input`. Loader's `input` in DEFAULT_OUTPUT_PORTS may be unused (loader creates InputNode via `_create_input_node` with variable-based output ports).
- **while_loop:** Builder `result`; mutator `output`. Inconsistent.

**Recommendations:**

1. Align composite default output: `result` (builder) vs `output` (mutator). Choose one; update the other.
2. Align while_loop: `result` (builder) vs `output` (mutator). Choose one.
3. Add `DEFAULT_OUTPUT_PORTS` / `_default_ports` to a shared module (e.g. `dan.models.ports` or `dan.constants`) to avoid drift.

---

### 7-2. Schema shapes

**Checklist:** `required` flags, JSON Schema usage.

**Findings:**

- **InputPort:** `required: bool = True`; `json_schema` dict.
- **OutputPort:** No `required`; `json_schema` dict.
- **Validation:** `_check_required_ports` skips entry-point nodes; requires non-entry nodes to have required ports connected.
- **Builder:** `_auto_generate_ports` adds `InputPort(required=True)` for needed ports.
- **Loader:** `_build_input_ports` uses `required=True` for declared ports; `DEFAULT_INPUT_PORT` gets `required=False` when no ports declared.

**Recommendations:** None. Schema usage is consistent. Document that `required` defaults to True for InputPort.

---

## Model/API changes for future work

| Priority | Change | Rationale |
|----------|--------|-----------|
| High | Add `read_set`/`write_set` to TS `NodeBase` (and composite nodes) | Round-trip fidelity; ContextEdge validation |
| High | Align `DEFAULT_OUTPUT_PORTS` for composite, while_loop, input across builder/loader/mutator | Prevent port mismatches |
| Medium | Standardise error shape `{code?, message, node_id?, edge_id?, op_index?}` | Consistent API surface |
| Medium | Document deprecation of `if_else`/`while_loop` in llm-api-guide | Clear migration path |
| Low | Extract `DEFAULT_OUTPUT_PORTS` to shared module | Single source of truth |
| Low | Add `EngineEvent` and `ChatStreamEvent` shapes to API docs | Documentation |
| Low | Document `chat_store` vs `chat.ts` casing (snake_case vs camelCase) | Frontend parity |

---

## Decisions

- None made during audit; recommendations only.

## Notes

- 7-8 baseline confirmed: ContextEdge read=target, gate mode-aware defaults, strict mode.
- 7-6 may change `state_schema`/`state_defaults`; flag in 11-3 if 7-6 is imminent.
- `ParallelSubagentsNode` has `failure_policy` in TS; Python has it in composite contract. Parity is mostly good.
