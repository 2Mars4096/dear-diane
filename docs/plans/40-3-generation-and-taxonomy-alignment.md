# 40-3: Generation & Taxonomy Alignment

**Parent:** [40-canonical-node-taxonomy](40-canonical-node-taxonomy.md)
**Status:** completed
**Goal:** Keep planner prompts, mutation tools, validator/runtime support, and generation compilers aligned to the canonical taxonomy so workflow generation fails only on genuine reasoning gaps, not contradictory schemas.

## Context

The generation path historically drifted in several ways:

- planner prompts advertised node kinds or shapes the implementation could not build,
- mutation prompts carried their own node enum separate from runtime truth,
- legacy aliases and canonical runtime kinds were mixed inconsistently across chat/build surfaces,
- some authoring helpers used canonical runtime kinds while others still taught deprecated aliases.

Plan 40 has already reduced much of that drift through canonical tests and narrower honest contracts. This sub-plan tracks the remaining work.

## Tasks

- [x] 1. Align planner/generation contracts to canonical taxonomy
  - [x] 1-1. Audit `src/dan/meta/planner.py` prompt contracts against what `_compile_generate_spec()` actually accepts.
  - [x] 1-2. Decide whether `GENERATE` should target runtime nodes directly, a reduced authoring schema, or `WorkflowIntent`/stage types only.
  - [x] 1-3. Remove prompt/examples that mention unsupported node types or unsupported config shapes.
  - [x] 1-4. Introduce a single source for the narrow PlanIR `GENERATE` node subset (`GENERATE_SPEC_NODE_TYPES`) and use it in both planner prompt rendering and `_compile_generate_spec()` error reporting.

- [x] 2. Align mutation and workflow-build prompts
  - [x] 2-1. Ensure `src/dan/server/chat/prompts.py` `NODE_TYPES` derive from the canonical mutation policy (`MUTATION_NODE_TYPES`) instead of a hand-maintained list.
  - [x] 2-2. Decide whether mutation tooling should operate on canonical runtime nodes only, with authoring macros handled separately.
  - [x] 2-3. Clarify how nested-body nodes and advanced primitives are introduced in chat without requiring the model to memorize hidden schema quirks.
  - [x] 2-4. Align `src/dan/server/graph_mutator.py` node defaults/body-graph support with the canonical runtime taxonomy for the covered node kinds.
  - [x] 2-5. Remove stale canonicality drift in prompt templates: `WORKFLOW_TEMPLATES` now prefers canonical `human` over `human_in_the_loop`.

- [x] 3. Align remaining support tables and policy docs
  - [x] 3-1. Remove remaining drift between runtime support, decompiler support, and any still-hand-maintained generation-related subsets.
  - [x] 3-2. Document which remaining subsets are intentionally narrower than the full runtime union (for example markdown export or PlanIR `GENERATE`) rather than accidental drift.
  - [x] 3-3. Keep builder/codegen reference material aligned with the canonical builder surface (`wf.human()`, `wf.vote()`, `wf.team()`, etc.).

- [x] 4. Add drift-detection coverage
  - [x] 4-1. Add a regression specifically covering prompt claims vs `_compile_generate_spec()` support.
  - [x] 4-2. Add a regression for canonical chat/mutation template taxonomy (for example canonical `human` in workflow templates).
  - [x] 4-3. Keep the broader exact taxonomy guardrails in `tests/test_runtime_taxonomy_alignment.py`, `tests/test_graph_mutator_taxonomy.py`, and `tests/test_builder_compiler_taxonomy_alignment.py` as the hard floor for this sub-plan.
  - [x] 4-4. Add a regression that markdown decompiler supported node kinds stay a subset of the canonical runtime taxonomy.

## Primary Files

- `src/dan/meta/planner.py`
- `src/dan/meta/intent_compiler.py`
- `src/dan/server/chat/prompts.py`
- `src/dan/server/graph_mutator.py`
- `src/dan/models/node_taxonomy.py`
- `tests/test_meta/test_planner_taxonomy_alignment.py`
- `tests/test_chat_prompt_templates_taxonomy.py`

## Decisions

- **Prompt honesty beats breadth.** If a surface cannot build a kind safely today, the prompt should not advertise it.
- **Mutation surfaces target canonical runtime kinds.** Deprecated aliases like `if_else` are intentionally omitted from `MUTATION_NODE_TYPES`.
- **PlanIR `GENERATE` may stay narrower than the full runtime union.** That is acceptable as long as the narrowing is explicit, tested, and documented.

## Notes

- `GENERATE` is now explicitly treated as the lightweight/simple path: it intentionally uses the reduced `GENERATE_SPEC_NODE_TYPES` subset rather than pretending to cover advanced runtime primitives.
- Current intentional narrower subsets are explicit and centralized: markdown export/import uses `MARKDOWN_DECOMPILER_SUPPORTED_NODE_TYPES`, PlanIR `GENERATE` uses `GENERATE_SPEC_NODE_TYPES`, and chat mutation uses `MUTATION_NODE_TYPES`.
- Most remaining future work after this sub-plan belongs in broader product decisions (for example whether `GENERATE` should someday collapse further toward `WorkflowIntent` only), not in unresolved taxonomy drift.
