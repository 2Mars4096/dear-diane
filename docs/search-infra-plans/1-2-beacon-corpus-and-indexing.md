# 1-2: Beacon Corpus And Indexing

**Parent:** [1-beacon-search-infra](1-beacon-search-infra.md)
**Status:** not-started
**Goal:** Build a first-party persistent corpus and retrieval index so DAN can answer more searches from its own accumulated evidence.

## Tasks
- [ ] 1. Define persistent document and chunk records
  - [ ] 1-1. Persist canonical URL, source type, content hash, fetched timestamp, published timestamp, and freshness metadata
  - [ ] 1-2. Add stable `document_id` and `chunk_id` identifiers decoupled from provider response shapes
  - [ ] 1-3. Track dedup lineage, refresh state, and TTL/eviction rules
- [ ] 2. Build local retrieval indices
  - [ ] 2-1. Add lexical search over stored documents/chunks
  - [ ] 2-2. Add optional embedding-backed semantic retrieval where it improves recall materially
  - [ ] 2-3. Support hybrid retrieve-plus-rerank against the local corpus before external discovery
- [ ] 3. Reuse existing fetch work instead of discarding it
  - [ ] 3-1. Save grounded fetch outputs from current search/fetch flows into the corpus
  - [ ] 3-2. Backfill high-value repeated domains and queries from prior DAN usage where feasible
  - [ ] 3-3. Distinguish cached, indexed, and fresh-discovery evidence in broker metadata so callers can reason about provenance

## Decisions
- Beacon v1 should prefer a simple persistent local store plus index rather than a distributed search cluster.
- The corpus is not just a cache; it is the reusable evidence base that should compound quality over repeated DAN usage.

## Notes
- This plan is where first-party advantage begins. Without persistent document/chunk storage, Beacon is just another metasearch wrapper.
