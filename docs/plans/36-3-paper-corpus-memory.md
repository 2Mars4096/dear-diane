# 36-3: Corpus Memory Schema

**Parent:** [36-recipe-distillation-spec](36-recipe-distillation-spec.md)
**Status:** completed
**Goal:** Extend the existing memory system with a source-agnostic corpus layer that can store source-level evidence, domain-level patterns, and recipe-level promoted knowledge without replacing the current MemoryKernel. Supports papers, blogs, docs, code, conversations, books, videos, notes, and other source types via `SourceType` enum.

## Tasks
- [x] 1. Define metadata extensions on `MemoryItem`
  - [x] 1-1. Add `family = "corpus"` (generalized from `paper_corpus`)
  - [x] 1-2. Add `corpus_id`, `source_id`, `source_type`, `knowledge_kind`, `generality`
  - [x] 1-3. Add evidence fields: page/section refs, support count, venue weight, confidence
- [x] 2. Finalize the `knowledge_kind` taxonomy
  - [x] 2-1. Source-level: source_metadata, source_summary, claim, method, dataset, measure
  - [x] 2-2. Domain-level: terminology, citation_norm, rhetorical_move, question_pattern, association_edge
  - [x] 2-3. Recipe-level: taste_signal, writing_rule, anti_pattern, recipe_snapshot
- [x] 3. Define promotion rules
  - [x] 3-1. Promote paper-level signals into domain-level patterns when recurrence is high enough
  - [x] 3-2. Promote domain-level patterns into recipe output only when evidence is strong enough
  - [x] 3-3. Keep low-confidence findings as paper-level evidence instead of over-generalizing
- [x] 4. Define retrieval bundles
  - [x] 4-1. Retrieval for draft writing
  - [x] 4-2. Retrieval for benchmark / evaluation tasks
  - [x] 4-3. Retrieval for provenance browsing in the UI
- [x] 5. Define write paths
  - [x] 5-1. Writes from the furnace extraction pipeline
  - [x] 5-2. Writes from the paper acquisition and summary workflow
  - [x] 5-3. Writes from manual user correction or curation

## Decisions
- Reuse `MemoryType` and `MemoryKernel`; do not create a parallel memory runtime.
- Store evidence links so domain taste can always trace back to concrete papers.
- Promotion from paper -> domain -> recipe must be explicit and reviewable.

## Notes
- This plan should build directly on [29-1-unified-memory-kernel](29-1-unified-memory-kernel.md) and [31-21-proactive-domain-learning](31-21-proactive-domain-learning.md).
