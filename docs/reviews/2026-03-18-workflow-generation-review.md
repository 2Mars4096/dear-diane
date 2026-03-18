# Workflow Generation & Execution Review

Date: 2026-03-18

Scope: review of whether a user can ask DAN to generate a workflow, whether the request is routed into the workflow-building path correctly, and whether the resulting workflow can execute successfully in the current engine/runtime stack.

## What I Ran

- `pytest -q tests/test_server/test_build_from_intent.py tests/test_server/test_mutation_regression.py tests/test_server/test_mutation_quality.py tests/test_chat_manager_tool_handoff.py tests/test_chat_manager_tool_turn_cap.py tests/test_chat_manager_codegen_resilience.py`
- `pytest -q tests/test_server/test_run_manager.py tests/test_engine/test_integration.py tests/test_engine/test_real_executor_integration.py tests/test_engine/test_tool_executor_integration.py tests/test_engine/test_composite_executor.py tests/test_engine/test_orchestrator.py`
- `pytest -q tests/test_concierge/test_tiered_dispatch.py tests/test_concierge/test_triage.py tests/test_concierge/test_fast_commands.py tests/test_concierge/test_surface_policy.py`
- direct repro of fallback triage + tier executor routing for a plain `"try again"` message inside a project with a linked workflow

Results:

- generation-facing suites passed: `129 passed`
- execution/runtime suites passed: `44 passed`
- concierge routing/dispatch suites passed: `83 passed`
- I did not find a confirmed workflow-engine execution failure in this pass
- I did find one workflow-generation usability/routing regression

## Assessment

The core answer is mostly yes: DAN appears capable of generating a workflow, and the workflow engine appears capable of running workflows. The generation/mutation path, run manager, and executor/integration suites are broadly healthy right now.

The main problem I found is not in the executor itself; it is in the language-routing layer that decides when a user is asking to edit/build a workflow. That means the product can still produce and run workflows, but users may be pushed into unintended workflow mutation flows more easily than they should be.

## Findings

### P2: Generic retry language now over-routes into workflow editing whenever the project has any linked workflow

Evidence:

- `src/dan/server/concierge/triage.py:879`-`889` now treats any linked workflow as enough workflow context for `_WORKFLOW_BUILD_VERB_RE` matches such as `"retry"` or `"try again"`, even when the user message contains no workflow noun at all.
- `src/dan/server/concierge/triage.py:938`-`956` then upgrades that fallback into `intent="agent"`, `tier=2`, and `route.target="workflow"` when `workflow_edit` is present.
- Direct repro in the current tree:
  - input text: `"try again"`
  - context: active project with `linked_workflow_ids=['workflow-quarterly']`
  - observed fallback result: `route_target=workflow`, `action_hints=['workflow_edit']`
- `src/dan/server/concierge/tier_executors.py:286`-`293` then sets `allow_mutation_tool=True` whenever `route_target == "workflow"`, so this fallback classification directly unlocks `plan_graph_mutations`.
- The new tests in `tests/test_concierge/test_triage.py:451`-`458` explicitly lock in this behavior by asserting that plain `"try again"` should infer `workflow_edit` when a linked workflow exists.

Why this matters:

- From a user's perspective, `"try again"` is often a generic retry request for the last answer, not a request to mutate the workflow graph.
- In a project that merely has a linked workflow, that plain retry language now steers DAN into workflow-edit mode and enables the mutation tool, which can make the product feel unpredictable or overly eager to rewrite the workflow.
- This is especially risky for workflow generation UX because retry/rephrase prompts are common when the user is still exploring requirements.

## Notes

- The workflow engine itself looked healthy in this pass: `tests/test_server/test_run_manager.py` plus the executor/integration suites were green.
- The workflow-generation path also looked broadly healthy: build-from-intent, mutation-regression, mutation-quality, and chat handoff/turn-cap/codegen resilience suites all passed.
- Residual risk: this pass did not run a live-provider end-to-end evaluation. The evidence here is strong for local logic/wiring correctness, but real-model prompt adherence remains a product-quality variable outside these mocked/provider-controlled regression suites.
