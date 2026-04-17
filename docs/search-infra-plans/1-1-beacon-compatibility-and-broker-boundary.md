# 1-1: Beacon Compatibility And Broker Boundary

**Parent:** [1-beacon-search-infra](1-beacon-search-infra.md)
**Status:** completed
**Goal:** Introduce Beacon behind a stable current web-tool surface so search internals can change without breaking existing DAN callers.

## Tasks
- [x] 1. Freeze the caller-visible compatibility membrane
  - [x] 1-1. Keep current `web_search` and `web_fetch` capability schemas usable for existing agents and product surfaces
  - [x] 1-2. Enumerate which `SearchResultSet` fields are hard compatibility guarantees versus adapter-only metadata
  - [x] 1-3. Audit any direct provider-specific/raw-result callers that bypass the canonical web capability membrane
- [x] 2. Introduce an internal `SearchBroker` lifecycle
  - [x] 2-1. Define explicit broker stages: `discover`, `fetch/render`, `extract/chunk`, `retrieve/rank`, `cite`
  - [x] 2-2. Move retrieval policy ownership out of `handle_web_search(...)` and into the broker layer
  - [x] 2-3. Treat Tavily, Serper, Brave, and DDG as backend discovery adapters instead of primary product logic
- [x] 3. Add rollout controls without losing the old path
  - [x] 3-1. Support `legacy`, `hybrid`, and `beacon` execution modes behind config/feature flags
  - [x] 3-2. Keep the legacy cascade callable for fallback and A/B or shadow comparison
  - [x] 3-3. Emit enough telemetry to compare Beacon against the legacy path before any default cutover

## Decisions
- Public tool IDs stay `web_search` and `web_fetch` during Beacon incubation.
- The compatibility seam should be owned by DAN, not by any one provider adapter.

## Notes
- Finish this boundary first so corpus, ingest, and ranking work can target one stable internal lifecycle instead of today's split ownership.
- 2026-04-17 implementation slice:
  - added `src/dan/search/broker.py` plus `src/dan/search/__init__.py` as the first Beacon broker seam
  - `handle_web_search(...)` now routes provider-backed query execution through `SearchBroker` while preserving the current capability schema and output contract
  - `DAN_SEARCH_BROKER_MODE=legacy|hybrid|beacon` now controls the requested broker mode, and `DAN_SEARCH_BROKER_SHADOW_LEGACY=1` enables hybrid-mode shadow comparison against the legacy cascade
  - `src/dan/server/search_models.py` now publishes an explicit Beacon compatibility membrane for `SearchResult` / `SearchResultSet` hard guarantees versus adapter metadata
  - the repo now has a meta test that fails if new production code bypasses the broker boundary and imports `dan.tools.web_search` directly outside the allowed adapter module
