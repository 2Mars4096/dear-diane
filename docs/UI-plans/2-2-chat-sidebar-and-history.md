# 2-2: Chat Sidebar And History

**Parent:** [2-frontend-hardening](2-frontend-hardening.md)
**Status:** in-progress
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

- [x] 1. Audit current persistence and ownership rules
  - [x] 1-1. Trace every persistence path used by the sidebar: draft session, thread snapshot, branch actions, and history restore
  - [x] 1-2. Decide which surface is the canonical source of truth for thread history in each mode
- [x] 2. Decompose the sidebar into bounded layers
  - [x] 2-1. Extract stream/transport lifecycle logic from render/composer logic
  - [x] 2-2. Extract history/persistence logic from mode-specific tool actions
  - [x] 2-3. Isolate Development-only affordances such as shell-command handoff and reviewable file edits behind explicit adapters
- [ ] 3. Reduce redundant or divergent UX between compact and full-screen chat
  - [x] 3-1. Align maximize/minimize behavior with explicit handoff semantics instead of shared incidental state
  - [ ] 3-2. Intentionally define where compact chat remains mode-scoped and where history/search should match full-screen Chat
  - [x] 3-3. Remove duplicate assumptions between temporary local draft state and persisted backend threads
  - [x] 3-4. Establish one clear compact-surface hierarchy: conversation first, history second, utilities third
- [ ] 4. Verify history reliability
  - [x] 4-1. Add frontend tests for restore, maximize/minimize handoff, and cross-workspace switching
  - [ ] 4-2. Manually smoke-test workflow switch / Save As / old-thread restore behavior in both compact and full-screen surfaces

## User-Facing Acceptance

- A user can explain where a thread lives and why it appears in compact or full chat without guessing about hidden local state.
- Maximize opens the same thread with the same recent state, not a lookalike.
- Compact chat feels focused and trustworthy rather than like a mini chat app missing random capabilities.

## Decisions

- Keep Development-only tool actions out of the generic reusable sidebar core.
- Preserve intentional mode-specific context injection, but move it behind explicit mode adapters rather than inline branching inside one component.
- Treat server-backed threads as the canonical history surface; compact local storage is only for per-workflow draft/session continuity.
- Legacy compact-chat storage fallback should only apply to `_scratch`, so old ambiguous `workspace + mode` keys cannot leak drafts into named workflows.
- Keep streaming/channel ownership behind a dedicated transport hook so the visible sidebar shell can stay focused on hierarchy, composer input, and thread selection.

## Notes

- This subplan should absorb lessons from the recent global chat-history discovery fix instead of reintroducing split authority between local storage and server-backed threads.
- Initial UX pass landed on 2026-04-02: Development and Research removed their duplicate top-bar chat buttons so the sidecar has one clear local affordance, and `ModeChatSidebar` now shows mode-specific sidecar labeling plus clearer empty-state copy.
- Second hardening slice landed on 2026-04-02: compact sidebar session state is now keyed by `workspace + mode + workflow` via `editor/src/components/shared/modeChatSidebarSession.ts`, the rendered sidebar is remounted by that explicit scope so workflow switches load the right draft/session immediately, scratch-only legacy fallback preserves old local drafts without leaking them into named workflows, and history/empty-state labels now surface the active workflow explicitly.
- Focused regression coverage now includes `editor/src/components/shared/__tests__/modeChatSidebarSession.test.ts` plus a workflow-switching compact-sidebar test in `editor/src/components/shared/__tests__/ModeChatSidebar.test.ts`.
- Development-only native-action isolation also landed on 2026-04-02: `useModeChatSidebarNativeActions.ts` now owns shell-command handoff, pre-write snapshot capture, and reviewable file-edit persistence instead of leaving those side effects inline inside `ModeChatSidebar.tsx`.
- Compact chat is now more mode-truthful too: shell `Run` affordances and review/edit handoff only appear when the Development adapter is active, instead of implying those behaviors are universal across Research and other sidecars.
- Focused regression coverage now also includes `editor/src/components/shared/__tests__/useModeChatSidebarNativeActions.test.ts`, and `ModeChatSidebar.test.ts` now checks both mode-truthful shell run controls and compact-to-full-chat handoff for the current conversation.
- Additional transport decomposition landed on 2026-04-02: `useModeChatSidebarTransport.ts` now owns compact-chat send/stream lifecycle, queued-message injection, channel ownership, thread polling fallback, and request-side context assembly. `ModeChatSidebar.tsx` is back to being mostly shell state, thread management, and composer UI instead of also carrying the full request runtime.
- Focused regression coverage now also includes workspace-scoped session restore in `editor/src/components/shared/__tests__/ModeChatSidebar.test.ts`, so compact chat now has explicit frontend coverage for workflow switching, workspace switching, and compact-to-full-chat handoff.
- History/handoff decomposition also landed on 2026-04-02: `useModeChatSidebarHistory.ts` now owns workflow-scoped thread listing, thread restore, new-chat reset, compact-to-full-chat handoff preparation, and clear/reset behavior. `ModeChatSidebar.tsx` no longer keeps backend thread snapshot persistence, maximize handoff queuing, and local draft reset semantics inline beside the render tree.
- Current validation after the history extraction: `cd editor && npm test` (`50` files / `231` tests), `cd editor && npm run build`, and `cd editor && npm run bundle:check` all pass. The remaining warnings are still the known Node `20.17.0` version mismatch and the intentionally heavy Monaco/worker path.
- The remaining compact-chat work is now mostly the final compact-vs-full history policy call plus manual Electron/Desktop smoke, not another large inline side-effect block in `ModeChatSidebar.tsx`.
