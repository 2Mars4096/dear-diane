# 38-19: Generation Entry Points & Hook Surface

**Parent:** [38-18-workflow-generation-contract-and-hooks](38-18-workflow-generation-contract-and-hooks.md)
**Status:** completed
**Goal:** Identify every workflow-authoring path and define exactly where the workflow-generation contract should be injected so build, mutate, review, and repair all receive the right engine-aware guidance.

## Problem

There previously was no single answer to:

1. Which code paths actually generate or repair workflows?
2. Which of those paths already receive workflow-specific prompt guidance?
3. Where should a built-in contract be injected so it helps the model before bad graph structure is proposed?

Without that inventory, any workflow-generation contract would be applied unevenly and drift would continue across surfaces.

## Scope

This sub-plan focused on *where* the contract hooks belong, not on the final contract wording.

Target surfaces included:

- initial workflow build,
- natural-language graph mutation,
- workflow review / critique turns,
- preview repair after invalid mutation plans,
- diagnosis / repair after failed runs,
- build-and-run or auto-apply shortcuts,
- any meta-controller or gateway path that bypasses the normal chat authoring loop.

This also included implicit authoring surfaces such as:

- post-tool follow-up recovery,
- `apply_last_mutation` / preview-apply continuations,
- run-triggered repair prompts,
- helper paths that construct workflow-edit prompts outside the main unified prompt builder.

## Deliverables

This sub-plan produced:

- an inventory matrix of workflow-authoring entry points,
- a hook timing map for workflow-generation guidance,
- an explicit owner map for shared contract assembly and audit metadata.

## Progress

Completed in code or audit:

- main chat build/mutate/review path,
- mutation-preview repair path,
- planner/codegen path,
- diagnosis repair path,
- structural repair path,
- intent-extraction prompt assembly,
- clarify-intent prompt surface,
- negative-control tests showing non-workflow turns do not receive the workflow contract.

Still remaining:

- none for the currently known workflow-authoring surfaces.

## Inventory Matrix

| Surface | Entry Point | Hook Owner | Guidance Surface | Status |
| --- | --- | --- | --- | --- |
| Unified chat build / mutate / review / repair | `ChatManager._build_messages()` via `DEFAULT_PROMPT_MODULE_RESOLVER` | `src/dan/server/chat/prompts.py` plus `src/dan/workflow_generation_guidance.py` | inferred `build` / `mutate` / `review` / `repair` | implemented |
| Clarification before build | `ChatManager.clarify_intent()` | `src/dan/server/chat_manager.py` plus `render_workflow_clarification_guidance()` | clarification supplement for build ambiguity | implemented |
| Invalid mutation-preview repair | mutation-plan repair branch in `ChatManager` | `src/dan/server/chat_manager.py` | `repair` | implemented |
| Intent extraction for build path | `build_intent_extraction_system_prompt()` | `src/dan/meta/intent_extraction.py` | `build` | implemented |
| Planner REUSE / ADAPT / GENERATE prompt | `PlanningPromptBuilder.build_system_prompt()` | `src/dan/meta/planner.py` | `build` + `mutate` | implemented |
| Builder DSL codegen | `CodegenPromptBuilder.build_system_prompt()` | `src/dan/meta/planner.py` | `codegen` | implemented |
| Builder-code diagnosis re-prompt | diagnosis retry prompt in `src/dan/meta/diagnosis.py` | `src/dan/meta/diagnosis.py` | `codegen` | implemented |
| Structural repair planner | `StructuralRepairPlanner._system_prompt()` | `src/dan/meta/repair.py` | `repair` | implemented |
| Deterministic intent compiler | `IntentCompiler` | `src/dan/meta/intent_compiler.py` | not applicable; deterministic compiler, no LLM hook | intentionally no prompt hook |
| Meta-controller / concierge orchestration | controller and concierge paths that call planner or chat manager | upstream prompt owners above | inherited from planner/chat surfaces | no separate prompt surface found |

## Hook Timing Map

| Timing | Concrete Surface | Hook Shape |
| --- | --- | --- |
| `pre-generation` | unified chat authoring, intent extraction, planner, builder codegen | shared workflow contract module or system-prompt supplement |
| `pre-mutation-plan` | unified chat mutate path | shared workflow contract module |
| `post-invalid-preview` | mutation preview repair | repair-specific contract injection |
| `post-run-failure` | diagnosis loop and structural repair | codegen or repair contract injection |
| `always-on when workflow-authored` | prompt metadata audit and eval reporting | prompt-module ids plus workflow-guidance surface |

## Injection Ownership

- Canonical workflow-turn classification lives in `src/dan/workflow_generation_guidance.py`.
- Canonical prompt-module assembly lives in `src/dan/server/chat/prompts.py`.
- Canonical audit recording for injected modules lives in `src/dan/server/chat_manager.py` and `src/dan/server/audit.py`.
- Surfaces that cannot call the prompt-module resolver directly consume the same helper text through dedicated prompt builders in `planner.py`, `diagnosis.py`, `repair.py`, and `intent_extraction.py`.
- Deterministic compiler paths intentionally do not inject prompt guidance; this is a deliberate non-hooked surface, not a gap.

## Tasks

- [x] 1. Inventory all workflow-authoring entry points
  - [x] 1-1. Chat build path
  - [x] 1-2. Chat mutate path
  - [x] 1-3. Mutation preview repair path
  - [x] 1-4. Planner/codegen path
  - [x] 1-5. Intent compiler path
  - [x] 1-6. Gateway/meta-controller path
  - [x] 1-7. Build-and-run shortcut paths

- [x] 2. Map current prompt and context injection at each entry point
  - [x] 2-1. Record which system prompts, examples, graph summaries, and tool manifests are currently available.
  - [x] 2-2. Record where workflow-specific knowledge is missing or only partially present.
  - [x] 2-3. Record where a path uses generic graph-mutation prompting even though it is performing workflow authoring.
  - [x] 2-4. Record which surfaces inject structured data vs plain prose only.
  - [x] 2-5. Record any provider/tool-mode constraints that affect what hook shape is realistic on that surface.

- [x] 3. Classify hook timing
  - [x] 3-1. `pre-generation` hooks for initial authoring guidance
  - [x] 3-2. `pre-mutation-plan` hooks for edit planning
  - [x] 3-3. `post-invalid-preview` hooks for repair
  - [x] 3-4. `post-run-failure` hooks for diagnosis / self-healing
  - [x] 3-5. `always-on` hooks for canonical workflow invariants

- [x] 4. Define per-surface hook shape
  - [x] 4-1. What should be injected as plain prompt text
  - [x] 4-2. What should be injected as structured machine-readable reference data
  - [x] 4-3. What should be omitted for token budget reasons on each surface
  - [x] 4-4. Which surfaces need stronger repair-specific guidance rather than full generation guidance

- [x] 5. Decide ownership and call graph
  - [x] 5-1. Define the single helper/module responsible for assembling workflow-generation guidance.
  - [x] 5-2. Ensure all workflow-authoring surfaces call through that helper instead of embedding local variants.
  - [x] 5-3. Make skipped-hook behavior explicit so future paths do not accidentally bypass the contract.
  - [x] 5-4. Define how non-workflow turns prove they did **not** receive workflow-generation guidance.

## Primary Files

- `src/dan/workflow_generation_guidance.py`
- `src/dan/server/chat/prompts.py`
- `src/dan/server/chat_manager.py`
- `src/dan/meta/planner.py`
- `src/dan/meta/intent_extraction.py`
- `src/dan/meta/intent_compiler.py`
- `src/dan/meta/diagnosis.py`
- `src/dan/meta/repair.py`
- `src/dan/server/audit.py`
- `tests/test_chat_prompt_modules.py`
- `tests/test_chat_manager_prompt_design.py`
- `tests/test_post_tool_followup_recovery.py`

## Decisions

- **Hook surfaces must be enumerated explicitly.** Relying on "main path" assumptions is what created drift.
- **Repair paths are first-class authoring paths.** If the contract is only injected on initial build, the system will still regress during repair.
- **One assembly point.** Shared contract assembly must be centralized even if some surfaces consume it through prompt builders instead of the module resolver directly.

## Notes

- The main output of this sub-plan is the matrix above: entry point, owner, hook shape, and status.
- This sub-plan makes the difference between `workflow build`, `workflow mutate`, `workflow review`, `workflow repair`, and `codegen` explicit at the hook layer.
