# 24: Reliable Workflow Generation

**Status:** completed
**Goal:** Replace the fragile mutation-JSON generation path with builder DSL codegen and intent compilation, backed by a quality suite and bounded diagnosis, so that "describe what you want → working workflow" is boringly reliable.

## Motivation

The current workflow generation path requires LLMs to produce structurally precise graph operations — exact port names, node configs, edge types, and sequential mutation ops — via the `MUTATION_TOOL_SCHEMA` function-calling interface. This is the wrong abstraction level for LLMs. Evidence from the last development cycle:

- **12+ schema-drift fixes** in one week: `parallel_subagents.branch_graphs` dict→list, `validator.rule_type` aliases, `edge_type: "context"` without required fields, stale `base_graph_revision`, node ID slug mismatches.
- **`_normalize_generated_mutation_ops()`** grew into a sprawling patch layer that silently coerces LLM output into valid mutations — brittle, grows with each new failure mode.
- **Auto-retry loop** (`DAN_MUTATION_AUTO_RETRY_MAX`) papers over structural failures by feeding errors back to the LLM, but convergence is unreliable for complex workflows.
- **PATTERN_LIBRARY** helps for known shapes (chain, review_loop, fan_out, rag_qa) but can't compose patterns or handle novel topologies.

The root cause: LLMs are asked to produce *low-level graph operations* (add_node with exact port specs, add_edge with exact source/target ports) when they're better at producing *high-level intent* (goals, stages, data flow) or *readable code* (Python builder DSL). The deterministic compiler and validation pipeline should handle the structural correctness — not the LLM.

**Strategy:** Two complementary generation paths, each playing to strengths:

1. **Builder codegen** (primary) — LLM writes `dan.builder` Python. Deterministic `build()` handles ports, edges, validation. Already proven: `GENERATE_CODE` path exists in `meta/planner.py` but is opt-in and lightly tested.
2. **Intent compiler** (constrained) — For common patterns, LLM emits a compact structured intent. A deterministic compiler maps it to builder code and validates it immediately. Predictable for known shapes; unsupported intents fail fast into the builder-codegen path.

Both paths produce validated `dan_graph_v1` graphs through the existing pipeline. A quality suite gates every change, and a bounded diagnosis loop handles recoverable failures without making normal generation slower.

**Canonical creation flow:** intent extraction and coverage check → deterministic intent compile when fully covered, otherwise builder codegen → validation gate → bounded diagnosis on failure only. The older mutation-JSON path stays in edit mode for modifying existing graphs, not for creating whole workflows from scratch.

## Existing Infrastructure

| Component | Location | Relevance |
|---|---|---|
| Builder DSL | `builder/builder.py` | 20+ node-creation methods (`llm`, `tool`, `code`, `gate`, `for_each`, `while_loop`, `composite`, `orchestrator`, `parallel_subagents`, `rag`, `validator`, etc.), `>>` chaining, f-string magic, context managers for sub-graphs, `build()` → validated `Graph` |
| Builder compiler | `builder/compiler.py` | Marker resolution, auto-port generation, edge deduplication, sub-graph assembly, `validate_graph()` call |
| Decompiler | `builder/decompiler.py` | `Graph` → executable Python round-trip; chain detection, topological sort |
| SandboxRunner | `sandbox/runner.py` | Subprocess execution with timeout (30s default), memory limits, `_result.json` structured output, `SandboxConfig`/`SandboxResult` |
| `GENERATE_CODE` path | `meta/planner.py` | `_BUILDER_CODE_HARNESS` wraps LLM code, runs via `SandboxRunner`, reads `graph` variable from `_result.json`. Opt-in via `planner_allow_code_generation` |
| `GENERATE` (declarative) | `meta/planner.py` | `_compile_generate_spec()` maps `{nodes, edges}` JSON to `dan_graph_v1`. Fragile: manual port/edge defaults |
| PATTERN_LIBRARY | `server/graph_mutator.py` | 6 patterns (chain, review_loop, fan_out, rag_qa, data_ingest, data_analysis) |
| WORKFLOW_TEMPLATES | `server/chat_manager.py` | 3 templates (paper_writing, rag_qa, chain_3) that expand to mutation op sequences |
| GraphMutator | `server/graph_mutator.py` | 13 mutation op types, `apply()`/`dry_run()`, alias resolution, entry/exit recompute |
| ChatManager | `server/chat_manager.py` | Build vs mutate mode, `MUTATION_TOOL_SCHEMA`, auto-retry, `_normalize_generated_mutation_ops()` |
| validate_graph() | `validation/graph.py` | 12 design-time checks (reachability, ports, edges, schemas, cycles, hyperedges) |
| Self-knowledge RAG | `meta/self_knowledge.py` | DAN API docs indexed for planner grounding |
| LLM API Guide | `docs/llm-api-guide.md` | Full builder DSL reference — injectible into codegen prompts |
| Existing tests | `tests/test_server/test_build_from_intent.py`, `tests/test_builder/` | Intent→plan golden cases, builder round-trip tests, pattern expansion tests |

## Sub-Plans

| # | Sub-Plan | Scope | Effort | Dependencies |
|---|----------|-------|--------|--------------|
| [24-1](24-1-builder-codegen-path.md) | Builder Codegen Path | Make builder DSL codegen the primary generation path. LLM generates `dan.builder` Python → sandbox → compile → validate → save. One-shot generation + small bounded repair. | ~3 days | None (foundational) |
| [24-2](24-2-intent-compiler.md) | Intent Compiler | Constrained fast path for common shapes. LLM emits structured intent → deterministic compiler → validated builder code / graph. Small supported catalog, strict diagnostics, fail-fast handoff to 24-1 on unsupported intents. | ~2 days | 24-1 (fallback path) |
| [24-3](24-3-generation-quality-suite.md) | Generation Quality Suite | Verification harness. Golden intents across workload families, dual-path evaluation, round-trip validation, regression snapshots, pass-rate tracking. | ~2 days | 24-1, 24-2 (both paths to evaluate) |
| [24-4](24-4-bounded-diagnosis-loop.md) | Bounded Diagnosis Loop | Small explicit repair loop for failed generations/runs. Error extraction → artifact mapping → targeted correction. Bounded attempts, outside steady-state path. | ~2 days | 24-1 (primary repair target) |

## Dependencies / Sequencing

```
24-1 (Builder Codegen Path) ← foundational, start here
  ├→ 24-2 (Intent Compiler) ← fast-path front-end; hands unsupported intents to 24-1
  ├→ 24-3 (Quality Suite) ← depends on 24-1 being testable; can start after 24-1 task 3
  └→ 24-4 (Diagnosis Loop) ← depends on 24-1 error reporting contract

24-2 → 24-3 (intent fast path also needs quality coverage)
24-2/24-1 → 24-4 (diagnosis only runs after a concrete generation attempt fails)
```

**Recommended sequence:**
1. **24-1** first — the primary generation path. Everything else builds on it.
2. **24-2** after 24-1 — it is intentionally subordinate to the primary codegen path and depends on it for unsupported intents.
3. **24-3** can start as soon as 24-1 has a testable codegen pipeline. Quality suite work can overlap with 24-2 once the intent schema is stable.
4. **24-4** last — diagnosis requires concrete failure contracts from 24-1 and representative cases from 24-3.

**Parallelizable:** After 24-1 completes, 24-2 and the initial 24-3 golden cases can proceed in parallel. 24-4 should wait until the codegen and intent paths expose stable error surfaces.

## Key Decisions

- **Builder codegen is the primary path, not mutation JSON.** The existing `MUTATION_TOOL_SCHEMA` function-calling approach remains for incremental edits to existing graphs (mutate mode). New workflow creation ("build mode") switches to builder codegen. This is not a rewrite — the mutation path is correct for small edits; it's wrong for whole-graph generation.
- **Intent compiler is a constrained optimization, not a replacement.** It handles a small, well-defined set of common patterns more predictably than free-form codegen. Anything outside its catalog hands off to the primary codegen path. It is a front-end to generation, not a separate planner architecture.
- **Quality suite gates all changes.** No generation change ships without proving it didn't break known cases. This is the enforcement mechanism — not code review, not manual testing.
- **Diagnosis is bounded and explicit.** Max 2 repair attempts per generation failure. No autonomous long-running redesign loop. Failed diagnosis surfaces the error to the user with actionable context — not a spinner that runs for 60 seconds.
- **SandboxRunner is the execution boundary.** All LLM-generated code runs in a subprocess with timeout and memory limits. No `exec()` in the main process. This is already implemented (`sandbox/runner.py`) and trusted.
- **Self-knowledge RAG enriches codegen prompts.** The `SelfKnowledgeIndex` (19-5) provides current API docs to the LLM. This is already built; 24-1 ensures the codegen prompt includes it.
- **Mutation path not deprecated.** `GraphMutator` + `MUTATION_TOOL_SCHEMA` remain for mutate-mode edits (add a node, rewire an edge, apply a skill). Only whole-graph build-from-scratch switches to codegen. The two paths coexist.
- **No new runtime autonomy.** This phase does not add self-repair loops to the core engine. The diagnosis loop (24-4) runs *before* a workflow enters the engine — it's a generation-time concern, not a runtime concern. The existing repair infrastructure (19-3) handles runtime failures.
- **Intent extraction is not a new planner action.** The system may use structured intent as an intermediate representation, but the planner should not grow a parallel long-lived "intent mode" alongside codegen. Intent is a fast-path pre-pass that either compiles cleanly or delegates.
- **Quality artifacts split into durable and ephemeral sets.** Golden fixtures and approved baselines are committed. Timestamped reports and exploratory snapshots are generated locally or in CI and are not treated as long-lived source artifacts.

## Success Criteria

- **Builder codegen produces valid workflows for all existing template scenarios** (chain, review_loop, fan_out, rag_qa, paper_writing) without manual normalization patches.
- **Intent compiler handles 5+ common patterns** with higher consistency than free-form codegen, and fails fast (< 1s) on unsupported patterns.
- **Quality suite has 15+ golden intents** across workload families, with automated pass-rate tracking and regression detection.
- **Codegen round-trip is clean:** generated builder code → `build()` → `Graph` → `decompile()` → code produces structurally equivalent graphs.
- **Diagnosis loop recovers 60%+ of validation failures** within 2 attempts, with clear error attribution (which artifact failed, why).
- **No regression** in existing mutation-mode edits (`MUTATION_TOOL_SCHEMA` path unchanged).
- **Generation latency is acceptable:** codegen path completes within 15s for typical workflows (LLM call + sandbox + validation).

## Notes

- The `GENERATE_CODE` path in `meta/planner.py` is the starting point for 24-1. It already has the sandbox harness and variable extraction. What's missing: a proper builder-focused prompt, self-knowledge injection, validation pipeline, error reporting, and integration into `ChatManager` build mode.
- The `GENERATE` (declarative spec) path in `meta/planner.py` is the starting point for 24-2. Its `_compile_generate_spec()` is manual and fragile — the intent compiler replaces it with a proper structured schema and deterministic compilation, but should reuse the 24-1 validation contract instead of inventing a parallel one.
- The existing `test_build_from_intent.py` has ~15 test cases that form the seed for 24-3's golden suite. The builder round-trip tests in `test_builder/test_decompiler.py` provide the validation pattern.
- `_normalize_generated_mutation_ops()` is a symptom, not a solution. As the codegen path matures, this function should stop growing. If it keeps growing, that's a signal the codegen path isn't working.
