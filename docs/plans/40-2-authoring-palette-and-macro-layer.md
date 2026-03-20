# 40-2: Authoring Palette & Macro Layer

**Parent:** [40-canonical-node-taxonomy](40-canonical-node-taxonomy.md)
**Status:** completed
**Goal:** Separate the user-facing authoring vocabulary from the canonical runtime schema so DAN can keep a stable IR while exposing a smaller, opinionated palette and macro layer.

## Context

The editor and builder already hint at the right layering:

- the editor exposes pseudo-types like `gate_if_else` / `gate_while` that lower to runtime `gate`,
- the builder exposes higher-level constructs like `branch()`, `review_loop()`, `map_reduce()`, and `tool_chain()`,
- chat-based workflow authoring wants an even smaller concept set than the raw runtime graph.
- graph materialization/decompilation already prefers readability in some cases, so DAN is implicitly balancing a canonical IR against a friendlier authoring surface today.

What is missing is an explicit policy for **which node forms are runtime storage**, **which are authoring-only**, and **how authoring constructs lower into canonical nodes without ambiguity**.

## Tasks

- [x] 1. Define the authoring/runtime layering model
  - [x] 1-1. Separate concepts into three buckets: runtime primitives, compatibility aliases, and authoring-only macros/pseudo-types.
  - [x] 1-2. Decide which runtime primitives are editor-visible by default, which are advanced/hidden, and which are primarily generated or decompiled forms.
  - [x] 1-3. Define whether chat-based workflow authoring should target runtime primitives directly or a smaller authoring grammar that later lowers to runtime nodes.
  - [x] 1-4. First editor/runtime parity slice: expanded the TypeScript `DanNode` union to cover additional canonical runtime node kinds (`human`, `orchestrator`, `reflection`, `goal_loop`, `vote`, `agent_team`) without forcing immediate palette exposure.
  - [x] 1-5. Explicit editor authoring/runtime split slice: introduced `PaletteNodeType` plus an explicit `PALETTE_NODE_TO_RUNTIME_NODE_TYPE` map so palette-only labels and canonical runtime `node_type` values are no longer conflated in the type system.

- [x] 2. Specify editor palette policy
  - [x] 2-1. Define the canonical editor palette categories and which items appear in each.
  - [x] 2-2. Keep the palette intentionally smaller than the runtime taxonomy where appropriate, especially for aliases and complex expert-only primitives.
  - [x] 2-3. Define how advanced primitives like `parallel_subagents`, `agent_team`, `vote`, `goal_loop`, and `reflection` should appear: visible by default, advanced drawer, template-first entry point, or config-driven.
  - [x] 2-4. Decide which authoring entries are direct node drops versus workflow templates/pattern starters that scaffold a canonical runtime shape.
  - [x] 2-5. First editor UX slice: imported/runtime `goal_loop` nodes now participate in the same body-graph drill-in affordance as `while_loop` / `for_each` / `composite`, and runtime-only node kinds gained colors/icons so loaded graphs are readable even before palette decisions are finalized.
  - [x] 2-6. Authoring split slice: the palette now emits canonical `human` nodes for new human interactions, and `ConfigPanel` now treats `human` / `human_in_the_loop` as first-class dedicated config sections instead of generic field blobs.
  - [x] 2-7. Advanced palette slice: low-risk canonical nodes `goal_loop`, `vote`, and `reflection` are now directly exposed in the palette with canonical defaults, and the higher-complexity multi-subgraph coordination nodes `agent_team` and `orchestrator` are now also direct palette items with canonical defaults.
  - [x] 2-8. Multi-subgraph inspection slice: `agent_team` and `orchestrator` now have dedicated config sections with explicit per-agent/per-team “Open subgraph” actions and key editing, making them practical to inspect/edit without ambiguous default drill-in behavior.

- [x] 3. Specify macro and pseudo-type lowering rules
  - [x] 3-1. Document lowering for existing editor pseudo-types such as `gate_if_else` / `gate_while`.
  - [x] 3-2. Document lowering for builder/chat convenience constructs such as branch, review-loop, map-reduce, tool-chain, and future workflow templates.
  - [x] 3-3. Define which macros are purely ergonomic wrappers and which carry extra semantics that must be preserved in metadata or decompilation.
  - [x] 3-4. Builder/runtime parity slice: added canonical builder entry points for `input`, `human`, and `vote` (`wf.input_node()`, `wf.human()`, `wf.vote()`) so these runtime primitives no longer fall through to fake `wf.llm(...)` decompilation.
  - [x] 3-5. Builder/runtime parity slice: added `wf.team()` plus `wf.group_chat()` alias for `agent_team`, and builder decompiler now emits `wf.team()` for that canonical runtime primitive instead of falling back or requiring JSON-only authoring.

- [x] 4. Define decompilation/materialization policy
  - [x] 4-1. Decide when decompilers should emit canonical runtime forms vs convenience forms for readability.
  - [x] 4-2. Ensure the lowering/raising rules stay lossless where DAN claims round-trip fidelity.
  - [x] 4-3. Document what information must survive round-trip even when a macro is re-emitted.
  - [x] 4-4. Align `graph_materializer` and builder decompiler behavior so "readable output" never invents a vocabulary that the canonical authoring policy does not recognize.

- [x] 5. Specify authoring-surface contracts
  - [x] 5-1. Align editor palette metadata, builder convenience methods, markdown flow syntax, and chat workflow-building prompts to the same authoring vocabulary.
  - [x] 5-2. Ensure each authoring surface can explain how its concepts map to the canonical runtime nodes.

## Primary Files

- `editor/src/types/graph.ts`
- `editor/src/lib/graphAdapter.ts`
- `editor/src/lib/paletteTemplates.ts`
- `editor/src/components/NodePalette.tsx`
- `editor/src/components/ConfigPanel.tsx`
- `src/dan/builder/builder.py`
- `src/dan/builder/decompiler.py`
- `src/dan/loader/compiler.py`
- `src/dan/loader/decompiler.py`
- `src/dan/meta/graph_materializer.py`
- `src/dan/server/chat/prompts.py`

## Decisions

- **Opinionated palette is intentional:** the editor does not need one tile per runtime primitive if that makes authoring noisier or more fragile.
- **Macros are first-class authoring tools:** convenience constructs are allowed to be richer and more ergonomic than the runtime IR as long as lowering is explicit and stable.
- **Runtime meaning does not depend on the palette:** changing authoring UX must not silently change the semantics of stored graphs.

## Notes

- This sub-plan is where DAN can become easier to use without weakening the runtime contract.
- The likely end state is an authoring vocabulary closer to how users think about workflows, while stored graphs remain canonical and reusable for future task families.
- This first slice improved runtime/load parity rather than authoring creation parity. The editor can now represent more canonical node kinds safely, but palette exposure and dedicated config UX remain explicit follow-up decisions.
- The builder parity slice improved authoring creation parity on the Python DSL side without forcing new palette exposure. The remaining open work is to define which of these canonical primitives should become first-class palette items or template-backed authoring concepts.
- The editor now has an explicit palette-to-runtime lowering map and emits canonical `human` nodes for new palette-created human interactions, while still preserving legacy `human_in_the_loop` support for imported/runtime graphs.
- The latest editor pass also made the canonical `human` path practical to use: the config panel now has dedicated human-node controls (`prompt`, timeout/default action, render mode/target, instructions) instead of only relying on generic JSON-ish field editing.
- Team-style multi-agent collaboration is no longer JSON/UI-only: the Python authoring surface now has a real `wf.team()` path, with `wf.group_chat()` as the free-form alias, and the builder decompiler round-trips `agent_team` through that surface.
- Current editor authoring policy is now explicit in practice:
- Leaf or single-body canonical nodes (`human`, `goal_loop`, `vote`, `reflection`) can be directly created in the palette.
- Higher-complexity multi-subgraph coordination primitives (`agent_team`, `orchestrator`) are now also palette-visible, but only after gaining dedicated config sections and explicit subgraph-open actions so they are not opaque drops.
- `gate_if_else` / `gate_while` remain authoring-only pseudo-types that lower to canonical `gate` nodes, keeping the palette ergonomic without changing the stored runtime graph meaning.
- Markdown authoring has also moved closer to the canonical runtime vocabulary: `human`, `vote`, `goal_loop`, and the minimal `agent_team` shape now round-trip through the loader/decompiler path instead of remaining JSON-only or builder-only surfaces.
