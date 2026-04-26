# 56-1: Universal Cell and Fixed System Prompt

**Parent:** [56-universal-organism-and-cell-restructure](56-universal-organism-and-cell-restructure.md)
**Status:** completed
**Goal:** Replace every per-product `WorkerDefinition` builder with one canonical `build_cell(model, sampling_policy, role_label)` that uses a fixed, domain-agnostic system prompt and fixed prompt-rendering architecture, while leaving orchestrator-discovered roles, task rules, budgets, sampling details, tool policy, validation criteria, and task-specific prompt content flexible in brief-supplied policy data.

## Tasks

- [x] 1. Define the canonical universal cell
  - [x] 1-1. Add `src/dan/worker/cell.py` with `build_cell(model, sampling_policy, role_label)` returning a `WorkerDefinition`
  - [x] 1-2. Add a single `UNIVERSAL_CELL_SYSTEM_PROMPT` constant with zero domain knowledge, thresholds, task-family rules, or budget caps: role identity, operating principles, constraint-following discipline, policy-following discipline, output-shape discipline, graceful-fallback behavior
  - [x] 1-3. Define `worker/contracts/sampling.py` as a flexible sampling-policy registry with baseline `creative` and `deterministic` profiles plus explicit per-brief overrides for temperature, max tokens, reasoning, and related provider hints
  - [x] 1-4. Confirm or adjust `WorkerCoreExecutor._build_system_prompt(...)` and `_build_user_prompt(...)` so invariant infrastructure remains in the system prompt while a stable prompt architecture renders brief-driven specifics through task, scope, constraints, evidence, tool policy, runtime policy, output contract, failure criteria, recovery hints, input payload, and metadata rather than hidden worker-builder fields
- [x] 2. Inventory per-variant cell builders for removal
  - [x] 2-1. Audit `cli/super_organism.py` for `_build_live_website_worker`, `_build_live_generic_worker`, `_build_live_website_validator`, `_build_live_generic_validator`; mark them for removal once `56-3` migrates callers
  - [x] 2-2. Audit `worker/organisms/coding_execution.py` for inline `WorkerDefinition` construction in orchestrator/worker/aggregator/validator stages
  - [x] 2-3. Audit `worker/organisms/project_execution.py`, `incident_execution.py`, `reference_demo.py`, `super_organism.py` for the same
  - [x] 2-4. Document the inventory in plan notes so subsequent slices have a complete migration list
- [x] 3. Lock the new shape with regressions
  - [x] 3-1. Add a focused regression that proves the universal cell can be invoked as worker, validator, explorer, scheduler, etc., differing only by `sampling_policy`, `tool_policy`, and `ExecutionRequest` brief content
  - [x] 3-2. Add an import-boundary check that no organism file constructs more than one `WorkerDefinition`-equivalent factory (allow a transitional grandfather list while migration is in flight)

## Decisions

- The cell carries no `instruction`, no `persona`, no role-specific `system_prompt`. Those used to be specialization vectors; they become part of the brief in `56-3`.
- The `Role:` line may stay in the assembled system prompt for trace disambiguation, but the role string is supplied by the orchestrator-discovered `RoleSpec`, not by a builder function or fixed role catalog.
- Sampling policy is the only per-call worker-definition input besides `model` and trace role label. Everything else flows through `ExecutionRequest` or brief policy metadata.
- `creative` and `deterministic` are baseline profiles, not hard-coded caps. Additional profiles or explicit overrides are allowed when the orchestrator has task evidence for them; the design should resist hidden global limits, not legitimate per-brief adaptation.
- "Fixed prompt" means fixed constitution plus fixed rendering architecture, not fixed task wording. Task-specific prompt content belongs in brief fields and reusable snippets.

## Notes

- 2026-04-26: `cell.py` now owns the invariant universal cell prompt and `build_cell(...)`. `brief.py` renders brief-driven task details into `ExecutionRequest.metadata["brief_rendered_user_prompt"]`, and `WorkerCoreExecutor._build_user_prompt(...)` honors that rendered prompt without moving task-specific text into the system prompt.
- 2026-04-26 follow-up: `tests/test_worker/test_plan56_import_boundaries.py` now AST-scans non-conversation organism modules for direct `WorkerDefinition(...)` constructor growth, with only the current `coding_execution.py` / `project_execution.py` migration counts grandfathered.
- 2026-04-26 inventory: immediate universal-cell candidates are the four Super DAN live builders, the DAN Code shared orchestrator builder plus dynamic worker/aggregator/validator construction, project/research reader builders, incident execution roles, reference demo live roles, and Super DAN deterministic/live cell constructors. Direct removal is deferred until `56-3`/`56-4` facades preserve imports.
- This is the smallest landable slice. It does not yet change orchestrators or organisms; it just makes the new substrate available so subsequent slices can migrate one caller at a time.
- After 56-1 + 56-3, the universal cell + brief becomes the single answer to "how do I create a worker / validator / explorer / scheduler cell?"
- Risk: the universal system prompt is necessarily more abstract than today's hand-tuned per-variant prompts, which may dent reliability for niche tasks. Mitigation: brief-side snippets in `56-2` carry the hand-tuned content from outside, so the cell stays generic while behavior remains task-aware.
