# 1-5: Beacon Service, Evals, And Cutover

**Parent:** [1-beacon-search-infra](1-beacon-search-infra.md)
**Status:** not-started
**Goal:** Package Beacon as its own subsystem, evaluate it against the legacy path, and decide when a separate repo/service or default cutover is actually justified.

## Tasks
- [ ] 1. Add a search-quality and dependency-reduction eval harness
  - [ ] 1-1. Measure answer quality, citation support, freshness, latency, cost, and external-call rate
  - [ ] 1-2. Run `legacy` vs `hybrid` vs `beacon` comparisons in shadow mode before changing defaults
  - [ ] 1-3. Define explicit cutover gates rather than cutting over on intuition
- [ ] 2. Package Beacon as an internal subsystem first
  - [ ] 2-1. Give Beacon its own config surface, storage root, and observability hooks
  - [ ] 2-2. Keep DAN adapters thin so future extraction is mostly packaging instead of rewrite
  - [ ] 2-3. Preserve direct access to legacy search tools for debugging and fallback
- [ ] 3. Decide on separate repo/service only after maturity gates
  - [ ] 3-1. Require at least one additional consumer beyond DAN or a clear ownership/deployment need
  - [ ] 3-2. Require a stable broker API plus operational budgets and retention policies
  - [ ] 3-3. Define storage, auth, and migration mechanics if extraction is later approved

## Decisions
- A separate repo/service is a later phase gate, not a day-one requirement.
- Default cutover should happen only after Beacon matches or beats legacy quality while reducing external dependency enough to matter.

## Notes
- The user's request here is to name Beacon separately now, not to force premature repo/service separation before the broker contract and evals are mature.
