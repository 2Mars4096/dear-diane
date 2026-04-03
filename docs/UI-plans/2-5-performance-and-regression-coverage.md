# 2-5: Performance And Regression Coverage

**Parent:** [2-frontend-hardening](2-frontend-hardening.md)
**Status:** completed
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

- [x] 1. Establish a frontend hardening baseline
  - [x] 1-1. Capture current bundle/chunk sizes and identify the highest-cost lazy boundaries
  - [x] 1-2. Record current mode-shell test coverage gaps and the most fragile user journeys
  - [x] 1-3. Define explicit budgets or thresholds after the baseline so later improvements are measurable
- [x] 2. Reduce avoidable payload and hidden-mode cost
  - [x] 2-1. Add additional lazy boundaries or manual chunking around Monaco, terminal, PDF, and heavy shared surfaces
  - [x] 2-2. Ensure hidden modes do not keep paying active-work costs after the isolation work lands
  - [x] 2-3. Revisit Vite build configuration so oversized chunks are intentional rather than accidental
- [x] 3. Expand regression coverage where the frontend is most fragile
  - [x] 3-1. Add component/integration coverage for Development mode, Research mode, and compact/full chat handoff behavior
  - [x] 3-2. Add explicit coverage for shortcut routing, panel toggles, browser gating, and history restore
  - [x] 3-3. Fix stale validation guidance so documented test commands match Vitest's actual CLI
  - [x] 3-4. Add focused regressions for extracted Development and Research shell helpers so future shell breakup can stay safe
- [x] 4. Verify the new guardrails
  - [x] 4-1. Keep `cd editor && npm test` green with the new coverage
  - [x] 4-2. Keep `cd editor && npm run build` green with reduced or explicitly accepted chunk warnings

## User-Facing Acceptance

- Mode entry feels lighter because the app defers heavy tools until they are actually needed.
- The highest-risk shell journeys have a named regression test path instead of relying on manual memory.
- Build/test commands and warnings become actionable rather than noisy trivia.

## Decisions

- Prioritize performance work that improves actual mode startup/use cost before micro-optimizing cosmetic interactions.
- Treat test coverage as part of hardening, not as a separate optional cleanup pass.

## Notes

- Residual chunk warnings may remain after this pass, but they should be understood, documented, and justified rather than surprising.
- Landed 2026-04-02: `editor/vite.config.ts` now assigns manual chunks for Monaco, terminal, PDF, KaTeX, graph, content, and shared vendor dependencies. The old shared `research` vendor blob is gone; Research mode now loads PDF and math tooling independently instead of dragging both at once.
- Regression coverage now includes `editor/src/hooks/__tests__/useModeScopedWindowEvent.test.ts`, and the current validation commands are `cd editor && npm test` for the full suite plus `cd editor && npm test -- <path>` for focused Vitest runs.
- Follow-up hardening landed 2026-04-02: `CodeMode.tsx` now lazy-loads more non-core Development surfaces, `useDebugEvents.ts` decouples the active debug listener from the heavy debug UI module, the old DebugPanel static/dynamic import conflict is resolved, and focused mode-shell tests now cover `DevelopmentModeShell`, `ResearchModeShell`, `useResearchModeShortcuts`, and `researchModeChatContext`. Current validation is `cd editor && npm test` (`45` files / `216` tests) plus `cd editor && npm run build`; residual warnings are now mostly the still-heavy `monaco` and `research` bundles.
- Additional Research hardening landed 2026-04-02: `useResearchFurnaceSessions.ts` now owns session polling and SSE reconnect logic, and `useResearchFurnaceSessions.test.ts` adds explicit regression coverage for backend summary sync plus reconnect retry behavior. Current validation is `cd editor && npm test` (`46` files / `218` tests) plus `cd editor && npm run build`; the remaining warnings are still the Node `20.17.0` version warning and the heavy `monaco` / `research` chunks.
- Additional compact-chat hardening landed 2026-04-02: `useModeChatSidebarNativeActions.ts` now covers Development-only shell-command handoff and reviewable file-edit capture, `useModeChatSidebarTransport.ts` now owns the sidecar send/stream runtime, and `ModeChatSidebar.test.ts` now verifies Development-only shell run controls, compact-to-full-chat handoff, workflow-scoped restore, and workspace-scoped restore.
- Additional Research lifecycle coverage also landed 2026-04-02: `researchFurnaceSessionStatus.ts` centralizes Furnace session-state labels/tone, `researchFurnaceSessionStatus.test.ts` locks live/reconnecting/failed presentation semantics, and `ResearchFurnaceSessionCards.test.ts` now covers the extracted Furnace family/card surface directly.
- Additional performance/test hardening landed on 2026-04-02: `ResearchFurnacePanel.tsx` is now lazy-loaded out of `ResearchMode.tsx`, `useResearchFurnaceSessions.ts` now has an explicit `enabled` gate so hidden/non-active Research shells do not keep paying session-sync cost, and focused coverage now includes `ResearchFurnacePanel.test.ts` plus the disabled-controller path in `useResearchFurnaceSessions.test.ts`. Current validation is `cd editor && npm test` (`50` files / `231` tests) plus `cd editor && npm run build`; the remaining warnings are still the Node `20.17.0` version warning and the heavy `monaco` / `research` bundles.
- Explicit bundle thresholds also landed on 2026-04-02: `editor/scripts/check-bundle-budgets.mjs` now enforces size ceilings for the main heavy chunks (`monaco`, `pdf`, `katex`, `ResearchMode`, `ResearchFurnacePanel`, and `CodeMode`), and `editor/package.json` now exposes `npm run bundle:check` plus `npm run build:verify` so bundle drift becomes measurable instead of living only in Vite warnings.
- Review follow-up landed on 2026-04-03: the new ConfigPanel/edge-lint tests are now TypeScript-correct so `npm run build` / `npm run build:verify` stay green instead of relying on Vitest alone, `DebugConsole.tsx` is split out of the lazy `DebugPanel` bundle, and `check-bundle-budgets.mjs` now warns rather than fails when a named chunk disappears while still hard-failing real over-budget regressions.
- Final validation for this plan: `cd editor && npm test` (`50` files / `231` tests), `cd editor && npm run build`, and `cd editor && npm run bundle:check` all pass. The remaining build warning is now effectively the intentionally heavy Monaco/tool-worker path plus the existing Node `20.17.0` version mismatch, so this plan's guardrail goal is complete.
