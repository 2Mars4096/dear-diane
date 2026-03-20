# 40: Canonical Node Taxonomy

**Status:** completed
**Goal:** Establish DAN's workflow graph as a stable, reusable runtime IR with one authoritative node taxonomy, explicit authoring/runtime separation, and a clear deprecation/migration policy.

## Motivation

The current node model is powerful, but the taxonomy drifts across layers:

- `src/dan/models/graph.py` defines a fixed discriminated-union runtime contract, while `src/dan/registry.py` only provides discovery and can drift from deserialization reality.
- `src/dan/meta/planner.py` and `src/dan/server/chat/prompts.py` advertise generation-time node vocabularies that do not fully match what planners, validators, or compilers actually accept.
- `editor/src/types/graph.ts` and `editor/src/lib/graphAdapter.ts` already expose authoring-only pseudo-types such as `gate_if_else` / `gate_while`, proving the UI wants a different vocabulary than the runtime.
- Legacy aliases (`if_else`, `while_loop`, `human_in_the_loop`) and newer primitives (`gate`, `human`, `goal_loop`, `reflection`, `agent_team`, `vote`) coexist without one explicit canonical policy.
- `src/dan/registry.py`, `src/dan/validation/graph.py`, and `src/dan/loader/decompiler.py` each preserve their own partial node lists, so a node can be schema-valid in one layer and still look unknown or unsupported in another.

This is now blocking the next step DAN needs: workflows that are not just runnable, but **stable, documented, reusable artifacts** that keep the same meaning across editor use, chat generation, storage, replay, decompilation, and future task reuse.

## Plan Principles

- The runtime taxonomy is a **versioned contract**, not an open-ended plugin vocabulary.
- Authoring surfaces may be **smaller and more opinionated** than the runtime schema.
- Convenience methods, templates, and palette items may lower to canonical runtime nodes, but they do not redefine runtime semantics.
- Backward compatibility is preserved through explicit aliases, migrations, and warnings rather than silent drift.

## Sub-Plans

| # | Sub-Plan | Scope | Priority | Status |
|---|----------|-------|----------|--------|
| [40-1](40-1-runtime-canonical-node-spec.md) | Runtime Canonical Node Spec | Define the authoritative runtime primitive catalog, alias policy, invariants, and single-source node metadata contract | P1 | completed |
| [40-2](40-2-authoring-palette-and-macro-layer.md) | Authoring Palette & Macro Layer | Separate editor/chat/builder authoring vocabulary from the runtime schema; define lowering rules and opinionated palette policy | P1 | completed |
| [40-3](40-3-generation-and-taxonomy-alignment.md) | Generation & Taxonomy Alignment | Align planner prompts, mutation schema, validator, registry, compiler, and decompiler with the canonical taxonomy so generation stops failing from vocabulary drift | P1 | completed |
| [40-4](40-4-migration-compatibility-and-regressions.md) | Migration, Compatibility & Regressions | Preserve existing graphs via aliases/migrations, update docs, and add drift-detection/roundtrip coverage | P1 | completed |

## Dependencies / Sequencing

Recommended execution order:

```text
40-1 (Runtime Canonical Node Spec)         ← define the contract first
  ├→ 40-2 (Authoring Palette & Macro Layer)     ← can start once the runtime catalog is stable
  ├→ 40-3 (Generation & Taxonomy Alignment)     ← can start once canonical runtime names and classes are fixed
  └→ 40-4 (Migration, Compatibility & Regressions) ← lands after the new runtime/authoring split is concrete
```

`40-2` and `40-3` can run in parallel after `40-1` is settled.

## Success Criteria

- [ ] One explicit runtime primitive catalog exists and is treated as the only authoritative source for node kinds.
- [ ] `parallel_subagents`, `agent_team`, `vote`, `goal_loop`, and `reflection` remain true runtime primitives with documented semantics.
- [ ] Legacy aliases are explicitly classified as canonical, deprecated alias, or authoring-only pseudo-type.
- [ ] Editor/runtime/planner/mutator/validator/decompiler no longer encode different hand-maintained node vocabularies.
- [ ] Registry, validator, mutation-schema, decompiler, and editor-runtime node tables are either derived from the canonical node definitions or checked against them with explicit regressions.
- [ ] The editor palette becomes intentionally opinionated without changing the meaning of stored runtime graphs.
- [ ] Existing graphs can still load, validate, run, and round-trip through the new taxonomy policy.

## Decisions

- **Real runtime primitives:** `parallel_subagents`, `agent_team`, `vote`, `goal_loop`, and `reflection` remain first-class runtime node types. They are not to be reduced to editor-only sugar.
- **Authoring/runtime separation:** The editor may expose a smaller, opinionated palette than the full runtime schema. Authoring ergonomics do not justify runtime taxonomy drift.
- **Canonical taxonomy is fixed and versioned:** runtime node kinds are part of the `dan_graph_v1` contract (or its eventual successor), not dynamically extensible through discovery-only registration.
- **Aliases are compatibility tools, not new concepts:** legacy names can continue to deserialize for compatibility, but must not compete with canonical names indefinitely.

## Notes

- This plan is intentionally cross-cutting. It is not just a workflow-generation cleanup; it affects storage, runtime semantics, authoring UX, and future workflow reuse as durable documentation.
- Existing evidence of drift to resolve includes: planner prompt vs `_compile_generate_spec()`, `Graph` union vs validator known-node lists, registry discovery vs actual deserialization, and editor pseudo-types vs runtime node types.
- A likely end state is: a stable runtime node IR, a smaller authoring palette, and explicit macro-lowering rules for convenience constructs such as branch/review-loop/map-reduce style authoring helpers.
- 2026-03-20 first implementation slice landed: runtime-support drift was reduced in `registry.py`, `validation/graph.py`, and `scheduler.py`; planner contracts were narrowed to the node/edge forms `_compile_generate_spec()` can actually build; and focused regressions now guard runtime taxonomy coverage plus planner honesty.
- 2026-03-20 second implementation slice landed: `graph_mutator.py` now has real defaults/ports for more canonical runtime primitives (`goal_loop`, `reflection`, `vote`, `human`, `agent_team`) and recognizes `goal_loop` as a body-graph node; chat mutation prompts now expose a broader honest runtime subset and explain `goal_loop` body graphs; builder decompiler now emits `wf.goal_loop(...)` instead of silently falling back for that runtime primitive; and the editor TypeScript runtime union now covers more canonical node kinds while `goal_loop` inherits existing body-graph drill-in behavior.
- 2026-03-20 third implementation slice landed: the builder/compiler/decompiler path now supports additional canonical runtime primitives directly (`wf.input_node()`, `wf.human()`, `wf.vote()`), new builder parity regressions lock those APIs in, and `docs/llm-api-guide.md` now documents the canonical builder/runtime contract for `input`, `human`, `vote`, and `goal_loop`.
- 2026-03-20 fourth implementation slice landed: the markdown loader/decompiler path now genuinely round-trips `goal_loop` bodies using the existing `## Agents` / `## Flow` structure instead of exporting `GoalLoopNode` as an unsupported stub, and canonical `HumanNode` markdown export now emits an explicit compatibility warning when it has to pass through the older `type: human` path.
- 2026-03-20 fifth implementation slice landed: markdown export now also warns when `parallel_subagents` branches collapse to the first branch agent in `parallel(...)`, and when `orchestrator` export drops richer runtime config that the current markdown dialect still cannot preserve. This makes loader export more explicit about what remains lossy even before new syntax is added.
- 2026-03-20 sixth implementation slice landed: the legacy gate migration toggle is now shared across `GET /api/graphs/{id}`, `GraphStore.load_as_model()`, adapter workflow loads, and JSON file adapter loads instead of only some router paths; the tracked `graphs/*.json` corpus now has a regression guard for parse/model/fatal validation health; and `graphs/batch_paper_writing.json` was repaired to restore valid top-level `entry_points` / `exit_points` so the committed corpus can stay strict.
- 2026-03-20 seventh implementation slice landed: taxonomy guardrails are now symmetric rather than one-way. Tests now enforce exact equality between the runtime `Graph` union and `NodeTypeRegistry`, exact equality between the runtime union and validator known-node kinds, exact TS runtime-node parity for `editor/src/types/graph.ts`, and an explicit mutation-schema policy (`NODE_TYPES == runtime - {"if_else"}`). The old non-IR `graphs/three_step_chain.json` sample was migrated into a real `dan_graph_v1` graph, so the committed graph corpus no longer needs a legacy allowlist exception.
- 2026-03-20 eighth implementation slice landed: the original pre-IR `three_step_chain` shape is now preserved explicitly under `tests/fixtures/migration/` with a structural regression, while `docs/architecture.md` and `docs/llm-api-guide.md` now carry the canonical taxonomy matrix, source-of-truth policy, and “new node kind” decision rule needed for the remaining `40-1` / `40-4` cleanup work.
