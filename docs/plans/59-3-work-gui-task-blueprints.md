# 59-3: Work GUI Task Blueprints

**Parent:** [59-adaptive-task-blueprints](59-adaptive-task-blueprints.md)
**Status:** completed
**Goal:** Make the Work GUI a clear projection of the canonical Task Blueprint, with task-family-specific summaries and separate expandable execution-attempt detail.

## Tasks

- [x] 1. Normalize canonical blueprint and legacy task-graph payloads into one frontend view model
- [x] 2. Show the protected goal/permissions/acceptance contract without presenting it as a separate workflow layer
- [x] 3. Add task-family projections for direct, debugging, research, design, meeting, and manufacturing work
- [x] 4. Distinguish semantic fulfilment from execution-attempt state
- [x] 5. Keep collapse/progressive disclosure view-only and preserve revision/provenance access
- [x] 6. Add focused component/view-model tests and production build validation

## Decisions

- The GUI owns no blueprint truth; it renders backend snapshots and emits typed user steering/approval events.
- Broad run phases remain telemetry below the semantic blueprint.
- The visual direction extends the existing Factory Worn industrial surface rather than introducing a separate dashboard aesthetic.

## Notes

- Arbitrary generated frontend code is out of scope; task-native views are composed from trusted typed components.
