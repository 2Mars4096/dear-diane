# 1-4: Followup Change Battery

**Parent:** [1-dan-code-live-capability-battery](1-dan-code-live-capability-battery.md)
**Status:** not-started
**Goal:** Add follow-up change requests to each live-test project so DAN Code is evaluated on adaptation, regression control, and codebase continuity rather than one-shot generation only.

## Tasks
- [ ] 1. Define the follow-up pattern for each project
  - [ ] 1-1. Require at least one scoped feature extension
  - [ ] 1-2. Require at least one repair/refactor or behavioral adjustment
- [ ] 2. Add follow-up evaluation criteria
  - [ ] 2-1. Preserve existing behavior and tests where appropriate
  - [ ] 2-2. Keep edits local instead of rebuilding whole modules
  - [ ] 2-3. Update or add regression tests when behavior changes
- [ ] 3. Decide how to score failed-first-pass recovery versus clean-first-pass adaptation
- [ ] 4. Fold the follow-up scenarios back into the parent battery summary

## Decisions
- The live battery should measure whether DAN Code can continue from its own prior work, not just produce an impressive first draft.

## Notes
- A project should only graduate from “initial build” to “strong capability proof” once it passes at least one meaningful follow-up change request without collateral regressions.
