# 48-1: Workflow Identity and Resolution

**Parent:** [48-workflow-continuity-and-control-plane-hardening](48-workflow-continuity-and-control-plane-hardening.md)
**Status:** not-started
**Goal:** Build one authoritative workflow identity resolver that can map user references and current session context onto an exact saved workflow target.

## Dependencies
- None (foundation sub-plan for the 48 family).
- **48-2** will consume the resolver result and revision metadata defined here; task 3-4 (feeding revision markers into context packs) defines the interface but full pack wiring lands in 48-2.
- **48-3** and **48-4** also consume the resolver; their stale-revision handling depends on the identity model shipped here.
- Coordinate with **48-6** early: resolver-shaped acceptance scenarios and baseline timing measurements for identity resolution should be drafted while this sub-plan is in progress.

## Tasks
- [ ] 1. Define the workflow identity model
  - [ ] 1-1. Document the difference between `graph_id`, normalized workflow ID, display name, `_scratch`, current workflow context, and project-linked workflow IDs.
  - [ ] 1-2. Define precedence rules for explicit ID, exact normalized name match, current workflow, project-linked workflow, and fresh inventory lookup.
  - [ ] 1-3. Define revision/fingerprint semantics so follow-up turns can tell whether “the same workflow” still refers to the same saved graph state.
- [ ] 2. Implement a shared resolver surface
  - [ ] 2-1. Add a shared helper that resolves workflow references for run/delete/schedule/edit actions.
  - [ ] 2-2. Return both machine target (`graph_id`) and human-facing resolution metadata so downstream paths can explain what was chosen.
  - [ ] 2-3. Return freshness metadata (`revision`, fingerprint/hash where available, resolution timestamp, and stale-or-fresh status against current inventory).
- [ ] 3. Integrate the resolver into workflow actions
  - [ ] 3-1. Use it in scheduling.
  - [ ] 3-2. Use it in workflow deletion and workflow run follow-ups.
  - [ ] 3-3. Use it in workflow continuation turns that need to bind “current workflow”.
  - [ ] 3-4. Feed revision/freshness markers into follow-up context packs and mutation callers so stale workflow references can fail cleanly instead of mutating the wrong revision.
- [ ] 4. Keep ambiguity honest
  - [ ] 4-1. Define the cases where DAN should refuse to guess and ask for clarification. Concrete examples to cover: duplicate display names within a project, deleted graph referenced by stale thread context, substring vs exact name match, and cross-project name collision when project scope is ambiguous.
  - [ ] 4-2. Prefer exact/fresh inventory results over stale remembered names when the reference is ambiguous.
- [ ] 5. Test the resolver contract
  - [ ] 5-1. Add focused unit tests for exact ID, exact name, normalized name, current workflow, and project-linked fallback.
  - [ ] 5-2. Add regressions for stale revision references and ambiguous references that must not silently target the wrong workflow.

## Decisions
- The system should resolve workflow identity once, then reuse the resolved target downstream.
- Display names are helpful for lookup, but saved `graph_id` remains the execution/scheduling authority.
- `_scratch` and saved workflows should never be conflated silently.
- The resolver result should carry revision/fingerprint state, because continuity bugs can come from stale workflow versions even when `graph_id` resolves correctly.

## Notes
- This sub-plan is the prerequisite for making follow-up workflow turns feel stateful instead of rediscovering the target every time.
- Resolution is always scoped to the current project/session context first; cross-project global lookup is out of scope for this phase.
- Primary codebase surfaces: graph store/persistence layer, `graphs.py` router, chat/session ID fields in `ChatMessageRequest`, adapter-level workflow context (`telegram_fleet.py`, CLI).
