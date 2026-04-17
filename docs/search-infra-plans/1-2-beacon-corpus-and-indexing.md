# 1-2: Beacon Corpus And Indexing

**Parent:** [1-beacon-search-infra](1-beacon-search-infra.md)
**Status:** completed
**Goal:** Build a first-party persistent corpus and retrieval index so DAN can answer more searches from its own accumulated evidence.

## Tasks
- [x] 1. Define persistent document and chunk records
  - [x] 1-1. Persist canonical URL, source type, content hash, fetched timestamp, published timestamp, and freshness metadata
  - [x] 1-2. Add stable `document_id` and `chunk_id` identifiers decoupled from provider response shapes
  - [x] 1-3. Track dedup lineage, refresh state, and TTL/eviction rules
- [x] 2. Build local retrieval indices
  - [x] 2-1. Add lexical search over stored documents/chunks
  - [x] 2-2. Add optional embedding-backed semantic retrieval where it improves recall materially
  - [x] 2-3. Support hybrid retrieve-plus-rerank against the local corpus before external discovery
- [x] 3. Reuse existing fetch work instead of discarding it
  - [x] 3-1. Save grounded fetch outputs from current search/fetch flows into the corpus
  - [x] 3-2. Backfill high-value repeated domains and queries from prior DAN usage where feasible
  - [x] 3-3. Distinguish cached, indexed, and fresh-discovery evidence in broker metadata so callers can reason about provenance

## Decisions
- Beacon v1 should prefer a simple persistent local store plus index rather than a distributed search cluster.
- The corpus is not just a cache; it is the reusable evidence base that should compound quality over repeated DAN usage.

## Notes
- This plan is where first-party advantage begins. Without persistent document/chunk storage, Beacon is just another metasearch wrapper.
- 2026-04-17 implementation slice:
  - added `src/dan/search/corpus.py`, a SQLite-backed Beacon corpus with document/chunk IDs, canonical URLs, content hashes, freshness/TTL metadata, dedup lineage, FTS retrieval, lazy chunk-embedding storage, and refresh-candidate listing
  - the default Beacon executor now queries the local corpus first, optionally adds semantic hits when `DAN_BEACON_ENABLE_EMBEDDINGS=1`, and only falls back to external discovery when corpus recall is insufficient
  - `handle_web_search(...)` and `handle_web_fetch(...)` now persist grounded fetches into the Beacon corpus and surface indexed/provenance metadata to callers
  - added `src/dan/search/backfill.py` so prior `web_search` / `web_fetch` capability payloads can seed the corpus from historical DAN usage
