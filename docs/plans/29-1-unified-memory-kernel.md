# 29-1: Unified Memory Kernel

**Parent:** [29-concierge-memory-evolvement](29-concierge-memory-evolvement.md)
**Status:** not-started
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
- [ ] 1-1. Define `MemoryItem` Pydantic model: `id`, `content` (str), `memory_type` (enum), `scope` (enum), `lifecycle` (enum), `importance` (float, 0-1), `created_at`, `updated_at`, `last_accessed`, `access_count`, `provenance` (source_interaction_id, source_run_id, confirmed_by_user), `tags` (list[str]), `related_ids` (list[str]), `embedding` (optional, list[float])
- [ ] 1-2. Define `MemoryType` enum: `FACT`, `PREFERENCE`, `WORKFLOW_PATTERN`, `WORKFLOW_ASSET`, `FAILURE_PATTERN`, `PRINCIPLE`, `EPISODE`, `WORKING_STATE`
- [ ] 1-3. Define `MemoryScope` enum: `SESSION`, `PROJECT`, `WORKFLOW`, `USER`, `GLOBAL`
- [ ] 1-4. Define `MemoryLifecycle` enum: `ACTIVE`, `DURABLE`, `ARCHIVE`
- [ ] 1-5. Define `RetrievalPolicy` model: `task_type` (str), `sections` (list of MemoryType to query), `budget_allocation` (dict mapping MemoryType to float weight for prompt budget), `ranking_overrides` (optional per-type scoring weights)

### 2. Memory kernel store
- [ ] 2-1. Implement `MemoryKernel` class in `src/dan/engine/memory_kernel.py`
- [ ] 2-2. `store(item: MemoryItem)` — persist to filesystem (JSON) + update vector index if embedding present
- [ ] 2-3. `retrieve(query: str, policy: RetrievalPolicy, limit: int) -> list[ScoredMemoryItem]` — route to relevant sections, rank within each, merge under budget
- [ ] 2-4. `update(item_id: str, **changes)` — partial update (importance, lifecycle, tags, content)
- [ ] 2-5. `delete(item_id: str)` — soft delete (move to ARCHIVE lifecycle)
- [ ] 2-6. `list_by_type(memory_type, scope, limit)` — filtered listing for browsing
- [ ] 2-7. Filesystem layout: `~/.dan/memory/{scope}/{memory_type}/{item_id}.json` with `_index.json` per directory

### 3. Per-type ranking
- [ ] 3-1. `PREFERENCE` ranking: explicit_confirmation > repeat_count > recency
- [ ] 3-2. `FACT` ranking: confidence > scope_match > recency
- [ ] 3-3. `WORKFLOW_PATTERN` / `WORKFLOW_ASSET` ranking: task_similarity (embedding) > success_rate > recency
- [ ] 3-4. `FAILURE_PATTERN` / `PRINCIPLE` ranking: structural_similarity (embedding) > recurrence_count > recent_applicability
- [ ] 3-5. `EPISODE` ranking: recency + local_relevance only
- [ ] 3-6. `WORKING_STATE` ranking: always highest priority within its scope, no scoring needed
- [ ] 3-7. Implement `TypedRanker` with per-type scoring functions, composable via `RetrievalPolicy`

### 4. Predefined retrieval policies
- [ ] 4-1. `WORKFLOW_BUILD` policy: 25% working_state, 20% preferences+facts, 35% workflow patterns/assets, 20% failure principles
- [ ] 4-2. `FACTUAL_ANSWER` policy: 30% facts, 30% episodes, 20% working_state, 20% preferences
- [ ] 4-3. `WORKFLOW_REPAIR` policy: 30% failure patterns, 25% principles, 25% working_state, 20% nearest successful variants
- [ ] 4-4. `GENERAL_CONVERSATION` policy: 30% working_state, 25% preferences, 25% facts, 20% episodes
- [ ] 4-5. `classify_task_type(message, context) -> str` — lightweight classifier to select retrieval policy

### 5. Adapter layer for existing stores
- [ ] 5-1. `ExperienceAdapter` — reads `ExperienceStore`/`ExperienceIndex`, maps to `WORKFLOW_ASSET` items
- [ ] 5-2. `ErrorAdapter` — reads `ErrorMemoryIndex`, maps to `FAILURE_PATTERN` items
- [ ] 5-3. `PrincipleAdapter` — reads `PrincipleStore`, maps to `PRINCIPLE` items
- [ ] 5-4. `ProfileAdapter` — reads `UserProfile`, maps to `PREFERENCE` and `FACT` items
- [ ] 5-5. `ConversationAdapter` — reads `ConversationMemoryStore`, maps to `EPISODE` items
- [ ] 5-6. Migration script: bulk-convert existing stored data into `MemoryItem` format
- [ ] 5-7. Dual-write mode: new items go to both old stores (for backward compat) and new kernel

### 6. Consolidation engine
- [ ] 6-1. `consolidate()` method: promote ACTIVE → DURABLE after age threshold (default 24h) with LLM summarization
- [ ] 6-2. `archive()` method: demote DURABLE → ARCHIVE after staleness threshold (default 30d without access)
- [ ] 6-3. Episode merging: group related episodes by time window + topic similarity → produce summary FACT or PATTERN
- [ ] 6-4. Principle compaction: merge similar principles (existing `PrincipleStore.compact()` logic)
- [ ] 6-5. Background consolidation: periodic task (configurable interval, default every 6 hours) or triggered on session boundaries
- [ ] 6-6. `DAN_MEMORY_CONSOLIDATION_INTERVAL` env var

### 7. Integration points
- [ ] 7-1. `ChatManager._build_messages()` — replace profile block + conversation memory with `memory_kernel.retrieve(query, GENERAL_CONVERSATION)`
- [ ] 7-2. Concierge `process()` — call `memory_kernel.retrieve()` before every decision
- [ ] 7-3. `chat_factory.py` — instantiate `MemoryKernel`, pass to ChatManager and Concierge
- [ ] 7-4. Post-interaction: call `memory_kernel.store()` for extracted candidates
- [ ] 7-5. `PreferenceExtractor` — run on every interaction (not just CLI), store results as PREFERENCE items

### 8. Tests
- [ ] 8-1. Unit tests for MemoryItem model, MemoryKernel CRUD
- [ ] 8-2. Unit tests for TypedRanker per-type scoring
- [ ] 8-3. Unit tests for RetrievalPolicy budget allocation
- [ ] 8-4. Unit tests for adapter layer (each adapter)
- [ ] 8-5. Integration test: store items → retrieve with policy → verify ranking
- [ ] 8-6. Integration test: consolidation lifecycle (ACTIVE → DURABLE → ARCHIVE)

## Decisions

- (to be filled during execution)

## Notes

- The memory kernel wraps existing stores via adapters rather than rewriting them. This preserves backward compatibility and allows incremental migration.
- Vector embeddings are optional per item. Items without embeddings fall back to keyword/metadata matching.
- The consolidation engine's LLM summarization can be disabled via config for environments without LLM access.
