# 38-16: Workflow Build Contract & Repair Hardening

**Parent:** [38-review-hardening](38-review-hardening.md)
**Status:** in_progress *(core build-contract, bounded repair, run-readiness, and provenance/status slice landed 2026-03-25)*
**Goal:** Make workflow build/save/apply/run trustworthy by enforcing one explicit build contract at the boundary and adding a bounded mechanical repair layer for fixable workflow graph defects.

## Problem

The high-level flow now mostly works:

`message -> concierge -> build path`

The remaining trust gap is the build artifact itself. The system can produce a workflow-shaped graph that still fails in practical ways:

- workflow ID vs workflow name are unclear or inconsistent,
- node IDs/names drift or collide,
- edge endpoints reference non-existent or wrong ports,
- input/output ports do not match schema expectations,
- graphs are schema-valid but not run-ready,
- mechanical issues are spread across generation, validation, mutation, and diagnosis layers with no single build-boundary contract.

The system needs one place that answers:

1. Is this workflow structurally valid?
2. Is it canonical and internally consistent?
3. Is it run-ready enough to save/apply/claim success?
4. If not, is the defect mechanical enough to auto-fix?

## Current State Review

The existing code already has useful validation pieces, but they do not yet form one trustworthy workflow-build boundary:

- `src/dan/meta/planner.py:174` `validate_codegen_output()` wraps `Graph.model_validate()` and `validate_graph()`, but it is codegen-centric and does not define a broader workflow identity or run-readiness contract.
- `src/dan/server/gateway/router.py` validates the generated graph shape, emits `plan_created`, and may call `start_run()` immediately, but there is no unified "validated vs run-ready" gate at that boundary.
- `src/dan/models/graph.py` provides `metadata.name`, while persisted `workflow_id` is carried separately by higher-level code. That split is currently under-specified.
- Current graph validation is strong on structure, but weaker on workflow-level concerns such as user-facing naming quality, save/apply truthfulness, and repair provenance.

## Design

### Core principle

Separate **semantic generation** from **mechanical contract enforcement**.

- LLM/codegen/intent paths decide *what* to build.
- The build contract validator decides whether the result is consistent and usable.
- A bounded repair layer may fix mechanical defects, but it must not invent new business logic or topology beyond clear local fixes.

### Build contract

The build boundary should validate at least:

- workflow identity:
  - persisted `workflow_id` is legal and stable,
  - human-facing workflow `name` is present and distinct from the ID contract when needed,
- node identity:
  - node IDs are unique, canonical, and stable,
  - node names are non-empty and collision-safe for user-facing summaries,
- ports and schemas:
  - every edge endpoint port exists,
  - required ports are satisfied,
  - source/target schema compatibility holds,
  - common alias/default-port mismatches are normalized or rejected explicitly,
- graph/runtime readiness:
  - entry/exit points are valid,
  - canonical node taxonomy is respected,
  - subgraph-bearing nodes have valid body references,
  - the graph is safe to save/apply and sufficiently ready to run.

### Status contract

The system should stop collapsing all successful-looking builds into one bucket. At minimum, the boundary should distinguish:

- `proposed` — graph candidate exists but has not passed the full build contract
- `validated` — schema/design-time/build-boundary checks passed
- `applied` — validated graph has been persisted or mutated into the current workflow
- `run_ready` — validated graph also passes the stricter execution preflight

### Bounded repair layer

Allow only local mechanical fixes, for example:

- normalize workflow IDs,
- dedupe or rewrite colliding node IDs,
- repair obvious default-port mismatches,
- normalize stale edge shape or legacy field aliases,
- reconcile workflow metadata/name fields,
- recompute entry/exit points when safe.

Do **not** auto-invent missing substantive nodes, major topology, or semantic prompts.

## Tasks

- [x] 1. Define the workflow build contract explicitly
  - [x] 1-1. Introduce a structured `WorkflowBuildContractReport` model with `errors`, `warnings`, `auto_fixes_applied`, and `run_ready` fields.
  - [x] 1-2. Define issue categories: `workflow_identity`, `workflow_metadata`, `node_identity`, `port_endpoint`, `schema`, `taxonomy`, `subgraph`, `entry_exit`, `run_readiness`.
  - [x] 1-3. Document which categories are fatal, repairable, or warning-only.
  - [x] 1-4. Define the external-vs-internal identity contract explicitly: persisted `workflow_id`, `Graph.metadata.name`, optional display name, and how they are normalized or preserved.

- [x] 2. Add a single build-boundary validator
  - [x] 2-1. Create one validator entry point that runs after build generation and before save/apply/run success is claimed.
  - [x] 2-2. Reuse existing layers where possible: `Graph.model_validate()`, `validate_graph()`, canonical taxonomy checks, and quality checks, but return one unified structured report.
  - [x] 2-3. Add explicit checks for workflow ID/name consistency and user-facing node name quality, which are not the main focus of low-level graph validation today.
  - [x] 2-4. Fold the current planner-centric validation into this shared contract instead of leaving separate codegen-only and gateway-only success checks.

- [x] 3. Add bounded mechanical repair
  - [x] 3-1. Introduce a small allowlist of deterministic local fixes.
  - [x] 3-2. Apply fixes only when the mapping is obvious and local.
  - [x] 3-3. Include the common workflow-build fixes explicitly: workflow ID slug normalization, workflow metadata/name normalization, colliding node ID dedupe, default-port alias normalization, and legacy edge-field normalization.
  - [x] 3-4. Re-run the full build contract validator after every applied fix batch.
  - [x] 3-5. If repairs fail or defects are semantic, hand off to the existing diagnosis/re-prompt path instead of looping locally.

- [ ] 4. Integrate into all workflow build/save/apply surfaces
  - [x] 4-1. Intent compiler path
  - [x] 4-2. Codegen path
  - [x] 4-3. Chat mutation/build preview path before auto-apply
  - [x] 4-4. Gateway/meta-controller path before `plan_created` is treated as a usable workflow and before any `start_run()` shortcut.
  - [x] 4-5. Any build-and-run shortcut path before `start_run`
  - [x] 4-6. Ensure user-visible responses say `proposed`, `validated`, `applied`, or `run-ready` accurately.

- [x] 5. Add run-readiness validation
  - [x] 5-1. Distinguish `schema-valid` from `run-ready`.
  - [x] 5-2. Add a lightweight run-readiness pass that catches common execution blockers before users hit run: invalid entry/exit, unresolved subgraphs, impossible required-port state, and missing executable path from entry to at least one exit.
  - [x] 5-3. Keep this pass preflight-only. It may inspect structure and declared requirements, but it must not execute arbitrary workflow logic.
  - [x] 5-4. Surface precise reasons when a workflow is saveable but not yet runnable.

- [x] 6. Define the overarching fixing approach
  - [x] 6-1. Classify every failure into one of three buckets: `mechanical_auto_fix`, `semantic_reprompt_or_diagnosis`, `hard_fail`.
  - [x] 6-2. Mechanical fixes run once per batch, then revalidate.
  - [x] 6-3. Semantic defects route into the existing bounded diagnosis/re-prompt path with the contract report as structured input.
  - [x] 6-4. Hard failures stop the build/apply/run claim and surface a compact, actionable report.

- [x] 7. Add observability and repair provenance
  - [x] 7-1. Emit structured events or metadata for contract failures, auto-fixes, final build status, and validator handoff reason.
  - [x] 7-2. Include a short user-facing build summary: what was validated, what was auto-fixed, and what still needs manual correction.

- [ ] 8. Add focused regression coverage
  - [x] 8-1. Duplicate/colliding node IDs
  - [x] 8-2. Wrong or missing edge ports
  - [x] 8-3. Workflow ID/name normalization
  - [x] 8-4. Graphs that are schema-valid but not run-ready
  - [x] 8-5. Repairable vs non-repairable defect separation
  - [x] 8-6. Gateway/build-and-run path cannot claim success or start a run before the contract report clears.

## Primary Files

- `src/dan/server/chat_manager.py`
- `src/dan/meta/planner.py`
- `src/dan/meta/diagnosis.py`
- `src/dan/server/gateway/router.py`
- `src/dan/validation/graph.py`
- `src/dan/models/graph.py`
- `src/dan/models/node_taxonomy.py`
- `src/dan/server/graph_mutator.py`
- `tests/test_meta/`
- `tests/test_concierge/`

## Decisions

- **One boundary, one report.** Build validity should not be inferred from scattered exceptions and log lines.
- **Repair stays mechanical.** Semantic fixes remain in diagnosis/re-prompt territory.
- **Run-ready is stricter than schema-valid.** Both statuses should be surfaced explicitly.
- **User-facing honesty matters.** The system should stop saying a workflow “works” when it has only passed a weaker validation layer.
- **Workflow identity is part of the contract.** `workflow_id` and human-facing naming should not be left to implicit conventions across layers.

## Notes

- This plan is a follow-up to the earlier workflow-generation hardening work in 24, 33, 38-7, and 40. It does not replace those plans; it adds the missing contract boundary between “graph generated” and “workflow actually usable.”
- This plan pairs naturally with 38-15 and 38-8:
  - 38-15 reduces workflow misroutes before build begins,
  - 38-8 sharpens concierge role/stage scope,
  - 38-16 hardens the build artifact itself.
- [38-17](38-17-trace-to-workflow-distillation.md) is the adjacent upstream follow-up for harder tasks: it improves the quality of candidate workflow drafts by distilling successful audited executions before those drafts hit this contract boundary.
- Concrete review finding behind this plan: current success claims are still split across planner validation, chat/build mutation handling, and gateway `plan_created` / `start_run` behavior, which makes it too easy for a graph to look successful before it is truly usable.
- The core slice is implemented in `src/dan/meta/workflow_contract.py`, is consumed by `validate_codegen_output()` in `src/dan/meta/planner.py`, gates `plan_created` / `start_run` in `src/dan/server/gateway/router.py`, and is exposed through the draft-validation path in `src/dan/server/routers/experiences.py`. As of 2026-03-25, the remaining repair gap is closed: colliding node-id dedupe and structured semantic handoff into diagnosis are both landed.
