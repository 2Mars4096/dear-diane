# 2-2: Chat Sidebar And History

**Parent:** [2-frontend-hardening](2-frontend-hardening.md)
**Status:** not-started
**Goal:** Decompose the shared mode chat sidebar into clearer layers and make chat/history behavior predictable across workflows, workspaces, and compact/full-screen surfaces.

## Context

Full-screen chat history discovery was recently fixed to find persisted threads across workflows, but the compact mode sidebar still carries a large amount of responsibility:

- local draft/session persistence in `localStorage`
- backend thread snapshot persistence
- streaming/tool-call lifecycle
- mode-specific context expansion
- Development-mode file review and terminal handoff
- maximize/minimize history handoff behavior

This makes the component fragile, difficult to test, and easy to desynchronize from full-screen Chat behavior.

## UI Brief

- **Surface:** compact per-mode chat as the sidecar conversation surface
- **Audience:** users who want quick follow-ups without abandoning their current mode
- **Primary action:** continue the same conversation, recover the right history, and expand to full chat without losing context
- **Dominant concept:** sidecar, not shadow app — compact chat should feel like a focused slice of the same conversation system
- **Constraints:** full-screen Chat remains the richer management surface, and mode-specific context injection still matters
- **Anti-goals:** competing histories, duplicate thread state, or maximize/minimize behavior that feels like copy-paste rather than continuity

## Visual System

- The composer and latest conversation state should dominate; management controls should stay quiet.
- Compact history rows should privilege title, recency, and only the minimum extra labels needed to disambiguate workflow/workspace ownership.
- Maximize/minimize should visually read as "open this same conversation in a larger canvas," not "switch to another chat product."

## No-Change Zones

- Keep full-screen Chat as the richer place for broad thread management, search, pin/export/delete, and deep history browsing.
- Keep compact chat mode-aware and faster than the full-screen surface rather than forcing complete parity everywhere.

## Tasks

- [ ] 1. Audit current persistence and ownership rules
  - [ ] 1-1. Trace every persistence path used by the sidebar: draft session, thread snapshot, branch actions, and history restore
  - [ ] 1-2. Decide which surface is the canonical source of truth for thread history in each mode
- [ ] 2. Decompose the sidebar into bounded layers
  - [ ] 2-1. Extract stream/transport lifecycle logic from render/composer logic
  - [ ] 2-2. Extract history/persistence logic from mode-specific tool actions
  - [ ] 2-3. Isolate Development-only affordances such as shell-command handoff and reviewable file edits behind explicit adapters
- [ ] 3. Reduce redundant or divergent UX between compact and full-screen chat
  - [ ] 3-1. Align maximize/minimize behavior with explicit handoff semantics instead of shared incidental state
  - [ ] 3-2. Intentionally define where compact chat remains mode-scoped and where history/search should match full-screen Chat
  - [ ] 3-3. Remove duplicate assumptions between temporary local draft state and persisted backend threads
  - [ ] 3-4. Establish one clear compact-surface hierarchy: conversation first, history second, utilities third
- [ ] 4. Verify history reliability
  - [ ] 4-1. Add frontend tests for restore, maximize/minimize handoff, and cross-workspace switching
  - [ ] 4-2. Manually smoke-test workflow switch / Save As / old-thread restore behavior in both compact and full-screen surfaces

## User-Facing Acceptance

- A user can explain where a thread lives and why it appears in compact or full chat without guessing about hidden local state.
- Maximize opens the same thread with the same recent state, not a lookalike.
- Compact chat feels focused and trustworthy rather than like a mini chat app missing random capabilities.

## Decisions

- Keep Development-only tool actions out of the generic reusable sidebar core.
- Preserve intentional mode-specific context injection, but move it behind explicit mode adapters rather than inline branching inside one component.

## Notes

- This subplan should absorb lessons from the recent global chat-history discovery fix instead of reintroducing split authority between local storage and server-backed threads.
