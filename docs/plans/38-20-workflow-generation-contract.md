# 38-20: Workflow Generation Contract

**Parent:** [38-18-workflow-generation-contract-and-hooks](38-18-workflow-generation-contract-and-hooks.md)
**Status:** completed
**Goal:** Define the compact, authoritative workflow-generation contract that captures the engine facts the authoring model must know in order to generate runnable workflows reliably.

## Problem

The workflow engine had many concrete invariants, but the generation layer did not consume them as one coherent contract.

Important rules were split across:

- runtime node taxonomy,
- graph mutator defaults,
- executor behavior,
- validation logic,
- repair logic,
- regression tests,
- example workflows,
- incidental prompt wording.

That created two bad failure modes:

- the generator invented structures the engine could not run,
- the repair path kept relearning the same mechanical rules after the fact.

## Concrete Motivating Failures

Recent workflow debugging exposed contract-worthy failures, including:

- double-brace prompt placeholders being generated even though runtime rendering prefers single-brace variables,
- `code_operator` bodies using bare `return` even though inline execution requires `result = ...`,
- `for_each` body graphs assuming entry `input` nodes automatically expose undeclared variables,
- wrong port wiring such as feeding `input` objects where a named scalar port is required,
- summary/repair prompts that were structurally plausible but ignored engine-level constraints.

The contract now encodes these as reusable authoring rules instead of leaving them as local lessons in tests and one-off fixes.

## Contract Contents

The workflow-generation contract covers at least these categories.

### 1. Canonical node and port semantics

- canonical runtime node types,
- authoring-visible aliases vs runtime primitives,
- default/common input and output ports,
- tool-specific manifests where generic `tool_operator` guidance is insufficient.

### 2. Runtime authoring rules

- placeholder syntax for prompt/query templates,
- `code_operator` execution conventions such as `result = ...`,
- allowed assumptions about inline code environment,
- `for_each` / subgraph input contracts,
- body-graph entry/exit invariants,
- edge wiring expectations and common port pitfalls.

### 3. Reliability and repair norms

- retry defaults and failure-handling expectations,
- when to prefer explicit repair vs leaving behavior ambiguous,
- what counts as a mechanical fix vs a semantic design choice,
- how preview repair should respond to invalid mutation plans.

### 4. Honesty and output discipline

- when the system may claim a workflow is only drafted, validated, applied, tested, or run-ready,
- when to emit placeholder logic vs real logic,
- when to avoid over-claiming executable completeness.

## Contract Boundaries

The workflow-generation contract includes:

- engine invariants the model must obey,
- mode-specific authoring guidance for build/mutate/review/repair/codegen,
- compact examples only when they prevent repeated real failures.

It does **not** include:

- domain-specific research instructions,
- broad user-facing tutorial material,
- large workflow examples copied wholesale into every prompt path,
- full runtime documentation already better represented in canonical code/spec tables.

## Design Constraints

- The contract must stay compact enough to inject repeatedly.
- The contract must be sourced from canonical runtime facts where possible.
- The contract must distinguish:
  - stable engine invariants,
  - opinionated authoring guidance,
  - temporary heuristics or examples.
- The contract must be easy to regression-test when runtime behavior changes.

## Tasks

- [x] 1. Define the contract sections and source of truth
  - [x] 1-1. Identify which sections can be derived from canonical runtime metadata.
  - [x] 1-2. Identify which sections must remain hand-authored because they encode policy or repair guidance.
  - [x] 1-3. Mark every section as `canonical`, `policy`, or `heuristic`.

- [x] 2. Extract the highest-value workflow constraints
  - [x] 2-1. Port contracts that commonly fail in generated workflows
  - [x] 2-2. Template syntax and rendering rules
  - [x] 2-3. `code_operator` execution and output rules
  - [x] 2-4. `for_each` / subgraph body invariants
  - [x] 2-5. Retry / repair conventions
  - [x] 2-6. Common tool-node manifests needed for real workflows
  - [x] 2-7. Honesty/status wording rules for drafted vs applied vs run-ready workflows

- [x] 3. Decide the contract representation
  - [x] 3-1. Which pieces should be prose bullets
  - [x] 3-2. Which pieces should be structured tables / JSON-ish reference data
  - [x] 3-3. Which examples are worth including because they prevent common mistakes
  - [x] 3-4. Which examples should be omitted because they are too domain-specific or token-expensive

- [x] 4. Define update and ownership policy
  - [x] 4-1. Runtime changes that must update the contract
  - [x] 4-2. Tests that should fail when the contract drifts from the runtime
  - [x] 4-3. The maintainer surface for canonical vs hand-authored sections
  - [x] 4-4. A source map from each contract rule to its canonical code source or regression test

## Primary Files

- `src/dan/workflow_generation_guidance.py`
- `src/dan/server/graph_mutator.py`
- `src/dan/utils/template_render.py`
- `src/dan/executors/llm.py`
- `src/dan/executors/rag.py`
- `src/dan/executors/code.py`
- `src/dan/engine/scheduler.py`
- `src/dan/server/chat/mutation_parser.py`
- `tests/test_graph_mutator.py`
- `tests/test_chat_prompts.py`
- `tests/test_meta/test_codegen_prompt.py`
- `tests/test_post_tool_followup_recovery.py`

## Decisions

- **The contract is not a general tutorial.** It exists to prevent generator/runtime mismatch.
- **Canonical first.** Runtime semantics should be derived from canonical engine definitions whenever possible.
- **Examples are earned.** Include only the examples that close real failure modes seen in workflow generation.
- **Repair guidance belongs in the contract.** For this system, workflow generation and workflow repair are too coupled to document separately.

## Progress

Implemented:

- compact shared contract covering placeholders, `result = ...`, `for_each` body invariants, exact ports/config, mutation-op limits, tool-id honesty, retries, and proposed/applied/tested status wording.

Remaining:

- none required for this contract slice; future extraction from runtime metadata is optional maintenance, not a blocker.

## Contract Source Map

| Contract Rule | Classification | Canonical Runtime / Product Source | Regression / Evidence Source |
| --- | --- | --- | --- |
| Exact node types, ports, and tool manifests | canonical | `src/dan/server/graph_mutator.py` default ports and `TOOL_PORT_MANIFESTS` | `tests/test_graph_mutator.py` and `tests/test_graph_mutator_taxonomy.py` |
| Prompt template placeholders use single-brace variables | policy over canonical runtime behavior | `src/dan/utils/template_render.py`, `src/dan/executors/llm.py`, `src/dan/executors/rag.py`, `src/dan/engine/scheduler.py` | `tests/test_chat_prompts.py`, `tests/test_providers/test_llm_executor_providers.py`, `tests/test_engine/test_rag.py` |
| Code nodes assign outputs with `result = ...` | canonical | `src/dan/executors/code.py` inline executor contract | `tests/test_engine/test_retry_policy.py`, `tests/test_meta/test_codegen_prompt.py` |
| `for_each` body ownership and `replace_body_graph` invariants | canonical | graph-mutation schema and dry-run logic in `src/dan/server/graph_mutator.py` | `tests/test_graph_mutator.py`, `tests/test_post_tool_followup_recovery.py`, `tests/test_meta/test_repair.py` |
| Mutation operations must be schema-supported only | canonical | mutation parsing and graph mutator operation validation in `src/dan/server/chat/mutation_parser.py` and `src/dan/server/graph_mutator.py` | `tests/test_post_tool_followup_recovery.py`, `tests/test_chat_prompt_modules.py` |
| Use registered tool ids instead of invented tool names | canonical + policy | tool catalog rendering in planner / intent extraction and registered capability manifests | `tests/test_meta/test_goal_contract.py`, `tests/test_meta/test_codegen_prompt.py` |
| Retry / failure-handling expectations for brittle steps | policy | shared workflow contract text plus repair/diagnosis prompt consumers | prompt-level regressions in `tests/test_chat_prompt_modules.py` and repair/diagnosis tests in `tests/test_meta/` |
| Honest proposed / applied / tested / run-ready wording | policy | shared workflow contract text plus audit / validation consumers | `tests/test_post_tool_followup_recovery.py`, `tests/test_eval_semantic_patch.py`, `tests/eval/report.py` |

## Representation Decision

- Compact prose carries the reusable authoring doctrine that must fit across chat, planner, codegen, and repair surfaces.
- Canonical structured facts remain in engine code, manifests, and builders; the contract references those facts rather than duplicating large tables inline.
- The long-form detail body exists as prompt detail, not as an always-on monolith, so normal turns stay small while repair/build turns can request more specifics.

## Ownership Policy

- Changes to runtime node/tool manifests, template semantics, control-flow body rules, code-node output semantics, retry defaults, or audit prompt metadata must update the shared contract text.
- At least one regression test or eval/report assertion must move with any such runtime change.
- The shared helper in `src/dan/workflow_generation_guidance.py` owns the hand-authored policy layer; runtime files own the canonical behavior they expose.

## Notes

- This sub-plan produced the contract skeleton consumed by the prompt-module work in [38-21](38-21-prompt-module-and-injection-design.md).
- Plan 40 remains an upstream source for node taxonomy and runtime primitives, but this sub-plan adds authoring and repair rules that taxonomy alone does not cover.
