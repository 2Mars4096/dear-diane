# 1-4: Beacon Ranking, Grounding, And Citations

**Parent:** [1-beacon-search-infra](1-beacon-search-infra.md)
**Status:** not-started
**Goal:** Turn Beacon's corpus and adapters into meaningfully better retrieval by improving ranking, passage grounding, and citation quality beyond today's heuristic stack.

## Tasks
- [ ] 1. Replace heuristic-only ranking with broker-level hybrid ranking
  - [ ] 1-1. Combine lexical, semantic, authority, freshness, and query-intent signals
  - [ ] 1-2. Preserve explicit query decomposition plus novelty/dedup behavior
  - [ ] 1-3. Log per-result ranking features so evaluation and debugging are possible
- [ ] 2. Improve grounding and passage extraction
  - [ ] 2-1. Reuse the current browser/http fetch seam but feed outputs into broker evidence records instead of ad hoc excerpts alone
  - [ ] 2-2. Extract query-relevant passages at chunk level rather than mostly top-of-page truncation
  - [ ] 2-3. Distinguish snippet evidence, fetched evidence, and corpus-cached evidence in the result contract
- [ ] 3. Tighten citation and verification surfaces
  - [ ] 3-1. Emit stable citations keyed to `document_id`/`chunk_id` plus canonical URL
  - [ ] 3-2. Support post-answer verification against stored evidence instead of only transient result text
  - [ ] 3-3. Preserve current provider-native `search_result` replay compatibility where available

## Decisions
- Ranking and citation logic should be provider-agnostic and corpus-aware.
- Beacon should improve answer quality without regressing the current citation-verification hardening DAN already landed.

## Notes
- This plan is the quality gate. A separate corpus is not enough if ranking, grounding, and citation trust do not beat the current external-first path.
