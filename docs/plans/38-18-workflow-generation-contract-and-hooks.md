# 38-18: Workflow Generation Contract & Hooks

**Parent:** [38-review-hardening](38-review-hardening.md)
**Status:** completed
**Goal:** Make workflow generation, mutation, review, and repair more reliable by introducing one built-in workflow-generation contract plus explicit prompt/hook injection points across all workflow-authoring paths.

## Problem

Workflow-generation knowledge was spread across:

- prompt examples,
- implicit engine/runtime behavior,
- graph mutator defaults,
- validation and repair code,
- regression tests that encode real constraints,
- ad hoc fixes discovered only after a workflow fails at run time.

That made the system correct only after repeated repair loops. The same engine facts kept being relearned:

- valid placeholder syntax,
- `for_each` body input contracts,
- `code_operator` result conventions,
- tool/node port names,
- subgraph entry/exit rules,
- retry/failure expectations,
- mutation constraints such as when `replace_body_graph` is required.

The result was prompt drift across build, mutate, review, and repair surfaces.

## Why This Belongs In Product, Not Just Docs

This was not mainly a "wiki missing" problem.

The system needed workflow-generation knowledge available inside the authoring path itself:

- during natural-language workflow build,
- during mutation planning,
- during graph review,
- during repair after a failed preview or run,
- during auto-apply / build-and-run shortcuts.

A human-readable wiki may still help, but it would not by itself improve generation quality unless the same knowledge was injected into the actual workflow-authoring path.

## Outcome

DAN now has a built-in workflow-generation contract that defines:

- what the generator must know about runtime node semantics,
- what formatting and port conventions are allowed,
- what repair behavior is mechanical vs semantic,
- what prompt modules or shared prompt helpers must be injected for each workflow-authoring surface.

This plan family did **not** assume the answer had to be a Codex skill. The implemented answer is:

- a compact built-in prompt module,
- a shared helper for prompt-only consumers,
- explicit hook points in build/mutate/review/repair/codegen paths,
- eval coverage for known workflow-generation failure modes.

## Progress

Implemented:

- shared workflow-generation contract helper in product code,
- built-in chat prompt module for workflow build/mutate/review/repair turns,
- shared contract reuse in mutation repair, planner codegen, diagnosis repair, structural repair, planning prompts, and intent-extraction prompt assembly,
- prompt-level regression coverage for authoring turns, repair turns, leakage prevention, and lightweight audit observability,
- explicit hook inventory and closure notes in [38-19](38-19-generation-entry-points-and-hook-surface.md),
- contract source mapping and ownership notes in [38-20](38-20-workflow-generation-contract.md),
- compare-mode evals, audit enrichment, and workflow success metrics in [38-22](38-22-evals-rollout-and-guardrails.md).

Still remaining:

- none for this plan family; future work is optional prompt cleanup or broader eval expansion, not a blocker for completion.

## Non-Goals

This plan family is **not** primarily about:

- writing a broad human-facing wiki as the main solution,
- replacing the build-boundary validation and repair work in [38-16](38-16-workflow-build-contract-and-repair.md),
- introducing domain-specific research or vertical profiles,
- solving arbitrary business-logic generation quality beyond engine-contract correctness.

## Sequence

The intended execution order was:

1. [38-19](38-19-generation-entry-points-and-hook-surface.md) to inventory all workflow-authoring surfaces and bypasses.
2. [38-20](38-20-workflow-generation-contract.md) to define the compact contract content.
3. [38-21](38-21-prompt-module-and-injection-design.md) to inject that contract consistently.
4. [38-22](38-22-evals-rollout-and-guardrails.md) to compare and guard the rollout.

That sequence is now complete.

## Subplans

- [38-19-generation-entry-points-and-hook-surface](38-19-generation-entry-points-and-hook-surface.md)
- [38-20-workflow-generation-contract](38-20-workflow-generation-contract.md)
- [38-21-prompt-module-and-injection-design](38-21-prompt-module-and-injection-design.md)
- [38-22-evals-rollout-and-guardrails](38-22-evals-rollout-and-guardrails.md)

## Tasks

- [x] 1. Audit all workflow-generation entry points and current prompt injection surfaces
  - [x] 1-1. Identify every path that can originate or repair a workflow graph.
  - [x] 1-2. Identify which paths currently receive engine-aware guidance and which only receive generic graph-edit prompts.
  - [x] 1-3. Identify where repeated workflow failures are currently discovered too late, after preview or run.

- [x] 2. Define one workflow-generation contract
  - [x] 2-1. Extract the runtime authoring rules that are currently implicit in tests and scattered prompt code.
  - [x] 2-2. Separate canonical, stable engine facts from task- or domain-specific examples.
  - [x] 2-3. Decide the structured-vs-prose split so the contract stays compact and hard to drift.

- [x] 3. Add explicit built-in hook points
  - [x] 3-1. Define where the contract is injected for build, mutate, review, and repair.
  - [x] 3-2. Define which parts are always-on vs mode-specific.
  - [x] 3-3. Ensure the same contract is available to auto-repair and mutation preview flows, not only initial generation.

- [x] 4. Add eval coverage before broad rollout
  - [x] 4-1. Lock in the known workflow-generation failures as regression scenarios.
  - [x] 4-2. Compare baseline vs contract-injected behavior before expanding scope.

## Primary Files

- `src/dan/workflow_generation_guidance.py`
- `src/dan/server/chat/prompts.py`
- `src/dan/server/chat_manager.py`
- `src/dan/server/graph_mutator.py`
- `src/dan/meta/planner.py`
- `src/dan/meta/diagnosis.py`
- `src/dan/meta/repair.py`
- `src/dan/meta/intent_extraction.py`
- `tests/test_chat_prompt_modules.py`
- `tests/test_chat_manager_prompt_design.py`
- `tests/test_post_tool_followup_recovery.py`
- `tests/eval/`

## Decisions

- **Built-in first.** The first-class solution should live in DAN's own workflow-authoring path, not depend on an external wiki being remembered.
- **Contract over folklore.** If a workflow rule matters repeatedly, it should be encoded once and reused, not rediscovered from tests.
- **Compact beats encyclopedic.** The generator needs a sharp contract, not a long generic manual.
- **One family, many surfaces.** Build, mutate, review, repair, and codegen should not each teach different workflow truths.

## Closeout

This plan family is complete because DAN now has:

- one shared workflow-generation contract assembly point in product code,
- one tested prompt-module path for normal chat authoring turns,
- equivalent contract reuse on repair, diagnosis, planner, and builder-codegen surfaces,
- audit metadata proving when workflow guidance was or was not injected,
- compare-mode eval support plus workflow-specific success metrics.

The remaining work from here is not foundational contract/hook work. It is ordinary follow-on maintenance:

- collapsing any future prompt fragments back into the shared helper,
- widening the eval battery with additional replay cases as new failures are discovered.

## Notes

- This plan is downstream of [38-16](38-16-workflow-build-contract-and-repair.md). `38-16` hardens the build boundary; `38-18` hardens the knowledge injected before and during generation.
- This also builds on [38-7](38-7-chat-wf-gen-fixes.md), which fixed confirmed bugs but did not yet create one shared workflow-generation contract.
- The runtime taxonomy work in Plan 40 remains a core upstream source for stable engine facts and should not be duplicated loosely in prompts.
