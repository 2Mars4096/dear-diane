# 50-4: Run Manager Lifecycle and Finalization Split

**Parent:** [50-structural-consolidation-and-module-reduction](50-structural-consolidation-and-module-reduction.md)
**Status:** not-started
**Goal:** Keep `RunManager` as the authoritative run lifecycle owner while extracting the post-run finalization/learning tail into a separate boundary.

## Dependencies

- **50-2** should land first so run start/resume invariants already belong to `RunManager` before the class is narrowed.
- Coordinate with **50-3**: facade retirement moves run-launch callers from `chat_manager.py` into `chat/*` and `agent_runtime/*`. The internal split here is independent, but the external caller surface may shift — verify test coverage against the post-50-3 call sites, not just the pre-retirement callers.

## Tasks

- [ ] 1. Map the current post-run tail
  - [ ] 1-1. Inventory telemetry enrichment, reflection scheduling, principle/rule persistence, experience consolidation, and outcome tracking currently owned by `RunManager`.
  - [ ] 1-2. Separate lifecycle-critical work from post-completion side effects.
- [ ] 2. Extract a post-run finalizer boundary
  - [ ] 2-1. Introduce one explicit boundary — expected shape: a `RunFinalizer` class or a `run_finalization.py` module adjacent to `run_manager.py` — responsible for post-run finalization and learning-related side effects. The boundary receives a completed `RunRecord` and handles telemetry/reflection/learning without holding lifecycle authority.
  - [ ] 2-2. Route the relevant completion paths through that boundary once per finished run.
  - [ ] 2-3. Define event-bus subscription strategy: `RunManager._event_callback()` continues to receive engine events for lifecycle transitions (state changes, cancellation) and forwards the terminal completion event to the finalizer via direct call — not a separate bus subscription — to preserve ordering guarantees.
- [ ] 3. Narrow `RunManager`
  - [ ] 3-1. Keep `start_run`, `resume_run`, `rerun_from_checkpoint`, `cancel`, subscriptions, and authoritative state transitions in `RunManager`.
  - [ ] 3-2. Reduce `_event_callback()` and completion handling to lifecycle/persistence concerns plus finalizer invocation.
- [ ] 4. Clarify reflection ownership
  - [ ] 4-1. Decide whether reflection-launch construction remains a lifecycle concern or belongs fully inside the finalizer path.
  - [ ] 4-2. Keep any remaining recursive run starts explicit and bounded: maximum reflection depth of 2 (one reflection re-run per original run, no cascading).
- [ ] 5. Regressions and performance
  - [ ] 5-1. Revalidate run completion, reflection, principle/rule persistence, telemetry, and recovery paths.
  - [ ] 5-2. Watch for completion-latency regression after extracting the tail — the finalizer must not add observable delay to the "run done" user-facing event.

## Primary Files

- `src/dan/server/run_manager.py`
- new `src/dan/server/run_finalization.py` (or similar)
- adjacent telemetry/reflection/learning modules already called by `RunManager`

## Success Criteria

- `RunManager` owns lifecycle authority, not the full learning/reflection/governance tail
- post-run side effects run through one explicit finalization boundary
- event-bus subscription ordering is preserved (no race between lifecycle and finalization)
- run completion remains correct and performance-neutral on the focused validation basket

## Decisions

- Lifecycle correctness wins over maximum extraction. If a concern must stay near lifecycle state transitions, keep it there and document why.
- The goal is a clearer boundary, not a proliferation of tiny helper classes.
- Finalizer receives terminal events via direct call from `_event_callback()`, not a separate event-bus subscription, to avoid ordering ambiguity.

## Notes

- This plan directly addresses the review finding that `RunManager` has become the sink for every "end of run" concern.
