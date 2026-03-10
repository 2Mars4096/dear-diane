# 31-13: Multi-Surface Continuity

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** not-started
**Goal:** Enable seamless task continuity across surfaces — start a task on desktop (dan-chat), continue on Telegram from your phone, come back to desktop and see the full thread. The Project scope is the continuity anchor, not the surface.

## Problem

DAN has a multi-surface gateway (Phase 13) that allows any surface to dispatch work. But each surface maintains its own conversation context. If you start a complex task on `dan-chat` and switch to Telegram, you lose the thread. The `Project`/`Task` model (25-6) already tracks work across surfaces, but conversation history isn't shared.

## Tasks

- [ ] 1. **Unified conversation view**
  - [ ] 1-1. `ProjectConversationStore` — aggregate conversation history across all surfaces for a project/task, ordered by timestamp
  - [ ] 1-2. When a surface connects and the user is in an active project, inject recent cross-surface history into the system prompt: "Recent activity on this project (from other surfaces): ..."
  - [ ] 1-3. Compact cross-surface history: include full messages from last 5 turns (any surface), summaries for older

- [ ] 2. **Surface handoff**
  - [ ] 2-1. Detect surface switch: when a user message on surface B relates to work started on surface A (same project/task), auto-link
  - [ ] 2-2. Handoff context: include the last response from surface A so the user doesn't need to repeat themselves
  - [ ] 2-3. `/sync` command: explicitly pull latest context from all surfaces for the current project

- [ ] 3. **Presence and routing**
  - [ ] 3-1. Track user's active surface (most recent message timestamp per surface)
  - [ ] 3-2. Route DAN-initiated messages (follow-ups, notifications, schedule results) to the active surface
  - [ ] 3-3. Surface priority: if multiple surfaces are active, prefer the one where the current project was most recently discussed

- [ ] 4. **Tests and docs**
  - [ ] 4-1. Unit tests: cross-surface history aggregation, handoff detection, presence tracking
  - [ ] 4-2. Integration test: message on surface A, continue on surface B, verify context carries over
  - [ ] 4-3. Update architecture, changelog

## Dependencies

- `ProjectStore` / `ProjectContextResolver` (25-6) for project/task tracking
- `ConversationMemoryStore` (26-3) for history persistence
- `ActivityTracker` (23-1) for surface presence
- `ConcurrentDispatcher` (27-2) for cross-surface routing
- Multi-surface gateway (Phase 13) for surface abstraction

## Estimate

1-1.5 days

## Notes

- This is lower complexity than it sounds because the Project scope already exists and tracks cross-surface work. The main new piece is injecting cross-surface conversation history into the prompt.
- Privacy consideration: all surfaces for the same user share the same project context. If different users share a surface (e.g., a Telegram group), project-level isolation must be respected.
- The `/sync` command is the escape hatch — if auto-detection fails, the user can explicitly pull context.
