# 1-5: Workflow Builder API

**Status:** completed
**Goal:** Fluent Python DSL (`dan.builder`) that compiles to `Graph` models / `dan_graph_v1` JSON. Four connection mechanisms (f-string magic, `>>` chaining, PortRef passing, explicit edge). Full decompiler for round-tripping with the visual editor.

## Tasks
- [x] 1. Module structure
  - [x] 1-1. Create `src/dan/builder/` package with `__init__.py`, `refs.py`, `builder.py`, `compiler.py`, `decompiler.py`
- [x] 2. Node-type output contract map
  - [x] 2-1. Define `DEFAULT_OUTPUT_PORTS` dict mapping each `node_type` to its actual runtime output port names (e.g. `llm_operator` -> `text` without schema, `result` with schema; `for_each` -> `results`; `if_else` -> `branch`; etc.)
  - [x] 2-2. Compiler uses this map instead of a universal `result` default
- [x] 3. Core reference types (`refs.py`)
  - [x] 3-1. `NodeRef` — `__format__` (marker emission), `__rshift__` (>> chaining), `__getitem__` (port subscript), `__repr__`
  - [x] 3-2. `PortRef` — immutable `(node_id, port_name)` pair, `__format__` marker emission
  - [x] 3-3. Sanitized template variable mapping — decouple placeholder tokens from raw node IDs to guarantee valid `str.format_map` keys (e.g. hyphens/dots in node IDs -> underscored aliases)
- [x] 4. WorkflowBuilder (`builder.py`)
  - [x] 4-1. `workflow()` factory function
  - [x] 4-2. Node creation methods: `.llm()`, `.tool()`, `.code()`, `.if_else()`, `.reduce()`, `.router()`, `.human_in_the_loop()`
  - [x] 4-3. `.edge()` explicit wiring, `>>` operator registration
  - [x] 4-4. Internal state tracking: pending nodes, pending edges, pending sub-graphs, marker registry
- [x] 5. Sub-graph context managers
  - [x] 5-1. `.while_loop()` — yields sub-`WorkflowBuilder` with `.input` PortRef
  - [x] 5-2. `.for_each()` — yields sub-`WorkflowBuilder` with `.item` PortRef
  - [x] 5-3. `.composite()` — yields sub-`WorkflowBuilder` with configurable I/O mappings
  - [x] 5-4. On `__exit__`, compile sub-builder into `Graph` and register in `parent.sub_graphs`
- [x] 6. Compiler (`compiler.py`)
  - [x] 6-1. Resolve f-string markers (`<<dan:node_id:port_name>>`) in prompt templates
  - [x] 6-2. Auto-generate `InputPort`/`OutputPort` definitions using output contract map
  - [x] 6-3. Auto-generate `DataEdge` objects from markers, `>>` registrations, and PortRef connections
  - [x] 6-4. Assemble `Graph` with `sub_graphs`, `entry_points`, `exit_points`, `shared_context`
  - [x] 6-5. Call `validate_graph()` — raise `BuildError` with all validation errors
- [x] 7. Decompiler (`decompiler.py`)
  - [x] 7-1. `decompile(graph: Graph) -> str` — topological sort, variable name generation, sub-graph detection
  - [x] 7-2. Chain detection → emit `>>` operator; non-trivial wiring → emit `wf.edge()`
  - [x] 7-3. Lossless preservation criteria: `ui`, `metadata`, `shared_context`, `artifact_refs`, `ControlEdge`, `ContextEdge`, deterministic output ordering
  - [x] 7-4. Output is a complete, importable Python module string
- [x] 8. Serialization helpers
  - [x] 8-1. `.to_json()`, `.to_dict()`, `.to_graph()` on `WorkflowBuilder`
  - [x] 8-2. `from_json()` / `from_dict()` class methods (decompile + rebuild)
- [x] 9. Unit tests
  - [x] 9-1. `NodeRef`/`PortRef` behavior: `__format__`, `>>`, `__getitem__`, sanitized aliases
  - [x] 9-2. Compiler: marker resolution, port generation, edge generation, each node type
  - [x] 9-3. Sub-graph context managers: while_loop, for_each, composite
  - [x] 9-4. Decompiler: round-trip (decompile → exec → build → compare Graph)
- [x] 10. Integration tests
  - [x] 10-1. Paper-writing workflow via builder (~30 lines), verify compiled Graph matches manual construction
  - [x] 10-2. Run compiled graph through Engine (mock LLM)
  - [x] 10-3. Decompile-recompile round-trip assertion (structural equality)
  - [x] 10-4. Editor round-trip golden test: builder → Graph JSON → editor adapter → Graph JSON → decompile → rebuild
- [x] 11. Update docs
  - [x] 11-1. `docs/architecture.md` — builder module layout
  - [x] 11-2. `docs/todo.md` — mark Phase 1.5 in-progress / complete
  - [x] 11-3. `docs/changelog.md` — append entry

## API Design

### Target Usage (paper-writing example)

```python
from dan.builder import workflow

paper = workflow("paper_writing")

ideas = paper.llm("idea_gen", model="claude-opus-4", prompt="Generate ideas about {topic}")
outline = paper.llm("planner", prompt=f"Create outline for: {ideas}")

with paper.for_each("section_writers", items=outline["sections"], parallelism=4) as body:
    writer = body.llm("writer", prompt=f"Write section: {body.item}")

assembled = paper.reduce("assembler", reducer="concatenate")

with paper.while_loop("review_revise", condition="verdict != 'accept'", max_iterations=5) as loop:
    reviewer = loop.llm("reviewer", model="claude-opus-4", prompt=f"Review: {loop.input}")
    reviser = loop.llm("reviser", prompt=f"Revise based on: {reviewer}")
    reviewer >> reviser

graph = paper.build()       # -> Graph (validated)
json_str = paper.to_json()  # -> dan_graph_v1 JSON string
```

### Four Connection Mechanisms

1. **f-string magic**: `prompt=f"Use: {ideas}"` — `NodeRef.__format__` emits a compile-time marker `<<dan:idea_gen:result>>`. At `build()`, the compiler parses prompt templates, creates DataEdges, and replaces markers with sanitized input port aliases.
2. **`>>` operator**: `ideas >> outline` — creates DataEdge from default output port to default input port. Returns the RHS NodeRef for further chaining: `a >> b >> c`.
3. **PortRef passing**: `items=outline["sections"]` — NodeRef subscript returns a PortRef. Builder methods that accept data sources (e.g., `for_each(items=...)`) resolve PortRefs at compile time.
4. **Explicit edge**: `paper.edge(ideas["text"], outline["input"])` — fully explicit source-port to target-port wiring.

### Sub-Graph Builders (Context Managers)

On context-manager exit, the sub-builder's nodes/edges are compiled into a `Graph` stored in `parent.sub_graphs[key]`.

### Decompiler (Graph -> Python Code)

`decompile(graph: Graph) -> str` produces executable Python that reconstructs the graph via the builder API.

Lossless criteria:
- All `ui`, `metadata`, `shared_context`, `artifact_refs` preserved
- `ControlEdge` and `ContextEdge` emitted (not just DataEdge)
- Deterministic output ordering (topological, then alphabetical tie-break)

### Module Structure

```
src/dan/builder/
    __init__.py       # Public API: workflow(), WorkflowBuilder, NodeRef, PortRef, decompile()
    refs.py           # NodeRef, PortRef — proxy objects with __format__, __rshift__, __getitem__
    builder.py        # WorkflowBuilder — node creation, edge registration, context managers
    compiler.py       # Compile builder state -> Graph model (marker resolution, port/edge generation)
    decompiler.py     # Graph -> Python builder code string
```

## Design Decisions

- **Marker format**: `<<dan:node_id:port_name>>` in prompt strings. Regex-parseable, unlikely to collide with user content.
- **Node-type output contract map**: Each node type has a known default output port name matching the runtime executor outputs (e.g. `llm_operator` -> `text` when no schema, `if_else` -> `branch`, `for_each` -> `results`). The compiler uses this map — NOT a universal `result` default.
- **Sanitized template aliases**: Auto-generated input port names are sanitized from node IDs to be valid Python `str.format_map` keys. Hyphens, dots, and other special chars are replaced with underscores. The alias mapping is stored so the decompiler can round-trip.
- **Validation on build**: `build()` calls `validate_graph()` and raises `BuildError` with all validation errors.
- **Immutable refs**: `NodeRef` and `PortRef` are lightweight immutable objects. They don't hold data — they're compile-time references resolved by the compiler.
- **Lossless decompiler**: The decompiler preserves all graph metadata, edge types, shared context, and artifacts for full editor round-trip fidelity.

## Notes
- (filled in during execution)
