# 38: Review Hardening

**Status:** not-started
**Goal:** Address all actionable findings from the 2026-03-17 code review and module audit, prioritized by risk.

## Motivation

A repository-wide code review and module audit (`docs/reviews/2026-03-17-code-review.md`, `docs/reviews/2026-03-17-module-audit.md`) identified issues across five areas: Furnace write safety, Furnace lifecycle correctness, startup/config robustness, domain learning fidelity, and provider/tool/doc alignment. This plan tracks fixes in priority order.

## Sub-Plans

| # | Sub-Plan | Scope | Priority | Dependencies |
|---|----------|-------|----------|--------------|
| [38-1](38-1-furnace-write-safety.md) | Furnace Write Safety | `source_id` path traversal, artifact containment, source ID collision | P1 | None |
| [38-2](38-2-furnace-lifecycle.md) | Furnace Lifecycle State Machine | cancel semantics, duplicate start/resume, delete/cancel races, multi-subscriber SSE | P2 | None |
| [38-3](38-3-startup-config-hardening.md) | Startup & Config Hardening | lazy `~/.dan` writes, safe-mode startup, dynamic telemetry DB path | P1/P2 | None |
| [38-4](38-4-domain-learning-fidelity.md) | Domain Learning Fidelity | broader keyword seeds, abbreviation aliases, original label preservation | P2 | None |
| [38-5](38-5-provider-tool-doc-alignment.md) | Provider, Tool & Doc Alignment | Google provider test realignment, clipboard error messaging, README safety contract | P2/P3 | None |

## Dependencies / Sequencing

```
38-1 (Furnace Write Safety)         ← start here, smallest effort, highest risk
38-3 (Startup & Config Hardening)   ← independent, unblocks CI
38-2 (Furnace Lifecycle)            ← independent, larger refactor
38-4 (Domain Learning Fidelity)     ← independent, product quality
38-5 (Provider/Tool/Doc Alignment)  ← independent, regression coverage
```

All sub-plans are independent and can run in any order. Recommended priority: 38-1, 38-3, 38-2, 38-4, 38-5.

## Success Criteria

- [ ] No Furnace artifact writes outside the session directory regardless of `source_id` content
- [ ] Server starts cleanly when `~/.dan` is not writable (features degrade, server doesn't crash)
- [ ] Furnace cancel actually stops the running worker; duplicate starts are prevented
- [ ] Domain learning captures common real-world domains and abbreviations
- [ ] Google provider regression tests pass against current API surface
- [ ] README safety claims match actual file-tool behavior
- [ ] All existing tests continue to pass

## Decisions

- (filled in during execution)

## Notes

- Review documents live in `docs/reviews/`.
- The 85 cascading test errors in the full suite collapse to a single root cause (home-directory writes); fixing 38-3 should eliminate them.
