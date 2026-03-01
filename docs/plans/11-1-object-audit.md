# 11-1: Object Audit

**Parent:** [11-structure-review](11-structure-review.md)
**Status:** completed
**Goal:** Audit and document object/data structure design: node types, graph schema, edges, execution state, API contracts, and TypeScript/Python parity.

## Tasks

### 1. Node type consistency

- [x] 1-1. **Base contract** — Do all 14 node types follow the same base contract? Document `NodeBase` fields and which node types override or extend.
- [x] 1-2. **read_set / write_set** — Are context declarations used consistently across atomic operators and control-flow nodes?
- [x] 1-3. **Legacy vs gate** — Any remaining `IfElseNode`/`WhileLoopNode` usage? Migration completeness.

**Primary files:** `models/nodes.py`, `models/control_flow.py`, `registry.py`

---

### 2. Graph schema

- [x] 2-1. **dan_graph_v1 stability** — Document the canonical schema. Are `sub_graphs`, `entry_points`, `exit_points` used consistently across builder, loader, mutator, engine?
- [x] 2-2. **Sub-graph key conventions** — How are `body_graph` keys assigned? Any collisions or ambiguity?

**Primary files:** `models/graph.py`, `validation/graph.py`

---

### 3. Edge semantics

- [x] 3-1. **Data / Control / Context** — Document semantics and enforcement points. (7-8 fixed ContextEdge read validation; check for similar gaps.)
- [x] 3-2. **Spread edges** — `DataEdge.spread` usage and validation alignment.

**Primary files:** `models/edges.py`, `validation/graph.py`, `docs/llm-api-guide.md`

---

### 4. Execution state

- [x] 4-1. **ExecutionState, PortDataStore, NodeStatus** — Aligned with what executors and scheduler expect? Redundant or missing fields?
- [x] 4-2. **Loop-scoped state** — `LocalStateManager`, `state_schema`, `state_defaults` usage (7-6 baseline).

**Primary files:** `engine/state.py`, `engine/scheduler.py`, `engine/context_runtime.py`

---

### 5. API contracts

- [x] 5-1. **REST / WebSocket payloads** — Do they match the models? Document `MutationPlan`, `GraphSummary`, `RunResult` shapes.
- [x] 5-2. **apply-mutation endpoint** — Request/response contract.
- [x] 5-3. **Event payload shapes** — Run events, chat events, mutation events (WebSocket). Document `EngineEvent`, `ChatStreamEvent`, mutation tool schema.
- [x] 5-4. **Chat-specific contracts** — `ChatMessage`, `ChatThread`; parity with `editor/src/types/chat.ts`.
- [x] 5-5. **Error / diagnostic contracts** — How do validation errors, runtime errors, mutation diagnostics propagate? Consistent shapes?

**Primary files:** `server/app.py`, `chat_manager.py`, `graph_mutator.py`, `engine/events.py`, `editor/src/types/chat.ts`, `validation/`

---

### 6. TypeScript / Python parity

- [x] 6-1. **graph.ts vs Python models** — Does `editor/src/types/graph.ts` stay in sync? (e.g. `read_set`/`write_set` on `NodeBase`.)
- [x] 6-2. **graphAdapter conversion** — Any lossy or inconsistent conversions?

**Primary files:** `editor/src/types/graph.ts`, `editor/src/lib/graphAdapter.ts`

---

### 7. Port / schema conventions

- [x] 7-1. **Default ports** — `DEFAULT_OUTPUT_PORTS` consistency across builder, loader, executors.
- [x] 7-2. **Schema shapes** — `required` flags, JSON Schema usage.

**Primary files:** `builder/compiler.py`, `loader/compiler.py`, executors

---

## Deliverables

- Object audit checklist with findings
- Schema/contract doc (or llm-api-guide extension) for `dan_graph_v1`, node types, edges, execution state
- List of model/API changes (deprecations, new fields, TS sync) for future work

## Implementation

- **Output:** Write findings to `docs/plans/11-1-findings.md`
- **Method:** Read primary files, document findings per task. Check off tasks as completed.

## Decisions

- Audit completed 2026-03-01. Findings in `docs/plans/11-1-findings.md`.

## Notes

- Treat 7-8 as baseline — ContextEdge read=target, gate mode-aware defaults, strict mode already done.
- 7-6 may change `state_schema`/`state_defaults`; note as dependency if 7-6 is imminent.
