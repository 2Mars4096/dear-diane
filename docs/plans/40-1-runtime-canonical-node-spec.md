# 40-1: Runtime Canonical Node Spec

**Parent:** [40-canonical-node-taxonomy](40-canonical-node-taxonomy.md)
**Status:** completed
**Goal:** Define the authoritative runtime node catalog, classify aliases vs canonical primitives, and establish one single-source metadata contract for node semantics.

## Context

Today the runtime node taxonomy is spread across multiple hand-maintained lists and models:

- `src/dan/models/graph.py` is the real deserialization contract.
- `src/dan/registry.py` is only discovery, but looks authoritative to callers.
- `src/dan/validation/graph.py` keeps its own `_KNOWN_NODE_TYPES` / `_COMPOSITE_NODE_TYPES`.
- Executors, decompilers, prompts, and TypeScript types each preserve their own subsets.
- Concrete drift already exists: the registry omits some runtime-capable node kinds, validator known-node lists omit some schema-valid kinds such as newer advanced primitives, and loader/decompiler support is narrower than the runtime union.
- Deprecated forms (`if_else`, `while_loop`, `human_in_the_loop`) still exist beside newer canonical candidates (`gate`, `human`), so the system currently has names that are both "supported" and semantically superseded.

Before any implementation cleanup, DAN needs one explicit answer to: **what is a runtime primitive, what is only a compatibility alias, and what is only an authoring convenience?**

## Tasks

- [x] 1. Inventory the current runtime taxonomy and drift points
  - [x] 1-1. Audit node kinds declared in `src/dan/models/graph.py`, `src/dan/models/control_flow.py`, and `src/dan/models/nodes.py`.
  - [x] 1-2. Audit drift across `src/dan/registry.py`, `src/dan/validation/graph.py`, scheduler executor registration, loader/decompiler support, and any runtime node-type sets.
  - [x] 1-3. Produce one explicit inventory table: `node_type`, owning model, executor, validator support, decompiler support, authoring exposure, status.
  - [x] 1-4. Classify every currently visible node form into one of four buckets: `canonical primitive`, `deprecated alias`, `authoring-only pseudo-type`, or `macro/template only`.
  - [x] 1-5. First runtime-alignment slice: `NodeTypeRegistry` now registers `human`, `agent_team`, and `vote`, matching the `Graph` runtime union for those built-ins.
  - [x] 1-6. First runtime-alignment slice: validator known/composite sets now include `reflection` / `goal_loop`, and sub-graph reference checks now cover `goal_loop`, `parallel_subagents`, `orchestrator`, and `agent_team`.
  - [x] 1-7. First runtime-alignment slice: scheduler slot-bypass taxonomy now uses canonical `rag_operator` instead of stale `rag`.

- [x] 2. Define the canonical runtime primitive catalog
  - [x] 2-1. Confirm the primitive status of `parallel_subagents`, `agent_team`, `vote`, `goal_loop`, and `reflection`.
  - [x] 2-2. Decide the canonical status of the rest of the runtime set (`llm_operator`, `tool_operator`, `code_operator`, `rag_operator`, `input`, `gate`, `for_each`, `router`, `reduce`, `human`, `validator`, `composite`, and `orchestrator`).
  - [x] 2-3. Classify `if_else`, `while_loop`, and `human_in_the_loop` explicitly as canonical, deprecated alias, or removed/migrated forms.
  - [x] 2-4. For each canonical primitive, specify required config fields, default ports, invariants, and whether it is a composite/sub-graph-bearing node.
  - [x] 2-5. Make an explicit call on `orchestrator`: canonical runtime primitive, compatibility holdover, or future macro/composite pattern.

- [x] 3. Define the single-source node-definition contract
  - [x] 3-1. Choose the authoritative source for node metadata: model-local declarations, a dedicated node-definition table, or generated metadata derived from models.
  - [x] 3-2. Ensure the chosen source can drive validator known-node sets, registry discovery, decompiler support tables, mutator defaults, and editor/runtime type metadata. *(Partially landed 2026-03-20: registry, validator, mutation prompt node enums, editor runtime node literals, and builder compiler coverage are now explicitly guarded against the canonical runtime taxonomy. Builder/loader port/export policy tables still remain separate by design and are regression-checked rather than blindly unified.)*
  - [x] 3-3. Document which information is authoritative per node: schema fields, ports, execution semantics, compatibility aliases, and authoring labels.
  - [x] 3-4. Define which downstream tables are generated from the canonical source versus hand-written but regression-checked (for example validator subsets or authoring-only palette groupings).

- [x] 4. Define taxonomy versioning and deprecation policy
  - [x] 4-1. State how node taxonomy evolves within `dan_graph_v1` and what requires a future contract bump.
  - [x] 4-2. Define the lifecycle for deprecated aliases: introduced, warned, hidden from authoring, migrated, eventually removed.
  - [x] 4-3. Define the policy for future new primitives: what evidence is needed to justify a new runtime node type instead of a macro/template.

- [x] 5. Publish the runtime taxonomy spec
  - [x] 5-1. Write a concise canonical-node matrix for `docs/architecture.md` and `docs/llm-api-guide.md`.
  - [x] 5-2. Record the decisions and explicitly mark non-canonical aliases so future plan work is anchored to one source.

## Primary Files

- `src/dan/models/graph.py`
- `src/dan/models/control_flow.py`
- `src/dan/models/nodes.py`
- `src/dan/registry.py`
- `src/dan/validation/graph.py`
- `src/dan/loader/decompiler.py`
- `src/dan/engine/scheduler.py`
- `editor/src/types/graph.ts`
- `docs/architecture.md`
- `docs/llm-api-guide.md`

## Decisions

- **Advanced primitives stay real:** `parallel_subagents`, `agent_team`, `vote`, `goal_loop`, and `reflection` are part of the canonical runtime catalog, not sugar.
- **Canonical runtime first, authoring second:** the runtime catalog is decided on semantic grounds, not on what is easiest to expose in the editor palette.
- **Single-source metadata is mandatory:** no long-term solution should depend on multiple manually synchronized node-type lists.
- **No dynamic-plugin illusion for `dan_graph_v1`:** discovery-only registration is not enough to create a new runtime node kind; runtime taxonomy changes require explicit schema/runtime support.

## Notes

- This sub-plan is the semantic anchor for the whole family. `40-2`, `40-3`, and `40-4` should not finalize implementation details until this catalog is explicit.
- A likely outcome is that some existing names survive only as deserialization aliases even if their underlying behavior remains supported.
- The runtime/source-of-truth section now lives in `docs/architecture.md` under **Canonical Node Taxonomy (Plan 40)**, with the LLM-facing summary in `docs/llm-api-guide.md`.
- The per-primitive detailed contract table now lives in the architecture runtime contract matrix, with subset-policy constants codified in `src/dan/models/node_taxonomy.py`.
