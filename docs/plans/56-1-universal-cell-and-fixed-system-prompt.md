# 56-1: Universal Cell and Fixed System Prompt

**Parent:** [56-universal-organism-and-cell-restructure](56-universal-organism-and-cell-restructure.md)
**Status:** not-started
**Goal:** Replace every per-product `WorkerDefinition` builder with one canonical `build_cell(model, sampling_preset)` that uses a fixed, domain-agnostic system prompt — the cell constitution — so every cell in the system is the same shape regardless of task family.

## Tasks

- [ ] 1. Define the canonical universal cell
  - [ ] 1-1. Add `src/dan/worker/cell.py` with `build_cell(model, sampling_preset, role_label)` returning a `WorkerDefinition`
  - [ ] 1-2. Add a single `UNIVERSAL_CELL_SYSTEM_PROMPT` constant with zero domain knowledge: role identity, operating principles, hard/soft constraint discipline, tool/budget discipline, output-shape discipline, graceful-fallback behavior
  - [ ] 1-3. Define exactly two sampling presets in `worker/contracts/sampling.py`: `creative` (`temperature ~0.30`, `max_tokens ~2800`) and `deterministic` (`temperature 0.0`, `max_tokens ~1600`)
  - [ ] 1-4. Confirm `WorkerCoreExecutor._build_system_prompt(...)` and `_build_user_prompt(...)` accept the new shape unchanged: the system prompt input becomes the universal constitution; the user prompt still carries brief-driven specifics through `task`, `scope`, `hard_constraints`, `soft_constraints`, `evidence`, and `input_payload`
- [ ] 2. Inventory and remove per-variant cell builders
  - [ ] 2-1. Audit `cli/super_organism.py` for `_build_live_website_worker`, `_build_live_generic_worker`, `_build_live_website_validator`, `_build_live_generic_validator`; mark them for removal once `56-3` migrates callers
  - [ ] 2-2. Audit `worker/organisms/coding_execution.py` for inline `WorkerDefinition` construction in orchestrator/worker/aggregator/validator stages
  - [ ] 2-3. Audit `worker/organisms/project_execution.py`, `incident_execution.py`, `reference_demo.py`, `super_organism.py` for the same
  - [ ] 2-4. Document the inventory in plan notes so subsequent slices have a complete migration list
- [ ] 3. Lock the new shape with regressions
  - [ ] 3-1. Add a focused regression that proves the universal cell can be invoked as worker, validator, explorer, scheduler, etc., differing only by `sampling_preset`, `tool_clamp`, and `ExecutionRequest` brief content
  - [ ] 3-2. Add an import-boundary check that no organism file constructs more than one `WorkerDefinition`-equivalent factory (allow a transitional grandfather list while migration is in flight)

## Decisions

- The cell carries no `instruction`, no `persona`, no role-specific `system_prompt`. Those used to be specialization vectors; they become part of the brief in `56-3`.
- The `Role:` line stays in the assembled system prompt for trace disambiguation, but the role string is supplied by the brief, not by a builder function.
- Sampling preset is the only per-call worker-definition input besides `model`. Everything else flows through `ExecutionRequest`.
- Two presets are the minimum viable set. Additional presets (e.g. `cautious_creative`) are added only when an evaluation gap demands it; resist proliferation.

## Notes

- This is the smallest landable slice. It does not yet change orchestrators or organisms; it just makes the new substrate available so subsequent slices can migrate one caller at a time.
- After 56-1 + 56-3, the universal cell + brief becomes the single answer to "how do I create a worker / validator / explorer / scheduler cell?"
- Risk: the universal system prompt is necessarily more abstract than today's hand-tuned per-variant prompts, which may dent reliability for niche tasks. Mitigation: brief-side snippets in `56-2` carry the hand-tuned content from outside, so the cell stays generic while behavior remains task-aware.
