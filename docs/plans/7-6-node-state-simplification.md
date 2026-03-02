# 7-6: Node State Simplification

**Parent:** [7-core-hardening](7-core-hardening.md)
**Status:** completed
**Goal:** Eliminate state-threading boilerplate in iterative workflows. Three backward-compatible changes — loop-scoped state, code node defaults, struct edges — so nodes stay simple Lego blocks that compose without glue code.

## Problem

The node types are clean Lego blocks (LLM, code, tool, gate, for_each, composite) with intuitive interfaces: input ports, output ports, prompt/code/tool_id, model settings. The three edge types (data, control, context) are easy to understand.

But iterative workflows break the simplicity. A while-loop with 10 state fields (results, strategies_tried, iteration, try_more, start_year, end_year, max_factors, active_departments, deleted_departments, results_summary) requires:

- **10 edges per node** just to thread state through — 87 total in vibe_research
- **30-line unpack/repack code nodes** at every boundary (unpack, persist, expand, merge, governor — all nearly identical boilerplate)
- **`try/except NameError`** on every variable in every code node because the code executor injects port values as raw locals with no defaults
- **`{"result": {...}}` wrapping convention** to pack state into a single port for the gate condition

The architecture already describes `SharedContextStore` (Layer 3) and `LocalStateManager` (Layer 2) for exactly this purpose. They exist in the code but nobody uses them — everything is forced through `DataEdge` → `PortDataStore`.

## Design

Three changes. No new node types. No new edge types. Existing workflows unchanged.

### A. Loop-Scoped State

`GateNode(while)` and `WhileLoopNode` gain an optional `state_schema` dict (JSON Schema) and `state_defaults` dict. When present, the engine maintains a state bag via `LocalStateManager` scoped to the loop.

**How it works:**
1. On loop entry: gate executor initializes scope from `state_defaults` merged with input port values
2. On each re-iteration: the scheduler injects scope fields into **every cycle node's** `inputs` dict inside `_execute_node` — state fields appear as regular port values. Code nodes see them as namespace locals (`results`, `iteration`, etc.). LLM nodes see them as template variables (`{results}`, `{iteration}`). No executor changes needed — the Lego block contract is preserved.
3. After each cycle node completes: the scheduler merges output keys matching `state_schema` back into scope (also in `_execute_node`). Sequential body nodes see each other's updates — scope is live between topological levels.
4. Gate condition evaluates against scope fields directly (no wrapping/unwrapping)
5. On loop exit (gate emits `done`): final scope becomes the loop's output

**Why scope injection, not spread edges from entry:** Spread edges are snapshots — they capture the source node's output at one point in time. In a sequential body (`entry → coder → backtest → gate`), if `coder` updates `results`, `backtest` would see the *old* `results` from entry's spread, not `coder`'s update. You'd need explicit edges between every sequential pair, plus pass-through boilerplate for unchanged fields — the exact problem we're solving. Scope injection is live: `coder` writes to scope, `backtest` reads the updated value automatically.

**Model change on `GateNode`:**
```python
state_schema: dict[str, Any] | None = None   # JSON Schema for loop state
state_defaults: dict[str, Any] | None = None  # initial values
```

**ExecutionContext change:**
```python
active_loop_scope_id: str | None = None  # set by scheduler during cycle re-execution
```

**Executor init (first call):**
- `GateExecutor.execute`: when `state_schema` is present and scope is empty, init scope from `state_defaults` merged with resolved input port values. This happens during the normal topological pass — BEFORE `_iterate_cycle` is called.

**Scope injection in `_execute_node` (the core mechanism):**
```python
# Before executor call (after resolve_inputs, ~line 833):
if context.active_loop_scope_id:
    gate = graph.node_by_id(context.active_loop_scope_id)
    if gate and getattr(gate, 'state_schema', None):
        scope = context.local_state.get_scope(context.active_loop_scope_id)
        for k in gate.state_schema:
            if k not in inputs:  # explicit edges take precedence
                inputs[k] = scope.get(k, (gate.state_defaults or {}).get(k))

# After executor call (after storing outputs, ~line 864):
if context.active_loop_scope_id:
    gate = graph.node_by_id(context.active_loop_scope_id)
    if gate and getattr(gate, 'state_schema', None):
        updates = {k: v for k, v in result.outputs.items() if k in gate.state_schema}
        if updates:
            context.local_state.update_scope(context.active_loop_scope_id, updates)
```

This is ~15 lines in one method. No executor changes. Nodes receive state fields as regular inputs and output normally — they don't know they're in a loop.

**Scheduler change in `_iterate_cycle`:**
- Set `context.active_loop_scope_id = gate_id` before re-executing cycle levels; clear it after
- Write `iteration` counter into scope at the top of each cycle turn (unifies the scheduler's `for iteration in range(1, max_iterations)` with the executor's `scope["gate_iteration"]` into one source of truth)
- Existing `continue_data` injection into `__input__` is preserved for backward compat

**Gate condition evaluation:**
- When `state_schema` is present, `GateExecutor` populates `condition_vars` from scope (replaces the current dict-flattening heuristic and the hardcoded `try_more` default)

**Parallel level safety:** Within a topological level, `asyncio.gather` runs nodes concurrently. All nodes in the same level see the scope snapshot from before the level started. After the level completes, their outputs are merged into scope (last-write-wins within a level). This is safe: asyncio is single-threaded, and concurrent scope reads don't race. If two nodes in the same level both write the same state key, graph validation can warn about it.

**First-pass vs. re-execution:** On the first topological pass, `active_loop_scope_id` is not set — body nodes receive inputs from normal data edges. The gate initializes scope on first call. Scope injection only activates during `_iterate_cycle` re-executions. This is correct: the first pass establishes initial values, subsequent iterations use scope as the source of truth.

**What this replaces in vibe_research:** The `unpack_multi_dept_input`, `persist_dept_state`, `merge_dept_results`, and `governor` code nodes are 80% state pass-through. With loop state, they shrink to their actual logic (3-5 lines each) or disappear entirely. Edge count for the iteration composite drops from ~53 to ~5 (only actual data-flow edges remain — no state threading at all).

### B. Code Node Port Defaults

`CodeExecutor` auto-provides type-appropriate defaults for missing optional input ports based on `json_schema`.

**Mapping:**
| `json_schema.type` | Default |
|---|---|
| `array` | `[]` |
| `object` | `{}` |
| `number` / `integer` | `0` |
| `string` | `""` |
| `boolean` | `False` |
| absent/unknown | `None` |

**Also inject `inputs` dict:** Code nodes get `inputs` as an additional local (alongside the individual port locals), so code can write `inputs.get("field", fallback)` for custom defaults.

**CodeExecutor change (small):**
```python
# Before namespace.update(inputs):
for port in node.input_ports:
    if port.name not in inputs and not port.required:
        inputs[port.name] = _default_for_schema(port.json_schema)
namespace["inputs"] = dict(inputs)
namespace.update(inputs)
```

**What this replaces:** Every `try: x = list(x) if isinstance(x, list) else [] / except NameError: x = []` block (~500 lines across the vibe_research workflow).

### C. Struct (Bundle) Edges

A data edge whose source port has `json_schema.type == "object"` can opt into auto-spread: the engine destructures the dict's fields into the target node's individual input ports.

**Model change on `DataEdge`:**
```python
spread: bool = False  # when True, source dict fields are spread into target input ports
```

A spread edge still requires a valid `source_port` and `target_port` in the serialized model (preserving the `EdgeBase` invariant). The `target_port` acts as the "landing port" — it must exist on the target node, and its `json_schema` should be `{type: object}`. At runtime, `resolve_inputs` populates both the landing port (full dict) and spreads individual fields. This keeps the graph valid for validation, the editor, and the mutation engine.

**PortDataStore.resolve_inputs change:**
```python
if edge.spread and isinstance(value, dict):
    inputs[edge.target_port] = value  # landing port still gets the full dict
    for k, v in value.items():
        if k not in inputs:  # explicit scalar edges take precedence
            inputs[k] = v
else:
    inputs[edge.target_port] = value
```

**Validation change in `_check_edge_ports` / `_check_data_edge_schemas`:**
- Spread edges skip per-field schema compatibility check (source is `object`, target landing port is `object` — that's sufficient)
- Port existence check still applies: both `source_port` and `target_port` must exist on their respective nodes

**Builder DSL sugar:**
```python
wf.edge(merge["state"], governor["state"], spread=True)
# or shorthand:
wf.spread_edge(merge["state"], governor["state"])
```

Note: `spread_edge` still requires a target port ref (not bare node) to satisfy the edge invariant. The convenience is the `spread=True` flag, not skipping port declaration.

**What this replaces:** 10 scalar edges `merge["results"] → governor["results"]`, `merge["iteration"] → governor["iteration"]`, ... become 1 spread edge on a single `state` port.

## Tasks

- [x] 1. Loop-scoped state — model
  - [x] 1-1. Add `state_schema: dict[str, Any] | None = None` and `state_defaults: dict[str, Any] | None = None` to `GateNode` in `models/control_flow.py`
  - [x] 1-2. Add same fields to `WhileLoopNode` (deprecated but still functional)
  - [x] 1-3. Update `dan_graph_v1` JSON contract — new optional fields serialize as nested objects
  - [x] 1-4. Update TypeScript `GateNode` type in `editor/src/types/graph.ts`

- [x] 2. Loop-scoped state — scope injection + scheduler sync
  - [x] 2-1. Add `active_loop_scope_id: str | None = None` field to `ExecutionContext` in `engine/executor.py`
  - [x] 2-2. In `GateExecutor.execute`: when `state_schema` is present and scope is empty (first call), init scope from `state_defaults` merged with resolved input port values. This must happen HERE — not in `_iterate_cycle` — because the gate's first execution is part of the normal topological pass (scheduler lines 639-647), before `_iterate_cycle` is ever called.
  - [x] 2-3. In `_execute_node` (before executor call, after `resolve_inputs`): when `context.active_loop_scope_id` is set, look up the gate's `state_schema` and merge scope fields into `inputs` for keys not already present (explicit edges take precedence). This is the core injection — ~8 lines.
  - [x] 2-4. In `_execute_node` (after executor call, after storing outputs): when `context.active_loop_scope_id` is set, merge output keys matching `state_schema` back into scope via `context.local_state.update_scope()`. This is the core write-back — ~5 lines.
  - [x] 2-5. In `_iterate_cycle`: set `context.active_loop_scope_id = gate_id` before the cycle level loop; clear it (set to `None`) after the loop exits or completes. Existing `continue_data → __input__` injection is preserved for backward compat.
  - [x] 2-6. In `GateExecutor.execute`: when `state_schema` is present, populate `condition_vars` from scope instead of doing the current dict-flattening heuristic
  - [x] 2-7. On loop exit (`done`): scheduler reads final scope via `context.local_state.get_scope(gate_id)` and sets those values as output port data on the gate node
  - [x] 2-8. Synchronize iteration counter: scheduler writes `iteration` into scope at the top of each `_iterate_cycle` turn via `context.local_state.update_scope(gate_id, {"iteration": iteration})`, so `GateExecutor` reads from scope instead of its own disconnected `gate_iteration` key

- [x] 3. Loop-scoped state — WhileLoopExecutor
  - [x] 3-1. In `WhileLoopExecutor.execute`: when `state_schema` present, use scope as `working_data` source instead of raw `inputs` dict
  - [x] 3-2. After each body sub-graph call, merge body outputs into scope (only declared keys)
  - [x] 3-3. Condition eval uses scope instead of `working_data`

- [x] 4. Code node port defaults
  - [x] 4-1. Add `_default_for_schema(json_schema: dict) -> Any` helper in `executors/code.py`
  - [x] 4-2. In `CodeExecutor._execute_inline`: before `namespace.update(inputs)`, fill missing optional port values from schema defaults
  - [x] 4-3. Inject `inputs` dict as additional local variable in code namespace
  - [x] 4-4. Same treatment in `CodeExecutor._execute_subprocess` (serialize defaults into subprocess inputs)

- [x] 5. Struct (spread) edges
  - [x] 5-1. Add `spread: bool = False` field to `DataEdge` in `models/edges.py`
  - [x] 5-2. In `PortDataStore.resolve_inputs`: when `edge.spread` is True and value is dict, populate landing `target_port` with full dict AND spread individual fields into inputs (explicit scalar edges take precedence over spread fields)
  - [x] 5-3. Add `wf.edge(..., spread=True)` support in builder `builder.py`
  - [x] 5-4. Add `wf.spread_edge(source_port_ref, target_port_ref)` convenience method (still requires target port ref to satisfy edge invariant)
  - [x] 5-5. Update `dan_graph_v1` JSON — `spread` field on data edges (default false, omitted when false)
  - [x] 5-6. Update TypeScript `DataEdge` type in `editor/src/types/graph.ts`
  - [x] 5-7. Update `_check_data_edge_schemas` in `validation/graph.py` — spread edges skip per-field schema check (source `object` → target `object` is sufficient); port existence check still enforced
  - [x] 5-8. Update `GraphMutator` edge ops to preserve `spread` field on add/edit; update mutation tool schema to accept optional `spread` boolean

- [x] 6. Builder + markdown integration
  - [x] 6-1. Builder DSL: `wf.while_loop(... state_schema={...}, state_defaults={...})` and `wf.gate(... state_schema={...})`
  - [x] 6-2. Markdown flow syntax: extend `LoopStatement` in `loader/models.py` to carry `state_schema: dict | None` and `state_defaults: dict | None`. Extend `_parse_loop` in `flow_parser.py` to parse these as kwargs: `agent_a | loop(agent_b, until: "cond", state_schema: {...}, state_defaults: {...})`. Pass through to `_compile_flow` which sets them on the generated `GateNode`. Note: gate nodes are NOT standalone `.md` files — they're auto-synthesized from `loop()` flow syntax. The `_parse_kwargs` helper may need extending to handle dict-valued arguments (currently handles strings/ints). If dict parsing proves too complex for v1, defer this sub-task and keep state_schema as builder-only.
  - [x] 6-3. Decompiler: emit `state_schema`/`state_defaults` kwargs in decompiled Python

- [x] 7. Tests
  - [x] 7-1. Unit: `GateNode` with `state_schema` serialization round-trip
  - [x] 7-2. Unit: `_default_for_schema` returns correct defaults for all JSON types
  - [x] 7-3. Unit: `CodeExecutor` fills missing optional port defaults
  - [x] 7-4. Unit: `PortDataStore.resolve_inputs` spread behavior
  - [x] 7-5. Integration: while-gate loop with `state_schema` — state persists across iterations without explicit state-threading edges
  - [x] 7-6. Integration: code node with optional ports gets schema defaults (no NameError)
  - [x] 7-7. Integration: spread edge carries struct, target node receives individual fields
  - [x] 7-8. Integration: existing vibe_research workflow still runs unchanged (backward compat)
  - [x] 7-9. Regression: gate condition eval with state_schema vs without (both paths work)
  - [x] 7-10. Unit: iteration counter in scope matches scheduler cycle count after N iterations
  - [x] 7-11. Unit: spread edge validation — port existence enforced, per-field schema check skipped
  - [x] 7-12. Unit: spread edge `resolve_inputs` — landing port gets full dict AND individual fields are spread
  - [x] 7-13. Integration: flow parser parses `state_schema`/`state_defaults` kwargs from `loop()` syntax and compiler passes them to generated `GateNode`
  - [x] 7-14. Integration: sequential 3-node body (`A → B → gate`) where A writes `results`, B reads updated `results` via scope — verifies live scope visibility between topological levels
  - [x] 7-15. Unit: `_execute_node` injects scope fields into `inputs` only when `active_loop_scope_id` is set; explicit edge values are not overwritten
  - [x] 7-16. Unit: `_execute_node` merges output keys matching `state_schema` back into scope after execution
  - [x] 7-17. Integration: `active_loop_scope_id` is `None` during first topological pass — body nodes use normal edge inputs; scope injection activates only during `_iterate_cycle`

- [x] 8. Validation: vibe_research v2
  - [x] 8-1. Write `examples/vibe_research_v2_md/` using state_schema + scope injection + code node port defaults. Copied from v1, refactored all 8 code nodes to remove try/except NameError blocks. Removed 4 scope-redundant start_year/end_year edges from department pipeline.
  - [x] 8-2. Edge count: v1 already uses state_schema (41 edges, down from ~87 pre-state_schema). v2 removes 4 more → 37. Remaining edges are all genuine data flow (orchestrator→dept assignments, dept→merge results, merge→governor). The ~87→~5 target applied to the pre-state_schema design; v1 had already captured most of that win.
  - [x] 8-3. Boilerplate: 14 try/except NameError blocks (79 code lines) → 0 (100% removed). Governor: 71→37 lines (−48%). Total code: 358→279 (−22%). The remaining code is actual logic, not boilerplate.
  - [x] 8-4. Runtime behavior identical to v1: same logic, same data flow. start_year/end_year now come from scope instead of dept_gate edges (orchestrator echoes loop-level values, so results are the same). README.md documents this.

- [x] 9. Docs
  - [x] 9-1. Update `llm-api-guide.md` — document state_schema, code node defaults, spread edges
  - [x] 9-2. Update `architecture.md` — document loop state as activated Layer 2
  - [x] 9-3. Update `changelog.md`
  - [x] 9-4. Update parent plan `7-core-hardening.md` — add 7-6 to sub-plan table

## Decisions

- **Loop state scope:** `LocalStateManager` (Layer 2). Not `SharedContextStore` (Layer 3) — loop state is private to the loop, not graph-wide. This matches the architecture: "mutable within scope, invisible to parent."
- **State write policy:** Only keys declared in `state_schema` are merged from body outputs into scope. Undeclared keys in body output are ignored for state purposes (still flow via normal edges). Prevents accidental state pollution.
- **Scope injection mechanism:** The scheduler injects scope fields into `inputs` for **all cycle nodes** inside `_execute_node`, not just the cycle entry node. After each node completes, the scheduler merges output keys matching `state_schema` back into scope (also in `_execute_node`). This makes scope a live, per-node read/write store within the cycle. The alternative — spread edges from the entry node — was rejected because spread edges are snapshots, not live references: in a sequential body (`A → B → C`), B's state updates aren't visible to C via A's spread.
- **No executor coupling:** Scope injection and write-back happen entirely in `_execute_node` (~15 lines). Executors receive state fields as regular `inputs` entries and output normally. No executor code (CodeExecutor, LLMExecutor, etc.) needs to know about loop state. The Lego block contract is preserved.
- **Scope init timing:** `GateExecutor.execute` initializes the scope on first call (when scope is empty and `state_schema` is present). The gate's first execution happens in the normal topological pass (scheduler `_execute_with_cycles`, lines 639-647), before `_iterate_cycle` is ever called. The executor must be the one to init, not the scheduler.
- **First pass vs. re-execution:** On the first topological pass, `active_loop_scope_id` is `None` — body nodes use normal edge-based inputs. Scope injection only activates during `_iterate_cycle` re-executions. The first pass establishes initial values; subsequent iterations use scope as source of truth.
- **Parallel level safety:** Nodes in the same topological level run via `asyncio.gather`. They all read the scope snapshot from before the level started. After the level completes, their outputs are merged (last-write-wins within a level). This is safe: asyncio is cooperative single-threaded, reads don't race. If two same-level nodes write the same state key, we can add a validation warning as a follow-up.
- **Iteration counter synchronization:** The scheduler writes `"iteration": N` into scope at the start of each `_iterate_cycle` turn. `GateExecutor` reads `iteration` from scope instead of maintaining its own `gate_iteration`. This unifies the two previously disconnected counters into one source of truth.
- **Nested loops:** For v1, `active_loop_scope_id` is a single `str | None`. Nested loops (gate inside gate) are out of scope. If needed later, `active_loop_scope_id` becomes a stack `list[str]`. The scope injection logic is the same — it just reads from the innermost scope.
- **Spread edge invariant:** Spread edges still require valid `source_port` and `target_port` (preserving `EdgeBase` contract). The `target_port` is a "landing port" that receives the full dict; individual fields are additionally spread into matching input ports. This keeps the graph structurally valid for the editor, validator, and mutation engine. `wf.spread_edge()` requires a target port ref, not a bare node.
- **Spread validation:** Spread edges enforce `object` type on both source and target landing ports (when schemas are present), but skip per-field type checking. Port existence checks still apply. This avoids the combinatorial complexity of validating dynamic field-to-port mappings at build time while catching obvious type mismatches.
- **Explicit edges take precedence:** If both a spread edge and an explicit scalar edge target the same port, the explicit edge wins. Spread is a fallback, not an override.
- **Backward compatible:** All three features are opt-in. `state_schema=None` (default) preserves current behavior. `spread=False` (default) preserves current edge semantics. Code node defaults only apply to ports with `required=False`. Zero breaking changes.
- **Code node `inputs` dict:** Injected alongside raw locals for backward compat. Existing code that uses raw variable names still works. New code can use `inputs.get(...)` for explicit access.
- **Markdown `state_schema` format:** Extended kwargs on `loop()` flow syntax: `state: '{...}'` and `defaults: '{...}'` (JSON strings). The flow parser's existing `_parse_kwargs` extracts quoted-string values; `_parse_loop` decodes them with `json.loads` and passes to `LoopStatement`. The compiler passes `state_schema`/`state_defaults` to the generated `GateNode`. No dict-valued kwarg parsing needed — JSON-in-string is sufficient.
- **ForEach + parent state:** Out of scope for v1. ForEach body nodes can't read parent loop state in this plan. That would require cross-scope visibility rules (Phase 10 territory). ForEach items still passed via normal data edges.
- **Editor UI for state_schema:** Out of scope for v1. State schema authored in Python DSL or `loop()` flow syntax. Visual editor shows the field but doesn't provide a visual schema builder. Can be added in a follow-up.

## Notes

- `GateExecutor` currently has a hardcoded `try_more` default (line 103 of `control_flow.py`). With loop state, this hack is replaced by `state_defaults: {"try_more": true}` — the gate reads from scope, no special-casing needed.
- The scope injection in `_execute_node` is symmetric with the existing context edge mechanism (`_read_context_edges` at line 833, `_write_context_edges` at line 866). Context edges read/write `SharedContextStore` (Layer 3); scope injection reads/writes `LocalStateManager` (Layer 2). Same pattern, different scope.
- The `_iterate_cycle` method already clears port data and re-injects virtual inputs on each iteration. The `continue_data → __input__` path is preserved for backward compat. For state_schema loops, scope injection in `_execute_node` is the authoritative path — `continue_data` still flows but the scope is the source of truth.
- `WhileLoopExecutor` already manages `working_data` and `scope` per iteration. Adding state_schema is a natural extension — scope becomes the authoritative working data instead of a bookkeeping sidecar.
- The vibe_research v2 validation (task 8) is the acceptance test. If the workflow produces the same results with ~5 edges instead of ~87, the design works. The target dropped from ~15 to ~5 because scope injection eliminates ALL intra-cycle state-threading edges, not just the entry→gate back-edges.
- **Design evolution (2026-02-27):** Originally the plan had scope injection only into the cycle entry node, with interior body nodes receiving state via spread edges. Code review revealed this is fundamentally broken for multi-node sequential bodies: spread edges are snapshots, so node B can't see node A's state update via the entry node's spread. Switched to direct scope injection in `_execute_node` for all cycle nodes — ~15 lines of scheduler code, zero executor changes.
- **Resolved review findings (2026-02-27):** (1) Markdown integration: corrected — gate nodes are synthesized from `loop()` flow syntax, not standalone `.md` files; state_schema goes as flow kwargs or deferred to builder-only. (2) `spread_edge` requires a target port ref, not bare node — preserves edge invariant. (3) Iteration counter unified via scheduler writing into scope. (4) Scope init timing: in `GateExecutor.execute` (first call) — the gate's first execution precedes `_iterate_cycle`. (5) Scope injection expanded to all cycle nodes via `_execute_node`, not just entry node.
- **Post-review cleanup (2026-03-02):** After migrating `plot_one.md`/`write_csv.md` to code nodes in `examples/vibe_research_v2_md/`, removed stale legacy tool registrations (`plot_backtest`, `save_grid_csv`) from `run_multi_dept.py` so v2 runtime wiring matches the new node pattern.
