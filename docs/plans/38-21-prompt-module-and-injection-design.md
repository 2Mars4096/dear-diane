# 38-21: Prompt Module & Injection Design

**Parent:** [38-18-workflow-generation-contract-and-hooks](38-18-workflow-generation-contract-and-hooks.md)
**Status:** completed
**Goal:** Design the built-in prompt/reference module and injection mechanism that supplies the workflow-generation contract to all relevant authoring surfaces without bloating context or duplicating prompt logic.

## Problem

Even with a good contract, the system will still drift if each surface injects it differently.

Prompt logic was already split across:

- unified chat prompts,
- mutation-specific prompting,
- graph summary helpers,
- repair prompts,
- planner/codegen instructions,
- provider/tool-call compatibility shims.

The system needed one injection design that was:

- compact,
- mode-aware,
- easy to reuse,
- easy to regression-test,
- hard to bypass accidentally.

## Design Questions

1. Should the contract be injected as one prose block, multiple prompt modules, structured data plus a prose wrapper, or a hybrid?
2. Which parts should always be present vs only included for workflow-authoring modes?
3. How should repair-specific prompting differ from initial generation prompting?
4. How would token budget be controlled as the contract grows?

## Target Design

The implemented design is a layered module:

- **base workflow contract module**
  - compact and reusable across workflow-authoring surfaces
- **surface-specific supplements**
  - clarification supplement
  - inferred build / mutate / review / repair variants
  - explicit codegen variant
- **prompt detail payload**
  - longer reference body available only on demand through `load_prompt_detail`

## Non-Goals

This sub-plan did not try to:

- rewrite every chat prompt family in one sweep,
- move all workflow knowledge into one giant static string,
- expose workflow-generation guidance to unrelated non-workflow ask-mode turns.

## Key Constraints

- The injection design must work across both normal chat prompting and workflow-repair/mutation flows.
- The design must tolerate provider/tool-call constraints where some surfaces need more structured guidance and less freeform prose.
- The design must make it easy to prove, in tests, which module(s) were injected for a given surface.

## Tasks

- [x] 1. Define the prompt module shape
  - [x] 1-1. Decide the base module contents.
  - [x] 1-2. Decide the per-surface supplements.
  - [x] 1-3. Decide the prose-vs-structured-data boundary.

- [x] 2. Define the injection API
  - [x] 2-1. Create one helper that assembles workflow-generation guidance by mode and surface.
  - [x] 2-2. Make the helper explicit about requested surface: `build`, `mutate`, `review`, `repair`.
  - [x] 2-3. Make the helper explicit about optional context such as current graph summary, failed validation report, or runtime error summary.
  - [x] 2-4. Make the helper return enough metadata that tests and telemetry can confirm which modules were injected.

- [x] 3. Control size and duplication
  - [x] 3-1. Avoid re-embedding the same long examples into every prompt path.
  - [x] 3-2. Reuse canonical reference blocks across surfaces.
  - [x] 3-3. Keep the always-on module small enough for frequent turns.

- [x] 4. Align with existing prompt families
  - [x] 4-1. Integrate with unified chat prompts without breaking non-workflow chat turns.
  - [x] 4-2. Align with mutation-repair prompting so the same contract language appears in both generation and repair.
  - [x] 4-3. Align with planner/codegen prompts where workflow generation bypasses the normal mutation path.
  - [x] 4-4. Define an explicit fallback strategy for surfaces that cannot yet consume the full structured module shape.

- [x] 5. Add prompt-level regression coverage
  - [x] 5-1. Tests for each authoring surface to verify the contract module is present.
  - [x] 5-2. Tests to verify prohibited stale guidance does not reappear.
  - [x] 5-3. Tests that the module stays compact and mode-scoped.

## Primary Files

- `src/dan/workflow_generation_guidance.py`
- `src/dan/server/chat/prompts.py`
- `src/dan/server/chat_manager.py`
- `src/dan/meta/planner.py`
- `src/dan/meta/diagnosis.py`
- `src/dan/meta/repair.py`
- `src/dan/meta/intent_extraction.py`
- `tests/test_chat_prompt_modules.py`
- `tests/test_chat_manager_prompt_design.py`
- `tests/test_post_tool_followup_recovery.py`

## Implemented Design

### Module shape

- base contract module: `workflow_generation_contract`
- clarification supplement: `render_workflow_clarification_guidance()`
- long-form reference body: prompt detail `prompt:workflow_generation_contract:full`
- surface-specific variants: inferred `build`, `mutate`, `review`, `repair`, and explicit `codegen`

### Injection API

- canonical surface inference: `infer_workflow_generation_surface(...)`
- canonical enable/disable gate: `workflow_generation_contract_enabled()` plus request-scoped override
- canonical compact render: `render_workflow_generation_contract(surface, tools_available=...)`
- canonical detail render: `render_workflow_generation_contract_detail()`

### Metadata and observability

- prompt-module resolution records `module_id="workflow_generation_contract"`
- surface metadata records `workflow_guidance_surface`
- chat audit persists `prompt_module_ids`, `workflow_guidance_injected`, and `workflow_guidance_surface`
- eval runner/report consumes the same metadata for compare-mode summaries and leakage checks

### Fallback design

- surfaces that cannot directly use the prompt-module resolver reuse the same contract renderer in their own system prompt builders
- deterministic compiler paths intentionally remain outside the prompt-module layer because they do not ask an LLM to author workflow structure

## Progress

Implemented:

- shared workflow-generation prompt helper,
- workflow prompt module with surface selection,
- compact clarification supplement,
- centralized intent-extraction system-prompt builder,
- integration into unified chat prompts, mutation repair, planner/codegen, diagnosis repair, structural repair, and planning prompts,
- prompt metadata sink for lightweight observability.

Remaining:

- none required for this plan slice; future prompt cleanup is optional maintenance.

## Decisions

- **Shared module, not copy-paste.** Prompt guidance is assembled from reusable blocks rather than duplicated in multiple strings.
- **Mode-aware by construction.** Workflow-generation guidance should not leak broadly into unrelated ask-mode turns.
- **Repair is not an afterthought.** Repair prompts consume the same contract with tighter emphasis, not a separate ad hoc doctrine.

## Notes

- If future work exposes that current prompt assembly becomes too tangled for clean injection, that should be treated as a new refactor rather than papered over with another local prompt fragment.
- A good outcome here is a small number of reusable prompt/reference modules with explicit surface selection, not one more monolithic prompt blob.
