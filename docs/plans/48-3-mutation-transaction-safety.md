# 48-3: Mutation Transaction Safety

**Parent:** [48-workflow-continuity-and-control-plane-hardening](48-workflow-continuity-and-control-plane-hardening.md)
**Status:** completed
**Goal:** Make workflow edits safer by preserving obvious existing invariants, repairing only unambiguous mistakes, and surfacing dry-run failures more usefully.

## Dependencies
- **48-1** must land first: stale-revision detection (tasks 2-3, 3-3) depends on the revision/fingerprint model defined there.
- **48-2** should land or be coordinated: follow-up context packs carry the revision state that mutation callers use to detect stale edits. If 48-3 ships in parallel with 48-2, scope 2-3/3-3 to "detect mismatch when caller provides expected revision" only.
- Single-writer assumption: this plan does not address concurrent edits from multiple tabs/sessions. Last-write-wins semantics are acceptable for this phase; distributed merge is out of scope.

## Success Criteria
- Required-input disconnects from partial rewires are repaired deterministically or fail with a named failure class — never silently drop the edge.
- Stale-revision mutations never apply without an explicit refresh or user-facing refusal.
- Ambiguous rewires fail closed with a tagged failure class rather than guessing.
- Dry-run failure reports carry stage, failure class, and structured context sufficient for a repair prompt.
- All safe-repair classes and ambiguous-rejection classes have focused regression tests.

## Tasks
- [x] 1. Catalog the common partial-edit failure classes (deliverable: enumerated `MutationFailureClass` enum or equivalent set of named codes used by task 3-1 tags)
  - [x] 1-1. Missing input variable ports on `input` nodes.
  - [x] 1-2. Stale port aliases.
  - [x] 1-3. Duplicate add-node / duplicate add-edge drift observed in recent mutation preview / duplicate-repair regressions.
  - [x] 1-4. Required-input disconnects caused by partial rewires.
  - [x] 1-5. Stale-revision edits where the mutation plan no longer matches the saved workflow revision it was generated against.
- [x] 2. Extend deterministic self-repair conservatively
  - [x] 2-1. Preserve existing edges when removing them would disconnect a still-required input without a replacement.
  - [x] 2-2. Keep repair scoped to clearly safe cases; ambiguous cases should still fail and trigger re-planning.
  - [x] 2-3. Refuse or re-plan against a fresh snapshot when the workflow revision changed underneath the pending mutation.
- [x] 3. Improve mutation failure attribution
  - [x] 3-1. Tag dry-run failures by stage and failure class.
  - [x] 3-2. Feed structured failure context back into mutation repair prompts. Define the JSON shape and max size for failure context injected into prompts; prefer stable failure codes plus short summaries over raw graph dumps to avoid prompt-injection risks.
  - [x] 3-3. Distinguish stale-revision failures from graph-shape failures so repair can refresh context instead of attempting the wrong rewrite.
- [x] 4. Keep graph semantics honest
  - [x] 4-1. Do not let self-repair silently rewrite user intent when a plan is materially ambiguous. (This is the product principle that 2-2's "fail and trigger re-planning" implements mechanically.)
  - [x] 4-2. Preserve diagnostics so the user and logs show what was repaired versus what still failed.
- [x] 5. Add regression coverage
  - [x] 5-1. Add focused tests for each safe repair class.
  - [x] 5-2. Add negative tests showing ambiguous rewires still fail instead of being guessed incorrectly.

## Decisions
- Mutation repair should preserve obviously valid existing structure, not invent new workflow semantics.
- A failed dry run is acceptable; a silently wrong graph is not.
- Stale workflow revisions should fail or trigger refresh explicitly rather than being silently merged by repair logic.
- Failure attribution now ships as structured `OperationError(stage, failure_class, context)` plus bounded repair-prompt payloads instead of raw string-only dry-run errors.

## Notes
- This sub-plan builds directly on the recently landed mutation repairs for missing input-variable ports and required-input edge preservation.
- Explicit undo/history UX is not part of this phase; this plan focuses on safe transactions and honest failure when a mutation no longer matches the current saved graph.
- Re-planning (2-2) is a single automatic retry with enriched structured context, not an unbounded LLM retry loop. If the single retry also fails, the mutation fails closed with diagnostics. Any broader retry policy lives in the existing mutation-preview budget cap.
- `graph_mutator.py` now tags stale revision, duplicate add, required-input disconnect, ambiguous rewire, validation, compilation, and apply failures explicitly; mutation preview telemetry records `wf_mutate_compile` and `wf_dryrun`.
