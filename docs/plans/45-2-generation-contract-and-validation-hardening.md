# 45-2: Generation Contract and Validation Hardening

**Parent:** [45-workflow-generation-hardening](45-workflow-generation-hardening.md)
**Status:** not-started
**Goal:** Strengthen the generation contract so ambiguous or weak graphs are rejected earlier, with clearer failure buckets and less reliance on advisory heuristics.

## Problem

The current system already validates a lot, but some weak spots remain:

- legacy advisory gates can still look authoritative even when they are only pass-through shims
- schedule and workflow semantics are still inferred too loosely in places
- semantic invalidity is sometimes discovered later than it should be
- failure attribution can still blur together extraction, structure, and run-readiness problems

## Tasks

- [ ] 1. Tighten the top-level contract boundaries
  - [ ] 1-1. Audit which generation gates are authoritative versus advisory, and stop reporting advisory signals as hard readiness.
  - [ ] 1-2. Replace or demote legacy pass-through checks where they contaminate routing or telemetry.
  - [ ] 1-3. Make the direct surface and concierge surface consume the same acceptance contract.
- [ ] 2. Harden semantic validation
  - [ ] 2-1. Add explicit schedule-intent validation so recurring execution metadata requires stronger evidence than a loose keyword hit.
  - [ ] 2-2. Add stricter grounding checks for nodes that claim external fetch/read/write behavior.
  - [ ] 2-3. Add stronger merge/fan-in/topology checks for graphs that need reducer or synthesis semantics.
  - [ ] 2-4. Clarify when port alias repair is allowed versus when generation should be rejected and retried.
- [ ] 3. Improve staged failure attribution
  - [ ] 3-1. Keep extraction/spec/node/section/boundary/whole-graph/acceptance failures separate in reporting.
  - [ ] 3-2. Ensure direct-surface responses and chat/runtime events surface the same failure bucket names.
- [ ] 4. Expand the negative test set
  - [ ] 4-1. Add explicit negative fixtures for false-positive schedules.
  - [ ] 4-2. Add fixtures for weakly grounded external-action nodes.
  - [ ] 4-3. Add fixtures for invalid fan-in and ambiguous boundary joins.

## Decisions

- Prefer mechanical and semantic validators over prompt advice whenever the rule can be expressed deterministically.
- Ambiguous schedule semantics should be rejected or left unset, not guessed aggressively.
- Validation should be shared across direct, structured, and experimental generation lanes.

## Notes

- This sub-plan is intentionally about contract truthfulness, not about adding more retries.
- It is the right place to clean up any remaining misleading readiness/coverage signals before more experiments are added.
