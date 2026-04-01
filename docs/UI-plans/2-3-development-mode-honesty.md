# 2-3: Development Mode Honesty

**Parent:** [2-frontend-hardening](2-frontend-hardening.md)
**Status:** not-started
**Goal:** Make Development mode truthful and reliable by clearly separating Electron-only capabilities from browser-preview-safe surfaces, and by removing or downgrading UI that looks finished but is still placeholder or demo-only.

## Context

The review confirmed that Development mode is a real desktop-app surface but only a partial browser-preview demo:

- file I/O, git, ripgrep, LSP, terminal, watch, and debug rely on Electron-backed bridges
- browser fallbacks mostly return `null`, `false`, empty strings, or `Not in Electron`
- the browser warning banner is truthful, but much of the IDE chrome still renders as if it were fully interactive
- Workflow and Furnace sidebar entries inside Code mode are currently placeholder panels rather than real tools

The hardening goal here is not to make browser preview fully capable. It is to make the surface honest so users do not mistake a demo shell for a working IDE.

## UI Brief

- **Surface:** Development mode in desktop Electron and browser preview
- **Audience:** daily developers using the desktop app plus users evaluating the shell in browser preview
- **Primary action:** know within a couple of seconds what is truly usable in the current runtime
- **Dominant concept:** real tool, honest demo — the desktop app should feel serious, and the browser preview should feel intentionally scoped rather than broken
- **Constraints:** preserve the dense VS Code-like desktop shell and avoid turning the browser into a fake parity target
- **Anti-goals:** dead clicks, decorative placeholder panels, or warning copy that shouts constantly without clarifying what still works

## Visual System

- Desktop-only features should degrade through explicit disabled states, scoped banners, or capability badges, not through silent no-ops.
- Warning language should be precise and local where possible: what works here, what requires desktop, and what action to take next.
- Placeholder surfaces should either become real surfaces or be replaced with direct, visually honest handoffs to the right mode.

## No-Change Zones

- Keep the desktop Development shell Monaco-centric and VS Code-like in density and layout.
- Do not pursue full browser parity inside this plan; honesty is the goal, not universal capability.

## Tasks

- [ ] 1. Classify Development-mode capabilities by runtime
  - [ ] 1-1. Inventory every major panel/action as `desktop-only`, `browser-safe`, or `placeholder`
  - [ ] 1-2. Define the minimum browser-preview contract the app should support intentionally
- [ ] 2. Harden capability gating and messaging
  - [ ] 2-1. Disable or hide nonfunctional controls in browser preview instead of relying on inert bridge fallbacks
  - [ ] 2-2. Tighten the warning/banner copy so it names what still works and what does not
  - [ ] 2-3. Make terminal, git, search, and LSP surfaces visibly unavailable when they are not backed by Electron
  - [ ] 2-4. Ensure the browser-preview states look intentionally disabled, not visually broken
- [ ] 3. Remove low-trust placeholder surfaces
  - [ ] 3-1. Replace Code-mode Workflow/Furnace placeholder sidebars with real functionality or remove the activity-bar entries until implemented
  - [ ] 3-2. Audit other low-signal or decorative IDE chrome that implies capability it does not yet have
- [ ] 4. Reduce Code-mode shell complexity while hardening it
  - [ ] 4-1. Extract shortcut wiring, shell chrome, and mode-chat context assembly out of the main `CodeMode` file
  - [ ] 4-2. Establish a small runtime-capability utility that child panels can consume consistently
- [ ] 5. Verify desktop-vs-browser honesty
  - [ ] 5-1. Add focused tests for browser gating and warning states
  - [ ] 5-2. Manually smoke-test the same flows in Electron and browser preview

## User-Facing Acceptance

- In browser preview, the user can tell at a glance which capabilities are real, which are preview-only, and where the desktop app is required.
- In the desktop app, the shell no longer exposes obviously placeholder panels as if they were finished tools.
- Development mode looks more trustworthy after the hardening pass even if the feature list does not materially expand.

## Decisions

- Treat Electron desktop as the primary supported Development-mode runtime.
- Keep browser preview as a demo/layout surface only if the unsupported controls are clearly gated and non-misleading.

## Notes

- This subplan is about trust and clarity, not about expanding browser support to parity with Electron.
