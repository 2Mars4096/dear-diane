# 2-5: Performance And Regression Coverage

**Parent:** [2-frontend-hardening](2-frontend-hardening.md)
**Status:** not-started
**Goal:** Add durable guardrails for frontend startup cost, bundle size, and regression coverage so the functional hardening work remains fast and stable over time.

## Context

The review/build snapshot showed two related problems:

1. **Large frontend payloads.** The current production build reports oversized chunks, especially around Monaco/editor, terminal, PDF, and mode-shell bundles.
2. **Coverage is thin where complexity is highest.** Shared chat/sidebar tests exist, but mode-shell tests for Development and Research behavior are limited relative to the amount of logic those surfaces own.

This subplan follows the functional hardening work because the performance and test strategy should reflect the cleaned-up shell boundaries rather than the current monoliths.

## UI Brief

- **Surface:** shared shell performance and frontend safety net
- **Audience:** every DAN user, especially those switching between heavy modes
- **Primary action:** enter a mode quickly and trust that the most fragile journeys stay covered by tests
- **Dominant concept:** fast shell, deliberate depth — heavy tools load when needed, not all at once
- **Constraints:** do not trade away clarity or maintainability for micro-optimizations
- **Anti-goals:** splitting everything into arbitrary chunks, helper-only tests that miss real UI risk, or performance work with no user-visible effect

## No-Change Zones

- Keep lazy loading aligned with mode boundaries and real heavy surfaces instead of chasing tiny bundle wins everywhere.
- Keep the test strategy user-journey oriented; reducer-only coverage is not enough for the shell behaviors this plan is meant to protect.

## Tasks

- [ ] 1. Establish a frontend hardening baseline
  - [ ] 1-1. Capture current bundle/chunk sizes and identify the highest-cost lazy boundaries
  - [ ] 1-2. Record current mode-shell test coverage gaps and the most fragile user journeys
  - [ ] 1-3. Define explicit budgets or thresholds after the baseline so later improvements are measurable
- [ ] 2. Reduce avoidable payload and hidden-mode cost
  - [ ] 2-1. Add additional lazy boundaries or manual chunking around Monaco, terminal, PDF, and heavy shared surfaces
  - [ ] 2-2. Ensure hidden modes do not keep paying active-work costs after the isolation work lands
  - [ ] 2-3. Revisit Vite build configuration so oversized chunks are intentional rather than accidental
- [ ] 3. Expand regression coverage where the frontend is most fragile
  - [ ] 3-1. Add component/integration coverage for Development mode, Research mode, and compact/full chat handoff behavior
  - [ ] 3-2. Add explicit coverage for shortcut routing, panel toggles, browser gating, and history restore
  - [ ] 3-3. Fix stale validation guidance so documented test commands match Vitest's actual CLI
- [ ] 4. Verify the new guardrails
  - [ ] 4-1. Keep `cd editor && npm test` green with the new coverage
  - [ ] 4-2. Keep `cd editor && npm run build` green with reduced or explicitly accepted chunk warnings

## User-Facing Acceptance

- Mode entry feels lighter because the app defers heavy tools until they are actually needed.
- The highest-risk shell journeys have a named regression test path instead of relying on manual memory.
- Build/test commands and warnings become actionable rather than noisy trivia.

## Decisions

- Prioritize performance work that improves actual mode startup/use cost before micro-optimizing cosmetic interactions.
- Treat test coverage as part of hardening, not as a separate optional cleanup pass.

## Notes

- Residual chunk warnings may remain after this pass, but they should be understood, documented, and justified rather than surprising.
