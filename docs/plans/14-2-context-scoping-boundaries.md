# 14-2: Context Scoping Across Agent Boundaries

**Parent:** [14-memory-cross-run-state](14-memory-cross-run-state.md)
**Status:** not-started
**Goal:** Turn the conceptual four-scope model (`global`, `local`, `pass_down`, `emit_up`) into enforced runtime contracts with explicit schemas and predictable signal propagation — by activating and extending existing dead-code models.

## Existing Baseline

| Component | Location | What exists | Gap |
|---|---|---|---|
| `ContextProjection` | `models/context.py` | Model with `include`, `exclude`, `rename`, `transform` fields | **Dead code** — never called by any executor or scheduler path |
| `CompactionRule` | `models/context.py` | Enum of `sliding_window`, `summarize`, `diff` strategies | **Dead code** — declared, never activated |
| `MergeStrategy` | `models/context.py` | Enum: `APPEND`, `LAST_WRITE_WINS`, `REDUCER` | Used only by `ParallelSubagentsExecutor` for fan-in; not applied to context merging |
| `SharedContextDeclaration` | `models/context.py` | `key`, `schema`, `description` — declares shared context keys | Used for key validation in `SharedContextStore`; no scope/direction |
| `CompositeNode` | `models/control_flow.py` | `external_input_schema`, `external_output_schema`, `projections`, `read_set`, `write_set` | `projections` unused; `external_*_schema` used for mapping, not runtime validation |
| `read_set` / `write_set` | `models/nodes.py` | On `NodeBase`; validated by `_check_context_edge_permissions` in `validation/graph.py` | Design-time validation only — no runtime access control |
| `SharedContextStore` | `engine/context_runtime.py` | In-memory KV with `read()`/`write()`/`append()` keyed against `graph.shared_context` | No scope isolation: `_run_subgraph` passes the **same instance** to children; no global/local distinction |
| `LocalStateManager` | `engine/context_runtime.py` | Node-scoped state bags; used for while-gate `state_schema` | Flat scoping — no hierarchical nesting |
| `CompositeExecutor` | `executors/control_flow.py` | Uses `input_mappings`/`output_mappings`; passes full `inputs` and full `body_output` | No projection applied; child sees everything parent passes |
| `_run_subgraph` | `engine/scheduler.py` | Receives `shared_context`, `artifacts`, `local_state` — shares parent's `SharedContextStore` instance | No boundary isolation; child writes are immediately visible to parent |
| `OrchestratorExecutor` | `executors/control_flow.py` | Writes to `shared_context.__orchestrator__{node_id}__received__{team}` | Ad-hoc key convention; no formal signal schema |
| Architecture docs | `docs/architecture.md` §Context Scoping | Defines `global`/`local`/`pass_down`/`emit_up`; sticky/non-sticky signals; agent boundary contract (`accepts`, `returns`, `signals`) | Prose-only — not implemented |

## Tasks

- [ ] 1. Activate and extend boundary contract models
  - [ ] 1-1. Extend existing `CompositeNode` fields into a unified `BoundaryContract` model: `accepts` (pass_down schema, subsumes `external_input_schema`), `returns` (emit_up schema, subsumes `external_output_schema`), `signals` (list of `SignalSpec`), `reads_global` (subset of `read_set`), `writes_global` (subset of `write_set`). Add as optional field on `CompositeNode`, `ForEachNode`, `ParallelSubagentsNode`, `OrchestratorNode`.
  - [ ] 1-2. Define `SignalSpec` model: `name`, `payload_schema` (JSON Schema), `sticky` (bool), `severity` (info/warning/error). Signals are typed upward emissions distinct from structured output.
  - [ ] 1-3. Activate `ContextProjection` for `pass_down` enforcement: extend with `required`/`optional`/`defaults` fields so projection doubles as input validation. Wire into `CompositeNode.projections` field.
  - [ ] 1-4. Define backward-compatible serialization: graphs without `BoundaryContract` run in permissive mode (warnings logged, no enforcement). Add `boundary_enforcement` flag to `EngineConfig` (default: `"warn"`; values: `"off"`, `"warn"`, `"strict"`).

- [ ] 2. Enforce directional scope semantics at runtime
  - [ ] 2-1. **`_run_subgraph` isolation**: instead of passing the parent's `SharedContextStore` directly, create a scoped view that exposes only `reads_global` keys as read-only, plus the child's own declared keys. Writes to global go through a pending buffer validated against `writes_global`.
  - [ ] 2-2. Apply `ContextProjection` on boundary entry in `CompositeExecutor` / `WhileLoopExecutor` / `ForEachExecutor`: filter `inputs` to match `accepts` schema before passing to `_run_subgraph`. Log warning (or error in strict mode) for undeclared fields.
  - [ ] 2-3. Apply `returns` validation on boundary exit: `body_output` is filtered to match `returns` schema before flowing back to parent. Undeclared fields are dropped (warn) or rejected (strict).
  - [ ] 2-4. Implement upward signal routing: sticky signals → write to global `SharedContextStore` under `signals/{signal_name}`; non-sticky signals → returned in subgraph result metadata, consumed by immediate parent only (not propagated further).
  - [ ] 2-5. Ensure `local` scope isolation: `LocalStateManager` scopes created inside `_run_subgraph` are not visible to the parent. Verify this is already the case and add explicit test; if not, add cleanup.

- [ ] 3. Integrate with context runtime and control-flow executors
  - [ ] 3-1. Add scope-aware context APIs: `ExecutionContext.read_global(key)`, `write_global(key, value)`, `read_local(key)`, `write_local(key, value)`, `emit_signal(name, payload)`. Implement as wrappers over existing `SharedContextStore`/`LocalStateManager` with boundary permission checks.
  - [ ] 3-2. Update all composite/loop/parallel executors to apply boundary contracts consistently. Priority order: `CompositeExecutor`, `GateExecutor` (while mode), `ForEachExecutor`, `ParallelSubagentsExecutor`, `OrchestratorExecutor`.
  - [ ] 3-3. Define merge behavior when parallel children emit colliding signals: last-write-wins for non-sticky (parent picks), append for sticky (all written to global).
  - [ ] 3-4. Emit engine events for boundary operations: `BOUNDARY_PROJECTION_APPLIED`, `SIGNAL_EMITTED`, `BOUNDARY_VIOLATION` (warning or error). Wire through existing `event_callback` infrastructure.

- [ ] 4. Validation, compiler, and decompiler parity
  - [ ] 4-1. Extend `validate_graph()` with boundary contract checks: missing `BoundaryContract` on composites (warning), `accepts`/`returns` type mismatch against connected edges, `reads_global`/`writes_global` referencing undeclared shared context keys.
  - [ ] 4-2. Extend builder API: `wf.composite("name", accepts={...}, returns={...}, signals=[...])`. Sugar for signal declaration: `composite.signal("discovery", schema={...}, sticky=True)`.
  - [ ] 4-3. Extend markdown loader: `boundary:` section in agent frontmatter for `accepts`/`returns`/`signals`. Decompiler emits matching frontmatter. Round-trip test.
  - [ ] 4-4. Add strict-mode diagnostics: warn when a composite has data edges crossing its boundary without matching `accepts`/`returns` declarations.

- [ ] 5. Tests and documentation
  - [ ] 5-1. Unit tests: `ContextProjection` filter correctness, `BoundaryContract` validation, `SignalSpec` routing (sticky vs non-sticky), scoped `SharedContextStore` view isolation.
  - [ ] 5-2. Integration tests: 3-depth nested composite with boundary contracts, parallel subagents with colliding signals, while-loop with projection + local state isolation.
  - [ ] 5-3. Compatibility tests: legacy graphs (no `BoundaryContract`) run in permissive mode with no errors; upgrade path via `boundary_enforcement` config.
  - [ ] 5-4. Update `docs/architecture.md` §Context Scoping: replace conceptual prose with executable contract definitions, link to model classes, add enforcement mode documentation.
  - [ ] 5-5. Update `docs/llm-api-guide.md`: boundary declaration examples in builder and markdown, signal handling, `boundary_enforcement` config.

## Primary Files

- `src/dan/models/context.py` — activate `ContextProjection`; add `BoundaryContract`, `SignalSpec`
- `src/dan/models/control_flow.py` — add `BoundaryContract` field to `CompositeNode`, `ForEachNode`, `ParallelSubagentsNode`, `OrchestratorNode`
- `src/dan/engine/context_runtime.py` — scoped `SharedContextStore` view; signal routing; scope-aware APIs
- `src/dan/engine/scheduler.py` — `_run_subgraph` creates scoped view instead of passing raw store
- `src/dan/executors/control_flow.py` — `CompositeExecutor`, `GateExecutor`, `ForEachExecutor`, `ParallelSubagentsExecutor`, `OrchestratorExecutor` apply boundary contracts
- `src/dan/validation/graph.py` — boundary contract validation rules
- `src/dan/builder/builder.py` — `wf.composite()` gains `accepts`/`returns`/`signals` kwargs
- `src/dan/loader/compiler.py` — `boundary:` frontmatter section
- `src/dan/loader/decompiler.py` — emit boundary frontmatter
- `src/dan/engine/events.py` — `BOUNDARY_PROJECTION_APPLIED`, `SIGNAL_EMITTED`, `BOUNDARY_VIOLATION` event types
- `tests/test_engine/` — boundary enforcement, projection, signal routing tests
- `tests/test_validation/` — boundary contract validation tests

## Decisions

- **Activate before replacing**: `ContextProjection` and `read_set`/`write_set` are extended, not duplicated. `BoundaryContract` unifies and supersedes `external_input_schema`/`external_output_schema` while keeping backward compat.
- **Directional data flow is explicit by default**: no implicit inheritance of parent local state into children.
- **Signals are typed artifacts**: they are not free-form side channels. `SignalSpec` with payload schema.
- **Compatibility first**: legacy workflows run in permissive mode (`boundary_enforcement: "warn"`) with logged warnings. Strict mode is opt-in.
- **`_run_subgraph` is the enforcement point**: boundary contracts are applied where control crosses the sub-graph boundary, not at individual node level.

## Notes

- This plan provides the safety envelope needed by 14-3 memory retrieval/injection (scope-aware memory access).
- Hyperedge/rule propagation (9B) must honor these boundary semantics once implemented.
- `OrchestratorExecutor`'s ad-hoc `__orchestrator__` key convention should be migrated to formal signal emissions as part of task 3-2.
