# 1-5: Beacon Service, Evals, And Cutover

**Parent:** [1-beacon-search-infra](1-beacon-search-infra.md)
**Status:** completed
**Goal:** Package Beacon as its own subsystem, evaluate it against the legacy path, and decide when a separate repo/service or default cutover is actually justified.

## Tasks
- [x] 1. Add a search-quality and dependency-reduction eval harness
  - [x] 1-1. Measure answer quality, citation support, freshness, latency, cost, and external-call rate
  - [x] 1-2. Run `legacy` vs `hybrid` vs `beacon` comparisons in shadow mode before changing defaults
  - [x] 1-3. Define explicit cutover gates rather than cutting over on intuition
- [x] 2. Package Beacon as an internal subsystem first
  - [x] 2-1. Give Beacon its own config surface, storage root, and observability hooks
  - [x] 2-2. Keep DAN adapters thin so future extraction is mostly packaging instead of rewrite
  - [x] 2-3. Preserve direct access to legacy search tools for debugging and fallback
- [x] 3. Decide on separate repo/service only after maturity gates
  - [x] 3-1. Require at least one additional consumer beyond DAN or a clear ownership/deployment need
  - [x] 3-2. Require a stable broker API plus operational budgets and retention policies
  - [x] 3-3. Define storage, auth, and migration mechanics if extraction is later approved

## Decisions
- A separate repo/service is a later phase gate, not a day-one requirement.
- Default cutover should happen only after Beacon matches or beats legacy quality while reducing external dependency enough to matter.

## Notes
- The user's request here is to name Beacon separately now, not to force premature repo/service separation before the broker contract and evals are mature.
- 2026-04-17 live validation note:
  - a real `FLY` equity-research prompt run through DAN's own `handle_web_search(...)` surface still returned `provider: tavily` for broad-web discovery
  - treat this plan as the completion of Beacon incubation and cutover mechanics, not as proof that provider-backed discovery is no longer needed
  - the remaining work is tracked explicitly in [2-beacon-provider-independent-discovery](2-beacon-provider-independent-discovery.md)
- 2026-04-17 implementation slice:
  - added `tests/eval/beacon_search_eval.py` plus regression coverage for per-mode scoring, summary rollups, and explicit cutover-gate evaluation
  - `DAN_BEACON_STORAGE_ROOT`, `DAN_BEACON_SEARCH_DB`, chunking/TTL knobs, and broker shadow-mode config now give Beacon its own internal config surface without changing the public tool IDs
  - `hybrid` mode can now run Beacon primary plus legacy shadow execution when `DAN_SEARCH_BROKER_SHADOW_LEGACY=1`, and callers still retain direct `legacy` mode plus the raw `web_search` / `web_fetch` surfaces for debugging and fallback
  - extraction into a separate repo/service remains a future packaging decision; the current maturity gate is now explicit in the eval harness and plan docs rather than implicit
