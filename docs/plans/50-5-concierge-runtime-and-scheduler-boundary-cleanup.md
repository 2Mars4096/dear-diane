# 50-5: Concierge Runtime and Scheduler Boundary Cleanup

**Parent:** [50-structural-consolidation-and-module-reduction](50-structural-consolidation-and-module-reduction.md)
**Status:** not-started
**Goal:** Narrow concierge orchestration, fast-command/schedule adaptation, and scheduler authority into clearer neighboring boundaries instead of one expanding control-plane sink.

## Dependencies

- **50-1** should land first for ownership mapping and guardrails.
- Coordinate with **50-2** so workflow identity/invariant helpers are already stable before schedule-related code moves.
- Sequenced after the **49-series** concierge hardening if that work is still in-flight: 49 adds task lifecycle and dispatch-mode contracts to `runtime/__init__.py` and `scheduler.py`; this plan narrows the resulting file boundaries without conflicting with those additions.

## Tasks

- [ ] 1. Narrow `runtime/__init__.py`
  - [ ] 1-1. Separate orchestration from fast-command adaptation, reassurance/progress signaling, and schedule-follow-up rewriting. Candidate extraction targets: `runtime/schedule_commands.py` (schedule-follow-up rewriting, schedule-related turn adaptation), `runtime/progress_ux.py` (reassurance/progress signaling, queued-hint delivery).
  - [ ] 1-2. Keep `process()` / `_process_inner()` focused on orchestration, state progression, and high-level dispatcher handoff.
  - [ ] 1-3. Target scope after narrowing: `runtime/__init__.py` should own orchestration and state progression only, shedding at least the schedule-follow-up rewriting and progress/reassurance signaling responsibilities. The file should lose at least 30% of its current lines.
- [ ] 2. Standardize queue/context/prompt ownership inside the concierge path
  - [ ] 2-1. Decide whether queue ownership belongs at project, task, or session level and remove overlapping ownership between dispatcher intake queues and background task queues.
  - [ ] 2-2. Reduce repeated context resolution/materialization so the same turn is not re-resolved by multiple layers without a clear boundary change.
  - [ ] 2-3. Define clearer prompt-stage slots for stage overlays, schedule-follow-up rewrites, and workflow continuity blocks so runtime metadata stops carrying overlapping prompt state.
- [ ] 3. Extract schedule-command semantics out of the scheduler daemon boundary
  - [ ] 3-1. Move workflow resolution, timezone/default interpretation, and user-facing schedule command normalization closer to the command surface. Note: 48-4 already shipped timezone preference management and `/timezone` / `/tz` commands; this task moves the remaining schedule-command semantics (workflow resolution, trigger parsing, natural-language schedule rewriting) that are still embedded in the daemon file.
  - [ ] 3-2. Keep lease/authority, fire-time execution, and daemon concerns grouped together.
- [ ] 4. Remove duplicated bridging logic
  - [ ] 4-1. Delete embedded schedule special-cases from runtime once the neighboring helper/module owns them.
  - [ ] 4-2. Remove duplicate delivery fallback or workflow-resolution glue from the wrong owner.
- [ ] 5. Clarify file/module ownership
  - [ ] 5-1. Introduce only the minimum new modules needed inside `src/dan/server/concierge/runtime/` or `concierge/` to reflect real boundaries. Candidate names: `runtime/schedule_commands.py`, `runtime/progress_ux.py`, `concierge/schedule_surface.py`.
  - [ ] 5-2. Ensure naming matches actual responsibility.
- [ ] 6. Regressions
  - [ ] 6-1. Revalidate fast commands, workflow schedule follow-ups, timezone/default behavior, delivery fallback, and fire-time execution.

## Primary Files

- `src/dan/server/concierge/runtime/__init__.py`
- `src/dan/server/concierge/scheduler.py`
- `src/dan/server/concierge/tier_executors.py` (2146 lines — tiered dispatch execution, closely coupled to the runtime orchestration boundary being narrowed; verify that extracted schedule/progress modules do not create circular imports or break the dispatch handoff contract)
- nearby `src/dan/server/concierge/runtime/` support modules
- new extraction targets (see task 4-1)

## Success Criteria

- concierge runtime orchestration is materially narrower (at least 30% line reduction) and no longer the default home for scheduling/control-plane special cases
- scheduler daemon authority is clearly separated from user-facing schedule-command semantics
- duplicated schedule bridging logic is removed rather than mirrored across two files
- duplicate queue/context/prompt ownership in the concierge path is reduced enough that 46-6 can standardize the remaining contract instead of inheriting the current ambiguity

## Decisions

- Keep patch-first extraction inside the existing concierge package; do not create a parallel control-plane framework.
- Scheduling authority and user-facing schedule semantics are related, but they are not the same boundary and should not keep widening the same file.

## Notes

- This plan should leave the concierge package easier to extend without every new fast command or scheduling nuance landing in `runtime/__init__.py`.
- 48-4 work (timezone preferences, `/timezone` and `/tz` commands) is done and not re-scoped here; only the remaining schedule-command semantics are in scope.
- **Scope boundary with 46-6:** This plan (50-5) narrows the *files* — extracts schedule-command and progress-UX modules, reduces duplicate bridging logic. 46-6 then reshapes the concierge prompt pipeline inside those cleaner files — slot-based assembly, stage overlay quality, typed handoff envelope. 50-5 moves code; 46-6 improves what the code produces.
