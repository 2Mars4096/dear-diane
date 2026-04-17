# 48: Workflow Continuity and Control-Plane Hardening

**Status:** completed
**Goal:** Make DAN feel sharper and more reliable on build → edit → rerun → schedule workflows by fixing workflow identity continuity, reducing concierge ambiguity, hardening mutation transactions, improving scheduling semantics, and cutting avoidable non-LLM latency.

## Problem

The current product gap is not primarily raw model capability. It is continuity and control-plane quality:

- DAN still loses track of which exact workflow the user means across build/edit/run/schedule/delete turns.
- workflow follow-ups still route through a generic concierge path too often, which adds ambiguity and unnecessary tool/routing latency.
- mutation previews are stronger than before, but partial edits can still invalidate existing required connections or fail in ways that feel arbitrary to users.
- scheduling still behaves like an internal command surface instead of a first-class workflow-continuation surface, especially around workflow binding and timezones.
- runtime lint is valuable, but generated workflows should not feel blocked by heuristic checks before the user has even reached a first clean run.
- several non-LLM stages (routing, context assembly, inventory lookup, mutation compilation, dry-run prep) still add latency that should be reduced or parallelized.

## Scope

In scope:

- authoritative workflow identity resolution across saved workflow IDs, display names, current workflow context, and project-linked workflow context
- revision/fingerprint propagation so follow-up turns can detect stale workflow references instead of assuming the same `graph_id` means the same workflow state
- a dedicated workflow follow-up lane for build/edit/run/rerun/schedule/delete with a compact workflow context pack
- conservative deterministic mutation self-repair and better transaction safety for partial workflow edits
- scheduling that binds to resolved workflow identity and treats timezone as a first-class user-facing concept
- a first-run policy for generated workflows that keeps structural safety strict while softening heuristic gates where appropriate
- latency measurement and reduction for non-provider stages of the workflow control plane
- acceptance and regression harnesses for realistic workflow-continuation sequences
- all chat surfaces that can trigger workflow actions (editor chat, Telegram adapter, CLI) must behave consistently through the shared resolver and follow-up lane; surface-specific quirks (Telegram queue hints, editor selection context) remain adapter concerns but must feed the same identity/continuation contracts

Out of scope:

- changing the underlying LLM provider latency or model capability
- replacing the current Worker/linter foundations; this plan builds on them
- turning the meta-workflow builder idea into the default product path
- full workflow undo/history UX beyond detecting stale revisions and refusing unsafe continuation
- broad editor-shell redesign unrelated to workflow continuity

## Sub-Plans

| # | Sub-Plan | Scope | Priority | Status |
|---|----------|-------|----------|--------|
| [48-1](48-1-workflow-identity-and-resolution.md) | Workflow Identity and Resolution | One authoritative resolver for workflow identity across chat, run, schedule, delete, and project context | P1 | completed |
| [48-2](48-2-workflow-followup-lane-and-context-pack.md) | Workflow Follow-up Lane and Context Pack | Dedicated workflow continuation routing plus compact workflow state injection and fast-path reductions | P1 | completed |
| [48-3](48-3-mutation-transaction-safety.md) | Mutation Transaction Safety | Conservative repair and failure semantics for partial graph edits | P1 | completed |
| [48-4](48-4-scheduling-workflow-binding-and-timezones.md) | Scheduling Workflow Binding and Timezones | Schedule against resolved workflow identity and support user-facing timezone semantics | P1 | completed |
| [48-5](48-5-generated-workflow-first-run-policy.md) | Generated Workflow First-Run Policy | Keep structural safety strict while softening heuristic first-run friction for generated workflows | P2 | completed |
| [48-6](48-6-latency-reduction-and-acceptance-harness.md) | Latency Reduction and Acceptance Harness | Measure and reduce non-LLM latency while locking realistic workflow-continuation regressions | P1 | completed |

## Dependencies / Sequencing

Recommended execution order:

```text
48-1 (Workflow Identity and Resolution)
  └→ draft 48-6 baseline measurements and acceptance scenarios immediately
  ↓
48-2 (Workflow Follow-up Lane and Context Pack)
  ├→ 48-3 (Mutation Transaction Safety)
  ├→ 48-4 (Scheduling Workflow Binding and Timezones)
  └→ 48-5 (Generated Workflow First-Run Policy)
       ↓
48-6 (Latency Reduction and Acceptance Harness)
```

Rationale:

- identity resolution is the foundation; run/schedule/delete continuity cannot be reliable without it
- once workflow identity is stable, the concierge can expose a narrower workflow follow-up lane and smaller context packs
- mutation safety, scheduling semantics, and first-run policy all depend on that clearer workflow state model; **48-3** and **48-4** in particular consume the resolver result and revision/fingerprint contracts defined in **48-1**, so they cannot ship their stale-revision paths until the identity model exists
- latency baselines and acceptance-sequence design should start early, but the final optimization pass and rollout gates should measure the improved workflow-continuation path, not the old ambiguous one

## Success Criteria

- DAN can resolve the user’s intended workflow consistently across build/edit/run/rerun/schedule/delete turns without requiring repeated explicit IDs
- downstream workflow actions can detect when the referenced workflow changed since the last turn and either refresh safely or ask for clarification instead of acting on stale state
- workflow follow-up turns use a dedicated, lower-ambiguity path instead of generic concierge routing whenever the user is clearly continuing work on a workflow
- mutation previews no longer fail on obvious partial-edit traps like accidentally disconnecting required inputs or losing stable references
- scheduling can target the current or named workflow reliably and represent timezone explicitly in both commands and stored schedules
- first generated runs are blocked only by hard structural issues, not by heuristic gates that should be warnings or canaries
- a realistic acceptance battery covers build → edit → rerun → schedule sequences and records both correctness and non-LLM latency
- measurable non-provider latency is lower after this plan than before, measured against explicit p50/p95 baselines captured at the start of 48-6 for each DAN-owned stage (routing, identity resolution, context assembly, mutation compilation, dry-run, dispatch)

## Decisions

- This phase prioritizes workflow continuity and control-plane quality over new model tricks.
- The model should not be asked to rediscover workflow identity every turn; that is system work.
- Structural validation remains strict; heuristic guidance should be introduced conservatively where it helps users recover.
- Speed work in this phase focuses on DAN-owned stages only: routing, context assembly, mutation compilation, lookup, and scheduling prep.
- Revision/fingerprint awareness is part of workflow identity; continuity should be explicit about which saved workflow and which revision are being continued.
- The implementation stayed patch-first: existing scheduler, concierge, capability, and mutation-preview paths were extended rather than replaced with a parallel control plane.

## Notes

- This plan is the practical continuation of the user-facing gaps surfaced after plans 46/47: the foundations are stronger, but workflow continuity still feels more brittle than it should.
- The best comparison point is not “is the model smart?” but “does DAN preserve the right workflow object, context, and action lane across turns?”
- The meta-builder eval harness under plan 45 can remain useful as a benchmark, but it is not the center of this phase.
- The old standalone chat-history housekeeping plan that previously used the `48` prefix was already folded into the conversation-lifecycle plan [12-5](12-5-conversation-lifecycle.md), so this `48` family is intentionally focused on workflow continuity and speed.
- Completed implementation summary: shared workflow resolver/context pack, workflow-lane stage timing, structured mutation failure attribution, timezone-aware scheduling with `/timezone`, first-run heuristic softening under `DAN_FIRST_RUN_POLICY`, and an eval baseline for workflow follow-up latency.
