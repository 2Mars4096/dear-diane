# 2-4: Research Mode UX Hardening

**Parent:** [2-frontend-hardening](2-frontend-hardening.md)
**Status:** in-progress
**Goal:** Make Research mode structurally clearer, less redundant, and easier to trust by simplifying navigation, reducing cross-mode coupling, and extracting its large inline orchestration logic into maintainable pieces.

## Context

Research mode is real and functional, especially around Furnace sessions and SSE-backed updates, but the shell has accumulated multiple concerns inside one large component:

- layout and panel composition
- shortcut wiring
- session listing / session creation / resume / variant logic
- SSE connection ownership
- context drawer and terminal toggles
- duplicated chat entry points
- mixed concerns between authoring, training, and session administration

This is a UX and maintainability issue more than a pure feature-gap issue.

## UI Brief

- **Surface:** Research workspace for reading, writing, reviewing, and running long-lived training sessions
- **Audience:** researchers who want the paper/document to stay central while tools remain close at hand
- **Primary action:** stay anchored on the active artifact while training/session state remains legible but secondary
- **Dominant concept:** scholar's desk — one dominant working surface, a quiet reference rail, and a clearly separate long-running learning lane
- **Constraints:** preserve the Desk/Library/Plan/Training model from the UI spec and keep Furnace meaningful
- **Anti-goals:** generic IDE chrome, duplicated toggles, or session administration dominating the writing/reading experience

## Visual System

- The active paper/document pane should remain the visual center of gravity.
- Reading/writing tools should use calmer neutral surfaces; long-running training states can use warmer accent cues without repainting the whole mode.
- Chat and context affordances should be obvious but singular; repeated toggles for the same panel should be treated as clutter unless they earn their place.

## No-Change Zones

- Keep Research mode distinct from Development mode; do not solve clutter by turning Research into a generic IDE layout.
- Keep Furnace and long-running training visible as a first-class research capability rather than hiding it in Settings-like administration UI.

## Tasks

- [x] 1. Audit the current surface for redundancy and cognitive load
  - [x] 1-1. Reassess duplicated chat toggles and decide on a single primary chat affordance
  - [x] 1-2. Reassess terminal visibility inside Research mode and its coupling to the code store
  - [x] 1-3. Identify low-value controls in the left rail and top bar that can be consolidated or removed
- [ ] 2. Split the shell into durable sub-surfaces
  - [x] 2-1. Extract `FunctionRail`, `PrimaryPanel`, `ContextPanel`, and `PipelineProgress` into separate modules/hooks
  - [x] 2-2. Extract Furnace session orchestration and SSE ownership out of the top-level mode component
  - [x] 2-3. Keep layout code focused on layout, not session business logic
  - [x] 2-4. Extract shortcut/status/chat-context support out of the top-level mode component so the later panel breakup starts from a thinner shell
- [ ] 3. Tighten research-specific UX truthfulness
  - [x] 3-1. Clarify session lifecycle states: starting, running, paused, reconnecting, failed, completed
  - [x] 3-2. Make document-writing vs training/session-management actions visually distinct
  - [x] 3-3. Ensure long-running session state survives surface switching without hidden surprises
  - [x] 3-4. Tighten empty/first-use states so users can tell where to write, where to find papers, and where to manage training without guesswork
- [ ] 4. Verify Research mode flows
  - [x] 4-1. Add focused tests for panel toggles, session-state rendering, and SSE reconnect behavior
  - [ ] 4-2. Manually smoke-test create/resume/pause/cancel/delete/recipe flows plus context-pane authoring

## User-Facing Acceptance

- A new user can answer three questions immediately: where do I write, where are my papers, and where do I monitor training.
- Session status is readable at a glance without overwhelming the authoring surface.
- Research mode feels calmer and more deliberate after the pass, not just more "modular."

## Decisions

- Keep Research mode as a purpose-built surface rather than collapsing it into Development mode with different tabs.
- Favor fewer, clearer entry points over duplicated controls that toggle the same panel from multiple places.

## Notes

- This plan should not dilute the mode into generic IDE chrome. The goal is to make Research mode more intentional, not more like Code mode.
- Landed 2026-04-02: Research removed the duplicate top-bar chat button, now uses a calmer desk-status strip, and shares the same active-mode listener contract as other shells so hidden Research mode no longer reacts once the user switches away.
- Support-layer extraction also landed on 2026-04-02: `ResearchStatusStrip.tsx`, `useResearchModeShortcuts.ts`, `useResearchAutoShowFurnace.ts`, and `researchModeChatContext.ts` now hold the mode-level status, shortcut, auto-rail, and sidecar-context seams that were previously embedded in `ResearchMode.tsx`. Focused regressions now cover Research shortcuts and the extracted chat-context builder, while the larger FunctionRail / panel / Furnace-session breakup remains open.
- Shell-panel extraction also landed on 2026-04-02: `ResearchModeShell.tsx` now owns `FunctionRail`, `PipelineProgress`, `PrimaryPanel`, and `ContextPanel`, and `ResearchMode.tsx` dropped from 3174 lines to 2047 lines. Focused shell regressions now cover empty-desk quick start, furnace tab routing, pipeline-stage panel routing, rail section switching, and context-tab rendering via `editor/src/components/modes/__tests__/ResearchModeShell.test.ts`.
- Furnace session-sync extraction also landed on 2026-04-02: `useResearchFurnaceSessions.ts` now owns backend session polling, SSE stream ownership, and reconnect scheduling/status updates, which drops `ResearchMode.tsx` further from 2047 lines to 1858 lines and removes the last active session-stream effects from the top-level file.
- Focused regression coverage now includes `editor/src/components/modes/__tests__/useResearchFurnaceSessions.test.ts`, which locks the extracted hook around backend summary sync plus SSE reconnect behavior.
- Additional Research lifecycle hardening landed on 2026-04-02: `researchFurnaceSessionStatus.ts` now centralizes Furnace session lifecycle labels/tone so the desk shows `Ready`, `Live`, `Paused`, `Reconnecting`, `Completed`, and `Failed` states intentionally instead of rendering raw status strings. `ResearchMode.tsx` now uses that helper for the Furnace cards, and `researchFurnaceSessionStatus.test.ts` adds explicit regression coverage for live, reconnecting, and failed session-state presentation.
- Additional Furnace session extraction also landed on 2026-04-02: `ResearchFurnaceSessionCards.tsx` now owns the family/session card rendering, tag editing, recipe reveal, and per-session action wiring that previously sat inline inside `ResearchMode.tsx`. The Research mode file now stays more clearly about Furnace composition and desk layout, while `ResearchFurnaceSessionCards.test.ts` locks the extracted card/group surface around reconnecting-state presentation and expanded family summaries.
- Additional Furnace desk extraction landed on 2026-04-02: `ResearchFurnacePanel.tsx` now owns the full Furnace composer/session-desk surface, `ResearchMode.tsx` has dropped to shell-layout composition plus a lazy-loaded Furnace entry point, and the top-level Research session controller is now mounted with an explicit `activeMode === "research"` gate so live session polling survives desk-surface switches without keeping hidden modes active. The new Furnace desk also separates the warm composer lane from the cooler session desk, tightens first-use copy, and lazy-loads the full Furnace surface into its own chunk.
- Focused regression coverage now also includes `editor/src/components/modes/__tests__/ResearchFurnacePanel.test.ts`, while `useResearchFurnaceSessions.test.ts` now locks the new disabled-controller path so hidden/non-active Research shells do not keep syncing in the background.
- The remaining open work in this plan is manual flow smoke (`4-2`), not another large inline `FurnacePanel` breakup.
