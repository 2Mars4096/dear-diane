# 52: Multicellular Composition

**Status:** not-started
**Goal:** Build the first trustworthy multicellular layer on top of the hardened universal worker by introducing signaling, tissues, organs, and one bounded reference organism.

## Problem

Once `51-*` produces a strong cell, DAN still needs an explicit way to go from “one viable cell” to “many cooperating cells” without falling into a giant shared prompt or a loose bag of subagents.

The main risk is architectural collapse at the first multicellular step:

- cells communicate through implicit shared context instead of explicit handoffs
- worker pools are treated as ad hoc parallelism instead of reusable tissue patterns
- bounded functional modules are skipped, so every larger composition becomes an undifferentiated manager/worker blob
- the first “organism” arrives before signaling and organ boundaries are strong enough to support it

`52-*` exists to keep the biology stack explicit: cells first, then tissues, then organs, then one reference organism.

## Scope

In scope:

- direct handoff and broadcast/event signaling contracts
- reusable tissue-level coordination patterns
- bounded organ-level modules with strict interfaces
- one reference organism that proves the architecture end to end

Out of scope:

- a giant general-purpose swarm framework
- uncontrolled self-spawning across the whole system
- replacing concierge with many competing top-level brains
- trying to solve every future multi-agent use case in the first organism

## Tasks
- [ ] 1. Define the multicellular sequencing rule
  - [ ] 1-1. Keep `52-*` blocked on the acceptance bar from [51-5-single-cell-evals-and-hardening](51-5-single-cell-evals-and-hardening.md)
- [ ] 2. Land the signaling layer
  - [ ] 2-1. Plan and sequence [52-1-cell-signaling-and-handoff-contracts](52-1-cell-signaling-and-handoff-contracts.md)
- [ ] 3. Land tissue-level coordination patterns
  - [ ] 3-1. Plan and sequence [52-2-tissue-patterns-worker-pools-and-quorum](52-2-tissue-patterns-worker-pools-and-quorum.md)
- [ ] 4. Land organ-level bounded modules
  - [ ] 4-1. Plan and sequence [52-3-organ-patterns-research-coding-validation-synthesis](52-3-organ-patterns-research-coding-validation-synthesis.md)
- [ ] 5. Assemble and prove one reference organism
  - [ ] 5-1. Plan and sequence [52-4-reference-organism-project-execution-system](52-4-reference-organism-project-execution-system.md)
- [ ] 6. Exit with one coherent multicellular story
  - [ ] 6-1. Confirm that the same cell contract can be reused across tissues, organs, and the reference organism without collapsing back into ad hoc orchestration

## Dependencies / Sequencing

Recommended order:

```text
51-5 single-cell acceptance bar
  ↓
52-1 cell signaling and handoff contracts
  ↓
52-2 tissue patterns
  ↓
52-3 organ patterns
  ↓
52-4 reference organism
```

Rationale:

- explicit cell signaling must come first or later layers will communicate through hidden shared prompt state
- tissues come before organs because same-type cooperation is the smallest reusable multicellular unit
- organs come before the reference organism because the first full organism should compose bounded modules, not invent them on the fly

## Success Criteria

- cross-cell communication is typed, inspectable, and compact enough to work through refs and bounded packets
- at least one tissue pattern is reusable without changing the underlying cell contract
- at least one organ exposes a strict input/output interface that hides internal cell chatter
- the first organism completes one bounded real task class with explicit decomposition, routing, validation, and synthesis
- the full stack remains intelligible as cells -> tissues -> organs -> organism rather than collapsing back into one oversized orchestration surface

## Decisions
- `52-*` is not “build a giant swarm framework.” It is a controlled multicellular layer on top of a hardened cell.
- Most cells remain workers. Management and coordination should emerge at tissue/organ/organism levels rather than being baked into every cell.

## Notes
- This plan is the execution counterpart to the “cells -> tissues -> organs -> organism” framing in [docs/universal-worker-strategy-summary.md](../universal-worker-strategy-summary.md).
- `52-*` should stay blocked until `51-5` makes the single-cell viability bar explicit and green.
