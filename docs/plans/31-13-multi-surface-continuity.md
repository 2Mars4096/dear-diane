# 31-13: Multi-Surface Continuity

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** not-started
**Goal:** Enable seamless task continuity across surfaces — start a task on desktop (dan-chat), continue on Telegram from your phone, come back to desktop and see the full thread. The Project scope is the continuity anchor, not the surface.

## Problem

DAN has a multi-surface gateway (Phase 13) that allows any surface to dispatch work. But each surface maintains its own conversation context. If you start a complex task on `dan-chat` and switch to Telegram, you lose the thread. The `Project`/`Task` model (25-6) already tracks work across surfaces, but conversation history isn't shared.

## Tasks

- [ ] 1. **Unified conversation view**
  - [ ] 1-1. `ProjectConversationStore` — aggregate conversation history across all surfaces for a project/task, ordered by timestamp
  - [ ] 1-2. When a surface connects and the user is in an active task, inject a **task-first handoff payload** into the system prompt: `TaskSnapshot` from 31-11 first, recent task turns second, project summary last
  - [ ] 1-3. Compact cross-surface history: include full messages from last 5 turns (any surface), summaries for older
  - [ ] 1-4. `SurfaceRoutingPolicy`: `visibility: Literal["private", "shared"]`, `allow_project_context: bool`, `allow_follow_ups: bool`. Shared/group surfaces default to **no continuity injection** unless the project explicitly opts in.

- [ ] 2. **Surface handoff**
  - [ ] 2-1. Detect surface switch: when a user message on surface B relates to work started on surface A (same project/task), auto-link
  - [ ] 2-2. Handoff context: include the current `TaskSnapshot` and the last response from surface A so the user doesn't need to repeat themselves
  - [ ] 2-3. `/sync` command: explicitly pull latest context from all surfaces for the current project

- [ ] 3. **Presence and routing**
  - [ ] 3-1. Track user's active surface (most recent message timestamp per surface)
  - [ ] 3-2. Route DAN-initiated messages (follow-ups, notifications, schedule results) to the active **private** surface by default; require explicit project-level opt-in before routing task context into shared/group chats
  - [ ] 3-3. Surface priority: if multiple surfaces are active, prefer the one where the current project was most recently discussed

- [ ] 4. **Tests and docs**
  - [ ] 4-1. Unit tests: cross-surface history aggregation, handoff detection, presence tracking
  - [ ] 4-2. Integration test: message on surface A, continue on surface B, verify context carries over
  - [ ] 4-3. Privacy test: private-chat to shared-chat handoff does not leak project/task context without explicit opt-in
  - [ ] 4-4. Update architecture, changelog

## Dependencies

- `ProjectStore` / `ProjectContextResolver` (25-6) for project/task tracking
- `ConversationMemoryStore` (26-3) for history persistence
- `ActivityTracker` (23-1) for surface presence
- `ConcurrentDispatcher` (27-2) for cross-surface routing
- Multi-surface gateway (Phase 13) for surface abstraction

## Estimate

1-1.5 days

## Primary Files

- `src/dan/server/concierge/continuity.py` — `ProjectConversationStore`, cross-surface history aggregation, handoff detection, `SurfaceRoutingPolicy` (new)
- `src/dan/server/concierge/runtime.py` — handoff payload injection on surface connect (modify)
- `src/dan/server/concierge/project_store.py` — `SurfaceRoutingPolicy` persistence (modify, per-project in `ProjectStore`)
- `src/dan/server/capability_handlers.py` — `/sync` command handler (modify)

## Notes

- This is lower complexity than it sounds because the Project scope already exists and tracks cross-surface work. The main new piece is injecting cross-surface conversation history into the prompt.
- **`ProjectConversationStore` vs `ConversationMemoryStore`:** `ProjectConversationStore` is a **view** over existing per-surface conversation stores, not a new independent store. It queries `ConversationMemoryStore` entries tagged with the same `project_id`, aggregates them by timestamp, and returns a unified view. No data duplication.
- **Surface-switch detection (task 2-1):** v1 uses heuristic matching — if the user's message on surface B mentions the same project label (by name or keyword match) as active work on surface A, auto-link. This reuses the existing `ProjectContextResolver.infer_project()` which already does keyword/similarity matching. No extra LLM call needed.
- **`SurfaceRoutingPolicy` persistence:** stored per-project in `ProjectStore` as an optional field. Default: `visibility="private"`, `allow_project_context=True`, `allow_follow_ups=False`. Group chats require explicit `/sync --allow-group` to opt in.
- Privacy consideration: all surfaces for the same user do **not** automatically share the same project context. Private 1:1 surfaces are continuity-safe by default; shared/group surfaces require explicit opt-in per project before context or DAN-initiated messages are routed there.
- **31-12 integration:** When 31-12 ships, it should use this plan's `SurfaceRoutingPolicy` and presence tracking for follow-up delivery routing instead of its own simple heuristic. Design the routing interface to be shared.
- The `/sync` command is the escape hatch — if auto-detection fails, the user can explicitly pull context.
- **Estimate note:** 1-1.5 days is tight for cross-surface detection, policy model, handoff injection, presence tracking, `/sync`, and tests. 2 days is more realistic.
- **Registry note (31-16):** Register `/sync` via `CommandDescriptor` if 31-16 has landed.
