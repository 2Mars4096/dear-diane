# 1-3: Beacon Connectors And Ingest

**Parent:** [1-beacon-search-infra](1-beacon-search-infra.md)
**Status:** completed
**Goal:** Feed Beacon from both bounded external discovery adapters and first-party ingest connectors aimed at DAN's highest-value source families.

## Tasks
- [x] 1. Keep third-party search providers as bounded discovery adapters
  - [x] 1-1. Normalize Tavily, Serper, Brave, and DDG outputs into one broker discovery-hit contract
  - [x] 1-2. Separate discovery ranking from raw provider order
  - [x] 1-3. Preserve provider health, domain controls, and budget guards during transition
- [x] 2. Add first-party high-value connectors
  - [x] 2-1. Prioritize official documentation, GitHub docs/releases/README surfaces, arXiv or Semantic Scholar, and authoritative company/regulatory pages
  - [x] 2-2. Support sitemap/feed/API/static-doc ingestion where those sources make it practical
  - [x] 2-3. Respect robots, rate limits, and per-source freshness rules
- [x] 3. Add refresh and backfill jobs
  - [x] 3-1. Schedule refresh for hot sources instead of relying only on ad hoc fetches
  - [x] 3-2. Trigger on-demand fetch when a result is stale or absent from the corpus
  - [x] 3-3. Deduplicate mirrored or near-identical pages before indexing

## Decisions
- First-party ingest should focus on narrow high-value verticals before attempting broad general-web crawl coverage.
- External provider APIs remain valid cold-start discovery inputs even after first-party connectors land.

## Notes
- This plan is intentionally not “build Google.” It is “own the source families DAN hits repeatedly enough that the fallback providers become the exception, not the core product.”
- 2026-04-17 implementation slice:
  - added `src/dan/search/connectors.py` with `DiscoveryHit`, provider-result normalization, broker-level discovery reranking, source-family classification, TTL/rate-limit/robots source profiles, sitemap/feed/static-doc seed helpers, and refresh-queue construction over stale corpus records
  - Beacon now treats legacy provider APIs as discovery adapters while keeping domain controls and budget guards in the existing caller-visible web capability path
  - the corpus now records near-duplicate lineage via `duplicate_of_document_id`, and Beacon triggers external fill automatically when the corpus is stale or sparse for a query
