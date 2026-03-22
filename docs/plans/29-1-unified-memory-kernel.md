# 29-1: Unified Memory Kernel

**Parent:** [29-concierge-memory-evolvement](29-concierge-memory-evolvement.md)
**Status:** in-progress
**Goal:** Replace the 6 siloed memory systems with a single concierge-owned memory kernel that stores typed memory items, organizes them by type/scope/lifecycle, and retrieves them through task-specific policies.

## Context

DAN currently has 6 separate memory/context systems:

| System | Storage | Retrieval | Used by |
|--------|---------|-----------|---------|
| `ConversationMemoryStore` | JSON file, 200-entry cap | Keyword substring + recency | ChatManager |
| `UserProfile` | JSON file | Direct key lookup | ChatManager, CLI, handlers |
| `ExperienceStore` + `ExperienceIndex` | MemoryStore + vector store | Semantic (embedding) | memory_bridge, MetaController |
| `ErrorMemoryIndex` | Vector store per workflow | Semantic (embedding) | RunManager, LLMExecutor |
| `PrincipleStore` | MemoryStore per workflow | By tags, confidence | ErrorContextProvider, ReflectionExecutor |
| `MemoryStore` (raw) | JSON files | By key, scope | ExperienceStore, PrincipleStore |

Problems: no unified retrieval API, different callers see different subsets, no importance weighting, no temporal consolidation, no cross-system learning.

## Tasks

### 1. Memory item data model
- [x] 1-1. Define `MemoryItem` Pydantic model: `id`, `content` (str), `memory_type` (enum), `scope` (enum), `lifecycle` (enum), `importance` (float, 0-1), `created_at`, `updated_at`, `last_accessed`, `access_count`, `provenance` (source_interaction_id, source_run_id, confirmed_by_user), `tags` (list[str]), `related_ids` (list[str]), `embedding` (optional, list[float])
- [x] 1-2. Define `MemoryType` enum: `FACT`, `PREFERENCE`, `WORKFLOW_PATTERN`, `WORKFLOW_ASSET`, `FAILURE_PATTERN`, `PRINCIPLE`, `EPISODE`, `WORKING_STATE`
- [x] 1-3. Define `MemoryScope` enum: `SESSION`, `PROJECT`, `WORKFLOW`, `USER`, `GLOBAL`
- [x] 1-4. Define `MemoryLifecycle` enum: `ACTIVE`, `DURABLE`, `ARCHIVE`
- [x] 1-5. Define `RetrievalPolicy` model: `task_type` (str), `sections` (list of MemoryType to query), `budget_allocation` (dict mapping MemoryType to float weight for prompt budget), `ranking_overrides` (optional per-type scoring weights)

### 2. Memory kernel store
- [x] 2-1. Implement `MemoryKernel` class in `src/dan/engine/memory_kernel.py`
- [x] 2-2. `store(item: MemoryItem)` — persist to filesystem (JSON) + update vector index if embedding present
- [x] 2-3. `retrieve(query: str, policy: RetrievalPolicy, limit: int) -> list[ScoredMemoryItem]` — route to relevant sections, rank within each, merge under budget
- [x] 2-4. `update(item_id: str, **changes)` — partial update (importance, lifecycle, tags, content)
- [x] 2-5. `delete(item_id: str)` — soft delete (move to ARCHIVE lifecycle)
- [x] 2-6. `list_by_type(memory_type, scope, limit)` — filtered listing for browsing
- [x] 2-7. Filesystem layout: `~/.dan/memory/{scope}/{memory_type}/{item_id}.json` with `_index.json` per directory

### 3. Per-type ranking
- [x] 3-1. `PREFERENCE` ranking: explicit_confirmation > repeat_count > recency
- [x] 3-2. `FACT` ranking: confidence > scope_match > recency
- [x] 3-3. `WORKFLOW_PATTERN` / `WORKFLOW_ASSET` ranking: task_similarity (embedding) > success_rate > recency
- [x] 3-4. `FAILURE_PATTERN` / `PRINCIPLE` ranking: structural_similarity (embedding) > recurrence_count > recent_applicability
- [x] 3-5. `EPISODE` ranking: recency + local_relevance only
- [x] 3-6. `WORKING_STATE` ranking: always highest priority within its scope, no scoring needed
- [x] 3-7. Implement `TypedRanker` with per-type scoring functions, composable via `RetrievalPolicy`

### 4. Predefined retrieval policies
- [x] 4-1. `WORKFLOW_BUILD` policy: 25% working_state, 20% preferences+facts, 35% workflow patterns/assets, 20% failure principles
- [x] 4-2. `FACTUAL_ANSWER` policy: 30% facts, 30% episodes, 20% working_state, 20% preferences
- [x] 4-3. `WORKFLOW_REPAIR` policy: 30% failure patterns, 25% principles, 25% working_state, 20% nearest successful variants
- [x] 4-4. `GENERAL_CONVERSATION` policy: 30% working_state, 25% preferences, 25% facts, 20% episodes
- [x] 4-5. `classify_task_type(message, context) -> str` — lightweight classifier to select retrieval policy

### 5. Adapter layer for existing stores
- [x] 5-1. `ExperienceAdapter` — reads `ExperienceStore`/`ExperienceIndex`, maps to `WORKFLOW_ASSET` items
- [x] 5-2. `ErrorAdapter` — reads `ErrorMemoryIndex`, maps to `FAILURE_PATTERN` items
- [x] 5-3. `PrincipleAdapter` — reads `PrincipleStore`, maps to `PRINCIPLE` items
- [x] 5-4. `ProfileAdapter` — reads `UserProfile`, maps to `PREFERENCE` and `FACT` items
- [x] 5-5. `ConversationAdapter` — reads `ConversationMemoryStore`, maps to `EPISODE` items
- [x] 5-6. Migration script: bulk-convert existing stored data into `MemoryItem` format
- [x] 5-7. Dual-write mode: new items go to both old stores (for backward compat) and new kernel

### 6. Consolidation engine
- [x] 6-1. `consolidate()` method: promote ACTIVE → DURABLE after age threshold (default 24h) with LLM summarization
- [x] 6-2. `archive()` method: demote DURABLE → ARCHIVE after staleness threshold (default 30d without access)
- [x] 6-3. Episode merging: group related episodes by time window + topic similarity → produce summary FACT or PATTERN
- [x] 6-4. Principle compaction: merge similar principles (existing `PrincipleStore.compact()` logic)
- [x] 6-5. Background consolidation: periodic task (configurable interval, default every 6 hours) or triggered on session boundaries
- [x] 6-6. `DAN_MEMORY_CONSOLIDATION_INTERVAL` env var

### 7. Integration points
- [x] 7-1. `ChatManager._build_messages()` — replace profile block + conversation memory with `memory_kernel.retrieve(query, GENERAL_CONVERSATION)`
- [x] 7-2. Concierge `process()` — call `memory_kernel.retrieve()` before every decision
- [x] 7-3. `chat_factory.py` — instantiate `MemoryKernel`, pass to ChatManager and Concierge
- [x] 7-4. Post-interaction: call `memory_kernel.store()` for extracted candidates
- [x] 7-5. `PreferenceExtractor` — run on every interaction (not just CLI), store results as PREFERENCE items

### 8. Tests
- [x] 8-1. Unit tests for MemoryItem model, MemoryKernel CRUD
- [x] 8-2. Unit tests for TypedRanker per-type scoring
- [x] 8-3. Unit tests for RetrievalPolicy budget allocation
- [x] 8-4. Unit tests for adapter layer (each adapter)
- [x] 8-5. Integration test: store items → retrieve with policy → verify ranking
- [x] 8-6. Integration test: consolidation lifecycle (ACTIVE → DURABLE → ARCHIVE)

### 9. Review follow-up: semantic retrieval and lower-latency persistence
- [ ] 9-1. Upgrade `MemoryKernel.retrieve()` to read a real vector index for semantic candidate generation when embeddings are enabled, then hybrid-rerank with typed lexical and scope signals. Do not treat embeddings as passive JSON metadata.
- [ ] 9-2. Define per-type hybrid scoring defaults so `WORKFLOW_PATTERN`, `WORKFLOW_ASSET`, `FAILURE_PATTERN`, and `PRINCIPLE` retrieval lean semantic-first over vector-index candidates, while `FACT` and `PREFERENCE` keep stronger exact-match and scope weighting.
- [ ] 9-3. Add paraphrase, alias, and no-keyword-overlap retrieval evals so relevant memories are recovered when the query is semantically related but lexically different.
- [ ] 9-4. Reduce write amplification in the hot path: stop rewriting `_index.json` on every store, update, or access bump; introduce an append-only journal or batched snapshotting path with background compaction.
- [ ] 9-5. Add durability and concurrency coverage for the new persistence path: crash-safe replay, ordered flush, and multi-writer access-count updates.

## Decisions

- 2026-03-21 review decision: the first semantic-retrieval tranche should read a real vector index rather than only scanning per-item stored embeddings. Hybrid retrieval here means semantic candidate generation from the index plus typed reranking.
- (to be filled during execution)

## Notes

- 2026-03-09 reconciliation: the core kernel, retrieval policies, adapters, CRUD APIs, and chat/concierge wiring are live. The remaining checklist items are mostly around dual-write compatibility, background consolidation scheduling, server-wide preference extraction, and direct test coverage.
- 2026-03-09 final reconciliation: All original 8 task groups complete. Dual-write adapter (§5-7), background consolidation (§6-5/6-6), preference extraction parity (§7-5) all shipped, and the initial tranche was marked completed.
- 2026-03-09 (d): Implemented dual-write mode (§5-7). `DualWriteAdapter` maps MemoryType to legacy store writes: EPISODE→ConversationMemory.add(), PREFERENCE/FACT→UserProfile.set(), WORKFLOW_ASSET→ExperienceStore.record(). Enabled via `DAN_MEMORY_DUAL_WRITE=1` (default off). Best-effort with silent failure. 8 tests added.
- 2026-03-21 review follow-up: reopened by [product review](../reviews/2026-03-21-product-review.md) for hybrid semantic retrieval and lower-latency persistence. The live kernel stores embeddings, but ranking still leans lexical and metadata scoring, and index writes are still too hot-path-heavy for tail-sensitive chat usage.
- The memory kernel wraps existing stores via adapters rather than rewriting them. This preserves backward compatibility and allows incremental migration.
- Vector embeddings are optional per item. Items without embeddings fall back to keyword/metadata matching.
- The consolidation engine's LLM summarization can be disabled via config for environments without LLM access.
