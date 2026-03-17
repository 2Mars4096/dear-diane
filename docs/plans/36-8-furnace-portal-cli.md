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
- 2026-03-16 session lifecycle follow-up: added explicit session deletion from Research-mode cards, backed by `DELETE /api/furnace/sessions/{session_id}` (with active-session guard + optional artifact cleanup) so users can prune obsolete/failed runs cleanly.
- 2026-03-16 delete UX follow-up: session-delete failures now surface backend reason text in toast notifications, and the card action shows an inline `Deleting...` loading state to avoid duplicate clicks during round-trip.
- 2026-03-16 legacy-store follow-up: deletion now handles pre-migration local sessions missing `sessionId` by backfilling IDs during backend sync (`name`+`topic` match) and by resolving backend IDs at delete-time; unmatched local-only sessions can still be removed from UI history.
- 2026-03-16 input-hardening follow-up: source parsing now defensively splits accidental one-line multi-entry pastes (for example space-joined absolute PDF paths) in both frontend (`furnaceSources.ts`) and backend (`/api/furnace/sessions/{id}/sources`) to reduce skipped-source runs from formatting mistakes.
- 2026-03-16 pill compaction follow-up: when project output is too long, furnace now keeps full text in `recipe_full.md` but writes a compact, bounded, action-ready default pill to `recipe.md` so the primary artifact stays operational.
- 2026-03-16 continuation/variant UX follow-up: Research-mode session cards now provide `Continue` and `Variant` actions that prefill recipe/topic/target context and switch CTA behavior (`Continue Training`, `Create Variant`) so users can extend an existing lineage or branch a new run with minimal friction.
- 2026-03-16 detailed-pill follow-up: Furnace session cards now render key runtime telemetry as compact visual pills (phase, paper progress, percent, cost, recency, extracted stats) for better scanability during concurrent sessions.
- 2026-03-16 continuation state-machine follow-up: Continue action now routes lifecycle calls by session status (`idle` start, `paused`/`failed` resume, `running` observe-only attach, `completed` guard to variant) to stay aligned with backend semantics.
- 2026-03-16 continuation UX guardrail follow-up: completed cards now steer users into Variant mode directly (and stale completed-continue drafts auto-convert) so `Continue Training` no longer lands in a no-op/error dead end.
- 2026-03-16 true-variant follow-up: backend `create_session` now accepts `parent_session_id` + `inherit_sources`, variant creation copies the parent source queue/metadata into a new paused session, and completed-session continue can no longer append sources to the original run before converting to variant mode.
- 2026-03-17 CTA cleanup follow-up: completed session cards now show only a single `Variant` action (not both `Branch` and `Variant`), while resumable sessions keep `Continue`, reducing ambiguity about which path actually mutates versus forks.
- 2026-03-17 session-browser follow-up: Training Sessions now groups lineage-related pills into collapsible families and exposes search, filter tags, and sorting; backend session summaries include `recipe_id`, `parent_session_id`, `family_session_id`, and `variant_label` so grouping survives refresh instead of only the current browser session.
- 2026-03-17 pill-tagging follow-up: furnace sessions now persist user-defined `tags` in the session model, expose them via list summaries and `POST /api/furnace/sessions/{id}/tags`, and let Research-mode cards add/remove tags inline with click-to-filter behavior plus quick suggestions from the existing tag set.
- 2026-03-17 tag-hardening follow-up: variant creation now inherits parent tags so forked pills stay organized under the same tag vocabulary, and Research-mode tag edits now resolve legacy cards without `sessionId` to backend sessions when possible or fall back to local persistence instead of showing a hard error.
- 2026-03-17 failed-retry follow-up: failed Furnace cards now expose a direct `Retry` action (instead of only the generic Continue setup path), wired to backend resume semantics; the shared frontend session-resolution fallback also now prefers recipe/lineage-aware matches plus normalized name/topic matching so retry/delete actions bind the correct older card more reliably.
