# 51: Universal Worker Hardening And Standalone Cell

**Status:** not-started
**Goal:** Mature the reusable universal worker into a truly independent, portable, and powerful cell that can later serve as the foundation for multicellular DAN systems.

## Problem

The reusable worker core now exists, but it is still closer to a promising internal primitive than a true standalone cell:

- the normalized execution contract is present, but the worker still lacks a fully hardened stepwise acquisition boundary
- capability selection is still thinner than the long-term “tool list first, details second” model
- memory/evidence handling is still closer to one-shot context fetch than a full lifecycle
- there is no minimal standalone runner that proves the cell can live outside the full DAN runtime
- there is no explicit single-cell acceptance bar that blocks premature multicellular work

If this phase is skipped, DAN risks repeating the usual pattern: interesting orchestration ideas built on top of a half-formed cell, which then forces later organism layers back into oversized prompts and ad hoc runtime glue.

## Scope

In scope:

- progressive context acquisition and continuation hooks
- capability manifests and selective tool-detail loading
- memory/evidence lifecycle work needed for cell-local continuation and recoverability
- a minimal standalone runner that proves independent use
- explicit evals and promotion criteria for “single-cell viability”

Out of scope:

- multicellular composition itself; that belongs to `52-*`
- broad new product surfaces unrelated to cell hardening
- turning every worker into a manager or sub-agent spawner
- a giant second framework parallel to DAN

## Tasks
- [ ] 1. Lock the independent-cell boundary
  - [ ] 1-1. Define the minimum properties every standalone cell must own: identity, instruction, capability surface, memory/evidence inputs, output contract, and governance limits
  - [ ] 1-2. Keep the cell boundary small and explicit so later organism layers are not forced back into prompt spaghetti
- [ ] 2. Land the staged acquisition hardening track
  - [ ] 2-1. Plan and sequence [51-1-progressive-context-acquisition-and-continuation-hooks](51-1-progressive-context-acquisition-and-continuation-hooks.md)
- [ ] 3. Land the capability/tool hardening track
  - [ ] 3-1. Plan and sequence [51-2-capability-manifest-and-selective-tool-loading](51-2-capability-manifest-and-selective-tool-loading.md)
- [ ] 4. Land the memory/evidence hardening track
  - [ ] 4-1. Plan and sequence [51-3-memory-and-evidence-lifecycle](51-3-memory-and-evidence-lifecycle.md)
- [ ] 5. Land the standalone runner track
  - [ ] 5-1. Plan and sequence [51-4-standalone-worker-runner](51-4-standalone-worker-runner.md)
- [ ] 6. Prove single-cell viability
  - [ ] 6-1. Plan and sequence [51-5-single-cell-evals-and-hardening](51-5-single-cell-evals-and-hardening.md)
- [ ] 7. Exit with one clear acceptance bar
  - [ ] 7-1. Confirm the worker can run independently, acquire only necessary context, preserve continuation hooks, respect governance limits, and produce contracted outputs on a small canonical task set

## Dependencies / Sequencing

Recommended order:

```text
50-6 / 50-7 worker-boundary cleanup
  ↓
51-1 progressive context acquisition and continuation hooks
  ├→ 51-2 capability manifests and selective tool loading
  ├→ 51-3 memory and evidence lifecycle
  └→ 51-4 standalone worker runner
         ↓
      51-5 single-cell evals and promotion gate
         ↓
        52-1 and beyond
```

Rationale:

- `51-1` comes first because the “only pick up what is necessary” rule affects every later cell surface
- `51-2` and `51-3` should be informed by the same staged-acquisition model so tools and memory both operate on discovery refs before full detail
- `51-4` should wrap hardened contracts, not invent them ad hoc
- `51-5` is last because it defines the acceptance bar for starting `52-*`

## Success Criteria

- the worker can start from catalog-level context and fetch full detail only when needed
- capability selection is manifest-driven and selective rather than prompt-lore-driven
- memory/evidence handling supports ref-first continuation and selective rehydration
- one minimal standalone runner can exercise the same hardened worker outside the full DAN runtime
- `52-*` has a real gate: no multicellular work starts without an explicit and satisfied single-cell viability bar

## Decisions
- This is a new top-level maturation phase, not an extension of `46-*`. `46` established the primitive; `51` is about making that primitive into a real standalone cell.
- The cell should practice progressive disclosure by default: discover first, select second, expand third, act fourth, and preserve hooks for later continuation.
- Not every cell needs to delegate. Delegation is treated as a specialized behavior that can remain above the baseline single-cell contract.

## Notes
- This plan is the execution counterpart to [docs/universal-worker-strategy-summary.md](../universal-worker-strategy-summary.md).
- `52-*` depends on `51-*`; multicellular composition should not start before the cell boundary is strong enough to stand on its own.
- `46-*` established the Worker primitive, while the remaining `50-*` cleanup still removes some runtime seams around it. `51-*` assumes those lower-level boundary cleanups continue to move in the same direction rather than reintroducing compatibility drift.
