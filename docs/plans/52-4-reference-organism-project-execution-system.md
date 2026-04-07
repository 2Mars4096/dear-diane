# 52-4: Reference Organism Project Execution System

**Parent:** [52-multicellular-composition](52-multicellular-composition.md)
**Status:** not-started
**Goal:** Assemble one bounded end-to-end organism that proves the hardened cell and the tissue/organ layers can work together coherently on a real user task.

## Tasks
- [ ] 1. Choose the first organism shape
  - [ ] 1-1. One concierge/lead brain
  - [ ] 1-2. One retrieval/research organ
  - [ ] 1-3. One coding organ
  - [ ] 1-4. One validation organ
  - [ ] 1-5. One synthesis/reporting organ
- [ ] 2. Choose the first bounded task class
  - [ ] 2-1. Prefer a project-execution task that is rich enough to require coordination but narrow enough to validate well
  - [ ] 2-2. Favor a code-change-style task with explicit retrieval, execution, validation, and final reporting rather than a vague “general assistant” target
- [ ] 3. Define organism-level orchestration rules
  - [ ] 3-1. Decomposition
  - [ ] 3-2. Routing
  - [ ] 3-3. Escalation
  - [ ] 3-4. Final synthesis
- [ ] 4. Add organism-level observability
  - [ ] 4-1. Make cross-organ handoffs and final accountability inspectable
- [ ] 5. Prove the “biology stack” end to end
  - [ ] 5-1. Show cells -> tissues -> organs -> organism on one real workflow instead of only in design notes

## Decisions
- The first organism should be bounded and inspectable, not maximally broad.
- The reference organism exists to prove the architecture, not to be the final product surface.

## Notes
- This is the proof that the cell work was worth doing.
- The first organism should look more like a bounded project-execution system than a general-purpose omniscient swarm.
