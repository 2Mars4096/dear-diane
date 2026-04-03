# 46-4: Builder DSL & Authoring Integration

**Parent:** [46-universal-worker-primitive](46-universal-worker-primitive.md)
**Status:** completed
**Goal:** Add a `worker()` method to the `WorkflowBuilder` DSL, update the compiler/decompiler for round-trip, and ensure Workers are first-class in all authoring surfaces without losing contract metadata, shared-context references, or execution/governance semantics.

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

- [x] 1. Add `worker()` to `WorkflowBuilder`
  - [x] 1-1. Signature: `worker(self, node_id, *, role="", instruction="", persona="", model=None, tool_ids=None, code="", authority="leaf", context=None, authority_policy=None, execution=None, llm=None, llm_hints=None, **kwargs) -> NodeRef`
  - [x] 1-2. Construct `Worker` node from arguments
  - [x] 1-3. Auto-create `LLMHints` from `llm` kwarg (accepts dict or `LLMHints` instance)
  - [x] 1-4. Auto-create `ControlFlowConfig` from `control_flow` kwarg
  - [x] 1-5. Add to `_nodes` via `_PendingNode`
  - [x] 1-6. Return `NodeRef` for `>>` chaining and f-string interpolation
  - [x] 1-7. Keep `description`, input/output port descriptions, and schemas as first-class authored fields on `worker()` rather than burying them in opaque metadata
  - [x] 1-8. Expose shared refs directly: instruction-profile refs, memory-policy refs, context-bundle refs, provider/retry refs, authority-policy refs, and execution/lock semantics must be authorable without hidden metadata escape hatches
- [x] 2. Add `worker_scope()` context manager for composite Workers
  - [x] 2-1. Creates a sub-graph builder for the Worker's `body_graph`
  - [x] 2-2. Same pattern as existing `while_loop()` / `for_each()` context managers
  - [x] 2-3. Supports named sub-worker graphs inside the scope: `with wf.worker_scope("mgr") as mgr: with mgr.sub_worker("team_a"): ...`
  - [x] 2-4. `wf.entry_input` and `wf.entry_item` available inside scope
- [x] 3. Update compiler (`compiler.py`)
  - [x] 3-1. Handle `Worker` pending nodes → `Worker` graph nodes
  - [x] 3-2. Handle `worker_scope()` sub-graphs → `sub_graphs` dict entries
  - [x] 3-3. Wire `body_graph` / `sub_workers` keys correctly
- [x] 4. Update decompiler (`decompiler.py`)
  - [x] 4-1. Detect `Worker` nodes in graph JSON → emit `wf.worker(...)` calls
  - [x] 4-2. For simple Workers matching a known role (pure LLM, pure tool): optionally emit the convenience alias for readability
  - [x] 4-3. For composite Workers: emit `with wf.worker_scope(...)` blocks
  - [x] 4-4. Preserve all config fields through round-trip
  - [x] 4-5. Preserve worker contract metadata (`description`, `role`, `persona`, port descriptions/schemas, boundary schemas) through round-trip without lossy fallback formatting
  - [x] 4-6. Preserve shared-context refs and authority/execution policy fields through round-trip without degrading them into opaque metadata blobs
- [x] 5. Round-trip tests
  - [x] 5-1. `worker()` → `build()` → JSON → decompile → recompile → compare
  - [x] 5-2. `worker()` + `worker_scope()` → round-trip
  - [x] 5-3. Mixed graph: some `worker()`, some `llm()`/`tool()` → round-trip
  - [x] 5-4. Worker with `llm` hints → round-trip preserves all LLM config
  - [x] 5-5. Worker with `control_flow` → round-trip preserves gate config
  - [x] 5-6. Worker contract metadata survives build → JSON → decompile → recompile unchanged, because plan 47 will later consume those fields for lint-config autogen
- [x] 6. Visual editor palette and authoring surface
  - [x] 6-1. Add `"worker"` to palette category registry
  - [x] 6-2. Default port templates: `input` input, `result` output
  - [x] 6-3. Worker config metadata for editor: field groups (identity, capability, composition)
  - [x] 6-4. Land the dedicated Worker config panel in the editor so Workers no longer fall back to generic raw-field rendering
  - [x] 6-5. Expose contract-oriented editor metadata (node description, port descriptions, schemas) as canonical Worker fields instead of legacy-node-specific affordances

## Likely Files

**Modified:**
- `src/dan/builder/builder.py` — add `worker()`, `worker_scope()`
- `src/dan/builder/compiler.py` — handle Worker pending nodes
- `src/dan/builder/decompiler.py` — emit Worker DSL

**Test:**
- `tests/test_worker/test_builder.py` (new) — builder + round-trip tests

## Decisions

- **`squad()/pipeline()/fan_out()` deferred.** These are sugar over `worker()` + `>>` and add complexity before the base is solid. They can be added in 46-5 or a future plan once Worker-based workflows are proven.
- **Decompiler heuristic:** Keep `decompile(graph)` canonical and lossless by default. The readability pass is explicitly opt-in via `decompile(graph, use_convenience_aliases=True)`, and only simple leaf Workers are lowered to familiar aliases such as `wf.llm(...)`, `wf.tool(...)`, `wf.code(...)`, `wf.input_node(...)`, `wf.reduce(...)`, `wf.rag(...)`, `wf.reflection(...)`, `wf.human(...)`, `wf.human_in_the_loop(...)`, or `wf.vote(...)`.
- **`llm` kwarg accepts dict.** `wf.worker("a", model="claude-4", llm={"temperature": 0.3})` auto-wraps the dict into `LLMHints`. Ergonomic for the builder DSL.
- **Round-trip fidelity matters beyond ergonomics.** If builder/compiler/decompiler lose Worker contract metadata, the follow-on linter plan will be forced to infer lint configs from degraded graph JSON. Preserve the authored surface exactly.
- **Shared references are first-class authoring fields.** If a Worker depends on an instruction profile, memory policy, or context bundle, the builder should expose that explicitly rather than hiding it inside ad hoc metadata dicts.

## Notes

- The builder's `>>` operator and f-string system work with `NodeRef`/`PortRef`. Workers produce `NodeRef` exactly like existing methods. No changes to the ref system needed.
- The `worker_scope()` context manager follows the exact same pattern as `while_loop()` and `for_each()` scoped builders. The compiler already handles scoped sub-graphs — this just adds a new scope type.
- Task 6 is no longer backend-only in this checkout. The editor now has the first dedicated Worker config surface, while deeper ergonomics like richer control-flow editing and more opinionated shared-ref pickers remain follow-up work.
- The editor type/palette surface now includes `worker` as a first-class palette entry with canonical `input` / `result` port defaults and a dedicated icon. The config panel also now exposes grouped Worker identity/capability/policy/composition fields plus contract-oriented port description/schema editing.
- The current landed `worker_scope()` surface exposes both the short scoped-builder refs (`.input`, `.item`) and the explicit aliases (`.entry_input`, `.entry_item`), plus a nested `.sub_worker(name)` authoring API.
- The convenience alias-emitting decompiler pass is now opt-in and readability-oriented. Canonical `decompile(graph)` remains the lossless Worker-preserving path in this checkout, while the readability mode now covers the simple specialized Worker leaves that correspond cleanly to existing compute aliases (`input_node`, `reduce`, `rag`, `reflection`, `human`, `human_in_the_loop`, and `vote`) in addition to the earlier `llm` / `tool` / `code` cases.
- The editor now gives `worker` a dedicated config section with grouped identity, capability, policy, and composition fields, and the shared port editor now exposes per-port descriptions and JSON schemas as first-class contract fields instead of leaving them implicit.
- Worker authoring now also preserves a lightweight `control_flow` sub-model through `wf.worker(...)`, `wf.worker_scope(...)`, build, JSON, decompile, and rebuild. That gives Worker a first-class gate-style contract surface without claiming loops/gates should stop delegating to specialized runtime executors.
