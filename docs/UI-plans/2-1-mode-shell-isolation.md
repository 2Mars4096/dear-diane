# 2-1: Mode Shell Isolation

**Parent:** [2-frontend-hardening](2-frontend-hardening.md)
**Status:** in-progress
**Goal:** Make visited-but-hidden modes inert unless explicitly active, so keyboard shortcuts, listeners, and shared stores do not leak behavior across DAN modes.

## Context

The current shell preserves mode-local state by keeping visited modes mounted per workspace. That is useful, but it needs an explicit contract: hidden modes should not keep responding to keyboard shortcuts or mutating shared state unless that behavior is intentionally global.

Known issues from the review:

- Research mode installs shortcuts without checking whether the active layout mode is actually `research`.
- Research mode currently toggles terminal state through `useCodeStore`, so a Research shortcut can still mutate Development-owned terminal state while hidden.
- Code mode and other modes also register window-level listeners; they need a clean audit so hidden-mode behavior is deliberate rather than accidental.

## UI Brief

- **Surface:** shell-level interaction between mounted modes
- **Audience:** users who switch layouts often and rely on shortcuts
- **Primary action:** the visible mode should be the only mode that reacts to mode-local input
- **Dominant concept:** invisible correctness — the shell feels boring because it is predictable
- **Constraints:** keep workspace-local mode state intact and avoid gratuitous remounts
- **Anti-goals:** global shortcut roulette, hidden-mode state drift, or flashy remount behavior that loses context

## No-Change Zones

- Keep the current workspace model and the general "visited modes can preserve local state" behavior unless the audit proves it cannot be made safe.
- Keep existing shortcut vocabulary where possible; the main change should be ownership and gating, not retraining users on new keys.

## Tasks

- [x] 1. Audit shell-level listeners, shortcuts, and subscriptions across mounted modes
  - [x] 1-1. Inventory all window/document listeners registered by Chat, Development, Research, and Operations mode shells
  - [x] 1-2. Mark each listener as `active-mode only`, `workspace-global`, or `always-on by design`
- [x] 2. Harden mode-local shortcut ownership
  - [x] 2-1. Gate Research shortcuts and mode-chat toggles on `activeMode === "research"`
  - [x] 2-2. Re-check Development mode global listeners and shortcut handlers under the same contract
  - [x] 2-3. Add or standardize a shared shell helper for active-mode window listeners so future mode hooks cannot drift back into ungated listeners
- [ ] 3. Tighten shared-state boundaries between modes
  - [x] 3-1. Decide whether terminal visibility/state is truly shared between Research and Development or should become mode-scoped
  - [ ] 3-2. Move any mode-local shell state out of shared stores if that state should not survive cross-mode interaction
- [ ] 4. Verify isolation behavior
  - [x] 4-1. Add focused frontend tests that open one mode, switch away, and assert hidden-mode shortcuts no longer fire
  - [ ] 4-2. Manually verify `Cmd+E`, `Cmd+I`, `Cmd+\`` and `Cmd+J` behavior across mode switches

## User-Facing Acceptance

- After opening Research once, switching back to Development means Research never responds again until it is reactivated.
- Toggling chat, sidebar, or terminal feels local to the visible mode rather than globally spooky.
- Workspace-local state remains intact without making hidden modes behaviorally active.

## Decisions

- Preserve the "visited modes stay mounted" shell model unless the audit shows it is the root cause of multiple unavoidable bugs.
- Prefer explicit active-mode guards over relying on hidden DOM or pointer-events to make a mode inert.
- Keep terminal visibility in the shared code store for now, but only the active mode may toggle it through shell listeners/shortcuts.

## Notes

- If the current keep-alive approach remains, document a small mode-shell contract for hooks: listeners must either be active-mode gated or registered through a shared shell utility that handles gating centrally.
- Landed 2026-04-02: `editor/src/hooks/useModeScopedWindowEvent.ts` now centralizes active-mode gating for window listeners, Research shortcuts use it for `keydown`, and Development/Research/Operations mode chat toggles now all route through that shared contract.
- Follow-up hardening landed 2026-04-03: `useModeScopedWindowEvent.ts` now keeps a ref-backed latest callback so inline mode-shell listeners no longer tear down and re-register on every render, and focused coverage now proves rerendered listeners keep one DOM subscription while still dispatching to the latest callback.
- Focused regression coverage landed in `editor/src/hooks/__tests__/useModeScopedWindowEvent.test.ts`. Manual cross-mode shortcut smoke testing is still open.
