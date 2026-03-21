# 38-17: Trace-to-Workflow Distillation

**Parent:** [38-review-hardening](38-review-hardening.md)
**Status:** in_progress *(v1 backend slice implemented 2026-03-21)*
**Goal:** Let DAN solve a task first, then distill the audited action trace into a reusable workflow draft that is generalized, contract-validated, and honest about whether it is only a draft or already run-ready.

## Problem

One-shot workflow generation is improving, but it is still not the most reliable path for harder tasks. In many real cases, DAN can complete the task conversationally or with tools, yet still fail to author the best workflow on the first attempt.

The missing capability is:

1. execute the task normally,
2. capture what actually worked,
3. generalize the successful trace into a reusable workflow draft,
4. validate the draft before calling it reusable.

Without that, DAN has to rely too heavily on up-front workflow synthesis for complex requirements.

## Design

### Core principle

**Run first, distill second.**

The source of truth for harder workflow authoring should be a successful audited execution trace, not only an abstract requirement prompt.

### Distillation contract

The first version should stay narrow:

- source: persisted `ChatAuditRecord` / audited tool trace
- normalization: convert raw tool calls into typed, ordered actions
- generalization: lift concrete paths/queries/URLs/content into reusable parameters
- output: a `WorkflowIntent` draft plus distillation metadata
- validation: compile the intent and pass it through the shared workflow build contract

### Draft status model

The distillation path should distinguish:

- `no_draft` — not enough meaningful trace structure to generalize
- `draft_only` — generalized intent exists but was not compiled/validated
- `validated_draft` — compiled and passes build validation
- `run_ready_draft` — validated and also passes run-readiness checks

### Scope limits

The first version should not try to infer every hidden semantic step. It should:

- keep the action taxonomy small,
- preserve uncertainty in descriptions instead of inventing topology,
- prefer linear or lightly-augmented stage sequences,
- rely on the existing workflow contract validator for the final truth boundary.

## Tasks

- [x] 1. Add a trace distillation module
  - [x] 1-1. Introduce a compact normalized action model for audited tool calls.
  - [x] 1-2. Map common audited tools into a small action taxonomy such as `research`, `read`, `compute`, `write`, `run_control`.
  - [x] 1-3. Skip purely operational/meta actions that should not become workflow stages.

- [x] 2. Generalize concrete traces into reusable inputs
  - [x] 2-1. Detect reusable parameter candidates from paths, URLs, queries, prompts, and large text payloads.
  - [x] 2-2. Replace concrete literals with placeholders while preserving examples/provenance.
  - [x] 2-3. Keep the parameterization heuristic and bounded in v1; ambiguous values should stay literal rather than over-generalized.

- [x] 3. Build workflow drafts from traces
  - [x] 3-1. Convert normalized actions into a `WorkflowIntent` draft.
  - [x] 3-2. Add only small deterministic structure where needed, such as an explicit transform/synthesis stage before a final write artifact.
  - [x] 3-3. Keep the first version biased toward simple, linear workflows that are easy to validate.

- [x] 4. Validate distilled drafts
  - [x] 4-1. Compile the distilled `WorkflowIntent` through the intent compiler.
  - [x] 4-2. Pass the compiled graph through the shared workflow build contract.
  - [x] 4-3. Surface whether the result is merely generalized, validated, or fully run-ready.

- [x] 5. Expose the capability through a narrow API surface
  - [x] 5-1. Add one endpoint that accepts an audited turn ID and returns the distilled draft.
  - [x] 5-2. Allow callers to request compile/validation in the same call.
  - [x] 5-3. Return enough provenance to explain what was abstracted and why the draft is or is not runnable yet.

- [x] 6. Add focused regression coverage
  - [x] 6-1. Search/research plus write traces
  - [x] 6-2. Read/transform/write traces
  - [x] 6-3. Parameter extraction and placeholder preservation
  - [x] 6-4. API-level compile/validation success and no-draft cases

## Primary Files

- `src/dan/server/audit.py`
- `src/dan/server/chat/mutation_parser.py`
- `src/dan/server/trace_workflow_distiller.py` *(new)*
- `src/dan/server/routers/experiences.py`
- `src/dan/meta/intent_schema.py`
- `src/dan/meta/intent_compiler.py`
- `src/dan/meta/workflow_contract.py`
- `tests/test_server/`

## Decisions

- **Successful traces are the safest seed for harder workflow authoring.**
- **Distillation must stay honest.** A generalized trace is not automatically a validated workflow.
- **The build contract remains authoritative.** Distillation can draft; validation decides usability.
- **V1 stays small.** The first goal is reusable draft extraction, not full automatic promotion into a workflow library.

## Notes

- This complements the bounded multi-round diagnosis/repair path for one-shot generation. Complex tasks can now improve through two strategies:
  - try to build directly with bounded repair,
  - if execution succeeds conversationally, distill the successful trace into a reusable workflow draft.
- Implemented in v1:
  - audited chat turn -> normalized action trace -> generalized `WorkflowIntent`
  - optional compile + workflow-contract validation through `/api/experiences/trace-draft`
  - focused regression coverage for research/write, transform/write, and no-draft cases
- A later follow-up can add replay scoring, promotion thresholds, and cross-trace clustering before workflows are auto-promoted.
