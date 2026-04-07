# 52-1: Cell Signaling And Handoff Contracts

**Parent:** [52-multicellular-composition](52-multicellular-composition.md)
**Status:** not-started
**Goal:** Define the direct handoff and broadcast/event contracts that let independent cells coordinate without sharing one giant implicit prompt.

## Tasks
- [ ] 1. Define direct handoff packets
  - [ ] 1-1. Include task, evidence refs, output contract, budget/authority limits, and continuation hooks
  - [ ] 1-2. Keep the handoff shape compact enough that cells can talk through refs and summaries before expanding detail
- [ ] 2. Define broadcast/event signals
  - [ ] 2-1. Status
  - [ ] 2-2. Warning/failure
  - [ ] 2-3. Budget pressure
  - [ ] 2-4. Completion/escalation
- [ ] 3. Separate cell-local and organism-level concerns
  - [ ] 3-1. Keep point-to-point task handoff distinct from broader supervisory/event signals
- [ ] 4. Add observability and traceability
  - [ ] 4-1. Make every cross-cell handoff inspectable after the fact
- [ ] 5. Validate on a small two- or three-cell coordination slice
  - [ ] 5-1. Prove that cells can coordinate through contracts instead of hidden shared prompt state

## Decisions
- Cell communication should be explicit, typed, and inspectable.
- Handoffs should prefer refs and compact packets over wholesale prompt dumping.

## Notes
- This is the first true multicellular boundary. If it is weak, everything above it will be mush.
