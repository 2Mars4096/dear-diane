# 32: Workflow Generation Optimization

**Status:** completed
**Goal:** Improve the NL→workflow pipeline end-to-end so generated workflows are more powerful, less verbose, and work correctly on first attempt more often — before measuring quality in Phase 23.

## Motivation

Phase 14 (24-reliable-generation) switched from fragile mutation-JSON to builder codegen and intent compilation, making generation structurally sound. But practical usability gaps remain:

1. **Builder DSL verbosity** — A 3-node chain requires creating each node individually, wiring them with `>>`, and setting prompts with f-string interpolation. The LLM must produce all of this correctly. Simpler APIs → fewer code lines → fewer errors → higher one-shot success rate.
2. **Narrow intent compiler coverage** — `COVERAGE_CATALOG` has 7 patterns (linear_chain, review_loop, fan_out_fan_in, rag_qa, data_pipeline, tool_augmented, human_gate). Anything outside falls to free-form codegen, which is slower and less predictable.
3. **No smart defaults** — Generated workflows lack retry policies, validation gates, and feedback loops unless explicitly requested. Real workflows need these; users shouldn't have to ask.
4. **Fragile progressive refinement** — "Add a review loop to my workflow" often triggers a full rebuild via the codegen path instead of a targeted structural mutation. The codegen path (one-shot) and mutation path (incremental edits via `GraphMutator`) don't compose well for structural changes.
5. **No domain awareness in generation** — Research workflows, data pipelines, equity analysis, and content workflows all get the same generic codegen treatment. Domain-specific tools, model tiers, and structure patterns aren't suggested.

These gaps compound: the LLM produces more code with more error opportunities, and results need more user refinement.

## Approach

Five targeted improvements, ordered by impact:

1. **Builder convenience layer** — High-level methods (`chain`, `review_loop`, `map_reduce`) and pipeline syntax that reduce the amount of code the LLM must generate. Direct impact: shorter codegen, fewer wiring errors.
2. **Intent compiler expansion** — Broader pattern catalog with auto-composition so the fast, deterministic path handles more requests without falling to free-form codegen.
3. **Smart generation defaults** — Codegen prompts and post-generation enrichment automatically include retry policies, validation checkpoints, and quality gates where appropriate.
4. **Progressive NL refinement** — Structural mutations ("add a review loop", "fan out this step") handled cleanly without full rebuild. Bridges the gap between one-shot codegen and incremental mutation.
5. **Domain generation profiles** — Per-domain configuration bundles (tools, model tiers, node patterns, validation rules) that the planner selects based on detected domain.

## Existing Infrastructure

| Component | Location | Relevance |
|---|---|---|
| Builder DSL | `builder/builder.py` | Target for convenience layer additions. 20+ node-creation methods, `>>` chaining, context managers. |
| Builder compiler | `builder/compiler.py` | Auto-port generation, edge dedup, `validate_graph()` call |
| NodeRef / PortRef | `builder/refs.py` | `>>` operator, f-string markers, `__getitem__` for ports. Target for `|` operator. |
| Intent compiler | `meta/intent_compiler.py` | `COVERAGE_CATALOG` (7 patterns), `CoverageChecker`, `IntentCompiler.compile()` |
| Intent schema | `meta/intent_schema.py` | `WorkflowIntent`, `StageIntent`, `StageType` enum |
| Codegen prompts | `meta/planner.py` → `CodegenPromptBuilder._SYSTEM_PROMPT` | System prompt for builder code generation |
| Intent extraction | `meta/intent_extraction.py` → `INTENT_EXTRACTION_SYSTEM_PROMPT` | Intent-path system prompt (new patterns/keywords) |
| SandboxRunner | `sandbox/runner.py` | Subprocess execution of generated builder code |
| GraphMutator | `server/graph_mutator.py` | Mutation ops for incremental edits; 6 PATTERN_LIBRARY entries |
| Domain learning | `concierge/domain_learning.py` | `detect_domain()`, `DomainTemplate`, domain-specific extraction |
| Planner | `meta/planner.py` | Orchestrates codegen vs intent paths, `GENERATE_CODE` mode |
| Self-knowledge RAG | `meta/self_knowledge.py` | API docs indexed for codegen grounding |
| LLM API Guide | `docs/llm-api-guide.md` | Must be updated with new convenience APIs |

## Sub-Plans

| # | Sub-Plan | Scope | Effort | Dependencies |
|---|----------|-------|--------|--------------|
| [32-1](32-1-builder-convenience-layer.md) | Builder Convenience Layer | `chain()`, `review_loop()`, `map_reduce()`, `tool_chain()` one-liners, pipeline `\|` operator, automatic single-port wiring | ~2 days | None (foundational) |
| [32-2](32-2-intent-compiler-expansion.md) | Intent Compiler Expansion | Expand `COVERAGE_CATALOG` from 7 to ~15 patterns, auto-composition of multi-pattern intents, domain-aware skeleton selection | ~2 days | 32-1 (new APIs as compilation targets) |
| [32-3](32-3-smart-generation-defaults.md) | Smart Generation Defaults | Auto-wire retry policies, validation gates, feedback loops in generated workflows; codegen prompt updates; post-generation enrichment | ~1.5 days | 32-1 |
| [32-4](32-4-progressive-refinement.md) | Progressive NL Refinement | Structural mutation macros (wrap-in-loop, fan-out, insert-gate), node resolution by name, graph-context injection for follow-up turns | ~2 days | 32-1, 32-2 |
| [32-5](32-5-domain-generation-profiles.md) | Domain Generation Profiles | Per-domain config bundles (tools, model tiers, patterns, validation), 4 seed profiles, codegen prompt injection | ~1.5 days | 32-2, 32-3 |

## Dependencies / Sequencing

```
32-1 (Builder Convenience Layer) ← foundational, start here
  ├→ 32-2 (Intent Compiler Expansion) ← new APIs as compilation targets
  ├→ 32-3 (Smart Generation Defaults) ← can start after 32-1
  └→ 32-4 (Progressive Refinement) ← needs 32-1 + 32-2
       └→ 32-5 (Domain Profiles) ← needs 32-2 + 32-3 (extends generation_defaults.py)
```

32-2 and 32-3 can run in parallel after 32-1 completes.
32-5 waits for both 32-2 (expanded pattern catalog) and 32-3 (`generation_defaults.py` created).

## Success Criteria

- [x] Builder convenience methods reduce typical workflow code by 40-60% (measure on 5 representative workflows)
- [x] Intent compiler handles 15+ patterns (up from 7) with auto-composition
- [x] Generated workflows include retry policies and validation gates by default where appropriate
- [x] "Add a review loop" NL follow-up modifies an existing workflow without full rebuild
- [x] 4+ domain profiles seeded (mapping to `detect_domain()` values: `paper_rendering`, `literature_review`, `equity_research`, `data_analysis`, `code_generation` etc.)
- [x] `docs/llm-api-guide.md` updated with all new convenience methods
- [x] Existing builder tests and quality suite fixtures continue to pass
- [x] Phase 23 (33-generation-quality-eval) can run against the optimized pipeline

## Decisions

- (filled in during execution)

## Notes

- This phase is deliberately scoped to the generation pipeline, not the execution engine. Engine-level improvements (checkpointing, long-running profiles, self-healing) are separate backlog items.
- The convenience layer doesn't replace the low-level builder API — it's syntactic sugar that compiles down to the same graph primitives. Power users can still use `wf.llm()` + `>>` for full control.
- Intent compiler expansion should be conservative: only add patterns that have clear, unambiguous structure. Ambiguous patterns should fall through to codegen.
- Domain profiles build on existing domain detection (31-21) and don't add a new domain learning pipeline. Profile domain keys must match `_DOMAIN_KEYWORDS` in `concierge/domain_learning.py` (currently: `paper_rendering`, `equity_research`, `data_analysis`, `literature_review`, `code_generation`, `workflow_building`).
- **No `codegen_prompts.py` exists.** The builder codegen system prompt lives in `meta/planner.py` (`CodegenPromptBuilder._SYSTEM_PROMPT`). The intent-extraction prompt lives in `meta/intent_extraction.py` (`INTENT_EXTRACTION_SYSTEM_PROMPT`). Sub-plans that reference `codegen_prompts.py` should target these locations instead.
- This phase should complete before Phase 23 (generation quality evaluation) runs its full battery, so the eval measures the optimized pipeline.
- The backlog item "Optimize workflow generation quality" is promoted to this phase.
