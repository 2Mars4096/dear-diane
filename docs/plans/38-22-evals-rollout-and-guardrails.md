# 38-22: Evals, Rollout & Guardrails

**Parent:** [38-18-workflow-generation-contract-and-hooks](38-18-workflow-generation-contract-and-hooks.md)
**Status:** completed
**Goal:** Validate the workflow-generation contract and hook design against real failure cases, then roll it out in a measured way with explicit guardrails and regression checks.

## Problem

Workflow-generation improvements are easy to over-claim because many prompts look better without actually improving runnable output.

For this plan family, success is not:

- "the prompt is more detailed,"
- "the mutation preview looks plausible,"
- "a few manually inspected workflows seem cleaner."

Success is:

- fewer malformed or semantically brittle generated workflows,
- fewer repeated repair loops for known engine-contract mistakes,
- better behavior on the hard workflow scenarios already represented in tests and manual review findings.

## What Must Be Evaluated

The eval set should include confirmed workflow-generation failure modes such as:

- wrong or missing tool/node ports,
- bad prompt placeholder syntax,
- incorrect `for_each` body wiring,
- invalid or incomplete body-graph replacement,
- `code_operator` stubs that are schema-valid but not executable,
- repair prompts that fail to apply the last valid preview,
- prompts that over-claim run-readiness,
- workflow review prompts that miss mechanical engine constraints.

The eval set should also include a negative-control slice:

- non-workflow ask/query turns that should **not** receive workflow-generation guidance,
- workflow-adjacent informational turns that mention workflows without requesting build/edit/review/repair.

## Rollout Strategy

The contract/hook system should not be turned on everywhere blindly.

Recommended rollout order:

1. prompt-level / helper-level tests
2. focused workflow-generation evals
3. mutate/repair surfaces
4. build surfaces
5. optional expansion to planner/codegen and build-and-run shortcuts

## Tasks

- [x] 1. Define the eval battery
  - [x] 1-1. Convert recent real workflow-generation failures into stable regression cases.
  - [x] 1-2. Label each case by failure class: ports, templates, subgraphs, code, repair, honesty.
  - [x] 1-3. Include both generation-time and repair-time cases.

- [x] 2. Define success metrics
  - [x] 2-1. Structural validity rate
  - [x] 2-2. run-ready / preview-valid rate
  - [x] 2-3. number of repair turns needed
  - [x] 2-4. frequency of repeated mechanical mistakes
  - [x] 2-5. false-confidence / over-claim rate
  - [x] 2-6. workflow-guidance leakage rate into non-workflow turns

- [x] 3. Add rollout guardrails
  - [x] 3-1. Ship behind a scoped flag or narrow call-site enablement first.
  - [x] 3-2. Allow comparison of baseline vs contract-injected behavior.
  - [x] 3-3. Keep rollback simple if prompt size or behavior regresses.
  - [x] 3-4. Require green prompt-level and negative-control tests before widening the enablement surface.

- [x] 4. Add observability
  - [x] 4-1. Record whether the contract module was injected for each workflow-authoring turn.
  - [x] 4-2. Record which surface used which supplement: build, mutate, review, repair.
  - [x] 4-3. Correlate contract-injected turns with preview validity and run outcomes.
  - [x] 4-4. Record when workflow-generation guidance is intentionally *not* injected, so leakage can be audited.

- [x] 5. Define drift prevention
  - [x] 5-1. Add tests ensuring the prompt module stays wired to the intended surfaces.
  - [x] 5-2. Add tests for known workflow-generation rules so future cleanup does not remove them accidentally.
  - [x] 5-3. Define when runtime changes require contract/eval updates.

## Primary Files

- `tests/eval/__main__.py`
- `tests/eval/__init__.py`
- `tests/eval/runner.py`
- `tests/eval/report.py`
- `tests/eval/workflow_contract_comparison_prompts.json`
- `tests/test_eval_runner.py`
- `tests/test_eval_semantic_patch.py`
- `tests/test_chat_manager_prompt_design.py`
- `tests/test_post_tool_followup_recovery.py`
- `src/dan/server/chat_manager.py`
- `src/dan/server/audit.py`

## Decisions

- **Measure real workflow outcomes.** Prompt detail alone is not evidence.
- **Roll out narrowly first.** Workflow-authoring behavior is too central to change without controlled comparison.
- **Regression cases should come from real failures.** The best battery is the one already paid for in prior debugging.

## Progress

Implemented:

- regression tests for workflow contract injection on authoring turns,
- repair-path regressions for invalid mutation-plan repair,
- codegen and planner prompt regressions,
- non-workflow leakage guards,
- lightweight audit fields for prompt module ids and workflow guidance surface,
- request-scoped workflow-contract enable/disable override via `surface_context["workflow_generation_contract_enabled"]`,
- eval client and runner propagation of the contract variant across first turns, follow-up turns, and clarification auto-replies,
- CLI support for `--workflow-contract enabled|disabled|compare|both`,
- a dedicated `tests/eval/workflow_contract_comparison_prompts.json` battery for contract-sensitive build cases,
- repair-oriented follow-up prompts in the comparison battery,
- audit-derived workflow-guidance fields stored on eval records and summarized in eval reports,
- explicit report-level success metrics for structural validity, run-ready rate, repair turns, repeated mechanical failures, false-confidence, and leakage,
- regression coverage for override precedence, eval variant propagation, and report-level contract/guidance summaries,
- a concrete drift trigger: any runtime change to node/tool manifests, placeholder semantics, control-flow body contracts, code-node output rules, retry defaults, or chat audit prompt-metadata fields must update the workflow contract text and at least one eval/report assertion in `tests/eval/`.

Remaining:

- none for this plan slice; future work is optional battery expansion, not a blocker for completion.

## Notes

- This sub-plan reuses the workflow-generation quality work from Plans 24 and 33 rather than inventing a separate evaluation culture.
- The rollout explicitly watches for two-sided regressions:
  - prompts that become larger but not better,
  - prompts that improve build behavior but degrade mutation/repair behavior.
- A practical battery here mixes:
  - prompt/helper unit tests,
  - synthetic workflow-authoring regression cases,
  - replay of recent real failures,
  - compare-mode reporting with audit-backed injection metadata.
