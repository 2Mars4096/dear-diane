# 54-3: Legacy Concierge Cutover And Surface Migration

**Parent:** [54-dan-control-plane-rewrite-around-universal-agents](54-dan-control-plane-rewrite-around-universal-agents.md)
**Status:** not-started
**Goal:** Define the coexistence, cutover, and retirement path for legacy concierge-first DAN routing as top-level surfaces migrate onto durable universal-agent controllers.

## Tasks

- [ ] 1. Inventory the current top-level DAN control seams that still depend on concierge / dispatcher / chat-manager routing
  - [ ] 1-1. Gateway entry path
  - [ ] 1-2. CLI / chat entry paths
  - [ ] 1-3. Surface-specific fallback or adapter-owned routing
- [ ] 2. Define migration phases by surface
  - [ ] 2-1. New DAN-v2 route runs in shadow or opt-in mode first
  - [ ] 2-2. Promote low-risk surfaces before high-risk general DAN surfaces
  - [ ] 2-3. Keep clear fallback rules when DAN-v2 cannot yet handle a route
- [ ] 3. Define the cutover criteria
  - [ ] 3-1. Benchmark parity or better on frozen routing scenarios
  - [ ] 3-2. No regression on specialist delegation to `dan code` / `dan research`
  - [ ] 3-3. Honest observability and rollback path
- [ ] 4. Define what remains concierge-owned versus what is retired
  - [ ] 4-1. Remove duplicated routing logic once DAN-v2 owns it
  - [ ] 4-2. Retain only the parts that still serve as reusable infrastructure rather than top-level brain logic

## Decisions

- Concierge should be strangled from the top-level “brain” role first, not ripped out of the repo before replacements are proven.
- Surface migration should be benchmark-gated, not faith-based.
- DAN-v2 should own routing and delegation; legacy concierge paths should become fallback or infrastructure only.

## Notes

- This subplan exists to keep the rewrite disciplined. Without an explicit coexistence/cutover plan, the repo will accumulate two partial brains instead of replacing one with another.
