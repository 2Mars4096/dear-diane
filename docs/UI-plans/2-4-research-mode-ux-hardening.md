# 2-4: Research Mode UX Hardening

**Parent:** [2-frontend-hardening](2-frontend-hardening.md)
**Status:** not-started
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

- [ ] 1. Audit the current surface for redundancy and cognitive load
  - [ ] 1-1. Reassess duplicated chat toggles and decide on a single primary chat affordance
  - [ ] 1-2. Reassess terminal visibility inside Research mode and its coupling to the code store
  - [ ] 1-3. Identify low-value controls in the left rail and top bar that can be consolidated or removed
- [ ] 2. Split the shell into durable sub-surfaces
  - [ ] 2-1. Extract `FunctionRail`, `PrimaryPanel`, `ContextPanel`, and `PipelineProgress` into separate modules/hooks
  - [ ] 2-2. Extract Furnace session orchestration and SSE ownership out of the top-level mode component
  - [ ] 2-3. Keep layout code focused on layout, not session business logic
- [ ] 3. Tighten research-specific UX truthfulness
  - [ ] 3-1. Clarify session lifecycle states: starting, running, paused, reconnecting, failed, completed
  - [ ] 3-2. Make document-writing vs training/session-management actions visually distinct
  - [ ] 3-3. Ensure long-running session state survives surface switching without hidden surprises
  - [ ] 3-4. Tighten empty/first-use states so users can tell where to write, where to find papers, and where to manage training without guesswork
- [ ] 4. Verify Research mode flows
  - [ ] 4-1. Add focused tests for panel toggles, session-state rendering, and SSE reconnect behavior
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
