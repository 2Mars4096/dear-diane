# 36-8: Furnace Portal CLI

**Parent:** [36-recipe-distillation-spec](36-recipe-distillation-spec.md)
**Status:** completed
**Goal:** Add a dedicated furnace entry portal that bypasses concierge routing and controls furnace sessions directly through `/api/furnace/*`.

## Tasks
- [x] 1. Add a dedicated CLI entry point (`dan-furnace`) for furnace lifecycle control.
  - [x] 1-1. Implement command surface for create/add/start/resume/pause/cancel/list/status/watch.
  - [x] 1-2. Add quality/cost controls (`estimate`, `budget`) and artifact retrieval (`recipe`).
  - [x] 1-3. Add all-in-one `ignite` command (create + add sources + start + optional watch).
  - [x] 1-4. Add intuitive one-liner `run <topic> [sources...]` with source auto-detection and help examples.
- [x] 2. Wire unified `dan` dispatcher to expose `dan furnace`.
- [x] 3. Update architecture/changelog/todo tracking docs.
- [x] 4. Align frontend portals with the same intuitive flow.
  - [x] 4-1. Add source auto-detection helper for path/url/source-id parsing.
  - [x] 4-2. Wire Distillation setup start action to real furnace create/add/start APIs.
  - [x] 4-3. Add direct source input in FurnacePanel and simplify CTA wording to `Run Furnace`.
  - [x] 4-4. Add quick, copy-ready source format examples in setup UIs.

## Decisions
- Dedicated CLI should call furnace endpoints directly via HTTP and avoid concierge/triage paths.
- Keep command syntax ergonomic for power users: repeatable `--pdf`, `--url`, `--source-id` and optional live `--watch`.

## Notes
- `watch` uses SSE (`/api/furnace/sessions/{id}/events`) and prints compact phase/source/session progress lines.
- `ignite` is the recommended entry for fast operational use after `dan-up`.
- 2026-03-16 UX follow-up: Research-mode Furnace cards now mirror `watch`-style progressive feedback (phase/status/recent events), persist sessions in local store across refresh, hydrate from `furnaceListSessions()` on load, and auto-reconnect SSE for active sessions so users can resume monitoring without re-creating sessions.
- 2026-03-16 input-hardening follow-up: source parsing now defensively splits accidental one-line multi-entry pastes (for example space-joined absolute PDF paths) in both frontend (`furnaceSources.ts`) and backend (`/api/furnace/sessions/{id}/sources`) to reduce skipped-source runs from formatting mistakes.
