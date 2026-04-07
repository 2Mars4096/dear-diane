# 50-5: Concierge Runtime and Scheduler Boundary Cleanup

**Parent:** [50-structural-consolidation-and-module-reduction](50-structural-consolidation-and-module-reduction.md)
**Status:** completed
**Goal:** Narrow concierge orchestration, fast-command/schedule adaptation, and scheduler authority into clearer neighboring boundaries instead of one expanding control-plane sink.

## Dependencies

- **50-1** should land first for ownership mapping and guardrails.
- Coordinate with **50-2** so workflow identity/invariant helpers are already stable before schedule-related code moves.
- Sequenced after the **49-series** concierge hardening if that work is still in-flight: 49 adds task lifecycle and dispatch-mode contracts to `runtime/__init__.py` and `scheduler.py`; this plan narrows the resulting file boundaries without conflicting with those additions.

## Tasks

- [x] 1. Narrow dispatch-mode policy
  - [x] 1-1. Fix `_select_dispatch_mode()` (runtime/__init__.py:3470-3501): the current policy returns `BACKGROUND` whenever `route.target` is `workflow`/`run` or `intent` is `agent`/`plan`, which backgrounds normal single-shot turns that should execute in the foreground. This causes `tiered_dispatch.py:656-673` to emit `ChatTaskAckEvent` and return immediately instead of executing the root session, producing ack-only responses like "Working on it — Understanding your request…" instead of real answers.
  - [x] 1-2. New dispatch-mode policy: `INLINE` for fast commands/status/social; `FOREGROUND` for normal single-shot ask/agent/plan turns; `BACKGROUND` only for explicit long-running work gated by concrete signals (`workflow_run`, real run-control operations, explicit background/autonomy markers, intentionally detached large mutations) — not by broad categories like "intent is plan" or "route target is workflow" alone.
  - [x] 1-3. Verify fix against the `test_tiered_dispatch.py` failure cluster: `test_tier1_forwards_request_metadata_and_action_hints`, `test_tier2_build_override_skips_decomposition_and_calls_builder_directly`, `test_tier1_synthesizes_terminal_response_when_handler_stream_ends_early`, `test_low_confidence_triage_requests_clarification_and_retriages_reply`, `test_single_shot_plan_mode_uses_reasoning_model`, and others where `call_log == []` because work was backgrounded.
- [x] 2. Consolidate queue/task/session ownership
  - [x] 2-1. Choose one authoritative queue model. Currently there are two overlapping systems: dispatcher-level queueing (`dispatcher.py` `_project_queues` + `_global_queue`) and background task queueing (`tiered_dispatch.py:656-673` launch path + `697-721` capacity checks). Decide whether dispatcher queueing is the only real queue and task registry only tracks state, or task registry owns the queue lease and dispatcher becomes a thin ingress adapter.
  - [x] 2-2. Resolve the split-brain execution/control models: `Task`/`Project` in `models.py`, `ConciergeTask` in `task_registry.py`, and `Session`/`SessionManager` in `session.py` all carry overlapping authority. Document one authoritative answer for: what the user-facing unit of work is, what the queue serializes on, what the session traces, what gets persisted as truth, and what IDs are canonical in logs/prompts/follow-up resolution.
  - [x] 2-3. Reduce repeated context resolution/materialization so the same turn is not re-resolved by multiple layers without a clear boundary change.
- [x] 3. Narrow `runtime/__init__.py`
  - [x] 3-1. Separate orchestration from fast-command adaptation, reassurance/progress signaling, and schedule-follow-up rewriting. Candidate extraction targets: `runtime/schedule_commands.py` (schedule-follow-up rewriting, schedule-related turn adaptation), `runtime/progress_ux.py` (reassurance/progress signaling, queued-hint delivery), `runtime/dispatch_policy.py` (dispatch-mode selection, extracted from the 3999-line monolith).
  - [x] 3-2. Keep `process()` / `_process_inner()` focused on orchestration, state progression, and high-level dispatcher handoff.
  - [x] 3-3. Target scope after narrowing: `runtime/__init__.py` (currently 3999 lines) should own orchestration and state progression only, shedding at least dispatch-mode policy, schedule-follow-up rewriting, and progress/reassurance signaling responsibilities. The file should lose at least 30% of its current lines.
- [x] 4. Extract schedule-command semantics out of the scheduler daemon boundary
  - [x] 4-1. Move workflow resolution, timezone/default interpretation, and user-facing schedule command normalization closer to the command surface. Note: 48-4 already shipped timezone preference management and `/timezone` / `/tz` commands; this task moves the remaining schedule-command semantics (workflow resolution, trigger parsing, natural-language schedule rewriting) that are still embedded in the daemon file.
  - [x] 4-2. Keep lease/authority, fire-time execution, and daemon concerns grouped together.
- [x] 5. Remove duplicated bridging logic
  - [x] 5-1. Delete embedded schedule special-cases from runtime once the neighboring helper/module owns them.
  - [x] 5-2. Remove duplicate delivery fallback or workflow-resolution glue from the wrong owner.
- [x] 6. Clarify file/module ownership
  - [x] 6-1. Introduce only the minimum new modules needed inside `src/dan/server/concierge/runtime/` or `concierge/` to reflect real boundaries. Candidate names: `runtime/schedule_commands.py`, `runtime/progress_ux.py`, `runtime/dispatch_policy.py`, `concierge/schedule_surface.py`.
  - [x] 6-2. Ensure naming matches actual responsibility.
- [x] 7. Regressions
  - [x] 7-1. Revalidate fast commands, workflow schedule follow-ups, timezone/default behavior, delivery fallback, and fire-time execution.
  - [x] 7-2. Confirm the full `test_tiered_dispatch.py` suite passes with the narrowed dispatch-mode policy — the ack-only failure cluster should be resolved.

## Primary Files

- `src/dan/server/concierge/runtime/__init__.py`
- `src/dan/server/concierge/scheduler.py`
- `src/dan/server/concierge/tier_executors.py` (2146 lines — tiered dispatch execution, closely coupled to the runtime orchestration boundary being narrowed; verify that extracted schedule/progress modules do not create circular imports or break the dispatch handoff contract)
- nearby `src/dan/server/concierge/runtime/` support modules
- new extraction targets (see task 4-1)

## Success Criteria

- normal single-shot ask/agent/plan turns execute in the foreground and produce real answers, not ack-only background responses
- the `test_tiered_dispatch.py` ack-only failure cluster is resolved
- one authoritative queue/lease model is documented and reflected in code — no overlapping queue authority between dispatcher and tiered_dispatch
- `Task`/`ConciergeTask`/`Session` ownership is documented in one state-flow doc with canonical IDs for each layer
- concierge runtime orchestration is materially narrower (at least 30% line reduction from 3999) and no longer the default home for scheduling/control-plane special cases
- scheduler daemon authority is clearly separated from user-facing schedule-command semantics
- duplicated schedule bridging logic is removed rather than mirrored across two files
- duplicate queue/context/prompt ownership in the concierge path is reduced enough that 46-6 can standardize the remaining contract instead of inheriting the current ambiguity

## Decisions

- Keep patch-first extraction inside the existing concierge package; do not create a parallel control-plane framework.
- Scheduling authority and user-facing schedule semantics are related, but they are not the same boundary and should not keep widening the same file.

## Notes

- This plan should leave the concierge package easier to extend without every new fast command or scheduling nuance landing in `runtime/__init__.py`.
- 48-4 work (timezone preferences, `/timezone` and `/tz` commands) is done and not re-scoped here; only the remaining schedule-command semantics are in scope.
- **Scope boundary with 46-6:** This plan (50-5) narrows the *files* — extracts schedule-command and progress-UX modules, fixes dispatch-mode policy, consolidates queue/task/session ownership, reduces duplicate bridging logic. 46-6 then reshapes the concierge prompt pipeline inside those cleaner files — slot-based assembly, stage overlay quality, typed handoff envelope. 50-5 moves code and fixes policy; 46-6 improves what the code produces.
- **Dispatch-policy narrowing is the highest-priority task in this plan.** It unblocks the largest cluster of failing tiered-dispatch tests and directly impacts user-facing behavior (ack-only vs. real responses). Execute task 1 first.
