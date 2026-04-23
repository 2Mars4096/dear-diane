# 55-3: Context, Artifact, And Provenance Membrane

**Parent:** [55-agent-level-stochastic-orchestration](55-agent-level-stochastic-orchestration.md)
**Status:** not-started
**Goal:** Make context quality, artifact promotion, and provenance explicit scheduler inputs so DAN can decide when to send better context, validate, reduce, or spawn more work.

## Tasks

- [ ] 1. Define context-state fields
  - [ ] 1-1. Track context completeness, freshness, compression loss, required references, and assumption debt
  - [ ] 1-2. Distinguish raw evidence, compressed summaries, briefs, and final-answer material
  - [ ] 1-3. Record which task and source produced each claim or artifact
- [ ] 2. Define artifact promotion
  - [ ] 2-1. Require validator gates before artifacts become final-answer material
  - [ ] 2-2. Support partial promotion for subclaims, files, tests, citations, and blocker reports
  - [ ] 2-3. Preserve unresolved assumptions instead of hiding them in aggregation prose
  - [ ] 2-4. Separate local reducer outputs from globally promoted artifacts so final aggregation waits on smaller, validated units rather than raw worker transcripts
- [ ] 3. Define context-improvement actions
  - [ ] 3-1. Let the scheduler choose to enrich context instead of spawning another worker
  - [ ] 3-2. Add reducer summaries that state what was compressed away
  - [ ] 3-3. Detect stale context when newer artifacts invalidate earlier briefs
- [ ] 4. Define barrier-aware aggregation
  - [ ] 4-1. Identify which aggregation/delivery steps are true serial barriers for each organism type
  - [ ] 4-2. Push partial reduction and validation earlier when it shrinks the final barrier without losing provenance
  - [ ] 4-3. Measure aggregation wait time caused by slow or uncertain upstream branches

## Decisions

- Context is a resource and a failure mode, not just a message payload.
- Reducers should be streaming and hierarchical where possible.
- Final delivery should assemble promoted artifacts and label unresolved assumptions clearly.
- Streaming reducers reduce barrier cost; they do not remove the need for a final coherent synthesis step.

## Notes

- This plan should reuse the richer worker-review membrane from `54-*` where possible.
- The first implementation can be a structured ledger attached to organism logs before it becomes a live scheduling policy.
