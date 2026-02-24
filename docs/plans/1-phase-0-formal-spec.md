# 1: Phase 0 Formal Spec

**Status:** completed
**Goal:** Define the formal Deep Agent Network abstraction as executable Python types and a versioned JSON graph contract shared with the future TypeScript visual editor.

## Tasks
- [x] 1. Scaffold the Python project baseline
  - [x] 1-1. Create `pyproject.toml` with `pydantic` and `pytest`
  - [x] 1-2. Create `src/dan/` and `tests/` package structure
- [x] 2. Implement core type system models
  - [x] 2-1. Define port models (`InputPort`, `OutputPort`) in `models/ports.py`
  - [x] 2-2. Define node models (LLMOperator, ToolOperator, CodeOperator) in `models/nodes.py`
  - [x] 2-3. Define control-flow nodes (IfElse, WhileLoop, ForEach, Reduce, Router, HumanInTheLoop, Composite) in `models/control_flow.py`
  - [x] 2-4. Define edge models (DataEdge, ControlEdge, ContextEdge) in `models/edges.py`
  - [x] 2-5. Define graph model with discriminated unions and node registry in `models/graph.py` and `registry.py`
- [x] 3. Implement context management models (in `models/context.py`)
  - [x] 3-1. `NodeLocalState` — private working memory for composite nodes
  - [x] 3-2. `SharedContextDeclaration` — namespaced key-value blackboard with `read_set`/`write_set`
  - [x] 3-3. `ArtifactRef` — URI-based references to large objects with content hashing
  - [x] 3-4. `ContextProjection` — scope boundary mapping (context keys, local state keys, artifact URIs)
  - [x] 3-5. Composite node contract fields on WhileLoop, ForEach, Composite nodes
  - [x] 3-6. Context policies: `CompactionRule`, `MergeStrategy`, `FailurePolicy`, `ContextMode`
- [x] 4. Define the canonical graph serialization contract
  - [x] 4-1. `dan_graph_v1` JSON via Pydantic `model_dump()` / `model_validate()`
  - [x] 4-2. Editor-facing fields: `Position`, `ui` dict on nodes and edges
  - [x] 4-3. Format versioning via `Graph.version` field
  - [x] 4-4. Context layer serialization (local state, shared context declarations, artifact refs)
- [x] 5. Implement validation rules
  - [x] 5-1. Port schema compatibility (`validation/schema.py` — MVP structural check)
  - [x] 5-2. Graph well-formedness (`validation/graph.py` — entry/exit, reachability, required ports, sub-graph refs)
  - [x] 5-3. Shared context validation (undeclared writes detection)
  - [x] 5-4. Data-edge cycle detection (cycles only through loop nodes)
- [x] 6. Prove the spec with tests (75 tests, all passing)
  - [x] 6-1. Paper-writing workflow example with review-revise loop, context projections, and compaction
  - [x] 6-2. Serialization round-trip tests (model → JSON → model) for all types
  - [x] 6-3. Context model tests (local state, shared context, artifact refs, projections)
- [x] 7. Sync tracking docs
  - [x] 7-1. Update `docs/todo.md` and this plan status
  - [x] 7-2. Append `docs/changelog.md`
  - [x] 7-3. Update `docs/architecture.md`

## Decisions
- Python core engine + TypeScript visual editor.
- Visual editor moved up to immediately follow engine in roadmap execution.
- Formal spec source of truth is Python models (Pydantic), with no separate normative spec file.
- Canonical shared serialization format is `dan_graph_v1`.
- Control-flow and composite internals use named sub-graph references.
- Context edges require explicit mode: `read`, `write`, or `append`.
- **Four-layer context model adopted**: edge data (bounded), node-local state (scoped), shared context store (blackboard), artifact store (by reference). See `architecture.md`.
- **Context projection at scope boundaries**: each composite/loop node defines projections that extract minimal views for each consumer (loop controller, inner workers, parent graph).
- **Composite node contract**: every composite/loop node must declare `external_input/output_schema`, `control_state`, `local_working_set`, `read_set`/`write_set`, and `compaction_rule`.

## Notes
- Keep Phase 0 focused on contracts and validation, not runtime scheduling optimizations.
- Favor a clear MVP schema-compatibility policy over full JSON-Schema theorem-proving.
- Context projection functions can start as simple key-selection; LLM-powered summarization gates are a Phase 1+ concern.
