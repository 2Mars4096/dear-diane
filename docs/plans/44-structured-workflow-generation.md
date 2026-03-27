# 44: Structured Workflow Generation

**Status:** in-progress
**Goal:** Replace empty-graph-first, monolithic workflow authoring with a staged, spec-driven, parallel candidate-graph pipeline that reduces latency and improves robustness on realistic workflow-authoring prompts.

## Motivation

Current workflow generation still has three linked problems:

- concierge and intent-path variability can send the same prompt through different downstream generation paths
- the main generation path still relies too heavily on shallow intent compilation or one-shot codegen for medium workflows
- the system can still spend too long building semantically weak graphs or leave behind a blank saved graph when authoring fails

The new architecture should:

- avoid persisting the saved graph up front
- produce a typed high-level workflow spec before node-level authoring
- parallelize node design and validation with a bounded worker pool
- validate sections before whole-graph linking
- optimize time from user message to runnable workflow, not just time to any graph artifact

## Scope

In scope:

- structured build-lane generation from conversational workflow-authoring prompts
- candidate/staging graph lifecycle before persistence
- high-level spec generation and deterministic sectioning
- node-worker contracts and node-level tests
- section assembly, section-local validation, and boundary-link repair
- bounded one-shot execution smoke checks for accepted candidate workflows
- schedule-intent preservation and scheduled-dispatch verification for prompts that ask for recurring execution
- routing, rollout, evals, and comparison against the current path

Out of scope:

- removing the current intent-compiler/codegen path before the new path proves stable
- one-off prompt tuning for the equity workflow without architectural improvements
- broad executor-taxonomy redesign beyond the contracts needed for this generation pipeline

## Sub-Plans

| # | Sub-Plan | Scope | Priority | Status |
|---|----------|-------|----------|--------|
| [44-1](44-1-high-level-spec-and-sectioning.md) | High-Level Spec and Sectioning | Define the typed workflow spec contract, candidate-graph staging model, and deterministic weighted sectioning algorithm | P1 | not-started |
| [44-2](44-2-node-worker-contracts-and-node-tests.md) | Node Worker Contracts and Node Tests | Turn node specs into typed executable node artifacts with minimal node-level validation | P1 | not-started |
| [44-3](44-3-section-assembly-and-parallel-validation.md) | Section Assembly and Parallel Validation | Assemble validated node artifacts into sections, repair section-local issues, and keep candidate graphs staged | P1 | not-started |
| [44-4](44-4-boundary-linking-and-whole-graph-repair.md) | Boundary Linking and Whole-Graph Repair | Link sections, repair section-boundary mismatches first, and gate persistence on whole-graph acceptance | P1 | not-started |
| [44-5](44-5-routing-rollout-and-evals.md) | Routing, Rollout, and Evals | Route the new structured path safely, compare it against the current path, and define acceptance metrics | P1 | not-started |

## Dependencies / Sequencing

Recommended execution order:

```text
44-1 (High-Level Spec and Sectioning)
  ↓
44-2 (Node Worker Contracts and Node Tests)
  ↓
44-3 (Section Assembly and Parallel Validation)
  ↓
44-4 (Boundary Linking and Whole-Graph Repair)
  ↓
44-5 (Routing, Rollout, and Evals)
```

Rationale:

- the spec contract and partitioning algorithm must stabilize before node workers can consume them
- node-worker outputs and tests must stabilize before section assembly becomes trustworthy
- section-local acceptance should be a prerequisite for boundary repair and whole-graph acceptance
- routing and rollout should happen after the new path has a coherent candidate-to-accepted-graph lifecycle

## Success Criteria

- new workflow-authoring turns no longer persist an empty saved graph before acceptance
- medium workflow prompts can be partitioned into debug-friendly sections and built through bounded parallel workers with a default cap of `8`
- failures are reported as node, section, or boundary failures instead of generic blank-graph or timeout-only outcomes
- accepted graphs pass at least one bounded execution smoke when required inputs can be satisfied from fixtures, defaults, or declared global inputs
- schedule-bearing prompts preserve a schedule sidecar and pass scheduled-dispatch smoke through the existing `/schedule workflow` path
- the structured path is benchmarked against the current path on realistic prompts and measured separately from concierge instability
- the structured path improves, or clearly justifies, the tradeoff between latency, `graph_created`, `run_ready`, and semantic quality

## Decisions

- Candidate graphs are first-class staged artifacts and should exist before any saved workflow mutation happens.
- The LLM should decide semantics, but deterministic partitioning should decide the section layout.
- Sections are debugging and repair units, not cosmetic grouping.
- Boundary repair should be cheaper than full regeneration, or the sectioning architecture loses its speed advantage.
- The structured path should roll out behind an explicit gate and compete head-to-head with the existing path before replacement.
- **No hardcoded thresholds or tuning constants.** Scalar tuning parameters — worker pool caps, quality thresholds, retry limits, timeout budgets, affinity weights — must be configurable via `DAN_*` environment variables with sensible defaults. Larger structured policy surfaces such as lexical corpora, routing patterns, or node-affinity tables should live in a versioned config module or JSON asset rather than being scattered as inline constants or forced through env vars.

## Notes

- This plan is the architectural answer to the current "empty graph plus slow fallback" generation failure mode.
- Concierge/triage instability remains a separate tracked problem and should not be conflated with graph-generation quality.
- The equity-research workflow is the motivating benchmark, but the plan is intentionally general and should cover other realistic conversation-style workflow prompts as well.
- The plans use the canonical runtime executor taxonomy (`llm_operator`, `tool_operator`, `code_operator`, `gate` per `GENERATE_SPEC_NODE_TYPES`), not shortened aliases. There is no `shell` executor — code execution uses `code_operator`.
- Be explicit about the boundary between `node_type` and execution family. `llm_operator`, `tool_operator`, `code_operator`, `gate`, `for_each`, and `while_loop` are runtime node types; the structured spec may still want a smaller execution-family field such as `llm`, `tool`, `code`, `control_flow` to keep planning simpler.
- Shell-style work is still representable, but it should be modeled explicitly as either a `tool_operator` bound to `shell_command` or a `code_operator` when custom code is actually required.
- Key existing infrastructure this plan builds on: `WorkflowIntent`/`StageIntent` (`intent_schema.py`), `accept_candidate_graph()` (`workflow_generation_acceptance.py`), `validate_workflow_build_contract()` (`workflow_contract.py`), `DiagnosisLoop` (`diagnosis.py`), `CoverageChecker` shim (`intent_compiler.py`), `record_generation_outcome()` (`workflow_generation_stats.py`).
- The engine already skips downstream nodes when required upstream data edges are missing due to failure (landed in [43-2-upstream-prerequisite-gating](43-2-upstream-prerequisite-gating.md)). This means graphs produced by the structured pipeline will degrade safely at runtime even if a node fails — but the structured pipeline should still catch semantic gaps at generation time rather than relying on runtime safety nets.
- Concierge routing stabilization for workflow-authoring turns is tracked separately as [12-13-workflow-authoring-triage-stability](12-13-workflow-authoring-triage-stability.md) and is a prerequisite for the structured pipeline to be exercised reliably on conversational prompts.
