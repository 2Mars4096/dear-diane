# 1-1: Beacon Compatibility And Broker Boundary

**Parent:** [1-beacon-search-infra](1-beacon-search-infra.md)
**Status:** not-started
**Goal:** Introduce Beacon behind a stable current web-tool surface so search internals can change without breaking existing DAN callers.

## Tasks
- [ ] 1. Freeze the caller-visible compatibility membrane
  - [ ] 1-1. Keep current `web_search` and `web_fetch` capability schemas usable for existing agents and product surfaces
  - [ ] 1-2. Enumerate which `SearchResultSet` fields are hard compatibility guarantees versus adapter-only metadata
  - [ ] 1-3. Audit any direct provider-specific/raw-result callers that bypass the canonical web capability membrane
- [ ] 2. Introduce an internal `SearchBroker` lifecycle
  - [ ] 2-1. Define explicit broker stages: `discover`, `fetch/render`, `extract/chunk`, `retrieve/rank`, `cite`
  - [ ] 2-2. Move retrieval policy ownership out of `handle_web_search(...)` and into the broker layer
  - [ ] 2-3. Treat Tavily, Serper, Brave, and DDG as backend discovery adapters instead of primary product logic
- [ ] 3. Add rollout controls without losing the old path
  - [ ] 3-1. Support `legacy`, `hybrid`, and `beacon` execution modes behind config/feature flags
  - [ ] 3-2. Keep the legacy cascade callable for fallback and A/B or shadow comparison
  - [ ] 3-3. Emit enough telemetry to compare Beacon against the legacy path before any default cutover

## Decisions
- Public tool IDs stay `web_search` and `web_fetch` during Beacon incubation.
- The compatibility seam should be owned by DAN, not by any one provider adapter.

## Notes
- Finish this boundary first so corpus, ingest, and ranking work can target one stable internal lifecycle instead of today's split ownership.
