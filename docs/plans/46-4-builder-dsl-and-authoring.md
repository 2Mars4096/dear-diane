# 46-4: Builder DSL & Authoring Integration

**Parent:** [46-universal-worker-primitive](46-universal-worker-primitive.md)
**Status:** not-started
**Goal:** Add a `worker()` method to the `WorkflowBuilder` DSL, update the compiler/decompiler for round-trip, and ensure Workers are first-class in all authoring surfaces without losing contract metadata.

## Design: One Primary Method

The builder gets one new primary method: `worker()`. It creates a Worker with whatever config you pass. Existing methods (`llm()`, `tool()`, `code()`, etc.) keep working unchanged — they're convenience aliases.

```python
wf = workflow("example")

# Direct worker creation — most flexible
a = wf.worker("fetch", tool_ids=["web_search"], persona="Find relevant data")
b = wf.worker("analyze", model="claude-4", persona="Analyze the data")
c = wf.worker("format", code="result = json.dumps(data, indent=2)")
a >> b >> c

# Existing methods still work (backward compat)
d = wf.llm("summarize", model="claude-4", prompt="Summarize: {data}")
```

Squad-building convenience methods (`squad()`, `pipeline()`, `fan_out()`) are deferred — they're sugar over `worker()` + `>>` + `worker_scope()` and add complexity before the base is proven.

## Tasks

- [ ] 1. Add `worker()` to `WorkflowBuilder`
  - [ ] 1-1. Signature: `worker(self, node_id, *, role="", persona="", model=None, tool_ids=None, code="", authority="leaf", llm=None, control_flow=None, **kwargs) -> NodeRef`
  - [ ] 1-2. Construct `Worker` node from arguments
  - [ ] 1-3. Auto-create `LLMHints` from `llm` kwarg (accepts dict or `LLMHints` instance)
  - [ ] 1-4. Auto-create `ControlFlowConfig` from `control_flow` kwarg
  - [ ] 1-5. Add to `_nodes` via `_PendingNode`
  - [ ] 1-6. Return `NodeRef` for `>>` chaining and f-string interpolation
  - [ ] 1-7. Keep `description`, input/output port descriptions, and schemas as first-class authored fields on `worker()` rather than burying them in opaque metadata
- [ ] 2. Add `worker_scope()` context manager for composite Workers
  - [ ] 2-1. Creates a sub-graph builder for the Worker's `body_graph`
  - [ ] 2-2. Same pattern as existing `while_loop()` / `for_each()` context managers
  - [ ] 2-3. Supports `sub_workers` variant: `with wf.worker_scope("mgr", sub_workers=["team_a", "team_b"]):`
  - [ ] 2-4. `wf.entry_input` and `wf.entry_item` available inside scope
- [ ] 3. Update compiler (`compiler.py`)
  - [ ] 3-1. Handle `Worker` pending nodes → `Worker` graph nodes
  - [ ] 3-2. Handle `worker_scope()` sub-graphs → `sub_graphs` dict entries
  - [ ] 3-3. Wire `body_graph` / `sub_workers` keys correctly
- [ ] 4. Update decompiler (`decompiler.py`)
  - [ ] 4-1. Detect `Worker` nodes in graph JSON → emit `wf.worker(...)` calls
  - [ ] 4-2. For simple Workers matching a known role (pure LLM, pure tool): optionally emit the convenience alias for readability
  - [ ] 4-3. For composite Workers: emit `with wf.worker_scope(...)` blocks
  - [ ] 4-4. Preserve all config fields through round-trip
  - [ ] 4-5. Preserve worker contract metadata (`description`, `role`, `persona`, port descriptions/schemas, boundary schemas) through round-trip without lossy fallback formatting
- [ ] 5. Round-trip tests
  - [ ] 5-1. `worker()` → `build()` → JSON → decompile → recompile → compare
  - [ ] 5-2. `worker()` + `worker_scope()` → round-trip
  - [ ] 5-3. Mixed graph: some `worker()`, some `llm()`/`tool()` → round-trip
  - [ ] 5-4. Worker with `llm` hints → round-trip preserves all LLM config
  - [ ] 5-5. Worker with `control_flow` → round-trip preserves gate config
  - [ ] 5-6. Worker contract metadata survives build → JSON → decompile → recompile unchanged, because plan 47 will later consume those fields for lint-config autogen
- [ ] 6. Visual editor palette preparation (backend only)
  - [ ] 6-1. Add `"worker"` to palette category registry
  - [ ] 6-2. Default port templates: `data` input, `result` output
  - [ ] 6-3. Worker config metadata for editor: field groups (identity, capability, composition)
  - [ ] 6-4. Defer full editor config panel to a later plan — Workers display as generic nodes initially
  - [ ] 6-5. Expose contract-oriented editor metadata (node description, port descriptions, schemas) as canonical Worker fields instead of legacy-node-specific affordances

## Likely Files

**Modified:**
- `src/dan/builder/builder.py` — add `worker()`, `worker_scope()`
- `src/dan/builder/compiler.py` — handle Worker pending nodes
- `src/dan/builder/decompiler.py` — emit Worker DSL

**Test:**
- `tests/test_worker/test_builder.py` (new) — builder + round-trip tests

## Decisions

- **`squad()/pipeline()/fan_out()` deferred.** These are sugar over `worker()` + `>>` and add complexity before the base is solid. They can be added in 46-5 or a future plan once Worker-based workflows are proven.
- **Decompiler heuristic:** When a Worker exactly matches a known role (model set, no code, no body_graph = LLM agent), emit `wf.llm(...)` for readability in decompiled code. Otherwise emit `wf.worker(...)`. This keeps backward-compatible decompiled output familiar.
- **`llm` kwarg accepts dict.** `wf.worker("a", model="claude-4", llm={"temperature": 0.3})` auto-wraps the dict into `LLMHints`. Ergonomic for the builder DSL.
- **Round-trip fidelity matters beyond ergonomics.** If builder/compiler/decompiler lose Worker contract metadata, the follow-on linter plan will be forced to infer lint configs from degraded graph JSON. Preserve the authored surface exactly.

## Notes

- The builder's `>>` operator and f-string system work with `NodeRef`/`PortRef`. Workers produce `NodeRef` exactly like existing methods. No changes to the ref system needed.
- The `worker_scope()` context manager follows the exact same pattern as `while_loop()` and `for_each()` scoped builders. The compiler already handles scoped sub-graphs — this just adds a new scope type.
- Task 6 (visual editor) is backend-side metadata only. The full editor config panel (role picker, persona editor, tool multi-select, code editor) is a separate UI plan. Workers render as generic nodes with the existing node display until then.
