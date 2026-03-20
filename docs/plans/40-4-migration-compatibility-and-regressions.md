# 40-4: Migration, Compatibility & Regressions

**Parent:** [40-canonical-node-taxonomy](40-canonical-node-taxonomy.md)
**Status:** completed
**Goal:** Roll the canonical taxonomy out safely through aliases, migrations, documentation, and regression coverage so existing workflows keep working while the system becomes more coherent.

## Context

Taxonomy cleanup only helps if it preserves trust in existing workflow assets. DAN already stores graphs, decompiles them, replays them through chat/editor surfaces, and uses them as future reusable artifacts. Any canonicalization effort therefore needs:

- compatibility for old graph JSON,
- explicit alias/deprecation handling,
- round-trip regression coverage,
- and updated docs so users and future contributors know which names are canonical.

This includes not just test fixtures, but real saved graphs under `graphs/`, example workflows, builder/decompiler output, and any persisted workflow assets users may already rely on.

## Tasks

- [x] 1. Define graph migration strategy
  - [x] 1-1. Decide which legacy node types continue to deserialize as aliases and which should be normalized on save/load.
  - [x] 1-2. Define whether migration happens eagerly (rewrite on save/export) or lazily (alias-only on read with warnings).
  - [x] 1-3. Document how migrations interact with `dan_graph_v1` and what would require a future contract bump.

- [x] 2. Preserve compatibility across stored workflow assets
  - [x] 2-1. Audit existing graph fixtures, examples, saved graphs, markdown workflows, and builder-decompiled outputs for legacy node kinds.
  - [x] 2-2. Ensure old graphs still validate, execute, and decompile after canonicalization work lands.
  - [x] 2-3. Ensure authoring surfaces do not silently reintroduce deprecated node forms on export or re-save.
  - [x] 2-4. Add a focused migration sweep across representative stored assets (legacy aliases, advanced primitives, editor-authored graphs, generated graphs) before considering the rollout safe.
  - [x] 2-5. Shared migration slice: graph model loads now use the same `DAN_GATE_MIGRATION_ENABLED` gate-migration behavior across `GraphStore.load_as_model()`, graph API loads, and adapter workflow loads instead of disagreeing by entry point.
  - [x] 2-6. Repaired the one fatal tracked corpus graph (`graphs/batch_paper_writing.json`) by restoring valid top-level `entry_points` / `exit_points`, so committed `dan_graph_v1` assets no longer require a permanent fatal-validation exception.

- [x] 3. Add regression and round-trip coverage
  - [x] 3-1. Add golden fixtures for legacy-alias graphs and canonicalized graphs.
  - [x] 3-2. Add round-trip tests for graph JSON, builder decompile/recompile, markdown compile/decompile, and editor import/export.
  - [x] 3-3. Add focused regressions for runtime primitive preservation, alias warnings, and authoring-palette lowering behavior.
  - [x] 3-4. Add at least one guard that compares a representative saved graph corpus before/after canonicalization to catch accidental semantic drift in ports, node kinds, or nested body references.
  - [x] 3-5. Added focused runtime taxonomy regressions covering registry/runtime-union alignment, validator/runtime-union alignment, sub-graph reference checks for advanced primitives, and scheduler canonical `rag_operator` bypass naming.
  - [x] 3-6. Added focused regressions for the second taxonomy slice: `graph_mutator` body-graph support for `goal_loop`, schema-valid defaults for newly surfaced runtime node kinds, and builder decompiler emission of `wf.goal_loop(...)` instead of falling back.
  - [x] 3-7. Added focused markdown compatibility regressions for `goal_loop` round-trip and canonical `HumanNode` export honesty via `tests/test_loader/test_goal_loop_markdown_roundtrip.py`.
  - [x] 3-8. Added focused markdown honesty regressions for lossy `parallel_subagents` branch collapse and omitted `orchestrator` config export via `tests/test_loader/test_markdown_decompiler_honesty.py`.
  - [x] 3-9. Added focused migration-path regressions for shared gate migration across `GraphStore.load_as_model()` and adapter workflow loads, plus a tracked-corpus guard over committed `graphs/*.json` via `tests/test_graph_migration_compatibility.py` and `tests/test_graph_corpus_compatibility.py`.
  - [x] 3-10. Tightened the guardrails to exact drift checks: runtime/registry equality, runtime/validator equality, editor runtime-type parity, and an explicit mutation-schema omission policy for deprecated `if_else`.
  - [x] 3-11. Added builder-compiler coverage guard: every runtime `node_type` in the canonical union now has a schema-valid builder compiler instantiation path via `tests/test_builder_compiler_taxonomy_alignment.py`.

- [x] 4. Update documentation and policy docs
  - [x] 4-1. Update `docs/architecture.md` with the canonical taxonomy, runtime/authoring split, and deprecation policy.
  - [x] 4-2. Update `docs/llm-api-guide.md` so workflow authors and LLM callers see the canonical node contract and any approved authoring macros.
  - [x] 4-3. Add one concise canonical-node reference table that future plans can cite instead of re-explaining the taxonomy.

- [x] 5. Add maintenance guardrails
  - [x] 5-1. Add tests or lint-like checks that fail when a new node type is added to models without updating the canonical metadata source.
  - [x] 5-2. Add tests or assertions that catch drift between runtime canonical nodes, editor runtime types, and planner/mutator-facing enums.
  - [x] 5-3. Define a lightweight contribution rule for future node additions: runtime primitive vs alias vs macro must be declared explicitly.

## Primary Files

- `graphs/`
- `examples/`
- `src/dan/models/graph.py`
- `src/dan/server/graph_store.py`
- `src/dan/server/routers/graphs.py`
- `src/dan/loader/compiler.py`
- `src/dan/loader/decompiler.py`
- `src/dan/builder/decompiler.py`
- `editor/src/types/graph.ts`
- `docs/architecture.md`
- `docs/llm-api-guide.md`
- `tests/`

## Decisions

- **Compatibility is part of the feature, not cleanup after the fact:** canonicalization must preserve existing workflow assets.
- **Round-trip promises must stay true:** if DAN claims reusable workflows as durable assets, taxonomy cleanup cannot make export/import/decompile paths unreliable.
- **Future drift should become hard to introduce:** regression coverage and contribution rules are part of the plan, not optional polish.

## Notes

- This sub-plan closes the gap between "new taxonomy designed" and "new taxonomy safe to live with."
- The implementation should prefer explicit warnings and deterministic migration behavior over silent implicit normalization.
- The first slice here is guardrail-first rather than migration-first: it adds tests that make future drift visible before broader graph-corpus migration work begins.
- `.gitignore` now explicitly allows the new taxonomy regression files under `tests/` so these guardrails are repository artifacts, not local-only untracked tests.
- The corpus audit so far suggests migration risk is concentrated in existing `while_loop`, `human_in_the_loop`, `gate`, `composite`, and nested graph assets. Newer advanced primitives such as `goal_loop`, `reflection`, `vote`, and `agent_team` currently appear more in code/tests than in stored graph assets, so test-first compatibility work remains the right priority for them.
- The tracked `graphs/*.json` corpus is now guarded at two levels: JSON parse + `Graph.model_validate()` for all committed canonical graphs, and “no fatal `validate_graph` errors” across the committed corpus. This keeps the compatibility track anchored to real stored assets rather than only synthetic tests.
- Representative committed graphs now also have a round-trip signature guard (`tests/test_graph_corpus_roundtrip.py`) that checks `Graph.model_validate()` + `model_dump()` preserve node-type counts, subgraph keys, entry/exit points, and top-level node/edge counts on real nested assets such as `paper_writing.json`, `batch_paper_writing.json`, and `vibe_research_multi_dept.json`.
- Markdown export now has explicit honesty warnings for two known lossy cases: `parallel_subagents` branches with multiple real body nodes, and `orchestrator` nodes whose richer runtime config cannot yet be represented in the current markdown format.
- The API guide now reflects the newer builder/runtime parity: `wf.input_node()`, `wf.human()`, `wf.vote()`, and `goal_loop` are documented as part of the canonical authoring/runtime contract instead of remaining implicit code-only features.
- `graphs/three_step_chain.json` is now a valid canonical `dan_graph_v1` sample rather than the lone committed legacy exception. If the pre-IR shape still needs preserving, it should live as a dedicated migration fixture rather than inside the canonical graph corpus.
- The old pre-IR `three_step_chain` shape is now preserved as `tests/fixtures/migration/pre_dan_graph_v1_three_step_chain.json` with a dedicated structural test, so migration/debugging can keep a representative legacy specimen without weakening the canonical `graphs/*.json` corpus contract.
- Markdown `type: human` now compiles and decompiles through canonical `HumanNode` rather than round-tripping through the deprecated `HumanInTheLoopNode` alias path. The remaining markdown honesty warning is for genuinely lossy cases, not for the normal human node path itself.
